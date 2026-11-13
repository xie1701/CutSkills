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
# 卡框里主体的像素占比上限（T-042）：脸只占人身上一小块，避了脸不等于没压主体。
# 实测 dianzan-c 的 term 卡（改前）：整块卡 61% 的像素是人，51% 的板面积压在身上。
# 0.35 = 「背景占大头、主体还能从卡后面认出来」的上限；超过就换位/缩尺/降级。
# 这是判据不是参数：check_overlay 报的那条 warning 用同一个数，两边不许漂。
SUBJ_COVER_MAX = 0.35
BLUR_PX = 22               # focus_blur 的高斯半径（B7 给的 20–28 区间中位）
FACE_PAD = 0.02            # 布局时的安全余量：跳切推近会让画面最多挪 ~1.5%W

# ------------------------------------------------- 大字（keyword / behind）的框与隔离
# T-045：上面那套避脸只服务 MG 卡（stat/versus/term 走 layout_cards），大字从来没量过
# 「字框压没压脸」，也从来没量过「字读不读得出」——`verify_film` 只数花字色占比，
# 字压在眼睛上、字被杂乱背景吃掉，读数全是绿的。这一段的几何与阈值是**渲染器与判据
# 同一份**：`render_hf.build_html` 用 kw_font_px / kw_isolate 画，
# `scripts/check_keyword_text.py` 用同一对函数量，两边许看不许各写一套。
KW_KINDS = ("keyword", "keyword-big", "behind", "cutout")
KW_WIDTH_MIN = 0.62        # 墨迹单排宽度下限（×W）：比这还窄就是「一个字顶一屏」或字号漏了
KW_WIDTH_MAX = 0.78        # 上限：包的 fit_width 硬规则是 ≤0.72，留一点字面（advance≠墨迹）余量
KW_DL_MIN = 12.0           # 墨迹 vs 紧邻背景的 ΔL 下限（与 test_panel_light / T-042 同一个数）
KW_LINE_H = 1.06           # CSS `.kw span` 的 line-height
KW_SIZE_MAX_H = 0.34       # 渲染器的字号上限：H × 这个数
KW_TOUCH_EPS = 0.0         # 字框与人脸框的重叠面积上限：> 0 就是压上了（硬线，禁止调大）
# 「头顶大字」那条路自带的一层顶部渐变（`.scrim`）。它不是一个装饰：大字上移到头顶之后，
# 画面顶部的梁柱管道会从字缝里透出来，渲器就是靠这层把它压下去的。
# 所以**判据量可读性时必须把它合上去**——不合就是在量一张没上屏的中间态：
# `qizao-ground-01` 那张 behind 实测「原始画面 ΔL 11.0（判红）/ 合上渐变 28.4（人眼看的是这个）」。
KW_SCRIM_ALPHA = 0.58      # `.scrim` 顶端的黑浓度（CSS `linear-gradient(to bottom, a, 0)`）
KW_SCRIM_H_MAX = 0.30      # 渐变最高压到画幅的这个比例
KW_SCRIM_PAD = 0.14        # 渐变底边 = 大字上沿 + 这么高
KW_HEAD_MIN_TOP = 0.055    # 头顶大字的上沿下限（再高就顶到平台 UI）
KW_HEAD_GAP = 0.095        # 字底与头顶之间留的距离
# 隔离层缺省（包没写 emphasis.isolate 时用这一份；只有 card.isolate 打开的那张才画）。
# 为什么是「字后局部压暗」而不是「整屏暗场」：整屏暗场在口播格是 B7 红线，
# 而且它改的是整个画面的曝光；一块跟着字框走的柔化板只改「字底下的那几行像素」。
KW_ISOLATE = {"fill": "rgba(18,15,12,.42)", "feather_px": 56,
              "pad_x": 0.05, "pad_y": 0.42, "radius_px": 26}

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


