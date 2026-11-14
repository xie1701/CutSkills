#!/usr/bin/env python3
"""第一次跑产线之前的体检：缺什么、去哪补，一次说完。

给谁用：把这套仓库复制到自己机器上的人。跑 `python3 scripts/doctor.py`，
逐项看 ✅ / ⚠️ / ❌ / ➖，全是 ✅ 就可以开工了。它只读不改——不装任何东西。

体检五组：
  1) config.json     产线的唯一私有路径入口（没有它，脚本只认 --library 显式传参）
  2) 素材库 / CC0 库 路径在不在、索引建没建
  3) 系统依赖        ffmpeg / ffprobe / node / python3 / hyperframes，附带自编 ffmpeg 能不能真跑
  4) 产线能力        语音转写模型、节拍检测、字体、AI 场景围栏
  5) 前端运行时      gsap.min.js（渲染层拷资产用）

**四种状态**（BRIEF：不许把平台不支持报成 ❌ 让用户以为坏了）：
  ✅ 在位        —— 能用
  ⚠️ 可选缺失    —— 少了降级但不致命，红不了，只是某些能力哑掉
  ❌ 缺失        —— 必装，缺了跑不下去
  ➖ 平台不支持  —— 本平台拿不到（例如 Windows 上没有 sandbox-exec、没有 coli），
                    不是用户的错，文案里写清楚替代路径

平台判定全部走纯函数 `platform_key()` / `dep_branch()`：可以注入平台值来测
（`scripts/test_platform.py` 就是这么在非 Windows 机器上验 Windows 分支的）。
想看别的平台的判定：`CUTSKILLS_PLATFORM=win-x64 python3 scripts/doctor.py`。

退出码：0 全绿（⚠️ / ➖ 不算红）；1 有 ❌。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))   # doctor 是新用户的第一条命令，必须零环境自足（yaml 在 vendor）
REPO = HERE.parent
DEPS_JSON = REPO / "deps.json"

OK, WARN, BAD, NA = "✅", "⚠️ ", "❌", "➖"
rows: list[tuple[str, str]] = []
counts = {OK: 0, WARN: 0, BAD: 0, NA: 0}

# 平台键：与 deps.json 的 platforms.keys 同一套写法
PLATFORMS = ("darwin-arm64", "darwin-x64", "win-x64", "linux-x64")

# 每个依赖在每种平台上属于哪个分支：required / optional / unsupported。
# 这张表是**判定的唯一出处**——check 的循环只按表走，不再各处写 if sys.platform。
DEP_TABLE: dict[str, dict[str, str]] = {
    "ffmpeg":        {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    "ffprobe":       {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    "node":          {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    "python3":       {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    "hyperframes":   {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    # 语音转写：macOS arm64 有 coli 闭包；其它平台确实拿不到 → 平台不支持
    "asr":           {"darwin-arm64": "optional", "darwin-x64": "unsupported",
                      "win-x64": "unsupported", "linux-x64": "unsupported"},
    "coli":          {"darwin-arm64": "optional", "darwin-x64": "unsupported",
                      "win-x64": "unsupported", "linux-x64": "unsupported"},
    # 节拍检测是纯 stdlib + ffmpeg，四平台都该有
    "beat":          {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    "fonts":         {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    "gsap":          {"darwin-arm64": "required", "darwin-x64": "required",
                      "win-x64": "required", "linux-x64": "required"},
    # AI 场景围栏只有 macOS 的 sandbox-exec；别的平台没有等价物
    "sandbox":       {"darwin-arm64": "optional", "darwin-x64": "optional",
                      "win-x64": "unsupported", "linux-x64": "unsupported"},
}

# 「缺了怎么办」：每条依赖一句 macOS 修法 + 一句 Windows 修法。
# 不在表里的按 required 处理（将来加依赖忘了登记，至少不会静默放行）。
FIX: dict[str, tuple[str, str]] = {
    "ffmpeg": ("brew install ffmpeg zimg（HDR 要带 zscale 的构建：bash scripts/runtime.sh install）",
               "winget install Gyan.FFmpeg（装完确保 ffmpeg.exe 在 PATH）"),
    "ffprobe": ("随 ffmpeg 一起装：brew install ffmpeg",
                "随 Gyan.FFmpeg 一起装（ffprobe.exe）"),
    "node": ("brew install node（要 >= 22）", "winget install OpenJS.NodeJS.LTS"),
    "python3": ("brew install python", "winget install Python.Python.3.12（命令名是 python 或 py）"),
    "hyperframes": ("bash scripts/runtime.sh install", "install.ps1 -Install"),
    "asr": ("python3 scripts/fetch_coli.py 重抽 coli，再跑一次 `coli asr` 下模型",
            "Windows 上没有 coli：转写退 hyperframes transcribe（whisper），见 windows-selfcheck.md"),
    "coli": ("python3 scripts/fetch_coli.py（从 Cola 安装包重抽，不联网）",
             "Windows 上没有 Cola.app，coli 不可用：用 hyperframes transcribe"),
    "beat": ("装 ffmpeg 即可（beat_sync 是纯 stdlib，解码靠 ffmpeg）",
             "装 ffmpeg 即可（winget install Gyan.FFmpeg）"),
    "fonts": ("把 ttf 拷进 ~/Library/Fonts 或双击安装（得意黑 atelier-anchor.com、思源 fonts.google.com/noto）",
              "install.ps1 -Install -FontSource <ttf 目录>，或拷到 %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts 右键安装"),
    "gsap": ("把任意一份 gsap.min.js 放到 assets/gsap.min.js", "把任意一份 gsap.min.js 放到 assets\\gsap.min.js"),
    "sandbox": ("macOS 自带 /usr/bin/sandbox-exec；被删了就恢复系统组件",
                "Windows 暂不支持 AI 场景生成：改用实拍素材或内置模板，别加 --allow-unsandboxed"),
}


def platform_key(sysname: str | None = None, machine: str | None = None) -> str:
    """纯函数：sys.platform + 机器架构 → deps.json 的平台键。

    Windows 不区分架构（依赖清单里只有 win-x64 一个键）：arm64 Windows 也先按
    win-x64 判定，并在结论里提醒「未在 Windows 真机验证」。
    """
    name = (sysname if sysname is not None else sys.platform).lower()
    mach = (machine if machine is not None else platform.machine()).lower()
    if name.startswith("darwin"):
        return "darwin-arm64" if mach in ("arm64", "aarch64") else "darwin-x64"
    if name.startswith("win") or name.startswith(("cygwin", "msys")):
        return "win-x64"
    if name.startswith("linux"):
        return "linux-x64" if mach in ("x86_64", "amd64", "i686", "") else "linux-arm64"
    return "unknown"


def dep_branch(dep: str, plat: str | None = None) -> str:
    """纯函数：某依赖在某平台的分支（required / optional / unsupported）。

    平台没登记（linux-arm64 / unknown）时：表里有这一项就退回 required 并提醒，
    免得「新平台 = 静默不检查」。
    """
    key = plat or platform_key()
    row = DEP_TABLE.get(dep)
    if not row:
        return "required"
    if key in row:
        return row[key]
    return "required"


def fix_hint(dep: str, plat: str | None = None) -> str:
    """纯函数：缺了怎么办——按平台给一条具体命令。"""
    key = plat or platform_key()
    mac, win = FIX.get(dep, ("见 deps.json", "见 deps.json"))
    return win if key.startswith("win") else mac


def pathext_exts(pathext: str | None = None) -> list[str]:
    """纯函数：PATHEXT → 后缀列表。Windows 用 `;` 分隔，别的地方也可能见到 `:`。"""
    raw = pathext if pathext is not None else os.environ.get("PATHEXT", "")
    if not raw:
        raw = ".COM;.EXE;.BAT;.CMD"          # Windows 的默认值
    return [e.strip().lower() for e in re.split(r"[;:]", raw) if e.strip()]


def find_exe(name: str, *, plat: str | None = None, path: str | None = None,
             pathext: str | None = None) -> str | None:
    """跨平台找可执行文件。POSIX 上就是 shutil.which；Windows 上再补 PATHEXT 后缀。

    `which` 是 POSIX 命令，Windows 上根本没有——所以这里不用 subprocess 调 which。
    """
    key = plat or platform_key()
    search = path if path is not None else os.environ.get("PATH", "")
    hit = shutil.which(name, path=search)
    if hit:
        return hit
    if key.startswith("win") and not Path(name).suffix:
        for ext in pathext_exts(pathext):
            hit = shutil.which(name + ext, path=search)
            if hit:
                return hit
    return None


def cutskills_home() -> Path:
    raw = (os.environ.get("CUTSKILLS_HOME") or "").strip()
    return Path(raw).expanduser() if raw else (Path.home() / ".cutskills")


def add(level: str, text: str) -> None:
    counts[level] = counts.get(level, 0) + 1
    rows.append((level, text))


def expect(dep: str, plat: str, ok: bool, ok_text: str, miss_text: str,
           na_text: str | None = None) -> None:
    """按分支落行：required 缺 → ❌；optional 缺 → ⚠️；unsupported → ➖（永不报红）。

    na_text 是该平台专属的「不支持」说法——「缺失」和「平台拿不到」不是一回事，
    文案得分开说，否则 Windows 用户会以为自己装坏了。
    """
    if ok:
        add(OK, ok_text)
        return
    branch = dep_branch(dep, plat)
    if branch == "unsupported":
        add(NA, na_text or f"{miss_text}——（本平台没有，不是坏了）")
    elif branch == "optional":
        add(WARN, f"{miss_text}——{fix_hint(dep, plat)}")
    else:
        add(BAD, f"{miss_text}——{fix_hint(dep, plat)}")


def load_deps() -> dict:
    try:
        return json.loads(DEPS_JSON.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return {}


def deps_status(deps: dict, section: str, item: str, plat: str) -> dict | str | None:
    """读 deps.json 里某项在某平台的分叉；读不到返回 None（不猜）。"""
    node = (deps.get(section) or {}).get(item)
    if isinstance(node, dict) and isinstance(node.get("platforms"), dict):
        return node["platforms"].get(plat)
    return None


def font_dirs(plat: str | None = None) -> list[Path]:
    """纯函数：该平台的字体目录（与 deps.json fonts.platforms.*.dirs 同一套）。"""
    key = plat or platform_key()
    if key.startswith("darwin"):
        return [Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    if key.startswith("win"):
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        return [windir / "Fonts", local / "Microsoft/Windows/Fonts"]
    return [Path.home() / ".local/share/fonts", Path.home() / ".fonts", Path("/usr/share/fonts")]


def font_present(filename: str, plat: str | None = None) -> Path | None:
    """字体文件在不在（同名不同后缀也算：ttf / otf / ttc）。"""
    stem = Path(filename).stem
    exts = (".ttf", ".otf", ".ttc")
    for d in font_dirs(plat):
        if not d.is_dir():
            continue
        for ext in exts:
            p = d / f"{stem}{ext}"
            if p.is_file():
                return p
        for p in sorted(d.glob(f"{stem}*")):
            if p.suffix.lower() in exts:
                return p
    return None


def beat_smoke() -> tuple[bool, str]:
    """节拍检测真跑一次：合成 120 BPM 点击轨 → detect_beats。

    「清单写着有 ≠ 跑得起来」（本仓的老教训）：所以不用 import 成功就报绿，
    而是真跑一遍 Onset Flux。合成音频用 stdlib 的 wave，不额外造依赖。
    """
    try:
        import beat_sync                                  # noqa: E402
    except Exception as e:                                # noqa: BLE001
        return False, f"导入失败：{type(e).__name__}: {e}"
    try:
        with tempfile.TemporaryDirectory(prefix="doctor-beats-") as td:
            p = Path(td) / "clicks.wav"
            sr, click_s, total_s = 16000, 0.012, 4.0
            n = int(sr * total_s)
            frames = bytearray()
            for i in range(n):
                t = i / sr
                loud = (t % 0.5) < click_s            # 120 BPM = 每 0.5s 一记
                v = 12000 if loud else 0
                frames += int(v).to_bytes(2, "little", signed=True)
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(bytes(frames))
            info = beat_sync.detect_beats(p, duration_s=total_s)
    except Exception as e:                                # noqa: BLE001
        return False, f"真跑一次失败：{type(e).__name__}: {e}"
    bpm = float(info.get("bpm") or 0)
    nbeats = len(info.get("beats") or [])
    if not (95 <= bpm <= 145) or nbeats < 4:
        return False, f"读数不对：bpm={bpm:.0f}、鼓点 {nbeats} 个（合成 120 BPM 应 ≈120 / ≥4）"
    return True, f"bpm={bpm:.0f}、鼓点 {nbeats} 个（合成 120 BPM）"


def main() -> int:
    plat = platform_key()
    env_plat = (os.environ.get("CUTSKILLS_PLATFORM") or "").strip()
    if env_plat:                                         # 只影响「怎么判」，不会真的去跑别的平台
        plat = env_plat
    deps = load_deps()

    # ---- 1) config.json
    cfg_p = REPO / "config.json"
    if not cfg_p.exists():
        add(WARN, "config.json 不存在——先 `cp config.example.json config.json` 再填你的路径；"
                  "不建也行，但每条命令都得显式给 --library")
        cfg = {}
    else:
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
            add(OK, f"config.json 在位（{cfg_p}）")
        except Exception as e:                            # noqa: BLE001
            add(BAD, f"config.json 解析失败：{e}")
            cfg = {}

    # ---- 2) 素材库与 CC0 库
    from libs import config_libs  # noqa: E402
    libs = config_libs()
    if not libs:
        add(WARN, "config.json 里 libs 为空——素材库根目录（含 _cutskills/index.json 的那个）还没配")
    for lib in libs:
        idx = Path(lib) / "_cutskills" / "index.json"
        if idx.exists():
            add(OK, f"素材库 {lib}（索引在位）")
        else:
            add(BAD, f"素材库 {lib} 没建索引：跑 `python3 scripts/index_assets.py --library \"{lib}\"`")
    from cc0_lib import BGM_LIB, SFX_LIB  # noqa: E402
    for name, lib, cat in (("BGM 库", BGM_LIB, "catalog.csv"),
                           ("音效库", SFX_LIB, "catalog.csv")):
        if (lib / cat).exists():
            add(OK, f"{name} {lib}")
        else:
            add(WARN, f"{name} {lib} 没找到 catalog.csv——渲染仍可跑，只是没有垫乐/音效；"
                      f"库放哪用 config.json 的 bgm_lib / sfx_lib 指定")

    # ---- 3) 系统依赖（可执行查找走 find_exe：Windows 上补 .exe/.cmd）
    exe_why = {"ffmpeg": "抽帧/调色/混音", "ffprobe": "规格探测",
               "node": "hyperframes 渲染层", "python3": "产线本体"}
    for b, why in exe_why.items():
        p = None
        if b == "ffmpeg" and os.environ.get("CUTSKILLS_FFMPEG"):
            p = os.environ["CUTSKILLS_FFMPEG"]
        elif b == "python3" and plat.startswith("win"):
            # Windows 上没有 `python3` 这个别名：python3 / python / py 三个名字依次找
            for name in ("python3", "python", "py"):
                p = find_exe(name, plat=plat)
                if p:
                    break
        else:
            p = find_exe(b, plat=plat)
        expect(b, plat, bool(p), f"{b} → {p}（{why}）", f"{b} 缺失（{why}）")
    # node 版本要 >= 22（hyperframes 的硬要求）：光有 node 不算过
    node_p = find_exe("node", plat=plat)
    if node_p:
        try:
            r = subprocess.run([node_p, "--version"], capture_output=True, text=True, timeout=20)
            major = int(re.sub(r"[^0-9.]", "", (r.stdout or "0").strip()).split(".")[0] or 0)
        except Exception:                                 # noqa: BLE001
            major = 0
        if major and major < 22:
            add(WARN, f"node 版本 {major} < 22——hyperframes 要 >= 22（{fix_hint('node', plat)}）")

    # ---- 4) 产线能力：转写模型 / 节拍检测 / 字体 / 场景围栏
    # 4a 语音转写（coli + sensevoice）。deps.json 按平台分叉，Windows 上是 unavailable
    colihit = find_exe("coli", plat=plat)
    asr_fork = deps_status(deps, "models", "asr", plat)
    asr_status = asr_fork.get("status") if isinstance(asr_fork, dict) else None
    if asr_status == "unavailable":
        why = (asr_fork or {}).get("why") or "本平台没有 coli/sensevoice 闭包"
        add(NA, f"语音转写（coli + sensevoice）平台不支持：{why}")
    elif colihit:
        add(OK, f"coli → {colihit}（本地 ASR，字级时间戳）")
    else:
        # 文案与改前逐字一致（macOS 读数要对得上）：分支只决定 ⚠️ 还是 ❌
        _msg = ("coli 不在 PATH——整条转写链哑。有 Cola.app 就跑 `python3 scripts/fetch_coli.py` 重抽；"
                "没有它渲染会退到 hyperframes transcribe（whisper），慢且无词级真值")
        add(WARN if dep_branch("coli", plat) == "optional" else BAD, _msg)
    model_dir = Path((asr_fork or {}).get("model_dir") or
                     "~/.coli/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17").expanduser()
    if asr_status == "unavailable":
        add(NA, "sensevoice 模型不适用本平台（无 coli 可调用）")
    elif (model_dir / "model.int8.onnx").exists() and (model_dir / "tokens.txt").exists():
        add(OK, f"语音转写模型 sensevoice 在位（{model_dir}）")
    else:
        expect("asr", plat, False, "",
               "sensevoice 模型没下——首次 `coli asr` 自动下 155.5 MB（files.colaos.ai），断流见 deps.json models.asr")

    # 自编 ffmpeg：只有 POSIX 上的自建二进制才有这一行；Windows 用 Gyan build（自带 libzimg）
    ffz = cutskills_home() / "opt/ffmpeg-zimg/bin/ffmpeg"
    if plat.startswith("win"):
        add(NA, "自编 ffmpeg-zimg（HDR→SDR）不适用 Windows：用 Gyan.FFmpeg 自带 libzimg 的构建")
    elif ffz.exists():
        r = subprocess.run([str(ffz), "-version"], capture_output=True, text=True)
        add(OK if r.returncode == 0 else BAD,
            f"自编 ffmpeg（HDR→SDR）{'真跑通过' if r.returncode == 0 else '装了但跑不起来（brew 把动态库换掉了，重跑 bash scripts/runtime.sh install 修）'}")
    else:
        add(WARN, "自编 ffmpeg（HDR→SDR）没装——HLG/杜比视界素材压出来会偏亮偏灰；"
                  f"`bash scripts/runtime.sh install` 装它")

    # 4b 节拍检测：纯 stdlib + ffmpeg，但要真跑一次
    ff_ok = bool(find_exe("ffmpeg", plat=plat) or os.environ.get("CUTSKILLS_FFMPEG"))
    if not ff_ok:
        expect("beat", plat, False, "", "节拍检测跑不了（没有 ffmpeg 解码）")
    else:
        ok, detail = beat_smoke()
        expect("beat", plat, ok, f"节拍检测真跑通过：{detail}", f"节拍检测跑不起来：{detail}",
               na_text=f"节拍检测平台不支持：{detail}")

    # 4b-2 人声降噪：BRIEF 第 8 步要的「人声降噪」靠 ffmpeg 的 afftdn 滤镜。
    # 不是每个 ffmpeg 构建都带 FFT 降噪（精简版会裁掉），所以**真去滤镜表里找**，
    # 不是「装了 ffmpeg 就算有」。缺了不影响出片（--denoise 是显式开关），所以只报 ⚠️。
    if ff_ok:
        try:
            import denoise as denoise_mod                 # noqa: E402
            _dn = denoise_mod.has_afftdn()
            _chain = denoise_mod.chain_of({"nr": 12, "nf": -40, "tn": 1})
        except Exception as e:                            # noqa: BLE001
            _dn, _chain = False, f"导入失败：{type(e).__name__}: {e}"
        add(OK if _dn else WARN,
            f"人声降噪可用：ffmpeg afftdn 在位（{_chain}）" if _dn else
            "人声降噪不可用：这支 ffmpeg 没有 afftdn 滤镜——--denoise 会跳过（其余混音不受影响）；"
            "要降噪就换一支带 FFT 降噪的 ffmpeg（bash scripts/runtime.sh install 装的那支自带）")

    # 4c 字体：BRIEF 明确要求装字体，字号按宽度比反算，缺字体渲染会在 @font-face 那步停
    import fonts as fonts_mod  # noqa: E402
    table = fonts_mod.load()
    want = list((table.get("fonts") or {}).keys())
    missing = [n for n in want
               if not (font_present(str(table["fonts"][n].get("file") or ""), plat)
                       or fonts_mod.file_path(n))]
    if not want:
        add(WARN, f"字体表 styles/fonts.yaml 读不出条目——字号只能退回默认值")
    elif not missing:
        add(OK, f"字体 {len(want)}/{len(want)} 在位（{' · '.join(want)}）")
    else:
        found = [n for n in want if n not in missing]
        expect("fonts", plat, False, "",
               f"字体缺 {len(missing)}/{len(want)}（缺 {' · '.join(missing)}；"
               f"在位 {' · '.join(found) or '无'}）")

    # 4d AI 场景围栏：sandbox-exec 只有 macOS 有，别的地方连找都不必找
    sb = find_exe("sandbox-exec", plat=plat) if plat.startswith("darwin") else None
    expect("sandbox", plat, bool(sb),
           f"AI 场景围栏 sandbox-exec → {sb}（拒网跑 AI 写的场景代码）",
           "AI 场景围栏缺席：AI 写的场景代码不会被执行（scene_sandbox exit 5 拒跑）",
           na_text="AI 场景围栏平台不支持：本平台没有 sandbox-exec，AI 场景生成暂不可用"
                   "（scene_sandbox exit 5 拒跑；用实拍素材或内置模板替代）")

    # ---- 5) 前端运行时
    from render_hf import GSAP_CANDIDATES  # noqa: E402
    hit = next((p for p in GSAP_CANDIDATES() if p.is_file() and p.stat().st_size > 4096), None)
    expect("gsap", plat, bool(hit),
           f"gsap → {hit}", "gsap.min.js 找不到——新建项目渲染会在拷资产这一步退出")

    # ---- 6) 依赖自举
    npm = cutskills_home() / "npm/node_modules/hyperframes"
    expect("hyperframes", plat, npm.exists(),
           "hyperframes 已装在 $CUTSKILLS_HOME",
           "hyperframes 未装——跑 `bash scripts/runtime.sh install`（依赖全落 $CUTSKILLS_HOME，不散装）")

    print(f"CutSkills 体检（平台 {plat}）")
    for lv, text in rows:
        print(f"  {lv} {text}")
    n_bad, n_warn, n_na, n_ok = counts[BAD], counts[WARN], counts[NA], counts[OK]
    print(f"\n结论：{'可以开工' if n_bad == 0 else '先处理上面的 ❌'}"
          f"（❌ {n_bad} · ⚠️ {n_warn} · ➖ {n_na} · ✅ {n_ok}）")
    if plat.startswith("win"):
        print("说明：Windows 路径**未在 Windows 真机验证**——只提供 install.ps1 与 "
              f"{cutskills_home() / 'windows-selfcheck.md'} 自检清单；➖ 是平台不支持，不是坏了。")
    return 1 if n_bad else 0


def which(b: str) -> str | None:
    """兼容旧调用：跨平台可执行查找（不再是 subprocess 调 POSIX 的 which）。"""
    return find_exe(b)


if __name__ == "__main__":
    sys.exit(main())
