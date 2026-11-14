#!/usr/bin/env python3
"""scene_sandbox.py 的合同测试（评审 P1-06 落地）：

  ① 静态门槛抓得住违规 import 与危险调用（socket / subprocess / eval）；
  ② 白名单内的合法场景代码放行；
  ③ 端到端：一个最小项目场景在沙箱里真的渲得出 mp4（拒网围栏下）；
  ④ 违规代码被拒（exit 3），一个字节都不会写出去。

跑法：python3 scripts/test_scene_sandbox.py（挂 selftest ㉚）
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scene_sandbox  # noqa: E402

GOOD_SCENE = '''from __future__ import annotations
import math
from PIL import Image, ImageDraw

SCENE_ID = "sandbox_ok"
EVENTS: list = []

def scene(t: float, W: int, H: int, dur: float) -> Image.Image:
    im = Image.new("RGB", (W, H), (24, 22, 30))
    d = ImageDraw.Draw(im)
    k = 0.2 + 0.6 * (t / max(dur, 1e-6))
    r = W * 0.18 * k
    d.ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill=(216, 180, 96))
    return im
'''

BAD_SOCKET = GOOD_SCENE.replace("from PIL import Image, ImageDraw",
                                "import socket\nfrom PIL import Image, ImageDraw")
BAD_SUBPROC = GOOD_SCENE.replace("import math", "import math, subprocess")
BAD_EVAL = GOOD_SCENE + "\n# __x = eval('1')\nx = eval('2+3')\n"
BAD_RELATIVE = GOOD_SCENE.replace("import math", "from .. import math")


def test_static_check():
    print("▶ 静态门槛")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.py"
        for name, src, kw in (("socket", BAD_SOCKET, "socket"),
                              ("subprocess", BAD_SUBPROC, "subprocess"),
                              ("eval", BAD_EVAL, "eval"),
                              ("相对导入", BAD_RELATIVE, "相对导入")):
            p.write_text(src)
            bad = scene_sandbox.static_check(p)
            assert bad, f"{name} 必须被拒"
            assert any(kw in b for b in bad), f"{name} 的拒绝理由要提到 {kw}：{bad}"
            print(f"  ✅ {name} 被拒：{bad[0]}")
        p.write_text(GOOD_SCENE)
        assert scene_sandbox.static_check(p) == [], "合法场景不许误拒"
        print("  ✅ 白名单内的合法场景放行")


def test_end_to_end():
    print("▶ 沙箱端到端（真渲染）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        scenes = root / "scenes"
        scenes.mkdir()
        (scenes / "sandbox_ok.py").write_text(GOOD_SCENE)
        out = root / "out" / "sandbox_ok.mp4"
        r = subprocess.run(
            [sys.executable, str(HERE / "scene_sandbox.py"),
             "--scenes-dir", str(scenes), "--scene", "sandbox_ok",
             "--out", str(out), "--dur", "1.0", "--fps", "6", "--size", "270x480"],
            capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, f"沙箱渲染应成功：{r.returncode}\n{r.stderr[-600:]}"
        assert out.exists() and out.stat().st_size > 0, "mp4 要真的写出来"
        print(f"  ✅ 拒网沙箱里渲出 {out.stat().st_size // 1024}KB mp4")

        # 违规场景：拒绝且不写产物
        (scenes / "sandbox_evil.py").write_text(BAD_SOCKET)
        out2 = root / "out2" / "evil.mp4"
        r2 = subprocess.run(
            [sys.executable, str(HERE / "scene_sandbox.py"),
             "--scenes-dir", str(scenes), "--scene", "sandbox_evil",
             "--out", str(out2), "--dur", "1.0", "--fps", "6", "--size", "270x480"],
            capture_output=True, text=True, timeout=60)
        assert r2.returncode == 3, f"违规场景必须 exit 3（静态拒绝）：实际 {r2.returncode}"
        assert "socket" in (r2.stderr or ""), "拒绝理由要提到 socket"
        assert not out2.exists(), "被拒的场景一个字节都不许写出去"
        print("  ✅ 违规场景被静态拒绝（exit 3），零产物")


def test_render_hf_routes_project_scene():
    print("▶ 渲器路由：项目场景经沙箱（scene_mp4 源码解析）")
    src = (HERE / "render_hf.py").read_text(encoding="utf-8")
    assert "scene_sandbox.py" in src and "src_proj = proj / \"scenes\"" in src, \
        "render_hf.scene_mp4 必须把项目场景路由到沙箱"
    print("  ✅ scene_mp4 项目场景 → scene_sandbox.py")


def main() -> int:
    print("▶ 场景沙箱合同测试")
    test_static_check()
    test_end_to_end()
    test_render_hf_routes_project_scene()
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
