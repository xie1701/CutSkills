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
import itertools
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
    except Exception as e:                     # noqa: BLE001
        # 以前这里是 `style = {}`：风格包在、但读不出来（yaml 缺失 / 语法坏）时静默退回
        # 空字典，于是 `duration_bands` 用默认中位 2.4s，节奏档、字号、字幕口径全部悄悄换掉，
        # 屏幕上只说「时间轴 24s」。量不到 ≠ 默认值 —— 读不出来就停，不做没把握的片子。
        raise SystemExit(f"风格包读不出来：{f}（{type(e).__name__}: {e}）——"
                         f"先修好它，别拿默认节奏档顶班") from e
    if not isinstance(style, dict):
        raise SystemExit(f"风格包不是键值表：{f}（读到 {type(style).__name__}）")
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


# ---------------------------------------------------------------- 开场两变（BRIEF 剪辑表第 4 条）
# BRIEF 原话：**前 3 秒至少 2 次画面变化，每次变化对应一个音频事件**。
# 判据在 `verify_film.first_seconds_verdict`（红线，不许动），产物得在这里就成立——
# 渲完再补等于让「首镜 5 秒」一路走到验收才被打回（实测 8/8 支片首镜 3.17–5.79s）。
#
# 判据的事件表来自 `sync_check.element_starts`，只认**元素起点**（硬切）与
# overlay 生成物入场。MG 卡/大字在本仓是挂在元素上的 `card` 字段（渲器从
# `element.card` 画），不是独立元素，所以它再显眼也不进事件表；进表的是
# `track == "overlay"` 的 gen 元素（钩子卡 / 标题条 / 大字）。
#
# 三种形态按优先级取（2026-11-15 改：切句内降为最后手段）：
#   ① 现成 overlay 挪位——把已经在轴上的那张卡/大字挪到前 3 秒、踩在音频事件上。
#      **一个刀口都不开**：字幕条数与中位必须和「没做过开场两变」时一模一样。
#      挪位守两条：卡不能早于它锚定的词（`sync_check.PASS_LAG_MS`：声音先到、卡跟），
#      挪完卡片覆盖的窗口还得罩住那个词（卡还在说这个词）。
#      优先用现成的，不新造元素——新造一张要现场写文案、过卡准入与品牌安全，
#      那是编辑拍的板，产线不替人编。
#   ② 真静音气口切镜——切点落在 `_breath_cuts_of` 量出来的**实量静音**后面那个字界上，
#      新元素第一块与原来那口气的边界重合，字幕分组不被劈开。
#   ③ 切字界——最后手段，必须过 `_subtitle_damage` 的所有闸（闪帧 / 碎条 / 助词孤行）。
#
# Audio-First 的硬约束（缺一条就不许切，宁可报错也不静默长镜）：
#   ① 切点落在字界上（`audio_map.chars_of` 的字起点）——新元素第一块必然从这一字起，
#      所以它同时就是 audio-map 的**块边界**，与判据的 ±80ms 口径天然 0 偏差，
#      不是「切完再算偏差碰运气」；
#   ② 新元素的第一块不能是填充词（嗯/啊/那个）——填充词不进事件表，切在那儿等于没切；
#   ③ 切点要躲开晃动帧（`stability.motion_at`：这一秒人是不是在转头），
#      并在目标时刻附近挑**最稳**的那一刀；字间停顿（气口）优先。
OPENING_WIN = 3.0             # 与 verify_film.FIRST_SEC_S 同一个数
OPENING_NEED = 2              # 与 verify_film.FIRST_SEC_MIN_CHANGES 同一个数
OPENING_TOL = 0.08            # 与 sync_check.TOL_MS 同一个数（80ms）
OPENING_MIN_SHOT = 0.6        # 切点两侧各留这么长，再短读作闪帧
OPENING_TARGETS = (1.3, 2.4)  # 两刀想落的地方：第一变要早，第二变跟上
OPENING_LEAD = 1.5            # 开场静帧裁到口播最多这会儿进场，后面才塞得下第二变
OPENING_PAUSE = 0.12          # 字间停顿 ≥ 这个数算「气口」，优先在这儿切
OPENING_GATE = 0.9            # 目标时刻 ±这么宽里找候选（再远就不是「开场」了）
OPENING_SHAKE_SLACK = 1.5     # 晃动值比中位高出这么多 → 这一帧是「人在动」，否掉
OPENING_TRY = 6               # 一个刀位把字幕切成闪帧就换下一个：最多试几个
OPENING_LAG = 0.12            # 与 sync_check.PASS_LAG_MS 同一个数：卡只许晚于它锚定的词
OPENING_MIN_GAP = 0.5         # 两次画面变化之间至少隔这么久（同一帧上叠两张卡只读作一次）

# 开场变化的三种形态，按优先级从高到低。见文件顶部那段注释。
OPENING_OVERLAY = "overlay"   # ① 现成卡/大字挪位（一个刀口都不开）
OPENING_GAP = "gap"           # ② 真静音气口切镜（不破坏字幕分组）
OPENING_CHAR = "char"         # ③ 切字界（过 `_subtitle_damage` 全部闸才用）

# ---------------------------------------------------------------- 量不到 ≠ 通过
# 本文件里每一处「探针量不出来」都必须留痕，不许静默变成绿灯。
# 2026-11-15 的假绿就是这个形状：`_subtitle_damage` 把 `subs.build` 的异常吞了，
# 于是「这一刀会不会切碎字幕」这道闸在 subs.py 抛错（`subs.py:939 NameError`）时
# **静默放行**——闸还在纸上、实际等于不存在。
# 两类处理，不含糊：
#   · 闸（判「能/不能」的那一类）→ 抛错就判**不通过**，把片子判红让人来查（`BLIND_SUBTITLE`）；
#   · 只影响排序/打分的探针（气口、词边界、晃动）→ 记进 `_BLIND`，收口成 warning 打在
#     方案输出里，写明「这几处判据没真正生效」。少一道否决可以，假装量过了不行。
BLIND_SUBTITLE = "字幕量不出来"
_BLIND: dict[str, int] = {}


def _blind(what: str) -> None:
    """记一次「这条判据没量出来」（`_blind_notes` 收口成 warning）。"""
    _BLIND[what] = _BLIND.get(what, 0) + 1


def _blind_notes() -> list[tuple[str, str]]:
    """把这一轮量不出来的判据报成 warning（不报 error：它们只是让排序少一层，没判死谁）。"""
    return [("warning", f"！{w}有 {n} 处量不出来：这几处判据没真正生效，不等于通过")
            for w, n in sorted(_BLIND.items())]


def _film_spans(elements: list[dict]) -> list[tuple[int, float, float]]:
    """video 轨元素的 (元素下标, 片头秒, 时长)。overlay 不占时间轴。"""
    out: list[tuple[int, float, float]] = []
    t = 0.0
    for i, el in enumerate(elements):
        if (el.get("track") or "video") == "overlay":
            continue
        d = element_dur(el)
        out.append((i, round(t, 3), d))
        t = round(t + d, 3)
    return out


def _change_times(elements: list[dict]) -> list[float]:
    """画面变化时刻：硬切（新元素起）+ overlay 生成物入场（at）。

    口径就是 `sync_check.element_starts` —— 判据用哪张表，产线就用哪张表，
    不在这儿另写一份（两份表迟早漂，本仓为此踩过多次假绿）。
    """
    import sync_check as _sc
    return [float(e["t"]) for e in _sc.element_starts({"elements": elements})]


