#!/usr/bin/env python3
"""CutSkills · 盲测：把成片稿交给一个不知道内情的读者，看他读出来的是什么。

这是 B 段唯一一个**外部裁判**。机器能查「有没有证据」，查不了「这段话到底说清了没有」——
判断那件事的永远是人（或另一个没被喂过判断的模型）。所以流程是：

  1. `blind_test.py --project P`        → 生成 `blind-test.md`：只有按顺序的口播文字，
                                            没有素材名、没有保留理由、没有角色标注。
  2. 把这份文件交给另一个人 / 另一个模型（**不要给它 intent.json，不要给它 why**），
     让它按三个问题回答：主张是什么 / 它拿什么当证据 / 哪里不信或没看懂。
  3. `blind_test.py --project P --score 回答.md` → 与 `intent.promise` 对账，写 `blind-report.md`。

为什么值得这么麻烦：作者读自己的稿子，读的是「我想说的」；陌生人读的是「实际说出来的」。
两者差多少，只有盲测测得出。三分钟盲测比三十分钟自评准。

机器只报数、不给「通过 / 不通过」——两句话是不是同一件事，只有并排看一眼才算数。

用法：
  blind_test.py --project <项目目录>
  blind_test.py --project <项目目录> --score 裁判1.md [--score 裁判2.md]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

QUESTION = """请你只根据下面这些文字回答三个问题（不要猜作者意图，不要评价文笔）：

1. **主张**：这条片子到底想让我相信什么？用一句话说。
2. **证据**：它拿了哪些具体的东西来支撑这句话？逐条列出来（没有就写「没有」）。
3. **不信**：哪几处你不信、或者没听懂？可以指序号（例如 #5）。没有就写「都信」。
"""


def bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fa5")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def render(proj: Path) -> Path:
    edl = json.loads((proj / "edl.json").read_text())
    segs = edl.get("segments") or []
    total = sum(s["out"] - s["in"] for s in segs)

    lines = ["# 盲测包", "",
             "> 给读者的话：这是一条还没发布的短视频的全部说话内容。",
             "> 别找作者，别问意图，只按下面的问题回答。答完再往下看。", "",
             QUESTION, "", "---", "", "## 文字（按成片顺序）", ""]
    for s in segs:
        text = (s.get("evidence") or "").strip()
        lines.append(f"{s['n']}. {text or '（这一段没有说话，只有画面和声音）'}")
    lines += ["", "---", "",
              f"（生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}；成片估算 {total:.1f}s / {len(segs)} 段。"
              "这两行是给流程看的，读者可以忽略。）", ""]
    out = proj / "blind-test.md"
    out.write_text("\n".join(lines))
    return out


def score(proj: Path, answers: list[str]) -> int:
    intent = json.loads((proj / "intent.json").read_text())
    promise = intent.get("promise") or ""
    want = bigrams(promise)

    lines = [f"# 盲测报告 · {proj.name}", "",
             f"**我承诺要说清的**：{promise or '（intent 里没写 promise）'}", ""]
    flags: list[str] = []
    for f in answers:
        ans = Path(f).read_text()
        m = re.search(r"主张[：:]\s*(.+)", ans)
        claim = (m.group(1).strip() if m else "").strip(" 。")
        cov = (len(want & bigrams(claim)) / len(want)) if want and claim else 0.0
        ev = re.search(r"证据[：:](.+?)(?:\n\s*3[\.、]|不信|$)", ans, re.S)
        n_ev = len(re.findall(r"(?:^|\n)\s*[-*•\d]", ev.group(1) if ev else ""))
        doubt = re.search(r"不信[：:](.+)", ans, re.S)
        doubt_text = (doubt.group(1).strip() if doubt else "")
        no_doubt = doubt_text.startswith(("都信", "没有", "无 "))
        segs_hit = re.findall(r"#(\d+)", doubt_text)

        lines += [f"## 裁判 · {Path(f).name}", "",
                  f"- 读出来的主张：{claim or '（没答）'}",
                  f"- 与承诺的字面重合：{cov * 100:.0f}%　<sub>低不等于跑题——改写一遍就低，两句并排看一眼再判</sub>",
                  f"- 数到的证据：{n_ev} 条",
                  f"- 不信/没懂：{'无' if no_doubt else (doubt_text[:160] or '（没答）')}"]
        if segs_hit:
            lines.append(f"- 被点到的段：{'、'.join('#' + x for x in segs_hit)}"
                         " —— 去 `gap.md` 看是不是同一处缺口")
        lines.append("")
        if n_ev == 0:
            flags.append(f"{Path(f).name}：证据 0 条 —— 读者读完说不出你凭什么")
        if segs_hit:
            flags.append(f"{Path(f).name}：点名 {'、'.join('#' + x for x in segs_hit)} 不可信")

    # ---- 多裁判：被两个人指到同一段的，才是高信号；只一个人说的可能是个人偏好
    from collections import Counter
    cnt: Counter = Counter()
    for f in answers:
        ans = Path(f).read_text()
        doubt = re.search(r"不信[：:](.+)", ans, re.S)
        for x in re.findall(r"#(\d+)", doubt.group(1) if doubt else ""):
            cnt[int(x)] += 1
    if len(answers) > 1 and cnt:
        multi = [n for n, c in cnt.items() if c >= 2]
        lines += ["## 共同指认（≥2 位裁判点到同一段）", ""]
        if multi:
            lines += [f"- #{n}（{cnt[n]} 位）" for n in sorted(multi)]
            lines += ["", "同一段被两个人独立指出，这条基本可以当成真的——去 `gap.md` 看它属于哪类缺口。", ""]
        else:
            lines += ["没有。所有人说的都不一样：要么是个人偏好，要么是每个人卡在不同的地方——"
                      "后者说明这条片子的信息密度不够均匀，每段都缺一点。", ""]

    lines += ["## 机器报数，判读归你", "",
              "盲测的意义就在这一步：**不许让写稿的那一方给自己打分**。所以这里不给「通过 / 不通过」。", "",
              "**三个判据**：", "",
              "1. 裁判的「主张」和你的「承诺」是不是同一件事（字面重合只是提示，改写会掉到 30% 以下）。",
              "2. 证据 ≥2 条：读得出来你凭什么。0–1 条就是只有主张（`gap.md` 会列）。",
              "3. 有人答「不信」并指了段号：那几段就是该补说理（`warrant`）或换证据的地方。", ""]
    if flags:
        lines += ["**这次的硬信号**："] + [f"- {x}" for x in flags] + [""]
    lines += ["判完记一行：`log_decision.py --said \"盲测：读者读出来的是…\" --did \"…\"`。", ""]

    out = proj / "blind-report.md"
    out.write_text("\n".join(lines))
    print(f"盲测报告 → {out}（{len(answers)} 位裁判）")
    for x in flags:
        print(f"  · {x}")
    print("机器只报数：把裁判的「主张」和你的「承诺」并排看一眼再判。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--score", action="append", default=[], help="裁判的回答文件，可多次")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    if not a.score:
        out = render(proj)
        print(f"盲测包 → {out}")
        print("交给另一个人或另一个模型（只给它这份文件），拿回答跑 --score。")
        return 0
    return score(proj, a.score)


if __name__ == "__main__":
    raise SystemExit(main())
