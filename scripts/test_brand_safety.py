#!/usr/bin/env python3
"""CutSkills · T-007 / T-036 品牌安全与第三方字样处置回归测试

为什么要有它：
商业片里出现未授权的第三方品牌字样、商标或手机号，会带来下架限流与侵权风险。
成片中曾出现 FESTOOL RO 150 打磨机、PPG 大师漆 T 恤、以及 protect-01 多达 11 段地保品牌。
本测试通过真实断言：
  1. FESTOOL / PPG 等第三方品牌在未处置时必须被精准拦截（unresolved WARN）；
  2. 元素标注处置（third_party_action）或 decisions.md 的**结构化核销行**后成功核销（resolved）；
  3. 干净片段合规放行；
  4. 真实项目对账：qizao-ground-01 与 qizao-protect-01 命中真实品牌片段。

T-036 判据变更（核销只认显式行）：
  decisions.md 是散文，判据不许读语感。T-035 自己写的一句「不在本单允许改动的两个路径里」
  配上同一行的 `1072`，就靠「允许」把 protect-01 #1 的品牌告警静默核销了（unresolved 11→10）。
  现在只有 `- [brand] #1 保留：<理由>` / `- [brand] #1 模糊：<理由>` / `- [brand] #1 裁掉`
  这一类结构化行算核销；散文一律不核销，「允许/可以/同意/认可」这类泛许可词不再单独构成核销，
  只由 ack_hints() 打一条「这句话没有被当作核销意见」的提示。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from brand_safety import (  # noqa: E402
    scan_third_party_risks, BRAND_PATTERN, ack_hints, is_brand_acknowledged,
)
from check_timeline import check as check_timeline_fn  # noqa: E402

MOCK_INDEX = {
    "asset_festool": {
        "id": "asset_festool",
        "tag": "地面打磨",
        "segments": [
            {"in": 0.0, "out": 5.0, "flags": ["机身带 FESTOOL RO 150 FEQ 标志", "工具品牌字样入镜"]}
        ]
    },
    "asset_ppg": {
        "id": "asset_ppg",
        "tag": "地坪摊料",
        "segments": [
            {"in": 0.0, "out": 6.0, "flags": ["后排白T印有第三方「PPG大师漆」字样"]}
        ]
    },
    "1072": {
        "id": "1072",
        "tag": "地保铺贴_必用",
        "segments": [
            {"in": 0.0, "out": 27.795,
             "flags": ["地面保护膜有第三方品牌字样（柒造 CICIAO / PPG大师漆 / by PPG）"]}
        ]
    },
    "asset_clean": {
        "id": "asset_clean",
        "tag": "纯净白墙",
        "segments": [
            {"in": 0.0, "out": 5.0, "flags": ["特写白墙平整度"]}
        ]
    },
}


def test_samples():
    # 1. 负样本 1：FESTOOL RO 150 未处置拦截
    items_festool = [{"n": 1, "asset": "asset_festool", "in": 0.0, "out": 4.0}]
    unres, res = scan_third_party_risks(items_festool, MOCK_INDEX)
    assert len(unres) == 1, f"FESTOOL 应被拦截，实际 unresolved={len(unres)}"
    assert any("FESTOOL" in f for f in unres[0]["flags"]), f"需明确包含 FESTOOL: {unres[0]['flags']}"
    assert len(res) == 0
    print("  ✅ 负样本 1（FESTOOL RO 150 未处置）被精准拦截：", unres[0]["flags"])

    # 2. 负样本 2：PPG 大师漆 未处置拦截
    items_ppg = [{"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0}]
    unres_ppg, res_ppg = scan_third_party_risks(items_ppg, MOCK_INDEX)
    assert len(unres_ppg) == 1, f"PPG 应被拦截，实际 unresolved={len(unres_ppg)}"
    assert any("PPG" in f for f in unres_ppg[0]["flags"]), f"需明确包含 PPG: {unres_ppg[0]['flags']}"
    print("  ✅ 负样本 2（PPG大师漆 未处置）被精准拦截：", unres_ppg[0]["flags"])

    # 3. 核心正样本（【脉动】指示：工具无需避开/保留出镜）—— 写成结构化行才算
    decisions_keep = """# decisions
