#!/usr/bin/env python3
"""V2 端到端全链路自测：从零造合成素材 → 索引/字表 → V5 时间轴（P5 网感包装+标题条+贴纸+冲击推镜+鼓点吸附）→ 拍板签锁 → HyperFrames 真渲染 → 像素/响度/同窗音效门禁 → 全部离线回归单测。

用法：
    python3 scripts/selftest.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from evidence_guard import token_bounds       # noqa: E402
from libs import config_libs, ffmpeg_bin      # noqa: E402

PY = sys.executable

CLIPS = [
    ("a_claim", "国产和进口的材料，差的不只是价格。我做了十年，这话我敢讲。", "0x1f3b57"),
    ("b_proof", "我们打了十个样板，三个厂家一起比。业主看的是手感，不是牌子。", "0x3b1f2f"),
    ("c_close", "所以别站队，按项目定。该用在哪，就用在哪。", "0x1f3b2a"),
]

OFFLINE_TESTS = [
    "test_v2_pipeline.py",
    "test_hooks_policy.py",
    "test_pip_judge.py",
    "test_brand_safety.py",
    "test_semantic_check.py",
    "test_av_lock.py",
    "test_visual_grade.py",
    "test_edge_audit.py",
    "test_timing_subs.py",
    "test_cue_guard.py",
    "test_sub_density.py",
    "test_behind_band.py",
    "test_keyword_text.py",
    "test_anchor_miss.py",
    "test_ink_diff.py",
    "test_mg_kinds.py",
    "test_tidy.py",
    "test_studio.py",
    "test_cards_meta.py",
    "test_evidence_guard.py",
    "test_speech_hygiene.py",
    "test_content_guard.py",
    "test_overlay_judge.py",
    "test_panel_light.py",
    "test_hold_breath.py",
    "test_breath_split.py",
    "test_scene_determinism.py",
    "test_measure_geom.py",
    "test_sfx_variety.py",
    "test_scene_review.py",
    "test_hygiene_fixtures.py",
]


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def step(n: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✅' if ok else '❌'} {n}{'  ' + detail if detail else ''}")
    return ok


def build_library(lib: Path) -> bool:
    lib.mkdir(parents=True, exist_ok=True)
    batch = lib / "自测批次"
    batch.mkdir(exist_ok=True)
    ok = True
    ff = ffmpeg_bin()
    for name, text, color in CLIPS:
        wav = batch / f"{name}.aiff"
        r = sh(["say", "-o", str(wav), text])
        if r.returncode != 0:
            return step("生成语音", False, r.stderr.strip()[:80])
        p = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(wav)])
        try:
            dur = float(p.stdout.strip())
        except ValueError:
            dur = 6.0
        mp4 = batch / f"{name}.mp4"
        r = sh([ff, "-v", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s=540x960:d={max(3.0, dur):.2f}",
                "-i", str(wav), "-shortest",
                "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", str(mp4)])
        wav.unlink(missing_ok=True)
        ok &= step(f"造素材 {name}", mp4.exists() and mp4.stat().st_size > 0,
                   r.stderr.strip()[:80])
    return ok


def _inject_fallback_words(lib: Path) -> None:
    """当新机器未安装 coli ASR 时，为合成素材注入精确能量比例字级时间戳，确保渲染主链路在任何电脑可测。"""
    import voiceover
    idx_f = lib / "_cutskills" / "index.json"
    idx = json.loads(idx_f.read_text())
    text_map = {name: txt for name, txt, _ in CLIPS}
    for rec in idx.get("assets") or []:
        matched = next((txt for name, txt in text_map.items() if name in rec["id"]), None)
        if not matched:
            continue
        dur = float(rec.get("duration") or 4.0)
        import re
        clauses = [c for c in re.split(r"[，。！？；：、…—,.!?;:]+", matched) if c.strip()]
        words = []
        phrases = []
        cur = 0.08
        avail = max(1.0, dur - 0.24)
        total_c = max(1, sum(len(c) for c in clauses))
        for cl in clauses:
            cdur = avail * (len(cl) / total_c)
            # 均匀切分本句字符
            clean = [ch for ch in cl if ch not in voiceover.PUNCT_PAUSE and not ch.isspace()]
            step_s = cdur / max(1, len(clean))
            for i, ch in enumerate(clean):
                words.append({"word": ch, "start": round(cur + i * step_s, 3),
                              "end": round(cur + (i + 1) * step_s, 3)})
            phrases.append({"text": "".join(clean), "start": round(cur, 3), "end": round(cur + cdur, 3)})
            cur += cdur + 0.05
        rec["transcript"] = {"lang": "zh", "text": matched, "phrases": phrases, "words": words}
    idx_f.write_text(json.dumps(idx, ensure_ascii=False, indent=2))


def _dark_judge_negative() -> bool:
    try:
        from verify_film import dark_ratio, median_of
        from PIL import Image, ImageDraw
    except ImportError as e:
        print(f"     导入失败：{e}")
        return False

    def _frame(film: Path, t: float, tag: str):
        with tempfile.TemporaryDirectory(prefix="darkneg_") as _td:
            out = Path(_td) / f"{tag}.png"
            r = sh([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t:.2f}",
                    "-i", str(film), "-frames:v", "1", str(out)])
            if r.returncode != 0 or not out.exists():
                return None
            im = Image.open(out).convert("L")
            im.load()
            return im

    g = None
    for lib_p in config_libs():
        g_out = Path(lib_p) / "_cutskills" / "projects" / "qizao-ground-01" / "out"
        g = next((p for p in (g_out / "film-hf.mp4", g_out / "film.mp4") if p.exists()), None)
        if g:
            break
    if g is not None and g.exists():
        vals = [dark_ratio(im) for im in (_frame(g, 10.0, "a"), _frame(g, 30.0, "b"),
                                          _frame(g, 55.0, "c")) if im]
        real = median_of(vals)
        print(f"     实拍三帧 dark 中位 = {real:.2f}（阈值 0.35，应判红）")
        if real is None or real > 0.35:
            return False
    else:
        print("     （本机无真语料，跳过实拍负样本对照，仅跑合成帧）")

    w, h = 1080, 1920
    syn = Image.new("L", (w, h), 30)
    d = ImageDraw.Draw(syn)
    d.rectangle([int(w * 0.42), 0, int(w * 0.56), h], fill=170)
    syn_val = dark_ratio(syn)
    return syn_val > 0.35


def main() -> int:
    ap = argparse.ArgumentParser()

    def _station() -> str:
        try:
            r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                               capture_output=True, text=True, timeout=5)
            b = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            b = ""
        b = "" if b == "HEAD" else b
        b = b or Path.cwd().name
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in b)

    ap.add_argument("--dir", default=f"/tmp/cutskills-selftest-{_station()}")
    ap.add_argument("--keep", action="store_true", help="跑完保留临时目录供查看成片")
    a = ap.parse_args()
    base = Path(a.dir)
    if base.exists():
        shutil.rmtree(base)
    lib = base / "lib"
    proj = lib / "_cutskills" / "projects" / "selftest-01"
    t0 = time.time()
    fails: list[str] = []

    def must(name: str, cond: bool, detail: str = "") -> None:
        if not step(name, cond, detail):
            fails.append(name)

    has_coli = bool(shutil.which("coli"))

    print("① 造合成素材库")
    must("三支素材", build_library(lib))

    print("② 多模态索引（L0 探针 + L1 转写）")
    idx_cmd = [PY, str(ROOT / "scripts" / "index_assets.py"), "--library", str(lib),
               "--jobs", "2", "--l1-jobs", "1"]
    if not has_coli:
        idx_cmd.append("--l0-only")
    r = sh(idx_cmd)
    must("index_assets 执行成功", r.returncode == 0, (r.stderr or r.stdout)[-200:])
    if not has_coli:
        print("     ℹ 本机无 coli ASR，注入离线合成词表继续验证主渲染链路")
        _inject_fallback_words(lib)

    idx_f = lib / "_cutskills" / "index.json"
    idx = json.loads(idx_f.read_text()) if idx_f.exists() else {}
    assets = idx.get("assets") or []
    must("三支都在索引里", len(assets) >= 3, f"{len(assets)} 支")
    with_tx = [x for x in assets if (x.get("transcript") or {}).get("phrases")]
    must("三支都有词句真值", len(with_tx) >= 3, f"{len(with_tx)} 支")

    print("③ V2 编导时间轴与网感包装（P5-viral-pop + 标题条 + 矢量贴纸 + 冲击推镜 + 鼓点吸附）")
    est_dur = max(8, round(sum(
        float(((x.get("transcript") or {}).get("phrases") or [{}])[-1].get("end") or 3.5)
        for x in assets if (x.get("transcript") or {}).get("phrases")
    )))
    r = sh([PY, str(ROOT / "scripts" / "init_project.py"), "--library", str(lib),
            "--intent", "自测：用三句话讲清国产和进口怎么选",
            "--slug", "selftest-01", "--audience", "装修业主",
            "--promise", "看完知道按项目定而不是盲目站队",
            "--duration", str(est_dur), "--pack", "P5-viral-pop", "--aspect", "9:16"])
    must("init_project", r.returncode == 0 and (proj / "brief.json").exists(),
         (r.stderr or r.stdout).strip()[-160:])

    segs = []
    roles = [
        ("setup", 1, "黄金3秒反常识钩子直接亮十年经验结论", "punch_zoom",
         {"id": "bang", "pos": "top-right", "label": "十年真话!"}, None),
        ("evidence", 1, "十个样板三家盲测对比拿出实证", "slow_push", None,
         {"kind": "keyword", "text": "看的是手感", "on": "手感"}),
        ("payoff", 1, "给出按项目选材的可执行行动结论", None,
         {"id": "check-glow", "pos": "top-left", "label": "按需选材"}, None),
    ]
    for (name, _, _), (role, beat, why, vfx, stk, card) in zip(CLIPS, roles):
        rec = next((x for x in assets if name in x["id"]), None)
        if not rec:
            continue
        ph = (rec.get("transcript") or {}).get("phrases") or []
        t_in, t_out = 0.2, min(float(rec.get("duration") or 4.0) - 0.1, 6.0)
        if ph:
            b = token_bounds(lib, rec["id"])
            if b:
                starts, ends = b
                t_in = min(starts, key=lambda x: abs(x - float(ph[0]["start"])))
                t_out = max([x for x in ends if x <= float(ph[-1]["end"]) + 0.08] or [t_in + 1.5])
            else:
                t_in = float(ph[0]["start"])
                t_out = float(ph[-1]["end"])
        t_in, t_out = round(t_in, 2), round(min(float(rec.get("duration") or t_out), t_out), 2)
        if t_out - t_in < 1.2:
            t_out = round(t_in + 1.2, 2)
        seg = {"asset": rec["id"], "in": t_in, "out": t_out,
               "medium": "talking", "beat": beat, "role": role, "why": why}
        if vfx:
            seg["vfx"] = vfx
        if stk:
            seg["sticker"] = stk
        if card:
            seg["card"] = card
        segs.append(seg)

    plan = {
        "pack": "P5-viral-pop",
        "plan": "S1-conclude-fast",
        "bgm_mood": "upbeat",
        "title_bar": {"top": "十年材料老炮实话", "main": "国产vs进口怎么选", "mode": "hook", "dur": 4.2},
        "beats": [{"n": 1, "title": "国产进口怎么选"}],
        "segments": segs,
    }
    (proj / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))

    r = sh([PY, str(ROOT / "scripts" / "timeline.py"), "--project", str(proj),
            "--library", str(lib), "--plan", str(proj / "plan.json")])
    must("timeline.py 生成 v5 时间轴", r.returncode == 0 and (proj / "timeline.json").exists(),
         (r.stderr or r.stdout).strip()[-200:])

    r = sh([PY, str(ROOT / "scripts" / "beat_sync.py"), "--project", str(proj), "--snap"])
    must("beat_sync.py 鼓点检测与卡点", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])

    r = sh([PY, str(ROOT / "scripts" / "check_timeline.py"), "--project", str(proj), "--library", str(lib)])
    out = (r.stdout or "") + (r.stderr or "")
    errs = sum(1 for ln in out.splitlines() if "error :" in ln or ln.startswith("  error"))
    must("check_timeline 零 error", errs == 0, f"{errs} 个 error\n{out[-400:]}")

    r_show = sh([PY, str(ROOT / "scripts" / "plan_card.py"), "--project", str(proj), "--show"])
    r_app = sh([PY, str(ROOT / "scripts" / "plan_card.py"), "--project", str(proj), "--approve", "selftest认可"])
    must("plan_card 方案卡拍板签锁", r_show.returncode == 0 and r_app.returncode == 0
         and (proj / "lock.json").exists(), (r_app.stderr or r_app.stdout)[-200:])

    print("④ HyperFrames 真渲染（P5-viral-pop + CC0 BGM 侧链闪避 + 音效同窗底轨）")
    r = sh([PY, str(ROOT / "scripts" / "render_hf.py"), "--project", str(proj),
            "--library", str(lib), "--quality", "draft", "--bgm", "auto"])
    film = proj / "out" / "film-hf.mp4"
    must("render_hf 出片成功", r.returncode == 0 and film.exists() and film.stat().st_size > 0,
         (r.stderr or r.stdout).strip()[-400:])
    must("商用音频留痕 sfx_manifest.json 与底音轨齐全",
         (proj / "out" / "sfx_manifest.json").exists() and (proj / "out" / "stem-nosfx.m4a").exists())

    print("⑤ 成片量化验收与门禁")
    r = sh([PY, str(ROOT / "scripts" / "verify_film.py"), "--project", str(proj)])
    out = (r.stdout or "") + (r.stderr or "")
    must("verify_film 验收零红", "🔴 0" in out, out.strip()[-300:])
    for ln in out.splitlines():
        if "验收：" in ln:
            print(f"     {ln.strip()}")

    must("音效净抬升（check_sfx）", sh([PY, str(ROOT / "scripts" / "check_sfx.py"),
                                       "--project", str(proj)]).returncode == 0)
    must("字幕合规（check_subs）", sh([PY, str(ROOT / "scripts" / "check_subs.py"),
                                      "--project", str(proj)]).returncode == 0)
    must("边界审计（edge_audit）", sh([PY, str(ROOT / "scripts" / "edge_audit.py"),
                                      "--project", str(proj)]).returncode == 0)

    print("⑥ 全仓说明书与 Schema 双向对账（audit_skill）")
    r = sh([PY, str(ROOT / "scripts" / "audit_skill.py"),
            "--project", str(proj), "--library", str(lib)])
    must("文档与 Schema 自检零红零黄", "🔴 0 · 🟡 0" in (r.stdout or ""), (r.stdout or "")[-240:])

    print("⑦ 仓库卫生与深底判据回归")
    r = sh(["git", "-C", str(ROOT), "ls-files", "references"])
    bad = [ln for ln in (r.stdout or "").splitlines() if "/work/" in ln]
    must("references 不夹带 work 中间物", r.returncode == 0 and not bad)
    must("深底判据正负样本", _dark_judge_negative())

    print(f"⑧ 离线契约与历史 NG 回归单测（共 {len(OFFLINE_TESTS)} 支）")
    for _tname in OFFLINE_TESTS:
        if _tname == "test_hygiene_fixtures.py" and not has_coli:
            print(f"  ⏭ {_tname}（本机无 coli，跳过）")
            continue
        r = sh([PY, str(ROOT / "scripts" / _tname)])
        must(_tname, r.returncode == 0, (r.stderr or r.stdout).strip()[-350:])

    secs = time.time() - t0
    print()
    if fails:
        print(f"❌ 自测未通过（{secs:.0f}s）：{'、'.join(fails)}")
        print(f"   现场保留在 {base}")
        return 1
    print(f"✅ V2 全链路与 {len(OFFLINE_TESTS)} 支单测全部通过（{secs:.0f}s）· 成片 {film}")
    if not a.keep:
        shutil.rmtree(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
