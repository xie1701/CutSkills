#!/usr/bin/env python3
"""R0 守卫测试：评审报告 §8 G-01～G-07 的负样本 + 正样本（2026-11-14）。

口径：每项先证「现在确实会失败/失败过」，再证修复后失败转绿。
挂 selftest ㉛。跑法：python3 scripts/test_r0_guards.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GOOD_SCENE = '''from __future__ import annotations
import math
from PIL import Image, ImageDraw

SCENE_ID = "{sid}"

def scene(t: float, W: int, H: int, dur: float) -> Image.Image:
    im = Image.new("RGB", (W, H), (24, 22, 30))
    d = ImageDraw.Draw(im)
    k = 0.2 + 0.6 * (t / max(dur, 1e-6))
    r = W * 0.18 * k
    d.ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill=(216, 180, 96))
    return im
'''


def _spec_json(sid: str) -> str:
    return json.dumps({
        "scene_id": sid, "kind": "causal", "purpose": "测试",
        "beats": {k: "测试" for k in ("subject", "action", "change",
                                      "camera", "audience", "sound")},
        "claim_source": {"asset_id": "", "start": 0.0, "end": 0.0,
                         "evidence": "他家的报价单"},
        "audio": {"events": [{"t": 0.5, "name": "pop-medium-in"}]},
        "review": {"visual": "ok", "audio": "ok", "truth": "ok"},
    }, ensure_ascii=False, indent=1)


def test_g01_new_scene_routes():
    """G-01：项目级新场景（注册表里没有的名字）能从 scene_mp4 真渲出 mp4。

    2026-11-15 对抗审查：临时目录必须落在 **/Users 下**——沙箱 profile 曾因
    `deny /Users` 压过 `allow out.parent` 让真实项目场景一律渲染失败，而测试跑在
    /var/folders 全绿，掩盖了 P0。用 home 下的临时目录防止再次「绿得虚假」。
    """
    import render_hf
    td = tempfile.mkdtemp(prefix="g01-", dir=str(Path.home()))
    try:
        proj = Path(td)
        (proj / "scenes").mkdir()
        (proj / "hf" / "clips").mkdir(parents=True)
        (proj / "scenes" / "brand_new_scene.py").write_text(
            GOOD_SCENE.replace("{sid}", "brand_new_scene"), encoding="utf-8")
        el = {"n": 1, "props": {"scene": "brand_new_scene"}, "dur": 0.5}
        out = render_hf.scene_mp4(proj, el, FPS=30, W=480, H=854)
        assert out.exists() and out.stat().st_size > 4096, f"新场景没渲出：{out}"
        print("  ✅ G-01 项目级新场景直接进沙箱渲出 mp4（/Users 下真实路径）")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_g02_no_dir_scan():
    """G-02：同目录同伙文件顶层代码一个字节都不许执行（gen_scene --scene-file）。"""
    with tempfile.TemporaryDirectory(prefix="g02-") as td:
        scenes = Path(td) / "scenes"
        scenes.mkdir()
        marker = Path(td) / "MARKER.txt"
        (scenes / "target.py").write_text(
            GOOD_SCENE.replace("{sid}", "target"), encoding="utf-8")
        # 冒名文件必须**语法合法**：副作用行插在 __future__ 之后（否则 SyntaxError，
        # 「冒名也不许过」这条断言永远走不到真路径——2026-11-15 对抗审查 P2）
        body = GOOD_SCENE.replace("{sid}", "NOT_target")
        first, rest = body.split("\n", 1)
        (scenes / "sneaky.py").write_text(
            first + "\n" + f"open({json.dumps(str(marker))!r}, 'w').write('pwned')\n" + rest,
            encoding="utf-8")
        out = Path(td) / "out.mp4"
        r = subprocess.run(
            [sys.executable, str(HERE / "gen_scene.py"),
             "--scene", "target", "--scene-file", str(scenes / "target.py"),
             "--out", str(out), "--dur", "0.4", "--fps", "30", "--size", "320x568"],
            capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"--scene-file 渲染失败：{r.stderr[-400:]}"
        assert not marker.exists(), "同伙文件的顶层代码被执行了！"
        # SCENE_ID 冒名也不许过
        r2 = subprocess.run(
            [sys.executable, str(HERE / "gen_scene.py"),
             "--scene", "target", "--scene-file", str(scenes / "sneaky.py"),
             "--out", str(out), "--dur", "0.4", "--fps", "30", "--size", "320x568"],
            capture_output=True, text=True, timeout=60)
        assert r2.returncode != 0 and not marker.exists(), \
            "冒名 SCENE_ID 或同伙文件被放行了"
        print("  ✅ G-02 --scene-file 只执行目标文件，同伙文件与冒名全被拒")


def test_g03_no_sandbox_exec_refuses():
    """G-03：没有 sandbox-exec 时拒绝执行 AI 场景（exit 5），除非显式放行。"""
    import scene_sandbox
    with tempfile.TemporaryDirectory(prefix="g03-") as td:
        scenes = Path(td) / "scenes"
        scenes.mkdir()
        (scenes / "ok.py").write_text(GOOD_SCENE.replace("{sid}", "ok"),
                                      encoding="utf-8")
        args = argparse.Namespace(scenes_dir=str(scenes), scene="ok",
                                  out=str(Path(td) / "o.mp4"), dur=0.4, fps=30,
                                  size="320x568", marks="", allow_unsandboxed=False)
        real_which = scene_sandbox.shutil.which
        scene_sandbox.shutil.which = lambda name: None if name == "sandbox-exec" else real_which(name)
        try:
            rc = scene_sandbox.render(args)
            assert rc == 5, f"无围栏必须拒绝（exit 5），实际 {rc}"
            assert not (Path(td) / "o.mp4").exists(), "无围栏还把片子渲出来了"
        finally:
            scene_sandbox.shutil.which = real_which
        print("  ✅ G-03 无 sandbox-exec 拒绝执行 AI 场景（exit 5）")


def test_g04_sign_binds_hash():
    """G-04：签收与场景代码/音效事件指纹绑定；改一字节签收即失效。"""
    import scene_spec
    with tempfile.TemporaryDirectory(prefix="g04-") as td:
        proj = Path(td)
        (proj / "scenes").mkdir()
        (proj / "scenes" / "sig_x.py").write_text(
            GOOD_SCENE.replace("{sid}", "sig_x"), encoding="utf-8")
        (proj / "scenes" / "sig_x.json").write_text(_spec_json("sig_x"),
                                                    encoding="utf-8")
        (proj / "timeline.json").write_text(json.dumps({
            "elements": [{"kind": "gen", "n": 1, "dur": 2.0,
                          "evidence": "他家的报价单",
                          "props": {"scene": "sig_x", "scene_spec": "sig_x"}}],
        }, ensure_ascii=False), encoding="utf-8")

        # 直接走子进程：签了字符串但没盖指纹 → error
        r = subprocess.run([sys.executable, str(HERE / "scene_spec.py"),
                            "--project", str(proj), "--check"],
                           capture_output=True, text=True)
        assert r.returncode == 1 and "没记指纹" in r.stdout, \
            f"没盖指纹的签收应该被拦：{r.stdout}"
        # 盖指纹 → 通过
        r2 = subprocess.run([sys.executable, str(HERE / "scene_spec.py"),
                             "--project", str(proj), "--sign", "sig_x"],
                            capture_output=True, text=True)
        assert r2.returncode == 0, f"--sign 失败：{r2.stderr}"
        r3 = subprocess.run([sys.executable, str(HERE / "scene_spec.py"),
                             "--project", str(proj), "--check"],
                            capture_output=True, text=True)
        assert r3.returncode == 0, f"盖完指纹应该通过：{r3.stdout}"
        # 改一个字节 → 签收作废
        p = proj / "scenes" / "sig_x.py"
        p.write_text(p.read_text() + "\n# touch\n", encoding="utf-8")
        r4 = subprocess.run([sys.executable, str(HERE / "scene_spec.py"),
                             "--project", str(proj), "--check"],
                            capture_output=True, text=True)
        assert r4.returncode == 1 and "已过期" in r4.stdout, \
            f"改代码后签收必须失效：{r4.stdout}"
        print("  ✅ G-04 签收绑定场景代码+事件指纹，改一字节即作废")


def test_g05_events_mismatch_hard_fail():
    """G-05：props.events 与任务单 audio.events 不一致 → 渲染硬拦，不再警告照渲。"""
    import render_hf
    with tempfile.TemporaryDirectory(prefix="g05-") as td:
        proj = Path(td)
        (proj / "scenes").mkdir()
        (proj / "scenes" / "sfx_x.json").write_text(json.dumps({
            "scene_id": "sfx_x", "kind": "causal", "purpose": "",
            "beats": {k: "t" for k in ("subject", "action", "change",
                                       "camera", "audience", "sound")},
            "claim_source": {"evidence": "e"},
            "audio": {"events": [{"t": 0.9, "name": "pop"}]},
            "review": {"visual": "ok", "audio": "ok", "truth": "ok",
                       "scene_sha1": "x", "events_sha1": "y"},
        }, ensure_ascii=False), encoding="utf-8")
        el = {"n": 1, "at": 3.0, "track": "overlay",
              "props": {"events": [{"t": 0.5, "name": "pop"}],
                        "scene_spec": "sfx_x"}}
        try:
            render_hf.gen_props_sfx(el, proj)
            raise AssertionError("不一致的事件表居然放行了")
        except SystemExit as e:
            assert "不一致" in str(e), f"拦截理由要说清楚：{e}"
        # 两边一致 → 正常编译
        el2 = {"n": 1, "at": 3.0, "track": "overlay",
               "props": {"events": [{"t": 0.9, "name": "pop"}],
                         "scene_spec": "sfx_x"}}
        evs = render_hf.gen_props_sfx(el2, proj)
        assert len(evs) == 1 and abs(evs[0]["t"] - 3.9) < 1e-6
        print("  ✅ G-05 音效事件与任务单不一致直接渲停，一致则正常编译")


def test_g06_no_unsourced_claims():
    """G-06：ideate 里不许有无来源数字/效果承诺（发表稿纪律）。"""
    src = (HERE / "ideate.py").read_text(encoding="utf-8")
    banned = ["90%的人都", "90%踩", "翻倍", "省一半", "保证", "百分百", "三万", "三千"]
    hit = [b for b in banned if b in src]
    assert not hit, f"ideate 还有无来源承诺：{hit}"
    # 负样本口径：金额型钩子（花三万/三千/数字+万）也是无来源数字——
    # 2026-11-15 对抗审查抓出 H03 原样带「三万/三千」，扫源码零命中≠实质清零
    import ideate
    money = re.compile(r"[0-9一二两三四五六七八九十百]+[万千百]")
    for hid, h in getattr(ideate, "HOOK_BANK", {}).items():
        tpl = str(h.get("tpl") or "")
        m = money.search(tpl)
        assert not m, f"钩子 {hid} 还有无来源金额：{m.group(0)}（{tpl}）"
    print("  ✅ G-06 ideate 钩子与兜底卡零无来源数字/承诺（含金额模式）")


def test_g07_manifest_no_cc0_fallback():
    """G-07：许可查无留痕 → manifest 如实标 unverified，绝不回退写死 CC0。"""
    import render_hf
    with tempfile.TemporaryDirectory(prefix="g07-") as td:
        proj = Path(td)
        (proj / "out").mkdir()
        # 一条不在任何带留痕库根里的假 BGM
        fake = Path(td) / "nolic" / "song.mp3"
        fake.parent.mkdir()
        fake.write_bytes(b"\x00" * 64)
        (proj / "bgm.json").write_text(json.dumps(
            {"source": "cc0", "title": "t", "file": str(fake)}), encoding="utf-8")
        out = render_hf.write_sfx_manifest(proj, [])
        m = json.loads(out.read_text())
        b = m.get("bgm") or {}
        assert b.get("licence") is None and b.get("source") == "unverified", \
            f"无留痕的 BGM 被写成了 CC0：{b}"
        assert "人工确认" in (b.get("note") or ""), "必须有商用前人工确认提示"
        assert "人工确认" in m.get("licence_summary", ""), "summary 必须如实计未核验"
        print("  ✅ G-07 manifest 无 CC0 回退，无留痕如实标 unverified")


def test_g03_sandbox_positive_deny():
    """G-03 正样本：seatbelt profile 真的拒网、真的拦工作区外写盘。

    2026-11-15 对抗审查：此前只有「无 sandbox-exec → exit 5」负样本，没有证明
    profile 生效的正样本。这里直接拿 _sandbox_profile 生成的同一份 profile 跑
    探针子进程：连 socket 必败、写 /Users 必败，探针输出 LEAKED 即失败。
    """
    import scene_sandbox
    if not shutil.which("sandbox-exec"):
        print("  · G-03 正样本跳过（本机无 sandbox-exec）")
        return
    with tempfile.TemporaryDirectory(prefix="g03p-") as td:
        scratch, out_dir = Path(td) / "w", Path(td) / "o"
        scratch.mkdir(); out_dir.mkdir()
        pf = scratch / "probe.sb"
        pf.write_text(scene_sandbox._sandbox_profile(scratch, out_dir))
        probe = scratch / "probe.py"
        probe.write_text(
            "import sys, pathlib\n"
            "mode = sys.argv[1]\n"
            "if mode == 'net':\n"
            "    import socket\n"
            "    s = socket.socket()\n"
            "    s.settimeout(2.0)\n"
            "    s.connect(('example.com', 80))  # 拒网时这里必抛\n"
            "elif mode == 'write':\n"
            "    pathlib.Path('/Users/cutskills-g03-probe-should-fail').write_text('x')\n"
            "print('LEAKED')\n", encoding="utf-8")
        for mode, why in (("net", "网络"), ("write", "写盘")):
            r = subprocess.run(["sandbox-exec", "-f", str(pf), sys.executable,
                                str(probe), mode], capture_output=True, text=True,
                               timeout=30)
            assert r.returncode != 0 and "LEAKED" not in r.stdout, \
                f"沙箱没拦住{why}：exit {r.returncode} · {r.stdout[:120]}"
        print("  ✅ G-03 正样本：seatbelt profile 实测拒网、拦工作区外写盘")


def main() -> int:
    tests = [test_g01_new_scene_routes, test_g02_no_dir_scan,
             test_g03_no_sandbox_exec_refuses, test_g03_sandbox_positive_deny,
             test_g04_sign_binds_hash, test_g05_events_mismatch_hard_fail,
             test_g06_no_unsourced_claims, test_g07_manifest_no_cc0_fallback]
    for t in tests:
        t()
    print(f"✅ R0 守卫 {len(tests)} 项全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
