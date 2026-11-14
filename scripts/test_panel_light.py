#!/usr/bin/env python3
"""T-042 回归：面板光感是「加光」，不是「减读」。

T-042 动了两件会伤可读性的事：
  ① `tokens.surface.plate` 从平涂色改成纵向光线渐变（顶部窄亮带 → 中部回落 → 底部沉）；
  ② 中段**不透明度下调**（.78→.70 / .76→.70 / .76→.68 / .90→.82），让背景纹理透出来。

「更好看」与「还读得出」是两笔账。这个测试只算第二笔，并且用**包里真实的那串 CSS**
算——不是抄一份「差不多的颜色」进来：

  1. 把 `plate` 的 gradient 解成色标（颜色 + 位置）；
  2. 逐档合成到一组背景色上（L* 0 / 60 / 120 / 180 / 240，覆盖暗场到白墙）；
  3. 在合成出来的面板上画细横条当笔画（3px，真实字干粗细的量级）；
  4. 用 `measure_panel.delta_l()`（**和实测 c5 用的是同一个测量器**）量 ΔL；
  5. 门槛：**每一档** ΔL ≥ 12。某一档不够就红——这正是「把底板调透」最先崩的地方。

另外三条结构断言（否则「渐变」写成一条水平线也过）：
  · 顶部 1% 的亮度 > 中部（有受光面）；
  · 底部 1% 的亮度 < 中部（下缘背光，不是又亮一圈）；
  · `edge` 是两条颜色、`light` 是一段 gradient。

用法：python3 scripts/test_panel_light.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import measure_panel as MP            # noqa: E402
from measure_panel import lstar       # noqa: E402

ROOT = HERE.parent
PACKS = ("P1-warm", "P2-paper", "P3-dark", "P4-canton-warm", "P5-viral-pop")
DL_MIN = 12.0                         # 可读性门槛（这个任务给的数）
BGS = (0, 20, 40, 60, 80, 100)        # 背景 L* 档位：黑 / 暗场 / 中灰 / 亮场 / 白墙
#   （L* 的上限就是 100，用 0–255 那套当档位是量错了一次——T-042 首版就踩过）


def parse_gradient(css: str) -> list[tuple[float, tuple[int, int, int, float]]]:
    """`linear-gradient(180deg, rgba(...) 0%, ... )` → [(位置 0–1, (r,g,b,a))]。"""
    stops = []
    for part in re.split(r",(?![^()]*\))", css):
        p = part.strip()
        p = re.sub(r"^linear-gradient\([^,]*,", "", p).strip().rstrip(")")
        if not p or p.startswith("180deg") or p.startswith("to "):
            continue
        m = re.match(r"rgba?\(([^)]*)\)\s*([\d.]+)%?", p)
        if not m:
            continue
        nums = [float(v) for v in m.group(1).split(",")]
        if len(nums) == 3:
            nums.append(1.0)
        pos = float(m.group(2)) / 100.0 if m.group(2) else 0.0
        stops.append((pos, (int(nums[0]), int(nums[1]), int(nums[2]), nums[3])))
    return stops


def sample(stops, pos: float) -> tuple[int, int, int, float]:
    """在色标序列上按位置插值（含 alpha）。"""
    if pos <= stops[0][0]:
        return stops[0][1]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if pos <= p1:
            t = 0.0 if p1 == p0 else (pos - p0) / (p1 - p0)
            return tuple(c0[i] + (c1[i] - c0[i]) * t for i in range(4))  # type: ignore
    return stops[-1][1]


def gray_of_L(l: float) -> float:
    """L* → 灰度 sRGB(0–255)（L* → Y → sRGB 的标准反函数）。
    不能用 `(L/100)**0.45*255` 近似：那个到 L*=55 就撞顶到 255 了，
    于是「亮墙」三档变成同一档——测试看着过了，其实一档没测。"""
    l = min(100.0, max(0.0, float(l)))
    y = ((l + 16.0) / 116.0) ** 3 if l > 8.0 else l / 903.3
    s = 12.92 * y if y <= 0.0031308 else 1.055 * (y ** (1.0 / 2.4)) - 0.055
    return min(255.0, max(0.0, s * 255.0))


def composite(color, bg_l: float) -> float:
    """把 (r,g,b,a) 压到 L* 背景上，返回合成后的 L*。
    **必须在 sRGB 里合成再转 L***：在 L* 空间里做线性混合是错的，
    暗底上的同一层透明度会被算得比实际亮（阶调不是线性的）。"""
    c = np.asarray(color, dtype=float)
    g = gray_of_L(bg_l)
    comp = c[:3] * c[3] + g * (1.0 - c[3])
    return float(lstar(comp)[0] if np.ndim(lstar(comp)) else lstar(comp))


def plate_panel(stops, bg_l: float, h: int = 180, w: int = 200) -> np.ndarray:
    """按渐变把面板画成一张 RGB 图（逐行取色标、在 sRGB 里合成）。"""
    g = gray_of_L(bg_l)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        c = sample(stops, y / max(1, h - 1))
        row = np.array([c[0] * c[3] + g * (1 - c[3]),
                        c[1] * c[3] + g * (1 - c[3]),
                        c[2] * c[3] + g * (1 - c[3])])
        img[y, :, :] = np.clip(row, 0, 255).astype(np.uint8)
    return img


def draw_strokes(img: np.ndarray, ink_rgb) -> np.ndarray:
    """在面板上画细横条当笔画（3px：真实字干的量级，不是一个大色块）。"""
    out = img.copy()
    h, w = out.shape[:2]
    for y in range(int(h * 0.18), int(h * 0.92), 14):
        out[y:y + 3, int(w * 0.12):int(w * 0.88)] = ink_rgb
    return out


def ink_rgb(hexs: str) -> tuple[int, int, int]:
    h = str(hexs).lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def main() -> int:
    fails = []
    print(f"面板可读性：门槛 ΔL ≥ {DL_MIN}（文字像素 vs 紧邻底板，"
          f"测量器 = scripts/measure_panel.delta_l）")
    print(f"{'包':<16}{'背景 L*':>7}{'底板 L*(顶/中/底)':>22}{'ΔL p10':>9}{'ΔL 中位':>9}")
    for name in PACKS:
        pack = yaml.safe_load((ROOT / "packs" / f"{name}.yaml").read_text())
        surf = (pack.get("tokens") or {}).get("surface") or {}
        stops = parse_gradient(str(surf.get("plate") or ""))
        if len(stops) < 3:
            fails.append(f"{name}: plate 不是纵向光线渐变（色标 {len(stops)} < 3）")
            continue
        ink = ink_rgb(surf.get("ink") or "#FFFFFF")
        # 结构：顶亮 / 底沉
        top = composite(sample(stops, 0.01), 0)
        mid = composite(sample(stops, 0.62), 0)
        bot = composite(sample(stops, 0.99), 0)
        if not top > mid:
            fails.append(f"{name}: 顶部亮带没亮过中部（顶 {top:.1f} / 中 {mid:.1f}）")
        if not bot < mid:
            fails.append(f"{name}: 底部没沉下去（底 {bot:.1f} / 中 {mid:.1f}）")
        eg = surf.get("edge")
        if not (isinstance(eg, (list, tuple)) and len(eg) == 2):
            fails.append(f"{name}: surface.edge 不是 [顶光, 底暗] 两条")
        lg = str(surf.get("light") or "")
        if "gradient(" not in lg:
            fails.append(f"{name}: surface.light 不是 gradient（底板那一层光没了）")
        for bg in BGS:
            panel = plate_panel(stops, bg)
            r = MP.delta_l(draw_strokes(panel, ink))
            Ls = [round(composite(sample(stops, p), bg), 1) for p in (0.01, 0.62, 0.99)]
            print(f"{name:<16}{bg:>7}{str(Ls):>22}{r['dL_min']!s:>9}{r['dL_med']!s:>9}")
            if not r["ok"] or (r["dL_min"] or 0) < DL_MIN:
                fails.append(f"{name}: 背景 L*={bg} 上 ΔL {r['dL_min']} < {DL_MIN}"
                             f"（面板在浅底上透得太多，字要糊了）")
    if fails:
        print("❌ 面板光感把可读性吃掉了：")
        for f in fails:
            print("   ·", f)
        return 1
    print("✅ 四个包 × 五档背景：顶部有受光面、底部沉下去、ΔL 全部 ≥ "
          f"{DL_MIN}（edge/light 两条结构字段齐）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
