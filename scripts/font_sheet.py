#!/usr/bin/env python3
"""font_sheet.py — 把同一句字幕排成 N 种字体的一张对照卡，让人眼当场判。

为什么要有这个：字号、位置、描边宽度都能量，**字形的味道量不出来**。
`match_font.py` 想从参考片反推字体，但 576 宽的源里字高只有 29 像素，
IoU 拉到 0.75 已经是上限，认不准。最后能落地的办法只有一张对照卡：
同样的字、同样的字高、同样的描边，摆在一起看。

用法：
  python3 font_sheet.py --out /tmp/fonts.png                       # 默认候选
  python3 font_sheet.py --text "不管珠海的、广州的、上海" --w 1080 --glyph 54
  python3 font_sheet.py --ref /tmp/ref_line.png                    # 顶部加一条参考片字样
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PINGFANG = Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
                "86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc")
FONTS = Path("~/Library/Fonts").expanduser()

# (显示名, 路径, ttc 索引, 备注) —— 注释写清楚"它是什么位置的字"
CANDIDATES = [
    ("阿里妈妈数黑体 Bold", FONTS / "AlimamaShuHeiTi-Bold.otf", 0, "现在用的 · 标题美术字，笔画带装饰，长期做字幕不合适"),
    ("思源黑体 Bold", FONTS / "NotoSansSC-Bold.ttf", 0, "字幕的工业标准，字面均匀"),
    ("思源黑体 SemiBold", FONTS / "NotoSansSC-SemiBold.ttf", 0, "比 Bold 轻一档"),
    ("思源黑体 Black", FONTS / "NotoSansSC-Black.ttf", 0, "最重，花字可用"),
    ("苹方 SC Semibold", PINGFANG, 11, "苹果系统字，屏幕感强，略窄"),
    ("苹方 SC Medium", PINGFANG, 7, "苹方中黑"),
    ("方正兰亭中黑", FONTS / "方正兰亭中黑_GBK.TTF", 0, "传统印刷味，笔画末梢略收"),
    ("方正兰亭大黑", FONTS / "方正兰亭大黑_GBK.TTF", 0, "更重，适合大字"),
    ("中易黑体", FONTS / "simhei.ttf", 0, "Windows 老黑体，横竖均匀但偏方"),
    ("微软雅黑 Bold", FONTS / "Microsoft Yahei Bold.ttf", 0, "Windows 屏幕字，字面偏大"),
    ("思源宋体 Bold", FONTS / "NotoSerifSC-Bold.ttf", 0, "衬线，作对照（认出来说明片子是宋体风）"),
]


def load(spec) -> ImageFont.FreeTypeFont | None:
    _, path, idx, _ = spec
    if not Path(path).is_file():
        return None
    try:
        return ImageFont.truetype(str(path), 40, index=idx)
    except Exception:
        return None


def render_row(text: str, spec, w: int, glyph_h: int, stroke_h: int, bg, fg=(255, 255, 255)) -> Image.Image:
    """按"墨迹高度"排字：先量一行真字形的墨迹高，再反算字号。"""
    row_h = int(glyph_h * 2.6)
    img = Image.new("RGB", (w, row_h), bg)
    f0 = load(spec)
    if f0 is None:
        d = ImageDraw.Draw(img)
        d.text((24, row_h // 2 - 16), f"（本机没有这个字体：{spec[1].name}）", fill=(255, 90, 90))
        return img
    # 反算：目标墨迹高
    size = glyph_h
    for _ in range(6):
        f = ImageFont.truetype(str(spec[1]), size, index=spec[2])
        box = f.getbbox(text)
        h = box[3] - box[1]
        if h <= 0:
            break
        size = max(8, int(round(size * glyph_h / h)))
    f = ImageFont.truetype(str(spec[1]), size, index=spec[2])
    box = f.getbbox(text)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = (w - tw) // 2 - box[0]
    y = (row_h - th) // 2 - box[1]
    stroke = max(1, int(round(glyph_h * stroke_h)))
    d = ImageDraw.Draw(img)
    d.text((x, y), text, font=f, fill=fg, stroke_width=stroke, stroke_fill=(0, 0, 0))
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description="同一句字幕 × N 种字体的对照卡")
    ap.add_argument("--text", default="不管珠海的、广州的、上海的")
    ap.add_argument("--w", type=int, default=1080, help="画幅宽（按成片宽度给）")
    ap.add_argument("--glyph", type=int, default=54, help="目标墨迹高（像素）。1080 宽的下沿字幕约 54")
    ap.add_argument("--stroke", type=float, default=0.052, help="描边宽 / 墨迹高，默认 0.052")
    ap.add_argument("--bg", default=None, help="背景色 #RRGGBB；不给就上下两种（中灰 + 近黑）")
    ap.add_argument("--ref", type=Path, help="参考片字幕裁图，贴在最上面做基准")
    ap.add_argument("--out", type=Path, default=Path("/tmp/font-sheet.png"))
    a = ap.parse_args()

    rows = []
    bgs = [tuple(int(a.bg.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))] if a.bg else [(120, 118, 112), (28, 26, 24)]
    for spec in CANDIDATES:
        for bg in bgs:
            img = render_row(a.text, spec, a.w, a.glyph, a.stroke, bg)
            d = ImageDraw.Draw(img)
            label = f"{spec[0]} · {spec[3]}" if bg == bgs[0] else ""
            d.text((14, 10), label, font=ImageFont.truetype(str(Path("~/Library/Fonts/NotoSansSC-Medium.ttf").expanduser()), 20),
                   fill=(255, 226, 90) if bg == bgs[0] else bg)
            rows.append(img)

    head = 0
    ref_img = None
    if a.ref and a.ref.is_file():
        ref_img = Image.open(a.ref).convert("RGB")
        scale = a.w / ref_img.width
        ref_img = ref_img.resize((a.w, int(ref_img.height * scale)), Image.LANCZOS)
        head = ref_img.height

    H = head + sum(r.height for r in rows)
    sheet = Image.new("RGB", (a.w, H), (10, 10, 10))
    if ref_img is not None:
        sheet.paste(ref_img, (0, 0))
    y = head
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    sheet.save(a.out, quality=94)
    print(f"{a.out}  {sheet.width}×{sheet.height}  共 {len(CANDIDATES)} 种字体 × {len(bgs)} 种底色")


if __name__ == "__main__":
    main()
