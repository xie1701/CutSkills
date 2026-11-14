#!/usr/bin/env python3
"""音效可听度检查：优先与混音前同包络底轨（stem-nosfx.m4a）同窗相减，按音色真实频段测瞬态净抬升（T-059）。

- 低频冲击类（punch / boom）：测量 45–500 Hz 低频能量带；
- 铃音旋律类（ding / chime / money / shimmer / appear / title）：测量 500–8000 Hz 中高频带；
- 脆响擦过类（pop / impact / whoosh / swish / click / flash / riser）：测量 1000–8000 Hz 瞬态频带。
- 在事件触发区间 `[t - 0.03, t + 0.18]` 内按人耳 70ms 时间积分窗搜索瞬态峰值净抬升，避免 60ms 短促音效被长窗稀释。

用法：
  check_sfx.py --project <项目目录>
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libs import ffmpeg_bin  # noqa: E402

SR = 48000
SLICE_S = 0.055
SPAN_PRE = 0.03
SPAN_POST = 0.18
BASE_GAPS = ((-0.65, -0.35), (-1.45, -1.15), (0.90, 1.20))
PASS_DB = 2.5


def band_for_event(name: str) -> tuple[int, int]:
    """按 24 条内置 CC0 音效实测主能量频段路由（原则 B12）：
    - 低频重击（punch / boom-* / impact-heavy-*）：主能量在 45–500 Hz；
    - 气泡/轻敲/拍击（pop-* / click-* / impact-light-*）：主能量集中在 200–2200 Hz（>2.2kHz 衰减近 28dB，混入高频只会被人声齿音稀释）；
    - 其余铃音/擦过/金币/闪光（ding / whoosh / swish / shimmer / money / appear / title）：覆盖 300–6000 Hz。
    """
    n = (name or "").lower()
    if n in ("punch",) or n.startswith("boom") or n.startswith("impact-heavy"):
        return (45, 500)
    if n in ("pop", "click", "impact", "keyword") or any(
        n.startswith(p) for p in ("pop-", "click-", "impact-light")
    ):
        return (200, 2200)
    return (300, 6000)


def load_mono(path: Path) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp = Path(tf.name)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(path), "-ac", "1",
                    "-ar", str(SR), "-c:a", "pcm_s16le", str(tmp)], check=True)
    with wave.open(str(tmp), "rb") as w:
        raw = w.readframes(w.getnframes())
    tmp.unlink(missing_ok=True)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def band_db(x: np.ndarray, a: float, b: float, band: tuple[int, int] = (1000, 8000)) -> float:
    i0, i1 = max(0, int(a * SR)), min(len(x), int(b * SR))
    if i1 - i0 < 64:
        return -120.0
    seg = x[i0:i1]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1 / SR)
    m = (freqs >= band[0]) & (freqs <= band[1])
    e = float(np.mean(spec[m] ** 2)) if m.any() else 1e-12
    return 10 * np.log10(max(e, 1e-12))


def median_band_offset(x_film: np.ndarray, x_bed: np.ndarray,
                       ev_times: list[float], band: tuple[int, int] = (1000, 8000)) -> float:
    """在远离音效事件的干净窗口估算 loudnorm 带来的整体电平平移。"""
    dur = min(len(x_film), len(x_bed)) / SR
    diffs = []
    t = 0.2
    while t + 0.25 < dur:
        if all(abs(t - et) > 0.35 for et in ev_times):
            bf = band_db(x_film, t, t + 0.20, band)
            bb = band_db(x_bed, t, t + 0.20, band)
            if bf > -95 and bb > -95:
                diffs.append(bf - bb)
        t += 0.30
    return float(np.median(diffs)) if diffs else 0.0


def best_transient_lift(x_film: np.ndarray, x_bed: np.ndarray | None,
                        t: float, offset_db: float, band: tuple[int, int]) -> tuple[float, float, float]:
    """在 [t - 0.03, t + 0.18] 内按 70ms 听觉积分窗取最大瞬态净抬升。"""
    dur = len(x_film) / SR
    t_start = max(0.0, t - SPAN_PRE)
    t_end = min(dur - SLICE_S, t + SPAN_POST - SLICE_S)
    if t_end < t_start:
        t_end = t_start

    best_own = -999.0
    best_pair = (-120.0, -120.0)
    cur = t_start
    while cur <= t_end + 1e-6:
        w1 = min(dur, cur + SLICE_S)
        f_db = band_db(x_film, cur, w1, band)
        if x_bed is not None and w1 * SR <= len(x_bed) + SR * 0.1:
            b_db = band_db(x_bed, cur, w1, band) + offset_db
        else:
            b_db = float(np.median([
                band_db(x_film, max(0.0, t + a), max(0.07, t + b), band) for a, b in BASE_GAPS
            ]))
        own = f_db - b_db
        if own > best_own:
            best_own = own
            best_pair = (f_db, b_db)
        cur += 0.025
    return best_pair[0], best_pair[1], best_own


def check_project(proj: Path) -> tuple[int, int, list[dict], str]:
    film = proj / "out" / "film-hf.mp4"
    sfx_p = proj / "out" / "sfx.json"
    stem_p = proj / "out" / "stem-nosfx.m4a"
    ev = json.loads(sfx_p.read_text()).get("events") or []

    x_film = load_mono(film)
    dur = len(x_film) / SR
    has_stem = stem_p.exists()
    x_bed = load_mono(stem_p) if has_stem else None
    ev_times = [float(e["t"]) for e in ev]
    mode = "混音前后同窗净抬升" if has_stem else "三段邻窗基线回退"

    offset_cache: dict[tuple[int, int], float] = {}
    rows, passes = [], 0
    for e in ev:
        t = float(e["t"])
        if t + SLICE_S >= dur:
            continue
        name = str(e.get("name") or "pop")
        band = band_for_event(name)
        if has_stem and x_bed is not None:
            if band not in offset_cache:
                offset_cache[band] = median_band_offset(x_film, x_bed, ev_times, band)
            off = offset_cache[band]
        else:
            off = 0.0
        on_db, bed_db, own = best_transient_lift(
            x_film, x_bed if has_stem else None, t, off, band
        )
        ok = bool(own >= PASS_DB)
        passes += int(ok)
        rows.append({
            "t": round(t, 2),
            "name": name,
            "band": f"{band[0]}-{band[1]}Hz",
            "gain_db": float(e.get("gain_db") or 0),
            "film_db": round(on_db, 1),
            "bed_db": round(bed_db, 1),
            "own_db": round(own, 1),
            "ok": ok,
        })
    return passes, len(rows), rows, mode


def main() -> int:
    ap = argparse.ArgumentParser(description="检查成片每处音效在对应声学频段的真实净抬升可听度")
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    if not (proj / "out" / "film-hf.mp4").exists():
        print("没有成片", file=sys.stderr)
        return 2
    if not (proj / "out" / "sfx.json").exists():
        print("没有 out/sfx.json（这张片子没混过音效）", file=sys.stderr)
        return 3

    passes, total, rows, mode = check_project(proj)
    if total == 0:
        print("sfx.json 里没有可测事件", file=sys.stderr)
        return 3

    print(f"音效可听度（{mode}）：{passes}/{total} 处过（判据对应频段 70ms 瞬态净抬升 ≥ {PASS_DB:.1f} dB）")
    for r in rows:
        print(f"  {'✅' if r['ok'] else '✗ '} {r['t']:6.2f}s {r['name']:<16} [{r['band']:<11}] "
              f"成片 {r['film_db']:+6.1f}dB  底音 {r['bed_db']:+6.1f}dB  净抬升 {r['own_db']:+5.1f}dB")
    return 0 if passes == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
