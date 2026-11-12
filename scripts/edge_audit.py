#!/usr/bin/env python3
"""边界审计：每一格的开头 / 结尾 / 中间，有没有「不干净」的东西。

Jerry 2026-11-12 的三条原话对应三件事：
  「包房属于主播自己提示自己的」      → 孤立提示词：一个词说完停一下，然后又从头说一遍
  「仲有开始那一段是摄影师的声音」    → 半句起头 / 尾巴挂着下一句的开头
  「口播声音结束，画面还在演，穿帮」  → 声音停了但画面帧差还在高位

判据全部用词级真值 + 源素材像素，不看 ASR 的整句（它会把两句话粘成一句）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_guard as EG  # noqa: E402

PITCH_GAP = 0.90     # 词后静默超过这个数，再看后面有没有把同一句重说一遍
PITCH_LOOK = 4.0     # 「重说」要在这么多年内出现
EDGE_TAIL = 0.55     # 结尾静默（和 speech_hygiene 同口径）
EDGE_HEAD = 0.40
TAIL_MOTION = 8.0    # 末段帧差（绝对）
TAIL_RATIO = 2.0     # 末段帧差 / 整格帧差中位（相对）——说话时人本来就在动，
                     # 单看绝对值全片都会报；要看的是「末段突然比平常动得厉害」
_FF = None


def ffmpeg() -> str:
    global _FF
    if _FF:
        return _FF
    import shutil
    c = Path("~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg").expanduser()
    _FF = str(c) if c.exists() else (shutil.which("ffmpeg") or "ffmpeg")
    return _FF


def _gray(src: Path, t: float, w=160, h=284):
    r = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
                        "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    return r.stdout or b""


def motion(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    step = 7
    n = len(a) // step
    return sum(abs(a[i] - b[i]) for i in range(0, n * step, step)) / max(1, n)


def audit(proj: Path, lib: Path) -> list[tuple[str, str]]:
    tl = json.loads((proj / "timeline.json").read_text())
    out: list[tuple[str, str]] = []
    for el in tl.get("elements") or []:
        if el.get("track") == "overlay":
            continue
        n = el.get("n")
        a = el.get("audio") or {}
        aid = a.get("asset")
        if not aid:
            continue
        i, o = float(a.get("in") or 0), float(a.get("out") or 0)
        ws = [w for w in EG.words_of(lib, aid) if i - 0.02 <= w[1] <= o + 0.02]
        if not ws:
            continue
        # ① 孤立提示词：说完一个词停 ≥0.70s，之后 4 秒内又把同一个词重说一遍。
        # 「每 个 包 [空 1.2s] 每 个 包 房 …」——前面那个「包」是主播在看提词卡自己念半截，
        # 正式那句从第二个「包」起。判据就看「静默前的词在静默后又出现」。
        for k, (w, t) in enumerate(ws[:-1]):
            gap = ws[k + 1][1] - t
            if gap < PITCH_GAP:
                continue
            after = [x for x, tt in ws[k + 1:] if tt <= t + PITCH_LOOK]
            if w in after:
                out.append(("红", f"#{n} 孤立提示词：「{w}」@{t:.2f} 之后空 {gap:.2f}s，"
                                  f"然后又把「{w}」重说了一遍——前面那半截是自己念题，不该进片"))
                break
        # ①b 尾巴挂着下一句的重复：窗口最后 1.2 秒里出现 2 字以上的短语重复
        # （「叉烧包 仲有 你仲有 好多人」——「仲有你仲有」是下一句的开头，被尾巴带进来了）
        tail_ws = [w for w, t in ws if t >= o - 1.4]
        tail_txt = "".join(tail_ws)
        for kl in (3, 2):
            hit = None
            for x in range(len(tail_txt) - kl + 1):
                g = tail_txt[x:x + kl]
                if tail_txt.count(g) >= 2:
                    hit = g
                    break
            if hit:
                out.append(("红", f"#{n} 尾巴挂着一句重复「{hit}」——窗口没收干净，"
                                  f"下一句的开头被带进来了"))
                break
        # ② 头尾静默
        if ws[0][1] - i > EDGE_HEAD:
            out.append(("黄", f"#{n} 开头 {ws[0][1] - i:.2f}s 没声音"))
        if o - ws[-1][1] > EDGE_TAIL:
            out.append(("黄", f"#{n} 结尾 {o - ws[-1][1]:.2f}s 没声音"))
        # ③ 画面拖尾：话停了，画面里人还在做新动作。
        # 单看「末段帧差」会把正常说话全判红（人一直在动），所以拿它跟**这一格自己**的平均动感比。
        vid = lib / (el.get("asset") or "")
        if vid.exists():
            vi, vo = float(el.get("in") or 0), float(el.get("out") or 0)
            span = max(0.4, vo - vi)
            pts = [vi + span * f for f in (0.12, 0.32, 0.52, 0.72, 0.88)]
            frames = [_gray(vid, t) for t in pts]
            base = [motion(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
            base = sorted(base)[len(base) // 2] if base else 0.0
            tail = motion(_gray(vid, max(vi, vo - 0.45)), _gray(vid, max(vi, vo - 0.05)))
            if tail > TAIL_MOTION and tail > max(4.0, TAIL_RATIO * base):
                out.append(("黄", f"#{n} 末尾画面突然动起来（帧差 {tail:.0f} vs 本格中位 {base:.0f}）"
                                  f"——话都停了，人还在演"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="边界审计：提示词 / 头尾静默 / 画面拖尾")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    if a.library:
        lib = Path(a.library).expanduser().resolve()
    else:
        # 素材库根 = 那个带 _cutskills/index.json 的目录。别用 proj.parent.parent 猜，
        # 猜错的时候 words_of 全返回空，审计会一路绿着骗人（这个坑刚踩过）。
        lib = proj
        for _ in range(5):
            if (lib / "_cutskills" / "index.json").exists():
                break
            lib = lib.parent
    rows = audit(proj, lib)
    red = [r for r in rows if r[0] == "红"]
    for lv, msg in rows:
        print(f"  {'🔴' if lv == '红' else '🟡'} {msg}")
    print(f"\n边界审计：🔴 {len(red)} · 🟡 {len(rows) - len(red)}")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
