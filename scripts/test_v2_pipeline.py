#!/usr/bin/env python3
"""V2 核心模块离线契约单测：选题引擎 / AI 配音字表 / 鼓点吸附 / 矢量贴纸 / 内置 CC0 兜底 / 同窗音效门禁。"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import beat_sync        # noqa: E402
import cc0_lib          # noqa: E402
import check_sfx        # noqa: E402
import evidence_guard   # noqa: E402
import ideate           # noqa: E402
import stickers         # noqa: E402
from libs import ffmpeg_bin  # noqa: E402

PY = sys.executable


def _write_wav(path: Path, samples: list[float], sr: int = 16000) -> None:
    buf = bytearray()
    for x in samples:
        v = max(-32767, min(32767, int(round(x * 32767.0))))
        buf += struct.pack("<h", v)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


def main() -> int:
    fails: list[str] = []

    def must(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✅' if cond else '❌'} {name}" + (f" ({detail})" if detail and not cond else ""))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory(prefix="cutskills-v2test-") as td:
        base = Path(td)
        lib = base / "lib"
        proj = lib / "_cutskills" / "projects" / "v2-demo"
        proj.mkdir(parents=True, exist_ok=True)

        # 1) ideate.py：双流向 3 提案 + 10 钩子库 + 拍片卡 + 转 plan.json
        print("① 上游选题创作引擎（ideate.py）")
        must("十大钩子库齐全", len(ideate.HOOK_BANK) >= 10)
        r = subprocess.run(
            [PY, str(HERE / "ideate.py"), "--project", str(proj),
             "--topic", "三百万装修六百平茶楼怎么避坑", "--persona", "二十年餐饮老炮",
             "--pick", "1", "--hook", "1", "--guide"],
            capture_output=True, text=True,
        )
        must("ideate 生成提案与拍片卡", r.returncode == 0 and (proj / "ideation.json").exists()
             and (proj / "shoot-guide.md").exists(), r.stderr[-200:])
        idoc = json.loads((proj / "ideation.json").read_text())
        opts = idoc.get("proposals") or []
        streams = {o.get("id") for o in opts}
        must("覆盖漏斗流与水桶流三套结构", streams == {"funnel-A1", "bucket-A2", "bucket-A3"}, str(streams))

        # 2) voiceover.py：离线 say 合成 + 句首尾裁静音 + index.json 字级真值注册
        print("② AI 配音与字级时间戳直通（voiceover.py → evidence_guard / render_hf）")
        r_vo = subprocess.run(
            [PY, str(HERE / "voiceover.py"), "--project", str(proj), "--library", str(lib),
             "--lines", "花了三百万装茶楼，三分之一全给了厨房。|先保出品再谈装修。",
             "--engine", "say"],
            capture_output=True, text=True,
        )
        vo_aid = f"_vo/{proj.name}.wav"
        must("voiceover 合成并入库", r_vo.returncode == 0, (r_vo.stderr or r_vo.stdout)[-200:])
        chars = evidence_guard.load_chars(lib, vo_aid)
        must("evidence_guard.load_chars 读出三元组逐字时间戳",
             len(chars) >= 18 and len(chars[0]) == 3 and chars[0][1] < chars[-1][2],
             f"chars={len(chars)}")

        r_tp = subprocess.run(
            [PY, str(HERE / "ideate.py"), "--project", str(proj),
             "--to-plan", "--vo-asset", vo_aid],
            capture_output=True, text=True,
        )
        pdoc = json.loads((proj / "plan.json").read_text()) if (proj / "plan.json").exists() else {}
        segs = pdoc.get("segments") or []
        must("ideate --to-plan 产出带 title_bar/punch_zoom 的分镜",
             r_tp.returncode == 0 and bool(pdoc.get("title_bar"))
             and bool(segs) and segs[0].get("vfx") == "punch_zoom")

        # 3) stickers.py：16 款矢量贴纸 XML 合法性与包装收集器
        print("③ 16 款矢量动态情绪贴纸与标题条（stickers.py）")
        must("内置贴纸 ≥ 16 款", len(stickers.STICKER_CATALOG) >= 16)
        svg_ok = True
        for sid, meta in stickers.STICKER_CATALOG.items():
            try:
                root = ET.fromstring(meta["svg"])
                if root.attrib.get("viewBox") != "0 0 128 128":
                    svg_ok = False
            except ET.ParseError:
                svg_ok = False
        must("全部贴纸 SVG 通过 XML 解析且视口统一 128×128", svg_ok)
        pkg = stickers.collect_packaging(
            {"title_bar": {"top": "避坑指南", "main": "300万茶楼真账", "mode": "hook", "dur": 4.0},
             "stickers": [{"id": "fire-hot", "at": 3.5, "dur": 1.8, "pos": "top-left", "label": "干货"}]},
            [{"n": 1, "_start": 0.0, "_dur_q": 4.0,
              "vfx": "punch_zoom",
              "sticker": {"id": "bang", "pos": "top-right", "label": "重点!"}}],
            total=8.0,
        )
        must("collect_packaging 生成标题条/贴纸/VFX/配对音效",
             'id="title-bar"' in pkg["html"] and 'id="stk-02"' in pkg["html"]
             and "vfx-flash" in pkg["html"] and len(pkg["sfx"]) >= 4)

        # 4) cc0_lib.py：剥离外部环境变量与 config 时自动回退随仓内置 CC0 包
        print("④ 跨机器零配置内置 CC0 音乐/音效兜底（cc0_lib.py）")
        env_clean = {k: v for k, v in os.environ.items()
                     if k not in ("CUTSKILLS_BGM_LIB", "CUTSKILLS_SFX_LIB")}
        r_cc0 = subprocess.run(
            [PY, "-c",
             "import sys, pathlib; sys.path.insert(0, 'scripts'); import libs; "
             "libs.CONFIG_PATH = pathlib.Path('/nonexistent/config.json'); "
             "import cc0_lib; "
             "b = cc0_lib.pick_bgm('upbeat', need_s=10); "
             "s = cc0_lib.pick_sfx('whoosh', nth=1); "
             "assert b and pathlib.Path(b['path']).exists() and (cc0_lib.licence_of(b['path']) or '').startswith('CC0'); "
             "assert s and pathlib.Path(s['path']).exists() and (cc0_lib.licence_of(s['path']) or '').startswith('CC0'); "
             "print('OK')"],
            cwd=str(REPO), env=env_clean, capture_output=True, text=True,
        )
        must("无外部库时自动命中 assets/audio 且带 CC0 许可证明",
             r_cc0.returncode == 0 and "OK" in r_cc0.stdout, r_cc0.stderr[-200:])

        # 5) beat_sync.py：真实内置 BGM 鼓点检测 + 口播锁死同源铁律
        print("⑤ BGM 鼓点检测与弹性切点吸附（beat_sync.py）")
        bgm_files = sorted((REPO / "assets" / "audio" / "bgm").rglob("*.mp3"))
        must("内置 BGM 存在", len(bgm_files) >= 5)
        binfo = beat_sync.detect_beats(bgm_files[0])
        must("检出有效 BPM 与鼓点序列",
             60 <= binfo["bpm"] <= 185 and len(binfo["beats"]) >= 10,
             f"bpm={binfo['bpm']}, beats={len(binfo['beats'])}")
        fake_tl = {
            "elements": [
                {"n": 1, "kind": "clip", "track": "video", "medium": "talking", "in": 1.00, "out": 3.42},
                {"n": 2, "kind": "gen", "track": "video", "dur": 2.50, "template": "keyword"},
                {"n": 3, "kind": "clip", "track": "video", "medium": "broll", "in": 0.50, "out": 3.00},
            ]
        }
        new_tl, stats = beat_sync.snap_timeline_to_beats(fake_tl, [2.50, 5.02, 7.50], max_shift_s=0.18)
        e1, e2 = new_tl["elements"][0], new_tl["elements"][1]
        must("口播段时长分毫不改（严守声画同源），代码段吸附鼓点",
             (e1["in"], e1["out"]) == (1.00, 3.42) and abs(e2["dur"] - 2.60) < 0.02 and stats["snapped"] >= 1,
             f"e1={e1}, e2={e2}, stats={stats}")

        # 6) check_sfx.py：同窗相减根治 T-059（正样本不受 t+1.0s 强口播干扰，负样本哑音必拦）
        print("⑥ 同窗净抬升音效门禁正负样本回归（check_sfx.py · T-059）")
        sr = 16000
        n_samp = sr * 4
        bed = []
        film = []
        for i in range(n_samp):
            t = i / sr
            base_v = 0.35 * math.sin(2 * math.pi * 1100 * t) if 1.8 <= t <= 2.6 else 0.015 * math.sin(2 * math.pi * 1100 * t)
            sfx_v = 0.18 * math.sin(2 * math.pi * 950 * t) if 0.99 <= t <= 1.08 else 0.0
            bed.append(base_v)
            film.append(base_v + sfx_v)
        sfx_proj = base / "sfx_proj"
        (sfx_proj / "out").mkdir(parents=True)
        bed_wav = base / "bed.wav"
        film_wav = base / "film.wav"
        _write_wav(bed_wav, bed, sr)
        _write_wav(film_wav, film, sr)
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(bed_wav),
                        str(sfx_proj / "out" / "stem-nosfx.m4a")], check=True)
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x568:d=4",
                        "-i", str(film_wav), "-shortest", "-c:v", "libx264", "-c:a", "aac",
                        str(sfx_proj / "out" / "film-hf.mp4")], check=True)
        (sfx_proj / "out" / "sfx.json").write_text(json.dumps({"events": [{"t": 1.00, "name": "pop"}]}))
        r_sfx = subprocess.run([PY, str(HERE / "check_sfx.py"), "--project", str(sfx_proj)],
                               capture_output=True, text=True)
        must("同窗相减正样本通过（不受 t+1.0s 强口播干扰）",
             r_sfx.returncode == 0, (r_sfx.stdout + r_sfx.stderr)[-300:])
        # 负样本：把成片换成没加音效的底音，必须判红拦截
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x568:d=4",
                        "-i", str(bed_wav), "-shortest", "-c:v", "libx264", "-c:a", "aac",
                        str(sfx_proj / "out" / "film-hf.mp4")], check=True)
        r_sfx_neg = subprocess.run([PY, str(HERE / "check_sfx.py"), "--project", str(sfx_proj)],
                                   capture_output=True, text=True)
        must("同窗相减负样本（漏混音效）必判红拦截", r_sfx_neg.returncode == 1)

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
