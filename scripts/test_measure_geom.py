#!/usr/bin/env python3
"""measure_geom 的负样本：判据必须能把画错的那一版抓出来。

四个合成样本，全部手画，不依赖任何真片：
  A 正的      杆在上、码垛在下、水平       → order ✅ / 两端等平
  B 倒画的    杆在下、码垛在上              → order 🔴（2026-11-14 那版真事）
  C 倾斜的    杆左低右高 7°                 → 两端高差 > 20px
  D 假的      杆画得很长但「码垛」是文字        → 文字不许被当成码垛

跑法：python3 scripts/test_measure_geom.py   （exit 0 = 全过）
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
W, H = 540, 720
GOLD = (210, 170, 60)


def bar(d: ImageDraw.ImageDraw, y: int, tilt: int = 0) -> None:
    d.polygon([(40, y + tilt), (500, y - tilt), (500, y - tilt + 12), (40, y + 12)],
              fill=GOLD)


def stacks(d: ImageDraw.ImageDraw, y: int, n: int = 5) -> None:
    for k in range(n):
        for cx in (120, 420):
            d.rectangle([cx - 34, y - k * 14, cx + 34, y - k * 14 + 11], fill=GOLD)


def make(kind: str, path: Path) -> None:
    im = Image.new("RGB", (W, H), (16, 12, 10))
    d = ImageDraw.Draw(im)
    if kind == "A":
        bar(d, 300)
        stacks(d, 460)
    elif kind == "B":                      # 倒着画：杆在下、码垛在上
        bar(d, 520)
        stacks(d, 420)
    elif kind == "C":                      # 倾斜：杆从 y+TILT 走到 y-TILT
        bar(d, 360, tilt=22)
        stacks(d, 520)
    elif kind == "D":                      # 「码垛」其实是字：宽度参差、数量多
        bar(d, 300)
        for x in range(70, 470, 22):
            d.rectangle([x, 430, x + 9, 462], fill=GOLD)
    im.save(path)


def run(args: list[str]) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(HERE / "measure_geom.py"), *args],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory(prefix="geom-") as td:
        p = Path(td)
        for kind in "ABCD":
            make(kind, p / f"{kind}.png")

        rc, out = run(["--frame", str(p / "A.png"), "--what", "order"])
        ok = rc == 0 and "下方" in out
        print(f"  {'✅' if ok else '🔴'} A 正着画：order 通过（rc={rc}）")
        bad += 0 if ok else 1

        rc, out = run(["--frame", str(p / "B.png"), "--what", "order"])
        ok = rc == 1 and "上方" in out
        print(f"  {'✅' if ok else '🔴'} B 倒着画：order 必须 🔴（rc={rc}）")
        bad += 0 if ok else 1

        rc, out = run(["--frame", str(p / "C.png"), "--what", "ends",
                       "--ends", "60,480", "--yrange", "300:420"])
        num = [ln for ln in out.splitlines() if "两端顶端高差" in ln]
        ok = bool(num) and abs(float(num[0].split("高差")[1].split("px")[0])) > 20
        print(f"  {'✅' if ok else '🔴'} C 倾斜 7°：两端高差 > 20px（{(num or ['—'])[0].strip() if num else '没量到'}）")
        bad += 0 if ok else 1

        rc, out = run(["--frame", str(p / "D.png"), "--what", "gold"])
        ok = "码垛" not in out
        print(f"  {'✅' if ok else '🔴'} D 文字假冒码垛：不许认成码垛")
        bad += 0 if ok else 1

    print("结论：" + ("✅ 4/4 通过" if bad == 0 else f"🔴 {bad} 条不过"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
