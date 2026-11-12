#!/usr/bin/env python3
"""evidence_guard 的合同测试：判据会不会在**该红的时候红、该绿的时候绿**。

这个测试锁的是三件事：
  1. 编造的词（「放心」）必须被抓出来；
  2. 对着摄影师说的「你」被写成对观众说的，必须判 error——这是本案的原始病灶；
  3. ASR 的同音错字（「纯手工」听成「纯土工」）**不许**误判成编造。

第 3 条最容易写错方向：判据一紧就误杀，一松就放过，两头都会让人失去信任。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_guard as EG  # noqa: E402

CASES: list[tuple[str, str, str, str]] = [
    # (名字, 真值, 台词, 期望)
    ("编造：放心",
     "所以大家可以喺喺我得十一月份应该冇乜问题咯",
     "所以大家可以放心我哋十一月份应该冇乜问题",
     "caught"),
    ("ASR 错字：纯手工→纯土工",
     "唔系因为纯土工期望系大系因为纯土工系我哋嘅根本",
     "唔系因为纯手工厨房系大系因为纯手工系我哋嘅根本",
     "tolerated"),
    ("人称错位：你→我哋",
     "唔系因为纯手工厨房系大系因为纯手工系你嘅根本所以厨房先要大",
     "唔系因为纯手工厨房系大系因为纯手工系我哋嘅根本所以厨房先要大",
     "you"),
    ("删口水：允许",
     "唔系唔系因为纯手工厨房系大 o ok 因为咧我觉得咧",
     "因为纯手工厨房系大",
     "ok"),
    ("正常台词",
     "我花咗三百万喺金湾开咗间六百平嘅点赞茶楼",
     "我花咗三百万喺金湾开咗间六百平嘅点赞茶楼",
     "ok"),
]


def main() -> int:
    bad = 0
    for name, truth, ev, want in CASES:
        found, _ = EG.invented(truth, ev)
        keep = len(EG.core(ev)) / max(1, len(EG.core(truth)))
        you = truth.count("你") > ev.count("你") and keep >= 0.6
        if want == "caught":
            ok = len(found) >= 1
        elif want == "tolerated":
            ok = len(found) <= 1
        elif want == "you":
            ok = you
        else:
            ok = len(found) == 0
        print(f"  {'✓' if ok else '✗'} {name}｜编造 {''.join(found) or '无'}｜人称 {'错位' if you else '正常'}")
        bad += 0 if ok else 1
    print(f"\n台词真值守卫：{len(CASES) - bad}/{len(CASES)} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
