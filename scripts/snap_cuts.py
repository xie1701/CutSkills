#!/usr/bin/env python3
"""CutSkills · 切点吸附：把一个切点的 100ms 级漂移，用「挪声音」而不是「裁画面」修掉。

怎么漂的：每个元素各自加了 padding（PAD_IN/PAD_OUT），切点是各格时长累加出来的，
于是切点会落在词边界旁边 20–160ms 的地方。统计上这就是「声画不同步」的来源。

怎么修：切点不动（画面不能裁掉话），把**这一格的音频起点**挪一个 delta，
让第一个词正好落在切点上。delta 超过 250ms 就不动——那说明选段本身就错了，
硬挪会把台词挪串。

用法：
  snap_cuts.py --project <项目> [--apply] [--max-delta 0.25]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--apply", action="store_true", help="真写回 timeline.json")
    ap.add_argument("--max-delta", type=float, default=0.25)
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    tl = load(proj / "timeline.json")
    am = load(proj / "audio-map.json")
    if not tl or not am:
        raise SystemExit("要 timeline.json 和 audio-map.json")

    from sync_check import element_starts

    blocks = [b for b in am["blocks"] if not b.get("is_filler")]
    bnds = sorted({round(b["t"], 3) for b in blocks} |
                  {round(b["t"] + b["dur"], 3) for b in blocks})
    els = tl["elements"]
    starts = element_starts(tl)
    by_n = {e["n"]: e for e in els}

    plan, worst = [], 0.0
    for ev in starts[1:]:                     # 0 秒那个不用动
        t = ev["t"]
        near = min(bnds, key=lambda x: abs(x - t))
        delta = round(near - t, 3)
        if abs(delta) < 0.02 or abs(delta) > a.max_delta:
            if abs(delta) > a.max_delta:
                print(f"  · {t:6.2f}s 差 {delta*1000:+.0f}ms 超上限，不动（选段本身该改）")
            continue
        el = by_n.get(ev["n"])
        if not el or el.get("kind") not in ("clip", "image"):
            continue
        aud = el.get("audio") or {}
        a_in = float(aud.get("in", el["in"]))
        a_out = float(aud.get("out", el["out"]))
        plan.append((el["n"], t, delta, a_in, a_out))
        worst = max(worst, abs(delta))

    print(f"可以吸附的切点 {len(plan)} 个，最大 {worst*1000:.0f}ms")
    for n, t, d, ai, ao in plan:
        print(f"  · #{n} {t:6.2f}s 音频 {ai:.2f} → {ai + d:.2f}（{d*1000:+.0f}ms）")

    if not a.apply or not plan:
        if plan:
            print("加 --apply 写回（然后要重跑 audio_map.py 与渲染）")
        return 0

    for n, _t, d, ai, ao in plan:
        el = by_n[n]
        aud = el.setdefault("audio", {"asset": el.get("audio_asset") or el["asset"]})
        aud["in"] = round(ai + d, 3)
        aud["out"] = round(ao + d, 3)
        el["audio_in"], el["audio_out"] = aud["in"], aud["out"]
    (proj / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(f"已写回 {proj / 'timeline.json'}；重跑 audio_map.py 再渲")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
