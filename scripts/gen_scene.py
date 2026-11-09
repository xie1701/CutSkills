#!/usr/bin/env python3
"""用代码画画面——不是把字卡换个样式，是让画面本身把话讲完。

为什么另起一条：现有 `gen` 模板（keyword / compare / number / steps / quote /
chapter / chart）本质都是**整屏图文卡**：一块底、几行字、进出动效。观众看到的是
"PPT 翻页"，叙事还是靠口播。Jerry 要的是另一种东西——画面自己会动、会变，
按台词的节奏把一件抽象的事演出来，口播只是旁白。

这里放的是**场景生成器**：每个场景是一个函数 `scene_xxx(t, W, H, ...) -> RGB 数组`，
输入只有「这一帧是第几秒」，输出就是这一帧的画面。没有素材、没有模板、没有
素材库依赖，全部是确定性的（同一秒永远画出同一帧，方便复现和对比）。

    python3 scripts/gen_scene.py --scene phase --out /tmp/phase.mp4 --dur 5

第一版只做 `phase`（相变：液态粒子在几秒内沉降成晶格）。它演的是熊工口播5 里
那段「涂料会发生相变，所以客户想象不到成品」——一句没有画面可拍的话。
"""
from __future__ import annotations

import argparse
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path.home() / "Library" / "Fonts"
# 这台机器上思源黑体是 .ttf 不是 .otf——写错后缀会静默落到 load_default()，
# 一个字只有几个像素亮，视觉复核只会说"标签看不清"，不会说"字体没加载"（踩过）。
FONT_BLACK = FONT_DIR / "NotoSansSC-Black.ttf"
FONT_BOLD = FONT_DIR / "NotoSansSC-Bold.ttf"
FONT_SEMI = FONT_DIR / "NotoSansSC-SemiBold.ttf"

# 画面配色跟 talking-head-v1 走同一套：深底、暖金、白字
BG_TOP = (14, 12, 11)
BG_BOT = (30, 24, 20)
LIQUID = (86, 118, 148)      # 液态：偏冷的蓝灰
SOLID = (246, 235, 52)       # 固态：品牌金


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    for p in (path, FONT_BLACK, FONT_BOLD, FONT_SEMI):
        if p and Path(p).exists():
            return ImageFont.truetype(str(Path(p)), size)
    return ImageFont.load_default()


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


