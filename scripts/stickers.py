#!/usr/bin/env python3
"""CutSkills · 百万剪辑师级动态表情贴纸、悬念标题条与镜头特效层（SVG + GSAP）。

为什么不用系统 Emoji 字符：
无头浏览器在跨平台（macOS / Linux）渲染裸 Emoji 字形不一致，且平面色块像廉价表情包。
本模块内置 16 款带高光切面、柔光投影与微标签的多层矢量徽章（SVG），配合 GSAP 弹簧缓动
（`back.out(2.4)` 入场 + 呼吸微摆 + 利落收场），兼顾强网感与精致高级感。

用法：
  stickers.py --list
  stickers.py --preview <输出目录>
"""
from __future__ import annotations

import argparse
import html as _html
import json
from pathlib import Path

# 贴纸落角：**只能落在平台安全区里**。
# 竖屏满屏播放时右侧 13% 宽是点赞/评论/转发按钮列、顶部 10% 是导航、底部 17% 是文案条
# （`platform_safe.DEFAULTS`，项目可覆写）。贴纸宽 154/1080 = 14.3%W，旧值 `right:5.2%`
# 的右边缘压到 94.8%W——整块坐在按钮列上（top-right 那款大面积被按钮遮蔽），
# 而 `verify_film.platform_verdict` 正是把这一块判成「压住平台 UI」。
# 所以落角按安全区反算，不再手写百分比。
STK_W = 154 / 1080           # 贴纸宽度（画幅比例）：.stk 的 width 与 W 等比
STK_H = 156 / 1080           # 高度（图标 + 短签，1080 基准，同样按 W 缩放）
STK_PAD = 0.016              # 离安全区边留一点气（贴纸有投影，贴边看着像被裁）
_SLOTS: dict[str, tuple[str, str]] = {
    "top-right": ("right", "top"),
    "top-left": ("left", "top"),
    "mid-right": ("right", "mid"),
    "mid-left": ("left", "mid"),
    "bottom-right": ("right", "bottom"),
    "bottom-left": ("left", "bottom"),
}


def pos_css(pos: str, W: int = 1080, H: int = 1920, spec: dict | None = None) -> str:
    """一个贴纸落角的 CSS。可落区域 = 平台安全区（`platform_safe`，与判据同一份）。"""
    import platform_safe as _ps
    s = _ps._merge(_ps.DEFAULTS, spec)
    top_min = float(s["top_h"]) + STK_PAD
    right_min = float(s["right_w"]) + STK_PAD          # 桶：距右边至少这么宽
    bottom_max = 1.0 - float(s["bottom_h"]) - STK_PAD
    h = STK_H * (float(W) / float(H or W * 16 / 9))     # 高度占 H 的比例
    side, row = _SLOTS.get(str(pos or "top-right"), _SLOTS["top-right"])
    y = {0: 0.145, 1: 0.395, 2: 0.545}[("top", "mid", "bottom").index(row)]
    y = min(max(y, top_min), max(top_min, bottom_max - h))
    if side == "right":
        return (f"left:auto;right:{round(right_min * 100, 1)}%;"
                f"top:{round(y * 100, 1)}%;bottom:auto;")
    return (f"left:{round((float(s.get('left_w') or 0.052) + STK_PAD) * 100, 1)}%;right:auto;"
            f"top:{round(y * 100, 1)}%;bottom:auto;")


# 默认落角（无项目覆写时）：值全落在竖屏安全区里（顶部 10% / 右侧 13% / 底部 17%）
POS_STYLE: dict[str, str] = {k: pos_css(k) for k in _SLOTS}

STICKER_SFX: dict[str, str] = {
    "bang": "impact",
    "fire-hot": "whoosh",
    "mind-blown": "shimmer",
    "question-pop": "pop",
    "check-glow": "ding",
    "cross-warn": "impact",
    "money-fly": "money",
    "bulb-idea": "ding",
    "eye-alert": "pop",
    "thumb-up": "celebration",
    "vs-fire": "impact",
    "sparkle-star": "shimmer",
    "sweat-awkward": "pop",
    "heart-hit": "pop",
    "target-lock": "click",
    "megaphone": "pop",
}

