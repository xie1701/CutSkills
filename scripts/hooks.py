#!/usr/bin/env python3
"""CutSkills · 第 2 层：先出前 3 秒的候选，再谈整条。

为什么先做这一步：整条片 79 秒，改一次要 1.5 分钟渲 + 3 分钟验；而观众决定要不要看
是前 3 秒决定的。先花 30 秒出 2–3 个开场，让人（或小额投放）挑一个，再排整条——
这是广告投放的 creative testing 搬到剪辑台上，不是「多做一个版本」。

三个候选不是随机三选一，是三种不同的**开场策略**：

  A 结论前置   一上来就把主张说出来（参考片 A 版赢的那一次）
  B 现场切入   先给最硬的现场画面，人后出
  C 反差提问   库里没有能打的画面时，用代码造的整屏（还带一个数字/反差的钩子）

候选落 `hooks.json`，每个候选本身就是一份能直接渲的 timeline；`--choose` 记进 decisions.md。

用法：
  hooks.py --project <项目>                     # 只生成候选（快）
  hooks.py --project <项目> --render            # 顺便渲成 3 秒片，给人二选一
  hooks.py --project <项目> --show              # 看现状
  hooks.py --project <项目> --choose A --note "业主看得懂"   # 定案留痕
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from libs import resolve_lib  # noqa: E402

REJUDGE = re.compile(r"其实|不是|别|千万|记住|关键|问题是|说到底|本质")
HOOK_MAX_S = 3.6


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def pick_conclusion(plan: dict | None, index: dict) -> dict | None:
    """A 结论前置：优先拿 plan 里已经标成 claim 的第一段（它本来就是主张）。"""
    for seg in (plan or {}).get("segments") or []:
        if seg.get("role") == "claim" and seg.get("asset") and seg.get("in") is not None:
            span = min(HOOK_MAX_S, float(seg["out"]) - float(seg["in"]))
            return {"kind": "clip", "asset": seg["asset"], "in": float(seg["in"]),
                    "out": round(float(seg["in"]) + span, 3), "medium": seg.get("medium") or "talking",
                    "source": seg.get("source") or "library",
                    "why": "结论前置：把主张放最前面，观众第一句就知道你要说什么"}
    return None


def pick_scene(index: dict) -> dict | None:
    """B 现场切入：找一段**单主体、有动作、够长**的 must-use 画面。

    第一版只看「动作描述多长」，结果挑到一段固定机位中景——前景一个人在翻手机、
    背景人字梯上还有一个人。Jerry 的原话是「人太多比较混乱」。所以这里改成按
    可拍性打分：人少、景别近、有真实动作，三条都算分，人多扣分。
    """
    MOVE = ("打磨", "倒料", "摊料", "刮平", "喷涂", "搅拌", "切割", "滚涂", "除尘", "上墙")
    best: tuple[float, dict] | None = None
    for rec in index.values():
        for sg in rec.get("l2") or rec.get("segments") or []:
            if sg.get("grade") != "must-use" or not sg.get("action"):
                continue
            span = float(sg.get("out", 0)) - float(sg.get("in", 0))
            if span < 3.0:
                continue
            people = sg.get("people") or []
            score = 0.0
            score += 3.0 if len(people) <= 1 else (0.0 if len(people) == 2 else -2.0)
            score += {"detail": 2.5, "close": 2.0, "pov": 1.5}.get(str(sg.get("shot")), 0.0)
            score += 2.0 if any(w in str(sg.get("action")) for w in MOVE) else 0.0
            if "固定机位" in str(sg.get("summary") or ""):
                score -= 1.0
            score += min(1.5, span / 6)
            if best is None or score > best[0]:
                best = (score, {"kind": "clip", "asset": rec["id"], "in": float(sg["in"]),
                                "out": round(float(sg["in"]) + min(5.8, span), 3),
                                "medium": "broll", "source": "library",
                                "why": f"现场切入：{len(people)} 人 · {sg.get('shot')} · "
                                       f"{str(sg.get('action'))[:18]}"})
    return best[1] if best else None


def pick_gen(brief: dict) -> dict:
    """C 反差提问：库里没有能打的画面时，用代码造一个。"""
    change = str((brief or {}).get("change") or "").strip("。")
    state = str((brief or {}).get("audience_present_state") or "").strip("。")
    # 钩子念的是**观众现在信的那句**，不是我们要他做的事。
    # 一开始写反了：把 change 截 6 字打成「比价前先问三」，读起来像被刀切的。
    text = (state[:8] or change[:8] or "真的越贵越好？").rstrip("，、 ") + "？"
    sub = change[:14] if change else ""
    return {"kind": "gen", "template": "keyword", "dur": 2.6,
            "props": {"text": text, "sub": sub}, "source": "generated",
            "why": "反差提问：把观众现在的想法直接打在画面上，制造「说的就是我」"}


def evaluate_hook_policy(h: dict | None) -> tuple[bool | None, list[str]]:
    """前 3 秒策略真判定引擎。
    返回: (passed, reasons)
      - passed is True:  全规则放行（候选有效、时长合规、达成 ≥2 票共识且定案一致）
      - passed is False: 存在硬性违规或未过门禁（超时、平票、冲突、残缺）
      - passed is None:  仅定案未投票或初生状态（未验，良率卡记为未验）
    """
    if not h:
        return None, ["没有 hooks.json"]

    reasons: list[str] = []
    cands = h.get("candidates") or []
    cand_map = {c["id"]: c for c in cands if isinstance(c, dict) and "id" in c}

    if not cands:
        return False, ["无可用候选"]

    chosen = h.get("chosen")
    votes_data = h.get("votes") or {}
    raw_votes = votes_data.get("raw") or []
    majority = votes_data.get("majority")

    # 1. 候选元素有效性与时长基础校验
    for c in cands:
        cid = c.get("id")
        dur = float(c.get("duration_s") or 0.0)
        if dur <= 0:
            reasons.append(f"候选 {cid} 时长异常 ({dur}s)")
        elements = c.get("elements") or []
        if not elements:
            reasons.append(f"候选 {cid} 缺少元素")
        for el in elements:
            if el.get("kind") == "clip":
                if not el.get("asset") or el.get("in") is None or el.get("out") is None:
                    reasons.append(f"候选 {cid} clip 元素缺少素材 asset 或起止点")
            elif el.get("kind") == "gen":
                text = (el.get("props") or {}).get("text")
                if not text:
                    reasons.append(f"候选 {cid} gen 元素缺少文本 props.text")

    # 2. 真人投票策略共识校验（如果有投票）
    has_votes = bool(raw_votes)
    vote_passed = False
    if has_votes:
        tally = votes_data.get("tally") or {}
        max_n = max(tally.values()) if tally else 0
        if max_n < 2:
            reasons.append(f"真人投票未达成多数共识（最高仅 {max_n}/{len(raw_votes)} 票，需 ≥2 票同选）")
        elif not majority:
            reasons.append("真人投票缺少 majority 胜出者记录")
        else:
            vote_passed = True

    # 3. 定案与共识对账及黄金 3 秒时长硬红线
    target_cand_id = chosen or (majority if (has_votes and vote_passed) else None)
    if target_cand_id:
        if target_cand_id not in cand_map:
            reasons.append(f"候选 {target_cand_id} 不在候选列表内")
        else:
            cand = cand_map[target_cand_id]
            dur = float(cand.get("duration_s") or 0.0)
            if dur > HOOK_MAX_S:
                reasons.append(f"候选 {target_cand_id} 时长超标：{dur:.2f}s > {HOOK_MAX_S}s 上限")

    if chosen and has_votes and vote_passed and majority:
        if chosen != majority:
            reasons.append(f"定案候选 {chosen} 与投票多数 {majority} 冲突（言行不一致）")

    # 综合裁决
    if reasons:
        return False, reasons

    # 达成多数共识，且定案候选与多数一致（或已由多数锁定）
    if has_votes and vote_passed:
        if chosen and chosen == majority:
            return True, []
        if not chosen:
            return True, []

    # 尚未投票
    if not has_votes:
        if chosen:
            return None, [f"定案 {chosen} 尚未进行真人投票验过"]
        return None, ["尚未选定候选且未进行真人投票"]

    return False, ["未满足放行条件"]


def build(proj: Path, lib: Path, index: dict,
          keep_chosen: str | None = None,
          keep_votes: dict | None = None,
          keep_passed: bool | None = None) -> dict:
    brief = load(proj / "brief.json") or {}
    plan = load(proj / "plan.json")
    cands = []

    a = pick_conclusion(plan, index)
    if a:
        cands.append({"id": "A", "label": "结论前置", "elements": [a]})
    b = pick_scene(index)
    if b:
        cands.append({"id": "B", "label": "现场切入", "elements": [b]})
    cands.append({"id": "C", "label": "反差提问", "elements": [pick_gen(brief)]})
    for c in cands:
        total = 0.0
        for i, el in enumerate(c["elements"], 1):
            el["n"] = i
            el["track"] = "video"
            el["_start"] = round(total, 3)
            total = round(total + (float(el["out"]) - float(el["in"]) if el["kind"] == "clip"
                                   else float(el.get("dur") or 2)), 3)
        c["duration_s"] = total

    doc = {"version": 1, "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
           "basis": {k: brief.get(k) for k in ("audience", "audience_present_state", "change")},
           "candidates": cands, "chosen": keep_chosen}
    if keep_votes is not None:
        doc["votes"] = keep_votes
    # 重新根据新生成的候选与既有投票/定案进行策略真判定
    passed, _ = evaluate_hook_policy(doc)
    doc["passed"] = passed
    return doc


def render_one(proj: Path, lib: Path, cand: dict, style: str) -> Path | None:
    d = proj / "hooks" / f"cand-{cand['id']}"
    d.mkdir(parents=True, exist_ok=True)
    tl = {"version": 4, "slug": f"hook-{cand['id']}", "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
          "duration_s": cand["duration_s"], "style_pack": style, "brief": None,
          "skeleton": None, "elements": cand["elements"], "beats": []}
    (d / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE / "render_hf.py"), "--project", str(d),
                        "--library", str(lib), "--style", style, "--extractor", "own",
                        "--quality", "draft", "--workers", "4", "--shot-match", "0",
                        "--no-cutout", "--no-loudnorm"],
                       capture_output=True, text=True)
    out = d / "out" / "film-hf.mp4"
    if r.returncode != 0 or not out.exists():
        print(f"  ！候选 {cand['id']} 渲不出来：{(r.stderr or r.stdout or '')[-300:]}", file=sys.stderr)
        return None
    dst = proj / "hooks" / f"{cand['id']}-{cand['label']}.mp4"
    shutil.copy2(out, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--choose", default=None, help="定案：A / B / C")
    ap.add_argument("--note", default=None)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--kit", action="store_true", help="出一份给人看的评审包（说明+评分表）")
    ap.add_argument("--vote", default=None,
                    help="记真人投票：逗号分隔，A/B/C 顺序对应看片顺序，如 A,B,C")
    ap.add_argument("--check", action="store_true", help="校验前 3 秒策略合规性与通过性")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    hp = proj / "hooks.json"

    if a.check:
        h = load(hp)
        if not h:
            print("🔴 FAIL: 还没有 hooks.json，先跑 hooks.py 生成候选", file=sys.stderr)
            return 1
        passed, reasons = evaluate_hook_policy(h)
        chosen = h.get("chosen")
        cands = h.get("candidates") or []
        cand = next((c for c in cands if c.get("id") == chosen), None) if chosen else None
        dur_str = f" · 时长 {cand['duration_s']:.2f}s" if cand else ""
        if passed is True:
            votes = (h.get("votes") or {}).get("raw") or []
            v_str = f" · 投票 {'/'.join(votes)}" if votes else ""
            print(f"🟢 OK: 前 3 秒策略通过（定案 {chosen}{dur_str}{v_str}）")
            return 0
        elif passed is None:
            r_str = f"（{reasons[0]}）" if reasons else ""
            print(f"🟡 WARN: 前 3 秒策略未验{r_str}：已选定候选，但未做真人投票（良率卡将记为未验）")
            return 0
        else:
            print(f"🔴 FAIL: 前 3 秒策略未通过：{'；'.join(reasons)}", file=sys.stderr)
            return 1

    if a.show:
        h = load(hp)
        if not h:
            raise SystemExit("还没有 hooks.json，先跑一次 hooks.py")
        for c in h["candidates"]:
            mark = "★" if h.get("chosen") == c["id"] else " "
            print(f" {mark}{c['id']}. {c['label']:<6} {c['duration_s']:.2f}s  {c['elements'][0]['why']}")
        status_str = "过" if h.get("passed") is True else ("没过" if h.get("passed") is False else "未验")
        print(f"定案：{h.get('chosen') or '还没定'} · 策略状态：{status_str}")
        return 0

    if a.kit:
        h = load(hp)
        if not h:
            raise SystemExit("先跑一次 hooks.py 生成候选")
        kit = proj / "hooks" / "review-kit.md"
        kit.parent.mkdir(parents=True, exist_ok=True)
        rows = "\n".join(
            f"| {c['id']} | {c['label']} | {c['duration_s']:.2f}s | "
            f"`hooks/{c['id']}-{c['label']}.mp4` |".replace("`hooks/", "`")
            for c in h["candidates"])
        kit.write_text(f"""# 前 3 秒评审包

