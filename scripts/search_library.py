#!/usr/bin/env python3
"""CutSkills · 在素材库里搜「哪段能当我这一段画面」。

这是 B 段「先有稿子、再找画面」那一步的基础设施。没有它，找画面只能靠翻
`inventory.md` 一页页看——上一次把「材料搅拌」和「地面涂刷」搞反，就是这么发生的。

命中三个地方，按可信度排序：

  1. L2 的 `tag`（画面名词短语，人看过画面才写得出来）
  2. L2 的 `summary` / `action` / `people`
  3. 转写的短语原文（带素材内时间码，可以直接当切点候选）

文件名也算，但排在最后——名字可能错（柒造真出过），只能当线索。

用法：
  search_library.py --library "<库>" --query 打磨
  search_library.py --library "<库>" --query 打磨 --query 涂刷 --grade must-use
  search_library.py --library "<库>" --query 展厅 --kind photo --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

GRADE_RANK = {"must-use": 0, "backup": 1, "do-not-use": 2}
GRADE_CN = {"must-use": "① 必用", "backup": "② 备用", "do-not-use": "③ 不用"}
# 命中位置的可信度：看过画面写下来的 > 自己猜的 > 文件名
WHERE_SCORE = {"tag": 3.0, "summary": 2.4, "action": 2.2, "people": 1.6, "transcript": 1.8, "name": 0.6}


def load_index(lib: Path) -> list[dict]:
    p = lib / "_cutskills" / "index.json"
    if not p.exists():
        raise SystemExit(f"索引不存在，先跑 index_assets.py：{p}")
    return json.loads(p.read_text())["assets"]


def hits_in_asset(rec: dict, terms: list[str]) -> list[dict]:
    """一支素材里所有命中的位置。"""
    out: list[dict] = []
    for s in rec.get("segments") or []:
        for field in ("tag", "summary", "action"):
            val = str(s.get(field) or "")
            if any(t in val for t in terms):
                out.append({"where": field, "in": s.get("in"), "out": s.get("out"),
                            "grade": s.get("grade"), "confidence": s.get("confidence"),
                            "shot": s.get("shot"), "tag": s.get("tag"),
                            "summary": s.get("summary"), "text": val})
                break                                  # 一段只报一次，按最可信的那格算
        people = " ".join(s.get("people") or [])
        if any(t in people for t in terms):
            out.append({"where": "people", "in": s.get("in"), "out": s.get("out"),
                        "grade": s.get("grade"), "confidence": s.get("confidence"),
                        "shot": s.get("shot"), "tag": s.get("tag"),
                        "summary": s.get("summary"), "text": people})
    phrases = (rec.get("transcript") or {}).get("phrases") or []
    for i, p in enumerate(phrases):
        if any(t in p["text"] for t in terms):
            ctx = "".join(x["text"] for x in phrases[max(0, i - 1):i + 2])
            out.append({"where": "transcript", "in": p["start"], "out": p["end"],
                        "grade": None, "confidence": None, "shot": None,
                        "tag": None, "summary": None, "text": ctx})
    if any(t in rec["id"] for t in terms) and not out:
        # 只在没有 L2/转写命中时才把文件名当线索：名字可能是错的
        out.append({"where": "name", "in": 0.0, "out": rec.get("duration") or 0,
                    "grade": None, "confidence": None, "shot": None,
                    "tag": None, "summary": None, "text": rec["id"].split("/")[-1]})
    return out


def _bg(text: str) -> set[str]:
    t = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fa5")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def search_by_text(lib: Path, text: str, *, limit: int = 15,
                   max_dur: float | None = None) -> list[dict]:
    """给一段话，找画面配得上它的段落（按共同二字组合算，不做分词）。"""
    index = load_index(lib)
    T = _bg(text)
    rows = []
    for r in index:
        for s in r.get("segments") or []:
            desc = " ".join(str(s.get(k) or "") for k in ("tag", "summary", "action"))
            if len(desc) < 4:
                continue
            span = (s.get("out") or 0) - (s.get("in") or 0)
            if span < 1.5 or span > 60:
                continue
            shared = T & _bg(desc)
            if len(shared) < 2:
                continue
            out_ = s.get("out") or 0
            if max_dur and span > max_dur:
                out_ = round((s.get("in") or 0) + max_dur, 3)
            rows.append({"asset": r["id"], "kind": r["kind"], "where": "L2",
                         "in": s.get("in"), "out": out_, "grade": s.get("grade"),
                         "confidence": s.get("confidence"), "tag": s.get("tag"),
                         "summary": s.get("summary"), "score": len(shared),
                         "shared": "".join(sorted(shared))[:24],
                         "text": s.get("summary") or s.get("action") or "",
                         "clipped": bool(max_dur and span > max_dur)})
    rows.sort(key=lambda x: (GRADE_RANK.get(x["grade"], 9), -x["score"]))
    return rows[:limit]


def for_cut(lib: Path, asset: str, tin: float, tout: float, *,
            limit: int = 15, max_dur: float | None = None) -> list[dict]:
    """「这段口播该配什么画面」——用它的原话去和每个 L2 段的画面描述算重合。

    不做分词、不猜关键词：这段的口播原文和「这段画面拍的是什么」之间共同的二字组合越多，
    越可能配得上。命不中时人换说法再试，不要相信一个假的关键词。

    返回每行带 `shared`（共同出现的词），让人一眼看出「为什么它觉得配得上」。
    """
    index = load_index(lib)
    idx = {r["id"]: r for r in index}
    rec = idx.get(asset)
    if not rec:
        raise SystemExit(f"索引里没有 {asset}")
    phrases = (rec.get("transcript") or {}).get("phrases") or []
    text = "".join(x["text"] for x in phrases if x["end"] > tin and x["start"] < tout)
    if len(text) < 4:
        raise SystemExit("这段没有转写，--for-cut 用不了，请直接给 --query")
    T = _bg(text)

    rows = []
    for r in index:
        for s in r.get("segments") or []:
            desc = " ".join(str(s.get(k) or "") for k in ("tag", "summary", "action"))
            if len(desc) < 4:
                continue
            span = (s.get("out") or 0) - (s.get("in") or 0)
            if span < 1.5 or span > 60:
                continue
            shared = T & _bg(desc)
            if len(shared) < 2:
                continue
            hit_in_span = _bg(text) and any(w in text for w in [s.get("tag") or ""]) \
                if s.get("tag") else False
            score = len(shared) + (3 if hit_in_span else 0)
            out_ = s.get("out") or 0
            if max_dur and span > max_dur:
                out_ = round((s.get("in") or 0) + max_dur, 3)
            rows.append({"asset": r["id"], "kind": r["kind"], "where": "L2",
                         "in": s.get("in"), "out": out_, "grade": s.get("grade"),
                         "confidence": s.get("confidence"), "tag": s.get("tag"),
                         "summary": s.get("summary"), "score": score,
                         "shared": "".join(sorted(shared))[:24],
                         "text": s.get("summary") or s.get("action") or "",
                         "clipped": bool(max_dur and span > max_dur)})
    rows.sort(key=lambda x: (GRADE_RANK.get(x["grade"], 9), -x["score"]))
    return rows[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--query", action="append", default=[], help="关键词，可多次；任一命中就算")
    ap.add_argument("--for-cut", nargs=3, metavar=("ASSET", "IN", "OUT"),
                    help="反查：这段口播（asset in out）该配什么画面——自动从它的转写里挑关键词")
    ap.add_argument("--grade", default="backup",
                    choices=["must-use", "backup", "do-not-use"], help="最低分级（默认 backup）")
    ap.add_argument("--kind", default="any", choices=["any", "video", "photo"])
    ap.add_argument("--min-dur", type=float, default=None, help="这段至少要多长（秒）")
    ap.add_argument("--max-dur", type=float, default=None, help="这段最多多长（秒），超了自动裁到这么长")
    ap.add_argument("--shot", default=None, help="只看某个景别：wide/medium/close/detail/pov/screen")
    ap.add_argument("--people", default=None, help="只看画面里有谁（speaker/host/worker/customer/none）")
    ap.add_argument("--plan", action="store_true", help="直接输出能粘进 plan.json 的片段（JSON）")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    terms = [t.strip() for t in a.query if t.strip()]
    if a.for_cut:
        aid = a.for_cut[0]
        rows = for_cut(lib, aid, float(a.for_cut[1]), float(a.for_cut[2]),
                       limit=a.limit, max_dur=a.max_dur)
        if a.plan:
            print(json.dumps([{"asset": r["asset"], "in": round(r["in"], 2),
                               "out": round(r["out"], 2), "medium": "broll",
                               "beat": 1, "role": "evidence", "why": "",
                               "_hit": (r["tag"] or r["text"] or "")[:20]} for r in rows],
                             ensure_ascii=False, indent=1))
            return 0
        print(f"# 为这段口播找画面：{aid} {float(a.for_cut[1]):.2f}–{float(a.for_cut[2]):.2f}")
        print("# 判据是「这段的原话」与「这段画面拍的是什么」共同出现的词，不是分词猜的关键词。")
        print()
        if not rows:
            print("没找到配上得上的画面 —— 这段可能只有抽象判断，没有对应实物。"
                  "这正是 `gap.md` 会记的缺口，不要硬配。")
            return 0
        print("| 分级 | 素材 | 素材内 | 景别 | 画面 | 共同词 |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            tc = f"{r['in']:.2f}–{r['out']:.2f}s" + (" ✂" if r["clipped"] else "")
            print(f"| {GRADE_CN.get(r['grade'], r['grade'] or '—')} | {r['asset']} | {tc} | "
                  f"{r.get('tag') or '—'} | {(r['text'] or '').replace('|', '/')[:46]} | {r['shared']} |")
        return 0

    if not terms:
        print("--query 或 --for-cut 至少要给一个", file=sys.stderr)
        return 2
    assets = load_index(lib)

    rows = []
    for rec in assets:
        if a.kind != "any" and rec.get("kind") != a.kind:
            continue
        hits = hits_in_asset(rec, terms)
        if not hits:
            continue
        # 分级过滤：用这支素材「最好的一段」，不是第一段
        gs = [s.get("grade") for s in rec.get("segments") or [] if s.get("grade")]
        best = min(gs, key=lambda g: GRADE_RANK.get(g, 9)) if gs else "backup"
        if GRADE_RANK.get(best, 9) > GRADE_RANK[a.grade]:
            continue
        for h in hits:
            if a.shot and h.get("shot") != a.shot:
                continue
            if a.people and a.people not in " ".join(str(x) for x in (h.get("text") or "").split()):
                if a.people not in str(h.get("text") or ""):
                    continue
            h_in, h_out = h.get("in") or 0.0, h.get("out") or 0.0
            span = h_out - h_in
            if a.min_dur and span < a.min_dur:
                continue
            if a.max_dur and span > a.max_dur:
                h_out = round(h_in + a.max_dur, 3)      # 裁到够用为止，别让用户自己算
                h["clipped"] = True
            score = WHERE_SCORE.get(h["where"], 1.0)
            rows.append({"asset": rec["id"], "kind": rec["kind"], "best_grade": best,
                         "where": h["where"], "in": h["in"], "out": h["out"],
                         "grade": h["grade"] or best, "confidence": h["confidence"],
                         "in": h_in, "out": h_out, "shot": h.get("shot"),
                         "tag": h.get("tag"), "summary": h.get("summary"),
                         "text": h["text"], "score": score,
                         "clipped": bool(h.get("clipped"))})
    rows.sort(key=lambda r: (GRADE_RANK.get(r["grade"], 9), -r["score"], r["asset"], r["in"] or 0))
    rows = rows[:a.limit]

    if a.plan:
        # 直接给能粘进 plan.json 的片段：why 留空，因为理由是人给的判断，不该由检索脚本代写
        snippets = [{"asset": r["asset"], "in": round(r["in"], 2), "out": round(r["out"], 2),
                     "medium": "broll", "beat": 1, "role": "evidence",
                     "why": "", "_hit": r["where"] + ":" + (r["tag"] or r["text"])[:20]}
                    for r in rows]
        print(json.dumps(snippets, ensure_ascii=False, indent=1))
        print("// why 是空的：理由要你填（check_timeline 会拦套话）。beat 先写 1，归到哪个论点你定。",
              file=sys.stderr)
        return 0

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    qs = " / ".join(terms)
    l2n = sum(1 for r in rows if r["where"] != "transcript")
    print(f"# 检索「{qs}」· 命中 {len(rows)} 处（L2 描述 {l2n} / 转写 {len(rows) - l2n}）")
    print(f"> 库：{lib.name} · 最低分级 {GRADE_CN.get(a.grade, a.grade)}")
    print()
    if not rows:
        print("没命中。要么这个说法库里没有，要么 L2 还没覆盖到这一段——"
              "先看 `pickup.md` 里分级为空的部分，或换同义词再试一次。")
        return 0
    print("| 分级 | 素材 | 素材内时间码 | 命中 | 内容 | 置信 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        tc = f"{r['in']:.2f}–{r['out']:.2f}s" if r["kind"] == "video" else "—"
        if r.get("clipped"):
            tc += " ✂"
        conf = f"{r['confidence']:.2f}" if isinstance(r["confidence"], (int, float)) else "—"
        text = r["text"].replace("|", "/")[:60]
        print(f"| {GRADE_CN.get(r['grade'], r['grade'])} | {r['asset']} | {tc} | "
              f"{r['where']} | {text} | {conf} |")
    print()
    print("时间码是**素材内时间**，不是成片时间。要用就写进 plan.json，padding 交给 timeline.py。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
