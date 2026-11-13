#!/usr/bin/env python3
"""场记口令 / 指导语 / 重说 —— 检出并把它们挡在片子外面。

为什么要有这一层：

真实口播素材里，主播能用的那几句之间夹着别人的话——摄影师在喊「三、二、一」，
在说「再来一次」「慢啲讲」「大声啲」，主播自己还会把同一句开头重说两遍。
ASR 是照声音抄的，它把这些话一样写成句子，于是选句的时候只要按 ASR 边界切，
口令就跟着进了片子：画面上是主播在听指挥，声音里是「三二一」。

这不是剪辑判断，是**素材事实**：凡是说话的人不是主播的段落，一律不该进片。
所以它必须先于剪辑方案判定，并且在验收层留一道闸。

检出靠词级时间戳（ASR 的 tokens/timestamps），不靠句子边界——因为 ASR 常把
口令和正话粘成一句（「三二一组所以大家可以…」），按句切一定带着口令走。

三类：

1. `count` 计数口令：三二一 / 一二三 / 三二 / 1,2,3（允许中间夹标点、气口）。
2. `cue`  指导语：再来一次、慢啲、大声啲、望住镜头、停一下……
3. `retake` 重说：窗口内同一开头（≥3 字）说了两遍——第一遍是要废掉的。

用法：

  python3 scripts/cue_guard.py --scan   <素材库根>            # 全库扫一遍，看谁有口令
  python3 scripts/cue_guard.py --check  <项目目录>            # 时间轴的音频窗口有没有踩到
  python3 scripts/cue_guard.py --fix    <项目目录>            # 就地修剪窗口两端（只动端点）
  python3 scripts/cue_guard.py --words  <素材库根> <asset id> # 打印某段的词级时间轴

`--fix` 只会往里收，不会挪位置：起点往后过口令、终点往前避开口令。
中间夹着的口令不修——那种要换素材或用代码画面盖住，机器不许自作主张。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import evidence_guard as EG  # noqa: E402  素材 → 词级真值只走它，本文件不再自己拼缓存路径

# ---------------------------------------------------------------- 词表

# 计数口令。ASR 把它和后面的字粘在一起（「三二一组」里的「组」其实是主播的起头），
# 所以匹配只要求这三个数字字按序出现，不管中间隔什么。
COUNT_PATTERNS = [
    r"三[\s，,、.]*二[\s，,、.]*一",
    r"一[\s，,、.]*二[\s，,、.]*三",
    r"三[\s，,、.]*二(?![\s，,、.]*十)",   # 「三二」也常是倒数被截断
]

# 指导语分两档，因为误判的代价不一样：
# STRONG 只可能是别人在指挥 —— 命中就当错误，片子不许带着它出。
# WEAK 正常对话里也会有（「开始」「等一下」），只在窗口两端踩到时提醒。
STRONG_CUES = [
    "再来一次", "再嚟一次", "再一次", "重来", "重拍", "重录", "重讲",
    "讲多次", "讲慢啲", "讲快啲", "慢啲讲", "大声啲", "细声啲", "大点声",
    "望住镜头", "看镜头", "唔好郁", "唔好望", "停一下", "停一停",
    "收音", "麦克风", "打灯", "最后一句", "一句就够", "预备好",
]
WEAK_CUES = [
    "预备", "准备", "开始", "开始吧", "再来", "再嚟", "讲慢", "讲快",
    "慢啲", "慢一点", "快啲", "大声", "细声", "小声", "等阵", "等一下",
    "稍等", "收到", "继续", "可以了", "ok好", "好啦好", "试下", "试一次",
]
CUE_WORDS = [(w, True) for w in STRONG_CUES] + [(w, False) for w in WEAK_CUES]

# 弱口令的词界宽容：**单独成句才算口令**。
#
# 弱口令都是正常词（开始 / 再来 / 收到 / 继续…），只要按子串匹配，正常说话就全被当成指导语——
# 「所以**开始**我们做的品类」（「一开始」）、「明天咱上**再来**一趟」、「容易就是说**收到**空气的水化」
# （ASR 把「说到」写歪）三条真的都踩中过（T-043）。区别不在词本身，在**句子长度**：
# 导演喊口令是一句短话（最多多带两三个字：好 / 来 / 一下），而夹在长句中间的是内容。
# 所以判「口令所在这句还剩几个字」，跟长度阈值（停 ≥0.90s / 端点 0.8s）无关，阈值一律不动。
# 宽容只留 1 个字（「好开始」「开始吧」这种口气词）：「一开始就完」剩 3 个字就已经是内容了，
# 实测这一条在柒造库里自主播句中断（T-043 复测）。
WEAK_SLACK = 1

# 只判「窗口边缘」的宽容：口令后面通常有个气口，切到气口中间最安全。
EDGE_PAD = 0.06
RETAKE_MIN_LEN = 3          # 少于 3 个字的重说不算（「我我」这种口吃归字幕管）
RETAKE_WINDOW = 4.0         # 同一开头在 4 秒内出现两次才算重说


# ---------------------------------------------------------------- 取词级时间轴

def _timeline_of(chars) -> list[dict]:
    """`(词, 起点)` 序列 → `[{t,start,end}]`。

    终点取下一个词的起点（封顶 0.8s），最后一个词给 0.3s——与改造前同一条口径：
    下游（`phrase_views` 的停顿、`speech_after` 的有声时长、`trim` 的词对齐）都按 end 算。
    """
    seq = [(str(t), float(s)) for t, s in chars]
    out: list[dict] = []
    for i, (tok, st) in enumerate(seq):
        nx = seq[i + 1][1] if i + 1 < len(seq) else st + 0.3
        out.append({"t": tok, "start": round(st, 3), "end": round(min(nx, st + 0.8), 3)})
    return out


def _hand_words(ws: list) -> list[dict]:
    """索引里手写的 `transcript.words` 归一成 `[{t,start,end}]`（缺 end 就按下一个词算）。"""
    out: list[dict] = []
    for i, w in enumerate(ws):
        st = float(w.get("start") or 0.0)
        en = w.get("end")
        if en is None:
            en = float(ws[i + 1].get("start") or st) if i + 1 < len(ws) else st + 0.3
        out.append({"t": str(w.get("t") or ""), "start": round(st, 3), "end": round(float(en), 3)})
    return out


def words_and_source(asset: dict, root: Path | None = None) -> tuple[list[dict], str]:
    """素材记录 → (词级时间轴, 真值等级 `word` / `sentence` / `none`)。

    优先序：索引里手写的 `transcript.words`（人校对过，比 ASR 准）→ ASR 缓存。

    缓存那一步**只走** `evidence_guard.chars_of_asset`：它按「新键 `h12(id)` + 索引里的
    `legacy_key`」两套键找，并把切片 `-v3cNNNNN` 名字里的秒数拼回绝对时间。这里不再
    自己拼缓存路径——之前自己拼的那版只认 `h12(id)` 和 `-v3c00000`，素材一改名就整库
    静默 0 命中，「别人的话不许进片」这条拦截表面在跑、实际一条都没查（T-038）。

    第二项是**必须往上传**的：返回空列表不等于「这段没有口令」，可能只是没有词级真值。
    """
    tr = asset.get("transcript") or {}
    ws = tr.get("words")
    if ws:
        return _hand_words(ws), EG.WORD
    if root is None:
        return [], EG.NONE
    aid = asset.get("id") or asset.get("path") or ""
    truth = EG.chars_of_asset(Path(root), aid)
    if not truth.chars:
        return [], truth.source            # 句级或一级都没有：判不了，不许当「干净」
    return _timeline_of(truth.chars), EG.WORD


def words_of(asset: dict, root: Path | None = None) -> list[dict]:
    """素材记录 → 词级时间轴 `[{t,start,end}]`。

    只保留列表形态给 `find_cues` / `trim` 用；**等级在这里丢掉了**——要判「到底有没有
    词级真值」请用 `words_and_source`。
    """
    return words_and_source(asset, root)[0]


PHRASE_GAP = 0.45          # 与 L1 建句同一条：停顿超过 0.45s 就是另一句


def phrase_views(words: list[dict]) -> list[dict]:
    """词级时间轴 → 句子视图（只带 start/end/text），供重说检测用。"""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 9.9
        if gap > PHRASE_GAP or i == len(words) - 1:
            t = "".join(x["t"] for x in cur).strip()
            if len([c for c in t if _is_word_char(c)]) >= 2:
                out.append({"start": cur[0]["start"], "end": round(cur[-1]["end"], 3), "text": t})
            cur = []
    return out


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or "\u4e00" <= ch <= "\u9fff"


# ---------------------------------------------------------------- 检出

def _phrase_spans(words: list[dict]) -> list[tuple[int, int]]:
    """词级时间轴 → 每个**小句**在「净串」里的字符区间 `[a, b)`。

    净串 = 剔掉标点后的全文（`find_cues` 里那个 `cstr`），这里按同一条口径重建
    （逐词的 `_is_word_char` 字符）。切在两种地方：

    - 停顿 > `PHRASE_GAP`；
    - 标点词（ASR 把它当独立 token 吐出来）。

    下标点那刀是必须的：喊口令的人常在口令前先撂一句评语（「太鸡贼了，再来下」），
    按停顿切会把整句算成一句长话，真口令被当成内容放过去（T-043 实测）。
    不能直接数 `phrase_views()` 的文本长度，那个会把不足 2 字的碎片句丢掉，位置就串了。
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    cur_start = 0
    for i, w in enumerate(words):
        tok = str(w["t"])
        n = sum(1 for c in tok if _is_word_char(c))
        if n == 0:                      # 标点：在这里断句
            if pos > cur_start:
                spans.append((cur_start, pos))
            cur_start = pos
            continue
        pos += n
        gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 9.9
        if gap > PHRASE_GAP or i == len(words) - 1:
            if pos > cur_start:
                spans.append((cur_start, pos))
            cur_start = pos
    return spans


