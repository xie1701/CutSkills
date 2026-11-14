#!/usr/bin/env python3
"""一张卡/一格代码画面的快速样张：改完立刻看，不用重渲整片。

为什么要它：调天平卡那一轮，每次改 SASS/SVG 都要重渲 78 秒的片（浏览器跑 3 分半），
一轮 4 分钟里有 3 分 50 秒在渲别人的镜头。这个工具只把**那一格**放进最小页面里
截图，一次 6–10 秒，改十轮也不心疼。

它不去猜现状：**用的是和成片同一份 `gen_html` + `build_html` + 同一套字体与 token**，
所以截到的像素就是成片里会出现的像素。唯一不同的是背后没有实拍底片（合成暗底）。

用法：
    python3 scripts/preview_card.py --project <项目> --n 15 --at 0.9,3.4
    python3 scripts/preview_card.py --gen '<元素 JSON>' --pack P1-warm --at 1,3
    # 元素 JSON 就是 timeline.json 里那一条（要有 at / dur / template / props）

产出：/tmp/preview-<项目>/gen-<n>-<t>.png（这是过程文件，默认不写进项目；--out 可指定）
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

W, H, FPS = 1080, 1920, 30


def _hf() -> str:
    for c in (Path.home() / ".cutskills/npm/node_modules/.bin/hyperframes",
              Path("/opt/homebrew/bin/hyperframes")):
        if c.exists():
            return str(c)
    return "hyperframes"


def _gsap() -> Path | None:
    for c in (ROOT / "assets" / "gsap.min.js",
              ROOT / "demo" / "pack-micro" / "hf" / "assets" / "gsap.min.js"):
        if c.exists():
            return c
    return None


def _pack(name: str) -> dict:
    import yaml  # noqa: PLC0415
    p = ROOT / "packs" / f"{name}.yaml"
    if not p.exists():
        raise SystemExit(f"没有这个风格包：{p}")
    return yaml.safe_load(p.read_text())


def build(work: Path, el: dict, pack: dict, total: float) -> Path:
    import render_hf as rh  # noqa: PLC0415

    (work / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    fonts = {}
    for key, nm in (("sub", (pack.get("subtitle") or {}).get("font")),
                    ("big", (pack.get("emphasis") or {}).get("font"))):
        f = rh.find_font(nm)
        if f is None:
            raise SystemExit(f"字体不在盘上：{nm}")
        shutil.copy2(f, work / "assets" / "fonts" / f.name)
        fonts[key] = f.name
    g = _gsap()
    if g:
        shutil.copy2(g, work / "assets" / "gsap.min.js")
    gen = rh.gen_html(el, H, total=total)
    html = rh.build_html(slug="preview", W=W, H=H, FPS=FPS, total=total,
                         videos=[], caps=[], cards=[], style=pack, fonts=fonts,
                         grade="none", gens=[gen])
    (work / "index.html").write_text(html)
    (work / "meta.json").write_text(json.dumps(
        {"style": "preview", "total_s": total, "cards": 0, "captions": 0,
         "engine": "hyperframes", "note": "单格样张"}, ensure_ascii=False))
    return work / "index.html"


def main() -> int:
    ap = argparse.ArgumentParser(description="单格代码画面的快速样张")
    ap.add_argument("--project", default=None, help="项目目录（从 timeline.json 取第 n 格）")
    ap.add_argument("--n", type=int, default=None, help="取第几格（timeline 的 n）")
    ap.add_argument("--gen", default=None, help="直接给元素 JSON")
    ap.add_argument("--pack", default="P1-warm")
    ap.add_argument("--at", default="1.0", help="相对卡片起点的秒数，逗号分隔")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dur", type=float, default=4.0, help="--gen 模式下的卡片时长")
    a = ap.parse_args()

    if a.gen:
        el = json.loads(a.gen)
        el.setdefault("dur", a.dur)
        el.setdefault("at", 0.2)
        el.setdefault("track", "overlay")
        out = Path(a.out or "/tmp").expanduser()
    else:
        if not a.project or a.n is None:
            raise SystemExit("要么给 --project 配 --n，要么给 --gen '<JSON>'")
        proj = Path(a.project).expanduser()
        tl = json.loads((proj / "timeline.json").read_text())
        el = next((e for e in tl["elements"] if int(e.get("n") or 0) == a.n), None)
        if el is None:
            raise SystemExit(f"timeline 里没有 #n={a.n}")
        if el.get("kind") != "gen":
            raise SystemExit(f"#n={a.n} 不是 gen（kind={el.get('kind')}），样张只渲代码画面")
        out = Path(a.out or (Path("/tmp") / f"preview-{proj.name}")).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    at = [float(v) for v in a.at.split(",") if v.strip()]
    total = float(el.get("at") or 0) + float(el.get("dur") or 4) + 1.0

    with tempfile.TemporaryDirectory(prefix="cutskills-preview-") as _td:
        work = Path(_td) / "page"
        work.mkdir()
        build(work, el, _pack(a.pack), total)
        shots = Path(_td) / "shots"
        cmd = [_hf(), "snapshot", str(work), "-o", str(shots),
               "--at", ",".join(f"{float(el.get('at') or 0) + t:g}" for t in at),
               "--no-end", "--keep"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work))
        if r.returncode != 0:
            raise SystemExit("snapshot 失败：\n" + ((r.stdout or "") + (r.stderr or "")).strip()[-1200:])
        made = []
        for t, src in zip(at, sorted(shots.glob("*.png"))):
            dst = out / f"gen-{el.get('n') or el.get('template')}-{t:g}.png"
            shutil.copy2(src, dst)
            made.append(dst)
        for p in made:
            print(f"→ {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
