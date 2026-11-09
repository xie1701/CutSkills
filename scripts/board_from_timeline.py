#!/usr/bin/env python3
"""timeline.json v4 → 制作看板（board.js + board.html）。

看板是**视图**，真值仍是 `timeline.json`。所以这里一条铁律：每镜都写 `fixed` = 真实秒数，
不让看板按字数估时长——不然预演和成片对不上，「逐镜起点差 ≤0.5s」就无从谈起。

字段映射写死在 `references/board-fusion.md` §2/§3，改映射改那份文档，别只改代码。

用法：
  python3 scripts/board_from_timeline.py --project <项目目录> [--library <素材库根>]
                                         [--out board] [--title 标题] [--no-build]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
VENDOR = SKILL / "vendor" / "production-board"
BUILD = VENDOR / "scripts" / "build_board.py"

# 卡片种类 → CC0 库里的音效（和 render_hf.CARD_SFX 保持一致，改一处改两处）
CARD_SFX = {
    "keyword": "pop-medium-in",
    "behind": "impact-light-firm",
    "cutout": "impact-light-firm",
    "idcard": "whoosh-medium-soft",
    "gen": "appear-materialise",
}

MODE_DEF = {
    "A": {"k": "a", "name": "口播全屏", "short": "A",
          "desc": "人物出镜，右侧留空。", "when": "开场、提问、收尾。", "layers": ["a"]},
    "AO": {"k": "a", "name": "口播 + 大字", "short": "A+",
           "desc": "口播画面压一张大字；kind=behind 的背上抠像层，字在人头后面。",
           "when": "说出一句要被人记住的话。", "layers": ["a", "ov"]},
    "S": {"k": "br", "name": "实拍 / 素材", "short": "B-R",
          "desc": "库里真素材，已经到位，不用再拍。", "when": "交代场景、给证据。", "layers": ["br"]},
    "E": {"k": "br", "name": "代码画的画面", "short": "GEN",
          "desc": "由 gen_scene.py 生成，不依赖任何素材。", "when": "讲不清的东西用画面讲。", "layers": ["br"]},
    "T": {"k": "br", "name": "标题卡", "short": "T",
          "desc": "1.4 秒章节卡。", "when": "分章开头。", "layers": ["br"]},
}


def resolve_lib(proj: Path, given: str | None) -> Path:
    if given:
        return Path(given).expanduser().resolve()
    cand = proj.parents[2] if len(proj.parents) > 2 else proj
    if (cand / "_cutskills" / "index.json").exists():
        return cand
    raise SystemExit(f"找不到素材库：{cand}/_cutskills/index.json 不存在。"
                     f"项目放在别处就加 --library。")


def el_dur(el: dict) -> float:
    """和 render_hf.el_dur 同一口径。两处算出来的时长必须一模一样，否则看板在骗人。"""
    if el.get("kind") == "clip":
        return round(float(el["out"]) - float(el["in"]), 3)
    return round(float(el.get("dur") or 0.0), 3)


def asset_label(asset: str) -> str:
    """素材名 → 人看得懂的短名。我们的命名是 YYMMDD_批次_内容_级别_原编号.ext。"""
    parts = Path(asset).stem.split("_")
    if len(parts) >= 3:
        return f"{parts[1]}·{parts[2]}"
    return Path(asset).stem


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return default


def build_board(proj: Path, lib: Path, *, title: str | None = None) -> dict:
    tl = load_json(proj / "timeline.json")
    if not tl:
        raise SystemExit(f"{proj}/timeline.json 不存在或坏了")
    els = tl.get("elements") or []
    if not els:
        raise SystemExit("时间轴里一格都没有")

    pack_name = tl.get("style_pack") or "talking-head-v1"
    pack_p = SKILL / "styles" / f"{pack_name}.yaml"
    pack = yaml.safe_load(pack_p.read_text()) if pack_p.exists() else {}
    canvas = pack.get("canvas") or {}
    W, H = int(canvas.get("width") or 1080), int(canvas.get("height") or 1920)
    FPS = int(canvas.get("fps") or 30)
    sub = pack.get("subtitle") or {}
    emp = pack.get("emphasis") or {}
    top_h = float(emp.get("top_h") or 0.30)
    kw_color = str(emp.get("keyword_color") or "#F6EB34")

    amap = load_json(proj / "audio-map.json") or {}
    blocks_by_el: dict[int, list] = {}
    for b in amap.get("blocks") or []:
        blocks_by_el.setdefault(int(b.get("el") or 0), []).append(b)
    cards = (load_json(proj / "out" / "cards.json") or load_json(proj / "cards.json") or {}).get("cards") or []
    scenes = (load_json(proj / "scenes.json") or {}).get("scenes") or []
    brief = load_json(proj / "brief.json") or {}
    thesis = load_json(proj / "thesis.json") or {}
    index = load_json(lib / "_cutskills" / "index.json") or {}
    by_id = {a["id"]: a for a in index.get("assets") or []}

    # ---- 排时间轴：绝对秒 → 顺排 + gap -------------------------------------
    rows, t, prev_end, pending_gap = [], 0.0, None, 0.0
    for el in els:
        kind = el.get("kind") or "clip"
        dur = el_dur(el)
        if dur <= 0:
            continue
        overlay = (el.get("track") or "video") == "overlay"
        if overlay:
            rows.append({"el": el, "overlay": True, "t0": float(el.get("at") or 0.0), "dur": dur})
            continue
        if kind == "silence":
            # 刻意的空 = 看板里的 gap，不是一个镜头。不这么处理会多出一个没有画面的镜
            pending_gap = round(pending_gap + dur, 3)
            t = round(t + dur, 3)
            prev_end = t
            continue
        gap = round(pending_gap + (t - prev_end if prev_end is not None else 0.0), 3)
        pending_gap = 0.0
        rows.append({"el": el, "overlay": False, "t0": round(t, 3), "dur": dur, "gap": gap})
        t = round(t + dur, 3)
        prev_end = t
    video_rows = [r for r in rows if not r["overlay"]]
    ov_rows = [r for r in rows if r["overlay"]]
    total = round(t, 3)

    # ---- 章节：相邻同类 beat 合并（和 skeleton.py 同一做法）-------------------
    chapters, seen = [], {}
    for r in video_rows:
        b = int(r["el"].get("beat") or 1)
        if b not in seen:
            cid = f"c{len(chapters)}"
            seen[b] = cid
            chapters.append({"id": cid, "name": f"第 {len(chapters) + 1} 段",
                             "en": f"PART {len(chapters) + 1:02d}"})
    if not chapters:
        chapters = [{"id": "c0", "name": "全片", "en": "ALL"}]

    # ---- 一格 → 一镜 ---------------------------------------------------------
    shots, prompt_map = [], {}
    for i, r in enumerate(video_rows, 1):
        el = r["el"]
        n = int(el.get("n") or i)
        sid = f"S{n:02d}"
        kind = el.get("kind") or "clip"
        medium = el.get("medium") or ("talking" if el.get("asset") else "broll")
        t0, dur = r["t0"], r["dur"]

        # 落在这一镜里的卡 / 代码画面 / 叠加层
        mine = [c for c in cards if t0 - 0.02 <= float(c.get("start") or 0) < t0 + dur - 0.02]
        my_scenes = [s for s in scenes if t0 - 0.02 <= float(s.get("at") or 0) < t0 + dur - 0.02]
        my_ov = [o for o in ov_rows if t0 - 0.02 <= o["t0"] < t0 + dur - 0.02]

        if kind == "gen":
            mode = "E"
        elif medium == "talking":
            mode = "AO" if (mine or my_scenes) else "A"
        else:
            mode = "S"

        vo = "".join(str(b.get("text") or "") for b in blocks_by_el.get(n, [])).strip()
        if not vo:
            vo = str(el.get("evidence") or "")

        label = asset_label(el["asset"]) if el.get("asset") else ""
        # 手绘分镜
        m: list = []
        if mode in ("A", "AO"):
            m.append(["pres", 0, 0, 100, 100])
        elif kind in ("clip", "image"):
            m.append(["photo", 0, 0, 100, 100, label or "素材"])
            if dur >= 5:
                m.append(["cam", 8, 10, 84, 70, "push", "慢推"])
        for s in my_scenes:
            m.append(["photo", 8, 14, 84, 60, f"代码画：{str(s.get('what'))[:16]}", 0,
                      {"t": round(float(s.get("at")) - t0, 2), "a": "fade"}])
            m.append(["note", 8, 8, "代码生成，素材库没有这一镜", 2.2])
        for c in mine:
            txt = str(c.get("text") or "")
            t_in = round(float(c.get("appear_t") or c.get("start") or t0) - t0, 2)
            if c.get("kind") == "behind":
                m.append(["ttl", 50, top_h * 100, txt, "", 11, {"t": t_in, "a": "pop"}])
                m.append(["note", 8, 22, "压在人头后面：抠像层贴回，字被扫到边", 2.2])
            elif c.get("kind") == "idcard":
                m.append(["tag", 62, 24, txt, "", -2, {"t": t_in, "a": "left"}])
            else:
                m.append(["ttl", 50, top_h * 100, txt, "", 10, {"t": t_in, "a": "pop"}])
        for o in my_ov:
            m.append(["photo", 20, 30, 60, 40, "叠加层（代码画面）", 0,
                      {"t": round(o["t0"] - t0, 2), "a": "fade"}])

        why = str(el.get("why") or "")
        d_bits = []
        if label:
            d_bits.append(f"库里真素材：{Path(el['asset']).name}")
        if why:
            d_bits.append(why)
        if mine:
            d_bits.append("大字：" + "、".join(str(c.get('text')) for c in mine))
        for s in my_scenes:
            d_bits.append(f"代码画面（待接入）：「{s.get('what')}」——{s.get('for')}")
        d = "；".join(d_bits)

        # 音效：只在卡上场时打；其余切点不出声（这也是诚实信息）
        sfx = "；".join(CARD_SFX.get(str(c.get("kind") or "keyword"), "pop-medium-in")
                       for c in mine) or "切点不出声"

        rec = None
        if kind in ("clip", "image") and el.get("asset"):
            rec = {"dev": "素材", "what": Path(el["asset"]).name,
                   "steps": ["已在库内，看板里直接到位", f"入库级别：{el.get('grade_at_cut') or '未标'}"]}
        elif kind == "gen":
            rec = {"dev": "代码生成", "what": "gen_scene.py", "steps": ["规格见「提示词」页"]}

        shot = {
            "id": sid,
            "ch": seen.get(int(el.get("beat") or 1), chapters[0]["id"]),
            "mode": mode,
            "vo": vo,
            "m": m,
            "d": d or "（待补画面说明）",
            "fixed": dur,
            "gap": r["gap"],
            "sfx": sfx,
        }
        if rec:
            shot["rec"] = rec
        if kind == "gen":
            br_id = f"BR{n:02d}"
            shot["br"] = {"id": br_id, "k": "全屏", "dur": f"{dur:.1f}s",
                          "d": why or "代码画的画面"}
        if my_ov:
            o = my_ov[0]
            shot["clips"] = {"ov": [round(o["t0"] - t0, 3), o["dur"]]}
            shot.setdefault("br", {"id": f"OV{n:02d}", "k": "叠加",
                                   "dur": f"{o['dur']:.1f}s", "d": why or "叠加层"})
        refs = []
        for c in mine:
            if c.get("kind") == "behind":
                refs.append("动效风格：柒造暗金（styles/talking-head-v1.yaml）——"
                            "大字压在人后，抠像层贴回，只用背景露出部分")
        for s in my_scenes:
            refs.append(f"动效风格：柒造暗金（styles/talking-head-v1.yaml）——{s.get('what')}")
        if refs:
            shot["ref"] = refs
        # 待核实：库里素材带第三方字样 / 口播里有借鉴说法
        flags = []
        for s in my_scenes:
            if str(s.get("status") or "").find("待") >= 0:
                flags.append(f"这处代码画面还没接进时间轴（scenes.json 有、timeline.json 没有）：{s.get('status')}")
        if "日本" in vo or "借鉴" in vo:
            flags.append("口播里有「借鉴」的说法，出去前决定留还是处理")
        if flags:
            shot["flag"] = "；".join(flags)
        if my_scenes:
            for j, s in enumerate(my_scenes, 1):
                key = f"{sid.lower()}-scene{j}"
                prompt_map[key] = {
                    "name": f"{sid} 代码画面",
                    "text": (f"生成一段 {float(s.get('dur') or 3):.1f} 秒、{W}×{H} 的画面：{s.get('what')}。"
                             f"用在本镜「{s.get('for')}」。风格：{kw_color} 为主色，深底，无实拍素材。"
                             + (f"\n已经有场景函数：python3 scripts/gen_scene.py --scene {s['scene']} "
                                f"--dur {float(s.get('dur') or 3):.1f} --fps {FPS} --out <输出>；"
                                f"样片在 {s.get('sample')}。改它，不要另起一套。"
                                if s.get("scene") else
                                "\n交付：scripts/gen_scene.py 的一个 scene 函数 + 一次样片；"
                                "先用 --probe 量一遍（节拍写死秒数会在短时长版本里露馅）。")),
                    "tool": "本仓 gen_scene.py",
                }
                shot.setdefault("rec", {})
                shot["rec"].setdefault("p", key)
        shots.append(shot)

    # ---- 顶层的其他页 --------------------------------------------------------
    speech_s = sum(r["dur"] for r in video_rows)
    chars = sum(len(s["vo"]) for s in shots)
    cps = round(chars / speech_s, 1) if speech_s else 4.5
    audience = brief.get("audience") or (thesis.get("sources") or {}).get("audience") or "（未填）"
    change = brief.get("change") or (thesis.get("sources") or {}).get("change") or "（未填）"
    claim = thesis.get("claim") or brief.get("change") or proj.name

    style_ent = {
        "id": "qizao-dark-gold",
        "cn": "柒造暗金",
        "en": "Qizao Dark Gold",
        "zh": (f"深底、{kw_color} 大字、思源黑体；大字顶边 {top_h:.2f}H、锚词晚 100ms、"
               f"字幕单行 {int(sub.get('max_chars_per_line') or 14)} 字、硬切入场。"),
        "pal": [kw_color, "#14110F", "#EFE9DC", "#FFFFFF", str(emp.get("negation_color") or "#C1121F")],
        "skill": "cutskills",
        "url": "",
    }

    board = {
        "meta": {
            "key": f"{proj.name}-{date.today().strftime('%Y%m%d')}",
            "no": "制作看板 No.01",
            "project": proj.name,
            "title": title or f"{proj.name} · 制作看板",
            "latin": "cut by decisions, not by feel",
            "ar": "9:16" if H > W else ("16:9" if W / H > 1.7 else "4:3"),
            "res": f"{W}×{H}",
            "fps": FPS,
            "date": date.today().isoformat(),
            "version": 1,
            "cps": cps,
            "presenter": "口播",
            "lede": (f"这份看板是从真时间轴导出的：{len(shots)} 镜 / {total:.2f} 秒，"
                     f"每镜的时长都是成片的真秒数（不是按字数估的），所以按空格预演＝成片的节奏。"
                     f"给 {audience} 看，要他现在觉得「{change}」。"
                     f"拖完改完点「复制修改给 AI」，改动会落回 timeline.json，方案锁定后才开渲。"),
            "footer": f"{proj.name} · 制作看板",
        },
        "style": {"name": "柒造暗金", "bg": "#14110F", "paper": "#EFE9DC", "ink": "#0E0E0E",
                  "bar": "#0B0A08", "accent": kw_color, "ui": "#FFFFFF",
                  "line": "#E8E1D2", "fg": "#E8E1D2", "noise": True},
        "chapters": chapters,
        "modes": {k: v for k, v in MODE_DEF.items() if k in {s["mode"] for s in shots}},
        "transitions": [["切点", "下一镜", "硬切，不加转场；大字和音效负责接缝", "无"]],
        "prompts": prompt_map,
        "styles": [style_ent],
        "canvas": [{"style": style_ent["id"],
                    "shots": [s["id"] for s in shots if s.get("ref")],
                    "note": f"大字 {kw_color}、顶边 {top_h:.2f}H、锚词起点 +100ms、"
                            f"字幕单行 ≤{int(sub.get('max_chars_per_line') or 14)} 字",
                    "say": "全片统一，别在片子中间换风格"}],
        "shots": shots,
        "preps": [
            ["素材", f"（{lib.name}）里的素材都已入库，看板里标「素材」的镜头直接到位"],
            ["录屏", "本片没有录屏"],
            ["核实", "带第三方字样的素材、口播里的「借鉴」说法，出片前定"],
        ],
        "rewrites": [],
        "specs": {
            "rec": [["通用", [["画幅", f"{W}×{H} {FPS}fps"], ["不需要录屏", "所有画面来自库内素材或代码"]]]],
            "brIntro": "代码画面由 gen_scene.py 生成，不依赖任何外部素材。",
            "br": [["代码画面", [["尺寸", f"{W}×{H} @ {FPS}fps"],
                               ["主色", kw_color],
                               ["字体", str(emp.get("font") or "NotoSansSC-Black")],
                               ["交付", "gen_scene.py 的 scene 函数 + 样片 mp4"]]],
                   ["声音", [["响度", f"{-14} LUFS ±1"], ["真峰", "-1 dBTP"],
                            ["音效库", "cc0-sfx-library（名字见每镜的 sfx）"]]]],
        },
        "changes": [{"v": 1, "date": date.today().isoformat(),
                     "items": [f"初版：从 timeline.json v4 导出（{len(shots)} 镜 / {total:.2f}s）",
                               f"每镜 fixed 都是成片真秒数，gap 由绝对秒反算",
                               f"代码画面 {len(scenes)} 处（接进时间轴的 0 处）"]}],
    }
    return board, {"proj": proj, "total": total, "shots": shots, "scenes": scenes,
                   "cards": cards, "sub": sub, "pack": pack_name}


def make_audio(proj: Path, out: Path) -> Path | None:
    """把成片的声音单独留一份给看板用。

    这条是「先声夺人」在看板上的落地：看板默认没有人声，按空格只能看到画面切换，
    那就等于没有预演。所以从成片里抽一条声轨，一次拖进 A1 就能听到真声音。
    """
    film = proj / "out" / "film-hf.mp4"
    dst = out / "vo.m4a"
    if not film.exists():
        return None
    if dst.exists() and dst.stat().st_mtime >= film.stat().st_mtime:
        return dst
    ff = Path(os.environ.get("CUTSKILLS_FFMPEG", "")) if os.environ.get("CUTSKILLS_FFMPEG") else None
    if not ff or not ff.exists():
        import shutil as _sh
        p = _sh.which("ffmpeg")
        ff = Path(p) if p else None
    if not ff:
        return None
    r = subprocess.run([str(ff), "-y", "-v", "error", "-i", str(film), "-vn",
                        "-c:a", "aac", "-b:a", "160k", str(dst)], capture_output=True, text=True)
    return dst if r.returncode == 0 and dst.exists() else None


README = """# 怎么看这份看板

