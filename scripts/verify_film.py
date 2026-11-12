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


def dark_ratio(im) -> float:
    """深底占比：像素 < 46（L 模式）的比例。供「代码画面真的在画面上」用，
    也供 selftest 的负样本调用——实拍帧必须量出低值，不然判据就是假的。"""
    px = list(im.getdata())
    return sum(1 for v in px if v < 46) / max(1, len(px))


def median_of(vals: list[float]):
    return sorted(vals)[len(vals) // 2] if vals else None


def pip_applied(meta: dict) -> bool:
    """meta 里的 pips_applied 只有渲片贴回成功才是 true。

    缺键（旧 meta / 渲染中途崩）必须按 false 算——曾经缺省 True，让
    「raw 成片冒充 + 删键」拿到 24/0/0（T-005 二次复核抓过）。
    """
    return bool(meta.get("pips_applied", False))


def pip_verdict(want: list, pips: list, applied: bool) -> tuple[str, str, str] | None:
    """PiP 的声明层 / 管线层 / 数量层判定；像素层留在调用方（要抽帧）。

    返回 (级别, 读数, 说明)；None = 前两层都过，交给像素层继续判。
    T-012：verify 与测试共用这一份判定，避免「测试自己写一套」。
    """
    n_want, n_pip = len(want), len(pips)
    if n_want and not n_pip:
        return ("red", "timeline 有 B 轴格，meta 里没有 pips",
                "渲片时 PiP 没建出来（缺 person_mask 或音频素材），或渲染器版本没带这功能")
    if n_pip and not applied:
        if n_want:
            return ("red", f"meta 声明 {n_pip} 格但贴回失败（pips_applied=false）",
                    "渲片日志里有「PiP 没贴上」——成片里没有小窗，声明不作数")
        return ("yellow", "贴回失败但 timeline 不要求 PiP",
                "meta 声明了 pips 但 pips_applied=false；timeline 没有 B 轴格所以不算硬伤，查渲片日志")
    if n_want and n_pip < n_want:
        return ("red", f"想要 {n_want} 格，meta 只有 {n_pip} 格",
                "有 B 轴格没建出小窗——查渲片日志里被跳过的那几格")
    return None


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout or "{}")


