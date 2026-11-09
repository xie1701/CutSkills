#!/usr/bin/env python3
"""音效到底听不听得见——量出来，别靠嘴说。

为什么要有这个：`mix_sfx.py` 只保证「合成进成片了」，不保证「听得见」。
2026-11-08 就撞过一次：音效在 loudnorm **之后**才混，归一把人声压回去的同时
也把音效抹平了，成片里 whoosh 只剩 1–3 dB 的抖动，报告却一路绿。

判据只在 **2–8 kHz** 这一条带上比：pop / whoosh / impact 的能量都在这儿，
口播男声的基频和垫乐的低频不在。拿全带能量比，人一说重音就分不清是谁的功劳。

对照组很关键：光看「卡片那一刻比前 0.5 秒响」会把人声重音算成音效。
所以同一张卡再取一个 t+1.0s 的时刻当对照，两边的抬升差才是音效自己的份量。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 48000
BAND = (2000, 8000)
WIN = 0.15          # 音效自身窗口：卡入场后 150ms
# 基线取**三段的中位数**，不再只看「前 0.6 秒那一段」。
# 有垫乐之后，单段基线会被音乐自己的鼓点带走：实测同一个 whoosh 在无垫乐时
# 抬 18 dB，垫乐进来后基线那 0.4 秒里正好有一下 hi-hat，算出来只剩 8 dB。
# 三个窗口（前、更前、后）取中位数，鼓点的偶发影响就被压掉了。
BASE_GAPS = ((-0.65, -0.35), (-1.45, -1.15), (0.90, 1.20))
PASS_DB = 3.0       # 音效相对基线的抬升；低于这个数，观众根本听不出来
CTRL_OFF = 1.0      # 对照组偏移


def load_mono(path: Path) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp = Path(tf.name)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1",
                    "-ar", str(SR), "-c:a", "pcm_s16le", str(tmp)], check=True)
    with wave.open(str(tmp), "rb") as w:
        raw = w.readframes(w.getnframes())
    tmp.unlink(missing_ok=True)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def band_db(x: np.ndarray, a: float, b: float) -> float:
    i0, i1 = max(0, int(a * SR)), min(len(x), int(b * SR))
    if i1 - i0 < 64:
        return -120.0
    seg = x[i0:i1]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1 / SR)
    m = (freqs >= BAND[0]) & (freqs <= BAND[1])
    e = float(np.mean(spec[m] ** 2)) if m.any() else 1e-12
    return 10 * np.log10(max(e, 1e-12))


def lift(x: np.ndarray, t: float) -> float:
    on = band_db(x, t, t + WIN)
    off = float(np.median([band_db(x, max(0.0, t + a), t + b) for a, b in BASE_GAPS[:-1]]
                          + [band_db(x, t + BASE_GAPS[2][0], t + BASE_GAPS[2][1])]))
    return on - off


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    proj = Path(a.project).expanduser()
    film = proj / "out" / "film-hf.mp4"
    sfx_p = proj / "out" / "sfx.json"
    if not film.exists():
        print("没有成片", file=sys.stderr)
        return 2
    if not sfx_p.exists():
        print("没有 out/sfx.json（这张片子没混过音效）", file=sys.stderr)
        return 3

    ev = json.loads(sfx_p.read_text()).get("events") or []
    if not ev:
        print("sfx.json 里一个事件都没有", file=sys.stderr)
        return 3

    x = load_mono(film)
    dur = len(x) / SR
    rows, passes = [], 0
    for e in ev:
        t = float(e["t"])
        if t + WIN >= dur:
            continue
        on = lift(x, t)
        ctrl = lift(x, min(dur - WIN - 0.05, t + CTRL_OFF))
        own = on - ctrl          # 扣掉人声/垫乐本来就在那儿的抬升
        ok = own >= PASS_DB
        passes += ok
        rows.append((t, str(e.get("name")), float(e.get("gain_db") or 0), on, ctrl, own, ok))

    print(f"音效可听度：{passes}/{len(rows)} 处过（判据 2–8 kHz 抬升 ≥ {PASS_DB:.1f} dB，已扣对照组）")
    for t, name, g, on, ctrl, own, ok in rows:
        print(f"  {'✅' if ok else '✗ '} {t:6.2f}s {name:<8} 增益 {g:+.1f}dB  抬升 {on:+.1f}dB"
              f"  对照 {ctrl:+.1f}dB  自有 {own:+.1f}dB")
    if passes < len(rows):
        print("\n不够响的原因通常不是音量：口播是连续人声，音效落在这上面只涨 1–3 dB。"
              "\n要的是给音效让位——在音效窗口里压 90ms 的人声（duck），不是把音效推到 −3dB。")
    return 0 if passes == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
