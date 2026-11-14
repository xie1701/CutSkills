#!/usr/bin/env python3
"""CutSkills · 封面与标题文案：从**口播真值**里长出 3 个主标题 + 1 句副文案 + 1 条发布标题。

## 为什么要有这个

发布包里封面和标题是「点击率的全部」，但以前这条链子是零实现——片子剪完了，
封面标题靠人临场想，想完没有留痕：这个标题是哪句话来的？是不是自己编了个数？
一周后回看播放数据，没人能回答「标题 A 比标题 B 好」到底差在哪。

这个脚本把「文案」变成**可追溯的产物**：

  1. 三个主标题全部从片子自己的口播原文里切出来（`timeline.json` 的 `elements[].evidence`），
     每条都记着「来自第几格、原话是什么」；没有口播真值时退回 `ideation.json` 的钩子
     （连钩子 id 一起记），仍然可追溯；
  2. 主标题 ≤ 12 字、不带标点、不带营销腔、**不出现原文没有的数字或承诺**（G-06：
     宁可少一个爆款标题，不许编一个数）；
  3. 发布标题可以带钩子感，但同一条纪律：零无来源数字、零无来源承诺；
  4. `--pick N` 选定后写进 `decisions.md`（走 `log_decision.py`，不改历史行）；
  5. `--render` 出 1080×1920 封面图（PIL，用现有包装包的配色与字体），标题超宽自动缩号
     并回报实测宽度——「差不多能放下」不算数，要给像素数。

## 用法

  cover.py --project <项目目录>                 # → cover.json + cover.md
  cover.py --project <项目目录> --pick 2        # 选定第 2 条，记进 decisions.md
  cover.py --project <项目目录> --render        # 顺便出 out/cover.png（1080×1920）
  cover.py --project <项目目录> --json          # 只打印 JSON

渲染需要 PIL：先 `source scripts/runtime.sh env`（依赖都在 ~/.cutskills/py）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from libs import REPO  # noqa: E402

TITLE_MAX = 12
SUB_MAX = 20
PUBLISH_MAX = 30

# 主标题里绝对不许出现的字符：标点（封面大字不吃标点，观众读的是块面不是句子）
PUNCT = "，。！？；：、,.!?;:…—～~（）()【】「」『』\"'“”‘’·|｜/\\-_ "
# 数字：阿拉伯数字、百分号，以及「一/两/三…+量词」的金额与倍数（G-06 的金额模式同源）
NUM_RE = re.compile(r"[0-9０-９%％]|[零一二两三四五六七八九十百千万亿]+[年天块元倍成个条人分秒层格万亿]")
# 无来源承诺 / 绝对化营销腔（BRIEF：不带营销腔、不许承诺）
PROMO = ("保证", "百分百", "一定能", "稳赚", "翻倍", "省一半", "第一", "唯一",
         "顶级", "神器", "必买", "秒杀", "立竿见影", "最好", "最强", "必看")
# 口播里「在主张」的信号词：有这些词的短句更适合做封面标题
CLAIM_CUES = ("不只是", "不是", "别", "才", "却", "其实", "只能", "按", "该", "得", "要",
              "根本", "真正", "问题", "差别", "区别", "关键")
ROLE_BONUS = {"claim": 3, "payoff": 3, "turn": 2, "setup": 1, "evidence": 0, "warrant": 0}


# ------------------------------------------------------------------ 真值

def _read_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clauses(text: str) -> list[str]:
    """一句话切成短句：按标点断，去掉标点与空白（封面大字不吃标点）。"""
    out, buf = [], ""
    for ch in str(text or ""):
        if ch in PUNCT:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def unsafe(text: str) -> str | None:
    """标题会不会带出「原文没有的数字/承诺」或营销腔 → 返回原因，干净返回 None。"""
    t = str(text or "")
    m = NUM_RE.search(t)
    if m:
        return f"含数字「{m.group(0)}」（标题不出数字：原文没有的数不许编）"
    hit = [w for w in PROMO if w in t]
    if hit:
        return f"含无来源承诺/营销腔「{hit[0]}」"
    return None


def _trim(text: str, limit: int) -> tuple[str, bool]:
    t = str(text or "").strip()
    m = NUM_RE.search(t)          # 截断后可能把「十个」切成「十」，残留单个数字照样拦
    if m:
        return t, False
    return (t[:limit], len(t) > limit)


def load_truth(proj: Path) -> dict:
    tl = _read_json(proj / "timeline.json") or {}
    idea = _read_json(proj / "ideation.json") or {}
    brief = _read_json(proj / "brief.json") or {}
    intent = _read_json(proj / "intent.json") or {}
    vo = []
    for el in tl.get("elements") or []:
        ev = str((el or {}).get("evidence") or "").strip()
        if not ev:
            continue
        vo.append({"n": el.get("n"), "role": el.get("role"), "text": ev,
                   "at": el.get("at") if el.get("at") is not None else el.get("_start")})
    props = idea.get("proposals") or []
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in props if p.get("index") == sel), props[0] if props else {}) or {}
    hooks = []
    for h in prop.get("hooks") or []:
        line = str((h or {}).get("line") or "").strip()
        if line:
            hooks.append({"id": h.get("id"), "line": line,
                          "why": h.get("why") or h.get("angle") or h.get("type")})
    tb = tl.get("title_bar") or prop.get("title_bar") or {}
    return {
        "vo": vo, "hooks": hooks, "title_bar": tb,
        "topic": str(idea.get("topic_raw") or intent.get("raw") or brief.get("change") or "").strip(),
        "body_change": str(brief.get("change") or "").strip(),
        "audience": str(brief.get("audience") or "").strip(),
        "pack": tl.get("pack") or brief.get("pack"),
        "has_timeline": bool(tl),
    }


# ------------------------------------------------------------------ 标题

def _score(text: str, role: str | None) -> int:
    s = sum(2 for c in CLAIM_CUES if c in text)
    n = len(text)
    s += 2 if 5 <= n <= 10 else (1 if 4 <= n <= TITLE_MAX else -3)
    s += ROLE_BONUS.get(str(role or ""), 0)
    return s


def make_titles(truth: dict) -> tuple[list[dict], list[dict]]:
    """三个主标题：优先口播短句，不够再用钩子行兜底。返回 (选中的, 被守卫拦下的)。"""
    cands: list[dict] = []
    for line in truth["vo"]:
        for cl in clauses(line["text"]):
            if not cl:
                continue
            text, cut = _trim(cl, TITLE_MAX)
            if len(text) < 3:
                continue
            cands.append({"text": text, "score": _score(text, line.get("role")),
                          "source": {"kind": "vo_clause", "file": "timeline.json",
                                     "element": line.get("n"), "role": line.get("role"),
                                     "at": line.get("at"), "quote": line["text"], "clause": cl},
                          "truncated": cut, "order": (line.get("n") or 0)})
    for h in truth["hooks"]:
        for cl in clauses(h["line"]) or [h["line"]]:
            text, cut = _trim(cl, TITLE_MAX)
            if len(text) < 3:
                continue
            cands.append({"text": text, "score": _score(text, "hook") + 1,
                          "source": {"kind": "hook", "file": "ideation.json", "hook": h.get("id"),
                                     "quote": h["line"], "clause": cl},
                          "truncated": cut, "order": 99})
    tb = truth["title_bar"] or {}
    for key in ("main", "top"):
        val = str(tb.get(key) or "").strip()
        if val:
            text, cut = _trim(clauses(val)[0] if clauses(val) else val, TITLE_MAX)
            cands.append({"text": text, "score": _score(text, "title_bar") - 2,
                          "source": {"kind": "title_bar", "file": "timeline.json",
                                     "field": key, "quote": val},
                          "truncated": cut, "order": 98})

    rejected: list[dict] = []
    picked: list[dict] = []
    seen: set[str] = set()
    used_elements: set = set()
    ordered = sorted(cands, key=lambda c: (-c["score"], c["order"]))

    def take(c: dict) -> bool:
        why = unsafe(c["text"])
        if why is None and any(ch in c["text"] for ch in PUNCT):
            why = "主标题不许带标点"
        if why is None and c["text"] in seen:
            why = "与已选标题重复"
        if why:
            if len(rejected) < 12:
                rejected.append({"text": c["text"], "why": why, "source": c["source"]})
            return False
        seen.add(c["text"])
        el = (c["source"] or {}).get("element")
        if el is not None:
            used_elements.add(el)
        picked.append({**c, "chars": len(c["text"]),
                       "why": ("口播原话直接切出的短句，没有改写" if c["source"]["kind"] == "vo_clause"
                               else "来自选题钩子切入点（未改写）" if c["source"]["kind"] == "hook"
                               else "来自成片标题条（已是发布口径）")})
        return True

    # 第一轮：同一格只出一条（三个标题要能分别对应片子的三处论据，不是同一句的三种写法）
    for c in ordered:
        if len(picked) == 3:
            break
        el = (c["source"] or {}).get("element")
        if el is not None and el in used_elements:
            continue
        take(c)
    # 第二轮：口播真值不够（比如整片只有一格有 evidence）时再用同格的其他短句补齐
    for c in ordered:
        if len(picked) == 3:
            break
        if c["text"] in seen:
            continue
        take(c)
    return picked, rejected


def make_subtitle(truth: dict, titles: list[dict]) -> dict:
    tb = truth["title_bar"] or {}
    top = str(tb.get("top") or "").strip()
    main = str(tb.get("main") or "").strip()
    cands: list[dict] = []
    if main:
        cands.append({"text": main[:SUB_MAX], "source": {
            "kind": "title_bar", "file": "timeline.json", "fields": ["main"], "quote": main}})
    if top or main:
        text = "，".join(x for x in (top, main) if x)
        cands.append({"text": text[:SUB_MAX], "source": {
            "kind": "title_bar", "file": "timeline.json",
            "fields": [k for k in ("top", "main") if tb.get(k)],
            "quote": "｜".join(x for x in (top, main) if x)}})
    if truth["body_change"]:
        cands.append({"text": truth["body_change"][:SUB_MAX],
                      "source": {"kind": "brief", "file": "brief.json", "field": "change",
                                 "quote": truth["body_change"]}})
    for line in truth["vo"]:
        if str(line.get("role") or "") in ("payoff", "claim"):
            cl = clauses(line["text"])[0] if clauses(line["text"]) else ""
            if cl:
                cands.append({"text": cl[:SUB_MAX], "source": {
                    "kind": "vo_clause", "file": "timeline.json", "element": line.get("n"),
                    "role": line.get("role"), "quote": line["text"], "clause": cl}})
                break
    if not cands:
        return {"text": None, "chars": 0, "source": None,
                "note": "项目里没有标题条/需求卡/口播真值，副文案无从追溯——先补齐 brief.json 或 timeline.json"}
    rejected = []
    for c in cands:
        why = unsafe(c["text"])
        if why is None:
            return {**c, "chars": len(c["text"]), "note": "", "guard_rejected": rejected}
        rejected.append({"text": c["text"], "why": why})
    c = cands[0]
    return {**c, "chars": len(c["text"]), "guard_rejected": rejected,
            "note": "候选里没找到完全干净的句子，这条需要人工复核措辞"}


def make_publish(truth: dict, titles: list[dict], subtitle: dict) -> dict:
    cands: list[dict] = []
    for h in truth["hooks"]:
        cands.append({"text": h["line"][:PUBLISH_MAX],
                      "source": {"kind": "hook", "file": "ideation.json", "hook": h.get("id"),
                                 "quote": h["line"]},
                      "why": "选题钩子原文（发布标题要钩子感）"})
    main_title = titles[0]["text"] if titles else ""
    topic = truth["topic"]
    if main_title and topic:
        cands.append({"text": f"{main_title}，{topic}"[:PUBLISH_MAX],
                      "source": {"kind": "composite", "parts": [
                          {"file": "timeline.json", "field": "elements[].evidence",
                           "quote": titles[0]["source"].get("quote")},
                          {"file": "ideation.json", "field": "topic_raw", "quote": topic}],
                          "quote": f"{main_title}，{topic}"},
                      "why": "结论句 + 选题原话：既守口播真值，又带悬念"})
    if subtitle.get("text"):
        cands.append({"text": f"{main_title}，{subtitle['text']}"[:PUBLISH_MAX],
                      "source": {"kind": "composite", "parts": [
                          {"file": "timeline.json", "field": "elements[].evidence",
                           "quote": titles[0]["source"].get("quote") if titles else None},
                          {"file": subtitle["source"]["file"], "quote": subtitle["source"].get("quote")}],
                          "quote": f"{main_title}，{subtitle['text']}"},
                      "why": "结论句 + 副文案"})
    rejected = []
    for c in cands:
        why = unsafe(c["text"])
        if why:
            rejected.append({"text": c["text"], "why": why})
            continue
        return {**c, "chars": len(c["text"]), "guard_rejected": rejected, "note": ""}
    return {"text": None, "chars": 0, "source": None, "guard_rejected": rejected,
            "note": "所有候选都过不了 G-06（含无来源数字/承诺）——宁可空着，不许编"}


# ------------------------------------------------------------------ 渲染

def _pack_tokens(pack: str | None) -> dict:
    if not pack:
        return {}
    p = REPO / "packs" / f"{pack}.yaml"
    if not p.exists():
        return {}
    try:
        import yaml
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:                                                   # noqa: BLE001
        return {}
    return {"palette": ((doc.get("tokens") or {}).get("palette") or {}),
            "font": (doc.get("subtitle") or {}).get("font"),
            "emphasis_font": (doc.get("emphasis") or {}).get("font"),
            "canvas": doc.get("canvas") or {}}


def _hex_rgb(s: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(s or "").strip())
    if not m:
        return fallback
    v = m.group(1)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _split_balanced(text: str) -> list[str]:
    n = len(text)
    if n <= 5:
        return [text]
    half = (n + 1) // 2
    return [text[:half], text[half:]]


def render_cover(proj: Path, report: dict, width: int = 1080, height: int = 1920) -> dict:
    """1080×1920 封面：大字标题 + 副文案，配色/字体取该片包装包，超宽自动缩号。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return {"ok": False, "skipped": "PIL 不在当前解释器：先 `source scripts/runtime.sh env` 再跑 --render"}
    import fonts as fonts_mod

    tok = _pack_tokens(report["project"].get("pack"))
    pal = tok.get("palette") or {}
    ground = _hex_rgb(pal.get("ground"), (18, 16, 14))
    ink = _hex_rgb(pal.get("ink"), (255, 255, 255))
    accent = _hex_rgb(pal.get("accent"), (255, 230, 0))

    titles = report["titles"]
    if not titles:
        return {"ok": False, "skipped": "没有可用的主标题（口播真值不足），不渲封面"}
    idx = 0
    if report.get("picked") and report["picked"].get("index"):
        idx = int(report["picked"]["index"]) - 1
    title = titles[idx]["text"]
    subtitle = (report.get("subtitle") or {}).get("text") or ""

    fname = tok.get("font") or fonts_mod.default() or "得意黑"
    fpath = fonts_mod.file_path(fname)
    if fpath is None:
        return {"ok": False, "skipped": f"找不到字体文件「{fname}」——本机没装这只字体，先装字或改 fonts.yaml"}

    W, H = width, height
    img = Image.new("RGB", (W, H), ground)
    d = ImageDraw.Draw(img)
    # 底色上深下浅一点点，避免纯色块像占位图
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(ground[0] + 10 * k), int(ground[1] + 9 * k),
                                       int(ground[2] + 7 * k)))
    margin = int(W * 0.09)
    max_w = W - margin * 2
    lines = _split_balanced(title)

    def fit(size: int):
        f = ImageFont.truetype(str(fpath), size)
        widths = [d.textbbox((0, 0), ln, font=f)[2] for ln in lines]
        return f, widths

    size = int(H * 0.115)
    floor = 56
    while size > floor:
        f, widths = fit(size)
        if max(widths) <= max_w:
            break
        size -= 4
    f, widths = fit(size)
    line_h = int(size * 1.18)
    block_h = line_h * len(lines)
    y = int(H * 0.40) - block_h // 2
    # 标题上方的强调色短杠（用包装包的 accent，跟片子里的花字同色）
    d.rectangle([margin, y - int(size * 0.55), margin + int(W * 0.14),
                 y - int(size * 0.55) + max(6, size // 12)], fill=accent)
    for ln, w in zip(lines, widths):
        d.text(((W - w) // 2, y), ln, font=f, fill=ink)
        y += line_h
    if subtitle:
        s_size = int(size * 0.42)
        sf = ImageFont.truetype(str(fpath), max(24, s_size))
        sw = d.textbbox((0, 0), subtitle, font=sf)[2]
        while sw > max_w and s_size > 24:
            s_size -= 2
            sf = ImageFont.truetype(str(fpath), s_size)
            sw = d.textbbox((0, 0), subtitle, font=sf)[2]
        d.text(((W - sw) // 2, int(H * 0.62)), subtitle, font=sf, fill=accent)

    out = proj / "out" / "cover.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return {"ok": True, "file": str(out), "canvas": [W, H],
            "title": title, "title_index": idx + 1, "lines": lines,
            "font": fname, "font_file": fonts_mod.file_path(fname).name,
            "size_px": size, "measured_width_px": max(widths), "max_width_px": max_w,
            "palette": {k: pal.get(k) for k in ("ground", "ink", "accent") if pal.get(k)},
            "note": ("标题超宽会自动缩号：这里报的是实测最宽行像素，不是估计值"
                     if max(widths) <= max_w else "缩到最小字号仍超宽，需要人工改短标题")}


# ------------------------------------------------------------------ 产物

def _slug(proj: Path) -> str:
    """项目标识：成片真值里的 slug 优先（时间轴是唯一真值源），没有才用目录名。"""
    tl = _read_json(proj / "timeline.json") or {}
    return str(tl.get("slug") or proj.name)


def build(proj: Path) -> dict:
    proj = Path(proj).expanduser().resolve()
    truth = load_truth(proj)
    titles, rejected = make_titles(truth)
    subtitle = make_subtitle(truth, titles)
    publish = make_publish(truth, titles, subtitle)
    warnings = []
    if not truth["has_timeline"]:
        warnings.append("没有 timeline.json：标题只能来自 ideation/brief，出处是选题阶段文案而不是成片口播")
    if not truth["vo"]:
        warnings.append("timeline.json 里没有 elements[].evidence（口播真值）：封面标题追不到「片子里真的说过」")
    if len(titles) < 3:
        warnings.append(f"只生成出 {len(titles)} 个主标题（要求 3 个）：口播短句不够干净，"
                        "补写 evidence 或加选题钩子后再跑")
    return {
        "version": 1,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "project": {"slug": _slug(proj), "path": str(proj), "pack": truth["pack"],
                    "topic": truth["topic"] or None, "audience": truth["audience"] or None},
        "guard": {"title_max_chars": TITLE_MAX, "subtitle_max_chars": SUB_MAX,
                  "publish_max_chars": PUBLISH_MAX,
                  "title_no_punct": True, "no_numbers": True, "no_promises": True,
                  "note": "主标题不带标点、不出数字、不出无来源承诺（G-06）；发布标题同纪律，只放开标点"},
        "titles": titles,
        "subtitle": subtitle,
        "publish_title": publish,
        "rejected": rejected,
        "warnings": warnings,
        "picked": None,
        "render": None,
    }


def render_md(rep: dict) -> str:
    p = rep["project"]
    L = [f"# 封面与标题 · {p['slug']}", ""]
    if rep["warnings"]:
        L.append("> ⚠ " + "；".join(rep["warnings"]))
        L.append("")
    L.append(f"包装包 `{p.get('pack') or '—'}` · 选题 `{p.get('topic') or '—'}` · 生成于 {rep['generated']}")
    L.append("")
    L.append(f"## 一、封面主标题（≤{rep['guard']['title_max_chars']} 字 · 无标点 · 零无来源数字/承诺）")
    L.append("")
    if rep["titles"]:
        L.append("| # | 主标题 | 字数 | 出处 |")
        L.append("|---|---|---|---|")
        for i, t in enumerate(rep["titles"], 1):
            mark = " ✅" if rep.get("picked") and rep["picked"]["index"] == i else ""
            L.append(f"| {i}{mark} | {t['text']} | {t['chars']} | {_src_text(t['source'])} |")
        L.append("")
        L.append("逐条追溯（原话是这片的真值，标题只是从里面切出来）：")
        L.append("")
        for i, t in enumerate(rep["titles"], 1):
            s = t["source"]
            L.append(f"- **{i}. {t['text']}** ← {_src_text(s)}"
                     f"{'（截断到 %d 字）' % TITLE_MAX if t.get('truncated') else ''}")
            if s.get("quote"):
                L.append(f"  - 原话：「{s['quote']}」")
            if s.get("clause") and s.get("clause") != s.get("quote"):
                L.append(f"  - 切出的短句：「{s['clause']}」")
    else:
        L.append("没有可用的主标题：片子里没有可追溯的口播真值。补 `elements[].evidence` 后再跑。")
    L.append("")

    sub = rep.get("subtitle") or {}
    L.append(f"## 二、封面副文案（≤{rep['guard']['subtitle_max_chars']} 字）")
    L.append("")
    if sub.get("text"):
        L.append(f"**{sub['text']}**（{sub['chars']} 字）")
        L.append("")
        L.append(f"- 出处：{_src_text(sub['source'])}")
        if (sub.get("source") or {}).get("quote"):
            L.append(f"- 原话：「{sub['source']['quote']}」")
        if sub.get("note"):
            L.append(f"- 注意：{sub['note']}")
    else:
        L.append("（无）" + (sub.get("note") or ""))
    L.append("")

    pub = rep.get("publish_title") or {}
    L.append(f"## 三、发布标题（≤{rep['guard']['publish_max_chars']} 字 · 带钩子感但守 G-06）")
    L.append("")
    if pub.get("text"):
        L.append(f"**{pub['text']}**（{pub['chars']} 字）")
        L.append("")
        L.append(f"- 出处：{_src_text(pub['source'])}")
        if pub.get("why"):
            L.append(f"- 思路：{pub['why']}")
    else:
        L.append("（无）" + (pub.get("note") or ""))
    if pub.get("guard_rejected"):
        L.append("")
        L.append("被守卫拦下的候选（没有偷偷放行）：")
        for r in pub["guard_rejected"]:
            L.append(f"- 「{r['text']}」→ {r['why']}")
    L.append("")

    if rep.get("rejected"):
        L.append("## 四、标题候选里被拦下的（守卫实跑记录）")
        L.append("")
        for r in rep["rejected"][:8]:
            L.append(f"- 「{r['text']}」→ {r['why']}")
        L.append("")

    if rep.get("render"):
        r = rep["render"]
        L.append("## 五、封面图")
        L.append("")
        if r.get("ok"):
            L.append(f"- 文件：`{r['file']}`（{r['canvas'][0]}×{r['canvas'][1]}）")
            L.append(f"- 标题：{r['title']}（第 {r['title_index']} 条）· 行数 {len(r['lines'])}")
            L.append(f"- 字体：{r['font']}（{r['font_file']}）· 实测字号 {r['size_px']}px · "
                     f"最宽行 {r['measured_width_px']}px / 上限 {r['max_width_px']}px")
            L.append(f"- {r.get('note') or ''}")
        else:
            L.append(f"- 没出图：{r.get('skipped')}")
        L.append("")

    L.append("## 怎么用")
    L.append("")
    L.append("```bash")
    L.append("python3 scripts/cover.py --project <项目>            # 重出文案")
    L.append("python3 scripts/cover.py --project <项目> --pick 2   # 选定第 2 条，记进 decisions.md")
    L.append("python3 scripts/cover.py --project <项目> --render   # 出 1080×1920 封面图")
    L.append("```")
    L.append("")
    return "\n".join(L)


def _src_text(src: dict | None) -> str:
    if not src:
        return "—"
    if src.get("kind") == "vo_clause":
        return (f"`{src.get('file')}` 第 {src.get('element')} 格"
                f"（role={src.get('role') or '—'}）的口播原话")
    if src.get("kind") == "hook":
        return f"`{src.get('file')}` 钩子 {src.get('hook') or '—'}（选题切入点）"
    if src.get("kind") == "title_bar":
        return f"`{src.get('file')}` 标题条 {src.get('field') or '｜'.join(src.get('fields') or [])}"
    if src.get("kind") == "brief":
        return f"`{src.get('file')}` 的 `{src.get('field')}`（需求卡真值）"
    if src.get("kind") == "composite":
        return " + ".join(_src_text(x) for x in (src.get("parts") or []) if x)
    return str(src.get("file") or "—")


def write(proj: Path, rep: dict) -> tuple[Path, Path]:
    proj = Path(proj).expanduser().resolve()
    j = proj / "cover.json"
    m = proj / "cover.md"
    j.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    m.write_text(render_md(rep), encoding="utf-8")
    return j, m


def pick(proj: Path, rep: dict, n: int, said: str | None = None) -> dict:
    """选定第 n 条：写回 cover.json，并（唯一入口）追加一行 decisions.md。"""
    titles = rep.get("titles") or []
    if not 1 <= n <= len(titles):
        raise SystemExit(f"✗ --pick {n} 超范围：本次只生成出 {len(titles)} 条主标题")
    import log_decision
    t = titles[n - 1]
    rep["picked"] = {"index": n, "text": t["text"], "chars": t["chars"],
                     "source": t["source"], "at": datetime.now().isoformat(timespec="seconds")}
    quote = (t["source"] or {}).get("quote") or t["text"]
    said_text = said or f"封面主标题用第 {n} 条「{t['text']}」"
    did = f"cover.json picked=第 {n} 条「{t['text']}」（出处：{quote}）"
    f = log_decision.append_row(Path(proj).expanduser().resolve(), said_text, did, "accepted")
    rep["picked"]["decisions"] = str(f)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="封面与标题文案：3 个主标题 + 副文案 + 发布标题（可追溯）")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--pick", type=int, default=None, help="选定第 N 条主标题（写进 decisions.md）")
    ap.add_argument("--note", default=None, help="拍板时人的原话（进 decisions.md 的「用户原话」列）")
    ap.add_argument("--render", action="store_true", help="出 1080×1920 封面图（需要 PIL）")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    if not proj.is_dir():
        print(f"✗ 项目目录不存在：{proj}", file=sys.stderr)
        return 2

    rep = build(proj)
    if a.pick is not None:
        rep = pick(proj, rep, a.pick, a.note)
    if a.render:
        rep["render"] = render_cover(proj, rep)
    j, m = write(proj, rep)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    for w in rep["warnings"]:
        print(f"⚠ {w}")
    for i, t in enumerate(rep["titles"], 1):
        mark = " ✅" if rep.get("picked") and rep["picked"]["index"] == i else ""
        print(f"{i}{mark}. {t['text']}（{t['chars']} 字）← {_src_text(t['source'])}")
    if (rep.get("subtitle") or {}).get("text"):
        print(f"副文案：{rep['subtitle']['text']}")
    if (rep.get("publish_title") or {}).get("text"):
        print(f"发布标题：{rep['publish_title']['text']}")
    if rep.get("render"):
        r = rep["render"]
        print(f"封面图：{r.get('file') if r.get('ok') else r.get('skipped')}"
              + (f"（{r['size_px']}px · 最宽 {r['measured_width_px']}px）" if r.get("ok") else ""))
    print(f"→ {j}\n→ {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
