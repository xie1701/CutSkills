#!/usr/bin/env python3
"""包装模板 → 渲染参数 + 壳体叠加层。

`styles/pack-*.yaml` 里的数字是**比例**（字高占画幅 4.5%、顶条占 5%H），
`render_film.py` 要的是**像素**（size_px、center_y_px）。中间少一层翻译，
模板就永远只是文档。这个文件就是那层翻译，只有两个出口：

    to_style(pack, W, H, FPS)   → render_film 认的 style dict
    shell_layers(pack, ...)     → 壳体的一叠全幅透明 PNG（顶条 / 角标 / 时间码）

为什么按比例存、按像素渲染：模板要跨 1080×1920 与 1280×720 都成立。
`workbuddy` 那片按像素写死的 5×21px 红竖条，换个分辨率就细到消失——
所以这里一律从比例反算，像素只是这一次画布的产物。

字号怎么定：pack 写的是「字形高度占画幅高度的比例」，字体文件给的是**字号**（em size），
两者差一个字形本身的上伸/下伸留白。直接 `size = ratio * H` 会画小 20–30%，
所以用 PIL 量一次真字形高度再反算（见 `size_for_glyph`）。这一步不能省：
省了以后模板里的 4.5%H 渲染出来是 3.1%H，"按模板做"和"照参考片做"就差出这一截。
"""
from __future__ import annotations

from pathlib import Path

import yaml

STYLES = Path(__file__).resolve().parent.parent / "styles"

# 字体族 → 本机字体名（render_film.find_font 按名字子串匹配）
FONT_CLASS = {
    "sans_heavy": "AlimamaShuHeiTi-Bold",     # 阿里妈妈数黑，免费商用
    "sans": "AlimamaShuHeiTi-Bold",
    "serif": "SourceHanSerifCN",              # 宋体；B 族签名
}

# 壳体的色板（取自参考片实测值，见 references/packaging-families.md）
AMBER = (232, 196, 67, 255)        # #E8C443 当前章节高亮
RED = (188, 52, 50, 255)           # #BC3432 时间码红点 / 极简锚点
WHITE = (255, 255, 255, 255)
DIM = (255, 255, 255, 150)


def load(name_or_path: str) -> dict:
    p = Path(name_or_path).expanduser()
    if not p.exists():
        p = STYLES / (name_or_path if name_or_path.endswith(".yaml") else f"{name_or_path}.yaml")
    if not p.exists():
        raise SystemExit(f"找不到包装模板：{name_or_path}（找过 {STYLES}）")
    return yaml.safe_load(p.read_text())


