#!/usr/bin/env python3
"""CutSkills · 包装选择：按实测规则，从一套包装里挑出「字幕系统 + 壳 + 卡」。

`styles/set-qizao.yaml` 规定了合法的叠法（A 或 B 的字幕系统 + C 的壳 + E 的卡，各层最多一个）。
这个脚本回答的是下一层问题：**这一支该用哪一格。**

规则不是拍脑袋定的，全部来自 references/packaging-families.md 与各 `styles/pack-*.yaml`
的 applies_to / fails_on。每条命中的规则都会写清是第几条、依据是哪个数字或哪句话——
选错了要能倒推是哪一步判错，而不是只看到一个结论。

五条规则（编号即输出里的编号）：

  1. 字幕系统。with_speech/assets > 0.4 且时长 ≤ 180s → A（安全口播）；否则若是展厅/空间/
     材质/作品/案例类内容 → B（纸感品牌）。
  2. 有「流程/步骤/教程/怎么做/几步/对比/测评」类词，或有 ≥4 个 beat → 加壳 C。
  3. 内容里有需要并排对照的事实（intent 里有数字对比，或 plan 里有 card 字段）→ 加卡 E。
  4. 时长 > 300s 或 beats ≥ 6 → 加壳 C（长片需要章节定位）。
  5. 命中任一所选 pack 的 fails_on → 降权，并把风险写进输出（不静默吞掉）。

第 5 条只认机器看得见的信号（画幅、字数、plan 里的字段、索引里的 luma）。像「主体偏上」
这种只能看画面的，脚本会说「需要人看一眼」，不假装判过。

用法：
  pick_pack.py --project "<项目目录>"
  pick_pack.py --project "<项目目录>" --library "<素材库>"
  pick_pack.py --project "<项目目录>" --json        # 机器可读，供别处调用
  pick_pack.py --project "<项目目录>" --no-write    # 只打印，不写 pack-choice.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
STYLES = REPO / "styles"

# ---- 规则 1：展厅/空间/材质类关键词（B 的入口）
SCENE_WORDS = ("展厅", "空间", "材质", "作品", "案例")
# ---- 规则 2：有流程感就有壳
FLOW_WORDS = ("流程", "步骤", "教程", "怎么做", "几步", "对比", "测评")
# ---- 规则 5：C 的「精致素材」判据（fails_on 第一条）
REFINED_WORDS = ("展厅", "作品", "样板", "材质", "工艺", "精致", "样板间")
# 数字对比：数字 + 计量单位，且前后 12 字内有比较词（「70秒」不算，秒不是比较计量）
NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:[–\-~—至到]\s*\d+(?:\.\d+)?)?\s*(倍|%|元|块|㎡|平米|厘米|cm|毫米|mm)")
CMP_WORDS = ("比", "对比", "差", "贵", "便宜", "高于", "低于")

SPEECH_RATIO_MIN = 0.40     # 规则 1 的门槛，来自「有语音素材占比高」
SHORT_S = 180               # 规则 1 的短片门槛
LONG_S = 300                # 规则 4 的长片门槛
BEATS_SHELL = 4             # 规则 2 的 beat 数门槛
BEATS_LONG = 6              # 规则 4 的 beat 数门槛
BRIGHT_LUMA = 150.0         # 规则 5：亮底判据（0–255，超过就当亮底素材）
PICK_THRESHOLD = 1.0        # 壳/卡：得分到这条线才选


# ---------------------------------------------------------------- 读料

def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def read_json(p: Path) -> dict:
    return json.loads(p.read_text())


def find_library(proj: Path) -> Path:
    """项目目录约定是 <库>/_cutskills/projects/<slug>，往上三级就是库。"""
    for cand in (proj, *proj.parents):
        if (cand / "_cutskills" / "index.json").exists():
            return cand
    raise SystemExit(f"找不到素材库索引：{proj}/_cutskills/index.json 往上都没有")


def intent_text(intent: dict) -> str:
    parts = [intent.get("raw"), intent.get("audience"), intent.get("promise")]
    parts += [str(x) for x in (intent.get("avoid") or [])]
    return " ".join(p for p in (norm(x) for x in parts) if p)


def plan_text(plan: dict) -> str:
    if not plan:
        return ""
    parts: list[str] = []
    for b in plan.get("beats") or []:
        parts.append(norm(b.get("title")))
    for s in plan.get("segments") or []:
        parts.append(norm(s.get("why")))
        card = s.get("card") or {}
        parts.append(norm(card.get("text")))
    return " ".join(p for p in parts if p)


def words_hit(text: str, words) -> list[str]:
    return [w for w in words if w in text]


def numeric_compare(text: str) -> str | None:
    """数字 + 计量单位，且前后有比较词才算「需要并排对照的事实」。"""
    for m in NUM_UNIT.finditer(text):
        window = text[max(0, m.start() - 10): m.end() + 4]
        if any(w in window for w in CMP_WORDS):
            return f"{m.group(0)}（语境「{norm(window)}」）"
    return None


def luma_stats(plan: dict, by_path: dict[str, dict]) -> tuple[float | None, str]:
    """plan 里真正用到的素材的平均亮度；查不到就退回全库。"""
    vals: list[float] = []
    seen = 0
    for s in (plan or {}).get("segments") or []:
        for k in ("asset", "audio_asset"):
            rec = by_path.get(s.get(k))
            if not rec:
                continue
            seen += 1
            lm = ((rec.get("quality") or {}).get("luma") or {}).get("mean")
            if isinstance(lm, (int, float)):
                vals.append(float(lm))
    if vals:
        return sum(vals) / len(vals), f"plan 引用的素材 mean luma {sum(vals) / len(vals):.1f}（{len(vals)} 条）"
    if by_path:
        allv = [float(((r.get("quality") or {}).get("luma") or {}).get("mean"))
                for r in by_path.values()
                if isinstance(((r.get("quality") or {}).get("luma") or {}).get("mean"), (int, float))]
        if allv:
            return sum(allv) / len(allv), f"全库 mean luma {sum(allv) / len(allv):.1f}（{len(allv)} 条）"
    return None, "索引里没有 luma 样本"


# ---------------------------------------------------------------- 打分

def score(intent: dict, plan: dict, counts: dict, by_path: dict[str, dict]) -> dict:
    """返回 {scores, hits, risks, facts}。hits 里每条都带规则编号。"""
    scores = {p: 0.0 for p in ("pack-safe-talking", "pack-paper-brand",
                               "pack-hud-monitor", "pack-ui-narrative")}
    hits: list[dict] = []
    risks: list[dict] = []

    itext = intent_text(intent)
    ptext = plan_text(plan)
    alltext = f"{itext} {ptext}"
    beats = (plan or {}).get("beats") or []
    segs = (plan or {}).get("segments") or []
    cards = [s for s in segs if s.get("card")]
    duration = intent.get("duration_s")
    aspect = norm(intent.get("aspect"))
    n_assets = counts.get("assets") or 0
    n_speech = counts.get("with_speech") or 0
    ratio = (n_speech / n_assets) if n_assets else None
    # 画幅以 intent 为准：库里竖屏素材多，不代表这支片子是竖屏
    if aspect in ("9:16", "3:4"):
        portrait = True
    elif aspect in ("16:9", "4:3", "21:9", "1:1"):
        portrait = False
    else:
        portrait = bool(n_assets) and (counts.get("portrait") or 0) / n_assets > 0.5

    def hit(rule: int, name: str, target: str, evidence: str, delta: float) -> None:
        scores[target] = round(scores[target] + delta, 2)
        hits.append({"rule": rule, "name": name, "target": target,
                     "evidence": evidence, "delta": delta})

    # ---- 规则 1：字幕系统
    scene = words_hit(alltext, SCENE_WORDS)
    if ratio is not None and ratio > SPEECH_RATIO_MIN and isinstance(duration, (int, float)) and duration <= SHORT_S:
        hit(1, "有语音素材占比高且时长 ≤180s → 字幕系统 A", "pack-safe-talking",
            f"with_speech/assets = {n_speech}/{n_assets} = {ratio:.3f} > {SPEECH_RATIO_MIN}；"
            f"时长 {duration}s ≤ {SHORT_S}s", 2.0)
    elif scene:
        hit(1, "非短口播但属展厅/空间/材质类 → 字幕系统 B", "pack-paper-brand",
            f"命中空间类关键词 {'、'.join(scene)}；"
            + (f"with_speech/assets = {ratio:.3f}" if ratio is not None else "索引缺 counts")
            + (f"，时长 {duration}s > {SHORT_S}s" if isinstance(duration, (int, float)) and duration > SHORT_S else ""),
            2.0)
    else:
        hits.append({"rule": 1, "name": "字幕系统 · 无规则命中，按 A 兜底", "target": "pack-safe-talking",
                     "evidence": f"with_speech/assets 未过 {SPEECH_RATIO_MIN} 或时长不符；也没有空间类关键词",
                     "delta": 0.0})
        scores["pack-safe-talking"] = round(scores["pack-safe-talking"] + 0.5, 2)

    # ---- 规则 2：流程感 → 壳 C
    flow = words_hit(alltext, FLOW_WORDS)
    if flow or len(beats) >= BEATS_SHELL:
        ev = []
        if flow:
            ev.append(f"命中流程类词 {'、'.join(flow)}")
        if len(beats) >= BEATS_SHELL:
            ev.append(f"beats = {len(beats)} ≥ {BEATS_SHELL}")
        hit(2, "有流程/步骤/对比类内容 → 加壳 C", "pack-hud-monitor", "；".join(ev), 1.5)

    # ---- 规则 3：并排对照的事实 → 卡 E
    card_ev = []
    if cards:
        card_ev.append(f"plan 里 {len(cards)} 段带 card 字段（如「{norm((cards[0].get('card') or {}).get('text'))[:24]}」）")
    nc = numeric_compare(alltext)
    if nc:
        card_ev.append(f"数字对比 {nc}")
    if card_ev:
        hit(3, "有需要并排对照的事实 → 加卡 E", "pack-ui-narrative", "；".join(card_ev), 1.5)

    # ---- 规则 4：长片 → 壳 C（章节定位）
    if (isinstance(duration, (int, float)) and duration > LONG_S) or len(beats) >= BEATS_LONG:
        ev = []
        if isinstance(duration, (int, float)) and duration > LONG_S:
            ev.append(f"时长 {duration}s > {LONG_S}s")
        if len(beats) >= BEATS_LONG:
            ev.append(f"beats = {len(beats)} ≥ {BEATS_LONG}")
        hit(4, "长片需要章节定位 → 加壳 C", "pack-hud-monitor", "；".join(ev), 1.0)

    # ---- 规则 5：fails_on 命中 → 降权 + 写风险
    def penalty(pack: str, name: str, quote: str, evidence: str, delta: float) -> None:
        scores[pack] = round(scores[pack] - delta, 2)
        risks.append({"rule": 5, "pack": pack, "name": name, "quote": quote,
                      "evidence": evidence, "delta": -delta})
        hits.append({"rule": 5, "name": f"fails_on 命中 → {name}", "target": pack,
                     "evidence": evidence, "delta": -delta})

    if portrait:
        penalty("pack-hud-monitor", "竖屏壳会挤掉可用高度",
                "竖屏 + 顶条会同时挤压主体和字幕，可用高度掉到 0.75 以下",
                f"画幅 {aspect or '未写'}，竖屏；C 的 usable_h_with_bar 只有 0.86、with_badge 0.75", 1.0)

    refined = words_hit(alltext, REFINED_WORDS)
    if refined:
        penalty("pack-hud-monitor", "精致素材再套壳 = 双重包装",
                "双侧胶片齿孔 + 精致素材 = 双重包装，一眼就是套壳",
                f"命中精致类词 {'、'.join(refined)}", 1.0)

    if flow:
        penalty("pack-paper-brand", "有信息推进的内容套纸感会「卡住」",
                "需要信息推进的内容：11.4s 中位 + 零音效会像「卡住了」",
                f"命中流程类词 {'、'.join(flow)}", 1.0)

    mean_luma, luma_ev = luma_stats(plan, by_path)
    if mean_luma is not None and mean_luma >= BRIGHT_LUMA:
        penalty("pack-safe-talking", "亮底素材 + 只用投影会掉字",
                "亮底素材 + 只用投影不用描边 → 白字直接消失",
                f"{luma_ev} ≥ {BRIGHT_LUMA}；A 要改回描边（stroke 2.5px @720h）", 0.5)

    # 段数太少时比例不成立：4 段里 2 段带卡不等于「卡面过半」
    if len(segs) >= 8 and len(cards) / len(segs) >= 0.5:
        penalty("pack-ui-narrative", "卡面过半会闷",
                "卡面占片长过半 + 4.1s 中位 → 节奏闷死",
                f"{len(cards)}/{len(segs)} 段带 card", 1.0)

    # 卡在门槛边上要写出来：结论对，但只对一点点，下次素材一换就翻
    notes: list[str] = []
    if ratio is not None and abs(ratio - SPEECH_RATIO_MIN) < 0.05:
        notes.append(f"语音占比 {ratio:.3f} 贴着规则 1 的门槛 {SPEECH_RATIO_MIN}（差 {abs(ratio - SPEECH_RATIO_MIN):.3f}），"
                     f"库再多几支无语音素材，字幕系统就会从 A 翻到 B")
    if isinstance(duration, (int, float)) and abs(duration - SHORT_S) <= 15:
        notes.append(f"时长 {duration}s 贴着规则 1 的 {SHORT_S}s 边界，改稿一变长就会按「非短口播」重判")
    if isinstance(duration, (int, float)) and abs(duration - LONG_S) <= 20:
        notes.append(f"时长 {duration}s 贴着规则 4 的 {LONG_S}s 边界")
    if len(beats) in (BEATS_SHELL, BEATS_LONG):
        notes.append(f"beats = {len(beats)} 正好落在门槛上（规则 2 用 {BEATS_SHELL}、规则 4 用 {BEATS_LONG}），"
                     f"少一个 beat 结论就可能变")

    return {"scores": scores, "hits": hits, "risks": risks, "notes": notes,
            "facts": {"speech_ratio": ratio, "n_assets": n_assets, "n_speech": n_speech,
                      "duration_s": duration, "aspect": aspect, "beats": len(beats),
                      "segments": len(segs), "cards": len(cards), "portrait": portrait,
                      "luma": mean_luma, "luma_evidence": luma_ev,
                      "scene_words": scene, "flow_words": flow}}


# ---------------------------------------------------------------- 组包

def choose(scores: dict, members: dict) -> tuple[dict, list[dict]]:
    """按层挑：字幕系统取高分（同分按 set 里写的顺序，A 在前），壳与卡过阈值才选。"""
    subs = members["subtitle_system"]["packs"]
    ranked = sorted(subs, key=lambda p: (-scores.get(p, 0.0), subs.index(p)))
    sub = ranked[0]

    shell_packs = members["shell"]["packs"]
    shell = None
    for p in shell_packs:
        if scores.get(p, 0.0) >= PICK_THRESHOLD:
            shell = p
            break

    card_packs = members["cards"]["packs"]
    cards = None
    for p in card_packs:
        if scores.get(p, 0.0) >= PICK_THRESHOLD:
            cards = p
            break

    rejected = []
    for p in ranked[1:]:
        rejected.append({"pack": p, "layer": "subtitle_system",
                         "reason": f"同层得分 {scores.get(p, 0.0):.1f} 低于 {sub} 的 {scores.get(sub, 0.0):.1f}"})
    if shell is None:
        rejected.append({"pack": shell_packs[0], "layer": "shell",
                         "reason": f"得分 {scores.get(shell_packs[0], 0.0):.1f} 未过门槛 {PICK_THRESHOLD}"
                                   f"（规则 2/4 未命中，或规则 5 已降权）"})
    if cards is None:
        rejected.append({"pack": card_packs[0], "layer": "cards",
                         "reason": f"得分 {scores.get(card_packs[0], 0.0):.1f} 未过门槛 {PICK_THRESHOLD}"})
    return {"subtitle_system": sub, "shell": shell, "cards": cards}, rejected


def combo_id(choice: dict, combos: list[dict]) -> str | None:
    for c in combos:
        m = c.get("compose") or {}
        if (m.get("subtitle_system") == choice["subtitle_system"]
                and m.get("shell") == choice["shell"]
                and m.get("cards") == choice["cards"]):
            return c.get("id")
    return None


# ---------------------------------------------------------------- 输出

def commands(proj: Path, lib: Path, choice: dict) -> list[dict]:
    sub = choice["subtitle_system"]
    shell = choice["shell"]
    base = f'python3 scripts/render_film.py --project "{proj}" --library "{lib}"'
    out = [{"label": "只出字幕版", "cmd": f"{base} --pack {sub}"},
           {"label": "壳 + 字幕版", "cmd": f"{base} --pack {sub} --shell {choice.get('shell') or 'pack-hud-monitor'}"}]
    rec = f"{base} --pack {sub}" + (f" --shell {shell}" if shell else "")
    out.append({"label": "本次推荐组合", "cmd": rec,
                "note": ("与第一条相同" if rec == out[0]["cmd"] else "")})
    return out


def render_md(slug: str, set_name: str, intent: dict, plan: dict, res: dict,
              choice: dict, rejected: list[dict], cid, cmds: list[dict], lib: Path) -> str:
    f = res["facts"]
    L: list[str] = []
    L.append(f"# 包装选择 · {slug}")
    L.append("")
    L.append(f"由 `scripts/pick_pack.py` 生成（{datetime.now().strftime('%Y-%m-%d %H:%M')}）。"
             f"包装套 **{set_name}**：字幕系统（A 或 B）二选一 + 壳（C，0 或 1）+ 卡（E，0 或 1）。"
             f"素材库 `{lib}`。")
    L.append("")
    L.append("依据：intent.json"
             f"（{intent.get('duration_s')}s / {intent.get('aspect') or '未写画幅'} / "
             f"观众「{norm(intent.get('audience'))[:40]}」）、"
             f"plan.json（{f['beats']} 个 beat / {f['segments']} 段 / {f['cards']} 处 card）、"
             f"索引 counts（with_speech {f['n_speech']} / assets {f['n_assets']}）。")
    L.append("")
    L.append("## 结论")
    L.append("")
    L.append("| 层 | 选择 | 说明 |")
    L.append("|---|---|---|")
    L.append(f"| 字幕系统 | `{choice['subtitle_system']}` | 必选，二选一 |")
    L.append(f"| 壳 | {'`' + choice['shell'] + '`' if choice['shell'] else '不加'} | 0 或 1 |")
    L.append(f"| 卡 | {'`' + choice['cards'] + '`' if choice['cards'] else '不加'} | 0 或 1 |")
    L.append("")
    if cid:
        L.append(f"对应 `styles/set-qizao.yaml` 的格子 **{cid}**。")
        L.append("")
    L.append("| pack | 得分 |")
    L.append("|---|---|")
    for p, s in res["scores"].items():
        L.append(f"| `{p}` | {s:.1f} |")
    L.append("")
    L.append("## 命中的规则")
    L.append("")
    L.append("| 规则 | 判定 | 依据 | 加减分 |")
    L.append("|---|---|---|---|")
    for h in res["hits"]:
        L.append(f"| 规则 {h['rule']} | {h['name']} | {h['evidence']} | {h['delta']:+.1f} |")
    L.append("")
    L.append("## 被否掉的选项")
    L.append("")
    L.append("| 层 | 选项 | 原因 |")
    L.append("|---|---|---|")
    for r in rejected:
        L.append(f"| {r['layer']} | `{r['pack']}` | {r['reason']} |")
    L.append("")
    selected = {v for v in (choice["subtitle_system"], choice["shell"], choice["cards"]) if v}
    kept = [r for r in res["risks"] if r["pack"] in selected]
    dropped = [r for r in res["risks"] if r["pack"] not in selected]
    if kept:
        L.append("## 风险（规则 5 · 落在最终组合上）")
        L.append("")
        for r in kept:
            L.append(f"- **{r['pack']}** · {r['name']}（{r['delta']:+.1f}）")
            L.append(f"  - 实测原话：{r['quote']}")
            L.append(f"  - 本条依据：{r['evidence']}")
        L.append("")
    else:
        L.append("## 风险（规则 5 · 落在最终组合上）")
        L.append("")
        L.append("本轮没有落到最终组合的 fails_on 信号。**这不等于没风险**：")
        L.append("「主体偏上的竖屏素材」「动作撑不住卡」这两类只能看画面，脚本判不了，交给人看一眼。")
        L.append("")
    if dropped:
        L.append("## 降权记录（规则 5 · 作用在没选的包上）")
        L.append("")
        L.append("这几条不是最终组合的风险，而是解释「为什么没选它」：")
        L.append("")
        for r in dropped:
            L.append(f"- **{r['pack']}** · {r['name']}（{r['delta']:+.1f}）：{r['evidence']}")
            L.append(f"  - 实测原话：{r['quote']}")
        L.append("")
    if res.get("notes"):
        L.append("## 边缘判定")
        L.append("")
        for n in res["notes"]:
            L.append(f"- {n}")
        L.append("")
    L.append("## 三条命令行")
    L.append("")
    L.append("```bash")
    for c in cmds:
        L.append(f"# {c['label']}" + (f"（{c['note']}）" if c.get("note") else ""))
        L.append(c["cmd"])
    L.append("```")
    L.append("")
    L.append("卡不单独给参数：E 的卡由 `plan.json` 里段落的 `card` 字段（text / at / dur）驱动。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目目录（_cutskills/projects/<slug>）")
    ap.add_argument("--library", default=None, help="素材库根；不给就按项目路径往上找")
    ap.add_argument("--set", dest="set_path", default=None,
                    help="包装套 yaml，默认 styles/set-qizao.yaml")
    ap.add_argument("--json", action="store_true", help="打印机器可读结果")
    ap.add_argument("--no-write", action="store_true", help="不写 pack-choice.md")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    if not (proj / "intent.json").exists():
        raise SystemExit(f"没有 intent.json：{proj}")
    intent = read_json(proj / "intent.json")
    plan_f = proj / "plan.json"
    plan = read_json(plan_f) if plan_f.exists() else {}

    lib = Path(a.library).expanduser().resolve() if a.library else find_library(proj)
    idx = read_json(lib / "_cutskills" / "index.json")
    counts = idx.get("counts") or {}
    by_path = {r.get("path") or r.get("id"): r for r in (idx.get("assets") or [])}

    set_path = Path(a.set_path).expanduser() if a.set_path else STYLES / "set-qizao.yaml"
    if not set_path.is_absolute():
        set_path = (Path.cwd() / set_path).resolve()
    if not set_path.exists():
        raise SystemExit(f"包装套不存在：{set_path}")
    pset = yaml.safe_load(set_path.read_text())
    members = pset.get("members") or {}
    for layer in ("subtitle_system", "shell", "cards"):
        if layer not in members or not members[layer].get("packs"):
            raise SystemExit(f"包装套缺 members.{layer}.packs：{set_path}")

    res = score(intent, plan, counts, by_path)
    choice, rejected = choose(res["scores"], members)
    cid = combo_id(choice, pset.get("combos") or [])
    cmds = commands(proj, lib, choice)
    slug = norm(intent.get("slug")) or proj.name

    md_path = proj / "pack-choice.md"
    if not a.no_write:
        md_path.write_text(render_md(slug, pset.get("name") or set_path.stem, intent, plan,
                                     res, choice, rejected, cid, cmds, lib))

    if a.json:
        print(json.dumps({"project": str(proj), "slug": slug, "library": str(lib),
                          "set": str(set_path), "set_name": pset.get("name"),
                          "facts": res["facts"], "scores": res["scores"],
                          "choice": choice, "combo": cid, "hits": res["hits"],
                          "rejected": rejected, "risks": res["risks"],
                          "commands": cmds,
                          "md": None if a.no_write else str(md_path)},
                         ensure_ascii=False, indent=1))
        return 0

    print(f"项目 {slug}")
    print(f"字幕系统 {choice['subtitle_system']} · 壳 {choice['shell'] or '不加'} · 卡 {choice['cards'] or '不加'}"
          + (f"（格子 {cid}）" if cid else ""))
    for h in res["hits"]:
        print(f"  规则 {h['rule']}  {h['delta']:+.1f}  {h['name']} —— {h['evidence']}")
    if res["risks"]:
        print("风险：")
        for r in res["risks"]:
            print(f"  · {r['pack']} {r['name']}（{r['delta']:+.1f}）：{r['evidence']}")
    if not a.no_write:
        print(f"写出 {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
