#!/usr/bin/env python3
"""CutSkills · 校验 timeline.json（v4 元素时间轴）。

判的是「这一格能不能拍/能不能造」，不是「这片子好不好看」。按级别分：
  error   —— 结构坏了，渲出来一定是废片（缺字段、时间轴有洞、gen 参数超限、素材不在索引）
  warning —— 能出片但大概是疏忽（单镜过长、生成物占比过高、缺 warrant、B-roll 没声音来源）
  info    —— 只报数，不改判

用法：
  check_timeline.py --project <项目> [--timeline timeline.json] [--strict] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import GEN_TEMPLATES, KINDS, element_dur  # noqa: E402

ROLES = {"setup", "claim", "evidence", "warrant", "turn", "payoff"}
MEDIUMS = {"talking", "broll", "photo", "screen"}
EPS = 0.02  # 时间轴连续性的容差（秒）。差过这个数就不是「同一个切点」了

# 套话。挖出来的是「说了等于没说」的话——写 why 时最容易滑进去的就是这类。
VAGUE = re.compile(r"^(开场|过渡|转场|介绍|展示|说明|补充|衔接|画面|镜头|可用|不错|好看|"
                   r"呼应|铺垫|渲染|强化|强调)[^，。]{0,6}$")


def load_index(lib: Path) -> dict:
    f = lib / "_cutskills" / "index.json"
    if not f.exists():
        return {}
    return {x["id"]: x for x in json.loads(f.read_text())["assets"]}


def check(tl: dict, index: dict, *, strict: bool = False) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    els = tl.get("elements") or []
    if not els:
        return [("error", "一个元素都没有")]

    prev_end = 0.0
    for el in els:
        n = el.get("n")
        kind, track = el.get("kind"), el.get("track")
        if kind not in KINDS:
            out.append(("error", f"#{n} kind「{kind}」非法（{('/'.join(sorted(KINDS)))}）"))
            continue
        if track not in ("video", "overlay"):
            out.append(("error", f"#{n} track 只支持 video / overlay，现在是「{track}」"))
        overlay = track == "overlay"

        d = element_dur(el)
        if d <= 0:
            out.append(("error", f"#{n} 时长 {d}s，不能为零或负"))

        # ---- 时间轴必须连续：没有空洞、没有重叠。老链路靠数组顺序隐式连续，
        # v4 混排之后必须显式查，否则 gen 元素漏算就会让后面全片错位。
        if overlay:
            if el.get("at") is None:
                out.append(("error", f"#{n} 是 overlay，必须给 at（盖在第几秒）"))
            elif float(el["at"]) + d > float(tl.get("duration_s") or 1e9) + 0.05:
                out.append(("error", f"#{n} overlay 从 {el['at']}s 起 {d}s，超出片长"))
        elif el.get("_start") is not None:
            if abs(float(el["_start"]) - prev_end) > EPS:
                out.append(("error", f"#{n} 起点 {el['_start']:.2f}s 与上一格收尾 {prev_end:.2f}s 不接"))
            prev_end = float(el["_start"]) + d

        # ---- why：不能是套话
        why = str(el.get("why") or "").strip()
        if not why:
            out.append(("error", f"#{n} 没写 why（这一格推进了什么）"))
        elif VAGUE.match(why) and len(why) <= 8:
            out.append(("error", f"#{n} why「{why}」是套话，换成「这段让观众多知道了什么」"))

        role = el.get("role")
        if role is not None and role not in ROLES:
            out.append(("error", f"#{n} role「{role}」不在约定值里"))

        # ---- 分类校验
        if kind in ("clip", "image"):
            aid = el.get("asset")
            if not aid:
                out.append(("error", f"#{n} 没写 asset"))
            elif index and aid not in index:
                out.append(("error", f"#{n} 素材不在索引里：{aid}"))
            else:
                rec = index.get(aid) or {}
                dur = float(rec.get("duration") or 0)
                tin, tout = float(el.get("in") or 0), float(el.get("out") or 0)
                if kind == "clip":
                    if "in" not in el or "out" not in el:
                        out.append(("error", f"#{n} clip 缺 in/out"))
                    elif tout - tin < 0.3:
                        out.append(("error", f"#{n} 只有 {tout - tin:.2f}s，短到看不清"))
                    if dur and tout > dur + 0.05:
                        out.append(("error", f"#{n} out {tout:.2f}s 超过素材长度 {dur:.2f}s"))
                if el.get("medium") not in MEDIUMS:
                    out.append(("error", f"#{n} medium「{el.get('medium')}」非法"))
                if el.get("medium") == "broll" and not el.get("audio"):
                    out.append(("warning", f"#{n} 是 B-roll 但没给 audio：画面换了，声音从哪来？"))
                g = rec.get("grade") or el.get("grade_at_cut")
                if g == "do-not-use":
                    out.append(("warning", f"#{n} 用的是 do-not-use 素材（{el.get('grade_at_cut')}）"))
            if d > 12:
                out.append(("warning", f"#{n} 单格 {d:.1f}s 超过 12s，确认是刻意留白"))

        elif kind == "gen":
            tpl = el.get("template")
            spec = GEN_TEMPLATES.get(tpl)
            if not spec:
                out.append(("error", f"#{n} gen 模板「{tpl}」不认识（{('/'.join(sorted(GEN_TEMPLATES)))}）"))
            else:
                if d < float(spec["min_dur"]) - 1e-6:
                    out.append(("error", f"#{n} {tpl} 只有 {d:.2f}s，模板下限 {spec['min_dur']}s——读不完"))
                if d > float(spec["max_dur"]) + 1e-6:
                    out.append(("warning", f"#{n} {tpl} {d:.2f}s 超过上限 {spec['max_dur']}s，会显得空"))
                props = el.get("props") or {}
                for k in spec.get("required") or []:
                    if props.get(k) in (None, "", []):
                        out.append(("error", f"#{n} {tpl} 缺必填 props.{k}"))
                for k, lim in (spec.get("props") or {}).items():
                    v = props.get(k)
                    if v is None:
                        continue
                    if isinstance(lim, dict):          # 嵌套结构：{"title": 6, "value": 10}
                        if isinstance(v, dict):
                            for kk, sub_lim in lim.items():
                                vv = v.get(kk)
                                if isinstance(vv, str) and len(vv) > int(sub_lim):
                                    out.append(("error",
                                                f"#{n} {tpl} props.{k}.{kk} {len(vv)} 字，上限 {sub_lim}"))
                        continue
                    if lim is None:
                        continue
                    if isinstance(v, str) and len(v) > int(lim):
                        out.append(("error", f"#{n} {tpl} props.{k} {len(v)} 字，上限 {lim}"))
                if el.get("source") != "generated":
                    out.append(("error", f"#{n} 是代码生成的，source 必须写 generated"))
                if tpl == "scene":
                    from gen_scene import SCENES
                    nm = str(props.get("scene") or "")
                    if nm not in SCENES:
                        out.append(("error", f"#{n} 的场景「{nm}」没有实现"
                                             f"（{'/'.join(sorted(SCENES))}）"))
                    # 整屏换画面时声音必须接上：剪辑上「换画面」是可以的，
                    # 「换画面顺手把人声弄没了」不是——实测撞出过 3 秒哑段。
                    if not el.get("audio"):
                        out.append(("error", f"#{n} 是整屏代码画面，但没给 audio："
                                             f"这几秒会没有声音"))
                    if el.get("track") == "overlay":
                        out.append(("warning", f"#{n} scene 写在 overlay 上没用："
                                               f"场景是整屏位图，只能走 video 轨"))

        elif kind == "silence":
            if d > 2.5:
                out.append(("warning", f"#{n} 空 {d:.1f}s，超过 2.5s 会以为是卡住了"))

    # ---- 全片级
    seq_total = round(sum(element_dur(e) for e in els if e.get("track") != "overlay"), 3)
    total = float(tl.get("duration_s") or seq_total)
    if tl.get("duration_s") is not None and abs(seq_total - total) > 0.1:
        out.append(("error", f"duration_s 写的是 {total:.2f}s，各格加起来 {seq_total:.2f}s"
                             f"——时间轴被改过但总长没跟着改"))
    # overlay 那一格是盖在口播上的，不吃时间也不该算进「生成物占比」
    gen_s = sum(element_dur(e) for e in els
                if e.get("kind") in ("gen", "silence") and e.get("track") != "overlay")
    if total and gen_s / total > 0.25:
        out.append(("warning", f"代码生成占 {gen_s / total:.0%}（{gen_s:.1f}s），超过 25%"
                               f"就开始像 PPT 了——要么补素材，要么砍论点"))

    roles = {e.get("role") for e in els}
    if "warrant" not in roles:
        out.append(("warning", "全片没有 warrant 段：只有证据没有说理，观众不信的往往是那半步"))

    brief = tl.get("brief") or {}
    if brief.get("length_s"):
        want = float(brief["length_s"])
        diff = (total - want) / want if want else 0
        if abs(diff) > 0.10:
            out.append(("error" if strict else "warning",
                        f"总时长 {total:.1f}s vs 需求 {want:.0f}s，差 {diff:+.0%}"))

    # 40 秒内至少换一次介质（口播片最容易被这个拖死）
    meds, last_switch, worst = [], 0.0, 0.0
    for e in els:
        m = e.get("template") if e.get("kind") == "gen" else e.get("medium")
        if m != (meds[-1] if meds else None):
            meds.append(m)
            last_switch = 0.0
        last_switch += element_dur(e)
        worst = max(worst, last_switch)
    if worst > 40:
        out.append(("warning", f"最长 {worst:.0f}s 没有换介质（上限 40s）"))

    return out


def summary(tl: dict) -> dict:
    els = tl.get("elements") or []
    by_kind: dict[str, float] = {}
    for e in els:
        k = f"{e['kind']}-overlay" if e.get("track") == "overlay" else e["kind"]
        by_kind[k] = round(by_kind.get(k, 0) + element_dur(e), 2)
    total = float(tl.get("duration_s") or sum(by_kind.values()))
    lens = sorted(element_dur(e) for e in els if e.get("track") != "overlay")
    med = lens[len(lens) // 2] if lens else 0
    return {"total_s": round(total, 2), "elements": len(els), "by_kind": by_kind,
            "median_len_s": round(med, 2),
            "p10": round(lens[int(len(lens) * 0.1)] if lens else 0, 2),
            "p90": round(lens[min(len(lens) - 1, int(len(lens) * 0.9))] if lens else 0, 2),
            "gen_ratio": round(sum(v for k, v in by_kind.items()
                                   if k in ("gen", "silence")) / total, 3)
            if total else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--library", default=None)
    ap.add_argument("--strict", action="store_true", help="把 warning 当 error")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    tp = Path(a.timeline) if a.timeline else proj / "timeline.json"
    tl = json.loads(tp.read_text())
    lib = Path(a.library).expanduser().resolve() if a.library else proj.parent.parent.parent
    index = load_index(lib)

    probs = check(tl, index, strict=a.strict)
    errs = [m for lv, m in probs if lv == "error"]
    warns = [m for lv, m in probs if lv == "warning"]
    s = summary(tl)

    if a.json:
        print(json.dumps({"problems": probs, "summary": s}, ensure_ascii=False, indent=1))
    else:
        for lv, m in probs:
            print(f"  {'✗' if lv == 'error' else '!'} {m}")
        kk = " · ".join(f"{k} {v:.1f}s" for k, v in s["by_kind"].items())
        print(f"时间轴 {s['total_s']}s · {s['elements']} 元素 · 中位 {s['median_len_s']}s"
              f"（{s['p10']}–{s['p90']}）· 生成物占比 {s['gen_ratio']:.0%}")
        print(f"  {kk}")
        print(f"校验：error {len(errs)} · warning {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
