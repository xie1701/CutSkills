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
import sys
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


# 节拍由谁定：**词**。脚本自己猜的比例（0.14 / 0.33 / 0.55）看着匀，但画面
# 换形态的那一帧跟台词对不上——观众听到「地面」时形状早就变了。
# 所以 `--marks` 把「第几秒变」换成了「念到那个词的第几成」：由调用方从 ASR
# 字级时间戳算出比例再传进来。空着才用默认比例。
MARKS: list[float] = []


def marks(default: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(MARKS) if len(MARKS) >= len(default) else default


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


def _plaster(d: ImageDraw.ImageDraw, W: int, H: int, box, alpha: int,
             prog: float = 1.0) -> None:
    """竖着的抹痕——墙是人一刀一刀抹出来的，横纹会被人读成地板。

    prog：抹到几成。一次全出来等于「已经抹好了」，画面就少了一半的意思——
    上墙这件事的本体是**一刀一刀覆盖过去**。
    """
    x0, y0, x1, y1 = box
    n = 26
    n = max(1, int(round(n * min(1.0, max(0.0, prog)))))
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
    m_out, m_join, m_fill = marks((0.12, 0.36, 0.52))
    k_out0, k_join_t, k_fill_t = dur * m_out, dur * m_join, dur * m_fill
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

    # 三个节拍默认 0.14 / 0.33 / 0.55，定型后收尾；台词有字级时间戳时由 --marks 接管
    m_b1, m_b2, m_b3, m_end = marks((0.14, 0.33, 0.55, 0.78))
    k_in = ease(t / (dur * 0.16))
    beats = (dur * m_b1, dur * m_b2, dur * m_b3)
    t_end = dur * m_end
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
        hi = 1.0 if t < t_end else max(0.0, 1 - ease((t - t_end) / (dur * 0.1))) * 0.6

    # 收尾：这块面长回到「一面做好的表面」并整块变金。不收的话结尾是画面最小的一帧。
    FINAL = [(0.18, 0.300), (0.82, 0.300), (0.86, 0.620), (0.14, 0.620)]
    k_end = ease((t - t_end) / (dur * 0.16))
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
    if beats[1] + dur * 0.03 < t < t_end:
        kk = ease((t - beats[1] - dur * 0.03) / (dur * 0.12)) * \
            (1 if t < t_end - dur * 0.06 else
             max(0.0, 1 - (t - (t_end - dur * 0.06)) / (dur * 0.06)))
        dim((pts[3][0] - W * 0.015, pts[3][1] + H * 0.042),
            (pts[2][0] + W * 0.015, pts[2][1] + H * 0.042), kk, LABELS[1], (0, H * 0.026))
    if beats[2] + dur * 0.03 < t <= t_end:
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


# ---------------------------------------------------------------- 四原语
# 前三支场景各是一部片一个函数，谁也复用不了谁。P3 把它们拆成四样可组合的东西：
#
#   subject    谁在变——**同一块几何体**，id 恒定，从第一幕活到最后一幕
#   state      变成什么——一组外观（填充色 / 粗糙度 / 光泽 / 标签）
#   transition 怎么变——原地变（morph）/ 浇上去（pour）/ 干（dry）
#   camera     镜头——**一条连续曲线**，从第一幕到最后不切、不回正
#
# 两个原语合起来就够演一件事，四个原语合起来能演一条线。承接通（「这一段像不像
# PPT 翻页」）就是靠 subject 不变 + camera 连续天然成立的，不靠临时加转场。
from dataclasses import dataclass


@dataclass(frozen=True)
class Subject:
    id: str
    quad: tuple = ((0.22, 0.24), (0.78, 0.24), (0.78, 0.62), (0.22, 0.62))

    def px(self, W: int, H: int, cam: "Camera", k: float) -> list[tuple[float, float]]:
        z, dy = cam.at(k)
        out = []
        for xf, yf in self.quad:
            out.append((W * (0.5 + (xf - 0.5) * z), H * (0.5 + (yf - 0.5) * z + dy)))
        return out


@dataclass(frozen=True)
class State:
    name: str
    fill: tuple
    grain: float = 0.5       # 0..1 表面颗粒：石头粗、膏体细
    gloss: float = 0.0       # 0..1 反光：干的东西不反光
    label: str = ""


@dataclass(frozen=True)
class Camera:
    """一条连续曲线。zoom / dy 都是「从 k=0 到 k=1」的两端，中间 ease。

    为什么不做运镜组合：一次推近只持续一句（这是 20 支参考片上量出来的），
    连续场景里一句话就是 8 秒以上，所以一条曲线正好，切碎了反而像演习。
    """
    zoom: tuple = (1.0, 1.16)
    dy: tuple = (0.0, 0.025)

    def at(self, k: float) -> tuple[float, float]:
        e = ease(k)
        return (self.zoom[0] + (self.zoom[1] - self.zoom[0]) * e,
                self.dy[0] + (self.dy[1] - self.dy[0]) * e)


def _grain_alpha(st: State, t: float, i: int) -> int:
    """颗粒是确定性的：同一帧永远同一颗，重渲能对上。"""
    h = (i * 2654435761) % 1000 / 1000.0
    wob = 0.5 + 0.5 * math.sin(t * 2.2 + h * 9.0)
    return int(max(0, min(255, 150 * st.grain * (0.35 + 0.65 * wob))))


def draw_subject(d: ImageDraw.ImageDraw, W: int, H: int, pts, st: State,
                 t: float, *, plaster: float = 0.0,
                 outline=(255, 255, 255, 26)) -> None:
    d.polygon(pts, fill=(*st.fill, 255))
    # 颗粒：只在面里撒，撒到外面就像噪点而不是材质
    x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
    y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
    n = int(360 * st.grain * (x1 - x0) * (y1 - y0) / (W * H))
    for i in range(n):
        fx = ((i * 7919) % 997) / 997.0
        fy = ((i * 6151) % 991) / 991.0
        d.point((x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy), fill=(0, 0, 0, _grain_alpha(st, t, i)))
    if plaster > 0:
        _plaster(d, W, H, (x0, y0, x1, y1), 46, plaster)
    if st.gloss > 0:
        # 光泽：一条斜向亮带，位置随时间极慢地扫——停着不动会被看成画上去的
        gw = (x1 - x0) * 0.55
        gx = x0 + (x1 - x0) * (0.12 + 0.12 * math.sin(t * 0.9))
        d.polygon([(gx, y0), (gx + gw, y0), (gx + gw * 0.55, y1), (gx - gw * 0.15, y1)],
                  fill=(255, 255, 255, int(30 * st.gloss)))
    d.line([*pts, pts[0]], fill=outline, width=2)


# ---------------------------------------------------------------- 效果原语
# 包装包 `fx` 里点了名的效果，得真能画出来，否则那行字只是声明。

def draw_particles(d: ImageDraw.ImageDraw, W: int, H: int, t: float, *, n: int = 16,
                   color=(210, 205, 190), rise: float = 0.0, alpha: int = 120,
                   region=(0.22, 0.78, 0.08, 0.92), seed: int = 7) -> None:
    """持续飘的粒子（尘埃 / 热粒 / 碎屑）。

    `rise` > 0 向上飘（热），= 0 向下落（屑）。位置由 t 直接算，确定性，
    同一秒永远同一帧——重渲能对上，验收也能复现。
    """
    x0, x1, y0, y1 = region
    for i in range(n):
        fx = x0 + (x1 - x0) * (((i * 7919 + seed * 131) % 997) / 997.0)
        ph = ((i * 6151 + seed * 37) % 991) / 991.0
        if rise > 0:
            fy = y1 - ((t * rise + ph) % 1.0) * (y1 - y0)
            a = int(alpha * (1 - (y1 - fy) / max(1e-6, y1 - y0)))
        else:
            fy = y0 + ((t * max(0.05, rise + 0.06) + ph) % 1.0) * (y1 - y0)
            a = int(alpha * 0.8)
        r = 2 + (i % 3)
        d.ellipse([W * fx - r, H * fy - r, W * fx + r, H * fy + r],
                  fill=(*color[:3], max(0, min(255, a))))


def draw_sweep(d: ImageDraw.ImageDraw, W: int, H: int, k: float, *,
               color=(255, 255, 255), band: float = 0.16, alpha: int = 90) -> None:
    """扫光 / 擦除：一条亮带从左划到右（k = 0..1）。

    用途是**换幕**：两块内容之间不想硬切、又没有真素材承接时，用它把观众的注意力
    从左边带过去。带宽按画幅宽算，斜着走（纯竖直的亮带会被看成「翻页插件」）。
    """
    if k <= 0 or k >= 1:
        return
    cx = -band + (1 + band * 2) * k
    x0 = (cx - band) * W
    x1 = (cx + band) * W
    d.polygon([(x0, 0), (x1 + W * 0.08, 0), (x1 - W * 0.12, H), (x0 - W * 0.16, H)],
              fill=(*color[:3], alpha))


def scene_lime(t: float, W: int, H: int, dur: float) -> Image.Image:
    """一块石灰基艺术涂料的一生（B 轴第一支 demo）。

    三幕，**同一块东西**：
      一·出身   石灰石 ──煅烧──► 生石灰
      二·变化   生石灰 ──消解·熟化──► 膏体
      三·落点   膏体 ──上墙──► 成品
    画的是**示意**不是工序录像：真实工序是石灰石→煅烧→消解→熟化→施工，
    每一步的时长、温度、设备这里一律不画，免得画错当真。
    """
    m = marks((0.30, 0.62))
    k = min(1.0, max(0.0, t / max(dur, 1e-6)))
    cam = Camera(zoom=(1.0, 1.17), dy=(0.0, 0.03))
    subj = Subject("lime-slab")

    stone = State("石灰石", (128, 126, 120), grain=1.0, gloss=0.0, label="石灰石")
    quicklime = State("生石灰", (222, 216, 198), grain=0.75, gloss=0.0, label="煅烧后：生石灰")
    putty = State("膏体", (236, 232, 220), grain=0.22, gloss=0.55, label="消解·熟化：膏体")
    wall = State("上墙", (240, 236, 226), grain=0.35, gloss=0.25, label="上墙")
    done = State("成品", (232, 196, 96), grain=0.18, gloss=0.9, label="成品")

    im = Image.fromarray(_backdrop(t, W, H, cy=0.43, glow=1.05), "RGB")
    d = ImageDraw.Draw(im, "RGBA")
    f_lab = font(FONT_BOLD, int(H * 0.026))
    f_tag = font(FONT_SEMI, int(H * 0.019))

    pts = subj.px(W, H, cam, k)
    lab = stone.label
    if t < dur * m[0]:                                   # 一幕：石头
        st = stone
        lab = stone.label
    elif t < dur * m[1]:                                 # 二幕：煅烧 → 粉
        kk = ease((t - dur * m[0]) / max(dur * (m[1] - m[0]), 1e-6))
        st = State("→", _mix(stone.fill, quicklime.fill, kk),
                   grain=stone.grain * (1 - kk) + quicklime.grain * kk,
                   gloss=0.0, label=quicklime.label)
        lab = stone.label if kk < 0.5 else quicklime.label
    elif t < dur * (m[1] + 0.16):                        # 三幕前段：粉 → 膏（有液体滴下）
        kk = ease((t - dur * m[1]) / max(dur * 0.16, 1e-6))
        st = State("→", _mix(quicklime.fill, putty.fill, kk),
                   grain=quicklime.grain * (1 - kk) + putty.grain * kk,
                   gloss=putty.gloss * kk, label=putty.label)
        lab = putty.label
        for i in range(4):                              # 水滴：给「消解」一点物理
            fx = 0.34 + 0.32 * (i / 3)
            yy = pts[0][1] + (pts[3][1] - pts[0][1]) * (0.12 + 0.62 * kk) - i * 10
            d.ellipse([W * fx - 5, yy - 7, W * fx + 5, yy + 7],
                      fill=(120, 170, 215, int(200 * (1 - kk * 0.5))))
    else:                                                # 三幕后段：上墙 → 成品
        kk = ease((t - dur * (m[1] + 0.16)) / max(dur * (1 - m[1] - 0.16), 1e-6))
        st = State("→", _mix(putty.fill, done.fill, ease(max(0.0, kk - 0.55) / 0.45)),
                   grain=putty.grain * (1 - kk) + done.grain * kk,
                   gloss=putty.gloss * (1 - kk) + done.gloss * kk,
                   label=done.label)
        lab = wall.label if kk < 0.55 else done.label

    # 每一幕都得有一件**一直在动**的小东西，否则 12 秒里会有 3 秒完全静止——
    # 视觉复核原话：「0.5s 和 4s 完全一样」。冻结不是靠加转场治的，是靠这个。
    if t < dur * m[0]:                                   # 一幕：石屑在落
        draw_particles(d, W, H, t, n=14, color=(210, 205, 190), alpha=90,
                       region=(0.26, 0.74, 0.05, 0.95))
    elif t < dur * m[1]:                                 # 二幕：煅烧的热在往上走
        kk = ease((t - dur * m[0]) / max(dur * (m[1] - m[0]), 1e-6))
        draw_particles(d, W, H, t, n=18, color=(255, 150, 60), alpha=int(160 * kk),
                       rise=0.10, region=(0.24, 0.76, 0.30, 0.66), seed=3)

    plaster_prog = 0.0
    if lab == "上墙":
        plaster_prog = 1.0
    elif lab == "成品":
        plaster_prog = 1.0
    for _b in (m[0], m[1]):                              # 换幕：扫光带一下注意力
        _k = (t - dur * _b) / max(1e-6, dur * 0.12)
        if 0 < _k < 1:
            draw_sweep(d, W, H, _k, color=(255, 250, 235), band=0.14, alpha=70)

    draw_subject(d, W, H, pts, st, t, plaster=plaster_prog)
    _text_at(d, W, H, lab, 0.5, (min(p[1] for p in pts) / H) - 0.055, f_lab, (255, 255, 255, 232))
    _text_at(d, W, H, "示意", 0.5, 0.905, f_tag, (255, 255, 255, 110))
    # 进度：三幕的刻度，让人知道自己在一条线上而不是三张图
    y = H * 0.945
    d.line([W * 0.22, y, W * 0.78, y], fill=(255, 255, 255, 40), width=3)
    d.line([W * 0.22, y, W * (0.22 + 0.56 * k), y], fill=(*SOLID, 190), width=5)
    return im


SCENES = {"phase": scene_phase, "pour": scene_pour,
          "measure": scene_measure, "lime": scene_lime}


def _peek_scene_id(path: Path) -> str | None:
    """只解析不执行：从源码里取出顶层 `SCENE_ID = "..."` 字面量。

    2026-11-15 对抗审查 P2：原来的校验在 exec_module **之后**，冒名文件的顶层
    代码已经执行完了才被拒——校验顺序反了。先 AST 拎出字面量比对，对不上就
    一行都不执行。拿不到字面量（动态表达式）返回 None，由调用方拒绝。
    """
    import ast
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "SCENE_ID":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    return None


def _load_scene_file(path: Path) -> tuple[str, object] | None:
    """从任意路径加载一个场景模块（项目级 AI 场景用），返回 (SCENE_ID, scene())。"""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(f"scene_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sid, fn = getattr(mod, "SCENE_ID", None), getattr(mod, "scene", None)
        if sid and callable(fn):
            return str(sid), fn
    except Exception as e:                          # noqa: BLE001
        print(f"⚠ 场景模块 {path.name} 没接上：{e}", file=sys.stderr)
    return None


def register_scenes_dir(d: Path) -> list[str]:
    """把一个目录里的 *.py 场景接进 SCENES（项目级场景可覆盖同名内置场景）。"""
    added: list[str] = []
    if not d.is_dir():
        return added
    for f in sorted(d.glob("*.py")):
        if f.name.startswith("_"):
            continue
        got = _load_scene_file(f)
        if got:
            SCENES[got[0]] = got[1]
            added.append(got[0])
    return added


def _register_scene_package() -> None:
    """把 `scripts/scenes/` 下的场景模块接进 SCENES（AI 视听方案 P0 起的新场景）。

    每个模块导出 `SCENE_ID` / `scene(t, W, H, dur)` / `EVENTS` / `STORY_BEATS`；
    模块画法必须纯 t 驱动（`test_scene_determinism.py` 的合同）。
    包不在 / 模块写错就只跳过它——**一个新场景写坏不许拖死老链路**。
    """
    pkg = Path(__file__).resolve().parent / "scenes"
    if not pkg.is_dir():
        return
    import importlib
    import sys as _sys
    if str(pkg.parent) not in _sys.path:
        _sys.path.insert(0, str(pkg.parent))
    for f in sorted(pkg.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"scenes.{f.stem}")
            sid, fn = getattr(mod, "SCENE_ID", None), getattr(mod, "scene", None)
            if sid and callable(fn):
                SCENES[sid] = fn
        except Exception as e:                      # noqa: BLE001
            print(f"⚠ 场景模块 {f.name} 没接上：{e}", file=sys.stderr)


_register_scene_package()


def render(scene: str, out: Path, dur: float, fps: int, W: int, H: int) -> Path:
    fn = SCENES[scene]
    frames = int(round(dur * fps))
    with tempfile.TemporaryDirectory(prefix="scene-") as _td:
        tmp = Path(_td)
        for i in range(frames):
            fn(i / fps, W, H, dur).save(tmp / f"f{i:04d}.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                            "-i", str(tmp / "f%04d.png"), "-c:v", "libx264", "-crf", "17",
                            "-pix_fmt", "yuv420p", str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stderr[-400:])
    return out


FREEZE_S = 2.0          # 静止多久算「冻住」：全片判会误杀（20 支参考片 7 支有空镜），
FREEZE_FPS = 8          # 但代码画面里连续 2 秒一模一样就是错——它不是素材，是我画的。
MOTION_EPS = 0.6        # 帧差小于这个算「这一格没动」（0–255 灰度均值差）


def motion_profile(fn, dur: float, W: int, H: int) -> tuple[float, float, float]:
    """帧差剖面：最长静止多少秒、有多少比例的相邻帧在动。

    为什么必须进 `--probe`：`bright` 只判「收尾空不空」，判不了「中间是不是一张静图」。
    实测反例——一支纯静止的亮画面，`--probe` 给的是 🟢 100% 收尾有东西。
    而「0.5s 和 4s 一模一样」正是这套场景被自己打回的原病，判据不能瞎。
    """
    step = 1.0 / FREEZE_FPS
    n = max(2, int(round(dur / step)))
    small = (max(120, W // 4), max(200, H // 4))
    prev = None
    diffs = []
    for i in range(n):
        t = min(dur - 1e-3, i * step)
        arr = np.asarray(fn(t, small[0], small[1], dur).convert("L"), dtype=np.float32)
        if prev is not None:
            diffs.append(float(np.abs(arr - prev).mean()))
        prev = arr
    run = worst = 0
    for d in diffs:
        if d < MOTION_EPS:
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    moving = sum(1 for d in diffs if d >= MOTION_EPS) / max(1, len(diffs))
    med = float(np.median(diffs)) if diffs else 0.0
    return worst * step, moving, med


def probe_scene(fn, dur: float, W: int, H: int) -> tuple[bool, list[str]]:
    msgs, bad = [], False
    rows, bright = [], []
    for i in range(6):
        t = round(dur * i / 5, 2)
        arr = np.asarray(fn(min(t, dur - 1e-3), W, H, dur).convert("L"))
        b = float((arr > 60).mean()) * 100
        rows.append((t, float(arr.mean()), b))
        bright.append(b)
    # 「最满的一帧」用**次高**，不用 max：扫光/闪一下这种瞬时会顶出一个假峰值，
    # 拿它当基准，正常的收尾全被判「收空了」（实测加了换幕扫光之后，峰值从 30.5% 跳到 51.6%）。
    # 判据要盯的是「典型地有多满」，不是「历史上某一帧碰巧多亮」。
    _sb = sorted(bright)
    peak = _sb[-2] if len(_sb) > 2 else max(bright)
    top = max(bright)
    for t, lu, b in rows:
        msgs.append(f"  t={t:5.2f}s  luma {lu:5.1f}  bright {b:5.2f}%"
                    f"{' ←' if b == peak else ('  (瞬间峰值)' if b == top and top > peak else '')}")
    if bright[-1] < 5.0:
        msgs.append(f"⚠ 结尾几乎全黑（{bright[-1]:.1f}%）——收尾没落下东西")
        bad = True
    elif bright[-1] < peak * 0.6:
        msgs.append(f"⚠ 结尾比最满的一帧空了 {100 - bright[-1] / peak * 100:.0f}%"
                    f"（{bright[-1]:.1f}% vs {peak:.1f}%）——收尾收空了")
        bad = True
    # 中位帧差要一起报：只看「动帧比例」认不出「变化小到看不见」——
    # 一个 2 像素的点在动也能让 90% 的相邻帧算「在动」。数字得自己看得见。
    still, moving, med = motion_profile(fn, dur, W, H)
    if still >= FREEZE_S:
        msgs.append(f"⚠ 最长静止 {still:.2f}s（判据 <{FREEZE_S}s）——中间有一段是静的，像一张图")
        bad = True
    if moving < 0.30:
        msgs.append(f"⚠ 只有 {moving * 100:.0f}% 的相邻帧在动——整段太静")
        bad = True
    if not bad:
        msgs.append(f"🟢 收尾有东西（{bright[-1]:.1f}% / 峰值 {peak:.1f}%）· "
                    f"最长静止 {still:.2f}s · 动帧 {moving * 100:.0f}% · 帧差中位 {med:.2f}")
    else:
        msgs.append(f"（结尾 {bright[-1]:.1f}% / 峰值 {peak:.1f}% · 最长静止 {still:.2f}s · "
                    f"动帧 {moving * 100:.0f}% · 帧差中位 {med:.2f}）")
    return (not bad), msgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="phase",
                    help="场景名；--scenes-dir 接进来的项目级场景也能用（手工校验，不用 choices）")
    ap.add_argument("--scene-file", default="",
                    help="沙箱专用：只加载这一个场景 .py 文件。\n"
                         "不走目录扫描——同目录里未审查的同伙文件一个都不许执行（评审 G-02）；\n"
                         "文件里 SCENE_ID 必须与 --scene 一致，否则拒绝渲染。")
    ap.add_argument("--scenes-dir", default="",
                    help="额外的场景源码目录（项目 scenes/）；AI 新写的场景从这里接进注册表，"
                         "渲染本身应走 scene_sandbox.py（拒网 + rlimit），不要直接裸跑本脚本")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dur", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=30, help="跟成片一致（talking-head-v1 是 30）")
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--marks", default="",
                    help="词级节拍：逗号分隔的**成数**（0.2,0.39,0.42,0.74），由 ASR 字级时间戳算出来。"
                         "空着就用脚本自己的默认比例——那样画面换形态的那一帧跟台词对不上。")
    ap.add_argument("--probe", action="store_true",
                    help="只量亮度剖面，不出片：每个场景都要「越演越有东西」，"
                         "结尾比中间空就是节拍写错了（视觉模型判不了这个）")
    a = ap.parse_args()
    if a.scene_file:
        # 沙箱专用入口：只执行静态检查过的这一个文件，绝不扫目录（评审 G-02）。
        sf = Path(a.scene_file).expanduser().resolve()
        # 先比名字、再执行：冒名文件的顶层代码一行都不许跑（2026-11-15 对抗审查 P2）
        peek = _peek_scene_id(sf)
        if peek != a.scene:
            print(f"✗ 场景文件声明的 SCENE_ID={peek!r} 与请求的 --scene {a.scene!r} 不一致，"
                  "拒绝加载（文件未执行）", file=sys.stderr)
            return 2
        got = _load_scene_file(sf)
        if not got:
            print(f"✗ 场景文件加载失败：{sf}", file=sys.stderr)
            return 2
        if got[0] != a.scene:
            print(f"✗ 文件里的 SCENE_ID={got[0]!r} 与请求的 --scene {a.scene!r} 不一致，拒绝渲染",
                  file=sys.stderr)
            return 2
        SCENES[got[0]] = got[1]
    elif a.scenes_dir:
        added = register_scenes_dir(Path(a.scenes_dir).expanduser())
        if added:
            print(f"  · 项目场景已注册：{', '.join(added)}")
    if a.scene not in SCENES:
        print(f"✗ 场景「{a.scene}」不认识（{'/'.join(sorted(SCENES))}）", file=sys.stderr)
        return 2
    global MARKS
    MARKS = [float(x) for x in a.marks.replace(" ", "").split(",") if x] if a.marks else []
    W, H = (int(x) for x in a.size.lower().split("x"))
    if a.probe:
        ok, msgs = probe_scene(SCENES[a.scene], a.dur, W, H)
        print("\n".join(msgs))
        return 0 if ok else 1
    out = render(a.scene, Path(a.out).expanduser(), a.dur, a.fps, W, H)
    print(f"→ {out}（{a.scene} · {a.dur}s · {W}×{H} @{a.fps}fps）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
