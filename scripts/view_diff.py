#!/usr/bin/env python3
"""CutSkills · 对账：视图被手改过没有，计划比上次改了什么。

两条契约以前只是写在文档里，没人检查：

1. **视图能和真值源不一致** —— `paper-edit.md` 是给人读的，人真会在上面改字。改了之后
   没人知道，稿子和 `edl.json` 各说一套，「以哪边为准」就成了悬案。
2. **plan.json 是意图** —— EDL 有 `edl-history`，计划没有。改了几版计划之后，没人说得清
   「现在这版是第几版、上一版为什么被否」。

这个脚本只报差异，不改任何东西。

用法：
  view_diff.py --project <项目目录>            # paper-edit.md vs edl.json + plan.json vs plan-history
  view_diff.py --project ... --plan            # 只看计划的历史差异
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def norm(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def paper_rows(proj: Path) -> list[tuple[int, str]]:
    f = proj / "paper-edit.md"
    if not f.exists():
        return []
    rows = []
    for ln in f.read_text().splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|(.+)$", ln)
        if not m:
            continue
        n = int(m.group(1))
        cell = m.group(2)
        text = cell.split("<br>")[-1].split("|")[0].strip()
        rows.append((n, text))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", action="store_true", help="只看 plan 与历史版本的差异")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    edl = json.loads((proj / "edl.json").read_text())
    segs = edl.get("segments") or []
    problems = 0

    if not a.plan:
        rows = paper_rows(proj)
        if not rows:
            print("没有 paper-edit.md（还没生成过，或被人删了——视图可以删，删了重跑 render_paper_edit.py）")
        else:
            drift = []
            if len(rows) != len(segs):
                drift.append(f"行数不同：稿子 {len(rows)} 行 / EDL {len(segs)} 段")
            for (n, text), s in zip(rows, segs):
                if n != s["n"]:
                    drift.append(f"第 {len(drift) + 1} 行的段号是 #{n}，EDL 这里是 #{s['n']}")
                    break
                want = norm(s.get("evidence") or "")
                got = norm(text)
                if not want:
                    continue
                if got and want[:12] not in got and norm(text)[:12] not in want:
                    drift.append(f"#{n} 的原文对不上：\n    稿子里：{text[:50]}\n    EDL 里：{(s.get('evidence') or '')[:50]}")
            print("## paper-edit.md vs edl.json")
            if drift:
                problems += 1
                print("视图被手改过（或没重新生成）：")
                for d in drift[:10]:
                    print(f"  · {d}")
                print("\n  以哪边为准？——稿子是视图，`edl.json` 是真值源。")
                print("  想按稿子走：把改动写回 plan.json 再跑 make_edl；"
                      "想按 EDL 走：重跑 render_paper_edit.py 覆盖稿子。")
            else:
                print("一致。")

    print()
    print("## plan.json vs plan-history")
    hist = proj / "plan-history"
    snaps = sorted(hist.glob("*.json")) if hist.exists() else []
    cur_f = proj / "plan.json"
    if not snaps:
        print("没有历史快照（每次 make_edl 落盘时会存一份）。")
    else:
        last = json.loads(snaps[-1].read_text())
        cur = json.loads(cur_f.read_text()) if cur_f.exists() else None

        def flat(plan) -> list[tuple[str, float, float]]:
            segs_ = plan if isinstance(plan, list) else (plan.get("segments") or [])
            return [(x.get("asset", ""), float(x.get("in", 0)), float(x.get("out", 0))) for x in segs_]

        a0, b0 = flat(last), flat(cur or [])
        added = [x for x in b0 if x not in a0]
        removed = [x for x in a0 if x not in b0]
        print(f"最近一次快照 {snaps[-1].stem}：{len(a0)} 段 → 现在 {len(b0)} 段"
              f"（新增 {len(added)}、删除 {len(removed)}）")
        for x in added[:8]:
            print(f"  + {x[0]} {x[1]:.2f}–{x[2]:.2f}")
        for x in removed[:8]:
            print(f"  - {x[0]} {x[1]:.2f}–{x[2]:.2f}")
        if added or removed:
            print("  只改了顺序或时间码、段数没变的，这里看不出来——看 edl-report.md 的时间线。")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
