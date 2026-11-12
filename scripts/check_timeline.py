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
from timeline import GEN_TEMPLATES, KINDS, element_dur, AXES, MG_ROLES, NUMERIC_TEMPLATES  # noqa: E402

ROLES = {"setup", "claim", "evidence", "warrant", "turn", "payoff"}
MEDIUMS = {"talking", "broll", "photo", "screen"}
EPS = 0.02  # 时间轴连续性的容差（秒）。差过这个数就不是「同一个切点」了
MICRO_GAP_MAX = 1.2        # 文字卡：参考片 0.35–0.9s 一次小变化，超过 1.2s 就是死卡
# 连续场景不一样：它的「一件事」本身就是一个 1–3 秒的变化（石头→粉、粉→膏），
# 拿卡片的 1.2s 要求它，等于要求一支场景每 1.2 秒换一次状态，反而碎。
MICRO_GAP_MAX_SCENE = 2.5
B_AXIS_MIN_S = 8.0    # B 轴连续场景的下限：短于 8s 装不下「谁/遇到什么/怎么变」

# 套话。挖出来的是「说了等于没说」的话——写 why 时最容易滑进去的就是这类。
VAGUE = re.compile(r"^(开场|过渡|转场|介绍|展示|说明|补充|衔接|画面|镜头|可用|不错|好看|"
                   r"呼应|铺垫|渲染|强化|强调)[^，。]{0,6}$")


def load_index(lib: Path) -> dict:
    f = lib / "_cutskills" / "index.json"
    if not f.exists():
        return {}
    return {x["id"]: x for x in json.loads(f.read_text())["assets"]}


