#!/usr/bin/env python3
"""CutSkills · 转写语义纠错（词表层）。

ASR 只保证「听起来像」，不保证「写对了」。品牌名、专业术语、人名，同一个音在字上
可能有四五个候选，本地模型会挑错——柒造这一库里，「瑞加」被写成「瑞家/瑞佳」5 次、
一次都没对；「石灰基」30 次全写成「石灰机」。

纠错不能靠模型自己猜，要靠背景：品牌语料手册、历史修正、客户给的标准写法。
流程是三步，职责分开：

  1. `index_assets.py --stage lint`  → 出 `_cutskills/transcript-fix.md`（脚本只统计，不下判断）
  2. 人/大模型读语料 + 上下文，把决定写进 `_cutskills/lexicon.json`（判断在这里）
  3. `index_assets.py --stage rephrase` → 按词表重算句子（不重跑 ASR，秒级）

词表是库级资产，跟着库走，跨项目复用；新建项目不用重新纠一遍。
"""
from __future__ import annotations

import json
from pathlib import Path

SENTINEL = "\x00"   # ASR 句界哨兵，不进字幕
# 单字拖音：ASR 把它们当真字，字幕上就成了「诶两百平嘅厨房啊」「我哋嘅诶四大轩王」。
# 只收**纯粹是停顿声**的几个（诶欸哎呃嗯）；「啊呀哦噢喔」是句尾语气词，
# 说话人真的在用它收句，删掉字幕反而断了语气。
NOISE_CHARS = set("诶欸哎呃嗯")
NOISE_PHRASES = ("喺喺",)   # 连着念两次同一个介词的卡顿


def strip_noise(text: str) -> str:
    """从**字幕文本**里剔掉拖音。只动字幕：真值（词级时间戳）一个字不改，
    否则 evidence_guard 会判「字幕上出现的字音频里没说过」。
    """
    t = str(text or "")
    for ph in NOISE_PHRASES:
        t = t.replace(ph, ph[0])
    return "".join(ch for ch in t if ch not in NOISE_CHARS)


def load(sysdir: Path) -> dict:
    p = Path(sysdir) / "lexicon.json"
    if not p.exists():
        return {"version": 1, "terms": {}, "protect": []}
    try:
        lex = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"version": 1, "terms": {}, "protect": []}
    lex.setdefault("terms", {})
    lex.setdefault("protect", [])
    return lex


def _spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    out = []
    for t in terms:
        if not t:
            continue
        i = text.find(t)
        while i >= 0:
            out.append((i, i + len(t)))
            i = text.find(t, i + 1)
    return out


def _overlaps(spans: list[tuple[int, int]], a: int, b: int) -> bool:
    return any(a < e and s < b for s, e in spans)


