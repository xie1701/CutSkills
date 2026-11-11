#!/usr/bin/env python3
"""T-010 回归：clips.ov 对账不许被 overlay 推歪（主轨起点只看视频格）。

上一轮复核在 qizao-ground-01 上抓到：board_check 算镜起点时把 overlay 的 dur
也加进了线性时间，overlay 之后的镜起点会漂移。这里用一个最小项目锁死三件事：

  A. 两个视频格中间夹一块 overlay，第二格挂 clips.ov → 必须绿；
  B. 把 clips.ov 挪 0.2s → 必须红（负样本，防判据被改松）；
  C. 视频格缺 `n` → 缺省镜号按「第几个视频格」补，仍必须绿
     （与 board_from_timeline.enumerate(video_rows, 1) 对齐）。

用法：python3 scripts/test_board_clips.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD_CHECK = ROOT / "scripts" / "board_check.py"


def board_obj(shift: float = 0.0) -> dict:
    return {
        "shots": [
            {"id": "S01", "ch": "c0", "mode": "A", "vo": "第一句口播", "fixed": 5.0, "gap": 0},
            {"id": "S02", "ch": "c0", "mode": "A", "vo": "第二句口播", "fixed": 5.0, "gap": 0,
             "clips": {"ov": [round(1.0 + shift, 3), 2.0]}},
        ],
        "chapters": [{"id": "c0", "name": "全片"}],
        "modes": ["A"],
        "styles": [{"id": "s1", "cn": "测试"}],
        "canvas": [{"style": "s1"}],
        "meta": {"fps": 30, "ar": "9:16", "res": "1080x1920", "project": "clips-fixture"},
    }


def tl_obj(missing_n: bool = False) -> dict:
    second = {"n": 2, "kind": "clip", "track": "video", "asset": "b",
              "in": 0.0, "out": 5.0, "medium": "talking"}
    if missing_n:
        second.pop("n")
    return {
        "version": 4,
        "slug": "clips-fixture",
        "duration_s": 10.0,
        "elements": [
            {"n": 1, "kind": "clip", "track": "video", "asset": "a",
             "in": 0.0, "out": 5.0, "medium": "talking"},
            # overlay 夹在主轨中间：它不在线性时间上，dur 不许累加
            {"n": 15, "kind": "gen", "track": "overlay", "at": 6.0, "dur": 2.0,
             "template": "keyword", "props": {"text": "叠加卡"}},
            second,
        ],
    }


def write_case(proj: Path, board: dict, tl: dict) -> None:
    (proj / "board").mkdir(parents=True, exist_ok=True)
    (proj / "board" / "board.js").write_text(
        "const BOARD = " + json.dumps(board, ensure_ascii=False) + ";\n", encoding="utf-8")
    (proj / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")


def run(proj: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BOARD_CHECK), "--project", str(proj)],
                          capture_output=True, text=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="board_clips_"))
    proj = tmp / "proj"
    try:
        # A. overlay 之后的镜起点必须还是 5.0s（不是 7.0s），clips.ov 对得上
        write_case(proj, board_obj(), tl_obj())
        r = run(proj)
        assert r.returncode == 0, f"A 应绿，实际退出 {r.returncode}\n{r.stdout}\n{r.stderr}"
        assert "clips.ov 与时间轴叠加层对账" in r.stdout, f"A 缺对账项\n{r.stdout}"
        assert "1 块投影 = 1 个叠加层" in r.stdout, f"A 对账读数不对\n{r.stdout}"

        # B. 负样本：clips.ov 挪 0.2s，必须红，且读数写明 S02 要 6.20s
        write_case(proj, board_obj(shift=0.2), tl_obj())
        r = run(proj)
        assert r.returncode == 1, f"B 应红，实际退出 {r.returncode}\n{r.stdout}"
        assert "对不上" in r.stdout, f"B 没有报对不上\n{r.stdout}"
        assert "S02 要 6.20s" in r.stdout, f"B 读数不对（起点是否又被 overlay 推歪？）\n{r.stdout}"

        # C. 缺省 n：第二格没有 n，按视频序号补成 S02，必须仍能对上
        write_case(proj, board_obj(), tl_obj(missing_n=True))
        r = run(proj)
        assert r.returncode == 0, f"C 应绿，实际退出 {r.returncode}\n{r.stdout}\n{r.stderr}"
        assert "1 块投影 = 1 个叠加层" in r.stdout, f"C 对账读数不对\n{r.stdout}"

        print("✅ clips.ov 对账回归通过：overlay 不推歪主轨起点；漂移判红；缺省镜号对齐")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
