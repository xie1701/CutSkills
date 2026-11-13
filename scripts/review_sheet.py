#!/usr/bin/env python3
"""生成逐格 IN/MID/OUT 审片接触表与卡片原像素裁图。

只读时间轴、已渲成片及渲染器写出的 out/cards.json；只在项目 review/ 下新增审片文件，
不重渲、不改时间轴、字幕、卡片或验收报告。卡片图像以 PNG 无损裁切，绝不缩成缩略图。

用法：
  python3 scripts/review_sheet.py --project <项目目录>
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from timeline import element_dur  # noqa: E402
from card_layout import MG_KINDS, KW_KINDS as _KW_TUPLE  # noqa: E402

KW_KINDS = set(_KW_TUPLE)


def load_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def ffmpeg_bin() -> str:
    return os.environ.get("CUTSKILLS_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def probe_video(path: Path) -> tuple[int, int, float]:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    p = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,duration:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"ffprobe 失败：{p.stderr.strip()[-300:]}")
    d = json.loads(p.stdout or "{}")
    streams = d.get("streams") or []
    if not streams:
        raise RuntimeError(f"成片没有视频流：{path}")
    st = streams[0]
    duration = float(st.get("duration") or (d.get("format") or {}).get("duration") or 0)
    return int(st["width"]), int(st["height"]), duration


def frame_bytes(film: Path, at: float) -> bytes:
    ffmpeg = ffmpeg_bin()
    p = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, at):.4f}",
                        "-i", str(film), "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "pipe:1"], capture_output=True)
    if p.returncode or not p.stdout:
        raise RuntimeError(f"抽帧失败 @ {at:.3f}s：{p.stderr.decode(errors='replace')[-300:]}")
    return p.stdout


def rect_xywh(value, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = (int(round(float(v))) for v in value)
    except (TypeError, ValueError):
        return None
    # cards.json 的 *_px 字段和 card_layout 都是 [x, y, width, height]。
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def pack_style(project: Path, timeline: dict) -> dict:
    pack_id = str(timeline.get("pack") or timeline.get("style_pack") or "").strip()
    meta = load_json(project / "hf" / "meta.json", {}) or {}
    pack_id = pack_id or str(meta.get("style") or "").strip()
    for base in (ROOT / "packs", ROOT / "styles"):
        f = base / f"{pack_id}.yaml"
        if pack_id and f.exists():
            import yaml
            return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {}


def card_crop_box(card: dict, style: dict, width: int, height: int) -> tuple[int, int, int, int] | None:
    kind = str(card.get("kind") or "")
    if kind in MG_KINDS:
        layout = card.get("layout") or {}
        # plate_px 是 .mgplate 的真实落点；再纳入羽化/投影范围，避免把卡边缘效果裁掉。
        raw = rect_xywh(layout.get("plate_px"), width, height)
        if raw:
            pad = max(0, int(round(float(layout.get("feather_px") or 0))))
            x0, y0, x1, y1 = raw
            return (max(0, x0 - pad), max(0, y0 - pad),
                    min(width, x1 + pad), min(height, y1 + pad))
        # 老产物没有 plate_px 时退到卡本体 box_px，并预留边缘空间。
        raw = rect_xywh(layout.get("box_px"), width, height)
        if raw:
            x0, y0, x1, y1 = raw
            return (max(0, x0 - 24), max(0, y0 - 24),
                    min(width, x1 + 24), min(height, y1 + 24))
        return None
    if kind in KW_KINDS:
        # 优先使用渲染器写进 cards.json 的真实像素几何。
        got = rect_xywh(card.get("kw_box_px"), width, height)
        if got:
            x0, y0, x1, y1 = got
            # 留出文字投影/柔边，几何中心仍完全来自渲染器真实字框。
            return (max(0, x0 - 24), max(0, y0 - 24),
                    min(width, x1 + 24), min(height, y1 + 24))
        # 旧产物缺 kw_box_px 时用渲染器/判据共用的字体几何函数补算，不另写估算规则。
        try:
            import card_layout as CL
            top_h = card.get("kw_top_h")
            if top_h is None:
                top_h = float(card.get("top_h") or (style.get("emphasis") or {}).get("top_h") or 0.44)
            geo = CL.kw_ink_box(str(card.get("text") or ""), style, width, height, float(top_h))
            if geo:
                return rect_xywh(geo.get("px"), width, height)
        except Exception:  # noqa: BLE001 - 页面会如实列出几何缺失，不伪造裁图
            return None
    return None


def shot_rows(elements: list[dict], film_duration: float) -> list[dict]:
    rows, cursor = [], 0.0
    for pos, el in enumerate(elements, 1):
        if el.get("track") == "overlay":
            continue
        try:
            dur = float(element_dur(el))
        except (KeyError, TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            continue
        start, end = cursor, cursor + dur
        # 按需求固定的入/中/出采样点；对于极短格夹到格内，避免越界。
        times = {
            "in": min(end, start + min(0.1, dur / 2)),
            "mid": start + dur / 2,
            "out": max(start, end - min(0.1, dur / 2)),
        }
        for k, t in times.items():
            if t >= film_duration:
                raise RuntimeError(f"#{el.get('n', pos)} {k} 帧 {t:.3f}s 超出成片 {film_duration:.3f}s")
        rows.append({"n": el.get("n", pos), "kind": el.get("kind"),
                     "medium": str(el.get("medium") or el.get("template") or el.get("kind") or "未知"),
                     "start": start, "end": end, "dur": dur, "times": times})
        cursor = end
    return rows


def generate(project: Path, *, force: bool = False) -> Path:
    project = project.expanduser().resolve()
    timeline = load_json(project / "timeline.json")
    if not isinstance(timeline, dict) or not isinstance(timeline.get("elements"), list):
        raise RuntimeError("项目缺少有效 timeline.json / elements")
    out = project / "out"
    cards_doc = load_json(out / "cards.json", {}) or {}
    cards = cards_doc if isinstance(cards_doc, list) else list(cards_doc.get("cards") or [])
    film = next((p for p in (out / "film-hf.mp4", out / "film.mp4") if p.exists()), None)
    if film is None:
        raise RuntimeError("找不到 out/film-hf.mp4 或 out/film.mp4；先提供已渲成片")
    W, H, film_duration = probe_video(film)
    if W < 1080 or H < 1920:
        raise RuntimeError(f"成片只有 {W}×{H}，无法满足 1080p 原像素审片要求")
    review = project / "review"
    # 不覆盖他人/用户已有的内容。新建的空 review/ 可安全使用；已有 sheet 或同名帧则停止。
    expected = [review / "sheet.html"]
    rows = shot_rows(timeline["elements"], film_duration)
    expected += [review / "frames" / f"cell-{int(r['n']):03d}-{k}.png"
                 for r in rows for k in ("in", "mid", "out")]
    expected += [review / "cards" / f"card-{i:02d}-seg{int(c.get('seg') or i)}.png"
                 for i, c in enumerate(cards, 1) if str(c.get("kind") or "") in MG_KINDS | KW_KINDS]
    collision = [p for p in expected if p.exists()]
    if collision and not force:
        raise RuntimeError("拒绝覆盖已存在的审片产物；确认后可用 --force 更新："
                            + ", ".join(str(p) for p in collision[:4]))
    review.mkdir(parents=True, exist_ok=True)
    frames_dir, cards_dir = review / "frames", review / "cards"
    frames_dir.mkdir(exist_ok=True)
    cards_dir.mkdir(exist_ok=True)

    frame_paths: dict[tuple[int, str], str] = {}
    for row in rows:
        for tag, at in row["times"].items():
            rel = f"frames/cell-{int(row['n']):03d}-{tag}.png"
            (review / rel).write_bytes(frame_bytes(film, at))
            frame_paths[(int(row["n"]), tag)] = rel

    style = pack_style(project, timeline)
    card_rows = []
    for i, card in enumerate(cards, 1):
        kind = str(card.get("kind") or "")
        if kind not in MG_KINDS | KW_KINDS:
            continue
        box = card_crop_box(card, style, W, H)
        name = f"card-{i:02d}-seg{int(card.get('seg') or i)}.png"
        if box is None:
            card_rows.append({"name": name, "kind": kind, "text": str(card.get("text") or ""),
                              "issue": "cards.json 缺少可验证的渲染几何，未生成假位置裁图"})
            continue
        start = float(card.get("start") or 0.0)
        end = float(card.get("end") or start)
        at = max(0.0, min(film_duration - 1e-3, (start + end) / 2))
        im = Image.open(BytesIO(frame_bytes(film, at))).convert("RGB")
        if im.size != (W, H):
            raise RuntimeError(f"抽出的成片帧尺寸异常：{im.size} != {(W, H)}")
        crop = im.crop(box)
        crop.save(cards_dir / name, format="PNG", optimize=False)
        card_rows.append({"name": f"cards/{name}", "kind": kind,
                          "text": str(card.get("text") or ""), "seg": card.get("seg"),
                          "mg": card.get("mg") or f"mg-{card.get('seg')}", "at": at,
                          "box": box, "size": crop.size})

    cell_html = []
    for row in rows:
        imgs = []
        for tag in ("in", "mid", "out"):
            at = row["times"][tag]
            src = frame_paths[(int(row["n"]), tag)]
            imgs.append(f'<figure><figcaption>{tag.upper()} · {at:.3f}s</figcaption>'
                        f'<a href="{html.escape(src)}"><img src="{html.escape(src)}" '
                        f'alt="cell {row["n"]} {tag}" width="{W}" height="{H}"></a></figure>')
        cell_html.append(
            f'<section class="cell"><header>格 #{html.escape(str(row["n"]))} · '
            f'{row["dur"]:.3f}s · medium: {html.escape(row["medium"])} · '
            f'成片 {row["start"]:.3f}–{row["end"]:.3f}s</header><div class="frames">'
            + "".join(imgs) + "</div></section>")

    card_html = []
    for row in card_rows:
        label = f"{row.get('mg', '卡')} · #{row.get('seg')} · {row.get('kind')} · {row.get('text', '')}"
        if row.get("issue"):
            card_html.append(f'<section class="card missing"><h3>{html.escape(label)}</h3>'
                             f'<p>{html.escape(row["issue"])}</p></section>')
            continue
        rel = row["name"]
        cw, ch = row["size"]
        card_html.append(
            f'<section class="card"><h3>{html.escape(label)}</h3>'
            f'<p>成片 {row["at"]:.3f}s · 原始裁框 {row["box"]} · '
            f'PNG {cw}×{ch}（1:1 原像素；点击打开原图）</p>'
            f'<a href="{html.escape(rel)}"><img class="original" src="{html.escape(rel)}" '
            f'width="{cw}" height="{ch}" style="width:{cw}px;height:{ch}px" '
            f'alt="{html.escape(label)}"></a></section>')

    doc = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>审片接触表 · __PROJECT__</title><style>
body{font:16px/1.5 system-ui,-apple-system,sans-serif;margin:24px;background:#171717;color:#eee}
h1,h2{margin:0 0 16px}.note{color:#bbb}.cell,.card{border:1px solid #555;border-radius:8px;padding:14px;margin:16px 0;background:#222}
.cell header{font-weight:700;margin-bottom:10px}.frames{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;overflow-x:auto}
figure{margin:0}figcaption{font-weight:700;margin-bottom:6px}figure img{display:block;width:min(100%,360px);height:auto;image-rendering:auto;border:1px solid #666}
.cards{overflow-x:auto}.card{width:max-content;max-width:none}.card img.original{display:block;max-width:none;max-height:none;image-rendering:auto}
.card p{color:#bbb}.missing{border-color:#a66;color:#ffc}.small{font-size:13px;color:#aaa}
</style></head><body>
<h1>审片接触表 · __PROJECT__</h1>
<p class="note">成片：__FILM__ · __WIDTH__×__HEIGHT__ · 全 __CELLS__ 格 · 采样 IN/MID/OUT = 入点+0.1s / 中点 / 出点−0.1s。MG/大字卡裁图来自渲染几何，PNG 无损裁切，不生成 320px 卡面缩略图。</p>
<h2>全片逐格三帧</h2>__CELLS_HTML__
<h2>MG / 大字卡 · 1080p 原像素裁图</h2><p class="small">每张卡均保留裁图原始像素尺寸；点图打开 PNG 原件。若某张几何缺失，页面明确列出且不猜位置。</p><div class="cards">__CARDS_HTML__</div>
</body></html>"""
    doc = (doc.replace("__PROJECT__", html.escape(project.name))
           .replace("__FILM__", html.escape(str(film)))
           .replace("__WIDTH__", str(W)).replace("__HEIGHT__", str(H))
           .replace("__CELLS__", str(len(rows)))
           .replace("__CELLS_HTML__", "\n".join(cell_html))
           .replace("__CARDS_HTML__", "\n".join(card_html)))
    sheet = review / "sheet.html"
    sheet.write_text(doc, encoding="utf-8")
    no_crop = sum(1 for c in card_rows if c.get("issue"))
    print(f"审片接触表：{sheet}")
    print(f"  格 {len(rows)} · IN/MID/OUT {len(rows) * 3} 帧 · 卡 {len(card_rows) - no_crop}/{len(card_rows)} 张有几何裁图")
    if no_crop:
        print(f"  注意：{no_crop} 张卡缺渲染几何，页面标为未裁切，未猜位置")
    return sheet


def main() -> int:
    ap = argparse.ArgumentParser(description="全片逐格 IN/MID/OUT + MG 卡 1080p 原像素裁图")
    ap.add_argument("--project", required=True, help="项目目录，只读时间轴与已渲产物")
    ap.add_argument("--force", action="store_true", help="更新已有 review/sheet.html 与同名生成帧")
    a = ap.parse_args()
    try:
        generate(Path(a.project), force=a.force)
    except Exception as exc:  # noqa: BLE001
        print(f"review_sheet: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
