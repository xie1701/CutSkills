#!/usr/bin/env python3
"""CutSkills · 卡面板可读性实测：文字像素 vs **紧邻**底板的 ΔL

为什么要有这个脚本（T-042）：
T-042 把 `tokens.surface.plate` 从一块平涂色改成纵向光线渐变，同时把中段不透明度
往下调了一点（让背景纹理透出来）。两件事都会**降低**字与底板的明度差——
「看起来更高级」和「字还读不读得出」是两笔账，必须分开记。
所以这里只做一件事：**量**。判据（≥12）在 `scripts/test_panel_light.py` 里。

量法（写进测试的就是这一份，两处不许各写一套）：

  1. 把图转成 CIE L*（0=黑 / 100=白）。用 L* 而不是 0–255 的 Y：
     同样 20 级灰差，在暗部与亮部的人眼感受差一倍，L* 已经把这件事算进去了。
  2. 取直方图峰值当**底板**明度（一块面板里笔画是小众，底板是多数）。
  3. 笔画 = 比底板高/低 25 个 L* 以上的像素（暗字/亮字都认；抗锯齿过渡不算）。
  4. 逐个笔画像素看它**四邻**（15×15 窗口里非笔画的像素）的中位 L*——
     这就是「紧邻底板」。渐变面板的顶部与底部明度不一样，取中位就把它算对了。
  5. 报 ΔL = 笔画 L* − 邻域底板 L*，取绝对值后看**第 10 百分位**当这张卡的可读性读数。
     为什么不用绝对最小值：字形边缘总有一圈半覆盖像素，它们的 ΔL 天然接近 0，
     那不是可读性，是抗锯齿；p10 把那一圈刨掉，留的才是「最淡的那一笔」。
     为什么不用平均值：平均值会让一大块实心数字把「释义小字发糊」盖过去。

用法：
    python3 scripts/measure_panel.py --image c5.png                # 整图
    python3 scripts/measure_panel.py --image c5.png --box 43,1190,713,538
    python3 scripts/measure_panel.py --image c5.png --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

TEXT_DELTA = 25.0        # 与底板差这么多 L* 才算「笔画」：18 以下全是抗锯齿过渡与底纹，
                         # 把它们算进笔画，p10 就会被一层半覆盖像素拉到 17（实测 c5），
                         # 那不是字发糊，是边缘。25 是「一眼认得出是一笔」的下限。


def lstar(rgb: np.ndarray) -> np.ndarray:
    """sRGB(0–255) → CIE L*（0–100）。rgb 形状任意，最后一维是 3。"""
    a = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    return np.where(y > 0.008856, 116.0 * np.cbrt(y) - 16.0, 903.3 * y)


def _plate_mode(L: np.ndarray) -> float:
    """直方图峰值 = 底板明度（面板里底板像素永远比笔画多）。"""
    h, edges = np.histogram(L, bins=50, range=(0.0, 100.0))
    i = int(np.argmax(h))
    return float((edges[i] + edges[i + 1]) / 2.0)


def delta_l(img: np.ndarray, box=None, win: int = 15, max_samples: int = 600) -> dict:
    """一张卡的可读性读数。img: HxWx3 sRGB。

    返回 {"plate_L", "text_L", "dL_min", "dL_med", "n_text", "ok"}；
    画面里找不到笔画（纯色块）时 dL_min=None, ok=False——调用方不许拿它当「合格」。
    """
    if box:
        x, y, w, h = [int(round(v)) for v in box]
        img = img[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]
    L = lstar(np.asarray(img, dtype=np.float64))
    if L.size < 64:
        return {"plate_L": None, "text_L": None, "dL_min": None, "dL_med": None,
                "n_text": 0, "ok": False}
    plate = _plate_mode(L)
    text = np.abs(L - plate) >= TEXT_DELTA
    n_text = int(text.sum())
    if n_text < 32:
        return {"plate_L": round(plate, 1), "text_L": None, "dL_min": None,
                "dL_med": None, "n_text": n_text, "ok": False}
    ys, xs = np.nonzero(text)
    if len(ys) > max_samples:                      # 固定步长取样：同一张图永远同一批点
        step = len(ys) // max_samples
        ys, xs = ys[::step][:max_samples], xs[::step][:max_samples]
    H, W = L.shape
    r = win // 2
    dl = []
    for yy, xx in zip(ys, xs):
        y0, y1 = max(0, yy - r), min(H, yy + r + 1)
        x0, x1 = max(0, xx - r), min(W, xx + r + 1)
        win_L = L[y0:y1, x0:x1]
        win_t = text[y0:y1, x0:x1]
        nb = win_L[~win_t]
        if nb.size < 8:
            continue
        # 笔画是亮的还是暗的，决定 ΔL 的符号：暗字压亮底也算可读，别把符号当噪声
        dl.append(float(L[yy, xx] - np.median(nb)))
    if not dl:
        return {"plate_L": round(plate, 1), "text_L": None, "dL_min": None,
                "dL_med": None, "n_text": n_text, "ok": False}
    a = np.abs(np.asarray(dl))
    worst = float(np.percentile(a, 10))
    med = float(np.median(a))
    text_L = float(np.median(L[ys, xs]))
    return {"plate_L": round(plate, 1), "text_L": round(text_L, 1),
            "dL_min": round(worst, 1), "dL_med": round(med, 1),
            "n_text": n_text, "ok": worst > 0}


def load_rgb(path: Path, box=None) -> np.ndarray:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if box:
        x, y, w, h = [int(round(v)) for v in box]
        im = im.crop((x, y, x + w, y + h))
    return np.asarray(im)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--box", default="", help="x,y,w,h（1080 原像素坐标，裁到卡面内）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    box = [float(v) for v in a.box.split(",")] if a.box else None
    if box and len(box) != 4:
        raise SystemExit("--box 要 x,y,w,h 四个数")
    img = load_rgb(Path(a.image).expanduser(), box)
    r = delta_l(img, box=None)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1
    print(f"{Path(a.image).name}"
          f"{f' 框 {[int(v) for v in box]}' if box else ' 整图'}："
          f"底板 L*={r['plate_L']} · 笔画 L*={r['text_L']} · "
          f"ΔL 最差(p10) {r['dL_min']} / 中位 {r['dL_med']} · 笔画 {r['n_text']}px")
    if not r["ok"]:
        print("！这一格量不出笔画（纯色块 / 没有字）——不许当合格")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
