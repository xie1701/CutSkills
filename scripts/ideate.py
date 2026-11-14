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
import json
import re
from datetime import datetime
from pathlib import Path

HOOK_BANK = {
    "H01": {"name": "不字/禁止开头", "tpl": "千万别在没搞懂{core}之前，随便花冤枉钱！"},
    "H02": {"name": "反常识颠覆", "tpl": "90%的人都以为{core}靠堆预算，其实恰恰相反！"},
    "H03": {"name": "具体数字反差", "tpl": "同样做{core}，有人花三万翻车，有人花三千做出高级感。"},
    "H04": {"name": "做观众嘴替", "tpl": "是不是每次看{core}，都被一堆听不懂的行话绕晕了？"},
    "H05": {"name": "揭秘行业猫腻", "tpl": "做{core}最容易踩的三个暗坑，今天我把底牌亮给你看。"},
    "H06": {"name": "内行身份背书", "tpl": "{persona}说句得罪同行的大实话：{core}只看这三处就够了。"},
    "H07": {"name": "痛点直球疑问", "tpl": "为什么别人做的{core}越看越耐看，你一上手就踩雷？"},
    "H08": {"name": "算清成本明账", "tpl": "把{core}的成本拆成两半看，你就知道钱到底该花在哪儿了。"},
    "H09": {"name": "现场冲突切入", "tpl": "看好了！这就是{core}省掉关键一步的真实后果。"},
    "H10": {"name": "结果倒叙吸睛", "tpl": "只改了两个小细节，{core}质感直接翻倍，怎么做到的？"},
}