# --------------------------------------------------------------- 场景：相变
def scene_phase(t: float, W: int, H: int, dur: float) -> Image.Image:
    """一滩漆 → 一张膜。

    不是「散点在中间，最后变成网格」——那样三个阶段的构图是断的（视觉复核原话：
    「像三个素材硬拼」）。真实发生的事是**同一摊东西换了形态**：面积几乎不变，
    从会晃的液面，变成不动的固体膜。所以起点和目标共用同一个footprint，
    变的只有三件：表面还在不在晃、粒子排不排得齐、颜色冷暖。

    三幕：晃（液面波纹）→ 收（波纹被抹平，粒子各自找位置）→ 定（晶格锁死，连线浮现）。
    """
    rng = np.random.default_rng(20261109)
    cols, rows = 40, 15
    n = cols * rows

    # 目标晶格：一块铺满画幅、高度约 0.44H 的"膜"
    gx, gy = np.meshgrid(np.arange(cols), np.arange(rows))
    g = np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float64)
    grid = np.empty_like(g)
    grid[:, 0] = 0.08 + (g[:, 0] / (cols - 1)) * 0.84
    grid[:, 1] = 0.30 + (g[:, 1] / (rows - 1)) * 0.44
    grid += rng.normal(0, 0.0035, grid.shape)

    # 液态起点：同一个 footprint，但纵向更自由、横向带波纹
    p0 = np.stack([
        rng.uniform(0.08, 0.92, n),
        rng.normal(0.52, 0.10, n),
    ], axis=1)

    k = ease((t - 1.0) / 2.0)           # 1.0s 起相变，2s 内完成
    ripple = (1 - k) * 0.030 * np.sin(p0[:, 0] * 11.0 - t * 3.2)
    wob = (1 - k) * 0.012 * np.stack([
        np.sin(t * 1.5 + np.arange(n) * 0.41),
        np.cos(t * 1.9 + np.arange(n) * 0.53),
    ], axis=1)
    pos = p0 * (1 - k) + grid * k + wob
    pos[:, 1] += ripple

    bg = np.zeros((H, W, 3), dtype=np.float32)
    ramp = np.linspace(0, 1, H)[:, None]
    for c in range(3):
        bg[..., c] = BG_TOP[c] * (1 - ramp) + BG_BOT[c] * ramp
    yy, xx = np.mgrid[0:H, 0:W]
    r2 = ((xx - W * 0.5) / W) ** 2 + ((yy - H * 0.52) / H) ** 2
    halo = np.exp(-r2 * 5.0) * (30 + 10 * math.sin(t * 1.7))
    tint = np.array([0.42, 0.62, 1.0]) * (1 - k) + np.array([1.0, 0.86, 0.28]) * k
    bg += halo[..., None] * tint[None, None, :]

    im = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im, "RGBA")

    col = tuple(int(LIQUID[i] * (1 - k) + SOLID[i] * k) for i in range(3))
    r_base = 6.2 - 2.4 * k
    for i in range(n):
        px, py = pos[i][0] * W, pos[i][1] * H
        r = r_base * (1 + 0.22 * math.sin(t * 6 + i))
        d.ellipse([px - r * 2.8, py - r * 2.8, px + r * 2.8, py + r * 2.8],
                  fill=(*col, int(30 + 16 * k)))
        d.ellipse([px - r, py - r, px + r, py + r],
                  fill=(*col, int(170 + 70 * k)))

    if k > 0.5:
        la = int(90 * ease((k - 0.5) / 0.5))
        for i in range(n):
            x, y = pos[i]
            for j in (i + 1, i + cols):
                if j >= n or (j == i + 1 and (i + 1) % cols == 0):
                    continue
                x2, y2 = pos[j]
                if abs(x - x2) * W < 60 and abs(y - y2) * H < 60:
                    d.line([x * W, y * H, x2 * W, y2 * H], fill=(*col, la), width=3)

    f_lab = font(FONT_BOLD, int(H * 0.032))
    if t < 1.5:
        a = int(255 * (1 - ease((t - 1.0) / 0.45)))
        _chip(d, W, H, "液相 · 会流动", 0.175, f_lab, a)
    if t > 2.4:
        a = int(255 * ease((t - 2.4) / 0.5))
        _chip(d, W, H, "固相 · 成了表面", 0.175, f_lab, a)
    return im


def _chip(d: ImageDraw.ImageDraw, W: int, H: int, text: str, y_frac: float,          f: ImageFont.FreeTypeFont, alpha: int) -> None:
    """标签配一块半透明底——纯文字压在画面上，观众根本分不清是字还是噪点。"""
    if alpha <= 2:
        return
    bb = d.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad_x, pad_y = int(H * 0.012), int(H * 0.008)
    x0 = (W - tw) / 2 - pad_x
    y0 = H * y_frac - th / 2 - pad_y
    d.rounded_rectangle([x0, y0, x0 + tw + pad_x * 2, y0 + th + pad_y * 2],
                        radius=int(H * 0.008), fill=(0, 0, 0, int(alpha * 0.45)))
    d.text(((W - tw) / 2 - bb[0], H * y_frac - th / 2 - bb[1]), text, font=f,
           fill=(255, 255, 255, alpha))


def _center_text(d: ImageDraw.ImageDraw, W: int, H: int, text: str, y_frac: float,
                 f: ImageFont.FreeTypeFont, fill: tuple) -> None:
    bb = d.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((W - tw) / 2 - bb[0], H * y_frac - th / 2 - bb[1]), text, font=f, fill=fill)


SCENES = {"phase": scene_phase}


