#!/usr/bin/env python3
"""CutSkills · 入口第一问：把「对话即需求」压成 brief.json 的四格。

旧入口 `intake.py` 只判素材够不够（full / thin / empty）——它回答「能不能开工」，
不回答「给谁做、做成什么样」。四格空着，后面全是装饰：

  audience               给谁看（一句话，具体到一类人）
  audience_present_state 他现在信什么 / 卡在哪一步（3 秒钩子的唯一依据）
  change                 要他怎么变（看完多信一件事，或多做一个动作）
  length                 多长（30 秒系列 / 60–90 秒口播 / 自定）
  style                  什么风格（styles/*.yaml 里的哪一支）

`brief.json` 是唯一真值源，`decisions.md` 只是它的视图（追加，不改旧行）。

缺格最多三问，问完带假设走：
  四格缺 2 格以内 —— 自己补假设，逐条写进 `assumed[]`，别拿「不确定」当借口不往下走；
  四格缺 3 格以上 —— 必须问，但一轮最多问三条（一次问齐，不要一条一条挤）。
非交互（`--set`）问不了人：audience 或 change 缺，或缺 3 格以上，直接拒写（退出码 4），
把该问的三条打出来让人去补。

用法：
  brief.py --project <项目目录>                       # 交互式问四格
  brief.py --project <项目目录> --set audience=... --set change=... \\
           --set audience_present_state=... --set length=60 --set style=talking-head-v1
  brief.py --project <项目目录> --show                # 打印当前 brief
  brief.py --project <项目目录> --reset               # 清空重来（旧文件备份为 brief.json.bak）

退出码：0 写成 / 2 用法错 / 3 套话或风格不存在 / 4 缺格 ≥3（或核心格缺）且非交互问不了
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STYLES = ROOT / "styles"

sys.path.insert(0, str(HERE))
from log_decision import append_row                       # noqa: E402  写 decisions.md 只有这一处实现

GRID = ("audience", "change", "length", "style")
STATE_OF = {"audience": "audience", "change": "change", "length": "length_s", "style": "style_pack"}
HARD = ("audience", "change")          # 这两格假设不出来：假设一个观众，等于假设整条片子的方向
SET_ALIASES = {"present_state": "audience_present_state",
               "length_s": "length", "style_pack": "style"}
SET_KEYS = ("audience", "audience_present_state", "change", "length", "style")

FIELD_CN = {"audience": "给谁看", "audience_present_state": "他现在信什么",
            "change": "要他怎么变", "length": "多长", "style": "什么风格"}

# 套话表：参照 check_edl.py 的 VAGUE_EVIDENCE——命中的不是「错」，是「等于没写」。
# 观众/变化写这类词，后面选段没有任何依据，只能靠猜。
VAGUE_AUDIENCE = ("所有人", "大众", "大家", "任何人", "全网", "泛人群", "普通人", "所有观众")
VAGUE_CHANGE = ("做得更好", "变得更好", "做得更专业", "更专业", "提升认知", "涨知识",
                "学到东西", "有收获", "更了解", "加深印象", "留下印象", "记住我们")

# 三档片长：给的是「选择」不是「填空」。150 个字的填空题没人认真填，三个选项才会被认真选。
LENGTH_TIERS = (
    (30, "30 秒系列（每天一条）", "题材多、单条只讲一件事——靠条数堆覆盖，别指望一条讲透"),
    (75, "60–90 秒口播（一条讲透）", "现有验收基准都在这档（画幅/字幕/响度那 19 项按这个长度调的）"),
    (None, "自定", "给一个秒数，5–600 之间"),
)

QUESTIONS = ("audience", "audience_present_state", "change", "length", "style")
QUESTION_TEXT = {
    "audience": "给谁看？一句话，具体到一类人",
    "audience_present_state": "他现在信什么？一句他此刻相信的话（3 秒钩子的依据）",
    "change": "要他怎么变？看完多信一件事、或多做一个动作",
    "length": "多长？30 秒系列 / 60–90 秒口播 / 自定",
    "style": "什么风格？styles/*.yaml 里挑一支",
}


# ------------------------------------------------------------------ 风格包

def canvas_label(raw: str) -> str:
    """从 yaml 原文里读 canvas，写成人能读的一行。

    **不能 trust `yaml.safe_load` 的 aspect**：`9:16` 在 YAML 1.1 里是 60 进制整数（=556），
    PyYAML 会把它当数字，于是 `9:16` 变成 556、`16:9` 变成 969——列出来就是一手假数据。
    组合套（set-*.yaml）没有 canvas 段。
    """
    m = re.search(r"^canvas:[ \t]*\n((?:[ \t]+[^\n]*\n)+)", raw, re.M)
    if not m:
        return "组合套（无 canvas）"
    blk = m.group(1)
    w = re.search(r"^\s+width:\s*(\d+)", blk, re.M)
    h = re.search(r"^\s+height:\s*(\d+)", blk, re.M)
    fps_one = re.search(r"^\s+fps:\s*(\d+)\s*(?:#.*)?$", blk, re.M)
    if w and h:
        return f"{w.group(1)}×{h.group(1)}@{fps_one.group(1) if fps_one else '?'}"
    aspect = re.search(r"^\s+aspect:\s*\[([^\]]*)\]", blk, re.M)
    fps = re.search(r"^\s+fps:\s*\[([^\]]*)\]", blk, re.M)
    if aspect:
        a = "|".join(x.strip() for x in aspect.group(1).split(",") if x.strip())
        f = "/".join(x.strip() for x in fps.group(1).split(",") if x.strip()) if fps else ""
        return f"{a} @{f}" if f else a
    return f"@{fps_one.group(1)}" if fps_one else "无 canvas"


def load_styles() -> list[dict]:
    out = []
    for p in sorted(STYLES.glob("*.yaml")) if STYLES.exists() else []:
        raw = p.read_text()
        try:
            meta = yaml.safe_load(raw) or {}
        except yaml.YAMLError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        out.append({
            "name": str(meta.get("name") or p.stem),
            "label": str(meta.get("label") or ""),
            "canvas": canvas_label(raw),
            "path": p,
        })
    return out


def find_style(styles: list[dict], needle: str) -> dict | None:
    n = needle.strip()
    for s in styles:
        if n == s["name"] or n == s["path"].stem:
            return s
    return None


def style_menu(styles: list[dict]) -> str:
    return "\n".join(f"  {i}) {s['name']} · {s['label']} · {s['canvas']}"
                     for i, s in enumerate(styles, 1))


# ------------------------------------------------------------------ 片长

def band_of(seconds: int) -> str:
    if seconds < 20:
        return "short"
    if seconds <= 45:
        return "30"
    if seconds <= 100:
        return "60-90"
    return "100+"


def parse_length(raw: str) -> int:
    s = raw.strip().replace(" ", "")
    if s in ("60-90", "60–90", "60~90"):
        return 75
    s = re.sub(r"(秒|s|S)$", "", s)
    if not re.fullmatch(r"\d+", s):
        raise ValueError(f"片长要一个秒数（30 / 60 / 90 / 75…）或 60-90 档，收到「{raw}」")
    v = int(s)
    if not 5 <= v <= 600:
        raise ValueError(f"片长 {v}s 不在 5–600 之间：太短讲不完一件事，太长不是这一档的片子")
    return v


def length_reason(seconds: int) -> str:
    band = band_of(seconds)
    for sec, label, hint in LENGTH_TIERS:
        if sec == seconds:
            return f"{label}：{hint}"
    if band == "30":
        return "落在 30 秒档：题材多、单条只讲一件事"
    if band == "60-90":
        return "落在 60–90 秒档：一条讲透，现有验收基准都在这档"
    return f"自定 {seconds}s（{band} 档）"


# ------------------------------------------------------------------ 读输入

def read_answer() -> str | None:
    """读一行。EOF（管道喂完了 / Ctrl-D）返回 None——不能崩，也不能死循环问下去。"""
    try:
        return input("> ").strip()
    except EOFError:
        return None


def ask_text(key: str, current: str, vague: tuple[str, ...]) -> str:
    title = {
        "audience": "① 给谁看？",
        "audience_present_state": "② 他现在信什么？",
        "change": "③ 要他怎么变？",
    }[key]
    lines = {
        "audience": ("一句话，具体到一类人（例：自己找师傅做艺术漆的业主）。写「所有人」「大众」会被打回。",),
        "audience_present_state": ("一句他此刻相信的话 / 卡在哪一步（例：只看总价，不知道基层算不算在内）。",
                                   "这是 3 秒钩子的唯一依据——不知道就空着，别编。"),
        "change": ("看完之后他多信一件事、或多做一个动作（例：问清楚基层和煅烧这两件事再比价）。",
                   "写「做得更好」「更专业」会被打回。"),
    }[key]
    while True:
        print(f"\n{title}")
        for ln in lines:
            print(f"   {ln}")
        if current:
            print(f"   回车保留：{current}")
        ans = read_answer()
        if ans is None:
            print("   （输入结束，这一格先空着）")
            return current
        if not ans:
            return current
        hits = [w for w in vague if w in ans]
        if hits:
            print(f"   ✗ 「{hits[0]}」等于没写{'' if key != 'audience' else '观众'}。换一句具体的，重答。")
            continue
        if len(ans) < 4:
            print("   · 有点短，先记下（选段前自己再看一眼）")
        return ans


def ask_length(current_s: int | None) -> tuple[int, str]:
    while True:
        print("\n④ 多长？")
        for i, (sec, label, hint) in enumerate(LENGTH_TIERS, 1):
            print(f"   {i}) {label} —— {hint}")
        if current_s:
            print(f"   回车保留：{current_s}s（{band_of(current_s)}）")
        ans = read_answer()
        if ans is None:
            if current_s:
                return current_s, length_reason(current_s)
            return 75, "没给片长，按 60–90 秒档补（验收基准在这档）"
        if not ans:
            if current_s:
                return current_s, length_reason(current_s)
            print("   ✗ 得挑一档，或直接给秒数")
            continue
        if ans in ("1", "2"):
            sec, label, hint = LENGTH_TIERS[int(ans) - 1]
            print(f"   → {label}：{hint}")
            return sec, f"{label}：{hint}"
        if ans == "3":
            while True:
                print("   多少秒？（给数字，例如 45）")
                raw = read_answer()
                if raw is None:
                    return 75, "没给片长，按 60–90 秒档补（验收基准在这档）"
                if not raw:
                    continue
                try:
                    sec = parse_length(raw)
                except ValueError as e:
                    print(f"   ✗ {e}")
                    continue
                return sec, f"自定 {sec}s（{band_of(sec)} 档）"
        try:
            sec = parse_length(ans)
        except ValueError as e:
            print(f"   ✗ {e}（挑 1/2/3，或直接给秒数）")
            continue
        return sec, f"自定 {sec}s（{band_of(sec)} 档）"


def ask_style(styles: list[dict], current: str) -> str:
    while True:
        print(f"\n⑤ 什么风格？（styles/*.yaml 里现有 {len(styles)} 支）")
        print(style_menu(styles))
        if current:
            print(f"   回车保留：{current}")
        ans = read_answer()
        if ans is None:
            return current
        if not ans:
            if current:
                return current
            print("   ✗ 挑一个编号")
            continue
        if ans.isdigit():
            n = int(ans)
            if 1 <= n <= len(styles):
                name = styles[n - 1]["name"]
                print(f"   → {name} · {styles[n - 1]['label']}")
                return name
            print(f"   ✗ 没有编号 {n}，只有 1–{len(styles)}")
            continue
        hit = find_style(styles, ans)
        if hit:
            return hit["name"]
        print(f"   ✗ 没有风格包「{ans}」，可选项见上")


# ------------------------------------------------------------------ brief.json

def load_brief(proj: Path) -> dict:
    f = proj / "brief.json"
    if not f.exists():
        return {}
    try:
        doc = json.loads(f.read_text())
    except json.JSONDecodeError:
        print(f"{f} 不是合法 JSON，当作没有处理（旧文件没动）", file=sys.stderr)
        return {}
    return doc if isinstance(doc, dict) else {}


def state_from(doc: dict) -> dict:
    ls = doc.get("length_s")
    return {
        "audience": doc.get("audience") or "",
        "audience_present_state": doc.get("audience_present_state") or "",
        "change": doc.get("change") or "",
        "length_s": int(ls) if isinstance(ls, (int, float)) and ls > 0 else None,
        "style_pack": doc.get("style_pack") or "",
    }


def top_up(b: dict, styles: list[dict]) -> tuple[list[str], list[str]]:
    """缺的格：能假设的自己补（假设逐条记下），补不出的留空进 missing。"""
    assumed, missing = [], []
    if b["length_s"] is None:
        b["length_s"] = 75
        assumed.append("片长没给：按 60–90 秒档（75s）走，因为现有验收基准都在这档；"
                       "要短的自己 --set length=30")
    if not b["style_pack"]:
        pick = find_style(styles, "talking-head-v1") or (styles[0] if styles else None)
        if pick is None:
            missing.append("style")
            assumed.append("风格没给，styles/*.yaml 里也一支都没有——先补一个风格包")
        else:
            b["style_pack"] = pick["name"]
            assumed.append(f"风格没给：用 {pick['name']}（单机位口播，"
                           f"现有唯一打通过验收的风格包）")
    for key in ("audience", "audience_present_state", "change"):
        if b[key]:
            continue
        missing.append(key)
        assumed.append({
            "audience": "audience 空着：观众不明，四格另外三格再准也没用——这一格必须问出来",
            "audience_present_state": "audience_present_state 空着：3 秒钩子暂时没有依据"
                                      "（不许拿自我介绍开场顶），选段前补一句他现在信什么",
            "change": "change 空着：要他怎么变没定，选段只能靠猜——这一格必须问出来",
        }[key])
    return assumed, missing


def carry_assumptions(assumed: list[str], prior: list[str], provided: set[str]) -> list[str]:
    """上一次补的假设，如果这次没人重新指定，它还是假设——别让 brief.json 看起来像「都定了」。

    片长/风格这种能被默认值填满的格，最容易被重跑一次就洗白：值没变，来源没变，就得继续记。
    """
    if "length" not in provided and not any(x.startswith("片长") for x in assumed):
        assumed += [x for x in prior if x.startswith("片长")]
    if "style" not in provided and not any(x.startswith("风格") for x in assumed):
        assumed += [x for x in prior if x.startswith("风格")]
    return assumed


def write_brief(proj: Path, b: dict, assumed: list[str], missing: list[str], created: str) -> dict:
    doc = {
        "version": 1,
        "created": created,
        "audience": b["audience"],
        "change": b["change"],
        "audience_present_state": b["audience_present_state"],
        "length_s": b["length_s"],
        "length_band": band_of(b["length_s"]) if b["length_s"] else "",
        "style_pack": b["style_pack"],
        "assumed": assumed,
        "missing": missing,
    }
    (proj / "brief.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return doc


def summary(doc: dict, proj: Path) -> None:
    print(f"\nbrief.json → {proj / 'brief.json'}")
    print(f"  ① 给谁看　　　　：{doc['audience'] or '（空）'}")
    print(f"  ② 他现在信什么　：{doc['audience_present_state'] or '（空）'}")
    print(f"  ③ 要他怎么变　　：{doc['change'] or '（空）'}")
    length = f"{doc['length_s']}s（{doc['length_band']}）" if doc["length_s"] else "（空）"
    print(f"  ④ 多长　　　　　：{length}")
    print(f"  ⑤ 什么风格　　　：{doc['style_pack'] or '（空）'}")
    if doc["assumed"]:
        print(f"  · 假设 {len(doc['assumed'])} 条（往下了，但记在这儿）")
        for a in doc["assumed"]:
            print(f"      - {a}")
    if doc["missing"]:
        print(f"  · 还缺 {len(doc['missing'])} 格：{'、'.join(FIELD_CN.get(k, k) for k in doc['missing'])}")
    print("\n下一步：选段前先读 brief.json——片长决定骨架，风格决定模板，"
          "「他现在信什么」决定 3 秒钩子。")


def missing_question_block(missing: list[str]) -> str:
    """缺 3 格以上时该问的那一轮：一次问齐，**最多三条**——问成五轮，人就跑了。"""
    picked = [k for k in QUESTIONS if k in missing] or list(QUESTIONS)
    out = ["一轮最多问这三条，一次问齐，别一条一条挤："]
    for i, key in enumerate(picked[:3], 1):
        out.append(f"  {i}. {QUESTION_TEXT[key]}")
    return "\n".join(out)


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目目录（<项目>/brief.json 落在它下面）")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    metavar="K=V", help="非交互写入：audience / audience_present_state / change / length / style")
    ap.add_argument("--show", action="store_true", help="打印当前 brief")
    ap.add_argument("--reset", action="store_true", help="清空重来（旧文件备份为 brief.json.bak）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    proj.mkdir(parents=True, exist_ok=True)
    styles = load_styles()

    if a.reset:
        f = proj / "brief.json"
        if f.exists():
            shutil.move(str(f), str(f.with_suffix(".json.bak")))
            print(f"已把旧 brief 备份到 {f.with_suffix('.json.bak')}")

    doc = load_brief(proj)

    if a.show:
        if not doc:
            print(f"{proj / 'brief.json'} 不存在——还什么都没定", file=sys.stderr)
            return 2
        b = state_from(doc)
        doc = {**doc, "length_band": band_of(b["length_s"]) if b["length_s"] else ""}
        summary(doc, proj)
        print()
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0

    created = doc.get("created") or datetime.now().isoformat(timespec="seconds")
    old = state_from(doc)
    b = state_from(doc)
    provided: set[str] = set()
    notes: list[str] = []

    # ---- 非交互：--set 写什么就是什么，缺的按规则补或拒写
    if a.sets:
        for raw in a.sets:
            if "=" not in raw:
                print(f"--set 要 k=v，收到「{raw}」", file=sys.stderr)
                return 2
            k, v = raw.split("=", 1)
            k = SET_ALIASES.get(k.strip(), k.strip())
            v = v.strip()
            if k not in SET_KEYS:
                print(f"不认识的格「{k}」。只有：{' / '.join(SET_KEYS)}"
                      "（present_state / length_s / style_pack 也认）", file=sys.stderr)
                return 2
            if not v:
                print(f"--set {k}= 是空的；要么别给，要么给个值", file=sys.stderr)
                return 2
            if k == "length":
                try:
                    b["length_s"] = parse_length(v)
                except ValueError as e:
                    print(e, file=sys.stderr)
                    return 2
                notes.append(f"片长 {b['length_s']}s 是人直接给的秒数（{band_of(b['length_s'])} 档）")
                provided.add("length")
                continue
            if k == "style":
                hit = find_style(styles, v)
                if hit is None:
                    print(f"没有风格包「{v}」。可选项：\n{style_menu(styles)}", file=sys.stderr)
                    return 3
                b["style_pack"] = hit["name"]
                provided.add("style")
                continue
            vague = VAGUE_AUDIENCE if k == "audience" else VAGUE_CHANGE if k == "change" else ()
            hits = [w for w in vague if w in v]
            if hits:
                print(f"{FIELD_CN[k]} 写的是「{hits[0]}」——等于没写，退出码 3。"
                      f"换成一句具体的：谁、什么处境、看完做什么。", file=sys.stderr)
                return 3
            if len(v) < 4:
                print(f"· {FIELD_CN[k]}「{v}」有点短，先记下", file=sys.stderr)
            b[k] = v
            provided.add(k)

        grid_missing = [f for f in GRID if not b[STATE_OF[f]]]
        hard_missing = [f for f in HARD if not b[f]]
        if hard_missing or len(grid_missing) >= 3:
            print(f"缺 {len(grid_missing)} 格（{'、'.join(FIELD_CN[f] for f in grid_missing)}）"
                  "——非交互模式问不了人，写完也是个空壳。\n"
                  + missing_question_block(grid_missing or hard_missing or list(GRID))
                  + "\n要么补 --set，要么去掉 --set 跑交互式。", file=sys.stderr)
            return 4
        snapshot = dict(b)
        assumed, missing = top_up(b, styles)
        assumed = carry_assumptions(assumed, doc.get("assumed") or [], provided)
    else:
        # ---- 交互：① 观众 → ② 他现在信什么 → ③ 要他怎么变 → ④ 片长 → ⑤ 风格
        print(f"B 段入口 · {proj.name} · 四格定方向（缺一格，后面全是装饰）")
        b["audience"] = ask_text("audience", b["audience"], VAGUE_AUDIENCE)
        b["audience_present_state"] = ask_text("audience_present_state",
                                               b["audience_present_state"], ())
        b["change"] = ask_text("change", b["change"], VAGUE_CHANGE)
        b["length_s"], reason = ask_length(b["length_s"])
        notes.append(f"片长 {b['length_s']}s（{band_of(b['length_s'])}）：{reason}")
        b["style_pack"] = ask_style(styles, b["style_pack"])
        for key in ("length", "style"):
            if b[STATE_OF[key]] != old[STATE_OF[key]]:
                provided.add(key)
        snapshot = dict(b)
        assumed, missing = top_up(b, styles)
        assumed = carry_assumptions(assumed, doc.get("assumed") or [], provided)

    doc = write_brief(proj, b, assumed, missing, created)

    # ---- 留痕：四格 + 假设 + 片长选择理由，追加进 decisions.md（唯一实现在 log_decision.py）
    said = "；".join(f"{FIELD_CN[k]}={snapshot[k] if not isinstance(snapshot[k], int) else f'{snapshot[k]}s'}"
                     for k in ("audience", "audience_present_state", "change") if snapshot[k])
    if not said:
        said = "（没答，按假设占位）"
    did = (f"写 brief.json：audience={b['audience'] or '（空）'} ／ "
           f"现状={b['audience_present_state'] or '（空）'} ／ change={b['change'] or '（空）'} ／ "
           f"片长={b['length_s']}s（{band_of(b['length_s'])}） ／ 风格={b['style_pack'] or '（空）'}")
    if notes:
        did += "；" + "；".join(notes)
    if assumed:
        did += f"；假设 {len(assumed)} 条：" + "；".join(assumed)
    if missing:
        did += "；仍缺：" + "、".join(FIELD_CN.get(k, k) for k in missing)
    append_row(proj, said, did, "accepted")

    summary(doc, proj)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
