#!/usr/bin/env python3
"""BRIEF 自动质检新补齐的几项：每条判据都要**正负样本成对过**。

为什么成对：门禁加判据最常见的两种坏法是「只会变红」和「永远绿」。
只测正样本，判据里写死 `return ("green", ...)` 也能通过；只测负样本，阈值调到 0 也能通过。
所以下面每一组都是同一条判据喂两份数据：一次合规、一次不合规，两边都必须答对。

覆盖：
  ① 静音段 >0.8s    —— 合成 wav 里真塞 1.2s 死气 / 0.4s 换气，走 silence.py 真探针
  ② 平台 UI 遮挡    —— 竖屏安全区求交、项目 JSON 覆盖、横屏不适用
  ③ 前 3 秒画面变化 —— 次数 ≥2 + 每个变化点距音频事件 ≤80ms
  ④ 音画同步         —— BRIEF 的两条硬判据：音视频时长差 ≤1 帧、口型同源偏差 ≤1 帧（红）；
                       sync_check 的 85% 同步率与断章率降为黄（口径不同、未校准），
                       卡片锚词的「只许晚不许早」仍为红
  ⑤ 对标逐秒对比     —— compare_ref 能指出结构差异，自比时一句差异都没有

用法：python3 scripts/test_qc_brief.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "vendor"))
from libs import cutskills_home                    # noqa: E402

_PY_DEPS = cutskills_home() / "py"                 # pillow / numpy（C 扩展，装不进 vendor）
if _PY_DEPS.exists() and str(_PY_DEPS) not in sys.path:
    sys.path.append(str(_PY_DEPS))

import platform_safe as PS                        # noqa: E402
import verify_film as VF                          # noqa: E402
from libs import ffmpeg_bin                        # noqa: E402

W, H = 1080, 1920


def say(ok: bool, name: str, detail: str = "") -> int:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  —— {detail}" if detail and not ok else ""))
    return 0 if ok else 1


def _wav(path: Path, parts: list[tuple[str, float]]) -> None:
    """parts = [(tone|silence, 秒)]，拼成一条 wav。"""
    ff = ffmpeg_bin()
    cmd = [ff, "-y", "-v", "error"]
    for kind, secs in parts:
        if kind == "tone":
            cmd += ["-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate=16000:duration={secs}"]
        else:
            cmd += ["-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={secs}"]
    n = len(parts)
    graph = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------- ① 静音段
def group_silence(td: Path) -> int:
    import silence
    bad = 0
    print("① 静音段（>0.8s 死气 → 红）· 真探针 silence.py")
    loud = td / "gap.wav"
    _wav(loud, [("tone", 1.5), ("silence", 1.2), ("tone", 1.5)])
    runs = silence.runs_strict(loud, 0.0, 4.2) or []
    longest = max((b - a for a, b in runs), default=0.0)
    lv, val, _ = VF.silence_verdict(runs)
    bad += say(lv == "red" and longest > VF.SILENCE_MAX_S,
               "1.2s 死气判红", f"{lv} {val} runs={runs}")
    tight = td / "breath.wav"
    _wav(tight, [("tone", 1.5), ("silence", 0.4), ("tone", 1.5)])
    runs2 = silence.runs_strict(tight, 0.0, 3.4) or []
    lv2, val2, _ = VF.silence_verdict(runs2)
    bad += say(lv2 == "green", "0.4s 换气判绿", f"{lv2} {val2} runs={runs2}")
    # 边界：0.8s 是「超过 0.8 秒」，不是「≥0.8 秒」——0.7 绿、0.9 红
    bad += say(VF.silence_verdict([(1.0, 1.7)])[0] == "green", "0.7s 判绿（边界）")
    bad += say(VF.silence_verdict([(1.0, 1.9)])[0] == "red", "0.9s 判红（边界）")
    bad += say(VF.silence_verdict(None)[0] == "yellow", "量不成不说「过」（None → 黄）")
    return bad


# ---------------------------------------------------------------- ② 平台 UI
def group_platform(td: Path) -> int:
    bad = 0
    print("② 平台 UI 遮挡（字幕/贴纸压住 UI → 红）")
    zs = PS.zones(W, H)
    names = [z["name"] for z in zs]
    bad += say(names == ["顶部", "右侧按钮列", "底部"], "竖屏三块遮挡区", str(names))
    bad += say(zs[0]["box"] == [0, 0, W, 192], "顶部 10% = 192px", str(zs[0]["box"]))
    bad += say(zs[1]["box"][0] == 940 and zs[2]["box"][1] == 1594,
               "右侧 13% 从 940px 起 · 底部 17% 从 1594px 起",
               f"{zs[1]['box']} {zs[2]['box']}")

    top_card = [{"label": "keyword卡「压顶部」", "t0": 1.0, "t1": 2.0,
                 "box": [162.0, 120.0, 756.0, 180.0]}]
    mid_card = [{"label": "keyword卡「中段」", "t0": 1.0, "t1": 2.0,
                 "box": [162.0, 700.0, 756.0, 180.0]}]
    bad += say(VF.platform_verdict(top_card, zs)[0] == "red", "卡压顶部 10% 判红",
               str(VF.platform_verdict(top_card, zs)))
    bad += say(VF.platform_verdict(mid_card, zs)[0] == "green", "卡在画面中段判绿",
               str(VF.platform_verdict(mid_card, zs)))

    # 字幕框：真字宽居中，宽到压上右侧按钮列就要红
    wide = {"cues": [{"start": 0.5, "end": 2.0, "text": "一二三四五六七八九十一二"}],
            "band": [1034, 1554], "size": 200, "font": "思源黑体"}
    narrow = {"cues": [{"start": 0.5, "end": 2.0, "text": "六个字"}],
              "band": [1034, 1554], "size": 60, "font": "思源黑体"}
    wb = PS.sub_boxes(wide, W, H)
    nb = PS.sub_boxes(narrow, W, H)
    bad += say(bool(wb) and VF.platform_verdict(wb, zs)[0] == "red",
               "200px×12 字压右侧按钮列判红", str(VF.platform_verdict(wb, zs)))
    bad += say(bool(nb) and VF.platform_verdict(nb, zs)[0] == "green",
               "60px×3 字居中判绿", str(VF.platform_verdict(nb, zs)))

    # 项目 JSON 覆盖：默认安全区(192px)下判绿的卡，把顶部改成 20% 后该判红
    proj = td / "proj-override"
    proj.mkdir()
    (proj / "platform-safe.json").write_text(json.dumps({"top_h": 0.2}))
    spec, src = PS.load_spec(proj)
    bad += say(abs(spec["top_h"] - 0.2) < 1e-9 and "platform-safe" in src,
               "项目 JSON 覆盖顶部安全区", f"{spec} {src}")
    edge_card = [{"label": "卡", "t0": 0, "t1": 1, "box": [100.0, 300.0, 400.0, 100.0]}]
    bad += say(VF.platform_verdict(edge_card, PS.zones(W, H, spec))[0] == "red",
               "覆盖后同一张卡由绿转红")
    # brief.json 也能覆盖（两个入口同一份语义）
    proj2 = td / "proj-brief"
    proj2.mkdir()
    (proj2 / "brief.json").write_text(json.dumps({"platform_safe": {"bottom_h": 0.3}}))
    spec2, _ = PS.load_spec(proj2)
    bad += say(abs(spec2["bottom_h"] - 0.3) < 1e-9, "brief.json 覆盖底部安全区", str(spec2))

    # 横屏：没有实测口径 → 不适用（黄，不当绿）
    bad += say(PS.zones(1920, 1080) == [], "横屏不给默认安全区")
    bad += say(VF.platform_verdict(mid_card, [])[0] == "yellow", "横屏判黄不判绿")
    # 没有框的卡：判不了，不许当合格
    bad += say(VF.platform_verdict([], zs, ["旧卡"])[0] == "yellow", "没框的卡判黄")
    bad += say(VF.platform_verdict(mid_card, zs, ["旧卡"])[0] == "yellow", "一半没框也判黄")
    return bad


# ---------------------------------------------------------------- ③ 前 3 秒
def group_first_seconds() -> int:
    bad = 0
    print("③ 前 3 秒画面变化（≥2 次 + 对应音频事件 ≤80ms → 红/绿）")
    tl = {"elements": [
        {"n": 1, "kind": "clip", "track": "video", "in": 0, "out": 2.0},
        {"n": 2, "kind": "clip", "track": "video", "in": 0, "out": 2.0},
        {"n": 3, "kind": "gen", "track": "overlay", "at": 1.5, "template": "keyword"},
    ]}
    ch = VF.change_points(tl)
    bad += say(sorted(round(c["t"], 2) for c in ch) == [0.0, 1.5, 2.0]
               and any(c.get("kind") == "overlay" for c in ch),
               "时间轴 → 变化点（硬切 + 卡片入场）", str(ch))

    # 正样本：2 次变化，都贴着句读边界
    good = [{"t": 0.0, "kind": "clip"}, {"t": 1.52, "kind": "overlay"},
            {"t": 2.0, "kind": "clip"}]
    bounds = [0.0, 1.5, 2.0, 4.0]
    bad += say(VF.first_seconds_verdict(good, bounds)[0] == "green",
               "2 次且贴边界判绿", str(VF.first_seconds_verdict(good, bounds)))
    # 负样本 1：只有 1 次变化
    bad += say(VF.first_seconds_verdict([{"t": 0.0}, {"t": 2.0}], bounds)[0] == "red",
               "只有 1 次变化判红")
    # 负样本 2：次数够，但有一个离音频事件 200ms
    off = [{"t": 0.0}, {"t": 1.7}, {"t": 2.0}]
    lv, val, _ = VF.first_seconds_verdict(off, bounds)
    bad += say(lv == "red" and "200ms" in val, "离音频事件 200ms 判红", f"{lv} {val}")
    # 负样本 3：过了 3 秒的不算（4.0s 那个变化不能拿来凑数）
    late = [{"t": 0.0}, {"t": 2.0}, {"t": 4.0}]
    bad += say(VF.first_seconds_verdict(late, bounds)[0] == "red", "4.0s 的变化不算进前 3 秒")
    # 没有音频事件表：次数够也不能说「过」
    bad += say(VF.first_seconds_verdict(good, None)[0] == "yellow",
               "没有 audio-map 判黄（不是绿）")
    # 片头 0s 那一下不是「变化」
    bad += say(VF.audio_event_bounds({"blocks": [{"t": 0.0, "dur": 2.0, "is_filler": False},
                                                 {"t": 2.0, "dur": 1.0, "is_filler": True}]})
               == [0.0, 2.0], "音频事件边界只取非填充块")
    return bad


# ---------------------------------------------------------------- ④ 音画同步
def _sync_project(td: Path, overlay_at: float, *, cards: list | None = None) -> Path:
    p = td / f"sync-{int(overlay_at * 1000)}-{len(cards or [])}"
    p.mkdir(parents=True, exist_ok=True)
    (p / "timeline.json").write_text(json.dumps({"elements": [
        {"n": 1, "kind": "clip", "track": "video", "asset": "a.mp4", "in": 0, "out": 2.0,
         "medium": "talking"},
        {"n": 2, "kind": "clip", "track": "video", "asset": "b.mp4", "in": 0, "out": 2.0,
         "medium": "talking"},
        {"n": 3, "kind": "gen", "track": "overlay", "template": "keyword", "dur": 0.5,
         "at": overlay_at, "props": {}},
    ]}, ensure_ascii=False))
    (p / "audio-map.json").write_text(json.dumps({"blocks": [
        {"id": 1, "t": 0.0, "dur": 2.0, "text": "第一句", "is_filler": False},
        {"id": 2, "t": 2.0, "dur": 2.0, "text": "第二句", "is_filler": False},
    ]}, ensure_ascii=False))
    if cards:
        (p / "out").mkdir(exist_ok=True)
        (p / "out" / "cards.json").write_text(json.dumps({"cards": cards}, ensure_ascii=False))
    return p


def group_sync(td: Path) -> int:
    bad = 0
    print("④ 音画同步的**两条 BRIEF 硬判据** + sync_check 降为黄")
    # ① 音视频总时长差 ≤1 帧（BRIEF 原文）
    lv, val, _ = VF.av_duration_verdict(38.300, 38.300, 30)
    bad += say(lv == "green", "时长差 0ms 判绿", f"{lv} {val}")
    lv, val, _ = VF.av_duration_verdict(45.20, 45.30, 30)
    bad += say(lv == "red", "时长差 100ms（3 帧）判红", f"{lv} {val}")
    bad += say(VF.av_duration_verdict(10.0, 10.0 + 1 / 30, 30)[0] == "green",
               "恰好 1 帧判绿（边界）")
    bad += say(VF.av_duration_verdict(10.0, 10.0 + 2 / 30, 30)[0] == "red",
               "恰好 2 帧判红（边界）")
    bad += say(VF.av_duration_verdict(0, 0, 30)[0] == "yellow", "量不到判黄不判绿")

    # ② 口型/同源偏差 ≤1 帧（BRIEF 原文）：视频切片入点 vs 配音入点
    same = [{"n": 1, "kind": "clip", "asset": "a.mp4", "in": 2.5, "out": 6.0,
             "audio": {"asset": "a.mp4", "in": 2.5, "out": 6.0}}]
    bad += say(VF.mouth_sync_verdict(same, None, 30)[0] == "green", "同源同段判绿")
    shifted = [{"n": 3, "kind": "clip", "asset": "a.mp4", "in": 0.0, "out": 3.5,
                "audio": {"asset": "a.mp4", "in": 2.5, "out": 6.0}}]
    lv, val, _ = VF.mouth_sync_verdict(shifted, None, 30)
    bad += say(lv == "red" and "2.50s" not in val, "画面入点被清零（差 2.5s = 75 帧）判红",
               f"{lv} {val}")
    one_f = [{"n": 1, "kind": "clip", "asset": "a.mp4", "in": 2.0, "out": 6.0,
              "audio": {"asset": "a.mp4", "in": 2.0 + 1 / 30, "out": 6.0}}]
    two_f = [{"n": 1, "kind": "clip", "asset": "a.mp4", "in": 2.0, "out": 6.0,
              "audio": {"asset": "a.mp4", "in": 2.0 + 2 / 30, "out": 6.0}}]
    bad += say(VF.mouth_sync_verdict(one_f, None, 30)[0] == "green", "差 1 帧判绿（边界）")
    bad += say(VF.mouth_sync_verdict(two_f, None, 30)[0] == "red", "差 2 帧判红（边界）")
    broll = [{"n": 1, "kind": "clip", "asset": "b.mp4", "in": 0.0, "out": 3.5,
              "audio": {"asset": "a.mp4", "in": 2.5, "out": 6.0}}]
    lv, _, note = VF.mouth_sync_verdict(broll, None, 30)
    bad += say(lv == "yellow" and "不适用" in note, "全是 B-roll 时判不适用（不硬算）", f"{lv} {note}")
    lv, _, note = VF.mouth_sync_verdict(same, {"duration_s": 9.0}, 30, tl_dur=6.0)
    bad += say("旧轴" in note, "音事件表片长对不上要写出来", note)

    # ③ 音事件表新鲜度：陈旧 → 红（断章率/锚词/前 3 秒对齐都读它，旧表算出来的数是假账）
    lv, val, _ = VF.amap_fresh_verdict({"duration_s": 45.20}, 45.20)
    bad += say(lv == "green", "映射与时间轴同长判绿", f"{lv} {val}")
    lv, val, _ = VF.amap_fresh_verdict({"duration_s": 73.53}, 45.20)
    bad += say(lv == "red" and "作废" in _,
               "陈旧映射（73.53s vs 45.20s）判红且明说读数作废", f"{lv} {val}")
    bad += say(VF.amap_fresh_verdict({"duration_s": 45.25}, 45.20)[0] == "green",
               "尾帧取整级差 50ms 判绿（不误杀）")
    bad += say(VF.amap_fresh_verdict({"duration_s": 45.35}, 45.20)[0] == "green",
               "恰好 150ms（容差边界）判绿")
    bad += say(VF.amap_fresh_verdict({"duration_s": 45.40}, 45.20)[0] == "red",
               "差 200ms 超容差判红（边界）")
    bad += say(VF.amap_fresh_verdict(None, 45.20)[0] == "yellow",
               "没有音事件表判黄不判绿（判不了≠判过了）")

    # ④ sync_check 降级：口径不同、未校准的数只能当黄灯；锚词抢拍保留红
    ok_p = _sync_project(td, 2.04)          # 卡片晚 40ms 进来：同步率 100%
    r = subprocess.run([sys.executable, str(SCRIPTS / "sync_check.py"),
                        "--project", str(ok_p), "--json"], capture_output=True, text=True)
    e, w, concl = __import__("gate").parse_sync(r.stdout or r.stderr, r.returncode)
    bad += say(r.returncode == 0 and e == 0 and w == 0, "对齐的片：error 0 warning 0",
               f"rc={r.returncode} {e}/{w} {concl}")

    bad_p = _sync_project(td, 2.5)          # 卡片晚 500ms：同步率 67%
    r2 = subprocess.run([sys.executable, str(SCRIPTS / "sync_check.py"),
                         "--project", str(bad_p), "--json"], capture_output=True, text=True)
    e2, w2, concl2 = __import__("gate").parse_sync(r2.stdout or r2.stderr, r2.returncode)
    bad += say(e2 == 0 and w2 >= 1 and "未校准" in concl2,
               "同步率不达 85%：只算 warning 并注明未校准", f"error {e2} · warning {w2} · {concl2}")

    # 锚词抢拍（卡比它锚定的词早出现）：有物理依据，必须红
    early_p = _sync_project(td, 2.0, cards=[{"kind": "keyword", "text": "抢拍", "start": 2.0,
                                             "end": 2.5, "anchor_t": 2.3, "anchor_word": "词"}])
    r3 = subprocess.run([sys.executable, str(SCRIPTS / "sync_check.py"),
                         "--project", str(early_p), "--json"], capture_output=True, text=True)
    e3, _w3, concl3 = __import__("gate").parse_sync(r3.stdout or r3.stderr, r3.returncode)
    bad += say(e3 == 1 and "抢拍" in concl3, "卡片抢拍判红（只许晚不许早）", concl3)

    # 接线本身：gate 的检查器列表里必须有它，否则「判据存在但没人跑」照旧发生
    gate_src = (SCRIPTS / "gate.py").read_text(encoding="utf-8")
    bad += say('cli_result("sync_check"' in gate_src, "gate.py 注册了 sync_check 检查器")
    vf_src = (SCRIPTS / "verify_film.py").read_text(encoding="utf-8")
    bad += say('add("音视频时长差（≤1 帧）"' in vf_src and 'add("口型同源（≤1 帧）"' in vf_src,
               "verify_film 两条 BRIEF 硬判据落进报告")
    bad += say('add("音事件表新鲜度"' in vf_src,
               "verify_film 把音事件表新鲜度落进报告且会拦交付")
    bad += say('add("静音段（>0.8s）"' in vf_src and 'add("平台 UI 遮挡"' in vf_src
               and 'add("前 3 秒画面变化"' in vf_src, "verify_film 三条判据都落进报告")
    sel_src = (SCRIPTS / "selftest.py").read_text(encoding="utf-8")
    bad += say('"test_qc_brief.py"' in sel_src, "selftest 注册了本单测")
    return bad


# ---------------------------------------------------------------- ⑤ 对标对比
def _film(path: Path, colors: list[str], secs: float = 2.0) -> None:
    ff = ffmpeg_bin()
    cmd = [ff, "-y", "-v", "error"]
    for c in colors:
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s=320x568:d={secs}:r=30"]
    n = len(colors)
    graph = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def group_compare(td: Path) -> int:
    bad = 0
    print("⑤ 对标逐秒结构对比（compare_ref）")
    import compare_ref as CR
    quick = td / "quick.mp4"
    slow = td / "slow.mp4"
    _film(quick, ["0x1f3b57", "0x3b1f2f", "0x1f3b2a"], 1.0)   # 3s 三镜 = 2 刀（都在前 3 秒）
    _film(slow, ["0x1f3b57"], 3.0)                          # 3s 一镜 = 0 刀
    a = CR.features(quick, None)
    b = CR.features(slow, None)
    bad += say(len(a["cuts"]) == 2 and len(b["cuts"]) == 0,
               "切点检测：三镜 2 刀 / 一镜 0 刀", f"{a['cuts']} {b['cuts']}")
    bad += say(a["first3"] - b["first3"] == 2, "前 3 秒变化数差 2",
               f"{a['first3']} vs {b['first3']}")
    rep = CR.build_report(a, b, ref_name="slow.mp4")
    bad += say("| 切点总数 | 2 | 0 | +2 |" in rep, "合计表算出差值", rep.splitlines()[6])
    bad += say("切点总数" in rep and "逐秒读数" in rep and "差在哪" in rep,
               "报告含合计表 / 逐秒表 / 解释节")
    exp = CR.explain(2, a, b, has_cards=False)
    bad += say(bool(exp) and "刀" in exp, "第 3 秒能指出差异", exp)
    same = CR.build_report(a, CR.features(quick, None), ref_name="self.mp4")
    bad += say("没看出明显差异" in same, "自比时报「没看出差异」")
    # 卡片入场只在本片有：报告里对标那侧必须是「—」，不许编数
    proj = td / "cardproj"
    (proj / "out").mkdir(parents=True, exist_ok=True)
    (proj / "out" / "cards.json").write_text(json.dumps({"cards": [
        {"kind": "keyword", "text": "钩子", "start": 1.2, "end": 2.0, "kw_box_px": [1, 2, 3, 4]}]}))
    c = CR.features(quick, proj)
    bad += say(c["first3"] == 3 and len(c["cards"]) == 1, "本片卡片入场计数计入前 3 秒",
               f"first3={c['first3']} cards={len(c['cards'])}")
    rep2 = CR.build_report(c, CR.features(slow, None), ref_name="slow.mp4")
    bad += say("对标无工程文件" in rep2, "对标卡片层留空并写明原因")
    return bad


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qc_brief_") as td:
        base = Path(td)
        bad = 0
        bad += group_silence(base)
        bad += group_platform(base)
        bad += group_first_seconds()
        bad += group_sync(base)
        bad += group_compare(base)
    print(f"\nBRIEF 自动质检判据：{'全部通过' if not bad else f'{bad} 项不符'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
