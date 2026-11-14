#!/usr/bin/env python3
"""首帧信息密度：第 0 帧到底有没有「东西」在画面上。

为什么要有它：BRIEF 第 4 步原文要求「**首帧要有足够的画面信息密度**」。
`verify_film.py` 原来只量「前 3 秒画面变化次数」（`first_seconds_verdict`）——它数的是
**变化**，不是**首帧**。于是首帧是一块纯色底板、一张全黑过渡、一片糊掉的空镜，
只要 1.2s 之后有硬切，那条判据照样全绿。观众手指一滑看到的第一眼却是空的。

四个读数（全部在**竖屏 UI 安全区**里量，口径复用 `platform_safe.py` 的遮挡带）：

    边缘密度  灰度 Sobel 幅值 > EDGE_T 的像素占比      —— 抓「糊」「空」「一整块纯色」
    直方图熵  64 桶灰度直方图的归一化熵（0–1）          —— 抓「整块死白/死黑」
    局部对比  p90 − p10 的灰度差（0–255）              —— 抓「灰蒙蒙一片」
    显著区域  与整帧中位色 RGB 距离 > SAL_T 的占比      —— 抓「几乎全是背景」

## 阈值出处：本仓 8 支真成片第 0 帧实测分布（2026-11-15）

样本：`ouyangTestCut/_cutskills/projects/{dianzan-a,dianzan-b,dianzan-c,r1a-shougong}`
+ `柒造Video/素材/_cutskills/projects/{qizao-ground-01,qizao-lime-baxis,qizao-naming-01,qizao-protect-01}`
的 `out/film-hf.mp4` 共 8 支（1080×1920 竖屏交付版），逐支 `ffmpeg -frames:v 1` 取
**第 0 帧**，量在竖屏 UI 安全区内（EDGE_T=32、BINS=64、SAL_T=40）：

    | 片子 | 边缘密度 | 熵    | 对比  | 显著  |
    |---|---|---|---|---|
    | dianzan-a        | 0.4077 | 0.911 | 126.6 | 0.7900 |
    | dianzan-b        | 0.3829 | 0.928 | 150.7 | 0.7365 |
    | dianzan-c        | 0.3785 | 0.899 | 124.0 | 0.7593 |
    | r1a-shougong     | 0.3199 | 0.863 | 160.0 | 0.6370 |
    | qizao-ground-01  | 0.2413 | 0.880 | 166.4 | 0.9086 |
    | qizao-lime-baxis | 0.0783 | 0.557 |  91.5 | 0.3754 |
    | qizao-naming-01  | 0.2522 | 0.939 | 149.8 | 0.6766 |
    | qizao-protect-01 | 0.2314 | 0.859 | 134.7 | 0.9062 |

    最低 / p10（线性插值，numpy 默认口径）：
        边缘 0.0783 / 0.1855 · 熵 0.557 / 0.768
        对比  91.5 / 114.2  · 显著 0.3754 / 0.5585

阈值取在**实测分布的下沿再留一档**，但**不能直接取 p10**：BRIEF 允许「大字卡先进场」，
而纯色底+大字的卡天生就是两色图——实测合成大字卡（1080×1920、150–190px 巨字）
边缘密度只有 0.0267–0.0582、熵只有 0.053–0.089、对比 p90−p10 = 0.0，显著区域
0.0267–0.0455。照真片 p10 立门，合法的卡首帧会全部判红。所以两类**可接受样本**
一起定地板（8 支真片首帧 + 4 张渲染口径的大字卡）：

    | 门 | 阈值 | 可接受样本下沿 | 拒绝样本上沿 | 依据 |
    |---|---|---|---|---|
    | 边缘密度 | ≥0.015 | 大字卡 0.0267、真片 0.0783（p10 0.1855） | 空底板 0.0034、纯色/纯黑 0.0000 | 离最近拒绝样本 4.4 倍，离最薄可接受样本 1.8 倍 |
    | 熵      | ≥0.35  | 真片 0.557（p10 0.768）；大字卡 0.053 | 纯色/纯黑 0.000、空底板 0.006 | 取真片最低的 63% |
    | 显著区域 | ≥0.020 | 大字卡 0.0267–0.0455、真片 0.3754（p10 0.5585） | 空底板 0.0019、纯色/纯黑 0.0000 | 离拒绝样本 10 倍；卡支配这个下限 |
    | 局部对比 | ≥40（旁证，不判门） | 真片 91.5 起 | 纯色/纯黑 0.0 | 大字卡本身就是 0.0，当门必误杀 |

## 判红规则：结构下限 + 「信息量」二选一

    red  ← 边缘密度 < EDGE_MIN                （纯色底板、纯黑过渡、几乎全糊/空镜）
    red  ← 熵 < ENTROPY_MIN **且** 显著区域 < SALIENT_MIN（色调单一又没有主体）
    green← 其余

为什么不把熵、显著区域各自当硬门：大字卡合法地熵 0.053、显著 0.027，暗调片合法地对比 91.5，
任一单项立门都会把 8 支里的某支或某种合法开场判红——那是判据挑刺，不是片子的问题。
门只拦「结构上真的空」（实测：拒绝样本三项全在 0.0034 / 0.006 / 0.0019 以下，
可接受样本最低 0.0267 / 0.557 / 0.0267，中间差一到两个数量级）。
8 支真片在本判据下 **8/8 绿**（0 误报）；判红时说人话给改法，不改阈值。

已知边界（写出来，不假装覆盖）：中度失焦（sigma≈18 的糊）边缘密度仍有 0.1145，
本条判不了——那种「糊但有余量」的片子得靠人眼；本条只拦「空/纯色/近全糊」。

用法：
    python3 scripts/first_frame.py --video <成片.mp4>        # 单文件
    python3 scripts/first_frame.py --project <项目目录>      # 自动找 out/film-hf.mp4、out/film.mp4
    python3 scripts/first_frame.py --video <任何图/片> --json # 机器读数

退出码：0 = 过；1 = 不过；2 = 判不了（取不到帧 / 文件坏 / 没装 numpy）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
# numpy 是 C 扩展装不进 scripts/vendor，落在 $CUTSKILLS_HOME/py（deps.json）——
# 和 verify_film.py / platform_safe.py 同一处挂载。没装也不崩：measure() 返回「判不了」。
try:
    from libs import cutskills_home as _cutskills_home
    _PY_DEPS = _cutskills_home() / "py"
    if _PY_DEPS.exists() and str(_PY_DEPS) not in sys.path:
        sys.path.append(str(_PY_DEPS))
except ImportError:
    pass

# ── 分析口径 ────────────────────────────────────────────────────────────────
ANALYSIS_W = 480          # 分析宽度：够看清结构，又把整帧 Sobel 压到毫秒级
EDGE_T = 32.0             # Sobel 幅值（0–255 灰度级）超过它才算一条边；定在编码噪声之上
BINS = 64                 # 灰度直方图桶数（归一化熵的分母 = log2(BINS)）
SAL_T = 40.0              # 与中位色的 RGB 欧氏距离超过它才算「显著」

# ── 判定阈值（出处：文件头 8 支真成片 + 4 张大字卡实测表）──────────────────
EDGE_MIN = 0.015          # 真片最低 0.0783（p10 0.1855）；大字卡最低 0.0267；空底板 0.0034
ENTROPY_MIN = 0.35        # 真片最低 0.557（p10 0.768）；纯色/纯黑 0.000、空底板 0.006
CONTRAST_MIN = 40.0       # 真片最低 91.5；大字卡 p90−p10 = 0.0 → 只作旁证，不判门
SALIENT_MIN = 0.020       # 真片最低 0.3754；大字卡 0.0267–0.0455；空底板 0.0019

LEVEL_RED, LEVEL_YELLOW, LEVEL_GREEN = "red", "yellow", "green"
LEVEL_ICON = {LEVEL_GREEN: "🟢", LEVEL_YELLOW: "🟡", LEVEL_RED: "🔴"}


def _ffmpeg() -> str:
    import os
    return os.environ.get("CUTSKILLS_FFMPEG") or "ffmpeg"


def _safe_box(W: int, H: int, proj=None) -> tuple[tuple[int, int, int, int], str]:
    """竖屏 UI 安全区（像素，原点左上）与出处一行。

    口径复用 `platform_safe.py`：顶部 10% / 右侧按钮列 13% / 底部 17% 三块遮挡带
    从画面里扣掉，剩下那块才是「观众真能看清」的区域。不另立一套 9:16 常量——
    两套口径迟早对不上，那时字幕算过的区域和首帧算过的区域就不是同一块。
    横屏没有竖屏遮挡带的实测口径（platform_safe 那时返回空表），退回整帧并在
    出处里写明，免得读数被当成「已排除遮挡」。
    """
    try:
        import platform_safe as _ps
        spec, src = _ps.load_spec(proj)
        zs = _ps.zones(W, H, spec)
    except Exception:                       # noqa: BLE001 判据依赖坏了 → 立刻退回整帧
        return (0, 0, W, H), "platform_safe 不可用，按整帧算"
    if not zs:
        return (0, 0, W, H), "横屏无竖屏遮挡带实测口径，按整帧算"
    x0, y0, x1, y1 = 0, 0, W, H
    for z in zs:
        zx, zy, zw, zh = z["box"]
        name = z["name"]
        if name == "顶部":
            y0 = max(y0, zy + zh)
        elif name == "底部":
            y1 = min(y1, zy)
        elif name == "右侧按钮列":
            x1 = min(x1, zx)
    return (x0, y0, x1, y1), f"竖屏 UI 安全区（{src}）"


def grab(path: Path | str, *, t0: float = 0.0, width: int = ANALYSIS_W):
    """取一帧 RGB 小图 → (uint8 ndarray HxWx3, None) 或 (None, 错误说明)。

    `t0=0` 且不传 `-ss` 才是**第 0 帧**；带 `-ss 0.0` 有的封装会跳到第一个关键帧，
    那已经不是观众看到的第一眼了，所以这里不传 seek。
    """
    try:
        import numpy as np
    except ImportError as e:               # 没装依赖 = 量不了，不许当通过
        return None, f"numpy 不可用（{e}）：先跑 scripts/deps 安装"
    p = Path(path).expanduser()
    if not p.exists():
        return None, f"文件不存在：{p}"
    vf = f"scale={width}:-2:flags=bilinear,format=rgb24"
    cmd = [_ffmpeg(), "-v", "error", "-nostdin"]
    if t0:
        cmd += ["-ss", f"{float(t0):.3f}"]
    cmd += ["-i", str(p), "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    raw = r.stdout or b""
    if not raw:
        tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        why = tail[-1][:180] if tail else f"ffmpeg 退出码 {r.returncode}，没吐出帧"
        when = f"第 {float(t0):g} 秒" if t0 else "第 0 帧"
        return None, f"取不到{when}：{why}"
    need = width * 3
    if len(raw) < need or len(raw) % need:
        return None, f"帧数据不完整：{len(raw)} 字节，不是 {width} 宽的整数行"
    arr = np.frombuffer(raw, dtype=np.uint8)
    h = arr.size // need
    return arr[: h * need].reshape(h, width, 3), None


def _sobel(gray):
    """3×3 Sobel 幅值图（边缘像素不算，用复制边填充）。"""
    import numpy as np
    g = np.pad(gray, 1, mode="edge").astype(np.float32)
    gx = (g[:-2, 2:] + 2 * g[1:-1, 2:] + g[2:, 2:]
          - g[:-2, :-2] - 2 * g[1:-1, :-2] - g[2:, :-2])
    gy = (g[2:, :-2] + 2 * g[2:, 1:-1] + g[2:, 2:]
          - g[:-2, :-2] - 2 * g[:-2, 1:-1] - g[:-2, 2:])
    return np.sqrt(gx * gx + gy * gy)


def measure(path: Path | str, *, zone: str = "safe", t0: float = 0.0,
            proj: Path | str | None = None, width: int = ANALYSIS_W) -> dict:
    """量一帧的四个读数。`zone="safe"` 只算 UI 安全区，`zone="full"` 算整帧。

    返回 dict（永远带 `error` 键；量成了就是 None）。量不成**不抛异常**——
    调用方（verify_film / gate）要能把它当「判不了」报出来，而不是崩掉整份报告。
    """
    out = {"path": str(path), "zone": zone, "t0": float(t0),
           "width": None, "height": None, "safe_box": None, "safe_source": None,
           "edge_density": None, "entropy": None, "contrast_p90_p10": None,
           "salient_ratio": None, "median_rgb": None, "error": None}
    try:
        import numpy as np
    except ImportError as e:
        out["error"] = f"numpy 不可用（{e}）：先跑 scripts/deps 安装"
        return out
    img, err = grab(path, t0=t0, width=width)
    if err:
        out["error"] = err
        return out
    H, W = img.shape[0], img.shape[1]
    out["width"], out["height"] = W, H
    box, src = _safe_box(W, H, proj)
    out["safe_source"] = src
    if zone == "safe":
        x0, y0, x1, y1 = box
    else:
        x0, y0, x1, y1 = 0, 0, W, H
        box = (0, 0, W, H)
    out["safe_box"] = [int(x0), int(y0), int(x1), int(y1)]
    sub = img[y0:y1, x0:x1]
    if sub.size == 0:
        out["error"] = "安全区裁出来是空的（遮挡带加起来吃掉了整帧）"
        return out

    # 灰度用 Rec.601 权重：与 ffmpeg 的 gray 转换同一口径，换机器不改读数
    gray = (sub[:, :, 0] * 0.299 + sub[:, :, 1] * 0.587 + sub[:, :, 2] * 0.114)
    npx = float(gray.size)

    mag = _sobel(gray)
    out["edge_density"] = float((mag > EDGE_T).sum() / npx)

    hist, _ = np.histogram(gray, bins=BINS, range=(0.0, 256.0))
    p = hist.astype(np.float64) / max(1.0, npx)
    p = p[p > 0]
    ent = float(max(0.0, -(p * np.log2(p)).sum()))
    out["entropy"] = float(min(1.0, ent / np.log2(BINS)))

    p10, p90 = np.percentile(gray, [10, 90])
    out["contrast_p90_p10"] = float(p90 - p10)

    med = np.median(sub.reshape(-1, 3), axis=0)
    out["median_rgb"] = [int(round(float(v))) for v in med]
    dist = np.sqrt(((sub.astype(np.float32) - med.astype(np.float32)) ** 2).sum(axis=2))
    out["salient_ratio"] = float((dist > SAL_T).sum() / npx)
    return out


def _fix_hint(m: dict, fails: list[str]) -> str:
    """判红时说清「该怎么改」，不甩一串数字让人自己猜。"""
    tips = []
    if "edge" in fails:
        tips.append("第 0 帧是纯色底板/黑场/近乎全糊：改用有主体的画面，或让大字卡在 0s 就进场")
    if "flat" in fails:
        tips.append("首帧色调单一又几乎全是背景：把主体或大字卡往前挪到 0s，别让空镜开局")
    if not tips:
        tips.append("首帧结构太薄：先上一张有主体的画面或让大字卡先进场")
    return "；".join(tips)


def verdict(m: dict) -> tuple[str, str]:
    """读数 → (level, reason)。`level` ∈ red / yellow / green。

    判不了 → **红**（不是绿、也不只是黄）：镜头取不到帧说明这条片子的首帧没人能确认，
    与 `amap_fresh_verdict` 同一个口径——「没量到」不许冒充「没问题」。
    """
    if not isinstance(m, dict):
        return LEVEL_RED, "判不了：没有读数"
    if m.get("error"):
        return LEVEL_RED, f"判不了：{m['error']}——这条没量到，不能当通过"
    need = ("edge_density", "entropy", "contrast_p90_p10", "salient_ratio")
    missing = [k for k in need if m.get(k) is None]
    if missing:
        return LEVEL_RED, "判不了：读数缺 " + "、".join(missing) + "——这条没量到"
    edge = float(m["edge_density"])
    ent = float(m["entropy"])
    con = float(m["contrast_p90_p10"])
    sal = float(m["salient_ratio"])
    fails = []
    if edge < EDGE_MIN:
        fails.append("edge")                 # 结构下限：空底板 / 纯黑 / 近乎全糊
    if ent < ENTROPY_MIN and sal < SALIENT_MIN:
        fails.append("flat")                # 色调单一又没有主体
    reading = (f"边缘 {edge * 100:.1f}% · 熵 {ent:.2f} · 对比 {con:.0f} · 显著 {sal * 100:.1f}%")
    if fails:
        return LEVEL_RED, f"{reading}：首帧信息量不够——" + _fix_hint(m, fails)
    return LEVEL_GREEN, reading + "：首帧有主体、有层次"


def find_film(project: Path | str) -> Path | None:
    """项目 → 成片路径（与 verify_film / gate 同一优先级）。"""
    p = Path(project).expanduser()
    for name in ("out/film-hf.mp4", "out/film.mp4"):
        f = p / name
        if f.exists():
            return f
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="量首帧（第 0 帧）信息密度：边缘/熵/对比/显著区域")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--video", help="成片或任意单帧图（mp4/png/jpg）")
    g.add_argument("--project", help="项目目录：自动找 out/film-hf.mp4 → out/film.mp4")
    ap.add_argument("--t", type=float, default=0.0, help="取第几秒（默认 0 = 首帧）")
    ap.add_argument("--zone", choices=("safe", "full"), default="safe",
                    help="safe=只算竖屏 UI 安全区（默认）；full=整帧")
    ap.add_argument("--json", action="store_true", help="打印机器读数 JSON")
    a = ap.parse_args()

    proj = Path(a.project).expanduser() if a.project else None
    target = find_film(proj) if proj else Path(a.video).expanduser()
    if target is None:
        print(f"🔴 判不了：{proj} 下没有 out/film-hf.mp4 或 out/film.mp4")
        return 2
    m = measure(target, zone=a.zone, t0=a.t, proj=proj)
    level, reason = verdict(m)
    if a.json:
        print(json.dumps({"measure": m, "level": level, "reason": reason},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{LEVEL_ICON[level]} 首帧 · {target}")
        if m.get("error"):
            print(f"  {reason}")
        else:
            print(f"  边缘密度 {m['edge_density'] * 100:.2f}%（≥{EDGE_MIN * 100:.1f}%）"
                  f" · 熵 {m['entropy']:.3f}（≥{ENTROPY_MIN}）"
                  f" · 局部对比 {m['contrast_p90_p10']:.1f}（旁证 ≥{CONTRAST_MIN:g}）"
                  f" · 显著区域 {m['salient_ratio'] * 100:.2f}%（≥{SALIENT_MIN * 100:.1f}%）")
            print(f"  口径：{m['width']}×{m['height']} 分析图 · {m['safe_source']}"
                  f" · 取样 {m['safe_box']}")
            print(f"  结论：{reason}")
    return 0 if level == LEVEL_GREEN else (2 if m.get("error") else 1)


if __name__ == "__main__":
    raise SystemExit(main())
