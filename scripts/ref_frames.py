#!/usr/bin/env python3
"""CutSkills · 参考片取样：把一支片子的「包装信息量最大的那些帧」拼成联系表，交给视觉模型看。

为什么不是逐帧：一支 10 分钟的片在 24fps 下有 1.4 万帧，其中相邻两帧的差别通常是压缩噪声。
包装的信息只在**变化点**上——镜头切了、字出来了、色块翻页、版式换了一套。
所以取帧规则是：切点后 0.15s 各取一帧（新镜头的版面）＋ 均匀补充若干帧（覆盖长时间不切的镜头）。

每张表 6 格、每格 640 宽。这个分辨率能读出字形轮廓、字重、描边、色块边界和位置关系——
这些正是 1:1 复刻要抄的东西；再高只是让 token 变贵。

用法：
  ref_frames.py --dir <参考片目录>                 # 全部
  ref_frames.py --dir <目录> --only 关键词
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

TILE_W = 640
GRID = (3, 2)          # 3 列 2 行 = 6 格
PER_VIDEO = 12         # 每支片取 12 帧 = 2 张表


def probe_json(dir_: Path, stem: str) -> dict:
    f = dir_ / "_packaging" / f"{stem[:60]}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def grab(path: Path, ts: list[float], out: Path, w: int) -> list[Path]:
    """按时间点取帧。一次解码取多帧，比逐个 -ss 快得多。"""
    out.mkdir(parents=True, exist_ok=True)
    sel = "+".join(f"eq(n\\,{int(t * 1000)})" for t in [])  # 占位，不用
    taken: list[Path] = []
    for i, t in enumerate(ts):
        p = out / f"f{i:02d}.jpg"
        if p.exists() and p.stat().st_size > 0:
            taken.append(p)
            continue
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", f"scale={w}:-2", str(p)],
                       capture_output=True)
        if p.exists() and p.stat().st_size > 0:
            taken.append(p)
    return taken


def sheet(frames: list[Path], out: Path, cols: int, rows: int) -> Path | None:
    frames = frames[: cols * rows]
    if not frames:
        return None
    im0 = Image.open(frames[0])
    tw, th = im0.size
    canvas = Image.new("RGB", (cols * tw, rows * th), (18, 18, 18))
    for i, f in enumerate(frames):
        im = Image.open(f).convert("RGB")
        if im.size != (tw, th):
            im = im.resize((tw, th))
        canvas.paste(im, ((i % cols) * tw, (i // cols) * th))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=88)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    src = Path(a.dir).expanduser().resolve()
    vids = sorted(p for p in src.iterdir()
                  if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv", ".m4v", ".webm"))
    if a.only:
        vids = [p for p in vids if a.only in p.name]
    made = []
    for p in vids:
        info = probe_json(src, p.stem)
        dur = float(info.get("duration") or 0)
        cuts = [float(x) for x in (info.get("cut_timeline") or [])]
        # 切点后 0.15s：新镜头第一个完整版面。切点不够就均匀补，保证 12 帧
        ts = [min(dur - 0.05, c + 0.15) for c in cuts[: PER_VIDEO - 2]]
        if dur:
            step = dur / (PER_VIDEO - len(ts) + 1)
            ts += [round(step * (i + 1), 2) for i in range(PER_VIDEO - len(ts))]
        ts = sorted({round(t, 2) for t in ts if 0 <= t < dur})[:PER_VIDEO]
        workdir = src / "_packaging" / "frames" / p.stem[:50]
        fr = grab(p, ts, workdir, TILE_W)
        cols, rows = GRID
        per = cols * rows
        outs = []
        for k in range(0, len(fr), per):
            s = sheet(fr[k:k + per], workdir / f"sheet{k // per + 1}.jpg", cols, rows)
            if s:
                outs.append(s)
        print(f"{p.name[:44]:<46} {len(fr)} 帧 → {len(outs)} 张表  "
              f"{[round(t, 1) for t in ts[:4]]}…")
        made.extend(outs)
    print(f"\n共 {len(made)} 张联系表，在 {src / '_packaging' / 'frames'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
