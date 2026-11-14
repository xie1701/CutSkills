#!/usr/bin/env python3
"""平台抽象的合同测试（BRIEF 最后一条：一键环境检查 / 安装脚本要覆盖 macOS 与 Windows）。

我们**没有 Windows 机器**，所以这里测的不是「Windows 上跑得通」，而是三件能在本机证伪的事：

  ① `deps.json` 的每个平台分叉四键齐全、或者明写 `unavailable`；`models.asr` 不再把
     `sherpa-onnx-darwin-arm64` 当默认值（v1 就是一句话写死 macOS 包名 = 把 macOS 当全世界）；
  ② `doctor.py` 的平台判定抽成了纯函数，注入三种平台值能返回正确分支（required /
     optional / unsupported），Windows 上「平台不支持」不会被判成 ❌；
  ③ Windows 上缺 `sandbox-exec` 时 `scene_sandbox.render` 仍然拒跑（exit 5 口径不变）——
     不许因为「跨平台」就静默裸跑 AI 代码；
  ④ `install.ps1` 与本仓 `runtime.sh` 的**能力清单逐项对齐**：两边用同一套 `# cap:` 标记
     声明能力，这里比的是标记集合（缺一项即红），不是「文件里出现过 ffmpeg 这个词」。

跑法：python3 scripts/test_platform.py（挂 selftest 离线单测）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

PLATFORMS = ("darwin-arm64", "darwin-x64", "win-x64", "linux-x64")


# ---------------------------------------------------------------- ① deps.json
def test_deps_platform_forks() -> None:
    print("▶ ① deps.json 平台分叉")
    deps = json.loads((ROOT / "deps.json").read_text(encoding="utf-8"))
    assert isinstance(deps.get("version"), int) and deps["version"] >= 2, \
        "deps.json 的平台分叉是 v2 结构（v1 把 macOS 包名当默认值）"

    keys = deps["platforms"]["keys"]
    assert set(keys) == set(PLATFORMS), f"platforms.keys 必须是 {PLATFORMS}：实际 {keys}"
    print(f"  ✅ platforms.keys = {keys}")

    # 递归：任何一个带 platforms 的节点，四键都得在，且每项要么明写 unavailable，要么带 status
    forks: list[tuple[str, dict]] = []

    def walk(node: dict, path: str) -> None:
        for k, v in node.items():
            if not isinstance(v, dict):
                continue
            if isinstance(v.get("platforms"), dict):
                forks.append((f"{path}.{k}", v["platforms"]))
            else:
                walk(v, f"{path}.{k}")

    walk(deps, "")
    assert forks, "deps.json 里一个平台分叉都没有——那就是又退回「一套包名管全世界」了"
    for name, plats in forks:
        missing = [p for p in PLATFORMS if p not in plats]
        assert not missing, f"{name} 缺平台分叉：{missing}（缺的平台必须明写 unavailable）"
        for p, body in plats.items():
            if isinstance(body, str):
                assert body.strip() == "unavailable", \
                    f"{name}.{p} 是字符串就必须是 'unavailable'，否则用 dict + status"
                continue
            assert isinstance(body, dict), f"{name}.{p} 形状不认识：{type(body)}"
            assert body.get("status") in ("available", "unavailable", "optional", "builtin"), \
                f"{name}.{p}.status 非法：{body.get('status')!r}"
            if body["status"] == "unavailable":
                assert body.get("why"), f"{name}.{p} 写了 unavailable 却没说 why"
    print(f"  ✅ {len(forks)} 个平台分叉四键齐全，unavailable 都带 why")

    # asr：不许把 darwin 包名当默认值
    asr = deps["models"]["asr"]
    assert isinstance(asr, dict) and isinstance(asr.get("platforms"), dict), \
        "models.asr 必须是带 platforms 的分叉结构，不能是一句话写死包名"
    win = asr["platforms"]["win-x64"]
    assert win["status"] == "unavailable", f"win-x64 的 asr 必须明写 unavailable：{win}"
    darm = asr["platforms"]["darwin-arm64"]
    assert "sherpa-onnx-darwin-arm64" in json.dumps(darm, ensure_ascii=False), \
        "darwin-arm64 分叉要写清是 sherpa-onnx-darwin-arm64（包名只许出现在它自己的分叉里）"
    for p in ("win-x64", "linux-x64", "darwin-x64"):
        blob = json.dumps(asr["platforms"][p], ensure_ascii=False)
        assert "package" not in json.dumps(asr["platforms"][p], ensure_ascii=False) or \
            "darwin" not in blob, f"{p} 分叉不许把 darwin 包名当默认值：{blob}"
    print("  ✅ models.asr 按平台分叉，win-x64/linux-x64 明写 unavailable")

    # 字体与节拍检测也得在清单里（BRIEF 明确要求覆盖这两项）
    assert deps["fonts"]["required"], "deps.json 必须登记字体（BRIEF：安装脚本要覆盖字体）"
    assert set(deps["fonts"]["platforms"]) == set(PLATFORMS), "字体目录也要按平台分叉"
    assert "beat_detection" in deps["models"], "节拍检测要在 models 里登记（哪怕是 builtin）"
    print(f"  ✅ 字体 {len(deps['fonts']['required'])} 个 / 节拍检测已登记")


# ---------------------------------------------------------------- ② doctor 判定
def test_doctor_platform_branches() -> None:
    print("▶ ② doctor.py 平台判定（注入三种平台值）")
    import doctor

    cases = {
        ("darwin", "arm64"): "darwin-arm64",
        ("win32", "AMD64"): "win-x64",
        ("linux", "x86_64"): "linux-x64",
    }
    for (sysname, machine), want in cases.items():
        got = doctor.platform_key(sysname, machine)
        assert got == want, f"platform_key({sysname!r}, {machine!r}) = {got!r}，应为 {want!r}"
    print("  ✅ platform_key：darwin-arm64 / win-x64 / linux-x64 三条分支对")

    # 依赖分支：三平台 × 关键依赖
    table = {
        ("ffmpeg", "darwin-arm64"): "required",
        ("ffmpeg", "win-x64"): "required",
        ("ffmpeg", "linux-x64"): "required",
        ("python3", "win-x64"): "required",
        ("coli", "darwin-arm64"): "optional",
        ("coli", "win-x64"): "unsupported",
        ("asr", "linux-x64"): "unsupported",
        ("sandbox", "win-x64"): "unsupported",
        ("sandbox", "darwin-arm64"): "optional",
        ("fonts", "win-x64"): "required",
        ("beat", "win-x64"): "required",
    }
    for (dep, plat), want in table.items():
        got = doctor.dep_branch(dep, plat)
        assert got == want, f"dep_branch({dep!r}, {plat!r}) = {got!r}，应为 {want!r}"
    print(f"  ✅ dep_branch：{len(table)} 条分支判定对（required / optional / unsupported）")

    # 「缺了怎么办」按平台给不同命令——Windows 分支不能给 brew
    mac_hint = doctor.fix_hint("ffmpeg", "darwin-arm64")
    win_hint = doctor.fix_hint("ffmpeg", "win-x64")
    assert "brew" in mac_hint, f"macOS 的修法要给 brew：{mac_hint}"
    assert "brew" not in win_hint and "winget" in win_hint, f"Windows 的修法不许给 brew：{win_hint}"
    print("  ✅ fix_hint 按平台分叉（macOS brew / Windows winget）")

    # Windows 上「平台不支持」必须是 ➖，不能落到 ❌
    collected: list[tuple[str, str]] = []
    for dep in ("coli", "asr", "sandbox"):
        collected.clear()
        doctor.rows.clear()
        doctor.counts.update({doctor.OK: 0, doctor.WARN: 0, doctor.BAD: 0, doctor.NA: 0})
        doctor.expect(dep, "win-x64", False, "在位", f"{dep} 缺失")
        assert not doctor.rows or doctor.rows[-1][0] == doctor.NA, \
            f"{dep} 在 win-x64 上不支持，必须报 ➖ 而不是 ❌：{doctor.rows}"
        assert doctor.counts[doctor.BAD] == 0, f"{dep} 不许被判成 ❌"
    print("  ✅ win-x64 上平台不支持项落 ➖（❌ 计数为 0）")

    # 可执行查找跨平台：Windows 上按 PATHEXT 补 .exe/.cmd（用临时目录造一个假 ffmpeg.cmd）
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "ffmpeg.cmd"
        fake.write_text("@echo off\n")
        fake.chmod(0o755)
        hit = doctor.find_exe("ffmpeg", plat="win-x64", path=td, pathext=".EXE;.CMD")
        assert hit and Path(hit).name == "ffmpeg.cmd", f"PATHEXT 查找没生效：{hit}"
        miss = doctor.find_exe("ffmpeg", plat="win-x64", path=td, pathext=".EXE")
        assert miss is None, f"PATHEXT 里没有 .CMD 时不该命中：{miss}"
        assert doctor.find_exe("nope-xyz", plat="linux-x64", path=td) is None
    print("  ✅ find_exe 处理 PATHEXT（.cmd 命中、.EXE 不命中）")

    # 字体目录按平台分叉
    mac_dirs = [str(p) for p in doctor.font_dirs("darwin-arm64")]
    win_dirs = [str(p) for p in doctor.font_dirs("win-x64")]
    assert any("Library/Fonts" in d for d in mac_dirs), mac_dirs
    assert all("Library/Fonts" not in d for d in win_dirs), win_dirs
    assert win_dirs, "Windows 字体目录不许空"
    print("  ✅ font_dirs 按平台分叉（macOS Library/Fonts ↔ Windows Fonts）")


# ---------------------------------------------------------------- ③ 沙箱拒跑
def test_scene_sandbox_refuses_on_windows() -> None:
    print("▶ ③ Windows 缺 sandbox-exec 时 scene_sandbox 仍然拒跑（exit 5）")
    import scene_sandbox as sb

    good = '''from __future__ import annotations
import math


SCENE_ID = "win_probe"


def scene(t, W, H, dur):
    return None
'''
    old_platform = sb.PLATFORM
    old_which = sb.shutil.which
    try:
        sb.PLATFORM = "win32"
        sb.shutil.which = lambda name, *a, **k: None      # 假装这台机器上什么都没有
        assert sb.os_family() == "windows"
        assert sb.sandbox_available() is False
        assert sb._sandbox_profile(Path("/tmp"), Path("/tmp/x.mp4")) is None

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scenes = root / "scenes"
            scenes.mkdir()
            (scenes / "win_probe.py").write_text(good)
            out = root / "out" / "win_probe.mp4"
            ns = argparse.Namespace(scenes_dir=str(scenes), scene="win_probe",
                                    out=str(out), dur=1.0, fps=6, size="270x480",
                                    marks="", allow_unsandboxed=False)
            import io
            import contextlib
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = sb.render(ns)
            assert rc == 5, f"Windows 无围栏必须 exit 5（拒跑），实际 {rc}"
            msg = err.getvalue()
            assert "Windows" in msg, f"报错文案要跨平台可读、点名 Windows：{msg!r}"
            assert "sandbox-exec" in msg, f"要说清缺的是 sandbox-exec：{msg!r}"
            assert "内置" in msg or "实拍" in msg, f"要给出替代路径（实拍 / 内置模板）：{msg!r}"
            assert not out.exists(), "被拒的场景一个字节都不许写出去"
        print("  ✅ win32 注入：exit 5 + 文案点名 Windows / sandbox-exec / 替代路径，零产物")

        # 口径对照：macOS 上照旧能起沙箱（没被跨平台改动弄坏）
        sb.PLATFORM = old_platform
        sb.shutil.which = old_which
        assert sb.os_family() == sb.os_family(sys.platform)
        if sys.platform == "darwin":
            assert sb.sandbox_available() is True, "macOS 上 sandbox-exec 必须仍然可用"
            assert sb._sandbox_profile(Path("/tmp"), Path("/tmp/o.mp4")), "macOS 的 profile 不许丢"
            print("  ✅ macOS 分支未被削弱（sandbox_available / profile 仍可用）")
    finally:
        sb.PLATFORM = old_platform
        sb.shutil.which = old_which


# ---------------------------------------------------------------- ④ 能力清单对齐
CAP_RE = re.compile(r"#\s*cap:\s*(.+)$", re.MULTILINE)
CAP_EXTRA_RE = re.compile(r"#\s*cap-extra:\s*(.+)$", re.MULTILINE)


def caps_of(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    out: set[str] = set()
    for line in CAP_RE.findall(text):
        out.update(t for t in line.strip().split() if t)
    return out


def extras_of(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    out: set[str] = set()
    for line in CAP_EXTRA_RE.findall(text):
        out.update(t for t in line.strip().split() if t)
    return out


def test_install_ps1_parity() -> None:
    print("▶ ④ install.ps1 与 runtime.sh 能力逐项对齐")
    ps1 = ROOT / "install.ps1"
    sh = HERE / "runtime.sh"
    assert ps1.is_file(), "仓库根要有 install.ps1"
    assert sh.is_file(), "缺 scripts/runtime.sh"

    bash_caps = caps_of(sh)
    ps_caps = caps_of(ps1)
    assert bash_caps, "runtime.sh 里一个 # cap: 标记都没有——能力清单没法定"
    assert len(bash_caps) >= 10, f"能力清单太短，像是被删过：{sorted(bash_caps)}"

    missing = bash_caps - ps_caps
    extra = ps_caps - bash_caps
    assert not missing, f"install.ps1 少了 bash 版的能力：{sorted(missing)}"
    assert not extra, f"install.ps1 多出没在 bash 版声明的能力（先加标记再说）：{sorted(extra)}"
    assert len(bash_caps) == len(ps_caps), \
        f"能力项数不一致：bash {len(bash_caps)} / ps1 {len(ps_caps)}"
    print(f"  ✅ 能力清单一一对应（{len(bash_caps)} 项）：{', '.join(sorted(bash_caps))}")

    # 清单里必须真有这些关键项——防「把 ffmpeg 那条删了也算对齐」
    required = {"check:ffmpeg", "check:ffprobe", "check:node", "check:python",
                "check:coli", "check:asr-model", "install:npm-hyperframes",
                "install:pip-deps", "check:gsap"}
    assert required <= bash_caps, f"内存清单少了关键项：{sorted(required - bash_caps)}"
    print(f"  ✅ 关键项齐全（{len(required)} 项硬要求）")

    # Windows 独有的能力单独声明，不许混进共享清单
    ps_extras = extras_of(ps1)
    for want in ("check:fonts", "install:fonts", "check:beat", "check:sandbox", "doc:selfcheck"):
        assert want in ps_extras, f"install.ps1 的 Windows 独有能力缺 {want}"
    print(f"  ✅ Windows 独有能力走 cap-extra：{', '.join(sorted(ps_extras))}")

    # 与「不吞错误」的要求对得上（注释里可以提到它，代码里不许用）
    ps_text = ps1.read_text(encoding="utf-8")
    code_only = "\n".join(ln for ln in ps_text.splitlines()
                           if not ln.lstrip().startswith("#"))
    assert "-ErrorAction SilentlyContinue" not in code_only, \
        "不许用 -ErrorAction SilentlyContinue 一把梭把安装失败吞掉"
    assert "Set-StrictMode" in ps_text, "脚本要开 StrictMode（未定义变量早报错，别静默）"
    assert "$ErrorActionPreference = 'Stop'" in ps_text or '$ErrorActionPreference = "Stop"' in ps_text, \
        "要开 ErrorActionPreference=Stop"
    assert "-CheckOnly" in ps_text, "要支持 -CheckOnly（对应 runtime.sh 的体检路径）"
    assert "windows-selfcheck.md" in ps_text, "要落一份 <CUTSKILLS_HOME>/windows-selfcheck.md"
    assert "未在 Windows 真机验证" in ps_text, "脚本里要如实标注未真机验证"
    print("  ✅ 无 SilentlyContinue / 有 StrictMode / -CheckOnly / 自检清单 / 未验证声明")

    # 括号配平（机械检查，不是语法解析：本机没有 pwsh 时这是唯一能自动验的一层）
    bad = ps1_structure_problems(ps_text)
    assert not bad, f"install.ps1 结构配平有问题：{bad[:5]}"
    print("  ✅ 结构配平（引号 / here-string / 括号）无残")


def ps1_structure_problems(text: str) -> list[str]:
    """粗检：注释、单双引号、here-string 之外的 { } ( ) [ ] 是否配平。

    这不是 PowerShell 语法解析（那要 pwsh）。它只保证「没有明显的截断/漏括号」，
    真正的解析验证要在装了 PowerShell 的机器上跑 `pwsh -NoProfile -Command`。
    """
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i, n = 0, len(text)
    line = 1
    in_block_comment = False
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        # 块注释 <# ... #>
        if in_block_comment:
            if text.startswith("#>", i):
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if text.startswith("<#", i):
            in_block_comment = True
            i += 2
            continue
        # here-string @' ... '@ / @" ... "@
        if c == "@" and i + 1 < n and text[i + 1] in "'\"":
            quote = text[i + 1]
            end_tok = "\n" + quote + "@"
            j = text.find(end_tok, i)
            if j < 0:
                problems.append(f"第 {line} 行：here-string 没闭合")
                return problems
            line += text.count("\n", i, j)
            i = j + len(end_tok)
            continue
        # 行注释
        if c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        # 单引号（'' 是转义）
        if c == "'":
            i += 1
            while i < n:
                if text[i] == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                if text[i] == "\n":
                    line += 1
                i += 1
            continue
        # 双引号（反引号转义）
        if c == '"':
            i += 1
            while i < n:
                if text[i] == "`" and i + 1 < n:
                    if text[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                if text[i] == "\n":
                    line += 1
                i += 1
            continue
        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if not stack:
                problems.append(f"第 {line} 行：多出来的 {c}")
            elif stack[-1][0] != pairs[c]:
                problems.append(f"第 {line} 行：{c} 和 {stack[-1][1]} 行的 {stack[-1][0]} 不配对")
                stack.pop()
            else:
                stack.pop()
        i += 1
    for ch, ln in stack:
        problems.append(f"第 {ln} 行的 {ch} 没闭合")
    return problems


def test_doctor_hints_are_runnable():
    """医生开的每条「修法」都必须真能跑。

    真事：doctor 对「自编 ffmpeg 没装」写着「`bash scripts/runtime.sh install` 装它」，
    而 `install` 从头到尾没碰过这一项——用户照着跑一遍，病没治，还以为自己装好了。
    这类「提示指着一个不存在的动作」比不提示更糟，所以把 doctor 里点名的 runtime.sh
    子命令全部抓出来，逐个核存在于分发器里。
    """
    root = Path(__file__).resolve().parent.parent
    doc = (root / "scripts" / "doctor.py").read_text()
    rt = (root / "scripts" / "runtime.sh").read_text()
    # runtime.sh 的分发器：`  xxx)` 分支名
    dispatch = set(re.findall(r"^\s{2}([a-z][a-z-]+)\)\s", rt, re.M))
    assert dispatch >= {"env", "check", "install", "adopt", "path", "run"}, dispatch
    named = set(re.findall(r"runtime\.sh\s+([a-z][a-z-]+)", doc))
    ghost = named - dispatch
    assert not ghost, f"doctor 让用户跑的 runtime.sh 子命令不存在：{sorted(ghost)}（分发器里有 {sorted(dispatch)}）"
    # 而且那个子命令得真有一份实现，不是只挂在用法串里
    if "install-ffmpeg-zimg" in dispatch:
        assert "cmd_install_ffmpeg_zimg()" in rt, "分发器有 install-ffmpeg-zimg 但没有 cmd_install_ffmpeg_zimg()"
        body = rt.split("cmd_install() {", 1)[1].split("cmd_install_ffmpeg_zimg", 1)[0]
        assert "--enable-libzimg" not in body, "日常 install 里不该塞编译 ffmpeg 的活（那要十几分钟）"
    print(f"  ✅ 医生点名的 {len(named)} 个 runtime.sh 子命令都真实存在：{'/'.join(sorted(named))}")


def main() -> int:
    print("▶ 平台抽象合同测试（未在 Windows 真机验证，测的是分支判定与清单一致性）")
    test_deps_platform_forks()
    test_doctor_platform_branches()
    test_scene_sandbox_refuses_on_windows()
    test_install_ps1_parity()
    test_doctor_hints_are_runnable()
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
