#!/usr/bin/env python3
"""CutSkills · BGM 鼓点/瞬态检测与百万剪辑师卡点吸附引擎（Beat Sync）。

为什么百万粉丝博主的片子看着「带感」：
画面切点、贴纸弹出、大字砸下，不是随便落在小数点上，而是踩在音乐鼓点（Onset）或人声重音上。

本模块做两件事：
1. `detect_beats(audio_path)`：把配乐解成 16kHz 单声道，按 10ms 帧算能量一阶正差分（Onset Flux），
   用局部滑窗自适应门限挑出瞬态鼓点与重拍（Strong Beats），估算 BPM，完全不依赖重型音频库。
2. `snap_timeline_to_beats(tl, beats, max_shift_s=0.18)`：
   - **严守音画同源铁律**：真人露脸口播段（`kind=="clip"` 且 `medium=="talking"`）绝不改时长，防止嘴形错位；
   - 对弹性视觉段（`gen` 代码动效、`image` 照片、纯画面 `broll` 无独立对口型锁）微调 `±max_shift_s`，
     让累计切点精准吸附在最近的鼓点上！

用法：
  beat_sync.py --bgm <音频路径> [--duration 45]
  beat_sync.py --project <项目目录> [--bgm auto] [--snap] [--max-shift 0.18]
"""
from __future__ import annotations

import argparse
import array
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cc0_lib  # noqa: E402
from libs import ffmpeg_bin  # noqa: E402

HOP_S = 0.010          # 10ms 分辨率（1/3 帧 @30fps，卡点误差 <0.5 帧）
MIN_GAP_S = 0.28       # 相邻鼓点最小间隔（对应最高 ~214 BPM）
WIN_RADIUS = 25        # 局部均值半径（±250ms）


