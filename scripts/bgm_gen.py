#!/usr/bin/env python3
"""CutSkills · 代码合成 BGM：按需求/观点的情绪出一段垫乐，不用去翻素材库。

为什么默认自己合成：找一首「合适的」曲子要花时间，还带版权风险；而口播片的 BGM 只干一件事——
**给节奏一个底**（观众几乎不该注意到它，注意到就说明太大了）。合成能精确控制：要多长有多长、
情绪可参数化。

## 2026-11-09 返工：为什么第一版听着压抑

量出来的，不是感觉：

| 指标 | 第一版 warm | 讲清楚该在的位置 |
|---|---|---|
| 谱质心（亮度） | **165 Hz** | 350–1000 Hz |
| ≤120Hz 能量占比 | **0.432** | ≤0.20 |
| >1kHz 能量占比 | **0.002** | ≥0.05 |

原因三条：① 和弦根音写在 110Hz、低音又叠一层 55Hz 的正弦持续音——55Hz 在 1920 宽的竖屏里
就是一团低频嗡嗡，人耳对它只有「闷」这一个感受；② `pulse: 0`，整段没有任何节奏动作；
③ 和弦里带小三度（[0,3,7]），慢速持续 ＝ 悼乐。

改法：根音整体上移一个八度（110 → 174.6/196/220）、低音只做点缀不再铺底、
高八度加**木琴式短拨**给出节奏、pad 补上四五次谐波把亮度提起来、输出过 80Hz 高通砍掉次低频。
`clear`（讲清楚·亮）成为默认，`tense` 留给「拆穿/风险」这种真需要压的段落。

参数全在 MOODS 里，改情绪就是改一张表，不用改代码。

**版权**：这段曲子由本脚本现场合成，不引用任何录音制品，因此没有第三方版权。
如果用户指定了别的曲子（`--bgm <文件>`），`render_hf` 只提示风险、不阻断，并记进 decisions.md。

用法：
  bgm_gen.py --project <项目> [--mood auto] [--dur 79] [--out bgm.wav]
  bgm_gen.py --moods               # 列出五套情绪的参数
  bgm_gen.py --analyze <wav>       # 量一段 BGM 的亮度/低频，判断压不压
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SR = 48000

# chords: 相对根音的半音；pulse: 每小节哪几拍下一下短拨（单位：拍）；levels: 各层增益
MOODS = {
    "clear": {"label": "讲清楚 · 亮",
              "root": 196.00,                       # G3：人声基频区上方，不跟口播抢
              "chords": [[0, 4, 7, 12], [7, 11, 14, 19], [9, 12, 16, 19], [5, 9, 12, 17]],
              "bpm": 96, "pulse": [0, 2], "sparkle": 12,
              "levels": {"pad": 0.34, "pluck": 0.13, "bass": 0.12}},
    "warm": {"label": "暖 · 轻",
             "root": 174.61,
             "chords": [[0, 4, 7, 12], [5, 9, 12, 16], [7, 11, 14, 19], [0, 4, 7, 12]],
             "bpm": 84, "pulse": [0], "sparkle": 12,
             "levels": {"pad": 0.36, "pluck": 0.11, "bass": 0.13}},
    "minimal": {"label": "极简 · 稳",
                "root": 146.83,
                "chords": [[0, 7, 12], [0, 7, 10, 15]],
                "bpm": 88, "pulse": [0], "sparkle": 12,
                "levels": {"pad": 0.30, "pluck": 0.07, "bass": 0.10}},
    "tense": {"label": "低音 · 紧（拆穿/风险才用）",
              "root": 110.00,
              "chords": [[0, 1, 7], [0, 1, 6]],
              "bpm": 100, "pulse": [0, 1.5], "sparkle": 0,
              "levels": {"pad": 0.26, "pluck": 0.12, "bass": 0.20}},
    "hopeful": {"label": "明亮 · 上行",
                "root": 220.00,
                "chords": [[0, 4, 7, 12], [2, 7, 11, 14], [4, 9, 12, 16], [0, 4, 7, 12]],
                "bpm": 96, "pulse": [0, 1, 2, 3], "sparkle": 12,
                "levels": {"pad": 0.28, "pluck": 0.18, "bass": 0.10}},
}
DEFAULT_MOOD = "clear"

# 情绪选择：先看这部片子在讲什么，别再看我上次写在默认值里的那个字。
# 顺序有讲究——风险词优先（哪怕整片是暖调，一句「别被坑」也要稍微压一点）。
MOOD_HINTS = [
    ("tense", "坑 骗 陷阱 避坑 缩水 水分 隐藏 亏 涨价 套路 风险 猫腻 忽悠 冤"),
    ("hopeful", "建议 接下来 你可以 一起 第一步 开始做 怎么问 三步 行动"),
    ("minimal", "价格 报价 成本 数据 对比 表格 清单 多少 倍"),
    ("warm", "家 生活 舒服 感受 值得 喜欢 陪伴 心"),
]


def auto_mood(text: str) -> tuple[str, str]:
    """从需求文字里挑情绪，返回 (mood, 理由)。挑不出来就用默认。"""
    t = text or ""
    for mood, words in MOOD_HINTS:
        hit = [w for w in words.split() if w and w in t]
        if hit:
            return mood, f"需求里出现「{'、'.join(hit[:3])}」"
    return DEFAULT_MOOD, "需求里没有明显的情绪词，用默认的「讲清楚」"


def synth(mood: str, dur: float) -> list[float]:
    import numpy as np
    m = MOODS[mood]
    n = int(SR * dur)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype="float32")

    def note(freq: float, start: float, length: float, gain: float, shape: str = "pad") -> None:
        a, b = int(start * SR), int(min(dur, start + length) * SR)
        if b <= a or gain <= 0:
            return
        tt = np.arange(b - a) / SR
        if shape == "pad":
            # 三角波的四次谐波叠加：多加 4/5 次谐波是为了把谱质心抬到人耳敏感的区间，
            # 第一版只有 1/2/3 次，量出来 165Hz——那是低频嗡嗡，不是垫乐。
            w = (np.sin(2 * np.pi * freq * tt) * 0.55
                 + np.sin(2 * np.pi * freq * 2 * tt) * 0.16
                 + np.sin(2 * np.pi * freq * 3 * tt) * 0.09
                 + np.sin(2 * np.pi * freq * 4 * tt) * 0.05
                 + np.sin(2 * np.pi * freq * 5 * tt) * 0.03)
            env = np.minimum(1.0, tt / 0.45) * np.minimum(1.0, (length - tt) / 0.7)
        elif shape == "pluck":                   # 高八度的短拨：给节奏一个「点」，不占低频
            w = (np.sin(2 * np.pi * freq * tt) * 0.8
                 + np.sin(2 * np.pi * freq * 2 * tt) * 0.25
                 + np.sin(2 * np.pi * freq * 3 * tt) * 0.10)
            env = np.minimum(1.0, tt / 0.008) * np.exp(-tt * 5.5)
        else:                                    # bass：只做点缀，不铺底
            w = np.sin(2 * np.pi * freq * tt) + 0.12 * np.sin(2 * np.pi * freq * 2 * tt)
            env = np.minimum(1.0, tt / 0.2) * np.exp(-tt * 1.4)
        out[a:b] += (w * env * gain).astype("float32")

    bar = 60.0 / m["bpm"] * 4
    beat = bar / 4
    i = 0
    while i * bar < dur:
        chord = m["chords"][i % len(m["chords"])]
        for semi in chord:
            f = m["root"] * (2 ** (semi / 12))
            note(f, i * bar, min(bar * 1.02, dur), m["levels"]["pad"] / len(chord))
        # 低音只在小节线下来一下，长度不到一拍——垫底成持续音就会变闷。
        note(m["root"] / 2, i * bar, beat * 0.9, m["levels"]["bass"], "bass")
        sp = m.get("sparkle") or 0
        for k in m.get("pulse") or []:
            note(m["root"] * 2 ** (sp / 12), i * bar + k * beat, 0.45,
                 m["levels"]["pluck"], "pluck")
        i += 1

    # 80Hz 高通（一阶）：砍掉次低频的「嗡嗡」，别的都留着。
    rc = 1.0 / (2 * 3.141592653589793 * 80.0)
    alpha = rc / (rc + 1.0 / SR)
    hp = np.empty_like(out)
    prev_in = 0.0
    prev_out = 0.0
    for j in range(n):
        x = float(out[j])
        y = alpha * (prev_out + x - prev_in)
        hp[j] = y
        prev_in, prev_out = x, y
    out = hp
    # 轻微的呼吸起伏：0.1 Hz 慢摆，避免整段一样平
    out *= (0.94 + 0.06 * np.sin(2 * np.pi * 0.1 * t)).astype("float32")
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.7).tolist()


def analyze(path: Path) -> int:
    """量亮度与低频占比——判断这段 BGM 会不会听成「压抑」，别靠形容词。"""
    import wave

    import numpy as np
    w = wave.open(str(path))
    n = w.getnframes()
    sr = w.getframerate()
    a = np.frombuffer(w.readframes(n), dtype="<i2").astype("float32") / 32768
    seg = min(len(a), sr * 8)
    if seg < sr:
        raise SystemExit("太短，至少 1 秒")
    spec = np.abs(np.fft.rfft(a[:seg] * np.hanning(seg)))
    fr = np.fft.rfftfreq(seg, 1 / sr)
    tot = spec.sum() or 1.0
    low = spec[fr < 120].sum() / tot
    high = spec[fr >= 1000].sum() / tot
    centroid = float((spec * fr).sum() / tot)
    rms = float(np.sqrt((a ** 2).mean()))
    ok_low = low <= 0.20
    # 350 以下听着就是闷；1000 以上会跟人声的齿擦音抢位置（「嘶」声打架）。
    # 带拨点的预设会顶到 900 上下——那不是亮过头，是短拨的瞬态，落在拍子上就对了。
    ok_cen = 350 <= centroid <= 1000
    print(f"{path.name}：{len(a)/sr:.1f}s")
    print(f"  谱质心 {centroid:.0f} Hz{'✅' if ok_cen else '✗'}（350–1000，低于 350 就闷）")
    print(f"  ≤120Hz 占比 {low:.3f}{'✅' if ok_low else '✗'}（≤0.20）")
    print(f"  ≥1kHz 占比 {high:.3f}{'✅' if high >= 0.05 else '✗'}（≥0.05 才有「亮」）")
    print(f"  RMS {20*np.log10(max(rms, 1e-9)):.1f} dBFS")
    if not ok_low:
        print("  提示：低频这么重只有一种正当理由——你在用 tense（拆穿/风险）。"
              "讲清楚、讲道理、讲故事都别用它。")
    return 0 if (ok_low and ok_cen) else 1


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
    ap.add_argument("--mood", default="auto", choices=sorted(MOODS) + ["auto"])
    ap.add_argument("--dur", type=float, default=None, help="秒；默认取 timeline 片长")
    ap.add_argument("--out", default=None)
    ap.add_argument("--moods", action="store_true")
    ap.add_argument("--analyze", default=None, help="只量一段 wav，不合成")
    a = ap.parse_args()

    if a.analyze:
        return analyze(Path(a.analyze).expanduser())

    if a.moods:
        for k, m in MOODS.items():
            print(f"{k:<8} {m['label']:<26} 根音 {m['root']:6.1f}Hz  {m['bpm']}bpm  "
                  f"拨点 {len(m.get('pulse') or [])} 个/小节")
        return 0

    mood, why = a.mood, ""
    if a.project:
        proj = Path(a.project).expanduser().resolve()
        tl = proj / "timeline.json"
        if a.mood == "auto":
            txt = ""
            bp = proj / "brief.json"
            if bp.exists():
                b = json.loads(bp.read_text())
                txt = " ".join(str(b.get(k) or "") for k in ("audience", "present_state", "change"))
            mood, why = auto_mood(txt)
        if a.dur is None and tl.exists():
            a.dur = float(json.loads(tl.read_text()).get("duration_s") or 0) or None
        out = Path(a.out) if a.out else proj / "bgm.wav"
    else:
        if a.mood == "auto":
            mood = DEFAULT_MOOD
        if a.dur is None:
            raise SystemExit("要么给 --project（读片长），要么给 --dur")
        out = Path(a.out or "bgm.wav")

    dur = float(a.dur or 60.0)
    write_wav(out, synth(mood, dur))
    # 盖个戳：渲染那边靠它判断「这条垫乐是不是当前情绪合成的」
    if a.project:
        (out.parent / "bgm.json").write_text(json.dumps(
            {"mood": mood, "dur": round(dur, 3), "why": why, "moods": sorted(MOODS)},
            ensure_ascii=False, indent=1), encoding="utf-8")
    tail = f"（{why}）" if why else ""
    print(f"{MOODS[mood]['label']} · {dur:.1f}s → {out}"
          f"（{out.stat().st_size/1e6:.1f} MB，代码合成，无第三方版权）{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