# ---------------------------------------------------------------- 场景：原料 → 成品

def _backdrop(t: float, W: int, H: int, cy: float = 0.5, glow: float = 1.0) -> np.ndarray:
    """每一幕共用的底子。**不能每一幕另起一个底色**，否则看起来又像三段素材硬拼。"""
    bg = np.zeros((H, W, 3), dtype=np.float32)
    ramp = np.linspace(0, 1, H)[:, None]
    for c in range(3):
        bg[..., c] = BG_TOP[c] * (1 - ramp) + BG_BOT[c] * ramp
    yy, xx = np.mgrid[0:H, 0:W]
    r2 = ((xx - W * 0.5) / W) ** 2 + ((yy - H * cy) / H) ** 2
    halo = np.exp(-r2 * 4.4) * (50 + 14 * math.sin(t * 1.5)) * glow
    bg += halo[..., None] * np.array([1.0, 0.88, 0.46])[None, None, :]
    return np.clip(bg, 0, 255).astype(np.uint8)


def _mix(a, b, k: float):
    return tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def _text_at(d: ImageDraw.ImageDraw, W: int, H: int, text: str, xf: float, yf: float,
             f: ImageFont.FreeTypeFont, fill: tuple) -> None:
    """把字放在**指定位置**。`_center_text` 永远居中在 W/2——桶标、尺寸标注都要按自己的
    x 放，用错就会三个桶的名字叠在画面正中（2026-11-09 视觉复核抓到的）。"""
    bb = d.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((xf * W - tw / 2 - bb[0], yf * H - th / 2 - bb[1]), text, font=f, fill=fill)


def _plaster(d: ImageDraw.ImageDraw, W: int, H: int, box, alpha: int) -> None:
    """竖着的抹痕——墙是人一刀一刀抹出来的，横纹会被人读成地板。"""
    x0, y0, x1, y1 = box
    n = 26
    for j in range(n):
        x = x0 + (x1 - x0) * (j + 0.5) / n
        wob = math.sin(j * 2.7) * (x1 - x0) * 0.004
        d.line([x + wob, y0 + 4, x - wob, y1 - 4], fill=(0, 0, 0, alpha), width=2)


def _strand(d: ImageDraw.ImageDraw, a, b, k: float, w0: float, w1: float,
            col, alpha: int, t: float, speed: float = 1.5, beads: int = 3) -> None:
    """一股会流的液体：越往下越细（不是等宽的管子），中间有几颗亮点在往下走。

    等宽直线会被看成「管路连接图」——视觉复核原话。液体得有粗细和流动感。
    """
    if k <= 0:
        return
    ax, ay = a
    bx, by = b
    mx, my = ax + (bx - ax) * k, ay + (by - ay) * k
    steps = 30
    for j in range(steps):
        u0, u1 = j / steps, (j + 1) / steps
        if u1 > k:
            break
        wa = w0 + (w1 - w0) * u0
        wb = w0 + (w1 - w0) * u1
        p0 = (ax + (bx - ax) * u0, ay + (by - ay) * u0)
        p1 = (ax + (bx - ax) * u1, ay + (by - ay) * u1)
        d.polygon([p0[0] - wa / 2, p0[1], p0[0] + wa / 2, p0[1],
                   p1[0] + wb / 2, p1[1], p1[0] - wb / 2, p1[1]], fill=(*col, alpha))
    # 外发光：液体在暗底上会有一圈晕，没有这层就只是一根等宽管子（视觉复核原话）
    for j in range(steps):
        u0, u1 = j / steps, (j + 1) / steps
        if u1 > k:
            break
        wa = (w0 + (w1 - w0) * u0) * 2.3
        wb = (w0 + (w1 - w0) * u1) * 2.3
        p0 = (ax + (bx - ax) * u0, ay + (by - ay) * u0)
        p1 = (ax + (bx - ax) * u1, ay + (by - ay) * u1)
        d.polygon([p0[0] - wa / 2, p0[1], p0[0] + wa / 2, p0[1],
                   p1[0] + wb / 2, p1[1], p1[0] - wb / 2, p1[1]], fill=(*col, 34))
    # 亮芯：随流动轻微涨落，读成「在流」而不是「接上了」
    pulse = 0.72 + 0.28 * math.sin(t * 5.4)
    for j in range(steps):
        u0, u1 = j / steps, (j + 1) / steps
        if u1 > k:
            break
        wa = (w0 + (w1 - w0) * u0) * 0.42 * pulse
        p0 = (ax + (bx - ax) * u0, ay + (by - ay) * u0)
        p1 = (ax + (bx - ax) * u1, ay + (by - ay) * u1)
        d.line([p0[0], p0[1], p1[0], p1[1]], fill=(255, 252, 235, 120), width=max(2, int(wa)))


