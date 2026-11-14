#!/usr/bin/env python3
"""CutSkills · 素材盘点（inventory.py）的真判据回归。

BRIEF 入口 2 的要求是「先盘点，再反推选题」。盘点最容易糊的三处，正好是下面六条断言各自盯的：

  ① **每个数字都能复算**：测试自己从 `index.json` 重算一遍（不复用 inventory 的函数），
     跟盘点报告里的数逐项对拍。数对不上就是糊。
  ② **关键词必须有出处**：每条主题词都带「哪个文件、哪句原话、第几个字」，
     测试回读索引里的转写原文逐字对上；再反过来塞一个原文里没有的词，
     必须被判「没有出处」——只认原文，不认联想。
  ③ **画质问题计数**：造一个已知问题的样本库（1 段横拍 / 1 段真 VFR / 1 段过暗 / 1 段无音轨），
     每一类都必须数出 1，且点名的就是那支文件。
  ④ **3 套选题带齐三样**：原文出处（拿 references/content-system.md 的真实标题逐级对账，
     复用 test_hooks_policy 的 check_ref）、素材依据（具体文件 + 时间码）、片长区间（实算）。
  ⑤ **端到端**：`ideate.py --from-inventory` 出的提案能被 `--pick` 接住，再 `--to-plan` 出需求与分镜。
  ⑥ **全废素材库**：全部 do-not-use 时如实说「拍不出片 + 缺什么」，不许硬凑选题。
  ⑦ **不撞招牌 / 不撞句式**：造两个内容完全不同的库，对账「去掉主体名后标题骨架串不相等」；
     同一库三套的**方向 / 锚点 / 句式骨架 id** 互不相同，每套带一段 essential 关键素材；
     库只撑得住两条时**只出两条**。每个判据旁边配一个故意做坏的样本，它必须报错——
     否则这些断言就是永远为真。

跑法：
    python3 scripts/test_inventory.py
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import index_assets as IA            # noqa: E402
import inventory as INV              # noqa: E402
from libs import ffmpeg_bin          # noqa: E402

PY = sys.executable
SCRIPTS = ROOT / "scripts"
FF = ffmpeg_bin()
USABLE = ("must-use", "backup")
fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------- 造素材

def make_video(path: Path, *, size: str = "540x960", dur: float = 6.0,
               color: str = "0x2f5f8a", audio: bool = True, vfr: bool = False) -> tuple[bool, str]:
    """造一支可控的素材：分辨率、时长、颜色、有没有音轨、是不是真 VFR。"""
    cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc2=s={size}:d={dur}:r=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:r=44100:a=0.05"]
    if color != "0x2f5f8a":
        # 纯色底：用来做「过暗」样本（平均亮度可控）
        cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i",
               f"color=c={color}:s={size}:d={dur}:r=30"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:r=44100:a=0.05"]
    if vfr:
        # 真 VFR：每 3 帧取 1 帧 + 时间戳抖动（同 test_normalize 的造法）
        vf = "select='not(mod(n,3))',setpts=PTS+0.02*sin(N)"
        cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i",
               f"testsrc2=s={size}:d={dur}:r=30",
               "-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:r=44100:a=0.05",
               "-vf", vf, "-fps_mode", "vfr"]
    if audio:
        cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "96k"]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)]
    r = sh(cmd)
    return path.exists() and path.stat().st_size > 0, (r.stderr or "")[-200:]


def inject_transcript(rec: dict, lines: list[str]) -> None:
    """模拟 L1 跑完之后的状态：索引里带句级转写（时间轴按句均分）。

    真实的 L1 输出也是这样进索引的（`transcript.phrases`）；这里不跑 ASR，
    是为了让测试在任何机器上都能验证「关键词出处」这一条，而不依赖模型装没装。
    """
    phrases, t = [], 0.0
    dur = float(rec.get("duration") or 0.0)
    step = dur / max(1, len(lines))
    for i, text in enumerate(lines):
        st = round(t + 0.1, 2)
        en = round(min(dur, t + step - 0.1), 2)
        if en > st:
            phrases.append({"start": st, "end": en, "text": text})
        t += step
    rec["transcript"] = {"engine": "test:injected", "lang": "zh", "phrases": phrases,
                         "word_count": sum(len(p["text"]) for p in phrases)}


def build_index(lib: Path, transcripts: dict[str, list[str]]) -> list[dict]:
    """用 index_assets 的 L0 真跑一遍，写成索引（不跑 ASR：转写由 transcripts 注入）。"""
    (lib / "_cutskills").mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in lib.rglob("*")
                   if p.is_file() and p.suffix.lower() in IA.VIDEO_EXT
                   and IA.SYS_DIR not in p.parts)
    assets = []
    for p in files:
        aid = str(p.relative_to(lib)).replace("\\", "/")
        rec = IA.stage_l0_one((str(p), str(lib), aid))
        lines = transcripts.get(aid)
        if lines:
            inject_transcript(rec, lines)
        assets.append(rec)
    IA.apply_grades(assets)                       # 有语音 + 清晰 → must-use；其余 backup
    doc = {"version": 2, "library": str(lib), "built_at": "test",
           "counts": {"assets": len(assets)}, "assets": assets}
    (lib / "_cutskills" / "index.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return assets


def build_sample_lib(lib: Path) -> None:
    """样本库：3 支干净口播 + 1 支空镜 + 四支各带一个已知问题。"""
    ok = True
    plan = [
        ("口播/谈料1.mp4", dict(dur=12.0)),
        ("口播/谈料2.mp4", dict(dur=12.0)),
        ("口播/谈料3.mp4", dict(dur=12.0)),
        ("工地/过程空镜.mp4", dict(dur=8.0)),
        ("问题样本/横拍.mp4", dict(size="960x540", dur=3.0)),
        ("问题样本/可变帧率.mp4", dict(dur=3.0, vfr=True)),
        ("问题样本/过暗.mp4", dict(dur=3.0, color="0x080808")),
        ("问题样本/没有音轨.mp4", dict(dur=3.0, audio=False)),
    ]
    for rel, kw in plan:
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        good, err = make_video(p, **kw)
        ok = ok and good
        if not good:
            print(f"     造素材失败 {rel}: {err}")
    must("造样本库（8 支短片）", ok)
    build_index(lib, {
        # 同一个主题词在 3 支里反复出现 → 才够格当「主题」（盘点里 min_assets=3）。
        # 四个主题词各有 6 次以上出处：三套选题要三块互不重复的招牌，一个词撑不起三套。
        "口播/谈料1.mp4": ["其实艺术涂料这两年客户问得最多，艺术涂料到底怎么挑",
                          "地面打磨这一步没人爱做，地面打磨做不好全白搭",
                          "展厅样板看完再谈，展厅样板的灯最容易骗人",
                          "报价单上写的和现场差很远，报价单要一项项问"],
        "口播/谈料2.mp4": ["地面打磨我们用的是细砂纸，地面打磨完还得养护两天",
                          "这个工艺在艺术涂料里算难的，工艺细节决定成败",
                          "艺术涂料的好要看肌理，艺术涂料的肌理靠师傅手感",
                          "展厅样板要和实景比，展厅样板的色差最坑人",
                          "辅料都藏在报价单里，报价单不写清就别签"],
        "口播/谈料3.mp4": ["报价单看不懂就别签，报价单要一项项对",
                          "艺术涂料颜色不对全白做，艺术涂料一错全屋返工",
                          "地面打磨不到位，地面打磨省下的钱全赔进去",
                          "展厅样板一定要白天看，展厅样板晚上看全是坑"],
        "工地/过程空镜.mp4": ["嗯嗯", "好的"],      # 画面用：两句口水不算口播主题
    })


# ---------------------------------------------------------------- ① 复算

def recompute_from_index(lib: Path) -> dict:
    """测试自己的算法：只读 index.json，一切数字重算一遍（不调 inventory 的任何函数）。"""
    idx = json.loads((lib / "_cutskills" / "index.json").read_text(encoding="utf-8"))
    assets = idx["assets"]
    out = {"assets": len(assets), "video": 0, "photo": 0, "duration_s": 0.0,
           "usable_s": 0.0, "direct_s": 0.0, "speech_s": 0.0, "speech_usable_s": 0.0,
           "grade_segments": {}, "landscape": 0, "dark": [], "blur_soft": [],
           "blur_hard": [], "audio_missing": [], "below_target": 0, "short": 0}
    for a in assets:
        kind = a.get("kind")
        out["video"] += 1 if kind == "video" else 0
        out["photo"] += 1 if kind == "photo" else 0
        out["duration_s"] += float(a.get("duration") or 0.0)
        segs = a.get("segments") or []
        usable_spans = []
        for s in segs:
            g = s.get("grade")
            sec = 0.0
            if kind == "video":
                sec = max(0.0, float(s.get("out") or 0) - float(s.get("in") or 0))
                if g != "do-not-use":
                    usable_spans.append((float(s.get("in") or 0), float(s.get("out") or 0)))
            out["grade_segments"][g] = out["grade_segments"].get(g, 0) + 1
            if g != "do-not-use":
                out["usable_s"] += sec
                if g == "must-use":
                    out["direct_s"] += sec
        # 口播与可用段的交集：逐句乘逐段（段之间不重叠，直接相加即可）
        for p in ((a.get("transcript") or {}).get("phrases") or []):
            ps, pe = float(p.get("start") or 0.0), float(p.get("end") or 0.0)
            if pe <= ps:
                continue
            out["speech_s"] += pe - ps
            for s0, s1 in usable_spans:
                out["speech_usable_s"] += max(0.0, min(pe, s1) - max(ps, s0))
        if str(a.get("orientation") or "") == "landscape":
            out["landscape"] += 1
        if kind == "video":
            q = a.get("quality") or {}
            luma = (q.get("luma") or {}).get("mean")
            blur = (q.get("blur") or {}).get("mean")
            if isinstance(luma, (int, float)) and not math.isnan(luma) and luma < IA.LUMA_DARK:
                out["dark"].append(a.get("id"))
            if isinstance(blur, (int, float)) and not math.isnan(blur):
                if blur > IA.BLUR_DO_NOT_USE:
                    out["blur_hard"].append(a.get("id"))
                elif blur > IA.BLUR_SOFT:
                    out["blur_soft"].append(a.get("id"))
            if not a.get("audio"):
                out["audio_missing"].append(a.get("id"))
            d = float(a.get("duration") or 0.0)
            if 0 < d < INV.TOO_SHORT_S:
                out["short"] += 1
        sp = a.get("spec") or {}
        w = sp.get("display_width") or sp.get("width")
        h = sp.get("display_height") or sp.get("height")
        if w and h:
            short, long_ = min(w, h), max(w, h)
            if max(INV.TARGET_W / short, INV.TARGET_H / long_) > 1.001:
                out["below_target"] += 1
    return out


def test_numbers_recompute(lib: Path, rep: dict) -> None:
    rc = recompute_from_index(lib)
    t, u, s = rep["totals"], rep["usable_clips"], rep["speech"]
    pair = [
        ("文件数", t["assets"], rc["assets"]),
        ("视频数", t["video"], rc["video"]),
        ("总时长", t["duration_s"], round(rc["duration_s"], 1)),
        ("可用时长(must+backup)", t["usable_s"], round(rc["usable_s"], 1)),
        ("可直接进片(must-use)", u["direct_use_s"], round(rc["direct_s"], 1)),
        ("口播总时长", s["speech_total_s"], round(rc["speech_s"], 1)),
        ("可用口播", s["speech_usable_s"], round(rc["speech_usable_s"], 1)),
        ("横拍数", rep["issues"]["orientation"]["landscape"], rc["landscape"]),
        ("过暗数", rep["issues"]["dark"]["count"], len(rc["dark"])),
        ("偏糊数", rep["issues"]["blur"]["soft"] + rep["issues"]["blur"]["hard"],
         len(rc["blur_soft"]) + len(rc["blur_hard"])),
        ("无音轨数", rep["issues"]["audio_missing"]["count"], len(rc["audio_missing"])),
        ("低于交付分辨率", rep["issues"]["resolution"]["below_target"], rc["below_target"]),
        ("短于1秒", rep["issues"]["too_short"]["count"], rc["short"]),
    ]
    bad = [f"{n}: 报告 {a} ≠ 复算 {b}" for n, a, b in pair if abs(float(a) - float(b)) > 0.05]
    must(f"盘点数字全部可从索引复算（{len(pair)} 项对拍）", not bad, "；".join(bad))
    grades = {k: v["segments"] for k, v in u["grades"].items()}
    gbad = [f"{k}: {grades.get(k)} ≠ {v}" for k, v in rc["grade_segments"].items()
            if grades.get(k) != v]
    must("分档段数与索引一致", not gbad, "；".join(gbad))


# ---------------------------------------------------------------- ② 关键词出处

def test_keyword_provenance(lib: Path, rep: dict) -> None:
    idx = json.loads((lib / "_cutskills" / "index.json").read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in idx["assets"]}
    kws = rep["keywords"]
    must("抽到了主题词", bool(kws), "报告里一个主题词都没有")
    bad = []
    for k in kws:
        kw = k["keyword"]
        for ev in k["evidence"]:
            a = by_id.get(ev["asset"])
            if a is None:
                bad.append(f"{kw}：出处指向不存在的素材 {ev['asset']}")
                continue
            texts = [p.get("text") or "" for p in ((a.get("transcript") or {}).get("phrases") or [])]
            if ev["text"] not in texts:
                bad.append(f"{kw}：原话不在 {ev['asset']} 的转写里：「{ev['text']}」")
                continue
            at = ev["at"]
            if ev["text"][at:at + len(kw)] != kw:
                bad.append(f"{kw}：第 {at} 字对不上原文（{ev['text'][at:at + len(kw)]}）")
    must("每个主题词都能在转写原文里逐字找到出处", not bad, "；".join(bad[:4]))

    # 反向断言：原文里没有的词必须被判「没有出处」，不许凭空生成
    assets = idx["assets"]
    for fake in ("量子烤漆", "纳米镀膜", "春卷配方"):
        if INV.keyword_evidence(assets, fake):
            bad.append(fake)
    must("原文里没有的词被拒（反向断言 3 个编造词）", not bad,
         "；".join([x for x in bad if x in ("量子烤漆", "纳米镀膜", "春卷配方")]))
    listed = [k["keyword"] for k in kws]
    must("编造词没混进报告的主题词表", all(f not in listed for f in ("量子烤漆", "纳米镀膜")),
         f"主题词表：{listed}")


# ---------------------------------------------------------------- ③ 画质问题计数

def test_issue_counts(rep: dict) -> None:
    iss = rep["issues"]
    cases = [
        ("横拍", "横拍.mp4", iss["orientation"]["landscape"],
         issue_names(iss["orientation"]["items"])),
        ("VFR", "可变帧率.mp4", iss["vfr"]["vfr"], issue_names(iss["vfr"]["items"])),
        ("过暗", "过暗.mp4", iss["dark"]["count"], issue_names(iss["dark"]["items"])),
        ("无音轨", "没有音轨.mp4", iss["audio_missing"]["count"],
         issue_names(iss["audio_missing"]["items"])),
    ]
    for label, want_file, count, names in cases:
        ok = count == 1 and any(want_file in n for n in names)
        must(f"画质问题计数：{label} = 1 且点名的就是 {want_file}", ok,
             f"count={count} 点名={names}")


def issue_names(items: list[dict]) -> list[str]:
    return [str(x.get("asset") or "") for x in items]


# ---------------------------------------------------------------- ④ 选题三件套

def test_topics_complete(rep: dict) -> None:
    """复用 test_hooks_policy 的 check_ref：出处必须逐级命中 references 原文标题。"""
    from test_hooks_policy import check_ref
    tp = rep["topics"]
    must("反推出 3 套选题", tp.get("ok") and len(tp.get("proposals") or []) == 3,
         f"ok={tp.get('ok')} n={len(tp.get('proposals') or [])}")
    bad = []
    for p in tp.get("proposals") or []:
        where = f"选题 {p.get('index')}"
        if not p.get("topic"):
            bad.append(f"{where}：没有选题句")
        for key in ("topic_ref", "formula_ref"):
            ref = p.get(key) or {}
            errs = check_ref(ref, f"{where} {key}")
            if errs:
                bad.append("；".join(errs))
        if not (p.get("topic_source") or "").startswith("原文『"):
            bad.append(f"{where}：话题出处不是引用原文：{p.get('topic_source')}")
        if not (p.get("formula_source") or "").startswith("原文『"):
            bad.append(f"{where}：公式出处不是引用原文：{p.get('formula_source')}")
        tb = p.get("title_basis") or {}
        errs = check_ref(tb.get("frame_ref") or {}, f"{where} 标题句式")
        if errs:
            bad.append("；".join(errs))
        elif not (tb.get("frame_when") or "").strip():
            bad.append(f"{where}：标题句式没写为什么用这一式")
        basis = p.get("material_basis") or []
        if not basis:
            bad.append(f"{where}：没有素材依据")
        for b in basis:
            if not b.get("asset") or b.get("in") is None or b.get("out") is None:
                bad.append(f"{where}：素材依据缺文件或时间码：{b}")
        dr = p.get("duration_range") or {}
        if not (isinstance(dr.get("min_s"), int) and isinstance(dr.get("max_s"), int)
                and dr["min_s"] > 0 and dr["max_s"] > 0 and dr.get("basis")):
            bad.append(f"{where}：片长区间不完整：{dr}")
        elif dr["enough"] != (dr["max_s"] >= dr["min_s"]):
            bad.append(f"{where}：enough 与 min/max 自相矛盾：{dr}")
        if not (p.get("hooks") and p["hooks"][0].get("source")):
            bad.append(f"{where}：钩子没有出处")
    must("3 套选题各带原文出处 + 素材依据 + 片长区间", not bad, "；".join(bad[:5]))
    # 素材依据必须是库里真有的文件
    ids = {a["id"] for a in rep.get("assets_index") or []}
    if ids:
        ghost = [b["asset"] for p in tp["proposals"] for b in p.get("material_basis") or []
                 if b["asset"] not in ids]
        must("素材依据点名的文件在库里真有", not ghost, f"不存在的文件：{ghost[:3]}")


# ---------------------------------------------------------------- ⑤ 端到端

def test_end_to_end(lib: Path, tmp: Path) -> None:
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    r = sh([PY, str(SCRIPTS / "ideate.py"), "--project", str(proj),
            "--from-inventory", str(lib)])
    idea_p = proj / "ideation.json"
    must("ideate --from-inventory 跑通并写出 ideation.json",
         r.returncode == 0 and idea_p.exists(), (r.stderr or r.stdout).strip()[-300:])
    if not idea_p.exists():
        return
    idea = json.loads(idea_p.read_text(encoding="utf-8"))
    must("提案结构与 ideate 原格式一致（--pick/--to-plan 才接得住）",
         len(idea.get("proposals") or []) == 3
         and all(k in idea["proposals"][0] for k in ("index", "beats", "hooks", "pack", "plan")),
         str(list((idea.get("proposals") or [{}])[0].keys()))[:200])
    r2 = sh([PY, str(SCRIPTS / "ideate.py"), "--project", str(proj), "--pick", "2"])
    idea2 = json.loads(idea_p.read_text(encoding="utf-8"))
    must("--pick 接住盘点出来的提案（选中第 2 套）",
         r2.returncode == 0 and idea2.get("selected_proposal") == 2,
         f"rc={r2.returncode} sel={idea2.get('selected_proposal')}")
    must("--pick 后拍片卡出得来", (proj / "shoot-guide.md").exists(),
         (r2.stderr or r2.stdout)[-200:])
    r3 = sh([PY, str(SCRIPTS / "ideate.py"), "--project", str(proj), "--to-plan"])
    must("--to-plan 接着出 brief.json + plan.json",
         r3.returncode == 0 and (proj / "brief.json").exists() and (proj / "plan.json").exists(),
         (r3.stderr or r3.stdout)[-300:])


# ---------------------------------------------------------------- ⑥ 全废素材库

def test_dead_library(tmp: Path) -> None:
    lib = tmp / "dead_lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "坏素材").mkdir(exist_ok=True)
    good, err = make_video(lib / "坏素材" / "全黑.mp4", dur=3.0, color="0x080808")
    must("造全废样本库（一支过暗到不能用的）", good, err)
    build_index(lib, {})
    rep = INV.build_report(lib, with_topics=True)
    t = rep["topics"]
    must("全废库：明说拍不出片", (not t["ok"]) and "拍不出片" in str(t["verdict"]), str(t["verdict"]))
    must("全废库：给出缺什么（不是空口说不行）", bool(t["missing"]), str(t["missing"]))
    must("全废库：一套选题都不硬凑", t["proposals"] == [], str(t["proposals"])[:120])
    md = INV.render_md(rep)
    must("全废库：报告里写清拍不出片与缺项", "拍不出片" in md and "缺的是" in md,
         md[-400:])
    proj = tmp / "dead_proj"
    proj.mkdir(parents=True, exist_ok=True)
    r = sh([PY, str(SCRIPTS / "ideate.py"), "--project", str(proj),
            "--from-inventory", str(lib)])
    must("全废库：ideate --from-inventory 拒绝出选题（非零退出、不写 ideation.json）",
         r.returncode != 0 and not (proj / "ideation.json").exists(),
         f"rc={r.returncode} 输出={(r.stdout or r.stderr).strip()[-200:]}")


# ---------------------------------------------------------------- ⑦ 不撞招牌 / 不撞句式
#
# 三套选题最容易被糊弄成「同一句话换个主体名」：一个画面标签当两次招牌，或者三条标题
# 只换名词不换句式。这一段造**内容完全不同的库**来对账，并且把断言本身也拿来撞一次——
# 每组判据都配一个「故意做坏的样本」，它必须报错，否则这条断言就是永远为真。

LIB_A_LINES = [          # 艺术涂料 / 工地
    ["其实艺术涂料这两年客户问得最多，艺术涂料到底怎么挑",
     "地面打磨这一步没人爱做，地面打磨做不好全白搭",
     "展厅样板看完再谈，展厅样板的灯最容易骗人",
     "报价单上写的和现场差很远，报价单要一项项问"],
    ["地面打磨我们用的是细砂纸，地面打磨完还得养护两天",
     "这个工艺在艺术涂料里算难的，工艺细节决定成败",
     "艺术涂料的好要看肌理，艺术涂料的肌理靠师傅手感",
     "展厅样板要和实景比，展厅样板的色差最坑人",
     "辅料都藏在报价单里，报价单不写清就别签"],
    ["报价单看不懂就别签，报价单要一项项对",
     "艺术涂料颜色不对全白做，艺术涂料一错全屋返工",
     "地面打磨不到位，地面打磨省下的钱全赔进去",
     "展厅样板一定要白天看，展厅样板晚上看全是坑"],
]
LIB_B_LINES = [          # 烘焙店（跟 A 一个字都不搭界，口播讲的也不是同一类判断）
    ["面团发酵好才进烤箱，面团发酵决定面包的高度",
     "面包烤出来才不会塌腰，面包的香气全靠烤箱温度",
     "今年面粉涨了两轮，这个城市的面包店都在扛"],
    ["烤箱温度差十度，烤箱里面的面包就废了",
     "面团揉过头就发不起来，面团要醒够时间",
     "面包店最怕发酵过头，发酵过了面包就酸",
     "有人说预拌粉省事对不对，你说对不对"],
    ["做这行最憋屈的是面包卖不完，面包只能当天做",
     "烤箱一开就是一整天，烤箱电费压得喘不过气",
     "面团和发酵都得看天气，面团发酵看天吃饭"],
]
LIB_C_LINES = [          # 茶叶：只够三块招牌里的一块半 → 该只出两条
    ["手工制茶最费时间，手工制茶一批要三天",
     "茶汤颜色要透亮，茶汤颜色一变就说明坏了",
     "存茶方式比买什么更重要，存茶方式错了白花钱",
     "客户问手工制茶多少钱一斤"],
    ["手工制茶要三炒三揉，手工制茶的味道才稳",
     "茶汤颜色偏黄说明火大了，茶汤颜色得清",
     "存茶方式用陶罐最好，存茶方式不对茶就闷",
     "存茶方式这道工序最麻烦"],
    ["手工制茶看天，手工制茶赶上下雨就得停",
     "茶汤颜色要透，茶汤颜色浑了客人不喝",
     "存茶方式我试过很多，存茶方式最后还是陶罐"],
]


def make_talk_lib(base: Path, name: str, lines: list[list[str]], *, dur: float = 9.0) -> Path:
    """造一个「只有口播、没有 L2 画面标签」的库：标题只能退到口播主题词当锚点。"""
    lib = base / name
    lib.mkdir(parents=True, exist_ok=True)
    ok = True
    for i, _phrases in enumerate(lines, 1):
        p = lib / f"口播/素材{i}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        good, err = make_video(p, dur=dur)
        ok = ok and good
        if not good:
            print(f"     造素材失败 {p.name}: {err}")
    must(f"造样本库「{name}」（{len(lines)} 支口播）", ok)
    build_index(lib, {f"口播/素材{i}.mp4": ph for i, ph in enumerate(lines, 1)})
    return lib


def _names_of(p: dict) -> list[str]:
    """一套选题里「属于这个库」的名词：锚点 + 两个主题词。去掉它们，剩下的就是骨架。"""
    tb = p.get("title_basis") or {}
    return [str((tb.get("anchor") or {}).get("text") or ""),
            str(tb.get("k1") or ""), str(tb.get("k2") or "")]


def skeleton_titles(tp: dict) -> list[str]:
    """把每套标题里的库内名词（锚点/主题词）去掉，剩下的串就是「句式骨架」的实测值。"""
    out = []
    for p in tp.get("proposals") or []:
        t = str(p.get("topic") or "")
        for n in sorted({n for n in _names_of(p) if n}, key=len, reverse=True):
            t = t.replace(n, "")
        out.append(t)
    return out


def topic_problems(tp: dict) -> list[str]:
    """同一库内的硬规矩：方向不重、锚点不重、骨架不重、每套都有一段 essential 关键素材。"""
    ps = tp.get("proposals") or []
    if not ps:
        return ["一套选题都没有"]
    bad: list[str] = []
    dirs = [str(p.get("topic_direction") or "") for p in ps]
    if len(set(dirs)) != len(dirs):
        bad.append(f"方向重复：{dirs}")
    anchors = [str((p.get("title_basis") or {}).get("anchor", {}).get("text") or "") for p in ps]
    if len(set(anchors)) != len(anchors):
        bad.append(f"锚点重复：{anchors}")
    frames = [str((p.get("title_basis") or {}).get("frame") or "") for p in ps]
    if len(set(frames)) != len(frames):
        bad.append(f"骨架 id 重复：{frames}")
    for p in ps:
        basis = p.get("material_basis") or []
        if not any(b.get("essential") and str(b.get("essential_why") or "").strip()
                   for b in basis):
            bad.append(f"选题 {p.get('index')} 没有 essential 关键素材")
    return bad


def cross_problems(ta: dict, tb: dict) -> list[str]:
    """两个库之间：去掉库内名词后，骨架串**一条都不许撞**。

    只比「两个列表相不相等」是不够的：上一轮那个毛病正是三条里两条逐字同构、另一条不同，
    列表一比并不相等，于是混过去了。所以这里逐条查交集。
    """
    a, b = skeleton_titles(ta), skeleton_titles(tb)
    if not a or not b:
        return ["有库没有选题，比不了"]
    bad: list[str] = []
    if a == b:
        bad.append(f"两库标题骨架逐字同构：{a}")
    shared = sorted(set(a) & set(b))
    if shared:
        bad.append(f"两库共用同一条骨架（{len(shared)} 条）：{shared}")
    return bad


def _broken(tp: dict, way: str) -> dict:
    """把一套正常选题故意做坏（用来证明判据会红，不是永远为真）。"""
    import copy
    bad = copy.deepcopy(tp)
    ps = bad["proposals"]
    if way == "anchor":
        ps[1]["title_basis"]["anchor"]["text"] = ps[0]["title_basis"]["anchor"]["text"]
    elif way == "direction":
        ps[1]["topic_direction"] = ps[0]["topic_direction"]
    elif way == "frame":
        ps[1]["title_basis"]["frame"] = ps[0]["title_basis"]["frame"]
    elif way == "essential":
        for b in ps[0]["material_basis"]:
            b.pop("essential", None)
            b.pop("essential_why", None)
    return bad


def test_title_differentiation(tmp: Path) -> None:
    from test_hooks_policy import check_ref
    # 骨架不是修辞清单：每一条都得逐级命中 references/content-system.md 的真实标题
    errs: list[str] = []
    for fid, f in INV.TITLE_FRAMES.items():
        got = check_ref(f, f"骨架 {fid}（{f.get('key')}）")
        errs.extend(got)
        if not str(f.get("tpl") or "").strip():
            errs.append(f"骨架 {fid} 没有模板")
    must(f"标题句式全部点得出原文出处（八大切入点 / 开篇技巧，共 {len(INV.TITLE_FRAMES)} 条）",
         not errs, "；".join(errs[:4]))
    must("判据会红：编一个原文里没有的切入点 → 出处校验必须报错",
         bool(check_ref({"sec": ("内容创作", "水桶流视频"),
                         "group": "开篇钩子八大切入点", "key": "岁月感切入"}, "编的骨架")),
         "编造出来的切入点居然过了校验，这条判据等于没写")

    lib_a = make_talk_lib(tmp, "库A涂料", LIB_A_LINES)
    lib_b = make_talk_lib(tmp, "库B烘焙", LIB_B_LINES)
    lib_c = make_talk_lib(tmp, "库C茶叶", LIB_C_LINES)
    tp_a = INV.build_report(lib_a, with_topics=True)["topics"]
    tp_b = INV.build_report(lib_b, with_topics=True)["topics"]
    tp_c = INV.build_report(lib_c, with_topics=True)["topics"]

    for name, tp in (("A 涂料库", tp_a), ("B 烘焙库", tp_b)):
        ps = tp.get("proposals") or []
        must(f"{name}：三套选题方向互不相同（不许三套都是认知话题）",
             len({p.get("topic_direction") for p in ps}) == len(ps) and len(ps) >= 2,
             f"方向={[p.get('topic_direction') for p in ps]}")
        must(f"{name}：三套选题锚点互不相同",
             len({p["title_basis"]["anchor"]["text"] for p in ps}) == len(ps),
             f"锚点={[p['title_basis']['anchor']['text'] for p in ps]}")
        must(f"{name}：三套选题的句式骨架 id 互不相同",
             len({p["title_basis"]["frame"] for p in ps}) == len(ps),
             f"骨架={[p['title_basis']['frame'] for p in ps]}")
        must(f"{name}：每套都有一段 essential 关键素材且写了理由",
             all(any(b.get("essential") and str(b.get("essential_why") or "").strip()
                     for b in p.get("material_basis") or []) for p in ps),
             "；".join(f"选题 {p.get('index')} 缺 essential" for p in ps
                      if not any(b.get("essential") for b in p.get("material_basis") or [])))
        print(f"     {name} 骨架：{skeleton_titles(tp)}")

    must("两个内容完全不同的库：去掉主体名后的标题骨架串一条都不撞",
         not cross_problems(tp_a, tp_b),
         "；".join(cross_problems(tp_a, tp_b)) +
         f"｜A={skeleton_titles(tp_a)} B={skeleton_titles(tp_b)}")

    # —— 判据自证：故意做坏的样本必须被判红（否则这些断言永远为真）
    for way, need in (("anchor", "锚点重复"), ("direction", "方向重复"),
                      ("frame", "骨架 id 重复"), ("essential", "essential")):
        got = topic_problems(_broken(tp_a, way))
        must(f"判据会红：把 A 库的{need}故意做坏 → 能报出来",
             any(need in g for g in got), f"实际返回 {got}")
    import copy
    must("判据会红：拿同一个库自己跟自己比骨架 → 必须报同构",
         bool(cross_problems(tp_a, copy.deepcopy(tp_a))),
         "两条完全一样的选题被判成「不同」，这条判据等于没写")
    # 上一轮的真病灶：三条里只有两条撞句（列表不相等），也必须判红
    tp_a_like = copy.deepcopy(tp_b)
    tp_a_like["proposals"][1] = copy.deepcopy(tp_a["proposals"][1])
    must("判据会红：两库只有一条骨架撞句（列表不相等但有一条逐字同构）→ 必须判红",
         bool(cross_problems(tp_a, tp_a_like)),
         "只查「两个列表相不相等」的话，这一种混得过去")

    # —— 只出两条：三块招牌被前两套吃完，第三套不许凑
    ps_c = tp_c.get("proposals") or []
    must("只出两条：库只撑得住两条差异化选题时，就只出两条（不硬凑三套）",
         len(ps_c) == 2, f"出了 {len(ps_c)} 套：{[p['topic'] for p in ps_c]}")
    must("只出两条：明写为什么少出一条（锚点用完/支撑不够，宁少勿凑）",
         "宁少勿凑" in str(tp_c.get("verdict") or ""), str(tp_c.get("verdict"))[:160])
    must("只出两条：两套的锚点仍然互不相同",
         len({p["title_basis"]["anchor"]["text"] for p in ps_c}) == len(ps_c),
         f"锚点={[p['title_basis']['anchor']['text'] for p in ps_c]}")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    if not Path(FF).exists():
        print(f"没有 ffmpeg（{FF}）：造不出样本素材，这条测试本来就没法真跑")
        return 2
    base = Path(tempfile.mkdtemp(prefix="inv_test_"))
    print(f"样本目录 {base}")
    try:
        print("① 造样本库并盘点")
        lib = base / "lib"
        lib.mkdir()
        build_sample_lib(lib)
        r = sh([PY, str(SCRIPTS / "inventory.py"), "--library", str(lib)])
        must("inventory.py 跑通", r.returncode == 0, (r.stderr or r.stdout).strip()[-400:])
        rep_p = lib / "_cutskills" / "inventory.json"
        must("产出 inventory.json + inventory.md",
             rep_p.exists() and (lib / "_cutskills" / "inventory.md").exists(),
             (r.stderr or r.stdout)[-300:])
        rep = json.loads(rep_p.read_text(encoding="utf-8"))

        print("② 数字复算 & 关键词出处")
        test_numbers_recompute(lib, rep)
        test_keyword_provenance(lib, rep)

        print("③ 画质问题计数")
        test_issue_counts(rep)

        print("④ 选题三件套（原文出处 / 素材依据 / 片长区间）")
        rep_in = INV.build_report(lib, with_topics=True)     # 带 assets_index，供文件核对
        test_topics_complete(rep_in)

        print("⑤ 端到端 --from-inventory → --pick → --to-plan")
        test_end_to_end(lib, base)

        print("⑥ 全废素材库的诚实表现")
        test_dead_library(base)

        print("⑦ 三套选题不撞招牌、不撞句式（含判据自证）")
        test_title_differentiation(base)

        # 底噪读数：量了就要有判据，没量成就要写明原因（不许假装量过）
        nf = rep["issues"]["noise_floor"]
        must("底噪：要么有读数，要么写明跳过原因",
             (nf["status"] == "measured" and nf["measured"] > 0)
             or (nf["status"] == "skipped" and bool(nf.get("reason"))),
             json.dumps(nf, ensure_ascii=False)[:200])
        if nf["status"] == "measured":
            print(f"     底噪实测：{nf['measured']} 支，偏重 {nf['count']} 支")
        else:
            print(f"     底噪跳过：{nf.get('reason')}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print()
    if fails:
        print(f"❌ 未通过 {len(fails)} 项：{'、'.join(fails)}")
        return 1
    print("✅ 素材盘点全部断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
