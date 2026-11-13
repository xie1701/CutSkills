#!/usr/bin/env python3
"""CutSkills · 卡的叠加态布局器（原则 B7 图底主权 / B10 局部底衬）

一件只做一次决定的事：**这张卡放在哪、底片让不让位**。

为什么必须单开一层（2026-11-13 第五轮对抗式审查）：
渲染器以前只有一种叠加态——整屏 radial 暗场 + 卡钉死在 `21%–62%H` 那条带里。
于是成片里出现「VS 圆章正贴在主讲人两眼之间」「600㎡ / 200㎡ 把脸压成一块黑板」
「四大天王黑膏药盖住菜盘上半截」，而 8 道脚本全绿——因为没有一道判据知道人脸在哪。
位置这种事不该由「一条固定百分比带」决定，它得由**这一格画面上有什么**决定。

三种合法叠加态（B7，三选一，没有第四种）：

| mode | 什么时候 | 底片怎么处理 | 卡 |
| --- | --- | --- | --- |
| `corner` | 实拍是主角（轻量标签、短数据） | **一点不压暗**，只在卡背后垫一块羽化局部底衬 | 缩进合法角/边 |
| `focus_blur` | 重型卡登场、底片是实拍人像 | 底片高斯模糊退后（22px）+ 局部半透明底板 | 占最大合法区 |
| `cutaway` | 没有真人脸、或允许短暂切走的格子 | 整屏切到品牌语域纯底 | 居中整屏图形 |

硬红线（不可配置）：`medium: talking` 且人占画面 ≥15% 时**禁止整屏暗场**，
卡只能走 `corner` / `focus_blur`；任何叠加态下卡与任一帧人脸框的 IoU 必须 < 2%。

判据实现：`scripts/check_overlay.py`（渲前闸门，error 退出码 7）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
NATIVE = Path(__file__).resolve().parent / "native" / "person_mask"

# 重型 MG 卡：一屏多要素（数字/两栏/三步/词条），必须把视觉主权拿走。
HEAVY_KINDS = {"stat", "proportion", "day-night", "versus", "steps", "term"}
LIGHT_KINDS = {"keyword", "keyword-big", "behind", "cutout", "idcard"}

FACE_IOU_MAX = 0.02        # 卡与任一帧人脸框的重叠率上限（B7 硬线，禁止调大）
COVER_TALKING_MAX = 0.15   # 口播格人占画面 ≥ 这个数 → 禁止整屏暗场
BLUR_PX = 22               # focus_blur 的高斯半径（B7 给的 20–28 区间中位）
FACE_PAD = 0.02            # 布局时的安全余量：跳切推近会让画面最多挪 ~1.5%W

# 一张卡「自然占多大」。宽高都是画幅比例；缩放只缩这两个数，字号跟着等比缩。
WANT = {
    "stat": (0.80, 0.34),
    "versus": (0.86, 0.32),
    "steps": (0.74, 0.38),
    "term": (0.66, 0.28),
    "proportion": (0.80, 0.26),
    "day-night": (0.82, 0.34),
    "keyword": (0.72, 0.20),
    "keyword-big": (0.72, 0.20),
    "idcard": (0.40, 0.16),
}
SCALES = (1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52)

SAFE = (0.04, 0.06, 0.96, 0.90)   # x0, y0, x1, y1：平台 UI / 圆角安全区
MIN_AREA = {"heavy": 0.075, "light": 0.055}   # 再小的卡读不完，宁可不摆


# ---------------------------------------------------------------- 几何小工具

def fbox(f) -> list[float]:
    """人脸记录（dict 或 list）→ [x, y, w, h]。Vision 输出是 dict，判据里有两种形态。"""
    if isinstance(f, dict):
        return [float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"])]
    return [float(v) for v in f[:4]]


def iou(a, b) -> float:
    """两个 [x, y, w, h]（归一化）的交并比。"""
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def hits(a, b) -> bool:
    """两个框有没有实质相交（面积>0）。"""
    return iou(a, b) > 0


def pad_box(b, dx, dy):
    return [b[0] - dx, b[1] - dy, b[2] + 2 * dx, b[3] + 2 * dy]


def clip_box(b):
    x0, y0 = max(0.0, b[0]), max(0.0, b[1])
    x1, y1 = min(1.0, b[0] + b[2]), min(1.0, b[1] + b[3])
    return [x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)]


def union_boxes(boxes):
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


def want_of(kind: str) -> tuple[float, float]:
    return WANT.get(kind, (0.78, 0.30))


def weight_of(kind: str) -> str:
    return "light" if kind in LIGHT_KINDS else "heavy"


# ---------------------------------------------------------------- 底片数据

def _stale(dst: Path, src: Path) -> bool:
    """缓存在、比素材新、而且**比工具本身也新**才算可用。

    只比素材 mtime 会在「重新编译 person_mask（换了算法/修了旋转）之后」继续吃旧结果——
    这种错最安静：缓存文件在、数字有、就是错的（T-019 实测过一次）。"""
    if not dst.exists() or dst.stat().st_size < 3:
        return False
    if dst.stat().st_mtime < src.stat().st_mtime:
        return False
    if NATIVE.exists() and dst.stat().st_mtime < NATIVE.stat().st_mtime:
        return False
    return True


def faces_of(proj: Path, clip: Path, fps: float = 2.0) -> list[dict] | None:
    """这一段素材里每一帧的人脸框（归一化，原点左上）。

    缓存写在 `hf/work/faces/<clip>.json`，素材比缓存新就重算。
    返回 None 表示**取不到**（缺编译产物 / 解码失败）——调用方必须当「没数据」处理，
    不许当「没有脸」：前者要降级、要报 warning，后者才有资格整屏切走。
    """
    dst = proj / "hf" / "work" / "faces" / (clip.stem + ".json")
    if _stale(dst, clip):
        try:
            return json.loads(dst.read_text()) or []
        except json.JSONDecodeError:
            pass
    if not NATIVE.exists():
        print("  ！缺 scripts/native/person_mask（swiftc -O person_mask.swift -o person_mask），"
              "人脸框拿不到", file=sys.stderr)
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(NATIVE), "--faces", str(clip), "0", f"{_dur(clip)}", str(fps)],
                       capture_output=True, text=True)
    try:
        rows = json.loads((r.stdout or "").strip() or "[]")
    except json.JSONDecodeError:
        print(f"  ！人脸框解析失败：{clip.name}（{r.returncode}）{(r.stderr or '')[-160:]}",
              file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"  ！人脸框没跑起来：{clip.name}（{r.returncode}）{(r.stderr or '')[-160:]}",
              file=sys.stderr)
        return None
    dst.write_text(json.dumps(rows, ensure_ascii=False))
    return rows


def _dur(clip: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(clip)], capture_output=True, text=True)
    try:
        return max(0.2, float((r.stdout or "1").strip()))
    except ValueError:
        return 1.0


def person_cover(proj: Path, clip: Path, t0: float, t1: float, W: int, H: int,
                 fps: float = 3.0) -> float | None:
    """人占画面多少（遮罩白像素比例，窗口内的最大值）。

    为什么取最大值不取均值：判「这一格能不能整屏压暗」问的是「有没有那么一瞬
    人是主角」。取均值会让一个人在画面里走动、某几帧侧身变小的镜头混过去。
    """
    import numpy as np
    from PIL import Image
    mdir = proj / "hf" / "work" / "pstats" / clip.stem
    stamp = mdir / "_stamp.json"
    need = abs(t1 - t0)
    ok = False
    if stamp.exists():
        try:
            _st = json.loads(stamp.read_text())
            ok = (float(_st.get("mtime") or 0) >= clip.stat().st_mtime
                  and (not NATIVE.exists()
                       or float(_st.get("bin") or 0) >= NATIVE.stat().st_mtime))
        except json.JSONDecodeError:
            ok = False
    if not ok and NATIVE.exists():
        mdir.mkdir(parents=True, exist_ok=True)
        for f in mdir.glob("f*.png"):
            f.unlink()
        subprocess.run([str(NATIVE), str(clip), "0", f"{_dur(clip)}", str(mdir), str(fps)],
                       capture_output=True, text=True)
        stamp.write_text(json.dumps({"mtime": clip.stat().st_mtime,
                                     "bin": NATIVE.stat().st_mtime if NATIVE.exists() else 0}))
    fs = sorted(mdir.glob("f*.png")) if mdir.exists() else []
    if not fs:
        return None
    # 帧序 × 步长 ≈ 时间：这里只需要「窗口里那几帧」，按比例挑
    step = 1.0 / fps
    vals = []
    for i, f in enumerate(fs):
        t = i * step
        if t < t0 - 0.05 or t > t1 + 0.05:
            continue
        a = np.asarray(Image.open(f).convert("L").resize((W, H)))
        vals.append(float((a > 128).mean()))
    if not vals:                                  # 窗口比采样还短：整段取最大
        for f in fs:
            a = np.asarray(Image.open(f).convert("L").resize((W, H)))
            vals.append(float((a > 128).mean()))
    return round(max(vals), 4) if vals else None


# ---------------------------------------------------------------- 布局搜索

def _anchors(w: float, h: float):
    """九宫格锚点 + 上下三分之一。给的是卡左上角的位置。"""
    x0, y0, x1, y1 = SAFE
    xl, xc, xr = x0, (1.0 - w) / 2, x1 - w
    yt, ym, yb = y0, max(y0, (1.0 - h) / 2 - 0.04), y1 - h
    out = []
    for x in (xl, xc, xr):
        for y in (yt, ym, yb):
            out.append((round(x, 4), round(y, 4)))
    # 下三分之一（broadcast 的 lower-third 位）：重心比正中低一点，读起来最顺
    out.append((round(xc, 4), round(min(y1 - h, 0.56 - h / 2), 4)))
    return out


def _legal(box, faces, band, subs_on, min_area):
    area = box[2] * box[3]
    if area < min_area:
        return False
    # 安全区
    if box[0] < SAFE[0] - 1e-6 or box[1] < SAFE[1] - 1e-6:
        return False
    if box[0] + box[2] > SAFE[2] + 1e-6 or box[1] + box[3] > SAFE[3] + 1e-6:
        return False
    # 人脸：任一帧的框都不许压（padding 已含跳切位移余量）
    for f in faces:
        if iou(box, pad_box(f, FACE_PAD * 0.5, FACE_PAD * 0.5)) >= FACE_IOU_MAX:
            return False
    # 字幕带：字幕还在的时候卡不许压上去（压上去就是两套字打架）
    if subs_on and band:
        if hits(box, [0.0, band[0], 1.0, band[1] - band[0]]):
            return False
    return True


def _search(kind, faces, band, subs_on, heavy, scale_cap=1.0):
    """在缩放梯度上找「面积最大且合法」的一格。返回 (box, scale) 或 None。"""
    wn, hn = want_of(kind)
    min_area = MIN_AREA["heavy" if heavy else "light"]
    best = None
    for k in SCALES:
        kw = min(scale_cap, k)
        w, h = round(wn * kw, 4), round(hn * kw, 4)
        if w * h < min_area:
            continue
        for (x, y) in _anchors(w, h):
            box = [x, y, w, h]
            if not _legal(box, faces, band, subs_on, min_area):
                continue
            # 分数：面积为主；靠边/靠角的位比正中更像「挂」，正中最容易撞脸也最像贴脸
            edge = 1.0 if (abs(x - (1 - w) / 2) > 1e-3 or abs(y - (1 - h) / 2) > 1e-3) else 0.94
            score = w * h * edge
            if best is None or score > best[2]:
                best = (box, kw, score)
    return best


# 羽化半径（按画幅换算的像素），T-019 复核②。
# 上一版 `feather_px` 算了却**没接进 CSS**：mask 写的是「径向 72%×66%、实心到 58%」，
# 椭圆半径比半个板大，于是板边界上 alpha 还有 ~26% 就被一刀切断——1080 原像素上
# 只剩一圈 20px 的过渡，读起来仍是「圆角矩形硬轮廓 + 贴片」。
# 现在羽化带是**定宽像素、四边都有**，且在板边界上恰好降到 0（没有任何一条边被硬切）：
# corner 3.8%W（1080 下 41px）、focus_blur 8.0%H（1920 下 154px），
# 再按「不超过板短边的 22%」封顶（如 812×684 的板上 154px 压到 150px），
# 因为羽化比板还宽就不叫羽化，叫一团雾。
PLATE_FEATHER = (0.038, 0.080)      # (corner: ×W, focus_blur: ×H)
PLATE_PAD = (0.052, 0.038)          # (×W, ×H)：留出余地，羽化带不许啃到卡本体
PLATE_FEATHER_MAX = 0.22            # 羽化带最多吃掉板短边的这么一截


def _plate(mode, box, faces, W, H):
    """底衬跟着卡走（B10）：不再是「整屏 90%」，而是卡背后一块带羽化的板。

    T-019 复核②把 focus_blur 的底衬从「横向铺满的一条带」收成「贴着卡的一块」：
    满宽暗带本质上还是「压暗全屏的一条」，卡的圆角矩形坐在上面就成了「贴片」；
    收成局部板之后，连 backdrop-filter 的模糊也只发生在卡背后那一块，
    再经羽化带过渡回清晰——这才读得出「底片退后 + 一块板压在上面」。
    """
    if mode == "cutaway":
        return [0.0, 0.0, 1.0, 1.0], 0
    padx, pady = PLATE_PAD
    px0 = max(0.0, box[0] - padx)
    px1 = min(1.0, box[0] + box[2] + padx)
    py0 = max(0.0, box[1] - pady)
    py1 = min(1.0, box[1] + box[3] + pady)
    plate = [round(px0, 4), round(py0, 4), round(px1 - px0, 4), round(py1 - py0, 4)]
    f = int(round((PLATE_FEATHER[1] * H) if mode == "focus_blur"
                  else (PLATE_FEATHER[0] * W)))
    f = min(f, int(round(PLATE_FEATHER_MAX * min(plate[2] * W, plate[3] * H))))
    return plate, max(12, f)


def plan(card: dict, ctx: dict) -> dict:
    """一次决定：这张卡用哪种叠加态、放哪、底衬多大。

    card: {"kind", "start", "end", "data"?, "weight"?}
    ctx:  {"W","H","faces":[…],"cover":float|None,"media":["talking",…],
           "prev_medium","next_medium","sub_band":(y0,h)|None,"subs_on":bool,
           "override":{…}}
    返回值里的 box/plate 都是**画幅比例**；px 由渲染器按 W/H 换算。
    """
    W, H = int(ctx["W"]), int(ctx["H"])
    kind = str(card.get("kind") or "stat")
    heavy = weight_of(kind) == "heavy"
    faces = [fbox(f) for f in (ctx.get("faces") or [])]
    media = [str(m) for m in (ctx.get("media") or [])]
    dur = round(float(card["end"]) - float(card["start"]), 3)
    band = ctx.get("sub_band")
    subs_on = bool(ctx.get("subs_on", True))
    cover = ctx.get("cover")
    face_src = ctx.get("face_src") or ("vision" if faces else "none")

    # 视觉主权红线：口播 + 人占 ≥15% → 不许整屏暗场
    talking_hit = ("talking" in media) and (cover is None or float(cover) >= COVER_TALKING_MAX)
    if cover is None and "talking" in media:
        why_cover = "人占比量不出来（遮罩缺失）→ 按 ≥15% 保守处理"
    else:
        why_cover = f"人占画面 {0 if cover is None else round(float(cover) * 100)}%"

    # cutaway 的入场券（B7 第三种态）：没有真人脸，或允许短暂切走
    nb = [str(ctx.get("prev_medium") or ""), str(ctx.get("next_medium") or "")]
    brief_broll = dur <= 2.5 and all(m in ("broll", "photo") for m in nb) and nb[0] and nb[1]
    cutaway_ok = (face_src == "vision" and not faces) or \
                 (media and all(m != "talking" for m in media)) or brief_broll
    if talking_hit:
        cutaway_ok = False

    ov = ctx.get("override") or {}
    if ov:
        # 人工覆写：只认这三个字段，别的一律不信（otherwise 就成了「谁都能改判据」）
        mode = str(ov.get("mode") or "")
        if mode in ("corner", "focus_blur", "cutaway"):
            box = ov.get("box") or None
            if box and len(box) == 4:
                box = [round(float(v), 4) for v in box]
            else:
                got = _search(kind, faces, band, subs_on, heavy)
                box = got[0] if got else [0.06, 0.56, want_of(kind)[0] * 0.8, want_of(kind)[1] * 0.8]
            plate, feather = _plate(mode, box, faces, W, H)
            if ov.get("plate") and len(ov["plate"]) == 4:
                plate = [round(float(v), 4) for v in ov["plate"]]
            k = round(box[2] / want_of(kind)[0], 3) if want_of(kind)[0] else 1.0
            return _pack(mode, box, plate, feather, k, faces, ctx, W, H,
                         f"人工覆写：{ov.get('why') or 'timeline card.layout'}", face_src, False)

    # ① cutaway：没有真人脸 + 不是口播 + 卡够长（不足 2.5s 切走再切回是眨眼）
    if heavy and cutaway_ok and face_src == "vision" and not faces and dur >= 2.5 \
            and "talking" not in media:
        box = [(1 - want_of(kind)[0]) / 2, max(SAFE[1], 0.50 - want_of(kind)[1] / 2),
               want_of(kind)[0], want_of(kind)[1]]
        box = [round(v, 4) for v in box]
        plate, feather = _plate("cutaway", box, faces, W, H)
        return _pack("cutaway", box, plate, feather, 1.0, faces, ctx, W, H,
                     f"{dur:.1f}s 空镜无脸 → 整屏纯底切出，底片主权正式移交卡片", face_src, False)

    # ② focus_blur：重型卡要舞台，底片模糊退后，卡仍避脸
    if heavy:
        got = _search(kind, faces, band, subs_on, heavy)
        if got:
            box, k, _ = got
            plate, feather = _plate("focus_blur", box, faces, W, H)
            return _pack("focus_blur", box, plate, feather, k, faces, ctx, W, H,
                         f"重型卡；底片模糊 {BLUR_PX}px 退后 + 局部底板，{why_cover}", face_src, False)
    # ③ corner：轻量挂角，一点不压暗
    got = _search(kind, faces, band, subs_on, heavy)
    if got:
        box, k, _ = got
        plate, feather = _plate("corner", box, faces, W, H)
        return _pack("corner", box, plate, feather, k, faces, ctx, W, H,
                     f"挂角不压暗（{k:.2f}×），{why_cover}", face_src, False)

    # ④ 哪都放不下：退到「最小尺寸 + 离脸最远」，并如实标 degraded 交给闸门
    wn, hn = want_of(kind)
    best, best_iou = None, 2.0
    for k in SCALES:
        w, h = wn * k, hn * k
        for (x, y) in _anchors(w, h):
            box = [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]
            worst = max((iou(box, pad_box(f, FACE_PAD * 0.5, FACE_PAD * 0.5))
                         for f in faces), default=0.0)
            if worst < best_iou:
                best, best_iou = box, worst
    box = best or [0.06, 0.60, wn * 0.6, hn * 0.6]
    plate, feather = _plate("focus_blur", box, faces, W, H)
    return _pack("focus_blur", box, plate, feather,
                 round(box[2] / wn, 3) if wn else 1.0, faces, ctx, W, H,
                 f"找不到完全避脸的位置（最小重叠 {best_iou * 100:.1f}%）→ 降级，交闸门判红",
                 face_src, True)


def _pack(mode, box, plate, feather, k, faces, ctx, W, H, why, face_src, degraded):
    W = int(ctx["W"])
    H = int(ctx["H"])
    px = lambda b: [int(round(b[0] * W)), int(round(b[1] * H)),
                    int(round(b[2] * W)), int(round(b[3] * H))]
    wn_k = (box[2] / k) if k else box[2]
    hn_k = (box[3] / k) if k else box[3]
    worst = max((iou(box, f) for f in faces), default=0.0)
    return {
        "mode": mode,
        "box": box,                       # 画幅比例 [x,y,w,h]，原点左上（可视框）
        "plate": plate,                   # 底衬（跟卡走的那一块）
        "box_px": px(box),
        "box_nat_px": px([0.0, 0.0, wn_k, hn_k])[2:],   # 未缩放的卡本体宽高
        "plate_px": px(plate),
        "feather_px": int(feather),
        "blur_px": BLUR_PX if mode == "focus_blur" else 0,
        "scale": round(float(k), 3),
        "face_iou": round(worst, 4),
        "face_refs": list(ctx.get("face_refs") or []),   # 闸门自己重算用：片段 + 在片里的偏移
        "face_src": face_src,             # vision = 真检出；none = 这段一帧脸都没有
        "cover": (None if ctx.get("cover") is None else round(float(ctx["cover"]), 4)),
        "media": list(ctx.get("media") or []),
        "why": why,
        "degraded": bool(degraded),
    }


# ---------------------------------------------------------------- 给渲染器用的整片一遍

def layout_cards(cards: list[dict], *, proj: Path, videos: list[dict], style: dict,
                 W: int, H: int, sub_band=None, subs_on: bool = True,
                 media_of=None, overrides: dict | None = None) -> list[dict]:
    """给每张 MG 卡算出 layout，原地写进 card["layout"]，返回 cards。

    `media_of(seg_n)` 给出该格的介质（talking / broll / photo / gen），
    `videos` 是渲染器已经切好的片段清单（含 start/dur/src），人脸就按片段缓存。
    """
    overrides = overrides or {}
    med = media_of or (lambda n: "")
    # 人脸/遮罩的缓存：一卡一取太贵，同一段素材整片只跑一次
    face_cache: dict[str, list | None] = {}
    cov_cache: dict[str, float | None] = {}
    done = 0
    for c in cards:
        kind = str(c.get("kind") or "")
        if kind not in HEAVY_KINDS:
            continue          # 大字走 .kw 层（有自己的 top_h 与抠像遮挡），不在本层挪
        a_, b_ = float(c["start"]), float(c["end"])
        faces, medias, covers = [], [], []
        face_ok, seen = True, False
        refs: list[dict] = []
        for v in videos:
            v0, v1 = float(v["start"]), float(v["start"]) + float(v["dur"])
            if v0 >= b_ - 0.01 or v1 <= a_ + 0.01:
                continue
            clip = proj / "hf" / v["src"]
            if not clip.exists():
                continue
            seen = True
            key = str(clip)
            if key not in face_cache:
                face_cache[key] = faces_of(proj, clip)
            fs = face_cache[key]
            if fs is None:
                face_ok = False
            refs.append({"clip": clip.stem, "offset": round(v0, 3),
                         "dur": round(float(v["dur"]), 3)})
            m = str(med(int(v["n"])) or "")
            medias.append(m)
            t0 = max(0.0, a_ - v0)
            t1 = min(float(v["dur"]), b_ - v0)
            for f in (fs or []):
                if t0 - 0.25 <= float(f["t"]) <= t1 + 0.25:
                    faces.append({**f, "seg": int(v["n"]), "at": round(v0 + float(f["t"]), 3)})
            if m == "talking":
                ck = f"{key}@{round(t0, 2)}-{round(t1, 2)}"
                if ck not in cov_cache:
                    cov_cache[ck] = person_cover(proj, clip, t0, t1, W, H)
                if cov_cache[ck] is not None:
                    covers.append(cov_cache[ck])
        # 前后格的介质：cutaway 的「短暂切走」判据要看它
        prev_m = next_m = None
        for i, v in enumerate(videos):
            if float(v["start"]) >= a_ - 0.01:
                if i > 0:
                    prev_m = str(med(int(videos[i - 1]["n"])) or "")
                if i + 1 < len(videos):
                    next_m = str(med(int(videos[i + 1]["n"])) or "")
                break
        cover = max(covers) if covers else (None if "talking" in medias else 0.0)
        ctx = {"W": W, "H": H, "faces": faces, "cover": cover,
               "media": sorted(set(medias)), "prev_medium": prev_m, "next_medium": next_m,
               "sub_band": sub_band, "subs_on": subs_on, "face_refs": refs,
               "face_src": ("vision" if (face_ok and seen) else "missing"),
               "override": overrides.get(str(c.get("seg"))) or {}}
        c["layout"] = plan(c, ctx)
        done += 1
    if done:
        print(f"  叠加态：{done} 张 MG 卡完成布局", file=sys.stderr)
    return cards
