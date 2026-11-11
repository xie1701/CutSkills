#!/usr/bin/env python3
"""CutSkills · golden 回归：加新能力之前，先证明旧样片没退化。

这是从软件工程借来的一招：golden file 测试。我们其实早就有 19 项像素断言，
但一直只拿它验「今天这条片」，从没拿它验「我改了这个脚本之后，上周那几条还绿吗」。
剪片的人不做这件事，所以他们永远不知道自己是变好了还是变差了。

基线存 `references/golden-baseline.json`（进 git），任何一条样片掉绿就是回归。

用法：
  golden.py --init                      # 把现在的读数记成基线
  golden.py                             # 跑一遍，和基线对账
  golden.py --only proj79               # 只跑一条
  golden.py --add <项目目录>            # 把新项目加进清单
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = ROOT / "references" / "golden-baseline.json"


def load() -> dict:
    if BASE.exists():
        return json.loads(BASE.read_text())
    return {"updated": None, "samples": {}}


def run_one(proj: Path) -> dict:
    r = subprocess.run([sys.executable, str(HERE / "verify_film.py"), "--project", str(proj)],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"验收：🟢\s*(\d+)\s*·\s*🟡\s*(\d+)\s*·\s*🔴\s*(\d+)", out)
    if not m:
        return {"error": out.strip().splitlines()[-1][:200] if out.strip() else "没有输出"}
    return {"green": int(m.group(1)), "yellow": int(m.group(2)), "red": int(m.group(3)),
            "exit": r.returncode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", action="append", default=[], help="要纳入的项目目录，可多次")
    ap.add_argument("--add", action="append", default=[], help="加进清单（写进基线文件）")
    ap.add_argument("--only", default=None)
    ap.add_argument("--init", action="store_true", help="用当前读数覆盖基线")
    a = ap.parse_args()

    base = load()
    for p in a.add:
        pth = Path(p).expanduser().resolve()
        base["samples"][pth.name] = {"path": str(pth)}
        print(f"＋ {pth.name} → {pth}")

    samples = {k: v for k, v in base["samples"].items() if not a.only or a.only in k}
    if not samples:
        print("清单是空的。先 --add 一个项目，或用 --project 临时跑一条。")
        return 2

    results, fails = {}, []
    for slug, meta in samples.items():
        proj = Path(meta["path"])
        if not (proj / "out" / "film-hf.mp4").exists():
            results[slug] = {"error": "没有成片"}
            fails.append(f"{slug}: 没有成片（{proj / 'out' / 'film-hf.mp4'}）")
            continue
        cur = run_one(proj)
        results[slug] = cur
        want = meta.get("green")
        if cur.get("error"):
            fails.append(f"{slug}: {cur['error']}")
        elif want is None:
            print(f"  {slug}: 🟢 {cur['green']} · 🟡 {cur['yellow']} · 🔴 {cur['red']}（还没有基线）")
        else:
            # 基线里要连红灯一起记：以前只存 green，于是「绿灯涨了、但多出一条红」
            # 会被打印成「绿灯从 13 掉到 23」——数字往上走，话往下说，谁看都懵。
            want_red = int(meta.get("red") or 0)
            ok_green = cur["green"] >= want
            ok_red = cur["red"] <= want_red
            status = "OK" if (ok_green and ok_red) else "REGRESSED"
            print(f"  {slug}: 🟢 {cur['green']}（基线 {want}）· 🟡 {cur['yellow']} · "
                  f"🔴 {cur['red']}（基线 {want_red}）  {status}")
            if not ok_green:
                fails.append(f"{slug}: 绿灯 {want} → {cur['green']}（掉了 {want - cur['green']} 条）")
            if not ok_red:
                fails.append(f"{slug}: 红灯 {want_red} → {cur['red']}（多了 {cur['red'] - want_red} 条）")

    if a.init:
        for slug, cur in results.items():
            if "green" in cur:
                base["samples"][slug]["green"] = cur["green"]
                base["samples"][slug]["red"] = cur["red"]
        base["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        BASE.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"基线已更新 → {BASE}")
    elif a.add:
        BASE.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n回归：{len(samples)} 条样片 · {'FAIL' if fails else 'OK'}")
    for f in fails:
        print(f"  ✗ {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
