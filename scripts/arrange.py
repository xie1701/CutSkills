#!/usr/bin/env python3
"""CutSkills · 结构方案：把「倒不倒序」交给人选，而不是我替他定。

剪片最有创作感的时刻是**排顺序**：同一批话，先给结论还是先给现场，是两种片子。
所以这里不自动决定，而是摆三套方案，说明每套前 10 秒会是什么样，让人挑。

三套方案的差别只有一个：**第一格放什么**。
后面沿用原来的顺序——真要大改顺序，是重写 plan，不是点一下按钮的事。

方案：
  keep      原顺序（第一格不动）
  conclude  结论前置（把主张那句提到最前）
  scene     现场切入（最强的画面先上，声音从主张那句开始）
  reverse   倒序（把最后一段提到最前，用于「先给结果再讲怎么来的」）

用法：
  arrange.py --project <项目>                 # 列出方案（读 timeline + audio-map）
  arrange.py --project <项目> --choose scene  # 定案，写 order.json + decisions.md
  arrange.py --project <项目> --show
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def starts(tl: dict) -> list[dict]:
    out, t = [], 0.0
    for el in tl.get("elements") or []:
        d = (round(float(el["out"]) - float(el["in"]), 3) if el.get("kind") == "clip"
             else float(el.get("dur") or 0))
        if el.get("track") != "overlay":
            out.append({"n": el["n"], "t": round(t, 2), "dur": d,
                        "kind": el.get("kind"), "role": el.get("role"),
                        "template": el.get("template"),
                        "medium": el.get("medium"),
                        "asset": (el.get("asset") or "").split("/")[-1][:26],
                        "text": (el.get("evidence") or "")[:24]})
            t = round(t + d, 2)
    return out


def pick(rows: list[dict], role: str, fallback: int = 0) -> dict:
    cand = [r for r in rows if r.get("role") == role and r["kind"] == "clip"]
    return (cand[0] if cand else rows[fallback])


def scene_pick(rows: list[dict]) -> dict:
    """现场切入：最短的 B-roll（画面硬、话少），没有就退回第一格。"""
    cand = [r for r in rows if r.get("medium") == "broll"]
    return min(cand, key=lambda r: r["dur"]) if cand else rows[0]


def build(tl: dict) -> dict:
    rows = starts(tl)
    ids = [r["n"] for r in rows]
    head_options = {
        "keep": ("原顺序", ids[0]),
        "conclude": ("结论前置", pick(rows, "claim")["n"]),
        "scene": ("现场切入", scene_pick(rows)["n"]),
        "reverse": ("倒序：结果先给", ids[-1]),
    }
    options = []
    for key, (label, head) in head_options.items():
        rest = [i for i in ids if i != head]
        order = [head] + rest
        r = next(x for x in rows if x["n"] == head)
        options.append({
            "id": key, "label": label, "order": order,
            "head": {"n": head, "kind": r["kind"], "dur": r["dur"],
                     "asset": r["asset"], "text": r["text"], "role": r.get("role")},
            "note": ("后面沿用原顺序；真要整片重排，改 plan.json 里的段序，"
                     "不是这里点一下"),
        })
    return {"version": 1, "duration_s": tl.get("duration_s"), "options": options,
            "chosen": None}


def show(d: dict) -> None:
    for o in d["options"]:
        h = o["head"]
        print(f" {o['id']:<9} {o['label']:<12} 第一格：{h['kind']} {h['dur']}s "
              f"{h['asset']}「{h['text']}」")
    print(f"定案：{d.get('chosen') or '还没定'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--choose", default=None)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    op = proj / "order.json"

    if a.show:
        d = load(op)
        if not d:
            raise SystemExit("还没有 order.json")
        show(d)
        return 0

    if a.choose:
        d = load(op)
        if not d:
            raise SystemExit("先跑一次 arrange.py 生成方案")
        ids = {o["id"] for o in d["options"]}
        if a.choose not in ids:
            raise SystemExit(f"没有「{a.choose}」，可选 {sorted(ids)}")
        d["chosen"] = a.choose
        op.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            from log_decision import append_row
            o = next(x for x in d["options"] if x["id"] == a.choose)
            append_row(proj, said=f"结构定为「{o['label']}」",
                       did=f"第一格换成 #{o['head']['n']}（{o['head']['kind']} "
                           f"{o['head']['dur']}s，{o['head']['text'][:12]}），后段沿用原顺序")
        except Exception as e:
            print(f"  ！decisions.md 没写进去：{e}")
        print(f"定案 {a.choose}。要真按这个顺序出片，把这套顺序写回 plan.json 的段序再重跑 timeline。")
        return 0

    tl = load(proj / "timeline.json")
    if not tl:
        raise SystemExit("先有 timeline.json")
    d = build(tl)
    d["chosen"] = (load(op) or {}).get("chosen")
    op.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"结构方案 {len(d['options'])} 套 → {op}")
    show(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