STREAMS = [
    {
        "id": "funnel-A1",
        "stream": "funnel",
        "crowd": "A1 泛流量破圈（占 50%）",
        "label": "漏斗流 · 反常识避坑（高播放拉新）",
        "structure": "观点锤击式（3秒暴击钩子 → 反常识结论 → 2个直观对比 → 一句话口诀收尾）",
        "hooks": ["H01", "H02", "H04"],
        "pack": "P1-warm",
        "plan": "S1-conclude-fast",
        "bgm_mood": "upbeat",
    },
    {
        "id": "bucket-A2",
        "stream": "bucket_persona",
        "crowd": "A2 人设与信任（占 30%）",
        "label": "水桶流 · 内行死磕细节（立专业人设）",
        "structure": "故事反转式（现场冲突切入 → 外行常见凑合做法 → 我们的死磕标准 → 态度金句）",
        "hooks": ["H06", "H09", "H10"],
        "pack": "P4-canton-warm",
        "plan": "S2-live-mid",
        "bgm_mood": "warm",
    },
    {
        "id": "bucket-A3",
        "stream": "bucket_convert",
        "crowd": "A3 垂直意向转化（占 20%）",
        "label": "水桶流 · 算清成本明账（高收藏高咨询）",
        "structure": "成本算账式（数字反差开篇 → 拆开表面价与长期账 → 怎么选最划算 → 互动提问收尾）",
        "hooks": ["H03", "H08", "H07"],
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
             "line": HOOK_BANK[c]["tpl"].format(core=core, persona=who)}
            for c in st["hooks"]
        ]
        lead_hook = hooks[0]["line"]
        if st["stream"] == "funnel":
            beats = [
                {
                    "n": 1, "role": "setup", "phase": "黄金3秒钩子", "dur_s": 3.2,
                    "line": lead_hook,
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "对着镜头直接说第一句，眼神笃定，前0.5秒别低头看稿",
                    "code_fallback": {"template": "keyword", "props": {"text": f"别盲目做{core[:4]}", "sub": "90%的人第一步就错了"}},
                    "sticker": {"id": "bang", "pos": "top-right"},
                    "vfx": "punch_zoom",
                    "sfx": "impact",
                },
                {
                    "n": 2, "role": "claim", "phase": "反常识核心结论", "dur_s": 6.5,
                    "line": f"真正决定{core}成败的，从来不是最贵的那一层表面功夫，而是看不见的底层细节。",
                    "visual_mode": "talking",
                    "shoot_tip": "继续面对窗户自然光说话，手势自然比一个『1』",
                    "card": {"kind": "keyword", "text": "胜负在底层细节", "on": "底层细节"},
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
            title_bar = {"top": f"做{core[:6]}前必看", "main": "90%踩过的隐形坑"}
        elif st["stream"] == "bucket_persona":
            beats = [
                {
                    "n": 1, "role": "setup", "phase": "现场冲突钩子", "dur_s": 3.4,
                    "line": lead_hook,
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
            title_bar = {"top": f"{who[:8]}真话", "main": f"{core[:6]}死磕标准"}
        else:
            beats = [
                {
                    "n": 1, "role": "setup", "phase": "数字反差钩子", "dur_s": 3.4,
                    "line": lead_hook,
                    "visual_mode": "talking_or_gen",
                    "shoot_tip": "伸出两根手指做对比状，第一句干脆利落",
                    "code_fallback": {"template": "number", "props": {"value": "10倍", "unit": "差距", "label": f"{core[:6]}明暗账"}},
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
                            "right": {"title": "贪便宜返工", "value": "耗时翻倍"},
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
                    "card": {"kind": "keyword", "text": "直接看实物小样", "on": "实物小样"},
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
            title_bar = {"top": "内行帮你算笔账", "main": f"{core[:6]}怎么花钱最值"}

        proposals.append({
            "index": idx,
            "id": st["id"],
            "crowd": st["crowd"],
            "label": st["label"],
            "structure": st["structure"],
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
            f"在「{core}」这件事上，客户最常被同行忽悠的一句套话是什么？（换进去钩子更狠）",
            "有没有一个你亲手处理过的真实案例或具体数字（比如省了多少钱、用了几年）？",
        ],
        "selected_proposal": 1,
        "proposals": proposals,
    }


def render_shoot_guide(idea: dict) -> str:
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in idea["proposals"] if p["index"] == sel), idea["proposals"][0])
    beats = prop["beats"]
    full_script = "".join(b["line"] for b in beats)
    est_s = round(sum(float(b.get("dur_s") or 5.0) for b in beats), 1)

    lines = [
        f"# 小白 10 分钟拍片与素材准备卡 · {idea['core']}",
        "",
        f"- **选定方向**：方案 {prop['index']} · {prop['label']}（面向 {prop['crowd']}）",
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

    lines += [
        "",
        "---",
        "",
        "## 三、逐句台词与傻瓜拍摄动作表",
        "",
        "> **小白防翻车三条口诀**：",
        "> 1. **擦镜头 + 面对窗户站**：手机竖放跟眼睛平齐（离自己一臂远），让窗户光打在脸上，**千万别背对窗户**。",
        "> 2. **看一句念一句，念错别关机**：念完一句停顿 1 秒再看下一句；嘴瓢了深吸一口气**只把刚才那句重念一遍**，废片程序会自动剪掉。",
        "> 3. **特写镜头手稳数 5 秒**：拍手头实物时别乱晃镜头，端稳手机默数 1-2-3-4-5 就好。",
        "",
        "| 段落 | 你要念的台词（一字不差照念即可） | 画面怎么准备（人话版） | 自动包装效果 |",
        "|---|---|---|---|",
    ]
    for b in beats:
        fx_desc = []
        if b.get("vfx"):
            fx_desc.append("开场冲击推镜")
        if b.get("card"):
            fx_desc.append(f"大字花字「{b['card']['text']}」")
        elif b.get("visual_mode") == "gen":
            fx_desc.append(f"代码生成 `{b['code_fallback']['template']}` 动效")
        if b.get("sticker"):
            fx_desc.append(f"动态贴纸 `{b['sticker']['id']}`")
        if b.get("sfx"):
            fx_desc.append(f"卡点音效 `{b['sfx']}`")
        lines.append(f"| {b['n']}. {b['phase']} | 「{b['line']}」 | {b['shoot_tip']} | {' + '.join(fx_desc) or '高清字幕'} |")

    lines += [
        "",
        "---",
        "",
        "## 四、拍完怎么交给我？",
        "",
        "- 把拍好的几个视频丢进同一个文件夹（**不用改名、不用自己剪开头结尾**），直接把文件夹路径发给我，或者直接说一句 **「就按这个脚本用 AI 配音直接做出来看看」**！",
        "",
    ]
    return "\n".join(lines)


MG_ROLE_OF = {"setup": "organization", "hook": "organization", "claim": "explanation",
              "evidence": "evidence", "warrant": "explanation", "turn": "calibration",
              "payoff": "action", "close": "consequence"}
TEXT_CHAIN_TPLS = ("keyword", "quote")      # 渲器支持 micro 文字接力的卡型
NUM_TPLS = {"compare", "number", "chart", "balance"}   # 数字卡：无出处时必须标 approx


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
    brief = {
        "slug": proj.name,
        "audience": idea.get("audience") or "目标短视频受众",
        "belief_now": "按惯性经验行事、容易踩坑",
        "change": f"看懂{idea.get('core') or '核心门道'}的关键判断标准",
        "length_s": round(total_s),
        "length_band": "30s" if total_s <= 38 else "60-90s",
        "style_pack": "talking-head-v1",
        "pack": prop.get("pack") or "P1-warm",
        "plan": prop.get("plan") or "S1-conclude-fast",
        "bgm_mood": prop.get("bgm_mood") or "upbeat",
        "assumed": [],
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
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    proj.mkdir(parents=True, exist_ok=True)
    idea_p = proj / "ideation.json"

    if a.topic:
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
                p["beats"][0]["line"] = p["hooks"][a.hook - 1]["line"]
                changed = True
    if changed:
        idea_p.write_text(json.dumps(idea, ensure_ascii=False, indent=2), encoding="utf-8")

    if a.show or a.topic:
        print(f"选题核心：{idea['core']} ｜ 人设：{idea['persona']} ｜ 受众：{idea['audience']}")
        for p in idea["proposals"]:
            mark = "★" if p["index"] == int(idea.get("selected_proposal") or 1) else " "
            print(f" {mark} [{p['index']}] {p['label']}（{p['crowd']}）")
            print(f"       开篇钩子：「{p['hooks'][p.get('selected_hook', 0)]['line']}」")
            print(f"       结构骨架：{p['structure']} ｜ 包装：{p['pack']} + {p['bgm_mood']}")

    if a.guide or a.topic or a.pick or a.hook:
        gp = proj / "shoot-guide.md"
        gp.write_text(render_shoot_guide(idea), encoding="utf-8")
        print(f"→ 小白拍片卡：{gp}")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
