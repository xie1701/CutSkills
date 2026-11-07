#!/usr/bin/env python3
"""CutSkills · 阶段 A：素材索引。

L0（元数据 + 抽帧 + 画质）、L1（词级转写）、L2 候选队列；增量、可全量重建。

用法：
  index_assets.py --library "<素材库>"                 # 建/更新索引
  index_assets.py --library "<素材库>" --merge-l2      # 把 l2/*.json 合进索引
  index_assets.py --library "<素材库>" --only 熊工      # 只处理路径含该子串的文件
  index_assets.py --library "<素材库>" --report         # 只打印现状，不干活

所有产物写在 <素材库>/_cutskills/ 下；素材文件只读。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_spec  # noqa: E402  规格/编码/横竖屏探测
import lexicon    # noqa: E402  转写词表纠错

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".insv"}
PHOTO_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac"}
DOC_EXT = {".md", ".txt", ".srt", ".pdf"}
SYS_DIR = "_cutskills"

# 画质阈值。lavfi.blur 是「越大越糊」的原始分（实测：清晰 4K 缩到 320 ≈ 5~7，
# gblur sigma=6 ≈ 33），不是 0~1 归一化值。阈值按柒造素材分布标定，见 references/index-schema.md。
BLUR_DO_NOT_USE = 20.0
BLUR_SOFT = 10.0
LUMA_DARK = 28.0
LUMA_BRIGHT = 232.0
ANA_FPS = 4                 # 画质分析的采样率；指标时间分辨率 = 1/ANA_FPS 秒
L2_BUDGET = 0.15            # L2 覆盖率软上限（预算指标：一段 L2 = 一次视觉调用）

PUNCT_BREAK = set("，。？！；、…—,?!;")
LINE_MAX = 22


# ---------------------------------------------------------------- 基础工具

def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def h12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def probe(path: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def fingerprint(path: Path) -> str:
    """新旧判据：大小 + mtime。**不含文件名**。

    分工：`uid_of()` 判「是不是同一个素材」，这个判「文件被动过没有」。
    旧版把文件名混进来，于是改一次名、全部素材看起来都像换了内容。
    """
    st = path.stat()
    return h12(f"{st.st_size}|{int(st.st_mtime)}")


def legacy_fingerprint(path: Path) -> str:
    """旧版指纹（含文件名）。只用来认迁移前写下的索引，认完就换成新格式。"""
    st = path.stat()
    return h12(f"{path.name}|{st.st_size}|{int(st.st_mtime)}")


def uid_of(path: Path) -> str:
    """内容指纹：改名、搬家都不变。

    只读首尾各 4MB + 文件大小，不读全文件——13G 的库也就几秒，而全文件哈希要几分钟。
    代价是：首尾相同、大小相同的两个文件会被当成同一个。素材库场景里可以接受。
    """
    st = path.stat()
    h = hashlib.sha1()
    h.update(str(st.st_size).encode())
    head = min(4 << 20, st.st_size)
    with path.open("rb") as f:
        h.update(f.read(head))
        if st.st_size > head:
            f.seek(max(head, st.st_size - (4 << 20)))
            h.update(f.read())
    return "a" + h.hexdigest()[:11]


def handling_for_photo(cap: dict) -> list[str]:
    """照片的处理码：主要是色域。P3 直接当 sRGB 用会变灰。"""
    out = []
    prof = (cap or {}).get("color_profile") or ""
    if "P3" in prof:
        out.append("display_p3")
    return out


def photo_spec(path: Path) -> dict:
    """照片：ffprobe 认不出 HEIC，用 sips（macOS 自带）拿像素尺寸。"""
    r = run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format", str(path)])
    spec: dict = {}
    for line in (r.stdout or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "pixelWidth" and v.isdigit():
            spec["width"] = int(v)
        elif k == "pixelHeight" and v.isdigit():
            spec["height"] = int(v)
        elif k == "format":
            spec["container"] = v.lower()
    w, h = spec.get("width", 0), spec.get("height", 0)
    if w and h:
        spec["orientation"] = "portrait" if h > w else "square" if h == w else "landscape"
        spec["display_width"], spec["display_height"] = w, h
    spec.setdefault("hdr", "sdr")
    return spec


# ---------------------------------------------------------------- L0 视频

def analyze_video(path: Path, cache: Path, aid: str, interval: float) -> dict:
    """一次解码同时做：抽缩略图 + 逐帧画质统计 + 检测事件。"""
    info = probe(path)
    fmt = info.get("format", {})
    vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    astream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)

    try:
        duration = float(fmt.get("duration") or vstream.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0

    fps = 0.0
    rate = vstream.get("r_frame_rate", "0/1")
    try:
        num, den = rate.split("/")
        fps = round(float(num) / float(den), 3) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    rec = {
        "id": aid,
        "kind": "video",
        "batch": aid.split("/")[0] if "/" in aid else ".",
        "bytes": path.stat().st_size,
        "duration": round(duration, 3),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "fps": fps,
        "vcodec": vstream.get("codec_name"),
        "audio": None,
        "quality": {},
        "silence": [],
        "transcript": None,
        "segments": [],
        "notes": [],
    }
    rec["spec"] = media_spec.spec_of(info)
    rec["orientation"] = rec["spec"].get("orientation")
    rec["handling"] = media_spec.handling_of(rec["spec"], path.stat().st_size)
    rec["capture"] = media_spec.capture_video(info)      # 设备 / 位置 / 本地拍摄时间
    rec["shoot_date"] = media_spec.shoot_date(str(path))
    if astream:
        rec["audio"] = {
            "codec": astream.get("codec_name"),
            "channels": astream.get("channels"),
            "sample_rate": int(astream["sample_rate"]) if astream.get("sample_rate") else None,
        }

    thumb_dir = cache / "thumbs" / h12(aid)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    meta_file = cache / f"meta_{h12(aid)}.txt"

    # 一次硬件解码：4Hz 画质分析 +（若缺）缩略图。比逐帧跑滤滤镜快 7 倍。
    have_thumbs = any(thumb_dir.glob("*.jpg"))

    def video_pass(hwaccel: bool) -> tuple[str, bool]:
        ana = (f"fps={ANA_FPS},scale=320:-2,signalstats,blurdetect,"
               f"blackdetect=d=0.15:pix_th=0.10,freezedetect=n=-60dB:d=1.5,"
               f"metadata=mode=print:file={meta_file}")
        if have_thumbs:
            graph = f"[0:v]{ana}[an]"
            maps = ["-map", "[an]", "-f", "null", "-"]
        else:
            graph = (f"[0:v]split=2[s1][s2];[s1]{ana}[an];"
                     f"[s2]fps=1/{max(interval, 0.5):g},scale=320:-2[th]")
            maps = ["-map", "[an]", "-f", "null", "-",
                    "-map", "[th]", "-q:v", "4", str(thumb_dir / "%04d.jpg")]
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-y"]
        if hwaccel:
            cmd += ["-hwaccel", "videotoolbox"]
        r = run(cmd + ["-i", str(path), "-filter_complex", graph] + maps)
        ok = r.returncode == 0 and (have_thumbs or any(thumb_dir.glob("*.jpg")))
        return (r.stderr or ""), ok

    log, ok = video_pass(hwaccel=True)
    if not ok:
        meta_file.unlink(missing_ok=True)
        if not have_thumbs:
            for old_t in thumb_dir.glob("*.jpg"):
                old_t.unlink(missing_ok=True)
        log, ok = video_pass(hwaccel=False)
        if not ok:
            rec["notes"].append("视频分析失败（解码或滤镜不可用）")
    rec["thumbs"] = [str(p) for p in sorted(thumb_dir.glob("*.jpg"))][:400]

    # 音频单独一遍：只解音频，极快
    if astream:
        ap_ = run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-y", "-i", str(path),
                   "-vn", "-af", "silencedetect=n=-35dB:d=0.4,volumedetect", "-f", "null", "-"])
        alog = ap_.stderr or ""
        log += "\n" + alog
        m = re.search(r"max_volume:\s*(-?[\d.]+) dB", alog)
        if m and rec["audio"] is not None:
            rec["audio"]["peak_db"] = float(m.group(1))

    blurs, lumas, ydif = [], [], []
    if meta_file.exists():
        cur = None
        for line in meta_file.read_text(errors="ignore").splitlines():
            if line.startswith("frame:"):
                cur = {}
                continue
            if line.startswith("lavfi.blur=") and cur is not None:
                try:
                    blurs.append(float(line.split("=")[1]))
                except ValueError:
                    pass
            elif line.startswith("lavfi.signalstats.YAVG="):
                try:
                    lumas.append(float(line.split("=")[1]))
                except ValueError:
                    pass
            elif line.startswith("lavfi.signalstats.YDIF="):
                try:
                    ydif.append(float(line.split("=")[1]))
                except ValueError:
                    pass
        meta_file.unlink(missing_ok=True)

    def stat(vals: list[float]) -> dict:
        if not vals:
            return {}
        s = sorted(vals)
        n = len(s)
        return {"mean": round(sum(s) / n, 3), "p10": round(s[max(0, int(n * 0.1) - 1)], 3),
                "p90": round(s[min(n - 1, int(n * 0.9))], 3), "n": n}

    black = [(float(a), float(b)) for a, b in
             re.findall(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", log)]
    freeze = [(float(a), float(b)) for a, b in
              re.findall(r"freeze_start:\s*([\d.]+)\s+freeze_end:\s*([\d.]+)", log)]
    silences = []
    starts = re.findall(r"silence_start:\s*([-\d.]+)", log)
    ends = re.findall(r"silence_end:\s*([\d.]+)", log)
    for i, s in enumerate(starts):
        e = float(ends[i]) if i < len(ends) else duration
        silences.append([round(max(0.0, float(s)), 2), round(e, 2)])

    q = {
        "blur": stat(blurs),
        "luma": stat(lumas),
        "motion": stat(ydif),
        "black_total": round(sum(b - a for a, b in black), 2),
        "freeze_total": round(sum(b - a for a, b in freeze), 2),
        "samples": len(lumas),
        "sample_fps": ANA_FPS,
    }
    rec["quality"] = q
    rec["silence"] = silences[:200]
    return rec


# ---------------------------------------------------------------- L1 转写

def extract_audio(path: Path, cache: Path, aid: str) -> Path | None:
    out = cache / "audio" / f"{h12(aid)}.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 1024:
        return out
    p = run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn",
             "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out)])
    return out if p.returncode == 0 and out.exists() else None


CHUNK_S = 240                 # 长音频切片长度。sensevoice 对长文件会返回空结果，必须切


def _asr_chunk(audio: Path, cache: Path, aid: str, lang: str, model: str,
               offset: float, allow_legacy: bool = False) -> dict | None:
    key = f"{h12(aid)}-v3c{int(offset):05d}"
    cache_f = cache / "asr" / f"{key}.json"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    legacy = cache / "asr" / f"{h12(aid)}.json"
    if allow_legacy and offset == 0.0 and not cache_f.exists() and legacy.exists():
        cache_f = legacy                       # 旧版整段缓存，只在未切片时复用
    if not cache_f.exists():
        p = run(["coli", "asr", "-j", "--model", model, "--language", lang, str(audio)], timeout=900)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        try:
            json.loads(p.stdout)
        except json.JSONDecodeError:
            return None
        cache_f.write_text(p.stdout)
    try:
        raw = json.loads(cache_f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    toks, stamps = raw.get("tokens") or [], raw.get("timestamps") or []
    if not toks or len(toks) != len(stamps):
        return None
    words = []
    for i, tok in enumerate(toks):
        st = float(stamps[i]) + offset
        nx = float(stamps[i + 1]) + offset if i + 1 < len(stamps) else st + 0.3
        words.append({"t": tok, "start": round(st, 3), "end": round(min(nx, st + 0.8), 3)})
    return {"words": words, "lang": raw.get("lang", lang)}


def content_len(text: str) -> int:
    """只数有信息的字符：中文、字母、数字。标点和空白不算。"""
    return sum(1 for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def asr(path: Path, cache: Path, aid: str, lang: str, model: str) -> dict | None:
    """coli asr → 字/词级时间戳。长音频先切片再拼（保时间轴偏移）。"""
    audio = extract_audio(path, cache, aid)
    if audio is None:
        return None

    info = probe(audio)
    try:
        dur = float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0

    all_words: list[dict] = []
    if dur <= CHUNK_S * 1.2:
        got = _asr_chunk(audio, cache, aid, lang, model, 0.0, allow_legacy=True)
        if got:
            all_words = got["words"]
    else:
        chunk_dir = cache / "chunks" / f"{h12(aid)}-v2"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        # 必须重编码切片：mp3 流复制切片会把第一段重复写满所有文件
        run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-f", "segment",
             "-segment_time", str(CHUNK_S), "-c:a", "libmp3lame", "-q:a", "5",
             str(chunk_dir / "%03d.mp3")])
        for cf in sorted(chunk_dir.glob("*.mp3")):
            try:
                idx = int(cf.stem)
            except ValueError:
                continue
            # 偏移必须同时进缓存键，否则各片会互相当成第一片
            got = _asr_chunk(cf, cache, aid, lang, model, idx * CHUNK_S, allow_legacy=False)
            if got:
                all_words.extend(got["words"])      # _asr_chunk 内已做过偏移
    if not all_words:
        return None

    # 语义纠错：ASR 只保证「听起来像」，写对要靠词表（品牌名、术语、人名）。
    # 词表在 _cutskills/lexicon.json，库级复用；这里只负责套用，不负责判断。
    lex = lexicon.load(cache.parent)
    words, fixes = lexicon.apply(all_words, lex)

    phrases, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt_gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 9.9
        long_enough = len("".join(x["t"] for x in cur)) >= LINE_MAX
        if w["t"] in PUNCT_BREAK or nxt_gap > 0.45 or long_enough or i == len(words) - 1:
            text = "".join(x["t"] for x in cur).strip()
            if content_len(text) >= 2:                  # 纯标点/噪声不是句子
                phrases.append({"start": cur[0]["start"], "end": round(cur[-1]["end"], 3), "text": text})
            cur = []
    if not phrases:
        return None

    # 噪声幻觉：一段画面里只“听”到几个字，整段当无语音处理
    speech_s = sum(p["end"] - p["start"] for p in phrases)
    total_s = dur if dur > 0 else max((p["end"] for p in phrases), default=0)
    ratio = round(speech_s / total_s, 3) if total_s else 0.0
    if ratio < 0.06 or len(phrases) < 2 or speech_s < 2.0:
        return None
    return {"engine": f"coli:{model}", "lang": lang, "phrases": phrases,
            "word_count": len(words), "chunked": dur > CHUNK_S * 1.2,
            "speech_ratio": ratio, "lexicon_fixes": len(fixes)}


# ---------------------------------------------------------------- 分级（L0 猜测）

def grade_l0(rec: dict) -> tuple[str, str, float]:
    q = rec.get("quality") or {}
    blur = (q.get("blur") or {}).get("mean")
    luma = (q.get("luma") or {}).get("mean")
    dur = rec.get("duration") or 0
    black = q.get("black_total", 0) or 0
    freeze = q.get("freeze_total", 0) or 0

    if dur <= 0.2:
        return "do-not-use", "时长几乎为零", 0.9
    if black > max(1.0, dur * 0.5):
        return "do-not-use", f"黑帧占 {black:.1f}s / {dur:.1f}s", 0.7
    if freeze > max(2.0, dur * 0.6):
        return "do-not-use", f"静帧占 {freeze:.1f}s / {dur:.1f}s", 0.7
    if blur is not None and blur > BLUR_DO_NOT_USE:
        return "do-not-use", f"对焦/运动模糊过重（blur {blur:.1f}）", 0.55
    if luma is not None and (luma < LUMA_DARK or luma > LUMA_BRIGHT):
        return "do-not-use", f"曝光异常（luma {luma:.0f}）", 0.5

    has_speech = bool((rec.get("transcript") or {}).get("phrases"))
    soft = blur is not None and blur > BLUR_SOFT
    if has_speech and not soft:
        return "must-use", "有可用语音且清晰度通过 L0", 0.4
    if dur >= 3.0 and not soft:
        return "backup", "L0 清晰可用，等待 L2 判断内容", 0.3
    if soft:
        return "backup", f"略糊（blur {blur:.1f}），L2 再定", 0.3
    return "backup", "过短，需 L2 确认", 0.25


# ---------------------------------------------------------------- 单文件

def stage_l0_one(args: tuple) -> dict:
    """L0：只碰画面与容器，不跑 ASR。解码是硬资源，必须和 ASR 分开调度。

    args = (当前路径, 库根, 缓存键路径)。缓存键路径是「第一次建索引时的相对路径」，
    改名搬家后仍然指向同一套缓存——否则一次重命名就会把所有转写重跑一遍。
    """
    path_s, lib_s, key_s = args
    path, lib = Path(path_s), Path(lib_s)
    aid = str(path.relative_to(lib)).replace(os.sep, "/")
    ext = path.suffix.lower()
    cache = lib / SYS_DIR / "cache"
    rec = {"id": aid, "path": aid, "legacy_key": h12(key_s), "legacy_path": key_s,
           "uid": uid_of(path), "kind": "other",
           "batch": aid.split("/")[0] if "/" in aid else ".",
           "bytes": path.stat().st_size, "duration": 0, "fingerprint": fingerprint(path),
           "transcript": None, "notes": [], "segments": []}

    if ext in VIDEO_EXT:
        t0 = time.time()
        rec.update(analyze_video(path, cache, key_s, 2.0))
        # 机器时间：硬指标「60 分钟素材 ≤8 分钟」靠这个数才能回归，而不是靠感觉
        rec["l0_seconds"] = round(time.time() - t0, 1)
        rec["fingerprint"] = fingerprint(path)
    elif ext in PHOTO_EXT:
        rec["kind"] = "photo"
        rec["spec"] = photo_spec(path)
        rec["orientation"] = rec["spec"].get("orientation")
        rec["capture"] = media_spec.capture_photo(path)   # EXIF：设备 / GPS / 曝光 / 色域
        rec["handling"] = handling_for_photo(rec["capture"])
        rec["shoot_date"] = media_spec.shoot_date(path)
        if ext in {".heic", ".heif"}:
            out = cache / "thumbs" / h12(aid)
            out.parent.mkdir(parents=True, exist_ok=True)
            jpg = out.with_suffix(".jpg")
            if not jpg.exists():
                run(["sips", "-s", "format", "jpeg", "--resampleWidth", "480",
                     str(path), "--out", str(jpg)])
            rec["thumbs"] = [str(jpg)] if jpg.exists() else []
        else:
            rec["thumbs"] = [str(path)]
    elif ext in AUDIO_EXT:
        rec["kind"] = "audio"
        info = probe(path)
        rec["spec"] = media_spec.spec_of(info)
        rec["handling"] = media_spec.handling_of(rec["spec"], path.stat().st_size)
        rec["duration"] = round(float(info.get("format", {}).get("duration") or 0), 3)
    elif ext in DOC_EXT:
        rec["kind"] = "doc"
        rec["spec"] = {"container": ext.lstrip("."), "orientation": None, "hdr": None}
        rec["handling"] = []
        if ext in {".md", ".txt", ".srt"}:
            try:
                rec["text_head"] = path.read_text(errors="ignore")[:4000]
            except OSError:
                pass
    return rec


def stage_l1_one(args: tuple) -> tuple[str, dict | None]:
    """L1：只跑转写。返回 (素材 id, transcript)。key_s 见 stage_l0_one。"""
    path_s, lib_s, key_s = args
    path, lib = Path(path_s), Path(lib_s)
    aid = str(path.relative_to(lib)).replace(os.sep, "/")
    return aid, asr(path, lib / SYS_DIR / "cache", key_s, "zh", "sensevoice")


def apply_grades(assets: list[dict]) -> None:
    """分级要等 L1 回来才能判（有没有可用语音是判据之一）。"""
    for rec in assets:
        if rec["kind"] == "video":
            g, why, conf = grade_l0(rec)
            rec["segments"] = [{
                "in": 0.0, "out": round(rec.get("duration") or 0, 3),
                "shot": None, "people": [], "action": None, "text_on_screen": None,
                "summary": None, "grade": g, "why": why, "confidence": conf, "flags": [],
            }]
        elif rec["kind"] in {"photo", "audio", "doc"} and not rec.get("segments"):
            rec["segments"] = []


# ---------------------------------------------------------------- L2 队列

def make_sheet(frames: list[str], out: Path) -> bool:
    """三帧拼成一张联系表 —— 一次视觉调用就能判“是什么、能不能用”。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = [f for f in frames if Path(f).exists()][:3]
    if not frames:
        return False
    if len(frames) == 1:
        run(["ffmpeg", "-v", "error", "-y", "-i", frames[0],
             "-vf", "scale=420:-2", "-q:v", "3", str(out)])
        return out.exists()
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f in frames:
        cmd += ["-i", f]
    graph = "".join(f"[{i}:v]scale=420:-2[s{i}];" for i in range(len(frames)))
    graph += "".join(f"[s{i}]" for i in range(len(frames))) + f"hstack=inputs={len(frames)}[o]"
    cmd += ["-filter_complex", graph, "-map", "[o]", "-q:v", "3", str(out)]
    run(cmd)
    return out.exists()


