#!/usr/bin/env python3
"""CutSkills · 按时间点把音效混进一条人声轨。

为什么不用 `ffmpeg amix`：amix 默认 `normalize=1`，会按输入条数把每条都压一遍——
贴 4 处音效，人声整体掉 12dB。这里的做法是把每处音效先放进一条和人声等长的空轨，
再用 numpy 直接相加，人声音量一动不动，只有音效叠上去。

cues.json（`t` 单位秒，`gain_db` 相对音效素材本身的 -6dBFS 峰值）：
  [{"t": 3.2, "name": "whoosh", "gain_db": -14},
   {"t": 8.0, "name": "pop",    "gain_db": -10}]

输出与人声轨同帧率/同声道数/同采样率/同长度（sample 级一致），16bit PCM。
同一份 cues 重复跑，输出字节一致——不引入任何随机量。

用法：
  mix_sfx.py --audio work/audio_norm.wav --cues cues.json --out work/audio_sfx.wav
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

CEILING_DB = -0.1        # 混后峰值硬顶：只防溢出，不代替最后的 loudnorm


def sfx_dir() -> Path:
    root = os.environ.get("CUTSKILLS_HOME") or os.path.expanduser("~/.cutskills")
    return Path(root).expanduser() / "assets" / "sfx"


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=sample_rate,channels,codec_name:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = (d.get("streams") or [{}])[0]
    return {"sr": int(s.get("sample_rate") or 0), "ch": int(s.get("channels") or 0),
            "dur": float(d.get("format", {}).get("duration") or 0),
            "codec": s.get("codec_name")}


def decode(path: Path, sr: int, ch: int) -> np.ndarray:
    """统一解码成 (n, ch) float32。让 ffmpeg 干格式兼容的脏活，numpy 只算混音。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
         "-acodec", "pcm_f32le", "-ac", str(ch), "-ar", str(sr), "-"],
        capture_output=True, check=True).stdout
    y = np.frombuffer(raw, dtype="<f4")
    return y.reshape(-1, ch).astype(np.float32, copy=True)


