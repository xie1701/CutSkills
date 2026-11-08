#!/usr/bin/env python3
"""CutSkills · B-roll 图形卡生成器：三种可参数化的图形卡 → 透明 PNG 叠加层。

E 族（图文界面叙事）的卡不是贴纸，是**替观众搭一个能读的结构**：知识图谱、侧栏要点、
横向对比条。参考片里这些卡全是一次性手做的，换个选题就得重画；能参数化的只有**版式**
（列数、行数、比例、配色），版式错了，卡就只是在画面上占地方。

三种卡（参数都从参考片实测，字段表见 references/boroll-specs.md）：
  grid   5 列 × 4–7 行色块章节卡（参考片 70s 那张「一堂基本功清单」）
  panel  一侧图形 + 大字，另一侧留给人物（参考片 60–62s 的分屏）
  stats  横向对比条，按最大值归一化长度（画「进口贵 2–3 倍」这类事实）

三条硬规矩：
  1. **空间一律用比例**（占画幅宽/高的比值），没有写死的像素——换画幅只等比放大，版式不动。
  2. **字体只有一支**（阿里妈妈数黑 Bold）。取不到就报错退出，不静默替换：静默替换会让
     「黑体」这个前提当场失效，而黑体是这套卡可读性的一半。
  3. **块内字色按底色算**：深底白字、浅底黑字。色板是实测色（偏浅的绿在一堆块里非常明显），
     统一写死白字就会有一列糊掉。

    gen_boroll.py --kind grid  --spec /tmp/grid.json  --out /tmp/grid.png
    gen_boroll.py --kind panel --spec /tmp/panel.json --out /tmp/panel.png
    gen_boroll.py --kind stats --spec /tmp/stats.json --out /tmp/stats.png

输出的 PNG 是 RGBA，只有画到的地方有不透明像素，其余全透明，直接当 render_film 的叠加层。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 默认字：卡面上是**要读的结构**（列名、数字、脚注），不是标题。
# 换掉阿里妈妈数黑体的理由和字幕一样——它的小字笔画带装饰，投影到 1080 宽就成脏点。
FONT_DEFAULT = Path("~/Library/Fonts/NotoSansSC-Bold.ttf").expanduser()
W_DEFAULT, H_DEFAULT = 1080, 1920

# 参考片实测的五列配色（「一堂基本功清单」抽帧取样，红/黄/绿/浅蓝/深蓝）。
# 最后一个绿是浅绿：白字压不住，所以块内字色由亮度算，见 _ink()。
DEFAULT_PALETTE = ("#D4674E", "#CDA239", "#B5C293", "#45C3B5", "#3579B7")

# ---------------------------------------------------------------- 版式比例
# 全部是「占画幅」的比例；改这里等于改版式，改像素等于埋雷（换画幅就错位）。
GRID = {
    "card_x": (0.040, 0.960),   # 卡左右边（占 W）
    "card_max_h": 0.72,         # 卡最高（占 H）；超了就把块等比缩小，不是把卡撑出画
    "card_radius": 0.018,       # 卡圆角（占 W）· 族模板实测 1.5–2%W
    "card_fill": "#FFFFFF",
    "card_alpha": 0.94,
    "pad_x": 0.028,             # 卡内左右留白（占 W）
    "pad_y": 0.030,             # 卡内上下留白（占 H）
    "title_size": 0.032,        # 标题字号（占 H）
    "kicker_size": 0.0135,      # 英文副题（占 H）
    "header_size": 0.017,       # 列头（占 H）
    "axis_size": 0.015,         # 左侧竖轴标签（占 H）
    "foot_size": 0.0135,        # 底部说明（占 H）
    "block_size": 0.019,        # 块内字号上限（占 H）
    "block_min_size": 0.010,    # 再压就不可读，宁可报出来让人减字
    "gap_ratio": 0.18,          # 块间距 = gap_ratio × 块宽
    "block_aspect": 0.80,       # 块高 / 块宽
    "axis_band": 0.068,         # 左侧竖轴标签占的带宽（占 W）· 含标签到首列的呼吸
    "text_max": 0.94,           # 文字最多占容器的多少
}

PANEL = {
    "pad_x": 0.055,             # 面板内左右留白（占 W）
    "pad_y": 0.085,             # 面板内上下留白（占 H）
    "title_size": 0.045,        # 大字（占 H）——比 grid 的标题大，这是全卡的主角
    "bullet_size": 0.019,       # 要点字号（占 H），与 grid 块内同档
    "bullet_lead": 0.030,       # 要点行距（占 H）
    "graphic_size": 0.075,      # 图形边长（占 W）
    "graphic_gap": 0.028,       # 图形 → 标题的间距（占 H）
    "rule_h": 0.005,            # 标题下那根强调线（占 H）
    "rule_w": 0.30,             # 强调线长度（占面板宽）
    "radius": 0.0,              # 面板圆角（占 W）；贴边满高时为 0
    "fill": "#141414",
    "fill_alpha": 0.90,
    "accent": "#FFD400",        # 与 pack-warm-keyword 的强调黄同值
    "text": "#FFFFFF",
    "marker_gap": 0.020,        # 序号 → 文字的间距（占 W）
}

STATS = {
    "card_x": (0.080, 0.920),   # 卡左右边（占 W）
    "pad_x": 0.040,             # 卡内左右留白（占 W）
    "pad_y": 0.036,             # 卡内上下留白（占 H）
    "radius": 0.014,            # 卡圆角（占 W）
    "fill": "#FFFFFF",
    "fill_alpha": 0.94,
    "text": "#141414",
    "title_size": 0.032,        # 标题（占 H）
    "label_size": 0.019,        # 条上标签（占 H）
    "value_size": 0.030,        # 数值（占 H）
    "bar_h": 0.028,             # 条高（占 H）
    "label_gap": 0.008,         # 标签 → 条的间距（占 H）
    "row_gap": 0.026,           # 条与条之间（占 H）
    "title_gap": 0.020,         # 标题 → 第一条（占 H）
    "track_alpha": 0.22,        # 满量程细基线的透明度；0 = 不画基线
    "value_reserve": 0.16,      # 右侧给数值留的宽度（占卡宽）
}

_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


# ---------------------------------------------------------------- 小工具

def _color(value, alpha: int = 255) -> tuple[int, int, int, int]:
    """`#RRGGBB` / `#RGB` → (r, g, b, a)。透明度单独传，spec 里不写八位十六进制。"""
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise SystemExit(f"！颜色写法看不懂：{value!r}（要 #RRGGBB）")
    try:
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise SystemExit(f"！颜色写法看不懂：{value!r}（要 #RRGGBB）") from None
    return (r, g, b, alpha)


def _alpha(spec: dict, key: str, default: float) -> int:
    """0–1 的透明度 → 0–255。"""
    v = spec.get(key)
    v = default if v is None else v
    return int(round(max(0.0, min(1.0, float(v))) * 255))


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    key = (str(path), int(size))
    f = _FONTS.get(key)
    if f is None:
        f = ImageFont.truetype(str(path), int(size))
        _FONTS[key] = f
    return f


def _ink(fill: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """深底白字、浅底黑字。亮度用 BT.601 权重——浅绿（#B5C293）这类块全靠这条才看得清。"""
    r, g, b = fill[:3]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return (255, 255, 255, 255) if lum < 150 else (17, 17, 17, 255)