def l2_candidates(assets: list[dict], cap: int, cause: str, lib: Path,
                  per_asset: int = 0) -> list[dict]:
    cands = []
    for a in assets:
        if a["kind"] == "video":
            dur = a.get("duration") or 0
            phrases = (a.get("transcript") or {}).get("phrases") or []
            if phrases:
                for i in range(0, len(phrases), 10):
                    blk = phrases[i:i + 10]
                    cands.append({"asset": a["id"], "in": blk[0]["start"],
                                  "out": min(blk[-1]["end"] + 0.3, dur),
                                  "cause": "speech", "lines": [p["text"] for p in blk]})
            else:
                cands.append({"asset": a["id"], "in": 0.0, "out": dur, "cause": "broll", "lines": []})
        elif a["kind"] == "photo":
            cands.append({"asset": a["id"], "in": 0.0, "out": 0.0, "cause": "photo", "lines": []})

    # 每支素材最多留几条：取「讲得最多」的那段当代表（字数总和最大），
    # 而不是最靠前的那段——开头往往是寒暄，中段才是内容。
    if per_asset:
        best: dict[str, list[dict]] = {}
        for c in cands:
            best.setdefault(c["asset"], []).append(c)
        picked = []
        for aid, group in best.items():
            group.sort(key=lambda c: -sum(len(t) for t in c.get("lines") or []))
            picked += group[:per_asset]
        order = {c["asset"]: i for i, a in enumerate(assets) for c in [{"asset": a["id"]}]}
        cands = sorted(picked, key=lambda c: (order.get(c["asset"], 0), c["in"]))

    if cause in {"speech", "broll"}:
        cands = [c for c in cands if c["cause"] == cause]
    elif cause == "mix":
        # 两边轮流拿，避免队列被长口播吃光
        sp = [c for c in cands if c["cause"] == "speech"]
        br = [c for c in cands if c["cause"] != "speech"]
        cands = [c for pair in zip(sp, br) for c in pair] + sp[len(br):] + br[len(sp):]
    else:
        cands.sort(key=lambda c: (c["cause"] != "speech",))

    out = cands[:cap]
    for c in out:
        a = next(x for x in assets if x["id"] == c["asset"])
        thumbs = a.get("thumbs") or []
        if a["kind"] == "video" and thumbs and a.get("duration"):
            # 抽帧必须落在**这一段**里头。早先按整支视频的 15/50/85% 取，
            # 同一素材的每一段都拿到同样三帧——七段口播的联系表 MD5 完全一样，
            # 判出来的「段级差异」全是假的。
            step = a["duration"] / len(thumbs)
            frames = []
            for f in (0.25, 0.55, 0.85):
                t = c["in"] + (c["out"] - c["in"]) * f
                i = min(len(thumbs) - 1, max(0, int(round(t / step))))
                frames.append(thumbs[i])
            c["frames"] = frames
        else:
            c["frames"] = thumbs[:1]
        sheet = lib / SYS_DIR / "l2s" / f"{a.get('legacy_key') or h12(c['asset'])}-{int(round(c['in']))}.jpg"
        c["sheet"] = str(sheet) if make_sheet(c["frames"], sheet) else None
        c["write_to"] = f"{SYS_DIR}/l2/{a.get('legacy_key') or h12(c['asset'])}-{int(round(c['in']))}.json"
    return out


