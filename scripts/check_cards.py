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
# 大字是把这句的话放大，不是另写一句：它上面的每个字都得是他说的。
# 「山上捉嘅，唔使腌」判红，因为他只说了「从山上入边捉翻嚟嘅鸡」，
# 「唔使腌」是编辑自己补的事实——观众会把卡当他的话。
# 虚字与常见连接词豁免（大字为了排版会省字）。
KW_EXEMPT = FUNC | set("就都系是有喺同而但所以如果不过仲还又先至")
# stat 的 label 是「所以呢」式的翻译，允许用口播里没有的词。但**不许凭空断言事实**：
# 时间承诺、经营扩张、销量利润这类，话里没有就是编的。
FORBIDDEN_CLAIM = ("明年", "下个月", "下一年", "今年底", "年底", "即将", "将会",
                   "将要", "计划开", "准备开", "打算开", "已经签", "签了", "分店",
                   "加盟", "连锁", "上市", "融资", "销量", "利润", "翻倍", "营业额")


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


def _nums(txt: str) -> list[int]:
    """把一段话里所有数字挖出来（阿拉伯 + 中文），用来做「这个数能不能算出来」。"""
    out = [int(x) for x in __import__("re").findall(r"\d+", str(txt or ""))]
    for s in _cn_span(str(txt or "")):
        out.append(int(s))
    return out


def derivable(num: int, truth: str) -> bool:
    """`num` 是不是这句话里的数，或者能由它们四则运算得到。

    「三百万 / 六百平 = 每平方米五千」是可算的（a 片 #1 的卡）；
    「第二家铺位」算不出来——那是凭空多出来的一个数。
    """
    ns = [n for n in _nums(truth) if n > 0]
    if not ns:
        return False
    if any(abs(num - n) <= max(1, n * 0.01) for n in ns):
        return True
    for a in ns:
        for b in ns:
            for r in (a + b, a - b, a * b, (a / b) if b else None):
                if r is not None and abs(num - r) <= max(1, abs(r) * 0.02):
                    return True
    return False


def has_number(text: str, value: str) -> bool:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return True
    if digits in text:
        return True
    return digits in _cn_span(text)


def words_of_window(lib: Path, el: dict) -> tuple[str, str]:
    """窗口内的口播真值（过词表）+ 真值来源（`EG.WORD` / `EG.SENTENCE` / `EG.NONE`）。

    查表一律走 `EG.chars_of_asset`（新键 `h12(id)` + 老键 `legacy_key` 都查），
    不在这里自己拼缓存路径——拼两份迟早只改一份，改漏的那份会静默退句级。
    句级真值只够看字面，判「大字里的字这句口播里有没有」会因窗口错位假红，
    所以调用方拿到 `SENTENCE` 时不许硬判。
    """
    au = el.get("audio") or {}
    aid = au.get("asset") or el.get("asset")
    i, o = au.get("in"), au.get("out")
    if not aid or i is None or o is None:
        return "", EG.NONE
    txt, src = EG.chars_of_asset(lib, aid).text_in(float(i), float(o), 0.35)
    # 必须过词表：ASR 把「四大天王」写成「四大轩王」，不过一遍就会判卡错——
    # 卡是对的，错的是转写。判据和字幕必须读同一份真值。
    lex = lexicon.load(lib / "_cutskills")
    for bad, good in (lex.get("terms") or {}).items():
        if bad in txt:
            txt = txt.replace(bad, good)
    return txt, src


