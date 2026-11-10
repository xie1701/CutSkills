#!/usr/bin/env python3
"""CutSkills · timeline.json v4 → v5 迁移。

v5 只**加字段**，不改结构：
  顶层  plan / pack / axis
  元素  axis / narrative / mg_role / micro / source_ref / approx / hard_cut / punch

三条原则：
  ① 不加东西也能跑——缺省的字段在 check/render 里都有默认值，v4 文件继续合法。
  ② 迁移只做「能从现有信息推出来的」：pack 从 style_pack 或风格包名推，
     plan 从 hooks/arrange 的决策推，推不出来就写默认值并在报告里说明，不假装知道。
  ③ 不动任何时间、素材、文字。迁移是加字段，不是改片子。

用法：
  schema_upgrade.py --project <项目> [--write] [--plan S2-live-mid] [--pack P1-warm]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_PLAN = "S2-live-mid"
DEFAULT_PACK = "P1-warm"

# hooks 的选择 / arrange 的结构 → 骨架 id。推不出来就用默认值并说出来。
HOOK_TO_PLAN = {"A": "S1-conclude-fast", "B": "S2-live-mid", "C": "S1-conclude-fast"}


def guess_plan(proj: Path) -> tuple[str, str]:
    dec = proj / "decisions.md"
    if dec.exists():
        txt = dec.read_text(errors="ignore")
        for hook, pid in HOOK_TO_PLAN.items():
            if f"--choose {hook}" in txt or f"开场 {hook}" in txt:
                return pid, f"从 decisions.md 的开场选择 {hook} 推得"
    for f in ("order.json", "arrange.json"):
        p = proj / f
        if p.exists():
            try:
                d = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            mode = str(d.get("mode") or d.get("chosen") or "")
            table = {"conclude": "S1-conclude-fast", "keep": "S3-probe-slow",
                     "scene": "S2-live-mid", "reverse": "S4-reverse-fast"}
            if mode in table:
                return table[mode], f"从 {f} 的结构「{mode}」推得"
    return DEFAULT_PLAN, "推不出来，用默认骨架（现场切入·中）"


def guess_pack(tl: dict) -> tuple[str, str]:
    sp = str(tl.get("style_pack") or "").strip()
    if sp and sp != "talking-head-v1":
        return sp, f"沿用 style_pack「{sp}」"
    return DEFAULT_PACK, "沿用现有风格包 → 记为 P1 暖实拍"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default="timeline.json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--plan", default=None)
    ap.add_argument("--pack", default=None)
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    path = proj / a.timeline
    if not path.exists():
        raise SystemExit(f"没有 {path}")
    tl = json.loads(path.read_text())
    ver = int(tl.get("version") or 4)

    plan, why_plan = (a.plan, "命令行指定") if a.plan else guess_plan(proj)
    pack, why_pack = (a.pack, "命令行指定") if a.pack else guess_pack(tl)

    changed: list[str] = []
    if ver < 5:
        tl["version"] = 5
        changed.append(f"version {ver} → 5")
    if not tl.get("plan"):
        tl["plan"] = plan
        changed.append(f"plan = {plan}（{why_plan}）")
    if not tl.get("pack"):
        tl["pack"] = pack
        changed.append(f"pack = {pack}（{why_pack}）")
    tl.setdefault("axis", "A")

    n_axis = 0
    for el in tl.get("elements") or []:
        if not el.get("axis"):
            el["axis"] = tl["axis"]
            n_axis += 1
    if n_axis:
        changed.append(f"{n_axis} 个元素补 axis（全部默认 A：口播/素材为主，代码画面服务论点）")

    print(f"{proj.name}：v{ver} → v5")
    for c in changed:
        print(f"  · {c}")
    if not changed:
        print("  （已是最新，没东西要迁）")
    if a.write:
        path.write_text(json.dumps(tl, ensure_ascii=False, indent=1))
        print(f"  已写入 {path}")
    else:
        print("  （预览，加 --write 才落盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
