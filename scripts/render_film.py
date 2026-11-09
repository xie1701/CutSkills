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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pack_style  # noqa: E402  包装模板 → 像素参数 / 壳体叠加层

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
        print(f"[stderr] {r.stderr[-1500:]!r}", file=sys.stderr)
        print(f"[cmd] {' '.join(str(c) for c in cmd)}", file=sys.stderr)
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
               lib: Path, max_chars: int, soft_chars: int = 6) -> list[dict]:
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
        # 收字窗口必须和 build_edl 算 evidence 的窗口**逐字一致**（`ain-0.02 ~ aout-0.02`）。
        # 敢差 40ms 就会出这种事：字幕上留着「那么因为有不同的需」，成片里这句已经被剪掉——
        # 声音对了、条数对了、时长对了，只有读字幕的人才看得出。
        items = [it for it in items if s_in - 0.02 <= it[1] < s_out - 0.02]

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
                # 带上这条字幕出自哪一格（seg 的 n）。验收靠它核对归属，
                # 不靠「时间段落在谁身上」——两段用同一支素材时，按时间找宿主会找错。
                cues.append({"start": round(a, 3), "end": round(b, 3), "text": txt,
                             "el": seg.get("n")})
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
            elif cur >= max_chars:                           # 硬上限（模板定）
                flush()
            elif cur >= soft_chars and (nxt is None or nxt - it[2] >= 0.12):
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


def ttf(font: Path, size: int, index: int = 0):
    """开字体。`index` 是 ttc 里的 face 序号——苹方第 0 个 face 是 HK Regular，
    不传序号就静默拿到错字重，画出来"说不清哪里不对"。"""
    from PIL import ImageFont
    return ImageFont.truetype(str(font), size, index=index)


def render_sub_pngs(cues: list[dict], font: Path, size: int, w: int, band: tuple[int, int],
                    out_dir: Path, stroke: int = 5, shadow: bool = True,
                    fill: tuple = (255, 255, 255, 255), font_index: int = 0) -> tuple[list[Path], dict]:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    f = ttf(font, size, font_index)
    y0, y1 = band
    bw, bh = w, y1 - y0
    paths: list[Path] = []
    overflow = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(cues):
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        # 描边和投影二选一（跨族铁律第 2 条）：宋体描边会糊笔画，黑体才用描边。
        # 叠着来一定脏，所以这里是模板说了算，不是"两个都给上一点"。
        if shadow:
            sh = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            d_sh = ImageDraw.Draw(sh)
            d_sh.text((bw / 2 + 2, bh / 2 + 5), c["text"], font=f,
                      fill=(0, 0, 0, 190), anchor="mm")
            img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
        d = ImageDraw.Draw(img)
        d.text((bw / 2, bh / 2), c["text"], font=f, fill=fill, anchor="mm",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 245))
        p = out_dir / f"sub{i:04d}.png"
        img.save(p)
        paths.append(p)
        if d.textlength(c["text"], font=f) > w - 80:
            overflow.append(c["text"])
    return paths, {"overflow": overflow, "band": [y0, y1], "font": str(font), "size": size}


