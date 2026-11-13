#!/usr/bin/env python3
"""`studio.py`（一键工程页）的离线回归：项目解析、命令拼装、双击入口。

不起服务、不碰网络——起 studio 是 5–9 秒的活，放进自检只会让人不想跑自检。
真起真停的验证是人工做过一次并写进 SKILL.md 的（端口 / HTTP 200 / 帧号）。

    python3 scripts/test_studio.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUDIO = ROOT / "scripts" / "studio.py"
fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def mk_lib(root: Path, names: list[str], with_hf: bool = True) -> Path:
    pr = root / "_cutskills" / "projects"
    for n in names:
        (pr / n).mkdir(parents=True, exist_ok=True)
        (pr / n / "timeline.json").write_text("{}", encoding="utf-8")
        (pr / n / "out").mkdir(exist_ok=True)
        (pr / n / "out" / "film-hf.mp4").write_bytes(b"\0" * 64)
        if with_hf:
            (pr / n / "hf").mkdir(exist_ok=True)
            (pr / n / "hf" / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STUDIO), *args], capture_output=True, text=True)


def case_resolve() -> None:
    print("① 按名字找项目：精确优先、含糊就报歧义、找不到退 2")
    with tempfile.TemporaryDirectory() as td:
        lib = mk_lib(Path(td) / "lib", ["film-a", "film-b", "other-c"])
        r = run("--print-cmd", "--project", "film-a", "--library", str(lib))
        must("精确名命中", r.returncode == 0 and "film-a/hf" in r.stdout, (r.stdout + r.stderr).strip()[-160:])
        r = run("--print-cmd", "--project", "film", "--library", str(lib))
        must("前缀命中多个 → 报歧义退 2", r.returncode == 2 and "命中 2 个" in r.stderr, r.stderr.strip()[-160:])
        r = run("--print-cmd", "--project", "nope", "--library", str(lib))
        must("找不到 → 退 2", r.returncode == 2, r.stderr.strip()[-160:])
        # 没有 hf/index.html 的项目不给入口（工程页无从起）
        lib2 = mk_lib(Path(td) / "lib2", ["nohf-x"], with_hf=False)
        r = run("--print-cmd", "--project", "nohf-x", "--library", str(lib2))
        must("没渲过工程页的项目不入口", r.returncode == 2, r.stderr.strip()[-160:])


def case_cmd() -> None:
    print("② 命令拼装：cd 到 hf 目录、调钉住的 hyperframes、带 --open")
    with tempfile.TemporaryDirectory() as td:
        lib = mk_lib(Path(td) / "lib", ["film-a"])
        r = run("--print-cmd", "--project", "film-a", "--library", str(lib))
        out = r.stdout.strip()
        must("命令以 cd 开头", out.startswith("cd \""), out)
        must("cd 到 hf 目录", "/hf\" &&" in out, out)
        must("带 --open", out.endswith("preview . --open"), out)
        must("--port 能拼进去", "--port 3111" in
             run("--print-cmd", "--project", "film-a", "--library", str(lib), "--port", "3111").stdout,
             out)


def case_launchers() -> None:
    print("③ 双击入口：每支片一个 .command，可执行、内容能跑")
    with tempfile.TemporaryDirectory() as td:
        lib = mk_lib(Path(td) / "lib", ["film-a", "film-b"], with_hf=True)
        mk_lib(Path(td) / "lib", ["no-hf"], with_hf=False)
        outdir = Path(td) / "产出"
        r = run("--emit-launchers", str(outdir), "--library", str(lib))
        must("退出码 0", r.returncode == 0, (r.stdout + r.stderr).strip()[-160:])
        made = sorted(p.name for p in outdir.glob("*.command"))
        must("有两个双击入口", len(made) == 2, str(made))
        must("不生成没工程页的项目", all("no-hf" not in n for n in made), str(made))
        f = outdir / "启动工程-film-a.command"
        txt = f.read_text(encoding="utf-8") if f.exists() else ""
        must("脚本带 shebang", txt.startswith("#!/bin/bash"), txt[:40])
        must("脚本 cd 到 hf", "/hf\" || exit 1" in txt, txt)
        must("脚本调 preview --open", "preview . --open" in txt, txt)
        must("可执行位", bool(f.stat().st_mode & 0o111) if f.exists() else False)


def case_stop_bad() -> None:
    print("④ 关不存在的项目：不许假装成功")
    with tempfile.TemporaryDirectory() as td:
        lib = mk_lib(Path(td) / "lib", ["film-a"])
        r = run("--stop", "nope", "--library", str(lib))
        must("退 2 并说清", r.returncode == 2, (r.stdout + r.stderr).strip()[-160:])


def main() -> int:
    case_resolve()
    case_cmd()
    case_launchers()
    case_stop_bad()
    print()
    if fails:
        print(f"❌ {len(fails)} 条没过：" + "、".join(fails))
        return 1
    print("✅ studio 入口负样本 4 组全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