def _fit(font_path: Path, text: str, target_px: float, max_w: float,
         min_px: float = 10) -> tuple[ImageFont.FreeTypeFont, int]:
    """字号从 target 往下压到放得进 max_w；压到 min_px 还放不下就用 min_px。"""
    px = max(int(min_px), int(round(target_px)))
    floor = max(int(min_px), 6)
    while px > floor and _font(font_path, px).getlength(text) > max_w:
        px -= 1
    return _font(font_path, px), px


def _num(spec: dict, key: str, default: float) -> float:
    v = spec.get(key)
    return float(default) if v is None else float(v)


def load_spec(path: Path) -> dict:
    """JSON 或 YAML（`.yaml`/`.yml` 走 PyYAML，仓库本来就依赖它）。"""
    if not path.exists():
        raise SystemExit(f"！spec 不存在：{path}")
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(raw)
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SystemExit(f"！spec 不是合法 JSON：{path} 第 {e.lineno} 行 {e.msg}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"！spec 顶层必须是对象（字段表见 references/boroll-specs.md）：{path}")
    return data


# ---------------------------------------------------------------- 1 · grid 色块章节卡

def draw_grid(spec: dict, W: int, H: int, font_path: Path) -> tuple[Image.Image, dict]:
    """5 列 × 4–7 行的色块网格：标题 / 块 / 列头 / 左侧竖轴 / 底部说明。

    块是**实心直角矩形**，没有圆角也没有描边——参考片里它靠间距和色相分列，不靠线和角。
    列高不齐（4–7 行）是正常的：短列顶部对齐，空出来的地方就是「还没长出来」。
    """
    title = str(spec.get("title") or "")
    kicker = str(spec.get("kicker") or "")
    columns = [str(c) for c in (spec.get("columns") or [])]
    blocks = spec.get("blocks") or []
    axis = str(spec.get("axis") or "")
    footnote = str(spec.get("footnote") or "")

    if not columns or not blocks:
        raise SystemExit("！grid 卡缺 columns / blocks：空网格没有意义")
    if len(blocks) != len(columns):
        raise SystemExit(f"！columns 有 {len(columns)} 列，blocks 有 {len(blocks)} 组，对不上")
    for i, col in enumerate(blocks):
        if not isinstance(col, list) or not col:
            raise SystemExit(f"！第 {i + 1} 列（{columns[i]}）一块都没有")
        if not 4 <= len(col) <= 7:
            print(f"！第 {i + 1} 列（{columns[i]}）有 {len(col)} 行，参考片实测 4–7 行",
                  file=sys.stderr)
    palette = [_color(c) for c in (spec.get("colors") or DEFAULT_PALETTE)]
    if len(palette) < len(columns):
        raise SystemExit(f"！colors 给了 {len(palette)} 个，列有 {len(columns)} 列")
    legend = spec.get("legend")   # 可选：每列角标（参考片的「研发中」浅色说明不画，留给人写 footnote）

    rows = max(len(b) for b in blocks)
    title_px = _num(spec, "title_size", GRID["title_size"]) * H
    kicker_px = GRID["kicker_size"] * H
    header_px = GRID["header_size"] * H
    foot_px = GRID["foot_size"] * H
    axis_px = GRID["axis_size"] * H
    block_target = _num(spec, "block_size", GRID["block_size"]) * H
    block_min = GRID["block_min_size"] * H
    gap_ratio = _num(spec, "gap_ratio", GRID["gap_ratio"])
    aspect = _num(spec, "block_aspect", GRID["block_aspect"])

    x0, x1 = (int(GRID["card_x"][0] * W), int(GRID["card_x"][1] * W))
    pad_x, pad_y = int(GRID["pad_x"] * W), int(GRID["pad_y"] * H)
    max_h = int(GRID["card_max_h"] * H)
    title_band = int(title_px * 1.55) if title else 0
    kicker_band = int(kicker_px * 2.0) if kicker else 0
    axis_w = int(GRID["axis_band"] * W) if axis else 0
    header_band = int(header_px * 2.0)
    foot_band = int(foot_px * 3.0) if footnote else 0

    avail_w = (x1 - x0) - 2 * pad_x - axis_w
    bw = avail_w / (len(columns) + (len(columns) - 1) * gap_ratio)
    bh = bw * aspect
    gx, gy = bw * gap_ratio, bh * gap_ratio

    def grid_h(b: float, h: float, g: float) -> float:
        return rows * h + (rows - 1) * g

    # 卡比画幅高就等比缩块（不是把卡撑出画）：宽度是硬约束，只能整体乘一个系数。
    fixed = pad_y * 2 + title_band + kicker_band + header_band + foot_band
    need = grid_h(bw, bh, gy)
    if fixed + need > max_h:
        k = (max_h - fixed) / need
        bw, bh, gx, gy = bw * k, bh * k, gx * k, gy * k
    card_w = x1 - x0
    grid_h_px = int(round(grid_h(bw, bh, gy)))
    card_h = int(round(fixed + grid_h_px))
    cy0 = (H - card_h) // 2

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    card_fill = spec.get("card_fill", GRID["card_fill"])
    if card_fill is not None and str(card_fill).strip():
        d.rounded_rectangle([x0, cy0, x1, cy0 + card_h],
                            radius=int(GRID["card_radius"] * W),
                            fill=_color(card_fill, _alpha(spec, "card_alpha", GRID["card_alpha"])))

    cx = (x0 + x1) // 2
    ink = _color(spec.get("title_color", "#141414"))
    y = cy0 + pad_y

    if title:
        f, _ = _fit(font_path, title, title_px, card_w - 2 * pad_x)
        d.text((cx, y + title_band // 2), title, font=f, fill=ink, anchor="mm")
    y += title_band
    if kicker:
        f, _ = _fit(font_path, kicker, kicker_px, card_w - 2 * pad_x)
        d.text((cx, y + kicker_band // 2), kicker, font=f,
               fill=_color("#7A7A7A"), anchor="mm")
    y += kicker_band

    grid_top = y
    x_left = x0 + pad_x + axis_w
    # 一套网格里字号必须**统一**：逐块各自压字号，4 字块比 5 字块大一圈，整张清单立刻像贴的。
    # 所以先按最长的那个短语定字，全部块用同一个字号——代价是短块的留白多，这正是清单该有的样子。
    block_px = min(_fit(font_path, str(t), block_target, bw * GRID["text_max"], block_min)[1]
                   for col in blocks for t in col)
    f_block = _font(font_path, block_px)
    for ci, col in enumerate(blocks):
        bx = x_left + ci * (bw + gx)
        fill = palette[ci]
        color_text = _ink(fill)
        for ri, text in enumerate(col):
            by = grid_top + ri * (bh + gy)
            d.rectangle([round(bx), round(by), round(bx + bw), round(by + bh)], fill=fill)
            d.text((bx + bw / 2, by + bh / 2), str(text), font=f_block, fill=color_text,
                   anchor="mm")
        if legend and ci < len(legend):
            f, _ = _fit(font_path, str(legend[ci]), header_px * 0.8, bw)
            d.text((bx + bw / 2, grid_top + grid_h_px - 2), str(legend[ci]), font=f,
                   fill=_color("#8A8A8A"), anchor="md")
    grid_bottom = grid_top + grid_h_px

    # 列头在网格下方（参考片实测），每列居中钉在自己的列宽里
    for ci, name in enumerate(columns):
        bx = x_left + ci * (bw + gx)
        f, _ = _fit(font_path, name, header_px, bw * 1.05)
        d.text((bx + bw / 2, grid_bottom + header_band / 2), name, font=f, fill=ink, anchor="mm")

    # 左侧竖轴：整串转 90°（逆时针）→ 从下往上读，"打基础" 在底、"拿结果" 在顶
    if axis:
        f, ax_px = _fit(font_path, axis, axis_px, grid_h_px * 0.95)
        tw = int(f.getlength(axis)) + 8
        strip = Image.new("RGBA", (tw, int(ax_px * 1.6)), (0, 0, 0, 0))
        sd = ImageDraw.Draw(strip)
        sd.text((tw // 2, strip.height // 2), axis, font=f,
                fill=_color(spec.get("axis_color", "#3A3A3A"), 230), anchor="mm")
        strip = strip.rotate(90, expand=True)
        # 标签右缘离首列至少留 0.014W：竖排字紧贴色块，整张卡立刻显脏
        gap = int(0.014 * W)
        sx = x0 + pad_x + max(0, axis_w - gap - strip.width)
        img.paste(strip, (sx, grid_top + (grid_h_px - strip.height) // 2), strip)

    if footnote:
        f, _ = _fit(font_path, footnote, foot_px, card_w - 2 * pad_x)
        d.text((x0 + pad_x, cy0 + card_h - pad_y * 0.5),
               footnote, font=f, fill=_color("#6E6E6E"), anchor="ls")

    info = {
        "card": f"{len(columns)} 列 × {rows} 行",
        "count": sum(len(c) for c in blocks),
        "rows": rows,
        "fit_px": block_px,
        "fit_ratio": block_px / H,
        "target_ratio": block_target / H,
    }
    return img, info


# ---------------------------------------------------------------- 2 · panel 侧栏图形卡

def draw_panel(spec: dict, W: int, H: int, font_path: Path) -> tuple[Image.Image, dict]:
    """一侧面板：图形 + 大字 + 3–5 行要点；另一侧**全是透明**，人物在那半边。

    面板贴边满高时圆角为 0——圆角贴边会露出一圈背景，分屏立刻就假了。
    """
    side = str(spec.get("side") or "left").lower()
    if side not in ("left", "right"):
        raise SystemExit(f"！side 只能是 left / right，给的是 {side!r}")
    title = str(spec.get("title") or "")
    points = spec.get("points") or spec.get("bullets") or []
    if not isinstance(points, list) or not 3 <= len(points) <= 5:
        raise SystemExit(f"！panel 卡要点要 3–5 行（参考片实测），给的是 {len(points)} 行")

    wr = _num(spec, "width_ratio", 0.42)
    hr = _num(spec, "height_ratio", 1.0)
    if not 0.20 <= wr <= 0.60:
        raise SystemExit(f"！width_ratio 要在 0.20–0.60（再宽就没人物位了），给的是 {wr}")
    pw, ph = int(round(wr * W)), int(round(hr * H))
    margin = int(round(_num(spec, "margin_x", 0.0) * W))
    px0 = margin if side == "left" else W - margin - pw
    py0 = (H - ph) // 2

    fill = _color(spec.get("fill", PANEL["fill"]), _alpha(spec, "fill_alpha", PANEL["fill_alpha"]))
    accent = _color(spec.get("accent", PANEL["accent"]))
    text_c = _color(spec.get("text", PANEL["text"]))
    title_c = _color(spec.get("title_color") or spec.get("accent", PANEL["accent"]))
    radius = int(round(_num(spec, "radius", PANEL["radius"]) * W))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([px0, py0, px0 + pw - 1, py0 + ph - 1], radius=radius, fill=fill)

    pad_x, pad_y = int(PANEL["pad_x"] * W), int(PANEL["pad_y"] * H)
    cx0 = px0 + pad_x
    inner_w = pw - 2 * pad_x

    # 先量尺寸再落笔：面板是满高的，内容全顶在上面会留半屏空底，所以整块内容垂直居中。
    graphic = str(spec.get("graphic") or "square").lower()
    if graphic not in ("square", "circle", "bar", "none"):
        raise SystemExit(f"！graphic 只能是 square / circle / bar / none，给的是 {graphic!r}")
    gs = int(_num(spec, "graphic_size", PANEL["graphic_size"]) * W)
    graphic_h = 0 if graphic == "none" else gs + int(PANEL["graphic_gap"] * H)

    title_px = _num(spec, "title_size", PANEL["title_size"]) * H
    bullet_px = _num(spec, "bullet_size", PANEL["bullet_size"]) * H
    lead = _num(spec, "bullet_lead", PANEL["bullet_lead"]) * H
    rule_h = max(2, int(PANEL["rule_h"] * H))
    fitted: list[int] = []

    f_title, tpx = _fit(font_path, title, title_px, inner_w) if title else (None, 0)
    title_h = (int(tpx * 1.30) + rule_h + int(lead * 0.9)) if title else 0
    content_h = graphic_h + title_h + len(points) * (lead + bullet_px * 0.55)
    y = py0 + max(pad_y, (ph - content_h) // 2)

    # 图形：正方形 / 圆 / 竖条。用几何体，不用图标——图标要靠字体或素材，这里两样都没有
    if graphic == "square":
        d.rectangle([cx0, y, cx0 + gs, y + gs], fill=accent)
    elif graphic == "circle":
        d.ellipse([cx0, y, cx0 + gs, y + gs], fill=accent)
    elif graphic == "bar":
        d.rectangle([cx0, y, cx0 + max(6, int(gs * 0.30)), y + gs], fill=accent)
    y += graphic_h

    if title:
        fitted.append(tpx)
        d.text((cx0, y), title, font=f_title, fill=title_c, anchor="la")
        y += int(tpx * 1.30)
        d.rectangle([cx0, y, cx0 + int(inner_w * PANEL["rule_w"]), y + rule_h], fill=accent)
        y += rule_h + int(lead * 0.9)

    numbered = str(spec.get("bullet_style") or "number").lower() != "square"
    marker_w = int(PANEL["marker_gap"] * W * (1.6 if numbered else 1.0))
    for i, point in enumerate(points):
        text = str(point)
        if numbered:
            f, _ = _fit(font_path, str(i + 1), bullet_px, marker_w * 0.9)
            d.text((cx0, y + lead / 2), str(i + 1), font=f, fill=accent, anchor="lm")
        else:
            sq = int(bullet_px * 0.42)
            d.rectangle([cx0, y + lead / 2 - sq // 2, cx0 + sq, y + lead / 2 + sq // 2], fill=accent)
        f, px = _fit(font_path, text, bullet_px, inner_w - marker_w)
        fitted.append(px)
        d.text((cx0 + marker_w, y + lead / 2), text, font=f, fill=text_c, anchor="lm")
        y += lead + bullet_px * 0.55

    info = {
        "card": f"{side} 侧 · {wr:.2f}W × {hr:.2f}H",
        "count": len(points),
        "rows": len(points),
        "fit_px": min(fitted) if fitted else 0,
        "fit_ratio": (min(fitted) / H) if fitted else 0.0,
        "target_ratio": bullet_px / H,
    }
    return img, info


# ---------------------------------------------------------------- 3 · stats 对比条

def draw_stats(spec: dict, W: int, H: int, font_path: Path) -> tuple[Image.Image, dict]:
    """2–4 根横向对比条，按最大值归一化长度——「贵 2–3 倍」这种事实，条长就是倍数。

    满量程画成条底下一条细基线：没有它就只剩相对长度，看不出「离顶还有多远」。
    """
    title = str(spec.get("title") or "")
    items = spec.get("items") or spec.get("bars") or []
    if not isinstance(items, list) or not 2 <= len(items) <= 4:
        raise SystemExit(f"！stats 卡要 2–4 根条（参考片实测），给的是 {len(items)} 根")
    parsed = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or "value" not in it:
            raise SystemExit(f"！第 {i + 1} 根条要 {{\"label\", \"value\"}}：{it!r}")
        parsed.append({
            "label": str(it.get("label") or ""),
            "value": float(it["value"]),
            "text": str(it.get("text") or ""),
            "color": it.get("color"),
        })
    unit = str(spec.get("unit") or "")
    fmt = str(spec.get("value_format") or "{value:g}{unit}")
    vmax = spec.get("max")
    vmax = max(float(vmax), 1e-9) if vmax is not None else max(p["value"] for p in parsed)
    if vmax <= 0:
        raise SystemExit("！stats 的 value 全是 0 / 负数，归一化没有意义")

    x0, x1 = int(STATS["card_x"][0] * W), int(STATS["card_x"][1] * W)
    pad_x, pad_y = int(STATS["pad_x"] * W), int(STATS["pad_y"] * H)
    title_px = _num(spec, "title_size", STATS["title_size"]) * H
    label_px = _num(spec, "label_size", STATS["label_size"]) * H
    value_px = _num(spec, "value_size", STATS["value_size"]) * H
    bar_h = int(_num(spec, "bar_h", STATS["bar_h"]) * H)
    label_gap = int(_num(spec, "label_gap", STATS["label_gap"]) * H)
    row_gap = int(_num(spec, "row_gap", STATS["row_gap"]) * H)
    title_gap = int(_num(spec, "title_gap", STATS["title_gap"]) * H)
    card_w = x1 - x0
    inner_w = card_w - 2 * pad_x
    palette = [_color(c) for c in (spec.get("colors") or DEFAULT_PALETTE)]

    value_txt = [p["text"] or fmt.format(value=p["value"], unit=unit) for p in parsed]
    reserve = int(inner_w * _num(spec, "value_reserve", STATS["value_reserve"]))
    bar_area = inner_w - reserve

    title_band = int(title_px * 1.5) if title else 0
    row_h = int(label_px * 1.15) + label_gap + bar_h
    card_h = int(pad_y * 2 + title_band + len(parsed) * row_h + (len(parsed) - 1) * row_gap
                 + (title_gap if title else 0))
    cy0 = (H - card_h) // 2
    cy1 = cy0 + card_h

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = spec.get("fill", STATS["fill"])
    if fill is not None and str(fill).strip():
        d.rounded_rectangle([x0, cy0, x1, cy1], radius=int(STATS["radius"] * W),
                            fill=_color(fill, _alpha(spec, "fill_alpha", STATS["fill_alpha"])))
    text_c = _color(spec.get("text", STATS["text"]))
    cx0 = x0 + pad_x
    y = cy0 + pad_y
    if title:
        f, _ = _fit(font_path, title, title_px, inner_w)
        d.text((cx0, y + title_band // 2), title, font=f, fill=text_c, anchor="lm")
        y += title_band + title_gap

    track_a = _alpha(spec, "track_alpha", STATS["track_alpha"])
    fitted: list[int] = []
    for i, p in enumerate(parsed):
        color = _color(p["color"]) if p["color"] else palette[i % len(palette)]
        f, lpx = _fit(font_path, p["label"], label_px, inner_w - reserve)
        fitted.append(lpx)
        d.text((cx0, y), p["label"], font=f, fill=text_c, anchor="la")
        by0 = y + int(label_px * 1.15) + label_gap
        # 满量程只画成条底下一条细基线，不画整条底槽：底槽和实心条并排，会被读成「两种颜色堆叠」，
        # 观众量到的是「段」而不是「长度」，这里最怕的就是这个。
        if track_a > 0:
            d.rectangle([cx0, by0 + bar_h, cx0 + bar_area, by0 + bar_h + max(2, int(bar_h * 0.16))],
                        fill=_color("#000000", track_a))
        ln = max(1, int(round(bar_area * min(1.0, p["value"] / vmax))))
        d.rectangle([cx0, by0, cx0 + ln, by0 + bar_h], fill=color)
        vf, _ = _fit(font_path, value_txt[i], value_px, reserve * 0.95)
        d.text((x1 - pad_x, by0 + bar_h / 2), value_txt[i], font=vf, fill=text_c, anchor="rm")
        y = by0 + bar_h + row_gap

    info = {
        "card": f"{len(parsed)} 根条",
        "count": len(parsed),
        "rows": len(parsed),
        "fit_px": min(fitted) if fitted else 0,
        "fit_ratio": (min(fitted) / H) if fitted else 0.0,
        "target_ratio": label_px / H,
        "normalize_max": vmax,
    }
    return img, info


# ---------------------------------------------------------------- 主流程

DRAWERS = {"grid": draw_grid, "panel": draw_panel, "stats": draw_stats}


def parse_canvas(text: str) -> tuple[int, int]:
    try:
        w, h = (int(part) for part in str(text).lower().replace("×", "x").split("x"))
    except ValueError:
        raise SystemExit(f"！画幅要写成 1080x1920，给的是 {text!r}") from None
    if w < 100 or h < 100:
        raise SystemExit(f"！画幅太小：{w}x{h}")
    return w, h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="用 PIL 画 B-roll 图形卡（透明 PNG 叠加层）：grid / panel / stats")
    ap.add_argument("--kind", required=True, choices=sorted(DRAWERS))
    ap.add_argument("--spec", required=True, help="JSON 或 YAML 参数文件（字段表见 references/boroll-specs.md）")
    ap.add_argument("--out", required=True, help="输出 PNG（RGBA，画幅外全透明）")
    ap.add_argument("--canvas", default=None, help="WxH，默认 1080x1920；spec 里也能写 canvas")
    ap.add_argument("--font", default=str(FONT_DEFAULT), help=f"字体，默认 {FONT_DEFAULT}")
    a = ap.parse_args(argv)

    font_path = Path(a.font).expanduser()
    if not font_path.is_file():
        raise SystemExit(f"！字体没找到：{font_path}\n"
                         f"  这套卡的可读性靠这支黑体（阿里妈妈数黑 Bold），不做静默替换。")

    spec_path = Path(a.spec).expanduser()
    spec = load_spec(spec_path)

    W, H = parse_canvas(a.canvas or spec.get("canvas") or f"{W_DEFAULT}x{H_DEFAULT}")
    img, info = DRAWERS[a.kind](spec, W, H, font_path)

    # 整幅底：这张卡是「一屏信息」，不是浮在画面上的贴纸。
    # 参考片那张图谱卡出现时，底下的实拍已经看不见了。不铺底的话，
    # 原有字幕和画面会从卡片四周露出来，同一屏出现两套字，读的人当场就乱。
    bd = spec.get("backdrop")
    if bd:
        from PIL import Image as _Im
        base = _Im.new("RGBA", (W, H),
                       _color(bd, int(round(float(spec.get("backdrop_alpha", 1.0)) * 255))))
        base.alpha_composite(img)
        img = base

    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)

    fit = info.get("fit_ratio") or 0.0
    hint = ""
    if fit and fit < info.get("target_ratio", 0) * 0.70:
        hint = f"！字被压到 {fit:.4f}H（目标 {info['target_ratio']:.4f}H）：块/槽太窄，减字或减列"
    print(f"{a.kind} · {out} · {W}×{H} {img.mode} · {info['card']} · {info['count']} 块"
          f" · 字号 {fit:.4f}H（目标 {info['target_ratio']:.4f}H）")
    if hint:
        print(hint, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
