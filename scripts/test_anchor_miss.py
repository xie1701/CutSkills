#!/usr/bin/env python3
"""锚词对不上时的合同测试（T-034）：**不许静默回落**。

T-027 的负样本 B 干过这件事：卡上 `on` 写一个窗口里根本没有的词，渲器只往 stderr
打一句「大字对不上原话，沿用默认时机」，然后照画——照样写下 `anchor_word` 和一个
lag 3880ms 的 `anchor_t`。于是「声音在前」判据一路绿灯，而卡落在一句和它无关的话上。
「有锚词、且卡比锚词晚」这两条都满足了，唯独没人问过**锚词是不是真的存在**。

这里钉五件事：

  1. `find_anchor()` 窗口内找得到 → 返回下标；找不到 → 返回 `None`（不是 0、不是 -1）；
  2. 窗口外的词即使全片存在，也不算这一格的锚词（时间必须落在这一格的音频窗口里，
     不同素材的绝对时间是各自录制的）；
  3. `card_windows()` 遇到对不上的 `on` **记进 `ANCHOR_MISS`**，不再悄悄沿用默认时机；
  4. `check_timing` 对「锚词不在该格词级真值里」判 **error**（负样本 B 从 0 error 变 error）；
  5. 真锚词零误报：卡照常落在「锚词发音 + CARD_LAG」上，判据 0 error；
  6. 老渲器写的卡没有 `seg`：按卡的**成片时间**落格（不是一律退回第一格，
     否则老卡的锚词永远在别人的话里找——`naming-01` 三张旧卡就这么全报假红）。

不跑 ASR、不碰真素材库，纯合成（秒级）。跑法：`python3 scripts/test_anchor_miss.py`
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_timing as CT  # noqa: E402
import lexicon as LX  # noqa: E402
import render_hf as RH  # noqa: E402

AID = "库/口播_熊工.MOV"
LEGACY = "feedfacecafe"
# 这一段说的是「三分是国产」——卡上的「进口」根本不在里面
SPEECH = [("这", 10.0), ("个", 10.2), ("三", 10.4), ("分", 10.6), ("是", 10.8),
          ("国", 11.0), ("产", 11.2)]
STYLE = {"emphasis": {"hold_s": [1.0, 1.5]},
         "card": {"mg_top_h": 0.21}}


def build_lib(root: Path) -> Path:
    lib = root / "lib"
    (lib / "_cutskills" / "cache" / "asr").mkdir(parents=True, exist_ok=True)
    (lib / "_cutskills" / "index.json").write_text(json.dumps({"assets": [
        {"id": AID, "legacy_key": LEGACY, "duration": 30.0}]}, ensure_ascii=False))
    (lib / "_cutskills" / "cache" / "asr" / f"{LEGACY}-v3c00000.json").write_text(
        json.dumps({"tokens": [t for t, _ in SPEECH],
                    "timestamps": [s for _, s in SPEECH]}, ensure_ascii=False))
    return lib


def a_seg(on: str, kind: str = "keyword", text: str = "进口一定好") -> dict:
    return {"n": 1, "asset": AID, "in": 10.0, "out": 11.6,
            "_start": 0.0, "_dur_q": 1.6,
            "audio": {"asset": AID, "in": 10.0, "out": 11.6},
            "card": {"kind": kind, "text": text, "on": on}}


def project_of(lib: Path, *, anchor_word: str, anchor_t: float, start: float,
               kind: str = "keyword", seg=1) -> Path:
    proj = lib / "_cutskills" / "projects" / "p"
    (proj / "out").mkdir(parents=True, exist_ok=True)
    (proj / "timeline.json").write_text(json.dumps({"duration_s": 2.0, "elements": [{
        "n": 1, "kind": "clip", "track": "video", "asset": AID,
        "in": 10.0, "out": 11.6, "evidence": "这个是国产",
        "audio": {"asset": AID, "in": 10.0, "out": 11.6},
        "card": {"kind": kind, "text": "卡", "on": anchor_word},
    }]}, ensure_ascii=False))
    (proj / "out" / "cards.json").write_text(json.dumps({"cards": [{
        "seg": seg, "kind": kind, "text": "卡", "start": start,
        "end": round(start + 1.5, 3),
        "anchor_word": anchor_word, "anchor_t": anchor_t,
        "lag_ms": round((start - anchor_t) * 1000),
    }]}, ensure_ascii=False))
    return proj


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lib = build_lib(root)
        chars = RH.load_chars(lib, AID)
        chars = LX.fix_items(chars, LX.load(lib / "_cutskills"))

        # ① 窗口里找得到 / 找不到
        j_real = RH.find_anchor(chars, 10.0, 1.6, "国产")
        j_bogus = RH.find_anchor(chars, 10.0, 1.6, "进口")
        ok = j_real is not None and j_bogus is None
        print(f"  {'✓' if ok else '✗'} find_anchor：真词 → {j_real}，"
              f"不存在的词 → {j_bogus}（应是数字 / None）")
        bad += 0 if ok else 1

        # ② 全片存在但不在这一格窗口里的词，同样不算
        j_out = RH.find_anchor(chars, 20.0, 1.6, "国产")
        ok = j_out is None
        print(f"  {'✓' if ok else '✗'} 窗口外的词不算这一格的锚词 → {j_out}（应是 None）")
        bad += 0 if ok else 1

        # ③ 渲器：对不上的 on 记进 ANCHOR_MISS（不是沿用默认时机）
        cache: dict = {}
        cards = RH.card_windows([a_seg("进口")], lib, cache, STYLE)
        miss = list(RH.ANCHOR_MISS)
        ok = bool(miss) and len(cards) == 1
        print(f"  {'✓' if ok else '✗'} 渲器对上不上的锚词记账：{miss[:1] or '空（静默回落了）'}")
        bad += 0 if ok else 1

        # ④ 真锚词：卡落在「锚词发音 + CARD_LAG」，且不记账
        cards = RH.card_windows([a_seg("国产")], lib, cache, STYLE)
        c0 = cards[0] if cards else {}
        want = round(max(0.0 + 0.15, 11.0 - 10.0 + 0.0 + RH.CARD_LAG), 3)
        ok = (not RH.ANCHOR_MISS and abs(float(c0.get("start", -9)) - want) < 0.02
              and abs(float(c0.get("anchor_t", -9)) - 1.0) < 0.02)
        print(f"  {'✓' if ok else '✗'} 真锚词：start={c0.get('start')}（应是 {want}）、"
              f"anchor_t={c0.get('anchor_t')}（应是 1.0）、未记账={not RH.ANCHOR_MISS}")
        bad += 0 if ok else 1

        # ⑤ 负样本 B：锚词不在真值里 → check_timing 判 error（改前 0 error）
        p_bad = project_of(lib, anchor_word="进口", anchor_t=0.4, start=4.28)
        msgs = CT.check(p_bad, lib)
        errs = [m for lv, m in msgs if lv == "error"]
        ok = any("不在该格词级真值里" in m for m in errs)
        print(f"  {'✓' if ok else '✗'} 负样本 B（on=「进口」）→ error {len(errs)} 条"
              f"｜{errs[0][:56] if errs else '仍判 0 error（假绿）'}")
        bad += 0 if ok else 1

        # ⑥ 真锚词零误报
        p_ok = project_of(lib, anchor_word="国产", anchor_t=1.0, start=1.15)
        errs = [m for lv, m in CT.check(p_ok, lib) if lv == "error"]
        ok = not errs and not [m for lv, m in CT.check(p_ok, lib) if lv == "warning"]
        print(f"  {'✓' if ok else '✗'} 真锚词零误报 → error {len(errs)} 条"
              f"｜{errs[0][:56] if errs else '干净'}")
        bad += 0 if ok else 1

        # ⑦ 老卡没有 seg：按成片时间落格，锚词找得到、零误报
        p_old = project_of(lib, anchor_word="国产", anchor_t=1.0, start=1.15, seg=None)
        msgs = CT.check(p_old, lib)
        errs = [m for lv, m in msgs if lv == "error"]
        warns = [m for lv, m in msgs if lv == "warning"]
        ok = not errs and not warns
        print(f"  {'✓' if ok else '✗'} 老卡没有 seg（按成片时间落格）→ error {len(errs)} · "
              f"warning {len(warns)}｜{(errs or warns)[:1]}")
        bad += 0 if ok else 1

    n = 7
    print(f"\n锚词对不上判据：{n - bad}/{n} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = main()
    sys.stdout.write(buf.getvalue())
    raise SystemExit(rc)
