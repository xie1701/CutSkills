#!/usr/bin/env python3
"""BRIEF 两步的回归：① 手机 VFR 素材 → 恒定帧率（音频逐样本不动）；② 拍摄任务卡六项字段 + 简笔示意。

为什么要这几条断言（每条都能挂掉一个真事）：

  ① VFR 判定靠实测帧间隔与 r/avg 帧率，不靠容器标记——所以先造一条**真 VFR**（不同时长帧 +
     非均匀时间戳），再验判据认得出来；CFR 素材必须被判 cfr 且**不被误转**。
  ② 音频保真是硬要求：转前后把音频解码成 PCM 逐样本比（字节数与 sha256 都要一致），
     任何重采样/重编码都会当场露馅。
  ③ 时长漂移只能在 1 帧内：超了说明重定时算法吃掉了内容，那不是「规整」是「重剪」。
  ④ `--dry-run` 一个文件都不许写（连 `_cfr/` 目录都不建）。
  ⑤ 拍摄卡每镜六项字段齐全、景别按台词内容分，不许整张卡都是「中景」。
  ⑥ 简笔示意要真出图、尺寸对、`shoot-guide.md` 里每镜都能对上一张。

跑法：
    source scripts/runtime.sh env && python3 scripts/test_normalize.py
（PIL / ffmpeg 都从 runtime.sh 的环境里来；没有它们这些断言本来就跑不成。）
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "vendor"))

import ideate                       # noqa: E402
import normalize as N               # noqa: E402
import shoot_diagram               # noqa: E402
from libs import ffmpeg_bin         # noqa: E402

FF = ffmpeg_bin()
PY = sys.executable
SCRIPTS = ROOT / "scripts"
DIAG_W, DIAG_H = shoot_diagram.W, shoot_diagram.H

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------- 造样本

def build_vfr(path: Path, dur: float = 3.0) -> tuple[bool, str]:
    """真 VFR：每 3 帧取 1 帧（真实帧率掉到 10fps），再把每帧时间戳按 sin(N) 抖动 ±20ms。
    `-fps_mode vfr` 保证时间戳原样写进容器，不会被补成恒定帧率。"""
    r = sh([FF, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s=360x640:d={dur}:r=30",
            "-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:r=44100:a=0.2",
            "-vf", "select='not(mod(n,3))',setpts=PTS+0.02*sin(N)",
            "-map", "0:v", "-map", "1:a", "-fps_mode", "vfr",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            str(path)])
    return r.returncode == 0 and path.exists() and path.stat().st_size > 0, \
        (r.stderr or "")[-200:]


def build_cfr(path: Path, dur: float = 2.0) -> tuple[bool, str]:
    r = sh([FF, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s=360x640:d={dur}:r=30",
            "-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:r=44100:a=0.2",
            "-map", "0:v", "-map", "1:a", "-fps_mode", "cfr", "-r", "30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            str(path)])
    return r.returncode == 0 and path.exists() and path.stat().st_size > 0, \
        (r.stderr or "")[-200:]


def independent_interval_jitter(src: Path) -> dict:
    """**独立**量一遍帧间隔（不复用 normalize 的采样逻辑）：测试要能抓出判据自己的错。
    整条片子读帧——只有 3 秒，代价可接受。"""
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "frame=pts_time", "-of", "csv=p=0", str(src)])
    ts = []
    for line in (r.stdout or "").splitlines():
        v = line.strip().rstrip(",")
        try:
            ts.append(float(v))
        except ValueError:
            continue
    ds = [b - a for a, b in zip(ts, ts[1:]) if b - a > 1e-6]
    if len(ds) < 4:
        return {"n": len(ds), "jitter": 0.0, "median_ms": 0.0}
    med = sorted(ds)[len(ds) // 2]
    off = sum(1 for d in ds if abs(d - med) / med > 0.02)
    return {"n": len(ds), "jitter": off / len(ds), "median_ms": med * 1000}


def audio_samples(src: Path) -> tuple[str, int]:
    """音频指纹（测试自己算，不用被测代码那条路）：解码成 s16le PCM 逐字节 hash。"""
    r = subprocess.run([FF, "-v", "error", "-i", str(src), "-map", "0:a:0",
                        "-c:a", "pcm_s16le", "-f", "s16le", "-"],
                       capture_output=True)
    data = r.stdout or b""
    return hashlib.sha256(data).hexdigest(), len(data)


def tree_of(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")} if root.exists() else set()


# ---------------------------------------------------------------- ① 素材规整

def test_normalize(base: Path) -> None:
    print("① 素材规整：VFR → CFR，音频逐样本不变")
    lib = base / "lib_vfr"
    lib.mkdir(parents=True)
    vfr = lib / "vfr.mp4"
    ok, err = build_vfr(vfr)
    must("造出真 VFR 样本（不同时长帧 + 非均匀时间戳）", ok, err)
    if not ok:
        return

    indep = independent_interval_jitter(vfr)
    must("样本本身确实是 VFR（独立测量：≥5 帧间隔脱离恒定网格）",
         indep["jitter"] >= 0.05 and indep["n"] >= 5,
         f"独立读数 {indep}（本机 ffmpeg 造不出 VFR，后面几条无意义）")

    j = N.judge(vfr)
    basis = j.get("basis") or {}
    must("判据识别为 vfr", j.get("verdict") == "vfr", f"实际 {j.get('verdict')}：{j.get('reason')}")
    must("判定依据含 r/avg 帧率与帧间隔统计",
         all(k in basis for k in ("r_fps", "avg_fps", "readings", "drift")),
         f"basis 键：{sorted(basis)}")
    r_fps, avg_fps = basis.get("r_fps"), basis.get("avg_fps")
    must("原片 r_frame_rate 与 avg_frame_rate 确实不一致（不是只看容器标记）",
         r_fps and avg_fps and abs(r_fps - avg_fps) > N.FPS_MISMATCH,
         f"r={r_fps} avg={avg_fps}")

    # --dry-run：一个文件都不许写
    dry_lib = base / "lib_dry"
    dry_lib.mkdir(parents=True)
    shutil.copy2(vfr, dry_lib / "vfr.mp4")
    before = tree_of(dry_lib)
    r = sh([PY, str(SCRIPTS / "normalize.py"), "--library", str(dry_lib), "--dry-run"])
    after = tree_of(dry_lib)
    must("--dry-run 退出 0 且报出要转的片子", r.returncode == 0 and "需要转成恒定帧率" in r.stdout,
         (r.stdout + r.stderr)[-300:])
    must("--dry-run 不写任何文件（连 _cfr/ 都不建）", before == after,
         f"多出来：{sorted(after - before)}")

    # 真转
    # 真转：先记下原文件指纹，转完要能证明它没被动过
    orig_bytes = vfr.read_bytes()
    orig_sha = hashlib.sha256(orig_bytes).hexdigest()
    r = sh([PY, str(SCRIPTS / "normalize.py"), "--library", str(lib), "--fps", "30"])
    must("真转退出 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    outs = sorted((lib / "_cfr").glob("*.mp4"))
    must("产物落在独立目录 _cfr/", len(outs) == 1, f"outs={[p.name for p in outs]}")
    must("原文件一个字节都没变（只读不改）",
         hashlib.sha256(vfr.read_bytes()).hexdigest() == orig_sha, "原文件被改写了")
    if not outs:
        return
    out = outs[0]

    info_o, info_n = N.probe_json(vfr), N.probe_json(out)
    import media_spec
    so, sn = media_spec.spec_of(info_o), media_spec.spec_of(info_n)
    must("转后 r_frame_rate == avg_frame_rate（真恒定帧率）",
         abs(float(sn["fps"]) - float(sn["avg_fps"])) < 1e-3 and
         abs(float(sn["fps"]) - 30.0) < 1e-3,
         f"r={sn['fps']} avg={sn['avg_fps']}")

    # 音频逐样本不变（测试自己解码比，不走被测代码）
    ha, la = audio_samples(vfr)
    hb, lb = audio_samples(out)
    must("音频逐样本一致（解码后 sha256 与字节数都相同）", ha == hb and la == lb and la > 0,
         f"{ha[:12]}/{la}B vs {hb[:12]}/{lb}B")
    a_o = next((s for s in info_o["streams"] if s.get("codec_type") == "audio"), {})
    a_n = next((s for s in info_n["streams"] if s.get("codec_type") == "audio"), {})
    must("音频采样率/声道没被改（不是靠重采样凑过去的）",
         a_o.get("sample_rate") == a_n.get("sample_rate") and
         a_o.get("channels") == a_n.get("channels"),
         f"{a_o.get('sample_rate')}/{a_o.get('channels')} → "
         f"{a_n.get('sample_rate')}/{a_n.get('channels')}")

    # 时长差 ≤ 1 帧
    one_frame = 1000.0 / 30.0
    vo = next(s for s in info_o["streams"] if s.get("codec_type") == "video")
    vn = next(s for s in info_n["streams"] if s.get("codec_type") == "video")
    d_fmt = abs(float(info_n["format"]["duration"]) - float(info_o["format"]["duration"])) * 1000
    d_vid = abs(float(vn["duration"]) - float(vo["duration"])) * 1000
    must("时长差在 1 帧内（容器与视频流分别量）",
         d_fmt <= one_frame + 2 and d_vid <= one_frame + 2,
         f"容器 {d_fmt:.1f}ms / 视频流 {d_vid:.1f}ms，1 帧 = {one_frame:.1f}ms")

    # manifest 记全了没有
    man = json.loads((lib / "_cfr" / "manifest.json").read_text(encoding="utf-8"))
    ent = (man.get("entries") or {}).get("vfr.mp4") or {}
    must("manifest 记录原文件/新文件/判定依据/转码命令/转后实测",
         all(k in ent for k in ("orig", "out", "verdict", "cmd", "audio", "post", "delta")),
         f"键：{sorted(ent)}")
    must("manifest 的转码命令里音频走 -c:a copy",
         "-c:a" in (ent.get("cmd") or []) and "copy" in (ent.get("cmd") or []),
         str((ent.get("cmd") or [])[-6:]))
    must("manifest 标了音频逐样本一致", bool((ent.get("audio") or {}).get("bit_exact")))
    must("manifest 记了漂移人话（剪到 N 分钟差多少）",
         "漂" in str(ent.get("drift_human") or ""), str(ent.get("drift_human"))[:80])

    # CFR 素材不许误转
    lib_cfr = base / "lib_cfr"
    lib_cfr.mkdir(parents=True)
    cok, cerr = build_cfr(lib_cfr / "cfr.mp4")
    must("造出 CFR 样本", cok, cerr)
    if cok:
        jc = N.judge(lib_cfr / "cfr.mp4")
        must("CFR 素材被判 cfr（不误判成 vfr）", jc.get("verdict") == "cfr",
             f"{jc.get('verdict')}：{jc.get('reason')}")
        r = sh([PY, str(SCRIPTS / "normalize.py"), "--library", str(lib_cfr)])
        produced = sorted((lib_cfr / "_cfr").glob("*.mp4")) if (lib_cfr / "_cfr").exists() else []
        must("CFR 素材不产生任何转码产物（不误转）", r.returncode == 0 and not produced,
             f"产物 {[p.name for p in produced]}")

    # 判不了就说判不了，不许默认 CFR
    broken = lib / "broken.mp4"
    broken.write_bytes(b"not a video")
    jb = N.judge(broken)
    must("探测不出帧率状态时说「判不了」（不默认当 CFR）",
         jb.get("verdict") == "unknown" and "判不了" in str(jb.get("reason")),
         f"{jb.get('verdict')}：{jb.get('reason')}")


# ---------------------------------------------------------------- ①b 音频保真判据

def test_audio_fidelity_rules() -> None:
    """`audio_fidelity` 只许放过「整段编码器预热样本」这一种差异，其余一概算失败。

    为何单独验：真实现场会遇到这种差异——iPhone 写的 .MOV 用编辑表把 AAC 的 2112 个
    priming samples 藏掉了，mp4 容器重写时又把它吐回来（实测墙梓那支：包 md5 一致，
    PCM 只差头部 2112 样本 = 47.9ms）。ffmpeg 自己写不出带苹果那种编辑表的 .MOV 样本，
    造不出稳定可复现的合成片，所以这里直接验判据分支（裁剪函数打桩），另有一条
    真素材读数留在 SKIP 说明里，不假造。
    """
    real = N._pcm_trimmed_md5
    # 打桩成「裁掉 2112 个样本就还原成源 PCM」——这才是真实现场的读数
    N._pcm_trimmed_md5 = lambda path, n: "PCM_SRC" if int(n) == 2112 else "OTHER"
    try:
        base = {"packet_md5": "PKT", "pcm_md5": "PCM_SRC", "start_s": 0.081315,
                "sample_rate": 44100, "channels": 2}
        prim = {"packet_md5": "PKT", "pcm_md5": "PCM_DST", "start_s": 0.033424,
                "sample_rate": 44100, "channels": 2}
        r = N.audio_fidelity(base, prim, Path("src"), Path("dst"))
        must("音频判据：包一致 + 只差 2112 个预热样本 → 放行（priming）",
             r["status"] == "priming" and r["bit_exact"] is True
             and r.get("priming_samples") == 2112
             and abs(float(r.get("priming_ms") or 0) - 47.9) < 0.5,
             str(r))

        same_start = dict(prim, start_s=base["start_s"])
        r = N.audio_fidelity(base, same_start, Path("src"), Path("dst"))
        must("音频判据：PCM 不同且不是预热偏移 → 红（mismatch）",
             r["status"] == "mismatch" and r["bit_exact"] is False, str(r))

        recoded = dict(prim, packet_md5="PKT2")
        r = N.audio_fidelity(base, recoded, Path("src"), Path("dst"))
        must("音频判据：编码数据变了（重编码）→ 红，不看样本差多少",
             r["status"] == "mismatch" and r["bit_exact"] is False, str(r))

        r = N.audio_fidelity(base, dict(prim, pcm_md5="PCM_SRC"), Path("s"), Path("d"))
        must("音频判据：PCM 完全相同 → 放行（exact）",
             r["status"] == "exact" and r["bit_exact"] is True, str(r))

        r = N.audio_fidelity(None, prim, Path("s"), Path("d"))
        must("音频判据：无音轨素材不报假红", r["status"] == "none" and r["bit_exact"], str(r))
    finally:
        N._pcm_trimmed_md5 = real
    print("  ⏭ 真素材的 priming 读数不进单测：本机 ffmpeg 写不出带苹果编辑表的 .MOV 样本；"
          "真片实测（柒造素材·墙面暗盒）：包 md5 91fc22e6→91fc22e6，PCM 只差 2112 个预热样本 47.9ms")


# ---------------------------------------------------------------- ② 拍摄任务卡

SIX = ("拍什么", "景别", "机位", "时长", "备份", "手机拍摄要点")


def test_shoot_guide(base: Path) -> None:
    print("② 拍摄任务卡：六项字段 + 简笔示意")
    proj = base / "proj"
    proj.mkdir(parents=True)
    idea = ideate.build_proposals("三百万装修六百平茶楼怎么避坑", "二十年餐饮老炮",
                                  "珠海找茶楼的人", 35)
    guide = ideate.render_shoot_guide(idea, proj)
    (proj / "shoot-guide.md").write_text(guide, encoding="utf-8")

    prop = idea["proposals"][0]
    shots = [ideate.shot_plan(b, who=idea["persona"]) for b in prop["beats"]]

    for s in shots:
        must(f"第 {s['n']} 镜六项字段齐全",
             all(s.get(k) for k in ("what", "size", "camera", "dur_s", "takes_why", "phone")),
             str({k: s.get(k) for k in ("what", "size", "camera", "dur_s", "takes")}))
        must(f"第 {s['n']} 镜手机拍摄要点 ≥2 条",
             len(s.get("phone") or []) >= (1 if s["skip"] else 2), str(s.get("phone")))
        must(f"第 {s['n']} 镜时长 > 0 且备份条数是整数", s["dur_s"] > 0 and s["takes"] >= 0,
             f"{s['dur_s']}s / {s['takes']} 条")

    # 景别必须按内容分，不许整张卡一个景别
    sizes = [s["size"] for s in shots]
    must("景别按镜头分（不是整张卡都写「中景」）", len(set(sizes)) >= 2, f"实际 {sizes}")
    must("钩子镜是近景/中近景（口播镜必须拍人）",
         shots[0]["size_key"] in ("close", "medium"), f"{shots[0]['size_key']}")
    must("钩子镜备份 3 条（规则：前 3 秒能多拍就多拍）", shots[0]["takes"] == 3,
         f"{shots[0]['takes']} 条")

    # 备份条数规则的四个分支
    t_setup = ideate.shot_plan({"n": 1, "role": "setup", "line": "钩子", "visual_mode": "talking"})
    t_mid = ideate.shot_plan({"n": 2, "role": "claim", "line": "中段口播", "visual_mode": "talking"})
    t_detail = ideate.shot_plan({"n": 3, "role": "evidence", "line": "边角露馅",
                                 "visual_mode": "broll_or_gen"})
    t_wide = ideate.shot_plan({"n": 4, "role": "evidence", "line": "带你看看店里现场",
                               "visual_mode": "broll_or_gen"})
    t_skip = ideate.shot_plan({"n": 5, "role": "evidence", "line": "三步标准",
                               "visual_mode": "gen", "shoot_tip": "【免拍】代码生成"})
    must("备份规则：钩子 3 条 / 口播 2 条 / 特写 3 条 / 免拍 0 条",
         (t_setup["takes"], t_mid["takes"], t_detail["takes"], t_skip["takes"]) == (3, 2, 3, 0),
         f"{t_setup['takes']}/{t_mid['takes']}/{t_detail['takes']}/{t_skip['takes']}")
    must("台词讲到实物细节 → 手部特写", t_detail["size_key"] == "detail", t_detail["size_key"])
    must("台词在交代场地 → 全景", t_wide["size_key"] == "wide", t_wide["size_key"])
    must("免拍镜的「拍什么」写明由代码画面承担", "免拍" in t_skip["what"], t_skip["what"])
    must("机位写了高度与角度（不只写「平视」）",
         t_detail["angle_deg"] > 0 and t_detail["cam_height"] in ("high", "eye", "chest"),
         f"{t_detail['camera']}")

    # 表头六列齐全 + 每镜一行对得上
    header = next((ln for ln in guide.splitlines() if ln.startswith("| 镜号")), "")
    must("任务卡表头列齐（镜号/台词/六项/示意）",
         all(k in header for k in SIX) and "简笔示意" in header, header)
    rows = [ln for ln in guide.splitlines()
            if ln.startswith("| ") and not ln.startswith("| 镜号") and "---" not in ln]
    must("每镜一行（行数 = 镜头数）", len(rows) == len(shots), f"{len(rows)} 行 / {len(shots)} 镜")
    for s, row in zip(shots, rows):
        cells = [c.strip() for c in row.strip("|").split("|")]
        must(f"第 {s['n']} 镜一行 9 格且六项都落在对应格",
             len(cells) == 9 and s["size"] in cells and f"{s['dur_s']:.1f}s" in cells
             and s["camera"] in cells, f"{len(cells)} 格：{cells[:4]}")

    # 简笔示意：真出图、尺寸对、md 里引用得到
    diag_dir = proj / "shoot"
    for s in shots:
        p = diag_dir / f"diagram-{s['n']}.png"
        must(f"第 {s['n']} 镜示意图存在", p.exists() and p.stat().st_size > 3000, str(p))
        if p.exists():
            from PIL import Image
            with Image.open(p) as im:
                must(f"第 {s['n']} 镜示意图尺寸 {DIAG_W}×{DIAG_H}（9:16 取景框 + 机位侧视）",
                     im.size == (DIAG_W, DIAG_H), str(im.size))
                lo = im.convert("L").getextrema()[0]
                must(f"第 {s['n']} 镜示意图不是空白图", lo < 120, f"最暗像素 {lo}")
        must(f"shoot-guide.md 引用了第 {s['n']} 镜示意图",
             f"shoot/diagram-{s['n']}.png" in guide)

    # 真人跑 CLI：--guide 的输出必须跟函数一致（不能让命令行绕开新字段）
    r = sh([PY, str(SCRIPTS / "ideate.py"), "--project", str(proj),
            "--topic", "三百万装修六百平茶楼怎么避坑", "--persona", "二十年餐饮老炮",
            "--guide"])
    md = (proj / "shoot-guide.md").read_text(encoding="utf-8") if r.returncode == 0 else ""
    must("ideate.py --guide 跑得通", r.returncode == 0, (r.stderr or r.stdout)[-300:])
    must("--guide 输出里每镜都能对上（台词 + 景别 + 示意图链接）",
         all(f"shoot/diagram-{s['n']}.png" in md for s in shots) and all(ln in md for ln in SIX),
         "缺字段或图链接")
    must("--guide 仍然保留钩子出处（test_hooks_policy 依赖）", "出处：" in md)


def main() -> int:
    print(f"ffmpeg: {FF}")
    base = Path(tempfile.mkdtemp(prefix="cutskills-normalize-"))
    try:
        test_normalize(base)
        test_audio_fidelity_rules()
        test_shoot_guide(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()
    if fails:
        print(f"❌ 未通过（{len(fails)} 条）：{'、'.join(fails)}")
        return 1
    print("✅ 素材规整与拍摄任务卡全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