def check(tl: dict, index: dict, *, strict: bool = False, decisions_text: str | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    els = tl.get("elements") or []
    if not els:
        return [("error", "一个元素都没有")]

    # ---- v5 顶层字段（v4 全部缺省，向后兼容）
    ver = int(tl.get("version") or 4)
    if ver not in (4, 5):
        out.append(("error", f"version {ver} 不认识（4 或 5）"))
    axis_default = str(tl.get("axis") or "A")
    if axis_default not in AXES:
        out.append(("error", f"顶层 axis「{axis_default}」非法（{'/'.join(sorted(AXES))}）"))
    for k in ("plan", "pack"):
        v = tl.get(k)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            out.append(("error", f"{k} 必须是字符串（骨架/包装的 id）"))

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

        # ---- 轴线（v5）：B 轴是「代码连续场景当叙事主体」，有准入条件
        ax = str(el.get("axis") or axis_default)
        if ax not in AXES:
            out.append(("error", f"#{n} axis「{ax}」非法（A/B）"))
        elif ax == "B":
            nar = el.get("narrative") or {}
            miss = [k for k in ("subject", "turn", "change")
                    if not str(nar.get(k) or "").strip()]
            if miss:
                _m = "/".join(miss)
                out.append(("error", f"#{n} 是 B 轴，narrative 缺 {_m}"
                                     f"（谁 / 遇到什么 / 怎么变）"))
            if d < B_AXIS_MIN_S - 1e-6:
                out.append(("error", f"#{n} B 轴只 {d:.1f}s，低于 {B_AXIS_MIN_S}s——"
                                     f"装不下「谁/遇到什么/怎么变」"))
            if kind != "gen":
                out.append(("error", f"#{n} B 轴必须是 gen（代码画的连续场景），现在是 {kind}"))

        # ---- 卡内微事件（v5）：一张卡/场景在场很久却一动不动，就是 PPT
        micro = el.get("micro")
        if micro is not None:
            if not isinstance(micro, list):
                out.append(("error", f"#{n} micro 必须是列表"))
            else:
                ts = []
                for it in micro:
                    # 一项可以是 {"t": 秒, "text": "..."}，也可以直接给秒数。
                    # 以前只认 dict 且只抓 TypeError/ValueError，写成一串字符串时
                    # 直接 AttributeError 崩掉——报错比错误本身还难懂。
                    if isinstance(it, dict):
                        _tv = it.get("t")
                    elif isinstance(it, (int, float)):
                        _tv = it
                    else:
                        out.append(("error", f"#{n} micro 里有一项不是 {{t, text}} 也不是秒数：{it!r}"))
                        continue
                    try:
                        ts.append(float(_tv))
                    except (TypeError, ValueError):
                        out.append(("error", f"#{n} micro 里有一项缺 t"))
                bad = [t for t in ts if t < -1e-6 or t > d + 1e-6]
                if bad:
                    out.append(("error", f"#{n} micro 时刻 {bad[0]:.2f}s 不在 0–{d:.2f}s 内"))
                seq = [0.0] + sorted(ts) + [d]
                gap = max(b - a for a, b in zip(seq, seq[1:])) if len(seq) > 1 else d
                _mg = MICRO_GAP_MAX_SCENE if str(el.get("template") or "") == "scene" else MICRO_GAP_MAX
                if gap > _mg:
                    out.append(("warning", f"#{n} 有 {gap:.1f}s 没有任何画面变化"
                                            f"（卡内微事件目标 ≤{_mg}s 一次）"))
        elif kind == "gen" and ver >= 5 and d > MICRO_GAP_MAX * 2:
            out.append(("warning", f"#{n} {d:.1f}s 的 gen 没写 micro：整段画面是死的"))

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

                # ---- 数字纪律（v5）：画面里出现的数要么有出处，要么标明是示意。
                # 模型写多少就是多少的年头结束在 2026-11-11：观众信不信，取决于这个数能不能被查。
                if tpl in NUMERIC_TEMPLATES and ver >= 5:
                    src = str(el.get("source_ref") or "").strip()
                    approx = el.get("approx")
                    if not src and approx is not True:
                        out.append(("error", f"#{n} {tpl} 是数字卡，但没有 source_ref 也没写 approx: true"
                                             f"——要么给出处，要么画面标「示意」"))

                # ---- 支撑功能（v5）：这张卡在论证里干什么。跟 role 是两条轴：
                # role 说「在论点的哪一步」，mg_role 说「这块图形解法了什么」。
                mgr = el.get("mg_role")
                if mgr is not None and mgr not in MG_ROLES:
                    out.append(("error", f"#{n} mg_role「{mgr}」不在约定值里"))
                elif mgr is None and ver >= 5 and tpl != "scene":
                    out.append(("warning", f"#{n} gen 没写 mg_role：说不清这张卡在论证里干什么"))
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

    # 承接通（结构判据，2026-11-11）：换介质/换模板的那一刻，必须有东西「活着变成下一个」。
    # 观众看到的是「一张换一张」就是 PPT——这是四支片被真实否决时唯一分得开的判据。
    # 在时间轴上判，不用等渲完：共享 = 同一素材延续 / 同一模板变形 / 都是口播（人还在）。
    # 共享不了就显式写 hard_cut 理由（有意为之的硬切），否则报。
    def _vis(e: dict) -> tuple:
        k = e.get("kind")
        if k == "clip":
            return ("clip", e.get("asset"))
        if k == "image":
            return ("image", e.get("asset"))
        if k == "gen":
            return ("gen", e.get("template"))
        return (k, None)

    prev_v, prev_e = None, None
    for e in els:
        if e.get("track") == "overlay":
            continue
        v = _vis(e)
        if prev_v is not None and v[0] != prev_v[0] or (prev_v is not None and v[1] != prev_v[1]):
            shared = (v[0] == prev_v[0] and v[1] == prev_v[1]) or \
                     (prev_e.get("medium") == e.get("medium") == "talking") or \
                     (e.get("medium") == "talking" and prev_e.get("medium") == "talking")
            if not shared and not str(e.get("hard_cut") or "").strip():
                out.append(("warning",
                            f"#{e.get('n')} 换到 {v[0]}/{v[1]}，和上一格 #{prev_e.get('n')} "
                            f"（{prev_v[0]}/{prev_v[1]}）没有共享元素：像「一张换一张」。"
                            f"要么做一个承接，要么写 hard_cut 说明为什么硬切"))
        prev_v, prev_e = v, e

    # ---- 画面里的第三方字样与品牌安全检查（T-007）
    from brand_safety import scan_third_party_risks
    clip_items = []
    for e in els:
        if e.get("kind") == "clip" and e.get("asset"):
            clip_items.append({
                "n": e.get("n"),
                "asset": e.get("asset"),
                "in": float(e.get("in", 0.0)),
                "out": float(e.get("out", 0.0)),
                "third_party_action": e.get("third_party_action") or e.get("third_party")
            })
    unresolved, resolved = scan_third_party_risks(clip_items, index, decisions_text=decisions_text)
    if unresolved:
        hits_str = " / ".join(f"#{r['n']} {r['tag']}：{'；'.join(r['flags'])}" for r in unresolved[:3])
        more_str = f" 等共 {len(unresolved)} 段" if len(unresolved) > 3 else ""
        out.append(("warning", f"{len(unresolved)} 段画面出现第三方品牌字样（施工工具/材料很多时候无需避开，请确认是否需要避开；若确认保留请在 decisions.md 记「无需避开/保留」，或元素标 third_party_action: keep）：{hits_str}{more_str}"))

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

    dec_f = proj / "decisions.md"
    dec_text = dec_f.read_text(encoding="utf-8") if dec_f.exists() else None
    probs = check(tl, index, strict=a.strict, decisions_text=dec_text)
    # 别人的话不许进片：场记口令 / 摄影师的指导语 / 主播自己的重说。
    # 这一条放在这里而不是只在 cue_guard 里，是因为它是**时间轴层面的错**：
    # 音频窗口一旦套住口令，剪辑方案就已经错了，后面渲得再好也是错的。
    try:
        import cue_guard
        by_id = {x.get("id"): x for x in (index.get("assets") if isinstance(index, dict) else []) or []}
        if not by_id and isinstance(index, list):
            by_id = {x.get("id"): x for x in index}
        root = lib
        for el in tl.get("elements") or []:
            au = el.get("audio") or {}
            aid = au.get("asset")
            rec = by_id.get(aid)
            if not rec:
                continue
            ws = cue_guard.words_of(rec, root)
            cues = cue_guard.find_cues(ws)
            if not cues:
                continue
            a0, a1 = float(au.get("in") or 0), float(au.get("out") or 0)
            na, nb, why = cue_guard.trim(a0, a1, cues, ws)
            stuck = [c for c in cues if na + 0.02 < c["t0"] and c["t1"] < nb - 0.02]
            if stuck:
                probs.append(("error", f"#{el.get('n')} 音频窗口里夹着口令「{stuck[0]['text']}」——"
                                       "别人的话进了片子，换素材或拆镜"))
            elif abs(na - a0) > 0.01 or abs(nb - a1) > 0.01:
                probs.append(("warning", f"#{el.get('n')} 端点踩到口令，应改成 "
                                         f"{na:.2f}–{nb:.2f}（cue_guard --fix）"))
    except Exception as exc:                      # noqa: BLE001
        probs.append(("warning", f"口令检查没跑起来：{type(exc).__name__}: {exc}"))
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
