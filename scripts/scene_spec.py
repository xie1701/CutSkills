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
import hashlib
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


def scene_sig(proj: Path, scene_id: str, spec: dict) -> dict:
    """签收指纹：场景源码 SHA1 + 音效事件指纹 + 时间线（dur/marks）指纹。

    签收不再是字符串口号：review 里必须同时存着「签收那一刻」的三样东西。
    代码改一个字节、锚点改一条、时间线改 0.05s，指纹就不再吻合，签收自动失效、
    场景回到待审（评审 G-04 + §12.2 反证⑤）。

    源码缺失的口径：**构造型场景（时间轴上真有 props.scene 在画它）源码被删不是
    「没这回事」**——记 `scene_sha1="missing"` 让签收失效；纯信息卡从来就没有代码，
    不记这一项（否则每个信息卡都会飙一条假过期）。2026-11-15 对抗审查 P2：
    原来删掉已签收的 .py，签收会静默消失、还能被同名内置场景顶替。
    """
    rv = spec.get("review") or {}
    src = proj / "scenes" / f"{scene_id}.py"
    out: dict = {}
    is_drawn = bool(rv.get("scene_sha1"))     # 曾经签过代码 = 本来就该有代码
    el = None
    try:
        tl = json.loads((proj / "timeline.json").read_text())
        el = next((e for e in tl.get("elements") or []
                   if (e.get("props") or {}).get("scene_spec") == scene_id
                   or (e.get("props") or {}).get("scene") == scene_id), None)
        if el is not None and (el.get("props") or {}).get("scene"):
            is_drawn = True
    except (OSError, ValueError, TypeError):
        pass
    if src.exists():
        try:
            out["scene_sha1"] = hashlib.sha1(src.read_bytes()).hexdigest()[:12]
        except OSError:
            out["scene_sha1"] = "unreadable"
    elif is_drawn:
        out["scene_sha1"] = "missing"
    try:
        payload = json.dumps((spec.get("audio") or {}).get("events") or [],
                             ensure_ascii=False, sort_keys=True)
        out["events_sha1"] = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    except (TypeError, ValueError):
        out["events_sha1"] = "unreadable"
    if el is not None:
        try:
            tlp = json.dumps({"dur": el.get("dur"),
                              "marks": el.get("marks")
                              or (el.get("props") or {}).get("marks") or ""},
                             ensure_ascii=False, sort_keys=True)
            out["tl_sha1"] = hashlib.sha1(tlp.encode("utf-8")).hexdigest()[:12]
        except (TypeError, ValueError):
            pass
    return out


def sig_stale(sig: dict, spec: dict) -> list[str]:
    """返回签收过期的原因清单（空 = 指纹吻合）。三项全签 + 指纹吻合才算有效签收。"""
    rv = spec.get("review") or {}
    unsigned = [k for k in ("visual", "audio", "truth")
                if str(rv.get(k) or "").strip().lower() not in REVIEW_OK]
    if unsigned:
        return unsigned
    stale: list[str] = []
    for key, cur in sig.items():
        recorded = str(rv.get(key) or "").strip()
        if not recorded:
            stale.append(f"{key} 没记指纹（用 scene_spec.py --sign 重签）")
        elif recorded != cur:
            stale.append(f"{key} 已过期（签收时 {recorded[:8]}…，现在是 {cur[:8]}…）")
    return stale


def review_signed(spec: dict) -> list[str]:
    """返回未签收的 review 字段名列表（空列表 = 三项全签了）。

    只看字符串——完整签收校验（含指纹过期，评审 G-04）在 main 里用
    scene_sig / sig_stale 做，因为指纹需要 proj 与 scene_id 才能算。
    """
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
    ap.add_argument("--sign", metavar="SCENE_ID",
                    help="把当前场景代码与音效事件指纹写进任务单 review（签收动作的最后一步）")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()

    if a.sign:
        sid = a.sign
        p = proj / "scenes" / f"{sid}.json"
        if not p.exists():
            print(f"✗ 找不到任务单：{p}", file=sys.stderr)
            return 2
        spec = json.loads(p.read_text())
        unsigned = review_signed(spec)
        if unsigned:
            print(f"✗ {sid}.json 还没签完（{','.join(unsigned)} 是 pending）——"
                  f"先把 review 三项改成 ok 再 --sign 盖指纹", file=sys.stderr)
            return 2
        spec.setdefault("review", {}).update(scene_sig(proj, sid, spec))
        p.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ 已把指纹盖进 {p.name}："
              f"{', '.join(f'{k}={v[:8]}' for k, v in scene_sig(proj, sid, spec).items())}")
        return 0

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
        own_id = str(spec.get("scene_id") or "").strip()
        if own_id and own_id != sid:
            errs.append(f"gen-{el.get('n')} 引用的 scenes/{sid}.json 里 scene_id={own_id!r}"
                        f"——文件名与自述不一致，签收无从谈起")
            continue
        if is_scene and str(props.get("scene") or "") != sid:
            errs.append(f"gen-{el.get('n')} 的 props.scene={props.get('scene')!r} 与 "
                        f"props.scene_spec={sid!r} 不是同一个场景——任务单对错了画面")
            continue
        n_checked += 1
        dur = float(el.get("dur") or (el.get("out", 0) - el.get("in", 0)))
        errs += check_one(spec, el, dur, sfx_ids)
        unsigned = review_signed(spec)
        if unsigned:
            errs.append(f"gen-{el.get('n')} 的任务单 {sid}.json 审核未签收："
                        f"{','.join(unsigned)} 还是 pending——先跑 scene_review.py 拿到读数、"
                        f"由审核工位把 review.{unsigned[0]} 改成 {sorted(REVIEW_OK)[0]} 才许渲")
        else:
            # 评审 G-04：签收必须与场景代码/音效事件指纹绑定，代码改一字节签收即失效。
            stale = sig_stale(scene_sig(proj, sid, spec), spec)
            for s in stale:
                errs.append(f"gen-{el.get('n')} 的任务单 {sid}.json 签收过期：{s}")

    for w in warns:
        print(f"  ! {w}")
    for e in errs:
        print(f"  ✗ {e}")
    print(f"场景任务单：{n_checked} 份已校验 · error {len(errs)} · warning {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