def render_cards(segs: list[dict], projects: list[dict], font: Path, w: int,
                 out_dir: Path, emphasis: dict | None = None,
                 H: int = 1920, card_font: Path | None = None) -> tuple[list[dict], dict]:
    """把 EDL 里的 `card` 画成透明 PNG：一句话、黑底圆角、压在字幕上方。

    字卡承载的是**口播里没有的事实**（数字、对比、专名）。不能拿它当字幕用——
    字幕跟着话音一个字一个字走，字卡是一块静止的牌子。所以一张卡只放一句话，
    超过 24 字就报出来，让写稿的人自己断，不在这里缩字号。
    """
    from PIL import Image, ImageDraw, ImageFont

    if not projects:
        return [], {}
    out_dir.mkdir(parents=True, exist_ok=True)
    out, long_texts = [], []
    for i, c in enumerate(projects):
        if c.get("path"):                    # 现成的图（B-roll 卡）直接贴，不再画字
            out.append({"path": c["path"], "y": 0, "kind": "boroll",
                        "start": c["start"], "end": c["end"]})
            continue
        text = c["text"]
        # 花字（kind: keyword）：不是"牌子"，是**从字幕条里抽出、放大、上抬**的那一句话。
        # 区别很实：卡片有底衬、居中不动；花字没有底、字号近 2 倍、颜色是模板指定的强调色。
        # 两者混着用，画面立刻变成"贴纸拼盘"。
        # 身份卡（kind: idcard）：参考片片头那四行——姓名最大、头衔次之、一行小字垫底。
        # 它和"字卡"不是一类东西：没有底衬、不许居中、贴左边界、四行左对齐、字号逐行递减。
        # 做成带底衬的牌子会立刻变成"贴纸拼盘"，那正是它看起来廉价的全部原因。
        if c.get("kind") == "idcard":
            lines = [str(x) for x in (c.get("lines") or []) if str(x).strip()]
            if not lines:
                continue
            sizes = [int(x) for x in (c.get("sizes") or [])] or [66, 48, 40, 32]
            col = str(c.get("color") or "#FFFFFF").lstrip("#")
            fill = tuple(int(col[k:k + 2], 16) for k in (0, 2, 4)) + (255,)
            gap = int(H * 0.008)
            heights = [img_h for img_h in sizes]
            band_h = sum(heights) + gap * (len(lines) - 1) + 40
            img = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
            d0 = ImageDraw.Draw(img)
            y = 20
            x0 = int(w * float(c.get("x") or 0.055))
            for k, ln in enumerate(lines):
                # 身份卡走**卡面字体**：四行从 66px 掉到 32px，小字那一行最吃字体分量。
                # 落到字幕字上会糊；落到标题美术字上会脏——这两条都吃过。
                fnt = ImageFont.truetype(str(card_font or font), sizes[min(k, len(sizes) - 1)])
                # 前两行用纯白，后面用 82% 白：参考片那一层靠明度分层，不靠颜色
                a = 255 if k < int(c.get("strong_lines") or 2) else 210
                d0.text((x0, y), ln, font=fnt, fill=fill[:3] + (a,),
                        stroke_width=0,
                        stroke_fill=(0, 0, 0, 0))
                y += heights[min(k, len(sizes) - 1)] + gap
            p = out_dir / f"card{i:03d}.png"
            img.save(p)
            out.append({"path": str(p), "y": int(H * float(c.get("y") or 0.16)),
                        "kind": "idcard", "start": c["start"], "end": c["end"],
                        "text": lines[0]})
            continue

        big = c.get("kind") in ("keyword", "behind", "cutout") and bool(emphasis)
        if big:
            size = int(emphasis["size_px"])
            em_font = Path(emphasis["font_path"]) if emphasis.get("font_path") else font
            f = ttf(em_font, size, int(emphasis.get("font_index") or 0))
            # 花字默认**一行**；只有一行在 9:16 里放不下时才折两行，而且不缩字号。
            # 缩字号会把"大字"塌成一条又长又细的横幅——那正是它不像大字的全部原因。
            lines = [text]
            if f.getlength(text) > 0.92 * w and len(text) > 2:
                cut = (len(text) + 1) // 2
                lines = [text[:cut], text[cut:]]
            band_h = int(size * (1.30 * len(lines) + 0.55))
            y = int(emphasis["center_y_px"]) - band_h // 2
        else:
            size, band_h, y = 58, 220, 900    # 卡片中线：字幕带上方，避开平台底栏
            f = ImageFont.truetype(str(font), size)
        if len(text) > 24:
            long_texts.append(text)
        img = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
        d0 = ImageDraw.Draw(img)
        if big:
            col = str(emphasis.get("color") or "#F5D33A").lstrip("#")
            fill = tuple(int(col[k:k + 2], 16) for k in (0, 2, 4)) + (255,)
            lh = size * 1.30
            y0 = (band_h - lh * len(lines)) / 2 + lh / 2
            # 红白撞色：参考片有一版花字是"上半白、下半红"两行两种颜色。
            # 这不是装饰，是**把一句话劈成两个概念**（核心 / 不稳），颜色负责分段。
            lc = emphasis.get("line_colors") or []
            grad = emphasis.get("gradient")       # [顶部色, 底部色]：参考片花字是黄→浅黄竖向渐变
            for k, ln in enumerate(lines):
                fl = fill
                if k < len(lc):
                    c2 = str(lc[k]).lstrip("#")
                    fl = tuple(int(c2[j:j + 2], 16) for j in (0, 2, 4)) + (255,)
                sw = int(emphasis.get("stroke_px") or 0)
                if grad:
                    # 先画到一张临时图上取墨迹，再拿竖向渐变去填。
                    # 直接给 fill 只能一个纯色，而参考片的花字明暗差 20 多级——
                    # 这层明暗就是它"亮而不糊"的原因，纯色会显平。
                    tw = int(f.getlength(ln)) + sw * 4
                    th = int(size * 1.6) + sw * 4
                    tmp = Image.new("L", (max(tw, 4), max(th, 4)), 0)
                    ImageDraw.Draw(tmp).text((tw // 2, th // 2), ln, font=f, fill=255,
                                             anchor="mm", stroke_width=sw, stroke_fill=255)
                    top = tuple(int(str(grad[0]).lstrip("#")[j:j + 2], 16) for j in (0, 2, 4))
                    bot = tuple(int(str(grad[1]).lstrip("#")[j:j + 2], 16) for j in (0, 2, 4))
                    g = Image.new("RGB", tmp.size)
                    gd = ImageDraw.Draw(g)
                    for yy in range(tmp.size[1]):
                        r = yy / max(1, tmp.size[1] - 1)
                        gd.line([(0, yy), (tmp.size[0], yy)],
                                fill=tuple(int(top[i] + (bot[i] - top[i]) * r) for i in range(3)))
                    layer = Image.new("RGBA", tmp.size, (0, 0, 0, 0))
                    layer.paste(g, (0, 0), tmp)
                    if sw:
                        # 描边单独画一圈再压上去：渐变层贴上去会把描边盖掉
                        ring = Image.new("RGBA", tmp.size, (0, 0, 0, 0))
                        ImageDraw.Draw(ring).text((tw // 2, th // 2), ln, font=f,
                                                  fill=(0, 0, 0, 0), anchor="mm",
                                                  stroke_width=sw, stroke_fill=(0, 0, 0, 245))
                        img.alpha_composite(ring, (int(w / 2 - tw / 2), int(y0 + k * lh - th / 2)))
                    img.alpha_composite(layer, (int(w / 2 - tw / 2), int(y0 + k * lh - th / 2)))
                    continue
                d0.text((w / 2, y0 + k * lh), ln, font=f, fill=fl, anchor="mm",
                        stroke_width=sw, stroke_fill=(0, 0, 0, 245))
        else:
            tw = f.getlength(text)
            pad_x, pad_y = 44, 30
            bw = int(min(w - 40, tw + pad_x * 2))
            x0, y0 = (w - bw) // 2, (band_h - (size + pad_y * 2)) // 2
            d0.rounded_rectangle([x0, y0, x0 + bw, y0 + size + pad_y * 2], radius=22,
                                 fill=(0, 0, 0, 185), outline=(255, 255, 255, 60), width=2)
            d0.text((w / 2, band_h / 2), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
        p = out_dir / f"card{i:03d}.png"
        img.save(p)
        out.append({"path": str(p), "y": y, "kind": c.get("kind") or "card",
                    "start": c["start"], "end": c["end"], "text": text})
    return out, {"too_long": long_texts}


MASK_BIN = Path(__file__).resolve().parent / "native" / "person_mask"


def person_masks(shot_video: Path, dur: float, fps: float, cache: Path) -> Path | None:
    """对**已经标准化过的镜头**（work/vNNN.mp4）逐帧出人物遮罩，返回灰度 mask 视频。

    为什么在标准化之后的镜头上抠，而不是原始素材上：
    手机竖拍的素材是横向编码 + rotation 元数据，原始帧是 1920×1080 躺着的；
    ffmpeg 会按 rotation 转正，Vision 不会——直接拿原始素材抠出来的 mask
    和画面对不上（实测第一次抠像，白色人形整个跑到画面右边）。
    在 work/ 里的镜头抠，两边都是同一套像素坐标，不用对变换。

    为什么遮罩编成视频而不是 PNG 序列：一套 3 秒镜头 90 张 1080×1920 PNG ≈ 300MB，
    一个项目下来几个 G；编成灰度 h264 就是几百 KB，ffmpeg 用 `format=gray` 取回来一模一样。
    """
    # 抠像器是 Swift 写的（用 macOS 自带 Vision，不下载任何模型）。
    # 第一次用到时现编：`swiftc -O` 1 秒出二进制，比"让用户先跑一条 build 命令"可靠——
    # 忘跑 build 的表现是"静默退化成没有抠像"，那种失败最难发现。
    if not MASK_BIN.exists():
        src = MASK_BIN.with_suffix(".swift")
        if not src.exists():
            return None
        r = subprocess.run(["swiftc", "-O", str(src), "-o", str(MASK_BIN)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ！抠像器编译失败：{(r.stderr or '')[-300:]}", file=sys.stderr)
            return None
    frames = cache / "mask_png"
    out = cache / "mask.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    if cache.exists() and (cache / "mask.mp4").exists():
        pass
    frames.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(MASK_BIN), str(shot_video), "0", f"{max(0.1, dur):.3f}",
                        str(frames), str(fps)], capture_output=True, text=True)
    if r.returncode != 0 or not any(frames.glob("f*.png")):
        return None
    n = len(list(frames.glob("f*.png")))
    run(["ffmpeg", "-v", "error", "-y", "-framerate", f"{fps}", "-i",
         str(frames / "f%04d.png"), "-vf", "format=gray", "-frames:v", str(n),
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         str(out)])
    return out


# BGM 铺底电平。参考片测不出绝对值（没有分轨），按"人声 -14 LUFS、BGM 低 18 dB"起手，
# 之后靠逐帧对照的静音段听感决定要不要再压。
BGM_GAIN_DB = -18


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", required=True)
    ap.add_argument("--style", default=None)

    # 包装模板（不写 --style 时用）
    ap.add_argument("--pack", default=None,
                    help="字幕系统模板：pack-safe-talking / pack-paper-brand / pack-bilingual 等")
    ap.add_argument("--shell", default=None,
                    help="壳体模板（可选）：pack-hud-monitor。只出壳，字幕系统仍由 --pack 定")
    ap.add_argument("--canvas", default="1080x1920", help="WxH；用 --pack 时生效")
    ap.add_argument("--fps", type=int, default=30)

    ap.add_argument("--bgm", default=None,
                    help="背景音乐（本地文件）。参考片全程铺底、不换曲；"
                         "没有 BGM 的片，人声一停就是录音棚空场，这是看着不像的一半")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--no-punch", dest="punch", action="store_false",
                    help="不做推镜切：口播长段保持一镜到底（口播中位会超标，只在刻意要长镜头时用）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve()
    edl = json.loads((proj / "edl.json").read_text())
    plan = json.loads((proj / "plan.json").read_text()) \
        if (proj / "plan.json").exists() else {}

    # 三条路进同一个 style dict：显式 --style / --pack（包装模板）/ EDL 里写的风格包。
    # 包装模板存的是比例，所以画布要现给（--canvas/--fps），默认竖版 1080×1920@30。
    if a.pack:
        cw, ch = (int(x) for x in a.canvas.lower().split("x"))
        style = pack_style.to_style(pack_style.load(a.pack), cw, ch, a.fps)
        style["name"] = pack_style.load(a.pack).get("name")
    elif a.style:
        style_path = Path(a.style).expanduser()
        style = yaml.safe_load(style_path.read_text())
    else:
        name = edl.get("style_pack") or "talking-head-v1"
        style_path = Path(__file__).resolve().parent.parent / "styles" / f"{name}.yaml"
        style = yaml.safe_load(style_path.read_text())
    W = style["canvas"]["width"]
    H = style["canvas"]["height"]
    FPS = style["canvas"]["fps"]
    shell_pack = pack_style.load(a.shell) if a.shell else None

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
        # 1.25 倍才拆：模板写的中位是**上界的一半**，1.6 倍会留下 4.8–5.4s 的洞
        # （实测 5.14s 的口播段不拆，镜头中位直接顶到 5.1s，超模板上限 4.8s）。
        # 口径改成 1.45 倍：逐帧对照里参考片镜头中位 5.0s、我们 4.0s——切得比它碎。
        # 1.25 倍是按"不许超过模板上限"设的，可模板上限是 4.8，参考片自己就 5.0，
        # 说明这条上限该按实测放宽，不是让片去迁就一个保守值。
        if a.punch and s.get("medium") == "talking" and s["_dur"] > target * 1.45:
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
    # 饱和度 1.42：参考片实测 sat_mean 0.511，初版 1.30 出来只有 0.436，
    # 差 0.075 就是"看着发灰"的全部来源。别再加对比度——提对比会把暗部压死。
    # 亮度这一项是逐帧对照才发现的：参考片平均亮度 0.381，我们 0.516——**亮 13 个百分点**。
    # 加饱和只管颜色，压不下来"整屏发白"；参考片的暖来自"暗部压深、亮部收敛"，
    # 不是把颜色拧艳。所以 eq 里加 -0.055 的亮度、对比度抬到 1.10。
    # 逐帧对照逼出来的三刀（每一步都在整片上量过才落地）：
    #   亮度 0.516 → 0.466（-0.055）→ 0.387（gamma 0.92 + -0.05），参考片 0.381
    #   饱和度 0.436 → 0.500（1.55），参考片 0.511
    #   对比 1.10：参考片的"暖"来自暗部压深，不是把颜色拧艳
    GRADE = {"warm_high_sat": ",eq=saturation=1.50:contrast=1.10:gamma=0.85:"
                             "brightness=-0.10,"
                             "colortemperature=temperature=4900:mix=0.45"}

    def base_vf(hdr: bool) -> str:
        v = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={W}:{H},setsar=1")
        if hdr:
            v += (",colorspace=all=bt709:iall=bt2020:fast=1,"
                  "eq=contrast=1.14:gamma=1.05:saturation=1.08,"
                  "colortemperature=temperature=5200:mix=0.7")
        v += GRADE.get(str(style.get("grade") or ""), "")
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
        # 模板的调色在 HDR 转换之后、白平衡增益之前：它是"风格"，不是"校正"
        vf += GRADE.get(str(style.get("grade") or ""), "")
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
    font_index = int(sub.get("font_index") or 0)
    font = Path(sub["font_path"]) if sub.get("font_path") else find_font(sub["font"])
    if font:
        print(f"  字幕字体：{sub.get('font') or font.name}"
              f"（{font.name}#{font_index}）", file=sys.stderr)
    if not font:
        print("找不到样式包要求的字体，字幕不做", file=sys.stderr)
        cues = []
        pngs, info = [], {}
    else:
        # 包装模板自带 chars_soft；老样式包（talking-head-v1）没有这个键，
        # 就保持原来的 6/9——12 是那个包的**绝对上限**，不是断句目标。
        # 直接拿 12 当硬上限，实测字幕从 37 条掉到 30 条（密度 28→22.8 条/分）。
        if sub.get("chars_soft"):
            soft_c, hard_c = int(sub["chars_soft"]), int(sub["max_chars_per_line"])
        else:
            soft_c, hard_c = 6, 9
        cues = build_cues(segs, by_id, chars_cache, lib, hard_c, soft_c)
        band_top = max(0, int(sub["center_y_px"]) - 260)
        band_bot = min(H, int(sub["center_y_px"]) + 260)
        col = str(sub.get("color") or "#FFFFFF").lstrip("#")
        fill = tuple(int(col[i:i + 2], 16) for i in (0, 2, 4)) + (255,) if len(col) == 6 else (255,) * 4
        pngs, info = render_sub_pngs(cues, font, int(sub["size_px"]), W,
                                     (band_top, band_bot), work / "subs",
                                     # 5px 是老样式的默认值；包装模板会显式给
                                     # stroke_px（A 族 7、B 族 0——宋体一律投影）
                                     stroke=int(sub.get("stroke_px", 5)),
                                     shadow=bool(sub.get("shadow")),
                                     fill=fill, font_index=font_index)
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
    # 片头身份卡：从 intent/plan 里读，不从 EDL 的段上挂——
    # 它不是"某一段的补充说明"，它是**片头那十秒一直挂着的那一层**，
    # 挂到某一句话上就会出现"说完就撤"，而参考片是一口气挂十秒。
    _idc = (plan.get("id_card") if isinstance(plan, dict) else None) or {}
    if _idc.get("lines"):
        card_proj.append({
            "kind": "idcard", "lines": [str(x) for x in _idc["lines"]],
            "sizes": _idc.get("sizes"), "y": _idc.get("y"), "x": _idc.get("x"),
            "color": _idc.get("color"), "strong_lines": _idc.get("strong_lines"),
            "start": float(_idc.get("at") or 0.35),
            "end": float(_idc.get("at") or 0.35) + float(_idc.get("dur") or 9.0),
            "text": ""})
    for s in segs:
        c = s.get("card")
        if not c:
            continue
        d = float(c.get("dur") or (s["_dur_q"] - 0.6))
        d = max(1.2, min(d, s["_dur_q"] - 0.2))
        at = c.get("at")
        start = round(s["_start"] + (float(at) if isinstance(at, (int, float)) else 0.25), 3)
        kind = str(c.get("kind") or "card")
        if kind in ("keyword", "behind", "cutout") and style.get("emphasis"):
            hold = style["emphasis"]["hold_s"]
            d = max(float(hold[0]), min(d, float(hold[1])))
            start = round(s["_start"] + (float(at) if isinstance(at, (int, float)) else 0.6), 3)
            # 大字**什么时候出**不由我们拍板，由这句话**什么时候被说出来**决定。
            # `on` 写这句话里的一个词（几个字），这里去逐字时间戳里找它的起止：
            # 提前 0.12s 起（让字比话音早一点点"亮"起来，人耳才不觉得迟），
            # 停留时间取这个词说完为止，夹在模板给的 1.0–1.5s 之间。
            # 之前固定"段内 0.6s"出来的大字，对不上任何一个重音——这就是"时机不对"。
            on = str(c.get("on") or "").strip()
            aid = (s.get("audio") or {}).get("asset") or s["asset"]
            a_in = float((s.get("audio") or {}).get("in", s["in"]))
            if on:
                chars = chars_cache[aid] if aid in chars_cache else \
                    chars_cache.setdefault(aid, load_chars(lib, aid))
                win = [(ch, st, en) for ch, st, en in chars
                       if a_in - 0.05 <= st < a_in + s["_dur_q"] + 0.05]
                txt = "".join(ch for ch, _, _ in win)
                j = txt.find(on)
                if j >= 0:
                    st0 = win[j][1] - a_in + s["_start"] - 0.12
                    st1 = win[min(j + len(on) - 1, len(win) - 1)][2] - a_in + s["_start"]
                    start = round(max(s["_start"] + 0.15, st0), 3)
                    d = round(min(float(hold[1]), max(float(hold[0]), st1 - start + 0.25)), 3)
                else:
                    print(f"  ！大字对不上原话「{on}」（段 {s['n']}），沿用默认时机", file=sys.stderr)
        card_proj.append({"text": str(c.get("text") or ""), "start": start,
                          "end": round(start + d, 3), "kind": kind,
                          "spec": c.get("spec"), "boroll_kind": c.get("boroll_kind")})
    # B-roll 图形卡（色块章节卡 / 侧栏卡 / 对比条）：不画字，交给 gen_boroll.py 用代码画。
    # 这一类卡和"一句话的牌子"不是一回事：它承载的是一屏需要读的结构，
    # 所以停留时间按模板给的 3–5s，而且**整屏盖住画面**（y=0，全幅 PNG）。
    bor_gen = Path(__file__).resolve().parent / "gen_boroll.py"
    for c in [x for x in card_proj if x.get("kind") == "boroll"]:
        # spec 写相对路径时按**项目目录**解析：卡是项目的一部分，不是"当前目录的一部分"
        spec = Path(str(c.get("spec") or "")).expanduser()
        if not spec.is_absolute():
            spec = proj / spec
        if not spec.exists():
            print(f"  ！B-roll spec 不存在：{spec}", file=sys.stderr)
            continue
        png = work / "boroll" / f"{spec.stem}-{W}x{H}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        if not png.exists():
            _cardfont = (style.get("card") or {}).get("font_path") or None
            _cmd = [sys.executable, str(bor_gen), "--kind",
                    str(c.get("boroll_kind") or "grid"), "--spec", str(spec),
                    "--out", str(png), "--canvas", f"{W}x{H}"]
            if _cardfont:
                _cmd += ["--font", str(_cardfont)]
            r = subprocess.run(_cmd, capture_output=True, text=True)
            if r.returncode != 0 or not png.exists():
                print(f"  ！B-roll 生成失败：{(r.stdout + r.stderr)[-300:]}", file=sys.stderr)
                continue
        c["path"] = str(png)
        c["start"], c["end"] = c["start"], c["start"] + max(3.0, min(5.0, c["end"] - c["start"]))
    card_proj = [c for c in card_proj if c.get("kind") != "boroll" or c.get("path")]
    _cf = (style.get("card") or {}).get("font_path")
    cards, card_info = render_cards(segs, card_proj, font, W, work / "cards",
                                    style.get("emphasis"), H,
                                    Path(_cf) if _cf else None) if (font and card_proj) else ([], {})

    # 音效：按模板的 sfx 带上限来选点，不是每刀都给。
    # 参考片的教训（family doc 第三条铁律）：音效密度和切点密度必须解耦——
    # 95 个切点只有 13 个有瞬态也是成立的。这里只给**换介质**的切点和**大字/卡的出场**，
    # 给出的处数再按模板的 per-minute 上限截断，宁可少不可多。
    sfx_cues: list[dict] = []
    _starts, _t = [], 0.0
    for sh in shots:
        _starts.append(_t)
        _t = round(_t + sh["dur"], 4)
    if style.get("sfx") and shell_pack is None or (style.get("sfx") and shell_pack):
        sfx_dir = Path(os.environ.get("CUTSKILLS_HOME") or (Path.home() / ".cutskills")) / "assets" / "sfx"
        if sfx_dir.exists():
            prev_med = None
            for idx, sh in enumerate(shots):
                med = sh["s"].get("medium")
                if idx and med != prev_med and (sfx_dir / "whoosh.wav").exists():
                    # 增益按"能不能听见"定，不按"别太吵"定。
                    # 实测：-16 dB 的 whoosh 落在 -48 dB 的语音空档里能高 33 dB（很响），
                    # 可同样 -16 落在 -18 dB 的连续人声底下只高 3.5 dB（等于没打）。
                    # 素材本身峰值 -6、RMS -21，-14 那一档在 100ms 窗口里能量只涨 0.0 dB。
                    sfx_cues.append({"t": round(_starts[idx], 3), "name": "whoosh", "gain_db": -7})
                prev_med = med
            for c in card_proj:
                nm = "pop" if c.get("kind") in ("keyword", "behind", "cutout") else "tick"
                if (sfx_dir / f"{nm}.wav").exists():
                    sfx_cues.append({"t": round(c["start"], 3), "name": nm,
                                     "gain_db": -3 if nm == "pop" else -4})
            # 按模板给的上限截断（每个切点都打是最典型的"音效疲劳"）
            cap = float((style.get("sfx") or {}).get("density_per_min", [0, 8])[-1])
            limit = max(1, int(round(cap * total / 60)))
            if len(sfx_cues) > limit:
                keep = sorted(sfx_cues, key=lambda x: x["name"] != "pop")[:limit]
                sfx_cues = sorted(keep, key=lambda x: x["t"])
            print(f"  音效 {len(sfx_cues)} 处（模板上限 {cap}/分 → {limit} 处）")

    # 混音：音效轨和归一后的人声轨相加（mix_sfx 用 numpy 相加，不用 ffmpeg amix——
    # amix 会按输入数做归一，人声会被压下去）。
    final_audio = norm
    if sfx_cues:
        mixer = Path(__file__).resolve().parent / "mix_sfx.py"
        cues_p = work / "sfx_cues.json"
        cues_p.write_text(json.dumps(sfx_cues, ensure_ascii=False, indent=1), encoding="utf-8")
        mixed = work / "audio_sfx.wav"
        if mixer.exists():
            r = subprocess.run([sys.executable, str(mixer), "--audio", str(norm),
                                "--cues", str(cues_p), "--out", str(mixed)],
                               capture_output=True, text=True)
            if r.returncode == 0 and mixed.exists():
                # 混完再归一一次。
                # 混音前的轨已经被 loudnorm 顶到 -1 dBTP，往上面加任何东西都会顶天花板，
                # mix_sfx 的护栏只能把音效床整体压掉——压到听不见，等于白做（实测 9 处里有 6 处
                # 带内提升 0.0 dB）。正确顺序是「先混、再归一」：让响度归一器一次性去解决
                # 人声和音效的关系，而不是先定死人声、再逼音效让路。
                remaster = work / "audio_final.wav"
                rg = subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-i", str(mixed), "-af",
                     "loudnorm=I=-14:TP=-1:LRA=11", "-ar", "48000", "-ac", "2", str(remaster)],
                    capture_output=True, text=True)
                if rg.returncode == 0 and remaster.exists():
                    final_audio = remaster
                else:
                    final_audio = mixed
                tail = [x for x in (r.stdout or "").strip().splitlines() if x.strip()][-1:]
                print(f"  混音 {tail[0] if tail else '完成'}")
            else:
                print(f"  ！混音失败，保留无音效版：{(r.stderr or '')[-200:]}", file=sys.stderr)

    # BGM：全程铺底，人声一响就自动让路（sidechain duck）。
    # 参考片 122 秒同一首、不换曲、不做段落起落——它的作用是**填掉人声之间的空气**，
    # 不是"配乐"。所以我们给的增益 (-18 dB) 远低于"能听出旋律"的程度：
    # 一耳朵听见旋律，观众的注意力就从话上挪走了。
    if a.bgm:
        bgm_p = Path(a.bgm).expanduser()
        if not bgm_p.exists():
            print(f"  ！BGM 不存在：{bgm_p}", file=sys.stderr)
        else:
            bed = work / "audio_bgm.wav"
            # BGM 先归一到一个**绝对响度**（-24 LUFS），再混。
            # 直接给音量 dB 是错的：素材自己多响并不由我们决定，同一句 `volume=-18dB`
            # 换一首曲子结果完全不同。归一到 -24 之后，"和-14 的人声差 10 LU"是确定关系。
            nbg = work / "bgm_norm.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(bgm_p), "-af",
                            "loudnorm=I=-24:TP=-2:LRA=11", "-ar", "48000", "-ac", "2",
                            str(nbg)], capture_output=True)
            fc = ("[1:a]atrim=0:" + f"{total:.3f}" + ",asetpts=N/SR/TB[bg];"
                  "[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=10:"
                  "release=300:makeup=1[bgd];"
                  "[0:a][bgd]amix=inputs=2:duration=first:normalize=0[mix]")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(final_audio),
                 "-stream_loop", "-1", "-i", str(nbg),
                 "-filter_complex", fc, "-map", "[mix]",
                 "-ar", "48000", "-ac", "2", str(bed)], capture_output=True, text=True)
            if r.returncode == 0 and bed.exists():
                # 最后一道压缩 + 归一。
                # 参考片实测「底噪 -27.8 dB / 动态差 14.0 dB」——它的声音是被压过的：
                # 说话段和间隙只差 14 dB，而大多数人声原轨差 30 dB 以上。
                # 不压，人声一停画面就"掉下来"；压完这两个数才和参考片对上（实测 -27.5 / 14.0）。
                remaster2 = work / "audio_master.wav"
                rg = subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-i", str(bed), "-af",
                     "acompressor=threshold=0.06:ratio=3.5:attack=8:release=220:makeup=1.6,"
                     "loudnorm=I=-14:TP=-1:LRA=9", "-ar", "48000", "-ac", "2",
                     str(remaster2)], capture_output=True, text=True)
                final_audio = remaster2 if (rg.returncode == 0 and remaster2.exists()) else bed
                print(f"  BGM {bgm_p.name} 底 -24 LUFS，人声处 sidechain 让路，整轨压到 -14 LUFS")
            else:
                print(f"  ！BGM 混音失败：{(r.stderr or '')[-200:]}", file=sys.stderr)

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
                  "kind": c.get("kind") or "card", "bg": c.get("bg"),
                  "start": c["start"], "end": c["end"]} for c in cards]

    # 壳体（C 族）：顶条 / 角标 / 时间码。
    #
    # 条目从 EDL 的 beats 来，不是从模板里抄一串固定词——「常驻条」之所以有用，
    # 是因为它告诉观众"现在是第几章、还有几章"，抄一串没有含义的条目只是装饰。
    # 时间码逐镜重画：一张静止 PNG 里的数字不会自己走。
    if shell_pack:
        beats = edl.get("beats") or []
        titles = [str(b.get("title") or f"第{b['n']}章")[:8] for b in beats]
        beat_of_seg = {i: s.get("beat") for i, s in enumerate(segs, 1)}
        shell_shots, tt2 = [], 0.0
        for sh in shots:
            b = beat_of_seg.get(sh["seg"])
            shell_shots.append({"start": round(tt2, 4), "dur": sh["dur"],
                                "active": (int(b) - 1) if isinstance(b, int) and b else None})
            tt2 = round(tt2 + sh["dur"], 4)
        layers = pack_style.shell_layers(
            shell_pack, W, H,
            {"title": str(edl.get("brand") or ""), "entries": titles,
             "total": total, "shots": shell_shots},
            work / "shell")
        overlays += [{"path": l["path"], "y": l["y"], "start": l["start"], "end": l["end"],
                      "shell": True} for l in layers]
        print(f"  壳体 {shell_pack.get('name')}  层 {len(layers)}  条目 {len(titles)}")

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

    # 抠像层：`kind: behind`（大字从人物背后穿过）/ `kind: cutout`（抠像换底 + 大字）
    back_cards = [o for o in overlays if o.get("card") and o.get("kind") in ("behind", "cutout")]
    parts = []
    for idx, sh in enumerate(shots, 1):
        ss, sd = shot_start[idx - 1], sh["dur"]
        mine = [o for o in overlays
                if not o.get("card")
                and min(sd, o["end"] - ss) > max(0.0, o["start"] - ss) + 0.02]
        srcp = work / f"v{idx:03d}.mp4"

        # 这一镜头里有没有压着抠像卡
        bc = None
        for o in back_cards:
            wa, wb = max(0.0, o["start"] - ss), min(sd, o["end"] - ss)
            if wb > wa + 0.05:
                bc = dict(o, _wa=wa, _wb=wb)
                break

        if not mine and not bc:
            parts.append(srcp)
            continue

        op = work / f"ov{idx:03d}.mp4"
        inputs = ["-i", str(srcp)]
        n_sub = len(mine)
        for o in mine:
            lim = max(1.0, min(sd, o["end"] - ss) + 0.6)
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{lim:.2f}", "-i", o["path"]]

        fc, prev = [], "[0:v]"
        sub_base = 1                      # 字幕图层在 inputs 里的起始下标
        if bc:
            # 层级：底(画面) → 大字 → 人物(抠出来压在字上) → 字幕
            mask = person_masks(srcp, sd, FPS, work / f"mask{idx:03d}")
            if mask:
                inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{sd:.3f}",
                           "-i", bc["path"],          # 大字（静止图，所以 -loop 1）
                           "-i", str(mask)]           # 遮罩（视频，不能加 -loop）
                # 输入顺序：字幕 PNG 先加（1..n_sub），然后是这张大字卡、最后是遮罩。
                # 所以字幕的下标从 1 起，不能被"后面又加了两个输入"带偏——
                # 写错过一次，表现是 ffmpeg 报 "Invalid file index 4"（一共只有 4 个输入）。
                t_i, m_i = 1 + n_sub, 2 + n_sub
                sub_base = 1
                wa, wb = bc["_wa"], bc["_wb"]
                fc.append("[0:v]split=2[bb][pp]")
                fc.append(f"[bb][{t_i}:v]overlay=0:{bc['y']}[bt]")
                if bc["kind"] == "cutout":
                    # 换底：底色一层在最下面，大字压上去，人物再压上去。
                    # 底色用镜头自己四角的中位色会更好看，但这里先用模板给的纯色——
                    # 猜底色会把下一步的"底色该是什么"变成又一个隐参数。
                    col = str(bc.get("bg") or "#241a13").lstrip("#")
                    fc.append(f"color=c=0x{col}:s={W}x{H}:d={sd:.3f},format=yuv420p[cg]")
                    fc.append(f"[cg][bt]overlay=0:0[bg1]")
                else:
                    fc.append("[bt]null[bg1]")
                # alphamerge：第 0 路是画面，第 1 路是灰度遮罩 → 得到带 alpha 的人物。
                # 遮罩要先单独 `format=gray` 出一条链——写成 `[pp][{m}:v]format=gray,alphamerge`
                # 时 ffmpeg 会把两个输入都塞给 format 滤镜（"More input link labels than it has inputs"）。
                # 遮罩直接当 alpha 用会留一圈半透明的羽化边：人压在明黄大字上时，
                # 那圈边把背后的黄色透上来，看着像人物自己长了一圈白毛。
                # 所以先把遮罩二值化（阈值 140，略高于中间值 = 顺手把边往里收一点），
                # 换成硬边再合——1080p 下硬边看不出来，羽化边却一眼就看得见。
                fc.append(f"[{m_i}:v]format=gray,lut=y='if(gt(val,140),255,0)'[mk]")
                fc.append("[pp][mk]alphamerge[pers]")
                fc.append(f"[bg1][pers]overlay=0:0:enable='between(t,{wa:.3f},{wb:.3f})'[bout]")
                prev = "[bout]"
                print(f"  抠像 镜头 {idx:03d}  {ss + wa:5.1f}–{ss + wb:5.1f}s  {bc['kind']}")
            else:
                print(f"  ！抠像失败，退化成普通大字：镜头 {idx:03d}", file=sys.stderr)

        if mine:
            for n, o in enumerate(mine):
                wa = max(0.0, o["start"] - ss)
                wb = min(sd, o["end"] - ss)
                fc.append(f"{prev}[{sub_base + n}:v]overlay=0:{o['y']}:"
                          f"enable='between(t,{wa:.3f},{wb:.3f})'[v{n}]")
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
        if mine:
            print(f"  贴字 镜头 {idx:03d}  {ss:5.1f}s  层数 {len(mine)}")
        cur = op
        # 同一个镜头里可能同时挂着两张卡（片头身份卡 + 这一句的花字），
        # 文件名的编号必须**每贴一次就换**：原来固定写 oc{idx}.mp4，
        # 第二张卡的输入和输出是同一个路径，ffmpeg 直接报
        # "Output ... same as Input #0 - exiting"（退出码 234）。
        for _ci, o in enumerate([x for x in overlays
                                 if x.get("card") and x.get("kind") not in ("behind", "cutout")]):
            wa = max(0.0, o["start"] - ss)
            wb = min(sd, o["end"] - ss)
            if wb <= wa + 0.05:
                continue
            cp = work / f"oc{idx:03d}_{_ci:02d}.mp4"
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
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video_ov), "-i", str(final_audio),
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
        rep += ["", "## 字卡", ""] + [
        f"- {c['start']:.2f}–{c['end']:.2f}s　{c.get('text') or c.get('kind')}" for c in cards]
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
