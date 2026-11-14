#!/usr/bin/env python3
"""CutSkills · 剪辑与包装方案卡（动手渲染前的人类拍板门禁）。

原则：渲染一次视频要跑浏览器与编码，改文字方案只需几秒钟。
所以在正式渲染前，先把「每句说什么、配什么画面、挂什么标题条/大字/动效卡/表情贴纸/音效」
整理成一张没有黑话的清晰方案卡。用户确认（`--approve`）后写入 `lock.json` 时间轴哈希，
`render_hf.py` 验过锁才放行渲染。

用法：
  plan_card.py --project <项目目录> [--show]
  plan_card.py --project <项目目录> --approve "认可"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
try:
    import yaml
except ImportError:
    yaml = None

MARK = "<!-- plan-card: v2 -->"

MG_LABEL = {
    "stat": "数字放大卡",
    "steps": "步骤流程卡",
    "versus": "双栏对比卡",
    "term": "名词解释卡",
    "proportion": "比例切分条",
    "day-night": "早晚接力卡",
    "chips": "招牌名牌卡",
}

VFX_LABEL = {
    "punch_zoom": "冲击推镜+闪光",
    "slow_push": "电影感缓推",
    "flash_cut": "光学闪切",
    "spotlight": "聚光微暗角",
}


def _style(root: Path, pack_or_style: str | None) -> dict:
    if not pack_or_style or yaml is None:
        return {}
    for sub in ("packs", "styles"):
        p = root / sub / f"{pack_or_style}.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
    return {}


def _card_label(c: dict) -> str:
    kind = str(c.get("kind") or "keyword")
    text = str(c.get("text") or "")
    if kind in MG_LABEL and not text:
        d = c.get("data") or {}
        text = f"{d.get('value', '')}{d.get('unit', '')} {d.get('label', '') or d.get('title', '') or d.get('term', '')}".strip()
    if kind in MG_LABEL:
        return f"{MG_LABEL[kind]}「{text}」" if text else MG_LABEL[kind]
    if kind == "behind":
        return f"人物脑后大字「{text}」"
    return f"高光大字「{text}」"


def build(proj: Path, root: Path) -> str:
    brief = json.loads((proj / "brief.json").read_text()) if (proj / "brief.json").exists() else {}
    tl = json.loads((proj / "timeline.json").read_text())
    pack_name = tl.get("pack") or brief.get("pack") or tl.get("style_pack") or "P1-warm"
    st = _style(root, pack_name)

    L: list[str] = []
    A = L.append

    A(MARK)
    A(f"# 剪辑与包装方案卡 · {proj.name}")
    A("")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 确认无误请回复 **「认可」**（或直接指出想改哪一句/哪张卡），确认后立即开始合成高清成片。")
    A("")
    A("## 一、成片总览")
    A("")
    aud = brief.get("audience") or "目标观众"
    chg = brief.get("change") or "记住核心观点"
    A(f"- **给谁看**：{aud}（看完让他：{chg}）")
    A(f"- **总时长**：**{tl.get('duration_s')} 秒**（共 {len(tl.get('elements') or [])} 个画面段落）")
    A(f"- **视觉包装风格**：`{pack_name}`（{st.get('label') or st.get('look_notes') or '高清竖屏包装'}）")
    tb = tl.get("title_bar")
    if isinstance(tb, dict) and (tb.get("main") or tb.get("text")):
        top_t = f"【{tb['top']}】" if tb.get("top") else ""
        A(f"- **开篇悬念标题条**：{top_t}**{tb.get('main') or tb.get('text')}**（黄金前 4.5 秒吸睛）")
    bs = tl.get("beat_sync")
    if isinstance(bs, dict) and bs.get("checked_cuts"):
        A(f"- **音乐与卡点节奏**：情绪 `{tl.get('bgm_mood') or 'upbeat'}` · 鼓点踩点率 **{bs.get('hit_rate', 0):.0%}**")
    else:
        A(f"- **背景配乐**：CC0 免版权曲库 `{tl.get('bgm_mood') or 'upbeat'}` 情绪（人声说话时音乐自动压低让路，绝不抢台词）")
    A("")

    A("## 二、逐段分镜：说什么 / 画面是什么 / 包装特效")
    A("")
    A("| 段号 | 时间(秒) | 台词内容 | 画面来源 | 叠加包装（花字 / 动效卡 / 贴纸 / 镜头特效） |")
    A("|---|---|---|---|---|")
    t0 = 0.0
    for e in tl.get("elements") or []:
        if e.get("track") == "overlay":
            continue
        if e["kind"] == "clip":
            d = (float(e["audio"]["out"]) - float(e["audio"]["in"])) if e.get("audio") else (float(e["out"]) - float(e["in"]))
        else:
            d = float(e.get("dur") or 0)
        span = f"{t0:.1f}–{t0 + d:.1f}"
        t0 += d
        said = (e.get("evidence") or "—").replace("|", "／")
        if e.get("kind") == "gen":
            tpl = e.get("template") or "gen"
            vis = f"代码生成动效（`{tpl}`）"
        elif e.get("kind") == "image":
            vis = f"图片特写（{Path(str(e.get('asset') or '')).name[-18:]}）"
        elif e.get("medium") == "talking":
            vis = f"真人出镜说话（{Path(str(e.get('asset') or '')).name[-18:]}）"
        else:
            vis = f"实拍细节画面（{Path(str(e.get('asset') or '')).name[-18:]}）"

        extras: list[str] = []
        if e.get("card"):
            extras.append(_card_label(e["card"]))
        stk = e.get("sticker")
        if isinstance(stk, dict) and stk.get("id"):
            extras.append(f"情绪贴纸 `{stk['id']}`")
        elif isinstance(stk, str) and stk:
            extras.append(f"情绪贴纸 `{stk}`")
        vfx = str(e.get("vfx") or "")
        if vfx:
            extras.append(VFX_LABEL.get(vfx, vfx))
        A(f"| #{e['n']} | {span} | {said} | {vis} | {' ＋ '.join(extras) or '高清无标点字幕'} |")
    A("")

    A("## 三、自动质量守卫（渲染前后逐项实测）")
    A("")
    A("- **台词与字幕**：单行无标点、按自然换气切句、不切断词语、严禁编造没说过的词")
    A("- **版面防遮挡**：动效卡与大字自动避开人脸和底部字幕安全区，字底对比度实测达标（ΔL ≥ 12）")
    A("- **声音响度**：平台标准响度（−14 LUFS，不忽大忽小、不爆音），全部配乐与音效均附 CC0 免版权留痕")
    A("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成剪辑与包装方案确认卡并执行拍板上锁")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--approve", nargs="?", const="", default=None,
                    help="确认方案并签发 lock.json，放行 render_hf.py")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    root = Path(a.root).expanduser().resolve()
    out = proj / "方案.md"

    if a.approve is not None:
        if not out.exists():
            out.write_text(build(proj, root), encoding="utf-8")
        rec = {"approved": True, "at": datetime.now().isoformat(timespec="seconds"),
               "note": a.approve or "认可"}
        (proj / "approval.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lock import lock as _lock
        from log_decision import append_row
        d = _lock(proj, rec["note"])
        append_row(proj, said="确认剪辑与包装方案", did=f"签发时间轴锁 {d['sha256'][:12]}", verdict="accepted")
        print(f"已拍板上锁：sha {d['sha256'][:12]} · {d['elements']} 格 · {d['duration_s']}s")
        return 0

    text = build(proj, root)
    out.write_text(text, encoding="utf-8")
    print(f"→ {out}")
    if a.show:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