def person_frames(proj: Path, clip: Path, fps: float = 3.0, shrink: int = 4):
    """这份片段的人物遮罩帧：[(相对秒, 布尔数组)]。**None = 量不出来**。

    遮罩缓存与失效判据只有这一份（`person_cover` / 主体框 / 卡压主体全走它）：
    同一份数据两个实现早晚会漂（T-038 就是照这个模式坏的）。

    数组降采样到 1/shrink：判的是「一块矩形里主体的面积比」，
    270×480 与 1080×1920 量出来的比例差 < 0.3%，但内存差 16 倍。
    <=> 返回 `[]` 与返回 `None` 是两件事：
        `[]`  = 量过了，这一段没有人（遮罩全黑）→ 可以走 cutaway；
        `None` = 根本没量（缺编译产物 / 解码失败）→ 必须降级并报 warning。
    """
    import numpy as np
    from PIL import Image
    mdir = proj / "hf" / "work" / "pstats" / clip.stem
    stamp = mdir / "_stamp.json"
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
    step = 1.0 / fps
    out = []
    for i, f in enumerate(fs):
        a = Image.open(f).convert("L")
        w, h = max(1, a.width // shrink), max(1, a.height // shrink)
        m = np.asarray(a.resize((w, h), Image.BOX)) > 128
        out.append((round(i * step, 3), m))
    return out


def person_cover(proj: Path, clip: Path, t0: float, t1: float, W: int, H: int,
                 fps: float = 3.0) -> float | None:
    """人占画面多少（遮罩白像素比例，窗口内的最大值）。

    为什么取最大值不取均值：判「这一格能不能整屏压暗」问的是「有没有那么一瞬
    人是主角」。取均值会让一个人在画面里走动、某几帧侧身变小的镜头混过去。
    `W/H` 只为兼容旧调用点保留（比例与分辨率无关）。
    """
    fr = person_frames(proj, clip, fps)
    if fr is None:
        return None
    vals = [float(m.mean()) for t, m in fr if t0 - 0.05 <= t <= t1 + 0.05]
    if not vals:                                  # 窗口比采样还短：整段取最大
        vals = [float(m.mean()) for _, m in fr]
    return round(max(vals), 4) if vals else 0.0


def subject_box(frames) -> list[float] | None:
    """窗口内主体的**联合外框** [x, y, w, h]（归一化）。量不到就 None。"""
    import numpy as np
    if not frames:
        return None
    x0, y0, x1, y1 = 1.0, 1.0, 0.0, 0.0
    seen = False
    for _, m in frames:
        ys, xs = np.nonzero(m)
        if not len(ys):
            continue
        seen = True
        h, w = m.shape
        x0, y0 = min(x0, xs.min() / w), min(y0, ys.min() / h)
        x1, y1 = max(x1, (xs.max() + 1) / w), max(y1, (ys.max() + 1) / h)
    if not seen:
        return None
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def cover_in(frames, box) -> float | None:
    """卡框里主体的像素占比（窗口内**最大**那帧）。压主体只能这么量。"""
    if frames is None:
        return None
    if not frames:
        return 0.0
    best = 0.0
    for _, m in frames:
        h, w = m.shape
        x0 = max(0, int(round(box[0] * w)))
        y0 = max(0, int(round(box[1] * h)))
        x1 = min(w, int(round((box[0] + box[2]) * w)))
        y1 = min(h, int(round((box[1] + box[3]) * h)))
        if x1 <= x0 or y1 <= y0:
            continue
        best = max(best, float(m[y0:y1, x0:x1].mean()))
    return round(best, 4)


def plate_tint(proj: Path, clip: Path, t: float, alpha: float = 0.26) -> dict | None:
    """底片单帧平均色 → 底板色温。**暖就暖、冷就冷，不写死。**

    为什么要在渲染期量：focus_blur 的底片可能是暖茶楼，也可能是冷白工作室，
    拿一个固定色温的板子压上去，不是「隔夜菜」就是「塑料膜」（T-018 事故的同一病根）。

    做法：ffmpeg 取一帧缩到 1×1 读 RGB，把明度归一到一个不抢眼的档（最大通道≈120），
    保住色相/色比的相对关系，再连同色温标签交给渲器。量不出来返回 None（渲器就用包里的底）。
    """
    from render_hf import ffmpeg_bin          # 延迟 import：render_hf 顶层会 import 本模块
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, t):.3f}",
                        "-i", str(clip), "-frames:v", "1",
                        "-vf", "scale=1:1:flags=area", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True)
    if r.returncode != 0 or len(r.stdout) < 3:
        return None
    pr, pg, pb = r.stdout[0], r.stdout[1], r.stdout[2]
    mx = max(pr, pg, pb, 1)
    k = 120.0 / mx
    cr, cg, cb = int(pr * k), int(pg * k), int(pb * k)
    # 色温判据只在「暖冷」两个标签上说话，不拿它做任何取舍：
    # 分差 < 6 就是中性，中性不许硬说成暖或冷。
    hue = "warm" if cr - cb > 6 else ("cool" if cb - cr > 6 else "neutral")
    return {"tint": f"rgba({cr},{cg},{cb},{alpha:g})", "hue": hue,
            "mean_rgb": [pr, pg, pb]}



