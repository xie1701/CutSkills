#!/usr/bin/env python3
"""check_timing 字幕后置判据的合同测试（T-040）：同音放行能放、编造照抓。

这条判据原来只数**字幕这边**多出来的实词（`evidence_guard.invented` 的方向），
ASR 一丢字，人写的正确字幕反倒被判「音频没说」。真案：

    qizao-naming-01 · 字幕「为什么不叫造漆我」｜词级真值「为不叫造为我没有」
    字幕这边多「什么漆」、音频这边多「为我没有」——账是平的，却被判 error。

现在的口径：双向对账，两边都过同一套同音放行（`evidence_guard.two_sided`，
声母/韵母层面）。**单边净多出 ≥2 个实词才是编造**；人称代词不参与放行。

四组样本缺一不可，缺哪组都会把判据推向一边：
  ① 同音/丢字对账 → **不许**报（否则误杀正确字幕，就是本单的原始病灶）；
  ② 字幕凭空加两个实词 → **必须**报（否则判据成了摆设）；
  ③ 人称错位（我/你）→ **必须**报（对谁说话反了，不是听错一个字）；
  ④ 音频多字（ASR 口水）→ 不许报（删字是允许的）。

断言统统落到 `check_timing.check()` 的真输出上，不测中间函数——测中间函数
只能证明函数对，证明不了判据接对了。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_timing  # noqa: E402
import evidence_guard as EG  # noqa: E402
from index_assets import h12  # noqa: E402

AID = "口播/自测_口播1.MOV"


def project_with(td: Path, tokens: list[str], stamps: list[float],
                 text: str, start: float, end: float) -> Path:
    """造一个只有一格声音 + 一条字幕的最小项目（真素材库形状，不走 mock）。"""
    lib = td / "lib"
    (lib / "_cutskills" / "cache" / "asr").mkdir(parents=True)
    (lib / "_cutskills" / "index.json").write_text(
        json.dumps({"assets": [{"id": AID}]}, ensure_ascii=False))
    (lib / "_cutskills" / "cache" / "asr" / f"{h12(AID)}-v3c00000.json").write_text(
        json.dumps({"tokens": tokens, "timestamps": stamps}, ensure_ascii=False))
    proj = lib / "_cutskills" / "projects" / "p"
    (proj / "out").mkdir(parents=True)
    (proj / "timeline.json").write_text(json.dumps({"elements": [{
        "n": 1, "kind": "clip", "track": "video", "asset": AID, "in": 0.0, "out": 4.0,
        "audio": {"asset": AID, "in": 0.0, "out": 4.0},
    }]}, ensure_ascii=False))
    (proj / "out" / "subs.json").write_text(json.dumps({"items": [{
        "start": start, "end": end, "text": text, "el": 1,
    }]}, ensure_ascii=False))
    return proj


# (名字, 词级真值 tokens, 时间戳, 字幕, 字幕起, 字幕止, 期望)
# 期望：0 = 必须零 error；2 = 必须报 error（且 ≥2 处）；swap = 必须报人称错位
CASES: list[tuple[str, list[str], list[float], str, float, float, object]] = [
    ("同音/丢字对账（naming-01 真案：什→为，音频这边同时多「为我没有」）",
     list("为不叫造为我没有"), [1.0, 1.2, 1.4, 1.6, 1.9, 2.1, 2.3, 2.5],
     "为什么不叫造漆我", 1.0, 2.2, 0),
    ("同音字（期/漆 同音节 qi）",
     list("造期物"), [1.0, 1.2, 1.4], "造漆物", 1.0, 1.6, 0),
    ("字幕凭空加两个实词（厨房装修好靓 / 音频只说厨房好靓）",
     list("厨房好靓"), [1.0, 1.2, 1.4, 1.6], "厨房装修好靓", 1.0, 1.8, 2),
    ("人称错位（字幕我 / 音频你）",
     list("你系边个"), [1.0, 1.2, 1.4, 1.6], "我系边个", 1.0, 1.8, "swap"),
    ("音频多字（ASR 口水「是」），字幕删字是允许的",
     list("就是以漆造物"), [1.0, 1.2, 1.4, 1.6, 1.8, 2.0], "以漆造物", 1.4, 2.2, 0),
    ("正常字幕",
     list("我花三百万"), [1.0, 1.2, 1.4, 1.6, 1.8], "我花三百万", 1.0, 2.0, 0),
]


def run_case(td: Path, tokens: list[str], stamps: list[float], text: str,
             start: float, end: float) -> list[tuple[str, str]]:
    proj = project_with(td, tokens, stamps, text, start, end)
    return check_timing.check(proj)


def main() -> int:
    bad = 0
    for name, tokens, stamps, text, start, end, want in CASES:
        with tempfile.TemporaryDirectory(prefix="timingsubs_") as t:
            v = run_case(Path(t), tokens, stamps, text, start, end)
        errs = [m for lv, m in v if lv == "error"]
        subs_errs = [m for m in errs if m.startswith("字幕")]
        other = [m for m in errs if not m.startswith("字幕")]
        if want == 0:
            ok = not errs
        elif want == 2:
            ok = len(subs_errs) == 1 and "装修" in subs_errs[0]
        else:                                   # swap
            ok = len(subs_errs) == 1 and "人称" in subs_errs[0]
        print(f"  {'✓' if ok else '✗'} {name}")
        print(f"      字幕 error {len(subs_errs)} 条｜" +
              ("；".join(m for m in subs_errs) if subs_errs else "无"))
        if other:
            print(f"      （另有非字幕 error {len(other)} 条：{other[0][:40]}）")
        bad += 0 if ok else 1

    # 同音放行必须是「声母/韵母」层面，不是「整串相似度」：两截对不上的字不许过。
    neg = [("什", "为"), ("我", "你"), ("漆", "为")]
    passed = [c for c in neg if EG.homophone(*c)]
    ok = not passed
    print(f"  {'✓' if ok else '✗'} 反例：不同音的字不许被放行｜误放 {passed or '无'}")
    bad += 0 if ok else 1

    total = len(CASES) + 1
    print(f"\n字幕后置判据：{total - bad}/{total} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