# ---------------------------------------------------------------- 汇总输出

def write_pickup(lib: Path, assets: list[dict]) -> None:
    label = {0: "must-use", 1: "backup", 2: "do-not-use"}
    order = {v: k for k, v in label.items()}
    rows = []
    for a in assets:
        for s in a.get("segments") or []:
            tc = f"{s['in']:.1f}–{s['out']:.1f}s" if a["kind"] == "video" else "—"
            rows.append((order.get(s.get("grade"), 3), a["id"], tc,
                         (s.get("summary") or s.get("action") or "—").replace("|", "/"),
                         (s.get("why") or "").replace("|", "/"),
                         f"{s.get('confidence', 0):.2f}"))
    n_must = sum(1 for r in rows if r[0] == 0)
    lines = ["# 分级取用（L0/L2 合并）", "",
             f"生成于 {datetime.now():%Y-%m-%d %H:%M} · {len(assets)} 个素材 · "
             f"must-use {n_must} 段 / 共 {len(rows)} 段", "",
             "只跑了 L0 的素材，`内容` 列是空的 —— 能不能用要等 L2。", "",
             "| 分级 | 素材 | 时间码 | 内容 | 理由 | 置信 |", "|---|---|---|---|---|---|"]
    for r in sorted(rows):
        lines.append(f"| {label.get(r[0], '?')} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |")
    (lib / SYS_DIR / "pickup.md").write_text("\n".join(lines) + "\n")