def _phrase_of(spans: list[tuple[int, int]], k: int) -> tuple[int, int] | None:
    for a, b in spans:
        if a <= k < b:
            return a, b
    return None


def find_cues(words: list[dict]) -> list[dict]:
    """词级时间轴 → 口令区间 [{kind, t0, t1, text}]，按时间排序。"""
    if not words:
        return []
    text = "".join(w["t"] for w in words)
    # 字符位置 → 词序号，用于把正则命中位置换算回时间
    pos2i: list[int] = []
    for i, w in enumerate(words):
        pos2i.extend([i] * max(1, len(w["t"])))
    pos2i = pos2i[:len(text)]

    def span(i0: int, i1: int) -> tuple[float, float]:
        return words[i0]["start"], max(words[i1]["end"], words[i0]["start"] + 0.2)

    hits: list[dict] = []

    # 先把标点剔掉做一份可匹配的串，同时留一张「净串位置 → 原串位置」对照
    clean, back = [], []
    for n, ch in enumerate(text):
        if _is_word_char(ch):
            clean.append(ch)
            back.append(n)
    cstr = "".join(clean)
    spans = _phrase_spans(words)

    strong_cov: set[int] = set()          # 已确认是口令的净串位置（弱口令判词界时不算「内容」）

    for pat in COUNT_PATTERNS:
        for m in re.finditer(pat, cstr):
            a, b = back[m.start()], back[m.end() - 1]
            t0, t1 = span(pos2i[a], pos2i[b])
            hits.append({"kind": "count", "t0": round(t0, 3), "t1": round(t1, 3),
                         "text": text[a:b + 1]})
            strong_cov.update(range(m.start(), m.end()))

    weak: list[tuple[int, int, dict]] = []
    for cw, strong in CUE_WORDS:
        k = cstr.find(cw)
        while k >= 0:
            a, b = back[k], back[k + len(cw) - 1]
            t0, t1 = span(pos2i[a], pos2i[b])
            h = {"kind": "cue" if strong else "cue-weak",
                 "t0": round(t0, 3), "t1": round(t1, 3), "text": text[a:b + 1]}
            if strong:
                hits.append(h)
                strong_cov.update(range(k, k + len(cw)))
            else:
                weak.append((k, k + len(cw), h))
            k = cstr.find(cw, k + len(cw))

    # 弱口令过词界：这句（小句）里**除掉口令字**之后还剩几个字。
    # 剩下的都是内容 → 这个「开始 / 再来 / 收到」是话里的词，不是喊给主播听的。
    # 「除掉」要连别的口令一起算（`strong_cov`）：连喊两句口令时（「等一下再一次」），
    # 弱口令旁边站着的也是口令，不能被当成内容把它顶掉。
    for k, e, h in weak:
        sp = _phrase_of(spans, k)
        if sp is None:
            hits.append(h)             # 跨小句找不到归属：宁可报，不静默
            continue
        rest = [i for i in range(sp[0], sp[1]) if i not in strong_cov and not (k <= i < e)]
        if len(rest) > WEAK_SLACK:
            continue
        hits.append(h)

    # 重说只认两种：句内把开头连说两遍、相邻两句开头一样。
    # 别用 n-gram 泛匹配——「两个门口…两个门口」是排比，不是重说，机器分不清就该闭嘴。
    # 两条都必须排除**纯口吃**：「对对对对对对对」是卡住，不是「这一遍作废、下一遍才算」。
    # 判法是看重复段**后面还有没有别的话**——重说的人一定会接着往下说完，口吃的人只是同一口气。
    for ph in phrase_views(words):
        t = ph["text"].replace(" ", "")
        m = re.match(r"^(.{3,8}?)\1", t)
        if m:
            unit = m.group(1)
            tail = t[len(unit) * 2:]
            if len(set(unit)) == 1 or not tail:
                continue                      # 单字反复 / 说完就停：口吃，不算重说
            seg = t[:len(unit) * 2]
            t0 = ph["start"]
            t1 = min(ph["end"], t0 + 0.9)
            hits.append({"kind": "retake", "t0": round(t0, 3), "t1": round(t1, 3),
                         "text": f"{seg}…（同一句开头说了两遍）"})
    phs = [p for p in phrase_views(words) if p["end"] - p["start"] >= 0.25]
    for prev, cur in zip(phs, phs[1:]):
        a, b = prev["text"].replace(" ", ""), cur["text"].replace(" ", "")
        n = 4
        if len(a) >= n and len(b) >= n and a[:n] == b[:n] and cur["start"] - prev["end"] < 0.6:
            hits.append({"kind": "retake", "t0": round(prev["start"], 3),
                         "t1": round(prev["end"], 3),
                         "text": f"{a[:n]}…（前一遍作废，后一遍才算）"})

    # 合并重叠 / 相邻（同一个人连喊两句口令，算一段）
    hits.sort(key=lambda h: (h["t0"], h["t1"]))
    merged: list[dict] = []
    for h in hits:
        if merged and h["t0"] <= merged[-1]["t1"] + 0.12:
            last = merged[-1]
            last["t1"] = max(last["t1"], h["t1"])
            if h["kind"] not in last["kind"].split("+"):
                last["kind"] = last["kind"] + "+" + h["kind"]
            if h["text"] not in last["text"]:
                last["text"] = last["text"] + "|" + h["text"]
        else:
            merged.append(dict(h))
    return merged


