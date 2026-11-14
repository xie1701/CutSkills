#!/usr/bin/env python3
"""看板数据的不变式检查，外加「导出 XML 后应该看到什么」的对照表。

为什么需要它：看板本体（引擎、导出 XML、画布）跑在浏览器里，脚本碰不到。能保证的只有
**喂给引擎的数是真数**。所以这里把引擎要靠的每个数验一遍，再把 XML 里该出现的 clip 表
打出来——人导出之后逐条对，不用凭感觉。

用法：
  python3 scripts/board_check.py --project <项目目录> [--xml] [--strict]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
VENDOR = SKILL / "vendor" / "production-board"
BUILD = VENDOR / "scripts" / "build_board.py"

TRACK_OF = {  # mode → 时间轴轨道（看板引擎的四轨命名）
    "A": "V1", "AO": "V1", "S": "V3", "E": "V3", "T": "V3",
}


def load_board(proj: Path) -> tuple[dict, str]:
    js = (proj / "board" / "board.js").read_text()
    body = js[js.index("=") + 1:].rstrip().rstrip(";")
    return json.loads(body), js


def checks(board: dict, proj: Path, tl: dict) -> list[tuple[str, str, str]]:
    """返回 [(级别, 项, 说明)]，级别 error / warn / ok。"""
    out: list[tuple[str, str, str]] = []
    shots = board.get("shots") or []
    meta = board.get("meta") or {}

    # 1. 结构
    ids = [s.get("id") for s in shots]
    dup = {i for i in ids if ids.count(i) > 1}
    out.append(("error" if dup else "ok", "镜号唯一", f"{len(ids)} 镜" + (f"，重复 {sorted(dup)}" if dup else "")))
    chs = {c["id"] for c in board.get("chapters") or []}
    bad_ch = [s["id"] for s in shots if s.get("ch") not in chs]
    out.append(("error" if bad_ch else "ok", "章节引用", f"{len(chs)} 章" + (f"，悬空 {bad_ch}" if bad_ch else "")))
    modes = set(board.get("modes") or {})
    bad_mode = [s["id"] for s in shots if s.get("mode") not in modes]
    out.append(("error" if bad_mode else "ok", "版式引用", f"{sorted(modes)}" + (f"，悬空 {bad_mode}" if bad_mode else "")))

    # 2. 时长：**这是最关键的一条**——看板每镜必须写 fixed，且加起来等于真时间轴
    no_fixed = [s["id"] for s in shots if not isinstance(s.get("fixed"), (int, float))]
    out.append(("error" if no_fixed else "ok", "每镜都有 fixed（真秒数）",
                "全有" if not no_fixed else f"缺 {no_fixed}"))
    total_board = round(sum(float(s.get("fixed") or 0) for s in shots)
                        + sum(float(s.get("gap") or 0) for s in shots), 3)
    total_tl = round(float(tl.get("duration_s") or 0), 3)
    d = round(abs(total_board - total_tl), 3)
    out.append(("ok" if d <= 0.05 else "error", "看板总时长 = timeline.duration_s",
                f"看板 {total_board}s / 时间轴 {total_tl}s（差 {d}s）"))

    # 3. 两边的格数
    n_video = sum(1 for e in tl.get("elements") or []
                  if (e.get("track") or "video") != "overlay" and e.get("kind") != "silence")
    out.append(("ok" if n_video == len(shots) else "error", "一格一镜（silence 算 gap 不算镜）",
                f"时间轴 {n_video} 格 / 看板 {len(shots)} 镜"))

    # 3b. clips.ov 是时间轴 overlay 的投影，不许变成第二份真值（T-010）
    # 写：board_from_timeline 投影出来；读：看板引擎排 V4、board_patch 回写。
    # 成片渲染只认 timeline 的 overlay 元素（唯一真值），所以两边必须逐块对上；
    # 对不上就是「看板看到的时刻」和「渲染用的时刻」脱钩，预演就不再≈成片。
    t, starts, n_vid = 0.0, {}, 0
    for e in tl.get("elements") or []:
        dur = ((float(e["out"]) - float(e["in"])) if e.get("kind") == "clip"
               else float(e.get("dur") or 0.0))
        if dur <= 0:
            continue
        if (e.get("track") or "video") == "overlay":
            # 叠加层不在主轨上：它的 dur 绝不许累加进线性时间（T-010 复核打回项）。
            # 这里必须和 board_from_timeline.py 的 `if overlay: continue` 一致。
            continue
        if (e.get("kind") or "clip") != "silence":
            n_vid += 1
            # 缺省镜号按「第几个视频格」补，和 board_from_timeline 的 enumerate(video_rows, 1) 一致
            starts[f"S{int(e.get('n') or n_vid):02d}"] = round(t, 3)
        t = round(t + dur, 3)
    ovs = [e for e in tl.get("elements") or []
           if (e.get("track") or "video") == "overlay" and float(e.get("dur") or 0.0) > 0]
    drift, claimed, n_proj = [], set(), 0
    for s in shots:
        c = (s.get("clips") or {}).get("ov")
        if not c:
            continue
        n_proj += 1
        st = starts.get(s["id"])
        if st is None:
            drift.append(f"{s['id']} 不在时间轴")
            continue
        want_at = round(st + float(c[0]), 3)
        want_dur = round(float(c[1]), 3)
        hit = None
        for i, o in enumerate(ovs):
            if (abs(float(o.get("at") or 0) - want_at) <= 0.05
                    and abs(float(o.get("dur") or 0) - want_dur) <= 0.05):
                hit = i
                break
        if hit is None:
            near = min(ovs, key=lambda o: abs(float(o.get("at") or 0) - want_at), default=None)
            drift.append(f"{s['id']} 要 {want_at:.2f}s/{want_dur:.2f}s" + (
                f"，最近 #{near.get('n')} {float(near.get('at') or 0):.2f}s/"
                f"{float(near.get('dur') or 0):.2f}s" if near else "，时间轴上没有叠加层"))
        else:
            claimed.add(hit)
    orphan = [f"#{o.get('n')} {float(o.get('at') or 0):.2f}s/{float(o.get('dur') or 0):.2f}s"
              for i, o in enumerate(ovs) if i not in claimed]
    if drift:
        out.append(("error", "clips.ov 与时间轴叠加层对账", f"对不上：{drift[:4]}"))
    elif orphan:
        out.append(("error", "clips.ov 与时间轴叠加层对账",
                    f"时间轴有、看板没投影 {orphan[:4]}（重跑 board_from_timeline）"))
    else:
        out.append(("ok", "clips.ov 与时间轴叠加层对账", f"{n_proj} 块投影 = {len(ovs)} 个叠加层"))

    # 3c. 看板 clips 只认 ov：br / rec / a 在本管线没有落点（T-011）
    # 回写链只有一条视频轨 + 一层 overlay；br 是看板引擎的整屏 B-roll 轨道，
    # 既没有第二视频层，也没有素材指针（board 数据里只有 offset/dur，没有 asset）。
    # 所以「看板上拖得动」不等于「本管线落得了」，在检查层就拦住，别让它悄悄消失。
    bad_clips = [f"{s['id']}:{k}" for s in shots
                 for k in (s.get("clips") or {}) if k != "ov"]
    out.append(("error" if bad_clips else "ok",
                "clips 只认 ov（br 无第二视频层/素材指针）",
                "干净" if not bad_clips
                else f"多出 {bad_clips[:6]}——看板可拖，但本管线只能回写 ov（T-011）"))

    # 4. 分镜元素：坐标在 0–100，字不压底部 12% 字幕区
    over, low = [], []
    for s in shots:
        for it in s.get("m") or []:
            nums = [x for x in it[1:] if isinstance(x, (int, float))]
            if any(x < 0 or x > 100 for x in nums):
                over.append(f"{s['id']}:{it[0]}")
            if it[0] in ("ttl", "txt", "tag", "note", "card") and len(it) > 2:
                y = it[2] if it[0] in ("ttl", "txt") else (it[2] if it[0] in ("tag", "note", "card") else None)
                if isinstance(y, (int, float)) and y > 88:
                    low.append(f"{s['id']}:{it[0]}@{y}")
    out.append(("error" if over else "ok", "元素坐标 0–100",
                "全内" if not over else f"越界 {over[:6]}"))
    out.append(("warn" if low else "ok", "文字不进底部 12%（字幕区）",
                "干净" if not low else f"偏低 {low[:6]}"))

    # 5. 口播：每镜都得有原文，且原话不改
    empty = [s["id"] for s in shots if not str(s.get("vo") or "").strip()]
    out.append(("warn" if empty else "ok", "每镜有口播原文", "全有" if not empty else f"空 {empty}"))

    # 6. 音效名：必须是 CC0 库里真有的（库路径走 config.json，不写死任何人的家目录）
    from cc0_lib import SFX_LIB  # noqa: E402
    cat = SFX_LIB / "catalog.csv"
    if cat.exists():
        import csv
        names = set()
        with cat.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                # 库里认的名字是 id 列（pop-medium-in）；file 是路径，name 是英文展示名
                names.add(str(row.get("id") or ""))
                names.add(Path(str(row.get("file") or "")).stem)
                names.add(str(row.get("name") or ""))
        missing = sorted({x for s in shots for x in str(s.get("sfx") or "").split("；")
                          if x and "不出声" not in x and x not in names})
        out.append(("error" if missing else "ok", "音效名在 CC0 库里",
                    "全命中" if not missing else f"库里没有 {missing}"))
    else:
        out.append(("warn", "音效名在 CC0 库里", f"库不在 {cat}，跳过"))

    # 7. 画布连线：风格 id 得在 styles 里
    sids = {s.get("id") for s in board.get("styles") or []}
    bad_canvas = [c.get("style") for c in board.get("canvas") or [] if c.get("style") not in sids]
    out.append(("warn" if bad_canvas else "ok", "画布连线指向本片风格",
                "一致" if not bad_canvas else f"styles 里没有 {bad_canvas}"))

    # 8. XML 要用的元信息
    fps = meta.get("fps")
    ok_meta = bool(fps) and "/" in str(meta.get("ar") or "") or str(meta.get("ar") or "") in ("9:16", "16:9", "4:3", "3:4", "1:1")
    out.append(("ok" if ok_meta else "error", "XML 元信息",
                f"{meta.get('ar')} {meta.get('res')} {fps}fps"))
    return out


def xml_table(board: dict) -> str:
    """XML 里该出现的 clip 表。人导出 XML 之后拿这张表逐条对。"""
    meta = board["meta"]
    fps = int(meta.get("fps") or 30)
    t = 0.0
    lines = [f"# 导出 XML 应有的内容 · {meta.get('project')} · {meta.get('ar')} "
             f"{meta.get('res')} {fps}fps · 序列时长 {sum(float(s.get('fixed') or 0) + float(s.get('gap') or 0) for s in board['shots']):.2f}s",
             "", "| 起点 | 时长 | 帧 | 轨道 | clip 名 | 口播（标记点） |", "|---|---|---|---|---|---|"]
    for s in board["shots"]:
        t = round(t + float(s.get("gap") or 0), 3)
        dur = float(s.get("fixed") or 0)
        name = f"{s['id']}_{'A' if s['mode'] in ('A', 'AO') else 'BR'}.mp4"
        vo = str(s.get("vo") or "")[:18]
        lines.append(f"| {t:.2f}s | {dur:.2f}s | {round(t * fps)} | {TRACK_OF.get(s['mode'], 'V3')} "
                     f"| {name} | {vo} |")
        t = round(t + dur, 3)
    lines += ["", "对账方法：达芬奇/Premiere 导入 XML → 看每个 clip 的起点和时长和上表一致 → "
                  "共 " + str(len(board["shots"])) + " 个画面 clip。"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--xml", action="store_true", help="打出 XML 应有的 clip 表")
    ap.add_argument("--md", action="store_true", help="写成 board/check-report.md")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    board, js = load_board(proj)
    tl = json.loads((proj / "timeline.json").read_text())

    rows = checks(board, proj, tl)
    n_err = sum(1 for r in rows if r[0] == "error")
    n_warn = sum(1 for r in rows if r[0] == "warn")
    icon = {"ok": "🟢", "warn": "🟡", "error": "🔴"}
    lines = [f"# 看板检查 · {proj.name}", "",
             f"结论：{'🔴 有硬伤' if n_err else '🟢 通过'} · 🔴{n_err} 🟡{n_warn} "
             f"🟢{len(rows) - n_err - n_warn}", "", "| | 项 | 读数 |", "|---|---|---|"]
    lines += [f"| {icon[lvl]} | {name} | {msg} |" for lvl, name, msg in rows]

    # 往返无损：从 HTML 里抽回来的数据必须和 board.js 一字不差
    html = proj / "board" / "board.html"
    if html.exists() and BUILD.exists():
        r = subprocess.run([sys.executable, str(BUILD), "--extract", str(html)],
                           capture_output=True, text=True)
        same = r.stdout.strip() == js.strip()
        lines += ["", f"往返无损（board.html 抽回 == board.js）：{'🟢 一致' if same else '🔴 不一致'}"]
        if not same:
            n_err += 1

    if a.xml:
        lines += ["", "---", "", xml_table(board)]
    text = "\n".join(lines) + "\n"
    if a.md:
        (proj / "board" / "check-report.md").write_text(text)
        print(f"已写 {proj / 'board' / 'check-report.md'}")
    print(text)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
