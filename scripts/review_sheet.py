#!/usr/bin/env python3
"""CutSkills · 目判工具：把成片拆成「能看的一页」，逼人（和我）真的看图。

为什么要有它：机判只能量像素和信号，量不了「这段在讲什么看得出来吗」。
过去三轮 Jerry 都说「你先逐帧审核」，而我每次只能自己猜时刻——找不全，
所以每次都漏。这个脚本把该看的时刻**生成出来**，看完才谈交付。

    review_sheet.py --project <项目> --all              # 一次出全套（推荐）
    review_sheet.py --project <项目> --sheet 16         # 全片 16 帧总览
    review_sheet.py --project <项目> --stills 12.3 30.5 # 原尺寸细节帧
    review_sheet.py --project <项目> --motion 22.1 22.6 # 某个转场区间 12 帧
    review_sheet.py --project <项目> --variants mute,subs-only

产物在 `<项目>/review/`：
    sheet.jpg      全片总览（默认 16 帧，5×4）
    stills/        原尺寸帧（名字带时刻）
    motion-*/      转场区间拼图
    mute.mp4       静音版：每一段在讲什么，看得出来吗
    subs-only.mp4  纯字幕版：只留字幕带，其余压黑——通顺吗，有哪句的名词屏上找不到
    CHECKLIST.md   12 条毛病对照表（看图时逐条对）
    manifest.json  这一页是用哪份成片、哪个时刻做的
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

CHECKLIST = """# 看图时逐条对（12 条）

| 看到什么 | 多半因为 | 改法 |
| --- | --- | --- |
| 主体小小地缩在中间 | 尺寸按「画得下」定的 | 主体占画面宽六成以上，最小可辨单元 ≥20px |
| 遮住字幕、认不出画面 | 画成了抽象记号 | 加体积与材质，或从一个人人认得的画面推进去 |
| 台词里说的名词，屏上找不到 | 分镜漏了 | 画出来，或改句子 |
| 一大片发白、发糊 | 实心大色块吃了辉光 | 拆层或降辉光 |
| 渐变上有一圈圈硬边 | 半透明叠层太少 | 多叠几层、每层更淡 |
| 东西是平的，像贴纸 | 没有明暗 | 压暗靠边、提亮受光面、垫一层极淡的底 |
| 小字压在主体上 | 标注随手放 | 拉到主体外，细引线连过去；一镜最多一条 |
| 线条伸进字幕带 | 推近时带进来了 | 该区域随景深淡出，字幕带保持干净 |
| 一句字幕里画面变了三四处理 | 贪多 | 拆成两句，或删掉次要变化 |
| 连着几句画面完全没变 | 没设计「那一处变化」 | 回编排补；但结论句停住是合法的，别误伤 |
| 转场中间有几帧说不清在干什么 | 两个状态硬插值 | 换成一个叫得出名字的动作，或压到半秒内 |
| 一秒里两样东西同时在动 | 没有主次 | 每一刻只有一个主体在动 |

## 三遍终检

1. **静音、遮住字幕看**（`mute.mp4`）：每一段在讲什么，看得出来吗？
2. **只读字幕看**（`subs-only.mp4`）：通顺吗？有哪句的名词屏上找不到吗？
3. **对门槛表**：见 `references/qa-checklist.md`。

