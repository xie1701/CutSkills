#!/usr/bin/env python3
"""CutSkills · 包装样张：用 token 画四张「同一条片不同层」的示意帧，30 秒出全套。

为什么不是直接渲微样片：渲一支 6 秒片要等 HyperFrames，而且一改 token 就得重渲。
样张是**用 token 画的**，改 token 立刻重画，回答的是「这套包装长什么样」里最贵的那部分：
色调、字体、字幕系统、卡片气质、大字分量、版式。

样张四帧：
  01 口播帧     —— 底图（实拍占位）+ 字幕系统
  02 卡片帧     —— 一张信息卡（主题色、圆角、描边、阴影）
  03 大字帧     —— 大字层（人脑后/居中），验证「够不够撑」
  04 代码画面帧 —— 暗底 + 强调色 + 单排大字（B 轴大概长这样）

注意：样张**不能**证明动效和节奏，那是微样片和 10 秒试片的事。所以每张都标「样张」。

用法：
  render_style_preview.py --pack P1-warm [--pack P3-dark ...] [--out <目录>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1080, 1920
FONTS = {
    "NotoSansSC-Regular": "NotoSansSC-Regular.ttf",
    "NotoSansSC-SemiBold": "NotoSansSC-SemiBold.ttf",
    "NotoSansSC-Bold": "NotoSansSC-Bold.ttf",
    "NotoSansSC-Black": "NotoSansSC-Black.ttf",
    "NotoSerifSC-Regular": "NotoSerifSC-Regular.ttf",
    "NotoSerifSC-Bold": "NotoSerifSC-Bold.ttf",
}
FDIR = Path.home() / "Library/Fonts"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    f = FDIR / FONTS.get(name, name + ".ttf")
    if f.exists():
        try:
            return ImageFont.truetype(str(f), size)
        except OSError:
            pass
    for alt in ("NotoSansSC-Regular.ttf", "PingFang.ttc"):
        p = FDIR / alt if (FDIR / alt).exists() else Path("/System/Library/Fonts") / alt
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


def hexa(c: str, a: int = 255) -> tuple[int, int, int, int]:
    c = (c or "#000000").lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (r, g, b, a)


def film_grain(img: Image.Image, seed: int = 7) -> Image.Image:
    """底图给一点颗粒，免得看起来像纯色块——样张太干净反而不像实拍。"""
    import random
    rnd = random.Random(seed)
    px = img.load()
    for _ in range(9000):
        x, y = rnd.randrange(W), rnd.randrange(H)
        r, g, b, a = px[x, y]
        d = rnd.randint(-14, 14)
        px[x, y] = (max(0, min(255, r + d)), max(0, min(255, g + d)), max(0, min(255, b + d)), a)
    return img


def base_frame(pal: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), hexa(pal.get("ground", "#141414")))
    d = ImageDraw.Draw(img)
    # 底图占位：一块渐深的「实拍」区域，别画具体人（样张只验包装，不验内容）
    for i in range(H // 2, H):
        k = i / H
        c = hexa(pal.get("ground", "#141414"))
        d.line([(0, i), (W, i)], fill=(min(255, int(c[0] * 1.5 + 10)),
                                       min(255, int(c[1] * 1.4 + 8)),
                                       min(255, int(c[2] * 1.3 + 6)), 255))
    d.text((60, 90), "样张 · 只验包装（不是成片）", font=font("NotoSansSC-Regular", 34),
           fill=hexa(pal.get("ink", "#FFFFFF"), 150))
    return film_grain(img)


def layout_est(text: str, size: int) -> float:
    u = 0.0
    for ch in text:
        u += 0.0 if ch == " " else (1.0 if ord(ch) > 0x2E7F else 0.34)
    return u * size


def fit_size(text: str, max_px: float, want: int, floor: int = 40) -> int:
    s = want
    while s > floor and layout_est(text, s) > max_px:
        s -= 2
    return s


def draw_subtitle(img, pack: dict, text: str, en: str | None = None) -> None:
    sub = pack.get("subtitle") or {}
    tok = pack.get("tokens") or {}
    pal = tok.get("palette") or {}
    sy = pack.get("subtitle_system") or {}
    d = ImageDraw.Draw(img)
    cy = int(sub.get("center_y_px") or 1294)
    if sy.get("id") == "none":
        return
    body = (tok.get("type") or {}).get("body") or sub.get("font") or "NotoSansSC-Regular"
    size = fit_size(text, W * 0.92, int(sub.get("size_px") or 80))
    f = font(body, size)
    tw = layout_est(text, size)
    d.text(((W - tw) / 2, cy - size * 0.62), text, font=f,
           fill=hexa(sub.get("color") or pal.get("ink") or "#FFFFFF"))
    if sy.get("id") == "bilingual" and en:
        esz = int(sy.get("en_size_px") or 34)
        ef = font((tok.get("type") or {}).get("body") or "NotoSansSC-Regular", esz)
        ew = layout_est(en, esz)
        d.text(((W - ew) / 2, cy + size * 0.55), en, font=ef,
               fill=hexa(sy.get("en_color") or "#888888"))


def frame_subtitle(pack: dict) -> Image.Image:
    tok = pack.get("tokens") or {}
    img = base_frame(tok.get("palette") or {})
    draw_subtitle(img, pack, "柒造把石灰基做到墙上", "Qizao puts lime base on the wall")
    return img


def frame_card(pack: dict) -> Image.Image:
    tok = pack.get("tokens") or {}
    pal = tok.get("palette") or {}
    img = base_frame(pal)
    d = ImageDraw.Draw(img)
    r = int(tok.get("radius") or 12)
    stroke = int(tok.get("stroke") or 0)
    box = [90, 330, W - 90, 900]
    bg = hexa(pal.get("ground", "#111111"))
    bg = (min(255, bg[0] + 24), min(255, bg[1] + 22), min(255, bg[2] + 20), 235)
    d.rounded_rectangle(box, radius=r, fill=bg,
                        outline=hexa(pal.get("accent", "#F6EB34")) if stroke else None,
                        width=max(1, stroke))
    disp = (tok.get("type") or {}).get("display") or "NotoSansSC-Black"
    d.text((140, 380), "原料价 vs 成品价", font=font(disp, 62), fill=hexa(pal.get("ink", "#FFFFFF")))
    d.text((140, 480), "差 4–5 倍", font=font(disp, 150), fill=hexa(pal.get("accent", "#F6EB34")))
    d.text((140, 690), "比的对象换了，结论就变了", font=font((tok.get("type") or {}).get("body") or "NotoSansSC-Regular", 40),
           fill=hexa(pal.get("ink", "#FFFFFF"), 200))
    d.text((140, 800), "示意 · 无出处", font=font((tok.get("type") or {}).get("body") or "NotoSansSC-Regular", 30),
           fill=hexa(pal.get("accent2", "#C1121F")))
    draw_subtitle(img, pack, "比的不是同一个东西", "not comparing the same thing")
    return img


def frame_bigword(pack: dict) -> Image.Image:
    tok = pack.get("tokens") or {}
    pal = tok.get("palette") or {}
    img = base_frame(pal)
    d = ImageDraw.Draw(img)
    text = "石灰基"
    size = fit_size(text, W * 0.70, int((pack.get("emphasis") or {}).get("big_keyword_px", [260])[0]
                                        if isinstance((pack.get("emphasis") or {}).get("big_keyword_px"), list) else 260))
    f = font((tok.get("type") or {}).get("display") or "NotoSansSC-Black", size)
    tw = layout_est(text, size)
    d.text(((W - tw) / 2, 620 - size * 0.6), text, font=f, fill=hexa(pal.get("accent", "#F6EB34")))
    draw_subtitle(img, pack, "它不是涂料，是石头变的")
    return img


def frame_codepic(pack: dict) -> Image.Image:
    tok = pack.get("tokens") or {}
    pal = tok.get("palette") or {}
    img = Image.new("RGBA", (W, H), hexa(pal.get("ground", "#0B0C0E")))
    d = ImageDraw.Draw(img)
    # 一个极简的「墙 + 抹痕 + 金」示意，只验版式与色调
    acc = hexa(pal.get("accent", "#39D0C4"))
    ink = hexa(pal.get("ink", "#F5F7FA"))
    d.rectangle([140, 520, W - 140, 1320], outline=acc, width=6)
    for i in range(9):
        x = 190 + i * 90
        d.line([(x, 560), (x + 26, 1280)], fill=(*acc[:3], 90), width=10)
    d.rectangle([140, 1320, W - 140, 1320 + 120], fill=hexa(pal.get("accent2", "#FF6B4A")))
    d.text((140, 380), "一块石灰基的一生", font=font((tok.get("type") or {}).get("display") or "NotoSansSC-Black", 76),
           fill=ink)
    d.text((140, 1480), "墙面，或者地面，或者任何表面", font=font((tok.get("type") or {}).get("body") or "NotoSansSC-Regular", 46),
           fill=ink)
    # PiP 占位
    d.rounded_rectangle([W - 380, 1500, W - 80, 1780], radius=int(tok.get("radius") or 4),
                        outline=acc, width=3)
    d.text((W - 350, 1590), "人物 PiP", font=font((tok.get("type") or {}).get("body") or "NotoSansSC-Regular", 34),
           fill=(*ink[:3], 170))
    return img


def sheet(frames: list[Image.Image], dst: Path) -> Path:
    tw, th = 360, 640
    pad = 20
    sheet_img = Image.new("RGB", (tw * len(frames) + pad * (len(frames) + 1), th + pad * 2), "#0A0B0D")
    for i, im in enumerate(frames):
        sheet_img.paste(im.convert("RGB").resize((tw, th), Image.LANCZOS),
                        (pad + i * (tw + pad), pad))
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet_img.save(dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", nargs="+", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    outdir = Path(a.out).expanduser() if a.out else ROOT / "styles" / "previews"
    made = []
    for pid in a.pack:
        p = ROOT / "packs" / f"{pid}.yaml"
        if not p.exists():
            print(f"没有 {p}")
            continue
        pack = yaml.safe_load(p.read_text())
        frames = [frame_subtitle(pack), frame_card(pack), frame_bigword(pack), frame_codepic(pack)]
        s = sheet(frames, outdir / f"{pid}.jpg")
        made.append(str(s))
        print(f"样张：{s}")
        # 同时落一张单人帧，细节可查
        frames[1].convert("RGB").save(outdir / f"{pid}-card.png")
    # 把 preview 写回目录
    cat_p = ROOT / "styles" / "catalog.json"
    cat = json.loads(cat_p.read_text())
    for pk in cat["packs"]:
        f = outdir / f"{pk['id']}.jpg"
        if f.exists():
            pk["preview"] = str(f.relative_to(ROOT))
            pk["status"] = "ready" if pk.get("status") != "planned" else "planned"
    cat_p.write_text(json.dumps(cat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
