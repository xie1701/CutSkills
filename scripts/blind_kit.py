#!/usr/bin/env python3
"""效果评估（方案 P4）：同一格里「旧卡 / 纯实拍 / AI 场景」三版盲测套件。

为什么要盲：谁做的谁看得出差别，这不叫评估。观众不知道哪版是谁做的、
也不知道我们在比什么，才能回答「哪一版让人更信」。

流程（本脚本跑完，人只负责看）：
  1. 备份成片与时间轴 → 依次把那一格改成三版（旧模板 / 去掉包装 / 现在这版）各自整片重渲；
  2. 从每条片里**同窗同长**裁出对比条（默认就是该格窗口）；
  3. 用固定种子把三版随机标成 甲/乙/丙（`key.json` 是唯一的对照表，别给观众看）；
  4. 生成 `问卷.md` 与 `评分表.csv`——问题与判据在跑之前就写死，跑完不许改。

用法：
    python3 scripts/blind_kit.py --project <项目> --n 15 --out <项目>/ab/p4
    python3 scripts/blind_kit.py --project <项目> --n 15 --dry-run     # 只算方案不渲

三版定义（改这里就是改实验设计，改完要重跑）：
    card   旧模板：把该格换回 `--card-template`（默认 compare）的文字卡
    real   纯实拍：把该格整块去掉，让下面真实的画面自己说话
    scene  现在这版（代码场景）
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 问卷与判据：跑之前写死（方案 P4 的要求），跑完不许改。
QUESTIONS = [
    ("看懂", "这一段在说什么？（1 完全不懂 / 5 一眼就懂）"),
    ("说服", "这一段让你更信「比的对象换了，结论就变了」吗？（1 更不信 / 5 更信）"),
    ("专业", "像不像一个做过片子的团队做的？（1 像学生作业 / 5 像正片）"),
    ("出戏", "有没有哪一刻觉得「这是模板 / PPT / 特效挡路」？（1 一直出戏 / 5 完全没出戏）"),
    ("节奏", "节奏拖不拖？（1 太拖 / 5 太快但跟得上）"),
]
DECISION = """\
**判据（跑之前定，跑完不改）**：天然赢家要同时满足
① 「说服」均值比旧卡版高 ≥ 0.5 分；② 「出戏」均值 ≥ 3.5；③ 没有一位观众在「看懂」上给 ≤ 2。
人数 ≥ 5；少于 5 人只报读数、不做结论。三版随机标 甲/乙/丙，对照表在 `key.json`。
"""


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def ffmpeg() -> str:
    return os.environ.get("CUTSKILLS_FFMPEG") or "ffmpeg"


def cut(src: Path, t0: float, dur: float, dst: Path) -> bool:
    cmd = [ffmpeg(), "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}",
           "-i", str(src), "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
           "-c:a", "aac", "-b:a", "192k", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    裁对比条失败：{r.stderr.strip()[-300:]}")
        return False
    return True


def patch(tl: dict, n: int, variant: str, card_template: str) -> dict:
    """把第 n 格改成某一版。返回新 timeline（不改原文件）。"""
    t2 = json.loads(json.dumps(tl))
    els = t2["elements"]
    i = next((k for k, e in enumerate(els) if int(e.get("n") or 0) == n), None)
    if i is None:
        raise SystemExit(f"timeline 里没有 #n={n}")
    cur = els[i]
    if variant == "scene":
        return t2                                  # 原样：现在就是这版
    if variant == "real":
        els.pop(i)                                 # 整块去掉，露出底下的实拍
        return t2
    if variant == "card":
        els[i] = {
            "n": cur["n"], "kind": "gen", "track": "overlay",
            "template": card_template,
            "at": cur.get("at"), "dur": cur.get("dur"),
            "props": json.loads(json.dumps(cur.get("props") or {})),
            "why": "盲测对照：旧模板（文字卡）",
            "axis": cur.get("axis") or "A",
        }
        return t2
    raise SystemExit(f"不认识的版本：{variant}")


def main() -> int:
    ap = argparse.ArgumentParser(description="三版盲测套件（旧卡 / 纯实拍 / AI 场景）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--card-template", default="compare")
    ap.add_argument("--variants", default="card,real,scene")
    ap.add_argument("--quality", default="looks")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser()
    tl_path = proj / "timeline.json"
    tl = json.loads(tl_path.read_text())
    el = next((e for e in tl["elements"] if int(e.get("n") or 0) == a.n), None)
    if el is None:
        raise SystemExit(f"timeline 里没有 #n={a.n}")
    t0, dur = float(el.get("at") or 0), float(el.get("dur") or 4)
    out = Path(a.out or (proj / "ab" / "p4")).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    vs = [v.strip() for v in a.variants.split(",") if v.strip()]
    print(f"窗口 {t0:.2f}–{t0 + dur:.2f}s · {len(vs)} 版：{', '.join(vs)}")
    if a.dry_run:
        for v in vs:
            print(f"  {v}: {'原样' if v == 'scene' else ('去掉该格' if v == 'real' else a.card_template + ' 文字卡')}")
        print(f"  → 会写到 {out}")
        return 0

    film = proj / "out" / "film-hf.mp4"
    if not film.exists():
        raise SystemExit("没有成片，先渲一遍再跑盲测")
    keep_film = out / "_film-keep.mp4"
    keep_tl = out / "_timeline-keep.json"
    shutil.copy2(film, keep_film)
    shutil.copy2(tl_path, keep_tl)

    made: dict[str, Path] = {}
    try:
        for v in vs:
            dst = out / f"{v}.mp4"
            if v == "scene" and film.exists():
                # 现在这版不必重渲：从**备份的**原片裁。不能读 film——循环里 film
                # 已经被上一版覆盖了（第一次跑就把 scene 和 real 裁成了同一条）。
                if cut(keep_film, t0, dur, dst):
                    made[v] = dst
                    print(f"  {v}: 裁自现有成片")
                continue
            tl_path.write_text(json.dumps(patch(tl, a.n, v, a.card_template),
                                          ensure_ascii=False, indent=1))
            print(f"  {v}: 重渲整片（{a.quality}）…")
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_hf.py"),
                                "--project", str(proj), "--quality", a.quality, "--force"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print("    渲染失败：" + ((r.stdout or "") + (r.stderr or "")).strip()[-400:])
                continue
            shutil.copy2(film, out / f"_film-{v}.mp4")
            if cut(out / f"_film-{v}.mp4", t0, dur, dst):
                made[v] = dst
    finally:
        shutil.copy2(keep_tl, tl_path)        # 时间轴一定要还原，哪怕中途炸了
        shutil.copy2(keep_film, film)
        print("  已还原时间轴与成片")

    # 随机标签：种子钉死，同一格每次跑出的对照表一致（可复核、不可作弊）
    labels = ["甲", "乙", "丙", "丁"][: len(made)]
    rnd = random.Random(sha1(f"{proj.name}:{a.n}:{','.join(sorted(made))}"))
    rnd.shuffle(labels)
    key = {}
    for lab, (v, src) in zip(labels, made.items()):
        dst = out / f"盲测-{lab}.mp4"
        shutil.copy2(src, dst)
        key[lab] = {"variant": v, "file": dst.name, "src": str(src)}
    (out / "key.json").write_text(json.dumps(
        {"project": str(proj), "n": a.n, "window": [round(t0, 3), round(t0 + dur, 3)],
         "labels": key}, ensure_ascii=False, indent=1))

    rows = [["观众编号", "版本"] + [q[0] for q in QUESTIONS]]
    for i in range(1, 6):
        for lab in labels:
            rows.append([f"V{i}", lab] + [""] * len(QUESTIONS))
    with (out / "评分表.csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

    q = ["# 盲测问卷（P4）", "",
         f"窗口：{t0:.2f}–{t0 + dur:.2f}s · 三版同窗同长、同字幕、同响度，只有包装不同。", "",
         "**给观众的话**：三个 4 秒片段，看完各打 1–5 分。不要互相商量，先说第一反应。"
         " 不给任何版本背景（谁知道哪版是哪个，这次评估就废了）。", "",
         "| 编号 | 文件 |", "|---|---|"]
    q += [f"| {lab} | `{key[lab]['file']}` |" for lab in labels]
    q += ["", "## 每题 1–5 分", ""] + [f"{i}. **{n}** — {t}" for i, (n, t) in enumerate(QUESTIONS, 1)]
    q += ["", "## 最后一句（自由填）", "", "哪一版最像一个真实做这行的人做出来的？为什么？", "",
          DECISION]
    (out / "问卷.md").write_text("\n".join(q), encoding="utf-8")
    print(f"→ {out}/问卷.md · 评分表.csv · key.json · {len(made)} 条对比条")
    return 0 if len(made) == len(vs) else 1


if __name__ == "__main__":
    sys.exit(main())
