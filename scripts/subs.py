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
import sys
from pathlib import Path

import lexicon  # noqa: E402  转写词表（品牌名/术语）纠错

PUNCT = "，。！？、；：…—～,.!?;:"
SENTINEL = "\x00"        # 句边界标记，不进字幕
GAP_BREAK = 0.30          # 呼吸组之间的停顿
GAP_SPLIT = 0.12          # 组内允许拆开的最小停顿
# 上限不是拍出来的，是**算**出来的：字号 80px、1080 宽，14 字就是 1120px，
# 比画幅还宽——2026-11-09 逐帧量成片才发现字幕两头都被切掉了（左上和右下都缺字），
# 而所有 JSON 和判据全是绿的（判据按「字数 ≤14」放行，没人算过像素）。
# 参考片同口径：字幕 bbox p50 57%W、p90 71%W、最大 88%W。12 字 × 80px = 960 = 89%W。
MAX_CHARS = 12            # 目标上限（≈89%W）
HARD_MAX = 12             # 真上限：超过就必然出画，不再"宁可长一点"
MIN_CHARS = 7             # 少于这个字并且前后离得近就并进邻居（8 字一闪太快）
TARGET_SOFT = 10          # 到这么多字、后面又有小停顿，就收口（凑成 10–14 的观感）

# ── 展示单位（2026-11-11）────────────────────────────────────────────────
# 过去按「字符数」算宽度，于是 `FESTOOL RO 150 FEQ` 这 18 个字符被当 18 字砍掉，
# 而屏幕上根本装得下（18 个半角 ≈ 6 个汉字宽）。改成展示单位：
# 中日韩/全角 = 1，半角可打印 ≈ 1/3，空白 = 0。
# 注意：单位是**写作规则**，不是像素保证——真宽度仍由 verify_film 的「字幕最宽（像素）」
# 和 check_subs 的估算宽判据兜。两条都不能省。
MAX_UNITS = 12.0          # = MAX_CHARS 个汉字
MIN_UNITS = 7.0
TARGET_SOFT_UNITS = 10.0
HARD_UNITS = 12.0
HALF_W = 0.34             # 半角字符相对汉字宽度（实测思源黑体 0.32–0.36）


def display_units(text: str) -> float:
    """一行字的「展示宽度」，单位是汉字宽。"""
    u = 0.0
    for ch in str(text or ""):
        if ch in " \u3000\t":
            continue
        u += 1.0 if ord(ch) > 0x2E7F else HALF_W
    return round(u, 2)


def est_width_px(text: str, size_px: float) -> float:
    """估算渲染宽度（像素）。偏保守：汉字按 1em，半角按 0.34em。"""
    return round(display_units(text) * float(size_px), 1)

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
# 不能收尾的字：单字代词/副词当句末，观众会以为下一行是另一句的补语
# （2026-11-11 欧阳这批实测：「所以好多人都觉得我」|「好傻一个六百平嘅店」）。
# 粤语口语字一并算上：嘅/咗/唔/喺/佢。
NO_END = set("我你他她它嘅咗唔喺佢哋同埋将把被对向从到为与和及或者系都就才很更最只得地")

# 分词器：只用来判「这个词能不能断」。装在 scripts/vendor/ 里（本机 pip 走 SOCKS 代理
# 必失败，只能手动放），装不上就退回上面那两个字表——不精确，但能把「厦|门」拦住。
_VENDOR = Path(__file__).resolve().parent / "vendor"
try:
    if str(_VENDOR) not in sys.path:
        sys.path.insert(0, str(_VENDOR))
    import warnings
    with warnings.catch_warnings():          # jieba 0.42 的正则里还写着 "\."，Python 3.12 会告警
        warnings.simplefilter("ignore", SyntaxWarning)
        import jieba  # noqa: E402

    jieba.setLogLevel(60)
    # 粤语词表：jieba 是普通话模型，「我哋」它会拆成「我|哋」，字幕就断在「我」后面。
    # 词表跟着仓库走，不用每个项目手配（2026-11-11 欧阳这批实测踩到）。
    _YW = _VENDOR.parent / "data" / "cantonese-words.txt"
    if _YW.exists():
        try:
            jieba.load_userdict(str(_YW))
            HAVE_YUE_WORDS = True
        except Exception:                       # pragma: no cover
            HAVE_YUE_WORDS = False
    else:                                       # pragma: no cover
        HAVE_YUE_WORDS = False
    HAVE_JIEBA = True
