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

# 参考片一条字幕多长（字）。来源是同一条 126.58 s 知识口播片的逐帧反解：
# 113 条、每条均 8.9 / 中位 8 / 上限 12（talking-head-packaging/references/measured-spec.md）。
# 判据用它当「切法基准」，而不是拿本片自己的均值反推——反推会把条数 N 约掉，
# 那条判据就永远 0 偏差、什么都不判了（T-041）。
REF_CHARS_PER_CUE = 8.0

# 「大字/花字真的上屏了吗」的墨迹下限，按卡型分开：
# behind/cutout 是故意被人挡住的，只剩可见边缘，墨迹天然远少于 keyword
# （实测同一块字 keyword 0.4% 上下、behind 0.13%）。这里只判「字确实在画面上」，
# 遮挡多少交给成片看，不假装能从一个数里量出来。
KW_FLOOR_BEHIND = 0.0008
KW_FLOOR_KEYWORD = 0.004


def _pixels(im) -> list:
    fn = getattr(im, "get_flattened_data", None) or im.getdata
    return list(fn())


def dark_ratio(im) -> float:
    """深底占比：像素 < 46（L 模式）的比例。供「代码画面真的在画面上」用，
    也供 selftest 的负样本调用——实拍帧必须量出低值，不然判据就是假的。"""
    px = _pixels(im)
    return sum(1 for v in px if v < 46) / max(1, len(px))