def loudness(path: Path, target_i: float, target_tp: float) -> dict:
    """量成片的整体响度 / 真峰。

    **必须切到最后一块 { … } 之间。** 以前是 `split("{")[-1]`，ffmpeg 打完 JSON 还会
    再打一行 `frame= … Lsize= …`，json.loads 于是抛错、返回 {}，而调用方看到
    `input_i` 缺失就默默跳过——“响度 −14 LUFS 真峰 −1 dBTP” 这条硬指标长期没人判
    （2026-11-11 欧阳 a/b 两条片 −15.46 LUFS / −0.54 dBTP 就这样绿着出了片）。
    """
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", f"loudnorm=I={target_i}:TP={target_tp}:print_format=json",
             "-f", "null", "-"])
    txt = r.stderr or ""
    try:
        return json.loads(txt[txt.rindex("{"):txt.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
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
                # 形参叫 proj，这里以前写成 p —— 空 plan 的时间轴一进来就 NameError。
                # 之前没炸只是因为样片恰好写了 plan，短路求值把这条残肢藏住了。
                "plan": tl.get("plan") or _brief_plan(proj),
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
    # 这套包装到底有没有字幕层？没有的话，所有「字幕有没有上屏」类判据全部跳过——
    # 拿「无字幕层」的片子去判字幕，只会判出假红（实测 P3-dark 两条）。
    _ssx = style.get("subtitle_system") or {}
    _ssx_id = (_ssx.get("id") if isinstance(_ssx, dict) else str(_ssx)) or "single-line"
    _has_subs = _ssx_id != "none"
    band = subs0.get("band") or style["subtitle"].get("center_y_px")
    if cues0 and _has_subs and isinstance(band, list) and len(band) == 2:
        y0, y1 = int(band[0]), int(band[1])
        picks = [c for c in cues0 if c["end"] - c["start"] > 0.8][:40]
        picks = picks[:: max(1, len(picks) // 3)][:3] if picks else []
        fracs = []
        for i, c in enumerate(picks):
            im = frame_at((c["start"] + c["end"]) / 2, f"sub{i}")
            if im is None:
                continue
            # 字幕是什么颜色，由**这套包装**决定：暖实拍是白字，纸感是深墨字（#1F1B16）。
            # 以前这里只会数白像素，纸感片上字幕明明在，却报「字幕带白像素 0.0%」——
            # 和 A4 那类错同一个病：判据把自己绑在某套包装的颜色上。
            _chex = str((style.get("subtitle") or {}).get("color") or "#FFFFFF")
            _cr, _cg, _cb = (int(_chex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            _light = (_cr + _cg + _cb) / 3 > 128
            cim = im.convert("RGB").crop((0, y0, W, y1))
            px = list(cim.getdata())
            if _light:
                hit = sum(1 for r, g, b in px if r > 200 and g > 200 and b > 200)
            else:
                hit = sum(1 for r, g, b in px
                          if abs(r - _cr) <= 40 and abs(g - _cg) <= 40 and abs(b - _cb) <= 40)
            fracs.append(hit / max(1, len(px)))
        if fracs:
            lo = min(fracs)
            add("字幕烧进画面",
                f"字幕带同色像素 {lo * 100:.1f}%–{max(fracs) * 100:.1f}%",
                "green" if lo > 0.008 else "red",
                "" if lo > 0.008 else
                f"subs.json 有字幕，但画面上找不到字幕色 {_chex}——滤镜或颜色没生效")

    # 双语第二行到底上没上屏 —— 必须去成片像素上找。
    # 以前整个验收脚本对「第二行」零防线：项目没英文稿、或英文被推出画面，
    # 照样 🟢30。而这件事刚刚真的出过一次（英文行压根没渲出来，见 fixes 补记）。
    if _has_subs and _ssx_id == "bilingual":
        _enp = proj / "subs-en.json"
        _enmap = {}
        if _enp.exists():
            try:
                _enmap = json.loads(_enp.read_text()) or {}
            except json.JSONDecodeError:
                _enmap = {}
        _sub_st = style.get("subtitle") or {}
        _cap_px = int(_sub_st.get("size_px") or 72)
        _top = int(float(_sub_st.get("center_y_px") or H * 0.674))
        _scan0, _scan1 = _top - 10, min(H - 2, int(_top + _cap_px * 2.4))
        # 代码画的整屏格本身就满是高对比图形（实测那两帧的行剖面跨度顶到 181px），
        # 拿它量字幕行高等于量背景。只取「这一段没有 gen 格压着」的时刻。
        # 注意：segments 里的 in/out 是**素材内的时间**，不是成片时间。
        # 代码画面要按「前面所有格子加起来」折成成片位置，否则过滤等于没过（踩过一次）。
        _genspans, _acc = [], 0.0
        try:
            for s_ in (load_timeline_or_edl(proj).get("segments") or []):
                if s_.get("_overlay"):
                    continue
                _d = (round(float(s_["out"]) - float(s_["in"]), 3)
                      if s_.get("kind") == "clip" else float(s_.get("dur") or 0))
                if s_.get("kind") == "gen":
                    _genspans.append((_acc, _acc + _d))
                _acc = round(_acc + _d, 3)
        except Exception:
            _genspans = []

        def _ink_span(im):
            """字幕块的行跨度（像素）。双语两行会把块撑高，单行撑不起来——
            这是唯一不靠猜颜色、又不受「中文字尾溢进下一行」影响的量。"""
            import numpy as _np
            arr = _np.asarray(im.convert("L").crop((180, _scan0, W - 180, _scan1))
                              ).astype("float32") / 255.0
            med = float(_np.median(arr))
            prof = (_np.abs(arr - med) > 0.20).mean(axis=1)
            peak = float(prof.max())
            if peak < 0.25:      # 这一帧根本看不到字幕
                return 0, peak
            rows = [i for i, v in enumerate(prof) if v >= peak * 0.10]
            span = int(rows[-1] - rows[0]) if rows else 0
            # 跨度顶到扫描窗两头 = 这一帧整个区域都是高对比（人物、地面、代码画面），
            # 量到的不是字幕。这种帧直接丢掉，别让它冒充「两行高」。
            if span >= 0.95 * (len(prof) - 1):
                return 0, peak
            return span, peak

        if not _enmap:
            add("双语第二行", "项目里没有 subs-en.json", "red",
                "这套包装声明双语双行，但没有英文稿——渲出来只有一行中文。"
                "要不补 subs-en.json（键=字幕序号），要不把这套包换掉")
        elif cues0:
            _cand = [c for c in cues0 if c["end"] - c["start"] > 0.8
                     and not any(c["start"] < b_ and c["end"] > a_ for a_, b_ in _genspans)]
            _cand = _cand[:40]
            _pick = _cand[:: max(1, len(_cand) // 4)][:4] if _cand else []
            _need = int(_cap_px * 1.55)      # 单行 ~0.9×cap；双语 ~1.6×cap
            _hits, _spans = 0, []
            for i, c in enumerate(_pick):
                im = frame_at((c["start"] + c["end"]) / 2, f"en{i}")
                if im is None:
                    continue
                sp, pk = _ink_span(im)
                if sp == 0:
                    continue
                _spans.append(sp)
                if sp >= _need:
                    _hits += 1
            if _spans:
                _ok = _hits >= max(2, len(_spans) - 1)
                add("双语第二行", f"{_hits}/{len(_spans)} 帧字幕块高 {min(_spans)}–{max(_spans)}px"
                    f"（门槛 {_need}px）", "green" if _ok else "red",
                    "英文行真的在画面上，把字幕块撑到了两行高" if _ok else
                    "声明双语双行，但字幕块只有一行高——英文行没上屏、或被遮住了")

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
            elif kind in ("stat", "steps", "versus", "term"):
                # MG 卡：底色是半透明 ground，accent 只出现在数字/标题/序号上，面积天然很小。
                # 量「accent 一丁点都没有」这件事就够了——它零，说明这一格根本没画上去。
                share = 0.0
                for frac in (0.4, 0.65, 0.85):
                    tt = min(dur - 0.05, a_ + (b_ - a_) * frac)
                    im_ = frame_at(tt, f"mg{c.get('el')}{frac}", "RGB")
                    if im_ is None:
                        continue
                    _box = (0, int(H * 0.19), W, int(H * 0.62))
                    if kind == "term":
                        share = max(share, color_share(im_, "#FFFFFF", _box, tol=46))
                    else:
                        share = max(share, kw_share(im_, _box))
                # term 卡的特征不是 accent：词和释义都是白字，accent 只有释义左边一条 4px 细线，
                # 拿 accent 占比判它必然报红（2026-11-12 实测 0.02%）。这是判据选错了特征，
                # 不是卡没上去——所以 term 改判白字占比，其余三种照旧判 accent。
                if kind == "term":
                    add(f"MG 卡烧进画面（{kind}）",
                        f"{mid:.1f}s 处白字占比 {share * 100:.2f}%",
                        "green" if share > 0.0012 else "red",
                        "" if share > 0.0012 else
                        f"term 卡在 {a_:.1f}–{b_:.1f}s，画面里找不到卡片文字——卡没上去")
                else:
                    add(f"MG 卡烧进画面（{kind}）",
                        f"{mid:.1f}s 处 accent 占比 {share * 100:.2f}%",
                        "green" if share > 0.0008 else "red",
                        "" if share > 0.0008 else
                        f"{kind} 卡在 {a_:.1f}–{b_:.1f}s，画面里找不到包装 accent 色——卡没上去")
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
    else:
        # 量不到就是量不到，不许当成「没问题」：这条判据曾经因为解析 bug 静默消失过。
        add("整体响度", "量不到", "red", "loudnorm 没吐出可解析的 JSON——先查 ffmpeg 链路，别当通过")

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
        # 单帧采样太脆：换幕扫光的亮带扫过时（宽 14%、alpha 70），背景橄榄底色
        # 本来就贴在阈值上，一帧就能从 48% 掉到 26%（2026-11-11 T-005 实测）。
        # 改成三采样取中位：扫光只占一格里的一个时刻，实拍误投则三帧全低。
        _darks = []
        for _fr in (0.25, 0.5, 0.75):
            _im = frame_at(g0 + gd * _fr, f"gen{seg.get('n')}_{_fr}", mode="L")
            if _im is not None:
                _darks.append(dark_ratio(_im))
        dark = median_of(_darks)
        if dark is not None:
            add(f"代码画面 #{seg.get('n')} 真的在画面上",
                f"深底占比 {dark * 100:.0f}%（三帧中位）",
                "green" if dark > 0.35 else "red",
                "" if dark > 0.35 else "这一格该是代码画的整屏，量到的却是实拍亮度")
        else:
            add(f"代码画面 #{seg.get('n')} 真的在画面上", "取帧失败", "yellow",
                "ffmpeg 没抽出帧，这条没量到")
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
    if cues and _has_subs:
        per_min = len(cues) / (dur / 60) if dur else 0
        # 包装模板不声明密度（A 族 0–8.5 处/分这种跨度没有意义），所以这一项可以缺
        lo, hi = (style["subtitle"].get("density_per_min") or ["—", "—"])
        # 密度这个指标本身依赖语速：参考片 8 字/秒，45–58 条/分钟；这条片子 3.8 字/秒，
        # 同样拆成 8–9 字一条就只有 25 条/分钟。所以判的是**期望值对不对**，
        # 绝对数字照样写在说明里，别让"内容慢"背成"剪辑懒"的锅。
        avg = float(subs.get("avg_chars") or 0) or 9.0
        cps = float(subs.get("chars_per_s") or 0)
        if not cps:   # subs.json 被别的包装写过（例如无字幕层那套）→ 就地按 cue 重算
            _cs = sum(len(c.get("text") or "") for c in cues)
            _ss = sum(float(c["end"]) - float(c["start"]) for c in cues)
            cps = round(_cs / _ss, 2) if _ss else 0.0
            avg = round(_cs / len(cues), 2) if _cs else avg
        derived = (cps / avg) * 60
        dev = abs(per_min - derived) / derived if derived else 1.0
        level = "green" if dev <= 0.15 else ("yellow" if dev <= 0.3 else "red")
        add("字幕密度", f"{per_min:.1f} 条/分钟", level,
            f"参考片 {lo}–{hi}（快语速）；本片语速 {cps:.1f} 字/秒、"
            f"单条 {avg:.1f} 字 → 期望 {derived:.0f} 条/分钟")
        longest = max((c.get("units") or len(c["text"])) for c in cues)
        mx = int(style["subtitle"].get("max_chars_per_line") or 16)
        # 按「展示单位」判，不按字数：半角字（OK、2026）只占 0.34 个单位，
        # 「捉翻嘅鸡绝对品质 OK 嘅」13 字实际 9.7 个单位，按字数算会误报。
        add("单条最长", f"{longest:.0f} 单位",
            "green" if longest <= mx + 0.01 else "yellow",
            f"上限 {mx}（超了断句，别缩字号）")
        # 字数合规不等于塞得下：字幕宽度必须按像素算一次。
        # 但**不能拿字数 × 字号**：半角字符（「OK」「2026」）在思源里只占一半宽，
        # 「捉翻嚟嘅鸡绝对品质 OK 嘅」14 字实际 10.7 个字宽，按 14 字算就多报 3%。
        # 工具就有真字体，直接量。
        _sz = float(style["subtitle"].get("size_px") or 0)
        if _sz and cues:
            widest, widest_txt = 0.0, ""
            try:
                from PIL import ImageFont
                _fname = str(style["subtitle"].get("font") or "NotoSansSC-SemiBold")
                try:                           # 字体名是中文（「得意黑」），得走别名表找真文件
                    import fonts as _fonts
                    _rel = _fonts.file_of(_fname) or f"{_fname}.ttf"
                except Exception:
                    _rel = f"{_fname}.ttf"
                _fp = Path("~/Library/Fonts").expanduser() / _rel
                if not _fp.exists():
                    _fp = Path("~/Library/Fonts").expanduser() / f"{_fname}.ttf"
                _font = ImageFont.truetype(str(_fp), int(_sz))
                for c in cues:
                    wpx = _font.getlength(c["text"])
                    if wpx > widest:
                        widest, widest_txt = wpx, c["text"]
            except Exception:                      # 字体不在就退回字数估算，并说清楚
                _font = None
                widest = max(len(c["text"]) for c in cues) * _sz
                widest_txt = "按字宽估算"
            add("字幕最宽（像素）", f"{widest / W * 100:.0f}%W",
                "green" if widest <= 0.92 * W else "red",
                f"上限 92%W（最宽一条「{widest_txt}」实量 {widest:.0f}px / {W}px）"
                + ("" if _font else "·字体缺失，按字数估")
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
            # 字幕这一步会干两件事：按词表纠错（「四大轩王」→「四大天王」）、
            # 剔掉拖音（「有啲诶造型」→「有啲造型」）。比对前必须**两边过同一道**，
            # 否则字幕正确地改了错，判据反而报「这句已经被剪掉了」——
            # 三支片各挂一条假红，全是这一个原因。
            try:
                import lexicon as _lx
                _lex = _lx.load(proj.parents[2] / "_cutskills")
                ev = _bare(_lx.strip_noise(_lx.fix_text(host.get("evidence") or "", _lex)))
            except Exception:                                      # noqa: BLE001
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
            # 没有字幕层不是问题，但「既没字幕又没大字」就是问题：屏幕上没有字可读。
            _ncard = 0
            _cp = proj / "out" / "cards.json"
            if isinstance(_cp.exists(), bool) and _cp.exists():
                try:
                    _ncard = len(json.loads(_cp.read_text()) or [])
                except json.JSONDecodeError:
                    _ncard = 0
            add("字幕", f"不适用（大字 {_ncard} 块承担台词）",
                "green" if _ncard else "red",
                "这套包装没有字幕层（大字即字幕）；要看字有没有上屏，看大字判据"
                if _ncard else "无字幕层又没有大字——屏幕上没有字可读")
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

    # 剪辑节奏：按**真实剪辑点**判（shots.json），不是按像素变化。
    # 为什么必须分开：成片里字幕出现、卡片入场都会造成像素变化，把它当剪辑点，等于
    # 让「有没有字幕」影响「剪得够不够快」。实测同一支片：有字幕视觉切点 14、无字幕 13，
    # 差的那一刀在 23.6s，是字幕造成的（2026-11-11 对抗式审计）。
    _axis = str(edl.get("axis") or "A").upper()
    _shots_p = proj / "out" / "shots.json"
    if _axis == "B":
        # B 轴是一整段连续场景（人物缩 PiP），**本来就不该有剪辑点**：
        # 拿「剪辑点中位」判它，等于要求「一块石灰基的一生」每 2.5 秒切一刀。
        # 它要防的病是「一张静图挂 14 秒」，所以按**场景里多久没有新东西发生**判。
        # 以前这里读的是 timeline.json 里人手填的 micro 数组，两个数相减就判绿——
        # 而渲染端根本没读过 micro（gen_scene 按自己的关键帧在动）。闭环自证，作废。
        # 现在按成片量：每帧之间的画面差找「事件」，再看最长空窗。
        import numpy as _np
        _fps = 10.0
        _tmp = proj / "work" / "_verify_baxis"
        if _tmp.exists():
            import shutil as _sh
            _sh.rmtree(_tmp, ignore_errors=True)
        _tmp.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-v", "error", "-y", "-i", str(film),
             "-vf", f"fps={_fps:g},scale=270:-1", str(_tmp / "f%04d.png")])
        _fr = sorted(_tmp.glob("f*.png"))
        if len(_fr) >= 12:
            prev, _diffs = None, []
            for f_ in _fr:
                arr = _np.asarray(Image.open(f_).convert("L")).astype("float32")
                if prev is not None:
                    _diffs.append(float(_np.abs(arr - prev).mean()))
                prev = arr
            _med = float(_np.median(_diffs))
            # 判「挂着不动」，不判「够不够热闹」：慢转场、缓推镜头每帧只差一点点，
            # 拿「帧差超过中位两倍」当事件，会把一段正常的长溶解判成空档（实测 2.6s 假红）。
            # 真正的病是**一模一样**挂在那里，所以量的是「帧差低到阈值以下」的最长连跑。
            _eps = max(0.25, 0.35 * _med)
            _run, _worst, _at = 0, 0.0, 0.0
            for i, dv in enumerate(_diffs, 1):
                if dv < _eps:
                    _run += 1
                    if _run / _fps > _worst:
                        _worst, _at = _run / _fps, i / _fps
                else:
                    _run = 0
            add("场景内最长静止", f"{_worst:.2f}s（到 {_at:.1f}s）· 帧差中位 {_med:.2f}",
                "green" if _worst <= 2.0 else "yellow",
                "在成片上逐帧量：超过 2s 一模一样，看着就是静图挂着"
                if _worst > 2.0 else "一直是动的（2s 内必有变化）")
        else:
            add("场景内最长静止", "取帧失败", "yellow", "ffmpeg 没抽出帧，这条没量到")

        # B 轴 PiP：meta.json 是渲片写下的（不是 timeline 自证），声明了就要
        # 真在画面上。判据看像素：PiP 区域的纹理量——人是抠像浮在场景上的，
        # 区域 std 低于 6 基本就是没贴上（贴上了也有人脸/衣纹在动）。
        _mp = proj / "hf" / "meta.json"
        _meta = {}
        if _mp.exists():
            try:
                _meta = json.loads(_mp.read_text())
            except json.JSONDecodeError:
                _meta = {}
        _mpips = _meta.get("pips") or []
        # _want_pip 必须读 timeline.json 原件：load_timeline_or_edl 返回的是 EDL 视图，
        # 只有 segments 没有 elements——读转换视图会让声明永远为空，这条红永远不响
        # （跟「B 轴微事件」当时犯的是同一个错，别再犯）。
        _tlp = proj / "timeline.json"
        _tl_els = (json.loads(_tlp.read_text()).get("elements") or []) if _tlp.exists() else []
        _want_pip = [e for e in _tl_els
                     if str(e.get("axis") or "") == "B"
                     and str(e.get("template") or "") == "scene"
                     and (e.get("props") or {}).get("pip") is not False]
        # 声明层（want vs meta）、管线层（pips_applied）、数量层先判；
        # 任何一层不过，像素 std 多高都不能算绿。
        _verdict = pip_verdict(_want_pip, _mpips, pip_applied(_meta) if _mpips else False)
        if _verdict:
            add("B 轴人物 PiP", _verdict[1], _verdict[0], _verdict[2])
        elif _mpips:
            _bad = []
            for _p in _mpips:
                _stds = []
                for _fr in (0.45, 0.8):
                    _im = frame_at(_p["start"] + _p["dur"] * _fr, f"pip{_p['n']}_{_fr}")
                    if _im is None:
                        continue
                    _c = _im.crop((_p["x"], _p["y"], _p["x"] + _p["w"], _p["y"] + _p["h"]))
                    _stds.append(float(_np.asarray(_c, dtype="float32").std()))
                _m = min(_stds) if _stds else -1.0
                if _m < 6:
                    _bad.append(f"格 {_p['n']}（std {_m:.1f}）")
            if _bad:
                add("B 轴人物 PiP", f"{'、'.join(_bad)} 区域没内容", "red",
                    "PiP 区域像素 std < 6：多半没贴上或贴错了位置")
            else:
                add("B 轴人物 PiP", f"{len(_mpips)} 格人物小窗都在画面上", "green",
                    "PiP 区域 std ≥ 6（有人在动，不是空场景）")
    elif _shots_p.exists():
        _sh = [x for x in json.loads(_shots_p.read_text()) if float(x.get("dur") or 0) > 0.05]
        if _sh:
            _durs = sorted(float(x["dur"]) for x in _sh)
            _sm = _durs[len(_durs) // 2]
            _pid = str(edl.get("plan") or "").strip()
            _pfile = Path(__file__).resolve().parent.parent / "plans" / f"{_pid}.yaml"
            _psp = yaml.safe_load(_pfile.read_text()) if _pfile.exists() else {}
            _bd = ((_psp.get("tempo") or {}).get("band") or [None, None])
            if _bd[0]:
                _lvl = "green" if _bd[0] - 1e-6 <= _sm <= _bd[1] + 1e-6 else "yellow"
                if len(_sh) <= 1:
                    _lvl = "green"
                add("剪辑点节奏（全部镜头）", f"中位 {_sm:.2f}s · {len(_sh)} 个镜头", _lvl,
                    f"{_pid} 的档是 {_bd[0]}–{_bd[1]}s"
                    + ("" if _lvl == "green" else "；调的是镜头长度，不是调色和字幕"))
            else:
                add("剪辑点节奏（全部镜头）", f"中位 {_sm:.2f}s · {len(_sh)} 个镜头", "green",
                    "时间轴没写 plan，只报数不判档")

    # 节奏落档：画面变化中位与 CV 必须落在所选骨架的 band 内（references/rhythm-bands.md）。
    # 为什么不用「口播镜头中位」代替：那量的是素材里的机位切换，这一格量的是**观众看到的换画面**。
    # 我们 naming-01 两个数是 4.86s 和 1.27s——差四倍，因为它们根本不是一件事。
    plan_id = str(edl.get("plan") or "").strip()
    if _axis == "B":
        add("画面变化（诊断）", "不适用", "green",
            "B 轴是一段连续场景，band 管的是剪辑点；这条改用上面的「场景内最长静止」")
        plan_id = ""            # 下面不再按 band 判
    try:
        if not plan_id:
            raise RuntimeError("no-plan")
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
        # 这一条**只做诊断**：它量的是「像素有没有变」，字幕、卡片入场都算。
        # 所以不能拿 band 当门——我们这支片 1.67s，参考片 2.19s，但它多出来的变化
        # 全来自字幕和卡片，不是剪得更碎。门放宽到 band 的 0.6×～1.8×，
        # 只在**明显**偏离时报黄（比如整段几乎不动）。
        _lo, _hi = (float(_band[0]) * 0.6, float(_band[1]) * 1.8) if _band[0] else (0.0, 99.0)
        _lvl = "green" if _lo <= _med <= _hi else "yellow"
        _note = (f"像素口径，含字幕与卡片；band {_band[0]}–{_band[1]}s 只管剪辑点。"
                 f"诊断区间 {_lo:.1f}–{_hi:.1f}s" if _band[0] else "时间轴没写 plan")
        add("节奏落档", f"中位 {_med:.2f}s · CV {_cv:.2f}（{plan_id or '未指定骨架'} "
                        f"{_band[0]}–{_band[1]}s）", _lvl,
            (_note + "；" if _note else "") + ("" if _cv >= _cvmin else f"CV < {_cvmin}：节奏太匀"))
    except Exception as e:
        # B 轴没有 band 可判（那段已经单独给了「场景内微事件间隔」），不制造噪音行。
        if "no-plan" not in str(e):
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

    # 字幕的两条**语义**判据（停留时间、助词孤行）原先只在 check_subs.py 里，
    # 验收层根本没有——于是「check_subs 三片全红、verify_film 全绿」同时成立，
    # 谁也不知道该信谁。判据抽成函数后两层跑同一份，不再各写一遍。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import check_subs as _cs
        _sj = proj / "out" / "subs.json"
        _cues = ((json.loads(_sj.read_text()) or {}).get("cues") or []) if _sj.exists() else []
        if not _cues:
            add("字幕停留", "没有 out/subs.json", "yellow", "这套包装没上字幕层，或还没渲")
        else:
            _hb, _hn = _cs.hold_judge(_cues)
            _short = [x for x in _hb if x["s"] < 0.6]
            _long = [x for x in _hb if x["s"] > _cs.HOLD_MAX + 0.02]
            if _hb:
                add("字幕停留", f"{len(_hb)}/{len(_cues)} 条越界"
                                f"（最短 {min(x['s'] for x in _hb):.2f}s）", "red",
                    "闪一下或赖在屏幕上：" + "、".join(x["text"] for x in _hb[:3]))
            else:
                add("字幕停留", f"{len(_cues)} 条全在 {_cs.HOLD_MIN}–{_cs.HOLD_MAX}s 内", "green",
                    f"另有 {len(_hn)} 条收在句读上的短句（可接受）" if _hn else "")
            _dg = _cs.dangling_judge(_cues)
            if _dg:
                add("助词孤行", f"{len(_dg)} 条", "red",
                    "；".join(f"{x['text']}（{x['why']}）" for x in _dg[:3]))
            else:
                add("助词孤行", "0 条", "green", "条首条尾都没落在虚字上")
    except Exception as e:                      # 判据本身坏了也要说出来
        add("字幕停留", "判据跑不动", "yellow", str(e)[:60])

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
