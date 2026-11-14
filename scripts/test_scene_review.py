#!/usr/bin/env python3
"""契约测试：`scene_review.py` 的四条判据真的抓得住——自造样例，不依赖真片。

为什么用合成片：真片在别人机器上、也会被重渲，判据的负样本必须能自己造，
否则「这条判据会不会红」永远只能靠人记得拿某条老片试。这里用 PIL/numpy 画
一段「前半静止、后半移动」的条形图，按 25fps 喂给 ffmpeg 编成真 mp4，
再让 `scene_review.py` 按 CLI 跑一遍，只读它自己写出来的 `review.json`。

四件事各自的正反样本：
  ① 静止段必须被抓红（全场「最长静止」+ 落在静止段里的那个事件）
  ② 移动段不许报（落在运动里的那个事件是绿的）
  ③ 时长不符必须被抓红（--dur 故意给 4.3s，实长 4.0s）
  ④ 全绿时退出码 0（连续运动的片子 + 锚点对着运动）
  ⑤ 动作比锚点早：锚点写晚了，要报黄并给出毫秒数（不是只报「不合格」）
  ⑥ --median-band 真透传：同一段按 6:20 判，读数里写明用的是哪条区间

用法：python3 scripts/test_scene_review.py      # exit 0 = 全过
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = HERE / "scene_review.py"
W, H, FPS, DUR = 540, 960, 25, 4.0
SPAN, SPEED = 340, 150.0          # 金色竖条的来回行程与速度（px/s）
BAR_W, BAR_Y0, BAR_Y1 = 120, 240, 720
STEP_AT = 2.20                    # 第⑤条样例：唯一一次位移发生在这一秒


def ffmpeg_bin() -> str:
    import os
    return os.environ.get("CUTSKILLS_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def bar_x(t: float) -> int:
    """来回扫（三角波）：任何时刻都在动，不会扫出画面外变成静止。"""
    p = (t * SPEED) % (2 * SPAN)
    return int(40 + (p if p < SPAN else 2 * SPAN - p))


def frame(t: float, mode: str) -> np.ndarray:
    a = np.zeros((H, W, 3), np.uint8)
    g = np.linspace(14, 30, H)[:, None]
    a[:, :, 0] = g
    a[:, :, 1] = g * 0.8
    a[:, :, 2] = g * 0.7
    if mode == "always":                     # 全程连续运动
        x = bar_x(t)
    elif mode == "half":                     # 前 2s 钉住，后 2s 才动
        x = bar_x(2.0) if t < 2.0 else bar_x(t)
    else:                                    # "step"：一直不动，只在 STEP_AT 跳一格
        x = bar_x(0.0) + (110 if t >= STEP_AT else 0)
    a[BAR_Y0:BAR_Y1, x:x + BAR_W] = (246, 235, 52)
    return a


def render(dst: Path, mode: str) -> None:
    p = subprocess.Popen(
        [ffmpeg_bin(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18",
         "-pix_fmt", "yuv420p", str(dst)], stdin=subprocess.PIPE)
    for i in range(int(DUR * FPS)):
        p.stdin.write(frame(i / FPS, mode).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"合成片编码失败：{dst}")


def run(tmp: Path, video: Path, dur: float, events: list[dict] | None,
        tag: str, extra: list[str] | None = None) -> tuple[subprocess.CompletedProcess, dict]:
    out = tmp / f"out-{tag}"
    cmd = [sys.executable, str(SR), "--video", str(video), "--dur", str(dur),
           "--out", str(out)]
    if events is not None:
        ev = tmp / f"ev-{tag}.json"
        ev.write_text(json.dumps(events), encoding="utf-8")
        cmd += ["--events", str(ev)]
    cmd += list(extra or [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    js = out / "scene-review" / "review.json"
    if not js.exists():
        raise AssertionError(f"[{tag}] 没写出 review.json：\n{p.stdout}\n{p.stderr}")
    return p, json.loads(js.read_text(encoding="utf-8"))


def row(rep: dict, name: str) -> dict:
    for r in rep["checks"]:
        if r["name"] == name:
            return r
    raise AssertionError(f"报告里没有「{name}」这一行："
                         f"{[r['name'] for r in rep['checks']]}")


def brief(rep: dict, name: str) -> str:
    r = row(rep, name)
    return f"{r['level']} {r['value']}"


def main() -> int:
    fails: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="scene-review-test-"))

    def check(cond: bool, ok: str, bad: str) -> None:
        print(("  ✅ " if cond else "  🔴 ") + (ok if cond else bad))
        if not cond:
            fails.append(bad)

    try:
        half, always, step = tmp / "half.mp4", tmp / "always.mp4", tmp / "step.mp4"
        render(half, "half")
        render(always, "always")
        render(step, "step")

        # ① ② 静止段判红、移动段不报
        p, rep = run(tmp, half, DUR,
                     [{"t": 1.0, "name": "idle"}, {"t": 3.0, "name": "move"}], "half")
        print(f"[1] 前半静止后半移动 · 退出码 {p.returncode}")
        print(f"    最长静止：{brief(rep, '最长静止')}")
        print(f"    事件 1：{brief(rep, '事件 1 idle @1.00s 动作')}")
        print(f"    事件 2：{brief(rep, '事件 2 move @3.00s 动作')}")
        check(p.returncode == 1, "静止片退出码 1", f"静止片退出码 {p.returncode}（应为 1）")
        check(row(rep, "最长静止")["level"] == "red",
              "① 静止段被抓红（最长静止）", "① 静止段没被抓红——最长静止判绿")
        check(row(rep, "事件 1 idle @1.00s 动作")["level"] == "red",
              "① 落在静止段的事件被抓红", "① 落在静止段的事件没判红")
        check(bool(re.search(r"\d", row(rep, "事件 1 idle @1.00s 动作")["value"])),
              "① 红项带读数（帧差/噪声底）", "① 红项没有读数")
        check(row(rep, "事件 2 move @3.00s 动作")["level"] == "green",
              f"② 移动段不报（{brief(rep, '事件 2 move @3.00s 动作')}）",
              f"② 移动段被误报：{brief(rep, '事件 2 move @3.00s 动作')}")

        # ③ 时长不符判红
        p, rep = run(tmp, always, DUR + 0.3,
                     [{"t": 1.0, "name": "pop"}], "offset")
        print(f"[2] 时长不符（时间轴 4.30s / 成片 4.00s）· 退出码 {p.returncode}")
        print(f"    时长偏移：{brief(rep, '时长偏移')}")
        check(p.returncode == 1, "时长不符退出码 1", f"时长不符退出码 {p.returncode}（应为 1）")
        check(row(rep, "时长偏移")["level"] == "red",
              "③ 时长不符被抓红", "③ 时长不符没判红")
        check("-0.3" in row(rep, "时长偏移")["value"],
              "③ 偏移读数带秒数（-0.300s）", "③ 偏移没有读数")

        # ④ 全绿 → 退出码 0，产物齐全
        p, rep = run(tmp, always, DUR,
                     [{"t": 1.0, "name": "pop"}, {"t": 3.0, "name": "slide"}], "good")
        reds = [r["name"] for r in rep["checks"] if r["level"] == "red"]
        print(f"[3] 连续运动 + 锚点对得上 · 退出码 {p.returncode} · 红 {len(reds)}")
        print(f"    {rep['summary']}")
        check(p.returncode == 0, "④ 全绿退出码 0", f"④ 全绿退出码 {p.returncode}（应为 0）：{reds}")
        check(not reds, "④ 全绿时没有红项", f"④ 全绿时还有红项：{reds}")
        for rel in ("scene-review/sheet.png", "scene-review/review.json"):
            f = tmp / "out-good" / rel
            check(f.exists() and f.stat().st_size > 1000, f"④ 产物 {rel} 已生成",
                  f"④ 缺产物或太小：{rel}")

        # ⑤ 动作早于锚点 → 黄 + 毫秒读数（不是只报「不合格」）
        p, rep = run(tmp, step, DUR, [{"t": 2.24, "name": "late"}], "step")
        r = row(rep, "事件 1 late @2.24s 动作")
        print(f"[4] 动作在 {STEP_AT:.2f}s、锚点写 2.24s · {r['level']} {r['value']}")
        check(r["level"] == "yellow", "⑤ 动作早于锚点报黄", f"⑤ 报成了 {r['level']}：{r['value']}")
        # 动作确实早于锚点：要么量出「早于 N ms」，要么量出「窗口开头已在动」——
        # 后者是同一件事在「起手早于 ±0.12s」时的说法，两种都带毫秒读数。
        check(("早于" in r["value"] or "窗口开头" in r["value"])
              and bool(re.search(r"\d+ms", r["value"])),
              "⑤ 黄项给出「早于 N ms」的读数", f"⑤ 没有毫秒读数：{r['value']}")
        check(any("props.events" in x["fix"] or "动作" in x["fix"] for x in rep["fixes"]),
              "⑤ 附「改哪里」", "⑤ 没有返工建议")

        # ⑥ --median-band 透传：同一段（中位 4.81）按实拍口径 6:20 判必须翻红，
        #    且读数里要写清用的是哪条区间——这是「实拍底片不许拿场景口径判」的正样本。
        p, rep = run(tmp, always, DUR, None, "band", ["--median-band", "6:20"])
        r = row(rep, "帧差中位")
        print(f"[5] 同一段改用 --median-band 6:20 · {r['level']} {r['value']}")
        check(r["level"] == "red" and "6.0–20.0" in r["value"],
              "⑥ --median-band 透传进判据且读数写明区间", f"⑥ 没透传：{r['level']} {r['value']}")
        check(rep["median_band"] == [6.0, 20.0], "⑥ 报告记下本次口径 6–20",
              f"⑥ 报告里的 median_band 不对：{rep.get('median_band')}")

        print(f"\n结论：{'全过' if not fails else f'{len(fails)} 条不过'}")
        return 0 if not fails else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
