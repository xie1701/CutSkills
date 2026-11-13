#!/usr/bin/env python3
"""CutSkills · 口播段「声 / 画」双评级与露脸门禁（T-015 · 原则 A2）。

为什么要把一支素材拆成两份评级：一手拿着 A4 台本纸、眼睛瞟场记、构图从脖子处切掉，
这些片段的**声音是好的**，画面是穿帮的。纪录片与商业片的通行做法不是把素材扔掉，
而是降级成「只留声」（Audio-Only）——声音继续当旁白，画面 100% 换成合格的 B-roll。
只用一个 `grade` 字段时只能一刀切（要么浪费一段好声音，要么把穿帮画面放进片），
所以这里按 A2 拆成两个正交字段：

    audio_grade   usable / ng                    声音能不能用
    video_grade   usable / audio_only / ng       画面能不能露脸

判定来源（前面的覆盖后面的）：

    1. 时间轴元素 `props.video_grade` / `props.audio_grade` —— 纸面剪辑时人手写，
       是这一格最终裁定的位置（同一支素材不同窗口可以不同评级，只能在这一层表达）；
    2. L2 视觉审片记录 `_cutskills/l2/*.json`，以及 `index_assets.py --merge-l2`
       合并后的 `segments[]` —— 窗口有重叠时取最差的一档。

**缺省（两格都没有）时门禁什么都不报。** 旧素材库没有这两个字段，给「未知」加一条
warning 会让所有老项目的红黄在一夜之间暴涨——那不是判据变严，那是判据在骗人。

踩过的坑（别踩第二次）：

  - 任务单写的是 `_cutskills/l2s/`，实际 JSON 落在 `_cutskills/l2/`，`l2s/` 里是接触表
    jpg。两个目录都扫 `*.json`：某天换了落盘目录，门禁不能因为找不到文件就静默放行。
  - L2 的 in/out 是人/模型框出来的估值，边界有 ±0.2–0.3s 误差。重叠不到 `EDGE_TOL_S`
    时报 warning（贴边，确认一下），不是 error——否则每一格都会因为半帧误差报红。
  - 只拦 `medium: talking`。B-roll 留声（画面换成别的素材、声音走 `audio` 轨）
    正是 A2 给的正解，拦它就是跟自己过不去。
  - `in` 是 Python 关键字，所以 `grade_of()` 的时间参数只能按位置传（t_in / t_out）。
"""
from __future__ import annotations

import json
from pathlib import Path

AUDIO_GRADES = {"usable", "ng"}
VIDEO_GRADES = {"usable", "audio_only", "ng"}
# 露脸用不了的两档：audio_only = 只留声；ng = 声画都不该进片
BAD_VIDEO = {"audio_only", "ng"}

# 评级排序：数字大的更差，合并多段记录时取最差
_RANK = {"usable": 0, "audio_only": 1, "ng": 2}

# L2 窗口边界是估出来的。重叠小于这个秒数只报「贴边」，不判死
EDGE_TOL_S = 0.30

# 每次调用都重读 index.json（153 支素材）会很慢，而 grade_of 是逐格调用的。
# 缓存键带上各目录的 mtime：记录一改，键就变，不会读到旧的评级。
_CACHE: dict[tuple, tuple[list[dict], dict[str, set[str]]]] = {}


def norm(v) -> str | None:
    """`Audio-Only` / ` audio only ` / `AUDIO_ONLY` 都是同一个意思——评级写错大小写不该漏拦。"""
    s = str(v if v is not None else "").strip().lower().replace("-", "_").replace(" ", "_")
    return s or None


