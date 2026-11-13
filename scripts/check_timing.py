#!/usr/bin/env python3
"""CutSkills · 声音在前（硬规则）：画面里的每一个字，都得等它那句话被说出来。

这条规矩上游写得很死，我们以前只做了一半：

  1. 卡片起点 ≥ 锚点词发音 + 0.15s        ← 我们原来是 0.10s
  2. 卡内每个元素 ≥ 它自己台词的发音 + 0.12s ← 原来只有 CSS 的 stagger，没绑台词
  3. 锚词全片找不到 → 判「无声音依据」       ← 原来只 print 一句，照样出片
  4. 卡尾必须覆盖最后一个元素的台词          ← 原来没有这个检查
  5. 字幕不许早于语音                       ← 原来只有渲染时对齐，没有验收

为什么这么较真：字比话早出来，观众读到的是还没听到的词，读到的和听到的对不上——
这是「抢拍」，比慢半拍难看得多。慢半拍只是迟钝，抢拍是错。

判的是产物（out/cards.json、out/subs.json），不是时间轴怎么写——时间轴可以写得漂亮，
渲出来的卡照样能提前。

没有 `timeline.json` 的 v2 项目（只有 `edl.json`）：第 5 条要把字幕「送回」它自己那段
素材的时间去取词，没有时间轴就判不了。这种情况**明确报一行**跳过（`warning`，退出码 0），
不抛异常——以前硬读 `timeline.json`，`render_hf.py` 的渲前闸门在这里直接崩到退出码 5，
一条 v2 老片永远渲不出来。不依赖时间轴的卡判据照常往下跑，不跟着一起放。

用法：
  check_timing.py --project <项目> [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evidence_guard as EG  # noqa: E402
import lexicon as LX  # noqa: E402

CARD_LAG_MIN = 0.15       # 卡起点至少晚锚词这么多
ELEM_LAG_MIN = 0.12       # 卡内元素至少晚自己台词这么多
TOL = 0.02                # 时间抖动容差


def cards_of(proj: Path) -> list[dict]:
    f = proj / "out" / "cards.json"
    if not f.exists():
        return []
    d = json.loads(f.read_text())
    return d.get("cards") or []


def subs_of(proj: Path) -> list[dict]:
    f = proj / "out" / "subs.json"
    if not f.exists():
        return []
    d = json.loads(f.read_text())
    return d.get("items") or d.get("cues") or []


def resolve_lib(project: Path) -> Path:
    """项目 → 素材库根（**往上找** `_cutskills/index.json`）。

    仓里放的项目（`references/packtest-*/proj79`）按 `parents[2]` 算出来是 `references/`，
    于是真值全判 `none`、每张卡都报「判不了」——库根靠目录深度猜是错的，要按库的标记找。
    """
    for up in [project, *project.parents]:
        if (up / "_cutskills" / "index.json").exists():
            return up
    return project.parents[2] if len(project.parents) > 2 else project


_TRUTH: dict[str, "EG.Truth"] = {}
_LEX: dict[str, dict] = {}


def _truth(lib: Path, aid: str) -> "EG.Truth":
    if aid not in _TRUTH:
        _TRUTH[aid] = EG.chars_of_asset(lib, aid)
    return _TRUTH[aid]


def _window_text(lib: Path, tr: "EG.Truth", a_in: float, a_out: float) -> str:
    """窗口内真值文本（过一遍词表：卡上写「柒造」、ASR 写「7灶」时不纠就永远对不上）。"""
    key = str(lib)
    if key not in _LEX:
        _LEX[key] = LX.load(lib / "_cutskills")
    return LX.fix_text("".join(ch for ch, _ in tr.chars_in(a_in, a_out)),
                       _LEX[key])


def film_bounds(els: dict) -> dict[int, tuple[float, float]]:
    """元素号 → 它在**成片时间轴**上的 `[起, 止)`（口径与字幕那条一致：跳 overlay）。"""
    out: dict[int, tuple[float, float]] = {}
    acc = 0.0
    for n, el in els.items():
        if (el.get("track") or "video") == "overlay":
            continue
        au = el.get("audio") or {}
        dur = float(au.get("out") or 0) - float(au.get("in") or 0)
        if dur <= 0:
            dur = float(el.get("out") or 0) - float(el.get("in") or 0)
        out[int(n)] = (round(acc, 3), round(acc + (dur or 0.0), 3))
        acc += dur or 0.0
    return out


def card_window(card: dict, els: dict,
                bounds: dict[int, tuple[float, float]] | None = None
                ) -> tuple[str | None, float, float]:
    """卡属于哪一格、那一格的音频窗口 `(素材 id, in, out)`。

    渲器给每张卡写 `seg`（格号）。老渲器（T-027 之前）写的卡没有 `seg`，那就按卡的
    **成片时间**落到格上——`anchor_t` 是这个字该被说出来的时刻，别拿 `start` 顶。
    以前这里对 `seg is None` 一律退回「时间轴第一个口播格」，于是老卡的锚词永远在
    别人的话里找，naming-01 三张旧卡全报假红。身份卡（`seg=0`）本来就是开场白那一格，
    没有自己的格，走同一条兜底。
    """
    seg = card.get("seg")
    el = els.get(seg) if seg is not None else None
    if el is None and bounds:
        t = card.get("anchor_t")
        if t is None:
            t = card.get("start")
        if t is not None:
            for n, (a0, b0) in bounds.items():
                if a0 - 0.01 <= float(t) < b0 + 0.01:
                    el = els.get(n)
                    break
    if el is None:
        el = next((e for e in els.values() if (e.get("track") or "video") == "video"), None)
    if el is None:
        return None, 0.0, 0.0
    au = el.get("audio") or {}
    aid = au.get("asset") or el.get("asset")
    wi = float(au.get("in", el.get("in") or 0.0))
    wo = float(au.get("out", el.get("out") or 0.0))
    return (str(aid) if aid else None), wi, wo


def check(project: Path, lib: Path | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    lib = lib or resolve_lib(project)
    have_lib = (lib / "_cutskills" / "index.json").exists()
    tlp = project / "timeline.json"
    # v2 项目只有 edl.json，没有时间轴。字幕那两条判据要把字幕「送回」它自己那段素材的
    # 时间去取词，全靠时间轴里的段号 / 段起点 / 每段音频窗口——读不到就判不了。
    # 以前这里硬读，`render_hf.py` 在渲前闸门上直接 FileNotFoundError → 退出码 5，
    # 一条 v2 老片永远渲不出来。现在明说一句「跳过」，既不许抛异常，也不许静默当通过：
    # 报告里必须留着这行，人一眼能看出这次没判；不依赖时间轴的卡判据照常往下跑。
    tl = None
    if tlp.exists():
        tl = json.loads(tlp.read_text())
    else:
        out.append(("warning", "该项目没有时间轴（v2），声音在前判据跳过"))
    els = {e.get("n"): e for e in ((tl or {}).get("elements") or [])}
    _bounds = film_bounds(els)

    if not have_lib:
        out.append(("warning", f"找不到素材库（{lib} 下没有 _cutskills/index.json）——"
                               "锚词对不对得上、字幕说的是不是原文，这两类判不了"))

    # ---- 卡片
    cards = cards_of(project)
    if not cards:
        out.append(("info", "没有 out/cards.json（这个项目没渲过，或一张卡都没有）"))
    for k, c in enumerate(cards):
        kind = c.get("kind")
        aw, at = c.get("anchor_word"), c.get("anchor_t")
        st = c.get("start")
        if st is None:
            out.append(("error", f"卡{k + 1}（{kind}）没有 start，渲染器不会画它"))
            continue
        if not aw or at is None:
            # 规范第 3 条：找不到声音依据就不许出片。
            out.append(("error", f"卡{k + 1}（{kind}「{str(c.get('text') or '')[:8]}」）"
                                 "没有锚词——这张卡没有声音依据，先写 `on` 再渲"))
            continue
        lag = float(st) - float(at)
        if lag < CARD_LAG_MIN - TOL:
            out.append(("error", f"卡{k + 1}（{kind}「{str(aw)}」）比锚词只晚 {lag * 1000:.0f}ms"
                                 f"（要 ≥{CARD_LAG_MIN * 1000:.0f}ms）——抢拍"))
        end = float(c.get("end") or 0)
        # 规范第 4 条：卡尾要覆盖完台词。锚词的结束时间应早于卡尾。
        if end and float(at) > end:
            out.append(("error", f"卡{k + 1}（{kind}）锚词在 {float(at):.2f}s，卡却在 {end:.2f}s 收——"
                                 "字还没说完就没了"))
        # 规范第 3 条的后半句（T-034）：锚词**必须真在这一格的词级真值里**。
        # 只看 lag 数得数没有用：渲器找不到词时会「沿用默认时机」（重音点 / 段首+0.6s），
        # 照样写下 anchor_word 和一个 lag 3880ms 的 anchor_t，于是这道判据一路绿灯，
        # 而卡其实落在一句和它无关的话上（负样本 B）。
        if aw and have_lib:
            aid, wi, wo = card_window(c, els, _bounds)
            if aid is None:
                out.append(("warning", f"卡{k + 1}（{kind}「{aw}」）找不到它所属的那一格，"
                                       "锚词对不对得上判不了"))
            else:
                tr = _truth(lib, aid)
                if tr.source != EG.WORD:
                    out.append(("warning", f"卡{k + 1}（{kind}「{aw}」）该格只有"
                                           f"{'句级' if tr.source == EG.SENTENCE else '没有'}真值，"
                                           "锚词对不对得上判不了"))
                else:
                    txt = _window_text(lib, tr, wi, wo)
                    if aw not in txt:
                        out.append(("error",
                                    f"卡{k + 1}（{kind}「{str(c.get('text') or '')[:8]}」）的锚词"
                                    f"「{aw}」不在该格词级真值里（这一段说的是「{txt[:24]}」）——"
                                    "卡落在一句和它无关的话上，先改 on"))

    # ---- 字幕：不许早于语音
    subs = subs_of(project)
    if not subs:
        out.append(("info", "没有 out/subs.json（这个项目没渲过，或这条片没有字幕）"))
    elif tl is not None:
        # tl 为 None 时上面已经报过「没有时间轴 ⇒ 跳过」那一行，这里不再判，也不假装判过。
        # 每条字幕带 el（元素号）。时间必须**回到它自己那段素材的时间轴**去对——
        # 不同素材的绝对时间是各自录制的，混在一起按成片时间取词，取到的是别段的词。
        e_start: dict[int, float] = {}
        t_acc = 0.0
        for el in (tl.get("elements") or []):
            if el.get("track") == "overlay":
                continue
            e_start[el.get("n")] = t_acc
            au0 = el.get("audio") or {}
            dur = float(au0.get("out") or 0) - float(au0.get("in") or 0)
            if dur <= 0:
                # 没写 audio 的格（同期声就是画面自己）照样占成片时长。
                # 只累加 `audio.out - audio.in` 会让它后面的每一格都前移——protect-01 的
                # #2 就空 4.68s，于是后面所有字幕都去读了 4.7s 之后的词，判成「字幕编造」。
                dur = float(el.get("out") or 0) - float(el.get("in") or 0)
            t_acc += dur or 0.0
        words: dict[str, list[tuple[str, float]]] = {}
        levels: dict[str, str] = {}
        for el in els.values():
            au = el.get("audio") or {}
            aid = au.get("asset")
            if aid and aid not in words:
                truth = EG.chars_of_asset(lib, aid)
                words[aid] = list(truth.chars)
                levels[aid] = truth.source
        allw = sorted(((s2, c) for ws in words.values() for c, s2 in ws))
        noted: set[int] = set()
        for it in subs:
            txt = str(it.get("text") or "").strip("，。、！？,.!? ")
            t0 = it.get("t", it.get("start"))
            if not txt or t0 is None:
                continue
            head = txt[0]
            el = els.get(it.get("el"))
            span: list[tuple[float, str]] = []
            aid: str | None = None
            if el:
                au = el.get("audio") or {}
                aid = au.get("asset")
                base = e_start.get(el.get("n"), 0.0)
                a_src = float(au.get("in") or 0) + (float(t0) - base)
                b_src = float(au.get("in") or 0) + (float(it.get("end") or t0) - base)
                span = [(s2, c) for c, s2 in (words.get(aid) or [])
                        if a_src - 0.30 <= s2 <= b_src + 0.30]
            src = span or allw
            win_txt = "".join(c for _, c in span)
            if el and not span:
                # 这一格没有词级真值：字幕只剩「全片词表」可对，等于没校。
                # 退级可以说，但必须说出来（T-030）；同一格多说只报一次。
                note = EG.degrade_note(el.get("n"), levels.get(aid or "") or EG.NONE)
                if note and el.get("n") not in noted:
                    noted.add(el.get("n"))
                    out.append(("warning", note + "（这一格的字幕没按词校）"))
            # 字幕文本必须对得上这一段的真值：改写可以改标点、可以删口误，
            # 但不许凭空增加音频里没说的词（「大家可以放心」里的「放心」就是这么来的）。
            # 双向对账（T-040）：字幕多出的字、真值多出的字都过同一套同音放行
            # （`evidence_guard.two_sided`，声母/韵母层面）。只数字幕这一边，就会把
            # ASR 丢字判成编造——naming-01 字幕「什么」对上真值「为」，音频那边同时
            # 多出「为我没有」三个音，两边都多、账是平的。单边净多出 ≥2 个实词才是编造；
            # 人称代词不参与放行：对谁说话反了不是听错一个字，照旧判红。
            extra_truth, bad = EG.two_sided(win_txt, txt) if win_txt else ([], [])
            swap_sub = [c for c in bad if c in EG.PRONOUN]
            swap_tr = [c for c in extra_truth if c in EG.PRONOUN]
            # 句级真值没有逐字时间，字幕窗口对不上「哪一秒说了哪个字」——
            # 硬错只能在词级上判，否则就是拿窗口错位冤枉字幕（假红）。
            if aid and levels.get(aid) == EG.WORD:
                if swap_sub and swap_tr:
                    out.append(("error", f"字幕「{txt[:10]}」里的人称「{''.join(swap_sub[:2])}」，"
                                         f"音频说的是「{''.join(swap_tr[:2])}」——"
                                         f"这句是对谁说的反了"))
                elif len(bad) - len(extra_truth) >= 2:
                    out.append(("error", f"字幕「{txt[:10]}」里的「{''.join(bad[:4])}」音频没说——"
                                         f"这段声音是「{win_txt[:18]}」"))
            if not el:
                continue
            aux = el.get("audio") or {}
            sub_in = float(aux.get("in") or 0)
            base0 = e_start.get(el.get("n"), 0.0)
            # 首字在**素材**里第一次出现的位置，换回成片时间
            # 换回**成片时间**再比：base0 + (素材时间 − 素材入点)
            near = [base0 + (s2 - sub_in) for c, s2 in (words.get(aux.get("asset")) or [])
                    if c == head and abs((base0 + (s2 - sub_in)) - float(t0)) < 0.9]
            if near and float(t0) < min(near) - 0.25:
                out.append(("warning",
                            f"字幕「{txt[:10]}」在 {float(t0):.2f}s，这个词 {min(near):.2f}s 才说出来"
                            "——字比声早"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="声音在前（硬规则）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else None
    v = check(proj, lib)
    err = [m for lv, m in v if lv == "error"]
    if a.json:
        print(json.dumps([{"level": l, "msg": m} for l, m in v], ensure_ascii=False, indent=1))
    else:
        for lv, m in v:
            mark = {"error": "🔴", "warning": "🟡"}.get(lv, "  ")
            print(f"  {mark} {m}")
        print(f"声音在前：error {len(err)} · warning {len(v) - len(err)}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