# ---------------------------------------------------------------- 大字几何（T-045）

def kw_adv_em(text: str, style: dict) -> float:
    """卡面文本的**可视宽度**（单位 em）：真字体量的 advance ÷ 字号。

    为什么不是字数：Jerry 的规则是「单排撑满屏宽 70%」，撑的是**看得见的宽度**。
    而「3 个是国产」「售楼部 · 酒店」「原料成本差 4–5 倍」里有空格、间隔号、阿拉伯数字——
    它们都不是一个汉字那么宽。按字数当分母，每个空格都白占一整个 em 的额度，
    字就小一整截（T-046 实测：「3 个是国产」6 个字算出 126px → 墨迹只 56.5%W；
    「售楼部 · 酒店」8 个字算出 94px → 56.2%W），判据那条 0.62W 下限于是永远过不去，
    而**画面上根本没有一个字是小的**，只是分母错了。

    纯汉字文本（含全角标点）的 advance 恰好 = 字数 em，所以这一改对
    `dianzan-c` 三张、`proj79` 四张、`naming-01`「柒造」的像素是零影响（T-046 逐张量过）。
    """
    t = str(text or "")
    fp = _font_file((style.get("emphasis") or {}).get("font"))
    if fp is None:                       # 字体找不到：退回字数，别让字号变成 0
        return float(max(1, len(t)))
    try:
        from PIL import ImageFont
        ref = 100.0
        return max(1.0, float(ImageFont.truetype(str(fp), int(ref)).getlength(t)) / ref)
    except OSError:
        return float(max(1, len(t)))


def kw_font_px(text: str, style: dict, W: int, H: int) -> int:
    """渲染器给一张大字卡定的字号（**同一份公式**，判据不许自己再估一个）。

    「fit_width × 画幅宽 ÷ 这张字实际占几 em」，再用 H×0.34 兜住 1–2 个字的极端情况。
    分母是 `kw_adv_em`（真字体的 advance），不是字数——见那个函数的说明。
    包里的 `emphasis.big_keyword_px` 不是这条路的上限：那条只当 CSS 兜底，
    渲器给每个 `.kw span` 都写了 inline font-size，上限实际是 H×0.34（652px@1920）。
    """
    emph = style.get("emphasis") or {}
    fit_w = float(emph.get("fit_width") or 0.70)
    n = kw_adv_em(text, style)
    return min(int(round(W * fit_w / n)), int(H * KW_SIZE_MAX_H))


def kw_top_of(card: dict, style: dict, W: int, H: int, override=None):
    """这张大字卡的上沿（画幅比例）→ (top_h, 要不要顶部渐变, 要不要自己写 inline top)。

    优先级（渲染器与判据同一份）：
      ① `kw_top_override`（抠像放不到脑后时的「头顶大字」：位置由素材里的人头反算，
         并挂一层顶部渐变压住梁柱管道——T-019 留下的路子）；
      ② 卡自己写的 `top_h`（timeline `card.top_h`，人工指定）：**不带**顶部渐变，
         指定位置的人自己负责；
      ③ behind 的 `emphasis.behind_top_h` / keyword 的 `emphasis.top_h`：走 CSS，不写 inline。
    """
    emph = style.get("emphasis") or {}
    if override is not None:
        try:
            return round(float(override), 4), True, True
        except (TypeError, ValueError):
            pass
    owned = card.get("top_h")
    if owned is not None:
        try:
            return round(float(owned), 4), False, True
        except (TypeError, ValueError):
            pass
    if str(card.get("kind") or "") == "behind":
        return round(float(emph.get("behind_top_h") or 0.285), 4), False, False
    return round(float(emph.get("top_h") or 0.44), 4), False, False