1. 双击 `board.html` 用浏览器打开。这是从 `timeline.json` 导出的：每镜时长都是成片的**真秒数**。
2. **先拖声音**：把同目录的 `vo.m4a` 拖到时间线 **A1 口播 VO** 轨的最左边。
   这条就是成片原声（人声 + 音效 + 垫乐）。不拖它，按空格只能看画面切换，听不到节奏。
3. 按**空格**预演。1× / 2× / 4× 在时间线工具栏。
4. 不满意的地方直接动：拖 clip 中间移动、拖两端改时长、直接在预览画布上拖元素。
   动完点右上「**复制修改给 AI**」，把那段文本粘回来。
5. 有已经剪好的成片时，点预览下方「◧ 对比成片」导入 `../out/film-hf.mp4`，
   用滑杆 / 橡皮擦把预演和成片擦开对比。
6. 「**导出 XML**」出 FCP 7 XML，Premiere / 达芬奇 / Final Cut 都能导入。
   导入后拿 `check-report.md` 末尾那张 clip 表逐条对：起点、时长、轨道、镜号。

## 这份看板不会做的事

- 它不是真相。真值是 `timeline.json`，改动要经过 `board_patch.py` 落回去才算数。
- 浏览器里拖改动存在浏览器本地，换设备/换浏览器不同步；换浏览器把 `vo.m4a` 再拖一次就行。
- 它不做渲染。渲不渲由 `lock.json` 拍板。
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--out", default="board", help="输出目录（相对项目，默认 board）")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-build", action="store_true", help="只出 board.js，不生成 html")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = resolve_lib(proj, a.library)
    board, meta = build_board(proj, lib, title=a.title)

    out = proj / a.out
    out.mkdir(parents=True, exist_ok=True)
    js = "const BOARD = " + json.dumps(board, ensure_ascii=False, indent=1) + ";\n"
    (out / "board.js").write_text(js)
    print(f"board.js  {len(js) // 1024} KB · {len(board['shots'])} 镜 · "
          f"{meta['total']:.2f}s · 章节 {len(board['chapters'])}")
    vo = make_audio(proj, out)
    print(f"声轨      {vo.name if vo else '（没有 out/film-hf.mp4，跳过）'}")
    (out / "README.md").write_text(README)

    if a.no_build:
        return 0
    if not BUILD.exists():
        raise SystemExit(f"看板引擎不在：{BUILD}")
    html = out / "board.html"
    r = subprocess.run([sys.executable, str(BUILD), str(out / "board.js"), str(html),
                        board["meta"]["title"]], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr, file=sys.stderr)
        return r.returncode
    print(r.stdout.strip())
    print(f"打开：file://{html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
