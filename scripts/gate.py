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
# 第三方依赖的两处挂载（与 verify_film.py / render_hf.py 同一个做法）：
#   · pyyaml 是纯 Python，随仓放在 scripts/vendor；
#   · pillow / numpy 是 C 扩展，装在 $CUTSKILLS_HOME/py（deps.json）。
# 不挂上它们，换一台没 pip install 过的机器，下面每个检查器都会变成
# 「检查器异常：ModuleNotFoundError」——看着像判据坏了，其实只是没装依赖。
sys.path.insert(0, str(ROOT / "scripts" / "vendor"))
try:
    from libs import cutskills_home as _cutskills_home
    _PY_DEPS = _cutskills_home() / "py"
    if _PY_DEPS.exists() and str(_PY_DEPS) not in sys.path:
        sys.path.append(str(_PY_DEPS))
except ImportError:
    pass


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


def _child_env() -> dict:
    """给子检查器带上随仓依赖与 $CUTSKILLS_HOME/py。

    cli_result 是**子进程**跑的：本文件顶层的 sys.path 挂载管不到它们。没有 PYTHONPATH 时
    check_sfx / check_timeline 就在 `import numpy` 上 ModuleNotFoundError，gate 报出来的
    却是「检查器异常」——看着像判据坏了。（2026-11-15 实测：干净环境下 check_sfx、
    check_timeline 同时挂。）runtime.sh 本来就是这么导的，这里补上同一个口径。
    """
    import os
    env = dict(os.environ)
    parts = [str(ROOT / "scripts" / "vendor")]
    if _PY_DEPS.exists():
        parts.append(str(_PY_DEPS))
    parts.append(str(ROOT / "scripts"))
    cur = env.get("PYTHONPATH")
    if cur:
        parts.append(cur)
    env["PYTHONPATH"] = ":".join(parts)
    return env


def cli_result(name: str, cmd: list[str], parse, *, cwd: Path = ROOT,
               accept_rc: tuple = (0,)) -> Result:
    """跑一个子检查器并把它的输出交给 `parse` 解读。

    `accept_rc`：哪些退出码算「跑完了」。默认只有 0——但有的检查器用 1 表示「判完了、
    有项没过」，而「没过」到底是 error 还是 warning 由本项目决定（比如 sync_check 的
    85% 同步率降成黄灯）。这种检查器要把 1 一起写进来，否则下面那条「退出码 1 但没解析出
    error → 当 error」的兜底会把它重新打回红灯，降级就白做了。
    """
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=_child_env())
    text = (p.stdout or "") + (p.stderr or "")
    try:
        errors, warnings, conclusion = parse(text, p.returncode)
    except Exception as exc:  # noqa: BLE001
        return Result(name, 1, 0, f"结果解析失败：{type(exc).__name__}: {exc}")
    if p.returncode == 1 and errors == 0 and 1 not in accept_rc:
        errors = 1
        conclusion = f"检查器返回失败但摘要未解析出 error：{conclusion or text.strip()[-180:]}"
    elif p.returncode not in (0, 1) and errors == 0:
        errors = 1
        conclusion = f"检查器退出码 {p.returncode}：{conclusion or text.strip()[-180:]}"
    return Result(name, errors, warnings, conclusion or "完成")


