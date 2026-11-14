#!/usr/bin/env python3
"""从 CC0 库挑 BGM 和音效——外置全量库优先，未配置时自动退回随仓迷你包。

设计原则：
1. 音效起始时间 = 画面时刻 − sync_at_s（打击峰值精准踩在切点/动作点上）
2. 音效增益 = under_voiceover_db（人声下合理退让）
3. BGM 先按 -28 LUFS 归一，再由人声 sidechain 压住；时长不够时由渲染器无缝循环
4. 跨电脑零配置可用：没有配外置库时自动回退到仓库自带的 `assets/audio/{bgm,sfx}`（CC0 1.0）
"""
from __future__ import annotations

import csv
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libs import REPO, load_config  # noqa: E402

BUILTIN_BGM = REPO / "assets" / "audio" / "bgm"
BUILTIN_SFX = REPO / "assets" / "audio" / "sfx"


def _resolve_audio_lib(env_key: str, cfg_key: str, builtin: Path) -> Path:
    cfg = load_config()
    raw = (os.environ.get(env_key) or str(cfg.get(cfg_key) or "")).strip()
    if raw:
        cand = Path(raw).expanduser()
        if (cand / "catalog.csv").exists():
            return cand
    return builtin


BGM_LIB = _resolve_audio_lib("CUTSKILLS_BGM_LIB", "bgm_lib", BUILTIN_BGM)
SFX_LIB = _resolve_audio_lib("CUTSKILLS_SFX_LIB", "sfx_lib", BUILTIN_SFX)

LICENCE = "CC0 1.0 Universal"
LICENCE_PROOF = {"bgm": "SOURCES.md", "sfx": "LICENSE-CC0.txt"}


def licence_of(path: str | Path) -> str | None:
    """给一个素材文件路径，返回它的许可口径；查不到许可依据的返回 None（未核实）。

    目录位置本身不构成许可证据（评审 P0-06）：库根必须有许可留痕文件
    （LICENSE-CC0.txt 或 SOURCES.md）才按 CC0 认定，否则返回 None，
    sfx_manifest.json 里会如实标成未核实而不是 CC0。
    """
    p = Path(path)
    proofs = ("LICENSE-CC0.txt", "SOURCES.md")
    try:
        rp = p.resolve()
        for root in (BGM_LIB, SFX_LIB, BUILTIN_BGM, BUILTIN_SFX):
            if root.exists() and rp.is_relative_to(root.resolve()):
                return LICENCE if any((root / f).exists() for f in proofs) else None
    except OSError:
        pass
    return None


MOODS: dict[str, dict] = {
    "upbeat": {
        "cats": ["funk-soul", "chillhop"],
        "tags": ["律动", "活力", "轻快"],
        "why": "强网感/漏斗流破圈：律动鲜明、适合卡点快切",
    },
    "warm": {
        "cats": ["activities", "seasonal-weather"],
        "tags": ["暖光", "居家"],
        "why": "烟火人情与手艺探店：温暖松弛、拉近信任",
    },
    "clear": {
        "cats": ["activities", "jazzhop", "chillhop"],
        "tags": ["舒缓", "书写/阅读"],
        "why": "知识科普与老板口播：节奏稳、不抢台词",
    },
    "hopeful": {
        "cats": ["asian-lofi", "ambient-lofi", "seasonal-weather", "activities"],
        "tags": ["治愈", "清晨", "雨"],
        "why": "故事升华与真诚收尾：明亮治愈、不喧哗",
    },
    "tense": {
        "cats": ["hybrid", "late-night", "跨曲风"],
        "tags": ["悬念", "深夜", "预告"],
        "why": "行业揭秘与避坑反转：有悬念张力",
    },
    "minimal": {
        "cats": ["ambient-lofi", "late-night"],
        "tags": ["氛围", "梦幻"],
        "why": "极简纪实托底：几乎不察觉旋律",
    },
}

