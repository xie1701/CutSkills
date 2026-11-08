#!/usr/bin/env python3
"""两条片子的影调对比：luma / G÷R / B÷R，不拿饱和度当指标。

为什么单列这三个（2026-11-09 定）：
- **luma**：整体明暗，参考片的"高级感"一半来自它（0.381 而不是 0.52）。
- **G÷R**：偏色轴上是绿还是在红。参考片 0.753。
- **B÷R**：冷暖。参考片 0.483，比我们暖很多——这个数才是"调色差在哪"的坐标。

**饱和度不进决策。** 参考片的饱和度是带大字、色卡、品牌色的整片均值（0.511），
我们的纯画面只有 0.13 起步；两个口径不同，按它调只会把画面拉成霓虹。

用法：
    python3 scripts/compare_tones.py --a out/film-hf.mp4 --b 参考片.mp4
    python3 scripts/compare_tones.py --a A --b B --fps 2 --size 270x480 --md out.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

# Rec.709 亮度系数（跟 ffmpeg 的 eq / colorlevels 同一套口径）
LW = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def frames(path: Path, fps: float, w: int, h: int) -> np.ndarray:
    """抽帧成 (N, h, w, 3) uint8。走管道，不落盘。"""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path),
           "-vf", f"fps={fps},scale={w}:{h}:flags=area",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    if n == 0:
        raise SystemExit(f"抽不到帧：{path}")
    return np.frombuffer(raw[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)


def metrics(arr: np.ndarray) -> dict:
    """逐帧算，再给分布。ratio 用通道均值之比，不用逐像素比（会爆）。"""
    f = arr.astype(np.float32) / 255.0
    luma = (f @ LW).mean(axis=(1, 2))                 # (N,)
    mr = f[..., 0].mean(axis=(1, 2))
    mg = f[..., 1].mean(axis=(1, 2))
    mb = f[..., 2].mean(axis=(1, 2))
    gr = mg / np.maximum(mr, 1e-6)
    br = mb / np.maximum(mr, 1e-6)
    sat = (f.max(axis=3) - f.min(axis=3)).mean(axis=(1, 2))
    q = lambda v: {"mean": round(float(v.mean()), 4),
                   "median": round(float(np.median(v)), 4),
                   "p10": round(float(np.percentile(v, 10)), 4),
                   "p90": round(float(np.percentile(v, 90)), 4)}
    out = {"frames": int(len(luma)), "luma": q(luma), "G/R": q(gr), "B/R": q(br),
           "sat": q(sat)}
    # 三段（开场 / 中段 / 收尾）各给一份 luma，用来抓"某一段特别亮"
    k = max(1, len(luma) // 3)
    out["luma_by_third"] = [round(float(x.mean()), 4)
                            for x in (luma[:k], luma[k:2 * k], luma[2 * k:])]
    return out


def verdict(a: dict, b: dict) -> list[str]:
    notes = []
    dl = a["luma"]["mean"] - b["luma"]["mean"]
    if abs(dl) <= 0.02:
        notes.append(f"亮度对得上（差 {dl:+.3f}，阈值 ±0.02）")
    else:
        notes.append(f"亮度{'偏亮' if dl > 0 else '偏暗'} {abs(dl):.3f}")
    dbr = a["B/R"]["mean"] - b["B/R"]["mean"]
    if abs(dbr) <= 0.03:
        notes.append(f"冷暖对得上（B/R 差 {dbr:+.3f}）")
    else:
        notes.append(f"{'偏冷' if dbr > 0 else '偏暖'}：B/R 差 {dbr:+.3f}"
                     f"（要{'降' if dbr > 0 else '升'} B 通道，约 ×{b['B/R']['mean'] / a['B/R']['mean']:.2f}）")
    dgr = a["G/R"]["mean"] - b["G/R"]["mean"]
    if abs(dgr) <= 0.03:
        notes.append(f"绿红平衡对得上（G/R 差 {dgr:+.3f}）")
    else:
        notes.append(f"G/R 差 {dgr:+.3f}：{'偏绿' if dgr > 0 else '偏红'}，约 ×{b['G/R']['mean'] / a['G/R']['mean']:.2f}")
    return notes


def table(rows: list[tuple[str, dict]]) -> str:
    head = ("| 指标 | " + " | ".join(n for n, _ in rows) + " |\n"
            "|---|" + "---|" * len(rows) + "\n")
    body = ""
    for key, label in [("luma", "亮度 luma"), ("G/R", "绿÷红 G/R"),
                       ("B/R", "蓝÷红 B/R"), ("sat", "饱和（仅参考，不决策）")]:
        cells = [f"{m[key]['mean']:.3f}（p10 {m[key]['p10']:.3f} · p90 {m[key]['p90']:.3f}）"
                 for _, m in rows]
        body += f"| {label} | " + " | ".join(cells) + " |\n"
    return head + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="我们的成片")
    ap.add_argument("--b", required=True, help="参考片（对标目标）")
    ap.add_argument("--fps", type=float, default=1.0, help="抽帧频率（每秒几帧）")
    ap.add_argument("--size", default="270x480", help="抽帧后缩放尺寸，两条片必须一样")
    ap.add_argument("--md", default="", help="把报告写到这个 md 文件")
    ap.add_argument("--json", action="store_true")
    a_arg = ap.parse_args()

    w, h = (int(x) for x in a_arg.size.lower().split("x"))
    A = metrics(frames(Path(a_arg.a), a_arg.fps, w, h))
    B = metrics(frames(Path(a_arg.b), a_arg.fps, w, h))
    if a_arg.json:
        print(json.dumps({"a": A, "b": B}, ensure_ascii=False, indent=2))
        return 0

    txt = table([("我们", A), ("参考片", B)])
    notes = verdict(A, B)
    print(txt)
    for n in notes:
        print(" ·", n)

    if a_arg.md:
        md = [f"# 影调对比（{Path(a_arg.a).name} vs {Path(a_arg.b).name}）\n",
              f"抽帧 {a_arg.fps}/秒，缩到 {w}×{h}，numpy 算均值与分位。"
              "**饱和度只作参考，不参与决策**——参考片是带大字色卡的整片，"
              "我们量的是同一块画面里的东西，口径不同。\n",
              txt,
              "\n判断：\n", *[f"- {n}" for n in notes], "\n",
              "## 怎么用它调\n",
              "只动两个旋钮，一次只动一个：\n",
              "1. **B/R 差** → `colortemperature=temperature=X:mix=Y`（X 越小越暖）。\n",
              "2. **luma 差** → `eq` 里的 `gamma` / `brightness`（gamma 压中暗部，"
              "brightness 整体平移）。\n",
              "改完重跑本脚本，别靠眼睛记。\n"]
        Path(a_arg.md).write_text("\n".join(md), encoding="utf8")
        print(f"→ {a_arg.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
