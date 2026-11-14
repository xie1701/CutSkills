#!/usr/bin/env python3
"""J-cut 的**渲器解耦**合同测试：画面层与声音层各有各的起点。

BRIEF 剪辑表第 4 步：「句与句之间默认用 J-cut：下一句的声音先进 2–4 帧，画面再切。」

上一轮查清的死结（`test_jcut.py` 里那四条「渲器探针」钉的就是它）：解耦前 `render_hf.py`
把一个元素的**音轨打进片段文件**（`-t dur` + `-shortest`），画面边界、声音边界、时钟推进
量永远是同一个数。所以「画面晚 3 帧」在那种渲器里只能写成「上一格画面多留 3 帧」，而
实测那会让片段音轨多出 4800 个采样（=100.0ms）。J-cut 在那套结构里做不出来。

解耦之后（本文件验证的就是这件事）：
  · 画面：`data-start = t + lead/fps`、`data-media-start = lead/fps`、
    `data-duration = dur + (lead_next - lead)/fps`（上一镜多留的那几帧从这一格扣回来，
    相邻两格首尾相接：不留空、不重叠、总长不变）；
  · 声音：`data-start = t`、`data-media-start = 0`、`data-duration = dur`——
    与解耦前**逐字节同一条音轨**（同一读窗口、同一组 AAC 参数），一秒不挪。
  · 首格的 lead 恒为 0（不黑屏），末格不写 lead（不截断）。

跑法：
    python3 scripts/test_av_decouple.py            # 离线合同（selftest 挂的就是这一档）
    python3 scripts/test_av_decouple.py --measure <项目> [<项目>…]
                                                  # 真片实测：量成片「声音起点 vs 画面切点」，
                                                  # 结果写进 tests/fixtures/av-decouple/
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import check_timing as CT                      # noqa: E402
import render_hf as RH                         # noqa: E402
import timeline as TL                          # noqa: E402
from libs import ffmpeg_bin, hf_bin            # noqa: E402

FPS = 30
FIX = ROOT / "tests" / "fixtures" / "av-decouple"
REAL_GLOBS = [
    "/Users/jerry/Downloads/新邻舍新素材/_cutskills/projects/*/timeline.json",
    "/Users/jerry/Downloads/OYTest2/**/_cutskills/projects/*/timeline.json",
    "/Users/jerry/Downloads/ouyangTestCut/_cutskills/projects/*/timeline.json",
    "/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/*/timeline.json",
]


# ---------------------------------------------------------------- 小工具

def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


FFPROBE = str(Path(ffmpeg_bin()).with_name("ffprobe"))


def ffprobe_json(path: Path, args: list[str]) -> dict:
    r = sh([FFPROBE, "-v", "error", "-of", "json"] + args + [str(path)])
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def streams(path: Path) -> list[dict]:
    return ffprobe_json(path, ["-show_streams"]).get("streams") or []


def adts_sha(path: Path) -> str:
    """这条文件的音轨**按 ADTS 字节**取 sha256（容器不同、字节相同 → 同一份声音）。"""
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path),
                        "-map", "0:a:0", "-c", "copy", "-f", "adts", "-"],
                       capture_output=True)
    return hashlib.sha256(r.stdout).hexdigest()


def pcm_sha(path: Path) -> str:
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:a:0",
                        "-f", "s16le", "-ac", "2", "-ar", "48000", "-"],
                       capture_output=True)
    return hashlib.sha256(r.stdout).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def html_layers(index_html: Path) -> dict[str, dict[str, str]]:
    """从 index.html 抓每个格子的 `<video id=vN>` 与 `<audio id=aN>` 时序属性。

    只抓属性不解析 DOM：要看的是「哪一层从第几秒开始、从素材第几秒起播、放多久」。
    """
    import re
    txt = index_html.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for tag, key in (("video", "v"), ("audio", "a")):
        for m in re.finditer(rf'<{tag} id="{key}(\d+)"([^>]*)>', txt):
            n, rest = m.group(1), m.group(2)
            attrs = dict(re.findall(r'(\b[a-z-]+)="([^"]*)"', rest))
            src = str(attrs.get("src") or "")
            out[f"{key}{n}"] = {"src": src.rsplit("/", 1)[-1],
                                "start": attrs.get("data-start"),
                                "dur": attrs.get("data-duration"),
                                "media": attrs.get("data-media-start"),
                                "muted": "muted" if re.search(r"\bmuted\b", rest) else ""}
    return out


def audio_seconds(path: Path) -> float:
    for st in streams(path):
        if st.get("codec_type") == "audio":
            return round(float(st.get("duration") or 0.0), 3)
    return 0.0


def make_source(dst: Path, dur: float = 3.0) -> Path:
    """造一支带声音的合成素材（画面在动 + 440Hz 单音），只用来量字节与时长。"""
    r = sh([ffmpeg_bin(), "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={dur}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", str(dst)])
    if r.returncode != 0 or not dst.exists():
        raise SystemExit(f"合成素材失败：{r.stderr[-500:]}")
    return dst


def clip(n: int, a0: float, a1: float, *, medium: str = "talking",
         aid: str = "a.mov", card: dict | None = None) -> dict:
    el = {"n": n, "kind": "clip", "track": "video", "asset": aid,
          "in": round(a0, 3), "out": round(a1, 3), "medium": medium,
          "why": "测试镜头",
          "audio": {"asset": aid, "in": round(a0, 3), "out": round(a1, 3)}}
    if card:
        el["card"] = card
    return el


def film_clock(elements: list[dict]) -> list[tuple[int, float, float]]:
    """(元素号, 声音起点, 时长)：口径与 `check_timing.film_bounds` 同一份。"""
    els = {e["n"]: e for e in elements}
    b = CT.film_bounds(els)
    return [(n, b[n][0], round(b[n][1] - b[n][0], 3)) for n in sorted(b)]


def real_timelines() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    seen: set[str] = set()
    for g in REAL_GLOBS:
        for p in sorted(Path("/").glob(g.lstrip("/"))):
            if str(p) in seen:
                continue
            seen.add(str(p))
            try:
                out.append((p, json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
    return out


# ---------------------------------------------------------------- 主流程

def main() -> int:
    if "--measure" in sys.argv:
        return measure(sys.argv[sys.argv.index("--measure") + 1:])

    fails: list[str] = []

    def must(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    TL._load_audio_map_engine()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================ ① 音频一个采样没动
        print("① 解耦只挪画面：声音那一层还是同一份文件、同一份字节")
        src = make_source(tmp / "src.mp4", 3.0)
        frag = tmp / "frag.mp4"          # 解耦前那一条（画面+声音同长）
        RH.extract_own(src, 0.5, 2.5, frag, W=320, H=240, fps=FPS)
        frag2 = tmp / "frag2.mp4"
        RH.extract_own(src, 0.5, 2.5, frag2, W=320, H=240, fps=FPS)
        must("同一条命令连跑两次，片段逐字节相同（「没变」这件事本身是可量的）",
             file_sha(frag) == file_sha(frag2), f"{file_sha(frag)[:12]} vs {file_sha(frag2)[:12]}")
        pic = tmp / "pic.mp4"            # 解耦后：画面多留 100ms、无音
        RH.extract_own(src, 0.5, 2.5, pic, W=320, H=240, fps=FPS, silent=True, vpad=0.1)
        must("画面这条没有音轨（`-an`；声音层只能来自 `<audio>` 指回的那个片段）",
             not any(s.get("codec_type") == "audio" for s in streams(pic)),
             f"{[s.get('codec_type') for s in streams(pic)]}")
        _vp = [float(s.get("duration") or 0) for s in streams(pic) if s.get("codec_type") == "video"]
        must("画面这条真的多留了 100ms 的画面", bool(_vp) and _vp[0] >= 2.1 - 1e-3,
             f"{_vp[0] if _vp else 0}s")
        must("同一段素材走解耦，声音这条文件（= 解耦前那个片段）音轨没被动过",
             audio_seconds(frag) == 2.0 and adts_sha(frag) == adts_sha(frag2),
             f"{audio_seconds(frag)}s · {adts_sha(frag)[:12]}")

        # 反证：解耦前那种「把片段拉长」的办法，声音会跟着变长（这就是做不了 J-cut 的原因）
        bad = tmp / "bad.mp4"
        RH.extract_own(src, 0.5, 2.5, bad, W=320, H=240, fps=FPS, vpad=0.1)
        must("反证：同一道命令只是把画面拉长 100ms，音轨跟着多出 ~100ms（sha256 也变了）",
             audio_seconds(bad) > 2.0 + 0.05 and adts_sha(bad) != adts_sha(frag),
             f"老办法音轨 {audio_seconds(bad)}s、sha {adts_sha(bad)[:12]}")

        # 老路径的命令行必须逐字符不变（否则老项目会无谓地重切一遍）
        seen_cmd: list[list[str]] = []
        real_run = RH.subprocess.run

        def spy(cmd, *a, **kw):                       # noqa: ANN001
            seen_cmd.append(list(cmd))
            return real_run(cmd, *a, **kw)

        RH.subprocess.run = spy
        try:
            RH.extract_own(src, 0.5, 2.5, tmp / "plain.mp4", W=320, H=240, fps=FPS)
        finally:
            RH.subprocess.run = real_run
        old = [ffmpeg_bin(), "-y", "-v", "error", "-ss", "0.500", "-i", str(src),
               "-t", "2.000", "-vf",
               "scale=320:240:force_original_aspect_ratio=increase:flags=lanczos,"
               "crop=320:240,format=yuv420p,fps=30",
               "-c:v", "libx264", "-preset", "medium", "-crf", "16",
               "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
               "-color_trc", "bt709", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
               "-movflags", "+faststart", str(tmp / "plain.mp4")]
        got = [c for c in seen_cmd if str(c[0]).endswith("ffmpeg")]
        must("不给 vpad/silent 时命令行与解耦前逐字符一致（老项目零改动）",
             bool(got) and got[0] == old, f"{got[0] if got else '没跑到'}")

        # ============================================================ ② 画面偏移=整数帧
        print("② 画面层偏移落在 2–4 帧（整数帧，吸附帧网格）")
        p0, pd, pm = RH.pic_window(10.0, 2.0, 3, 0, FPS)
        must("第 3 帧 → 画面 0.100s 后才起、素材入点 0.100s、时长扣回 0.100s",
             (p0, pd, pm) == (10.1, 1.9, 0.1), f"{(p0, pd, pm)}")
        for f in (2, 3, 4):
            a0, ad, am = RH.pic_window(5.0, 2.0, f, 0, FPS)
            must(f"{f} 帧：画面比声音晚的就是 {f} 帧，不多不少（秒号落毫秒，容差 1ms）",
                 abs((a0 - 5.0) * FPS - f) <= 0.031 and abs(am * FPS - f) <= 0.031,
                 f"{(a0, ad, am)}")
        must("提前量只接受整数帧（2/3/4），帧网格外的值不出现",
             all(abs(round(x) - x) < 1e-9 for x in (p0 * FPS, pm * FPS)), f"{(p0 * FPS, pm * FPS)}")

        # ============================================================ ③ 首尾守卫
        print("③ 片头不黑屏、片尾不截断、总时长一格不多一格不少")
        axis = [clip(1, 0.0, 4.0), clip(2, 4.0, 6.0), clip(3, 6.0, 10.0)]
        els, notes, _probs = TL.apply_jcut(axis, lib=None, fps=FPS, bands={"p10": 0.5})
        els = [dict(e) for e in els]
        must("首格不写提前量（否则片头会黑 L 帧）", not els[0].get("pic_lead"), "")
        must("末格不写提前量（否则片尾画面被截断）", not els[-1].get("pic_lead"), "")
        must("中间那一刀写了 3 帧", els[1].get("pic_lead") == 3, f"{els[1].get('pic_lead')}")
        dic = {e["n"]: e for e in els}
        pb = CT.picture_bounds(dic, FPS)
        must("画面从 0s 开始（第一帧不是黑）", pb[1][0] == 0.0, f"{pb}")
        a_total = sum(CT.element_span(e) for e in dic.values())
        must("画面止点 == 声音总长（片尾不多不少）",
             abs(max(v[1] for v in pb.values()) - a_total) < 1e-9,
             f"{max(v[1] for v in pb.values())} vs {a_total}")
        must("相邻两格画面首尾相接（不留空、不重叠）",
             all(abs(pb[n][1] - pb[n + 1][0]) < 1e-9 for n in (1, 2)),
             f"{pb}")
        lv, val, note = CT.av_span_verdict(dic, FPS)
        must("⑨ 解耦后「音视频总时长差 ≤ 1 帧」仍成立", lv == "green", f"{lv} {val} {note}")

        # ============================================================ ④ 只在句间口播切点
        print("④ J-cut 只出现在「句间口播」切点：B-roll / 卡片进场 / 片头片尾都不做")
        mixed = [
            clip(1, 0.0, 1.5),                        # 片头（切点 <3s）
            clip(2, 1.5, 4.0),
            clip(3, 4.0, 6.0, medium="broll", aid="b.mov"),   # B-roll 进来：不做
            clip(4, 6.0, 9.0),
            clip(5, 9.0, 12.0),                       # 中间有卡在 9.1s 入场：不做
            clip(6, 12.0, 14.0),
            clip(7, 14.0, 16.0),                      # 收尾格：最后一刀不做（片尾）
        ]
        mixed[4]["card"] = {"kind": "keyword", "text": "卡", "on": "口"}
        # 卡的入场时刻 = 锚词在这一格里的源起点 + CARD_LAG。造一个 9.1s 说「口」的字表，
        # 于是这张卡落在 9.25s——离切点 9.0s 只有 0.25s，必须在保护带里被让开。
        _chars = lambda _aid: [{"c": "口", "s": 9.1, "e": 9.4}]        # noqa: E731
        els4, notes4, _p4 = TL.apply_jcut(mixed, lib=None, fps=FPS, bands={"p10": 0.5},
                                          chars_of=_chars)
        leads4 = {e["n"]: e.get("pic_lead") for e in els4}
        must("片头那一刀不做（切点 1.5s < 3s）", not leads4[2], f"{leads4}")
        must("B-roll 那两刀不做（#2→#3 与 #3→#4）", not leads4[3], f"{leads4}")
        must("卡片进场那一刀不做（#4→#5，卡在 9.1s）", not leads4[5], f"{leads4}")
        must("剩下的句间口播那一刀做了（#5→#6）", leads4[6] == 3, f"{leads4}")
        must("片尾那一刀不做（#6→#7）", not leads4[7], f"{leads4}")
        must("不做的原因都落在 notes 里（不静默跳过）",
             any("片头" in n for n in notes4) and any("非句间口播" in n for n in notes4)
             and any("卡片进场" in n for n in notes4), f"{notes4}")

        # ============================================================ ⑤ 降级 / 不做
        print("⑤ 降级到 2 帧、以及「2 帧也不行就不做」两种情形各一例")
        deg = [clip(1, 0.0, 4.0), clip(2, 4.0, 5.4), clip(3, 5.4, 9.0)]
        e5, n5, _p5 = TL.apply_jcut(deg, lib=None, fps=FPS, bands={"p10": 1.32})
        must("降级：3 帧会让这一镜短于下限 → 用 2 帧", e5[1].get("pic_lead") == 2,
             f"{e5[1].get('pic_lead')}")
        must("降级理由写进 notes（哪一刀、卡在哪条）",
             any("降到 2 帧" in x and "节奏档下限" in x for x in n5), f"{n5}")
        skip = [clip(1, 0.0, 4.0), clip(2, 4.0, 5.35), clip(3, 5.35, 9.0)]
        e6, n6, _p6 = TL.apply_jcut(skip, lib=None, fps=FPS, bands={"p10": 1.32})
        must("2 帧也不行 → 这一刀不做（不硬切）", not e6[1].get("pic_lead"), f"{n6}")
        must("不做也留痕：写明哪一刀、3 帧与 2 帧各卡在哪",
             any("没做" in x and "2 帧不行" in x for x in n6), f"{n6}")

        # ============================================================ ⑥ 不撞节奏档
        print("⑥ 不撞节奏档每镜下限（画面时长，不是声音时长）")
        els6, _n6, _p6 = TL.apply_jcut([clip(1, 0.0, 4.0), clip(2, 4.0, 6.0),
                                        clip(3, 6.0, 8.0), clip(4, 8.0, 12.0)],
                                       lib=None, fps=FPS, bands={"p10": 1.5})
        dic6 = {e["n"]: e for e in els6}
        pb6 = CT.picture_bounds(dic6, FPS)
        short = [(n, round(pb6[n][1] - pb6[n][0], 3)) for n in pb6
                 if pb6[n][1] - pb6[n][0] < 1.5 - 1e-6]
        must("每一格画面都不短于节奏档下限 1.5s", not short, f"{short}")
        must("声音窗口一格没动（还是 4/2/2/4s）",
             [CT.element_span(dic6[n]) for n in sorted(dic6)] == [4.0, 2.0, 2.0, 4.0],
             f"{[CT.element_span(dic6[n]) for n in sorted(dic6)]}")

        # ============================================================ ⑦ 幂等
        print("⑦ 幂等：连跑两次不再变化")
        one = json.loads(json.dumps([clip(1, 0.0, 4.0), clip(2, 4.0, 6.0), clip(3, 6.0, 9.0)]))
        r1, _n1, _p1 = TL.apply_jcut(one, lib=None, fps=FPS, bands={"p10": 0.5})
        snap1 = json.dumps(r1, ensure_ascii=False, sort_keys=True)
        r2, _n2, _p2 = TL.apply_jcut(r1, lib=None, fps=FPS, bands={"p10": 0.5})
        snap2 = json.dumps(r2, ensure_ascii=False, sort_keys=True)
        must("第二次跑出来的时间轴与第一次逐字节相同", snap1 == snap2, "")
        must("关掉（--no-jcut）时 pic_lead 被清干净",
             all(not e.get("pic_lead") for e in
                 TL.apply_jcut(r1, lib=None, fps=FPS, bands={"p10": 0.5},
                               enabled=False)[0]), "")

        # ============================================================ ⑨ 真项目读数
        print("⑨ 真项目：套上 J-cut 后音视频总长仍对齐（全仓真轴扫一遍）")
        real = real_timelines()
        if real:
            bad = []
            n_jcut = 0
            for p, tl in real:
                els_r, _nr, _pr = TL.apply_jcut(
                    tl.get("elements") or [], lib=None, fps=FPS,
                    bands=tl.get("bands") or {}, chars_of=lambda _a: [])
                dic_r = {e["n"]: e for e in els_r}
                n_jcut += sum(1 for e in els_r if e.get("pic_lead"))
                lv_r, val_r, note_r = CT.av_span_verdict(dic_r, FPS)
                if lv_r != "green":
                    bad.append(f"{p.parent.name}: {lv_r} {val_r} {note_r}")
            must(f"{len(real)} 支真轴的「画面/声音总长」全部仍逐帧对齐（共 {n_jcut} 处 J-cut）",
                 not bad, f"{bad[:2]}")
        else:
            print("     （本机没有真项目样本：这一条只跑了合成轴，不假装量过真片）")

        # ============================================================ ⑧ 真片实测
        print("⑧ 真片实测：成片里「声音起点 vs 画面切点」量出来的时间戳")
        fx = FIX / "real-measure.json"
        if not fx.exists():
            must(f"有真片量测记录（{fx.relative_to(ROOT)}）——没有就说明这一条没量", False, "")
        else:
            doc = json.loads(fx.read_text(encoding="utf-8"))
            recs = doc.get("films") or []
            layers = doc.get("layers") or []
            must("有「图层属性」记录（解耦后 <audio> 的起点/时长/入点 == 解耦前 <video> 的）",
                 bool(layers), f"{len(layers)} 条")
            for L in layers:
                ov, nv, na = L["old_video"], L["new_video"], L["new_audio"]
                must(f"{L['slug']}#{L['elem']}：声音层指回**同一个文件**"
                     f"（{na['src']}），起点/时长/入点一字不差",
                     na["src"] == ov["src"] and na["start"] == ov["start"]
                     and na["dur"] == ov["dur"] and na["media"] == "0",
                     f"老 <video> {ov} → 新 <audio> {na}")
                must(f"{L['slug']}#{L['elem']}：画面层只晚了 {L['lead']} 帧，时长扣回、入点跟着挪",
                     abs((float(nv["start"]) - float(ov["start"])) * L["fps"] - L["lead"]) <= 0.03
                     and abs(float(nv["media"]) * L["fps"] - L["lead"]) <= 0.03,
                     f"{nv}")
            must("至少 2 支真片量过", len(recs) >= 2, f"{len(recs)} 支")
            for r in recs:
                a, b = r["no_jcut"], r["jcut"]
                lead = (b["pic_cut"] - b["audio_arrival"]) * r["fps"]
                base = (a["pic_cut"] - a["audio_arrival"]) * r["fps"]
                moved = (b["pic_cut"] - a["pic_cut"]) * r["fps"]
                must(f"{r['slug']}：有 J-cut 时提前量实测 {lead:.2f} 帧（2–4 帧）",
                     2.0 - 0.6 <= lead <= 4.0 + 0.6, f"pic {b['pic_cut']}s · audio {b['audio_arrival']}s")
                must(f"{r['slug']}：同一处没有 J-cut 时提前量 {base:.2f} 帧（≈0，说明量的是画面不是别处）",
                     -0.6 <= base <= 1.0, f"pic {a['pic_cut']}s · audio {a['audio_arrival']}s")
                must(f"{r['slug']}：画面确实晚了 {moved:.2f} 帧，声音一点没挪",
                     1.5 <= moved <= 4.5
                     and abs(b["audio_arrival"] - a["audio_arrival"]) * r["fps"] <= 1.0,
                     f"Δpic {moved:.2f} 帧 · Δaudio "
                     f"{(b['audio_arrival'] - a['audio_arrival']) * 1000:.1f}ms")
                live = Path(r.get("film") or "")
                same_film = (live.exists() and r.get("film_sha256")
                             and file_sha(live) == r["film_sha256"])
                if same_film and r.get("source") and Path(r["source"]).exists():
                    t = remeasure(live, Path(r["source"]), r["t_boundary"],
                                  r["src_t"], r["fps"])
                    must(f"{r['slug']}：成片还在（sha 对得上），现量一遍 "
                         f"{t['frames']:.2f} 帧（与记录差 ≤1 帧）",
                         abs(t["frames"] - lead) <= 1.0,
                         f"现量 pic {t['pic_cut']}s · audio {t['audio_arrival']}s")
                else:
                    # 不装「量过了」：成片被重渲/还原过，就只能认当初那一次量测记录。
                    print(f"     · {r['slug']}：成片已换过（sha 对不上），这一条只认记录里那次的"
                          f" {r['jcut']['pic_cut']}s / {r['jcut']['audio_arrival']}s")

    print()
    if fails:
        print(f"❌ J-cut 解耦合同测试未通过：{'、'.join(fails)}")
        return 1
    print("✅ J-cut 解耦（画面层 / 声音层两个起点）合同测试全过")
    return 0


# ---------------------------------------------------------------- 真片量测

def _pcm(path: Path, ss: float | None = None, t: float | None = None, sr: int = 16000):
    import numpy as np
    cmd = [ffmpeg_bin(), "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", str(path)]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float64)


def audio_arrival(film: Path, source: Path, src_t: float, around: float,
                  win: float = 0.6, span: float = 0.5, sr: int = 16000) -> tuple[float, float]:
    """成片里「源素材 src_t 那一刻的声音」出现在第几秒（归一化互相关），返回 (秒, 相似度)。

    搜索窗必须比模板长出一整段（`span` 前后各 0.5s + 模板长度），否则真峰值可能落在
    窗口外——那时互相关会「照样返回一个最大值」，把假峰当成答案（踩过一次）。
    """
    import numpy as np
    tpl = _pcm(source, src_t, win, sr)
    hay = _pcm(film, around - span, win + 2 * span, sr)
    tn = tpl - tpl.mean()
    tden = float(np.sqrt((tn ** 2).sum())) or 1.0
    best, bi = -2.0, -1
    for i in range(0, max(1, len(hay) - len(tpl))):
        w = hay[i:i + len(tpl)]
        w = w - w.mean()
        d = float(np.sqrt((w ** 2).sum())) or 1.0
        c = float((w * tn).sum() / (d * tden))
        if c > best:
            best, bi = c, i
    return (round(around - span + bi / sr, 4), round(best, 3))


def picture_cut(film: Path, around: float, fps: float = 30.0,
                span: float = 0.35) -> tuple[float, float]:
    """画面切点：在 around±span 里逐帧比对「像不像切点前那一镜」，取第一次翻脸的那一帧。

    返回 (切点秒, 该帧的差值)。分辨率就是 1/fps（帧网格），所以判据按帧数给容差。
    """
    import numpy as np
    W, H = 270, 480
    t0 = around - span
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{t0:.3f}", "-i", str(film),
                          "-t", f"{2 * span:.3f}",
                          "-vf", f"scale={W}:{H},format=gray,fps={fps:g}",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (W * H)
    if n < 6:
        return (0.0, 0.0)
    fr = np.frombuffer(raw[:n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float64)
    ref_old = fr[0]
    ref_new = fr[-1]
    for k in range(1, n):
        d_old = float(np.abs(fr[k] - ref_old).mean())
        d_new = float(np.abs(fr[k] - ref_new).mean())
        if d_new < d_old:
            return (round(t0 + k / fps, 4), round(d_old - d_new, 2))
    return (round(t0 + (n - 1) / fps, 4), 0.0)


def remeasure(film: Path, source: Path, t_boundary: float, src_t: float,
              fps: float = 30.0) -> dict:
    ta, corr = audio_arrival(film, source, src_t, t_boundary)
    tc, _d = picture_cut(film, t_boundary + 2.0 / fps, fps)
    return {"audio_arrival": ta, "corr": corr, "pic_cut": tc,
            "frames": round((tc - ta) * fps, 2)}


def measure(projects: list[str]) -> int:
    """`--measure <项目>…`：量成片（有 J-cut / 无 J-cut 两次渲的成片）并记录。

    需要先把两次渲的成片留在项目里：`out/film-hf.mp4`（当前状态）与
    `--keep-a` 指定的旧成片。为了不靠猜，这一档要求项目里有 `out/film-hf.mp4`
    和 `out/film-hf-nojcut.mp4`（无 J-cut 的那一版）。
    """
    recs: list[dict] = []
    layers: list[dict] = []
    ok = True
    for p in projects:
        proj = Path(p).expanduser().resolve()
        tl = json.loads((proj / "timeline.json").read_text(encoding="utf-8"))
        els = tl.get("elements") or []
        fps = CT._fps_of(proj)
        lead_n = next((int(e["n"]) for e in els if e.get("pic_lead")), None)
        if lead_n is None:
            print(f"  ✗ {proj.name}：时间轴上没有 pic_lead，没得量")
            ok = False
            continue
        el = next(e for e in els if e["n"] == lead_n)
        b = CT.film_bounds({e["n"]: e for e in els})
        t_b = b[lead_n][0]
        au = el.get("audio") or {}
        aid = au.get("asset") or el.get("asset")
        src = None
        for cand in proj.resolve().parents:
            f = cand / str(aid)
            if f.exists():
                src = f
                break
        if src is None:
            print(f"  ✗ {proj.name}：找不到源素材 {aid}")
            ok = False
            continue
        film = proj / "out" / "film-hf.mp4"
        film_a = proj / "out" / "film-hf-nojcut.mp4"
        # out/index-nojcut.html：无 J-cut 那一次渲留下的 index.html（量图层属性用）
        if not (film.exists() and film_a.exists()):
            print(f"  ✗ {proj.name}：缺成片（要 out/film-hf.mp4 与 out/film-hf-nojcut.mp4）")
            ok = False
            continue
        rb = remeasure(film, src, t_b, float(au.get("in") or 0), fps)
        ra = remeasure(film_a, src, t_b, float(au.get("in") or 0), fps)
        # 图层属性：解耦前的 <video>（声音+画面同一个窗口）vs 解耦后的 <video>/<audio>
        old_html = proj / "out" / "index-nojcut.html"
        cur_html = proj / "hf" / "index.html"
        if old_html.exists():
            lo, ln = html_layers(old_html), html_layers(cur_html)
            key = f"v{lead_n}"
            if key in lo and f"a{lead_n}" in ln and key in ln:
                layers.append({"slug": proj.name, "elem": lead_n, "fps": fps,
                               "lead": int(el.get("pic_lead") or 0),
                               "old_video": lo[key], "new_video": ln[key],
                               "new_audio": ln[f"a{lead_n}"]})
        rec = {"slug": proj.name, "project": str(proj), "fps": fps,
               "lead_frames": int(el.get("pic_lead") or 0),
               "elem": lead_n, "t_boundary": round(t_b, 3),
               "src_t": round(float(au.get("in") or 0), 3), "source": str(src),
               "film": str(film),
               "no_jcut": {"pic_cut": ra["pic_cut"], "audio_arrival": ra["audio_arrival"]},
               "jcut": {"pic_cut": rb["pic_cut"], "audio_arrival": rb["audio_arrival"]}}
        recs.append(rec)
        print(f"  · {proj.name}：切点 {t_b:.3f}s · 无 J-cut 画面 {ra['pic_cut']}s/声音 "
              f"{ra['audio_arrival']}s · 有 J-cut 画面 {rb['pic_cut']}s/声音 "
              f"{rb['audio_arrival']}s → 提前量 {rb['frames']:.2f} 帧")
    if ok and recs:
        FIX.mkdir(parents=True, exist_ok=True)
        (FIX / "real-measure.json").write_text(json.dumps(
            {"films": recs, "layers": layers,
             "how": "test_av_decouple.py --measure；画面切点=帧差翻脸首帧，"
                    "声音到达=源素材 0.6s 模板块在成片音轨里的互相关峰；"
                    "layers 是从 index.html 抓的 <video>/<audio> 时序属性"},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {FIX / 'real-measure.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