# 16 款多层受光矢量贴纸（128x128 视口）+ 默认短签
STICKER_CATALOG: dict[str, dict] = {
    "bang": {
        "label": "划重点!",
        "accent": "#FFB800",
        "svg": """<svg viewBox="0 0 128 128">
          <defs><linearGradient id="g-bang" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#FFE600"/><stop offset="100%" stop-color="#FF5500"/></linearGradient></defs>
          <circle cx="64" cy="66" r="48" fill="rgba(20,16,14,.86)" stroke="rgba(255,230,0,.55)" stroke-width="3"/>
          <path d="M56 26 H72 L69 76 H59 Z" fill="url(#g-bang)"/>
          <circle cx="64" cy="94" r="8.5" fill="#FFE600"/>
          <path d="M22 34 L10 24 M106 34 L118 24 M64 10 L64 2" stroke="#FFE600" stroke-width="5" stroke-linecap="round"/>
        </svg>""",
    },
    "fire-hot": {
        "label": "高能!",
        "accent": "#FF5E1A",
        "svg": """<svg viewBox="0 0 128 128">
          <defs><linearGradient id="g-fire" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#FFE14D"/><stop offset="55%" stop-color="#FF6A00"/><stop offset="100%" stop-color="#D91E18"/></linearGradient></defs>
          <circle cx="64" cy="66" r="48" fill="rgba(22,14,12,.86)" stroke="rgba(255,106,0,.5)" stroke-width="3"/>
          <path d="M66 20 C82 38 96 54 92 78 C89 96 76 106 64 106 C48 106 36 93 36 76 C36 61 45 50 52 44 C50 54 56 58 60 54 C57 40 60 28 66 20 Z" fill="url(#g-fire)"/>
          <path d="M64 58 C72 68 75 76 72 86 C70 93 66 96 62 96 C56 96 52 90 53 82 C54 74 59 66 64 58 Z" fill="#FFF3B0"/>
        </svg>""",
    },
    "mind-blown": {
        "label": "颠覆认知",
        "accent": "#A855F7",
        "svg": """<svg viewBox="0 0 128 128">
          <defs><linearGradient id="g-bolt" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#FFF066"/><stop offset="100%" stop-color="#9333EA"/></linearGradient></defs>
          <circle cx="64" cy="66" r="48" fill="rgba(18,14,26,.88)" stroke="rgba(192,132,252,.55)" stroke-width="3"/>
          <polygon points="70,20 40,68 62,68 54,106 90,56 67,56" fill="url(#g-bolt)"/>
        </svg>""",
    },
    "question-pop": {
        "label": "真相是?",
        "accent": "#38BDF8",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(14,20,28,.88)" stroke="rgba(56,189,248,.55)" stroke-width="3"/>
          <path d="M48 48 C48 35 56 29 66 29 C77 29 84 36 84 46 C84 55 78 59 71 64 C66 67 64 70 64 76" fill="none" stroke="#7DD3FC" stroke-width="11" stroke-linecap="round"/>
          <circle cx="64" cy="94" r="7" fill="#FFE600"/>
        </svg>""",
    },
    "check-glow": {
        "label": "正解!",
        "accent": "#22C55E",
        "svg": """<svg viewBox="0 0 128 128">
          <defs><linearGradient id="g-ok" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#4ADE80"/><stop offset="100%" stop-color="#15803D"/></linearGradient></defs>
          <circle cx="64" cy="66" r="48" fill="url(#g-ok)" stroke="rgba(255,255,255,.45)" stroke-width="3"/>
          <path d="M42 67 L57 82 L88 49" fill="none" stroke="#FFFFFF" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>""",
    },
    "cross-warn": {
        "label": "避坑!",
        "accent": "#EF4444",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="#DC2626" stroke="rgba(255,228,230,.5)" stroke-width="3"/>
          <path d="M46 48 L82 84 M82 48 L46 84" stroke="#FFFFFF" stroke-width="12" stroke-linecap="round"/>
        </svg>""",
    },
    "money-fly": {
        "label": "算笔明账",
        "accent": "#FACC15",
        "svg": """<svg viewBox="0 0 128 128">
          <defs><linearGradient id="g-coin" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#FEF08A"/><stop offset="50%" stop-color="#EAB308"/><stop offset="100%" stop-color="#A16207"/></linearGradient></defs>
          <ellipse cx="64" cy="72" rx="44" ry="42" fill="#713F12"/>
          <ellipse cx="64" cy="64" rx="44" ry="42" fill="url(#g-coin)" stroke="#FEF9C3" stroke-width="2.5"/>
          <path d="M52 46 L64 60 L76 46 M48 63 H80 M48 73 H80 M64 60 V86" stroke="#713F12" stroke-width="6.5" stroke-linecap="round" fill="none"/>
        </svg>""",
    },
    "bulb-idea": {
        "label": "内行妙招",
        "accent": "#FDE047",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(24,20,12,.86)" stroke="rgba(253,224,71,.5)" stroke-width="3"/>
          <path d="M64 26 C46 26 36 39 36 54 C36 65 43 72 49 79 L49 86 H79 L79 79 C85 72 92 65 92 54 C92 39 82 26 64 26 Z" fill="#FDE047"/>
          <rect x="51" y="90" width="26" height="8" rx="4" fill="#CBD5E1"/>
          <path d="M56 58 L62 65 L73 50" fill="none" stroke="#854D0E" stroke-width="5.5" stroke-linecap="round"/>
        </svg>""",
    },
    "eye-alert": {
        "label": "盯准这处",
        "accent": "#F59E0B",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(18,18,22,.88)" stroke="rgba(245,158,11,.55)" stroke-width="3"/>
          <path d="M22 66 Q64 30 106 66 Q64 102 22 66 Z" fill="#F8FAFC" stroke="#F59E0B" stroke-width="4"/>
          <circle cx="64" cy="66" r="16" fill="#1E293B"/>
          <circle cx="64" cy="66" r="8" fill="#F59E0B"/>
          <circle cx="59" cy="60" r="4" fill="#FFFFFF"/>
        </svg>""",
    },
    "thumb-up": {
        "label": "靠谱!",
        "accent": "#FBBF24",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(24,19,14,.88)" stroke="rgba(251,191,36,.55)" stroke-width="3"/>
          <path d="M52 58 L63 32 C66 28 73 30 72 37 L70 52 H88 C93 52 96 57 94 62 L87 86 C85 90 81 92 76 92 H52 Z" fill="#FBBF24"/>
          <rect x="34" y="56" width="14" height="36" rx="4" fill="#F59E0B"/>
        </svg>""",
    },
    "vs-fire": {
        "label": "正面对决",
        "accent": "#FB7185",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(24,14,18,.9)" stroke="rgba(251,113,133,.6)" stroke-width="3"/>
          <path d="M32 46 L45 86 L58 46" fill="none" stroke="#FFE600" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M88 50 C80 44 68 48 70 57 C72 65 90 66 88 78 C86 87 72 88 65 82" fill="none" stroke="#FF4D6D" stroke-width="9" stroke-linecap="round"/>
        </svg>""",
    },
    "sparkle-star": {
        "label": "精致细节",
        "accent": "#FDE68A",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(20,18,16,.84)" stroke="rgba(253,230,138,.48)" stroke-width="2.5"/>
          <path d="M64 24 Q68 56 100 60 Q68 64 64 96 Q60 64 28 60 Q60 56 64 24 Z" fill="#FDE047"/>
          <path d="M92 26 Q94 38 106 40 Q94 42 92 54 Q90 42 78 40 Q90 38 92 26 Z" fill="#FFFBEB"/>
        </svg>""",
    },
    "sweat-awkward": {
        "label": "扎心了",
        "accent": "#38BDF8",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(15,23,42,.88)" stroke="rgba(56,189,248,.5)" stroke-width="3"/>
          <path d="M64 28 C78 52 86 66 86 78 C86 91 76 100 64 100 C52 100 42 91 42 78 C42 66 50 52 64 28 Z" fill="#38BDF8"/>
          <path d="M55 68 C53 76 56 84 62 87" fill="none" stroke="#E0F2FE" stroke-width="5" stroke-linecap="round"/>
        </svg>""",
    },
    "heart-hit": {
        "label": "良心推荐",
        "accent": "#F43F5E",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(28,14,18,.86)" stroke="rgba(244,63,94,.55)" stroke-width="3"/>
          <path d="M64 96 C64 96 30 74 30 50 C30 38 40 30 50 30 C57 30 62 34 64 39 C66 34 71 30 78 30 C88 30 98 38 98 50 C98 74 64 96 64 96 Z" fill="#F43F5E"/>
          <path d="M42 46 C42 41 46 38 50 38" fill="none" stroke="#FFE4E6" stroke-width="4.5" stroke-linecap="round"/>
        </svg>""",
    },
    "target-lock": {
        "label": "一针见血",
        "accent": "#FB923C",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="46" fill="rgba(20,16,14,.88)" stroke="#FB923C" stroke-width="4"/>
          <circle cx="64" cy="66" r="28" fill="none" stroke="#FED7AA" stroke-width="4" stroke-dasharray="10 6"/>
          <circle cx="64" cy="66" r="10" fill="#EA580C"/>
          <path d="M64 12 V26 M64 106 V120 M10 66 H24 M104 66 H118" stroke="#FB923C" stroke-width="4" stroke-linecap="round"/>
        </svg>""",
    },
    "megaphone": {
        "label": "听句劝!",
        "accent": "#FACC15",
        "svg": """<svg viewBox="0 0 128 128">
          <circle cx="64" cy="66" r="48" fill="rgba(22,18,14,.88)" stroke="rgba(250,204,21,.55)" stroke-width="3"/>
          <path d="M32 58 L78 38 V90 L32 70 Z" fill="#FACC15"/>
          <rect x="26" y="56" width="10" height="18" rx="3" fill="#FEF08A"/>
          <path d="M40 72 L46 92 H56 L50 74" fill="#CA8A04"/>
          <path d="M88 50 Q98 64 88 78 M96 42 Q110 64 96 86" fill="none" stroke="#FEF08A" stroke-width="4.5" stroke-linecap="round"/>
        </svg>""",
    },
}

