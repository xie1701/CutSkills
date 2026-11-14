#!/usr/bin/env python3
"""J-cut（BRIEF 剪辑表第 4 步）与「量不到 ≠ 通过」的合同测试。

两件事合在一处，因为 J-cut 必须经过字幕闸，而**字幕闸本身曾经是假绿的**：

乙（更严重）· `timeline._subtitle_damage` 调 `subs.build`，异常被 `except` 吞掉。
    2026-11-15 真发生过一次（`subs.py:939` 的 `NameError`）：字幕量不出来 → 闸返回 [] →
    `_cut_one` 认为「这一刀没有伤到字幕」照样下刀。「切点会不会切碎字幕」这道闸等于不存在。
    现在反过来：量不出来 = 无法判断 = **拒绝**，并把原因写进报错里。

甲 · J-cut：下一句的声音先进 2–4 帧、画面再切。
    ⚠️ **本仓库现状：未实现，也不能只靠 timeline.py 实现。** 这里把拦路的东西钉成测试，
    而不是写一支「假装有 J-cut」的绿灯：

      渲染器的硬不变量（`render_hf.py`，本工位无权修改）：
        · `get_clip` / `extract_own` 把一个元素的**音轨打进片段文件**（`-t dur` + `-shortest`），
          声音长度 == 画面长度；
        · `<video data-start=t data-duration=cl.dur>`，下一个元素的 `t` 由
          `t = round(t + cl["dur"], 3)` 累加 —— 画面推进量 == 该格音频长度；
        · 判据侧的时钟（`check_timing.film_bounds`）也拿 `audio.out - audio.in` 当这一格的
          成片时长。
      三处口径互相咬死：画面边界、声音边界、时钟推进量永远是同一个数。
      而 J-cut 的定义就是「画面边界 ≠ 声音边界」。所以「只把画面尾多留 2–4 帧」
      在现渲器里不是「少改一点」，而是：① 那一格的音轨跟着多出 2–4 帧素材（实测：
      `10月15日.mov` 3766.12–3774.90 那一格，`extract_own` 同形命令 +3 帧 → 片段音轨
      多 4800 个采样 = 100.0ms，sha256 变了），② 后面每一格（含它们的声音）整体后移
      2–4 帧，③ 判据的成片时钟与渲器错位、越往后差越多。
      这组算术就在下面「甲-②」里算成断言，任何人都能重跑。

    「那就按元素重划分（上一格音尾、下一格音头各挪 2 帧）」也不行：实测 86 个真口播格里，
    `audio.in` 到「这一格第一个字起点」的间隔中位 **0.0s**、90.7% 小于 2 帧、最大 0.22s ——
    2–4 帧的重划分必然把**下一句首字的起音**划进上一格（元素墙），而字幕分组是逐元素建的：
    首字会被扒成一条 2–4 帧的闪条，正好撞在 `_subtitle_damage` 的三条闸上。也就是说，
    就算声音能随便动，「改元素划分」这条路的代价比 J-cut 本身还大。

    ★ 2026-11-15 更新：渲器已经把「画面层」和「声音层」解耦了（`render_hf.pic_window`）：
    画面 `data-start = t + lead`、`data-media-start = lead`、声音 `data-start = t`。
    于是上面那组「解耦那天会报红」的渲器探针已经改写成了「解耦后成立的四条」——
    钉的是新事实（画面有自己的起点、声音单独一条且字节不变、时钟仍按声音推进、
    「画面长度 == 声音长度」这条老不变量确实已经不成立），不再是「拦路原因」。
    解耦本身的合同测试在 `scripts/test_av_decouple.py`。

吞异常清单（`test_no_silent_swallow_in_timeline`）：`timeline.py` 里每一个 `except`
都必须留痕（`_blind`）／抛错／判不通过，不许有裸的 `except: pass` / `except: return []`。

    python3 scripts/test_jcut.py
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evidence_guard as EG              # noqa: E402
import libs as libs_mod                   # noqa: E402
import subs                              # noqa: E402
import timeline as TL                     # noqa: E402


def _real_lib_globs() -> list[str]:
    """本机真项目语料的位置：`$CUTSKILLS_REAL_LIBS`（冒号分隔）+ config 里的 libs。

    不写死路径：这是要发给陌生人的包，里面不许出现任何一个人的家目录。
    没配就返回空，测试只跑合成样本并说出口。
    """
    cands: list[str] = []
    env = (os.environ.get("CUTSKILLS_REAL_LIBS") or "").strip()
    if env:
        cands += [p for p in env.split(os.pathsep) if p.strip()]
    try:
        cands += [str(x) for x in (libs_mod.config_libs() or [])]
    except Exception:
        pass
    out: list[str] = []
    for lib in dict.fromkeys(cands):
        f = Path(lib).expanduser()
        if f.is_dir():
            out.append(str(f / "_cutskills" / "projects" / "*" / "timeline.json"))
    return out

FPS = 30
TEXT = "我花咗三百万喺金湾开咗间茶楼"
# 真项目的落点（本机配了素材库就顺带量一遍；没配就只跑合成样本，并且说出口）
# 路径从 `$CUTSKILLS_REAL_LIBS`（冒号分隔）与 config 的 libs 读，不写死任何人的家目录。
PROJECT_GLOBS = _real_lib_globs()


# ---------------------------------------------------------------- 夹具

def make_lib(root: Path) -> Path:
    """造一个最小素材库：一个字表 + 两句 transcript.phrases（subs 认的是 phrases）。"""
    lib = root / "lib"
    (lib / "_cutskills").mkdir(parents=True, exist_ok=True)
    words = [{"word": c, "start": round(i * 0.25, 3)} for i, c in enumerate(TEXT)]
    phrases = [{"text": TEXT[:8], "start": 0.0, "end": 2.0},
               {"text": TEXT[8:], "start": 2.0, "end": 4.0}]
    (lib / "_cutskills" / "index.json").write_text(json.dumps(
        {"assets": [{"id": "talk.mov", "duration": 8.0,
                     "transcript": {"words": words, "phrases": phrases}}]},
        ensure_ascii=False), encoding="utf-8")
    return lib


def clip(n: int, a0: float, a1: float, start: float, aid: str = "talk.mov") -> dict:
    """一格口播：画面、声音同一窗口（这正是渲器的硬不变量）。"""
    ev = "".join(c for i, c in enumerate(TEXT) if a0 - 0.02 <= i * 0.25 < a1 - 0.02)
    return {"n": n, "kind": "clip", "track": "video", "asset": aid,
            "in": round(a0, 3), "out": round(a1, 3), "medium": "talking",
            "source": "library", "why": "测试镜头", "evidence": ev,
            "audio": {"asset": aid, "in": round(a0, 3), "out": round(a1, 3)},
            "_start": start, "_dur_q": round(a1 - a0, 3)}


def chars_of(_aid: str) -> list[dict]:
    return [{"c": c, "s": round(i * 0.25, 3), "e": round(i * 0.25 + 0.25, 3)}
            for i, c in enumerate(TEXT)]


@contextlib.contextmanager
def subs_build_raises(exc: BaseException):
    """让 `subs.build` 抛一次错（monkeypatch，不动 scripts/subs.py）。

    为什么不用「打补丁把函数替换成常量返回」：要证明的是**真抛错**时闸怎么反应，
    所以照真实故障（`NameError: name '_x' is not defined`）的形状抛。
    """
    orig = subs.build

    def boom(*_a, **_k):
        raise exc

    subs.build = boom
    try:
        yield
    finally:
        subs.build = orig


def load_timeline_variant(dst: Path, mutate) -> object:
    """拿**当前** timeline.py 的源码做一个变体模块（改前对照用，别指望手抄一份不漏漂）。"""
    src = (HERE / "timeline.py").read_text(encoding="utf-8")
    new_src, n = mutate(src)
    assert n == 1, f"改前对照：源码手术没命中（命中 {n} 次）—— 测试自己报错，不许静默跳过"
    dst.write_text(new_src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"tl_variant_{dst.stem}", dst)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _unswallow(src: str) -> tuple[str, int]:
    """把 fail-closed 的 `return [...]` 还原成修复前的 `return []`（吞掉）。"""
    return re.subn(r'return \[f"\{BLIND_SUBTITLE\}.*?所以拒绝"\]', "return []", src, flags=re.S)


# ---------------------------------------------------------------- 甲：不变量与算术

def film_clock(elements: list[dict]) -> list[dict]:
    """照 `render_hf.py` 的推进规则算成片时钟：`t += 画面时长`，声音挂在同一格起点上。

    `a_render_len` 是**渲出来**的声音长度：clip 格的声音打进片段文件（`-t dur`），
    所以它 == 画面时长，不是时间轴上写的 `audio.out - audio.in`。
    """
    t, rows = 0.0, []
    for el in elements:
        if (el.get("track") or "video") == "overlay":
            continue
        d = (round(float(el["out"]) - float(el["in"]), 3) if el.get("kind") == "clip"
             else round(float(el.get("dur") or 0.0), 3))
        au = el.get("audio") or {}
        a_len = (round(float(au["out"]) - float(au["in"]), 3)
                 if au.get("out") is not None else None)
        rows.append({"n": el.get("n"), "start": round(t, 3), "pic_dur": d,
                     "a_in": au.get("in"), "a_out": au.get("out"),
                     "a_start": round(t, 3), "a_len": a_len,
                     "a_render_len": d if el.get("kind") == "clip" and a_len is not None
                     else a_len})
        t = round(t + d, 3)
    return rows


def real_timelines() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for g in PROJECT_GLOBS:
        for p in sorted(Path("/").glob(g.lstrip("/"))):
            try:
                out.append((p.parent.name, json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
    return out


# ---------------------------------------------------------------- 主流程

def main() -> int:
    fails: list[str] = []
    notes: list[str] = []

    def must(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✅' if cond else '❌'} {name}" + (f" ({detail})" if detail and not cond else ""))
        if not cond:
            fails.append(name)

    TL._load_audio_map_engine()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        lib = make_lib(tmp)

        # ============================================================ 乙 · 字幕闸
        print("乙-① 真触发：subs.build 抛错 → 字幕闸判**不通过**（不是放行）")
        trial = [clip(1, 0.0, 1.5, 0.0), clip(2, 1.5, 3.0, 1.5)]
        with subs_build_raises(NameError("name '_x' is not defined")):
            dmg = TL._subtitle_damage(trial, lib)
        must("闸返回了东西（不是空 = 不是「没伤到」）", bool(dmg), f"{dmg}")
        must("判成「量不出来」而不是「量过了」", TL._is_blind(dmg), f"{dmg}")
        must("报错说清「无法判断这一刀会不会切碎字幕，所以拒绝」",
             any("无法判断这一刀会不会切碎字幕" in d for d in dmg), f"{dmg}")
        must("报错带上了真实异常类型（NameError）",
             any("NameError" in d for d in dmg), f"{dmg}")

        print("乙-② 改前对照：同一个抛错，把 except 分支还原成 `return []` → 闸放行")
        before = load_timeline_variant(tmp / "_tl_before.py", _unswallow)
        before._load_audio_map_engine()
        with subs_build_raises(NameError("name '_x' is not defined")):
            dmg_before = before._subtitle_damage(trial, lib)
        must("改前：字幕量不出来时返回空 = 判「这一刀没伤到字幕」",
             dmg_before == [], f"{dmg_before}")
        must("改后与改前在这一处行为相反（这就是被修掉的假绿）",
             dmg_before == [] and dmg and TL._is_blind(dmg), "")

        print("乙-③ 调用方语义：闸返回空就真的下刀；闸说「量不出来」就拒绝")
        one = [clip(1, 0.0, 3.0, 0.0)]
        ok_cut = TL._cut_one(one, TL.OPENING_CHAR, 1.3, 1, chars_of=chars_of,
                             motion=lambda _a, _t: 0.0, lib=lib, win=3.0)
        must("好路径：能下刀（闸没冤枉好刀位）", ok_cut is not None, "")
        real_gate = TL._subtitle_damage
        TL._subtitle_damage = lambda *_a, **_k: []           # 模拟「闸吞掉异常后的返回值」
        try:
            cut_if_swallowed = TL._cut_one(one, TL.OPENING_CHAR, 1.3, 1, chars_of=chars_of,
                                          motion=lambda _a, _t: 0.0, lib=lib, win=3.0)
        finally:
            TL._subtitle_damage = real_gate
        must("闸返回空 → _cut_one 直接把刀切下去（改前就是这样放行的）",
             cut_if_swallowed is not None, "")
        TL._subtitle_damage = lambda *_a, **_k: [f"{TL.BLIND_SUBTITLE}（注入）：无法判断"]
        try:
            cut_if_blind = TL._cut_one(one, TL.OPENING_CHAR, 1.3, 1, chars_of=chars_of,
                                       motion=lambda _a, _t: 0.0, lib=lib, win=3.0)
        finally:
            TL._subtitle_damage = real_gate
        must("闸说「量不出来」→ 这个刀位被拒（不是切下去）", cut_if_blind is None, "")

        print("乙-④ 端到端：add_opening_changes 里 subs.build 抛错 → 报 error + 留痕，不出片")
        with subs_build_raises(NameError("name '_x' is not defined")):
            els4, notes4, probs4 = TL.add_opening_changes(
                [clip(1, 0.0, 4.0, 0.0)],
                chars_of=chars_of,
                motion=lambda _a, _t: 0.0, lib=lib)
        errs4 = [m for lv, m in probs4 if lv == "error"]
        warns4 = [m for lv, m in probs4 if lv == "warning"]
        must("报 error（片子出不来，等人来查）", bool(errs4), f"{probs4}")
        must("error 里点明是字幕闸量不出来",
             any("字幕" in m for m in errs4), f"{errs4}")
        must("另有 warning 记下「哪条判据没生效」",
             any(TL.BLIND_SUBTITLE in m for m in warns4), f"{warns4}")
        must("没有偷偷切（元素还是 1 个）", len(els4) == 1, f"{len(els4)} 元素")
        notes.extend(notes4)

        print("乙-⑤ 好路径不回归：量得出来时，真伤要报、干净要放（同一条闸两个方向）")
        clean = [clip(1, 0.0, 1.5, 0.0), clip(2, 1.5, 3.0, 1.5)]
        must("干净的两格 → 闸空（不冤枉）", TL._subtitle_damage(clean, lib) == [],
             f"{TL._subtitle_damage(clean, lib)}")
        cut_bad = [clip(1, 0.0, 0.15, 0.0), clip(2, 0.15, 3.0, 0.15)]
        dmg_bad = TL._subtitle_damage(cut_bad, lib)
        must("一刀切出一个字 → 闸报出来（闪帧/碎条）", bool(dmg_bad), f"{dmg_bad}")
        must("不把「真伤」说成「量不出来」", not TL._is_blind(dmg_bad), f"{dmg_bad}")

        # ============================================================ 甲 · J-cut
        print("甲-① 硬不变量：画面长度 == 音频长度 == 时钟推进量（J-cut 的定义就是打破它）")
        synth = [clip(1, 0.0, 2.0, 0.0), clip(2, 2.0, 4.0, 2.0),
                 clip(3, 4.0, 6.0, 4.0)]
        bad = [r for r in film_clock(synth) if abs(r["pic_dur"] - r["a_len"]) > 1e-6]
        must("合成轴：每一格画面长度 == 它覆盖的音频长度", not bad, f"{bad}")
        real = real_timelines()
        if real:
            n_el = n_bad = 0
            for name, tl in real:
                for el in tl.get("elements") or []:
                    if (el.get("track") or "video") == "overlay" or el.get("kind") != "clip":
                        continue
                    au = el.get("audio") or {}
                    if au.get("out") is None:
                        continue
                    n_el += 1
                    if abs((float(el["out"]) - float(el["in"]))
                           - (float(au["out"]) - float(au["in"]))) > 0.011:
                        n_bad += 1
                        notes.append(f"{name}#{el.get('n')} 画面/音频长度不等")
            must(f"真项目（{len(real)} 支 / {n_el} 格）：没有一格画面长度 ≠ 音频长度",
                 n_bad == 0, f"{n_bad} 格不等 · {notes[-3:]}")
        else:
            print("     （本机没有真项目样本：这一条只跑了合成样本，不假装量过真片）")

        print("甲-② 照 BRIEF 原文「只把画面尾多留 3 帧」在现渲器里会变成什么")
        tail = 3
        d = round(tail / FPS, 3)
        extended = [dict(e) for e in synth]
        extended[0] = dict(extended[0], out=round(float(extended[0]["out"]) + d, 3))
        clock_from = film_clock(synth)
        clock_to = film_clock(extended)
        own_from, own_to = clock_from[0], clock_to[0]
        # 渲器把声音打进片段文件（`-t dur` + `-shortest`）：画面多长，这条音轨就多长。
        # 实测（10月15日.mov，3766.12–3774.90 那一格，extract_own 同形命令）：
        # +3 骤 → 片段音轨 4800 个采样（100.0ms），sha256 变了。
        must(f"① 时间轴上的音频窗口没动（{own_from['a_len']}s → {own_to['a_len']}s），"
             f"但渲出来那条音轨跟着画面长到 {own_to['a_render_len']}s",
             abs(own_to["a_len"] - own_from["a_len"]) < 1e-9
             and abs(own_to["a_render_len"] - (own_from["a_render_len"] + d)) < 1e-6,
             f"{own_from} → {own_to}")
        shifts = [(a["n"], round(b["a_start"] - a["a_start"], 3))
                  for a, b in zip(clock_from[1:], clock_to[1:])]
        must(f"② 后面每一格的起播（含声音）都整体后移 {round(d * 1000)}ms",
             all(abs(s - d) < 1e-6 for _n, s in shifts), f"{shifts}")
        must("③ 片长也跟着长了（判据的成片时钟与渲器口径开始错位）",
             abs((clock_to[-1]["start"] + clock_to[-1]["pic_dur"])
                 - (clock_from[-1]["start"] + clock_from[-1]["pic_dur"]) - d) < 1e-6, "")
        print(f"     读数：延长 {tail} 帧 = {d * 1000:.1f}ms；"
              f"后续各格起播位移 {[s for _n, s in shifts]}；"
              f"这一格渲出来的音轨 {own_from['a_render_len']}s → {own_to['a_render_len']}s")

        print("甲-③ 渲器探针：解耦后成立的四个事实（解耦前这四条里有三条是反的）")
        rh = (HERE / "render_hf.py").read_text(encoding="utf-8")
        import render_hf as RH
        must("① 画面层有自己的起点：`pic_window` 算 data-start / data-media-start，"
             "解耦格的 <video> 是 muted（声音不走它）",
             "def pic_window(" in rh and 'data-media-start="{pm}"' in rh
             and "playsinline muted{extra}" in rh, "")
        must("② 声音层单独一条：`<audio>` 指的就是**同一个片段文件**（`src` 与画面那条同源），"
             "`data-media-start=0`——字节与解耦前那条音轨一模一样，一个采样都没重编",
             '<audio id="a{v["n"]}" src="{v["src"]}"' in rh
             and 'data-duration="{v["dur"]}" data-media-start="0" data-track-index="3"' in rh, "")
        # 曾试过另编一条 `.m4a` 给 <audio>：ADTS 字节 sha256 与旧音轨完全相同，但渲出来
        # 整片声音早 21.4ms ≈ 1024 采样（m4a 容器 edit list 在那条渲染路径上没被认）。
        # 所以这一版改成指回原片段本身：字节相同，且**听出来也相同**（实测 +2.0ms，与解耦前一致）。
        must("③ 时钟仍按**声音**时长累加（画面多留的那几帧不进时钟，片长因此不变）",
             't = round(t + cl["dur"], 3)' in rh, "")
        _p = RH.pic_window(2.0, 1.5, 3, 0, 30)
        must("④ 「画面长度 == 声音长度」这条老不变量**已经不成立**（J-cut 要的就是这个）",
             abs(_p[1] - 1.5) > 1e-6 and abs(_p[0] - 2.0) > 1e-6 and abs(_p[2]) > 1e-6,
             f"pic_window → {_p}")

        # ============================================================ 吞异常清单
        print("吞异常清单：timeline.py 里每个 except 都得留痕/抛错/判不通过")
        src_tl = (HERE / "timeline.py").read_text(encoding="utf-8")
        tree = ast.parse(src_tl)
        handlers: list[tuple[str, int, ast.ExceptHandler]] = []

        def walk(node, fn=""):
            for ch in ast.iter_child_nodes(node):
                name = getattr(ch, "name", fn) if isinstance(
                    ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(ch, ast.ExceptHandler):
                    handlers.append((name, ch.lineno, ch))
                walk(ch, name)

        walk(tree)

        def traced(h: ast.ExceptHandler) -> bool:
            """这个 except 分支算不算「留了痕」：抛错 / `_blind` / 报 error 问题 / 判不通过。"""
            body = ast.get_source_segment(src_tl, h) or ""
            for node in ast.walk(h):
                if isinstance(node, ast.Raise):
                    return True
                if isinstance(node, ast.Call):
                    f = node.func
                    nm = getattr(f, "id", None) or getattr(f, "attr", None)
                    if nm in ("_blind", "SystemExit"):
                        return True
                    if nm == "append" and '"error"' in body:
                        return True
                if isinstance(node, ast.Name) and node.id == "BLIND_SUBTITLE":
                    return True
            return False

        must("timeline.py 里的 except 都扫到了", len(handlers) >= 5, f"{len(handlers)} 处")
        silent = [f"{fn}:{ln}" for fn, ln, h in handlers if not traced(h)]
        must("没有「吞了但不留痕」的 except", not silent, f"{silent}")
        print(f"     {len(handlers)} 处 except："
              + "、".join(sorted({h[0] for h in handlers})))

    print()
    if fails:
        print(f"❌ J-cut / 字幕闸合同测试未通过：{'、'.join(fails)}")
        return 1
    print("✅ J-cut 判据与「量不到 ≠ 通过」合同测试全过")
    print("   注：J-cut 本身（画面层/声音层两个起点）的合同测试在 scripts/test_av_decouple.py；"
          "本文件的 甲-③ 只钉解耦后的渲器事实。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
