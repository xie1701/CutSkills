#!/usr/bin/env python3
"""CutSkills · 阶段 B 起步：建一个剪辑项目。

生成 _cutskills/projects/<slug>/ 下的 intent.json、paper-edit.md、edl.json。
四个意图字段抽不全时，脚本只留空并报告缺哪几个 —— 由 agent 去问用户（最多两条）。

用法：
  init_project.py --library "<素材库>" --intent "<用户原话>" --slug qizao-01
                  [--audience ..] [--promise ..] [--duration 180] [--aspect 9:16]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

DEFAULTS = {
    "cut_source": "speech",
    "visual_selector": "single_cam_talking",
    "style_pack": "talking-head-v1",
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
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--intent", required=True, help="用户原话，别改写")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--audience", default=None)
    ap.add_argument("--promise", default=None, help="看完要让观众相信 / 做什么")
    ap.add_argument("--duration", type=int, default=None)
    ap.add_argument("--aspect", default=None)
    ap.add_argument("--avoid", action="append", default=[], help="不许出现的内容，可多次")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    proj = lib / "_cutskills" / "projects" / a.slug
    proj.mkdir(parents=True, exist_ok=True)

    intent = {
        "raw": a.intent,
        "audience": a.audience,
        "promise": a.promise,
        "duration_s": a.duration or guess_duration(a.intent),
        "aspect": a.aspect or guess_aspect(a.intent),
        "avoid": a.avoid,
        **DEFAULTS,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (proj / "intent.json").write_text(json.dumps(intent, ensure_ascii=False, indent=1))

    if not (proj / "edl.json").exists():
        (proj / "edl.json").write_text(json.dumps({
            "version": 1, "slug": a.slug,
            "created": datetime.now().isoformat(timespec="seconds"),
            "cut_source": intent["cut_source"],
            "visual_selector": intent["visual_selector"],
            "style_pack": intent["style_pack"],
            "segments": [],
        }, ensure_ascii=False, indent=1))

    if not (proj / "decisions.md").exists():
        (proj / "decisions.md").write_text(
            f"# 确认记录 · {a.slug}\n\n"
            "B 段唯一的人工确认点留痕。一行一条，**追加，不改旧行**。\n"
            "写作用 `log_decision.py --project <项目> --said 原话 --did 动作`。\n\n"
            "| 时间 | 用户原话 | 做了什么 | 结论 |\n|---|---|---|---|\n"
        )

    if not (proj / "paper-edit.md").exists():
        (proj / "paper-edit.md").write_text(
            f"# 纸面剪辑 · {a.slug}\n\n"
            f"**意图原文**：{a.intent}\n\n"
            f"**观众**：{intent['audience'] or '（待补）'} ｜ "
            f"**看完要相信/做什么**：{intent['promise'] or '（待补）'} ｜ "
            f"**时长**：{intent['duration_s'] or '（待补）'}s ｜ "
            f"**画幅**：{intent['aspect'] or '（待补）'}\n\n"
            "---\n\n"
            "填三列。**理由列不接受「过渡用」**——每条都要能回溯到一句具体的话或一个具体镜头。\n\n"
            "| # | 原文（素材内时间码） | 成片稿 | 保留理由 |\n|---|---|---|---|\n"
        )

    missing = [k for k in ("audience", "promise", "duration_s", "aspect") if not intent[k]]
    print(f"项目 {proj}")
    if missing:
        print(f"意图缺 {len(missing)} 项：{', '.join(missing)} —— 问用户，最多两条，问完带假设往下走")
    else:
        print("意图四项齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
