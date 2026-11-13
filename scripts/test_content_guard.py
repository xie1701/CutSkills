#!/usr/bin/env python3
"""content_guard 的合同测试：三条新判据该红时红、该绿时绿。

这个测试锁四件事：
  1. **改动前不拦、改动后拦**——三组负样本先证明原来的 `check_timeline.check()` 全绿
     （这正是第五轮审查的病：8 道脚本 0 error、成片三处穿帮），再由 `content_guard` 判红。
     只写「现在会红」证明不了任何事，可能是旧判据早就红了；
  2. 起尾余量（A1）三档：起幅不足 / 尾段不足 / 量不出时长（降级 warning，不许假装判过）；
  3. 视觉指代词（A3）四条：无佐证判 error 且写进 `gap.md`；`visual_ref` / image-gen /
     L2 图类标签三种佐证都放行；
  4. 品类禁忌（A4）三条：L2 标签、元素标签、干净画面，以及写错标签名要报出来。

判据一紧就误杀，一松就放过，两头都会让人不再信它——所以正样本和负样本必须同时在这里。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_timeline as CT  # noqa: E402
import content_guard as CG  # noqa: E402

BROLL_WHY = "用门口画面证明四个门口这件事"
N = 0          # 跑了多少条用例（计数只为了让尾行报分母）


def index_of(dur: float = 10.0, *, tags: list[str] | None = None,
             l2: list[dict] | None = None) -> dict:
    """一支 10 秒素材。tags 是 L2 标签文本，l2 是整条段记录（含 taboo_tags）。"""
    segs = l2 if l2 is not None else (
        [{"in": 0.0, "out": dur, "tag": tags[0], "action": "", "summary": "",
          "grade": "must-use"}] if tags else [])
    return {"a.mov": {"id": "a.mov", "kind": "video", "duration": dur, "l2_segments": segs,
                      "segments": segs},
            "p.jpg": {"id": "p.jpg", "kind": "photo", "duration": 0, "segments": []}}


def broll(vin: float, vout: float, *, evidence: str = "我哋一共有四个门口",
          props: dict | None = None, asset: str = "a.mov") -> dict:
    el = {"n": 1, "kind": "clip", "track": "video", "asset": asset,
          "in": vin, "out": vout, "medium": "broll", "source": "library",
          "why": BROLL_WHY, "evidence": evidence,
          # 声音走另一支素材：同源时 check_timeline 会另判「音画同源」，这里不掺进来
          "audio": {"asset": "vo.mov", "in": 10.0, "out": 10.0 + (vout - vin)}}
    if props is not None:
        el["props"] = props
    return el


def tl_of(el: dict) -> dict:
    dur = round(float(el.get("out") or 0) - float(el.get("in") or 0), 3) \
        if el.get("kind") == "clip" else float(el.get("dur") or 0)
    return {"version": 4, "duration_s": dur, "elements": [el]}


def old_errors(tl: dict, index: dict) -> list[str]:
    """改动前的行为：只跑 check_timeline 自己的判据（content_guard 是在 main() 里挂的）。"""
    return [m for lv, m in CT.check(tl, index) if lv == "error"]


def new_probs(tl: dict, index: dict, proj: Path) -> list[tuple[str, str]]:
    return CG.guard(tl, index, proj=proj)


def run_case(name: str, tl: dict, index: dict, want: str, *, key: str,
             old_clean: bool = False) -> int:
    """want: error / warning / clean。key 是期望命中的中文片段（clean 时忽略）。"""
    global N
    N += 1
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "_cutskills" / "projects" / "t"
        proj.mkdir(parents=True)
        if old_clean:
            oe = old_errors(tl, index)
            if oe:
                print(f"  ✗ {name}｜旧判据已经报错，证明不了「改动前不拦」：{oe[:2]}")
                return 1
        probs = new_probs(tl, index, proj)
        got = [f"{lv}:{m}" for lv, m in probs]
        if want == "clean":
            ok = not probs
        elif want == "error":
            ok = any(lv == "error" and key in m for lv, m in probs)
        else:
            ok = (any(lv == "warning" and key in m for lv, m in probs)
                  and not any(lv == "error" for lv, _ in probs))
        print(f"  {'✓' if ok else '✗'} {name}｜" + ("无问题" if not got else got[0][:110]))
        if not ok:
            for g in got[1:]:
                print("      " + g[:150])
        return 0 if ok else 1


def main() -> int:
    global N
    bad = 0

    print("① 起尾余量（A1）：0.00s 按键糊片 / 收机甩镜 / 量不出 / 豁免")
    bad += run_case("in: 0.0 的 B-roll（按键糊片，原案）", tl_of(broll(0.0, 3.0)),
                    index_of(), "error", key="起幅余量不足", old_clean=True)
    bad += run_case("尾段只剩 0.10s（收机甩镜）", tl_of(broll(5.0, 9.9)),
                    index_of(), "error", key="距素材尾", old_clean=True)
    bad += run_case("起 0.39s（还不够 0.40s）", tl_of(broll(0.39, 5.0)),
                    index_of(), "error", key="起幅余量不足", old_clean=True)
    bad += run_case("起 0.40s / 尾留 0.60s（正样本：不是误杀阈值）", tl_of(broll(0.40, 9.40)),
                    index_of(), "clean", key="")
    bad += run_case("索引量不出时长（降级 warning，不许当干净）",
                    tl_of(broll(1.0, 5.0, asset="a.mov")),
                    {"a.mov": {"id": "a.mov", "duration": 0}}, "warning", key="量不出",
                    old_clean=True)
    bad += run_case("豁免带理由（放行但留痕）",
                    tl_of(broll(0.0, 3.0, props={"handles_exempt": "素材本身只有 3s，动作在开头半秒"})),
                    index_of(), "warning", key="豁免放行", old_clean=True)
    bad += run_case("豁免没写理由（不认，照判红）",
                    tl_of(broll(0.0, 3.0, props={"handles_exempt": "  "})),
                    index_of(), "error", key="起幅余量不足", old_clean=True)
    bad += run_case("照片（medium: photo）不套 B-roll 余量判据",
                    tl_of({"n": 1, "kind": "image", "track": "video", "asset": "p.jpg",
                           "dur": 3.0, "medium": "photo", "source": "library",
                           "why": "用一张照片交代门口数量"}),
                    index_of(), "clean", key="")

    print("② 视觉指代词（A3）：「四张图片」配工地空镜 = 口播承诺落空")
    dej = "今日，我就用四张图片同大家介绍下，我哋嘅布局系点样嘅"
    bad += run_case("四张图 + 工地空镜（原案）",
                    tl_of(broll(0.5, 4.0, evidence=dej)),
                    index_of(tags=["工地空镜"]), "error", key="四张图", old_clean=True)
    bad += run_case("四张图 + props.visual_ref（放行）",
                    tl_of(broll(0.5, 4.0, evidence=dej, props={"visual_ref": "厨房布局图.png"})),
                    index_of(tags=["工地空镜"]), "clean", key="")
    bad += run_case("四张图 + L2 标签命中「效果图」（放行）",
                    tl_of(broll(0.5, 4.0, evidence=dej)),
                    index_of(l2=[{"in": 0.0, "out": 10.0, "tag": "厨房布局效果图",
                                  "grade": "must-use"}]), "clean", key="")
    bad += run_case("四张图 + kind: image（照片本身就是图，放行）",
                    tl_of({"n": 1, "kind": "image", "track": "video", "asset": "p.jpg",
                           "dur": 3.0, "medium": "photo", "source": "library",
                           "evidence": dej, "why": "四张布局图连着上屏"}),
                    index_of(), "clean", key="")
    bad += run_case("没提指代词的台词（放行：不许误杀）",
                    tl_of(broll(0.5, 4.0, evidence="我哋一共有四个门口楼面两个门口厨房")),
                    index_of(tags=["门口外景"]), "clean", key="")

    print("③ 品类禁忌（A4）：夸「原汁原味」配生肉血水 = 反向宣传")
    bad += run_case("夸原汁原味 + L2 raw_meat（原案：四大天王+生鸡肉特写）",
                    tl_of(broll(0.5, 4.0, evidence="四大天王招牌点心，就系鸡嗰种原汁原味")),
                    index_of(l2=[{"in": 0.0, "out": 10.0, "tag": "生鸡肉切盘",
                                  "taboo_tags": ["raw_meat"], "grade": "backup"}]),
                    "error", key="raw_meat", old_clean=True)
    bad += run_case("夸「干干净净」+ 元素 props.taboo_tags 命中 crew_leak",
                    tl_of(broll(0.5, 4.0, evidence="厨房干干净净，放心嚟食",
                                props={"taboo_tags": ["crew_leak"]})),
                    index_of(), "error", key="crew_leak", old_clean=True)
    bad += run_case("夸舒服 + 干净画面（放行：不许把空间镜头全判死）",
                    tl_of(broll(0.5, 4.0, evidence="空间高先可以坐得舒服")),
                    index_of(l2=[{"in": 0.0, "out": 10.0, "tag": "大厅空镜",
                                  "taboo_tags": [], "grade": "must-use"}]), "clean", key="")
    bad += run_case("夸「招牌」+ 标签写错（raw-meat → 认不出，但必须报出来）",
                    tl_of(broll(0.5, 4.0, evidence="我哋嘅招牌特色炒面",
                                props={"taboo_tags": ["raw-meat"]})),
                    index_of(), "warning", key="不认识的标签", old_clean=True)

    print("④ 缺口落盘：error 同时写进 gap.md，带时间戳，重跑不堆重复")
    N += 1
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "_cutskills" / "projects" / "t"
        proj.mkdir(parents=True)
        (proj / "gap.md").write_text("# 缺口清单 · t\n\n## 一、论证缺口\n\n没有。\n", encoding="utf-8")
        tl, idx = tl_of(broll(0.5, 4.0, evidence=dej)), index_of(tags=["工地空镜"])
        CG.guard(tl, idx, proj=proj)
        CG.guard(tl, idx, proj=proj)          # 第二遍：整节替换，不许堆两条
        g = (proj / "gap.md").read_text(encoding="utf-8")
        ok = ("指代词缺画面" in g and "四张图" in g and "最近检查：" in g
              and g.count("最近检查：") == 1 and "论证缺口" in g)
        print(f"  {'✓' if ok else '✗'} gap.md 追加了指代缺口（1 节 / 带时间戳 / 保留原有章节）")
        bad += 0 if ok else 1

    print(f"\n内容守卫：{N - bad}/{N} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