def packaging_css(W: int = 1080, spec: dict | None = None) -> str:
    s = max(0.35, float(W) / 1080.0)
    px = lambda v: f"{max(1, round(v * s, 1))}px"
    # 悬念标题条也是一张「卡」：它同样不许钻到平台 UI 下面。
    # 旧值 top:5.8% 在竖屏满屏里正好是状态栏与顶部导航那一带（顶部 10%），
    # 于是「开篇钩子」在手机上看起来是被系统 UI 盖住的一条胶囊。
    import platform_safe as _ps
    _tb_top = max(0.058, float(_ps._merge(_ps.DEFAULTS, spec)["top_h"]) + STK_PAD)
    return f"""
/* 百万剪辑师网感包装层：显式覆盖 .clip 的 inset:0/height:100%，并按画幅等比缩放 */
.clip.title-bar {{ position:absolute; inset:auto; left:0; right:0; top:{round(_tb_top * 100, 1)}%; bottom:auto;
  width:100%; height:auto; z-index:26; display:flex; justify-content:center; align-items:flex-start;
  background:none; pointer-events:none; }}
.title-bar .tb-inner {{ display:inline-flex; align-items:center; gap:{px(14)}; max-width:90%;
  padding:{px(12)} {px(28)} {px(12)} {px(16)}; border-radius:999px;
  background:linear-gradient(180deg, rgba(28,24,20,.90) 0%, rgba(14,12,10,.94) 100%);
  border:{px(1.5)} solid rgba(255,244,224,.26);
  box-shadow:0 {px(16)} {px(38)} rgba(0,0,0,.48), inset 0 1px 0 rgba(255,255,255,.24);
  backdrop-filter:blur({px(12)}); white-space:nowrap; }}
.title-bar .tb-tag {{ font-family:"SC-Big",sans-serif; font-weight:900; font-size:{px(28)};
  color:#111111; background:var(--accent,#FFE600); padding:{px(6)} {px(16)};
  border-radius:999px; letter-spacing:.02em; line-height:1.15; }}
.title-bar .tb-main {{ font-family:"SC-Big",sans-serif; font-weight:900; font-size:{px(36)};
  color:#FFFFFF; letter-spacing:.01em; line-height:1.15;
  text-shadow:0 {px(2)} {px(10)} rgba(0,0,0,.78); }}

.clip.stk {{ position:absolute; inset:auto; z-index:25; display:flex; flex-direction:column;
  align-items:center; justify-content:flex-start; gap:{px(6)};
  width:{px(154)}; height:auto; pointer-events:none;
  filter:drop-shadow(0 {px(12)} {px(24)} rgba(0,0,0,.50)); }}
.clip.stk svg {{ width:{px(112)}; height:{px(112)}; overflow:visible; display:block; }}
.clip.stk .stk-pill {{ font-family:"SC-Big",sans-serif; font-weight:900; font-size:{px(25)};
  color:#12100E; background:var(--stk-accent,#FFE600); padding:{px(4)} {px(14)};
  border-radius:999px; letter-spacing:.02em; white-space:nowrap; line-height:1.15;
  box-shadow:0 {px(6)} {px(14)} rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.55); }}

.clip.vfx-flash {{ position:absolute; inset:0; width:100%; height:100%; z-index:24; pointer-events:none;
  background:radial-gradient(circle at 50% 45%, rgba(255,250,235,.72) 0%, rgba(255,225,160,.28) 55%, rgba(0,0,0,0) 100%);
  mix-blend-mode:screen; opacity:0; }}
.clip.vfx-vignette {{ position:absolute; inset:0; width:100%; height:100%; z-index:18; pointer-events:none;
  background:radial-gradient(78% 68% at 50% 46%, rgba(0,0,0,0) 54%, rgba(8,6,4,.42) 100%); }}
"""


