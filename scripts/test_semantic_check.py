#!/usr/bin/env python3
"""T-017 合同测试：上屏前的语义常识校对闭环（准绳 A5）。

这个闭环要成立，四件事必须同时为真，缺一条就是假的：

  ① **检出**：欧阳三片（`dianzan-a/b/c`）四处真错字 + 一处口吃重尾，逐条对上位置；
  ② **清零**：把词表等长修正落上去，同一批字幕的残词/量级判据全归零
     （等长替换不动字宽，所以切得回原 cue——这正是「等长」的意义）；
  ③ **守卫**：不等长默认拒绝、标了 `unsafe` 也要调用方显式允许、吞吐否定字一律硬拦；
  ④ **防误杀**：「效果好好」「三十米高的中庭」「慢慢坐坐」「对对对」这些真话不许判红。

为什么要合同测试而不是「我看了，报了」：A5 是**语义**判断，最容易写成
「正则抄了一遍样本」的自证（T-004 就是这么被打回的）。这里所有断言都走产线函数——
`check_subs.semantic_judge()`、`lexicon.fix_text()`、`lint_transcript.scan_magnitudes()`，
测试自己一个判据都不写。

样本：`tests/fixtures/negative-samples/semantic-ouyang-2026-11-13.json`
（三片 `out/subs.json` 逐字复制，2026-11-13 实读）。跑法：

    python3 scripts/test_semantic_check.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_subs          # noqa: E402
import lexicon             # noqa: E402
import lint_transcript as LT  # noqa: E402

FIX = HERE.parent / "tests" / "fixtures" / "negative-samples" / "semantic-ouyang-2026-11-13.json"

# 三片里真实存在的东西。错字是 Jerry 2026-11-13 对着成片判的，不是测试自己挑的：
#   a#13「大约有十万米高」（应为十几米）
#   b#21「大咧先可以用入」（大咧→大家，标待人工）
#   c#8 「我哋嘅效果好先」（应为效果图）
#   c#16–17「生」+「万鱼池」（应为生猛鱼池，被宽度拆在两条 cue 里）
#   a#33「可以喺评论区讲下下」（口吃重尾）
REAL = [
    ("dianzan-a", 13, "十万米", "十几米"),
    ("dianzan-b", 21, "大咧", "大家"),
    ("dianzan-c", 8, "效果好", "效果图"),
    ("dianzan-c", 16, "生万", "生猛"),
]
STUTTER = [("dianzan-a", 33, "下下")]

# 词表修正（人做过的判断）：全部等长，可以直接落地
TERMS = {"十万米": "十几米", "效果好": "效果图", "生万": "生猛",
         "大咧": "大家", "四大轩王": "四大天王"}


def load_films() -> dict[str, list[dict]]:
    d = json.loads(FIX.read_text(encoding="utf-8"))
    return {k: v["cues"] for k, v in d["films"].items()}


def apply_equal_len(cues: list[dict], terms: dict[str, str]) -> list[dict]:
    """把等长替换落到 cue 文本上，并按原字数切回去。

    为什么要先拼再切：被宽度拆开的相邻 cue 是**同一句话**（「生」+「万鱼池」），
    逐条替换抓不到跨条的「生万」。等长替换字数不变，所以拼起来改完能一位不差地切回去。
    """
    lex = {"terms": dict(terms)}
    out = [dict(c) for c in cues]
    for run in check_subs.width_runs(cues):
        texts = [str(cues[j].get("text") or "") for j in run]
        joined = "".join(texts)
        fixed = lexicon.fix_text(joined, lex)
        if len(fixed) != len(joined):          # 等长被破坏＝这份工单不该自动落地
            raise AssertionError(f"等长替换改了字数：{joined} → {fixed}")
        pos = 0
        for j, t in zip(run, texts):
            out[j]["text"] = fixed[pos:pos + len(t)]
            pos += len(t)
    return out


def main() -> int:
    films = load_films()
    fails: list[str] = []

    def must(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""))
        if not ok:
            fails.append(name)

    # ---- ① 检出：五处真错字逐条对上位置
    print("① 检出（欧阳三片真实错字）")
    sem = {k: check_subs.semantic_judge(v) for k, v in films.items()}
    for slug, idx, hit, suggest in REAL:
        got = [h for h in sem[slug]["suspect"] if h["hit"] == hit]
        where = [(h["cue"], h.get("cue_to", h["cue"])) for h in got]
        ok = any(a <= idx <= b for a, b in where) and any(h["suggest"] == suggest for h in got)
        must(f"{slug} #{idx}「{hit}」→「{suggest}」", ok, f"实际 {where}")
    must("dianzan-a 量级荒谬：十万米高 = 100000 米",
         any(m["hit"].startswith("十万米") and m["tier"] == "absurd" for m in sem["dianzan-a"]["magnitude_bad"]),
         f"{[m['hit'] for m in sem['dianzan-a']['magnitude_bad']]}")
    for slug, idx, tail in STUTTER:
        ok = any(h["cue"] == idx and h["tail"] == tail for h in sem[slug]["stutter"])
        must(f"{slug} #{idx} 口吃重尾「{tail}」", ok, f"{sem[slug]['stutter']}")

    # ---- ② 清零：等长修正落上去，同一批字幕不再报
    print("② 清零（词表等长修正后）")
    fixed = {k: apply_equal_len(v, TERMS) for k, v in films.items()}
    sem2 = {k: check_subs.semantic_judge(v) for k, v in fixed.items()}
    for k in sorted(fixed):
        s = sem2[k]
        # 四处**错字**（残词/量级）必须清零；「讲下下」不算错字——它是删字，
        # 字数会变，按守卫只能走下面的显式不等长通道，所以这里单独排除它。
        left = [h for h in s["suspect"] if h["hit"] != "讲下下"]
        must(f"{k} 四处错字清零", not left, f"{[h['hit'] for h in left]}")
        must(f"{k} 荒谬量级清零", not s["magnitude_bad"], f"{[m['hit'] for m in s['magnitude_bad']]}")
    # 口吃重尾不是错字，是删字（字数变了）。机器建议口径仍严：要 unsafe + 显式允许；
    # 人把它写进库词表就是批准，直接落地（只在报告里 info 点名“不等长”）。
    t = [c["text"] for c in films["dianzan-a"] if c["text"].endswith("讲下下")][0]
    must("口吃重尾（机器建议口径）：不等长默认拒绝",
         not lexicon.plan("讲下下", "讲下", human=False)["apply"])
    must("口吃重尾（机器建议口径）：标 unsafe + 显式允许才放",
         lexicon.plan("讲下下", "讲下", {"unsafe": True},
                      human=False, allow_unsafe=True)["apply"])
    unsafe_lex = {"terms": {"讲下下": {"to": "讲下", "unsafe": True}}}
    must("口吃重尾：写进库词表即落地（人已批准）",
         lexicon.fix_text(t, unsafe_lex) == t.replace("讲下下", "讲下"))
    stut_fixed = [dict(c) for c in fixed["dianzan-a"]]
    for c in stut_fixed:
        c["text"] = lexicon.fix_text(str(c.get("text") or ""), unsafe_lex)
    s_after = check_subs.semantic_judge(stut_fixed)
    must("dianzan-a 残词全部清零（含口吃重尾）", not s_after["suspect"],
         f"{[h['hit'] for h in s_after['suspect']]}")
    must("dianzan-a 口吃重尾清零", not s_after["stutter"])

    # ---- ③ 守卫："谁写的"决定口径
    print("③ 守卫（人写的照旧应用 / 机器建议才要签名 / 否定字一律硬拦）")
    must("等长、不碰否定字 → 放行",
         lexicon.guard("十万米", "十几米")[0])
    # 库词表里的条目＝人写下的，写下去那一次就是显式批准：不等长也照旧应用
    human_lex = {"terms": {"物为不叫造为": "物，为什么不叫造漆"}}
    must("库词表不等长条目默认应用（人写的，不再要 unsafe）",
         lexicon.fix_text("物为不叫造为", human_lex) == "物，为什么不叫造漆")
    # 但增删否定字仍硬拦：人写的也不放过（会静默反转句意）
    human_neg = {"terms": {"唔好": "太好"}}
    must("库词表里的条目改否定字仍被硬拦",
         lexicon.fix_text("唔好嘅", human_neg) == "唔好嘅")
    must("库词表否定字条目：显式 --allow-negation-edit 才落地",
         lexicon.fix_text("唔好嘅", human_neg, allow_negation_edit=True) == "太好嘅")
    # 机器扫出来的建议（`suspect-words.txt` 这一路）→ 严格口径不变
    blocked, why = lexicon.guard("讲下下", "讲下")
    must("机器建议：不等长未标 unsafe → 拦", not blocked and "不等长" in why, why)
    blocked, why = lexicon.guard("讲下下", "讲下", {"unsafe": True})
    must("机器建议：已标 unsafe 但没显式允许 → 仍拦", not blocked and "allow-unsafe" in why, why)
    must("机器建议：已标 unsafe + 显式允许 → 放行",
         lexicon.guard("讲下下", "讲下", {"unsafe": True}, allow_unsafe=True)[0])
    blocked, why = lexicon.guard("唔好", "太好")
    must("等长但删掉否定字「唔」→ 硬拦", not blocked and "否定字" in why, why)
    blocked, why = lexicon.guard("唔系", "系", {"unsafe": True}, allow_unsafe=True)
    must("不等长 + 吞否定字：只开 allow-unsafe 也拦", not blocked and "否定字" in why, why)
    must("否定字编辑必须显式 --allow-negation-edit",
         lexicon.guard("唔系", "系", {"unsafe": True}, allow_unsafe=True,
                       allow_negation_edit=True)[0])
    # 整条链路（词表 → 字幕文本）也必须拦得住，不能只在 guard() 里拦
    neg_lex = {"terms": {"唔系": {"to": "系", "unsafe": True}}}
    must("词表链路：唔系→系 默认不落地", lexicon.fix_text("唔系纯手工", neg_lex) == "唔系纯手工")
    must("词表链路：只开 allow-unsafe 也不落地",
         lexicon.fix_text("唔系纯手工", neg_lex, allow_unsafe=True) == "唔系纯手工")
    must("词表链路：两道闸都显式开了才落地",
         lexicon.fix_text("唔系纯手工", neg_lex, allow_unsafe=True,
                          allow_negation_edit=True) == "系纯手工")

    # ---- ④ 防误杀：真话不许判红
    print("④ 防误杀（真话不许判红）")
    keep = [
        ("你要效果好好啊得噶", "残词"),
        ("我哋嘅厨房先大", "残词"),
        ("就系纯手工现点现做嘅", "残词"),
    ]
    for text, why in keep:
        cues = [{"text": text, "start": 0.0, "end": 1.0, "el": 1, "break_after": None}]
        must(f"不误杀：{text}（{why}）", not check_subs.semantic_judge(cues)["suspect"])
    mag_ok = ["三十米高的中庭", "六百平嘅点赞茶楼", "坐到十个人以上", "里面有两百平嘅厨房"]
    for text in mag_ok:
        hits = LT.scan_magnitudes([("x", text)])
        must(f"不判红：{text}", not [h for h in hits if h["tier"] == "absurd"],
              f"{[h['hit'] for h in hits]}")
    for text in ("慢慢坐坐", "得闲过嚟坐下", "对对对", "哈哈哈", "好嘅呀"):
        must(f"不判口吃：{text}", lexicon.stutter_tail(text) is None,
             f"{lexicon.stutter_tail(text)}")
    must("真口吃照样判：坐坐坐", lexicon.stutter_tail("你坐坐坐") == "坐坐坐")

    # ---- ⑤ 数词解析（量级判据的地基）：错一个，整张量级表就是噪音
    print("⑤ 中文数词")
    for s, v in [("十万", 100000), ("廿五", 25), ("两百", 200), ("十一", 11),
                 ("一万二千", 12000), ("2.5万", 25000), ("15", 15)]:
        must(f"cn2num({s}) = {v}", LT.cn2num(s) == v, f"实际 {LT.cn2num(s)}")

    # ---- ⑥ 门禁接线：check_subs 真的会因此判红（不是只有函数会说）
    print("⑥ 门禁接线（check_subs 端到端）")
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "p"
        (proj / "out").mkdir(parents=True)
        (proj / "out" / "subs.json").write_text(
            json.dumps({"cues": films["dianzan-c"]}, ensure_ascii=False), encoding="utf-8")
        raw = subprocess.run([sys.executable, str(HERE / "check_subs.py"), "--project", str(proj)],
                             capture_output=True, text=True)
        out = raw.stdout + raw.stderr
        must("原片：残词判红", "残词 ✗" in out, out[-200:])
        must("原片：退出码非 0", raw.returncode != 0, f"exit={raw.returncode}")
        (proj / "out" / "subs.json").write_text(
            json.dumps({"cues": fixed["dianzan-c"]}, ensure_ascii=False), encoding="utf-8")
        fix = subprocess.run([sys.executable, str(HERE / "check_subs.py"), "--project", str(proj)],
                             capture_output=True, text=True)
        out2 = fix.stdout + fix.stderr
        must("修正后：残词 ✅", "残词 ✅" in out2, out2[-200:])
        must("修正后：量级 ✅", "量级 ✅" in out2, out2[-200:])
        must("修正后：口吃重尾 ✅", "口吃重尾 ✅" in out2, out2[-200:])

    print()
    if fails:
        print(f"语义校对合同：{len(fails)} 条没过")
        for f in fails:
            print(f"  · {f}")
        return 1
    print("语义校对合同：全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
