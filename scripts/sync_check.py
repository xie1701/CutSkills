#!/usr/bin/env python3
"""CutSkills · 声画同步裁判：Audio-First 唯一能算的那三个数。

19 项像素断言一个都不管「画面是不是跟着台词走」——它只判几何和信号。
上一版靠耳朵听，听得出毛病但说不出差多少。这三个数把「听着别扭」变成可比的读数：

  ① 同步率    视觉事件（换镜、卡片入场）落在**词/气口边界 ±80ms** 内的比例
  ② 断章率    一口气里被切了几刀（块内部有换镜 = 断章），>1 刀算过切
  ③ 大字偏移  卡片出现时间 vs 该句**重音点**（块内能量峰值）的偏移

阈值先按行业经验给：同步率 ≥85%、断章率 ≤15%、大字偏移 ≤120ms。
**这三个数没有用你的素材校准过**（见 references/audio-first.md 的校准记录）。

用法：
  sync_check.py --project <项目> [--map audio-map.json] [--json] [--tolerance 80]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOL_MS = 80
PASS_SYNC = 0.85
PASS_CUT = 0.15
PASS_KW_MS = 120


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def element_starts(tl: dict) -> list[dict]:
    """成片里所有「画面变了」的时刻。overlay 也算事件（卡片压上来是能看见的）。"""
    out, t = [], 0.0
    for el in tl.get("elements") or []:
        if el.get("track") == "overlay":
            out.append({"t": round(float(el.get("at") or 0), 3), "kind": "overlay",
                        "n": el["n"], "label": el.get("template") or ""})
            continue
        out.append({"t": round(t, 3), "kind": el.get("kind"),
                    "n": el["n"], "label": el.get("template") or el.get("medium") or ""})
        d = (round(float(el["out"]) - float(el["in"]), 3) if el.get("kind") == "clip"
             else float(el.get("dur") or 0))
        t = round(t + d, 3)
    return out


def nearest_boundary(t: float, bounds: list[float]) -> tuple[float, float]:
    best = min(bounds, key=lambda b: abs(b - t))
    return round(best - t, 3), best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--map", default=None)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--tolerance", type=int, default=TOL_MS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    tl = load(Path(a.timeline) if a.timeline else proj / "timeline.json")
    am = load(Path(a.map) if a.map else proj / "audio-map.json")
    if not tl or not am:
        raise SystemExit("要 timeline.json 和 audio-map.json（先跑 timeline.py / audio_map.py）")

    blocks = [b for b in am["blocks"] if not b.get("is_filler")]
    bounds = sorted({round(b["t"], 3) for b in blocks} | {round(b["t"] + b["dur"], 3) for b in blocks})
    ev = element_starts(tl)
    tol = a.tolerance / 1000.0

    # ① 同步率
    rows = []
    for e in ev:
        d, b = nearest_boundary(e["t"], bounds)
        rows.append({**e, "dev_ms": round(abs(d) * 1000), "near": b, "signed_ms": round(d * 1000)})
    # 不对称：早切是对的（J-cut 要先让声音进来），晚切才是毛病。
    # 所以判据是 -150ms ~ +40ms，不是对称的 ±80ms。
    hits = [r for r in rows if -a.tolerance * 1.9 <= (r["near"] - r["t"]) * 1000 <= a.tolerance * 0.5]
    sync = len(hits) / len(rows) if rows else 0.0

    # ② 断章：块内部有换镜 = 一口气被切断
    inside = 0
    broken: list[dict] = []
    for b in blocks:
        st, en = b["t"] + 0.15, b["t"] + b["dur"] - 0.15
        cuts = [e["t"] for e in ev if e["kind"] != "overlay" and st < e["t"] < en]
        if cuts:
            inside += 1
            broken.append({"block": b["id"], "text": b["text"], "cuts": cuts})
    cut_rate = inside / len(blocks) if blocks else 0.0

    # ③ 大字偏移：卡片该落在重音上，不是落在段首
    cards = (load(proj / "out" / "cards.json") or {}).get("cards") or []
    kw_all = [c for c in cards if c.get("kind") != "idcard"]
    # 为了不和 overlay 卡片抢同一秒而主动挪后的大字，单列一栏：
    # 它不是「没对上」，是「让开了」，拿它算平均偏移等于自己骗自己。
    kw = [c for c in kw_all if not c.get("deferred_to")]
    deferred = [c for c in kw_all if c.get("deferred_to")]
    offs = []
    for c in kw:
        st = float(c["start"])
        cand = [b for b in blocks if b["t"] - 0.4 <= st <= b["t"] + b["dur"] + 0.4]
        if not cand:
            continue
        b = min(cand, key=lambda b: abs(b["t"] - st))
        peak = b["t"] + (float(b.get("peak_at") or b["t"]) - float(b["src_start"]))
        offs.append({"card": c.get("text", "")[:10], "start": round(st, 2),
                     "peak": round(peak, 2), "off_ms": round((st - peak) * 1000)})
    mean_off = round(sum(abs(o["off_ms"]) for o in offs) / len(offs)) if offs else None
    dnote = []
    for c in deferred:
        st = float(c["start"])
        cand = [b for b in blocks if b["t"] - 0.4 <= st <= b["t"] + b["dur"] + 0.4]
        peak = None
        if cand:
            b = min(cand, key=lambda b: abs(b["t"] - st))
            peak = round(b["t"] + (float(b.get("peak_at") or b["t"]) - float(b["src_start"])), 2)
        dnote.append({"card": str(c.get("text", ""))[:10], "start": round(st, 2),
                      "peak": peak, "reason": f"让给 overlay 卡至 {c['deferred_to']}s"})

    # 一张可量的卡都没有时，这一栏判「不适用」（None）而不是「没过」：
    # 全片只有一块大字、并且它为了给对比屏让位挪后了，这不是偏移失败。
    kw_state = ("na" if (not offs and deferred) else
                ("pass" if (mean_off is not None and mean_off <= PASS_KW_MS) else "fail"))
    verdict = {
        "sync_rate": {"value": round(sync, 3), "pass": sync >= PASS_SYNC, "need": PASS_SYNC},
        "cut_inside_rate": {"value": round(cut_rate, 3), "pass": cut_rate <= PASS_CUT, "need": PASS_CUT},
        "keyword_offset_ms": {"value": mean_off, "state": kw_state, "need": PASS_KW_MS,
                              "deferred": len(deferred)},
    }
    ok = all(v["pass"] for v in verdict.values() if "pass" in v)

    rep = {"events": len(ev), "blocks": len(blocks), "tolerance_ms": a.tolerance,
           "verdict": verdict, "ok": ok,
           "worst": sorted(rows, key=lambda r: -r["dev_ms"])[:5],
           "broken": broken[:5], "keyword": offs[:6]}
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"声画同步：{sync:.0%}（{len(hits)}/{len(rows)} 个视觉事件落在词边界"
              f" −{int(a.tolerance*1.9)}ms~+{int(a.tolerance*0.5)}ms 内，需要 ≥{PASS_SYNC:.0%}）"
              f"{'✅' if verdict['sync_rate']['pass'] else '✗'}")
        print(f"断章率　：{cut_rate:.0%}（{inside}/{len(blocks)} 口气里被切了刀，"
              f"需要 ≤{PASS_CUT:.0%}）{'✅' if verdict['cut_inside_rate']['pass'] else '✗'}")
        if kw_state == "na":
            print(f"大字偏移：不适用（{len(deferred)} 块大字让位给 overlay 卡，"
                  f"没有可量的卡）")
        elif mean_off is None:
            print("大字偏移：没有大字卡，不适用")
        else:
            print(f"大字偏移：{mean_off}ms（离重音点，需要 ≤{PASS_KW_MS}ms）"
                  f"{'✅' if kw_state == 'pass' else '✗'}")
        for r in rep["worst"][:3]:
            print(f"  · {r['t']:6.2f}s {r['kind']:<7}{r['label'][:10]:<10} 差 {r['dev_ms']}ms")
        for b in broken[:3]:
            print(f"  · 断章：{b['text'][:12]}（{len(b['cuts'])} 刀 @ "
                  f"{', '.join(f'{c:.2f}' for c in b['cuts'])}）")
        for o in offs[:3]:
            print(f"  · 大字「{o['card']}」{o['start']}s vs 重音 {o['peak']}s → {o['off_ms']:+}ms")
        for d in dnote:
            print(f"  · 大字「{d['card']}」{d['start']}s（{d['reason']}，重音 {d['peak']}s）——不计入偏移")
        print(f"结论：{'过' if ok else '没过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
