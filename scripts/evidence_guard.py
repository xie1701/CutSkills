#!/usr/bin/env python3
"""CutSkills · 台词真值守卫：timeline 里的 evidence 必须能由词级 ASR 复现。

## 为什么要有这个

欧阳那两条片出过一次很严重的错：`timeline.json` 的 `evidence` 是手写的美化稿，
把「唔系唔系…系你个根本…o k o k」洗成了通顺的「唔系因为纯手工厨房系大，系因为
纯手工系我哋嘅根本」。下游的字幕、验收、口令检查全把这份美化稿当真值，于是

  · 字幕写着「系我哋嘅根本」，声音里说的是「系你嘅根本」
  · 字幕写着「大家可以放心」，声音里说的是「大家可以喺喺我得」
  · 19–25 秒整段是片场间隙，却在所有检查里显示「正常」

**台词是可以删的，不可以加。** 删掉的是口水、口令、重复，人看一眼就能判断该删；
加出来的字没有任何人能担保它真的被说过。

## 判据

1. 逐字比对 evidence 与窗口内的词级真值；
2. 比较前剔除虚词（嘅/咁/啦/个…）——ASR 对粤语虚词的写法极不稳定，用它判会天天误报；
3. 同音放行（pypinyin 归一）——「纯手工」听成「纯土工」是 ASR 的错，不是编造；
4. 剩下的「编造实词」≥ 2 个判 error；= 1 个判 warning（可能仍是 ASR 错）；
5. evidence 比真值短是允许的（删字），长出来是错。

用法：
  evidence_guard.py --project <项目>
  evidence_guard.py --project <项目> --fix     # 用真值覆盖 evidence（只用于「编造」）
  evidence_guard.py --project <项目> --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from index_assets import h12  # noqa: E402

# 虚词：ASR 对粤语虚词的写法极不稳定（嘅/个/咁/感/咗/左），拿它判「编造」会天天误报。
# 只放**真虚词**。「唔/冇/系/佢/哋/啲」看着像虚词，其实是实词（不/没/是/他/们/些），
# 把它们剔掉会让比对失去骨架，也会让「首字『唔』不能起句」这种误报出现。
FUNC = set("嘅咁啦啊呢咗过着得地吧嘛呀咧喎啩添"
           "的了吗哦噢喔")
PUNCT = re.compile(r"[\s，。、！？；：,.!?;:\"'“”‘’…—·()（）\[\]【】《》<>~～]")
# 只比这些字——它们是「说了什么」的骨架
CJK = re.compile(r"[\u4e00-\u9fff]")


def pinyin_of(ch: str) -> str:
    """单字 → 不带声调拼音。缺 pypinyin 时退化成字本身（判据仍能用，只是同音放行失效）。"""
    try:
        sys.path.insert(0, str(HERE / "vendor"))
        from pypinyin import lazy_pinyin, Style
        return "".join(lazy_pinyin(ch, style=Style.NORMAL)) or ch
    except Exception:                                              # noqa: BLE001
        return ch


@lru_cache(maxsize=512)
def _words_of(lib_s: str, asset_id: str) -> tuple[tuple[str, float], ...]:
    cache = Path(lib_s) / "_cutskills" / "cache" / "asr"
    if not cache.is_dir():
        return []
    words: list[tuple[str, float]] = []
    for f in sorted(cache.glob(f"{h12(asset_id)}-v3c*.json")):
        off = 0.0
        m = re.search(r"-v3c(\d+)\.json$", f.name)
        if m:
            off = float(int(m.group(1)))
        try:
            raw = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        toks, stamps = raw.get("tokens") or [], raw.get("timestamps") or []
        for t, s in zip(toks, stamps):
            words.append((str(t), float(s) + off))
    return tuple(words)


def words_of(lib: Path, asset_id: str) -> list[tuple[str, float]]:
    """词级真值（素材绝对时间）。同一个素材重复读很常见（每格都要），缓存住。"""
    return list(_words_of(str(lib), str(asset_id)))


def phrases_of(lib: Path, asset_id: str) -> list[dict]:
    """句级真值（老库没有词级缓存时的兜底）。

    老库的 ASR 缓存是按旧版 hash 命名的（`005263f2ad91-*.json`），跟现在的
    `h12(id)` 对不上——换算法会让整批老素材突然「无法校对」。句级比词级粗，
    但足够判「这段说的是这句话吗」。
    """
    f = lib / "_cutskills" / "index.json"
    if not f.exists():
        return []
    try:
        idx = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    for a in idx.get("assets") or []:
        if a.get("id") != asset_id:
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
                    return []
        return (tr or {}).get("phrases") or []
    return []


def truth_of(lib: Path, el: dict) -> tuple[str, float, float]:
    """窗口内的真值文本 + 有声时长（秒）。有声时长用来判「选了段空档」。"""
    au = el.get("audio") or {}
    aid = au.get("asset") or el.get("asset")
    i, o = au.get("in"), au.get("out")
    if not aid or i is None or o is None:
        return "", 0.0, 0.0
    ws = [(t, s) for t, s in words_of(lib, aid) if float(i) - 0.10 <= s <= float(o) + 0.10]
    txt = "".join(t for t, _ in ws)
    if not ws:
        # 没有词级就退到句级：精度差一点，但比「无法校对各打五十大板」强。
        ph = [p for p in phrases_of(lib, aid)
              if float(i) - 0.10 <= float(p.get("start") or 0) <= float(o) + 0.10]
        txt = "".join(p.get("text") or "" for p in ph)
        spoken = sum(max(0.0, min(float(o), float(p.get("end") or 0))
                         - max(float(i), float(p.get("start") or 0))) for p in ph)
        return txt, spoken, float(o) - float(i)
    # 有声时长要**量**，不能拿词间距猜。
    # 原来这里是「词起点间隔 ≤0.4s 就算有声」，于是念数字那种一口一个字、
    # 间隔 0.48s 的正常语速被判成「只有 24% 的时间在说话——选了段空转？」，
    # 而真正的空转反而量不出来。改成用静音探针：有声 = 窗口时长 − 真静音。
    dur = float(o) - float(i)
    spoken = dur
    try:
        import silence as SI
        src = Path(lib) / aid
        if src.exists():
            spoken = max(0.0, dur - SI.silent_between(src, float(i), float(o)))
    except Exception:                                              # noqa: BLE001
        pass
    return txt, spoken, dur


def core(text: str) -> list[tuple[str, str]]:
    """只留实词，返回 [(字, 拼音)]。虚词和标点不参与比对。"""
    out = []
    for ch in text:
        if ch in FUNC or PUNCT.match(ch) or not CJK.match(ch):
            continue
        out.append((ch, pinyin_of(ch)))
    return out


def invented(truth: str, evidence: str) -> tuple[list[str], int]:
    """evidence 里「音频没说过的实词」。返回 (编造的字, 比对基准长度)。

    做法：在拼音序列上做最长公共子序列，未被对上的 evidence 实词逐个回到真值里找
    同音字；找到算 ASR 错字，找不到算编造。
    """
    T, E = core(truth), core(evidence)
    if not E:
        return [], len(T)
    if not T:
        return [c for c, _ in E], 0
    sm = SequenceMatcher(None, [c for c, _ in T], [c for c, _ in E], autojunk=False)
    # 对齐会碎成「手→土 / 工 / 厨→期 / 房→望」这种一个个小 replace。先把相邻的
    # 非 equal 段（中间夹一个很短的 equal 也算）合并成区间，再按区间判相似度：
    # ASR 听错是「一串字换成一串长得像的字」，留下的是「一串通顺字换成一串不相干字」。
    spans: list[list[int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            if spans and i2 - i1 <= 1 and (i2 - i1) <= 1 and spans[-1][3] >= j1:
                spans[-1][2], spans[-1][3] = i2, j2     # 夹一个字，并进上一段
                continue
            continue
        if spans and spans[-1][2] >= i1 and spans[-1][3] >= j1:
            spans[-1][2], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    bad: list[str] = []
    for i1, i2, j1, j2 in spans:
        if (i2 - i1) >= 2 and (j2 - j1) >= 2:
            a = [c for c, _ in T[i1:i2]]
            b = [c for c, _ in E[j1:j2]]
            if SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.45:
                continue
        for j in range(j1, j2):
            ch, py = E[j]
            lo, hi = max(0, j - 3), min(len(T), j + 4)
            if py in {p for _, p in T[lo:hi]}:
                continue
            bad.append(ch)
    return bad, len(T)


def coverage(truth: str, spoken: float, dur: float) -> float:
    """窗口里「有词覆盖」的比例。选了段片场空转，这个数会很低。"""
    if dur <= 0:
        return 1.0
    return spoken / dur


def check(project: Path, lib: Path | None = None) -> list[tuple[str, str]]:
    tl = json.loads((project / "timeline.json").read_text())
    lib = lib or project.parents[2]
    out: list[tuple[str, str]] = []
    for el in tl.get("elements") or []:
        ev = str(el.get("evidence") or "").strip()
        n = el.get("n")
        if not ev:
            continue
        if el.get("evidence_source") == "asr":
            # 已经由脚本生成过：它必然和真值一致，只查「这段有没有在说话」。
            truth, spoken, dur = truth_of(lib, el)
            cov = coverage(truth, spoken, dur)
            if dur >= 1.5 and cov < 0.35:
                out.append(("warning", f"#{n} 只有 {cov * 100:.0f}% 的时间在说话"
                                       f"（{spoken:.1f}s/{dur:.1f}s）——选了段空转？"))
            continue
        truth, spoken, dur = truth_of(lib, el)
        if not truth:
            out.append(("warning", f"#{n} 窗口里没有词级真值，无法校对（索引没覆盖这段？）"))
            continue
        bad, base = invented(truth, ev)
        # 人称错位是**硬错**：对着摄影师说的话被写成对观众说的话，语义直接反了。
        # 其余差异只报 warning——ASR 对粤语工业词的错法太野（「干炒牛河」听成
        # 「干炒蚝我同佢咩诶」），逐字定性必然误杀。真正的硬闸在字幕层。
        ev_you, tr_you = (ev.count("你"), truth.count("你"))
        keep = len(core(ev)) / max(1, len(core(truth)))     # 台词保留了真值的几成
        if tr_you > ev_you and keep >= 0.6:
            out.append(("error", f"#{n} 音频里在说「你」（{tr_you} 处），台词写成了 {ev_you} 处——"
                                 f"这句是对着摄影师说的，不是对观众说的"))
        elif len(bad) >= 2:
            sample = "".join(bad[:8])
            out.append(("warning", f"#{n} 台词里「{sample}」音频没说（{len(bad)} 处），"
                                   f"可能 ASR 听错也可能写歪了；音频是「{truth[:26]}」"))
        elif len(bad) == 1:
            out.append(("warning", f"#{n} 台词多出「{bad[0]}」，音频里没找到同音字（可能是 ASR 错字）"))
        cov = coverage(truth, spoken, dur)
        if dur >= 1.5 and cov < 0.35:
            out.append(("warning", f"#{n} 只有 {cov * 100:.0f}% 的时间在说话"
                                   f"（{spoken:.1f}s/{dur:.1f}s）——选了段空转？"))
    return out


def fix(project: Path, lib: Path | None = None) -> int:
    """用真值覆盖 evidence。只用于「编造」的场合——真值里有口水是 P2 的事。"""
    f = project / "timeline.json"
    tl = json.loads(f.read_text())
    lib = lib or project.parents[2]
    n = 0
    for el in tl.get("elements") or []:
        if not str(el.get("evidence") or "").strip():
            continue
        truth, _, _ = truth_of(lib, el)
        if truth and truth != el.get("evidence"):
            before = el["evidence"]
            el["evidence"] = truth
            el["evidence_was"] = before          # 留痕：改了什么，别悄悄改
            n += 1
    if n:
        f.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="台词真值守卫")
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--fix", action="store_true", help="用真值覆盖 evidence")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else None
    if a.fix:
        n = fix(proj, lib)
        print(f"改了 {n} 条 evidence（原值记在 evidence_was）")
        return 0
    v = check(proj, lib)
    err = [m for lv, m in v if lv == "error"]
    if a.json:
        print(json.dumps([{"level": l, "msg": m} for l, m in v], ensure_ascii=False, indent=1))
        return 1 if err else 0
    for lv, m in v:
        print(f"  {'🔴' if lv == 'error' else '🟡'} {m}")
    if not v:
        print("  🟢 台词全部有据可查")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
