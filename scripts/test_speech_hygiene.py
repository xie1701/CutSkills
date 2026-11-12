#!/usr/bin/env python3
"""speech_hygiene 的合同测试：五类残渣必须各自被抓到。

用的是 manifest 里五段真音频的**文字真值**（不跑 ASR，秒级），
锁的是判据的方向：该红的红，该绿的绿。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speech_hygiene as SH  # noqa: E402


def ws(text: str, t0: float = 0.0, step: float = 0.22) -> list[tuple[str, float]]:
    return [(c, round(t0 + k * step, 3)) for k, c in enumerate(text)]


CASES = [
    # (名字, 词序列, 期望命中的判据关键字, 期望是否有 error)
    ("重说：唔系唔系",
     ws("唔系唔系因为纯手工厨房系大"), "重新起句", True),
    ("场记：o k o k",
     ws("绝对品质 o k o k 因为咧我觉得咧"), "口水", True),
    ("口水尾巴：因为咧我觉得咧",
     ws("所以厨房先要大因为咧我觉得咧因"), "口水", True),
    ("同一句说两遍",
     ws("空间高先可以坐得舒服空间感高先会坐得舒服"), "说了两遍", True),
    ("正常一句",
     ws("我花咗三百万喺金湾开咗间六百平嘅点赞茶楼"), "", False),
    ("半句起头：切在词中间",
     ws("咧都系我哋嘅一个两百平嘅厨房"), "切在词中间", True),
    ("半句起头：就系",
     ws("，就系朝早做纯手工嘅早茶"), "半句起头", False),
    ("尾巴悬空：因为",
     ws("所以厨房先要大因为"), "尾巴", True),
    ("句号收尾：不报悬空",
     ws("晚上就系做桑拿鸡。"), "", False),
]


def main() -> int:
    bad = 0
    for name, words, want, want_err in CASES:
        end = (words[-1][1] + 0.20) if words else 1.0
        el = {"n": 1, "kind": "clip", "audio": {"asset": "x", "in": 0, "out": end}}
        got = SH.per_element(el, words)
        errs = [m for lv, m in got if lv == "error"]
        hit = any(want in m for _, m in got) if want else True
        ok = hit and (bool(errs) == want_err)
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            for lv, m in got:
                print(f"      {lv}: {m}")
        bad += 0 if ok else 1
    print(f"\n口播洁净度：{len(CASES) - bad}/{len(CASES)} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
