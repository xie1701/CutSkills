#!/usr/bin/env python3
"""cue_guard 查表口径的合同测试（T-038）：口令检查到底有没有真在跑。

这个测试锁的是六件事，前四条是 T-038 的原始病灶，后两条是它必须一直能抓住的东西：

  1. 索引里手写的 `transcript.words` **优先**——人校对过的词比 ASR 准；
  2. 没有手写词就回退 ASR 缓存，且**两套键 + 切片偏移**都要认：
     素材改过名（缓存落在 `legacy_key` 下）、长素材切成了 `-v3c00240` 这种片子，
     老实现只认 `h12(id)` 和 `-v3c00000`，于是整库静默 0 命中；
  3. 有词级真值时，窗口中间夹着「三二一」必判 error（闸门本身）；干净窗口不误报；
  4. 没有词级真值**不许静默**：返回空必须带上等级（`sentence` / `none`），
     `--check` 要打出「该格没有词级真值，口令判不了」——空列表不等于「这段干净」；
  5. 键改回来以后必须恢复命中（证明 ④ 的黄是键坏了，不是判据坏了）。

不跑 ASR、不碰真素材库，纯合成（秒级）。跑法：`python3 scripts/test_cue_guard.py`
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cue_guard as CG  # noqa: E402
import evidence_guard as EG  # noqa: E402

AID = "工地素材/改名后_遮蔽保护_IMG_1069.MOV"
LEGACY = "0123456789ab"
# 真景：口令前后都有正话，两端都切不掉——这种才该判 error（trim 只动端点）
SPEECH = [("贴", 250.0), ("分", 250.4), ("色", 250.8),
          ("三", 251.2), ("二", 251.4), ("一", 251.6),
          ("纸", 252.0), ("贴", 252.4)]


def build(lib: Path, *, words: list[dict] | None = None,
          chunks: dict[str, list[tuple[str, float]]] | None = None) -> dict:
    """造一个最小库 + 一个素材记录。`chunks` 的 key 是切片名（如 `-v3c00240`）。"""
    (lib / "_cutskills" / "cache" / "asr").mkdir(parents=True, exist_ok=True)
    rec: dict = {"id": AID, "legacy_key": LEGACY,
                 "transcript": {"phrases": [{"text": "分色纸。", "start": 1.0, "end": 3.0}]}}
    if words is not None:
        rec["transcript"]["words"] = words
    (lib / "_cutskills" / "index.json").write_text(
        json.dumps({"assets": [rec]}, ensure_ascii=False))
    for suffix, toks in (chunks or {}).items():
        (lib / "_cutskills" / "cache" / "asr" / f"{LEGACY}{suffix}.json").write_text(
            json.dumps({"tokens": [t for t, _ in toks],
                        "timestamps": [s for _, s in toks]}, ensure_ascii=False))
    EG._key_map.cache_clear()
    EG._words_of.cache_clear()
    return rec


def record_of(lib: Path) -> dict:
    return json.loads((lib / "_cutskills" / "index.json").read_text())["assets"][0]


def project(lib: Path, a_in: float, a_out: float) -> Path:
    proj = lib / "_cutskills" / "projects" / "p"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "timeline.json").write_text(json.dumps({"duration_s": a_out, "elements": [{
        "n": 1, "kind": "clip", "track": "video", "asset": AID,
        "in": a_in, "out": a_out, "evidence": "分色纸",
        "audio": {"asset": AID, "in": a_in, "out": a_out},
    }]}, ensure_ascii=False))
    return proj


def run_check(proj: Path) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = CG.check(proj)
    return rc, buf.getvalue()


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # ① 手写 transcript.words 优先：缓存里放的是**另一句话**，必须出手写的
        lib1 = root / "c1"
        build(lib1,
              words=[{"t": "三", "start": 1.0}, {"t": "二", "start": 1.2},
                     {"t": "一", "start": 1.4}],
              chunks={"-v3c00000": [("完", 1.0), ("全", 1.2), ("不同", 1.4)]})
        ws, src = CG.words_and_source(record_of(lib1), lib1)
        text = "".join(w["t"] for w in ws)
        cues = CG.find_cues(ws)
        ok = (src == EG.WORD and text == "三二一"
              and [c["kind"] for c in cues] == ["count"])
        print(f"  {'✓' if ok else '✗'} 手写词优先｜source={src}｜词「{text}」｜口令 "
              f"{[(c['kind'], c['t0']) for c in cues]}")
        bad += 0 if ok else 1

        # ② 缓存回退：素材改过名（只认 legacy_key），且切片 -v3c00240 的 240s 偏移要拼回来
        lib2 = root / "c2"
        build(lib2, chunks={"-v3c00240": SPEECH})
        ws, src = CG.words_and_source(record_of(lib2), lib2)
        cues = CG.find_cues(ws)
        t0 = cues[0]["t0"] if cues else -1.0
        ok = (src == EG.WORD and abs(t0 - 491.2) < 0.01
              and [c["kind"] for c in cues] == ["count"])
        print(f"  {'✓' if ok else '✗'} 老键 + 切片偏移｜source={src}｜口令起点 {t0}"
              f"（应是 491.2 = 251.2 + 240s 切片偏移）")
        bad += 0 if ok else 1

        # ③ 有词级真值、口令卡在窗口中间（两端都有正话）→ 必判 error
        rc, out = run_check(project(lib2, 489.5, 493.5))
        ok = rc == 2 and "夹着" in out and "三二一" in out
        print(f"  {'✓' if ok else '✗'} 窗口中间夹口令 → exit={rc}（应是 2=红）｜"
              f"{out.strip().splitlines()[0][:62] if out.strip() else '无输出'}")
        bad += 0 if ok else 1

        # ④ 干净窗口不误报：口令在 491.2，窗口取 495–497
        rc, out = run_check(project(lib2, 495.0, 497.0))
        ok = rc == 0 and "🟢" in out
        print(f"  {'✓' if ok else '✗'} 干净窗口 → exit={rc}（应是 0=绿）｜{out.strip()[:56]}")
        bad += 0 if ok else 1

        # ⑤ 缺真值不许静默：键全改坏 → 等级必须是句级，--check 必须说「判不了」
        real = EG.keys_of
        EG.keys_of = lambda lib_, asset_: ("deadbeefdead",)
        try:
            EG._words_of.cache_clear()
            ws, src = CG.words_and_source(record_of(lib2), lib2)
            rc, out = run_check(project(lib2, 489.5, 493.5))
            said = [ln for ln in out.splitlines() if "没有词级真值，口令判不了" in ln]
            ok = (ws == [] and src == EG.SENTENCE and rc != 0 and bool(said))
            print(f"  {'✓' if ok else '✗'} 缺词级真值 → source={src}，exit={rc}，"
                  f"报「判不了」：{said[0][:52] if said else '静默装绿（假绿）'}")
            bad += 0 if ok else 1
        finally:
            EG.keys_of = real
            EG._words_of.cache_clear()

        # ⑥ 键改回来以后必须恢复命中——证明⑤的黄是键坏了，不是判据坏了
        ws, src = CG.words_and_source(record_of(lib2), lib2)
        ok = src == EG.WORD and len(ws) == len(SPEECH)
        print(f"  {'✓' if ok else '✗'} 键改回 → source={src}，词 {len(ws)} 个"
              f"（应是 {len(SPEECH)}）")
        bad += 0 if ok else 1

    n = 6
    print(f"\n口令守卫查表口径：{n - bad}/{n} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
