#!/usr/bin/env python3
"""CutSkills · 第 2 层：先出前 3 秒的候选，再谈整条。

为什么先做这一步：整条片 79 秒，改一次要 1.5 分钟渲 + 3 分钟验；而观众决定要不要看
是前 3 秒决定的。先花 30 秒出 2–3 个开场，让人（或小额投放）挑一个，再排整条——
这是广告投放的 creative testing 搬到剪辑台上，不是「多做一个版本」。

三个候选不是随机三选一，是三种不同的**开场策略**：

  A 结论前置   一上来就把主张说出来（参考片 A 版赢的那一次）
  B 现场切入   先给最硬的现场画面，人后出
  C 反差提问   库里没有能打的画面时，用代码造的整屏（还带一个数字/反差的钩子）

候选落 `hooks.json`，每个候选本身就是一份能直接渲的 timeline；`--choose` 记进 decisions.md。

用法：
  hooks.py --project <项目>                     # 只生成候选（快）
  hooks.py --project <项目> --render            # 顺便渲成 3 秒片，给人二选一
  hooks.py --project <项目> --show              # 看现状
  hooks.py --project <项目> --choose A --note "业主看得懂"   # 定案留痕
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_edl import resolve_lib  # noqa: E402

REJUDGE = re.compile(r"其实|不是|别|千万|记住|关键|问题是|说到底|本质")
HOOK_MAX_S = 3.6


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def pick_conclusion(plan: dict | None, index: dict) -> dict | None:
    """A 结论前置：优先拿 plan 里已经标成 claim 的第一段（它本来就是主张）。"""
    for seg in (plan or {}).get("segments") or []:
        if seg.get("role") == "claim" and seg.get("asset") and seg.get("in") is not None:
            span = min(HOOK_MAX_S, float(seg["out"]) - float(seg["in"]))
            return {"kind": "clip", "asset": seg["asset"], "in": float(seg["in"]),
                    "out": round(float(seg["in"]) + span, 3), "medium": seg.get("medium") or "talking",
                    "source": seg.get("source") or "library",
                    "why": "结论前置：把主张放最前面，观众第一句就知道你要说什么"}
    return None


def pick_scene(index: dict) -> dict | None:
    """B 现场切入：找一段 must-use、有运动、时长够的 B-roll。"""
    best = None
    for rec in index.values():
        for sg in rec.get("l2") or rec.get("segments") or []:
            if sg.get("grade") != "must-use" or not sg.get("action"):
                continue
            span = float(sg.get("out", 0)) - float(sg.get("in", 0))
            if span < 1.6:
                continue
            score = len(str(sg.get("action"))) + (2 if sg.get("shot") in ("detail", "close") else 0)
            if not best or score > best[0]:
                best = (score, {"kind": "clip", "asset": rec["id"], "in": float(sg["in"]),
                                "out": round(float(sg["in"]) + min(HOOK_MAX_S, span), 3),
                                "medium": "broll", "source": "library",
                                "why": "现场切入：先给最硬的画面，人后出"})
    return best[1] if best else None


def pick_gen(brief: dict) -> dict:
    """C 反差提问：库里没有能打的画面时，用代码造一个。"""
    change = str((brief or {}).get("change") or "").strip("。")
    state = str((brief or {}).get("audience_present_state") or "").strip("。")
    # 钩子念的是**观众现在信的那句**，不是我们要他做的事。
    # 一开始写反了：把 change 截 6 字打成「比价前先问三」，读起来像被刀切的。
    text = (state[:8] or change[:8] or "真的越贵越好？").rstrip("，、 ") + "？"
    sub = change[:14] if change else ""
    return {"kind": "gen", "template": "keyword", "dur": 2.6,
            "props": {"text": text, "sub": sub}, "source": "generated",
            "why": "反差提问：把观众现在的想法直接打在画面上，制造「说的就是我」"}


def build(proj: Path, lib: Path, index: dict) -> dict:
    brief = load(proj / "brief.json") or {}
    plan = load(proj / "plan.json")
    cands = []

    a = pick_conclusion(plan, index)
    if a:
        cands.append({"id": "A", "label": "结论前置", "elements": [a]})
    b = pick_scene(index)
    if b:
        cands.append({"id": "B", "label": "现场切入", "elements": [b]})
    cands.append({"id": "C", "label": "反差提问", "elements": [pick_gen(brief)]})
    for c in cands:
        total = 0.0
        for i, el in enumerate(c["elements"], 1):
            el["n"] = i
            el["track"] = "video"
            el["_start"] = round(total, 3)
            total = round(total + (float(el["out"]) - float(el["in"]) if el["kind"] == "clip"
                                   else float(el.get("dur") or 2)), 3)
        c["duration_s"] = total
    return {"version": 1, "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "basis": {k: brief.get(k) for k in ("audience", "audience_present_state", "change")},
            "candidates": cands, "chosen": None}


def render_one(proj: Path, lib: Path, cand: dict, style: str) -> Path | None:
    d = proj / "hooks" / f"cand-{cand['id']}"
    d.mkdir(parents=True, exist_ok=True)
    tl = {"version": 4, "slug": f"hook-{cand['id']}", "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
          "duration_s": cand["duration_s"], "style_pack": style, "brief": None,
          "skeleton": None, "elements": cand["elements"], "beats": []}
    (d / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE / "render_hf.py"), "--project", str(d),
                        "--library", str(lib), "--style", style, "--extractor", "own",
                        "--quality", "draft", "--workers", "4", "--shot-match", "0",
                        "--no-cutout", "--no-loudnorm"],
                       capture_output=True, text=True)
    out = d / "out" / "film-hf.mp4"
    if r.returncode != 0 or not out.exists():
        print(f"  ！候选 {cand['id']} 渲不出来：{(r.stderr or r.stdout or '')[-300:]}", file=sys.stderr)
        return None
    dst = proj / "hooks" / f"{cand['id']}-{cand['label']}.mp4"
    shutil.copy2(out, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--choose", default=None, help="定案：A / B / C")
    ap.add_argument("--note", default=None)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else resolve_lib(proj, None)
    hp = proj / "hooks.json"

    if a.show:
        h = load(hp)
        if not h:
            raise SystemExit("还没有 hooks.json，先跑一次 hooks.py")
        for c in h["candidates"]:
            mark = "★" if h.get("chosen") == c["id"] else " "
            print(f" {mark}{c['id']}. {c['label']:<6} {c['duration_s']:.2f}s  {c['elements'][0]['why']}")
        print(f"定案：{h.get('chosen') or '还没定'}")
        return 0

    if a.choose:
        h = load(hp)
        if not h:
            raise SystemExit("还没有 hooks.json，先跑一次 hooks.py")
        ids = {c["id"] for c in h["candidates"]}
        if a.choose not in ids:
            raise SystemExit(f"候选「{a.choose}」不在里面（有 {sorted(ids)}）")
        h["chosen"] = a.choose
        hp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            from log_decision import append_row   # decisions.md 只有这一个写入实现
            label = next(c["label"] for c in h["candidates"] if c["id"] == a.choose)
            append_row(proj, said=a.note or f"前 3 秒定 {a.choose}（{label}）",
                       did=f"片头接 {a.choose} 候选：{label}，时长 "
                           f"{next(c['duration_s'] for c in h['candidates'] if c['id'] == a.choose)}s")
        except Exception as e:  # 留痕失败不许静默
            print(f"  ！decisions.md 没写进去：{e}", file=sys.stderr)
        print(f"定案 {a.choose}，已记进 decisions.md。timeline.py 会把这一段接到片头。")
        return 0

    idx = {}
    try:
        idx = {x["id"]: x for x in (load(lib / "_cutskills" / "index.json") or {"assets": []})["assets"]}
    except FileNotFoundError:
        raise SystemExit(f"素材库没有索引：{lib}")
    brief = load(proj / "brief.json")
    if not brief:
        raise SystemExit("先跑 brief.py：钩子要对着「他现在信什么」做，不然三个候选都在猜")
    style = a.style or brief.get("style_pack") or "talking-head-v1"

    h = build(proj, lib, idx)
    hp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"前 3 秒候选 {len(h['candidates'])} 个 → {hp}")
    for c in h["candidates"]:
        print(f"  {c['id']}. {c['label']:<6} {c['duration_s']:.2f}s  {c['elements'][0]['why']}")
    if a.render:
        (proj / "hooks").mkdir(exist_ok=True)
        for c in h["candidates"]:
            p = render_one(proj, lib, c, style)
            if p:
                print(f"  → {p}（{p.stat().st_size / 1e6:.1f} MB）")
    print("下一步：给人看这 2–3 个 3 秒，二选一 → hooks.py --choose A --note \"...\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
