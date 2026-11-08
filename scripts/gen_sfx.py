#!/usr/bin/env python3
"""CutSkills · 合成音效素材：一次算完落盘，之后只读复用。

参考片里音效是标点（8.5 处/分是上限，不是节拍器），可产线一直没这一层：
模板写着 `sfx_density_per_min`，实测 0.0。素材库又不能靠下载——依赖只装一次、
不许重复下载，所以这里用 numpy 现场合成。

四个音各管一件事，全部是短促的机械音，不带音高旋律：
  whoosh  切点     带通噪声从低扫到高，0.33s，快起慢收
  pop     大字入场 一个低频冲击 + 一记高频咔哒，0.10s
  impact  转折     低频下扫 + 短噪声尾巴，0.42s
  tick    列表/数字 0.05s 的极短嗒声

落盘位置：`${CUTSKILLS_HOME:-~/.cutskills}/assets/sfx/<name>.wav`（48k / 单声道 / 16bit），
**不写进仓库**。已存在就跳过，所以可以反复跑；要重做单个用 `--force`。

用法：
  gen_sfx.py
  gen_sfx.py --name whoosh --force
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
PEAK_DBFS = -6.0          # 统一峰值：够响但不给混音留麻烦
FADE_IN = 0.001           # 两端各留 1–5ms 淡变，避免起止的咔哒
FADE_OUT = 0.005


def sfx_dir() -> Path:
    root = os.environ.get("CUTSKILLS_HOME") or os.path.expanduser("~/.cutskills")
    return Path(root).expanduser() / "assets" / "sfx"


def _env_ad(n: int, attack: float, decay: float) -> np.ndarray:
    """快起慢收：attack 决定多少毫秒冲到顶，decay 是指数尾巴的时间常数。"""
    t = np.arange(n) / SR
    return (1.0 - np.exp(-t / max(1e-5, attack))) * np.exp(-t / max(1e-5, decay))


def _bandpass_sweep(x: np.ndarray, f_start: float, f_end: float, q: float,
                    block: int = 96) -> np.ndarray:
    """中心频率随时间指数扫动的带通。逐块换系数、逐样本过状态，纯 numpy 不引 scipy。

    块长 96（2ms）足够让扫动听出来，又不会因为系数跳变产生咔哒。
    """
    n = len(x)
    y = np.zeros(n)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(0, n, block):
        k = i / max(1, n - 1)
        f0 = min(SR * 0.45, f_start * (f_end / f_start) ** k)
        w0 = 2 * np.pi * f0 / SR
        alpha = np.sin(w0) / (2 * q)
        a0 = 1 + alpha
        b0, b1, b2 = alpha / a0, 0.0, -alpha / a0
        a1, a2 = -2 * np.cos(w0) / a0, (1 - alpha) / a0
        seg = x[i:i + block]
        for j in range(len(seg)):
            xi = seg[j]
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xi, y1, yi
            y[i + j] = yi
    return y


def _bp(x: np.ndarray, f_start: float, f_end: float, q: float,
        order: int = 2) -> np.ndarray:
    """级联带通。单级 biquad 的裙边只有 12dB/oct，白噪声从裙边漏出来的高频
    会把质心拉到 4–6kHz（听起来是"嘶"不是"风"）；两级就够压回中频。"""
    y = x
    for _ in range(order):
        y = _bandpass_sweep(y, f_start, f_end, q)
    return y


def _glide(f_start: float, f_end: float, n: int, tau: float) -> np.ndarray:
    """指数滑音的生相位（频率高→低或低→高，不做持续的音高）。"""
    t = np.arange(n) / SR
    f = f_end + (f_start - f_end) * np.exp(-t / max(1e-5, tau))
    return 2 * np.pi * np.cumsum(f) / SR


def _norm(y: np.ndarray) -> np.ndarray:
    """去直流 → 两端淡变 → 再定峰值。顺序不能反：先定峰值再淡入，
    峰值若正好落在头 1ms（tick 就是），报出来的峰值会比目标低 1–2dB。"""
    y = y - y.mean()
    fi, fo = int(FADE_IN * SR), int(FADE_OUT * SR)
    if fi:
        y[:fi] *= np.linspace(0, 1, fi)
    if fo:
        y[-fo:] *= np.linspace(1, 0, fo)
    peak = float(np.max(np.abs(y))) or 1.0
    y = y / peak * 10 ** (PEAK_DBFS / 20)
    return y.astype(np.float32)


def make_whoosh() -> np.ndarray:
    """切点用：三层错开的带通噪声往上扫，听感是"一阵风从画面里穿过去"。"""
    rng = np.random.default_rng(20261108)
    n = int(0.33 * SR)
    noise = rng.standard_normal(n)
    layers = (
        (0.7, 350, 1200, 0.9, 1.0),     # 底噪：中低频，风的"身体"
        (1.0, 600, 2800, 1.4, 1.0),     # 主体
        (1.3, 900, 4600, 1.8, 0.55),    # 高频"嘶"，让扫过去有方向
    )
    y = np.zeros(n)
    for gain, f0, f1, q, w in layers:
        y += w * _bp(noise, f0 * gain, f1 * gain, q)
    # 幅度包络：8ms 冲到位，之后指数收；最后整段按 0.45 次幂压到零，尾巴才干净
    t = np.arange(n) / SR
    env = (1 - np.exp(-t / 0.008)) * np.exp(-t / 0.16)
    env *= np.minimum(1.0, (1 - t / t[-1]) ** 0.45)
    return _norm(y * env)


def make_pop() -> np.ndarray:
    """大字/花字入场：短促的低频冲击（下扫）+ 一记高频咔哒。"""
    rng = np.random.default_rng(17)
    n = int(0.10 * SR)
    thump = np.sin(_glide(220, 62, n, 0.028))
    thump *= _env_ad(n, 0.0018, 0.020)
    thump = np.tanh(thump * 1.8) / np.tanh(1.8)      # 轻饱和，冲击更实
    click = _bp(rng.standard_normal(n), 3600, 3000, 1.0)
    click *= _env_ad(n, 0.0004, 0.0016)
    y = 1.0 * thump + 0.6 * click
    return _norm(y)


def make_impact() -> np.ndarray:
    """段落转折：低频从 150 砸到 40Hz，尾巴是一段快速收干的中频噪声。"""
    rng = np.random.default_rng(99)
    n = int(0.42 * SR)
    low = np.sin(_glide(150, 40, n, 0.06))
    low += 0.25 * np.sin(2 * _glide(150, 40, n, 0.06))   # 一点二次谐波，低频才听得见
    low *= _env_ad(n, 0.004, 0.11)
    low = np.tanh(low * 1.6) / np.tanh(1.6)
    tail = _bp(rng.standard_normal(n), 1800, 300, 0.9)
    tail *= _env_ad(n, 0.002, 0.045)
    body = _bp(rng.standard_normal(n), 800, 400, 0.7)
    body *= _env_ad(n, 0.003, 0.07)
    return _norm(1.0 * low + 0.45 * tail + 0.35 * body)


def make_tick() -> np.ndarray:
    """列表/数字出现：0.05s，一记干脆的嗒声，没有余音。"""
    rng = np.random.default_rng(7)
    n = int(0.05 * SR)
    click = _bp(rng.standard_normal(n), 3200, 2600, 1.2)
    click *= _env_ad(n, 0.0003, 0.0022)
    ring = _bp(rng.standard_normal(n), 1500, 1400, 6.0)
    ring *= _env_ad(n, 0.0004, 0.0045)
    y = 1.0 * click + 0.55 * ring
    return _norm(y)


BUILDERS = {"whoosh": make_whoosh, "pop": make_pop,
            "impact": make_impact, "tick": make_tick}
DESC = {"whoosh": "切点", "pop": "大字/花字入场",
        "impact": "段落转折", "tick": "列表/数字出现"}


def write_wav(path: Path, y: np.ndarray) -> None:
    pcm = np.clip(np.round(y * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def probe(path: Path) -> dict:
    """时长/规格交给 ffprobe，峰值交给 ffmpeg astats —— 和自己算的不互相印证。"""
    info = {"dur": None, "sr": None, "ch": None, "peak_db": None}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=sample_rate,channels", "-of", "json", str(path)],
            capture_output=True, text=True, check=True).stdout
        d = json.loads(out)
        s = (d.get("streams") or [{}])[0]
        info["dur"] = float(d.get("format", {}).get("duration") or 0)
        info["sr"] = s.get("sample_rate")
        info["ch"] = s.get("channels")
    except (subprocess.CalledProcessError, ValueError, KeyError):
        pass
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                            "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
                           capture_output=True, text=True)
        for line in (r.stderr or "").splitlines():
            if "Peak level dB" in line:
                info["peak_db"] = float(line.split(":")[-1])
    except (subprocess.CalledProcessError, ValueError):
        pass
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="合成 CutSkills 音效素材（幂等）")
    ap.add_argument("--name", choices=sorted(BUILDERS), help="只做这一个")
    ap.add_argument("--force", action="store_true", help="已存在也重做")
    ap.add_argument("--dir", default=None, help="输出目录（默认 $CUTSKILLS_HOME/assets/sfx）")
    a = ap.parse_args()

    if shutil.which("ffprobe") is None:
        print("找不到 ffprobe，没法报时长/峰值", file=sys.stderr)
        return 2

    out_dir = Path(a.dir).expanduser() if a.dir else sfx_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [a.name] if a.name else list(BUILDERS)

    print(f"音效目录 {out_dir}  （{SR}Hz / 单声道 / 16bit）")
    rows = []
    for name in names:
        p = out_dir / f"{name}.wav"
        if p.exists() and not a.force:
            st = "复用"
        else:
            write_wav(p, BUILDERS[name]())
            st = "新建"
        info = probe(p)
        rows.append((name, p, st, info))
        print(f"  {name:7s} {st}  {info['dur'] or 0:5.3f}s  "
              f"峰值 {info['peak_db']:.1f} dBFS  {info['sr']}Hz/{info['ch']}ch  ({DESC[name]})")

    bad = [r[0] for r in rows if r[3]["peak_db"] is None or r[3]["peak_db"] > -0.3]
    if bad:
        print(f"警告：{', '.join(bad)} 峰值异常（应 ≈{PEAK_DBFS:.0f} dBFS 且不削波）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
