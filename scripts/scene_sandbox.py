#!/usr/bin/env python3
"""场景沙箱：AI 写的场景代码在**受限子进程**里渲染，不在主进程里裸跑。

为什么要有它（评审 P1-06）：`gen_scene.py` 会动态 import 场景模块。仓库自带的场景
是人写死、审过的；AI 按对话新写的场景是**刚出生的任意代码**——直接 import 进主进程，
它拥有主进程的一切：网络、文件系统、无限 CPU。所以 AI 场景的渲染必须走这里：

  ① 静态门槛：AST 扫描 import，只放行白名单（numpy / PIL / math / json / pathlib…），
     socket / urllib / subprocess / ctypes 这类直接拒绝，根本不进子进程；
  ② 系统围栏（macOS）：外层用 `sandbox-exec` 拒网（profile 内联写入临时文件），
     子进程再叠 resource 限额：CPU 时长 / 内存 / 单文件大小 / 进程数；
  ③ 时间围栏：整次渲染有墙钟超时，超时杀整个进程组；
  ④ 输出围栏：cwd 是临时工作目录，产物只允许写到 --out 指定路径，渲完 ffprobe 验明正身。

被沙箱拒绝/超时/崩溃都不算渲染失败之外的事——退出码 3 = 代码被拒，4 = 超时或崩溃。
场景写坏了就该重写代码，而不是把围栏拆了。

用法（渲器自动调用，人也可以手动跑）：
  python3 scripts/scene_sandbox.py --scenes-dir <项目>/scenes --scene <name> \
      --out <mp4> --dur 6.0 --fps 30 --size 1080x1920 [--marks "1.2,3.4"]

退出码：0 成功；2 参数/文件错；3 静态检查拒绝（含违规 import）；4 超时或子进程崩溃；
5 本机没有 sandbox-exec 且未显式放行（评审 G-03：无隔离不跑 AI 代码）。
    sandbox-exec 是 macOS 的 seatbelt；Windows / Linux 上没有等价物，所以那些平台上
    **默认同样是退 5 拒跑**——Windows 上暂不支持 AI 场景生成，可用实拍素材或内置模板替代。
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# resource 是 POSIX 专属（Windows 上 import 直接 ModuleNotFoundError）。
# 围栏的 rlimit 部分因此按平台降级：没有 resource 就不设 rlimit，靠墙钟超时停车。
try:
    import resource
except ImportError:                                  # pragma: no cover - 只在 Windows 上走到
    resource = None                                  # type: ignore[assignment]

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 平台判定留一个可注入的常量：单测在 macOS 上把 PLATFORM 换成 "win32" 验 Windows 分支，
# 不需要一台真的 Windows 机器。
PLATFORM = sys.platform

# 场景代码允许 import 的东西：画图与纯计算。白名单外一律拒绝——
# 宁可让作者来申请加白，也不给「顺手 import requests」留门。
IMPORT_ALLOW = {
    # 绘图与计算
    "numpy", "PIL", "math", "colorsys", "random",
    # 基础设施（场景里合法要用到的）
    "json", "pathlib", "typing", "dataclasses", "collections", "itertools",
    "functools", "enum", "abc", "decimal", "fractions", "statistics",
    "__future__",
    # 宿主模块：场景读 --marks 拍点的唯一正道（gen_scene.marks 是 trusted host）
    "gen_scene",
}
# 但把整个 gen_scene 放进来等于连它的 subprocess/tempfile/os 一起送人——场景写
# `import gen_scene; gen_scene.subprocess.run([...])` 静态检查零命中、沙箱里真跑
# （2026-11-15 对抗审查 P1 已复现）。所以 import 可以，**属性只许这几个**。
GEN_SCENE_API = {"marks", "MARKS"}
# 反射类调用一律拒：getattr(gen_scene, "subprocess") 是绕过属性检查的后门
BANNED_CALLS = {"system", "popen", "eval", "exec", "compile", "__import__",
                "getattr", "setattr", "vars", "globals", "locals"}
# 这些名字哪怕出现在 import 里也必拒（防御白名单将来误加）
IMPORT_DENY = {
    "socket", "ssl", "urllib", "http", "ftplib", "telnetlib", "smtplib",
    "subprocess", "ctypes", "multiprocessing", "pickle", "shutil",
    "importlib", "runpy", "ast", "sys", "os", "signal", "resource",
    "asyncio", "threading", "sqlite3", "email", "xml", "platform",
}

# CPU 限额：渲染帧数决定下限，再给 4 倍余量；墙钟另算（是 CPU 的 2 倍，等 IO）。
# macOS 不支持 RLIMIT_AS（实测 setrlimit 报 "current limit exceeds maximum limit"），
# 内存没有硬围栏——靠 CPU 限额 + 墙钟超时兜住跑飞的场景。
CPU_BASE_S = 90
CPU_MULT = 4
WALL_MULT = 2
MEM_BYTES = 4 * 1024**3        # 仅文档化目标值；macOS 实际不强制（见上）
FSIZE_BYTES = 512 * 1024**2    # 单个输出文件封顶 512 MB
# 不设 RLIMIT_NPROC：它限的是**整个用户**的进程总数，不是这个子树的——
# 日常机器用户进程一多，fork 当场被自己的限额卡死（实测 Errno 35）。进程数靠墙钟超时兜。
MEM_BYTES = 4 * 1024**3        # 仅文档化目标值；macOS 实际不强制（见上）


def static_check(scene_file: Path) -> list[str]:
    """AST 扫描场景源码，返回违规清单（空 = 放行）。**只看 import 与危险调用。**"""
    bad: list[str] = []
    try:
        tree = ast.parse(scene_file.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [f"语法错误 line {e.lineno}: {e.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = (a.name or "").split(".")[0]
                if root in IMPORT_DENY:
                    bad.append(f"line {node.lineno}: import {a.name}（被禁模块）")
                elif root not in IMPORT_ALLOW:
                    bad.append(f"line {node.lineno}: import {a.name}（不在白名单，"
                               f"需要的话改用白名单内等价物或来申请）")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level:                       # 相对导入：场景文件必须自包含
                bad.append(f"line {node.lineno}: 相对导入 from {'.' * node.level}"
                           f"{node.module or ''}（场景文件必须自包含）")
            elif root and root in IMPORT_DENY:
                bad.append(f"line {node.lineno}: from {node.module} import …（被禁模块）")
            elif root and root not in IMPORT_ALLOW:
                bad.append(f"line {node.lineno}: from {node.module} import …（不在白名单）")
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if name in BANNED_CALLS:
                bad.append(f"line {node.lineno}: 调用 {name}()（被禁调用）")
        elif isinstance(node, ast.Attribute):
            # gen_scene.subprocess / gen_scene.os 这类属性链：只许 GEN_SCENE_API
            base = node.value
            if (isinstance(base, ast.Name) and base.id == "gen_scene"
                    and node.attr not in GEN_SCENE_API):
                bad.append(f"line {node.lineno}: gen_scene.{node.attr}"
                           f"（只许 {', '.join(sorted(GEN_SCENE_API))}）")
    return bad


def os_family(plat: str | None = None) -> str:
    """纯函数：sys.platform / 显式注入值 → darwin / windows / linux / other。"""
    p = (plat if plat is not None else PLATFORM or sys.platform).lower()
    if p.startswith("darwin"):
        return "darwin"
    if p.startswith("win") or p.startswith(("cygwin", "msys")):
        return "windows"
    if p.startswith("linux"):
        return "linux"
    return "other"


def sandbox_available(plat: str | None = None) -> bool:
    """本平台有没有能用的系统围栏。目前只有 macOS 的 sandbox-exec 算数。"""
    return os_family(plat) == "darwin" and bool(shutil.which("sandbox-exec"))


def _refuse_text(plat: str | None = None) -> str:
    """拒跑时的报错文案：跨平台可读，并说明本平台该怎么办。"""
    fam = os_family(plat)
    if fam == "windows":
        return ("✗ AI 场景拒绝执行：Windows 上没有 sandbox-exec，也没有等价的系统围栏（评审 G-03）。\n"
                "  Windows 上暂不支持 AI 场景生成——用实拍素材或内置场景模板替代；\n"
                "  不接受裸跑：--allow-unsandboxed 只给确认过的内网开发机，不是 Windows 的默认路径。")
    if fam == "linux":
        return ("✗ AI 场景拒绝执行：Linux 上没有 sandbox-exec，也没有等价的系统围栏（评审 G-03）。\n"
                "  要用 AI 场景：迁到 macOS，或自己接一层 bwrap/nsjail 后改本文件的围栏实现；\n"
                "  确认过的内网开发机可加 --allow-unsandboxed 显式放行（rlimit 与白名单仍生效）。")
    return ("✗ 本机没有 sandbox-exec——AI 场景拒绝在无隔离环境执行（评审 G-03）。\n"
            "  macOS 的 sandbox-exec 在 /usr/bin/sandbox-exec，被删了就恢复系统组件；\n"
            "  或确认内网开发机后加 --allow-unsandboxed 显式放行。")


def _limits(cpu_s: int):
    if resource is None:                             # Windows：没有 rlimit，返回 None 不传 preexec_fn
        return None

    def apply():                             # 子进程 fork 后、exec 前执行
        # 逐项设、设不了的跳过：macOS 不让设 RLIMIT_AS/DATA/RSS（
        # "current limit exceeds maximum limit"），内存围栏退到 CPU + 墙钟 + FSIZE。
        for which, pair in ((resource.RLIMIT_CPU, (cpu_s, cpu_s + 2)),
                            (resource.RLIMIT_FSIZE, (FSIZE_BYTES, FSIZE_BYTES))):
            try:
                resource.setrlimit(which, pair)
            except (ValueError, OSError):
                pass
    return apply


def _sandbox_profile(scratch: Path, out: Path, plat: str | None = None) -> str | None:
    """macOS seatbelt profile：拒网 + 写盘只许工作目录与产物目录。

    2026-11-15 对抗审查 P0：旧写法 `(allow default)` + `(deny file-write* /Users)`
    自相矛盾——seatbelt 里 **deny 永远压过 allow**，于是
    `(allow file-write* (subpath out.parent))` 对 /Users 下的产物路径全部失效，
    真实项目（都在 /Users 下）的项目级场景一律 Operation not permitted；
    而测试跑在 /var/folders 临时目录，掩盖了实况。
    改为**显式放行**：不开 allow default，只白名单读/执行/必要写盘——
    这才是「默认拒绝」的正确写法。

    平台分叉：seatbelt profile 是 macOS 专有格式，Windows / Linux 上没有可用的等价系统围栏
    （Linux 的 bwrap 需要另装，本仓没接）。所以非 darwin 一律返回 None → 调用方退 5 拒跑，
    **不会**退成「无围栏裸跑」。
    """
    if os_family(plat) != "darwin" or not shutil.which("sandbox-exec"):
        return None
    return (
        "(version 1)\n"
        "(deny network*)\n"                     # 网络：一律拒（沙箱存在的主要理由）
        "(allow file-read*)\n"                  # 读：python/numpy/PIL 要读系统库与场景源码
        "(allow process-exec*)\n"               # 执行：ffmpeg / python 自身
        "(allow process-fork)\n"
        "(allow signal)\n"
        "(allow sysctl-read)\n"
        "(allow file-write* "                   # 写：只许沙箱工作目录与产物目录
        f'(subpath "{scratch}") (subpath "{out.parent}") '
        '(subpath "/dev") (subpath "/private/var/folders"))\n'
    )


def _scene_path(src_dir: Path, scene: str) -> Path | None:
    """场景名 → 文件路径，**不许跳出 scenes 目录**。

    2026-11-15 对抗审查 P2：`--scene "../x"` 原来能指到项目里任意 .py——只要那个
    文件里的 SCENE_ID 恰好等于这个带 ../ 的字符串就能执行（沙箱仍在，但越界本身
    就该拒）。名字只许 [A-Za-z0-9_-]，且 resolve 后必须仍在 scenes 下。
    """
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", str(scene or "")) or ".." in str(scene):
        return None
    p = (src_dir / f"{scene}.py").resolve()
    try:
        p.relative_to(src_dir.resolve())
    except ValueError:
        return None
    return p


def render(args: argparse.Namespace) -> int:
    src_dir = Path(args.scenes_dir).expanduser().resolve()
    scene_file = _scene_path(src_dir, args.scene)
    if scene_file is None:
        print(f"✗ 场景名不合法：{args.scene!r}（只许字母数字下划线横线，且必须在 scenes/ 内）",
              file=sys.stderr)
        return 2
    if not scene_file.exists():
        print(f"✗ 找不到场景源码：{scene_file}", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    # ① 静态门槛：违规 import / 危险调用根本不进子进程
    bad = static_check(scene_file)
    if bad:
        print("✗ 场景代码被沙箱拒绝（先改代码，不要拆围栏）：", file=sys.stderr)
        for b in bad[:12]:
            print(f"   · {b}", file=sys.stderr)
        if len(bad) > 12:
            print(f"   …另有 {len(bad) - 12} 条", file=sys.stderr)
        return 3

    frames = max(1, int(round(float(args.dur) * int(args.fps))))
    cpu_s = max(CPU_BASE_S, int(frames * CPU_MULT / max(int(args.fps), 1) or CPU_BASE_S))
    cpu_s = max(cpu_s, int(float(args.dur) * CPU_MULT))
    wall_s = cpu_s * WALL_MULT

    with tempfile.TemporaryDirectory(prefix="scene-sandbox-") as td:
        scratch = Path(td)
        # ② 系统围栏：sandbox-exec 拒网；围栏缺席时**拒绝执行**——刚出生的 AI 代码
        # 没有拒网/写盘围栏就是裸跑（评审 G-03），改机器环境或显式 --allow-unsandboxed。
        inner = [sys.executable, str(HERE / "gen_scene.py"),
                 "--scene", args.scene, "--scene-file", str(scene_file),
                 "--out", str(out), "--dur", f"{float(args.dur):.3f}",
                 "--fps", str(args.fps), "--size", args.size]
        if args.marks:
            inner += ["--marks", args.marks]
        profile = _sandbox_profile(scratch, out, PLATFORM)
        if profile:
            pf = scratch / "scene-sandbox.sb"
            pf.write_text(profile)
            cmd = ["sandbox-exec", "-f", str(pf)] + inner
        elif args.allow_unsandboxed:
            cmd = inner
            print("  ⚠ 显式放行：本机没有 sandbox-exec，拒网围栏缺席（rlimit 与白名单仍然生效）",
                  file=sys.stderr)
        else:
            print(_refuse_text(PLATFORM), file=sys.stderr)
            return 5
        t0 = time.monotonic()
        posix = os_family(PLATFORM) != "windows"     # preexec_fn / start_new_session 是 POSIX 语义
        try:
            r = subprocess.run(cmd, cwd=scratch,
                               preexec_fn=_limits(cpu_s) if posix else None,
                               capture_output=True, text=True, timeout=wall_s,
                               start_new_session=posix)
        except subprocess.TimeoutExpired:
            print(f"✗ 场景渲染超时（>{wall_s}s）——通常是死循环或每帧太重，改代码",
                  file=sys.stderr)
            return 4
        except OSError as e:
            print(f"✗ 子进程起不来：{e}", file=sys.stderr)
            return 4
        dt = time.monotonic() - t0
        if r.returncode != 0 or not out.exists() or out.stat().st_size <= 0:
            print(f"✗ 场景在沙箱里没画出来（exit {r.returncode}，{dt:.1f}s）",
                  file=sys.stderr)
            tail = (r.stderr or r.stdout or "").strip()[-800:]
            print(tail, file=sys.stderr)
            return 4
    print(f"  · 沙箱渲染完成：{out.name}（{dt:.1f}s，拒网={'是' if profile else '否'}，"
          f"CPU 限额 {cpu_s}s）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 场景的受限执行器（拒网 + rlimit + 墙钟超时）")
    ap.add_argument("--scenes-dir", required=True, help="场景源码目录（项目 scenes/）")
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dur", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--marks", default="")
    ap.add_argument("--allow-unsandboxed", action="store_true",
                    help="显式放行无 sandbox-exec 的裸跑（仅供内网开发机；默认拒绝，评审 G-03）")
    a = ap.parse_args()
    return render(a)


if __name__ == "__main__":
    raise SystemExit(main())
