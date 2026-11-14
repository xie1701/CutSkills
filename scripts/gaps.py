#!/usr/bin/env python3
"""CutSkills · 缺口清单：把时间轴里缺证据、用代码补画、或台词点图缺画面的地方汇总成 gap.md。

用法：
  gaps.py --project <项目目录> [--library <素材库>]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libs import resolve_lib  # noqa: E402
from search_library import search_by_text  # noqa: E402

ROLE_CN = {"setup": "交代", "claim": "主张", "evidence": "举证", "warrant": "说理",
           "turn": "转折", "payoff": "收束"}

VISUAL_GAP_BEGIN = "<!-- content_guard:visual_gap:begin -->"
VISUAL_GAP_END = "<!-- content_guard:visual_gap:end -->"


def _bg(t: str) -> set[str]:
    t = "".join(ch for ch in t if "\u4e00" <= ch <= "\u9fa5")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _el_dur(el: dict) -> float:
    if el.get("kind") == "clip":
        return max(0.0, float(el.get("out") or 0) - float(el.get("in") or 0))
    return max(0.0, float(el.get("dur") or 0))


def transcript_hits(lib: Path, text: str, limit: int = 4,
                    used: list[tuple[str, float, float]] | None = None) -> list[dict]:
    idx_f = lib / "_cutskills" / "index.json"
    if not idx_f.exists():
        return []
    idx = json.loads(idx_f.read_text())["assets"]
    T = _bg(text)
    out = []
    for a in idx:
        for ph in (a.get("transcript") or {}).get("phrases") or []:
            if used and any(u[0] == a["id"] and u[1] - 0.5 <= ph["start"] <= u[2] + 0.5
                            for u in used):
                continue
            sh = T & _bg(ph["text"])
            if len(sh) >= 3:
                out.append({"asset": a["id"], "in": ph["start"], "text": ph["text"],
                            "score": len(sh)})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def append_visual_gap(proj: Path, rows: list[dict]) -> Path | None:
    """把「台词点了图、画面没给」的缺口写进 `gap.md`（带时间戳，整节替换；清零时自动剥掉旧节）。"""
    p = proj / "gap.md"
    if not rows:
        if p.exists():
            old = p.read_text(encoding="utf-8")
            if VISUAL_GAP_BEGIN in old and VISUAL_GAP_END in old:
                head, _, rest = old.partition(VISUAL_GAP_BEGIN)
                _, _, tail = rest.partition(VISUAL_GAP_END)
                cleaned = (head.rstrip() + "\n\n" + tail.lstrip()).strip()
                if cleaned:
                    p.write_text(cleaned + "\n", encoding="utf-8")
                else:
                    p.unlink()
        return None
    lines = [VISUAL_GAP_BEGIN,
             "## 四、指代词缺画面（说了「呢张图 / 四张图 / 效果图」，画面没给）", "",
             f"最近检查：{datetime.now():%Y-%m-%d %H:%M}（`check_timeline.py` 的视觉指代词守卫）", "",
             "口播说了靠画面才能成立的词，画面却是别的镜头——观众会立刻看出是拿废片硬凑。"
             "二选一：把对应的图/图纸/效果图入库并写 L2（标签要写「图纸」「效果图」这类画面词），"
             "或改选不带指代词的 take，或用代码生成对应示意图。", "",
             "| 镜号 | 台词 | 指代词 | 画面 | 为什么不成立 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| #{r.get('n')} | {str(r.get('text') or '')[:40]} | {r.get('word') or ''} | "
                     f"{r.get('asset') or '—'} | {r.get('why') or ''} |")
    lines += ["", VISUAL_GAP_END]
    block = "\n".join(lines)

    old = p.read_text(encoding="utf-8") if p.exists() else ""
    if VISUAL_GAP_BEGIN in old and VISUAL_GAP_END in old:
        head, _, rest = old.partition(VISUAL_GAP_BEGIN)
        _, _, tail = rest.partition(VISUAL_GAP_END)
        new = head + block + tail
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + block + "\n"
    p.write_text(new, encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="生成项目素材与论证缺口清单 gap.md")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None, help="给了就在库里反查：这个缺口是不是真没有画面")
    ap.add_argument("--no-search", action="store_true", help="不查库（快）")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = resolve_lib(proj, a.library)
    tl_f = proj / "timeline.json"
    if not tl_f.exists():
        raise SystemExit(f"没有 timeline.json：{tl_f}")
    tl = json.loads(tl_f.read_text())
    els = [e for e in (tl.get("elements") or []) if (e.get("track") or "video") != "overlay"]
    total = float(tl.get("duration_s") or sum(_el_dur(e) for e in els) or 1.0)

    arg_gaps = []
    for b in tl.get("beats") or []:
        own = [s for s in els if s.get("beat") == b.get("n")]
        roles = {s.get("role") for s in own}
        if "claim" in roles and "evidence" not in roles:
            arg_gaps.append((b, own))
        elif "claim" in roles and "evidence" in roles and "warrant" not in roles:
            arg_gaps.append((b, own))

    outside = [s for s in els if (s.get("source") and s.get("source") != "library") or s.get("kind") == "gen"]
    gen_dur = sum(_el_dur(s) for s in outside)

    lines = [f"# 缺口清单 · {tl.get('slug') or proj.name}", "",
             f"成片 {total:.1f}s / {len(els)} 格。这份清单只列**真的缺**：论证缺证据、代码代画段、"
             "以及值得补拍的画面。", ""]

    lines += ["## 一、论证缺口（论点有主张、缺证据或说理）", ""]
    if arg_gaps:
        lines += ["| 论点 | 格 | 已有角色 | 缺什么 | 不加会怎样 |", "|---|---|---|---|---|"]
        for b, own in arg_gaps:
            rs = {s.get("role") for s in own if s.get("role")}
            shown = "、".join(ROLE_CN.get(r, r) for r in
                             sorted(rs, key=lambda r: list(ROLE_CN).index(r) if r in ROLE_CN else 9))
            need = "证据画面（实拍或代码示意图）" if "evidence" not in rs else "说理（为什么这条证据成立）"
            cost = "观众会怀疑这一句结论" if "evidence" not in rs else "画面摆在那儿，观众看不懂门道"
            lines.append(f"| {b.get('n')}. {b.get('title') or '—'} | "
                         f"{'、'.join('#' + str(s['n']) for s in own)} | {shown or '—'} | "
                         f"{need} | {cost} |")
    else:
        lines.append("没有。每个论点都有主张也有证据。")
    lines.append("")

    lines += ["## 二、代码代画 / 外部补充段", ""]
    if outside:
        lines += ["| 格 | 类型 | 时长 | 为什么用它 | 如果有实拍可替换成什么 |", "|---|---|---|---|---|"]
        for s in outside:
            kd = s.get("template") or s.get("kind")
            lines.append(f"| #{s['n']} | `{kd}` | {_el_dur(s):.1f}s | {s.get('why', '')} | 实拍对应特写更真 |")
        lines.append("")
        lines.append(f"代码/外部画面合计 {gen_dur:.1f}s，占 {gen_dur / total * 100:.0f}%。")
    else:
        lines.append("全部来自实拍素材库。")
    lines.append("")

    if not a.no_search and arg_gaps and (lib / "_cutskills" / "index.json").exists():
        lines += ["## 三、库内反查（是不是选段漏了）", ""]
        used = [(x.get("asset"), float(x.get("in") or 0), float(x.get("out") or 0))
                for x in els if x.get("asset")]
        for b, own in arg_gaps:
            text = "".join((s.get("evidence") or "") for s in own if s.get("role") == "claim")
            need_ev = not any(s.get("role") == "evidence" for s in own)
            pics = search_by_text(lib, text, limit=3, max_dur=8) if (need_ev and text.strip()) else []
            words = transcript_hits(lib, text, limit=3, used=used) if text.strip() else []
            lines.append(f"- 论点 {b.get('n')}「{b.get('title') or '—'}」")
            if pics:
                lines.append("  - 画面：库里有 " + "；".join(
                    f"`{h['asset'].split('/')[-1]}` {h['in']:.1f}s（{h['tag'] or '—'}）" for h in pics))
            elif need_ev:
                lines.append("  - 画面：库里没有现成镜头 → 建议补拍一镜或用 `gen` 代码画")
            if words:
                lines.append("  - 口播：库里有讲过相关 → " + "；".join(
                    f"`{h['asset'].split('/')[-1]}` {h['in']:.1f}s「{h['text'][:26]}」" for h in words))
        lines.append("")

    out_p = proj / "gap.md"
    # 保留已有的指代词缺画面块
    old = out_p.read_text(encoding="utf-8") if out_p.exists() else ""
    vis_block = ""
    if VISUAL_GAP_BEGIN in old and VISUAL_GAP_END in old:
        _, _, rest = old.partition(VISUAL_GAP_BEGIN)
        mid, _, _ = rest.partition(VISUAL_GAP_END)
        vis_block = "\n" + VISUAL_GAP_BEGIN + mid + VISUAL_GAP_END + "\n"
    out_p.write_text("\n".join(lines) + vis_block, encoding="utf-8")
    print(f"→ {out_p}（论证缺口 {len(arg_gaps)} · 代码/外部段 {len(outside)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
