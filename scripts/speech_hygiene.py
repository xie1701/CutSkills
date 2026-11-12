#!/usr/bin/env python3
"""CutSkills · 口播洁净度：进片的声音里不许有片场残渣。

## 这个脚本挡的是什么

欧阳那两条片，观众听到的是这样的开头：

    「唔系唔系……因为纯手工厨房系大……系因为你嘅根本……o k o k，因为咧，我觉得咧，
     因为有咁 / 我有咁大嘅厨房」

三件事同时发生：口误重来（唔系唔系）、对着摄影师确认收工（o k o k）、
下一句起头被切断。这不是渲染问题，是**选句**问题——按 ASR 句子边界往下挑，
挑到哪个 take 算运气。

还有一处更隐蔽：前段尾巴「因为有咁」+ 后段开头「我有咁大嘅厨房」接出来的回声。
观众以为是音频有回响，其实是切点造的。这一条必须跨段查，单段永远查不出来。

## 判据（逐元素）

  filler   口水词：o k / 诶 / 「我觉得咧」这种没有信息的语气堆叠
  head     半句起头：切在词中间（首词是半个词）判 error；「，就系」起头判 warning
  gap      句中无声 > 0.55s：话说一半断了
  edge     首 >0.40s / 尾 >0.55s 空转：画面在走，没人说话
  retake   重说（复用 cue_guard 的词级检出）

## 判据（跨元素）

  seam     前段尾 6 字 ∩ 后段头 6 字 ≥3 字 → 观众听到同一句说两遍
  topic    相邻三段的实词集合重合 > 0.5 → 三段在讲同一件事

用法：
  speech_hygiene.py --project <项目> [--json] [--fix-hints]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evidence_guard as EG  # noqa: E402

# 没有信息的语气堆叠。「味道 ok」是正常表达，所以只在**独立出现**或**连续 ≥2 个**
# 语气词时才算——单看一个「诶」判不出深浅。
FILLER_PAT = re.compile(
    r"(?:\bo\s*k\b|\bok\b|okay|"
    r"[诶嗯呃]|"
    r"(?:因为|所以|就)咧|我觉得咧|"
    r"咩啊|啦啊|嘞)",
    re.I)
# 起头就是「接着上一句说」的词。不是错，但通常意味着切口吃掉了上半句。
CONT_HEAD = {"然后", "所以", "因为", "就系", "咁", "同埋", "仲有", "同", "即系"}
# 半句起头：首字是这些，说明切在词中间（「房」「包」「嘅」这种不可能起句）。
BAD_HEAD = set("嘅咗啦咁啊呀咧喎啵哋啲")
GAP_BAD = 0.55
HEAD_LEFTOVER = 0.15            # 句中空档
EDGE_HEAD = 0.40          # 开头空转
EDGE_TAIL = 0.55          # 结尾空转
SEAM_N = 6                # 接缝看每边几个字
SEAM_HIT = 3              # 重合几个字算「说两遍」
TOPIC_OVERLAP = 0.50
FUNC = EG.FUNC


def _ok_is_content(txt: str, hit: str) -> bool:
    """「品质 ok」「味道 ok」是正常表达，不是口水。前后两字内沾着评价词就放过。"""
    if hit.lower().replace(" ", "") != "ok":
        return False
    for m in re.finditer(r"ok", txt, re.I):
        win = txt[max(0, m.start() - 3):m.end() + 3]
        if re.search(r"品质|味道|质量|水准|出品|口感", win):
            return True
    return False


def words_in(lib: Path, el: dict) -> list[tuple[str, float]]:
    au = el.get("audio") or {}
    aid = au.get("asset") or el.get("asset")
    i, o = au.get("in"), au.get("out")
    if not aid or i is None or o is None:
        return []
    return [(t, s) for t, s in EG.words_of(lib, aid)
            if float(i) - 0.10 <= s <= float(o) + 0.10]


def core_str(text: str) -> str:
    return "".join(c for c in text if c not in FUNC and EG.CJK.match(c))


def per_element(el: dict, ws: list[tuple[str, float]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    n = el.get("n")
    au = el.get("audio") or {}
    i, o = float(au.get("in") or 0), float(au.get("out") or 0)
    if not ws:
        return out
    txt = "".join(t for t, _ in ws)

    # filler
    hits = [h for h in FILLER_PAT.findall(txt) if not _ok_is_content(txt, h)]
    soft = [h for h in hits if h in "诶嗯呃"]
    # 粤语里「所以咧」「因为咧」是正常的连接词（「所以咧，佢饮茶嘅氛围我认为…」），
    # 单用一次不是废料。只有**堆叠**才算——「因为咧，我觉得咧，因为有咁」这句里
    # 三个语气短语连着出现，那才是主播在找词。判据要能分开这两件事。
    conn = {"因为咧", "所以咧", "就咧", "我觉得咧"}
    conn_n = sum(1 for h in hits if h in conn)
    hard = [h for h in hits if h not in "诶嗯呃"
            and not (h in conn and conn_n < 2)]
    if hard:
        out.append(("error", f"#{n} 口水「{''.join(hard)[:6]}」——这段没有信息，换 take"))
    if soft:
        out.append(("warning", f"#{n} 填充音「{''.join(soft)}」——切掉这个音就行，不必换 take"))

    # head
    first = ws[0][0]
    if first in BAD_HEAD or (len(first) == 1 and first in FUNC):
        out.append(("error", f"#{n} 切在词中间（首字「{first}」不能起句）——前挪到句首"))
    elif first.strip("，,、。.") in CONT_HEAD:
        out.append(("warning", f"#{n} 半句起头（「{first}」）——观众听到的是上一句的尾巴"))
    elif first in ("，", ",") and ws[0][1] - i >= HEAD_LEFTOVER:
        # 词级真值把上一句的逗号标在这个词头上。真要看的是「起点前还有没有声音」：
        # 起点贴着这个词（<0.15s）说明上一句已经说完了，逗号只是 ASR 的句子边界记号。
        out.append(("warning", f"#{n} 起点前还留着 {ws[0][1] - i:.2f}s 上一句的尾巴"))

    # gap / edge
    gaps = []
    for k in range(1, len(ws)):
        g = ws[k][1] - ws[k - 1][1]
        if g > GAP_BAD:
            gaps.append(round(g, 2))
    if gaps:
        out.append(("warning", f"#{n} 话说一半断了（空档 {gaps}）——中间的静默要切掉"))
    if ws[0][1] - i > EDGE_HEAD:
        out.append(("warning", f"#{n} 开头 {ws[0][1] - i:.2f}s 没声音"))
    if o - ws[-1][1] > EDGE_TAIL:
        out.append(("warning", f"#{n} 结尾 {o - ws[-1][1]:.2f}s 没声音"))

    # 尾巴悬空：最后几个词里挂着「因为/所以/就系」，说明下一句的起头被切在窗口外，
    # 观众听到的是半截话接下一段的第一句——欧阳那片「因为有咁 / 我有咁大嘅厨房」
    # 听起来像回声，就是这么来的。
    tail_words = [t for t, _ in ws[-2:]]
    tail_txt = "".join(tail_words)
    ended = bool(re.search(r"[。！？!?]$", "".join(t for t, _ in ws)))
    for c in ("因为", "所以", "但系", "就系", "同埋", "仲有"):
        if not ended and c in tail_txt:
            out.append(("error", f"#{n} 尾巴挂着「{c}」（…{tail_txt[-8:]}）——"
                                 "下一句的起头被切在窗口外，接上下一段就是重复"))
            break
    if ws[-1][0] in BAD_HEAD or (len(ws[-1][0]) == 1 and ws[-1][0] in FUNC):
        out.append(("warning", f"#{n} 尾巴停在虚词「{ws[-1][0]}」上，半截话"))
    if ws[-1][0] in set("因所但就同仲"):
        out.append(("error", f"#{n} 尾巴切在「{ws[-1][0]}…」中间——"
                             "下一句从这儿起头，接上去就是同一句说两遍"))

    # 起句自我否定：「唔系唔系 / 唔对唔对 / 等等」——主播 NG 之后重新起句，
    # 前面这半句是废的，声音里改不掉，只能整段不用。
    if re.match(r"^(唔系唔系|唔对唔对|唔好唔好|等等|慢住|嚟过)", txt):
        out.append(("error", f"#{n} 开口就是「{txt[:4]}」——NG 之后重新起句，前半句是废的"))

    # 自重复：同一句话在里面说了两遍（「空间高先可以坐得舒服，空间感高先会坐得舒服」）。
    # 要跟「列举」分开：「楼面两个门口、厨房两个门口」也是同一串字出现两次，
    # 但那是正常说法。分界看两样——中间隔了多久、隔了多少字。
    # 复述是「说完一遍、停顿、换个说法再说一遍」，间隔 ≥1.2s 且中间夹着 ≥5 个字。
    pos2t: list[float] = []
    for t, s0 in ws:
        pos2t.extend([s0] * max(1, len(t)))
    pos2t = pos2t[:len(txt)]
    dup = None
    for _len in (6, 5, 4):
        seen: dict[str, int] = {}
        for k in range(0, max(0, len(txt) - _len + 1)):
            g = txt[k:k + _len]
            if sum(1 for c in g if c not in FUNC and EG.CJK.match(c)) < 2:
                continue
            if g in seen:
                gap_chars = k - seen[g]
                gap_t = (pos2t[min(k, len(pos2t) - 1)]
                         - pos2t[min(seen[g], len(pos2t) - 1)])
                if gap_chars >= _len + 5 and gap_t >= 1.2:
                    dup = g
                    break
            else:
                seen[g] = k
        if dup:
            break
    if dup:
        out.append(("error", f"#{n} 同一句说了两遍（重复「{dup}」）——只留一遍"))

    # retake
    try:
        import cue_guard
        rec_words = [{"t": t, "start": s, "end": s + 0.25} for t, s in ws]
        cues = cue_guard.find_cues(rec_words)
        rt = [c for c in cues if c.get("kind") in ("retake",)]
        if rt:
            out.append(("error", f"#{n} 重说「{rt[0]['text'][:8]}」——同一句说了两遍，只留一遍"))
    except Exception:                                              # noqa: BLE001
        pass
    return out


def cross(elements: list[dict], allwords: dict[int, list[tuple[str, float]]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k in range(1, len(elements)):
        a, b = elements[k - 1], elements[k]
        wa, wb = allwords.get(a.get("n"), []), allwords.get(b.get("n"), [])
        if not wa or not wb:
            continue
        ta = core_str("".join(t for t, _ in wa))
        tb = core_str("".join(t for t, _ in wb))
        tail, head = ta[-SEAM_N:], tb[:SEAM_N]
        common = [c for c in tail if c in head]
        # 字面重合是硬证据；但更常见的是「前段尾巴挂着一个没说完的接续词」——
        # 「…因为有咁」接「我有咁大嘅厨房」，字面只重合一个「有」，声音上却是同一句说了两遍。
        wa_tail = "".join(t for t, _ in wa[-2:])
        # 「话说完了没有」不看 ASR 给的句号——它会给半截话也补一个句号。
        # 看最后一个词落没落在实词上：段尾只剩虚词（「…我觉得咧。」），就是半截话。
        lands = any(c not in FUNC and EG.CJK.match(c) for t, _ in wa[-2:] for c in t)
        # 还要有**实词重合**才算「同一句说两遍」。粤语里「…嘅，」结尾很常见，
        # 下一句接别的内容很正常（#11「运翻嚟嘅，」接 #12「空间高先可以」就是）。
        last_real = {c for t, _ in wa[-5:] for c in t if c not in FUNC and EG.CJK.match(c)}
        head_real = {c for c in head if c not in FUNC}
        hanging = (not lands) and bool(head) and bool(last_real & head_real)
        if len(set(common)) >= SEAM_HIT or hanging:
            why = (f"前段尾挂着「{wa_tail}」没说完" if hanging
                   else f"共 {len(set(common))} 字重合")
            out.append(("error",
                        f"#{a.get('n')}→#{b.get('n')} 接缝重复：前段尾「{tail}」接后段头「{head}」，"
                        f"{why}——观众听到同一句说两遍（这就是「回音」）"))
    for k in range(2, len(elements)):
        trio = elements[k - 2:k + 1]
        sets = []
        for el in trio:
            w = allwords.get(el.get("n"), [])
            sets.append(set(core_str("".join(t for t, _ in w))))
        if not all(sets):
            continue
        inter = sets[0] & sets[1] & sets[2]
        base = min(len(s) for s in sets) or 1
        if len(inter) / base > TOPIC_OVERLAP and len(inter) >= 6:
            out.append(("warning",
                        f"#{trio[0].get('n')}–#{trio[2].get('n')} 三段在说同一件事"
                        f"（共同实词 {len(inter)} 个：{''.join(sorted(inter))[:12]}）——合并或删一段"))
    return out


def check(project: Path, lib: Path | None = None) -> list[tuple[str, str]]:
    tl = json.loads((project / "timeline.json").read_text())
    lib = lib or project.parents[2]
    els = [e for e in (tl.get("elements") or []) if e.get("kind") == "clip"]
    allwords = {e.get("n"): words_in(lib, e) for e in els}
    out: list[tuple[str, str]] = []
    for e in els:
        out.extend(per_element(e, allwords.get(e.get("n"), [])))
    out.extend(cross(els, allwords))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="口播洁净度检查")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else None
    v = check(proj, lib)
    err = [m for lv, m in v if lv == "error"]
    if a.json:
        print(json.dumps([{"level": l, "msg": m} for l, m in v], ensure_ascii=False, indent=1))
    else:
        for lv, m in v:
            print(f"  {'🔴' if lv == 'error' else '🟡'} {m}")
        print(f"洁净度：error {len(err)} · warning {len(v) - len(err)}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