def _audio_window(el: dict) -> tuple[str | None, float, float]:
    """这一格的声音窗口（素材, in, out）。没有就返回 (None, 0, 0)。"""
    au = el.get("audio") or {}
    aid = au.get("asset") or el.get("asset")
    a0, a1 = au.get("in"), au.get("out")
    if aid is None or a0 is None or a1 is None:
        return None, 0.0, 0.0
    return str(aid), float(a0), float(a1)


def _el_chars(el: dict, chars_of) -> list[dict]:
    """这一格自己的字（源时间）。取法照 `audio_map.build`：`[a0, a0+元素时长)`。

    用元素时长而不是 audio.out-in：B-roll 借用别人声音时两者不等长，
    audio_map 取的是元素时长，这里跟它一致，否则块表对不上。
    """
    aid, a0, _ = _audio_window(el)
    if not aid:
        return []
    d = element_dur(el)
    return [c for c in chars_of(aid) if a0 - 0.02 <= float(c["s"]) < a0 + d - 0.02]


# `audio_map` 的切块口径：段函数 / 填充词表 / 标点表都从那边引，不在这儿另写一份。
_AUDIO_SEGMENT = None
_FILLERS: frozenset[str] = frozenset()
_FILLERS_STRIP = ""


def _load_audio_map_engine() -> None:
    """延迟绑定 `audio_map` 的切块引擎（模块级 import 会让 timeline 先吃它的依赖）。"""
    global _AUDIO_SEGMENT, _FILLERS, _FILLERS_STRIP
    if _AUDIO_SEGMENT is None:
        import audio_map as _am
        _AUDIO_SEGMENT = _am.segment
        _FILLERS = frozenset(_am.FILLERS)
        _FILLERS_STRIP = _am.PUNCT


def _block_bounds(elements: list[dict], chars_of) -> list[float]:
    """`audio-map.json` 会写出来的块边界（块首与块尾）。

    切块本身走 `audio_map.segment` / `FILLERS` / `PUNCT`（同一份实现），
    这里只把「逐元素累加成片时间」那 8 行走一遍——与 `audio_map.build` 同形，
    这样渲前就能算出判据将看到的边界，不必等 audio-map 落盘。
    """
    import audio_map as _am
    _load_audio_map_engine()
    out: set[float] = set()
    t = 0.0
    for el in elements:
        if (el.get("track") or "video") == "overlay":
            continue
        d = element_dur(el)
        aid, a0, _ = _audio_window(el)
        if el.get("kind") in ("clip", "gen") and aid and el.get("audio"):
            for blk in _AUDIO_SEGMENT(_el_chars(el, chars_of)):
                txt = str(blk["text"]).strip(_am.PUNCT + " ")
                if not txt or txt in _am.FILLERS:
                    continue
                b0 = round(t + (blk["start"] - a0), 3)
                out.add(b0)
                out.add(round(b0 + (blk["end"] - blk["start"]), 3))
        t = round(t + d, 3)
    return sorted(out)


def _first_block_text(chars: list[dict], i: int) -> str:
    """从第 `i` 个字起会切出哪一块（切在这里时，新元素的第一块就是它）。"""
    seg = _AUDIO_SEGMENT(chars[i:])
    return str(seg[0]["text"]).strip(_FILLERS_STRIP + " ") if seg else ""


def _evidence_of(chars: list[dict], a0: float, a1: float) -> str:
    """这一格说了哪几个字。与 `_build_clip_element` 的取字口径一致。"""
    return "".join(str(c["c"]) for c in chars if a0 - 0.02 <= float(c["s"]) < a1 - 0.02)


def _anchor_src(chars: list[dict], a0: float, a1: float, on: str) -> float | None:
    """卡上的锚词（`card.on`）在这一格里的源起点。找不到返回 None。"""
    seq = [c for c in chars if a0 - 0.02 <= float(c["s"]) < a1 - 0.02]
    j = "".join(str(c["c"]) for c in seq).find(on)
    return float(seq[j]["s"]) if j >= 0 else None


def _breath_cuts_of(chars: list[dict], lib: Path | None, aid: str | None,
                    a0: float, a1: float) -> set[int]:
    """这一格里**真的**换了一口气的字界（第 j 个字之前）。量声音，不信 ASR 词起点间隔。

    为什么不能只看 `chars[j].s - chars[j-1].e`：ASR 会把一处停顿记进前一个字的时长里，
    字与字的起点于是首尾相接、gap 全是 0，那口气在字表上根本看不见。
    T-032 已经在字幕层踩过这个坑（`subs.retime_off_silence`）；这里**同一份探针**：
    `subs.speech_silences` 量真静音 → 把字的起止从静音里拉出来 → `subs.breath_cuts`。

    不这么做的后果是开场切口退化成「离目标最近的字界」，一刀切在句子中间：
    2026-11-15 自测样片实测切在「国产和进口的 | 材料差的」——字幕的呼吸组被劈成
    6+4 两段（那一句本来是一条 8 单位的「国产和进口的材料」），全片字幕宽度中位
    从 7.0 掉到 6.0。真实气口在 1.70–2.04s，「差」这个字界就在它后面。

    量不成（素材不在本机 / 没有 ffmpeg / 没给 lib）返回空集：切点按老口径判，不把片判死。
    """
    if lib is None or not aid or not chars:
        return set()
    try:
        import subs as _subs                       # 探针只有一份，在字幕层
        runs = _subs.speech_silences(Path(lib) / str(aid), a0, a1)
        if not runs:
            return set()
        items = [(str(c["c"]), float(c["s"]), float(c["e"])) for c in chars]
        return _subs.breath_cuts(_subs.retime_off_silence(items, runs))
    except Exception:                              # noqa: BLE001 量不到不当拦路虎
        _blind("静音探针（气口）")                   # 留痕：切口会退回「离目标最近的字界」，
        return set()                               # 可能切在句子中间，不是真换气的地方


def _cut_word_ok(chars: list[dict], j: int) -> bool:
    """在第 `j` 个字前面切，是不是落在**词与词之间**（subs 规则②：不切词）。

    不拆成硬过滤：这一格可能整句就是一个词（或者没装分词器），硬过滤会把候选清空，
    于是「开场两变」直接报 error——为了字幕静音条把一个 BRIEF 硬要求干死，不值。
    所以它只进排序（下面 `ranked`）：词边界优先，实在没有才退到字中间。
    """
    if j <= 0 or j >= len(chars):
        return True
    try:
        import subs as _subs
        txt = "".join(str(c["c"]) for c in chars)
        wb = _subs.word_bounds(txt)
        return (not wb) or j in wb or str(chars[j]["c"]) in _subs.PUNCT
    except Exception:                       # noqa: BLE001 量不到不把刀位判死
        _blind("词边界探针")                     # 「不拆词」这一层排序失效，留痕别装没事
        return True


