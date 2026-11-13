#!/usr/bin/env python3
"""MG 新卡型（proportion / day-night / chips）结构与词证/修辞误用回归（T-020）。

覆盖内容：
  1. `render_hf._mg_html()` 三型 DOM 结构断言（关键 class / 字段 / 百分比数值 / 名牌枚数）；
  2. `check_cards.audit()` 4 条正反样本：
     - 正样本：真词证合规（台词中有对应词）→ 0 error、0 误报；
     - 负样本 1：编造词证（卡面说明/名牌台词里没有）→ error；
     - 负样本 2：互补时段误用 versus（早市 vs 晚市画成死敌擂台）→ warning；
     - 负样本 3：互斥优劣误用 day-night（传统起壳 vs 现代打磨画成时段接力）→ warning。

运行方式：
  source scripts/runtime.sh env && python3 scripts/test_mg_kinds.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_cards  # noqa: E402
import evidence_guard as EG  # noqa: E402
import render_hf as RH  # noqa: E402


def test_mg_html_structures() -> None:
    print("[1/2] 结构断言：render_hf._mg_html() 三型卡...")

    # --- 1. proportion 比例切分条 ---
    c_prop = {
        "kind": "proportion",
        "start": 1.0,
        "end": 4.0,
        "text": "明档厨房占三分之一",
        "data": {
            "value": "200",
            "total": "600",
            "unit": "㎡",
            "ratio": "1/3",
            "part_label": "明档厨房",
            "total_label": "全店面积",
            "label": "三分之一明档 现做现卖",
            "kicker": "空间规划",
        },
    }
    h_prop = RH._mg_html(c_prop, 0)
    assert "mg-proportion" in h_prop, "proportion 缺失外层 .mg-proportion"
    assert "mg-prop-hero" in h_prop, "proportion 缺失 .mg-prop-hero"
    assert '<span class="ratio">1/3</span>' in h_prop, "proportion 缺失 1/3 比值"
    assert '<span class="prop-tag">明档厨房</span>' in h_prop, "proportion 缺失局部标签"
    assert 'class="mg-prop-track"' in h_prop, "proportion 缺失进度轨"
    assert 'style="width:33.3%"' in h_prop, "proportion 缺失 33.3% 宽度算式"
    assert "mg-prop-pin" in h_prop, "proportion 缺失游标点 .mg-prop-pin"
    assert "明档厨房 200㎡" in h_prop, "proportion 缺失局部说明刻度"
    assert "全店面积 600㎡" in h_prop, "proportion 缺失整体说明刻度"
    assert "三分之一明档 现做现卖" in h_prop, "proportion 缺失底部主张文字"
    print("  ✓ proportion: ratio/tag/track/fill(33.3%)/ticks/label 结构完整")

    # --- 2. day-night 时段接力卡 ---
    c_dn = {
        "kind": "day-night",
        "start": 1.0,
        "end": 4.0,
        "text": "全时段双轮驱动",
        "data": {
            "title": "全时段运营",
            "day": {
                "tag": "早市",
                "head": "朝早",
                "body": "纯手工早茶",
                "sub": "7:00-11:00",
            },
            "night": {
                "tag": "晚市",
                "head": "晚上",
                "body": "滋补桑拿鸡",
                "sub": "17:00-22:00",
            },
            "relay": "接力",
            "tail": "早茶晚鸡 全日无休",
        },
    }
    h_dn = RH._mg_html(c_dn, 1)
    assert "mg-day-night" in h_dn, "day-night 缺失外层 .mg-day-night"
    assert "mg-dn-relay" in h_dn, "day-night 缺失接力容器 .mg-dn-relay"
    assert "side day" in h_dn and "side night" in h_dn, "day-night 缺失 day/night 两侧"
    assert "朝早" in h_dn and "纯手工早茶" in h_dn and "7:00-11:00" in h_dn, "day 侧内容缺失"
    assert "晚上" in h_dn and "滋补桑拿鸡" in h_dn and "17:00-22:00" in h_dn, "night 侧内容缺失"
    assert '<div class="dn-pill">接力</div>' in h_dn, "day-night 缺失接力胶囊"
    assert '<div class="dn-arrow">→</div>' in h_dn, "day-night 缺失接力流向箭头"
    assert "早茶晚鸡 全日无休" in h_dn, "day-night 缺失尾注 .mg-tail"
    print("  ✓ day-night: relay/sides/tags/heads/flow-pill/arrow/tail 结构完整")

    # --- 3. chips 招牌名牌卡 ---
    c_chips = {
        "kind": "chips",
        "start": 1.0,
        "end": 4.0,
        "text": "四大招牌点心",
        "data": {
            "title": "四大招牌",
            "items": [
                {"name": "鲜虾饺", "tag": "手工现包"},
                {"name": "叉烧包", "tag": "老面发酵"},
                {"name": "红米肠", "tag": "现点现拉"},
            ],
            "tail": "每日现做",
        },
    }
    h_chips = RH._mg_html(c_chips, 2)
    assert "mg-chips" in h_chips, "chips 缺失外层 .mg-chips"
    assert "chips-grid" in h_chips, "chips 缺失 .chips-grid"
    assert h_chips.count('class="chip"') == 3, f"chips 应有 3 枚名牌，实际: {h_chips.count('class=\"chip\"')}"
    for idx_s, name, tag in [("01", "鲜虾饺", "手工现包"), ("02", "叉烧包", "老面发酵"), ("03", "红米肠", "现点现拉")]:
        assert f'<span class="chip-num">{idx_s}</span>' in h_chips, f"chips 缺失编号 {idx_s}"
        assert f'<span class="chip-name">{name}</span>' in h_chips, f"chips 缺失名牌 {name}"
        assert f'<span class="chip-tag">{tag}</span>' in h_chips, f"chips 缺失标签 {tag}"
    assert "四大招牌" in h_chips and "每日现做" in h_chips, "chips 缺失标题与尾注"
    print("  ✓ chips: grid/3-chips/numbered/names/tags/title/tail 结构完整")


def _setup_mock_proj(tmp_path: Path, aid: str, spoken_text: str, card: dict, card_timing: dict | None = None) -> tuple[Path, Path]:
    lib = tmp_path / "lib"
    asr = lib / "_cutskills" / "cache" / "asr"
    asr.mkdir(parents=True, exist_ok=True)
    kh = EG.h12(aid)
    tokens = list(spoken_text)
    stamps = [round(0.1 * i, 2) for i in range(len(tokens))]
    (asr / f"{kh}.json").write_text(json.dumps({"tokens": tokens, "timestamps": stamps}, ensure_ascii=False))

    proj = tmp_path / "proj"
    (proj / "out").mkdir(parents=True, exist_ok=True)

    dur = max(30.0, float(stamps[-1] if stamps else 4.0) + 1.0)
    tl = {
        "elements": [
            {
                "n": 1,
                "dur": dur,
                "audio": {"asset": aid, "in": 0.0, "out": min(dur, float(stamps[-1] if stamps else 4.0) + 0.5)},
                "card": card,
            }
        ]
    }
    cards = [
        {
            "seg": 1,
            "kind": card.get("kind"),
            "text": card.get("text", ""),
            "start": card_timing.get("start", 0.5) if card_timing else 0.5,
            "end": card_timing.get("end", 3.5) if card_timing else 3.5,
            "anchor_t": card_timing.get("anchor_t", 0.3) if card_timing else 0.3,
            "anchor_word": card_timing.get("anchor_word", tokens[0] if tokens else "词"),
            "data": card.get("data", {}),
        }
    ]
    (proj / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    (proj / "out" / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return proj, lib


def test_check_cards_samples() -> None:
    print("\n[2/2] check_cards 正反样本断言（4 条）...")
    td = Path(tempfile.mkdtemp(prefix="test_mg_kinds_"))
    try:
        aid = "audio_sample.mov"

        # --- 样本 1：真词证合规（放行） ---
        spoken_1 = "明档厨房占地两百平全店六百平朝早手工早餐晚上桑拿鸡鲜虾饺叉烧包红米肠"
        card_1 = {
            "kind": "proportion",
            "text": "明档厨房占三分之一",
            "data": {
                "value": "200",
                "total": "600",
                "unit": "平",
                "ratio": "1/3",
                "part_label": "明档厨房",
                "total_label": "全店",
                "label": "明档厨房",
            },
        }
        p1, l1 = _setup_mock_proj(td / "case1", aid, spoken_1, card_1, {"start": 0.5, "end": 3.5, "anchor_t": 0.3, "anchor_word": "明档厨房"})
        res1 = check_cards.audit(p1, l1)
        errs1 = [m for lv, m in res1 if lv == "error"]
        assert not errs1, f"样本 1（真词证合规）不应报 error，实际: {errs1}"
        print(f"  ✓ 样本 1（真词证合规）：0 error（放行），通过")

        # --- 样本 2：编造词证（报 error） ---
        spoken_2 = "朝早做早市手工早餐晚上做桑拿鸡"
        card_2 = {
            "kind": "proportion",
            "text": "太空舱科技展厅占三分之一",
            "data": {
                "value": "200",
                "total": "600",
                "unit": "平",
                "ratio": "1/3",
                "part_label": "太空舱",
                "total_label": "全店",
                "label": "太空舱",
            },
        }
        p2, l2 = _setup_mock_proj(td / "case2", aid, spoken_2, card_2, {"start": 0.5, "end": 3.5, "anchor_t": 0.3, "anchor_word": "朝早"})
        res2 = check_cards.audit(p2, l2)
        errs2 = [m for lv, m in res2 if lv == "error"]
        assert any("太空舱" in m and "没有着落" in m for m in errs2), f"样本 2 未检出编造说明错误，实际: {errs2}"
        bad_msg = next(m for m in errs2 if "太空舱" in m)
        print(f"  ✓ 样本 2（编造词证「太空舱」）：成功报 error 拦截（{bad_msg}）")

        # --- 样本 3：互补时段误用 versus（报 warning） ---
        spoken_3 = "我们朝早做纯手工早市早茶，晚上做滋补桑拿鸡晚市"
        card_3 = {
            "kind": "versus",
            "text": "早市对晚市",
            "data": {
                "left": {"head": "早市", "body": "朝早手工早茶"},
                "right": {"head": "晚市", "body": "晚上滋补桑拿鸡"},
            },
        }
        p3, l3 = _setup_mock_proj(td / "case3", aid, spoken_3, card_3, {"start": 0.5, "end": 3.5, "anchor_t": 0.3, "anchor_word": "朝早"})
        res3 = check_cards.audit(p3, l3)
        warns3 = [m for lv, m in res3 if lv == "warning"]
        assert any("严禁误用带 VS 圆章的 versus 卡把自家互补业态画成死敌 PK" in m for m in warns3), (
            f"样本 3 未检出互补时段误用 versus warning，实际: {warns3}"
        )
        print(f"  ✓ 样本 3（互补时段误用 versus）：成功报 warning 引导改用 day-night（{warns3[0]}）")

        # --- 样本 4：互斥优劣误用 day-night（报 warning） ---
        spoken_4 = "以前传统旧做法容易起壳淘汰，现代升级新做法精细打磨"
        card_4 = {
            "kind": "day-night",
            "text": "工艺对比",
            "data": {
                "day": {"head": "旧做法", "body": "容易起壳"},
                "night": {"head": "现代升级", "body": "精细打磨"},
                "relay": "升级",
            },
        }
        p4, l4 = _setup_mock_proj(td / "case4", aid, spoken_4, card_4, {"start": 0.5, "end": 3.5, "anchor_t": 0.3, "anchor_word": "传统"})
        res4 = check_cards.audit(p4, l4)
        warns4 = [m for lv, m in res4 if lv == "warning"]
        assert any("属于互斥优劣对比而非时段接力——互斥对立应选用 versus 卡" in m for m in warns4), (
            f"样本 4 未检出互斥优劣误用 day-night warning，实际: {warns4}"
        )
        print(f"  ✓ 样本 4（互斥优劣误用 day-night）：成功报 warning 引导改用 versus（{warns4[0]}）")

    finally:
        shutil.rmtree(td, ignore_errors=True)


def main() -> int:
    test_mg_html_structures()
    test_check_cards_samples()
    print("\n✅ test_mg_kinds.py: 全部结构断言与 4 条正反样本通过！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
