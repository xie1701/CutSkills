#!/usr/bin/env python3
"""帧差剖面：一段画面到底有没有在动、有没有卡住。

为什么要有它：`--probe` 的 `luma` / `bright%` 只回答「有没有东西」，
不回答「东西有没有在变」。一张静止但配色好看的画面能全绿通过，
然后观众看到的就是「一张图放了 4 秒」——代码画的场景最容易犯这个错。

三个读数（判据与含义见 references/scene-art-standard.md）：

  动帧占比   帧差 > 噪声底的帧 ÷ 总帧            ≥ 0.85
  最长静止   连续静止帧数 × 步长 的极大值          < 1.2s
  帧差中位   帧差中位数（区分度）                 1.2 – 6.0

静止帧的阈值不写死成绝对数，而是与该场景自己的噪声底比：
    still = diff < max(FLOOR, median × 0.25)
纯色背景、慢节奏场景的帧差天然只有 1–2，用绝对阈值会恒报「一直在动」。

用法：
    python3 scripts/measure_motion.py --video <场景.mp4> [--fps 10] [--json m.json]
    python3 scripts/measure_motion.py --video <片.mp4> --from 64.3 --dur 4.0

退出码：0 = 三条判据都过；1 = 至少一条不过（读数照样完整打印）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

DEF_FPS = 10
FLOOR = 0.8            # 帧差的绝对地板（编码噪声常年在 0.5–1.0 灰度级）
REL = 0.25             # 静止判据相对噪声底的倍数
STILL_MAX_S = 1.2      # 最长静止
MOVING_MIN = 0.85      # 动帧占比下限
MED_MIN, MED_MAX = 1.2, 6.0
SMALL = (270, 480)     # 分析尺寸：够看动静，又不吃内存


def ffmpeg() -> str:
    import os
    return os.environ.get("CUTSKILLS_FFMPEG") or "ffmpeg"


def frames(path: Path, fps: float, t0: float | None, dur: float | None,
           size=SMALL) -> np.ndarray:
    """抽灰度小图序列，返回 (n, h, w) float32。"""
    cmd = [ffmpeg(), "-v", "error", "-nostdin"]
    if t0 is not None:
        cmd += ["-ss", f"{t0:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    w, h = size
    cmd += ["-i", str(path), "-vf",
            f"fps={fps},scale={w}:{h}:flags=bilinear,format=gray",
            "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace")[-800:])
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    step = w * h
    n = len(buf) // step
    if n < 2:
        raise RuntimeError(f"抽到的帧不足（{n} 帧）")
    return buf[: n * step].reshape(n, h, w).astype(np.float32)


def profile(arr: np.ndarray, fps: float = DEF_FPS) -> dict:
    """帧差剖面 → 三个读数和逐帧数组。"""
    d = np.abs(np.diff(arr, axis=0))                 # (n-1, h, w)
    per = d.reshape(d.shape[0], -1).mean(axis=1)     # 每对相邻帧的平均绝对差
    med = float(np.median(per))
    still_cut = max(FLOOR, med * REL)
    moving = per > still_cut
    # 最长连续静止（按帧数 → 秒）
    longest = cur = 0
    for mv in moving:
        cur = 0 if mv else cur + 1
        longest = max(longest, cur)
    return {
        "frames": int(arr.shape[0]),
        "diffs": [round(float(x), 3) for x in per],
        "median": round(med, 3),
        "moving_ratio": round(float(moving.mean()), 3),
        "longest_still_s": round(longest / fps, 2),
        "still_cut": round(still_cut, 3),
    }


def judge(prof: dict, med_band: tuple[float, float] = (MED_MIN, MED_MAX)) -> list[tuple[str, bool, str]]:
    checks = []
    checks.append(("动帧占比", prof["moving_ratio"] >= MOVING_MIN,
                   f"{prof['moving_ratio']:.2f}（≥{MOVING_MIN}）"))
    checks.append(("最长静止", prof["longest_still_s"] < STILL_MAX_S,
                   f"{prof['longest_still_s']:.2f}s（<{STILL_MAX_S}s）"))
    ok_med = med_band[0] <= prof["median"] <= med_band[1]
    checks.append(("帧差中位", ok_med,
                   f"{prof['median']:.2f}（{med_band[0]}–{med_band[1]}）"))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description="帧差剖面：动帧占比 / 最长静止 / 帧差中位")
    ap.add_argument("--video", required=True)
    ap.add_argument("--fps", type=float, default=DEF_FPS)
    ap.add_argument("--from", dest="t0", type=float, default=None)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--json", dest="js", default=None)
    ap.add_argument("--median-band", default=None,
                    help="帧差中位的合格区间 y0:y1。代码场景默认 1.2:6.0；"
                         "实拍/手持段天然更大（是镜头在动，不是问题），给 6:20 之类")
    a = ap.parse_args()

    v = Path(a.video).expanduser()
    if not v.exists():
        print(f"找不到视频：{v}")
        return 2

    arr = frames(v, a.fps, a.t0, a.dur)
    prof = profile(arr, a.fps)
    prof["fps"] = a.fps
    prof["video"] = str(v)

    print(f"帧差剖面：{v.name} · {prof['frames']} 帧 @ {a.fps:g}fps")
    bad = 0
    band = tuple(float(v) for v in a.median_band.split(":")) if a.median_band else (MED_MIN, MED_MAX)
    for name, ok, detail in judge(prof, band):
        print(f"  {'✅' if ok else '🔴'} {name} {detail}")
        bad += 0 if ok else 1
    # 逐秒剖面，方便看出是哪一段站桩
    per_s = max(1, int(round(a.fps)))
    line = []
    for i in range(0, len(prof["diffs"]), per_s):
        seg = prof["diffs"][i: i + per_s]
        line.append(f"{sum(seg) / len(seg):.1f}")
    print("  每秒帧差： " + " ".join(line))

    if a.js:
        Path(a.js).write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {a.js}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