- [brand] #1 保留：FESTOOL 打磨机是施工实拍的真实施工工具，客户认可无需避开
"""
    unres_ack, res_ack = scan_third_party_risks(items_festool, MOCK_INDEX, decisions_text=decisions_keep)
    assert len(unres_ack) == 0, f"结构化行「保留」后应核销，实际仍有 {len(unres_ack)} 项未解决"
    assert len(res_ack) == 1, f"核销后应进入 resolved，实际 resolved={len(res_ack)}"
    assert "结构化核销" in res_ack[0]["resolved_reason"]
    print("  ✅ 核心正样本（结构化行「- [brand] #1 保留：…」）：成功核销放行：", res_ack[0]["resolved_reason"])

    # 4. 关键负样本（Cola 打回项①：否定/整改指示严禁被误核销）
    decisions_reject = """# decisions
- [brand] #1 裁掉：FESTOOL 打磨机不能保留，必须打码或换段
"""
    unres_rej, res_rej = scan_third_party_risks(items_festool, MOCK_INDEX, decisions_text=decisions_reject)
    assert len(unres_rej) == 0, f"结构化行明确写了「裁掉」＝已拍板处置，实际 unres={len(unres_rej)}"
    assert len(res_rej) == 1 and "结构化核销" in res_rej[0]["resolved_reason"]
    # 散文版否定指令（T-007 原案）仍然不得核销
    decisions_reject_prose = """# decisions
- 第三方字样：#1 FESTOOL 打磨机不能保留，必须打码或换段。
"""
    unres_rej2, res_rej2 = scan_third_party_risks(items_festool, MOCK_INDEX, decisions_text=decisions_reject_prose)
    assert len(unres_rej2) == 1, f"散文否定指令绝不能被当成核销，实际 unres={len(unres_rej2)}"
    assert len(res_rej2) == 0, f"散文不得进入 resolved，实际 resolved={len(res_rej2)}"
    print("  ✅ 关键负样本（「裁掉」核销 / 散文「不能保留，必须换段」不核销）：口径正确")

    # 4.1 第二轮复核打回项①：跨段防误核销（决不能因泛指词「第三方/字样」导致 #2 的核销把 #1 也带走）
    items_multi = [
        {"n": 1, "asset": "asset_festool", "in": 0.0, "out": 4.0},
        {"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0}
    ]
    decisions_only_2 = """# decisions
- [brand] #2 保留：PPG 客户认可保留
"""
    unres_m, res_m = scan_third_party_risks(items_multi, MOCK_INDEX, decisions_text=decisions_only_2)
    assert len(unres_m) == 1 and unres_m[0]["n"] == 1, f"#1 FESTOOL 绝不能被跨段误核销！实际 unres={unres_m}"
    assert len(res_m) == 1 and res_m[0]["n"] == 2, f"#2 PPG 应正常核销！实际 res={res_m}"
    print("  ✅ 第二轮复核打回项①（跨段防误核销）：#2 的核销绝不跨段带走 #1")

    # 4.2 第二轮复核打回项②：未决弱语义拒收（「先保留，待确认」或「待定」绝不能被当成核销）
    decisions_pending1 = """# decisions
- [brand] #1 保留：先保留，待确认
"""
    unres_p1, res_p1 = scan_third_party_risks(items_festool, MOCK_INDEX, decisions_text=decisions_pending1)
    assert len(unres_p1) == 1 and len(res_p1) == 0, f"「待确认」弱语义绝不能被核销！实际 unres={unres_p1}, res={res_p1}"
    decisions_pending2 = """# decisions
