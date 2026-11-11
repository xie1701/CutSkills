#!/usr/bin/env python3
"""CutSkills · T-007 品牌安全与第三方字样处置回归测试

为什么要有它：
商业片里出现未授权的第三方品牌字样、商标或手机号，会带来下架限流与侵权风险。
成片中曾出现 FESTOOL RO 150 打磨机、PPG 大师漆 T 恤、以及 protect-01 多达 11 段地保品牌。
本测试通过真实断言：
  1. FESTOOL / PPG 等第三方品牌在未处置时必须被精准拦截（unresolved WARN）；
  2. 元素标注处置（third_party_action）或 decisions.md 人工确认留痕后成功核销（resolved）；
  3. 干净片段合规放行；
  4. 真实项目对账：qizao-ground-01 与 qizao-protect-01 命中真实品牌片段。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from brand_safety import scan_third_party_risks, BRAND_PATTERN  # noqa: E402
from check_timeline import check as check_timeline_fn  # noqa: E402


def test_samples():
    # 模拟索引
    mock_index = {
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
        "asset_clean": {
            "id": "asset_clean",
            "tag": "纯净白墙",
            "segments": [
                {"in": 0.0, "out": 5.0, "flags": ["特写白墙平整度"]}
            ]
        }
    }

    # 1. 负样本 1：FESTOOL RO 150 未处置拦截
    items_festool = [{"n": 1, "asset": "asset_festool", "in": 0.0, "out": 4.0}]
    unres, res = scan_third_party_risks(items_festool, mock_index)
    assert len(unres) == 1, f"FESTOOL 应被拦截，实际 unresolved={len(unres)}"
    assert any("FESTOOL" in f for f in unres[0]["flags"]), f"需明确包含 FESTOOL: {unres[0]['flags']}"
    assert len(res) == 0
    print("  ✅ 负样本 1（FESTOOL RO 150 未处置）被精准拦截：", unres[0]["flags"])

    # 2. 负样本 2：PPG 大师漆 未处置拦截
    items_ppg = [{"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0}]
    unres_ppg, res_ppg = scan_third_party_risks(items_ppg, mock_index)
    assert len(unres_ppg) == 1, f"PPG 应被拦截，实际 unresolved={len(unres_ppg)}"
    assert any("PPG" in f for f in unres_ppg[0]["flags"]), f"需明确包含 PPG: {unres_ppg[0]['flags']}"
    print("  ✅ 负样本 2（PPG大师漆 未处置）被精准拦截：", unres_ppg[0]["flags"])

    # 3. 核心正样本（【脉动】指示：工具无需避开/保留出镜）
    decisions_keep = """# decisions
- 第三方字样：#1 FESTOOL 打磨机作为真实施工工具证据无需避开，客户认可保留。
"""
    unres_ack, res_ack = scan_third_party_risks(items_festool, mock_index, decisions_text=decisions_keep)
    assert len(unres_ack) == 0, f"经用户确认「无需避开」后应核销，实际仍有 {len(unres_ack)} 项未解决"
    assert len(res_ack) == 1, f"核销后应进入 resolved，实际 resolved={len(res_ack)}"
    assert "无需避开/保留" in res_ack[0]["resolved_reason"]
    print("  ✅ 核心正样本（用户确认「无需避开/真实工具保留」）：成功核销放行：", res_ack[0]["resolved_reason"])

    # 4. 关键负样本（Cola 打回项①：否定/整改指示严禁被误核销）
    decisions_reject = """# decisions
