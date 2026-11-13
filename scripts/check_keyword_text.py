#!/usr/bin/env python3
"""CutSkills · 大字（keyword / behind）裁判：压脸 / 可读性 / 单排宽度

为什么要有这一道（T-045，T-022 的 1080 原像素复核暴露）：
`card_layout` 的避脸只服务 MG 卡（stat / versus / term），`check_overlay` 里写着
「大字走 .kw 层，另有 top_h 规则，不在本判据内」——于是大字是**唯一没有人量过位置**
的一层。成片里两次同时出现了：

  · `dianzan-c` 10.74s「四张图，介绍布局」：字框 `y 585–679`，而那一帧
    （`素材/2026-10-16 144023.mov` 第 0.63s）有一张脸 `x 0.314–0.526 / y 0.279–0.398`
    —— 大字正好压在眼睛、鼻子、颧骨上；视觉判词 2/10；
  · 同一片 6.75s：亮黄字压在杂乱的工作台/工具/人物上，没有描边、没有底衬。
    这一条`verify_film` 是绿的——它只数「花字色占比」，字被背景吃掉它也数得出花字色。

所以这里判三件事，全部用**渲之前就能拿到的真数据**：

  ① 字框 × 人脸框：卡窗内每一采样帧的每一张脸，与**实际墨迹框**重叠面积 > 0 → error。
     人脸的来源与 `check_overlay` 同一份（`card_layout.faces_of`，Vision 原始读数，
     2fps），不采信渲染器写在 cards.json 里的结论；B-roll 格照量（这次的洞就在 B-roll）。
  ② 可读性：墨迹与它**紧邻**的背景实量 ΔL p10 ≥ 12（`measure_panel` 那一套：
     转 CIE L*、笔画像素对 15×15 窗口里非笔画像素的中位取差、取 p10 刨掉抗锯齿）。
     差值取自真实片段帧（`hf/clips/*.mp4`，就是马上要被合成的那几帧）；
     卡开了隔离层（timeline `card.isolate`）就先把包里 `emphasis.isolate` 那块柔化压暗
     合成上去再量——隔离层有没有用，得能量出来，不能靠「我加了」。
  ③ 单排宽度：墨迹宽 ÷ 画幅宽必须落在 0.62–0.78（Jerry 2026-11-09：大字单排撑 70%W）。

三个数都不是估的：字框由 `card_layout.kw_font_px / kw_ink_box` 从真字体文件量出来
（**渲染器画字用的是同一份函数**，两边不会漂），实测与渲出帧的墨迹横向差 ≤ 8px。

    python3 scripts/check_keyword_text.py --project <项目>          # 判项目（渲前闸门）
    python3 scripts/check_keyword_text.py --project <项目> --film   # 顺手对一遍渲出的成片
    python3 scripts/check_keyword_text.py --project <项目> --json

`--film` 时字框改成**从渲出的帧里量墨迹 bbox**（不再用字体模型）：渲出来的位置和
预测对不对得上、字到底上没上屏，只有这一条能回答。成片不存在就明说跳过，不当通过。

退出码：0 = 没有 error；1 = 有 error（render_hf.py 里是 8，和 5/6/7 分开）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import card_layout as CL          # noqa: E402
import measure_panel as MP        # noqa: E402

KW_KINDS = CL.KW_KINDS
KW_WIDTH_MIN, KW_WIDTH_MAX = CL.KW_WIDTH_MIN, CL.KW_WIDTH_MAX
KW_DL_MIN = CL.KW_DL_MIN
SAMPLE_STEP = 0.25                # 判据采样步长（秒）：一张 1s 的卡取 4 帧
FACE_HZ = 2.0                     # 人脸采样率——与 card_layout.faces_of 的缓存同一档
FACE_TOL = 0.5 / FACE_HZ + 0.05   # 半格容差：只判「有脸数据的那几个瞬间」
INK_TOL = 64                      # 与 verify_film.color_share 同一个容差
DL_WIN = 15                       # 邻域窗口（与 measure_panel.delta_l 相同）
DL_SAMPLES = 1200                 # 每帧最多取这么多个笔画点（固定步长，可复现）
DILATE_PX = 10                    # --film 时「花字色 ∩ 预测字形」的外扩半径（预测框 ±8px）


# ---------------------------------------------------------------- 读盘

def load(p: Path, default=None):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except json.JSONDecodeError:
        return default


def timeline(proj: Path):
    for name in ("timeline.json", "edl.json"):
        d = load(proj / name)
        if d:
            return d, list(d.get("elements") or d.get("segments") or [])
    return {}, []


def pack_of(proj: Path, pack_file: Path | None = None) -> tuple[dict, int, int]:
    """项目现在穿的是哪件包：`timeline.pack`（渲染器读的就是它）→ meta.json 兜底。

    `pack_file` 直接指一个 yaml：夹具（`test_keyword_text.py`）用它在临时目录里造
    「宽到越界 / 隔离层浓度不同」的包，不用往仓库 `packs/` 里塞测试包
    （那一目录是 check_packs 的输入，塞进去就污染了真包的合同测试）。
    """
    import yaml
    if pack_file is not None:
        d = yaml.safe_load(Path(pack_file).read_text()) or {}
        c = d.get("canvas") or {}
        return d, int(c.get("width") or 1080), int(c.get("height") or 1920)
    tl, _ = timeline(proj)
    pack = str(tl.get("pack") or tl.get("style_pack") or "").strip()
    if not pack:
        pack = str((load(proj / "hf" / "meta.json", {}) or {}).get("style") or "").strip()
    for base in ("packs", "styles"):
        p = HERE.parent / base / f"{pack}.yaml"
        if pack and p.exists():
            d = yaml.safe_load(p.read_text()) or {}
            c = d.get("canvas") or {}
            return d, int(c.get("width") or 1080), int(c.get("height") or 1920)
    return {}, 1080, 1920


def el_dur(el: dict) -> float:
    if (el.get("kind") or "clip") == "clip":
        return round(float(el.get("out") or 0) - float(el.get("in") or 0), 3)
    return round(float(el.get("dur") or 0), 3)


def clip_tag(el: dict) -> str:
    """`render_hf.get_clip` 给片段起的名字（同一份规则，判据才找得到那几帧）。"""
    tag = f"{Path(str(el.get('asset'))).stem[-14:]}_{float(el['in']):.2f}-" \
          f"{float(el['out']):.2f}".replace(".", "p")
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)


def videos_of(proj: Path, segs: list[dict]) -> list[dict]:
    """成片时间 → 片段文件。渲前闸门拿不到渲染器的 videos 变量，就按同一条规则重算：
    起点靠累计时长，文件名靠 clip_tag（渲染器切段用的就是这两个数）。"""
    out, t = [], 0.0
    for el in segs:
        d = el_dur(el)
        if d <= 0:
            continue
        if (el.get("kind") or "clip") in ("clip", "image"):
            p = proj / "hf" / "clips" / f"{clip_tag(el)}.mp4"
            if not p.exists():                     # 素材改过 in/out：找同素材的现成片段
                cand = sorted((proj / "hf" / "clips").glob(
                    f"{clip_tag(el).rsplit('_', 1)[0]}_*.mp4"))
                p = cand[-1] if cand else p
            out.append({"n": int(el.get("n") or 0), "clip": p,
                        "start": round(t, 3), "dur": d,
                        "medium": str(el.get("medium") or "")})
        t = round(t + d, 3)
    return out


def frame_of(clip: Path, t: float, W: int, H: int, cache: dict):
    """片段第 t 秒的一帧（RGB, H×W×3）。等同于渲染器会合成的那一帧。"""
    key = (str(clip), round(max(0.0, t), 3))
    if key in cache:
        return cache[key]
    from render_hf import ffmpeg_bin
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, t):.3f}",
                        "-i", str(clip), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True)
    if r.returncode != 0 or len(r.stdout) < W * H * 3:
        cache[key] = None
        return None
    a = np.frombuffer(r.stdout[:W * H * 3], dtype=np.uint8).reshape(H, W, 3)
    cache[key] = a
    return a


# ---------------------------------------------------------------- 三条量法

def _ink_from_color(frame: np.ndarray, hexs: str, tol: int = INK_TOL):
    r0, g0, b0 = (int(hexs.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    d = np.abs(frame.astype(np.int16) - np.array([r0, g0, b0], dtype=np.int16))
    return (d <= tol).all(axis=2)


def _dilate(mask: np.ndarray, px: int) -> np.ndarray:
    """字形掩码外扩 px（方形膨胀）。只用来吸收「预测框 vs 渲出帧」几个像素的差。"""
    if px <= 0:
        return mask
    from PIL import Image, ImageFilter
    im = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    return np.asarray(im.filter(ImageFilter.MaxFilter(px * 2 + 1))) > 128


def _bbox(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return None
    return [int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def readable_dl(frame: np.ndarray, ink: np.ndarray, box, ink_l: float) -> dict:
    """墨迹 vs 紧邻背景的 ΔL（p10）。`measure_panel.delta_l` 的口径，但笔画从外面给：

    面板那张图里「哪个像素是笔画」靠「与底板差 25 个 L*」猜；大字压在实拍上时没有
    底板可猜（背景自己就是一片杂色），所以笔画掩码由字体栅格化（渲前）/ 花字色匹配
    （渲后）给进来，邻域里「非笔画的像素」才是这块字真正的背景。

    笔画的明度由字色给（`ink_l`，不透明色就是它自己的 L*），背景取**实拍帧**的
    L*——渲前那几帧还没把字合上去，正好是「字底下压着什么」的原始读数。

    返回 {"dl_p10", "dl_med", "n"}；`n == 0` = 这份掩码找不到任何边缘笔画（
    字太大块、或者根本没有字）→ 调用方不许当合格。
    """
    x, y, w, h = [int(round(v)) for v in box]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return {"dl_p10": None, "dl_med": None, "n": 0}
    sub_ink = ink[y0:y1, x0:x1]
    if not sub_ink.any():
        return {"dl_p10": None, "dl_med": None, "n": 0}
    L = MP.lstar(frame[y0:y1, x0:x1].astype(np.float64))
    ys, xs = np.nonzero(sub_ink)
    if len(ys) > DL_SAMPLES:
        step = len(ys) // DL_SAMPLES
        ys, xs = ys[::step][:DL_SAMPLES], xs[::step][:DL_SAMPLES]
    hh, ww = L.shape
    r = DL_WIN // 2
    dl = []
    for yy, xx in zip(ys, xs):
        wy0, wy1 = max(0, yy - r), min(hh, yy + r + 1)
        wx0, wx1 = max(0, xx - r), min(ww, xx + r + 1)
        nb = L[wy0:wy1, wx0:wx1][~sub_ink[wy0:wy1, wx0:wx1]]
        if nb.size < 8:                     # 笔画内部：周围全是自己，量不出背景
            continue
        dl.append(float(ink_l - float(np.median(nb))))
    if not dl:
        return {"dl_p10": None, "dl_med": None, "n": 0}
    a = np.abs(np.asarray(dl))
    return {"dl_p10": round(float(np.percentile(a, 10)), 1),
            "dl_med": round(float(np.median(a)), 1), "n": len(dl)}


def face_hits(proj: Path, win, videos: list[dict], box_n) -> list[dict]:
    """卡窗内每一采样帧里，与字框真重叠（面积 > 0）的每一张脸。

    `box_n` 是归一化字框 [x, y, w, h]。人脸数据按片段缓存（2fps，Vision 原始读数），
    只在「有脸数据的那几个瞬间」判——半格容差，不把 0.4s 之外的脸算进这一帧。
    """
    hits = []
    t = float(win[0])
    while t <= float(win[1]) + 1e-6:
        v = next((v for v in videos
                  if v["start"] - 1e-6 <= t <= v["start"] + v["dur"] + 1e-6), None)
        if v and v["clip"].exists():
            faces = CL.faces_of(proj, v["clip"], FACE_HZ) or []
            local = t - v["start"]
            for f in faces:
                if abs(float(f.get("t") or 0.0) - local) > FACE_TOL:
                    continue
                fb = CL.fbox(f)
                ix = max(0.0, min(box_n[0] + box_n[2], fb[0] + fb[2]) - max(box_n[0], fb[0]))
                iy = max(0.0, min(box_n[1] + box_n[3], fb[1] + fb[3]) - max(box_n[1], fb[1]))
                inter = ix * iy
                if inter > CL.KW_TOUCH_EPS:
                    hits.append({"t": round(t, 3), "face": [round(v_, 3) for v_ in fb],
                                 "inter": round(inter, 6),
                                 "face_cov": round(inter / max(1e-9, fb[2] * fb[3]), 4)})
        t = round(t + SAMPLE_STEP, 3)
    return hits


# ---------------------------------------------------------------- 主判据

def audit(proj: Path, film: Path | None = None,
          pack_file: Path | None = None) -> list[tuple[str, str]]:
    proj = Path(proj).expanduser().resolve()
    style, W, H = pack_of(proj, pack_file)
    if not style:
        return [("warning", "找不到包装包（timeline.pack / hf/meta.json）——大字判据判不了")]
    emph = style.get("emphasis") or {}
    ink_hex = str(emph.get("keyword_color") or "#FFD400")
    _rgb = [int(ink_hex.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    ink_l = float(MP.lstar(np.array([_rgb], dtype=np.float64))[0])
    cards = list((load(proj / "out" / "cards.json", {}) or {}).get("cards") or [])
    # 卡面文字 / 卡型 / 上沿 / 隔离层这四样是**作者数据**（timeline），时长才是渲染器算的
    # （cards.json）。只看 cards.json 会读到上一轮渲的旧值——改了 timeline 还没重渲时，
    # 闸门会拿旧字框判新卡，判的和画的是两件事。
    _, _segs0 = timeline(proj)
    _by_n = {int(e.get("n") or 0): (e.get("card") or {}) for e in _segs0}
    for c in cards:
        _tc = _by_n.get(int(c.get("seg") or 0)) or {}
        if not _tc:
            continue
        # 这几个字段以 timeline 为准（读的是**现值**，不是上一轮渲的旧值）：
        # 删掉 isolate、改 top_h 这类动作如果等到重渲才生效，闸门就成了「拿旧卡判新卡」。
        for k in ("text", "kind"):
            if _tc.get(k) is not None:
                c[k] = _tc[k]
        if _tc.get("top_h") is not None:
            c["top_h"] = _tc["top_h"]
        else:
            c.pop("top_h", None)
        if _tc.get("isolate"):
            c["isolate"] = True
        else:
            c.pop("isolate", None)
    kw = [c for c in cards if str(c.get("kind") or "") in KW_KINDS]
    if not kw:
        return [("info", "没有 keyword / behind 大字卡，这一层不适用")]
    _, segs = timeline(proj)
    videos = videos_of(proj, segs)

    film_frames: dict = {}
    if film is not None and not Path(film).exists():
        film = None

    rows: list[tuple[str, str]] = []
    for c in kw:
        mg = str(c.get("mg") or f"mg-{c.get('seg')}")
        kind = str(c.get("kind") or "")
        text = str(c.get("text") or "")
        win = (float(c["start"]), float(c["end"]))
        top_h, _scrim, _own = CL.kw_top_of(c, style, W, H)
        box = CL.kw_ink_box(text, style, W, H, top_h)
        if box is None:
            rows.append(("error", f"{mg}（{kind} {win[0]:.2f}–{win[1]:.2f}s）"
                                  f"量不出字框：字体文件找不到（{emph.get('font')}）"))
            continue
        mask, _ = CL.ink_mask(text, style, W, H, top_h)
        if mask is None:
            why = ("卡面文字为空——这张大字根本没上屏" if not text.strip()
                   else f"字形栅格化失败（{emph.get('font')} 装不上）")
            rows.append(("error", f"{mg}（{kind} {win[0]:.2f}–{win[1]:.2f}s）{why}，"
                                  f"压脸/可读性都判不了，不当它通过"))
            continue
        iso = CL.kw_isolate(style, box["px"], W, H) if c.get("isolate") else None
        pod = CL.kw_pod_alpha(iso, W, H) if iso else None

        # ③ 单排宽度（渲前就能判，不需要任何帧）
        bw = box["box"][2]
        if not (KW_WIDTH_MIN <= bw <= KW_WIDTH_MAX):
            rows.append(("error",
                         f"{mg}（{kind} {win[0]:.2f}–{win[1]:.2f}s）大字「{text}」单排宽 "
                         f"{bw * 100:.1f}%W，越界 {KW_WIDTH_MIN * 100:.0f}–"
                         f"{KW_WIDTH_MAX * 100:.0f}%W（{box['px'][2]}px / {W}px，"
                         f"字号 {box['font_px']}px）"))
        else:
            rows.append(("info", f"{mg}「{text}」字框 {box['px']} = {bw * 100:.1f}%W"
                                 f"（字号 {box['font_px']}px，上沿 {top_h:.0%}H"
                                 f"{'，隔离层 ' + iso['rgba'] if iso else ''}）"))

        # ① 压脸
        hits = face_hits(proj, win, videos, box["box"])
        if not videos:
            rows.append(("warning", f"{mg} 这一格找不到片段文件（hf/clips）——压脸判不了"))
        elif hits:
            worst = max(hits, key=lambda h: h["face_cov"])
            rows.append(("error",
                         f"{mg}（{kind} {win[0]:.2f}–{win[1]:.2f}s）大字「{text}」压脸："
                         f"{worst['t']:.2f}s 那一帧的人脸 {worst['face']}"
                         f"（归一化）与字框 {[round(v, 3) for v in box['box']]} 重叠 "
                         f"{worst['inter'] * 100:.2f}% 画面面积（占那张脸 "
                         f"{worst['face_cov'] * 100:.0f}%），共 {len(hits)} 帧命中"))
        elif not any(v for v in videos
                     if v["start"] < win[1] and v["start"] + v["dur"] > win[0]):
            rows.append(("warning", f"{mg} 卡窗与任何片段都不重叠——压脸判不了"))

        # ② 可读性：逐采样帧量，取最差那一帧
        # 采样从入场动画之后开始（`.kw span` 是 0.34s 的 back.out 淡入）：淡到一半的帧
        # 墨迹是半透明的，拿去量 ΔL 等于给每张卡都判一条假红（实测 dianzan-c mg-02
        # 在 10.54s 量到 10.6、等它落定之后同一张是 60+）。
        ts = []
        t = round(win[0] + 0.45, 3)
        while t < win[1] - 0.05 and len(ts) < 4:
            ts.append(t)
            t = round(t + max(SAMPLE_STEP, (win[1] - win[0] - 0.5) / 4), 3)
        worst_dl, meas_boxes = None, []
        for t in ts:
            v = next((v for v in videos
                      if v["start"] - 1e-6 <= t <= v["start"] + v["dur"] + 1e-6), None)
            if not v:
                continue
            fr = frame_of(v["clip"], t - v["start"], W, H, film_frames)
            if fr is None:
                continue
            if film is not None:
                ff = frame_of(Path(film), t, W, H, film_frames)
                if ff is not None:
                    # 成片里的墨迹 = 花字色匹配 **∩** 预测字形（外扩 DILATE_PX）。
                    # 为什么必须取交集：画面里本来就有同色的东西（招牌、黄漆、水温牌）——
                    # `qizao-ground-01` 30.2s 那一带躺着 682 个 #F6EB34 像素，
                    # 它们不是墨迹，却会被色匹配算成笔画，ΔL 立刻塌到 10 以下（T-044 同一个病）。
                    # 外扩 10px 是给「预测框 ±8px」留的余量，不是给「字跑到别处」留的。
                    m2 = _ink_from_color(ff, ink_hex) & _dilate(mask, DILATE_PX)
                    bb = _bbox(m2)
                    if bb is None:
                        rows.append(("error",
                                     f"{mg}（{kind}）{t:.2f}s 的成片帧里，预测字形的位置上"
                                     f"量不到花字色 {ink_hex}——这张大字没上屏"))
                        continue
                    r2 = readable_dl(ff, m2, bb, ink_l)
                    r2["_src"] = "film"
                    r2["_box"] = bb
                    meas_boxes.append(bb)
                    worst_dl = r2 if worst_dl is None or (
                        r2["dl_p10"] is not None and (worst_dl["dl_p10"] is None or
                                                      r2["dl_p10"] < worst_dl["dl_p10"])) else worst_dl
                continue
            f2 = fr.astype(np.float32)
            if pod is not None:                    # 隔离层：先压暗，再量「字底下的那几行像素」
                a = pod[..., None]
                f2 = f2 * (1.0 - a) + np.array(iso["rgb"], dtype=np.float32) * a
            r = readable_dl(f2.astype(np.uint8), mask, box["px"], ink_l)
            r["_src"] = "clip"
            worst_dl = r if worst_dl is None or (
                r["dl_p10"] is not None and (worst_dl["dl_p10"] is None or
                                             r["dl_p10"] < worst_dl["dl_p10"])) else worst_dl
        if meas_boxes:
            # 渲出来的位置到底对不对：预测框 vs 实测墨迹 bbox（--film 才量得到）
            mb = meas_boxes[len(meas_boxes) // 2]
            dx, dy = mb[0] - box["px"][0], mb[1] - box["px"][1]
            mw = mb[2] / W
            rows.append(("info", f"{mg} 成片墨迹 bbox {mb} = {mw * 100:.1f}%W"
                                 f"（预测 {box['px']} = {bw * 100:.1f}%W，横向差 {dx:+d}px、"
                                 f"纵向差 {dy:+d}px）"))
            if not (KW_WIDTH_MIN <= mw <= KW_WIDTH_MAX):
                rows.append(("error",
                             f"{mg}（{kind}）成片里大字墨迹单排宽 {mw * 100:.1f}%W，越界 "
                             f"{KW_WIDTH_MIN * 100:.0f}–{KW_WIDTH_MAX * 100:.0f}%W"
                             f"（实测 bbox {mb}）"))
        if worst_dl is None:
            rows.append(("warning", f"{mg} 一帧都没取到（片段缺失）——可读性判不了"))
        elif worst_dl["dl_p10"] is None:
            rows.append(("error", f"{mg}（{kind}）量不出墨迹边缘（{worst_dl['_src']} 帧"
                                  f"里找不到笔画邻域）——「字读不读得出」判不了，"
                                  f"不当它通过"))
        elif worst_dl["dl_p10"] < KW_DL_MIN and worst_dl["_src"] == "film":
            # 成片口径只报读数、不当判据：这一层的墨迹靠**颜色匹配**找，背景里同色的
            # 东西（黄塑料筐、暖木、水反光）会被算成笔画或算成邻域，p10 立刻塌下去
            # （`dianzan-c` mg-04 实测 10.5，而同一张卡在渲前片段上量到 37——差别就是
            # 画面里那片黄筐）。把这一层量对的修法在 T-044（卡前/卡后帧间差分），
            # 在它落地之前，成片这条只能当读数看，不许拿它判红——那会把「背景有黄色的
            # 片子」一律判死，还是假红。
            rows.append(("info",
                         f"{mg} 成片口径读数（不当判据）：墨迹 vs 紧邻背景 ΔL 最差(p10) "
                         f"{worst_dl['dl_p10']}（T-044 未落地：色匹配分不开背景同色，"
                         f"以渲前片段那条读数为准）"))
        elif worst_dl["dl_p10"] < KW_DL_MIN:
            rows.append(("error",
                         f"{mg}（{kind} {win[0]:.2f}–{win[1]:.2f}s）大字「{text}」可读性："
                         f"墨迹 vs 紧邻背景 ΔL 最差(p10) {worst_dl['dl_p10']} < "
                         f"{KW_DL_MIN:.0f}（{worst_dl['_src']} 帧，笔画 L*≈{ink_l:.0f}，"
                         f"{worst_dl['n']} 个边缘点）——字被背景吃掉"))
        else:
            rows.append(("info", f"{mg} 可读性 ΔL(p10) {worst_dl['dl_p10']} ≥ "
                                 f"{KW_DL_MIN:.0f}（{worst_dl['_src']} 帧，"
                                 f"{worst_dl['n']} 点）"
                                 + ("· 含隔离层" if iso and worst_dl['_src'] == 'clip' else "")))

    if not any(lv == "error" for lv, _ in rows):
        rows.append(("info", f"{len(kw)} 张大字全部通过（压脸 / 可读性 / 单排宽度）"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--film", action="store_true",
                    help="顺手对一遍渲出的成片（字框改成从帧里量墨迹 bbox）")
    ap.add_argument("--pack-file", default=None,
                    help="直接指定包装包 yaml（夹具用；缺省按 timeline.pack 找）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    film = (proj / "out" / "film-hf.mp4") if a.film else None
    if a.film and not film.exists():
        print(f"！{film} 不存在：--film 这一遍没跑（渲出来再判），不当通过", file=sys.stderr)
        film = None
    rows = audit(proj, film, Path(a.pack_file).expanduser() if a.pack_file else None)
    if a.json:
        print(json.dumps([{"level": lv, "msg": m} for lv, m in rows],
                         ensure_ascii=False, indent=1))
    else:
        mark = {"error": "✗", "warning": "!", "info": "·"}
        for lv, m in rows:
            print(f"{mark.get(lv, '·')} {m}")
    n_err = sum(1 for lv, _ in rows if lv == "error")
    n_warn = sum(1 for lv, _ in rows if lv == "warning")
    if not a.json:
        print(f"大字校验：error {n_err} · warning {n_warn}")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