def scene_pour(t: float, W: int, H: int, dur: float) -> Image.Image:
    """三个桶 → 一股 → 一面墙。

    演的是「我们不生产涂料：漆、色浆、助剂是原料；原料汇成一股，灌进去，才成为一面墙」。
    所以三幕必须是**同一件东西在变**：三个桶一直在原地，墙从一开始就在那儿等，
    最后被灌满——不能画成三张图依次切换。

    三幕：流（三股各自下落）→ 合（汇成一股）→ 铺（落点向两边扩成面）。
    """
    im = Image.fromarray(_backdrop(t, W, H, cy=0.42, glow=1.25), "RGB")
    d = ImageDraw.Draw(im, "RGBA")

    wx0, wx1 = W * 0.12, W * 0.88
    wy0, wy1 = H * 0.505, H * 0.845
    # 墙先在：暗轮廓 + 抹痕 + 一条竖向侧光，说明它在等；被灌满才亮
    d.rectangle([wx0, wy0, wx1, wy1], fill=(34, 29, 24, 165), outline=(238, 230, 210, 75), width=3)
    d.line([wx0 + (wx1 - wx0) * 0.18, wy0 + 6, wx0 + (wx1 - wx0) * 0.18, wy1 - 6],
           fill=(255, 246, 220, 42), width=10)
    _plaster(d, W, H, (wx0, wy0, wx1, wy1), 40)

    buckets = [("漆", 0.255), ("色浆", 0.5), ("助剂", 0.745)]
    bw, bh = W * 0.155, H * 0.125
    by = H * 0.155
    join = (W * 0.5, H * 0.425)
    f_lab = font(FONT_BOLD, int(H * 0.026))
    liquid = _mix(LIQUID, SOLID, 0.22)

    # 节拍按片长算，不写死秒数：写死的话 3 秒版在结尾只填了墙的一条边（2026-11-09 实测）
    k_out0, k_join_t, k_fill_t = dur * 0.12, dur * 0.36, dur * 0.52
    k_join = ease((t - k_join_t) / (dur * 0.18))
    k_fill = ease((t - k_fill_t) / (dur * 0.42))

    for i, (name, cx) in enumerate(buckets):
        x0 = cx * W - bw / 2
        d.rounded_rectangle([x0, by, x0 + bw, by + bh], radius=int(bw * 0.14),
                            outline=(230, 220, 198, 135), width=3)
        lvl = by + bh * (0.34 + 0.10 * math.sin(t * 1.7 + i * 1.1))
        d.rectangle([x0 + 4, lvl, x0 + bw - 4, by + bh - 4], fill=(*liquid, 100))
        d.line([x0 + 4, lvl, x0 + bw - 4, lvl], fill=(255, 255, 255, 90), width=2)
        # 名字放桶**上方**：放下面会被液流穿过去（视觉复核：读着累）
        _text_at(d, W, H, name, cx, (by - H * 0.032) / H, f_lab, (242, 235, 216, 220))

        k = ease((t - k_out0 - i * dur * 0.03) / (dur * 0.30))
        if k <= 0:
            continue
        # 每股都**落进同一个点**（不是各自停在旁边），粗细从 26px 收到 15px
        _strand(d, (cx * W, by + bh - 2), join, k, W * 0.024, W * 0.014, liquid, 205,
                t, speed=1.6 + i * 0.2)

    if k_join > 0:
        x, y0 = join
        y1 = wy0 + (wy1 - wy0) * 0.10
        _strand(d, (x, y0), (x, y1), k_join, W * 0.020, W * 0.017, _mix(LIQUID, SOLID, 0.5),
                225, t, speed=2.2, beads=2)
        # 灌入点：液体碰到墙的那一刻亮一下
        if k_join > 0.85:
            a = int(210 * ease((k_join - 0.85) / 0.15) * (1 - 0.35 * k_fill))
            d.ellipse([x - W * 0.05, y1 - W * 0.05, x + W * 0.05, y1 + W * 0.05],
                      fill=(*SOLID, max(0, int(a * 0.35))))
            d.ellipse([x - W * 0.018, y1 - W * 0.018, x + W * 0.018, y1 + W * 0.018],
                      fill=(255, 250, 214, a))

    if k_fill > 0:
        half = (wx1 - wx0) / 2 * k_fill
        fy = wy0 + (wy1 - wy0) * 0.10
        d.rectangle([W * 0.5 - half, fy, W * 0.5 + half, wy1],
                    fill=(*_mix(SOLID, LIQUID, 0.18), 224))
        if k_fill > 0.30:
            _plaster(d, W, H, (W * 0.5 - half, fy, W * 0.5 + half, wy1),
                     int(52 * ease((k_fill - 0.3) / 0.7)))
        if k_fill > 0.55:
            _chip(d, W, H, "做成的一面", (fy + H * 0.055) / H, f_lab,
                  int(255 * ease((k_fill - 0.55) / 0.45)))

    if t < dur * 0.42:
        _chip(d, W, H, "原料", 0.075, f_lab,
              int(230 * (1 - ease((t - dur * 0.20) / (dur * 0.18)))))
    if t > dur * 0.70:
        _chip(d, W, H, "原料 → 成品", 0.075, f_lab,
              int(235 * ease((t - dur * 0.70) / (dur * 0.18))))
    return im


