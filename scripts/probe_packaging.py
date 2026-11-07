#!/usr/bin/env python3
"""CutSkills · 包装探针：从参考片里量出「包装」这一层的规格。

`index_assets.py` 量的是一支素材**能不能用**（模糊、收音、讲什么）；这个脚本量的是**它长什么样**：
字幕什么时候出现、多高、占多宽，镜头多久换一次，画面主色是什么，音效和 BGM 怎么铺。

为什么要单开一个：1:1 复刻包装不能靠形容词。「高级感」「节奏快」抄不成参数；
「字高占画幅 4.2%、居中线 y=0.72H、出现 1.4s、每 3.2s 一条」才能写成 hyperframes 的代码。

**做法是「按变化量采样」，不是「逐帧」。** 两支片子 67 分钟 ≈ 12 万帧，
对每帧跑视觉模型既贵又慢，而且 30fps 里相邻两帧的差别通常只是压缩噪声——
签名的信息不在帧数里，在**变化点**上：字出现/消失、镜头切、色块翻页。
所以按 10fps 抽一条低分辨率时间序列，从序列里找边界，再到边界处按原始分辨率取帧细看。
这条路径和「逐帧看」得到的是同一批结论，成本差三个数量级。

量的东西：
  · 字幕：出现次数、中位持续、每分钟条数、底带白像素峰值（≈字号）、底带上下边界
  · 切点：镜头数、中位镜头时长、最短/最长
  · 色彩：全片主色板、每镜头主色、平均饱和度亮度
  · 声音：整片响度、短时能量峰值密度（≈音效/重音）、静音占比

用法：
  probe_packaging.py --dir <参考片目录> --out <输出目录>
  probe_packaging.py --dir <目录> --only 关键词      # 只跑匹配的文件
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

FPS = 10.0          # 时间序列采样率：够判字幕起落（±0.1s），比逐帧省两个数量级
GW, GH = 64, 36     # 缩略尺寸：判版面和色块足够，判字形不够（字形交给高分辨率复核）


def run(cmd: list[str], binary: bool = False):
    return subprocess.run(cmd, capture_output=True, text=not binary)


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    try:
        d = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    au = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    return {
        "duration": float(d.get("format", {}).get("duration") or 0),
        "w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
        "fps": round(float(num) / float(den or 1), 3) if den else 30.0,
        "vcodec": v.get("codec_name"), "has_audio": bool(au),
        "abytes_mb": round(float(d.get("format", {}).get("size") or 0) / 1e6, 1),
    }


def series(path: Path) -> np.ndarray:
    """整片压成 (T, GH, GW, 3) uint8 的低分辨率时间序列。一次解码，后面全在这个数组上算。"""
    r = run(["ffmpeg", "-v", "error", "-i", str(path),
             "-vf", f"fps={FPS},scale={GW}:{GH}:flags=area", "-an",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], binary=True)
    buf = np.frombuffer(r.stdout or b"", dtype=np.uint8)
    n = buf.size // (GW * GH * 3)
    return buf[:n * GW * GH * 3].reshape(n, GH, GW, 3)


def runs(mask: np.ndarray, min_len: int, max_gap: int) -> list[tuple[int, int]]:
    """布尔序列 → [(起, 止)]，合并小于 max_gap 的断点，丢掉短于 min_len 的段。照字幕的出现规律写的。"""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    spans: list[list[int]] = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        if i - spans[-1][1] <= max_gap:
            spans[-1][1] = int(i)
        else:
            spans.append([int(i), int(i)])
    return [(a, b) for a, b in spans if b - a + 1 >= min_len]


TB_FPS = 5.0          # 字幕检测采样率：字幕最短 0.2s，5fps 够判起落
TB_W = 256            # 底带横向分辨率：笔画在这一档才看得见


def text_band(path: Path) -> dict:
    """底带边缘密度时间序列 → 字幕区间 + 字幕带的上下边界（占画幅高度的比例）。

    为什么不看「白色像素」：白底界面、白墙、白字卡会让白色占比冲到 60% 以上，
    和字幕完全分不开。字的特征是**笔画产生的高频边缘**，而且它出现、消失都是瞬间的。
    两个条件同时成立（边缘密度高 + 边界处有跳变）才算一条字幕。
    """
    r = run(["ffmpeg", "-v", "error", "-i", str(path),
             "-vf", f"fps={TB_FPS},crop=iw:ih*0.40:0:ih*0.60,scale={TB_W}:-2,format=gray",
             "-an", "-f", "rawvideo", "-"], binary=True)
    buf = np.frombuffer(r.stdout or b"", dtype=np.uint8)
    if buf.size == 0:
        return {"spans": [], "bright_p90": 0.0, "band_top": None, "band_bottom": None,
                "glyph_h": None}
    # 帧高由 ffmpeg 的 -2 保比例决定，这里反推：总像素数 / 宽 / 帧数
    # 用固定帧高更稳：先按已知宽度取一次，猜不出来就按 16:9 兜底
    fh = None
    for cand in range(40, 400):
        if buf.size % (TB_W * cand) == 0:
            n = buf.size // (TB_W * cand)
            if 100 <= n <= 100000 and cand % 2 == 0:
                fh, frames = cand, n
                break
    if not fh:
        return {"spans": [], "bright_p90": 0.0, "band_top": None, "band_bottom": None,
                "glyph_h": None}
    a = buf.reshape(frames, fh, TB_W).astype(np.int16)
    gx = np.abs(np.diff(a, axis=2))                      # 横向梯度：笔画就长这样
    gy = np.abs(np.diff(a, axis=1))
    rows = gx[:, :, :].sum(axis=2) + gy.sum(axis=1).repeat(1, axis=0)[:, :, :].sum(axis=2) \
        if False else gx.sum(axis=2)
    dens = (rows > 40).mean(axis=1)                      # 每帧「有笔画的行」比例
    bright = (a > 225).mean(axis=(1, 2))
    thr = max(0.004, float(np.percentile(dens, 70)) * 0.6)
    mask = dens > thr
    spans = runs(mask, min_len=int(0.20 * TB_FPS), max_gap=int(0.30 * TB_FPS))
    # 字高与位置：把所有判成字幕的帧加起来，看笔画落在哪几行
    if spans:
        profile = np.zeros(fh)
        for s0, s1 in spans:
            profile += (gx[s0:s1 + 1] > 40).sum(axis=(0, 2))
        hot = np.flatnonzero(profile > profile.max() * 0.25)
        top, bot = (int(hot[0]), int(hot[-1])) if hot.size else (0, fh - 1)
    else:
        top = bot = None
    # 边界换算成全画幅比例：底带从 0.60H 起，占 0.40H
    def to_full(y):
        return round(0.60 + 0.40 * y / fh, 3) if y is not None else None
    return {
        "spans": [(int(a_ * FPS / TB_FPS), int(b_ * FPS / TB_FPS)) for a_, b_ in spans],
        "bright_p90": round(float(np.percentile(bright, 90)) * 100, 2),
        "band_top": to_full(top), "band_bottom": to_full(bot),
        "glyph_h": round(0.40 * (bot - top + 1) / fh, 3) if top is not None else None,
        "n": len(spans),
    }


def video_metrics(path: Path) -> dict:
    info = probe(path)
    if not info.get("duration"):
        return {"file": path.name, "error": "probe 失败"}
    arr = series(path)
    if arr.size == 0:
        return {"file": path.name, **info, "error": "解码失败"}
    T, H, W = arr.shape[0], GH, GW
    gray = arr.mean(axis=3)
    dur = info["duration"]
    t = np.arange(T) / FPS

    # ① 字幕：底带的高频边缘密度。字是有笔画的，纯画面没有——所以判「有没有字」看的是
    #    边缘密度突变，不是白像素占比（白底界面、白墙、白字卡都会把白像素占比顶到 60%+）。
    #    这一路单独用 5fps、宽 256 取底带，因为 64 宽的缩略图里笔画已经被抹平了。
    band_info = text_band(path)
    subs = band_info["spans"]
    sub_dur = [round(b / FPS - a / FPS + 1 / FPS, 2) for a, b in subs]
    white = np.array([band_info["bright_p90"]])

    # ② 切点：相邻帧平均差。硬切是大跳变，动效是小抖动，两者分开看。
    diff = np.abs(gray[1:] - gray[:-1]).mean(axis=(1, 2))
    dmax = float(diff.max() or 1.0)
    cut_thr = max(6.0, dmax * 0.35)
    cut_idx = np.flatnonzero(diff > cut_thr) + 1
    keep: list[int] = []
    for i in cut_idx:                      # 0.4s 内只算一次，去掉动效抖动造成的连击
        if not keep or i - keep[-1] > 0.4 * FPS:
            keep.append(int(i))
    cuts = [round(i / FPS, 2) for i in keep]
    shot_lens = np.diff([0.0] + cuts + [dur]) if dur else np.array([])

    # ③ 色彩：整片平均色 + 每镜头主色。饱和度用 HSV 的 S 近似。
    mx = arr.max(axis=3).astype(float) / 255.0
    mn = arr.min(axis=3).astype(float) / 255.0
    sat = float(((mx - mn) / np.maximum(mx, 1e-6)).mean())
    bounds = [0] + [int(c * FPS) for c in cuts] + [T]
    shot_colors = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        c = arr[a:b].reshape(-1, 3).mean(axis=0).astype(int)
        shot_colors.append("#%02x%02x%02x" % tuple(c))
    glob = arr.reshape(-1, 3).mean(axis=0).astype(int)

    # ④ 声音：短时能量尖峰密度 ≈ 音效/重音落点；静音占比看有没有留白。
    audio = run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                 "-ar", "8000", "-f", "s16le", "-"], binary=True)
    peaks_per_min = silent_pct = None
    if audio.stdout:
        a = np.frombuffer(audio.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        hop = 400                                   # 50ms 一帧
        n = a.size // hop
        if n > 4:
            env = np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
            thr = np.percentile(env, 92) * 1.5 + 1e-4
            peaks = np.flatnonzero(env > thr)
            keep_p: list[int] = []
            for i in peaks:                          # 120ms 内算同一个音
                if not keep_p or i - keep_p[-1] > 2.4:
                    keep_p.append(int(i))
            peaks_per_min = round(len(keep_p) / (dur / 60), 1)
            silent_pct = round(float((env < 0.01).mean()) * 100, 1)

    return {
        "file": path.name,
        "duration": round(dur, 2),
        **{k: info[k] for k in ("w", "h", "fps", "has_audio", "abytes_mb")},
        "ratio": round(info["w"] / max(1, info["h"]), 3),
        "subs_n": len(subs),
        "subs_per_min": round(len(subs) / (dur / 60), 1) if dur else None,
        "subs_dur_median": round(float(np.median(sub_dur)), 2) if sub_dur else None,
        "subs_dur_max": max(sub_dur) if sub_dur else None,
        "subs_gap_median": round(float(np.median(np.diff([b for _, b in subs]))) / FPS, 2)
        if len(subs) > 2 else None,
        "bottom_bright_p90": band_info["bright_p90"],   # 底带亮像素占比（界面/白墙会拉高，不单独用来判字幕）
        "band_top": band_info["band_top"], "band_bottom": band_info["band_bottom"],
        "glyph_h": band_info["glyph_h"],                # 字幕带高度占画幅比 ≈ 字高量级
        "cuts": len(cuts),
        "shot_median": round(float(np.median(shot_lens)), 2) if shot_lens.size else None,
        "shot_min": round(float(shot_lens.min()), 2) if shot_lens.size else None,
        "shot_max": round(float(shot_lens.max()), 2) if shot_lens.size else None,
        "global_color": "#%02x%02x%02x" % tuple(glob),
        "shot_colors": shot_colors[:40],
        "sat_mean": round(sat, 3),
        "motion_mean": round(float(diff.mean()), 2),
        "sfx_per_min": peaks_per_min,
        "silent_pct": silent_pct,
        "sub_timeline": [[round(a / FPS, 2), round(b / FPS, 2)] for a, b in subs][:120],
        "cut_timeline": cuts[:120],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None, help="默认 <dir>/_packaging")
    ap.add_argument("--only", default=None, help="只跑文件名含该子串的")
    a = ap.parse_args()
    src = Path(a.dir).expanduser().resolve()
    out = Path(a.out).expanduser().resolve() if a.out else src / "_packaging"
    out.mkdir(parents=True, exist_ok=True)

    vids = sorted(p for p in src.iterdir()
                  if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv", ".m4v", ".webm"))
    if a.only:
        vids = [p for p in vids if a.only in p.name]
    print(f"包装探针 · {len(vids)} 支")
    rows = []
    for i, p in enumerate(vids, 1):
        m = video_metrics(p)
        rows.append(m)
        if "error" in m:
            print(f"  {i:02d} ✗ {p.name[:50]}  {m['error']}")
        else:
            print(f"  {i:02d} {m['duration']:7.1f}s {m['w']}×{m['h']}@{m['fps']:g}  "
                  f"字幕 {m['subs_n']:3d} 条 ({m['subs_per_min']} /分, 中位 {m['subs_dur_median']}s)"
                  f" · 镜头 {m['cuts']:3d} (中位 {m['shot_median']}s)"
                  f" · 音效 {m['sfx_per_min']}/分 · {p.name[:34]}")
        (out / f"{p.stem[:60]}.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = [r for r in rows if "error" not in r]
    if ok:
        def med(k):
            v = [r[k] for r in ok if isinstance(r.get(k), (int, float))]
            return round(float(np.median(v)), 2) if v else None
        summary = {
            "videos": len(ok),
            "total_minutes": round(sum(r["duration"] for r in ok) / 60, 1),
            "median": {k: med(k) for k in
                       ("subs_per_min", "subs_dur_median", "glyph_h", "shot_median",
                        "sfx_per_min", "sat_mean", "motion_mean", "silent_pct")},
            "ratios": sorted({f"{r['ratio']:.2f}" for r in ok}),
            "resolutions": sorted({f"{r['w']}x{r['h']}" for r in ok}),
            "fps": sorted({r["fps"] for r in ok}),
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        print("\n中位：", json.dumps(summary["median"], ensure_ascii=False))
        print(f"画幅 {summary['ratios']} · 分辨率 {summary['resolutions']} · fps {summary['fps']}")
        print(f"总量 {summary['total_minutes']} 分钟 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
