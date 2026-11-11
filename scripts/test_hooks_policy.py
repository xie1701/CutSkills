#!/usr/bin/env python3
"""CutSkills · T-006 钩子策略真判定回归测试

为什么要有它：
前 3 秒策略长期处于「只出候选、良率卡读 passed 却永远是 null / 假通过」的状态。
本测试通过真 import evaluate_hook_policy 与跑 CLI 断言真实的策略判定与门禁，
涵盖超时、平票、冲突、未验、全绿与重构持久化 7 组正负样本，全程临时目录隔离。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from hooks import evaluate_hook_policy, build  # noqa: E402


def test_samples():
    # 1. 负样本：时长超标（3.8s > 3.6s）
    h_overtime = {
        "version": 1,
        "candidates": [
            {"id": "A", "label": "超长候选", "duration_s": 3.8,
             "elements": [{"kind": "clip", "asset": "v01", "in": 0.0, "out": 3.8}]}
        ],
        "chosen": "A",
        "votes": {"raw": ["A", "A", "A"], "tally": {"A": 3}, "majority": "A"}
    }
    passed, reasons = evaluate_hook_policy(h_overtime)
    assert passed is False, f"时长超标应判 False，实际得到 {passed}"
    assert any("时长超标" in r for r in reasons), f"需明确报超时，实际理由: {reasons}"
    print("  ✅ 负样本 1（时长超标 3.8s > 3.6s）被真拦截：", reasons[0])

    # 2. 负样本：平票未达多数共识（A, B, C 各 1 票）
    h_tie = {
        "version": 1,
        "candidates": [
            {"id": "A", "label": "结论前置", "duration_s": 2.5,
             "elements": [{"kind": "clip", "asset": "v01", "in": 0.0, "out": 2.5}]},
            {"id": "B", "label": "现场切入", "duration_s": 3.0,
             "elements": [{"kind": "clip", "asset": "v02", "in": 0.0, "out": 3.0}]},
            {"id": "C", "label": "反差提问", "duration_s": 2.6,
             "elements": [{"kind": "gen", "template": "keyword", "dur": 2.6, "props": {"text": "真的好？"}}]}
        ],
        "votes": {"raw": ["A", "B", "C"], "tally": {"A": 1, "B": 1, "C": 1}, "majority": None}
    }
    passed, reasons = evaluate_hook_policy(h_tie)
    assert passed is False, f"平票应判 False，实际得到 {passed}"
    assert any("未达成多数共识" in r for r in reasons), f"需报未达共识，实际理由: {reasons}"
    print("  ✅ 负样本 2（平票无多数共识）被真拦截：", reasons[0])

    # 3. 负样本：定案与共识冲突（投票选 B，定案定 A）
    h_conflict = {
        "version": 1,
        "candidates": [
            {"id": "A", "label": "结论前置", "duration_s": 2.5,
             "elements": [{"kind": "clip", "asset": "v01", "in": 0.0, "out": 2.5}]},
            {"id": "B", "label": "现场切入", "duration_s": 3.0,
             "elements": [{"kind": "clip", "asset": "v02", "in": 0.0, "out": 3.0}]}
        ],
        "chosen": "A",
        "votes": {"raw": ["B", "B", "A"], "tally": {"B": 2, "A": 1}, "majority": "B"}
    }
    passed, reasons = evaluate_hook_policy(h_conflict)
    assert passed is False, f"冲突应判 False，实际得到 {passed}"
    assert any("冲突" in r for r in reasons), f"需报定案冲突，实际理由: {reasons}"
    print("  ✅ 负样本 3（定案与投票冲突）被真拦截：", reasons[0])

    # 4. 负样本：元素残缺（clip 缺 asset）
    h_broken = {
        "version": 1,
        "candidates": [
            {"id": "A", "label": "残缺候选", "duration_s": 2.0,
             "elements": [{"kind": "clip", "asset": None, "in": 0.0, "out": 2.0}]}
        ],
        "chosen": "A",
        "votes": {"raw": ["A", "A"], "tally": {"A": 2}, "majority": "A"}
    }
    passed, reasons = evaluate_hook_policy(h_broken)
    assert passed is False, f"残缺元素应判 False，实际得到 {passed}"
    assert any("缺少素材" in r for r in reasons), f"需报缺少素材，实际理由: {reasons}"
    print("  ✅ 负样本 4（元素结构残缺）被真拦截：", reasons[0])

    # 5. 未验状态（仅定案但无投票记录，良率卡记为未验）
    h_unverified = {
        "version": 1,
        "candidates": [
            {"id": "B", "label": "现场切入", "duration_s": 3.0,
             "elements": [{"kind": "clip", "asset": "v02", "in": 0.0, "out": 3.0}]}
        ],
        "chosen": "B"
    }
    passed, reasons = evaluate_hook_policy(h_unverified)
    assert passed is None, f"未投票应为 None (null)，实际得到 {passed}"
    assert any("尚未进行真人投票" in r for r in reasons)
    print("  ✅ 未验状态（如实返回 passed: null，不虚假放行）：", reasons[0])

    # 6. 正样本：全规则合规放行
    h_pass = {
        "version": 1,
        "candidates": [
            {"id": "A", "label": "结论前置", "duration_s": 2.5,
             "elements": [{"kind": "clip", "asset": "v01", "in": 0.0, "out": 2.5}]},
            {"id": "B", "label": "现场切入", "duration_s": 3.2,
             "elements": [{"kind": "clip", "asset": "v02", "in": 0.0, "out": 3.2}]}
        ],
        "chosen": "B",
        "votes": {"raw": ["B", "B", "A"], "tally": {"B": 2, "A": 1}, "majority": "B"}
    }
    passed, reasons = evaluate_hook_policy(h_pass)
    assert passed is True, f"合规应当判 True，实际得到 {passed}，原因: {reasons}"
    assert not reasons, f"通过时不应有失败原因: {reasons}"
    print("  ✅ 正样本（时长 ≤3.6s + 多数达成 + 定案对齐）真判通过：passed: True")


def test_cli_and_persistence():
    with tempfile.TemporaryDirectory(prefix="cutskills_hook_test_") as tmpdir:
        td = Path(tmpdir)
        proj = td / "proj"
        proj.mkdir()
        lib = td / "lib"
        lib.mkdir()

        # 写入 mock brief 与 plan
        brief = {"audience": "装修业主", "audience_present_state": "进口一定比国产好", "change": "按场景选"}
        (proj / "brief.json").write_text(json.dumps(brief, ensure_ascii=False))
        plan = {
            "segments": [
                {"role": "claim", "asset": "a01", "in": 0.5, "out": 3.5, "medium": "talking"}
            ]
        }
        (proj / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))

        # 写入 mock 索引
        idx_data = {
            "assets": [
                {
                    "id": "a01",
                    "duration": 5.0,
                    "segments": [
                        {"grade": "must-use", "action": "打磨工序", "shot": "close",
                         "in": 0.2, "out": 3.4, "people": ["worker"]}
                    ]
                }
            ]
        }
        (lib / "_cutskills").mkdir()
        (lib / "_cutskills" / "index.json").write_text(json.dumps(idx_data, ensure_ascii=False))

        # 1. 跑 hooks.py 生成初始候选
        r = subprocess.run([sys.executable, str(HERE / "hooks.py"), "--project", str(proj),
                            "--library", str(lib)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"hooks.py 生成失败: {r.stderr}"
        hp = proj / "hooks.json"
        assert hp.exists()
        h = json.loads(hp.read_text())
        assert len(h["candidates"]) >= 2
        assert h.get("passed") is None, f"初始状态 passed 应为 None，实际: {h.get('passed')}"

        # 2. 跑 --check，未验状态返回 0 并告警
        r_chk = subprocess.run([sys.executable, str(HERE / "hooks.py"), "--project", str(proj), "--check"],
                               capture_output=True, text=True)
        assert r_chk.returncode == 0
        assert "WARN" in r_chk.stdout
        print("  ✅ CLI --check 未验状态正确报警")

        # 3. 跑 --vote B,B,A，达成共识
        r_vote = subprocess.run([sys.executable, str(HERE / "hooks.py"), "--project", str(proj),
                                 "--vote", "B,B,A"],
                                capture_output=True, text=True)
        assert r_vote.returncode == 0, f"投票失败: {r_vote.stderr}"
        h_voted = json.loads(hp.read_text())
        assert h_voted.get("chosen") == "B"
        assert h_voted.get("passed") is True
        assert h_voted["votes"]["majority"] == "B"

        # 4. 跑 --check，断言 🟢 OK 退出 0
        r_chk2 = subprocess.run([sys.executable, str(HERE / "hooks.py"), "--project", str(proj), "--check"],
                                capture_output=True, text=True)
        assert r_chk2.returncode == 0
        assert "🟢 OK" in r_chk2.stdout
        print("  ✅ CLI --check 投票达成后真放行 🟢 OK")

        # 5. 重新运行 hooks.py（重新生成候选），断言状态不被冲刷
        r_rebuild = subprocess.run([sys.executable, str(HERE / "hooks.py"), "--project", str(proj),
                                    "--library", str(lib)],
                                   capture_output=True, text=True)
        assert r_rebuild.returncode == 0
        h_rebuilt = json.loads(hp.read_text())
        assert h_rebuilt.get("chosen") == "B", "重新生成后 chosen 不应丢失"
        assert h_rebuilt.get("passed") is True, "重新生成后 passed 不应被抹成 null"
        assert h_rebuilt.get("votes", {}).get("majority") == "B", "重新生成后 votes 不应丢失"
        print("  ✅ 候选重新生成时历史定案与投票共识完整保留（防冲刷验证通过）")


def main():
    print("▶ 开始运行前 3 秒策略真判定回归测试...")
    test_samples()
    test_cli_and_persistence()
    print("✅ 全部 7 组策略正负样本与持久化回归通过！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