def resample(y: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性插值够用：音效都是噪声/瞬态，重采样只改长度不改性格。"""
    if src_sr == dst_sr:
        return y
    n_out = max(1, int(round(len(y) * dst_sr / src_sr)))
    x_old = np.linspace(0.0, 1.0, len(y), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, y).astype(np.float32)


def load_sfx(path: Path, sr: int, ch: int) -> tuple[np.ndarray, float]:
    """返回 (按目标声道铺开的素材, 素材自身峰值 dBFS)——峰值是量出来的，不是照 -6dBFS 假设。"""
    info = ffprobe(path)
    y = decode(path, info["sr"], info["ch"])
    mono = resample(y.mean(axis=1), info["sr"], sr)
    return np.repeat(mono[:, None], ch, axis=1), db(float(np.max(np.abs(mono))))


def db(x: float) -> float:
    return 20 * np.log10(max(float(x), 1e-9))


def main() -> int:
    ap = argparse.ArgumentParser(description="把音效按 cues 混进人声轨（numpy 相加，不用 amix）")
    ap.add_argument("--audio", required=True, help="人声轨 wav（混音的底）")
    ap.add_argument("--cues", required=True, help="cues.json：[{t, name, gain_db}]")
    ap.add_argument("--out", required=True, help="输出 wav")
    ap.add_argument("--sfx-dir", default=None, help="音效目录（默认 $CUTSKILLS_HOME/assets/sfx）")
    ap.add_argument("--ceiling-db", type=float, default=CEILING_DB,
                    help=f"混后峰值硬顶（默认 {CEILING_DB} dBFS）。人声轨若已经过 loudnorm，"
                         "它的样本峰本身就贴着 -1dBTP，硬顶设得更严只会把音效压没")
    a = ap.parse_args()

    audio, cues_p, out = Path(a.audio), Path(a.cues), Path(a.out)
    for p, what in ((audio, "人声轨"), (cues_p, "cues")):
        if not p.exists():
            print(f"找不到{what}：{p}", file=sys.stderr)
            return 2

    info = ffprobe(audio)
    sr, ch, dur = info["sr"], info["ch"], info["dur"]
    main = decode(audio, sr, ch)
    n = len(main)

    try:
        cues = json.loads(cues_p.read_text(encoding="utf-8"))
        assert isinstance(cues, list)
    except (json.JSONDecodeError, AssertionError):
        print(f"cues 不是合法的 JSON 数组：{cues_p}", file=sys.stderr)
        return 2

    sd = Path(a.sfx_dir).expanduser() if a.sfx_dir else sfx_dir()
    avail = {p.stem: p for p in sorted(sd.glob("*.wav"))}
    if not avail:
        print(f"音效目录是空的：{sd}\n先跑 scripts/gen_sfx.py", file=sys.stderr)
        return 2

    bed = np.zeros_like(main)
    placed = []
    for i, c in enumerate(cues):
        try:
            t, name = float(c["t"]), str(c["name"])
            gain = float(c.get("gain_db", -12.0))
        except (KeyError, TypeError, ValueError):
            print(f"第 {i + 1} 条 cue 缺 t / name：{c}", file=sys.stderr)
            return 2
        if name not in avail:
            print(f"第 {i + 1} 条 cue 要的 {name!r} 不在 {sd}\n可选：{', '.join(avail)}",
                  file=sys.stderr)
            return 2
        if t < 0 or t >= dur:
            print(f"第 {i + 1} 条 cue 的 t={t} 越界（轨道 {dur:.3f}s）", file=sys.stderr)
            return 2

        sfx, src_peak = load_sfx(avail[name], sr, ch)
        sfx = sfx * 10 ** (gain / 20.0)
        start = int(round(t * sr))
        room = n - start
        cut = sfx[:room]
        bed[start:start + len(cut)] += cut
        placed.append({"t": t, "name": name, "gain_db": gain, "src_peak": src_peak,
                       "dur": round(len(sfx) / sr, 3),
                       "truncated": round(max(0.0, (len(sfx) - room) / sr), 3)})

    peak_main = float(np.max(np.abs(main)))
    ceiling = 10 ** (a.ceiling_db / 20.0)
    bed_drop = 0.0
    if float(np.max(np.abs(main + bed))) > ceiling:
        # 只压音效床，不碰人声：主轨已经被上一道 loudnorm 归到 -1 dBTP，
        # 再整轨降就等于把响度目标改了；这里让音效让路，人声一动不动。
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if float(np.max(np.abs(main + bed * mid))) <= ceiling:
                lo = mid
            else:
                hi = mid
        if lo > 1e-3:
            bed = bed * lo
            bed_drop = -db(lo)
        else:                      # 音效全撤也压不下来：主轨自己就贴着/过了硬顶
            bed = np.zeros_like(bed)
    mixed = main + bed
    peak_mix = float(np.max(np.abs(mixed)))
    clipped = int(np.count_nonzero(np.abs(mixed) > 1.0))

    pcm = np.clip(np.round(mixed * 32767), -32768, 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())

    muted = peak_main > ceiling
    print(f"贴了 {len(placed)} 处：")
    for p in placed:
        note = f"  ← 尾部被截 {p['truncated']:.2f}s" if p["truncated"] else ""
        if muted:
            level = "未混入"
        else:
            level = f"峰值落到 {p['src_peak'] + p['gain_db'] - bed_drop:.0f}dBFS"
        print(f"  {p['t']:7.2f}s  {p['name']:7s} {p['gain_db']:+.0f}dB"
              f"（{level}，长约 {p['dur']:.2f}s）{note}")
    print(f"主轨峰值 {db(peak_main):.1f} dBFS  →  混后峰值 {db(peak_mix):.1f} dBFS"
          f"（上限 {a.ceiling_db:+.1f}）")
    if bed_drop:
        print(f"  音效床整体再降 {bed_drop:.1f} dB 换 headroom，人声未动")
    if muted:
        print(f"  警告：主轨本身就在 {db(peak_main):+.1f} dBFS，已过高顶 "
              f"{a.ceiling_db:+.1f} dBFS，音效全部撤掉；要么放宽 --ceiling-db，"
              f"要么先降人声")
    print(f"削波 " + (f"有！{clipped} 个样本过 1.0" if clipped else "无")
          + f"   时长 {n / sr:.3f}s（原轨 {dur:.3f}s，{sr}Hz/{ch}ch）")
    print(f"输出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
