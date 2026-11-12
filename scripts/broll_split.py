#!/usr/bin/env python3
"""CutSkills · 一组片子的素材互斥：同一批素材剪两条，发出去不能是同一支片。

## 为什么要查

欧阳这一库剪了 a（钱花在哪）和 b（厨房为什么大）两条。两条都是好片，
但把时间轴摊开一对：8 段 B-roll 里 6 段是同一段素材的不同取点，
口播有 7 段完全共用。观众在同一个账号里连着看到两条，会觉得「同一条发了两遍」。

这不是剪辑水平问题，是**分配**问题：每条片各自挑「最合适的那一段」，
挑的时候不知道别人挑了哪一段。必须在**一组**的层面上查。

## 判据

  shared_clip  B-roll 素材文件在同组多个片里都出现（同一段秒数也算）
  shared_take  口播素材同组多片共用，且声音窗口有重叠（不同窗口不算）
  slot_dup     同一条片里同一段素材用了两次以上（观众看得出在重复自己）

同一条片内部重复用同一段素材是允许的（长素材切两处不同内容），
但**跨片**就不行：整组要发出去，观众会认脸、认机位、认那一下转场。

## 用法

  broll_split.py --projects <项目目录> <项目目录> [...]
  broll_split.py --projects ... --json

退出码 1 = 有跨片共用；0 = 干净。它不改时间轴，只报事实与建议（谁该让）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(p: Path) -> dict:
    return json.loads((p / "timeline.json").read_text())


def clips_of(tl: dict) -> list[tuple[str, float, float]]:
    out = []
    for e in tl.get("elements") or []:
        if e.get("medium") == "broll" and e.get("asset"):
            out.append((str(e["asset"]), float(e.get("in") or 0), float(e.get("out") or 0)))
    return out


def takes_of(tl: dict) -> list[tuple[str, float, float]]:
    """这条片子里**观众听见的每一段原声**来自哪里。

    这里曾经写成 `e.get("medium") == "talking"`——而 a 片 13 格里 7 格是
    `medium: broll` 但声音仍然从口播素材来（画面换到他在说的东西，声音没断）。
    于是这道门对整个 b-roll 的声轨视而不见，报「error 0」，
    而实测 a×b 真口播重叠 25.49s（a 片口播总长的 49.5%）。
    """
    out = []
    for e in tl.get("elements") or []:
        au = e.get("audio") or {}
        aid = au.get("asset")
        if aid:
            out.append((str(aid), float(au.get("in") or 0), float(au.get("out") or 0)))
    return out


def audit(projects: list[Path]) -> list[tuple[str, str]]:
    v: list[tuple[str, str]] = []
    names = [p.name for p in projects]
    clipmap: dict[str, list[str]] = {}
    takemap: dict[str, list[tuple[str, float, float]]] = {}
    for p in projects:
        tl = load(p)
        seen: dict[str, int] = {}
        for a, i, o in clips_of(tl):
            clipmap.setdefault(a, []).append(p.name)
            seen[a] = seen.get(a, 0) + 1
        for a, c in seen.items():
            if c > 1:
                v.append(("warning", f"{p.name} 里同一段素材用了 {c} 次：{a[-26:]}"
                                     "（自查一下是不是在重复自己）"))
        for a, i, o in takes_of(tl):
            takemap.setdefault(a, []).append((p.name, i, o))

    for a, who in clipmap.items():
        u = sorted(set(who))
        if len(u) > 1:
            v.append(("error", f"B-roll 跨片共用：{a[-30:]} —— {'、'.join(u)} 都在用。"
                               "同组发出去的片子不该出现同一段画面，让其中一条换点。"))

    for a, uses in takemap.items():
        by = {}
        for n, i, o in uses:
            by.setdefault(n, []).append((i, o))
        for n1 in by:
            for n2 in by:
                if n1 >= n2:
                    continue
                for i1, o1 in by[n1]:
                    for i2, o2 in by[n2]:
                        ov = min(o1, o2) - max(i1, i2)
                        if ov > 0.3:
                            v.append(("error",
                                      f"口播跨片共用：{a[-26:]} —— {n1} 用 {i1:.1f}-{o1:.1f}s，"
                                      f"{n2} 用 {i2:.1f}-{o2:.1f}s，重叠 {ov:.1f}s。"
                                      "同一句原话在两条片里出现，听感就是「发了两遍」。"))
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="一组片子的素材互斥检查")
    ap.add_argument("--projects", nargs="+", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    ps = [Path(x).expanduser().resolve() for x in a.projects]
    v = audit(ps)
    err = [m for lv, m in v if lv == "error"]
    if a.json:
        print(json.dumps([{"level": l, "msg": m} for l, m in v], ensure_ascii=False, indent=1))
    else:
        for lv, m in v:
            print(f"  {'🔴' if lv == 'error' else '🟡'} {m}")
        print(f"素材互斥：error {len(err)} · warning {len(v) - len(err)}"
              f"（{len(ps)} 条片）")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
