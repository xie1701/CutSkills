#!/usr/bin/env python3
"""CutSkills · 规格探测：把「这条素材是什么规格、剪辑时会踩什么坑」变成机器可判的字段。

为什么必须单独一层：同一个素材库里往往混着
  · 相机拍的 8bit SDR BT.709（老规矩）
  · iPhone 拍的 10bit HLG HDR + BT.2020（新默认）
  · 竖着拍的片子，像素却存成 1920×1080 横向，靠 rotation 元数据转 90° 显示

这三件事任何一件没标出来，后面拼接、套字幕、出片都会出问题——而且是在交付之后才被发现。

本模块只做探测和标记，不做转码。处理方式写在 handling 码表里，由 SKILL.md 解释。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# handling 码 → 人事后可读的处理要求。渲染阶段按这些码逐条处理，不要靠人记得。
HANDLING = {
    "hdr_hlg": "HLG 高动态（iPhone 默认 HDR 的基底信号）。混剪前要 tone map 到 SDR BT.709，否则与 SDR 段拼接会突然变灰/变亮。",
    "hdr_pq": "PQ / HDR10 高动态。同 HLG，需 tone map；截图与封面取帧也要先映射，不然发出来发灰。",
    "dolby_vision": "杜比视界（Dolby Vision）。光有基础层不够，还有逐帧的动态元数据；普通剪辑工具只认基础层，剪完颜色会漂。iPhone 常见 Profile 8.4，基底是 HLG。先转 SDR 代理再剪最稳。",
    "wide_gamut": "BT.2020 广色域。转 BT.709 时饱和度会掉，字幕/花字的品牌色要用色卡校准后重新取值。",
    "ten_bit": "10bit 色深。部分硬件编码器和老播放器不支持，代理与最终编码都要确认 pix_fmt。",
    "rotated": "画面靠 rotation 元数据转 90°/180°。ffmpeg 默认自动转，但 -c copy、部分滤镜链会丢旋转导致画面横躺；要显式 transpose。",
    "non_integer_fps": "29.97/59.94 这类非整数帧率。按秒切点会累积漂移，长片要按帧对齐。",
    "high_fps": "≥50fps。文件大、解码重，路上做代理能省很多时间。",
    "vfr": "可变帧率。切点落在帧边界之外时会出现重复帧或跳帧，剪前建议转 CFR。",
    "hevc": "HEVC/H.265。体积小但解码吃硬件；Windows 与部分安卓机播放需额外解码器，交付前确认播放端。",
    "no_audio": "无音轨。口播片里只能当画面（B-roll）。",
    "mono_audio": "单声道。混音时按单声道处理，别当立体声居中。",
    "multichannel": "多声道。转写只取一路，混音要决定下混策略。",
    "long_gop": "长 GOP。逐帧精确定位会变慢，切点附近要多解几帧。",
    "huge": "单个文件超过 1GB。适合放冷存储，本地只留代理。",
    "display_p3": "照片是 Display P3 色域（苹果屏幕宽色域）。转成 sRGB 交付会变灰，要先转再导出。",
    "no_gps": "没有拍摄位置信息。以后想按地点找素材会找不到——不影响剪辑，只影响检索。",
}


def probe(path: str | Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def _fps(s: str) -> float:
    try:
        num, den = s.split("/")
        return round(float(num) / float(den), 3) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(v: dict) -> int:
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(sd["rotation"])
            except (TypeError, ValueError):
                pass
    tags = v.get("tags") or {}
    try:
        return int(float(tags.get("rotate", 0)))
    except (TypeError, ValueError):
        return 0


def spec_of(info: dict) -> dict:
    """ffprobe 输出 → 规格字典。字段只放能直接用于判断的，不放原始噪声。"""
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    spec: dict = {"container": (fmt.get("format_name") or "").split(",")[0] or None}

    if v:
        rotation = _rotation(v)
        w, h = v.get("width") or 0, v.get("height") or 0
        disp_w, disp_h = (h, w) if abs(rotation) % 180 == 90 else (w, h)
        pix = v.get("pix_fmt") or ""
        bit_depth = 10 if ("10" in pix) else 12 if ("12" in pix) else 8
        ct = (v.get("color_transfer") or "").lower()
        cp = (v.get("color_primaries") or "").lower()
        tag = (v.get("codec_tag_string") or "").lower()
        # 杜比视界不是单纯的 transfer 值：它是在 HLG 或 PQ 基础层上加一层动态元数据。
        # 只按 color_transfer 判，会把 DV 误认成普通 HDR——处理方式完全不同。
        dovi = next((sd for sd in (v.get("side_data_list") or [])
                     if sd.get("side_data_type") == "DOVI configuration record"), None)
        dv = bool(dovi) or tag in {"dvh1", "dvhe", "dvav", "dav1"}
        if dv:
            base = "pq" if ct in {"smpte2084", "smpte-st-2084"} else "hlg"
            hdr = "dolby_vision"
        elif ct == "arib-std-b67":
            base, hdr = "hlg", "hlg"
        elif ct in {"smpte2084", "smpte-st-2084"}:
            base, hdr = "pq", "pq"
        else:
            base, hdr = None, "sdr"
        spec.update({
            "vcodec": v.get("codec_name"),
            "profile": v.get("profile"),
            "pix_fmt": pix or None,
            "bit_depth": bit_depth,
            "width": w, "height": h,
            "display_width": disp_w, "display_height": disp_h,
            "rotation": rotation,
            "orientation": "portrait" if disp_h > disp_w else "square" if disp_h == disp_w else "landscape",
            "fps": _fps(v.get("r_frame_rate") or "0/1"),
            "avg_fps": _fps(v.get("avg_frame_rate") or "0/1"),
            "color": {"primaries": cp or None, "transfer": ct or None,
                      "space": (v.get("color_space") or "").lower() or None,
                      "range": v.get("color_range")},
            "hdr": hdr,
            "hdr_base": base,
            "dolby_vision": ({"profile": dovi.get("dv_profile"),
                              "level": dovi.get("dv_level"),
                              "base": base,
                              "bl_present": bool(dovi.get("bl_present_flag"))}
                             if dovi else True if dv else None),
        })
    if a:
        spec["audio"] = {
            "codec": a.get("codec_name"),
            "channels": a.get("channels"),
            "sample_rate": int(a["sample_rate"]) if str(a.get("sample_rate", "")).isdigit() else None,
        }
    try:
        spec["duration"] = round(float(fmt.get("duration") or 0), 3)
    except (TypeError, ValueError):
        spec["duration"] = 0.0
    return spec


def handling_of(spec: dict, bytes_: int = 0) -> list[str]:
    """规格 → 处理要求码。渲染阶段按码执行，不靠人记。"""
    out: list[str] = []
    if not spec:
        return out
    hdr = spec.get("hdr")
    base = spec.get("hdr_base")
    if hdr == "dolby_vision":
        out += ["dolby_vision", "hdr_pq" if base == "pq" else "hdr_hlg", "wide_gamut", "ten_bit"]
    elif hdr == "hlg":
        out += ["hdr_hlg", "wide_gamut", "ten_bit"]
    elif hdr == "pq":
        out += ["hdr_pq", "wide_gamut", "ten_bit"]
    elif (spec.get("color") or {}).get("primaries") == "bt2020":
        out.append("wide_gamut")
    if spec.get("bit_depth", 8) > 8 and "ten_bit" not in out:
        out.append("ten_bit")
    if abs(spec.get("rotation") or 0) % 360:
        out.append("rotated")
    fps, avg = spec.get("fps") or 0, spec.get("avg_fps") or 0
    if fps and abs(fps - round(fps)) > 0.01:
        out.append("non_integer_fps")
    if fps >= 50:
        out.append("high_fps")
    if fps and avg and abs(fps - avg) > 0.05:
        out.append("vfr")
    if (spec.get("vcodec") or "") in {"hevc", "h265"}:
        out.append("hevc")
    au = spec.get("audio")
    if au is None:
        out.append("no_audio")
    elif au.get("channels") == 1:
        out.append("mono_audio")
    elif (au.get("channels") or 0) > 2:
        out.append("multichannel")
    if bytes_ > 1 << 30:
        out.append("huge")
    return out


def sips_creation(path: str | Path) -> str | None:
    """照片的拍摄时间：sips 能读出 HEIC/JPG 里的 creation（本地时间）。"""
    r = subprocess.run(["sips", "-g", "creation", str(path)], capture_output=True, text=True)
    m = None
    for line in (r.stdout or "").splitlines():
        if "creation:" in line:
            m = line.split("creation:", 1)[1].strip()
    if not m:
        return None
    # 形如 2024:07:25 14:48:53
    digits = "".join(ch for ch in m[:10] if ch.isdigit())
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _iso6709(s: str) -> dict | None:
    """解析 `+22.3681+113.6202+042.120/` 这类 QuickTime 位置串。

    iPhone 把经纬度和海拔写在容器的 com.apple.quicktime.location.ISO6709 里。
    这串东西长得像乱码，但它是「按地点找素材」的入口：拍完一年后你会想
    「那次在售楼部拍的那段打磨在哪」，而不是按文件名找。
    """
    s = (s or "").strip()
    if not s:
        return None
    parts = re.findall(r"[+-]\d+(?:\.\d+)?", s)
    if len(parts) < 2:
        return None
    out = {"lat": float(parts[0]), "lon": float(parts[1])}
    if len(parts) >= 3:
        out["alt_m"] = float(parts[2])
    return out


def _mdls(path: str | Path, attrs: list[str]) -> dict:
    cmd = ["mdls"]
    for a in attrs:
        cmd += ["-name", a]
    r = subprocess.run(cmd + [str(path)], capture_output=True, text=True)
    out = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            out[k.strip()] = None if v in {"(null)", ""} else v
    return out


def capture_video(info: dict) -> dict:
    """从 QuickTime 容器标签里掏设备、时间、位置。"""
    tags = {k.lower(): v for k, v in ((info.get("format") or {}).get("tags") or {}).items()}
    cap: dict = {}
    make, model = tags.get("com.apple.quicktime.make"), tags.get("com.apple.quicktime.model")
    if make or model:
        cap["device"] = " ".join(x for x in (make, model) if x)
    if tags.get("com.apple.quicktime.software"):
        cap["software"] = tags["com.apple.quicktime.software"]
    if tags.get("com.apple.quicktime.creationdate"):
        cap["created_local"] = tags["com.apple.quicktime.creationdate"]
    elif tags.get("creation_time"):
        # 容器时间只在苹果没写本地时间时才落一格，且**单独命名**：
        # 它经常是错的（柒造的地面素材文件名写 20261017，容器却是 2024-07-29），
        # 混进 created_local 里，之后没人知道该信哪个。
        cap["created_container"] = tags["creation_time"]
    lens = lens_of(_num(tags.get("com.apple.quicktime.lens.focal.length")),
                   _num(tags.get("com.apple.quicktime.lens.f.number")))
    if lens:
        cap["lens"] = lens
    gps = _iso6709(tags.get("com.apple.quicktime.location.iso6709", ""))
    if gps:
        acc = _num(tags.get("com.apple.quicktime.location.accuracy.horizontal"))
        if acc:
            gps["accuracy_m"] = round(acc, 1)
        cap["gps"] = gps
    return cap


# 按焦距判断是哪颗镜头。iPhone 不把镜头型号写进文件（EXIF 的 LensModel 是空的），
# 但会写焦距和光圈：6.765mm f/1.78 = 主摄，2.22mm = 超广角，15.66mm = 长焦。
# 分段而不是查型号表，是因为剪辑要的是「这几段是不是同一个视角」，不是镜头编号。
LENS_WHICH = ((3.5, "超广角"), (9.5, "主摄"), (99.0, "长焦"))


def lens_of(focal_mm: float | None, f_number: float | None = None,
            model: str | None = None) -> dict | None:
    """镜头：有型号用型号，没有就用焦距+光圈判断哪颗。判不出返回 None，不猜。"""
    if not model and focal_mm is None:
        return None
    out: dict = {}
    if model:
        out["model"] = model
    if focal_mm is not None:
        out["focal_mm"] = round(focal_mm, 3)
        out["which"] = next((n for edge, n in LENS_WHICH if focal_mm < edge), "长焦")
    if f_number is not None:
        out["f"] = round(f_number, 2)
    return out


def capture_photo(path: str | Path) -> dict:
    """照片：mdls 拿 EXIF（含 GPS、光圈、快门、ISO），sips 补设备与色域。"""
    m = _mdls(path, ["kMDItemAcquisitionMake", "kMDItemAcquisitionModel", "kMDItemLensModel",
                     "kMDItemLatitude", "kMDItemLongitude", "kMDItemAltitude",
                     "kMDItemExposureTimeSeconds", "kMDItemFNumber", "kMDItemFocalLength",
                     "kMDItemISOSpeed", "kMDItemContentCreationDate"])
    cap: dict = {}
    make, model = m.get("kMDItemAcquisitionMake"), m.get("kMDItemAcquisitionModel")
    make = make.strip('"') if make else None
    model = model.strip('"') if model else None
    if make or model:
        cap["device"] = " ".join(x for x in (make, model) if x)
    lat, lon = _num(m.get("kMDItemLatitude")), _num(m.get("kMDItemLongitude"))
    if lat is not None and lon is not None and (lat or lon):
        cap["gps"] = {"lat": round(lat, 5), "lon": round(lon, 5)}
        alt = _num(m.get("kMDItemAltitude"))
        if alt is not None:
            cap["gps"]["alt_m"] = round(alt, 1)
    exp = {}
    for key, name in (("kMDItemExposureTimeSeconds", "time_s"), ("kMDItemFNumber", "f"),
                      ("kMDItemFocalLength", "focal_mm"), ("kMDItemISOSpeed", "iso")):
        v = _num(m.get(key))
        if v is not None:
            exp[name] = round(v, 4)
    if exp:
        cap["exposure"] = exp
    lens_model = m.get("kMDItemLensModel")
    lens = lens_of(exp.get("focal_mm"), exp.get("f"), str(lens_model).strip('"') if lens_model else None)
    if lens:
        cap["lens"] = lens
    s = subprocess.run(["sips", "-g", "profile", "-g", "make", "-g", "model", "-g", "software",
                        "-g", "creation", str(path)], capture_output=True, text=True)
    for line in (s.stdout or "").splitlines():
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        if k == "profile":
            cap["color_profile"] = v
        elif k == "software" and v and "software" not in cap:
            cap["software"] = v
        elif k == "creation" and v:
            # sips 给本地时间：2024:07:25 14:48:53 → 2024-07-25T14:48:53
            cap["created_local"] = v.replace(":", "-", 2).replace(" ", "T")
    return cap


def capture(path: str | Path, info: dict | None = None) -> dict:
    ext = Path(path).suffix.lower()
    if ext in {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
        return capture_photo(path)
    return capture_video(info or probe(path))


def shoot_date(path: str | Path, spec: dict | None = None) -> str | None:
    """拍摄日期：优先「本地时间」，其次 UTC。

    视频：com.apple.quicktime.creationdate 是拍摄地当地时间（带 +0800），
    creation_time 是 UTC——晚上拍的片子两者会差一天，所以本地时间优先。
    照片：sips 的 creation 也是本地时间。

    mtime 是「拷进这台机器的时间」，不是拍摄时间，绝不拿它顶。
    """
    ext = Path(path).suffix.lower()
    if ext in {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
        return sips_creation(path)
    info = probe(path)
    tags = {k.lower(): v for k, v in ((info.get("format") or {}).get("tags") or {}).items()}
    local = tags.get("com.apple.quicktime.creationdate")
    if local:
        return str(local)[:10]
    utc = tags.get("creation_time") or (info.get("format") or {}).get("duration") and None
    if utc:
        return str(utc)[:10]
    return None
