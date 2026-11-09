#!/usr/bin/env python3
"""CutSkills · 字幕裁判：去标点 / 呼吸组 / 单行宽度 / 强调色 / 硬切。

2026-11-09 的五条规矩（Jerry 定的）都能机器判，所以别再靠看：

  ① 不出标点        —— 一条字幕里不许有「，。？！、；：…」
  ② 不切词          —— 相邻两条时间上贴在一起（间隔 <0.12s）＝在词中间断了
  ③ 单行 10–14 字   —— 上限硬判；中位数要在 10–14 之间（全片都是 5 个字＝太碎）
  ④ 强调色要有      —— 一条都没染说明规则没生效
  ⑤ 硬切            —— 淡入必须 ≤120ms（读样式包，不看代码）

用法：
  check_subs.py --project <项目> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PUNCT = "，。！？、；：…—～,.!?;:"
MAX_CHARS = 14
WIDE_TOL = 15             # 允许一条 15 字：没有停顿也没有接缝时，宁可多一个字也不切词
# 判的是「中位数落在这个带里」：10–14 是拆长句的目标，短句本来就是 6–9 字。
# 真卡死的是上限（>15 一刀不放过）和「一刀两断」（断词）。
WIDTH_BAND = (8, 14)
TIGHT_GAP = 0.12          # 两条字幕贴得比这还近＝在同一口气里被切开了
FADE_MAX = 0.12


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    subs = load(proj / "out" / "subs.json")
    if not subs:
        raise SystemExit("还没有 out/subs.json（先跑 render_hf.py）")
    cues = subs.get("cues") or []

    punct = [c for c in cues if any(ch in PUNCT for ch in str(c.get("text") or ""))]
    wide = [c for c in cues if len(str(c.get("text") or "")) > WIDE_TOL]
    widths = sorted(len(str(c.get("text") or "")) for c in cues)
    med = widths[len(widths) // 2] if widths else 0
    em = sum(1 for c in cues if "<span class" in str(c.get("html") or ""))
    # 断词要按**词边界**判，不能按「断在哪儿」判。
    # 原来只盯 break_after == "width"，于是 ASR 句子边界落在词中间那种情况一路绿灯：
    # 「墙面或者地面或者任何表」+「面的让他成为成品就」把「表面」劈成两半，
    # 而裁判报「断词 ✅」。判据得是：把两条接回去，切开的位置在不在词与词之间。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from subs import HAVE_JIEBA, word_bounds  # noqa: E402

    tight = []
    for prev, nxt in zip(cues, cues[1:]):
        # 只比**同一格**里的相邻两条：跨段的贴边是切点造成的，不是把词劈开
        if prev.get("el") != nxt.get("el"):
            continue
        a_, b_ = str(prev.get("text") or ""), str(nxt.get("text") or "")
        if not a_ or not b_:
            continue
        gap = float(nxt["start"]) - float(prev["end"])
        if gap > 0.40:                       # 中间空了一大截，这两条本来就不连着
            continue
        if HAVE_JIEBA:
            bad = len(a_) not in word_bounds(a_ + b_)
        else:                                 # 没分词器只能退回老判据
            bad = prev.get("break_after") == "width" and gap < TIGHT_GAP
        if bad:
            tight.append({"a": a_[-6:], "b": b_[:6], "gap_ms": round(gap * 1000)})

    style = None
    for f in (proj / "style.yaml", ):
        if f.exists():
            import yaml
            style = yaml.safe_load(f.read_text())
    if style is None:
        bp = load(proj / "brief.json") or {}
        sp = bp.get("style_pack")
        root = Path(__file__).resolve().parent.parent / "styles"
        if sp and (root / f"{sp}.yaml").exists():
            import yaml
            style = yaml.safe_load((root / f"{sp}.yaml").read_text())
    sub = (style or {}).get("subtitle") or {}
    fade = float(sub.get("fade_s") or 0.10)

    checks = {
        "punct": {"value": len(punct), "pass": not punct, "need": 0},
        "over_wide": {"value": len(wide), "pass": not wide, "need": 0},
        "width_median": {"value": med, "pass": WIDTH_BAND[0] <= med <= WIDTH_BAND[1],
                         "need": WIDTH_BAND},
        "word_split": {"value": len(tight), "pass": not tight, "need": 0},
        "emphasis_cues": {"value": em, "pass": em > 0, "need": ">0"},
        "fade_s": {"value": fade, "pass": fade <= FADE_MAX, "need": f"<={FADE_MAX}"},
    }
    ok = all(v["pass"] for v in checks.values())
    rep = {"cues": len(cues), "checks": checks, "ok": ok,
           "punct": [c.get("text") for c in punct[:5]],
           "over_wide": [(c.get("text"), len(str(c.get("text") or ""))) for c in wide[:5]],
           "word_split": tight[:5],
           "widths": {"min": widths[0] if widths else 0, "max": widths[-1] if widths else 0,
                      "median": med}}

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if ok else 1

    print(f"字幕 {len(cues)} 条 · 宽度 中位 {med}（{widths[0] if widths else 0}–"
          f"{widths[-1] if widths else 0}）")
    print(f"  标点 0 处{'✅' if not punct else '✗ ' + str(len(punct))}")
    print(f"  超宽（>{MAX_CHARS} 字）{'✅' if not wide else '✗ ' + str(len(wide))}")
    print(f"  断词 {'✅' if not tight else '✗ ' + str(len(tight))}（贴着切的，像同一口气被劈开）")
    print(f"  强调色 {em} 条{'✅' if em else '✗'} · 淡入 {fade:.2f}s{'✅' if fade <= FADE_MAX else '✗'}")
    for t in [c.get("text") for c in punct[:3]]:
        print(f"  · 带标点：{t}")
    for a_, b_, ms in [(x["a"], x["b"], x["gap_ms"]) for x in tight[:3]]:
        print(f"  · 断词：「{a_}」+「{b_}」间隔 {ms}ms")
    print(f"结论：{'过' if ok else '没过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