except Exception:                                    # pragma: no cover
    HAVE_JIEBA = False
    HAVE_YUE_WORDS = False


def word_bounds(txt: str) -> list[int]:
    """能切的位置（第几个字之后）。词内部不算。

    没有它的时候，「墙面或者地面或者任何表面的让他成为成品就」被切成
    「…任何表」+「面的让他成为成品就」——**把「表面」劈成两半**。
    验收没抓到，因为验收只查宽度、标点、条数；断词得靠词边界本身才判得出来。
    """
    if not HAVE_JIEBA or not txt:
        return []
    out, k = [], 0
    for w in jieba.cut(txt):
        k += len(w)
        if k < len(txt):
            out.append(k)
    return out


def is_word_bound(txt: str) -> bool:
    """这一串字本身是不是「由若干个完整的词拼起来的」。没有分词器就不判（返回 True）。

    ASR 的句界不可全信：它把「墙面或者地面或者任何表」当成一句、下一句从「面」开始。
    照它断，字幕上就是「任何表」+「面的…」——把「表面」劈成两半。
    所以句界只当**候选**，落在词中间的那个不要。
    """
    if not HAVE_JIEBA or not txt:
        return True
    return len(txt) in word_bounds(txt)


def choose_cut(buf: list[tuple[str, float, float]], target: int | None = None) -> int | None:
    """一条太长的字幕该从第几个字处断开。**只用词边界**，优先两侧都不太短。"""
    txt = "".join(t for t, _, _ in buf)
    n = len(txt)
    lo, hi = 5, n - 3
    if hi <= lo:
        return None
    b = [p for p in word_bounds(txt) if lo <= p <= hi]
    if b:
        goal = target if target else 0.55 * n
        # 光看「离目标多近」会挑出「…任何表面」|「的让他成为成品就」这种：
        # 「的」被甩到下一句开头，读着像下一句的定语。中文习惯把「的」留在前一句，
        # 所以落在轻声字（的了着过…）之后的切点加分。
        return min(b, key=lambda p: abs(p - goal) - (1.5 if buf[p - 1][0] in ENDERS else 0)
                      + (3.0 if buf[p - 1][0] in NO_END else 0))
    best, best_k = 0, None
    for k in range(lo, hi + 1):
        sc = (2 if buf[k - 1][0] in ENDERS else 0) + (2 if buf[k][0] in STARTERS else 0) \
             - (3 if buf[k - 1][0] in NO_END else 0)
        if sc > best:
            best, best_k = sc, k
    return best_k

# 否定 / 对比 / 程度：只收会改变判断的那几个词，不收「很、也、就」这类口语填充
NEG_WORDS = ("不是", "不会", "不能", "不", "没有", "没", "别", "只", "仅", "才",
             "更", "最", "差", "贵", "便宜", "翻", "连", "都")


def _items(segments, index_by_id, chars_cache, lib, load_chars, lex):
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
                        # 书面稿：元素上写了 evidence 就照它出字幕（口误、场记口令、
                        # 「三二一组」这类话 ASR 会照念，字幕不能跟着一起念）。
                        "ev": seg.get("evidence") or "",
                        "items": lexicon.fix_items(items, lex)})
    return out


