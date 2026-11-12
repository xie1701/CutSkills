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

SUBTITLE_SYSTEMS = {"single-line", "bilingual", "none"}
# MG 库：包里声明「这套衣服允许用哪几种动效卡」。不认识的 kind 直接判红——
# 声明了却渲不出来，是最难查的一类：方案卡上写着有卡，画面上什么都没有。
KNOWN_MG = {"keyword-big", "stat", "steps", "versus", "term"}
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
        for pid in report:
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