PACKAGING_JS = """
// 悬念标题条：弹簧落定 + 收尾干净上收
document.querySelectorAll(".title-bar").forEach((tb) => {
  const t = parseFloat(tb.dataset.start || "0");
  const d = parseFloat(tb.dataset.duration || "4.2");
  tl.fromTo(tb,
    { y: -38, scale: 0.88, opacity: 0 },
    { y: 0, scale: 1, opacity: 1, duration: 0.36, ease: "back.out(1.7)", immediateRender: false }, t);
  if (d < totalDur - 0.3) {
    tl.to(tb, { y: -24, opacity: 0, duration: 0.22, ease: "power2.in" }, t + Math.max(0.4, d - 0.22));
  }
});
// 动态矢量贴纸：Q弹跳入 + 呼吸微摆 + 顺滑退场
document.querySelectorAll(".stk").forEach((s) => {
  const t = parseFloat(s.dataset.start || "0");
  const d = parseFloat(s.dataset.duration || "2.2");
  tl.fromTo(s,
    { scale: 0.25, rotation: -16, opacity: 0 },
    { scale: 1, rotation: 4, opacity: 1, duration: 0.34, ease: "back.out(2.4)", immediateRender: false }, t);
  tl.to(s, { rotation: -3, scale: 1.04, duration: 0.42, ease: "sine.inOut" }, t + 0.34);
  tl.to(s, { rotation: 2, scale: 1.0, duration: 0.42, ease: "sine.inOut" }, t + 0.76);
  tl.to(s, { scale: 0.65, opacity: 0, duration: 0.18, ease: "power1.in" }, t + Math.max(0.35, d - 0.18));
});
// 闪切光效
document.querySelectorAll(".vfx-flash").forEach((f) => {
  const t = parseFloat(f.dataset.start || "0");
  tl.fromTo(f, { opacity: 0.78 }, { opacity: 0, duration: 0.22, ease: "power2.out", immediateRender: false }, t);
});
"""


