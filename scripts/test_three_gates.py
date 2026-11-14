#!/usr/bin/env python3
"""CutSkills · BRIEF「交互约束」的三次拍板回归：留痕、确认对象、打断点上限、提问句式。

BRIEF 原文：「每条视频最多打断用户 **3 次**：① 确认选题 ② 确认拍摄任务卡或素材
③ 确认锁定音轨和低清粗剪预览。其余环节全部用默认值推进……提问时先给出猜测让用户确认，
不问开放题。」

这条约束最容易变成一句口号，所以下面五组断言各盯一件**可执行**的事：

  ① **三次拍板各有留痕，且没确认时下游真能看见**：造「只确认了①就去渲」的样本，
     `render_hf.py` 必须退 4 并把**缺哪几拍**点名说出来；把②签掉，闸门就放行；
     再把②的记录抹掉，闸门重新拦——不许出现「没确认也能一路渲完没人知道」。
  ② **确认对象两种都跑**：入口 A 的拍摄任务卡（改了哪几镜要真的写进去）、入口 B 的素材盘点
     （排除了哪几段要真的写进去、写回盘点报告、并影响后续反推选题）。改完卡/盘点再改一次，
     留痕要自动失效（确认绑在那一版上，不是绑在一句「认可」上）。
  ③ **打断点总数 ≤ 3**：给全流程加计数留痕（`approval.json` 的 `asks`），跑一条完整链路，
     断言登记到的 gate 去重后 ≤ 3；第 4 个 gate 一进登记处就抛错（负样本）。
  ④ 拍板③ 的两条老口径不许退化：只改画面 → 音轨指纹一个字符不变；改口播 → 锁失效。
  ⑤ **开放式提问守卫**：`ask_*` 里的提问必须「先给猜测或给选项」；`input()` 只许出现在
     读 stdin 的那一个函数里、且调用方必须是 `ask_*`；全仓禁止出现「你想做什么选题？」这类
     开放句式——三条都有负样本证明它真会判红。

跑法：python3 scripts/test_three_gates.py     （全离线：不渲片，不碰真项目，全部在 /tmp）
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "scripts"
PY = sys.executable
FF = None                                # 用到时才解析（没装 ffmpeg 的机器上也要能跑 ①③④⑤）

sys.path.insert(0, str(HERE))

fails: list[str] = []
skips: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> bool:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)
    return ok


def skip(what: str) -> None:
    print(f"  ⏭ {what}")
    skips.append(what)


def env() -> dict:
    e = dict(os.environ)
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


def run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(x) for x in args], capture_output=True, text=True,
                          env=env(), input=stdin, timeout=600)


def jwrite(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def load(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sha16(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16] if Path(p).exists() else ""


def ideate(proj: Path, *args: str) -> subprocess.CompletedProcess:
    return run([PY, HERE / "ideate.py", "--project", proj, *args])


# ---------------------------------------------------------------- 造项目（两种入口）

TIMELINE = {
    "version": 5, "project": "demo", "duration_s": 12.0,
    "style_pack": "talking-head-v1", "pack": "P1-warm", "plan": "S1-conclude-fast",
    "elements": [
        {"n": 1, "kind": "clip", "track": "video", "asset": "a_口播.mov", "in": 0.0, "out": 6.0,
         "medium": "talking", "evidence": "先说结论",
         "audio": {"asset": "a_口播.mov", "in": 0.0, "out": 6.0},
         "grade_at_cut": "must-use", "why": "开场最能留人"},
        {"n": 2, "kind": "clip", "track": "video", "asset": "a_口播.mov", "in": 6.0, "out": 12.0,
         "medium": "broll", "evidence": "再说理由",
         "audio": {"asset": "a_口播.mov", "in": 6.0, "out": 12.0},
         "card": {"kind": "keyword", "text": "问三件事", "on": "三件事"},
         "grade_at_cut": "backup", "why": "例子镜头"},
    ],
}


def make_topic_project(base: Path, name: str = "film") -> Path:
    """入口 A：从一句话选题出三套提案 + 拍片卡（真跑 ideate.py，不手搓 ideation.json）。"""
    proj = base / name
    proj.mkdir(parents=True, exist_ok=True)
    r = ideate(proj, "--topic", "老楼翻新艺术涂料", "--guide", "--duration", "30")
    if r.returncode != 0:
        print(f"  ！ideate 出选题失败：{(r.stderr or r.stdout)[-300:]}")
    return proj


def add_timeline(proj: Path) -> None:
    """拍板③ 要时间轴才签得了锁：手写一份最小 v5 时间轴（不渲、不依赖素材库）。"""
    jwrite(proj / "timeline.json", TIMELINE)
    jwrite(proj / "brief.json", {
        "version": 1, "audience": "珠海金湾的街坊", "change": "比价前先问三件事",
        "length_s": 12, "length_band": "short", "style_pack": "talking-head-v1",
        "pack": "P1-warm", "plan": "S1-conclude-fast",
    })


def make_inventory(base: Path) -> tuple[Path, Path] | None:
    """入口 B：造一个真素材库（3 支合成片 + 索引 + 真盘点），再按盘点反推选题。"""
    global FF
    try:
        sys.path.insert(0, str(HERE))
        from libs import ffmpeg_bin                      # noqa: PLC0415
        FF = ffmpeg_bin()
    except Exception as e:                               # noqa: BLE001
        skip(f"素材库夹具（ffmpeg 解析不出来：{e}）")
        return None
    lib = base / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    clips = [("a_talk.mov", "0x2f5f8a", "talking"), ("b_detail.mov", "0x3b5f2f", "detail"),
             ("c_wide.mov", "0x5f2f3b", "still")]
    for name, color, _ in clips:
        p = lib / name
        if p.exists():
            continue
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c={color}:s=360x640:d=4:r=30", "-f", "lavfi", "-i",
                            "anoisesrc=d=4:c=pink:r=44100:a=0.05", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            skip(f"素材库夹具（合成片子失败：{(r.stderr or '')[-160:]}）")
            return None
    phrases = {
        "a_talk.mov": [("其实艺术涂料根本不是越贵越好，我做了十年", "0", "2"),
                       ("好多人都以为进口的就一定好", "2", "4")],
        "b_detail.mov": [("你看这个工序，底料这一步偷工就全废", "0", "2"),
                         ("我们打磨要过三遍才算合格", "2", "4")],
        "c_wide.mov": [("做完之后客户到门店来看整体效果", "0", "2"),
                       ("摸手感就知道值不值这个报价", "2", "4")],
    }
    assets = []
    for name, _, shot in clips:
        tag = {"talking": "艺术涂料现场", "detail": "施工工序特写", "still": "成品展厅"}[shot]
        assets.append({
            "id": name, "uid": f"u-{name}", "kind": "video", "duration": 4.0,
            "orientation": "portrait", "batch": "自测",
            "spec": {"width": 360, "height": 640, "display_width": 1080,
                     "display_height": 1920, "fps": 30},
            "audio": {"peak_db": -6.0},
            "quality": {"luma": {"mean": 120.0}, "blur": {"mean": 40.0}},
            "fingerprint": f"fp-{name}",
            "transcript": {"phrases": [{"text": t, "start": float(s), "end": float(e)}
                                       for t, s, e in phrases[name]]},
            "segments": [{"in": 0.0, "out": 4.0, "grade": "must-use", "tag": tag,
                          "shot": shot, "summary": tag, "why": "自测片段", "flags": []}],
        })
    jwrite(lib / "_cutskills" / "index.json", {"version": 1, "assets": assets})
    r = run([PY, HERE / "inventory.py", "--library", lib,
             "--vfr-limit", "1", "--grain-limit", "1", "--audio-limit", "1"])
    if r.returncode != 0:
        skip(f"素材库夹具（inventory.py 没跑成：{(r.stderr or r.stdout)[-200:]}）")
        return None
    proj = base / "film-inv"
    proj.mkdir(parents=True, exist_ok=True)
    r = ideate(proj, "--from-inventory", lib, "--duration", "30")
    if r.returncode != 0:
        skip(f"素材库夹具（--from-inventory 没跑成：{(r.stderr or r.stdout)[-200:]}）")
        return None
    return lib, proj


# ---------------------------------------------------------------- ① 留痕与下游可见

def check_ledger(base: Path) -> None:
    print("① 三次拍板各有留痕；没确认时下游拦得住、并说清缺哪一拍")
    proj = make_topic_project(base / "ledger")
    approval = proj / "approval.json"
    must("新项目还没有任何拍板留痕", not approval.exists())
    r = ideate(proj, "--gates")
    must("--gates 能一眼看出三拍全红",
         r.returncode == 0 and r.stdout.count("🔴") == 3, r.stdout[-300:])

    r = ideate(proj, "--pick", "2", "--hook", "1")
    gt = load(approval).get("gates") or {}
    must("拍板①（--pick/--hook）留下记录", bool(gt.get("1-topic", {}).get("ok")),
         f"gates={sorted(gt)}")
    must("①的记录带「谁/何时/凭什么」三样",
         bool(gt.get("1-topic", {}).get("at")) and bool(gt.get("1-topic", {}).get("note"))
         and bool(gt.get("1-topic", {}).get("where")), str(gt.get("1-topic"))[:200])
    must("①签了不等于②③也签了", not gt.get("2-shots") and not gt.get("3-cut"),
         f"gates={sorted(gt)}")
    must("①的留痕也进了 decisions.md（人看的那份）",
         (proj / "decisions.md").exists()
         and "选定方案 2" in (proj / "decisions.md").read_text(encoding="utf-8"))
    must("①的打断登记进 asks", [a["gate"] for a in (load(approval).get("asks") or [])]
         == ["1-topic"], str(load(approval).get("asks"))[:200])

    # ---- 只确认了①就去渲：必须有明确结果，不许静默放行
    add_timeline(proj)
    r = run([PY, HERE / "render_hf.py", "--project", proj])
    err = r.stderr or ""
    must("只签了①就渲 → 退出码 4（被拦）", r.returncode == 4, f"退出码 {r.returncode}｜{err[-200:]}")
    must("拦的时候点名「还没拍板（lock 缺）」", "还没拍板" in err, err[-300:])
    must("拦的时候点名「拍板② 还没签」", "拍板② 还没签" in err, err[-300:])
    must("拦的时候说清三次拍板各是哪三次", "①选题" in err and "③锁定音轨" in err, err[-300:])

    # ---- ②签掉
    r = ideate(proj, "--approve-shots", "认可")
    must("拍板②（--approve-shots）签得上", r.returncode == 0
         and bool(load(approval).get("gates", {}).get("2-shots", {}).get("ok")),
         (r.stderr or r.stdout)[-300:])
    r = run([PY, HERE / "render_hf.py", "--project", proj])
    err = r.stderr or ""
    must("②签了、③没签 → 仍然退 4（缺③）", r.returncode == 4, f"退出码 {r.returncode}")
    must("这时不该再抱怨「拍板② 还没签」", "拍板② 还没签" not in err, err[-300:])

    # ---- ③签掉（顺带把拍板③ 的老口径一起签）
    r = run([PY, HERE / "plan_card.py", "--project", proj, "--approve", "认可"])
    must("拍板③（plan_card --approve）签得上", r.returncode == 0
         and (proj / "lock.json").exists(), (r.stderr or r.stdout)[-300:])
    gt = load(approval).get("gates") or {}
    must("③的记录落在同一个 approval.json 里（没另造一套留痕）",
         bool(gt.get("3-cut", {}).get("ok")) and bool(gt.get("1-topic")) and bool(gt.get("2-shots")),
         f"gates={sorted(gt)}")
    must("签③ 没把①② 的记录盖掉（顶层老字段与 gates 共存）",
         load(approval).get("approved") is True and bool(gt.get("2-shots")),
         str(sorted(load(approval)))[:200])
    r = run([PY, HERE / "render_hf.py", "--project", proj])
    err = r.stderr or ""
    must("三拍齐了 → 闸门放行（不再退 4）", r.returncode != 4, f"退出码 {r.returncode}｜{err[-200:]}")
    must("放行时能看见「已拍板」", "已拍板" in err, err[-200:])

    # ---- ①只有②③ 就渲：不硬拦（老项目没这个历史），但每一次渲都要把这事念出来
    keep = load(approval)
    stripped = json.loads(json.dumps(keep))
    stripped["gates"].pop("1-topic", None)
    jwrite(approval, stripped)
    r = run([PY, HERE / "render_hf.py", "--project", proj])
    must("①没留痕但不硬拦（老项目也能渲）", r.returncode != 4, f"退出码 {r.returncode}")
    must("但每一次渲都念出「拍板① 没有留痕」",
         "拍板①" in (r.stderr or "") and "没有留痕" in (r.stderr or ""), (r.stderr or "")[-300:])
    jwrite(approval, keep)                                  # 还原，后面的断言还要用

    # ---- 把②的证据抹掉：闸门必须重新拦（不是签过一次就永久放行）
    doc = load(approval)
    doc["gates"].pop("2-shots", None)
    jwrite(approval, doc)
    r = run([PY, HERE / "render_hf.py", "--project", proj])
    must("③还在但②被抹掉 → 重新退 4", r.returncode == 4, f"退出码 {r.returncode}")
    must("并指名道姓说②没签", "拍板② 还没签" in (r.stderr or ""), (r.stderr or "")[-300:])

    # ---- 硬渲逃逸口：--force 准用，但跳过了哪一拍必须自己报出来（不靠人猜）
    r = run([PY, HERE / "render_hf.py", "--project", proj, "--force"])
    err = r.stderr or ""
    must("--force 能绕过闸门（不进真渲，能跑到后面的错）", r.returncode != 4, f"退出码 {r.returncode}")
    must("--force 时把「跳过了拍板②」念出来", "--force 跳过了拍板②" in err, err[-300:])


# ---------------------------------------------------------------- ② 两种确认对象

def check_shoot_card(base: Path) -> None:
    print("②A 拍摄任务卡：认可 + 改了哪几镜，都要真的写进留痕")
    proj = make_topic_project(base / "shots")
    guide = proj / "shoot-guide.md"
    must("拍片卡已生成（确认对象真存在）", guide.exists())

    note = "第3镜改成俯拍特写，第4镜不要了，其余认可"
    r = ideate(proj, "--approve-shots", note)
    g2 = load(proj / "approval.json").get("gates", {}).get("2-shots") or {}
    det = g2.get("detail") or {}
    must("--approve-shots 签得上并落进 gates.2-shots", r.returncode == 0 and g2.get("ok"),
         (r.stderr or r.stdout)[-300:])
    must("确认对象类型记成「拍摄任务卡」", det.get("kind") == "shoot-card", str(det.get("kind")))
    must("留痕绑在那一版卡上（存了卡的指纹）",
         det.get("sha") == hashlib.sha256(guide.read_bytes()).hexdigest(), str(det.get("sha")))
    must("改了哪几镜真写进去了 [3,4]（不是只存一句原话）",
         det.get("changed_shots") == [3, 4], str(det.get("changes")))
    must("每一镜的改动说明也留着",
         len(det.get("changes") or []) == 2
         and all(c.get("note") for c in det["changes"]), str(det.get("changes")))
    md = (proj / "decisions.md").read_text(encoding="utf-8") if (proj / "decisions.md").exists() else ""
    must("改动同时进了 decisions.md（人看的那份）", "第3镜" in md and "第4镜" in md, md[-200:])

    # ---- 卡改了 → 旧确认失效
    r = ideate(proj, "--hook", "2")            # 换钩子会重写拍片卡（第 1 镜台词变了）
    r = ideate(proj, "--gates")
    out = r.stdout
    must("卡改过之后，②的确认自动失效（所在行是 🔴）",
         re.search(r"🔴 2-shots", out) is not None, out[-400:])
    must("失效原因写明「确认之后又改过」", "又改过" in out, out[-400:])

    # ---- 把卡删了也不能混过拍板②（脚本里还有要真拍的镜，就得有卡）
    guide.unlink()
    r = ideate(proj, "--gates")
    must("删掉拍片卡 → 拍板② 仍然算欠着（不能靠删文件绕过）",
         re.search(r"🔴 2-shots", r.stdout) is not None and "不在了" in r.stdout, r.stdout[-400:])


def check_material(base: Path) -> None:
    print("②B 素材盘点：认可 + 排除了哪几段，写回报告并影响反推选题")
    made = make_inventory(base / "inv")
    if made is None:
        return
    lib, proj = made
    inv = lib / "_cutskills" / "inventory.json"
    md = lib / "_cutskills" / "inventory.md"
    must("盘点报告已生成（确认对象真存在）", inv.exists() and md.exists())

    r = ideate(proj, "--approve-material", "这个库拍得出这几条，但 b_detail 那段另有用途，别用",
               "--exclude-clip", "b_detail.mov")
    g2 = load(proj / "approval.json").get("gates", {}).get("2-shots") or {}
    det = g2.get("detail") or {}
    must("--approve-material 签得上并落进 gates.2-shots", r.returncode == 0 and g2.get("ok"),
         (r.stderr or r.stdout)[-300:])
    must("确认对象类型记成「素材盘点」", det.get("kind") == "inventory", str(det.get("kind")))
    must("confirmation 绑在那一版盘点报告上",
         det.get("sha") == hashlib.sha256(inv.read_bytes()).hexdigest(), str(det.get("sha"))[:32])
    must("排除了哪几段写进留痕", [x.get("asset") for x in (det.get("excluded") or [])]
         == ["b_detail.mov"], str(det.get("excluded"))[:200])
    must("排除项写回盘点报告本身（不是只存在 approval.json）",
         [x.get("asset") for x in (load(inv).get("user_exclusions") or [])] == ["b_detail.mov"],
         str(load(inv).get("user_exclusions"))[:200])
    must("盘点报告（人看的那份）里也留痕",
         "用户点名的排除" in md.read_text(encoding="utf-8")
         and "b_detail.mov" in md.read_text(encoding="utf-8"))
    must("盘点里既有数字一个没动（只加排除，不重算）",
         (load(inv).get("totals") or {}).get("assets") == 3, str(load(inv).get("totals"))[:120])

    # ---- 排除真的影响后续反推选题：重跑 --from-inventory，被点名的段不再当候选
    r = ideate(proj, "--from-inventory", lib, "--duration", "30")
    out = (r.stdout or "") + (r.stderr or "")
    must("重跑 --from-inventory 会按排除摘掉画面（并说摘了几段）",
         "摸掉了" in out and "排除" in out, out[-400:])
    idea = load(proj / "ideation.json")
    basis_assets = [b.get("asset") for p in (idea.get("proposals") or [])
                    for b in (p.get("material_basis") or [])]
    must("被排除的那支不再出现在任何一套选题的素材依据里",
         "b_detail.mov" not in basis_assets, str(sorted(set(basis_assets))))

    # ---- 盘点重跑（报告换了一版）→ 旧确认失效
    r = run([PY, HERE / "inventory.py", "--library", lib,
             "--vfr-limit", "1", "--grain-limit", "1", "--audio-limit", "1"])
    r = ideate(proj, "--gates")
    must("盘点重跑之后②的确认失效（并说明要重新确认）",
         re.search(r"🔴 2-shots", r.stdout) is not None and "又改过" in r.stdout, r.stdout[-400:])


# ---------------------------------------------------------------- ③ 打断点 ≤ 3

def check_budget(base: Path) -> None:
    print("③ 一条片子的必经打断点 ≤ 3（可执行计数，不靠人数文档）")
    sys.path.insert(0, str(HERE))
    import ideate as IDE                                # noqa: PLC0415

    must("预算常量就是 3", IDE.INTERRUPT_BUDGET == 3, str(IDE.INTERRUPT_BUDGET))
    proj = make_topic_project(base / "budget")
    add_timeline(proj)
    ideate(proj, "--pick", "1")
    ideate(proj, "--approve-shots", "认可")
    add_timeline(proj)                                  # 拍板③ 之前把时间轴落定
    r = run([PY, HERE / "plan_card.py", "--project", proj, "--approve", "认可"])
    must("完整链路：选题 → 拍板② → 拍板③ 都跑通", r.returncode == 0, (r.stderr or r.stdout)[-200:])

    asks = load(proj / "approval.json").get("asks") or []
    ids = [a.get("gate") for a in asks]
    must("问过的打断点去重后 ≤ 3", len(set(ids)) <= 3, f"{sorted(set(ids))}")
    must("三次都登记到了（不多不少）",
         sorted(set(ids)) == sorted(IDE.REQUIRED_GATES), f"{sorted(set(ids))}")
    must("每一次都写清「在哪问、问什么」",
         all(a.get("where") and a.get("question") for a in asks), str(asks)[:300])
    r = ideate(proj, "--gates")
    must("--gates 自报的打断数 ≤ 3", "上限 3" in r.stdout and "3 个 / 上限 3" in r.stdout,
         r.stdout[-300:])

    # ---- 负样本：第 4 个必经打断点一登记就得炸
    raised = ""
    try:
        IDE.note_interrupt(proj, "4-broll", "test", "再问一句？")
    except ValueError as e:
        raised = str(e)
    must("第 4 个 gate 直接抛错（结构上就加不进来）", bool(raised) and "三次拍板" in raised, raised)
    must("没被写进去", len(load(proj / "approval.json").get("asks") or []) == 3,
         str(load(proj / "approval.json").get("asks"))[:200])

    # ---- 同一入口重复跑不会把计数灌成四次
    ideate(proj, "--approve-shots", "再确认一遍")
    idea2 = ideate(proj, "--pick", "1")
    n = len(load(proj / "approval.json").get("asks") or [])
    must("同一入口重复跑不重复计数", n == 3 and idea2.returncode == 0, f"asks={n}")


# ---------------------------------------------------------------- ④ 音轨口径不退化

def check_av(base: Path) -> None:
    print("④ 只改画面 → 音轨指纹不变；改口播 → 锁失效（老口径补进测试防退化）")
    proj = make_topic_project(base / "av")
    add_timeline(proj)
    ideate(proj, "--approve-shots", "认可")
    r = run([PY, HERE / "plan_card.py", "--project", proj, "--approve", "认可"])
    must("签锁成功", r.returncode == 0 and (proj / "lock.json").exists(),
         (r.stderr or r.stdout)[-200:])
    l1 = load(proj / "lock.json")
    fp1 = l1.get("audio_fp")
    must("锁里记了音轨指纹", bool(fp1), str(sorted(l1)))

    # 只改画面：换卡面、换解释、换视觉素材，音源与切点一个字不动
    tl = load(proj / "timeline.json")
    tl["elements"][1]["card"]["text"] = "改过的卡面"
    tl["elements"][1]["why"] = "换个说法解释为什么改画面"
    tl["elements"][0]["asset"] = "b_替身画面.mov"
    jwrite(proj / "timeline.json", tl)
    r = run([PY, HERE / "plan_card.py", "--project", proj, "--approve", "只改了画面"])
    l2 = load(proj / "lock.json")
    must("只改画面后重签成功", r.returncode == 0, (r.stderr or r.stdout)[-200:])
    must("重签后时间轴 sha 变了（锁确实重签过）", l2.get("sha256") != l1.get("sha256"))
    must("重签后音轨指纹一个字符不变（只改画面不用重听音频）",
         l2.get("audio_fp") == fp1, f"{str(fp1)[:12]} → {str(l2.get('audio_fp'))[:12]}")

    # 改口播：挪一个切点
    tl = load(proj / "timeline.json")
    tl["elements"][0]["audio"]["in"] = round(float(tl["elements"][0]["audio"]["in"]) + 0.4, 3)
    jwrite(proj / "timeline.json", tl)
    r = run([PY, HERE / "lock.py", "--project", proj, "--verify"])
    must("改口播切点后 lock.verify 判失效（退出码 1）", r.returncode == 1,
         f"退出码 {r.returncode}｜{(r.stdout or r.stderr).strip()[-160:]}")
    r = run([PY, HERE / "plan_card.py", "--project", proj, "--approve", "改了口播"])
    fp3 = load(proj / "lock.json").get("audio_fp")
    must("改口播后音轨指纹跟着变", r.returncode == 0 and fp3 != fp1,
         f"{str(fp1)[:12]} → {str(fp3)[:12]}")


# ---------------------------------------------------------------- ⑤ 开放式提问守卫

# 「先给猜测 / 给选项」的标记：提问文案里必须至少有一个，否则就是拿开放题审人。
GUESS_MARKS = ("我猜", "对吗", "是不是", "要不要", "回车", "还是", "挑一个", "1)", "2)", "3)")
# 全仓禁止的开放句式（BRIEF 点名的反面样本就是「你想做什么选题？」这一类）
OPEN_PATTERNS = (
    r"你想(?:做|要|拍|用|改|选)?什么",
    r"你要(?:做|拍|选)(?:什么|哪种|哪类)",
    r"想做什么(?:内容|选题|视频|片子)",
    r"给谁看？",
    r"有什么想法",
    r"你想要什么",
)
# 提问必须写成「我猜…」的模块（BRIEF 正例：track.py 的 guess_line）
ASK_FILES = ("brief.py", "track.py", "ideate.py")


def _docstring_lines(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for ln in range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1):
                    out.add(ln)
    return out


def _ask_prompts(path: Path) -> list[tuple[int, str]]:
    """抽取「问句」：ask_*/guess_*/options_* 函数里的字符串 + QUESTION_TEXT 之类的常量。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    skip = _docstring_lines(tree)
    found: list[tuple[int, str]] = []

    def collect(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                    and sub.lineno not in skip and "？" in sub.value:
                found.append((sub.lineno, sub.value))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                (node.name.startswith(("ask_", "guess_", "options_"))):
            collect(node)
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and ("QUESTION" in t.id or t.id.endswith("_TEXT"))
                for t in node.targets):
            collect(node)
    return found