def _font_file(name: str | None):
    """字体名 → 盘上的真文件（仅判据侧用：渲染器自己已经有 find_font）。"""
    from render_hf import find_font          # 延迟 import：render_hf 顶层 import 本模块
    return find_font(name) if name else None


def kw_scrim_h(top_h: float) -> float:
    """顶部渐变的高度（画幅比例）：字上沿 + KW_SCRIM_PAD，最多 KW_SCRIM_H_MAX。"""
    return round(min(KW_SCRIM_H_MAX, float(top_h or 0.0) + KW_SCRIM_PAD), 4)


def kw_scrim_alpha(W: int, H: int, top_h: float):
    """顶部渐变的 alpha 场（0–1，H×W）：顶端 `KW_SCRIM_ALPHA`、底边 0。判据合成背景用。"""
    import numpy as np
    h = max(1, min(H, int(round(kw_scrim_h(top_h) * H))))
    a = np.zeros((H, W), dtype=np.float32)
    a[:h, :] = np.linspace(KW_SCRIM_ALPHA, 0.0, h, dtype=np.float32)[:, None]
    return a


def kw_head_override(stats: dict | None) -> float:
    """behind / cutout 放不到脑后时，「头顶大字」的上沿（画幅比例）。**渲器与判据同一份**。

    渲器那边的原话是 `max(0.055, head - 0.095)`，读的是 `render_hf.mask_stats()` 的
    `head_top`；取不到就按 0.15 当头顶（与渲器 `(st or {}).get("head_top") or 0.15` 同义，
    所以 head_top=0.0 也走 0.15）。
    """
    head = float((stats or {}).get("head_top") or 0.15)
    return round(max(KW_HEAD_MIN_TOP, head - KW_HEAD_GAP), 4)


def kw_ink_box(text: str, style: dict, W: int, H: int, top_h: float | None = None):
    """大字**实际会落到画面上的墨迹框** [x, y, w, h]（像素，原点左上）。

    不是「70% 宽」的估算：字体的 advance 与墨迹不是一回事（三个字的「门」就比
    三个汉字窄），所以拿真字体文件量。排版模型与 CSS 对齐：
      · `.kw` 是 `left:0; right:0` 的 flex 容器、`justify-content:center` → 行盒水平居中；
      · `line-height:1.06` → 半行距 = (1.06×字号 − (ascent+descent)) / 2；
      · 墨迹 = 行盒顶 + 半行距 + 字体自己的墨迹偏移。
    实测校准（dianzan-c 三张 keyword 卡，1080 原像素量出来）：预测框与渲出帧的墨迹
    横向差 ≤ 8px、纵向差 ≤ 1px（宽 756 预测 / 742 实测——差的 14px 是 advance 与墨迹的差）。

    返回 None = 字体文件找不到（判不了，调用方必须报出来，不许当合格）。
    """
    from PIL import ImageFont
    emph = style.get("emphasis") or {}
    fp = _font_file(emph.get("font"))
    if fp is None:
        return None
    px = kw_font_px(text, style, W, H)
    try:
        font = ImageFont.truetype(str(fp), px)
    except OSError:
        return None
    asc, desc = font.getmetrics()
    bb = font.getbbox(str(text or ""))            # 墨迹框（相对 ascent-left 原点）
    adv = font.getlength(str(text or ""))
    if top_h is None:
        top_h = float(emph.get("top_h") or 0.44)
    half = (KW_LINE_H * px - (asc + desc)) / 2.0
    x = (W - adv) / 2.0 + bb[0]
    y = top_h * H + half + bb[1]
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    return {"px": [int(round(x)), int(round(y)), int(round(w)), int(round(h))],
            "box": [round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4)],
            "font_px": px, "font": str(fp), "top_h": round(float(top_h), 4),
            "adv_px": int(round(adv))}


