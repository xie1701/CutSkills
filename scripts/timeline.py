#!/usr/bin/env python3
"""CutSkills · plan.json + brief.json → timeline.json（元素时间轴 v5）。

每一格带 `kind`（clip / image / gen / silence）和 `track`（video / overlay），
同时承载短视频网感全包装字段：
- 顶层 `title_bar`（开篇/常驻悬念标题条）、`stickers`（全局反应贴纸轨）、`bgm_mood`、`beat_sync`
- 元素级 `card`（大字/MG 动效卡）、`sticker`（段内情绪贴纸）、`vfx`（冲击推镜/缓推/闪切/光效）

用法：
  timeline.py --project <项目> --plan plan.json [--style talking-head-v1]
              [--skeleton skeleton.json] [--fill] [--fit-skeleton]
  timeline.py --project <项目> --show
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_guard import pad_edge, token_bounds, tokens  # noqa: E402
from libs import resolve_lib  # noqa: E402

GRADE_ORDER = {"must-use": 0, "backup": 1, "do-not-use": 2}
MEDIUMS = {"talking", "broll", "photo", "card", "chart", "screen", "generated", "silence"}
ROLES = {"setup", "claim", "evidence", "warrant", "turn", "payoff"}
AXES = {"A", "B"}
MG_ROLES = {"evidence", "explanation", "calibration", "organization", "action", "consequence"}
NUMERIC_TEMPLATES = {"number", "chart", "compare"}

GEN_TEMPLATES = {
    # 时长上限按零拍摄路线的 AI 配音句窗实测校准（2026-11-14：4 句 32.8s，句均 8.2s）：
    # compare/flow 渲器原生子件逐条进场、quote/keyword 有 micro 文字接力，不是死卡；
    # 上限定得比句窗略宽，避免「会显得空」的假警告漏到正常配音片上。
    "keyword": {"min_dur": 1.6, "max_dur": 6.0,
                "props": {"text": 8, "sub": 14, "color": None}, "required": ["text"]},
    "compare": {"min_dur": 3.5, "max_dur": 10.0,
                "props": {"left": {"title": 6, "value": 12},
                          "right": {"title": 6, "value": 12},
                          "note": 18, "title": 10},
                "required": ["left", "right"]},
    "balance": {"min_dur": 3.5, "max_dur": 8.0,
                "props": {"left": {"title": 6, "value": 12},
                          "right": {"title": 6, "value": 12},
                          "note": 18},
                "required": ["left", "right"]},
    "number":  {"min_dur": 2.5, "max_dur": 6.0,
                "props": {"value": 8, "label": 14, "unit": 4}, "required": ["value", "label"]},
    "steps":   {"min_dur": 3.0, "max_dur": 10.0,
                "props": {"title": 10, "items": None}, "required": ["items"]},
    "quote":   {"min_dur": 2.8, "max_dur": 7.5,
                "props": {"text": 24, "who": 8}, "required": ["text"]},
    "chapter": {"min_dur": 1.5, "max_dur": 4.0,
                "props": {"n": 3, "title": 10}, "required": ["n", "title"]},
    "flow":    {"min_dur": 3.5, "max_dur": 10.0,
                "props": {"title": 12, "items": None, "foot": 16}, "required": ["items"]},
    "layers":  {"min_dur": 3.2, "max_dur": 8.0,
                "props": {"title": 12, "items": None, "foot": 16}, "required": ["items"]},
    "chart":   {"min_dur": 2.8, "max_dur": 8.0,
                "props": {"title": 12, "bars": None, "unit": 6}, "required": ["bars"]},
    "scene":   {"min_dur": 2.0, "max_dur": 16.0,
                "props": {"scene": 20, "label": 10}, "required": ["scene"]},
}

KINDS = {"clip", "image", "gen", "silence"}
PAD_IN, PAD_OUT = 0.03, 0.05


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    return json.loads(p.read_text())


def split_plan(plan) -> tuple[list[dict], dict[int, str], dict]:
    """兼容 list 与 dict 格式计划；返回 (段落, {论点号: 标题}, 顶层扩展字段)。"""
    if isinstance(plan, list):
        return plan, {}, {}
    beats = {int(b["n"]): (b.get("title") or "") for b in (plan.get("beats") or []) if "n" in b}
    extra = {k: plan[k] for k in ("title_bar", "stickers", "bgm_mood", "pack", "plan", "axis")
             if plan.get(k) is not None}
    return (plan.get("segments") or plan.get("elements") or []), beats, extra


def _best_grade(segs: list[dict]) -> str:
    gs = [s.get("grade") for s in segs if s.get("grade")]
    return min(gs, key=lambda g: GRADE_ORDER.get(g, 9)) if gs else "backup"


def _grade_at(rec: dict, tin: float, tout: float) -> tuple[str, float | None]:
    segs = rec.get("segments") or []
    if not segs:
        return "backup", None
    best, best_ov = None, -1.0
    for s in segs:
        ov = min(tout, float(s.get("out") or 0)) - max(tin, float(s.get("in") or 0))
        if ov > best_ov:
            best, best_ov = s, ov
    if best is None or best_ov <= 0:
        return _best_grade(segs), None
    conf = best.get("confidence")
    return (best.get("grade") or _best_grade(segs),
            round(float(conf), 2) if isinstance(conf, (int, float)) else None)


def resolve_style(proj: Path, name: str | None, brief: dict | None) -> tuple[str, dict]:
    here = Path(__file__).resolve().parent.parent
    name = name or (brief or {}).get("style_pack") or "talking-head-v1"
    f = here / "styles" / f"{name}.yaml"
    if not f.exists():
        raise SystemExit(f"风格包不存在：{f}")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
        import yaml
        style = yaml.safe_load(f.read_text()) or {}
    except Exception:
        style = {}
    return name, style


def kind_of(p: dict) -> str:
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
    return "clip"


def gen_element(p: dict, n: int) -> dict:
    tpl = str(p.get("template") or "").strip()
    props = dict(p.get("props") or {})
    spec = GEN_TEMPLATES.get(tpl) or {}
    dur = float(p.get("dur") or spec.get("min_dur") or 2.0)
    track = "overlay" if str(p.get("track") or "") == "overlay" else "video"
    el = {"n": n, "kind": "gen", "track": track, "template": tpl, "props": props,
          "dur": round(max(dur, float(spec.get("min_dur") or 0.0)), 3),
          "source": "generated", "why": p.get("why") or ""}
    for k in ("beat", "role", "audio", "evidence", "card", "sticker", "vfx", "mg_role",
              "narrative", "micro", "approx", "source_ref", "hard_cut", "carry"):
        if p.get(k) is not None:
            el[k] = p[k]
    if track == "overlay":
        el["at"] = round(float(p.get("at") or 0.0), 3)
    return el


def element_dur(el: dict) -> float:
    if el["kind"] == "clip":
        return round(float(el["out"]) - float(el["in"]), 3)
    return round(float(el.get("dur") or 0.0), 3)


def duration_bands(skeleton: dict | None, style: dict) -> dict:
    if skeleton and (skeleton.get("cuts") or {}).get("median_s"):
        c = skeleton["cuts"]
        return {"p10": float(c.get("p10") or 1.0), "median": float(c["median_s"]),
                "p75": float(c.get("p75") or c["median_s"] * 1.6),
                "p90": float(c.get("p90") or c["median_s"] * 2.2), "src": "skeleton"}
    shots = style.get("shots") or {}
    med = float(shots.get("talking_median_s") or 2.4)
    return {"p10": med * 0.55, "median": med, "p75": med * 1.7, "p90": med * 2.6, "src": "style"}


def _trim_at_token(lib: Path, el: dict, target: float) -> float | None:
    b = token_bounds(lib, el["asset"])
    if not b or not b[0]:
        return None
    want = el["in"] + target
    cand = [t for t in b[0] if el["in"] + 0.6 < t <= want + 0.35]
    return max(cand) if cand else None


def fit_skeleton(lib: Path, elements: list[dict], bands: dict, *, snap: bool = True) -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    if not snap:
        return elements, notes
    p75 = bands["p75"]
    out: list[dict] = []
    for el in elements:
        d = element_dur(el)
        if el["kind"] == "clip" and el.get("medium") == "talking" and d > bands["p90"]:
            target = round(min(p75, d), 3)
            new_out = el.get("_token_out") or _trim_at_token(lib, el, target)
            if new_out and 0 < new_out - el["in"] < d:
                notes.append(f"#{el['n']} {d:.2f}s → {new_out - el['in']:.2f}s（裁到字界）")
                el = dict(el, out=round(new_out, 3))
                if isinstance(el.get("audio"), dict) and el["audio"].get("asset") == el.get("asset"):
                    el["audio"] = dict(el["audio"], out=round(new_out, 3))
        out.append(el)
    return out, notes


def _build_clip_element(lib: Path, index: dict, p: dict, n: int,
                        prev_out: dict[str, float],
                        bounds_cache: dict) -> tuple[dict | None, list[tuple[str, str]]]:
    probs: list[tuple[str, str]] = []
    medium = str(p.get("medium") or "talking")
    if medium not in MEDIUMS:
        probs.append(("error", f"#{n} medium「{medium}」非法"))
        return None, probs
    aid = p.get("asset")
    if not aid:
        probs.append(("error", f"#{n} 缺 asset"))
        return None, probs
    rec = index.get(aid)
    if not rec:
        probs.append(("error", f"#{n} 素材不在索引里：{aid}"))
        return None, probs

    au_obj = p.get("audio") if isinstance(p.get("audio"), dict) else {}
    src = au_obj.get("asset") or p.get("audio_asset") or aid
    dur = float(rec.get("duration") or 0.0)
    if src not in bounds_cache:
        bounds_cache[src] = token_bounds(lib, src)
    b = bounds_cache[src]

    if medium == "talking":
        ain = pad_edge(b, float(p["in"]), -1, PAD_IN, dur)
        aout = pad_edge(b, float(p["out"]), +1, PAD_OUT, dur)
        prev = prev_out.get(src)
        if prev is not None and ain < prev and 0 < prev - ain < 1.0 and aout > prev:
            ain = prev
        if aout <= ain:
            aout = round(ain + 0.4, 3)
        prev_out[src] = aout
        el: dict = {
            "n": n, "kind": "clip", "track": "video",
            "asset": aid, "in": round(ain, 3), "out": round(aout, 3),
            "medium": medium, "source": p.get("source", "library"),
            "why": p.get("why", ""),
            "audio": {"asset": src, "in": round(ain, 3), "out": round(aout, 3)},
        }
    else:
        raw_ain = au_obj.get("in") if au_obj.get("in") is not None else p.get("audio_in")
        raw_aout = au_obj.get("out") if au_obj.get("out") is not None else p.get("audio_out")
        v_in = float(p.get("in", 0.0))
        v_out = float(p.get("out", min(dur, v_in + 3.0)))
        el = {
            "n": n, "kind": "clip", "track": "video",
            "asset": aid, "in": round(v_in, 3), "out": round(v_out, 3),
            "medium": medium, "source": p.get("source", "library"),
            "why": p.get("why", ""),
        }
        if raw_ain is not None and raw_aout is not None:
            if src not in index:
                probs.append(("error", f"#{n} 声音素材不在索引里：{src}"))
                return None, probs
            srcd = float(index[src].get("duration") or 0.0)
            ain = pad_edge(b, float(raw_ain), -1, PAD_IN, srcd)
            aout = pad_edge(b, float(raw_aout), +1, PAD_OUT, srcd)
            prev = prev_out.get(src)
            if prev is not None and ain < prev and 0 < prev - ain < 1.0 and aout > prev:
                ain = prev
            prev_out[src] = aout
            span = round(aout - ain, 3)
            v_out = min(dur, max(v_in + 0.4, v_in + span))
            if v_out - v_in > span:
                v_in = max(0.0, v_out - span)
            el["in"], el["out"] = round(v_in, 3), round(v_out, 3)
            el["audio"] = {"asset": src, "in": round(ain, 3), "out": round(aout, 3)}
        else:
            ain, aout = el["in"], el["out"]

    if p.get("evidence") is not None:
        text = str(p["evidence"])
    else:
        tok = tokens(lib, src)
        if tok and ("audio" in el or medium == "talking"):
            chars_, st_, _en = tok
            text = "".join(c for c, st in zip(chars_, st_) if ain - 0.02 <= st < aout - 0.02)
        else:
            ph = (index.get(src, {}).get("transcript") or {}).get("phrases") or []
            text = "".join(x["text"] for x in ph if float(x.get("end", 0)) > ain + 0.05 and float(x.get("start", 0)) < aout - 0.05)

    el["evidence"] = text[:400]
    el["chars"] = len(text)
    grade, conf = _grade_at(rec, el["in"], el["out"])
    el["grade_at_cut"] = grade
    if conf is not None:
        el["grade_confidence"] = conf

    for k in ("beat", "role", "card", "sticker", "vfx", "props", "third_party_action"):
        if p.get(k) is not None:
            el[k] = p[k]
    return el, probs


def build_timeline(lib: Path, plan, *, brief: dict | None = None, style: dict | None = None,
                   skeleton: dict | None = None, style_name: str = "talking-head-v1",
                   fill: bool = False, fit: bool = False,
                   amap: dict | None = None) -> tuple[dict, list[tuple[str, str]]]:
    style = style or {}
    problems: list[tuple[str, str]] = []
    segs_in, beat_titles, extra = split_plan(plan)
    try:
        index = {x["id"]: x for x in json.loads(
            (lib / "_cutskills" / "index.json").read_text())["assets"]}
    except FileNotFoundError:
        problems.append(("error", f"素材库没有索引：{lib / '_cutskills' / 'index.json'}"))
        index = {}

    prev_out: dict[str, float] = {}
    bounds_cache: dict = {}
    elements: list[dict] = []

    for p in segs_in:
        kd = kind_of(p)
        idx_n = len(elements) + 1
        if kd == "silence":
            el = {"n": idx_n, "kind": "silence", "track": "video",
                  "dur": round(float(p.get("dur") or 0.8), 3),
                  "why": p.get("why") or "", "source": "generated"}
            for k in ("beat", "role"):
                if p.get(k) is not None:
                    el[k] = p[k]
            elements.append(el)
        elif kd == "image":
            rec = index.get(p.get("asset") or "")
            if not rec:
                problems.append(("error", f"照片不在索引里：{p.get('asset')}"))
            el = {"n": idx_n, "kind": "image", "track": "video",
                  "asset": p.get("asset"), "dur": round(float(p.get("dur") or 3.0), 3),
                  "source": p.get("source") or "library", "why": p.get("why") or "",
                  "medium": "photo"}
            for k in ("beat", "role", "audio", "evidence", "card", "sticker", "vfx"):
                if p.get(k) is not None:
                    el[k] = p[k]
            elements.append(el)
        elif kd == "gen":
            el = gen_element(p, idx_n)
            tpl = el["template"]
            if tpl not in GEN_TEMPLATES:
                problems.append(("error", f"gen 模板「{tpl}」不认识：{'/'.join(sorted(GEN_TEMPLATES))}"))
            elif float(el["dur"]) < float((GEN_TEMPLATES.get(tpl) or {}).get("min_dur") or 0):
                problems.append(("error", f"gen#{el['n']} {tpl} 只有 {el['dur']}s，"
                                          f"模板下限 {GEN_TEMPLATES[tpl]['min_dur']}s"))
            elements.append(el)
        else:
            cel, probs = _build_clip_element(lib, index, p, idx_n, prev_out, bounds_cache)
            problems.extend(probs)
            if cel is not None:
                elements.append(cel)

    if fill:
        have = {el.get("beat") for el in elements}
        for n, title in (beat_titles or {}).items():
            if n in have:
                continue
            el = gen_element({"template": "chapter", "dur": 2.0,
                              "props": {"n": str(n).zfill(2), "title": (title or "")[:10]},
                              "why": f"论点 {n} 补章节卡",
                              "beat": n, "role": "setup"}, 0)
            elements.append(el)
            problems.append(("warning", f"论点 {n}「{title}」无画面，已补章节卡"))

    notes: list[str] = []
    if fit:
        elements, notes = fit_skeleton(lib, elements, duration_bands(skeleton, style))

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

    total = round(sum(element_dur(el) for el in elements
                      if (el.get("track") or "video") != "overlay"), 3)
    beats = []
    for n in sorted({el.get("beat") for el in elements if el.get("beat") is not None}):
        own = [el for el in elements if el.get("beat") == n]
        beats.append({"n": n, "title": (beat_titles or {}).get(n, ""),
                      "elements": [el["n"] for el in own],
                      "seconds": round(sum(element_dur(el) for el in own), 2),
                      "roles": sorted({el.get("role") for el in own if el.get("role")})})

    tl: dict = {
        "version": 5,
        "slug": None,
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": total,
        "axis": extra.get("axis") or (brief or {}).get("axis") or "A",
        "plan": extra.get("plan") or (brief or {}).get("plan") or "S1-conclude-fast",
        "pack": extra.get("pack") or (brief or {}).get("pack") or "P1-warm",
        "style_pack": style_name,
        "bgm_mood": extra.get("bgm_mood") or (brief or {}).get("bgm_mood") or "upbeat",
        "brief": ({k: brief.get(k) for k in
                   ("audience", "change", "length_s", "style_pack", "length_band")}
                  if brief else None),
        "skeleton": "skeleton.json" if skeleton else None,
        "bands": duration_bands(skeleton, style),
        "beats": beats,
        "elements": elements,
        "_notes": notes,
    }
    if extra.get("title_bar"):
        tl["title_bar"] = extra["title_bar"]
    if extra.get("stickers"):
        tl["stickers"] = extra["stickers"]

    if brief and brief.get("length_s"):
        want = float(brief["length_s"])
        diff = (total - want) / want if want else 0
        if abs(diff) > 0.15:
            problems.append(("warning", f"总时长 {total:.1f}s，目标片长 {want:.0f}s，差 {diff:+.0%}"))
    return tl, problems


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
    ap = argparse.ArgumentParser(description="将分镜/剪辑计划生成为 timeline.json 元素时间轴")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--brief", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--skeleton", default=None)
    ap.add_argument("--fill", action="store_true", help="无画面论点自动补章节卡")
    ap.add_argument("--fit-skeleton", action="store_true", help="超长口播段自动吸附字界收紧")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    ap.add_argument("--show", action="store_true", help="查看当前时间轴摘要")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else resolve_lib(proj, None)
    tl_path = proj / "timeline.json"

    if a.show:
        tl = load_json(tl_path)
        if not tl:
            raise SystemExit(f"没有 timeline.json：{tl_path}")
        print(f"{tl.get('duration_s')}s · {len(tl.get('elements') or [])} 元素 · 包 {tl.get('pack') or tl.get('style_pack')}")
        for el in tl["elements"]:
            d = element_dur(el)
            head = el.get("template") or (f"{el.get('medium')} {Path(str(el.get('asset'))).name}"
                                          if el.get("asset") else "—")
            print(f"  {el['n']:>2}. {el['kind']:<7} {d:>5.2f}s  {head}"
                  f"  {('★' + el['role']) if el.get('role') else ''}")
        return 0

    brief = load_json(Path(a.brief) if a.brief else proj / "brief.json")
    style_name, style = resolve_style(proj, a.style, brief)
    skeleton = load_json(Path(a.skeleton) if a.skeleton else proj / "skeleton.json")
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

    print(f"时间轴 {tl['duration_s']}s · {len(tl['elements'])} 元素 · 包装 {tl['pack']}"
          f" · 节奏中位 {tl['bands']['median']:.2f}s")
    for el in tl["elements"]:
        d = element_dur(el)
        flag = "·" if el["kind"] == "clip" and el.get("role") in ("claim", "warrant") else " "
        head = el.get("template") or (Path(str(el.get("asset"))).stem[-24:] if el.get("asset") else "—")
        print(f" {flag}{el['n']:>2}. {el['kind']:<7} {d:>5.2f}s  {head}")
    for lv, m in problems:
        print(f"  {'✗' if lv == 'error' else '!'} {m}")

    if not a.dry_run:
        tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {tl_path}")
    print(f"校验：error {len(errs)} · warning {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
