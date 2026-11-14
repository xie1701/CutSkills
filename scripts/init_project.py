#!/usr/bin/env python3
"""CutSkills · 建一个剪辑项目目录（初始化 intent.json / brief.json / decisions.md）。

用法：
  init_project.py --library "<素材库>" --intent "<用户原话>" --slug my-film
                  [--audience ..] [--promise ..] [--duration 45] [--aspect 9:16]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

DEFAULTS = {
    "style_pack": "talking-head-v1",
    "pack": "P1-warm",
    "plan": "S1-conclude-fast",
}


def guess_duration(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(秒|分钟|分)", text)
    if not m:
        return None
    v = float(m.group(1))
    return int(v * 60) if "分" in m.group(2) else int(v)


def guess_aspect(text: str) -> str | None:
    if re.search(r"竖|9:16|抖音|视频号|小红书|shorts", text, re.I):
        return "9:16"
    if re.search(r"横|16:9|b站|B站|youtube|YouTube", text):
        return "16:9"
    if re.search(r"4:5", text):
        return "4:5"
    return "9:16"


def main() -> int:
    ap = argparse.ArgumentParser(description="创建剪辑项目并写入初始意图与需求卡")
    ap.add_argument("--library", required=True)
    ap.add_argument("--intent", required=True, help="用户原话，别改写")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--audience", default=None)
    ap.add_argument("--promise", default=None, help="看完要让观众相信 / 做什么")
    ap.add_argument("--duration", type=int, default=None)
    ap.add_argument("--aspect", default=None)
    ap.add_argument("--pack", default=DEFAULTS["pack"], help="视觉包装包（默认 P1-warm，可选 P5-viral-pop 等）")
    ap.add_argument("--plan", default=DEFAULTS["plan"], help="剪辑骨架方案（默认 S1-conclude-fast）")
    ap.add_argument("--avoid", action="append", default=[], help="不许出现的内容，可多次")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    proj = lib / "_cutskills" / "projects" / a.slug
    proj.mkdir(parents=True, exist_ok=True)

    dur = a.duration or guess_duration(a.intent) or 45
    chosen = {**DEFAULTS, "pack": a.pack, "plan": a.plan}
    intent = {
        "raw": a.intent,
        "audience": a.audience,
        "promise": a.promise,
        "duration_s": dur,
        "aspect": a.aspect or guess_aspect(a.intent),
        "avoid": a.avoid,
        **chosen,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (proj / "intent.json").write_text(json.dumps(intent, ensure_ascii=False, indent=1), encoding="utf-8")

    if not (proj / "brief.json").exists():
        assumed = [k for k in ("audience", "promise") if not intent.get(k)]
        (proj / "brief.json").write_text(json.dumps({
            "slug": a.slug,
            "audience": a.audience or "对本话题感兴趣的短视频观众",
            "audience_present_state": "还不了解这里面的关键门道",
            "change": a.promise or a.intent[:60],
            "length_s": dur,
            "length_band": "30s" if dur <= 38 else "60-90s",
            "style_pack": chosen["style_pack"],
            "pack": chosen["pack"],
            "plan": chosen["plan"],
            "assumed": assumed,
            "created": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    if not (proj / "decisions.md").exists():
        (proj / "decisions.md").write_text(
            f"# 确认记录 · {a.slug}\n\n"
            "关键拍板留痕。一行一条，**追加，不改旧行**。\n"
            "写作用 `log_decision.py --project <项目> --said 原话 --did 动作`。\n\n"
            "| 时间 | 用户原话 | 做了什么 | 结论 |\n|---|---|---|---|\n",
            encoding="utf-8",
        )

    missing = [k for k in ("audience", "promise") if not a.__dict__.get(k)]
    print(f"项目 {proj}")
    if missing:
        print(f"已带默认假设初始化（可随时改 brief.json）：{', '.join(missing)}")
    else:
        print("意图与需求卡已就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
