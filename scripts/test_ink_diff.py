#!/usr/bin/env python3
"""T-044 合同单：大字墨迹新口径（字形掩膜 × 帧差）的六条断言。

判据要证明的不是「能量出一个数」，而是这四件事同时成立：
  A 真卡在场 → 绿（不透明 accent 字压在会动的背景上）
  B 把卡抹掉 → 红（同一位置、同一掩膜，没有字）
  C 暖底移动镜头、没人写字 → 零误报（这是 T-041 挂出这一单的原始病灶）
  D 半透明淡黄字 → 能量出，alpha 估在 0.3–0.9
  E 深色字压暖底 → 能量出（颜色不在 accent 连线上时 alpha 如实报「不适用」）
  F 字形框预测与实测 bbox 偏差在定值内

合成样本用**与产线同一份** `verify_film.ink_diff_stats()` / `card_layout.ink_mask()`，
不另写一套量法；真片能取到就补跑真片（取不到就明确 SKIP，不静默当通过）。

    python3 scripts/test_ink_diff.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))

import card_layout          # noqa: E402
import verify_film as vf    # noqa: E402

W, H = 540, 960
FPS = 20
DUR = 3.0
ACCENT = (246, 235, 52)     # P1-warm 的 accent（与包同一份）
STYLE = yaml.safe_load((ROOT / "packs" / "P1-warm.yaml").read_text())
TEXT = "成本贵在哪"
TOP_H = 0.30


def _background(t: float, blob: bool = False) -> np.ndarray:
    """暖底 + 会动的纹理。纹理平移制造 control 区真实的帧间差。"""
    yy, xx = np.mgrid[0:H, 0:W].astype("float32")
    shift = 3.0 * t
    base = 92.0 + 46.0 * (xx / W) + 34.0 * (yy / H)
    tex = 18.0 * np.sin((xx * 0.9 + yy * 0.35 - shift) / 11.0)
    tex += 11.0 * np.cos((yy * 1.1 - xx * 0.2 - shift * 0.7) / 17.0)
    img = base + tex
    if blob:
        # 一根亮条横向扫过字形带：只压 ink、control 区影响小 —— 负样本 C 的假信号来源。
        cx = int((t / DUR) * (W + 160) - 80)
        band = (np.abs(xx - cx) < 22) & (np.abs(yy - (TOP_H * H + 90)) < 150)
        img = img + band * 105.0
    # 暖底色：三通道按暖比铺开（判 alpha 需要真 RGB，不是灰度复制）
    return np.stack([img, img * 0.86, img * 0.68], axis=2)


def _burn(img: np.ndarray, color, alpha: float) -> np.ndarray:
    mask, _box = card_layout.ink_mask(TEXT, STYLE, W, H, TOP_H)
    out = img.copy()
    c = np.array(color, dtype="float32")
    out[mask] = alpha * c + (1.0 - alpha) * out[mask]
    return out


def _make_clip(path: Path, mode: str) -> None:
    """mode: opaque / erased / blob / half / dark"""
    n = int(round(DUR * FPS))
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         "-pix_fmt", "yuv420p", str(path)],
        stdin=subprocess.PIPE)
    for i in range(n):
        t = i / FPS
        img = _background(t, blob=(mode == "blob"))
        inside = 1.0 <= t <= 2.0
        if inside and mode == "opaque":
            img = _burn(img, ACCENT, 1.0)
        elif inside and mode == "half":
            img = _burn(img, (242, 233, 143), 0.5)   # 旧片里那种淡黄 #F2E98F
        elif inside and mode == "dark":
            img = _burn(img, (126, 54, 34), 1.0)
        p.stdin.write(np.clip(img, 0, 255).astype("uint8").tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"合成片编码失败：{path}")


def _card(start: float, end: float) -> dict:
    return {"kind": "keyword", "text": TEXT, "start": start, "end": end,
            "kw_top_h": TOP_H}


def _stats(film: Path, card: dict) -> dict:
    return vf.ink_diff_stats(film, card, STYLE, W, H, DUR, [card], cuts=[])


def _expect(name: str, cond: bool, detail: str, fails: list[str]) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name} — {detail}")
    if not cond:
        fails.append(name)


def synthetic(tmp: Path) -> list[str]:
    fails: list[str] = []
    print("合成样本（540×960 · 暖底平移纹理 · 字在 1.0–2.0s）")
    clips = {}
    for mode in ("opaque", "erased", "blob", "half", "dark"):
        fp = tmp / f"{mode}.mp4"
        _make_clip(fp, mode)
        clips[mode] = fp

    # A 真卡在场：不透明 accent 字，背景在动
    a = _stats(clips["opaque"], _card(1.0, 2.0))
    _expect("A 真卡在场判绿", a.get("level") == "green",
            f"level={a.get('level')} R={a.get('ratio'):.2f} inkΔ={a.get('ink_delta'):.1f}",
            fails)

    # B 把卡抹掉：同一位置、同一掩膜，窗口里没有字
    b = _stats(clips["erased"], _card(0.2, 0.6))
    _expect("B 抹掉卡同帧判红", b.get("level") == "red",
            f"level={b.get('level')} R={b.get('ratio'):.2f} inkΔ={b.get('ink_delta'):.1f}",
            fails)

    # C 暖底移动镜头、无人为字：亮条从字形带扫过 → 必须零误报
    c = _stats(clips["blob"], _card(0.2, 0.6))
    _expect("C 暖底移动镜头零误报", c.get("level") == "red",
            f"level={c.get('level')} R={c.get('ratio'):.2f} "
            f"occ={c.get('occ_ratio') if c.get('occ_ratio') is None else round(c['occ_ratio'], 2)}",
            fails)

    # D 半透明淡黄：能量出，alpha 估在 0.3–0.9
    d = _stats(clips["half"], _card(1.0, 2.0))
    ok_d = (d.get("level") == "green" and d.get("alpha") is not None
            and 0.3 <= d["alpha"] <= 0.9)
    _expect("D 半透明淡黄卡能量出", ok_d,
            f"level={d.get('level')} alpha={None if d.get('alpha') is None else round(d['alpha'], 2)} "
            f"R={d.get('ratio'):.2f} occ={d.get('occ_ratio') if d.get('occ_ratio') is None else round(d['occ_ratio'], 2)}",
            fails)

    # E 深色字压暖底：字不在 accent 连线上，但帧差能量必须够
    e = _stats(clips["dark"], _card(1.0, 2.0))
    _expect("E 深色字暖底能量出", e.get("level") == "green",
            f"level={e.get('level')} R={e.get('ratio'):.2f} inkΔ={e.get('ink_delta'):.1f} "
            f"color={e.get('color')} alpha={e.get('alpha')}",
            fails)

    # F 字形框预测 vs 实测 bbox
    mask, pred = card_layout.ink_mask(TEXT, STYLE, W, H, TOP_H)
    frame = _burn(_background(1.5), ACCENT, 1.0)
    hit = np.abs(frame - np.array(ACCENT, dtype="float32")).max(axis=2) < 12
    ys, xs = np.where(hit)
    got = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min()))
    px = pred["px"]
    dx0, dy0 = abs(got[0] - px[0]), abs(got[1] - px[1])
    dx1, dy1 = abs((got[0] + got[2]) - (px[0] + px[2])), abs((got[1] + got[3]) - (px[1] + px[3]))
    _expect("F 字形框预测偏差 ≤6px", max(dx0, dy0, dx1, dy1) <= 6,
            f"预测 {px} / 实测 {list(got)} → 边差 {dx0},{dy0},{dx1},{dy1}px", fails)
    return fails


def real_films(tmp: Path) -> list[str]:
    """真片补跑：有素材就跑，没有就 SKIP（不许静默当通过）。"""
    fails: list[str] = []
    proj79 = Path("/Users/jerry/Downloads/Aiworks/CutSkills-wsT047/"
                  "references/packtest-2026-11-08/proj79")
    naming = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-naming-01")
    ground = Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-ground-01")
    if not (proj79 / "out" / "film-hf.mp4").exists():
        print("  SKIP 真片补跑：proj79 新片不在本机（wsT047 工作树）")
        return fails
    film = proj79 / "out" / "film-hf.mp4"
    cards = json.loads((proj79 / "out" / "cards.json").read_text())["cards"]
    fin = vf.load_timeline_or_edl(proj79)
    cuts = vf.film_cut_times(fin, proj79)
    pr = vf.probe(film)
    dur = float(pr["format"]["duration"])
    w = int(pr["streams"][0]["width"])
    h = int(pr["streams"][0]["height"])
    cost = [c for c in cards if c.get("text") == "成本贵在哪"][0]

    r = vf.ink_diff_stats(film, cost, STYLE, w, h, dur, cards, cuts=cuts)
    _expect("R1 新 proj79「成本贵在哪」判绿", r.get("level") == "green",
            f"R={r.get('ratio'):.2f} inkΔ={r.get('ink_delta'):.1f} "
            f"color={r.get('color')} alpha={round(r['alpha'], 2) if r.get('alpha') else None}",
            fails)

    # R2 稳态 alpha：现片这张卡是**不透**的暖黄（11-13 重渲），alpha 反混合要量得出来且不撒谎。
    # 原先这里断言「入场淡入帧 alpha∈0.3–0.9」，前提就不成立：0.48 那个 alpha 只存在于单帧，
    # 10fps 采样 + 0.1s 基线间距量到的是稳态 0.97；与其硬凑一个区间，不如断真话。
    steady = dict(cost, start=cost["start"] + 0.4, end=cost["start"] + 1.0)
    rf = vf.ink_diff_stats(film, steady, STYLE, w, h, dur, cards, cuts=cuts)
    _expect("R2 真片稳态 alpha 量得出来且接近 1", rf.get("alpha") is not None and rf["alpha"] >= 0.9,
            f"alpha={round(rf['alpha'], 2) if rf.get('alpha') is not None else None} "
            f"color={rf.get('color')} R={rf.get('ratio'):.2f}", fails)

    if (ground / "out" / "film-hf.mp4").exists():
        gf = ground / "out" / "film-hf.mp4"
        gcards = json.loads((ground / "out" / "cards.json").read_text())["cards"]
        gfin = vf.load_timeline_or_edl(ground)
        gcuts = vf.film_cut_times(gfin, ground)
        gpr = vf.probe(gf)
        gd = float(gpr["format"]["duration"])
        gw = int(gpr["streams"][0]["width"])
        gh = int(gpr["streams"][0]["height"])
        neg = {"kind": "behind", "text": "售楼部 · 酒店", "start": 29.5,
               "end": 29.8, "kw_top_h": 0.055}
        g = vf.ink_diff_stats(gf, neg, STYLE, gw, gh, gd, gcards, cuts=gcuts)
        _expect("R3 ground-01 29.6s 无人为字零误报", g.get("level") == "red",
                f"R={g.get('ratio'):.2f} inkΔ={g.get('ink_delta'):.1f} "
                f"occ={g.get('occ_ratio') if g.get('occ_ratio') is None else round(g['occ_ratio'], 2)}",
                fails)
    else:
        print("  SKIP R3：ground-01 不在本机")

    if (naming / "out" / "film-hf.mp4").exists():
        nf = naming / "out" / "film-hf.mp4"
        ncards = json.loads((naming / "out" / "cards.json").read_text())["cards"]
        npr = vf.probe(nf)
        nd = float(npr["format"]["duration"])
        nw = int(npr["streams"][0]["width"])
        nh = int(npr["streams"][0]["height"])
        qi = [c for c in ncards if c.get("text") == "柒造"][0]
        n = vf.ink_diff_stats(nf, qi, STYLE, nw, nh, nd, ncards, cuts=[])
        _expect("R4 naming-01「柒造」深色字暖底判绿", n.get("level") == "green",
                f"R={n.get('ratio'):.2f} inkΔ={n.get('ink_delta'):.1f} color={n.get('color')}",
                fails)
    else:
        print("  SKIP R4：qizao-naming-01 不在本机")
    return fails


def main() -> int:
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="t044_ink_") as td:
        tmp = Path(td)
        fails += synthetic(tmp)
        print("真片补跑（有素材才跑）")
        fails += real_films(tmp)
    print()
    if fails:
        print(f"❌ {len(fails)} 条失败：{'、'.join(fails)}")
        return 1
    print("✅ 大字墨迹新口径 6 条合成断言全过（真片补跑见上）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
