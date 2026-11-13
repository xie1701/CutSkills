#!/usr/bin/env python3
"""CutSkills · 叠加态裁判：卡压脸 / 口播整屏暗场 / 重卡撞字幕（原则 B7 + B9）

为什么要有这一道：2026-11-13 第五轮对抗式审查，8 道脚本全 0 error，成片里
「VS 圆章贴在主讲人两眼之间」「200㎡ 把脸压成黑板」「四大天王盖住菜盘上半截」
三处同时存在。根因不是哪一步写错了，而是**没有任何一道判据知道人脸在哪**。
这个脚本补的就是这一条，判三件事：

  ① 每张 MG 卡与窗口内任一帧人脸框的 IoU 必须 < 2%；
  ② `medium: talking` 且人占画面 ≥15% 的格子上出现整屏暗场 → error；
  ③ 重型卡（stat/proportion/day-night/versus/steps/term）时间窗与字幕重叠，
     但 subs.json 的 `suppressed` 里没有这条留痕 → error。
  ④ 额外一条：`cutaway`（整屏纯底切出）只许用在「没有人脸」或「允许短暂切走」的格子上。

三条判据都**自己重算**：人脸来自 `hf/work/faces/*.json`（Vision 原始读数），
人占比来自 `hf/work/pstats/`（人物遮罩），不拿渲染器写在 cards.json 里的结论当证据。
拿不到数据时**判 warning 并说清哪一段没了**，绝不静默放行。

    python3 scripts/check_overlay.py --project <项目> [--json]

退出码：0 = 没有 error；1 = 有 error（render_hf.py 里是 7，两头都拦住）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import card_layout as CL      # noqa: E402  布局器同一份阈值，避免「判据两份」漂移

HEAVY = set(CL.HEAVY_KINDS)
FACE_IOU_MAX = CL.FACE_IOU_MAX
COVER_TALKING_MAX = CL.COVER_TALKING_MAX
CUE_OVERLAP_MIN = 0.15         # 字幕与重卡窗口重叠到这个秒数就算「同屏打架」
FULLSCREEN_AREA = 0.80         # 底衬占屏 ≥80% 视作整屏暗场


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except json.JSONDecodeError:
        return default


def _timeline(proj: Path) -> tuple[dict, list[dict]]:
    for name in ("timeline.json", "edl.json"):
        d = load(proj / name)
        if d:
            return d, list(d.get("elements") or d.get("segments") or [])
    return {}, []


def canvas_of(proj: Path) -> tuple[int, int]:
    tl, _ = _timeline(proj)
    pack = str(tl.get("pack") or tl.get("style_pack") or "").strip()
    for base in ("packs", "styles"):
        p = HERE.parent / base / f"{pack}.yaml"
        if pack and p.exists():
            try:
                import yaml
                d = yaml.safe_load(p.read_text()) or {}
                c = d.get("canvas") or {}
                return int(c.get("width") or 1080), int(c.get("height") or 1920)
            except Exception:                                   # noqa: BLE001
                break
    return 1080, 1920


def seg_windows(segs: list[dict]) -> list[dict]:
    """每格在成片里的绝对区间（长片靠累计时长，不看元素自己写的 in/out）。"""
    out, t = [], 0.0
    for s in segs:
        d = float(s.get("dur") or 0) or max(0.0, float(s.get("out") or 0) - float(s.get("in") or 0))
        if d <= 0:
            continue
        out.append({"n": int(s.get("n") or 0), "medium": str(s.get("medium") or ""),
                    "start": round(t, 3), "end": round(t + d, 3),
                    "card": s.get("card") or None})
        t += d
    return out


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def audit(proj: Path) -> list[tuple[str, str]]:
    proj = Path(proj).expanduser().resolve()
    rows: list[tuple[str, str]] = []
    cards_doc = load(proj / "out" / "cards.json") or {}
    cards = list(cards_doc.get("cards") or [])
    subs = load(proj / "out" / "subs.json") or {}
    cues = list(subs.get("cues") or [])
    sup_raw = subs.get("suppressed")
    if isinstance(sup_raw, list):
        sup = [s for s in sup_raw if isinstance(s, dict)]
        layer_off = bool(subs.get("subtitle_suppressed"))
    else:
        # 老 subs.json 把 suppressed 写成布尔（整层字幕关掉）。不认这一版会误判，
        # 但也不能把「旧字段」当成「重卡退字幕」的留痕——所以分成两件事读。
        sup = []
        layer_off = bool(sup_raw) or bool(subs.get("subtitle_suppressed"))
    W, H = canvas_of(proj)
    _, segs = _timeline(proj)
    wins = seg_windows(segs)

    if not cards:
        return [("info", "没有 MG 卡，叠加态不适用")]

    def cells_over(win, min_cover=0.0):
        out = []
        for w in wins:
            ov = _overlap(win[0], win[1], w["start"], w["end"])
            if ov >= min(min_cover, 0.3) and w["medium"] == "talking":
                out.append((w, ov))
        return out

    n_face_checked = 0
    for c in cards:
        kind = str(c.get("kind") or "")
        if kind not in HEAVY:
            continue                      # 大字走 .kw 层，另有 top_h 规则，不在本判据内
        mg = str(c.get("mg") or f"mg-{c.get('seg')}")
        win = (float(c["start"]), float(c["end"]))
        lay = c.get("layout") or {}
        if not lay or not lay.get("box"):
            rows.append(("warning",
                         f"{mg}（{kind} {win[0]:.1f}–{win[1]:.1f}s）没有叠加态布局——"
                         f"这张卡的位置无可对账（旧渲染器产物？重渲一次再判）"))
            continue
        box = [float(v) for v in lay["box"]]
        mode = str(lay.get("mode") or "")
        refs = lay.get("face_refs") or []

        # ① 压脸：用 Vision 的原始读数重算 IoU，不采信 cards.json 里写的 face_iou
        if mode == "cutaway":
            rows.append(("info", f"{mg} 整屏纯底切出（{win[0]:.1f}–{win[1]:.1f}s）："
                                 f"实拍已被切走，不存在「压在脸上」"))
        else:
            worst, worst_at, data_seen, missing = 0.0, None, False, []
            for r in refs:
                fp = proj / "hf" / "work" / "faces" / (str(r.get("clip")) + ".json")
                faces = load(fp)
                if faces is None:
                    missing.append(str(r.get("clip")))
                    continue
                data_seen = True
                off, dur = float(r.get("offset") or 0.0), float(r.get("dur") or 0.0)
                for f in faces:
                    tt = off + float(f.get("t") or 0.0)
                    if not (win[0] - 0.15 <= tt <= win[1] + 0.15):
                        continue
                    i = CL.iou(box, [float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"])])
                    if i > worst:
                        worst, worst_at = i, tt
            if missing and not data_seen:
                rows.append(("warning",
                             f"{mg} 缺人脸数据（{', '.join(missing)} 没跑过 --faces）——"
                             f"压脸这一条判不了，不当它通过"))
            elif not data_seen:
                rows.append(("warning",
                             f"{mg} 这一张卡没有任何人脸采样数据（face_refs 空）——判不了"))
            else:
                n_face_checked += 1
                if worst >= FACE_IOU_MAX:
                    rows.append(("error",
                                 f"{mg}（{kind} {mode} {win[0]:.1f}–{win[1]:.1f}s）"
                                 f"压脸：与人脸框 IoU {worst * 100:.2f}% ≥ {FACE_IOU_MAX * 100:.0f}%"
                                 f"（{worst_at if worst_at is None else round(worst_at, 2)}s）· "
                                 f"卡框 {[round(v, 3) for v in box]}"))

        # ② 口播格整屏暗场
        plate = [float(v) for v in (lay.get("plate") or [0, 0, 1, 1])]
        full = mode == "cutaway" or (plate[2] * plate[3]) >= FULLSCREEN_AREA
        if full:
            hits = cells_over(win, 0.3)
            if hits:
                best = None
                for w, _ov in hits:
                    cov = _cover(proj, refs, win, W, H) if refs else lay.get("cover")
                    best = (w, cov)
                    if cov is None:
                        rows.append(("warning",
                                     f"{mg} 在口播格 #{w['n']} 上整屏压暗，但人占比量不出来"
                                     f"（缺人物遮罩）——按 ≥15% 保守判"))
                    if cov is None or float(cov) >= COVER_TALKING_MAX:
                        rows.append(("error",
                                     f"{mg}（{kind} {mode}）压在口播格 #{w['n']}"
                                     f"（{w['start']:.1f}–{w['end']:.1f}s，medium: talking，"
                                     f"人占画面 {'量不出' if cov is None else f'{float(cov) * 100:.0f}%'}）"
                                     f"上做整屏暗场——B7 红线：露脸格只许 corner / focus_blur"))
                # ④ cutaway 的入场券
        if mode == "cutaway":
            hit_talk = cells_over(win, 0.3)
            dur = win[1] - win[0]
            if hit_talk and dur > 2.5:
                rows.append(("error",
                             f"{mg}（{kind}）整屏切走 {dur:.1f}s 且横跨口播格"
                             f" #{hit_talk[0][0]['n']}——B7 只允许「没有人脸」或"
                             f"「≤2.5s 且前后有 B-roll 承接」的切走"))

        # ⑤ 压主体（T-042，B10）：**自**己从人物遮罩重算，不采信 cards.json 的 subject_cov。
        # 为什么是 warning 不是 error：卡压在一条腿上，字仍读得出，属于「美术不达标」；
        # 压在脸上才是硬伤（①就是 error）。但美术不达标也必须被看见——
        # 否则「主体占多少从来没人量过」就会再发生一次。
        if mode != "cutaway":
            sub_cov, sub_known, miss = _subject_cov(proj, refs, win, box)
            if not sub_known:
                rows.append(("warning",
                             f"{mg}（{kind} {mode} {win[0]:.1f}–{win[1]:.1f}s）主体占比量不出来"
                             f"（缺 {', '.join(miss) or '人物遮罩'}）——压主体这一条判不了，"
                             f"不当它通过；布局应已降级挂角（cards.json degraded="
                             f"{bool(lay.get('degraded'))}）"))
            elif sub_cov is not None and sub_cov > CL.SUBJ_COVER_MAX:
                rows.append(("warning",
                             f"{mg}（{kind} {mode} {win[0]:.1f}–{win[1]:.1f}s）压主体："
                             f"卡面主体占比 {sub_cov * 100:.0f}% > "
                             f"{CL.SUBJ_COVER_MAX * 100:.0f}%"
                             f"（卡框 {[round(v, 3) for v in box]}）"
                             f"——B10 卡是「浮在主体之上」，不是「盖住主体一半」"))
        if lay.get("degraded"):
            rows.append(("warning",
                         f"{mg} 布局降级（{lay.get('why') or ''}）——"
                         f"降级不是通过：重新选锚词/换卡型/补遮罩都行，就是不许当没看见"))

        # ③ 重卡退字幕（B9）
        if layer_off:
            continue
        for cue in cues:
            ov = _overlap(win[0], win[1], float(cue["start"]), float(cue["end"]))
            if ov < CUE_OVERLAP_MIN:
                continue
            kept = any(str(s.get("card")) == mg
                       and abs(float((s.get("cue") or {}).get("start") or -9)
                               - float(cue["start"])) <= 0.02 for s in sup)
            if not kept:
                rows.append(("error",
                             f"{mg}（{kind} {win[0]:.1f}–{win[1]:.1f}s）与字幕"
                             f"「{cue.get('text')}」（{float(cue['start']):.2f}–"
                             f"{float(cue['end']):.2f}s，重叠 {ov:.2f}s）同屏，"
                             f"且 subs.json 的 suppressed 里没有这条留痕——"
                             f"B9：重卡在场必须退字幕"))

    if not rows:
        rows.append(("info", f"{n_face_checked} 张卡全部通过叠加态判据"))
    return rows


def _cover(proj: Path, refs: list[dict], win, W: int, H: int):
    """人占画面：从人物遮罩重算（不采信渲染器写的数）。"""
    best = None
    for r in refs:
        clip = proj / "hf" / "clips" / (str(r.get("clip")) + ".mp4")
        if not clip.exists():
            continue
        off = float(r.get("offset") or 0.0)
        dur = float(r.get("dur") or 0.0)
        t0 = max(0.0, win[0] - off)
        t1 = min(dur, win[1] - off)
        if t1 <= t0:
            continue
        c = CL.person_cover(proj, clip, t0, t1, W, H)
        if c is not None:
            best = c if best is None else max(best, c)
    return best


def _subject_cov(proj: Path, refs: list[dict], win, box):
    """卡框里的主体占比（自己从人物遮罩重算）。

    返回 (占比 | None, 有没有量到, 缺了哪几段)。
    遮罩目录就用 `card_layout.person_frames` 那一份：**同一个测量器**，
    判据不许自己再写一套（两份实现早晚会漂，T-038 就是这么坏的）。
    """
    frames, missing = [], []
    for r in refs:
        clip = proj / "hf" / "clips" / (str(r.get("clip")) + ".mp4")
        if not clip.exists():
            missing.append(str(r.get("clip")))
            continue
        fr = CL.person_frames(proj, clip)
        if fr is None:
            missing.append(str(r.get("clip")))
            continue
        off = float(r.get("offset") or 0.0)
        dur = float(r.get("dur") or 0.0)
        t0 = max(0.0, win[0] - off)
        t1 = min(dur, win[1] - off)
        frames += [(t, m) for t, m in fr if t0 - 0.05 <= t <= t1 + 0.05]
    if missing and not frames:
        return None, False, missing
    return CL.cover_in(frames, box), True, missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    rows = audit(proj)
    if a.json:
        print(json.dumps([{"level": lv, "msg": m} for lv, m in rows],
                         ensure_ascii=False, indent=1))
    else:
        mark = {"error": "✗", "warning": "!", "info": "·"}
        for lv, m in rows:
            print(f"{mark.get(lv, '·')} {m}")
    n_err = sum(1 for lv, _ in rows if lv == "error")
    n_warn = sum(1 for lv, _ in rows if lv == "warning")
    if not a.json:
        print(f"叠加态校验：error {n_err} · warning {n_warn}")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
