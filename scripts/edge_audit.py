#!/usr/bin/env python3
"""边界审计：每一格的开头 / 结尾 / 中间，有没有「不干净」的东西。

Jerry 2026-11-12 的三条原话对应三件事：
  「包房属于主播自己提示自己的」      → 孤立提示词：一个词说完停一下，然后又从头说一遍
  「仲有开始那一段是摄影师的声音」    → 半句起头 / 尾巴挂着下一句的开头
  「口播声音结束，画面还在演，穿帮」  → 声音停了但画面帧差还在高位

判据全部用词级真值 + 源素材像素，不看 ASR 的整句（它会把两句话粘成一句）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_guard as EG  # noqa: E402
import silence as SI  # noqa: E402


def speech(ws):
    """去掉 ASR 里那些**标点 token**。

    词级真值里「。」「，」是独立 token，还常常挂着这一口气结束几秒后的时间戳。
    不清掉它们的话：① 「鸡@6.24 。@7.26」会被算成 1 秒空档；② 尾巴判断把「。」
    当成一个词，虚词/接续词判据全部错位。这是两个假报的共同源头。
    """
    return [(w, t) for w, t in ws if w.strip() and not EG.PUNCT.fullmatch(w.strip())]

PITCH_GAP = 0.90     # 词后静默超过这个数，再看后面有没有把同一句重说一遍
PITCH_LOOK = 4.0     # 「重说」要在这么多年内出现
PITCH_TOL = 2        # 「贴着重说」两次出现之间最多夹几个字（沿用 ①b 的紧邻口径）
FRAG_BREAK = 0.40    # 一个字跟上一个字隔这么久，就算另起一口气（回溯「一小截」时到此为止）
FRAG_MIN = 2         # 被重说的那一截至少 2 字——单字不判（「的」「了」这种一抓一大把）
# 虚词/语气词：一截里全是这些字就不算「念题」（「大定制的…立面的」重复的是「的」）
FILLER = set("的了吗哦噢呢吧啊嘛呀着得地是就都也还又很太再在么那这咋嗯呃唉喔咯啦哈呗个")
EDGE_TAIL = 0.55     # 结尾静默（和 speech_hygiene 同口径）
EDGE_HEAD = 0.40
TAIL_MOTION = 8.0    # 末段帧差（绝对）
TAIL_RATIO = 2.0     # 末段帧差 / 整格帧差中位（相对）——说话时人本来就在动，
                     # 单看绝对值全片都会报；要看的是「末段突然比平常动得厉害」
_FF = None


def _frag(ws, k: int) -> tuple[int, str]:
    """停住之前「一口气念完的那一小截」（返回 起始下标 + 文本）。

    从 k 往前回溯，碰到 ≥ FRAG_BREAK 的空隙或这一格的窗口开头就停。判据要看的是
    「他先念了什么就停了」——如果念的是整句才停，那就不是念题。
    """
    j = k
    while j > 0 and ws[j][1] - ws[j - 1][1] < FRAG_BREAK:
        j -= 1
    return j, "".join(w for w, _ in ws[j:k + 1])


def _solid(unit: str) -> bool:
    """这一截里有实词才算「念了词」，全是虚词（的/了/是/就…）不算。"""
    return any(c not in FILLER for c in unit)


def prompt_restart(ws, k: int) -> str:
    """停在 ws[k] 之后 ≥ PITCH_GAP，他是不是把刚才那半截**又念了一遍**。

    只认两条，都要「≥2 字且含实词」：
      (i) 又把**开头**念了一遍：先念半句的起头，停一下，再从头念——
          「其实是（停 1.1s）… 其实是边缘一定要用专业的」（先岔一句再回来也算）；
      (ii) 又**贴着重说**收尾词：两次出现之间只隔 ≤PITCH_TOL 字——
          「我们每个包（停）每个包房」。

    两条不判的（都是 2026-11-13 实测的假红）：
      · 并列列举——重复的是**收尾词**且隔得远：「白云石断烧，或者说石灰石断烧」隔 7 字；
        「差到 4 倍，四到 5 倍」隔 3 字；
      · 排比循环——重复的那一截在**停之前就已经说过**（「它该有它该去的地方（停）该有它该有的位置」），
        那是句内的两段并列，不是他从头念题。

    返回被重说的那一截（str）或 ""。
    """
    t = ws[k][1]
    after = "".join(w for w, tt in ws[k + 1:] if tt <= t + PITCH_LOOK)
    start, frag = _frag(ws, k)
    if len(frag) < FRAG_MIN or not after:
        return ""
    before = "".join(w for w, _ in ws[:start])
    # (i) 开头重说：位置不限（先岔一句再回来也算），但必须是这一截的前缀
    for L in range(min(len(frag), len(after)), FRAG_MIN - 1, -1):
        unit = frag[:L]
        pos = after.find(unit)
        if pos < 0 or not _solid(unit):
            continue
        if pos <= PITCH_TOL and unit in before:
            return ""            # 紧跟着重复一句他刚刚说过的话＝排比，不是念题
        return unit
    # (ii) 贴着重说：收尾词在停之后 0–PITCH_TOL 字内又出现（①b 的紧邻口径）
    for L in range(len(frag), FRAG_MIN - 1, -1):
        unit = frag[-L:]
        pos = after.find(unit)
        if 0 <= pos <= PITCH_TOL and _solid(unit) and unit not in before:
            return unit
    return ""


def ffmpeg() -> str:
    global _FF
    if _FF:
        return _FF
    import shutil
    c = Path("~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg").expanduser()
    _FF = str(c) if c.exists() else (shutil.which("ffmpeg") or "ffmpeg")
    return _FF


def _gray(src: Path, t: float, w=160, h=284):
    """抽一帧灰度。**拿不到就返回 b""**，但调用方必须把空字节当「量不出」而不是「没动」。

    可执行文件不存在（dyld 挂了 / brew 把瓶子抽了）时 subprocess 会抛 FileNotFoundError，
    这里一并吞成空字节——让审计层去报「没量」。
    """
    try:
        r = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
                            "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
    except OSError:
        return b""
    return r.stdout or b""


def motion(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    step = 7
    n = len(a) // step
    return sum(abs(a[i] - b[i]) for i in range(0, n * step, step)) / max(1, n)


def audit(proj: Path, lib: Path) -> list[tuple[str, str]]:
    tl = json.loads((proj / "timeline.json").read_text())
    out: list[tuple[str, str]] = []
    for el in tl.get("elements") or []:
        if el.get("track") == "overlay":
            continue
        n = el.get("n")
        a = el.get("audio") or {}
        aid = a.get("asset")
        if not aid:
            continue
        i, o = float(a.get("in") or 0), float(a.get("out") or 0)
        truth = EG.chars_of_asset(lib, aid)
        ws = speech(truth.chars_in(i, o, 0.02))
        if not ws:
            # 量不出词就报「没量」，不许当成「这段干净」——猜错的时候 words_of 全空，
            # 审计会一路绿着骗人（这个坑刚踩过）。
            note = EG.degrade_note(n, truth.source)
            if note:
                out.append(("黄", note + "（这一格的提示词/头尾静默没查）"))
            continue
        # ① 孤立提示词：说完一小截停 ≥0.90s，然后又把这一截念一遍。
        # 「每 个 包 [空 1.2s] 每 个 包 房 …」——前面那个「包」是主播在看提词卡自己念半截，
        # 正式那句从第二个「包」起。
        #
        # 2026-11-13：T-030 接通词级真值后这条第一次真跑，四支片 6 条红里 5 条是假红——
        # 它当时只问「停之前那个词后面还出现过吗」，于是「的」这种虚词、
        # 「断烧」这种并列句的收尾词全被打成缺陷。算法收紧到 prompt_restart()：
        # 被重说的必须是**这一小截的开头**（或贴着 0–2 字重说的收尾），≥2 字且含实词。
        for k in range(len(ws) - 1):
            gap = ws[k + 1][1] - ws[k][1]
            if gap < PITCH_GAP:
                continue
            unit = prompt_restart(ws, k)
            if unit:
                out.append(("红", f"#{n} 孤立提示词：念到「{_frag(ws, k)[1]}」停了 {gap:.2f}s，"
                                    f"又把「{unit}」重说一遍——前面那半截是自己念题，不该进片"))
                break
        # ①b 尾巴挂着下一句的重复：窗口最后 1.4 秒里出现**紧邻**的短语重说
        # （「叉烧包 仲有 你仲有 好多人」——「仲有你仲有」是下一句的开头，被尾巴带进来了）
        #
        # 2026-11-12 夜：头一版只要有 2 字重复就判红，把并列结构全打成缺陷——
        # 「我哋一共有四个门口 楼面两个门口 厨房两个门口」是正经列举，两个「个门口」
        # 中间隔着「楼面」「厨房」（间隔 3 字）。真正的口吃是**贴着**重说：
        # 「仲有 你 仲有」间隔 1 字。所以判据要量「两次出现之间隔着几个字」，
        # 只认 ≤2 字的紧邻重说。
        tail_ws = [w for w, t in ws if t >= o - 1.4]
        tail_txt = "".join(tail_ws)
        for kl in (3, 2):
            hit = None
            for x in range(len(tail_txt) - kl + 1):
                g = tail_txt[x:x + kl]
                pos = tail_txt.find(g, x + 1)
                while pos >= 0:
                    if pos - (x + kl) <= 2:          # 中间只夹 0–2 字＝贴着重说
                        hit = g
                        break
                    pos = tail_txt.find(g, pos + 1)
                if hit:
                    break
            if hit:
                out.append(("红", f"#{n} 尾巴挂着一句重复「{hit}」——窗口没收干净，"
                                  f"下一句的开头被带进来了"))
                break
        # ② 头尾静默：**量**出来的静音，不是词间距。词间距大可能是 ASR 漏了词，
        # 那种情况里声音是有的，提示「没声音」会让编辑去切掉他说的话。
        _head = SI.silent_between(lib / aid, i, ws[0][1]) if ws else 0.0
        _tail = SI.silent_between(lib / aid, ws[-1][1], o) if ws else 0.0
        if _head > EDGE_HEAD:
            out.append(("黄", f"#{n} 开头 {_head:.2f}s 没声音"))
        if _tail > EDGE_TAIL:
            out.append(("黄", f"#{n} 结尾 {_tail:.2f}s 没声音"))
        # ③ 画面拖尾：话停了，画面里人还在做新动作。
        # 单看「末段帧差」会把正常说话全判红（人一直在动），所以拿它跟**这一格自己**的平均动感比。
        vid = lib / (el.get("asset") or "")
        if vid.exists():
            vi, vo = float(el.get("in") or 0), float(el.get("out") or 0)
            span = max(0.4, vo - vi)
            pts = [vi + span * f for f in (0.12, 0.32, 0.52, 0.72, 0.88)]
            frames = [_gray(vid, t) for t in pts]
            # 拿不到帧就是量不出，不是「没动」。这个口子 2026-11-12 摆在那里一整晚：
            # ffmpeg 被 brew 清掉了，_gray 全返回 b""，motion() 对空字节返回 0.0，
            # 「末尾画面突然动起来」这条永不红，审计报 🔴 0 🟡 0。
            if not all(frames):
                out.append(("黄", f"#{n} 抽不出帧（ffmpeg 跑不通？）——画面拖尾这条没量"))
            else:
                base = [motion(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
                base = sorted(base)[len(base) // 2] if base else 0.0
                tail = motion(_gray(vid, max(vi, vo - 0.45)), _gray(vid, max(vi, vo - 0.05)))
                if tail > TAIL_MOTION and tail > max(4.0, TAIL_RATIO * base):
                    out.append(("黄", f"#{n} 末尾画面突然动起来（帧差 {tail:.0f} vs 本格中位 {base:.0f}）"
                                      f"——话都停了，人还在演"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="边界审计：提示词 / 头尾静默 / 画面拖尾")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    if a.library:
        lib = Path(a.library).expanduser().resolve()
    else:
        # 素材库根 = 那个带 _cutskills/index.json 的目录。别用 proj.parent.parent 猜，
        # 猜错的时候 words_of 全返回空，审计会一路绿着骗人（这个坑刚踩过）。
        lib = proj
        for _ in range(5):
            if (lib / "_cutskills" / "index.json").exists():
                break
            lib = lib.parent
    rows = audit(proj, lib)
    red = [r for r in rows if r[0] == "红"]
    for lv, msg in rows:
        print(f"  {'🔴' if lv == '红' else '🟡'} {msg}")
    print(f"\n边界审计：🔴 {len(red)} · 🟡 {len(rows) - len(red)}")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
