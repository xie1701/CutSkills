#!/usr/bin/env python3
"""平台 UI 遮挡区：竖屏满屏播放时压住画面的三块区域，以及「字幕/贴纸进没进去」的求交。

阈值出处（BRIEF 自动质检「字幕和贴纸是否进入平台 UI 遮挡区」项）：
  · 顶部 10%  —— 状态栏 + 顶部导航（返回/关注/推荐）
  · 右侧按钮列 13% 宽 —— 点赞、评论、转发、音乐转盘
  · 底部 17%  —— 用户名/文案/话题/进度条/评论输入框
这三个数是**竖屏默认值**，不是普适真理：项目可以覆盖，覆盖顺序是
  ① 本文件的 DEFAULTS
  ② `<项目>/platform-safe.json`
  ③ `<项目>/brief.json` 的 `platform_safe` 键（同一个形状）
形状：`{"top_h": 0.10, "right_w": 0.13, "bottom_h": 0.17}`，值都是**画幅比例**，
不是像素——换分辨率不用改配置。

横屏（W > H）不给默认值：平台的横屏 UI 分布跟竖屏不是一回事（右侧没有按钮列、
底部是标题栏），没有实测就不编一个数出来。`zones()` 那时返回空表，调用方必须报
「不适用」，不许当成「过」——这正是本仓反复踩过的「量不到 = 假绿」。

本文件只做几何与读数，不判红黄：判定留在 verify_film（那里才知道该 add 哪一行）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# pillow / numpy 是 C 扩展，装不进 scripts/vendor，落在 $CUTSKILLS_HOME/py（deps.json）。
# 挂上它，字幕框才能按真字宽量（_text_px）；没装也不崩——那里会退回按字宽估算。
try:
    from libs import cutskills_home as _cutskills_home
    _PY_DEPS = _cutskills_home() / "py"
    if _PY_DEPS.exists() and str(_PY_DEPS) not in sys.path:
        sys.path.append(str(_PY_DEPS))
except ImportError:                             # 只用到估算时不需要 pillow
    pass

# 竖屏默认安全区（BRIEF / 抖快满屏实测口径，见文件头）
DEFAULTS = {"top_h": 0.10, "right_w": 0.13, "bottom_h": 0.17}

# 字幕纵向按**字高**算，不按 subs.json 的 band 算：band 是给「字幕该落在哪一段」用的
# 取样带（实测 ±260px，1080×1920 下高 520px），拿它当墨迹框会把根本没字的空白
# 也算成「压住 UI」，报一堆假红。字高系数 1.4 是行盒（line-height 1.06 字号 + 行距）。
SUB_LINE_H = 1.4


def _merge(base: dict, extra: dict | None) -> dict:
    out = dict(base)
    for k in ("top_h", "right_w", "bottom_h"):
        v = (extra or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
    return out


def load_spec(proj: Path | str | None) -> tuple[dict, str]:
    """返回 (安全区, 出处一行)。项目没写就用默认值。"""
    spec, src = dict(DEFAULTS), "竖屏默认（顶部 10% / 右侧 13% / 底部 17%）"
    if not proj:
        return spec, src
    p = Path(proj).expanduser()
    ov = p / "platform-safe.json"
    if ov.exists():
        try:
            spec = _merge(spec, json.loads(ov.read_text(encoding="utf-8")))
            src = "platform-safe.json"
        except (json.JSONDecodeError, OSError, TypeError):
            src = "platform-safe.json 解析失败，退回竖屏默认"
    b = p / "brief.json"
    if b.exists():
        try:
            blk = (json.loads(b.read_text(encoding="utf-8")) or {}).get("platform_safe")
            if isinstance(blk, dict):
                spec = _merge(spec, blk)
                src = (src + " + " if "默认" not in src else "") + "brief.json:platform_safe"
        except (json.JSONDecodeError, OSError):
            pass
    return spec, src


def portrait(W: int, H: int) -> bool:
    return H > W


def zones(W: int, H: int, spec: dict | None = None) -> list[dict]:
    """三块遮挡区，像素框 `[x, y, w, h]`，原点左上。横屏返回空表。"""
    if not (W and H) or not portrait(W, H):
        return []
    s = _merge(DEFAULTS, spec)
    top_h = max(0.0, min(0.6, s["top_h"]))
    right_w = max(0.0, min(0.6, s["right_w"]))
    bottom_h = max(0.0, min(0.6, s["bottom_h"]))
    out = []
    if top_h > 0:
        out.append({"name": "顶部", "box": [0, 0, W, int(round(top_h * H))]})
    if right_w > 0:
        w = int(round(right_w * W))
        out.append({"name": "右侧按钮列", "box": [W - w, 0, w, H]})
    if bottom_h > 0:
        h = int(round(bottom_h * H))
        out.append({"name": "底部", "box": [0, H - h, W, h]})
    return out


def overlap(a, b) -> float:
    """两个 `[x, y, w, h]` 的相交面积（像素）。"""
    ax, ay, aw, ah = (float(v) for v in a)
    bx, by, bw, bh = (float(v) for v in b)
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    return max(0.0, dx) * max(0.0, dy)


def worst_overlap(box, zs: list[dict]) -> tuple[str, float, float]:
    """这个框压在哪块 UI 上、压了多少像素、占自己多大比例。"""
    area = max(1e-6, float(box[2]) * float(box[3]))
    best = ("", 0.0, 0.0)
    for z in zs:
        ov = overlap(box, z["box"])
        if ov > 0 and ov / area > best[2]:
            best = (str(z["name"]), ov, ov / area)
    return best


# ---------------------------------------------------------------- 框从哪来
def _text_px(text: str, size: int, font_name: str | None):
    """一条字的墨迹宽度（像素）。字体文件在就真量，不在就按「展示单位 × 字号」估。

    估也算得比实际偏宽（得意黑 1 单位 = 0.881 字号），所以宁可估宽——
    估窄了会把「已经压到按钮列」的字判成没压。
    """
    try:
        from PIL import ImageFont
        import fonts as _fonts
        rel = _fonts.file_of(font_name) or f"{font_name}.ttf"
        for base in (Path("~/Library/Fonts").expanduser(), Path("/Library/Fonts"),
                     Path("/System/Library/Fonts")):
            fp = base / rel
            if fp.exists():
                return float(ImageFont.truetype(str(fp), int(size)).getlength(str(text)))
    except Exception:                       # noqa: BLE001 字体/PIL 缺了就退回估算
        pass
    units = 0.0
    for ch in str(text or ""):
        units += 1.0 if ord(ch) > 0x2E7F else 0.34
    ratio = 1.0
    try:                                    # 字体表里的宽度比（styles/fonts.yaml）
        import fonts as _fonts
        ratio = float((_fonts.resolve(font_name) or {}).get("ratio") or 1.0)
    except Exception:                       # noqa: BLE001
        ratio = 1.0
    return units * float(size) * max(0.5, ratio)


def sub_boxes(subs: dict | None, W: int, H: int) -> list[dict]:
    """`out/subs.json` → 每条字幕在画面上真正占的框（像素）。

    横向：整行居中（渲器 `.sub` 是居中 flex），宽度按真字宽量；
    纵向：band 中心 ± 半个行盒（SUB_LINE_H × 字号）。
    没有 band / 没写字号时返回空表——判不了，不猜。
    """
    doc = subs or {}
    cues = doc.get("cues") or []
    band = doc.get("band")
    size = doc.get("size") or 0
    if not cues or not isinstance(band, list) or len(band) != 2 or not (W and H):
        return []
    y0, y1 = float(band[0]), float(band[1])
    if size:
        cy = (y0 + y1) / 2.0
        h = float(size) * SUB_LINE_H
        top = max(0.0, cy - h / 2.0)
    else:
        top, h = y0, max(0.0, y1 - y0)
    font = doc.get("font")
    out = []
    for c in cues:
        tw = _text_px(str(c.get("text") or ""), int(size or 0), font)
        tw = min(float(W), tw)
        out.append({"label": f"字幕「{str(c.get('text') or '')[:10]}」",
                    "t0": float(c.get("start") or 0.0), "t1": float(c.get("end") or 0.0),
                    "box": [round((W - tw) / 2.0, 2), round(top, 2),
                            round(tw, 2), round(min(h, H - top), 2)]})
    return out


def _card_rect(card: dict, W: int, H: int):
    """一张卡在画面上的框。优先用渲器写下的像素框，其次 layout.box（画幅比例）。"""
    px = card.get("kw_box_px")
    if isinstance(px, list) and len(px) == 4:
        return [float(v) for v in px]
    lay = card.get("layout") or {}
    box = lay.get("box") if isinstance(lay, dict) else None
    if isinstance(box, list) and len(box) == 4:
        # layout.box 是 [x, y, w, h]，画幅比例
        return [round(float(box[0]) * W, 2), round(float(box[1]) * H, 2),
                round(float(box[2]) * W, 2), round(float(box[3]) * H, 2)]
    return None


def card_boxes(cards_doc: dict | None, W: int, H: int) -> list[dict]:
    """`out/cards.json` → 大字/动效卡/贴纸的框。

    大字（keyword/behind 等）有 `kw_box_px`（渲器真落点）；MG 卡有 `layout.box`。
    两个都没有的卡（旧渲染器产物）跳过并在调用方报「没框」——不拿估算值当证据。
    """
    doc = cards_doc or {}
    out = []
    for c in doc.get("cards") or []:
        rect = _card_rect(c, W, H)
        if not rect:
            continue
        kind = str(c.get("kind") or "card")
        name = str(c.get("text") or c.get("mg") or c.get("kind") or "")[:10]
        out.append({"label": f"{kind}卡「{name}」",
                    "t0": float(c.get("start") or 0.0), "t1": float(c.get("end") or 0.0),
                    "box": rect})
    return out


def unboxed(cards_doc: dict | None) -> list[str]:
    """有卡但没有框的（判不了，必须报出来）。"""
    out = []
    for c in (cards_doc or {}).get("cards") or []:
        if _card_rect(c, 1, 1) is None:
            out.append(str(c.get("text") or c.get("kind") or "?")[:12])
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="平台 UI 遮挡区（竖屏默认 / 项目覆盖）")
    ap.add_argument("--project", default=None)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    a = ap.parse_args()
    spec, src = load_spec(a.project)
    zs = zones(a.width, a.height, spec)
    if not zs:
        print(f"{a.width}×{a.height}：非竖屏，没有默认遮挡区（横屏 UI 分布未实测）")
        return 0
    print(f"安全区出处：{src} · 画幅 {a.width}×{a.height}")
    for z in zs:
        x, y, w, h = z["box"]
        print(f"  {z['name']:<8} x {x}–{x+w} · y {y}–{y+h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
