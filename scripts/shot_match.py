#!/usr/bin/env python3
"""逐镜归一：把每一镜的亮度与白平衡往目标值拉一部分，别整片一个平均值糊过去。

为什么需要（2026-11-09 量出来的）：整片 luma 0.377 对上参考片 0.381，
G÷R 0.755 / 0.750、B÷R 0.477 / 0.471 也都对上了——**但那是平均对上了**。
逐段看，口播段 luma 0.346 / B÷R 0.372（又暗又暖），施工 B-roll 段
0.487 / 0.621（又亮又冷）。参考片逐秒 luma 的 p10–p90 是 0.353–0.456，
我们原来是 0.309–0.490，散一倍。观众看到的就是"每换一镜，色温跳一下"。

做法：每镜量一次 luma / G÷R / B÷R，算一组通道增益，**按 strength 只拉一部分**
（默认 0.6，全拉会把刻意压暗的镜头拉平），增益夹在 [0.88, 1.15] 之间防止某个
极端镜头被修坏。拉完重编码一过，crf 16。

用法：
    python3 scripts/shot_match.py --clips <项目>/hf/clips [--strength 0.6] [--apply]
不给 --apply 只出报告（先把数字看一眼，别直接改）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

LW = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
TARGET = {"luma": 0.381, "gr": 0.750, "br": 0.471}
CLAMP = (0.65, 1.35)   # 一开始是 (0.88,1.15)，但两边都不够用：口播段太暖（蓝通道要 1.3 才拉得回），
# 施工段太冷（蓝通道要 0.6）。下限定 0.65 是对着参考片的 p90（B÷R 0.618）定的——
# 比参考片最冷的镜头更冷就没道理了。


def stats(path: Path, fps: float = 2.0, w: int = 270, h: int = 480) -> dict:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                          "-vf", f"fps={fps},scale={w}:{h}:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    if n == 0:
        raise SystemExit(f"抽不到帧：{path}")
    a = np.frombuffer(raw[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)
    f = a.astype(np.float32) / 255.0
    luma = float((f @ LW).mean())
    r, g, b = (float(f[..., i].mean()) for i in range(3))
    return {"luma": luma, "gr": g / max(r, 1e-6), "br": b / max(r, 1e-6)}


def gains(cur: dict, target: dict, strength: float) -> tuple[float, float, float]:
    """三个通道增益。只拉 strength 那一部分，夹在 CLAMP 里。"""
    def blend(now: float, want: float) -> float:
        return now + strength * (want - now)

    want_luma = blend(cur["luma"], target["luma"])
    want_gr = blend(cur["gr"], target["gr"])
    want_br = blend(cur["br"], target["br"])
    # 亮度靠三通道同乘，颜色靠 R/G/B 之间的相对比例
    k = want_luma / max(cur["luma"], 1e-6)          # 整体亮度系数
    # G/R 想变成 want_gr/cur_gr，B/R 想变成 want_br/cur_br；把比例摊到 G、B 上，R 不动
    kg = (want_gr / cur["gr"]) if cur["gr"] > 0 else 1.0
    kb = (want_br / cur["br"]) if cur["br"] > 0 else 1.0
    # 三个乘上去之后亮度会变，用 k 归一回去（近似，够用）
    norm = (LW[0] + LW[1] * kg + LW[2] * kb)
    scale = k * 3.0 / (3.0 * norm) if norm > 0 else k
    out = []
    for v in (scale, scale * kg, scale * kb):
        out.append(round(min(CLAMP[1], max(CLAMP[0], v)), 4))
    return tuple(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True, help="切好的片段目录（hf/clips）")
    ap.add_argument("--strength", type=float, default=0.6, help="0=不动，1=完全拉到目标")
    ap.add_argument("--apply", action="store_true", help="真的重编码（不加只出报告）")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--json", default="", help="把每镜的增益写到这个文件")
    a = ap.parse_args()

    clips = sorted(Path(a.clips).glob("*.mp4"))
    if not clips:
        raise SystemExit(f"没有片段：{a.clips}")
    rows, allg = [], {}
    print(f"{'片段':<46}{'luma':>7}{'G/R':>7}{'B/R':>7}   →  增益 R/G/B")
    for p in clips:
        cur = stats(p)
        g = gains(cur, TARGET, a.strength)
        allg[p.name] = list(g)
        rows.append((p.name, cur, g))
        print(f"{p.name[:44]:<46}{cur['luma']:7.3f}{cur['gr']:7.3f}{cur['br']:7.3f}"
              f"   →  {g[0]:.3f} / {g[1]:.3f} / {g[2]:.3f}")

    ls = [c["luma"] for _, c, _ in rows]
    print(f"\n校正前 逐镜 luma：p10 {np.percentile(ls,10):.3f} · "
          f"中位 {np.median(ls):.3f} · p90 {np.percentile(ls,90):.3f}"
          f"（参考片 0.353 – 0.456）")

    if a.json:
        Path(a.json).write_text(json.dumps(allg, ensure_ascii=False, indent=2), encoding="utf8")
        print(f"→ {a.json}")

    if not a.apply:
        print("\n没加 --apply，什么都没改。")
        return 0

    for p in clips:
        g = allg[p.name]
        tmp = p.with_suffix(".tmp.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p),
                            "-vf", f"colorchannelmixer=rr={g[0]}:gg={g[1]}:bb={g[2]}",
                            "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf),
                            "-pix_fmt", "yuv420p", "-c:a", "copy", str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"重编码失败：{p.name}\n{(r.stderr or '')[-600:]}")
        tmp.replace(p)
    ls2 = [stats(p)["luma"] for p in clips]
    print(f"校正后 逐镜 luma：p10 {np.percentile(ls2,10):.3f} · "
          f"中位 {np.median(ls2):.3f} · p90 {np.percentile(ls2,90):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
