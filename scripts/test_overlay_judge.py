#!/usr/bin/env python3
"""T-019 回归：check_overlay 的三条判据必须判红，且**不许误杀合法卡**。

第五轮对抗式审查的病是「8 道脚本 0 error、成片压脸又糊菜」——
判据只有一条路能证明自己有用：**给负样本，它红了**。
这里起四个迷你项目，直接跑 `check_overlay.py` 的命令行：

  A 压脸            → ① error（IoU ≥ 2%）
  B 口播格整屏暗场   → ② error（talking + 人占 37%）
  C 重卡与字幕同屏   → ③ error（suppressed 里没有留痕）
  D 合法（对照）     → 0 error（避脸 + 有留痕 + 只挂角）

用法：python3 scripts/test_overlay_judge.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE / "check_overlay.py"
W, H = 1080, 1920
FACE = [0.39, 0.28, 0.23, 0.13]          # 口播近景里的一张脸（Vision 归一化读数）
DUR = 4.0
CARD = (0.6, 4.0)                        # 卡的时间窗


def build(root: Path, *, box, mode, plate, cues, suppressed, cover=0.37,
          medium="talking", dur=DUR) -> Path:
    """造一个最小可判的项目：cards.json / subs.json / timeline.json / 人脸 / 遮罩。"""
    from PIL import Image, ImageDraw
    (root / "out").mkdir(parents=True, exist_ok=True)
    (root / "hf" / "clips").mkdir(parents=True, exist_ok=True)
    (root / "hf" / "work" / "faces").mkdir(parents=True, exist_ok=True)
    mdir = root / "hf" / "work" / "pstats" / "clipX"
    mdir.mkdir(parents=True, exist_ok=True)
    clip = root / "hf" / "clips" / "clipX.mp4"
    clip.write_bytes(b"\0" * 64)
    (root / "hf" / "work" / "faces" / "clipX.json").write_text(json.dumps(
        [{"t": 0.5, "x": FACE[0], "y": FACE[1], "w": FACE[2], "h": FACE[3], "conf": 0.87},
         {"t": 1.5, "x": FACE[0], "y": FACE[1], "w": FACE[2], "h": FACE[3], "conf": 0.88}]))
    # 人物遮罩：画一块占总面积 = cover 的白，量出来的就是这个人占比
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    bw = int(W * 0.9)
    bh = int((cover * W * H) / max(1, bw))
    d.rectangle([int(W * 0.05), H - bh - 40, int(W * 0.05) + bw, H - 40], fill=255)
    im.save(mdir / "f0000.png")
    (mdir / "_stamp.json").write_text(json.dumps({"mtime": clip.stat().st_mtime + 60}))

    (root / "timeline.json").write_text(json.dumps({
        "version": 4, "pack": "P4-canton-warm", "style_pack": "P4-canton-warm",
        "elements": [{"n": 1, "kind": "clip", "medium": medium, "asset": "a.mov",
                      "in": 0.0, "out": dur, "dur": dur,
                      "card": {"kind": "stat", "on": "六百平"}}]}))
    (root / "out" / "cards.json").write_text(json.dumps({"cards": [{
        "mg": "mg-01", "seg": 1, "kind": "stat", "text": "600㎡",
        "start": CARD[0], "end": CARD[1],
        "layout": {"mode": mode, "box": box, "plate": plate,
                   "box_px": [int(box[0] * W), int(box[1] * H),
                              int(box[2] * W), int(box[3] * H)],
                   "plate_px": [int(plate[0] * W), int(plate[1] * H),
                                int(plate[2] * W), int(plate[3] * H)],
                   "blur_px": 22 if mode == "focus_blur" else 0,
                   "face_refs": [{"clip": "clipX", "offset": 0.0, "dur": dur}],
                   "face_src": "vision", "cover": cover, "media": [medium]},
    }], "engine": "hyperframes"}))
    (root / "out" / "subs.json").write_text(json.dumps({
        "cues": cues, "suppressed": suppressed, "subtitle_suppressed": False,
        "subtitle_system": "single-line", "band": [1034, 1554]}))
    return root


def run(proj: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CLI), "--project", str(proj)],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


CUE = {"start": 1.4, "end": 2.6, "text": "我花咗三百万喺金湾开咗间六百平嘅点赞茶楼"}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="t019-overlay-"))
    fails = []

    # A 压脸：卡框正落在脸上（底衬跟卡走，不是整屏）
    a = build(tmp / "A", box=[0.30, 0.28, 0.52, 0.22], mode="focus_blur",
              plate=[0.0, 0.24, 1.0, 0.30], cues=[CUE],
              suppressed=[{"card": "mg-01", "kind": "stat",
                           "cue": {"start": CUE["start"], "end": CUE["end"],
                                   "text": CUE["text"]}, "reason": "测试"}])
    rc, out = run(a)
    print("── A 压脸 ────────────────")
    print(out.strip())
    if rc == 0 or "压脸" not in out:
        fails.append("A 压脸没有判红")

    # B 口播格上整屏暗场（人占 37%）
    b = build(tmp / "B", box=[0.04, 0.56, 0.8, 0.34], mode="cutaway",
              plate=[0.0, 0.0, 1.0, 1.0], cues=[CUE],
              suppressed=[{"card": "mg-01", "kind": "stat",
                           "cue": {"start": CUE["start"], "end": CUE["end"],
                                   "text": CUE["text"]}, "reason": "测试"}])
    rc, out = run(b)
    print("── B 口播上整屏暗场 ──────")
    print(out.strip())
    if rc == 0 or "整屏暗场" not in out:
        fails.append("B 口播整屏暗场没有判红")

    # C 重卡与字幕同屏（suppressed 里没有这条留痕）
    c = build(tmp / "C", box=[0.04, 0.58, 0.62, 0.28], mode="corner",
              plate=[0.02, 0.56, 0.66, 0.32], cues=[CUE], suppressed=[])
    rc, out = run(c)
    print("── C 重卡撞字幕 ──────────")
    print(out.strip())
    if rc == 0 or "退字幕" not in out:
        fails.append("C 重卡撞字幕没有判红")

    # D 合法对照：右下角挂角、避脸、字幕已挂起留痕 → 必须一条 error 都没有
    d = build(tmp / "D", box=[0.06, 0.62, 0.55, 0.24], mode="corner",
              plate=[0.04, 0.60, 0.59, 0.28], cues=[CUE],
              suppressed=[{"card": "mg-01", "kind": "stat",
                           "cue": {"start": CUE["start"], "end": CUE["end"],
                                   "text": CUE["text"]},
                           "overlap_s": 1.2, "reason": "重型 MG 卡在场，同期声字幕挂起"}])
    rc, out = run(d)
    print("── D 合法对照 ────────────")
    print(out.strip())
    if rc != 0 or "error 0" not in out:
        fails.append(f"D 合法卡被误杀（rc={rc}）")

    if fails:
        print("❌ 叠加态判据负样本没抓住：" + "；".join(fails))
        return 1
    print("✅ 叠加态判据：压脸 / 口播整屏暗场 / 重卡撞字幕 三红，合法卡零误杀"
          f"（夹具 {tmp}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
