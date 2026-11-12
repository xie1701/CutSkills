#!/usr/bin/env python3
"""真实静音探针：给定素材与窗口，量出里面**真有**几段没声音。

为什么要有这个文件：`speech_hygiene` / `edge_audit` 的「话说一半断了」原来是拿
ASR 词级时间戳的**间隔**推的。间隔大不等于静音——ASR 会漏词（「从嗰个渔船」
只记成「从…渔船」，中间 0.6s 有声没词），而真正该切的是**静音**。两者混在一起，
编辑照提示去「切掉中间静默」，切掉的可能是他说的话。

## 为什么不用 ffmpeg 的 silencedetect

用过的，会瞎。`silencedetect=noise=-38dB` 拿的是**固定绝对阈值**、并且取**所有声道
的最大值**：手机竖拍常见「右声道正常、左声道一路带底噪」，或者空调/风噪把底噪
抬到 −37dB。这时整条素材从头到尾都判不出静音——

  2026-10-22 152640.mov：真人说完「桑拿鸡」之后安静了 0.87 秒，
  Channel 1 RMS −48.8dB（静），Channel 2 RMS −37.9dB（一路的低频噪）。
  silencedetect 报「零段静音」，于是 a 片 #7 尾巴那 1 秒死气被判成「正常」。

所以改成**相对阈值**：先算这个窗口自己的说话电平（每 20ms 一帧 RMS 的 90 分位），
低于它 REL_DB 分贝的帧才算没声音。谁家底噪高都不影响判断——静音是「比说话低很多」，
不是一个固定的 dB 数。

结果按 (路径, mtime, size, in, out, 参数) 缓存，同一格反复查不重复跑 ffmpeg。
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

RATE = 16000          # 只用来量包络，16k 足够
FRAME = 0.02          # 20ms 一帧
MIN_D = 0.20          # 短于这个的停顿是换气，不算静默
SPEECH_PCT = 90       # 用窗口内 RMS 的 90 分位当「说话电平」
REL_DB = -24.0        # 低于说话电平 24dB 才算没声音
ABS_FLOOR = -70.0     # 绝对下限：整个窗口都低于这个，说明根本没有说话

_CACHE_DIR = Path("~/.cutskills/cache/silence").expanduser()
_MEM: dict[str, list[tuple[float, float]]] = {}


def _ffmpeg() -> str:
    import shutil
    c = Path("~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg").expanduser()
    return str(c) if c.exists() else (shutil.which("ffmpeg") or "ffmpeg")


def _envelope(src: Path, i: float, o: float) -> list[float] | None:
    """窗口内每 FRAME 秒一帧的 RMS（dBFS）。取不出音频返回 None。"""
    try:
        r = subprocess.run(
            [_ffmpeg(), "-v", "error", "-ss", f"{i:.3f}", "-t", f"{max(0.0, o - i):.3f}",
             "-i", str(src), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
            capture_output=True)
    except OSError:
        return None
    if r.returncode != 0 or len(r.stdout) < 2 * RATE * FRAME:
        return None
    raw = r.stdout[:len(r.stdout) // 2 * 2]
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    n = int(RATE * FRAME)
    m = len(a) // n
    if m <= 0:
        return None
    out: list[float] = []
    for k in range(m):
        seg = a[k * n:(k + 1) * n]
        s = 0
        for v in seg:
            s += v * v
        rms = math.sqrt(s / n) / 32768.0
        out.append(20.0 * math.log10(rms + 1e-9))
    return out


def runs(src: Path, i: float, o: float,
         rel_db: float = REL_DB, min_d: float = MIN_D) -> list[tuple[float, float]]:
    """返回窗口内的静音段（时间已还原成**素材绝对时间**）。量不出返回 []。"""
    src = Path(src)
    if not src.exists() or o <= i:
        return []
    key = hashlib.sha1(
        f"{src}|{src.stat().st_mtime_ns}|{src.stat().st_size}|{i:.3f}|{o:.3f}|{rel_db}|{min_d}".encode()
    ).hexdigest()[:16]
    if key in _MEM:
        return _MEM[key]
    cp = _CACHE_DIR / f"{key}.json"
    if cp.exists():
        try:
            v = [(float(a), float(b)) for a, b in json.loads(cp.read_text())]
            _MEM[key] = v
            return v
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    db = _envelope(src, i, o)
    if db is None:
        return []
    lvl = sorted(db)[min(len(db) - 1, int(len(db) * SPEECH_PCT / 100))]
    thr = max(lvl + rel_db, ABS_FLOOR)
    out: list[tuple[float, float]] = []
    st: int | None = None
    for k, x in enumerate(db + [1e9]):     # 末尾塞一个「不静」哨兵，收尾不用特判
        quiet = x < thr
        if quiet and st is None:
            st = k
        elif not quiet and st is not None:
            if (k - st) * FRAME >= min_d:
                out.append((round(i + st * FRAME, 3), round(i + k * FRAME, 3)))
            st = None
    _MEM[key] = out
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(out))
    except OSError:
        pass
    return out


def silent_between(src: Path, a: float, b: float, **kw) -> float:
    """[a,b] 之间**真有**多长的静音。"""
    if b <= a:
        return 0.0
    tot = 0.0
    for s, e in runs(src, a, b, **kw):
        tot += max(0.0, min(e, b) - max(s, a))
    return round(tot, 3)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="量一个窗口里真有多长的静音")
    ap.add_argument("src")
    ap.add_argument("in_", type=float)
    ap.add_argument("out", type=float)
    a = ap.parse_args()
    rs = runs(Path(a.src).expanduser().resolve(), a.in_, a.out)
    print(json.dumps({"runs": rs, "total": round(sum(e - s for s, e in rs), 3)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
