#!/usr/bin/env python3
"""CutSkills · 用参考片校准「声画同步」的阈值。

`sync_check` 的三个阈值是行业经验给的（80ms / 15% / 120ms），没人验证过。
这里反过来做：拿一支**真人剪好、能发的片子**，用同一套算法量它自己的读数——
它就是这套标准能达到的现实上限。我们的片子和它的差距，才是「差多少」。

做法（不引入任何主观判断）：
  1. 切点：`skeleton.py` 的模式变化（参考片自带剪辑，切点是既成事实）
  2. 词级时间戳：ASR 自己的音轨（`index_assets.asr`，与素材库同一条路）
  3. 一口气：字间停顿 ≥240ms 切一刀（与 `audio_map.py` 定义一致）
  4. 同一个判据：切点离最近词/气口边界落在 −152ms ~ +40ms 内算同步

大字偏移**校准不了**：参考片没有大字出现时刻的标注，靠画面找字是猜。
这一条如实保留经验值，别假装量过。

用法：
  calibrate.py --video <参考片> [--skeleton <json>] [--json]
  calibrate.py --video <参考片> --tolerance-from 0.85   # 反推「要多宽才过」
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GAP_S = 0.240          # 一口气的判定：字间停顿 ≥240ms
PASS_SYNC = 0.85


def cut_times(video: Path, sk: dict) -> list[float]:
    """从骨架里拿切点。骨架存的是「镜长」，切点是累加。"""
    out, t = [], 0.0
    for ln in sk.get("lengths") or []:
        t = round(t + float(ln), 3)
        out.append(t)
    return out[:-1] if out else []      # 最后一个等于片尾，不是切点


def load_words(cache: Path, prefix: str) -> list[dict]:
    """从 ASR 的分片缓存里取字级时间戳（`index_assets.asr` 只回整句，字级在缓存里）。

    缓存文件名是 `{key}-v3c{偏移秒:05d}.json`，偏移必须加回去，否则长片后半段全错位。
    """
    import json as _json
    import re
    out = []
    for f in sorted(cache.glob("asr/*-v3c*.json")):
        if prefix and prefix not in f.name:
            continue
        m = re.search(r"-v3c(\d+)\.json$", f.name)
        if not m:
            continue
        off = float(int(m.group(1)))
        raw = _json.loads(f.read_text())
        toks, stamps = raw.get("tokens") or [], raw.get("timestamps") or []
        if not toks or len(toks) != len(stamps):
            continue
        for t, st in zip(toks, stamps):
            s0 = float(st[0] if isinstance(st, (list, tuple)) else st)
            s1 = float(st[1] if isinstance(st, (list, tuple)) else st)
            out.append({"t": t, "start": round(s0 + off, 3), "end": round(s1 + off, 3)})
    out.sort(key=lambda w: w["start"])
    return out


def blocks_from_words(words: list[dict]) -> list[dict]:
    """字 → 一口气。定义必须和 audio_map.py 一致，否则两边读数不能比。"""
    blocks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 9.9
        if nxt >= GAP_S or i == len(words) - 1:
            blocks.append({"start": round(cur[0]["start"], 3),
                           "end": round(cur[-1]["end"], 3),
                           "text": "".join(x["t"] for x in cur)})
            cur = []
    return blocks


def sync_rate(events: list[float], bounds: list[float], tol_ms: int) -> tuple[float, list[int]]:
    """同一个不对称判据：早切是 J-cut（对的），晚切才是毛病。"""
    devs = []
    for t in events:
        b = min(bounds, key=lambda x: abs(x - t))
        devs.append(round((b - t) * 1000))
    hits = [d for d in devs if -tol_ms * 1.9 <= d <= tol_ms * 0.5]
    return (len(hits) / len(devs) if devs else 0.0), devs


def cut_inside(events: list[float], blocks: list[dict]) -> tuple[float, list[dict]]:
    broken = []
    for b in blocks:
        st, en = b["start"] + 0.15, b["end"] - 0.15
        cuts = [t for t in events if st < t < en]
        if cuts:
            broken.append({"text": b["text"][:14], "cuts": cuts})
    return (len(broken) / len(blocks) if blocks else 0.0), broken


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--skeleton", default=None, help="skeleton.json；不给就现场跑 skeleton.py")
    ap.add_argument("--cache", default=str(Path.home() / ".cutskills/cache/calib"))
    ap.add_argument("--tolerance", type=int, default=80)
    ap.add_argument("--tolerance-from", type=float, default=None,
                    help="反推：要多宽的窗口才能让这支片自己达到这个同步率")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import index_assets as ia

    video = Path(a.video).expanduser().resolve()
    if not video.exists():
        raise SystemExit(f"找不到 {video}")

    sk = None
    if a.skeleton:
        sk = json.loads(Path(a.skeleton).read_text())
    else:
        import skeleton                                 # 同一条码，不另写一套
        skeleton.need_ffmpeg()
        info = skeleton.probe(video)
        info["fps"] = info["fps"] or 24.0
        _stats, lengths = skeleton.cut_stats(skeleton.detect_cuts(video, info), info["duration_s"])
        sk = {"lengths": lengths}
    cuts = cut_times(video, sk)

    cache = Path(a.cache).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    tag = "calib-" + ia.h12(str(video))
    got = ia.asr(video, cache, tag, "zh", "sensevoice")
    if not got:
        raise SystemExit("ASR 没出结果，先确认 coli asr 能跑")
    words = load_words(cache, ia.h12(tag))
    if len(words) < 20:
        raise SystemExit(f"字级时间戳只有 {len(words)} 个，转写没落盘")
    blocks = blocks_from_words(words)
    bounds = sorted({b["start"] for b in blocks} | {b["end"] for b in blocks})

    rate, devs = sync_rate(cuts, bounds, a.tolerance)
    rate_in, broken = cut_inside(cuts, blocks)
    absdev = sorted(abs(d) for d in devs)
    p50 = absdev[len(absdev) // 2] if absdev else None
    p75 = absdev[int(len(absdev) * 0.75)] if absdev else None

    need = None
    if a.tolerance_from:
        for tol in range(20, 401, 10):
            r, _ = sync_rate(cuts, bounds, tol)
            if r >= a.tolerance_from:
                need = tol
                break

    rep = {"video": video.name, "cuts": len(cuts), "blocks": len(blocks),
           "sync_rate": round(rate, 3), "cut_inside_rate": round(rate_in, 3),
           "dev_ms_p50": p50, "dev_ms_p75": p75,
           "tolerance_needed_for": {str(a.tolerance_from): need} if need else None,
           "notes": "大字偏移无法从参考片校准：没有标注"}

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"参考片：{video.name}")
        print(f"  切点 {len(cuts)} · 一口气 {len(blocks)}")
        print(f"  同步率 {rate:.0%}（判据 −152ms~+40ms）· 断章率 {rate_in:.0%}")
        print(f"  切点偏差 |p50| {p50}ms · |p75| {p75}ms")
        if need:
            print(f"  要它自己过 {a.tolerance_from:.0%}，窗口得开到 ±{need}ms")
        print("  大字偏移：校准不了（参考片没有大字标注），保留经验值 120ms")
        print(f"  → {HERE.parent / 'references'}/audio-first.md 里核对；建议阈值取参考片 p75 附近")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
