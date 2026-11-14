#!/usr/bin/env python3
"""T-020 回归：开场两变（BRIEF 剪辑表第 4 条）必须在**时间轴生成期**就成立。

判据是 `verify_film.first_seconds_verdict`（前 3 秒 ≥2 次画面变化，每个变化点离最近
音频事件 ≤80ms），阈值不许动；能改的是产线。这个测试有两类样本：

  正样本：一条 5s 口播 → 该切的位置切两刀，然后把切完的轴喂回**判据本身**，
          必须判绿。拿判据验，不另写一套「应该算过了」的自我安慰。
  优先级①：轴上已经有钩子卡/大字（`track == "overlay"` 的 gen 元素）时，先把它挪进
          前 3 秒踩在音频事件上——**一个刀口都不开**（元素/字幕与没做开场两变时一样）。
  负样本：只有一镜空镜（没声音）/ 口播没有逐字时间戳 / 前场声音挪不动 / 把那张卡
          删了只剩切句内
          → 必须显式失败并说清缺什么，**不许**静默退回「首镜 5 秒」——
          那正是 8/8 支片首镜 3.17–5.79s、判据全红的根因。

全部用合成字表（`chars_of` / `motion` 注入），不依赖素材库是否在本机。

用法：python3 scripts/test_opening_changes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import timeline as TL                  # noqa: E402
import verify_film as V                # noqa: E402

W, H = 1080, 1920


def _clip(n: int, asset: str, a0: float, a1: float, medium: str = "talking",
          audio: dict | None = None, **kw) -> dict:
    el = {"n": n, "kind": "clip", "track": "video", "asset": asset,
          "in": round(a0, 3), "out": round(a1, 3), "medium": medium,
          "source": "library", "why": kw.pop("why", "测试镜头")}
    if audio is not None:
        el["audio"] = dict(audio)
    elif medium == "talking":
        el["audio"] = {"asset": asset, "in": round(a0, 3), "out": round(a1, 3)}
    el.update(kw)
    return el


def _chars(text: str, t0: float, step: float = 0.25, gaps: dict[int, float] | None = None):
    """合成逐字时间戳：每字 step 秒，`gaps[i]` 在第 i 个字前插一段停顿。"""
    out, t = [], float(t0)
    for i, ch in enumerate(text):
        t += float((gaps or {}).get(i, 0.0))
        out.append({"c": ch, "s": round(t, 3), "e": round(t + step, 3)})
        t += step
    return out


def _verdict(els: list[dict], chars_of) -> tuple[str, str, str]:
    """把切完的轴喂回真判据：变化点（sync_check.element_starts 口径）+ 块边界。"""
    tl = {"elements": els}
    return V.first_seconds_verdict(V.change_points(tl), TL._block_bounds(els, chars_of))


def main() -> int:
    fails: list[str] = []

    def must(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✅' if cond else '❌'} {name}" + (f" ({detail})" if detail and not cond else ""))
        if not cond:
            fails.append(name)

    flat = lambda aid, t: 1.0                                 # 晃动：全片站定了

    # ---------------------------------------------------------------- ① 正样本
    print("① 正样本：一整条 5s 口播 → 开场两变成立，且判据判绿")
    TEXT = "我花咗三百万喺金湾开咗间六百平嘅点赞茶楼"
    chars = _chars(TEXT, 0.0)
    chars_of = lambda aid: chars                              # noqa: E731
    els = [_clip(1, "talk.mov", 0.0, 5.0)]
    out, notes, probs = TL.add_opening_changes(els, chars_of=chars_of, motion=flat)
    lvl, val, note = _verdict(out, chars_of)
    changes = [t for t in TL._change_times(out) if 1e-6 < t <= TL.OPENING_WIN + 1e-6]
    must("没有 error", not probs, str(probs))
    must("前 3 秒有 2 次画面变化", len(changes) >= TL.OPENING_NEED, f"{changes}")
    must("判据判绿", lvl == "green", f"{lvl} · {val} · {note}")
    must("片长不变（切的是画面不是声音）",
         abs(sum(TL.element_dur(e) for e in out) - 5.0) < 1e-6,
         f"{[TL.element_dur(e) for e in out]}")
    must("声音首尾相接、不重不漏",
         all(abs(out[i]["audio"]["out"] - out[i + 1]["audio"]["in"]) < 1e-6
             for i in range(len(out) - 1)),
         str([(e.get("audio") or {}) for e in out]))
    must("台词按切口重新切分（不整句复读）",
         "".join(e["evidence"] for e in out) == TEXT,
         " | ".join(e["evidence"] for e in out))
    must("每格都写了 why", all(str(e.get("why") or "").strip() for e in out))

    # 第二变必须晚于第一变、且不贴边（不许闪帧）
    gaps = [TL.element_dur(e) for e in out[:-1]]
    must("每一格都不短于 OPENING_MIN_SHOT", min(gaps) >= TL.OPENING_MIN_SHOT - 1e-6, f"{gaps}")

    print("② 幂等：同一根轴跑第二遍不再动刀")
    out2, notes2, probs2 = TL.add_opening_changes(out, chars_of=chars_of, motion=flat)
    must("元素数不变", len(out2) == len(out), f"{len(out)} → {len(out2)}")
    must("没有新的切点（不重复动刀）",
         not [n for n in notes2 if n.startswith("开场第")], str(notes2))

    print("③ 已够两变的老轴：一刀不动")
    out3, notes3, probs3 = TL.add_opening_changes(out, chars_of=chars_of, motion=flat)
    must("原样返回", [e["n"] for e in out3] == [e["n"] for e in out] and not probs3)

    # ---------------------------------------------------------------- ④ 填充词不许当切点
    print("④ 填充词：落在「嗯」上的字界不能当变化点（填充词不进事件表）")
    # 桑0.00 拿0.30 ｜停顿｜ 嗯1.20 ｜停顿｜ 鸡1.80 咧2.10 嘅2.40 咁2.70
    # 「嗯」自成一块 = 填充词，进不了 audio-map 的事件表 → 切在这儿判据根本不认，
    # 所以它虽然离目标 1.3 最近（0.1s），也必须被跳过，退到下一个字界「鸡」。
    t_chars = _chars("桑拿嗯鸡咧嘅咁", 0.0, step=0.3, gaps={2: 0.6, 3: 0.3})
    out4, _n4, p4 = TL.add_opening_changes(
        [_clip(1, "talk.mov", 0.0, 3.3)], chars_of=lambda a: t_chars, motion=flat)
    lvl4, val4, note4 = _verdict(out4, lambda a: t_chars)
    cut_src = [round(float(e["in"]), 3) for e in out4[1:]]
    must("没有切在填充词「嗯」上（1.20s）",
         all(abs(c - 1.20) > 1e-6 for c in cut_src), f"切口 {cut_src}")
    must("仍然凑够两变并判绿", lvl4 == "green", f"{lvl4} · {val4} · {note4}")

    # ---------------------------------------------------------------- ⑤ 负样本
    print("⑤ 负样本：只有一镜且没有声音 → 显式失败，说清缺什么")
    silent = [_clip(1, "broll.mov", 0.0, 6.0, medium="broll")]
    out5, _n5, p5 = TL.add_opening_changes(silent, chars_of=lambda a: [], motion=flat)
    lvl5, val5, note5 = _verdict(out5, lambda a: [])
    errs5 = [m for lv, m in p5 if lv == "error"]
    must("报 error", bool(errs5), str(p5))
    must("说清「没有可对齐的语音块」", any("语音块" in m for m in errs5), str(errs5))
    must("没有偷偷切（还是原来那一镜）", len(out5) == 1 and out5[0]["out"] == 6.0)
    must("判据仍是红的（不假装过）", lvl5 != "green", f"{lvl5} · {val5}")

    print("⑥ 负样本：口播没有逐字时间戳（ASR 缺）→ 显式失败")
    out6, _n6, p6 = TL.add_opening_changes(
        [_clip(1, "talk.mov", 0.0, 4.0)], chars_of=lambda a: [], motion=flat)
    errs6 = [m for lv, m in p6 if lv == "error"]
    must("报 error", bool(errs6), str(p6))
    must("没有动轴", len(out6) == 1)

    print("⑦ 负样本：口播进得太晚、前场那镜又有自己的声音（挪不动）→ 显式失败")
    late = [_clip(1, "broll.mov", 0.0, 2.6, medium="broll",
                  audio={"asset": "amb.mov", "in": 0.0, "out": 2.6}),
            _clip(2, "talk.mov", 0.0, 5.4)]
    out7, _n7, p7 = TL.add_opening_changes(
        late, chars_of=lambda a: chars if a == "talk.mov" else [], motion=flat)
    errs7 = [m for lv, m in p7 if lv == "error"]
    must("报 error", bool(errs7), str(p7))
    must("说清缺的是「场上没有声音的开场镜头」",
         any("开场就出声" in m or "静帧不够" in m for m in errs7), str(errs7))

    print("⑧ 静帧开场：可以把没声音的开场镜头裁短，把口播拉进前 3 秒")
    late2 = [_clip(1, "broll.mov", 0.5, 3.6, medium="broll"),
             _clip(2, "talk.mov", 0.0, 5.0)]
    out8, notes8, p8 = TL.add_opening_changes(late2, chars_of=chars_of, motion=flat)
    lvl8, val8, note8 = _verdict(out8, chars_of)
    must("没有 error", not p8, str(p8))
    must("开场静帧被裁短并留痕", any("裁短" in n for n in notes8), str(notes8))
    must("判据判绿", lvl8 == "green", f"{lvl8} · {val8} · {note8}")

    print("⑨ 卡跟着锚词走：锚词在后半段 → 卡挂到后半段那一格")
    card_chars = _chars(TEXT, 0.0)
    elc = _clip(1, "talk.mov", 0.0, 5.0,
                card={"kind": "stat", "on": "六百平", "data": {"value": "600"}})
    outc, _nc, pc = TL.add_opening_changes([elc], chars_of=lambda a: card_chars, motion=flat)
    holder = [e for e in outc if e.get("card")]
    must("只有一格挂着卡", len(holder) == 1, str([e.get("card") for e in outc]))
    must("卡挂在锚词所在的那一格",
         bool(holder) and "六百平" in (holder[0].get("evidence") or ""),
         str([e.get("evidence") for e in outc]))
    must("没有 error", not pc, str(pc))

    # ------------------------------------------------ ⑩ 优先级①：现成的卡挪位
    # 文案在两处逗号断开：块边界落在 1.25s / 2.50s，正是两张卡该踩的位置。
    # 卡原在 6.5s / 8.0s，都在前 3 秒之外。
    print("⑩ 优先级①：轴上现成的两张 overlay 卡挪进开场，一刀不切")
    card_text = "我花咗三，百万喺金，湾开咗六百平"
    cchars = _chars(card_text, 0.0, step=0.25)
    cards = [{"n": 2, "kind": "gen", "track": "overlay", "template": "keyword",
              "props": {"text": "三百万"}, "dur": 2.0, "at": 6.5, "source": "generated",
              "why": "钩子卡"},
             {"n": 3, "kind": "gen", "track": "overlay", "template": "keyword",
              "props": {"text": "六百平"}, "dur": 2.0, "at": 8.0, "source": "generated",
              "why": "大字"}]
    axis = [_clip(1, "talk.mov", 0.0, 6.0)] + cards
    out10, notes10, p10 = TL.add_opening_changes(axis, chars_of=lambda a: cchars, motion=flat)
    lvl10, val10, note10 = _verdict(out10, lambda a: cchars)
    ov10 = [round(float(e.get("at") or 0), 3) for e in out10 if e.get("track") == "overlay"]
    must("没有 error", not p10, str(p10))
    must("判据判绿", lvl10 == "green", f"{lvl10} · {val10} · {note10}")
    must("一刀没切（元素还是 3 个）", len(out10) == len(axis), f"{len(axis)} → {len(out10)}")
    must("要切镜的口播格原样（in/out/evidence 都没变）",
         out10[0]["in"] == axis[0]["in"] and out10[0]["out"] == axis[0]["out"]
         and not out10[0].get("evidence"),
         str({k: out10[0].get(k) for k in ("in", "out", "evidence")}))
    must("两张卡都挪进了前 3 秒", len(ov10) == 2 and all(0 < t <= 3.0 for t in ov10), f"{ov10}")
    must("两张卡不叠在同一帧上", len(ov10) == 2 and abs(ov10[0] - ov10[1]) >= TL.OPENING_MIN_GAP,
         f"{ov10}")
    must("只挪卡、不切句（notes 里没有任何一刀）",
         not [n for n in notes10 if "切一刀" in n], str(notes10))
    must("挪位留痕（说清了从哪儿挪到哪儿）",
         len([n for n in notes10 if "入场" in n]) == 2, str(notes10))

    print("⑪ 幂等：已经挪好的卡再跑一遍不再动")
    out11, notes11, p11 = TL.add_opening_changes(out10, chars_of=lambda a: cchars, motion=flat)
    must("没有任何新动作", not [n for n in notes11 if "入场" in n or "切一刀" in n], str(notes11))
    must("卡的位置没变", [round(float(e.get("at") or 0), 3) for e in out11
                          if e.get("track") == "overlay"] == ov10)
    must("没有 error", not p11, str(p11))

    # ------------------------------------------------ ⑫ 负样本：把卡删了/挪出窗口
    print("⑫ 负样本：把两张卡删掉（只剩口播）→ 退到优先级②/③切镜，不崩也不静默不补")
    out12, notes12, p12 = TL.add_opening_changes([_clip(1, "talk.mov", 0.0, 6.0)],
                                                chars_of=lambda a: cchars, motion=flat)
    lvl12, val12, note12 = _verdict(out12, lambda a: cchars)
    must("没有 error", not p12, str(p12))
    must("判据判绿（补上了，不是静默退回长首镜）", lvl12 == "green", f"{lvl12} · {val12}")
    must("证据：这次是靠切镜补的", bool([n for n in notes12 if "切一刀" in n]), str(notes12))
    must("确实开了刀（元素变多）", len(out12) > 1, f"{len(out12)} 元素")
    must("片长不变（切的是画面不是声音）",
         abs(sum(TL.element_dur(e) for e in out12) - 6.0) < 1e-6,
         f"{[TL.element_dur(e) for e in out12]}")

    print("⑫b 同一根轴：把那张已经挪进开场的卡删掉再跑 → 退到切镜（不崩、不静默不补）")
    stripped = [e for e in out10 if e.get("track") != "overlay"]
    out12b, notes12b, p12b = TL.add_opening_changes(stripped, chars_of=lambda a: cchars,
                                                    motion=flat)
    lvl12b, val12b, _note12b = _verdict(out12b, lambda a: cchars)
    must("没有 error", not p12b, str(p12b))
    must("判据判绿", lvl12b == "green", f"{lvl12b} · {val12b}")
    must("这次只能靠切镜补上", bool([n for n in notes12b if "切一刀" in n]), str(notes12b))
    must("片长不变", abs(sum(TL.element_dur(e) for e in out12b) - 6.0) < 1e-6,
         f"{[TL.element_dur(e) for e in out12b]}")

    print("⑬ 负样本：卡挪出窗口、口播又短到切不出两刀 → 显式 error 并说清缺什么")
    short = [_clip(1, "talk.mov", 0.0, 1.0)]
    out13, _n13, p13 = TL.add_opening_changes(short, chars_of=lambda a: cchars, motion=flat)
    errs13 = [m for lv, m in p13 if lv == "error"]
    must("报 error", bool(errs13), str(p13))
    must("说清缺的是「现成的钩子卡/大字」或更长的开场素材",
         any("钩子卡" in m or "开场素材" in m for m in errs13), str(errs13))
    must("没有偷偷改轴", len(out13) == 1)

    print("⑭ 卡不许抢拍：卡只落在音频事件上，且到「接下来那个字」不超过 PASS_LAG")
    # 两处气口：0.75–1.60s（0.6s）与 2.10–2.50s（0.4s）。
    # 块尾 1.00s / 2.10s 都落在气口里 —— 卡落在那儿就是抢拍（声音 0.4–0.6s 后才到），
    # 只能挑「下一字起点」的 1.60s / 2.50s。
    gchars = _chars("我花咗三百万六百", 0.0, step=0.25, gaps={4: 0.6, 6: 0.4})
    one = [_clip(1, "talk.mov", 0.0, 3.4),
           {"n": 2, "kind": "gen", "track": "overlay", "template": "keyword",
            "props": {"text": "三百万"}, "dur": 2.0, "at": 7.0, "source": "generated",
            "why": "钩子卡"},
           {"n": 3, "kind": "gen", "track": "overlay", "template": "keyword",
            "props": {"text": "六百平"}, "dur": 2.0, "at": 8.0, "source": "generated",
            "why": "大字"}]
    out14, _n14, p14 = TL.add_opening_changes(one, chars_of=lambda a: gchars, motion=flat)
    ov14 = [round(float(e.get("at") or 0), 3) for e in out14 if e.get("track") == "overlay"]
    words = [TL._word_from(out14, lambda a: gchars, t) for t in ov14]
    must("两张卡都挪进了开场", len(ov14) == 2 and all(0 < t <= 3.0 for t in ov14), f"{ov14}")
    must("没有卡落在气口中间 1.00s / 2.10s（抢拍）",
         all(abs(t - 1.0) > 1e-6 and abs(t - 2.1) > 1e-6 for t in ov14), f"{ov14}")
    must("声音先到：每张卡入场都不早于它锚定的词（≤PASS_LAG）",
         all(w and 0 <= w["s"] - t <= TL.OPENING_LAG + 1e-6
             for t, w in zip(ov14, words)), f"at={ov14} words={words}")
    must("卡窗口罩得住那个词",
         all(w and t + 2.0 >= w["e"] - 1e-6 for t, w in zip(ov14, words)),
         f"at={ov14} words={words}")
    must("没有 error、一刀没切", not p14 and len(out14) == len(one), f"{p14} · {len(out14)}")

    print()
    if fails:
        print(f"❌ {len(fails)} 项没过：{'、'.join(fails)}")
        return 1
    print("✅ 开场两变单测全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
