#!/usr/bin/env python3
"""CutSkills · 代码合成 BGM：按需求/观点的情绪出一段垫乐，不用去翻素材库。

为什么默认自己合成：找一首「合适的」曲子要花时间，还带版权风险；而口播片的 BGM 只干一件事——
**给节奏一个底**（观众几乎不会注意到它，注意到就说明太大了）。合成能精确控制：要多长有多长、
要多低有多低、情绪可参数化。

四套情绪（`--mood`）：
  warm     暖 · 慢（默认给「讲道理、劝人」的片子）
  minimal  极简 · 稳（讲事实、报价格）
  tense    低音持续 · 紧（拆穿、对比、风险提示）
  hopeful  明亮 · 上行（结尾收束、方案）

参数全在 MOODS 里，改情绪就是改一张表，不用改代码。

**版权**：这段曲子由本脚本现场合成，不引用任何录音制品，因此没有第三方版权。
如果用户指定了别的曲子（`--bgm <文件>`），`mix_bgm.py` 会先跑一遍风险评估再混：
有风险只提示、不阻断——但会把判断记进 decisions.md。

用法：
  bgm_gen.py --project <项目> [--mood warm] [--dur 79] [--out bgm.wav]
  bgm_gen.py --moods        # 列出四套情绪的参数
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SR = 48000

# dir: 三和弦走向（根音 Hz）；pulse: 每小节几下（0=不要节奏）；level: 各层增益
MOODS = {
    "warm": {"label": "暖 · 慢", "root": 110.0, "chords": [[0, 4, 7], [0, 3, 7], [-2, 3, 5], [0, 4, 7]],
             "bpm": 72, "pulse": 0, "levels": {"pad": 0.55, "bass": 0.22, "air": 0.05}},
    "minimal": {"label": "极简 · 稳", "root": 98.0, "chords": [[0, 7, 12], [0, 7, 10]],
                "bpm": 84, "pulse": 1, "levels": {"pad": 0.40, "bass": 0.28, "air": 0.03}},
    "tense": {"label": "低音持续 · 紧", "root": 73.42, "chords": [[0, 1, 7], [0, 1, 6]],
              "bpm": 92, "pulse": 2, "levels": {"pad": 0.34, "bass": 0.40, "air": 0.04}},
    "hopeful": {"label": "明亮 · 上行", "root": 130.81, "chords": [[0, 4, 7], [2, 7, 11], [4, 9, 12], [0, 4, 7]],
                "bpm": 88, "pulse": 1, "levels": {"pad": 0.50, "bass": 0.20, "air": 0.06}},
}


def synth(mood: str, dur: float) -> list[float]:
    import numpy as np
    m = MOODS[mood]
    n = int(SR * dur)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype="float32")

    def note(freq: float, start: float, length: float, gain: float, shape: str = "pad") -> None:
        a, b = int(start * SR), int(min(dur, start + length) * SR)
        if b <= a:
            return
        tt = np.arange(b - a) / SR
        if shape == "pad":
            # 三角波的三次谐波叠加：比正弦有内容，比锯齿干净（不刺耳）
            w = (np.sin(2 * np.pi * freq * tt) * 0.6
                 + np.sin(2 * np.pi * freq * 2 * tt) * 0.12
                 + np.sin(2 * np.pi * freq * 3 * tt) * 0.05)
            env = np.minimum(1.0, tt / 0.6) * np.minimum(1.0, (length - tt) / 0.8)
        else:                                    # bass
            w = np.sin(2 * np.pi * freq * tt) + 0.15 * np.sin(2 * np.pi * freq * 2 * tt)
            env = np.minimum(1.0, tt / 0.25) * np.exp(-tt * 0.8)
        out[a:b] += (w * env * gain).astype("float32")

    bar = 60.0 / m["bpm"] * 4
    i = 0
    while i * bar < dur:
        chord = m["chords"][i % len(m["chords"])]
        for semi in chord:
            f = m["root"] * (2 ** (semi / 12))
            note(f, i * bar, min(bar * 1.05, dur), m["levels"]["pad"] / len(chord))
        note(m["root"] / 2, i * bar, bar * 0.9, m["levels"]["bass"], "bass")
        for k in range(m["pulse"]):
            note(m["root"] * 2, i * bar + k * bar / max(1, m["pulse"] * 2), 0.35,
                 m["levels"]["air"], "bass")
        i += 1

    # 轻微的呼吸起伏：0.1 Hz 的慢摆，避免整段一样平（人耳会听出「垫音在嗡」）
    out *= (0.92 + 0.08 * np.sin(2 * np.pi * 0.1 * t)).astype("float32")
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.7).tolist()


def write_wav(path: Path, samples: list[float]) -> None:
    import numpy as np
    a = np.asarray(samples, dtype="float32")
    pcm = np.clip(a, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--mood", default="warm", choices=sorted(MOODS))
    ap.add_argument("--dur", type=float, default=None, help="秒；默认取 timeline 片长")
    ap.add_argument("--out", default=None)
    ap.add_argument("--moods", action="store_true")
    a = ap.parse_args()

    if a.moods:
        for k, m in MOODS.items():
            print(f"{k:<8} {m['label']:<10} {m['bpm']}bpm 脉冲 {m['pulse']}  "
                  f"{len(m['chords'])} 个和弦")
        return 0

    if a.project:
        proj = Path(a.project).expanduser().resolve()
        tl = proj / "timeline.json"
        if a.dur is None and tl.exists():
            a.dur = float(json.loads(tl.read_text()).get("duration_s") or 0) or None
        out = Path(a.out) if a.out else proj / "bgm.wav"
    else:
        if a.dur is None:
            raise SystemExit("要么给 --project（读片长），要么给 --dur")
        out = Path(a.out or "bgm.wav")

    dur = float(a.dur or 60.0)
    write_wav(out, synth(a.mood, dur))
    print(f"{MOODS[a.mood]['label']} · {dur:.1f}s → {out}（{out.stat().st_size/1e6:.1f} MB，代码合成，无第三方版权）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
