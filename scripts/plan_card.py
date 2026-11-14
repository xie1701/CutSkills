#!/usr/bin/env python3
"""CutSkills · 剪辑与包装方案卡（动手渲染前的人类拍板门禁）。

原则：渲染一次视频要跑浏览器与编码，改文字方案只需几秒钟。
所以在正式渲染前，先把「每句说什么、配什么画面、挂什么标题条/大字/动效卡/表情贴纸/音效」
整理成一张没有黑话的清晰方案卡。用户确认（`--approve`）后写入 `lock.json` 时间轴哈希，
`render_hf.py` 验过锁才放行渲染。

拍板 ② 的形态是「锁定音轨 + 低清粗剪预览」（BRIEF 的三次打断之一），所以这里的
`--preview` 不是可选装饰：它把这条片渲一版**降级**的低清粗剪（半分辨率、低码率、
`--quality draft`），并在锁里同时钉住两样东西——

  · 整条时间轴的字节哈希（`lock.json` 的 sha256，改一个字节就失效，口径与 `lock.py` 一致）
  · **音轨指纹**（timeline.json 里「每格用哪段音、几分到几分」那一层的 SHA）

两者的分工是回答一句话问题：**只改画面要不要重新确认音频？** 不要——改画面后音轨指纹
一个字符都不变；改口播（换音源、挪切点、改时长）指纹就变，锁也得重签。

低清预览是预览，不是成片：画面上烧了「粗剪预览 · 未过门禁」的条，`preview.json` 里写着
降级参数与实测分辨率/码率，方案卡上也标红——不许拿它当交付物。

用法：
  plan_card.py --project <项目目录> [--show]
  plan_card.py --project <项目目录> --preview      # 渲低清粗剪预览 + 重写方案卡
  plan_card.py --project <项目目录> --approve "认可"

退出码：0 成 / 1 拒绝签锁（人工拍板时预览与当前时间轴不是同一版）/ 2 预览渲染失败
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))
try:
    import yaml
except ImportError:
    yaml = None

from libs import ffmpeg_bin, resolve_lib            # noqa: E402

MARK = "<!-- plan-card: v2 -->"

# 低清粗剪预览：全部是**明确降级**的参数，一个都不许悄悄跟着成片走。
PREVIEW_NAME = "preview-roughcut.mp4"
PREVIEW_META = "preview.json"
PREVIEW_QUALITY = "draft"          # 渲器（hyperframes）的草稿档
PREVIEW_SCALE = 0.5               # 半分辨率：1080x1920 → 540x960
PREVIEW_CRF = 30                  # 成片走 looknorm 前是 crf18 档，这里刻意松
PREVIEW_MAXRATE = "900k"
PREVIEW_ABR = "96k"
PREVIEW_BANNER = "粗剪预览 · 未过门禁"
# 临时工程里要软链过去的只读输入（渲器只读它们；out/ 与 hf/ 一律新建，绝不碰真项目）
PREVIEW_LINKS = ("timeline.json", "brief.json", "plan.json", "bgm.json", "bgm.wav",
                 "audio-map.json", "subs-en.json", "intent.json", "scenes")

MG_LABEL = {
    "stat": "数字放大卡",
    "steps": "步骤流程卡",
    "versus": "双栏对比卡",
    "term": "名词解释卡",
    "proportion": "比例切分条",
    "day-night": "早晚接力卡",
    "chips": "招牌名牌卡",
}

VFX_LABEL = {
    "punch_zoom": "冲击推镜+闪光",
    "slow_push": "电影感缓推",
    "flash_cut": "光学闪切",
    "spotlight": "聚光微暗角",
}


# ---------------------------------------------------------------- 音轨指纹

def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except (json.JSONDecodeError, OSError):
        return default


def audio_slice(tl: dict) -> list[dict]:
    """时间轴里「决定声音」的那一层：每格用哪段音、从几分到几分。

    画面那一层（asset / card / vfx / sticker / 文字）一个字段都不进来——
    否则「只改画面，音轨不动」这句话没法被验证。
    """
    rows: list[dict] = []
    for e in tl.get("elements") or []:
        if (e.get("track") or "video") == "overlay":
            continue
        a = e.get("audio") or {}
        rows.append({"n": e.get("n"), "kind": e.get("kind"),
                     "asset": a.get("asset") or e.get("asset"),
                     "in": a.get("in"), "out": a.get("out"), "dur": e.get("dur")})
    return rows


def audio_fingerprint(proj: Path) -> tuple[str, list[dict]]:
    """音轨指纹：只对「音源 + 切点 + 时长」这一层取 SHA。"""
    tl = _load(proj / "timeline.json", {})
    sl = audio_slice(tl)
    blob = json.dumps({"duration_s": tl.get("duration_s"), "audio": sl},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest(), sl


def timeline_sha(proj: Path) -> str:
    """与 lock.py 同一口径：只认 timeline.json 的字节。"""
    return hashlib.sha256((proj / "timeline.json").read_bytes()).hexdigest()


# ---------------------------------------------------------------- 低清粗剪预览

def _fail(msg: str) -> None:
    """预览失败一律打到 stderr 并退 2（与「拒绝签锁」的 1 分开，调用方不用猜）。"""
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def _ffprobe_bin() -> str:
    sib = Path(ffmpeg_bin()).with_name("ffprobe")
    if sib.exists() and os.access(sib, os.X_OK):
        return str(sib)
    return shutil.which("ffprobe") or "ffprobe"


def _probe(path: Path) -> dict:
    r = subprocess.run([_ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,avg_frame_rate",
                        "-show_entries", "format=duration,size",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        doc = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        doc = {}
    st = (doc.get("streams") or [{}])[0] or {}
    fmt = doc.get("format") or {}
    fps = 0.0
    raw = str(st.get("avg_frame_rate") or "")
    try:
        if "/" in raw:
            a, b = raw.split("/", 1)
            fps = float(a) / float(b) if float(b or 0) else 0.0
        elif raw:
            fps = float(raw)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        dur = float(fmt.get("duration") or 0)
    except ValueError:
        dur = 0.0
    return {"w": st.get("width"), "h": st.get("height"),
            "fps": round(fps, 3), "dur": round(dur, 3)}


def _banner_font() -> Path | None:
    """烧「粗剪预览」条要一个能显示中文的字体文件；找不到就跳过（不硬造）。"""
    for base in (Path.home() / "Library" / "Fonts", Path("/Library/Fonts"),
                 Path("/System/Library/Fonts"), HERE.parent / "assets" / "fonts"):
        if not base.is_dir():
            continue
        for pat in ("NotoSansSC-Black.ttf", "NotoSansSC-Bold.ttf", "SmileySans*.ttf"):
            hit = sorted(base.glob(pat))
            if hit:
                return hit[0]
    return None


def preview_meta(proj: Path) -> dict | None:
    """读上一次 `--preview` 的留痕，并标出它跟当前时间轴是不是同一版。"""
    doc = _load(proj / "out" / PREVIEW_META)
    if not isinstance(doc, dict) or not doc.get("timeline_sha"):
        return None
    doc["_fresh"] = doc.get("timeline_sha") == timeline_sha(proj)
    doc["_file_exists"] = (proj / str(doc.get("file") or "")).exists()
    return doc


def transcode_preview(src: Path, dst: Path) -> dict:
    """把成片那版压成低清粗剪：半分辨率 + 低码率 + 烧一条「粗剪预览」的条。

    单拎出来是为了能被单测直接喂一段合成片验证「分辨率确实更低、码率确实更低」，
    不必真跑浏览器渲一条片。
    """
    info = _probe(src)
    if not info.get("w") or not info.get("h"):
        _fail(f"读不到预览源的分辨率：{src}")
    W = max(2, int(round(int(info["w"]) * PREVIEW_SCALE / 2) * 2))
    H = max(2, int(round(int(info["h"]) * PREVIEW_SCALE / 2) * 2))
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = [f"scale={W}:{H}:flags=bicubic", "setsar=1"]
    font = _banner_font()
    banner = False
    if font:
        fs = max(14, int(round(H * 0.028)))
        vf.append(f"drawtext=fontfile='{font}':text='{PREVIEW_BANNER}':x=18:y=18:"
                  f"fontsize={fs}:fontcolor=white@0.94:box=1:boxcolor=black@0.55:boxborderw=12")
        banner = True
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(src),
                        "-vf", ",".join(vf),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(PREVIEW_CRF),
                        "-maxrate", PREVIEW_MAXRATE, "-bufsize", "1800k",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", PREVIEW_ABR, "-ac", "2", "-ar", "48000",
                        "-movflags", "+faststart", str(dst)], capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        _fail("预览转码失败：\n" + (r.stderr or "")[-600:])
    out = _probe(dst)
    size = dst.stat().st_size
    dur = float(out.get("dur") or info.get("dur") or 0)
    return {"width": out.get("w"), "height": out.get("h"), "fps": out.get("fps"),
            "duration_s": round(dur, 3), "bytes": size,
            "kbps": int(round(size * 8 / 1000 / dur)) if dur else 0,
            "sha256_16": hashlib.sha256(dst.read_bytes()).hexdigest()[:16],
            "banner": banner,
            "source_width": info.get("w"), "source_height": info.get("h")}


def render_preview(proj: Path, *, library: str | None = None, workers: str = "4",
                   keep_work: bool = False) -> dict:
    """渲一版低清粗剪：真渲（降质量档）→ 半分辨率低码率转码 → 写 preview.json。

    为什么不直接渲到项目里：`render_hf.py` 的产物路径是钉死的（out/film-hf.mp4 等），
    预览要是渲在那儿，就会把**交付成片**覆盖成草稿画质。所以这里搭一个只读软链的临时工程，
    渲完取走预览、丢掉现场；真项目的 out/、hf/、lock.json 一个字节都不碰。
    """
    if not (proj / "timeline.json").exists():
        _fail(f"没有 timeline.json：{proj}（先跑 timeline.py）")
    stage = proj / "out" / "_preview"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    sb = stage / "project"
    (sb / "out").mkdir(parents=True)
    (sb / "hf").mkdir(parents=True)
    for name in PREVIEW_LINKS:
        src = proj / name
        if src.exists():
            os.symlink(src, sb / name)
    clips = proj / "hf" / "clips"
    if clips.is_dir():
        os.symlink(clips, sb / "hf" / "clips")     # 切好的片段共用缓存，预览不必重切

    from lock import lock as _lock                  # 只给临时工程签，渲完随工程一起扔
    _lock(sb, "粗剪预览临时锁（渲完即删，不是交付拍板）")

    lib = library or str(resolve_lib(proj, None))
    cmd = [sys.executable, str(HERE / "render_hf.py"), "--project", str(sb),
           "--quality", PREVIEW_QUALITY, "--workers", str(workers), "--library", lib]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = round(time.time() - t0, 1)
    film = sb / "out" / "film-hf.mp4"
    if r.returncode != 0 or not film.exists():
        tail = "\n".join(((r.stdout or "") + (r.stderr or "")).splitlines()[-12:])
        if not keep_work:
            shutil.rmtree(stage, ignore_errors=True)
        _fail(f"预览渲染失败（render_hf 退出码 {r.returncode}）：\n{tail}")

    src = _probe(film)
    if not src.get("w") or not src.get("h"):
        _fail(f"读不到预览源的分辨率：{film}")
    dst = proj / "out" / PREVIEW_NAME
    try:
        low = transcode_preview(film, dst)
    finally:
        if not keep_work:
            shutil.rmtree(stage, ignore_errors=True)
    afp, aslice = audio_fingerprint(proj)
    meta = {
        "version": 1,
        "status": "粗剪预览 · 未过门禁",
        "note": ("低清粗剪：降质量档 + 半分辨率 + 低码率出的预览片。它没过 gate.py / verify_film.py，"
                 "画面细节与字幕清晰度以成片为准，不许当成交付物。"),
        "file": f"out/{PREVIEW_NAME}",
        "created": datetime.now().isoformat(timespec="seconds"),
        "timeline_sha": timeline_sha(proj),
        "audio_fp": afp,
        "audio_blocks": len(aslice),
        "width": low["width"], "height": low["height"],
        "fps": low["fps"], "duration_s": low["duration_s"],
        "bytes": low["bytes"], "kbps": low["kbps"],
        "sha256_16": low["sha256_16"],
        "banner": low["banner"],
        "render": {"renderer": "render_hf.py", "quality": PREVIEW_QUALITY,
                   "scale": PREVIEW_SCALE, "crf": PREVIEW_CRF, "preset": "veryfast",
                   "maxrate": PREVIEW_MAXRATE, "audio_bitrate": PREVIEW_ABR,
                   "workers": workers, "elapsed_s": elapsed,
                   "source_width": src.get("w"), "source_height": src.get("h")},
    }
    (proj / "out" / PREVIEW_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return meta


def _style(root: Path, pack_or_style: str | None) -> dict:
    if not pack_or_style or yaml is None:
        return {}
    for sub in ("packs", "styles"):
        p = root / sub / f"{pack_or_style}.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
    return {}


def _card_label(c: dict) -> str:
    kind = str(c.get("kind") or "keyword")
    text = str(c.get("text") or "")
    if kind in MG_LABEL and not text:
        d = c.get("data") or {}
        text = f"{d.get('value', '')}{d.get('unit', '')} {d.get('label', '') or d.get('title', '') or d.get('term', '')}".strip()
    if kind in MG_LABEL:
        return f"{MG_LABEL[kind]}「{text}」" if text else MG_LABEL[kind]
    if kind == "behind":
        return f"人物脑后大字「{text}」"
    return f"高光大字「{text}」"


def build(proj: Path, root: Path) -> str:
    brief = json.loads((proj / "brief.json").read_text()) if (proj / "brief.json").exists() else {}
    tl = json.loads((proj / "timeline.json").read_text())
    pack_name = tl.get("pack") or brief.get("pack") or tl.get("style_pack") or "P1-warm"
    st = _style(root, pack_name)

    L: list[str] = []
    A = L.append

    A(MARK)
    A(f"# 剪辑与包装方案卡 · {proj.name}")
    A("")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 确认无误请回复 **「认可」**（或直接指出想改哪一句/哪张卡），确认后立即开始合成高清成片。")
    A("")
    A("## 一、成片总览")
    A("")
    aud = brief.get("audience") or "目标观众"
    chg = brief.get("change") or "记住核心观点"
    A(f"- **给谁看**：{aud}（看完让他：{chg}）")
    A(f"- **总时长**：**{tl.get('duration_s')} 秒**（共 {len(tl.get('elements') or [])} 个画面段落）")
    A(f"- **视觉包装风格**：`{pack_name}`（{st.get('label') or st.get('look_notes') or '高清竖屏包装'}）")
    tb = tl.get("title_bar")
    if isinstance(tb, dict) and (tb.get("main") or tb.get("text")):
        top_t = f"【{tb['top']}】" if tb.get("top") else ""
        A(f"- **开篇悬念标题条**：{top_t}**{tb.get('main') or tb.get('text')}**（黄金前 4.5 秒吸睛）")
    bs = tl.get("beat_sync")
    if isinstance(bs, dict) and bs.get("checked_cuts"):
        A(f"- **音乐与卡点节奏**：情绪 `{tl.get('bgm_mood') or 'upbeat'}` · 鼓点踩点率 **{bs.get('hit_rate', 0):.0%}**")
    else:
        A(f"- **背景配乐**：CC0 免版权曲库 `{tl.get('bgm_mood') or 'upbeat'}` 情绪（人声说话时音乐自动压低让路，绝不抢台词）")
    asm = proj / "assumptions.md"
    if asm.exists():
        A("- **我替你做的决定**：`assumptions.md`（系统默认掉的每一条都列在那儿，一句话就能改）")
    # 三次拍板的状态（只读 approval.json，不在这里另判一套）：主卡上就能看见「还差哪一拍」
    import ideate as _gates
    _st = _gates.gate_state(proj)
    _open = [g for g in _gates.REQUIRED_GATES if not _st[g]["ok"]]
    A(f"- **三次拍板**（BRIEF：一条片最多打断 {_gates.INTERRUPT_BUDGET} 次）："
      + "　".join(f"{('🟢' if _st[g]['ok'] else '🔴')}{_st[g]['label']}"
                  for g in _gates.REQUIRED_GATES))
    if _open:
        for g in _open:
            A(f"  - 🔴 **{_st[g]['label']}还没签**：{_st[g]['why']}")
        if _gates.GATE_SHOTS in _open:
            A("    （泄漏了也行不了：渲前闸门会拿这一条拦下来）")
    A("")

    A("## 二、逐段分镜：说什么 / 画面是什么 / 包装特效")
    A("")
    A("| 段号 | 时间(秒) | 台词内容 | 画面来源 | 叠加包装（花字 / 动效卡 / 贴纸 / 镜头特效） |")
    A("|---|---|---|---|---|")
    t0 = 0.0
    for e in tl.get("elements") or []:
        if e.get("track") == "overlay":
            continue
        if e["kind"] == "clip":
            d = (float(e["audio"]["out"]) - float(e["audio"]["in"])) if e.get("audio") else (float(e["out"]) - float(e["in"]))
        else:
            d = float(e.get("dur") or 0)
        span = f"{t0:.1f}–{t0 + d:.1f}"
        t0 += d
        said = (e.get("evidence") or "—").replace("|", "／")
        if e.get("kind") == "gen":
            tpl = e.get("template") or "gen"
            vis = f"代码生成动效（`{tpl}`）"
        elif e.get("kind") == "image":
            vis = f"图片特写（{Path(str(e.get('asset') or '')).name[-18:]}）"
        elif e.get("medium") == "talking":
            vis = f"真人出镜说话（{Path(str(e.get('asset') or '')).name[-18:]}）"
        else:
            vis = f"实拍细节画面（{Path(str(e.get('asset') or '')).name[-18:]}）"

        extras: list[str] = []
        if e.get("card"):
            extras.append(_card_label(e["card"]))
        stk = e.get("sticker")
        if isinstance(stk, dict) and stk.get("id"):
            extras.append(f"情绪贴纸 `{stk['id']}`")
        elif isinstance(stk, str) and stk:
            extras.append(f"情绪贴纸 `{stk}`")
        vfx = str(e.get("vfx") or "")
        if vfx:
            extras.append(VFX_LABEL.get(vfx, vfx))
        A(f"| #{e['n']} | {span} | {said} | {vis} | {' ＋ '.join(extras) or '高清无标点字幕'} |")
    A("")

    A("## 三、锁定音轨 + 低清粗剪预览（拍板 ②）")
    A("")
    afp, aslice = audio_fingerprint(proj)
    cur_sha = timeline_sha(proj)
    pv = preview_meta(proj)
    if pv and pv.get("_fresh") and pv.get("_file_exists"):
        A(f"- **粗剪预览**：`{pv.get('file')}`（{pv.get('width')}×{pv.get('height')} · "
          f"{pv.get('kbps')} kbps · {float(pv.get('duration_s') or 0):.1f}s）"
          f"—— 跟当前时间轴**同一版**（{cur_sha[:12]}）")
    elif pv:
        A(f"- 🔴 **预览是旧版**：`{pv.get('file')}` 出在时间轴 `{str(pv.get('timeline_sha'))[:12]}` 上，"
          f"现在是 `{cur_sha[:12]}`——你看到的那版不是这一版，先重跑 `--preview`")
    else:
        A("- 🟡 **还没渲低清粗剪预览**：跑 `plan_card.py --project … --preview` 出一版"
          "（半分辨率、低码率），看过再拍板；不看也能签，但锁里会记「无预览」")
    A(f"- **音轨指纹**：`{afp[:12]}`（timeline 里 {len(aslice)} 格的音源与切点，"
      "口径就是「每格用哪段音、几分到几分」）")
    A("- 🔴 **这是粗剪预览、未过门禁**：分辨率和码率都是降的，画面细节、字幕清晰度、卡面观感"
      "以成片为准；它没跑过 `gate.py` / `verify_film.py`，不许当成交付物。")
    A(f"- **你点头签的锁同时钉住两样**：整条时间轴的 sha（`lock.json`，改一个字节就失效）"
      f"+ 音轨指纹 `{afp[:12]}`——只改画面时音轨指纹不变，换口播/挪切点它就变。")
    A("")

    A("## 四、自动质量守卫（渲染前后逐项实测）")
    A("")
    A("- **台词与字幕**：单行无标点、按自然换气切句、不切断词语、严禁编造没说过的词")
    A("- **版面防遮挡**：动效卡与大字自动避开人脸和底部字幕安全区，字底对比度实测达标（ΔL ≥ 12）")
    A("- **声音响度**：平台标准响度（−14 LUFS，不忽大忽小、不爆音），全部配乐与音效均附 CC0 免版权留痕")
    A("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成剪辑与包装方案确认卡并执行拍板上锁")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--approve", nargs="?", const="", default=None,
                    help="确认方案并签发 lock.json，放行 render_hf.py")
    ap.add_argument("--preview", action="store_true",
                    help="渲一版低清粗剪预览（降质量档 + 半分辨率 + 低码率），"
                         "产物 out/preview-roughcut.mp4 与 out/preview.json，并把预览段写进方案卡")
    ap.add_argument("--preview-workers", default="4", help="预览渲染的并发数（默认 4）")
    ap.add_argument("--keep-preview-work", action="store_true",
                    help="保留预览的临时工程目录 out/_preview（排查用，平时不留）")
    ap.add_argument("--library", default=None, help="素材库根（预览渲染用，默认按项目向上解析）")
    ap.add_argument("--accept-stale-preview", action="store_true",
                    help="人工拍板时预览与当前时间轴不是同一版仍要签：会写进 lock.json 留给复核")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--source", default="human",
                    help="这次拍板是谁发起的：human（默认，人手确认）/ agent-revise（改片重签）等。"
                         "审计口径：机器代签绝不能冒充人工确认点")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    root = Path(a.root).expanduser().resolve()
    out = proj / "方案.md"

    if a.preview:
        meta = render_preview(proj, library=a.library, workers=a.preview_workers,
                              keep_work=a.keep_preview_work)
        print(f"粗剪预览：{meta['file']} · {meta['width']}×{meta['height']} · {meta['kbps']} kbps · "
              f"{meta['duration_s']}s · 渲+转码 {meta['render']['elapsed_s']}s")
        print("🔴 这是粗剪预览、未过门禁（不是成片）：看过再 `--approve`")
        text = build(proj, root)
        out.write_text(text, encoding="utf-8")
        print(f"→ {out}")
        if a.show:
            print(text)
        return 0

    if a.approve is not None:
        afp, aslice = audio_fingerprint(proj)
        cur = timeline_sha(proj)
        pv = preview_meta(proj)
        stale = bool(pv) and bool(pv.get("timeline_sha")) and pv.get("timeline_sha") != cur
        # 人工拍板必须落在「自己看过的那一版」上；机器代签（改片重签）只留痕不拦，
        # 否则 revise.py 这类自动重签流程会直接卡死。
        if stale and a.source == "human" and not a.accept_stale_preview:
            print(f"预览跟当前时间轴不是同一版：预览出在 {str(pv.get('timeline_sha'))[:12]}，"
                  f"现在是 {cur[:12]}。\n  先跑 `plan_card.py --project … --preview` 看这一版，再拍板。"
                  "\n  （只改了包装/花字、不想再看一遍，就加 --accept-stale-preview："
                  "这条会写进 lock.json 留给复核。）", file=sys.stderr)
            return 1
        # 方案卡总是和当前时间轴对齐：拍板的时候，人看到的那页必须就是这一版
        out.write_text(build(proj, root), encoding="utf-8")
        rec = {"approved": True, "at": datetime.now().isoformat(timespec="seconds"),
               "note": a.approve or "认可", "source": a.source}
        # approval.json 现在是三次拍板的唯一留痕处（gates / asks）——签拍板③ 只改自己那几个顶层字段，
        # 拍板①② 的记录一个字节都不许被盖掉。
        import ideate as _gates
        _doc = _gates.load_approval(proj)
        _doc.update(rec)
        _gates.save_approval(proj, _doc)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lock import lock as _lock
        from log_decision import append_row
        d = _lock(proj, rec["note"])
        # 锁的对象不只是「时间轴改没改」，还有「音轨是哪一条」与「预览是哪一版」。
        # lock.py 只管 sha256（status/verify 口径不动）；这两笔是本文件补上的维度。
        lock_p = proj / "lock.json"
        doc = json.loads(lock_p.read_text())
        doc["audio_fp"] = afp
        doc["audio_scope"] = f"{len(aslice)} 格音源与切点（timeline.json 音频部分）"
        doc["preview"] = ({
            "file": pv.get("file"), "sha256_16": pv.get("sha256_16"),
            "timeline_sha": pv.get("timeline_sha"),
            "matches_timeline": not stale,
            "width": pv.get("width"), "height": pv.get("height"),
            "kbps": pv.get("kbps"), "created": pv.get("created"),
            "status": pv.get("status"),
        } if pv else None)
        if stale:
            doc["preview_stale_accepted"] = True
        lock_p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        said = "确认剪辑与包装方案" if a.source == "human" else f"[{a.source}] {a.approve or '改片重签'}"
        pv_note = ("预览同版" if pv and not stale
                   else ("无预览" if not pv else "预览为旧版(已明确接受)"))
        _gates.record_gate(proj, _gates.GATE_CUT, note=rec["note"], source=a.source,
                           where="plan_card --approve",
                           detail={"sha256": d["sha256"], "audio_fp": afp,
                                   "preview": pv_note, "elements": d["elements"]})
        # 机器代签（改片重签）不算「问过人」：asks 只记真的问过用户的那些
        if a.source == "human":
            _gates.note_interrupt(proj, _gates.GATE_CUT, "plan_card --approve",
                                  "锁定音轨 + 低清粗剪预览认可吗？")
        append_row(proj, said=said,
                   did=f"签发时间轴锁 {d['sha256'][:12]} · 音轨指纹 {afp[:12]} · {pv_note}",
                   verdict="accepted")
        print(f"已拍板上锁：sha {d['sha256'][:12]} · 音轨指纹 {afp[:12]} · {d['elements']} 格 · "
              f"{d['duration_s']}s · {pv_note}")
        if stale:
            print("🔴 这次拍板用的预览是旧版（与当前时间轴不是同一版）：已写进 lock.json 的 "
                  "preview.matches_timeline=false，复核时看得到", file=sys.stderr)
        elif not pv:
            print("🟡 这次拍板没有低清粗剪预览：锁里 preview=null（下次跑 --preview 补上）", file=sys.stderr)
        return 0

    text = build(proj, root)
    out.write_text(text, encoding="utf-8")
    print(f"→ {out}")
    if a.show:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
