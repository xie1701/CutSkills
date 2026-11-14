#!/usr/bin/env python3
"""「我替你做的决定」清单：系统替用户按默认值走掉的每一步，逐条摊在明面上。

为什么要它：BRIEF 的交互约束是「一条片最多打断人 3 次，其余全用默认值推进」。
默认值不写出来，就成了暗箱——用户看到成片才发现「片长是它自己定的」「BGM 是它自己挑的」，
然后只能整段重来。这份清单把每条默认值写成三列：决定性的一行话、依据（真从哪个文件读到的）、
以及**一句话怎么改**。

来源全部是真读，不写死文案：
  · 账号轨        <项目>/account.json（track.py 判出的门店/博主与线索原话）
  · 片长与节奏档  brief.json 的 length_s/length_band · timeline.json 的 duration_s/plan · plans/<档>.yaml
  · 包装包        timeline.json 的 pack → packs/<id>.yaml（画幅/框架/字幕系统/look_notes）
  · BGM 与音效    bgm.json · out/sfx.json · out/sfx_manifest.json
  · 素材取舍      timeline.json 每格的 why/grade_at_cut · 素材库 _cutskills/index.json 的镜头评级
                  · decisions.md 的逐行留痕
  · 调色与响度    packs/<id>.yaml 的 grade/audio · hf/meta.json 的实测 LUFS
  · 字幕版式      packs/<id>.yaml 的 subtitle（字号基线/行宽/字心）· out/subs.json 实测

读不到就写「⚠ 取不到：为什么」，**不许消失，也不许把没做的决定写成做了**。

用法：
  assumptions.py --project <项目目录>              # 扫描并写 <项目>/assumptions.md
  assumptions.py --project <项目目录> --show       # 顺便打到终端
  assumptions.py --project <项目目录> --top 4      # 首屏放几条（默认 4：片长/包装/BGM/账号轨）
  assumptions.py --project <项目目录> --out /tmp/x.md

退出码：0 写成 / 2 缺 timeline.json（没有时间轴就没什么可摊的）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))
try:
    import yaml
except ImportError:                                        # pragma: no cover
    yaml = None

from libs import resolve_lib                               # noqa: E402

NA = "⚠ 取不到"
# 首屏顺序：BRIEF 里最常被改的四条（片长 / 包装 / BGM / 账号轨），其余折叠在后面
TOP_ORDER = ("length", "pack", "bgm", "track")
# 折叠区的顺序：按「一改就要重排/重渲」的代价从大到小
REST_ORDER = ("tempo", "takes", "subs", "sfx", "grade", "loudness")


def _j(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _y(p: Path) -> dict:
    if yaml is None or not Path(p).exists():
        return {}
    try:
        return yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _clip(s, n: int = 80) -> str:
    """表格里的一格：压成一行、去掉竖线（否则 Markdown 表会错列）。"""
    t = " ".join(str(s or "").split()).replace("|", "／")
    return t if len(t) <= n else t[:n] + "…"


def _row(key: str, what: str, basis: str, fix: str, *, na: bool = False) -> dict:
    """一条决定。三列都必须真：`fix` 永远非空（哪怕这条没做过也要给出改法）。"""
    return {"key": key, "what": _clip(what, 160), "basis": _clip(basis, 300),
            "fix": _clip(fix, 200), "na": na}


def _na_row(key: str, what: str, why: str, fix: str) -> dict:
    return _row(key, what, f"{NA}：{why}", fix, na=True)


# ---------------------------------------------------------------- 各来源

def row_track(proj: Path) -> dict:
    """账号轨：account.json 里 track.py 判出的门店/博主 + 判定线索原文。"""
    acc = _j(proj / "account.json")
    if not acc.get("track"):
        return _na_row("track", "账号轨（按门店做还是按博主做）",
                       "项目里没有 account.json（还没跑 track.py，这条轨没判过）",
                       "说一句「我们是门店 / 是博主」→ `python3 scripts/track.py --project <项目>` "
                       "猜一次，再 `--set 门店` 或 `--set 博主` 定下来")
    track = acc["track"]
    other = "博主" if track == "门店" else "门店"
    label = acc.get("track_label") or track
    metrics = " / ".join(acc.get("success_metrics") or []) or "（account.json 没写成功指标）"
    bits = []
    for b in (acc.get("basis") or [])[:3]:
        bits.append(f"{b.get('field') or '原话'}里出现「{b.get('clue') or '?'}」：{_clip(b.get('quote'), 30)}")
    if acc.get("default_guess"):
        bits.append("零线索命中，按默认猜了门店轨（default_guess=true）")
    if not bits:
        bits.append(_clip(acc.get("basis_reason") or NA, 90))
    bits.append(f"decided_by={acc.get('decided_by')}、confirmed={acc.get('confirmed')}")
    return _row("track", f"账号按「{label}」做，成功标准 = {metrics}", "；".join(bits),
                f"说「其实我们是{other}」→ `track.py --project <项目> --set {other}`，"
                f"内容配比与成功指标一起换")


def row_length(proj: Path, tl: dict) -> dict:
    """片长：brief 要的秒数 × 时间轴实际落盘的秒数。"""
    dur = tl.get("duration_s")
    if dur is None:
        return _na_row("length", "片长", "timeline.json 里没有 duration_s",
                       "说一个目标秒数，我按它重排时间轴")
    brief = _j(proj / "brief.json")
    ls, band = brief.get("length_s"), brief.get("length_band")
    if ls:
        what = f"片长 {float(dur):.1f} 秒（需求卡写的是 {ls} 秒 · {band or '未标档位'} 档）"
        basis = (f"brief.json length_s={ls}、length_band={band}；timeline.json duration_s={dur}"
                 f"（差 {float(dur) - float(ls):+.1f}s）")
    else:
        what = f"片长 {float(dur):.1f} 秒（没人给目标时长，按排完的时间轴落盘）"
        basis = f"{NA}：brief.json 里没有 length_s；timeline.json duration_s={dur}"
    return _row("length", what, basis,
                "说「压到 45 秒 / 放到 90 秒」→ 我重排时间轴（`timeline.py --plan <档>`，"
                "或按你给的秒数切）")


def row_pack(proj: Path, root: Path, tl: dict) -> dict:
    """包装包：画幅 / 框架 / 字幕系统全从 packs/<id>.yaml 读，不凭记忆。"""
    brief = _j(proj / "brief.json")
    pid = tl.get("pack") or brief.get("pack") or tl.get("style_pack") or ""
    if not pid:
        return _na_row("pack", "包装包", "timeline.json / brief.json 里都没有 pack 字段",
                       "说「要暖实拍 / 纸感 / 冷暗」→ `style_menu.py --choose <plan> <pack> --project <项目>`")
    st = _y(root / "packs" / f"{pid}.yaml") or _y(root / "styles" / f"{pid}.yaml")
    if not st:
        return _na_row("pack", f"包装包 `{pid}`",
                       f"packs/{pid}.yaml 与 styles/{pid}.yaml 都不存在，包里写了什么读不到",
                       "说「换一套包装」→ `style_menu.py --choose <plan> <pack> --project <项目>`")
    cv = st.get("canvas") or {}
    frame = (st.get("frame") or {}).get("desc")
    subsys = (st.get("subtitle_system") or {}).get("desc")
    what = (f"包装用 `{pid}`（{st.get('label') or '无标签'}）："
            f"画幅 {cv.get('width') or '?'}×{cv.get('height') or '?'}·{cv.get('fps') or '?'}fps"
            + (f"，{frame}" if frame else "") + (f"，{subsys}" if subsys else ""))
    basis = (f"timeline.json pack={tl.get('pack') or '（空）'} → packs/{pid}.yaml"
             f"｜look_notes：{_clip(st.get('look_notes'), 90)}")
    return _row("pack", what, basis,
                "说「换纸感 / 换冷色调」→ `style_menu.py --choose <plan> <pack> --project <项目>`"
                "（只换衣服，不动切点）")


def row_tempo(proj: Path, root: Path, tl: dict) -> dict:
    """节奏档：plans/<档>.yaml 的 tempo 与 shot_logic。"""
    brief = _j(proj / "brief.json")
    plan_id = tl.get("plan") or brief.get("plan") or ""
    if not plan_id:
        return _na_row("tempo", "节奏档", "timeline.json / brief.json 里都没有 plan 字段",
                       "说「慢一点 / 快一点」→ `timeline.py --plan S1-conclude-fast`（或 S3-probe-slow）")
    pl = _y(root / "plans" / f"{plan_id}.yaml")
    tempo = (pl.get("tempo") or {}) if pl else {}
    band = tempo.get("band") or []
    if not tempo:
        return _na_row("tempo", f"节奏档 `{plan_id}`",
                       f"plans/{plan_id}.yaml 读不到，档位里写的节奏参数看不见",
                       "说「慢一点 / 快一点」→ `timeline.py --plan <另一个档>`")
    span = f"{band[0]}–{band[1]} 秒" if len(band) >= 2 else "（档里没写 band）"
    what = f"节奏按档 `{plan_id}`：每镜 {span}、目标中位 {tempo.get('target_median_s', '?')} 秒"
    basis = (f"timeline.json plan={plan_id} → plans/{plan_id}.yaml："
             f"tempo.band={band}、target_median_s={tempo.get('target_median_s')}、"
             f"cv_min={tempo.get('cv_min')}｜shot_logic={_clip(json.dumps(pl.get('shot_logic') or {}, ensure_ascii=False), 80)}")
    return _row("tempo", what, basis,
                "说「慢一点 / 快一点」→ `timeline.py --plan <另一档>`（档位在 plans/*.yaml，"
                "改的是每镜长度不是内容）")


def row_bgm(proj: Path, st: dict) -> dict:
    """BGM：bgm.json 里那条曲子的来历 + 授权留痕 + 侧链闪避档位。"""
    bg = _j(proj / "bgm.json")
    if not bg:
        return _na_row("bgm", "背景音乐",
                       "项目里还没有 bgm.json（渲的时候才会按情绪从 CC0 库自动挑）",
                       "说「要一首更轻的 / 不要垫乐」→ 渲时 `render_hf.py --bgm none`，"
                       "或指定一个文件")
    duck = (st.get("audio") or {}).get("duck_db")
    what = (f"BGM 用 CC0 曲库里的《{bg.get('title') or '未记名'}》（mood={bg.get('mood') or 'auto'}）"
            + (f"，说话时压低 {duck} dB 给口播让路" if duck else ""))
    man = _j(proj / "out" / "sfx_manifest.json").get("bgm") or {}
    basis = (f"bgm.json：why=「{bg.get('why') or '未写'}」、source={bg.get('source')}"
             + (f"｜授权：{man.get('licence')}（{man.get('sha256_16')}）" if man.get("licence") else ""))
    return _row("bgm", what, basis,
                "说「换一首 / 关掉」→ 重选 `bgm.json` + `bgm.wav`，或渲时 `--bgm none`")


def row_sfx(proj: Path) -> dict:
    """音效：out/sfx.json 的事件表 + 授权总表。"""
    ev = (_j(proj / "out" / "sfx.json")).get("events") or []
    if not ev:
        return _na_row("sfx", "卡片音效", "out/sfx.json 没有事件（还没渲过，或这次一个都没挂）",
                       "说「要一点音效 / 不要音效」→ 渲时 `--no-sfx` 关掉，或按卡点补")
    names = Counter(str(e.get("name")) for e in ev)
    top = "、".join(f"{k}×{v}" if v > 1 else k for k, v in names.most_common(4))
    ex = "；".join(f"{float(e.get('t') or 0):.1f}s「{_clip(e.get('for'), 12)}」" for e in ev[:3])
    lic = _j(proj / "out" / "sfx_manifest.json").get("licence_summary") or ""
    what = f"音效 {len(ev)} 处，用的是 {top}"
    basis = f"out/sfx.json 事件表：{ex}" + (f"｜{_clip(lic, 60)}" if lic else "")
    return _row("sfx", what, basis,
                "说「音效太多 / 全关」→ 渲时 `--no-sfx`；想换材质就改 `out/sfx.json` 里的 name"
                "（CC0 库里挑）")


def row_subs(proj: Path, st: dict, pid: str) -> dict:
    """字幕版式：包里的字号基线/行宽/字心 × 渲器实测落盘的字幕表。"""
    sub = st.get("subtitle") or {}
    if not sub:
        return _na_row("subs", "字幕版式", f"packs/{pid or '?'}.yaml 里没有 subtitle 段",
                       "说「字幕大一号」→ 改包里的 subtitle.size_px 再重渲")
    caps = _j(proj / "out" / "subs.json").get("cues") or []
    longest = max((len(str(c.get("text") or "")) for c in caps), default=0)
    cv_h = (st.get("canvas") or {}).get("height") or "?"
    what = (f"字幕单行 {sub.get('size_px')}px、每条最多 {sub.get('max_chars_per_line')} 字、"
            f"字心在 {sub.get('center_y_px')}px（画幅高 {cv_h}）")
    basis = (f"packs/{pid}.yaml subtitle：font={sub.get('font')}、weight={sub.get('weight')}、"
             f"safe_bottom_px={sub.get('safe_bottom_px')}、hold={sub.get('hold_min_s')}–{sub.get('hold_max_s')}s"
             + (f"｜out/subs.json 实测 {len(caps)} 条、最长 {longest} 字" if caps
                else f"｜{NA}：out/subs.json 还没有（没渲过）"))
    return _row("subs", what, basis,
                "说「字幕大一号 / 往上挪」→ 改 `packs/" + (pid or "<包>") +
                ".yaml` 的 size_px、center_y_px 再重渲（只动版式，不动切点）")


def row_grade(proj: Path, st: dict, tl: dict, pid: str) -> dict:
    """调色：包里那一档 grade 名（渲器就按它给每格上滤镜）。"""
    els = [e for e in tl.get("elements") or [] if (e.get("track") or "video") != "overlay"]
    grade = st.get("grade")
    if not grade:
        return _na_row("grade", "调色", f"packs/{pid or '?'}.yaml 没有 grade 字段，用的色就说不清",
                       "说「暖一点 / 亮一点」→ 渲时 `--grade 'saturate(1.12)'`，或给包里补一个 grade")
    what = f"调色整片按包装包的 `{grade}` 一档到底"
    basis = (f"packs/{pid}.yaml grade={grade}（渲器逐格套这档滤镜）｜时间轴 {len(els)} 格里没有逐格单独指定 grade"
             if not any(e.get("grade") for e in els)
             else f"packs/{pid}.yaml grade={grade}｜时间轴里有 "
                  f"{sum(1 for e in els if e.get('grade'))}/{len(els)} 格自己指定了 grade")
    return _row("grade", what, basis,
                "说「再暖一点 / 亮一点」→ 渲时 `--grade 'saturate(1.12) contrast(1.04)'`，"
                "或改包里的 grade（不动切点）")


def row_loudness(proj: Path, st: dict, pid: str) -> dict:
    """响度：包里的目标值 × 渲完写回 meta.json 的实测值。"""
    au = st.get("audio") or {}
    if not au.get("loudness_lufs"):
        return _na_row("loudness", "成片响度", f"packs/{pid or '?'}.yaml 里没有 audio.loudness_lufs",
                       "说一个目标 LUFS，我改包里 audio.loudness_lufs")
    meta = _j(proj / "hf" / "meta.json")
    what = f"响度锁 {au.get('loudness_lufs')} LUFS、真峰 {au.get('true_peak_dbtp')} dBTP"
    basis = (f"packs/{pid}.yaml audio：loudness_lufs={au.get('loudness_lufs')}、"
             f"true_peak_dbtp={au.get('true_peak_dbtp')}、duck_db={au.get('duck_db')}"
             + (f"｜hf/meta.json 实测 {meta.get('lufs')} LUFS / 真峰 {meta.get('true_peak')}"
                if meta.get("lufs") is not None else f"｜{NA}：还没渲过，没有实测值"))
    return _row("loudness", what, basis,
                "响度是平台通行口径（−14 LUFS），一般别动；真要改说一声，我改包里的 "
                "audio.loudness_lufs 再重渲")


def row_takes(proj: Path, tl: dict, lib: Path | None) -> dict:
    """素材取舍：逐格为什么用这一镜 · 库里判死的段 · decisions.md 的改动留痕。"""
    els = [e for e in tl.get("elements") or [] if (e.get("track") or "video") != "overlay"]
    used = {str(e.get("asset") or "") for e in els}
    used |= {str((e.get("audio") or {}).get("asset") or "") for e in els}
    used.discard("")
    cut_grades = Counter(str(e.get("grade_at_cut")) for e in els if e.get("grade_at_cut"))
    parts: list[str] = []
    dn_n: int | None = None
    whys = [e for e in els if e.get("why")]
    if whys:
        ex = whys[0]
        parts.append(f"timeline 里 {len(whys)}/{len(els)} 格写了取用理由，例 #{ex.get('n')}"
                     f"「{_clip(ex.get('why'), 56)}」")
    if cut_grades:
        parts.append("逐格素材级别 " + "、".join(f"{k}×{v}" for k, v in cut_grades.most_common(4)))
    if lib is not None:
        idx = _j(lib / "_cutskills" / "index.json")
        segs = [s for a in idx.get("assets") or [] for s in (a.get("segments") or [])]
        dn = [s for s in segs if s.get("grade") == "do-not-use"]
        dn_n = len(dn) if segs else None
        if segs:
            parts.append(f"素材库 {len(segs)} 段镜头评级里 {len(dn)} 段判 do-not-use（不进片）")
            if dn:
                parts.append(f"其中一条被弃的原因：「{_clip(dn[0].get('why'), 56)}」")
        else:
            parts.append(f"{NA}：素材库 index.json 里没有镜头评级段（segments），弃用原因读不到")
    else:
        parts.append(f"{NA}：找不到素材库的 _cutskills/index.json，弃用段与评级读不到")
    dec = proj / "decisions.md"
    if dec.exists():
        rows = [ln for ln in dec.read_text(encoding="utf-8").splitlines()
                if ln.startswith("|") and re.search(r"删|弃|不用|去掉|换掉", ln)]
        rows = [r for r in rows if r.count("|") >= 4]
        if rows:
            cols = rows[-1].split("|")
            parts.append(f"decisions.md 有 {len(rows)} 条改动留痕，最近一条「{_clip(cols[2], 40)}」"
                         f"→ {_clip(cols[3], 50)}")
        else:
            parts.append(f"{NA}：decisions.md 里没有涉及取舍的行")
    else:
        parts.append(f"{NA}：没有 decisions.md，改片留痕读不到")
    what = f"素材取舍：{len(els)} 格里用了 {len(used)} 段素材"
    if dn_n is not None:
        what += f"，库里判 do-not-use 的 {dn_n} 段一律没进片"
    return _row("takes", what, "；".join(parts),
                "说「第 3 格换个更近的镜头 / 这段不要」→ 我改 timeline.json 那一格的 "
                "asset、in/out（只动画面，音轨不动）")


# ---------------------------------------------------------------- 组装

def collect(proj: Path, root: Path) -> list[dict]:
    tl = _j(proj / "timeline.json")
    brief = _j(proj / "brief.json")
    pid = str(tl.get("pack") or brief.get("pack") or tl.get("style_pack") or "")
    st = (_y(root / "packs" / f"{pid}.yaml") or _y(root / "styles" / f"{pid}.yaml")) if pid else {}
    try:
        lib: Path | None = resolve_lib(proj, None)
    except SystemExit:
        lib = None
    rows = [
        row_length(proj, tl),
        row_pack(proj, root, tl),
        row_bgm(proj, st),
        row_track(proj),
        row_tempo(proj, root, tl),
        row_takes(proj, tl, lib),
        row_subs(proj, st, pid),
        row_sfx(proj),
        row_grade(proj, st, tl, pid),
        row_loudness(proj, st, pid),
    ]
    order = {k: i for i, k in enumerate(TOP_ORDER + REST_ORDER)}
    rows.sort(key=lambda r: order.get(r["key"], 99))
    return rows


def render(rows: list[dict], proj: Path, top: int) -> str:
    top_n = max(1, min(int(top), len(rows)))
    head = rows[:top_n]
    rest = rows[top_n:]
    na_n = sum(1 for r in rows if r["na"])
    L: list[str] = []
    A = L.append
    A(f"# 我替你做的决定 · {proj.name}")
    A("")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 共 {len(rows)} 条，"
      f"其中 {na_n} 条「{NA}」｜ 每条都能一句话推翻：看完直接说改哪条，不用重开需求会。")
    A("")

    def table(items: list[dict]) -> None:
        A("| # | 决定了什么 | 依据 | 怎么改 |")
        A("|---|---|---|---|")
        for i, r in enumerate(items, 1):
            A(f"| {i} | {r['what']} | {r['basis']} | {r['fix']} |")
        A("")

    A(f"## 首屏 · 最可能想改的 {len(head)} 条")
    A("")
    table(head)
    if rest:
        keys = "、".join(r["key"] for r in rest)
        A("<details>")
        A(f"<summary><b>其余 {len(rest)} 条系统默认</b>（{keys}）——点开看依据与改法</summary>")
        A("")
        table(rest)
        A("</details>")
        A("")
    A(f"> 标「{NA}」的行，是这件事系统**没有**替你定、或者留痕读不到——不是已经定了。"
      "看到这一行就说明该补的东西还没补（跑对应的脚本补上，清单里就会变成真依据）。")
    A("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="列出系统替用户做的每一项默认决定（决定了什么｜依据｜怎么改）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=str(HERE.parent), help="仓根（找 packs/ plans/ 用）")
    ap.add_argument("--out", default=None, help="输出路径（默认 <项目>/assumptions.md）")
    ap.add_argument("--top", type=int, default=4, help="首屏放几条（默认 4：片长/包装/BGM/账号轨）")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    root = Path(a.root).expanduser().resolve()
    if not (proj / "timeline.json").exists():
        print(f"没有 timeline.json：{proj}（先跑 timeline.py 排出时间轴）", file=sys.stderr)
        return 2
    rows = collect(proj, root)
    text = render(rows, proj, a.top)
    out = Path(a.out).expanduser() if a.out else proj / "assumptions.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    na_n = sum(1 for r in rows if r["na"])
    print(f"→ {out}（{len(rows)} 条 · 其中 {na_n} 条取不到）")
    if a.show:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
