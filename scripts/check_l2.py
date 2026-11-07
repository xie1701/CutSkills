#!/usr/bin/env python3
"""CutSkills · L2 记录自检。

L2 是唯一手写的部分，所以它最容易出现「记录和素材对不上」的错：文件名写的是 A，
内容判的是 B。这种错特别隐蔽——合并进索引照样成功，改出来的名字却是别人的内容。
柒造这一库就真出过：`材料搅拌` 和 `地面涂刷` 两支素材的名字互换，两边的 L2 记录
都「有理由、有摘要」，只有对着素材本身看才发现判的是隔壁那条。

所以每次写完 L2、合并之前，先跑一遍这个：

  check_l2.py --library "<素材库>"

它不判断内容对不对（那要靠人/大模型看画面），只检查**结构和归属**：
记录文件名里的哈希、记录里的 asset、这一段的时长范围，三者是不是同一支素材。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SHOTS = {"close", "medium", "wide", "detail", "graphic", "pov", "screen", "unknown"}
GRADES = {"must-use", "backup", "do-not-use"}
FIELDS = ("asset", "in", "out", "shot", "people", "action", "text_on_screen",
          "tag", "summary", "grade", "why", "confidence", "flags")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    lib = Path(a.library).expanduser().resolve()
    idx = lib / "_cutskills" / "index.json"
    if not idx.exists():
        print(f"还没建索引：{idx}", file=sys.stderr)
        return 2
    assets = json.loads(idx.read_text())["assets"]
    by_id = {x["id"]: x for x in assets}
    by_alias = {al: x for x in assets for al in (x.get("aliases") or [])}

    errs, warns, n = [], [], 0
    for f in sorted((lib / "_cutskills" / "l2").glob("*.json")):
        n += 1
        try:
            r = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            errs.append(f"{f.name}：JSON 读不出（{e}）")
            continue
        miss = [k for k in FIELDS if k not in r]
        if miss:
            warns.append(f"{f.name}：缺字段 {', '.join(miss)}")

        # 归属：文件名里的哈希 == 该素材的 legacy_key（或当前路径哈希）
        rec_asset = r.get("asset") or ""
        a_rec = by_id.get(rec_asset) or by_alias.get(rec_asset)
        if not a_rec:
            errs.append(f"{f.name}：asset 不在索引里（{rec_asset}）")
            continue
        stem_hash = f.stem.rsplit("-", 1)[0]
        own = {a_rec.get("legacy_key"), a_rec.get("uid")}
        if stem_hash not in {x for x in own if x}:
            errs.append(f"{f.name}：文件名哈希 {stem_hash} 与素材 {a_rec['id']} 的 "
                        f"legacy_key={a_rec.get('legacy_key')} 不一致——这条判断可能记在了别人头上")

        # 范围：in/out 必须在素材时长内、且 in < out（照片除外）
        dur = a_rec.get("duration") or (a_rec.get("spec") or {}).get("duration") or 0
        try:
            i, o = float(r.get("in", 0)), float(r.get("out", 0))
        except (TypeError, ValueError):
            errs.append(f"{f.name}：in/out 不是数字")
            continue
        if a_rec["kind"] in {"video", "audio"}:
            if o <= i:
                errs.append(f"{f.name}：out({o}) 不大于 in({i})")
            if dur and o > dur + 0.5:
                errs.append(f"{f.name}：out({o}) 超出素材时长 {dur:.1f}s")

        if r.get("shot") not in SHOTS:
            warns.append(f"{f.name}：shot={r.get('shot')} 不在枚举里")
        if r.get("grade") not in GRADES:
            errs.append(f"{f.name}：grade={r.get('grade')} 不在枚举里")
        tag = str(r.get("tag") or "")
        if not (2 <= len(tag) <= 8):
            warns.append(f"{f.name}：tag「{tag}」应该是 2–8 个字的名词短语")
        try:
            conf = float(r.get("confidence", -1))
            if not 0 <= conf <= 1:
                warns.append(f"{f.name}：confidence={conf} 超出 0–1")
        except (TypeError, ValueError):
            warns.append(f"{f.name}：confidence 不是数字")

    print(f"L2 记录 {n} 条 · error {len(errs)} warning {len(warns)}")
    if not a.quiet:
        for e in errs:
            print(f"  error: {e}")
        for w in warns:
            print(f"  warn : {w}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
