#!/usr/bin/env python3
"""场景：工序延误（queue_block）——前一道没交面，后一道只能等。

为什么画这个：方案 P1 案例 A 要替的那句话是「不是木工不愿意来，是前面的泥工
一直没交面。」这句话**没有可拍的外形**——延误是时间上的事，机位拍不到「等」。
能借的实物只有一件：三个工位排成一条线，一件货卡在中间，后面的灯全灭。
所以按 `references/scene-art-standard.md` §1「形状即语义」，把关系画成物理：
**不是三个人懒，是一条线上有一个断点**。

三拍（全部纯 t 驱动，见 `scripts/test_scene_determinism.py`）：

  一 · 亮（0 → 0.16dur）  上方黄铜横梁上吊的三盏工作灯从左到右依次亮起，
     灯下的光池一个个打到台面上；泥工台上有交出来的「面」，木工/油漆台的槽是空的。
     镜头从 1.10 起，一路缓慢拉远。
  二 · 堵（0.16 → 0.50dur）一件黄铜货（两箱叠一起）沿过道右滑，减速、停在
     第一道和第二道之间**过不去**；停在那一瞬，第二、三盏灯先后灭掉，两个
     台面跟着变暗，两个人立在原地不动（泥工还在干活，手臂一直在动）。
  三 · 长（0.50 → dur）    卡住的地方从地面上冒出一圈**真的在长大**的等待环
     （暗火土色，不是霓虹），环一直长到收尾；镜头最后再拉远一段，把
     「一整条线都在等第一个」收进画。

口播关系：第一拍对应「三个工序排着」，第二拍对应「泥工没交面」那一下，
第三拍对应「木工只能等」——环越长，等的越久，不用任何数字和箭头。
画面上没有一个字（只有角落一个 `示意`），也不许出现等宽色块 / 连线箭头 /
科技蓝：全部实体都是带顶面 / 正面 / 侧面的斜俯视小块，脚下有接地投影（§2 §3）。

尺寸：所有几何写在 1080×1920 的设计坐标系里，最后按 `min(W/1080, H/1920)`
等比缩放到画布并居中——任何画布尺寸都不溢出。

EVENTS 的 t 是「8 秒版」的秒数（= 下面的拍点比例 × 8）；换片长请按同一比例换算。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCENE_ID = "queue_block"

STORY_BEATS = [
    {"beat": "setup", "marks": 0.05,
     "what": "三盏吊灯从左到右依次亮起，三个工位排成一条线，泥工台上有料"},
    {"beat": "change", "marks": 0.42,
     "what": "货滑到第一、二道之间卡住；第二、三盏灯随即灭掉，两个工位变暗，人站着不动"},
    {"beat": "result", "marks": 0.50,
     "what": "卡住处冒出一圈随时间增大的等待环，镜头最后轻微拉远"},
]

EVENTS = [
    {"id": "piece-depart", "t": 1.28, "mark": 0.16, "name": "whoosh-medium-soft",
     "intent": "物件起步，沿过道往下一道走"},
    {"id": "jam", "t": 3.36, "mark": 0.42, "name": "impact-light-firm",
     "intent": "堆在第一、二道之间那一下（同时后两盏灯灭）"},
    {"id": "wait-ring", "t": 4.00, "mark": 0.50, "name": "appear-materialise",
     "intent": "等待环出现，开始长大"},
]

# 拍点（占片长的成数）：亮 / 走 / 堵 / 环 / 收尾拉远
_DEFAULT_MARKS = (0.05, 0.16, 0.42, 0.50, 0.78)

# ---------------------------------------------------------------- 调色
# 只取包里的暖陶土 / 黄铜 / 米白这一族（§5）。禁科技蓝、霓虹、紫粉。
BG_TOP = (14, 12, 11)
BG_BOT = (32, 26, 21)
WALL_TOP = (24, 20, 17)
WALL_BOT = (40, 32, 26)
WALL_SEAM = (58, 47, 38)
FLOOR = (52, 40, 32)
FLOOR_LINE = (72, 56, 45)

RAIL_TOP = (206, 164, 86)      # 黄铜：顶面受光
RAIL_FRONT = (170, 126, 60)
RAIL_SIDE = (128, 90, 40)

BENCH_TOP = (156, 100, 64)     # 暖陶土工作台（G 压在 100，避开 measure_geom 的金色族）
BENCH_FRONT = (120, 74, 46)
BENCH_SIDE = (88, 52, 32)
TROUGH = (42, 30, 22)          # 台面上那道凹槽：货该坐进去的空位

PANEL_TOP = (228, 218, 196)    # 泥工交出来的「面」：米白板材
PANEL_FRONT = (196, 184, 158)
PANEL_SIDE = (158, 146, 122)

CRATE_TOP = (226, 182, 96)     # 黄铜货箱
CRATE_FRONT = (184, 134, 58)
CRATE_SIDE = (140, 98, 42)

SKIN_TOP = (196, 156, 124)     # 人脸色：暖陶土（B 抬到 124，不进 measure_geom 的金色族）
BODY_TOP = (120, 96, 74)
BODY_FRONT = (96, 74, 56)
BODY_SIDE = (70, 52, 40)
LEG = (64, 46, 34)

LAMP_ON = (255, 226, 162)      # 灯泡 / 光池
LAMP_SHADE = (82, 60, 44)
LAMP_SHADE_OFF = (40, 31, 25)
MOTE = (255, 242, 214)
EMBER = (206, 96, 52)          # 等待环：暗火土，不是霓虹（G 压到 96 = 不进 measure_geom 的金色族）
CREAM = (245, 238, 222)

FONT_DIR = Path.home() / "Library" / "Fonts"


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("NotoSansSC-SemiBold.ttf", "NotoSansSC-Bold.ttf", "NotoSansSC-Black.ttf"):
        p = FONT_DIR / name
        if p.exists():
            return ImageFont.truetype(str(p), max(8, int(size)))
    return ImageFont.load_default()


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _ease_out(x: float) -> float:
    """减速停住：power2.out。物件进站是「滑到停」，不是匀速撞停（§6）。"""
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 2


def _mix(a, b, k: float):
    k = min(1.0, max(0.0, k))
    return tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def _dim(c, k: float):
    """整体压暗（灯灭之后台面要变暗，但三面关系不能变）。"""
    k = min(1.0, max(0.0, k))
    return tuple(int(v * k) for v in c)


def _marks(default: tuple[float, ...]) -> tuple[float, ...]:
    """`gen_scene.py --marks` 接管拍点。

    gen_scene 是以 `__main__` 跑起来的，`MARKS` 落在 `__main__` 里；先找
    `__main__.marks`，再退回 import 的那份——否则 `--marks` 会被静默忽略。
    """
    import sys
    fn = getattr(sys.modules.get("__main__"), "marks", None)
    if callable(fn):
        try:
            got = tuple(fn(default))
            if got:
                return got
        except Exception:                                      # noqa: BLE001
            pass
    try:
        from gen_scene import marks as _gmarks                 # noqa: PLC0415
        got = tuple(_gmarks(default))
        if got:
            return got
    except Exception:                                          # noqa: BLE001
        pass
    return tuple(default)


# ---------------------------------------------------------------- 画布 / 相机
class _Cam:
    """设计坐标（1080×1920）→ 屏幕坐标，含一条连续的运镜曲线。

    等比只用一个缩放因子（`min(W/1080, H/1920)`）而不是 x/y 分开缩：分开缩会把
    斜俯视的进深拉变形，三面律立刻破掉。相机只做缩放（不旋转、不切），
    所以「同一件东西一直在那儿」这件事不会被运镜破坏。
    """

    def __init__(self, W: int, H: int) -> None:
        self.su = min(W / 1080.0, H / 1920.0)
        self.ox = (W - 1080.0 * self.su) / 2.0
        self.oy = (H - 1920.0 * self.su) / 2.0
        self.z = 1.0
        self.px, self.py = 400.0, 1050.0      # 支点压在「卡住的那个缺口」上

    def p(self, x: float, y: float) -> tuple[float, float]:
        z = self.z
        return (self.ox + (self.px + (x - self.px) * z) * self.su,
                self.oy + (self.py + (y - self.py) * z) * self.su)

    def u(self, v: float) -> float:
        return v * self.su * self.z

    def wd(self, v: float) -> int:
        return max(1, int(round(v * self.su * self.z)))


def _poly(d: ImageDraw.ImageDraw, cam: _Cam, pts, fill) -> None:
    d.polygon([cam.p(x, y) for x, y in pts], fill=fill)


def _line(d: ImageDraw.ImageDraw, cam: _Cam, pts, fill, width: float) -> None:
    d.line([cam.p(x, y) for x, y in pts], fill=fill, width=cam.wd(width), joint="curve")


def _box(d: ImageDraw.ImageDraw, cam: _Cam, x: float, y: float, w: float, h: float,
         dx: float, dy: float, top, front, side, hi=None, hi_a: int = 96,
         tex: int = 0, seed: int = 1) -> None:
    """一块实体的三面：正面（矩形）+ 右侧面（进深）+ 顶面（平行四边形）+ 上棱高光 + 颗粒。

    (x, y) 是**正面左上角**；顶面往右上走 (dx, dy)。进深别超过厚度的一半（§2），
    三面明度逐级下降（顶最亮、正中间、侧最暗），光一律左上。
    底面压一道环境遮蔽暗边（实体的「坐」感全靠它）。
    """
    fA, fB = (x, y), (x + w, y)
    fC, fD = (x + w, y + h), (x, y + h)
    _poly(d, cam, [fA, fB, fC, fD], (*front, 255))
    sA, sB = (x + w, y), (x + w + dx, y - dy)
    sC, sD = (x + w + dx, y - dy + h), (x + w, y + h)
    _poly(d, cam, [sA, sB, sC, sD], (*side, 255))
    tA, tB = (x, y), (x + w, y)
    tC, tD = (x + w + dx, y - dy), (x + dx, y - dy)
    _poly(d, cam, [tA, tB, tC, tD], (*top, 255))
    if tex > 0:
        n = max(6, int(tex * (w * h) / 26.0))
        _grain_quad(d, cam, fA, fB, fC, fD, n, (0, 0, 0), 26, seed)
        _grain_quad(d, cam, tA, tB, tC, tD, max(6, n // 2), (255, 255, 255), 20, seed + 3)
        _grain_quad(d, cam, sA, sB, sC, sD, max(4, n // 3), (0, 0, 0), 34, seed + 7)
    # 环境遮蔽：越靠底越暗（一条软的黑边）
    for j, aa in enumerate((54, 34, 20)):
        yy = y + h - (j + 1) * max(2.0, h * 0.055)
        _poly(d, cam, [(x, yy), (x + w, yy), (x + w, yy + max(2.0, h * 0.055)),
                       (x, yy + max(2.0, h * 0.055))], (0, 0, 0, aa))
    if hi is not None and hi_a > 0:
        _line(d, cam, [(x, y), (x + w, y)], (*hi, hi_a), 2.2)
        _line(d, cam, [(x + w, y), (x + w + dx, y - dy)], (*hi, int(hi_a * 0.55)), 1.6)


def _ellipse_pts(cx: float, cy: float, rx: float, ry: float, n: int = 64,
                 phase: float = 0.0):
    tau = 2.0 * math.pi
    return [(cx + rx * math.cos(phase + tau * i / n),
             cy + ry * math.sin(phase + tau * i / n)) for i in range(n)]


def _shadow(d: ImageDraw.ImageDraw, cam: _Cam, cx: float, cy: float,
            rx: float, ry: float, a: int = 210, n: int = 8) -> None:
    """接地投影：一圈套一圈的半透明椭圆叠出软边（§3 没有投影一律读成图标）。

    内圈叠到接近实黑，外圈几乎看不见——这是「软」的唯一可靠画法（PIL 没有
    带模糊的填充，硬边椭圆会被读成贴了一块黑板）。
    """
    for i in range(n, 0, -1):
        k = i / n
        _poly(d, cam, _ellipse_pts(cx, cy, rx * k, ry * k, 44),
              (0, 0, 0, max(4, int(a / n * 1.9))))


def _contact(d: ImageDraw.ImageDraw, cam: _Cam, x: float, y: float, w: float,
             a: int = 120) -> None:
    """台面上的东西脚下那一层薄阴影（§3：盘里的东西也要有）。"""
    _poly(d, cam, _ellipse_pts(x + w / 2.0, y + 2, w * 0.56, 7, 32), (0, 0, 0, a))


def _lerp2(a, b, k: float):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)


def _bilin(A, B, C, D, u: float, v: float):
    """四角双线性插值（A 前左 / B 前右 / C 后右 / D 后左）。写纹理要用它取面内的点。"""
    w00, w10 = (1 - u) * (1 - v), u * (1 - v)
    w11, w01 = u * v, (1 - u) * v
    return (A[0] * w00 + B[0] * w10 + C[0] * w11 + D[0] * w01,
            A[1] * w00 + B[1] * w10 + C[1] * w11 + D[1] * w01)


def _grain_quad(d: ImageDraw.ImageDraw, cam: _Cam, A, B, C, D, n: int,
                color=(0, 0, 0), alpha: int = 30, seed: int = 1) -> None:
    """在一块面里撒颗粒：没有颗粒的面一定读成矢量色块（§2 材质的唯一低成本做法）。

    确定性：位置全由整数 hash 推出来，同一帧永远同一颗，重渲能对上。
    """
    for i in range(n):
        u = ((i * 7919 + seed * 104729) % 9973) / 9973.0
        v = ((i * 6151 + seed * 7919) % 9967) / 9967.0
        x, y = _bilin(A, B, C, D, u, v)
        r = 1.2 + (i % 3) * 0.8
        _poly(d, cam, _ellipse_pts(x, y, r, r * 0.62, 8), (*color, alpha))


# ---------------------------------------------------------------- 版面（设计坐标）
BENCH_X = (118.0, 412.0, 702.0)
BENCH_W = 160.0
BENCH_BASE = 1450.0        # 台子落地那条线（也是过道地面）
BENCH_H = 130.0            # 正面高度（人高 260 的一半：台面到腰）
BENCH_DX, BENCH_DY = 40.0, 28.0
BENCH_TOP_Y = BENCH_BASE - BENCH_H      # 正面顶棱 1320
RAIL_Y = 452.0
RAIL_H = 26.0
RAIL_DX, RAIL_DY = 36.0, 26.0
SHADE_Y = 1040.0           # 灯罩顶
CAM_Z = (0.80, 1.33, 1.25)  # 起手 / 最紧 / 收尾的缩放（整段只有这一条运镜曲线）
CAM_TURN = 0.86            # 镜头由推转拉的时刻（成数）
CAM_EASE = 0.82            # 运镜进度里「线性」占多少：越大越匀（两头不站桩）
LAMP_SWING = 0.011         # 吊灯摆幅（弧度/单位绳长）：绳长 ~560，摆 ±6
# 缺口中心按**看得见的**那道缝算：台子顶面往右上多出 BENCH_DX，量缝的时候要把它算进去
GAP_CX = (BENCH_X[0] + BENCH_W + BENCH_DX + BENCH_X[1]) / 2.0
PIECE_W = 74.0
PIECE_BASE = 1470.0                     # 货走在台子之间那道过道上（比台脚落地线再靠前一点）
PIECE_START = BENCH_X[0] + 104.0
PIECE_STOP = GAP_CX - (PIECE_W + 20.0) / 2.0
WALL_Y = 1206.0            # 墙脚线（台子站在它前面的地面上）
WORKER_DY = 50.0           # 人站在台子**前面**一点（整个人都在画面里，脚下有投影）


def _backdrop(t: float, W: int, H: int, cam: _Cam) -> np.ndarray:
    bg = np.zeros((H, W, 3), dtype=np.float32)
    ramp = np.linspace(0, 1, H)[:, None]
    for c in range(3):
        bg[..., c] = BG_TOP[c] * (1 - ramp) + BG_BOT[c] * ramp
    cx, cy = cam.p(330.0, 1120.0)
    yy, xx = np.mgrid[0:H, 0:W]
    r2 = ((xx - cx) / W) ** 2 + ((yy - cy) / H) ** 2
    halo = np.exp(-r2 * 3.6) * (58 + 9 * math.sin(t * 1.9))      # 呼吸 ≤4%
    bg += halo[..., None] * np.array([1.0, 0.86, 0.44])[None, None, :]
    # 暗角：把注意力压回中间那三个工位，也给「房间」一点体积
    vig = 0.82 + 0.18 * np.exp(-(((xx - W * 0.46) / W) ** 2
                                + ((yy - H * 0.60) / H) ** 2) * 4.2)
    bg *= vig[..., None]
    return np.clip(bg, 0, 255).astype(np.uint8)


def _bench(d: ImageDraw.ImageDraw, cam: _Cam, bx: float, lit: float,
           idx: int, t: float) -> None:
    """一个工位：台子三面 + 台面凹槽 + 这一道自己的活。

    灯灭之后整个台子按同一个系数压暗（三面明度差保持不变），这样「这个工位
    熄了」读起来是光没了，不是换了一个色的方块。
    """
    k = 0.56 + 0.44 * lit
    # 亮着的工位往暖黄推一档：有灯的那个台子必须比熄了的亮出一截，
    # 只靠灯锥（一层透明三角）永远「没打在东西上」。
    litk = lit * 0.55
    top = _dim(_mix(BENCH_TOP, (196, 98, 50), litk), k)
    front = _dim(_mix(BENCH_FRONT, (160, 92, 54), litk), k)
    side = _dim(_mix(BENCH_SIDE, (112, 64, 38), litk), k)
    _box(d, cam, bx, BENCH_TOP_Y, BENCH_W, BENCH_H, BENCH_DX, BENCH_DY,
         top, front, side, hi=CREAM, hi_a=int(46 + 52 * lit),
         tex=1.0, seed=40 + idx * 13)
    # 木纹：正面两道横的木纹 + 侧面一道竖的（有纹理才不像纯色块）
    for f in (0.34, 0.68):
        yy = BENCH_TOP_Y + BENCH_H * f
        _line(d, cam, [(bx + 6, yy), (bx + BENCH_W - 6, yy)], (*_dim(FLOOR_LINE, k), 46), 1.8)
    _line(d, cam, [(bx + BENCH_W + BENCH_DX * 0.5, BENCH_TOP_Y + 8),
                   (bx + BENCH_W + BENCH_DX * 0.5, BENCH_BASE - 8)],
          (*_dim((0, 0, 0), 1.0), 40), 1.8)
    # 台脚落地那条暗缝：不画它台子像贴在墙上的纸
    _line(d, cam, [(bx, BENCH_BASE), (bx + BENCH_W + BENCH_DX, BENCH_BASE - BENCH_DY)],
          (18, 14, 11, 190), 3.0)

    # 台面凹槽：货该坐进去的空位（空着就是空着，不放虚线不要箭头）
    u0, u1, v0, v1 = 0.10, 0.90, 0.16, 0.84
    ax, ay = bx, BENCH_TOP_Y
    px0, _ = _lerp2((ax, ay), (ax + BENCH_W, ay), u0)
    px1, _ = _lerp2((ax, ay), (ax + BENCH_W, ay), u1)
    _poly(d, cam, [(px0 + BENCH_DX * v0, ay - BENCH_DY * v0),
                   (px1 + BENCH_DX * v0, ay - BENCH_DY * v0),
                   (px1 + BENCH_DX * v1, ay - BENCH_DY * v1),
                   (px0 + BENCH_DX * v1, ay - BENCH_DY * v1)],
          (*_dim(TROUGH, 0.75 + 0.25 * lit), 255))
    _line(d, cam, [(px0 + BENCH_DX * v0, ay - BENCH_DY * v0 + 2),
                   (px1 + BENCH_DX * v0, ay - BENCH_DY * v0 + 2)],
          (*CREAM, int(26 + 22 * lit)), 1.6)

    sx = bx + BENCH_DX * 0.45
    sy = BENCH_TOP_Y - BENCH_DY * 0.45
    if idx == 0:
        # 泥工台上：三块刚交出来的「面」叠着（有料 = 这一道在干）
        _contact(d, cam, sx + 16, sy - 2, 104, a=int(70 + 60 * lit))
        for j in range(3):
            _box(d, cam, sx + 18, sy - 8 - j * 8, 94, 9, 16, 10,
                 _dim(PANEL_TOP, k), _dim(PANEL_FRONT, k), _dim(PANEL_SIDE, k),
                 hi=CREAM, hi_a=int(64 * lit))
        _contact(d, cam, sx + 110, sy - 2, 46, a=int(60 + 60 * lit))
        _box(d, cam, sx + 112, sy - 26, 36, 26, 12, 9,
             _dim((144, 96, 64), k), _dim((110, 76, 46), k), _dim((82, 56, 34), k),
             hi=CREAM, hi_a=int(50 * lit))
    elif idx == 1:
        # 木工台上：一把没人拿的槌子躺在空槽边（人闲着，工具也闲着）
        _contact(d, cam, sx + 50, sy - 4, 92, a=int(60 + 55 * lit))
        _box(d, cam, sx + 96, sy - 30, 34, 20, 11, 8,
             _dim((146, 104, 64), k), _dim((108, 74, 44), k), _dim((80, 54, 32), k),
             hi=CREAM, hi_a=int(46 * lit))
        _box(d, cam, sx + 52, sy - 22, 46, 9, 9, 6,
             _dim((158, 116, 74), k), _dim((118, 84, 52), k), _dim((88, 60, 36), k))
    else:
        # 油漆台上：一桶没开封的漆（盖着）
        _contact(d, cam, sx + 88, sy - 4, 62, a=int(60 + 55 * lit))
        _box(d, cam, sx + 92, sy - 38, 56, 32, 14, 10,
             _dim((168, 160, 140), k), _dim((124, 118, 104), k), _dim((94, 88, 78), k),
             hi=CREAM, hi_a=int(44 * lit))
        _line(d, cam, [(sx + 92, sy - 12), (sx + 148, sy - 12)],
              (*CREAM, int(34 + 22 * lit)), 2.0)


def _worker(d: ImageDraw.ImageDraw, cam: _Cam, cx: float, base: float,
            working: bool, lit: float, t: float) -> None:
    """一个站在台子边的人：三面体的身/头 + 接地投影。

    working=True 的人手臂一直在动（上游还在干）；等的人**一站到底不动**——
    这两者的差别就是这句口播要演的东西，也是全片不至于冻住的动源。
    """
    k = 0.66 + 0.34 * lit
    _shadow(d, cam, cx + 8, base + 3, 52, 13, a=235, n=7)
    for lx in (-16.0, 4.0):                       # 腿
        _box(d, cam, cx + lx, base - 96, 16, 96, 7, 5,
             _dim((92, 70, 54), k), _dim(LEG, k), _dim((46, 33, 24), k))
    _box(d, cam, cx - 30, base - 196, 66, 104, 17, 12,      # 身
         _dim(BODY_TOP, k), _dim(BODY_FRONT, k), _dim(BODY_SIDE, k), hi=CREAM, hi_a=48)
    _box(d, cam, cx - 17, base - 244, 36, 46, 12, 9,        # 头
         _dim(SKIN_TOP, k), _dim((170, 136, 118), k), _dim((126, 98, 84), k))
    _box(d, cam, cx - 23, base - 250, 48, 10, 14, 10,       # 安全帽沿（米白）
         _dim(CREAM, 0.92), _dim((214, 204, 182), k), _dim((172, 162, 142), k))
    if working:
        sw = math.sin(t * 4.3)                              # 手臂在抡（sin 驱动，可复现）
        ax2 = cx - 30 + 13 * sw
        ay2 = base - 190 + 22 * sw
        _box(d, cam, ax2, ay2, 17, 78, 10, 7,
             _dim((132, 104, 78), k), _dim((104, 78, 56), k), _dim((76, 56, 40), k))
        _box(d, cam, ax2 - 7, ay2 + 74, 32, 9, 10, 6,       # 手里的抹刀
             _dim((200, 190, 170), k), _dim((160, 150, 132), k), _dim((124, 116, 100), k),
             hi=CREAM, hi_a=int(70 * lit))
    else:
        for ax2 in (cx - 43.0, cx + 32.0):                  # 两条手臂都垂着，一动不动
            _box(d, cam, ax2, base - 188, 15, 96, 8, 5,
                 _dim((124, 98, 74), k), _dim((96, 72, 52), k), _dim((70, 52, 38), k))


def scene(t: float, W: int, H: int, dur: float) -> Image.Image:
    dur = max(0.4, float(dur))
    tt = min(max(t, 0.0), dur - 1e-4)
    u = tt / dur

    m = _marks(_DEFAULT_MARKS)
    t_go, t_jam, t_ring = dur * m[1], dur * m[2], dur * m[3]
    lamp_on0 = dur * m[0]

    cam = _Cam(W, H)
    # 一条连续运镜曲线（不回正、不切）：一路慢慢推近（跟着卡车往前凑），
    # 最后一段往回一收（「轻微拉远」），把整条线收进画。
    # 进度 = CAM_EASE·线性 + 其余缓动：两头慢、中间匀、导数处处不为 0——
    # 纯缓动会在中段掉到「一帧几乎没变」（实测 t=4–5s 帧差只有 0.4），纯线性又像机器推轨。
    z0, z1, z2 = CAM_Z
    T = CAM_TURN
    k1 = (u / T) if u <= T else 1.0
    e = CAM_EASE
    if u <= T:
        cam.z = z0 + (z1 - z0) * (e * k1 + (1 - e) * _ease(k1))
    else:
        k2 = (u - T) / (1 - T)
        cam.z = z1 + (z2 - z1) * (e * k2 + (1 - e) * _ease(k2))

    im = Image.fromarray(_backdrop(tt, W, H, cam), "RGB")
    d = ImageDraw.Draw(im, "RGBA")

    # ---------------------------------------------------------------- 后墙
    _poly(d, cam, [(-300, -400), (1300, -400), (1300, WALL_Y), (-300, WALL_Y)],
          (*WALL_BOT, 255))
    _poly(d, cam, [(-300, -400), (1300, -400), (1300, WALL_Y * 0.52), (-300, WALL_Y * 0.52)],
          (*WALL_TOP, 255))
    for i, wy in enumerate((470.0, 760.0, 1010.0)):        # 墙板缝
        _line(d, cam, [(-300, wy), (1300, wy)], (*WALL_SEAM, 54), 2.0)
    _grain_quad(d, cam, (-300, -400), (1300, -400), (1300, WALL_Y), (-300, WALL_Y),
                460, (0, 0, 0), 30, seed=11)
    _grain_quad(d, cam, (-300, -400), (1300, -400), (1300, WALL_Y), (-300, WALL_Y),
                260, (255, 236, 200), 16, seed=17)
    # 横梁投在墙上的那道影：把梁和墙分开（不然梁像贴在墙上的一条色块）
    for j, aa in enumerate((32, 22, 13)):
        y0 = RAIL_Y + RAIL_H + 2 + j * 17
        _poly(d, cam, [(-300, y0), (1300, y0), (1300, y0 + 19), (-300, y0 + 19)],
              (0, 0, 0, aa))

    # 灯打到墙上的那两块光（只跟亮的工位有关，让「这盏灯亮着」有环境证据）
    lit_pre = []
    for i in range(3):
        on = _ease((tt - lamp_on0 - i * dur * 0.028) / max(1e-6, dur * 0.035))
        off = _ease((tt - t_jam - (i - 1) * dur * 0.028) / max(1e-6, dur * 0.06)) if i else 0.0
        lit_pre.append(on * (1.0 - off))
    for i, bx in enumerate(BENCH_X):
        if lit_pre[i] <= 0.01:
            continue
        cx = bx + BENCH_W / 2.0 + 14.0
        _poly(d, cam, _ellipse_pts(cx + 30, 980, 170, 165, 40),
              (*LAMP_ON, int(13 * lit_pre[i])))

    # ---------------------------------------------------------------- 地面
    _poly(d, cam, [(-300, WALL_Y), (1300, WALL_Y), (1300, 2200), (-300, 2200)],
          (*FLOOR, 255))
    _poly(d, cam, [(-300, WALL_Y), (1300, WALL_Y), (1300, WALL_Y + 26), (-300, WALL_Y + 26)],
          (18, 14, 11, 150))
    for i in range(1, 8):                      # 地板横木：间距越远越大（有进深）
        yy = WALL_Y + 500.0 * (i / 8.0) ** 1.5
        _line(d, cam, [(-300, yy), (1300, yy)], (*FLOOR_LINE, 52), 2.0)
    _grain_quad(d, cam, (-300, WALL_Y), (1300, WALL_Y), (1300, 2200), (-300, 2200),
                540, (0, 0, 0), 34, seed=23)
    _grain_quad(d, cam, (-300, WALL_Y), (1300, WALL_Y), (1300, 2200), (-300, 2200),
                320, (255, 232, 196), 18, seed=29)

    # ---------------------------------------------------------------- 等待环（地面上，货底下那圈）
    ring_k = min(1.0, max(0.0, (tt - t_ring) / max(1e-6, dur * 0.44)))
    strain = 0.0
    jammed = tt >= t_jam
    if jammed:
        ph = tt - t_jam
        strain = 2.6 * math.sin(ph * 3.4) * math.exp(-0.10 * ph)
    piece_x = PIECE_START + (PIECE_STOP - PIECE_START) * \
        _ease_out((tt - t_go) / max(1e-6, t_jam - t_go)) + strain
    ring_cx = PIECE_STOP + (PIECE_W + 20.0) / 2.0 + strain   # 环要套在货的**外接框中心**上

    if ring_k > 0:
        e = ring_k ** 0.78                                  # 先慢后匀：一眼看出「它在长」
        ring_r = 56.0 + 126.0 * e
        ry = ring_r * 0.24
        cy = PIECE_BASE + 6.0
        fade = min(1.0, ring_k / 0.10)                      # 起环那一下淡入，别凭空「啪」一下
        _poly(d, cam, _ellipse_pts(ring_cx, cy, ring_r * 0.9, ry * 0.9, 64),
              (*EMBER, int((12 + 20 * e) * fade)))
        _line(d, cam, _ellipse_pts(ring_cx, cy, ring_r, ry, 76) +
              [_ellipse_pts(ring_cx, cy, ring_r, ry, 76)[0]],
              (*EMBER, int((126 + 92 * e) * fade)), 5.2 + 2.4 * e)
        _line(d, cam, _ellipse_pts(ring_cx, cy, ring_r * 1.17, ry * 1.17, 76) +
              [_ellipse_pts(ring_cx, cy, ring_r * 1.17, ry * 1.17, 76)[0]],
              (*EMBER, int((34 + 30 * e) * fade)), 1.8)

    # ---------------------------------------------------------------- 落地的影子先铺（不许压在物体上）
    for bx in BENCH_X:
        _shadow(d, cam, bx + BENCH_W / 2.0 + BENCH_DX * 0.55, BENCH_BASE + 10,
                BENCH_W * 0.60, 26, a=255, n=9)
    _shadow(d, cam, piece_x + PIECE_W / 2.0, PIECE_BASE + 6, PIECE_W * 0.82, 19,
            a=255, n=8)
    _shadow(d, cam, piece_x + PIECE_W / 2.0, PIECE_BASE + 3, PIECE_W * 0.52, 9,
            a=200, n=5)      # 贴地那一层更实：箱底和地面之间不许透光

    # ---------------------------------------------------------------- 横梁 + 吊灯
    rx0, rx1 = 182.0, 862.0
    for j, (bx_, col) in enumerate(((rx0 + 96, 0.0), (rx1 - 132, 0.0))):
        # 两个墙托架：梁不能自己浮在半空（§1 每块都要有物理归属）
        _poly(d, cam, [(bx_, RAIL_Y + 6), (bx_ + 15, RAIL_Y + 6),
                       (bx_ + 62, RAIL_Y - 42), (bx_ + 47, RAIL_Y - 42)],
              (*_dim(RAIL_SIDE, 0.85), 235))
        _line(d, cam, [(bx_ + 2, RAIL_Y + 6), (bx_ + 49, RAIL_Y - 42)],
              (*CREAM, 60), 1.6)
    _box(d, cam, rx0, RAIL_Y, rx1 - rx0, RAIL_H, RAIL_DX, RAIL_DY,
         RAIL_TOP, RAIL_FRONT, RAIL_SIDE, hi=CREAM, hi_a=76, tex=0.7, seed=101)
    # 黄铜的镜面亮带：一道横在正面上的亮条（没这道就是一块木头）
    _line(d, cam, [(rx0 + 8, RAIL_Y + RAIL_H * 0.34), (rx1 - 8, RAIL_Y + RAIL_H * 0.34)],
          (255, 240, 200, 105), 2.2)
    for i, bx in enumerate(BENCH_X):
        cx = bx + BENCH_W / 2.0 + 14.0
        # 吊灯在微微摆（一条正弦，可复现）：绳端、灯罩、灯锥一起走，不许各自乱晃。
        # 这点摆幅是全片在中段唯一「整块变亮区域在移动」的动源，也是车间该有的动静。
        swing = LAMP_SWING * math.sin(tt * 1.7 + i * 0.9)
        sx_ = cx + swing * (SHADE_Y - RAIL_Y - RAIL_H)
        # 吊线：从横梁**正面下沿**出发（§3：挂在实体上，不挂在空气里）
        _line(d, cam, [(cx, RAIL_Y + RAIL_H), (sx_, SHADE_Y + 4)], (156, 140, 116, 205), 3.0)
        _line(d, cam, [(cx, RAIL_Y + RAIL_H), (sx_, SHADE_Y + 4)], (0, 0, 0, 90), 1.0)
        cx = sx_
        shade = _mix(LAMP_SHADE_OFF, LAMP_SHADE, lit_pre[i])
        # 灯罩：上窄下宽的锥筒 + 一道外沿（有厚度才像罩子）
        _poly(d, cam, [(cx - 17, SHADE_Y), (cx + 17, SHADE_Y),
                       (cx + 52, SHADE_Y + 58), (cx - 52, SHADE_Y + 58)], (*shade, 255))
        _poly(d, cam, [(cx + 17, SHADE_Y), (cx + 26, SHADE_Y - 8),
                       (cx + 61, SHADE_Y + 54), (cx + 52, SHADE_Y + 58)],
              (*_dim(shade, 0.70), 240))
        _line(d, cam, [(cx - 17, SHADE_Y), (cx - 52, SHADE_Y + 58)],
              (*_dim(shade, 1.45), 130), 1.8)
        _line(d, cam, [(cx - 52, SHADE_Y + 58), (cx + 52, SHADE_Y + 58)],
              (*_dim(shade, 1.6), 150), 2.0)
        bulb = _mix((30, 23, 19), LAMP_ON, lit_pre[i])
        _poly(d, cam, _ellipse_pts(cx + 34, SHADE_Y + 118, 96, 118, 40),
              (0, 0, 0, 24))                       # 罩子投在墙上的影（软、变形，不是剪影）
        _poly(d, cam, _ellipse_pts(cx, SHADE_Y + 57, 34, 9, 28), (*bulb, 255))
        if lit_pre[i] > 0.02:                    # 灯泡外的一圈晕
            _poly(d, cam, _ellipse_pts(cx, SHADE_Y + 57, 78, 30, 40),
                  (*LAMP_ON, int(20 * lit_pre[i])))

    # ---------------------------------------------------------------- 三个工位
    for i, bx in enumerate(BENCH_X):
        _bench(d, cam, bx, lit_pre[i], i, tt)

    # ---------------------------------------------------------------- 灯锥 + 台面光池（在台子之后、人之前）
    for i, bx in enumerate(BENCH_X):
        if lit_pre[i] <= 0.01:
            continue
        cx = bx + BENCH_W / 2.0 + 14.0
        # 灯锥：靠上窄、靠下宽，靠上的那一段更淡（光在空气里越远越散）
        for j, (aa, fa, fb) in enumerate(((10, 0.0, 0.34), (9, 0.34, 0.67), (8, 0.67, 1.0))):
            y0 = SHADE_Y + 58 + (BENCH_TOP_Y - 6 - SHADE_Y - 58) * fa
            y1 = SHADE_Y + 58 + (BENCH_TOP_Y - 6 - SHADE_Y - 58) * fb
            w0 = 50 + 62 * fa
            w1 = 50 + 62 * fb
            _poly(d, cam, [(cx - w0, y0), (cx + w0, y0), (cx + w1, y1), (cx - w1, y1)],
                  (*LAMP_ON, int(aa * lit_pre[i])))
        # 光落在台面上的那一窝：两层，内层更亮（否则「灯亮了」只是个透明三角）
        _poly(d, cam, _ellipse_pts(cx + 14.0, BENCH_TOP_Y - BENCH_DY * 0.5, 82, 14, 48),
              (*LAMP_ON, int(42 * lit_pre[i])))
        _poly(d, cam, _ellipse_pts(cx + 14.0, BENCH_TOP_Y - BENCH_DY * 0.5, 54, 9, 40),
              (*LAMP_ON, int(38 * lit_pre[i])))
        # 光溢出到台前的地面上（灯是吊着的，地面只能拾到一点漏下来的光）
        _poly(d, cam, _ellipse_pts(cx + 20.0, BENCH_BASE + 40, 128, 26, 44),
              (*LAMP_ON, int(16 * lit_pre[i])))

    # ---------------------------------------------------------------- 三个人（在台子前面：整个人读得出来）
    for i, bx in enumerate(BENCH_X):
        # 泥工站在自己台子的靠左一头（他正在那一头忙），另两个站在台子中间等
        frac = 0.30 if i == 0 else 0.60
        _worker(d, cam, bx + BENCH_W * frac, BENCH_BASE + WORKER_DY,
                i == 0, lit_pre[i], tt)

    # ---------------------------------------------------------------- 那件货（两箱叠着，走在最靠前那道过道上）
    bob = 1.6 * math.sin(tt * 3.1) if tt > t_go else 0.0
    _box(d, cam, piece_x, PIECE_BASE - 62 + bob, PIECE_W, 62, 20, 14,
         CRATE_TOP, CRATE_FRONT, CRATE_SIDE, hi=CREAM, hi_a=96, tex=1.3, seed=71)
    _box(d, cam, piece_x + 12, PIECE_BASE - 88 + bob, PIECE_W - 22, 26, 16, 11,
         _mix(CRATE_TOP, CREAM, 0.20), CRATE_FRONT, CRATE_SIDE, hi=CREAM, hi_a=86,
         tex=1.2, seed=83)
    # 箱底落地那道实黑：只有接地缝才能把「墩在地上」和「贴着地面」分开
    _poly(d, cam, [(piece_x - 2, PIECE_BASE + bob - 6), (piece_x + PIECE_W + 18, PIECE_BASE + bob - 19),
                   (piece_x + PIECE_W + 18, PIECE_BASE + bob - 11), (piece_x - 2, PIECE_BASE + bob + 3)],
          (0, 0, 0, 205))
    # 箱盖缝 + 一道米白封条（说明它是「要交给下一道的一件东西」）
    _line(d, cam, [(piece_x + 6, PIECE_BASE - 20 + bob), (piece_x + PIECE_W - 22, PIECE_BASE - 20 + bob)],
          (*CREAM, 84), 3.0)
    _line(d, cam, [(piece_x + 6, PIECE_BASE - 54 + bob), (piece_x + PIECE_W - 18, PIECE_BASE - 54 + bob)],
          (255, 244, 214, 96), 2.0)                # 黄铜箱的镜面高光带

    # ---------------------------------------------------------------- 浮尘（连续微动，不许循环闪）
    for i in range(26):
        fx = 120.0 + (i * 149.0) % 840.0 + 16.0 * math.sin(tt * 0.63 + i * 1.7)
        fy = 660.0 + ((i * 271.0) % 680.0) + 22.0 * math.sin(tt * 0.47 + i * 2.3)
        r = 2.2 + (i % 3) * 1.2
        _poly(d, cam, _ellipse_pts(fx, fy, r, r, 12), (*MOTE, 30 + (i % 4) * 10))

    # ---------------------------------------------------------------- 角落一个「示意」
    # 「示意」标在屏幕坐标里（不跟镜头走，任何画幅都待在角落），颜色先和底色混过：
    # PIL 在 RGB 图上写字不吃 fill 的 alpha，直接给 (245,238,222,84) 会画成一块死白。
    f = _font(max(10, W * 0.019))
    d.text((W * 0.055, H * 0.925), "示意", font=f,
           fill=_mix(_mix(FLOOR, BG_TOP, 0.45), CREAM, 0.34))

    # 最后一道：胶片颗粒。颗粒是**静态**的（固定种子 → 每帧同一张），所以它只加
    # 质感，不动画面：既不会把帧差刷假绿，也不会被误读成噪点。
    arr = np.asarray(im, dtype=np.float32)
    rng = np.random.default_rng(20261114)
    arr += rng.normal(0.0, 2.4, (H, W, 1)).astype(np.float32)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