def ink_mask(text: str, style: dict, W: int, H: int, top_h: float | None = None):
    """字形掩码（同字号、同位置、同字体），判据拿它当「墨迹落在哪几个像素」。

    `measure_panel` 那一套「笔画 vs 紧邻底板」必须先知道哪几个像素是笔画；
    判据在**渲之前**就要知道这件事，所以用同一份字体文件把字栅格化。
    返回 (mask[H×W bool], box_px) 或 (None, None)。
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    box = kw_ink_box(text, style, W, H, top_h)
    if box is None:
        return None, None
    px = box["font_px"]
    font = ImageFont.truetype(box["font"], px)
    asc, _desc = font.getmetrics()
    half = (KW_LINE_H * px - sum(font.getmetrics())) / 2.0
    x = (W - font.getlength(str(text))) / 2.0
    y = float(box["top_h"]) * H + half
    im = Image.new("L", (W, H), 0)
    ImageDraw.Draw(im).text((x, y), str(text), fill=255, font=font, anchor="la")
    m = np.asarray(im) > 128
    if not m.any():
        return None, None
    return m, box


def parse_rgba(s: str):
    """'rgba(18,15,12,.42)' / '#RRGGBB' → ((r,g,b), alpha 0–1)。判据合成背景用。"""
    import re
    t = str(s or "").strip()
    m = re.match(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)"
                 r"(?:\s*,\s*([\d.]+)\s*)?\)$", t)
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3)))), a
    h = t.lstrip("#")
    if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h):
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), 1.0
    return (0, 0, 0), 0.0


def kw_isolate(style: dict, box_px, W: int, H: int) -> dict:
    """大字隔离层（字后那块柔化压暗）的几何与底色。渲染器与判据同一份。

    包 `emphasis.isolate` 决定它长什么样（色、浓度、柔化半径、外扩），缺字段逐项回退到
    KW_ISOLATE；**画不画**由卡自己决定（timeline `card.isolate`），不是包一改全片的字
    都跟着变——同一包里有的字本来就压在干净背景上，不该平白多一块板。
    """
    emph = style.get("emphasis") or {}
    tok = dict(KW_ISOLATE)
    got = emph.get("isolate")
    if isinstance(got, dict):
        tok.update({k: v for k, v in got.items() if v is not None})
    x, y, w, h = [float(v) for v in box_px]
    px = max(0.0, float(tok["pad_x"]) * W)
    py = max(0.0, float(tok["pad_y"]) * h)
    rect = [round(x - px), round(y - py), round(w + 2 * px), round(h + 2 * py)]
    (r, g, b), a = parse_rgba(tok["fill"])
    return {"px": rect, "feather_px": int(round(float(tok["feather_px"]))),
            "radius_px": int(round(float(tok["radius_px"]))),
            "rgba": f"rgba({r},{g},{b},{a:g})", "rgb": [r, g, b], "alpha": a}


def kw_pod_alpha(geo: dict, W: int, H: int):
    """隔离层在画面上的 alpha 场（0–1，H×W）。判据合成背景用：
    `frame × (1−α) + fill × α`，与渲染器那条 CSS 的 `filter:blur(radius)` 同半径。
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new("L", (W, H), 0)
    x, y, w, h = geo["px"]
    ImageDraw.Draw(im).rounded_rectangle([x, y, x + w, y + h],
                                         radius=max(0, geo["radius_px"]), fill=255)
    f = geo["feather_px"]
    if f > 0:
        im = im.filter(ImageFilter.GaussianBlur(f))
    return np.asarray(im, dtype=np.float32) / 255.0 * float(geo["alpha"])


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


def _legal(box, faces, band, subs_on, min_area, cov=None):
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
    # 主体（T-042，B10）：不只避脸——脸下面还有身体/手上那盘菜。
    # 一块板子把主体盖掉一半，观众看到的是「被拦腰截断的人」，不是「卡浮在上面」。
    # 阈值 SUBJ_COVER_MAX 是判据不是参数：它和 check_overlay 报的那条 warning 同一个数。
    if cov is not None:
        c = cov(box)
        if c is not None and c > SUBJ_COVER_MAX:
            return False
    # 字幕带：字幕还在的时候卡不许压上去（压上去就是两套字打架）
    if subs_on and band:
        if hits(box, [0.0, band[0], 1.0, band[1] - band[0]]):
            return False
    return True


