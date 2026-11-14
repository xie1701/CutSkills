#!/usr/bin/env python3
"""CutSkills · 字幕裁判：去标点 / 呼吸组 / 单行宽度 / 强调色 / 硬切。

2026-11-09 的五条规矩（Jerry 定的）都能机器判，所以别再靠看：

  ① 不出标点        —— 一条字幕里不许有「，。？！、；：…」
  ② 不切词          —— 相邻两条时间上贴在一起（间隔 <0.12s）＝在词中间断了
  ③ 单行 10–14 字   —— 上限硬判；中位数要在 10–14 之间（全片都是 5 个字＝太碎）
  ④ 强调色要有      —— 一条都没染说明规则没生效
  ⑤ 硬切            —— 淡入必须 ≤120ms（读样式包，不看代码）

用法：
  check_subs.py --project <项目> [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexicon          # noqa: E402  残词 / 口吃重尾的判据只有一份，就写在 lexicon 里
import lint_transcript  # noqa: E402  量级判据表与待审工单同源，不另写一份

PUNCT = "，。！？、；：…—～,.!?;:"
# 判的是「中位数落在这个带里」：10–14 是拆长句的目标，短句本来就是 6–9 字。
# 真卡死的是上限（>15 一刀不放过）和「一刀两断」（断词）。
WIDTH_BAND = (6.5, 10.0)   # 上限 10 单位之后，中位落在 6.5–10 之间；7 是正常值
# 字幕横向的**像素**安全区：占画幅宽的比例上限（左右各留 3%）。单位数判的是「写了几个字」，
# 判不了「几个字 × 多大字号 = 出不出画」——模板字号一变，同样 9 个字的像素宽就差 30%。
# 0.94 是从成片量出来的口径：参考片字幕最宽 88%W，留 6 个点是给左右滑栏/安全边的余量。
SAFE_W_RATIO = 0.94
TIGHT_GAP = 0.12          # 两条字幕贴得比这还近＝在同一口气里被切开了
FADE_MAX = 0.12
OVERLAP_EPS = 0.005       # 秒：小于这个算「相接」，允许


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


HOLD_MIN, HOLD_MAX = 0.8, 3.2
# ── 字幕停留的「单呼吸组」豁免（2026-11-13 T-029）────────────────────
# 3.2s 是「一句话赖在屏幕上」的上限，但它量不出**语速**：慢语速的人真连着讲
# 3.3–4.1 秒才把 8–9 个字说完（ASR 会把一处停顿记进某个字的时长里），
# 而 `subs.py` 的规则是「没有停顿就不许拆」（GAP_BREAK = 0.30）。
# 两条规则在慢语速上互锁，永远拆不出来。
# 实测（2026-11-13，现行 subs.py 产出的 ground-01）：4 条 3.30–4.38s 的长条里，
# 1 条窗口静音 0.00s（真一口气讲完，旧判据在这里是**误报**）；
# 另外 3 条有 0.46–1.22s 真静音（ASR 把停顿算进了某个字的时长里）——那三条该判红。
# 样本与实测读数：tests/fixtures/negative-samples/hold-breath-groups.md。
#
# 所以：窗口内**实量**不到 ≥BREATH_MIN 的停顿 → 这是一口气没断，上限放到 4.5s；
#       窗口内量到停顿却仍撑过 3.2s → 那才是「话停了字还赖着」，照旧判红。
# 停顿一律用 `silence.runs()` 量真静音，**不许**拿 ASR 词起点间隔猜（ASR 会漏词）。
HOLD_MAX_SINGLE = 4.5     # 单呼吸组（窗口内最长停顿 < BREATH_MIN）的上限
BREATH_MIN = 0.30         # 与 subs.GAP_BREAK 同口径：≥这个才算「换了一口气」


def _library_of(proj: Path, given: str | Path | None = None) -> Path | None:
    """素材库根目录：显式给 > CUTSKILLS_LIB > 祖先目录里有 `_cutskills/index.json` 的那一层。

    和 `check_edl` 同一口径。找不到就是 None——量不出静音，按严格上限判，不静默放行。
    """
    if given:
        return Path(given).expanduser().resolve()
    env = os.environ.get("CUTSKILLS_LIB")
    if env and (Path(env).expanduser() / "_cutskills" / "index.json").exists():
        return Path(env).expanduser().resolve()
    for p in proj.parents:
        if (p / "_cutskills" / "index.json").exists():
            return p
    return None


def longest_pause(src: Path, i: float, o: float) -> float | None:
    """窗口 [i,o] 里最长的一段**没声音**（秒）。量不出返回 None。

    贴着窗口两头的也算：字幕先亮后撤（或先撤后亮），那半秒同样没人说话——
    「话 2 秒就说完、字幕赖到 3.3 秒」的尾巴就是这种。
    所以不做「内部才算」的过滤：安静 0.30s 以上，就是换了一口气。
    """
    import silence as SI
    rs = SI.runs_strict(Path(src), i, o, min_d=BREATH_MIN)
    if rs is None:
        return None
    return round(max((e - s for s, e in rs), default=0.0), 3)


def make_hold_probe(proj: Path, library: str | Path | None = None):
    """造一个 `probe(cue) -> 窗口内最长停顿 | None`。

    把一个字幕窗口从**成片时间**映回**素材时间**，再拿 `silence` 实量里面最长的一处停顿。
    映射要三样东西：`out/shots.json`（每格在成片里的时长，累加就是格子起点）、
    `timeline.json` / `edl.json`（每格的素材与素材内入点）、素材库根目录。

    为什么不能只看词起点间隔：ASR 会漏词，也会把停顿记进某个字的时长
    （ground-01 那句「其中有」的「有」占了 1.62s）——间隔大不等于没声音。
    这里差一个东西就返回 None（调用方按 3.2s 严格判），不拿「没量成」当「没停顿」。
    """
    shots = load(proj / "out" / "shots.json")
    if not shots:
        return None
    segs: dict = {}
    for f in ("timeline.json", "edl.json"):
        d = load(proj / f) or {}
        for el in (d.get("elements") or d.get("segments") or []):
            au = el.get("audio") or {}
            segs[el.get("n")] = (au.get("asset") or el.get("asset"),
                                  float(au.get("in", el.get("in") or 0.0)))
        if segs:
            break
    if not segs:
        return None
    lib = _library_of(proj, library)
    if lib is None:
        return None
    # 每格在成片里的区间：shots 按时长累加，同一格的多镜并成一个区间
    spans: list[tuple] = []
    t = 0.0
    for s in shots:
        d = float(s.get("dur") or 0.0)
        n = s.get("seg")
        if spans and spans[-1][0] == n:
            spans[-1] = (n, spans[-1][1], t + d)
        else:
            spans.append((n, t, t + d))
        t += d

    def probe(cue: dict) -> float | None:
        st = float(cue["start"])
        hit = None
        # 先按 el 找（现行 subs.json 都有），再退回纯时间定位（老文件没有 el）
        for sn, a_, b_ in spans:
            if sn == cue.get("el") and a_ - 1e-6 <= st < b_ + 1e-6:
                hit = (sn, a_)
                break
        if hit is None:
            for sn, a_, b_ in spans:
                if a_ - 1e-6 <= st < b_ - 1e-6:
                    hit = (sn, a_)
                    break
        if hit is None:
            return None
        sn, a_ = hit
        seg = segs.get(sn)
        if not seg or not seg[0]:
            return None
        src = lib / str(seg[0])
        i = seg[1] + (st - a_)
        o = i + (float(cue["end"]) - st)
        return longest_pause(src, i, o)

    return probe


def hold_judge(cues: list[dict], probe=None) -> tuple[list[dict], list[dict]]:
    """每条字幕的停留时间。返回 (判红, 提示)。

    有稿子的片子里句读是写字的人定的：快语速下一句话只有 0.6 秒是**事实**，
    硬拉长只能破坏句子。所以「收在句读上、0.6–0.8 秒」只记提示；判红的是
    低于 0.6 秒、超过上限、或者被宽度硬拆出来的短条。

    上限分两档（2026-11-13 T-029）：
      · 窗口内实量到 ≥BREATH_MIN 停顿（或量不出）→ HOLD_MAX = 3.2s；
      · 窗口内最长停顿 < BREATH_MIN → 单呼吸组，HOLD_MAX_SINGLE = 4.5s。
    第二档不是放宽，是承认「他一口气真的讲了这么久」：拆开反而违背
    「一段字幕必须是一个完整呼吸组」（subs.py 规则 ②）。
    没给 probe 就当量不出，一律按 3.2s 判。
    """
    bad, note = [], []
    for c in cues:
        dur = round(float(c["end"]) - float(c["start"]), 3)
        clause = str(c.get("break_after") or "") == "clause"
        limit, pause = HOLD_MAX, None
        if dur > HOLD_MAX + 0.02 and probe is not None:
            pause = probe(c)
            if pause is not None and pause < BREATH_MIN:
                limit = HOLD_MAX_SINGLE
        if dur < 0.6 or dur > limit + 0.02 or (dur < HOLD_MIN - 0.02 and not clause):
            bad.append({"text": str(c.get("text"))[:10], "s": dur,
                        "limit": limit, "pause": pause})
        elif dur < HOLD_MIN - 0.02:
            note.append({"text": str(c.get("text"))[:10], "s": dur, "why": "clause"})
        elif limit > HOLD_MAX:
            note.append({"text": str(c.get("text"))[:10], "s": dur,
                        "why": "single", "pause": pause})
    return bad, note


def dangling_judge(cues: list[dict]) -> list[dict]:
    """助词孤行：条首/条尾落在虚字上，读起来像上一句的尾巴。

    条首要看**这个词是不是被切成两半**，不能只看头一个字在不在虚字表里：
    「过嚟办公噶啦」的「过」在表里，可「过嚟」是一个词，它起头完全正常——
    照字表判，b 片那条字幕被冤枉成孤行。所以再加一条：这个字得在分词里
    自成一段（词边界落在第 1 个字之后）才算甩出来的尾巴。
    「过/着/咁」还能起句（「咁先可以坐得舒服」），不进这一条。
    """
    from subs import NO_HEAD, NO_END, word_bounds
    START_OK = set("过着咁")
    tail = []
    for c in cues:
        t = str(c.get("text") or "")
        if not t:
            continue
        style_end = str(c.get("break_after") or "") == "clause"
        wb = word_bounds(t) if len(t) > 1 else {1}
        if t[0] in NO_HEAD and t[0] not in START_OK and 1 in wb:
            tail.append({"text": t[:10], "why": f"起头是「{t[0]}」"})
        elif len(t) >= 2 and t[-1] in NO_END and not style_end:
            tail.append({"text": t[:10], "why": f"收尾是「{t[-1]}」"})
    return tail


def width_runs(cues: list[dict]) -> list[list[int]]:
    """把「被宽度硬拆开」的相邻 cue 归成一串，其余各成串。

    为什么要有这个：subs.py 按展示宽度断行，一个词会被劈到两条 cue 里（c 片的
    「我哋嘅生」+「万鱼池」）。残词/量级判据要看到「生万鱼池」才认得出错，
    而**只拼 `break_after == "width"` 的条**：那是同一句话被排版切开的证据。
    句读处硬拼会造出原文没有的邻接关系（「…好」+「生…」），冤枉好句子。
    """
    runs: list[list[int]] = []
    for i, c in enumerate(cues):
        if runs and c.get("el") == cues[runs[-1][-1]].get("el") \
                and str(cues[runs[-1][-1]].get("break_after")) == "width":
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def semantic_judge(cues: list[dict], suspects: list[dict] | None = None,
                   rules: list[dict] | None = None) -> dict:
    """上屏前的语义常识门禁：同音残词 / 口吃重尾 / 反常量级。

    为什么放在 check_subs：这三类都是「字幕上已经印出来、声音却对不上」的硬伤，
    跟标点、超宽是同一层——观众一眼看见；而且都不存在「风格选择」：
    「十万米高」「讲下下」只能是错的，留着的唯一后果就是观众看见。

    判据不在这里再写一份：残词/口吃重尾在 `lexicon.load_suspects()` 与
    `lexicon.stutter_tail()`，量级在 `data/abnormal-magnitudes.json`（由
    `lint_transcript.scan_magnitudes` 读同一张表）。工单与门禁同源，才不会出现
    「工单报了、门禁不放」「门禁拦了、工单没提」这种两张嘴。
    """
    table = suspects if suspects is not None else lexicon.load_suspects()
    sus: list[dict] = []
    stut: list[dict] = []

    for i, c in enumerate(cues):
        t = str(c.get("text") or "")
        for h in lexicon.suspect_hits(t, table):
            sus.append({"cue": i, "text": t, "hit": h["bad"], "suggest": h["good"],
                        "same_len": h["same_len"], "needs_human": h["needs_human"]})
        tail = lexicon.stutter_tail(t)
        if tail:
            stut.append({"cue": i, "text": t, "tail": tail})

    # 跨条扫：字幕被宽度硬拆开时，一个词会落在两条 cue 里——
    # dianzan-c 的「我哋嘅生」+「万鱼池」就是「生万鱼池」，逐条扫永远扫不到。
    for run in width_runs(cues):
        if len(run) < 2:
            continue
        spans, off, joined = [], 0, ""
        for j in run:
            t = str(cues[j].get("text") or "")
            spans.append((off, off + len(t), j))
            joined += t
            off += len(t)
        for h in lexicon.suspect_hits(joined, table):
            end = h["at"] + len(h["bad"])
            if any(h["at"] >= a and end <= b for a, b, _ in spans):
                continue                      # 单条里看得见，上面已经报过
            sus.append({"cue": run[0], "cue_to": run[-1], "text": joined,
                        "hit": h["bad"], "suggest": h["good"],
                        "same_len": h["same_len"], "needs_human": h["needs_human"]})

    # 量级：荒谬档判红，可疑档只提——「三十米高的中庭」是真话，判红等于逼人改真台词。
    mags = lint_transcript.scan_magnitudes(
        [(str(i), str(c.get("text") or "")) for i, c in enumerate(cues)], rules)
    return {"suspect": sus, "stutter": stut,
            "magnitude_bad": [m for m in mags if m["tier"] == "absurd"],
            "magnitude_note": [m for m in mags if m["tier"] == "suspicious"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None,
                    help="素材库根（量字幕窗口里的真静音要用）；默认从祖先目录找 _cutskills/index.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    d = load(proj / "out" / "subs.json")
    if not d:
        raise SystemExit("还没有 out/subs.json（先跑 render_hf.py）")
    cues = d.get("cues") or []

    # 生效的字幕口径：项目显式选的模板 > 包装包自带的 subtitle 参数 > 默认模板。
    # 包装包的解析顺序与 render_hf 一致（brief.pack → packs/，老项目退回 styles/style_pack），
    # 判据量的必须是**画上去的那一套**，两边各猜一套的话，判红判绿都失去意义。
    bp = load(proj / "brief.json") or {}
    root = Path(__file__).resolve().parent.parent
    tl_doc = load(proj / "timeline.json") or {}
    style = None
    # 与 render_hf 同顺序：时间轴的 pack > brief 的 pack > 老的 style_pack（timeline 优先）
    _pid = str(tl_doc.get("pack") or bp.get("pack") or "").strip()
    _sp = tl_doc.get("style_pack") or bp.get("style_pack")
    for _cand in ([root / "packs" / f"{_pid}.yaml"] if _pid else []) + \
                 ([root / "styles" / f"{_sp}.yaml"] if _sp else []):
        if _cand.exists():
            import yaml
            style = yaml.safe_load(_cand.read_text())
            break
    try:
        import subs as _subs_mod
        eff = _subs_mod.subtitle_config(proj, style)
    except _subs_mod.SubtitleTemplateError as e:
        print(f"字幕模板配置有问题：{e}", file=sys.stderr)
        return 1
    sub = (style or {}).get("subtitle") or {}
    fade = float(sub.get("fade_s") or 0.10)

    punct = [c for c in cues if any(ch in PUNCT for ch in str(c.get("text") or ""))]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from subs import (HAVE_JIEBA, word_bounds, display_units, MAX_UNITS,  # noqa: E402
                      NO_HEAD, NO_END)

    def _u(c) -> float:
        return float(c.get("units") if c.get("units") is not None
                     else display_units(str(c.get("text") or "")))

    units = [_u(c) for c in cues]
    # 行宽上限按**生效的那一套**判：模板 > 包装包 > 默认模板。
    # 注意判据本身没松：模板的 max_units_per_line 在加载时就被钉在 ≤ MAX_UNITS（只许收窄）。
    mu = float(eff.get("max_units_per_line") or MAX_UNITS)
    wide = [c for c in cues if _u(c) > mu + 0.4]
    # 像素安全区：字号 × 字宽比 × 单位数，看最宽那条会不会出画。
    # 模板换了字号（72/96/64/66px），单位数一样、像素宽差三成——这条判据就是补这个洞，
    # 也是「选了模板之后按模板口径判」能看见差别的地方。
    _cw = int((eff.get("canvas") or {}).get("width") or 1080)
    _ratio, _fp = 1.0, None
    try:
        import fonts as _fonts                                         # noqa: PLC0415
        _fp = _fonts.resolve(eff.get("font")) or None
        _ratio = float((_fp or {}).get("ratio") or 1.0)
    except Exception:                                                  # noqa: BLE001
        _ratio = 1.0
    _sz = float(eff.get("render_size_px") or eff.get("size_px") or 72)
    _ls = float(eff.get("letter_spacing") or 0.0)
    widest_px = round(max((u * _sz * _ratio + max(0.0, u - 1.0) * _ls * _sz
                           for u in units), default=0.0), 1)
    safe_px = round(_cw * SAFE_W_RATIO, 1)
    # 选了模板就要真按模板渲：subs.json 里记的模板与 brief.json 选的不一致 = 这一版是旧的
    _want_tpl = None
    try:
        _want_tpl = str((bp or {}).get("subtitle_template") or "").strip() or None
    except AttributeError:
        _want_tpl = None
    _got_tpl = str(d.get("subtitle_template") or "").strip() or None
    tpl_match = (not _want_tpl) or (_got_tpl == _want_tpl)
    _em_want = str(eff.get("em_color") or "")
    _em_got = str(d.get("em_color") or "")
    em_match = (not _em_want) or (not _em_got) or (_em_want.lower() == _em_got.lower())
    widths = sorted(units)
    med = widths[len(widths) // 2] if widths else 0
    em = sum(1 for c in cues if "<span class" in str(c.get("html") or ""))

    # 每条的字幕停留 0.8–3.2 秒（对齐 srt-motion-cards 的规范）。
    # 低于 0.8s 是「闪一下」：眼睛抓不住，比没有更累；超过 3.2s 是「一句话赖在屏幕上」，
    # 在口播片里通常意味着这一格没有被剪好，而不是字幕该停这么久。
    # 有稿子的片子里，句读是写的人定的：快语速下一句话只有 0.6 秒是**事实**，
    # 硬把它拉长只能破坏句子。所以「收在句读上、并且 0.6–0.8 秒」记为提示，
    # 真正判红的是：低于 0.6 秒，或者被宽度硬拆出来的短条（那种本来可以避免）。
    #
    # 3.2s 是「赖在屏幕上」的上限，不是语速的上限：慢语速的人一口气真能连着说 3.3–4.1 秒，
    # 而拆条规则（subs.GAP_BREAK）在不许切的地方不切。所以超过 3.2s 的条要先**量**窗口里
    # 有没有真停顿（silence.runs，不看词起点间隔）：量不到 ≥0.30s 停顿 = 单呼吸组，
    # 上限放到 4.5s；量得到停顿却仍超过 3.2s = 话停了字还赖着，照旧判红。
    hold_probe = make_hold_probe(proj, a.library)
    hold_bad, hold_note = hold_judge(cues, probe=hold_probe)
    hold_single = [x for x in hold_note if x.get("why") == "single"]

    # 助词孤行：判据抽到 dangling_judge()，验收层也要跑同一条，别两边各写一遍。
    tail = dangling_judge(cues)

    # 半开帧窗零重叠（2026-11-11）：字幕重叠是画面上**必然**看得见的错，
    # 不该靠事后审计发现，也不该允许被 --force 越过。老 subs.json 没有 sf/ef，
    # 先按秒判（等价），并提示重渲。
    has_frames = all(isinstance(c.get("sf"), int) and isinstance(c.get("ef"), int) for c in cues) and bool(cues)
    overlap = []
    for prev, nxt in zip(cues, cues[1:]):
        if has_frames:
            if int(nxt["sf"]) < int(prev["ef"]):
                overlap.append({"a": str(prev.get("text"))[:8], "b": str(nxt.get("text"))[:8],
                                "frames": int(prev["ef"]) - int(nxt["sf"])})
        elif float(nxt["start"]) < float(prev["end"]) - OVERLAP_EPS:
            overlap.append({"a": str(prev.get("text"))[:8], "b": str(nxt.get("text"))[:8],
                            "ms": round((float(prev["end"]) - float(nxt["start"])) * 1000)})
    bad_frames = [c for c in cues if has_frames and int(c["ef"]) <= int(c["sf"])]
    # 变色是可以关的（Jerry 2026-11-09：「字幕的重点词变色还是取消」）。关了就要
    # 一条都没有——不能因为「规则没染上」也算过。
    colorize = bool(d.get("colorize", True))
    # 断词要按**词边界**判，不能按「断在哪儿」判。
    # 原来只盯 break_after == "width"，于是 ASR 句子边界落在词中间那种情况一路绿灯：
    # 「墙面或者地面或者任何表」+「面的让他成为成品就」把「表面」劈成两半，
    # 而裁判报「断词 ✅」。判据得是：把两条接回去，切开的位置在不在词与词之间。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from subs import HAVE_JIEBA, word_bounds  # noqa: E402,F811

    tight = []
    for prev, nxt in zip(cues, cues[1:]):
        # 只比**同一格**里的相邻两条：跨段的贴边是切点造成的，不是把词劈开
        if prev.get("el") != nxt.get("el"):
            continue
        a_, b_ = str(prev.get("text") or ""), str(nxt.get("text") or "")
        if not a_ or not b_:
            continue
        gap = float(nxt["start"]) - float(prev["end"])
        if gap > 0.40:                       # 中间空了一大截，这两条本来就不连着
            continue
        # 在稿子的逗号处断的不算断词：那里本来就是两个小句，
        # 接回去当然「不是一个词」——判据要盯的是被宽度硬拆的地方。
        if str(prev.get("break_after") or "") == "clause":
            continue
        if HAVE_JIEBA:
            bad = len(a_) not in word_bounds(a_ + b_)
        else:                                 # 没分词器只能退回老判据
            bad = prev.get("break_after") == "width" and gap < TIGHT_GAP
        if bad:
            tight.append({"a": a_[-6:], "b": b_[:6], "gap_ms": round(gap * 1000)})


    # 语义常识门禁（T-017）：残词 / 口吃重尾 / 荒谬量级。
    # 这三类都是「字幕上已经印出来」的硬伤，与标点超宽同一层；
    # 荒谬量级判红、可疑量级只提（真话也会落在可疑档）。
    sem = semantic_judge(cues)

    checks = {
        "punct": {"value": len(punct), "pass": not punct, "need": 0},
        "over_wide": {"value": len(wide), "pass": not wide,
                      "need": f"<={mu:g} 单位"
                              + ("" if eff.get("template") else "（包装包口径）")},
        "text_width_px": {"value": widest_px, "pass": widest_px <= safe_px,
                          "need": f"<={safe_px:g}px（{SAFE_W_RATIO:.0%}W，{_sz:g}px×{_ratio:g}）"},
        "template_applied": {"value": _got_tpl or eff.get("id") or "—", "pass": tpl_match,
                             "need": (_want_tpl or "—") + "（brief 选的 = 这一版渲的）"},
        "em_color": {"value": _em_got or _em_want or "—", "pass": em_match,
                     "need": (_em_want or "—") + "（强调色与生效口径一致）"},
        "width_median": {"value": round(med, 1), "pass": WIDTH_BAND[0] <= med <= WIDTH_BAND[1],
                         "need": WIDTH_BAND},
        "word_split": {"value": len(tight), "pass": not tight, "need": 0},
        "overlap": {"value": len(overlap), "pass": not overlap, "need": 0},
        # 老 subs.json 没有 sf/ef：缺数据不等于违规，标 stale 让它显形，但不判红。
        "frames": {"value": len(bad_frames), "pass": not bad_frames,
                   "need": "sf<ef（半开窗）", "stale": (not has_frames) and bool(cues)},
        "emphasis_cues": ({"value": em, "pass": em > 0, "need": ">0"} if colorize
                          else {"value": em, "pass": em == 0, "need": "=0（已取消变色）"}),
        "fade_s": {"value": fade, "pass": fade <= FADE_MAX, "need": f"<={FADE_MAX}"},
        "hold": {"value": len(hold_bad), "pass": not hold_bad,
                 "need": f"每条 {HOLD_MIN}–{HOLD_MAX}s（单呼吸组 {HOLD_MAX_SINGLE}s）",
                 "note": [f"{x['text']} {x['s']}s" for x in hold_note[:3]],
                 "single_breath": [f"{x['text']} {x['s']}s" for x in hold_single[:5]],
                 "probed": hold_probe is not None},
        "dangling": {"value": len(tail), "pass": not tail, "need": "助词不孤行"},
        # 未修正的嫌疑残词：命中就是「声音里说的是 A，字幕印的是 B」
        "suspect_words": {"value": len(sem["suspect"]), "pass": not sem["suspect"],
                          "need": "0（命中即改，或改嫌疑表）"},
        "stutter_tail": {"value": len(sem["stutter"]), "pass": not sem["stutter"],
                         "need": "0（句尾不许叠字）"},
        "magnitude": {"value": len(sem["magnitude_bad"]), "pass": not sem["magnitude_bad"],
                      "need": "0 处荒谬量级",
                      "note": [f"{x['hit']} {x['value']:g}{x['unit']}"
                               for x in sem["magnitude_note"][:3]]},
    }
    ok = all(v["pass"] for v in checks.values())
    rep = {"cues": len(cues), "checks": checks, "ok": ok,
           "punct": [c.get("text") for c in punct[:5]],
           "over_wide": [(c.get("text"), _u(c)) for c in wide[:5]],
           "word_split": tight[:5],
           "overlap": overlap[:5],
           "frames": "✅ 半开帧窗" if has_frames else "老文件没有 sf/ef，重渲后生效",
           "hold": hold_bad[:5], "dangling": tail[:5],
           "hold_single_breath": hold_single[:5], "hold_probed": hold_probe is not None,
           "semantic": {"suspect": sem["suspect"][:8], "stutter": sem["stutter"][:8],
                        "magnitude_bad": sem["magnitude_bad"][:8],
                        "magnitude_note": sem["magnitude_note"][:8]},
           "widths": {"min": widths[0] if widths else 0, "max": widths[-1] if widths else 0,
                      "median": round(med, 2), "unit": "汉字宽",
                      "widest_px": widest_px, "safe_px": safe_px,
                      "size_px": round(_sz, 1), "ratio": _ratio},
           # 判的是哪一套字幕样式（模板 id / 包装包 / 默认模板）——读数要能对上脸
           "subtitle_style": {"source": eff.get("source"), "id": eff.get("id"),
                              "font": eff.get("font"), "size_px": eff.get("size_px"),
                              "max_units": mu, "em_color": _em_want or None,
                              "license": eff.get("font_license")}}

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if ok else 1

    if eff.get("template"):
        print(f"字幕口径：模板「{eff.get('name')}」{eff.get('id')} · {eff.get('font')} "
              f"{_sz:g}px · 授权 {eff.get('font_license')} · 行宽 ≤{mu:g} 单位")
    else:
        print(f"字幕口径：包装包自带参数（{eff.get('font')} · 行宽 ≤{mu:g} 单位）")
    print(f"字幕 {len(cues)} 条 · 宽度 中位 {med:.1f} 单位（{widths[0] if widths else 0}–"
          f"{widths[-1] if widths else 0}） · 最宽 {widest_px:g}px"
          f"（{widest_px / _cw:.0%}W，安全区 ≤{safe_px:g}px）")
    print(f"  标点 0 处{'✅' if not punct else '✗ ' + str(len(punct))}")
    print(f"  超宽（>{mu:g} 展示单位）{'✅' if not wide else '✗ ' + str(len(wide))}"
          f" · 像素 {widest_px:g}/{safe_px:g}px{'✅' if widest_px <= safe_px else '✗'}")
    print(f"  断词 {'✅' if not tight else '✗ ' + str(len(tight))}（贴着切的，像同一口气被劈开）")
    print(f"  重叠 {'✅' if not overlap else '✗ ' + str(len(overlap))}"
          f" · 帧窗 {'✅' if has_frames and not bad_frames else ('✗' if bad_frames else '老文件')}")
    _ok = (em > 0) if colorize else (em == 0)
    print(f"  停留 {'✅' if not hold_bad else '✗ ' + str(len(hold_bad))}"
          f"（每条 {HOLD_MIN}–{HOLD_MAX}s；单呼吸组可到 {HOLD_MAX_SINGLE}s）")
    if hold_single:
        print(f"    · 单呼吸组放行 {len(hold_single)} 条（窗口内静音 <{BREATH_MIN}s）："
              + "、".join(f"{x['text']} {x['s']}s" for x in hold_single[:3]))
    elif hold_probe is None and any(
            float(c["end"]) - float(c["start"]) > HOLD_MAX + 0.02 for c in cues):
        print(f"    · 静音量不了（没 shots.json / 素材库不在本机），超 {HOLD_MAX}s 的条按严格上限判")
    print(f"  助词孤行 {'✅' if not tail else '✗ ' + str(len(tail))}"
          + ("" if not tail else f"  例：{tail[0]['text']}（{tail[0]['why']}）"))
    print(f"  残词 {'✅' if not sem['suspect'] else '✗ ' + str(len(sem['suspect']))}"
          f"（同音残词未修正）")
    for h in sem["suspect"][:4]:
        where = f"#{h['cue']}" if "cue_to" not in h else f"#{h['cue']}–#{h['cue_to']}"
        tail_note = ("（待人工拍板）" if h["needs_human"]
                     else "" if h["same_len"] else "（不等长，需人工）")
        print(f"  · {where}「{h['hit']}」→「{h['suggest']}」{tail_note}")
    print(f"  口吃重尾 {'✅' if not sem['stutter'] else '✗ ' + str(len(sem['stutter']))}"
          + ("" if not sem["stutter"] else
             "  例：" + "、".join(f"#{x['cue']}「{x['tail']}」" for x in sem["stutter"][:3])))
    print(f"  量级 {'✅' if not sem['magnitude_bad'] else '✗ ' + str(len(sem['magnitude_bad']))}"
          f"（荒谬判红 / 可疑只提 {len(sem['magnitude_note'])}）")
    for m in sem["magnitude_bad"][:3]:
        print(f"  · #{m['aid']}「{m['hit']}」= {m['value']:g}{m['unit']}（{m['rule']}）")
    for m in sem["magnitude_note"][:3]:
        print(f"  · 可疑 #{m['aid']}「{m['hit']}」= {m['value']:g}{m['unit']}——看一眼再定")
    print(f"  强调色 {em} 条{'✅' if _ok else '✗'}{'' if colorize else '（已取消）'}"
          f" · 淡入 {fade:.2f}s{'✅' if fade <= FADE_MAX else '✗'}")
    for t in [c.get("text") for c in punct[:3]]:
        print(f"  · 带标点：{t}")
    for a_, b_, ms in [(x["a"], x["b"], x["gap_ms"]) for x in tight[:3]]:
        print(f"  · 断词：「{a_}」+「{b_}」间隔 {ms}ms")
    print(f"结论：{'过' if ok else '没过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
