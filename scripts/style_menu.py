#!/usr/bin/env python3
"""CutSkills · 风格菜单：把 4 套剪法 + 3 套包装摆给用户点，而不是让他写配置。

设计原则（2026-11-11 Jerry 定的口径）：
  用户说「换个风格」时，他要的不是改 yaml，是**看见有什么可选、各能实现什么效果**。
  所以每项必须回答四件事：适合什么 / 需要什么 / 牺牲什么 / 长什么样。
  「长什么样」分三层：节奏条（秒出）→ 空镜微样片（20–40s 渲一次，缓存）→ 你的素材 10 秒试片（定了才渲）。

用法：
  style_menu.py --list                        # 文字菜单（对话里直接贴）
  style_menu.py --bars                        # 渲 4 张节奏条 PNG
  style_menu.py --choose S2-live-mid P1-warm --project <项目>   # 记下选择
  style_menu.py --show S3-probe-slow          # 单套详情
  style_menu.py --subtitles                   # 4 套字幕模板（中文名 / 适合什么 / 字体与授权）
  style_menu.py --subtitles --choose cool-thin --project <项目>  # 只换字幕，不换整套包装
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "styles" / "catalog.json"
PLANS = ROOT / "plans"
BARS = ROOT / "styles" / "previews"

FONT_CANDIDATES = [
    Path.home() / "Library/Fonts/NotoSansSC-Regular.ttf",
    Path.home() / "Library/Fonts/NotoSansSC-SemiBold.ttf",
    Path("/System/Library/Fonts/PingFang.ttc"),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONT_CANDIDATES:
        if f.exists():
            try:
                return ImageFont.truetype(str(f), size)
            except OSError:
                continue
    return ImageFont.load_default()


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text())


def plan_spec(pid: str) -> dict:
    p = PLANS / f"{pid}.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


# ── 节奏条：一张图说明「这套剪法怎么切」

def draw_bar(plan: dict, dst: Path, w: int = 1200, h: int = 300) -> Path:
    """横轴时间 0–60s 归一，竖线是切点，色带标出叙事段落。"""
    img = Image.new("RGB", (w, h), "#101214")
    d = ImageDraw.Draw(img)
    f_big, f_mid, f_small = font(30), font(20), font(16)
    tempo = (plan.get("tempo") or {})
    target = float(tempo.get("target_median_s") or 2.4)
    total = 60.0
    # 叙事段落宽度按 arc.max_s 比例分配
    arc = plan.get("arc") or []
    weights = [float(b.get("max_s") or 10) for b in arc] or [1]
    s = sum(weights) or 1
    x0, y0, x1, y1 = 70, 110, w - 40, h - 90
    span = x1 - x0
    colors = ["#F6EB34", "#39D0C4", "#E0724B", "#7A6CF0", "#8FBF5A"]
    acc, tx = 0.0, x0
    for i, (b, wt) in enumerate(zip(arc, weights)):
        seg = span * wt / s
        d.rectangle([tx, y0, tx + seg, y1], fill=colors[i % len(colors)] + "")
        label = f"{b.get('beat')} · {b.get('job')}"
        # 段窄就把 job 截短，宁可少说，不许出界（第一次渲就撞上了："hook · 现"）
        max_chars = max(3, int((seg - 14) / (f_small.size * 0.62)))
        if len(label) > max_chars:
            label = label[:max(3, max_chars - 1)] + "…"
        d.text((tx + 8, y0 + 6), label, font=f_small, fill="#101214")
        acc += wt
        tx += seg
    # 切点：按 target 中位在 60 秒里画竖线（示意，不是真切点）
    t = target
    while t < total:
        x = x0 + span * t / total
        d.line([x, y0 - 26, x, y1], fill="#F2F5F8", width=2)
        t += target
    d.line([x0, y0 - 30, x1, y0 - 30], fill="#39424D", width=1)
    d.text((x0, 20), f"{plan.get('name', '')}  ·  {plan.get('one_line', '')}", font=f_big, fill="#F5F7FA")
    band = tempo.get("band") or []
    d.text((x0, y0 - 62), f"节奏档 {band[0] if band else '—'}–{band[1] if band else '—'}s "
                          f"（画面变化中位，60 秒示意）", font=f_mid, fill="#B9C2CC")
    for i in range(0, 61, 10):
        x = x0 + span * i / total
        d.line([x, y1, x, y1 + 6], fill="#39424D", width=1)
        d.text((x - 12, y1 + 12), f"{i}s", font=f_small, fill="#7C8794")
    d.text((x0, h - 34), "竖线 = 一次画面变化（按目标中位均匀示意）；色带 = 叙事段落",
           font=f_small, fill="#7C8794")
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)
    return dst


def cmd_bars() -> int:
    cat = load_catalog()
    made = []
    for p in cat["plans"]:
        spec = plan_spec(p["id"]) or {}
        spec.setdefault("name", p["name"])
        spec.setdefault("one_line", p["one_line"])
        spec.setdefault("tempo", {"band": p.get("tempo_band"), "target_median_s": (p.get("tempo_band") or [2.4])[0]})
        dst = BARS / f"{p['id']}.png"
        made.append(draw_bar(spec, dst))
        p["preview"] = str(dst.relative_to(ROOT))
    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2))
    for m in made:
        print(f"节奏条：{m}")
    return 0


def cmd_list(show_paths: bool = False) -> int:
    cat = load_catalog()
    print("现在有 4 套剪法，适合不同的东西：\n")
    for p in cat["plans"]:
        mark = "（默认）" if p.get("status") == "default" else ""
        b = p.get("tempo_band") or ["—", "—"]
        print(f"  {p['id']:<18} {p['name']}{mark}")
        print(f"    {p['one_line']}")
        print(f"    节奏 {b[0]}–{b[1]}s 一换画面 · 适合：{p.get('suits', '')}")
        print(f"    需要：{p.get('needs', '')}")
        print(f"    代价：{p.get('caveat', '')}")
        pv = p.get("preview")
        print(f"    样例：{pv if (pv and show_paths) else (pv or '待渲')}")
        print()
    print("包装 3 套（排版/图形/动效/版式/色调）：\n")
    for p in cat["packs"]:
        print(f"  {p['id']:<10} {p['name']}  — {p['one_line']}")
        print(f"    字幕：{(p.get('subtitle_system') or {}).get('desc', '—')}")
        print(f"    适合：{p.get('suits', '')}")
        print(f"    需要：{p.get('needs', '')}")
        print(f"    代价：{p.get('caveat', '')}")
        pv = p.get("preview")
        print(f"    样例：{pv if (pv and show_paths) else (pv or '待渲')}")
        print()
    print("字幕想单独换？（只换字幕，不换剪法、不换包装）")
    print("  style_menu.py --subtitles        # 4 套：极简白字 / 大字重描边 / 纸感底衬 / 冷色细体")
    print("回一句「S2 + P2」就定；说「都差不多」我再补样例或拿你的素材试 10 秒。")
    return 0


def cmd_show(pid: str) -> int:
    cat = load_catalog()
    for group in ("plans", "packs"):
        for p in cat[group]:
            if p["id"] == pid:
                print(json.dumps(p, ensure_ascii=False, indent=1))
                return 0
    print(f"没这个 id：{pid}", file=sys.stderr)
    return 1


def cmd_choose(plan: str, pack: str | None, project: str | None) -> int:
    cat = load_catalog()
    pids = {p["id"] for p in cat["plans"]}
    kids = {p["id"] for p in cat["packs"]}
    if plan not in pids:
        print(f"剪法「{plan}」不在目录里：{' / '.join(sorted(pids))}", file=sys.stderr)
        return 1
    if pack and pack not in kids:
        print(f"包装「{pack}」不在目录里：{' / '.join(sorted(kids))}", file=sys.stderr)
        return 1
    if not project:
        print(f"选了 {plan}" + (f" + {pack}" if pack else "") + "（没给 --project，只打印不落盘）")
        return 0
    proj = Path(project).expanduser().resolve()
    bf = proj / "brief.json"
    brief = json.loads(bf.read_text()) if bf.exists() else {}
    brief["plan"] = plan
    if pack:
        brief["pack"] = pack
    bf.write_text(json.dumps(brief, ensure_ascii=False, indent=1))
    dec = proj / "decisions.md"
    line = f"- {__import__('datetime').date.today()} 风格选择：剪法 `{plan}`" + (f" · 包装 `{pack}`" if pack else "") + "\n"
    dec.write_text((dec.read_text() if dec.exists() else "") + line)
    print(f"记下了：{plan}" + (f" + {pack}" if pack else "") + f" → {bf}")
    return 0


# ── 字幕模板菜单（styles/subtitles.yaml）────────────────────────────────
# 字幕独立成一层：用户想「只把字幕换个样子」时，不该被逼着连卡片和色调一起换。
# 四套的字体都必须是 fonts.yaml 里核过授权的（OFL/明确可商用）——这条由 subs 加载时守。

def sub_templates() -> dict:
    """读模板表（走 subs 那一份加载与校验，别在这里再写一套）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import subs
    return subs.load_subtitle_templates()