# ---------------------------------------------------------------- 场景：量一遍

def scene_measure(t: float, W: int, H: int, dur: float) -> Image.Image:
    """同一块表面变三次：墙面 → 地面 → 任何表面，每变一次量一遍，最后成为「成品」。

    前两版都错了，错法不同但同一个病根：
    · 第一版把三组画成「宽 / 高 / 底边」，视觉复核一句话否掉——三根线量的是同一个边长；
    · 第二版画成三块面叠起来，复核说「单一体积堆着三条线，分不清」。
    口播的原话是「墙面，**或者**地面，**或者**任何表面」——是**alternatives**，不是同时。
    所以正确形状是同一块面在原地变：面积、朝向、比例都变，但一直是那一个东西在等量。
    """
    im = Image.fromarray(_backdrop(t, W, H, cy=0.47, glow=1.18), "RGB")
    d = ImageDraw.Draw(im, "RGBA")

    # 三态：墙（竖）、地（横）、台面（小）。角点顺序都是 左上/右上/右下/左下
    POSES = [
        [(0.26, 0.255), (0.74, 0.255), (0.74, 0.595), (0.26, 0.595)],
        [(0.11, 0.430), (0.89, 0.430), (0.95, 0.585), (0.05, 0.585)],
        [(0.25, 0.440), (0.75, 0.440), (0.80, 0.560), (0.20, 0.560)],
    ]
    LABELS = ["墙面", "地面", "任何表面"]

    # 三个节拍：0.45–1.05 / 1.05–1.75 / 1.75–2.45，之后定型
    k_in = ease(t / (dur * 0.16))
    beats = (dur * 0.14, dur * 0.33, dur * 0.55)
    seg = 0
    for idx, start in enumerate(beats):
        if t >= start:
            seg = idx
    prev = max(0, seg - 1)
    k = ease((t - beats[seg]) / (dur * 0.17)) if t > beats[0] else 0.0

    if t <= beats[0]:
        quad = POSES[0]
        hi = ease(t / beats[0])
    else:
        quad = [(POSES[prev][n][0] * (1 - k) + POSES[seg][n][0] * k,
                 POSES[prev][n][1] * (1 - k) + POSES[seg][n][1] * k) for n in range(4)]
        hi = 1.0 if t < dur * 0.78 else max(0.0, 1 - ease((t - dur * 0.78) / (dur * 0.1))) * 0.6

    # 收尾：这块面长回到「一面做好的表面」并整块变金。不收的话结尾是画面最小的一帧。
    FINAL = [(0.18, 0.300), (0.82, 0.300), (0.86, 0.620), (0.14, 0.620)]
    k_end = ease((t - dur * 0.78) / (dur * 0.16))
    if k_end > 0:
        quad = [(quad[n][0] * (1 - k_end) + FINAL[n][0] * k_end,
                 quad[n][1] * (1 - k_end) + FINAL[n][1] * k_end) for n in range(4)]
    pts = [(x * W, y * H) for x, y in quad]
    d.polygon(pts, fill=(*_mix(SOLID, BG_TOP, 0.80), int(200 * k_in)))
    d.polygon(pts, fill=(*SOLID, int(44 * hin if (hin := hi) else 0)))
    d.line(pts + [pts[0]], fill=(238, 230, 210, int(180 * k_in)), width=4)
    # 表面的抹痕：永远是竖的（墙是竖着抹出来的），跟着这块面一起变
    for n in range(1, 12):
        u = n / 12
        ax_, ay_ = pts[0][0] + (pts[1][0] - pts[0][0]) * u, pts[0][1] + (pts[1][1] - pts[0][1]) * u
        bx_, by_ = pts[3][0] + (pts[2][0] - pts[3][0]) * u, pts[3][1] + (pts[2][1] - pts[3][1]) * u
        d.line([ax_, ay_ + 3, bx_, by_ - 3], fill=(0, 0, 0, int(46 * k_in)), width=2)

    f_lab = font(FONT_BOLD, int(H * 0.023))
    f_num = font(FONT_BLACK, int(H * 0.031))

    def dim(a, b, kk, label, off):
        if kk <= 0.08:
            return
        ax, ay = a
        bx, by_ = b
        mx, my = ax + (bx - ax) * kk, ay + (by_ - ay) * kk
        d.line([ax, ay, mx, my], fill=(*SOLID, 215), width=4)
        for cx, cy in ((ax, ay), (mx, my)):
            d.line([cx - 11, cy - 11, cx + 11, cy + 11], fill=(*SOLID, 215), width=4)
        if kk > 0.8:
            a_ = int(255 * ease((kk - 0.8) / 0.2))
            _text_at(d, W, H, label, ((ax + bx) / 2 + off[0]) / W,
                     ((ay + by_) / 2 + off[1]) / H, f_lab, (*SOLID, a_))

    # 量的是「这一形态自己的一对边」：墙量上边，地量下边，台面量下边
    if t > beats[0] + dur * 0.03:
        kk = ease((t - beats[0] - dur * 0.03) / (dur * 0.13)) * \
            max(0.0, min(1.0, (beats[1] - t) / (dur * 0.05) + 0.15))
        dim((pts[0][0], pts[0][1] - H * 0.048), (pts[1][0], pts[1][1] - H * 0.048),
            min(1.0, kk), LABELS[0], (0, -H * 0.028))
    if beats[1] + dur * 0.03 < t < dur * 0.78:
        kk = ease((t - beats[1] - dur * 0.03) / (dur * 0.12)) * \
            (1 if t < dur * 0.72 else max(0.0, 1 - (t - dur * 0.72) / (dur * 0.06)))
        dim((pts[3][0] - W * 0.015, pts[3][1] + H * 0.042),
            (pts[2][0] + W * 0.015, pts[2][1] + H * 0.042), kk, LABELS[1], (0, H * 0.026))
    if beats[2] + dur * 0.03 < t <= dur * 0.78:
        kk = ease((t - beats[2] - dur * 0.03) / (dur * 0.10))
        dim((pts[3][0] - W * 0.010, pts[3][1] + H * 0.098),
            (pts[2][0] + W * 0.010, pts[2][1] + H * 0.098), kk, LABELS[2], (0, H * 0.026))

    if k_end > 0:
        # 收在「这块面自己就是成品」：整块变金，字压在金上。第一版收在一个黑框里，
        # probe 量到结尾亮像素只剩 0.6%——最该像成品的一帧反而最空。
        d.polygon(pts, fill=(*SOLID, int(238 * k_end)))
        _plaster(d, W, H, (min(p[0] for p in pts), min(p[1] for p in pts),
                           max(p[0] for p in pts), max(p[1] for p in pts)),
                 int(40 * k_end))
        cy = (pts[0][1] + pts[3][1]) / 2
        _text_at(d, W, H, "成品", 0.5, cy / H, f_num, (*BG_TOP, int(255 * k_end)))
    else:
        _chip(d, W, H, "做成成品", 0.085, f_lab, int(210 * ease((t - dur * 0.05) / (dur * 0.13))))
    return im