- [brand] #1 保留：施工工具保留，方案待定再议。
"""
    unres_p2, res_p2 = scan_third_party_risks(items_festool, MOCK_INDEX, decisions_text=decisions_pending2)
    assert len(unres_p2) == 1 and len(res_p2) == 0, f"「待定再议」弱语义绝不能被核销！实际 unres={unres_p2}, res={res_p2}"
    print("  ✅ 第二轮复核打回项②（弱语义防误核销）：「先保留，待确认」与「待定再议」精准拦截")

    # 5. 核销样本 2：元素里显式标注 third_party_action
    items_keep = [{"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0, "third_party_action": "keep"}]
    unres_keep, res_keep = scan_third_party_risks(items_keep, MOCK_INDEX)
    assert len(unres_keep) == 0 and len(res_keep) == 1
    assert "元素显式标注处置：keep" in res_keep[0]["resolved_reason"]
    print("  ✅ 核销样本 2（元素标注 third_party_action: keep）：成功核销：", res_keep[0]["resolved_reason"])

    items_crop = [{"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0, "third_party_action": "crop"}]
    unres_crop, res_crop = scan_third_party_risks(items_crop, MOCK_INDEX)
    assert len(unres_crop) == 0 and len(res_crop) == 1
    assert "元素显式标注处置：crop" in res_crop[0]["resolved_reason"]
    print("  ✅ 核销样本 3（元素标注 third_party_action: crop）：成功核销：", res_crop[0]["resolved_reason"])

    # 6. 正样本：合规纯净片段放行
    items_clean = [{"n": 3, "asset": "asset_clean", "in": 0.0, "out": 4.0}]
    unres_cln, res_cln = scan_third_party_risks(items_clean, MOCK_INDEX)
    assert len(unres_cln) == 0 and len(res_cln) == 0
    print("  ✅ 正样本（合规纯净素材）：0 拦截 0 报警")


def test_t036_structured_only():
    """T-036 四组负样本：核销必须是显式行，散文里的动词一律不算。"""
    items_1072 = [{"n": 1, "asset": "1072", "in": 23.0, "out": 26.5}]

    # 负样本 A（T-035 原话原样写回）：散文里出现「允许」+ 同一行的 1072 → 不核销
    decisions_prose = (
        "# decisions\n"
        "| 2026-11-13 10:28 | T-035：1072 的 L2 补到 27.795s | brand_safety 复核：unresolved 9→11，新增 #1 [23.0,26.5]「地面保护膜有第三方品牌字样（柒造 CICIAO / PPG大师漆 / by PPG）」，"
        "index.json 没有重写（不在本单允许改动的两个路径里），落 index 要另跑 index_assets。 | ✅ 接受 |\n"
    )
    hints: list[str] = []
    unres, res = scan_third_party_risks(items_1072, MOCK_INDEX, decisions_text=decisions_prose, hints=hints)
    assert len(unres) == 1, f"散文里的「允许」绝不能被当成核销！实际 unres={len(unres)}, res={res}"
    assert len(res) == 0, f"散文不得进 resolved，实际 res={res}"
    assert hints, "看起来像在说品牌的散文必须打一条「没被当作核销意见」的提示"
    assert hints[0].startswith("第 2 行「"), f"提示要指出具体行：{hints[0]}"
    print("  ✅ 负样本 A（「不在本单允许改动的两个路径里」+ 1072）：不核销，且给出散文提示：", hints[0][:50])

    # 正样本 B：结构化行 → 核销
    unres_b, res_b = scan_third_party_risks(
        items_1072, MOCK_INDEX, decisions_text="# decisions\n- [brand] #1 保留：客户授权露出\n")
    assert len(unres_b) == 0 and len(res_b) == 1, f"结构化行必须核销，实际 unres={unres_b}, res={res_b}"
    print("  ✅ 正样本 B（`- [brand] #1 保留：客户授权露出`）：核销")

    # 负样本 C：「#1 这个不用改，允许留着」→ 不核销 + warning
    prose_c = "# decisions\n#1 这个不用改，允许留着\n"
    unres_c, res_c = scan_third_party_risks(items_1072, MOCK_INDEX, decisions_text=prose_c, hints=hints)
    assert len(unres_c) == 1 and len(res_c) == 0, f"泛许可词散文不核销，实际 unres={unres_c}, res={res_c}"
    hints_c = ack_hints(prose_c)
    assert hints_c, f"「允许留着」必须给提示，实际 {hints_c}"
    print("  ✅ 负样本 C（「#1 这个不用改，允许留着」）：不核销，给出提示：", hints_c[0][:40])

    # 负样本 D：同一行既有「保留」又有「不保留」→ 没拍板，不核销
    for clash in (
        "- [brand] #1 保留：先留着，但另一处不保留",
        "- [brand] #1 保留：不保留，客户还在犹豫",
        "- [brand] #1 保留：留着 · 不能保留",
    ):
        unres_d, res_d = scan_third_party_risks(items_1072, MOCK_INDEX, decisions_text=clash + "\n")
        assert len(unres_d) == 1 and len(res_d) == 0, f"「保留」与「不保留」同现不得核销：{clash} → {unres_d}/{res_d}"
    print("  ✅ 负样本 D（同一行既「保留」又「不保留」）：不核销")

    # 附带：泛许可词表本身不得再单独核销（原漏洞的直接回归）
    for prose in ("允许", "可以", "同意", "认可", "允许保留 #1", "#1 允许"):
        ok, why = is_brand_acknowledged(1, ["PPG大师漆"], "1072", prose)
        assert not ok, f"泛许可词「{prose}」不得单独构成核销，实际 acked={ok} {why}"
    print("  ✅ 附带回归（「允许/可以/同意/认可」不再单独核销）")


def test_timeline_integration():
    mock_index = {
        "asset_festool": {
            "id": "asset_festool",
            "tag": "地面打磨",
            "segments": [{"in": 0.0, "out": 5.0, "flags": ["机身带 FESTOOL RO 150 FEQ 标志"]}]
        }
    }
    tl = {
        "version": 4,
        "duration_s": 4.0,
        "elements": [
            {"n": 1, "kind": "clip", "track": "video", "asset": "asset_festool",
             "in": 0.0, "out": 4.0, "why": "施工过程说明让观众明白"}
        ]
    }

    # 未留痕时 check_timeline 必报 warning
    probs = check_timeline_fn(tl, mock_index)
    brand_warns = [m for lv, m in probs if lv == "warning" and "第三方品牌字样" in m]
    assert len(brand_warns) == 1, f"check_timeline 应报第三方品牌字样 warning，实际: {probs}"
    assert "FESTOOL" in brand_warns[0]
    print("  ✅ check_timeline 集成：未处置时报出 WARN：", brand_warns[0][:40], "…")

    # 结构化行写进 decisions.md 后，warning 自动消失
    dec_text = "- [brand] #1 保留：FESTOOL 打磨机是施工实拍真实工具，客户认可无需避开\n"
    probs_ack = check_timeline_fn(tl, mock_index, decisions_text=dec_text)
    brand_warns_ack = [m for lv, m in probs_ack if lv == "warning" and "第三方品牌字样" in m]
    assert len(brand_warns_ack) == 0, f"check_timeline 在结构化核销后应无第三方品牌字样 warning，实际: {brand_warns_ack}"
    print("  ✅ check_timeline 集成：结构化核销行后 WARN 消失")

    # 散文不核销，且 check_timeline 要提醒「这句没被当作核销意见」
    dec_prose = "- 第三方字样：#1 FESTOOL 不用改，允许留着出镜。\n"
    probs_prose = check_timeline_fn(tl, mock_index, decisions_text=dec_prose)
    still = [m for lv, m in probs_prose if lv == "warning" and "第三方品牌字样" in m]
    hints = [m for lv, m in probs_prose if lv == "warning" and "没有被当作核销意见" in m]
    assert len(still) == 1, f"散文不得核销，warning 必须还在：{probs_prose}"
    assert len(hints) == 1, f"散文必须给一条「没被当作核销意见」的提示：{probs_prose}"
    print("  ✅ check_timeline 集成：散文仍报 WARN，并附「没被当作核销意见」提示")


def test_real_projects():
    lib_index_f = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/index.json")
    if not lib_index_f.exists():
        print("  ℹ 素材库 index 不存在，跳过真实素材对账")
        return

    real_index = {x["id"]: x for x in json.loads(lib_index_f.read_text())["assets"]}
    pdir = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects")

    # 对账 qizao-ground-01
    ground_tl_f = pdir / "qizao-ground-01" / "timeline.json"
    if ground_tl_f.exists():
        ground_tl = json.loads(ground_tl_f.read_text())
        clip_items = [e for e in ground_tl.get("elements", []) if e.get("kind") == "clip"]
        unres, _ = scan_third_party_risks(clip_items, real_index)
        assert len(unres) >= 2, f"ground-01 至少应检出 2 段品牌字样，实际 {len(unres)}"
        flags_flat = [f for r in unres for f in r["flags"]]
        assert any("品牌" in f or "工具" in f for f in flags_flat)
        print(f"  ✅ 真实项目 ground-01 对账成功：检出 {len(unres)} 段第三方品牌字样")

    # 对账 qizao-protect-01：T-036 的口子就在这一片（#1 / 1072）
    protect_dir = pdir / "qizao-protect-01"
    protect_tl_f = protect_dir / "timeline.json"
    if protect_tl_f.exists():
        protect_tl = json.loads(protect_tl_f.read_text())
        clip_items = [e for e in protect_tl.get("elements", []) if e.get("kind") == "clip"
                      and e.get("asset")]
        dec_f = protect_dir / "decisions.md"
        dec = dec_f.read_text(encoding="utf-8") if dec_f.exists() else None
        unres, res = scan_third_party_risks(clip_items, real_index, decisions_text=dec)
        assert len(unres) >= 10, f"protect-01 应检出至少 10 段品牌字样，实际 {len(unres)}"
        print(f"  ✅ 真实项目 protect-01 对账成功：{len(unres)} 段未处置 / {len(res)} 段核销")

        # T-036 原话原样写回 decisions.md → 仍然 11 段未核销（不许被「允许」吃掉 #1）
        inject = ("\n| 2026-11-13 10:28 | T-035 | brand_safety 复核：unresolved 9→11，新增 #1 "
                  "「第三方品牌字样」；index.json 没有重写（不在本单允许改动的两个路径里），"
                  "1072 的 L2 也不动。 | ✅ 接受 |\n")
        unres2, res2 = scan_third_party_risks(clip_items, real_index, decisions_text=(dec or "") + inject)
        assert len(res2) == 0, f"散文不得核销任何一段，实际 res={[r['n'] for r in res2]}"
        assert len(unres2) == len(unres) >= 10, (
            f"把这句原话写回 decisions.md 后未核销段数必须不变（{len(unres)}），实际 {len(unres2)}")
        hints: list[str] = []
        scan_third_party_risks(clip_items, real_index, decisions_text=(dec or "") + inject, hints=hints)
        assert hints, "这句原话必须被 ack_hints 认成「像在说品牌」的散文并给提示"
        print(f"  ✅ T-036 原话写回 decisions.md：unresolved 仍 {len(unres2)}（核销 0），已给散文提示")


def main():
    print("▶ 开始运行品牌安全与第三方字样处置回归测试...")
    test_samples()
    test_t036_structured_only()
    test_timeline_integration()
    test_real_projects()
    print("✅ 全部正负样本与真实成片对账回归通过！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