def merge_l2(lib: Path, assets: list[dict]) -> int:
    d = lib / SYS_DIR / "l2"
    if not d.exists():
        return 0
    by_id = {a["id"]: a for a in assets}
    by_alias = {al: a for a in assets for al in (a.get("aliases") or [])}
    for a in assets:
        a["l2_segments"] = []          # 每次重建，避免重复合并
    n = 0
    for f in sorted(d.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except json.JSONDecodeError:
            print(f"  ! 跳过无法解析的 {f.name}", file=sys.stderr)
            continue
        a = by_id.get(rec.get("asset")) or by_alias.get(rec.get("asset"))
        if not a:
            continue
        a.setdefault("l2_segments", []).append({k: v for k, v in rec.items() if k != "asset"})
        n += 1
    # L2 段覆盖 L0 段
    for a in assets:
        if a.get("l2_segments"):
            a["segments"] = sorted(a["l2_segments"], key=lambda s: s.get("in", 0))
    return n


def l2_budget_note(lib: Path, cli_note: str = "") -> str | None:
    """超了 15% 要留一句原因，写在 `_cutskills/l2_budget.json` 里跨轮保留。

    只在这一轮把原因传进来，下一轮忘了传也不会丢——否则每跑一次索引
    就要重新解释一遍钱花在哪。
    """
    p = lib / SYS_DIR / "l2_budget.json"
    if (cli_note or "").strip():
        p.write_text(json.dumps({"note": cli_note.strip(),
                                 "set_at": datetime.now().isoformat(timespec="seconds")},
                                ensure_ascii=False, indent=1))
        return cli_note.strip()
    if p.exists():
        try:
            return (json.loads(p.read_text()) or {}).get("note") or None
        except json.JSONDecodeError:
            return None
    return None


def write_index(lib: Path, assets: list[dict], n_l2: int, a, queue: bool = True) -> dict:
    sysdir = lib / SYS_DIR
    # 批次就是一级目录名；根目录素材没有批次，写空而不是 "."（"." 会出现在给人看的 CSV 里）
    for x in assets:
        x["batch"] = x["id"].split("/")[0] if "/" in x["id"] else ""
    q = l2_candidates(assets, a.l2_cap, a.l2_cause, lib,
                      getattr(a, "l2_per_asset", 0) or 0) if queue else \
        json.loads((sysdir / "l2_queue.json").read_text()) if (sysdir / "l2_queue.json").exists() else []
    # L2 是花钱的（一段一次视觉调用），所以覆盖率要能算出来：
    # 只看过画面的段时长 / 全库时长。这是预算指标，不是质量指标。
    total_s = sum(x.get("duration") or 0 for x in assets)
    l2_s = sum((float(s.get("out") or 0) - float(s.get("in") or 0))
               for x in assets for s in (x.get("l2_segments") or []))
    l2_cov = round(l2_s / total_s, 4) if total_s else 0.0
    counts = {
        "assets": len(assets),
        "video": sum(1 for x in assets if x["kind"] == "video"),
        "photo": sum(1 for x in assets if x["kind"] == "photo"),
        "with_speech": sum(1 for x in assets if (x.get("transcript") or {}).get("phrases")),
        "duration_min": round(sum(x.get("duration") or 0 for x in assets) / 60, 2),
        "bytes": sum(x.get("bytes") or 0 for x in assets),
        "portrait": sum(1 for x in assets if x.get("orientation") == "portrait"),
        "offline": sum(1 for x in assets if not (lib / x["id"]).exists()),
        "l2_records": n_l2,
        "l2_minutes": round(l2_s / 60, 2),
        "l2_coverage": l2_cov,
        "l2_assets": sum(1 for x in assets if x.get("l2_segments")),
    }
    # 15% 是软上限：超了不拦，但必须留下一句为什么（写在库级配置里，跨轮保留）。
    if l2_cov > L2_BUDGET:
        counts["l2_budget"] = L2_BUDGET
        counts["l2_budget_note"] = l2_budget_note(lib, getattr(a, "l2_budget_note", ""))
    index = {
        "version": 2,
        "library": str(lib),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "assets": assets,
    }
    (sysdir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    if q:
        (sysdir / "l2_queue.json").write_text(json.dumps(q, ensure_ascii=False, indent=1))
    write_pickup(lib, assets)
    try:
        import render_library_index
        render_library_index.render(lib, assets)
    except Exception as e:                                          # noqa: BLE001
        print(f"  ! 生成人类可读索引失败：{e}", file=sys.stderr)
    return index


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--jobs", type=int, default=3, help="L0 并发（解码是硬资源，别超过 4）")
    ap.add_argument("--l1-jobs", type=int, default=1, help="L1 转写并发（coli asr 并发会互相堵）")
    ap.add_argument("--stage", default="all", choices=["all", "l0", "l1", "rephrase", "finish", "migrate"],
                    help="l0=只探针抽帧画质；l1=只转写；rephrase=从转写缓存重算句子（不重跑模型）；"
                         "finish=只重算分级并出报告；migrate=给老索引补规格与指纹，不重跑任何重活")
    ap.add_argument("--only", default=None, help="只处理路径含该子串的文件")
    ap.add_argument("--merge-l2", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--l2-cap", type=int, default=24)
    ap.add_argument("--l2-per-asset", type=int, default=0,
                    help="每支素材最多取几条候选。默认 0 = 不限（长口播会占满队列）；"
                         "要铺整库命名就设 1，一个文件一条，cap 覆盖的文件数才等于素材数")
    ap.add_argument("--l2-cause", default="mix", choices=["mix", "speech", "broll", "all"],
                    help="排哪些候选进 L2 队列")
    ap.add_argument("--l2-budget-note", default="",
                    help="L2 覆盖率超 15%% 时留一句原因（写进 _cutskills/l2_budget.json，跨轮保留）")
    ap.add_argument("--force", action="store_true", help="忽略指纹，全部重算")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    if not lib.is_dir():
        print(f"素材库不存在：{lib}", file=sys.stderr)
        return 2
    sysdir = lib / SYS_DIR
    index_path = sysdir / "index.json"
    sysdir.mkdir(parents=True, exist_ok=True)

    old: dict[str, dict] = {}
    if index_path.exists() and not a.force:
        try:
            for rec in json.loads(index_path.read_text()).get("assets", []):
                old[rec["id"]] = rec
        except json.JSONDecodeError:
            old = {}

    # ---- 迁移：给老索引补 uid / spec / handling / shoot_date，不重跑解码也不重跑转写
    if a.stage == "migrate":
        n = 0
        for rec in old.values():
            p = lib / rec["id"]
            rec.setdefault("legacy_key", h12(rec["id"]))
            rec.setdefault("legacy_path", rec["id"])
            rec["path"] = rec["id"]
            if not p.exists():
                continue
            rec.setdefault("uid", uid_of(p))
            if rec.get("kind") == "video":
                info = media_spec.probe(str(p))
                spec = media_spec.spec_of(info)
                rec["spec"] = spec
                rec["orientation"] = spec.get("orientation")
                rec["handling"] = media_spec.handling_of(spec, rec.get("bytes") or 0)
                rec["capture"] = media_spec.capture_video(info)
                rec["shoot_date"] = media_spec.shoot_date(str(p))
            elif rec.get("kind") == "photo":
                spec = photo_spec(p)
                rec["spec"] = spec
                rec["orientation"] = spec.get("orientation")
                rec["capture"] = media_spec.capture_photo(p)
                rec["handling"] = handling_for_photo(rec["capture"])
                rec["shoot_date"] = media_spec.shoot_date(p)
            elif rec.get("kind") in {"audio", "doc"}:
                rec.setdefault("spec", {"container": p.suffix.lstrip(".").lower(),
                                        "orientation": None, "hdr": None})
                rec.setdefault("handling", [])
            n += 1
        assets = [old[k] for k in sorted(old)]
        apply_grades(assets)
        n_l2 = merge_l2(lib, assets)
        write_index(lib, assets, n_l2, a, queue=False)
        print(f"迁移完成：{n} 个素材补上规格/指纹（未重跑解码与转写）")
        return 0

    files: list[Path] = []
    for p in sorted(lib.rglob("*")):
        if not p.is_file() or SYS_DIR in p.parts:
            continue
        if p.suffix.lower() not in VIDEO_EXT | PHOTO_EXT | AUDIO_EXT | DOC_EXT:
            continue
        if p.name.startswith("素材索引"):          # 索引本身是脚本产物，不能再被索引进去
            continue
        if a.only and a.only not in str(p):
            continue
        files.append(p)

    if a.report:
        total = sum(r.get("duration") or 0 for r in old.values())
        print(f"库：{lib}\n素材：{len(old)} 个，总时长 {total/60:.1f} 分钟")
        for b in sorted({r.get("batch") for r in old.values()}):
            sub = [r for r in old.values() if r.get("batch") == b]
            print(f"  {b}: {len(sub)} 个, {sum(x.get('duration') or 0 for x in sub)/60:.1f} min")
        return 0

    print(f"素材库 {lib}")
    print(f"待处理 {len(files)} 个文件，{a.jobs} 并发")

    by_uid = {r["uid"]: r for r in old.values() if r.get("uid")}
    results: dict[str, dict] = {}
    keymap: dict[str, str] = {}          # 当前路径 → 缓存键路径（改名后仍指向同一套缓存）
    todo = []
    for p in files:
        aid = str(p.relative_to(lib)).replace(os.sep, "/")
        fp = fingerprint(p)
        try:
            uid = uid_of(p)
        except OSError:
            uid = None
        prev = old.get(aid)
        if prev is None and uid and uid in by_uid:
            prev = dict(by_uid[uid])                    # 改名或搬家：按内容认人，不重新解码
            if prev.get("id") != aid:
                prev["aliases"] = sorted(set((prev.get("aliases") or []) + [prev["id"]]))
                prev["id"] = prev["path"] = aid
                print(f"  ~ 认得是老素材：{prev['aliases'][-1]} → {aid}")
        if prev is not None:
            if prev.get("uid") not in (None, uid):
                # 同一个路径、内容指纹变了：文件被换掉了。旧记录不能沿用。
                print(f"  ~ 内容变了，重算：{aid}")
                prev = None
            else:
                # uid 相同就是同一个素材；大小/mtime 变了只可能是拷贝或触碰，
                # 转写和画质都不用重跑——旧版在这里把「指纹变了」当成「内容变了」。
                if prev.get("fingerprint") not in (fp, legacy_fingerprint(p)):
                    notes = [n for n in (prev.get("notes") or []) if "文件时间变过" not in n]
                    prev["notes"] = notes + ["文件时间/大小变过，但内容指纹未变（拷贝或触碰），沿用旧索引"]
                prev["fingerprint"] = fp
                results[aid] = prev                   # 已有的永远不被丢掉，哪怕没跑 L0
                keymap[aid] = prev.get("legacy_path") or aid
                if not prev.get("quality") and p.suffix.lower() in VIDEO_EXT and a.stage in {"all", "l0"}:
                    todo.append((str(p), str(lib), keymap[aid]))
                continue
        keymap[aid] = aid
        if a.stage in {"all", "l0"}:
            todo.append((str(p), str(lib), aid))

    print(f"L0 命中缓存 {len(results)}，需计算 {len(todo)}")
    if todo and a.stage in {"all", "l0"}:
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(stage_l0_one, t): t[0] for t in todo}
            done = 0
            for f in as_completed(futs):
                done += 1
                try:
                    rec = f.result()
                except Exception as e:                              # noqa: BLE001
                    print(f"  ! {Path(futs[f]).name}: {e}", file=sys.stderr)
                    continue
                results[rec["id"]] = rec
                print(f"  L0 {done}/{len(todo)}", flush=True)

    # ---- L1 独立一遍：只转写，串行度更低
    if a.stage in {"all", "l1", "rephrase"}:
        need = []
        for p in files:
            aid = str(p.relative_to(lib)).replace(os.sep, "/")
            rec = results.get(aid)
            if not rec or rec.get("kind") not in {"video", "audio"}:
                continue
            if a.stage != "rephrase" and rec.get("transcript"):
                continue
            if p.suffix.lower() in VIDEO_EXT and not rec.get("audio"):
                continue
            need.append((str(p), str(lib), keymap.get(aid, aid)))
        label = "重算句子" if a.stage == "rephrase" else "待转写"
        print(f"L1 {label} {len(need)}", flush=True)
        if need:
            with ProcessPoolExecutor(max_workers=max(1, a.l1_jobs)) as ex:
                futs = {ex.submit(stage_l1_one, t): t[0] for t in need}
                done = 0
                for f in as_completed(futs):
                    done += 1
                    try:
                        aid, tr = f.result()
                    except Exception as e:                          # noqa: BLE001
                        print(f"  ! ASR {Path(futs[f]).name}: {e}", file=sys.stderr)
                        continue
                    if tr and aid in results:
                        results[aid]["transcript"] = tr
                    elif a.stage == "rephrase" and aid in results:
                        results[aid]["transcript"] = None
                    print(f"  L1 {done}/{len(need)}", flush=True)

    assets = [results[k] for k in sorted(results)]
    apply_grades(assets)
    n_l2 = merge_l2(lib, assets)
    index = write_index(lib, assets, n_l2, a)

    c = index["counts"]
    print(f"\n完成：{c['assets']} 个素材（视频 {c['video']} / 照片 {c['photo']}），"
          f"总时长 {c['duration_min']} 分钟，有语音 {c['with_speech']} 个，L2 记录 {c['l2_records']}")
    print(f"L2 覆盖 {c['l2_coverage']*100:.1f}%（{c['l2_minutes']} 分钟 / {c['l2_assets']} 个素材）")
    l0_s = sum(x.get("l0_seconds") or 0 for x in assets)
    if l0_s and c["duration_min"]:
        per_hour = l0_s / 60 / (c["duration_min"] / 60)
        print(f"L0 机器时间累计 {l0_s/60:.1f} 分钟，折合每 60 分钟素材 {per_hour:.1f} 分钟"
              f"（硬指标 ≤8；含历次重跑的累计，只作量级参考）")
    if c.get("l2_budget"):
        note = c.get("l2_budget_note")
        if note:
            print(f"  超软上限 {c['l2_budget']*100:.0f}%·原因：{note}")
        else:
            print("  超软上限但没留原因：补一句 --l2-budget-note \"为什么这次多花钱\"，"
                  "它会存在 _cutskills/l2_budget.json 里，下一轮不用重复解释。")
    print(f"索引 {index_path}")
    print(f"分级表 {sysdir / 'pickup.md'}")
    print(f"人类可读索引 {lib / '素材索引.md'}")
    print(f"L2 队列 {sysdir / 'l2_queue.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