def ask_style_violations(path: Path) -> list[str]:
    """一条文件里的提问形状：ask_* 的问句必须「先给猜测」或「给选项」，否则就是开放题。"""
    out: list[str] = []
    for lineno, text in _ask_prompts(path):
        if not any(m in text for m in GUESS_MARKS):
            out.append(f"{path.name}:{lineno} 「{text[:40]}」")
    return out


def open_question_hits(paths: list[Path]) -> list[str]:
    """整仓扫「开放句式」。只看真的会被人看见的字符串字面量（注释/docstring 不算）。"""
    hits: list[str] = []
    for p in paths:
        try:
            tree_p = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        skip = _docstring_lines(tree_p)
        for node in ast.walk(tree_p):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if node.lineno in skip:
                continue
            for pat in OPEN_PATTERNS:
                if re.search(pat, node.value):
                    hits.append(f"{p.name}:{node.lineno} 「{node.value.strip()[:50]}」")
                    break
    return hits


def check_question_guard(base: Path) -> None:
    print("⑤ 提问守卫：先给猜测、不问开放题（三条判据各自带负样本）")
    # ---- A：ask_* 里的问句必须是「我猜…对吗」或「给选项」
    bad: list[str] = []
    total = 0
    for name in ASK_FILES:
        p = HERE / name
        if not p.exists():
            continue
        total += len(_ask_prompts(p))
        bad += ask_style_violations(p)
    must("有提问文案被抽到（守卫不是空转）", total >= 4, f"抽到 {total} 条")
    must("ask_* 里没有开放式提问（每条都先给猜测/给选项）", not bad, "；".join(bad[:4]))

    # ---- A'：负样本——真文件里的开放题必须被同一条扫描器判红
    fake = base / "qguard-bad.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text('def ask_topic():\n    print("你想做什么选题？")\n\n\n'
                    'QUESTION_TEXT = {"audience": "给谁看？一句话，具体到一类人"}\n',
                    encoding="utf-8")
    a_hits = ask_style_violations(fake)
    must("负样本文件：「给谁看？」被形状判据判红", len(a_hits) >= 1, str(a_hits))

    # ---- B：input() 只许出现在读 stdin 的那一个函数里，且调用方必须是 ask_*
    src = (HERE / "brief.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    input_funcs = [n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                           and c.func.id == "input" for c in ast.walk(n))]
    must("input() 只出现在一个地方（读 stdin 的唯一入口）", input_funcs == ["read_answer"],
         str(input_funcs))
    callers = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                       and c.func.id == "read_answer" for c in ast.walk(n))}
    must("read_answer 的调用方全是 ask_*（提问不许藏在别的函数里）",
         callers == {"ask_guess"}, str(sorted(callers)))
    # 负样本：把「提问藏在一个不许提问的函数里」的写法必须被认出来
    hidden = base / "qguard-hidden.py"
    hidden.write_text('def read_answer():\n    return input("> ")\n\n\n'
                      'def render_card(x):\n    return read_answer()\n', encoding="utf-8")
    hp = hidden.read_text(encoding="utf-8") if hidden.exists() else ""
    c_hidden = {n.name for n in ast.walk(ast.parse(hp))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id == "read_answer" for c in ast.walk(n))}
    must("负样本文件：把 read_answer 藏在 render_card 里会被判红",
         c_hidden != {"ask_guess"}, str(sorted(c_hidden)))

    # ---- C：全仓禁止开放句式
    files = [p for p in sorted((ROOT / "scripts").glob("*.py")) if p.name != Path(__file__).name]
    hits = open_question_hits(files)
    must(f"全仓 {len(files)} 支脚本里没有「你想做什么选题？」这类开放句式",
         not hits, "；".join(hits[:5]))

    # ---- C'：负样本走同一个扫描器（生成一个真文件再扫，不是只对字符串做正则）
    c_hits = open_question_hits([fake])
    must("负样本文件同时命中「开放句式」黑名单", len(c_hits) >= 1, str(c_hits))
    for sample in ("你想做什么选题？", "你要拍哪种片子？", "给谁看？一句话"):
        one = base / "qguard-one.py"
        one.write_text(f'print("{sample}")\n', encoding="utf-8")
        must(f"开放句式负样本判红：「{sample}」", len(open_question_hits([one])) == 1, sample)

    # ---- 正例不能被误杀（track.py 的「我猜你是…，对吗？」）
    positive = "我猜你是门店轨（实体店老板），因为你的原话里出现了「店」，对吗？"
    good = base / "qguard-good.py"
    good.write_text(f'def guess_line():\n    return "{positive}"\n', encoding="utf-8")
    must("正例（track.py 的猜测句）不被误杀",
         not open_question_hits([good]) and not ask_style_violations(good))


# ---------------------------------------------------------------- main

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cutskills-gates-") as td:
        base = Path(td)
        check_ledger(base)
        check_shoot_card(base)
        check_material(base)
        check_budget(base)
        check_av(base)
        check_question_guard(base)
    print()
    if fails:
        print(f"❌ 未通过（{len(fails)} 项）：{'、'.join(fails)}")
        return 1
    tail = f"（跳过 {len(skips)} 项：{'；'.join(skips)}）" if skips else ""
    print(f"✅ 三次拍板全绿{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
