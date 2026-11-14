#!/usr/bin/env python3
"""字幕模板（`styles/subtitles.yaml`）回归：四套样式、可商用字体、优先级、向后兼容。

守的是五件事（BRIEF 第 5 步「字幕：模板 3–5 套 + 可商用字体」）：
  ① 四套模板字段齐全，而且**真不一样**：至少三处关键字段不同（不是同一套换个名字）；
  ② 每套的字体都在 `styles/fonts.yaml` 里登记过，`license` 不是 `unverified`；
     负样本：把某套字体换成一个没授权的字 → 必须报错（不许带着没授权的字出片）；
  ③ 优先级：项目显式选的模板 > 包装包自带的 subtitle 参数 > 默认模板。
     **判据真的跟着换**：同一份 subs.json，选了模板就按模板的字号/行宽/强调色判，
     反过来不选就按包装包判——两个口径一个绿一个红，证明不是「读了没用」；
  ④ 不选模板时与旧版一致（向后兼容）：口径默认值等于共用常量、`subs.build()` 不传
     参数与显式传 10 单位结果一致、`check_subs` 的行宽上限读数仍是 10 单位；
  ⑤ 模板 id 不存在 / YAML 坏了 / 字体缺失 → 可读报错（人话 + 退出码），不抛 traceback。

只读仓内文件 + 临时目录；不起渲染、不碰真实素材库，跑一次 2 秒内：

    python3 scripts/test_subtitle_templates.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "vendor"))

import subs  # noqa: E402  模板加载/校验/优先级都只有这一份

fails: list[str] = []
PY = sys.executable
# 子进程要能 import yaml（check_subs 里 `import yaml` 走 vendor）
CHILD_ENV = {**os.environ,
             "PYTHONPATH": os.pathsep.join([str(SCRIPTS), str(SCRIPTS / "vendor"),
                                            os.environ.get("PYTHONPATH") or ""])}


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def eye(name: str) -> None:
    print(f"\n{name}")


# ---------------------------------------------------------------- 夹具

def mk_project(root: Path, cues: list[dict], brief: dict) -> Path:
    proj = root
    (proj / "out").mkdir(parents=True, exist_ok=True)
    (proj / "out" / "subs.json").write_text(
        json.dumps({"cues": cues, "colorize": True}, ensure_ascii=False), encoding="utf-8")
    (proj / "brief.json").write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    return proj


def make_cues(units: float, n: int = 3, text: str = "我们打个样板看看") -> list[dict]:
    """n 条字幕，每条 `units` 个展示单位、1.2s，首尾相接（半开帧窗）。"""
    step = 1.4
    return [{"text": text, "start": round(i * step, 3), "end": round(i * step + 1.2, 3),
             "html": f'<span class="em">{text[0]}</span>{text[1:]}',
             "chars": len(text), "units": float(units),
             "sf": int(round(i * step * 30)), "ef": int(round((i * step + 1.2) * 30)),
             "el": 1, "break_after": "clause"} for i in range(n)]


def run_check(proj: Path) -> tuple[int, dict | None, str]:
    r = subprocess.run([PY, str(SCRIPTS / "check_subs.py"), "--project", str(proj), "--json"],
                       capture_output=True, text=True, env=CHILD_ENV, timeout=120)
    try:
        return r.returncode, json.loads(r.stdout), (r.stderr or "")
    except json.JSONDecodeError:
        return r.returncode, None, (r.stderr or r.stdout)


# ---------------------------------------------------------------- ① 四套模板

def case_fields() -> None:
    eye("① 四套模板：字段齐全，而且互不相同")
    doc = subs.load_subtitle_templates()
    tpls = doc["templates"]
    must("模板数是 3–5 套（BRIEF 要求）", 3 <= len(tpls) <= 5, str(len(tpls)))
    for tid, t in tpls.items():
        miss = [f for f in subs.SUB_FIELDS if f not in t]
        must(f"{tid} 必填字段齐全", not miss, "缺 " + "、".join(miss))
    # 关键字段：字体 / 字号 / 描边 / 阴影 / 强调方案 / 位置 / 行宽 / 大小写规则
    keys = ("font", "size_px", "stroke", "shadow", "emphasis", "position",
            "max_units_per_line", "case_rules", "color")
    ids = list(tpls)
    worst = None
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = tpls[ids[i]], tpls[ids[j]]
            diff = [k for k in keys if a.get(k) != b.get(k)]
            if worst is None or len(diff) < len(worst[2]):
                worst = (ids[i], ids[j], diff)
            must(f"{ids[i]} ≠ {ids[j]}：至少 3 处关键字段不同", len(diff) >= 3, "、".join(diff))
    must("最像的两套也差 ≥3 处（不是同一套换名字）", bool(worst) and len(worst[2]) >= 3,
         str(worst))
    # 四套都要写清「适合什么 / 不适合什么」，别只留个名字
    for tid, t in tpls.items():
        must(f"{tid} 写了中文名 + 适合/不适合",
             bool(str(t.get("name") or "").strip()) and bool(str(t.get("suits") or "").strip())
             and bool(str(t.get("not_suits") or "").strip()), tid)
    must("默认模板在表里", doc["default"] in tpls, doc["default"])

    # case_rules 不是装饰：keep_together 里的词真的不许被切开（同一条拆条链路上生效）
    line = "佢个名叫做点赞茶楼真系好"
    term = "点赞茶楼"
    at = line.find(term)
    g = [(ch, i * 0.2, (i + 1) * 0.2) for i, ch in enumerate(line)]
    with subs.width_scope(8, None):
        plain = subs.split_long(list(g))
    with subs.width_scope(8, {"keep_together": [term]}):
        kept = subs.split_long(list(g))
    must("不加 case_rules 时这个词真会被切开（负样本，说明这条规则有用）",
         any(at < c < at + len(term) for c in plain), str(plain))
    must("加了 keep_together 之后不再切在词内部", not any(at < c < at + len(term) for c in kept),
         str(kept))


# ---------------------------------------------------------------- ② 字体授权

def case_licence() -> None:
    eye("② 每套字体都在 fonts.yaml 里登记，且许可可商用（unverified 进不来）")
    doc = subs.load_subtitle_templates()
    import fonts as fonts_mod
    for tid, t in doc["templates"].items():
        alias, ent = subs._font_entry(str(t["font"]), fonts_mod.load())
        must(f"{tid} 的字体「{t['font']}」在 styles/fonts.yaml 里", ent is not None)
        lic = subs.font_licence(t["font"])
        must(f"{tid} 的字体许可不是 unverified", lic["ok"] and lic["license"] != "unverified",
             lic["why"] or lic["license"])
        must(f"{tid} 的字体许可写明可商用（OFL/商业授权）",
             "open font license" in lic["license"].lower() or lic["commercial_ok"] is True,
             lic["license"])
        # 文件级核验：本机装了字体就必须对得上（没装 → partial，也不算失败）
        info = fonts_mod.licence_of(str(t["font"]))
        must(f"{tid} 的字体授权记录与字体文件 name 表一致（或本机没装该字）",
             info["status"] in ("ok", "partial"), info["status"])

    # 负样本：把一套模板的字体换成一个「没登记 / 没授权」的字 → 必须报错
    with tempfile.TemporaryDirectory() as td:
        src = (ROOT / "styles" / "subtitles.yaml").read_text(encoding="utf-8")
        bad_font = src.replace("    font: 思源黑体\n", "    font: 某只没授权的字\n", 1)
        must("负样本夹具真的改到了字体", bad_font != src)
        p = Path(td) / "subtitles.yaml"
        p.write_text(bad_font, encoding="utf-8")
        try:
            subs.load_subtitle_templates(p)
            must("负样本：没登记的字体 → 必须报错", False, "居然加载通过了")
        except subs.SubtitleTemplateError as e:
            must("负样本：没登记的字体 → 报错且点名是哪只", "某只没授权的字" in str(e), str(e))

        # 负样本 B：字体表里登记了，但 license 是 unverified
        tbl = {
            "target_unit_w": 0.074,
            "fonts": {"无授权字": {"file": "NoSuchFont-900.ttf", "ratio": 1.0,
                                   "license": "unverified"}},
            "default": "无授权字",
        }
        bad2 = src.replace("    font: 思源黑体\n", "    font: 无授权字\n", 1)
        p2 = Path(td) / "subtitles-unverified.yaml"
        p2.write_text(bad2, encoding="utf-8")
        try:
            subs.load_subtitle_templates(p2, fonts_table=tbl)
            must("负样本：unverified 许可的字体 → 必须报错", False, "居然加载通过了")
        except subs.SubtitleTemplateError as e:
            must("负样本：unverified 许可的字体 → 报错说清「未核验」", "unverified" in str(e)
                 or "未核验" in str(e), str(e))


# ---------------------------------------------------------------- ③ 优先级

def case_priority() -> None:
    eye("③ 优先级：模板 > 包装包 > 默认模板，而且判据真的按换上的那套判")
    pack = {"subtitle": {"font": "得意黑", "size_px": 80, "color": "#FFFFFF",
                         "stroke": "2px rgba(0,0,0,0.35)",
                         "shadow": "0 3px 8px rgba(0,0,0,.75)",
                         "center_y_px": 1294, "max_chars_per_line": 10,
                         "entry": "hard_cut", "colorize_emphasis": False}}
    cfg_pack = subs.subtitle_config(None, pack)
    must("没选模板 → 走包装包那一条（source=pack）", cfg_pack["source"] == "pack",
         cfg_pack["source"])
    must("包装包口径的行宽 = 共用上限 10 单位", cfg_pack["max_units_per_line"] == subs.MAX_UNITS,
         str(cfg_pack["max_units_per_line"]))

    with tempfile.TemporaryDirectory() as td:
        # 9 单位的字幕：包装包口径（≤10）合规；选 bold-outline（≤8）就不合规
        cues = make_cues(9.0)
        proj = mk_project(Path(td) / "p", cues, {"pack": "P1-warm"})
        code_a, rep_a, err_a = run_check(proj)
        must("不选模板：9 单位字幕判绿", bool(rep_a) and rep_a["checks"]["over_wide"]["pass"],
             err_a or str(rep_a and rep_a["checks"]["over_wide"]))
        must("不选模板：口径写着包装包，行宽需要 ≤10 单位",
             bool(rep_a) and rep_a["subtitle_style"]["source"] == "pack"
             and "10" in str(rep_a["checks"]["over_wide"]["need"]),
             str(rep_a and rep_a["subtitle_style"]))

        brief = {"pack": "P1-warm", "subtitle_template": "bold-outline"}
        proj2 = mk_project(Path(td) / "p2", cues, brief)
        code_b, rep_b, err_b = run_check(proj2)
        must("选了 bold-outline：同一份字幕按模板口径判红（9 > 8 单位）",
             bool(rep_b) and not rep_b["checks"]["over_wide"]["pass"] and code_b != 0,
             err_b or "exit=" + str(code_b))
        must("选了模板：报告里的口径真的换成模板",
             bool(rep_b) and rep_b["subtitle_style"]["source"] == "template:bold-outline"
             and rep_b["subtitle_style"]["size_px"] == 96,
             str(rep_b and rep_b["subtitle_style"]))
        must("选了模板：行宽判据写成 ≤8 单位（不是仍按 10）",
             bool(rep_b) and "8" in str(rep_b["checks"]["over_wide"]["need"]),
             str(rep_b and rep_b["checks"]["over_wide"]))
        # 像素口径：同一批字，模板 96px 与包装包 91px（得意黑反算）宽出来的数不同
        must("字号口径也换了：模板 96px 的像素宽 > 包装包反算的 91px",
             bool(rep_a) and bool(rep_b)
             and rep_b["widths"]["widest_px"] > rep_a["widths"]["widest_px"],
             f"{rep_a and rep_a['widths']['widest_px']} vs {rep_b and rep_b['widths']['widest_px']}")

        # 反方向：包装包自己写了更窄的行宽（6 单位）→ 包装包口径判红，换 cool-thin（10）合规
        narrow = {"subtitle": {**pack["subtitle"], "max_chars_per_line": 6}}
        cfg_narrow = subs.subtitle_config(None, narrow)
        must("包装包写 6 单位 → 生效口径跟着收窄到 6（判据按包判）",
             cfg_narrow["max_units_per_line"] == 6.0, str(cfg_narrow["max_units_per_line"]))
        must("包装包写 14 单位也不会把判据放到 10 以上（只许收窄）",
             subs.subtitle_config(None, {"subtitle": {**pack["subtitle"],
                                                     "max_chars_per_line": 14}}
                                  )["max_units_per_line"] == subs.MAX_UNITS)
        # 模板 10 单位 > 包装包 6 单位：同一批 9 单位字，模板合规、包装包不合规
        with tempfile.TemporaryDirectory() as td2:
            proj3 = mk_project(Path(td2) / "p3", cues, {"subtitle_template": "cool-thin"})
            code_c, rep_c, err_c = run_check(proj3)
            must("选了 cool-thin（≤10 单位）：9 单位字幕判绿（模板合规）",
                 bool(rep_c) and rep_c["checks"]["over_wide"]["pass"], err_c)
            must("同一批字：包装包 6 单位口径下是非法的（两个口径判得出相反结论）",
                 cfg_narrow["max_units_per_line"] < 9.0,
                 str(cfg_narrow["max_units_per_line"]))

        # 优先级第一档：项目选了模板就压过包装包
        cfg_both = subs.subtitle_config(proj2, pack)
        must("项目选了模板 → 压过包装包（source=template:*）",
             cfg_both["source"] == "template:bold-outline", cfg_both["source"])
        must("项目选的模板连字体都换（得意黑→模板里的字）",
             cfg_both["font"] == subs.subtitle_template("bold-outline")["font"],
             cfg_both["font"])


# ---------------------------------------------------------------- ④ 向后兼容

def case_compat() -> None:
    eye("④ 不选模板 → 与旧版一致（向后兼容）")
    must("口径默认值 = 共用常量（max_units 10 / soft 8 / hard 10 / max_chars 10）",
         (subs._W["max_units"], subs._W["soft"], subs._W["hard"], subs._W["max_chars"])
         == (subs.MAX_UNITS, subs.TARGET_SOFT_UNITS, subs.HARD_UNITS, subs.MAX_CHARS),
         str(subs._W))

    # `subs.build()` 的行为：不传参数 == 显式传 10 单位（同一条拆条链路，逐条比对）
    items = []
    text = "我花咗三百万喺金湾开咗间六百平嘅点赞茶楼"
    for i, ch in enumerate(text):
        items.append((ch, round(i * 0.18, 3), round((i + 1) * 0.18, 3)))
        if ch in "咗嘅":
            items.append((subs.SENTINEL, round((i + 1) * 0.18, 3), round((i + 1) * 0.18, 3)))
    a = subs.regroup(list(items))
    with subs.width_scope(subs.MAX_UNITS, None):
        b = subs.regroup(list(items))
    must("regroup：默认口径与显式 10 单位逐条一致（向后兼容）",
         [(len(g), w, "".join(t for t, _, _ in g)) for g, w in a]
         == [(len(g), w, "".join(t for t, _, _ in g)) for g, w in b],
         str([(len(g), w) for g, w in a]) + " vs " + str([(len(g), w) for g, w in b]))
    must("拆完每条都不超过 10 单位（默认口径没被放宽）",
         all(subs.display_units("".join(t for t, _, _ in g)) <= subs.MAX_UNITS + 1e-9
             for g, _ in a),
         str([subs.display_units("".join(t for t, _, _ in g)) for g, _ in a]))

    # 包装包的参数原样透出（渲染器读的就是这些键）
    pack = {"canvas": {"width": 1080, "height": 1920},
            "subtitle": {"font": "得意黑", "size_px": 80, "color": "#FFFFFF",
                         "stroke": "2px rgba(0,0,0,0.35)",
                         "shadow": "0 3px 8px rgba(0,0,0,.75), 0 1px 2px rgba(0,0,0,.9)",
                         "center_y_px": 1294, "max_chars_per_line": 10,
                         "entry": "hard_cut", "colorize_emphasis": False},
            "emphasis": {"keyword_color": "#F6EB34"}}
    cfg = subs.subtitle_config(None, pack)
    must("包装包口径原样透出：字体/字号/字色/描边/位置/行宽",
         (cfg["font"], cfg["size_px"], cfg["color"], cfg["stroke"], cfg["center_y_px"],
          cfg["max_units_per_line"]) == ("得意黑", 80, "#FFFFFF",
                                         "2px rgba(0,0,0,0.35)", 1294, 10.0),
         json.dumps({k: cfg[k] for k in ("font", "size_px", "color", "stroke",
                                         "center_y_px", "max_units_per_line")},
                    ensure_ascii=False))
    must("包装包的强调开关不被模板改写（colorize_emphasis: false 仍为 false）",
         cfg["colorize"] is False, str(cfg["colorize"]))
    must("包装包口径不冒充模板（template=False、不带字号锁）",
         cfg["template"] is False and "font_size_locked" not in cfg, str(cfg["template"]))

    # 检查器：不选模板时行宽读数仍是 10 单位（老项目的判据一字未改）
    with tempfile.TemporaryDirectory() as td:
        proj = mk_project(Path(td) / "p", make_cues(9.0), {"pack": "P1-warm"})
        code, rep, err = run_check(proj)
        must("不选模板：check_subs 行宽上限仍是 10 单位", bool(rep)
             and "10" in str(rep["checks"]["over_wide"]["need"]), err)
        must("不选模板：没有模板字样混进报告", bool(rep)
             and rep["subtitle_style"]["source"] == "pack"
             and rep["subtitle_style"]["id"] is None, str(rep and rep["subtitle_style"]))
        must("不选模板：9 单位字幕仍判绿（没被新判据误伤）", bool(rep) and code == 0,
             err or str(code))
        # 像素安全区判据对老项目不该误伤：P1 的 10 单位也装得下
        must("不选模板：像素安全区判据对 10 单位老字幕也放行",
             bool(rep) and rep["checks"]["text_width_px"]["pass"],
             str(rep and rep["checks"]["text_width_px"]))


# ---------------------------------------------------------------- ⑤ 报错可读

def case_errors() -> None:
    eye("⑤ 错模板 id / 坏 YAML / 缺字体 → 可读报错，不崩")
    with tempfile.TemporaryDirectory() as td:
        # 坏 YAML（缩进断了）
        bad = Path(td) / "broken.yaml"
        bad.write_text("templates:\n  a:\n   id: a\n     name: 缩进坏了\n", encoding="utf-8")
        try:
            subs.load_subtitle_templates(bad)
            must("坏 YAML → 报错", False, "居然加载通过了")
        except subs.SubtitleTemplateError as e:
            must("坏 YAML → 可读报错（点名文件 + YAML）",
                 "YAML" in str(e) and "broken.yaml" in str(e), str(e))
        except Exception as e:                                     # noqa: BLE001
            must("坏 YAML → 报错类型是 SubtitleTemplateError（不是裸异常）", False, repr(e))

        # 缺字段
        miss = Path(td) / "missing.yaml"
        miss.write_text("templates:\n  x:\n    id: x\n    name: 缺一半\n"
                        "default: x\n", encoding="utf-8")
        try:
            subs.load_subtitle_templates(miss)
            must("缺字段 → 报错", False, "居然加载通过了")
        except subs.SubtitleTemplateError as e:
            must("缺字段 → 报错点名缺了哪些字段", "缺字段" in str(e), str(e))

        # 模板 id 不存在：函数层与 CLI 层都要有话说
        try:
            subs.subtitle_template("no-such-template")
            must("不存在的 id → 报错", False, "居然取到了")
        except subs.SubtitleTemplateError as e:
            must("不存在的 id → 报错并列出可选的", "no-such-template" in str(e)
                 and "cool-thin" in str(e), str(e))
        # brief.json 里写了不存在的 id：不许静默退回包装包
        proj = mk_project(Path(td) / "badid", make_cues(9.0),
                          {"pack": "P1-warm", "subtitle_template": "no-such-template"})
        try:
            subs.subtitle_config(proj, None)
            must("brief 里的 id 不存在 → 报错（不许静默退回包装包）", False, "静默退回了")
        except subs.SubtitleTemplateError as e:
            must("brief 里的 id 不存在 → 报错写明是 brief 里选的", "brief.json" in str(e), str(e))
        code, rep, err = run_check(proj)
        must("check_subs 遇到坏模板配置：退出码非 0 且给人话",
             code != 0 and "字幕模板" in err and "Traceback" not in err,
             f"exit={code}｜{err[:160]}")
        # 字体缺失（表里没登记）
        tbl_bad = {"target_unit_w": 0.074, "fonts": {}, "default": None}
        try:
            subs.load_subtitle_templates(None, fonts_table=tbl_bad)
            must("字体表里一只都没有 → 报错", False, "居然加载通过了")
        except subs.SubtitleTemplateError as e:
            must("字体表里一只都没有 → 报错点名字体", "字体" in str(e), str(e))

        # CLI：--subtitles --choose 一个不存在的 id → 退出 1 + 可选清单，不写 brief
        r = subprocess.run([PY, str(SCRIPTS / "style_menu.py"), "--subtitles",
                            "--choose", "no-such-template", "--project", str(proj)],
                           capture_output=True, text=True, env=CHILD_ENV, timeout=120)
        must("style_menu --choose 不存在的 id：退出码 1 + 列出可选 + 不打 traceback",
             r.returncode == 1 and "cool-thin" in (r.stdout + r.stderr)
             and "Traceback" not in (r.stderr + r.stdout),
             f"exit={r.returncode}｜{(r.stdout + r.stderr)[:160]}")
        brief = json.loads((proj / "brief.json").read_text())
        must("style_menu 不会把坏 id 写进 brief.json", brief.get("subtitle_template")
             == "no-such-template", str(brief))     # 没被改成别的，也没被清空


def main() -> int:
    case_fields()
    case_licence()
    case_priority()
    case_compat()
    case_errors()
    print()
    if fails:
        print(f"❌ {len(fails)} 条不过：" + "、".join(fails))
        return 1
    print("✅ 字幕模板：四套字段/授权/优先级/向后兼容/报错 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
