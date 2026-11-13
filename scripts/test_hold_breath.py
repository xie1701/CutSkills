#!/usr/bin/env python3
"""T-029 合同测试：「字幕停留」的单呼吸组豁免，放行该放行的、别放过该抓的。

为什么要有这一份：`HOLD_MAX = 3.2` 量的是「一句话赖在屏幕上」，但它量不出语速。
慢语速的人一口气真能连讲 3.3–4.1 秒（`subs.py` 的规则是「没有停顿就不许拆」），
两条规则在慢语速上互锁，永远拆不出来——T-024 诊断里 `ground-01` / `proj79`
那几条 3.30–4.08s 就是这么红的。

2026-11-13 加的豁免只有一条：**窗口内实量不到 ≥0.30s 停顿**（单呼吸组）时，上限放到 4.5s。
它很容易写成「超过 3.2s 就放行」，那样正好把真正的「话停了字还赖着」全放过。
所以这一份测试必须**两头都钉住**：

  ① 正样本判过：3.30s / 3.60s 的连续话音（窗口内静音 0.00s）；
  ② 负样本判红：话约 1.9s 说完、字幕撑到 3.42s（尾巴 1.50s 静音）；
     中间停了 1.22s 还撑 4.38s；
  ③ 量不出静音时**不许**放行：同一个 3.6s 窗口，不给 probe 就是判红
     （「量不出」不等于「没有停顿」）；
  ④ 停顿恰好压在门槛上时按严格判：probe 报 0.299s → 放行，0.300s → 判红。

样本：`references/negative-samples/hold-breath-groups.json`（真实素材窗口）。
断言全部走产线函数——`check_subs.make_hold_probe()` / `hold_judge()` /
`longest_pause()`，测试自己一个判据都不写。跑法：

    python3 scripts/test_hold_breath.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_subs  # noqa: E402

FIX = HERE.parent / "references" / "negative-samples" / "hold-breath-groups.json"


def _case_project(root: Path, case: dict) -> Path:
    """把一条样本搭成一个最小项目：一段、一个镜头、窗口从 0 起。

    这样 cue 的成片时间 [0, dur] 唯一地映到素材时间 [src_in, src_in+dur]，
    走的是产线同一条 `make_hold_probe()`（shots.json ＋ timeline.json ＋ 素材库）。
    """
    proj = root / case["id"]
    (proj / "out").mkdir(parents=True, exist_ok=True)
    dur = round(float(case["dur"]), 3)
    (proj / "out" / "shots.json").write_text(json.dumps(
        [{"n": 1, "seg": 1, "in": 0.0, "out": dur, "dur": dur,
          "zoom": 1.0, "medium": "talking"}], ensure_ascii=False))
    (proj / "timeline.json").write_text(json.dumps(
        {"version": 4, "elements": [
            {"n": 1, "kind": "clip", "asset": case["asset"],
             "in": round(float(case["src_in"]), 3),
             "out": round(float(case["src_in"]) + dur, 3)}]}, ensure_ascii=False))
    return proj


def _cues(case: dict) -> list[dict]:
    return [{"start": 0.0, "end": round(float(case["dur"]), 3),
             "text": case["text"], "el": 1, "break_after": "clause"}]


def test_logic_offline():
    """不出网也要能跑：判据的边界（量不出 / 门槛两侧）。"""
    print("▶ 不停机判据（不碰素材）")
    cues = [{"start": 0.0, "end": 3.6, "text": "一口气讲了三点六秒", "el": 1}]

    # 量不出静音：probe 返回 None → 不许放行，按 3.2s 判
    bad, note = check_subs.hold_judge(cues, probe=lambda c: None)
    assert len(bad) == 1 and bad[0]["limit"] == check_subs.HOLD_MAX, \
        f"量不出静音时 3.6s 必须按 {check_subs.HOLD_MAX}s 判红，实际 {bad}"
    print("  ✅ 量不出静音 → 按 3.2s 判红（不拿「没量成」当「没停顿」）")

    # 给都不给 probe（老调用方）：同样按 3.2s
    bad2, _ = check_subs.hold_judge(cues)
    assert len(bad2) == 1 and bad2[0]["pause"] is None
    print("  ✅ 没有 probe → 同上，按 3.2s 判红")

    # 门槛两侧：0.299s 放行、0.300s 判红
    ok, note = check_subs.hold_judge(cues, probe=lambda c: round(check_subs.BREATH_MIN - 0.001, 3))
    assert not ok and note and note[0]["why"] == "single", f"0.299s 停顿应放行，实际 {ok}"
    bad3, _ = check_subs.hold_judge(cues, probe=lambda c: check_subs.BREATH_MIN)
    assert len(bad3) == 1 and bad3[0]["limit"] == check_subs.HOLD_MAX, \
        f"{check_subs.BREATH_MIN}s 停顿应判红，实际 {bad3}"
    print(f"  ✅ 停顿 {check_subs.BREATH_MIN - 0.001}s → 放行；{check_subs.BREATH_MIN}s → 判红")

    # 4.5s 是天花板，不是「长就放行」：5.0s 的单呼吸组也判红
    long_cue = [{"start": 0.0, "end": 5.0, "text": "太长了", "el": 1}]
    bad4, _ = check_subs.hold_judge(long_cue, probe=lambda c: 0.0)
    assert len(bad4) == 1 and bad4[0]["limit"] == check_subs.HOLD_MAX_SINGLE
    print(f"  ✅ 单呼吸组 5.0s 仍判红（上限 {check_subs.HOLD_MAX_SINGLE}s）")


def test_real_media(fix: dict) -> None:
    lib = Path(os.environ.get("CUTSKILLS_LIB") or fix["library"]).expanduser()
    if not (lib / "_cutskills" / "index.json").exists():
        print("  ℹ 素材库不在这台机器上，跳过真实素材用例（判据逻辑上面已经跑过）")
        return
    cases = fix["cases"]
    missing = [c for c in cases if not (lib / c["asset"]).exists()]
    if missing:
        print(f"  ℹ 缺素材 {[c['asset'] for c in missing]}，这几条跳过")
        cases = [c for c in cases if c not in missing]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for c in cases:
            proj = _case_project(root, c)
            probe = check_subs.make_hold_probe(proj, lib)
            assert probe is not None, f"{c['id']} 探针没造出来（shots/timeline/素材库缺一样）"
            cues = _cues(c)
            pause = probe(cues[0])
            assert pause is not None, f"{c['id']} 量不出静音"
            assert abs(pause - c["expect_pause"]) <= 0.02, \
                f"{c['id']} 实测静音 {pause}s ≠ 样本记的 {c['expect_pause']}s（素材变过？）"
            bad, note = check_subs.hold_judge(cues, probe=probe)
            got = "red" if bad else "pass"
            assert got == c["expect"], \
                f"{c['id']} {c['name']}：期望 {c['expect']}，实际 {got}（静音 {pause}s）"
            print(f"  ✅ {c['id']} {c['name']}：静音 {pause}s → {got}")


def main() -> int:
    print("▶ 开始运行字幕停留（单呼吸组）合同测试…")
    test_logic_offline()
    fix = json.loads(FIX.read_text())
    test_real_media(fix)
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