给谁看：**两个到三个人**，最好有一个不是做这行的。不要给他们讲设计意图，也不要说是 AI 剪的。

怎么看：先放 A、B、C 各一遍，中间停 3 秒。第二遍打乱顺序再放（顺序会带偏见）。
然后只问一句：**「这三条里，你会继续往下看哪一条？为什么？」**

判过：**至少 2 个人选同一个**，才算定了。一个人选的不算，全不一样就是三个都没做对，
回去重做——不是投票选出最好的，是准确地被同一句话打中。

| 候选 | 做法 | 时长 | 文件 |
|---|---|---|---|
{rows}

投票记法（看完回来跑这条，别凭记忆）：

```
python3 scripts/hooks.py --project <项目> --vote B,B,A
```

三个字母＝三个人各自选的，顺序他们无所谓。命令会算多数、写进 hooks.json 的 `votes`
（真人读数）和 `passed`（≥2 票同一才 true），良率卡按 `passed` 说话。
""", encoding="utf-8")
        print(f"评审包 → {kit}")
        print(f"候选文件在 {proj / 'hooks'}：")
        for c in h["candidates"]:
            f = proj / "hooks" / f"{c['id']}-{c['label']}.mp4"
            print(f"  {c['id']} {c['label']:<6} {'有' if f.exists() else '缺（先 --render）'} {f.name}")
        return 0

    if a.vote:
        h = load(hp)
        if not h:
            raise SystemExit("还没有 hooks.json")
        ids = {c["id"] for c in h["candidates"]}
        votes = [v.strip().upper() for v in a.vote.split(",") if v.strip()]
        bad = [v for v in votes if v not in ids]
        if bad:
            raise SystemExit(f"投票里有不认识的候选 {bad}（有 {sorted(ids)}）")
        tally: dict[str, int] = {}
        for v in votes:
            tally[v] = tally.get(v, 0) + 1
        win, n = max(tally.items(), key=lambda kv: kv[1])
        has_majority = n >= 2
        h["votes"] = {"raw": votes, "tally": tally, "majority": win if has_majority else None,
                      "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        if has_majority and not h.get("chosen"):
            h["chosen"] = win
        # 统一执行策略真判定
        passed, reasons = evaluate_hook_policy(h)
        h["passed"] = passed
        hp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            from log_decision import append_row
            append_row(proj, said=f"前 3 秒真人投票：{'/'.join(votes)}",
                       did=(f"多数选 {win}（{n}/{len(votes)}），判过" if passed
                            else f"策略未过（{'；'.join(reasons) if reasons else '未达共识'}），该重做"))
        except Exception as e:
            print(f"  ！decisions.md 没写进去：{e}", file=sys.stderr)
        said = (f"多数选 {win}（{n}/{len(votes)}）" if has_majority
                else f"没人拿到 2 票（{tally}）")
        print(f"投票 {'/'.join(votes)} → {said}；{'策略判过 (passed: true)' if passed else '策略未过 (passed: false)，回去重做候选'}")
        if has_majority and h.get("chosen") and h["chosen"] != win:
            print(f"  ⚠ 投票多数选的是 {win}，但当前定的是 {h['chosen']}——定案与共识冲突，需 --choose {win} 对齐")
        return 0

    if a.choose:
        h = load(hp)
        if not h:
            raise SystemExit("还没有 hooks.json，先跑一次 hooks.py")
        ids = {c["id"] for c in h["candidates"]}
        if a.choose not in ids:
            raise SystemExit(f"候选「{a.choose}」不在里面（有 {sorted(ids)}）")
        h["chosen"] = a.choose
        passed, reasons = evaluate_hook_policy(h)
        h["passed"] = passed
        hp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            from log_decision import append_row   # decisions.md 只有这一个写入实现
            label = next(c["label"] for c in h["candidates"] if c["id"] == a.choose)
            append_row(proj, said=a.note or f"前 3 秒定 {a.choose}（{label}）",
                       did=f"片头接 {a.choose} 候选：{label}，时长 "
                           f"{next(c['duration_s'] for c in h['candidates'] if c['id'] == a.choose)}s")
        except Exception as e:  # 留痕失败不许静默
            print(f"  ！decisions.md 没写进去：{e}", file=sys.stderr)
        status_txt = "策略真判通过" if passed is True else ("未验（需真人投票）" if passed is None else f"策略未通过（{'；'.join(reasons)}）")
        print(f"定案 {a.choose}，已记进 decisions.md。当前状态：{status_txt}。")
        return 0

    lib = Path(a.library).expanduser().resolve() if a.library else resolve_lib(proj, None)
    idx = {}
    try:
        idx = {x["id"]: x for x in (load(lib / "_cutskills" / "index.json") or {"assets": []})["assets"]}
    except FileNotFoundError:
        raise SystemExit(f"素材库没有索引：{lib}")
    brief = load(proj / "brief.json")
    if not brief:
        raise SystemExit("先跑 brief.py：钩子要对着「他现在信什么」做，不然三个候选都在猜")
    style = a.style or brief.get("style_pack") or "talking-head-v1"

    # 重跑候选不许把已定的选择冲掉——冲掉了 decisions.md 说「定 B」、hooks.json 说空，
    # 良率卡就会撒谎说「没做 A/B」。
    old_h = load(hp) or {}
    h = build(proj, lib, idx,
              keep_chosen=old_h.get("chosen"),
              keep_votes=old_h.get("votes"),
              keep_passed=old_h.get("passed"))
    hp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"前 3 秒候选 {len(h['candidates'])} 个 → {hp}")
    for c in h["candidates"]:
        print(f"  {c['id']}. {c['label']:<6} {c['duration_s']:.2f}s  {c['elements'][0]['why']}")
    if a.render:
        (proj / "hooks").mkdir(exist_ok=True)
        for c in h["candidates"]:
            p = render_one(proj, lib, c, style)
            if p:
                print(f"  → {p}（{p.stat().st_size / 1e6:.1f} MB）")
    print("下一步：给人看这 2–3 个 3 秒，二选一 → hooks.py --choose A --note \"...\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
