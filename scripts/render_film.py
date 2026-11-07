#!/usr/bin/env python3
"""CutSkills · C 段：EDL → 成片。

只做四件事，每一件都能在成片上验：**画幅统一、声音对齐、字幕贴准、响度达标**。
动效/花字/音效留到 C2（这一版只保证"能看、能听懂、不爆音"）。

    render_film.py --project <项目目录> --library <素材库> [--style styles/talking-head-v1.yaml]

产出（都在项目的 `work/` 与 `out/` 下）：
    out/film.mp4          成片
    out/subs.json         字幕轨（给 verify_film.py 用，也是可改的真值）
    work/v**.mp4          逐段画面（统一画幅/帧率）
    work/a**.wav          逐段声音（30ms 淡变）
    work/audio_norm.wav   归一后的声轨（-14 LUFS）
    render-report.md      这次渲染做了什么

为什么按段渲染再拼：单通道 filtergraph 一次合成，加覆盖层时所有素材都要重编码，
一旦某段要改就整片重来；逐段渲染 + 无损 concat 才能做到"只重渲受影响的段"。

字幕为什么用 PIL 画 PNG 再 overlay：这台机器的 ffmpeg 没编 libass/drawtext
（`-h filter=ass` 会报 Unknown filter），所以不能 `subtitles=` 或 `drawtext=`。
用 PIL 拿真字体（阿里妈妈数黑）画成透明 PNG，再按时间窗 overlay，效果等价、
字体不替代、还能对每条字幕单独看完再改。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

import yaml

W_FALLBACK = 1080
H_FALLBACK = 1920


def run(cmd: list[str], quiet: bool = True, fatal: bool = True) -> subprocess.CompletedProcess:
    """跑一条外部命令。默认失败就停——**没停过一次的代价是交出去一个坏成片**。

    2026-11-07 实测：最后那次编码被杀（返回码 -9，stderr 全空），脚本照样打印
    「成片 79.07s 字幕 38 条」，而那个 mp4 连 moov atom 都没有，播放器直接打不开。
    只要不看返回码，就会出现「脚本说成功、文件是坏的」这种最难查的状态。
    """
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        head = " ".join(str(c) for c in cmd[:6])
        print(f"！命令失败（退出码 {r.returncode}）：{head} …", file=sys.stderr)
        if r.stderr:
            print(r.stderr[-1200:], file=sys.stderr)
        if fatal:
            raise SystemExit(f"停在失败的这一步：{head}")
    return r


def sample_rgb(path: Path, t: float, vf: str) -> tuple[int, int, int] | None:
    """缩到 1×1 取均值色：用来对齐不同镜头之间的白平衡。

    冷暖不是靠眼睛猜的——口播是暖光室内、工地是日光灰，直接拼在一起就割裂。
    量出每一镜的 R/B 比，把它们往同一比例上拉，比全局加暖安全得多
    （全局加暖会把本来就对的镜头一起染黄）。
    """
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path),
                        "-frames:v", "1", "-vf", vf + ",scale=1:1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True)
    out = r.stdout or b""
    return (out[0], out[1], out[2]) if len(out) >= 3 else None


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout or "{}")


def find_font(name: str) -> Path | None:
    """样式包里写的是字体名，这里在系统字体目录里找真文件。

    PIL 不能用 `local()` 那种兜底——找不到就是找不到，宁可报错也不静默换成别的字体，
    否则每次换机器字幕字形都会悄悄变一次。
    """
    stem = name.replace(" ", "").lower()
    dirs = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
            Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
    cands: list[Path] = []
    for d in dirs:
        if d.exists():
            for p in d.rglob("*"):
                if p.suffix.lower() in {".otf", ".ttf", ".ttc", ".ttc"}:
                    cands.append(p)
    for p in cands:
        if stem in p.name.replace(" ", "").lower():
            return p
    for p in cands:
        n = p.name.lower()
        if "sourcehansanscn-heavy" in n or "alimama" in n or "pingfang" in n:
            return p
    return None


# ---------------------------------------------------------------- 字幕

def load_chars(lib: Path, aid: str) -> list[tuple[str, float, float]]:
    """从 ASR 缓存取逐字时间和字。切片过的文件按偏移拼起来。

    缓存按 legacy_key（首次建索引时的路径哈希）命名，素材改名后仍能对上。
    这里取的是**字**，不是词——中文按字断字幕才不会断在词中间。
    """
    import hashlib
    d = lib / "_cutskills" / "cache" / "asr"
    key = hashlib.sha1(aid.encode("utf-8")).hexdigest()[:12]
    idx = lib / "_cutskills" / "index.json"
    if idx.exists():
        try:
            for rec in json.loads(idx.read_text()).get("assets", []):
                if rec.get("id") == aid and rec.get("legacy_key"):
                    key = rec["legacy_key"]
                    break
        except json.JSONDecodeError:
            pass
    out: list[tuple[str, float, float]] = []
    chunks = []
    for f in d.glob(f"{key}-v3c*.json"):
        m = re.search(r"-v3c(\d+)\.json$", f.name)
        if m:
            chunks.append((int(m.group(1)), f))
    files = sorted(chunks) if chunks else [(0, d / f"{key}.json")]
    for off, f in files:
        if not f.exists():
            continue
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        ts, toks = raw.get("timestamps") or [], raw.get("tokens") or []
        if not ts or len(ts) != len(toks):
            continue
        st = [float(t) + off for t in ts]
        for i, tok in enumerate(toks):
            e = st[i + 1] if i + 1 < len(st) else st[i] + 0.3
            out.append((str(tok), round(st[i], 3), round(e, 3)))
    return out


def build_cues(segments: list[dict], index_by_id: dict, chars_cache: dict,
               lib: Path, max_chars: int) -> list[dict]:
    """把逐字时间拼成字幕条，时间换算到输出时间轴。

    output_time = 字.start - seg_in + seg_start —— 硬规则第 6 条。
    断句优先看标点和停顿：一条字幕不跨句、不跨超过 0.35s 的停顿。
    """
    cues: list[dict] = []
    for seg in segments:
        src = seg.get("audio") or {}
        aid = src.get("asset") or seg["asset"]
        s_in = float(src.get("in", seg["in"]))
        s_out = float(src.get("out", seg["out"]))
        rec = index_by_id.get(aid) or {}
        phrases = (rec.get("transcript") or {}).get("phrases") or []
        if not phrases:
            continue
        chars = chars_cache[aid] if aid in chars_cache else \
            chars_cache.setdefault(aid, load_chars(lib, aid))
        items: list[tuple[str, float, float]] = []
        for ph in phrases:
            if ph["end"] <= s_in + 0.01 or ph["start"] >= s_out - 0.01:
                continue
            text = ph["text"]
            # 词表纠错替换前后字数不变（石灰机→石灰基），所以能直接按序对上缓存里的字。
            # 对齐要**逐字对字**，不能只看字数：±0.06 的容差常在句尾多咬进下一个字，
            # 一旦字数不符就掉到「按字数均分」，那个近似会把已经被剪掉的字重新算进窗口里。
            cs = [c for c in chars if ph["start"] - 0.08 <= c[1] <= ph["end"] + 0.08]
            hit = None
            for off in range(0, max(0, len(cs) - len(text)) + 1):
                if off + len(text) <= len(cs) and \
                        all(cs[off + i][0] == text[i] for i in range(len(text))):
                    hit = cs[off:off + len(text)]
                    break
            if hit:
                items += [(text[i], hit[i][1], hit[i][2]) for i in range(len(text))]
            else:                                  # 对不上就按字数均分，宁可粗一点也不错位
                span = (ph["end"] - ph["start"]) / max(1, len(text))
                items += [(text[i], ph["start"] + i * span, ph["start"] + (i + 1) * span)
                          for i in range(len(text))]
        # 段窗口是硬边界，按**字**裁，不是按整句裁。
        # 2026-11-07 踩过：一句 phrase 只要和窗口沾边就被整句收进字幕，于是成片把
        # 「进口的艺术」「相当相当」剪掉了、字幕却还留着——声音是对的，字是错的，
        # 而 D 段只数字幕条数，全绿。这一条只能按字的时间戳裁。
        items = [it for it in items if s_in - 0.06 <= it[1] < s_out - 0.01]

        seg_start = seg["_start"]
        dur = seg.get("_dur_q") or seg["_dur"]
        buf: list[tuple[str, float, float]] = []

        def flush():
            nonlocal buf
            if not buf:
                return
            a = max(0.0, buf[0][1] - s_in) + seg_start
            b = min(dur, buf[-1][2] - s_in) + seg_start
            txt = "".join(t for t, _, _ in buf).strip()
            if txt and b > a:
                cues.append({"start": round(a, 3), "end": round(b, 3), "text": txt})
            buf = []

        for idx2, it in enumerate(items):
            ch, st, _ = it
            nxt = items[idx2 + 1][1] if idx2 + 1 < len(items) else None
            if buf and buf[-1][2] < st - 0.35:            # 换气，收口
                flush()
            if buf and len("".join(t for t, _, _ in buf)) + 1 > max_chars:
                flush()
            buf.append(it)
            cur = len("".join(t for t, _, _ in buf))
            # 目标 7–9 字一条（样式包写的），所以到了 7 字、后面又有小停顿就断，
            # 不要一路攒到 12 字上限才断——那样一分钟只有 23 条，读起来是"一整块"。
            if ch in "。？！」…":
                flush()
            elif ch in "，、；：" and cur >= 4:
                flush()
            elif cur >= 9:                                   # 硬上限 9 字
                flush()
            elif cur >= 6 and (nxt is None or nxt - it[2] >= 0.12):
                flush()
        flush()
    cues.sort(key=lambda c: c["start"])
    # 纯标点不单独成条：「？」「上。」这种一条 0.7 秒的闪字，比没有字幕还差
    cleaned: list[dict] = []
    for c in cues:
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", c["text"]):
            if cleaned:
                cleaned[-1]["end"] = max(cleaned[-1]["end"], c["end"])
            continue
        cleaned.append(c)
    cues = cleaned
    out = []
    for c in cues:                                    # 重叠的往后推，播不掉的两条留第一条
        if out and c["start"] < out[-1]["end"] - 0.01:
            c["start"] = out[-1]["end"]
            if c["end"] - c["start"] < 0.2:
                continue
        out.append(c)
    return out


def render_sub_pngs(cues: list[dict], font: Path, size: int, w: int, band: tuple[int, int],
                    out_dir: Path) -> tuple[list[Path], dict]:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    f = ImageFont.truetype(str(font), size)
    y0, y1 = band
    bw, bh = w, y1 - y0
    paths: list[Path] = []
    overflow = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(cues):
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        # 先画阴影层再画字：直接 stroke 只能描边，没有投影的柔和过渡
        sh = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d_sh = ImageDraw.Draw(sh)
        d_sh.text((bw / 2 + 2, bh / 2 + 5), c["text"], font=f, fill=(0, 0, 0, 190), anchor="mm")
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        img.alpha_composite(sh)
        d = ImageDraw.Draw(img)
        d.text((bw / 2, bh / 2), c["text"], font=f, fill=(255, 255, 255, 255),
               anchor="mm", stroke_width=5, stroke_fill=(0, 0, 0, 245))
        p = out_dir / f"sub{i:04d}.png"
        img.save(p)
        paths.append(p)
        if d.textlength(c["text"], font=f) > w - 80:
            overflow.append(c["text"])
    return paths, {"overflow": overflow, "band": [y0, y1], "font": str(font), "size": size}


def render_cards(segs: list[dict], projects: list[dict], font: Path, w: int,
                 out_dir: Path) -> tuple[list[dict], dict]:
    """把 EDL 里的 `card` 画成透明 PNG：一句话、黑底圆角、压在字幕上方。

    字卡承载的是**口播里没有的事实**（数字、对比、专名）。不能拿它当字幕用——
    字幕跟着话音一个字一个字走，字卡是一块静止的牌子。所以一张卡只放一句话，
    超过 24 字就报出来，让写稿的人自己断，不在这里缩字号。
    """
    from PIL import Image, ImageDraw, ImageFont

    if not projects:
        return [], {}
    size = 58
    f = ImageFont.truetype(str(font), size)
    band_h = 220
    y = 900                                   # 卡片中线：字幕带（1294）上方，避开平台底栏
    out_dir.mkdir(parents=True, exist_ok=True)
    out, long_texts = [], []
    for i, c in enumerate(projects):
        text = c["text"]
        if len(text) > 24:
            long_texts.append(text)
        tw = f.getlength(text)
        pad_x, pad_y = 44, 30
        bw = int(min(w - 40, tw + pad_x * 2))
        img = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
        d0 = ImageDraw.Draw(img)
        x0, y0 = (w - bw) // 2, (band_h - (size + pad_y * 2)) // 2
        d0.rounded_rectangle([x0, y0, x0 + bw, y0 + size + pad_y * 2], radius=22,
                             fill=(0, 0, 0, 185), outline=(255, 255, 255, 60), width=2)
        d0.text((w / 2, band_h / 2), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
        p = out_dir / f"card{i:03d}.png"
        img.save(p)
        out.append({"path": str(p), "y": y - band_h // 2,
                    "start": c["start"], "end": c["end"], "text": text})
    return out, {"too_long": long_texts}


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", required=True)
    ap.add_argument("--style", default=None)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--no-punch", dest="punch", action="store_false",
                    help="不做推镜切：口播长段保持一镜到底（口播中位会超标，只在刻意要长镜头时用）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve()
    edl = json.loads((proj / "edl.json").read_text())
    if a.style:
        style_path = Path(a.style).expanduser()
    else:
        name = edl.get("style_pack") or "talking-head-v1"
        style_path = Path(__file__).resolve().parent.parent / "styles" / f"{name}.yaml"
    style = yaml.safe_load(style_path.read_text())
    W = style["canvas"]["width"] if W_FALLBACK is None else style["canvas"]["width"]
    H = style["canvas"]["height"]
    FPS = style["canvas"]["fps"]

    index = json.loads((lib / "_cutskills" / "index.json").read_text())
    by_id = {x["id"]: x for x in index["assets"]}
    by_alias = {al: x for x in index["assets"] for al in (x.get("aliases") or [])}
    chars_cache: dict[str, list[tuple[str, float, float]]] = {}

    segs = edl["segments"]
    t = 0.0
    for s in segs:
        s["_start"] = round(t, 3)
        s["_dur"] = round(s["out"] - s["in"], 3)
        t = round(t + s["_dur"], 3)
    total = t

    work = proj / "work"
    out = proj / "out"
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    log: list[dict] = []
    vlist: list[Path] = []
    alist: list[Path] = []
    man_path = work / "segments.json"
    old_man = json.loads(man_path.read_text()) if man_path.exists() else {}
    man: dict = {}

    # 口播长段拆镜：单机位一条长镜头会让「口播中位」永远超标（71s 的片子里口播中位 4.95s）。
    # 拆法是**推镜切**：同一段画面按 1.0× / 1.12× 交替，切点吸附到字界，
    # 声音不拆（一整段连续），所以听不出断，只是画面在换景别。
    # 这是样式包的节奏参数，不是剪辑决定——所以放在 C 段，EDL 不用改。
    target = float(style["shots"]["talking_median_s"])
    shots: list[dict] = []
    for i, s in enumerate(segs, 1):
        if a.punch and s.get("medium") == "talking" and s["_dur"] > target * 1.6:
            aid = (s.get("audio") or {}).get("asset") or s["asset"]
            chars = chars_cache[aid] if aid in chars_cache else \
                chars_cache.setdefault(aid, load_chars(lib, aid))
            starts = [c[1] for c in chars]
            k = max(2, int(round(s["_dur"] / target)))
            bounds = [s["in"]]
            for j in range(1, k):
                want = s["in"] + s["_dur"] * j / k
                if starts:
                    near = min(starts, key=lambda t: abs(t - want))
                    if abs(near - want) <= 0.35:
                        want = near
                if want - bounds[-1] >= 0.8:
                    bounds.append(round(want, 3))
            bounds.append(s["out"])
        else:
            bounds = [s["in"], s["out"]]
        for j in range(len(bounds) - 1):
            shots.append({"seg": i, "s": s, "in": bounds[j], "out": bounds[j + 1],
                          "zoom": 1.0 if j % 2 == 0 else 1.12})

    # 每段时长必须落在整帧上：不然 23 个镜头各多半帧，成片就比声轨长半秒，
    # 音画一开始差 0.55s，字幕和口型就全漂了。所以先量化、再按量化值重算时间轴。
    for i, s in enumerate(segs, 1):
        mine = [x for x in shots if x["seg"] == i]
        for x in mine:
            x["frames"] = max(1, int(round((x["out"] - x["in"]) * FPS)))
            x["dur"] = round(x["frames"] / FPS, 4)
        s["_dur_q"] = round(sum(x["frames"] for x in mine) / FPS, 4)
    t = 0.0
    for s in segs:
        s["_start"] = round(t, 4)
        t = round(t + s["_dur_q"], 4)
    total = t

    # ---- 白平衡对齐：先量每一镜的 R/B，再统一到口播段的水平
    def base_vf(hdr: bool) -> str:
        v = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={W}:{H},setsar=1")
        if hdr:
            v += (",colorspace=all=bt709:iall=bt2020:fast=1,"
                  "eq=contrast=1.14:gamma=1.05:saturation=1.08,"
                  "colortemperature=temperature=5200:mix=0.7")
        return v

    ratios: dict[int, float] = {}
    for idx, sh in enumerate(shots, 1):
        rec0 = by_id.get(sh["s"]["asset"]) or by_alias.get(sh["s"]["asset"]) or {}
        hdr0 = bool({"hdr_hlg", "hdr_pq", "dolby_vision", "ten_bit"} & set(rec0.get("handling") or []))
        mid = (sh["in"] + sh["out"]) / 2
        rgb = sample_rgb(lib / sh["s"]["asset"], mid, base_vf(hdr0))
        if rgb and rgb[2] > 4:
            ratios[idx] = rgb[0] / rgb[2]
    talk_r = [ratios[i] for i, sh in enumerate(shots, 1)
              if sh["s"].get("medium") == "talking" and i in ratios]
    ref = statistics.median(talk_r) if talk_r else 1.25
    for idx, sh in enumerate(shots, 1):
        r = ratios.get(idx)
        if not r:
            sh["gain"] = 1.0
            continue
        g = max(0.90, min(1.12, ref / r))
        sh["gain"] = round(g, 3)

    for idx, sh in enumerate(shots, 1):
        s, i = sh["s"], sh["seg"]
        dur = sh["dur"]
        src = lib / s["asset"]
        rec = by_id.get(s["asset"]) or by_alias.get(s["asset"]) or {}
        handling = rec.get("handling") or []
        hdr = bool({"hdr_hlg", "hdr_pq", "dolby_vision", "ten_bit"} & set(handling))
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W}:{H},setsar=1")
        if sh["zoom"] > 1.0:
            z = sh["zoom"]
            vf += (f",crop=iw/{z}:ih/{z}:(iw-iw/{z})/2:(ih-ih/{z})/2,scale={W}:{H}")
        if hdr:
            # 这台机器的 ffmpeg 没编 zscale，所以走 colorspace 做 BT.2020/HLG → BT.709，
            # 再用轻微对比/伽马补偿把 HLG 的"平"拉回来（按真实帧多版对照选的参数）。
            vf += (",colorspace=all=bt709:iall=bt2020:fast=1,"
                   "eq=contrast=1.14:gamma=1.05:saturation=1.08,"
                   "colortemperature=temperature=5200:mix=0.7")
        if abs(sh["gain"] - 1.0) > 0.005:
            vf += f",colorchannelmixer=rr={sh['gain']:.3f}:bb={1 / sh['gain']:.3f}"
        vf += f",fps={FPS},format=yuv420p"
        vp = work / f"v{idx:03d}.mp4"
        sig = {"asset": s["asset"], "in": round(sh["in"], 3), "out": round(sh["out"], 3),
               "zoom": sh["zoom"], "crf": a.crf, "w": W, "h": H, "fps": FPS,
               "frames": sh["frames"], "vf": vf}
        man[str(idx)] = sig
        # 只认同一套编号下的缓存。早先按「2 位数段号」存过的文件不能再复用——
        # 那批文件是"整段"，一旦被当成"镜头"拼进去，成片会凭空长出一大截
        # （实测 71.15s 的片被拼成 95.53s，音画不同步 24 秒）。
        cached = old_man.get(str(idx)) == sig and vp.exists() and vp.stat().st_size > 0
        if cached:
            vlist.append(vp)
        else:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{sh['in']:.3f}",
                 "-t", f"{dur:.3f}", "-i", str(src), "-an", "-vf", vf,
                 "-c:v", "libx264", "-crf", str(a.crf), "-preset", "veryfast",
                 "-pix_fmt", "yuv420p", "-r", str(FPS), str(vp)])
            vlist.append(vp)
        sh["hdr"] = hdr
        sh["cached"] = cached

    # 声音按「段」走，不按镜头走：推镜切只换画面，声音必须连续
    for i, s in enumerate(segs, 1):
        aud = s.get("audio") or {}
        aid = aud.get("asset") or s["asset"]
        a_in = float(aud.get("in", s["in"]))
        dur = s["_dur_q"]
        ap_ = work / f"a{i:02d}.wav"
        fade_out = max(0.0, dur - 0.03)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a_in:.3f}", "-t", f"{dur:.3f}",
             "-i", str(lib / aid), "-vn", "-ac", "2", "-ar", "48000",
             "-af", f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03",
             "-c:a", "pcm_s16le", str(ap_)])
        alist.append(ap_)
        log.append({"n": i, "asset": s["asset"], "in": s["in"], "out": s["out"],
                    "dur": s["_dur"], "start": s["_start"], "medium": s.get("medium"),
                    "hdr": any(x["hdr"] for x in shots if x["seg"] == i),
                    "audio": aid,
                    "shots": sum(1 for x in shots if x["seg"] == i)})
        print(f"  段 {i:02d}  {s['_dur']:6.2f}s  {s['asset'].split('/')[-1][:44]}"
              f"  镜头 {log[-1]['shots']}{'  (HDR→SDR)' if log[-1]['hdr'] else ''}"
              f"{'  [全部命中缓存]' if all(x.get('cached') for x in shots if x['seg'] == i) else ''}")

    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    # 把实际切出来的镜头清单落盘：D 段要按**成片里的镜头**判节奏，
    # 不是按 EDL 的段——推镜切之后两者不一样，拿 EDL 判会把好片判成不达标。
    (out / "shots.json").write_text(json.dumps(
        [{"n": idx, "seg": sh["seg"], "in": round(sh["in"], 3), "out": round(sh["out"], 3),
          "dur": sh["dur"], "frames": sh["frames"], "zoom": sh["zoom"],
          "medium": sh["s"].get("medium"), "asset": sh["s"]["asset"]}
         for idx, sh in enumerate(shots, 1)], ensure_ascii=False, indent=1), encoding="utf-8")

    # 无损拼接：逐段同参数编码后 concat copy，不再过一次编码
    vl = work / "video_list.txt"
    vl.write_text("".join(f"file '{p}'\n" for p in vlist))
    video = work / "video.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(vl),
         "-c", "copy", str(video)])

    al = work / "audio_list.txt"
    al.write_text("".join(f"file '{p}'\n" for p in alist))
    audio = work / "audio.wav"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(al),
         "-c", "copy", str(audio)])

    # 拼接后允许有半帧级误差（每个 mp4 容器自带一点余量），真正的等长由最后那次
    # 编码的 -t 统一裁到 total。这里只提示，不中断。
    vdur = float(probe(video).get("format", {}).get("duration") or 0)
    adur = float(probe(audio).get("format", {}).get("duration") or 0)
    if abs(vdur - adur) > 0.2:
        print(f"  提示：拼接后画面 {vdur:.2f}s / 声音 {adur:.2f}s，最后会一起裁到 {total:.2f}s")

    # 两遍 loudnorm：先量再归，一次到位会因动态范围变化而偏
    L = style["audio"]
    m = run(["ffmpeg", "-hide_banner", "-i", str(audio), "-af",
             f"loudnorm=I={L['loudness_lufs']}:TP={L['true_peak_dbtp']}:print_format=json",
             "-f", "null", "-"])
    meas = {}
    try:
        txt = (m.stderr or "").split("{")
        meas = json.loads("{" + txt[-1]) if len(txt) > 1 else {}
    except json.JSONDecodeError:
        meas = {}
    norm = work / "audio_norm.wav"
    if meas.get("input_i"):
        af = (f"loudnorm=I={L['loudness_lufs']}:TP={L['true_peak_dbtp']}:"
              f"measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
              f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
              f"linear=true:print_format=summary")
    else:
        af = f"loudnorm=I={L['loudness_lufs']}:TP={L['true_peak_dbtp']}"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-af", af,
         "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(norm)])

    # 字幕
    sub = style["subtitle"]
    font = find_font(sub["font"])
    if not font:
        print("找不到样式包要求的字体，字幕不做", file=sys.stderr)
        cues = []
        pngs, info = [], {}
    else:
        cues = build_cues(segs, by_id, chars_cache, lib, int(sub["max_chars_per_line"]))
        band_top = max(0, int(sub["center_y_px"]) - 260)
        band_bot = min(H, int(sub["center_y_px"]) + 260)
        pngs, info = render_sub_pngs(cues, font, int(sub["size_px"]), W,
                                     (band_top, band_bot), work / "subs")
        hold = sub.get("hold_min_s", 0.75)
        for c in cues:
            if c["end"] - c["start"] < hold:
                c["end"] = round(c["start"] + hold, 3)

    speech_chars = sum(len(c["text"]) for c in cues)
    speech_s = sum(min(c["end"], total) - c["start"] for c in cues)
    (out / "subs.json").write_text(json.dumps(
        {"cues": cues, "band": info.get("band"), "font": info.get("font"),
         "size": info.get("size"), "overflow": info.get("overflow") or [],
         "speech_chars": speech_chars,
         "avg_chars": round(speech_chars / len(cues), 2) if cues else 0,
         "chars_per_s": round(speech_chars / speech_s, 2) if speech_s else 0},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # 字卡：跟着段的时间轴走，压在字幕带上方的固定高度
    card_proj = []
    for s in segs:
        c = s.get("card")
        if not c:
            continue
        d = float(c.get("dur") or (s["_dur_q"] - 0.6))
        d = max(1.2, min(d, s["_dur_q"] - 0.2))
        at = c.get("at")
        start = round(s["_start"] + (float(at) if isinstance(at, (int, float)) else 0.25), 3)
        card_proj.append({"text": str(c["text"]), "start": start,
                          "end": round(start + d, 3)})
    cards, card_info = render_cards(segs, card_proj, font, W, work / "cards") if (font and card_proj) else ([], {})

    # 最后一次编码：贴字幕 + 贴字卡 + 上归一后的声轨。
    #
    # 不把所有叠加层塞进一个滤镜图：一张静止 PNG 要跨几十秒的 enable 窗口，
    # 就必须 -loop 1，而三十多张图一起无限循环解码会把内存打爆（SIGKILL，返回码 -9，
    # stderr 全空）；不加 -loop 又会变成「早期画得出来、70 秒以后一个像素都没有」。
    # 所以按时间切成几段，每段只带自己那几张图（2–4 个输入），代价是多一次编码。
    overlays = [{"path": p, "y": info["band"][0], "loop": True,
                 "start": cues[i]["start"], "end": cues[i]["end"]}
                for i, p in enumerate(pngs)]
    overlays += [{"path": c["path"], "y": c["y"], "loop": True, "card": True,
                  "start": c["start"], "end": c["end"]} for c in cards]

    film = out / "film.mp4"

    # 每个镜头单独贴自己的那几层（一个镜头最多几条字幕 + 一张卡），拼起来再上声轨。
    # 为什么不是一次贴完全片：一张静止 PNG 要跨它的 enable 窗口就必须 -loop 1，
    # 三十多张图一起循环会把内存打爆（SIGKILL、返回码 -9、stderr 全空）；
    # 不加 -loop 又会出现「前 20 秒画得出来、70 秒以后一个像素都没有」。
    # 按镜头贴，每次只有 1–4 个输入、两三秒的画面，两个坑都不踩。
    shot_start, tt = [], 0.0
    for sh in shots:
        shot_start.append(tt)
        tt = round(tt + sh["dur"], 4)
    parts = []
    for idx, sh in enumerate(shots, 1):
        ss, sd = shot_start[idx - 1], sh["dur"]
        mine = [o for o in overlays
                if not o.get("card")
                and min(sd, o["end"] - ss) > max(0.0, o["start"] - ss) + 0.02]
        srcp = work / f"v{idx:03d}.mp4"
        if not mine:
            parts.append(srcp)
            continue
        op = work / f"ov{idx:03d}.mp4"
        inputs = ["-i", str(srcp)]
        for o in mine:
            lim = max(1.0, min(sd, o["end"] - ss) + 0.6)
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{lim:.2f}", "-i", o["path"]]
        fc, prev = [], "[0:v]"
        for n, o in enumerate(mine, 1):
            wa = max(0.0, o["start"] - ss)
            wb = min(sd, o["end"] - ss)
            fc.append(f"{prev}[{n}:v]overlay=0:{o['y']}:enable='between(t,{wa:.3f},{wb:.3f})'[v{n}]")
            prev = f"[v{n}]"
        fc.append(f"{prev}format=yuv420p[vo]")
        # -t 必须钉死成镜头时长：循环输入的帧会比画面多，不钉的话每个贴图层都长一点，
        # 二十个镜头累加下来成片比 EDL 多 12 秒，后面每个镜头的时间点全错位
        # （实测 video_ov 91.47s vs 79.07s，字卡在自己的时间里怎么找都找不到）。
        run(["ffmpeg", "-v", "error", "-y"] + inputs + [
            "-filter_complex", ";".join(fc), "-map", "[vo]", "-an", "-t", f"{sd:.4f}",
            "-c:v", "libx264", "-crf", str(a.crf), "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(op)])
        parts.append(op)
        print(f"  贴字 镜头 {idx:03d}  {ss:5.1f}s  层数 {len(mine)}")
        cur = op
        for o in [x for x in overlays if x.get("card")]:
            wa = max(0.0, o["start"] - ss)
            wb = min(sd, o["end"] - ss)
            if wb <= wa + 0.05:
                continue
            cp = work / f"oc{idx:03d}.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(cur),
                 "-loop", "1", "-framerate", str(FPS), "-t", f"{sd + 1.0:.2f}", "-i", o["path"],
                 "-filter_complex",
                 f"[0:v][1:v]overlay=0:{o['y']}:enable='between(t,{wa:.3f},{wb:.3f})',format=yuv420p",
                 "-an", "-t", f"{sd:.4f}", "-c:v", "libx264", "-crf", str(a.crf), "-preset", "veryfast",
                 "-pix_fmt", "yuv420p", "-r", str(FPS), str(cp)])
            cur = cp
            print(f"  贴卡 镜头 {idx:03d}  {ss + wa:5.1f}–{ss + wb:5.1f}s")
        parts[-1] = cur
    ol = work / "ov_list.txt"
    ol.write_text("".join(f"file '{p}'\n" for p in parts))
    video_ov = work / "video_ov.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(ol),
         "-c", "copy", str(video_ov)])
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video_ov), "-i", str(norm),
         "-map", "0:v", "-map", "1:a", "-t", f"{total:.4f}",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(film)])

    fin = probe(film)
    fv = next((x for x in fin.get("streams", []) if x.get("codec_type") == "video"), {})
    fa = next((x for x in fin.get("streams", []) if x.get("codec_type") == "audio"), {})
    if abs(float(fv.get("duration") or 0) - float(fa.get("duration") or 0)) > 0.15:
        print(f"！成片音画不等长：画面 {fv.get('duration')}s / 声音 {fa.get('duration')}s",
              file=sys.stderr)
        return 3

    rep = ["# 渲染报告", "",
           f"- 项目：`{proj.name}`  风格包：`{style['name']}`",
           f"- 画布：{W}×{H} @ {FPS}fps  目标响度：{L['loudness_lufs']} LUFS / "
           f"真峰 {L['true_peak_dbtp']} dBTP",
           f"- 段数 {len(segs)}  总长 {total:.2f}s  字幕 {len(cues)} 条",
           f"- 字体：`{info.get('font') or '—'}`（样式包要求 `{sub['font']}`）", "",
           "| # | 素材 | 入点 | 出点 | 时长 | 成片起点 | 画幅处理 |", "|---|---|---|---|---|---|---|"]
    for r in log:
        rep.append(f"| {r['n']} | {r['asset'].split('/')[-1]} | {r['in']:.2f} | {r['out']:.2f} | "
                   f"{r['dur']:.2f} | {r['start']:.2f} | {'HDR→SDR' if r['hdr'] else '—'} |")
    if cards:
        rep += ["", "## 字卡", ""] + [f"- {c['start']:.2f}–{c['end']:.2f}s　{c['text']}" for c in cards]
    if (card_info or {}).get("too_long"):
        rep += ["", "## 字卡过长（>24 字，自己断句，不缩字号）", ""] + \
               [f"- {t}" for t in card_info["too_long"]]
    if info.get("overflow"):
        rep += ["", "## 字幕超宽（已画，但可能贴边）", ""] + [f"- {t}" for t in info["overflow"]]
    rep += ["", f"成片：`{film}`"]
    print(f"  成片 {film.name}  字卡 {len(cards)} 块" if cards else "")
    (proj / "render-report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")

    print(f"成片 {film}  {total:.2f}s  字幕 {len(cues)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
