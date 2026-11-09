#!/usr/bin/env python3
"""从 CC0 库里挑 BGM 和音效——不再自己合成。

Jerry 2026-11-09 17:37：「音效和BGM用现有的素材吧，不要自己生成了」。
两个库都带机器可读目录，比合成靠谱得多：每首 BGM 标了类目/时长/中文标签，
每条音效标了**人声下要降多少 dB**（`under_voiceover_db`）和**同步点在哪一秒**
（`sync_at_s`，意思是"这条音效里最该对上画面的那一点距文件开头多少秒"）。

有了这两个字段，混音就不该再拍脑袋：
    音效起始时间 = 画面时刻 − sync_at_s
    音效增益     = under_voiceover_db（+ 全局微调）
垫乐也一样：先按 -28 LUFS 归一，再让人声 12:1 压住它。

两个库的位置可以用环境变量盖掉：
    CUTSKILLS_BGM_LIB / CUTSKILLS_SFX_LIB
"""
from __future__ import annotations

import csv
import os
import random
from pathlib import Path

BGM_LIB = Path(os.environ.get("CUTSKILLS_BGM_LIB") or (Path.home() / "Downloads" / "cc0-bgm-library"))
SFX_LIB = Path(os.environ.get("CUTSKILLS_SFX_LIB") or (Path.home() / "Downloads" / "cc0-sfx-library"))

# 情绪 → 类目 / 中文标签。不是"我觉得好听"，是对着库里的选曲指南定的：
# 口播讲解要律动稳、没旋律抢注意力；治愈/希望要环境感；紧张要压迫。
MOODS: dict[str, dict] = {
    "clear": {          # 讲清楚一件事：稳、不抢
        "cats": ["activities", "jazzhop", "chillhop"],
        "tags": ["舒缓", "书写/阅读"],
        "why": "口播讲解：律动稳、无明显旋律",
    },
    "warm": {           # 手艺、现场、人情
        "cats": ["activities", "seasonal-weather"],
        "tags": ["暖光", "居家"],
        "why": "生活感与暖光，配手艺现场",
    },
    "hopeful": {        # 收尾、向好
        "cats": ["ambient", "seasonal-weather", "activities"],
        "tags": ["治愈", "清晨"],
        "why": "明亮但不喧哗，落在片尾",
    },
    "minimal": {        # 几乎听不见
        "cats": ["ambient"],
        "tags": ["氛围", "梦幻"],
        "why": "只托底，几乎察觉不到",
    },
    "tense": {          # 反差、揭穿
        "cats": ["hybrid", "cinematic"],
        "tags": ["悬念", "预告"],
        "why": "有压迫感，用在揭晓前",
    },
}

# 画面事件 → 音效。key 是我们 EDL 里的 `CARD_SFX` 名字与 gen 种类。
SFX_MAP: dict[str, str] = {
    "keyword": "pop-medium-in",
    "card": "pop-medium-in",
    "behind": "impact-light-firm",
    "cutout": "impact-light-firm",
    "idcard": "whoosh-medium-soft",
    "gen": "appear-materialise",
    "whoosh": "whoosh-medium-soft",
    "pop": "pop-medium-in",
    "impact": "impact-light-firm",
}

_cache: dict[str, list[dict]] = {}


def _rows(path: Path) -> list[dict]:
    key = str(path)
    if key not in _cache:
        with path.open(encoding="utf-8") as f:
            _cache[key] = list(csv.DictReader(f))
    return _cache[key]


def bgm_available() -> bool:
    return (BGM_LIB / "catalog.csv").exists()


def sfx_available() -> bool:
    return (SFX_LIB / "catalog.csv").exists()


def pick_bgm(mood: str = "clear", need_s: float = 60.0, seed: str = "") -> dict | None:
    """挑一首：类目对得上、时长够、没被同一批片子用过。

    返回 {path, title, dur, why}。挑不到就返回 None（调用方要能接受这种情况）。
    """
    if not bgm_available():
        return None
    spec = MOODS.get(mood) or MOODS["clear"]
    rows = _rows(BGM_LIB / "catalog.csv")
    cand = [r for r in rows
            if r["category"] in spec["cats"]
            and float(r["duration_s"] or 0) >= need_s + 5]
    if not cand:                       # 时长不够就放宽到「够用就行」
        cand = [r for r in rows if float(r["duration_s"] or 0) >= need_s]
    if not cand:
        return None
    tagged = [r for r in cand if any(t and t in (r.get("tags_zh") or "") for t in spec["tags"])]
    pool = tagged or cand
    rnd = random.Random(seed or f"{mood}-{int(need_s)}")
    r = rnd.choice(pool)
    return {"path": str(BGM_LIB / r["file"]), "title": r["title"], "dur": float(r["duration_s"]),
            "category": r["category_zh"], "why": spec["why"]}


def pick_sfx(name: str) -> dict | None:
    """按事件名取一条音效，带回它的同步点和人声下的增益。"""
    if not sfx_available():
        return None
    want = SFX_MAP.get(name, name)
    rows = _rows(SFX_LIB / "catalog.csv")
    hit = next((r for r in rows if r["id"] == want), None)
    if hit is None:                    # 兜底：同类里挑第一条
        fam = want.split("-")[0]
        hit = next((r for r in rows if r["id"].startswith(fam + "-")), None)
    if hit is None:
        return None
    return {"path": str(SFX_LIB / hit["file"]), "id": hit["id"],
            "dur": float(hit["duration_s"] or 0),
            "sync_at": float(hit["sync_at_s"] or 0),
            "gain_db": float(hit["under_voiceover_db"] or -6),
            "use": hit["use_zh"]}


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="看看这套库会挑出什么")
    ap.add_argument("--bgm", nargs="?", const="clear")
    ap.add_argument("--moods", action="store_true")
    ap.add_argument("--sfx")
    ap.add_argument("--dur", type=float, default=60.0)
    a = ap.parse_args()
    if a.moods:
        for m, s in MOODS.items():
            p = pick_bgm(m, a.dur, seed=m)
            print(f"{m:8} → {p['title'] if p else '（库里没有）'}")
            if p:
                print(f"           {p['category']} · {p['dur']:.0f}s · {p['why']}")
    elif a.sfx:
        print(json.dumps(pick_sfx(a.sfx), ensure_ascii=False, indent=1))
    elif a.bgm:
        print(json.dumps(pick_bgm(a.bgm, a.dur, seed=a.bgm), ensure_ascii=False, indent=1))
