#!/usr/bin/env python3
"""T-032 合同测试：拆条按**实测静音**判「换没换一口气」，不是按 ASR 的词起点间隔。

为什么要有这一份：ASR 会把一处停顿记进前一个字的时长里（`qizao-ground-01` 的「有」
占 1.689s，其中 1.06s 没声音）。`subs.py` 原来只看「词起点间隔 ≥0.30s」，于是这种停顿
一个也看不见——三支老片各留了几条「窗口里明明静了 0.5–1.2s、字幕却撑 3.5–4.4s」。
T-029 已经把判据改严（量出停顿就判红），产线必须跟上。

这一份测试两头都钉住（`references/negative-samples/breath-split.md`）：

  ① 0.35s 真停顿（ASR 把它算进前一个字的时长里）→ **必须**在那儿断开；
  ② 0.10s 停顿（同一处、同一句话，只是短了点）→ **不许**断（一口气没断，拆开反而难读）；
  ③ 素材不在本机 → 探针不可用要说出口（stderr 有「静音探针不可用，按词界拆分」），
     并且照旧出字幕（退回词界逻辑，不许崩、不许静默）；
  ④ 量不出音频时**不许**当「没有停顿」：退回旧逻辑，长条该判红还是红。

音是现场合成的（ffmpeg 生成的纯音 + 静音），素材时长/停顿位置都是已知量，
所以断言写的是「第几秒到第几秒必须断开」，不掺真人素材的不确定性。
字级时间戳按 ASR 的坏习惯伪造：把停顿塞进前一个字的时长里。

    python3 scripts/test_breath_split.py
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import subs  # noqa: E402

TEXT = "我们的厨房占了三分"          # 9 个字 = 9 个展示单位，不该被宽度拆开
# 「我们的厨房」|「占了三分」正好落在词边界上（jieba 切在「厨房」之后），
# 假语料也得守这条：真静音落在词中间时，产线会（也应该）宁可不拆。
TOTAL = 3.5                          # 一句话 3.5 秒 > HOLD_MAX(3.2)


def _ffmpeg() -> str:
    c = Path("~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg").expanduser()
    if c.exists():
        return str(c)
    import shutil
    return shutil.which("ffmpeg") or "ffmpeg"


def make_speech(path: Path, gap: tuple[float, float]) -> None:
    """造一条「说话 — 静音 — 说话」的音：说话 440Hz 正弦，静音是数字零。

    停顿位置由 `gap` 给（秒）。用正弦而不是白噪：`silence.py` 拿 90 分位当说话电平，
    纯音的电平稳定，静音段是绝对零，边界清楚，不受编解码抖动影响。
    """
    a, b = gap
    src = (f"aevalsrc='0.25*sin(2*PI*440*t)':s=16000:d={a:.3f}[s1];"
           f"anullsrc=r=16000:cl=mono:d={b - a:.3f}[s2];"
           f"aevalsrc='0.25*sin(2*PI*440*t)':s=16000:d={TOTAL - b:.3f}[s3];"
           f"[s1][s2][s3]concat=n=3:v=0:a=1[out]")
    r = subprocess.run([_ffmpeg(), "-v", "error", "-y", "-filter_complex", src,
                        "-map", "[out]", "-ar", "16000", "-ac", "1", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not path.exists():
        raise SystemExit(f"合不出测试音：{r.stderr[-300:]}")


def asr_chars(gap: tuple[float, float]) -> list[tuple[str, float, float]]:
    """伪造字级时间戳，**把停顿算进前一个字的时长**——ASR 就是这么干的。

    9 个字均分 3.5s，然后第 5 个字的下沿拉到停顿末尾（它「吃掉」了那 0.35s），
    于是词起点之间 gap=0，旧的「词起点间隔 ≥0.30s」永远看不到这处停顿。
    """
    a, b = gap
    step = TOTAL / len(TEXT)
    out = []
    for i, ch in enumerate(TEXT):
        s, e = round(i * step, 3), round((i + 1) * step, 3)
        if i == 4:                       # 第 5 个字跨在停顿上
            s, e = round(4 * step, 3), round(b, 3)
        out.append((ch, s, e))
    return out


def build(lib: Path, gap: tuple[float, float], asset: str = "speech.wav"):
    """走产线同一条 `subs.build()`：不给它任何测试专用的口子。"""
    chars = asr_chars(gap)
    segs = [{"n": 1, "asset": asset, "in": 0.0, "out": TOTAL,
             "_start": 0.0, "_dur_q": TOTAL, "_dur": TOTAL}]
    # 一整句 = 一条 phrase：ASR 不会替你在停顿处切句（它把停顿算进前一个字的时长）。
    # 这样「量不到静音」那一支才有意义——没有真静音，词起点之间也确实没有空档。
    phrases = [{"start": 0.0, "end": TOTAL, "text": TEXT}]
    idx = {asset: {"id": asset, "transcript": {"phrases": phrases}}}

    def fake_chars(_lib, _aid):
        return chars

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        cues = subs.build(segs, idx, {}, lib, fake_chars, colorize=False)
    return cues, err.getvalue()


def test_gap(gap: tuple[float, float], expect: int, label: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td)
        make_speech(lib / "speech.wav", gap)
        cues, _ = build(lib, gap)
        texts = [c["text"] for c in cues]
        assert len(cues) == expect, f"{label}：期望 {expect} 条，实际 {len(texts)} 条 {texts}"
        if expect == 2:
            # 断点必须落在停顿上，且两半都读得通
            assert texts[0] == TEXT[:5] and texts[1] == TEXT[5:], \
                f"{label}：断点不在停顿上，实际 {texts}"
            gap_seen = round(cues[1]["start"] - cues[0]["end"], 3)
            assert gap_seen >= subs.BREATH_MIN - 0.02, \
                f"{label}：两段之间的空档 {gap_seen}s，不该小于一口气门槛"
        else:
            assert texts == [TEXT], f"{label}：不该断开，实际 {texts}"
        print(f"  ✅ {label}：{len(cues)} 条 {texts}")


def test_light_pause_not_split() -> None:
    """0.10s 停顿：不足一口气，不许断（拆开就是「闪一下又闪一下」）。"""
    test_gap((1.9, 2.0), 1, "0.10s 停顿不拆")


def test_real_breath_splits() -> None:
    """0.35s 停顿：真换了一口气，必须在静音处断开。"""
    test_gap((1.9, 2.25), 2, "0.35s 停顿必拆")


def test_probe_unavailable_is_loud() -> None:
    """素材不在本机：退回词界逻辑，但必须**说出来**，不许静默。"""
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td)                      # 里面什么都没有：speech.wav 不存在
        cues, err = build(lib, (1.9, 2.25))
        assert cues and "".join(c["text"] for c in cues) == TEXT, f"缺素材时也要照旧出字幕，实际 {cues}"
        assert subs.LAST_PROBE["missing"], "缺素材时 LAST_PROBE 必须记下来"
        assert "静音探针不可用" in err, f"缺素材时必须在 stderr 报出来，实际 {err!r}"
        assert "按词界拆分" in err, f"要说清退回了哪条逻辑，实际 {err!r}"
        assert len(cues) == 1, f"量不出静音时不许猜停顿，实际 {len(cues)} 条（{cues}）"
        print(f"  ✅ 探针不可用：{cues[0]['text']} 一条 · stderr 已报（不静默）")


def test_cache_reused() -> None:
    """同一格反复拆条不许重抽音轨：第二次量同一个窗口必须命中缓存。"""
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td)
        make_speech(lib / "speech.wav", (1.9, 2.25))
        subs._SIL_MEM.clear()
        r1 = subs.speech_silences(lib / "speech.wav", 0.0, TOTAL)
        assert r1 is not None and r1, f"该音里明明有静音，实际 {r1}"
        # 第二次：把 ffmpeg 换掉。还要能拿到同样的结果，就说明没再抽音轨。
        real = subs.speech_silences

        def boom(*_a, **_k):                # pragma: no cover
            raise AssertionError("缓存没命中：又去请 ffmpeg 了")

        import silence as SI
        old = SI._envelope
        try:
            SI._envelope = boom
            r2 = subs.speech_silences(lib / "speech.wav", 0.0, TOTAL)
        finally:
            SI._envelope = old
        assert r1 == r2, f"缓存命中后读数必须一致，{r1} ≠ {r2}"
        print(f"  ✅ 同一个窗口第二次走缓存：{len(r1)} 段静音，没重抽音轨")


def main() -> int:
    print("▶ 拆条按实测静音的合同测试…")
    test_light_pause_not_split()
    test_real_breath_splits()
    test_probe_unavailable_is_loud()
    test_cache_reused()
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
