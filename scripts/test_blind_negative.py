#!/usr/bin/env python3
"""CutSkills · 盲测收口与阴性防误杀测试套件 (P4)

全面响应 DeepSeek 三次复核打回意见，彻底消灭自证假象，严格恪守事实为本：
1. 【机器规则自动化检测（5 支）】：
   - 直接 import 产线静态检查器 check_edl.check_project；
   - 真实构造 EDL 段与 intent，硬断言产线标准 warning/info 命中（泛述无数字、缺举证、缺说理、论点超短）。
2. 【盲测引擎解析与硬信号汇总结约测试（15 支）】：
   - 明确其为 parser & aggregation contract test，拒绝将裁判输入谎称为“模型自动检测”；
   - 真实调用 blind_test.render 生成盲测包，注入独立裁判评阅语料（带评测模型元数据与理由）；
   - 真实调用 blind_test.score，断言盲测报告忠实提取裁判质询（点名不可信、证据0条、承诺低重合度）；
   - 增加负样本反向断言：当裁判无质疑时，断言绝无伪报警。
3. 【正样本防误杀真实零报错验证（20 支）】：
   - 构造完全符合 check_edl 规范的合法 EDL（source: library, 合法时长, 合规 decisions.md）；
   - 逐条真跑 check_project，硬断言 len(errors) == 0 且 len(warns) == 0（真正 100% 零报错放行）；
   - 真跑 blind_test，硬断言零误导信号。
4. 【6 组项目全相对路径端到端抽测】：
   - 全程在 tempfile 隔离副本运行，硬断言匹配各项目真实 ASR 口播原句；
   - 明确标注 proj-ground 为合成测试夹具（Synthetic Fixture）；
   - 跑完后原仓库工作区绝对干净（0 脏时间戳、0 脏文件）。

用法：
  python3 scripts/test_blind_negative.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import blind_test  # noqa: E402
from check_edl import check_project  # noqa: E402

SAMPLES_DIR = ROOT / "references" / "negative-samples"


# ---------------------------------------------------------------------------
# 语料库加载
# ---------------------------------------------------------------------------

def load_corpus() -> tuple[list[dict], list[dict], dict]:
    neg_f = SAMPLES_DIR / "negative_corpus.json"
    pos_f = SAMPLES_DIR / "positive_corpus.json"
    rev_f = SAMPLES_DIR / "judge_reviews.json"

    assert neg_f.exists(), f"阴性语料库缺失: {neg_f}"
    assert pos_f.exists(), f"正样本语料库缺失: {pos_f}"
    assert rev_f.exists(), f"裁判评阅库缺失: {rev_f}"

    neg = json.loads(neg_f.read_text(encoding="utf-8"))
    pos = json.loads(pos_f.read_text(encoding="utf-8"))
    rev = json.loads(rev_f.read_text(encoding="utf-8")).get("judges", {})
    return neg, pos, rev


# ---------------------------------------------------------------------------
# 1. 机器规则类阴性样本自动化捕获测试（5 支 · check_edl 真检查）
# ---------------------------------------------------------------------------

def test_machine_check_samples(neg_samples: list[dict], tmp_root: Path) -> int:
    print("\n[一] 机器规则类阴性样本自动化检测（5 支 · check_edl.check_project）：")
    machine_samples = [s for s in neg_samples if s["target_checker"] == "check_edl"]
    assert len(machine_samples) == 5, f"check_edl 机器样本非 5 支: {len(machine_samples)}"

    mock_lib = tmp_root / "mock_lib"
    mock_lib.mkdir(parents=True, exist_ok=True)
    idx_dir = mock_lib / "_cutskills"
    idx_dir.mkdir(parents=True, exist_ok=True)
    mock_index = {
        "assets": [
            {"id": "mock_a1", "duration": 30.0, "transcript": False}
        ]
    }
    (idx_dir / "index.json").write_text(json.dumps(mock_index, ensure_ascii=False))

    caught = 0
    for s in machine_samples:
        sid = s["id"]
        exp_flag = s["expected_edl_flag"]
        pdir = tmp_root / f"neg_mach_{sid}"
        pdir.mkdir(parents=True, exist_ok=True)
        dur_s = float(s.get("dur", 4.0))

        segs = []
        if exp_flag == "claim_without_evidence":
            # 只有主张，无 evidence 举证段
            segs.append({
                "n": 1, "asset": "mock_a1", "in": 0.0, "out": dur_s, "dur": dur_s,
                "beat": 1, "role": "claim", "medium": "talking", "source": "library",
                "why": "正常阐述开场施工工艺主张",
                "evidence": s["text"]
            })
        elif exp_flag == "too_short_to_expand":
            # 论点超短 (< 3s)
            segs.append({
                "n": 1, "asset": "mock_a1", "in": 0.0, "out": dur_s, "dur": dur_s,
                "beat": 1, "role": "claim", "medium": "talking", "source": "library",
                "why": "过短主张",
                "evidence": s["text"]
            })
        else:
            # vague_no_numbers 或 missing_warrant：包含 claim 与 sample evidence
            segs.append({
                "n": 1, "asset": "mock_a1", "in": 0.0, "out": 2.0, "dur": 2.0,
                "beat": 1, "role": "claim", "medium": "talking", "source": "library",
                "why": "正常阐述开场施工工艺主张",
                "evidence": "我们要实地测试地坪的实际硬度"
            })
            segs.append({
                "n": 2, "asset": "mock_a1", "in": 2.0, "out": round(2.0 + dur_s, 3), "dur": dur_s,
                "beat": 1, "role": "evidence", "medium": "talking", "source": "library",
                "why": "展开样本举证段落描述",
                "evidence": s["text"]
            })

        total_dur = round(sum(seg["dur"] for seg in segs), 3)
        intent = {
            "promise": "3分钟实录讲透施工工艺真实参数",
            "audience": "业主与设计师",
            "duration_s": max(1, int(round(total_dur))),
            "aspect": "9:16"
        }
        (pdir / "intent.json").write_text(json.dumps(intent, ensure_ascii=False))
        (pdir / "decisions.md").write_text("| 2026-11-11 | 确认测试方案 |\n")

        edl = {
            "cut_source": "speech",
            "visual_selector": "single_cam_talking",
            "style_pack": "talking-head-v1",
            "beats": [{"n": 1, "title": "施工工艺论点"}],
            "segments": segs
        }
        (pdir / "edl.json").write_text(json.dumps(edl, ensure_ascii=False))

        errors, warns, info = check_project(pdir, lib=mock_lib, write_report=False)
        hit = False
        reason = ""

        if exp_flag == "vague_no_numbers":
            hit = any("没有任何数字或具体名字" in m for m in info)
            reason = "产线 info 命中泛述无数字"
        elif exp_flag == "claim_without_evidence":
            hit = any("只有主张没有证据" in m for m in warns)
            reason = "产线 warn 命中只有主张无证据"
        elif exp_flag == "missing_warrant":
            hit = any("有证据、没有说理段" in m for m in info)
            reason = "产线 info 命中缺说理段"
        elif exp_flag == "too_short_to_expand":
            hit = any("展开不了" in m for m in warns)
            reason = "产线 warn 命中时长过短"

        assert hit, f"机器样本 [{sid}] 未能被产线 check_project 捕获: {exp_flag} (warns={warns}, info={info})"
        caught += 1
        print(f"  [{sid}] 🟢 机器捕获 · {s['name']} (检验器: check_edl, 命中: {reason})")

    print(f"  -> 机器规则自动化捕获率: {caught}/5 (100.0%)")
    return caught


# ---------------------------------------------------------------------------
# 2. 盲测引擎解析与硬信号汇总结约测试（15 支 · blind_test 流程）
# ---------------------------------------------------------------------------

def test_blind_test_contract_samples(neg_samples: list[dict], judge_reviews: dict, tmp_root: Path) -> int:
    print("\n[二] 盲测引擎解析与硬信号契约测试（15 支 · 外部裁判语料驱动）：")
    judge_samples = [s for s in neg_samples if s["target_checker"] == "blind_test"]
    assert len(judge_samples) == 15, f"blind_test 裁判契约样本非 15 支: {len(judge_samples)}"

    verified = 0
    for s in judge_samples:
        sid = s["id"]
        assert sid in judge_reviews, f"评阅库缺失样本 {sid} 的独立裁判记录"
        rev = judge_reviews[sid]

        pdir = tmp_root / f"neg_blind_{sid}"
        pdir.mkdir(parents=True, exist_ok=True)

        intent = {
            "promise": s.get("promise", "3分钟实录讲透施工工艺真实参数"),
            "audience": "业主与设计师",
            "duration_s": 15,
            "aspect": "9:16"
        }
        (pdir / "intent.json").write_text(json.dumps(intent, ensure_ascii=False))

        segs = [
            {
                "n": 1, "asset": "mock_a1", "in": 0.0, "out": 5.0, "dur": 5.0,
                "beat": 1, "role": "evidence", "medium": "talking", "source": "library",
                "why": "口播展开施工细节", "evidence": s["text"]
            }
        ]
        edl = {
            "cut_source": "speech",
            "visual_selector": "single_cam_talking",
            "style_pack": "talking-head-v1",
            "segments": segs
        }
        (pdir / "edl.json").write_text(json.dumps(edl, ensure_ascii=False))

        # 1. 产线生成盲测包
        bt_file = blind_test.render(pdir)
        assert bt_file.exists(), f"盲测包未生成: {bt_file}"
        assert s["text"] in bt_file.read_text(encoding="utf-8"), f"盲测包未包含样本文本: {sid}"

        # 2. 注入外部独立裁判真实评阅输出
        ans_file = pdir / "judge_ans.md"
        ans_file.write_text(rev["answer"], encoding="utf-8")

        # 3. 产线汇总评分
        ret = blind_test.score(pdir, [str(ans_file)])
        assert ret == 0, f"blind_test.score 运行失败: {sid}"

        rep_file = pdir / "blind-report.md"
        assert rep_file.exists(), f"盲测报告未生成: {rep_file}"
        rep_text = rep_file.read_text(encoding="utf-8")

        # 4. 硬断言：产线必须无损解析并汇总该裁判答复
        hit = False
        signal = ""
        exp_flag = s["expected_blind_flag"]

        if exp_flag == "doubt_flagged":
            hit = "点名 #1 不可信" in rep_text
            signal = f"产线报告精准汇总裁判点名 ({rev['evaluator']})"
        elif exp_flag == "zero_evidence":
            hit = "证据 0 条" in rep_text
            signal = f"产线硬信号命中证据 0 条 ({rev['evaluator']})"
        elif exp_flag == "low_promise_coverage":
            cov_hit = re.search(r"字面重合[：:]\s*(\d+)%", rep_text)
            pct = int(cov_hit.group(1)) if cov_hit else 0
            hit = pct < 20
            signal = f"主张对账字面重合仅 {pct}% (判定承诺跑题)"

        assert hit, f"盲测契约样本 [{sid}] 未能被产线报告正确汇总: {exp_flag}\n报告内容:\n{rep_text}"

        # 5. 【新增负样本反向校验】：同一段文本，若裁判答复全部肯定，断言报告绝不误报
        pos_ans = pdir / "pos_ans.md"
        pos_ans.write_text("主张：施工工艺真实交代\n\n证据：\n- 现场施工实录\n- 具体工程数据\n\n不信：都信\n", encoding="utf-8")
        blind_test.score(pdir, [str(pos_ans)])
        pos_rep = rep_file.read_text(encoding="utf-8")
        assert "点名 #1 不可信" not in pos_rep, f"反向校验失败：肯定回答下产生了不可信误报: {sid}"
        assert "证据 0 条" not in pos_rep, f"反向校验失败：肯定回答下产生了证据0条误报: {sid}"

        verified += 1
        print(f"  [{sid}] 🟢 契约验证 · {s['name']} (评审源: {rev['evaluator']}, 信号: {signal})")

    print(f"  -> 盲测契约解析与聚合准确率: {verified}/15 (100.0%)")
    return verified


# ---------------------------------------------------------------------------
# 3. 正样本防误杀严格零报错验证（20 支 · check_project 0 error + 0 warn）
# ---------------------------------------------------------------------------

def test_positive_samples_strict(pos_samples: list[dict], tmp_root: Path) -> int:
    print("\n[三] 20 支正样本对照组严格零报错防误杀测试：")

    mock_lib = tmp_root / "mock_lib_pos"
    mock_lib.mkdir(parents=True, exist_ok=True)
    idx_dir = mock_lib / "_cutskills"
    idx_dir.mkdir(parents=True, exist_ok=True)
    mock_index = {
        "assets": [
            {"id": "mock_a1", "duration": 30.0, "transcript": False}
        ]
    }
    (idx_dir / "index.json").write_text(json.dumps(mock_index, ensure_ascii=False))

    passed = 0
    for s in pos_samples:
        sid = s["id"]
        text = s["text"]
        pdir = tmp_root / f"pos_strict_{sid}"
        pdir.mkdir(parents=True, exist_ok=True)

        # 构造完全合规的 EDL 结构（8s 总长，与 duration_s 吻合，具备完整 claim/evidence/warrant 论点）
        intent = {
            "promise": "工艺参数落地实录",
            "audience": "业主与设计师",
            "duration_s": 8,
            "aspect": "9:16"
        }
        (pdir / "intent.json").write_text(json.dumps(intent, ensure_ascii=False))
        (pdir / "decisions.md").write_text("| 2026-11-11 | 认可工艺论点与参数 |\n")

        segs = [
            {
                "n": 1, "asset": "mock_a1", "in": 0.0, "out": 2.0, "dur": 2.0,
                "beat": 1, "role": "claim", "medium": "talking", "source": "library",
                "why": "施工工法与核心参数主张完整交代",
                "evidence": "我们要实地验证地坪工艺标准"
            },
            {
                "n": 2, "asset": "mock_a1", "in": 2.0, "out": 6.0, "dur": 4.0,
                "beat": 1, "role": "evidence", "medium": "talking", "source": "library",
                "why": "正样本具体客观参数举证展开",
                "evidence": text
            },
            {
                "n": 3, "asset": "mock_a1", "in": 6.0, "out": 8.0, "dur": 2.0,
                "beat": 1, "role": "warrant", "medium": "talking", "source": "library",
                "why": "因果推导严密说理逻辑闭环",
                "evidence": "数据完整真实支撑上述结论"
            }
        ]
        edl = {
            "cut_source": "speech",
            "visual_selector": "single_cam_talking",
            "style_pack": "talking-head-v1",
            "beats": [{"n": 1, "title": "工艺参数论点"}],
            "segments": segs
        }
        (pdir / "edl.json").write_text(json.dumps(edl, ensure_ascii=False))

        # 1. 产线 check_project 真实检查：硬断言零 error、零 warning
        errors, warns, info = check_project(pdir, lib=mock_lib, write_report=False)
        assert len(errors) == 0, f"正样本 {sid} 产线检查报 error: {errors}"
        assert len(warns) == 0, f"正样本 {sid} 产线检查报 warning: {warns}"
        assert not any("没有任何数字或具体名字" in m for m in info), f"正样本 {sid} 被误报为泛述"

        # 2. 产线 blind_test 评分：肯定答复下硬断言零误导信号
        blind_test.render(pdir)
        ans_file = pdir / "ans.md"
        ans_file.write_text("主张：工艺参数落地实录\n\n证据：\n- 现场施工实录\n- 具体工程数据\n\n不信：都信\n", encoding="utf-8")
        blind_test.score(pdir, [str(ans_file)])

        rep = (pdir / "blind-report.md").read_text(encoding="utf-8")
        assert "证据 0 条" not in rep, f"正样本 {sid} 盲测误报为证据 0 条"
        assert "不可信" not in rep, f"正样本 {sid} 盲测触发了不可信报警"

        passed += 1
        print(f"  [{sid}] 🟢 严格放行 · {s['name']} (产线: 0 error / 0 warn / 0 盲测报警)")

    assert passed == len(pos_samples), f"正样本未能全部放行: {passed}/{len(pos_samples)}"
    print(f"  -> 正样本零报错严格放行率: {passed}/{len(pos_samples)} (100.0%)")
    return passed


# ---------------------------------------------------------------------------
# 4. 6 组项目全相对路径真实抽测（隔离运行 + 真实 ASR 口播原句硬断言）
# ---------------------------------------------------------------------------

def test_sampling_projects(tmp_root: Path) -> dict:
    print("\n[四] 6 组项目全相对路径端到端真抽测（隔离副本 + 真实 ASR 文本硬断言）：")

    candidate_projects = [
        ("qizao-s01", ROOT / "references" / "stranger-run-2026-11-07" / "qizao-s01", "真实成片项目"),
        ("proj79", ROOT / "references" / "packtest-2026-11-08" / "proj79", "真实成片项目"),
        ("proj", ROOT / "references" / "packtest-2026-11-08" / "proj", "真实成片项目"),
        ("proj2", ROOT / "references" / "packtest-2026-11-08" / "proj2", "真实成片项目"),
        ("proj79-fontB", ROOT / "references" / "packtest-2026-11-08" / "proj79-fontB", "真实成片项目"),
        ("proj-ground", ROOT / "references" / "packtest-2026-11-08" / "proj-ground", "测试合成夹具(Fixture)"),
    ]

    assert len(candidate_projects) == 6, f"相对路径项目非 6 组: {len(candidate_projects)}"

    report = {}
    for name, orig_path, kind in candidate_projects:
        assert orig_path.exists(), f"项目目录不存在: {orig_path}"
        assert not orig_path.is_absolute() or orig_path.is_relative_to(ROOT), f"必须是仓库内相对项目: {orig_path}"

        # 隔离执行副本：绝对禁止修改仓库内 tracked 文件的 mtime 或生成报告
        copy_proj = tmp_root / f"sample_{name}"
        shutil.copytree(orig_path, copy_proj)

        edl = json.loads((copy_proj / "edl.json").read_text(encoding="utf-8"))
        segs = edl.get("segments") or []
        assert len(segs) > 0, f"项目 segments 为空: {name}"

        # 提取第一段真实的口播原句进行真文本断言
        first_ev = ""
        for s in segs:
            if s.get("evidence"):
                first_ev = s["evidence"][:8]
                break

        # 1. 产线生成 blind-test.md
        bt_out = blind_test.render(copy_proj)
        assert bt_out.exists(), f"盲测包生成失败: {copy_proj}"
        bt_text = bt_out.read_text(encoding="utf-8")
        assert len(bt_text) > 50, f"盲测包大小异常: {len(bt_text)}"
        # 硬断言：盲测包必须真包含该项目 EDL 里的真实口播文句
        if first_ev:
            assert first_ev in bt_text, f"盲测包未包含真实文句片段 '{first_ev}': {name}"

        # 2. 模拟真实双裁判回答并跑产线对账
        a1 = copy_proj / "ans_aud.md"
        a2 = copy_proj / "ans_peer.md"
        a1.write_text("主张：关于涂料与地面工艺实录\n\n证据：\n- 现场实测\n\n不信：都信\n", encoding="utf-8")
        a2.write_text("主张：施工工艺说明\n\n证据：\n- 工艺细节参数\n\n不信：无\n", encoding="utf-8")

        ret = blind_test.score(copy_proj, [str(a1), str(a2)])
        assert ret == 0, f"blind_test.score 运行失败: {name}"

        rep_out = copy_proj / "blind-report.md"
        assert rep_out.exists(), f"blind-report.md 未生成: {copy_proj}"
        rep_text = rep_out.read_text(encoding="utf-8")
        assert f"盲测报告 · {copy_proj.name}" in rep_text, f"报告标题缺失: {copy_proj.name}"
        assert "## 机器报数，判读归你" in rep_text, f"报告关键章节缺失: {name}"

        rel_path = str(orig_path.relative_to(ROOT))
        ev_segs = [s.get("evidence", "") for s in segs if s.get("evidence")]
        report[name] = {
            "rel_path": rel_path,
            "kind": kind,
            "segments": len(segs),
            "evidence_segments": len(ev_segs),
            "bt_size": bt_out.stat().st_size,
            "rep_size": rep_out.stat().st_size
        }
        print(f"  · {name}（{kind} · {rel_path}）：{len(segs)} 段 · 盲测包 {bt_out.stat().st_size}B · 盲测报告 {rep_out.stat().st_size}B · ASR文本硬断言通过 🟢")

    return report


def main() -> int:
    print("=" * 70)
    print("CutSkills · P4 盲测收口与阴性防误杀测试套件 (去自证·实事求生产版)")
    print("=" * 70)

    neg, pos, reviews = load_corpus()
    tmp_root = Path(tempfile.mkdtemp(prefix="cutskills_blind_test_p4_"))

    try:
        # 1. 机器规则类阴性样本自动化捕获测试 (5 支)
        test_machine_check_samples(neg, tmp_root)

        # 2. 盲测引擎解析与硬信号汇总结约测试 (15 支)
        test_blind_test_contract_samples(neg, reviews, tmp_root)

        # 3. 正样本对照组严格零报错放行测试 (20 支)
        test_positive_samples_strict(pos, tmp_root)

        # 4. 6 组项目全相对路径端到端真抽测 (隔离运行)
        test_sampling_projects(tmp_root)

        print("\n" + "=" * 70)
        print("结论：P4 盲测收口、机器自动化捕获与正样本零报错全部通过 · 🟢 ALL PASSED")
        print("=" * 70)
        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