def _opening_candidates(elements: list[dict], chars_of, motion, win: float,
                        lib: Path | None = None) -> list[dict]:
    """窗口内所有**合法**的字界切点：非填充块首、两侧各留 OPENING_MIN_SHOT、画面稳。"""
    out: list[dict] = []
    for i, t0, d in _film_spans(elements):
        el = elements[i]
        if t0 >= win - OPENING_MIN_SHOT:
            continue
        if el.get("kind") != "clip" or not el.get("audio"):
            continue
        aid, a0, a1 = _audio_window(el)
        chars = _el_chars(el, chars_of)
        breaths = _breath_cuts_of(chars, lib, aid, a0, a1)
        prev_end = None
        prev_c = ""
        for j, c in enumerate(chars):
            s = float(c["s"])
            gap = 0.0 if prev_end is None else s - prev_end
            # 气口 = 字间停顿够长，或上一字是标点——这恰好就是 audio_map 切块的两个条件，
            # 所以切在这儿的不只是「块边界」，还是原来那口气本身的边界（听起来不悵）。
            # 字表上的 gap 靠不住（见 `_breath_cuts_of`），所以加上**实量静音**这一路：
            # 气口优先的排序（下面 `ranked` 的 0/1）没有真读数就是空话。
            breath = gap >= OPENING_PAUSE or prev_c in _FILLERS_STRIP or j in breaths
            prev_end, prev_c = float(c["e"]), str(c["c"])
            rel = round(s - a0, 3)                      # 这一字在本格里的偏移
            t_film = round(t0 + rel, 3)
            if t_film <= 1e-6 or t_film > win + 1e-6:
                continue
            if rel < OPENING_MIN_SHOT or d - rel < OPENING_MIN_SHOT:
                continue                            # 闪帧：这一刀太贴边
            if not _first_block_text(chars, j):
                continue
            if _first_block_text(chars, j) in _FILLERS:
                continue                            # 填充词不进事件表，切在这儿等于没切
            out.append({"idx": i, "src": round(s, 3), "t": t_film,
                        "pause": breath, "gap": j in breaths,
                        "word_ok": _cut_word_ok(chars, j),
                        "motion": float(motion(aid, s)) if motion else 0.0,
                        "piece": (t0, t0 + d)})
    return out


def _split_element(el: dict, cut_src: float, *, k: int, t_film: float,
                   chars: list[dict]) -> tuple[dict, dict]:
    """在源时间 `cut_src` 把一格切成两格：声音连续（首尾相接，不重不漏），画面换景别。"""
    au = dict(el.get("audio") or {})
    a0, a1 = float(au.get("in") or el.get("in") or 0.0), float(au.get("out") or el.get("out") or 0.0)
    first, second = dict(el), dict(el)
    first["out"] = round(cut_src, 3)
    second["in"] = round(cut_src, 3)
    if au:
        first["audio"] = {**au, "out": round(cut_src, 3)}
        second["audio"] = {**au, "in": round(cut_src, 3)}
    # 台词跟着各自的窗口走：不重算 evidence，「这一格说了什么」就会与声音对不上
    # （`verify_film` 的「字幕文字对得上声音」按 evidence 对账）。
    first["evidence"] = _evidence_of(chars, a0, cut_src)
    second["evidence"] = _evidence_of(chars, cut_src, a1)
    first["chars"] = len(first["evidence"])
    second["chars"] = len(second["evidence"])
    orig = str(el.get("why") or "")
    second["why"] = (f"开场第 {k} 变：{t_film:.2f}s 在字界切一刀换景别"
                     f"（原镜：{orig[:40] or '—'}）")
    # 卡/贴纸跟着它自己的锚词走：锚词在哪一片，卡就挂哪一片。
    card = dict(el.get("card") or {})
    if card:
        on = str(card.get("on") or "").strip()
        at = _anchor_src(chars, a0, a1, on) if on else None
        if at is not None and at >= cut_src:
            first.pop("card", None)              # 锚词在后半段 → 卡跟过去
        else:
            second.pop("card", None)
    return first, second


def _subtitle_damage(elements: list[dict], lib: Path | None,
                     *, win: float = OPENING_WIN) -> list[str]:
    """这一刀把开场字幕打坏了没有：闪帧（<0.8s / >3.2s）、碎条、助词孤行。

    三个都是**已有判据**（`verify_film` 的字幕停留、`check_subs` 的宽度中位、
    `check_subs.dangling_judge` 的孤行）：开场为了凑两个变化点而把字幕切成“闪一下”、
    或者把一个呼吸组劈成两条碎条，就是把一个问题换成另一个问题。
    所以刀位选定前先用字幕层自己的口径验一遍。

    碎条的口径（2026-11-15）：一条字幕的展示宽度低于 `subs.MIN_UNITS`——那是
    `subs.regroup` 自己「太短的并进邻居」的门槛。一个组落在门槛下还活下来，
    只可能因为**元素墙**把它圈住了：这不是「这句本来就短」，这是切镜扒下来的碎片。
    自测样片实测：一刀切在「国产和进口的|材料差的」之后，全片字幕宽度中位
    从 7.0 掉到 6.0（判据下限 6.5），而闪帧/孤行两条全是绿的。

    只算开场前后那几格（字幕分组是逐格重算的，后面几格影响不到前面）。
    overlay 生成物不占时间轴、也不是字幕的来源，所以直接跳过（不筛掉的话
    `subs.build` 拿不到 `in`/`out` 会抛异常，这一道闸就静默失效）。
    分组与虚字表全部走 `subs.build` / `subs.NO_HEAD` / `subs.word_bounds`，不另写一套——
    本仓反复踩的坑就是「渲器一份、判据一份」，两份迟早漂。

    **量不到 ≠ 通过（2026-11-15 假绿）**：以前这里 `except Exception: return []`，
    `subs.build` 一旦抛错（真实发生过一次 `subs.py:939` 的 `NameError`），这道闸就
    静默放行——等于不存在。现在反过来：量不出来就返回 `BLIND_SUBTITLE` 开头的一条，
    调用方 `continue` 掉这个刀位、最后报 error。宁可这条片出不来让人来查，也不许它
    拿“没量到”当“没伤到”。

    `lib is None` 不算量不到：没有素材库就没有字幕层（也没字表可量），这一条判据不适用，
    不是“量了失败”。真实生产路径（`build_timeline` / `--opening`）一定会把 lib 传进来。
    """
    if lib is None:
        return []                           # 没有字幕层可伤（不是量不到），见上
    try:
        import subs as _subs
        from evidence_guard import load_chars
        index = {x["id"]: x for x in json.loads(
            (Path(lib) / "_cutskills" / "index.json").read_text())["assets"]}
        cache: dict = {}
        segs, t = [], 0.0
        for i, el in enumerate(elements, 1):
            if (el.get("track") or "video") == "overlay":
                continue            # 卡不占时间轴，也不是字幕的来源（不筛掉会让 subs 拿不到 in/out）
            d = element_dur(el)
            # 元素号必须重编：同一个源元素切出来的几格共用旧号时，下面的字幕短条
            # 合并会把两格拼成一条（它按 `el` 判断同不同格），量出来的读数就是假的。
            segs.append({"n": i, "asset": el.get("asset"),
                         "in": el.get("in"), "out": el.get("out"),
                         "audio": el.get("audio"), "evidence": el.get("evidence"),
                         "_start": round(t, 3), "_dur_q": d})
            t = round(t + d, 3)
            if t > win + 1.5:
                break                       # 开场之后的字幕与这一刀无关
        out: list[str] = []
        for c in _subs.build(segs, index, cache, Path(lib), load_chars, colorize=False):
            txt = str(c.get("text") or "")
            d = float(c["end"]) - float(c["start"])
            if float(c["start"]) <= win and not (0.8 - 1e-6 <= d <= 3.2 + 1e-6):
                out.append(f"「{txt[:10]}」{d:.2f}s")
            if float(c["start"]) <= win \
                    and float(c.get("units") or 0) < float(_subs.MIN_UNITS) - 1e-6:
                out.append(f"「{txt[:10]}」{float(c.get('units') or 0):g} 单位（碎条）")
            if not txt:
                continue
            # 助词孤行（与 check_subs.dangling_judge 同一条）：条首落在虚字上，
            # 而且这个字在分词里自成一段（词边界落在第 1 字之后）才算甩出来的尾巴。
            wb = _subs.word_bounds(txt) if len(txt) > 1 else {1}
            if txt[0] in _subs.NO_HEAD and txt[0] not in set("过着咁") and 1 in wb:
                out.append(f"「{txt[:10]}」起头是「{txt[0]}」")
            elif len(txt) >= 2 and txt[-1] in _subs.NO_END \
                    and str(c.get("break_after") or "") != "clause":
                out.append(f"「{txt[:10]}」收尾是「{txt[-1]}」")
        return out
    except Exception as e:                  # noqa: BLE001
        # 量不到 ≠ 通过：这一刀到底会不会切碎字幕，**没量出来就无法判断**，所以拒绝。
        # 报错要把「为什么拒绝」写清楚，不然下一个人只会看到「开场补不出合法变化」。
        _blind(f"{BLIND_SUBTITLE}（无法判断这一刀会不会切碎字幕）")
        return [f"{BLIND_SUBTITLE}（{type(e).__name__}: {e}）："
                f"无法判断这一刀会不会切碎字幕，所以拒绝"]


