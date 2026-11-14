#!/usr/bin/env python3
"""CutSkills · 上游选题、高网感脚本与小白拍片卡生成器（飞书双流向内容系统）。

衔接两条创作路径：
1. 「我只有一个模糊想法 / 行业身份」→ 生成 3 套双流向选题提案（A1 漏斗破圈 / A2 人设信任 / A3 痛点转化）
   + 每套 3 个黄金 3 秒钩子候选 + 逐句口播脚本 + 《小白 10 分钟拍片与素材准备卡》（shoot-guide.md）
2. 「不拍实拍，直接用 AI 配音 + 代码画面出成片」→ `--to-plan` 把 `ideation.json` 一键转成 `brief.json` + `plan.json`

AI Agent 在对话中也可以直接按本脚本的 `ideation.json` 结构写入量身定制的脚本，再跑 `--guide` 或 `--to-plan`。

用法：
  ideate.py --project <项目目录> --topic "<一句话想法/行业>" [--persona "<人设/身份>"] [--audience "<目标受众>"] [--duration 45]
  ideate.py --project <项目目录> --pick 1 --guide
  ideate.py --project <项目目录> --to-plan [--vo-asset <配音素材ID>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 三次拍板（BRIEF「交互约束」）
# 一条片最多打断用户 3 次：① 确认选题 ② 确认拍摄任务卡或素材 ③ 确认锁定音轨与低清预览。
# 这三次的留痕**只有一个文件**：<项目>/approval.json（plan_card.py 签发拍板③时也写它）。
# 结构（顶层四格是拍板③的老字段，保持不动，旧项目读得懂）：
#   {approved, at, note, source,                 # 拍板③（lock.json 的有效性由 lock.py 判）
#    gates:  {"1-topic"|"2-shots"|"3-cut": {gate, ok, at, source, note, where, detail}},
#    asks:   [ {gate, where, question, at} ]}     # 全流程「停下来问用户」的登记处
# `asks` 是给测试做机械计数用的：一条片跑下来不同的 gate 只许出现 3 个。
APPROVAL_FILE = "approval.json"
GATE_TOPIC = "1-topic"          # 确认选题（三套提案里选哪套 / 要不要换钩子）
GATE_SHOTS = "2-shots"          # 确认拍摄任务卡，或确认素材盘点结果
GATE_CUT = "3-cut"              # 确认锁定音轨 + 低清粗剪预览（plan_card.py --approve）
REQUIRED_GATES = (GATE_TOPIC, GATE_SHOTS, GATE_CUT)
INTERRUPT_BUDGET = len(REQUIRED_GATES)
GATE_LABEL = {GATE_TOPIC: "确认选题", GATE_SHOTS: "确认拍摄卡或素材",
              GATE_CUT: "确认锁定音轨与低清预览"}
# 拍摄卡里「这一镜」的写法：第3镜 / 3镜 / #3 —— 用户改哪几镜要靠它认出来
SHOT_MENTION = re.compile(r"(?:第\s*)?(\d+)\s*镜|#\s*(\d+)")
CLIP_SPEC = re.compile(r"^(?P<asset>.+?):(?P<a>\d+(?:\.\d+)?)-(?P<b>\d+(?:\.\d+)?)$")


def load_approval(proj: Path) -> dict:
    """读 <项目>/approval.json。读不出来就当空的读——但绝不因此把旧内容抹掉（写入走 save_approval）。"""
    try:
        doc = json.loads((proj / APPROVAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_approval(proj: Path, doc: dict) -> Path:
    p = proj / APPROVAL_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def note_interrupt(proj: Path, gate: str, where: str, question: str = "") -> dict:
    """登记一次「停下来问用户」。**必经的打断只有三个 gate**，多一个这里直接炸。

    `where` 是这条问句出在哪个入口（`ideate --pick` / `brief 交互四格` …），同一个入口重复跑
    只留一行——计数看的是「这条片子问了几次」，不是「脚本跑了几遍」。
    """
    if gate not in REQUIRED_GATES:
        raise ValueError(f"第 4 个必经打断点「{gate}」不许加：BRIEF 只给三次拍板，"
                         f"其余一律走默认值并在交付时列进「我替你做的决定」")
    doc = load_approval(proj)
    asks = doc.setdefault("asks", [])
    row = {"gate": gate, "where": where, "question": question,
           "at": datetime.now().isoformat(timespec="seconds")}
    if not any(a.get("gate") == gate and a.get("where") == where for a in asks):
        asks.append(row)
        save_approval(proj, doc)
    return row


def record_gate(proj: Path, gate: str, *, note: str, source: str = "human",
                detail: dict | None = None, where: str = "") -> dict:
    """把「用户确认了这一拍」写进 approval.json 的 gates。<只加这一键，顶层老字段不动。"""
    if gate not in REQUIRED_GATES:
        raise ValueError(f"不认识的拍板「{gate}」：只认 {REQUIRED_GATES}")
    doc = load_approval(proj)
    rec = {"gate": gate, "ok": True, "at": datetime.now().isoformat(timespec="seconds"),
           "source": source, "note": note, "where": where, "detail": detail or {}}
    doc.setdefault("gates", {})[gate] = rec
    save_approval(proj, doc)
    return rec


def sha256_of(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _needs_filming(idea: dict) -> bool:
    """这套脚本里有没有「要真拍」的镜（visual_mode 不是纯 gen）。有就必须有拍摄卡。"""
    sel = int((idea or {}).get("selected_proposal") or 1)
    props = (idea or {}).get("proposals") or []
    prop = next((p for p in props if p.get("index") == sel), props[0] if props else {})
    for b in (prop.get("beats") or []):
        vm = str(b.get("visual_mode") or "talking")
        if vm != "gen":
            return True
    return False


def shots_object(proj: Path) -> dict | None:
    """拍板② 的确认对象是哪一类：拍摄任务卡 / 素材盘点 / 无对象（零实拍且没带素材）。

    「有没有对象」不是猜的：
      · 项目里有 `shoot-guide.md` → 就是这个卡；
      · `ideation.json` 带 `from_inventory.library` → 是那份素材盘点结果；
      · 没有卡，但这套脚本里有「要真拍」的镜 → 卡还没出/被删了（`gone`，仍算欠拍板）；
      · 零实拍纯代码画面、也没盘素材 → 没有要确认的东西（返回 None，不当成「缺」）。
    最后这一层很关键：不能靠「把卡删了」混过拍板②。
    """
    guide = proj / "shoot-guide.md"
    if guide.exists():
        return {"kind": "shoot-card", "path": str(guide), "sha": sha256_of(guide)}
    try:
        idea = json.loads((proj / "ideation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lib = str(((idea or {}).get("from_inventory") or {}).get("library") or "").strip()
    if lib:
        for cand in (Path(lib) / "_cutskills" / "inventory.json", Path(lib) / "inventory.json"):
            if cand.exists():
                return {"kind": "inventory", "path": str(cand), "sha": sha256_of(cand)}
        return {"kind": "inventory", "path": str(Path(lib) / "_cutskills" / "inventory.json"),
                "sha": "", "gone": True}
    if _needs_filming(idea):
        return {"kind": "shoot-card", "path": str(guide), "sha": "", "gone": True}
    return None


def gate_state(proj: Path) -> dict[str, dict]:
    """三次拍板各自「留痕了没有 / 那一刻之后改过没有」。只读，不动任何文件。

    下游（render_hf 的渲前闸门、plan_card 的方案卡）只看这一份判断，不各自另判一套。
    """
    gates = load_approval(proj).get("gates") or {}
    out: dict[str, dict] = {}

    t = gates.get(GATE_TOPIC) or {}
    out[GATE_TOPIC] = {
        "gate": GATE_TOPIC, "label": GATE_LABEL[GATE_TOPIC], "ok": bool(t.get("ok")),
        "at": t.get("at"), "note": t.get("note"), "where": t.get("where"),
        "object": "topic",
        "why": "" if t.get("ok") else "选题还没被确认过（`ideate.py --project … --pick 1` 就是确认动作）",
    }

    obj = shots_object(proj)
    r = gates.get(GATE_SHOTS) or {}
    det = r.get("detail") or {}
    kind_cn = "拍摄任务卡" if (obj or {}).get("kind") == "shoot-card" else "素材盘点结果"
    if obj is None:
        ok, why = True, ""
        note = "这条片没有拍摄卡也没有素材盘点：拍板② 无对象（零实拍纯代码画面）"
    elif not r.get("ok"):
        ok, why = False, (f"{kind_cn}还没被确认过"
                          f"（`ideate.py --project … "
                          f"{'--approve-shots' if obj['kind'] == 'shoot-card' else '--approve-material'}"
                          f" \"认可\"`）")
        note = ""
    elif det.get("kind") != obj["kind"]:
        ok, why = False, (f"确认过的是{det.get('kind') or '别的对象'}，现在要确认的是{kind_cn}")
        note = ""
    elif obj.get("gone"):
        ok, why = False, (f"{kind_cn}不在了（{obj['path']}）："
                          + ("先跑 `ideate.py --project … --guide` 出卡，再 --approve-shots"
                             if obj["kind"] == "shoot-card"
                             else "用 `--inventory <新的 inventory.json>` 重新确认，或重跑 inventory.py"))
        note = ""
    elif det.get("sha") != obj["sha"]:
        ok, why = False, ("确认之后这张卡/这份盘点又改过（指纹 "
                          f"{str(det.get('sha'))[:12]} → {obj['sha'][:12]}），要重新确认")
        note = ""
    else:
        ok, why = True, ""
        note = r.get("note") or ""
    out[GATE_SHOTS] = {
        "gate": GATE_SHOTS, "label": GATE_LABEL[GATE_SHOTS], "ok": ok,
        "at": r.get("at"), "note": note, "where": r.get("where"),
        "object": (obj or {}).get("kind") or "none",
        "object_path": (obj or {}).get("path"), "object_sha": (obj or {}).get("sha"),
        "changes": list(det.get("changes") or []),
        "excluded": list(det.get("excluded") or []),
        "why": why,
    }

    lock_p = proj / "lock.json"
    doc_lock = {}
    try:
        doc_lock = json.loads(lock_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc_lock = {}
    cut_ok = False
    if lock_p.exists() and doc_lock:
        # 锁的有效性只有一份判据：lock.py（sha256 与当前 timeline.json 对不）。这里调用它，不另写。
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import lock as _lockmod
            cut_ok = _lockmod.verify(proj)[0] == 0
        except Exception:                                   # noqa: BLE001
            cut_ok = False
    out[GATE_CUT] = {
        "gate": GATE_CUT, "label": GATE_LABEL[GATE_CUT], "ok": cut_ok,
        "at": (gates.get(GATE_CUT) or {}).get("at") or doc_lock.get("locked_at"),
        "note": (gates.get(GATE_CUT) or {}).get("note") or doc_lock.get("note"),
        "where": (gates.get(GATE_CUT) or {}).get("where"),
        "object": "cut", "audio_fp": doc_lock.get("audio_fp"),
        "why": "" if cut_ok else "还没签锁（`plan_card.py --project … --approve \"认可\"`）",
    }
    return out


def open_gates(proj: Path) -> list[dict]:
    """还没拍板、或者拍板后又被改掉的拍板（下游据此拦或提示）。"""
    return [st for st in gate_state(proj).values() if not st.get("ok")]

# ---------------------------------------------------------------- 出处引用
# BRIEF 的硬要求：生成选题/文案必须写明「用了原文哪一节、哪个公式或切入点」。
# 所以出处不是手写备注，是**能被程序校验的结构**：
#   sec    原文的章节标题路径（逐级对上 references/content-system.md 的真实标题）
#   group  该节里的分组标题（如「开篇钩子八大切入点」），可选
#   key    具体的切入点名 / 招式名 / 结构名
# 校验在 scripts/test_hook_source.py：拿原文标题当锚点，逐级断言，不硬编码白名单。


def _src(sec: tuple[str, ...], key: str, group: str | None = None) -> dict:
    return {"sec": sec, "group": group, "key": key}


def _ref_text(ref: dict) -> str:
    """一条引用的人读串：原文『内容创作』一节 · 水桶流视频 · 开篇钩子八大切入点 · 人群切入。"""
    sec, group, key = ref["sec"], ref.get("group"), ref["key"]
    out = f"原文『{sec[0]}』一节"
    if len(sec) > 1:
        out += " · " + " · ".join(sec[1:])
    if group:
        out += f" · {group}"
    return f"{out} · {key}"


def hook_source(hook: dict) -> str:
    """钩子出处。原文条目给人读的引用串；自建条目必须写死「自建扩展，非原文」，
    并附它借的那一式（basis），不许含糊带过。"""
    s = hook.get("source") or {}
    if s.get("self_built"):
        out = "自建扩展，非原文"
        if s.get("basis"):
            out += f"（{_ref_text(s['basis'])} 的具体化）"
        return out
    return _ref_text(s)


# ---------------------------------------------------------------- 钩子库
# 每条钩子只说一件事、只挂一个出处。原文『内容创作』一节 · 水桶流视频 · 开篇钩子八大切入点
# 八条全部显式实现（H01–H08），原文『文案框架内容』一节 · 开篇技巧的招式另列（H09–H13），
# 原文没有的式子（H14/H15）一律 self_built，source 写「自建扩展，非原文」。
# 旧库里的 H02「反常识颠覆」= 颠覆切入 + 好奇切入、H05「揭秘行业猫腻」= 制造敌人 + 内幕话题（话题不是钩子）、
# H07「痛点直球疑问」= 痛点切入 + 疑问切入，三条混捏的都已拆成一条一件事。
_POINT = "开篇钩子八大切入点"
_CT = ("内容创作", "水桶流视频")
_KT = ("文案框架内容", "开篇技巧")

HOOK_BANK = {
    # —— 原文『内容创作』· 水桶流视频 · 开篇钩子八大切入点（八条，一条不少）
    "H01": {"name": "人群切入",
            "source": _src(_CT, "人群切入", group=_POINT),
            "formula": "身份标签+应该做什么/注意什么/揭秘+核心话题/承诺",
            "tpl": "{crowd}注意了：{core}这件事，先看这三处，再谈钱。"},
    "H02": {"name": "痛点切入",
            "source": _src(_CT, "痛点切入", group=_POINT),
            "formula": "你是不是遇到过（痛点场景）+糟糕的感受/结果",
            "tpl": "你是不是也遇到过：{core}报价看着差不多，做完全返工，钱花了还窝一肚子气？"},
    "H03": {"name": "效果切入",
            "source": _src(_CT, "效果切入", group=_POINT),
            "formula": "方法/产品+时间/条件+惊人效果/承诺",
            "tpl": "只要在{core}上多花半天，三个月后它比隔壁那家还耐看。"},
    "H04": {"name": "情绪切入",
            "source": _src(_CT, "情绪切入", group=_POINT),
            "formula": "情绪/感受词+引发情绪的事件/对象+解决方案/共鸣点",
            "tpl": "最气人的不是{core}贵，是钱花了还被人当外行糊弄。"},
    "H05": {"name": "好奇切入",
            "source": _src(_CT, "好奇切入", group=_POINT),
            "formula": "反常识/反认知的结论+暗示有内幕/原因",
            "tpl": "做{core}真正的成本，从来不在你看到的那张报价单上。"},
    "H06": {"name": "身份推荐切入",
            "source": _src(_CT, "身份推荐切入", group=_POINT),
            "formula": "权威/资深/亲临者身份+时间/经验+自我总结/教你/揭秘+价值点",
            "tpl": "{persona}说句得罪同行的大实话：{core}只看这三处就够了。"},
    "H07": {"name": "颠覆切入",
            "source": _src(_CT, "颠覆切入", group=_POINT),
            "formula": "开篇第一句=先说行业常识，其实/但颠倒他",
            "tpl": "都说{core}越贵越好，其实恰恰相反——多花的钱常花在你根本看不见的地方。"},
    "H08": {"name": "疑问切入",
            "source": _src(_CT, "疑问切入", group=_POINT),
            "formula": "为什么/怎么样+一个普遍存在的难题/现象",
            "tpl": "为什么别人做的{core}越看越耐看，你一上手就踩雷？"},
    # —— 原文『文案框架内容』· 开篇技巧（十式里挑出本仓脚本真用得上的五式）
    "H09": {"name": "不字开头",
            "source": _src(_KT, "不字开头"),
            "formula": "不+要做的事+就等于+后果（对标原文例句「云南旅游不涂防晒一天老十岁」）",
            "tpl": "千万别在没搞懂{core}之前，随便花冤枉钱！"},
    "H10": {"name": "做嘴替",
            "source": _src(_KT, "做嘴替"),
            "formula": "把观众心里那句话替他说出来+替他解释为什么会这样",
            "tpl": "你不是{core}做不好，是根本没人把这三处给你讲清楚。"},
    "H11": {"name": "制造敌人",
            "source": _src(_KT, "制造敌人"),
            "formula": "有粉丝说/有人这样说（敌意来源）+我今天必须证明一下",
            "tpl": "有人说{core}贵了就是被坑，今天我必须当场证明一下。"},
    "H12": {"name": "冲突开篇",
            "source": _src(_KT, "冲突开篇"),
            "formula": "明明只是做A，我却为了B花了大代价（两件事互相打架）",
            "tpl": "明明只是做{core}，我光选料就选了大半年——同行都说我疯了。"},
    "H13": {"name": "数字开篇",
            "source": _src(_KT, "数字开篇"),
            "formula": "把几个硬数字砸在第一句（对标原文例句「200w 600平 60网友等我们开店」）",
            "tpl": "报价差三成，{core}最后多花了整整一倍，这笔账你算过吗？"},
    # —— 原文没有的式子：标自建，并指明借的是哪一式的具体化
    "H14": {"name": "算清成本明账",
            "source": {"self_built": True, "basis": _src(_KT, "数字开篇"),
                       "note": "原文无成本算账钩子；本式是「数字开篇」的具体化——同样用数字开场，"
                               "但把数字限定成成本账（表面价 vs 长期账）"},
            "formula": "成本拆成 A/B 两半 + 两半各报一个数 + 一句该把钱花在哪儿的判断",
            "tpl": "把{core}的成本拆成两半看，你就知道钱到底该花在哪儿了。"},
    "H15": {"name": "结果倒叙吸睛",
            "source": {"self_built": True,
                       "basis": _src(("文案框架内容", "中间内容"), "结果+原因+为什么", group="结构框架"),
                       "note": "原文无倒叙钩子；本式是中间结构「结果+原因+为什么」的倒装——"
                               "先甩结果不解释，原因留到片中再揭"},
            "formula": "小改动 + 大结果（结果先给、不解释，原因留到后面）+ 一个具体到手的细节",
            "tpl": "只改了两个小细节，{core}的质感直接上了一个台阶。"},
}

# 三条流的脚本结构：名字回原文『文案框架内容』一节 · 中间内容（3 框架 + 4 手法）。
# 旧稿那三套「XX式」结构名（观点锤击 / 故事反转 / 成本算账 那一类）是二手改写，原文里没有，全部改回原名；
# 四拍施工顺序是本仓自建的，单独写进 structure_note 标「自建扩展，非原文」，不冒充原文。
_MID = ("文案框架内容", "中间内容")

STREAMS = [
    {
        "id": "funnel-A1",
        "stream": "funnel",
        "crowd": "A1 泛流量破圈（占 50%）",
        "label": "漏斗流 · 反常识避坑（高播放拉新）",
        "structure": "观点+原因+案例+观点",
        "structure_source": _src(_MID, "观点+原因+案例+观点", group="结构框架"),
        "method": "案例式",
        "method_source": _src(_MID, "案例式"),
        "structure_note": "四拍编排（3秒钩子 → 反常识结论 → 直观对比 → 口诀收尾）为自建扩展，非原文",
        "hooks": ["H07", "H05", "H01"],
        "pack": "P1-warm",
        "plan": "S1-conclude-fast",
        "bgm_mood": "upbeat",
    },
    {
        "id": "bucket-A2",
        "stream": "bucket_persona",
        "crowd": "A2 人设与信任（占 30%）",
        "label": "水桶流 · 内行死磕细节（立专业人设）",
        "structure": "人格化表达",
        "structure_source": _src(_MID, "人格化表达", group="结构框架"),
        "method": "反转式",
        "method_source": _src(_MID, "反转式"),
        "structure_note": "四拍编排（现场冲突 → 外行凑合做法 → 我们的死磕标准 → 态度金句）为自建扩展，非原文",
        "hooks": ["H12", "H06", "H04"],
        "pack": "P4-canton-warm",
        "plan": "S2-live-mid",
        "bgm_mood": "warm",
    },
    {
        "id": "bucket-A3",
        "stream": "bucket_convert",
        "crowd": "A3 垂直意向转化（占 20%）",
        "label": "水桶流 · 算清成本明账（高收藏高咨询）",
        "structure": "结果+原因+为什么",
        "structure_source": _src(_MID, "结果+原因+为什么", group="结构框架"),
        "method": "提问式",
        "method_source": _src(_MID, "提问式"),
        "structure_note": "算账编排（表面价 vs 长期账 → 自测法 → 互动收尾）为自建扩展，非原文",
        "hooks": ["H13", "H14", "H02"],
        "pack": "P2-paper",
        "plan": "S1-conclude-fast",
        "bgm_mood": "clear",
    },
]


def _extract_core(topic: str) -> str:
    t = re.sub(r"^(我想做|帮我做|帮我|关于|拍一条|做一条|聊聊|讲讲)", "", topic.strip())
    t = re.sub(r"(的视频|短视频|口播|脚本|选题|讲讲|聊聊)$", "", t).strip(" ，。！？、")
    if len(t) <= 12:
        return t or "这件事"
    # 优先在最后一个「的」后取中心语短语（至少 6 字），最贴钩子语感：「…的茶楼 → 茶楼」不够短则退回尾部切片；
    # 切片起点不许落在数字串中间：「用300万打造600平…」截成「打造6」是钩子里的硬伤。
    p = t.rfind("的")
    if p != -1 and len(t) - p - 1 >= 6:
        return t[p + 1:].strip(" ，。！？、")
    start = len(t) - 12
    if start > 0 and t[start].isdigit() and t[start - 1].isdigit():
        while start < len(t) and t[start].isdigit():
            start += 1          # 跳过被砍剩的半个数字串
    return t[start:].strip(" ，。！？、的了与和") or t[:12]


def build_proposals(topic: str, persona: str, audience: str, duration_s: int) -> dict:
    core = _extract_core(topic)
    who = persona.strip() or "干了一线多年的内行人"
    aud = audience.strip() or f"关心「{core}」但怕踩坑的普通人"

    proposals = []
    for idx, st in enumerate(STREAMS, 1):
        hooks = [
            {"code": c, "type": HOOK_BANK[c]["name"],
             "line": HOOK_BANK[c]["tpl"].format(core=core, persona=who, crowd=aud),
             "formula": HOOK_BANK[c]["formula"],
             "source": hook_source(HOOK_BANK[c]),
             "self_built": bool((HOOK_BANK[c].get("source") or {}).get("self_built")),
             "note": (HOOK_BANK[c].get("source") or {}).get("note")}
            for c in st["hooks"]
        ]
        # 第 1 段念的就是钩子：出处跟着钩子走，人在拍片卡里能直接看到用了原文哪一条
        lead = {"line": hooks[0]["line"], "type": hooks[0]["type"],
                "source": hooks[0]["source"], "formula": hooks[0]["formula"]}
        if st["stream"] == "funnel":
            beats = [
                {
                    "n": 1, "role": "setup", "phase": "黄金3秒钩子", "dur_s": 3.2,
                    "line": lead["line"],
                    "hook_type": lead["type"], "hook_source": lead["source"],
                    "hook_formula": lead["formula"],
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "对着镜头直接说第一句，眼神笃定，前0.5秒别低头看稿",
                    "code_fallback": {"template": "keyword", "props": {"text": f"别盲目做{_short_core(core, 5)}", "sub": "大多数人第一步就错了"}},
                    "sticker": {"id": "bang", "pos": "top-right"},
                    "vfx": "punch_zoom",
                    "sfx": "impact",
                },
                {
                    "n": 2, "role": "claim", "phase": "反常识核心结论", "dur_s": 6.5,
                    "line": f"真正决定{core}成败的，从来不是最贵的那一层表面功夫，而是看不见的底层细节。",
                    "visual_mode": "talking",
                    "shoot_tip": "继续面对窗户自然光说话，手势自然比一个『1』",
                    "card": {"kind": "keyword", "text": "看不见的底层细节", "on": "底层细节"},
                    "sfx": "pop",
                },
                {
                    "n": 3, "role": "evidence", "phase": "直观对比举证", "dur_s": 9.0,
                    "line": f"你看普通做法图省事，看着差不多，过半年边角全露馅；把基础工序做扎实，用五年还是稳稳当当。",
                    "visual_mode": "broll_or_gen",
                    "shoot_tip": "手机贴近拍5秒钟手头实物细节（不用说话，手稳住数5秒）；没实物直接免拍，由代码画对比卡",
                    "code_fallback": {
                        "template": "compare",
                        "props": {
                            "title": f"{core[:6]}两种做法",
                            "left": {"title": "图省事做法", "value": "半年易返工"},
                            "right": {"title": "扎实工序", "value": "五年稳当"},
                            "note": "差在看不见的基础",
                        },
                    },
                    "sticker": {"id": "vs-fire", "pos": "top-left"},
                    "sfx": "whoosh",
                },
                {
                    "n": 4, "role": "payoff", "phase": "口诀金句收尾", "dur_s": 5.5,
                    "line": f"记住一句话：面子决定第一眼，底子才决定能走多远。换作是你，你会怎么选？",
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "微笑收尾，说完最后一字保持看镜头1秒再按停",
                    "code_fallback": {"template": "quote", "props": {"text": "面子管第一眼，底子管走多远", "who": who[:8]}},
                    "sticker": {"id": "check-glow", "pos": "bottom-right"},
                    "sfx": "ding",
                },
            ]
            title_bar = {"top": f"做{_short_core(core)}前必看", "main": "大多数人踩过的隐形坑"}
        elif st["stream"] == "bucket_persona":
            beats = [
                {
                    "n": 1, "role": "setup", "phase": "现场冲突钩子", "dur_s": 3.4,
                    "line": lead["line"],
                    "hook_type": lead["type"], "hook_source": lead["source"],
                    "hook_formula": lead["formula"],
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "手里拿一件干活的真家伙（样品/工具/成品）直接开口",
                    "code_fallback": {"template": "keyword", "props": {"text": "内行只盯三处", "sub": f"{who[:8]}实话"}},
                    "sticker": {"id": "eye-alert", "pos": "top-right"},
                    "vfx": "punch_zoom",
                    "sfx": "impact",
                },
                {
                    "n": 2, "role": "turn", "phase": "行业凑合现象", "dur_s": 7.0,
                    "line": f"很多人劝我，{core}差不多就行了，反正外行第一眼根本看不出来。",
                    "visual_mode": "talking",
                    "shoot_tip": "原位说话，语气带一点无奈但坚定",
                    "card": {"kind": "keyword", "text": "外行第一眼看不出", "on": "看不出来"},
                    "sfx": "swish",
                },
                {
                    "n": 3, "role": "evidence", "phase": "死磕三步标准", "dur_s": 9.5,
                    "line": "但我们偏不凑合：第一步先验底，第二步卡准收边，第三步逐处复检，少一步都不交活。",
                    "visual_mode": "gen",
                    "shoot_tip": "【免拍】这一段自动用代码生成三步流程动效卡",
                    "code_fallback": {
                        "template": "steps",
                        "props": {"title": "死磕交付三步", "items": ["第一步：严验基础", "第二步：卡准细节", "第三步：逐项复检"]},
                    },
                    "sfx": "pop",
                },
                {
                    "n": 4, "role": "payoff", "phase": "人设态度收尾", "dur_s": 5.5,
                    "line": f"慢一点没关系，睡得踏实比赚快钱重要。我是{who[:6]}，下期见。",
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "点头致意，留1秒干净收尾",
                    "code_fallback": {"template": "quote", "props": {"text": "睡得踏实比赚快钱重要", "who": who[:8]}},
                    "sticker": {"id": "thumb-up", "pos": "bottom-right"},
                    "sfx": "shimmer",
                },
            ]
            title_bar = {"top": f"{who[:8]}真话", "main": f"{_short_core(core)}死磕标准"}
        else:
            beats = [
                {
                    "n": 1, "role": "setup", "phase": "数字反差钩子", "dur_s": 3.4,
                    "line": lead["line"],
                    "hook_type": lead["type"], "hook_source": lead["source"],
                    "hook_formula": lead["formula"],
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "伸出两根手指做对比状，第一句干脆利落",
                    "code_fallback": {"template": "number", "props": {"value": "几倍", "unit": "差距", "label": f"{core[:6]}明暗账"}},
                    "sticker": {"id": "money-fly", "pos": "top-right"},
                    "vfx": "punch_zoom",
                    "sfx": "money",
                },
                {
                    "n": 2, "role": "claim", "phase": "拆开表面价与长期账", "dur_s": 8.0,
                    "line": f"表面省下两成预算，返工一次全赔进去；把预算花在核心材料上，长期算下来反而省了一半。",
                    "visual_mode": "gen",
                    "shoot_tip": "【免拍】这一段自动用代码生成物理天平对比动效",
                    "code_fallback": {
                        "template": "balance",
                        "props": {
                            "left": {"title": "好料一步到位", "value": "省心耐用"},
                            "right": {"title": "贪便宜返工", "value": "越拖越贵"},
                            "note": "算总账别只看单价",
                        },
                    },
                    "sfx": "impact",
                },
                {
                    "n": 3, "role": "warrant", "phase": "给观众一招自测法", "dur_s": 7.5,
                    "line": f"教你一招最简单的判断法：别听嘴上吹什么概念，直接让他拿实物小样现场比对。",
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "对镜头支招，像跟老朋友私底下聊天一样自然",
                    "card": {"kind": "keyword", "text": "直接拿实物小样", "on": "实物小样"},
                    "sticker": {"id": "bulb-idea", "pos": "top-left"},
                    "sfx": "pop",
                },
                {
                    "n": 4, "role": "payoff", "phase": "互动提问收尾", "dur_s": 5.0,
                    "line": f"你最近在看{core}吗？遇到拿不准的发在评论区，我帮你避坑。",
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "手指向下方评论区，微笑停顿1秒结束",
                    "code_fallback": {"template": "quote", "props": {"text": "拿不准发评论区，帮你把关", "who": who[:8]}},
                    "sticker": {"id": "sparkle-star", "pos": "bottom-right"},
                    "sfx": "ding",
                },
            ]
            title_bar = {"top": "内行帮你算笔账", "main": f"{_short_core(core)}怎么花钱最值"}

        proposals.append({
            "index": idx,
            "id": st["id"],
            "crowd": st["crowd"],
            "label": st["label"],
            "structure": st["structure"],
            "structure_source": _ref_text(st["structure_source"]),
            "method": st["method"],
            "method_source": _ref_text(st["method_source"]),
            "structure_note": st["structure_note"],
            "pack": st["pack"],
            "plan": st["plan"],
            "bgm_mood": st["bgm_mood"],
            "title_bar": title_bar,
            "hooks": hooks,
            "selected_hook": 0,
            "beats": beats,
        })

    return {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "topic_raw": topic,
        "core": core,
        "persona": who,
        "audience": aud,
        "target_duration_s": duration_s,
        "follow_up_questions": [
            # 先给猜测、再让人一句话改：开放题（「客户最常说什么？」）没人愿意答
            f"我猜客户最常问的是「{core}到底多少钱」，对吗？不对就把他真实的原话发我（换进去钩子更准）",
            "我猜你手上有一次返工或者被坑的例子，对吗？有就发我一句：返了多少钱、隔了多久",
        ],
        "selected_proposal": 1,
        "proposals": proposals,
    }


# ---------------------------------------------------------------- 拍摄任务卡（六项字段）
# BRIEF 硬要求：每条视频一个镜头一张卡，写清六项——拍什么 / 机位（高度与角度）/ 景别 /
# 时长 / 备份条数 / 手机拍摄要点。最容易糊弄的一处是「景别」：整张卡都写「中景」，
# 人照着拍出来就是一串长得一样的画面。所以景别按**这句台词在说什么 + 这镜拍不拍人**判：
#   · 这镜是 B-roll 且台词点到手头实物/细节（手、样品、边角、打磨…）→ 手部特写，俯拍 45°；
#   · 这镜是 B-roll 且台词在交代场地/环境（现场、店面、整体、空间…）→ 全景，退 3 米；
#   · 钩子与收尾（情绪最重、观众要看你的脸）→ 近景，平视；
#   · 其余口播 → 中近景，手机略高于眼睛 5–10°。
# 口播镜永远拍人：台词里提到「材料/细节」不代表要把镜头怼到手上，那只是嘴上说的内容。
DETAIL_KEYS = ("手", "实物", "样品", "小样", "边角", "细节", "材料", "工具", "做工",
               "收边", "打磨", "表面", "截面", "拉手", "五金", "工艺", "样板")
WIDE_KEYS = ("现场", "店面", "店里", "环境", "工地", "全景", "整体", "一排", "整条街",
             "空间", "户型", "房间", "车间", "展厅")

SHOT_SIZES = {"close": "近景", "medium": "中近景", "detail": "手部特写",
              "wide": "全景", "screen": "免拍（代码画面）"}

# 手机拍摄要点：每类镜头三条，都是「不这么做就一定翻车」的那三条，不写正确但没用的话。
PHONE_TIPS = {
    "close": ("擦镜头，面朝窗户站，别背光（背光人脸会黑）",
              "手机与眼睛同高，离一臂（约 60cm），别贴太近变形",
              "念完最后一字保持看镜头 1 秒再按停"),
    "medium": ("手机比眼睛高 5–10°，微微俯拍，脸更精神",
               "胸口以上入画，头顶留一点空（别切到头发）",
               "念错别关机，深吸一口气只把这一句重念一遍"),
    "detail": ("手机端稳或顶在桌沿，别悬空举着（一抖就废）",
               "离实物 20–30cm，别用数码变焦（放大就糊）",
               "默数 5 秒不动；侧面来光才看得清纹理"),
    "wide": ("后退到 3 米左右，手机端平，别仰拍",
             "人站在三分线上，四周留出环境，别把人拍满",
             "走位慢一点，宁可多拍一条也别晃"),
    "screen": ("这一镜不用拍：画面由代码动效自动生成",),
}

# 机位：高度 + 角度，一句话能照着做（写「平视」不够，要说手机放多高、离多远）。
CAMERA_OF = {
    "close": "平视 0°｜手机与眼睛同高，离你一臂（约 0.6m）",
    "medium": "略俯 5–10°｜手机比眼睛高半个头，离约 0.8–1m",
    "detail": "俯拍 45°｜手机举到实物上方 20–30cm，正对着拍",
    "wide": "平视 0°｜手机端平在胸口高，后退约 3m",
    "screen": "—（免拍，无实拍机位）",
}

FRAME_NOTE = {
    "close": "人是主体：头顶留 1/10 空，眼睛落在上三分线",
    "medium": "胸口以上入画：头顶留一点空，两侧别切肩",
    "detail": "手和实物摆画面正中：别出框、别切到指头",
    "wide": "人站三分线上，四周留出环境交代场地",
    "screen": "本镜不用拍：代码画面自动生成",
}

# 机位高度/俯仰/距离的数值（画简笔示意用）。跟 CAMERA_OF 的文字必须对得上。
CAM_HEIGHT_OF = {"close": "eye", "medium": "high", "detail": "high",
                 "wide": "chest", "screen": "eye"}
CAM_ANGLE_OF = {"close": 0, "medium": 8, "detail": 45, "wide": 0, "screen": 0}
CAM_DIST_OF = {"close": 0.6, "medium": 0.9, "detail": 0.3, "wide": 3.0}


def _shot_size(line: str, role: str, visual_mode: str) -> tuple[str, str]:
    """→ (size_key, 中文景别)。B-roll 看台词内容，口播看段落在脚本里的位置。"""
    vm = visual_mode or "talking"
    if vm.startswith("broll"):
        if any(k in line for k in WIDE_KEYS):
            return "wide", SHOT_SIZES["wide"]
        if any(k in line for k in DETAIL_KEYS):
            return "detail", SHOT_SIZES["detail"]
        return "detail", SHOT_SIZES["detail"]      # B-roll 没有明确指向时，手头实物最稳
    if role in ("setup", "hook", "payoff", "close"):
        return "close", SHOT_SIZES["close"]
    return "medium", SHOT_SIZES["medium"]


def _takes_of(role: str, size_key: str) -> tuple[int, str]:
    """备份条数的规则依据（不是随手写个 2）：

    · 一句话一个 take 的**下限是 2 条**：一条正式 + 一条保险。手机拍摄最常见的废片原因
      是嘴瓢和眼神飘，2 条里挑 1 条的成功率约七成，3 条约九成；
    · 钩子/首句（setup/hook）**3 条**：前 3 秒决定完播，丢了就全丢；而且这句最短，
      多拍一条的成本是整条片子最低的；
    · 关键举证镜头（手部特写/实物细节）**3 条**：手稳、对焦、光线三个变量要同时对，
      2 条经常不够选；
    · 收尾/中段口播 **2 条**；免拍镜头 **0 条**（不用拍）。
    """
    if size_key == "screen":
        return 0, "免拍：代码画面承载，不用拍"
    if role in ("setup", "hook"):
        return 3, "钩子：3 条（前 3 秒决定完播，这句最短，多拍一条最便宜）"
    if size_key == "detail":
        return 3, "关键举证：3 条（手稳/对焦/光线三个变量都对上才算一条）"
    return 2, "口播下限：2 条（一条正式、一条保险）"


def shot_plan(beat: dict, who: str = "") -> dict:
    """一句台词 → 一个镜头（拍摄任务卡的一行 + 一张简笔示意的参数）。

    六项字段一次给全：下游 `render_shoot_guide` 只负责排版、`shoot_diagram` 只负责画，
    两边都不再自己判断——判据只有这一处。
    """
    line = str(beat.get("line") or "")
    role = str(beat.get("role") or "claim")
    vm = str(beat.get("visual_mode") or "talking")
    dur = round(float(beat.get("dur_s") or max(2.8, len(line) / 4.2)), 1)
    # 免拍提示里的「【免拍】」是给机读的标记，人读的卡上不要
    tip = re.sub(r"^【免拍】\s*", "", str(beat.get("shoot_tip") or "")).strip()

    skip = vm == "gen"
    size_key, size = ("screen", SHOT_SIZES["screen"]) if skip else _shot_size(line, role, vm)
    takes, takes_why = _takes_of(role, size_key)

    tpl = str(((beat.get("code_fallback") or {}).get("template")) or "keyword")
    if skip:
        what = f"免拍：本镜由代码画面「{tpl}」承担（{tip}）" if tip else \
               f"免拍：本镜由代码画面「{tpl}」承担"
    elif size_key == "detail":
        what = f"手部特写：{tip}" if tip else "手部特写：手和实物一起入画，拍 5 秒"
    elif size_key == "wide":
        what = f"环境全景：{tip}" if tip else "环境全景：人和场地一起入画"
    else:
        what = f"你本人出镜说这句（{tip}）" if tip else "你本人出镜说这句"

    label = f"镜头 {int(beat.get('n') or 0)} · {beat.get('phase') or ''}".strip(" ·")
    return {
        "n": int(beat.get("n") or 0),
        "phase": str(beat.get("phase") or ""),
        "line": line,
        "role": role,
        "size_key": size_key,
        "size": size,
        "what": what,
        "camera": CAMERA_OF[size_key],
        "dur_s": dur,
        "takes": takes,
        "takes_why": takes_why,
        "phone": list(PHONE_TIPS[size_key]),
        "frame_note": FRAME_NOTE[size_key],
        "skip": skip,
        "motion": "pan_right" if size_key == "wide" else "none",
        "cam_height": CAM_HEIGHT_OF[size_key],
        "angle_deg": CAM_ANGLE_OF[size_key],
        "cam_dist_m": CAM_DIST_OF.get(size_key),
        "label": label,
    }


def render_shoot_guide(idea: dict, proj: Path | None = None) -> str:
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in idea["proposals"] if p["index"] == sel), idea["proposals"][0])
    beats = prop["beats"]
    full_script = "".join(b["line"] for b in beats)
    est_s = round(sum(float(b.get("dur_s") or 5.0) for b in beats), 1)
    shots = [shot_plan(b, who=str(idea.get("persona") or "")) for b in beats]

    # 简笔示意：每个镜头一张（免拍镜头也有一张，画的是「这镜不用拍」）。
    # PIL 缺失时只报警，不影响卡本身——六项字段照样能照着拍。
    diagrams: dict[int, str] = {}
    if proj is not None:
        try:
            import shoot_diagram
            diagrams = shoot_diagram.render_all(proj, shots)
        except Exception as e:                              # noqa: BLE001
            print(f"  ⚠ 简笔示意没出图（{e}）——拍片卡其余字段不受影响")

    real = [s for s in shots if not s["skip"]]
    takes_total = sum(s["takes"] for s in shots)
    # 实拍耗时：每条 take 按「机位摆好 + 念一遍 + 停 1 秒」≈ 镜头时长 + 12 秒 估（摆位/重来都算进去）
    est_min = round(sum(s["takes"] * (s["dur_s"] + 12) for s in real) / 60.0, 1)

    lines = [
        f"# 小白 10 分钟拍片与素材准备卡 · {idea['core']}",
        "",
        f"- **选定方向**：方案 {prop['index']} · {prop['label']}（面向 {prop['crowd']}）",
        f"- **脚本结构**：{prop.get('structure') or '（旧版 ideation.json 无结构字段，重跑 --topic 生成）'}"
        f" ／ 推进手法 {prop.get('method') or '—'}",
        f"  - 结构出处：{prop.get('structure_source') or '（缺）'}",
        f"  - 手法出处：{prop.get('method_source') or '（缺）'}",
        f"  - 编排说明：{prop.get('structure_note') or '（缺）'}",
        f"- **预计成片时长**：约 {est_s:.0f} 秒（共 {len(full_script)} 字，正常说话语速即可）",
        f"- **包装与配乐预设**：`{prop['pack']}` + `{prop['bgm_mood']}` 律动配乐 + 顶部标题条「{prop['title_bar']['top']}｜{prop['title_bar']['main']}」",
        "",
        "---",
        "",
        "## 一、三种省心拍法（任选一种，不用懂任何摄影）",
        "",
        "1. **最火真人感（推荐，手机 10 分钟搞定）**：按下面「逐句台词」用手机竖屏录 3–4 句说话 + 随手拍 1 段 5 秒手头特写，其余对比卡/算账画面全部由 **CutSkills 代码自动生成**。",
        "2. **只录声音 / 只给旧素材**：不想露脸？直接拿手机录音机把下面台词念一遍发过来（或丢几段以前拍过的店里/干活视频），画面交给代码和老素材混剪。",
        "3. **零拍摄全自动出片**：连录音都不用！直接告诉 AI「用配音直接出片」，AI 自动合成自然人声 + 动态视觉卡片 + 贴纸音效直接出成片。",
        "",
        "---",
        "",
        "## 二、前 3 秒黄金钩子（默认用第 1 条，想换直接说编号）",
        "",
    ]
    for i, h in enumerate(prop.get("hooks") or [], 1):
        mark = "✅ 当前选用" if i - 1 == int(prop.get("selected_hook") or 0) else "备选"
        lines.append(f"- **钩子 {i}（{h['type']} · {mark}）**：「{h['line']}」")
        lines.append(f"  - 出处：{h.get('source') or '（旧版 ideation.json 无出处，重跑 --topic 生成）'}")
        lines.append(f"  - 句式：{h.get('formula') or '（缺）'}")
        if h.get("note"):
            lines.append(f"  - 说明：{h['note']}")

    lines += [
        "",
        "---",
        "",
        "## 三、拍摄任务卡（每镜一行，按顺序从上往下拍）",
        "",
        "> **小白防翻车三条口诀**：",
        "> 1. **擦镜头 + 面对窗户站**：手机竖放跟眼睛平齐（离自己一臂远），让窗户光打在脸上，**千万别背对窗户**。",
        "> 2. **看一句念一句，念错别关机**：念完一句停顿 1 秒再看下一句；嘴瓢了深吸一口气**只把刚才那句重念一遍**，废片程序会自动剪掉。",
        "> 3. **同一镜的备份连着拍完再换机位**：机位一动，前面那条就跟后面配不上了。",
        "",
        f"- 共 **{len(shots)}** 镜：实拍 **{len(real)}** 镜 + 免拍 **{len(shots) - len(real)}** 镜；"
        f"备份合计 **{takes_total}** 条",
        f"- 预计实拍耗时：约 **{est_min} 分钟**（每条 take 按「镜头时长 + 12 秒摆位/重来」估）",
        "- 顺序就是表格从上往下：**先把第 1 镜的备份拍完**，再挪机位拍第 2 镜。",
        "",
        "| 镜号 | 台词（一字不差照念） | 拍什么 | 景别 | 机位（高度·角度） | 时长 | 备份 | 手机拍摄要点 | 简笔示意 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in shots:
        cell = f"[图{s['n']}]({diagrams[s['n']]})" if s["n"] in diagrams else "（见 shoot/）"
        takes_cell = f"{s['takes']} 条" if s["takes"] else "不用拍"
        tips = "；".join(f"{i}) {t}" for i, t in enumerate(s["phone"], 1))
        lines.append(
            f"| {s['n']}. {s['phase']} | 「{s['line']}」 | {s['what']} | {s['size']} | "
            f"{s['camera']} | {s['dur_s']:.1f}s | {takes_cell} | {tips} | {cell} |")

    lines += ["", "**备份条数怎么定的**（不是随手写个 2）："]
    for s in shots:
        lines.append(f"- 第 {s['n']} 镜 · {s['takes_why']}")

    # 第 1 段念的是钩子，出处写脚注：人要能直接看到「这句用了原文哪一节、哪个切入点」
    if beats and beats[0].get("hook_source"):
        b0 = beats[0]
        lines += ["",
                  f"> 第 1 段念的是钩子「{b0.get('hook_type') or ''}」——出处：{b0['hook_source']}；"
                  f"句式：{b0.get('hook_formula') or '（缺）'}"]

    if diagrams:
        lines += ["", "---", "",
                  "## 四、简笔示意（每镜一张：取景框 9:16 · 主体位置 · 机位高度 · 运动方向）",
                  "",
                  "> 不要美术效果，看懂就行：上半张框里是**手机屏幕里该看到的样子**，"
                  "下半张是**站在旁边看**的机位高度；红色箭头是镜头要往哪动（没有箭头＝别动）。",
                  ""]
        for s in shots:
            lines.append(f"**镜头 {s['n']} · {s['size']} · {s['dur_s']:.1f}s · {s['camera'].split('｜')[0]}**")
            lines.append("")
            lines.append(f"![镜头 {s['n']} 示意]({diagrams[s['n']]})")
            lines.append("")

    lines += [
        "---",
        "",
        f"## {'五' if diagrams else '四'}、拍完怎么交给我？",
        "",
        "- 把拍好的几个视频丢进同一个文件夹（**不用改名、不用自己剪开头结尾**），直接把文件夹路径发给我，或者直接说一句 **「就按这个脚本用 AI 配音直接做出来看看」**！",
        "- 交之前顺手跑一遍素材规整（手机素材多为可变帧率，先统一成恒定帧率再剪，音画才不会越剪越偏）：",
        "  `python3 scripts/normalize.py --library <素材库> --dry-run` 先看哪些要转，去掉 `--dry-run` 真转。",
        "",
    ]
    return "\n".join(lines)


MG_ROLE_OF = {"setup": "organization", "hook": "organization", "claim": "explanation",
              "evidence": "evidence", "warrant": "explanation", "turn": "calibration",
              "payoff": "action", "close": "consequence"}
TEXT_CHAIN_TPLS = ("keyword", "quote")      # 渲器支持 micro 文字接力的卡型
NUM_TPLS = {"compare", "number", "chart", "balance"}   # 数字卡：无出处时必须标 approx


def _short_core(core: str, max_len: int = 8) -> str:
    """标题条/短文案用：把选题核心截到 max_len 内，但只在词界断（jieba），
    不出现「600平纯手」这种半截词。"""
    core = str(core or "").strip()
    if len(core) <= max_len:
        return core
    try:
        import jieba
        cur = ""
        for w in jieba.lcut(core):
            w = w.strip()
            if not w:
                continue
            if cur and len(cur) + len(w) > max_len:
                break
            cur += w
        return cur or core[:max_len]
    except Exception:
        return core[:max_len]


def _phrases(line: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[，。！？；、,.!?;:]", str(line)) if p.strip()]
    return parts or [str(line).strip()]


def _micro_chain(line: str, dur: float) -> list[dict] | None:
    """按台词气口把长卡大字拆成几段接力（渲器 .mstack 逐段换字）。

    分段三条硬规矩（判据同源）：任意两段之间 ≤1.2s 要有一次换字
    （段数 ≥ ceil(dur/1.05)，时刻均匀等分）；单段 ≤5 字左右且不切词
    （jieba 词界拼段）；优先在标点短语边界断段，标点不上大字。"""
    import math
    parts = _phrases(line)
    if len(parts) < 2:
        return None
    toks: list[tuple[str, int]] = []      # (词, 短语序号)
    try:
        import jieba
        for pi, p in enumerate(parts):
            for w in jieba.lcut(p):
                w = re.sub(r"[，。！？；：、,.!?;:\s]", "", w)
                if w:
                    toks.append((w, pi))
    except Exception:
        toks = [(c, i) for i, p in enumerate(parts) for c in p
                if not re.match(r"[，。！？；：、,.!?;:\s]", c)]
    total = sum(len(w) for w, _ in toks) or 1
    need = max(2, math.ceil(dur / 1.05))
    budget = total / need
    cap = math.ceil(budget) + 1
    groups, curg, cur_len = [], [], 0
    for w, pi in toks:
        boundary = curg and pi != curg[-1][1] and cur_len >= budget * 0.5
        if curg and (cur_len + len(w) > cap or boundary):
            groups.append(curg)
            curg, cur_len = [], 0
        curg.append((w, pi))
        cur_len += len(w)
    if curg:
        groups.append(curg)
    # 段数还凑不够节奏下限 → 把最长的组按词界对半拆（不切词，只拆组）
    while len(groups) < need and max(sum(len(w) for w, _ in g) for g in groups) >= 2:
        gi = max(range(len(groups)), key=lambda k: sum(len(w) for w, _ in groups[k]))
        g = groups.pop(gi)
        half = sum(len(w) for w, _ in g) / 2
        acc, cut = 0, 1
        for k, (w, _) in enumerate(g[:-1]):
            acc += len(w)
            if acc >= half:
                cut = k + 1
                break
        groups[gi:gi] = [g[:cut], g[cut:]]
    chunks = ["".join(w for w, _ in g) for g in groups]
    if len(chunks) < 2:
        return None
    n = len(chunks)
    return [{"t": round(dur * i / n, 2), "text": c} for i, c in enumerate(chunks)]


def _tpl_for_dur(dur: float, base: str, line: str) -> tuple[str, dict]:
    """音频窗比这张卡能撑的上限还长 → 升级卡型（阈值对齐 GEN_TEMPLATES 上限），
    别让「会显得空」漏到成片。"""
    tpl = base
    props: dict = {}
    if dur > 6.0 and tpl == "keyword":
        if dur <= 7.5:
            tpl = "quote"                  # 整句引言卡
        else:
            ph = _phrases(line)
            tpl = "steps" if dur <= 9.5 else "flow"
            props = {"items": ph}
    elif dur > 7.5 and tpl == "quote":
        ph = _phrases(line)
        tpl = "steps" if dur <= 9.5 else "flow"
        props = {"items": ph}
    return tpl, props


def ideation_to_plan(proj: Path, idea: dict, *, vo_asset: str | None = None,
                     vo_spans: list[tuple[float, float]] | None = None) -> tuple[Path, Path]:
    """把选定的选题脚本转成 brief.json + plan.json（支持零实拍纯代码画面 + AI 配音时间窗）。"""
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in idea["proposals"] if p["index"] == sel), idea["proposals"][0])
    beats_in = prop["beats"]
    who = str(idea.get("persona") or "").strip() or "内行人"

    segments = []
    beats_meta = []
    cur_t = 0.0
    for idx, b in enumerate(beats_in):
        n = int(b["n"])
        beats_meta.append({"n": n, "title": b.get("phase") or f"段落{n}"})
        if vo_spans and idx < len(vo_spans):
            ain, aout = vo_spans[idx]
            dur = round(max(1.8, aout - ain), 3)
        else:
            dur = round(float(b.get("dur_s") or max(2.8, len(b["line"]) / 4.2)), 3)
            ain, aout = round(cur_t, 3), round(cur_t + dur, 3)
        cur_t = round(cur_t + dur, 3)

        fb = b.get("code_fallback") or {"template": "keyword", "props": {"text": b["line"][:7]}}
        tpl = str(fb.get("template") or "keyword")
        up_tpl, up_props = _tpl_for_dur(dur, tpl, b["line"])
        if up_tpl != tpl:
            tpl = up_tpl
        props = dict(fb.get("props") or {})
        props.update(up_props or {})
        if tpl == "quote" and not props.get("text"):
            props = {"text": b["line"], "who": who}
        seg: dict = {
            "kind": "gen",
            "template": tpl,
            "props": props,
            "dur": dur,
            "beat": n,
            "role": b.get("role") or "claim",
            "mg_role": MG_ROLE_OF.get(str(b.get("role") or "")),
            "evidence": b["line"],
            "why": f"{b.get('phase') or '叙事段落'}：代码动效承载主视觉",
        }
        micro = _micro_chain(b["line"], dur) if tpl in TEXT_CHAIN_TPLS else None
        if micro:
            seg["micro"] = micro
        if segments and tpl != segments[-1].get("template"):
            seg["hard_cut"] = (f"叙事推进：{segments[-1].get('role')} → {b.get('role')}，"
                               f"{segments[-1].get('template')} 换 {tpl} 卡型承接脚本气口")
        if tpl in NUM_TPLS:
            seg["approx"] = True      # 零拍摄路线没有可引用出处，画面标「示意」
        if vo_asset:
            seg["audio"] = {"asset": vo_asset, "in": round(ain, 3), "out": round(aout, 3)}
        if b.get("card"):
            seg["card"] = b["card"]
        if b.get("sticker"):
            seg["sticker"] = b["sticker"]
        if b.get("vfx"):
            seg["vfx"] = b["vfx"]
        segments.append(seg)

    total_s = round(sum(float(s["dur"]) for s in segments), 2)
    # 字段名以消费方 hooks.py 为准：audience_present_state。
    # 旧稿这里写的是 belief_now——全仓只有它写那个名字、钩子链却读另一个，所以 3 秒钩子永远拿到空值。
    present = "觉得价格差不多就行，剩下就看谁报价低"
    brief = {
        "slug": proj.name,
        "audience": idea.get("audience") or "目标短视频受众",
        "audience_present_state": present,
        "change": f"看懂{idea.get('core') or '核心门道'}的关键判断标准",
        "length_s": round(total_s),
        "length_band": "30s" if total_s <= 38 else "60-90s",
        "style_pack": "talking-head-v1",
        "pack": prop.get("pack") or "P1-warm",
        "plan": prop.get("plan") or "S1-conclude-fast",
        "bgm_mood": prop.get("bgm_mood") or "upbeat",
        "assumed": ["audience_present_state 是模板占位（谁都没回答过他此刻信什么）："
                    "钩子定稿前改成他真实相信的那句（brief.py --set audience_present_state=...）"],
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    plan_doc = {
        "slug": proj.name,
        "pack": prop.get("pack") or "P1-warm",
        "plan": prop.get("plan") or "S1-conclude-fast",
        "bgm_mood": prop.get("bgm_mood") or "upbeat",
        "title_bar": prop.get("title_bar"),
        "beats": beats_meta,
        "segments": segments,
    }
    brief_p = proj / "brief.json"
    plan_p = proj / "plan.json"
    brief_p.write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
    plan_p.write_text(json.dumps(plan_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return brief_p, plan_p


def script_lines_of(idea: dict) -> list[str]:
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in idea["proposals"] if p["index"] == sel), idea["proposals"][0])
    return [str(b["line"]).strip() for b in prop["beats"] if str(b.get("line") or "").strip()]


# ---------------------------------------------------------------- 拍板②：确认拍摄卡 / 盘点
def shots_of(idea: dict) -> list[dict]:
    """当前选题的镜头表（与 shoot-guide.md 的表格同源：都走 shot_plan）。"""
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in idea["proposals"] if p["index"] == sel), idea["proposals"][0])
    return [shot_plan(b, who=str(idea.get("persona") or "")) for b in prop["beats"]]


def parse_shot_changes(note: str) -> list[dict]:
    """从用户的一句话里认出「改了哪几镜」。

    「第3镜改成俯拍，第4镜不要了」→ [{n:3, note:"第3镜改成俯拍"}, {n:4, note:"第4镜不要了"}]。
    认只认带「镜」字或 # 号的数字：用户随口说的「半小时」不能被当成第 3 镜。
    """
    out: list[dict] = []
    for clause in re.split(r"[，,；;。\n]", str(note or "")):
        for m in SHOT_MENTION.finditer(clause):
            n = int(m.group(1) or m.group(2))
            if not any(c["n"] == n for c in out):
                out.append({"n": n, "note": clause.strip(" 。")})
    return sorted(out, key=lambda c: c["n"])


def parse_clip_spec(spec: str) -> dict:
    """`a.mov` = 整支不用；`a.mov:12-18.5` = 只排除这一小段。"""
    m = CLIP_SPEC.match(str(spec or "").strip())
    if not m:
        return {"asset": str(spec or "").strip(), "in": None, "out": None}
    a, b = float(m.group("a")), float(m.group("b"))
    return {"asset": m.group("asset").strip(), "in": round(min(a, b), 3),
            "out": round(max(a, b), 3)}


def resolve_inventory(proj: Path, explicit: str | None = None) -> Path | None:
    """盘点报告在哪：手给的 > 选题是从哪个库反推出来的（ideation.json 记着）。"""
    if explicit:
        cand = Path(explicit).expanduser()
        if cand.is_file():
            return cand.resolve()
        for c in (cand / "_cutskills" / "inventory.json", cand / "inventory.json"):
            if c.exists():
                return c.resolve()
        return None
    obj = shots_object(proj) or {}
    if obj.get("kind") == "inventory":
        return Path(obj["path"])
    lib = ""
    try:
        idea = json.loads((proj / "ideation.json").read_text(encoding="utf-8"))
        lib = str(((idea or {}).get("from_inventory") or {}).get("library") or "")
    except (OSError, ValueError):
        lib = ""
    if not lib:
        return None
    for c in (Path(lib) / "_cutskills" / "inventory.json", Path(lib) / "inventory.json"):
        if c.exists():
            return c.resolve()
    return None


def apply_exclusions(rep: dict, exclusions: list[dict]) -> tuple[dict, int]:
    """把用户排除的段从盘点结果里摘掉；摘了几条如实返回。

    改就改在盘点结果本身（`clip_pool` 与 `usable_clips.top` 两处消费点），不另存一份黑名单——
    否则选题照样引用「他说另有用途、别用」的那段，留痕变成装饰。
    """
    if not exclusions:
        return rep, 0

    def hit(c: dict) -> bool:
        for ex in exclusions:
            if str(c.get("asset") or "") != str(ex.get("asset") or ""):
                continue
            if ex.get("in") is None:
                return True                      # 整支不用
            ci, co = float(c.get("in") or 0), float(c.get("out") or 0)
            if co > float(ex["in"]) and ci < float(ex["out"]):
                return True                      # 有重叠就算被点名
        return False

    removed = 0
    pool = rep.get("clip_pool") or {}
    for bucket, clips in list(pool.items()):
        keep = [c for c in clips if not hit(c)]
        removed += len(clips) - len(keep)
        pool[bucket] = keep
    top = (rep.get("usable_clips") or {}).get("top") or []
    if top:
        keep = [c for c in top if not hit(c)]
        removed += len(top) - len(keep)
        rep["usable_clips"]["top"] = keep
    return rep, removed


def _write_exclusions_to_report(inv_p: Path, exclusions: list[dict]) -> dict:
    """把排除项写回盘点报告（inventory.json + inventory.md），不重算任何既有数字。"""
    rep = json.loads(inv_p.read_text(encoding="utf-8"))
    rep["user_exclusions"] = exclusions
    rep["user_exclusions_source"] = ("ideate.py --approve-material（用户点名「这段另有用」；"
                                     "不改盘点测出的任何数字）")
    inv_p.write_text(json.dumps(rep, ensure_ascii=False, indent=1, allow_nan=False),
                     encoding="utf-8")
    md = inv_p.with_name("inventory.md")
    if md.exists():
        block = ["", "<!-- user-exclusions: 拍板② 用户排除，重跑 inventory.py 会被重写 -->",
                 "## 用户点名的排除（拍板②）", ""]
        for ex in exclusions:
            span = "整支" if ex.get("in") is None else f"{ex['in']}–{ex['out']}s"
            block.append(f"- `{ex['asset']}` {span}：{ex.get('why') or '（用户未写原因）'}")
        text = md.read_text(encoding="utf-8")
        cut = text.find("\n<!-- user-exclusions")
        if cut >= 0:
            text = text[:cut].rstrip("\n") + "\n"
        md.write_text(text.rstrip("\n") + "\n" + "\n".join(block) + "\n", encoding="utf-8")
    return rep


def approve_shots(proj: Path, idea: dict, note: str) -> dict:
    """拍板②A：用户认可了这张拍摄任务卡（或指明了改哪几镜）。返回写进 approval.json 的那条记录。"""
    gp = proj / "shoot-guide.md"
    if not gp.exists():
        gp.write_text(render_shoot_guide(idea, proj), encoding="utf-8")
        print(f"  （拍片卡当时不在，先按当前脚本重新生成：{gp}）")
    shots = shots_of(idea)
    changes = parse_shot_changes(note)
    total = len(shots)
    bad = [c["n"] for c in changes if not 1 <= c["n"] <= total]
    detail = {"kind": "shoot-card", "path": str(gp), "sha": sha256_of(gp),
              "shots_total": total, "changes": changes,
              "changed_shots": [c["n"] for c in changes]}
    rec = record_gate(proj, GATE_SHOTS, note=note, detail=detail,
                      where="ideate --approve-shots")
    note_interrupt(proj, GATE_SHOTS, "ideate --approve-shots",
                   "这张拍摄任务卡认可吗？改了哪几镜？")
    from log_decision import append_row
    did = f"确认拍摄任务卡（{total} 镜、卡指纹 {detail['sha'][:12]}）"
    if changes:
        did += "；改了 " + "、".join(f"第{c['n']}镜（{c['note']}）" for c in changes)
    append_row(proj, said=note, did=did, verdict="accepted")
    print(f"→ 拍板②（拍摄卡）已留痕：{total} 镜，改 {len(changes)} 镜 → {proj / APPROVAL_FILE}")
    for c in changes:
        print(f"   第 {c['n']} 镜：{c['note']}")
    if bad:
        print(f"   ⚠ 卡里只有 {total} 镜，{bad} 镜不在卡上——是不是脚本又改过了？看完新卡再签一遍")
    return rec


def approve_material(proj: Path, note: str, exclude_specs: list[str],
                     inventory: str | None = None) -> dict:
    """拍板②B：用户认可了素材盘点结果（或点掉几段「这段另有用」）。"""
    inv_p = resolve_inventory(proj, inventory)
    if inv_p is None:
        raise SystemExit("找不到盘点报告：先跑 python3 scripts/inventory.py --library <素材库>，"
                         "或用 --inventory 指一下 inventory.json")
    rep = json.loads(inv_p.read_text(encoding="utf-8"))
    merged: list[dict] = [dict(x) for x in (rep.get("user_exclusions") or [])]
    # 盘点报告是 inventory.py 生成的，重跑一次就会把 user_exclusions 洗掉。
    # 上次点过的排除存在 approval.json 里，这里先把它接回来，不让用户的判断无声无息地丢了。
    prev = (load_approval(proj).get("gates") or {}).get(GATE_SHOTS) or {}
    for x in ((prev.get("detail") or {}).get("excluded") or []):
        key = (x.get("asset"), x.get("in"), x.get("out"))
        if not any((y.get("asset"), y.get("in"), y.get("out")) == key for y in merged):
            merged.append(dict(x))
    for spec in exclude_specs:
        ex = parse_clip_spec(spec)
        ex["why"] = note
        ex["at"] = datetime.now().isoformat(timespec="seconds")
        key = (ex["asset"], ex.get("in"), ex.get("out"))
        if not any((x.get("asset"), x.get("in"), x.get("out")) == key for x in merged):
            merged.append(ex)
    rep = _write_exclusions_to_report(inv_p, merged)
    totals = rep.get("totals") or {}
    detail = {"kind": "inventory", "path": str(inv_p), "sha": sha256_of(inv_p),
              "assets": totals.get("assets"), "usable_s": totals.get("usable_s"),
              "speech_usable_s": (rep.get("speech") or {}).get("speech_usable_s"),
              "excluded": merged,
              "excluded_count": len(merged)}
    rec = record_gate(proj, GATE_SHOTS, note=note, detail=detail,
                      where="ideate --approve-material")
    note_interrupt(proj, GATE_SHOTS, "ideate --approve-material",
                   "这个库拍得出这几条吗？哪几段另有用途、别用？")
    from log_decision import append_row
    did = (f"确认素材盘点（{totals.get('assets')} 个文件 · 可用 {totals.get('usable_s')}s · "
           f"盘点指纹 {detail['sha'][:12]}）")
    if merged:
        did += "；排除 " + "、".join(
            (f"{x['asset']}（整支）" if x.get("in") is None
             else f"{x['asset']} {x['in']}–{x['out']}s") for x in merged[:6])
    append_row(proj, said=note, did=did, verdict="accepted")
    print(f"→ 拍板②（素材盘点）已留痕：{inv_p.name} 指纹 {detail['sha'][:12]}"
          f"（排除 {len(merged)} 段）")
    for ex in merged:
        span = "整支" if ex.get("in") is None else f"{ex['in']}–{ex['out']}s"
        print(f"   排除 {ex['asset']} {span}：{ex.get('why') or '—'}")
    return rec


def print_gates(proj: Path) -> None:
    """三次拍板一眼看完：哪次签了、签的是哪一版、还没签的缺什么。"""
    st = gate_state(proj)
    print(f"三次拍板 · {proj.name}（上限 {INTERRUPT_BUDGET} 次，BRIEF 交互约束）")
    for gate in REQUIRED_GATES:
        s = st[gate]
        mark = "🟢" if s["ok"] else "🔴"
        obj = {"shoot-card": "拍摄任务卡", "inventory": "素材盘点",
               "none": "无对象", "topic": "选题", "cut": "锁定音轨"}[s.get("object") or "none"]
        print(f"  {mark} {gate} {s['label']}（{obj}）"
              f"{(' · ' + str(s.get('at'))) if s.get('at') else ''}"
              f"{(' · ' + str(s.get('note'))[:40]) if s.get('note') else ''}")
        if not s["ok"]:
            print(f"      {s['why']}")
        if s.get("changes"):
            print("      改过的镜：" + "、".join(f"第{c['n']}镜" for c in s["changes"]))
        if s.get("excluded"):
            print(f"      排除的段：{len(s['excluded'])} 段")
    asks = load_approval(proj).get("asks") or []
    gates_asked = sorted({a.get("gate") for a in asks})
    print(f"  这条片子登记到的打断：{len(gates_asked)} 个 / 上限 {INTERRUPT_BUDGET}"
          f"{('（' + '、'.join(gates_asked) + '）') if gates_asked else ''}")



def main() -> int:
    ap = argparse.ArgumentParser(description="上游选题策划、黄金3秒钩子与小白拍片卡生成")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--topic", default=None, help="一句话选题想法、产品或行业")
    ap.add_argument("--persona", default="", help="出镜人身份/人设（可选）")
    ap.add_argument("--audience", default="", help="给谁看（可选）")
    ap.add_argument("--duration", type=int, default=35, help="期望时长（秒，默认 35）")
    ap.add_argument("--pick", type=int, choices=[1, 2, 3], default=None, help="选定第几套提案（1漏斗/2人设/3转化）")
    ap.add_argument("--hook", type=int, choices=[1, 2, 3], default=None, help="选定该提案的第几个钩子（1-3）")
    ap.add_argument("--guide", action="store_true", help="输出/刷新 shoot-guide.md 小白拍片卡")
    ap.add_argument("--to-plan", action="store_true", help="将选定脚本一键转为 brief.json + plan.json")
    ap.add_argument("--vo-asset", default=None, help="绑定已生成的 AI 配音素材 ID")
    ap.add_argument("--show", action="store_true", help="打印 3 套提案概览")
    ap.add_argument("--approve-shots", dest="approve_shots", default=None, metavar="NOTE",
                    help="拍板②A：用户认可了这张拍摄任务卡。NOTE 写他的一句原话；"
                         "提到「第 3 镜…」会把改动逐镜记进 approval.json 与 decisions.md")
    ap.add_argument("--approve-material", dest="approve_material", default=None, metavar="NOTE",
                    help="拍板②B：用户认可了素材盘点结果（「这个库拍得出这几条」）。"
                         "配合 --exclude-clip 记下「这段另有用，别用」")
    ap.add_argument("--exclude-clip", dest="exclude_clips", action="append", default=[],
                    metavar="ASSET[:IN-OUT]",
                    help="把盘点里的一段排除掉（可给多次）。只给文件名 = 整支不用；"
                         "a.mov:12-18.5 = 只排除这一小段")
    ap.add_argument("--inventory", default=None,
                    help="盘点报告路径（默认按 ideation.json 的 from_inventory.library 找）")
    ap.add_argument("--gates", action="store_true", help="只看三次拍板的留痕状态（不改任何东西）")
    ap.add_argument("--from-inventory", default=None, metavar="素材库|inventory.json",
                    help="先用 scripts/inventory.py 盘完素材，再按盘点结果直接出 3 套选题"
                         "（可给素材库目录或 inventory.json 路径）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    proj.mkdir(parents=True, exist_ok=True)
    idea_p = proj / "ideation.json"

    if a.gates:
        print_gates(proj)
        return 0

    if (a.approve_shots is not None or a.approve_material is not None) and not idea_p.exists():
        raise SystemExit("还没有选题（ideation.json）：拍板② 确认的对象得先有——"
                         "入口 A 跑 `--topic … --guide`，入口 B 跑 `--from-inventory <素材库>`")

    if a.from_inventory:
        # 延后导入：inventory.py 反过来要用本模块的 HOOK_BANK / STREAMS，顶部互相 import 会成环。
        import inventory as _inv
        rep = _inv.load_report(Path(a.from_inventory))
        # 用户点过「这段另有用途、别用」的话，选题就不能再引用它——排除项存在盘点报告自己身上
        rep, n_ex = apply_exclusions(rep, rep.get("user_exclusions") or [])
        if n_ex:
            print(f"  按拍板② 的排除，从盘点里摸掉了 {n_ex} 段画面（不再当候选）")
        idea = _inv.build_ideation(rep, duration_s=a.duration, persona=a.persona,
                                   audience=a.audience)
        if idea.get("_refused"):
            # 盘点说这批料拍不出片：如实拒绝，不给三套假选题
            print(f"{idea['verdict']}（{idea['library']}）")
            print("缺的是：")
            for m in idea["missing"]:
                print(f"  - {m}")
            print("不硬凑选题：先把上面几项补齐，再跑 python3 scripts/inventory.py --library …")
            return 3
        if a.pick:
            # 盘点可能只撑得住两条（宁少勿凑）：那时 --pick 3 必须明确拒绝，
            # 不许拿兜底提案糊一张拍片卡给用户。
            n_ok = len(idea.get("proposals") or [])
            if not 1 <= a.pick <= n_ok:
                print(f"这个库只撑得住 {n_ok} 套差异化选题，没有第 {a.pick} 套。")
                print(f"换 `--pick {n_ok}`，或按盘点报告里「还缺什么」补素材后重跑 inventory.py。")
                return 2
            idea["selected_proposal"] = a.pick
        idea_p.write_text(json.dumps(idea, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"按盘点出选题：{idea['core']}（库：{idea['from_inventory']['library']}）")
        print(f"  可用画面 {idea['from_inventory']['usable_s']}s · "
              f"可用口播 {idea['from_inventory']['speech_usable_s']}s · "
              f"主题词：{'、'.join(idea['from_inventory']['keywords'][:5])}")
    elif a.topic:
        idea = build_proposals(a.topic, a.persona, a.audience, a.duration)
        if a.pick:
            idea["selected_proposal"] = a.pick
        idea_p.write_text(json.dumps(idea, ensure_ascii=False, indent=2), encoding="utf-8")
    elif idea_p.exists():
        idea = json.loads(idea_p.read_text(encoding="utf-8"))
    else:
        raise SystemExit("尚未生成选题，请带 `--topic \"你的想法\"` 运行。")

    changed = False
    if a.pick and idea.get("selected_proposal") != a.pick:
        idea["selected_proposal"] = a.pick
        changed = True
    if a.hook:
        sel = int(idea.get("selected_proposal") or 1)
        for p in idea["proposals"]:
            if p["index"] == sel:
                p["selected_hook"] = a.hook - 1
                hk = p["hooks"][a.hook - 1]
                p["beats"][0]["line"] = hk["line"]
                # 出处跟着换：换了钩子不换出处，拍片卡就变成假引用
                p["beats"][0]["hook_type"] = hk.get("type")
                p["beats"][0]["hook_source"] = hk.get("source")
                p["beats"][0]["hook_formula"] = hk.get("formula")
                changed = True
    if changed:
        idea_p.write_text(json.dumps(idea, ensure_ascii=False, indent=2), encoding="utf-8")

    if a.pick or a.hook:
        # 拍板①：用户明确选了第几套/第几个钩子——这就是「确认选题」那个动作本身，把它留痕。
        sel = int(idea.get("selected_proposal") or 1)
        prop = next((p for p in idea["proposals"] if p["index"] == sel), idea["proposals"][0])
        said = [f"选定方案 {sel}（{prop['label']}）"]
        if a.hook:
            hk = prop["hooks"][a.hook - 1]
            said.append(f"钩子 {a.hook}「{hk['line']}」（{hk['type']}）")
        note = "；".join(said)
        record_gate(proj, GATE_TOPIC, note=note, where="ideate --pick/--hook",
                    detail={"proposal": sel, "hook": a.hook or (prop.get("selected_hook", 0) + 1),
                            "topic": prop.get("topic") or idea.get("core")})
        note_interrupt(proj, GATE_TOPIC, "ideate --pick/--hook",
                       "三套选题（含三个钩子）里选哪一套？")
        from log_decision import append_row
        append_row(proj, said=note, did=f"按方案 {sel} 往下做拍片卡", verdict="accepted")
        print(f"→ 拍板①（选题）已留痕：{note}")

    if a.show or a.topic or a.from_inventory:
        print(f"选题核心：{idea['core']} ｜ 人设：{idea['persona']} ｜ 受众：{idea['audience']}")
        for p in idea["proposals"]:
            mark = "★" if p["index"] == int(idea.get("selected_proposal") or 1) else " "
            print(f" {mark} [{p['index']}] {p['label']}（{p['crowd']}）")
            if p.get("topic"):
                print(f"       选题：{p['topic']}（{p['topic_kind']}）")
            if p.get("duration_range"):
                dr = p["duration_range"]
                print(f"       能撑：{dr['min_s']}–{dr['max_s']} 秒 ｜ 素材依据 "
                      f"{len(p.get('material_basis') or [])} 段 ｜ 缺画面 "
                      f"{len(p.get('missing_shots') or [])} 处")
            hk = p["hooks"][p.get("selected_hook", 0)]
            print(f"       开篇钩子：「{hk['line']}」（{hk['type']}）")
            print(f"         出处：{hk.get('source') or '（旧版 ideation.json 无出处，重跑 --topic 生成）'}"
                  f"｜句式：{hk.get('formula') or '（缺）'}")
            print(f"       结构骨架：{p.get('structure') or '（缺）'} ／ 推进手法 {p.get('method') or '（缺）'}")
            print(f"         出处：{p.get('structure_source') or '（缺）'}；{p.get('method_source') or '（缺）'}")
            if p.get("structure_note"):
                print(f"         编排：{p['structure_note']}")
            print(f"       包装：{p['pack']} + {p['bgm_mood']}")

    if a.guide or a.topic or a.pick or a.hook:
        gp = proj / "shoot-guide.md"
        gp.write_text(render_shoot_guide(idea, proj), encoding="utf-8")
        print(f"→ 小白拍片卡：{gp}")

    if a.approve_shots is not None:
        approve_shots(proj, idea, a.approve_shots or "认可")

    if a.approve_material is not None:
        approve_material(proj, a.approve_material or "认可", a.exclude_clips, a.inventory)
    elif a.exclude_clips:
        print("--exclude-clip 要跟 --approve-material 一起用（排除也是拍板② 的一部分）",
              file=sys.stderr)
        return 2

    if a.to_plan:
        vo_spans = None
        if a.vo_asset:
            # 若项目内有 voiceover.json 分句时间表则精准对齐每段时长
            vo_meta = proj / "voiceover.json"
            if vo_meta.exists():
                vdoc = json.loads(vo_meta.read_text(encoding="utf-8"))
                if vdoc.get("asset_id") == a.vo_asset and vdoc.get("spans"):
                    vo_spans = [(float(s["in"]), float(s["out"])) for s in vdoc["spans"]]
        bp, pp = ideation_to_plan(proj, idea, vo_asset=a.vo_asset, vo_spans=vo_spans)
        print(f"→ 已生成需求与分镜：{bp.name}, {pp.name}")
        # 「我替你做的决定」不能只藏在文件里：写 brief 的时候就念一遍，成片前 assumptions.md 再全量列
        assumed = json.loads(bp.read_text(encoding="utf-8")).get("assumed") or []
        if assumed:
            print(f"  · 先替你按默认值定了 {len(assumed)} 处（一句话能改，全量清单在 assumptions.md）：")
            for item in assumed:
                print(f"      - {item}")
        # 拍板② 没签就往下走：不能默默放行——现在就说清楚，免得渲到一半才被闸门拦
        shots_wait = gate_state(proj)[GATE_SHOTS]
        if not shots_wait["ok"]:
            print(f"  🔴 拍板② 还没签：{shots_wait['why']}", file=sys.stderr)
            print("     渲前闸门（render_hf.py）会拦这一条，签完再渲。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
