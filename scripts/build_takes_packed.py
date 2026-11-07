#!/usr/bin/env python3
"""CutSkills · A→B 桥：把 index.json 变成 agent 能读的视图。

产出两个文件（都在 _cutskills/ 或指定项目里）：
  takes_packed.md  有语音的素材，短语级、带时间码 —— 剪取舍主要读这个
  inventory.md     无语音素材（B-roll / 照片）的可用段清单，带分级和 L2 描述

不要直接读 index.json：那是给机器看的，token 太贵。

用法：
  build_takes_packed.py --library "<素材库>" [--project <项目目录>]
                        [--batch 熊工] [--min-grade backup] [--max-chars 60000]
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

GRADE_RANK = {"must-use": 0, "backup": 1, "do-not-use": 2}


def best_seg(rec: dict) -> dict | None:
    """这支素材「最好的一段」。

    取第一段会出事：口播1 开头是喝茶闲聊，第一段判「不用」会把整支标成不用。
    口径与 rename_assets.py / make_edl.py 一致，三处不能各说各的。
    """
    segs = rec.get("segments") or []
    return min(segs, key=lambda s: GRADE_RANK.get(s.get("grade") or "backup", 3)) if segs else None


def repeat_flags(phrases: list[dict]) -> list[str]:
    """标出疑似口误与重复。B 段删东西时最需要的两个提示。

    只标不改——删不删是判断，不是规则。三条信号：
      ① 相邻 5 句内出现包含关系（长度差不多）→ 同一意思说了两遍
      ② 句子以修正词开头/前 6 字 → 口误后自我修正，该留后面那句
      ③ 全片重复出现（出现 ≥2 次）→ 开头寒暄常见，删的时候别犹豫
    """
    FIX = ("我是说", "说错了", "说错", "重来", "重说", "不是不是", "不对不对", "呃不对")

    def norm(t: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", t)

    norms = [norm(p["text"]) for p in phrases]
    flags = ["" for _ in phrases]
    for i, t in enumerate(norms):
        if len(t) < 3:
            continue
        # ① 同一句说两遍：包含关系，或者只差几个字的近似
        for j in range(max(0, i - 6), min(len(norms), i + 7)):
            if j == i:
                continue
            o = norms[j]
            if len(o) < 3:
                continue
            if (t in o or o in t) and min(len(t), len(o)) / max(len(t), len(o)) > 0.6:
                flags[i] = "⚠重复"
                break
            if len(t) >= 6 and len(o) >= 6 and difflib.SequenceMatcher(None, t, o).ratio() >= 0.75:
                flags[i] = "⚠重复"
                break
        # ② 说一半重来：本句尾巴就是下一句开头（ASR 里表现为同一串字重复出现）
        if i + 1 < len(norms):
            nxt = norms[i + 1]
            for k in range(min(8, len(t)), 2, -1):
                if nxt.startswith(t[-k:]):
                    flags[i] = f"{flags[i]}/⚠重启" if flags[i] else "⚠重启"
                    break
        # ③ 口误后自我修正
        if any(w in t[:6] for w in FIX):
            flags[i] = f"{flags[i]}/⚠疑似修正" if flags[i] else "⚠疑似修正"
    return flags


def load_index(lib: Path) -> dict:
    p = lib / "_cutskills" / "index.json"
    if not p.exists():
        raise SystemExit(f"索引不存在，先跑 index_assets.py：{p}")
    return json.loads(p.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--project", default=None)
    ap.add_argument("--batch", default=None, help="只看某个批次")
    ap.add_argument("--include", action="append", default=[],
                    help="只要路径含该子串的素材，可多次")
    ap.add_argument("--min-grade", default="backup", choices=["must-use", "backup", "do-not-use"])
    ap.add_argument("--max-chars", type=int, default=60000)
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    idx = load_index(lib)
    out_dir = Path(a.project).expanduser().resolve() if a.project else lib / "_cutskills"
    out_dir.mkdir(parents=True, exist_ok=True)

    assets = [x for x in idx["assets"] if (not a.batch or a.batch in x["id"])]
    if a.include:
        assets = [x for x in assets if any(k in x["id"] for k in a.include)]
    speech = [x for x in assets if (x.get("transcript") or {}).get("phrases")]
    broll = [x for x in assets if x["kind"] in {"video", "photo"} and x not in speech]

    # ---- takes_packed.md
    lines = ["# 转写（短语级，带时间码）\n", "\n",
             f"库：{idx['library']} · 有语音素材 {len(speech)} 个 · "
             f"总时长 {sum((x.get('duration') or 0) for x in speech)/60:.1f} 分钟\n", "\n",
             "时间码是**素材内时间**，不是成片时间。切边一律吸附到这里出现的字符边界。\n",
             "`⚠重复` / `⚠疑似修正` 是脚本标出的可疑处（同一意思说两遍、口误后自我修正）——"
             "只标不改，删不删你来定。\n", "\n"]
    used = sum(len(x) for x in lines)
    for x in sorted(speech, key=lambda y: y["id"]):
        tr = x["transcript"]
        head = (f"\n## {x['id']}\n<{x.get('duration', 0):.1f}s · {x.get('width')}x{x.get('height')} · "
                f"{len(tr['phrases'])} 短语 · 引擎 {tr.get('engine')}>\n")
        lines.append(head)
        used += len(head)
        marks = repeat_flags(tr["phrases"])
        for k, p in enumerate(tr["phrases"]):
            mark = f"   {marks[k]}" if marks[k] else ""
            row = f"  [{p['start']:07.2f}-{p['end']:07.2f}] {p['text']}{mark}\n"
            if used + len(row) > a.max_chars:
                lines.append("\n> 已截断：素材太多，用 --batch 或 --include 分批看。\n")
                break
            lines.append(row)
            used += len(row)
        if used > a.max_chars:
            break
    (out_dir / "takes_packed.md").write_text("".join(lines))

    # ---- inventory.md
    inv = ["# 可用画面（无语音素材 / B-roll）", "",
           "分级来自 L0（能不能看）；有 `L2` 描述的才是能不能用。剪片时从这里取画面证据。", "",
           "| 素材 | 时长 | 分级 | 可用段 | 内容（L2） | 备注 |", "|---|---|---|---|---|---|"]
    for x in sorted(broll, key=lambda y: (GRADE_RANK.get(
            ((best_seg(y) or {}).get("grade") or "backup"), 3), y["id"])):
        segs = x.get("segments") or []
        bs = best_seg(x) or {}
        g = bs.get("grade") or "backup"
        if GRADE_RANK.get(g, 3) > GRADE_RANK[a.min_grade]:
            continue
        rng = (f"{bs['in']:.1f}–{bs['out']:.1f}s" if bs and x["kind"] == "video" else "—")
        content = " / ".join(filter(None, [s.get("summary") or s.get("action") for s in segs]))
        note = "; ".join(filter(None, [
            (x.get("quality") or {}).get("blur") and f"blur {(x['quality']['blur']['mean']):.1f}",
            bs.get("why"),
        ]))
        inv.append(f"| {x['id']} | {x.get('duration') or 0:.1f}s | {g} | {rng} | "
                   f"{(content or '—').replace('|', '/')} | {(note or '').replace('|', '/')} |")
    inv += ["", f"共 {len([1 for l in inv if l.startswith('| ') and '---' not in l]) - 1} 条可用画面。"]
    (out_dir / "inventory.md").write_text("\n".join(inv) + "\n")

    print(f"takes_packed.md  {len(speech)} 个有语音素材")
    print(f"inventory.md     {len(broll)} 个画面素材")
    print(f"→ {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
