#!/usr/bin/env python3
"""把仓库打成可复制的分发包：CutSkills-<日期>.zip

包 = `git archive HEAD`（只含已跟踪文件，天然不带渲染中间物与 config.json）
  − 内部开发史（`docs/`：验收报告、风险台账、多工位协作看板——用户拿到只是噪音）
  + 已含 SKILL.md（AI 工位说明书）、GETTING-STARTED.md、config.example.json（打完自检）

用法：
  python3 scripts/pack_dist.py            # 打到 ~/Downloads/CutSkills-dist/
  python3 scripts/pack_dist.py --out DIR

打完自检：zip 能解开、SKILL.md 与 GETTING-STARTED.md 在包根、
包内不含 docs/、包内 grep 不到任何写死的家目录路径。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# 不进分发包的顶层目录：docs/ 是内部验收与协作史，只对维护者有意义。
# tests/（含 tests/fixtures/ 的回归夹具）必须随包发：selftest.py 与 scripts/test_*.py
# 是交付物的一部分，用户拿到解压包后要能一条命令自检——夹具缺了它们必红。
EXCLUDE_PREFIXES = ("docs/",)


def _restore_modes(zpath: Path, root: Path) -> int:
    """把 zip 里记的执行位补回磁盘（zipfile.extractall 不做这件事）。返回补了几个。"""
    n = 0
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            mode = (info.external_attr >> 16) & 0o777
            if not mode or not (mode & 0o100):
                continue
            f = root / info.filename
            if f.is_file() and not os.access(f, os.X_OK):
                f.chmod(0o755)
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "CutSkills-dist"))
    ap.add_argument("--allow-dirty", action="store_true",
                    help="工作树有未提交改动时仍打包（会打出**旧版**，只用于复现历史包）")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    name = f"CutSkills-{date.today():%Y%m%d}"
    zpath = out / f"{name}.zip"

    # 1) git archive → 干净的已跟踪内容
    # 先拦一个坑：`git archive HEAD` 只含**已提交**的文件。工作树有未提交改动时，
    # 打出来的包会是旧版，而且一路打绿、没人看得出来。这种包发出去最坑。
    _st = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                         capture_output=True, text=True)
    _dirty = [ln for ln in (_st.stdout or "").splitlines() if ln.strip()]
    if _dirty:
        print(f"打不了包：工作树有 {len(_dirty)} 处未提交改动，`git archive HEAD` 会打出旧版。")
        for ln in _dirty[:10]:
            print(f"  {ln}")
        if len(_dirty) > 10:
            print(f"  …另 {len(_dirty) - 10} 处")
        print("先提交（或 `git stash`）再打包；确认就是要打旧版时加 `--allow-dirty`。", file=sys.stderr)
        if not a.allow_dirty:
            return 3
    r = subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "HEAD"],
                       capture_output=True)
    if r.returncode != 0:
        print(r.stderr.decode(), file=sys.stderr)
        return 2

    # 1b) 按白名单外的顶层目录再滤一遍：git archive 不支持负路径，重写一份 zip
    raw = out / f".{name}.raw.zip"
    raw.write_bytes(r.stdout)
    dropped = 0
    with zipfile.ZipFile(raw) as zin, zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.is_dir():
                continue
            if any(info.filename.startswith(p) for p in EXCLUDE_PREFIXES):
                dropped += 1
                continue
            zout.writestr(info, zin.read(info))
    raw.unlink()

    # 2) 关键文件校验（SKILL.md 工位说明书、上手页、配置样例都随 git 走，不另拼）
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        for must in ("SKILL.md", "GETTING-STARTED.md", "config.example.json"):
            if not any(n == must for n in names):
                print(f"包里缺 {must}，先提交它", file=sys.stderr)
                return 2
        leaked = [n for n in names if any(n.startswith(p) for p in EXCLUDE_PREFIXES)]
        if leaked:
            print(f"包里夹带了内部开发史：{'、'.join(leaked[:5])}…", file=sys.stderr)
            return 2

    # 3) 自检：解开、查关键文件、查私有路径泄漏
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(td)
            names = z.namelist()
        root = Path(td)
        # zipfile.extractall **不还原权限位**（这是 Python 的已知行为，`unzip` 会还原）。
        # 包里有可执行文件（scripts/native/person_mask 等），不补这一步就会验出假红：
        # 「Permission denied」看起来像包坏了，其实只是解压方式不同。
        restored = _restore_modes(zpath, root)
        missing = [f for f in ("SKILL.md", "GETTING-STARTED.md",
                               "config.example.json", "scripts/runtime.sh",
                               "scripts/doctor.py", "deps.json") if not (root / f).exists()]
        if missing:
            print(f"包里缺：{'、'.join(missing)}", file=sys.stderr)
            return 2
        leak = subprocess.run(["grep", "-rlIE", r"/Users/[a-zA-Z0-9_.-]+/", str(root)],
                              capture_output=True, text=True)  # -I 跳二进制：全仓扫描防任何私有家目录泄漏
        if leak.stdout.strip():
            print(f"包内仍有私有路径泄漏：\n{leak.stdout}", file=sys.stderr)
            return 1
        n_files = len([n for n in names if not n.endswith("/")])
        print(f"✅ {zpath}")
        print(f"   {n_files} 个文件 · {zpath.stat().st_size / 1e6:.1f} MB"
              f"（已排除 {'、'.join(p.rstrip('/') for p in EXCLUDE_PREFIXES)} 共 {dropped} 项）"
              f" · 可执行 {restored} 个")
        print(f"   上手：解开后读 GETTING-STARTED.md（或整包丢给 AI 工位）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
