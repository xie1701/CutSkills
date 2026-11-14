#!/usr/bin/env python3
"""把仓库打成可复制的分发包：CutSkills-<日期>.zip

包 = `git archive HEAD`（只含已跟踪文件，天然不带渲染中间物与 config.json）
  + 已含 SKILL.md（AI 工位说明书）、GETTING-STARTED.md、config.example.json（打完自检）

用法：
  python3 scripts/pack_dist.py            # 打到 ~/Downloads/CutSkills-dist/
  python3 scripts/pack_dist.py --out DIR

打完自检：zip 能解开、SKILL.md 与 GETTING-STARTED.md 在包根、
包内 grep 不到任何写死的家目录路径。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "CutSkills-dist"))
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    name = f"CutSkills-{date.today():%Y%m%d}"
    zpath = out / f"{name}.zip"

    # 1) git archive → 干净的已跟踪内容
    r = subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "HEAD"],
                       capture_output=True)
    if r.returncode != 0:
        print(r.stderr.decode(), file=sys.stderr)
        return 2
    zpath.write_bytes(r.stdout)

    # 2) 关键文件校验（SKILL.md 工位说明书、上手页、配置样例都随 git 走，不另拼）
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        for must in ("SKILL.md", "GETTING-STARTED.md", "config.example.json"):
            if not any(n == must for n in names):
                print(f"包里缺 {must}，先提交它", file=sys.stderr)
                return 2

    # 3) 自检：解开、查关键文件、查私有路径泄漏
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(td)
            names = z.namelist()
        root = Path(td)
        missing = [f for f in ("SKILL.md", "GETTING-STARTED.md",
                               "config.example.json", "scripts/runtime.sh",
                               "scripts/doctor.py", "deps.json") if not (root / f).exists()]
        if missing:
            print(f"包里缺：{'、'.join(missing)}", file=sys.stderr)
            return 2
        leak = subprocess.run(["grep", "-rl", "/Users/jerry", str(root / "scripts")],
                              capture_output=True, text=True)
        if leak.stdout.strip():
            print(f"scripts 里仍有私有路径泄漏：\n{leak.stdout}", file=sys.stderr)
            return 1
        n_files = len([n for n in names if not n.endswith("/")])
        print(f"✅ {zpath}")
        print(f"   {n_files} 个文件 · {zpath.stat().st_size / 1e6:.1f} MB")
        print(f"   上手：解开后读 GETTING-STARTED.md（或整包丢给 AI 工位）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
