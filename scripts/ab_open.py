#!/usr/bin/env python3
"""CutSkills · A/B 双开场：同一批素材、两种开头、其余完全不动。

「开头三秒决定人留不留」这句话人人会说，但以前做不了对照——手剪两条片，变量不止开场一个，
比出来也不知道差在哪。结构层（beat/role）做好之后，第一次可能做到**只换开场**：

  变体 A「结论前置」：把片子里态度最硬的那句主张搬到最前
  变体 B「现场切入」：先给一个能看见的现场，第 4 秒才说清在干什么

其余段（11 段）**逐字节相同**，所以 A/B 的差别只可能来自开场。

产出（项目下 `ab/`）：
  plan-A.json / plan-B.json   两版计划（可直接交给 make_edl）
  A/  B/                      各是一个迷你项目，已渲染开场
  A.mp4  B.mp4                两条开场（8–12 秒），可以直接看
  compare.md                  并排文字 + 两版差在哪
  blind.md                    随机标成「甲 / 乙」的盲选包（隐藏变体名），给人选

用法：
  ab_open.py --project <项目目录> [--variant A,B] [--no-render]
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(proj: Path) -> tuple[dict, list[dict]]:
    plan = json.loads((proj / "plan.json").read_text())
    beats = plan.get("beats") or [] if isinstance(plan, dict) else []
    segs = plan.get("segments") if isinstance(plan, dict) else plan
    return {"beats": beats}, list(segs)


def hook_a(segs: list[dict]) -> tuple[dict, int]:
    """结论前置：搬最硬的一句主张到最前。

    挑「在论点 2 之后、role=claim、字最多的那段」——开场不该用第一个论点里还没立住的话，
    要找片中段那个已经被证据准备好的结论。
    """
    claims = [s for s in segs
              if s.get("role") == "claim" and (s.get("beat") or 1) >= 2]
    pool = [s for s in claims if s.get("out", 0) - s.get("in", 0) <= 9] or claims
    if not pool:
        return segs[0], 0
    pick = max(pool, key=lambda s: s.get("out", 0) - s.get("in", 0))
    return pick, segs.index(pick)


def hook_b(segs: list[dict]) -> tuple[dict, int]:
    """现场切入：搬一段能看见的 broll（带原口播声音）到最前。"""
    pool = [s for s in segs if s.get("medium") == "broll" and s.get("audio_in") is not None]
    if pool:
        pick = max(pool, key=lambda s: s.get("out", 0) - s.get("in", 0))
        return pick, segs.index(pick)
    # 没有带声音的 broll，就退一步找纯画面段（开场会变成无声），不推荐但比没有好
    pics = [s for s in segs if s.get("medium") in ("photo", "broll", "screen")]
    if pics:
        return pics[0], segs.index(pics[0])
    return segs[0], 0


def variant_plan(segs: list[dict], pick: dict, at: int, note: str) -> dict:
    """把 pick 挪到最前，其余顺序不变；论点号保留（开场借用别处的段，是刻意的）。"""
    rest = [s for i, s in enumerate(segs) if i != at]
    moved = dict(pick)
    moved["why"] = f"【A/B 开场】{note}｜原位置：" + (pick.get("why") or "")
    return {"segments": [moved] + rest}


def opening(plan: dict, seconds: float = 10.0) -> dict:
    """只取开场那几段：对比开场不需要让人看完整片，也不需要渲染 71 秒。"""
    segs, acc = [], 0.0
    for s in plan["segments"]:
        segs.append(s)
        acc += (s.get("audio_out", s.get("out", 0)) - s.get("audio_in", s.get("in", 0))) \
            if s.get("audio_in") is not None else (s.get("out", 0) - s.get("in", 0))
        if acc >= seconds:
            break
    return {"segments": segs}


def write_project(proj: Path, lib: Path, tag: str, plan: dict) -> Path:
    """在项目下建一个迷你项目：只有开场几段，能直接交给 render_film。"""
    sub = proj / "ab" / tag
    (sub / "out").mkdir(parents=True, exist_ok=True)
    intent = json.loads((proj / "intent.json").read_text())
    intent["duration_s"] = None
    (sub / "intent.json").write_text(json.dumps(intent, ensure_ascii=False, indent=1))
    sys.path.insert(0, str(HERE))
    from make_edl import build_edl                      # noqa: PLC0415

    index = {x["id"]: x for x in json.loads(
        (lib / "_cutskills" / "index.json").read_text())["assets"]}
    built, problems = build_edl(lib, {"beats": [], "segments": plan["segments"]}, index)
    for lvl, msg in problems:
        print(f"  {tag} {lvl}: {msg}")
    edl = {"version": 2, "slug": f"{proj.name}-{tag}", "created": intent.get("created"),
           "cut_source": intent.get("cut_source", "speech"),
           "visual_selector": intent.get("visual_selector", "single_cam_talking"),
           "style_pack": intent.get("style_pack", "talking-head-v1"),
           "beats": built["beats"], "segments": built["segments"]}
    (sub / "edl.json").write_text(json.dumps(edl, ensure_ascii=False, indent=1))
    return sub



def resolve_lib(proj: Path, given: str | None = None) -> Path:
    """素材库在哪（委托给 libs.resolve_lib 单一真值源）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from libs import resolve_lib as _resolve_lib
    return _resolve_lib(proj, given, strict=True)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None, help="默认取项目往上三层的素材库")
    ap.add_argument("--variant", default="A,B", help="要生成哪几版，默认 A,B")
    ap.add_argument("--seconds", type=float, default=10.0, help="每条开场渲染多长（默认 10s）")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = resolve_lib(proj, a.library)
    meta, segs = load(proj)
    outdir = proj / "ab"
    outdir.mkdir(exist_ok=True)

    makers = {"A": (hook_a, "结论前置"), "B": (hook_b, "现场切入")}
    made = {}
    for tag in [t.strip() for t in a.variant.split(",") if t.strip()]:
        if tag not in makers:
            print(f"没有变体 {tag}（只有 A / B）", file=sys.stderr)
            return 2
        maker, note = makers[tag]
        pick, at = maker(segs)
        plan = variant_plan(segs, pick, at, note)
        (outdir / f"plan-{tag}.json").write_text(
            json.dumps({"beats": meta["beats"], "segments": plan["segments"]},
                       ensure_ascii=False, indent=1))
        made[tag] = {"plan": plan, "pick": pick, "note": note, "from": at + 1}

    # ---- 并排文字 + 盲选包
    rows = ["# A/B 双开场", "",
            f"基准：{len(segs)} 段。两版**只换开场**，其余段逐字节相同。", "",
            "| 变体 | 打法 | 开场段 | 原来在第几段 | 时长 |", "|---|---|---|---|---|"]
    for tag, m in made.items():
        p = m["pick"]
        dur = (p.get("audio_out", p.get("out")) - p.get("audio_in", p.get("in"))) \
            if p.get("audio_in") is not None else (p["out"] - p["in"])
        rows.append(f"| {tag} | {m['note']} | `{p['asset']}` {p['in']:.2f}–{p['out']:.2f} | "
                    f"#{m['from']} | {dur:.1f}s |")
    rows += ["", "## 开场说了什么", ""]
    for tag, m in made.items():
        rows += [f"**{tag}（{m['note']}）**：{m['pick'].get('why', '').replace('【A/B 开场】', '')}", ""]

    order = list(made.items())
    random.shuffle(order)
    labels = ["甲", "乙"]
    rows += ["## 盲选包（给人选，不给变体名）", "",
             "> 只问一句：**哪条让你更想往下看，为什么？**不要告诉对方哪条是 A、哪条是 B，"
             "也不要解释打法。两样都发：视频（`ab/blind/甲.mp4`、`ab/blind/乙.mp4`）和下面的文字。", ""]
    for lab, (tag, m) in zip(labels, order):
        rows.append(f"**{lab}**")
        rows.append("")
    rows += ["", f"> 答案对照（选完再看）：{'、'.join(f'{l}={t}' for l, (t, _) in zip(labels, order))}", ""]
    (outdir / "compare.md").write_text("\n".join(rows))

    # ---- 渲染开场
    if not a.no_render:
        for tag, m in made.items():
            sub = write_project(proj, lib, tag, opening(m["plan"], a.seconds))
            cmd = [sys.executable, str(HERE / "render_film.py"),
                   "--project", str(sub), "--library", str(lib), "--no-punch"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            film = sub / "out" / "film.mp4"
            ok = film.exists()
            print(f"{tag} 开场 {'→ ' + str(film) if ok else '渲染失败'}")
            if not ok:
                print((r.stdout or "")[-500:], (r.stderr or "")[-500:])

    # 盲选包：视频和文字都对上，但名字只有甲乙
    blind_dir = outdir / "blind"
    blind_dir.mkdir(exist_ok=True)
    order = list(made.items())
    random.shuffle(order)
    labels = ["甲", "乙"]
    txt = []
    for lab, (tag, m) in zip(labels, order):
        src = outdir / tag / "out" / "film.mp4"
        if src.exists():
            shutil.copyfile(src, blind_dir / f"{lab}.mp4")
        lines = []
        edl = outdir / tag / "edl.json"
        if edl.exists():
            for s in json.loads(edl.read_text())["segments"]:
                if (s.get("evidence") or "").strip():
                    lines.append(f"{s['n']}. {s['evidence']}")
        txt += [f"**{lab}**", ""] + (lines or ["（没有文字）"]) + [""]
    cmp_f = outdir / "compare.md"
    body = cmp_f.read_text()
    body = body.replace("**甲**\n\n**乙**\n", "\n".join(txt))
    cmp_f.write_text(body)

    print(f"A/B 已生成：{', '.join(made)}")
    print(f"对照 → {outdir / 'compare.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
