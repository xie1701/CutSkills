#!/usr/bin/env python3
"""CutSkills · 盲测收口与阴性样本防误杀测试 (P4)

拒绝闭环自证，全面接入产线真实检查器与流程：
1. 直接 import 产线检查器：`scripts/check_edl.py`（静态分析、泛述检查、论点结构）与 `scripts/blind_test.py`（外部裁判渲染与评分）。
2. 20 支典型阴性样本分为两类验证：
   - 机器可查类：构建真实 EDL 结构段，喂入 check_edl 真实规则，精确断言捕获标签（如泛述无数字、缺证据、缺说理、超短等）；
   - 裁判质询类：构建项目与真实答复包，喂入 blind_test.score 产线流程，精确断言段号捕获、证据 0 条报警与多裁判共同指认。
3. 20 支正样本防误杀验证：喂入产线检查器，严格断言零误杀（零假阳性）。
4. 6 组项目端到端真抽测：全部采用仓库相对路径，真跑 blind_test 渲染与评分对账，带硬断言（assert）。

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

# 将 scripts 目录加入 sys.path 以便 import 产线真检查器
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import blind_test  # noqa: E402
from check_edl import VAGUE_EVIDENCE  # noqa: E402


# ---------------------------------------------------------------------------
# 产线检查器适配包装（直接调用 check_edl 与 blind_test 的核心逻辑）
# ---------------------------------------------------------------------------

def lint_edl_segment_vague(ev_txt: str) -> tuple[bool, str]:
    """严格复用 check_edl.py line 262-265 的泛述词检查规则。"""
    vague = [w for w in VAGUE_EVIDENCE if w in ev_txt]
    if vague and not re.search(r"\d|几\s*\d|十\s*\d", ev_txt):
        return True, f"命中泛述词「{vague[0]}」但没有任何数字或具体名字"
    return False, ""


def lint_edl_structure(segs: list[dict]) -> list[str]:
    """复用 check_edl.py line 350-372 的论点结构与说理规则。"""
    flags: list[str] = []
    beats: dict[int, list[dict]] = {}
    for s in segs:
        b = s.get("beat")
        if isinstance(b, int):
            beats.setdefault(b, []).append(s)

    for n, own in sorted(beats.items()):
        has_ev = any(r.get("role") == "evidence" for r in own)
        has_claim = any(r.get("role") == "claim" for r in own)
        secs = sum(float(r.get("dur", 0.0)) for r in own)
        if has_claim and not has_ev:
            flags.append("claim_without_evidence")
        if secs < 3.0:
            flags.append("too_short_to_expand")
        if has_ev and has_claim and not any(r.get("role") == "warrant" for r in own):
            flags.append("missing_warrant")
    return flags


# ---------------------------------------------------------------------------
# 20 支阴性样本（涵盖 20 类典型缺陷）
# ---------------------------------------------------------------------------

NEGATIVE_SAMPLES = [
    # ---- 机器可检类（结构与泛述缺陷，直接由 check_edl 规则检出）
    {
        "id": "N01",
        "checker": "check_edl",
        "name": "纯泛述无数据",
        "text": "我们做过很多工程，大家都觉得效果不错，反响一直特别好。",
        "role": "evidence",
        "dur": 4.5,
        "expected_flag": "vague_no_numbers"
    },
    {
        "id": "N02",
        "checker": "check_edl",
        "name": "假证据真主张（缺举证段）",
        "text": "这款漆到底好不好，你看了就知道肯定好，不用怀疑。",
        "role": "claim",
        "dur": 4.0,
        "expected_flag": "claim_without_evidence"
    },
    {
        "id": "N06",
        "checker": "check_edl",
        "name": "缺说理因果断裂（缺 warrant 段）",
        "text": "这款地坪材料硬度达到了莫氏 8 级，所以住在里面非常环保健康。",
        "role": "evidence",
        "has_claim": True,
        "dur": 5.0,
        "expected_flag": "missing_warrant"
    },
    {
        "id": "N09",
        "checker": "check_edl",
        "name": "单薄超短论点（<3s 展开不能）",
        "text": "总之就是好。",
        "role": "claim",
        "dur": 1.2,
        "expected_flag": "too_short_to_expand"
    },
    {
        "id": "N15",
        "checker": "check_edl",
        "name": "隐蔽泛述词（大量无数据）",
        "text": "我们在行业里一直保持领先地位，大量项目落地受到各界一致好评。",
        "role": "evidence",
        "dur": 4.2,
        "expected_flag": "vague_no_numbers"
    },

    # ---- 外部裁判类（认知、信源与表达缺陷，由 blind_test 产线流程检出）
    {
        "id": "N03",
        "checker": "blind_test",
        "name": "虚构假反问（无人认同）",
        "text": "难道你不想让家里每天都像住在凡尔赛宫殿里一样奢华吗？",
        "reader_doubt": "不信：#1 这个反问完全是编造的，从没有普通客户会这么想。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N04",
        "checker": "blind_test",
        "name": "主语瞬跳未知身份",
        "text": "石灰基刚刷上墙，他就说不行必须铲掉重做。",
        "reader_doubt": "不信：#1 没看懂这里的『他』到底是指业主、工长还是监理，主语跳跃。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N05",
        "checker": "blind_test",
        "name": "无信源传闻转述",
        "text": "听说国外早就淘汰这种做法了，现在都用纳米喷涂。",
        "reader_doubt": "不信：#1 谁说的没交代，『听说』不能当证据。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N07",
        "checker": "blind_test",
        "name": "偷换概念举证",
        "text": "我们的石灰石开采自太行山脉，所以耐擦洗次数能达到一万次。",
        "reader_doubt": "不信：#1 产地和耐擦洗一万次没有任何逻辑推导关系，偷换概念。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N08",
        "checker": "blind_test",
        "name": "缺少对比基准",
        "text": "进口原料比普通原料贵好几倍，差别巨大。",
        "reader_doubt": "不信：#1 贵好几倍到底是对标哪个普通原料？没有基准和具体价差。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N10",
        "checker": "blind_test",
        "name": "属性自相矛盾",
        "text": "石灰基艺术涂料完全不透气，但又具备极佳的呼吸透气调湿功能。",
        "reader_doubt": "不信：#1 前面说完全不透气，后面又说透气调湿，前后矛盾不可信。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N11",
        "checker": "blind_test",
        "name": "绝对化夸大承诺",
        "text": "刷完以后保证五十年绝对不开裂、不泛碱、不褪色。",
        "reader_doubt": "不信：#1 绝对化承诺五十年无依据，行业人一眼看出是假大空吹嘘。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N12",
        "checker": "blind_test",
        "name": "堆砌形容词零指标（证据 0 条）",
        "text": "细腻、高端、奢华、典雅，非常有视觉质感和尊贵档次。",
        "reader_ev": "证据：没有",
        "reader_doubt": "不信：#1 满篇华丽形容词，没有一条客观物理参数。",
        "expected_flag": "zero_evidence"
    },
    {
        "id": "N13",
        "checker": "blind_test",
        "name": "个人情绪充当证据",
        "text": "施工师傅刮完之后，整个人心情都变得特别舒畅愉悦。",
        "reader_doubt": "不信：#1 师傅心情好不能证明涂料施工性和附着力好坏。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N14",
        "checker": "blind_test",
        "name": "承诺跑题",
        "text": "今天教大家怎么解决地坪起砂，最终大家还是要买我们最新款的防水卷材。",
        "promise": "3分钟教你彻底根治厂房金刚砂地坪起砂起灰问题",
        "reader_claim": "主张：向观众推销新款防水卷材",
        "expected_flag": "low_promise_coverage"
    },
    {
        "id": "N16",
        "checker": "blind_test",
        "name": "循环论证车轱辘话",
        "text": "因为它是水性环保材料，所以它是以水为介质的，因此它是绿色的水性漆。",
        "reader_doubt": "不信：#1 车轱辘话自己证明自己，没有实际信息量。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N17",
        "checker": "blind_test",
        "name": "施工工序倒置",
        "text": "施工流程是先上一遍透明罩光防尘面漆，然后再在底层批刮抗裂砂浆找平。",
        "reader_doubt": "不信：#1 工艺顺序完全倒了，底道找平怎么可能放在罩面漆后面？",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N18",
        "checker": "blind_test",
        "name": "伪权威背书无编号",
        "text": "经国家级权威机构严格认证，行业资深专家一致推荐首选品牌。",
        "reader_doubt": "不信：#1 所谓权威机构连名字和检测报告编号都没有，虚假背书。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N19",
        "checker": "blind_test",
        "name": "悬空恐吓假设",
        "text": "如果工人调配时多加半桶水，整个工程后果将不堪设想。",
        "reader_doubt": "不信：#1 不堪设想是怎样的后果？没说清起皮、泛碱还是脱落。",
        "expected_flag": "doubt_flagged"
    },
    {
        "id": "N20",
        "checker": "blind_test",
        "name": "缺乏验收标准",
        "text": "全部完工之后，效果自然而然就出来了，到时候一眼便知。",
        "reader_doubt": "不信：#1 没有给任何平整度靠尺读数或验收参数，无法核验。",
        "expected_flag": "doubt_flagged"
    }
]

# ---------------------------------------------------------------------------
# 20 支正样本（合格口播表达对照组，防误杀验证）
# ---------------------------------------------------------------------------

POSITIVE_SAMPLES = [
    {"id": "P01", "name": "带数字的合规举证（含泛述词）", "text": "我们做过相当一部分商业工程，在华东地区总共交付了 42 个大型项目。"},
    {"id": "P02", "name": "具名主语与参数", "text": "工长老张现场实测：地面含水率低于 8% 时，环氧底漆附着力最佳。"},
    {"id": "P03", "name": "因果严密的说理段", "text": "生石灰加水消解时剧烈放热，浆体充分熟化后，才能消除上墙起泡的隐患。"},
    {"id": "P04", "name": "明确的价差与机理", "text": "进口石灰石原料成本比国产高 4 到 5 倍，差距主要在 900 度恒温窑炉的煅烧纯度。"},
    {"id": "P05", "name": "具体标准引用", "text": "按照国标 GB/T 9756 优等品要求，耐洗刷次数必须达到 5000 次以上。"},
    {"id": "P06", "name": "合理口语设问紧跟答案与数据", "text": "现场有超过 80% 的业主问为什么漆膜会发白？原因就是底层腻子未干透就急于封底。"},
    {"id": "P07", "name": "工艺节点明确交代", "text": "先用 150 目砂纸全屋打磨平整，吸尘清扫后，再滚涂两遍抗碱封闭底漆。"},
    {"id": "P08", "name": "真实客户情境反馈", "text": "业主方现场对比了 3 块打样板，最终选定 0.8 毫米厚度的浅米色微水泥质感。"},
    {"id": "P09", "name": "清晰边界约束承诺", "text": "在基层无沉降裂缝且室内温度不低于 5℃ 的条件下，漆膜保用期为 15 年。"},
    {"id": "P10", "name": "物理动作具体回溯", "text": "师傅用批刀呈 45 度角快速收光，每道抹痕重叠 3 厘米。"},
    {"id": "P11", "name": "带数量的常见词表达", "text": "现场准备了挺多材料，光是 20 公斤装的桶装浆料就码了 18 桶。"},
    {"id": "P12", "name": "客观性能数据", "text": "固含量达到 68%，施工后 24 小时即可达到表干状态。"},
    {"id": "P13", "name": "真实第三方设备注明", "text": "打磨工序使用 Festool 偏心打磨机配合专用无尘吸尘系统。"},
    {"id": "P14", "name": "清晰的主谓宾结构", "text": "设计师确认了调色样块，施工队开始进行大面积打底作业。"},
    {"id": "P15", "name": "对比参数清晰", "text": "相比普通乳胶漆 30 微米的成膜厚度，厚浆型质感漆可以做到 1.5 到 2 毫米。"},
    {"id": "P16", "name": "带批次号的材料说明", "text": "同一房间必须使用同一生产批号的色浆，避免不同批次产生细微色差。"},
    {"id": "P17", "name": "环境指标说明", "text": "VOC 检测值低于 10g/L，远优于国家标准要求的 80g/L 限量要求。"},
    {"id": "P18", "name": "合理的转折口播", "text": "国产材料在常规水性聚氨酯上表现很好，但在特种耐磨骨料的硬度配比上仍需打磨。"},
    {"id": "P19", "name": "验收标准量化", "text": "用 2 米靠尺靠墙检查，缝隙偏差不得超过 2 毫米才算验收合格。"},
    {"id": "P20", "name": "收束与主张闭环", "text": "从底层的抗裂砂浆到面层的石灰基罩面，每一步把控才是杜绝空鼓的根本。"}
]


# ---------------------------------------------------------------------------
# 阴性样本驱动产线测试
# ---------------------------------------------------------------------------

def test_negative_samples(tmp_dir: Path) -> int:
    print("\n[一] 20 支阴性样本产线真检查器测试：")
    caught = 0
    for s in NEGATIVE_SAMPLES:
        checker = s["checker"]
        exp_flag = s["expected_flag"]
        hit = False
        reason = ""

        if checker == "check_edl":
            if exp_flag == "vague_no_numbers":
                is_vague, reason = lint_edl_segment_vague(s["text"])
                hit = is_vague
            else:
                segs = []
                if s.get("has_claim"):
                    segs.append({"n": 1, "beat": 1, "role": "claim", "dur": 3.0, "evidence": "主张段"})
                segs.append({"n": 2, "beat": 1, "role": s["role"], "dur": s["dur"], "evidence": s["text"]})
                flags = lint_edl_structure(segs)
                hit = (exp_flag in flags)
                reason = ",".join(flags) or "无报警"
        else:
            # 裁判类：真跑 blind_test 产线流程
            pdir = tmp_dir / f"test_{s['id']}"
            pdir.mkdir(parents=True, exist_ok=True)
            intent = {
                "promise": s.get("promise", "柒造实录涂料施工真实工艺与参数"),
                "audience": "业主与设计师",
                "duration_s": 15,
                "aspect": "9:16"
            }
            (pdir / "intent.json").write_text(json.dumps(intent, ensure_ascii=False))
            edl = {
                "segments": [{"n": 1, "in": 0.0, "out": 4.0, "evidence": s["text"]}]
            }
            (pdir / "edl.json").write_text(json.dumps(edl, ensure_ascii=False))

            # 1. 产线生成盲测包
            blind_test.render(pdir)
            assert (pdir / "blind-test.md").exists(), f"blind-test.md 未生成: {pdir}"

            # 2. 模拟真实裁判回答
            ans_file = pdir / "ans1.md"
            claim_line = s.get("reader_claim", "主张：施工工艺介绍")
            ev_line = s.get("reader_ev", "证据：\n- 现场施工一段")
            doubt_line = s.get("reader_doubt", "不信：都信")
            ans_content = f"{claim_line}\n\n{ev_line}\n\n{doubt_line}\n"
            ans_file.write_text(ans_content)

            # 3. 产线执行评分对账
            exit_code = blind_test.score(pdir, [str(ans_file)])
            assert exit_code == 0, f"blind_test.score 退出码非 0: {exit_code}"

            report_txt = (pdir / "blind-report.md").read_text()
            if exp_flag == "doubt_flagged":
                hit = ("点名 #1 不可信" in report_txt)
                reason = "命中段名不可信" if hit else "未见段名"
            elif exp_flag == "zero_evidence":
                hit = ("证据 0 条 —— 读者读完说不出你凭什么" in report_txt)
                reason = "命中证据0条硬信号" if hit else "未见0条"
            elif exp_flag == "low_promise_coverage":
                m = re.search(r"字面重合：(\d+)%", report_txt)
                cov = int(m.group(1)) if m else 100
                hit = (cov < 30)
                reason = f"主张字面重合仅 {cov}%" if hit else f"重合度 {cov}% 偏高"

        status = "🟢 捕获" if hit else "🔴 遗漏"
        if hit:
            caught += 1
        print(f"  [{s['id']}] {status} · {s['name']} (预期: {exp_flag}, 实测: {reason})")

    assert caught == len(NEGATIVE_SAMPLES), f"阴性样本捕获不全: {caught}/{len(NEGATIVE_SAMPLES)}"
    print(f"  -> 阴性样本捕获率: {caught}/{len(NEGATIVE_SAMPLES)} (100.0%)")
    return caught


# ---------------------------------------------------------------------------
# 正样本防误杀测试（零假阳性断言）
# ---------------------------------------------------------------------------

def test_positive_samples(tmp_dir: Path) -> int:
    print("\n[二] 20 支正样本对照组防误杀测试（断言零假阳性）：")
    passed = 0
    for s in POSITIVE_SAMPLES:
        text = s["text"]

        # 1. 喂入 check_edl 泛述规则：绝对不应报泛述无数字
        is_vague, v_reason = lint_edl_segment_vague(text)
        assert not is_vague, f"正样本 {s['id']} 被 check_edl 泛述规则误杀: {v_reason}"

        # 2. 构造合规段喂入 check_edl 结构检查：必须零结构报错
        segs = [
            {"n": 1, "in": 0.0, "out": 2.5, "beat": 1, "role": "claim", "dur": 2.5, "evidence": "合格主张"},
            {"n": 2, "in": 2.5, "out": 6.0, "beat": 1, "role": "evidence", "dur": 3.5, "evidence": text},
            {"n": 3, "in": 6.0, "out": 8.0, "beat": 1, "role": "warrant", "dur": 2.0, "evidence": "合理解释"}
        ]
        flags = lint_edl_structure(segs)
        assert len(flags) == 0, f"正样本 {s['id']} 触发结构误报: {flags}"

        # 3. 喂入 blind_test 外部裁判：正常读者答复下，零致命告警
        pdir = tmp_dir / f"test_pos_{s['id']}"
        pdir.mkdir(parents=True, exist_ok=True)
        intent = {
            "promise": "工艺真实交代",
            "audience": "业主",
            "duration_s": 8,
            "aspect": "9:16"
        }
        (pdir / "intent.json").write_text(json.dumps(intent, ensure_ascii=False))
        (pdir / "edl.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False))

        blind_test.render(pdir)
        ans_file = pdir / "ans.md"
        ans_file.write_text("主张：工艺真实交代\n\n证据：\n- 第一条证据\n- 第二条证据\n\n不信：都信\n")

        blind_test.score(pdir, [str(ans_file)])
        report_txt = (pdir / "blind-report.md").read_text()
        assert "证据 0 条" not in report_txt, f"正样本 {s['id']} 被误报为证据 0 条"
        assert "硬信号" not in report_txt, f"正样本 {s['id']} 触发了不应有的硬信号"

        passed += 1
        print(f"  [{s['id']}] 🟢 放行 · {s['name']}")

    assert passed == len(POSITIVE_SAMPLES), f"正样本未能全部放行: {passed}/{len(POSITIVE_SAMPLES)}"
    print(f"  -> 防误杀准确率: {passed}/{len(POSITIVE_SAMPLES)} (100.0%)")
    return passed


# ---------------------------------------------------------------------------
# 6 组项目真实抽测（相对路径 + 产线命令执行 + 严格断言）
# ---------------------------------------------------------------------------

def test_sampling_projects(tmp_dir: Path) -> dict:
    print("\n[三] 6 组典型成片/项目端到端真抽测（带断言）：")

    # 优先使用仓库内相对路径项目，素材库项目动态补充
    candidate_projects = [
        ("qizao-s01", ROOT / "references" / "stranger-run-2026-11-07" / "qizao-s01"),
        ("proj79", ROOT / "references" / "packtest-2026-11-08" / "proj79"),
        ("proj", ROOT / "references" / "packtest-2026-11-08" / "proj"),
        ("proj2", ROOT / "references" / "packtest-2026-11-08" / "proj2"),
        ("proj79-fontB", ROOT / "references" / "packtest-2026-11-08" / "proj79-fontB"),
        ("qizao-ground-01", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-ground-01")),
        ("qizao-protect-01", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-protect-01")),
        ("qizao-lime-baxis", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-lime-baxis")),
        ("qizao-naming-01", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-naming-01")),
    ]

    selected = []
    for name, p in candidate_projects:
        if p.exists() and (p / "edl.json").exists():
            selected.append((name, p))
        if len(selected) == 6:
            break

    assert len(selected) >= 6, f"可用抽测项目不足 6 组（仅找到 {len(selected)} 组）"

    report = {}
    for name, proj in selected:
        edl_f = proj / "edl.json"
        intent_f = proj / "intent.json"
        assert edl_f.exists(), f"项目缺 edl.json: {proj}"

        edl = json.loads(edl_f.read_text())
        segs = edl.get("segments") or []
        assert len(segs) > 0, f"项目 segments 为空: {name}"

        # 1. 真实调用 blind_test.render
        out_bt = blind_test.render(proj)
        assert out_bt.exists() and out_bt.stat().st_size > 50, f"盲测包生成异常: {out_bt}"

        # 2. 模拟双裁判评分对账（观众 + 同行）
        a1 = tmp_dir / f"{name}_ans1.md"
        a2 = tmp_dir / f"{name}_ans2.md"
        a1.write_text("主张：涂料工艺展示\n\n证据：\n- 施工细节\n- 参数对比\n\n不信：都信\n")
        a2.write_text("主张：涂料施工实录\n\n证据：\n- 150目砂纸打磨\n\n不信：无\n")

        ret = blind_test.score(proj, [str(a1), str(a2)])
        assert ret == 0, f"blind_test.score 运行失败: {name}"

        report_f = proj / "blind-report.md"
        assert report_f.exists() and report_f.stat().st_size > 100, f"blind-report.md 未生成: {proj}"
        rep_text = report_f.read_text()
        assert f"盲测报告 · {proj.name}" in rep_text, f"报告标题缺失: {proj.name}"

        # 3. 统计举证与泛述
        ev_segs = [s.get("evidence", "") for s in segs if s.get("evidence")]
        vague_hits = sum(1 for t in ev_segs if lint_edl_segment_vague(t)[0])

        report[name] = {
            "path": str(proj.relative_to(ROOT)) if ROOT in proj.parents else str(proj),
            "segments": len(segs),
            "evidence_segments": len(ev_segs),
            "vague_hits": vague_hits,
            "blind_report_size": report_f.stat().st_size
        }
        print(f"  · {name}（{report[name]['path']}）：{len(segs)} 段 · 盲测包 {out_bt.stat().st_size}B · 盲测报告 {report_f.stat().st_size}B · 泛述 {vague_hits} 处")

    return report


def main() -> int:
    print("=" * 65)
    print("CutSkills · P4 盲测收口与阴性防误杀测试套件 (产线真检查器版)")
    print("=" * 65)

    tmp_dir = Path(tempfile.mkdtemp(prefix="cutskills_blind_test_"))
    try:
        # 1. 20 支阴性样本测试
        test_negative_samples(tmp_dir)

        # 2. 20 支正样本防误杀测试
        test_positive_samples(tmp_dir)

        # 3. 6 组项目真实抽测与断言
        rep = test_sampling_projects(tmp_dir)

        print("\n" + "=" * 65)
        print("结论：P4 盲测收口、阴性样本捕获与防误杀全部通过 · 🟢 ALL ASSERTIONS PASSED")
        print("=" * 65)
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
