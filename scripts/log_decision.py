#!/usr/bin/env python3
"""CutSkills · 确认点留痕：把「人说了什么 → 我们做了什么 → 结果」记进 decisions.md。

B 段只有一个人工确认点（读文字稿）。以前这件事除了聊天记录没有任何落盘证据：
一周后没人知道当时是「没提这条」还是「提了没改」，也没法统计「人改稿的频率和方向」。

一行一条，追加，不改旧行：

  log_decision.py --project <项目目录> --said "第 3 条删掉，开头有点绕" \
      --did "删 #3（熊工口播3 03:51–03:57），#4 升为开场" --verdict accepted

用法：
  log_decision.py --project P --said 原话 --did 动作 [--verdict accepted|rejected|pending]
  log_decision.py --project P --show
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HEADER = """# 确认记录 · {slug}

B 段唯一的人工确认点留痕。一行一条，**追加，不改旧行**——这份文件的价值在于当时到底说了什么，
事后觉得当时说得不好，就再加一行，不要回去改写历史。

| 时间 | 用户原话 | 做了什么 | 结论 |
|---|---|---|---|
"""


def load_rows(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("| 20")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--said", default=None, help="用户原话，别改写")
    ap.add_argument("--did", default=None, help="据此改了什么（段号 + 动作）")
    ap.add_argument("--verdict", default="accepted", choices=["accepted", "rejected", "pending"])
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    f = proj / "decisions.md"

    if a.show:
        print(f.read_text() if f.exists() else "还没有确认记录")
        return 0

    if not a.said or not a.did:
        print("--said 和 --did 都要给（--show 除外）", file=sys.stderr)
        return 2

    if not f.exists():
        f.write_text(HEADER.format(slug=proj.name))

    # 结论前面挂上当时的片子状态：段数 / 时长，以后回看才知道那条决定影响多大
    shape = ""
    edl_f = proj / "edl.json"
    if edl_f.exists():
        try:
            segs = json.loads(edl_f.read_text()).get("segments") or []
            if segs:
                total = sum(s["out"] - s["in"] for s in segs)
                shape = f"<sub>{len(segs)} 段 / {total:.1f}s</sub>"
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    said = a.said.replace("|", "／").replace("\n", " ").strip()
    did = a.did.replace("|", "／").replace("\n", " ").strip()
    verdict = {"accepted": "✅ 接受", "rejected": "❌ 推翻", "pending": "⏳ 待定"}[a.verdict]
    cell = f"{verdict} {shape}".strip()
    row = f"| {datetime.now().strftime('%Y-%m-%d %H:%M')} | {said} | {did} | {cell} |"

    text = f.read_text()
    if not text.endswith("\n"):
        text += "\n"
    f.write_text(text + row + "\n")
    print(f"已记入 {f}（第 {len(load_rows(f.read_text()))} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
