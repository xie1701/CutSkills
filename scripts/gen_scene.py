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


def _chip(d: ImageDraw.ImageDraw, W: int, H: int, text: str, y_frac: float,
          f: ImageFont.FreeTypeFont, alpha: int) -> None:
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


def render(scene: str, out: Path, dur: float, fps: int, W: int, H: int) -> Path:
    fn = SCENES[scene]
    tmp = Path(tempfile.mkdtemp(prefix="scene-"))
    frames = int(round(dur * fps))
    for i in range(frames):
        im = fn(i / fps, W, H, dur)
        im.save(tmp / f"f{i:04d}.png")
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
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--size", default="1080x1920")
    a = ap.parse_args()
    W, H = (int(x) for x in a.size.lower().split("x"))
    out = render(a.scene, Path(a.out).expanduser(), a.dur, a.fps, W, H)
    print(f"→ {out}（{a.scene} · {a.dur}s · {W}×{H}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
