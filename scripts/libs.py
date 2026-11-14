#!/usr/bin/env python3
"""库、项目与运行环境的唯一解析入口。

别人复制这份仓库后只改一件事：把 config.example.json 复制成 config.json，
填自己的素材库 / 外置音乐音效库 / 产出目录。脚本里不许再出现任何人的家目录，
所有 ~/.cutskills 路径一律通过 cutskills_home() 解析（尊重 $CUTSKILLS_HOME）。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "config.json"


def cutskills_home() -> Path:
    """统一依赖根目录：优先 $CUTSKILLS_HOME，默认 ~/.cutskills。"""
    raw = (os.environ.get("CUTSKILLS_HOME") or "").strip()
    return Path(raw).expanduser() if raw else (Path.home() / ".cutskills")


def ffmpeg_bin() -> str:
    """优先用 $CUTSKILLS_HOME 里带 zscale/libzimg 的 ffmpeg，否则退 PATH。"""
    cand = cutskills_home() / "opt" / "ffmpeg-zimg" / "bin" / "ffmpeg"
    if cand.exists() and os.access(cand, os.X_OK):
        return str(cand)
    return shutil.which("ffmpeg") or "ffmpeg"


def hf_bin() -> str:
    """本地装好的 hyperframes 优先，绝不默认走联网 npx。"""
    env = (os.environ.get("CUTSKILLS_HF") or "").strip()
    if env:
        return env
    for c in (
        cutskills_home() / "npm" / "node_modules" / ".bin" / "hyperframes",
        REPO / "node_modules" / ".bin" / "hyperframes",
        Path("/opt/homebrew/bin/hyperframes"),
    ):
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return shutil.which("hyperframes") or "npx --yes hyperframes"


def resolve_lib(proj: Path, given: str | Path | None = None, *, strict: bool = False) -> Path:
    """项目 → 素材库根目录（单一真值源）。

    优先级：显式 `--library` > 沿祖先目录向上找 `_cutskills/index.json` > 环境变量 `CUTSKILLS_LIB`。
    """
    if given:
        return Path(given).expanduser().resolve()
    for up in (proj, *proj.parents):
        if (up / "_cutskills" / "index.json").exists():
            return up
    env = os.environ.get("CUTSKILLS_LIB")
    if env and (Path(env).expanduser() / "_cutskills" / "index.json").exists():
        return Path(env).expanduser().resolve()
    cand = proj.parents[2] if len(proj.parents) > 2 else proj
    if strict:
        raise SystemExit(
            f"找不到素材库：{cand}/_cutskills/index.json 不存在。\n"
            f"  项目默认建在 <库>/_cutskills/projects/<slug> 下；\n"
            f"  放在别处请加 --library \"<素材库根目录>\"。"
        )
    return cand


def load_config() -> dict:
    """读 config.json（无则返回空 dict）。"""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _expand(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    return [str(Path(x).expanduser()) for x in v if str(x).strip()]


def config_libs() -> list[str]:
    return _expand(load_config().get("libs"))


def config_out_dir() -> str:
    d = str(load_config().get("out_dir") or "").strip()
    return str(Path(d).expanduser()) if d else str(Path.home() / "CutSkills-产出")


DEFAULT_LIBS = config_libs() or _expand(os.environ.get("CUTSKILLS_LIB"))

PROJECT_NOTES: dict[str, str] = {
    "dianzan": "「点赞茶楼」· 珠海金湾 600㎡ 粤语探店/老板口播",
    "qizao-ground": "柒造 · 地面施工（进口 vs 国产漆对比）",
    "qizao-protect": "柒造 · 成品保护与收边工艺",
    "qizao-naming": "柒造 · 品牌名字来历",
    "qizao-lime-baxis": "柒造 · B 轴连续代码叙事场景 + 人物画中画",
    "selftest": "全链路自检合成样片",
}


def _pick(text: str) -> str:
    for key in sorted(PROJECT_NOTES, key=len, reverse=True):
        if text.startswith(key):
            return PROJECT_NOTES[key]
    return ""


def find_projects(libs: list[str]) -> list[Path]:
    out: list[Path] = []
    for lib in libs:
        base = Path(lib).expanduser()
        if not base.is_dir():
            continue
        cand = base / "_cutskills" / "projects"
        if cand.is_dir():
            out += [d for d in sorted(cand.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        if (base / "timeline.json").exists() or (base / "out").is_dir():
            out.append(base)
        else:
            for sub in sorted(base.iterdir()):
                if sub.is_dir() and (sub / "out").is_dir() and (sub / "timeline.json").exists():
                    out.append(sub)
    seen, uniq = set(), []
    for p in out:
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        uniq.append(p)
    return uniq


def find_project(slug: str) -> Path | None:
    for lib in config_libs():
        p = Path(lib) / "_cutskills" / "projects" / slug
        if p.is_dir():
            return p
    return None
