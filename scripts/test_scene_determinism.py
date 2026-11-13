#!/usr/bin/env python3
"""T-055 回归：代码场景必须可重复渲染（相同输入 → 逐帧一致）。

病与判据的出处：AI 视听方案（2026-11-14）的机器硬性门禁第一条——
「场景代码可重复渲染，相同输入任意抽帧结果一致；无联网执行脚本、无越权文件访问」。
`gen_scene.py` 的场景函数是纯 `t` 驱动：随机用固定种子的 `default_rng`，
抖动用整数 hash 推导，不允许 `Date.now` / 无种子随机。这条回归钉住它不许退化：
同一个场景渲两遍，文件哈希必须一模一样；哈希若因编码线程抖动不一致，
退而抽帧比像素（帧内容才是真正的合同）。

用法：python3 scripts/test_scene_determinism.py
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE / "gen_scene.py"

# 小规格快速渲：phase 场景 2s / 12fps / 540x960，两遍加起来十几秒
SCENE, DUR, FPS, SIZE = "phase", 2.0, 12, "540x960"


def render_once(out: Path) -> None:
    r = subprocess.run([sys.executable, str(GEN), "--scene", SCENE, "--out", str(out),
                        "--dur", str(DUR), "--fps", str(FPS), "--size", SIZE],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"渲染失败：{(r.stderr or r.stdout).strip()[-300:]}"
    assert out.exists() and out.stat().st_size > 0, "没有产出片段"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="scene-det-"))
    try:
        a, b = base / "a.mp4", base / "b.mp4"
        render_once(a)
        render_once(b)
        if sha(a) == sha(b):
            print("✅ 场景可重复渲染：两遍文件哈希一致（确定性成立，含编码层）")
            return 0
        # 编码层线程抖动的兜底：抽帧比像素。文件哈希不同但帧内容一致也算过，
        # 因为场景合同的真值在帧里，不在容器字节里。
        import numpy as np
        from PIL import Image
        max_diff = 0.0
        for t in (0.1, 0.5, 1.0, 1.5):
            fa, fb = base / "fa.png", base / "fb.png"
            for out, dst in ((a, fa), (b, fb)):
                r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t),
                                    "-i", str(out), "-frames:v", "1", str(dst)],
                                   capture_output=True, text=True)
                assert r.returncode == 0, f"抽帧失败 @ {t}s：{r.stderr[-200:]}"
            ia = np.asarray(Image.open(fa).convert("L"), dtype=np.int16)
            ib = np.asarray(Image.open(fb).convert("L"), dtype=np.int16)
            max_diff = max(max_diff, float(np.abs(ia - ib).max()))
        assert max_diff == 0.0, \
            f"场景不确定：文件哈希不同且同帧像素差 max={max_diff}（场景函数里混入了非确定源）"
        print("✅ 场景可重复渲染：容器哈希随编码线程抖动，但 4 个采样帧逐像素一致（帧内容合同成立）")
        return 0
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