def replace_chars(chars: list[list], lex: dict) -> tuple[list[list], list[dict]]:
    """在字级序列上做最长匹配替换。

    `chars` 是 `[字, 起始, 结束]` 的列表；替换后的字继承原字的时间跨度（按字数均分）。
    为什么非要拆到字：替换前后字数常常不同（「石灰机」→「石灰基」同长，
    「物为不叫造为」→「物为什么不叫造漆」就不保证），按词替换会让时间戳整体错位，
    字幕就对不上了。

    `\x00` 是 ASR 句界哨兵（subs.py 用它断呼吸组）。匹配时**当它不存在**——
    2026-11-09 实测：「我们把涂料\x00吧油漆作为原料」里那条「涂料吧油漆」跨了句界，
    哨兵把词劈成两半，词表就静默不生效，字幕上照样印着「涂料吧油漆」。
    命中区间内的哨兵一并丢掉（替换文本里已经带了该有的断句）。
    """
    terms = [(k, v) for k, v in (lex.get("terms") or {}).items() if k and v]
    if not terms or not chars:
        return [list(c) for c in chars], []
    terms.sort(key=lambda kv: -len(kv[0]))
    idx = [i for i, c in enumerate(chars) if c[0] != SENTINEL]
    text = "".join(chars[i][0] for i in idx)
    protected = _spans(text, lex.get("protect") or [])

    hits: dict[int, tuple[int, str]] = {}
    i = 0
    while i < len(text):
        bad = None
        for b_, g_ in terms:
            if text.startswith(b_, i) and not _overlaps(protected, i, i + len(b_)):
                bad = (b_, g_)
                break
        if bad:
            hits[i] = (i + len(bad[0]), bad[1])
            i += len(bad[0])
        else:
            i += 1
    if not hits:
        return [list(c) for c in chars], []

    out: list[list] = []
    fixes: list[dict] = []
    p, ti = 0, 0
    while p < len(chars):
        ch = chars[p]
        if ch[0] == SENTINEL:
            out.append(list(ch))
            p += 1
            continue
        if ti in hits:
            b, good = hits[ti]
            st = chars[p][1]
            en = chars[idx[b - 1]][2]
            span = max((en - st) / max(len(good), 1), 0.01)
            for k, c_ in enumerate(good):
                out.append([c_, round(st + span * k, 3), round(st + span * (k + 1), 3)])
            fixes.append({"from": text[ti:b], "to": good, "at": st})
            while p < len(chars) and ti < b:
                if chars[p][0] != SENTINEL:
                    ti += 1
                p += 1
            continue
        out.append(list(ch))
        p += 1
        ti += 1

    return out, fixes


def apply(words: list[dict], lex: dict) -> tuple[list[dict], list[dict]]:
    """词表版 replace_chars：吃 ASR 的词列表，吐纠错后的词列表。"""
    chars: list[list] = []
    for w in words:
        t = w.get("t") or ""
        if not t:
            continue
        n = len(t)
        span = (float(w["end"]) - float(w["start"])) / n
        for i, ch in enumerate(t):
            chars.append([ch, round(float(w["start"]) + span * i, 3),
                          round(float(w["start"]) + span * (i + 1), 3)])
    chars, fixes = replace_chars(chars, lex)
    return [{"t": c[0], "start": c[1], "end": c[2]} for c in chars], fixes


def fix_items(items: list[tuple], lex: dict) -> list[tuple]:
    """`[(字, 起, 止), …]` → 同结构。字幕、音频块、evidence 全走这一条。"""
    if not items:
        return items
    chars, _ = replace_chars([list(it) for it in items], lex)
    return [(c[0], c[1], c[2]) for c in chars]


def fix_text(text: str, lex: dict) -> str:
    """纯文本版（没有字级时间戳的场合，比如 evidence、看板口播原文）。"""
    if not text:
        return text
    chars = [[ch, float(i), float(i + 1)] for i, ch in enumerate(text)]
    chars, _ = replace_chars(chars, lex)
    return "".join(c[0] for c in chars)


def fix_timeline(tl: dict, lex: dict) -> int:
    """把词表（与拖音清单）落到**时间轴的 evidence 上**，返回改了几条。

    以前只有字幕走词表，evidence 还是 ASR 原字，于是同一屏上：
    字卡写「四大天王」、字幕写「四大轩王」——两份真值自己打自己。
    """
    n = 0
    for e in tl.get("elements") or []:
        ev = e.get("evidence") or ""
        if not ev:
            continue
        new = strip_noise(fix_text(ev, lex))
        if new != ev:
            e["evidence_was"] = e.get("evidence_was") or ev
            e["evidence"] = new
            n += 1
    return n


def violations(texts, lex: dict) -> list[tuple[str, str]]:
    """词表里的「错写法」还出现在哪条文本里。验收用——字面检查比人眼可靠。"""
    bad = [k for k in (lex.get("terms") or {}) if k]
    hits: list[tuple[str, str]] = []
    for t in texts:
        tt = t or ""
        for b in bad:
            if b in tt:
                hits.append((b, tt))
    return hits


def seed_template() -> dict:
    return {
        "version": 1,
        "updated": None,
        "why": "每条都是一个人做过的判断，不是机器猜的。改之前先看 transcript-fix.md 的上下文。",
        "source": [],
        "terms": {},
        "protect": [],
    }
