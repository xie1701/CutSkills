#!/usr/bin/env python3
"""旧版 dianzan-a/b/c 硬伤回归负样本。

每一案都喂给产线现有检查器的函数，不在本文件重写检测规则。正向通过条件是：旧 NG
确实被对应判据报告 error；缺少素材的视觉案使用隔离的合成项目与已存在测试包。

  python3 scripts/test_regression_ng.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import broll_split as BS  # noqa: E402
import check_overlay as CO  # noqa: E402
import check_subs as CS  # noqa: E402
import check_timeline as CT  # noqa: E402
import content_guard as CG  # noqa: E402
import speech_hygiene as SH  # noqa: E402
import visual_grade as VG  # noqa: E402
import test_keyword_text as KT_FIXTURE  # noqa: E402


def has_error(rows, text: str) -> bool:
    return any(level == "error" and text in msg for level, msg in rows)


def main() -> int:
    bad = 0
    passed = 0

    def case(name: str, hit: bool, detail: str = "") -> None:
        nonlocal bad, passed
        print(f"  {'🔴 已抓红' if hit else '❌ 漏放'} · {name}" + (f"｜{detail}" if detail else ""))
        if hit:
            passed += 1
        else:
            bad += 1

    # a#13：异常物理量级必须从字幕语义门禁报红。
    sem_a13 = CS.semantic_judge([{"text": "大约有十万米高"}])
    hit_a13 = any(x["hit"].startswith("十万米") and x["tier"] == "absurd"
                  for x in sem_a13["magnitude_bad"])
    case("dianzan-a #13「十万米高」量级荒谬", hit_a13,
         ", ".join(x["hit"] for x in sem_a13["magnitude_bad"]))

    # a#5：口播音频 / 画面来源的窗口时长错位由 check_timeline.check 报红。
    av_tl = {"duration_s": 4.0, "elements": [{
        "n": 5, "kind": "clip", "track": "video", "asset": "picture-take.mov",
        "medium": "talking", "in": 2.0, "out": 6.0,
        "audio": {"asset": "voice-take.mov", "in": 19.0, "out": 22.5},
        "why": "负样本：画面和口播窗口不是同一时长", "evidence": "早茶很好吃"}]}
    av_rows = CT.check(av_tl, {})
    case("dianzan-a #5 声画异源且时长不同", has_error(av_rows, "声画时长要对齐"),
         next((m for lv, m in av_rows if lv == "error" and "声画时长" in m), ""))

    # b#8/#9：两个独立窗口，正向夸赞撞 raw_meat 必须分别红。
    for n, phrase in ((8, "原汁原味"), (9, "招牌新鲜")):
        aid = f"b-roll-{n}.mov"
        tl = {"elements": [{"n": n, "kind": "clip", "track": "video", "asset": aid,
                            "medium": "broll", "in": 0.5, "out": 4.0,
                            "evidence": f"这款点心{phrase}"}]}
        index = {aid: {"id": aid, "duration": 8.0, "l2_segments": [
            {"in": 0.0, "out": 5.0, "tag": "生鸡肉切盘", "taboo_tags": ["raw_meat"]}]}}
        rows = CG.guard(tl, index)
        case(f"dianzan-b #{n} 正向夸赞撞生鸡肉画面", has_error(rows, "raw_meat"),
             next((m for lv, m in rows if lv == "error" and "raw_meat" in m), ""))

    # b#21 / c#8：旧 ASR 残词也是 T-017 同一条字幕语义门禁。
    for slug, n, text, wrong in (
        ("dianzan-b", 21, "大咧先可以用入", "大咧"),
        ("dianzan-c", 8, "我哋嘅效果好先", "效果好"),
    ):
        sem = CS.semantic_judge([{"text": text}])
        hits = [x["hit"] for x in sem["suspect"]]
        case(f"{slug} #{n}「{wrong}」残词", wrong in hits, ", ".join(hits))

    # c#16–19：字幕排版把「生万」拆跨条时，width_runs 会拼回原词再判。
    split_cues = [
        {"text": "我哋嘅", "break_after": "width", "el": 16},
        {"text": "生", "break_after": "width", "el": 16},
        {"text": "万鱼", "break_after": "width", "el": 16},
        {"text": "池", "break_after": "clause", "el": 16},
    ]
    sem_c16 = CS.semantic_judge(split_cues)
    split_hits = [x["hit"] for x in sem_c16["suspect"]]
    case("dianzan-c #16–19 跨字幕条「生万」", "生万" in split_hits,
         ", ".join(split_hits))

    # 切点贴进半个词：入口残字「嘅」与尾巴悬空「因」由同一口播洁净度函数报红。
    base_el = {"n": 4, "audio": {"asset": "voice.mov", "in": 1.0, "out": 2.0}}
    head_rows = SH.per_element(base_el, [("嘅", 1.02), ("厨房", 1.25)], None)
    case("跳切入口切在半个词（首字「嘅」）", has_error(head_rows, "切在词中间"),
         next((m for lv, m in head_rows if lv == "error" and "切在词中间" in m), ""))
    tail_rows = SH.per_element(base_el, [("我哋", 1.1), ("因", 1.8)], None)
    case("跳切尾端切在词起头（「因…」）", has_error(tail_rows, "尾巴切在"),
         next((m for lv, m in tail_rows if lv == "error" and "尾巴切在" in m), ""))

    # a×b：真实硬伤读数 25.49s，确认 broll_split 现在会按跨片口播窗口重叠判红。
    with tempfile.TemporaryDirectory(prefix="regression-ng-") as td:
        root = Path(td)
        group = []
        for slug in ("dianzan-a", "dianzan-b"):
            p = root / slug
            p.mkdir()
            group.append(p)
            (p / "timeline.json").write_text(json.dumps({"elements": [{
                "n": 1, "kind": "clip", "track": "video", "asset": f"picture-{slug}.mov",
                "medium": "broll", "in": 0.5, "out": 26.0,
                "audio": {"asset": "shared-voice.mov", "in": 0.0, "out": 25.49}}]},
                ensure_ascii=False), encoding="utf-8")
        rows = BS.audit(group)
        hit = any(lv == "error" and "重叠 25.5s" in msg for lv, msg in rows)
        case("dianzan-a×b 口播素材重叠 25.49s", hit,
             next((m for lv, m in rows if lv == "error" and "重叠" in m), ""))

    # 0.00s 糊片、无图证的指代词分别使用 T-016 现行纯函数。
    h_tl = {"elements": [{"n": 6, "kind": "clip", "track": "video", "asset": "blur.mov",
                           "medium": "broll", "in": 0.0, "out": 3.0,
                           "why": "负样本：从按键帧起"}]}
    h_rows = CG.guard(h_tl, {"blur.mov": {"id": "blur.mov", "duration": 8.0}})
    case("B-roll 0.00s 起幅糊片", has_error(h_rows, "起幅余量不足"),
         next((m for lv, m in h_rows if lv == "error" and "起幅余量不足" in m), ""))
    ref_tl = {"elements": [{"n": 3, "kind": "clip", "track": "video", "asset": "site.mov",
                             "medium": "broll", "in": 0.5, "out": 4.0,
                             "evidence": "我用四张图片介绍布局"}]}
    ref_rows = CG.guard(ref_tl, {"site.mov": {"id": "site.mov", "duration": 10.0}})
    case("说四张图却给工地空镜", has_error(ref_rows, "没有对应视觉佐证"),
         next((m for lv, m in ref_rows if lv == "error" and "视觉佐证" in m), ""))

    # T-015：拿稿/看场外只留声音；挂成 talking 露脸段必须报红。
    bad_take = [{"n": 2, "kind": "clip", "track": "video", "asset": "script.mov",
                 "medium": "talking", "in": 5.0, "out": 8.0,
                 "props": {"video_grade": "audio_only", "grade_why": "手持 A4 提词稿"}}]
    vg_rows = VG.gate(bad_take, None)
    case("拿稿画面被挂成 talking 露脸", has_error(vg_rows, "画面穿帮"),
         next((m for lv, m in vg_rows if lv == "error"), ""))

    # T-019：MG 卡与 Vision 原始脸框重叠，使用 check_overlay.audit 真正入口。
    with tempfile.TemporaryDirectory(prefix="regression-face-") as td:
        p = Path(td)
        (p / "out").mkdir()
        (p / "hf" / "work" / "faces").mkdir(parents=True)
        element = {"n": 1, "kind": "clip", "track": "video", "asset": "face.mov",
                   "medium": "talking", "in": 0.0, "out": 3.0}
        (p / "timeline.json").write_text(json.dumps({"elements": [element]}), encoding="utf-8")
        face = {"t": 1.0, "x": 0.2, "y": 0.2, "w": 0.35, "h": 0.25}
        (p / "hf" / "work" / "faces" / "faceclip.json").write_text(json.dumps([face]), encoding="utf-8")
        (p / "out" / "cards.json").write_text(json.dumps({"cards": [{
            "seg": 1, "kind": "term", "text": "硬伤负样本", "start": 0.5, "end": 2.5,
            "layout": {"mode": "focus_blur", "box": [0.2, 0.2, 0.35, 0.25],
                       "plate": [0.15, 0.15, 0.45, 0.35],
                       "face_refs": [{"clip": "faceclip", "offset": 0.0, "dur": 3.0}],
                       "face_src": "vision"}}]}), encoding="utf-8")
        face_rows = CO.audit(p)
        case("MG 卡框挡住口播人脸", has_error(face_rows, "压脸"),
             next((m for lv, m in face_rows if lv == "error" and "压脸" in m), ""))

    # T-045：低对比「撞色」画面必须由真实的大字墨迹可读性判据拦截。
    with tempfile.TemporaryDirectory(prefix="regression-contrast-") as td:
        root = Path(td)
        pack = KT_FIXTURE.pack(root, name="contrast", fit_w=0.70)
        project = KT_FIXTURE.project(root / "project", face=None, color="0xEDE8DC")
        import check_keyword_text as KT
        contrast_rows = KT.audit(project, pack_file=pack)
        hit = has_error(contrast_rows, "可读性") and any(
            lv == "error" and "背景吃掉" in msg for lv, msg in contrast_rows)
        case("亮底黄字撞色 / ΔL 不足", hit,
             next((m for lv, m in contrast_rows if lv == "error" and "可读性" in m), ""))

    total = passed + bad
    print(f"\n旧版 dianzan 回归负样本：{passed}/{total} 条已被产线判据报红；漏放 {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