def _search(kind, faces, band, subs_on, heavy, scale_cap=1.0, cov=None):
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
            if not _legal(box, faces, band, subs_on, min_area, cov):
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
    # 主体遮罩（T-042）：`None` = 这一格量不出来，不给判据假装「主体 = 0」。
    subj = ctx.get("subj")
    subj_known = subj is not None

    def cov(box):
        return cover_in(subj, box) if subj_known else None

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
                got = _search(kind, faces, band, subs_on, heavy, cov=cov)
                box = got[0] if got else [0.06, 0.56, want_of(kind)[0] * 0.8, want_of(kind)[1] * 0.8]
            plate, feather = _plate(mode, box, faces, W, H)
            if ov.get("plate") and len(ov["plate"]) == 4:
                plate = [round(float(v), 4) for v in ov["plate"]]
            k = round(box[2] / want_of(kind)[0], 3) if want_of(kind)[0] else 1.0
            return _pack(mode, box, plate, feather, k, faces, ctx, W, H,
                         f"人工覆写：{ov.get('why') or 'timeline card.layout'}", face_src, False)

    # ⑤ 主体量不到（T-042）：**不许把 `cover` 当 0 就完事**。
    # 「这一段没有主角」与「我不知道有没有主角」是两个判断：前者能切走，后者只能挂角。
    # 以前 b-roll 格根本不量（person_cover 只在 talking 格调），于是 cover 硬写 0.0、
    # 卡继续走 focus_blur 压在一条腿上——这就是 dianzan-c 的 c5。
    if heavy and not subj_known:
        got = _search(kind, faces, band, subs_on, heavy)          # 只能避脸
        if got:
            box, k, _ = got
            plate, feather = _plate("corner", box, faces, W, H)
            return _pack("corner", box, plate, feather, k, faces, ctx, W, H,
                         f"主体占比量不出来（缺人物遮罩）→ 降级挂角，{why_cover}，"
                         f"交闸门报 warning", face_src, True)

    # ① cutaway：没有真人脸 + 不是口播 + 卡够长（不足 2.5s 切走再切回是眨眼）
    if heavy and cutaway_ok and face_src == "vision" and not faces and dur >= 2.5 \
            and "talking" not in media:
        box = [(1 - want_of(kind)[0]) / 2, max(SAFE[1], 0.50 - want_of(kind)[1] / 2),
               want_of(kind)[0], want_of(kind)[1]]
        box = [round(v, 4) for v in box]
        plate, feather = _plate("cutaway", box, faces, W, H)
        return _pack("cutaway", box, plate, feather, 1.0, faces, ctx, W, H,
                     f"{dur:.1f}s 空镜无脸 → 整屏纯底切出，底片主权正式移交卡片", face_src, False)

    # ② focus_blur：重型卡要舞台，底片模糊退后，卡仍避脸 + 避主体
    if heavy:
        got = _search(kind, faces, band, subs_on, heavy, cov=cov)
        if got:
            box, k, _ = got
            plate, feather = _plate("focus_blur", box, faces, W, H)
            return _pack("focus_blur", box, plate, feather, k, faces, ctx, W, H,
                         f"重型卡；底片模糊 {BLUR_PX}px 退后 + 局部底板，{why_cover}"
                         f"，卡面主体 {0 if not subj_known else round((cov(box) or 0) * 100)}%",
                         face_src, not subj_known)
    # ③ corner：轻量挂角，一点不压暗
    got = _search(kind, faces, band, subs_on, heavy, cov=cov)
    if got:
        box, k, _ = got
        plate, feather = _plate("corner", box, faces, W, H)
        return _pack("corner", box, plate, feather, k, faces, ctx, W, H,
                     f"挂角不压暗（{k:.2f}×），{why_cover}", face_src, not subj_known)

    # ④ 哪都放不下：退到「最小尺寸 + 离脸/离主体最远」，并如实标 degraded 交给闸门
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
    plate, feather = _plate("corner", box, faces, W, H)
    _c = cov(box)
    return _pack("corner", box, plate, feather,
                 round(box[2] / wn, 3) if wn else 1.0, faces, ctx, W, H,
                 f"找不到「不压脸 + 卡面主体 ≤{SUBJ_COVER_MAX * 100:.0f}%」的位置"
                 f"（最小脸重叠 {best_iou * 100:.1f}%，卡面主体 "
                 f"{'量不出' if _c is None else f'{_c * 100:.0f}%'}）→ 降级挂角，交闸门报红",
                 face_src, True)


