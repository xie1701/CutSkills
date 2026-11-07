#!/usr/bin/env python3
"""CutSkills · 把剪辑计划落成 edl.json（自动吸附字界 + 自动留 padding）。

计划只要写「从哪句到哪句」和「这段在论证里干什么」，padding、吸附、相邻让位由脚本算。
手写时间码最容易犯的错就是两段互相重叠。

计划格式（plan.json），两种都行：

  [ {"asset": "熊工口播3.MOV", "in": 231.66, "out": 234.36, "medium": "talking",
     "beat": 1, "role": "claim", "why": "开场把观众的怀疑原话说出来"} ]

  {"beats": [{"n": 1, "title": "两种说法各摆一边"},
             {"n": 2, "title": "我们怎么选"}],
   "segments": [ ... 同上 ... ]}

`beat`（论点号）和 `role`（这段在干什么）是可选的，但**没有它们就没有结构层**：
check_edl 只会判节奏和切边，判不了「这条片子有没有论点、每个论点有没有证据」。

用法：
  make_edl.py --project <项目目录> --plan plan.json [--pad-in 0.06] [--pad-out 0.09]
  make_edl.py --project <项目目录> --list | --revert
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_edl import token_bounds, tokens, pad_edge  # noqa: E402

GRADE_ORDER = {"must-use": 0, "backup": 1, "do-not-use": 2}
MEDIUMS = {"talking", "broll", "photo", "card", "chart", "screen", "generated", "silence"}
ROLES = {"setup", "claim", "evidence", "warrant", "turn", "payoff"}


def best_grade(segs: list[dict]) -> str:
    """一支素材的「最好一段」分级 —— 与 rename_assets.py 同一口径。"""
    gs = [s.get("grade") for s in segs if s.get("grade")]
    return min(gs, key=lambda g: GRADE_ORDER.get(g, 9)) if gs else "backup"


def grade_at(rec: dict, tin: float, tout: float) -> tuple[str, float | None]:
    """这段落下来时，索引里它的分级与置信度。

    按区间重叠取最相关的那条记录：L2 存的是段级判断，L0 存的是整支猜测。
    取不到（区间不在任何记录里）时退到「这支素材最好的一段」，而不是默认 must-use——
    默认必用会让事后统计「系统判断准不准」永远显示 100% 准确。

    置信度透传自 L2（L0 最高 0.4，那是“还没看过画面”的自白，不是置信度）。
    """
    segs = rec.get("segments") or []
    if not segs:
        return "backup", None
    best, best_ov = None, -1.0
    for s in segs:
        ov = min(tout, float(s.get("out") or 0)) - max(tin, float(s.get("in") or 0))
        if ov > best_ov:
            best, best_ov = s, ov
    if best is None or best_ov <= 0:
        return best_grade(segs), None
    conf = best.get("confidence")
    return (best.get("grade") or best_grade(segs),
            round(float(conf), 2) if isinstance(conf, (int, float)) else None)


def split_plan(plan) -> tuple[list[dict], dict[int, str]]:
    """兼容 list 和老手写的 dict；返回 (段落, {论点号: 标题})。"""
    if isinstance(plan, list):
        return plan, {}
    beats = {int(b["n"]): (b.get("title") or "") for b in (plan.get("beats") or []) if "n" in b}
    return (plan.get("segments") or []), beats


def build_edl(lib: Path, plan, index: dict | None = None, *,
              pad_in: float = 0.06, pad_out: float = 0.09) -> tuple[dict, list[tuple[str, str]]]:
    """plan → edl，**不落盘**。落盘（make_edl）与试算（preview_plan）共用这一份算法，
    否则「预览说 71 秒、落盘 74 秒」这种分歧迟早会出现，而且没人查得出来。
    """
    if index is None:
        index = {x["id"]: x for x in json.loads(
            (lib / "_cutskills" / "index.json").read_text())["assets"]}
    segs_in, beat_titles = split_plan(plan)
    problems: list[tuple[str, str]] = []

    bounds_cache: dict[str, tuple[list[float], list[float]] | None] = {}

    def bounds(aid: str):
        if aid not in bounds_cache:
            bounds_cache[aid] = token_bounds(lib, aid)
        return bounds_cache[aid]

    segs: list[dict] = []
    # 声音轨上一个收尾点：相邻段共用字界时不许互吃。
    # 只在**同一支素材**上、且真实重叠（间隔 <1s）时才夹——不然「开场把最后一段搬上来」
    # 这种倒序剪辑会被夹成 0.4 秒（实测：开场 304.02 之后接 231.60，整段被压成 0.40s）。
    prev_out: dict[str, float] = {}
    for i, p in enumerate(segs_in, 1):
        medium = p.get("medium")
        if medium not in MEDIUMS:
            problems.append(("error", f"#{i} medium「{medium}」不在约定值里：{'/'.join(sorted(MEDIUMS))}"))
            continue
        if not p.get("asset"):
            problems.append(("error", f"#{i} 没写 asset"))
            continue
        rec = index.get(p["asset"])
        if not rec:
            problems.append(("error", f"#{i} 素材不在索引里：{p['asset']}"))
            continue
        beat, role = p.get("beat"), p.get("role")
        if beat is not None and not isinstance(beat, int):
            problems.append(("error", f"#{i} beat 要是整数（论点号），现在是 {beat!r}"))
            beat = None
        if role is not None and role not in ROLES:
            problems.append(("error", f"#{i} role「{role}」不在约定值里：{'/'.join(sorted(ROLES))}"))

        src = p.get("audio_asset") or p["asset"]
        dur = rec.get("duration") or 0
        b = bounds(src)

        if medium == "talking":
            ain = pad_edge(b, float(p["in"]), -1, pad_in, dur)
            aout = pad_edge(b, float(p["out"]), +1, pad_out, dur)
            prev = prev_out.get(src)
            if prev is not None and ain < prev and 0 < prev - ain < 1.0 and aout > prev:
                ain = prev
            if aout <= ain:
                aout = round(ain + 0.4, 3)
            seg = {"n": i, "asset": p["asset"], "in": round(ain, 3), "out": round(aout, 3),
                   "medium": medium, "source": p.get("source", "library"), "why": p.get("why", "")}
        else:
            if src not in index:
                problems.append(("error", f"#{i} 声音素材不在索引里：{src}"))
                continue
            srcd = index[src]["duration"]
            try:
                ain = pad_edge(b, float(p["audio_in"]), -1, pad_in, srcd)
                aout = pad_edge(b, float(p["audio_out"]), +1, pad_out, srcd)
            except KeyError:
                problems.append(("error", f"#{i} 是 {medium} 段，必须给 audio_in / audio_out"
                                          f"（声音从哪来），或把 audio_asset 指向自己"))
                continue
            prev = prev_out.get(src)
            if prev is not None and ain < prev and 0 < prev - ain < 1.0 and aout > prev:
                ain = prev
            span = round(aout - ain, 3)
            v_in = float(p["in"])
            v_out = min(dur, max(v_in + 0.4, v_in + span))
            if v_out - v_in > span:
                v_in = max(0.0, v_out - span)
            seg = {"n": i, "asset": p["asset"], "in": round(v_in, 3), "out": round(v_out, 3),
                   "medium": medium, "source": p.get("source", "library"), "why": p.get("why", ""),
                   "audio": {"asset": src, "in": round(ain, 3), "out": round(aout, 3)}}
        prev_out[src] = aout

        # 字数与「这一段说了什么」都按**字**算，不按 ASR 的整句短语算：
        # 一句短语横跨几秒，两段各切一半时它会同时落进两段，于是查重全是假阳性。
        tok = tokens(lib, src)
        if tok:
            chars_, st_, en_ = tok
            text = "".join(c for c, st, en in zip(chars_, st_, en_)
                           # 按**起点**筛：切点落在字中间时，那个字是上一个词的尾巴，
                           # 收进来会让稿子读成「说事情不是…」「实都有偏颇」这种掉头断句
                           if st >= ain - 0.02 and st < aout - 0.02)
        else:
            ph = (index.get(src, {}).get("transcript") or {}).get("phrases") or []
            text = "".join(x["text"] for x in ph if x["end"] > ain + 0.05 and x["start"] < aout - 0.05)
        seg["evidence"] = text[:80]
        seg["chars"] = len(text)
        if beat is not None:
            seg["beat"] = beat
        if role:
            seg["role"] = role
        # 字卡：口播里没说、但观众需要看见的事实（数字、对比、专名）。
        # 它不是字幕——字幕跟着话音走，字卡是人写上去的一句话。
        card = p.get("card")
        if isinstance(card, dict) and str(card.get("text") or "").strip():
            seg["card"] = {"text": str(card["text"]).strip(),
                           "at": card.get("at"),
                           "dur": card.get("dur")}
        # 分级取「画面这段」的分级：broll 的画面和声音来自不同素材，分级说的是画面
        grade, conf = grade_at(rec, seg["in"], seg["out"])
        seg["grade_at_cut"] = grade
        if conf is not None:
            seg["grade_confidence"] = conf
        segs.append(seg)

    # ---- 结构层：把论点摊平成可读、可校的形状
    beats = []
    for n in sorted({s["beat"] for s in segs if s.get("beat") is not None}):
        own = [s for s in segs if s.get("beat") == n]
        beats.append({"n": n, "title": beat_titles.get(n) or "",
                      "segs": [s["n"] for s in own],
                      "seconds": round(sum(s["out"] - s["in"] for s in own), 2),
                      "roles": sorted({s["role"] for s in own if s.get("role")})})
    return {"segments": segs, "beats": beats}, problems



def resolve_lib(proj: Path, given: str | None) -> Path:
    """素材库在哪。项目默认建在 `<库>/_cutskills/projects/<slug>`，那种情况下往上三层就是库；
    项目放在别处（/tmp、别的盘）时，往上三层会解析到一个没有索引的目录——以前这里
    直接抛 FileNotFoundError，看不出是「库路径错了」还是「索引没建」。所以要能显式指定。
    """
    if given:
        return Path(given).expanduser().resolve()
    cand = proj.parents[2] if len(proj.parents) > 2 else proj
    if (cand / "_cutskills" / "index.json").exists():
        return cand
    raise SystemExit(f"找不到素材库：{cand}/_cutskills/index.json 不存在。\n"
                     f"  项目默认要建在 <库>/_cutskills/projects/<slug> 下；\n"
                     f"  放在别处就加 --library \"<素材库根目录>\"。")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", default=None, help="剪辑计划；--revert / --list 时不需要")
    ap.add_argument("--library", default=None, help="默认取项目往上三层的素材库")
    ap.add_argument("--pad-in", type=float, default=0.06)
    ap.add_argument("--pad-out", type=float, default=0.09)
    ap.add_argument("--revert", action="store_true", help="回到上一个版本（edl-history 里最新一份）")
    ap.add_argument("--list", action="store_true", help="列出历史版本")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    hist = proj / "edl-history"

    # ---- 历史：edl.json 是真值源，真值源没有历史就等于不可回滚
    if a.list or a.revert:
        snaps = sorted(hist.glob("*.json")) if hist.exists() else []
        if not snaps:
            print("没有历史版本（每跑一次 make_edl.py 会存一份）")
            return 0 if a.list else 1
        if a.list:
            for f in snaps:
                try:
                    d = json.loads(f.read_text())
                    n, t = len(d.get("segments") or []), round(
                        sum(s["out"] - s["in"] for s in d.get("segments") or []), 2)
                except (json.JSONDecodeError, KeyError):
                    n = t = "?"
                print(f"{f.stem}  {n} 段 / {t}s")
            return 0
        latest = snaps[-1]
        (proj / "edl.json").write_text(latest.read_text())
        latest.rename(latest.with_suffix(".reverted.json"))     # 用过就改名，防止连点两次回到同一版
        print(f"已回滚到 {latest.stem}（那份已标记 .reverted，不再参与排序）")
        return 0

    if not a.plan:
        print("--plan 必填（或改用 --revert / --list）", file=sys.stderr)
        return 2
    lib = resolve_lib(proj, a.library)
    index = {x["id"]: x for x in json.loads((lib / "_cutskills" / "index.json").read_text())["assets"]}
    intent = json.loads((proj / "intent.json").read_text())
    plan_path = Path(a.plan)
    plan = json.loads(plan_path.read_text())

    built, problems = build_edl(lib, plan, index, pad_in=a.pad_in, pad_out=a.pad_out)
    if problems:
        for lvl, msg in problems:
            print(f"{lvl}: {msg}", file=sys.stderr)
        print(f"计划有 {len(problems)} 处问题，没有落盘", file=sys.stderr)
        return 2

    segs = built["segments"]
    edl = {"version": 2, "slug": proj.name, "created": intent.get("created"),
           "cut_source": intent.get("cut_source", "speech"),
           "visual_selector": intent.get("visual_selector", "single_cam_talking"),
           "style_pack": intent.get("style_pack", "talking-head-v1"),
           "beats": built["beats"],
           "segments": segs}

    # 计划本身也是决定：EDL 能回滚、计划不能，等于只能回到「另一版结果」，回不到「当时的意图」
    ph = proj / "plan-history"
    ph.mkdir(exist_ok=True)
    plan_snap = ph / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    plan_snap.write_text(plan_path.read_text())

    (proj / "edl-history").mkdir(exist_ok=True)
    cur = proj / "edl.json"
    if cur.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (hist / f"{stamp}.json").write_text(cur.read_text())
    cur.write_text(json.dumps(edl, ensure_ascii=False, indent=1))
    total = sum(s["out"] - s["in"] for s in segs)
    nb = len(built["beats"])
    print(f"edl.json 写入 {len(segs)} 段 / {nb} 个论点，总长 {total:.2f}s"
          f"（目标 {intent.get('duration_s')}s）")
    if nb == 0:
        print("提示：这次没有 beat / role —— 结构层是空的，check_edl 只能判节奏，判不了论证")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
