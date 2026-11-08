#!/usr/bin/env python3
"""成片 vs 参考片 · 逐帧对照台。

为什么要有这个脚本：`measure_pack.py` 只回答"8 个 token 落没落进带内"，它是一种
**静态**对账——一条片子只要字幕带位置对、镜头中位对，8 项全绿，可看着还是"不像"。
"不像"通常藏在**时间轴上**：什么时候亮、什么时候静、什么时候有第二层信息进来、
镜头怎么切。这一层必须按秒摆出来比。

量什么（全部按画幅比例或每秒值，不掺像素绝对值，1080×1920 和 576×1024 能直接比）：

  亮度曲线   每条片子每 0.5 秒的平均亮度 —— 冷暖、曝光节奏、有没有呼吸
  饱和度曲线 同上 —— 参考片是暖高饱和，我们的往往是灰的
  运动量     相邻帧差 —— 静帧多还是镜头多，也顺带看切点位置
  字幕覆盖   底部字幕带里"有字"的秒数占比 —— 不是条数，是"观众有多少秒在读字"
  信息层     画面中上部有没有叠加层（花字/卡/壳）的秒数占比
  切点       场景切换的时刻表 —— 两片的切点密度与分布

用法：
  compare_films.py --ref <参考片> --ours <成片> --out <目录>
产物：
  <目录>/compare.json      全部曲线与汇总数字
  <目录>/pair-*.jpg        同一秒位的配对帧（左参考右成片）
  <目录>/report.md         人读的对照表
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = Path.home() / "Library" / "Fonts" / "AlimamaShuHeiTi-Bold.otf"
SAMPLE_FPS = 2          # 每秒取两帧做统计；够看出呼吸，又不至于跑十分钟
THUMB_W = 270


def run(cmd: list[str]) -> bytes:
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{r.stderr.decode()[-500:]}")
    return r.stdout


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_streams", "-show_format", str(path)])
    return json.loads(out)


def frames(path: Path, fps: int = SAMPLE_FPS) -> np.ndarray:
    """抽成 RGB uint8 数组 (n, h, w, 3)，统一宽 360。"""
    w, h = 360, 640
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
               f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-"])
    a = np.frombuffer(raw, dtype=np.uint8)
    n = len(a) // (w * h * 3)
    return a[: n * w * h * 3].reshape(n, h, w, 3)


def curves(fr: np.ndarray, fps: int = SAMPLE_FPS) -> dict:
    f = fr.astype(np.float32) / 255.0
    lum = (0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2])
    mx, mn = f.max(-1), f.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean((1, 2))
    warm = (f[..., 0] - f[..., 2]).mean((1, 2))          # R−B：暖/冷
    motion = np.r_[0.0, np.abs(np.diff(f, axis=0)).mean((1, 2, 3))]
    H, W = lum.shape[1], lum.shape[2]
    # 字幕带：画面 62%–76% 高，白/亮像素占比
    band = f[:, int(H * .62):int(H * .76)]
    sub = ((band.min(-1) > 0.72)).mean((1, 2))
    # 信息层：22%–58% 高，出现高饱和彩色块的帧 = 有花字/卡/壳
    mid = f[:, int(H * .22):int(H * .58)]
    mmx, mmn = mid.max(-1), mid.min(-1)
    sat_mid = np.where(mmx > 0, (mmx - mmn) / np.maximum(mmx, 1e-6), 0)
    bright_mid = (mid.mean(-1) > 0.55) & (sat_mid > 0.45)
    info = bright_mid.mean((1, 2))
    return {
        "n": int(len(fr)),
        "fps": fps,
        "lum": lum.mean((1, 2)).round(4).tolist(),
        "sat": sat.round(4).tolist(),
        "warm": warm.round(4).tolist(),
        "motion": motion.round(4).tolist(),
        "sub": sub.round(4).tolist(),
        "info": info.round(4).tolist(),
    }


def cuts(fr: np.ndarray) -> list[int]:
    f = fr.astype(np.float32) / 255.0
    d = np.abs(np.diff(f, axis=0)).mean((1, 2, 3))
    thr = max(0.06, float(np.percentile(d, 92)))
    idx, last = [], -10
    for i, v in enumerate(d):
        if v >= thr and i - last >= SAMPLE_FPS:
            idx.append(i + 1)
            last = i
    return idx


def audio_stats(path: Path) -> dict:
    """听感上的三个数：静音占比、人声段电平、安静段的"底噪"（BGM 在不在就看这个）。"""
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000",
               "-f", "s16le", "-"])
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    if not len(a):
        return {}
    win = int(0.05 * 16000)
    n = len(a) // win
    rms = np.array([20 * np.log10(max(1e-9, float(np.sqrt((a[i * win:(i + 1) * win] ** 2).mean()))))
                    for i in range(n)])
    quiet = rms < -45
    return {
        "silence_ratio": round(float(quiet.mean()), 3),
        "floor_db": round(float(np.percentile(rms, 10)), 1),
        "loud_db": round(float(np.percentile(rms, 90)), 1),
        "dyn_db": round(float(np.percentile(rms, 90) - np.percentile(rms, 10)), 1),
    }


def summarize(c: dict, cut_idx: list[int]) -> dict:
    sub = np.array(c["sub"])
    info = np.array(c["info"])
    lum = np.array(c["lum"])
    sat = np.array(c["sat"])
    warm = np.array(c["warm"])
    dur = c["n"] / c["fps"]
    gaps = np.diff(np.r_[0, np.where(sub > 0.004)[0], c["n"] - 1]) / c["fps"]
    return {
        "duration_s": round(dur, 2),
        "cuts": len(cut_idx),
        "cut_per_min": round(len(cut_idx) / (dur / 60), 2) if dur else 0,
        "shot_median_s": round(float(np.median(np.diff(np.r_[0, cut_idx, c["n"]]) / c["fps"])), 2),
        "lum_mean": round(float(lum.mean()), 3),
        "lum_std": round(float(lum.std()), 3),
        "sat_mean": round(float(sat.mean()), 3),
        "warm_mean": round(float(warm.mean()), 4),
        "sub_cover": round(float((sub > 0.004).mean()), 3),
        "sub_longest_gap_s": round(float(gaps.max()), 2),
        "info_cover": round(float((info > 0.01).mean()), 3),
        "motion_mean": round(float(np.mean(c["motion"])), 5),
        # 运动量要去掉切点那一帧再比：切点前后是两段不同画面，帧差天然巨大，
        # 切点越多、"场景差别越大"的片子，这个数就越高——那比的不是"镜头稳不稳"。
        "motion_steady": round(float(np.median(
            [v for i, v in enumerate(c["motion"]) if not any(abs(i - k) <= 1 for k in cut_idx)]
            or c["motion"])), 5),
    }


def _pair(ref: Path, ours: Path, out: Path, marks: list[tuple[float, float]],
          n: int) -> list[Path]:
    made = []
    for k in range(0, len(marks), 4):
        hit = marks[k:k + 4]
        cell_w, cell_h = 270, 480
        sheet = Image.new("RGB", (cell_w * len(hit) * 2 + 8 * (len(hit) * 2 + 1),
                                  cell_h + 34 + 8), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        f = ImageFont.truetype(str(FONT), 20) if FONT.exists() else ImageFont.load_default()
        x = 8
        for tr, to in hit:
            for tag, src, t in (("参考", ref, tr), ("成片", ours, to)):
                raw = run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src),
                           "-frames:v", "1", "-vf", f"scale={cell_w}:{cell_h}",
                           "-f", "image2pipe", "-vcodec", "png", "-"])
                im = Image.open(__import__("io").BytesIO(raw)).convert("RGB")
                sheet.paste(im, (x, 30))
                d.text((x + 4, 6), f"{t:.1f}s {tag}", font=f,
                       fill=(255, 210, 60) if tag == "参考" else (120, 220, 255))
                x += cell_w + 8
        p = out / f"pair-{k // 4 + 1:02d}.jpg"
        sheet.save(p, quality=88)
        made.append(p)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--out", default="comparison")
    a = ap.parse_args()
    ref, ours = Path(a.ref).expanduser(), Path(a.ours).expanduser()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    res = {}
    for tag, p in (("ref", ref), ("ours", ours)):
        fr = frames(p)
        c = curves(fr)
        ci = cuts(fr)
        c["cut_idx"] = ci
        c["summary"] = summarize(c, ci)
        c["summary"].update(audio_stats(p))
        res[tag] = c
        # 配对帧：每条片子按自己的长度取 8 个百分比位
        res[tag]["marks"] = [round(i / 8 * c["n"] / c["fps"], 2) for i in range(1, 8)]

    (out / "compare.json").write_text(json.dumps(
        {"ref": {k: v for k, v in res["ref"].items()},
         "ours": {k: v for k, v in res["ours"].items()}}, ensure_ascii=False, indent=1))

    # 配对：两条片子等长不等长都能配，按各自百分比位对齐
    n = min(len(res["ref"]["marks"]), len(res["ours"]["marks"]))
    marks = [(res["ref"]["marks"][i], res["ours"]["marks"][i]) for i in range(n)]
    # 按**各自的时间轴**取帧：同一百分比位的画面才可比（126s 的 50% 和 79s 的 50%
    # 是"同一处叙事位置"；按绝对秒数配会把两片的开头和中段错着比）
    sheets = []
    for i in range(0, n, 4):
        sheets += _pair(ref, ours, out, marks[i:i + 4], i // 4 + 1)

    rows = [("时长（秒）", "duration_s"), ("切点数", "cuts"), ("切点/分钟", "cut_per_min"),
            ("镜头中位（秒）", "shot_median_s"), ("平均亮度", "lum_mean"),
            ("亮度标准差", "lum_std"), ("平均饱和度", "sat_mean"), ("暖度 R−B", "warm_mean"),
            ("字幕覆盖", "sub_cover"), ("最长无字幕（秒）", "sub_longest_gap_s"),
            ("信息层覆盖", "info_cover"), ("运动量（含切点）", "motion_mean"),
            ("运动量（不含切点）", "motion_steady"),
            ("静音占比", "silence_ratio"), ("底噪电平 dB", "floor_db"),
            ("动态差 dB", "dyn_db")]
    lines = ["# 成片 vs 参考片 · 逐帧对照", "",
             f"- 参考：`{ref}`", f"- 成片：`{ours}`", "",
             "| 项 | 参考片 | 成片 | 差 |", "|---|---|---|---|"]
    for label, key in rows:
        rv = res["ref"]["summary"][key]
        ov = res["ours"]["summary"][key]
        try:
            diff = f"{ov - rv:+.3f}"
        except TypeError:
            diff = "—"
        lines.append(f"| {label} | {rv} | {ov} | {diff} |")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n配对图 {len(sheets)} 张 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
