#!/usr/bin/env python3
"""CutSkills · 缺口清单：这条片子缺什么，写到 gap.md 让人去补。

BRIEF 的四级兜底（本库 → CC0 → 代码生成 → 反馈用户）要求"给用户一张缺口清单：缺什么、
在哪一段、为什么别的东西替代不了、不加会怎样"。以前只有 `source: generated` 字段和 20% 占比
警告，清单本身不存在——等于这一级写在需求里但没落地。

脚本能自动填两类：**论证缺口**（论点没有证据）和**非本库素材**（CC0 / 生成物，必须说清为什么）。
第三类「缺的素材该长什么样」只有人（或 agent）知道，这里留一张空表让人填，不假装能推断。

用法：
  gaps.py --project <项目目录>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from search_library import search_by_text  # noqa: E402


def _bg(t: str) -> set[str]:
    t = "".join(ch for ch in t if "\u4e00" <= ch <= "\u9fa5")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def transcript_hits(lib: Path, text: str, limit: int = 4,
                    used: list[tuple[str, float, float]] | None = None) -> list[dict]:
    """口播里有没有人讲过这件事 —— 缺「说理」时该搜的是这个，不是画面。

    `used` 是成片已经用掉的段：搜出来的东西必须是**还没用的**，否则只是把已经剪进去的句子
    指回来一遍，看着像「库里有」，其实是自我复读。
    """
    idx = json.loads((lib / "_cutskills" / "index.json").read_text())["assets"]
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

ROLE_CN = {"setup": "交代", "claim": "主张", "evidence": "举证", "warrant": "说理",
           "turn": "转折", "payoff": "收束"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--blind", action="append", default=[], help="盲测回答文件，可多次")
    ap.add_argument("--library", default=None, help="给了就在库里反查：这个缺口是不是真没有画面")
    ap.add_argument("--no-search", action="store_true", help="不查库（快，但缺口只报不验证）")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else proj.parents[2]
    edl = json.loads((proj / "edl.json").read_text())
    intent = json.loads((proj / "intent.json").read_text()) if (proj / "intent.json").exists() else {}
    segs = edl.get("segments") or []
    total = sum(s["out"] - s["in"] for s in segs)

    # ---- 一、论证缺口：论点有主张、没证据
    arg_gaps = []
    for b in edl.get("beats") or []:
        own = [s for s in segs if s.get("beat") == b.get("n")]
        roles = {s.get("role") for s in own}
        if "claim" in roles and "evidence" not in roles:
            arg_gaps.append((b, own))
        elif "claim" in roles and "evidence" in roles and "warrant" not in roles:
            arg_gaps.append((b, own))          # 轻缺口：证据在、没说理

    # ---- 二、非本库素材：必须写清为什么替代不了
    outside = [s for s in segs if s.get("source") and s.get("source") != "library"]

    lines = [f"# 缺口清单 · {edl.get('slug', '?')}", "",
             f"成片 {total:.1f}s / {len(segs)} 段。这份清单只列**真的缺**：论证缺证据、素材非本库、"
             "以及需要你补的东西。不列「可以更好」的地方——那些不是缺口。", ""]

    lines += ["## 一、论证缺口（论点有主张、缺证据或说理）", ""]
    if arg_gaps:
        lines += ["| 论点 | 段 | 已有角色 | 缺什么 | 不加会怎样 |", "|---|---|---|---|---|"]
        for b, own in arg_gaps:
            rs = {s.get("role") for s in own if s.get("role")}
            shown = "、".join(ROLE_CN.get(r, r) for r in
                             sorted(rs, key=lambda r: list(ROLE_CN).index(r) if r in ROLE_CN else 9))
            if "evidence" not in rs:
                need = "证据（一段能看到的东西）"
                cost = "观众会怀疑这一段的结论，即使前面都对"
            else:
                need = "说理（为什么这条证据能推出这个结论）"
                cost = "证据摆在那儿，观众不知道它证明了什么"
            lines.append(f"| {b.get('n')}. {b.get('title') or '—'} | "
                         f"{'、'.join('#' + str(s['n']) for s in own)} | {shown or '—'} | "
                         f"{need} | {cost} |")
    else:
        lines.append("没有。每个论点都有主张也有证据。")
    lines.append("")

    lines += ["## 二、非本库素材（CC0 / 代码生成）", ""]
    if outside:
        lines += ["| 段 | 来源 | 为什么要用它 | 能不能换成库里的 |", "|---|---|---|---|"]
        for s in outside:
            lines.append(f"| #{s['n']} | {s.get('source')} | {s.get('why', '')} | 待确认 |")
        lines.append("")
        lines.append(f"生成物 + 外部素材合计 {sum(s['out'] - s['in'] for s in outside):.1f}s，"
                     f"占 {sum(s['out'] - s['in'] for s in outside) / total * 100:.0f}%（超过 20% 片子会像模板）。")
    else:
        lines.append("没有，全部来本库。")
    lines.append("")

    lines += ["## 三、需要补的素材（只有你知道该长什么样）", "",
              "| 缺什么 | 用在哪一段 | 为什么别的替代不了 | 不加会怎样 | 优先级 |",
              "|---|---|---|---|---|",
              "| | | | | |", "",
              "> 上一行是模板，填完可以删掉表头说明。**这张表是给用户看的**，不是在项目里留痕用的。", ""]

    # ---- 缺口验证：这个缺口是「没剪进去」还是「库里真没有」
    found: dict[int, list[dict]] = {}
    if not a.no_search and arg_gaps:
        lines += ["## 一之二、缺口验证（库里到底有没有）", "",
                  "把每个缺口论点的原话拿去和全库 L2 的画面描述比对。**「库里有」= 是选段漏了；"
                  "「库里没有」= 要么新拍，要么换论点。**", ""]
        for b, own in arg_gaps:
            text = "".join((s.get("evidence") or "") for s in own if s.get("role") == "claim")
            need_ev = not any(s.get("role") == "evidence" for s in own)
            pics = search_by_text(lib, text, limit=3, max_dur=8) if (need_ev and text.strip()) else []
            used = [(x.get("asset"), float(x.get("in") or 0), float(x.get("out") or 0))
                    for x in segs]
            words = transcript_hits(lib, text, limit=3, used=used) if text.strip() else []
            found[b.get("n")] = pics
            kind = "缺证据（该找画面）" if need_ev else "缺说理（该找一段把道理讲出来的话）"
            lines.append(f"- 论点 {b.get('n')}「{b.get('title') or '—'}」（{kind}）")
            if pics:
                lines.append("  - 画面：库里有 " + "；".join(
                    f"`{h['asset'].split('/')[-1]}` {h['in']:.1f}s（{h['tag'] or '—'}）"
                    for h in pics) + " —— 是选段漏了")
            elif need_ev:
                lines.append("  - 画面：库里**没有**配得上的 —— 不是剪辑问题，是素材/数据问题")
            if words:
                lines.append("  - 口播：库里有讲过相关的 → " + "；".join(
                    f"`{h['asset'].split('/')[-1]}` {h['in']:.1f}s「{h['text'][:26]}」" for h in words))
            else:
                lines.append("  - 口播：库里也没有人讲过这件事 —— 要补内容，不是补剪辑")
        lines.append("")

    # ---- 三之二、裁判原话：读的人说缺什么，照抄不改写
    if a.blind:
        chunks = []
        for f in a.blind:
            fp = Path(f).expanduser().resolve()
            if not fp.exists():
                chunks.append(f"- （没找到 `{fp.name}`）")
                continue
            keep, on = [], False
            for ln in fp.read_text().splitlines():
                head = ln.strip()
                if head.startswith("## 3"):
                    on = True
                    continue
                if on and head.startswith("## "):
                    break
                if on and head:
                    keep.append(ln.rstrip())
            chunks.append(f"**{fp.stem}**\n\n" + ("\n".join(keep) if keep
                                                   else "（这份回答没有单列「不信」一节）"))
        lines += ["## 三之二、裁判说缺什么（原话，不改写）", "",
                  "机器自检补不上的那一半，只有读的人说得清。这里直接贴他们的话，不归纳——"
                  "归纳会把「我需要每平米差价」变成「价格信息不足」，然后没人能照着补。", "",
                  "> 这些原话是针对**当时那一版稿子**说的。改过之后再读，如果某一条对不上了，"
                  "说明那条改到位了；对得上的，才是还欠着的。", ""]
        for c in chunks:
            lines += [c, ""]

    notes = []
    for s in segs:
        if s.get("grade_at_cut") == "do-not-use":
            notes.append(f"#{s['n']} 用的是 do-not-use 段（{s['asset']}）——确认是刻意的")
    if not edl.get("beats"):
        notes.append("整条片子没有 beat/role：结构层是空的，缺口清单只能列素材、列不了论证")
    lines += [f"- {n}" for n in notes] or ["没有。"]
    lines.append("")

    out = proj / "gap.md"
    out.write_text("\n".join(lines))
    print(f"gap.md 已生成：论证缺口 {len(arg_gaps)} 个 / 非本库素材 {len(outside)} 段 → {out}")
    for b, _ in arg_gaps:
        print(f"  · 论点 {b.get('n')}「{b.get('title') or '—'}」缺"
              + ("证据" if "evidence" not in {s.get('role') for s in segs if s.get('beat') == b.get('n')}
                 else "说理"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
