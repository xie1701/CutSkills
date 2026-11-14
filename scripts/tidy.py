#!/usr/bin/env python3
"""收工清理：把「能重建的过程文件」和「必须留的交付物」分开，只删前者。

判据只有一条：**这个文件删掉之后，重跑哪一步能一模一样地拿回来？**
能答出来的才是可删项；答不出来的一律按真值源对待，不许动。

默认演练（只列表），`--apply` 才真删。

    python3 scripts/tidy.py --project <项目目录>            # 看这一片能回收多少
    python3 scripts/tidy.py --project <项目目录> --apply    # 真删
    python3 scripts/tidy.py --library <库根> [--apply]      # 扫库里全部项目
    python3 scripts/tidy.py --tmp [--apply]                 # 只清 /tmp 残骸

**留（KEEP）**

| 文件 | 为什么不能删 |
|---|---|
| `timeline.json` `brief.json` `intent.json` `plan.json` `thesis.json` `edl.json` | 真值源 |
| `decisions.md` `方案.md` `paper-edit.md` `bgm.json` `audio-map.json` | 决策留痕 |
| `approval.json` `lock.json` | 拍板与签锁，渲前闸门读它 |
| `verify-report.md` `cover/` `board/` `edl-history/` `plan-history/` | 验收报告与可追溯记录 |
| `out/film-hf.mp4`（或 `out/film.mp4`） | 成片 |
| `out/{subs,shots,cards,sfx}.json` | 验收与闸门读的就是这几份 |
| `hf/meta.json` `hf/index.html` `hf/assets/` | 渲器元数据与素材 |
| `hf/work/{faces,pstats,mask}` `hf/work/stab-*.json` | 叠加态/大字/压脸判据的证据 |
| `hf/clips/` `hf/clips/.pristine/` | 逐格渲染缓存 + shot_match 幂等基线 |
| 库根 `index.json` `lexicon.json` `l2s/` `cache/asr/` | 索引与词级真值，重跑 ASR 很贵 |

**删（DISPOSABLE）**

| 目标 | 重建方式 |
|---|---|
| `out/_hf_raw.mp4` `_hf_bed.mp4` `_hf_fg.mp4` | 重渲 |
| `out/film.mp4`（当 `film-hf.mp4` 存在时） | 旧 ffmpeg 链残留，留着会被 `verify_film` 按 mtime 误读 |
| `out/film-P*.mp4` | `render_style_preview.py` |
| `work/` | 重渲 |
| `hooks/*.mp4` `hooks/cand-*/` | `hooks.py`（`review-kit.md` 留） |
| `ab/*/` | `ab_open.py`（`compare.md` `plan-*.json` 留） |
| `review/` | `review_sheet.py` |
| `bgm-previews/` | 重渲时挑 BGM 即得 |
| 同库下的 `*-t042` 之类人工验证副本 | 交付已并入正片，副本即废 |
| `/tmp/cutskills-selftest-*` `/tmp/t0*` | 自检/调试残骸 |

**什么时候跑**：交付或判收之后、每轮重渲之后。跑完 `gate` 与 `verify_film` 再复核一遍，
确认删的都是中间物（这两步只读真值源与成片，删对了应当一条读数都不变）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

# 项目根下这些名字整份保留
KEEP_NAMES = {
    "timeline.json", "brief.json", "intent.json", "plan.json", "thesis.json", "edl.json",
    "decisions.md", "方案.md", "paper-edit.md", "bgm.json", "audio-map.json", "bgm.wav",
    "approval.json", "lock.json", "verify-report.md",
    "cover", "board", "edl-history", "plan-history", "out", "hf", "l2", "l2s",
    "index.json", "lexicon.json", "cache", "_cutskills", "subtitles",
}
# out/ 里这些整份保留
OUT_KEEP = {"subs.json", "shots.json", "cards.json", "sfx.json"}
# hf/ 里这些整份保留
HF_KEEP = {"meta.json", "index.html", "assets", "clips"}
# hf/work 里这些整份保留（闸门证据）
HFWORK_KEEP_PREFIX = ("faces", "pstats", "mask", "stab")
# hooks / ab 里这些留（决策留痕）
HOOKS_KEEP = {"review-kit.md"}
AB_KEEP = {"compare.md"}

PROCESS_DIRS = ("work", "review", "bgm-previews")
TMP_PATTERNS = (r"^cutskills-selftest", r"^t\d{3}(-|$)")   # 自检残骸 / 各单留下的 t0XX-backup 项目副本


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def plan_project(proj: Path) -> list[tuple[int, str, Path]]:
    """列出这个项目里可删的东西：(字节数, 人话, 路径)。"""
    rows: list[tuple[int, str, Path]] = []
    out, hf = proj / "out", proj / "hf"
    film_hf = out / "film-hf.mp4"

    for f in ("_hf_raw.mp4", "_hf_voc.mp4", "_hf_bed.mp4", "_hf_fg.mp4", "_hf_sfx.mp4"):
        q = out / f
        if q.exists():
            rows.append((_size(q), f"中间层 out/{f}（重渲即得）", q))
    if film_hf.exists():
        q = out / "film.mp4"
        if q.exists():
            rows.append((_size(q), "旧 ffmpeg 链残留 out/film.mp4（留着会被验片误读）", q))
    if out.is_dir():
        for q in sorted(out.glob("film-P*.mp4")):
            rows.append((_size(q), f"风格预览 out/{q.name}（render_style_preview 重出）", q))

    for d in PROCESS_DIRS:
        q = proj / d
        if q.is_dir():
            rows.append((_size(q), f"合成/审片过程目录 {d}/", q))

    hk = proj / "hooks"
    if hk.is_dir():
        for q in sorted(hk.iterdir()):
            if q.name in HOOKS_KEEP or q.name.startswith("."):
                continue
            rows.append((_size(q), f"钩子候选 hooks/{q.name}（hooks.py 重出）", q))

    ab = proj / "ab"
    if ab.is_dir():
        for q in sorted(ab.iterdir()):
            if q.name in AB_KEEP or q.name.startswith("plan-") or q.name.startswith("."):
                continue
            rows.append((_size(q), f"A/B 盲测 ab/{q.name}（ab_open.py 重出）", q))

    return rows


def plan_library(lib: Path) -> list[tuple[int, str, Path]]:
    rows: list[tuple[int, str, Path]] = []
    projects = lib / "_cutskills" / "projects"
    if not projects.is_dir():
        return rows
    names = [d for d in sorted(projects.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    real = {d.name for d in names}
    # 人工验证副本：同库下 <项目>-t042 之类，**正片已存在**才删（孤儿副本可能是唯一一份，不许动）
    copies: dict[str, Path] = {}
    for d in names:
        for suf in ("-t042", "-t041", "-copy", "-bak"):
            if d.name.endswith(suf) and d.name[: -len(suf)] in real:
                copies[d.name] = d
    for d in names:
        if d.name not in copies:      # 副本整目录删，不再往里逐项列（否则父子重复计数）
            rows += plan_project(d)
    for name, d in copies.items():
        rows.append((_size(d), f"验证副本 {name}/（正片已存在）", d))
    return rows


def plan_tmp() -> list[tuple[int, str, Path]]:
    rows: list[tuple[int, str, Path]] = []
    tmp = Path("/tmp")
    if not tmp.is_dir():
        return rows
    pats = [re.compile(x) for x in TMP_PATTERNS]
    for q in sorted(tmp.iterdir()):
        if not any(p.match(q.name) for p in pats):
            continue
        rows.append((_size(q), f"临时残骸 /tmp/{q.name}", q))
    return rows


def report(rows: list[tuple[int, str, Path]], apply: bool) -> int:
    if not rows:
        print("干净：没有可回收的过程文件。")
        return 0
    # 父子同时上榜时只留父级（整目录删已经覆盖子项，重复计入会把总账吹大）
    keep: list[tuple[int, str, Path]] = []
    for r in sorted(rows, key=lambda r: len(r[2].parts)):
        if any(k[2] == r[2] or k[2] in r[2].parents for k in keep):
            continue
        keep.append(r)
    rows = keep
    rows.sort(reverse=True)
    total = sum(r[0] for r in rows)
    for s, why, p in rows:
        print(f"{s / 1e6:9.1f}M  {why}")
    print(f"\n合计 {len(rows)} 项 · {total / 1e9:.2f} GB")
    if not apply:
        print("（演练，未删除；加 --apply 执行）")
        return 0
    failed = 0
    for s, why, p in rows:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            failed += 1
            print(f"删除失败 {p}：{e}")
    print(f"已回收 {total / 1e9:.2f} GB" + (f"（{failed} 项失败）" if failed else ""))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="删可重建的过程文件，留真值源与成片")
    ap.add_argument("--project", help="单个项目目录")
    ap.add_argument("--library", help="库根（含 _cutskills/projects）")
    ap.add_argument("--tmp", action="store_true", help="只清 /tmp 残骸")
    ap.add_argument("--apply", action="store_true", help="真删（默认演练）")
    a = ap.parse_args(argv)

    rows: list[tuple[int, str, Path]] = []
    if a.project:
        p = Path(a.project).expanduser()
        if not (p / "timeline.json").exists() and not (p / "out").exists():
            print(f"不像项目目录：{p}", file=sys.stderr)
            return 2
        rows += plan_project(p)
    if a.library:
        lib = Path(a.library).expanduser()
        if not lib.is_dir():
            print(f"库根不存在：{lib}", file=sys.stderr)
            return 2
        rows += plan_library(lib)
    if a.tmp or not (a.project or a.library):
        rows += plan_tmp()
    return report(rows, a.apply)


if __name__ == "__main__":
    sys.exit(main())
