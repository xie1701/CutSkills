#!/usr/bin/env python3
"""画面稳定度曲线：某一秒的这个人，是站定了在说话，还是正在转头挥手。

跳切切点光对齐字界不够——字界对了，但那一刻人正好在转头，切进去就是一张中间帧，
观众看到的不是「换了一个景别」而是「这人在抽搐」。20 支参考片里没有一镜切在动作中。

做法：把素材按 STAB_FPS 抽成小灰度帧，算相邻帧差；查表得到「这一秒有多动」。
曲线按素材路径 + 文件大小 + 修改时间缓存，一段素材只算一次。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

STAB_FPS = 10
_FF = None


def _ffmpeg() -> str:
    global _FF
    if _FF:
        return _FF
    import shutil
    cand = Path("~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg").expanduser()
    _FF = str(cand) if cand.exists() else (shutil.which("ffmpeg") or "ffmpeg")
    return _FF


def _key(src: Path) -> str:
    st = src.stat()
    return hashlib.sha1(f"{src}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def curve(src: Path, cache_dir: Path, w: int = 160, h: int = 284) -> list[float]:
    """返回每个 STAB_FPS 采样点上的帧差（0 = 完全静止）。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cache_dir / f"stab-{_key(src)}.json"
    if dst.exists():
        try:
            return json.loads(dst.read_text())["d"]
        except Exception:
            pass
    r = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", str(src),
         "-vf", f"fps={STAB_FPS},scale={w}:{h},format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    raw = r.stdout or b""
    n = len(raw) // (w * h)
    out: list[float] = []
    prev = None
    for i in range(n):
        f = raw[i * w * h:(i + 1) * w * h]
        if prev is not None:
            d = sum(abs(a - b) for a, b in zip(f[::7], prev[::7])) / max(1, len(f[::7]))
            out.append(round(d, 2))
        else:
            out.append(0.0)
        prev = f
    dst.write_text(json.dumps({"src": str(src), "fps": STAB_FPS, "d": out}))
    return out


def motion_at(cur: list[float], t: float, win: float = 0.25) -> float:
    """t 秒附近 win 窗口内的平均帧差。没数据就当「平稳」，不要把整片判死。"""
    if not cur:
        return 0.0
    a = max(0, int((t - win) * STAB_FPS))
    b = min(len(cur), int((t + win) * STAB_FPS) + 1)
    if b <= a:
        return 0.0
    seg = cur[a:b]
    return sum(seg) / len(seg)
