#!/usr/bin/env python3
"""track.py——第一句话先判「你属于哪条轨」，**给猜测让人点头，不拿开放题审人**。

BRIEF 的两条轨道不是内容方法，是两套成功口径（原文『内容配比』一节只有 5A 人群档位，
没有门店/博主二分——所以本脚本判的不是「原文哪一档」，而是**这套账号到底在追什么**）：

  门店轨 —— 实体店老板，做同城获客；成功标准 = 评论区咨询 / 私信 / 到店
  博主轨 —— 个人创作者，追泛流量；成功标准 = 3 秒留存 / 完播 / 涨粉

交互口径（BRIEF 的硬约束，别改回问卷）：
  先猜、再让确认。输出一句话「我猜你是…，因为…，对吗？」+ 2 个可改选项；
  用户点头跑 `--confirm`，用户改判跑 `--set 门店|博主`。**不问「你是做什么的？」这种开放题。**

判定不许黑箱：每个线索词都要留原文片段（field + clue + quote）写进 account.json 的 basis，
判完自己看得见证据在哪一句上；零线索时明确标 `default_guess=true` 并说明「这是默认猜测」。

落库：<项目>/account.json —— track / basis / confirmed / decided_by，外加两条轨各自的
成功指标（tracks 全量 + 当前轨的 success_metrics 便捷字段），复盘与日历都读它。

用法：
  track.py --project <项目目录>                 # 猜一次（读 intent.json / brief.json）
  track.py --project <项目目录> --ask "<用户原话补充>"
  track.py --project <项目目录> --confirm       # 用户点头，确认现在这条轨
  track.py --project <项目目录> --set 门店       # 用户改判
  track.py --project <项目目录> --show          # 只看已有结论

退出码：0 写成本/只看 / 2 用法错（--set 值不认识） / 3 读不到原话（可读提示，不甩 traceback）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ACCOUNT_NAME = "account.json"
TRACKS = ("门店", "博主")

# 判定词表：每条线索都带权重，命中的原句片段进 basis，不许黑箱。
# 词表是本仓的工程实现（原文未给出判定词），归因写进 account.json 的 basis_source。
TRACK_SPEC = {
    "门店": {
        "label": "门店轨",
        "definition": "实体店老板，做同城获客",
        "success_metrics": ["评论区咨询", "私信", "到店"],
        "clues": [
            ("同城", 3), ("到店", 3), ("门店", 3), ("实体店", 3), ("堂食", 3), ("客流", 3),
            ("店", 2), ("老板", 2), ("顾客", 2), ("回头客", 2), ("房租", 2), ("铺子", 2),
            ("招牌", 2), ("开店", 2), ("分店", 2),
            ("选址", 1), ("商圈", 1), ("外卖", 1), ("生意", 1),
        ],
    },
    "博主": {
        "label": "博主轨",
        "definition": "个人创作者，追泛流量",
        "success_metrics": ["3 秒留存", "完播", "涨粉"],
        "clues": [
            ("涨粉", 3), ("完播", 3), ("3秒留存", 3), ("3 秒留存", 3), ("播放量", 3),
            ("粉丝", 3), ("账号", 3),
            ("博主", 2), ("创作者", 2), ("泛流量", 2), ("人设", 2), ("个人IP", 2),
            ("个人ip", 2), ("内容号", 2), ("爆款", 2), ("流量", 2), ("播放", 2),
            ("个人", 1), ("点赞", 1), ("收藏", 1), ("转发", 1), ("关注", 1), ("vlog", 1),
        ],
    },
}

BASIS_SOURCE = ("判定词表是本仓自建扩展，非原文（原文『内容配比』一节只有 5A 人群档位，"
                "没有门店/博主二分；BRIEF 才把账号分成两条轨）")
METRICS_SOURCE = ("成功指标口径来自 BRIEF 双轨定义（原文只给 5A 人群与内容配比，"
                  "没给「成功标准」这一层）")
# 零线索时的默认猜测：原文的案例几乎全是门店（老赵烧烤店、女装店、瓷砖店…），先按门店轨猜，
# 让用户一句话改判；这一层是本仓自建，非原文。
DEFAULT_TRACK = "门店"


# ---------------------------------------------------------------- 读原话

def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


INTENT_FIELDS = ("raw", "promise", "audience")
BRIEF_FIELDS = ("audience", "change", "audience_present_state", "promise")


def collect_inputs(proj: Path, ask: str = "") -> tuple[dict[str, str], list[str]]:
    """把用户开口原话收成 {字段名: 原文}，字段名要能让人直接翻到出处。"""
    texts: dict[str, str] = {}
    seen: list[str] = []
    intent = _read_json(proj / "intent.json")
    for f in INTENT_FIELDS:
        v = str(intent.get(f) or "").strip()
        if v:
            texts[f"intent.json:{f}"] = v
    if texts:
        seen.append("intent.json")
    brief = _read_json(proj / "brief.json")
    for f in BRIEF_FIELDS:
        v = str(brief.get(f) or "").strip()
        if v and v not in texts.values():
            texts[f"brief.json:{f}"] = v
    if any(k.startswith("brief.json:") for k in texts):
        seen.append("brief.json")
    if ask.strip():
        texts["--ask"] = ask.strip()
        seen.append("--ask")
    return texts, seen


# ---------------------------------------------------------------- 判定

def _quote(text: str, s: int, e: int, pad: int = 8) -> str:
    lo, hi = max(0, s - pad), min(len(text), e + pad)
    return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def _hits(text: str, clues: list[tuple[str, int]]) -> list[tuple[int, int, str, int]]:
    """同一段话里长词优先、重叠只算一次：「门店」命中后不再把里面的「店」再算一遍。"""
    spans = [(m.start(), m.end(), w, n)
             for w, n in clues for m in re.finditer(re.escape(w), text)]
    spans.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    kept: list[tuple[int, int, str, int]] = []
    for s, e, w, n in spans:
        if any(not (e <= ks or s >= ke) for ks, ke, _, _ in kept):
            continue
        kept.append((s, e, w, n))
    return sorted(kept)


def judge(texts: dict[str, str]) -> dict:
    """纯函数判定：给 texts，回 score/basis/free-guess 说明，不碰磁盘。"""
    per: dict[str, dict] = {}
    for track, spec in TRACK_SPEC.items():
        basis: list[dict] = []
        for field, text in texts.items():
            for s, e, word, weight in _hits(text, spec["clues"]):
                basis.append({"field": field, "clue": word, "weight": weight,
                              "quote": _quote(text, s, e)})
        basis.sort(key=lambda b: -b["weight"])
        per[track] = {"score": sum(b["weight"] for b in basis), "basis": basis}

    a, b = TRACKS
    if per[a]["score"] > per[b]["score"]:
        guess, evidence_free = a, False
    elif per[b]["score"] > per[a]["score"]:
        guess, evidence_free = b, False
    else:
        guess, evidence_free = DEFAULT_TRACK, True
    if evidence_free and per[a]["score"] and per[b]["score"]:
        reason = (f"门店线索 {per[a]['score']} 分、博主线索 {per[b]['score']} 分，两条轨打平，"
                  f"先按{DEFAULT_TRACK}轨猜")
    elif evidence_free:
        reason = (f"你的原话里没有任何门店/博主的线索，我先按{DEFAULT_TRACK}轨猜"
                  f"（原文案例几乎全是门店：老赵烧烤店、女装店、瓷砖店…）")
    else:
        words = []
        for item in per[guess]["basis"]:
            if item["clue"] not in words:
                words.append(item["clue"])
        reason = "你的原话里出现了" + "、".join(f"「{w}」" for w in words[:4])
    return {"track": guess, "scores": {t: per[t]["score"] for t in TRACKS},
            "basis": per[guess]["basis"], "reason": reason,
            "default_guess": evidence_free,
            "dissent": (per[b if guess == a else a]["basis"][:2]) if not evidence_free else []}


def guess_line(track: str, reason: str) -> str:
    spec = TRACK_SPEC[track]
    return f"我猜你是{spec['label']}（{spec['definition']}），因为{reason}，对吗？"


def options_line(track: str) -> list[str]:
    other = "博主" if track == "门店" else "门店"
    # 不点头也往下走：猜测就是默认值（BRIEF：只给猜测让人确认，不拿开放题卡流程）
    return [f"对，就是{TRACK_SPEC[track]['label']}（我跑 --confirm）",
            f"不对，我是{TRACK_SPEC[other]['label']}（我跑 --set {other}）",
            "不答也行：就按这个猜测往下走，要改一句话的事"]


# ---------------------------------------------------------------- 落库

def build_account(proj: Path, texts: dict[str, str], inputs: list[str],
                  *, forced: str | None = None, confirm: bool = False,
                  prev: dict | None = None) -> dict:
    if forced:
        track = forced
        decided_by, confirmed = "user", True
        basis: list[dict] = []
        for f, t in texts.items():
            for s, e, w, n in _hits(t, TRACK_SPEC[track]["clues"]):
                basis.append({"field": f, "clue": w, "weight": n, "quote": _quote(t, s, e)})
        reason = f"你自己拍板改成{TRACK_SPEC[track]['label']}（--set {track}）"
        scores = {t: 0 for t in TRACKS}
        default_guess = False
        dissent: list[dict] = []
        if prev and prev.get("scores"):
            scores = prev["scores"]
        # 证据跟改判对着干也要如实留着：用户改判优先，但账面不能说改就改
        auto = judge(texts) if texts else None
        if auto and auto["track"] != track and not auto["default_guess"]:
            dissent = auto["basis"][:3]
            reason += f"；注意线索更偏{TRACK_SPEC[auto['track']]['label']}，已按你的话走"
    elif confirm and prev and prev.get("track"):
        track = prev["track"]
        decided_by, confirmed = "user", True
        basis = list(prev.get("basis") or [])
        reason = "你点头确认了这个猜测（--confirm）"
        scores = prev.get("scores") or {t: 0 for t in TRACKS}
        default_guess = bool(prev.get("default_guess"))
        dissent = list(prev.get("dissent") or [])
    else:
        j = judge(texts)
        track, basis, reason = j["track"], j["basis"], j["reason"]
        scores, default_guess, dissent = j["scores"], j["default_guess"], j["dissent"]
        decided_by, confirmed = "guess", False

    spec = TRACK_SPEC[track]
    now = datetime.now().isoformat(timespec="seconds")
    doc = {
        "version": 1,
        "track": track,
        "track_label": spec["label"],
        "definition": spec["definition"],
        "success_metrics": spec["success_metrics"],
        "success_metrics_source": METRICS_SOURCE,
        "basis": basis,
        "basis_source": BASIS_SOURCE,
        "basis_reason": reason,
        "scores": scores,
        "default_guess": default_guess,
        "dissent": dissent,
        "guess_line": guess_line(track, reason),
        "options": options_line(track),
        "confirmed": confirmed,
        "decided_by": decided_by,
        "decided_at": now,
        "inputs": inputs,
        "project": str(proj),
        # 两条轨的成功口径都写进去：复盘/日历读得到「另一条轨怎么算成功」，
        # 用户中途改判也不必重跑判定
        "tracks": {t: {"label": s["label"], "definition": s["definition"],
                       "success_metrics": s["success_metrics"]}
                   for t, s in TRACK_SPEC.items()},
        "created": (prev or {}).get("created") or now,
        "updated": now,
    }
    return doc


def render(doc: dict) -> str:
    lines = [f"判定：{doc['track_label']}（{doc['definition']}）"
             f" · {doc['decided_by']} · confirmed={doc['confirmed']}",
             f"  成功指标：{' / '.join(doc['success_metrics'])}",
             f"  依据：{doc['basis_reason']}"]
    for b in doc["basis"][:6]:
        lines.append(f"    - {b['field']} 命中「{b['clue']}」(+{b['weight']})：{b['quote']}")
    if not doc["basis"] and doc.get("default_guess"):
        lines.append("    - （没有命中任何线索词，这是默认猜测）")
    if doc.get("dissent"):
        lines.append("  反向线索（留着备查，不覆盖你的判断）：")
        for b in doc["dissent"]:
            lines.append(f"    - {b['field']} 命中「{b['clue']}」：{b['quote']}")
    if not doc["confirmed"]:
        lines.append("")
        lines.append(doc["guess_line"])
        for i, o in enumerate(doc["options"], 1):
            lines.append(f"  {i}. {o}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="判定账号轨（门店轨 / 博主轨）：先猜、再让用户确认")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--ask", default="", help="用户补充的一句原话（进判定证据）")
    ap.add_argument("--set", dest="force", default=None,
                    help="用户改判：门店 或 博主（也认 门店轨/博主轨）")
    ap.add_argument("--confirm", action="store_true", help="用户点头确认现有猜测")
    ap.add_argument("--show", action="store_true", help="只打印已有 account.json")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    acc_p = proj / ACCOUNT_NAME
    prev = _read_json(acc_p) if acc_p.exists() else {}

    forced = None
    if a.force is not None:
        raw = a.force.strip().rstrip("轨")
        if raw not in TRACKS:
            print(f"--set 只认 {'/'.join(TRACKS)}（收到「{a.force}」）", file=sys.stderr)
            return 2
        forced = raw

    if a.show and prev and not forced and not a.ask and not a.confirm:
        print(render(prev))
        print(f"\n（account.json：{acc_p}）")
        return 0

    texts, inputs = collect_inputs(proj, a.ask)
    # 已经有判定、这次又没给新原话：沿用上一次，别让人再念一遍（也不覆盖 confirmed 状态）
    if not texts and not forced and not a.confirm and prev.get("track"):
        print(render(prev))
        print(f"\n（没读到新原话，沿用上一次的判定；account.json：{acc_p}）")
        return 0
    # --set 是用户拍板，不需要原话；--confirm 认的是上一次的猜测；只有「要猜」才必须有输入
    if not texts and not forced and not (a.confirm and prev.get("track")):
        print(f"没读到你的原话：「{proj}」里没有 intent.json / brief.json 的可读字段。\n"
              f"  两条路任选：\n"
              f"    1) 先建项目：python3 scripts/init_project.py --library <素材库> "
              f"--intent \"<你的原话>\" --slug {proj.name}\n"
              f"    2) 直接补一句：python3 scripts/track.py --project \"{proj}\" "
              f"--ask \"<你的原话>\"", file=sys.stderr)
        return 3

    doc = build_account(proj, texts, inputs, forced=forced, confirm=a.confirm, prev=prev)
    proj.mkdir(parents=True, exist_ok=True)
    acc_p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(render(doc))
    print(f"\n→ {acc_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
