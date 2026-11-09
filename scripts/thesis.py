#!/usr/bin/env python3
"""CutSkills · 观点先行：先定「一句话讲完什么」，再回头挑素材。

为什么要有这一层：以前是「从素材里挑好句子拼成片子」，句子都对，合起来没有观点。
倒过来才对——先写死主张，再问「这句话要用哪几句真话撑住」，撑不住的才是缺口。

这一步**不替你写观点**。观点只能人定（写在这张表里），脚本干三件能干的事：

  ① 匹配：把每条主张的词扔进整个素材库的字幕里，找出能当证据的原话（带 in/out）
  ② 排序：证据在原素材里的先后 vs 主张的先后——不一致的地方就是「要倒序」的位置
  ③ 缺口：谁都匹配不上的主张，直接给出该用哪个生成模板补（flow / layers / compare / number…）

`--init` 写一张空表（把 brief 里已经定下的东西填进去），`--match` 填证据，`--show` 看表。

用法：
  thesis.py --project <项目> --init
  thesis.py --project <项目> --match
  thesis.py --project <项目> --show
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAD_IN, PAD_OUT = 0.03, 0.05
MAX_EVID_S = 9.0          # 一条证据最长几秒（再长就听不出在说什么了）
MIN_HIT = 2               # 关键词命中几个才算候补

# 缺口 → 该用哪个生成模板。模板名必须和 timeline.GEN_TEMPLATES 对齐。
GAP_TEMPLATE = [
    (("流程", "工序", "步骤", "顺序", "先", "然后", "最后"), "flow", "库里没有工序画面时，用节点逐个亮起代替"),
    (("价格", "成本", "倍", "差价", "报价"), "compare", "两个口径并排，比一句话更清楚"),
    (("数量", "个数", "多少", "几次", "几倍", "百分比"), "number", "一个数字占一屏，别用嘴说"),
    (("对比", "区别", "不同"), "compare", "左右两栏，观众自己看"),
    (("结构", "分层", "基层", "打底", "从下"), "layers", "剖面从下往上叠，讲结构最省话"),
    (("时间", "多久", "几年", "个月"), "chart", "有数的东西画出来"),
]


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def dump(p: Path, d) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def keywords(text: str) -> list[str]:
    """主张里的实词。中文没有空格，按 2 字滑窗取，去掉虚词组合。"""
    stop = set("的了是在和就都很也不没有一个这个那个我们你们他们因为所以但是可以"
               "什么怎么这样那样其实如果就是还是而且以及已经还有两种三个")
    t = "".join(ch for ch in text if ch.strip() and ch not in "，。！？、；：…—·「」“”")
    out = []
    for i in range(len(t) - 1):
        w = t[i:i + 2]
        if w in stop:
            continue
        out.append(w)
    return list(dict.fromkeys(out))


def init(proj: Path) -> dict:
    brief = load(proj / "brief.json") or {}
    th = {
        "version": 1,
        "claim": "",                      # 一句话讲完：观众看完该记住哪一句
        "why_now": "",                    # 为什么现在说这句（谁在什么处境里）
        "claims": [                       # 支撑这条主张的论点，按你希望的出场顺序写
            {"id": "c1", "text": "", "role": "claim",
             "note": "用一句你会在饭桌上说的话写，不要写标题"},
        ],
        "sources": {"audience": brief.get("audience", ""),
                    "present_state": brief.get("present_state", ""),
                    "change": brief.get("change", ""),
                    "length_s": brief.get("length_s")},
        "filled": False,
    }
    dump(proj / "thesis.json", th)
    return th


def index_phrases(lib: Path) -> list[dict]:
    idx = load(lib / "_cutskills/index.json") or {}
    out = []
    for a in idx.get("assets") or []:
        for ph in ((a.get("transcript") or {}).get("phrases") or []):
            txt = str(ph.get("text") or "")
            if len(txt) < 4:
                continue
            out.append({"asset": a["id"], "in": round(float(ph["start"]), 3),
                        "out": round(float(ph["end"]), 3), "text": txt,
                        "dur": round(float(ph["end"]) - float(ph["start"]), 3)})
    return out


def match(proj: Path, lib: Path, th: dict) -> dict:
    phrases = index_phrases(lib)
    for c in th["claims"]:
        kw = keywords(str(c.get("text") or ""))
        c["keywords"] = kw
        cands = []
        for ph in phrases:
            if ph["dur"] > MAX_EVID_S * 1.6:
                continue
            hits = [w for w in kw if w in ph["text"]]
            if len(hits) < min(MIN_HIT, max(1, len(kw))):
                continue
            cands.append({**ph, "hits": hits,
                          "score": round(len(hits) * 2 + max(0, 3 - abs(ph["dur"] - 4) / 2), 2)})
        cands.sort(key=lambda x: -x["score"])
        c["evidence"] = cands[:5]
        c["reversed_in_source"] = None
        c["gap"] = None
        if not cands:
            tpl = next((t for words, t, _ in GAP_TEMPLATE
                        if any(w in str(c.get("text") or "") for w in words)), "keyword")
            why = next((w for words, t, w in GAP_TEMPLATE
                        if any(x in str(c.get("text") or "") for x in words)),
                       "库里没有这句话的画面，用一屏字卡把它钉住")
            c["gap"] = {"template": tpl, "why": why}

    # 排序可行性：把每条主张挑中的第一顺位证据，按它在原素材里出现的顺序排一遍，
    # 跟主张的先后比。不一致就是「必须倒序」——这件事要人点头，脚本只负责指出。
    picks = []
    for i, c in enumerate(th["claims"]):
        ev = (c.get("evidence") or [None])[0]
        picks.append((i, c["id"], ev["asset"] if ev else None, ev["in"] if ev else None))
    seq = [p for p in picks if p[2]]
    order_ok = all(seq[i][3] <= seq[i + 1][3] for i in range(len(seq) - 1)) if len(seq) > 1 else True
    th["order"] = {
        "as_written": [p[1] for p in picks],
        "source_order_matches": order_ok,
        "note": ("证据在原素材里的先后和主张先后一致，按顺序剪就行"
                 if order_ok else
                 "有几条证据在原素材里是倒着的——要么接受倒序（先给结论再给原因），"
                 "要么换证据。倒序只允许在块级做，别把一句话拆开"),
    }
    th["gap_count"] = sum(1 for c in th["claims"] if c.get("gap"))
    th["filled"] = bool(th["claims"]) and all(str(c.get("text") or "").strip() for c in th["claims"])
    dump(proj / "thesis.json", th)
    return th


def show(th: dict) -> None:
    print(f"主张：{th.get('claim') or '（还没写）'}")
    if th.get("why_now"):
        print(f"为什么现在说：{th['why_now']}")
    for c in th.get("claims") or []:
        print(f"\n[{c['id']}] {c.get('role')} · {c.get('text') or '（空）'}")
        if c.get("gap"):
            print(f"  ⚠ 素材缺口 → 用生成模板「{c['gap']['template']}」：{c['gap']['why']}")
        for e in (c.get("evidence") or [])[:3]:
            print(f"  · {e['score']:>4} {e['asset'][:26]} {e['in']:.2f}–{e['out']:.2f}"
                  f"「{e['text'][:22]}」命中 {'/'.join(e['hits'][:4])}")
    o = th.get("order") or {}
    if o:
        print(f"\n顺序：{'一致，可顺剪' if o.get('source_order_matches') else '需倒序'}")
        print(f"  {o.get('note')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--lib", default=None, help="素材库根目录；默认从项目路径往上找 _cutskills")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--match", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.lib).expanduser().resolve() if a.lib else None
    if lib is None:
        for p in [proj, *proj.parents]:
            if (p / "_cutskills" / "index.json").exists():
                lib = p
                break
    if lib is None:
        raise SystemExit("找不到素材库（往上找不到 _cutskills/index.json），用 --lib 指定")

    if a.init:
        init(proj)
        print(f"→ {proj / 'thesis.json'}（把 claim 和 claims 写完，再跑 --match）")
        return 0

    th = load(proj / "thesis.json")
    if not th:
        raise SystemExit("先 --init")
    if a.match:
        th = match(proj, lib, th)
        print(f"→ {proj / 'thesis.json'}")
    if a.show or not a.match:
        show(th)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