def _stamp(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _span_of(rec: dict, src: str) -> dict | None:
    """一条记录 → 一个可比较的区间。没有评级字段的记录直接丢掉（L0/L1 的段不参与门禁）。"""
    vg, ag = norm(rec.get("video_grade")), norm(rec.get("audio_grade"))
    if vg is None and ag is None:
        return None
    try:
        i, o = float(rec.get("in") or 0), float(rec.get("out") or 0)
    except (TypeError, ValueError):
        return None
    if o <= i:
        return None
    # 理由优先取 why（人写的判断依据），退到 flags（审片时的现象），再退到 tag
    why = str(rec.get("why") or "").strip()
    flags = [str(x).strip() for x in (rec.get("flags") or []) if str(x).strip()]
    return {"asset": str(rec.get("asset") or ""), "in": i, "out": o,
            "video_grade": vg, "audio_grade": ag,
            "reason": why or "；".join(flags) or str(rec.get("tag") or ""), "src": src}


def _load(lib: Path) -> tuple[list[dict], dict[str, set[str]]]:
    """读全部带评级的区间 + 素材别名表（旧路径写的 L2 记录也认得出是同一支）。"""
    sysdir = lib / "_cutskills"
    idx_f = sysdir / "index.json"
    l2_dirs = [sysdir / "l2", sysdir / "l2s"]
    key = (str(lib), _stamp(idx_f), *(_stamp(d) for d in l2_dirs))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    spans: list[dict] = []
    aliases: dict[str, set[str]] = {}
    for d in l2_dirs:                       # 手写记录（唯一持久的地方）
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                r = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(r, dict) or not r.get("asset"):
                continue
            sp = _span_of(r, "l2")
            if sp:
                spans.append(sp)
    if idx_f.exists():                      # 合并进索引的那一份（视图，可重建）
        try:
            idx = json.loads(idx_f.read_text())
        except (OSError, json.JSONDecodeError):
            idx = {}
        for a in idx.get("assets") or []:
            aid = a.get("id")
            if not aid:
                continue
            aliases[aid] = {aid, *(a.get("aliases") or [])}
            # l2_segments 是每次 --merge-l2 重建的；没有它时才回退 segments，
            # 否则同一段会被两条记录各算一次，穿帮秒数翻倍。
            segs = a.get("l2_segments") or a.get("segments") or []
            for s in segs:
                sp = _span_of({**s, "asset": aid}, "index")
                if sp:
                    spans.append(sp)
    _CACHE.clear()                          # 只留最新一轮：素材库是按项目切换的，攒着没意义
    _CACHE[key] = (spans, aliases)
    return spans, aliases


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _cover(ivals: list[tuple[float, float]]) -> float:
    """区间并集长度。两条记录各写了一半的同一段穿帮，不能把同一秒算两遍。"""
    total, cur0, cur1 = 0.0, None, None
    for b0, b1 in sorted(ivals):
        if cur1 is None or b0 > cur1:
            if cur1 is not None:
                total += cur1 - cur0
            cur0, cur1 = b0, b1
        else:
            cur1 = max(cur1, b1)
    if cur1 is not None:
        total += cur1 - cur0
    return total


def grade_of(lib, asset, t_in=None, t_out=None, props: dict | None = None) -> dict:
    """一支素材（或它的一个窗口）的声 / 画评级。

    返回 `{audio_grade, video_grade, source, reason, hit_s}`：
      - `source` = `props`（时间轴手写）/ `l2`（审片记录）/ `index`（合并后的索引）/ None（未知）
      - `hit_s`  = 这个窗口里被判穿帮的秒数（并集），给门禁判「真穿帮」还是「只贴边」
    两格都是 None 表示**这条素材没人评过**，调用方不许把「未知」当「合格」以外的任何结论。
    """
    props = props or {}
    vg, ag = norm(props.get("video_grade")), norm(props.get("audio_grade"))
    if vg or ag:
        why = str(props.get("grade_why") or "").strip()
        return {"audio_grade": ag, "video_grade": vg, "source": "props",
                "reason": why or "时间轴手写评级", "hit_s": 0.0}
    unknown = {"audio_grade": None, "video_grade": None, "source": None,
               "reason": "", "hit_s": 0.0}
    if lib is None or not asset:
        return unknown
    spans, aliases = _load(Path(lib).expanduser().resolve())
    keys = aliases.get(str(asset)) or {str(asset)}
    cands = [s for s in spans if s["asset"] in keys]
    if t_in is not None and t_out is not None:
        win = (float(t_in), float(t_out))
        cands = [s for s in cands if _overlap(*win, s["in"], s["out"]) > 0]
        bad_v = [(max(win[0], s["in"]), min(win[1], s["out"]))
                 for s in cands if s["video_grade"] in BAD_VIDEO]
        hit = _cover(bad_v)
    else:
        hit = _cover([(s["in"], s["out"]) for s in cands])
    if not cands:
        return unknown

    def _worst(field: str) -> str | None:
        got = [s[field] for s in cands if s.get(field)]
        return max(got, key=lambda g: _RANK.get(g, 0)) if got else None

    worst_v = _worst("video_grade")
    # 理由取「判出这一档」的那条记录的原话——门禁报出来的该是「审片时看到了什么」，不是套话
    src = next((s for s in cands if s["video_grade"] in BAD_VIDEO), None) \
        or next((s for s in cands if s["audio_grade"] == "ng"), None) or cands[0]
    return {"audio_grade": _worst("audio_grade"), "video_grade": worst_v,
            "source": src["src"], "reason": src["reason"] or f"评级 {worst_v}",
            "hit_s": round(hit, 2)}


def gate(elements, lib=None) -> list[tuple[str, str]]:
    """`medium: talking` 的露脸门禁（A2）。返回 [(级别, 说明)]，级别是 error / warning。

    规矩只有一条：**露脸的那一格不能引用被判「画不能露」的窗口**。
    穿帮画面盖不住的唯一正解是改铺 B-roll 留声（画面换掉、声音走 audio 轨）或换 take，
    所以在报错里直接把两条出路写出来，省得下一个人去翻准绳文件。
    """
    out: list[tuple[str, str]] = []
    for el in elements or []:
        if el.get("track") == "overlay" or el.get("kind") != "clip":
            continue
        if el.get("medium") != "talking":
            continue
        n = el.get("n")
        # props 是正式写法；元素顶层也认一份——手写时间轴时顺手写在元素上不该静默失效
        props = dict(el.get("props") or {})
        for k in ("video_grade", "audio_grade"):
            if k not in props and el.get(k) is not None:
                props[k] = el[k]
        g = grade_of(lib, el.get("asset"), el.get("in"), el.get("out"), props)
        vg, ag = g["video_grade"], g["audio_grade"]

        # 评级写错（打错字、用了旧枚举）不能当「合格」放过去，但也别判死
        for field, known in (("video_grade", VIDEO_GRADES), ("audio_grade", AUDIO_GRADES)):
            val = g.get(field)
            if val is not None and val not in known:
                out.append(("warning", f"#{n} {field}「{val}」不认识"
                                       f"（{'/'.join(sorted(known))}）——按未知处理，门禁不生效"))
        if vg in BAD_VIDEO:
            reason = g["reason"] or f"评级 {vg}"
            msg = (f"#{n} 该口播段画面穿帮（{reason}），改铺 B-roll 留声或换 take")
            # 手写评级是「这一格就这样定」，整窗算穿帮；审片记录是估出来的边界，
            # 只擦到一点点时提示确认，别把半帧误差判成废片。
            if g["source"] == "props" or g["hit_s"] >= EDGE_TOL_S:
                out.append(("error", msg))
            else:
                out.append(("warning", f"{msg}（只压到边界 {g['hit_s']:.2f}s，确认是否掐掉）"))
        elif ag == "ng":
            out.append(("warning", f"#{n} 该口播段声音评级 ng（{g['reason'] or '声音不可用'}）——"
                                   f"声音不能用的 take 不能因为画面好看就上"))
    return out
