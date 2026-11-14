#!/usr/bin/env python3
"""第一次跑产线之前的体检：缺什么、去哪补，一次说完。

给谁用：把这套仓库复制到自己机器上的人。跑 `python3 scripts/doctor.py`，
逐项看 ✅ / ⚠️ / ❌，全是 ✅ 就可以开工了。它只读不改——不装任何东西。

体检四组：
  1) config.json     产线的唯一私有路径入口（没有它，脚本只认 --library 显式传参）
  2) 素材库 / CC0 库 路径在不在、索引建没建
  3) 系统依赖        ffmpeg / ffprobe / node / python3 /coli，附带自编 ffmpeg 能不能真跑
  4) 前端运行时      gsap.min.js（渲染层拷资产用）

退出码：0 全绿（⚠️ 不算红）；1 有 ❌。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))   # doctor 是新用户的第一条命令，必须零环境自足（yaml 在 vendor）
REPO = HERE.parent

OK, WARN, BAD = "✅", "⚠️ ", "❌"
rows: list[tuple[str, str]] = []
has_bad = False


def add(level: str, text: str) -> None:
    global has_bad
    if level == BAD:
        has_bad = True
    rows.append((level, text))


def main() -> int:
    # ---- 1) config.json
    cfg_p = REPO / "config.json"
    if not cfg_p.exists():
        add(WARN, "config.json 不存在——先 `cp config.example.json config.json` 再填你的路径；"
                  "不建也行，但每条命令都得显式给 --library")
        cfg = {}
    else:
        import json
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
            add(OK, f"config.json 在位（{cfg_p}）")
        except Exception as e:
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

    # ---- 3) 系统依赖
    for b, why in (("ffmpeg", "抽帧/调色/混音"), ("ffprobe", "规格探测"),
                   ("node", "hyperframes 渲染层"), ("python3", "产线本体")):
        p = None
        for cand in ([os.environ["CUTSKILLS_FFMPEG"]] if b == "ffmpeg" and os.environ.get("CUTSKILLS_FFMPEG") else []):
            p = cand
        if not p:
            p = which(b)
        if p:
            add(OK, f"{b} → {p}（{why}）")
        else:
            add(BAD, f"{b} 缺失（{why}）——ffmpeg 建议 brew install ffmpeg zimg，"
                     f"HDR 素材要自编带 zimg 的版本——bash scripts/runtime.sh install 会装它")
    coli = which("coli")
    if coli:
        add(OK, f"coli → {coli}（本地 ASR，字级时间戳）")
    else:
        add(WARN, "coli 不在 PATH——整条转写链哑。有 Cola.app 就跑 `python3 scripts/fetch_coli.py` 重抽；"
                  "没有它渲染会退到 hyperframes transcribe（whisper），慢且无词级真值")
    ffz = Path(os.environ.get("CUTSKILLS_HOME", str(Path.home() / ".cutskills"))) / "opt/ffmpeg-zimg/bin/ffmpeg"
    if ffz.exists():
        r = subprocess.run([str(ffz), "-version"], capture_output=True, text=True)
        add(OK if r.returncode == 0 else BAD,
            f"自编 ffmpeg（HDR→SDR）{'真跑通过' if r.returncode == 0 else '装了但跑不起来（brew 把动态库换掉了，重跑 bash scripts/runtime.sh install 修）'}")

    # ---- 4) 前端运行时
    from render_hf import GSAP_CANDIDATES  # noqa: E402
    hit = next((p for p in GSAP_CANDIDATES() if p.is_file() and p.stat().st_size > 4096), None)
    if hit:
        add(OK, f"gsap → {hit}")
    else:
        add(BAD, "gsap.min.js 找不到——新建项目渲染会在拷资产这一步退出；放一份到 assets/gsap.min.js")

    # ---- 5) 依赖自举
    npm = Path(os.environ.get("CUTSKILLS_HOME", str(Path.home() / ".cutskills"))) / "npm/node_modules/hyperframes"
    if npm.exists():
        add(OK, "hyperframes 已装在 $CUTSKILLS_HOME")
    else:
        add(WARN, "hyperframes 未装——跑 `bash scripts/runtime.sh install`（依赖全落 $CUTSKILLS_HOME，不散装）")

    print("CutSkills 体检")
    for lv, text in rows:
        print(f"  {lv} {text}")
    n_bad = sum(1 for lv, _ in rows if lv == BAD)
    n_warn = sum(1 for lv, _ in rows if lv.startswith("⚠"))
    print(f"\n结论：{'可以开工' if not has_bad else '先处理上面的 ❌'}"
          f"（❌ {n_bad} · ⚠️ {n_warn} · ✅ {len(rows) - n_bad - n_warn}）")
    return 1 if has_bad else 0


def which(b: str) -> str | None:
    r = subprocess.run(["which", b], capture_output=True, text=True)
    return r.stdout.strip() or None


if __name__ == "__main__":
    sys.exit(main())
