#!/usr/bin/env python3
"""scene_spec.json——单个补镜画面的生产任务单（AI 视听方案 2026-11-14 · P0）。

时间轴只有一个真值源（timeline.json），scene_spec 不是第二套时间线，它是
**一个 gen 元素的委托书**：这段画面想表达什么、事实依据是哪句口播、场景里有
哪些动作锚点要配音效。渲器把 `props.events` 里的锚点映射到全片时间轴混音；
验收层拿 `claim_source` 证明场景没有画出原文未支持的事实。

文件放在 `<项目>/scenes/<scene_id>.json`，timeline 的 gen 元素用
`props.scene_spec: "<scene_id>"` 引用。`template: scene` 的叙事场景**必须**有任务单，
且 review 三项（visual / audio / truth）签收后才能渲——未签收在 scene_spec --check
与 gate 里都是 error（2026-11-14 收紧）。

用法：
  python3 scripts/scene_spec.py --check <项目>          # 校验全部引用
  python3 scripts/scene_spec.py --init <项目> <scene_id>  # 生成空白任务单
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 场景六类（方案 §4.2）：kind 必须落在其中，防「想到什么写什么」
KINDS = {"process", "spatial", "situational", "causal", "informational", "concept"}
# 六拍问答（§4.2）：一场戏必须先回答，再写代码
BEAT_KEYS = ("subject", "action", "change", "camera", "audience", "sound")
# 审核签收口径（2026-11-14 收紧）：三个 review 字段必须落在这些值里才算签收。
# pending / 空串 / 自造词一律算「未签收」——无人值守交付前，必要审查未完成就是硬失败。
REVIEW_OK = {"ok", "approved", "signed", "done"}


def review_signed(spec: dict) -> list[str]:
    """返回未签收的 review 字段名列表（空列表 = 三项全签了）。"""
    rv = spec.get("review") or {}
    return [k for k in ("visual", "audio", "truth")
            if str(rv.get(k) or "").strip().lower() not in REVIEW_OK]


def load(proj: Path, scene_id: str) -> dict | None:
    p = proj / "scenes" / f"{scene_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"✗ {p.name} 不是合法 JSON：{e}")


def check_one(spec: dict, el: dict, dur: float, sfx_ids: set[str]) -> list[str]:
    """返回 error 列表（warning 另算）。spec 与引用它的 timeline 元素一起判。"""
    errs: list[str] = []
    sid = spec.get("scene_id") or "?"
    if spec.get("kind") not in KINDS:
        errs.append(f"{sid}: kind 必须是六类之一 {sorted(KINDS)}，现在是 {spec.get('kind')!r}")
    beats = spec.get("beats") or {}
    miss = [k for k in BEAT_KEYS if not (beats.get(k) or "").strip()]
    if miss:
        errs.append(f"{sid}: 六问没答全（缺 {miss}）——先讲故事再写代码，不许跳过")
    # 事实依据：claim_source.evidence 必须与元素 evidence 同源（场景不许画出口播没说的话）
    cs = spec.get("claim_source") or {}
    ev_el = (el.get("evidence") or "").strip()
    ev_cs = (cs.get("evidence") or "").strip()
    if not ev_cs:
        errs.append(f"{sid}: claim_source.evidence 缺失——场景的事实依据必须追溯到口播原句")
    elif ev_el and ev_cs not in ev_el and ev_el not in ev_cs:
        errs.append(f"{sid}: claim_source.evidence 与时间轴 evidence 不同源")
    # 音效事件：t 在场景时长内、name 必须能在 SFX 库对上
    for ev in spec.get("audio", {}).get("events") or []:
        t = ev.get("t")
        if not isinstance(t, (int, float)) or not (0 <= t <= dur + 0.05):
            errs.append(f"{sid}: 事件 {ev.get('id')!r} 的 t={t!r} 不在场景时长 [0,{dur:.2f}] 内")
        nm = ev.get("name") or ""
        if nm and sfx_ids and nm not in sfx_ids:
            errs.append(f"{sid}: 事件 {ev.get('id')!r} 的音效 {nm!r} 在 CC0 库里查不到")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--check", action="store_true", help="校验项目里全部 scene_spec 引用")
    ap.add_argument("--init", nargs=2, metavar=("SCENE_ID", "KIND"), default=None)
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()

    if not (a.init or a.check):
        a.check = True   # 默认就是校验，--check 是显式写法
    if a.init:
        sid, kind = a.init
        if kind not in KINDS:
            print(f"✗ kind 必须是六类之一：{sorted(KINDS)}", file=sys.stderr)
            return 2
        d = proj / "scenes"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.json"
        if p.exists():
            print(f"✗ {p} 已存在，不覆盖", file=sys.stderr)
            return 2
        p.write_text(json.dumps({
            "scene_id": sid, "kind": kind, "purpose": "",
            "beats": {k: "" for k in BEAT_KEYS},
            "claim_source": {"asset_id": "", "start": 0.0, "end": 0.0, "evidence": ""},
            "audio": {"events": []},
            "review": {"visual": "pending", "audio": "pending", "truth": "pending"},
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {p}（六问答完、claim_source 填上才许写代码）")
        return 0

    tl = json.loads((proj / "timeline.json").read_text())
    try:
        import cc0_lib
        sfx_ids = {r["id"] for r in cc0_lib._rows(cc0_lib.SFX_LIB / "catalog.csv")} \
            if cc0_lib.sfx_available() else set()
    except Exception:
        sfx_ids = set()

    errs, warns, n_checked = [], [], 0
    for el in tl.get("elements") or []:
        if el.get("kind") != "gen":
            continue
        props = el.get("props") or {}
        sid = props.get("scene_spec")
        is_scene = bool(props.get("scene"))   # template: scene（代码叙事场景）
        if not sid:
            if is_scene:
                # 2026-11-14 收紧：叙事场景没有任务单就是硬失败——没有六问、
                # 没有事实依据、没有审核记录的画面不许进无人值守的成片。
                errs.append(f"gen-{el.get('n')} 是叙事场景（props.scene={props.get('scene')!r}）"
                            f"但没有 props.scene_spec——先 scene_spec.py --init 建任务单再开工")
            else:
                warns.append(f"gen-{el.get('n')} 没带 props.scene_spec（信息卡/图表卡无需任务单）")
            continue
        spec = load(proj, sid)
        if spec is None:
            errs.append(f"gen-{el.get('n')} 引用的 scenes/{sid}.json 不存在")
            continue
        n_checked += 1
        dur = float(el.get("dur") or (el.get("out", 0) - el.get("in", 0)))
        errs += check_one(spec, el, dur, sfx_ids)
        unsigned = review_signed(spec)
        if unsigned:
            errs.append(f"gen-{el.get('n')} 的任务单 {sid}.json 审核未签收："
                        f"{','.join(unsigned)} 还是 pending——先跑 scene_review.py 拿到读数、"
                        f"由审核工位把 review.{unsigned[0]} 改成 {sorted(REVIEW_OK)[0]} 才许渲")

    for w in warns:
        print(f"  ! {w}")
    for e in errs:
        print(f"  ✗ {e}")
    print(f"场景任务单：{n_checked} 份已校验 · error {len(errs)} · warning {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
