#!/usr/bin/env python3
"""CutSkills · B 段入口检查：这次是「有需求的剪辑」，还是「一堆素材想变成片子」。

B 段开工前最容易犯的错，是拿着素材直接选段——用户其实还没说要什么。片子做出来才发现
方向不对，返工比重新问一句贵十倍。

这个脚本只做判断和供料，不做创作判断：

  full   —— 有用户原话 + 稿子或参考片：直接进 B（读稿 → 选段）。
  thin   —— 有原话（带目标），但没有稿、没有参考片：两条路让用户二选一——
             ① 他给稿/给参考（最省事，十分钟）；
             ② 授权我按库里的语料 + A 段索引出 2–3 个选题给他挑。
  empty  —— 连原话都没有或只有一句「帮我剪个视频」：必须先问。

`--derive` 把「选题工作台」写到项目里（`options.md` 的供料部分）：库里能当证据的段落清单、
高频主题词、六套骨架。选题卡由 agent 读它来写，脚本不替它编。
`--verify` 检查 agent 写好的 `options.md` 里点名的素材是否真在索引里——选题不许凭空许诺。

用法：
  intake.py --library "<库>" --project <项目> [--intent "用户原话"] [--script 稿.md] [--reference 参考.mp4]
  intake.py --library "<库>" --project <项目> --derive
  intake.py --library "<库>" --project <项目> --verify
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTENT_WORDS = ("剪", "出片", "成片", "做一条", "出一条", "做个视频", "做支", "短片", "口播")
TARGET_WORDS = (r"\d+\s*(秒|分钟|分)", r"9:16", r"16:9", r"4:5", "竖", "横", "抖音", "视频号",
                "小红书", "B站", "b站", "快手", "朋友圈", "YouTube")
AUDIENCE_WORDS = ("业主", "客户", "老板", "设计师", "工长", "同行", "新手", "宝妈", "员工")
TEMPLATES = [
    ("一 · 分歧拆解", "两种对立说法摆出来，再讲临界点和我们的判据"),
    ("二 · 现场带看", "从动作进，三个看点各配一个细节特写"),
    ("三 · 三步做法", "结论前置，三步可照做，附一个常见失败点"),
    ("四 · 反常识 + 举证", "先抛反直觉结论，给对手发言权，再上证据"),
    ("五 · 客户问答", "把最高频的质疑当开场，短答案 + 三个理由 + 边界"),
    ("六 · 对比测评", "条件对等，逐维度对比，最后给选择条件"),
]


def load_index(lib: Path) -> dict:
    p = lib / "_cutskills" / "index.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def has_target(text: str) -> bool:
    return any(re.search(w, text) for w in TARGET_WORDS)


def corpus_files(lib: Path) -> list[Path]:
    return sorted([p for p in lib.glob("*.md") if p.name != "素材索引.md"] +
                  [p for p in lib.glob("*.txt")])


def diagnose(intent: dict, lib: Path, index: dict) -> tuple[str, dict]:
    raw = (intent.get("raw") or "").strip()
    sig = {
        "原话够读": len(raw) >= 8,
        "原话里有剪辑动作": any(w in raw for w in INTENT_WORDS),
        "原话里带目标": bool(has_target(raw)),
        "原话里点名观众": any(w in raw for w in AUDIENCE_WORDS),
        "用户给了稿": bool(intent.get("script_path")),
        "用户给了参考片/方案": bool(intent.get("reference_cut")),
        "库里有背景语料": bool(corpus_files(lib)),
        "索引可用": bool(index.get("assets")),
    }
    if sig["用户给了稿"] or sig["用户给了参考片/方案"]:
        mode = "full"
    elif sig["原话里有剪辑动作"] and (sig["原话里带目标"] or sig["原话里点名观众"]):
        mode = "thin"
    else:
        mode = "empty"
    return mode, sig


def derive(lib: Path, proj: Path, index: dict) -> Path:
    """把「选题工作台」写进项目：能当证据的段落 + 高频主题 + 六套骨架。"""
    lines = ["# 选题工作台", "", "> 这里只有供料，没有判断。选题卡由 agent 读完这些再写，"
             "写的时候每个 `evidence` 都要指到下面某一行的具体时间码——指不到就换模板。", ""]

    assets = index.get("assets") or []
    speech = [a for a in assets if (a.get("transcript") or {}).get("phrases")]
    usable = []
    for a in speech:
        for s in a.get("segments") or []:
            dur_s = s.get("out", 0) - s.get("in", 0)
            # 只认看过画面的段（L2 才写 tag / summary）。L0 的段是整支文件的粗猜，
            # 动辄 700 秒，写进选题卡只会让「能指数」变成一个假数字。
            if (s.get("tag") or s.get("summary")) and s.get("grade") in ("must-use", "backup") \
                    and 2 <= dur_s <= 60:
                usable.append((s.get("grade"), a["id"], s.get("in"), s.get("out"),
                               s.get("tag") or "", s.get("summary") or ""))
    usable.sort(key=lambda x: ({"must-use": 0, "backup": 1}.get(x[0], 9), -(x[3] - x[2])))
    lines += [f"## 能当证据的段落（{len(usable)} 条，画面前 60 条）", "",
              "| 分级 | 素材 | 素材内 | tag | 说的是什么 |", "|---|---|---|---|---|"]
    for g, aid, i0, i1, tag, summ in usable[:60]:
        lines.append(f"| {g} | `{aid}` | {i0:.2f}–{i1:.2f} | {tag} | {summ} |")

    cnt: Counter = Counter()
    for a in speech:
        for ph in (a.get("transcript") or {}).get("phrases") or []:
            for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", ph.get("text") or ""):
                cnt[w] += 1
    lines += ["", "## 高频词（可能是选题主题，也可能是语气词）", "",
              "、".join(f"{w}({n})" for w, n in cnt.most_common(40)), ""]

    tags = Counter()
    for a in assets:
        for s in a.get("segments") or []:
            if s.get("tag"):
                tags[s["tag"]] += 1
    lines += ["## 画面里出现过的东西（L2 的 tag）", "",
              "、".join(f"{t}({n})" for t, n in tags.most_common(40)) or "（还没有 L2 记录）", ""]

    lines += ["## 六套骨架", "", "| 模板 | 一句话 |", "|---|---|"]
    lines += [f"| {n} | {d} |" for n, d in TEMPLATES]
    lines += ["", "详细骨架见 `references/plan-templates.md`。", ""]

    out = proj / "options.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def verify(lib: Path, proj: Path, index: dict) -> int:
    """选题卡里点名的素材，必须真在索引里。"""
    f = proj / "options.md"
    if not f.exists():
        print("没有 options.md，先跑 --derive", file=sys.stderr)
        return 2
    ids = {a["id"] for a in index.get("assets") or []}
    text = f.read_text()
    referenced = set(re.findall(r"`([^`]+\.(?:MOV|mov|mp4|MP4|HEIC|heic|jpg|JPG|png|PNG))`", text))
    missing = sorted(r for r in referenced if r not in ids)
    print(f"options.md 点名素材 {len(referenced)} 个，索引里没有的 {len(missing)} 个")
    for m in missing:
        print(f"  缺：{m}")
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--intent", default=None, help="用户原话；给了会写进 intent.json")
    ap.add_argument("--script", default=None, help="用户提供的口播/旁白稿")
    ap.add_argument("--reference", default=None, help="已有的成品或剪辑方案")
    ap.add_argument("--derive", action="store_true", help="生成选题工作台 options.md")
    ap.add_argument("--verify", action="store_true", help="检查 options.md 里点名的素材是否存在")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    proj = Path(a.project).expanduser().resolve()
    proj.mkdir(parents=True, exist_ok=True)
    index = load_index(lib)

    if a.verify:
        return verify(lib, proj, index)

    intent_f = proj / "intent.json"
    intent = json.loads(intent_f.read_text()) if intent_f.exists() else {}
    if a.intent:
        intent["raw"] = a.intent
    if a.script:
        intent["script_path"] = a.script
    if a.reference:
        intent["reference_cut"] = a.reference

    if a.derive:
        out = derive(lib, proj, index)
        print(f"选题工作台 → {out}")
        print("读它和 references/plan-templates.md，写 2–3 个选题卡（标题 / 骨架 / 能指数 / 时长），"
              "写完跑 --verify 确认点名的素材真在索引里。")
        return 0

    mode, sig = diagnose(intent, lib, index)
    intent["intake"] = {"mode": mode, "checked_at": datetime.now().isoformat(timespec="seconds"),
                        "signals": sig}
    intent_f.write_text(json.dumps(intent, ensure_ascii=False, indent=1))

    counts = index.get("counts") or {}
    print(f"B 段入口检查 · {proj.name} · {mode}")
    for k, v in sig.items():
        print(f"  {'✓' if v else '·'} {k}")
    if counts:
        print(f"  库：{counts.get('assets')} 个素材 / 有语音 {counts.get('with_speech')} / "
              f"L2 {counts.get('l2_records')} 条（覆盖 {counts.get('l2_coverage', 0) * 100:.1f}%）")

    if mode == "full":
        print("\n可以直接进 B：读 takes_packed.md → 选段。原料齐全，别自己编结构。")
        return 0

    print()
    if mode == "thin":
        print("有需求、没原料。两条路让用户挑，别自己替他决定：")
        print("  ① 「你手上有稿子（口播稿 / 旁白 / 大纲）或者一条想做成这样的片子吗？"
              "有的话给我，最省事。」")
        print("  ② 「没有也行——我按库里的语料和已经看过画面的那部分素材，出两三个选题给你挑，"
              "你选一个我再往下剪。」")
        print("\n（原话里能读出目标，但结构、论点、篇幅都还没定——现在选段只能靠猜。）")
    else:
        print("连需求都没成形。先问，最多两条：")
        print("  ① 「这条片子是给谁看、想让他看完做什么？」")
        print("  ② 「是已经有稿子，还是让我从素材里给你出几个方向？」")
    print("\n给了 `--derive` 我就把能当证据的段落、高频主题、六套骨架摆成选题工作台。")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
