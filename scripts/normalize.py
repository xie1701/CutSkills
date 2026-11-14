#!/usr/bin/env python3
"""CutSkills · 素材规整：可变帧率（VFR）→ 恒定帧率（CFR），音频一个采样都不动。

为什么必须有这一步：
  手机（尤其 iPhone）在暗光、省电、长时间录制时会动态掉帧/补帧，拍出来就是 VFR——
  容器里 `r_frame_rate` 标的是它的「峰值帧率」，`avg_frame_rate` 才是实测平均值。
  剪辑软件按恒定帧率理解这些时间戳时，会把每一帧重新摆到一张固定网格上；
  片子越长，网格与真实时间轴的偏差累得越多，于是「越剪越长，音画越走越偏」。
  所以入口只有一句：剪之前先把 VFR 规整成 CFR。

这一层只做一件事：把**时间戳**规整到恒定帧率网格。画面必须重编码（重定时没法 copy），
音频一律 `-c:a copy` 原样搬——音频一旦重采样/重编码，后面「音画同源」的门禁
（audio_map / sync_check）就全废了。所以转完当场验两道关：编码数据逐包 md5 是否没动、
解码样本是否逐字节一致；唯一放行的差异是「头部多出编码器预热样本」——
iPhone 写的 .MOV 用编辑表把 AAC 那 2112 个 priming samples 藏掉了，mp4 重写时会吐回来，
裁掉后逐字节相同、音频绝对时刻不变，那才不叫音画漂移。其余任何差异一律判红。

判定不看容器标记：r/avg 帧率不一致**或**实测帧间隔抖动超阈值都算 VFR；
两者都测不出来时明说「判不了」，绝不默认当 CFR（默认 CFR 会把漂移带进成片）。

产物写在 `<素材库>/_cfr/` 下（`--out` 可改），**用户原文件只读不动**。
`_cfr/manifest.json` 记：原文件、新文件、判定依据（原 r/avg 帧率 + 帧间隔统计 + 漂移推算）、
转码命令、转后实测（帧率、总帧数、时长差、音频 md5）。

用法：
  normalize.py --library "<素材库>" --dry-run                 # 只报哪些片子要转、为什么
  normalize.py --library "<素材库>"                           # 真转，默认 30fps
  normalize.py --library "<素材库>" --only 熊工 --fps 25       # 只处理路径含该子串的
  normalize.py --library "<素材库>" --out ~/Desktop/cfr

退出码：0 = 没有失败；1 = 有片子转码失败，或音频不再逐样本一致。
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_spec  # noqa: E402  帧率/色域/旋转的单一真值源

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts"}
CFR_DIR = "_cfr"
SYS_DIRS = {CFR_DIR, "_cutskills"}          # 自己的产物目录，扫描时跳过

# ---- 判定阈值（都写在这里，改判据只能改这一处，注释里写清为什么是这个数）
# 帧间隔偏离中位数超过 2% 就算「这一帧脱离了恒定网格」。2% 的依据：30fps 的名义间隔
# 33.333ms，mp4 时间基取整（1/12800）造成的天然误差 < 0.1%，远低于 2%；真的掉帧/补帧
# 一次至少差半帧（>30%），不会卡在 2% 边上。
INTERVAL_TOL = 0.02
# 抖动的帧占比超过 5% 就整条判 VFR。5% 的依据：单条素材偶尔因为编码器收尾丢 1–2 帧很常见
# （3 秒 90 帧丢 2 帧 = 2.2%），那不叫 VFR；超过 5% 才是手机在动态变速，剪起来一定出事。
JITTER_RATIO = 0.05
# r_frame_rate 与 avg_frame_rate 差 > 0.05 就是容器层面的不自洽（29.97 的 r 与 avg 差 0.03，
# 属于正常非整数帧率，不能误判）。口径与 media_spec.handling_of 的 vfr 码一致。
FPS_MISMATCH = 0.05
# 帧率读数超出这个范围就认为是错的读数（有些 mkv 会填 r=1000），不拿去下结论。
FPS_SANE = (1.0, 240.0)

# 采样窗口：长片整条读帧太慢（1 万帧要几十秒），VFR 的抖是整段性的，取窗即可判定。
SCAN_WINDOWS = 3
WINDOW_S = 6.0
MAX_FRAMES = 600

# 重编码参数。crf 18 + veryfast：这一层是**中间片**不是成片，要在「肉眼无差」和
# 「一次编译等太久」之间平衡；成片仍由 render_hf 按交付参数重压。
CRF = 18
PRESET = "veryfast"

ONE_FRAME_TOL = 0.002        # 时长差判定的额外容差（mp4 时间基取整，2ms 以内不算问题）


# ---------------------------------------------------------------- 基础工具

def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe_json(path: Path) -> dict:
    p = _run(["ffprobe", "-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", str(path)])
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def _fps_str(fps: float) -> str:
    """30 → "30"，29.97 → "29.97"：别给 ffmpeg 送 29.970000000001 这种数。"""
    return f"{fps:.3f}".rstrip("0").rstrip(".") or "0"


def _fmt_ms(ms: float) -> str:
    """毫秒差 → 人话。小于 1 秒说毫秒，超过就说秒/分。"""
    a = abs(ms)
    if a < 1000:
        return f"{a:.0f}ms"
    if a < 60000:
        return f"{a / 1000:.1f} 秒"
    return f"{int(a // 60000)} 分 {int(round((a % 60000) / 1000))} 秒"


# ---------------------------------------------------------------- 帧时间戳采样

def frame_times(src: Path, duration_s: float, *, windows: int = SCAN_WINDOWS,
                window_s: float = WINDOW_S, max_frames: int = MAX_FRAMES) -> list[float]:
    """采一段帧的显示时间（pts，秒）。整条读完太慢，所以按「开头 + 等分处」取窗。

    读的是**包（packet）时间戳**而不是解码后的 frame：帧率是容器层的事，不必把 1GB 的
    4K 画面真解一遗（实测同一支片：packet 0.1s vs frame 6.1s，结果一模一样）。
    真有容器只给 dts / 不给 pts 时，再退回 frame 那条路。
    """
    if duration_s <= 0:
        duration_s = 0.0
    spans: list[tuple[float, float]] = []
    if duration_s and duration_s > window_s * windows:
        step = duration_s / windows
        for i in range(windows):
            start = max(0.0, i * step + step * 0.15)
            spans.append((start, window_s))
    else:
        spans.append((0.0, max(window_s, duration_s or window_s)))

    def _read(kind: str) -> list[float]:
        out: list[float] = []
        for start, dur in spans:
            interval = f"{start:.3f}%+{dur:.3f}" if start > 0 else f"%+{dur:.3f}"
            p = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                      "-show_entries", f"{kind}=pts_time", "-of", "csv=p=0",
                      "-read_intervals", interval, str(src)])
            if p.returncode != 0:
                continue
            for line in (p.stdout or "").splitlines():
                raw = line.strip().rstrip(",")
                if not raw or raw.upper() == "N/A":
                    continue
                try:
                    out.append(float(raw))
                except ValueError:
                    continue
            if len(out) >= max_frames:
                break
        out.sort()
        return out[:max_frames]

    times = _read("packet")
    if len(times) < 4:
        times = _read("frame")
    return times


def interval_stats(times: list[float]) -> dict | None:
    """帧间隔统计。两个口径必须说清楚：

    · 用中位数不用平均：偶发一次长间隔（手机省电暂停）会把平均值带偏；
      中位数仍落在真实网格上，判「哪些帧脱网格」才准；
    · **只在采样窗内部统计**（_sample_runs 切段）：窗与窗之间本来就不连续，
      把窗口边界当成一帧间隔去算是错的，会让一个正常片子的 jitter 凭白飘高。"""
    runs = [r for r in _sample_runs(times) if len(r) >= 2]
    deltas = [b - a for run in runs for a, b in zip(run, run[1:]) if b - a > 1e-6]
    if len(deltas) < 3:
        return None
    med = statistics.median(deltas)
    if med <= 0:
        return None
    devs = [abs(d - med) / med for d in deltas]
    off = [d for d, x in zip(deltas, devs) if x > INTERVAL_TOL]
    sd = statistics.pstdev(deltas)
    return {
        "n_intervals": len(deltas),
        "median_ms": round(med * 1000, 2),
        "min_ms": round(min(deltas) * 1000, 2),
        "max_ms": round(max(deltas) * 1000, 2),
        "p95_ms": round(sorted(deltas)[max(0, int(len(deltas) * 0.95) - 1)] * 1000, 2),
        "stdev_ms": round(sd * 1000, 2),
        "jitter_ratio": round(len(off) / len(deltas), 4),
        "span_s": round(sum(r[-1] - r[0] for r in runs), 3),
        "windows": len(runs),
    }


def _sample_runs(times: list[float]) -> list[list[float]]:
    """把采样点按「中间断了很久」切成几段。

    采样是分窗的（开头、1/3、2/3 各一窗），窗与窗之间隔了几十秒；把它们当成一串连续帧
    算漂移，窗口边界就会被当成「一口气漂了几十秒」，读数直接离谱。
    """
    if not times:
        return []
    gaps = [b - a for a, b in zip(times, times[1:])]
    med = statistics.median(gaps) if gaps else 0.0
    thr = max(0.25, med * 8)
    runs: list[list[float]] = [[times[0]]]
    for a, b in zip(times, times[1:]):
        if b - a > thr:
            runs.append([b])
        else:
            runs[-1].append(b)
    return runs


def drift_estimate(times: list[float], nominal_fps: float) -> dict:
    """把「真实帧时间戳 vs 恒定帧率网格」的差拆成两笔，各说各的：

      累积项（线性斜率）：每过 1 秒画面相对时间轴偏多少 ms —— 这才是「剪得越长漂越大」
        的来源，可以外推到任意时长；
      抖动项（拟合残差）：忽快忽慢造成的往返误差，不会累加，只会造成单点重影/卡顿。

    两笔都从实测 pts 最小二乘算出来，不猜、不套固定系数。**每窗单独拟合**（见 _sample_runs）：
    帧序号只在窗内连续，跨窗拼成一条直线算出来的斜率是假的。
    """
    runs = [r for r in _sample_runs(times) if len(r) >= 4]
    if not runs or nominal_fps <= 0:
        return {}
    nom = 1.0 / nominal_fps
    slopes: list[float] = []
    resid_max = 0.0
    endpoint = 0.0
    span = 0.0
    for run in runs:
        xs = [t - run[0] for t in run]
        # 误差 = 「网格以为它在哪」−「它真在哪」，正值 = 画面滞后于时间轴
        errs = [(i * nom - x) * 1000 for i, x in enumerate(xs)]
        n = len(xs)
        mx = sum(xs) / n
        my = sum(errs) / n
        den = sum((x - mx) ** 2 for x in xs)
        slope = (sum((x - mx) * (y - my) for x, y in zip(xs, errs)) / den) if den > 1e-9 else 0.0
        intercept = my - slope * mx
        resid_max = max(resid_max, max(abs(y - (intercept + slope * x))
                                       for x, y in zip(xs, errs)))
        slopes.append(slope)
        if xs[-1] >= span:
            span = xs[-1]
            endpoint = errs[-1]
    slope = statistics.median(slopes)
    return {
        "nominal_fps": round(nominal_fps, 3),
        "rate_ms_per_s": round(slope, 2),        # 累积漂移速率，正 = 画面滞后
        "endpoint_ms": round(endpoint, 2),
        "residual_ms": round(resid_max, 2),
        "span_s": round(span, 3),
        "windows": len(runs),
    }


def judge(src: Path) -> dict:
    """一条素材的帧率判定。返回 verdict ∈ {vfr, cfr, unknown} + 人读理由 + 全部读数。"""
    info = probe_json(src)
    if not info:
        return {"verdict": "unknown", "reason": "ffprobe 读不出来（文件损坏或不是视频），判不了", "basis": {}}
    spec = media_spec.spec_of(info)
    if not spec.get("vcodec"):
        return {"verdict": "unknown", "reason": "没有视频流（纯音频/纯图片），判不了", "basis": {}}
    dur = float(spec.get("duration") or 0.0)
    r_fps = float(spec.get("fps") or 0.0)
    avg_fps = float(spec.get("avg_fps") or 0.0)
    times = frame_times(src, dur)
    st = interval_stats(times)
    basis: dict = {
        "duration_s": dur,
        "r_fps": r_fps,
        "avg_fps": avg_fps,
        "sampled_frames": len(times),
        "readings": st or {},
    }
    if st:
        basis["drift"] = drift_estimate(times, r_fps if r_fps > 1 else
                                       (1.0 / (st["median_ms"] / 1000) if st["median_ms"] else 0))
        # 第二笔账：整条平均帧率。窗口只能看到 6 秒，而「剪到 N 分钟漂多少」问的是整条，
        # 所以再拿容器的 avg_frame_rate 与 r_frame_rate 的差反推一遍（覆盖全片，不受采样窗局限）。
        if FPS_SANE[0] <= r_fps <= FPS_SANE[1] and FPS_SANE[0] <= avg_fps <= FPS_SANE[1]:
            drift = basis["drift"]
            drift["rate_from_avg_ms_per_s"] = round((avg_fps / r_fps - 1) * 1000, 2)
            drift["avg_note"] = "按整条平均帧率推算（容器 avg_frame_rate / r_frame_rate）"

    # 帧率读数不合理 → 不拿去下结论（宁可说「判不了」也不默认 CFR）
    if not (FPS_SANE[0] <= r_fps <= FPS_SANE[1]) and not (FPS_SANE[0] <= avg_fps <= FPS_SANE[1]):
        return {"verdict": "unknown", "basis": basis,
                "reason": f"帧率读数不合理（r={r_fps} / avg={avg_fps}），判不了"}
    if not st:
        return {"verdict": "unknown", "basis": basis,
                "reason": f"只采到 {len(times)} 帧的时间戳，间隔统计不出来，判不了"}

    mismatch = (FPS_SANE[0] <= r_fps <= FPS_SANE[1] and FPS_SANE[0] <= avg_fps <= FPS_SANE[1]
                and abs(r_fps - avg_fps) > FPS_MISMATCH)
    jittered = st["jitter_ratio"] > JITTER_RATIO
    hits = []
    if mismatch:
        hits.append(f"容器 r={r_fps:g}fps 与实测 avg={avg_fps:g}fps 不一致"
                    f"（差 {abs(r_fps - avg_fps):.2f}）")
    if jittered:
        hits.append(f"{st['jitter_ratio'] * 100:.0f}% 的帧间隔脱出恒定网格"
                    f"（中位 {st['median_ms']:g}ms / 最小 {st['min_ms']:g}ms / 最大 {st['max_ms']:g}ms）")
    if hits:
        return {"verdict": "vfr", "basis": basis, "reason": "；".join(hits)}
    return {"verdict": "cfr", "basis": basis,
            "reason": f"帧间隔稳定在 {st['median_ms']:g}ms（r={r_fps:g} / avg={avg_fps:g} 一致）"}


def drift_sentence(res: dict) -> str:
    """把漂移换算成人话：先说「按容器标的帧率硬剪会怎样」，再说这条片子整段差多少。

    两笔账都说出来：
      · 实测斜率（窗口内连续帧，如实但只看得到几秒）；
      · 整条平均帧率反推（覆盖全片，但依赖容器的 nb_frames/duration 可信）。
    两者差得远时两个数都报——一个含含糊糊的数比两个真数差。
    """
    basis = res.get("basis") or {}
    d = basis.get("drift") or {}
    if not d:
        return "漂移推算不出来（采样帧不够）——转完以下面的时长差读数为准。"
    local = float(d.get("rate_ms_per_s") or 0.0)
    whole = d.get("rate_from_avg_ms_per_s")
    nom = float(d.get("nominal_fps") or 0.0)
    span = float(d.get("span_s") or 0.0)
    dur = float(basis.get("duration_s") or 0.0)
    rate = local
    if whole is not None and abs(float(whole)) > abs(local):
        rate = float(whole)              # 取账大的一边，宁可高估不可漏掉
    per_min = abs(rate) * 60
    if per_min < 1:
        return (f"按容器标的 {nom:g}fps 硬当恒定帧率剪，累计漂移 <1ms/分钟——"
                f"局部抖动最多 {d.get('residual_ms', 0):g}ms，不累积。")
    dirn = "画面滞后于声音" if rate > 0 else "画面跑到声音前面"
    tail = ""
    if dur:
        how = (f"按 {dur:.1f} 秒全片外推"
               if whole is not None and rate == float(whole)
               else f"按 {span:.1f} 秒采样外推")
        tail = f"；这条 {dur:.1f} 秒的片子整段就会差 {_fmt_ms(rate * dur)}（{how}）。"
    both = ""
    if whole is not None and local != 0.0 and abs(float(whole)) > abs(local) * 3:
        both = (f"（采样窗内实测只有 {_fmt_ms(abs(local) * 60)}/分钟，"
                f"差额落在窗外那些掉帧上）")
    return (f"按容器标的 {nom:g}fps 硬当恒定帧率剪，每剪 1 分钟{dirn}约 {_fmt_ms(per_min)}"
            f"——剪得越长漂得越多{both}{tail}")


# ---------------------------------------------------------------- 转码

def _color_args(spec: dict) -> list[str]:
    """色域/传输特性原样带上：这一层只动时间戳，顺手把 HDR 标签弄丢的话，
    后面 tone map 就无从下手（media_spec 的 hdr_* 码全部依赖这些标签）。"""
    c = spec.get("color") or {}
    out: list[str] = []
    for key, flag in (("primaries", "-color_primaries"), ("transfer", "-color_trc"),
                      ("space", "-colorspace"), ("range", "-color_range")):
        v = c.get(key)
        if v and v not in {"unknown", "reserved"}:
            out += [flag, str(v)]
    return out


def build_cmd(src: Path, dst: Path, fps: float, spec: dict, *, audio: str) -> list[str]:
    """audio="copy" → 音频原样搬；audio="aac" → 容器装不下时的显式兜底（会在报告里点名）。"""
    pix = "yuv420p10le" if int(spec.get("bit_depth") or 8) > 8 else "yuv420p"
    cmd = [_ff(), "-v", "error", "-y", "-i", str(src),
           "-map", "0:v:0", "-map", "0:a?",
           "-fps_mode", "cfr", "-r", _fps_str(fps),
           "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET, "-pix_fmt", pix]
    cmd += _color_args(spec)
    if audio == "copy":
        cmd += ["-c:a", "copy"]
    else:
        au = spec.get("audio") or {}
        cmd += ["-c:a", "aac", "-b:a", "192k"]
        if au.get("sample_rate"):
            cmd += ["-ar", str(au["sample_rate"])]   # 只锁原采样率，不做任何重采样
        if au.get("channels"):
            cmd += ["-ac", str(au["channels"])]
    cmd += [str(dst)]
    return cmd


def _ff() -> str:
    from libs import ffmpeg_bin
    return ffmpeg_bin()


def _md5_of(cmd: list[str]) -> str | None:
    p = _run(cmd)
    if p.returncode != 0:
        return None
    for line in (p.stdout or "").splitlines():
        if line.startswith("MD5="):
            return line.split("=", 1)[1].strip()
    return None


def audio_digest(path: Path) -> dict | None:
    """音频指纹：编码数据（包）、解码样本（PCM）、流参数三样一起记。

    为什么两样都要：
      · 包 md5：证明 `-c:a copy` 真的没动编码数据（没重编码、没重采样）；
      · 解码 PCM md5：证明放到时间轴上听到的东西也没变。
    """
    info = probe_json(path)
    a = next((s for s in (info.get("streams") or []) if s.get("codec_type") == "audio"), None)
    if not a:
        return None
    pkt = _md5_of([_ff(), "-v", "error", "-i", str(path), "-map", "0:a:0",
                   "-c", "copy", "-f", "md5", "-"])
    pcm = _md5_of([_ff(), "-v", "error", "-i", str(path), "-map", "0:a:0",
                   "-c:a", "pcm_s16le", "-f", "md5", "-"])
    if not pcm:
        return None
    try:
        start = round(float(a.get("start_time") or 0.0), 6)
    except (TypeError, ValueError):
        start = 0.0
    return {
        "codec": a.get("codec_name"),
        "sample_rate": int(a["sample_rate"]) if str(a.get("sample_rate", "")).isdigit() else None,
        "channels": a.get("channels"),
        "start_s": start,
        "packet_md5": pkt,
        "pcm_md5": pcm,
    }


def _pcm_trimmed_md5(path: Path, trim_samples: int) -> str | None:
    """去掉开头 trim_samples 个解码样本后的 PCM md5（atrim 只裁已解码的样本，不重编码）。"""
    return _md5_of([_ff(), "-v", "error", "-i", str(path), "-map", "0:a:0",
                    "-af", f"atrim=start_sample={int(trim_samples)}",
                    "-c:a", "pcm_s16le", "-f", "md5", "-"])


def audio_fidelity(before: dict | None, after: dict | None, src: Path, dst: Path) -> dict:
    """音频保真的判据：两道关，过了才算没伤音频。

    ① 编码数据逐包一致（before/after 的 packet_md5 相同）——证明走的是 `-c:a copy`，
       没有被重编码/重采样。
    ② 解码样本对得上：
       · exact：PCM md5 完全相同，最干净；
       · priming：PCM 只差在**头部多出编码器预热样本**（AAC 固定 1024/2048 个 priming samples）。
         真实原因：iPhone 写的 .MOV 用编辑表把那几十毫秒预热藏掉了，而 mp4 容器重写时
         会把它吐回来。这时必须再验一步：把多出来的那段裁掉后，剩下样本与源逐字节相同，
         且第一帧音频的**绝对时刻**不变——那才是「音画不漂」，不是「差不多」。
       · mismatch：其余情况一律算音频被动了（失败）。
    """
    if before is None:
        return {"status": "none", "bit_exact": True,
                "why": "无音轨（纯画面素材），不涉及音频保真"}
    if after is None:
        return {"status": "mismatch", "bit_exact": False, "why": "转后音频流丢了"}
    out = {
        "coded_identical": bool(before.get("packet_md5") and
                                before.get("packet_md5") == after.get("packet_md5")),
        "pcm_md5_before": before.get("pcm_md5"),
        "pcm_md5_after": after.get("pcm_md5"),
        "sample_rate": before.get("sample_rate"),
        "channels": before.get("channels"),
    }
    if before.get("pcm_md5") == after.get("pcm_md5"):
        out.update({"status": "exact", "bit_exact": True,
                    "why": "解码样本逐字节相同（-c:a copy，未重采样）"})
        return out

    rate = int(before.get("sample_rate") or 0)
    delta_s = float(after.get("start_s") or 0.0) - float(before.get("start_s") or 0.0)
    if out["coded_identical"] and rate and abs(delta_s) > 1e-4:
        priming = int(round(abs(delta_s) * rate))
        if delta_s < 0:      # 新文件音频开始得更早 = 头部多了预热样本，裁新文件
            got, ref = _pcm_trimmed_md5(dst, priming), before.get("pcm_md5")
        else:                # 反过来：源多，裁源
            got, ref = after.get("pcm_md5"), _pcm_trimmed_md5(src, priming)
        if got and ref and got == ref:
            out.update({
                "status": "priming", "bit_exact": True,
                "priming_samples": priming,
                "priming_ms": round(priming / rate * 1000, 2),
                "content_align_ms": 0.0,
                "why": (f"编码数据逐包一致；解码只在头部差 {priming} 个编码器预热样本"
                        f"（{priming / rate * 1000:.1f}ms）——裁掉后逐字节相同，"
                        f"音频绝对时刻不变（音画不漂）"),
            })
            return out
    out.update({"status": "mismatch", "bit_exact": False,
                "why": "解码样本既不一致、也不是预热样本造成的偏移——音频被动了"})
    return out


def _stream_readings(path: Path) -> dict:
    """转后实测：帧率、总帧数、时长——都从文件本身重新 probe，不引用命令参数。"""
    info = probe_json(path)
    spec = media_spec.spec_of(info)
    v = next((s for s in (info.get("streams") or []) if s.get("codec_type") == "video"), None)
    nb = None
    if v:
        for key in ("nb_frames", "nb_read_frames"):
            if str(v.get(key) or "").isdigit():
                nb = int(v[key])
                break
    return {
        "r_fps": float(spec.get("fps") or 0.0),
        "avg_fps": float(spec.get("avg_fps") or 0.0),
        "nb_frames": nb,
        "duration_s": float(spec.get("duration") or 0.0),
        "video_duration_s": round(float(v.get("duration") or 0.0), 3) if v else 0.0,
    }


def transcode(src: Path, lib: Path, out_dir: Path, fps: float, spec: dict) -> dict:
    """真转一条。返回 manifest 一条记录（含失败原因）。

    容器策略：先 mp4 + `-c:a copy`；装不下原音频编码（例如 PCM）再试 mov + copy；
    两个容器都装不下才显式重编码音频——那一条会在报告里点名，且整体算失败。
    """
    rel = src.relative_to(lib)
    stem = src.stem
    dst_mp4 = out_dir / rel.parent / f"{stem}.cfr{_fps_str(fps)}.mp4"
    dst_mov = dst_mp4.with_suffix(".mov")
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)

    src_info = probe_json(src)
    src_v = next((s for s in (src_info.get("streams") or [])
                  if s.get("codec_type") == "video"), {})
    src_video_dur = float(src_v.get("duration") or spec.get("duration") or 0.0)
    before = audio_digest(src)
    attempts: list[dict] = []
    for audio, dst in (("copy", dst_mp4), ("copy", dst_mov), ("aac", dst_mp4)):
        cmd = build_cmd(src, dst, fps, spec, audio=audio)
        p = _run(cmd)
        attempts.append({"dst": str(dst), "audio": audio, "returncode": p.returncode,
                         "stderr": (p.stderr or "").strip()[:400]})
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            after = audio_digest(dst)
            fidelity = audio_fidelity(before, after, src, dst)
            post = _stream_readings(dst)
            one_frame_ms = 1000.0 / fps
            fmt_delta = (post["duration_s"] - float(spec.get("duration") or 0.0)) * 1000
            vid_delta = (post["video_duration_s"] - src_video_dur) * 1000
            # 判「漂了多少」的主口径是**容器总时长**（编辑器拿它对音轨，也是 BRIEF 说的
            # 「原片时长 vs 转后时长」）。视频流时长会被对齐到整帧边界，源片尾那一帧本来就
            # 可能不足一帧，所以它只作旁证：超过 1 帧就点名，但不冒充「内容被吃掉」。
            within = abs(fmt_delta) <= one_frame_ms + ONE_FRAME_TOL
            video_within = abs(vid_delta) <= one_frame_ms + ONE_FRAME_TOL
            return {
                "ok": True,
                "orig": str(src),
                "out": str(dst),
                "cmd": cmd,
                "audio": {
                    "mode": "none" if before is None else ("copy" if audio == "copy" else "reencode"),
                    "codec": (after or {}).get("codec"),
                    "bit_exact": bool(fidelity.get("bit_exact")),
                    "fidelity": fidelity,
                    "before": before, "after": after,
                },
                "post": post,
                "delta": {
                    "format_ms": round(fmt_delta, 2),
                    "video_ms": round(vid_delta, 2),
                    "one_frame_ms": round(one_frame_ms, 2),
                    "within_one_frame": within,
                    "video_within_one_frame": video_within,
                },
                "container_fallbacks": attempts[:-1],
            }
        # 失败：清掉半截产物，别留在 _cfr 里让人误以为转好了
        if dst.exists():
            dst.unlink()
    return {"ok": False, "orig": str(src), "attempts": attempts}


# ---------------------------------------------------------------- 扫描/编排

def iter_videos(root: Path, only: str | None = None):
    """扫素材库（跳过 _cfr / _cutskills / 隐藏目录），按路径排序保证可复现。"""
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
            continue
        rel = p.relative_to(root)
        if any(part in SYS_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        if only and only not in rel.as_posix():
            continue
        out.append(p)
    return out


def load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"version": 1, "entries": {}}


def main() -> int:
    ap = argparse.ArgumentParser(description="VFR → CFR 素材规整（音频逐样本保留）")
    ap.add_argument("--library", required=True, help="素材库根目录")
    ap.add_argument("--only", default=None, help="只处理路径含该子串的素材")
    ap.add_argument("--fps", type=float, default=30.0, help="目标恒定帧率（默认 30）")
    ap.add_argument("--out", default=None, help="产物目录（默认 <素材库>/_cfr）")
    ap.add_argument("--dry-run", action="store_true", help="只报告哪些需要转、为什么，不写任何文件")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    if not lib.is_dir():
        raise SystemExit(f"素材库不存在：{lib}")
    if a.fps <= 0:
        raise SystemExit("--fps 必须大于 0")
    out_dir = Path(a.out).expanduser().resolve() if a.out else lib / CFR_DIR

    videos = iter_videos(lib, a.only)
    print(f"素材库：{lib}｜候选视频 {len(videos)} 支｜目标恒定帧率 {_fps_str(a.fps)}fps")
    if not a.dry_run:
        print(f"产物目录：{out_dir}（原文件只读，不动）")

    todo, cfr, unknown = [], [], []
    for src in videos:
        rel = src.relative_to(lib).as_posix()
        j = judge(src)
        if j["verdict"] == "vfr":
            todo.append((rel, src, j))
        elif j["verdict"] == "cfr":
            cfr.append((rel, j))
        else:
            unknown.append((rel, j))

    for rel, j in cfr:
        print(f"  ⏭  {rel}｜已是恒定帧率，跳过（{j['reason']}）")
    for rel, j in unknown:
        print(f"  ⚠  {rel}｜判不了：{j['reason']}")

    if a.dry_run:
        print()
        if not todo:
            print("没有需要规整的片子（本次 --dry-run 未写任何文件）。")
            return 0
        print(f"需要转成恒定帧率的片子 {len(todo)} 支（--dry-run：未写任何文件）：")
        for rel, src, j in todo:
            b = j["basis"]
            print(f"  • {rel}")
            print(f"      判定：{j['reason']}")
            print(f"      读数：r={b.get('r_fps'):g}fps / avg={b.get('avg_fps'):g}fps"
                  f" / 采样 {b.get('sampled_frames')} 帧 / 时长 {b.get('duration_s', 0):.2f}s")
            print(f"      漂移：{drift_sentence(j)}")
        print()
        print(f"预计产物：{out_dir}/<原相对路径>/<原名>.cfr{_fps_str(a.fps)}.mp4")
        return 0

    if not todo:
        print("没有需要规整的片子。")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    man_p = out_dir / "manifest.json"
    man = load_manifest(man_p)
    man.update({"version": 1, "fps": a.fps, "library": str(lib),
                "updated": datetime.now().isoformat(timespec="seconds")})
    entries = man.setdefault("entries", {})

    failures = 0
    for rel, src, j in todo:
        info = probe_json(src)
        spec = media_spec.spec_of(info)
        print(f"\n▶ {rel}｜{j['reason']}")
        # 重编码躲不掉（重定时没法 copy），所以 HDR 类素材的这一层只能保住基础信号：
        # 杜比视界的逐帧动态元数据进不了 x264，必须当场说清，别让人以为「转完还是 DV」。
        warns: list[str] = []
        codes = media_spec.handling_of(spec)
        if "dolby_vision" in codes:
            warns.append("杜比视界：重编码后只剩基础层（HLG/PQ），逐帧动态元数据丢失；"
                         "这条要交付 HDR 就别用本层产物")
        for w in warns:
            print(f"  ⚠ {w}")
        rec = transcode(src, lib, out_dir, a.fps, spec)
        if not rec.get("ok"):
            failures += 1
            print("  ❌ 转码失败：")
            for at in rec.get("attempts") or []:
                print(f"     [{at['audio']}→{Path(at['dst']).suffix}] rc={at['returncode']}"
                      f" {at['stderr'][:200]}")
            entries[rel] = {"orig": str(src), "ok": False, "attempts": rec.get("attempts"),
                            "verdict": j, "at": datetime.now().isoformat(timespec="seconds")}
            continue

        au, delta, post = rec["audio"], rec["delta"], rec["post"]
        fid = au.get("fidelity") or {}
        if not au["bit_exact"]:
            failures += 1
            print(f"  🔴 音频保真没过（mode={au['mode']}）：{fid.get('why')}"
                  f"——这条上线前必须人工确认，音画同源会失效")
        fps_ok = abs(post["r_fps"] - post["avg_fps"]) < 1e-3
        print(f"  ✅ 转后帧率 r={post['r_fps']:g} / avg={post['avg_fps']:g}"
              f"（{'恒定' if fps_ok else '仍不自洽'}）｜总帧数 {post['nb_frames']}｜"
              f"时长 {post['duration_s']:.3f}s（差 {delta['format_ms']:+.0f}ms，"
              f"1 帧 = {delta['one_frame_ms']:.1f}ms）")
        if au["mode"] == "none":
            audio_line = "无音轨（纯画面素材），不涉及音频保真"
        else:
            bp, ap = fid.get("pcm_md5_before") or "", fid.get("pcm_md5_after") or ""
            audio_line = (f"音频 mode={au['mode']} 包 md5 "
                          f"{(au['before'] or {}).get('packet_md5', '')[:8]}→"
                          f"{(au['after'] or {}).get('packet_md5', '')[:8]}"
                          f"｜PCM {bp[:8]}→{ap[:8]}｜{fid.get('status')}：{fid.get('why')}")
        print(f"  {'✅' if delta['within_one_frame'] else '⚠'} "
              f"{'时长差在 1 帧内' if delta['within_one_frame'] else '时长差超过 1 帧，复核素材'}"
              f"（视频流 {delta['video_ms']:+.0f}ms "
              f"{'也在 1 帧内' if delta.get('video_within_one_frame') else '超出 1 帧：尾帧对齐到整帧边界，看一眼尾巴'}）"
              f"｜{audio_line}")
        print(f"  漂移：{drift_sentence(j)}")

        entries[rel] = {
            "orig": str(src),
            "out": rec["out"],
            "ok": True,
            "fps": a.fps,
            "verdict": j,
            "cmd": rec["cmd"],
            "audio": au,
            "post": post,
            "delta": delta,
            "drift_human": drift_sentence(j),
            "rotation_applied": bool(spec.get("rotation")),
            "video_reencoded": {"crf": CRF, "preset": PRESET,
                                "why": "重定时（改时间戳）必须重新解码编码，无法 copy"},
            "container_fallbacks": rec.get("container_fallbacks"),
            "warnings": warns,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        man_p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n汇总：转过 {len(todo) - failures} 支 · 失败 {failures} 支 · "
          f"已是恒定帧率 {len(cfr)} 支 · 判不了 {len(unknown)} 支")
    print(f"清单：{man_p}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
