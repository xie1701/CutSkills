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


def apply(words: list[dict], lex: dict) -> tuple[list[dict], list[dict]]:
    """在字级时间轴上做最长匹配替换，替换后的字继承原字的时间跨度。

    为什么要拆到字：替换前后字数常常不同（「石灰机」→「石灰基」同长，
    但「美制」→「镁质」这类不保证），按词替换会让时间戳整体错位，字幕就对不上了。
    """
    terms = [(k, v) for k, v in (lex.get("terms") or {}).items() if k and v]
    if not terms:
        return words, []
    terms.sort(key=lambda kv: -len(kv[0]))

    chars: list[list] = []                     # [字, 起始, 结束]
    for w in words:
        t = w.get("t") or ""
        if not t:
            continue
        n = len(t)
        span = (float(w["end"]) - float(w["start"])) / n
        for i, ch in enumerate(t):
            chars.append([ch, round(float(w["start"]) + span * i, 3),
                          round(float(w["start"]) + span * (i + 1), 3)])
    text = "".join(c[0] for c in chars)
    protected = _spans(text, lex.get("protect") or [])

    out: list[list] = []
    fixes: list[dict] = []
    i = 0
    while i < len(chars):
        hit = None
        for bad, good in terms:
            if text.startswith(bad, i) and not _overlaps(protected, i, i + len(bad)):
                hit = (bad, good)
                break
        if hit is None:
            out.append(chars[i])
            i += 1
            continue
        bad, good = hit
        st, en = chars[i][1], chars[i + len(bad) - 1][2]
        span = max((en - st) / max(len(good), 1), 0.01)
        for k, ch in enumerate(good):
            out.append([ch, round(st + span * k, 3), round(st + span * (k + 1), 3)])
        fixes.append({"from": bad, "to": good, "at": st})
        i += len(bad)

    return [{"t": c[0], "start": c[1], "end": c[2]} for c in out], fixes


def seed_template() -> dict:
    return {
        "version": 1,
        "updated": None,
        "why": "每条都是一个人做过的判断，不是机器猜的。改之前先看 transcript-fix.md 的上下文。",
        "source": [],
        "terms": {},
        "protect": [],
    }