def detect_beats(audio_path: str | Path, *, duration_s: float = 90.0) -> dict:
    """从音频提取鼓点瞬态时间戳列表、重拍列表与估算 BPM。"""
    p = Path(audio_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"找不到音频文件：{p}")

    sr = 16000
    with tempfile.TemporaryDirectory(prefix="cutskills-beats-") as td:
        wav_p = Path(td) / "bgm.wav"
        subprocess.run(
            [ffmpeg_bin(), "-y", "-v", "error", "-i", str(p),
             "-t", f"{max(5.0, duration_s + 2.0):.2f}",
             "-vn", "-ac", "1", "-ar", str(sr), "-sample_fmt", "s16", str(wav_p)],
            check=True,
        )
        with wave.open(str(wav_p), "rb") as w:
            raw = w.readframes(w.getnframes())

    samples = array.array("h", raw)
    hop = int(sr * HOP_S)
    n_frames = len(samples) // hop
    if n_frames < 20:
        return {"bpm": 0.0, "beats": [], "strong_beats": []}

    energies = []
    for i in range(n_frames):
        seg = samples[i * hop:(i + 1) * hop]
        energies.append(sum(abs(x) for x in seg) / hop)

    # 半波整流能量差分（瞬态冲击度）
    flux = [0.0] + [max(0.0, energies[i] - energies[i - 1]) for i in range(1, n_frames)]

    peaks: list[tuple[float, float]] = []
    min_gap_frames = int(round(MIN_GAP_S / HOP_S))
    last_idx = -min_gap_frames

    for i in range(2, n_frames - 2):
        if not (flux[i] > flux[i - 1] and flux[i] >= flux[i + 1]):
            continue
        lo = max(0, i - WIN_RADIUS)
        hi = min(n_frames, i + WIN_RADIUS + 1)
        local_mean = sum(flux[lo:hi]) / max(1, hi - lo)
        if flux[i] < max(45.0, local_mean * 1.85):
            continue
        if i - last_idx < min_gap_frames:
            if peaks and flux[i] > peaks[-1][1]:
                peaks[-1] = (round(i * HOP_S, 3), flux[i])
                last_idx = i
            continue
        peaks.append((round(i * HOP_S, 3), flux[i]))
        last_idx = i

    # 若音频较长但原曲循环播放，补齐循环周期内的节拍
    actual_dur = n_frames * HOP_S
    beats = [t for t, _ in peaks]
    if actual_dur > 3.0 and duration_s > actual_dur + 0.5 and beats:
        ext_beats = list(beats)
        ext_peaks = list(peaks)
        k = 1
        while k * actual_dur < duration_s:
            shift = k * actual_dur
            for t, strength in peaks:
                if t + shift <= duration_s:
                    ext_beats.append(round(t + shift, 3))
                    ext_peaks.append((round(t + shift, 3), strength))
            k += 1
        beats = ext_beats
        peaks = ext_peaks

    # 选前 40% 最强冲击作为重拍（适合章节切点与冲击推镜）
    if peaks:
        sorted_strength = sorted((s for _, s in peaks), reverse=True)
        cutoff = sorted_strength[min(len(sorted_strength) - 1, max(0, int(len(sorted_strength) * 0.4)))]
        strong = [t for t, s in peaks if s >= cutoff]
    else:
        strong = []

    # 用相邻鼓点间隔中位数估算 BPM
    bpm = 0.0
    if len(beats) >= 4:
        ibis = [beats[i] - beats[i - 1] for i in range(1, len(beats)) if 0.28 <= beats[i] - beats[i - 1] <= 1.2]
        if ibis:
            ibis.sort()
            med = ibis[len(ibis) // 2]
            bpm = round(60.0 / med, 1)
            while bpm > 175:
                bpm = round(bpm / 2, 1)
            while 0 < bpm < 72:
                bpm = round(bpm * 2, 1)

    return {"bpm": bpm, "beats": beats, "strong_beats": strong, "source": str(p)}


def _is_elastic(el: dict) -> bool:
    """判断该元素是否允许微调时长去踩鼓点（真人说话/带独立人声窗的绝不拉伸以保音画同步）。"""
    if (el.get("track") or "video") == "overlay":
        return False
    if el.get("audio"):
        return False
    kd = el.get("kind")
    if kd in ("gen", "image", "silence"):
        return True
    if kd == "clip" and el.get("medium") == "broll":
        return True
    return False


def _el_dur(el: dict) -> float:
    if el.get("kind") == "clip":
        return float(el["out"]) - float(el["in"])
    return float(el.get("dur") or 0.0)


def snap_timeline_to_beats(tl: dict, beats: list[float], *, max_shift_s: float = 0.18) -> tuple[dict, dict]:
    """把弹性视觉段的切点吸附到最近鼓点，返回 (新 timeline, 卡点报告)。"""
    if not beats:
        return tl, {"snapped": 0, "checked_cuts": 0, "hit_rate": 0.0}

    els = [dict(e) for e in (tl.get("elements") or [])]
    cur_t = 0.0
    snapped = 0
    cuts: list[float] = []

    for el in els:
        if (el.get("track") or "video") == "overlay":
            # overlay 卡片的入场点也可轻吸最近鼓点（若无强制锚词）
            if not el.get("card") and el.get("at") is not None:
                at = float(el["at"])
                near = min(beats, key=lambda b: abs(b - at))
                if abs(near - at) <= max_shift_s:
                    el["at"] = round(near, 3)
                    snapped += 1
            continue

        d = _el_dur(el)
        end_t = cur_t + d
        if _is_elastic(el) and d >= 1.2:
            near = min(beats, key=lambda b: abs(b - end_t))
            delta = near - end_t
            new_d = d + delta
            min_ok = 1.6 if el.get("kind") == "gen" else 0.8
            if abs(delta) <= max_shift_s and new_d >= min_ok:
                if el.get("kind") == "clip":
                    el["out"] = round(float(el["in"]) + new_d, 3)
                else:
                    el["dur"] = round(new_d, 3)
                end_t = cur_t + round(new_d, 3)
                if abs(delta) >= 0.015:
                    snapped += 1
        cur_t = round(end_t, 3)
        cuts.append(cur_t)

    # 统计全部切点落在鼓点 ±60ms 内的比例
    internal_cuts = cuts[:-1] if len(cuts) > 1 else cuts
    hits = sum(1 for c in internal_cuts if min(abs(b - c) for b in beats) <= 0.065)
    rate = round(hits / max(1, len(internal_cuts)), 3) if internal_cuts else 1.0

    new_tl = dict(tl, elements=els, duration_s=round(cur_t, 3))
    report = {
        "snapped": snapped,
        "checked_cuts": len(internal_cuts),
        "on_beat_cuts": hits,
        "hit_rate": rate,
    }
    new_tl["beat_sync"] = report
    return new_tl, report


def main() -> int:
    ap = argparse.ArgumentParser(description="BGM 鼓点检测与时间轴卡点吸附")
    ap.add_argument("--project", default=None, help="项目目录")
    ap.add_argument("--bgm", default="auto", help="BGM 音频文件路径，或 auto / upbeat / warm / clear")
    ap.add_argument("--duration", type=float, default=60.0, help="分析时长上限（秒）")
    ap.add_argument("--snap", action="store_true", help="将项目 timeline.json 的弹性切点吸附到鼓点")
    ap.add_argument("--max-shift", type=float, default=0.18, help="单处最大微调秒数（默认 0.18s）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve() if a.project else None
    tl = None
    dur = a.duration
    if proj and (proj / "timeline.json").exists():
        tl = json.loads((proj / "timeline.json").read_text(encoding="utf-8"))
        dur = float(tl.get("duration_s") or dur)

    bgm_path: Path | None = None
    if a.bgm and Path(a.bgm).expanduser().exists():
        bgm_path = Path(a.bgm).expanduser().resolve()
    else:
        mood = (tl or {}).get("bgm_mood") or ("upbeat" if a.bgm == "auto" else a.bgm)
        picked = cc0_lib.pick_bgm(mood, dur, seed=proj.name if proj else mood)
        if picked:
            bgm_path = Path(picked["path"])

    if not bgm_path or not bgm_path.exists():
        raise SystemExit("未找到可分析的 BGM 文件")

    info = detect_beats(bgm_path, duration_s=dur)
    print(f"BGM：{bgm_path.name} ｜ 估算 BPM：{info['bpm']} ｜ 检出鼓点：{len(info['beats'])} 处（重拍 {len(info['strong_beats'])} 处）")

    if proj:
        out_beats = proj / "beats.json"
        out_beats.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        if a.snap and tl:
            new_tl, rep = snap_timeline_to_beats(tl, info["beats"], max_shift_s=a.max_shift)
            (proj / "timeline.json").write_text(json.dumps(new_tl, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"→ 卡点吸附完成：微调 {rep['snapped']} 处切点 · 踩点率 {rep['hit_rate']:.0%}（{rep['on_beat_cuts']}/{rep['checked_cuts']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