def parse_sync(text: str, rc: int = 0):
    """`sync_check.py --json` 的结构化输出 → (error, warning, 一行结论)。

    **哪个数能拦交付，按 BRIEF 分：**
      · 卡片锚词「只许晚不许早」（state=fail）→ **error**。有物理依据：卡先于它锚定的
        那个词出现就是抢拍，观众看到字、耳朵里还没有声音。
      · 同步率 ≥85%（视觉事件落在词/气口边界 ±80ms 内的比例）→ **warning**。
        口径与 BRIEF 的两条要求（口型偏差 ≤1 帧、音视频总时长差 ≤1 帧）不是一回事：
        它连字幕换句都算成「视觉事件」，而 `sync_check.py` 自己的 docstring 写着
        「这三个数没有用你的素材校准过」。拿一条未标定、口径不同的数当硬门，结果就是
        8/8 成片全红、所有人学会「反正会红」——那是坏门禁，不是严门禁。
        这两条的**硬判据**在 verify_film.py（`av_duration_verdict` / `mouth_sync_verdict`），
        客观可复现、与素材无关。
      · 断章率 ≤25%→ **warning**，同理（参考片自己 24%，标定源只有一支）。

    标定办法（没做完之前它不阻断交付）：拿 20 支参考片 + 自有成片的像素切点与词边界
    重算这个比例的分布，看 85% 落在第几百分位；同时把「视觉事件」拆成「硬切」与
    「字幕/卡片入场」两类分别算——现在这两类混在一起，字幕密的片天然吃亏。

    跑不起来（没 timeline.json 或没 audio-map.json）时按最后一行报错，算 error：
    “判不了”和“判过了”是两件事，本仓吃过这个亏。
    """
    try:
        rep = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        tail = (str(text or "").strip().splitlines() or ["sync_check 无输出"])[-1]
        return 1, 0, f"跑不起来：{tail[:150]}"
    verdict = rep.get("verdict") or {}
    sync_v = (verdict.get("sync_rate") or {}).get("value")
    cut_v = (verdict.get("cut_inside_rate") or {}).get("value")
    lag = verdict.get("card_lag_ms") or {}
    lag_v = lag.get("value")

    def rate(v):
        return f"{v:.0%}" if isinstance(v, (int, float)) else "—"

    sync_fail = (verdict.get("sync_rate") or {}).get("pass") is False
    cut_fail = (verdict.get("cut_inside_rate") or {}).get("pass") is False
    # 卡锚词拆成两半判，不按 aggregate 的 state 一棍子打死：
    #   · 画面先到（lag < 下限，即锚词还没出声卡就上屏了）→ **红**。这是物理：
    #     耳朵里的声音后到，观众先看见字后听见话，必然觉得抢拍。
    #   · 晚太多（lag > 上限 120ms）→ **黄**。「允许晚多少」这个 120ms 没有标定过
    #     （sync_check 的默认值），拿它当硬门会造出一批「反正会红」的片。
    need = lag.get("need") or [0, 120]
    lo_need = float(need[0]) if isinstance(need, list) and need else 0.0
    hi_need = float(need[-1]) if isinstance(need, list) and need else 120.0
    cards_lag = [c for c in (lag.get("cards") or [])
                 if isinstance(c.get("lag_ms"), (int, float))]
    early = [c for c in cards_lag if c["lag_ms"] < lo_need]
    late = [c for c in cards_lag if c["lag_ms"] > hi_need]
    lag_state = str(lag.get("state") or "")
    lag_txt = {"pass": "全在窗内", "na": "不适用（让位给 overlay）"}.get(lag_state, "")
    if early:
        lag_txt = (f"{len(early)} 张抢拍（红）" +
                   (f"，最早 {min(c['lag_ms'] for c in early)}ms" if early else ""))
    elif late:
        lag_txt = f"{len(late)} 张晚于 {hi_need:.0f}ms（未标定，黄）"

    conclusion = (f"同步率 {rate(sync_v)}{'✗未校准' if sync_fail else ''} · "
                  f"断章率 {rate(cut_v)}{'✗未校准' if cut_fail else ''} · "
                  f"卡片锚词 {lag_v if lag_v is not None else '—'}"
                  + (f"（{lag_txt}）" if lag_txt else ""))
    warnings = (int(sync_fail) + int(cut_fail)
                + int(bool(late) and not early))
    return (1 if early else 0), warnings, conclusion


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

    # 首帧信息密度（BRIEF 第 4 步「首帧要有足够的画面信息密度」）：verify_film 里那条
    # 只数「前 3 秒变了几次」，首帧是纯色底板/黑场/空镜它一律看不见。这里量第 0 帧本身。
    #
    # **为什么挂 error 而不是 warning（不是「为了不挡人」那个理由）：**
    #   · 8 支真成片（dianzan-a/b/c、r1a-shougong、qizao-ground/lime/naming/protect）
    #     第 0 帧全部判绿（边缘密度 0.078–0.408、熵 0.557–0.939），误报率实测 0/8；
    #     两个负样本（纯黑 mp4、单色底板）全判红，大字卡正样本判绿（见
    #     scripts/test_first_frame.py）。它不是那种「正片天然吃亏」的未标定数——
    #     本文件里 sync_check 的 85% 同步率才因误报太高降为 warning。
    #   · 抓到的是**交付硬伤**：观众手指一滑的第一眼是一块空底板，后面剪得再好也留不住人；
    #     这一项没量成（取不到帧）同理不放行——「判不了」不等于「判过了」。
    # 实测分布与阈值出处：scripts/first_frame.py 文件头（含 8 支真片 + 4 张大字卡的表）。
    import first_frame as FIRST_FRAME
    try:
        ff_film = next((p for p in (project / "out" / "film-hf.mp4", project / "out" / "film.mp4")
                        if p.exists()), None)
        if ff_film is None:
            results.append(Result("first_frame", 1, 0, "没有成片可量：先跑 render_hf.py 再验首帧"))
        else:
            ff_m = FIRST_FRAME.measure(ff_film, proj=project)
            ff_level, ff_reason = FIRST_FRAME.verdict(ff_m)
            if ff_m.get("error"):
                results.append(Result("first_frame", 1, 0, f"判不了：{ff_m['error']}"))
            else:
                results.append(Result("first_frame", 1 if ff_level == "red" else 0,
                                      1 if ff_level == "yellow" else 0, ff_reason))
    except Exception as exc:  # noqa: BLE001
        results.append(Result("first_frame", 1, 0, f"检查器异常：{type(exc).__name__}: {exc}"))

    # 声画同步（BRIEF「音画同步：同期声口型与声音的偏差不超过 1 帧」）：sync_check 一直有判据，
    # 但只在人手动跑的时候才看得到——“判据存在”与“判据进了门禁”是两件事。
    results.append(cli_result("sync_check", [py, str(scripts / "sync_check.py"),
                              "--project", project_s, "--json"], parse_sync,
                              accept_rc=(0, 1)))
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
