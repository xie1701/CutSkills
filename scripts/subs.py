#!/usr/bin/env python3
"""CutSkills · 字幕：按呼吸组切、去标点、单行、强调词变色。

2026-11-09 改的规则（Jerry 定的）：
  ① 不出标点——句读靠停顿和位置，屏幕上不出现「，。？！、；」
  ② 一段字幕必须是一个**完整的呼吸组**，不许把一个词切成前后两条
  ③ 单行，不换行；宽度落在 10–14 字（长了切、短了并）
  ④ 整段硬切：淡入淡出 ≤120ms，不做滑动
  ⑤ 数字与否定/对比词变色（只这两类，别乱染）

做法：拿字级时间戳先按「停顿 ≥0.30s 或 标点」断成呼吸组；超过 14 字才在组内
找最大的停顿（≥0.12s）拆开——**只在有停顿的地方拆，所以永远不会切在词中间**。
断字这件事没有词典也能做对，因为人说话不会在词中间停顿。

（`render_film.build_cues` 是保留路径的老实现，仍按字数软断，主链路不用它。）
"""
from __future__ import annotations

import re
from pathlib import Path

PUNCT = "，。！？、；：…—～,.!?;:"
SENTINEL = "\x00"        # 句边界标记，不进字幕
GAP_BREAK = 0.30          # 呼吸组之间的停顿
GAP_SPLIT = 0.12          # 组内允许拆开的最小停顿
MAX_CHARS = 14            # 目标上限
HARD_MAX = 19             # 真上限：没停顿也别硬切，宁可这一条长一点（切词比长更难读）
MIN_CHARS = 7             # 少于这个字并且前后离得近就并进邻居（8 字一闪太快）
TARGET_SOFT = 10          # 到这么多字、后面又有小停顿，就收口（凑成 10–14 的观感）

# 数字：阿拉伯数字连写、中文数词+量词
NUM_RE = re.compile(
    r"(?:\d+(?:[.\-–~]\d+)?\s*(?:倍|个|年|月|天|秒|元|%|成|次|遍|种|款|公分|毫米|平米|平)?"
    r"|[一二三四五六七八九十百千万两半]+\s*(?:倍|个|年|月|天|秒|元|成|次|遍|种|款))")
# 没有停顿也可以安全断开的地方：这些词前面断，中文里读得通
BREAK_HINTS = ("因为", "所以", "但是", "而且", "那么", "就是", "我们", "你们", "他们",
               "如果", "其实", "然后", "第一", "第二", "第三", "不是", "可以", "还是",
               "包括", "比如", "或者说", "一方面", "另一方面")

# 中文里「能断在这两个位置之后/之前」的字：收尾字与起头字。
# 没有分词器时，用它判断一个切点像不像词边界——不精确，但能把「厦|门」这种切开拦住。
ENDERS = set("的了着过是在和与上里中后前也都还又再很更最只就才把被让给对向从到为")
STARTERS = set("我你他她它这那哪谁什怎那可就也还又再才只不没别和与从到对把被让给因为所如但而")

# 否定 / 对比 / 程度：只收会改变判断的那几个词，不收「很、也、就」这类口语填充
NEG_WORDS = ("不是", "不会", "不能", "不", "没有", "没", "别", "只", "仅", "才",
             "更", "最", "差", "贵", "便宜", "翻", "连", "都")


