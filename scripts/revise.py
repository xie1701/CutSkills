#!/usr/bin/env python3
"""revise.py——对话式局部改片（R2 里程碑）：用户说人话，系统定位→改→只重渲必要内容。

支持的自然语言指令家族（中文口语，不动命令行的用户也能改片）：

  定位                                动作
  ───────────────────────────────    ─────────────────────────────────────
  「第N段」「第N格」                「换成X」「改成X」→ gen 卡文案（keyword/
  「开头」「结尾」「中间」「N秒附近」    quote/number 的 props.text；有 micro
  「那张…卡」「那段…」                 stage 的接力卡按每屏改）
  「前3秒不抓人」「钩子不吸引」         → 开头钩子改造：给了新文案就换第一张有字的卡；
                                        没给就把当前钩子念出来、请用户发新钩子（两步对话）
  「结尾换我自己的话」                 → 同上：先念当前结尾卡文案，请用户发想说的原话
                                     「中间说明太慢/太快」→ 定位中段 gen 元素 dur ±10%
                                        （音画同源：talking 段绝不改时长，明确拒绝）
                                     「音效小声点/太吵/大声点」→ props.sfx_gain_offset ±3dB
                                     「删掉贴纸」→ props.sticker = false
                                     「贴纸换成X」→ props.sticker.id = X
                                     「这段快点/慢点」→ gen 元素 dur ±10%（同上）

每一笔改动写进 `revisions.jsonl`（谁改的、改了什么、时间轴指纹、渲染缓存复用读数），
然后自动跑 check_timeline → 重签拍板锁 → render_hf（clip 缓存让未动片段零重渲）。
渲后不自动跑 gate——由调用方（Agent）跑完把读数念给用户听。

用法：
  python3 scripts/revise.py --project <项目> --library <库> --instruction "第3段的音效小声点"
  python3 scripts/revise.py --project <项目> --library <库> --instruction "结尾那张卡换成更闲了" --apply
默认演练（--dry-run 语义）：只定位、出 diff、不写盘；--apply 才真正改。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 口语→动作的解析都写在这一个文件里，改片纪律：每一笔必须有 revisions.jsonl 留痕。

CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10}


def _int_cn(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        return int(s)
    return CN_NUM.get(s)


def locate(tl: dict, text: str) -> tuple[dict | None, str]:
    """返回 (元素, 定位描述)。定位优先级：第N段/格 > N秒 > 开头/结尾 > 关键词。"""
    els = [e for e in tl.get("elements") or [] if e.get("template")]
    els.sort(key=lambda e: float(e.get("at") or e.get("_start") or e.get("in") or 0))
    m = re.search(r"第\s*([0-9一二两三四五六七八九十]+)\s*[段格]", text)
    if m:
        n = _int_cn(m.group(1))
        for e in els:
            if int(e.get("n") or 0) == n:
                return e, f"第{n}格（{e.get('template')}）"
        return None, f"第{n}格不存在（这片一共 {len(els)} 格）"
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*秒", text)
    if m:
        t = float(m.group(1))
        for e in els:
            st = float(e.get("at") or e.get("_start") or e.get("in") or 0)
            en = st + float(e.get("dur") or 0)
            if st - 0.01 <= t <= en + 0.01:
                return e, f"{t}s 落在第{e.get('n')}格（{e.get('template')}，{st:.1f}-{en:.1f}s）"
        return None, f"{t}s 落在片外"
    if "开头" in text or "第一段" in text or "第 1 段" in text:
        if els:
            return els[0], f"开头（第{els[0].get('n')}格，{els[0].get('template')}）"
    if "结尾" in text or "最后" in text:
        if els:
            return els[-1], f"结尾（第{els[-1].get('n')}格，{els[-1].get('template')}）"
    if "中间" in text or "中段" in text and els:
        mid = (float(els[0].get("at") or 0) + float(els[-1].get("at") or 0)) / 2
        best = min(els, key=lambda e: abs(float(e.get("at") or 0) - mid))
        return best, f"中段（第{best.get('n')}格，{best.get('template')}，最接近片中点）"
    return None, "定位不了：说「第N段」「N秒附近」「开头」「中间」「结尾」其中一种"


def _card_of(el: dict) -> dict | None:
    c = el.get("card")
    if isinstance(c, dict):
        return c
    p = el.get("props") or {}
    if isinstance(p.get("card"), dict):
        return p["card"]
    return None


def plan_change(el: dict, text: str) -> tuple[dict | None, str]:
    """返回 (props 补丁, 人话说明)。None = 拒绝（说明原因）。"""
    props = dict(el.get("props") or {})
    tpl = el.get("template")
    card = _card_of(el)

    # ---- 音效
    if re.search(r"音效|声音特效", text) and re.search(r"小声|太吵|太闹|轻点|小一点|低点", text):
        props["sfx_gain_offset"] = round(float(props.get("sfx_gain_offset") or 0) - 3.0, 2)
        return props, (f"第{el.get('n')}格音效整体 −3dB（累计 offset {props['sfx_gain_offset']}）"
                       "——提醒：音效变轻可能掉到可听度门禁（净抬升 ≥2.5dB）以下，"
                       "渲后把 check_sfx 读数念给用户，由用户拍板要不要回一点")
    if re.search(r"音效|声音特效", text) and re.search(r"大声|响点|明显|强点|大一点", text):
        props["sfx_gain_offset"] = round(float(props.get("sfx_gain_offset") or 0) + 3.0, 2)
        return props, f"第{el.get('n')}格音效整体 +3dB（累计 offset {props['sfx_gain_offset']}）"

    # ---- 贴纸
    if "贴纸" in text:
        if re.search(r"删掉|去掉|不要|移除", text):
            props["sticker"] = False
            return props, f"第{el.get('n')}格贴纸已删"
        m = re.search(r"换成\s*([a-z0-9\-]+)", text)
        if m:
            cur = props.get("sticker")
            props["sticker"] = {"id": m.group(1),
                                "pos": (cur or {}).get("pos") if isinstance(cur, dict) else "top-right"}
            return props, f"第{el.get('n')}格贴纸换成 {m.group(1)}"

    # ---- 开头钩子 / 结尾换自己的话：报告 §11 R2 最低演示的三类指令之二。
    # 这两类本质是「用户对内容不满但还没给新内容」——正确反应是把现状念出来、
    # 请用户发原话，而不是装听不懂或自作主张编文案。
    if re.search(r"抓人|不吸引|钩子|开场不|没记忆点", text) and not re.search(r"换成|改成", text):
        cur = (card or {}).get("text") or props.get("text") or "（这格没有可换的文案）"
        return None, (f"当前开头钩子是「{cur}」。把你想用的钩子原话发我，"
                      "格式：开头钩子换成<你的原话>，我改完自动重渲。")
    if re.search(r"我自己的话|我的原话|自己想说", text) and not re.search(r"换成|改成", text):
        cur = (card or {}).get("text") or props.get("text") or "（这格没有可换的文案）"
        return None, (f"当前这格文案是「{cur}」。把你想说的原话发我，"
                      "格式：结尾那张卡换成<你的原话>，我改完自动重渲。")

    # ---- 文案（gen 卡）。注意：必须是「换成/改成」二选一，不能写成字符类 [换成|改成]
    #（那是 换/成/|/改 任一字符，会把「换个说法，改成X」切成「个说法，改成X」）
    m = re.search(r"(?:换成|改成|改写为)\s*[「『\"]?(\S.*?)[」』\"]?(?:[。？！]|$)", text)
    if m and re.search(r"换成|改成|改写为", text):
        new = m.group(1).strip()
        if not new:
            return None, "换的内容是空的"
        if tpl in ("keyword", "quote", "number", "term", "chapter"):
            if card is not None:
                key = "text"
                old = str(card.get(key) or "")
                card[key] = new
                el["card"] = card
                return props, f"第{el.get('n')}格卡面「{old}」→「{new}」"
            old = str(props.get("text") or "")
            props["text"] = new
            return props, f"第{el.get('n')}格大字「{old}」→「{new}」"
        if tpl == "scene":
            return None, ("叙事场景的画面不由一句文案决定——要改内容请走 scene-forge 闭环"
                          f"（改 scenes/{(props.get('scene_spec') or props.get('scene'))}.json 再重签）")
    # ---- 节奏（只对代码画面成立；talking 音画同源绝不改时长）
    # 注意方向：用户说「太快/太慢」是在抱怨现状——太慢要加速（缩短），太快要放慢（拉长）
    if re.search(r"快点|慢点|节奏[快慢]|太快|太慢|太急|太拖", text):
        if str(el.get("kind") or "") == "clip" or el.get("audio"):
            return None, ("这格带着真人声音，改时长就是改口播——音画同源纪律不允许。"
                          "要调这段节奏请回到剪辑层改 in/out（会动到台词取舍），不是一句话的事。")
        if el.get("template"):
            dur = float(el.get("dur") or 0)
            # 缩短（加速）：快点/太快/太慢/太拖/节奏快；拉长（放慢）：慢点/太急/节奏慢
            shrink = bool(re.search(r"快点|太快|太慢|太拖|节奏快", text))
            nd = round(dur * (0.9 if shrink else 1.1), 3)
            el["dur"] = nd
            verb = "加速" if shrink else "放慢"
            return props, f"第{el.get('n')}格{verb}：时长 {dur:.2f}s → {nd:.2f}s"
    return None, ("听不懂这个组合。目前能改：卡面文案（换成X）、音效大小（音效小声点）、"
                  "贴纸（删掉贴纸/贴纸换成X）、代码画面节奏（这段快点/慢点）。")


def main() -> int:
    ap = argparse.ArgumentParser(description="对话式局部改片（R2）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default="")
    ap.add_argument("--instruction", required=True, help="用户原话")
    ap.add_argument("--apply", action="store_true", help="真正写盘；缺省只演练出 diff")
    ap.add_argument("--no-render", action="store_true", help="只改不渲（改完由 Agent 决定）")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    tl_p = proj / "timeline.json"
    tl = json.loads(tl_p.read_text())

    el, where = locate(tl, a.instruction)
    # 钩子/结尾换自己的话：目标必须是有文案的卡，不是落在 3s 的那格 B-roll
    if re.search(r"抓人|不吸引|钩子|我自己的话|我的原话", a.instruction):
        has_text = bool((_card_of(el) or {}).get("text") if el else None) or bool(
            el and (el.get("props") or {}).get("text"))
        if not has_text:
            els = [e for e in tl.get("elements") or []
                   if (_card_of(e) or {}).get("text") or (e.get("props") or {}).get("text")]
            if els:
                key = "开头" if re.search(r"抓人|不吸引|钩子|开场", a.instruction) else "结尾"
                el = els[0] if key == "开头" else els[-1]
                where = f"{key}（第{el.get('n')}格，{el.get('template')}）"
    print(f"· 定位：{where}")
    if el is None:
        return 2
    patch, note = plan_change(el, a.instruction)
    print(f"· 动作：{note}")
    if patch is None:
        return 2
    if not a.apply:
        print("· （演练模式，加 --apply 才写盘）")
        return 0

    # 写盘：元素级补丁 + 字数守卫复核 + 留痕
    props = dict(el.get("props") or {})
    props.update(patch)
    el["props"] = props
    problems = []
    try:
        import check_timeline as TL
        idx = {}
        if a.library:
            try:
                idx = TL.load_index(Path(a.library))
            except Exception:                      # noqa: BLE001
                idx = {}
        # 元素级最小复核：卡文案长度与守卫按全量 check 走（这里只跑它，读数留给调用方）
        probs = TL.check(tl, idx, proj=proj)
        problems = [p for lv, p in probs if lv == "error"]
    except Exception as e:                         # noqa: BLE001
        problems = [f"check_timeline 跑不了：{e}"]
    if problems:
        print("✗ 改动过不了静态守卫，已回滚：", file=sys.stderr)
        for p in problems[:6]:
            print(f"  · {p}", file=sys.stderr)
        return 3
    tl_p.write_text(json.dumps(tl, ensure_ascii=False, indent=1))

    rev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "instruction": a.instruction, "element": el.get("n"),
           "note": note, "timeline_sha": None}
    import hashlib
    rev["timeline_sha"] = hashlib.sha1(tl_p.read_bytes()).hexdigest()[:12]

    # 重签拍板锁（用户主动改片 = 新一次拍板；留痕在 revisions.jsonl）
    r = subprocess.run([sys.executable, str(HERE / "plan_card.py"),
                        "--project", str(proj),
                        "--approve", f"改片重签：{a.instruction}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"⚠ 拍板锁重签失败：{r.stderr[-300:]}", file=sys.stderr)
        return 4
    if a.no_render:
        with (proj / "revisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rev, ensure_ascii=False) + "\n")
        print(f"· 已改并重签：{rev['timeline_sha']}")
        return 0
    cmd = [sys.executable, str(HERE / "render_hf.py"), "--project", str(proj),
           "--quality", "looks", "--bgm", "auto"]
    if a.library:
        cmd += ["--library", a.library]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    # 留痕补渲染读数：缓存命中/重用行如实记录，让「只重渲必要内容」可从 revisions.jsonl 复核
    reuse = [ln.strip() for ln in out.splitlines()
             if re.search(r"缓存|复用|已存在|跳过", ln)][:4]
    if reuse:
        rev["render_reuse"] = reuse
    rev["render_exit"] = r.returncode
    with (proj / "revisions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rev, ensure_ascii=False) + "\n")
    tail = out.strip().splitlines()[-3:]
    print("\n".join(tail))
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
