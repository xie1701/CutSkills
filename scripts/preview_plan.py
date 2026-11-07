#!/usr/bin/env python3
"""CutSkills · 计划的试算（dry-run）：不落 edl.json，直接看这一版会成什么样的片子。

没有这一步，改一条计划要跑 make_edl → check_edl → render_paper_edit 三步才知道「现在多长、
几段、离目标差多少」。真库上每步都要读索引，一次几十秒——写的时候看不见结果，就谈不上在
文字层剪。

这里用和 make_edl **同一套**算法（`build_edl`），所以预览说 71.2s，落盘就是 71.2s。

看四件事：
  1. 时长：吸附后总长 vs 目标
  2. 结构：每个论点多少段、多少秒、有没有证据（`role: evidence`）
  3. 语速：总字数 / 秒，判字幕密度和「话说得急不急」
  4. 节奏：口播段中位、最长同介质连续

用法：
  preview_plan.py --library "<库>" --project <项目> --plan plan.json
  preview_plan.py ... --plan plan.json --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_edl import build_edl  # noqa: E402

ROLE_CN = {"setup": "交代", "claim": "主张", "evidence": "举证", "warrant": "说理", "turn": "转折", "payoff": "收束"}
MEDIUM_WINDOW = 40.0


def medium_runs(rows: list[dict]) -> float:
    """最长同质画面连续（秒）—— 40s 不换，观众就开始滑走。

    broll 连段按**素材**算：整片口播盖施工现场画面时 medium 全是 broll，
    按 medium 算永远是 0，这条规则等于不存在。
    """
    if not rows:
        return 0.0

    def key(r: dict) -> str:
        return f"broll:{r.get('asset')}" if r["medium"] == "broll" else r["medium"]

    worst, start, cur, t = 0.0, 0.0, key(rows[0]), 0.0
    for r in rows:
        if key(r) != cur:
            worst = max(worst, t - start)
            start, cur = t, key(r)
        t += r["dur"]
    return max(worst, t - start)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pad-in", type=float, default=0.06)
    ap.add_argument("--pad-out", type=float, default=0.09)
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    proj = Path(a.project).expanduser().resolve()
    plan = json.loads(Path(a.plan).read_text())
    index = {x["id"]: x for x in json.loads(
        (lib / "_cutskills" / "index.json").read_text())["assets"]}
    intent = json.loads((proj / "intent.json").read_text()) if (proj / "intent.json").exists() else {}

    built, problems = build_edl(lib, plan, index, pad_in=a.pad_in, pad_out=a.pad_out)
    segs, beats = built["segments"], built["beats"]

    rows = []
    t = 0.0
    for s in segs:
        d = round(s["out"] - s["in"], 3)
        rows.append({"n": s["n"], "medium": s["medium"], "dur": d, "start": t,
                     "chars": s.get("chars", 0), "beat": s.get("beat"), "role": s.get("role"),
                     "why": s.get("why", ""), "asset": s["asset"]})
        t += d
    total = round(t, 3)
    target = intent.get("duration_s")
    total_chars = sum(r["chars"] for r in rows)
    talk = [r["dur"] for r in rows if r["medium"] == "talking"]
    med = round(statistics.median(talk), 2) if talk else None

    notes: list[str] = [f"{lvl}: {msg}" for lvl, msg in problems]

    # ---- 结构层
    struct = []
    for b in beats:
        own = [r for r in rows if r.get("beat") == b["n"]]
        has_ev = any(r["role"] == "evidence" for r in own)
        struct.append({"n": b["n"], "title": b["title"], "segs": b["segs"],
                       "seconds": b["seconds"], "roles": b["roles"], "has_evidence": has_ev})
        if not has_ev and any(r["role"] == "claim" for r in own):
            notes.append(f"论点 {b['n']} 只有主张、没有 evidence 段——论证缺口")
        if b["seconds"] < 3 and len(own) == 1:
            notes.append(f"论点 {b['n']} 只有 {b['seconds']:.1f}s 一段，展开不了")
    if not beats:
        notes.append("没有 beat / role：预览只能看时长和节奏，看不了结构")
    if beats and len(beats) > max(2, len(segs) // 2):
        notes.append(f"{len(beats)} 个论点 / {len(segs)} 段：论点比段落还密，等于没有论点")

    # ---- 信息密度：每 10s 说多少字；连续多久只有一个人、一直在说
    buckets, step = [], 10.0
    if total > 0:
        nb = int(total // step) + 1
        buckets = [0] * nb
        for r in rows:
            k = int(r["start"] // step)
            if 0 <= k < nb:
                buckets[k] += r["chars"]
    longest_talk, cur_talk, cur_same = 0.0, 0.0, None
    for r in rows:
        dense = r["chars"] / r["dur"] >= 4 if r["dur"] else False
        same = r["medium"] == cur_same
        if dense and same:
            cur_talk += r["dur"]
        else:
            longest_talk = max(longest_talk, cur_talk)
            cur_talk = r["dur"] if dense else 0.0
        cur_same = r["medium"] if same or cur_same is None else r["medium"]
    longest_talk = max(longest_talk, cur_talk)
    if buckets:
        notes.append("每 10s 字数：" + " ".join(str(b) for b in buckets)
                     + "　（连续两格都在 50 字以上=一直在说，观众没时间想）")
    if longest_talk > 25:
        notes.append(f"最长连续 {longest_talk:.0f}s 是同一介质 + 每秒 ≥4 字：插一个画面或留白")

    # ---- 节奏
    runs = medium_runs(rows)
    notes.append(f"最长同质画面连续 {runs:.1f}s（上限 {MEDIUM_WINDOW:.0f}s；broll 按素材算）")
    if runs > MEDIUM_WINDOW:
        notes.append(f"⚠ 有 {runs:.0f}s 没换过画面类型或素材")

    # ---- 超时长时给删段候选（只挑「主张、短、且不是唯一证据」的）
    trim = []
    if target and total > target * 1.05:
        over = total - target
        cands = sorted([r for r in rows if r["role"] in (None, "claim") and r["dur"] <= 6],
                       key=lambda r: -r["dur"])
        acc = 0.0
        for r in cands:
            if acc >= over:
                break
            trim.append(r)
            acc += r["dur"]
        if trim:
            notes.append(f"比目标多 {over:.1f}s；可考虑删 "
                         + "、".join(f"#{r['n']}" for r in trim)
                         + f"（共省 {acc:.1f}s）")

    if a.json:
        print(json.dumps({"segments": rows, "beats": struct, "total": total, "target": target,
                          "chars": total_chars, "talk_median": med, "longest_run": round(runs, 1),
                          "notes": notes}, ensure_ascii=False, indent=1))
        return 0

    print(f"计划 {len(segs)} 段 / {len(beats)} 个论点 · 吸附后 {total:.2f}s"
          + (f"（目标 {target}s，{(total - target) / target * 100:+.0f}%）" if target else ""))
    print(f"字数 {total_chars} 字 · 语速 {total_chars / total:.1f} 字/秒"
          + (f" · 口播段中位 {med}s" if med else ""))
    if struct:
        print("\n论点")
        for b in struct:
            flags = " ".join(ROLE_CN.get(r, r) for r in b["roles"]) or "—"
            ev = "" if b["has_evidence"] else "  ⚠ 没有 evidence"
            print(f"  {b['n']}. {b['title'] or '（没写标题）'}  "
                  f"{b['seconds']:.1f}s / {len(b['segs'])} 段  [{flags}]{ev}")
            print(f"     段 {'、'.join('#' + str(x) for x in b['segs'])}")
    print("\n段")
    for r in rows:
        print(f"  #{r['n']:<2} {r['start']:6.2f}s  {r['medium']:<9} {r['dur']:5.2f}s  "
              f"{r['chars']:>3}字  {(r['role'] or '—'):<8} {r['why'][:26]}")
    if notes:
        print("\n要看一眼")
        for n in notes:
            print(f"  · {n}")
    print("\n（试算没落盘。满意了再跑 make_edl.py --plan 同一份）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
