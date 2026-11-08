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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--style", default=None)
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    edl = json.loads((proj / "edl.json").read_text())
    if a.style:
        style_path = Path(a.style).expanduser()
    else:
        style_path = (Path(__file__).resolve().parent.parent / "styles"
                      / f"{edl.get('style_pack') or 'talking-head-v1'}.yaml")
    style = yaml.safe_load(style_path.read_text())
    # 包装模板（pack-*.yaml）不是渲染参数表：它的画幅写成 `canvas.aspect`（[9:16, 16:9]），
    # 因为同一族要跨两个方向用。验收这一步却需要**一个**确定画幅。
    # 所以碰到这种模板，画幅/帧率一律以成片自己的流信息为准，模板在这两项上不发言——
    # 硬塞一个 width 进去，等于让模板替片子决定它该是什么尺寸。
    if "subtitle" in style and "stem" not in (style.get("subtitle") or {}):
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
    film = proj / "out" / "film.mp4"
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
        diff = abs(vdur - adur)
        add("音画等长", f"{vdur:.2f}s / {adur:.2f}s", "green" if diff <= 0.2 else "red",
            "" if diff <= 0.2 else f"差 {diff:.2f}s（画面多了或声音少了）")

    # 叠加层到底烧进画面了没有：这一条是**看像素**，不是读 subs.json。
    # 2026-11-07 踩过：字幕/字卡的 JSON 都在，滤镜链也在命令里，但一张静止 PNG 的
    # enable 窗口开在 72 秒，overlay 一个像素都没画上去（要 -loop 1 才行）。
    # 只读 JSON 的验收会把这种事全判成绿灯。
    def frame_at(t: float, tag: str) -> "object":
        from PIL import Image
        out = proj / "work" / f"_verify_{tag}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(film),
             "-frames:v", "1", str(out)])
        return Image.open(out).convert("L") if out.exists() else None

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

    fin = json.loads((proj / "edl.json").read_text())
    t0 = 0.0
    for s in fin.get("segments") or []:
        d = round(float(s["out"]) - float(s["in"]), 3)
        card = s.get("card")
        if card:
            cd = float(card.get("dur") or max(1.2, d - 0.6))
            at = float(card["at"]) if isinstance(card.get("at"), (int, float)) else 0.25
            mid = t0 + at + max(0.3, cd / 2)
            im = frame_at(mid, "card")
            if im is not None:
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
        for c in cues:
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

    # 黑帧
    bl = black_runs(film)
    add("黑帧", f"{len(bl)} 段", "green" if not bl else "yellow",
        "" if not bl else "黑帧可能是刻意留白，也可能是渲染断了")

    red = [r for r in rows if r[2] == "red"]
    yellow = [r for r in rows if r[2] == "yellow"]
    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = [f"# 验收报告 · {proj.name}", "",
             f"- 成片：`out/film.mp4` · {dur:.2f}s · {W}×{H} @ {fps_v:.2f}fps · "
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