def _speech_units(t: str) -> int:
    """汉字算 1，连着的字母/数字算 1；单蹦的字母（ASR 的 o/k 噪声）不算。"""
    cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    latin = len(re.findall(r"[A-Za-z]{2,}|\d+", t))
    return cjk + latin


def speech_after(words: list[dict], t: float, until: float) -> float:
    """t 之后到 until 之间，真正在说话的时间（连着的字 ≥0.3s，且至少 2 个字）。"""
    run, total = [], 0.0
    for w in words:
        if w["start"] < t or w["end"] > until + 0.001:
            continue
        run.append(w)
    if not run:
        return 0.0
    span = run[-1]["end"] - run[0]["start"]
    if span >= 0.3 and _speech_units("".join(x["t"] for x in run)) >= 2:
        total = span
    return total


def speech_before(words: list[dict], t: float, since: float) -> float:
    run = [w for w in words if w["start"] >= since and w["end"] <= t + 0.001]
    if not run:
        return 0.0
    span = run[-1]["end"] - run[0]["start"]
    return span if span >= 0.3 else 0.0


def _next_word_start(words: list[dict], t: float) -> float | None:
    for w in words:
        if w["start"] >= t - 0.001:
            return w["start"]
    return None


TAIL_SLACK = 0.8            # 口令离终点不到 0.8s，就算「在尾巴上」，直接收终点
HEAD_SLACK = 0.8            # 口令离起点不到 0.8s，就算「在头顶上」，直接抬起点