def _items(segments, index_by_id, chars_cache, lib, load_chars):
    """每段一段的 (字, 起, 止)，已经换到输出时间轴。

    与 `render_film.build_cues` 收集部分同源；收字窗口必须和 build_edl 算 evidence
    的窗口逐字一致（`ain-0.02 ~ aout-0.02`），差 40ms 就会出现「字幕上留着已剪掉的话」。
    """
    out = []
    for seg in segments:
        src = seg.get("audio") or {}
        aid = src.get("asset") or seg["asset"]
        s_in = float(src.get("in", seg["in"]))
        s_out = float(src.get("out", seg["out"]))
        rec = index_by_id.get(aid) or {}
        phrases = (rec.get("transcript") or {}).get("phrases") or []
        if not phrases:
            continue
        chars = chars_cache[aid] if aid in chars_cache else \
            chars_cache.setdefault(aid, load_chars(lib, aid))
        items: list[tuple[str, float, float]] = []
        for ph in phrases:
            if ph["end"] <= s_in + 0.01 or ph["start"] >= s_out - 0.01:
                continue
            text = ph["text"]
            cs = [c for c in chars if ph["start"] - 0.08 <= c[1] <= ph["end"] + 0.08]
            hit = None
            for off in range(0, max(0, len(cs) - len(text)) + 1):
                if off + len(text) <= len(cs) and \
                        all(cs[off + i][0] == text[i] for i in range(len(text))):
                    hit = cs[off:off + len(text)]
                    break
            if hit:
                items += [(text[i], hit[i][1], hit[i][2]) for i in range(len(text))]
            else:
                span = (ph["end"] - ph["start"]) / max(1, len(text))
                items += [(text[i], ph["start"] + i * span, ph["start"] + (i + 1) * span)
                          for i in range(len(text))]
            # 句子边界打一个哨兵：ASR 的整句本来就是「一口气」的边界，
            # 只在字间停顿判会漏——两句之间常常是无缝拼接的（gap≈0）。
            items.append((SENTINEL, ph["end"], ph["end"]))
        items = [it for it in items if s_in - 0.02 <= it[1] < s_out - 0.02]
        if items:
            out.append({"el": seg.get("n"), "seg_start": seg["_start"],
                        "s_in": s_in, "dur": seg.get("_dur_q") or seg.get("_dur"),
                        "items": items})
    return out


def emphasis_html(text: str) -> str:
    """给数字和否定/对比词套 <span class="em">。返回 HTML，不破坏原文。"""
    spans: list[tuple[int, int]] = []
    for m in NUM_RE.finditer(text):
        spans.append((m.start(), m.end()))
    for w in NEG_WORDS:
        i = text.find(w)
        while i >= 0:
            spans.append((i, i + len(w)))
            i = text.find(w, i + 1)
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, pos = [], 0
    for a, b in merged:
        out.append(text[pos:a])
        out.append(f'<span class="em">{text[a:b]}</span>')
        pos = b
    out.append(text[pos:])
    return "".join(out)


