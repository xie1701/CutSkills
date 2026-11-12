#!/usr/bin/env python3
"""CutSkills · 卡准入：一张卡凭什么能进片。

## 为什么要有这个闸门

Jerry 2026-11-12 的原话：「srt-motion-cards 的卡还是用的不好，出现的太短，
观众都没有看完就切了，而且也都是生搬硬套的卡，内容关联度都不高，所有的内容
都是要来真实辅助到片子的叙事主线的！这个是核心！」

这三句对应三个可判的事：

1. **出现太短** → 时长闸门。一张卡要读完（标题 + 数字 + 说明 + 出处），
   中文按 7 单位/秒算，20 单位上下就是 2.6 秒起。卡允许跨切点活着，
   但两张卡之间必须空 0.2 秒，否则画面在打架。

2. **生搬硬套** → 词证闸门。卡上的每一个要素（versus 的左右、steps 的每一步、
   term 的词、stat 的数字）都必须在**这句口播的词级真值**里出现过。
   口播里只有「吊水鱼一种」，就不能做成「吊水鱼 VS 渔船海鲜」的对比——
   那半边是我编的。观众会觉得字和话不是一回事，因为确实不是。

3. **关联度不高、不辅助主线** → 主张闸门。卡要么把一个数字翻译成「所以呢」，
   要么把一句行话翻译成人话，要么把一条对比立起来。**复述名词不算**：
   口播说「两百平厨房」，卡再写一遍「两百平的厨房」，观众没得到任何新东西，
   只是把听到的字看了一遍。判据：stat 的 label 必须携带窗口词表里没有的信息量
   （新实词 ≥ 2 个），否则判「复述」。

## 用法

  check_cards.py --project <项目> [--json]

读 out/cards.json（渲染器算出来的真时间）+ timeline.json（卡的内容与归属）
+ 素材库的词级真值。不重算一遍时间——那会和真渲出来的东西分家。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evidence_guard as EG  # noqa: E402
import lexicon  # noqa: E402

CARD_HOLD_MIN = 2.6       # 读完一张**信息卡**的下限（秒）
KEYWORD_HOLD_MIN = 0.9    # 大字只有四五个字，读得快；但也不能闪一下就过
KEYWORD_HOLD_MAX = 2.4
CARD_HOLD_MAX = 4.5       # 再长就是赖着不走
MG_KINDS_CHK = {"stat", "steps", "versus", "term"}
CARD_GAP = 0.20           # 相邻两张卡之间的空档
MIN_LEAD = 0.12           # 卡不许早于它锚定的词（声音在前）
SCREEN_SHARE = 0.25       # 卡在场时间占全片的上限
SEC_PER_CARD = 18.0       # 每多少秒最多一张卡（下限密度）
NEW_REAL = 2              # stat 的 label 至少带来几个窗口里没有的实词
CJK = EG.CJK
FUNC = EG.FUNC


def real_chars(txt: str) -> list[str]:
    return [c for c in str(txt or "") if CJK.match(c) and c not in FUNC]


# 口播说的是「十个人」「两百平」「廿五张」，卡上写的是 10 / 200 / 25。
# 不归一就永远对不上，于是每张卡都报「数字口播里没说过」——那是判据的错，不是卡的错。
_CN_DIGIT = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _cn_span(txt: str) -> set[str]:
    """把一串中文数字换成阿拉伯数字，返回所有可能读法。

    粤语口播里「两百平」「廿五张」「十个人上」都会出现，全部要能对上 200 / 25 / 10。
    """
    out: set[str] = set()
    i = 0
    while i < len(txt):
        if txt[i] in _CN_DIGIT or txt[i] in _CN_UNIT or txt[i] == "廿":
            j, total, section, cur = i, 0, 0, 0
            while j < len(txt) and (txt[j] in _CN_DIGIT or txt[j] in _CN_UNIT
                                   or txt[j] == "廿"):
                ch = txt[j]
                if ch == "廿":
                    section += 20
                elif ch in _CN_DIGIT:
                    cur = _CN_DIGIT[ch]
                elif ch == "万":
                    total += (section + cur) * 10000
                    section, cur = 0, 0
                else:
                    section += (cur or 1) * _CN_UNIT[ch]
                    cur = 0
                j += 1
            total += section + cur
            if total:
                out.add(str(total))
            i = j
        else:
            i += 1
    return out


def has_number(text: str, value: str) -> bool:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return True
    if digits in text:
        return True
    return digits in _cn_span(text)


def words_of_window(lib: Path, el: dict) -> str:
    au = el.get("audio") or {}
    aid = au.get("asset") or el.get("asset")
    i, o = au.get("in"), au.get("out")
    if not aid or i is None or o is None:
        return ""
    txt = "".join(t for t, s in EG.words_of(lib, aid)
                  if float(i) - 0.35 <= s <= float(o) + 0.35)
    # 必须过词表：ASR 把「四大天王」写成「四大轩王」，不过一遍就会判卡错——
    # 卡是对的，错的是转写。判据和字幕必须读同一份真值。
    lex = lexicon.load(lib / "_cutskills")
    for bad, good in (lex.get("terms") or {}).items():
        if bad in txt:
            txt = txt.replace(bad, good)
    return txt


def seg_windows(tl: dict) -> dict[int, tuple[float, float]]:
    """每个元素在成片里的绝对窗口。卡跨格存活时，窗口要一起放宽。"""
    out, t = {}, 0.0
    for e in tl.get("elements") or []:
        dur = float(e.get("dur") or 0)
        if not dur:
            au = e.get("audio") or {}
            ai, ao = au.get("in", e.get("in")), au.get("out", e.get("out"))
            dur = max(0.0, float(ao or 0) - float(ai or 0))
        out[int(e["n"])] = (round(t, 3), round(t + dur, 3))
        t += dur
    return out


def card_texts(c: dict) -> dict[str, list[str]]:
    """卡上要观众读的每一块文字，按类分。"""
    d = c.get("data") or {}
    kind = c.get("kind")
    out: dict[str, list[str]] = {}
    if kind == "stat":
        out["value"] = [str(d.get("value") or "")]
        out["label"] = [str(d.get("label") or "")]
        out["kicker"] = [str(d.get("kicker") or "")]
    elif kind == "versus":
        for side in ("left", "right"):
            s = d.get(side) or {}
            out[side] = [str(s.get("head") or ""), str(s.get("body") or "")]
    elif kind == "steps":
        out["title"] = [str(d.get("title") or "")]
        out["items"] = [str(x) for x in (d.get("items") or [])]
    elif kind == "term":
        out["term"] = [str(d.get("term") or "")]
        out["def"] = [str(d.get("def") or "")]
    elif kind in ("keyword", "behind", "cutout"):
        out["text"] = [str(c.get("text") or "")]
    return out


def audit(proj: Path, lib: Path | None = None) -> list[tuple[str, str]]:
    tl = json.loads((proj / "timeline.json").read_text())
    lib = lib or proj.parents[2]
    total = 0.0
    for e in tl.get("elements") or []:
        au = e.get("audio") or {}
        total += float(e.get("dur") or 0) or max(
            0.0, float(au.get("out", e.get("out") or 0)) - float(au.get("in", e.get("in") or 0)))
    cj = proj / "out" / "cards.json"
    if not cj.exists():
        return [("error", "没有 out/cards.json——先渲一次，卡的时长是渲染器算的")]
    cards = json.loads(cj.read_text())
    cards = cards if isinstance(cards, list) else (cards.get("cards") or [])
    by_seg = {int(e["n"]): e for e in tl.get("elements") or []}
    wins = seg_windows(tl)
    v: list[tuple[str, str]] = []

    if not cards:
        return [("error", "一块卡都没有——先确认 plan_card 的「已使用」清单")]

    # 1. 时长与相邻
    order = sorted(cards, key=lambda c: float(c["start"]))
    for k, c in enumerate(order):
        held = round(float(c["end"]) - float(c["start"]), 3)
        kind = c.get("kind")
        lo = CARD_HOLD_MIN if kind in MG_KINDS_CHK else KEYWORD_HOLD_MIN
        name = f"#{c.get('seg')} {kind}「{str(c.get('text') or (c.get('data') or {}).get('value') or (c.get('data') or {}).get('term') or '')[:10]}」"
        if held < lo:
            v.append(("error", f"{name} 只放 {held:.2f}s——下限 {lo}s，观众没看完就切了"
                               "（信息卡允许跨格活着，别缩在元素里）"))
        if held > CARD_HOLD_MAX + 0.01 and kind in MG_KINDS_CHK:
            v.append(("warning", f"{name} 放了 {held:.2f}s，超过 {CARD_HOLD_MAX}s，"
                                 "该收口了（或拆成两张）"))
        if k + 1 < len(order):
            nxt = order[k + 1]
            gap = round(float(nxt["start"]) - float(c["end"]), 3)
            if gap < 0:
                v.append(("error", f"{name} 和下一张卡重叠 {-gap:.2f}s——画面在打架"))
            elif gap < CARD_GAP - 0.01:
                v.append(("warning", f"{name} 和下一张卡只隔 {gap:.2f}s，太挤"))

    # 2. 不许早于锚词
    for c in cards:
        at, an = c.get("anchor_t"), c.get("anchor_word")
        if an and at is not None and float(c["start"]) < float(at) + MIN_LEAD - 0.02:
            v.append(("error", f"#{c.get('seg')} 卡「{an}」早于锚词 {float(at):.2f}s 就出了"
                               f"（{c['start']}）——声音在前"))

    # 3. 密度与占屏（只算信息卡：大字是节奏点，不是把口播重写一遍）
    mcards = [c for c in cards if c.get("kind") in MG_KINDS_CHK]
    if total > 0:
        share = sum(float(c["end"]) - float(c["start"]) for c in mcards) / total
        if share > SCREEN_SHARE:
            v.append(("error", f"信息卡占了全片 {share*100:.0f}%——上限 {SCREEN_SHARE*100:.0f}%，"
                               "再多就成 PPT 了"))
        cap = max(1, int(total / SEC_PER_CARD) + 1)
        if len(mcards) > cap:
            v.append(("error", f"{len(mcards)} 张信息卡 / {total:.0f}s——按每 {SEC_PER_CARD:.0f}s "
                               f"一张，最多 {cap} 张，多了就只是把口播又写了一遍"))

    # 4. 词证与主张（核心：这张卡说什么，话里到底有没有）
    for c in cards:
        seg = by_seg.get(int(c.get("seg") or 0))
        if not seg:
            v.append(("error", f"卡挂在不存在的段 #{c.get('seg')}"))
            continue
        text = words_of_window(lib, seg)
        if not text:
            v.append(("warning", f"#{c.get('seg')} 窗口里没有词级真值，卡的关联查不了"))
            continue
        chunks = card_texts(c)
        name = f"#{c.get('seg')} {c.get('kind')}"

        if c.get("kind") == "versus":
            for side in ("left", "right"):
                head, body = (chunks.get(side) or ["", ""])[:2]
                for label, s in (("左", head), ("右", body)):
                    rs = real_chars(s)
                    if rs and not any("".join(rs[:2]) in text or rs[0] in text for _ in [0]):
                        v.append(("error", f"{name} versus 的{label}边「{s}」口播里没说过——"
                                           "对比是编的，观众会觉得字和话不是一回事"))
        elif c.get("kind") == "steps":
            for it in chunks.get("items") or []:
                rs = real_chars(it)
                if rs and sum(1 for ch in rs if ch in text) < max(1, len(rs) // 3):
                    v.append(("error", f"{name} steps 这一步「{it}」口播里没有着落——"
                                       "先后顺序是编的"))
        elif c.get("kind") == "term":
            t_ = (chunks.get("term") or [""])[0]
            if real_chars(t_) and not all(ch in text for ch in real_chars(t_)):
                v.append(("error", f"{name} term「{t_}」这几个字口播里没齐——"
                                   "观众听不懂为什么突然解释它"))
        elif c.get("kind") == "stat":
            d = c.get("data") or {}
            val = str(d.get("value") or "")
            if not has_number(text, val):
                v.append(("error", f"{name} 数字 {val} 口播里没说过（中文数字也算过一遍了）"))
            label = (chunks.get("label") or [""])[0]
            fresh = [ch for ch in real_chars(label) if ch not in text]
            if len(fresh) < NEW_REAL:
                v.append(("error", f"{name} 卡的说明「{label}」几乎全是口播原话"
                                   f"（新信息只有 {len(fresh)} 字）——这是复述，不是辅助叙事"))
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="卡准入检查")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else None
    v = audit(proj, lib)
    err = [m for lv, m in v if lv == "error"]
    if a.json:
        print(json.dumps([{"level": l, "msg": m} for l, m in v], ensure_ascii=False, indent=1))
    else:
        for lv, m in v:
            print(f"  {'🔴' if lv == 'error' else '🟡'} {m}")
        print(f"卡准入：error {len(err)} · warning {len(v) - len(err)}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
