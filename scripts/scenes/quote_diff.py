#!/usr/bin/env python3
"""场景 quote_diff：两份报价差在哪儿（AI 视听方案 P1 · 案例 C）。

要演的那句话是「顾客不是只看价格，而是根本不知道两份报价差在哪里」。这句话没有
可拍的外形（不是动作、不是人、不是现场），落进 B14 允许补镜的第三档：**抽象口径 /
量的对比**。能不能画看修辞关系——这里要画的关系是「两张纸，同一行上一边有字、
一边没有」，落到实物上是两张报价单加一格凹下去的空白。

按 `references/scene-art-standard.md` 逐条对：

§1 形状即语义  关系画成物理：两张纸是**实物**，差的是纸面上一个**空缺**，不是红色警示。
§2 三面律      每张纸画顶面（纸面）+ 正面（下沿切面）+ 侧面（右沿切面），ΔL 都在 30 以上；
               光一律从左上打，全画面一致。
§3 接地律      纸下有一层模糊投影，下沿再压一道更紧的接触影；「落下」那一拍投影随距离
               由虚变实——不动的投影会让人读成浮着。
§4 可量判据    `frame_quads()` 把这一秒两张纸落在屏幕上的四个角吐出来，量测脚本据此算
               「两张纸的 x 位置差」和「空白格那一格有多少墨」，不靠形容词。
§6 运动        落桌 0.32s / power2.out、不弹；逐项展开每项一个自己的时刻（成数按 dur 算）；
               落定之后靠一条极宽的窗光横扫 + ≤2% 呼吸继续动，不许变成一张静止的图。
§7 声音        4 个事件，都落在动作起点；「空白被看见」那一下**不出声**，让口播自己站住。

禁：表格。两张纸是**两个分离的实物**（各自透视、各自投影、各自倾斜），纸上的横线是
印刷的行线，不是把两栏连起来的表格线。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCENE_ID = "quote_diff"

# ---------------------------------------------------------------- 先讲故事，再写代码
STORY_BEATS = {
    "setup": "两张报价单先后落桌：46,800 / 46,300。总价那一行字一样大、位置一样，"
             "远远看过去两张纸没有区别。",
    "change": "材料 · 施工范围 · 质保 · 验收 一项一项浮上纸面；第二张的「质保」那一格"
              "是纸面上的一个凹进去的空白窗口——不是划掉，是从来没写。",
    "result": "两张纸推到并排，四条行线对成同一行；一边有字、一边是空的，"
              "差的那一处自己显出来——不在总价上。",
}

# t 是 8 秒片长下的秒数（scene() 里的节拍按 dur 的成数算，8s 时正好落在这些点上）
EVENTS = [
    {"id": "quote-drop-a", "t": 0.44, "name": "foley-paper-touch",
     "intent": "第一张报价单落桌，纸角碰到桌面"},
    {"id": "quote-drop-b", "t": 0.72, "name": "foley-paper-slide",
     "intent": "第二张滑上桌，压在前一张边上"},
    {"id": "quote-rows", "t": 1.96, "name": "writing-pen-draw",
     "intent": "第一项（材料）被写到纸上——逐项展开的起点"},
    {"id": "quote-align", "t": 5.76, "name": "foley-card-slide",
     "intent": "两张纸推到并排就位"},
]
# 空白被看见那一下不出声：scene-art-standard §7「结论/留白处不出声」（口播要自己站住）。

# ---------------------------------------------------------------- 调色（跟 gen_scene 同族）
BG_TOP = (34, 27, 22)          # 桌面：远（边上没人照，但也不是黑）
BG_BOT = (56, 44, 34)          # 桌面：近
PAPER_TOP = (243, 236, 219)    # 纸面受光
PAPER_BOT = (227, 218, 197)    # 纸面背光
PAPER_EDGE_FRONT = (176, 166, 146)   # 下沿切面（正面，中调）
PAPER_EDGE_SIDE = (146, 136, 118)    # 右沿切面（侧面，最暗）
INK = (44, 36, 30)             # 墨
INK_MUTED = (126, 112, 96)     # 淡墨（小标签）
RULE = (150, 138, 118)         # 印上去的行线
BRASS = (166, 132, 66)         # 黄铜墨：只给「甲/乙」那个字，一格里最多两处强调

FONT_DIR = Path.home() / "Library" / "Fonts"
FONT_BLACK = FONT_DIR / "NotoSansSC-Black.ttf"
FONT_BOLD = FONT_DIR / "NotoSansSC-Bold.ttf"
FONT_SEMI = FONT_DIR / "NotoSansSC-SemiBold.ttf"

# 两张纸的四条对比项。第二张的第三项没有值——那就是要演出来的那个缺口。
ROWS_A = [("材料", "3 项"), ("施工范围", "含厨房"), ("质保", "五年"), ("验收", "两次")]
ROWS_B = [("材料", "3 项"), ("施工范围", "含厨房"), ("质保", None), ("验收", "两次")]
TOTAL_A, TOTAL_B = "46,800", "46,300"      # 位数、字号、位置全一样：读不出差别才是设计

# 纸面版式（成数，相对纸宽/纸高）。量测脚本读这里，不要另抄一份——
# 抄一份的结果是改了版式以后读数还在量旧位置，那不算量到了。
LAYOUT = {"mx": 0.088, "vw": 0.340, "y0": 0.415, "pitch": 0.115, "half": 0.040}

_GRID: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
_DAPPLE: dict[tuple[int, int], np.ndarray] = {}


# ---------------------------------------------------------------- 小工具
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    for p in (path, FONT_BLACK, FONT_BOLD, FONT_SEMI):
        if p and Path(p).exists():
            return ImageFont.truetype(str(Path(p)), size)
    return ImageFont.load_default()


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _ease_out(u: float) -> float:
    """power2.out：落下的东西是「快到慢」，不是弹跳（纪实片里弹跳像玩具）。"""
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 2


def _span(t: float, a: float, b: float) -> float:
    return _smooth((t - a) / max(1e-6, b - a))


def _grid(W: int, H: int) -> tuple[np.ndarray, np.ndarray]:
    hit = _GRID.get((W, H))
    if hit is None:
        yy, xx = np.mgrid[0:H, 0:W]
        hit = (xx.astype(np.float32), yy.astype(np.float32))
        _GRID[(W, H)] = hit
    return hit


def _marks(default: tuple[float, ...]) -> tuple[float, ...]:
    """节拍由调用方的 `--marks` 给（gen_scene 的全局 MARKS）。

    场景模块拿不到那个全局：脚本以 `__main__` 跑时它在 `__main__` 里，被 import 时
    在 `gen_scene` 里。两处都看一眼，取不到就用默认成数（按片长算，写死秒数会在
    短版里把动作挤出画面）。
    """
    for name in ("gen_scene", "__main__"):
        mod = sys.modules.get(name)
        mk = getattr(mod, "MARKS", None) if mod is not None else None
        if mk and len(mk) >= len(default):
            return tuple(float(x) for x in mk)
    return default


def _beats(dur: float) -> tuple[float, float, float]:
    """纸落桌 / 逐项展开 / 推到并排 三拍。"""
    m = _marks((0.055, 0.245, 0.72))
    return dur * m[0], dur * m[1], dur * m[2]


# ---------------------------------------------------------------- 透视
def _persp(dst, src) -> tuple[float, ...]:
    """PIL PERSPECTIVE 的 8 个系数。它做的是**逆映射**（输出像素 → 输入像素），
    所以这里传的是 dst → src，传反了整张纸会翻到画外去。"""
    A, b = [], []
    for (X, Y), (x, y) in zip(dst, src):
        A.append([X, Y, 1, 0, 0, 0, -X * x, -Y * x])
        b.append(x)
        A.append([0, 0, 0, X, Y, 1, -X * y, -Y * y])
        b.append(y)
    return tuple(np.linalg.solve(np.array(A, float), np.array(b, float)))


def _warp(img: Image.Image, quad, size: tuple[int, int]):
    """把一张平面图按四角映射贴到画布坐标系里，只处理它自己那块外接矩形。"""
    Wd, Hd = size
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    x0 = max(0, int(math.floor(min(xs))) - 2)
    y0 = max(0, int(math.floor(min(ys))) - 2)
    x1 = min(Wd, int(math.ceil(max(xs))) + 2)
    y1 = min(Hd, int(math.ceil(max(ys))) + 2)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None, (0, 0)
    sw, sh = img.size
    dst = [(px - x0, py - y0) for px, py in quad]
    src = [(0, 0), (sw, 0), (sw, sh), (0, sh)]
    coeffs = _persp(dst, src)
    out = img.transform((x1 - x0, y1 - y0), Image.PERSPECTIVE, coeffs,
                        resample=Image.BICUBIC)
    return out, (x0, y0)


def _shadow(canvas: Image.Image, quad, sw: int, sh: int, *, blur: float,
            alpha: float, dx: float = 0.0, dy: float, grow: float = 1.0) -> None:
    """纸下面的投影。落地那一拍靠它——没有影子的纸读成浮在屏上的图标（§3）。

    影要**偏**：光从左上打，影子就往右下走。正下方一团糊影是「卡片投影」，
    偏出去的一团才是「东西搁在桌面上」。
    """
    if alpha <= 0.01:
        return
    cx = sum(p[0] for p in quad) / 4
    cy = sum(p[1] for p in quad) / 4
    q = [(cx + (p[0] - cx) * grow + dx, cy + (p[1] - cy) * grow + dy) for p in quad]
    mask = Image.new("L", (sw, sh), 255)
    m, (x0, y0) = _warp(mask, q, canvas.size)
    if m is None:
        return
    m = m.filter(ImageFilter.GaussianBlur(max(0.5, blur)))
    a = np.asarray(m, dtype=np.float32) * min(1.0, max(0.0, alpha))
    layer = Image.new("RGBA", m.size, (0, 0, 0, 0))
    layer.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L"))
    canvas.alpha_composite(layer, (x0, y0))


# ---------------------------------------------------------------- 纸面
def _well(paper: Image.Image, box, depth: float, sh: float) -> None:
    """空白窗口：纸面上凹下去的一格。

    不许红叉、不许感叹号、不许警告三角（这一类符号一出现，观众读的是「出错了」，
    而这里要读的是「这一格本来该有东西」）。做法纯靠光影：光从左上来，所以凹口的
    **上沿和左沿断光（暗）**，下沿和右沿把光反上来（亮）。

    不画圆角、不描边，也不给一块均匀的灰底：均匀灰底 + 一圈边 = 输入框（视觉复核原话）。
    凹口是一道**从暗到纸色的渐变**，四边的光影都是衰减的，不是描出来的线。
    """
    if depth <= 0.02:
        return
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = max(2, x1 - x0), max(2, y1 - y0)
    gx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    gy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    lip = np.exp(-gy * 4.6)                            # 上沿：光在这里断掉
    left = np.exp(-gx * 7.0) * 0.40                    # 左沿：同样断光，弱一档
    foot = np.clip((gy - 0.72) / 0.28, 0, 1) * 0.75    # 下沿：凹底反射回来的光
    right = np.clip((gx - 0.78) / 0.22, 0, 1) * 0.55   # 右沿：同弱
    dark = np.clip(lip + left, 0, 1) * depth
    light = np.clip(foot + right, 0, 1) * depth
    base = np.array([223, 215, 197], np.float32)
    col = np.broadcast_to(base, (h, w, 3)).astype(np.float32).copy()
    col -= dark[..., None] * np.array([122, 112, 94], np.float32)
    col += light[..., None] * np.array([17, 16, 13], np.float32)
    layer = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    layer.putalpha(Image.new("L", (w, h), int(255 * min(1.0, depth))))
    paper.alpha_composite(layer, (x0, y0))


def _box(a: np.ndarray, r: float) -> np.ndarray:
    r = max(1, int(round(r)))
    for ax in (0, 1):
        n = a.shape[ax]
        pad = np.zeros_like(np.take(a, [0], axis=ax))
        c = np.cumsum(np.concatenate([pad, a], axis=ax), axis=ax)
        hi = np.take(c, np.clip(np.arange(n) + r + 1, 0, n), axis=ax)
        lo = np.take(c, np.clip(np.arange(n) - r, 0, n), axis=ax)
        cnt = np.clip(np.arange(n) + r + 1, 0, n) - np.clip(np.arange(n) - r, 0, n)
        cnt = cnt.reshape((-1, 1) if ax == 0 else (1, -1)).astype(np.float32)
        a = (hi - lo) / cnt
    return a


def _blur(a: np.ndarray, r: float) -> np.ndarray:
    """浮点数组上的高斯（三次盒滤波近似）。PIL 的 C 高斯不接 F 模式，先模糊再转 uint8
    又会把颗粒压平——纸纹必须先在浮点上做出来。"""
    for _ in range(3):
        a = _box(a, max(0.6, r / 1.73))
    return a


def _cloud(rng, shape, cell: float, amp: float) -> np.ndarray:
    """一块「不匀」：噪声模糊成云，再归一化到**目标亮度标准差**。

    不归一化就写不匀：模糊半径一大，噪声就被平均掉了（第一版写死乘 13，实际只剩 ±0.2
    个亮度级——纸上看着还是纯色平涂）。要多少级就给多少级。
    """
    b = _blur(rng.normal(0.0, 1.0, shape), cell)
    s = float(b.std()) or 1.0
    return b * (amp / s)


def _paper_base(sw: int, sh: int, seed: int, sheen: float, tint: float = 0.0) -> np.ndarray:
    """纸面本身：竖向的明暗、成团的纤维颗粒、极缓的云斑、四边微暗、近端微微翘起。

    一张纯平的浅色矩形不管加多少阴影都读成「卡片」（视觉复核原话）。纸要读成纸，
    靠的是**不匀**，而且是好几个尺度上的不匀：颗粒（1px）、云斑（1/16 纸宽）、
    整张纸受光不一（1/3 纸宽）；再加四边微暗和近端上翘的黑边。两张纸本来也不是
    同一批纸：tint 让乙那张黄一点、旧一点。
    """
    rng = np.random.default_rng(seed)
    ramp = np.linspace(0, 1, sh, dtype=np.float32)[:, None]
    base = np.empty((sh, sw, 3), np.float32)
    for c in range(3):
        base[..., c] = PAPER_TOP[c] * (1 - ramp) + PAPER_BOT[c] * ramp
    base -= np.array([0.0, 3.0, 9.0], np.float32)[None, None, :] * tint
    uneven = (_cloud(rng, (sh, sw), 1.2, 2.4)                 # 颗粒：成团，不是噪点
              + _cloud(rng, (sh, sw), 0.055 * sw, 3.2)         # 云斑
              + _cloud(rng, (sh, sw), 0.32 * sw, 3.6))         # 整张纸受光不一
    base += uneven[..., None]
    # 纤维：几道极淡的横向压光纹（纸是压出来的，不是喷出来的）
    base += (0.9 + 0.9 * np.sin(np.arange(sh, dtype=np.float32)[:, None] * 0.42))[..., None]
    # 尘埃：落在桌上过的纸会粘几粒东西（1px，不刷得满地都是）
    dust = np.zeros((sh, sw), np.float32)
    dy_, dx_ = rng.integers(0, sh, 90), rng.integers(0, sw, 90)
    dust[dy_, dx_] = -rng.uniform(10.0, 34.0, 90)
    base += dust[..., None]
    # 四边微暗：纸有厚度，边缘折光少；横向再压一点，光从左边进来
    gx = np.linspace(0, 1, sw, dtype=np.float32)[None, :]
    gy = np.linspace(0, 1, sh, dtype=np.float32)[:, None]
    ex = 6.0 * (1 - np.clip(np.sin(np.pi * gx), 0, 1) ** 0.35) + 7.0 * gx
    ey = 4.5 * (1 - np.clip(np.sin(np.pi * gy), 0, 1) ** 0.35)
    base -= (ex + ey)[..., None]
    # 近端上翘：纸不会严丝合缝贴着桌面，最后一小截有自身的弯度阴影
    near = np.clip((gy - 0.945) / 0.055, 0, 1) ** 1.6
    base -= (near * 9.0)[..., None]
    # 一道缓慢划过纸面的高光（光在动，纸才不是一张印上去的图）
    band = np.exp(-((gx - sheen) / 0.20) ** 2) * 9.0
    base += band[..., None] * np.array([1.0, 0.94, 0.74], np.float32)[None, None, :]
    return np.clip(base, 0, 255)


def _sheet(sw: int, sh: int, tag: str, total: str, rows, progs,
           seed: int, sheen: float = 0.5, tint: float = 0.0) -> Image.Image:
    """一张报价单的纸面：平视、未变形（变形在 `_warp` 里一次做完）。

    字和行线画在**单独一层**上，最后整体轻微模糊一次再压到纸面上——真实的印刷没有
    矢量边缘，锐利的黑字是「截图」的味道（视觉复核把锐利边缘读成 UI 卡片）。
    凹下去的那一格不在这层：它是纸面自己的起伏，不跟字一起模糊。

    逐项展开在这里：每项一个 `progs[i]`，行线从左边长出去、字随后浮上来——一项一项地，
    不是四条同时铺满。
    """
    paper = Image.fromarray(_paper_base(sw, sh, seed, sheen, tint).astype(np.uint8),
                            "RGB").convert("RGBA")
    ink = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(ink, "RGBA")

    mx = LAYOUT["mx"] * sw
    inner = sw - 2 * mx
    f_title = _font(FONT_BOLD, max(9, int(0.042 * sh)))
    f_small = _font(FONT_SEMI, max(8, int(0.030 * sh)))
    f_cur = _font(FONT_SEMI, max(9, int(0.052 * sh)))
    f_num = _font(FONT_BLACK, max(14, int(0.125 * sh)))
    f_row = _font(FONT_BOLD, max(8, int(0.038 * sh)))
    f_val = _font(FONT_SEMI, max(8, int(0.042 * sh)))
    lw = max(1, int(0.0028 * sh))

    # —— 抬头：报什么价、哪一家（打字打上去的，不是贴一个胶囊标签）
    hy = 0.078 * sh
    d.text((mx, hy), "报价单", font=f_title, fill=(*INK, 232), anchor="lm")
    tw_ = d.textlength("报价单", font=f_title)
    d.text((mx + tw_ + 0.030 * sw, hy), tag, font=f_title, fill=(*BRASS, 245), anchor="lm")
    d.line([mx, 0.118 * sh, sw - mx, 0.118 * sh], fill=(*RULE, 105), width=lw)

    # —— 总价：全画面最大的一行，两张纸同字号同位置
    d.text((mx, 0.168 * sh), "总价", font=f_small, fill=(*INK_MUTED, 225), anchor="lm")
    base_y = 0.306 * sh
    cur_w = d.textlength("¥", font=f_cur)
    d.text((mx, base_y), "¥", font=f_cur, fill=(*INK_MUTED, 235), anchor="ls")
    d.text((mx + cur_w + 0.008 * sw, base_y), total, font=f_num,
           fill=(*INK, 250), anchor="ls")
    d.line([mx, 0.352 * sh, mx + inner * 0.56, 0.352 * sh], fill=(*RULE, 105), width=lw)

    # —— 四条对比项：一条一条长出来
    y0, pitch = LAYOUT["y0"] * sh, LAYOUT["pitch"] * sh
    vw = LAYOUT["vw"] * sw
    for i, (label, value) in enumerate(rows):
        p = progs[i] if i < len(progs) else 0.0
        if p <= 0.0:
            continue
        cy = y0 + i * pitch
        ry = cy + 0.045 * sh
        # 行线只走 48%：拉满整幅会立刻被读成表格的分隔线（视觉复核原话）
        d.line([mx, ry, mx + inner * 0.48 * p, ry],
               fill=(*RULE, int(88 * min(1.0, p * 1.5))), width=lw)
        if value is None:
            _well(paper, (sw - mx - vw, cy - LAYOUT["half"] * sh,
                          sw - mx, cy + LAYOUT["half"] * sh),
                  min(1.0, max(0.0, (p - 0.15) / 0.70)), sh)
        # 字是一笔一笔划过去的，不是整体淡入：淡入读成「加载完成」，划过去才读成「正在写」。
        # 所以每一行先画在自己那一小条上，再按进度从左往右裁一段贴回去——任何一帧都能看出来
        # 这一行写到哪儿了（整体淡入的话，前半段等于什么都没有）。
        rh = int(0.105 * sh)
        tmp = Image.new("RGBA", (sw, rh), (0, 0, 0, 0))
        dt = ImageDraw.Draw(tmp, "RGBA")
        dt.text((mx, rh * 0.5), label, font=f_row, fill=(*INK, 238), anchor="lm")
        if value is not None:
            dt.text((sw - mx, rh * 0.5), value, font=f_val, fill=(*INK, 244), anchor="rm")
        cut = int(round(sw * min(1.0, p)))
        if cut > 1:
            ink.alpha_composite(tmp.crop((0, 0, cut, rh)),
                                (0, int(round(cy - rh * 0.5))))

    # —— 页脚：签字那一栏。留着它，纸才像一张要签字才作数的单子
    sy = 0.902 * sh
    d.line([mx, sy, mx + 0.50 * sw, sy], fill=(*RULE, 95), width=lw)
    d.text((mx, sy + 0.040 * sh), "签字", font=f_small, fill=(*INK_MUTED, 165), anchor="lm")

    paper.alpha_composite(ink.filter(ImageFilter.GaussianBlur(0.45 + 0.25 * sw / 454)))
    # 纸边不是刀切的：靠边 2px 按噪声磨一下，直尺边是「导出的图形」的味道
    nb = max(1, int(0.005 * sw))
    a = np.asarray(paper.getchannel("A"), dtype=np.float32).copy()
    noise = np.random.default_rng(seed + 7).random(a.shape)
    a[:nb] = np.where(noise[:nb] > 0.30, a[:nb], 0)
    a[-nb:] = np.where(noise[-nb:] > 0.30, a[-nb:], 0)
    a[:, :nb] = np.where(noise[:, :nb] > 0.30, a[:, :nb], 0)
    a[:, -nb:] = np.where(noise[:, -nb:] > 0.30, a[:, -nb:], 0)
    paper.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L"))
    return paper


# ---------------------------------------------------------------- 桌面与光
def _table(t: float, W: int, H: int) -> np.ndarray:
    xx, yy = _grid(W, H)
    bg = np.zeros((H, W, 3), np.float32)
    ramp = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    for c in range(3):
        bg[..., c] = BG_TOP[c] * (1 - ramp) + BG_BOT[c] * ramp
    # 木纹：横向拉长的随机斑 + 一道极缓的明暗，桌面才不是背景板
    bg += (_wood(W, H) * 13.0)[..., None] * np.array([1.0, 0.84, 0.66], np.float32)
    bg += (np.sin(0.010 * yy + 0.7) * 5.0)[..., None]
    # 光从左上方斜下来，光位极慢地横移——片头片尾不要同一个光位
    cx = W * (0.42 + 0.07 * math.sin(t * 0.19))
    cy = H * (0.40 + 0.02 * math.sin(t * 0.13 + 1.1))
    r2 = ((xx - cx) / W) ** 2 + ((yy - cy) / H) ** 2
    halo = np.exp(-r2 * 4.0) * (54.0 + 2.6 * math.sin(t * 1.7))
    bg += halo[..., None] * np.array([1.0, 0.86, 0.50], np.float32)
    # 两张纸所在的那块桌面本来就是被照着的。背景一片纯黑、纸一片雪白、中间没有过渡，
    # 就只剩剪影——纸没有影子可落，就只能「浮」着（视觉复核原话：没有表面，纸是飘的）。
    # 桌面得有亮度，影子才有地方显形。
    pool = np.exp(-(((xx - W * 0.5) / (0.66 * W)) ** 2
                    + ((yy - H * 0.46) / (0.38 * H)) ** 2)) * 62.0
    bg += pool[..., None] * np.array([1.0, 0.84, 0.58], np.float32)
    # 板缝：桌面是拼出来的。一道接缝比一百道纹理都能说清「这是一张桌子」
    for yf, a in ((0.128, 0.55), (0.952, 0.75)):
        yy0 = yf * H
        seam = np.exp(-((yy - yy0) / (0.0022 * H)) ** 2)
        bg -= (seam * 26.0 * a)[..., None]
        bg += (np.exp(-((yy - yy0 - 0.0035 * H) / (0.0020 * H)) ** 2)
               * 9.0 * a)[..., None]
    # 桌上的浮尘：桌面一干净就成背景板了
    rr = np.random.default_rng(5)
    dust = np.zeros((H, W), np.float32)
    dy_, dx_ = rr.integers(0, H, 240), rr.integers(0, W, 240)
    dust[dy_, dx_] = rr.uniform(6.0, 20.0, 240)
    bg += dust[..., None] * np.array([1.0, 0.9, 0.7], np.float32)
    return np.clip(bg, 0, 255).astype(np.uint8)


def _cloud_periodic(W: int, H: int, seed: int, cells) -> np.ndarray:
    """周期性云斑：FFT 上乘高斯传递函数，结果天然首尾相接。

    `cells` 是 (σx, σy, 权重)：各向同性的（12,12）是云斑，各向异性的（260,7）是木纹。
    为什么不用「几对正弦相乘」：那个画出来是一层菱形网格——桌面上出现规则网格，
    视觉复核立刻读成「壁纸/贴图」，不是桌子（2026-11-14 实测）。纹理得是随机的。
    周期性是必需的：光斑靠 roll 平移、木纹跟着光走，不首尾相接就会滚出一条竖接缝。
    """
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(H)[:, None]
    fx = np.fft.fftfreq(W)[None, :]
    out = np.zeros((H, W), np.float32)
    for sx, sy, amp in cells:
        n = rng.normal(0.0, 1.0, (H, W)).astype(np.float32)
        k = np.exp(-2.0 * (np.pi ** 2) * ((sx * fx) ** 2 + (sy * fy) ** 2))
        out += amp * np.real(np.fft.ifft2(np.fft.fft2(n) * k)).astype(np.float32)
    out -= out.mean()
    return (out / (out.std() + 1e-6)).astype(np.float32)


def _dapple(W: int, H: int) -> np.ndarray:
    """窗子那边过来的光斑（云状，不是网格）。"""
    key = (W, H)
    d = _DAPPLE.get(key)
    if d is None:
        d = _cloud_periodic(W, H, 777, ((0.010 * W, 0.010 * W, 1.0),
                                        (0.035 * W, 0.035 * W, 0.55)))
        d = np.clip(d, -2.5, 2.5).astype(np.float32)
        _DAPPLE[key] = d
    return d


def _wood(W: int, H: int) -> np.ndarray:
    """桌面的木纹：横向拉长的斑（σx 大、σy 小），不是一条条正弦——正弦是贴图。"""
    key = ("wood", W, H)
    d = _DAPPLE.get(key)
    if d is None:
        d = _cloud_periodic(W, H, 4242, ((0.16 * W, 0.010 * H, 1.0),
                                         (0.055 * W, 0.007 * H, 0.70),
                                         (0.30 * W, 0.032 * H, 0.45)))
        d = np.clip(d, -2.5, 2.5).astype(np.float32)
        _DAPPLE[key] = d
    return d


def _light(x: np.ndarray, t: float, W: int, H: int) -> np.ndarray:
    """压在整幅画面上的那一层光：一条很宽的窗光慢慢横扫 + 窗外的光斑在动 + 呼吸。

    这一层是「一直在动」的保险：两张纸落定、四项展开完以后，画面要是完全不动，
    `measure_motion` 最长静止就会报红，观众看到的就是「一张图放在那儿」
    （实测第一版：第 3–6 秒帧差只有 0.3，四秒静止）。

    三条同时跑，是因为单靠一条都不够——位移量太小的话，帧差全被首尾相接的
    平缓渐变吃掉了：宽光带扫过只改边缘、呼吸只改整体亮度、光斑只改纹理。
    """
    s = W / 1080.0
    xx, _ = _grid(W, H)
    bx = -0.30 + ((t * 0.135) % 1.52)          # 归一化 x：0.135 / 秒 → 约 7.4 秒扫一趟
    band = np.exp(-(((xx / W) - bx) / 0.21) ** 2).astype(np.float32)
    breath = 1.0 + 0.016 * math.sin(2 * math.pi * 1.15 * t) + 0.010 * math.sin(2 * math.pi * 0.43 * t + 1.2)
    ox = int(round(t * 190 * s)) % W
    oy = int(round(t * 66 * s)) % H
    pat = np.roll(_dapple(W, H), (oy, ox), axis=(0, 1))
    # 亮部（纸面）少受一点光斑：纸是漫反射，光斑在它身上本来就弱；
    # 全强度压上去，纸面会被读成「脏」而不是「被照到」。
    lum = x.mean(axis=2)
    w = 1.0 - 0.62 * np.clip((lum - 165.0) / 60.0, 0.0, 1.0)
    gain = breath * (1.0 - 0.052 * band) * (1.0 - 0.062 * pat * w)
    out = x.astype(np.float32) * gain[..., None]
    out += (band * 9.0)[..., None] * np.array([1.0, 0.84, 0.58], np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 两张纸在桌上的姿态
def _quads(t: float, W: int, H: int, dur: float) -> tuple[dict, dict]:
    """这一秒两张纸各自落在屏幕上的四个角 + 落下进度。

    三段落：落桌（带一点歪）→ 各自让开一点、放正 → 推到并排、转正、行行对齐。
    最后一段不再改大小：纸一旦推到位，字就该停在原地给观众读，不许继续缩放。
    """
    b_land, b_rows, b_align = _beats(dur)
    sw = 0.42 * W
    sh = sw * 1.4142
    land_b = b_land + 0.035 * dur
    fall_s = 0.040 * dur
    spread0 = b_land + 0.035 * dur + 0.040 * dur
    spread1 = b_rows - 0.020 * dur
    align0 = b_align
    align1 = b_align + 0.24 * dur

    out = []
    # (落桌位, 让开位, 并排位)；x/y 是成数，rot 是度
    poses = (((0.345, 0.440, -5.5), (0.300, 0.442, -2.2), (0.285, 0.462, 0.0)),
             ((0.655, 0.478, 6.0), (0.700, 0.442, 2.2), (0.715, 0.462, 0.0)))
    for (land, spread, final), td, sgn in ((poses[0], b_land, -1.0), (poses[1], land_b, 1.0)):
        ks = _span(t, spread0, spread1)
        ka = _span(t, align0, align1)
        p = tuple(land[k] + (spread[k] - land[k]) * ks for k in range(3))
        p = tuple(p[k] + (final[k] - p[k]) * ka for k in range(3))
        kf = _ease_out((t - (td - fall_s)) / fall_s)
        # 落桌之后压一下（1.5px，0.14s 收干）：纸接触桌子是「止住」，不是「弹起」
        press = 1.4 * (1.0 - _ease_out((t - td) / max(1e-6, 0.14))) if t >= td else 0.0
        cx = p[0] * W
        cy = p[1] * H + (1.0 - kf) * 0.30 * H + press
        rot = p[2] + (1.0 - kf) * 7.0 * sgn
        scale = 1.0 + 0.06 * (1.0 - kf)
        # 呼吸位移 ≤1px：纸在桌上不会飘，但完全不动就是一张图
        cy += 0.7 * math.sin(t * 0.9 + (0.0 if sgn < 0 else 1.7))
        half_t = sw * 0.5 * 0.900 * scale      # 上沿窄 10%：看的人比桌子高
        half_b = sw * 0.5 * scale
        half_th = sh * 0.5 * 0.955 * scale     # 上沿还被拉近：纵向也上窄下宽
        half_bh = sh * 0.5 * scale
        pts = [(-half_t, -half_th), (half_t, -half_th),
               (half_b, half_bh), (-half_b, half_bh)]
        r = math.radians(rot)
        co, si = math.cos(r), math.sin(r)
        quad = [(cx + x * co - y * si, cy + x * si + y * co) for x, y in pts]
        out.append({"quad": quad, "fall": kf, "sw": sw * scale, "sh": sh * scale,
                    "visible": t >= td - fall_s})
    return out[0], out[1]


def frame_quads(t: float, W: int, H: int, dur: float) -> dict:
    """量测用：这一秒两张纸（甲 / 乙）在屏幕上的四个角。读数脚本拿它算
    「两张纸的 x 位置差」和「空白那一格有多少墨」，不靠形容词。"""
    a, b = _quads(t, W, H, dur)
    return {"甲": a["quad"], "乙": b["quad"], "甲落在": a["fall"], "乙落在": b["fall"]}


def _edges(d: ImageDraw.ImageDraw, quad, th: float) -> None:
    """三面律：纸面（顶面）之外，下沿是切面、右沿是暗切面，上沿一道细高光。"""
    q0, q1, q2, q3 = quad
    d.polygon([q3, q2, (q2[0], q2[1] + th), (q3[0], q3[1] + th)],
              fill=(*PAPER_EDGE_FRONT, 255))
    d.polygon([q1, q2, (q2[0] + th * 0.30, q2[1] + th * 0.92),
               (q1[0] + th * 0.30, q1[1] + th * 0.92)], fill=(*PAPER_EDGE_SIDE, 255))
    d.line([q0, q1], fill=(255, 252, 238, 110), width=max(1, int(th * 0.35)))


# ---------------------------------------------------------------- 场景
def scene(t: float, W: int, H: int, dur: float) -> Image.Image:
    dur = max(1.0, float(dur))
    s = W / 1080.0
    b_land, b_rows, b_align = _beats(dur)
    a, b = _quads(t, W, H, dur)

    # 逐项展开：每项自己的时刻（成数按片长算，短版也不会挤在一起）。
    # 动画比间隔长，所以任何一帧都有可能「正在展开某一项」，不会出现「全都摆好了」的顿点。
    step = 0.0975 * dur
    anim = 0.090 * dur
    progs = [_span(t, b_rows + i * step, b_rows + i * step + anim) for i in range(4)]

    canvas = Image.fromarray(_table(t, W, H)).convert("RGBA")
    d = ImageDraw.Draw(canvas, "RGBA")
    th = 0.0105 * W            # 纸的厚度（进深 5–7px @900 → 这里 11px @1080）
    # 纸面上那道划过的高光跟着全画面的光走，两张纸相位差开
    sheen_a = -0.30 + ((t * 0.135) % 1.52)
    sheen_b = sheen_a + 0.26

    for pose, tag, total, rows, seed, sh_ph in (
            (a, "甲", TOTAL_A, ROWS_A, 11, sheen_a),
            (b, "乙", TOTAL_B, ROWS_B, 23, sheen_b)):
        if not pose["visible"]:
            continue
        q, kf = pose["quad"], pose["fall"]
        sw, sh = int(round(pose["sw"])), int(round(pose["sh"]))
        # 影子：落下途中又虚又淡，贴桌那一刻收紧、变实（§3 接地律）
        _shadow(canvas, q, sw, sh, blur=(7.0 + 17.0 * kf) * s,
                alpha=0.66 * kf, dx=0.012 * W, dy=(0.014 * pose["sh"]) + 6 * s,
                grow=1.02 + 0.05 * (1 - kf))
        _shadow(canvas, q, sw, sh, blur=2.4 * s, alpha=0.40 * kf,
                dx=0.003 * W, dy=th * 0.45, grow=1.0)
        face = _sheet(sw, sh, tag, total, rows, progs, seed,
                      sheen=sh_ph % 1.0, tint=0.0 if tag == "甲" else 1.0)
        warped, (ox, oy) = _warp(face, q, canvas.size)
        if warped is not None:
            canvas.alpha_composite(warped, (ox, oy))
        _edges(d, q, th)

    return Image.fromarray(_light(np.asarray(canvas.convert("RGB")), t, W, H), "RGB")
