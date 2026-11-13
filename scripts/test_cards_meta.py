#!/usr/bin/env python3
"""MG 卡「剧组建事本」判据回归：谁说的、哪天拍的、什么设备——一律不许上屏。

Jerry 2026-11-13 21:53：「MG 卡片有类似『牛哥口述 2026-10-22』、『工地现场口述』
这些无意义的标注是不能出现在成片里的！」

这条判据容易两头错，所以两头都测：
- **松**：观众能拿去核对的真出处（菜单价 / 合同 / 店里实测）不许误杀；
- **紧**：口述/拍摄/日期/文件名/设备这五类，一个都不许漏到屏幕上。

    python3 scripts/test_cards_meta.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_cards as CC  # noqa: E402

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


# 必须判红：形状是「这段片怎么拍出来的」
BAD = [
    ("牛哥口述 · 2026-10-22", "口述/口播/原声类"),
    ("工地现场口述", "口述/口播/原声类"),
    ("熊工口播原声", "口述/口播/原声类"),
    ("拍摄于 2026-10-17 工地", "拍摄/素材/机位类"),
    ("素材：地面施工素材/IMG_1042", "拍摄/素材/机位类"),
    ("手机拍摄", "拍摄/素材/机位类"),
    ("大疆航拍", "文件名/设备"),
    ("DJI_20261005155601_0497", "文件名/设备"),
    ("口播/2026-10-22 151047.mov", "口述/口播/原声类"),
    ("2026-10-22 收工后", "日期"),
    ("2026年10月", "日期"),
]
# 必须放行：观众能核对的东西，或者干脆没有出处
GOOD = [
    "",
    "菜单价",
    "店里实测",
    "他自己报的数",
    "合同第 3 条",
    "三百万换六百平",
    "六百平 · 三楼",
    "早市 · 晚市",
    "两百平纯手工厨房",
]


def main() -> int:
    print("① 记事本类文案必须判红")
    for txt, want in BAD:
        hit = CC.meta_leak(txt)
        must(f"「{txt}」→ 红（{want}）", hit is not None and hit[0] == want, str(hit))

    print("② 真出处与正常文案不许误杀")
    for txt in GOOD:
        hit = CC.meta_leak(txt)
        must(f"「{txt or '（空）'}」→ 放行", hit is None, str(hit))

    print("③ 出处字段必须进判据视野（以前它根本不在 card_texts 里）")
    fields = CC.card_texts({"kind": "stat", "data": {"value": "600", "unit": "㎡",
                                                    "source": "牛哥口述 · 2026-10-22"}})
    must("stat 的 source 被收进 card_texts", "source" in fields, str(sorted(fields)))
    must("收进来的就是那句话", fields.get("source") == ["牛哥口述 · 2026-10-22"], str(fields.get("source")))

    print()
    if fails:
        print(f"❌ {len(fails)} 条没过：" + "、".join(fails))
        return 1
    print(f"✅ 记事本判据 3 组全过（判红 {len(BAD)} / 放行 {len(GOOD)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
