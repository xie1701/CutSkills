#!/usr/bin/env python3
"""从参考片抽「节拍骨架」：切点、字幕带、大字、介质变化、推断的 beats、音频底子。

为什么要这个脚本：主链路现在要**贴着参考片的时间结构**剪，而不是凭感觉切。
`compare_films.py` / `probe_packaging.py` 那一套已经雪藏（见 SHELVED.md），
但它们量过的东西里只有一层是可靠的：**时间轴本身**——什么时候切、字幕在画面
哪个高度、大字什么时候砸出来。这里把这一层单独做成一个自足的探针，不 import
任何已雪藏脚本，也不依赖它们的中间产物。

量的口径（和仓库里其它测量脚本一致）：
  · 一律按画幅比例（h_h = 占画面高度的比例）表达，不写像素；
  · 切点用帧差找突变，不用白像素那种偏方（STATUS 里已实测失败）；
  · 字幕带用「行剖面 + 边缘密度」在全画幅里找，不预设在下三分之一——
    有的片字幕在顶部 y≈10–30%，写死底带会漏；
  · 大字只认「突然出现、占了好几行、边缘密度远高于该行平时水平」的块；
  · beats 是**推断**，每一段都给 why，且 why 里必须有能被推翻的数字/原话。

用法：
  python3 scripts/skeleton.py --video <参考片> --out skeleton.json
  python3 scripts/skeleton.py --video <参考片> --out skeleton.json --json-only
产物：一段 JSON（字段见 README / 任务书），默认另打一张紧凑的表。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

AN_FPS = 10.0          # 版面/字幕/大字的采样率：字幕最短 ~0.3s，10fps 够判起落
AN_W = 288             # 版面分析宽度：字高 2.8%H 时约 14px，够量
CUT_W = 144            # 切点分析宽度：只要帧差，不需要看清字
CUT_FPS_CAP = 24.0


# ---------------------------------------------------------------- 基础工具

def need_ffmpeg() -> None:
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise SystemExit(
                f"找不到 {name}。先跑：source scripts/runtime.sh env\n"
                "（它会把 ~/.cutskills/opt/ffmpeg-zimg/bin 放到 PATH 前面）"
            )


def run(cmd: list[str]) -> bytes:
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"命令失败：{' '.join(cmd[:6])}…\n{tail}")
    return r.stdout


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)])
    d = json.loads(out)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise SystemExit(f"没有视频流：{path}")
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if den and float(den or 1) else 0.0
    duration = float(d.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "duration_s": duration,
        "w": int(v.get("width") or 0),
        "h": int(v.get("height") or 0),
        "fps": fps,
        "has_audio": bool(a),
    }


def even(n: float) -> int:
    return max(2, int(round(n / 2.0)) * 2)


def frame_size(src_w: int, src_h: int, width: int) -> tuple[int, int]:
    """按源比例给出分析尺寸（高度取偶数，ffmpeg 的 scale 才稳）。"""
    w = even(width)
    h = even(width * src_h / max(1, src_w))
    return w, h


def iter_frames(path: Path, fps: float, w: int, h: int, chunk: int = 48):
    """流式解码成 gray 帧块，返回 uint8 (n, h, w)。不把整片塞进内存。"""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
           "-vf", f"fps={fps:g},scale={w}:{h}:flags=area",
           "-an", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    fsize = w * h
    buf = b""
    try:
        while True:
            buf += p.stdout.read(fsize * chunk)
            if not buf:
                break
            n = len(buf) // fsize
            if n == 0:
                break
            out = np.frombuffer(buf[: n * fsize], np.uint8).reshape(n, h, w)
            yield out
            buf = buf[n * fsize:]
        if p.wait() != 0:
            err = p.stderr.read().decode("utf-8", "replace")[-500:]
            raise RuntimeError(f"解码失败：{path.name}\n{err}")
    finally:
        try:
            p.stdout.close()
        finally:
            if p.poll() is None:
                p.kill()


def runs(mask: np.ndarray, min_len: int = 1, max_gap: int = 1) -> list[tuple[int, int]]:
    """布尔序列 → [(起, 止)]，允许 max_gap 的空隙，丢掉短于 min_len 的段。"""
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


def pct(vals, q: float) -> float:
    return float(np.percentile(vals, q)) if len(vals) else 0.0


# ---------------------------------------------------------------- 切点

def detect_cuts(path: Path, info: dict) -> list[float]:
    """帧差突变 = 切点。

    为什么不用 ffmpeg 的 `select='gt(scene,0.3)'`：这支参考片是**同一机位跳剪**，
    主体和背景几乎不动，scene 分数很低（0.3 阈值只出 4 个），但每 2–4 秒确实有一次跳剪。
    帧差能看见「人换了个姿势」这种阶跃，scene 分数看不见。
    阈值按片自适应：med + 5.5×MAD，再夹到 [0.022, 0.045]——
    固定 0.3（旧探针）会把 38 个切点压成 14 个。
    """
    fps = min(info["fps"] or CUT_FPS_CAP, CUT_FPS_CAP)
    w, h = frame_size(info["w"], info["h"], CUT_W)
    d: list[np.ndarray] = []
    prev = None
    for chunk in iter_frames(path, fps, w, h):
        g = chunk.astype(np.float32) / 255.0
        if prev is not None:
            both = np.concatenate([prev[None], g], axis=0)
            dd = np.abs(np.diff(both, axis=0)).mean(axis=(1, 2))
            d.append(dd)
        elif len(g) > 1:
            d.append(np.abs(np.diff(g, axis=0)).mean(axis=(1, 2)))
        prev = g[-1]
    if not d:
        return []
    arr = np.concatenate(d)
    if arr.size < 4:
        return []
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med))) + 1e-6
    thr = float(np.clip(med + 5.5 * mad, 0.022, 0.045))
    half = max(1, int(0.25 * fps))
    cand = [i for i in range(1, len(arr) - 1)
            if arr[i] > thr and arr[i] >= arr[max(0, i - half):i + half + 1].max()]
    # 0.9s 内只留最强的一个：跳剪和动效会在相邻几帧连击，
    # 不合并的话 126s 的片会数出 70+ 个「切点」，镜头中位掉到 1.3s。
    min_gap = int(np.clip(0.007 * info["duration_s"], 0.6, 1.2) * fps)
    keep: list[int] = []
    for i in sorted(cand, key=lambda k: -arr[k]):
        if all(abs(i - j) >= min_gap for j in keep):
            keep.append(i)
    keep.sort()
    return [round((i + 1) / fps, 3) for i in keep
            if (i + 1) / fps < info["duration_s"] - 0.05]


def cut_stats(cuts: list[float], dur: float) -> tuple[dict, list[float]]:
    lengths = [round(b - a, 3) for a, b in zip([0.0] + cuts, cuts + [dur])]
    lengths = [max(0.0, x) for x in lengths]
    n = len(cuts)
    return {
        "median_s": round(float(np.median(lengths)), 2) if lengths else None,
        "p10": round(pct(lengths, 10), 2),
        "p25": round(pct(lengths, 25), 2),
        "p75": round(pct(lengths, 75), 2),
        "p90": round(pct(lengths, 90), 2),
        "count": n,
    }, lengths


# ---------------------------------------------------------------- 版面采样（一遍）

def layout_pass(path: Path, info: dict) -> dict:
    """一遍解码，留下版面分析需要的**行剖面**，不留原帧。

    每帧只存三个 (H,) 数组：
      row_gx  每行横向边缘能量（字、物体边界都在这里）
      row_gd  每行「强边缘像素占比」（大字用的块状判据）
      lum / edge  整帧标量（介质变化用的镜头签名）
    259s 的片也就 ~11MB，比存帧省两个数量级。
    """
    w, h = frame_size(info["w"], info["h"], AN_W)
    row_gx: list[np.ndarray] = []
    row_gd: list[np.ndarray] = []
    lum: list[np.ndarray] = []
    edge: list[np.ndarray] = []
    for chunk in iter_frames(path, AN_FPS, w, h):
        g = chunk.astype(np.float32) / 255.0
        gx = np.abs(np.diff(g, axis=2)).mean(axis=2)            # (n, H)
        gy = np.abs(np.diff(g, axis=1)).mean(axis=2)             # (n, H-1)
        gy_full = np.zeros_like(gx)
        gy_full[:, :-1] += gy
        gmag = gx + gy_full
        row_gx.append(gx.astype(np.float32))
        row_gd.append((gmag > 0.14).astype(np.float32))
        lum.append(g.mean(axis=(1, 2)))
        edge.append(gmag.mean(axis=1))
    if not row_gx:
        return {"n": 0, "w": w, "h": h}
    return {
        "n": int(sum(len(x) for x in row_gx)),
        "w": w, "h": h,
        "row_gx": np.concatenate(row_gx),
        "row_gd": np.concatenate(row_gd),
        "lum": np.concatenate(lum),
        "edge": np.concatenate(edge),
    }


# ---------------------------------------------------------------- 字幕带

def subtitle_candidates(lay: dict, cuts: list[float]) -> list[dict]:
    """全画幅找「行边缘能量随时间起伏最大」的窄带，返回候选，不在底带写死。"""
    n, H = lay["n"], lay["h"]
    if n < 8:
        return []
    gx = lay["row_gx"]
    valid = np.ones(n, bool)
    for c in cuts:
        i = int(round(c * AN_FPS))
        valid[max(0, i - 3):i + 4] = False
    if valid.sum() < 5:
        valid[:] = True
    std = gx[valid].std(axis=0)
    sm = np.convolve(std, np.ones(3) / 3.0, mode="same")
    mx = float(sm.max())
    if mx < 0.008:            # 整片没有足够剧烈的行变化 = 大概率没有常驻字幕
        return []
    hot = sm > 0.55 * mx
    out: list[dict] = []
    for a, b in runs(hot, min_len=2):
        hh = (b - a + 1) / H
        if not (0.012 <= hh <= 0.10):
            continue
        out.append({
            "y0": int(a), "y1": int(b),
            "center_h": round(float((a + b) / 2 / H), 4),
            "height_h": round(float(hh), 4),
            "score": float(sm[a:b + 1].mean()),
        })
    out.sort(key=lambda c: -c["score"])
    return out[:4]


def subtitle_detail(path: Path, info: dict, lay: dict, cand: dict,
                    cuts: list[float]) -> dict:
    """在选定带里数「换行」：白/亮像素的列剖面一帧一变的那一下就是换行。

    这一步单独解码一遍：行剖面只保留了每行的平均值，数不出「这一行字换了」。
    """
    w, h = lay["w"], lay["h"]
    y0, y1 = cand["y0"], min(cand["y1"], h - 1)
    bright_sig: list[np.ndarray] = []
    edge_sig: list[np.ndarray] = []
    white_frac: list[float] = []
    for chunk in iter_frames(path, AN_FPS, w, h):
        g = chunk.astype(np.float32) / 255.0
        band = g[:, y0:y1 + 1]
        bright = band > 0.82
        bright_sig.append(bright.mean(axis=1).astype(np.float32))
        gxb = np.abs(np.diff(band, axis=2)) > 0.12
        edge_sig.append(gxb.mean(axis=1).astype(np.float32))
        white_frac.append(bright.mean(axis=(1, 2)).astype(np.float32))
    if not bright_sig:
        return {"present": False, "note": "解码不出帧"}
    bs = np.concatenate(bright_sig)
    es = np.concatenate(edge_sig)
    wf = np.concatenate(white_frac)
    n = len(bs)
    dur = n / AN_FPS

    def changes(sig: np.ndarray) -> tuple[int, list[int]]:
        hd = np.abs(np.diff(sig, axis=0)).sum(axis=1) / max(1, sig.shape[1])
        p90 = float(np.percentile(hd, 90))
        thr = max(0.010, 0.4 * p90)
        idx = np.where(hd > thr)[0] + 1
        keep: list[int] = []
        for i in idx:
            if not keep or i - keep[-1] >= int(0.30 * AN_FPS):
                keep.append(int(i))
        return len(keep), keep

    cov = float((wf > 0.003).mean())
    n_bright, t_bright = changes(bs)
    n_edge, t_edge = changes(es)
    use_bright = cov >= 0.20
    n_changes, t_changes = (n_bright, t_bright) if use_bright else (n_edge, t_edge)
    lines = n_changes + 1 if n_changes else 0
    lpm = round(lines / dur * 60, 1) if dur and lines else None

    present = bool(cov >= 0.15 and lpm is not None and 4 <= lpm <= 180
                   and cand["height_h"] >= 0.012)
    note = None
    if not present:
        if cov < 0.15:
            note = "带宽内几乎没有持续出现的亮字，判为无字幕"
        elif lpm is None or lpm < 4:
            note = "带宽里没有稳定的换行节奏，判为无字幕"
        else:
            note = f"换行频率 {lpm}/分 不像字幕（可能在量到别的动态层）"
    return {
        "present": present,
        "band_center_h": cand["center_h"],
        "glyph_h_h": cand["height_h"],
        "lines_per_min": lpm if present else None,
        "band_h_h": cand["height_h"],
        "coverage": round(cov, 3),
        "line_changes": int(n_changes),
        "change_times": [round(i / AN_FPS, 2) for i in t_changes],
        "method": "white" if use_bright else "edge",
        "note": note,
    }


def measure_subtitle(path: Path, info: dict, lay: dict, cuts: list[float]) -> dict:
    cands = subtitle_candidates(lay, cuts)
    if not cands:
        return {"present": False, "band_center_h": None, "glyph_h_h": None,
                "lines_per_min": None,
                "note": "全画幅行剖面没有 1.2–10%H 的候选带：这几行没有随时间起伏的边缘（无字幕或字幕静止不动）"}
    best = None
    for c in cands:
        got = subtitle_detail(path, info, lay, c, cuts)
        # 字幕的判据是「频繁换行」；静态标题/花字换行少，让位给真正的字幕带。
        rank = (got["lines_per_min"] or 0) if got["present"] else 0
        if best is None or rank > best[0]:
            best = (rank, got)
    return best[1]


# ---------------------------------------------------------------- 大字

def detect_bigwords(lay: dict, cuts: list[float], sub: dict) -> list[dict]:
    """大字/大色块：某几行的边缘密度**突然远高于该行平时的水平**。

    判据故意保守：只看「该行相对自己基线的增量」，所以白板、书架、桌面这些
    **一直在**的硬边不会被算进来；手势只会让一小片变化，撑不满 5%H 的块。
    """
    n, H = lay["n"], lay["h"]
    if n < 10:
        return []
    gd = lay["row_gd"]
    base = np.median(gd, axis=0)
    delta = gd - base[None, :]
    valid = np.ones(H, bool)
    valid[: int(0.04 * H)] = False
    valid[int(0.96 * H):] = False
    if sub.get("present") and sub.get("band_center_h") is not None:
        c = float(sub["band_center_h"]) * H
        hh = float(sub.get("band_h_h") or 0.03) * H
        valid[int(max(0, c - hh)):int(min(H, c + hh))] = False
    cut_idx = {int(round(t * AN_FPS)) for t in cuts}
    recs: list[tuple[int, int, int, float]] = []
    for k in range(n):
        if any(abs(k - c) <= int(0.35 * AN_FPS) for c in cut_idx):
            continue
        d = np.where(valid, delta[k], 0.0)
        hot = d > 0.25
        blocks: list[list[int]] = []
        for a, b in runs(hot, min_len=2, max_gap=1):
            if blocks and (a - blocks[-1][1] - 1) / H <= 0.05:
                blocks[-1][1] = b
            else:
                blocks.append([a, b])
        blocks = [b for b in blocks if (b[1] - b[0] + 1) / H >= 0.025]
        if not blocks:
            continue
        # 同一层的几块（两行大字、卡片里的几行）合成一个组；组与组之间差得远才算不同层。
        # 只取最高的那一组，而不是把整帧所有块拼起来——拼起来会把 0.3H 的花字量成 1H。
        groups: list[list[int]] = []
        for b in blocks:
            if groups and (b[0] - groups[-1][1] - 1) / H <= 0.06:
                groups[-1][1] = b[1]
            else:
                groups.append(list(b))
        top, bot = max(groups, key=lambda g: g[1] - g[0])
        if (bot - top + 1) / H > 0.60:      # 全屏卡不再往大里量
            continue
        recs.append((k, top, bot, float(d[top:bot + 1].max())))

    events: list[list[tuple[int, int, int, float]]] = []
    for rec in recs:
        if events and rec[0] - events[-1][-1][0] <= int(0.35 * AN_FPS):
            events[-1].append(rec)
        else:
            events.append([rec])

    out: list[dict] = []
    for ev in events:
        ks = [e[0] for e in ev]
        dur = (ks[-1] - ks[0] + 1) / AN_FPS
        if dur < 0.5:
            continue
        heights = [(e[2] - e[1] + 1) / H for e in ev]
        hh = float(np.median(heights))
        if hh < 0.045:
            continue
        out.append({
            "t": round(ks[0] / AN_FPS, 2),
            "h_h": round(hh, 3),
            "dur_s": round(dur, 2),
            "score": round(max(e[3] for e in ev), 2),
        })
    # 同一处反复报（花字闪两下）合并成一条
    merged: list[dict] = []
    for e in out:
        if merged and e["t"] - (merged[-1]["t"] + merged[-1]["dur_s"]) < 0.6:
            last = merged[-1]
            last["dur_s"] = round(max(last["dur_s"], e["t"] + e["dur_s"] - last["t"]), 2)
            last["h_h"] = round(max(last["h_h"], e["h_h"]), 3)
        else:
            merged.append(dict(e))
    return merged


# ---------------------------------------------------------------- 介质变化

def detect_medium_changes(lay: dict, cuts: list[float], bigwords: list[dict]) -> list[float]:
    """镜头级签名（亮度 + 边缘密度）跳得最狠的几刀，外加全屏级大字/色块的出现点。

    这是一个**低精度**指标：它只能回答「这里像换了一种介质」，不能回答
    「换成了什么」。所以宁少不多，并且和切点分开列。
    """
    n = lay["n"]
    if n < 10 or not cuts:
        return []
    bounds = [0.0] + list(cuts) + [n / AN_FPS]
    sigs = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        i, j = int(a * AN_FPS), min(n, int(b * AN_FPS))
        if j - i < 2:
            continue
        sigs.append((a, float(np.mean(lay["lum"][i:j])), float(np.mean(lay["edge"][i:j]))))
    if len(sigs) < 2:
        return []
    scores = []
    for (ta, la, ea), (tb, lb, eb) in zip(sigs[:-1], sigs[1:]):
        scores.append((tb, abs(lb - la) / 0.15 + abs(eb - ea) / 0.03))
    vals = np.array([s for _, s in scores])
    thr = max(1.4, float(np.percentile(vals, 75)))
    out = [round(t, 1) for t, s in scores if s >= thr][:5]
    for b in bigwords:
        if b["h_h"] >= 0.20 and b["dur_s"] >= 1.2:
            out.append(round(b["t"], 1))
    out = sorted({x for x in out})
    dedup: list[float] = []
    for t in out:
        if not dedup or t - dedup[-1] > 1.0:
            dedup.append(t)
    return dedup[:8]


# ---------------------------------------------------------------- 音频

def audio_metrics(path: Path, info: dict) -> dict:
    if not info["has_audio"]:
        return {"has_bgm": None, "bgm_low_ratio": None, "loudness_lufs": None,
                "note": "没有音频流"}
    # 综合响度：loudnorm 的 input_i（和仓库其它脚本一个口径）
    loud = None
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-i", str(path),
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r'"input_i"\s*:\s*"(-?[\d.]+|-inf)"', r.stderr)
    if m and m.group(1) != "-inf":
        loud = round(float(m.group(1)), 1)

    def env(args: list[str]) -> np.ndarray:
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path)] + args +
                           ["-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                           capture_output=True)
        a = np.frombuffer(r.stdout[: len(r.stdout) // 4 * 4], np.float32)
        hop = 1600  # 0.2s
        n = len(a) // hop
        if n < 2:
            return np.zeros(0)
        return np.sqrt((a[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)

    full = env([])
    low = env(["-af", "highpass=f=40,lowpass=f=200"])
    if len(full) < 3 or len(low) < 3:
        return {"has_bgm": None, "bgm_low_ratio": None, "loudness_lufs": loud,
                "note": "音频太短，判不了 BGM"}
    ratio = float((low ** 2).mean() / max(1e-12, (full ** 2).mean()))
    low_db = float(20 * math.log10(float(np.median(low)) + 1e-9))
    quiet = full <= np.percentile(full, 20)
    quiet_low_db = float(20 * math.log10(float(np.median(low[quiet])) + 1e-9)) if quiet.any() else -120
    has_bgm = bool(ratio >= 0.12 and low_db > -45 and quiet_low_db > -48)
    note = ("bgm_low_ratio = 40–200Hz 低频能量 / 全带能量；"
            "has_bgm 还要求低频底子在安静段仍可闻。人声连续铺满时这项只能当参考。")
    return {
        "has_bgm": has_bgm,
        "bgm_low_ratio": round(ratio, 3),
        "loudness_lufs": loud,
        "note": note,
    }


# ---------------------------------------------------------------- 转写（可选）

def parse_srt(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        body = "\n".join(x for x in block.splitlines() if "-->" not in x and not x.strip().isdigit())
        out.append({"start": h1 * 3600 + m1 * 60 + s1 + ms1 / 1000,
                    "end": h2 * 3600 + m2 * 60 + s2 + ms2 / 1000,
                    "text": body.replace("\n", "").strip()})
    return out


def load_transcript(video: Path, explicit: str | None) -> tuple[list[dict], str | None]:
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    else:
        for suf in (".srt", ".vtt", ".json", ".txt"):
            cands.append(video.with_suffix(suf))
        for d in (video.parent, video.parent.parent):
            for name in ("_cutskills/index.json", "index.json"):
                cands.append(d / name)
    seen = set()
    for p in cands:
        p = p.resolve()
        if p in seen or not p.exists():
            continue
        seen.add(p)
        try:
            if p.suffix.lower() in (".srt", ".vtt"):
                phrases = parse_srt(p.read_text(encoding="utf-8", errors="replace"))
                if phrases:
                    return phrases, str(p)
            elif p.suffix.lower() == ".json":
                d = json.loads(p.read_text(encoding="utf-8"))
                ph = None
                if isinstance(d, dict) and isinstance(d.get("transcript"), dict):
                    ph = d["transcript"].get("phrases")
                elif isinstance(d, dict) and isinstance(d.get("phrases"), list):
                    ph = d["phrases"]
                elif isinstance(d, dict) and isinstance(d.get("assets"), list):
                    for a in d["assets"]:
                        if a.get("id") == video.name or Path(str(a.get("path", ""))).name == video.name:
                            ph = (a.get("transcript") or {}).get("phrases")
                            break
                if ph:
                    return [{"start": float(x["start"]), "end": float(x["end"]),
                             "text": str(x.get("text", ""))} for x in ph], str(p)
            elif p.suffix.lower() == ".txt":
                phrases = []
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    m = re.match(r"\s*([\d.]+)\s*[-–]\s*([\d.]+)\s+(.+)", line)
                    if m:
                        phrases.append({"start": float(m.group(1)), "end": float(m.group(2)),
                                        "text": m.group(3).strip()})
                if phrases:
                    return phrases, str(p)
        except Exception:
            continue
    return [], None


# ---------------------------------------------------------------- beats（推断）

SIGNALS: dict[str, tuple[str, int]] = {
    "但是": ("turn", 3), "可是": ("turn", 3), "然而": ("turn", 3),
    "不过": ("turn", 2), "问题是": ("turn", 3), "但": ("turn", 2),
    "其实": ("turn", 2),
    "第一": ("evidence", 2), "第二": ("evidence", 2), "第三": ("evidence", 2),
    "首先": ("evidence", 1), "其次": ("evidence", 1),
    "最后": ("payoff", 2), "总结": ("payoff", 2), "总之": ("payoff", 2),
    "所以": ("claim", 1), "因此": ("claim", 1), "那么": ("payoff", 1),
}


def signal_hits(transcript: list[dict]) -> list[dict]:
    hits = []
    for p in transcript:
        text = p.get("text", "")
        best = None
        for w, (role, weight) in SIGNALS.items():
            if w in text:
                if best is None or weight > best[2]:
                    best = (w, role, weight)
        if best:
            hits.append({"t": round(float(p["start"]), 2), "word": best[0],
                         "role": best[1], "weight": best[2],
                         "text": text[:24]})
    # 2 秒内的同一句信号只留最重的
    hits.sort(key=lambda h: h["t"])
    out = []
    for h in hits:
        if out and h["t"] - out[-1]["t"] < 2.0:
            if h["weight"] > out[-1]["weight"]:
                out[-1] = h
        else:
            out.append(h)
    return out


def cut_density_shift(cuts: list[float], dur: float, start: float, end: float,
                      taken: list[float]) -> tuple[float, float, float] | None:
    """在 body 里找「切点密度突然变了」的位置。返回 (t, before, after)。"""
    best = None
    step = max(2.0, dur / 40)
    win = max(6.0, dur / 12)
    t = start + win
    while t < end - win:
        b0, b1 = max(0.0, t - win), t
        a0, a1 = t, min(dur, t + win)
        nb = sum(1 for c in cuts if b0 <= c < b1)
        na = sum(1 for c in cuts if a0 <= c < a1)
        rb, ra = nb / (b1 - b0) * 60, na / (a1 - a0) * 60
        score = abs(ra - rb) / (0.5 + min(ra, rb))
        if abs(ra - rb) >= 2.0 and all(abs(t - x) > 0.08 * dur for x in taken):
            if best is None or score > best[0]:
                best = (score, t, rb, ra)
        t += step
    if not best:
        return None
    return round(best[1], 2), round(best[2], 1), round(best[3], 1)


def rate_between(cuts: list[float], a: float, b: float) -> float:
    span = max(1e-6, b - a)
    return round(sum(1 for c in cuts if a <= c < b) / span * 60, 1)


def infer_beats(dur: float, cuts: list[float], transcript: list[dict],
                bigwords: list[dict]) -> tuple[list[dict], str]:
    # hook：0–20% 之内、最靠近 15% 的那一刀。没有就取 15%。
    lo, hi = 0.12 * dur, 0.20 * dur
    in_win = [c for c in cuts if lo <= c <= hi]
    target = 0.15 * dur
    hook_end = min(in_win, key=lambda c: abs(c - target)) if in_win else round(target, 2)

    hits = signal_hits(transcript) if transcript else []
    body_hits = [h for h in hits if hook_end + 0.06 * dur < h["t"] < dur - 0.06 * dur]

    taken: list[float] = []
    picked: list[dict] = []
    for h in sorted(body_hits, key=lambda h: (-h["weight"], h["t"])):
        if len(picked) >= 3:
            break
        if all(abs(h["t"] - x) >= 0.10 * dur for x in taken):
            picked.append(h)
            taken.append(h["t"])
    picked.sort(key=lambda h: h["t"])

    shift = cut_density_shift(cuts, dur, hook_end, dur, taken) if len(picked) < 3 else None
    bounds = [p["t"] for p in picked]
    bound_info: dict[float, dict] = {p["t"]: p for p in picked}
    if shift:
        bounds.append(shift[0])
        bound_info[shift[0]] = {"t": shift[0], "word": None, "role": "density",
                                "text": None, "before": shift[1], "after": shift[2]}
        bounds.sort()

    if not bounds:
        mid = round(hook_end + (dur - hook_end) * 0.58, 2)
        bounds = [mid]
        bound_info[mid] = {"t": mid, "word": None, "role": "density", "text": None,
                           "before": None, "after": None}

    edges = [0.0, hook_end] + bounds + [dur]
    beats: list[dict] = []
    middle_default = ["claim", "evidence", "turn", "claim"]
    mi = 0
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
        if b - a < 0.01:
            continue
        if i == 0:
            kind = "hook"
        elif i == len(edges) - 2:
            kind = "payoff"
        else:
            info_ = bound_info.get(round(a, 2)) or bound_info.get(a)
            role = info_.get("role") if info_ else None
            if role in ("turn", "evidence", "claim", "payoff") and kind != "payoff":
                kind = role if role != "claim" or mi == 0 else "claim"
            else:
                kind = middle_default[min(mi, len(middle_default) - 1)]
            mi += 1

        ncut = sum(1 for c in cuts if a <= c < b)
        rate = rate_between(cuts, a, b)
        overall = rate_between(cuts, 0, dur)
        info_ = bound_info.get(a) or {}
        quote = ""
        if info_.get("text"):
            quote = f"语料『{info_['text']}』（{a:.1f}s，信号词『{info_['word']}』）；"
        if kind == "hook":
            first = transcript[0]["text"][:18] if transcript else ""
            q = f"首句『{first}』；" if first else "无转写；"
            why = (f"推断：0–{b:.1f}s 只有 {ncut} 个切点（{rate}/分，全片中位 {overall}/分），"
                   f"镜头停得比后面久；{q}把这段时间当 hook，是因为它没有靠切点推进，"
                   f"靠的是一句话把人留住。")
        elif info_.get("role") == "density" and info_.get("before") is not None:
            why = (f"推断：{a:.1f}–{b:.1f}s 共 {ncut} 个切点（{rate}/分，全片中位 {overall}/分）；"
                   f"分界处切点密度从 {info_['before']}/分 跳到 {info_['after']}/分。"
                   f"依据只有密度突变，没有转写可核对——这条最容易被推翻。")
        else:
            why = (f"推断：{a:.1f}–{b:.1f}s 共 {ncut} 个切点（{rate}/分，全片中位 {overall}/分）；"
                   f"{quote}选这里当分界，是因为原话在这里换了说话目的（{info_.get('role', '结构')}），"
                   f"不是因为画面。可以拿转写复核这句话是不是真的转折。")

        beats.append({"start": round(a, 2), "end": round(b, 2), "kind": kind, "why": why})

    note = ("beats 是推断：边界来自 "
            + ("转写里的信号词" if transcript else "切点密度（没有找到转写）")
            + (" + 切点密度突变" if shift else "")
            + "。hook 固定取 0–20% 内最接近 15% 的一刀。"
            "每条 why 都写了可核对的数字/原话，不成立就删掉那一段，不要硬凑。")
    return beats, note


def build_hook(dur: float, cuts: list[float], sub: dict, bigwords: list[dict]) -> dict:
    win = min(3.0, dur)
    cin = [c for c in cuts if c < win]
    changes = {0.0}
    changes.update(round(c, 2) for c in cin)
    if sub.get("present"):
        for t in sub.get("change_times", []):
            if t < win:
                changes.add(round(t, 2))
                break
    for b in bigwords:
        if b["t"] < win:
            changes.add(round(b["t"], 2))
    changes = sorted(changes)
    dedup: list[float] = []
    for t in changes:
        if not dedup or t - dedup[-1] > 0.40:
            dedup.append(t)
    changes = dedup

    bits = []
    if cin:
        bits.append("切了 " + str(len(cin)) + " 次（" + "、".join(f"{c:.2f}s" for c in cin) + "）")
    else:
        bits.append("没有切点")
    if sub.get("present"):
        first_change = next((t for t in sub.get("change_times", []) if t < win), None)
        n_in = sum(1 for t in sub.get("change_times", []) if t < win)
        if first_change is not None:
            bits.append(f"字幕 {first_change:.2f}s 起出现，窗口内换 {n_in} 行")
        else:
            bits.append("窗口内字幕没有换行")
    else:
        bits.append("没测到字幕")
    bw = [b for b in bigwords if b["t"] < win]
    if bw:
        bits.append("大字 " + "、".join(f"{b['t']:.2f}s（{b['h_h']:.2f}H）" for b in bw) + " 入场")
    else:
        bits.append("没有大字入场")
    return {"window_s": [0, round(win, 1)], "cuts_in_window": len(cin),
            "change_at": changes, "note": "；".join(bits) + "。"}


# ---------------------------------------------------------------- 输出

def human(res: dict) -> None:
    c = res["cuts"]
    print(f"\n{Path(res['src']).name}")
    print(f"  {res['duration_s']}s · {res['w']}×{res['h']}@{res['fps']:g}")
    print(f"  切点 {c['count']} · 中位 {c['median_s']}s · "
          f"p10 {c['p10']} p25 {c['p25']} p75 {c['p75']} p90 {c['p90']}")
    s = res["subtitle"]
    if s.get("present"):
        print(f"  字幕 中心 {s['band_center_h']:.3f}H · 字高 {s['glyph_h_h']:.3f}H · "
              f"{s['lines_per_min']}/分（{s['line_changes']} 次换行）")
    else:
        print(f"  字幕 无 / 未测到：{s.get('note')}")
    if res["bigword"]:
        print("  大字 " + " | ".join(f"{b['t']}s {b['h_h']:.2f}H {b['dur_s']}s"
                                    for b in res["bigword"]))
    else:
        print("  大字 无")
    print("  介质变化 " + ("、".join(f"{t}s" for t in res["medium_changes"]) or "无"))
    print("  beats " + " | ".join(f"{b['start']}-{b['end']} {b['kind']}" for b in res["beats"]))
    a = res["audio"]
    bm = "?" if a["has_bgm"] is None else ("有" if a["has_bgm"] else "无")
    print(f"  音频 {a['loudness_lufs']} LUFS · BGM {bm} · 低频占比 {a['bgm_low_ratio']}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="从参考片抽节拍骨架（切点/字幕带/大字/beats）")
    ap.add_argument("--video", required=True, help="参考片路径")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--transcript", default=None,
                    help="可选：SRT/VTT/JSON 转写；不给就自动找同名字幕或 _cutskills/index.json")
    ap.add_argument("--json-only", action="store_true", help="不打印人类可读的表")
    a = ap.parse_args()

    need_ffmpeg()
    video = Path(a.video).expanduser()
    if not video.exists():
        raise SystemExit(f"找不到视频：{video}")
    info = probe(video)
    dur = info["duration_s"]
    info["fps"] = info["fps"] or 24.0

    cuts = detect_cuts(video, info)
    cstats, lengths = cut_stats(cuts, dur)
    lay = layout_pass(video, info)
    sub = measure_subtitle(video, info, lay, cuts)
    bigwords = detect_bigwords(lay, cuts, sub)
    medium = detect_medium_changes(lay, cuts, bigwords)
    audio = audio_metrics(video, info)
    transcript, tsrc = load_transcript(video, a.transcript)
    beats, beats_note = infer_beats(dur, cuts, transcript, bigwords)
    hook = build_hook(dur, cuts, sub, bigwords)

    subtitle_out = {
        "present": bool(sub.get("present")),
        "band_center_h": sub.get("band_center_h"),
        "glyph_h_h": sub.get("glyph_h_h"),
        "lines_per_min": sub.get("lines_per_min"),
        "line_changes": sub.get("line_changes"),
        "band_h_h": sub.get("band_h_h"),
        "coverage": sub.get("coverage"),
        "note": sub.get("note"),
    }
    res = {
        "src": str(video.resolve()),
        "duration_s": round(dur, 2),
        "fps": int(round(info["fps"])) if abs(info["fps"] - round(info["fps"])) < 0.01 else round(info["fps"], 3),
        "w": info["w"], "h": info["h"],
        "cuts": cstats,
        "lengths": [round(x, 3) for x in lengths],
        "hook": hook,
        "subtitle": subtitle_out,
        "bigword": [{"t": b["t"], "h_h": b["h_h"], "dur_s": b["dur_s"]} for b in bigwords],
        "medium_changes": medium,
        "beats": beats,
        "beats_note": beats_note,
        "audio": audio,
        "transcript_src": tsrc,
        "method": {
            "cuts": "相邻帧平均差，阈值 med+5.5×MAD（夹 0.022–0.045），±0.25s 取局部峰、0.9s 内合并",
            "subtitle": "全画幅行剖面 std + 边缘密度；选定带后数亮字列剖面换行",
            "bigword": "行边缘密度相对该行基线的增量 >0.25、块高 ≥4.5%H、持续 ≥0.5s",
            "medium": "镜头签名（亮度/边缘）距离的 p75 以上，加全屏级大字",
            "beats": "转写信号词 + 切点密度突变；hook 固定 0–20%",
        },
    }
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.json_only:
        human(res)
        print(f"  → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