def trim(a: float, b: float, cues: list[dict], words: list[dict] | None = None
         ) -> tuple[float, float, list[str]]:
    """把窗口两端收过口令。只动端点、不改位置。

    口令落在窗口两头（离端点 < 0.8s）→ 端点让开；
    口令卡在中间、两边都有正话 → 不自动切（那是一个镜头要拆成两个，人的决定）。
    """
    notes: list[str] = []
    for _ in range(8):
        moved = False
        for c in cues:
            if c["t0"] - EDGE_PAD <= a < c["t1"]:
                a = c["t1"] + EDGE_PAD
                if words:
                    nx = _next_word_start(words, a)
                    if nx is not None and nx - a < 0.4:
                        a = nx            # 别从半个字里起，对齐到词
                notes.append(f"起点后移过 {c['kind']}「{c['text']}」")
                moved = True
                continue
            if c["t0"] < b <= c["t1"] + EDGE_PAD:
                b = c["t0"] - EDGE_PAD
                notes.append(f"终点前收避 {c['kind']}「{c['text']}」")
                moved = True
                continue
            # 整段口令落在窗口内：靠尾巴 / 靠头顶，才让端点让步
            if a + 0.02 < c["t0"] and c["t1"] < b - 0.02:
                tail_silent = words is not None and speech_after(words, c["t1"], b) < 0.3
                head_silent = words is not None and speech_before(words, c["t0"], a) < 0.3
                if tail_silent and not head_silent:
                    b = c["t0"] - EDGE_PAD
                    notes.append(f"口令「{c['text']}」之后没话了，终点收到它前面")
                    moved = True
                elif head_silent and not tail_silent:
                    a = c["t1"] + EDGE_PAD
                    if words:
                        nx = _next_word_start(words, a)
                        if nx is not None and nx - a < 0.4:
                            a = nx
                    notes.append(f"口令「{c['text']}」之前没话，起点抬到它后面")
                    moved = True
                elif b - c["t1"] <= TAIL_SLACK:
                    b = c["t0"] - EDGE_PAD
                    notes.append(f"尾巴上有一句 {c['kind']}「{c['text']}」，终点收到它前面")
                    moved = True
                elif c["t0"] - a <= HEAD_SLACK:
                    a = c["t1"] + EDGE_PAD
                    if words:
                        nx = _next_word_start(words, a)
                        if nx is not None and nx - a < 0.4:
                            a = nx
                    notes.append(f"头上有 {c['kind']}「{c['text']}」，起点抬到它后面")
                    moved = True
        if not moved:
            break
    return round(max(0.0, a), 3), round(b, 3), notes


