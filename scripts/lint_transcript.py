#!/usr/bin/env python3
"""CutSkills · 转写出「待纠错清单」给人/大模型判断。

脚本只做统计，不下结论。判据来自三份本地数据，不用外网也不用字典：

  · **语料**（库根目录的 .md，比如品牌语料手册）—— 正确写法长什么样
  · **转写** —— 模型实际听成了什么
  · **同音**（pypinyin，纯 Python，已随 skill 放在 scripts/vendor/）—— 是不是同一个音

最硬的信号是「语料确认的正确写法 vs 转写里占优的同音错写」：
柒造这一库，「石灰基」在语料 22 次、在转写里 3 次，而「石灰机」30 次、语料里 0 次；
「瑞加」在语料 11 次、转写里 0 次，被写成「瑞家/瑞佳」。这类差异不会自己消失。

反过来，「来的/实际/视频」这类在语料里也大量出现的词，属于正常用词，不是错写——
所以用「错写候选在语料里出现过就不算错写」这条来压噪，比任何人工词表都准。

判断和决定写进 `_cutskills/lexicon.json`，然后 `index_assets.py --stage rephrase` 生效。

清单第一节是**语义常识扫描**（T-017，准绳 A5）：反常物理量级（「大约有十万米高」）、
同音残词（「效果好先」「生万鱼池」）、口吃重尾（「讲下下」）。这三类靠语料对账判不出来——
语料里根本没有这句话；它们靠的是世界知识，判据全在 `scripts/data/` 的两张表里，
引擎与上屏门禁（`check_subs.py`）共用同一份。

用法：
  lint_transcript.py --library "<素材库>" [--top 40]
                      [--suspects <表>] [--magnitudes <表>] [--worklist <json>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]+")
STOP = set("就是 这个 那个 什么 我们 他们 自己 可以 因为 所以 但是 其实 然后 现在 一个".split())
HINT = Path(__file__).resolve().parent / "vendor"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexicon  # noqa: E402  同音残词与口吃重尾的判据只有一份，就写在 lexicon 里


def load_index(lib: Path) -> list[dict]:
    p = lib / "_cutskills" / "index.json"
    return json.loads(p.read_text())["assets"] if p.exists() else []


def all_text(assets: list[dict]) -> list[tuple[str, str]]:
    """(素材, 短语文本) —— 保留归属，方便回到具体句子核对。"""
    out = []
    for a in assets:
        for p in ((a.get("transcript") or {}).get("phrases") or []):
            out.append((a["id"], p["text"]))
    return out


def corpus_terms(lib: Path) -> Counter:
    c: Counter = Counter()
    for p in sorted(lib.glob("*.md")):
        if p.name.startswith("素材索引"):          # 索引是脚本生成的，不能当语料
            continue
        txt = p.read_text(errors="ignore")
        for run in CJK.findall(txt):
            for n in range(2, 6):
                for i in range(len(run) - n + 1):
                    c[run[i:i + n]] += 1
    return c


def pinyin():
    """字符 → 不带声调的拼音；没有 pypinyin 就返回 None（降级但不崩）。"""
    sys.path.insert(0, str(HINT))
    try:
        from pypinyin import lazy_pinyin, Style
    except Exception:                                              # noqa: BLE001
        return None
    cache: dict[str, str] = {}

    def py(s: str) -> str:
        out = []
        for ch in s:
            if ch not in cache:
                try:
                    cache[ch] = "".join(lazy_pinyin(ch, style=Style.NORMAL)) or ch
                except Exception:                                  # noqa: BLE001
                    cache[ch] = ch
            out.append(cache[ch])
        return " ".join(out)

    return py


def maximal(terms: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """去掉被更长候选包住的子串：「威尼斯」在列表里时，不该再出现「威尼」「尼斯」。"""
    return [(w, n) for w, n in terms
            if not any(w != o and w in o and on >= n * 0.9 for o, on in terms)]


def contexts(sents: list[tuple[str, str]], needle: str, k: int = 1,
             aid: str | None = None) -> list[str]:
    """回到具体句子看上下文。`aid` 给了就只在那个素材里找——

    同一个错词在几个素材里各有一份时，不限定归属会把 A 片的上下文挂到 B 片的命中上，
    人照着错句子拍板比没有上下文更坑。
    """
    out = []
    for a_, text in sents:
        if aid and a_ != aid:
            continue
        i = text.find(needle)
        if i >= 0:
            a = max(0, i - 10)
            out.append(f"…{text[a:i + len(needle) + 12]}…（{a_.split('/')[-1]}）")
            if len(out) >= k:
                break
    return out


# ---------------------------------------------------------------- 语义常识扫描（T-017）
#
# 准绳 references/editorial-packaging-principles.md 原则 A5：ASR 只保证「听起来像」，
# 上屏前必须再跑一遍「人读得懂」的常识校对。三件事，判据全部数据化：
#
#   ① 反常物理量级 —— 数字 + 长度/重量单位，落在 data/abnormal-magnitudes.json 的量级之外；
#   ② 同音残词     —— 命中 data/suspect-words.txt（「效果好/生万/大咧」这一类）；
#   ③ 口吃重尾     —— 句尾叠字（「讲下下」「坐坐坐」），与 check_subs 共用 lexicon 那份判据。
#
# 为什么这三件事不能靠上牛段那套「语料对账」：语料里根本没有这句话。它们的共同点是**世界
# 知识**（楼有多高、鱼有多重、粤语怎么说），不是**用词频率**——频率法只会把它们当正常词。

DATA = Path(__file__).resolve().parent / "data"

# 中文数词表。自己写而不引库：pypinyin / jieba 都不把「十万」转成数值，
# 而这条判据的全部意义就是「拿数值跟现实量级比」。
_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
          "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL = {"十": 10, "百": 100, "仟": 1000, "千": 1000}
_MULT = {"万": 10000, "亿": 100000000, "百": 100, "千": 1000, "仟": 1000}
NUM_CORE = r"(?:\d+(?:\.\d+)?[万千百亿]?|[零〇一二两三四五六七八九十百千万亿廿卅]+)"
TIER_RANK = {"suspicious": 1, "absurd": 2}


def cn2num(s: str) -> float | None:
    """中文/阿拉伯数词 → 数值。「十万」100000、「廿五」25、「两百」200、「2.5万」25000。

    不认识就返回 None（宁可不判，不能把「几十」猜成 50 去判红）。
    """
    t = str(s or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    # 廿/卄（二十）、卅（三十）先展开成常规中文数词，后面的状态机就不用特例
    for a, b in (("廿", "二十"), ("卄", "二十"), ("卅", "三十")):
        t = t.replace(a, b)
    if not t:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([万仟百千亿]?)", t)
    if m:
        return float(m.group(1)) * _MULT.get(m.group(2), 1)
    total = section = num = 0.0
    seen = False
    for ch in t:
        if ch in _DIGIT:
            num = float(_DIGIT[ch])
            seen = True
        elif ch in _SMALL:
            section += (num or 1) * _SMALL[ch]
            num = 0.0
            seen = True
        elif ch == "万":
            section += num
            total += (section or 1) * 10000
            section = num = 0.0
            seen = True
        elif ch == "亿":
            section += num
            total = (total + section) * 100000000
            section = num = 0.0
            seen = True
        else:
            return None
    return total + section + num if seen else None


def load_magnitude_rules(path: Path | None = None) -> list[dict]:
    """读量级判据表。改判据的人改这张表，不该改逻辑（为什么：这是行业知识，不是代码）。"""
    p = Path(path) if path else DATA / "abnormal-magnitudes.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("rules") or []
    except json.JSONDecodeError:
        return []


def scan_magnitudes(texts: list[tuple[str, str]], rules: list[dict] | None = None) -> list[dict]:
    """数字+单位的量级跟现实对不对得上。

    返回每命中一条：`tier` 分两档（absurd 荒谬 / suspicious 可疑），而不是一刀判红——
    因为「三十米高的中庭」是真话，一口咬定就是听错，等于逼人改真台词。
    同一个数字被两条规则盖住时（「十万米」既属于长度·高也属于长度·距离）取重的那档。
    """
    rs = rules if rules is not None else load_magnitude_rules()
    pats: list[tuple[dict, re.Pattern]] = []
    for r in rs:
        units = sorted(r.get("units") or [], key=len, reverse=True)
        if not units or not r.get("suspicious"):
            continue
        suf = "|".join(map(re.escape, r.get("suffix") or []))
        pat = re.compile(r"(?P<num>" + NUM_CORE + r")\s*(?P<unit>"
                         + "|".join(map(re.escape, units)) + r")"
                         + (r"(?P<suf>" + suf + r")" if suf else r""))
        pats.append((r, pat))

    out: list[dict] = []
    for aid, text in texts:
        t = str(text or "")
        found: list[dict] = []
        for r, pat in pats:
            for m in pat.finditer(t):
                if r.get("suffix") and not m.groupdict().get("suf"):
                    continue                       # 规则要「米高」这种语境才成立
                v = cn2num(m.group("num"))
                if v is None:
                    continue
                tier = ("absurd" if v >= float(r.get("absurd") or 1e18)
                        else "suspicious" if v >= float(r.get("suspicious")) else None)
                if not tier:
                    continue
                found.append({"aid": aid, "text": t, "at": m.start(), "hit": m.group(0),
                              "value": v, "unit": m.group("unit"), "tier": tier,
                              "rule": r.get("id"), "kind": r.get("kind"), "why": r.get("why")})
        found.sort(key=lambda h: -TIER_RANK[h["tier"]])
        kept: list[dict] = []
        for h in found:
            b, e = h["at"], h["at"] + len(h["hit"])
            if any(b < k["at"] + len(k["hit"]) and k["at"] < e for k in kept):
                continue                           # 同一处数字只说一次
            kept.append(h)
        kept.sort(key=lambda h: h["at"])
        out.extend(kept)
    return out


def scan_suspects(texts: list[tuple[str, str]], suspects: list[dict] | None = None) -> list[dict]:
    """同音残词：命中嫌疑表即报。匹配细节（排除后接）在 `lexicon.suspect_hits` 里。"""
    table = suspects if suspects is not None else lexicon.load_suspects()
    out: list[dict] = []
    for aid, text in texts:
        for h in lexicon.suspect_hits(text, table):
            out.append({"aid": aid, "text": str(text), "at": h["at"], "hit": h["bad"],
                        "suggest": h["good"], "same_len": h["same_len"],
                        "kind": h["kind"], "needs_human": h["needs_human"], "note": h["note"]})
    return out


def scan_stutter(texts: list[tuple[str, str]]) -> list[dict]:
    """口吃重尾：句尾叠字。判据只有一份（`lexicon.stutter_tail`），check_subs 用同一个。"""
    out: list[dict] = []
    for aid, text in texts:
        tail = lexicon.stutter_tail(text)
        if tail:
            out.append({"aid": aid, "text": str(text), "tail": tail})
    return out


def semantic_scan(texts: list[tuple[str, str]], rules: list[dict] | None = None,
                  suspects: list[dict] | None = None) -> dict:
    """三张扫一遍，一次拿全。返回的四个表就是上屏前的待审工作单。"""
    sus = scan_suspects(texts, suspects)
    return {"suspects": sus,
            "magnitudes": scan_magnitudes(texts, rules),
            "stutters": scan_stutter(texts)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--suspects", default=None,
                    help="嫌疑词表（默认 scripts/data/suspect-words.txt）")
    ap.add_argument("--magnitudes", default=None,
                    help="量级判据表（默认 scripts/data/abnormal-magnitudes.json）")
    ap.add_argument("--worklist", default=None,
                    help="待审工单 JSON 输出路径（默认 <库>/_cutskills/semantic-worklist.json）")
    a = ap.parse_args()
    lib = Path(a.library).expanduser().resolve()
    sysdir = lib / "_cutskills"
    assets = load_index(lib)
    sents = all_text(assets)
    joined = "".join(t for _, t in sents)
    if not joined:
        print("索引里没有转写，先跑 --stage l1", file=sys.stderr)
        return 2

    lex_path = sysdir / "lexicon.json"
    lex = json.loads(lex_path.read_text()) if lex_path.exists() else {"terms": {}, "protect": []}
    terms = lex.get("terms") or {}
    protect = lex.get("protect") or []

    py = pinyin()
    ct = corpus_terms(lib)

    # 转写里的同音桶：拼音键 → {写法: 次数}
    buckets: dict[str, Counter] = {}
    gram_counts: Counter = Counter()
    for run in CJK.findall(joined):
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                gram_counts[g] += 1
                if py:
                    buckets.setdefault(py(g), Counter())[g] += 1

    L = [f"# 待纠错清单 · {lib.name}", "",
         "> 脚本只统计，不下结论。定论写进 `_cutskills/lexicon.json`，再跑 `--stage rephrase`。",
         f"> 生成于 {datetime.now():%Y-%m-%d %H:%M} · 覆盖 {len(assets)} 个素材 / {len(sents)} 条短语 · "
         f"同音检测 {'开' if py else '关（缺 pypinyin，降级）'}", ""]

    # ---- 0 语义常识扫描（T-017）
    # 这一段为什么排在最前面：前面那几节是「统计」，这一节是「上屏前一定要人看的东西」。
    # 一条片子里五处错字没改，其它统计再漂亮也没意义（准绳 A5：ASR 没有常识，审校是硬环节）。
    mag_rules = load_magnitude_rules(Path(a.magnitudes).expanduser() if a.magnitudes else None)
    suspects = lexicon.load_suspects(Path(a.suspects).expanduser() if a.suspects else None)
    sem = semantic_scan(sents, mag_rules, suspects)
    # 词表条目分两路：人写的（库词表）照旧应用，不等长只当 info 点名；机器建议不等长则拒绝。
    # 两种都要报出来：不报就等于静默失效（见 lexicon.plan）。
    term_notes = lexicon.term_entries(lex)[1]
    blocked_terms = [n for n in term_notes if n["level"] == "block"]
    info_terms = [n for n in term_notes if n["level"] == "info"]
    # 可直接落地的等长替换（工单主体）+ 必须人拍板的（待人工 / 不等长）
    proposed = {s["hit"]: s["suggest"] for s in sem["suspects"]
                if s["same_len"] and not s["needs_human"]}
    hold, seen_hold = [], set()
    for s in sem["suspects"]:
        if (s["needs_human"] or not s["same_len"]) and s["hit"] not in seen_hold:
            seen_hold.add(s["hit"])
            hold.append(s)

    L += ["## 一、语义常识扫描（上屏前必须先看）", "",
          "ASR 没有物理与行业常识：它按音节抄，把「十几米」抄成「十万米」也理直气壮。",
          "这一节不跟语料对账——语料里根本没有这句话。判据全在 `scripts/data/` 的两张表里，",
          "改判据改表，不改这里。", ""]

    L += ["### 1.1 反常物理量级", "",
          f"规则表 `data/abnormal-magnitudes.json`（{len(mag_rules)} 条）。",
          "「荒谬」= 量级差一万倍以上，几乎只可能是听错（`check_subs.py` 判 error）；",
          "「可疑」只进工单（真话也会落在这档，不能一刀判红）。", ""]
    if sem["magnitudes"]:
        L += ["| 素材 | 命中 | 数值 | 档位 | 上下文 |", "|---|---|---|---|---|"]
        for m in sem["magnitudes"][:a.top]:
            L.append(f"| {m['aid'].split('/')[-1]} | `{m['hit']}` | {m['value']:g}{m['unit']} | "
                     f"{m['tier']} | {' <br> '.join(contexts(sents, m['hit'], 1, m['aid']))} |")
    else:
        L.append("（无）")
    L.append("")

    L += ["### 1.2 同音残词", "",
          f"嫌疑表 `data/suspect-words.txt`（{len(suspects)} 条）。命中只算嫌疑，",
          "定论写进 `_cutskills/lexicon.json`——标「待人工」的必须有人读上下文（猜错比留着更糟）。", ""]
    if sem["suspects"]:
        L += ["| 素材 | 命中 | 建议 | 等长 | 待人工 | 备注 | 上下文 |", "|---|---|---|---|---|---|---|"]
        for s in sem["suspects"][:a.top]:
            L.append(f"| {s['aid'].split('/')[-1]} | `{s['hit']}` | `{s['suggest']}` | "
                     f"{'✓' if s['same_len'] else '✗'} | {'是' if s['needs_human'] else '—'} | "
                     f"{s['note']} | {' <br> '.join(contexts(sents, s['hit'], 1, s['aid']))} |")
    else:
        L.append("（无）")
    L.append("")

    L += ["### 1.3 口吃重尾", "",
          "句尾叠字（「讲下下」「坐坐坐」）：ASR 把拖音录成了同字重复。判据与 `check_subs.py` 共用",
          "`lexicon.stutter_tail()`；「慢慢」「坐坐」「睇睇」这类正常叠词不在里面。", ""]
    if sem["stutters"]:
        L += ["| 素材 | 句尾 | 整句 |", "|---|---|---|"]
        for s in sem["stutters"][:a.top]:
            L.append(f"| {s['aid'].split('/')[-1]} | `{s['tail']}` | {s['text'][:40]} |")
    else:
        L.append("（无）")
    L.append("")

    L += ["### 1.4 待审等长替换工单（**核对上下文再粘**）", "",
          "只收「等长 + 不撞否定字」的条目——这类能直接落地，字级时间戳不用动。", ""]
    if proposed:
        L += ["```json", json.dumps({"terms": proposed}, ensure_ascii=False, indent=1), "```", ""]
    else:
        L += ["（无）", ""]
    if hold:
        L += ["**必须人拍板的（不要整段粘）**：", ""]
        for s in hold[:a.top]:
            why = "待人工：可能不止一种改法" if s["needs_human"] else "不等长：会漂时间戳"
            L.append(f"- `{s['hit']}` → `{s['suggest']}`（{why}）{s['note']}")
        L.append("")
    if blocked_terms:
        L += ["### 1.5 词表里被拦下的条目（没落地）", "",
              "库词表是人写的，不等长照旧应用；**只有增删否定字**的条目会被拦下——",
              "它会把句意静默反转，必须人读上下文后显式 `--allow-negation-edit`：", ""]
        for b in blocked_terms:
            L.append(f"- `{b['from']}` → `{b['to']}`：{b['why']}")
        L.append("")
    if info_terms:
        L += ["### 1.6 词表已应用的「不等长」条目（只是让你看见）", "",
              "这些是库词表里人写定的替换，**照旧生效**；但字数变了，字宽会被均摊、",
              "时间戳按新字数重分布，所以在此点名：", ""]
        for b in info_terms:
            L.append(f"- `{b['from']}` → `{b['to']}`：{b['why']}")
        L.append("")

    # 工单落成 JSON：让人/下一道门禁读同一份，而不是两边各数一遍
    work = Path(a.worklist).expanduser() if a.worklist else sysdir / "semantic-worklist.json"
    work.write_text(json.dumps({
        "generated": f"{datetime.now():%Y-%m-%d %H:%M}",
        "library": str(lib),
        "magnitudes": sem["magnitudes"], "suspects": sem["suspects"],
        "stutters": sem["stutters"], "proposed_terms": proposed,
        "needs_human": hold, "blocked_terms": blocked_terms,
        "info_terms": info_terms,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 1 关注词表（lexicon.protect）的同音竞争
    # 为什么从词表出发、不从语料高频片段出发：语料里任意二字片段都不是术语，
    # 拿它去对转写会满屏「个事/始就/坐着」这类假信号。术语是人先认下来的，
    # 脚本只负责看它在转写里到底被写成了几个同音版本。
    watch = list(dict.fromkeys(protect + list(terms.values())))
    rows_watch = []
    clean = 0
    for w in watch:
        if not w:
            continue
        cands = buckets.get(py(w)) if py else None
        variants = [(g, c) for g, c in (cands or {}).items()
                    if g != w and ct.get(g, 0) <= 1 and c >= 2]
        variants.sort(key=lambda x: -x[1])
        if variants:
            rows_watch.append((w, joined.count(w), variants[:4]))
        elif joined.count(w):
            clean += 1
    rows_watch.sort(key=lambda r: -sum(c for _, c in r[2]))

    L += ["## 二、关注词表在转写里的同音竞争", "",
          "词表（`lexicon.json` 的 `protect` 与已修正项）是「必须写对」的名单：品牌名、术语、人名。",
          "「转写正确」不为零不代表没问题——错写占优时照样得纠。", ""]
    if rows_watch:
        L += ["| 标准写法 | 转写正确 | 同音错写（次数） | 上下文 |", "|---|---|---|---|"]
        for w, ok, variants in rows_watch[:a.top]:
            ctx = " <br> ".join(contexts(sents, variants[0][0], 1))
            L.append(f"| {w} | {ok} | " +
                     "、".join(f"`{g}`({c})" for g, c in variants) + f" | {ctx} |")
    else:
        L.append("（无）")
    L.append("")
    if clean:
        L += [f"另有 {clean} 个词表词在转写里写法正常、没有同音干扰。", ""]

    # ---- 2 语料里的多字术语，转写里没有
    missing = [(w, n) for w, n in ct.items()
               if n >= 3 and len(w) >= 3 and w not in STOP and joined.count(w) == 0
               and not any(w in v for v in terms.values())]
    missing.sort(key=lambda x: -x[1])
    rest = maximal(missing)[:a.top]
    L += ["## 三、语料里的多字术语，转写里一次都没有", "",
          "只取三字以上（两字片段大半是普通搭配）。这类可能是没被念到，也可能被听成了别的音。", ""]
    if rest:
        L += ["| 标准写法 | 语料出现 |", "|---|---|"]
        for w, n in rest:
            L.append(f"| {w} | {n} |")
    else:
        L.append("（无）")
    L.append("")

    # ---- 3 高频片段
    rows = [(g, c) for g, c in gram_counts.most_common(a.top * 5)
            if c >= 3 and g not in STOP
            and not any(g in bad or g in good for bad, good in terms.items())
            and not any(g in p for p in protect)][:a.top]
    L += ["## 四、高频片段（按出现次数）", "",
          "同一个片段反复出现、读起来又别扭的，通常是模型的一贯性错误——纠一次，全库生效。", "",
          "| 片段 | 次数 | 上下文 |", "|---|---|---|"]
    for g, c in rows:
        L.append(f"| {g} | {c} | {' <br> '.join(contexts(sents, g, 2))} |")
    L.append("")

    # ---- 4 词表现状
    L += ["## 五、当前词表在做什么", ""]
    if terms:
        L += ["| 错写 | 改成 | 仍命中 |", "|---|---|---|"]
        for bad, good in sorted(terms.items(), key=lambda kv: -joined.count(kv[0])):
            L.append(f"| {bad} | {good} | {joined.count(bad)} |")
        L += ["", f"受保护专名（不参与替换）：{'、'.join(protect) or '—'}"]
    else:
        L.append("词表还是空的。先看上面两张表和语料手册，把决定写进 `_cutskills/lexicon.json`。")
    L.append("")

    # ---- 5 异常短语
    weird = []
    for aid, t in sents:
        if len(t) > 45 and "，" not in t and "。" not in t:
            weird.append((aid, t, "超长无标点"))
        elif re.search(r"(.)\1\1", t):
            weird.append((aid, t, "单字反复"))
    L += ["## 六、异常短语（模型在硬撑）", ""]
    if weird:
        L += ["| 素材 | 短语 | 症状 |", "|---|---|---|"]
        for aid, t, why in weird[:a.top]:
            L.append(f"| {aid.split('/')[-1]} | {t[:60]} | {why} |")
    else:
        L.append("（无）")
    L.append("")

    # ---- 6 机器猜的候选（必须核对）
    top = [(w, ok, v) for w, ok, v in rows_watch
           if ct.get(v[0][0], 0) == 0 and v[0][1] >= 5 and v[0][1] >= 2 * max(ok, 1)]
    if top:
        L += ["## 七、机器猜的候选（**核对过上下文再粘，别整段贴**）", "",
              "只保留「错写在语料里 0 次、次数远超正确写法」的，误报率低但仍有——", "",
              "```json",
              json.dumps({"terms": {v[0][0]: w for w, _, v in top[:15]}},
                         ensure_ascii=False, indent=1), "```", ""]

    out = sysdir / "transcript-fix.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"已生成 {out}")
    print(f"  语义常识：量级 {len(sem['magnitudes'])} · 残词 {len(sem['suspects'])} · "
          f"口吃 {len(sem['stutters'])} · 待审等长 {len(proposed)} · "
          f"待人工/不等长 {len(hold)} · 词表被拦 {len(blocked_terms)}")
    print(f"  同音竞争 {len(rows_watch)} 个词 · 语料有转写无 {len(rest)} 条 · "
          f"高频片段 {len(rows)} 条 · 异常短语 {len(weird)} 条")
    print(f"  工单：{work}")
    return 0


def catalog_ok(g: str) -> bool:
    """错写候选得像个词：允许单字例外，但整串不能只由虚词构成（这里只挡掉纯数字/字母）。"""
    return bool(CJK.search(g))


if __name__ == "__main__":
    raise SystemExit(main())
