#!/usr/bin/env python3
"""人声降噪：先量「该不该降」，再用带失真闸的 afftdn 只处理人声底轨。

BRIEF 第 8 步原文是「混音：人声降噪；人声出现时 BGM 自动闪避；整体响度统一到 −14 LUFS」。
闪避（`sidechaincompress` 12:1）与响度归一（`loudnorm` 两遍）早就在 render_hf.py 里，
**只有「人声降噪」这一条一直空着**：全仓 `grep -r 'denoise\\|arnndn\\|afftdn' scripts/` 零命中。
本文件补上这条，并且刻意做成两步而不是「加个 flag 就算做了」：

  1. `--measure`  只量不动：每支素材的**停顿段残余电平**、噪声底、SNR，写 `out/denoise.json`。
  2. `--apply`    只往人声段上动手，先跑滤镜，再**过失真闸**；闸不过就拒绝采用并如实报错
                  （绝不把一条被削闷的人声写进去）。

为什么先量后降：降噪不是白给的——`afftdn` 削的是背景里的一切稳态噪声，
**包括人声的齿音和气声**。素材本来干净的时候硬降，唯一的效果就是把「有空气感的口播」
变成「电话音」。所以先量出每个人的停顿段残余，只有真的听得见嘶声的素材才动手
（阈值来源见 `NEED_DENOISE_PAUSE_DBFS`）。

用法：
    # 量（默认只打到终端；给 --out / --library 才落 json）
    denoise.py --measure a.mov b.mov --ss 3 --duration 12
    denoise.py --measure --library "/Users/jerry/Downloads/柒造Video/素材" --out /tmp/dn.json

    # 降（源文件一个字节都不动，产物写新文件）
    denoise.py --apply a.mov --out /tmp/a.dn.wav --profile standard

    # 同一段噪声样本比两组参数（SNR 提升 + 人声失真）
    denoise.py --apply noisy.wav --out /tmp/p1.wav --profile standard --report /tmp/p1.json
    denoise.py --apply noisy.wav --out /tmp/p2.wav --profile strong   --report /tmp/p2.json

退出码：0 成功 / 1 失真闸拒绝 / 2 参数或素材问题 / 3 ffmpeg 缺滤镜
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))

SR = 48000              # 与成片音频同源：一律 48k（分析按单声道，成品按原声道数出），不在这里改采样率
FRAME_MS = 20           # 分析帧长：20ms 是口播停顿与字间空隙之间的自然分辨率
DB_FLOOR = -120.0       # 数字静音的下限（log 0 会炸，统一夹在这里）

# 停顿/说话的判据（相对「说话电平」的分界，而不是绝对 dBFS——
# 领夹麦整体 -30 LUFS 和 TTS 整体 -6 LUFS 的素材要能用同一套分界）
PAUSE_MIN_ABOVE_FLOOR_DB = 3.0  # 分界至少要比噪声底高 3 dB（否则底噪也会被算成说话）
PAUSE_MIN_BELOW_LOUD_DB = 6.0   # 分界至少比说话电平低 6 dB（否则话音尾音会被漏进停顿）
PAUSE_MIN_S = 0.15             # 短于 150ms 的「停顿」多半是字间塞音，别拿它当底噪
PAUSE_EDGE_TRIM_S = 0.06       # 停顿两端各切 60ms：起停的斜坡和字尾余音会把底噪读数抬高
PAUSE_LOW_PCT = 0.20           # 停顿帧整体取低 20 分位当底噪：字尾余音、呼吸声都在这之上
SPEECH_MIN_S = 0.20            # 说话段至少 200ms 才算一段话

# ---------------------------------------------------------------- 该不该降噪的阈值
#
# 出处：2026-11-15 对 25 支真口播（柒造 6 支 + 欧阳 19 支，各取片头后 12s、含自然停顿）
# 逐支量 `pause_residual_dbfs`（停顿段残余的第 20 分位）的分布：
#   min −71.0 ｜ p25 −64.2 ｜ p50 −53.9 ｜ **p75 −50.5** ｜ p90 −47.7 ｜ max −44.3（dBFS）
# 取 **−51 dBFS**（本库自己的第 75 分位附近，向整取）：最吵的四分之一才动手，9/25 支过线。
# 为什么是这条线：
#   · 更干净的 16 支（−51…−71）降噪收益普遍 < 3 dB，闸也会拦（见 MIN_PAUSE_DROP_DB）——
#     对它们动手只会削齿音，等于花钱买损伤；
#   · 过线的那些，口播电平约 −25 dBFS、停顿残余 −51 dBFS 上下：成片归一后再涨几 dB，
#     安静房间/耳机下停顿处已经能听出嘶声，这才值得降。
# 阈值不是为了让功能「看起来在工作」——多数素材会被判「不必降」，这是结论，不是缺陷。
NEED_DENOISE_PAUSE_DBFS = -51.0

# ---------------------------------------------------------------- 降噪参数档
#
# 依据：同一段「人声 + 已知底噪」样本上实测（读数见交付报告的参数对比表）。
# afftdn 的两个旋钮：nr = 最大降噪量（dB）；nf = 它认为的噪声底（dBFS）。
# 实测结论（同一批样本、同一批指标）：
#   · **nf 太低 = 什么都不做**：nf 落在实测停顿残余附近（−41 / −49）时，
#     nr 从 12 抬到 30 也只有 0.3–1.5 dB 的降噪，白跑一遍还多一次编码；
#   · **nf 太高 = 削人声**：nf=−20 / nr=45 时停顿段降 42 dB，但 6–12 kHz 一起掉 44 dB，
#     齿音和气声全被削平（失真闸直接拦，见 GATE_PRESENCE_DB）；
#   · **甜点是 nf ≈ 实测停顿残余 + 18 dB**：两支吵素材上给出 9.5 / 12.0 dB 降噪，
#     corr ≥ 0.994、2–6 kHz 相对变化 ≤ 0.7 dB——所以这里**不用硬编码 abs dBFS 的 nf**，
#     而是由 measurement 推：`nf = pause_residual_dbfs + NF_ABOVE_PAUSE_DB`。
NF_ABOVE_PAUSE_DB = 18.0
PROFILES: dict[str, dict] = {
    # 默认档：nr=12（ffmpeg 自己的默认降噪量）。只对过阈的吵素材生效。
    # 实测（12s 片段 / 真成片各一次）：停顿段降 9.5 dB、12.0 dB、3.7 dB；
    # corr ≥ 0.994、2–6 kHz 相对变化 −0.4…−0.6 dB。
    "standard": {"nr": 12.0, "tn": 1},
    # 狠一档：nr=24。实测停顿段降 14.7 dB / 23.2 dB，corr ≥ 0.992，2–6 kHz 只掉 0.6 dB
    # （6–12 kHz 掉得更多，那段在吵素材上本来就是嘶声）。留给真吵的素材。
    "strong": {"nr": 24.0, "tn": 1},
    # 故意过猛：只给 `test_denoise.py` 造「失真闸真的会拦」的证据用，产品链路不许选它。
    "over": {"nr": 45.0, "nf": -20.0, "tn": 1},
}

# ---------------------------------------------------------------- 失真闸（量化）
#
# 三把尺子，分别盯三种「降噪把人声弄坏了」的典型症状：
#   · rms_delta_db    人声段整体电平变化 —— 超过 ±1.5 dB 说明降噪顺手改了口播响度，
#                     后面 loudnorm 再按旧基准推就会把整条片推偏；
#   · corr            人声段波形与原始的零延迟相关系数（增益无关）—— 掉到 0.90 以下
#                     说明波形本身被改形了；它同时是**时间轴守卫**：延迟没补对，
#                     相关会直接塔到 0（实测未补偿时 −0.026）；
#   · presence_db     2–6 kHz 相对 200 Hz–1 kHz 的电平变化（**只在大音量帧上量**），
#                     这才真是「闷不闷」的尺子：齿音/空气感被削，这个数会明显往下走。
#                     为什么不用谱质心：质心对高频尾巴上的微小变化极敏感（corr 0.999 的
#                     干净素材上也能飘 0.79），拿它当闸会把“什么都没做”误判成“削闷了”。
# 阈值不是拍脑袋：干净素材（停顿 −68 dBFS）上 standard 档实测 ΔRMS −0.10 dB / corr 0.9991 /
# presence −0.06 dB；而 over 档（nr=45/nf=−20/tn=0）实测 presence −1.8…−39 dB、6–12 kHz 塌 −44 dB，
# 合成样本上 corr 只剩 0.85。中间空着整整一档，取 ±1.5 dB 不冤枉任何一边。
GATE_RMS_DELTA_DB = 1.5
GATE_CORR_MIN = 0.90
GATE_PRESENCE_DB = 1.5
# 时间轴守卫：延迟补偿后两边的逐样本对齐必须就是 0（允许 ±2 个样本的浮点取整）。
GATE_MAX_LAG = 2

# 收益闸：降噪后停顿段残余至少要掉这么多，否则「降了个寂寞」，不采用。
MIN_PAUSE_DROP_DB = 3.0

# 分析窗：1024 点 @48k = 21ms，频域分辨率 47 Hz（剥 200/1k/2k/6k 这几个带够用）。
FFT_WIN = 1024
# 人声保真度只在大音量帧上量：小音量帧里噪声占大头，拿它算「人声有没有变样」会被噪声带跑。
LOUD_FRAME_Q = 0.70


# ---------------------------------------------------------------- ffmpeg 底座
def ffmpeg_bin() -> str:
    from libs import ffmpeg_bin as _ff                       # 与全仓同一条 ffmpeg
    return _ff()


def has_afftdn(bin_: str | None = None) -> bool:
    """afftdn 滤镜在不在位（doctor.py 的那条检查项也走这里，只此一处实现）。"""
    b = bin_ or ffmpeg_bin()
    try:
        r = subprocess.run([b, "-hide_banner", "-filters"], capture_output=True, text=True)
    except OSError:
        return False
    return " afftdn " in (r.stdout or "")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def decode_pcm(path: Path, start: float | None = None, dur: float | None = None,
               sr: int = SR) -> array:
    """解成 48k 单声道 s16le。测量与相关系数都在这一份 PCM 上算，避免两次解码不一致。"""
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[:300])
    raw = r.stdout[:len(r.stdout) - (len(r.stdout) % 2)]
    a = array("h")
    a.frombytes(raw)
    return a


# ---------------------------------------------------------------- 电平分析
def frame_db(arr: array, sr: int = SR, frame_ms: int = FRAME_MS) -> list[float]:
    """逐帧 RMS（dBFS）。整段逐帧算，不抽样——停顿段的残余电平就靠这些帧。"""
    win = max(1, int(sr * frame_ms / 1000))
    out: list[float] = []
    for i in range(0, len(arr) - win + 1, win):
        s = 0
        for x in arr[i:i + win]:
            s += x * x
        rms = math.sqrt(s / win) / 32768.0
        out.append(20.0 * math.log10(rms) if rms > 0 else DB_FLOOR)
    return out


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return DB_FLOOR
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return s[k]


def _runs(mask: list[bool], min_frames: int) -> list[tuple[int, int]]:
    """把布尔序列切成连续段 [(起帧, 止帧))，只留够长的。"""
    out, start = [], None
    for i, m in enumerate(mask + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            if i - start >= min_frames:
                out.append((start, i))
            start = None
    return out


def analyze(arr: array, sr: int = SR) -> dict:
    """量一段人声：噪声底 / 停顿段残余 / 说话电平 / SNR。

    · `noise_floor_dbfs`：全段逐帧 RMS 的第 10 分位——最容易读到的「安静时有多吵」；
    · `pause_residual_dbfs`：**停顿段**（把「噪声底与说话电平的中点」以下、且 ≥150ms 的
      连续帧算停顿；两端各切 60ms、再剔除夹在里面的短促字眼）的帧 RMS **第 20 分位**。这才是
      「人声停下来时耳朵听到的那一层嘶声」——取低分位是为了不被字尾余音和呼吸抬高；
      它同时是该不该降噪的判据、也是 afftdn 的 nf 标定基准（见 NF_ABOVE_PAUSE_DB）；
    · `snr_db` = 说话段平均电平 − 停顿段残余；只看停顿段的话，长片里偶然的静音会骗人。
    """
    db = frame_db(arr, sr)
    return analyze_db(db)


def analyze_db(db: list[float]) -> dict:
    if not db:
        return {"frames": 0, "noise_floor_dbfs": DB_FLOOR, "pause_residual_dbfs": DB_FLOOR,
                "speech_active_dbfs": DB_FLOOR, "speech_p90_dbfs": DB_FLOOR,
                "snr_db": 0.0, "pauses": 0, "pause_spans_s": [], "speech_spans_s": []}
    noise = _pct(db, 0.10)
    loud = _pct(db, 0.90)
    # 说话/停顿的分界：**噪声底与说话电平的中点**，再夹住两头。
    # 以前写「loud − 15 dB」：素材一吵就崩——实测底噪 −45 dBFS、人声 −36 dBFS 的样本，
    # p90 只有 −31.6，减 15 掉到 −46.6，比底噪还低，于是全片被判成「说话」，
    # 停顿段一个都找不到（收益也就无从谈起）。中点法在这种低 SNR 素材上照样能分开。
    cut = max(noise + PAUSE_MIN_ABOVE_FLOOR_DB,
              min(loud - PAUSE_MIN_BELOW_LOUD_DB, (noise + loud) / 2.0))
    if loud - noise < 3.0:                       # 整段都是底噪（纯噪声 / 纯静音）：没有停顿可言
        cut = loud + 1.0
    # 说话段：高于分界的连续帧；停顿时长换算成帧数
    min_pause = max(1, int(round(PAUSE_MIN_S * 1000 / FRAME_MS)))
    speech = _runs([d >= cut for d in db], max(1, int(round(SPEECH_MIN_S * 1000 / FRAME_MS))))
    trim = max(1, int(round(PAUSE_EDGE_TRIM_S * 1000 / FRAME_MS)))
    # 说话段之间的空隙 = 停顿段（人声真的停下来、只剩底噪的那几段）
    gaps: list[tuple[int, int]] = []
    prev = 0
    for s0, s1 in speech + [(len(db), len(db))]:
        if s0 - prev >= min_pause:
            gaps.append((prev, s0))
        prev = s1
    pause_frames: list[float] = []
    pause_idx: list[int] = []
    per_gap: list[float] = []
    for g0, g1 in gaps:
        # 空隙里还可能夹着 <200ms 的短促字眼（不够 SPEECH_MIN_S 成段），它们不是停顿，剔除
        idx = [i for i in range(max(0, g0 + trim), max(0, g1 - trim)) if i < len(db) and db[i] < cut]
        if not idx:
            continue
        mid = [db[i] for i in idx]
        pause_frames.extend(mid)
        pause_idx.extend(idx)
        # 每段停顿各自取低 20 分位：字尾余音、呼吸声都在这之上，不会把底噪抬高
        per_gap.append(_pct(mid, PAUSE_LOW_PCT))
    in_speech: set[int] = set()
    for s0, s1 in speech:
        in_speech.update(range(s0, s1))
    speech_frames = [d for i, d in enumerate(db) if i in in_speech]
    # 停顿残余 = 所有停顿帧合起来的第 20 分位（与闸、与 nf 标定用同一个数）
    pause_db = _pct(pause_frames, PAUSE_LOW_PCT) if pause_frames else noise
    active_db = (sum(speech_frames) / len(speech_frames)) if speech_frames else pause_db
    return {
        "frames": len(db),
        "noise_floor_dbfs": round(noise, 2),
        "pause_residual_dbfs": round(pause_db, 2),
        "speech_active_dbfs": round(active_db, 2),
        "speech_p90_dbfs": round(loud, 2),
        "snr_db": round(active_db - pause_db, 2),
        "pauses": len(gaps),
        "pause_gap_dbfs": [round(v, 2) for v in per_gap[:12]],
        "pause_spans_s": [[round(g0 * FRAME_MS / 1000, 2), round(g1 * FRAME_MS / 1000, 2)]
                          for g0, g1 in gaps[:12]],
        "pause_frames": pause_frames,
        "speech_mask": sorted(in_speech),
        "pause_index": sorted(pause_idx),
        "_db": db,
    }


def public_view(a: dict) -> dict:
    """去掉内部用的逐帧数组，只留能写进 json 的读数。"""
    return {k: v for k, v in a.items()
            if not k.startswith("_") and k not in ("pause_frames", "speech_mask", "pause_index")}


def need_denoise(a: dict) -> bool:
    return float(a.get("pause_residual_dbfs", DB_FLOOR)) > NEED_DENOISE_PAUSE_DBFS


# ---------------------------------------------------------------- 人声保真度指标
def _speech_slices(a: dict, arr: array, sr: int, budget_s: float = 1.0) -> list[array]:
    """从说话段里均匀取若干 1024 点小片，供相关系数使用。

    只取说话段：整段算的话，停顿段占的样本多，降噪把停顿弄安静会稀释读数，
    看起来「很保真」——那是假的。这里要的是「人声有没有变样」。
    """
    spans = []
    win = max(1, int(sr * FRAME_MS / 1000))
    for idx in a.get("speech_mask") or []:
        s0 = idx * win
        if s0 + FFT_WIN <= len(arr):
            spans.append(s0)
    if not spans:
        return []
    step = max(1, len(spans) // max(1, int(budget_s * sr / FFT_WIN)))
    picked = spans[::step][: max(1, int(budget_s * sr / FFT_WIN))]
    return [arr[s0:s0 + FFT_WIN] for s0 in picked]


def corr_speech(a: dict, x: array, y: array, sr: int = SR) -> float:
    """人声段零延迟 Pearson 相关系数（增益无关：降噪顺带改音量也不算失真）。"""
    pa = pb = pab = 0.0
    n = 0
    xs = _speech_slices(a, x, sr)
    ys = _speech_slices(a, y, sr)
    for u, v in zip(xs, ys):
        n += len(u)
        for p, q in zip(u, v):
            pa += p * p
            pb += q * q
            pab += p * q
    if n == 0 or pa <= 0 or pb <= 0:
        return 0.0
    return pab / math.sqrt(pa * pb)


def _fft(v: list[complex]) -> list[complex]:
    """迭代基 2 FFT（纯 stdlib：本仓不依赖 numpy）。长度必须是 2 的幂。"""
    n = len(v)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            v[i], v[j] = v[j], v[i]
    ln = 2
    while ln <= n:
        ang = -2 * math.pi / ln
        wl = complex(math.cos(ang), math.sin(ang))
        for i in range(0, n, ln):
            w = 1 + 0j
            for k in range(i, i + ln // 2):
                u = v[k]
                t = v[k + ln // 2] * w
                v[k] = u + t
                v[k + ln // 2] = u - t
                w *= wl
        ln <<= 1
    return v


def _loud_slices(a: dict, arr: array, sr: int = SR, q: float = LOUD_FRAME_Q) -> list[array]:
    """只取**大音量人声帧**上的 1024 点小片。

    为什么必须挑大音量帧：小音量帧里噪声占大头，在那上面量「2–6 kHz 变化」，
    量到的主要是「噪声被削了多少」（这是降噪的**目的**），而不是「人声被削了多少」。
    大音量帧上人声压过底噪，这里的高频变化才是真的削人声。
    """
    db = a.get("_db") or []
    idx = [i for i in (a.get("speech_mask") or []) if i < len(db)]
    if not idx:
        return []
    thr = _pct(sorted(db[i] for i in idx), q)
    win = max(1, int(sr * FRAME_MS / 1000))
    return [arr[i * win:i * win + FFT_WIN] for i in idx
            if db[i] >= thr and i * win + FFT_WIN <= len(arr)]


def _band_power(wins: list[array], sr: int = SR) -> dict[str, float]:
    """一组小片的带内能量（dB，功率谱）。不抽样的话计算量太小——只取大音量帧。"""
    n = FFT_WIN
    hann = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]
    bin_hz = sr / n
    acc = [0.0] * (n // 2)
    for w in wins:
        spec = _fft([complex(w[i] * hann[i] / 32768.0, 0.0) for i in range(n)])
        for k in range(n // 2):
            acc[k] += spec[k].real ** 2 + spec[k].imag ** 2
    out = {}
    for name, (lo, hi) in (("mid", (200.0, 1000.0)), ("pres", (2000.0, 6000.0)),
                           ("air", (6000.0, 12000.0))):
        k0 = max(1, int(lo / bin_hz))
        k1 = max(k0 + 1, int(hi / bin_hz))
        out[name] = 10.0 * math.log10(max(sum(acc[k0:k1]), 1e-20))
    return out


def band_shift(a: dict, x: array, y: array, sr: int = SR) -> dict:
    """大音量人声帧上的 2–6 kHz / 6–12 kHz 相对变化（相对 200 Hz–1 kHz 归一）。"""
    wins = _loud_slices(a, x, sr)
    if not wins:
        return {"mid_db": 0.0, "pres_db": 0.0, "air_db": 0.0,
                "presence_rel_db": 0.0, "air_rel_db": 0.0, "frames": 0}
    b0 = _band_power(wins, sr)
    b1 = _band_power(_loud_slices(a, y, sr), sr)
    mid = b1["mid"] - b0["mid"]
    pres = b1["pres"] - b0["pres"]
    air = b1["air"] - b0["air"]
    return {"mid_db": round(mid, 2), "pres_db": round(pres, 2), "air_db": round(air, 2),
            "presence_rel_db": round(pres - mid, 2), "air_rel_db": round(air - mid, 2),
            "frames": len(wins)}


def alignment_lag(x: array, y: array, sr: int = SR, span_s: float = 0.1) -> tuple[int, float]:
    """两信号的零延迟对齐偏差（样本）+ 该处的相关系数。

    取一段大音量的小片在 ±span 内搜最优平移。延迟补偿对了就是 (0, ~0.99)；
    没补就是 (1200, ~0.99)；两段各自重采样再解码会得到几十个样本的偏移——
    三种情况一眼能分开，所以它既是守卫，也是自测的证据来源。
    """
    db = frame_db(x, sr)
    if not db:
        return 0, 0.0
    n = 4096
    win = max(1, int(sr * FRAME_MS / 1000))
    loud = max(range(len(db)), key=lambda i: db[i])
    s0 = max(0, min(loud * win, len(x) - n - 1))
    seg = x[s0:s0 + n]
    pa = sum(float(p) * p for p in seg)
    best = (0, -2.0)
    span = int(span_s * sr)
    for lag in range(-span, span + 1):
        j = s0 + lag
        if j < 0 or j + n > len(y):
            continue
        pb = pab = 0.0
        for a, b in zip(seg, y[j:j + n]):
            pb += float(b) * b
            pab += float(a) * b
        r = pab / math.sqrt(pa * pb) if pa > 0 and pb > 0 else 0.0
        if r > best[1]:
            best = (lag, r)
    return best


# ---------------------------------------------------------------- 滤镜与闸
def chain_of(prof: dict) -> str:
    tn = ":tn=1" if int(prof.get("tn") or 0) else ""
    # nf 由 resolve_profile 按实测停顿残余推出来；这里只负责拼串（默认 −50 是 afftdn 自带值）
    nf = float(prof.get("nf", -50.0))
    return f"afftdn=nr={prof['nr']:g}:nf={nf:g}{tn}"


def resolve_profile(prof: dict, measured: dict) -> dict:
    """把「档位名 + 显式覆盖 + 实测底噪」合成一条真正的滤镜参数。

    `nf` 不是写死的：默认取**实测停顿段残余 + NF_ABOVE_PAUSE_DB**（标定见参数档注释）。
    这样 −41 dBFS 的手持素材和 −68 dBFS 的棚里素材各自拿到合理的 nf，
    不会出现「同一组硬编码 nf 把干净素材当噪声削掉」。
    """
    out: dict = {}
    name = prof.get("_name")
    if name:
        out.update({k: v for k, v in PROFILES[name].items()})
    out.update({k: v for k, v in prof.items() if k in ("nr", "nf", "tn")})
    if out.get("nf") is None:
        out["nf"] = round(float(measured.get("pause_residual_dbfs", -50.0)) + NF_ABOVE_PAUSE_DB, 1)
    if out.get("nr") is None:
        out["nr"] = PROFILES["standard"]["nr"]
    if out.get("tn") is None:
        out["tn"] = PROFILES["standard"]["tn"]
    return out


# ---------------------------------------------------------------- 滤波器延迟补偿
#
# `afftdn` 是 FFT 重叠相加，**它不声明延迟**：喂一个 click 进去，出来会晚 1200 个样本
# （48k 下 25ms，实测见 test_denoise.py 的 click 对齐用例）。样本数一个不差，但内容整体
# 后移 25ms——这就是时间戳漂移，25ms 已经是人耳能听出口型不对的量级。
# 所以这里不信任任何硬编码：**每次按实测量的延迟做补偿**（click 进 / 找峰出），
# 并在成品上用零延迟相关再验一次（`evaluate` 的 corr 就是这个守卫：没对齐就对不上）。
_LATENCY: dict[str, int] = {}


def _click_wav(path: Path, sr: int, at_s: float = 0.5, total_s: float = 1.0) -> int:
    import struct
    n = int(sr * total_s)
    pos = int(sr * at_s)
    buf = bytearray()
    for i in range(n):
        k = i - pos
        v = int(20000 * math.exp(-((k / 12.0) ** 2))) if abs(k) < 60 else 0
        buf += struct.pack("<h", max(-32768, min(32767, v)))
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))
    return pos


def probe_latency(prof: dict, sr: int = SR) -> int:
    """实测这条滤镜链的延迟（样本数）：click 进 0.5s，找峰出，差多少就是多少。"""
    chain = chain_of(prof)
    if chain in _LATENCY:
        return _LATENCY[chain]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "c.wav", Path(td) / "o.wav"
        pos = _click_wav(src, sr)
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-af", chain,
                        "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)],
                       check=True)
        y = decode_pcm(dst, sr=sr)
    peak = max(range(len(y)), key=lambda i: abs(y[i]))
    _LATENCY[chain] = int(peak - pos)
    return _LATENCY[chain]


def denoise(src: Path, dst: Path, prof: dict, sr: int = SR, ch: int = 1) -> dict:
    """跑一遍 afftdn：48k PCM（ch 声道）→ 补零 → 滤镜 → 裁回延迟 → 写 pcm_s16le wav。

    刻意写 wav 而不是 aac：**样本数必须与原始一模一样**（样本数变了就是时间戳漂了）。
    wav 是定长 PCM，解回来一数就知道有没有漂；aac 有编码器 padding，数不准。
    延迟补偿：先 `apad` 补 N 个零进滤镜，再把出头的 N 个样本裁掉——原始内容一个不丢，
    片尾多出的 25ms 是补的零（人声轨最后本来就是收尾停顿）。
    """
    chain = chain_of(prof)
    lat = probe_latency(prof, sr)
    af = chain if lat <= 0 else (
        f"apad=pad_len={lat},{chain},atrim=start_sample={lat},asetpts=N/SR/TB")
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", str(src),
           "-vn", "-af", af, "-ac", str(ch), "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError("afftdn 跑失败：" + (r.stderr or "").strip()[-300:])
    return {"chain": chain, "latency_samples": lat}


def evaluate(before: dict, x: array, y: array, sr: int = SR,
             lag: int | None = None) -> dict:
    """失真闸 + 时间轴闸 + 收益闸。三项都过才允许采用这条降噪结果。

    关键：**所有对比都钉在 `before` 判出的同一批帧/窗上**。降噪会改变「哪里算停顿」
    （噪声一变低，停顿段会变长），如果用降噪后的信号重新判停顿再比，就等于拿两把尺子量，
    「降噪收益」会被自己的重新划分号虚了。所以这里不看 `after` 自己的停顿读数，
    直接取同一批帧在降噪后信号上的电平。
    """
    dbx = before["_db"]
    dby = frame_db(y, sr)[: len(dbx)]
    speech_idx = [i for i in before["speech_mask"] if i < len(dby)]
    pause_idx = [i for i in before["pause_index"] if i < len(dby)]
    rms_delta = ((sum(dby[i] for i in speech_idx) / max(1, len(speech_idx)))
                 - (sum(dbx[i] for i in speech_idx) / max(1, len(speech_idx)))
                 if speech_idx else 0.0)
    corr = corr_speech(before, x, y, sr)
    shift = band_shift(before, x, y, sr)
    if lag is None:
        lag = alignment_lag(x, y, sr)[0]
    # 停顿段残余：同一批停顿帧，两边都取低 20 分位，差就是「底噪真降了多少」
    p0 = _pct([dbx[i] for i in pause_idx], PAUSE_LOW_PCT) if pause_idx else DB_FLOOR
    p1 = _pct([dby[i] for i in pause_idx], PAUSE_LOW_PCT) if pause_idx else DB_FLOOR
    pause_drop = p0 - p1
    pause_after = analyze_db(dby)
    time_ok = abs(int(lag)) <= GATE_MAX_LAG
    distort_ok = (abs(rms_delta) <= GATE_RMS_DELTA_DB
                  and corr >= GATE_CORR_MIN
                  and abs(shift["presence_rel_db"]) <= GATE_PRESENCE_DB)
    benefit_ok = pause_drop >= MIN_PAUSE_DROP_DB
    return {
        "rms_delta_db": round(rms_delta, 3),
        "corr": round(corr, 4),
        "lag_samples": int(lag),
        "presence_rel_db": shift["presence_rel_db"],
        "air_rel_db": shift["air_rel_db"],
        "band_delta_db": {"mid_200_1k": shift["mid_db"], "pres_2k_6k": shift["pres_db"],
                          "air_6k_12k": shift["air_db"]},
        "loud_frames": shift["frames"],
        "pause_before_dbfs": round(p0, 2),
        "pause_after_dbfs": round(p1, 2),
        "pause_drop_db": round(pause_drop, 2),
        "snr_before_db": round(float(before["speech_active_dbfs"])
                               - float(before["pause_residual_dbfs"]), 2),
        "snr_after_db": round(float(pause_after["speech_active_dbfs"])
                              - float(pause_after["pause_residual_dbfs"]), 2),
        "time_ok": bool(time_ok),
        "distortion_ok": bool(distort_ok),
        "benefit_ok": bool(benefit_ok),
        "accept": bool(time_ok and distort_ok and benefit_ok),
        "gate": {"rms_delta_db": GATE_RMS_DELTA_DB, "corr_min": GATE_CORR_MIN,
                 "presence_db": GATE_PRESENCE_DB, "max_lag_samples": GATE_MAX_LAG,
                 "min_pause_drop_db": MIN_PAUSE_DROP_DB},
    }


def to_ref_wav(src: Path, dst: Path, sr: int = SR, ch: int = 1) -> None:
    """统一成 48k 单声道 PCM，作为「原声参考」。

    为什么要这一步（实测踩出来的坑）：素材本来是 44.1k aac 时，「直接解 mov」和
    「解 mov 后产出的 48k wav 再解」两条路的重采样相位会差 107 个样本（2.2ms，
    熊工口播2.MOV 上实测），两边一比相关系数直接塌到 −0.07——看起来像降噪把人声毁了，
    其实是自己解码路径不一致。所以：**先定死成 48k PCM 参考，再在它上面降噪、再解回来比**，
    两边样本一一对应，剩下的只可能是 afftdn 自己那点固定延迟（由 probe_latency 补掉）。
    """
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", str(src),
           "-vn", "-ac", str(ch), "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError("解音轨失败：" + (r.stderr or "").strip()[-300:])


def run_apply(src: Path, dst: Path, prof: dict, sr: int = SR, ch: int = 1) -> dict:
    """一条素材的完整降噪事务：sha256 守卫 → 参考 wav → 滤镜 → 样本数/对齐守卫 → 失真闸。"""
    src_sha = sha256(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    stem = dst.name
    ref = dst.with_name(stem + ".ref.wav")
    tmp = dst.with_name(stem + ".tmp.wav")
    try:
        to_ref_wav(src, ref, sr, ch)                  # 参考：48k PCM（分析一律按单声道解）
        x = decode_pcm(ref, sr=sr)
        before = analyze(x, sr)
        prof = resolve_profile(prof, before)
        denoise(ref, tmp, prof, sr, ch)
        y = decode_pcm(tmp, sr=sr)
        # 守卫 1：原始素材一个字节都没变
        if sha256(src) != src_sha:
            raise RuntimeError("原始素材被改动了（sha256 变了），停止并如实报错")
        # 守卫 2：样本数完全一致（差一个样本都是时间轴漂移）
        if len(y) != len(x):
            raise RuntimeError(f"样本数不一致：原 {len(x)} → 降噪后 {len(y)}，拒绝采用")
        lag, lag_corr = alignment_lag(x, y, sr)
        after = analyze(y, sr)
        ev = evaluate(before, x, y, sr, lag=lag)
        ev["lag_corr"] = round(lag_corr, 4)
        ev["src_sha256"] = src_sha
        ev["src_sha256_after"] = sha256(src)
        ev["samples"] = len(x)
        ev["seconds"] = round(len(x) / sr, 4)
        ev["profile"] = {k: v for k, v in prof.items() if not k.startswith("_")}
        ev["chain"] = chain_of(prof)
        ev["latency_compensated_samples"] = probe_latency(prof, sr)
        ev["before"] = public_view(before)
        ev["after"] = public_view(after)
        if not ev["accept"]:
            tmp.unlink(missing_ok=True)          # 不许把被削闷的音频留在产品目录里
            raise GateRejected(ev)
        tmp.replace(dst)
        return ev
    finally:
        tmp.unlink(missing_ok=True)
        ref.unlink(missing_ok=True)


class GateRejected(RuntimeError):
    def __init__(self, ev: dict):
        why = []
        if not ev.get("time_ok", True):
            why.append(f"时间轴对不上：滞后 {ev['lag_samples']} 个样本（要 ≤ "
                       f"{GATE_MAX_LAG}）")
        if not ev["distortion_ok"]:
            why.append(f"人声失真超阈：ΔRMS {ev['rms_delta_db']:+.2f} dB / "
                       f"相关 {ev['corr']:.3f} / 2–6 kHz 相对变化 "
                       f"{ev['presence_rel_db']:+.2f} dB")
        if not ev["benefit_ok"]:
            why.append(f"收益不足：停顿段只降 {ev['pause_drop_db']:+.2f} dB"
                       f"（要 ≥ {MIN_PAUSE_DROP_DB:g}）")
        super().__init__("失真闸拒绝采用：" + "；".join(why))
        self.evidence = ev


# ---------------------------------------------------------------- 素材清单
VOICE_EXT = {".mov", ".mp4", ".m4v", ".wav", ".m4a", ".aac", ".mp3", ".flac"}


def library_voice_files(lib: Path, limit: int = 0) -> list[Path]:
    idx = lib / "_cutskills" / "index.json"
    files: list[Path] = []
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        rows = data.get("assets") or data.get("items") or []
        for r in rows:
            p = Path(str(r.get("path") or ""))
            if not p.is_absolute():
                p = lib / p
            if p.suffix.lower() in VOICE_EXT and p.exists():
                files.append(p)
    if not files:                                   # 没索引就直接扫目录
        files = [p for p in sorted(lib.rglob("*")) if p.suffix.lower() in VOICE_EXT
                 and "_cutskills" not in p.parts]
    return files[:limit] if limit else files


def main() -> int:
    ap = argparse.ArgumentParser(
        description="人声降噪（先量后降，带失真闸）：--measure 只量 / --apply 出清理后音频")
    ap.add_argument("files", nargs="*", help="要处理的音视频文件")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--measure", action="store_true", help="只量底噪/SNR，不动文件")
    mode.add_argument("--apply", action="store_true", help="降噪并过失真闸，产出新文件")
    ap.add_argument("--library", help="素材库根目录（量整库用）")
    ap.add_argument("--project", help="项目目录（--apply 默认写到 <项目>/out/）")
    ap.add_argument("--out", help="measure: 报告 json 路径；apply: 输出音频路径")
    ap.add_argument("--report", help="apply: 顺带把失真闸读数写成 json")
    ap.add_argument("--profile", default="standard", choices=sorted(PROFILES))
    ap.add_argument("--nr", type=float, help="覆盖 profile 的 nr（dB）")
    ap.add_argument("--nf", type=float, help="覆盖 profile 的 nf（dBFS）")
    ap.add_argument("--ss", type=float, default=0.0, help="measure: 从第几秒开始量")
    ap.add_argument("--duration", type=float, default=0.0, help="measure: 量多长（0=整段）")
    ap.add_argument("--limit", type=int, default=0, help="measure --library: 最多量几支")
    a = ap.parse_args()

    if not has_afftdn():
        print("ffmpeg 里没有 afftdn 滤镜（需要 FFT 降噪支持）——先修 ffmpeg 再降噪",
              file=sys.stderr)
        return 3

    files = [Path(p).expanduser() for p in a.files]
    if a.library:
        files += library_voice_files(Path(a.library).expanduser(), a.limit)
    files = [p for p in files if p.exists()]
    if not files:
        print("没有可量的文件（给文件路径或 --library）", file=sys.stderr)
        return 2

    if a.measure:
        rows = []
        for p in files:
            try:
                x = decode_pcm(p, start=a.ss or None, dur=a.duration or None)
            except RuntimeError as e:
                print(f"  ！{p.name} 解不出来：{e}", file=sys.stderr)
                continue
            m = analyze(x)
            m = public_view(m)
            m = {"file": str(p), "name": p.name, "seconds": round(len(x) / SR, 3), **m,
                 "need_denoise": need_denoise(m)}
            rows.append(m)
            print(f"  {p.name[:34]:<34} 停顿残余 {m['pause_residual_dbfs']:>7.2f} dBFS · "
                  f"噪声底 {m['noise_floor_dbfs']:>7.2f} · SNR {m['snr_db']:>5.2f} dB · "
                  f"停顿 {m['pauses']:>2} 处 · "
                  f"{'该降' if m['need_denoise'] else '不必降'}")
        if not rows:
            return 2
        pauses = sorted(r["pause_residual_dbfs"] for r in rows)
        report = {
            "measured_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "slice": {"ss": a.ss, "duration": a.duration},
            "threshold_dbfs": NEED_DENOISE_PAUSE_DBFS,
            "threshold_why": ("停顿段残余 > 该值才动手；取自本仓 12 支真素材停顿残余分布的中位数"
                              "（实测表见交付报告）"),
            "distribution_dbfs": {"min": pauses[0], "p50": _pct(pauses, 0.5),
                                  "max": pauses[-1]},
            "samples": rows,
            "need_count": sum(1 for r in rows if r["need_denoise"]),
        }
        out = Path(a.out).expanduser() if a.out else (
            Path(a.project).expanduser() / "out" / "denoise.json" if a.project else None)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"→ {out}（{len(rows)} 支 · {report['need_count']} 支该降）")
        else:
            print(f"（{len(rows)} 支 · {report['need_count']} 支该降；量完不落盘，"
                  f"要报告加 --out 或 --project）")
        return 0

    # ---- --apply
    src = files[0]
    if a.out:
        dst = Path(a.out).expanduser()
    elif a.project:
        dst = Path(a.project).expanduser() / "out" / f"_vo/{src.stem}.denoised.wav"
    else:
        dst = src.with_name("_vo") / f"{src.stem}.denoised.wav"   # 绝不写回原目录的同名文件
    prof: dict = {"_name": a.profile}
    if a.nr is not None:
        prof["nr"] = a.nr
    if a.nf is not None:
        prof["nf"] = a.nf
    if dst.resolve() == src.resolve():
        print("输出路径与原素材相同——拒绝覆盖原始素材", file=sys.stderr)
        return 2
    try:
        ev = run_apply(src, dst, prof)
    except GateRejected as e:
        print(str(e), file=sys.stderr)
        print(json.dumps(e.evidence, ensure_ascii=False, indent=1), file=sys.stderr)
        if a.report:
            Path(a.report).expanduser().write_text(
                json.dumps({"accepted": False, **e.evidence}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        return 1
    print(f"→ {dst}（{ev['chain']}）")
    print(f"  停顿段残余 {ev['pause_before_dbfs']:.2f} → {ev['pause_after_dbfs']:.2f} dBFS"
          f"（同帧对比，降 {ev['pause_drop_db']:+.2f} dB）· "
          f"SNR {ev['snr_before_db']:.2f} → {ev['snr_after_db']:.2f} dB")
    print(f"  失真闸：ΔRMS {ev['rms_delta_db']:+.2f} dB · 相关 {ev['corr']:.4f} · "
          f"2–6 kHz 相对变化 {ev['presence_rel_db']:+.2f} dB · 对齐滞后 {ev['lag_samples']} 样本")
    print(f"  样本数 {ev['samples']}（{ev['seconds']:.4f}s）与原素材逐样本一致 · "
          f"原素材 sha256 未变（{ev['src_sha256'][:12]}…）")
    if a.report:
        Path(a.report).expanduser().write_text(
            json.dumps({"accepted": True, **ev}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