def inside(a: float, b: float, cues: list[dict]) -> list[dict]:
    """完全落在窗口内部（两端切不掉）的口令——这类只能换素材。"""
    return [c for c in cues if a + 0.05 < c["t0"] and c["t1"] < b - 0.05]


# ---------------------------------------------------------------- 库 / 项目

def load_index(root: Path) -> dict:
    f = root / "_cutskills" / "index.json"
    if not f.exists():
        raise SystemExit(f"没有索引：{f}")
    return json.loads(f.read_text())


def scan(root: Path) -> int:
    idx = load_index(root)
    bad = 0
    blind = 0
    for a in idx.get("assets") or []:
        tr = a.get("transcript") or {}
        ws, src = words_and_source(a, root)
        if src != EG.WORD or not ws:
            # 没有词级真值不等于「这段干净」：句级真值没有逐字时间，拿它猜口令
            # 会把「三二一组」整句判成口令，也会把真口令放过。有声音的素材才计数——
            # 照片/文档本来就没有台词，把它们算进「判不了」只会把真缺口淹掉。
            if a.get("audio"):
                blind += 1
            continue
        cues = find_cues(ws)
        if not cues:
            continue
        bad += 1
        print(f"\n{a['id']}  {a.get('duration', 0):.1f}s")
        for c in cues:
            print(f"   {c['kind']:12s} {c['t0']:7.2f}-{c['t1']:7.2f}  {c['text']}")
        # 哪些 ASR 句被这些口令污染了
        for p in tr.get("phrases") or []:
            for c in cues:
                if p["start"] < c["t1"] and p["end"] > c["t0"]:
                    print(f"      ⚠ 句子被污染 {p['start']:.2f}-{p['end']:.2f} {p['text']}")
                    break
    print(f"\n共 {bad} 段带口令 / 重说")
    if blind:
        print(f"🟡 {blind} 段没有词级真值，口令判不了（句级真值没有逐字时间，不拿它猜）")
    return 0 if bad == 0 else 1


