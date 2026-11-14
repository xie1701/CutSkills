#!/usr/bin/env python3
"""T-041（追加）回归：behind / cutout 大字的「取样带 + 基线」缺一条就假红。

真案（`qizao-ground-01` 30.4s，behind 卡「售楼部 · 酒店」29.877–30.877）：

    y 115–204（画面 6%–11%H）有 24 320 个 #F6EB34 像素；
    keyword 的中段带从 28%H 才开始 → 那一带只剩画面里本来就有的几点黄（682 px，0.068%），
    低于 behind 的墨迹下限 0.08% → 判「字没上屏」。带子换成人脑后大字该在的顶部
    (0, 0, W, 0.45H) → 仍在 2.7%–3.2%，字明明在。

带子对了还不够：那 0.068% 的背景黄在一张「字根本没上屏」的帧上照样存在，而 behind 的
下限只有 0.08%——不减掉基线，一张空白帧能擦着过线（本测试 D 就是这条负样本）。所以
判据量的是「有卡帧 − 卡前/卡后同带中位」的**净墨迹**。

给帧按真案几何合成，不依赖素材库在本机；本机有真片时额外对一次真帧（没有就明说跳过）。

用法：python3 scripts/test_behind_band.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image, ImageDraw  # noqa: E402

from verify_film import (KW_FLOOR_BEHIND, KW_FLOOR_KEYWORD,  # noqa: E402
                         color_share, kw_ink_level, text_band)

W, H = 1080, 1920
YELLOW = "#F6EB34"
# 真案几何（抽 30.227s 一帧量出来的）
INK_ROWS = (115, 204)          # 大字墨迹纵向范围
INK_W = 270                    # 每行约 270 px 有墨 → 90 行 ≈ 24 300 px，实测 24 320
SCENE_ROWS = (268, 335)        # 带子里「本来就有的黄」，实测 682 px
SCENE_W = 10
from libs import find_project  # noqa: E402
_p = find_project("qizao-ground-01")
FILM = (_p / "out" / "film-hf.mp4") if _p else Path("/nonexistent/film-hf.mp4")


def frame(with_text: bool = True, scene: bool = True) -> Image.Image:
    """按真案比例造一帧：深底 + 顶部大字墨迹（可抹掉）+ 中段带背景黄（可关）。"""
    im = Image.new("RGB", (W, H), (28, 30, 34))
    d = ImageDraw.Draw(im)
    if with_text:
        x0 = (W - INK_W) // 2
        d.rectangle([x0, INK_ROWS[0], x0 + INK_W, INK_ROWS[1]], fill=YELLOW)
    if scene:
        x1 = (W - SCENE_W) // 2
        # ImageDraw 的右下角是闭区间：写 +SCENE_W 会多一列（680 → 748 px，正好把
        # 噪声抬过 behind 的 0.08% 下限），这里按真案 682 px 对住
        d.rectangle([x1, SCENE_ROWS[0], x1 + SCENE_W - 1, SCENE_ROWS[1]], fill=YELLOW)
    return im


def main() -> int:
    band_kw = text_band("keyword", None, W, H)
    band_behind = text_band("behind", None, W, H)

    # A. keyword 的带子不许动（消 behind 的红，不该连 keyword 的判据一起松）
    assert band_kw == (0, int(H * 0.28), W, int(H * 0.76)), f"keyword 取样带被改了：{band_kw}"
    assert band_behind == (0, 0, W, int(H * 0.45)), f"behind 取样带不对：{band_behind}"

    # B. 旧行为复现：拿中段带量 behind 的字 → 只有背景黄，判红（这就是那条假红）
    old_share = color_share(frame(), YELLOW, band_kw)
    old_level, old_ink = kw_ink_level(old_share, None, "behind")
    assert old_level == "red", f"中段带本该判红，却判 {old_level}（{old_share * 100:.3f}%）"

    # C. 新带子 + 基线 → 字确实在，判绿（先红后绿）
    text_share = color_share(frame(), YELLOW, band_behind)
    noise_share = color_share(frame(with_text=False), YELLOW, band_behind)
    level, ink = kw_ink_level(text_share, noise_share, "behind")
    assert level == "green", (f"behind 带子 + 基线仍判 {level}："
                             f"带内 {text_share * 100:.2f}% / 基线 {noise_share * 100:.2f}%")
    assert ink > KW_FLOOR_BEHIND * 15, f"净墨迹 {ink * 100:.3f}% 离下限太近"

    # D. 负样本：大字抹掉，背景照旧 → 净墨迹 ≈ 0，必须仍判红
    blank_level, blank_ink = kw_ink_level(noise_share, noise_share, "behind")
    assert blank_level == "red", f"大字抹掉了却判 {blank_level}（净 {blank_ink * 100:.3f}%）"
    # 没基线时也不许靠背景擦线过去（真案背景 0.068% 就在 0.08% 下限边上）
    no_base_level, _ = kw_ink_level(noise_share, None, "behind")
    assert no_base_level == "red", f"背景噪声 {noise_share * 100:.3f}% 没基线竟判 {no_base_level}"

    # D2. 带子被画面同色占满时不许靠「减基线」报红：proj79 的 behind「成本贵在哪」
    #     带内 2.55%、卡不在 2.48%（那张卡在画面上，只是渲成半透明淡黄）→ 退回旧口径判绿，
    #     并在读数里写明「净墨迹不可判」
    sat_level, sat_ink = kw_ink_level(0.0255, 0.0248, "behind")
    assert sat_level == "green", f"带子饱和时被误判 {sat_level}（净 {sat_ink * 100:.3f}%）"

    # E. 两字小卡照旧放行：`naming-01` 的 behind「柒造」实测顶部带 0.170%（真人挡住大半）
    small = color_share(Image.new("RGB", (W, H), (28, 30, 34)), YELLOW, band_behind)  # 0%
    assert small == 0.0
    level_small, ink_small = kw_ink_level(0.00170, 0.00020, "behind")
    assert level_small == "green", f"两字小卡被误判 {level_small}（净 {ink_small * 100:.3f}%）"

    # F. keyword 的门槛不许被 behind 的放宽带走
    level_kw, _ = kw_ink_level(0.00170, 0.0, "keyword")
    assert level_kw == "red", "keyword 卡只剩 0.17% 墨迹却判绿——keyword 的下限被带松了"

    # G. cutout 与 behind 同一条带；有 layout.box_px 时以卡自己的框为准
    assert text_band("cutout", None, W, H) == band_behind
    assert text_band("behind", (10, 20, 300, 60), W, H) == (10, 20, 300, 60)
    # T-045 改了这一条：**有卡框就以卡框为准，keyword 也一样**。
    # 原来的断言是「keyword 永远回中段带」——那时大字的 top_h 是全局的（pack 的
    # `emphasis.top_h`），中段带天然罩得住。T-045 给卡加了 `card.top_h`（作者可以把它挪到
    # 头顶避开人脸），同一条断言就变成了假红的来源：dianzan-c mg-02 上沿 0.165H 时
    # 字在 y 326–419，中段带从 537 起，`verify_film` 报「大字烧进画面 10.7s 处花字色
    # 占比 0.00%」，而那一帧实测有 28 800 个 #F6EB34 像素（负样本见 T-045 报告）。
    # 松的那头没变：`box` 为 None 时 keyword 仍回中段带（上一行 band_kw 的断言）。
    assert text_band("keyword", (10, 20, 300, 60), W, H) == (10, 20, 300, 60), \
        "有卡框时 keyword 也该按卡框量（T-045：卡能挪到头顶，固定中段带会量空）"
    assert text_band("keyword", None, W, H) == band_kw, \
        "没有卡框时 keyword 必须回中段带——这条不许松"

    # H. 本机有真片就再对一次真帧（没有就明说跳过，不当通过）
    real = "· 真帧跳过（素材库不在本机）"
    if FILM.exists():
        tmp = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
        png = tmp / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "30.227", "-i", str(FILM),
                        "-frames:v", "1", str(png)], capture_output=True)
        if png.exists():
            rim = Image.open(png).convert("RGB")
            r_old = color_share(rim, YELLOW, band_kw)
            r_new = color_share(rim, YELLOW, band_behind)
            r_lvl, r_ink = kw_ink_level(r_new, r_old, "behind")
            assert r_old < KW_FLOOR_BEHIND, f"真帧中段带量出 {r_old * 100:.3f}%（本该量空）"
            assert r_lvl == "green", f"真帧判 {r_lvl}：顶部带 {r_new * 100:.2f}% / 中段 {r_old * 100:.2f}%"
            real = (f"· 真帧 30.227s：中段带 {r_old * 100:.2f}% 🔴 / 顶部带 {r_new * 100:.2f}% 🟢"
                    f"（净 {r_ink * 100:.2f}%）")
            for p in tmp.iterdir():
                p.unlink()
            tmp.rmdir()
    assert KW_FLOOR_KEYWORD > KW_FLOOR_BEHIND

    print(f"✅ behind 取样带/基线 9/9：keyword 带不变 · 中段带 {old_share * 100:.2f}%🔴 · "
          f"顶部带 {text_share * 100:.2f}% 净 {ink * 100:.2f}%🟢 · 抹掉大字净 {blank_ink * 100:.2f}%🔴"
          f"（无基线 {no_base_level}）· 带子饱和（proj79 2.55/2.48）{sat_level}🟢 · "
          f"两字小卡 0.15%🟢 · keyword 0.17%🔴 {real}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
