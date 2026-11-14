#!/usr/bin/env python3
"""CutSkills · T-006 钩子策略真判定 + 钩子库出处可追溯回归

为什么要有它：
前 3 秒策略长期处于「只出候选、良率卡读 passed 却永远是 null / 假通过」的状态。
本测试通过真 import evaluate_hook_policy 与跑 CLI 断言真实的策略判定与门禁，
涵盖超时、平票、冲突、未验、全绿与重构持久化 7 组正负样本，全程临时目录隔离。

还有另一半（钩子库出处）：`ideate.HOOK_BANK` 曾是十式二手改写——原文「开篇钩子八大切入点」
缺四条（人群/效果/情绪/好奇），多两条没标出处的自建，还有三条把两套体系混着捏。
BRIEF 要求「生成选题/文案必须写明用了原文哪一节、哪个公式或切入点」，所以出处不能是
手写备注：下面拿 `references/content-system.md` 的**真实标题**当锚点，逐级核对每条钩子的
`source`，并断言 `audience_present_state` 在 ideate → brief → hooks 三方读写闭环（旧名
`belief_now` 不许崩）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from hooks import evaluate_hook_policy, build  # noqa: E402
import ideate                                   # noqa: E402

PY = sys.executable
DOC = ROOT / "references" / "content-system.md"
POINT_GROUP = "开篇钩子八大切入点"


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


# ======================================================================
# 钩子库出处可追溯：出处是能被程序校验的结构，不是手写备注
# ======================================================================
def _sections() -> dict[str, dict]:
    """把原文按标题切成 {标题: {level, body}}。body = 本节标题之后，到下一个同级或更高标题之前。"""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    heads: list[tuple[int, int, str]] = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.+?)\s*$", ln)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    out: dict[str, dict] = {}
    for k, (i, level, title) in enumerate(heads):
        end = len(lines)
        for j, lv, _t in heads[k + 1:]:
            if lv <= level:
                end = j
                break
        out[title] = {"level": level, "body": lines[i + 1:end], "line": i + 1}
    return out


SECTIONS = _sections()


def _list_children(body: list[str], name: str) -> set[str] | None:
    """在 body 里找列表项 `- name`，返回它下面**直接子项**的名字集合（按缩进判嵌套）。

    分组名不存在返回 None；分组存在但没有子项返回空集。
    """
    gind: int | None = None
    kids: list[tuple[int, str]] = []
    for ln in body:
        m = re.match(r"^(\s*)-\s+(.+?)\s*$", ln)
        if m is None:
            continue
        indent, item = len(m.group(1)), m.group(2)
        if gind is None:
            if item == name:
                gind = indent
            continue
        if indent <= gind:            # 同级或更高一级的同组项出现 → 本分组结束
            break
        kids.append((indent, item))
    if gind is None:
        return None
    if not kids:
        return set()
    base = min(i for i, _ in kids)
    return {t for i, t in kids if i == base}


def check_ref(ref: dict, where: str) -> list[str]:
    """逐级校验一条出处：章节标题 → 子章节标题 → 分组 → 切入点/招式名。返回错误清单。"""
    errs: list[str] = []
    sec = list(ref.get("sec") or [])
    if not sec:
        return [f"{where}：source 缺 sec（章节路径）"]
    prev_title, prev_level = None, 0
    for depth, title in enumerate(sec):
        s = SECTIONS.get(title)
        if s is None:
            errs.append(f"{where}：原文没有章节标题「{title}」")
            return errs
        if depth == 0:
            if s["level"] != 2:
                errs.append(f"{where}：顶层章节「{title}」在原文是 {'#' * s['level']} 级，应为 ##")
        else:
            parent = SECTIONS.get(prev_title) or {"body": []}
            if not any(re.match(rf"^#+\s+{re.escape(title)}\s*$", ln) for ln in parent["body"]):
                errs.append(f"{where}：「{title}」不在『{prev_title}』一节里")
            if s["level"] <= prev_level:
                errs.append(f"{where}：「{title}」不是『{prev_title}』的下级章节")
        prev_title, prev_level = title, s["level"]
    body = SECTIONS[sec[-1]]["body"]
    key = str(ref.get("key") or "")
    group = ref.get("group")
    if not key:
        errs.append(f"{where}：source 缺 key（切入点/招式名）")
        return errs
    if group:
        kids = _list_children(body, group)
        if kids is None:
            errs.append(f"{where}：『{sec[-1]}』里没有分组「{group}」")
        elif key not in kids:
            errs.append(f"{where}：「{group}」下没有「{key}」（原文实有：{'、'.join(sorted(kids))}）")
    else:
        if not any(re.match(rf"^-\s+{re.escape(key)}\s*$", ln) for ln in body):
            errs.append(f"{where}：『{sec[-1]}』一节里没有「{key}」这一条")
    return errs


def test_eight_points():
    """八大切入点逐条有实现：名单从原文现抓，不抄进测试。"""
    points = _list_children(SECTIONS["水桶流视频"]["body"], POINT_GROUP)
    assert points is not None, "原文『水桶流视频』一节里找不到『开篇钩子八大切入点』分组"
    assert len(points) == 8, f"原文该分组应为 8 条，实抓 {len(points)} 条：{sorted(points)}"

    covered = {h["source"]["key"] for h in ideate.HOOK_BANK.values()
               if not (h.get("source") or {}).get("self_built")
               and (h.get("source") or {}).get("group") == POINT_GROUP}
    missing = sorted(points - covered)
    assert not missing, f"八大切入点还有 {len(missing)} 条没有显式实现：{missing}"
    extra = sorted(covered - points)
    assert not extra, f"钩子里挂了『{POINT_GROUP}』、但原文该分组没有的名字：{extra}"
    print(f"  ✅ 八大切入点逐条有实现（{len(points)}/{len(points)}）："
          f"{'、'.join(sorted(points))}")


def test_self_built_marked():
    """原文没有的式子必须标「非原文」，并写清借的是哪一式（basis 也要过锚点）。"""
    self_built = []
    for code, h in ideate.HOOK_BANK.items():
        src = h.get("source") or {}
        text = ideate.hook_source(h)
        if src.get("self_built"):
            self_built.append(h["name"])
            assert "非原文" in text, f"{code} 是自建，出处里却没有「非原文」：{text}"
            assert src.get("note"), f"{code} 自建缺 note（得说清它是怎么来的）"
            assert src.get("basis"), f"{code} 自建没指明借的是原文哪一式"
            assert check_ref(src["basis"], f"{code}（basis）") == [], \
                f"{code} 自建的 basis 指错了原文位置：{src['basis']}"
        else:
            assert text.startswith("原文『"), f"{code} 的出处不是引用原文：{text}"
            assert "非原文" not in text, f"{code} 不是自建，却写了「非原文」：{text}"
    for need in ("算清成本明账", "结果倒叙吸睛"):
        assert need in self_built, f"「{need}」原文没有，必须标成自建；当前自建项={self_built}"
    print(f"  ✅ 自建项带「非原文」标记且写明借的式子：{len(self_built)} 条（{'、'.join(self_built)}）")


def test_source_anchors():
    """每条钩子 / 每条流的结构出处逐级命中原文标题；旧二手结构名不能复活。"""
    errs: list[str] = []
    for code, h in ideate.HOOK_BANK.items():
        assert h.get("formula"), f"{code} 缺 formula（只能是可套用的句式，不能空着）"
        assert h.get("tpl") and "{" in h["tpl"], f"{code} 缺可填的 tpl 句式"
        src = h.get("source") or {}
        if src.get("self_built"):
            continue
        errs += check_ref(src, code)
    mid = _list_children(SECTIONS["中间内容"]["body"], "结构框架") or set()
    methods = {ln.strip()[2:].strip() for ln in SECTIONS["中间内容"]["body"]
               if re.match(r"^-\s+\S+式\s*$", ln)}
    for st in ideate.STREAMS:
        errs += check_ref(st["structure_source"], f"{st['id']} 结构")
        errs += check_ref(st["method_source"], f"{st['id']} 手法")
        assert st["structure"] in mid, f"{st['id']} 结构「{st['structure']}」不在原文 3 框架里：{sorted(mid)}"
        assert st["method"] in methods, f"{st['id']} 手法「{st['method']}」不在原文 4 手法里：{sorted(methods)}"
    assert not errs, "出处锚点校验失败：\n    " + "\n    ".join(errs)
    src_text = (HERE / "ideate.py").read_text(encoding="utf-8")
    for old in ("观点锤击式", "故事反转式", "成本算账式"):
        assert old not in src_text, f"旧二手结构名「{old}」还在 ideate.py 里，没改回原文叫法"
    print(f"  ✅ {len(ideate.HOOK_BANK)} 条钩子 + {len(ideate.STREAMS)} 条流的结构出处，"
          "逐级锚点全部命中原文标题")


def _mk_lib(base: Path) -> Path:
    lib = base / "lib"
    (lib / "_cutskills").mkdir(parents=True)
    (lib / "_cutskills" / "index.json").write_text(json.dumps({"assets": []}, ensure_ascii=False))
    return lib


def _hook_c_text(proj: Path) -> tuple[str, str]:
    """跑 hooks.py，取 C 候选（反差提问）的画大字与被引用的现状值。"""
    h = json.loads((proj / "hooks.json").read_text())
    cand = next(c for c in h["candidates"] if c["id"] == "C")
    return cand["elements"][0]["props"]["text"], (h.get("basis") or {}).get("audience_present_state") or ""


def test_present_state_loop():
    """audience_present_state 读写闭环：ideate → brief → hooks；旧数据 belief_now 不许崩。"""
    with tempfile.TemporaryDirectory(prefix="cutskills_present_state_") as td:
        base = Path(td)
        lib = _mk_lib(base)
        proj = base / "proj"
        proj.mkdir()

        # 1) ideate --topic：每套提案、每个钩子都带出处；拍片卡里人读得到
        r = subprocess.run([PY, str(HERE / "ideate.py"), "--project", str(proj),
                            "--topic", "三百万装修六百平茶楼怎么避坑",
                            "--persona", "二十年餐饮老炮", "--audience", "珠海找茶楼的人"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"ideate 失败：{r.stderr[-300:]}"
        idea = json.loads((proj / "ideation.json").read_text())
        for p in idea["proposals"]:
            for hk in p["hooks"]:
                assert hk.get("source", ""), f"钩子 {hk['type']} 没有出处：{hk}"
            assert p.get("structure_source"), f"提案 {p['id']} 没有结构出处"
        assert "出处：" in (proj / "shoot-guide.md").read_text(encoding="utf-8"), \
            "拍片卡里看不到出处，人读文案就是断的"

        # 2) --to-plan 写的是统一字段，不许再写 belief_now
        r = subprocess.run([PY, str(HERE / "ideate.py"), "--project", str(proj), "--to-plan"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"ideate --to-plan 失败：{r.stderr[-300:]}"
        brief = json.loads((proj / "brief.json").read_text())
        assert brief.get("audience_present_state"), "brief.json 缺 audience_present_state（钩子没有依据）"
        assert "belief_now" not in brief, f"brief.json 还在写旧字段 belief_now：{sorted(brief)}"

        # 3) hooks.py 消费同一字段：C 候选念的就是这句话
        r = subprocess.run([PY, str(HERE / "hooks.py"), "--project", str(proj), "--library", str(lib)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"hooks.py 失败：{r.stderr[-300:]}"
        text, basis_state = _hook_c_text(proj)
        state = brief["audience_present_state"]
        assert basis_state == state, f"hooks.json basis 没读到现状：{basis_state!r} != {state!r}"
        # 卡上念的必须是「他现在信的那句」的开头（按标点/词界截短，不得串成别的话），
        # 也不能太长：keyword 大字单排墨迹要撑到画幅宽的 70%（check_keyword_text）。
        assert text.endswith("？") and len(text) <= 11 and state.startswith(text[:-1]), \
            f"C 候选没念现状：{text!r}（现状 {state!r}）"
        print(f"  ✅ ideate→brief→hooks 同一个字段跑通：{text}")

        # 4) 老数据：只有 belief_now 的项目不许崩，钩子照旧有依据
        legacy = base / "legacy"
        legacy.mkdir()
        (legacy / "brief.json").write_text(json.dumps(
            {"audience": "装修业主", "belief_now": "进口一定比国产好",
             "change": "按场景选", "length_s": 45, "style_pack": "talking-head-v1"},
            ensure_ascii=False))
        r = subprocess.run([PY, str(HERE / "hooks.py"), "--project", str(legacy), "--library", str(lib)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"旧项目跑 hooks.py 崩了：{r.stderr[-300:]}"
        text, basis_state = _hook_c_text(legacy)
        assert basis_state == "进口一定比国产好", f"旧字段 belief_now 没被兼容读到：{basis_state!r}"
        assert text == "进口一定比国产好？", f"旧项目钩子没念现状：{text!r}"
        r = subprocess.run([PY, str(HERE / "brief.py"), "--project", str(legacy), "--show"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and "进口一定比国产好" in r.stdout, \
            f"brief.py --show 读不了旧项目：{r.returncode} {(r.stderr or '')[-300:]}"
        print(f"  ✅ 老数据 belief_now 兼容读取（hooks.py / brief.py 都读到）：{text}")

        # 5) 写回即迁移：brief.py --set 之后只留统一字段
        r = subprocess.run([PY, str(HERE / "brief.py"), "--project", str(legacy),
                            "--set", "audience=装修业主",
                            "--set", "audience_present_state=进口一定比国产好",
                            "--set", "change=按场景选", "--set", "length=45",
                            "--set", "style=talking-head-v1"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"brief.py --set 失败：{r.stderr[-300:]}"
        saved = json.loads((legacy / "brief.json").read_text())
        assert saved.get("audience_present_state") == "进口一定比国产好"
        assert "belief_now" not in saved, f"写回后旧字段还在：{sorted(saved)}"
        print("  ✅ 老数据写回即迁移到 audience_present_state")

        # 6) brief.py 零环境自足：-S（不挂 site-packages）也要能跑，pyyaml 走 scripts/vendor
        r = subprocess.run([PY, "-S", str(HERE / "brief.py"), "--project", str(legacy), "--show"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"brief.py 没 vendor 兜底，缺 pyyaml 就崩：{(r.stderr or '')[-300:]}"
        print("  ✅ brief.py 无 site-packages（-S）也能跑：pyyaml 走 scripts/vendor")


def main():
    print("▶ 开始运行前 3 秒策略真判定与钩子库出处回归测试...")
    test_samples()
    test_cli_and_persistence()
    print("\n▶ 钩子库出处可追溯（原文锚点校验 + audience_present_state 闭环）")
    test_eight_points()
    test_self_built_marked()
    test_source_anchors()
    test_present_state_loop()
    print("✅ 全部 7 组策略正负样本与持久化回归 + 4 组出处锚点/字段闭环通过！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