def median_of(vals: list[float]):
    return sorted(vals)[len(vals) // 2] if vals else None


def color_share(im, hexs, box, tol: int = 64) -> float:
    """一块区域里接近某个颜色的像素占比。

    为什么不用「深色占比」：那判据只对 ffmpeg 那条链的深底衬卡成立。
    HyperFrames 链的大字是**亮黄字压画面**，深色占比天然为 0，
    于是"卡明明在画面上"也会报红。卡是我们自己设计的，颜色是已知的，
    就按颜色查——这比"深不深"诚实地多。
    """
    r0, g0, b0 = (int(hexs.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    crop = im.crop(box).convert("RGB")
    px = _pixels(crop)
    n = sum(1 for r, g, b in px
            if abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol)
    return n / max(1, len(px))


def kw_ink_level(in_share: float, base_share, kind: str) -> tuple[str, float]:
    """有卡帧的墨迹减去「卡不在」的基线 → 判 green/red，返回 (级别, 净墨迹)。

    为什么要减基线：画面里本来就有同色的东西（招牌、包装 accent 用在大字以外的地方）。
    `qizao-ground-01` 30.2s 那一帧，behind 的中段带里躺着 682 个 #F6EB34 像素（0.07%，
    和卡无关）；而 behind 的墨迹下限只有 0.08%，一张「字根本没上屏」的帧能靠这点背景黄
    擦着过线。减掉卡前/卡后的同一带子中位，背景就抵消了，判据量到的才是这张卡加的墨。
    """
    floor = KW_FLOOR_BEHIND if kind in ("behind", "cutout") else KW_FLOOR_KEYWORD
    if base_share is None:
        return ("green" if in_share > floor else "red"), float(in_share)
    base = max(0.0, min(float(base_share), float(in_share)))
    ink = float(in_share) - base
    if in_share > 0 and base >= 0.5 * float(in_share):
        # 带子被画面自己的同色占满（暖色/木色/陶土色多的镜头）：这时候减基线会把卡的墨迹
        # 一起减掉——proj79 的 behind「成本贵在哪」带内 2.55%、卡不在时 2.48%，净 0.07%，
        # 但那张卡其实在画面上（渲出来是半透明的淡黄 #F2E98F，与 pack accent 差 91 灰阶）。
        # 退回旧口径「带内墨迹够量就算上屏」，并如实标注「净墨迹不可判」——
        # 别拿一个量不出来的差值去报红（T-041 追加，留 T-044 用帧间差分重做这一层）。
        return ("green" if in_share > floor else "red"), ink
    return ("green" if ink > floor else "red"), ink


def kw_box_px(card: dict, W: int, H: int, margin: int = 24):
    """大字卡**自己**在画面上的框（渲器落盘的 `cards.json.kw_box_px`），四边留 margin。

    为什么不去现场算：`verify_film` 手里的 `style` 是 `pack_style.to_style()` 转过一道的
    渲染 schema（`emphasis.top_h` / `fit_width` 到这一步已经变成 `center_y_px` 之类的
    另一套字段），拿它算字框会算出第二次错误的几何。画字的和记框的是渲染器同一次调用
    （`render_hf` 写 `kw_box_px` 用的是 `build_html` 同一个函数），判据只管读。
    老项目没有这个字段 → 返回 None，退回按卡型的固定带子（旧行为，不退步）。
    """
    b = card.get("kw_box_px")
    if not b or len(b) != 4:
        return None
    return (max(0, int(b[0]) - margin), max(0, int(b[1]) - margin),
            min(W, int(b[0]) + int(b[2]) + margin), min(H, int(b[1]) + int(b[3]) + margin))


def text_band(kind: str, box, W: int, H: int) -> tuple[int, int, int, int]:
    """大字/花字该在画面的哪一带取样——**按卡型分开**。

    keyword（替字幕说话的中央大字）落在中段带；behind / cutout（人脑后大字）
    的排版天然更高：`emphasis.top_h: 0.30` 只是上限，字撑到 70% 屏宽之后整块上移，
    实测 `qizao-ground-01` 那张 behind 的「售楼部 · 酒店」黄字在 **y 115–204**
    （画面 6%–11%H），而中段带从 28%H 才开始——必然量空，报「字没上屏」（假红）。
    有卡自己的框时（`layout.box_px` / `kw_box_px`，外面各留 24px 余量，由调用方给）
    **一律以它为准**——T-045 的 `dianzan-c` mg-02 从 0.30H 抬到 0.165H 之后，
    keyword 的中段带（28%H 起）同样量空，报「花字色 0.00%」（和 behind 那条假红
    一个病根：带子写死在判据里，卡挪了判据没挪）。
    """
    if box:
        return box
    if kind in ("behind", "cutout"):
        return (0, 0, W, int(H * 0.45))
    return (0, int(H * 0.28), W, int(H * 0.76))


# ------------------------------------------------- 大字墨迹：字形掩膜 × 帧差（T-044）
# 旧口径「数接近 accent 色的像素占多少」有两个量不出来的地方（T-041 挂出）：
#   ① 半透明大字渲出来是淡黄 #F2E98F，与包里的 #F6EB34 差 91 个蓝阶，颜色测试根本看不见；
#   ② 暖色镜头（木色/陶罐/招牌）在 tol=64 下自带 0.07–2.5% 的「假同色」，把带子填满时
#      净墨迹不可判。
# 新口径不猜颜色，拿**这张字自己的字形掩膜**（PIL 同字体/同字号/同位置栅格化）当探针，
# 量「卡在时 vs 卡不在时」的帧差：
#   ink     = 掩膜内的笔画像素；
#   control = 同一卡框内、笔画之外（背景/框内留白）。
# 同一个镜头里两边承受的背景运动近似，字是**只加在 ink 上**的那部分。判据：
#   R_add = median|Δ|(ink) / median|Δ|(control) ≥ INK_DIFF_K 且 median|Δ|(ink) ≥ 地板。
# 半透明字把背景运动挡掉，帧差比会掉到 1 以下，但它同时留下另一个证据：
# **静态叠加层**——三张卡内帧上 ink 的时间标准差远小于 control。两条都量，任一成立即绿；
# 一条都不成立才红。这样「淡黄字压暖底」与「暖底上本来就没人写字」才分得开。
INK_DIFF_K = 1.4          # ink 帧差 ≥ K × control（正样本最低 1.63：ground-01「售楼部 · 酒店」）
INK_DIFF_FLOOR = 8.0      # ink 帧差绝对地板（灰阶 0–255）；负样本实测 1–19
INK_OCC_K = 0.55          # ink 时间标准差 ≤ K × control（正样本 ≤0.28，负样本 ≥0.75）
INK_OCC_CTL_STD = 1.5     # control 必须真的在动，静态叠加这条才有信息量
INK_OCC_INK_STD = 2.0     # ink 自身必须稳（移动物体的 std 是 15–40）
INK_MOTION_BLOB = 0.8     # ink 动得比 control 还厉害 = 移动物体，不是字（正样本 ≤0.5，负样本 ≥1.25）
INK_FPS = 10              # 帧差取样帧率；10fps 下 0.1s 间隔足够避开压缩噪声
INK_PAD = 24              # 卡框外扩（与旧取样带同口径）
INK_ERODE = 2             # 笔画内缩 2px：抗字形预测的 ±1–4px 偏差
INK_CTRL_GAP = 12         # control 与笔画之间留 12px：把投影/抗锯齿边排除掉
INK_CUT_GUARD = 60.0      # 全帧中位帧差超过大概率是切点（快速摇镜能到 50，底线留 60）


def _mask_morph(mask, radius: int, grow: bool):
    """二值掩膜的膨胀/腐蚀（PIL MaxFilter/MinFilter，正圆核近似方核）。"""
    from PIL import Image, ImageFilter
    im = Image.fromarray((mask * 255).astype("uint8"))
    im = im.filter(ImageFilter.MaxFilter(2 * radius + 1) if grow
                   else ImageFilter.MinFilter(2 * radius + 1))
    import numpy as np
    return np.asarray(im) > 128


def glyph_ink_mask(card: dict, style: dict, W: int, H: int):
    """这张大字卡**本该占的**字形掩膜与墨迹框。渲器与判据同一份几何。

    位置优先读渲器落盘的 `kw_top_h`（画字与记框是同一次调用）；老项目没有这个字段，
    退回 `card_layout.kw_top_of(card, style, W, H)`——与渲染器同一条优先级。
    返回 (mask, box_px, top_h) 或 (None, None, None)。
    """
    try:
        import card_layout
    except Exception:
        return None, None, None
    text = str(card.get("text") or "")
    if not text:
        return None, None, None
    th = card.get("kw_top_h")
    if th is None:
        try:
            th = card_layout.kw_top_of(card, style, W, H)[0]
        except Exception:
            th = None
    try:
        mask, kbox = card_layout.ink_mask(text, style, W, H, None if th is None else float(th))
    except Exception:
        return None, None, None
    if mask is None or kbox is None:
        return None, None, None
    return mask, kbox["px"], float(kbox["top_h"])


def _gray_window(film: Path, t0: float, dur: float, W: int, H: int):
    """取一段灰度帧（rawvideo，无 JPEG 压缩）。返回 (frames[n,H,W], t0)。量不到返回 (None, t0)。"""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.3f}",
                        "-i", str(film), "-t", f"{max(0.04, dur):.3f}",
                        "-vf", f"fps={INK_FPS},scale={W}:{H}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None, t0
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (W * H)
    if n < 1:
        return None, t0
    return a[: n * W * H].reshape(n, H, W).astype("float32"), t0


def _rgb_frame(film: Path, t: float, W: int, H: int):
    """单帧 RGB（rawvideo）。量不到返回 None。"""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}",
                        "-i", str(film), "-frames:v", "1",
                        "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True)
    if r.returncode != 0 or len(r.stdout) < W * H * 3:
        return None
    return np.frombuffer(r.stdout[: W * H * 3], np.uint8).reshape(H, W, 3).astype("float32")


def _hex_rgb(s: str):
    h = str(s or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def film_cut_times(fin: dict, proj: Path) -> list[float]:
    """成片里的硬切点（秒）。取自 `out/shots.json`（渲器落盘的镜头表，时间相对段内）。

    为什么需要它：卡窗两侧 ±1.5s 里可能有一刀。跨过切点的两帧不是同一镜头，
    拿它们做帧差会把「换镜」当成「字加了一笔墨」。只看全帧中位帧差不够——
    快速摇镜的中位差同样能到 50，会把同镜头的好基线也误杀（proj79 48.5s 实测）。
    """
    sp = proj / "out" / "shots.json"
    if not sp.exists():
        return []
    try:
        shots = json.loads(sp.read_text()) or []
    except json.JSONDecodeError:
        return []
    starts, t = {}, 0.0
    for s in fin.get("segments") or []:
        if s.get("kind") == "overlay" or s.get("_overlay"):
            continue
        n = int(s.get("n") or 0)
        starts[n] = t
        d = (round(float(s["out"]) - float(s["in"]), 3)
             if s.get("kind") == "clip" else float(s.get("dur") or 0))
        t = round(t + d, 3)
    cuts = []
    for sh in shots:
        try:
            n = int(sh.get("seg"))
            out = float(sh["out"])
        except (KeyError, TypeError, ValueError):
            continue
        if n not in starts:
            continue
        ft = round(starts[n] + out, 3)
        if t - 0.04 > ft > 0.04:
            cuts.append(ft)
    return sorted(set(cuts))


def ink_diff_stats(film: Path, card: dict, style: dict, W: int, H: int, dur: float,
                   all_cards: list[dict] | None = None,
                   cuts: list[float] | None = None) -> dict:
    """量一张大字卡的墨迹（字形掩膜 × 卡前/卡后帧差）。

    返回 dict：
      level       green / red；量不出来（无掩膜 / 无干净基线）时**不含 level**，调用方走旧口径兜底
      ink_delta / ctl_delta / ratio      帧差中位与比值（判据主体）
      ink_std / ctl_std / occ_ratio      卡内三帧的时间标准差（静态叠加证据）
      alpha / color / base_color         反混合估计与实测典型色
    任何一项量不出来都如实写进 note，不假装通过。
    """
    import numpy as np
    a_, b_ = float(card["start"]), float(card["end"])
    mask, box_px, top_h = glyph_ink_mask(card, style, W, H)
    if mask is None:
        return {"error": "字形掩膜建不出来（字体文件缺失或文本为空）"}
    x, y, bw, bh = box_px
    box = (max(0, x - INK_PAD), max(0, y - INK_PAD),
           min(W, x + bw + INK_PAD), min(H, y + bh + INK_PAD))
    ink = _mask_morph(mask, INK_ERODE, grow=False)
    ctl = np.zeros_like(mask)
    ctl[box[1]:box[3], box[0]:box[2]] = True
    ctl &= ~_mask_morph(mask, INK_CTRL_GAP, grow=True)
    if ink.sum() < 50 or ctl.sum() < 500:
        return {"error": f"字形掩膜太小（ink {int(ink.sum())}px / control {int(ctl.sum())}px）"}

    t0 = max(0.0, a_ - 1.5)
    t1 = min(dur, b_ + 1.5)
    frames, t00 = _gray_window(film, t0, t1 - t0, W, H)
    if frames is None or frames.shape[0] < 3:
        return {"error": "取不到卡窗内的帧"}
    times = t00 + np.arange(frames.shape[0]) / INK_FPS

    def at(t):
        return int(np.argmin(np.abs(times - t)))

    # 取样避开入场/出场动画：0.85 落在卡尾 0.15s 处，实测 proj79「都有偏颇」在 16.15s
    # 已在位移动画里（同一掩膜的帧差中位 33）——那是字在动，不是背景在动，会把
    # ink σ 抬到 15。0.45/0.6/0.75 全部落在稳态。
    fracs = [f for f in (0.45, 0.6, 0.75)]
    idx_card = sorted({at(a_ + (b_ - a_) * f) for f in fracs})
    if len(idx_card) < 2:
        return {"error": "卡窗太短，取不到两帧"}

    # 干净基线：卡窗之外、不被任何别的卡占住、且中间没有切点（全帧中位帧差 < 26）。
    other = []
    for c2 in (all_cards or []):
        if c2 is card or c2 is None:
            continue
        try:
            other.append((float(c2["start"]), float(c2["end"])))
        except (KeyError, TypeError, ValueError):
            continue

    def blocked(t):
        return any(s0 - 0.08 <= t <= e0 + 0.08 for s0, e0 in other)

    def crosses_cut(t, edge):
        lo, hi = sorted((t, edge))
        return any(lo + 0.02 < c < hi - 0.02 for c in (cuts or []))

    mid = at((a_ + b_) / 2)
    cands = []
    for here, step in ((a_, -1), (b_, 1)):
        for k in range(1, 16):
            t = here + step * 0.1 * k
            if t < 0 or t > dur or t > times[-1] or blocked(t):
                continue
            if cuts and crosses_cut(t, here):
                continue        # 中间有一刀，这一对不是同一镜头
            i = at(t)
            if float(np.median(np.abs(frames[mid] - frames[i]))) >= INK_CUT_GUARD:
                continue        # shots.json 没覆盖到时的最后一道切点防线
            cands.append((abs(step * 0.1 * k), t, i))
    cands.sort()
    bases = []
    for _, bt, i in cands:
        if all(abs(bt - x[1]) > 0.05 for x in bases):
            bases.append((abs(bt - a_ if bt < a_ else bt - b_), bt, i))
        if len(bases) >= 2:
            break
    if not bases:
        return {"error": "卡前/卡后找不到干净基线帧（被别的卡占住或中间有切点）"}

    pairs = []
    for i0 in idx_card:
        for _, bt, i1 in bases:
            d = np.abs(frames[i0] - frames[i1])
            ik = float(np.median(d[ink]))
            ct = float(np.median(d[ctl]))
            pairs.append((ik, ct, ik / max(0.5, ct), bt))
    ink_delta = float(np.median([p[0] for p in pairs]))
    ctl_delta = float(np.median([p[1] for p in pairs]))
    ratio = float(np.median([p[2] for p in pairs]))

    occ_ratio = ink_std = ctl_std = None
    if len(idx_card) >= 2:
        stack = frames[idx_card]
        s = stack.std(axis=0)
        ink_std = float(np.median(s[ink]))
        ctl_std = float(np.median(s[ctl]))
        occ_ratio = ink_std / max(0.2, ctl_std)

    # 反混合：alpha ≈ (实测色 − 底片色) / (accent 色 − 底片色)，逐像素投影后取中位。
    color = base_color = None
    alpha = alpha_resid = None
    icol = int(np.median(idx_card))
    f_card = _rgb_frame(film, float(times[icol]), W, H)
    f_base = _rgb_frame(film, float(bases[0][1]), W, H)
    if f_card is not None and f_base is not None:
        p0 = f_card[ink]
        p1 = f_base[ink]
        color = tuple(int(v) for v in np.median(p0, axis=0))
        base_color = tuple(int(v) for v in np.median(p1, axis=0))
        emph = style.get("emphasis") or {}
        acc = (_hex_rgb(emph.get("keyword_color"))
               or _hex_rgb(((style.get("tokens") or {}).get("palette") or {}).get("accent")))
        if acc is not None:
            acc_v = np.array(acc, dtype="float32")
            den = acc_v - p1
            num = p0 - p1
            good = np.abs(den).max(axis=1) > 25
            if int(good.sum()) >= 30:
                al = (num[good] * den[good]).sum(axis=1) / (den[good] * den[good]).sum(axis=1)
                alpha = float(np.median(al))
                alpha_resid = float(np.median(np.abs(num[good] - alpha * den[good]).mean(axis=1)))
                # 实测色不在 accent 连线上（深红棕/青绿字压暖底）→ alpha 不可解释，别硬报一个数
                if alpha < -0.15 or alpha > 1.3 or (alpha_resid is not None and alpha_resid > 28):
                    alpha = None

    # 移动物体：ink 区域动得比 control 还厉害，而且 control 真的在动。
    # ground-01 29.6s 那颗人头就从字形位置穿过去（ink σ 40 / control σ 7.6），
    # 帧差比 3.5–5.7 与真卡重叠——只有这条把它挡下来。control 太静时这个比值
    # 没有信息量（分母接近噪声，'都有偏颇' 背景平得很，occ_ratio 4.7 是假的），不参与判定。
    motion_blob = (occ_ratio is not None and ctl_std is not None
                   and ctl_std >= INK_OCC_CTL_STD and occ_ratio >= INK_MOTION_BLOB)
    add_ok = (ratio >= INK_DIFF_K and ink_delta >= INK_DIFF_FLOOR and not motion_blob)
    occ_ok = (occ_ratio is not None and occ_ratio <= INK_OCC_K
              and (ctl_std or 0.0) >= INK_OCC_CTL_STD
              and (ink_std or 0.0) <= INK_OCC_INK_STD)
    level = "green" if (add_ok or occ_ok) else "red"

    bits = [f"字形帧差 ink {ink_delta:.1f} / control {ctl_delta:.1f} = {ratio:.2f}"]
    bits.append(f"地板 {INK_DIFF_FLOOR:g} × 比值线 {INK_DIFF_K:g}")
    if occ_ratio is not None:
        bits.append(f"静态叠加 ink/control σ {ink_std:.1f}/{ctl_std:.1f} = {occ_ratio:.2f}"
                    f"（线 {INK_OCC_K:g}）")
    if color is not None:
        bits.append(f"实测色 {color} / 底片 {base_color}")
    if alpha is not None:
        bits.append(f"估计 alpha {alpha:.2f}")
    elif color is not None:
        bits.append("alpha 不适用（实测色不在 accent 连线上）")
    return {"level": level, "ink_delta": ink_delta, "ctl_delta": ctl_delta,
            "ratio": ratio, "occ_ratio": occ_ratio, "ink_std": ink_std, "ctl_std": ctl_std,
            "alpha": alpha, "color": color, "base_color": base_color,
            "top_h": top_h, "box": box, "ink_px": int(ink.sum()),
            "add_ok": add_ok, "occ_ok": occ_ok,
            "note": " · ".join(bits)}


def subtitle_density(cues: list[dict], dur: float, lo, hi) -> tuple[str, str, str] | None:
    """字幕密度：实测与期望**都按整片分钟**算，跟参考片的 45–58 条/分钟同口径。

    为什么分母必须是「整片分钟」：参考片那个基准就是这么量的——113 条 ÷
    (126.58/60) 分 = 53.6 条/分钟（talking-head-packaging 的 measured-spec）。
    旧版期望值写的是 `cps/avg*60`，分母其实是**字幕覆盖**分钟；片子里有多少秒
    没字幕，就被硬当成「密度变了」报出来（T-032 把字幕窗口收窄到只盖说话的秒，
    ground-01 的覆盖率 79.2%，凭空多一条黄）。更糟的是 N 在两边约掉之后，
    那条判据量到的只剩「字幕覆盖率 C/T」：把条数砍掉 1/3 依旧 0 偏差。

    所以期望值不能拿本片自己的 avg 反推（那样 N 又会约掉），而要用**参考片的每条字数**
    当切法基准：同样的字量、按参考的切法，整片分钟该有多少条。这条判据量的是
    「你切得比参考碎还是粗」，两侧同一把尺。

    返回 (级别, 读数, 说明)；没有字幕时返回 None。
    """
    cues = [c for c in (cues or []) if (c.get("text") or "").strip()]
    if not cues or not dur:
        return None
    chars_n = sum(len(c["text"]) for c in cues)
    n = len(cues)
    per_min = n / (dur / 60)
    avg = chars_n / n
    # 语速只作读数：分母是字幕覆盖时长，跟片长无关，不参与判定
    cover = sum(float(c["end"]) - float(c["start"]) for c in cues)
    cps = chars_n / cover if cover > 0 else 0.0
    expect = (chars_n / REF_CHARS_PER_CUE) / (dur / 60)
    dev = abs(per_min - expect) / expect
    level = "green" if dev <= 0.15 else ("yellow" if dev <= 0.3 else "red")
    note = (f"整片分钟口径（参考片 {lo}–{hi} 同口径）；本片 {n} 条 ÷ {dur:.1f}s，"
            f"{chars_n} 字、语速 {cps:.1f} 字/秒、单条 {avg:.1f} 字 → "
            f"按参考每条 {REF_CHARS_PER_CUE:.0f} 字该是 {expect:.0f} 条/分钟")
    return level, f"{per_min:.1f} 条/分钟", note


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
    ap.add_argument("--library", default=None,
                    help="素材库根（字幕窗口里的真静音要用）；默认从祖先目录找 _cutskills/index.json")
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
    film = (proj / a.film) if (a.film and not Path(a.film).is_absolute()) else (
        Path(a.film).expanduser().resolve() if a.film else proj / "out" / "film-hf.mp4")
    if not a.film:
        cands = [x for x in (proj / "out" / "film-hf.mp4", proj / "out" / "film.mp4")
                 if x.exists()]
        if cands:
            film = max(cands, key=lambda x: x.stat().st_mtime)
    if not film.exists():
        print(f"没有成片：{film}（先跑 render_hf.py）", file=sys.stderr)
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
        picks = [c for c in cues0 if c["end"] - c["start"] > 0.8]
        # 被重卡挂起的字幕（B9）在这一屏本来就不该有字：拿它去判「字幕色在不在画面上」
        # 必然假红。按 subs.json 的 suppressed 窗口把它们排掉。
        _sup = subs0.get("suppressed")
        _supw = []
        if isinstance(_sup, list):
            for s_ in _sup:
                cu = (s_ or {}).get("cue") or {}
                if cu.get("start") is not None:
                    _supw.append((float(cu["start"]), float(cu["end"])))
        if _supw:
            picks = [c for c in picks
                     if not any(abs(float(c["start"]) - a0) <= 0.02
                                for a0, _b0 in _supw)]
        picks = picks[:40]
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
            px = _pixels(cim)
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

    if cards_meta:
        _all_cards = [x for v in cards_meta.values() for x in v]
        _cuts = film_cut_times(fin, proj)
        for c in _all_cards:
            a_, b_ = float(c["start"]), float(c["end"])
            if a_ >= dur:
                continue
            mid = min(dur - 0.05, (a_ + b_) / 2)
            im = frame_at(mid, "cardrgb", "RGB")
            if im is None:
                continue
            kind = str(c.get("kind") or "card")
            # 卡在哪里量：以**这张卡实际落在的框**为准（cards.json 的 layout.box_px，
            # 由 scripts/card_layout.py 算出来）。以前这里写死 (0.19H–0.62H) 那条带——
            # 卡一旦按「避脸」挪到下半屏，判据就会在旧位置上找不到字，把「卡没上去」误报。
            _box = None
            _lay = c.get("layout") or {}
            if _lay.get("box_px"):
                _bx, _by, _bw, _bh = _lay["box_px"]
                _m = 24
                _box = (max(0, _bx - _m), max(0, _by - _m),
                        min(W, _bx + _bw + _m), min(H, _by + _bh + _m))
            elif kind in ("keyword", "behind", "cutout"):
                # 大字以前没有自己的框，于是按「卡型固定在中段带」量。卡一旦按作者的
                # `card.top_h` 挪到头顶（T-045 的 dianzan-c mg-02：0.30H → 0.165H），
                # 固定带里就量不到字，报「大字烧进画面 0.00%」——假红，和 T-041 那条
                # behind 假红同一个病根。现在读渲器落盘的 `kw_box_px`（画字与记框是同一个
                # 函数），取样带从「三分之一屏」收成「这块字+24px」：这不是放宽，
                # 是把带子对齐到字真正在的地方。老项目没这个字段 → 仍是固定带（不退步）。
                _box = kw_box_px(c, W, H)
            if kind in ("keyword", "behind", "cutout"):
                # T-044：默认走字形掩膜 × 卡前/卡后帧差。旧的颜色占比只当兜底——
                # 掩膜建不出来（字体缺失）或卡前后没有干净基线时，才退回旧口径，
                # 并把「为什么退回」写进判据文案，不许静默换算法。
                _label = ("大字烧进画面（可见部分）" if kind in ("behind", "cutout")
                          else "大字烧进画面")
                _st, _err = None, ""
                try:
                    _st = ink_diff_stats(film, c, style, W, H, dur, _all_cards, cuts=_cuts)
                except Exception as _e:          # 判据自己坏了也要说出来，不许静默
                    _err = f"新判据跑不动：{str(_e)[:80]}"
                if _st and _st.get("level"):
                    add(_label, f"{mid:.1f}s 处{_st['note']}", _st["level"],
                        "" if _st["level"] == "green" else
                        f"大字「{c.get('text')}」在 {a_:.1f}–{b_:.1f}s：字形掩膜里量不到"
                        f"这张卡加的墨（{_st['note']}）")
                else:
                    if not _err and _st:
                        _err = str(_st.get("error") or "量不出来")
                    # ---- 旧口径兜底（颜色占比 + 净墨迹）----
                    # 只在上面量不出来时跑。它量不到半透明淡黄，但它不会把「没量到」
                    # 说成「没问题」：退回的这条判据在文案里写明原因。
                    _band = text_band(kind, _box, W, H)
                    share = 0.0
                    for frac in (0.35, 0.6, 0.85):
                        tt = min(dur - 0.05, a_ + (b_ - a_) * frac)
                        im_ = frame_at(tt, f"card{c.get('el')}{frac}", "RGB")
                        if im_ is None:
                            continue
                        share = max(share, kw_share(im_, _band))
                    if im_ is None:
                        continue
                    _bases = []
                    for _tt, _tag in ((a_ - 0.35, "pre"), (b_ + 0.35, "post")):
                        if not (0.0 <= _tt <= dur - 0.05):
                            continue
                        _im_b = frame_at(_tt, f"card{c.get('el')}{_tag}", "RGB")
                        if _im_b is not None:
                            _bases.append(kw_share(_im_b, _band))
                    _base = min(_bases) if _bases else None
                    level, ink = kw_ink_level(share, _base, kind)
                    floor = KW_FLOOR_BEHIND if kind in ("behind", "cutout") else KW_FLOOR_KEYWORD
                    label = "大字烧进画面（可见部分）" if floor < KW_FLOOR_KEYWORD else "大字烧进画面"
                    if _base is None:
                        _bt = ""
                    elif share > 0 and _base >= 0.5 * share:
                        _bt = (f"（卡不在时同带 {_base * 100:.2f}%——带子被画面同色占满，"
                               f"净墨迹不可判，按带内墨迹总量判）")
                    else:
                        _bt = f"（卡不在时同带 {_base * 100:.2f}%，净 {ink * 100:.2f}%）"
                    add(label,
                        f"{mid:.1f}s 处花字色占比 {share * 100:.2f}%{_bt}；"
                        f"退回旧口径：{_err or '字形掩膜量不出来'}",
                        level,
                        "" if level == "green" else
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
                    _box_mg = _box or (0, int(H * 0.19), W, int(H * 0.62))
                    if kind == "term":
                        share = max(share, color_share(im_, "#FFFFFF", _box_mg, tol=46))
                    else:
                        share = max(share, kw_share(im_, _box_mg))
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
                px = _pixels(im.convert("L").crop((0, 760, W, 1040)))
                dark = sum(1 for v_ in px if v_ < 80) / max(1, len(px))
                add("字卡烧进画面", f"{mid:.1f}s 处深色占比 {dark * 100:.1f}%",
                    "green" if dark > 0.12 else "red", "")
        cards_meta = {"_done": []}          # 下面按 EDL 的那段不再重复判

    # 叠加态（B7 图底主权 / B9 双通道去重）。这一项是 2026-11-13 第五轮审查的头号硬伤：
    # 卡压在主讲人鼻梁上、把脸压成黑板、把菜盘盖住，而 8 道脚本当时全绿——
    # 因为没有任何一道判据知道人脸在哪。现在由 check_overlay.py 重算（不采信渲染器写的数）。
    try:
        import check_overlay as _co
        _orows = _co.audit(proj)
        _oe = [m for lv, m in _orows if lv == "error"]
        _ow = [m for lv, m in _orows if lv == "warning"]
        _modes: dict[str, int] = {}
        for _c in (json.loads((proj / "out" / "cards.json").read_text()).get("cards") or []
                   if (proj / "out" / "cards.json").exists() else []):
            _md = str((_c.get("layout") or {}).get("mode") or "legacy")
            _modes[_md] = _modes.get(_md, 0) + 1
        _val = " · ".join(f"{k} {v}" for k, v in sorted(_modes.items())) or "无卡"
        if _oe:
            add("叠加态", _val, "red", _oe[0])
        elif _ow:
            add("叠加态", _val, "yellow", _ow[0])
        else:
            add("叠加态", _val, "green",
                "卡全避脸（IoU<2%）· 露脸格无整屏暗场 · 重卡均退字幕")
    except Exception as _e:                      # 判据本身坏了也要说出来
        add("叠加态", "判据跑不动", "yellow", str(_e)[:60])

    # EDL 路径（没有 out/cards.json 的老项目）也要知道「别的卡在哪」：基线帧不能被
    # 另一张卡占住，否则量到的是两张卡的叠加。先把所有段上的 card 折成成片时间。
    _edl_cards, _tacc = [], 0.0
    _cuts = film_cut_times(fin, proj)
    for _s in (fin.get("segments") or []):
        _dd = round(float(_s["out"]) - float(_s["in"]), 3)
        _cc = _s.get("card")
        if _cc:
            _cat = (float(_cc["at"]) if isinstance(_cc.get("at"), (int, float)) else 0.25)
            _cdur = float(_cc.get("dur") or max(1.2, _dd - 0.6))
            _edl_cards.append({**_cc, "start": _tacc + _cat, "end": _tacc + _cat + _cdur})
        _tacc += _dd

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
                    # T-044：与 cards.json 路径同一条规则（字形掩膜 × 帧差）。老项目没有
                    # kw_top_h，退到 `card_layout.kw_top_of`——与渲器同一条优先级。
                    _ecard = {**card, "start": t0 + at, "end": t0 + at + cd}
                    _st, _err = None, ""
                    try:
                        _st = ink_diff_stats(film, _ecard, style, W, H, dur, _edl_cards,
                                             cuts=_cuts)
                    except Exception as _e:
                        _err = f"新判据跑不动：{str(_e)[:80]}"
                    if _st and _st.get("level"):
                        add("大字烧进画面", f"{mid:.1f}s 处{_st['note']}", _st["level"],
                            "" if _st["level"] == "green" else
                            f"EDL 里有大字「{card.get('text')}」，字形掩膜里量不到卡加的墨"
                            f"（{_st['note']}）")
                        t0 += d
                        continue
                    if not _err and _st:
                        _err = str(_st.get("error") or "量不出来")
                    # ---- 旧口径兜底（颜色占比）----
                    _band = text_band(kind, None, W, H)
                    share = kw_share(im, _band)
                    _bases = []
                    for _tt, _tag in ((mid - cd / 2 - 0.35, "pre"), (mid + cd / 2 + 0.35, "post")):
                        if not (0.0 <= _tt <= dur - 0.05):
                            continue
                        _im_b = frame_at(_tt, f"cardE{_tag}", "RGB")
                        if _im_b is not None:
                            _bases.append(kw_share(_im_b, _band))
                    _base = min(_bases) if _bases else None
                    level, ink = kw_ink_level(share, _base, kind)
                    add("大字烧进画面",
                        f"{mid:.1f}s 处花字色占比 {share * 100:.2f}%；"
                        f"退回旧口径：{_err or '字形掩膜量不出来'}",
                        level,
                        "" if level == "green" else
                        f"EDL 里有大字「{card.get('text')}」，画面 {kw_hex} 色几乎为零")
                else:
                    px = _pixels(im.crop((0, 760, W, 1040)))
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
        # 分母一律「**整片**分钟」，实测侧和期望侧同一把尺——参考片的 45–58 条/分钟就是
        # 这么标的（113 条 ÷ (126.58/60) 分 = 53.6 条/分钟）。口径与判据一起看
        # subtitle_density() 的 docstring（T-041）。
        lo, hi = (style["subtitle"].get("density_per_min") or ["—", "—"])
        _d = subtitle_density(cues, dur, lo, hi)
        if _d:
            add("字幕密度", _d[1], _d[0], _d[2])
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
                from libs import resolve_lib as _res_lib
                _lex = _lx.load(_res_lib(proj, a.library) / "_cutskills")
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
        # 判据是字面扫描，不靠模型：词表里每一条「错写法」都不许出现在字幕和手写 evidence 里。
        try:
            import lexicon
            from libs import resolve_lib
            _lex = lexicon.load(resolve_lib(proj, a.library) / "_cutskills")
            _els = (edl.get("elements") or [])
            _ev_chk = [e.get("evidence") or "" for e in _els if e.get("evidence_source") != "asr"]
            _bad = lexicon.violations([c["text"] for c in cues] + _ev_chk, _lex)
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
            # 3.2s 是「赖在屏幕上」的上限，不是语速的上限：慢语速的
            # 一口气真能连着说 3.3–4.1 秒。超 3.2s 的条先**量**窗口里的真停顿，
            # 量不到 ≥0.30s 停顿（单呼吸组）才放到 4.5s（T-029，2026-11-13）。
            _probe = _cs.make_hold_probe(proj, a.library)
            _hb, _hn = _cs.hold_judge(_cues, probe=_probe)
            _single = [x for x in _hn if x.get("why") == "single"]
            if _hb:
                add("字幕停留", f"{len(_hb)}/{len(_cues)} 条越界"
                                f"（最短 {min(x['s'] for x in _hb):.2f}s）", "red",
                    "闪一下或赖在屏幕上：" + "、".join(x["text"] for x in _hb[:3]))
            else:
                _head = (f"另有 {len(_single)} 条单呼吸组到 {_cs.HOLD_MAX_SINGLE}s（窗口内无 "
                         f"≥{_cs.BREATH_MIN}s 停顿）" if _single else
                         f"另有 {len(_hn)} 条收在句读上的短句（可接受）" if _hn else "")
                add("字幕停留", f"{len(_cues)} 条全在 {_cs.HOLD_MIN}–{_cs.HOLD_MAX}s 内", "green", _head)
            # 量不出静音时不停默：超 3.2s 的条全部按严格上限判，但要让人知道是「没量成」
            if _probe is None and any(
                    float(c["end"]) - float(c["start"]) > _cs.HOLD_MAX + 0.02 for c in _cues):
                add("字幕停留静音探针", "没跑起来", "yellow",
                    "没 shots.json 或素材库不在本机——超 3.2s 的条按严格上限判，没有豁免")
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