SFX_MAP: dict[str, str] = {
    # 二十个别名全部精确命中内置 catalog.csv（评审 P1-04：曾育 9 个目标 id
    # 不在内置 24 条里，靠同族回退搪塞——回退声音未必符合原意）。
    "keyword": "pop-medium-in",
    "card": "pop-medium-in",
    "title": "whoosh-medium-soft",
    "sticker": "pop-medium-in",
    "behind": "impact-light-firm",
    "cutout": "impact-light-firm",
    "idcard": "whoosh-medium-soft",
    "gen": "appear-materialise",
    "whoosh": "whoosh-medium-soft",
    "swish": "swish-medium",
    "pop": "pop-medium-in",
    "impact": "impact-light-firm",
    "punch": "boom-hit-soft",
    "flash": "whoosh-medium-hard",
    "riser": "riser-1s-soft",
    "ding": "notification-alert-soft",
    "shimmer": "shimmer-short-soft",
    "money": "money-card-tap",
    "click": "click-clap-soft",
    "celebration": "celebration-popper",
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
    """按情绪挑一首配乐；若单曲短于 need_s 仍可返回（渲染层支持无缝循环）。"""
    if not bgm_available():
        return None
    spec = MOODS.get(mood) or MOODS["clear"]
    rows = _rows(BGM_LIB / "catalog.csv")
    cand = [r for r in rows
            if r["category"] in spec["cats"]
            and float(r["duration_s"] or 0) >= need_s + 5]
    if not cand:
        cand = [r for r in rows if r["category"] in spec["cats"]]
    if not cand:
        cand = rows
    if not cand:
        return None
    tagged = [r for r in cand if any(t and t in (r.get("tags_zh") or "") for t in spec["tags"])]
    pool = tagged or cand
    rnd = random.Random(seed or f"{mood}-{int(need_s)}")
    r = rnd.choice(pool)
    return {
        "path": str(BGM_LIB / r["file"]),
        "title": r["title"],
        "dur": float(r["duration_s"] or 60.0),
        "category": r.get("category_zh") or r.get("category") or "",
        "why": spec["why"],
        "licence": LICENCE,
    }


def pick_sfx(name: str, nth: int = 0) -> dict | None:
    """按事件名取一条音效，支持 nth 同家族轮换防听觉疲劳。"""
    if not sfx_available():
        return None
    want = SFX_MAP.get(name, name)
    rows = _rows(SFX_LIB / "catalog.csv")
    fam = want.split("-")[0]
    if nth and nth > 0:
        pool = [r for r in rows if r["id"].startswith(fam + "-") or r["id"] == fam]
        if not pool:
            pool = [r for r in rows if r["id"].startswith("pop-")] or rows
        hit = pool[nth % len(pool)] if pool else None
    else:
        hit = next((r for r in rows if r["id"] == want), None)
        if hit is None:
            hit = next((r for r in rows if r["id"].startswith(fam + "-") or r["id"] == fam), None)
        if hit is None:
            hit = next((r for r in rows if r["id"].startswith("pop-")), None)
    if hit is None:
        return None
    return {
        "path": str(SFX_LIB / hit["file"]),
        "id": hit["id"],
        "dur": float(hit["duration_s"] or 0),
        "sync_at": float(hit["sync_at_s"] or 0),
        "gain_db": float(hit["under_voiceover_db"] or -6),
        "use": hit.get("use_zh") or "",
        "licence": LICENCE,
    }


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="预览 CC0 音乐与音效匹配结果")
    ap.add_argument("--bgm", nargs="?", const="upbeat")
    ap.add_argument("--moods", action="store_true")
    ap.add_argument("--sfx")
    ap.add_argument("--dur", type=float, default=45.0)
    a = ap.parse_args()
    if a.moods:
        for m in MOODS:
            p = pick_bgm(m, a.dur, seed=m)
            print(f"{m:8} → {p['title'] if p else '（空）'} ({p['category'] if p else ''})")
    elif a.sfx:
        print(json.dumps(pick_sfx(a.sfx), ensure_ascii=False, indent=1))
    elif a.bgm:
        print(json.dumps(pick_bgm(a.bgm, a.dur, seed=a.bgm), ensure_ascii=False, indent=1))
