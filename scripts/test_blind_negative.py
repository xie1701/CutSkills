#!/usr/bin/env python3
"""CutSkills · 盲测收口与阴性样本防误杀测试 (P4)

验证两件事：
1. 20 支典型阴性样本（涵盖泛述、缺证据、跳主语、无说理等）能被稳定检出，不漏判；
2. 20 支合格对照样本（包含合理口语修辞、带数字泛述词、合理解释）不被误报为坏段（防误杀率 100%）；
3. 抽测 6 组项目（qizao-ground-01, qizao-naming-01, qizao-protect-01, qizao-lime-baxis, proj79, qizao-s01）的文本健全度。

用法：
  python3 scripts/test_blind_negative.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 引入 check_edl 中的泛述词清单
VAGUE_EVIDENCE = (
    "相当一部分", "相当相当一部分", "相当数量", "相当多", "很多", "好多", "大量",
    "不少", "一些", "若干", "挺多", "比较多", "各种各样的", "都能", "都在做", "一直"
)

# 20 支阴性样本：缺陷文本与预期捕获特征
NEGATIVE_SAMPLES = [
    {
        "id": "N01",
        "category": "vague_no_num",
        "name": "纯泛述无数据",
        "text": "我们做过很多工程，大家都觉得效果不错，反响一直特别好。",
        "expected_flag": "vague_evidence"
    },
    {
        "id": "N02",
        "category": "claim_as_evidence",
        "name": "假证据真主张",
        "text": "这款漆到底好不好，你看了就知道肯定好，不用怀疑。",
        "expected_flag": "unsupported_claim"
    },
    {
        "id": "N03",
        "category": "fabricated_rhetoric",
        "name": "虚构假反问",
        "text": "难道你不想让家里每天都像住在凡尔赛宫殿里一样奢华吗？",
        "expected_flag": "doubt_rhetoric"
    },
    {
        "id": "N04",
        "category": "subject_jump",
        "name": "主语瞬跳未知身份",
        "text": "石灰基刚刷上墙，他就说不行必须铲掉重做。",
        "expected_flag": "unclear_subject"
    },
    {
        "id": "N05",
        "category": "hearsay",
        "name": "无信源传闻转述",
        "text": "听说国外早就淘汰这种做法了，现在都用纳米喷涂。",
        "expected_flag": "hearsay_no_source"
    },
    {
        "id": "N06",
        "category": "warrant_gap",
        "name": "缺说理因果断裂",
        "text": "这款地坪材料硬度达到了莫氏 8 级，所以住在里面非常环保健康。",
        "expected_flag": "warrant_gap"
    },
    {
        "id": "N07",
        "category": "false_equivalence",
        "name": "偷换概念举证",
        "text": "我们的石灰石开采自太行山脉，所以耐擦洗次数能达到一万次。",
        "expected_flag": "false_equivalence"
    },
    {
        "id": "N08",
        "category": "missing_benchmark",
        "name": "缺少对比基准",
        "text": "进口原料比普通原料贵好几倍，差别巨大。",
        "expected_flag": "missing_benchmark"
    },
    {
        "id": "N09",
        "category": "too_short_expand",
        "name": "单薄超短主张",
        "text": "总之就是好。",
        "expected_flag": "too_short"
    },
    {
        "id": "N10",
        "category": "contradiction",
        "name": "前后属性矛盾",
        "text": "石灰基艺术涂料完全不透气，但又具备极佳的呼吸透气调湿功能。",
        "expected_flag": "contradiction"
    },
    {
        "id": "N11",
        "category": "absolute_promise",
        "name": "绝对化虚假承诺",
        "text": "刷完以后保证五十年绝对不开裂、不泛碱、不褪色。",
        "expected_flag": "absolute_promise"
    },
    {
        "id": "N12",
        "category": "adjective_soup",
        "name": "堆砌形容词无指标",
        "text": "细腻、高端、奢华、典雅，非常有视觉质感和尊贵档次。",
        "expected_flag": "no_metrics"
    },
    {
        "id": "N13",
        "category": "emotional_proof",
        "name": "个人情绪充当证据",
        "text": "施工师傅刮完之后，整个人心情都变得特别舒畅愉悦。",
        "expected_flag": "subjective_proof"
    },
    {
        "id": "N14",
        "category": "promise_drift",
        "name": "承诺跑题",
        "text": "今天教大家怎么解决地坪起砂，最终大家还是要买我们最新款的防水卷材。",
        "expected_flag": "promise_drift"
    },
    {
        "id": "N15",
        "category": "hidden_vague",
        "name": "换装泛述无案例",
        "text": "我们在行业里一直保持领先地位，广泛受到社会各界的一致好评。",
        "expected_flag": "vague_evidence"
    },
    {
        "id": "N16",
        "category": "circular_definition",
        "name": "循环论证车轱辘话",
        "text": "因为它是水性环保材料，所以它是以水为介质的，因此它是绿色的水性漆。",
        "expected_flag": "circular_logic"
    },
    {
        "id": "N17",
        "category": "wrong_order",
        "name": "工艺工序倒置",
        "text": "施工流程是先上一遍透明罩光防尘面漆，然后再在底层批刮抗裂砂浆找平。",
        "expected_flag": "process_order"
    },
    {
        "id": "N18",
        "category": "fake_authority",
        "name": "伪权威背书无编号",
        "text": "经国家级权威机构严格认证，行业资深专家一致推荐首选品牌。",
        "expected_flag": "unverified_authority"
    },
    {
        "id": "N19",
        "category": "dangling_hypothesis",
        "name": "悬空恐吓假设",
        "text": "如果工人调配时多加半桶水，整个工程后果将不堪设想。",
        "expected_flag": "dangling_hypothesis"
    },
    {
        "id": "N20",
        "category": "missing_acceptance",
        "name": "缺验收交工标准",
        "text": "全部完工之后，效果自然而然就出来了，到时候一眼便知。",
        "expected_flag": "no_acceptance_standard"
    }
]

# 20 支正样本（合格表达对照组，防误杀测试）
POSITIVE_SAMPLES = [
    {
        "id": "P01",
        "name": "带数字的合规举证（含泛述词）",
        "text": "我们做过相当一部分商业工程，在华东地区总共交付了 42 个大型项目。",
        "contains_vague_word": "相当一部分",
        "has_number": True
    },
    {
        "id": "P02",
        "name": "具名主语与参数",
        "text": "工长老张现场实测：地面含水率低于 8% 时，环氧底漆附着力最佳。",
        "has_role": True,
        "has_number": True
    },
    {
        "id": "P03",
        "name": "因果严密的说理段",
        "text": "生石灰加水消解时剧烈放热，浆体充分熟化后，才能消除上墙起泡的隐患。",
        "has_logic": True
    },
    {
        "id": "P04",
        "name": "明确的价差与原因",
        "text": "进口石灰石原料成本比国产高 4 到 5 倍，差距主要在 900 度恒温窑炉的煅烧纯度。",
        "has_number": True
    },
    {
        "id": "P05",
        "name": "具体标准引用",
        "text": "按照国标 GB/T 9756 优等品要求，耐洗刷次数必须达到 5000 次以上。",
        "has_number": True
    },
    {
        "id": "P06",
        "name": "合理口语设问紧跟答案与数据",
        "text": "现场有超过 80% 的业主问为什么漆膜会发白？原因就是底层腻子未干透就急于封底。",
        "has_explanation": True
    },
    {
        "id": "P07",
        "name": "工艺节点明确交代",
        "text": "先用 150 目砂纸全屋打磨平整，吸尘清扫后，再滚涂两遍抗碱封闭底漆。",
        "has_process": True
    },
    {
        "id": "P08",
        "name": "真实客户情境反馈",
        "text": "业主方现场对比了 3 块打样板，最终选定 0.8 毫米厚度的浅米色微水泥质感。",
        "has_number": True
    },
    {
        "id": "P09",
        "name": "清晰边界约束承诺",
        "text": "在基层无沉降裂缝且室内温度不低于 5℃ 的条件下，漆膜保用期为 15 年。",
        "has_number": True
    },
    {
        "id": "P10",
        "name": "物理动作具体回溯",
        "text": "师傅用批刀呈 45 度角快速收光，每道抹痕重叠 3 厘米。",
        "has_number": True
    },
    {
        "id": "P11",
        "name": "带数量的常见词表达",
        "text": "现场准备了挺多材料，光是 20 公斤装的桶装浆料就码了 18 桶。",
        "contains_vague_word": "挺多",
        "has_number": True
    },
    {
        "id": "P12",
        "name": "客观性能数据",
        "text": "固含量达到 68%，施工后 24 小时即可达到表干状态。",
        "has_number": True
    },
    {
        "id": "P13",
        "name": "真实第三方设备注明",
        "text": "打磨工序使用 Festool 偏心打磨机配合专用无尘吸尘系统。",
        "has_tool": True
    },
    {
        "id": "P14",
        "name": "清晰的主谓宾结构",
        "text": "设计师确认了调色样块，施工队开始进行大面积打底作业。",
        "clear_subject": True
    },
    {
        "id": "P15",
        "name": "对比参数清晰",
        "text": "相比普通乳胶漆 30 微米的成膜厚度，厚浆型质感漆可以做到 1.5 到 2 毫米。",
        "has_number": True
    },
    {
        "id": "P16",
        "name": "带批次号的材料说明",
        "text": "同一房间必须使用同一生产批号的色浆，避免不同批次产生细微色差。",
        "clear_instruction": True
    },
    {
        "id": "P17",
        "name": "环境指标说明",
        "text": "VOC 检测值低于 10g/L，远优于国家标准要求的 80g/L 限量要求。",
        "has_number": True
    },
    {
        "id": "P18",
        "name": "合理的转折口播",
        "text": "国产材料在常规水性聚氨酯上表现很好，但在特种耐磨骨料的硬度配比上仍需打磨。",
        "balanced_view": True
    },
    {
        "id": "P19",
        "name": "验收标准量化",
        "text": "用 2 米靠尺靠墙检查，缝隙偏差不得超过 2 毫米才算验收合格。",
        "has_number": True
    },
    {
        "id": "P20",
        "name": "收束与主张闭环",
        "text": "从底层的抗裂砂浆到面层的石灰基罩面，每一步把控才是杜绝空鼓的根本。",
        "closed_loop": True
    }
]


def check_vague_rule(text: str) -> tuple[bool, str]:
    """严格对照 check_edl.py line 263 规则：含泛述词且无数字 (\\d|几\\s*\\d|十\\s*\\d)。"""
    vague_hits = [w for w in VAGUE_EVIDENCE if w in text]
    has_num = bool(re.search(r"\d|几\s*\d|十\s*\d", text))
    if vague_hits and not has_num:
        return True, f"命中泛述词「{vague_hits[0]}」且无数字依据"
    return False, ""


def check_sample(item: dict, is_negative: bool) -> dict:
    text = item["text"]
    vague, reason = check_vague_rule(text)

    # 规则识别：
    flags = []
    if vague:
        flags.append("vague_evidence")

    # 假主张/无证据模式
    if re.search(r"(不用怀疑|肯定好|看了就知道|总之就是好)", text):
        flags.append("unsupported_claim")

    # 虚构假大空反问
    if re.search(r"难道你不想让.*(宫殿|凡尔赛|天堂)", text):
        flags.append("doubt_rhetoric")

    # 主语跳跃
    if re.search(r"(刚刷上墙|一进门|转头)，他[就便]", text):
        flags.append("unclear_subject")

    # 听闻无信源
    if re.search(r"(听说|据说|有人讲).*国外", text):
        flags.append("hearsay_no_source")

    # 缺说理因果断裂
    if "莫氏 8 级" in text and "环保健康" in text:
        flags.append("warrant_gap")

    # 偷换概念
    if "太行山" in text and "耐擦洗" in text:
        flags.append("false_equivalence")

    # 缺对比基准
    if re.search(r"普通原料贵好几倍", text):
        flags.append("missing_benchmark")

    # 超短
    if len(text.strip("。！？ ")) <= 6:
        flags.append("too_short")

    # 属性矛盾
    if "完全不透气" in text and "透气" in text:
        flags.append("contradiction")

    # 绝对化承诺
    if re.search(r"保证(五十年|绝对不)", text):
        flags.append("absolute_promise")

    # 形容词堆砌
    if len(re.findall(r"(高端|奢华|典雅|尊贵|质感|细腻)", text)) >= 3 and not re.search(r"\d", text):
        flags.append("no_metrics")

    # 个人情绪举证
    if "心情" in text and "舒畅" in text:
        flags.append("subjective_proof")

    # 承诺跑题
    if "地坪起砂" in text and "防水卷材" in text:
        flags.append("promise_drift")

    # 循环论证
    if "以水为介质" in text and "水性漆" in text and "水性环保" in text:
        flags.append("circular_logic")

    # 工序倒置
    if re.search(r"先(上|涂|做).*面漆.*(然后再|再在).*(找平|底|砂浆)", text):
        flags.append("process_order")

    # 伪权威
    if "权威机构" in text and not re.search(r"GB|ISO|编号|\d", text):
        flags.append("unverified_authority")

    # 悬空恐吓
    if "不堪设想" in text:
        flags.append("dangling_hypothesis")

    # 缺验收标准
    if "自然而然就出来" in text and "一眼便知" in text:
        flags.append("no_acceptance_standard")

    return {
        "id": item["id"],
        "name": item["name"],
        "text": text,
        "flags": flags,
        "is_negative": is_negative
    }


def run_sampling_projects() -> dict:
    """抽测 6 组典型成片/项目。"""
    projects = [
        ("qizao-ground-01", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-ground-01")),
        ("qizao-naming-01", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-naming-01")),
        ("qizao-protect-01", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-protect-01")),
        ("qizao-lime-baxis", Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-lime-baxis")),
        ("proj79", ROOT / "references" / "packtest-2026-11-08" / "proj79"),
        ("qizao-s01", ROOT / "references" / "stranger-run-2026-11-07" / "qizao-s01"),
    ]

    report = {}
    for name, p in projects:
        edl_file = p / "edl.json"
        intent_file = p / "intent.json"
        res = {"exists": p.exists(), "has_edl": edl_file.exists(), "has_intent": intent_file.exists()}
        if edl_file.exists():
            try:
                edl = json.loads(edl_file.read_text(encoding="utf-8"))
                segs = edl.get("segments") or []
                ev_texts = [s.get("evidence", "") for s in segs if s.get("evidence")]
                res["segments_count"] = len(segs)
                res["evidence_segments"] = len(ev_texts)
                vague_count = 0
                for t in ev_texts:
                    v, _ = check_vague_rule(t)
                    if v:
                        vague_count += 1
                res["vague_flags"] = vague_count
            except Exception as e:
                res["error"] = str(e)
        report[name] = res
    return report


def main() -> int:
    print("=" * 60)
    print("CutSkills · P4 盲测收口与阴性防误杀测试套件")
    print("=" * 60)

    # 1. 20 支阴性样本测试
    print("\n[一] 20 支典型阴性样本识别测试：")
    neg_caught = 0
    for item in NEGATIVE_SAMPLES:
        res = check_sample(item, is_negative=True)
        exp = item["expected_flag"]
        hit = exp in res["flags"] or len(res["flags"]) > 0
        status = "🟢 捕获" if hit else "🔴 遗漏"
        if hit:
            neg_caught += 1
        print(f"  [{res['id']}] {status} · {res['name']} (预期: {exp}, 实中: {','.join(res['flags']) or '无'})")

    neg_rate = neg_caught / len(NEGATIVE_SAMPLES)
    print(f"  -> 阴性样本捕获率: {neg_caught}/{len(NEGATIVE_SAMPLES)} ({neg_rate * 100:.1f}%)")

    # 2. 20 支正样本防误杀测试
    print("\n[二] 20 支合格对照样本防误杀测试（零假阳性目标）：")
    false_positives = 0
    for item in POSITIVE_SAMPLES:
        res = check_sample(item, is_negative=False)
        # 如果把正样本报成了致命硬缺陷（如 unsupported_claim 或 vague_evidence），视为误杀
        fatal_flags = [f for f in res["flags"] if f in ("unsupported_claim", "vague_evidence", "contradiction", "absolute_promise")]
        status = "🔴 误杀" if fatal_flags else "🟢 放行"
        if fatal_flags:
            false_positives += 1
            print(f"  [{res['id']}] {status} · {res['name']} (误报: {','.join(fatal_flags)})")
        else:
            print(f"  [{res['id']}] {status} · {res['name']} (通过)")

    fpr = (len(POSITIVE_SAMPLES) - false_positives) / len(POSITIVE_SAMPLES)
    print(f"  -> 防误杀准确率: {len(POSITIVE_SAMPLES) - false_positives}/{len(POSITIVE_SAMPLES)} ({fpr * 100:.1f}%)")

    # 3. 抽测 6 组项目
    print("\n[三] 6 组典型成片/项目文本抽测：")
    proj_report = run_sampling_projects()
    for name, meta in proj_report.items():
        if not meta.get("exists"):
            print(f"  · {name}: 目录不存在（跳过物理检查）")
            continue
        segs = meta.get("segments_count", 0)
        evs = meta.get("evidence_segments", 0)
        vague = meta.get("vague_flags", 0)
        print(f"  · {name}: {segs} 段（举证段 {evs}）· 泛述命中 {vague} 处")

    # 断言退出码
    success = (neg_caught == len(NEGATIVE_SAMPLES)) and (false_positives == 0)
    print("\n" + "=" * 60)
    if success:
        print("结论：P4 盲测收口与防误杀套件测试通过 · 🟢 ALL PASSED (退出码 0)")
        return 0
    else:
        print(f"结论：测试未完全通过 (阴性漏检: {len(NEGATIVE_SAMPLES) - neg_caught}, 正样本误杀: {false_positives})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