SCENES = {"phase": scene_phase, "pour": scene_pour, "measure": scene_measure}


def render(scene: str, out: Path, dur: float, fps: int, W: int, H: int) -> Path:
    fn = SCENES[scene]
    tmp = Path(tempfile.mkdtemp(prefix="scene-"))
    frames = int(round(dur * fps))
    for i in range(frames):
        fn(i / fps, W, H, dur).save(tmp / f"f{i:04d}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                        "-i", str(tmp / "f%04d.png"), "-c:v", "libx264", "-crf", "17",
                        "-pix_fmt", "yuv420p", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr[-400:])
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="phase", choices=sorted(SCENES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--dur", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=30, help="跟成片一致（talking-head-v1 是 30）")
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--probe", action="store_true",
                    help="只量亮度剖面，不出片：每个场景都要「越演越有东西」，"
                         "结尾比中间空就是节拍写错了（视觉模型判不了这个）")
    a = ap.parse_args()
    W, H = (int(x) for x in a.size.lower().split("x"))
    if a.probe:
        # 判据只有两条，都是「真看出来过」的病：
        #   ① 结尾比全片最满的时候少了 40% 以上（收尾收空了）
        #   ② 结尾亮度低于 5%（几乎全黑）
        # 中间的起伏不判死：墙面→台面本来就会变小，那不是错。
        fn = SCENES[a.scene]
        rows, bright = [], []
        for i in range(6):
            t = round(a.dur * i / 5, 2)
            arr = np.asarray(fn(min(t, a.dur - 1e-3), W, H, a.dur).convert("L"))
            b = float((arr > 60).mean()) * 100
            rows.append((t, float(arr.mean()), b))
            bright.append(b)
        peak = max(bright)
        for t, lu, b in rows:
            mark = " ←" if b == peak else ""
            print(f"  t={t:5.2f}s  luma {lu:5.1f}  bright {b:5.2f}%{mark}")
        if bright[-1] < 5.0:
            print(f"⚠ 结尾几乎全黑（{bright[-1]:.1f}%）——收尾没落下东西")
            return 1
        if bright[-1] < peak * 0.6:
            print(f"⚠ 结尾比最满的一帧空了 {100 - bright[-1] / peak * 100:.0f}%"
                  f"（{bright[-1]:.1f}% vs {peak:.1f}%）——收尾收空了")
            return 1
        print(f"🟢 {a.scene} 收尾有东西（结尾 {bright[-1]:.1f}% / 峰值 {peak:.1f}%）")
        return 0
    out = render(a.scene, Path(a.out).expanduser(), a.dur, a.fps, W, H)
    print(f"→ {out}（{a.scene} · {a.dur}s · {W}×{H} @{a.fps}fps）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
