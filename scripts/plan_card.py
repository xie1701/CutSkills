#!/usr/bin/env python3
"""剪辑 + 包装方案：动手之前先让人看一遍、拍一次板。

为什么要有这一步（2026-11-09 Jerry 的原话）：
「其实应该在剪辑开始前，先让用户确认剪辑和包装方案，被认可后，再去动。」

之前是反的：我先渲完、再让他看片提意见，一轮一轮改。片子是视频，改一次
就是几分钟机器时间 + 他一次完整观看，代价太高。方案是文字，改一行是几秒钟。

所以这条链变成：
    brief.py（问清楚）→ plan_card.py（出方案）→ 人看了 → --approve（拍板）
    → render_hf.py（才允许渲）

`render_hf.py` 会检查 `lock.json`（拍板时的时间轴哈希）；锁失效或没有锁就拒绝渲（`--force` 可以绕过，但不该用）。

方案里必须写清楚的四件事：**这段在说什么 / 画面是什么 / 有没有代码画的画面 /
观众会听到什么**。少一件，人就只能凭想象拍板。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

MARK = "<!-- plan-card: v1 -->"


def _style(root: Path, name: str | None) -> dict:
    if not name or yaml is None:
        return {}
    p = root / "styles" / f"{name}.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def _cards(proj: Path) -> dict[int, dict]:
    """cards.json 按格号归堆。渲的时候算出来的卡片没有 el，只能按时间窗口落进格子。"""
    p = proj / "out" / "cards.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    rows = d if isinstance(d, list) else (d.get("cards") or [])
    tl_p = proj / "timeline.json"
    wins: list[tuple[float, float, int]] = []
    if tl_p.exists():
        t = 0.0
        for e in json.loads(tl_p.read_text())["elements"]:
            if e.get("track") == "overlay":
                continue
            if e["kind"] == "clip":
                d_ = (float(e["audio"]["out"]) - float(e["audio"]["in"])) if e.get("audio") \
                    else float(e["out"]) - float(e["in"])
            else:
                d_ = float(e.get("dur") or 0)
            wins.append((t, t + d_, int(e["n"])))
            t += d_
    out: dict[int, list] = {}
    for c in rows:
        el = c.get("el")
        if el is None:
            st = float(c.get("start") or 0)
            hit = next((n for a, b, n in wins if a - 0.05 <= st < b), None)
            el = hit
        if el is not None:
            out.setdefault(int(el), []).append(c)
    return out


def _events(proj: Path) -> list[dict]:
    p = proj / "out" / "sfx.json"
    return json.loads(p.read_text()).get("events") or [] if p.exists() else []


def build(proj: Path, root: Path) -> str:
    brief = json.loads((proj / "brief.json").read_text())
    tl = json.loads((proj / "timeline.json").read_text())
    st = _style(root, tl.get("style_pack") or brief.get("style_pack"))
    cards = _cards(proj)
    sfx = _events(proj)
    L = []
    A = L.append

    A(MARK)
    A(f"# 方案 · {proj.name}")
    A("")
    A(f"> 生成于 {datetime.now():%Y-%m-%d %H:%M}｜看完请回一句话：**认可** / 要改哪几条。"
      "没有认可之前不会开渲。")
    A("")
    A("## 一句话")
    A("")
    A(f"给 **{brief.get('audience','?')}** 看。他现在以为 **{brief.get('audience_present_state','?')}**；"
      f"看完要让他 **{brief.get('change','?')}**。片长 {tl.get('duration_s')} 秒，"
      f"风格包 `{tl.get('style_pack')}`。")
    A("")
    A("## 逐格：说什么 / 画面是什么 / 有什么额外的东西")
    A("")
    A("| # | 秒 | 说什么（原话） | 画面 | 大字 / 代码画面 |")
    A("|---|---|---|---|---|")
    t0 = 0.0
    for e in tl["elements"]:
        if e.get("track") == "overlay":
            continue
        if e["kind"] == "clip":
            d = float(e["audio"]["out"]) - float(e["audio"]["in"]) if e.get("audio") else float(e["out"]) - float(e["in"])
        else:
            d = float(e.get("dur") or 0)
        span = f"{t0:.1f}–{t0 + d:.1f}"
        t0 += d
        said = (e.get("evidence") or "").replace("|", "／")
        vis = e.get("asset") or e.get("kind") or ""
        if e.get("medium") == "talking":
            vis = "口播（" + Path(vis).name[-18:] + "）"
        elif e.get("kind") == "gen":
            vis = f"**代码画的**：{e.get('template') or e.get('kind')}"
        elif vis:
            vis = f"B-roll（{Path(str(vis)).name[-16:]}）"
        extra = []
        # 大字可能写在 timeline 的格子里，也可能是渲染时才算出来的 cards.json。
        # cards.json 里的 el 是空的（卡片是渲的时候才算的），所以要按时间窗口兜一层。
        for c in cards.get(int(e["n"]), []):
            extra.append(f"大字「{c.get('text')}」")
        if not extra and e.get("card"):
            extra.append(f"大字「{e['card'].get('text')}」")
        if e.get("kind") == "gen":
            extra.append("代码画面（非卡片）")
        A(f"| {e['n']} | {span} | {said} | {vis} | {'；'.join(extra) or '—'} |")
    A("")
    scenes_p = proj / "scenes.json"
    if scenes_p.exists():
        sc = json.loads(scenes_p.read_text())
        A("## 代码画的画面（不是字卡，是画面自己在讲故事）")
        A("")
        A("| 秒 | 画什么 | 演的是哪句话 | 状态 |")
        A("|---|---|---|---|")
        for x in sc.get("scenes") or []:
            A(f"| {x.get('at')}–{round(float(x.get('at', 0)) + float(x.get('dur', 0)), 1)} "
              f"| {x.get('what')} | {x.get('for')} | {x.get('status', '待做')} |")
        A("")
    A("## 包装")
    A("")
    sub = (st.get("subtitle") or {})
    emph = (st.get("emphasis") or {})
    A(f"- 字幕：{sub.get('max_chars_per_line', '—')} 字上限、单行、硬切 {int(float(sub.get('fade_s', 0.1)) * 1000)}ms；"
      "不带标点，一个呼吸组一条。")
    A(f"- 大字：{emph.get('keyword_color', '—')}，顶边 {emph.get('top_h', '—')}H，"
      f"锚在某一个词上、晚 {100}ms 出场。")
    A(f"- 垫乐：代码合成（`bgm_gen.py`），−28 LUFS 垫底，人声 12:1 压住。")
    A(f"- 音效：{len(sfx)} 处，跟着每张字卡/代码画面入场；混音前先给人声让 90ms 位。")
    A("")
    if sfx:
        A("音效点：" + "、".join(f"{e['t']:.1f}s {e.get('name')}" for e in sfx[:12]))
        A("")
    A("## 素材与法律风险")
    A("")
    risks = brief.get("risks") or []
    A("- 用到的素材全部来自本库；代码画面不依赖任何外部素材。")
    for r in risks:
        A(f"- ⚠️ {r}")
    A("")
    A("## 验收（渲完我会跑这些，数字会贴给你）")
    A("")
    A("声画同步 ≥85% · 断章率 ≤25% · 卡片锚词 0~120ms · 字幕合规四条 · "
      "音效可听度（2–8 kHz 抬升 ≥3 dB）· 响度 −14 LUFS ±1 · 像素断言（字/卡真的在画面上）")
    A("")
    A("## 要你拍的（最多两条）")
    A("")
    A("1. 结构认不认：这一串格子的先后、以及哪一格用代码画而不是用素材。")
    A("2. 风险认不认：上面列出的第三方字样 / 借鉴说法，留还是处理。")
    A("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--approve", nargs="?", const="", default=None,
                    help="人看过之后拍板：写 approval.json，render 才放行")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser()
    root = Path(a.root).expanduser()
    out = proj / "方案.md"

    if a.approve is not None:
        if not out.exists():
            print("还没有方案，先跑一次（不带 --approve）", flush=True)
            return 2
        rec = {"approved": True, "at": datetime.now().isoformat(timespec="seconds"),
               "note": a.approve or "认可"}
        (proj / "approval.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        # 拍板要绑在具体的时间轴版本上，否则改完一个字节「批准」还挂着（见 lock.py）
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from lock import lock as _lock
            d = _lock(proj, rec["note"])
            print(f"已上锁：sha {d['sha256'][:12]} · {d['elements']} 格 · {d['duration_s']}s")
        except Exception as e:
            print(f"（上锁失败：{e}）")
        with (proj / "decisions.md").open("a") as f:
            f.write(f"\n| {datetime.now():%Y-%m-%d %H:%M} | 方案确认 | 按方案剪 | "
                    f"{rec['note']} |\n")
        print(f"已记录拍板：{rec['note']} → {proj / 'approval.json'}")
        return 0

    text = build(proj, root)
    out.write_text(text)
    print(f"→ {out}")
    if a.show:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
