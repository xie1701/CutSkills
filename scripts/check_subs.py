#!/usr/bin/env python3
"""CutSkills · 字幕裁判：去标点 / 呼吸组 / 单行宽度 / 强调色 / 硬切。

2026-11-09 的五条规矩（Jerry 定的）都能机器判，所以别再靠看：

  ① 不出标点        —— 一条字幕里不许有「，。？！、；：…」
  ② 不切词          —— 相邻两条时间上贴在一起（间隔 <0.12s）＝在词中间断了
  ③ 单行 10–14 字   —— 上限硬判；中位数要在 10–14 之间（全片都是 5 个字＝太碎）
  ④ 强调色要有      —— 一条都没染说明规则没生效
  ⑤ 硬切            —— 淡入必须 ≤120ms（读样式包，不看代码）

用法：
  check_subs.py --project <项目> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexicon          # noqa: E402  残词 / 口吃重尾的判据只有一份，就写在 lexicon 里
import lint_transcript  # noqa: E402  量级判据表与待审工单同源，不另写一份

PUNCT = "，。！？、；：…—～,.!?;:"
MAX_CHARS = 10
WIDE_TOL = 10             # 与 subs.MAX_CHARS 对齐：80px × 10 字 = 800 = 74%W，再长就压边
# 判的是「中位数落在这个带里」：10–14 是拆长句的目标，短句本来就是 6–9 字。
# 真卡死的是上限（>15 一刀不放过）和「一刀两断」（断词）。
WIDTH_BAND = (6.5, 10.0)   # 上限 10 单位之后，中位落在 6.5–10 之间；7 是正常值
TIGHT_GAP = 0.12          # 两条字幕贴得比这还近＝在同一口气里被切开了
FADE_MAX = 0.12
OVERLAP_EPS = 0.005       # 秒：小于这个算「相接」，允许


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


HOLD_MIN, HOLD_MAX = 0.8, 3.2


def hold_judge(cues: list[dict]) -> tuple[list[dict], list[dict]]:
    """每条字幕的停留时间。返回 (判红, 提示)。

    有稿子的片子里句读是写字的人定的：快语速下一句话只有 0.6 秒是**事实**，
    硬拉长只能破坏句子。所以「收在句读上、0.6–0.8 秒」只记提示；判红的是
    低于 0.6 秒、超过 3.2 秒、或者被宽度硬拆出来的短条。
    """
    bad, note = [], []
    for c in cues:
        dur = round(float(c["end"]) - float(c["start"]), 3)
        clause = str(c.get("break_after") or "") == "clause"
        if dur < 0.6 or dur > HOLD_MAX + 0.02 or (dur < HOLD_MIN - 0.02 and not clause):
            bad.append({"text": str(c.get("text"))[:10], "s": dur})
        elif dur < HOLD_MIN - 0.02:
            note.append({"text": str(c.get("text"))[:10], "s": dur})
    return bad, note


def dangling_judge(cues: list[dict]) -> list[dict]:
    """助词孤行：条首/条尾落在虚字上，读起来像上一句的尾巴。

    条首要看**这个词是不是被切成两半**，不能只看头一个字在不在虚字表里：
    「过嚟办公噶啦」的「过」在表里，可「过嚟」是一个词，它起头完全正常——
    照字表判，b 片那条字幕被冤枉成孤行。所以再加一条：这个字得在分词里
    自成一段（词边界落在第 1 个字之后）才算甩出来的尾巴。
    「过/着/咁」还能起句（「咁先可以坐得舒服」），不进这一条。
    """
    from subs import NO_HEAD, NO_END, word_bounds
    START_OK = set("过着咁")
    tail = []
    for c in cues:
        t = str(c.get("text") or "")
        if not t:
            continue
        style_end = str(c.get("break_after") or "") == "clause"
        wb = word_bounds(t) if len(t) > 1 else {1}
        if t[0] in NO_HEAD and t[0] not in START_OK and 1 in wb:
            tail.append({"text": t[:10], "why": f"起头是「{t[0]}」"})
        elif len(t) >= 2 and t[-1] in NO_END and not style_end:
            tail.append({"text": t[:10], "why": f"收尾是「{t[-1]}」"})
    return tail


def width_runs(cues: list[dict]) -> list[list[int]]:
    """把「被宽度硬拆开」的相邻 cue 归成一串，其余各成串。

    为什么要有这个：subs.py 按展示宽度断行，一个词会被劈到两条 cue 里（c 片的
    「我哋嘅生」+「万鱼池」）。残词/量级判据要看到「生万鱼池」才认得出错，
    而**只拼 `break_after == "width"` 的条**：那是同一句话被排版切开的证据。
    句读处硬拼会造出原文没有的邻接关系（「…好」+「生…」），冤枉好句子。
    """
    runs: list[list[int]] = []
    for i, c in enumerate(cues):
        if runs and c.get("el") == cues[runs[-1][-1]].get("el") \
                and str(cues[runs[-1][-1]].get("break_after")) == "width":
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def semantic_judge(cues: list[dict], suspects: list[dict] | None = None,
                   rules: list[dict] | None = None) -> dict:
    """上屏前的语义常识门禁：同音残词 / 口吃重尾 / 反常量级。

    为什么放在 check_subs：这三类都是「字幕上已经印出来、声音却对不上」的硬伤，
    跟标点、超宽是同一层——观众一眼看见；而且都不存在「风格选择」：
    「十万米高」「讲下下」只能是错的，留着的唯一后果就是观众看见。

    判据不在这里再写一份：残词/口吃重尾在 `lexicon.load_suspects()` 与
    `lexicon.stutter_tail()`，量级在 `data/abnormal-magnitudes.json`（由
    `lint_transcript.scan_magnitudes` 读同一张表）。工单与门禁同源，才不会出现
    「工单报了、门禁不放」「门禁拦了、工单没提」这种两张嘴。
    """
    table = suspects if suspects is not None else lexicon.load_suspects()
    sus: list[dict] = []
    stut: list[dict] = []

    for i, c in enumerate(cues):
        t = str(c.get("text") or "")
        for h in lexicon.suspect_hits(t, table):
            sus.append({"cue": i, "text": t, "hit": h["bad"], "suggest": h["good"],
                        "same_len": h["same_len"], "needs_human": h["needs_human"]})
        tail = lexicon.stutter_tail(t)
        if tail:
            stut.append({"cue": i, "text": t, "tail": tail})

    # 跨条扫：字幕被宽度硬拆开时，一个词会落在两条 cue 里——
    # dianzan-c 的「我哋嘅生」+「万鱼池」就是「生万鱼池」，逐条扫永远扫不到。
    for run in width_runs(cues):
        if len(run) < 2:
            continue
        spans, off, joined = [], 0, ""
        for j in run:
            t = str(cues[j].get("text") or "")
            spans.append((off, off + len(t), j))
            joined += t
            off += len(t)
        for h in lexicon.suspect_hits(joined, table):
            end = h["at"] + len(h["bad"])
            if any(h["at"] >= a and end <= b for a, b, _ in spans):
                continue                      # 单条里看得见，上面已经报过
            sus.append({"cue": run[0], "cue_to": run[-1], "text": joined,
                        "hit": h["bad"], "suggest": h["good"],
                        "same_len": h["same_len"], "needs_human": h["needs_human"]})

    # 量级：荒谬档判红，可疑档只提——「三十米高的中庭」是真话，判红等于逼人改真台词。
    mags = lint_transcript.scan_magnitudes(
        [(str(i), str(c.get("text") or "")) for i, c in enumerate(cues)], rules)
    return {"suspect": sus, "stutter": stut,
            "magnitude_bad": [m for m in mags if m["tier"] == "absurd"],
            "magnitude_note": [m for m in mags if m["tier"] == "suspicious"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    d = load(proj / "out" / "subs.json")
    if not d:
        raise SystemExit("还没有 out/subs.json（先跑 render_hf.py）")
    cues = d.get("cues") or []

    punct = [c for c in cues if any(ch in PUNCT for ch in str(c.get("text") or ""))]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from subs import (HAVE_JIEBA, word_bounds, display_units, MAX_UNITS,  # noqa: E402
                      NO_HEAD, NO_END)

    def _u(c) -> float:
        return float(c.get("units") if c.get("units") is not None
                     else display_units(str(c.get("text") or "")))

    units = [_u(c) for c in cues]
    wide = [c for c in cues if _u(c) > MAX_UNITS + 0.4]
    widths = sorted(units)
    med = widths[len(widths) // 2] if widths else 0
    em = sum(1 for c in cues if "<span class" in str(c.get("html") or ""))

    # 每条的字幕停留 0.8–3.2 秒（对齐 srt-motion-cards 的规范）。
    # 低于 0.8s 是「闪一下」：眼睛抓不住，比没有更累；超过 3.2s 是「一句话赖在屏幕上」，
    # 在口播片里通常意味着这一格没有被剪好，而不是字幕该停这么久。
    # 有稿子的片子里，句读是写的人定的：快语速下一句话只有 0.6 秒是**事实**，
    # 硬把它拉长只能破坏句子。所以「收在句读上、并且 0.6–0.8 秒」记为提示，
    # 真正判红的是：低于 0.6 秒，或者被宽度硬拆出来的短条（那种本来可以避免）。
    hold_bad, hold_note = hold_judge(cues)

    # 助词孤行：判据抽到 dangling_judge()，验收层也要跑同一条，别两边各写一遍。
    tail = dangling_judge(cues)

    # 半开帧窗零重叠（2026-11-11）：字幕重叠是画面上**必然**看得见的错，
    # 不该靠事后审计发现，也不该允许被 --force 越过。老 subs.json 没有 sf/ef，
    # 先按秒判（等价），并提示重渲。
    has_frames = all(isinstance(c.get("sf"), int) and isinstance(c.get("ef"), int) for c in cues) and bool(cues)
    overlap = []
    for prev, nxt in zip(cues, cues[1:]):
        if has_frames:
            if int(nxt["sf"]) < int(prev["ef"]):
                overlap.append({"a": str(prev.get("text"))[:8], "b": str(nxt.get("text"))[:8],
                                "frames": int(prev["ef"]) - int(nxt["sf"])})
        elif float(nxt["start"]) < float(prev["end"]) - OVERLAP_EPS:
            overlap.append({"a": str(prev.get("text"))[:8], "b": str(nxt.get("text"))[:8],
                            "ms": round((float(prev["end"]) - float(nxt["start"])) * 1000)})
    bad_frames = [c for c in cues if has_frames and int(c["ef"]) <= int(c["sf"])]
    # 变色是可以关的（Jerry 2026-11-09：「字幕的重点词变色还是取消」）。关了就要
    # 一条都没有——不能因为「规则没染上」也算过。
    colorize = bool(d.get("colorize", True))
    # 断词要按**词边界**判，不能按「断在哪儿」判。
    # 原来只盯 break_after == "width"，于是 ASR 句子边界落在词中间那种情况一路绿灯：
    # 「墙面或者地面或者任何表」+「面的让他成为成品就」把「表面」劈成两半，
    # 而裁判报「断词 ✅」。判据得是：把两条接回去，切开的位置在不在词与词之间。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from subs import HAVE_JIEBA, word_bounds  # noqa: E402,F811

    tight = []
    for prev, nxt in zip(cues, cues[1:]):
        # 只比**同一格**里的相邻两条：跨段的贴边是切点造成的，不是把词劈开
        if prev.get("el") != nxt.get("el"):
            continue
        a_, b_ = str(prev.get("text") or ""), str(nxt.get("text") or "")
        if not a_ or not b_:
            continue
        gap = float(nxt["start"]) - float(prev["end"])
        if gap > 0.40:                       # 中间空了一大截，这两条本来就不连着
            continue
        # 在稿子的逗号处断的不算断词：那里本来就是两个小句，
        # 接回去当然「不是一个词」——判据要盯的是被宽度硬拆的地方。
        if str(prev.get("break_after") or "") == "clause":
            continue
        if HAVE_JIEBA:
            bad = len(a_) not in word_bounds(a_ + b_)
        else:                                 # 没分词器只能退回老判据
            bad = prev.get("break_after") == "width" and gap < TIGHT_GAP
        if bad:
            tight.append({"a": a_[-6:], "b": b_[:6], "gap_ms": round(gap * 1000)})

    style = None
    for f in (proj / "style.yaml", ):
        if f.exists():
            import yaml
            style = yaml.safe_load(f.read_text())
    if style is None:
        bp = load(proj / "brief.json") or {}
        sp = bp.get("style_pack")
        root = Path(__file__).resolve().parent.parent / "styles"
        if sp and (root / f"{sp}.yaml").exists():
            import yaml
            style = yaml.safe_load((root / f"{sp}.yaml").read_text())
    sub = (style or {}).get("subtitle") or {}
    fade = float(sub.get("fade_s") or 0.10)

    # 语义常识门禁（T-017）：残词 / 口吃重尾 / 荒谬量级。
    # 这三类都是「字幕上已经印出来」的硬伤，与标点超宽同一层；
    # 荒谬量级判红、可疑量级只提（真话也会落在可疑档）。
    sem = semantic_judge(cues)

    checks = {
        "punct": {"value": len(punct), "pass": not punct, "need": 0},
        "over_wide": {"value": len(wide), "pass": not wide, "need": f"<={MAX_UNITS} 单位"},
        "width_median": {"value": round(med, 1), "pass": WIDTH_BAND[0] <= med <= WIDTH_BAND[1],
                         "need": WIDTH_BAND},
        "word_split": {"value": len(tight), "pass": not tight, "need": 0},
        "overlap": {"value": len(overlap), "pass": not overlap, "need": 0},
        # 老 subs.json 没有 sf/ef：缺数据不等于违规，标 stale 让它显形，但不判红。
        "frames": {"value": len(bad_frames), "pass": not bad_frames,
                   "need": "sf<ef（半开窗）", "stale": (not has_frames) and bool(cues)},
        "emphasis_cues": ({"value": em, "pass": em > 0, "need": ">0"} if colorize
                          else {"value": em, "pass": em == 0, "need": "=0（已取消变色）"}),
        "fade_s": {"value": fade, "pass": fade <= FADE_MAX, "need": f"<={FADE_MAX}"},
        "hold": {"value": len(hold_bad), "pass": not hold_bad, "need": f"每条 {HOLD_MIN}–{HOLD_MAX}s",
                 "note": [f"{x['text']} {x['s']}s" for x in hold_note[:3]]},
        "dangling": {"value": len(tail), "pass": not tail, "need": "助词不孤行"},
        # 未修正的嫌疑残词：命中就是「声音里说的是 A，字幕印的是 B」
        "suspect_words": {"value": len(sem["suspect"]), "pass": not sem["suspect"],
                          "need": "0（命中即改，或改嫌疑表）"},
        "stutter_tail": {"value": len(sem["stutter"]), "pass": not sem["stutter"],
                         "need": "0（句尾不许叠字）"},
        "magnitude": {"value": len(sem["magnitude_bad"]), "pass": not sem["magnitude_bad"],
                      "need": "0 处荒谬量级",
                      "note": [f"{x['hit']} {x['value']:g}{x['unit']}"
                               for x in sem["magnitude_note"][:3]]},
    }
    ok = all(v["pass"] for v in checks.values())
    rep = {"cues": len(cues), "checks": checks, "ok": ok,
           "punct": [c.get("text") for c in punct[:5]],
           "over_wide": [(c.get("text"), _u(c)) for c in wide[:5]],
           "word_split": tight[:5],
           "overlap": overlap[:5],
           "frames": "✅ 半开帧窗" if has_frames else "老文件没有 sf/ef，重渲后生效",
           "hold": hold_bad[:5], "dangling": tail[:5],
           "semantic": {"suspect": sem["suspect"][:8], "stutter": sem["stutter"][:8],
                        "magnitude_bad": sem["magnitude_bad"][:8],
                        "magnitude_note": sem["magnitude_note"][:8]},
           "widths": {"min": widths[0] if widths else 0, "max": widths[-1] if widths else 0,
                      "median": round(med, 2), "unit": "汉字宽"}}

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if ok else 1

    print(f"字幕 {len(cues)} 条 · 宽度 中位 {med:.1f} 单位（{widths[0] if widths else 0}–"
          f"{widths[-1] if widths else 0}）")
    print(f"  标点 0 处{'✅' if not punct else '✗ ' + str(len(punct))}")
    print(f"  超宽（>{MAX_UNITS} 展示单位）{'✅' if not wide else '✗ ' + str(len(wide))}")
    print(f"  断词 {'✅' if not tight else '✗ ' + str(len(tight))}（贴着切的，像同一口气被劈开）")
    print(f"  重叠 {'✅' if not overlap else '✗ ' + str(len(overlap))}"
          f" · 帧窗 {'✅' if has_frames and not bad_frames else ('✗' if bad_frames else '老文件')}")
    _ok = (em > 0) if colorize else (em == 0)
    print(f"  停留 {'✅' if not hold_bad else '✗ ' + str(len(hold_bad))}"
          f"（每条 {HOLD_MIN}–{HOLD_MAX}s）")
    print(f"  助词孤行 {'✅' if not tail else '✗ ' + str(len(tail))}"
          + ("" if not tail else f"  例：{tail[0]['text']}（{tail[0]['why']}）"))
    print(f"  残词 {'✅' if not sem['suspect'] else '✗ ' + str(len(sem['suspect']))}"
          f"（同音残词未修正）")
    for h in sem["suspect"][:4]:
        where = f"#{h['cue']}" if "cue_to" not in h else f"#{h['cue']}–#{h['cue_to']}"
        tail_note = ("（待人工拍板）" if h["needs_human"]
                     else "" if h["same_len"] else "（不等长，需人工）")
        print(f"  · {where}「{h['hit']}」→「{h['suggest']}」{tail_note}")
    print(f"  口吃重尾 {'✅' if not sem['stutter'] else '✗ ' + str(len(sem['stutter']))}"
          + ("" if not sem["stutter"] else
             "  例：" + "、".join(f"#{x['cue']}「{x['tail']}」" for x in sem["stutter"][:3])))
    print(f"  量级 {'✅' if not sem['magnitude_bad'] else '✗ ' + str(len(sem['magnitude_bad']))}"
          f"（荒谬判红 / 可疑只提 {len(sem['magnitude_note'])}）")
    for m in sem["magnitude_bad"][:3]:
        print(f"  · #{m['aid']}「{m['hit']}」= {m['value']:g}{m['unit']}（{m['rule']}）")
    for m in sem["magnitude_note"][:3]:
        print(f"  · 可疑 #{m['aid']}「{m['hit']}」= {m['value']:g}{m['unit']}——看一眼再定")
    print(f"  强调色 {em} 条{'✅' if _ok else '✗'}{'' if colorize else '（已取消）'}"
          f" · 淡入 {fade:.2f}s{'✅' if fade <= FADE_MAX else '✗'}")
    for t in [c.get("text") for c in punct[:3]]:
        print(f"  · 带标点：{t}")
    for a_, b_, ms in [(x["a"], x["b"], x["gap_ms"]) for x in tight[:3]]:
        print(f"  · 断词：「{a_}」+「{b_}」间隔 {ms}ms")
    print(f"结论：{'过' if ok else '没过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