def _pack(mode, box, plate, feather, k, faces, ctx, W, H, why, face_src, degraded):
    W = int(ctx["W"])
    H = int(ctx["H"])
    px = lambda b: [int(round(b[0] * W)), int(round(b[1] * H)),
                    int(round(b[2] * W)), int(round(b[3] * H))]
    wn_k = (box[2] / k) if k else box[2]
    hn_k = (box[3] / k) if k else box[3]
    worst = max((iou(box, f) for f in faces), default=0.0)
    _subj = ctx.get("subj")
    _tint = ctx.get("plate_tint") or {}
    return {
        "mode": mode,
        "subject_cov": cover_in(_subj, box),           # 卡框里主体占比（窗口最大帧）
        "subject_box": subject_box(_subj),             # 主体联合外框（量不到就 null）
        "subject_src": "mask" if _subj is not None else "missing",
        "plate_tint": _tint.get("tint"),               # 底片平均色温（量不到就不写）
        "plate_hue": _tint.get("hue"),                 # warm / cool / neutral（量出来的）
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
    mask_cache: dict[str, list | None] = {}
    tint_cache: dict[str, dict | None] = {}
    done = 0
    for c in cards:
        kind = str(c.get("kind") or "")
        if kind not in HEAVY_KINDS:
            continue          # 大字走 .kw 层（有自己的 top_h 与抠像遮挡），不在本层挪
        a_, b_ = float(c["start"]), float(c["end"])
        faces, medias, covers = [], [], []
        subj: list | None = []        # None = 有一格量不出来（这张卡就不能当「主体清楚」用）
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
                cv = person_cover(proj, clip, t0, t1, W, H)
                if cv is not None:
                    covers.append(cv)
            # 主体遮罩（T-042）：**不只 talking 格量**。b-roll 里也可以有一个占满画面的
            # 人（dianzan-c seg 5 就是：通话中景，人占 31%、纵向 17%–100%），
            # 以前它连量都不量，cover 硬写 0.0，卡就心安理得地压在腿上。
            if key not in mask_cache:
                mask_cache[key] = person_frames(proj, clip)
            mf = mask_cache[key]
            if mf is None:
                subj = None
            elif subj is not None:
                subj += [(tt, mm) for tt, mm in mf if t0 - 0.05 <= tt <= t1 + 0.05]
        # 前后格的介质：cutaway 的「短暂切走」判据要看它
        prev_m = next_m = None
        for i, v in enumerate(videos):
            if float(v["start"]) >= a_ - 0.01:
                if i > 0:
                    prev_m = str(med(int(videos[i - 1]["n"])) or "")
                if i + 1 < len(videos):
                    next_m = str(med(int(videos[i + 1]["n"])) or "")
                break
        # 人占画面：现在对**所有**介质都量（以前 b-roll 格是硬写 0.0）。
        # 量不到 = None（「不知道」），与「量到 0%（确实没人）」分开写进 cards.json。
        _all = cover_in(subj, [0.0, 0.0, 1.0, 1.0]) if subj else None
        cover = max(covers) if covers else _all
        # 底板色温（T-042）：取卡窗所在那一格的中点帧的平均色，暖就暖、冷就冷。
        tk = None
        for v in videos:
            v0 = float(v["start"])
            v1 = v0 + float(v["dur"])
            if v0 > b_ or v1 <= a_:
                continue
            mid = min(max(float(c["start"]), v0), max(v0, v1 - 0.05))
            clip = proj / "hf" / v["src"]
            if clip.exists():
                ck2 = f"{clip}@{round(mid - v0, 2)}"
                if ck2 not in tint_cache:
                    tint_cache[ck2] = plate_tint(proj, clip, mid - v0)
                tk = tint_cache[ck2]
            break
        ctx = {"W": W, "H": H, "faces": faces, "cover": cover,
               "media": sorted(set(medias)), "prev_medium": prev_m, "next_medium": next_m,
               "sub_band": sub_band, "subs_on": subs_on, "face_refs": refs,
               "face_src": ("vision" if (face_ok and seen) else "missing"),
               "subj": subj, "plate_tint": tk,
               "override": overrides.get(str(c.get("seg"))) or {}}
        c["layout"] = plan(c, ctx)
        done += 1
    if done:
        print(f"  叠加态：{done} 张 MG 卡完成布局", file=sys.stderr)
    return cards
