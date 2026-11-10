#!/usr/bin/env python3
"""CutSkills · plan.json + brief.json → timeline.json（元素时间轴 v4）。

v2 的 edl.json 是一条**镜头列表**，它默认「片子是从库里挑出来的」。
一旦承认素材不足要靠代码造，这个默认就不成立：成片的某一格可以是真实素材、
代码生成的动效、一张图、一段纯文字节奏，或者一段刻意的空。
所以 v4 换成 `elements[]`，每一格带 `kind`（clip / image / gen / silence）和 `track`。

这一层只回答一件事：**第几秒到第几秒，画面上是什么**。
论点、角色、为什么这么剪，来自 plan；风格参数来自 style 包；时间结构参考 skeleton。

用法：
  timeline.py --project <项目> --plan plan.json [--style talking-head-v1]
              [--skeleton skeleton.json] [--fill] [--fit-skeleton]
  timeline.py --project <项目> --from-edl        # v2 项目升级
  timeline.py --project <项目> --show
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_edl import build_edl, resolve_lib, split_plan  # noqa: E402

ROLES = {"setup", "claim", "evidence", "warrant", "turn", "payoff"}

# gen 模板表：**单一真值源**，check_timeline 直接 import 这里，不许各写一份。
# min_dur 是下限不是建议——低于它观众读不完，宁可不放。
# props 里数字是「字数上限」，None 表示自由文本。
# 轴线（v5）：A = 代码画面服务论点；B = 代码连续场景为叙事主体（人物缩成 PiP）。
AXES = {"A", "B"}
# 画面卡的**支撑功能**（跟 role 的叙事角色是两条轴，别混）：
#   evidence 出示来源 / explanation 讲因果 / calibration 给基准 / organization 分组排序 /
#   action 指向下一步 / consequence 前因后果
MG_ROLES = {"evidence", "explanation", "calibration", "organization", "action", "consequence"}
# 需要「数字有出处」的模板：画面里出现可被当真的数值
NUMERIC_TEMPLATES = {"number", "chart", "compare"}

GEN_TEMPLATES = {
    "keyword": {"min_dur": 2.0, "max_dur": 6.0,
                "props": {"text": 6, "sub": 12, "color": None}, "required": ["text"]},
    # left/right 是嵌套结构，限额写在字典里：{"title": 6, "value": 10}
    "compare": {"min_dur": 4.0, "max_dur": 8.0,
                # value 上限 12：「同品牌·同工艺·含基层」正好 11 字，这是这句话最短的写法。
                # 超过 12 字就会在栏里折两行，字号得往下降，两栏就不等重了。
                "props": {"left": {"title": 6, "value": 12},
                          "right": {"title": 6, "value": 12},
                          "note": 18, "title": 10},
                "required": ["left", "right"]},
    "number":  {"min_dur": 3.0, "max_dur": 6.0,
                "props": {"value": 6, "label": 14, "unit": 4}, "required": ["value", "label"]},
    "steps":   {"min_dur": 3.5, "max_dur": 8.0,
                "props": {"title": 10, "items": None}, "required": ["items"]},
    "quote":   {"min_dur": 3.0, "max_dur": 7.0,
                "props": {"text": 24, "who": 8}, "required": ["text"]},
    "chapter": {"min_dur": 1.8, "max_dur": 4.0,
                "props": {"n": 3, "title": 8}, "required": ["n", "title"]},
    # 下面两个是**会动的画面**，不是文字卡：库里没有对应实拍时，它们顶替 B-roll。
    "flow":    {"min_dur": 4.0, "max_dur": 9.0,
                "props": {"title": 12, "items": None, "foot": 16}, "required": ["items"]},
    "layers":  {"min_dur": 3.5, "max_dur": 8.0,
                "props": {"title": 12, "items": None, "foot": 16}, "required": ["items"]},
    "chart":   {"min_dur": 3.0, "max_dur": 8.0,
                "props": {"title": 12, "bars": None, "unit": 6}, "required": ["bars"]},
    # 场景生成器（scripts/gen_scene.py）画的整屏：不是文字卡，是**一件事在变**。
    # 位图由 gen_scene 逐帧画好再当画面播；声音必须另给 `audio`，否则那几秒是哑的。
    "scene":   {"min_dur": 2.0, "max_dur": 9.0,
                "props": {"scene": 16, "label": 8}, "required": ["scene"]},
}

KINDS = {"clip", "image", "gen", "silence"}
PAD_IN, PAD_OUT = 0.03, 0.05


# ---------------------------------------------------------------- 读取

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    return json.loads(p.read_text())


def proj_paths(proj: Path, lib: Path | None = None) -> dict:
    return {
        "lib": lib or resolve_lib(proj, None),
        "brief": proj / "brief.json",
        "plan": proj / "plan.json",
        "style": None,   # 由 resolve_style 补
        "timeline": proj / "timeline.json",
        "edl": proj / "edl.json",
        "skeleton": proj / "skeleton.json",
    }


def resolve_style(proj: Path, name: str | None, brief: dict | None) -> tuple[str, dict]:
    here = Path(__file__).resolve().parent.parent
    name = name or (brief or {}).get("style_pack") or "talking-head-v1"
    f = here / "styles" / f"{name}.yaml"
    if not f.exists():
        raise SystemExit(f"风格包不存在：{f}")
    try:
        import yaml  # noqa: F401
        style = yaml.safe_load(f.read_text())
    except Exception:
        style = {}
    return name, style


# ---------------------------------------------------------------- 元素构造

def kind_of(p: dict) -> str:
    """这一格是什么。**先看显式声明，再看 medium 的老写法**——plan 里 `medium: photo`
    是 v2 遗留，进 v4 就是 image；`medium: silence` 就是 silence。"""
    k = str(p.get("kind") or "").strip()
    if k in KINDS:
        return k
    if p.get("template"):
        return "gen"
    m = str(p.get("medium") or "").strip()
    if m == "silence":
        return "silence"
    if m == "photo":
        return "image"
    if m in ("card", "chart"):
        return "clip"   # 老写法：卡挂在镜头上，不是独立元素
    return "clip"


def _is_gen(p: dict) -> bool:
    return kind_of(p) in ("gen", "silence", "image")


def gen_element(p: dict, n: int) -> dict:
    tpl = str(p.get("template") or "").strip()
    props = dict(p.get("props") or {})
    spec = GEN_TEMPLATES.get(tpl) or {}
    dur = float(p.get("dur") or spec.get("min_dur") or 2.0)
    # track=overlay：**不占时间**，盖在某一段口播上（声音不断、画面不换）。
    # 为什么需要这一档：整屏滑屏要吃掉 4 秒旁白，而口播片里没有 4 秒是空的。
    # 做过的蠢事：把对比屏插成独立一格 → 4.3 秒无字幕空档，验收直接报警。
    track = "overlay" if str(p.get("track") or "") == "overlay" else "video"
    el = {"n": n, "kind": "gen", "track": track, "template": tpl, "props": props,
          "dur": round(max(dur, float(spec.get("min_dur") or 0.0)), 3),
          "source": "generated", "why": p.get("why") or ""}
    for k in ("beat", "role"):
        if p.get(k) is not None:
            el[k] = p[k]
    if track == "overlay":
        el["at"] = round(float(p.get("at") or 0.0), 3)
    return el


def clip_element(seg: dict, n: int) -> dict:
    el = {"n": n, "kind": "clip", "track": "video",
          "asset": seg["asset"], "in": seg["in"], "out": seg["out"],
          "medium": seg.get("medium"), "source": seg.get("source", "library"),
          "why": seg.get("why", ""), "evidence": seg.get("evidence", ""),
          "chars": seg.get("chars")}
    for k in ("beat", "role", "audio", "card", "grade_at_cut", "grade_confidence"):
        if seg.get(k) is not None:
            el[k] = seg[k]
    return el


def element_dur(el: dict) -> float:
    if el["kind"] == "clip":
        return round(float(el["out"]) - float(el["in"]), 3)
    return round(float(el.get("dur") or 0.0), 3)


# ---------------------------------------------------------------- 骨架贴合

def duration_bands(skeleton: dict | None, style: dict) -> dict:
    """每个元素该多长。有骨架用骨架，没骨架用风格包。"""
    if skeleton and (skeleton.get("cuts") or {}).get("median_s"):
        c = skeleton["cuts"]
        return {"p10": float(c.get("p10") or 1.0), "median": float(c["median_s"]),
                "p75": float(c.get("p75") or c["median_s"] * 1.6),
                "p90": float(c.get("p90") or c["median_s"] * 2.2), "src": "skeleton"}
    shots = style.get("shots") or {}
    med = float(shots.get("talking_median_s") or 2.6)
    return {"p10": med * 0.55, "median": med, "p75": med * 1.7, "p90": med * 2.6, "src": "style"}


def fit_skeleton(elements: list[dict], bands: dict, *, snap: bool = True) -> tuple[list[dict], list[str]]:
    """把明显偏离骨架的元素收回来。

    **只收不放**：能从素材里裁短，不能凭空变长（变长等于重复播放或拉慢动作）。
    长过 p90 的口播段裁到 p75 以内；裁的时候尽量落在字界上——由 check_edl 的
    token_bounds 给出安全切点，找不到字界就不裁，宁可留着也不切半句话。
    """
    notes: list[str] = []
    if not snap:
        return elements, notes
    p75 = bands["p75"]
    out: list[dict] = []
    for el in elements:
        d = element_dur(el)
        if el["kind"] == "clip" and d > bands["p90"]:
            target = round(min(p75, d), 3)
            new_out = el.get("_token_out") or _trim_at_token(el, target)
            if new_out and 0 < new_out - el["in"] < d:
                notes.append(f"#{el['n']} {d:.2f}s → {new_out - el['in']:.2f}s（超骨架 p90 "
                             f"{bands['p90']:.2f}s，裁到字界）")
                el = dict(el, out=round(new_out, 3))
        out.append(el)
    return out, notes


def _trim_at_token(el: dict, target: float) -> float | None:
    """在目标时长附近找一个真实的字界。没有库/索引就返回 None（不猜）。"""
    try:
        from check_edl import token_bounds
        b = token_bounds(_TRIM_LIB, el["asset"]) if _TRIM_LIB else None
    except Exception:
        return None
    if not b or not b[0]:
        return None
    want = el["in"] + target
    cand = [t for t in b[0] if el["in"] + 0.6 < t <= want + 0.35]
    return max(cand) if cand else None


_TRIM_LIB: Path | None = None


# ---------------------------------------------------------------- 主构造

def build_timeline(lib: Path, plan, *, brief: dict | None = None, style: dict | None = None,
                   skeleton: dict | None = None, style_name: str = "talking-head-v1",
                   fill: bool = False, fit: bool = False,
                   amap: dict | None = None) -> tuple[dict, list[tuple[str, str]]]:
    style = style or {}
    problems: list[tuple[str, str]] = []
    segs_in, beat_titles = split_plan(plan)
    try:
        index = {x["id"]: x for x in json.loads(
            (lib / "_cutskills" / "index.json").read_text())["assets"]}
    except FileNotFoundError:
        problems.append(("error", f"素材库没有索引：{lib / '_cutskills' / 'index.json'}"))
        index = {}

    # 1) 能进 build_edl 的（有素材、有进出的）先走老算法：吸附字界、留 padding、相邻让位。
    clip_plan = []
    for p in segs_in:
        if kind_of(p) != "clip":
            continue
        q = dict(p)
        # v4 写的是嵌套 `audio: {asset,in,out}`，build_edl 认的是摊平的
        # `audio_asset / audio_in / audio_out`（v2 约定）。这里只做一次翻译，
        # 不让两套写法在同一个仓库里并存——否则每加一种元素都要翻译一遍。
        a = q.pop("audio", None)
        if isinstance(a, dict) and a.get("asset"):
            q.setdefault("audio_asset", a["asset"])
            q.setdefault("audio_in", a.get("in"))
            q.setdefault("audio_out", a.get("out"))
        clip_plan.append(q)
    sub_plan = {"segments": clip_plan,
                "beats": [{"n": n, "title": t} for n, t in (beat_titles or {}).items()]}
    # padding 会直接把切点推离词边界：原来 0.06+0.09 让每个切点差 120ms，
    # 声画同步裁判一上来就量出来了（81% vs 需要 85%）。Audio-First 下切点必须贴着气口，
    # 所以这里只留够防爆音的量。
    edl, probs = build_edl(lib, sub_plan, pad_in=PAD_IN, pad_out=PAD_OUT)
    problems += probs
    clip_segs = iter(edl["segments"])

    # 2) 按**原顺序**合并：gen 元素不进 build_edl，直接构造。
    elements: list[dict] = []
    for p in segs_in:
        kd = kind_of(p)
        if kd == "silence":
            elements.append({"n": len(elements) + 1, "kind": "silence", "track": "video",
                             "dur": round(float(p.get("dur") or 0.8), 3),
                             "why": p.get("why") or "", "source": "generated",
                             **({"beat": p["beat"]} if p.get("beat") is not None else {}),
                             **({"role": p["role"]} if p.get("role") else {})})
            continue
        if kd == "image":
            rec = (index or {}).get(p.get("asset") or "") or {}
            if not rec:
                problems.append(("error", f"照片不在索引里：{p.get('asset')}"))
            el = {"n": len(elements) + 1, "kind": "image", "track": "video",
                  "asset": p.get("asset"), "dur": round(float(p.get("dur") or 3.0), 3),
                  "source": p.get("source") or "library", "why": p.get("why") or "",
                  "medium": "photo"}
            for k in ("beat", "role"):
                if p.get(k) is not None:
                    el[k] = p[k]
            elements.append(el)
            continue
        if kd == "gen":
            el = gen_element(p, len(elements) + 1)
            tpl = el["template"]
            if tpl not in GEN_TEMPLATES:
                problems.append(("error", f"gen 模板「{tpl}」不认识：{'/'.join(sorted(GEN_TEMPLATES))}"))
            if float(el["dur"]) < float((GEN_TEMPLATES.get(tpl) or {}).get("min_dur") or 0):
                problems.append(("error", f"gen#{el['n']} {tpl} 只有 {el['dur']}s，"
                                          f"模板下限 {GEN_TEMPLATES[tpl]['min_dur']}s"))
            elements.append(el)
        else:
            try:
                elements.append(clip_element(next(clip_segs), len(elements) + 1))
            except StopIteration:
                problems.append(("error", "内部错：clip 段数与 build_edl 结果不一致"))

    # 3) 补缺口：plan 里宣告了却没给画面的 beat，插章节卡（兜底链里的 gen，不是硬塞图）
    if fill:
        have = {el.get("beat") for el in elements}
        for n, title in (beat_titles or {}).items():
            if n in have:
                continue
            el = gen_element({"template": "chapter", "dur": 2.0,
                              "props": {"n": str(n).zfill(2), "title": (title or "")[:8]},
                              "why": f"论点 {n} 没有可用画面，用章节卡补位（兜底链第 3 级）",
                              "beat": n, "role": "setup"}, 0)
            elements.append(el)
            problems.append(("warning", f"论点 {n}「{title}」没有画面，已插章节卡"))

    # 4) 贴骨架
    notes: list[str] = []
    if fit:
        global _TRIM_LIB
        _TRIM_LIB = lib
        elements, notes = fit_skeleton(elements, duration_bands(skeleton, style))
        _TRIM_LIB = None

    # 声音说了算：overlay 卡片的入场时间贴着音频块的重音点（没有音频层时才用 plan 的 at）
    if amap:
        peaks = [round(float(b["t"]) + (float(b["peak_at"]) - float(b["src_start"])), 3)
                 for b in (amap.get("blocks") or [])
                 if b.get("peak_at") is not None and not b.get("is_filler")]
        for el in elements:
            if el.get("track") != "overlay" or not peaks:
                continue
            want = float(el.get("at") or 0.0)
            near = min(peaks, key=lambda p_: abs(p_ - want))
            if abs(near - want) <= 1.5:
                el["at"] = round(max(0.0, near - 0.12), 3)

    for i, el in enumerate(elements, 1):
        el["n"] = i

    # overlay 格不占时间轴（它盖在别的画面上），算总长时必须排除，否则片长凭空长 4 秒
    total = round(sum(element_dur(el) for el in elements
                      if (el.get("track") or "video") != "overlay"), 3)
    beats = []
    for n in sorted({el.get("beat") for el in elements if el.get("beat") is not None}):
        own = [el for el in elements if el.get("beat") == n]
        beats.append({"n": n, "title": (beat_titles or {}).get(n, ""),
                      "elements": [el["n"] for el in own],
                      "seconds": round(sum(element_dur(el) for el in own), 2),
                      "roles": sorted({el.get("role") for el in own if el.get("role")})})

    tl = {
        "version": 5,
        "slug": None,
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": total,
        "style_pack": style_name,
        "brief": ({k: brief.get(k) for k in
                   ("audience", "change", "length_s", "style_pack", "length_band")}
                  if brief else None),
        "skeleton": "skeleton.json" if skeleton else None,
        "bands": duration_bands(skeleton, style),
        "beats": beats,
        "elements": elements,
        "_notes": notes,
    }
    if brief and brief.get("length_s"):
        want = float(brief["length_s"])
        diff = (total - want) / want if want else 0
        if abs(diff) > 0.10:
            problems.append(("warning", f"总时长 {total:.1f}s，需求片长 {want:.0f}s，"
                                        f"差 {diff:+.0%}（阈值 ±10%）"))
    return tl, problems


# ---------------------------------------------------------------- 校验（轻量版，重版在 check_timeline.py）

def quick_check(tl: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for el in tl.get("elements") or []:
        if el.get("kind") not in KINDS:
            out.append(("error", f"#{el.get('n')} kind「{el.get('kind')}」非法"))
        if not (el.get("why") or "").strip():
            out.append(("warning", f"#{el.get('n')} 没写 why"))
    if not tl.get("elements"):
        out.append(("error", "一个元素都没有"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--brief", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--skeleton", default=None)
    ap.add_argument("--fill", action="store_true", help="没有画面的论点插章节卡补位")
    ap.add_argument("--fit-skeleton", action="store_true", help="长过骨架 p90 的元素裁到字界")
    ap.add_argument("--from-edl", action="store_true", help="把 v2 edl.json 升成 v4")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else resolve_lib(proj, None)
    tl_path = proj / "timeline.json"

    if a.show:
        tl = load_json(tl_path)
        if not tl:
            raise SystemExit(f"没有 timeline.json：{tl_path}")
        print(f"{tl.get('duration_s')}s · {len(tl.get('elements') or [])} 元素 · {tl.get('style_pack')}")
        for el in tl["elements"]:
            d = element_dur(el)
            head = el.get("template") or (f"{el.get('medium')} {Path(str(el.get('asset'))).name}"
                                          if el.get("asset") else "—")
            print(f"  {el['n']:>2}. {el['kind']:<7} {d:>5.2f}s  {head}"
                  f"  {('★' + el['role']) if el.get('role') else ''}")
        return 0

    style_name, style = resolve_style(proj, a.style, load_json(proj / "brief.json"))
    brief = load_json(Path(a.brief) if a.brief else proj / "brief.json")
    skeleton = load_json(Path(a.skeleton) if a.skeleton else proj / "skeleton.json")

    if a.from_edl:
        edl = load_json(proj / "edl.json")
        if not edl:
            raise SystemExit("没有 edl.json，无可升级")
        plan = {"beats": [{"n": b["n"], "title": b.get("title", "")} for b in edl.get("beats") or []],
                "segments": [{k: s[k] for k in ("asset", "in", "out", "medium", "source", "why",
                                                "beat", "role", "audio", "card")
                              if s.get(k) is not None} for s in edl["segments"]]}
        problems: list[tuple[str, str]] = []
        tl = {"version": 4, "slug": edl.get("slug"), "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
              "duration_s": round(sum(s["out"] - s["in"] for s in edl["segments"]), 3),
              "style_pack": edl.get("style_pack") or style_name,
              "brief": brief, "skeleton": None, "bands": duration_bands(skeleton, style),
              "elements": [clip_element(s, i) for i, s in enumerate(edl["segments"], 1)],
              "beats": edl.get("beats") or [], "_notes": ["从 v2 edl.json 原样搬过来，全部 kind=clip"]}
    else:
        plan_path = Path(a.plan) if a.plan else proj / "plan.json"
        plan = load_json(plan_path)
        if not plan:
            raise SystemExit(f"没有 plan.json：{plan_path}")
        amap = load_json(proj / "audio-map.json")
        tl, problems = build_timeline(lib, plan, brief=brief, style=style, skeleton=skeleton,
                                      style_name=style_name, fill=a.fill, fit=a.fit_skeleton,
                                      amap=amap)
        tl["slug"] = proj.name

    problems += quick_check(tl)
    errs = [m for lv, m in problems if lv == "error"]
    warns = [m for lv, m in problems if lv == "warning"]

    print(f"时间轴 {tl['duration_s']}s · {len(tl['elements'])} 元素 · 风格 {tl['style_pack']}"
          f" · 骨架 {tl['bands']['src']}（中位 {tl['bands']['median']:.2f}s）")
    for el in tl["elements"]:
        d = element_dur(el)
        flag = "·" if el["kind"] == "clip" and el.get("role") in ("claim", "warrant") else " "
        head = el.get("template") or (Path(str(el.get("asset"))).stem[-24:] if el.get("asset") else "—")
        print(f" {flag}{el['n']:>2}. {el['kind']:<7} {d:>5.2f}s  {head}")
    for nt in tl.get("_notes") or []:
        print(f"  · {nt}")
    for lv, m in problems:
        print(f"  {'✗' if lv == 'error' else '!'} {m}")

    if not a.dry_run:
        tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {tl_path}")
    print(f"校验：error {len(errs)} · warning {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