def cmd_subtitles(choose: str | None = None, project: str | None = None) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import subs
    try:
        doc = sub_templates()
    except subs.SubtitleTemplateError as e:
        print(f"字幕模板表有问题：{e}", file=sys.stderr)
        return 1
    if choose:
        if choose not in doc["templates"]:
            print(f"没有这套字幕模板：「{choose}」。可选："
                  + " / ".join(f"{k}（{v.get('name')}）" for k, v in doc["templates"].items()),
                  file=sys.stderr)
            return 1
        if not project:
            print(f"选了字幕模板 {choose}（没给 --project，只打印不落盘）")
            return 0
        proj = Path(project).expanduser().resolve()
        bf = proj / "brief.json"
        brief = json.loads(bf.read_text()) if bf.exists() else {}
        brief["subtitle_template"] = choose
        bf.write_text(json.dumps(brief, ensure_ascii=False, indent=1))
        dec = proj / "decisions.md"
        line = (f"- {__import__('datetime').date.today()} 字幕模板：`{choose}`"
                f"（{doc['templates'][choose]['name']}）\n")
        dec.write_text((dec.read_text() if dec.exists() else "") + line)
        print(f"记下了：字幕模板 {choose}（{doc['templates'][choose]['name']}）→ {bf}")
        return 0
    print(f"现在有 {len(doc['templates'])} 套字幕模板，字体都核过授权（可直接商用）：\n")
    for tid, t in doc["templates"].items():
        lic = subs.font_licence(t.get("font"))
        print(f"  {tid:<14} {t.get('name')}　—　{t.get('one_line') or ''}")
        print(f"    字体 {t.get('font')} {t.get('size_px')}px · 授权 {lic['license']}"
              f" · 行宽 ≤{t.get('max_units_per_line')} 单位"
              f" · 位置 {((t.get('position') or {}).get('center_y_px'))}px")
        print(f"    适合：{t.get('suits') or '—'}")
        print(f"    不适合：{t.get('not_suits') or '—'}")
        print()
    print("回一句「字幕用 cool-thin」我就存下来；也可以 "
          "`style_menu.py --subtitles --choose <id> --project <项目>`。")
    print("不选就沿用包装包自带的字幕参数（老项目行为不变）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--paths", action="store_true", help="样例给出绝对路径（聊天里要发文件时用）")
    ap.add_argument("--bars", action="store_true")
    ap.add_argument("--show", default=None)
    ap.add_argument("--choose", nargs="+", default=None,
                    metavar="PLAN [PACK] | <字幕模板 id>")
    ap.add_argument("--project", default=None)
    ap.add_argument("--subtitles", action="store_true",
                    help="列出 4 套字幕模板（配合 --choose <id> 只换字幕，不换整套包装）")
    a = ap.parse_args()
    if a.bars:
        return cmd_bars()
    if a.subtitles:
        # 这里的 --choose 是「字幕模板 id」（只一个词）。剪法/包装仍是 --choose PLAN [PACK]。
        return cmd_subtitles((a.choose[0] if a.choose else None), a.project)
    if a.show:
        return cmd_show(a.show)
    if a.choose:
        return cmd_choose(a.choose[0], a.choose[1] if len(a.choose) > 1 else None, a.project)
    return cmd_list(show_paths=a.paths)


if __name__ == "__main__":
    raise SystemExit(main())