def _is_blind(dmg: list[str]) -> bool:
    """这份字幕损伤读数是不是「根本没量出来」（这种不许当成「伤得轻」拿去兜底）。"""
    return any(str(d).startswith(BLIND_SUBTITLE) for d in dmg)


def _chars_of_default(lib: Path | None):
    """`audio_map.chars_of` 同一个口径的取字函数（按素材缓存一次）。"""
    _cache: dict[str, list[dict]] = {}

    def chars_of(aid: str) -> list[dict]:
        if aid not in _cache:
            from audio_map import chars_of as _co                 # 同一口径，不另写
            _cache[aid] = _co(lib, aid, None) if lib else []
        return _cache[aid]

    return chars_of


def _word_from(elements: list[dict], chars_of, t: float) -> dict | None:
    """片头 `t` 秒之后（含 `t`）说的第一个字，起止换成片头时间。取不到返回 None。"""
    for i, t0, d in _film_spans(elements):
        if t > t0 + d + 1e-6:
            continue
        el = elements[i]
        aid, a0, _a1 = _audio_window(el)
        if not aid:
            continue
        for c in _el_chars(el, chars_of):
            s, e = float(c["s"]) - a0, float(c["e"]) - a0
            if s + 1e-6 >= t - t0:
                return {"s": round(t0 + s, 3), "e": round(t0 + e, 3)}
    return None


def _overlay_moves(elements: list[dict], chars_of, bounds: list[float],
                  win: float) -> list[dict]:
    """已经在轴上的 overlay 生成物能挪到哪些音频事件上（好用的在前）。

    只挪**已经存在的**卡：一张卡就是一个画面变化点，而新造一张要现场写文案、
    过卡准入与品牌安全——那是编辑拍的板，产线不替人编。所以没有卡时这里返回空，
    由下游改走「气口切镜 / 切字界」，或在两条都不合法时显式报错。

    两条硬约束（`T-020` 观感反馈：卡不能抢拍、不能扫过自己要说的词）：
      ① 卡不早于它锚定的词：入场时刻到「接下来那个字的起点」不得超过
         `sync_check.PASS_LAG_MS`（声音先到，卡跟）；落在气口中间（离下一个字
         几十上百毫秒）就是抢拍，不作靶位；
      ② 挪完卡片的窗口 [入场, 入场+时长] 得罩住那个词（卡还在说这个词）。
    """
    out: list[dict] = []
    for i, el in enumerate(elements):
        if el.get("kind") != "gen" or (el.get("track") or "video") != "overlay":
            continue
        dur = element_dur(el)
        if dur <= 0:
            continue
        cur = round(float(el.get("at") or 0.0), 3)
        if 1e-6 < cur <= win + 1e-6 \
                and any(abs(b - cur) <= OPENING_TOL + 1e-6 for b in bounds):
            continue                      # 已经踩在事件上、已经在数：别再挪它（幂等）
        for b in bounds:
            if b <= 1e-6 or b > win + 1e-6 or abs(b - cur) <= 1e-6:
                continue
            word = _word_from(elements, chars_of, b)
            if word is None:
                continue
            if word["s"] - b > OPENING_LAG + 1e-6:
                continue                  # 声音还没到卡就到 = 抢拍（PASS_LAG_MS）
            if b + dur < word["e"] - 1e-6:
                continue                  # 卡先收了，这一词还没说完（卡不再说这个词）
            nxt = min((x for x in bounds if x > b + 1e-6), default=None)
            cover = 1 if (nxt is not None and b + dur < nxt - 1e-6) else 0
            out.append({"idx": i, "t": b, "cover": cover,
                        "dist": round(min(abs(b - x) for x in OPENING_TARGETS), 3)})
    # 先要「整口气都被卡罩住」（卡不会说到一半就收），再要离目标时刻近
    out.sort(key=lambda m: (m["cover"], m["dist"], m["t"]))
    return out


def _move_overlays(elements: list[dict], chars_of, win: float, n: int,
                   *, taken: list[float] | tuple = ()) -> tuple[list[dict], list[str]] | None:
    """把 `n` 张现成的卡挪进开场、踩在音频事件上。凑不够 `n` 张返回 None。

    一个刀口都不开：字幕层拿到的还是同一根轴（没有元素墙），条数/中位不变。
    """
    bounds = _block_bounds(elements, chars_of)
    moves = _overlay_moves(elements, chars_of, bounds, win)
    picked: list[dict] = []
    used_i: set[int] = set()
    used_t: list[float] = list(taken)
    for m in moves:
        if len(picked) >= n:
            break
        if m["idx"] in used_i:
            continue
        if any(abs(m["t"] - t) < OPENING_MIN_GAP for t in used_t):
            continue                      # 两张卡叠在同一帧上只读作一次变化
        picked.append(m)
        used_i.add(m["idx"])
        used_t.append(m["t"])
    if len(picked) < n:
        return None
    els = [dict(e) for e in elements]
    notes: list[str] = []
    for m in picked:
        ov = els[m["idx"]]
        old = round(float(ov.get("at") or 0.0), 3)
        ov["at"] = round(m["t"], 3)
        label = str(ov.get("template") or "卡")
        notes.append(f"开场变化：现成的「{label}」入场 {old:.2f}s → {m['t']:.2f}s"
                     f"（踩在音频事件上，不切句）")
    return els, notes


def _repair_overlays(elements: list[dict], chars_of,
                    win: float) -> tuple[list[dict], list[str]]:
    """前 `win` 秒已经入场、但没踩在音频事件上的卡：就地挪到最近的事件上。

    只动 overlay：硬切是编辑的板，不替人改（那条仍按原口径报 error）。
    已经踩在事件上的卡不动——这是幂等的关键（跑第二遍不会重复挪）。
    """
    els = [dict(e) for e in elements]
    notes: list[str] = []
    bounds = _block_bounds(els, chars_of)
    for i, el in enumerate(els):
        if (el.get("track") or "video") != "overlay":
            continue
        cur = round(float(el.get("at") or 0.0), 3)
        if not (1e-6 < cur <= win + 1e-6):
            continue
        if any(abs(b - cur) <= OPENING_TOL + 1e-6 for b in bounds):
            continue
        near = [m for m in _overlay_moves(els, chars_of, bounds, win) if m["idx"] == i]
        if not near:
            continue
        m = min(near, key=lambda x: abs(x["t"] - cur))
        el["at"] = round(m["t"], 3)
        notes.append(f"开场卡入场 {cur:.2f}s → {m['t']:.2f}s（原来没踩在音频事件上）")
    return els, notes