def regroup(items: list[tuple[str, float, float]]):
    """一口气一组。只在停顿和标点处断，绝不切在词中间。

    返回值带上「这一组是被什么断的」：punct（标点）/ pause（停顿）/ width（到宽度上限硬断）。
    第 3 种才是真断词，字幕裁判只盯它。
    """
    groups: list[tuple[list[tuple[str, float, float]], str]] = []
    buf: list[tuple[str, float, float]] = []
    for i, it in enumerate(items):
        ch = it[0]
        buf.append(it)
        nxt = items[i + 1] if i + 1 < len(items) else None
        gap = (nxt[1] - it[2]) if nxt else 9.9
        width = len("".join(t for t, _, _ in buf))
        if ch == SENTINEL:                    # 句子边界：够一段就断，别硬拼两句
            if len("".join(t for t, _, _ in buf)) >= MIN_CHARS:
                groups.append((buf, "pause"))
                buf = []
            continue
        if ch in PUNCT:                       # 标点：断，标点自己不进字幕
            groups.append((buf, "punct"))
            buf = []
        elif gap >= GAP_BREAK or nxt is None:
            groups.append((buf, "pause"))
            buf = []
        elif width >= MAX_CHARS:              # 到上限：先找停顿，再找语义接缝，最后才硬断
            cut = max(range(1, len(buf)),
                      key=lambda j: buf[j][1] - buf[j - 1][2], default=None)
            if cut is not None and buf[cut][1] - buf[cut - 1][2] >= GAP_SPLIT:
                groups.append((buf[:cut], "pause"))
                buf = buf[cut:]
            else:
                txt = "".join(t for t, _, _ in buf)
                cand = [txt.rfind(h) for h in BREAK_HINTS if txt.rfind(h) >= 5]
                if cand:
                    k = max(cand)
                    groups.append((buf[:k], "pause"))
                    buf = buf[k:]
                elif width >= HARD_MAX:
                    # 真到上限：挑一个「最像词边界」的地方断（收尾字后 / 起头字前）
                    best, best_k = 0, None
                    for k in range(6, len(buf) - 3):
                        prev_ch, next_ch = buf[k - 1][0], buf[k][0]
                        sc = (2 if prev_ch in ENDERS else 0) + (2 if next_ch in STARTERS else 0)
                        if sc > best:
                            best, best_k = sc, k
                    if best_k:
                        groups.append((buf[:best_k], "pause"))
                        buf = buf[best_k:]
                    else:
                        groups.append((buf, "width"))
                        buf = []
        elif width >= TARGET_SOFT and gap >= GAP_SPLIT:
            groups.append((buf, "pause"))
            buf = []
    if buf:
        groups.append((buf, "pause"))

    # 标点/停顿断完仍超宽的（一口气 16 字没停顿），再按「最像词边界」的地方拆一次
    fixed: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in groups:
        txt = "".join(t for t, _, _ in g)
        if len(txt) <= MAX_CHARS + 2:
            fixed.append((g, why))
            continue
        best, best_k = 0, None
        for k in range(6, len(g) - 3):
            prev_ch, next_ch = g[k - 1][0], g[k][0]
            sc = (2 if prev_ch in ENDERS else 0) + (2 if next_ch in STARTERS else 0)
            if sc > best:
                best, best_k = sc, k
        if best_k:
            fixed.append((g[:best_k], "pause"))
            fixed.append((g[best_k:], why))
        else:
            fixed.append((g, why))
    groups = fixed

    # 太短的并进邻居（3 个字闪一下，比没有字幕还差）
    merged: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in groups:
        w = len("".join(t for t, _, _ in g))
        if merged and (w < MIN_CHARS or w <= 2) and \
                len("".join(t for t, _, _ in merged[-1][0])) + w <= MAX_CHARS and \
                g[0][1] - merged[-1][0][-1][2] < 0.55:
            merged[-1] = (merged[-1][0] + g, merged[-1][1])
        else:
            merged.append((g, why))
    # 只剩一个字的，无论多宽都塞回邻居：闪一个字比越界更糟
    final: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in merged:
        if len("".join(t for t, _, _ in g)) <= 1 and final:
            final[-1] = (final[-1][0] + g, final[-1][1])
        else:
            final.append((g, why))
    return final


def build(segments, index_by_id, chars_cache, lib, load_chars) -> list[dict]:
    cues: list[dict] = []
    for blk in _items(segments, index_by_id, chars_cache, lib, load_chars):
        for g, why in regroup(blk["items"]):
            raw = "".join(t for t, _, _ in g)
            text = "".join(ch for ch in raw if ch not in PUNCT and ch != SENTINEL).strip()
            if not text:
                continue
            a = max(0.0, g[0][1] - blk["s_in"]) + blk["seg_start"]
            b = min(blk["dur"], g[-1][2] - blk["s_in"]) + blk["seg_start"]
            if b <= a:
                continue
            cues.append({"start": round(a, 3), "end": round(b, 3), "text": text,
                         "html": emphasis_html(text), "chars": len(text),
                         "el": blk["el"], "break_after": why})
    cues.sort(key=lambda c: c["start"])
    # 相邻重叠：后一条推到前一条收口之后（硬切不留缝）
    out: list[dict] = []
    for c in cues:
        if out and c["start"] < out[-1]["end"] - 0.01:
            c["start"] = out[-1]["end"]
            if c["end"] - c["start"] < 0.25:
                out[-1]["end"] = max(out[-1]["end"], c["end"])
                out[-1]["text"] += c["text"]
                out[-1]["html"] = emphasis_html(out[-1]["text"])
                out[-1]["chars"] = len(out[-1]["text"])
                continue
        out.append(c)
    return out
