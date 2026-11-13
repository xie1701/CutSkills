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


def lookup_cases() -> int:
    """查表口径的合同（T-030）：两套键都要查，查不到必须说「缺词级真值」。

    负样本是第③条：把缓存键换成一个不存在的键 → `source` 必须是 `sentence`，
    且 `check()` 必须报「没有词级真值」。不许静默退句级装成查过了（那会把
    真话判成「音频没说」），也不许在句级文本上判硬错（那是拿窗口错位冤枉台词）。
    """
    import json
    import tempfile

    aid = "素材/改名后_IMG_1069.MOV"
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td)
        cache = lib / "_cutskills" / "cache" / "asr"
        cache.mkdir(parents=True)
        (lib / "_cutskills" / "index.json").write_text(json.dumps({"assets": [{
            "id": aid, "legacy_key": "0123456789ab",
            "transcript": {"phrases": [{"text": "分色纸。", "start": 1.0, "end": 3.0}]},
        }]}, ensure_ascii=False))
        chunk = json.dumps({"tokens": ["分", "色", "纸"], "timestamps": [1.0, 1.2, 1.4]})

        # ① 只有老键的缓存（素材改过名）也要查得到
        (cache / "0123456789ab-v3c00000.json").write_text(chunk)
        t = EG.chars_of_asset(lib, aid)
        ok = t.source == EG.WORD and "".join(c for c, _ in t.chars) == "分色纸"
        print(f"  {'✓' if ok else '✗'} 老键 legacy_key 命中｜source={t.source}｜{''.join(c for c, _ in t.chars)}")
        bad += 0 if ok else 1

        # ② 新键也在时先新后旧，不跨键拼（同一个字不能算两遍）
        (cache / f"{EG.h12(aid)}-v3c00000.json").write_text(chunk)
        t = EG.chars_of_asset(lib, aid)
        ok = t.source == EG.WORD and len(t.chars) == 3
        print(f"  {'✓' if ok else '✗'} 两套键都存在：只读新键、不重复计数｜字 {len(t.chars)} 个（应是 3）")
        bad += 0 if ok else 1

        # ③ 负样本：键彻底改坏 → 必须报「缺词级真值」，不能假绿
        proj = lib / "_cutskills" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "timeline.json").write_text(json.dumps({"elements": [{
            "n": 8, "kind": "clip", "track": "video", "asset": aid,
            "in": 1.0, "out": 3.0, "evidence": "分色纸",
            "audio": {"asset": aid, "in": 1.0, "out": 3.0},
        }]}, ensure_ascii=False))
        real = EG.keys_of
        EG.keys_of = lambda lib_, asset_: ("deadbeefdead",)     # 只认一个不存在的键
        try:
            EG._words_of.cache_clear()
            t = EG.chars_of_asset(lib, aid)
            v = EG.check(proj, lib)
            said = [m for _, m in v if "没有词级真值" in m]
            ok = t.source == EG.SENTENCE and bool(said) and not [m for lv, m in v if lv == "error"]
            print(f"  {'✓' if ok else '✗'} 键改坏 → source={t.source}，报「缺词级真值」："
                  f"{said[0] if said else '静默装绿（假绿）'}")
            bad += 0 if ok else 1
        finally:
            EG.keys_of = real
            EG._words_of.cache_clear()

        # ④ 改回来以后必须恢复命中（证明③的“变黄”是键坏了，不是判据坏了）
        t = EG.chars_of_asset(lib, aid)
        ok = t.source == EG.WORD and len(t.chars) == 3
        print(f"  {'✓' if ok else '✗'} 键改回 → source={t.source}，字 {len(t.chars)} 个")
        bad += 0 if ok else 1
    return bad


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
    print("\n查表口径（两套键 + 退级不许静默）：")
    lbad = lookup_cases()
    return 1 if (bad or lbad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