def _timeline(project: Path) -> tuple[Path, dict]:
    f = project / "timeline.json"
    if not f.exists():
        raise SystemExit(f"没有时间轴：{f}")
    return f, json.loads(f.read_text())


def _root_of(project: Path) -> Path:
    """项目目录 → 素材库根（`<_cutskills>/projects/<name>` 的上级上级）。"""
    p = project.resolve()
    for up in p.parents:
        if (up / "index.json").exists() and up.name == "_cutskills":
            return up.parent
    for up in p.parents:
        if (up / "_cutskills" / "index.json").exists():
            return up
    raise SystemExit(f"找不到素材库根（从 {project} 往上找不到 _cutskills/index.json）")


def check(project: Path) -> int:
    root = _root_of(project)
    idx = load_index(root)
    by_id = {a["id"]: a for a in idx.get("assets") or []}
    f, tl = _timeline(project)
    errs, notes, blind = [], [], []
    for e in tl.get("elements") or []:
        au = e.get("audio") or {}
        aid = au.get("asset")
        if not aid:
            continue
        a = by_id.get(aid)
        if not a:
            continue
        ws, src = words_and_source(a, root)
        if src != EG.WORD or not ws:
            blind.append(f"el{e['n']} 该格没有词级真值，口令判不了（{aid}，"
                         + ("只到句级 transcript.phrases）" if src == EG.SENTENCE
                            else "索引和缓存都没覆盖这段）"))
            continue
        cues = find_cues(ws)
        if not cues:
            continue
        a0, a1 = float(au.get("in") or 0), float(au.get("out") or 0)
        na, nb, why = trim(a0, a1, cues, ws)
        for c in cues:
            if na + 0.02 < c["t0"] and c["t1"] < nb - 0.02:
                errs.append(f"el{e['n']} 窗口内夹着 {c['kind']}「{c['text']}」"
                            f"（{c['t0']:.2f}-{c['t1']:.2f}，两边都有正话，切不掉，要拆镜或换素材）")
        if abs(na - a0) > 0.01 or abs(nb - a1) > 0.01:
            notes.append(f"el{e['n']} 端点踩到口令：{a0:.2f}-{a1:.2f} → 应改 {na:.2f}-{nb:.2f}"
                         f"（{'; '.join(why)}）")
    for m in errs:
        print(f"🔴 {m}")
    for m in notes:
        print(f"🟡 {m}")
    for m in blind:
        print(f"🟡 {m}")
    if not errs and not notes and not blind:
        print("🟢 音频窗口里没有场记口令 / 指导语 / 重说")
    return 0 if not errs and not notes and not blind else (2 if errs else 1)


