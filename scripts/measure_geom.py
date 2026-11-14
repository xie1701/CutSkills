#!/usr/bin/env python3
"""几何靠量，不靠视觉模型描述。

为什么要有它：2026-11-14 那张天平卡，视觉模型三次都把它描述成「托盘挂在横杆下方」，
而像素里码垛（y383–458）明明在横杆（y592）**上方**——整个天平是倒着画的。
它读的是「天平应该长什么样」的常识，不是画面。此后几何一律走这里：

  --what gold    把画面里同一族的实体按颜色分开，报「几个实体、各在哪一行」
  --what order   直接回答「A 在 B 上还是下」（判倒画、判吊挂关系）
  --what shift   量一条水平实体的斜率（判倾斜→回平到底有没有发生）

做法是「颜色分离 → 按行找连续区间 → 按区间长度/数量认出实体」，
每条都只输出布尔与计数，不输出形容词。

用法：
    python3 scripts/measure_geom.py --frame <帧.png> --what gold,order,shift
    python3 scripts/measure_geom.py --frame <帧.png> --what shift --band 380:470

退出码：0 = 量到了；1 = 量不到（没有命中颜色的实体）；2 = 参数错。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# 金色族（横杆 + 砝码 + 强调色文字共用同一族渐变）
RULES = {
    "gold": lambda a: (a[:, :, 0] > 140) & (a[:, :, 1] > 105)
                       & (a[:, :, 2] < 115) & (a[:, :, 0].astype(int) - a[:, :, 2] > 55),
    "dark": lambda a: (a[:, :, 0] < 90) & (a[:, :, 1] < 85) & (a[:, :, 2] < 80),
    "white": lambda a: (a.min(axis=2) > 195),
}

MIN_RUN = 18          # 一行里短于这个宽度的区间当噪点（文字笔画也会被滤掉）
MIN_BAND = 3          # 一个实体至少占几行
BEAM_RATIO = 0.30     # 某行最长连续区间 ≥ 画面宽 × 这个比例 → 认成「一条横的东西」


def runs(mask_row: np.ndarray) -> list[tuple[int, int]]:
    """一行的一维连通区间 [(x0, x1_inclusive), ...]。"""
    idx = np.where(mask_row)[0]
    if len(idx) == 0:
        return []
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i > p + 2:                     # 允许 2px 断续（抗锯齿）
            out.append((int(s), int(p)))
            s = i
        p = i
    out.append((int(s), int(p)))
    return [(a, b) for a, b in out if b - a + 1 >= MIN_RUN]


def bands(mask: np.ndarray) -> list[dict]:
    """把「有实体的行」聚成一段段实体带，每带记录行范围与逐行区间。"""
    rows = [y for y in range(mask.shape[0]) if runs(mask[y])]
    out, cur = [], []
    for y in rows:
        if cur and y > cur[-1] + 2:
            out.append(cur)
            cur = []
        cur.append(y)
    if cur:
        out.append(cur)
    res = []
    for grp in out:
        if len(grp) < MIN_BAND:
            continue
        per = {y: runs(mask[y]) for y in grp}
        widest = max(((y, r) for y in grp for r in per[y]), key=lambda t: t[1][1] - t[1][0])
        widths = [w for y in grp for (x0, x1) in per[y] for w in [x1 - x0 + 1]]
        res.append({
            "y0": grp[0], "y1": grp[-1], "rows": len(grp),
            "max_run": widest[1][1] - widest[1][0] + 1, "max_run_y": widest[0],
            "x_span": max((x1 for y in grp for (x0, x1) in per[y]), default=0)
                      - min((x0 for y in grp for (x0, x1) in per[y]), default=0),
            "clusters_median": int(np.median([len(per[y]) for y in grp])),
            "w_med": float(np.median(widths)) if widths else 0.0,
            "w_cv": float(np.std(widths) / np.mean(widths)) if widths else 9.9,
            "per": per,
        })
    return res


def is_stack(b: dict) -> bool:
    """码垛：两块以上并排、每块宽度像「一块东西」（30–150px）、且宽度齐整。

    文字也会在行内留下好几簇，但字的宽度参差不齐（CV 大）——这条把它们筛掉，
    否则「差 4–5 倍」会被当成一垛砝码（第一版就这么误报了 1 垛）。
    """
    return (b["clusters_median"] >= 2 and 30 <= b["w_med"] <= 150 and b["w_cv"] < 0.35)


def report_gold(a: np.ndarray) -> int:
    mask = RULES["gold"](a)
    if not mask.any():
        print("  （画面里没有金色族实体）")
        return 1
    bs = bands(mask)
    W = mask.shape[1]
    print(f"  金色族实体带 {len(bs)} 段（画面 {mask.shape[1]}×{mask.shape[0]}）")
    for b in bs:
        kind = "横条" if b["max_run"] >= W * BEAM_RATIO else ("码垛" if is_stack(b) else "小块")
        print(f"    y {b['y0']:>4}–{b['y1']:<4} 高 {b['rows']:>3} 行  最长区间 {b['max_run']:>4}px"
              f"  行内区间数 中位 {b['clusters_median']}  块宽 {b['w_med']:.0f}px/CV {b['w_cv']:.2f}"
              f"  → {kind}")
    return 0


def _beam_and_stacks(a: np.ndarray):
    mask = RULES["gold"](a)
    W = mask.shape[1]
    bs = bands(mask)
    # 横条两种形态都要认：水平时是「一行里很长的一条」，倾斜时单行的连续区间
    # 会被斜率切成 W·2H/tanθ 那么短（7° 只剩 200px，20%W）——所以补一条
    # 「整体横向跨度够宽 + 竖向不够高」的口子，否则倾斜帧永远认不出杆。
    beam = next((b for b in bs if b["max_run"] >= W * BEAM_RATIO), None)
    if beam is None:
        cand = [b for b in bs if b["x_span"] >= W * 0.45 and b["rows"] <= 90
                and not is_stack(b)]
        beam = max(cand, key=lambda b: b["x_span"]) if cand else None
    stacks = [b for b in bs if b is not beam and is_stack(b)]
    return mask, bs, beam, stacks


def _bar_rows(mask: np.ndarray, beam: dict) -> list[int]:
    """横条自己的行：这一行里存在「够长的一条连续区间」。三角支架、码垛都被排掉。
    倾斜时单行都不够长 → 退回整段（那段本来就只有杆自己）。"""
    W = mask.shape[1]
    rows = []
    for y in range(beam["y0"], beam["y1"] + 1):
        rr = runs(mask[y])
        if rr and max(b - a for a, b in rr) >= W * BEAM_RATIO:
            rows.append(y)
    return (rows or list(range(beam["y0"], beam["y1"] + 1))), W


def report_order(a: np.ndarray) -> int:
    mask, bs, beam, stacks = _beam_and_stacks(a)
    if beam is None:
        print("  🔴 没认出横条（最长连续区间不足画面宽 %.0f%%）" % (BEAM_RATIO * 100))
        return 1
    rows, _ = _bar_rows(mask, beam)
    beam_y = float(np.median(rows))
    if not stacks:
        print(f"  横条在 y {beam_y:.0f}；没认出码垛，上下关系无从判")
        return 1
    ys = [(s["y0"] + s["y1"]) / 2 for s in stacks]
    print(f"  横条 y {beam_y:.0f}（{len(rows)} 行） · 码垛 {len(stacks)} 垛 中位 y {np.median(ys):.0f}")
    # 称的物理关系：杆在上，盘与盘里的东西在杆下方。反过来就是倒着画的。
    ok = np.median(ys) > beam_y
    print(f"  {'✅' if ok else '🔴'} 码垛在横条{'下方' if ok else '上方'}（判据：必须在本体下方）")
    return 0 if ok else 1


def report_shift(a: np.ndarray, band: tuple[int, int] | None) -> int:
    mask, _, beam, _ = _beam_and_stacks(a)
    if beam is None:
        if band is None:
            print("  🔴 没认出横条，给 --band y0:y1 指定量哪一条")
            return 1
        rows = list(range(band[0], band[1] + 1))
    else:
        rows, _ = _bar_rows(mask, beam)
        if band is not None:
            rows = [y for y in rows if band[0] <= y <= band[1]] or rows
    sub_mask = mask.copy()
    keep = np.zeros(mask.shape[0], bool)
    keep[rows] = True
    sub = sub_mask & keep[:, None]
    cols = np.where(sub.any(0))[0]
    if len(cols) < 20:
        print("  🔴 指定带里没有命中颜色的像素")
        return 1
    x0, x1 = int(cols.min()), int(cols.max())
    # 每 40px 一桶取墨迹中心 y，拟合左右端高度差
    buckets = np.linspace(x0, x1, 41).astype(int)
    pts = []
    for i in range(len(buckets) - 1):
        seg = sub[:, buckets[i]:buckets[i + 1]]
        yy = np.where(seg.any(1))[0]
        if len(yy) > 0:
            pts.append((buckets[i] + 8, float(np.median(yy))))
    if len(pts) < 4:
        print("  🔴 采样点不足，量不出斜率")
        return 1
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    slope = float(np.polyfit(xs, ys, 1)[0])
    dy = float(ys[-1] - ys[0])
    deg = float(np.degrees(np.arctan2(dy, x1 - x0)))
    print(f"  横条行 y {min(rows)}–{max(rows)} · x {x0}–{x1} · {len(pts)} 采样点")
    print(f"  左端 {ys[0]:.1f} → 右端 {ys[-1]:.1f}  高差 {dy:+.1f}px  斜率 {slope:+.4f}"
          f"  倾角 {deg:+.2f}°")
    verdict = "向右下倾" if dy > 6 else ("向右上倾" if dy < -6 else "水平")
    print(f"  → {verdict}")
    return 0


def report_ends(a: np.ndarray, spec: str, yrange: tuple[int, int] | None) -> int:
    """两端的竖直位置：倾斜到底发生没发生，看两端高差最直接。

    `--ends 200,880` 给两个 x 中心（各取 ±30px 的窗口），报每端命中的金色像素
    在 y 区间内的上下沿。杆倾斜时两端高差就是杆端位移；水平时两数应几乎相等。
    为什么不用斜率拟合：倾斜时单行连续区间被斜率切成窄条，按行找「一条长杆」会失手；
    两端位置则不依赖行连续性。
    """
    mask = RULES["gold"](a)
    xs = [int(v) for v in spec.split(",")]
    if yrange:
        y0, y1 = yrange
        mask = mask.copy()
        mask[:y0] = False
        mask[y1:] = False
    tops, meds = [], []
    for cx in xs:
        sub = mask[:, max(0, cx - 30): cx + 30]
        yy = np.where(sub.any(1))[0]
        if len(yy) == 0:
            print(f"  🔴 x={cx} 的窗口里没有金色像素（y 区间给对了吗：--yrange y0:y1）")
            return 1
        tops.append(float(yy.min()))
        meds.append(float(np.median(yy)))
        print(f"  x≈{cx}：y {yy.min()}–{yy.max()}（顶端 {yy.min()} · 中位 {np.median(yy):.0f}）")
    # 判上下看**顶端**：杆是一根细条，一端的上沿就是那一端的高度。
    # 中位数会被窗口里其他金色（码垛、强调色文字）拽走，第一版就是这样量反了方向。
    dy = tops[-1] - tops[0]
    print(f"  两端顶端高差 {dy:+.1f}px（{'右高（左端沉）' if dy < 0 else '右低（左端抬）'}）")
    if abs(meds[-1] - meds[0]) > 40 and abs(dy) < 20:
        print("  ⚠️ 中位差与顶端差矛盾：窗口里有别的金色实体混进来，缩小 --yrange 或换更靠边的 x")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="帧上的几何测量：实体带 / 上下关系 / 斜率")
    ap.add_argument("--frame", required=True)
    ap.add_argument("--what", default="gold,order,shift")
    ap.add_argument("--band", default=None, help="只量这条 y 带：y0:y1")
    ap.add_argument("--ends", default=None, help="量两端位置：两个 x 中心，如 200,880")
    ap.add_argument("--yrange", default=None, help="两端模式的 y 限制：y0:y1")
    ap.add_argument("--crop", default=None, help="只分析这块：x0,y0,x1,y1")
    a = ap.parse_args()

    p = Path(a.frame).expanduser()
    if not p.exists():
        print(f"找不到帧：{p}")
        return 2
    im = Image.open(p).convert("RGB")
    arr = np.asarray(im)
    if a.crop:
        x0, y0, x1, y1 = [int(v) for v in a.crop.split(",")]
        arr = arr[y0:y1, x0:x1]
    band = None
    if a.band:
        band = tuple(int(v) for v in a.band.split(":"))
    yrange = tuple(int(v) for v in a.yrange.split(":")) if a.yrange else None

    print(f"帧：{p.name} · {im.size[0]}×{im.size[1]}")
    bad = 0
    for what in [w.strip() for w in a.what.split(",") if w.strip()]:
        if what == "gold":
            bad += 1 if report_gold(arr) else 0
        elif what == "order":
            bad += 0 if report_order(arr) == 0 else 1
        elif what == "shift":
            bad += 0 if report_shift(arr, band) == 0 else 1
        elif what == "ends":
            if not a.ends:
                print("  ends 需要 --ends x1,x2")
                return 2
            bad += 0 if report_ends(arr, a.ends, yrange) == 0 else 1
        else:
            print(f"不认识的检查项：{what}（有 gold / order / shift）")
            return 2
    print("结论：" + ("✅ 量到了，且关系都成立" if bad == 0 else f"🔴 {bad} 项不成立"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
