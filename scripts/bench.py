#!/usr/bin/env python3
"""CutSkills · 产线基准：量「产出变得可重复」这件事本身，不量这一条片好不好。

**为什么会有这个脚本。** 这套东西最初的目的是「以后每来一批素材，不用从头再来一遍」，
片只是验证手段（2026-11-06 18:53 的定案）。可前面每一次验收（`verify_film.py`）量的都是
**这一件样品**：字幕几条、响度准不准、口播中位多少秒。这些数字全绿，也说明不了下一次
换一批素材要花几个人、几分钟。

产线的第一指标在 `BRIEF.md` 里早就写下了，从来没量过一次：

    60 分钟素材 → 3 分钟成片，机器端到端 ≤45 分钟；人工 ≤2 次、总计 ≤20 分钟

这个脚本就是那把尺：跑一遍 `plan.json → edl → 成片 → 验收`，记下机器分钟数、缓存命中、
人工确认次数，和这条指标对账，再把结果追加进 `_cutskills/bench-history.jsonl`——
**下次跑同一批素材，两个数并排看，才知道这套东西是在变快还是在变复杂。**

人工次数取 `decisions.md` 的条数：那是这套流水线里唯一「人真的做了什么决定」的留痕，
脚本自己不会往里写，所以它数得准。

用法：
  bench.py --library <素材库> --project <项目目录>            # 跑一遍并留档
  bench.py --library <素材库> --project <项目目录> --render-only  # 只量渲染（改一段后的返工成本）
  bench.py --library <素材库> --project <项目目录> --show      # 只看历史，不跑
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def step(name: str, cmd: list[str]) -> tuple[float, str, int]:
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.time() - t0
    out = (r.stdout or "") + (r.stderr or "")
    print(f"  {name:<8} {dt:7.1f}s  {'ok' if r.returncode == 0 else 'FAIL(%d)' % r.returncode}")
    return round(dt, 1), out, r.returncode


def film_stats(proj: Path) -> dict:
    """从成片产物里取几个能算的数。取不到就返回空——别编。"""
    out = proj / "out"
    st: dict = {}
    if (out / "film.mp4").exists():
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_format", str(out / "film.mp4")],
                           capture_output=True, text=True)
        try:
            st["film_s"] = round(float(json.loads(r.stdout)["format"]["duration"]), 2)
            st["film_mb"] = round(int(json.loads(r.stdout)["format"]["size"]) / 1e6, 1)
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    if (out / "subs.json").exists():
        subs = json.loads((out / "subs.json").read_text())
        st["cues"] = len(subs.get("cues") or [])
    if (out / "shots.json").exists():
        st["shots"] = len(json.loads((out / "shots.json").read_text()))
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", default=None, help="默认用项目里的 plan.json")
    ap.add_argument("--render-only", action="store_true", help="只量渲染+验收（改一段的返工成本）")
    ap.add_argument("--show", action="store_true", help="只打印历史")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    proj = Path(a.project).expanduser().resolve()
    hist_path = lib / "_cutskills" / "bench-history.jsonl"

    if a.show:
        if not hist_path.exists():
            print("还没有基准记录。")
            return 0
        print(f"{'时间':<16} {'项目':<18} {'机器分钟':>8} {'人':>3} {'成片':>7} {'缓存':>5}")
        for line in hist_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            print(f"{r['at']:<16} {r['project']:<18} {r['machine_min']:>8.2f} "
                  f"{r['human_touches']:>3} {r.get('film_s', 0):>7.2f} "
                  f"{'全命中' if r.get('all_cached') else '部分'}")
        return 0

    plan = Path(a.plan).expanduser() if a.plan else proj / "plan.json"
    if not plan.exists():
        print(f"没有 plan.json：{plan}", file=sys.stderr)
        return 2

    index = json.loads((lib / "_cutskills" / "index.json").read_text())
    counts = index.get("counts") or {}
    lib_min = float(counts.get("duration_min") or 0)

    print(f"产线基准 · {proj.name} · 库 {lib.name}（{lib_min:.1f} 分钟 / "
          f"{counts.get('assets', 0)} 个素材）")

    times: dict[str, float] = {}
    fails: list[str] = []
    render_out = ""

    if not a.render_only:
        dt, out, rc = step("edl", [sys.executable, str(HERE / "make_edl.py"),
                                   "--project", str(proj), "--plan", str(plan),
                                   "--library", str(lib)])
        times["edl"] = dt
        if rc:
            fails.append("edl")
        dt, out, rc = step("纸面稿", [sys.executable, str(HERE / "render_paper_edit.py"),
                                     "--project", str(proj), "--library", str(lib)])
        times["paper"] = dt

    dt, render_out, rc = step("渲染", [sys.executable, str(HERE / "render_film.py"),
                                       "--project", str(proj), "--library", str(lib)])
    times["render"] = dt
    if rc:
        fails.append("render")

    dt, _, rc = step("验收", [sys.executable, str(HERE / "verify_film.py"),
                              "--project", str(proj)])
    times["verify"] = dt

    decisions = proj / "decisions.md"
    if decisions.exists():
        human = sum(1 for ln in decisions.read_text(encoding="utf-8").splitlines()
                    if ln.strip().startswith("|") and "---" not in ln
                    and not ln.strip().startswith("| 时间"))
    else:
        human = 0

    # 缓存命中：render_film 每段打一行 [全部命中缓存]，全段命中才是「第二次跑」的真实成本
    hit = render_out.count("[全部命中缓存]")
    edl = json.loads((proj / "edl.json").read_text())
    nseg = len(edl.get("segments") or [])
    st = film_stats(proj)
    machine = round(sum(times.values()) / 60, 2)

    rec = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "project": proj.name,
        "library": lib.name,
        "library_min": round(lib_min, 2),
        "machine_min": machine,
        "times_s": times,
        "human_touches": human,
        "segments": nseg,
        "cached_segments": hit,
        "all_cached": bool(nseg) and hit >= nseg,
        "render_only": a.render_only,
        **st,
    }
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with hist_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 对着 BRIEF 的产线指标判：这里判的是「产线」，不是「片子」
    rows: list[tuple[str, str, str, str]] = []
    if lib_min:
        per60 = machine / lib_min * 60
        rows.append(("每 60 分钟素材的机器用时（仅 edl→出片）", f"{per60:.1f} 分钟",
                     "green" if per60 <= 45 else "red",
                     "指标 ≤45 分钟（BRIEF · 出片效率）"))
    rows.append(("人工确认次数", f"{human} 次", "green" if human <= 2 else "yellow",
                 "指标 ≤2 次（载体是文字稿）；计入 `decisions.md` 全部条目，"
                 "含修补类指令，不全是剪辑确认"))
    if nseg:
        rows.append(("段级缓存命中", f"{hit}/{nseg} 段",
                     "green" if hit >= nseg else "yellow",
                     "全命中＝只重渲受影响的段；改动前改后并排看才知道返工成本"))
    if st.get("film_s"):
        rows.append(("成片时长", f"{st['film_s']:.2f}s", "green", f"{st.get('shots', 0)} 个镜头"))
    for f in fails:
        rows.append((f, "失败", "red", "这一步没跑通，上面的时间不算数"))

    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    prev = []
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines()[:-1]:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["project"] == proj.name and not r["render_only"]:
                prev.append(r)
    last = prev[-1] if prev else None
    if last:
        d = machine - last["machine_min"]
        rows.append(("对比上一次同类跑", f"{d:+.2f} 分钟",
                     "green" if d <= 0 else "yellow",
                     f"上次 {last['machine_min']:.2f} 分钟 @ {last['at']}"))

    lines = [f"# 产线基准 · {proj.name}", "",
             f"- {rec['at']} · 库 `{lib.name}`（{lib_min:.1f} 分钟 / "
             f"{counts.get('assets', 0)} 个素材）· {nseg} 段",
             f"- 机器用时合计 **{machine:.2f} 分钟**"
             + (f"（每 60 分钟素材 {machine / lib_min * 60:.1f} 分钟）" if lib_min else ""),
             f"- 人工确认 **{human} 次** · 缓存命中 **{hit}/{nseg}** 段", "",
             "| 指标 | 实测 | 判定 | 说明 |", "|---|---|---|---|"]
    for name, val, lvl, note in rows:
        lines.append(f"| {name} | {val} | {icon[lvl]} | {note} |")
    lines += ["", "| 步骤 | 秒 |", "|---|---|"]
    for k, v in times.items():
        lines.append(f"| {k} | {v:.1f} |")
    lines += ["", "## 这份基准量的是哪一段",
              "",
              "上表时钟 = `make_edl → 纸面稿 → render_film → verify_film`，也就是"
              "**「决定已经写好了，到出片」这一段**。它证明的是改完再跑的代价，"
              "不是从一堆新素材到成片的代价。",
              "",
              "没算在里面、也还没被量过的：",
              "",
              "- **A 段索引**：新素材进库要多久（`l0_seconds` 字段 2026-11-07 才加，"
              "第一遍全库跑的老记录全是 null，基线永久缺失）。要补只能重新冷跑一个库。",
              "- **人写 plan / 读 paper-edit 的时间**：这是流水线里最大的一块人工，脚本看不见。",
              "- **盲测与改稿的往返**：按轮次算，不按分钟算。",
              "",
              "所以 BRIEF 那条「60 分钟素材 → 机器 ≤45 分钟端到端」，用这张表**证不了**。"
              "能证明它的只有一件事：拿一块新库，从零跑到成片，计时——那需要第二块素材库。",
              "",
              "> 这张表量的是**产线**：同样的机器和素材，下一次要不要重来一遍。"
              "片子的好坏不在这儿判（那是 `verify_film.py` 和盲测的事）。"]
    (proj / "bench.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    red = sum(1 for r in rows if r[2] == "red")
    print(f"产线基准：机器 {machine:.2f} 分钟 · 人工 {human} 次 · 缓存 {hit}/{nseg} 段"
          + (f" · 每 60 分钟素材 {machine / lib_min * 60:.1f} 分钟" if lib_min else ""))
    for name, val, lvl, note in rows:
        if lvl != "green":
            print(f"  {icon[lvl]} {name} {val}  {note}")
    print(f"→ {proj / 'bench.md'}（历史：{hist_path.relative_to(lib)}）")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
