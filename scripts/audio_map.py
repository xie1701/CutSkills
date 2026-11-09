#!/usr/bin/env python3
"""CutSkills · 音频层：把一条片的声音切成「块」，块是所有时间安排的基准。

为什么要块，不直接用整句：整句是最粗的尺子。用整句排画面，动画只能跟着句子一蹦一跳，
大字永远落在句首，切点永远跟停顿对齐——看着像配字幕，不像剪辑。
真正的单位是**半句**：一口气说完的那一小段。它自带能量、自带重音点，
画面要跟的就是这些东西。

块从词级时间戳来（`index.json` 的 transcript chars），不重新跑 ASR：
  - 断块：字间停顿 ≥ GAP_MS，或字数 ≥ MAX_CHARS，或遇到标点
  - 能量：把该段音频解成 16k 单声道，20ms 一帧算 RMS，块内取均值与峰值位置
  - 重音点：块内能量最高的那一帧的时间（大字、动效入场都该落在这儿）
  - 标记：嗯/啊/那个/然后 这类填充词；与上一块高度重复的（口误/重说）

产物 `audio-map.json`：每块带**素材内时间**和**成片时间**（后者由 timeline 推出）。
之后所有画面事件都写「第几块 + 偏移毫秒」，不写绝对秒。

用法：
  audio_map.py --project <项目> [--library <库>] [--out audio-map.json]
  audio_map.py --project <项目> --show
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_edl import resolve_lib  # noqa: E402
from check_edl import tokens as char_tokens  # noqa: E402  词级时间戳只有这一处来源

GAP_MS = 240           # 字间停顿 ≥ 这个就算一口气断了
MAX_CHARS = 9          # 一口气通常 5–9 字；超过就再切一刀
FRAME_MS = 20
FILLERS = {"嗯", "啊", "呃", "哦", "诶", "哎", "这个", "那个", "然后", "就是", "所以就是",
           "对吧", "是吧", "怎么说", "你知道"}
PUNCT = "，。！？、；：…—"


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def ffmpeg_bin() -> str:
    import os
    return os.environ.get("CUTSKILLS_FFMPEG") or "ffmpeg"


def read_text_wav(path: Path) -> tuple[list[float], int]:
    """解成 16k 单声道，返回 (采样, 采样率)。只解一遍，之后全在内存里算。"""
    import numpy as np
    raw = path.with_suffix(".raw")
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(path),
                        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", str(raw)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not raw.exists():
        return [], 16000
    a = np.fromfile(str(raw), dtype="<i2").astype("float32") / 32768.0
    raw.unlink(missing_ok=True)
    return a.tolist(), 16000


def rms_frames(samples: list[float], sr: int) -> list[float]:
    import numpy as np
    a = np.asarray(samples, dtype="float32")
    n = max(1, int(sr * FRAME_MS / 1000))
    k = len(a) // n
    if k == 0:
        return []
    f = a[: k * n].reshape(k, n)
    return np.sqrt((f ** 2).mean(axis=1)).tolist()


def db(x: float) -> float:
    import math
    return round(20 * math.log10(max(x, 1e-6)), 1)


def segment(blocks: list[dict]) -> list[dict]:
    """把连续的字并成块。断块三条件任一命中就切。"""
    out: list[dict] = []
    cur: dict | None = None
    for ch in blocks:
        c = ch["c"]
        if cur and (ch["s"] - cur["end"] >= GAP_MS / 1000.0
                    or len(cur["text"]) >= MAX_CHARS
                    or cur["text"][-1] in PUNCT):
            out.append(cur)
            cur = None
        if cur is None:
            cur = {"start": ch["s"], "end": ch["e"], "text": c}
        else:
            cur["end"] = ch["e"]
            cur["text"] += c
    if cur:
        out.append(cur)
    return out


def chars_of(lib: Path, aid: str) -> list[dict]:
    """字级时间戳。**只有一处来源**：`_cutskills/cache/asr/` 里的 chunk 文件，
    index.json 里存的是整句 phrases，没有单字时间——用整句当块，动画就永远落在句首。"""
    got = char_tokens(lib, aid)
    if not got:
        return []
    chars, starts, ends = got
    return [{"c": c, "s": float(s), "e": float(e)} for c, s, e in zip(chars, starts, ends)]


def analyze_span(lib: Path, aid: str, t0: float, t1: float, frames_of) -> dict:
    """对一段素材时间做能量分析，返回 {mean_db, peak_db, peak_ms(相对 span 起点)}。"""
    fr = frames_of(aid)
    if not fr:
        return {}
    per = FRAME_MS / 1000.0
    a, b = int(t0 / per), int(t1 / per)
    seg = fr[max(0, a): max(1, b)]
    if not seg:
        return {}
    peak = max(seg)
    idx = seg.index(peak)
    mean = sum(seg) / len(seg)
    return {"mean_db": db(mean), "peak_db": db(peak),
            "peak_at": round(t0 + idx * per, 3)}


def build(proj: Path, lib: Path) -> dict:
    tl = load(proj / "timeline.json")
    if not tl:
        raise SystemExit("先有 timeline.json（scripts/timeline.py）")
    index = {x["id"]: x for x in json.loads(
        (lib / "_cutskills" / "index.json").read_text())["assets"]}

    # 只解一次每条素材的音频
    cache: dict[str, list[float]] = {}

    def frames_of(aid: str) -> list[float]:
        if aid not in cache:
            src = lib / aid
            if not src.exists():
                cache[aid] = []
            else:
                s, sr = read_text_wav(src)
                cache[aid] = rms_frames(s, sr)
        return cache[aid]

    blocks: list[dict] = []
    t = 0.0
    seen: dict[str, int] = {}
    for el in tl["elements"]:
        d = (round(float(el["out"]) - float(el["in"]), 3) if el["kind"] == "clip"
             else float(el.get("dur") or 0))
        if el.get("track") == "overlay":
            continue
        if el["kind"] == "clip":
            a = el.get("audio") or {}
            aid = a.get("asset") or el["asset"]
            a0 = float(a.get("in") if a.get("in") is not None else el["in"])
            rec = index.get(aid) or {}
            # 只在 [a0, a0+d) 里取字。以前两头各留 50ms 容差，结果相邻两段共用的那个字
            # 同时进了两段（「我」既是片头最后一块、又是下一段的第一块），块列表里凭空多一条。
            chs = [c for c in chars_of(lib, aid) if a0 - 0.02 <= c["s"] < a0 + d - 0.02]
            for n_, blk in enumerate(segment(chs), 1):
                txt = blk["text"].strip(PUNCT + " ")
                if not txt:
                    continue          # 纯标点块没有信息，也不该占位置
                en = analyze_span(lib, aid, blk["start"], blk["end"], frames_of)
                key = txt[:6]
                dup = seen.get(key, 0)
                seen[key] = dup + 1
                blocks.append({
                    "id": len(blocks) + 1,
                    "el": el["n"],
                    "src": aid,
                    "src_start": round(max(blk["start"], a0), 3),
                    "src_end": round(min(blk["end"], a0 + d), 3),
                    "t": round(t + (blk["start"] - a0), 3),          # 成片时间
                    "dur": round(blk["end"] - blk["start"], 3),
                    "text": txt,
                    **{k: v for k, v in en.items()},
                    # 填充词只认表里的完整词，不按「字数少」判——
                    # 按长度判会把「我」「就」「因」这种正常字全打成噪声（实测 13/49）。
                    "is_filler": txt in FILLERS or (len(txt) <= 3 and txt in FILLERS),
                    "is_repeat": dup > 0,
                })
        t = round(t + d, 3)

    real = [b for b in blocks if not b["is_filler"]]
    return {
        "version": 1,
        "duration_s": round(t, 3),
        "frame_ms": FRAME_MS,
        "counts": {"blocks": len(blocks), "speech": len(real),
                   "filler": sum(1 for b in blocks if b["is_filler"]),
                   "repeat": sum(1 for b in blocks if b["is_repeat"])},
        "blocks": blocks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    out = Path(a.out) if a.out else proj / "audio-map.json"

    if a.show:
        am = load(out)
        if not am:
            raise SystemExit("还没有 audio-map.json")
        for b in am["blocks"]:
            flag = ("填" if b["is_filler"] else "") + ("重" if b["is_repeat"] else "")
            print(f" {b['id']:>3} {b['t']:7.2f}s +{b['dur']:.2f} "
                  f"{b.get('mean_db', 0):6.1f}dB 重音@{b['t'] + (b.get('peak_at', 0) - b['src_start']):.2f}s "
                  f"{b['text'][:14]} {flag}")
        print(f"块 {am['counts']['blocks']}（填充 {am['counts']['filler']} · 重复 {am['counts']['repeat']}）")
        return 0

    lib = Path(a.library).expanduser().resolve() if a.library else resolve_lib(proj, None)
    am = build(proj, lib)
    out.write_text(json.dumps(am, ensure_ascii=False, indent=1), encoding="utf-8")
    c = am["counts"]
    print(f"块 {c['blocks']}（有声 {c['speech']} · 填充词 {c['filler']} · 重复 {c['repeat']}）"
          f" · 片长 {am['duration_s']}s")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
