#!/usr/bin/env python3
"""场景自审：给一段场景视频（或成片里的一格）出一份「每条判据都带读数」的复核报告。

为什么另起一个脚本：`verify_film` 验的是**成片**（响度、字幕、卡位），
`measure_motion` 只回答「动没动」，两样都答不出「这一格的关键动作到底有没有在锚点上发生」。
代码画的场景最常坏的不是配色，是**动作和锚点错位**：事件写 1.2s，托盘 1.1s 就到顶了；
或者 4 秒里只有 1 秒在动。这类毛病在 10fps 均匀抽帧里看不见——动作只占那 0.1 秒。

所以四件事：

  1. 关键动作密集采样：每个事件前后各 0.12s、按**原生帧率逐帧**抽（不是均匀抽），
     量「窗口里到底有没有帧差」「变化起点离锚点差几帧」。均匀抽帧会把动作整段跳过。
  2. 接触表：采样帧拼成 `<out>/scene-review/sheet.png`，每格带时间标签，
     不用打开视频就能看出哪一帧开始动、锚点对在哪两格之间。
  3. 静音 / 响度 / 偏移：静音段走 `silence.py`（相对阈值，不是 silencedetect 的绝对阈值）；
     响度走 ffmpeg `ebur128`（集成响度 + 真峰）；「偏移」= 视频实长 vs 时间轴上这一格的
     `dur`，|差| > 0.05s 判红（0.05s 是半帧多一点，超过它后面每一格的音画都对不上）。
  4. 局部返工建议：每条红/黄给一句「改哪里」，指到事件号、毫秒数与具体动作。

一个口径要说清：音效可听度直接调 `check_sfx.best_transient_lift()`（同窗净抬升，
无 stem 时对照组取事件前后三段基线），并按事件名路由主能量频段；
所以拿成片里截的一段来跑，读数会和 `check_sfx --project` 跑全片的差几 dB——量的是同一件事，
对照窗口落在不同的位置（实测 ground-01 天平那两个事件：66.26s 全片口径 +4.9dB、4s 片段 +2.5dB）。
想复现成片的结论就把全片喂给它，想看单格就喂这一格，两种读数都如实打印，不互相覆盖。

判据与含义见 references/scene-art-standard.md §4（可量判据）与 §8（交付前自查）。
阈值一个都不新造：动帧占比 / 最长静止 / 帧差中位复用 `measure_motion` 的常量与实现（`--median-band` 原样透传给它的 `judge()`），
音效可听度复用 `check_sfx` 的同窗净抬升接口与频段路由，静音段复用 `silence.py`，
本文件只加「事件锚点」这一层——它不在那两个脚本的视野里。

用法：
    python3 scripts/scene_review.py --video <场景.mp4> --dur <时间轴上的秒数> \
        [--events /tmp/ev.json] [--out <目录>] [--json <报告.json>] \
        [--median-band 1.2:6.0]

    # --median-band 只换「帧差中位」这一条的合格区间，默认 1.2:6.0（代码场景口径）。
    # 实拍/手持/推镜的底片帧差天然 8–13（是镜头在动），给 6:20，否则每次假红。

    # /tmp/ev.json 就是场景模块的 EVENTS：`[{"t":1.2,"name":"pop"}, ...]`（场景内相对秒）
    # 不给 --events 就只做均匀采样（接触表仍出，事件层跳过）

退出码：0 = 没有红；1 = 至少一条红；2 = 参数 / 素材错（文件不在、读不出流）。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import measure_motion as MM       # noqa: E402  帧差剖面三判据（动帧占比 / 最长静止 / 帧差中位）
import silence as SLC             # noqa: E402  真静音段（相对阈值）
import check_sfx as CSF           # noqa: E402  2–8kHz 抬升 + 对照组（音效到底听不听得见）

WARPIN = 0.12        # 事件前后各取多宽（秒）——动作只发生在这 0.24s 里
OFFSET_TOL = 0.05    # 视频实长 vs 时间轴 dur 的容差
EVENT_SILENCE = 0.30 # 事件起点后这么久全是静音 = 音效没混进来
LOUD_MIN = -45.0     # 集成响度低于它 = 整段几乎没声音
LOUD_MAX = -8.0      # 高于它 = 这段过响（成片目标是 −14 LUFS 左右）
TPK_MAX = -1.0       # 真峰上限，过了就是削顶风险
# 抬升为多大负数才算「音效压根没混进来」：正负 3–6dB 的口播/垫乐起伏很常见
# （实测同一格换个对照窗口就能差 5dB），所以只有比周围安静一大截（>12dB）
# 才敢说「没混进来」；−12dB 以内一律按「抬升不足」处理，两条建议都给。
ABSENT_DB = -12.0
SHEET_W = 150        # 接触表每格宽（px）
BG = (14, 12, 11)    # 与场景渲染同一套底色
GOLD = (246, 235, 52)
INK = (232, 228, 220)
DIM = (170, 165, 155)
LEVEL_COLOR = {"red": (193, 18, 31), "yellow": (230, 180, 34), "green": (70, 170, 100)}
ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}

# 逐条判据「为什么」——报告里不能只有数字
WHY = {
    "动帧占比": "低于 0.85 说明有大段站桩（观众看到的是「一张图放了 N 秒」）",
    "最长静止": "连续静止 ≥1.2s 就是卡住了，不是留白",
    "帧差中位": "代码场景才用 1.2–6.0（太低是动得没分量，太高是抖成频闪）",
}


# ── 素材探测与抽帧 ─────────────────────────────────────────────────────────────

def ffmpeg_bin() -> str:
    return MM.ffmpeg()


def probe(path: Path) -> dict:
    """宽高 / 原生帧率 / 实长 / 有没有音轨。"""
    exe = shutil.which("ffprobe") or "ffprobe"
    p = subprocess.run(
        [exe, "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate,avg_frame_rate,duration"
         ":format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"ffprobe 失败：{(p.stderr or '').strip()[-300:]}")
    d = json.loads(p.stdout or "{}")
    streams = d.get("streams") or []
    vs = [s for s in streams if s.get("codec_type") == "video"]
    if not vs:
        raise RuntimeError(f"没有视频流：{path}")
    st = vs[0]
    fps = 0.0
    for key in ("r_frame_rate", "avg_frame_rate"):     # "30/1"
        v = str(st.get(key) or "")
        try:
            if "/" in v:
                a, b = v.split("/")[:2]
                fps = float(a) / float(b) if float(b) else 0.0
            elif v:
                fps = float(v)
        except ValueError:
            fps = 0.0
        if 0 < fps <= 240:
            break
    dur = float(st.get("duration") or (d.get("format") or {}).get("duration") or 0)
    return {
        "w": int(st.get("width") or 0), "h": int(st.get("height") or 0),
        "fps": round(fps, 3) if 0 < fps <= 240 else 30.0,
        "dur": round(dur, 4),
        "audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def even_h(w: int, w0: int, h0: int) -> int:
    """按源画幅算高度（偶数，h264 友好）。"""
    return max(2, int(round(w * h0 / max(1, w0) / 2)) * 2)


def raw_window(path: Path, t0: float, dur: float, fps: float,
               w: int, h: int, ch: int = 1) -> tuple[list[float], np.ndarray]:
    """把 [t0, t0+dur] 按 fps 抽成小图序列，返回 (每帧时间, (n,h,w,ch) uint8)。

    一次 ffmpeg 解完整个窗口：事件窗口只有 0.24s，逐帧起进程几十次会慢在启动上。
    """
    pix = "gray" if ch == 1 else "rgb24"
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin"]
    if t0 > 0:
        cmd += ["-ss", f"{t0:.3f}"]
    cmd += ["-t", f"{max(0.02, dur):.3f}", "-i", str(path),
            "-vf", f"fps={fps:.6f},scale={w}:{h}:flags=bilinear,format={pix}",
            "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode or not p.stdout:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace")[-300:])
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    step = w * h * ch
    n = len(buf) // step
    arr = buf[: n * step].reshape(n, h, w, ch)
    return [round(t0 + i / fps, 4) for i in range(n)], arr


def grab_one(path: Path, t: float, w: int, h: int) -> Image.Image:
    """单帧（均匀采样用）。"""
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.4f}",
           "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={w}:{h}:flags=bilinear", "-f", "image2pipe",
           "-vcodec", "png", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode or not p.stdout:
        raise RuntimeError(f"抽帧失败 @ {t:.2f}s："
                           f"{(p.stderr or b'').decode('utf-8', 'replace')[-200:]}")
    return Image.open(io.BytesIO(p.stdout)).convert("RGB")


# ── 事件动作：密集采样 → 到底有没有动、动在哪一帧 ─────────────────────────────

def event_span(t: float, dur: float, fps: float) -> tuple[float, float]:
    """事件窗口 [t-0.12, t+0.12] 再各让出一帧，保证窗口两端各有一帧可比。"""
    fd = 1.0 / fps
    t0 = max(0.0, t - WARPIN)
    t1 = min(max(dur, fd), t + WARPIN + fd)
    return t0, max(t1, t0 + 2 * fd)


def window_diffs(path: Path, t: float, dur: float, fps: float,
                 w: int, h: int) -> dict:
    """窗口逐帧灰图 → 相邻帧差、峰值位置、起手位置。"""
    fd = 1.0 / fps
    t0, t1 = event_span(t, dur, fps)
    times, arr = raw_window(path, t0, t1 - t0, fps, w, h, 1)
    if len(arr) < 2:
        raise RuntimeError(f"窗口里只抽到 {len(arr)} 帧")
    a = arr.astype(np.float32)
    diffs = np.abs(np.diff(a, axis=0)).reshape(len(arr) - 1, -1).mean(axis=1)
    peak_i = int(np.argmax(diffs))
    return {
        "t": t,
        "frames": int(len(arr)),
        "times": times,
        "diffs": [round(float(x), 3) for x in diffs],
        "peak": round(float(diffs[peak_i]), 3),
        "peak_off": round(times[peak_i] + 0.5 * fd - t, 4),  # 峰值区间中点相对锚点
        "frame_ms": round(fd * 1000),
        "median": round(float(np.median(diffs)), 3),
    }


def judge_motion(m: dict, still_cut: float) -> tuple[str, str, str, str]:
    """(level, value, note, fix)。读数与判词分开，红项一定带数字。"""
    peak, off, fd_ms, t = m["peak"], m["peak_off"], m["frame_ms"], m["t"]
    if peak < still_cut:
        return ("red", f"峰值帧差 {peak:.2f}（噪声底 {still_cut:.2f}）",
                f"锚点前后 ±{int(WARPIN * 1000)}ms 一帧没变",
                f"这一格的动作要么在锚点之前就做完，要么根本没做 → 把动作起点对齐到 {t:.2f}s，"
                f"或补 ≤{int(WARPIN * 1000)}ms 的预备动作（位移/缩放起手）")
    # 有可辨认的起手（峰值明显高于本窗口常态）才谈「锚点偏了几帧」；
    # 持续运动的窗口没有唯一起点，硬报偏移就是拿噪声当读数。
    burst = peak >= max(still_cut * 1.5, 2.0 * max(m["median"], 1e-6))
    if not burst:
        return ("green", f"峰值 {peak:.2f} @ {off * 1000:+.0f}ms（窗口内一直在动）",
                "持续运动，没有单独起手可对——锚点落在这段运动里", "")
    onset = next((m["times"][i] for i, d in enumerate(m["diffs"])
                  if d >= max(still_cut, 0.5 * peak)), None)
    if onset is None:
        return ("green", f"峰值 {peak:.2f} @ {off * 1000:+.0f}ms", "起手落在窗口内", "")
    if onset <= m["times"][0] + 1e-6:
        return ("yellow", f"峰值 {peak:.2f}（窗口开头已在动，一帧 {fd_ms}ms）",
                f"起手早于 ±{int(WARPIN * 1000)}ms 窗口，锚点可能晚了",
                f"锚点 {t:.2f}s 处的变化在窗口之前就发生了 → 把 props.events 的 t 提前，"
                f"或让这段动作改在锚点处起手")
    delta_ms = round((onset - t) * 1000)
    if abs(delta_ms) <= 1.5 * fd_ms:
        return ("green", f"起手 {delta_ms:+d}ms · 峰值 {peak:.2f}（一帧 {fd_ms}ms）",
                "动作起手对着锚点（≤1.5 帧）", "")
    side = "早于" if delta_ms < 0 else "晚于"
    return ("yellow", f"起手{side}锚点 {abs(delta_ms)}ms（一帧 {fd_ms}ms）· 峰值 {peak:.2f}",
            f"起手{side}锚点 {abs(delta_ms)}ms，超过 1.5 帧",
            f"动作{side}锚点 {abs(delta_ms)}ms → 要么把 props.events 里这个事件的 t "
            f"{'提前' if delta_ms < 0 else '推后'} {abs(delta_ms)}ms，要么把这段动作整体"
            f"{'后移' if delta_ms < 0 else '提前'} {abs(delta_ms)}ms")


# ── 声音：静音段 / 响度 / 每个事件听不听得见 ──────────────────────────────────

def loudness(path: Path) -> dict:
    """ffmpeg ebur128 的集成响度 / 响度范围 / 真峰（取 Summary 段，不取逐帧行）。"""
    p = subprocess.run([ffmpeg_bin(), "-nostats", "-hide_banner", "-v", "info",
                        "-i", str(path), "-filter_complex", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    txt = p.stderr or ""
    i = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", txt)
    lra = re.findall(r"LRA:\s+(-?\d+(?:\.\d+)?)\s+LU\b", txt)
    tp = re.findall(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", txt)
    return {"I": float(i[-1]) if i else None,
            "LRA": float(lra[-1]) if lra else None,
            "TPK": float(tp[-1]) if tp else None}


def silence_stats(runs: list[tuple[float, float]], dur: float) -> dict:
    total = round(sum(e - s for s, e in runs), 3)
    return {"runs": [[round(s, 2), round(e, 2)] for s, e in runs],
            "total": total,
            "longest": round(max((e - s for s, e in runs), default=0.0), 3),
            "coverage": round(total / max(1e-6, dur) * 100, 1)}


# ── 接触表 ─────────────────────────────────────────────────────────────────────

def _font(size: int):
    for p in (Path.home() / "Library/Fonts/NotoSansSC-SemiBold.ttf",
              Path.home() / "Library/Fonts/NotoSansSC-Bold.ttf",
              Path.home() / "Library/Fonts/NotoSansSC-Black.ttf"):
        if p.exists():
            return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.load_default(size)      # PIL ≥ 10
    except TypeError:                            # 老 PIL 没有 size 参数
        return ImageFont.load_default()


def build_sheet(dst: Path, head: list[str], rows: list[dict]) -> None:
    """一眼能看的接触表：一行一个事件，每格一帧，格下是本格相对锚点的毫秒偏移。"""
    pad, gap = 18, 8
    tw = SHEET_W
    th = rows[0]["tiles"][0][1].height if rows and rows[0]["tiles"] else 240
    ncol = max(len(r["tiles"]) for r in rows) if rows else 1
    row_h = 28 + th + 24
    width = pad * 2 + ncol * tw + (ncol - 1) * gap
    height = pad * 2 + len(head) * 28 + 10 + len(rows) * row_h
    im = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(im)
    y = pad
    for k, line in enumerate(head):
        d.text((pad, y), line, font=_font(21 if k == 0 else 18), fill=INK)
        y += 28
    y += 10
    for r in rows:
        d.rectangle([pad, y + 5, pad + 11, y + 16],
                    fill=LEVEL_COLOR.get(r["level"], GOLD))
        d.text((pad + 20, y), r["label"], font=_font(17), fill=GOLD)
        note = r.get("note") or ""
        if note:
            nw = d.textlength(note, font=_font(15))
            d.text((width - pad - nw, y + 2), note, font=_font(15), fill=DIM)
        y += 28
        x = pad
        for lab, fr in r["tiles"]:
            im.paste(fr, (x, y))
            d.text((x + 2, y + th + 4), lab, font=_font(13), fill=DIM)
            x += tw + gap
        y += th + 24
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def load_events(p: str | None) -> list[dict]:
    if not p:
        return []
    raw = json.loads(Path(p).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit('--events 必须是 JSON 数组：[{"t":1.2,"name":"pop"}, ...]')
    out = []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict) or not isinstance(e.get("t"), (int, float)):
            raise SystemExit(f"--events 第 {i} 条没有数字 t：{e!r}")
        out.append({"t": round(float(e["t"]), 4),
                    "name": str(e.get("name") or f"ev{i}")})
    return sorted(out, key=lambda e: e["t"])


def main() -> int:
    ap = argparse.ArgumentParser(description="场景自审：密集采样 + 接触表 + 静音/响度/偏移")
    ap.add_argument("--video", required=True)
    ap.add_argument("--dur", type=float, required=True, help="时间轴上这一格的 dur（秒）")
    ap.add_argument("--events", default=None,
                    help="JSON 数组：[{t,name}]（事件锚点，场景内相对秒）")
    ap.add_argument("--out", default=None, help="产物目录（默认取视频所在目录）")
    ap.add_argument("--json", dest="js", default=None,
                    help="报告路径（默认 <out>/scene-review/review.json）")
    ap.add_argument("--median-band", default=None,
                    help="帧差中位的合格区间 y0:y1，默认 1.2:6.0（代码场景口径）。"
                         "实拍/手持/推镜段给 6:20——底片帧差天然 8–13，用场景口径会恒假红")
    a = ap.parse_args()

    if a.dur <= 0:
        print(f"--dur 必须是正数（时间轴上这一格的时长），现在给的是 {a.dur}", file=sys.stderr)
        return 2
    md_band = (MM.MED_MIN, MM.MED_MAX)          # 代码场景口径，判据的默认值原样透传
    if a.median_band:
        try:
            lo, hi = (float(x) for x in a.median_band.split(":"))
        except ValueError:
            print(f"--median-band 要写成 y0:y1（如 6:20），收到的是 {a.median_band!r}",
                  file=sys.stderr)
            return 2
        if not (0 <= lo < hi):
            print(f"--median-band 要满足 0 ≤ y0 < y1，收到的是 {a.median_band!r}",
                  file=sys.stderr)
            return 2
        md_band = (lo, hi)
    v = Path(a.video).expanduser()
    if not v.exists():
        print(f"找不到视频：{v}", file=sys.stderr)
        return 2
    try:
        info = probe(v)
    except RuntimeError as e:
        print(f"读不出视频流：{e}", file=sys.stderr)
        return 2
    out = Path(a.out).expanduser() if a.out else v.parent
    sdir = out / "scene-review"
    sdir.mkdir(parents=True, exist_ok=True)
    js_path = Path(a.js).expanduser() if a.js else sdir / "review.json"
    sheet_path = sdir / "sheet.png"
    events = load_events(a.events)

    rows: list[dict] = []
    fixes: list[tuple[str, str]] = []
    skipped: list[str] = []

    def add(name: str, value: str, level: str, note: str = "", fix: str = "") -> None:
        rows.append({"name": name, "value": value, "level": level, "note": note})
        if fix and level != "green":
            fixes.append((name, fix))

    # ① 偏移：成片实长 vs 时间轴 dur
    delta = info["dur"] - a.dur
    if abs(delta) > OFFSET_TOL:
        add("时长偏移", f"成片 {info['dur']:.2f}s / 时间轴 {a.dur:.2f}s（{delta:+.3f}s）", "red",
            f"|差| 超过 {OFFSET_TOL}s：后面每一格的音画起点都会跟着错",
            f"这一格渲成 {info['dur']:.2f}s、时间轴要 {a.dur:.2f}s（{delta * 1000:+.0f}ms）→ "
            f"渲器帧数对齐 dur×fps（{a.dur:.2f}×{info['fps']:g}≈{round(a.dur * info['fps'])} 帧），"
            f"或把时间轴这一格的 dur 改成 {info['dur']:.2f}")
    else:
        add("时长偏移", f"成片 {info['dur']:.2f}s / 时间轴 {a.dur:.2f}s（{delta:+.3f}s）", "green",
            f"|差| ≤ {OFFSET_TOL}s")

    # ② 全场帧差剖面：复 measure_motion 的判据与阈值，不自造
    prof = None
    try:
        prof = MM.profile(MM.frames(v, 10, None, None))
    except Exception as e:                                    # noqa: BLE001
        add("帧差剖面", "量不出", "yellow", str(e).strip()[:100])
        skipped.append("动帧占比 / 最长静止 / 帧差中位（帧差剖面量不出）")
    still_cut = prof["still_cut"] if prof else MM.FLOOR
    band_is_default = md_band == (MM.MED_MIN, MM.MED_MAX)
    if prof:
        for name, ok, detail in MM.judge(prof, md_band):
            note, fix = WHY.get(name, ""), ""
            if name == "帧差中位":
                if band_is_default:
                    note = ("代码场景才用 1.2–6.0（太低=只漂几个像素，太高=抖动/频闪）；"
                            f"**实拍/手持/推镜段要用 --median-band 6:20**——底片帧差天然 8–13，"
                            f"拿场景口径判它每次都假红")
                else:
                    note = (f"本次按 {md_band[0]:g}–{md_band[1]:g} 判（实拍/手持段口径）；"
                            f"代码场景仍用默认 {MM.MED_MIN:g}–{MM.MED_MAX:g}")
                if not ok:
                    if prof["median"] < md_band[0]:
                        fix = (f"帧差中位 {prof['median']:.2f} 低于 {md_band[0]:g}：动得没分量 → "
                               f"加位移/缩放幅度，别只漂几个像素")
                    else:
                        fix = (f"帧差中位 {prof['median']:.2f} 高于 {md_band[1]:g}：抖成频闪 → "
                               f"查是不是每帧都在跳（逐帧随机/相机抖），给动作加缓动、去掉逐帧随机")
                        if band_is_default:
                            fix += ("；如果这段本来就是实拍底片（手持/推镜），这条是口径错位——"
                                    f"给 --median-band 6:20 重跑，别去改画面")
            elif not ok:
                fix = (f"{name} = {detail} → 逐秒帧差看哪一段站桩：该动的地方加连续微动"
                       f"（呼吸位移 ≤2px），或把动作拆成两拍，别让画面整段钉住")
            add(name, detail, "green" if ok else "red", note, fix)

    # ③ 关键动作密集采样（每事件一次灰图窗口 + 一次彩色窗口供接触表）
    fps, mw, tw = info["fps"], MM.SMALL[0], SHEET_W
    mh, th = even_h(mw, info["w"], info["h"]), even_h(tw, info["w"], info["h"])
    run_list = SLC.runs(v, 0.0, info["dur"]) if info["audio"] else []
    silent = silence_stats(run_list, info["dur"]) if info["audio"] else None
    mono = None
    if info["audio"] and events:
        try:
            mono = CSF.load_mono(v)
        except Exception as e:                                # noqa: BLE001
            skipped.append(f"音效可听度（音频解不出：{str(e).strip()[:60]}）")

    sheet_rows: list[dict] = []
    ev_json: list[dict] = []
    sampled = 0

    for idx, ev in enumerate(events, 1):
        t = ev["t"]
        label = f"事件 {idx} {ev['name']} @{t:.2f}s"
        if not (0 <= t <= a.dur + 0.05):
            add(label, f"t={t:.2f}s 不在 [0, {a.dur:.2f}]", "yellow",
                "锚点落在这一格之外（时间轴越界）",
                f"把 props.events 里 {ev['name']} 的 t 收到 0–{a.dur:.2f}s 之内")
            continue

        level = "green"
        m = None
        try:
            m = window_diffs(v, t, info["dur"], fps, mw, mh)
            level, value, note, fix = judge_motion(m, still_cut)
            add(f"{label} 动作", value, level, note, fix)
        except Exception as e:                                # noqa: BLE001
            add(f"{label} 动作", "量不出", "yellow", str(e).strip()[:100])
            skipped.append(f"{label} 动作（窗口抽帧失败）")

        if mono is not None:
            level_a = "green"
            own = None
            gap_max = max(b for _, b in CSF.BASE_GAPS)
            if t + gap_max < info["dur"]:
                try:
                    # check_sfx 重写后的稳定接口：同窗净抬升（无 stem 时对照组取 BASE_GAPS 三段基线），
                    # 且按事件名路由主能量频段（低频重击/中频轻敲/宽频铃音）。别再复制旧版 lift 算法。
                    band = CSF.band_for_event(ev["name"])
                    own = CSF.best_transient_lift(mono, None, t, 0.0, band)[2]
                except Exception:                             # noqa: BLE001
                    own = None
            hit = next(((s, e) for s, e in run_list if s - 0.10 <= t <= e + EVENT_SILENCE), None)
            if hit:
                level_a = "red"
                add(f"{label} 音效", f"锚点落在静音段 {hit[0]:.2f}–{hit[1]:.2f}s", "red",
                    "silence.py 量到这一段是静音（既没人声也没音效）",
                    f"{label} 在静音段里 → 音效没混进来：确认渲染读了 props.events"
                    f"（t 是场景内相对秒），或把这个事件挪到有声的 {max(0.0, hit[1] + 0.05):.2f}s 之后")
            elif own is None:
                level_a = "yellow"
                add(f"{label} 音效", "量不出", "yellow",
                    f"离片尾太近（抬升要对照组到 t+{gap_max:.2f}s）")
                skipped.append(f"{label} 音效可听度（片尾余量不足）")
            elif own < CSF.PASS_DB:
                level_a = "red"
                # 分两种病：抬升 1–3dB 是「压在人声上」，抬升为负是「压根没混进来」。
                # 前者只能让位（推增益没用），后者先去查事件有没有被渲进 out/sfx.json。
                if own <= ABSENT_DB:
                    add(f"{label} 音效", f"净抬升 {own:+.1f}dB（判据 ≥{CSF.PASS_DB:.1f}）",
                        "red", f"这一段比周围安静 {-own:.1f}dB：音效基本没混进来（或窗口掉进发声空档）",
                        f"{label} 抬升 {own:+.1f}dB → 先确认它真被渲进了事件表"
                        f"（渲完看 out/sfx.json 有没有这一条、gain_db 是不是被压到听不见），"
                        f"再谈响度")
                else:
                    add(f"{label} 音效", f"净抬升 {own:+.1f}dB（判据 ≥{CSF.PASS_DB:.1f}）",
                        "red", "扣掉对照组后这个事件没露出来（压在人声/垫乐上，或压根没混进来）",
                        f"{label} 只涨 {own:+.1f}dB → 先确认 out/sfx.json 有这一条，"
                        f"再在事件前后压 90ms 人声（duck −6dB）让瞬态自己露头；"
                        f"推高增益没用")
            else:
                add(f"{label} 音效", f"净抬升 {own:+.1f}dB", "green",
                    "扣掉对照组仍有抬升，听得见")
            if level_a == "red":
                level = "red"

        # 接触表：同一窗口的原生帧率彩色小图（格下标注相对锚点的毫秒）
        try:
            t0, t1 = event_span(t, info["dur"], fps)
            times, arr_c = raw_window(v, t0, t1 - t0, fps, tw, th, 3)
            tiles = [(f"{(tt - t) * 1000:+.0f}ms", Image.fromarray(arr_c[i]))
                     for i, tt in enumerate(times)]
            sampled += len(tiles)
            sheet_rows.append({
                "label": label, "tiles": tiles, "level": level,
                "note": (f"峰值 {m['peak_off'] * 1000:+.0f}ms · 帧差 {m['peak']:.2f}"
                         if m else "动作量不出"),
            })
            ev_json.append({"index": idx, "t": t, "name": ev["name"],
                            "motion": m, "level": level})
        except Exception as e:                                # noqa: BLE001
            skipped.append(f"{label} 接触表帧（{str(e).strip()[:60]}）")

    # ④ 没给事件（或事件全越界）→ 均匀采样兜底：接触表仍要出，
    #    否则「没给 --events」就等于什么都不看
    if not sheet_rows:
        tiles = []
        for i in range(6):
            tt = min(info["dur"] * (i + 0.5) / 6, max(0.0, info["dur"] - 0.05))
            try:
                tiles.append((f"{tt:.2f}s", grab_one(v, tt, tw, th)))
                sampled += 1
            except Exception as e:                            # noqa: BLE001
                skipped.append(f"均匀采样帧 @ {tt:.2f}s（{str(e).strip()[:50]}）")
        if tiles:
            sheet_rows.append({
                "label": "均匀采样（"
                         + ("没给 --events" if not events else "事件锚点全部越界")
                         + f"）· {info['dur']:.2f}s", "tiles": tiles,
                "level": "red" if any(r["level"] == "red" for r in rows) else "green",
                "note": "只看整体动静，动作要 --events 才量得到"})

    # ⑤ 静音 / 响度
    if not info["audio"]:
        add("静音 / 响度", "没有音轨", "green",
            "场景片段常态（画面是画的，音效在成片混音里）——这一层没量，不是过")
        skipped.append("静音段 / 响度 / 音效可听度（本片没有音轨）")
    else:
        sl = silent or silence_stats([], info["dur"])
        add("静音段", f"{len(sl['runs'])} 段 / 共 {sl['total']:.2f}s"
                     f"（最长 {sl['longest']:.2f}s，占 {sl['coverage']:.0f}%）", "green",
            "静音本身不算错（§7 结论/留白处不出声）——只有盖住锚点时在事件行判红")
        lo = loudness(v)
        if lo["I"] is None:
            add("响度", "量不出", "yellow", "ebur128 没给出集成响度")
            skipped.append("响度（ebur128 无读数）")
        elif lo["I"] > LOUD_MAX:
            add("响度", f"{lo['I']:+.1f} LUFS（LRA {lo['LRA'] or 0:.1f} LU）", "red",
                f"过响：高于 {LOUD_MAX} LUFS，混进成片会把口播压下去",
                f"这段 {lo['I']:+.1f} LUFS → 先按成片目标 −14 LUFS 归一，别等混音时再压")
        elif lo["I"] < LOUD_MIN:
            add("响度", f"{lo['I']:+.1f} LUFS", "yellow",
                f"低于 {LOUD_MIN} LUFS：整段几乎没声音（只剩底噪？）",
                f"这段只有 {lo['I']:+.1f} LUFS → 若它本该有音效，查 props.events 有没有真混进来")
        else:
            add("响度", f"{lo['I']:+.1f} LUFS（LRA {lo['LRA'] or 0:.1f} LU）", "green",
                f"在 {LOUD_MIN}–{LOUD_MAX} LUFS 之间")
        if lo["TPK"] is not None:
            if lo["TPK"] > TPK_MAX:
                add("真峰", f"{lo['TPK']:+.1f} dBFS", "red",
                    f"超过 {TPK_MAX} dBFS：峰值没余量，转码/平台归一后容易削顶",
                    f"真峰 {lo['TPK']:+.1f} dBFS → 整段降 {abs(lo['TPK'] - TPK_MAX) + 0.2:.1f}dB，"
                    f"或给那个瞬态加限幅")
            else:
                add("真峰", f"{lo['TPK']:+.1f} dBFS", "green", f"≤ {TPK_MAX} dBFS")

    # ⑥ 接触表
    red_n = sum(1 for r in rows if r["level"] == "red")
    yellow_n = sum(1 for r in rows if r["level"] == "yellow")
    green_n = len(rows) - red_n - yellow_n
    head = [f"场景自审 · {v.name}",
            f"{info['dur']:.2f}s（时间轴 {a.dur:.2f}s）· {info['w']}×{info['h']} @ {fps:g}fps · "
            f"{len(events)} 个事件 × ±{int(WARPIN * 1000)}ms 密集采样"
            f"（每格标相对锚点的毫秒）"
            if events else
            f"{info['dur']:.2f}s（时间轴 {a.dur:.2f}s）· {info['w']}×{info['h']} @ {fps:g}fps · "
            f"均匀 6 帧（没给 --events）",
            f"红 {red_n} · 黄 {yellow_n} · 绿 {green_n} · "
            f"帧差中位判据 {md_band[0]:g}–{md_band[1]:g}"
            + ("" if band_is_default else "（实拍/手持段口径）")]
    if sheet_rows:
        build_sheet(sheet_path, head, sheet_rows)

    # ⑦ 报告
    print(f"场景自审：{v.name} · 成片 {info['dur']:.2f}s / 时间轴 {a.dur:.2f}s · "
          f"{info['w']}×{info['h']} @ {fps:g}fps"
          + (f" · {len(events)} 个事件" if events else " · 无事件（均匀采样）"))
    print(f"采样：{sampled} 帧（" + (f"每事件 ±{int(WARPIN * 1000)}ms 逐帧密集，原生 {fps:g}fps"
                                  if events else "均匀 6 帧（没给 --events）") + "）")
    for r in rows:
        print(f"  {ICON[r['level']]} {r['name']} {r['value']}"
              + (f" —— {r['note']}" if r["note"] else ""))
    if fixes:
        print("返工建议（改哪里）：")
        for k, (name, fix) in enumerate(fixes, 1):
            print(f"  {k}. {name}：{fix}")
    if skipped:
        print("跳过（量不到位，不是过）：" + "；".join(skipped))
    print(f"结论：🟢 {green_n} · 🟡 {yellow_n} · 🔴 {red_n}"
          + ("（有红，这一格不能收）" if red_n else ""))
    print(f"→ {sheet_path}" if sheet_rows else "（没出接触表：一帧都没抽到）")
    js_path.parent.mkdir(parents=True, exist_ok=True)
    js_path.write_text(json.dumps({
        "video": str(v), "video_dur": info["dur"], "dur_timeline": a.dur,
        "offset_s": round(delta, 4), "fps": fps, "size": [info["w"], info["h"]],
        "audio": info["audio"], "warpin_s": WARPIN, "sampled_frames": sampled,
        "median_band": [md_band[0], md_band[1]],
        "events": ev_json, "silence": silent, "checks": rows,
        "fixes": [{"item": n, "fix": f} for n, f in fixes], "skipped": skipped,
        "sheet": str(sheet_path),
        "summary": {"green": green_n, "yellow": yellow_n, "red": red_n},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {js_path}")
    return 1 if red_n else 0


if __name__ == "__main__":
    sys.exit(main())
