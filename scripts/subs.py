#!/usr/bin/env python3
"""CutSkills · 字幕：按呼吸组切、去标点、单行、强调词变色。

2026-11-09 改的规则（Jerry 定的）：
  ① 不出标点——句读靠停顿和位置，屏幕上不出现「，。？！、；」
  ② 一段字幕必须是一个**完整的呼吸组**，不许把一个词切成前后两条
  ③ 单行，不换行；一行最多 10 个展示单位（约 10 个汉字）、目标 8 个；每条 0.8–3.2 秒
  ④ 整段硬切：淡入淡出 ≤120ms，不做滑动
  ⑤ 数字与否定/对比词变色（只这两类，别乱染）

做法：拿字级时间戳先按「停顿 ≥0.30s 或 标点」断成呼吸组；超过 14 字才在组内
找最大的停顿（≥0.12s）拆开——**只在有停顿的地方拆，所以永远不会切在词中间**。
断字这件事没有词典也能做对，因为人说话不会在词中间停顿。

2026-11-13（T-032）：停顿**不许再信 ASR 的词起点间隔**。ASR 会把一处停顿记进前一个字的
时长里（`qizao-ground-01` 的「有」占 1.689s、里面 1.06s 是没声音的），于是词起点之间
gap=0，「≥0.30s 才断」这条规则永远看不见它——三支老片各留了几条「窗口里明明静了
0.5–1.2s，字幕还撑着 3.5–4.4s」。所以拆条前先用 `silence.py` 量真静音，把字的起止从
静音里拉出来（`retime_off_silence()`），停顿于是变成真的 gap；停留超 3.2s 而窗口里
又有 ≥0.30s 真静音的条，再在静音处补一刀（`split_breath_groups()`）。
量不出音频（素材不在本机 / ffmpeg 解不出）时**不静默放行**：退回旧口径并在 stderr 报
「静音探针不可用，按词界拆分」。

（`render_film.build_cues` 是保留路径的老实现，仍按字数软断，主链路不用它。）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import lexicon  # noqa: E402  转写词表（品牌名/术语）纠错

PUNCT = "，。！？、；：…—～,.!?;:"
SENTINEL = "\x00"        # 句边界标记，不进字幕
MIN_HOLD = 0.8            # 一条字幕最短停留（低于这个眼睛抓不住；对齐 srt-motion-cards 规范）
MAX_HOLD = 3.2            # 一条字幕最长停留（对齐 check_subs.HOLD_MAX；超了就在真静音处断）
GAP_BREAK = 0.30          # 呼吸组之间的停顿
GAP_SPLIT = 0.12          # 组内允许拆开的最小停顿
# 一口气断开的门槛（秒）：与 `check_subs.BREATH_MIN` 同口径。
# 它的另一半在 check_subs：窗口里量不到 ≥0.30s 停顿的「单呼吸组」上限放到 4.5s。
# 两边必须同一个数，否则产线把 0.28s 的停顿当呼吸断开、裁判却当没停过，互相打脸。
BREATH_MIN = 0.30
# 上限不是拍出来的，是**算**出来的：字号 80px、1080 宽，14 字就是 1120px，
# 比画幅还宽——2026-11-09 逐帧量成片才发现字幕两头都被切掉了（左上和右下都缺字），
# 而所有 JSON 和判据全是绿的（判据按「字数 ≤14」放行，没人算过像素）。
# 参考片同口径：字幕 bbox p50 57%W、p90 71%W、最大 88%W。
# 2026-11-12 再收一档到 10 个单位（对齐 srt-motion-cards 的规范：单行 ≤10 显示单位）。
# 10 字 × 80px = 800px = 74%W，跟参考片的 p90 对上；12 字是 89%W，已经在满幅边缘。
MAX_CHARS = 10            # 目标上限（≈74%W）
TARGET_SOFT = 8           # 到这么多字、后面又有小停顿，就收口

# ── 展示单位（2026-11-11）────────────────────────────────────────────────
# 过去按「字符数」算宽度，于是 `FESTOOL RO 150 FEQ` 这 18 个字符被当 18 字砍掉，
# 而屏幕上根本装得下（18 个半角 ≈ 6 个汉字宽）。改成展示单位：
# 中日韩/全角 = 1，半角可打印 ≈ 1/3，空白 = 0。
# 注意：单位是**写作规则**，不是像素保证——真宽度仍由 verify_film 的「字幕最宽（像素）」
# 和 check_subs 的估算宽判据兜。两条都不能省。
MAX_UNITS = 10.0          # = MAX_CHARS 个汉字
MIN_UNITS = 5.0
TARGET_SOFT_UNITS = 8.0
HARD_UNITS = 10.0
HALF_W = 0.34             # 半角字符相对汉字宽度（实测思源黑体 0.32–0.36）

# ── 当前生效的行宽口径（字幕模板可收窄，不许放宽）──────────────────────
# 上面那几个 10 / 8 / 5 是**全链路共用**的硬上限：check_subs 判的也是它。
# 字幕模板（styles/subtitles.yaml）只能把自己的 max_units_per_line 定在 ≤ MAX_UNITS，
# 所以模板换的是「按哪一档判」，不是「判据松了」。没选模板时这里就是原值，
# 拆分逻辑读到的数与改动前完全一样（向后兼容的落点就在这一行）。
_W = {"max_units": MAX_UNITS, "soft": TARGET_SOFT_UNITS, "hard": HARD_UNITS,
      "max_chars": MAX_CHARS, "keep_together": (), "no_break_after": frozenset(),
      "no_break_before": frozenset()}


def _scope(max_units: float | None = None, case_rules: dict | None = None,
           target_soft: float | None = None) -> tuple[dict, dict]:
    """算出「这一轮生效的口径」，返回 (新值, 旧值) 供调用方还原。"""
    mu = float(max_units) if max_units else MAX_UNITS
    mu = max(1.0, min(mu, MAX_UNITS))          # 只许收窄：模板不许把上限抬高
    rules = case_rules or {}
    new = {"max_units": mu,
           # 软收口与硬断都跟着上限走：上限 8 时「到 8 字就该断」，不能还等 10
           "soft": min(float(target_soft or TARGET_SOFT_UNITS), mu),
           "hard": min(HARD_UNITS, mu),
           "max_chars": min(MAX_CHARS, int(round(mu))),
           "keep_together": tuple(str(w) for w in (rules.get("keep_together") or []) if str(w)),
           "no_break_after": frozenset(str(c) for c in (rules.get("no_break_after") or [])),
           "no_break_before": frozenset(str(c) for c in (rules.get("no_break_before") or []))}
    return new, dict(_W)


class width_scope:
    """`with width_scope(max_units, case_rules):` —— 临时换一套行宽口径。

    为什么用作用域而不是把 max_units 一路当参数传：拆条链路有 6 个函数
    （regroup / regroup_script / split_point / split_long / split_breath_groups /
    build）都在读同几个上限，逐个加参数等于给同一条规矩开六份副本。
    作用域只有一份，且在 `build()` 入口设、出口还原，不带状态到下一次。
    """

    def __init__(self, max_units: float | None = None, case_rules: dict | None = None,
                 target_soft: float | None = None):
        self.new, self.old = _scope(max_units, case_rules, target_soft)

    def __enter__(self) -> dict:
        _W.update(self.new)
        return _W

    def __exit__(self, *exc) -> None:
        _W.clear()
        _W.update(self.old)


def _keep_together_breaks(txt: str, p: int) -> bool:
    """切点 p 是否落在某个「不许拆」的词内部（templates 的 case_rules.keep_together）。"""
    for w in _W["keep_together"]:
        i = txt.find(w)
        while i >= 0:
            if i < p < i + len(w):
                return True
            i = txt.find(w, i + 1)
    return False


def display_units(text: str) -> float:
    """一行字的「展示宽度」，单位是汉字宽。"""
    u = 0.0
    for ch in str(text or ""):
        if ch in " \u3000\t":
            continue
        u += 1.0 if ord(ch) > 0x2E7F else HALF_W
    return round(u, 2)


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
# 不许起头的字：助词、量词尾巴、粤语助词。落在这儿读起来像上一句的尾巴
# （「…任何表面」|「的让他成为成品就」），也是一条条读下来最别扭的地方。
NO_HEAD = set("的了着过嘅咗咧啦呀㗎啲哋咩嘛喎啩添嚟返咁")

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


# 数词与量词之间不是词边界：「六百平」被切成「六百|平」、「十个|人」同理。
# jieba 是通用模型，不知道「平」在粤语口播里是个高频量词。
_NUM = set("零一二三四五六七八九十百千万亿两廿0123456789")
_MEASURE = set("平个间张把只条米年月日次位层人分秒蚊元块斤两杯碗份套台部支点斗升克吨公里倍成番阵嚿")


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
    # 量词卡的补丁：落在一个「数词 + 量词」的内部就不算边界
    return [p for p in out if not (txt[p - 1] in _NUM and txt[p] in _MEASURE)]


def is_word_bound(txt: str) -> bool:
    """这一串字本身是不是「由若干个完整的词拼起来的」。没有分词器就不判（返回 True）。

    ASR 的句界不可全信：它把「墙面或者地面或者任何表」当成一句、下一句从「面」开始。
    照它断，字幕上就是「任何表」+「面的…」——把「表面」劈成两半。
    所以句界只当**候选**，落在词中间的那个不要。
    """
    if not HAVE_JIEBA or not txt:
        return True
    return len(txt) in word_bounds(txt)


# ── 真静音探针（2026-11-13 T-032）────────────────────────────────────────
# 「他是不是在这儿换了一口气」只能量声音，不能看 ASR 的词起点：ASR 把停顿记进前一个字
# 的时长里（ground-01 的「有」占 1.689s，里面 1.06s 没声音），词起点之间 gap=0。
# 量声音走 `silence.py`：它按窗口自己的说话电平算相对阈值，并且有 _MEM + 磁盘两层缓存，
# 一条片子几十条字幕只按「一格」抽一次音轨。
_SIL_MEM: dict[str, list | None] = {}
# 上一次 `build()` 的探针状态。量不出音频时调用方要能说出「没量成」，不许静默当「没停顿」。
LAST_PROBE: dict = {"segments": 0, "measured": 0, "missing": [], "silent_segments": 0}


def speech_silences(src, a: float, b: float) -> list[tuple[float, float]] | None:
    """[a,b] 里**真有**的静音段（秒，素材绝对时间）。量不出返回 None。

    `silence.runs_strict()` 分得清两件事：「窗口里一点静音都没有」（返回 []）与
    「根本量不到」（None）。前者是慢语速的一口气，后者是素材不在本机 / ffmpeg 解不出音频——
    后者必须退回旧口径并且**说出来**，不许当成「没有停顿」放行。
    """
    if not src:
        return None
    try:
        src = Path(str(src))
    except (TypeError, ValueError):
        return None
    if not src.exists():
        return None
    key = f"{src}|{src.stat().st_mtime_ns}|{a:.3f}|{b:.3f}|{BREATH_MIN}"
    if key in _SIL_MEM:
        return _SIL_MEM[key]
    try:
        import silence as SI          # 同目录：量静音的判据只有一份
        runs = SI.runs_strict(src, a, b, min_d=BREATH_MIN)
    except Exception:                 # pragma: no cover  探针坏掉不许拖垮出字幕
        runs = None
    _SIL_MEM[key] = runs
    return runs


def retime_off_silence(items: list[tuple], runs) -> list[tuple]:
    """把字的起止从静音里拉出来——ASR 把停顿算进了某个字的时长。

    「有」占 1689.60–1691.22，其中 1690.03–1691.23 是静音：拉完之后「有」收到 1690.03，
    下一个字从 1691.23 起，gap 变成 0.83s，旧的「gap ≥ 0.30 就断」终于看得见这口气。
    三种关系都要处理：
      · 静音在字的后半段（`部` 332.10–333.54 里 332.71–333.03 是静音）→ 字收在静音起点；
      · 字整体泡在静音里（ASR 把停顿记在了下一个字的头上）→ 推到静音结束；
      · 静音盖过整字 → 退化成零长度，不许出现负时长。
    """
    if not items or not runs:
        return items
    out: list[tuple] = []
    for ch, a, b in items:
        na, nb = float(a), float(b)
        for r0, r1 in runs:
            if nb <= r0 or na >= r1:
                continue
            if na < r0:
                nb = r0
            else:
                na, nb = r1, max(nb, r1)
        # 拉完如果只剩零长度（整个字泡在静音里），保留原样：ASR 把这个位置给了它，
        # 抹成 0 秒会让后面的书面稿对齐跟着错位（dianzan-a 实测：抹掉一个零长度字，
        # 稿子尾上的「食」就被挤出成片窗口，屏幕上少一个字）。
        out.append((ch, na, nb) if nb - na > 1e-6 else (ch, float(a), float(b)))
    return out


def breath_cuts(items: list[tuple]) -> set[int]:
    """相邻两字之间**真有** ≥BREATH_MIN 静音的切点（第几个字之前）。

    只该在 `retime_off_silence()` 拉过的时间戳上算——那时 gap 才是声音，
    不是 ASR 的记账。
    """
    return {i for i in range(1, len(items))
            if items[i][1] - items[i - 1][2] >= BREATH_MIN - 1e-9}


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
                      + (3.0 if buf[p - 1][0] in NO_END else 0)
                      + (2.5 if buf[p][0] in NO_HEAD else 0))
    best, best_k = 0, None
    for k in range(lo, hi + 1):
        sc = (2 if buf[k - 1][0] in ENDERS else 0) + (2 if buf[k][0] in STARTERS else 0) \
             - (3 if buf[k - 1][0] in NO_END else 0) - (2.5 if buf[k][0] in NO_HEAD else 0)
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
            # 真静音重记时（T-032）：先把字的起止从静音里拉出来，
            # 后面的「gap ≥ 0.30 就断」才量的是声音而不是 ASR 的记账。
            # 一整格只量一次（silence.py 按窗口缓存，_MEM 命中就不再请 ffmpeg）。
            runs = speech_silences(Path(lib) / str(aid) if aid else None, s_in, s_out)
            LAST_PROBE["segments"] += 1
            if runs is None:
                LAST_PROBE["missing"].append(str(aid))
            else:
                LAST_PROBE["measured"] += 1
                if runs:
                    LAST_PROBE["silent_segments"] += 1
                    items = retime_off_silence(items, runs)
                    # 拉完可能有个别字被推到窗口外（整字泡在尾部静音里）：
                    # 收字窗口与 evidence 同口径是硬规矩，出了窗口就不再算这一格的字。
                    items = [it for it in items
                             if s_in - 0.02 <= it[1] < s_out - 0.02]
            # evidence 也要过词表。 b#5 卡上写「四大天王」、同屏字幕写「四大轩王」——
            # 真因就在这里：卡走的是 words_of_window（过词表），字幕直接拿 evidence，
            # 而 evidence 是 ASR 原字。两边读的不是同一份真值，屏幕上就自己打自己。
            ev = seg.get("evidence") or ""
            if ev:
                ev = lexicon.strip_noise(lexicon.fix_text(ev, lex))
            out.append({"el": seg.get("n"), "seg_start": seg["_start"],
                        "s_in": s_in, "dur": seg.get("_dur_q") or seg.get("_dur"),
                        # 书面稿：元素上写了 evidence 就照它出字幕（口误、场记口令、
                        # 「三二一组」这类话 ASR 会照念，字幕不能跟着一起念）。
                        "ev": ev,
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
    # 书面稿里的句读是**写的人定的句界**，比 ASR 的停顿可靠得多：
    # 照 ASR 断，会上出「…厨房系大系」|「因为纯手工」这种把连词甩到下一行的切口。
    # 所以稿子上的逗号句号都记成句界，regroup 在那儿优先断。
    B, brk = [], set()
    for ch in (script or ""):
        if ch == SENTINEL:
            continue
        if ch in PUNCT:
            if B:
                brk.add(len(B))
            continue
        B.append(ch)
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
    # 有稿子的时候，**ASR 的句界一律不用**：稿子上的标点就是句界。
    # 两边都用，会得到「…系我哋嘅」|「根本所以厨房先要大」——ASR 恰好在「嘅」后面
    # 断了一下气，把「嘅根本」拆成两行，而稿子上那里根本没有逗号。
    new: list[tuple] = []
    for j, ch in enumerate(B):
        if j in brk:
            new.append((SENTINEL, times[j][0], times[j][0]))
        new.append((ch, times[j][0], times[j][1]))
    return new


def split_point(g: list[tuple[str, float, float]]) -> int | None:
    """超宽的一条从哪儿断开——**同时**看宽度、时长和词边界。

    只按宽度挑（离 55% 最近）会切出两半都在 0.6 秒内的条：
    观众看到的是「闪一下又闪一下」，比一条长一点的字幕难读得多。
    所以候选切点要过三道：两半都不超 10 个单位、两半都至少 0.8 秒、
    切点落在词边界上（有分词器时），再按「离目标宽度最近 + 不是虚字收尾」挑。
    """
    txt = "".join(t for t, _, _ in g)
    n = len(txt)
    if n < 5:
        return None
    # 候选既可以是词边界，也可以是字间（词边界优先但不独占）。
    # 只用词边界时，碰上一句「系因为纯手工系我哋嘅根本」，jieba 给出的候选全在
    # 「嘅」后面，于是怎么选都是「…我哋嘅」收尾——虚字孤行是这么来的。
    wb = {p for p in word_bounds(txt) if 3 <= p <= n - 3}
    cands = sorted(wb | set(range(4, n - 3)))
    # 模板的 case_rules.keep_together：这个词内部不许下刀
    if _W["keep_together"]:
        kept = [p for p in cands if not _keep_together_breaks(txt, p)]
        cands = kept or cands
    if not cands:
        return None
    MU = _W["max_units"]
    # 真静音优先（T-032）：同样是词边界，落在「他真停了一下」的地方比落在
    # 宽度算出来的中点好读得多——这是两条优秀字幕与一条别扭字幕的差别。
    breaths = breath_cuts(g)

    def cost(p: int) -> float:
        left, right = txt[:p], txt[p:]
        lu, ru = display_units(left), display_units(right)
        if lu > MU or ru > MU:
            return 1e6
        t0, t1 = g[0][1], g[p - 1][2]
        t2, t3 = g[p][1], g[-1][2]
        dur_l, dur_r = t1 - t0, t3 - t2
        c = abs(lu - MU * 0.55)
        # 时长不足是最贵的：一条读不完的字幕比稍微偏宽的更糟
        if dur_l < MIN_HOLD:
            c += (MIN_HOLD - dur_l) * 40
        if dur_r < MIN_HOLD:
            c += (MIN_HOLD - dur_r) * 40
        if txt[p - 1] in NO_END or txt[p - 1] in _W["no_break_after"]:
            c += 12
        if txt[p] in NO_HEAD or txt[p] in _W["no_break_before"]:
            c += 12
        if p in wb:                     # 落在词边界上更好
            c -= 4
        if p in breaths:                # 落在真静音上最好（量出来的，不是猜的）
            c -= 3
        return c

    return min(cands, key=cost)


def split_long(g: list[tuple[str, float, float]]) -> list[int]:
    """把过宽的一组按**词边界**切成若干段，每段不超上限。返回切点。

    为什么不能只切一刀：一句 20 字的话，只允许两段就只能是 10/10，
    而 10 字那刀恰好落在「开|咗」中间——因为两侧都必须 ≤10，根本没得选。
    实测切出「我花咗三百万喺金湾开 | 咗间六百平嘅点赞茶楼」，
    而且 break_after 还被记成 clause，验收直接豁免。
    切三段就是 6/6/8，每一刀都在词边界上。
    """
    txt = "".join(t for t, _, _ in g)
    n = len(txt)
    wb = sorted({p for p in word_bounds(txt) if 1 <= p <= n - 1} | {n})
    if not wb or n < 6:
        return []
    # 真静音切点（T-032）：宽到必须拆的时候，拆在「他真停了一下」的地方
    # 比拆在宽度算出来的等分点上好读。只减一点分，不足以多拆出一段来。
    breaths = breath_cuts(g)
    INF = float("inf")
    best = [INF] * (n + 1)
    back: list[int | None] = [None] * (n + 1)
    best[0] = 0.0
    MU, SOFT = _W["max_units"], _W["soft"]
    for i in range(n):
        if best[i] == INF:
            continue
        for j in wb:
            if j <= i:
                continue
            u = display_units(txt[i:j])
            if u > MU:
                break
            # 每段都想靠近水口径的软目标（默认 8）。原来写的是 0.62×上限 = 6.2，
            # 于是全片字幕中位数被压到 6.0 单位：判据下限是 6.5，每条也更容易
            # 掉到 0.8 秒以下变成「闪一下」。上限 10 是硬约束，目标该取 8 而不是 6。
            c = best[i] + (u - SOFT) ** 2
            # 切在两边的虚字上要重罚：「同埋更稳定嘅 | 出品俾大家好」这种
            # 行尾挂着「嘅」、行首挂着「啲」，跟被宽度硬拆出来的一模一样难读。
            if txt[j - 1] in NO_END or txt[j - 1] in _W["no_break_after"]:
                c += 18
            if j < n and (txt[j] in NO_HEAD or txt[j] in _W["no_break_before"]):
                c += 18
            if j in wb and _keep_together_breaks(txt, j):
                c += 1e5              # case_rules：这个词内部不许开一刀
            if j in breaths:
                c -= 8
            if c < best[j]:
                best[j], back[j] = c, i
    if best[n] == INF:
        return []
    cuts, cur = [], n
    while cur:
        cur = back[cur]  # type: ignore[assignment]
        if cur is None:
            return []
        if cur:
            cuts.append(cur)
    return sorted(cuts)


def _is_tail(clause: list[tuple[str, float, float]],
             piece: list[tuple[str, float, float]]) -> bool:
    """这一段是不是它所在小句的最后一段（最后一段断在句读上，不是被宽度拆的）。"""
    return bool(clause) and bool(piece) and piece[-1][0] == clause[-1][0]


def regroup_script(items: list[tuple[str, float, float]]
                   ) -> list[tuple[list[tuple[str, float, float]], str]]:
    """有书面稿时用这一套：**只按稿子上的句读断**，太长再按词边界+时长拆。

    不走 `regroup()` 那条「按停顿断」的路，是因为停顿属于说话人、句读属于写的人。
    说话人一口气断了但句子没完（「…先可以做更好嘅」/「…渔船嗰度运翻嚟嘅」），
    照停顿切就会把「嘅」甩在行尾；而稿子上的逗号永远落在该断的地方。
    """
    segs: list[list[tuple[str, float, float]]] = []
    cur: list[tuple[str, float, float]] = []
    for it in items:
        if it[0] == SENTINEL:
            txt = "".join(t for t, _, _ in cur)
            if display_units(txt) >= MIN_UNITS:
                segs.append(cur)
                cur = []
            continue
        cur.append(it)
    if cur and "".join(t for t, _, _ in cur).strip():
        segs.append(cur)

    out: list[tuple[list[tuple[str, float, float]], str]] = []
    MU = _W["max_units"]
    for g in segs:
        u = display_units("".join(t for t, _, _ in g))
        if u <= MU:
            out.append((g, "clause"))
            continue
        # 太长的句子：拆到每段都不超上限。
        #
        # 这里曾经写成 `"clause" if _is_tail(...) or cu <= MAX_UNITS else "width"`——
        # 而 `cu <= MAX_UNITS` 正是走到这一行的**条件**，于是每一段都记成 clause。
        # 后果不是显示问题：check_subs 的断词判据遇到 clause 就 `continue`，
        # 三片 100% 的切口都被自己豁免掉了，实测切出「我花咗三百万喺金湾开 | 咗间…」
        # 「做更好嘅出品同埋更稳 | 定嘅出品俾大家好」全部绿灯。
        cuts = split_long(g)
        if cuts:
            pts = [0] + cuts + [len(g)]
            for a, b in zip(pts, pts[1:]):
                piece = g[a:b]
                out.append((piece, "clause" if _is_tail(g, piece) else "width"))
            continue
        # 没有可用词边界（没分词器 / 整句一个词）时退回一刀切
        stack = [g]
        while stack:
            cur_g = stack.pop(0)
            cu = display_units("".join(t for t, _, _ in cur_g))
            if cu <= MU or len(cur_g) < 6:
                out.append((cur_g, "clause" if _is_tail(g, cur_g) else "width"))
                continue
            k = split_point(cur_g)
            if not k:
                out.append((cur_g, "width"))
                continue
            stack.insert(0, cur_g[:k])
            stack.insert(1, cur_g[k:])
    return out


def _pick_breath_cut(piece: list[tuple[str, float, float]],
                     cuts: set[int]) -> int | None:
    """在「真静音 ≥BREATH_MIN」的切点里挑最好的一刀。没有可用的返回 None。

    词界是硬约束：真静音落在词中间（口语里真会这样）时，宁可不拆，也不把词切两半。
    两半都要能看清（≥MIN_HOLD），平衡的两刀最舒服，虚字收/起要额外扣分。
    """
    txt = "".join(t for t, _, _ in piece)
    n = len(txt)
    wb = set(word_bounds(txt)) if HAVE_JIEBA else None
    best, best_c = None, 1e9
    for p in sorted(cuts):
        if p <= 0 or p >= n:
            continue
        if wb is not None and p not in wb:
            continue
        dur_l = piece[p - 1][2] - piece[0][1]
        dur_r = piece[-1][2] - piece[p][1]
        c = abs(dur_l - dur_r)
        if dur_l < MIN_HOLD:
            c += (MIN_HOLD - dur_l) * 6
        if dur_r < MIN_HOLD:
            c += (MIN_HOLD - dur_r) * 6
        if txt[p - 1] in NO_END:
            c += 1.2
        if txt[p] in NO_HEAD:
            c += 1.2
        if c < best_c:
            best, best_c = p, c
    return best


def split_breath_groups(groups: list[tuple[list[tuple[str, float, float]], str]]
                        ) -> list[tuple[list[tuple[str, float, float]], str]]:
    """停留超过 MAX_HOLD、窗口里又有真静音的条，添在静音处补一刀（递归到每段都合格）。

    为什么只盯「超时长 + 有停顿」两条同时成立：
      · 有停顿还撑过 3.2s 是「话停了字还赖着」——check_subs 判红，必须拆；
      · 没停顿的 3.3–4.5s 是慢语速的一口气，拆开就违背规则②（一条 = 一个完整呼吸组）；
        T-029 已把它放行到 4.5s，这里不许替它做主。
    左边的 break_after 记成 pause（不是 clause）：它没收在句读上，
    check_subs 的「助词孤行」与「断词」两条都必须真审它，不许被 clause 豁免。
    """
    out: list[tuple[list[tuple[str, float, float]], str]] = []
    stack = list(groups)
    MU = _W["max_units"]
    while stack:
        g, why = stack.pop(0)
        dur = (g[-1][2] - g[0][1]) if g else 0.0
        if dur <= MAX_HOLD + 0.02 or len(g) < 2:
            out.append((g, why))
            continue
        p = _pick_breath_cut(g, breath_cuts(g))
        if not p:
            out.append((g, why))
            continue
        left, right = g[:p], g[p:]
        wl = display_units("".join(t for t, _, _ in left))
        if wl < MIN_UNITS and out:
            # 左半边只剩 2–3 字（「简单的」|「去非黑即白」）：跟「太短的并进邻居」
            # 同一条规矩——装得下、又是同一口气（空档 <0.55s）就并回去，别让它自己闪一下。
            pw = display_units("".join(t for t, _, _ in out[-1][0]))
            if pw + wl <= MU and left[0][1] - out[-1][0][-1][2] < 0.55:
                out[-1] = (out[-1][0] + left, out[-1][1])
                left = []
        if not left:
            stack.insert(0, (right, why))
            continue
        out.append((left, "pause"))
        stack.insert(0, (right, why))
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


def regroup(items: list[tuple[str, float, float]], pause_break: bool = True):
    """一口气一组。只在停顿和标点处断，绝不切在词中间。

    `pause_break=False`：有书面稿时不按停顿断，只按稿子上的句读和宽度上限断。
    因为稿子是**写的人定的句界**，而停顿是说话人的呼吸——一口气断了、
    句子还没完，照停顿切就会得到「先可以做更好嘅」这种把「嘅」甩在行尾的切口。

    返回值带上「这一组是被什么断的」：punct（标点）/ pause（停顿）/ width（到宽度上限硬断）。
    第 3 种才是真断词，字幕裁判只盯它。
    """
    groups: list[tuple[list[tuple[str, float, float]], str]] = []
    buf: list[tuple[str, float, float]] = []
    MU = _W["max_units"]
    for i, it in enumerate(items):
        ch = it[0]
        buf.append(it)
        nxt = items[i + 1] if i + 1 < len(items) else None
        gap = (nxt[1] - it[2]) if nxt else 9.9
        width = display_units("".join(t for t, _, _ in buf))
        if ch == SENTINEL:                    # 句子边界：够一段就断，别硬拼两句
            txt = "".join(t for t, _, _ in buf if t != SENTINEL)
            u = display_units(txt)
            if u < MIN_UNITS:                 # 太短，跟后面的字一起走
                buf.pop()
                continue
            # 句界有两个来源：书面稿上的句读（pause_break=False，写的人定的，永远信）；
            # ASR 的停顿句界（会把词切开，落在词中间就不算数）。
            # 老代码在这里多了一条「超过 10 单位就扔掉这个句界」——于是
            # 「唔系因为纯手工厨房系大，」这个 11 单位的句子被迫和后一句焊在一起，
            # 再被宽度硬切成「厨房系大系」，逗号连同「系」一起割坏。
            if pause_break and u > MU and not is_word_bound(txt):
                buf.pop()
                continue
            groups.append((buf, "pause"))
            buf = []
            continue
        if ch in PUNCT:                       # 标点：断，标点自己不进字幕
            groups.append((buf, "punct"))
            buf = []
        elif (gap >= GAP_BREAK and pause_break) or nxt is None:
            groups.append((buf, "pause"))
            buf = []
        elif width >= _W["max_chars"]:        # 到上限：先找停顿，再找语义接缝，最后才硬断
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
                elif width >= _W["hard"]:
                    # 真到上限：挑一个**词的边界**断（有分词器就按词典，没有才靠字表猜）
                    best_k = choose_cut(buf, target=int(MU) - 2)
                    if best_k:
                        groups.append((buf[:best_k], "pause"))
                        buf = buf[best_k:]
                    else:
                        groups.append((buf, "width"))
                        buf = []
        elif width >= _W["soft"] and gap >= GAP_SPLIT:
            groups.append((buf, "pause"))
            buf = []
    if buf:
        groups.append((buf, "pause"))

    # 标点/停顿断完仍超宽的（一口气 16 字没停顿），再按「最像词边界」的地方拆一次
    fixed: list[tuple[list[tuple[str, float, float]], str]] = []
    for g, why in groups:
        txt = "".join(t for t, _, _ in g)
        # 硬上限就是 10 个展示单位，不留「宽容」：留一点就有一条 14 字落地，
        # 80px × 14 = 1120px > 1080px，两头真的被画幅吃掉（2026-11-11 欧阳 a/b 两条片实测 104%W）。
        if display_units(txt) <= MU:
            fixed.append((g, why))
            continue
        best_k = split_point(g)
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
                display_units("".join(t for t, _, _ in merged[-1][0])) + w <= MU and \
                g[0][1] - merged[-1][0][-1][2] < 0.55:
            merged[-1] = (merged[-1][0] + g, merged[-1][1])
        elif merged and w <= 3:
            # 夹在两句话之间、又并不进邻居的 2–3 字（「那么」「其实就」）：一闪就没了，
            # 观众反而以为字幕出错。丢掉它，那个停顿就空着——口播片里空半秒不算断。
            # 但**不许丢真话**：真实停顿一显形，这一支会把「食」这种实词当填充词吞掉
            # （2026-11-13 T-032 实测：dianzan-a 264 字变 263 字）。所以先试并进前一条
            # （不看空档多宽——宁可在停顿上多停半秒，也不许把一个字从屏幕上抹掉），
            # 装不下才丢。
            if display_units("".join(t for t, _, _ in merged[-1][0])) + w <= MU:
                merged[-1] = (merged[-1][0] + g, merged[-1][1])
                continue
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


def _report_probe() -> None:
    """把「静音探针到底量没量到」说出口。量不到不许静默装作「没停顿」。

    素材不在本机（拷到别的工位、素材盘拔了）是最常见的：那时 `silence.runs_strict()`
    返回 None，拆条退回旧的「ASR 词起点间隔」口径——结果是那几条长字幕又会撑过 3.2s。
    这不是错误，但必须让人在屏幕上看到原因，而不是等 check_subs 报红再反查。
    """
    miss = sorted({m for m in LAST_PROBE.get("missing") or [] if m})
    if not miss:
        return
    print(f"  ！静音探针不可用（{len(miss)} 格量不到音频：{', '.join(miss[:3])}"
          f"{' …' if len(miss) > 3 else ''}），按词界拆分——"
          f"这些格里的停顿只能靠 ASR 的词起点间隔判，长条可能又撑过 {MAX_HOLD}s",
          file=sys.stderr)


# ── 字幕模板（styles/subtitles.yaml）────────────────────────────────────
#
# 四套可单独选用的字幕样式。优先级**必须**是：
#   项目显式选的模板（brief.json 的 `subtitle_template`）> 包装包自带的 subtitle 参数
#   > 本文件的 `default` 模板。
# 老项目（没选模板、包里有 subtitle 段）走的是第二条，读出来的数与改动前一样——
# 这条兼容性由 `test_subtitle_templates.py` 的 ④ 守着。
SUB_TEMPLATES = Path(__file__).resolve().parent.parent / "styles" / "subtitles.yaml"
# 必填字段：少一个就报错，不许「缺了当没说」——那等于又回到「参数散在包里」。
SUB_FIELDS = ("id", "name", "font", "size_px", "line_spacing", "letter_spacing",
              "color", "stroke", "shadow", "emphasis", "position",
              "max_units_per_line", "case_rules")
# 这些许可字样本身就等于「可商用」，不用再翻 commercial_ok
SUB_COMMERCIAL_LICENCES = ("open font license", "apache", "mit license", "cc0",
                           "public domain")

try:                                        # 模板表是 YAML；仓内 vendor/ 有一份
    import yaml  # noqa: E402
except ImportError:                                                        # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
    import yaml  # noqa: E402


class SubtitleTemplateError(ValueError):
    """模板表 / 模板引用有问题。消息是给人看的（CLI 直接打这一句，不打 traceback）。"""


def _font_entry(name: str, table: dict) -> tuple[str | None, dict | None]:
    """字体别名 / 文件名 / 同族变体 → (条目名, 条目)。与 fonts.entry_of 同口径。"""
    fonts = (table or {}).get("fonts") or {}
    if name in fonts:
        return name, fonts[name]
    for k, v in fonts.items():
        if v.get("file") == name:
            return k, v
    low = str(name).lower()
    for k, v in fonts.items():
        for pref in (v.get("file_prefixes") or []):
            if low == str(pref).lower() or low.startswith(str(pref).lower() + "-"):
                return k, v
    return None, None


def font_licence(name: str, table: dict | None = None) -> dict:
    """模板用字的授权口径：必须在 fonts.yaml 里有 license，且不是 unverified。

    返回 {"license":..., "ok": bool, "why": str, "alias":..., "commercial_ok":...}。
    「查不到」一律按不许用处理——模板是要拿去出片的，不许赌。
    """
    if table is None:
        import fonts as _f                                        # noqa: PLC0415
        table = _f.load()
    alias, ent = _font_entry(str(name or ""), table)
    if not ent:
        return {"alias": None, "license": "unverified", "ok": False, "commercial_ok": None,
                "why": f"styles/fonts.yaml 里没有「{name}」这只字体（先去登记，再进模板）"}
    lic = str(ent.get("license") or "").strip()
    if not lic or lic.lower() == "unverified":
        return {"alias": alias, "license": lic or "unverified", "ok": False,
                "commercial_ok": ent.get("commercial_ok"),
                "why": f"字体「{alias}」在 fonts.yaml 里没填 license（或写着 unverified）"
                       f"——未核验授权的字不许进字幕模板"}
    low = lic.lower()
    auto = any(s in low for s in SUB_COMMERCIAL_LICENCES)
    if not auto and ent.get("commercial_ok") is not True:
        return {"alias": alias, "license": lic, "ok": False,
                "commercial_ok": ent.get("commercial_ok"),
                "why": f"字体「{alias}」的许可「{lic}」不在自动认定的可商用名单里，"
                       f"fonts.yaml 也没写 commercial_ok: true——先人工确认再进模板"}
    return {"alias": alias, "license": lic, "ok": True,
            "commercial_ok": ent.get("commercial_ok", True), "why": ""}


def _validate_template(t: dict, key: str, where: str, table: dict) -> list[str]:
    """一套模板的全部校验。返回人话错误清单（空 = 通过）。"""
    errs: list[str] = []
    if not isinstance(t, dict):
        return [f"{where} 模板「{key}」不是一个字典（YAML 缩进写坏了？）"]
    miss = [f for f in SUB_FIELDS if f not in t]
    if miss:
        errs.append(f"{where} 模板「{key}」缺字段：{'、'.join(miss)}")
        return errs
    if str(t.get("id")) != key:
        errs.append(f"{where} 模板「{key}」里的 id 是「{t.get('id')}」，与键名不一致"
                    f"（brief.json 写的是键名，对不上就等于选不到）")
    lic = font_licence(t.get("font"), table)
    if not lic["ok"]:
        errs.append(f"{where} 模板「{key}」{lic['why']}")
    try:
        size = int(t.get("size_px"))
        if not 20 <= size <= 240:
            errs.append(f"{where} 模板「{key}」的 size_px={size} 不像个字幕字号（20–240）")
    except (TypeError, ValueError):
        errs.append(f"{where} 模板「{key}」的 size_px 不是整数：{t.get('size_px')!r}")
    for k in ("line_spacing", "letter_spacing"):
        try:
            float(t.get(k))
        except (TypeError, ValueError):
            errs.append(f"{where} 模板「{key}」的 {k} 不是数字：{t.get(k)!r}")
    for k in ("color",):
        v = t.get(k)
        if not isinstance(v, str) or not v.strip() or v.strip().lower() == "none":
            errs.append(f"{where} 模板「{key}」的 {k} 必须是一个颜色（例如 #FFFFFF）")
    if not _stroke_css(t.get("stroke"), errs=errs, key=key, where=where):
        pass
    if not isinstance(t.get("emphasis"), dict):
        errs.append(f"{where} 模板「{key}」的 emphasis 必须是字典（color/bold/scale/plate）")
    else:
        em = t["emphasis"]
        on = [k for k in ("color", "bold", "scale", "plate")
              if em.get(k) not in (None, False, 0, 0.0, "", "none", 1.0)]
        # scale=1.0 / bold=False / plate=none 都算「没开」，所以上面把它们排除
        if not on:
            errs.append(f"{where} 模板「{key}」的 emphasis 一个方案都没开"
                        f"（颜色 / 加粗 / 放大 / 底衬 至少选一个）")
    pos = t.get("position")
    if not isinstance(pos, dict) or "center_y_px" not in pos:
        errs.append(f"{where} 模板「{key}」的 position 必须给 center_y_px")
    else:
        try:
            float(pos.get("center_y_px"))
        except (TypeError, ValueError):
            errs.append(f"{where} 模板「{key}」的 position.center_y_px 不是数字")
    try:
        mu = float(t.get("max_units_per_line"))
        if mu < 4:
            errs.append(f"{where} 模板「{key}」的 max_units_per_line={mu} 太窄"
                        f"（一条字幕至少放得下 4 个单位，否则一闪一闪）")
        elif mu > MAX_UNITS + 1e-9:
            errs.append(f"{where} 模板「{key}」的 max_units_per_line={mu} 超过共用上限 "
                        f"{MAX_UNITS:g}——模板只能收窄行宽，改模板不改判据")
    except (TypeError, ValueError):
        errs.append(f"{where} 模板「{key}」的 max_units_per_line 不是数字")
    cr = t.get("case_rules")
    if not isinstance(cr, dict):
        errs.append(f"{where} 模板「{key}」的 case_rules 必须是字典"
                    f"（keep_together / no_break_after / no_break_before）")
    else:
        for k in ("keep_together", "no_break_after", "no_break_before"):
            v = cr.get(k)
            if v is not None and not isinstance(v, (list, tuple)):
                errs.append(f"{where} 模板「{key}」的 case_rules.{k} 必须是列表")
    return errs


def _stroke_css(v, errs: list | None = None, key: str = "", where: str = "") -> str | None:
    """模板里的 stroke（none / CSS 串 / {width_px,color}）→ CSS 的 `-webkit-text-stroke` 值。"""
    if v is None:
        return None
    if isinstance(v, dict):
        try:
            w = float(v.get("width_px"))
        except (TypeError, ValueError):
            if errs is not None:
                errs.append(f"{where} 模板「{key}」的 stroke.width_px 不是数字")
            return None
        col = str(v.get("color") or "").strip()
        if not col:
            if errs is not None:
                errs.append(f"{where} 模板「{key}」的 stroke 给了宽度却没给颜色")
            return None
        return f"{w:g}px {col}"
    s = str(v).strip()
    if not s or s.lower() == "none":
        return None
    return s


def load_subtitle_templates(path: str | Path | None = None,
                            fonts_table: dict | None = None) -> dict:
    """读 `styles/subtitles.yaml` 并**整套校验**。坏了抛 `SubtitleTemplateError`。

    返回 {"canvas": {width,height}, "default": <id>, "templates": {id: 模板}}。
    校验的三条底线：字段齐全、字体在 fonts.yaml 里且许可可商用、行宽不超过共用上限。
    """
    p = Path(path) if path else SUB_TEMPLATES
    where = p.name if p.parent != SUB_TEMPLATES.parent else f"styles/{p.name}"
    if not p.exists():
        raise SubtitleTemplateError(f"字幕模板表不存在：{p}")
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        line = str(e).splitlines()[0] if str(e) else ""
        raise SubtitleTemplateError(f"字幕模板表 {where} 的 YAML 坏了：{line}")
    if not isinstance(doc, dict):
        raise SubtitleTemplateError(f"字幕模板表 {where} 顶层不是字典")
    tpls = doc.get("templates")
    if not isinstance(tpls, dict) or not tpls:
        raise SubtitleTemplateError(f"字幕模板表 {where} 里没有 templates（至少要 4 套）")
    table = fonts_table
    if table is None:
        import fonts as _f                                        # noqa: PLC0415
        table = _f.load()
    errs: list[str] = []
    for key, t in tpls.items():
        errs += _validate_template(t, str(key), where, table)
    if errs:
        raise SubtitleTemplateError("；".join(errs[:6]) + ("…" if len(errs) > 6 else ""))
    default = str(doc.get("default") or "")
    if default not in tpls:
        raise SubtitleTemplateError(
            f"字幕模板表 {where} 的 default=「{default}」不在 templates 里"
            f"（可选：{' / '.join(tpls)}）")
    canvas = doc.get("canvas") or {}
    return {"canvas": {"width": int(canvas.get("width") or 1080),
                       "height": int(canvas.get("height") or 1920)},
            "default": default,
            "templates": {str(k): v for k, v in tpls.items()}, "path": str(p)}


def subtitle_template(tid: str, path: str | Path | None = None,
                      fonts_table: dict | None = None) -> dict:
    """按 id 取一套模板。id 不存在给**可读**错误并列出可选 id。"""
    doc = load_subtitle_templates(path, fonts_table)
    t = doc["templates"].get(str(tid))
    if not t:
        raise SubtitleTemplateError(
            f"没有「{tid}」这套字幕模板。可选："
            + " / ".join(f"{k}（{v.get('name')}）" for k, v in doc["templates"].items()))
    return {**t, "_canvas": doc["canvas"]}


def template_to_subtitle(tpl: dict) -> dict:
    """模板 → 渲染器认识的 subtitle 字典（键名与 packs/*.yaml 的 subtitle 段一致）。

    这样 `render_hf.py` 里画字幕那条链路**一行都不用改**：字号、字色、描边、
    阴影、纵位置、行宽、强调色全从同一个字典来。多出来的 `line_spacing` /
    `letter_spacing` / `em_*` / `cap_plate` 只给 CSS 用，老包没有这些键 → 不出那几行。
    """
    em = _em_norm(tpl.get("emphasis") or {})
    pos = tpl.get("position") or {}
    plate = tpl.get("plate")
    return {
        "font": tpl["font"],
        "size_px": int(tpl["size_px"]),
        # 模板字号是设计选择，按它画（不反算）。只有模板会带这个键。
        "font_size_locked": True,
        "line_spacing": float(tpl.get("line_spacing") or 1.28),
        "letter_spacing": float(tpl.get("letter_spacing") or 0.01),
        "color": tpl["color"],
        "stroke": _stroke_css(tpl.get("stroke")),
        # 模板写 none 就**真的没有阴影**：渲染端 `sub.get("shadow") or 默认阴影` 的口径
        # 会把 None 当「没写」补一层默认影，那不是模板要的效果，所以这里输出 "none"。
        "shadow": "none" if str(tpl.get("shadow") or "").strip().lower() in ("", "none")
        else str(tpl["shadow"]),
        "center_y_px": int(pos.get("center_y_px") or 1294),
        "safe_bottom_px": int(pos.get("safe_bottom_px") or 300),
        "max_chars_per_line": int(round(float(tpl["max_units_per_line"]))),
        "entry": "hard_cut",
        "colorize_emphasis": bool(em.get("color")),
        "em_color": em.get("color"),
        "em_bold": em["bold"],
        "em_scale": em["scale"],
        "em_plate": em["plate"],
        # 强调块的内缩（上下, 左右）：比纸带小一档，才像「标记」而不是「第二块底板」
        "em_plate_pad": ([float(x) for x in (em.get("plate_pad_em") or [0.04, 0.16])[:2]]
                         if em["plate"] else None),
        "cap_plate": (None if not isinstance(plate, dict) else {
            "fill": str(plate.get("fill") or "rgba(246,236,219,0.72)"),
            "pad_em": list(plate.get("pad_em") or [0.26, 0.34]),
            "radius_em": float(plate.get("radius_em") or 0.16),
            # 极轻的纸边影（可选）：没有就是 none，老模板/包装包不会凭空长出一层阴影
            "edge": str(plate.get("edge") or "").strip() or None}),
        "light_text": False,
        "_template": tpl.get("id"),
    }


def _em_norm(em: dict) -> dict:
    """强调方案归一：`plate: none` 是字符串，别让它冒充「开了底衬」。"""
    plate = em.get("plate")
    plate_s = str(plate).strip() if plate is not None else ""
    pad = em.get("plate_pad_em")
    return {"color": (str(em.get("color")).strip() if em.get("color") else None),
            "bold": bool(em.get("bold")),
            "scale": float(em.get("scale") or 1.0),
            "plate": None if plate_s.lower() in ("", "none", "false") else plate_s,
            "plate_pad_em": ([float(x) for x in list(pad)[:2]] if isinstance(pad, (list, tuple))
                             else None)}


def _from_template(tpl: dict, canvas: dict) -> dict:
    """模板 → 判定/渲染共用的**生效口径**（check_subs 与 render_hf 都读这一份）。"""
    em = _em_norm(tpl.get("emphasis") or {})
    pos = tpl.get("position") or {}
    size = int(tpl["size_px"])
    lic = font_licence(tpl.get("font"))
    return {
        "template": True, "id": str(tpl.get("id")), "name": tpl.get("name"),
        "suits": tpl.get("suits"), "not_suits": tpl.get("not_suits"),
        "one_line": tpl.get("one_line"),
        "font": tpl["font"], "size_px": size,
        # 模板字号就是实际字号（渲染端 font_size_locked 同源），判定端也用它算像素
        "render_size_px": size,
        "line_spacing": float(tpl.get("line_spacing") or 1.28),
        "letter_spacing": float(tpl.get("letter_spacing") or 0.01),
        "color": tpl.get("color"), "stroke": _stroke_css(tpl.get("stroke")),
        "shadow": tpl.get("shadow"),
        "emphasis": dict(em), "em_color": em.get("color"),
        "center_y_px": int(pos.get("center_y_px") or 1294),
        "safe_bottom_px": int(pos.get("safe_bottom_px") or 300),
        "max_units_per_line": float(tpl["max_units_per_line"]),
        "case_rules": {k: list((tpl.get("case_rules") or {}).get(k) or [])
                       for k in ("keep_together", "no_break_after", "no_break_before")},
        "colorize": bool(em.get("color")),
        "plate": tpl.get("plate") if isinstance(tpl.get("plate"), dict) else None,
        "canvas": dict(canvas),
        "font_license": lic["license"], "font_license_alias": lic["alias"],
        "raw": tpl,          # 原始模板：`template_to_subtitle()` 要按原键名翻译
    }


def _from_pack(sub: dict, style: dict, canvas: dict) -> dict:
    """包装包自带的 subtitle 段 → 同一套生效口径（老项目的路）。"""
    emph = (style or {}).get("emphasis") or {}
    pack_canvas = (style or {}).get("canvas") or {}
    cv = {"width": int(pack_canvas.get("width") or canvas.get("width") or 1080),
          "height": int(pack_canvas.get("height") or canvas.get("height") or 1920)}
    font = sub.get("font")
    size = int(sub.get("size_px") or 72)
    render = size
    try:                                       # 与 render_hf 同一个反算（老包仍走它）
        import fonts as _f                                        # noqa: PLC0415
        render = int(_f.size_px(font, cv["width"], fallback=size))
    except Exception:                                             # noqa: BLE001
        render = size
    mc = sub.get("max_chars_per_line")
    try:
        # 包写的是「每行几个字」，当展示单位用；但**只许收窄**：包写 14 也不能judge得比 10 松
        mu = min(float(mc), MAX_UNITS) if mc else MAX_UNITS
    except (TypeError, ValueError):
        mu = MAX_UNITS
    em_col = sub.get("em_color") or emph.get("keyword_color") or "#FFD400"
    return {
        "template": False, "id": None, "name": (style or {}).get("label"),
        "one_line": (style or {}).get("look_notes"),
        "suits": None, "not_suits": None,
        "font": font, "size_px": size, "render_size_px": render,
        "line_spacing": float(sub.get("line_spacing") or 1.28),
        "letter_spacing": float(sub.get("letter_spacing") or 0.01),
        "color": sub.get("color") or "#FFFFFF",
        "stroke": sub.get("stroke") or None,
        "shadow": sub.get("shadow"),
        "emphasis": {"color": em_col, "bold": False, "scale": 1.0, "plate": None},
        "em_color": em_col,
        "center_y_px": int(sub.get("center_y_px") or 1294),
        "safe_bottom_px": int(sub.get("safe_bottom_px") or 300),
        "max_units_per_line": float(mu),
        "case_rules": {"keep_together": [], "no_break_after": [], "no_break_before": []},
        "colorize": bool(sub.get("colorize_emphasis", True)),
        "plate": None,
        "canvas": cv,
        "font_license": None, "font_license_alias": None,
    }


def subtitle_config(project: str | Path | None = None, style: dict | None = None,
                    path: str | Path | None = None,
                    fonts_table: dict | None = None) -> dict:
    """生效的字幕口径。优先级：项目选的模板 > 包装包的 subtitle 段 > 默认模板。

    `project` 给的是项目目录（读 `brief.json` 的 `subtitle_template`）；
    `style` 给的是包装包/样式包的字典（读它的 `subtitle` 段）。
    模板 id 写错**不静默退回**：抛 `SubtitleTemplateError`，让调用方报错——用户选了
    A 却按 B 出片，是比崩掉更坏的失败。
    """
    doc = load_subtitle_templates(path, fonts_table)
    brief: dict = {}
    if project:
        bp = Path(project) / "brief.json"
        if bp.exists():
            try:
                brief = json.loads(bp.read_text(encoding="utf-8")) or {}
            except json.JSONDecodeError:
                brief = {}
    tid = str(brief.get("subtitle_template") or "").strip()
    if tid:
        tpl = doc["templates"].get(tid)
        if not tpl:
            raise SubtitleTemplateError(
                f"brief.json 里选的 subtitle_template「{tid}」不存在。可选："
                + " / ".join(doc["templates"]))
        cfg = _from_template(tpl, doc["canvas"])
        cfg["source"] = f"template:{tid}"
        return cfg
    sub = ((style or {}).get("subtitle") or {})
    if sub:
        cfg = _from_pack(sub, style or {}, doc["canvas"])
        cfg["source"] = "pack"
        return cfg
    cfg = _from_template(doc["templates"][doc["default"]], doc["canvas"])
    cfg["source"] = f"default:{doc['default']}"
    return cfg


def build(segments, index_by_id, chars_cache, lib, load_chars,
          colorize: bool = True, fps: int = 30,
          max_units: float | None = None, case_rules: dict | None = None) -> list[dict]:
    """`colorize=False` 时一个字都不变色（Jerry 2026-11-09：重点词变色取消）。

    每条字幕带 `units`（展示单位）与 `sf/ef`（半开帧窗 [sf, ef)）。
    帧窗是后面所有重叠/对齐判据的唯一口径——秒数保留只为向后兼容。

    `max_units` / `case_rules` 来自字幕模板（`styles/subtitles.yaml` 的
    `max_units_per_line` 与 `case_rules`）：**不传就是原来的 10 单位**，
    所以没选模板的项目走的是同一条路、同样的数（向后兼容）。
    """
    with width_scope(max_units, case_rules):
        return _build(segments, index_by_id, chars_cache, lib, load_chars,
                      colorize=colorize, fps=fps)


def _build(segments, index_by_id, chars_cache, lib, load_chars,
           colorize: bool = True, fps: int = 30) -> list[dict]:
    """`build()` 的本体：口径已经由 `width_scope` 设好，这里只跟 `_W` 读。"""
    MU = _W["max_units"]
    cues: list[dict] = []
    lex = lexicon.load(Path(lib) / "_cutskills")
    # 探针账要每跑一次重算：上一次的「量不成」不能污到这一片。
    LAST_PROBE.update({"segments": 0, "measured": 0, "missing": [], "silent_segments": 0})
    for blk in _items(segments, index_by_id, chars_cache, lib, load_chars, lex):
        items = blk["items"]
        if blk.get("ev"):
            items = retime_to_script(items, blk["ev"])
        _groups = (regroup_script(items) if blk.get("ev") else regroup(items))
        # 停留超 3.2s 而窗口里又有真静音的，在静音处补一刀（T-032）。
        # 放在 regroup 之后：太短的句合并完可能又跨过一处停顿，那时才看得出它撑得太久。
        _groups = split_breath_groups(_groups)
        for g, why in _groups:
            raw = "".join(t for t, _, _ in g)
            text = "".join(ch for ch in raw if ch not in PUNCT and ch != SENTINEL).strip()
            text = lexicon.strip_noise(text)
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
    _report_probe()
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
    # 太短的条：先往后面的空隙里伸长，缝隙不够就往前借，还不行就并进前一条。
    # 10 个单位的硬上限会把一条切成两条，快语速下后半截可能只剩 0.5s——观众只看到闪一下。
    # 宁可在标点处多停 0.2 秒，也不要给一条看不清的字幕。
    fixed: list[dict] = []
    for c in out:
        if fixed and c["end"] - c["start"] < MIN_HOLD - 0.02:
            need = MIN_HOLD - (c["end"] - c["start"])
            room = max(0.0, c["start"] - fixed[-1]["end"] - 0.02)
            if room > 0:
                c["start"] = round(c["start"] - min(need, room), 3)
                need = MIN_HOLD - (c["end"] - c["start"])
            if need > 0.02:                      # 借不到就并进前一条（前一条也得装得下）
                merged_u = fixed[-1]["units"] + c["units"] + 1.0
                # 合并只许发生在同一格内：跨格拼出来的字幕一边说「茶」、
                # 一边已经把下一格的「我哋一共」写出来了，观众看到的字比听到的话超前。
                if merged_u <= MU and fixed[-1].get("el") == c.get("el"):
                    fixed[-1]["text"] += c["text"]
                    fixed[-1]["html"] = emphasis_html(fixed[-1]["text"]) if colorize else None
                    fixed[-1]["chars"] = len(fixed[-1]["text"])
                    fixed[-1]["units"] = display_units(fixed[-1]["text"])
                    fixed[-1]["end"] = max(fixed[-1]["end"], c["end"])
                    continue
        fixed.append(c)
    out = fixed
    for i, c in enumerate(out):
        if i + 1 < len(out) and c["end"] - c["start"] < MIN_HOLD - 0.02:
            room = max(0.0, out[i + 1]["start"] - c["end"] - 0.02)
            if room > 0:
                c["end"] = round(c["end"] + min(MIN_HOLD - (c["end"] - c["start"]), room), 3)

    # 帧窗收尾：必须 ef > sf，且相邻半开窗不相交（相接合法）
    for i, c in enumerate(out):
        if c["ef"] <= c["sf"]:
            c["ef"] = c["sf"] + max(1, int(round((c["end"] - c["start"]) * fps)))
        if i:
            c["sf"] = max(c["sf"], out[i - 1]["ef"])
            if c["ef"] <= c["sf"]:
                c["ef"] = c["sf"] + 1
    return out
