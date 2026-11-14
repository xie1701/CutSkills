#!/usr/bin/env python3
"""first_frame 的正负样本：判据必须能拦住「空首帧」，又不能误会大字卡。

五组，全部真造像素/真解码，不 mock verdict：

  ① 单调性   全黑/纯色的边缘密度≈0、熵≈0；棋盘格的边缘密度远高于纯色；
             颜色级数越多熵越高（纯色 < 大字卡 < 两色块 < 噪声）
  ② 正负样本 负：纯黑 mp4、纯色底板图 → 🔴；正：大字卡、真片口播首帧 → 🟢
  ③ 安全区口径 同一张图「整帧算」与「只算安全区」读数必须不同（证明遮挡带真被排除）
  ④ 真成片 smoke 拿一支真项目量第 0 帧，读数要落在人工看帧能认可的范围内
  ⑤ 判不了   文件坏 / 取不到帧 → 回「判不了」，不许静默判绿

跑法：python3 scripts/test_first_frame.py   （exit 0 = 全过）
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import first_frame as FF                                   # noqa: E402

W, H = 540, 960
# 真成片的候选位置（本机实测：8 支全在这两处）。没有也不失败，④ 会明说 SKIP。
SAMPLE_ROOTS = [
    Path("~/Downloads/ouyangTestCut/_cutskills/projects").expanduser(),
    Path("~/Downloads/柒造Video/素材/_cutskills/projects").expanduser(),
]
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    str(HERE.parent / "assets" / "fonts" / "NotoSansSC-Regular.ttf"),
]

bad = 0


def ok(flag: bool, label: str, detail: str = "") -> None:
    global bad
    if not flag:
        bad += 1
    print(f"  {'✅' if flag else '🔴'} {label}" + (f"：{detail}" if detail else ""))


def note(label: str) -> None:
    print(f"  ⚠️  {label}")


def _pil():
    """PIL 在 $CUTSKILLS_HOME/py（deps.json），不在默认 sys.path 上。"""
    try:
        from PIL import Image, ImageDraw, ImageFont     # noqa: PLC0415
        return Image, ImageDraw, ImageFont
    except ImportError:
        return None


def _font(size: int):
    PIL = _pil()
    if not PIL:
        return None
    _, _, ImageFont = PIL
    for p in FONT_CANDIDATES:
        f = Path(p).expanduser()
        if f.exists():
            try:
                return ImageFont.truetype(str(f), size)
            except OSError:
                continue
    return None


def build_frames(td: Path) -> dict:
    """造像素：纯色、纯黑、棋盘格、两色块、噪声、大字卡、遮挡带样本 + 两支 mp4。"""
    PIL = _pil()
    if not PIL:
        raise SystemExit("🔴 没有 pillow（$CUTSKILLS_HOME/py）：本测试需要它造样本")
    Image, ImageDraw, _ = PIL
    import numpy as np

    out = {}
    Image.new("RGB", (W, H), (18, 26, 32)).save(td / "solid.png")
    Image.new("RGB", (W, H), (0, 0, 0)).save(td / "black.png")

    im = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for y in range(0, H, 8):
        for x in range(0, W, 8):
            if ((x // 8) + (y // 8)) % 2 == 0:
                d.rectangle([x, y, x + 7, y + 7], fill=(255, 255, 255))
    im.save(td / "checker.png")

    im = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(im).rectangle([0, 0, W // 2, H], fill=(255, 255, 255))
    im.save(td / "two_tone.png")

    rng = np.random.default_rng(7)
    Image.fromarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8)).save(td / "noise.png")

    # 大字卡：渲染器里 keyword 卡的形状——纯色底 + 巨字 + 一根色条
    font = _font(150)
    for name, bg, fg, acc in (("card_dark", (20, 16, 14), (245, 243, 238), (196, 255, 0)),
                              ("card_light", (242, 239, 230), (24, 20, 18), (220, 60, 40))):
        im = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(im)
        if font:
            d.text((48, 380), "三百万", font=font, fill=fg)
        else:                                   # 没字体就用笔画块近似「大字」的墨迹量
            for k in range(3):
                x = 48 + k * 160
                d.rectangle([x, 380, x + 120, 520], fill=fg)
                d.rectangle([x + 12, 420, x + 108, 500], fill=bg)
        d.rectangle([48, 560, 48 + 240, 578], fill=acc)
        im.save(td / f"{name}.png")

    # 空底板：一块平底 + 两个小字（真实的「没上主体就开场」）
    im = Image.new("RGB", (W, H), (24, 22, 20))
    d = ImageDraw.Draw(im)
    small = _font(26)
    if small:
        d.text((40, 150), "口播", font=small, fill=(230, 228, 220))
    else:
        d.rectangle([40, 150, 90, 176], fill=(230, 228, 220))
    im.save(td / "sparse.png")

    # 遮挡带样本：安全区里是空的，顶部/底部/右侧三块遮挡带全是噪声。
    # 整帧算 → 边缘密度很高；只算安全区 → 0。用来证明遮挡带真的被排除了。
    a = np.zeros((H, W, 3), dtype=np.uint8)
    a[:, :] = (240, 238, 232)
    box, _ = FF._safe_box(W, H)
    x0, y0, x1, y1 = box
    a[0:y0, :, :] = rng.integers(0, 256, (max(1, y0), W, 3), dtype=np.uint8)
    a[y1:, :, :] = rng.integers(0, 256, (H - y1, W, 3), dtype=np.uint8)
    a[:, x1:, :] = rng.integers(0, 256, (H, W - x1, 3), dtype=np.uint8)
    Image.fromarray(a).save(td / "bands.png")

    # 真 mp4：纯黑 0.5s。负样本走完整解码链路，不是「拿张图冒充」。
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=black:s={W}x{H}:d=0.5:r=30", str(td / "black.mp4")],
                   check=True)
    (td / "broken.mp4").write_bytes(b"this is not a video" * 200)
    out.update({k: td / f"{k}.png" for k in
                ("solid", "black", "checker", "two_tone", "noise", "sparse",
                 "card_dark", "card_light", "bands")})
    out["black_mp4"] = td / "black.mp4"
    out["broken"] = td / "broken.mp4"
    return out


def sample_film() -> Path | None:
    """本机真实成片：环境变量优先，否则扫两个已知项目根。"""
    import os
    env = os.environ.get("CUTSKILLS_SAMPLE_FILM")
    if env and Path(env).expanduser().exists():
        return Path(env).expanduser()
    for root in SAMPLE_ROOTS:
        if not root.exists():
            continue
        for f in sorted(root.glob("*/out/film-hf.mp4")):
            return f
    return None


def main() -> int:
    print("first_frame 判据自测")
    with tempfile.TemporaryDirectory(prefix="firstframe-") as tmp:
        td = Path(tmp)
        fr = build_frames(td)

        # ── ① 单调正确性 ────────────────────────────────────────────────
        print("\n① 读数单调性（构造帧）")
        m_solid = FF.measure(fr["solid"])
        m_black = FF.measure(fr["black"])
        m_check = FF.measure(fr["checker"])
        m_two = FF.measure(fr["two_tone"])
        m_noise = FF.measure(fr["noise"])
        m_card = FF.measure(fr["card_dark"])
        ok(m_black["edge_density"] <= 0.001 and m_black["entropy"] <= 0.01,
           f"全黑帧：边缘≈0 · 熵≈0（{m_black['edge_density']:.4f} / {m_black['entropy']:.3f}）")
        ok(m_solid["edge_density"] <= 0.001 and m_solid["entropy"] <= 0.01,
           f"纯色帧：边缘≈0 · 熵≈0（{m_solid['edge_density']:.4f} / {m_solid['entropy']:.3f}）")
        ok(m_check["edge_density"] > 0.5 and
           m_check["edge_density"] > m_solid["edge_density"] + 0.5,
           f"棋盘格边缘密度远高于纯色（{m_check['edge_density']:.3f} vs {m_solid['edge_density']:.3f}）")
        ok(m_two["contrast_p90_p10"] > 200 and m_solid["contrast_p90_p10"] <= 1,
           f"局部对比：两色块 {m_two['contrast_p90_p10']:.0f} ≫ 纯色 {m_solid['contrast_p90_p10']:.0f}")
        ents = [m_solid["entropy"], m_card["entropy"], m_two["entropy"], m_noise["entropy"]]
        ok(all(a < b for a, b in zip(ents, ents[1:])),
           "熵随颜色级数单调上升（纯色 < 大字卡 < 两色块 < 噪声）",
           " / ".join(f"{e:.3f}" for e in ents))
        ok(m_two["salient_ratio"] > 0.2 and m_solid["salient_ratio"] <= 0.01,
           f"显著区域：两色块 {m_two['salient_ratio']:.3f} ≫ 纯色 {m_solid['salient_ratio']:.3f}")

        # ── ② 正负样本（真造的帧，不 mock verdict）────────────────────────
        print("\n② 正负样本判定")
        for key, label in (("black_mp4", "纯黑 mp4 首帧"), ("solid", "单色底板图"),
                           ("sparse", "空底板＋小字")):
            m = FF.measure(fr[key])
            lv, why = FF.verdict(m)
            ok(lv == "red" and "判不了" not in why, f"负样本·{label} → 🔴", why[:56])
        for key, label in (("card_dark", f"正样本·大字卡（深底）"),
                           ("card_light", "正样本·大字卡（浅底）")):
            m = FF.measure(fr[key])
            lv, why = FF.verdict(m)
            ok(lv == "green", f"{label} → 🟢", why[:56])
        film = sample_film()
        if film:
            m = FF.measure(film)
            lv, why = FF.verdict(m)
            ok(lv == "green", f"正样本·真片口播首帧（{film.parent.parent.name}）→ 🟢", why[:56])
        else:
            note("本机没有真成片样本，② 的「口播首帧」这一格 SKIP（④ 同样 SKIP）")

        # ── ③ 安全区口径 ───────────────────────────────────────────────
        print("\n③ 安全区口径：遮挡带必须真被排除")
        safe = FF.measure(fr["bands"], zone="safe")
        full = FF.measure(fr["bands"], zone="full")
        diff = abs(full["edge_density"] - safe["edge_density"])
        ok(safe["safe_box"] != [0, 0, safe["width"], safe["height"]],
           "安全区确实比整帧小", f"{safe['safe_box']} vs {safe['width']}×{safe['height']}")
        ok(full["edge_density"] > 0.2 and safe["edge_density"] < 0.02 and diff > 0.2,
           "同一张图整帧 vs 安全区读数不同（遮挡带里的噪声被排除）",
           f"整帧 {full['edge_density']:.3f} · 安全区 {safe['edge_density']:.3f}")

        # ── ④ 真成片 smoke ─────────────────────────────────────────────
        print("\n④ 真成片 smoke（第 0 帧，不是第 1 秒）")
        if film:
            m = FF.measure(film)
            lv, why = FF.verdict(m)
            print(f"     {film}")
            print(f"     边缘 {m['edge_density'] * 100:.1f}% · 熵 {m['entropy']:.3f} · "
                  f"对比 {m['contrast_p90_p10']:.0f} · 显著 {m['salient_ratio'] * 100:.1f}% · {lv}")
            # 人工看帧：这支首帧是口播中景 + 字幕，四项都该有实打实的量
            ok(lv == "green" and m["edge_density"] > 0.05 and m["entropy"] > 0.45
               and m["contrast_p90_p10"] > 60 and m["salient_ratio"] > 0.2,
               "真片首帧读数落在「有人物、有字幕」的量级", why[:56])
        else:
            note("SKIP：本机没有真成片（④ 的 smoke 没跑）——不是通过，是没测")

        # ── ⑤ 判不了 ───────────────────────────────────────────────────
        print("\n⑤ 取不到帧 / 文件坏 → 判不了，不许判绿")
        for key, label in (("broken", "坏文件"),):
            m = FF.measure(fr[key])
            lv, why = FF.verdict(m)
            ok(m["error"] and lv == "red" and "判不了" in why,
               f"{label} → 判不了（🔴）", (m["error"] or "")[:56])
        m = FF.measure(td / "does-not-exist.mp4")
        lv, why = FF.verdict(m)
        ok(m["error"] and lv == "red" and "判不了" in why,
           "文件不存在 → 判不了（🔴）", (m["error"] or "")[:56])

        r = subprocess.run([sys.executable, str(HERE / "first_frame.py"),
                            "--video", str(fr["broken"])], capture_output=True, text=True)
        ok(r.returncode == 2 and "判不了" in (r.stdout + r.stderr),
           "CLI：坏文件退出码 2 且说「判不了」", f"rc={r.returncode}")
        r = subprocess.run([sys.executable, str(HERE / "first_frame.py"),
                            "--video", str(fr["card_dark"])], capture_output=True, text=True)
        ok(r.returncode == 0, "CLI：大字卡退出码 0", f"rc={r.returncode}")
        r = subprocess.run([sys.executable, str(HERE / "first_frame.py"),
                            "--video", str(fr["black_mp4"])], capture_output=True, text=True)
        ok(r.returncode == 1, "CLI：纯黑首帧退出码 1", f"rc={r.returncode}")

    print("\n结论：" + ("✅ 全过" if bad == 0 else f"🔴 {bad} 条不过"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
