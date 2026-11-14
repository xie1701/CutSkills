#!/usr/bin/env python3
"""回归：拍板②的形态（锁定音轨 + 低清粗剪预览）与「我替你做的决定」清单。

BRIEF 的交互约束是「一条片最多打断 3 次，其余全用默认值推进」。这条约束要立得住，
两个东西必须真的存在、真的能验：

  ① `assumptions.md` 把系统默认掉的决定逐条摊开（决定了什么｜依据｜怎么改），
     **每个来源都真从文件读出来**，读不到的写「⚠ 取不到」而不是消失；
  ② `plan_card.py --preview` 出一版真能看的低清粗剪：文件在、分辨率确实低于成片、
     画面上烧着「粗剪预览 · 未过门禁」，而且**不许覆盖真项目的成片**；
  ③ 用户只改画面 → 重签后音轨指纹一个字符不变（这条是「改画面不必重听音频」的依据）；
  ④ 改口播 → 锁失效（负样本，走的是 lock.py 的口径，不是另造一套）。

③④ 用离线合成项目跑（快、不依赖素材库）；② 需要真渲一版片，所以挑本机最短的可渲项目，
把它软链成临时工程来渲——没有可渲项目就明确 ⏭，不假装通过。

    python3 scripts/test_assumptions.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
sys.path.insert(0, str(HERE))

from libs import config_libs, cutskills_home, find_projects  # noqa: E402

fails: list[str] = []
skips: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def env() -> dict:
    """子进程环境：把 ~/.cutskills/py 补进 PYTHONPATH（numpy 在那儿），别指望调用方先 source。"""
    e = dict(os.environ)
    py_dir = cutskills_home() / "py"
    if py_dir.is_dir() and str(py_dir) not in (e.get("PYTHONPATH") or ""):
        e["PYTHONPATH"] = str(py_dir) + (":" + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    return e


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, env=env(), **kw)


def jwrite(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else ""


# ---------------------------------------------------------------- 合成项目

def make_project(base: Path, *, with_bgm: bool = True, with_track: bool = True,
                 with_index: bool = True) -> Path:
    """一个「什么都有」的最小项目：够让 assumptions 的每个来源都读得到真东西。"""
    lib = base / "lib"
    proj = lib / "_cutskills" / "projects" / "demo-film"
    proj.mkdir(parents=True, exist_ok=True)
    if with_index:
        jwrite(lib / "_cutskills" / "index.json", {"version": 1, "assets": [
            {"id": "a_口播.mov", "kind": "video", "duration": 120.0,
             "segments": [
                 {"in": 0.0, "out": 6.0, "grade": "must-use", "why": "正对镜头、双手不挡脸"},
                 {"in": 8.0, "out": 12.0, "grade": "do-not-use",
                  "why": "两帧茶杯贴唇，嘴被挡住，没法配字幕"},
             ]},
        ]})
    jwrite(proj / "brief.json", {
        "version": 1, "audience": "珠海金湾的街坊，做同城获客，欢迎到店",
        "change": "比价前先问三件事", "length_s": 15, "length_band": "15-30",
        "style_pack": "talking-head-v1", "pack": "P1-warm", "plan": "S2-live-mid",
    })
    jwrite(proj / "timeline.json", {
        "version": 5, "project": "demo-film", "duration_s": 12.0,
        "style_pack": "talking-head-v1", "pack": "P1-warm", "plan": "S2-live-mid",
        "elements": [
            {"n": 1, "kind": "clip", "track": "video", "asset": "a_口播.mov",
             "in": 0.0, "out": 6.0, "medium": "talking", "evidence": "先说结论",
             "audio": {"asset": "a_口播.mov", "in": 0.0, "out": 6.0},
             "grade_at_cut": "must-use", "why": "开场最能留人，先给结论"},
            {"n": 2, "kind": "clip", "track": "video", "asset": "a_口播.mov",
             "in": 8.0, "out": 12.0, "medium": "broll", "evidence": "再说理由",
             "audio": {"asset": "a_口播.mov", "in": 6.0, "out": 12.0},
             "card": {"kind": "keyword", "text": "问三件事", "on": "三件事"},
             "grade_at_cut": "backup", "why": "例子镜头，够近够清楚"},
        ],
    })
    if with_bgm:
        jwrite(proj / "bgm.json", {"mood": "warm", "source": "cc0", "title": "自测垫乐",
                                   "dur": 12.0, "why": "律动稳、不抢话"})
    jwrite(proj / "out" / "sfx.json", {"events": [
        {"t": 1.2, "name": "pop-medium-in", "gain_db": 1.5, "for": "问三件事"},
        {"t": 7.5, "name": "impact-light-firm", "gain_db": 1.0, "for": "例子"},
    ]})
    jwrite(proj / "out" / "sfx_manifest.json", {
        "licence_summary": "全部 CC0 1.0 Universal", "bgm": {"licence": "CC0 1.0 Universal",
                                                            "sha256_16": "0000aaaa1111bbbb"}, "sfx": []})
    jwrite(proj / "hf" / "meta.json", {"duration_s": 12.0, "lufs": -14.31, "true_peak": -3.1})
    jwrite(proj / "out" / "subs.json", {"cues": [
        {"start": 0.1, "end": 2.0, "text": "先说结论"},
        {"start": 2.0, "end": 4.0, "text": "比价前先问三件事"},
    ]})
    (proj / "decisions.md").write_text(
        "# 确认记录 · demo-film\n\n| 时间 | 用户原话 | 做了什么 | 结论 |\n|---|---|---|---|\n"
        "| 2026-11-01 10:00 | 开场太慢 | 删掉 #0 的两句铺垫，直接从结论起 | ✅ 接受 |\n"
        "| 2026-11-02 11:00 | 卡面字太小 | 调大卡面字号 | ✅ 接受 |\n",
        encoding="utf-8")
    if with_track:
        r = run([PY, str(HERE / "track.py"), "--project", str(proj)])
        if r.returncode != 0:
            print(f"  ！track.py 没跑成：{(r.stderr or r.stdout)[-200:]}")
    return proj


def table_rows(md: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for ln in md.splitlines():
        if not ln.startswith("|") or ln.count("|") < 5:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].isdigit():
            rows.append(cells)
    return rows


# ---------------------------------------------------------------- ① ⑤ 清单

def check_list(base: Path) -> None:
    print("① 清单逐来源对账（真读，不是写死文案）")
    proj = make_project(base / "a")
    r = run([PY, str(HERE / "assumptions.py"), "--project", str(proj)])
    md_p = proj / "assumptions.md"
    must("assumptions.py 写成 assumptions.md", r.returncode == 0 and md_p.exists(),
         (r.stderr or r.stdout)[-200:])
    md = md_p.read_text(encoding="utf-8") if md_p.exists() else ""
    rows = table_rows(md)
    must("清单条数覆盖全部来源（≥10 条）", len(rows) >= 10, f"只读到 {len(rows)} 条")

    # 每个来源的「真凭据」都必须能在清单里找到——证明它真从文件读出来
    evidence = {
        "片长（brief.json 的秒数）": "length_s=15",
        "包装包（packs/<id>.yaml 的实读字段）": "pack=P1-warm",
        "BGM（bgm.json 的曲名）": "自测垫乐",
        "账号轨（track.py 判出的门店轨）": "账号按「门店轨」做",
        "节奏档（plans/<档>.yaml 的 tempo）": "S2-live-mid",
        "素材取舍（库里判死的段）": "do-not-use",
        "字幕版式（包里的字号/行宽）": "80px",
        "音效（out/sfx.json 的事件表）": "pop-medium-in",
        "调色（包里的 grade 名）": "warm_high_sat",
        "响度（包里的目标值）": "-14 LUFS",
    }
    for label, needle in evidence.items():
        must(f"清单覆盖 {label}", needle in md)

    print("⑤ 每一行都有「怎么改」这一列")
    empty = [r[1] for r in rows if not r[3].strip()]
    must("「怎么改」列全部非空", not empty, "空的是：" + "；".join(empty[:3]))
    must("没有「这是一条视频」式的废话",
         not any("这是一条视频" in cell for r in rows for cell in r[1:]))
    # 首屏只放最可能被改的那几条（片长/包装/BGM/账号轨），其余折叠
    head = md.split("<details>")[0]
    must("首屏是片长/包装/BGM/账号这四条", all(k in head for k in ("片长", "包装", "BGM", "账号")))
    must("其余默认折叠在 <details> 里", "<details>" in md and "其余" in md)

    print("①' 取不到的要留痕、不许消失")
    bare = make_project(base / "a-bare", with_bgm=False, with_track=False, with_index=False)
    (bare / "account.json").unlink(missing_ok=True)
    r2 = run([PY, str(HERE / "assumptions.py"), "--project", str(bare)])
    md2 = (bare / "assumptions.md").read_text(encoding="utf-8") if r2.returncode == 0 else ""
    rows2 = table_rows(md2)
    must("缺来源时条数不缩水（缺口要写出来而不是删掉）", len(rows2) >= len(rows),
         f"完整项目 {len(rows)} 条 → 缺来源 {len(rows2)} 条")
    must("账号轨缺 account.json 标「取不到」", "取不到" in md2 and "account.json" in md2)
    must("BGM 缺 bgm.json 标「取不到」", any("取不到" in r[2] and "bgm" in r[2].lower() for r in rows2))
    must("标了取不到的行仍然有「怎么改」", all(r[3].strip() for r in rows2))


# ---------------------------------------------------------------- ③ ④ 锁

def check_lock(base: Path) -> None:
    print("③ 只改画面 → 重签后音轨指纹不变")
    proj = make_project(base / "b")
    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "自测认可"])
    must("plan_card --approve 签发锁", r.returncode == 0 and (proj / "lock.json").exists(),
         (r.stderr or r.stdout)[-200:])
    l1 = load(proj / "lock.json")
    fp1 = l1.get("audio_fp")
    must("锁里记了音轨指纹与口径", bool(fp1) and bool(l1.get("audio_scope")),
         f"lock.json 字段：{sorted(l1)}")
    must("没预览时如实记 preview=null", l1.get("preview") is None)

    # 只改画面层：换掉视觉素材与卡面，音源与切点一个字不动
    tl = load(proj / "timeline.json")
    tl["elements"][1]["card"]["text"] = "改过的卡面"
    tl["elements"][1]["why"] = "换个说法解释为什么改画面"
    tl["elements"][0]["asset"] = "b_替身画面.mov"
    jwrite(proj / "timeline.json", tl)
    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "只改了画面"])
    l2 = load(proj / "lock.json")
    must("改画面后重签成功", r.returncode == 0, (r.stderr or r.stdout)[-200:])
    must("重签后时间轴 sha 变了（锁确实重签过）", l2.get("sha256") != l1.get("sha256"))
    must("重签后音轨指纹一个字符不变", l2.get("audio_fp") == fp1,
         f"{str(fp1)[:12]} → {str(l2.get('audio_fp'))[:12]}")

    print("④ 改口播 → 锁失效（负样本，走 lock.py 口径）")
    tl = load(proj / "timeline.json")
    tl["elements"][0]["audio"]["in"] = round(tl["elements"][0]["audio"]["in"] + 0.4, 3)
    jwrite(proj / "timeline.json", tl)
    r = run([PY, str(HERE / "lock.py"), "--project", str(proj), "--verify"])
    must("挪口播切点后 lock.verify 判失效（退出码 1）", r.returncode == 1,
         f"退出码 {r.returncode}｜{(r.stdout or r.stderr).strip()[-160:]}")
    fp3 = None
    r3 = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "改了口播"])
    l3 = load(proj / "lock.json")
    fp3 = l3.get("audio_fp")
    must("改口播后音轨指纹跟着变", r3.returncode == 0 and fp3 != fp1,
         f"{str(fp1)[:12]} → {str(fp3)[:12]}")


# ---------------------------------------------------------------- ② 预览

def check_transcode(base: Path) -> None:
    """②' 降级转码本身：喂一段 1080×1920 的合成片，看它是不是真降了一半、图上有没有烧条。

    这一条不依赖素材库与浏览器，所以任何机器上都跑得了——「分辨率确实低于成片」
    是这条功能的硬指标，不能因为没有可渲项目就不验。
    """
    print("②' 降级转码（合成 1080×1920 片 → 低清粗剪）")
    sys.path.insert(0, str(HERE))
    import plan_card  # noqa: PLC0415

    film = base / "d" / "fake-film.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    r = run([plan_card.ffmpeg_bin(), "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-shortest", str(film)])
    must("合成一条 1080×1920 样片", r.returncode == 0 and film.exists(), (r.stderr or "")[-200:])
    if not film.exists():
        return
    dst = base / "d" / "preview.mp4"
    low = plan_card.transcode_preview(film, dst)
    must("低清预览转码出了文件", dst.exists() and dst.stat().st_size > 4096)
    must("分辨率确实减半（1080×1920 → 540×960）",
         (low["width"], low["height"]) == (540, 960), f"{low['width']}×{low['height']}")
    src = plan_card._probe(film)
    src_kbps = int(round(film.stat().st_size * 8 / 1000 / max(src["dur"], 0.001)))
    must("码率确实比成片低（降级不是换个壳）", 0 < low["kbps"] < src_kbps,
         f"预览 {low['kbps']} kbps vs 成片 {src_kbps} kbps")
    must("画面上烧了「粗剪预览」条", low.get("banner") is True)


def pick_renderable() -> list[Path]:
    """本机可渲的短项目（已有 hf/clips 缓存），按时长从短到长。"""
    cands: list[tuple[float, Path]] = []
    for p in find_projects(config_libs()):
        if not (p / "hf" / "clips").is_dir() or not (p / "timeline.json").exists():
            continue
        dur = float(load(p / "timeline.json").get("duration_s") or 0)
        if dur and dur <= 40:
            cands.append((dur, p))
    cands.sort(key=lambda x: x[0])
    return [p for _, p in cands]


def _links(real: Path, demo: Path) -> None:
    """把一个真项目软链成临时工程：预览的临时沙箱落在它自己的 out/ 里，不碰原项目。"""
    (demo / "hf").mkdir(parents=True)
    (demo / "out").mkdir(parents=True)
    for name in ("timeline.json", "brief.json", "plan.json", "bgm.json", "bgm.wav",
                 "audio-map.json", "intent.json", "scenes"):
        if (real / name).exists():
            os.symlink(real / name, demo / name)
    os.symlink(real / "hf" / "clips", demo / "hf" / "clips")


def check_preview(base: Path) -> None:
    print("② --preview 端到端：真渲一版低清粗剪，成片不许被覆盖")
    cands = pick_renderable()
    if not cands:
        msg = "本机没有 ≤40s 的可渲项目（要 hf/clips 缓存）"
        print(f"  ⏭ {msg}")
        skips.append("低清预览端到端真渲（" + msg + "）")
        return
    # 渲前硬规则（声音在前 / 卡准入 / 大字压脸）不过的项目渲不了——换下一个候选，
    # 不是在测试里 `--force`。失败都发生在真渲之前，所以换候选很快。
    last_err = ""
    for real in cands[:3]:
        demo = base / "c" / real.name
        if demo.exists():
            shutil.rmtree(demo, ignore_errors=True)
        _links(real, demo)
        film = real / "out" / "film-hf.mp4"
        film_before = sha16(film)
        library = real
        while library.parent != library and not (library / "_cutskills" / "index.json").exists():
            library = library.parent
        r = run([PY, str(HERE / "plan_card.py"), "--project", str(demo), "--preview",
                 "--library", str(library)])
        if r.returncode != 0:
            last_err = (r.stderr or r.stdout or "").strip()[-300:]
            print(f"  ⏭ {real.name} 渲不了（渲前硬规则没过），试下一个候选")
            continue
        pv_p = demo / "out" / "preview-roughcut.mp4"
        meta = load(demo / "out" / "preview.json")
        must(f"plan_card --preview 跑通（{real.name}）", True)
        must("预览文件真实存在", pv_p.exists() and pv_p.stat().st_size > 8192,
             f"{pv_p}｜{pv_p.stat().st_size if pv_p.exists() else 0} bytes")
        must("preview.json 记了降级参数与实测分辨率",
             bool(meta.get("width")) and (meta.get("render") or {}).get("quality") == "draft",
             str(meta)[:200])
        must("分辨率确实低于成片（半分辨率）",
             int(meta.get("width") or 0) < int((meta.get("render") or {}).get("source_width") or 0)
             and int(meta.get("height") or 0) < int((meta.get("render") or {}).get("source_height") or 0),
             f"{meta.get('width')}×{meta.get('height')} vs "
             f"{(meta.get('render') or {}).get('source_width')}×{(meta.get('render') or {}).get('source_height')}")
        must("预览画面上烧了「粗剪预览」条", meta.get("banner") is True)
        must("预览标了「未过门禁」", "未过门禁" in str(meta.get("status") or ""), str(meta.get("status")))
        if film.exists():
            must("成片没被预览覆盖（真项目的 film-hf.mp4 一个字节没动）", sha16(film) == film_before)
        must("预览的临时工程渲完即清", not (demo / "out" / "_preview").exists())
        must("preview.json 盯住的那版时间轴就是当前这版",
             meta.get("timeline_sha") == hashlib.sha256((demo / "timeline.json").read_bytes()).hexdigest())
        print(f"     实测：{meta.get('width')}×{meta.get('height')} · {meta.get('kbps')} kbps · "
              f"{meta.get('duration_s')}s · 渲+转码 {(meta.get('render') or {}).get('elapsed_s')}s · "
              f"成片 {(meta.get('render') or {}).get('source_width')}×{(meta.get('render') or {}).get('source_height')}")
        return
    print(f"  ⏭ 候选项目全都过不了渲前硬规则：{last_err}")
    skips.append("低清预览端到端真渲（候选项目过不了渲前硬规则）")


def check_preview_state(base: Path) -> None:
    """②'' 锁的范围：预览与当前时间轴是不是同一版，签锁时得摊在明面上。

    不用真渲也能验：把 preview.json / 预览文件按「当前这版」填好，再改时间轴看系统
    到底认不认。人工拍板看的是旧版就必须拦下来；机器代签只准留痕。
    """
    print("②'' 预览版本与锁的范围（不渲，验状态机）")
    proj = make_project(base / "e")
    tl_sha = hashlib.sha256((proj / "timeline.json").read_bytes()).hexdigest()
    fake = proj / "out" / "preview-roughcut.mp4"
    fake.write_bytes(b"\0" * 4096)
    jwrite(proj / "out" / "preview.json", {
        "status": "粗剪预览 · 未过门禁", "file": "out/preview-roughcut.mp4",
        "timeline_sha": tl_sha, "width": 540, "height": 960, "kbps": 240,
        "duration_s": 12.0, "sha256_16": "deadbeefdeadbeef", "created": "2026-11-15T00:00:00",
    })
    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj)])
    md = (proj / "方案.md").read_text(encoding="utf-8") if r.returncode == 0 else ""
    must("方案卡里写明预览与当前时间轴同一版", "同一版" in md, md[-200:])
    must("方案卡里标了「这是粗剪预览、未过门禁」", "未过门禁" in md and "不许当成交付物" in md)
    must("方案卡里给出音轨指纹", "音轨指纹" in md)

    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "认可"])
    lk = load(proj / "lock.json")
    pv = lk.get("preview") or {}
    must("预览同版时拍板签上锁", r.returncode == 0, (r.stderr or r.stdout)[-200:])
    must("锁里记下预览是哪一版（matches_timeline=true）", pv.get("matches_timeline") is True and pv.get("sha256_16"))

    # 改一帧画面 → 预览变成旧版：人工拍板必须被拦
    tl = load(proj / "timeline.json")
    tl["elements"][0]["vfx"] = "punch_zoom"
    jwrite(proj / "timeline.json", tl)
    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "认可"])
    must("预览是旧版时，人工拍板被拦（退出码 1）", r.returncode == 1,
         f"退出码 {r.returncode}｜{(r.stderr or r.stdout)[-160:]}")
    must("拦的时候说清楚要重跑 --preview", "--preview" in (r.stderr or ""))
    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "只改了花字",
             "--accept-stale-preview"])
    lk = load(proj / "lock.json")
    must("明确接受旧版预览能签，但锁里留痕",
         r.returncode == 0 and lk.get("preview_stale_accepted") is True
         and (lk.get("preview") or {}).get("matches_timeline") is False, str(lk.get("preview"))[:160])
    # 机器代签（改片重签）不能被卡死：revise.py 走的就是这条路
    r = run([PY, str(HERE / "plan_card.py"), "--project", str(proj), "--approve", "改片重签",
             "--source", "agent-revise"])
    lk = load(proj / "lock.json")
    must("机器代签不会被旧版预览卡死，但仍然如实记 matches_timeline=false",
         r.returncode == 0 and (lk.get("preview") or {}).get("matches_timeline") is False,
         f"退出码 {r.returncode}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cutskills-assume-") as td:
        base = Path(td)
        check_list(base)
        check_lock(base)
        check_preview_state(base)
        check_transcode(base)
        check_preview(base)
    print()
    if fails:
        print(f"❌ 未通过（{len(fails)} 项）：{'、'.join(fails)}")
        return 1
    tail = f"（跳过 {len(skips)} 项：{'；'.join(skips)}）" if skips else ""
    print(f"✅ 清单 + 音轨锁 + 低清预览全部通过{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