规则：**看图改图，至少两轮**；改完重截同一时刻确认，再出全片。
"""


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def find_film(proj: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else proj / p
    cands = [x for x in (proj / "out" / "film.mp4", proj / "out" / "film-hf.mp4") if x.exists()]
    if not cands:
        raise SystemExit(f"没有成片：{proj}/out/film*.mp4")
    return max(cands, key=lambda x: x.stat().st_mtime)


def caption_band(proj: Path) -> tuple[float, float]:
    """返回 (band_top, band_h) 的归一化比例，用来裁「只有字幕」的版本。"""
    style = {}
    for f in (proj / "style.yaml",):
        if f.exists():
            style = yaml.safe_load(f.read_text()) or {}
    if not style:
        brief = proj / "brief.json"
        pack = None
        if brief.exists():
            try:
                pack = (json.loads(brief.read_text()) or {}).get("style_pack")
            except json.JSONDecodeError:
                pack = None
        p = ROOT / "styles" / f"{pack or 'talking-head-v1'}.yaml"
        if p.exists():
            style = yaml.safe_load(p.read_text()) or {}
    sub = style.get("subtitle") or {}
    size = float(sub.get("size_px") or 80)
    center = float(sub.get("center_y_px") or 1294)
    h = float((style.get("canvas") or {}).get("height") or 1920)
    top = max(0.0, (center - size * 1.6) / h)
    band = min(1.0 - top, (size * 3.2) / h)
    return round(top, 4), round(band, 4)


def grab(film: Path, at: float, dst: Path, w: int | None = None) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={w}:-2" if w else "null"
    r = run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(film),
             "-frames:v", "1", "-vf", vf, str(dst)])
    return r.returncode == 0 and dst.exists()


def tile(frames: list[Path], dst: Path, cols: int, rows: int) -> bool:
    if not frames:
        return False
    # 用 concat 滤镜不行（尺寸不一），统一走 glob + tile
    seq = dst.parent / f"_seq_{dst.stem}"
    if seq.exists():
        shutil.rmtree(seq)
    seq.mkdir(parents=True)
    for i, f in enumerate(frames[:cols * rows]):
        shutil.copy(f, seq / f"{i:03d}{f.suffix}")
    # 注意：ffmpeg 的 glob 不吃相对路径，也不吃纯 "*"——必须绝对路径 + "*.png"
    r = run(["ffmpeg", "-v", "error", "-y", "-pattern_type", "glob", "-i", str((seq / "*.png").resolve()),
             "-filter_complex", f"tile={cols}x{rows}:padding=8:margin=8:color=0x111111",
             "-frames:v", "1", str(dst)])
    return r.returncode == 0 and dst.exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--film", default=None)
    ap.add_argument("--all", action="store_true", help="一次出全套：总览 + 全片 24 帧 + 两版变体")
    ap.add_argument("--sheet", type=int, default=None, help="全片均匀 N 帧拼一张")
    ap.add_argument("--stills", nargs="*", type=float, default=None, help="原尺寸帧时刻")
    ap.add_argument("--motion", nargs=2, type=float, default=None, help="转场区间 t0 t1")
    ap.add_argument("--variants", default=None, help="mute,subs-only")
    ap.add_argument("--cols", type=int, default=5)
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    film = find_film(proj, a.film)
    out = proj / "review"
    out.mkdir(parents=True, exist_ok=True)
    (out / "CHECKLIST.md").write_text(CHECKLIST, encoding="utf-8")

    info = probe(film)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = float(info.get("format", {}).get("duration") or 0)
    W = int(v.get("width") or 0)
    H = int(v.get("height") or 0)
    manifest: dict = {"film": str(film), "duration": round(dur, 2), "size": f"{W}x{H}",
                      "made": [], "items": {}}

    n_sheet = a.sheet if a.sheet else (24 if a.all else None)
    if n_sheet:
        times = [round(dur * (i + 0.5) / n_sheet, 2) for i in range(n_sheet)]
        d = out / f"_sheet{n_sheet}"
        frames = []
        for i, tt in enumerate(times):
            f = d / f"{i:03d}.png"
            if grab(film, tt, f, 270):
                frames.append(f)
        rows = max(1, (len(frames) + a.cols - 1) // a.cols)
        cols = min(a.cols, max(1, len(frames)))
        name = "sheet.jpg" if n_sheet == 24 else f"sheet-{n_sheet}.jpg"
        ok = tile(frames, out / name, cols, rows)
        manifest["items"]["sheet"] = {"file": name, "frames": len(frames), "times": times,
                                      "grid": f"{cols}x{rows}"}
        if ok:
            manifest["made"].append(name)
        print(f"总览：{out / name}  ({len(frames)} 帧 · {cols}×{rows})")

    if a.stills:
        d = out / "stills"
        got = [t for t in a.stills if grab(film, t, d / f"{t:07.2f}.png")]
        manifest["items"]["stills"] = {"dir": "stills", "times": got}
        manifest["made"].append(f"stills/{len(got)}")
        print(f"细节帧：{d}  ({len(got)} 张原尺寸)")

    if a.motion:
        t0, t1 = a.motion
        n = 12
        times = [round(t0 + (t1 - t0) * i / max(1, n - 1), 3) for i in range(n)]
        d = out / f"_motion_{t0:.1f}-{t1:.1f}"
        frames = []
        for i, tt in enumerate(times):
            f = d / f"{i:03d}.png"
            if grab(film, tt, f, 270):
                frames.append(f)
        name = f"motion-{t0:.1f}-{t1:.1f}.jpg"
        ok = tile(frames, out / name, 4, 3)
        manifest["items"]["motion"] = {"file": name, "t0": t0, "t1": t1, "frames": len(frames)}
        if ok:
            manifest["made"].append(name)
        print(f"转场：{out / name}  ({len(frames)} 帧 · {t0:.1f}–{t1:.1f}s)")

    want = (a.variants or ("mute,subs-only" if a.all else "")).split(",")
    if want and any(want):
        if "mute" in want:
            dst = out / "mute.mp4"
            r = run(["ffmpeg", "-v", "error", "-y", "-i", str(film), "-an", "-c:v", "copy", str(dst)])
            if r.returncode == 0:
                manifest["made"].append("mute.mp4")
                print(f"静音版：{dst}（每段在讲什么，看得出来吗）")
        if "subs-only" in want:
            top, band = caption_band(proj)
            dst = out / "subs-only.mp4"
            y = int(round(top * H))
            h = max(2, int(round(band * H)))
            vf = f"crop={W}:{h}:0:{y},pad={W}:{H}:0:{y}:black"
            r = run(["ffmpeg", "-v", "error", "-y", "-i", str(film), "-vf", vf,
                     "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p", "-c:a", "copy", str(dst)])
            if r.returncode == 0:
                manifest["made"].append("subs-only.mp4")
                print(f"纯字幕版：{dst}（只留字幕带 y={y} h={h}，其余压黑）")
            else:
                print(f"！纯字幕版失败：{r.stderr[-200:]}", file=sys.stderr)

    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"清单：{out / 'manifest.json'}")
    print(f"对照表：{out / 'CHECKLIST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