def _opening_levels(missing: int) -> list[tuple[str, ...]]:
    """给 `missing` 个缺口排形态组合：现成卡 优先，其后真静音气口，字界兜底。

    按「总代价」排：`OPENING_OVERLAY` 算 0、气口 1、字界 2。
    **带卡的组合里不许再切字界**（排到最后）：卡已经能把画面变化补上，再切句内
    就是白伤一条字幕——走优先级①的轴，字幕条数与中位必须和没做过开场两变时一模一样。
    真到了「不切就凑不齐」的角落才允许破这一步（卡 + 一刀），总比无片好。
    """
    lv = (OPENING_OVERLAY, OPENING_GAP, OPENING_CHAR)
    rows = []
    for seq in itertools.product(range(len(lv)), repeat=max(0, min(missing, 3))):
        names = tuple(lv[i] for i in seq)
        mixed = 1 if (OPENING_OVERLAY in names and OPENING_CHAR in names) else 0
        rows.append((mixed, sum(seq), seq, names))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return [n for _m, _s, _q, n in rows]


def _cut_one(elements: list[dict], level: str, target: float, k: int, *, chars_of,
             motion, lib: Path | None, win: float
             ) -> tuple[list[dict], list[str]] | None:
    """按 `level` 切一刀；这一级没有一处过得了字幕闸的刀位就返回 None。"""
    cands = _opening_candidates(elements, chars_of, motion, win, lib=lib)
    if level == OPENING_GAP:
        cands = [c for c in cands if c.get("gap")]     # ① 只认实量静音后面那个字界
        if not cands:
            return None
    if not cands:
        return None
    near = [c for c in cands if abs(c["t"] - target) <= OPENING_GATE] or cands
    # 晃动用**否决**而不是排序：人转头的半秒里，离目标再近也不能切（切进去是一张
    # 中间帧，观众读作「这人在抽搐」）；平得差不多时仍按目标时刻选，不靠晃动微差抳。
    med = sorted(c["motion"] for c in near)[len(near) // 2]
    pool = [c for c in near if c["motion"] <= med + OPENING_SHAKE_SLACK] or near
    ranked = sorted(pool, key=lambda c: (0 if c.get("word_ok", True) else 1,
                                        round(abs(c["t"] - target), 1),
                                        0 if c.get("gap") else 1,
                                        round(c["motion"], 1)))
    for cand in ranked[:OPENING_TRY]:
        i0 = cand["idx"]
        el0 = elements[i0]
        aid0, _a0, _a1 = _audio_window(el0)
        first, second = _split_element(el0, cand["src"], k=k, t_film=cand["t"],
                                      chars=chars_of(aid0))
        trial = elements[:i0] + [first, second] + elements[i0 + 1:]
        # 字幕是刻在切点上的下游：切开后如果字幕被劈成碎条/闪一下/助词孤行，
        # 那不是本项判据，但为它破一条别的硬线不值。所以先拿字幕层自己的口径验一遍。
        if _subtitle_damage(trial, lib):
            continue
        notes = [f"开场第 {k} 变：{cand['t']:.2f}s 切一刀"
                 f"（{'气口' if cand.get('gap') else '字界'}·晃动 {cand['motion']:.1f}）"]
        if el0.get("card"):
            # 切完卡的“引用范围”变窄了：它原来盖着整句，现在只盖某一半。
            # check_cards 要求卡上的每个数字都出自**它自己那一格**的话，所以把利害说出来，
            # 不替编辑做决定（改卡文、换格、换素材都是人拍的板）。
            home = first if first.get("card") else second
            notes.append(
                f"！卡「{str((el0['card'].get('text') or '')[:8])}」切后只盖「{home.get('evidence', '')[:12]}」"
                f"（原句「{str(el0.get('evidence') or '')[:18]}」）—— 卡上的数字/说法必须出自这一格")
        return trial, notes
    return None


def add_opening_changes(elements: list[dict], *, chars_of=None, motion=None,
                        lib: Path | None = None, proj: Path | None = None,
                        win: float = OPENING_WIN, need: int = OPENING_NEED
                        ) -> tuple[list[dict], list[str], list[tuple[str, str]]]:
    """让前 `win` 秒真的站起来两次。返回 (元素, notes, problems)。

    `chars_of(aid) -> [{c,s,e}]` 与 `motion(aid, src_t) -> float` 可注入（单测用），
    默认从素材库的 ASR 字表读、从 `stability.curve` 量。

    给不出两个变化点时**返回 error**：宁可这条片出不来，也不许静默退回「首镜 5 秒」
    ——那正是判据 8/8 全红的原因。

    外壳：本函数负责清空并收口「量不到」账本（`_BLIND`）——气口/词边界/晃动
    这几条探针一旦量不出来，就以 warning 形式跟着 notes 一起回。少了否决可以，
    假装量过了不行（T-0xx 门禁假绿）。
    """
    _BLIND.clear()
    els, notes, problems = _add_opening_changes(elements, chars_of=chars_of, motion=motion,
                                               lib=lib, proj=proj, win=win, need=need)
    problems = problems + _blind_notes()
    return els, notes, problems


def _add_opening_changes(elements: list[dict], *, chars_of=None, motion=None,
                         lib: Path | None = None, proj: Path | None = None,
                         win: float = OPENING_WIN, need: int = OPENING_NEED
                         ) -> tuple[list[dict], list[str], list[tuple[str, str]]]:
    """`add_opening_changes` 的本体（账本收口在外壳，这里只管切）。"""
    notes: list[str] = []
    problems: list[tuple[str, str]] = []
    els = [dict(e) for e in elements]

    def changes(xs: list[dict]) -> list[float]:
        return [t for t in _change_times(xs) if 1e-6 < t <= win + 1e-6]

    def aligned(times: list[float],
                bounds: list[float]) -> tuple[list[float], list[tuple[float, float]]]:
        """哪些变化点踩在块边界上（±OPENING_TOL），哪些没踩上。"""
        ok: list[float] = []
        bad: list[tuple[float, float]] = []
        for t in times:
            dev = min((abs(b - t) for b in bounds), default=9.9)
            if dev <= OPENING_TOL + 1e-6:
                ok.append(t)
            else:
                bad.append((t, dev))
        return ok, bad

    if chars_of is None:
        chars_of = _chars_of_default(lib)

    if motion is None:
        _stab: dict[str, list[float]] = {}

        def motion(aid: str, src_t: float) -> float:                 # noqa: F811
            """这一秒有多动（0 = 站定了）。量不到就当平稳——不让缺缓存把片判死。"""
            if lib is None or proj is None:
                return 0.0
            if aid not in _stab:
                try:
                    import stability as _st
                    sp = Path(lib) / aid
                    _stab[aid] = (_st.curve(sp, Path(proj) / "hf" / "work")
                                  if sp.exists() else [])
                except Exception:                    # noqa: BLE001 量不到就当平稳
                    _stab[aid] = []
                    _blind("晃动曲线")               # 「晃动否决」缺席 = 可能切进转头帧
            cur = _stab[aid]
            if not cur:
                return 0.0
            import stability as _st
            return float(_st.motion_at(cur, src_t))

    bounds = _block_bounds(els, chars_of)
    ok, bad = aligned(changes(els), bounds)
    if len(ok) >= need and not bad:
        notes.append("开场两变已成立：" + "、".join(f"{t:.2f}s" for t in ok))
        return els, notes, problems
    # 已经在窗口里、但没踩在音频事件上的卡：挪到事件上就好（别人的硬切不碰）
    if bad:
        els, _fix = _repair_overlays(els, chars_of, win)
        notes.extend(_fix)
        bounds = _block_bounds(els, chars_of)
        ok, bad = aligned(changes(els), bounds)
        if len(ok) >= need and not bad:
            notes.append("开场两变已成立：" + "、".join(f"{t:.2f}s" for t in ok))
            return els, notes, problems
    if bad:
        problems.append(("error",
                         "前 3 秒已有变化点不在音频事件上："
                         + "、".join(f"{t:.2f}s 差 {d * 1000:.0f}ms" for t, d in bad[:3])
                         + " —— 这一刀得挪到字界/块界，不会自动改别人定的切点"))
        if len(ok) >= need:
            return els, notes, problems

    # ① 开场静帧太长：口播进不来，窗口里就一个音频事件也没有 → 把没声音的开场镜头裁短。
    #    只裁**没有声音**的镜头（没有 audio 字段 = 这一格的画面独走，裁它不动台词）。
    spans = _film_spans(els)
    lead = next(((i, t0, d) for i, t0, d in spans
                 if (els[i].get("kind") in ("clip", "gen") and els[i].get("audio")
                     and _el_chars(els[i], chars_of))), None)
    if lead is None:
        problems.append(("error",
                         f"窗口里没有可对齐的语音块（前 {win:g}s 一格带台词的声音都没有）："
                         "没有 audio-map 事件表就切不出合规的开场两变 —— "
                         "换一格一开口就在场上的镜头（口播/同期声），别用空镜开场"))
        return els, notes, problems
    if lead[1] > win - 2 * OPENING_MIN_SHOT:
        excess = round(lead[1] - OPENING_LEAD, 3)
        for i, t0, d in spans:
            if excess <= 0:
                break
            el = els[i]
            if el.get("audio") or el.get("sticker"):
                continue                     # 有声音/挂着贴纸的不裁（贴纸时长按格算）
            take = min(excess, round(max(0.0, d - OPENING_MIN_SHOT), 3))
            if take <= 0:
                continue
            if el.get("kind") == "clip":
                el["out"] = round(float(el["out"]) - take, 3)
            else:
                el["dur"] = round(float(el.get("dur") or d) - take, 3)
            notes.append(f"开场静帧裁短 {take:.2f}s（口播提前到 {t0 + d - take:.2f}s 进场）")
            excess = round(excess - take, 3)
        if excess > 0:
            problems.append(("error",
                             f"口播要到 {lead[1]:.2f}s 才进场，开场能裁的静帧不够（还差 {excess:.2f}s）："
                             f"前 {win:g}s 只有一镜、且这一镜没有声音 —— "
                             "要么换一个开场就出声的镜头，要么把这句口播提到片头"))
            return els, notes, problems

    # ② 补变化点：现成的卡挪位 ＞ 真静音气口切镜 ＞ 切字界（见文件顶部那段注释）。
    #    形态组合按优先级排好一个个试，取第一组「一处字幕都没伤」的；一组都不合法时
    #    退到伤得最轻的那组，并把伤在哪写进 notes —— 不静默妥协，也不死成无片。
    def fill(levels: tuple[str, ...]) -> tuple[list[dict], list[str]] | None:
        cur = [dict(e) for e in els]
        out: list[str] = []
        for lv in levels:
            if lv == OPENING_OVERLAY:
                continue                  # 卡放到最后挪：切完的块表才是真块表
            okn, _b = aligned(changes(cur), _block_bounds(cur, chars_of))
            target = OPENING_TARGETS[min(len(okn), len(OPENING_TARGETS) - 1)]
            got = _cut_one(cur, lv, target, len(okn) + 1, chars_of=chars_of,
                           motion=motion, lib=lib, win=win)
            if got is None:
                return None
            cur, more = got
            out.extend(more)
        n_ov = sum(1 for x in levels if x == OPENING_OVERLAY)
        if n_ov:
            got = _move_overlays(cur, chars_of, win, n_ov, taken=changes(cur))
            if got is None:
                return None                       # 现成的卡不够凑这几变
            cur, more = got
            out.extend(more)
        return cur, out

    if need - len(ok) > 0:
        chosen = None
        soft: tuple | None = None                # 兜底：伤得最轻的那组
        for levels in _opening_levels(need - len(ok)):
            got = fill(levels)
            if got is None:
                continue
            trial, tnotes = got
            b1 = _block_bounds(trial, chars_of)
            ok1, bad1 = aligned(changes(trial), b1)
            if len(ok1) < need or bad1:
                continue                         # 凑不齐 / 还有变化点没踩上事件
            dmg = (_subtitle_damage(trial, lib)
                   if any(x != OPENING_OVERLAY for x in levels) else [])
            if _is_blind(dmg):
                # 字幕量不出来 ≠ 伤得轻：这种不能当兜底样本，直接放弃这一组形态。
                # 否则「量不到」会被当成「几乎没伤」选中，闸就又没了。
                continue
            if not dmg:
                chosen = (trial, tnotes)
                break
            score = (len(dmg), len(trial))
            if soft is None or score < soft[0]:
                soft = (score, trial, tnotes, dmg)
        if chosen is None and soft is not None:
            chosen = (soft[1], soft[2])
            for d in soft[3]:
                notes.append(f"！开场这一刀伤到字幕：{d}（开场没有现成的卡、也没有合法的气口）")
        if chosen is None:
            problems.append(("error",
                             f"前 {win:g}s 补不出合法的画面变化：开场那一镜要么已经切碎，"
                             f"要么每一刀两侧都不到 {OPENING_MIN_SHOT}s，要么切下去字幕就碎 —— "
                             "给开场加一张现成的钩子卡/大字（overlay），"
                             "或换一段更长、换一个景别的开场素材"
                             + ("（字幕闸这次量不出来：刀位全被拒在闸外，先查字幕那头的报错）"
                                if _BLIND else "")))
            return els, notes, problems
        els, _tn = chosen
        notes.extend(_tn)

    bounds = _block_bounds(els, chars_of)
    ok, bad = aligned(changes(els), bounds)
    if len(ok) < need:
        problems.append(("error",
                         f"前 {win:g}s 凑不出 {need} 次画面变化（现在 {len(ok)} 次）："
                         "开场镜头不够/声音起得太晚 —— 换一个开场就出声的镜头"))
    if bad:
        problems.append(("error",
                         "切完仍有变化点不在音频事件上："
                         + "、".join(f"{t:.2f}s 差 {d * 1000:.0f}ms" for t, d in bad[:3])))
    notes.append("开场两变：" + "、".join(f"{t:.2f}s" for t in ok))
    return els, notes, problems


# ---------------------------------------------------------------- J-cut（BRIEF 剪辑表第 4 步）
# BRIEF 原文：**句与句之间默认用 J-cut：下一句的声音先进 2–4 帧，画面再切。**
#
# 为什么只能在**渲器解耦之后**做：解耦前画面边界、声音边界、时钟推进量是同一个数
# （片段文件里 `-t dur` + `-shortest` 把音轨与画面咬死，`<video>` 的 data-start 同时
# 当声音起点）。那种渲器里「画面晚 3 帧」只能写成「上一格画面多留 3 帧」，而片段音轨
# 会因为同一道 `-t` 多出 4800 个采样（=100.0ms）。现在 `render_hf.py` 把画面层与声音层
# 拆成两条：画面 `data-start=t+lead` / `data-media-start=lead`，声音 `data-start=t` /
# `data-media-start=0`。本文件只负责**决策**：在后一句那个元素上写 `pic_lead`（整数帧）。
#
# 只在「句与句之间、后一句是口播」的切点做；B-roll 段、卡片进场处、片头片尾不做
# （`JCUT_*` 是这几道门的常量）。降级也叫决策，一定会留痕：
#   · 3 帧放不下（素材尾巴不够 / 某一镜会短于节奏档下限）→ 降到 2 帧；
#   · 2 帧也不行 → 这一刀不做，并在 notes 里写清是哪一格、卡在哪条。
JCUT_DEFAULT = 3              # 默认提前量（帧）。30fps → 100.0ms，落在 BRIEF 的 2–4 帧里
JCUT_FLOOR = 2                # 降级的底线（帧）。再少就不叫「听见下一句」了
JCUT_OPENING = OPENING_WIN    # 片头保护：与开场两变同一个窗口（那 3 秒里另有专门逻辑）
JCUT_FINALE = 2.0             # 片尾保护：最后这么多秒不切（收尾镜不许被挪）
JCUT_CARD_GUARD = 0.5         # 卡片进场保护：卡在切点 ±这么多秒内入场就不动这一刀
JCUT_CARD_LAG = 0.15          # 与 render_hf.CARD_LAG 同一个数（卡比锚词晚出场）


def _asset_dur(lib: Path | None) -> dict[str, float]:
    """素材 id → 时长。量不到就返回空表（调用方会记 `_blind`，不当「够用」）。"""
    if lib is None:
        return {}
    try:
        idx = json.loads((Path(lib) / "_cutskills" / "index.json").read_text())
    except (OSError, json.JSONDecodeError):
        _blind("J-cut 素材尾巴余量")          # 索引读不出来 = 余量量不出来，不当「够用」
        return {}
    out: dict[str, float] = {}
    for a in idx.get("assets") or []:
        try:
            out[str(a["id"])] = float(a.get("duration") or 0.0)
        except (KeyError, TypeError, ValueError):
            _blind("J-cut 素材尾巴余量")
            continue
    return out


def _card_times(elements: list[dict], spans: dict[int, tuple[float, float]],
                chars_of) -> list[float]:
    """卡片/叠加生成物**入场**在成片上的时刻（用来给切点让路）。

    卡在时间轴上只是元素的一个 `card` 字段（没有自己的 start），入场时刻是渲器算的：
    `anchor_t + CARD_LAG`。这里按同一口径估：锚词在这一格里的源起点 → 换成片头时间。
    锚词找不到就不进表（这一刀的「卡离得够不够远」就量不成，由 `_blind` 记一笔）。
    """
    out: list[float] = []
    for el in elements:
        if (el.get("track") or "video") == "overlay":
            if el.get("at") is not None:
                out.append(round(float(el["at"]), 3))
            continue
        card = el.get("card") or {}
        on = str(card.get("on") or "").strip()
        if not on:
            continue
        sp = spans.get(el.get("n"))
        aid, a0, a1 = _audio_window(el)
        if not sp or not aid:
            continue
        s = _anchor_src(chars_of(aid), a0, a1, on)
        if s is None:
            _blind("J-cut 卡片入场时刻")
            continue
        out.append(round(sp[0] + (s - a0) + JCUT_CARD_LAG, 3))
    return out


def apply_jcut(elements: list[dict], *, lib: Path | None = None, fps: float = 30.0,
               bands: dict | None = None, enabled: bool = True,
               chars_of=None) -> tuple[list[dict], list[str], list[tuple[str, str]]]:
    """在「后一句」那个元素上写 `pic_lead`（帧）：画面晚这么多帧、声音不动。

    幂等：每次先把所有元素的 `pic_lead` 清掉，再按**当前**轴重新算一遍。
    返回 (元素, notes, problems)；所有「没做」都落在 notes 里，不静默。
    """
    notes: list[str] = []
    problems: list[tuple[str, str]] = []
    _blind_before = dict(_BLIND)          # 只报本函数自己新增的「量不出来」
    for el in elements:                                    # 幂等：先清场
        el.pop("pic_lead", None)
    if not enabled:
        notes.append("J-cut：本次关掉了（--no-jcut），画面与声音同一格起点")
        return elements, notes, problems
    order = [el for el in elements if (el.get("track") or "video") != "overlay"]
    if len(order) < 3:
        notes.append("J-cut：全片不到 3 格，没有句间切点")
        return elements, notes, problems
    spans: dict[int, tuple[float, float]] = {}
    acc = 0.0
    for el in order:
        d = element_dur(el)
        spans[el.get("n")] = (round(acc, 3), round(acc + d, 3))
        acc = round(acc + d, 3)
    total = acc
    dur_a = _asset_dur(lib)
    if not dur_a:
        _blind("J-cut 素材尾巴余量")
    chars_of = chars_of or _chars_of_default(lib)
    cards = sorted(_card_times(elements, spans, chars_of))
    floor = float((bands or {}).get("p10") or 0.0)
    frm = 1.0 / float(fps or 30.0)
    why_skip = {"片头": 0, "片尾": 0, "卡片进场": 0, "非句间口播": 0}
    applied: list[str] = []
    for k in range(len(order) - 1):
        a, b = order[k], order[k + 1]
        tb = spans[b.get("n")][0]
        if tb < JCUT_OPENING - 1e-9:
            why_skip["片头"] += 1
            continue
        if b is order[-1] or tb > total - JCUT_FINALE + 1e-9:
            why_skip["片尾"] += 1
            continue
        if not (a.get("kind") == "clip" and b.get("kind") == "clip"
                and str(a.get("medium") or "") == "talking"
                and str(b.get("medium") or "") == "talking"):
            why_skip["非句间口播"] += 1          # B-roll 段、代码画面、静音格都走这里
            continue
        if any(abs(tb - ct) < JCUT_CARD_GUARD for ct in cards):
            why_skip["卡片进场"] += 1
            continue
        aaid, _ain, aout = _audio_window(a)
        baid, _bin_, bout = _audio_window(b)
        if not aaid or not baid:
            notes.append(f"J-cut # {a.get('n')}→#{b.get('n')} 没做：一格没有声音窗口，量不出余量")
            continue
        dor = round(element_dur(b), 3)
        block: list[tuple[int, str]] = []
        chosen = 0
        for f in (JCUT_DEFAULT, JCUT_FLOOR):
            need = round(f * frm, 3)
            if dur_a and aaid in dur_a and dur_a[aaid] > 0 \
                    and round(aout + need, 3) > round(dur_a[aaid] - frm, 3):
                block.append((f, f"上一镜尾巴只剩 {round(dur_a[aaid] - aout, 3)}s"))
                continue
            if floor and round(dor - need, 3) < floor - 1e-6:
                block.append((f, f"这一镜会短于节奏档下限 {floor:.2f}s"))
                continue
            chosen = f
            break
        if not chosen:
            notes.append(f"J-cut #{a.get('n')}→#{b.get('n')} 没做："
                         + "；".join(f"{f} 帧不行（{r}）" for f, r in block)
                         + "（画面与声音仍同起点）")
            continue
        b["pic_lead"] = chosen
        applied.append(f"#{b.get('n')}({chosen}帧={chosen * frm * 1000:.0f}ms)")
        if chosen != JCUT_DEFAULT:
            notes.append(f"J-cut #{a.get('n')}→#{b.get('n')} 降到 {chosen} 帧："
                         + "；".join(f"{f} 帧不行（{r}）" for f, r in block if f != chosen))
    if applied:
        notes.append(f"J-cut {len(applied)} 处：" + "、".join(applied))
    else:
        notes.append("J-cut：全片没有一个句间口播切点可以提前（B-roll/卡片/片头片尾都让路）")
    rest = sum(why_skip.values())
    if rest:
        notes.append("J-cut 不做的切点 {} 处（".format(rest)
                     + " / ".join(f"{k} {v}" for k, v in why_skip.items() if v) + "）")
    _delta = sorted((k, _BLIND.get(k, 0) - _blind_before.get(k, 0))
                    for k in set(_BLIND) | set(_blind_before))
    problems.extend(("warning", f"！{w}有 {d} 处量不出来：这几处判据没真正生效，不等于通过")
                    for w, d in _delta if d > 0)
    return elements, notes, problems


def build_timeline(lib: Path, plan, *, brief: dict | None = None, style: dict | None = None,
                   skeleton: dict | None = None, style_name: str = "talking-head-v1",
                   fill: bool = False, fit: bool = False,
                   amap: dict | None = None, proj: Path | None = None,
                   jcut: bool = True) -> tuple[dict, list[tuple[str, str]]]:
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

    # 开场两变（BRIEF 剪辑表第 4 条）：生成期就成立，不等到渲完再补。
    # 放在 fit 之后、amap 锚点吸附之前：切完的轴才是真的轴，overlay 的 at 要按新轴吸。
    elements, _open_notes, _open_probs = add_opening_changes(elements, lib=lib, proj=proj)
    notes.extend(_open_notes)
    problems.extend(_open_probs)

    # J-cut（BRIEF 剪辑表第 4 步）：在「后一句」那个元素上写 `pic_lead`（帧）。
    # 放在开场两变**之后**：开场那两刀已经定了，J-cut 只在 3 秒以后的句间切点动；
    # 也不改任何元素的 in/out——它只是告诉渲器「画面比声音晚几帧进」。
    _canvas = style.get("canvas") or {}
    elements, _jc_notes, _jc_probs = apply_jcut(
        elements, lib=lib, fps=float(_canvas.get("fps") or 30.0),
        bands=duration_bands(skeleton, style), enabled=jcut)
    notes.extend(_jc_notes)
    problems.extend(_jc_probs)

    if amap:
        peaks = [round(float(b["t"]) + (float(b["peak_at"]) - float(b["src_start"])), 3)
                 for b in (amap.get("blocks") or [])
                 if b.get("peak_at") is not None and not b.get("is_filler")]
        # 已经踩在事件表上的卡不再吸到峰值：吸的是 `峰值-120ms`，差得能超过判据的 ±80ms，
        # 把刚对齐的开场变化又推回红灯。开场变化靠的就是这个对齐。
        _bounds_at = _block_bounds(elements, _chars_of_default(lib)) if peaks else []
        for el in elements:
            if el.get("track") != "overlay" or not peaks:
                continue
            want = float(el.get("at") or 0.0)
            if any(abs(b - want) <= OPENING_TOL + 1e-6 for b in _bounds_at):
                continue
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
    ap.add_argument("--opening", action="store_true",
                    help="只补「开场两变」：读现有 timeline.json，切片后写回（手写轴/历史项目用）")
    ap.add_argument("--no-jcut", action="store_true",
                    help="生成时不写 J-cut（画面与声音同一格起点，用于对比/回退）")
    ap.add_argument("--jcut-only", action="store_true",
                    help="只重算 J-cut：读现有 timeline.json，写/清 pic_lead 后写回（不动别的东西）")
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

    # 手写轴 / 历史项目：没有 plan.json 也能补开场两变。
    # 这一支与 build_timeline 里的那一步是**同一个函数**（add_opening_changes），
    # 不是“渲完再补”的另外一套：只是把“生成”那一步换成读盘上的轴。
    if a.opening:
        tl = load_json(tl_path)
        if not tl:
            raise SystemExit(f"没有 timeline.json：{tl_path}")
        els, notes, probs = add_opening_changes(tl.get("elements") or [], lib=lib, proj=proj)
        for i, el in enumerate(els, 1):
            el["n"] = i                     # 切完必须重编号：卡/贴纸/字幕都按元素号挂
        tl["elements"] = els
        tl["duration_s"] = round(sum(element_dur(el) for el in els
                                     if (el.get("track") or "video") != "overlay"), 3)
        tl.setdefault("_notes", [])
        tl["_notes"] = [n for n in (tl.get("_notes") or []) if not str(n).startswith("开场")] + notes
        errs = [m for lv, m in probs if lv == "error"]
        print(f"开场两变：{len(els)} 元素 · 片长 {tl['duration_s']}s")
        for el in els:
            d = element_dur(el)
            head = Path(str(el.get("asset"))).stem[-24:] if el.get("asset") else "—"
            print(f"  {el['n']:>2}. {el['kind']:<7} {d:>5.2f}s  {head}")
        for n in notes:
            print(f"  · {n}")
        for lv, m in probs:
            print(f"  {'✗' if lv == 'error' else '!'} {m}")
        if not a.dry_run and not errs:
            tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"→ {tl_path}")
        print(f"校验：error {len(errs)} · warning {len(probs) - len(errs)}")
        return 1 if errs else 0
    skeleton = load_json(Path(a.skeleton) if a.skeleton else proj / "skeleton.json")
    if a.jcut_only:
        # 手写轴 / 历史项目：不重建轴，只把 J-cut 决策按**当前**轴重算一遍。
        # 幂等：apply_jcut 先把所有 pic_lead 清掉再算，连跑两次结果一致。
        tl = load_json(tl_path)
        if not tl:
            raise SystemExit(f"没有 timeline.json：{tl_path}")
        _fps = float((style.get("canvas") or {}).get("fps") or 30.0)
        els, notes, probs = apply_jcut(
            tl.get("elements") or [], lib=lib, fps=_fps,
            bands=tl.get("bands") or duration_bands(skeleton, style),
            enabled=not a.no_jcut)
        tl["elements"] = els
        tl["duration_s"] = round(sum(element_dur(el) for el in els
                                     if (el.get("track") or "video") != "overlay"), 3)
        tl.setdefault("_notes", [])
        tl["_notes"] = ([n for n in (tl.get("_notes") or [])
                          if not str(n).startswith("J-cut")] + notes)
        print(f"J-cut：{len(els)} 元素 · 片长 {tl['duration_s']}s")
        for n in notes:
            print(f"  · {n}")
        for lv, m in probs:
            print(f"  {'✗' if lv == 'error' else '!'} {m}")
        if not a.dry_run:
            tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"→ {tl_path}")
        return 1 if [m for lv, m in probs if lv == "error"] else 0
    plan_path = Path(a.plan) if a.plan else proj / "plan.json"
    plan = load_json(plan_path)
    if not plan:
        raise SystemExit(f"没有 plan.json：{plan_path}")
    amap = load_json(proj / "audio-map.json")
    tl, problems = build_timeline(lib, plan, brief=brief, style=style, skeleton=skeleton,
                                  style_name=style_name, fill=a.fill, fit=a.fit_skeleton,
                                  amap=amap, proj=proj, jcut=not a.no_jcut)
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
