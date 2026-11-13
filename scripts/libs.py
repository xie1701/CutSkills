#!/usr/bin/env python3
"""库与项目的解析：影子和工程页两处入口共用这一份，不各写一套。"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LIBS = [
    "/Users/jerry/Downloads/ouyangTestCut",              # 欧阳 · 点赞茶楼 dianzan-a/b/c
    "/Users/jerry/Downloads/柒造Video/素材",              # 柒造 · qizao-*
    str(REPO / "references" / "packtest-2026-11-08"),    # 打包试验 proj79
]

# 项目名 → 一句话（名字是给人看的，缩写在库里，含义写在这儿）
PROJECT_NOTES = {
    "dianzan": "「点赞茶楼」· 珠海金湾 600㎡ 粤语口播（牛哥）",
    "qizao-ground": "柒造 · 地面施工（熊工讲进口 vs 国产的漆）",
    "qizao-protect": "柒造 · 保护与收边（边角细节怎么贴）",
    "qizao-naming": "柒造 · 「柒造」这名字的来历",
    "qizao-lime-baxis": "柒造 · B 轴试验片（代码画的连续场景 + 人物画中画，13.9s）",
    "proj79": "打包试验片 · 六个包装 token 的对照（79s）",
    "selftest": "自检用的合成素材片（不交付，验证链路没断）",
}


def _pick(text: str) -> str:
    """按项目名前缀找一句人话说明。"""
    for key in sorted(PROJECT_NOTES, key=len, reverse=True):
        if text.startswith(key):
            return PROJECT_NOTES[key]
    return ""


def find_projects(libs: list[str]) -> list[Path]:
    out: list[Path] = []
    for lib in libs:
        base = Path(lib).expanduser()
        if not base.is_dir():
            continue
        cand = base / "_cutskills" / "projects"
        if cand.is_dir():
            out += [d for d in sorted(cand.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        if (base / "timeline.json").exists() or (base / "out").is_dir():
            out.append(base)
        else:
            for sub in sorted(base.iterdir()):
                if sub.is_dir() and (sub / "out").is_dir() and (sub / "timeline.json").exists():
                    out.append(sub)
    # 同一项目可能被两个入口扫到
    seen, uniq = set(), []
    for p in out:
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        uniq.append(p)
    return uniq


