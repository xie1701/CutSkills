#!/usr/bin/env python3
"""`tidy.py` 负样本：它必须删掉可重建的过程文件，且**一个真值源都不许碰**。

清错东西的代价比多占几 G 磁盘高得多——成片没了要重渲、`timeline.json` 没了要重剪、
`hf/clips/.pristine` 没了下次渲会把调色叠第二遍。所以这条测试按「先造一个什么都有的假项目，
再让 tidy 真删，最后逐项断言谁死谁活」来写。

    python3 scripts/test_tidy.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIDY = ROOT / "scripts" / "tidy.py"

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def mk(p: Path, size: int = 1024) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * size)


# 这些是「删了能一模一样拿回来」的
DISPOSABLE = [
    "out/_hf_raw.mp4", "out/_hf_bed.mp4", "out/_hf_fg.mp4",
    "out/film.mp4",                       # 有 film-hf.mp4 时的旧链残留
    "out/film-P2-paper.mp4",              # 风格预览
    "work/video.mp4", "work/audio.wav",
    "hooks/A-结论前置.mp4", "hooks/cand-A/seg.mp4",
    "ab/A/plan.mp4", "ab/blind/x.mp4",
    "review/frames/0.jpg", "review/sheet.html",
    "bgm-previews/track1.wav",
]
# 这些一个字都不许动
KEEP = [
    "timeline.json", "brief.json", "intent.json", "plan.json", "decisions.md", "方案.md",
    "approval.json", "lock.json", "verify-report.md",
    "out/film-hf.mp4",
    "out/subs.json", "out/shots.json", "out/cards.json", "out/sfx.json",
    "hf/meta.json", "hf/index.html", "hf/assets/fonts/x.ttf",
    "hf/work/faces/c0.json", "hf/work/pstats/c0/m0.png", "hf/work/mask/c0/a.png",
    "hf/work/stab-abc.json",
    "hf/clips/seg-0.mp4", "hf/clips/.pristine/seg-0.mp4",
    "hooks/review-kit.md", "ab/compare.md", "ab/plan-A.json",
    "cover/brief.json", "board/index.html", "edl-history/v1.json",
]


def case_project() -> None:
    print("① 单项目：过程文件清掉，真值源与成片、闸门证据一个不动")
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        for rel in DISPOSABLE + KEEP:
            mk(proj / rel)
        r = subprocess.run([sys.executable, str(TIDY), "--project", str(proj), "--apply"],
                           capture_output=True, text=True)
        must("tidy 退出码 0", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])
        alive = [rel for rel in KEEP if (proj / rel).exists()]
        dead = [rel for rel in DISPOSABLE if not (proj / rel).exists()]
        must(f"留下的 {len(KEEP)} 项全在", len(alive) == len(KEEP),
             "丢了：" + "、".join(x for x in KEEP if x not in alive))
        must(f"可删的 {len(DISPOSABLE)} 项全清", len(dead) == len(DISPOSABLE),
             "还在：" + "、".join(x for x in DISPOSABLE if x not in dead))
        # 判据里最贵的一条：没有 film-hf 时，out/film.mp4 就是成片，不许删
        proj2 = Path(td) / "proj2"
        mk(proj2 / "out/film.mp4")
        mk(proj2 / "timeline.json")
        subprocess.run([sys.executable, str(TIDY), "--project", str(proj2), "--apply"],
                       capture_output=True, text=True)
        must("没有 film-hf.mp4 时 out/film.mp4 是成片，必须留", (proj2 / "out/film.mp4").exists())


def case_dry() -> None:
    print("② 默认演练：不写盘")
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        mk(proj / "timeline.json")
        mk(proj / "out/_hf_raw.mp4", 4096)
        r = subprocess.run([sys.executable, str(TIDY), "--project", str(proj)],
                           capture_output=True, text=True)
        must("加了 --apply 才删", r.returncode == 0 and (proj / "out/_hf_raw.mp4").exists(),
             (r.stdout or "").strip()[-160:])
        must("演练有读数", "GB" in r.stdout or "M" in r.stdout, (r.stdout or "").strip()[-160:])


def case_library() -> None:
    print("③ 整库：验证副本按「正片尚在」才删，孤儿副本不敢动")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        pr = lib / "_cutskills" / "projects"
        mk(lib / "_cutskills" / "index.json", 64)
        for name in ("film-a", "film-a-t042", "film-b-copy"):
            mk(pr / name / "timeline.json")
            mk(pr / name / "out" / "film-hf.mp4")
            mk(pr / name / "out" / "_hf_raw.mp4")
        r = subprocess.run([sys.executable, str(TIDY), "--library", str(lib), "--apply"],
                           capture_output=True, text=True)
        must("整库扫能跑", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])
        must("正片还在", (pr / "film-a" / "out" / "film-hf.mp4").exists())
        must("正片尚在 → t042 副本删掉", not (pr / "film-a-t042").exists())
        must("没有正片的孤儿副本不许删（可能是唯一一份）", (pr / "film-b-copy").exists())
        must("正片的中间层照常清", not (pr / "film-a" / "out" / "_hf_raw.mp4").exists())


def case_report_shape() -> None:
    print("④ 报告的每一条都要说清「删了什么、怎么重建」")
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        mk(proj / "timeline.json")
        mk(proj / "out/_hf_raw.mp4")
        mk(proj / "work/v.mp4")
        out = subprocess.run([sys.executable, str(TIDY), "--project", str(proj)],
                             capture_output=True, text=True).stdout
        must("报告带重建方式", "重渲" in out, out.strip()[-200:])
        must("报告带总计", "合计" in out, out.strip()[-200:])


def main() -> int:
    case_project()
    case_dry()
    case_library()
    case_report_shape()
    print()
    if fails:
        print(f"❌ {len(fails)} 条没过：" + "、".join(fails))
        return 1
    print("✅ tidy 负样本 4 组全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