def find_font_file(name: str) -> Path | None:
    """和 render_film.find_font 同一套查找规则，这里单独实现一份是为了不循环 import。"""
    stem = name.replace(" ", "").lower()
    cands: list[Path] = []
    for d in (Path.home() / "Library/Fonts", Path("/Library/Fonts"),
              Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")):
        if d.exists():
            cands += [p for p in d.rglob("*") if p.suffix.lower() in {".otf", ".ttf", ".ttc"}]
    for p in cands:
        if stem in p.name.replace(" ", "").lower():
            return p
    for p in cands:
        n = p.name.lower()
        if "sourcehansanscn-heavy" in n or "alimama" in n or "pingfang" in n:
            return p
    return None


def size_for_glyph(font_path: Path, target_px: float) -> int:
    """反算字号：让一个汉字的**实际墨迹高度**等于 target_px。

    `getbbox("国")` 给的是墨迹框，不是 em 框——这正是 pack 里那句话的意思。
    """
    from PIL import ImageFont

    probe = 100
    f = ImageFont.truetype(str(font_path), probe)
    b = f.getbbox("国")
    ink = b[3] - b[1]
    if ink <= 0:
        return int(target_px)
    return max(8, int(round(target_px * probe / ink)))


def _stroke_px(stroke, H: int) -> int:
    """模板里的 stroke 有三种写法：dict（带 px_at_720h）、字符串 `none`、缺项。
    只有第一种要真描边；`none` 是**明确要求不描边**（B 族宋体），不能当默认值处理。
    """
    if not isinstance(stroke, dict):
        return 0
    return int(round(float(stroke.get("px_at_720h") or 0) * H / 720))


def to_style(pack: dict, W: int, H: int, FPS: int) -> dict:
    """包装模板 → render_film 的 style schema。

    缺项一律取模板里的中位值；模板没写的（响度、真峰）用 BRIEF 的硬指标，
    因为那是交付规格，不是风格。
    """
    if not pack.get("subtitle"):
        raise SystemExit(
            f"`{pack.get('name')}` 只提供壳（shell），不提供字幕系统。\n"
            "族可叠，但只允许「A/B/D 的字幕系统 + C 的壳 + E 的卡」——\n"
            "给它配一支字幕系统再渲染：--pack pack-safe-talking --shell "
            f"{pack.get('name')}")

    name = str(pack.get("name") or "pack")
    fam = str(pack.get("family") or "?")
    sub = pack.get("subtitle") or {}
    variant = (pack.get("variant_yield_to_footage") or {}) if sub.get("band_center_h") else {}
    band = float(sub.get("band_center_h") or variant.get("band_center_h") or 0.86)
    glyph = float(sub.get("glyph_h_ratio") or variant.get("glyph_h_ratio") or 0.045)
    chars = sub.get("chars_per_line") or [8, 12]
    if isinstance(chars, list):
        soft, hard = int(chars[0]), min(int(chars[0]) + 3, int(chars[-1]))
    else:
        soft = hard = int(chars)

    font_name = FONT_CLASS.get(str(sub.get("font_class") or "sans_heavy"), FONT_CLASS["sans_heavy"])
    font_path = find_font_file(font_name)
    size_px = size_for_glyph(font_path, round(glyph * H)) if font_path else int(glyph * H)

    tempo = pack.get("tempo") or {}
    med = tempo.get("shot_median_s") or [2.0, 4.8]
    med_s = float(med[0] if isinstance(med, list) else med) if not isinstance(med, list) \
        else round((float(med[0]) + float(med[1])) / 2, 2)

    safe_bottom = float(sub.get("safe_bottom_h") or 0.05)

    return {
        "name": name,
        "label": pack.get("label"),
        "family": fam,
        "canvas": {"width": W, "height": H, "fps": FPS},
        "subtitle": {
            "font": font_name,
            "size_px": size_px,
            "weight": str(sub.get("weight") or "bold"),
            "color": str(sub.get("color") or "#FFFFFF"),
            "center_y_px": int(round(band * H)),
            "safe_bottom_px": int(round(safe_bottom * H)),
            "max_chars_per_line": hard,
            "chars_soft": soft,          # 到了这个字数、后面又有小停顿就断（密度靠它）
            "hold_min_s": 0.75,
            "hold_max_s": 2.4,
            "band_center_h": round(band, 4),
            "glyph_h_ratio": round(glyph, 4),
            # 描边/投影二选一这条铁律落成渲染参数：模板里有 stroke 就只用描边。
            # 模板里 `stroke: none` 是字符串，不是缺项——B 族就是靠这一句规定
            # 「宋体一律用投影」，写成 None 就会被当成「有配置」而描一圈边。
            "stroke_px": _stroke_px(sub.get("stroke"), H),
            "shadow": sub.get("shadow") is not None and _stroke_px(sub.get("stroke"), H) == 0,
            "variant": "yield_to_footage" if variant else None,
        },
        "shots": {"talking_median_s": med_s},
        "sfx": {"density_per_min": tempo.get("sfx_density_per_min") or [0, 8]},
        "audio": {"loudness_lufs": -14, "true_peak_dbtp": -1, "cut_fade_ms": 30},
        "card": {"bottom_edge_max_px": int(round(0.90 * H))},
    }


# ---------------------------------------------------------------- 壳体

def _bar_png(W: int, H: int, pack: dict, font_path: Path | None, meta: dict,
             out: Path) -> Path:
    """顶条：一条通栏黑条 + 条目 + 右侧 REC 时间码。

    条目数、字高、条高全部来自模板；`active` 决定哪一条是高亮色。
    时间码跟着成片时间走（meta["t"]），不是跟着素材走——壳是"正在录制"的假象，
    它要和服务器的墙钟没关系，必须和观众看到的秒数对齐。
    """
    from PIL import Image, ImageDraw, ImageFont

    sh = (pack.get("shell") or {}).get("bar") or {}
    h_bar = max(8, int(round(float(sh.get("height_h") or 0.05) * H)))
    type_h = float((pack.get("shell") or {}).get("badge", {}).get("type_scale_h")
                   or (pack.get("shell") or {}).get("bar", {}).get("type_scale_h") or 0.014)
    size = size_for_glyph(font_path, round(type_h * H)) if font_path else int(type_h * H)
    f = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()

    # 全幅画布，条只占顶部 h_bar——因为叠加用的是 `overlay=0:0`，
    # 一次贴上去，字幕带/字卡各贴各的，互不干扰。
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, h_bar], fill=(0, 0, 0, 232))

    cy = h_bar / 2
    x = int(0.03 * W)                                    # 四角安全留白 3%W
    title = str(meta.get("title") or "")
    if title:
        d.text((x, cy), title, font=f, fill=DIM, anchor="lm")
        x += d.textlength(title + "   ", font=f)

    entries = [str(e) for e in (meta.get("entries") or [])]
    active = int(meta.get("active")) if meta.get("active") is not None else -1
    for i, e in enumerate(entries):
        col = AMBER if i == active else DIM
        d.text((x, cy), e, font=f, fill=col, anchor="lm")
        x += d.textlength(e, font=f)
        if i != len(entries) - 1:
            d.text((x + 8, cy), "·", font=f, fill=(255, 255, 255, 80), anchor="lm")
            x += d.textlength("·", font=f) + 16

    # 右侧时间码：红点 + REC + HH:MM:SS。只画在条内，不进画面。
    t = float(meta.get("t") or 0)
    tc = f"REC 00:{int(t) // 60:02d}:{int(t) % 60:02d}"
    tw = d.textlength(tc, font=f)
    tx = W - int(0.03 * W) - tw
    d.text((tx, cy), tc, font=f, fill=DIM, anchor="lm")
    r = max(3, int(round(type_h * H * 0.45)))
    d.ellipse([tx - r * 3, cy - r, tx - r, cy + r], fill=RED)

    p = out / "bar.png"
    img.save(p)
    return p


