#!/usr/bin/env python3
"""CutSkills · 一个论点有好几个 take 时，选哪个。

这批素材的现实：同一个论点主播说了 4–8 遍（「我花咗三百万开咗间六百平嘅点赞茶楼」
十二秒里说了四遍），而每一遍的干净程度差得很远。按 ASR 句子边界往下挑，
挑到哪个是运气——欧阳那两条片就是挑到了带「o k o k」和「唔系唔系」的那一版。

所以选 take 不能靠手感，要有可数指标。这个脚本的量法：

  口水（ok / 诶 / 我觉得咧）  每次 −3
  场记口令（三二一 / 再来一次）  每次 −5
  句中空档 > 0.55s             每次 −1
  同句内自重复                 每次 −3
  有声覆盖率                    +2 × 覆盖率（选到片场间隙的分会烂）
  长度在 2.5–6s                 +1

用法：
  pick_take.py --library <素材库> --about 纯手工
  pick_take.py --library <素材库> --about 空间 --top 3 --json
  pick_take.py --library <素材库> --about 纯手工 --for talking   # 要露脸：画面穿帮的 take 直接排除
  pick_take.py --library <素材库> --about 纯手工 --for vo        # 只要声音：画面评级不管，声音 ng 仍排除
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evidence_guard as EG  # noqa: E402
import visual_grade as VG    # noqa: E402  声/画双评级（T-015 · A2）

FILLER_PAT = re.compile(r"(?:\bo\s*k\b|\bok\b|okay|[诶嗯呃]|(?:因为|所以|就)咧|我觉得咧)", re.I)
CUE_PAT = re.compile(r"三[，,、.]*二|再来一次|慢啲|望住镜头|一二三")
GOOD_LEN = (2.5, 6.0)
GAP_BAD = 0.55


def score(ws: list[tuple[str, float]], i: float, o: float) -> tuple[float, dict]:
    txt = "".join(t for t, _ in ws)
    dur = max(0.01, o - i)
    filler = len(FILLER_PAT.findall(txt))
    cue = len(CUE_PAT.findall(txt))
    gaps = sum(1 for k in range(1, len(ws)) if ws[k][1] - ws[k - 1][1] > GAP_BAD)
    # 自重复：同一个 4 字串在窗口里出现两次以上
    core_t = "".join(EG.core(txt)[k][0] if False else "" for k in [])
    plain = "".join(c for c in txt if EG.CJK.match(c))
    self_rep = 0
    for n in (4, 5):
        for k in range(0, max(0, len(plain) - n + 1)):
            if plain.count(plain[k:k + n]) > 1:
                self_rep += 1
                break
        if self_rep:
            break
    spoken = 0.0
    prev = None
    for _, s in ws:
        if prev is not None and s - prev <= 0.40:
            spoken += s - prev
        prev = s
    cov = spoken / dur
    # 对着摄影师说的「你」——这句话不是给观众听的，选它等于把片场对话剪进片
    you = txt.count("你") - txt.count("你哋")
    you_pen = 4 * max(0, you)
    # 尾巴挂着接续词：下一句的起头被切在窗口外
    dangling = 1 if re.search(r"(因为|所以|但系|就系|同埋|仲有)[^。！？]{0,3}$", txt) else 0
    s = (-3 * filler - 5 * cue - 1 * gaps - 3 * self_rep - you_pen - 3 * dangling
         + 2 * min(1.0, cov))
    if GOOD_LEN[0] <= dur <= GOOD_LEN[1]:
        s += 1.0
    return round(s, 1), {"filler": filler, "cue": cue, "gaps": gaps,
                         "self_repeat": self_rep, "coverage": round(cov, 2), "dur": round(dur, 2),
                         "you": max(0, you), "dangling": dangling}


def takes(lib: Path, about: str, purpose: str | None = None) -> list[dict]:
    """用 ASR 的 phrase（句级，自带标点）当候选单位——词级流没有标点，按标点切会碎。

    一个 take 可以是 1 个或 2 个连着说的 phrase：有时候论点本身就是一句，
    有时候要带上下一句才完整（「纯手工系我哋嘅根本」+「所以我哋嘅厨房就够大」）。

    `purpose`（T-015 · A2）：同一句话选两遍的标准不一样。
      - `talking`：露脸用。画面被判 `audio_only`（拿稿、瞟场记、切头切肩）或 `ng`
        的 take 直接排除——分数再高也是穿帮；
      - `vo`：旁白用。画面换成 B-roll 或生成画面，画面评级不参与，但声音 `ng` 仍排除。
      - `None`：老行为，不看评级（旧呼叫方/脚本不受影响）。
    """
    idx = json.loads((lib / "_cutskills" / "index.json").read_text())
    out = []
    for a in idx.get("assets") or []:
        if not str(a.get("id", "")).startswith("口播/"):
            continue
        tr = a.get("transcript")
        if isinstance(tr, str):
            try:
                tr = json.loads(tr)
            except json.JSONDecodeError:
                import ast
                try:
                    tr = ast.literal_eval(tr)
                except Exception:                                  # noqa: BLE001
                    continue
        ph = (tr or {}).get("phrases") or []
        if not ph:
            continue
        ws_all = EG.words_of(lib, a["id"])
        for k, p in enumerate(ph):
            if about not in p["text"]:
                continue
            for span in (1, 2):
                seg = ph[k:k + span]
                if len(seg) < span:
                    break
                txt = "".join(x["text"] for x in seg)
                i, o = max(0.0, seg[0]["start"] - 0.05), seg[-1]["end"] + 0.20
                ws = [w for w in ws_all if i - 0.10 <= w[1] <= o + 0.10]
                if len(ws) < 4:
                    continue
                sc, d = score(ws, i, o)
                rec = {"asset": a["id"], "in": round(i, 2), "out": round(o, 2),
                       "score": sc, "text": txt[:44], **d}
                if purpose:
                    # 评级要按这个 take 自己的窗口算：同一支素材前 10 秒拿稿、后 10 秒干净，
                    # 按整支判会把好转的窗口一起废掉。
                    g = VG.grade_of(lib, a["id"], i, o)
                    rec["video_grade"], rec["audio_grade"] = g["video_grade"], g["audio_grade"]
                    if g["audio_grade"] == "ng":
                        continue
                    if purpose == "talking" and g["video_grade"] in VG.BAD_VIDEO:
                        continue
                out.append(rec)
    best: dict[tuple[str, float], dict] = {}
    for t in out:
        key = (t["asset"], round(t["in"]))
        if key not in best or t["score"] > best[key]["score"]:
            best[key] = t
    return sorted(best.values(), key=lambda x: -x["score"])


def main() -> int:
    ap = argparse.ArgumentParser(description="同一论点的 take 择优")
    ap.add_argument("--library", required=True)
    ap.add_argument("--about", required=True, help="要找的论点关键词")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--for", dest="purpose", choices=("talking", "vo"), default=None,
                    help="talking=挑露脸镜头（排除画面 audio_only/ng）；vo=只取旁白声音（不管画面评级）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    lib = Path(a.library).expanduser().resolve()
    got = takes(lib, a.about, a.purpose)[:a.top]
    if a.json:
        print(json.dumps(got, ensure_ascii=False, indent=1))
        return 0
    if not got:
        if a.purpose == "talking":
            print(f"「{a.about}」没有能露脸的 take（画面都判了 audio_only/ng）——"
                  f"要么换 --for vo 只取声音、画面铺 B-roll，要么重拍")
        else:
            print(f"没有找到含「{a.about}」的 take")
        return 1
    print(f"「{a.about}」的候选 take（{len(got)} 条，按分排序）")
    for k, t in enumerate(got):
        mark = "★" if k == 0 else " "
        grade = f"  画面 {t.get('video_grade') or '未知'}" if a.purpose and not a.json else ""
        print(f" {mark} {t['score']:5.1f}  {t['asset'].split('/')[-1]} {t['in']:.2f}–{t['out']:.2f}"
              f"（{t['dur']:.1f}s） 口水{t['filler']} 口令{t['cue']} 空档{t['gaps']}"
              f" 重复{t['self_repeat']} 有声{t['coverage']:.0%}{grade}")
        print(f"        「{t['text']}」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
