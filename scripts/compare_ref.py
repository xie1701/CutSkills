#!/usr/bin/env python3
"""对标视频逐秒结构对比：把成片和「对标.mp4」都拆成每秒一行，比结构，不比内容。

为什么要这一份：BRIEF 的自动质检最后一条是「如果用户提供了对标视频，再额外输出一份
与对标视频的逐秒结构对比」。前面的判据都在问「这片自己合不合规」，只有这一份回答
「跟人家比，差在哪个动作上」——第 7 秒人家切了两刀、我们挂着一个 1.9 秒的镜头不动，
这种话只有秒级对照说得出来。

抽的五个特征（每个都按秒开桶，同时也给整片合计）：

  切点数        镜头切换落在第几秒            skeleton.detect_cuts（与 band 判据同一个探针）
  卡片入场数    大字/动效卡在第几秒进来        out/cards.json（对标片没有这个文件，标 —）
  平均镜头长    该秒覆盖到的镜头平均多长       cuts 反推，按秒归属
  前 3 秒变化数 0<t≤3.0 内的切点 + 卡片入场     BRIEF 剪辑表第 4 条那个数
  平均亮度      该秒灰度均值（0–1）            ffmpeg fps=1 → rawvideo gray

对标的卡片入场数**量不出来**：那是人家成片里的包装层，没有工程文件。硬编一个数
（比如从像素里猜）等于编证据，所以这一列留空并写清楚原因——逐秒差异里也跳过它。

用法：
  python3 scripts/compare_ref.py --film out/film-hf.mp4 --ref 对标.mp4 [--project <项目>]
输出：`<项目>/ref-compare.md`（没有项目就写到成片旁边）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))
# numpy 是 C 扩展（装不进 vendor）：换镜探针 skeleton.detect_cuts 靠它，
# 亮度采样也靠它。落在 $CUTSKILLS_HOME/py（deps.json）。
try:
    from libs import cutskills_home as _cutskills_home
    _PY_DEPS = _cutskills_home() / "py"
    if _PY_DEPS.exists() and str(_PY_DEPS) not in sys.path:
        sys.path.append(str(_PY_DEPS))
except ImportError:                             # 只用到纯 Python 部分时不需要 numpy
    pass

BRIGHT_W = 160          # 量亮度用的宽度：只要均值，160 够，快两个数量级
FIRST_N_S = 3.0         # BRIEF 剪辑表第 4 条：前 3 秒
LIGHT_EPS = 0.10        # 亮度差多少算「看得出来」的一档（10% 灰度）
SHOT_EPS = 1.0          # 镜头长差多少算「节奏不一样」（1 秒）


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True)


def probe_basic(path: Path) -> dict:
    def _ffprobe() -> dict:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", str(path)],
                           capture_output=True, text=True)
        return json.loads(r.stdout or "{}")
    d = _ffprobe()
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration_s": float(d.get("format", {}).get("duration") or 0.0),
            "w": int(v.get("width") or 0), "h": int(v.get("height") or 0)}


def brightness_per_second(path: Path, w: int, h: int) -> list[float]:
    """每秒一帧的灰度均值（0–1）。量不出来返回 []。"""
    import numpy as np
    if not (w and h):
        return []
    bw = BRIGHT_W
    bh = max(2, int(round(bw * h / max(1, w) / 2)) * 2)
    r = run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps=1,scale={bw}:{bh}",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    raw = r.stdout or b""
    npx = bw * bh
    if not raw or len(raw) < npx:
        return []
    a = np.frombuffer(raw[:len(raw) // npx * npx], dtype=np.uint8).reshape(-1, npx)
    return [round(float(x) / 255.0, 4) for x in a.mean(axis=1)]


def cut_times(path: Path) -> tuple[list[float], dict]:
    """切点时刻（秒）。探针复用 skeleton.detect_cuts —— 与 verify_film 的 band 判据同源。"""
    import skeleton as SK
    info = SK.probe(path)
    return SK.detect_cuts(path, info), info


def lens_of(cuts: list[float], dur: float) -> list[float]:
    return [round(b - a, 3) for a, b in zip([0.0] + list(cuts), list(cuts) + [dur])]


def shot_len_per_second(cuts: list[float], dur: float) -> dict[int, float]:
    """每一秒落在哪个镜头上、那个镜头多长。跨秒的镜头两边都记它自己的长度。"""
    lens = lens_of(cuts, dur)
    out: dict[int, float] = {}
    t = 0.0
    for L in lens:
        i0, i1 = int(t), int(min(dur - 1e-6, t + L))
        for s in range(i0, i1 + 1):
            out[s] = round(L, 2)
        t += L
    return out


def cards_of(project: Path | None) -> list[dict]:
    if not project:
        return []
    f = project / "out" / "cards.json"
    if not f.exists():
        return []
    try:
        return list((json.loads(f.read_text()) or {}).get("cards") or [])
    except json.JSONDecodeError:
        return []


def cards_per_second(cards: list[dict]) -> dict[int, int]:
    out: dict[int, int] = {}
    for c in cards:
        try:
            s = int(float(c.get("start") or 0))
        except (TypeError, ValueError):
            continue
        out[s] = out.get(s, 0) + 1
    return out


def counts_per_second(times: list[float]) -> dict[int, int]:
    out: dict[int, int] = {}
    for t in times:
        s = int(float(t))
        out[s] = out.get(s, 0) + 1
    return out


def first_n_changes(cuts: list[float], cards: list[dict], span: float = FIRST_N_S) -> int:
    """前 `span` 秒内的画面变化次数：切点 + 卡片入场（0s 那一下是片头，不算）。"""
    n = sum(1 for t in cuts if 1e-6 < float(t) <= span + 1e-6)
    n += sum(1 for c in cards
             if 1e-6 < float(c.get("start") or 0) <= span + 1e-6)
    return n


def features(film: Path, project: Path | None) -> dict:
    info = probe_basic(film)
    dur = info["duration_s"]
    cuts, _sk_info = cut_times(film)
    cards = cards_of(project)
    lens = lens_of(cuts, dur)
    bright = brightness_per_second(film, info["w"], info["h"])
    return {
        "path": str(film), "duration_s": round(dur, 2), "cuts": cuts,
        "cards": cards, "lens": lens,
        "median_shot": round(statistics.median(lens), 2) if lens else 0.0,
        "mean_shot": round(statistics.mean(lens), 2) if lens else 0.0,
        "brightness": bright,
        "first3": first_n_changes(cuts, cards),
        "cut_sec": counts_per_second(cuts),
        "card_sec": cards_per_second(cards),
        "shot_sec": shot_len_per_second(cuts, dur),
    }


def explain(i: int, ours: dict, ref: dict, *, has_cards: bool) -> str:
    """这一秒差在哪、为什么。没有明显差异就返回空串。"""
    bits: list[str] = []
    oc, rc = ours["cut_sec"].get(i, 0), ref["cut_sec"].get(i, 0)
    if oc != rc:
        if oc < rc:
            bits.append(f"对标这一秒换了 {rc} 刀，我们只有 {oc} 刀"
                        + ("（镜头停着没动）" if oc == 0 else ""))
        else:
            bits.append(f"我们这一秒切了 {oc} 刀，对标 {rc} 刀（我们在赶进度）")
    if has_cards and ours["card_sec"].get(i):
        bits.append(f"我们这一秒有 {ours['card_sec'][i]} 张卡入场，对标量不到卡片层")
    ol, rl = ours["shot_sec"].get(i), ref["shot_sec"].get(i)
    if ol is not None and rl is not None and abs(ol - rl) >= SHOT_EPS:
        bits.append(f"这一镜我们 {ol:.1f}s、对标 {rl:.1f}s"
                    + ("（慢了一档，观众更容易划走）" if ol > rl else "（快了一档）"))
    ob = ours["brightness"][i] if i < len(ours["brightness"]) else None
    rb = ref["brightness"][i] if i < len(ref["brightness"]) else None
    if ob is not None and rb is not None and abs(ob - rb) >= LIGHT_EPS:
        bits.append(f"亮度 {ob:.2f} vs {rb:.2f}（"
                    + ("我们偏亮，白墙容易过曝" if ob > rb else "我们偏暗，手机上发闷") + "）")
    return "；".join(bits)


def _fmt(v, nd: int = 2) -> str:
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def build_report(ours: dict, ref: dict, *, ref_name: str = "对标") -> str:
    """生成 ref-compare.md 的正文（纯函数：测试直接喂两份 features）。"""
    has_cards = bool(ours["cards"])
    n = int(max(ours["duration_s"], ref["duration_s"])) + 1
    lines = ["# 对标结构对比（逐秒）", "",
             f"- 本片：`{Path(ours['path']).name}` · {ours['duration_s']:.2f}s · "
             f"{len(ours['cuts'])} 刀 · 镜头中位 {ours['median_shot']:.2f}s",
             f"- 对标：`{ref_name}` · {ref['duration_s']:.2f}s · "
             f"{len(ref['cuts'])} 刀 · 镜头中位 {ref['median_shot']:.2f}s", "",
             "| 对比项 | 本片 | 对标 | 差 |", "|---|---:|---:|---|"]
    rows = [
        ("片长", f"{ours['duration_s']:.2f}s", f"{ref['duration_s']:.2f}s",
         f"{ours['duration_s'] - ref['duration_s']:+.2f}s"),
        ("切点总数", str(len(ours["cuts"])), str(len(ref["cuts"])),
         f"{len(ours['cuts']) - len(ref['cuts']):+d}"),
        ("镜头长中位", f"{ours['median_shot']:.2f}s", f"{ref['median_shot']:.2f}s",
         f"{ours['median_shot'] - ref['median_shot']:+.2f}s"),
        ("镜头长均值", f"{ours['mean_shot']:.2f}s", f"{ref['mean_shot']:.2f}s",
         f"{ours['mean_shot'] - ref['mean_shot']:+.2f}s"),
        ("前 3 秒变化数", str(ours["first3"]), str(ref["first3"]),
         f"{ours['first3'] - ref['first3']:+d}"),
        ("卡片入场数", str(len(ours["cards"])) if has_cards else "—", "—", "对标无工程文件"),
        ("平均亮度", _fmt(sum(ours["brightness"]) / len(ours["brightness"]) if ours["brightness"] else None),
         _fmt(sum(ref["brightness"]) / len(ref["brightness"]) if ref["brightness"] else None),
         _fmt((sum(ours["brightness"]) / len(ours["brightness"])
               - sum(ref["brightness"]) / len(ref["brightness"]))
              if (ours["brightness"] and ref["brightness"]) else None)),
    ]
    for name, a, b, d in rows:
        lines.append(f"| {name} | {a} | {b} | {d} |")

    lines += ["", "## 逐秒读数", "",
              "| 秒 | 本片切点 | 对标切点 | 本片镜头 | 对标镜头 | 本片亮度 | 对标亮度 | 差异 |",
              "|---:|---:|---:|---:|---:|---:|---:|---|"]
    diffs: list[str] = []
    for i in range(n):
        why = explain(i, ours, ref, has_cards=has_cards)
        if why:
            diffs.append(f"- **{i}–{i + 1}s**：{why}")
        ob = ours["brightness"][i] if i < len(ours["brightness"]) else None
        rb = ref["brightness"][i] if i < len(ref["brightness"]) else None
        lines.append(
            f"| {i} | {ours['cut_sec'].get(i, 0)} | {ref['cut_sec'].get(i, 0)} | "
            f"{_fmt(ours['shot_sec'].get(i))} | {_fmt(ref['shot_sec'].get(i))} | "
            f"{_fmt(ob)} | {_fmt(rb)} | {why or '—'} |")

    lines += ["", "## 差在哪（自动解释）", ""]
    lines += diffs or ["- 逐秒结构没看出明显差异（切点、镜头长、亮度都在同一档）。"]
    lines += ["", "## 读法", "",
              "- 「切点」用同一支探针（skeleton.detect_cuts）量两边，同口径可比；",
              "- 「卡片入场」只有我们这侧有工程文件，对标那侧的包装层量不出来，"
              "所以这一列的空格不代表「人家没卡」；",
              "- 亮度差 ≥0.10 才算一档，小于它的按同一档处理（手机屏上看不出来）。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="成片 vs 对标视频的逐秒结构对比")
    ap.add_argument("--film", required=True, help="本片（成片 mp4）")
    ap.add_argument("--ref", required=True, help="对标视频 mp4")
    ap.add_argument("--project", default=None, help="项目目录（读 out/cards.json；不给就按—）")
    ap.add_argument("--out", default=None, help="报告路径，默认 <项目>/ref-compare.md")
    a = ap.parse_args()
    film = Path(a.film).expanduser().resolve()
    ref = Path(a.ref).expanduser().resolve()
    proj = Path(a.project).expanduser().resolve() if a.project else None
    for p in (film, ref):
        if not p.exists():
            print(f"找不到片子：{p}", file=sys.stderr)
            return 2
    ours = features(film, proj)
    theirs = features(ref, None)          # 对标没有工程文件：卡片层量不出来
    out = (Path(a.out) if a.out else (proj / "ref-compare.md" if proj
                                      else film.parent / "ref-compare.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(ours, theirs,
                                ref_name=f"{ref.parent.parent.name}/{ref.name}"),
                   encoding="utf-8")

    print(f"对标对比：本片 {len(ours['cuts'])} 刀 / 中位 {ours['median_shot']:.2f}s · "
          f"对标 {len(theirs['cuts'])} 刀 / 中位 {theirs['median_shot']:.2f}s · "
          f"前 3 秒变化 {ours['first3']} vs {theirs['first3']}")
    nd = sum(1 for i in range(int(max(ours["duration_s"], theirs["duration_s"])) + 1)
             if explain(i, ours, theirs, has_cards=bool(ours["cards"])))
    print(f"有差异的秒：{nd} 秒")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
