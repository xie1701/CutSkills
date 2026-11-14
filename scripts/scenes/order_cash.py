"""订单越多现金越紧 —— 方案 P1 · 案例 B（situational）。

一句口播：「订单多了，老板更忙了，最后现金却更紧。」（追溯见 `references/scene-specs/scene-order-cash.json`）

**为什么这一格非画不可**：库里拍不到「票据在涨、现金在掉」这两件事**同时**发生
（原则 B14：补镜只打库里没有、也拍不出来的关系）。这里要替的不是某个现场的实拍，
是一个**量的对比 + 方向相反的因果**，只能靠一块画面自己演。

**形状即语义**（scene-art-standard §1）落在两件实物上，而不是两根柱子：

  · 左边：订单票据一张张从上方飘落，**落到桌面就叠上去**，堆真的变高；
  · 右边：一沓硬币**一枚一枚被拿走**，拿走一枚整沓矮一截（不是渐隐）；
  · 最后镜头一拉，两个方向的量差自己说话——**全片不上一个字**。

数量关系写死，不随机：落 `N_SLIPS=7` 张票、支出 `N_EXPENSE=5` 笔、现金 `N_LAYERS=6` 层，
**一笔支出正好取走一枚**（6 − 5 = 1），所以结尾「票 7 张、钱剩 1 层」是数得出来的，
不是画着画着随手停下的。

伪 3D（§2 三面律）：票据是薄片（顶面 + 前侧 + 右侧三面，边 1–2px 量级）；
硬币按标准里的圆柱写法——**顶面 + 柱面横向渐变 + 一道上棱高光 + 一道底边暗边 + 一层齿边细竖纹**，
枚与枚之间靠"上压下的自阴影"分层，不靠描边分家。
接地（§3）：桌面是一块斜俯视的平面（双线性透视：远边窄、近边宽），
票据堆与硬币柱各有一层软投影，**每张落下的票据自带接触阴影**（越近越实、越远越散）。

纯 `t` 驱动：所有"随机"都是下标推出来的固定值，`t` 相同则逐像素相同
（`scripts/test_scene_determinism.py` 的合同）。
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

SCENE_ID = "order_cash"

# ---------------------------------------------------------------- 数量对账
# 写死的三个数，画面里能一张张 / 一层层数出来，不靠文字说明。
N_SLIPS = 7          # 落下的订单票据张数
N_EXPENSE = 5        # 支出次数（每笔支出取走一层现金）
N_LAYERS = 6         # 现金池初始层数；N_LAYERS - N_EXPENSE = 1 → 收尾真剩一层

# ---------------------------------------------------------------- 时间（秒）
FALL_S = 0.36        # 单张票据从入画到落桌（纸片会飘；比 §6 的 0.24s 略长，不弹跳）
SETTLE_S = 0.09      # 落桌后轻微下压、回弹到静止
LIFT_S = 0.75        # 一笔支出：抬起 → 甩出画面（0.2 抬 + 0.55 走）
PULL_S = 0.88        # 结尾镜头一拉

SS = 2               # 超采样倍数：PIL 的 polygon 不抗锯齿，2× 再缩一半才有实物的边

# ---------------------------------------------------------------- 配色
# 沿用库里 gen 场景的暖底（深棕 + 暖金晕），禁科技蓝 / 霓虹 / 紫粉（§5）。
DESK_FAR = (104, 82, 62)         # 桌面远处（受光多一点）—— 桌面得够亮，影子才看得见
DESK_NEAR = (50, 40, 31)         # 桌面近处（压暗，才有俯视的进深）
PAPER = (228, 220, 203)          # 票据纸（米白，不吃金色判据）
PAPER_ALT = (240, 233, 219)      # 相邻两张纸差一点点，堆才有"一张一张"的层次
PAPER_EDGE = (168, 157, 138)     # 票据侧面（厚度）
PAPER_SEAM = (74, 66, 55)        # 两张纸之间的缝（纸与纸是靠影子分家的）
PAPER_INK = (146, 136, 120)      # 票据上的印刷横线（不写字，只留行距）
STAMP = (150, 62, 44)            # 单据上的印章块（深陶土红；刻意不落在金色判据里）
CASH_FACE = (230, 192, 112)      # 硬币顶面（受光最亮）
CASH_FACE2 = (204, 166, 92)      # 下面的硬币暗一档（上压下的自阴影）
CASH_RIM_HI = (196, 160, 96)     # 柱面受光带
CASH_RIM = (140, 108, 60)        # 柱面中调
CASH_RIM_DARK = (92, 70, 38)     # 柱面暗侧
CASH_EDGE = (58, 43, 22)         # 硬币底边（最暗的一圈）

# 1080 宽下的基础尺寸（其余一切按 W/1080 等比，见 scene()）
PITCH_1080 = 18.0     # 一张票据的可见厚度 —— 7 张叠起来才有"堆"（层数数得出来）
LAYER_1080 = 34.0     # 一枚硬币的厚度（= 一层现金）—— 6 枚摞起来才是一沓
BAND_1080 = 0.92      # 票据侧面高度 = PITCH × 这个数（留一道缝）

# ---------------------------------------------------------------- 音效锚点
# t 是按 **dur=8s**（本场景默认片长）标定的场景内相对秒；落在动作**起点**上（§7）。
# name 必须是 `cc0_lib.SFX_MAP` 的 key（由 pick_sfx 解析成 CC0 库里的具体文件）：
#   impact → impact-light-firm（东西落地 / 卡片拍下）
#   pop    → pop-medium-in（列表逐条出现）
#   whoosh → whoosh-medium-soft（物体横移、抽走）
EVENTS = [
    {"id": "slip-1", "t": 0.36, "name": "impact",
     "intent": "第一张订单票据落到桌面（落桌触点，接触声）"},
    {"id": "slip-2", "t": 0.43, "name": "pop",
     "intent": "第二张票据开始下落（票据开始一张接一张地来）"},
    {"id": "expense-1", "t": 3.00, "name": "whoosh",
     "intent": "第一笔支出：最上面那一枚硬币被抬离桌面"},
    {"id": "expense-5", "t": 6.15, "name": "whoosh",
     "intent": "最后一笔支出：整沓只剩一枚"},
]

STORY_BEATS = {
    "setup": "订单票据从上方一张张飘落到桌面，落到就叠上去；右边一沓硬币原封不动。",
    "change": "票据越叠越高；每付一笔钱，右边最上面那枚硬币被抬走，整沓矮一截。",
    "result": "镜头一拉：左边七张票堆成一座，右边只剩一枚——同一张桌上，两个方向。",
}


# ---------------------------------------------------------------- 小工具
def _gc():
    """延迟取 gen_scene 的底子 / 缓动表（避免 import 期的循环依赖）。"""
    import gen_scene as g
    return g


def _mix(a, b, k: float) -> tuple:
    return tuple(int(round(a[i] * (1 - k) + b[i] * k)) for i in range(3))


def _rotpt(x: float, y: float, cx: float, cy: float, deg: float) -> tuple:
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    dx, dy = x - cx, y - cy
    return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)


def _marks(default: tuple) -> tuple:
    """节拍由调用方（词级时间戳）定；没给就用默认比例。"""
    try:
        mk = _gc().MARKS
    except Exception:                       # noqa: BLE001 —— 单独 import 时也给得出默认值
        mk = []
    return tuple(mk) if len(mk) >= len(default) else default


def _times(dur: float) -> dict:
    """三拍的时间轴：票据落齐 → 逐笔支出 → 拉镜头。全按 dur 的比例算，片长变了也不塌。"""
    b1, b2, b3 = _marks((0.00, 0.375, 0.870))
    land_first = dur * b1 + FALL_S
    land_last = dur * b2 - 0.06             # 最后一张要在第二拍之前落定
    step = (land_last - land_first) / (N_SLIPS - 1) if N_SLIPS > 1 else 0.0
    lands = [land_first + i * step for i in range(N_SLIPS)]
    starts = [x - FALL_S for x in lands]
    exp0 = dur * b2
    exp_last = dur * b3 - LIFT_S - 0.06     # 最后一笔要在拉镜头之前走完
    estep = (exp_last - exp0) / (N_EXPENSE - 1) if N_EXPENSE > 1 else 0.0
    return {"lands": lands, "starts": starts,
            "exp": [exp0 + j * estep for j in range(N_EXPENSE)],
            "pull0": dur * b3}


def readout(t: float, W: int, H: int, dur: float) -> dict:
    """场景自报状态，用于对账（像素侧另有独立测量，见交付读数）。

    高度单位是 1080 宽下的设计像素（未计镜头缩放）：堆高 = 落定张数 × PITCH，
    池高 = 剩余层数 × LAYER。
    """
    tm = _times(dur)
    landed = sum(1 for x in tm["lands"] if t >= x)
    taken = sum(1 for x in tm["exp"] if t >= x)
    S = W / 1080.0
    return {"t": round(t, 3), "slips_landed": landed,
            "cash_layers": N_LAYERS - taken,
            "pile_height": round(landed * PITCH_1080 * S, 1),
            "cash_height": round((N_LAYERS - taken) * LAYER_1080 * S, 1)}


# ---------------------------------------------------------------- 画一帧
def _paint(t: float, W: int, H: int, dur: float) -> Image.Image:
    g = _gc()
    S = W / 1080.0

    # 底子保持 RGB：`ImageDraw.Draw(im, "RGBA")` 只有在 RGB 画布上才会**真混合**
    # （RGBA 画布上它把带 alpha 的颜色整块写进去、不做合成 —— 实测 22/255 的暖光
    #  变成满屏纯黄。PIL 13 的 Draw(mode) 语义，踩过一次，别改回 RGBA 画布。）
    im = Image.fromarray(g._backdrop(t, W, H, cy=0.50, glow=0.85), "RGB")
    d = ImageDraw.Draw(im, "RGBA")

    tm = _times(dur)
    lands, starts, exp = tm["lands"], tm["starts"], tm["exp"]

    # 镜头：一条连续曲线（§6）——前段极慢推近，收尾 0.7s 一拉到底，中间不切不回正。
    z_in = 1.020 + 0.035 * g.ease(min(1.0, t / max(1e-6, tm["pull0"])))
    if t <= tm["pull0"]:
        z = z_in
    else:
        z = z_in * (1.0 - 0.17 * g.ease(min(1.0, (t - tm["pull0"]) / PULL_S)))
    # 手持呼吸：≤2px（§6），只为把"完全静止"的帧抹掉，不让人察觉
    ox = 2.0 * S * math.sin(t * 0.87)
    oy = 1.7 * S * math.sin(t * 1.23 + 1.1)
    cx, cy = 0.5 * W, 0.5 * H

    def T(p):
        return (cx + (p[0] - cx) * z + ox, cy + (p[1] - cy) * z + oy)

    def poly(pts, fill=None, outline=None, width=2):
        pp = [T(p) for p in pts]
        if fill is not None:
            d.polygon(pp, fill=fill)
        if outline is not None:
            d.line(pp + [pp[0]], fill=outline, width=max(1, int(width)))

    def seg(p0, p1, fill, width=2):
        d.line([T(p0), T(p1)], fill=fill, width=max(1, int(width)))

    # ------------------------------------------------ 桌面（斜俯视的平面）
    # 远边 0.44H、近边跑到画面外 → 一整块桌面（后沿是一条横线，不是浮在中间的一块板）。
    # 远近宽差只留 0.48W→0.74W：差太多会被读成"被拉长的梯形"，反而假。
    y0, y1 = 0.340 * H, 1.045 * H
    h0, h1 = 0.480 * W, 0.740 * W

    def dp(u, v):
        """桌面坐标 (u 横, v 深；0 = 远边) → 屏幕点。远边窄、近边宽 = 有俯视角。"""
        yy = y0 + (y1 - y0) * v
        hw = h0 + (h1 - h0) * v
        return (0.5 * W + (u - 0.5) * 2.0 * hw, yy)

    def flat(u0, u1, v0, v1, lift=0.0):
        """桌面上的一块矩形（四角，顺序 左上/右上/右下/左下）抬高 lift 后的屏幕坐标。"""
        pts = [dp(u0, v0), dp(u1, v0), dp(u1, v1), dp(u0, v1)]
        return [(x, y - lift) for x, y in pts]

    NB = 26
    for i in range(NB):
        va, vb = i / NB, (i + 1) / NB
        poly([dp(0, va), dp(1, va), dp(1, vb), dp(0, vb)],
             fill=_mix(DESK_FAR, DESK_NEAR, i / (NB - 1)))
    # 木纹：顺着桌面的横纹（跟远边平行），轻微起伏 —— 没有它桌面就是一块色卡
    for i in range(11):
        vv = 0.030 + i * 0.094 + 0.020 * math.sin(i * 2.3)
        pts = []
        for j in range(25):
            u = j / 24.0
            pts.append(dp(u, vv + math.sin(u * 5.1 + i * 1.7) * 0.010
                          + math.sin(u * 13.0 + i * 0.9) * 0.004))
        pp = [T(p) for p in pts]
        d.line(pp, fill=(26, 18, 12, 96 if i % 2 else 62), width=max(1, int(3.2 * S)))
        pp2 = [T((p[0], p[1] - 3.0 * S)) for p in pts[2:22]]
        d.line(pp2, fill=(255, 214, 150, 26), width=max(1, int(2.4 * S)))
    # 受光不做成一圈光斑（视觉复核把它读成“浮在半空的圆形光晕”）：靠桌面自身的
    # 远近渐变 + 一道从左上往右下压暗的线性渐变表达光向（§2：全画面左上受光）。
    for i in range(10):
        xx0 = -0.15 + i * 0.13
        poly([dp(xx0, 0.02), dp(xx0 + 0.13, 0.02), dp(xx0 + 0.13, 1.0), dp(xx0, 1.0)],
             fill=(0, 0, 0, int(4 + 3.4 * i)))
    # 远边（桌子的后沿）：一条亮线，把平面和背景分开
    # 颜色刻意压成暖白而不是暖金：金色留给硬币，判据才分得开"哪块是钱"
    seg(dp(0, 0), dp(1, 0), (240, 226, 202, 105), 3 * S)
    seg((dp(0, 0)[0], dp(0, 0)[1] + 4 * S), (dp(1, 0)[0], dp(1, 0)[1] + 4 * S),
        (0, 0, 0, 90), 5 * S)
    # 画面最下面一道压暗：桌面往镜头外延伸，别让底边"切"在观众眼前
    for i in range(10):
        va = 0.88 + i * 0.012
        poly([dp(0, va), dp(1, va), dp(1, va + 0.012), dp(0, va + 0.012)],
             fill=(0, 0, 0, 18))

    # ------------------------------------------------ 两个实体的位置与尺寸
    # 左边：票据堆
    SU0, SU1 = 0.170, 0.470
    SV0, SV1 = 0.330, 0.520
    PITCH = PITCH_1080 * S
    BAND = BAND_1080 * PITCH
    PC = dp((SU0 + SU1) / 2.0, (SV0 + SV1) / 2.0)

    # 右边：现金池 —— 一沓硬币（圆柱：顶面 + 柱面渐变 + 底边）
    coinu, coinv = 0.720, 0.425
    CXC, CYC = dp(coinu, coinv)
    R = 0.150 * W                  # 硬币半径
    RY = 0.235 * R                 # 俯视角下的椭圆压扁（RY/R ≈ 0.24，太圆就看不出层厚）
    COIN_T = LAYER_1080 * S        # 一枚硬币的厚度 = 一层
    # 每枚固定的小偏位：一沓手放的硬币不是对齐的圆柱
    COIN_OFF = [(((l * 37) % 7) - 3) * 2.2 * S for l in range(N_LAYERS)]
    COIN_OFFY = [(((l * 53) % 5) - 2) * 1.8 * S for l in range(N_LAYERS)]

    # 每张票的固定"歪法"与角点抖动：下标推出来的，不是随机（确定性的）
    # 歪法收在 ±1.7° / ±5px 以内：再大就不是"一沓纸"而是"一叠滑开的牌"（视觉复核原话）。
    SH_ROT = [((k * 47) % 11 - 5) * 0.34 for k in range(N_SLIPS)]
    SH_OFF = [(((k * 61) % 9) - 4) * 1.25 * S for k in range(N_SLIPS)]
    SH_JIT = [[(((k * 31 + c * 17) % 7) - 3) * 1.05 * S,
               (((k * 13 + c * 29) % 5) - 2) * 0.85 * S]
              for k in range(N_SLIPS) for c in range(4)]
    SH_JIT = [SH_JIT[k * 4:(k + 1) * 4] for k in range(N_SLIPS)]

    def sp(k, a, b, h=0.0, rot=0.0, ox_=0.0, oy_=0.0, corner=None):
        """第 k 张票的票面局部点 (a 沿宽, b 沿深) → 屏幕点（含离桌高度、歪法、飞行位移）。"""
        x, yy = dp(SU0 + (SU1 - SU0) * a, SV0 + (SV1 - SV0) * b)
        yy -= k * PITCH + h
        x, yy = _rotpt(x, yy, PC[0], PC[1] - k * PITCH - h, SH_ROT[k] + rot)
        x, yy = x + SH_OFF[k] + ox_, yy + oy_
        if corner is not None:
            x, yy = x + SH_JIT[k][corner][0], yy + SH_JIT[k][corner][1]
        return (x, yy)

    landed = sum(1 for x in lands if t >= x)
    taken = sum(1 for x in exp if t >= x)

    # ------------------------------------------------ 接地投影（§3）
    shA = Image.new("L", (W, H), 0)          # 大块（现金）：模糊半径 ≥ 实体高 12%
    shB = Image.new("L", (W, H), 0)          # 小块（票据）
    dA, dB = ImageDraw.Draw(shA), ImageDraw.Draw(shB)

    def shp(dd, quad, v, grow=1.08, dx=0.0, dy=0.0):
        """把一块桌面矩形放大 grow 倍、偏移 (dx,dy) 当作影子的形状。"""
        mx = sum(p[0] for p in quad) / 4.0
        my = sum(p[1] for p in quad) / 4.0
        dd.polygon([T((mx + (p[0] - mx) * grow + dx, my + (p[1] - my) * grow + dy))
                    for p in quad], fill=int(max(0, min(255, v))))

    # 现金池落影：硬币柱脚下一层软影（偏移朝右下，与左上受光一致）
    _px, _py = T((CXC + 14 * S, CYC + 8 * S))
    dA.ellipse([_px - R * 1.04 * z, _py - RY * 1.12 * z,
                _px + R * 1.04 * z, _py + RY * 1.12 * z], fill=165)
    shC0 = Image.new("L", (W, H), 0)          # 硬币根部：贴地的一圈（分量最重）
    _px, _py = T((CXC + 3 * S, CYC + 2 * S))
    ImageDraw.Draw(shC0).ellipse(
        [_px - R * 1.02 * z, _py - RY * 1.05 * z,
         _px + R * 1.02 * z, _py + RY * 1.05 * z], fill=195)
    # 票据堆的落影：整堆一层，印在桌面上、位置不动
    shp(dB, flat(SU0, SU1, SV0, SV1), 200, 1.10, 13 * S, 10 * S)

    shA2 = Image.new("L", (W, H), 0)         # 空中那枚的影子：单独一层，模糊更大
    dA2 = ImageDraw.Draw(shA2)

    # 被抬走的硬币在桌面上留下的影：**跟着硬币横向走**（光在左上，影落在右下），
    # 越往上越淡越散。影不跟着走，空中那枚就会被读成"浮着的贴图"（视觉复核原话）。
    for j, te in enumerate(exp):
        kk = (t - te) / LIFT_S
        if 0.0 <= kk <= 1.0:
            e = kk ** 1.3
            idx = N_LAYERS - 1 - j
            _rise = (55 + 1320 * (kk ** 1.35)) * S
            # 影子按"灯在左上"往右下推；但推的量要封顶，否则影子被推出画面，
            # 空中那枚就真的"没有影子"了（实测：偏移 0.34×升高 直接落到画外）
            _px, _py = T((CXC + COIN_OFF[idx] + 0.66 * (210 * S * (kk ** 2.4))
                          + 24 * S + min(0.30 * _rise, 140 * S),
                          CYC + COIN_OFFY[idx] + 10 * S + min(0.06 * _rise, 40 * S)))
            _r = R * (1.0 + 1.05 * e)
            dA2.ellipse([_px - _r * z, _py - RY * 1.45 * z,
                         _px + _r * z, _py + RY * 1.45 * z],
                        fill=int(95 * (1.0 - 0.45 * e)))

    shC = Image.new("L", (W, H), 0)          # 贴地的那一道（影子贴着物件根部）
    dC = ImageDraw.Draw(shC)
    shp(dC, flat(SU0, SU1, SV0, SV1), 150, 1.02, 5 * S, 4 * S)
    shC = Image.composite(shC0, shC, shC0)
    for mask, blur in ((shA, 26 * S), (shA2, 60 * S), (shB, 13 * S), (shC, 4 * S)):
        m = mask.filter(ImageFilter.GaussianBlur(radius=max(1.0, blur)))
        im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, m)
    d = ImageDraw.Draw(im, "RGBA")

    # ------------------------------------------------ 左：票据一张张叠上去
    def draw_sheet(k, h_extra=0.0, rot=0.0, ox_=0.0, oy_=0.0, alpha=255):
        face = [sp(k, a, b, h_extra, rot, ox_, oy_, corner=c)
                for c, (a, b) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1)))]
        # 三面律（§2）：这张纸的**整块底边**往下挤出 BAND 高的一条侧壁 ——
        # 左、前、右三段都要画，只画前侧会被看成"纸下面贴了一块梯形"（视觉复核原话）。
        # 光从左上：左侧壁中亮、前侧壁中调、右侧壁最暗。
        def drop(p0, p1, h):
            return [p0, p1, (p1[0], p1[1] + h), (p0[0], p0[1] + h)]

        poly(drop(face[0], face[3], BAND), fill=_mix(PAPER_EDGE, (208, 197, 176), 0.45) + (alpha,))
        poly(drop(face[1], face[2], BAND), fill=_mix(PAPER_EDGE, (96, 86, 71), 0.55) + (alpha,))
        poly(drop(face[3], face[2], BAND), fill=PAPER_EDGE + (alpha,))
        paper = PAPER if k % 2 == 0 else PAPER_ALT
        poly(face, fill=paper + (alpha,))
        # 顶面的受光：靠后沿亮一档、靠前沿压一档（受光在左上，§2）
        poly([face[0], face[1],
              ((face[1][0] + face[2][0]) / 2, (face[1][1] + face[2][1]) / 2),
              ((face[0][0] + face[3][0]) / 2, (face[0][1] + face[3][1]) / 2)],
             fill=(255, 248, 228, int(alpha * 0.16)))
        poly([((face[0][0] + face[3][0]) / 2, (face[0][1] + face[3][1]) / 2),
              ((face[1][0] + face[2][0]) / 2, (face[1][1] + face[2][1]) / 2),
              face[2], face[3]], fill=(60, 46, 32, int(alpha * 0.13)))
        # 纸缝：两张纸靠**影子**分家，不靠描边 —— 上面那张压着下面那张，
        # 缝口三层递减的暗线就是这条影子（用小 blur 会每帧多两次卷积，够用就行）
        for j, a_ in enumerate((0.62, 0.34, 0.16)):
            off = 1.6 * S * (j + 1)
            seg((face[3][0], face[3][1] + off), (face[2][0], face[2][1] + off),
                PAPER_SEAM + (int(alpha * a_),), max(1, 2.2 * S))
        seg(face[1], face[2], PAPER_SEAM + (int(alpha * 0.5),), max(1, 2 * S))
        # 票面：几道印刷横线 + 一枚印章块（不写字，只给"这是单据"的形状）
        for j, b in enumerate((0.30, 0.46, 0.62)):
            p0 = sp(k, 0.10, b, h_extra, rot, ox_, oy_)
            p1 = sp(k, 0.66 - 0.10 * j, b, h_extra, rot, ox_, oy_)
            seg(p0, p1, PAPER_INK + (int(alpha * 0.6),), max(1, 2 * S))
        stamp = [sp(k, a, b, h_extra, rot, ox_, oy_)
                 for a, b in ((0.68, 0.64), (0.90, 0.64), (0.90, 0.90), (0.68, 0.90))]
        poly(stamp, fill=STAMP + (alpha,))
        # 上棱高光（§2）：一道就够，否则顶面和桌面糊成一片
        seg(face[0], face[1], (255, 250, 232, int(alpha * 0.8)), max(1, 2.4 * S))

    for k in range(landed):
        s = (t - lands[k]) / SETTLE_S
        press = 2.6 * S * math.sin(math.pi * s) if 0 <= s <= 1.0 else 0.0
        draw_sheet(k, h_extra=-press)

    # ------------------------------------------------ 右：一沓硬币，一枚枚被拿走
    # 圆柱写法（§2）：底边暗圈 + 柱面横向渐变（左上受光）+ 齿边细竖纹 + 顶面 + 内圈 + 上棱高光。
    # "少一层"不是把颜色变淡，是**真的少一枚**，整沓矮一截，枚数一眼数得出来。
    def ell(cxx, cyy, rx, ry_, fill=None, outline=None, width=2):
        p = T((cxx, cyy))
        box = [p[0] - rx * z, p[1] - ry_ * z, p[0] + rx * z, p[1] + ry_ * z]
        if fill is not None:
            d.ellipse(box, fill=fill)
        if outline is not None:
            d.ellipse(box, outline=outline, width=max(1, int(width)))

    def draw_coin(cxx, cyy, ry_, h, face, alpha=255):
        """画一枚硬币：`cyy` 是它在桌面上的中心 y，`h` 是这枚**顶面**离桌面的高度。"""
        y_top = cyy - h
        y_bot = y_top + COIN_T
        ell(cxx, y_bot, R, ry_, fill=CASH_EDGE + (alpha,))       # 底边（最暗的一圈）
        n = 16
        for j in range(n):                                       # 柱面：横向渐变
            u0, u1 = -1 + 2 * j / n, -1 + 2 * (j + 1) / n
            uu = (u0 + u1) / 2
            kk = min(1.0, abs(uu + 0.42))
            col = _mix(CASH_RIM_HI, CASH_RIM_DARK, kk ** 0.8)
            poly([(cxx + u0 * R, y_top), (cxx + u1 * R, y_top),
                  (cxx + u1 * R, y_bot + 1.5 * S), (cxx + u0 * R, y_bot + 1.5 * S)],
                 fill=col + (alpha,))
        for j in range(-11, 12):                                 # 齿边（滚花：硬币侧的细竖纹）
            xx = cxx + j * (R / 11.5)
            seg((xx, y_top + 1.5 * S), (xx, y_bot - 0.5 * S),
                (30, 21, 10, int(alpha * 0.55)), max(1, int(2.0 * S)))
        # 最下沿一圈压暗：硬币压在桌上，接触处比柱面暗（接地，§3）——
        # 做法是在柱面底端画一条**贴合椭圆**的暗带，不是另画一道弧（那道弧会被读成
        # "融化的影子"，视觉复核原话）。
        n2 = 16
        for j in range(n2):
            u0, u1 = -1 + 2 * j / n2, -1 + 2 * (j + 1) / n2
            poly([(cxx + u0 * R, y_bot - 4 * S), (cxx + u1 * R, y_bot - 4 * S),
                  (cxx + u1 * R, y_bot + 1.0 * S), (cxx + u0 * R, y_bot + 1.0 * S)],
                 fill=(26, 18, 8, int(alpha * 0.55)))
        ell(cxx, y_top, R, ry_, fill=face + (alpha,))            # 顶面
        ell(cxx, y_top, R * 0.80, ry_ * 0.80,
            outline=(96, 72, 36, int(alpha * 0.45)), width=max(1, 2.4 * S))
        ell(cxx, y_top, R * 0.74, ry_ * 0.74,
            outline=(255, 248, 220, int(alpha * 0.62)), width=max(1, 2 * S))
        ell(cxx, y_top, R * 0.44, ry_ * 0.44,
            outline=(120, 92, 48, int(alpha * 0.40)), width=max(1, 2 * S))
        # 上棱高光：顶面靠后沿的一小截弧（受光在左上）
        p = T((cxx, y_top))
        d.arc([int(p[0] - R * 0.99 * z), int(p[1] - ry_ * 0.99 * z),
               int(p[0] + R * 0.99 * z), int(p[1] + ry_ * 0.99 * z)],
              200, 340, fill=(255, 252, 236, int(alpha * 0.55)), width=max(1, int(2 * S)))

    n_stack = N_LAYERS - taken
    for l in range(n_stack):
        face = CASH_FACE if l == n_stack - 1 else _mix(CASH_FACE2, CASH_RIM, 0.25)
        draw_coin(CXC + COIN_OFF[l], CYC + COIN_OFFY[l], RY, (l + 1) * COIN_T, face)

    # 刚被抬走的那一枚在整沓顶面留下的压痕暗影：让"少了一枚"这件事看得见
    for j, te in enumerate(exp):
        kk = (t - te) / LIFT_S
        if 0.0 <= kk < 0.55:
            a = int(70 * (1.0 - kk / 0.55))
            p0 = T((CXC + COIN_OFF[N_LAYERS - 1 - j], CYC + COIN_OFFY[N_LAYERS - 1 - j]
                    - n_stack * COIN_T))
            d.ellipse([p0[0] - R * 0.92 * z, p0[1] - RY * 0.92 * z,
                       p0[0] + R * 0.92 * z, p0[1] + RY * 0.92 * z],
                      fill=(30, 20, 8, a))

    # 正在被抬走的那一枚：抬起 → 甩出画面（不是渐隐，是**离场**）
    for j, te in enumerate(exp):
        kk = (t - te) / LIFT_S
        if not (0.0 <= kk <= 1.0):
            continue
        # 先直着抬起来（前 1/3 几乎只有竖直位移），后半段才甩出去：
        # 一上来就往右飘，空中那枚会被读成"浮在旁边的多余硬币"（视觉复核原话）。
        e = kk ** 1.35
        rise = (55 + 1320 * e) * S
        dx = 210 * S * (kk ** 2.4)          # 横向只挪一点：先"抬起"，出画靠竖直位移

        idx = N_LAYERS - 1 - j
        draw_coin(CXC + COIN_OFF[idx] + dx, CYC + COIN_OFFY[idx],
                  RY, (idx + 1) * COIN_T + rise, CASH_FACE)
    # ------------------------------------------------ 正在下落的那一张票
    for i in range(N_SLIPS):
        if not (starts[i] <= t < lands[i] + SETTLE_S):
            continue
        p = min(1.0, max(0.0, (t - starts[i]) / FALL_S))
        rise = 0.90 * H * (1.0 - p) ** 1.8
        drift = 30 * S * (1.0 - p)
        rot = 7.5 * math.sin(p * 6.3) * (1.0 - p * 0.45)
        # 接触阴影（§3）：**影子跟着这张纸的形状走**（椭圆会被读成"打了一圈聚光灯"），
        # 越接近桌面越实、越小、中心越贴住落点；空中时散开、淡。
        lx0, ly0 = sp(i, 0.5, 0.5)
        foot = [sp(i, a, b, corner=c)
                for c, (a, b) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1)))]
        mx = sum(q[0] for q in foot) / 4.0
        my = sum(q[1] for q in foot) / 4.0
        grow = 1.06 + 0.16 * (1.0 - p)
        sh = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(sh)
        # 高处那张纸的影子必须很淡很散（它离桌面还远）；只有快贴上时才变实 ——
        # 一上来就给 30% 的暗，整座纸堆会被压成灰的（第一版就是，纸都不像纸了）。
        sd.polygon([T((mx + (q[0] - mx) * grow + (6 + 10 * (1 - p)) * S,
                       my + (q[1] - my) * grow + 3 * S)) for q in foot],
                   fill=int(22 + 205 * (p ** 1.8)))
        m = sh.filter(ImageFilter.GaussianBlur(radius=max(1.0, (16 - 10 * p) * S)))
        im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, m)
        d = ImageDraw.Draw(im, "RGBA")
        draw_sheet(i, h_extra=rise, rot=rot, ox_=drift)
        # 落定的一瞬：桌面上溅起一点纸屑（接触的物理反馈）
        if p >= 1.0:
            s = (t - lands[i]) / SETTLE_S
            for q in range(5):
                ang = -1.2 + q * 0.6
                rr = (52 * S) * s ** 0.55
                px2, py2 = T((lx0 + math.cos(ang) * rr,
                              ly0 + 6 * S + math.sin(ang) * rr * 0.40))
                d.ellipse([px2 - 2.2 * S, py2 - 2.2 * S, px2 + 2.2 * S, py2 + 2.2 * S],
                          fill=(234, 224, 202, int(130 * (1.0 - s))))

    # ------------------------------------------------ 空气里的尘（连续微动）
    for i in range(7):
        fx = 0.10 + 0.80 * (((i * 7919 + 13) % 997) / 997.0)
        ph = ((i * 6151 + 7) % 991) / 991.0
        fy = 0.12 + ((t * 0.011 + ph) % 1.0) * 0.62
        r = (1.8 + (i % 3) * 0.9) * S
        pxy = T((fx * W + math.sin(t * 0.7 + i) * 5 * S, fy * H))
        d.ellipse([pxy[0] - r * z, pxy[1] - r * z, pxy[0] + r * z, pxy[1] + r * z],
                  fill=(255, 240, 205, 26 + 8 * (i % 3)))
    return im


def scene(t: float, W: int, H: int, dur: float) -> Image.Image:
    """纯 t 驱动的一帧：同一秒永远同一帧（超采样 2× 后缩回来，边才不抖）。"""
    im = _paint(float(t), int(W) * SS, int(H) * SS, float(dur))
    return im.resize((int(W), int(H)), Image.LANCZOS)
