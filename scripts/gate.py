#!/usr/bin/env python3
"""CutSkills 项目总门禁：集中运行现有检查器并汇总 error / warning。

本脚本只编排检查器，不复制判据实现。warning 如实汇总但不阻止；任一 error 或任一
检查器无法执行均退出 1。broll_split 的跨片检查可传 --projects；dianzan-* 默认同组扫描。

用法：
  python3 scripts/gate.py --project <项目> [--library <素材库>] [--projects <项目A> <项目B> ...]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


@dataclass
class Result:
    name: str
    errors: int
    warnings: int
    conclusion: str


def count_levels(rows, *, error_levels=("error",), warning_levels=("warning",)):
    return (sum(1 for lv, _ in rows if lv in error_levels),
            sum(1 for lv, _ in rows if lv in warning_levels))


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def resolve_library(project: Path, given: str | None = None) -> Path:
    if given:
        return Path(given).expanduser().resolve()
    for parent in (project, *project.parents):
        if (parent / "_cutskills" / "index.json").exists():
            return parent
    return project


def sibling_group(project: Path, supplied: list[str] | None) -> list[Path]:
    if supplied:
        paths = [Path(x).expanduser().resolve() for x in supplied]
        if project not in paths:
            paths.insert(0, project)
        return list(dict.fromkeys(paths))
    if project.name in {"dianzan-a", "dianzan-b", "dianzan-c"}:
        group = [project.parent / name for name in ("dianzan-a", "dianzan-b", "dianzan-c")
                 if (project.parent / name / "timeline.json").exists()]
        if project not in group:
            group.insert(0, project)
        return group
    return [project]


def cli_result(name: str, cmd: list[str], parse, *, cwd: Path = ROOT) -> Result:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    text = (p.stdout or "") + (p.stderr or "")
    try:
        errors, warnings, conclusion = parse(text, p.returncode)
    except Exception as exc:  # noqa: BLE001
        return Result(name, 1, 0, f"结果解析失败：{type(exc).__name__}: {exc}")
    if p.returncode == 1 and errors == 0:
        errors = 1
        conclusion = f"检查器返回失败但摘要未解析出 error：{conclusion or text.strip()[-180:]}"
    elif p.returncode not in (0, 1) and errors == 0:
        errors = 1
        conclusion = f"检查器退出码 {p.returncode}：{conclusion or text.strip()[-180:]}"
    return Result(name, errors, warnings, conclusion or "完成")


def run(project: Path, library: Path, group: list[Path]) -> list[Result]:
    results: list[Result] = []
    py = sys.executable
    scripts = ROOT / "scripts"
    project_s = str(project)
    library_s = str(library)
    import check_timeline as TL
    try:
        index = TL.load_index(library)
    except Exception:  # noqa: BLE001
        index = {}

    # 时间轴及其 T-015～T-019 报告节。只解析输出读数，不重复实现判据。
    timeline_readout: list[dict] = []
    def parse_timeline(text, rc):
        rep = json.loads(text)
        probs = rep.get("problems") or []
        errors = sum(1 for lv, _ in probs if lv == "error")
        warnings = sum(1 for lv, _ in probs if lv == "warning")
        timeline_readout[:] = rep.get("t015_019") or []
        return errors, warnings, f"时间轴 error {errors} · warning {warnings}"
    results.append(cli_result("check_timeline", [py, str(scripts / "check_timeline.py"),
                              "--project", project_s, "--library", library_s, "--json"], parse_timeline))

    # 字幕：直接采纳 check_subs 的结构化读数；失效项是 error，stale 帧窗口与可疑量级是 warning。
    def parse_subs(text, rc):
        rep = json.loads(text)
        checks = rep.get("checks") or {}
        errors = sum(1 for v in checks.values() if not v.get("pass") and not v.get("stale"))
        warnings = sum(1 for v in checks.values() if v.get("stale"))
        sem = rep.get("semantic") or {}
        warnings += len(sem.get("magnitude_note") or [])
        return errors, warnings, (f"{rep.get('cues', 0)} 条字幕 · "
                                 f"{sum(1 for v in checks.values() if not v.get('pass'))} 项未通过")
    results.append(cli_result("check_subs", [py, str(scripts / "check_subs.py"),
                              "--project", project_s, "--library", library_s, "--json"], parse_subs))

    import check_cards as CC
    try:
        errs = CC.audit(project, library)
        e, w = count_levels(errs)
        results.append(Result("check_cards", e, w, f"卡准入 {len(errs)} 条读数" if errs else "卡准入无命中"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("check_cards", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import check_timing as CT
    try:
        rows = CT.check(project, library)
        e, w = count_levels(rows)
        results.append(Result("check_timing", e, w, f"声音在前 / 锚词对账 {len(rows)} 条读数" if rows else "时序与锚词无命中"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("check_timing", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import check_overlay as CO
    try:
        rows = CO.audit(project)
        e, w = count_levels(rows)
        results.append(Result("check_overlay", e, w, f"叠加态 {len(rows)} 条读数" if rows else "叠加态无命中"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("check_overlay", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import check_keyword_text as KT
    try:
        film = next((p for p in (project / "out" / "film-hf.mp4", project / "out" / "film.mp4") if p.exists()), None)
        rows = KT.audit(project, film)
        e, w = count_levels(rows)
        results.append(Result("check_keyword_text", e, w,
                              f"大字 {len(rows)} 条读数" if rows else "大字无命中"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("check_keyword_text", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import edge_audit as EA
    try:
        rows = EA.audit(project, library)
        e = sum(1 for lv, _ in rows if lv == "红")
        w = sum(1 for lv, _ in rows if lv == "黄")
        results.append(Result("edge_audit", e, w, f"边界审计 {len(rows)} 条读数" if rows else "边界审计无命中"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("edge_audit", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import content_guard as CG
    try:
        tl = read_json(project / "timeline.json", {}) or {}
        rows = CG.guard(tl, index)  # 不传 proj：汇总只读，不在 gate 里写 gap.md。
        e, w = count_levels(rows)
        results.append(Result("content_guard", e, w,
                              f"起尾余量 / 指代 / 禁忌 {len(rows)} 条读数" if rows else "内容守卫无命中"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("content_guard", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import visual_grade as VG
    try:
        tl = read_json(project / "timeline.json", {}) or {}
        rows = VG.gate(tl.get("elements") or [], library)
        e, w = count_levels(rows)
        results.append(Result("visual_grade", e, w, f"声/画双评级 {len(rows)} 条读数" if rows else "双评级无冲突"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("visual_grade", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import lint_transcript as LT
    try:
        assets = LT.load_index(library)
        sem = LT.semantic_scan(LT.all_text(assets))
        # lint_transcript 是库级待审工单生成器，不是项目放行 CLI；其命中计 warning，
        # 项目字幕的硬拦截由 check_subs.semantic_judge 单一真值源负责。
        warnings = (len(sem["suspects"]) + len(sem["stutters"])
                    + len(sem["magnitudes"]))
        results.append(Result("lint_transcript", 0, warnings,
                              f"库级待审：荒谬/可疑量级 {len(sem['magnitudes'])} · "
                              f"残词嫌疑 {len(sem['suspects'])} · 口吃 {len(sem['stutters'])}"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("lint_transcript", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import broll_split as BS
    try:
        rows = BS.audit(group)
        e, w = count_levels(rows)
        results.append(Result("broll_split", e, w,
                              f"素材互斥扫描 {len(group)} 个项目 · {len(rows)} 条读数"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("broll_split", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import brand_safety as BRAND
    try:
        tl = read_json(project / "timeline.json", {}) or {}
        dec = (project / "decisions.md").read_text(encoding="utf-8") if (project / "decisions.md").exists() else None
        items = [{"n": el.get("n"), "asset": el.get("asset"), "in": float(el.get("in") or 0),
                  "out": float(el.get("out") or 0),
                  "third_party_action": el.get("third_party_action") or el.get("third_party")}
                 for el in tl.get("elements") or [] if el.get("kind") == "clip" and el.get("asset")]
        hints: list[str] = []
        unresolved, resolved = BRAND.scan_third_party_risks(items, index, decisions_text=dec, hints=hints)
        warnings = len(unresolved) + len(hints)
        results.append(Result("brand_safety", 0, warnings,
                              f"未处置风险 {len(unresolved)} · 已处置 {len(resolved)} · 散文提示 {len(hints)}"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("brand_safety", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    import cue_guard as CUE
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CUE.check(project)
        text = buf.getvalue()
        e = sum(line.lstrip().startswith("🔴") for line in text.splitlines())
        w = sum(line.lstrip().startswith("🟡") for line in text.splitlines())
        if rc == 2 and not e:
            e = 1
        if rc == 1 and not w:
            w = 1
        results.append(Result("cue_guard", e, w,
                              text.strip().splitlines()[-1] if text.strip() else f"exit {rc}"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("cue_guard", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    # 包装结构（T-018）：gate 与 timeline 报告共用 check_packs 的检查函数，不触发渲染写入。
    try:
        import check_packs as CP
        import yaml
        from render_hf import GRADE
        tl = read_json(project / "timeline.json", {}) or {}
        pack_id = str(tl.get("pack") or tl.get("style_pack") or "").strip()
        pack_f = ROOT / "packs" / f"{pack_id}.yaml"
        if not pack_f.exists():
            pack_f = ROOT / "styles" / f"{pack_id}.yaml"
        if pack_id and pack_f.exists():
            pack = yaml.safe_load(pack_f.read_text(encoding="utf-8")) or {}
            pe, pw = CP.check_schema(pack_id, pack, set(GRADE))
            pe += CP.check_renderer_source()
            results.append(Result("check_packs", len(pe), len(pw),
                                  f"{pack_id} · schema/surface/renderer"))
        else:
            results.append(Result("check_packs", 0, 1, f"无法从时间轴解析仓库包装包：{pack_id or '空'}"))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("check_packs", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    # verify_film 是产线验收入口（其报告写回项目 verify-report.md 属于既有行为）。
    def parse_verify(text, rc):
        m = re.search(r"验收：🟢\s*(\d+)\s*·\s*🟡\s*(\d+)\s*·\s*🔴\s*(\d+)", text)
        if not m:
            return (1 if rc else 0), 0, text.strip().splitlines()[-1] if text.strip() else "无验收摘要"
        green, yellow, red = map(int, m.groups())
        detail = next((line.strip() for line in text.splitlines() if line.strip().startswith("→")), "验收已完成")
        return red, yellow, f"🟢 {green} · 🟡 {yellow} · 🔴 {red}；{detail}"
    verify_result = cli_result("verify_film", [py, str(scripts / "verify_film.py"),
                                 "--project", project_s, "--library", library_s], parse_verify)
    results.append(verify_result)
    # verify_film.py 属禁止修改的单一真值源；由 gate 将 check_timeline 已汇总的同一组
    # 读数接到它刚生成的报告末尾，不复制任何 T-015～T-019 判据。
    report = project / "verify-report.md"
    if timeline_readout and report.exists():
        marker = "## T-015～T-019 新判据读数汇总"
        text = report.read_text(encoding="utf-8").split(marker, 1)[0].rstrip()
        section = ["", marker, "", "| 判据 | error | warning | 一行结论 |",
                   "|---|---:|---:|---|"]
        for row in timeline_readout:
            conclusion = str(row.get("conclusion") or "").replace("|", "/")
            section.append(f"| {row.get('task')} | {row.get('errors', 0)} | "
                           f"{row.get('warnings', 0)} | {conclusion} |")
        report.write_text(text + "\n" + "\n".join(section) + "\n", encoding="utf-8")
        verify_result.conclusion += "；已附 T-015～T-019 汇总"
    elif not report.exists():
        verify_result.errors += 1
        verify_result.conclusion += "；verify-report.md 未生成，T-015～T-019 汇总未能落入报告"

    # 音效可听度（评审 P0-04）：verify_film 只答响度与画面，音效听不听得见归 check_sfx。
    def parse_sfx(text, rc):
        m = re.search(r"(\d+)/(\d+) 处过", text)
        if rc in (2, 3):
            return 0, 0, text.strip().splitlines()[-1] if text.strip() else "无音效可测"
        if not m:
            return 1, 0, text.strip()[-160:]
        p_, t_ = int(m.group(1)), int(m.group(2))
        return (t_ - p_), 0, f"{p_}/{t_} 处净抬升过线（≥2.5dB）"
    results.append(cli_result("check_sfx", [py, str(scripts / "check_sfx.py"),
                              "--project", project_s], parse_sfx))

    # 场景任务单（评审 P0-04）：template:scene / scene_spec 引用的完整性、六问、
    # claim_source 与音效事件在库；未接任务单的旧场景渐进采纳，只计 warning。
    def parse_scene(text, rc):
        m = re.search(r"error (\d+) · warning (\d+)", text)
        if m:
            return int(m.group(1)), int(m.group(2)), text.strip().splitlines()[-1]
        return (1 if rc else 0), 0, text.strip()[-160:]
    results.append(cli_result("scene_spec", [py, str(scripts / "scene_spec.py"),
                              "--check", "--project", project_s], parse_scene))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="运行项目总门禁并输出检查器汇总表")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None, help="素材库根；默认从项目祖先找 _cutskills/index.json")
    ap.add_argument("--projects", nargs="*", default=None,
                    help="broll_split 互斥检查的同组项目目录；dianzan-* 默认自动发现同组")
    a = ap.parse_args()
    project = Path(a.project).expanduser().resolve()
    library = resolve_library(project, a.library)
    group = sibling_group(project, a.projects)
    rows = run(project, library, group)
    print(f"总门禁 · 项目 {project.name} · 素材库 {library}")
    print("| 工具名 | error | warning | 一行结论 |")
    print("|---|---:|---:|---|")
    for r in rows:
        print(f"| {r.name} | {r.errors} | {r.warnings} | {r.conclusion.replace('|', '/')} |")
    total_errors = sum(r.errors for r in rows)
    total_warnings = sum(r.warnings for r in rows)
    print(f"\n合计：error {total_errors} · warning {total_warnings} · "
          f"检查器 {len(rows)} 项 · broll_split 项目数 {len(group)}")
    print("注：合计按工具行求和，不跨工具去重；综合检查与专项检查可能同时报同一问题。")
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
