#!/usr/bin/env python3
"""CutSkills · 单向导出剪映草稿（给用户手动精修用）。

═══ 口径（不许在文档/输出里写成别的样子）═══
① **单向、一次性**：本工具只把 timeline.json 里的时间轴**写出去**。草稿写完后，
   剪映 6.0+ 打开时会**就地加密**成自己的格式，本工具**读不回来**，也不打算读回来。
   所以「导出 → 剪映精修 → 再导回工具」这条路**不存在**，精修完就在剪映里出片。
② **结构自检 ≠ 剪映一定能打开**：我们只能验「文件写全了、JSON 解得开、字段齐、
   片段数对、微秒是整数、素材都在草稿目录里、帧网格与时长不变式成立」。
   剪映到底认不认、观感对不对，只能用户打开看。
③ **可降级**：本机版本指纹不兼容 → 拒绝写，只出 mp4；写出后结构自检不过 →
   把半成品草稿目录整个删掉（不留坏草稿污染剪映的草稿列表），报降级。

═══ 格式依据（2026-11-15 调研定稿，代码里不再现查）═══
- 草稿根（macOS）：`~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`
  （注意剪映把它拼成 `com.lveditor` 而不是 `com.lvdeditor`；本机若被用户在
  「全局设置 → 草稿位置」改过，用 `--draft-root` 指定）。Windows：
  `%LOCALAPPDATA%\\JianyingPro\\User Data\\Projects\\com.lveditor.draft\\`。
- 一个草稿目录最少两个文件：主文件 `draft_info.json`（CapCut Windows 叫
  `draft_content.json`）+ `draft_meta_info.json`；本工具另外写 `draft_cover.jpg`
  （10.3+ 对封面校验变严，缺帧可能报「内容已损坏」）与 `assets/` 下的素材副本。
- `clip.transform` 归一化区间 −1..1，以画布中心为原点、**y 向上为正**，单位是
  画布的半个宽/高。
- 文本内容在 `materials.texts[].content` 里，它**本身是一段 JSON 字符串**；
  `styles[].range` 是 **UTF-16 code unit 下标**（不是字节、不是字形数）。
- 不变式：`source_timerange.duration == target_timerange.duration × speed`。
  写错剪映会自己「修」，修出来就是错位——所以算完必须自检，不成立直接拒写。

用法：
  export_jianying.py --project <项目> [--out <草稿目录>] [--draft-root <草稿根>]
                     [--name <草稿名>] [--fps 30] [--dry-run] [--force]

退出码：0=ok / 1=degraded（草稿没成，只出 mp4）/ 2=refused（版本不兼容，不写）/ 3=入参或前置条件错
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libs import ffmpeg_bin, resolve_lib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# ── 草稿文件约定 ────────────────────────────────────────────────────────
JY_MAIN_FILE = "draft_info.json"          # 剪映 6.0+ 主文件名
JY_MAIN_ALT = "draft_content.json"        # CapCut Windows / 老版本叫这个
JY_META_FILE = "draft_meta_info.json"
JY_COVER_FILE = "draft_cover.jpg"
JY_ASSETS_DIR = "assets"

# ── 版本指纹 ────────────────────────────────────────────────────────────
# 社区口径：version > 360000 拒写、< 360000 警告。360000 是**实测量级**
# （本机另一支工具写出的草稿就是 360000），不是官方语义，所以按「不可超过」用。
MAX_VERSION = 360000
# 探不到本机指纹时的保守值：正好卡在 360000，既不越线也不触发「版本偏低」告警。
CONSERVATIVE_FP = {"app_version": "6.0.0", "app_source": "lv",
                   "version": MAX_VERSION, "new_version": "169.0.0"}

# ── 文本换算 ────────────────────────────────────────────────────────────
# 剪映文本的 `size` 是它自己的单位，不是像素。换算用短边 1080 作基准：
# 社区语料里 CapCut/剪映「默认可编辑文本」size=15 在 1920×1080 画布上字高约 120px，
# 即 1 个单位 ≈ 8px（1080 短边基准）。竖屏 1080×1920 与横屏同字号视觉等大，
# 所以按短边归一，不按高度归一：
#     size = px × (1080 / min(W, H)) / 8
# 这是**一阶估算**：剪映没有公开这个单位，实测只能靠人打开看一眼。所以导出后字号
# 可能需要手动微调——报告里写清楚，别假装它是精确值。
SIZE_UNIT_PX = 8.0
SIZE_REF_SHORT = 1080.0
# 字块纵向位置：项目真值 out/subs.json 的 `band`（像素行，原点左上）或 pack 的
# `subtitle.center_y_px`。剪映 transform.y 以画布中心为 0、向上为正、单位是半高：
#     y = (0.5 - center_px / H) × 2
# 例：1080×1920 上 band=[1034,1554] → center=1294 → 1294/1920=0.674 →
#     y = -0.348（画面中心偏下 0.35 个半高 = 672px）
CENTER_Y_PX_FALLBACK = 1294
FONT_SIZE_PX_FALLBACK = 80

EMPTY_MATERIAL_GROUPS = ("videos", "audios", "texts", "images", "stickers", "effects",
                         "filters", "transitions", "audio_effects", "audio_fades",
                         "beats", "material_animations", "speeds", "placeholders")


# ── 小工具 ──────────────────────────────────────────────────────────────
def load_json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def new_id() -> str:
    return str(uuid.uuid4()).upper()


def utf16_len(s: str) -> int:
    """UTF-16 code unit 数（剪映 styles[].range 的口径；emoji 算 2 个）。"""
    return len(s.encode("utf-16-le")) // 2


def strip_punct(text: str) -> str:
    """去标点（沿用项目既有口径：subs.py 的 PUNCT / SENTINEL）。

    字幕真值本来就是无标点的（subs.py 产出时就去了），这里再抹一道是因为
    真值有可能是手改过的——草稿是单向交付物，写进剪映的句读屏幕上也看得见。
    """
    try:
        import subs as _subs                     # 单一真值源，不在这里抄一份标点表
        punct, sentinel = _subs.PUNCT, _subs.SENTINEL
    except Exception:
        punct, sentinel = "，。！？、；：…—～,.!?;:", "\x00"
    return "".join(ch for ch in str(text) if ch not in punct and ch != sentinel).strip()


def us_of_frames(n: int, fps: float) -> int:
    """帧号 → 微秒。帧号是唯一的时间真值，微秒由它算，反过来不成立。"""
    return int(round(n * 1_000_000 / fps))


def frames_of_s(seconds: float, fps: float) -> int:
    """秒 → 帧号（四舍五入，至少 1 帧：0 帧的片段剪映会当坏数据）。"""
    return max(1, int(round(float(seconds) * fps)))


def span_us(start_frame: int, frames: int, fps: float) -> int:
    """帧号区间 → 微秒时长。用「帧边界相减」而不是「帧数 × 帧长」，
    这样相邻片段的 start 与上一个片段的 end 严丝合缝，合计也正好等于总时长。
    """
    return us_of_frames(start_frame + frames, fps) - us_of_frames(start_frame, fps)


def is_frame_multiple(us: int, fps: float, tol: int = 1) -> bool:
    """微秒时长是不是帧的整数倍（容差 1µs：30fps 一帧 33333.33µs，写不进整数）。"""
    if not isinstance(us, int) or us <= 0:
        return False
    n = round(us * fps / 1_000_000)
    return n >= 1 and abs(us - n * 1_000_000 / fps) <= tol


def ratio_of(w: int, h: int) -> str:
    g = math.gcd(int(w), int(h)) or 1
    return f"{int(w) // g}:{int(h) // g}"


# ── 草稿根与本机版本指纹 ─────────────────────────────────────────────────
def draft_root_candidates() -> list[Path]:
    """本机剪映草稿根候选。注意目录名是 com.lveditor（剪映自己的拼写），两种都试。"""
    out: list[Path] = []
    home = Path.home()
    import os
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        for app in ("JianyingPro", "CapCut"):
            out.append(base / app / "User Data" / "Projects" / "com.lveditor.draft")
    else:
        for app in ("JianyingPro", "CapCut"):
            for spelling in ("com.lveditor.draft", "com.lvdeditor.draft"):
                out.append(home / "Movies" / app / "User Data" / "Projects" / spelling)
    return out


def pick_draft_root(given: str | None) -> tuple[Path, list[str]]:
    """返回 (草稿根, 说明)。显式 --draft-root 永远优先。"""
    notes: list[str] = []
    if given:
        p = Path(given).expanduser()
        return p, notes
    cands = draft_root_candidates()
    for c in cands:
        if c.is_dir() and any(c.iterdir()):
            return c, notes
    for c in cands:
        if c.is_dir():
            return c, notes
    # 一个都不存在：用第一候选（macOS 上是剪映默认路径），由调用方决定要不要建。
    notes.append(f"本机没有现成的草稿根，按剪映默认位置取 {cands[0]}")
    return cands[0], notes


def probe_fingerprint(root: Path) -> tuple[dict, list[str]]:
    """读草稿根里**已存在**草稿的版本指纹（只读，不改任何东西）。

    探到的是用户的剪映自己写的草稿，比任何写死的默认值可信。
    读不出来的草稿多半是 6.0+ 加密件——那本身就是「本机剪映 ≥6.0」的证据，
    只是读不到具体版本号，记一条说明。
    """
    notes: list[str] = []
    if not root.is_dir():
        return {}, [f"草稿根不存在（{root}）——未探测到本机版本，按保守值写入"]
    found: list[dict] = []
    encrypted = 0
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        main = next((d / fn for fn in (JY_MAIN_FILE, JY_MAIN_ALT) if (d / fn).exists()), None)
        if main is None:
            continue
        try:
            content = json.loads(main.read_text(encoding="utf-8"))
        except Exception:
            encrypted += 1          # 6.0+ 加密草稿：存在但读不出
            continue
        plat = content.get("platform") or {}
        meta = load_json(d / JY_META_FILE) or {}
        found.append({"dir": d.name,
                      "app_version": plat.get("app_version"),
                      "app_source": plat.get("app_source"),
                      "version": content.get("version"),
                      "new_version": content.get("new_version"),
                      "tm": int(meta.get("tm_draft_modified") or 0)})
    if encrypted:
        notes.append(f"草稿根里有 {encrypted} 个草稿读不出 JSON（6.0+ 加密件），"
                     f"读不到版本号——按保守值写入")
    if not found:
        if not notes:
            notes.append("草稿根里没有历史草稿——未探测到本机版本，按保守值写入")
        return {}, notes
    # 优先取「有 app_version」的最新一支；都没有就用最新的 version
    with_ver = [f for f in found if f.get("app_version")]
    pool = with_ver or found
    pool.sort(key=lambda f: (f.get("tm") or 0, f.get("version") or 0), reverse=True)
    best = dict(pool[0])
    best["_probed_drafts"] = len(found)
    return best, notes


def fingerprint_verdict(fp: dict) -> tuple[bool, str]:
    """(能不能写, 不能写的原因)。宁可少写一个草稿，也不往用户剪映里塞坏草稿。"""
    if not fp:
        return True, ""
    v = fp.get("version")
    if isinstance(v, int) and v > MAX_VERSION:
        return False, (f"本机草稿指纹 version={v} 超过上限 {MAX_VERSION}：本机剪映新于 6.0，"
                       f"草稿格式已被改过")
    src = fp.get("app_source")
    if src and src not in ("lv", "cc"):
        return False, (f"本机草稿指纹 app_source={src!r} 不认识（只认 lv=剪映 / cc=CapCut），"
                       f"不敢按未知客户端的格式写")
    return True, ""


# ── 项目真值 → 草稿计划 ─────────────────────────────────────────────────
def subtitle_truth(proj: Path, pack: dict) -> dict:
    """字幕的字号/纵向位置/颜色，来源优先级：项目产物 out/subs.json > pack 配置。"""
    subs = load_json(proj / "out" / "subs.json") or {}
    sub_pack = (pack or {}).get("subtitle") or {}
    band = subs.get("band")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        center_px = (float(band[0]) + float(band[1])) / 2.0
        center_src = "out/subs.json band"
    elif sub_pack.get("center_y_px"):
        center_px = float(sub_pack["center_y_px"])
        center_src = "pack subtitle.center_y_px"
    else:
        center_px = float(CENTER_Y_PX_FALLBACK)
        center_src = "内置默认"
    size_px = float(subs.get("size") or sub_pack.get("size_px") or FONT_SIZE_PX_FALLBACK)
    size_src = "out/subs.json size" if subs.get("size") else (
        "pack subtitle.size_px" if sub_pack.get("size_px") else "内置默认")
    return {"center_px": center_px, "center_src": center_src,
            "size_px": size_px, "size_src": size_src,
            "font": sub_pack.get("font") or "", "color": (1.0, 1.0, 1.0)}


def load_pack(proj: Path, tl: dict) -> dict:
    """读包装包（字幕字号/位置的次级真值）。缺文件就空 dict，不报错。"""
    name = str(tl.get("pack") or "").strip()
    if not name:
        return {}
    for base in (ROOT / "packs", proj / "packs"):
        for ext in (".yaml", ".yml", ".json"):
            p = base / f"{name}{ext}"
            if not p.exists():
                continue
            try:
                if p.suffix == ".json":
                    return json.loads(p.read_text(encoding="utf-8"))
                import yaml  # 仓库依赖里已有 PyYAML
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


def canvas_of(tl: dict, pack: dict) -> dict:
    cv = (pack or {}).get("canvas") or {}
    w = int(cv.get("width") or 1080)
    h = int(cv.get("height") or 1920)
    return {"width": w, "height": h, "ratio": ratio_of(w, h)}


def collect_plan(proj: Path, lib: Path, fps: float) -> dict:
    """timeline.json + subs.json + 素材索引 → 只含「能导出」片段的计划。

    时间轴只认 timeline.json（不给第二套真值）；字幕另算——它本来就不在时间轴里，
    是项目的字幕产物（out/subs.json），字号/位置也取自那里的真值。
    """
    warnings: list[str] = []
    blockers: list[str] = []
    tl = load_json(proj / "timeline.json")
    if not tl:
        return {"ok": False, "warnings": warnings,
                "blockers": [f"没有可读的 {proj / 'timeline.json'}"], "blocks": [],
                "audios": [], "texts": [], "canvas": canvas_of({}, {}),
                "sub": subtitle_truth(proj, {}), "total_frames": 0}

    pack = load_pack(proj, tl)
    canvas = canvas_of(tl, pack)
    sub = subtitle_truth(proj, pack)

    idx_path = lib / "_cutskills" / "index.json"
    index: dict[str, dict] = {}
    if idx_path.exists():
        try:
            index = {x["id"]: x for x in json.loads(idx_path.read_text(encoding="utf-8"))["assets"]}
        except Exception as exc:
            warnings.append(f"素材索引读不出来（{exc}），只能按文件名猜路径")
    else:
        warnings.append(f"没有素材索引 {idx_path}，只能按文件名猜路径")

    blocks: list[dict] = []
    audios: list[dict] = []
    unexportable: list[str] = []
    decorated: list[str] = []
    cur_frames = 0

    if tl.get("title_bar") or tl.get("stickers"):
        decorated.append("全局标题条/贴纸轨")

    for el in tl.get("elements") or []:
        kind = el.get("kind")
        track = el.get("track") or "video"
        marks = [k for k in ("card", "sticker", "vfx") if el.get(k)]
        if marks:
            decorated.append(f"#{el.get('n')} {'/'.join(marks)}")
        if track == "overlay":
            # 卡片/贴纸/画中画是渲染期合成的（没有单独的媒体文件），单向导出带不走。
            unexportable.append(f"#{el.get('n')} overlay/{kind}（卡片/贴纸，渲染期合成，无独立媒体）")
            continue
        if kind in ("silence", "gen"):
            unexportable.append(f"#{el.get('n')} {kind}（还没有独立媒体文件，需先渲成片）")
            continue
        aid = el.get("asset")
        if not aid:
            unexportable.append(f"#{el.get('n')} {kind}（没有 asset）")
            continue
        rec = index.get(aid) or {}
        src = lib / str(rec.get("path") or aid)
        if not src.exists():
            unexportable.append(f"#{el.get('n')} 素材文件不存在：{src}")
            continue
        # 时长：clip 用 out−in，其他用 dur（与 timeline.element_dur 同口径）
        if kind == "clip":
            tin = float(el.get("in") or 0.0)
            tout = float(el.get("out") or 0.0)
            span = tout - tin
        else:
            tin = 0.0
            span = float(el.get("dur") or 0.0)
        if span <= 0:
            unexportable.append(f"#{el.get('n')} 时长 ≤0")
            continue
        mat_kind = "photo" if (rec.get("kind") in ("photo", "image") or kind == "image") else "video"
        mat_dur_us = int(round(float(rec.get("duration") or 0.0) * 1_000_000))
        frames = frames_of_s(span, fps)
        src_start = int(round(tin * 1_000_000))
        if not mat_dur_us:
            warnings.append(f"#{el.get('n')} 索引里没有素材时长，按片段时长写入")
            mat_dur_us = src_start + us_of_frames(frames, fps)
        # 吸附到帧后可能顶出素材尾巴：收帧，宁短一帧也不要剪映显示冻结/黑场
        while frames > 1 and src_start + us_of_frames(frames, fps) > mat_dur_us:
            frames -= 1

        block = {"n": el.get("n"), "kind": mat_kind, "asset": aid, "src": src,
                 "rec": rec, "src_start": src_start, "frames": frames,
                 "start_frame": cur_frames}
        blocks.append(block)
        cur_frames += frames

        au = el.get("audio") if isinstance(el.get("audio"), dict) else None
        if au and au.get("asset"):
            if au.get("asset") == aid:
                block["mute"] = False       # 画面自带原声：视频片段自己放，不另起音轨
            else:
                arec = index.get(au["asset"]) or {}
                asrc = lib / str(arec.get("path") or au["asset"])
                if asrc.exists():
                    audios.append({"n": el.get("n"), "asset": au["asset"], "src": asrc,
                                   "rec": arec, "start_frame": block["start_frame"],
                                   "frames": frames,
                                   "src_start": int(round(float(au.get("in") or 0.0) * 1_000_000)),
                                   "mute_video": True})
                else:
                    unexportable.append(f"#{el.get('n')} 声音素材不存在：{asrc}")
        else:
            block["mute"] = True            # 没写声音就是没用它的声音

    # 字幕：每条一个 text 片段 + 一个 text material
    subs = load_json(proj / "out" / "subs.json") or {}
    texts: list[dict] = []
    cues = subs.get("cues") or []
    if not cues:
        warnings.append("out/subs.json 里没有 cues（这条片没有字幕，或还没渲过）")
    for i, c in enumerate(cues):
        text = strip_punct(c.get("text") or "")
        if not text:
            continue
        sf = c.get("sf")
        ef = c.get("ef")
        if sf is None or ef is None:
            sf = int(round(float(c.get("start") or 0) * fps))
            ef = int(round(float(c.get("end") or 0) * fps))
        sf, ef = int(sf), int(ef)
        if ef <= sf:
            ef = sf + 1
        texts.append({"i": i, "text": text, "start_frame": sf, "end_frame": ef,
                      "chars": utf16_len(text)})

    if unexportable:
        blockers.extend(unexportable)
    if decorated:
        warnings.append(
            f"{len(decorated)} 处包装（卡片/贴纸/动效/标题条）是渲染期合成的，草稿里没有："
            + "、".join(decorated[:6]) + ("……" if len(decorated) > 6 else "")
            + "——要这些效果就在剪映里手动加，或者直接用 mp4。")
    if not blocks and not blockers:
        blockers.append("没有任何可导出的画面元素（timeline.json 的 elements 是空的）")

    return {"ok": not blockers, "warnings": warnings, "blockers": blockers,
            "blocks": blocks, "audios": audios, "texts": texts,
            "canvas": canvas, "sub": sub, "total_frames": cur_frames,
            "pack": tl.get("pack"), "name_hint": tl.get("slug")}


# ── 草稿内容构造 ────────────────────────────────────────────────────────
def clip_defaults() -> dict:
    return {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0}}


def segment_defaults(render_index: int) -> dict:
    return {"visible": True, "speed": 1.0, "volume": 1.0, "reverse": False,
            "intensifies_audio": False, "is_placeholder": False,
            "template_id": "", "template_scene": "default",
            "enable_adjust": False, "cartoon": False,
            "uniform_scale": {"on": True, "value": 1.0},
            "material_speed": 1.0, "extra_material_refs": [],
            "render_index": render_index, "clip": clip_defaults()}


def text_content_json(text: str, size: float, color: tuple[float, float, float]) -> str:
    """文本内容：一段 JSON 字符串。`range` 用 UTF-16 code unit 下标。

    只写一条覆盖全文的 style：强调变色（subs.json 的 html）暂不带——颜色规则是
    渲染器的语法，抄进草稿需要逐段 range 对齐，做糙了反而把整句染色。
    """
    payload = {"text": text,
               "styles": [{"fill": {"content": {"solid": {"color": [round(c, 4) for c in color]}}},
                           "range": [0, utf16_len(text)],
                           "size": round(float(size), 2),
                           "font": {"path": "", "id": ""},
                           "strokes": [], "shadows": []}]}
    return json.dumps(payload, ensure_ascii=False)


def build_draft(plan: dict, fp: dict, fps: float, name: str,
                draft_dir: Path, root: Path, media: dict) -> tuple[dict, dict]:
    """plan → (主文件内容, meta 文件内容)。media 是「素材路径 → 草稿内相对路径」。"""
    W, H = plan["canvas"]["width"], plan["canvas"]["height"]
    short = float(min(W, H))
    sub = plan["sub"]
    jy_size = round(sub["size_px"] * (SIZE_REF_SHORT / short) / SIZE_UNIT_PX, 2)
    ty = round((0.5 - sub["center_px"] / float(H)) * 2.0, 4)

    materials: dict = {k: [] for k in EMPTY_MATERIAL_GROUPS}
    video_track: list[dict] = []
    audio_track: list[dict] = []
    text_track: list[dict] = []
    imported: list[dict] = []

    def material_for(src: Path, kind: str, rec: dict) -> str:
        rel = media[src]
        mid = new_id()
        w = int(rec.get("width") or W)
        h = int(rec.get("height") or H)
        dur_us = int(round(float(rec.get("duration") or 0.0) * 1_000_000))
        entry = {"id": mid, "type": kind, "path": rel, "material_name": Path(rel).name,
                 "width": w, "height": h, "duration": dur_us,
                 "has_audio": kind == "video",
                 "category_name": "local", "check_flag": 63487,
                 "crop": {"lower_left_x": 0.0, "lower_left_y": 0.0,
                          "lower_right_x": 1.0, "lower_right_y": 0.0,
                          "upper_left_x": 0.0, "upper_left_y": 1.0,
                          "upper_right_x": 1.0, "upper_right_y": 1.0},
                 "crop_ratio": "free", "crop_scale": 1.0, "reverse_intensifies_audio": False,
                 "aigc_type": "none", "audio_fade": None, "extra_type_option": 0,
                 "has_sound_separated": False, "is_unified_beauty_mode": False,
                 "local_material_id": "", "material_id": "", "object_id": "",
                 "source": 0, "source_platform": 0, "stable_volume": False,
                 "team_id": "", "unique_id": "", "video_algorithm": ""}
        # 照片也放进 materials.videos（剪映自己的草稿里就是桶里放、`type: "photo"` 区分）——
        # 这一条没有本机样件可验，按社区草稿的通行形状写。
        materials["videos"].append(entry)
        imported.append({"path": rel, "kind": kind, "dur_us": dur_us, "w": w, "h": h})
        return mid

    for i, b in enumerate(plan["blocks"]):
        mid = material_for(b["src"], b["kind"], b["rec"])
        dur_us = span_us(b["start_frame"], b["frames"], fps)
        src_start = b["src_start"]
        seg = {"id": new_id(), "material_id": mid,
               "target_timerange": {"start": us_of_frames(b["start_frame"], fps),
                                    "duration": dur_us},
               "source_timerange": {"start": src_start, "duration": dur_us},
               **segment_defaults(i)}
        seg["volume"] = 0.0 if b.get("mute") else 1.0
        video_track.append(seg)

    for i, a in enumerate(plan["audios"]):
        rel = media[a["src"]]
        mid = new_id()
        dur_us = span_us(a["start_frame"], a["frames"], fps)
        materials["audios"].append({"id": mid, "type": "extract" if a.get("extract") else "music",
                                    "path": rel, "name": Path(rel).name,
                                    "duration": int(round(float(a["rec"].get("duration") or 0) * 1_000_000)),
                                    "music_id": "", "source_timerange": None,
                                    "category_name": "local", "check_flag": 3,
                                    "local_material_id": "", "material_id": "", "team_id": ""})
        imported.append({"path": rel, "kind": "audio",
                         "dur_us": int(round(float(a["rec"].get("duration") or 0) * 1_000_000)),
                         "w": W, "h": H})
        audio_track.append({"id": new_id(), "material_id": mid,
                            "target_timerange": {"start": us_of_frames(a["start_frame"], fps),
                                                 "duration": dur_us},
                            "source_timerange": {"start": a["src_start"], "duration": dur_us},
                            "volume": 1.0, "speed": 1.0, "visible": True, "reverse": False,
                            "intensifies_audio": False, "is_placeholder": False,
                            "template_id": "", "template_scene": "default",
                            "enable_adjust": False, "cartoon": False,
                            "uniform_scale": {"on": True, "value": 1.0},
                            "material_speed": 1.0, "extra_material_refs": [],
                            "render_index": i, "clip": clip_defaults()})

    for i, t in enumerate(plan["texts"]):
        mid = new_id()
        content = text_content_json(t["text"], jy_size, sub["color"])
        materials["texts"].append({
            "id": mid, "type": "subtitle", "content": content,
            "alignment": 1, "letter_spacing": 0.0, "line_spacing": 0.02,
            "text_color": "#FFFFFF", "text_alpha": 1.0,
            "text_size": jy_size, "font_size": jy_size,
            "typesetting": 0, "check_flag": 7, "global_alpha": 1.0,
            "background_color": "", "background_alpha": 1.0, "background_style": 0,
            "border_color": "", "border_width": 0.0, "border_alpha": 1.0,
            "shadow_color": "", "shadow_alpha": 0.9, "shadow_distance": 5.0,
            "shadow_smoothing": 0.45, "shadow_point": {"x": 0.0, "y": 0.0},
            "group_id": "", "preset_id": "", "preset_name": "", "comment": "",
            "fixed_width": -1, "fixed_height": -1, "force_apply_line_max_width": False,
        })
        dur_us = us_of_frames(t["end_frame"], fps) - us_of_frames(t["start_frame"], fps)
        clip = clip_defaults()
        clip["transform"] = {"x": 0.0, "y": ty}
        seg = {"id": new_id(), "material_id": mid,
               "target_timerange": {"start": us_of_frames(t["start_frame"], fps),
                                    "duration": dur_us},
               "source_timerange": {"start": 0, "duration": dur_us},
               "clip": clip, "volume": 1.0, "speed": 1.0,
               "render_index": i, "extra_material_refs": [],
               "visible": True, "reverse": False, "intensifies_audio": False,
               "is_placeholder": False, "template_id": "", "template_scene": "default",
               "enable_adjust": False, "cartoon": False,
               "uniform_scale": {"on": True, "value": 1.0}, "material_speed": 1.0}
        text_track.append(seg)

    tracks = []
    if video_track:
        tracks.append({"id": new_id(), "type": "video", "flag": 0, "attribute": 0,
                       "name": "", "is_default_name": True, "segments": video_track})
    if audio_track:
        tracks.append({"id": new_id(), "type": "audio", "flag": 0, "attribute": 0,
                       "name": "", "is_default_name": True, "segments": audio_track})
    if text_track:
        tracks.append({"id": new_id(), "type": "text", "flag": 0, "attribute": 0,
                       "name": "", "is_default_name": True, "segments": text_track})

    total_us = us_of_frames(plan["total_frames"], fps)
    platform = {"app_id": 3704, "app_source": fp.get("app_source") or "lv",
                "app_version": fp.get("app_version") or "6.0.0",
                "device_id": "", "hard_disk_id": "", "mac_address": "",
                "os": "mac", "os_version": ""}
    content = {
        "id": new_id(),
        "name": name,
        "duration": total_us,
        "fps": float(fps),
        "canvas_config": {"width": W, "height": H, "ratio": plan["canvas"]["ratio"]},
        "platform": platform,
        "last_modified_platform": dict(platform),
        "version": int(fp.get("version") or MAX_VERSION),
        "new_version": str(fp.get("new_version") or "169.0.0"),
        "materials": materials,
        "tracks": tracks,
        "keyframes": {},
        "config": {"adjust_max_index": 1, "attachment_info": [], "combination_max_index": 1,
                   "export_range": None, "extract_audio_last_index": 1,
                   "lyrics_recognition_id": "", "lyrics_sync": True,
                   "lyrics_taskinfo": [], "maintrack_adsorb": True,
                   "material_save_mode": 0, "multi_language_current": "none",
                   "multi_language_list": [], "multi_language_main": "none",
                   "multi_language_mode": "none", "original_sound_last_index": 1,
                   "record_audio_last_index": 1, "sticker_max_index": 1,
                   "subtitle_keywords_config": None, "subtitle_recognition_id": "",
                   "subtitle_sync": True, "subtitle_taskinfo": [],
                   "system_font_list": [], "video_mute": False, "zoom_info_params": None},
        "extra_info": None,
    }

    now_us = int(time.time() * 1_000_000)
    draft_id = content["id"]
    meta = {
        "draft_id": draft_id,
        "draft_fold_path": str(draft_dir),
        "draft_json_file": JY_MAIN_FILE,
        "draft_name": name,
        "draft_root_path": str(root),
        "draft_cover": JY_COVER_FILE,
        "draft_removable_storage_device": "",
        "draft_timeline_materials_size_": 0,
        "draft_materials_copied_info": [],
        "draft_segment_extra_info": [],
        "draft_settings": {},
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": total_us,
        # draft_materials：type 0 = 用户导入的媒体。**剪映靠这张表判断「哪些素材已导入」，
        # 不看时间轴里的路径**——漏写这张表，时间轴上的片段会变成红框空素材。
        "draft_materials": [{"type": 0, "value": [
            {"ai_group_type": "", "create_time": -1, "duration": it["dur_us"],
             "extra_info": Path(it["path"]).name,
             "file_Path": it["path"], "height": it["h"], "width": it["w"],
             "id": new_id(), "import_time": int(time.time()),
             "import_time_ms": 0, "item_source": 1, "md5": "",
             "metetype": {"video": "video", "photo": "photo", "audio": "music"}.get(it["kind"], "video"),
             "roughcut_time_range": {"duration": it["dur_us"], "start": 0},
             "sub_time_range": {"duration": -1, "start": -1},
             "type": 0}
            for it in imported]}] + [{"type": t, "value": []} for t in range(1, 7)],
        "draft_timeline_id": "",
    }
    return content, meta


# ── 素材复制 ────────────────────────────────────────────────────────────
def plan_media(plan: dict, draft_dir: Path) -> dict:
    """把用到的素材排成「源路径 → 草稿内相对路径」。

    剪映 Mac 是沙盒应用，只能读 ~/Movies：外部绝对路径的素材打开就报
    「暂无访问权限」。所以**必须**复制进草稿目录，主文件里只写相对路径。
    """
    srcs: list[Path] = []
    for b in plan["blocks"]:
        srcs.append(b["src"])
    for a in plan["audios"]:
        srcs.append(a["src"])
    media: dict = {}
    used: set[str] = set()
    for s in srcs:
        if s in media:
            continue
        base = s.name
        stem, suf = Path(base).stem, Path(base).suffix
        k = 2
        name = base
        while name in used:
            name = f"{stem}_{k}{suf}"
            k += 1
        used.add(name)
        media[s] = f"{JY_ASSETS_DIR}/{name}"
    return media


def write_cover(draft_dir: Path, first_video: Path | None) -> str:
    """封面：优先从第一段视频抽第 0.1 秒的帧；抽不到就写一张 1×1 白 JPEG。

    10.3+ 对 draft_cover 校验变严，缺文件可能直接报「内容已损坏」，
    所以宁可写一张占位图也不留空。
    """
    cover = draft_dir / JY_COVER_FILE
    if first_video and first_video.exists():
        try:
            r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-ss", "0.1",
                                "-i", str(first_video), "-frames:v", "1",
                                "-vf", "scale=360:-2", str(cover)],
                               capture_output=True, text=True, timeout=90)
            if r.returncode == 0 and cover.exists() and cover.stat().st_size > 512:
                return "抽帧"
        except Exception:
            pass
    cover.write_bytes(base64.b64decode(TINY_JPEG_B64))
    return "占位图（抽帧失败）"


# 1×1 白 JPEG（最小合法 JPEG 文件，用来兜底封面）
TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


# ── 结构自检（写完再看，不是打开剪映验） ────────────────────────────────
def structure_check(draft_dir: Path, expect: dict, fps: float) -> list[str]:
    """结构自检。任何一项不过 → 调用方删掉整个草稿目录。

    这**只**证明「文件形状对」，不证明剪映认。报告里必须照这个口径说。
    """
    bad: list[str] = []
    main = draft_dir / JY_MAIN_FILE
    meta_p = draft_dir / JY_META_FILE
    if not main.exists():
        return [f"主文件缺失：{main.name}"]
    if not meta_p.exists():
        return [f"meta 文件缺失：{meta_p.name}"]
    content = load_json(main)
    meta = load_json(meta_p)
    if not isinstance(content, dict):
        return ["主文件 JSON 解不开"]
    if not isinstance(meta, dict):
        return ["meta 文件 JSON 解不开"]

    need = ("id", "name", "duration", "fps", "canvas_config", "platform", "version",
            "new_version", "tracks", "materials", "keyframes", "config", "extra_info",
            "last_modified_platform")
    miss = [k for k in need if k not in content]
    if miss:
        bad.append("主文件缺字段：" + "、".join(miss))
    need_meta = ("draft_id", "draft_fold_path", "draft_json_file", "draft_name",
                 "draft_root_path", "draft_cover", "tm_draft_create", "tm_draft_modified",
                 "tm_duration", "draft_materials")
    miss = [k for k in need_meta if k not in meta]
    if miss:
        bad.append("meta 缺字段：" + "、".join(miss))
    if not content.get("tracks"):
        bad.append("tracks 是空的")

    # 片段数：与源时间轴一致
    counts: dict[str, int] = {}
    mids = set()
    for grp in content.get("materials", {}).values():
        if isinstance(grp, list):
            for m in grp:
                if isinstance(m, dict) and m.get("id"):
                    mids.add(m["id"])
    for tr in content.get("tracks") or []:
        ttype = tr.get("type")
        segs = tr.get("segments") or []
        counts[ttype] = len(segs)
        for seg in segs:
            sid = seg.get("id") or "?"
            tr_t = seg.get("target_timerange") or {}
            sr_t = seg.get("source_timerange") or {}
            td, sd = tr_t.get("duration"), sr_t.get("duration")
            sp = seg.get("speed", 1.0)
            if not isinstance(td, int) or not isinstance(sd, int):
                bad.append(f"{ttype}/{sid} 时长不是整数微秒（{td!r} / {sd!r}）")
                continue
            # 不变式①：source = target × speed（容差 1µs）
            if abs(sd - td * float(sp)) > 1:
                bad.append(f"{ttype}/{sid} 源时长不变式不成立：source={sd} target={td} speed={sp}")
            # 不变式②：target 是帧的整数倍（容差 1µs）
            if not is_frame_multiple(td, fps, tol=1):
                bad.append(f"{ttype}/{sid} target={td}µs 不是 {fps}fps 的整数帧")
            if seg.get("material_id") not in mids:
                bad.append(f"{ttype}/{sid} 指向不存在的 material：{seg.get('material_id')}")
            if not isinstance(tr_t.get("start"), int) or not isinstance(sr_t.get("start"), int):
                bad.append(f"{ttype}/{sid} 起点不是整数微秒")
    for k, want in expect.items():
        if counts.get(k, 0) != want:
            bad.append(f"{k} 轨道片段数 {counts.get(k, 0)} ≠ 源时间轴的 {want}")

    # 素材路径必须都在草稿目录里（剪映沙盒只认草稿目录内的东西）
    root_str = str(draft_dir.resolve())
    for kind in ("videos", "audios", "images"):
        for m in content.get("materials", {}).get(kind) or []:
            p = str(m.get("path") or "")
            if Path(p).is_absolute() or ".." in Path(p).parts:
                bad.append(f"{kind} 里还有外部绝对路径：{p}")
            elif not (draft_dir / p).exists():
                bad.append(f"{kind} 素材不在草稿目录里：{p}")
    for it in (meta.get("draft_materials") or [{}])[0].get("value") or []:
        p = str(it.get("file_Path") or "")
        if Path(p).is_absolute() or not (draft_dir / p).exists():
            bad.append(f"draft_materials 引用了草稿目录外的文件：{p}")
    if not (draft_dir / str(meta.get("draft_cover") or JY_COVER_FILE)).exists():
        bad.append("封面文件缺失")

    # 总时长对得上（＝视频轨每段时长相加，不含音轨/字幕轨）
    total = sum(int((sg.get("target_timerange") or {}).get("duration") or 0)
                for tr in content.get("tracks") or [] if tr.get("type") == "video"
                for sg in tr.get("segments") or [])
    if content.get("duration") != total:
        bad.append(f"总时长 {content.get('duration')} ≠ 视频轨合计 {total}")
    if str(Path(meta.get("draft_fold_path") or "").resolve()) != root_str:
        bad.append("meta.draft_fold_path 与草稿目录不一致")
    return bad


# ── 主流程 ──────────────────────────────────────────────────────────────
def find_mp4(proj: Path) -> str | None:
    """降级时要给用户的 mp4 路径（只报已存在的，不猜）。"""
    out = proj / "out"
    for name in ("film-hf.mp4", "film.mp4", "成片.mp4"):
        if (out / name).exists():
            return str(out / name)
    if out.is_dir():
        cands = sorted((p for p in out.glob("*.mp4") if not p.name.startswith("_")),
                       key=lambda p: -p.stat().st_mtime)
        if cands:
            return str(cands[0])
    return None


def write_report(proj: Path, result: dict) -> Path:
    out = proj / "out"
    out.mkdir(parents=True, exist_ok=True)
    p = out / "jianying-export.json"
    p.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def export(a) -> tuple[int, dict]:
    """跑一次导出，返回 (退出码, 报告)。--dry-run 时一行都不落盘。"""
    proj = Path(a.project).expanduser().resolve()
    if not proj.is_dir():
        return 3, {"status": "error", "reason": f"项目目录不存在：{proj}"}
    fps = float(a.fps)
    if fps <= 0:
        return 3, {"status": "error", "reason": f"--fps 非法：{a.fps}"}
    lib = resolve_lib(proj)

    root, root_notes = pick_draft_root(a.draft_root)
    warnings: list[str] = list(root_notes)
    fp, fp_notes = probe_fingerprint(root)
    warnings.extend(fp_notes)
    probed = bool(fp)
    if not probed:
        fp = dict(CONSERVATIVE_FP)
    else:
        warnings.append(
            f"本机版本指纹（只读探测自草稿根）：app_version={fp.get('app_version')} · "
            f"app_source={fp.get('app_source')} · version={fp.get('version')} · "
            f"new_version={fp.get('new_version')}")
        v = fp.get("version")
        if isinstance(v, int) and v < MAX_VERSION:
            warnings.append(f"本机草稿 version={v} < {MAX_VERSION}，剪映可能提示「升级草稿」")

    name = (a.name or proj.name).strip()
    draft_dir = Path(a.out).expanduser().resolve() if a.out else (root / name)

    plan = collect_plan(proj, lib, fps)
    warnings.extend(plan.get("warnings") or [])
    base_report = {"app_version": fp.get("app_version"),
                   "version": fp.get("version"), "app_source": fp.get("app_source"),
                   "probed": probed, "warnings": warnings,
                   "mode": "one-way-export", "fps": fps,
                   "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}

    # ① 版本指纹不合 → 不写，只出 mp4
    ok, why = fingerprint_verdict(fp if probed else {})
    if not ok:
        mp4 = find_mp4(proj)
        reason = (f"{why}。这条片只出 mp4：{mp4 or '（还没渲，先跑 render_hf.py）'}；"
                  f"要精修就用这个 mp4 在剪映里手动剪辑。")
        # draft_path 留空：真没写出东西；想覆盖的路径记在 would_be_path。
        rep = {**base_report, "status": "refused", "reason": reason, "mp4": mp4,
               "draft_path": "", "would_be_path": str(draft_dir)}
        print(f"⛔ 拒绝写草稿：{why}")
        print(f"   降级方案：{reason}")
        return 2, rep

    # ② 前置条件（画面元素不全有独立媒体 / 缺 timeline）
    if not plan.get("ok"):
        blockers = plan.get("blockers") or []
        reason = ("这些画面元素没有可导出的独立媒体文件，导出的草稿会缺画面，所以不写："
                  + "；".join(blockers[:6])
                  + ("……" if len(blockers) > 6 else "")
                  + "。要么先把它们渲进成片后用 mp4 精修，要么手工在剪映里补。")
        mp4 = find_mp4(proj)
        rep = {**base_report, "status": "degraded", "reason": reason, "mp4": mp4,
               "draft_path": "", "would_be_path": str(draft_dir)}
        print(f"🟡 不写草稿（降级为只出 mp4）：{reason}")
        return 1, rep

    media = plan_media(plan, draft_dir)
    expect = {"video": len(plan["blocks"]), "text": len(plan["texts"])}
    if plan["audios"]:
        expect["audio"] = len(plan["audios"])

    if a.dry_run:
        print(f"（--dry-run，不落盘）将写入草稿：{draft_dir}")
        print(f"  主文件 {JY_MAIN_FILE} · {JY_META_FILE} · {JY_COVER_FILE} · "
              f"{len(media)} 个素材副本 → {JY_ASSETS_DIR}/")
        print(f"  版本指纹：app_version={fp.get('app_version')} · version={fp.get('version')} "
              f"· app_source={fp.get('app_source')} · {'探测到' if probed else '未探测到（保守值）'}")
        print(f"  轨道：video {expect['video']} 段 · audio {expect.get('audio', 0)} 段 · "
              f"text {expect['text']} 段 · 总 {us_of_frames(plan['total_frames'], fps)/1e6:.3f}s")
        print(f"  字幕：字号 {plan['sub']['size_px']}px（{plan['sub']['size_src']}）"
              f"→ 剪映 size {round(plan['sub']['size_px'] * (SIZE_REF_SHORT / min(plan['canvas']['width'], plan['canvas']['height'])) / SIZE_UNIT_PX, 2)}"
              f" · 纵向中心 {plan['sub']['center_px']}px（{plan['sub']['center_src']}）"
              f"→ transform.y {round((0.5 - plan['sub']['center_px'] / plan['canvas']['height']) * 2, 4)}")
        for w in warnings:
            print(f"  ⚠️ {w}")
        return 0, {**base_report, "status": "dry-run", "reason": "只打印，不落盘",
                   "draft_path": "", "would_be_path": str(draft_dir)}

    # ③ 真的写：目录已存在就不是我们造的，--force 才接管
    if draft_dir.exists() and any(draft_dir.iterdir()):
        if not a.force:
            rep = {**base_report,
                   "status": "degraded",
                   "reason": f"目标草稿目录已存在且非空：{draft_dir}（要覆盖加 --force；"
                             f"或者用 --name 换个草稿名）",
                   "draft_path": "", "would_be_path": str(draft_dir)}
            print(f"🟡 没写：{rep['reason']}")
            return 1, rep
        shutil.rmtree(draft_dir)
        print(f"   ⚠️ --force：已删除旧目录 {draft_dir}")
    (draft_dir / JY_ASSETS_DIR).mkdir(parents=True, exist_ok=True)

    try:
        for src, rel in media.items():
            dst = draft_dir / rel
            shutil.copy2(src, dst)
        # meta.draft_root_path 跟草稿目录的实际父目录对齐：--out 指到别处时，
        # 写探测到的剪映草稿根会让剪映在设置页显示一个不存在的路径。
        content, meta = build_draft(plan, fp, fps, name, draft_dir, draft_dir.parent, media)
        (draft_dir / JY_MAIN_FILE).write_text(
            json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
        cover_how = write_cover(draft_dir, plan["blocks"][0]["src"] if plan["blocks"] else None)
        meta["draft_timeline_materials_size_"] = sum(
            p.stat().st_size for p in (draft_dir / JY_ASSETS_DIR).iterdir())
        (draft_dir / JY_META_FILE).write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:                                    # 写盘炸了也要清干净
        shutil.rmtree(draft_dir, ignore_errors=True)
        rep = {**base_report, "status": "degraded",
               "reason": f"写草稿时出错（{exc}），已清掉半成品目录",
               "mp4": find_mp4(proj), "draft_path": "", "would_be_path": str(draft_dir)}
        print(f"🟡 {rep['reason']}")
        return 1, rep

    # ④ 结构自检：不过就整目录删掉，不留坏草稿
    bad = structure_check(draft_dir, expect, fps)
    if bad:
        shutil.rmtree(draft_dir, ignore_errors=True)
        rep = {**base_report, "status": "degraded",
               "reason": ("草稿结构自检没过（已删除半成品目录，不留坏草稿）："
                          + "；".join(bad[:5]) + ("……" if len(bad) > 5 else "")),
               "failed_checks": bad, "mp4": find_mp4(proj),
               "draft_path": "", "would_be_path": str(draft_dir)}
        print(f"🟡 {rep['reason']}")
        return 1, rep

    rep = {**base_report, "status": "ok", "reason": "",
           "draft_path": str(draft_dir),
           "tracks": expect,
           "duration_s": round(us_of_frames(plan["total_frames"], fps) / 1e6, 3),
           "cover": cover_how,
           "subtitle": {"size_px": plan["sub"]["size_px"],
                        "size_src": plan["sub"]["size_src"],
                        "center_px": plan["sub"]["center_px"],
                        "center_src": plan["sub"]["center_src"]},
           "checks": ["文件齐全", "JSON 可解析", "必要字段齐全", "片段数与源时间轴一致",
                      "微秒整数", "源/目标时长不变式", "帧网格对齐",
                      "素材路径全在草稿目录内"],
           "caveat": ("结构自检通过 ≠ 剪映一定能打开：本工具只验文件形状，"
                      "打不打得开、观感对不对只能打开看。草稿是一次性的——"
                      "剪映 6.0+ 打开后就地加密，本工具读不回来，精修请直接在剪映里做完出片。")}
    print(f"✅ 草稿已写出：{draft_dir}")
    print(f"   轨道：video {expect['video']} 段 · audio {expect.get('audio', 0)} 段 · "
          f"text {expect['text']} 段 · 总 {rep['duration_s']}s · 封面 {cover_how}")
    print(f"   版本指纹：app_version={fp.get('app_version')} · version={fp.get('version')} "
          f"· app_source={fp.get('app_source')} · {'探测到' if probed else '未探测到（保守值）'}")
    for w in warnings:
        print(f"   ⚠️ {w}")
    print("   ⚠️ 结构自检通过 ≠ 剪映一定能打开：只验了文件形状。"
          "草稿是单向一次性交付——剪映 6.0+ 打开后会就地加密，工具读不回来。")
    return 0, rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把项目时间轴单向导出成剪映草稿（供人工精修；写出后剪映会就地加密，工具读不回来）")
    ap.add_argument("--project", required=True, help="项目目录（其下应有 timeline.json）")
    ap.add_argument("--out", default=None, help="草稿目录（默认 <草稿根>/<草稿名>）")
    ap.add_argument("--draft-root", default=None,
                    help="剪映草稿根（默认自动探测 ~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft）")
    ap.add_argument("--name", default=None, help="草稿名（默认取项目目录名）")
    ap.add_argument("--fps", type=float, default=30.0, help="帧率，所有时长吸附到 1/fps（默认 30）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将写入什么，不落盘")
    ap.add_argument("--force", action="store_true", help="目标草稿目录非空时覆盖（会先删掉）")
    a = ap.parse_args(argv)

    code, rep = export(a)
    if not a.dry_run and rep.get("status") not in ("error", "dry-run"):
        p = write_report(Path(a.project).expanduser().resolve(), rep)
        print(f"   结果记录：{p}")
    if rep.get("status") == "error":
        print(f"❌ {rep.get('reason')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