def _badge_png(W: int, H: int, pack: dict, font_path: Path | None, meta: dict,
               out: Path) -> Path:
    """角标：左上、落在 11.6–14%H。只写一行字，不加框不加底——加了就是第二个壳。"""
    from PIL import Image, ImageDraw, ImageFont

    sh = pack.get("shell") or {}
    bd = sh.get("badge") or {}
    y0, _ = (bd.get("y_h") or [0.116, 0.14])[:2]
    type_h = float(bd.get("type_scale_h") or 0.014)
    size = size_for_glyph(font_path, round(type_h * H)) if font_path else int(type_h * H)
    f = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text = str(meta.get("badge") or "// REEL 01")
    x = int(float(bd.get("corner_inset_w") or 0.03) * W)
    y = int(float(y0) * H)
    # 角标压在任意画面上都需要自保：一行细描边，不用底衬（底衬会在浅画面上显成一块脏斑）
    d.text((x, y), text, font=f, fill=WHITE, anchor="la", stroke_width=2,
           stroke_fill=(0, 0, 0, 200))
    p = out / "badge.png"
    img.save(p)
    return p


def shell_layers(pack: dict, W: int, H: int, meta: dict, out_dir: Path) -> list[dict]:
    """壳体的叠加层清单。每层是一张全幅透明 PNG + 一个时间窗。

    `meta["shots"]` 给了每个镜头在成片里的起点/时长（秒）；时间码要逐镜重画，
    因为一张静止 PNG 里的数字不会自己走。不逐镜重画的后果很具体：
    10 秒的片，时间码从头到尾停在 00:00——观众第一眼就看出这是个贴图。
    """
    shell = pack.get("shell") or {}
    kinds = shell.get("type") or []
    if isinstance(kinds, str):
        kinds = [kinds]
    font_name = FONT_CLASS.get(str((pack.get("subtitle") or {}).get("font_class") or "sans"), "AlimamaShuHeiTi-Bold")
    font_path = find_font_file(font_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    layers: list[dict] = []
    shots = meta.get("shots") or [{"start": 0.0, "dur": float(meta.get("total") or 0)}]
    for i, s in enumerate(shots):
        win = {"start": round(float(s["start"]), 3),
               "end": round(float(s["start"]) + float(s["dur"]), 3)}
        if "top_bar" in kinds:
            d = out_dir / f"bar{i:03d}"
            d.mkdir(parents=True, exist_ok=True)
            # 条目随章节推进：每个镜头把 active 往后挪一格，观众能看出"在往下走"
            m = dict(meta)
            m["t"] = win["start"]
            # 当前章节：优先用镜头自带的 active（来自 EDL 的 beat），没有才轮转。
            # 轮转只是兜底——真片子里章节是按内容推进的，不是每 3 秒换一格。
            if s.get("active") is not None:
                m["active"] = int(s["active"])
            elif m.get("active") is None and meta.get("entries"):
                m["active"] = i % len(meta["entries"])
            layers.append({"path": str(_bar_png(W, H, pack, font_path, m, d)), "y": 0,
                           "name": "shell:top_bar", **win})
        if "corner_badge" in kinds:
            m = dict(meta)
            m.pop("badge", None) if not meta.get("badge") else None
            d = out_dir / f"badge{i:03d}"
            d.mkdir(parents=True, exist_ok=True)
            layers.append({"path": str(_badge_png(W, H, pack, font_path, m, d)), "y": 0,
                           "name": "shell:corner_badge", **win})
    return layers


def main() -> int:
    """命令行自检：`pack_style.py --pack pack-safe-talking --canvas 1080x1920`
    打印翻译后的像素参数。模板改完先跑这一行，比渲一遍便宜得多。"""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--canvas", default="1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    w, h = (int(x) for x in a.canvas.lower().split("x"))
    st = to_style(load(a.pack), w, h, a.fps)
    sub = st["subtitle"]
    print(f"{st['name']}（{st['label']}）@ {w}×{h} {a.fps}fps")
    print(f"  字形墨迹高 = {sub['glyph_h_ratio']:.3f}×H = {round(sub['glyph_h_ratio'] * h)}px"
          f"  → 字号 {sub['size_px']}px（字体 {sub['font']}）")
    print(f"  字幕带中心 = {sub['band_center_h']:.3f}×H = {sub['center_y_px']}px")
    print(f"  每行 {sub['chars_soft']}–{sub['max_chars_per_line']} 字  "
          f"下沿距底 ≥{sub['safe_bottom_px']}px  描边 {sub['stroke_px']}px "
          f"投影 {'有' if sub['shadow'] else '无'}")
    print(f"  镜头中位 {st['shots']['talking_median_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