def _esc(s: str) -> str:
    return _html.escape(str(s), quote=True)


def collect_packaging(tl_doc: dict, segs: list[dict], total: float, W: int = 1080,
                      H: int = 0, spec: dict | None = None) -> dict:
    """从 timeline 顶层和元素提取标题条、贴纸、镜头特效与配套音效事件。

    `H` 与 `spec` 只影响贴纸落角（平台安全区），缺省按 9:16 与竖屏默认安全区算。
    """
    spec_ = spec
    html_nodes: list[str] = []
    vfx_js_lines: list[str] = [f"const totalDur = {total:.3f};"]
    sfx_events: list[dict] = []

    # 1) 顶部标题条（默认在开篇黄金前 4.5s 亮出，或 mode=="full" 全程常驻）
    tb = tl_doc.get("title_bar")
    if isinstance(tb, dict) and (tb.get("main") or tb.get("text")):
        top_txt = str(tb.get("top") or "").strip()
        main_txt = str(tb.get("main") or tb.get("text") or "").strip()
        t0 = float(tb.get("at") or 0.08)
        mode = str(tb.get("mode") or "hook")
        dur = round(max(1.5, total - t0 if mode == "full" else min(float(tb.get("dur") or 4.5), total - t0)), 3)
        if dur > 0.4:
            tag_html = f'<span class="tb-tag">{_esc(top_txt)}</span>' if top_txt else ""
            html_nodes.append(
                f'    <div id="title-bar" class="clip title-bar" data-start="{t0:.3f}" data-duration="{dur:.3f}">'
                f'<div class="tb-inner">{tag_html}<span class="tb-main">{_esc(main_txt)}</span></div></div>'
            )
            sfx_events.append({"t": round(t0, 3), "name": "title", "gain_db": -0.5, "for": "title-bar"})

    # 2) 贴纸：支持顶层 stickers[] 与元素级 el["sticker"]
    raw_stickers: list[dict] = []
    for s in (tl_doc.get("stickers") or []):
        if isinstance(s, dict) and s.get("id"):
            raw_stickers.append(dict(s))
    for el in segs:
        st = el.get("sticker")
        if isinstance(st, str) and st.strip():
            st = {"id": st.strip()}
        if isinstance(st, dict) and st.get("id") and el.get("_start") is not None:
            el_d = float(el.get("_dur_q") or el.get("dur") or (float(el.get("out", 0)) - float(el.get("in", 0))) or 2.4)
            at = round(float(el["_start"]) + float(st.get("offset") or 0.22), 3)
            dur = round(min(float(st.get("dur") or 2.2), max(1.0, el_d - 0.25)), 3)
            raw_stickers.append({**st, "at": at, "dur": dur})

    for idx, st in enumerate(raw_stickers, 1):
        sid = str(st.get("id") or "bang").strip()
        spec = STICKER_CATALOG.get(sid) or STICKER_CATALOG["bang"]
        at = round(max(0.0, float(st.get("at") or 0.2)), 3)
        if at >= total - 0.3:
            continue
        dur = round(min(float(st.get("dur") or 2.2), total - at), 3)
        pos_css_ = pos_css(str(st.get("pos") or "top-right"), W, H, spec_)
        label = st.get("label") if st.get("label") is not None else spec["label"]
        pill = f'<span class="stk-pill">{_esc(label)}</span>' if str(label).strip() else ""
        html_nodes.append(
            f'    <div id="stk-{idx:02d}" class="clip stk" data-start="{at:.3f}" data-duration="{dur:.3f}" '
            f'style="{pos_css_}--stk-accent:{spec["accent"]};">{spec["svg"]}{pill}</div>'
        )
        sfx_name = STICKER_SFX.get(sid, "pop")
        sfx_events.append({"t": at, "name": sfx_name, "gain_db": 0.0, "for": f"stk-{sid}"})

    # 3) 元素级镜头特效（vfx: punch_zoom / slow_push / flash_cut / spotlight）
    has_vignette = bool(tl_doc.get("vignette"))
    for el in segs:
        vfx = str(el.get("vfx") or "").strip()
        if not vfx or el.get("_start") is None:
            continue
        t0 = float(el["_start"])
        el_d = float(el.get("_dur_q") or el.get("dur") or (float(el.get("out", 0)) - float(el.get("in", 0))) or 2.5)
        n = el.get("n")
        target_sel = f"#v{n}, #s{n}, #gen-{n}"
        if vfx == "punch_zoom":
            vfx_js_lines.append(
                f'gsap.utils.toArray("{target_sel}").forEach(( node ) => {{ '
                f'tl.fromTo(node, {{ scale: 1.0 }}, {{ scale: 1.08, duration: 0.24, ease: "expo.out", '
                f'transformOrigin: "50% 46%", immediateRender: false }}, {t0:.3f}); }});'
            )
            html_nodes.append(
                f'    <div class="clip vfx-flash" data-start="{t0:.3f}" data-duration="0.24"></div>'
            )
            sfx_events.append({"t": round(t0, 3), "name": "punch", "gain_db": 0.5, "for": f"vfx-punch-{n}"})
        elif vfx == "slow_push":
            vfx_js_lines.append(
                f'gsap.utils.toArray("{target_sel}").forEach(( node ) => {{ '
                f'tl.fromTo(node, {{ scale: 1.0 }}, {{ scale: 1.06, duration: {max(0.8, el_d):.3f}, ease: "none", '
                f'transformOrigin: "50% 48%", immediateRender: false }}, {t0:.3f}); }});'
            )
        elif vfx == "flash_cut":
            html_nodes.append(
                f'    <div class="clip vfx-flash" data-start="{t0:.3f}" data-duration="0.24"></div>'
            )
            sfx_events.append({"t": round(t0, 3), "name": "flash", "gain_db": 0.0, "for": f"vfx-flash-{n}"})
        elif vfx == "spotlight":
            has_vignette = True

    if has_vignette:
        html_nodes.insert(0, f'    <div class="clip vfx-vignette" data-start="0" data-duration="{total:.3f}"></div>')

    return {
        "css": packaging_css(W, spec_),
        "html": "\n".join(html_nodes),
        "js": "\n".join(vfx_js_lines) + "\n" + PACKAGING_JS,
        "sfx": sfx_events,
    }