def fix(project: Path) -> int:
    root = _root_of(project)
    idx = load_index(root)
    by_id = {a["id"]: a for a in idx.get("assets") or []}
    f, tl = _timeline(project)
    changed = 0
    for e in tl.get("elements") or []:
        au = e.get("audio") or {}
        aid = au.get("asset")
        if not aid or aid not in by_id:
            continue
        ws = words_of(by_id[aid], root)
        cues = find_cues(ws)
        if not cues:
            continue
        a0, a1 = float(au.get("in") or 0), float(au.get("out") or 0)
        na, nb, why = trim(a0, a1, cues, ws)
        stuck = [c for c in cues if na + 0.02 < c["t0"] and c["t1"] < nb - 0.02]
        if stuck:
            for c in stuck:
                print(f"🔴 el{e['n']} 窗口中间夹着「{c['text']}」——不自动修，要拆镜或换素材")
            continue
        if abs(na - a0) <= 0.01 and abs(nb - a1) <= 0.01:
            continue
        au["in"], au["out"] = na, nb
        # 音频短了，画格得跟着短，否则声音说完画面还杵在那儿
        span = round(nb - na, 3)
        if e.get("kind") == "clip" and e.get("asset"):
            same = e["asset"] == aid and abs(float(e["in"]) - a0) < 0.05 and abs(float(e["out"]) - a1) < 0.05
            if same:                       # 这一格就是口播本身：两边一起收
                e["in"], e["out"] = na, nb
            else:                          # B-roll 盖着口播：画面跟着音频长度走
                lim = (by_id.get(e["asset"]) or {}).get("duration") or 0
                want = round(float(e["in"]) + span, 3)
                e["out"] = round(min(want, lim - 0.05), 3) if lim else want
        changed += 1
        print(f"✂ el{e['n']} 音频 {a0:.2f}-{a1:.2f} → {na:.2f}-{nb:.2f}"
              f"（{'; '.join(why)}）· 画格 {e.get('in', 0):.2f}-{e.get('out', 0):.2f}")
    if changed:
        f.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n")
        print(f"\n改了 {changed} 个音频窗口。时间轴总长 {tl['duration_s']:.2f}s。")
        print("⚠ 时间轴变了，方案卡要重出、要重新上锁。")
    else:
        print("没有需要修的窗口。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="场记口令 / 指导语 / 重说守卫")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", metavar="库根")
    g.add_argument("--check", metavar="项目目录")
    g.add_argument("--fix", metavar="项目目录")
    g.add_argument("--words", nargs=2, metavar=("库根", "asset id"))
    a = ap.parse_args(argv)
    if a.scan:
        return scan(Path(a.scan))
    if a.check:
        return check(Path(a.check))
    if a.fix:
        return fix(Path(a.fix))
    root, aid = Path(a.words[0]), a.words[1]
    idx = load_index(root)
    rec = next((x for x in idx["assets"] if x["id"] == aid), None)
    if not rec:
        raise SystemExit(f"库里没有 {aid}")
    ws = words_of(rec, root)
    if not ws:
        src = words_and_source(rec, root)[1]
        print(f"🟡 该素材没有词级真值，口令判不了（{rec.get('id')}，"
              + ("只到句级 transcript.phrases）" if src == EG.SENTENCE else "索引和缓存都没覆盖这段）"))
        return 1
    for w in ws:
        print(f"{w['start']:7.2f}-{w['end']:7.2f}  {w['t']}")
    for c in find_cues(ws):
        print(f"   {c['kind']} {c['t0']:.2f}-{c['t1']:.2f} {c['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
