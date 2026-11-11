#!/usr/bin/env python3
"""CutSkills · D 段验收：拿成片和风格包对账。

只验「能在成片上量出来的东西」：画幅帧率、时长、响度与真峰、字幕密度与单条字数、
口播镜头中位、介质变化间隔、黑帧。**验不了「好不好看」**——那要人看。

    verify_film.py --project <项目目录>

红黄绿三档，红=必须改，黄=人看一眼，绿=过。退出码 0 表示没有红。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

import yaml


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout or "{}")


def loudness(path: Path, target_i: float, target_tp: float) -> dict:
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", f"loudnorm=I={target_i}:TP={target_tp}:print_format=json",
             "-f", "null", "-"])
    txt = (r.stderr or "").split("{")
    try:
        return json.loads("{" + txt[-1])
    except (json.JSONDecodeError, IndexError):
        return {}


def black_runs(path: Path) -> list[float]:
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-vf", "blackdetect=d=0.10:pix_th=0.10", "-an", "-f", "null", "-"])
    out = []
    for line in (r.stderr or "").splitlines():
        if "black_start" in line:
            for part in line.split():
                if part.startswith("black_duration:"):
                    try:
                        out.append(float(part.split(":")[1]))
                    except ValueError:
                        pass
    return out


def _brief_plan(proj: Path) -> str:
    """时间轴没写 plan 时认 brief.json 的菜单选择（同一件事不许有两个答案）。"""
    b = proj / "brief.json"
    if not b.exists():
        return ""
    try:
        return str((json.loads(b.read_text()) or {}).get("plan") or "")
    except json.JSONDecodeError:
        return ""


def load_timeline_or_edl(proj: Path) -> dict:
    """返回 `{segments: [...], style_pack}` 这种统一形状。

    timeline.json（v4）的每一格带 kind；edl.json（v2）只有 clip。验收只关心
    「第几秒到第几秒是什么画面、说了什么」，所以这里把两种都摊成 segments，
    并且把 gen / silence 也放进去（它们占时间，会让后面的窗口整体位移）。
    """
    tp = proj / "timeline.json"
    if tp.exists():
        tl = json.loads(tp.read_text())
        segs, t = [], 0.0
        for el in tl.get("elements") or []:
            if el.get("track") == "overlay":
                # overlay **不占时间轴**：这里给它零长度。给成 dur 的话，
                # 后面所有段的窗口会整体后移 4 秒，字幕归属全错（实测 3 条误报）。
                segs.append({"n": el["n"], "kind": "overlay", "medium": "overlay",
                             "in": t, "out": t, "evidence": "", "why": el.get("why", ""),
                             "_overlay": True})
                continue
            d = (round(float(el["out"]) - float(el["in"]), 3) if el.get("kind") == "clip"
                 else float(el.get("dur") or 0))
            segs.append({"n": el["n"], "kind": el.get("kind"), "medium": el.get("medium") or el.get("kind"),
                         "asset": el.get("asset"), "in": float(el.get("in") or 0),
                         "out": float(el.get("in") or 0) + d, "dur": d,
                         "evidence": el.get("evidence", ""), "why": el.get("why", ""),
                         "audio": el.get("audio"), "card": el.get("card")})
            t = round(t + d, 3)
        return {"segments": segs, "beats": tl.get("beats") or [],
                "style_pack": tl.get("style_pack"), "duration_s": t, "_src": "timeline",
                # v5：骨架与包装的 id 要一路带下来，验收才知道该拿哪个 band 对账
                "plan": tl.get("plan") or _brief_plan(p),
                "pack": tl.get("pack"), "axis": tl.get("axis") or "A"}
    return {**json.loads((proj / "edl.json").read_text()), "_src": "edl"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--style", default=None)
    ap.add_argument("--film", default=None,
                    help="成片路径（默认 out/film.mp4；HyperFrames 链路是 out/film-hf.mp4）")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    # 真值源优先 timeline.json（v4）。读 edl.json 的话，片段窗口会和成片对不上——
    # 实测：换了 padding 之后成片 78.6s、edl 还写着 79.0s，字幕归属整片错位，
    # 报「10 条字幕文字对不上声音」，其实声音和字幕都对，是核对用的窗口是旧的。
    edl = load_timeline_or_edl(proj)
    # 包装以**实际渲这片时用的那套**为准。三个来源按可信度排：
    #   ① --style（命令行显式指定）
    #   ② proj/hf/meta.json 的 style —— 渲染那一步自己记下来的，最接近事实
    #   ③ timeline.pack / style_pack —— 时间轴声明的
    # 以前只有 ①③：`--style P3-dark` 渲完之后，验收照时间轴里的 P1-warm 去查，
    # 于是暗调片上三块大字被判「花字色占比 0.00%」（颜色在查错的那套里）。
    root = Path(__file__).resolve().parent.parent
    _meta_style = ""
    _mp = proj / "hf" / "meta.json"
    if _mp.exists():
        try:
            _meta_style = str((json.loads(_mp.read_text()) or {}).get("style") or "")
        except json.JSONDecodeError:
            _meta_style = ""
    if a.style:
        style_path = Path(a.style).expanduser()
        if style_path.suffix != ".yaml" and (root / "packs" / f"{a.style}.yaml").exists():
            style_path = root / "packs" / f"{a.style}.yaml"
    elif _meta_style and ((root / "packs" / f"{_meta_style}.yaml").exists()
                          or (root / "styles" / f"{_meta_style}.yaml").exists()):
        style_path = ((root / "packs" / f"{_meta_style}.yaml")
                      if (root / "packs" / f"{_meta_style}.yaml").exists()
                      else (root / "styles" / f"{_meta_style}.yaml"))
    elif edl.get("pack") and (root / "packs" / f"{edl['pack']}.yaml").exists():
        style_path = root / "packs" / f"{edl['pack']}.yaml"
    else:
        style_path = (root / "styles" / f"{edl.get('style_pack') or 'talking-head-v1'}.yaml")
    style = yaml.safe_load(style_path.read_text())
    # 包装模板（pack-*.yaml）不是渲染参数表：它的画幅写成 `canvas.aspect`（[9:16, 16:9]），
    # 因为同一族要跨两个方向用。验收这一步却需要**一个**确定画幅。
    # 所以碰到这种模板，画幅/帧率一律以成片自己的流信息为准，模板在这两项上不发言——
    # 硬塞一个 width 进去，等于让模板替片子决定它该是什么尺寸。
    # 只有**包装模板**才需要翻译：它们写的是 `canvas.aspect`（一族跨两个方向用）。
    # 风格包（talking-head-v1）本来就是最终参数表，再翻译一遍会把 size_px 80 变成 96——
    # 2026-11-09 就是这一步让验收算出「96px × 12 字 = 1152px 出画」，
    # 而渲染端老老实实按 80px 排，字幕其实是好的。两边读两套数，谁都不知道对方的存在。
    if "width" not in (style.get("canvas") or {}):
        # 走渲染时用的同一个翻译层，别让验收去读模板的原始字段：
        # 两边各读一套字段，迟早会出现"渲的时候 0.098H、验的时候 0.070H"这种对不上的事。
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import pack_style as _ps
        _c = style.get("canvas") or {}
        _W = int(_c.get("width") or 1080)
        _H = int(_c.get("height") or 1920)
        _f = _c.get("fps")
        _fps = int((_f[0] if isinstance(_f, (list, tuple)) else _f) or 30)
        style = _ps.to_style(style, _W, _H, _fps)
        # 模板允许两种帧率（[24, 30]）时，翻译层只会挑第一个。验收得知道"哪些都算对"，
        # 不然 30fps 的成片会被 24 判红——这不是片子的问题，是我们把选择权丢了。
        if isinstance(_f, (list, tuple)):
            style.setdefault("canvas", {})["fps"] = list(_f)
    film = (proj / a.film) if (a.film and not Path(a.film).is_absolute()) else (
        Path(a.film).expanduser().resolve() if a.film else proj / "out" / "film.mp4")
    if not a.film:
        cands = [x for x in (proj / "out" / "film.mp4", proj / "out" / "film-hf.mp4")
                 if x.exists()]
        if cands:
            film = max(cands, key=lambda x: x.stat().st_mtime)
    if not film.exists():
        print(f"没有成片：{film}（先跑 render_film.py）", file=sys.stderr)
        return 2
    info = probe(film)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    aud = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    dur = float(info.get("format", {}).get("duration") or 0)
    W, H = int(v.get("width") or 0), int(v.get("height") or 0)
    fps = v.get("avg_frame_rate") or "0/1"
    n, d = (fps.split("/") + ["1"])[:2]
    fps_v = float(n) / float(d or 1)

    rows: list[tuple[str, str, str, str]] = []

    def add(name: str, value: str, level: str, note: str = ""):
        rows.append((name, value, level, note))

    # 字体：分了几层、每一层是谁。
    # 这一条为什么要在验收里：位置和字高都能量，**字形味道量不出来**，
    # 于是"整片四个层共用一个标题美术字"可以一路绿灯到人眼前——
    # 2026-11-08 Jerry 就是在那一步停下来的（"字体简直不堪入目"）。
    # 能机器判的只有两条：① 字幕不许用标题美术字；② 字幕和花字不许同一个字体。
    _sub = style.get("subtitle") or {}
    _em = style.get("emphasis") or {}
    DISPLAY_ONLY = {"阿里妈妈数黑体 Bold", "阿里妈妈数黑体", "AlimamaShuHeiTi-Bold",
                    "方正兰亭大黑"}
    _subf = str(_sub.get("font") or "")
    _emf = str(_em.get("font") or _subf) if _em else ""
    _probs = []
    if _subf in DISPLAY_ONLY:
        _probs.append("字幕用了标题美术字（大字号有魂，小字号是脏点）")
    if _em and _emf and _emf == _subf:
        _probs.append("字幕与花字同一字体（四层共用一个是这次翻车的根因）")
    # 老样式包（talking-head-v1）的 emphasis 是一组语法配置，没有 font 字段。
    # 这种「没声明」不能判红——它可能渲染时就用了别的字重；但也不能判绿，
    # 因为没声明就意味着"落到字幕字体"是默认行为，而默认就是错的。
    _undeclared = bool(_em) and not _em.get("font") and not _probs
    add("字体分层", f"字幕 {_subf or '—'} · 花字 {_emf or '—'}",
        "red" if _probs else ("yellow" if _undeclared else "green"),
        "；".join(_probs) or ("花字字体未声明，默认会落到字幕字体——写一行 emphasis.font" if _undeclared else ""))

    # 画幅 / 帧率
    canvas = style["canvas"]
    if canvas.get("width"):
        ok = (W, H) == (int(canvas["width"]), int(canvas["height"]))
        add("画幅", f"{W}×{H}", "green" if ok else "red",
            "" if ok else f"应为 {canvas['width']}×{canvas['height']}")
    else:
        add("画幅", f"{W}×{H}", "green", "包装模板不限定画幅，以成片为准")
    fps_spec = canvas.get("fps")
    if isinstance(fps_spec, (list, tuple)):     # 模板写 [24, 30] = 两个方向都接受
        ok = any(abs(fps_v - float(x)) < 0.05 for x in fps_spec)
        add("帧率", f"{fps_v:.2f}", "green" if ok else "red",
            "" if ok else f"应为 {'/'.join(str(x) for x in fps_spec)}")
    elif fps_spec:
        ok = abs(fps_v - float(fps_spec)) < 0.05
        add("帧率", f"{fps_v:.2f}", "green" if ok else "red",
            "" if ok else f"应为 {fps_spec}")
    else:
        add("帧率", f"{fps_v:.2f}", "green", "包装模板不限定帧率，以成片为准")

    # 音轨
    ok = aud.get("codec_name") == "aac" and int(aud.get("channels") or 0) == 2
    add("音轨", f"{aud.get('codec_name')} {aud.get('channels')}ch "
              f"{aud.get('sample_rate')}Hz", "green" if ok else "yellow",
        "" if ok else "建议 AAC 双声道 48kHz")

    # 音画等长：视频流和声轨不等长，后面字幕和口型全错位——这种错在播放器里看不出来
    try:
        vdur = float(v.get("duration") or 0)
        adur = float(aud.get("duration") or 0)
    except (TypeError, ValueError):
        vdur = adur = 0.0
    if vdur and adur:
        diff = adur - vdur
        # 方向有讲究（2026-11-11 标定）：20 支参考片里 13 支**声轨比画面长**（最大 +0.155s，
        # 是编码器的尾部填充）；反过来「声轨短一截」才是事故——结尾静音、最后一个字被砍。
        ok = -0.05 <= diff <= 0.25
        add("音画等长", f"画面 {vdur:.2f}s / 声轨 {adur:.2f}s", "green" if ok else "red",
            "" if ok else (f"声轨短 {-diff:.2f}s：最后一个字被砍或结尾静音" if diff < 0
                           else f"声轨多出 {diff:.2f}s：尾巴拖了静音"))

    # 流起点差：两条流**起点**不同步时，整片口型错位，而播放器不会报错。
    # 只看起点，不看末点——末点不同只是尾部填充（参考片里 13/20 都长短不一）。
    try:
        vs0 = float(v.get("start_time") or 0)
        as0 = float(aud.get("start_time") or 0)
        tol = max(0.1, 2.0 / (fps_v or 30))
        add("流起点差", f"{abs(vs0 - as0) * 1000:.0f} ms",
            "green" if abs(vs0 - as0) <= tol else "red",
            "" if abs(vs0 - as0) <= tol else f"超过 {tol * 1000:.0f} ms：整片口型会偏")
    except (TypeError, ValueError):
        add("流起点差", "读不到", "yellow", "ffprobe 没给 start_time")

    # color tag：未标注的片子在手机上会被当另一个色彩空间解，看起来暗一半。
    # 20 支参考片 18 支有、2 支缺——所以这是我们**自家的规矩**，不是行业普遍事实，只判黄。
    _tags = [v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")]
    _ok = all(x == "bt709" for x in _tags)
    add("color tag", " / ".join(str(x) for x in _tags), "green" if _ok else "yellow",
        "" if _ok else "应为 bt709 ×3；缺标会让手机端智库发暗")

    # 叠加层到底烧进画面了没有：这一条是**看像素**，不是读 subs.json。
    # 2026-11-07 踩过：字幕/字卡的 JSON 都在，滤镜链也在命令里，但一张静止 PNG 的
    # enable 窗口开在 72 秒，overlay 一个像素都没画上去（要 -loop 1 才行）。
    # 只读 JSON 的验收会把这种事全判成绿灯。
    def frame_at(t: float, tag: str, mode: str = "L") -> "object":
        """取一帧。默认给灰度（判深浅），判颜色时要显式要 RGB。

        2026-11-08 踩过：这里一律转 L，于是「按颜色查大字在不在」永远查到 0%——
        灰度图转 RGB 出来是 (v,v,v)，任何彩色都比不上，红得很自信。
        """
        from PIL import Image
        out = proj / "work" / f"_verify_{tag}{'' if mode == 'L' else '-' + mode}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(film),
             "-frames:v", "1", str(out)])
        return Image.open(out).convert(mode) if out.exists() else None

    from PIL import Image  # noqa: F401

    subs0 = json.loads((proj / "out" / "subs.json").read_text()) \
        if (proj / "out" / "subs.json").exists() else {}
    cues0 = subs0.get("cues") or []
    band = subs0.get("band") or style["subtitle"].get("center_y_px")
    if cues0 and isinstance(band, list) and len(band) == 2:
        y0, y1 = int(band[0]), int(band[1])
        picks = [c for c in cues0 if c["end"] - c["start"] > 0.8][:40]
        picks = picks[:: max(1, len(picks) // 3)][:3] if picks else []
        fracs = []
        for i, c in enumerate(picks):
            im = frame_at((c["start"] + c["end"]) / 2, f"sub{i}")
            if im is None:
                continue
            px = list(im.crop((0, y0, W, y1)).getdata())
            fracs.append(sum(1 for v in px if v > 245) / max(1, len(px)))
        if fracs:
            lo = min(fracs)
            add("字幕烧进画面", f"字幕带白像素 {lo * 100:.1f}%–{max(fracs) * 100:.1f}%",
                "green" if lo > 0.008 else "red",
                "" if lo > 0.008 else "subs.json 有字幕，但画面上看不到——滤镜没生效")

    fin = load_timeline_or_edl(proj)
    cards_meta = {}
    if (proj / "out" / "cards.json").exists():
        try:
            for c in json.loads((proj / "out" / "cards.json").read_text()).get("cards") or []:
                cards_meta.setdefault(str(c.get("kind")), []).append(c)
        except json.JSONDecodeError:
            pass
    # 大字是什么颜色，由**当前这套包装**决定，不由判据自己决定。
    # 以前这里写死 emphasis.keyword_color（暖金黄 #F6EB34），于是拿暗调包装（青 #39D0C4）
    # 渲一遍，三块大字明明在画面上，判据全报「花字色占比 0.00%」——判据和包装耦合，
    # 换包必假红（2026-11-11 对抗式审查抓到）。
    _pal = (style.get("tokens") or {}).get("palette") or {}
    _kw_cands = []
    for _h in (_pal.get("accent"), (style.get("emphasis") or {}).get("keyword_color")):
        if _h and str(_h).upper() not in [x.upper() for x in _kw_cands]:
            _kw_cands.append(str(_h))
    if not _kw_cands:
        _kw_cands = ["#FFD400"]
    kw_hex = _kw_cands[0]
    _kw_list = _kw_cands

    def kw_share(im, box):
        """大字色占比：候选色里取最大。候选来自当前包装的 accent / emphasis 色。"""
        return max(color_share(im, h, box) for h in _kw_list)

    def color_share(im, hexs, box, tol=64):
        """一块区域里接近某个颜色的像素占比。

        为什么不再用「深色占比」：那判据只对 ffmpeg 那条链的深底衬卡成立。
        HyperFrames 链的大字是**亮黄字压画面**，深色占比天然为 0，
        于是"卡明明在画面上"也会报红。卡是我们自己设计的，颜色是已知的，
        就按颜色查——这比"深不深"诚实地多。
        """
        r0, g0, b0 = (int(hexs.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        crop = im.crop(box).convert("RGB")
        px = list(crop.getdata())
        n = sum(1 for r, g, b in px
                if abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol)
        return n / max(1, len(px))

    if cards_meta:
        for c in [x for v in cards_meta.values() for x in v]:
            a_, b_ = float(c["start"]), float(c["end"])
            if a_ >= dur:
                continue
            mid = min(dur - 0.05, (a_ + b_) / 2)
            im = frame_at(mid, "cardrgb", "RGB")
            if im is None:
                continue
            kind = str(c.get("kind") or "card")
            if kind in ("keyword", "behind", "cutout"):
                # 只在窗口中点取一帧会撞上入场动画的半路（擦显才到一半、字刚出来三分一），
                # 实测同一块字中点 0.02%、往后 0.1s 就 0.13%——一帧定生死会寃红。
                # 取窗口内的三帧取最大值：只要曾经完整上过屏，就算它上了。
                share = 0.0
                for frac in (0.35, 0.6, 0.85):
                    tt = min(dur - 0.05, a_ + (b_ - a_) * frac)
                    im_ = frame_at(tt, f"card{c.get('el')}{frac}", "RGB")
                    if im_ is None:
                        continue
                    share = max(share, kw_share(im_, (0, int(H * 0.28), W, int(H * 0.76))))
                if im_ is None:
                    continue
                # behind 卡是故意被人挡住的，可见墨迹天然远少于 keyword：
                # 实测同一块字，keyword 在 0.4% 上下，behind 只剩 0.13%（人挡住大半）。
                # 拿 keyword 的门槛要求 behind 只会永远报红；这里只判「字确实在画面上」，
                # 遮挡多少交给成片看，不假装能从一个数里量出来。
                floor = 0.0008 if kind in ("behind", "cutout") else 0.004
                label = "大字烧进画面（可见部分）" if floor < 0.004 else "大字烧进画面"
                add(label, f"{mid:.1f}s 处花字色占比 {share * 100:.2f}%",
                    "green" if share > floor else "red",
                    "" if share > floor else
                    f"大字「{c.get('text')}」在 {a_:.1f}–{b_:.1f}s，画面 {kw_hex} 色几乎为零")
            elif kind == "idcard":
                whites = color_share(im, "#FFFFFF", (0, 0, W, int(H * 0.42)), tol=40)
                add("身份卡烧进画面", f"{mid:.1f}s 处白字占比 {whites * 100:.2f}%",
                    "green" if whites > 0.002 else "red", "")
            else:
                px = list(im.convert("L").crop((0, 760, W, 1040)).getdata())
                dark = sum(1 for v_ in px if v_ < 80) / max(1, len(px))
                add("字卡烧进画面", f"{mid:.1f}s 处深色占比 {dark * 100:.1f}%",
                    "green" if dark > 0.12 else "red", "")
        cards_meta = {"_done": []}          # 下面按 EDL 的那段不再重复判

    t0 = 0.0
    for s in fin.get("segments") or []:
        d = round(float(s["out"]) - float(s["in"]), 3)
        card = s.get("card")
        if card and "_done" not in cards_meta:
            cd = float(card.get("dur") or max(1.2, d - 0.6))
            at = float(card["at"]) if isinstance(card.get("at"), (int, float)) else 0.25
            mid = t0 + at + max(0.3, cd / 2)
            im = frame_at(mid, "card")
            if im is not None:
                kind = str(card.get("kind") or "card")
                if kind in ("keyword", "behind", "cutout"):
                    share = kw_share(im, (0, int(H * 0.30), W, int(H * 0.74)))
                    floor = 0.0008 if kind in ("behind", "cutout") else 0.004
                    add("大字烧进画面", f"{mid:.1f}s 处花字色占比 {share * 100:.2f}%",
                        "green" if share > floor else "red",
                        "" if share > floor else
                        f"EDL 里有大字「{card.get('text')}」，画面 {kw_hex} 色几乎为零")
                else:
                    px = list(im.crop((0, 760, W, 1040)).getdata())
                    dark = sum(1 for v in px if v < 80) / max(1, len(px))
                    add("字卡烧进画面", f"{mid:.1f}s 处深色占比 {dark * 100:.1f}%",
                        "green" if dark > 0.12 else "red",
                        "" if dark > 0.12 else f"EDL 里有字卡「{card.get('text')}」，画面上没有")
        t0 += d

    # 响度
    L = style["audio"]
    m = loudness(film, L["loudness_lufs"], L["true_peak_dbtp"])
    try:
        got_i, got_tp = float(m.get("input_i")), float(m.get("input_tp"))
    except (TypeError, ValueError):
        got_i = got_tp = None
    if got_i is not None:
        dev = abs(got_i - L["loudness_lufs"])
        add("整体响度", f"{got_i:.1f} LUFS", "green" if dev <= 1.0 else
            ("yellow" if dev <= 2.0 else "red"), f"目标 {L['loudness_lufs']} ±1")
        ok = got_tp <= L["true_peak_dbtp"] + 0.3
        add("真峰", f"{got_tp:.1f} dBTP", "green" if ok else "red",
            f"上限 {L['true_peak_dbtp']}（超了必削波）")

    # 整屏代码画面那几秒有没有声音。
    # 这是本轮最容易翻车的一处：画面换成代码画的整屏之后，人声得靠单独一条 <audio> 接着播。
    # 光看 HTML 是对的、字幕也在走（字幕只读时间轴），成片里那几秒却可能是哑的——
    # 只有量成片自己的声轨才看得见。判据：窗口内有声（均值 > −45 dB）。
    # 整屏代码画面那几秒有没有声音。
    # 这是本轮最容易翻车的一处：画面换成代码画的整屏之后，人声得靠单独一条 <audio> 接着播。
    # 光看 HTML 是对的、字幕也在走（字幕只读时间轴），成片里那几秒却可能是哑的——
    # 只有量成片自己的声轨才看得见。判据：窗口内有声（均值 > −45 dB）。
    import re as _re
    # 位置要自己累加：`seg["in"]` 是**素材内**的时间（clip 的原片入点），不是成片时间。
    # 拿它当片内位置量过一遍，量到的是片头——判词会变成「代码画面量到的却是实拍亮度」，
    # 而片子其实是对的（2026-11-09 实测这一条差点把我带偏）。
    _t = 0.0
    for seg in edl.get("segments") or []:
        _d = float(seg.get("dur") or 0)
        if seg.get("kind") != "gen" or not _d:
            _t += _d
            continue
        g0, gd = round(_t, 3), _d
        r = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{g0 + 0.15:.3f}",
                 "-t", f"{max(0.3, gd - 0.3):.3f}", "-i", str(film),
                 "-af", "volumedetect", "-f", "null", "-"])
        txt = (r.stderr or "") + (r.stdout or "")
        mm = _re.search(r"mean_volume:\s*(-?[\d.]+) dB", txt)
        mv = float(mm.group(1)) if mm else None
        if mv is None:
            add(f"代码画面 #{seg.get('n')} 的声音", "量不出来", "yellow", "volumedetect 没返回")
        else:
            add(f"代码画面 #{seg.get('n')} 的声音", f"{g0:.1f}s 起 {gd:.1f}s，均值 {mv:.0f} dB",
                "green" if mv > -45 else "red",
                "" if mv > -45 else "整屏换了画面，人声没接上——这几秒是哑的")
        # 画面也得真的换掉了：代码画面是深底 + 品牌金，跟展厅/工地实拍差得远。
        im = frame_at(g0 + gd * 0.6, f"gen{seg.get('n')}", mode="L")
        if im is not None:
            px = list(im.getdata())
            dark = sum(1 for v in px if v < 46) / max(1, len(px))
            add(f"代码画面 #{seg.get('n')} 真的在画面上", f"深底占比 {dark * 100:.0f}%",
                "green" if dark > 0.35 else "red",
                "" if dark > 0.35 else "这一格该是代码画的整屏，量到的却是实拍亮度")
        # 这一格是「在演」还是「一张静图」——量成片自己，不信脚本的自我评价。
        # 为什么这条必须有：gen_scene --probe 只有画的时候跑，渲进片子里之后没人再看。
        # 而「0.5s 和 4s 一模一样」正是这套场景被打回的原病。
        if gd >= 2.5:
            import numpy as np
            sdir = proj / "work" / f"_gen{seg.get('n')}_seq"
            if sdir.exists():
                for f_ in sdir.glob("*.png"):
                    f_.unlink()
            sdir.mkdir(parents=True, exist_ok=True)
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{g0:.3f}", "-t", f"{gd:.3f}",
                 "-i", str(film), "-vf", "fps=6,scale=180:-1", str(sdir / "f%04d.png")])
            frames = sorted(sdir.glob("f*.png"))
            diffs = []
            prev = None
            for f_ in frames:
                arr = np.asarray(Image.open(f_).convert("L"), dtype=np.float32)
                if prev is not None:
                    diffs.append(float(np.abs(arr - prev).mean()))
                prev = arr
            if diffs:
                run_len = worst = 0
                for d_ in diffs:
                    if d_ < 0.6:
                        run_len += 1
                        worst = max(worst, run_len)
                    else:
                        run_len = 0
                still = worst / 6.0
                med = float(np.median(diffs))
                add(f"代码画面 #{seg.get('n')} 不是静图",
                    f"最长静止 {still:.2f}s · 帧差中位 {med:.2f}",
                    "green" if still < 2.0 else "red",
                    "" if still < 2.0 else "这一格有连续 2 秒一模一样——代码画的是图，不是画面")
        _t = round(_t + _d, 3)

    # 字幕
    subs = json.loads((proj / "out" / "subs.json").read_text()) if (proj / "out" / "subs.json").exists() else {}
    cues = subs.get("cues") or []
    if cues:
        per_min = len(cues) / (dur / 60) if dur else 0
        # 包装模板不声明密度（A 族 0–8.5 处/分这种跨度没有意义），所以这一项可以缺
        lo, hi = (style["subtitle"].get("density_per_min") or ["—", "—"])
        # 密度这个指标本身依赖语速：参考片 8 字/秒，45–58 条/分钟；这条片子 3.8 字/秒，
        # 同样拆成 8–9 字一条就只有 25 条/分钟。所以判的是**期望值对不对**，
        # 绝对数字照样写在说明里，别让"内容慢"背成"剪辑懒"的锅。
        avg = float(subs.get("avg_chars") or 0) or 9.0
        cps = float(subs.get("chars_per_s") or 0)
        derived = (cps / avg) * 60
        dev = abs(per_min - derived) / derived if derived else 1.0
        level = "green" if dev <= 0.15 else ("yellow" if dev <= 0.3 else "red")
        add("字幕密度", f"{per_min:.1f} 条/分钟", level,
            f"参考片 {lo}–{hi}（快语速）；本片语速 {cps:.1f} 字/秒、"
            f"单条 {avg:.1f} 字 → 期望 {derived:.0f} 条/分钟")
        longest = max(len(c["text"]) for c in cues)
        mx = int(style["subtitle"].get("max_chars_per_line") or 16)
        add("单条最长", f"{longest} 字", "green" if longest <= mx else "yellow",
            f"上限 {mx}（超了断句，别缩字号）")
        # 字数合规不等于塞得下：字幕宽度必须按像素算一次。
        # 80px × 14 字 = 1120 > 1080，两头真的被切掉了，而按字数的判据全绿。
        _sz = float(style["subtitle"].get("size_px") or 0)
        if _sz:
            widest = max(len(c["text"]) for c in cues) * _sz
            add("字幕最宽（像素）", f"{widest / W * 100:.0f}%W",
                "green" if widest <= 0.92 * W else "red",
                f"上限 92%W（{_sz:.0f}px × {longest} 字 = {widest:.0f}px / {W}px）"
                + ("" if widest <= 0.92 * W else "——超了会被画幅切掉两头"))
        gaps = [(cues[i + 1]["start"] - cues[i]["end"]) for i in range(len(cues) - 1)]
        dead = [g for g in gaps if g > 3.0]
        add("最长无字幕空档", f"{max(gaps):.1f}s" if gaps else "—",
            "green" if not dead else "yellow", "超过 3s 观众会觉得断")

        # 字幕文字必须出自它所在那一段的原声。
        # 2026-11-07 踩过：画面剪对了、声音剪对了、条数对了，字幕却把已经被剪掉的半句
        # 原样念出来（成片里没有「相当相当」，字幕上有）。四条 JSON 级检查全是绿。
        wins, acc = [], 0.0
        for s_ in edl["segments"]:
            d_ = s_["out"] - s_["in"]
            wins.append((acc, acc + d_, s_))
            acc += d_

        def _bare(t: str) -> str:
            return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t or "")

        strays = []
        by_n = {s_["n"]: s_ for _, _, s_ in wins}
        for c in cues:
            host = by_n.get(c.get("el")) if c.get("el") is not None else None
            if host is None:
                host = next((s_ for a_, b_, s_ in wins
                             if a_ - 0.05 <= c["start"] < b_), None)
            if not host:
                continue
            ev = _bare(host.get("evidence") or "")
            txt = _bare(c["text"])
            if ev and txt and txt not in ev:
                strays.append((c["text"], ev[:26]))
        add("字幕文字对得上声音", f"{len(cues) - len(strays)}/{len(cues)} 条",
            "green" if not strays else "red",
            "每条都出自本段原声" if not strays else
            "这些是已经被剪掉、字幕还留着的句子：" +
            " / ".join(f"「{t}」" for t, _ in strays[:3]))
        # 词表：品牌名/术语写错是最容易被观众一眼抓住的错误（「7灶」）。
        # 判据是字面扫描，不靠模型：词表里每一条「错写法」都不许出现在字幕和 evidence 里。
        try:
            import lexicon
            from make_edl import resolve_lib
            _lex = lexicon.load(resolve_lib(proj) / "_cutskills")
            _els = (edl.get("elements") or [])
            _bad = lexicon.violations([c["text"] for c in cues] +
                                      [e.get("evidence") or "" for e in _els], _lex)
        except Exception:
            _bad = []
        add("过词表（品牌名/术语）", f"{len(_bad)} 处",
            "green" if not _bad else "red",
            "字幕与 evidence 里没有词表认定的错写法" if not _bad else
            "ASR 写错的词漏到画面上了：" +
            " / ".join(f"「{b}」→「{_lex['terms'][b]}」" for b, _ in _bad[:3]))
    else:
        _ss = (style.get("subtitle_system") or {})
        _ssid = (_ss.get("id") if isinstance(_ss, dict) else str(_ss)) or "single-line"
        if _ssid == "none":
            add("字幕", "不适用", "yellow",
                "这套包装没有字幕层（大字即字幕）—— 要看字有没有上屏，看下面的大字判据")
        else:
            add("字幕", "0 条", "red", "没有字幕")

    # 节奏：优先按成片里真实的镜头清单判（推镜切之后和 EDL 不一样）
    shots_path = proj / "out" / "shots.json"
    if shots_path.exists():
        units = json.loads(shots_path.read_text())
    else:
        units = [{"dur": s["out"] - s["in"], "medium": s.get("medium")} for s in edl["segments"]]
    talk = [u["dur"] for u in units if u.get("medium") == "talking"]
    if talk:
        med = statistics.median(talk)
        tgt = float((style.get("shots") or {}).get("talking_median_s")
                    or ((style.get("tempo") or {}).get("shot_median_s") or [2.4])[0])
        level = "green" if abs(med - tgt) <= 0.8 else ("yellow" if abs(med - tgt) <= 2.0 else "red")
        add("口播镜头中位", f"{med:.2f}s", level, f"目标 {tgt}s")

    # 介质变化
    win = float((style.get("shots") or {}).get("medium_change_within_s") or 40.0)
    last, worst = 0.0, 0.0
    t = 0.0
    prev = None
    for u in units:
        if prev and u.get("medium") != prev:
            worst = max(worst, t - last)
            last = t
        prev = u.get("medium")
        t += u["dur"]
    worst = max(worst, t - last)
    add("最长同介质连续", f"{worst:.1f}s", "green" if worst <= win else "yellow",
        f"目标 {win:.0f}s 内换一次")

    # 节奏落档：画面变化中位与 CV 必须落在所选骨架的 band 内（references/rhythm-bands.md）。
    # 为什么不用「口播镜头中位」代替：那量的是素材里的机位切换，这一格量的是**观众看到的换画面**。
    # 我们 naming-01 两个数是 4.86s 和 1.27s——差四倍，因为它们根本不是一件事。
    plan_id = str(edl.get("plan") or "").strip()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import skeleton as _sk
        _pf = Path(__file__).resolve().parent.parent / "plans" / f"{plan_id}.yaml"
        _spec = yaml.safe_load(_pf.read_text()) if _pf.exists() else {}
        _band = ((_spec.get("tempo") or {}).get("band") or [None, None])
        _cvmin = float((_spec.get("tempo") or {}).get("cv_min") or 0.42)
        _ci = _sk.probe(film)
        _cuts = _sk.detect_cuts(film, _ci)
        _st, _lens = _sk.cut_stats(_cuts, _ci["duration_s"])
        _lens = [x for x in _lens if x > 0.05]
        _med = statistics.median(_lens) if _lens else 0.0
        _cv = (statistics.pstdev(_lens) / statistics.mean(_lens)) if len(_lens) > 1 else 0.0
        _okband = bool(_band[0]) and (_band[0] - 1e-6 <= _med <= _band[1] + 1e-6)
        _near = bool(_band[0]) and (abs(_med - (_band[0] + _band[1]) / 2) <= 0.5)
        _lvl = "green" if _okband else ("yellow" if _near else "yellow")
        _note = "" if _okband else (
            "画面换得比这一档还快：减卡或减动效" if (_band[0] and _med < _band[0])
            else "画面换得比这一档还慢：补微事件或加换介质")
        add("节奏落档", f"中位 {_med:.2f}s · CV {_cv:.2f}（{plan_id or '未指定骨架'} "
                        f"{_band[0]}–{_band[1]}s）", _lvl,
            (_note + "；" if _note else "") + ("" if _cv >= _cvmin else f"CV < {_cvmin}：节奏太匀"))
    except Exception as e:
        add("节奏落档", "量不出", "yellow", str(e)[:60])

    # 黑帧
    bl = black_runs(film)
    add("黑帧", f"{len(bl)} 段", "green" if not bl else "yellow",
        "" if not bl else "黑帧可能是刻意留白，也可能是渲染断了")

    # 冻结（≥2s 画面完全不动）——只算**口播段**。
    # 标定依据：20 支参考片里 7 支有 ≥2s 冻结（照片/空镜持有），全片判死会误杀三分之一的行业成品；
    # 反过来口播画面冻住 2 秒 = 卡死或渲坏了，那是真事故。
    _tw, _t = [], 0.0
    for u in units:
        if u.get("medium") == "talking":
            _tw.append((_t, _t + float(u["dur"])))
        _t += float(u["dur"])
    _fd = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(film),
               "-vf", "freezedetect=n=0.003:d=2", "-an", "-f", "null", "-"])
    _fs = [float(x) for x in re.findall(r"freeze_start:\s*([0-9.]+)", _fd.stderr or "")]
    _fe = [float(x) for x in re.findall(r"freeze_end:\s*([0-9.]+)", _fd.stderr or "")]
    _freeze = list(zip(_fs, _fe + [dur] * (len(_fs) - len(_fe))))
    _bad = [(s, e) for s, e in _freeze
            if any(a0 <= (s + e) / 2 <= a1 for a0, a1 in _tw)]
    add("冻结（口播段）", f"{len(_bad)} 处" + (f" · 最久 {max(e - s for s, e in _bad):.1f}s" if _bad else ""),
        "yellow" if _bad else "green",
        ("口播画面冻住，最早 " + f"{_bad[0][0]:.1f}s" ) if _bad else "空镜/静帧/片尾不计")

    # 最暗 1 秒：全片偏暗或某段全黑，手机上看着像“没渲染”。
    # 标定依据：20 支参考片平均亮度 p25 0.30 / p50 0.39，最暗 1s 最低可以到 0.001——
    # 所以不拿“亮度区间”当门，只拿“平均值太暗”和“连续 1 秒几乎全黑”两个诊断。
    try:
        import numpy as np
        lw = 320
        lh = int(round(lw * H / max(1, W) / 2)) * 2
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(film),
                              "-vf", f"fps=1,scale={lw}:{lh}", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True).stdout
        npx = lw * lh
        if raw and npx and len(raw) >= npx:
            a1 = np.frombuffer(raw[:len(raw) // npx * npx], dtype=np.uint8).reshape(-1, npx)
            sec = a1.mean(axis=1) / 255.0
            dark_at = int(sec.argmin())
            lvl = "yellow" if (sec.mean() < 0.10 or sec.min() < 0.02) else "green"
            add("亮度", f"均 {sec.mean():.2f} · 最暗 1s {sec.min():.2f}（{dark_at}s）", lvl,
                "" if lvl == "green" else "接近全黑的段落会被平台当低质，也看不出画了什么")
    except Exception as e:                      # 量不出来就说出来，不静默跳过
        add("亮度", "量不出", "yellow", str(e)[:60])

    red = [r for r in rows if r[2] == "red"]
    yellow = [r for r in rows if r[2] == "yellow"]
    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = [f"# 验收报告 · {proj.name}", "",
             f"- 成片：`out/{film.name}` · {dur:.2f}s · {W}×{H} @ {fps_v:.2f}fps · "
             f"{int(info.get('format', {}).get('size') or 0)/1e6:.1f} MB",
             f"- 风格包：`{style['name']}`",
             f"- 结果：🟢 {len(rows)-len(red)-len(yellow)} · 🟡 {len(yellow)} · 🔴 {len(red)}", "",
             "| 指标 | 实测 | 判定 | 说明 |", "|---|---|---|---|"]
    for name, value, level, note in rows:
        lines.append(f"| {name} | {value} | {icon[level]} | {note} |")
    lines += ["", "> 这份报告只覆盖能量出来的东西。节奏是不是顺、话说清没清，得人看一遍。"]
    (proj / "verify-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"验收：🟢 {len(rows)-len(red)-len(yellow)} · 🟡 {len(yellow)} · 🔴 {len(red)}")
    for name, value, level, note in red + yellow:
        print(f"  {icon[level]} {name} {value}  {note}")
    print(f"→ {proj / 'verify-report.md'}")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
