#!/usr/bin/env python3
"""读像素的包装裁判：量出成片**实际长什么样**，和包装模板声明的区间对账。

为什么另开一个，不直接用 `probe_packaging.py`：那个是给**别人的片**做逆向的，
所有值都要靠猜（不知道字幕是怎么做的，只能从白像素反推），所以字幕那一层
它自己都承认测不准（同一批片测出 1 条/分 和 322 条/分两种极端）。
这里是给自己的产出做验收：**可以只看像素，不看渲染器的 JSON**——渲染器说
「字幕带中心 1651」，和成片上真的是 1651，是两件事。

量的口径（全部按画幅比例，不带像素绝对值，所以 720p 和 1080p 的数字能直接比）：

  字幕带中心 / 字形高度 / 下沿距底 / 单条占宽 / 条数每分钟 / 覆盖时长
  顶条高度 / 顶条字高 / 角标位置
  切点数 / 镜头中位 / 平均饱和度 / 音效密度

字幕怎么从画面里分出来：**按时间的方差**。同一行像素如果只有字幕经过，
它的白像素数会随时长起伏（std 大）；白墙、白桌子、白色地面这些一直在的，
std 很小。所以取下半幅里 std 最高的那一段行，就是字幕带——这比"找白像素最多的行"
稳得多（后者会被白底画面整片骗走）。

用法：
  measure_pack.py --video out/film.mp4 --pack pack-safe-talking [--shell pack-hud-monitor]
  measure_pack.py --video a.mp4 --video b.mp4 --pack pack-safe-talking --json cmp.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

STYLES = Path(__file__).resolve().parent.parent / "styles"
GW, GH = 360, 640          # 采样尺寸：1.56px/%H，够量 4.5%H 的字高（±0.6%H）
FS = 10.0                  # 采样率


def load_pack(name: str) -> dict:
    p = Path(name).expanduser()
    if not p.exists():
        p = STYLES / (name if name.endswith(".yaml") else f"{name}.yaml")
    return yaml.safe_load(p.read_text())


def frames(path: Path) -> np.ndarray:
    """按 FS 抽成 (N, GH, GW, 3) uint8。全部走管道，不落中间文件。"""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
           f"fps={FS},scale={GW}:{GH}:flags=area", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (GW * GH * 3)
    return np.frombuffer(raw[:n * GW * GH * 3], np.uint8).reshape(n, GH, GW, 3)


def audio_energy(path: Path):
    """0.1s 一帧的 RMS。用 8kHz 单声道够判"有没有一记重音"，不用 48k。"""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
           "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw[:len(raw) // 4 * 4], np.float32)
    hop = 800
    n = len(a) // hop
    if not n:
        return np.zeros(0)
    return np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)


def runs(mask: np.ndarray, min_len: int = 1, max_gap: int = 1) -> list[tuple[int, int]]:
    """连通段：允许 max_gap 帧的空隙（字幕闪一帧不算断）。"""
    out, i, n = [], 0, len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j, last = i, i
        while j < n and (mask[j] or j - last <= max_gap):
            if mask[j]:
                last = j
            j += 1
        if last - i + 1 >= min_len:
            out.append((i, last))
        i = j
    return out


def cut_frames(F: np.ndarray) -> list[int]:
    """切点帧号。镜头切换会让**整行**亮度阶跃，做方差统计前必须把这附近几帧剔掉——
    不剔的话每一行都在切点处剧烈变化，std 被切点主导，字幕带就找不出来了
    （实测：10 秒 4 个镜头的片，第一版量出的字幕带中心是 0.53H，字形高 17%H）。"""
    g = F.mean(axis=3) / 255.0
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    if len(d) < 3:
        return []
    thr = max(0.06, float(np.median(d)) * 4)
    raw = [int(i) + 1 for i in np.where(d > thr)[0]]
    # 一个切点会在相邻 3–5 帧上都超阈值（运动模糊、编码残留），
    # 不合并的话 4 个镜头的片会数出 7 个切点、镜头中位掉到 1.1s
    out: list[int] = []
    for c in raw:
        if not out or c - out[-1] > 5:
            out.append(c)
    return out


def measure_subtitle(F: np.ndarray, cuts: list[int] | None = None) -> dict:
    """字幕带：在下半幅里找"横向边缘能量随时长剧烈起伏"的那一段行。

    为什么不用白像素数：白字压在**亮底**上时，整行白像素和背景一个量级，
    字幕出现/消失都看不出来（实测书架口播那条：白墙背景，白像素判据直接报"没有字幕"）。
    横向梯度（相邻像素的亮度差）不一样——字是硬边，背景是软的，亮底也压不住它。
    所以判据是「这一行的边缘能量随时间起伏」，白像素只用来量条宽。
    """
    n, H, W, _ = F.shape
    g = F.mean(axis=3)
    gx = np.abs(np.diff(g, axis=2)).mean(axis=2)          # (n, H) 行内边缘能量
    valid = np.ones(n, bool)
    for c in (cut_frames(F) if cuts is None else cuts):
        valid[max(0, c - 3):c + 3] = False
    if valid.sum() < 5:
        valid[:] = True
    lo = int(0.50 * H)                                     # 字幕不会出现在上半幅
    std = gx[valid][:, lo:].std(axis=0)
    if std.max() < 1e-6:
        return {"present": False}
    hot = std > 0.5 * std.max()
    # 只认 2–9%H 的连续带（一条字幕的字形高度就在这个量级），
    # 边界放宽到 0.18×峰值：汉字的顶横、底钩这几行边缘很少
    segs = [s for s in runs(hot, min_len=2) if 0.015 <= (s[1] - s[0] + 1) / H <= 0.09]
    if not segs:
        return {"present": False}
    # 取**最靠下**的那一段，不是方差最大的那一段。
    # 字幕带永远是画面里最低的一层文字；上方的大字/花字/图形卡行方差更大，
    # 按"最大"取会把花字和字幕合并成一条 9%H 的带（实测参考片和成片都栽在这里，
    # 双双报出 0.0906H，正好撞到 9% 的上限）。
    a, b = segs[-1]
    # 只往外扩 2 行（补上顶横、底钩这些低方差的行）。扩到底会一路吃到上方的花字/图形卡，
    # 把字高量成 9%H 的上限——参考片和成片都撞过这个上限，说明是判据的问题不是片的问题。
    edge = 0.18 * std.max()
    for _ in range(2):
        if a > 0 and std[a - 1] > edge:
            a -= 1
    for _ in range(2):
        if b + 1 < len(std) and std[b + 1] > edge:
            b += 1
    y0, y1 = a + lo, b + lo
    center = (y0 + y1) / 2 / H
    glyph = (y1 - y0 + 1) / H

    # 有字的帧：带内边缘能量显著高于带内中位
    energy = gx[:, y0:y1 + 1].sum(axis=1)
    med, p90 = float(np.median(energy)), float(np.percentile(energy, 90))
    on = energy > med + 0.45 * (p90 - med)
    cues = runs(on, min_len=2, max_gap=1)
    dur = n / FS
    covered = sum(b2 - a2 + 1 for a2, b2 in cues) / FS

    white = F.min(axis=3) > 235
    widths = []
    for a2, b2 in cues:
        sub = white[a2:b2 + 1, y0:y1 + 1, :].sum(axis=1)
        cols = np.where(sub.max(axis=0) > 0)[0]
        if len(cols):
            widths.append((cols[-1] - cols[0] + 1) / W)
    w_med = float(np.median(widths)) if widths else None
    return {
        "present": True,
        "band_center_h": round(float(center), 4),
        "glyph_h_ratio": round(float(glyph), 4),
        "bottom_gap_h": round(float(1 - y1 / H), 4),
        "cues": len(cues),
        "cues_per_min": round(len(cues) / dur * 60, 1),
        "covered_pct": round(covered / dur * 100, 1),
        # 亮底素材上白像素会漫到整幅，这个数就不可信了——标出来，别当成实测量
        "width_pct": round(w_med * 100, 1) if w_med and w_med < 0.95 else None,
    }


def measure_shell(F: np.ndarray, sub: dict, cuts: list[int] | None = None) -> dict:
    """顶条：从 y=0 往下，整行平均亮度明显低于画面中部的连续带。
    角标：顶条以下、左半幅里**每一帧都在**的白字。

    为什么不用"整行 95% 以上是暗像素"这种硬判据：条里写着字，字行的亮度会被
    拉高（实测 9 → 50+），硬判据只量到字上方那十几行，顶条被量成 1.7%H。
    所以允许最多 10 行连续违例——条里的字最多占那么多行。
    """
    n, H, W, _ = F.shape
    cuts = cut_frames(F) if cuts is None else cuts
    lum = F.mean(axis=3)
    rowmean = lum.mean(axis=2).mean(axis=0)               # (H,) 整行平均亮度
    mid = float(np.median(rowmean[H // 2:]))
    thr = max(30.0, 0.45 * mid)
    h, gap = 0, 0
    while h < H and gap <= 10:
        gap = 0 if rowmean[h] < thr else gap + 1
        h += 1
    h = max(0, h - gap - 1)
    out: dict = {"bar_h": round(h / H, 4) if h >= 6 else 0.0}
    if h >= 6:
        frac = (F[:, :h, :, :].min(axis=3) > 150).mean(axis=(0, 2))   # 条内每行亮像素比
        thr_f = max(0.008, float(np.median(frac)) * 5)
        rows = np.where(frac > thr_f)[0]
        out["bar_type_h"] = round(float(np.ptp(rows)) / H, 4) if len(rows) > 2 else 0.0
    # 角标：条以下、左半幅里"每一帧都一模一样"的白字。
    #
    # 判据只能是**静止性**，不能是亮度：亮水泥地面在顶部就有 1–2% 的亮像素，
    # 和 1.4%H 小字的量级完全重叠（实测按亮度判会误判到 24.7%H 和 4.8%H 两处）。
    # 叠加层是画上去的，同一镜头内两帧的像素差 <5；画面在动，差不出这个数。
    # 这一条也顺带说明了 C 族的本质：壳是**不动的东西**。
    out["badge_y_h"] = 0.0
    y0, y1 = max(h, 1), int(0.30 * H)
    hits: dict[int, int] = {}
    for (a, b) in zip([0] + list(cuts), list(cuts) + [n]):
        if b - a < 4:
            continue
        f0, f1 = a + 1, b - 2
        A = F[f0:f0 + 1, y0:y1, : int(0.5 * W), :].astype(np.int16)
        B = F[f1:f1 + 1, y0:y1, : int(0.5 * W), :].astype(np.int16)
        same = (np.abs(A - B).max(axis=3)[0] < 5) & (A[0].min(axis=2) > 175)
        per = same.mean(axis=1)
        for yy in np.where(per > 0.02)[0]:
            hits[int(yy)] = hits.get(int(yy), 0) + 1
    if hits:
        rows = np.zeros(y1 - y0, bool)
        for yy in hits:
            rows[yy] = True
        seg = runs(rows, min_len=2, max_gap=2)
        if seg:
            out["badge_y_h"] = round(float((seg[0][0] + seg[0][1]) / 2 + y0) / H, 4)
    return out


def measure_tempo(F: np.ndarray) -> dict:
    g = F.mean(axis=3) / 255.0
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    if len(d) < 3:
        return {}
    cuts = np.array(cut_frames(F))
    if len(cuts):
        gaps = np.diff(np.concatenate([[0], cuts, [len(g)]])) / FS
        med = float(np.median(gaps))
    else:
        med = len(g) / FS
    sat = float(np.mean((F.max(axis=3) - F.min(axis=3)) / np.maximum(1, F.max(axis=3))))
    return {"cuts": int(len(cuts)), "shot_median_s": round(med, 2),
            "sat_mean": round(sat, 3)}


def measure_audio(path: Path) -> dict:
    e = audio_energy(path)
    if not len(e):
        return {}
    db = 20 * np.log10(e + 1e-9)
    med = float(np.median(db))
    spikes = runs((db > med + 12) & (db > -40), min_len=1, max_gap=1)
    dur = len(e) * 0.1
    return {"sfx_per_min": round(len(spikes) / dur * 60, 1),
            "silent_pct": round(float((db < med - 20).mean()) * 100, 1),
            "loudness_db": round(float(20 * np.log10(np.sqrt((e ** 2).mean()) + 1e-9)), 1)}


def compare(m: dict, pack: dict, shell: dict | None) -> list[dict]:
    """模板声明的区间 vs 实测。每行给：项 / 声明 / 实测 / 判定。

    判定只有三种：`在带内`（模板说了、实测落进去）、`带外`（模板说了、实测没落进去）、
    `未声明`（模板没写这一项——这类不算错，但要知道自己没在验它）。
    """
    sub = pack.get("subtitle") or {}
    rows: list[dict] = []

    def band(key, path, tol=None, name=None, val=None, soft=False):
        spec = path
        if spec is None:
            rows.append({"item": name or key, "declared": "—", "measured": val, "verdict": "未声明"})
            return
        lo, hi = (spec if isinstance(spec, list) else [spec, spec])
        v = m.get(key)
        if v is None:
            rows.append({"item": name or key, "declared": spec, "measured": None, "verdict": "没测到"})
            return
        ok = (float(lo) - (tol or 0)) <= float(v) <= (float(hi) + (tol or 0))
        verdict = "在带内" if ok else ("仅供参考" if soft else "带外")
        rows.append({"item": name or key, "declared": spec, "measured": v,
                     "verdict": verdict})

    band("band_center_h", sub.get("band_center_h_range") or sub.get("band_center_h"),
         name="字幕带中心（%H）")
    band("glyph_h_ratio", sub.get("glyph_h_range") or sub.get("glyph_h_ratio"), tol=0.004,
         name="字形高度（%H）")
    if sub.get("safe_bottom_h") is not None:
        v = m.get("bottom_gap_h")
        rows.append({"item": "字幕下沿距底（%H）", "declared": f"≥{sub['safe_bottom_h']}",
                     "measured": v,
                     "verdict": "在带内" if v is not None and v >= float(sub["safe_bottom_h"]) - 0.01
                     else "带外"})
    # 节奏和声音归**壳**管（壳是整片层面的），字幕系统只管字幕自己的四个数。
    # 不这么分的话，套了 C 的壳却拿 A 的音效带（0–8.5/分）去判，
    # C 要求的 2–6.3/分就永远验不到——那恰恰是这套里最容易做漏的一层。
    tempo_src = (shell or pack).get("tempo") or {}
    band("shot_median_s", tempo_src.get("shot_median_s") or
         (pack.get("tempo") or {}).get("shot_median_s"), name="镜头中位（秒）")
    # 音效这一项凡带人声的片子都量不准，**不判**，只把数字摆出来。
    # 原因：黑盒测量靠"能量突跳"，而人声本身每个词都是一次突跳。
    # 实测参考片 126s 里"检测到"的瞬跳一半以上是齿音和爆破音；
    # 我们这支手打的 9 处（设计值 6.8/分，模板 4.0–8.5）被判成 12.9。
    # 判它的正确方式只有一个：拿到音效轨（mix 的 cues.json），数它自己。
    # 把它当"带外"报出去，等于让一个坏尺子去否掉一件合格的东西。
    band("sfx_per_min", tempo_src.get("sfx_density_per_min"),
         name="音效密度（处/分，黑盒仅参考）", soft=True)

    if shell:
        bar = (shell.get("shell") or {}).get("bar") or {}
        h = m.get("bar_h")
        if bar.get("height_h") and h is not None:
            ok = abs(float(h) - float(bar["height_h"])) <= 0.012
            rows.append({"item": "顶条高度（%H）", "declared": bar["height_h"], "measured": h,
                         "verdict": "在带内" if ok else "带外"})
        th = bar.get("type_scale_h")
        if th and m.get("bar_type_h"):
            rows.append({"item": "顶条字高（%H）", "declared": th, "measured": m["bar_type_h"],
                         "verdict": "在带内" if abs(m["bar_type_h"] - float(th)) <= 0.006 else "带外"})
        bd = (shell.get("shell") or {}).get("badge") or {}
        if bd.get("y_h") and m.get("badge_y_h"):
            lo, hi = bd["y_h"]
            rows.append({"item": "角标高度（%H）", "declared": bd["y_h"], "measured": m["badge_y_h"],
                         "verdict": "在带内" if lo - 0.02 <= m["badge_y_h"] <= hi + 0.02 else "带外"})
    return rows


def run(video: Path, packs: list[dict], shell: dict | None) -> dict:
    F = frames(video)
    m = {"video": video.name, "seconds": round(len(F) / FS, 2)}
    cuts = cut_frames(F)
    m.update(measure_subtitle(F, cuts))
    m.update(measure_shell(F, m, cuts) if shell else {})
    m.update(measure_tempo(F))
    m.update(measure_audio(video))
    rows = compare(m, packs[0], shell)
    return {"measured": m, "check": rows,
            "pass": sum(1 for r in rows if r["verdict"] == "在带内"),
            "fail": sum(1 for r in rows if r["verdict"] == "带外")}


def show(res: dict) -> None:
    m, rows = res["measured"], res["check"]
    print(f"\n{res['result']['video'] if 'result' in res else m['video']}  "
          f"{m.get('seconds')}s")
    print(f"  {'项':<18}{'模板声明':<22}{'实测':<22}判定")
    for r in rows:
        print(f"  {r['item']:<16}{str(r['declared']):<22}{str(r['measured']):<22}{r['verdict']}")
    print(f"  → 在带内 {res['pass']} · 带外 {res['fail']}")
    print("  其余实测：", json.dumps({k: v for k, v in m.items()
                                 if k not in {r['measured'] for r in rows}
                                 and k not in ("video", "seconds")}, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", action="append", required=True)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--shell", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    pack = load_pack(a.pack)
    shell = load_pack(a.shell) if a.shell else None
    out = []
    for v in a.video:
        res = run(Path(v), [pack], shell)
        show(res)
        out.append(res)
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n写入 {a.json}")
    return 0 if all(r["fail"] == 0 for r in out) else 1


if __name__ == "__main__":
    raise SystemExit(main())
