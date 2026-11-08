#!/usr/bin/env python3
"""match_font.py — 认一支片子的字幕/花字用的是哪一种字体。

为什么要这个：`measure_pack.py` 只量位置和字号，`compare_films.py` 只量亮度和节奏。
**字体从来没有被量过**——它会一直"在带内"，然后一眼就被人看出来不对劲。

做法（不是"看着像"）：
  1. 从成片某一帧的下方带里把白字抠成二值掩膜（亮 + 低饱和），按行切出字幕行；
  2. 每行取紧致外框；
  3. 每个候选字体用同一串字渲染，同样二值化、同样紧致裁切、缩放到同一尺寸；
  4. 算 IoU（交并比）和笔画粗细比（墨迹面积 / 外框面积），排序。

判读：IoU 第一名领先第二名 **0.06 以上**才算认出来；差得少就是"候选里没有它"，别硬认。
字体识别的上限由源分辨率决定——576 宽的片子里字高只有 29 像素，IoU 到 0.75 就已经很高了。

用法：
  python3 match_font.py --video ref.mp4 --t 19 --text "这都是我们|绕不开的坑啊"
  python3 match_font.py --video ours.mp4 --t 46 --text "成本贵在哪" --top 5
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = [
    Path("~/Library/Fonts").expanduser(),
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
]

NAME_MAP = {
    "AlimamaShuHeiTi-Bold": "阿里妈妈数黑体 Bold",
    "NotoSansSC-Bold": "思源黑体 Bold（Noto Sans SC）",
    "NotoSansSC-Black": "思源黑体 Black",
    "NotoSansSC-SemiBold": "思源黑体 SemiBold",
    "NotoSansSC-Medium": "思源黑体 Medium",
    "方正兰亭中黑_GBK": "方正兰亭中黑",
    "方正兰亭大黑_GBK": "方正兰亭大黑",
    "方正兰亭准黑_GBK": "方正兰亭准黑",
    "方正兰亭细黑_GBK_M": "方正兰亭细黑",
    "字幕黑体M": "字幕黑体 M",
    "simhei": "中易黑体",
    "Microsoft Yahei Bold": "微软雅黑 Bold",
    "Microsoft Yahei": "微软雅黑",
    "DroidSansChinese": "Droid Sans 中文",
    "NotoSerifSC-Bold": "思源宋体 Bold",
    "SourceHanSerifSC-Bold": "思源宋体 Bold",
    "Songti": "宋体",
}

# 默认候选：本机可用的中文黑体类（宋体留一个做对照，认出来说明是衬线）
DEFAULT_CANDIDATES = [
    "AlimamaShuHeiTi-Bold", "NotoSansSC-Bold", "NotoSansSC-Black",
    "NotoSansSC-SemiBold", "NotoSansSC-Medium", "方正兰亭中黑_GBK",
    "方正兰亭大黑_GBK", "方正兰亭准黑_GBK", "字幕黑体M", "simhei",
    "Microsoft Yahei Bold", "DroidSansChinese", "NotoSerifSC-Bold",
]


def find_font(name: str) -> Path | None:
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in {".otf", ".ttf", ".ttc"} and p.stem.split(".")[0].lower() == name.lower():
                return p
    # 模糊匹配（含 ttc 里的子字体名做不到，退一步按前缀）
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in {".otf", ".ttf", ".ttc"} and p.stem.lower().startswith(name.lower()):
                return p
    return None


def grab_frame(video: Path, t: float) -> Image.Image:
    if video.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return Image.open(video).convert("RGB")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    ).stdout
    if not raw:
        raise SystemExit(f"！取帧失败：{video} @ {t}s")
    return Image.open(io.BytesIO(raw)).convert("RGB")


def white_mask(im: Image.Image) -> np.ndarray:
    a = np.asarray(im).astype(int)
    mx, mn = a.max(2), a.min(2)
    return (mx > 200) & ((mx - mn) < 45)


def line_boxes(mask: np.ndarray, min_gap: int = 3) -> list[tuple[int, int]]:
    """按行切出文字行：(y0, y1)。要求行内墨迹足够多。"""
    rows = mask.sum(1)
    thr = max(4, rows.max() * 0.10) if rows.max() else 1
    on = rows > thr
    boxes, start = [], None
    for y, v in enumerate(on):
        if v and start is None:
            start = y
        elif not v and start is not None:
            boxes.append((start, y))
            start = None
    if start is not None:
        boxes.append((start, len(on)))
    merged = []
    for b in boxes:
        if merged and b[0] - merged[-1][1] <= min_gap:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(list(b))
    return [tuple(b) for b in merged if b[1] - b[0] >= 6]


def tight(mask: np.ndarray) -> tuple[np.ndarray, dict]:
    ys, xs = np.where(mask)
    if not len(ys):
        return mask, {}
    b = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return b, {"w": b.shape[1], "h": b.shape[0], "ink": int(mask.sum())}


def render_mask(text: str, font_path: Path, size: int) -> np.ndarray:
    f = ImageFont.truetype(str(font_path), size)
    pad = size
    img = Image.new("L", (int(f.getlength(text)) + pad * 2, size * 2 + pad * 2), 0)
    ImageDraw.Draw(img).text((pad, pad), text, font=f, fill=255)
    return np.asarray(img) > 127


def iou(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = np.asarray(Image.fromarray((b * 255).astype(np.uint8)).resize(
            (a.shape[1], a.shape[0]), Image.LANCZOS)) > 110
    inter = (a & b).sum()
    uni = (a | b).sum()
    return float(inter / uni) if uni else 0.0


def score_line(ref: np.ndarray, text: str, font_path: Path, aspect_tol=1.0) -> dict:
    """在同一目标尺寸下比 IoU。字号按参考行高反算，几何比例也作为一项。"""
    rh, rw = ref.shape
    best = {"iou": 0.0}
    ink_ref = ref.mean()
    for size in range(max(10, int(rh * 0.6)), int(rh * 2.2) + 1):
        m = render_mask(text, font_path, size)
        m, info = tight(m)
        if not info:
            continue
        # 长宽比差太多直接跳过（同一串字，换字体宽度变化不该超过 25%）
        if not (0.75 < (info["w"] / info["h"]) / (rw / rh) < 1.35):
            continue
        s = iou(ref, m)
        if s > best["iou"]:
            best = {"iou": s, "size": size, "aspect": (info["w"] / info["h"]) / (rw / rh),
                    "ink_ratio": info["ink"] / (info["w"] * info["h"])}
    best["ink_ratio_ref"] = round(float(ink_ref), 3)
    return best


WEIGHTS = {"Regular", "Medium", "SemiBold", "Bold", "Black", "ExtraBold", "Heavy", "Light"}


def family_of(name: str) -> str:
    """字体族：去掉末尾的字重词。

    为什么要按族判：思源黑体 Medium / SemiBold / Bold 是同一套骨架，
    三者的 IoU 只差 0.03——**判字重是在判噪声，判族才是有意义的结论**。
    """
    parts = NAME_MAP.get(name, name).split()
    if len(parts) > 1 and parts[-1] in WEIGHTS | {"Bold（Noto", "Sans"}:
        return " ".join(parts[:-1])
    return NAME_MAP.get(name, name)


def main() -> None:
    ap = argparse.ArgumentParser(description="认出成片里字幕用的是哪一种字体（IoU 模板匹配）")
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--t", required=True, type=float, help="取帧时间（秒）")
    ap.add_argument("--text", required=True, help="该帧的字幕文字；多行用 | 分隔")
    ap.add_argument("--band", nargs=2, type=float, default=[0.55, 1.0],
                    help="只在画幅的这个纵向区间找字（比例）。默认 0.55–1.0")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--crop-out", type=Path, help="把参考字幕行裁出来存成图，方便人眼复核")
    a = ap.parse_args()

    im = grab_frame(a.video, a.t)
    W, H = im.size
    mask = white_mask(im)
    y0, y1 = int(a.band[0] * H), int(a.band[1] * H)
    mask[:y0] = False
    mask[y1:] = False
    boxes = line_boxes(mask)
    lines = [t for t in a.text.split("|") if t.strip()]
    print(f"画面 {W}×{H} · {a.t}s · 找到 {len(boxes)} 行白字 · 期望 {len(lines)} 行")
    if len(boxes) != len(lines):
        print(f"！行数对不上，按最多能配上的行比。修 --band 或换一帧。")

    results: dict[str, list[dict]] = {}
    for i, (by0, by1) in enumerate(boxes[: len(lines)]):
        sub = mask[by0:by1 + 1]
        ref, info = tight(sub)
        if not info:
            continue
        print(f"  第 {i+1} 行「{lines[i]}」 参考外框 {info['w']}×{info['h']}px · 墨迹占比 {info['ink_ratio'] if 'ink_ratio' in info else round(info['ink']/(info['w']*info['h']),3)}")
        if a.crop_out and i == 0:
            xs = np.where(sub.any(0))[0]
            im.crop((int(xs.min()) - 8, by0 - 8, int(xs.max()) + 9, by1 + 9)).resize(
                ((int(xs.max()) - int(xs.min()) + 17) * 4, (by1 - by0 + 17) * 4), Image.LANCZOS
            ).save(a.crop_out)
        for name in DEFAULT_CANDIDATES:
            fp = find_font(name)
            if not fp:
                continue
            r = score_line(ref, lines[i], fp)
            if r["iou"]:
                results.setdefault(name, []).append(r)

    rows = []
    for name, rs in results.items():
        if len(rs) < len(boxes[: len(lines)]):
            continue
        rows.append((sum(r["iou"] for r in rs) / len(rs), name, rs))
    rows.sort(reverse=True)
    if not rows:
        raise SystemExit("！没有候选字体能配上——换一帧，或放宽 --band")

    print(f"\n{'字体':<26}{'IoU':>7}{'墨迹占比':>9}{'参考墨迹':>9}  笔画")
    for avg, name, rs in rows[: a.top]:
        ink = sum(r["ink_ratio"] for r in rs) / len(rs)
        ink_ref = rs[0].get("ink_ratio_ref", 0)
        thick = "更粗" if ink > ink_ref + 0.015 else ("更细" if ink < ink_ref - 0.015 else "相当")
        print(f"{NAME_MAP.get(name, name):<26}{avg:>7.3f}{ink:>9.3f}{ink_ref:>9.3f}  {thick}")

    # 族级结论：同族不同字重只差 0.03，那是噪声
    fam: dict[str, tuple[float, str]] = {}
    for avg, name, rs in rows:
        f = family_of(name)
        if f not in fam or avg > fam[f][0]:
            fam[f] = (avg, name)
    fam_rows = sorted(((v[0], k, v[1]) for k, v in fam.items()), reverse=True)
    print("\n按字体族：")
    for avg, f, best_name in fam_rows[: a.top]:
        print(f"{f:<20}{avg:>7.3f}   （该族里最像的字重：{NAME_MAP.get(best_name, best_name)}）")

    top1, top2 = fam_rows[0][0], (fam_rows[1][0] if len(fam_rows) > 1 else 0)
    margin = top1 - top2
    print()
    if margin >= 0.06:
        print(f"→ 最像：{fam_rows[0][1]}（领先第二名 {margin:.3f}，可以认）")
    else:
        print(f"→ 认不出：前两名只差 {margin:.3f}。候选池里没有它，或者源分辨率不够——"
              f"别硬认，去扩候选池或换一帧更清楚的。")


if __name__ == "__main__":
    main()