def retime_to_script(items, script: str):
    """把字幕文字换成书面稿，时间仍钉在原来那几千个字上。

    为什么不能直接拿 ASR 的句子出字幕（2026-11-11 欧阳这批的实测）：
    口播里满是「咧」「诶」「三二一组」和重复句，ASR 照单全收，字幕就成了念稿事故。
    但也不能换成书面稿之后就按字数平均分配时间——那样后段会越走越偏。
    所以用 difflib 把「稿子」对到「原声字」上：能对上的字继承原声时间，
    改写的片段按原声那一段的时长摊开，删掉的字（口误）把时间让出去，
    停顿于是自然出现，`regroup()` 照旧按停顿切呼吸组。
    """
    A, sent_at = [], []
    for it in items:
        if it[0] == SENTINEL:
            sent_at.append(len(A))
        else:
            A.append(it)
    B = [ch for ch in (script or "") if ch not in PUNCT and ch != SENTINEL]
    asr = "".join(t for t, _, _ in A)
    if not B or not asr:
        return items
    import difflib
    sm = difflib.SequenceMatcher(None, asr, B, autojunk=False)
    times: list[tuple[float, float]] = [(0.0, 0.0)] * len(B)
    src: list[int] = [0] * len(B)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                times[j1 + k] = (A[i1 + k][1], A[i1 + k][2])
                src[j1 + k] = i1 + k
        elif tag in ("replace", "insert"):
            if i2 > i1:
                a0, a1 = A[i1][1], A[i2 - 1][2]
            else:
                a0 = A[i1][2] if i1 < len(A) else (A[-1][2] if A else 0.0)
                a1 = A[i1][1] if i1 < len(A) else a0
            n = max(1, j2 - j1)
            step = max(0.0, (a1 - a0)) / n
            for k in range(n):
                times[j1 + k] = (a0 + k * step, a0 + (k + 1) * step)
                src[j1 + k] = min(len(A) - 1, i1 + int(k * max(1, i2 - i1) / n)) if A else 0
        # delete：原声里的口误/填充词在这里被丢掉，前后两字之间自然留出停顿
    new: list[tuple] = []
    cut = list(sent_at)
    for j, ch in enumerate(B):
        while cut and src[j] >= cut[0]:
            new.append((SENTINEL, times[j][0], times[j][0]))
            cut.pop(0)
        new.append((ch, times[j][0], times[j][1]))
    while cut:
        new.append((SENTINEL, times[-1][1], times[-1][1]))
        cut.pop(0)
    return new


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
        width = display_units("".join(t for t, _, _ in buf))
        if ch == SENTINEL:                    # 句子边界：够一段就断，别硬拼两句
            txt = "".join(t for t, _, _ in buf if t != SENTINEL)
            # 但 ASR 的句界会落在词中间（「…任何表」|「面的让他成为成品」）。
            # 这种边界不算数：把哨兵扔掉，让它跟后面的字一起走，宽度上限自然会管。
            if not is_word_bound(txt) and display_units(txt) <= MAX_UNITS:
                buf.pop()
                continue
            if display_units(txt) >= MIN_UNITS:
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
                elif width >= HARD_UNITS:
                    # 真到上限：挑一个**词的边界**断（有分词器就按词典，没有才靠字表猜）
                    best_k = choose_cut(buf, target=int(MAX_UNITS) - 2)
                    if best_k:
                        groups.append((buf[:best_k], "pause"))
                        buf = buf[best_k:]
                    else:
                        groups.append((buf, "width"))
                        buf = []
        elif width >= TARGET_SOFT_UNITS and gap >= GAP_SPLIT:
            groups.append((buf, "pause"))
            buf = []
    if buf:
        groups.append((buf, "pause"))

    # 标点/停顿断完仍超宽的（一口气 16 字没停顿），再按「最像词边界」的地方拆一次
    fixed: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in groups:
        txt = "".join(t for t, _, _ in g)
        # 硬上限就是 12 个展示单位，不留 +2 的「宽容」：+2 会让 14 字的条落地，
        # 80px × 14 = 1120px > 1080px，两头真的被画幅吃掉（2026-11-11 欧阳 a/b 两条片实测 104%W）。
        if display_units(txt) <= MAX_UNITS:
            fixed.append((g, why))
            continue
        best_k = choose_cut(g)
        if best_k:
            fixed.append((g[:best_k], "pause"))
            fixed.append((g[best_k:], why))
        else:
            fixed.append((g, why))
    groups = fixed

    # 太短的并进邻居（3 个字闪一下，比没有字幕还差）
    merged: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in groups:
        w = display_units("".join(t for t, _, _ in g))
        if merged and (w < MIN_UNITS or w <= 2) and \
                display_units("".join(t for t, _, _ in merged[-1][0])) + w <= MAX_UNITS and \
                g[0][1] - merged[-1][0][-1][2] < 0.55:
            merged[-1] = (merged[-1][0] + g, merged[-1][1])
        elif merged and w <= 3:
            # 夹在两句话之间、又并不进邻居的 2–3 字（「那么」「其实就」）：一闪就没了，
            # 观众反而以为字幕出错。丢掉它，那个停顿就空着——口播片里空半秒不算断。
            continue
        else:
            merged.append((g, why))
    # 只剩一个字的，无论多宽都塞回邻居：闪一个字比越界更糟
    final: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in merged:
        if display_units("".join(t for t, _, _ in g)) <= 1 and final:
            final[-1] = (final[-1][0] + g, final[-1][1])
        else:
            final.append((g, why))
    return final


