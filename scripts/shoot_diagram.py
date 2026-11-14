#!/usr/bin/env python3
"""CutSkills · 拍摄简笔示意：一个镜头一张图，看得懂就够，不要美术效果。

为什么要有它：拍片卡上的字再多，「手机放多高、往哪边推、画面里留多少空」这三件事
仍然只能靠比划。所以每个镜头配一张纯 PIL 画的示意图，只画四件东西：

  ① 取景框 9:16（外加三分线与安全区）——画面边界在哪；
  ② 主体位置与景别——人要占多大、手/物件摆哪；
  ③ 机位高度示意的侧视图——手机放眼睛高还是胸口高，俯还是平；
  ④ 箭头标运动方向——推近/横移/固定，一眼看出要不要动。

图画完交给 `shoot-guide.md` 引用（`<项目>/shoot/diagram-N.png`）。
所有参数都由 `ideate.shot_plan()` 按台词内容算好再传进来，这里只负责画，不做判断。

用法（单独看一眼某张图）：
  shoot_diagram.py --out /tmp/d1.png --size close --height eye --angle 0 --motion push_in \
      --dur 3.2 --takes 3 --label "镜头 1 · 黄金3秒钩子"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 640, 1120
BG = (245, 243, 239)
INK = (36, 31, 26)
MUTED = (120, 112, 104)
LINE = (160, 152, 143)
FILL = (203, 196, 187)
SCREEN = (231, 227, 219)
ACCENT = (206, 66, 37)

# 取景框：306×544 = 9:16（306/544 = 0.5625，正好）
FRAME_X, FRAME_Y, FRAME_W, FRAME_H = 56, 100, 306, 544

# 中文字体候选：机器上装哪个用哪个，找不到就退 PIL 自带位图字体（英文标签仍可读）。
FONT_CANDS = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)
_FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size in _FONTS:
        return _FONTS[size]
    for cand in FONT_CANDS:
        p = Path(cand)
        if not p.exists():
            continue
        try:
            f = ImageFont.truetype(str(p), size)
        except OSError:
            continue
        _FONTS[size] = f
        return f
    return ImageFont.load_default()


def _text(d: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, size: int = 18,
          fill=INK, anchor: str | None = None) -> None:
    d.text(xy, s, font=_font(size), fill=fill, anchor=anchor)


def _text_w(s: str, size: int) -> int:
    try:
        box = _font(size).getbbox(s)
        return int(box[2] - box[0])
    except Exception:                                        # noqa: BLE001
        return int(len(s) * size * 0.6)


def _dashed(d: ImageDraw.ImageDraw, p0: tuple[float, float], p1: tuple[float, float],
            color=LINE, width: int = 2, dash: int = 10) -> None:
    x0, y0 = p0
    x1, y1 = p1
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if length <= 0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    t = 0.0
    while t < length:
        e = min(t + dash, length)
        d.line([(x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)],
               fill=color, width=width)
        t = e + dash * 0.7


def _arrow(d: ImageDraw.ImageDraw, p0: tuple[float, float], p1: tuple[float, float],
           color=ACCENT, width: int = 6, head: int = 16) -> None:
    d.line([p0, p1], fill=color, width=width)
    (x0, y0), (x1, y1) = p0, p1
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 or 1.0
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    bx, by = x1 - ux * head, y1 - uy * head
    d.polygon([(x1, y1), (bx - uy * head * 0.6, by + ux * head * 0.6),
               (bx + uy * head * 0.6, by - ux * head * 0.6)], fill=color)


# ---------------------------------------------------------------- 取景框内画面

def _person(d: ImageDraw.ImageDraw, cx: float, top: float, scale: float,
            fill=FILL, edge=INK, bottom: float | None = None) -> None:
    """一个正面人形：头 + 脖子 + 肩 + 躯干 + 两条手臂。scale=1 时身高约 250px。
    bottom 给定的时候躯干一直画到那条线（表示被取景框下边切掉）。
    只求「一眼看出这是个人、占多少画面」，不追求像不像。"""
    hr = 26 * scale
    d.ellipse([cx - hr, top, cx + hr, top + 2 * hr], fill=fill, outline=edge, width=2)
    neck_y = top + 2 * hr
    d.rectangle([cx - 9 * scale, neck_y - 2, cx + 9 * scale, neck_y + 10 * scale],
                fill=fill, outline=edge, width=2)
    sh_y = neck_y + 8 * scale
    body_h = max(150 * scale, (bottom - sh_y) if bottom is not None else 150 * scale)
    d.rounded_rectangle([cx - 62 * scale, sh_y, cx + 62 * scale, sh_y + body_h],
                        radius=int(26 * scale), fill=fill, outline=edge, width=2)
    for sx in (-1, 1):                                     # 手臂：贴在两边的两条粗线
        ax = cx + sx * 58 * scale
        d.line([(ax, sh_y + 14 * scale), (ax + sx * 6 * scale, sh_y + body_h * 0.78)],
               fill=edge, width=max(2, int(7 * scale)))


def _hands(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float) -> None:
    """手部特写：掌 + 四指 + 手里一件东西 + 对焦虚框。"""
    d.rounded_rectangle([x, y, x + w, y + h * 0.62], radius=18, fill=FILL, outline=INK, width=2)
    for i in range(4):
        fx = x + w * (0.12 + i * 0.24)
        d.rounded_rectangle([fx, y - h * 0.34, fx + w * 0.16, y + h * 0.06],
                            radius=int(w * 0.08), fill=FILL, outline=INK, width=2)
    obj = [x + w * 0.22, y + h * 0.30, x + w * 0.86, y + h * 0.74]
    d.rounded_rectangle(obj, radius=8, fill=SCREEN, outline=INK, width=3)
    d.ellipse([obj[0] - 14, obj[1] - 14, obj[2] + 14, obj[3] + 14],
              outline=ACCENT, width=2)


def _scene(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float) -> None:
    """全景：地面线 + 两侧环境块 + 小比例的人。"""
    ground = y + h * 0.78
    d.line([(x + 8, ground), (x + w - 8, ground)], fill=MUTED, width=2)
    d.rectangle([x + 10, y + h * 0.28, x + w * 0.22, ground], fill=SCREEN, outline=LINE, width=2)
    d.rectangle([x + w * 0.78, y + h * 0.36, x + w - 10, ground],
                fill=SCREEN, outline=LINE, width=2)
    _person(d, x + w * 0.5, ground - h * 0.44, 0.52)


def _screen_only(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float,
                 label: str = "免拍 · 代码画面") -> None:
    """免拍镜头：画一块屏，说明这一段由代码动效承载，不用实拍。"""
    d.rounded_rectangle([x + w * 0.12, y + h * 0.28, x + w * 0.88, y + h * 0.72],
                        radius=14, fill=SCREEN, outline=INK, width=3)
    cx, cy = x + w * 0.5, y + h * 0.5
    d.polygon([(cx - 14, cy - 22), (cx - 14, cy + 22), (cx + 20, cy)], fill=ACCENT)
    _text(d, (cx, y + h * 0.78), label, 18, MUTED, anchor="mm")


def _motion(d: ImageDraw.ImageDraw, motion: str, x: float, y: float, w: float, h: float) -> None:
    """镜头运动方向：画在该动的那一块上，别糊在字上。"""
    cx, cy = x + w * 0.5, y + h * 0.5
    if motion == "push_in":                       # 四角向内收 = 推近
        for (ax, ay, bx, by) in ((x + 18, y + 18, x + 74, y + 74),
                                 (x + w - 18, y + 18, x + w - 74, y + 74),
                                 (x + 18, y + h - 18, x + 74, y + h - 74),
                                 (x + w - 18, y + h - 18, x + w - 74, y + h - 74)):
            _arrow(d, (ax, ay), (bx, by))
    elif motion == "pull_out":
        for (ax, ay, bx, by) in ((x + 74, y + 74, x + 18, y + 18),
                                 (x + w - 74, y + 74, x + w - 18, y + 18),
                                 (x + 74, y + h - 74, x + 18, y + h - 18),
                                 (x + w - 74, y + h - 74, x + w - 18, y + h - 18)):
            _arrow(d, (ax, ay), (bx, by))
    elif motion == "pan_right":
        _arrow(d, (cx - w * 0.26, y + h * 0.12), (cx + w * 0.26, y + h * 0.12))
    elif motion == "pan_left":
        _arrow(d, (cx + w * 0.26, y + h * 0.12), (cx - w * 0.26, y + h * 0.12))
    elif motion == "tilt_down":
        _arrow(d, (x + w * 0.86, cy - h * 0.16), (x + w * 0.86, cy + h * 0.16))
    else:                                          # 固定机位：用一个小三角架符号交代
        _text(d, (x + w - 12, y + 10), "机位固定", 15, MUTED, anchor="ra")


# ---------------------------------------------------------------- 侧视图（机位高度）

def _side_view(d: ImageDraw.ImageDraw, shot: dict, box: tuple[int, int, int, int]) -> None:
    """机位高度示意的侧视图：地面线 + 侧面人 + 手机 + 视线 + 角度。
    「手机放多高、往上还是往下拍、离多远」三件事在这一格里全部画死。"""
    x0, y0, x1, y1 = box
    ground = y1 - 46
    d.line([(x0 + 16, ground), (x1 - 16, ground)], fill=MUTED, width=3)
    detail = str(shot.get("size_key")) == "detail"

    # 人：侧身站姿（头 + 身体 + 腿），站在地面线右侧。特写镜头不拍人，就不画人。
    px = x0 + (x1 - x0) * 0.70
    head_r = 16
    head_cy = ground - 150
    if not detail:
        d.ellipse([px - head_r, head_cy - head_r, px + head_r, head_cy + head_r],
                  fill=FILL, outline=INK, width=2)
        d.line([(px, head_cy + head_r), (px, ground - 56)], fill=INK, width=5)
        d.line([(px, ground - 56), (px - 16, ground)], fill=INK, width=4)
        d.line([(px, ground - 56), (px + 18, ground)], fill=INK, width=4)
        d.line([(px, head_cy + head_r + 22), (px + 26, head_cy + head_r + 52)],
               fill=INK, width=4)                    # 手臂，表示身前有动作
        _text(d, (px, ground + 6), "被拍的人", 15, MUTED, anchor="ma")

    # 手机：高度按 cam_height，俯仰按 cam_angle_deg
    key = str(shot.get("cam_height") or "eye")
    heights = {"eye": 150, "chest": 106, "waist": 92, "low": 40, "high": 186}
    hgt = heights.get(key, 150)
    cam_x = x0 + (x1 - x0) * 0.22
    cam_y = ground - hgt

    if detail:
        # 特写不拍人：画一张桌面 + 桌上的实物，手机在实物正上方 45° 俯拍。
        # 「手机悬在哪、对着什么」只有画出来才说不含糊。
        desk_y = ground - 74
        d.rectangle([x0 + 40, desk_y, x1 - 40, desk_y + 10], fill=LINE)
        _text(d, (x1 - 44, desk_y + 14), "实物放桌面 / 手上", 15, MUTED, anchor="ra")
        obj_cx = x0 + (x1 - x0) * 0.64
        d.rounded_rectangle([obj_cx - 34, desk_y - 34, obj_cx + 34, desk_y - 2],
                            radius=6, fill=SCREEN, outline=INK, width=3)
        cam_y = desk_y - 108
        cam_x = obj_cx - 66
        d.rounded_rectangle([cam_x - 12, cam_y - 22, cam_x + 12, cam_y + 22],
                            radius=5, fill=SCREEN, outline=INK, width=3)
        d.rectangle([cam_x - 6, cam_y - 9, cam_x + 6, cam_y + 9], fill=INK)
        _dashed(d, (cam_x + 12, cam_y + 6), (obj_cx - 28, desk_y - 20), ACCENT, 2, 7)
        _text(d, (cam_x, cam_y - 40), f"手机·俯 {abs(float(shot.get('angle_deg') or 45)):.0f}°",
              16, ACCENT, anchor="ma")
        _text(d, (cam_x, cam_y + 34), f"离实物约 {shot.get('cam_dist_m') or 0.3}m", 15, MUTED,
              anchor="ma")
        return

    d.rounded_rectangle([cam_x - 12, cam_y - 22, cam_x + 12, cam_y + 22],
                        radius=5, fill=SCREEN, outline=INK, width=3)
    d.rectangle([cam_x - 6, cam_y - 9, cam_x + 6, cam_y + 9], fill=INK)   # 镜头朝右

    angle = float(shot.get("angle_deg") or 0.0)
    # 视线：从镜头中心连到被拍的人脸上。俯仰角不是画一条斜线（那样容易画歪），
    # 而是靠「手机的高度差」本身表达：高度差 + 距离就是真实的拍摄角度。
    sight_end = (px - head_r - 2, head_cy)
    _dashed(d, (cam_x + 12, cam_y), sight_end, ACCENT, 2, 8)

    # 角度标签 + 高度标签
    label = {0: "平视 0°", 5: "略俯 5°", 10: "略俯 10°", 15: "俯 15°",
             20: "俯 20°", 30: "俯 30°", 45: "俯 45°"}.get(int(round(abs(angle))), f"{angle:+.0f}°")
    if angle < 0:
        label = f"仰 {abs(angle):.0f}°"
    _text(d, (cam_x, cam_y - 40), f"手机·{label}", 16, ACCENT, anchor="ma")
    hname = {"eye": "与眼睛同高", "chest": "胸口高", "waist": "腰高",
             "low": "贴地/桌面高度", "high": "举过头顶"}.get(key, "与眼睛同高")
    _text(d, (cam_x, ground + 6), hname, 15, MUTED, anchor="ma")

    dist = shot.get("cam_dist_m")
    if dist:
        mid = ((cam_x + px) / 2, ground - 12)
        d.line([(cam_x + 14, ground - 24), (px - 12, ground - 24)], fill=LINE, width=1)
        _text(d, mid, f"约 {dist}m", 15, MUTED, anchor="md")


# ---------------------------------------------------------------- 景别示意

def _size_inset(d: ImageDraw.ImageDraw, shot: dict, box: tuple[int, int, int, int]) -> None:
    """景别示意：画一个全身参考人，再用红框标出「这个景别只收进来哪一段」。"""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    top = y0 + 12
    body_h = bh - 40
    cx = x0 + bw * 0.38
    scale = body_h / 250.0 * 0.96
    _person(d, cx, top, scale)

    size = str(shot.get("size_key") or "medium")
    crop = {"close": (0.00, 0.42), "medium": (0.00, 0.63), "wide": (0.0, 1.0),
            "detail": (0.40, 0.72)}.get(size, (0.0, 0.63))
    ry0 = top + body_h * crop[0]
    ry1 = top + body_h * crop[1]
    d.rectangle([x0 + 8, ry0, x1 - 8, ry1], outline=ACCENT, width=3)
    _text(d, (x0 + 8, ry0 - 19), "收进画面", 14, ACCENT)


# ---------------------------------------------------------------- 主绘制

def draw_shot(shot: dict, out_path: str | Path) -> Path:
    """画一个镜头的一张示意。shot 的字段见 ideate.shot_plan()。"""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # 标题条
    d.rectangle([0, 0, W, 76], fill=(255, 255, 255))
    d.rectangle([0, 0, 10, 76], fill=ACCENT)
    title = str(shot.get("label") or f"镜头 {shot.get('n', 1)}")
    _text(d, (26, 12), title, 25, INK)
    meta = " · ".join(str(x) for x in (
        shot.get("size"), f"{float(shot.get('dur_s') or 0):.1f} 秒",
        (f"拍 {shot.get('takes')} 条" if int(shot.get("takes") or 0) > 0 else "不用拍"),
    ) if x)
    _text(d, (26, 44), meta, 18, MUTED)

    # ① 取景框 9:16
    d.rectangle([FRAME_X - 3, FRAME_Y - 3, FRAME_X + FRAME_W + 3, FRAME_Y + FRAME_H + 3],
                outline=INK, width=3)
    d.rectangle([FRAME_X, FRAME_Y, FRAME_X + FRAME_W, FRAME_Y + FRAME_H], fill=(255, 255, 255))
    for k in (1, 2):                       # 三分线
        _dashed(d, (FRAME_X + FRAME_W * k / 3, FRAME_Y),
                (FRAME_X + FRAME_W * k / 3, FRAME_Y + FRAME_H), LINE, 1, 8)
        _dashed(d, (FRAME_X, FRAME_Y + FRAME_H * k / 3),
                (FRAME_X + FRAME_W, FRAME_Y + FRAME_H * k / 3), LINE, 1, 8)
    # 安全区（字幕/贴纸不会压到的地方）：四条虚线边，不是一条斜线
    inset = 22
    sx0, sy0 = FRAME_X + inset, FRAME_Y + inset
    sx1, sy1 = FRAME_X + FRAME_W - inset, FRAME_Y + FRAME_H - inset
    for a, b in (((sx0, sy0), (sx1, sy0)), ((sx1, sy0), (sx1, sy1)),
                 ((sx1, sy1), (sx0, sy1)), ((sx0, sy1), (sx0, sy0))):
        _dashed(d, a, b, (198, 190, 180), 1, 6)

    size = str(shot.get("size_key") or "medium")
    if size == "screen" or shot.get("skip"):
        _screen_only(d, FRAME_X, FRAME_Y, FRAME_W, FRAME_H)
    elif size == "detail":
        _hands(d, FRAME_X + FRAME_W * 0.16, FRAME_Y + FRAME_H * 0.34,
               FRAME_W * 0.68, FRAME_H * 0.40)
    elif size == "wide":
        _scene(d, FRAME_X, FRAME_Y, FRAME_W, FRAME_H)
    else:
        # 近景/中近景：人顶到画面下沿（被切掉），头顶留的空白就是录下来的构图
        sc = 1.45 if size == "close" else 0.95
        _person(d, FRAME_X + FRAME_W * 0.5, FRAME_Y + FRAME_H * (0.10 if size == "close" else 0.08),
                sc, bottom=FRAME_Y + FRAME_H)
    _motion(d, str(shot.get("motion") or "none"), FRAME_X, FRAME_Y, FRAME_W, FRAME_H)

    _text(d, (FRAME_X, FRAME_Y + FRAME_H + 10), "9:16 竖屏取景框（虚线＝三分线/安全区）",
          15, MUTED)

    # ② 右侧：景别示意 + 主体位置
    _text(d, (392, 96), "景别示意", 18, INK)
    _size_inset(d, shot, (392, 124, 600, 404))
    _text(d, (392, 412), f"{shot.get('size') or ''}", 17, ACCENT)
    note = str(shot.get("frame_note") or "")
    y = 440
    for line in _wrap(note, 14)[:4]:
        _text(d, (392, y), line, 16, MUTED)
        y += 24

    # ③ 侧视图：机位高度与俯仰
    d.line([(28, 676), (W - 28, 676)], fill=LINE, width=1)
    _text(d, (28, 690), "机位（侧视）：手机放多高、往哪拍、离多远", 18, INK)
    _side_view(d, shot, (28, 720, W - 28, 1020))

    # ④ 脚注
    _text(d, (28, 1052), "箭头＝镜头运动方向（没有箭头＝机位固定）；全程竖屏，别中途横过来",
          15, MUTED)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def _wrap(text: str, width: int) -> list[str]:
    """按「字数」硬折行：示意图里的说明文字故意不排字，够读就行。"""
    text = str(text or "")
    if not text:
        return []
    out, cur = [], ""
    for ch in text:
        if ch == "\n":
            out.append(cur)
            cur = ""
            continue
        cur += ch
        if _text_w(cur, 16) > width * 16:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def render_all(proj: Path, shots: list[dict]) -> dict[int, str]:
    """给一组镜头出图，写到 `<项目>/shoot/diagram-N.png`，返回 {镜号: 相对路径}。

    返回的是**相对项目根**的路径（写进 shoot-guide.md 的引用就长这样），
    用正斜杠，Markdown 在任何平台都能点开。
    """
    out_dir = Path(proj) / "shoot"
    out_dir.mkdir(parents=True, exist_ok=True)
    rel: dict[int, str] = {}
    for s in shots:
        n = int(s.get("n") or 0)
        path = draw_shot(s, out_dir / f"diagram-{n}.png")
        rel[n] = path.relative_to(Path(proj)).as_posix()
    return rel


def main() -> int:
    ap = argparse.ArgumentParser(description="单个镜头的拍摄简笔示意")
    ap.add_argument("--out", required=True, help="输出 png 路径")
    ap.add_argument("--label", default="镜头 1", help="标题（如「镜头 1 · 黄金3秒钩子」）")
    ap.add_argument("--size", default="medium",
                    choices=["close", "medium", "wide", "detail", "screen"], help="景别")
    ap.add_argument("--height", default="eye", choices=["eye", "chest", "waist", "low", "high"],
                    help="机位高度")
    ap.add_argument("--angle", type=float, default=0.0, help="俯仰角（负=仰拍）")
    ap.add_argument("--dist", type=float, default=None, help="机位到主体距离（米）")
    ap.add_argument("--motion", default="none",
                    choices=["none", "push_in", "pull_out", "pan_left", "pan_right", "tilt_down"],
                    help="镜头运动方向")
    ap.add_argument("--dur", type=float, default=3.0, help="镜头时长（秒）")
    ap.add_argument("--takes", type=int, default=2, help="备份条数")
    ap.add_argument("--note", default="", help="主体位置说明（两行内）")
    a = ap.parse_args()

    sizes = {"close": "近景", "medium": "中近景", "wide": "全景",
             "detail": "手部特写", "screen": "免拍（代码画面）"}
    shot = {"n": 1, "label": a.label, "size_key": a.size, "size": sizes[a.size],
            "dur_s": a.dur, "takes": a.takes, "cam_height": a.height,
            "angle_deg": a.angle, "cam_dist_m": a.dist, "motion": a.motion,
            "frame_note": a.note, "skip": a.size == "screen"}
    p = draw_shot(shot, a.out)
    print(f"→ {p}（{W}×{H}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
