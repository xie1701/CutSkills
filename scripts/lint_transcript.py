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

用法：
  lint_transcript.py --library "<素材库>" [--top 40]
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


def contexts(sents: list[tuple[str, str]], needle: str, k: int = 1) -> list[str]:
    out = []
    for aid, text in sents:
        i = text.find(needle)
        if i >= 0:
            a = max(0, i - 10)
            out.append(f"…{text[a:i + len(needle) + 12]}…（{aid.split('/')[-1]}）")
            if len(out) >= k:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--top", type=int, default=40)
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

    L += ["## 一、关注词表在转写里的同音竞争", "",
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
    L += ["## 二、语料里的多字术语，转写里一次都没有", "",
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
    L += ["## 三、高频片段（按出现次数）", "",
          "同一个片段反复出现、读起来又别扭的，通常是模型的一贯性错误——纠一次，全库生效。", "",
          "| 片段 | 次数 | 上下文 |", "|---|---|---|"]
    for g, c in rows:
        L.append(f"| {g} | {c} | {' <br> '.join(contexts(sents, g, 2))} |")
    L.append("")

    # ---- 4 词表现状
    L += ["## 四、当前词表在做什么", ""]
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
    L += ["## 五、异常短语（模型在硬撑）", ""]
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
        L += ["## 六、机器猜的候选（**核对过上下文再粘，别整段贴**）", "",
              "只保留「错写在语料里 0 次、次数远超正确写法」的，误报率低但仍有——", "",
              "```json",
              json.dumps({"terms": {v[0][0]: w for w, _, v in top[:15]}},
                         ensure_ascii=False, indent=1), "```", ""]

    out = sysdir / "transcript-fix.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"已生成 {out}")
    print(f"  同音竞争 {len(rows_watch)} 个词 · 语料有转写无 {len(rest)} 条 · "
          f"高频片段 {len(rows)} 条 · 异常短语 {len(weird)} 条")
    return 0


def catalog_ok(g: str) -> bool:
    """错写候选得像个词：允许单字例外，但整串不能只由虚词构成（这里只挡掉纯数字/字母）。"""
    return bool(CJK.search(g))


if __name__ == "__main__":
    raise SystemExit(main())