def licensing_declaration() -> dict:
    """贴纸授权声明——按代码事实说，不按「应该是原创」猜。

    事实（可现场复核，不是自述）：
      1. 每一款贴纸的图形是 `STICKER_CATALOG[...]["svg"]` 里的一段内联 SVG 字符串，
         直接写在 `scripts/stickers.py` 源码里，仓里没有对应的图片/字体文件；
      2. 没有任何 `<image>` / `href` 外链 / `url(http…)` 位图嵌入——`external_refs` 为空
         才能说「纯代码自绘」；一旦有人往后塞外部素材，这里会当场标红。
    """
    import re
    ids = sorted(STICKER_CATALOG)
    items, refs = [], []
    for sid, v in sorted(STICKER_CATALOG.items()):
        svg = str(v.get("svg") or "")
        hits = sorted(set(re.findall(r"<image\b|href\s*=|url\(\s*['\"]?https?:|data:image", svg)))
        if hits:
            refs.append({"id": sid, "hits": hits})
        items.append({"id": sid, "label": v.get("label"), "accent": v.get("accent"),
                      "origin": "code_drawn" if not hits else "external_asset",
                      "needs_review": bool(hits)})
    return {
        "kind": "code_drawn_svg",
        "author": "本仓库（CutSkills · scripts/stickers.py）",
        "origin": "代码内自绘：16 款多层矢量徽章直接写成 SVG 字符串，无外部图形素材",
        "count": len(ids),
        "ids": ids,
        "items": items,
        "external_refs": refs,
        "license": "本仓库原创素材，随仓分发；代码与文档许可见 README「📄 许可」一节",
        "third_party_risk": False if not refs else True,
        "animation_engine": "gsap（贴纸入场/呼吸/退场动画由 assets/gsap.min.js 驱动，非图形素材）",
        "needs_review": [] if not refs else [r["id"] for r in refs],
        "note": ("自绘 SVG = 无第三方素材侵权风险；但如果以后往 svg 字符串里嵌外部图片/字体，"
                 "licensing.py 会把它从「自绘」改成「外部素材·需核验」"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="列出或导出内置 16 款矢量动态表情贴纸")
    ap.add_argument("--list", action="store_true", help="打印全部 16 款贴纸 ID 与默认短签")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出目录")
    ap.add_argument("--preview", default=None, help="生成一页所有贴纸的 HTML 预览卡目录")
    ap.add_argument("--licence", action="store_true", help="打印贴纸授权声明（代码自绘 or 外部素材）")
    a = ap.parse_args()

    if a.licence:
        print(json.dumps(licensing_declaration(), ensure_ascii=False, indent=2))
        return 0

    if a.json:
        print(json.dumps({k: {"label": v["label"], "accent": v["accent"], "sfx": STICKER_SFX.get(k)}
                          for k, v in STICKER_CATALOG.items()}, ensure_ascii=False, indent=2))
        return 0

    if a.preview:
        out_d = Path(a.preview).expanduser().resolve()
        out_d.mkdir(parents=True, exist_ok=True)
        cards = "".join(
            f'<div style="background:#1c1917;border:1px solid #333;border-radius:20px;padding:20px;'
            f'display:flex;flex-direction:column;align-items:center;gap:10px;">'
            f'<div style="width:110px;height:110px">{v["svg"]}</div>'
            f'<span style="background:{v["accent"]};color:#111;font-weight:900;padding:4px 14px;border-radius:99px">{v["label"]}</span>'
            f'<code style="color:#a8a29e;font-size:13px">{k}</code></div>'
            for k, v in STICKER_CATALOG.items()
        )
        html_p = out_d / "stickers-preview.html"
        html_p.write_text(
            f'<!doctype html><meta charset="utf-8"><body style="background:#0c0a09;color:#fff;'
            f'font-family:system-ui;padding:36px"><h2 style="margin-bottom:24px">CutSkills · 16 款内置矢量情绪贴纸</h2>'
            f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:18px;max-width:960px">{cards}</div></body>',
            encoding="utf-8",
        )
        print(f"→ {html_p}")
        return 0

    for k, v in STICKER_CATALOG.items():
        print(f"  {k:<16} 標籤：{v['label']:<8} 配色：{v['accent']}  音效：{STICKER_SFX.get(k, 'pop')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