- 第三方字样：#1 FESTOOL 打磨机不能保留，必须打码或换段。
"""
    unres_rej, res_rej = scan_third_party_risks(items_festool, mock_index, decisions_text=decisions_reject)
    assert len(unres_rej) == 1, f"明确写了「不能保留，必须打码或换段」绝不能被当成核销，实际 unres={len(unres_rej)}"
    assert len(res_rej) == 0, f"否定整改指令不得进入 resolved，实际 resolved={len(res_rej)}"
    print("  ✅ 关键负样本（否定整改指令「不能保留，必须换段」）：精准拒绝误核销，保持报警！")

    # 5. 核销样本 2：在元素中显式标注 third_party_action: "keep"（无需避开保留）或 "crop"
    items_keep = [{"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0, "third_party_action": "keep"}]
    unres_keep, res_keep = scan_third_party_risks(items_keep, mock_index)
    assert len(unres_keep) == 0 and len(res_keep) == 1
    assert "元素显式标注处置：keep" in res_keep[0]["resolved_reason"]
    print("  ✅ 核销样本 2（元素标注 third_party_action: keep 无需避开）：成功核销：", res_keep[0]["resolved_reason"])

    items_crop = [{"n": 2, "asset": "asset_ppg", "in": 0.0, "out": 4.0, "third_party_action": "crop"}]
    unres_crop, res_crop = scan_third_party_risks(items_crop, mock_index)
    assert len(unres_crop) == 0, f"元素标注 crop 后应核销，实际 unresolved={len(unres_crop)}"
    assert len(res_crop) == 1
    assert "元素显式标注处置：crop" in res_crop[0]["resolved_reason"]
    print("  ✅ 核销样本 3（元素标注 third_party_action: crop）：成功核销降级：", res_crop[0]["resolved_reason"])

    # 6. 正样本：合规纯净片段放行
    items_clean = [{"n": 3, "asset": "asset_clean", "in": 0.0, "out": 4.0}]
    unres_cln, res_cln = scan_third_party_risks(items_clean, mock_index)
    assert len(unres_cln) == 0 and len(res_cln) == 0
    print("  ✅ 正样本（合规纯净素材）：0 拦截 0 报警")


def test_timeline_integration():
    mock_index = {
        "asset_festool": {
            "id": "asset_festool",
            "tag": "地面打磨",
            "segments": [{"in": 0.0, "out": 5.0, "flags": ["机身带 FESTOOL RO 150 FEQ 标志"]}]
        }
    }
    # 构造 mock timeline
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
    print("  ✅ check_timeline 集成验证：未处置时成功报出 WARN（含无需避开引导）：", brand_warns[0])

    # 传入 decisions.md 确认无需避开后，warning 自动消失
    dec_text = "- 第三方字样：#1 FESTOOL 打磨机无需避开，系施工实拍真实工具，客户认可保留"
    probs_ack = check_timeline_fn(tl, mock_index, decisions_text=dec_text)
    brand_warns_ack = [m for lv, m in probs_ack if lv == "warning" and "第三方品牌字样" in m]
    assert len(brand_warns_ack) == 0, f"check_timeline 在确认后应无第三方品牌字样 warning，实际: {brand_warns_ack}"
    print("  ✅ check_timeline 集成验证：decisions.md 确认「无需避开」后 WARN 自动核销放行")


def test_real_projects():
    # 对账真项目（若存在素材库）
    lib_index_f = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/index.json")
    if not lib_index_f.exists():
        print("  ℹ 素材库 index 不存在，跳过真实素材对账")
        return

    real_index = {x["id"]: x for x in json.loads(lib_index_f.read_text())["assets"]}

    # 对账 qizao-ground-01
    ground_tl_f = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-ground-01/timeline.json")
    if ground_tl_f.exists():
        ground_tl = json.loads(ground_tl_f.read_text())
        clip_items = [e for e in ground_tl.get("elements", []) if e.get("kind") == "clip"]
        unres, _ = scan_third_party_risks(clip_items, real_index)
        assert len(unres) >= 2, f"ground-01 至少应检出 2 段品牌字样，实际 {len(unres)}"
        flags_flat = [f for r in unres for f in r["flags"]]
        assert any("品牌" in f or "工具" in f for f in flags_flat)
        print(f"  ✅ 真实项目 ground-01 对账成功：检出 {len(unres)} 段第三方品牌字样")

    # 对账 qizao-protect-01
    protect_tl_f = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-protect-01/timeline.json")
    if protect_tl_f.exists():
        protect_tl = json.loads(protect_tl_f.read_text())
        clip_items = [e for e in protect_tl.get("elements", []) if e.get("kind") == "clip"]
        unres, _ = scan_third_party_risks(clip_items, real_index)
        assert len(unres) >= 10, f"protect-01 应检出至少 10 段品牌字样，实际 {len(unres)}"
        print(f"  ✅ 真实项目 protect-01 对账成功：检出 {len(unres)} 段第三方品牌字样")


def main():
    print("▶ 开始运行品牌安全与第三方字样处置回归测试...")
    test_samples()
    test_timeline_integration()
    test_real_projects()
    print("✅ 全部正负样本与真实成片对账回归通过！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
