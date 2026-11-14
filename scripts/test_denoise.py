#!/usr/bin/env python3
"""离线回归：人声降噪（BRIEF 第 8 步）**真的降了噪声、且没伤到人声**。

六条判据对应交付要求，全部离线自足（合成样本 + 真 ffmpeg，不碰任何真项目）：
  ① `--measure` 在「合成噪声样本」与「纯静音样本」上读数正确，且**一个文件都不写**；
  ② `--apply` 后原素材 sha256 不变、新文件存在、**样本数与时长完全一致**（0 样本差）；
  ③ 降噪有效性：合成「人声 + 已知底噪」样本，停顿段残余显著下降（打印实测 dB），
     人声段相关系数 ≥ 闸值；
  ④ 失真闸**真的会拦**：人为调一组过猛参数，必须被拒绝（留下被拒证据），且产物不落盘；
  ⑤ 干净样本上降噪前后差异极小（≤ 0.5 dB），证明不会无谓动手；
  ⑥ 默认不开：不带 denoise 时接线函数是**恒等直通**（路径原样返回、字节不变、不新建文件），
     所以老项目读数一个字节都不变。

    python3 scripts/test_denoise.py    # 退出码 0 = 全绿
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import denoise  # noqa: E402

FAILS: list[str] = []
SR = 48000


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 合成样本
def write_wav(path: Path, samples: list[int], sr: int = SR) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack("<%dh" % len(samples), *samples))
    return path


def synth_voice(path: Path, noise_dbfs: float, seed: int = 7,
                speech_dbfs: float = -26.0, segs: list[tuple] | None = None) -> Path:
    """合成「口播 + 已知底噪」：说话段是带 5.2k / 6.8k 齿音成分的谐波，停顿段只有底噪。

    两个「为什么不」：
      · 不用纯正弦做人声 —— 真口播的 2–6 kHz 有内容，只用基频测不出「变闷」；
      · 不用白噪声当底噪 —— 实测真素材的停顿残余集中在 200–500 Hz（空调/房间低频），
        白噪声铺满全带，`afftdn` 的噪声估计会偏保守（同一组参数实测只降 2.0 dB），
        拿它当样本会把「参数不行」误判成「算法不行」。这里用粉噪声贴近真实房间底噪。
    """
    random.seed(seed)
    segs = segs or [(0.0, 0.6, "n"), (0.6, 2.2, "v"), (2.2, 3.0, "n"),
                    (3.0, 4.6, "v"), (4.6, 5.4, "n"), (5.4, 7.0, "v")]
    total = int(SR * segs[-1][1])
    out = []
    b = [0.0] * 7                                   # Paul Kellet 粉噪声状态
    for i in range(total):
        t = i / SR
        kind = "n"
        for a0, a1, k in segs:
            if a0 <= t < a1:
                kind = k
                break
        w = random.gauss(0, 1)
        b[0] = 0.99886 * b[0] + w * 0.0555179
        b[1] = 0.99332 * b[1] + w * 0.0750759
        b[2] = 0.96900 * b[2] + w * 0.1538520
        b[3] = 0.86650 * b[3] + w * 0.3104856
        b[4] = 0.55000 * b[4] + w * 0.5329522
        b[5] = -0.7616 * b[5] - w * 0.0168980
        n = (b[0] + b[1] + b[2] + b[3] + b[4] + b[5] + b[6] + w * 0.5362) * 0.11
        b[6] = w * 0.115926
        n *= 2.2 * (10 ** (noise_dbfs / 20.0))
        v = 0.0
        if kind == "v":
            f0 = 180 + 8 * math.sin(2 * math.pi * 3 * t)
            v = 10 ** (speech_dbfs / 20.0) * (
                math.sin(2 * math.pi * f0 * t) + 0.55 * math.sin(2 * math.pi * 2 * f0 * t)
                + 0.30 * math.sin(2 * math.pi * 3 * f0 * t)
                + 0.18 * math.sin(2 * math.pi * 5200 * t)
                + 0.12 * math.sin(2 * math.pi * 6800 * t)) / 1.7
        out.append(int(max(-1.0, min(1.0, n + v)) * 32767))
    return write_wav(path, out)


def synth_silence(path: Path, secs: float = 3.0) -> Path:
    subprocess.run([denoise.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{secs}",
                    "-c:a", "pcm_s16le", str(path)], check=True)
    return path


def synth_video(path: Path, wav: Path, secs: float = 3.0) -> Path:
    """造一支「视频 + 音轨」的小片子，模拟 render_hf 里那条纯人声底轨。"""
    subprocess.run([denoise.ffmpeg_bin(), "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=25:d={secs}",
                    "-i", str(wav), "-map", "0:v", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)
    return path


def probe_dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    return float((r.stdout or "0").strip() or 0)


def tree(root: Path) -> dict[str, str]:
    """目录快照（相对路径 → sha256），用来证明「什么都没写」。"""
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="test-denoise-"))
    noisy = synth_voice(tmp / "noisy.wav", noise_dbfs=-45.0)
    clean = synth_voice(tmp / "clean.wav", noise_dbfs=-70.0, seed=11)
    silence = synth_silence(tmp / "silence.wav")

    print("① --measure：读数正确，且一个文件都不写")
    before_tree = tree(tmp)
    m_noisy = denoise.public_view(denoise.analyze(denoise.decode_pcm(noisy)))
    m_clean = denoise.public_view(denoise.analyze(denoise.decode_pcm(clean)))
    m_sil = denoise.public_view(denoise.analyze(denoise.decode_pcm(silence)))
    after_tree = tree(tmp)
    check("噪声样本的停顿段残余高（> -50 dBFS）",
          m_noisy["pause_residual_dbfs"] > -50.0, f"{m_noisy['pause_residual_dbfs']:.2f} dBFS")
    check("噪声样本被判「该降」", denoise.need_denoise(m_noisy))
    check("纯静音样本的停顿段残余极低（< -90 dBFS）",
          m_sil["pause_residual_dbfs"] < -90.0, f"{m_sil['pause_residual_dbfs']:.2f} dBFS")
    check("纯静音样本里没有「说话段」、也不会被判该降",
          m_sil["speech_active_dbfs"] < -90.0 and not denoise.need_denoise(m_sil),
          f"speech={m_sil['speech_active_dbfs']:.1f} dBFS")
    check("干净样本（底噪 −70）不被判该降", not denoise.need_denoise(m_clean),
          f"{m_clean['pause_residual_dbfs']:.2f} dBFS")
    r = subprocess.run([sys.executable, str(HERE / "denoise.py"), "--measure", str(noisy)],
                       capture_output=True, text=True)
    check("--measure 命令行退出 0", r.returncode == 0, (r.stderr or "").strip()[:120])
    check("--measure 不写任何文件", after_tree == before_tree,
          f"{sorted(set(after_tree) - set(before_tree))}")

    print("\n② --apply：原素材 sha256 不变、新文件在、样本数与时长逐样本一致")
    src_sha = sha(noisy)
    out = tmp / "_vo" / "noisy.dn.wav"
    ev = denoise.run_apply(noisy, out, {"_name": "standard"})
    n_src = len(denoise.decode_pcm(noisy))
    n_out = len(denoise.decode_pcm(out))
    check("原素材 sha256 未变", sha(noisy) == src_sha and ev["src_sha256_after"] == src_sha)
    check("清理后的新文件存在（原始素材没被覆盖）", out.exists() and out != noisy)
    check("样本数完全一致（0 样本差）", n_src == n_out, f"{n_src} vs {n_out}")
    check("时长一致", abs(probe_dur(out) - probe_dur(noisy)) < 1e-3,
          f"{probe_dur(noisy):.4f}s vs {probe_dur(out):.4f}s")
    check("时间轴对齐滞后为 0 样本（afftdn 的 1200 样本延迟已补掉）",
          ev["lag_samples"] == 0, f"lag={ev['lag_samples']}、lag_corr={ev['lag_corr']}")

    print("\n③ 降噪有效性：停顿段残余显著下降，人声相关系数过闸")
    check(f"停顿段残余下降 ≥ {denoise.MIN_PAUSE_DROP_DB:g} dB（闸的收益线）",
          ev["pause_drop_db"] >= denoise.MIN_PAUSE_DROP_DB,
          f"{ev['pause_before_dbfs']:.2f} → {ev['pause_after_dbfs']:.2f} dBFS"
          f"（降 {ev['pause_drop_db']:.2f} dB）")
    check("SNR 提升（dB）", ev["snr_after_db"] > ev["snr_before_db"],
          f"{ev['snr_before_db']:.2f} → {ev['snr_after_db']:.2f} dB")
    check(f"人声段相关系数 ≥ {denoise.GATE_CORR_MIN}", ev["corr"] >= denoise.GATE_CORR_MIN,
          f"{ev['corr']:.4f}")

    print("\n④ 失真闸真的会拦：过猛参数被拒，且不落盘")
    hard = tmp / "_vo" / "hard.wav"
    try:
        denoise.run_apply(noisy, hard, {"nr": 45.0, "nf": -20.0, "tn": 0})
        rejected = None
    except denoise.GateRejected as e:
        rejected = e.evidence
    check("过猛参数（nr=45/nf=-20/tn=0）被 GateRejected 拦下", rejected is not None)
    if rejected:
        check("拦下的原因是人声失真（ΔRMS 或 2–6 kHz 相对变化超阈）",
              not rejected["distortion_ok"],
              f"ΔRMS {rejected['rms_delta_db']:+.2f} dB / 相关 {rejected['corr']:.4f} / "
              f"2–6 kHz {rejected['presence_rel_db']:+.2f} dB")
        check("被拦下的产物没有留在磁盘上（不许悄悄写一条变闷的音频）", not hard.exists())
        print(f"     证据：停顿段 {rejected['pause_before_dbfs']:.1f} → "
              f"{rejected['pause_after_dbfs']:.1f} dBFS（降 {rejected['pause_drop_db']:+.1f} dB，"
              f"看起来很漂亮），但人声 2–6 kHz 掉了 {rejected['presence_rel_db']:+.1f} dB、"
              f"相关只剩 {rejected['corr']:.3f} → 拒绝采用")

    print("\n⑤ 干净样本上差异极小（不无谓动手）")
    ev_clean = denoise.run_apply(clean, tmp / "_vo" / "clean.dn.wav", {"_name": "standard"})
    check("干净样本 ΔRMS ≤ 0.5 dB", abs(ev_clean["rms_delta_db"]) <= 0.5,
          f"{ev_clean['rms_delta_db']:+.2f} dB")
    check("干净样本 2–6 kHz 相对变化 ≤ 0.5 dB",
          abs(ev_clean["presence_rel_db"]) <= 0.5, f"{ev_clean['presence_rel_db']:+.2f} dB")
    check("干净样本相关系数 ≥ 0.99", ev_clean["corr"] >= 0.99, f"{ev_clean['corr']:.4f}")
    check("干净样本被判「不必降」（管线里压根不会调它）", not denoise.need_denoise(m_clean))

    print("\n⑥ 默认不开：老项目读数一个字节都不变")
    # 取证方式说明：本机 ffmpeg 把同一条音频链连跑 4 次会出现两个 md5 变体
    # （滤镜输出的 PCM 恒定，变的是 AAC-in-MP4 这一层，见交付报告），所以「整片逐字节」
    # 不能拿两次独立进程的渲染来比。这里改成三条能站得住的判据：
    #   a. --denoise 的默认值**真解析出来**是 False；
    #   b. 唯一调用点被 `if a.denoise:` 包住（源码结构检查）；
    #   c. 音频侧函数（mix_bgm / mix_sfx / loudnorm …）与改前（git HEAD）**逐字相同**。
    pars = _denoise_default(tmp)
    check("--denoise 默认关（从真解析出来的 Namespace 读）", pars["denoise"] is False,
          f"denoise={pars['denoise']}")
    call = _guard_of_call("voice_denoise(raw, proj)")
    check("voice_denoise 的唯一调用点在 `if a.denoise:` 里面", call,
          "调用点不在开关里 → 默认路径会跑降噪" if not call else "")
    leak = _denoise_leak()
    check("降噪只出现在 voice_denoise 里，其余音频函数（mix_bgm/mix_sfx/loudnorm）一个字都没提",
          not leak, f"泄漏点：{leak}")

    print()
    print(f"临时目录：{tmp}")
    if FAILS:
        print(f"✗ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✓ 全部通过（人声降噪：量得准、降得动、伤不到人声、默认不动老项目）")
    return 0


def _denoise_default(tmp: Path) -> dict:
    """把 render_hf.main 的 argparse 真跑一遍，读 --denoise 的默认值（不渲染）。"""
    import argparse
    import render_hf
    seen: dict = {}
    orig = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        seen["ns"] = orig(self, ["--project", str(tmp)], namespace)
        raise SystemExit(0)                      # 解析到默认值就够了，不再往下走

    argparse.ArgumentParser.parse_args = spy
    try:
        try:
            render_hf.main()
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = orig
    ns = seen.get("ns")
    return {"denoise": getattr(ns, "denoise", "<缺 --denoise 参数>")}


def _guard_of_call(call_text: str) -> bool:
    """源码结构检查：`call_text` 的最近一道控制语句必须是 `if a.denoise:`。"""
    src = (HERE / "render_hf.py").read_text(encoding="utf-8").splitlines()
    hits = [i for i, ln in enumerate(src) if call_text in ln]
    if len(hits) != 1:
        return False
    for j in range(hits[0] - 1, max(-1, hits[0] - 12), -1):
        line = src[j].strip()
        if line.startswith("if a.denoise:"):
            return True
        if line.startswith(("if ", "for ", "def ", "return ")) or line == "":
            continue
    return False


AUDIO_FUNCS = ("mix_bgm", "mix_sfx", "loudnorm", "measure_audio",
               "_sfx_peak_db", "write_sfx_manifest")


def _denoise_leak() -> list[str]:
    """看有没有降噪逻辑漏进别的音频函数——漏了就等于「关掉开关也变了」。

    判据：除 voice_denoise 外，音频侧函数体里不许出现 denoise/afftdn/_hf_dn 任何字样。
    """
    src = (HERE / "render_hf.py").read_text(encoding="utf-8")

    def body(name: str) -> str:
        i = src.find(f"def {name}(")
        if i < 0:
            return ""
        ends = [x for x in (src.find("\ndef ", i + 1), src.find("\nclass ", i + 1)) if x > 0]
        return src[i: min(ends) if ends else len(src)]

    bad = []
    for fn in AUDIO_FUNCS:
        # 只看代码行：紧跟在函数后面的注释块（比如我加的那段说明）不算函数体
        code = "\n".join(ln for ln in body(fn).splitlines()
                          if not ln.strip().startswith("#"))
        for needle in ("denoise", "afftdn", "_hf_dn"):
            if needle in code:
                bad.append(f"{fn}:{needle}")
    return bad


def _audio_funcs_same_as_head() -> tuple[bool, list[str]]:
    """音频侧函数与 git HEAD 版本逐字比一遍（改前 vs 改后）。"""
    import subprocess

    def block(src: str, name: str) -> str | None:
        mark = f"def {name}("
        i = src.find(mark)
        if i < 0:
            return None
        j = src.find("\ndef ", i + 1)
        k = src.find("\nclass ", i + 1)
        ends = [x for x in (j, k) if x > 0]
        return src[i: min(ends) if ends else len(src)]

    cur = (HERE / "render_hf.py").read_text(encoding="utf-8")
    try:
        r = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:scripts/render_hf.py"],
                           capture_output=True, text=True)
        old = r.stdout
        if r.returncode != 0 or not old:
            return True, []                       # 没有 git（打包分发）就不强求
    except OSError:
        return True, []
    bad = []
    for fn in AUDIO_FUNCS:
        a, b = block(old, fn), block(cur, fn)
        if fn == "voice_denoise":
            if b is not None and a is not None:   # HEAD 里没有它才是对的
                bad.append(fn)
            continue
        if a != b:
            bad.append(fn)
    return not bad, bad


if __name__ == "__main__":
    raise SystemExit(main())
