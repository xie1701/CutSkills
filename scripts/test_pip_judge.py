#!/usr/bin/env python3
"""T-012 回归：PiP 判据的三层负样本必须红（缺键 / 贴回失败 / 数量不足）。

T-005 的 PiP 判据曾在「meta 有 pips、pips_applied 缺键」时默认成功，
让「raw 成片冒充 + 删键」拿到 24/0/0。这个测试直接调用 verify_film 的
纯函数，不依赖任何外部成片，锁死四条：
  A. pips_applied 缺键/None/false → pip_applied 必须 False（只有显式 true 才算成功）；
  B. timeline 要 PiP、meta 没有 → 红；
  C. meta 声明了但贴回失败 → 红；
  D. 「raw 冒充 + 删键」→ 用 pip_applied({}) 喂 pip_verdict，必须红；
  E. 数量不足（want 2 / meta 1）→ 红；无要求时贴回失败只黄，不硬红。

用法：python3 scripts/test_pip_judge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from verify_film import pip_applied, pip_verdict  # noqa: E402

ONE = [{"n": 1}]
TWO = [{"n": 1}, {"n": 2}]


def main() -> int:
    # A. 只有显式 true 才算贴回成功；缺键 / None / false 一律 False
    assert pip_applied({}) is False, "缺 pips_applied 被当成了成功（T-005 假绿口子）"
    assert pip_applied({"pips_applied": None}) is False, "None 被当成了成功"
    assert pip_applied({"pips_applied": False}) is False
    assert pip_applied({"pips_applied": True}) is True

    # B. 声明层：timeline 要、meta 没有
    v = pip_verdict(ONE, [], False)
    assert v is not None and v[0] == "red" and "没有 pips" in v[1], f"声明缺失未判红: {v}"

    # C. 管线层：meta 声明了但贴回失败
    v = pip_verdict(ONE, ONE, False)
    assert v is not None and v[0] == "red" and "pips_applied=false" in v[1], f"贴回失败未判红: {v}"

    # D. 打回原案：raw 成片 + meta 保留 pips + 删掉 applied 键
    v = pip_verdict(ONE, ONE, pip_applied({}))
    assert v is not None and v[0] == "red", f"raw 冒充 + 删键未判红: {v}"

    # E. 数量层：want 2 / meta 1
    v = pip_verdict(TWO, ONE, True)
    assert v is not None and v[0] == "red" and "想要 2 格" in v[1], f"数量不足未判红: {v}"

    # 无要求时贴回失败只黄（不把没启用 PiP 的片子判成硬伤）
    v = pip_verdict([], ONE, False)
    assert v is not None and v[0] == "yellow", f"无要求却判了硬红: {v}"

    # 三层都过 → None，交给像素层；没有 B 轴格也没有 pips → None
    assert pip_verdict(ONE, ONE, True) is None, "三层都过却仍报警"
    assert pip_verdict([], [], False) is None, "没有 PiP 的片子被误判"

    print("✅ PiP 判据负样本通过：缺键=False / 声明缺失、贴回失败、数量不足均红 / 无要求失败仅黄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
