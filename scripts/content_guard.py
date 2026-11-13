#!/usr/bin/env python3
"""CutSkills · 内容守卫：起尾余量（Handles）、视觉指代词、品类禁忌表。

## 为什么要有这个

2026-11-13 第五轮对抗式审查：8 道脚本全报 `0 error`，成片里却同时有「0.00 秒按键糊片」
「嘴上说四张图、画面放工地空镜」「夸原汁原味、画面给生肉血水」。这三类都不是结构错——
`check_timeline` 原来判的是时长、连续性、字段合法性；**画面有没有证明这句话**，
一条都碰不到，所以才会全绿。这个模块专门补这一层。

## 三条判据（准绳：references/editorial-packaging-principles.md A1 / A3 / A4）

1. **Handles 起尾余量（A1）**：实拍 B-roll（`kind: clip` + `medium: broll`）画面起点
   `in < 0.40s`，或距素材尾 `duration - out < 0.40s` → **error**。
   按下录制键的头几帧是机身微震、还没对上焦，停机前几帧是收机甩镜、松气低头——
   专业剪辑规范里这是物理废片禁区，不是「凑合能用」。素材总时长取索引里的 `duration`，
   量不出（0 或缺失）时只能降级为 warning 并说明，不假装判过。
   豁免：`props.handles_exempt: "理由"`（必须带理由，理由会原样进 warning，可审）。
2. **视觉指代词（A3）**：台词命中「四张图 / 呢张图 / 效果图 / 睇下呢度」这类
   **靠画面才能成立的词**，而这一格画面拿不出对应视觉佐证（`props.visual_ref` 缺失、
   `kind` 不是 image/gen、素材 L2 标签也没有图类命中）→ **error**，同时把缺口写进
   项目 `gap.md`（带时间戳）。口播承诺落空，观众立刻知道是拿废片硬凑。
3. **品类禁忌（A4）**：正向夸赞台词（舒服 / 正宗 / 靓 / 原汁原味 / 招牌…）撞上
   L2 `taboo_tags` 或元素 `props.taboo_tags` 的负向画面（生肉血水 / 厨房垃圾 /
   建筑垃圾 / 工人穿帮 / 欠曝发灰食物）→ **error**。嘴上夸、画面膈应，是反向宣传。

用法：
  content_guard.py --project <项目> [--library <素材库>] [--json]
  （正常不用手跑：check_timeline.py 会调本模块的 guard()）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HANDLE_MIN = 0.40          # A1：起幅/尾段的物理废片禁区。0.4s 是行业通用的最小余量
TOL = 1e-9                 # 浮点比较容差：0.40 就是 0.40，不因为二进制表示被误判

# 视觉指代词：依赖现场画面才成立的词。命中就要有对应视觉佐证。
# 粤语（呢/睇/呢四样）和普通话（这/看）两套都收——这一库两条口播是混着说的。
DEICTIC = re.compile(r"四张图|几张图|这张图|呢张图|效果图|睇下呢度|睇呢度|看这里|呢四样|这四张")
# 素材 L2 标签里算什么「视觉佐证」。写的是画面里有什么（图/图纸/效果图/屏幕截图），
# 不是判断词——所以「工地图」这种真拍到的图纸也算数。
VISUAL_TAG = re.compile(r"图纸|图片|效果图|平面图|示意图|方案图|设计图|施工图|看板|照片|截图|样张|海报")
# 正向夸赞：这句话在把画面往好里说。撞上负向画面就是反向宣传。
PRAISE = re.compile(r"舒服|正宗|原汁原味|好食|好吃|靓|享受|招牌|新鲜|干净|卫生")
# 品类负向标签（A4 的表）。可扩充：扩充时同一次改动里要改 references/index-schema.md。
TABOO_TAGS = {"raw_meat", "kitchen_trash", "unappetizing_dark",
              "heavy_construction_trash", "crew_leak"}


def load_index(lib: Path) -> dict:
    """<素材库>/_cutskills/index.json → {id: asset}。没有索引就是空——判据照样跑，
    只是「量不出」的部分改报 warning（静默跳过等于放行，那是更坏的结果）。"""
    f = Path(lib) / "_cutskills" / "index.json"
    if not f.exists():
        return {}
    return {x["id"]: x for x in json.loads(f.read_text()).get("assets") or []}


def _rec_of(index, aid) -> dict:
    if not aid:
        return {}
    if isinstance(index, dict):
        return index.get(aid) or {}
    return next((x for x in index or [] if x.get("id") == aid), {}) or {}


def _l2_segments(rec: dict) -> list[dict]:
    """这支素材的段记录。合并过 L2 的素材，`segments` 已经被 `l2_segments` 覆盖；
    只跑过 L0 的素材没有 tag / taboo_tags，检索自然不命中——这是如实反映「没看过」。"""
    segs = rec.get("l2_segments") or rec.get("segments") or []
    return [s for s in segs if isinstance(s, dict)]


def _overlapping(rec: dict, tin: float, tout: float) -> list[dict]:
    """只认**和这一格画面重叠**的段：同一支素材别的段落里有图，证明不了这一格有图。"""
    out = []
    for s in _l2_segments(rec):
        try:
            s0, s1 = float(s.get("in") or 0.0), float(s.get("out") or 0.0)
        except (TypeError, ValueError):
            continue
        if s1 <= tin or s0 >= max(tout, tin + 1e-6):
            continue
        out.append(s)
    return out


def _as_tags(v) -> set[str]:
    """`taboo_tags` 允许写成列表，也允许写成「raw_meat, kitchen_trash」这种串——
    L2 是手写的，手写就会两种都出现，只认一种等于漏判。"""
    if v is None or v == "":
        return set()
    if isinstance(v, str):
        return {x.strip() for x in re.split(r"[,，;；\s]+", v) if x.strip()}
    if isinstance(v, (list, tuple, set)):
        return {str(x).strip() for x in v if str(x).strip()}
    return set()


def check_handles(n, el: dict, rec: dict, props: dict) -> list[tuple[str, str]]:
    """A1：实拍 B-roll 的起幅与尾段余量。"""
    out: list[tuple[str, str]] = []
    tin = float(el.get("in") or 0.0)
    tout = float(el.get("out") or 0.0)
    dur = float(rec.get("duration") or 0.0)
    aid = el.get("asset") or "?"
    ex = props.get("handles_exempt")
    exempt = ex.strip() if isinstance(ex, str) else ""

    head_bad = tin < HANDLE_MIN - TOL
    beyond = dur and tout > dur + 0.05          # out 超长度：那是另一条判据，这里不重复报
    tail_bad = bool(dur) and not beyond and (dur - tout) < HANDLE_MIN - TOL
    unknown_tail = not dur

    if exempt and (head_bad or tail_bad or unknown_tail):
        # 放行，但必须留痕：豁免是人的判断，脚本不再拦；下一轮复核要能看见「谁放过的、为什么」。
        span = f"素材 [{tin:.2f}, {tout:.2f}]s / 全长 {dur:.2f}s" if dur else f"素材起于 {tin:.2f}s（全长量不出）"
        out.append(("warning", f"#{n} B-roll 起尾余量豁免放行（理由：{exempt}）——{span}"))
        return out

    if head_bad:
        out.append(("error", f"#{n} B-roll 从素材第 {tin:.2f}s 起——起幅余量不足 {HANDLE_MIN:.2f}s，"
                             f"那是按键/对焦未稳的糊片；往后挪到 {HANDLE_MIN:.2f}s 之后再起，"
                             f"或用 props.handles_exempt 写清为什么必须贴头发"))
    if tail_bad:
        out.append(("error", f"#{n} B-roll 收在 {tout:.2f}s，距素材尾只剩 {dur - tout:.2f}s"
                             f"（素材全长 {dur:.2f}s）——尾段是收机甩镜/松气低头，"
                             f"要留 ≥{HANDLE_MIN:.2f}s；提前收，或用 props.handles_exempt 说明"))
    if unknown_tail:
        out.append(("warning", f"#{n} 起尾余量只量了起幅：索引里 {aid} 量不出时长"
                               f"（duration 缺失或为 0），尾巴判不了——别当它干净"))
    return out


def visual_evidence(el: dict, rec: dict, props: dict) -> str:
    """这一格画面凭什么叫「图」的佐证。返回成立的依据，空串 = 没有佐证。"""
    vr = props.get("visual_ref")
    if isinstance(vr, (list, tuple)):
        vr = "、".join(str(x) for x in vr if str(x).strip())
    if isinstance(vr, str) and vr.strip():
        return f"props.visual_ref = {vr.strip()[:40]}"
    if el.get("kind") in ("image", "gen"):
        # 照片本身就是图；gen 是代码画的图/卡——两者都看得见「图」，不需要再标。
        return f"kind = {el.get('kind')}"
    tin, tout = float(el.get("in") or 0.0), float(el.get("out") or 0.0)
    blob = " ".join(f"{s.get('tag') or ''} {s.get('action') or ''} {s.get('summary') or ''}"
                    for s in _overlapping(rec, tin, tout))
    m = VISUAL_TAG.search(blob)
    return f"L2 标签「{m.group(0)}」" if m else ""


def check_deictic(n, el: dict, rec: dict, props: dict) -> tuple[list[tuple[str, str]], dict | None]:
    """A3：说了指代词，画面得有那张图。返回 (问题, 缺口行)。"""
    text = str(el.get("evidence") or "")
    m = DEICTIC.search(text)
    if not m:
        return [], None
    why = visual_evidence(el, rec, props)
    if why:
        return [], None
    why_not = ("这不是 image/gen 生成画面，props.visual_ref 没标，"
               "素材 L2 标签里也没有图类命中")
    out = [("error", f"#{n} 台词说「{m.group(0)}」，这一格画面没有对应视觉佐证"
                     f"（{why_not}）——口播承诺落空，观众会看出是拿废片硬凑；"
                     f"把那张图入库并标 L2，或改选不带指代词的 take")]
    row = {"n": n, "text": text, "word": m.group(0),
           "asset": el.get("asset") or ("gen:" + str(el.get("template") or "")),
           "why": why_not}
    return out, row


def check_taboo(n, el: dict, rec: dict, props: dict) -> list[tuple[str, str]]:
    """A4：正向夸赞台词撞负向画面。"""
    text = str(el.get("evidence") or "")
    m = PRAISE.search(text)
    if not m:
        return []
    tags = _as_tags(props.get("taboo_tags"))
    tin, tout = float(el.get("in") or 0.0), float(el.get("out") or 0.0)
    for s in _overlapping(rec, tin, tout):
        tags |= _as_tags(s.get("taboo_tags"))
    hit = sorted(tags & TABOO_TAGS)
    out: list[tuple[str, str]] = []
    if hit:
        out.append(("error", f"#{n} 台词在夸「{m.group(0)}」，画面却标了禁忌 {'/'.join(hit)}"
                             f"——正向夸赞撞负向画面是反向宣传；换一段干净的画面，"
                             f"或把这句话挪到讲「筹备/过程」的格子上"))
    unknown = sorted(tags - TABOO_TAGS)
    if unknown:
        # 标签写错和没标一样危险：`raw-meat` / `Raw_Meat` 都不会命中，静默放行。
        out.append(("warning", f"#{n} 的 taboo_tags 有不认识的标签 {'/'.join(unknown)}"
                               f"（认识的：{'/'.join(sorted(TABOO_TAGS))}）"))
    return out


def guard(tl: dict, index, *, proj: Path | None = None) -> list[tuple[str, str]]:
    """入口。返回 (级别, 说明) 列表，级别只可能是 error / warning（与 check_timeline 一致）。"""
    probs: list[tuple[str, str]] = []
    gap_rows: list[dict] = []
    for el in tl.get("elements") or []:
        n = el.get("n")
        props = el.get("props") or {}
        rec = _rec_of(index, el.get("asset"))
        if el.get("kind") == "clip" and el.get("medium") == "broll":
            probs += check_handles(n, el, rec, props)
        p1, row = check_deictic(n, el, rec, props)
        probs += p1
        if row:
            gap_rows.append(row)
        probs += check_taboo(n, el, rec, props)

    if proj:
        # 缺口必须落到人看得见的地方：只在 stdout 报一遍，下一轮就没人记得补；
        # 缺口补齐后也要把旧的 visual_gap 节清掉，免得 gap.md 留假账。
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from gaps import append_visual_gap
            p = append_visual_gap(Path(proj), gap_rows)
            if p:
                probs.append(("warning", f"指代缺口 {len(gap_rows)} 条已记入 {p}（带时间戳）"))
        except Exception as exc:                        # noqa: BLE001
            if gap_rows:
                probs.append(("warning", f"指代缺口没能写进 gap.md：{type(exc).__name__}: {exc}"))
    return probs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None, help="默认 <项目>/timeline.json")
    ap.add_argument("--library", default=None, help="默认从项目目录往上推三层")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    tp = Path(a.timeline) if a.timeline else proj / "timeline.json"
    tl = json.loads(tp.read_text())
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from libs import resolve_lib
    lib = resolve_lib(proj, a.library)
    probs = guard(tl, load_index(lib), proj=proj)
    errs = [m for lv, m in probs if lv == "error"]
    warns = [m for lv, m in probs if lv == "warning"]
    if a.json:
        print(json.dumps({"problems": probs}, ensure_ascii=False, indent=1))
    else:
        for lv, m in probs:
            print(f"  {'✗' if lv == 'error' else '!'} {m}")
        print(f"内容守卫：error {len(errs)} · warning {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
