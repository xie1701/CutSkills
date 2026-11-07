#!/usr/bin/env python3
"""CutSkills · 视图生成：edl.json → paper-edit.md（按论点分节的三列稿）。

paper-edit.md 是**视图**：人看它做确认，机器只信 edl.json。
先手写草稿 → 确认后落 edl.json → 再用这个脚本重新生成定稿版，避免两边打架。

按论点分节，是因为「删第 3 条」这种改法只有看得见结构时才想得出来：人读稿时真正在判的是
「这个论点有没有立住」，不是每一句好不好。

用法：
  render_paper_edit.py --project "<项目目录>"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROLE_CN = {"setup": "交代", "claim": "主张", "evidence": "举证", "warrant": "说理", "turn": "转折", "payoff": "收束"}



def resolve_lib(proj: Path, given: str | None) -> Path:
    """素材库在哪。项目默认建在 `<库>/_cutskills/projects/<slug>`，那种情况下往上三层就是库；
    项目放在别处（/tmp、别的盘）时，往上三层会解析到一个没有索引的目录——以前这里
    直接抛 FileNotFoundError，看不出是「库路径错了」还是「索引没建」。所以要能显式指定。
    """
    if given:
        return Path(given).expanduser().resolve()
    cand = proj.parents[2] if len(proj.parents) > 2 else proj
    if (cand / "_cutskills" / "index.json").exists():
        return cand
    raise SystemExit(f"找不到素材库：{cand}/_cutskills/index.json 不存在。\n"
                     f"  项目默认要建在 <库>/_cutskills/projects/<slug> 下；\n"
                     f"  放在别处就加 --library \"<素材库根目录>\"。")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None, help="默认取项目往上三层的素材库")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = resolve_lib(proj, a.library)

    intent = json.loads((proj / "intent.json").read_text())
    edl = json.loads((proj / "edl.json").read_text())
    index = {x["id"]: x for x in json.loads((lib / "_cutskills" / "index.json").read_text())["assets"]}
    titles = {b.get("n"): (b.get("title") or "") for b in (edl.get("beats") or [])}

    # 逐段渲染成行，同时记每个论点从哪一行开始
    body, beat_of_row, durs = [], [], []
    t = 0.0
    for i, s in enumerate(edl.get("segments") or [], 1):
        aid, tin, tout = s.get("asset"), float(s.get("in", 0)), float(s.get("out", 0))
        au = s.get("audio") or {}
        src = au.get("asset") or aid
        ain = float(au.get("in", tin))
        aout = float(au.get("out", tout))
        dur = round(aout - ain, 2) if s.get("audio") else round(tout - tin, 2)
        rec = index.get(src) or {}
        phrases = (rec.get("transcript") or {}).get("phrases") or []
        src_text = "".join(p["text"] for p in phrases
                           if p["end"] > ain + 0.05 and p["start"] < aout - 0.05)
        if not src_text:
            vrec = index.get(aid) or {}
            src_text = (vrec.get("segments") or [{}])[0].get("summary") or ""
        where = f"`{aid}` {tin:.2f}–{tout:.2f}" if not s.get("audio") \
            else f"`{aid}` {tin:.2f}–{tout:.2f}<br><sub>声音 {src} {ain:.2f}–{aout:.2f}</sub>"
        role = ROLE_CN.get(s.get("role"), "—")
        body.append(f"| {i} | {where}<br>{src_text or '—'} | "
                    f"[{t:.2f}–{t + dur:.2f}] {s.get('medium', '?')} {dur:.2f}s | {role} | "
                    f"{s.get('why', '')} |")
        beat_of_row.append(s.get("beat"))
        durs.append(dur)
        t += dur

    n_seg = len(body)

    # ---- 按论点分节
    lines = []
    beat_ids = sorted({b for b in beat_of_row if b is not None})
    chunks: list[tuple[str, list[int]]] = []
    for n in beat_ids:
        idxs = [k for k, b in enumerate(beat_of_row) if b == n]
        secs = sum(durs[k] for k in idxs)
        chunks.append((f"论点 {n} · {titles.get(n) or '（没写标题）'}　{secs:.1f}s / {len(idxs)} 段",
                       idxs))
    loose = [k for k, b in enumerate(beat_of_row) if b is None]
    if loose:
        chunks.append((f"没归论点　{len(loose)} 段", loose))

    table_head = ["| # | 原文（素材内） | 成片（秒）| 角色 | 保留理由 |", "|---|---|---|---|---|"]
    if not chunks:
        chunks = [("", list(range(n_seg)))]

    for title, idxs in chunks:
        lines += ([""] + ([f"## {title}"] if title else []) + table_head)
        lines += [body[k] for k in idxs]

    header = [
        f"# 纸面剪辑 · {edl.get('slug', '?')}", "",
        f"**意图原文**：{intent.get('raw', '')}", "",
        f"**观众**：{intent.get('audience') or '—'} ｜ "
        f"**看完要相信/做什么**：{intent.get('promise') or '—'} ｜ "
        f"**目标时长**：{intent.get('duration_s') or '—'}s ｜ **画幅**：{intent.get('aspect') or '—'}", "",
        f"**当前成片估算**：{t:.1f}s / {n_seg} 段"
        + (f"（目标 {intent.get('duration_s')}s）" if intent.get("duration_s") else "")
        + (f" ｜ {len(beat_ids)} 个论点" if beat_ids else " ｜ ⚠ 没有论点结构"), "",
        "> 确认这一版只要读稿子。改法直接说「删第 3 条」「第 5 和第 6 换顺序」"
        "「论点 2 缺证据」，不用描述怎么剪。", "",
    ]

    dec = proj / "decisions.md"
    tail = []
    if dec.exists():
        hist_rows = [ln for ln in dec.read_text().splitlines() if ln.startswith("| 20")]
        if hist_rows:
            tail = ["", "## 已确认过的（最近的在前）", "",
                    "| 时间 | 用户原话 | 做了什么 | 结论 |", "|---|---|---|---|"]
            tail += list(reversed(hist_rows))[:10]

    (proj / "paper-edit.md").write_text("\n".join(header + lines + tail) + "\n")
    print(f"paper-edit.md 已生成：{n_seg} 段 / {t:.1f}s / {len(beat_ids)} 个论点"
          f" → {proj/'paper-edit.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
