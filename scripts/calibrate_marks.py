#!/usr/bin/env python3
"""三幕时长标定：把 B 轴场景格的 marks 从「拍的比例」变成「跟着旁白走的」。

Audio-First 的 B 轴延伸：每一幕的换幕点，应该落在旁白念到那一幕锚词的时刻，
而不是拍脑袋的 0.30 / 0.62。锚词由时间轴**显式声明**（`anchors`，与 marks
一一对应），本工具只做三件事：

  1. 从 `_cutskills/cache/asr/` 的字级时间戳里找每个锚词的第一次出现
     （先过词表——「断烧」要纠回「煅烧」再找，否则永远找不到）；
  2. 换算成格内占比 frac = (t − audio.in) / (audio.out − audio.in)；
  3. 逐个判收：frac ≥ 前一幕 + 0.05 才采纳；锚词没找到、或出现在前一幕
     之前（叙述顺序与工序顺序冲突），**保持原值并在报告里如实说原因**——
     不悄悄钳位凑顺序，那是在掩盖素材本身的问题。

报告里每一行都说清「为什么是这个数」。要改素材、换旁白段落，那是内容决策，
不属于这个工具。

用法：
  python3 scripts/calibrate_marks.py --project qizao-lime-baxis            # 只看报告
  python3 scripts/calibrate_marks.py --project qizao-lime-baxis --write    # 写回 timeline.json

项目定位：`--root`（默认环境变量 CUTSKILLS_PROJECTS，再默认
/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects）、`--lib`（素材根，
默认同目录上一级）。退出码：0 = 全部锚词落实；1 = 有锚词没落实（报告已说明）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audio_map import chars_of  # noqa: E402
import lexicon  # noqa: E402

DEF_ROOT = ("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects")
GAP = 0.05  # 两幕之间至少隔 5% 的时长，贴着换幕会看不出过渡


def find_anchor(chars: list[dict], anchor: str, t0: float, t1: float) -> float | None:
    """在 [t0, t1) 的字流里找 anchor 的第一次出现，返回素材绝对秒。"""
    win = [c for c in chars if t0 <= c["s"] < t1]
    text = "".join(c["c"] for c in win)
    idx = text.find(anchor)
    if idx < 0:
        return None
    return float(win[idx]["s"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目 slug（--root 下的目录名）")
    ap.add_argument("--root", default=os.environ.get("CUTSKILLS_PROJECTS", DEF_ROOT))
    ap.add_argument("--lib", default=None, help="素材根目录，默认 --root 的上两级")
    ap.add_argument("--write", action="store_true", help="把采纳的新 marks 写回 timeline.json")
    a = ap.parse_args()

    root = Path(a.root)
    proj = root / a.project
    tl_p = proj / "timeline.json"
    if not tl_p.exists():
        print(f"找不到 {tl_p}")
        return 1
    tl = json.loads(tl_p.read_text())
    lib = Path(a.lib) if a.lib else root.parent.parent

    lex = lexicon.load(lib / "_cutskills")
    chars_cache: dict[str, list[dict]] = {}
    ok_all = True

    for el in tl.get("elements") or []:
        if str(el.get("template") or "") != "scene":
            continue
        marks = el.get("marks") or []
        anchors = el.get("anchors") or (el.get("props") or {}).get("anchors") or []
        if not anchors:
            continue
        au = el.get("audio") or {}
        aid = au.get("asset") or el.get("asset")
        if not aid or len(anchors) != len(marks):
            print(f"格 {el.get('n')}：anchors({len(anchors)}) 与 marks({len(marks)}) "
                  f"数目不一致，跳过——锚词必须与 marks 一一对应")
            ok_all = False
            continue
        t0, t1 = float(au.get("in") or 0.0), float(au.get("out") or 0.0)
        span = t1 - t0
        if span <= 0.5:
            print(f"格 {el.get('n')}：audio 区间无效（{t0}–{t1}），跳过")
            ok_all = False
            continue
        if aid not in chars_cache:
            chars_cache[aid] = chars_of(lib, aid, lex)
        chars = chars_cache[aid]
        print(f"\n格 {el.get('n')}（{el.get('props', {}).get('scene')}，{span:.2f}s）"
              f"  旁白 {aid} {t0:.2f}–{t1:.2f}")
        new_marks, prev_frac = [], 0.0
        for i, (m, anc) in enumerate(zip(marks, anchors), 1):
            t_hit = find_anchor(chars, str(anc), t0, t1)
            old = float(m)
            if t_hit is None:
                new_marks.append(old)
                print(f"  幕{i} 锚「{anc}」旁白里没有 → 保持原值 {old:.4f}")
                ok_all = False
                continue
            frac = max(0.02, min(0.98, (t_hit - t0) / span))
            if frac < prev_frac + GAP:
                new_marks.append(old)
                print(f"  幕{i} 锚「{anc}」出现在 frac {frac:.3f}，早于前一幕 +{GAP}"
                      f"（旁白叙述顺序与工序顺序冲突）→ 保持原值 {old:.4f}")
                ok_all = False
                continue
            new_marks.append(round(frac, 4))
            act = f"  幕{i} 锚「{anc}」@ {t_hit:.2f}s → frac {frac:.4f}"
            print(act + ("（不变）" if abs(frac - old) < 1e-4 else f"（原 {old:.4f}）"))
            prev_frac = frac
        if a.write and new_marks != [float(m) for m in marks]:
            el["marks"] = new_marks

    if a.write:
        tl_p.write_text(json.dumps(tl, ensure_ascii=False, indent=1))
        print(f"\n已写回 {tl_p}")
    else:
        print("\n（报告模式，未写回；要落盘加 --write）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
