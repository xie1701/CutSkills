#!/usr/bin/env python3
"""CutSkills · 良率卡：一条片出片时打一张卡，累积成趋势。

为什么要有这个东西：19 项像素断言只回答「这一条合不合格」，回答不了
「这条比上一条好还是差」。制造业靠首件检验 + 良率曲线活着，剪片的人不做这件事，
所以每次改参数都是凭记忆。卡一打，劣化立刻看得见。

四个判官，各有各的盲区：
  形式 judge —— verify_film 的 19 项（像素上量，管几何和信号）
  结构 judge —— timeline 摘要（元素数、中位长度、生成物占比、role 齐不齐）
  主张 judge —— 盲测结果（≥2 位独立读者说同一句话才算过）
  钩子 judge —— 前 3 秒 A/B（第三方二选一，2/3 过线）

用法：
  yield_card.py --project <项目> [--hook hooks.json] [--blind blind.json]
                [--json] [--no-verify]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sh(*cmd: str) -> str:
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def parse_verify(proj: Path, style: str | None) -> dict:
    out = sh(sys.executable, str(HERE / "verify_film.py"), "--project", str(proj),
             *(["--style", style] if style else []))
    m = re.search(r"验收：🟢\s*(\d+)\s*·\s*🟡\s*(\d+)\s*·\s*🔴\s*(\d+)", out)
    green, yellow, red = (int(m.group(i)) for i in (1, 2, 3)) if m else (0, 0, 0)
    fails = [ln.strip(" ·") for ln in out.splitlines() if ln.strip().startswith(("×", "✗", "!"))]
    return {"green": green, "yellow": yellow, "red": red, "detail": fails[:12], "raw": out[-1500:]}


def timeline_summary(proj: Path) -> dict:
    f = proj / "timeline.json"
    if not f.exists():
        return {}
    tl = json.loads(f.read_text())
    els = tl.get("elements") or []
    if not els:
        return {}
    # overlay 格不占时间轴——算进中位和生成物占比会把 79 秒的片算成 83 秒
    seq = [e for e in els if e.get("track") != "overlay"]
    lens = sorted((float(e["out"]) - float(e["in"])) if e.get("kind") == "clip"
                  else float(e.get("dur") or 0) for e in seq)
    total = sum(lens) or 1.0
    gen_s = sum(float(e.get("dur") or 0) for e in seq if e.get("kind") in ("gen", "silence"))
    roles = {e.get("role") for e in els if e.get("role")}
    kinds: dict[str, int] = {}
    for e in els:
        kinds[e.get("kind") or "clip"] = kinds.get(e.get("kind") or "clip", 0) + 1
    return {"elements": len(els), "kinds": kinds, "total_s": round(total, 2),
            "median_len_s": round(lens[len(lens) // 2], 2),
            "p10": round(lens[int(len(lens) * 0.1)], 2),
            "p90": round(lens[min(len(lens) - 1, int(len(lens) * 0.9))], 2),
            "gen_ratio": round(gen_s / total, 3),
            "roles": sorted(roles), "missing_roles": sorted({"setup", "claim", "evidence",
                                                             "warrant", "turn", "payoff"} - roles)}


def subs_summary(proj: Path) -> dict:
    f = proj / "out" / "subs.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text())
    cues = d.get("cues") or []
    film = proj / "out" / "film-hf.mp4"
    return {"cues": len(cues), "avg_chars": d.get("avg_chars"),
            "chars_per_s": d.get("chars_per_s"),
            "film_s": round(float(sh_s(proj)), 2) if film.exists() else None}


def sh_s(proj: Path) -> float:
    f = proj / "out" / "film-hf.mp4"
    if not f.exists():
        return 0.0
    out = sh("ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(f)).strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def decisions_count(proj: Path) -> int:
    f = proj / "decisions.md"
    if not f.exists():
        return 0
    return sum(1 for ln in f.read_text().splitlines() if ln.startswith(("-", "|", "*")) and ln.strip(" |-*"))


def gaps_count(proj: Path) -> int:
    for nm in ("gaps.md", "gap.md"):
        f = proj / nm
        if f.exists():
            return len(re.findall(r"^\s*(?:[-*]|\d+\.)\s+\S", f.read_text(), re.M))
    return 0


def verdict(v: dict, tl: dict, blind: dict | None, hook: dict | None) -> tuple[str, list[str]]:
    bad: list[str] = []
    if v.get("red"):
        bad.append(f"形式 {v['red']} 项红")
    if v.get("yellow"):
        bad.append(f"形式 {v['yellow']} 项黄")
    if tl.get("gen_ratio", 0) > 0.25:
        bad.append(f"生成物占 {tl['gen_ratio']:.0%}")
    if "warrant" in (tl.get("missing_roles") or []):
        bad.append("缺 warrant（只说证据不说理）")
    if blind and not blind.get("passed"):
        bad.append("盲测没过（读者说不出同一句话）")
    if hook and hook.get("passed") is False:
        bad.append("前 3 秒 A/B 没过")
    elif hook and hook.get("chosen") and hook.get("passed") is None:
        bad.append(f"前 3 秒选了 {hook['chosen']}，但没真人验过（只能算「看着还行」）")
    if not blind:
        bad.append("没做盲测")
    if not hook or (not hook.get("chosen") and hook.get("passed") is None):
        bad.append("没做前 3 秒 A/B")
    if not bad:
        return "可发", []
    severe = (bool(v.get("red"))
              or bool(blind and not blind.get("passed"))
              or bool(hook and hook.get("passed") is False))
    return ("不可发" if severe else "可发但记着"), bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--style", default=None)
    ap.add_argument("--hook", default=None, help="hooks.json（前 3 秒 A/B 结果）")
    ap.add_argument("--blind", default=None, help="blind.json（盲测聚合结果）")
    ap.add_argument("--no-verify", action="store_true", help="不重跑 verify_film，读旧报告")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    verify = ({"green": 0, "yellow": 0, "red": 0, "detail": [], "raw": ""}
              if a.no_verify else parse_verify(proj, a.style))
    tl = timeline_summary(proj)
    subs = subs_summary(proj)
    blind = json.loads(Path(a.blind).read_text()) if a.blind else None
    hook = json.loads(Path(a.hook).read_text()) if a.hook else None
    vd, bad = verdict(verify, tl, blind, hook)

    card = {
        "slug": proj.name, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "verdict": vd, "reasons": bad,
        "film_s": round(sh_s(proj), 2),
        "form": {k: verify.get(k) for k in ("green", "yellow", "red")},
        "timeline": tl, "subs": subs,
        "blind": {"passed": blind.get("passed"), "agree": blind.get("agree")} if blind else None,
        "hook": ({"passed": hook.get("passed"), "winner": hook.get("chosen") or hook.get("winner")}
                 if hook else None),
        "gaps": gaps_count(proj), "decisions": decisions_count(proj),
    }

    # 趋势：和上一张卡比
    hist = proj.parent.parent / "yield-history.jsonl"
    prev = None
    if hist.exists():
        lines = [ln for ln in hist.read_text().splitlines() if ln.strip()]
        same = [json.loads(ln) for ln in lines if json.loads(ln).get("slug") == proj.name]
        prev = same[-1] if same else None
    if prev:
        d = card["film_s"] - float(prev.get("film_s") or 0)
        card["delta_vs_prev"] = {"film_s": round(d, 2),
                                 "form_green": card["form"]["green"] - (prev.get("form") or {}).get("green", 0)}
    with hist.open("a") as f:
        f.write(json.dumps(card, ensure_ascii=False) + "\n")

    md = [f"# 良率卡 · {card['slug']}", "",
          f"{card['at']} · **{vd}** · {card['film_s']}s", ""]
    if bad:
        md += ["没过的：", *[f"- {b}" for b in bad], ""]
    md += ["| 判官 | 读数 |", "|---|---|",
           f"| 形式（像素 19 项）| 🟢 {card['form']['green']} · 🟡 {card['form']['yellow']} · 🔴 {card['form']['red']} |",
           f"| 结构 | {tl.get('elements', '—')} 元素 · 中位 {tl.get('median_len_s', '—')}s"
           f" · 生成物 {tl.get('gen_ratio', 0):.0%} |" if tl else "| 结构 | 没有 timeline.json |",
           f"| 字幕 | {subs.get('cues', '—')} 条 · 均 {subs.get('avg_chars', '—')} 字 |",
           f"| 盲测 | {'过' if (blind or {}).get('passed') else ('没过' if blind else '没做')} |",
           f"| 前 3 秒 A/B | " + ("过" if (hook or {}).get("passed") else
                                ("没过" if (hook or {}).get("passed") is False else
                                 (f"选了 {hook['chosen']}，未验" if (hook or {}).get("chosen") else "没做"))) + " |",
           f"| 内容缺口 | {card['gaps']} 条待补 |",
           f"| 人工决定 | {card['decisions']} 条 |", ""]
    if prev:
        md += [f"对上一张卡：时长 {card['delta_vs_prev']['film_s']:+.2f}s"
               f" · 绿灯 {card['delta_vs_prev']['form_green']:+d}", ""]
    (proj / "yield-card.md").write_text("\n".join(md), encoding="utf-8")

    if a.json:
        print(json.dumps(card, ensure_ascii=False, indent=1))
    else:
        print("\n".join(md))
        print(f"→ {proj / 'yield-card.md'}")
        print(f"→ 趋势追加到 {hist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
