#!/usr/bin/env python3
"""T-041 回归：`verify_film` 的「字幕密度」两侧必须同分母（整片分钟），且真判密度。

旧版期望值写的是 `chars_per_s / avg_chars * 60`，分母是**字幕覆盖**分钟，实测侧却是
整片分钟：两者一比，条数 N 直接约掉，判据量到的只剩「字幕覆盖率 C/T」。
证据（T-032 收窄字幕窗口之后露出来的）：

    ground-01  per_min 28.2（37 条 / 78.6s）
               旧期望 36    （4.28 字/秒 ÷ 7.19 字 × 60 = 条数 ÷ 字幕覆盖 62.22s）
               偏差 20.8%  == 1 − 62.22/78.60，与「条数」无关

所以这个文件锁四条：
  A. 参考片自身形态（113 条 / 126.58s / 8 字每条）→ 绿，53.6 条/分钟；
  B. ground-01 实测读数（37 条 / 78.6s / 266 字）→ 绿，不能再报假黄；
  C. 同字数、同片长，条数砍掉 1/3（37 → 25）→ **必须**黄（这是旧口径量不到的负样本）；
  D. 砍掉 2/3（37 → 12）→ 必须红；粗切到 12 字/条 → 也必须红（不能只挡一个方向）；
  E. 字幕覆盖时长从 62.2s 改成 7.4s，判定与读数一字不变 → 锁死「分母是整片分钟」；
  F. 文案里必须写明用的是什么分母（剪辑师要知道这个数怎么读）。

用法：python3 scripts/test_sub_density.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from verify_film import subtitle_density  # noqa: E402

LO, HI = 45, 58          # packs/P1-warm.yaml 的 density_per_min，参考片同口径
DUR = 78.60              # qizao-ground-01 成片时长
CHARS = 266              # 该片字幕总字数


def make_cues(n: int, chars: int, dur: float, cover: float) -> list[dict]:
    """n 条字幕，总字数 chars，总覆盖时长 cover（每条均分），首尾都合法。"""
    base, extra = divmod(chars, n)
    step = cover / n
    out = []
    for i in range(n):
        text = "字" * (base + (1 if i < extra else 0))
        out.append({"text": text, "start": round(i * step, 3),
                    "end": round((i + 1) * step, 3)})
    return out


def main() -> int:
    # A. 参考片自身必须绿：8 字/条 × 113 条在 126.58s 里就是 53.6 条/分钟
    ref = make_cues(113, 113 * 8, 126.58, 113 * 0.86)
    lvl, reading, note = subtitle_density(ref, 126.58, LO, HI)
    assert lvl == "green", f"参考片自身形态被判 {lvl}：{note}"
    assert reading.startswith("53.6"), f"参考片密度读数不对：{reading}"

    # B. ground-01 实测：37 条 / 78.6s / 266 字（覆盖 62.22s）
    sub = make_cues(37, CHARS, DUR, 62.22)
    lvl_b, reading_b, note_b = subtitle_density(sub, DUR, LO, HI)
    assert lvl_b == "green", f"ground-01 实测被报 {lvl_b}（T-041 假黄没消）：{note_b}"
    assert reading_b == "28.2 条/分钟", reading_b

    # E. 覆盖时长砍到 7.4s（片子里 90% 没字幕）→ 判定与读数一字不变
    tight = make_cues(37, CHARS, DUR, 7.4)
    lvl_e, reading_e, _ = subtitle_density(tight, DUR, LO, HI)
    assert (lvl_e, reading_e) == (lvl_b, reading_b), (
        f"分母还是覆盖时长：覆盖 62.2s 判 {lvl_b}，覆盖 7.4s 判 {lvl_e}")

    # C. 负样本：同字数同片长，条数砍掉 1/3（37 → 25），旧口径这是 0 偏差
    cut1 = make_cues(25, CHARS, DUR, 62.22)
    lvl_c, reading_c, note_c = subtitle_density(cut1, DUR, LO, HI)
    assert lvl_c in ("yellow", "red"), f"条数砍掉 1/3 仍判 {lvl_c}：{note_c}"
    assert reading_c.startswith("19.1"), reading_c

    # D. 砍掉 2/3（37 → 12）必须红；粗切到 12 字/条（37 条只有 12 条那么粗）也必须红
    cut2 = make_cues(12, CHARS, DUR, 62.22)
    lvl_d, _, note_d = subtitle_density(cut2, DUR, LO, HI)
    assert lvl_d == "red", f"条数砍掉 2/3 却只判 {lvl_d}：{note_d}"
    coarse = make_cues(28, 28 * 12, DUR, 62.22)       # 12 字一条 = 单行上限，仍算粗切
    lvl_d2, _, note_d2 = subtitle_density(coarse, DUR, LO, HI)
    assert lvl_d2 in ("yellow", "red"), f"12 字/条被放行（判据只挡一个方向）：{note_d2}"

    # F. 文案要写清分母，剪辑师才知道这个数怎么读
    assert "整片分钟口径" in note_b, f"说明里没说分母：{note_b}"

    # G. 没字幕 / 时长缺失 → 不编数
    assert subtitle_density([], DUR, LO, HI) is None
    assert subtitle_density([{"text": "  ", "start": 0, "end": 1}], DUR, LO, HI) is None
    assert subtitle_density(sub, 0, LO, HI) is None

    print("✅ 字幕密度分母口径 7/7：A 参考片 53.6🟢 · B ground-01 28.2🟢 · "
          "C 砍 1/3 → 19.1🟡 · D 砍 2/3 → 🔴 · E 覆盖 7.4s 判定不变 · "
          "F 文案写清整片分钟口径 · G 空字幕不编数")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