def film_text(lib: Path, tl: dict) -> tuple[str, list[str]]:
    """全片口播真值 + 退级的那些段在哪。只用来判「这个词片子里到底存不存在」。

    卡上的释义可以解释的是**影片里说过的那个词**，而不是从词典里搬一套标准答案过来。
    局限也要认：拿全片而不是拿本句，是一条故意放松的口子——本句窗口只有一句，
    而解释一个行话往往要用到隔壁那句里说过的事（比如「四大天王」的四样分别在两句里）。
    """
    txt, notes = "", []
    for e in tl.get("elements") or []:
        t, src = words_of_window(lib, e)
        txt += t
        note = EG.degrade_note(e.get("n"), src)
        if note:
            notes.append(note)
    return txt, notes


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

    # 0. 两份真值源得先对上。卡的内容写在 timeline.json，时长是渲染器算出来写进
    # cards.json 的。只改时间轴不重渲（或 cards.json 是旧的），以前这一段整段是瞎的：
    # 把 label 改成「这家店明年就要开分店」，error 仍然是 0。
    # 对不上的卡只报一次「重渲」，不再拿旧卡的内容去跑后面四条判据。
    # 以前这里报完「没重渲」接着又拿 out/cards.json 里的旧卡逐条查词证，于是
    # 一张早就改掉的旧卡能刷出五六条「口播里没说过」——编辑看到一屏红，
    # 分不清哪条是真错、哪条只是没重渲。两份真值源打架时，只说打架这一件事。
    stale: set[int] = set()
    orphan: set[int] = set()
    for c in cards:
        seg = int(c.get("seg") or 0)
        tc = (by_seg.get(seg) or {}).get("card") or {}
        if not tc:
            orphan.add(seg)
            v.append(("error", f"#{seg} 已渲的卡在时间轴上已经没有了——重渲一次就清掉"))
            continue
        for key in ("kind", "text"):
            if str(tc.get(key) or "") != str(c.get(key) or ""):
                stale.add(seg)
                v.append(("error", f"#{seg} 的卡 {key} 两份真值源对不上"
                                   f"（timeline「{str(tc.get(key) or '')[:16]}」/"
                                   f"已渲「{str(c.get(key) or '')[:16]}」）——改完卡记得重渲"))
        if (tc.get("data") or {}) != (c.get("data") or {}):
            stale.add(seg)
            v.append(("error", f"#{seg} 的卡 data 两份真值源对不上——"
                               "时间轴上的卡改了但没重渲，屏幕上还是旧卡"))
    skip = stale | orphan

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
        if int(c.get("seg") or 0) in skip:
            continue
        at, an = c.get("anchor_t"), c.get("anchor_word")
        if an and at is not None and float(c["start"]) < float(at) + MIN_LEAD - 0.02:
            v.append(("error", f"#{c.get('seg')} 卡「{an}」早于锚词 {float(at):.2f}s 就出了"
                               f"（{c['start']}）——声音在前"))

    # 3. 密度与占屏（只算信息卡：大字是节奏点，不是把口播重写一遍）
    mcards = [c for c in cards if c.get("kind") in MG_KINDS_CHK
              and int(c.get("seg") or 0) not in skip]
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
    # 全片文本只拼一次（原来每张卡都重生拼一遍）；拼的时候有段只到句级要说出来。
    film, film_notes = film_text(lib, tl)
    if film_notes:
        v.append(("warning", f"全片有 {len(film_notes)} 段没有词级真值，释义比对只能跟着句级走"
                               f"（{film_notes[0]}）"))
    for c in cards:
        if int(c.get("seg") or 0) in skip:
            continue
        seg = by_seg.get(int(c.get("seg") or 0))
        if not seg:
            v.append(("error", f"卡挂在不存在的段 #{c.get('seg')}"))
            continue
        text, src = words_of_window(lib, seg)
        if src != EG.WORD:
            # 查不到词级真值就如实说「查不了」——退到句级去硬判只会把窗口错位
            # 当成「卡上的字是编的」（T-030：protect-01 五条卡全落在这条上）。
            why = ("这一格没写 audio 的 asset/in/out，取不到声音窗口" if src == EG.NONE
                   else "只查得到句级真值，没有逐字时间，硬判会误判")
            v.append(("warning", f"#{c.get('seg')} 窗口里没有词级真值，卡的关联查不了（{why}）"))
            continue
        if not text:
            v.append(("warning", f"#{c.get('seg')} 窗口里没有词，卡的关联查不了"))
            continue
        chunks = card_texts(c)
        name = f"#{c.get('seg')} {c.get('kind')}"

        if c.get("kind") in ("keyword", "behind", "cutout"):
            # 大字就是这句的话被放大。出现这句里没有的字，就是替他说了他没说的话。
            t_ = (chunks.get("text") or [""])[0]
            stray = [ch for ch in t_ if CJK.match(ch) and ch not in KW_EXEMPT and ch not in text]
            if stray:
                v.append(("error", f"{name} 大字「{t_}」里的「{''.join(stray)}」这句口播里没有——"
                                   "大字是把他这句话放大，不是替他加一句"))
        elif c.get("kind") == "versus":
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
            # 释义可以带常识（「广式早茶的四样招牌」），但**具体列举的东西**必须真存在。
            # b#5 口播「虾饺排骨凤爪同埋叉烧包」，卡上列「虾饺、烧卖、叉烧包、蛋挞」——
            # 同一屏，卡在纠正主播，观众不知道该信哪个。
            # 只查「、」分隔的那段列举，不查 —— 后面的解释句（那是该卡的职责）。
            dfn = (chunks.get("def") or [""])[0]
            head = __import__("re").split(r"——|—|这类|这种|这些|等等|等[^、]", dfn)[0]
            for item in __import__("re").split(r"[、，,/／]", head):
                rs = real_chars(item)
                if len(rs) < 2:
                    continue
                missing = [ch for ch in rs if ch not in film]
                if missing:
                    v.append(("error", f"{name} 释义里举的「{item.strip()}」整部片子里没说过"
                                       f"（没出现的字：{''.join(missing)}）——"
                                       "卡上列的不是他说的那几样"))
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
            # 反方向：新信息也不能是**凭空的事实断言**。
            # 「新实词够多」只证明它没在复述，不证明它是真的——
            # 「这家店明年就要开分店」新词一堆，同样是编的。
            for bad in FORBIDDEN_CLAIM:
                if bad in label and bad not in text:
                    v.append(("error", f"{name} 卡的说明里写了「{bad}」——这句口播里没有这句话，"
                                       "片子的素材里也没有这回事"))
            for n in _nums(label):
                if not derivable(n, text):
                    v.append(("error", f"{name} 卡的说明里的数字 {n} 算不出来"
                                       "——口播的这句话里没有它，也推不出来"))
                    break
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
