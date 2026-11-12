#!/usr/bin/env python3
"""端到端自测：从零造一个小素材库，把 A→B→C→D 整条链跑一遍。

为什么要有它：现在的验收集全部依赖柒造那一库真素材 + 一个真项目。改一行 index_assets
或 render_film，要重新跑 153 分钟素材才知道坏没坏——所以没人会改完就跑，坏了也不知道，
一直坏到某一天出片才炸。

这个脚本造三支 8 秒的合成素材（macOS 的 `say` 念真句子 + 纯色底），带语音、带时长、
带真实转写，然后：

    A  索引（L0 探针 + L1 转写）→ 三支都要有 transcript
    B  建项目 → 写计划 → make_edl → check_edl（error 必须 0）→ 文字稿
    C  渲染（crf 30，缩到 30 秒以内，一分钟内出片）
    D  验收（不能有红）

跑一次约 1–3 分钟。**它证明的是「链路没断」，不是「片子好看」**——合成素材长得跟
工地素材一点关系都没有，节奏、冷暖、换介质这些全都验不出来。

    selftest.py [--keep]
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
from check_edl import token_bounds            # noqa: E402  只用它吸字界
PY = sys.executable

# 三句都要有真内容：ASR 要能转出来，check_edl 的字数才有意义
# 每句都要有**两个分句**：索引的噪声滤网会把「只有一句短语」的音频判成杂音
# （`len(phrases) < 2` 就丢），那是为 89 秒长口播调的阈值，短素材要绕开它。
CLIPS = [
    ("a_claim", "国产和进口的材料，差的不只是价格。我做了十年，这话我敢讲。", "0x1f3b57"),
    ("b_proof", "我们打了十个样板，三个厂家一起比。业主看的是手感，不是牌子。", "0x3b1f2f"),
    ("c_close", "所以别站队，按项目定。该用在哪，就用在哪。", "0x1f3b2a"),
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
    for name, text, color in CLIPS:
        wav = batch / f"{name}.aiff"
        # 注意：`say -v Flo`（系统列出的中文音色）在这台机器上产出 0.011s 的空音频，
        # 用默认音色反而念得对、ASR 也能转出来。别写死音色。
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
        r = sh(["ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s=540x960:d={max(3.0, dur):.2f}",
                "-i", str(wav), "-shortest",
                "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", str(mp4)])
        wav.unlink(missing_ok=True)
        ok &= step(f"造素材 {name}", mp4.exists() and mp4.stat().st_size > 0,
                   r.stderr.strip()[:80])
    return ok


def _dark_judge_negative() -> bool:
    """深底判据的负样本（T-005 复核④）：「代码画面深底占比 > 0.35」这条判据，
    必须能区分真代码画面和实拍帧——拿 ground-01 成片（纯实拍，无 gen）抽三帧，
    用与 verify_film 同一个 dark_ratio/median_of 量，必须低（会触发红）；
    再拿合成素材的纯深底帧量，必须高。两边都过才说明判据不是摆设。"""
    try:
        from verify_film import dark_ratio, median_of
        from PIL import Image
    except ImportError as e:
        print(f"     导入失败：{e}"); return False

    def _frame(film: Path, t: float, tag: str) -> Image.Image | None:
        out = Path(tempfile.mkdtemp(prefix="darkneg_")) / f"{tag}.png"
        r = sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
                "-i", str(film), "-frames:v", "1", str(out)])
        if r.returncode != 0 or not out.exists():
            return None
        return Image.open(out).convert("L")

    # 负样本主体：拿 ground-01 成片（纯实拍，无 gen）抽三帧，用与 verify_film
    # 同一个 dark_ratio/median_of 量——必须低（会触发红）；没有真语料时跳过
    # 这半边但明说，合成帧对照必须跑。
    g = Path.home() / "Downloads" / "柒造Video" / "素材" / "_cutskills" / "projects" \
        / "qizao-ground-01" / "out" / "film.mp4"
    if g.exists():
        vals = [dark_ratio(im) for im in (_frame(g, 10.0, "a"), _frame(g, 30.0, "b"),
                                          _frame(g, 55.0, "c")) if im]
        real = median_of(vals)
        print(f"     实拍三帧 dark 中位 = {real:.2f}（阈值 0.35，应判红）")
        if real is None or real > 0.35:
            return False
    else:
        print("     （无真语料，实拍负样本跳过——合成对照照跑）")

    # 对照组：深底代码画面帧 → 必须高（判据会放它绿）。
    # 用纯合成帧（PIL 画）：深橄榄底 + 一条品牌金亮带，模拟 gen 场景。
    w, h = 1080, 1920
    syn = Image.new("L", (w, h), 30)
    from PIL import ImageDraw
    d = ImageDraw.Draw(syn)
    d.rectangle([int(w * 0.42), 0, int(w * 0.56), h], fill=170)
    syn_val = dark_ratio(syn)
    print(f"     合成深底帧（含扫光亮带）dark = {syn_val:.2f}（应 > 0.35 判绿）")
    return syn_val > 0.35


def main() -> int:
    ap = argparse.ArgumentParser()
    # 默认目录按工位身份隔离（T-005 复核⑤）：用 git 分支名当工位标识，不用目录名——
    # 主仓和复审 worktree 可以同名（都叫 CutSkills），但分支不会重名。
    #（2026-11-11 实撞：两个 AI 同时跑 selftest 互删现场，假红「转写少一支/v007 凭空消失」）
    def _station() -> str:
        try:
            r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                               capture_output=True, text=True, timeout=5)
            b = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            b = ""
        # detached HEAD 时 git 返回 "HEAD"，所有 worktree 都一样——退回目录名
        b = "" if b == "HEAD" else b
        b = b or Path.cwd().name
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in b)
    ap.add_argument("--dir", default=f"/tmp/cutskills-selftest-{_station()}")
    ap.add_argument("--keep", action="store_true", help="跑完不删（想看成片时用）")
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

    # 预检：这条链路**离不开**转写引擎（`coli asr`）。没它就往下跑，
    # 只会在 ② 报「0 支有 phrases」，然后 ③④⑤ 连环假红——人会以为是代码坏了。
    # 2026-11-13 实际发生过：Cola 升级后 `coli` 从 PATH 上消失，
    # 自测一口气报出五条失败，没有一条是真代码问题。缺前置就明说，不硬跑。
    if not shutil.which("coli"):
        print("⛔ 转写引擎 `coli` 不在 PATH 上——这条链路没它就没有词级真值。")
        print("   受影响：自测 ②–⑤、test_hygiene_fixtures.py、任何新素材的索引。")
        print("   已有 ASR 缓存的素材不受影响（字幕/evidence/静音判据照常跑）。")
        return 2

    print("① 造合成素材库")
    must("三支素材", build_library(lib))

    print("② A 段 · 索引")
    r = sh([PY, str(ROOT / "scripts" / "index_assets.py"), "--library", str(lib),
            "--jobs", "2", "--l1-jobs", "1"])
    if r.returncode != 0:
        must(f"index_assets 退出码 {r.returncode}", False, (r.stderr or r.stdout)[-200:])
    idx_f = lib / "_cutskills" / "index.json"
    must("index.json", idx_f.exists())
    idx = json.loads(idx_f.read_text()) if idx_f.exists() else {}
    assets = idx.get("assets") or []
    must("三支都在索引里", len(assets) >= 3, f"{len(assets)} 支")
    with_tx = [x for x in assets if (x.get("transcript") or {}).get("phrases")]
    must("三支都有转写", len(with_tx) >= 3, f"{len(with_tx)} 支有 phrases")
    if not with_tx:
        print("".join(f"     {x['id']}: notes={x.get('notes')}\n" for x in assets))

    print("③ B 段 · 编导")
    r = sh([PY, str(ROOT / "scripts" / "init_project.py"), "--library", str(lib),
            "--intent", "自测：用三句话讲清国产和进口怎么选",
            "--slug", "selftest-01", "--audience", "业主",
            "--promise", "看完知道按项目定，而不是站队",
            "--duration", str(max(6, round(sum(  # 目标时长按素材实际能拼出来的算，写死会造假红
                (float(((x.get("transcript") or {}).get("phrases") or [{}])[-1].get("end") or 3.0))
                for x in assets if (x.get("transcript") or {}).get("phrases"))))),
            "--aspect", "9:16"])
    must("init_project", r.returncode == 0 and (proj / "intent.json").exists(),
         (r.stderr or r.stdout).strip()[-160:])

    # 计划按「每支一段」写，段落之间用 beat/role 交代结构
    segs = []
    roles = [("claim", 1, "开场放结论"), ("evidence", 1, "样板对比就是证据"),
             ("warrant", 1, "说清为什么这样选")]
    for (name, _, _), (role, beat, why) in zip(CLIPS, roles):
        rec = next((x for x in assets if name in x["id"]), None)
        if not rec:
            continue
        # 切点必须落在字界上：拿第一句的起点和最后一句的终点，别用固定 0.3
        # （check_edl 会因为「切在一个字中间」直接判 error，那是它该干的事）
        ph = (rec.get("transcript") or {}).get("phrases") or []
        t_in, t_out = 0.2, min(float(rec.get("duration") or 4.0) - 0.2, 5.0)
        if ph:
            # 切点吸到**字界**上：短语的起止和字的起止不是一回事，差一百多毫秒
            # check_edl 就判「切在一个字中间」。用 check_edl 自己的字表来吸，别另造一套。
            b = token_bounds(lib, rec["id"])
            if b:
                starts, _ = b
                t_in = min(starts, key=lambda x: abs(x - float(ph[0]["start"])))
                t_out = max([x for x in starts if x <= float(ph[-1]["end"]) + 0.05] or [t_in + 1.0])
            else:
                t_in = float(ph[0]["start"])
                t_out = float(ph[-1]["end"])
        t_in, t_out = round(t_in, 2), round(t_out, 2)
        if t_out - t_in < 1.0:
            t_out = round(t_in + 1.0, 2)
        segs.append({"asset": rec["id"], "in": t_in, "out": t_out,
                     "medium": "talking", "beat": beat, "role": role, "why": why})
    plan = {"beats": [{"n": 1, "title": "国产进口怎么选"}], "segments": segs}
    (proj / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))

    r = sh([PY, str(ROOT / "scripts" / "make_edl.py"), "--project", str(proj),
            "--plan", str(proj / "plan.json")])
    must("make_edl", r.returncode == 0 and (proj / "edl.json").exists(),
         (r.stderr or r.stdout).strip()[-200:])
    edl = json.loads((proj / "edl.json").read_text()) if (proj / "edl.json").exists() else {}
    must("EDL 有段", bool(edl.get("segments")), f"{len(edl.get('segments') or [])} 段")

    r = sh([PY, str(ROOT / "scripts" / "check_edl.py"), "--project", str(proj)])
    out = (r.stdout or "") + (r.stderr or "")
    errs = 0
    for ln in out.splitlines():
        if "error :" in ln or ln.startswith("  error"):
            errs += 1
    must("check_edl 零 error", errs == 0, f"{errs} 个 error")
    if errs:
        print(out[-800:])

    r = sh([PY, str(ROOT / "scripts" / "render_paper_edit.py"), "--project", str(proj)])
    must("文字稿", r.returncode == 0 and (proj / "paper-edit.md").exists())

    print("④ C 段 · 渲染（crf 30，为快）")
    r = sh([PY, str(ROOT / "scripts" / "render_film.py"), "--project", str(proj),
            "--library", str(lib), "--crf", "30"])
    film = proj / "out" / "film.mp4"
    must("成片", r.returncode == 0 and film.exists() and film.stat().st_size > 0,
         (r.stderr or r.stdout).strip()[-200:])

    print("⑤ D 段 · 验收")
    r = sh([PY, str(ROOT / "scripts" / "verify_film.py"), "--project", str(proj)])
    out = (r.stdout or "") + (r.stderr or "")
    must("验收无红", "🔴 0" in out or r.returncode == 0, out.strip().splitlines()[-2:][0]
         if out.strip() else "")
    for ln in out.splitlines():
        if "验收：" in ln:
            print(f"     {ln.strip()}")

    # 字幕判据必须跟验收一起跑。 2026-11-12 对账发现两套判据分家：
    # check_subs 三片全部「没过」（停留 3–5 条、助词孤行 2–3 条），
    # 而 verify_film 报 🟢26 —— 因为 verify_film 压根没有这两条。
    # SKILL.md 把 check_subs 列为项目级校验，但它不在 selftest 里，于是从来没人跑。
    must("字幕合规（check_subs）", sh([PY, str(ROOT / "scripts" / "check_subs.py"),
                                      "--project", str(proj)]).returncode == 0)
    must("卡准入（check_cards）", sh([PY, str(ROOT / "scripts" / "check_cards.py"),
                                     "--project", str(proj)]).returncode in (0, 1),
         "（无卡的项目返回 1，不算错）")
    must("边界审计（edge_audit）", sh([PY, str(ROOT / "scripts" / "edge_audit.py"),
                                      "--project", str(proj)]).returncode == 0)

    print("⑥ 文档自检（顺带跑，确保链路改动没有把文档甩在后面）")
    r = sh([PY, str(ROOT / "scripts" / "audit_skill.py"),
            "--project", str(proj), "--library", str(lib)])
    reds = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("🔴")]
    must("文档自检零红", not reds, f"{len(reds)} 条红")
    for ln in reds[:5]:
        print(f"     {ln.strip()[:150]}")

    print("⑦ 产线基准（顺带跑，确保 bench 量得出东西）")
    r = sh([PY, str(ROOT / "scripts" / "bench.py"),
            "--library", str(lib), "--project", str(proj)])
    out = (r.stdout or "") + (r.stderr or "")
    must("产线基准可量", "产线基准：" in out and r.returncode in (0, 1), out.strip()[-160:])
    for ln in out.splitlines():
        if ln.startswith("产线基准："):
            print(f"     {ln.strip()}")

    print("⑧ 看板 clips.ov 对账回归（T-010：overlay 不推歪主轨起点）")
    r = sh([PY, str(ROOT / "scripts" / "test_board_clips.py")])
    must("clips.ov 对账回归", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])

    print("⑨ 钩子策略真判定回归（T-006：passed 闭环与 7 组正负样本）")
    r = sh([PY, str(ROOT / "scripts" / "test_hooks_policy.py")])
    must("钩子策略真判定回归", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])

    print("⑩ 深底判据负样本（T-005 复核④：实拍帧必须被判红，背景纹理不能当证据）")
    ok10 = _dark_judge_negative()
    must("深底判据负样本", ok10, "实拍帧 dark_ratio 应 ≤ 0.35（触发红），代码画面帧应 > 0.35（绿）")

    print("⑪ PiP 判据负样本（T-012：缺键 / 贴回失败 / 数量不足）")
    r = sh([PY, str(ROOT / "scripts" / "test_pip_judge.py")])
    must("PiP 判据负样本", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])

    print("⑫ 品牌安全与第三方字样处置回归（T-007：FESTOOL/PPG 拦截与 decisions 留痕核销）")
    r = sh([PY, str(ROOT / "scripts" / "test_brand_safety.py")])
    must("品牌安全与第三方字样处置回归", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])

    print("⑭ 口播段音画同源（画面起点被清零＝嘴形全错，原案 2026-11-12）")
    r = sh([PY, str(ROOT / "scripts" / "test_av_lock.py")])
    must("口播段音画同源", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:])

    print("⑬ 仓库不夹带生成中间物（references/**/work/）")
    r = sh(["git", "-C", str(ROOT), "ls-files", "references"])
    bad = [ln for ln in (r.stdout or "").splitlines() if "/work/" in ln]
    must("仓库不夹带生成中间物", r.returncode == 0 and not bad,
         f"{len(bad)} 个 tracked 文件在 references/**/work/：" + "、".join(bad[:3])
         if bad else (r.stderr or r.stdout).strip()[-160:])

    secs = time.time() - t0
    print()
    if fails:
        print(f"❌ 自测没过（{secs:.0f}s）：{'、'.join(fails)}")
        print(f"   现场保留在 {base}，去看 work/ 下的中间件")
        return 1
    print(f"✅ 全链路通（{secs:.0f}s）· 成片 {film}")
    print("   它证明的是「链路没断」，不是「片子好看」——合成素材验不出节奏、冷暖、换介质。")
    if not a.keep:
        shutil.rmtree(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