def build(segments, index_by_id, chars_cache, lib, load_chars,
          colorize: bool = True, fps: int = 30) -> list[dict]:
    """`colorize=False` 时一个字都不变色（Jerry 2026-11-09：重点词变色取消）。

    每条字幕带 `units`（展示单位）与 `sf/ef`（半开帧窗 [sf, ef)）。
    帧窗是后面所有重叠/对齐判据的唯一口径——秒数保留只为向后兼容。
    """
    cues: list[dict] = []
    lex = lexicon.load(Path(lib) / "_cutskills")
    for blk in _items(segments, index_by_id, chars_cache, lib, load_chars, lex):
        items = blk["items"]
        if blk.get("ev"):
            items = retime_to_script(items, blk["ev"])
        for g, why in regroup(items):
            raw = "".join(t for t, _, _ in g)
            text = "".join(ch for ch in raw if ch not in PUNCT and ch != SENTINEL).strip()
            if not text:
                continue
            a = max(0.0, g[0][1] - blk["s_in"]) + blk["seg_start"]
            b = min(blk["dur"], g[-1][2] - blk["s_in"]) + blk["seg_start"]
            if b <= a:
                continue
            cues.append({"start": round(a, 3), "end": round(b, 3), "text": text,
                         "html": emphasis_html(text) if colorize else None,
                         "chars": len(text), "units": display_units(text),
                         "sf": int(round(a * fps)), "ef": int(round(b * fps)),
                         "el": blk["el"], "break_after": why})
    cues.sort(key=lambda c: c["start"])
    # 相邻重叠：后一条推到前一条收口之后（硬切不留缝）
    out: list[dict] = []
    for c in cues:
        if out and c["start"] < out[-1]["end"] - 0.01:
            c["start"] = out[-1]["end"]
            c["sf"] = int(round(c["start"] * fps))
            if c["end"] - c["start"] < 0.25:
                out[-1]["end"] = max(out[-1]["end"], c["end"])
                out[-1]["text"] += c["text"]
                out[-1]["html"] = emphasis_html(out[-1]["text"]) if colorize else None
                out[-1]["chars"] = len(out[-1]["text"])
                out[-1]["units"] = display_units(out[-1]["text"])
                out[-1]["ef"] = int(round(out[-1]["end"] * fps))
                continue
        out.append(c)
    # 帧窗收尾：必须 ef > sf，且相邻半开窗不相交（相接合法）
    for i, c in enumerate(out):
        if c["ef"] <= c["sf"]:
            c["ef"] = c["sf"] + max(1, int(round((c["end"] - c["start"]) * fps)))
        if i:
            c["sf"] = max(c["sf"], out[i - 1]["ef"])
            if c["ef"] <= c["sf"]:
                c["ef"] = c["sf"] + 1
    return out
