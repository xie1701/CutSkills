#!/usr/bin/env python3
"""CutSkills · 包装包裁判：三套「衣服」的合同测试。

包装包（`packs/*.yaml`）是三个正交维度里的第二个。它只许决定**穿什么**，
不许决定**骨架**。这条约束不靠自觉，靠两个检查：

  ① 结构合法 —— 必填 token 齐、字体在盘上、`grade` 在 GRADE 表里、
     `subtitle_system.id` 在枚举里、`fx` 是认得的原语。
  ② 不含时间字段 —— 递归扫，出现 `tempo / cut / dur / beat / plan / 时间` 一类键
     直接判红。包装包一旦能改时间，「换 look 不动切点」就是句空话。

再加一条**渲染级**证据（`--project` 给了才跑）：把同一份时间轴分别按每个包
渲一次 HTML（`--no-render`，不跑浏览器），从产物里把视频轨每格的
`data-start / data-duration` 抽出来，逐包比对——序列必须**完全一致**。

用法：
  check_packs.py                      # 只查结构
  check_packs.py --project demo/pack-micro --library <素材库>   # 连切点一起查
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from card_layout import MG_KINDS  # noqa: E402

SUBTITLE_SYSTEMS = {"single-line", "bilingual", "none"}
# MG 库：包里声明「这套衣服允许用哪几种动效卡」。不认识的 kind 直接判红——
# 声明了却渲不出来，是最难查的一类：方案卡上写着有卡，画面上什么都没有。
KNOWN_MG = MG_KINDS | {"keyword-big"}
KNOWN_FX = {
    "hard_cut", "nudge_in", "cover_fg", "page_turn", "note_drop", "wipe",
    "type_on", "count_up", "draw_line", "blur_in", "shake", "none",
    # 2026-11-11 实现：sweep / particles 是 gen_scene 的绘制原语，
    # morph_carry 是 gen 格的「不重新入场」（承接不闪）。
    "sweep", "particles", "morph_carry",
}
# 方案里点了名、代码还没做的原语。声明可以先行，但必须显形——不能像以前那样
# 「fx 里写了什么」和「片子会做什么」是两件互不相干的事。
PLANNED_FX: set[str] = set()
# 这些键名一出现就说明包装包伸到骨架里去了
TIMING_KEYS = {
    "tempo", "tempos", "band", "target_median_s", "cv_min", "cut", "cuts",
    "cut_s", "dur", "duration", "duration_s", "beat", "beats", "shot_logic",
    "plan", "plans", "spacing", "hold_s", "start", "end",
}
# 例外：帧率是画布属性，不是切点。画布 30fps 和「第几秒切」是两回事。
TIMING_EXEMPT = {"canvas.fps"}
FONT_DIR = Path.home() / "Library" / "Fonts"
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# ---------------------------------------------------------------- 语域（surface）
# 原则 B6：包装包的色板必须归包管，渲染代码里零硬编码色值。
# 背景：MG 卡底板曾经写死在 render_hf.py 的 CSS 里（冷黑蓝弹窗底 + 90% 全屏黑遮罩），
# 换包换不掉；拿它去包广式早茶，虾饺被压成生冷灰暗色（2026-11-13 T-018 事故）。
# 这里两道闸：① 每个包必须把 surface 交代清楚；② 渲染器源码不许再写回那套冷黑蓝。
SURFACE_REQUIRED = ("plate", "scrim", "border", "blur_ness", "edge", "light")
# T-042 补的两个字段：
#   edge   = [顶光, 底暗] 面板内侧 1px 倒角——「厚度」由这两条线读出来
#   light  = 底板（.mgplate）那一层纵向光线——「受光面」由这层读出来
# 两者都不写也能渲（渲器有单值回退），但四个包必须交代：一套面板光不能只有某个包有。
# 可选键：词条卡要更实的底/描边（字多面积小）、卡面字色（深色纸底包不能跟 palette.ink）、
# 面板的「浮起」（阴影）与词条卡的呼吸（内边距 / 行间）——不写就等于没这一层
SURFACE_OPTIONAL = ("plate_term", "border_term", "ink", "shadow", "term_pad", "term_gap")
BLUR_MAX = 40          # 超过这个数就不叫「柔化」了，叫把底片糊成一片

RENDERER = ROOT / "scripts" / "render_hf.py"
# 这几个值就是「冷黑蓝弹窗底」家族：一旦出现在渲染器里，说明有人又把语域写回代码了。
# 注意：判据盯的是**渲染器**，包里的 yaml 里出现它们是合法的（P2/P3 就该承接它们）。
BANNED_IN_RENDERER = (
    (re.compile(r"rgba\(\s*10\s*,\s*13\s*,\s*19"), "冷黑蓝卡底 #0A0D13",
     "卡面语域必须走 tokens.surface"),
    (re.compile(r"rgba\(\s*12\s*,\s*15\s*,\s*21"), "冷黑蓝卡底 #0C0F15",
     "卡面语域必须走 tokens.surface"),
    # 非等比挤压字骨（scaleX）是广电低级事故：字的骨骼被压扁，字号账也全废
    (re.compile(r"\bscaleX\s*\("), "非等比字重 scaleX()",
     "紧凑排版只能选原生窄体字库，原则 B10"),
)
# 反证：变量管道本身不许被拆。包声明了 surface，渲染器不读它，就会变成
# 「yaml 写着暖食、画面上还是冷黑」——比没抽更糟（声明与行为分家）。
REQUIRED_IN_RENDERER = ("var(--surface-plate", "var(--surface-scrim")


def check_surface(pack: dict) -> tuple[list[str], list[str]]:
    """包的卡面语域：必须完整，且 blur 不能把底片糊成一片。"""
    err, warn = [], []
    tok = pack.get("tokens") or {}
    surf = tok.get("surface")
    if not isinstance(surf, dict) or not surf:
        return ([f"tokens.surface 缺失（卡面底板/遮罩/描边/字色要有自己的语域，"
                 f"必填 {list(SURFACE_REQUIRED)}）"], warn)
    for k in SURFACE_REQUIRED:
        if k not in surf:
            err.append(f"tokens.surface 缺 {k}")
    # 写了的键不许是空串：空串会被渲染器当「没写」退成默认色，静默跑偏
    for k in ("plate", "border", "scrim") + SURFACE_OPTIONAL:
        if k in surf and not str(surf[k] or "").strip():
            err.append(f"tokens.surface.{k} 是空的")
    # 遮罩一律写成一段完整的 CSS 背景（radial/linear-gradient）——
    # 拆成「颜色 + 浓度」两个 key 就设不下「只局部柔化、边缘归零」这种话。
    scr = str(surf.get("scrim") or "")
    if scr and "gradient(" not in scr:
        warn.append(f"tokens.surface.scrim 不是 gradient：{scr[:40]}（平板遮罩就是全屏压暗）")
    # plate 从 T-042 起可以是纵向光线渐变。单色仍然收（老包兼容，渲器会包成同色渐变），
    # 但要提一句：一块平涂色就是「贴纸」的病根。
    pl = str(surf.get("plate") or "")
    if pl and "gradient(" not in pl:
        warn.append(f"tokens.surface.plate 是单色：{pl[:40]}"
                    f"（平涂底板没有受光面，读起来是贴纸——T-042 建议写成纵向光线渐变）")
    # light：底板光线层，要么不写，要么是一条 gradient（裸颜色不能当背景层叠上去）
    lg = surf.get("light")
    if lg is not None:
        if not str(lg).strip():
            err.append("tokens.surface.light 是空的")
        elif "gradient(" not in str(lg):
            err.append(f"tokens.surface.light 不是 gradient：{str(lg)[:40]}"
                       f"（它要叠在 scrim 之上，只能是 <image>）")
    # edge：[顶, 底] 两条 1px 内侧倒角，颜色必须是颜色
    eg = surf.get("edge")
    if eg is not None:
        ok_eg = (isinstance(eg, (list, tuple)) and len(eg) == 2
                 and all(re.match(r"^(#[0-9A-Fa-f]{6}|rgba?\([^)]*\)|hsla?\([^)]*\))$",
                                  str(x).strip()) for x in eg))
        if not ok_eg:
            err.append(f"tokens.surface.edge 要写 [顶光色, 底暗色] 两个颜色：{eg}")
    bl = surf.get("blur_ness", 0)
    try:
        bl = float(bl)
        if bl < 0:
            err.append(f"tokens.surface.blur_ness={bl} 不能是负数")
        elif bl > BLUR_MAX:
            err.append(f"tokens.surface.blur_ness={bl:g} 超过 {BLUR_MAX}（底片会被糊成一片）")
    except (TypeError, ValueError):
        err.append(f"tokens.surface.blur_ness={bl!r} 不是数字")
    ink = surf.get("ink")
    if ink is not None and not HEX_RE.match(str(ink)):
        err.append(f"tokens.surface.ink 不是 #RRGGBB：{ink}")
    # shadow：要么 none，要么是一串能生效的 CSS 阴影（含 px 半径）；「0 0 0 #000」这种硬边也算写了
    sh = surf.get("shadow")
    if sh is not None and str(sh).strip().lower() != "none" and "px" not in str(sh):
        err.append(f"tokens.surface.shadow 不像 CSS 阴影：{sh}")
    # term_pad：[上下, 左右] 两个数字（像素）；只写一个数会让上下左右挤成一圈字
    tp = surf.get("term_pad")
    if tp is not None:
        ok_tp = (isinstance(tp, (list, tuple)) and len(tp) == 2
                 and all(isinstance(v, (int, float)) and v >= 0 for v in tp))
        if not ok_tp:
            err.append(f"tokens.surface.term_pad 要写 [上下, 左右] 两个非负数：{tp}")
    tg = surf.get("term_gap")
    if tg is not None and not (isinstance(tg, (int, float)) and tg >= 0):
        err.append(f"tokens.surface.term_gap 要是非负数（px）：{tg}")
    return err, warn


def check_renderer_source() -> list[str]:
    """反回潮：渲器源码里不许再出现硬编码语域 / 非等比字重。

    这是全包共用的判据（不跟某一包绑定），所以从 check_schema 里拆出来单独跑：
    一个人新开一个包，也不会漏掉这道闸。
    """
    if not RENDERER.exists():
        return [f"找不到渲染器：{RENDERER}"]
    src = RENDERER.read_text()
    err = [f"scripts/render_hf.py 里又写死了{why}（{hint}）"
           for rx, why, hint in BANNED_IN_RENDERER if rx.search(src)]
    for needle in REQUIRED_IN_RENDERER:
        if needle not in src:
            err.append(f"scripts/render_hf.py 不再读 {needle}（包的 surface 白写了）")
    return err


def walk_keys(obj, path="") -> list[tuple[str, str]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append((f"{path}.{k}" if path else str(k), str(k)))
            out += walk_keys(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += walk_keys(v, f"{path}[{i}]")
    return out


def check_schema(pid: str, pack: dict, grades: set[str]) -> tuple[list[str], list[str]]:
    err, warn = [], []

    def need(cond, msg):
        if not cond:
            err.append(msg)

    need(pack.get("id") in (None, pid) or str(pack.get("id")) == pid,
         f"id 写的是 {pack.get('id')}，但文件名是 {pid}")

    tok = pack.get("tokens") or {}
    need(bool(tok), "没有 tokens（palette / type / radius / stroke / shadow）")
    pal = tok.get("palette") or {}
    for k in ("ground", "ink", "accent"):
        need(k in pal, f"tokens.palette 缺 {k}")
        if k in pal:
            need(bool(HEX_RE.match(str(pal[k]))), f"tokens.palette.{k} 不是 #RRGGBB：{pal[k]}")
    typ = tok.get("type") or {}
    for k in ("display", "body"):
        need(k in typ, f"tokens.type 缺 {k}")
        if k in typ:
            f = FONT_DIR / f"{typ[k]}.ttf"
            need(f.exists(), f"字体不在盘上：{f}")
    need("radius" in tok, "tokens 缺 radius")
    need("stroke" in tok, "tokens 缺 stroke")
    # 卡面语域（底板/遮罩/描边/字色）：单独一个函数，因为它是 T-018 的事故点，
    # 以后还得能单独跑（只看这一块时不用读整块 schema 报错）。
    _se, _sw = check_surface(pack)
    err += _se
    warn += _sw

    sy = pack.get("subtitle_system") or {}
    sid = sy.get("id") if isinstance(sy, dict) else sy
    need(sid in SUBTITLE_SYSTEMS, f"subtitle_system 不认识：{sid}（可选 {sorted(SUBTITLE_SYSTEMS)}）")

    fx = pack.get("fx") or []
    bad_fx = [x for x in fx if x not in KNOWN_FX | PLANNED_FX]
    need(not bad_fx, f"fx 里有不认识的原语：{bad_fx}")
    planned = [x for x in fx if x in PLANNED_FX]
    if planned:
        warn.append(f"fx 里这几个还没做：{planned}（声明先行，但那几样效果现在不会发生）")

    mg = pack.get("mg") or {}
    lib = [str(x) for x in (mg.get("library") or [])]
    bad_mg = [x for x in lib if x not in KNOWN_MG]
    need(not bad_mg, f"mg.library 里有渲染器画不出来的卡型：{bad_mg}（可选 {sorted(KNOWN_MG)}）")
    if not lib:
        warn.append("没写 mg.library：这条片只能靠大字，等于没做 MG")
    if lib and "keyword-big" not in lib:
        warn.append("mg.library 里没有 keyword-big——一句话结论没有卡可用")

    g = pack.get("grade")
    if g:
        need(g in grades, f"grade={g} 不在 render_hf.GRADE 里（可选 {sorted(grades)}）")

    # ② 时间字段
    hits = [(p, k) for p, k in walk_keys(pack) if k in TIMING_KEYS and p not in TIMING_EXEMPT]
    need(not hits, "包装包里混进了时间字段（包装不许动切点）：" + "、".join(f"{p}" for p, _ in hits[:5]))

    # 「大字撑 70% 宽、单排」是 Jerry 2026-11-09 定的硬规则。包里写 0.82 就是违规——
    # 判据放在这里，是因为这种数字太容易被「这套风格想更满一点」这种理由悄悄改掉。
    fw = (pack.get("emphasis") or {}).get("fit_width")
    if fw is not None:
        need(float(fw) <= 0.72, f"emphasis.fit_width={fw} 超过 0.72（大字单排撑 70% 是硬规则）")

    # 提示级：这套包靠什么活
    if sid == "none" and not (pack.get("emphasis") or {}).get("big_keyword_px"):
        warn.append("字幕系统是 none，但没给大字尺寸——等于什么字都没有")
    if sid == "bilingual":
        warn.append("bilingual 要真有英文稿：项目里得放 subs-en.json（键=字幕序号）；"
                    "没有它渲出来仍是单行，渲染时会打一行提示")
    if not pack.get("look_notes"):
        warn.append("没写 look_notes：后面的人不知道这套包为什么这么配色")
    return err, warn


# 只认 <div> 是不够的：实拍格是 `<video class="clip" ...>`，代码画面是
# `<video class="clip">`，只有整屏 gen 卡才是 `<div class="clip gen">`。
# 第一版正则只匹配 div，于是在**真项目上恒为 0 格**——0 == 0，「切点一致 ✅」，
# 一个永远不会失败的自证。判据必须用见过的两种错来校准，不是用「跑通了」校准。
TAG_RE = re.compile(r'<(\w+)([^>]*)>', re.S)
CLS_RE = re.compile(r'class="([^"]*)"')
START_RE = re.compile(r'data-start="([\d.]+)"')
DUR_RE = re.compile(r'data-duration="([\d.]+)"')
SKIP_CLASS = {"cap", "kw", "idcard"}      # 字幕层 / 大字层：本来就该随包装变


def cut_list(html: str) -> list[tuple[float, float]]:
    """**视频轨**每格的 (start, dur)。字幕、大字、抠像层不进这个列表。

    判据：class 里有 clip、且不是字幕/大字/身份卡、且不在 overlay 轨（track-index 1）。
    """
    out = []
    for _tag, attrs in TAG_RE.findall(html):
        m = CLS_RE.search(attrs)
        if not m:
            continue
        toks = set(m.group(1).split())
        if "clip" not in toks or toks & SKIP_CLASS:
            continue
        if 'data-track-index="1"' in attrs:      # 抠像层，不是时间轴上的格
            continue
        s, d = START_RE.search(attrs), DUR_RE.search(attrs)
        if not (s and d):
            continue
        out.append((round(float(s.group(1)), 3), round(float(d.group(1)), 3)))
    # 按起点排序：文档顺序里代码画面的 <video> 排在实拍格后面，直接比会误报「不一致」。
    return sorted(out)


def render_cuts(project: Path, pack: str, library: str | None) -> list[tuple[float, float]] | None:
    cmd = [sys.executable, str(ROOT / "scripts" / "render_hf.py"),
           "--project", str(project), "--style", pack, "--no-render", "--force"]
    if library:
        cmd += ["--library", library]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {pack} 渲 HTML 失败：\n" + "\n".join((r.stdout + r.stderr).splitlines()[-6:]))
        return None
    idx = project / "hf" / "index.html"
    if not idx.exists():
        print(f"  {pack} 没找到 {idx}")
        return None
    return cut_list(idx.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs", nargs="*", default=None)
    ap.add_argument("--project", default=None, help="给了就顺手验「换 look 不动切点」")
    ap.add_argument("--library", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(ROOT / "scripts"))
    from render_hf import GRADE  # noqa: E402

    files = ([ROOT / "packs" / f"{p}.yaml" for p in a.packs] if a.packs
             else sorted((ROOT / "packs").glob("*.yaml")))
    report, ok = {}, True
    # ① 反回潮：渲器源码不许再自带语域（全局一条，不跟包绑）
    _renderer_err = check_renderer_source()
    if _renderer_err:
        ok = False
        print("✗ 渲器源码（全包共用）")
        for e in _renderer_err:
            print(f"    ✗ {e}")
    else:
        print("✅ 渲器源码：卡面语域全走 tokens.surface（无冷黑蓝硬编码 / 无 scaleX）")
    report["_renderer"] = {"error": _renderer_err}
    for f in files:
        if not f.exists():
            print(f"没有 {f}")
            ok = False
            continue
        pid = f.stem
        pack = yaml.safe_load(f.read_text()) or {}
        err, warn = check_schema(pid, pack, set(GRADE))
        ok &= not err
        report[pid] = {"error": err, "warning": warn,
                       "subtitle_system": (pack.get("subtitle_system") or {}).get("id")
                       if isinstance(pack.get("subtitle_system"), dict) else pack.get("subtitle_system"),
                       "grade": pack.get("grade"), "fx": pack.get("fx") or []}
        mark = "✅" if not err else "✗"
        print(f"{mark} {pid}  字幕体系 {report[pid]['subtitle_system']} · "
              f"调色 {pack.get('grade') or '不调'} · fx {'/'.join(pack.get('fx') or []) or '无'}")
        for e in err:
            print(f"    ✗ {e}")
        for w in warn:
            print(f"    ! {w}")

    # ② 渲染级：同一份时间轴，逐包比切点
    # 副作用要收拾：这里靠 `render_hf --no-render` 出 HTML，会覆盖那个项目的
    # hf/index.html 与 meta.json（成片还是旧的）。跑完必须还原，否则下一个环节
    # （验收按 meta.json 认包装）会认错包 —— 本轮踩过一次。
    _saved = {}
    if a.project:
        _proj = Path(a.project).expanduser().resolve()
        for _rel in ("hf/index.html", "hf/meta.json"):
            _f = _proj / _rel
            if _f.exists():
                _saved[_rel] = _f.read_bytes()
    try:
        ok = _cut_checks(a, report, ok)
    finally:
        for _rel, _blob in _saved.items():
            (_proj / _rel).write_bytes(_blob)
        if _saved:
            print("  已还原项目的 hf/index.html 与 meta.json")
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n结论：{'过' if ok else '没过'}")
    return 0 if ok else 1


def _cut_checks(a, report: dict, ok: bool) -> bool:
    if a.project:
        proj = Path(a.project).expanduser().resolve()
        base = None
        print(f"\n换 look 不动切点（{proj.name}）：")
        for pid in [k for k in report if not k.startswith("_")]:   # `_renderer` 不是包
            cuts = render_cuts(proj, pid, a.library)
            if cuts is None:
                ok = False
                continue
            if base is None:
                base = cuts
                print(f"  基准 {pid}：{len(cuts)} 格 " + " ".join(f"{s:.2f}+{d:.2f}" for s, d in cuts[:4])
                      + (" …" if len(cuts) > 4 else ""))
                continue
            same = cuts == base
            ok &= same
            print(f"  {'✅' if same else '✗'} {pid}：{len(cuts)} 格"
                  + ("" if same else f"，和基准不一样：{cuts[:4]}"))
            report[pid]["cuts_match"] = same

    return ok


if __name__ == "__main__":
    raise SystemExit(main())
