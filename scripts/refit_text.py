#!/usr/bin/env python3
"""把每一格的 `evidence` 按 ASR 字流重算——**改过剪切就必须重跑这一步**。

为什么单独做一个脚本：`evidence` 是「这一格在说什么」的唯一记录，字幕、大字锚词、
看板的口播原文全从它派生。以前它是手打的（或者从 ASR 里抄一段），于是三件事会同时错：

1. 切开一格之后，两半还留着同一句 evidence（实测 qizao-naming-01 的第 7、8 格一字不差）；
2. 手打的和 ASR 用字不同（「柒造」ASR 写作「7灶」），字幕因此被判「文字对不上声音」，
   17 条里 9 条假红；
3. 卡片的 `on` 词在新窗口里根本不存在了，渲染端只能「沿用默认时机」并打一行警告。

窗口规则和 `subs.py` / `render_film.build_cues` 完全一致（`in-0.02 ~ out-0.02`），
差 40ms 就会出现「字幕上留着已剪掉的话」。

    python3 scripts/refit_text.py --project <项目>
    python3 scripts/refit_text.py --project <项目> --check     # 只报，不写
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_edl import tokens  # noqa: E402
from timeline import resolve_lib  # noqa: E402


def window_text(chars, starts, ends, s_in: float, s_out: float) -> str:
    return "".join(c for c, st in zip(chars, starts)
                   if s_in - 0.02 <= st < s_out - 0.02)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--check", action="store_true", help="只报差异，不写回")
    a = ap.parse_args()
    proj = Path(a.project).expanduser()
    lib = resolve_lib(proj, a.library)
    tl_p = proj / "timeline.json"
    tl = json.loads(tl_p.read_text())

    cache: dict[str, tuple] = {}
    changed = 0
    for el in tl["elements"]:
        aud = el.get("audio") or {}
        aid = aud.get("asset") or (el.get("asset") if el.get("kind") == "clip" else None)
        if not aid:
            continue
        s_in = float(aud.get("in", el.get("in") or 0))
        s_out = float(aud.get("out", el.get("out") or 0))
        if aid not in cache:
            cache[aid] = tokens(lib, aid)
        txt = window_text(*cache[aid], s_in, s_out)
        old = el.get("evidence") or ""
        if txt != old:
            changed += 1
            print(f"  #{el['n']} {s_in:.2f}-{s_out:.2f}\n    旧：{old}\n    新：{txt}")
            if not a.check:
                el["evidence"] = txt
                el["chars"] = len(txt)
        on = str((el.get("card") or {}).get("on") or "")
        if on and on not in txt:
            print(f"  ！#{el['n']} 大字锚词「{on}」不在这一格的原话里：{txt}", file=sys.stderr)

    if not a.check and changed:
        tl_p.write_text(json.dumps(tl, ensure_ascii=False, indent=1) + "\n")
        print(f"→ 改了 {changed} 格（{tl_p}）")
    else:
        print(f"{'要改' if changed else '不用改'} {changed} 格")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
