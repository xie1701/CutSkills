#!/usr/bin/env python3
"""C 段主链路：edl.json → HyperFrames 合成（HTML）→ mp4。

为什么要换掉 ffmpeg 合成层（2026-11-08）：那一层没有布局引擎。所有坑都是同一件事的
变体——用 PIL 量墨迹高反算字号、没有 libass 只能画 PNG 再 overlay、静止图要 -loop 1、
叠加不钉 -t 会漂、渐变参数要穿三层函数且丢了一句错都不报。HTML 里这些是 CSS。

分工：
- **素材标准化**仍归 ffmpeg（`hyperframes measure extract` 走的就是它）：HDR→709、旋转、切段。
- **字幕切条与字级对齐**仍用 render_film.build_cues——那里的逻辑是踩出来的
  （按字裁段窗口、逐字对字、换气收口、纯标点不成条）。这一层不重写。
- **合成、排版、动效**交给浏览器。

用法：
    python3 scripts/render_hf.py --project <项目> --library <库> [--segments 3]
"""
from __future__ import annotations

import argparse, html as _html, json, os, re, shutil, subprocess, sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_edl import resolve_lib                      # noqa: E402
from render_film import build_cues, load_chars        # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
HF = os.environ.get("CUTSKILLS_HF", "npx --yes hyperframes")


# ------------------------------------------------------------------ 字体

FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
             Path("/System/Library/Fonts")]


def find_font(name: str) -> Path | None:
    """样式包里写的是字体名（NotoSansSC-SemiBold），这里找到真文件。

    HTML 不吃 `local()`：lint 会报 `font_family_without_font_face`，
    所以必须有一份能进 @font-face 的文件，复制进项目的 assets/fonts。
    """
    if not name:
        return None
    p = Path(name).expanduser()
    if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.exists():
        return p
    cands = [p for d in FONT_DIRS if d.exists() for p in d.glob(f"{name}.*")]
    if not cands:
        cands = [p for d in FONT_DIRS if d.exists()
                 for p in d.glob(f"*{name}*") if p.suffix.lower() in (".ttf", ".otf")]
    return cands[0] if cands else None


# ------------------------------------------------------------------ 素材

def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,nb_frames",
                        "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"dur": float((d.get("format") or {}).get("duration") or 0.0),
            "w": int(st.get("width") or 0), "h": int(st.get("height") or 0)}


def get_clip(lib: Path, proj: Path, seg: dict, force: bool = False) -> dict:
    """把一段切出来。**缓存**：同一段再渲不再切（切一段 6.7s 素材要约 38 秒）。

    切好的片段是"已经标准化的素材"——旋转过的 HEVC 竖屏、杜比视界，到这一步
    都是统一尺寸的 H.264，浏览器只管播。
    """
    clips = proj / "hf" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    aid = seg["asset"]
    tag = f"{Path(aid).stem[-14:]}_{seg['in']:.2f}-{seg['out']:.2f}".replace(".", "p")
    tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
    dst = clips / f"{tag}.mp4"
    if force or not dst.exists() or dst.stat().st_size < 4096:
        src = lib / aid
        if not src.exists():
            raise SystemExit(f"素材不存在：{src}")
        r = subprocess.run(
            [*HF.split(), "measure", "extract", str(src),
             "--from", str(seg["in"]), "--to", str(seg["out"]),
             "--audio", "--out", str(dst)],
            capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            raise SystemExit(f"切段失败：{aid} {seg['in']}-{seg['out']}\n{r.stderr[-800:]}")
    info = probe(dst)
    return {"path": dst, "dur": round(info["dur"], 3), "w": info["w"], "h": info["h"]}


# ------------------------------------------------------------------ 卡片

def card_windows(segs, lib, chars_cache, style):
    """EDL 的 card → (text, start, end, kind)。

    大字什么时候出不由我们拍板，由这句话什么时候被说出来决定：`on` 写这类词里的
    几个字，去逐字时间戳里找它的起止，提前 0.12s 起、停到说完，夹在模板的 hold 区间。
    """
    emph = style.get("emphasis") or {}
    hold = emph.get("hold_s") or style.get("hold_pair") or [1.0, 1.5]
    h0, h1 = float(hold[0]), float(hold[1])
    cards = []
    idc = (style.get("_id_card") or None)
    if idc:
        cards.append({"kind": "idcard", "text": "", "lines": [str(x) for x in idc["lines"]],
                      "start": float(idc.get("at") or 0.35),
                      "end": round(float(idc.get("at") or 0.35) + float(idc.get("dur") or 9.0), 3)})
    for s in segs:
        c = s.get("card")
        if not c:
            continue
        kind = str(c.get("kind") or "keyword")
        start = round(s["_start"] + (float(c["at"]) if isinstance(c.get("at"), (int, float)) else 0.6), 3)
        d = max(h0, min(float(c.get("dur") or h1), h1))
        on = str(c.get("on") or "").strip()
        aid = (s.get("audio") or {}).get("asset") or s["asset"]
        a_in = float((s.get("audio") or {}).get("in", s["in"]))
        if on:
            chars = chars_cache.get(aid) or chars_cache.setdefault(aid, load_chars(lib, aid))
            win = [(ch, st, en) for ch, st, en in chars
                   if a_in - 0.05 <= st < a_in + s["_dur_q"] + 0.05]
            txt = "".join(ch for ch, _, _ in win)
            j = txt.find(on)
            if j >= 0:
                st0 = win[j][1] - a_in + s["_start"] - 0.12
                st1 = win[min(j + len(on) - 1, len(win) - 1)][2] - a_in + s["_start"]
                start = round(max(s["_start"] + 0.15, st0), 3)
                d = round(min(h1, max(h0, st1 - start + 0.25)), 3)
            else:
                print(f"  ！大字对不上原话「{on}」（段 {s['n']}），沿用默认时机",
                      file=sys.stderr)
        cards.append({"kind": kind, "text": str(c.get("text") or ""),
                      "start": start, "end": round(start + d, 3)})
    return cards


# ------------------------------------------------------------------ 合成

CSS_TMPL = """<!doctype html>
<html lang="zh" data-resolution="{res}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={W}, height={H}" />
<title>{slug}</title>
<!-- 由 scripts/render_hf.py 从 edl.json 生成；改剪辑去改 EDL，别手改这个文件 -->
<style>
@font-face {{ font-family: "SC-Sub"; src: url("assets/fonts/{fr}"); font-weight: 600; }}
@font-face {{ font-family: "SC-Big"; src: url("assets/fonts/{fb}"); font-weight: 900; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{W}px; height:{H}px; overflow:hidden; background:#000; }}
#root {{ position:relative; width:100%; height:100%; }}
.clip {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
#grade {{ position:absolute; inset:0; {grade} }}
.cap {{ position:absolute; left:0; right:0; top:{cap_top}%; display:flex; justify-content:center;
        padding:0 {cap_pad}px; }}
.cap span {{ font-family:"SC-Sub", sans-serif; font-weight:600; font-size:{cap_px}px;
  color:{cap_color}; letter-spacing:0.01em; line-height:1.28; text-align:center;
  text-shadow:{cap_shadow}; {cap_extra} }}
.kw {{ position:absolute; left:0; right:0; top:{kw_top}%; display:flex; justify-content:center;
       padding:0 {cap_pad}px; }}
.kw span {{ font-family:"SC-Big", sans-serif; font-weight:900; font-size:{kw_px}px;
  line-height:1.06; text-align:center; color:{kw_color};
  text-shadow:0 6px 22px rgba(0,0,0,0.6); {kw_extra} }}
.idcard {{ position:absolute; left:{idc_x}%; top:{idc_y}%; text-align:left; }}
.idcard span {{ display:block; font-family:"SC-Sub", sans-serif; color:#fff; line-height:1.34; }}
</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0"
       data-width="{W}" data-height="{H}" data-fps="{FPS}" data-duration="{total}">
{videos}
    <div id="grade"></div>
{cards}
    <div id="caplayer">
{caps}
    </div>
  </div>
<script src="assets/gsap.min.js"></script>
<script>
const tl = gsap.timeline({{ paused: true }});
// 字幕：{cap_entry}
document.querySelectorAll(".cap").forEach((c) => {{
  const t = parseFloat(c.dataset.start);
  const h = c.querySelector("span");
  {cap_tween}
}});
// 大字：先弹进来，停住，再退
document.querySelectorAll(".kw").forEach((c) => {{
  const t = parseFloat(c.dataset.start);
  const d = parseFloat(c.dataset.duration);
  tl.fromTo(c.querySelector("span"),
    {{ scale: 0.74, opacity: 0, rotate: -2.4 }},
    {{ scale: 1, opacity: 1, rotate: 0, duration: 0.34, ease: "back.out(2.2)" }}, t);
  tl.to(c.querySelector("span"), {{ opacity: 0, duration: 0.16, ease: "power1.in" }},
        t + Math.max(0, d - 0.16));
}});
document.querySelectorAll(".idcard > span").forEach((el, i) => {{
  tl.fromTo(el, {{ x: -18, opacity: 0 }},
    {{ x: 0, opacity: 1, duration: 0.42, ease: "power3.out" }}, 0.42 + i * 0.08);
}});
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body>
</html>
"""


def fonts_hint(style) -> str:
    f = (style.get("subtitle") or {}).get("font")
    return str(f) if f else ""


def esc(t: str) -> str:
    return _html.escape(str(t), quote=False)


def build_html(*, slug, W, H, FPS, total, videos, caps, cards, style, fonts, grade):
    sub = style.get("subtitle") or {}
    emph = style.get("emphasis") or {}
    cap_px = int(sub.get("size_px") or 72)
    cap_top = round(float(sub.get("center_y_px") or (H * 0.674)) / H * 100, 2)
    # 单条字幕宽度：按最长的预期字数估一个安全边距，中文大约 1em 一字
    cap_pad = max(24, int((W - cap_px * 10) / 2))
    # big_keyword_px 可能是 [下限, 上限]（talking-head-v1 写的是 [133,304]）。
    # 取区间里靠下的值：花字是压画面用的，不是把一屏塞满。
    _kw = emph.get("big_keyword_px")
    if isinstance(_kw, (list, tuple)) and _kw:
        kw_px = int(_kw[0] + (_kw[1] - _kw[0]) * 0.18)
    elif isinstance(_kw, (int, float)):
        kw_px = int(_kw)
    else:
        kw_px = int(round(H * 0.10))
    kw_top = round(float(emph.get("top_h") or 0.44) * 100, 2)
    entry = str(sub.get("entry") or "fade")
    tween = ('tl.fromTo(h, { y: 16, opacity: 0 }, { y: 0, opacity: 1, duration: 0.18, '
             'ease: "power3.out" }, t);' if entry != "hard_cut" else
             'tl.set(h, { y: 0, opacity: 1 }, t);')
    idcard = next((c for c in cards if c["kind"] == "idcard"), None)
    idc_x = round(float((style.get("_id_card") or {}).get("x") or 0.055) * 100, 2)
    idc_y = round(float((style.get("_id_card") or {}).get("y") or 0.155) * 100, 2)

    v_html = "\n".join(
        f'    <video id="v{i}" class="clip" src="{v["src"]}" data-start="{v["start"]}" '
        f'data-duration="{v["dur"]}" data-media-start="0" data-track-index="0" '
        f'playsinline data-has-audio="true"></video>'
        for i, v in enumerate(videos, 1))
    c_html = "\n".join(
        f'      <div id="cap-{i:03d}" class="clip cap" data-start="{c["start"]}" '
        f'data-duration="{round(c["end"] - c["start"], 3)}"><span>{esc(c["text"])}</span></div>'
        for i, c in enumerate(caps, 1))
    card_html = []
    for i, c in enumerate(cards, 1):
        d = round(c["end"] - c["start"], 3)
        if c["kind"] == "idcard":
            lines = "\n".join(f'      <span style="font-size:{sz}px">{esc(t)}</span>'
                              for t, sz in zip(c.get("lines") or [],
                                               (style.get("_id_card") or {}).get("sizes")
                                               or [64, 44, 36, 30]))
            card_html.append(
                f'    <div id="idc" class="clip idcard" data-start="{c["start"]}" '
                f'data-duration="{d}">\n{lines}\n    </div>')
        else:
            card_html.append(
                f'    <div id="kw-{i:02d}" class="clip kw" data-start="{c["start"]}" '
                f'data-duration="{d}"><span>{esc(c["text"])}</span></div>')

    return CSS_TMPL.format(
        res="portrait" if H > W else "landscape", W=W, H=H, FPS=FPS, total=total, slug=slug,
        fr=fonts["sub"], fb=fonts["big"],
        grade=grade or "background:transparent;",
        cap_top=cap_top, cap_pad=cap_pad, cap_px=cap_px,
        cap_color=sub.get("color") or "#FFFFFF",
        cap_shadow=sub.get("shadow") or "0 3px 8px rgba(0,0,0,.75)",
        cap_extra="",
        kw_top=kw_top, kw_px=kw_px,
        kw_color=emph.get("keyword_color") or "#FFD400",
        kw_extra=("background:linear-gradient(180deg,%s 0%%,%s 100%%);"
                  "-webkit-background-clip:text;color:transparent;" %
                  tuple((emph.get("gradient") or ["#FFE53B", "#FFB000"])[:2])
                  if emph.get("gradient") else ""),
        idc_x=idc_x, idc_y=idc_y,
        videos=v_html, caps=c_html, cards="\n".join(card_html),
        cap_entry=entry, cap_tween=tween)


# ------------------------------------------------------------------ 主流程

def measure_audio(path: Path) -> tuple[float, float]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    try:
        j = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
        return float(j["input_i"]), float(j["input_tp"])
    except (ValueError, KeyError):
        return -99.0, 0.0


def loudnorm(src: Path, dst: Path, I: float, TP: float) -> int:
    """把混音拉回样式包给的响度，并且真的落在断言上。

    **不能用 linear=true。** 这条链的混音 integrated -20.4 / 真峰 -2.3，
    要涨 6 dB 才到 -14，可线性增益会把真峰顶到 +4 —— loudnorm 于是自己缩了回去，
    实测停在 -15.7，差 1.7 dB。要涨响度只能先压再归一（和 ffmpeg 那条链一样）。
    AAC 编码还有回峰：TP 目标抄 -1 时出来常是 -0.8，所以留 0.4 dB 余量。
    最后量一遍，不够就带着偏差再归一一次——最多三次，收敛就停。
    """
    delta = 0.0
    last = (I, TP)
    for _ in range(3):
        af = ("acompressor=threshold=0.06:ratio=3.5:attack=8:release=220:makeup=1.6,"
              f"loudnorm=I={I + delta:.2f}:TP={TP - 1.0:.2f}:LRA=9")
        rc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "copy",
             "-af", af, "-c:a", "aac", "-b:a", "192k", str(dst)],
            capture_output=True, text=True).returncode
        if rc != 0:
            return rc
        last = measure_audio(dst)
        if abs(last[0] - I) <= 0.4 and last[1] <= TP:
            break
        delta += I - last[0]
    print(f"  响度 {last[0]:.2f} LUFS · 真峰 {last[1]:.2f} dBTP（目标 {I} / {TP}）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--segments", type=int, default=0, help="只渲前 N 段（0=全部）")
    ap.add_argument("--quality", default="looks", choices=["draft", "looks", "delivery"])
    ap.add_argument("--workers", default="4")
    ap.add_argument("--grade", default=None, help="CSS filter，例：saturate(1.5) contrast(1.1)")
    ap.add_argument("--no-render", action="store_true", help="只生成 HTML 并 lint")
    ap.add_argument("--no-loudnorm", action="store_true")
    ap.add_argument("--force-extract", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = resolve_lib(proj, a.library)
    edl = json.loads((proj / "edl.json").read_text())

    name = a.style or edl.get("style_pack") or "talking-head-v1"
    style_path = SKILL / "styles" / f"{name}.yaml"
    if not style_path.exists():
        raise SystemExit(f"样式包不存在：{style_path}")
    style = yaml.safe_load(style_path.read_text())
    plan_p = proj / "plan.json"
    if plan_p.exists():
        try:
            style["_id_card"] = (json.loads(plan_p.read_text()) or {}).get("id_card")
        except json.JSONDecodeError:
            pass

    W = int(style["canvas"]["width"])
    H = int(style["canvas"]["height"])
    FPS = int(style["canvas"]["fps"])

    fonts = {}
    for key, nm in (("sub", (style.get("subtitle") or {}).get("font")),
                    ("big", (style.get("emphasis") or {}).get("font"))):
        f = find_font(nm) if nm else None
        if f is None:
            raise SystemExit(f"找不到字体文件：{nm}（样式包 styles/{name}.yaml）")
        fonts[key] = f

    segs = edl["segments"][:a.segments] if a.segments else edl["segments"]

    # 1) 切段（带缓存）
    hf = proj / "hf"
    (hf / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    videos, t, n_fail = [], 0.0, 0
    for i, s in enumerate(segs, 1):
        try:
            cl = get_clip(lib, proj, s, force=a.force_extract)
        except SystemExit as e:
            print(f"  ！段 {s.get('n', i)} 切不出来：{e}", file=sys.stderr)
            n_fail += 1
            continue
        videos.append({"src": f"clips/{cl['path'].name}", "start": round(t, 3),
                       "dur": cl["dur"]})
        s["_start"] = round(t, 3)
        s["_dur"] = round(s["out"] - s["in"], 3)
        s["_dur_q"] = cl["dur"]                    # 以真正播出来的长度为准
        t = round(t + cl["dur"], 3)
    total = round(t, 3)
    if not videos:
        raise SystemExit("一段都没切出来")

    index = json.loads((lib / "_cutskills" / "index.json").read_text())
    by_id = {x["id"]: x for x in index["assets"]}
    by_alias = {al: x for x in index["assets"] for al in (x.get("aliases") or [])}
    chars_cache: dict[str, list] = {}

    sub = style.get("subtitle") or {}
    if sub.get("chars_soft"):
        soft_c, hard_c = int(sub["chars_soft"]), int(sub["max_chars_per_line"])
    else:
        soft_c, hard_c = 6, 9
    active = [s for s in segs if s.get("_start") is not None]
    caps = build_cues(active, by_id, chars_cache, lib, hard_c, soft_c)
    caps = [c for c in caps if c["start"] < total]
    for c in caps:
        c["end"] = min(round(c["end"], 3), total)

    cards = card_windows(active, lib, chars_cache, style)

    # 字幕轨落盘：verify_film.py 读的是 out/subs.json，两条链路必须吐同一个东西，
    # 否则 D 段会拿 ffmpeg 那版留下的旧字幕去算密度（实测 136 条/分钟）。
    (proj / "out").mkdir(parents=True, exist_ok=True)
    speech_chars = sum(len(c["text"]) for c in caps)
    speech_s = sum(min(c["end"], total) - c["start"] for c in caps)
    ctr = float(sub.get("center_y_px") or H * 0.674)
    (proj / "out" / "subs.json").write_text(json.dumps({
        "cues": caps,
        "band": [max(0, int(ctr) - 260), min(H, int(ctr) + 260)],
        "font": fonts_hint(style), "size": int(sub.get("size_px") or 0),
        "overflow": [],
        "speech_chars": speech_chars,
        "avg_chars": round(speech_chars / len(caps), 2) if caps else 0,
        "chars_per_s": round(speech_chars / speech_s, 2) if speech_s else 0,
        "engine": "hyperframes",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (proj / "out" / "cards.json").write_text(json.dumps(
        {"cards": cards, "engine": "hyperframes"}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # 2) 字体与运行时复制进项目（lint 要求 @font-face 指向真的文件）
    for k, f in fonts.items():
        dst = hf / "assets" / "fonts" / f.name
        if not dst.exists() or dst.stat().st_size != f.stat().st_size:
            shutil.copy2(f, dst)
        fonts[k] = f.name
    gsap_src = Path("/Users/jerry/.agents/skills/talking-head-recut/assets/vendor/gsap.min.js")
    gsap_dst = hf / "assets" / "gsap.min.js"
    if not gsap_dst.exists():
        if not gsap_src.exists():
            raise SystemExit(f"缺 gsap：{gsap_src}")
        shutil.copy2(gsap_src, gsap_dst)

    # 3) 写 HTML
    html_txt = build_html(slug=edl.get("slug") or proj.name, W=W, H=H, FPS=FPS,
                          total=total, videos=videos, caps=caps, cards=cards,
                          style=style, fonts=fonts, grade=a.grade)
    (hf / "index.html").write_text(html_txt)
    (hf / "meta.json").write_text(json.dumps(
        {"from_edl": "edl.json", "style": name, "segments": len(videos),
         "total_s": total, "captions": len(caps), "cards": len(cards),
         "fonts": fonts, "engine": "hyperframes"}, ensure_ascii=False, indent=1))

    print(f"  段 {len(videos)} · {total}s · 字幕 {len(caps)} 条 · 卡 {len(cards)} 块"
          + (f" · 切不出来 {n_fail} 段" if n_fail else ""))

    # 4) lint
    r = subprocess.run([*HF.split(), "lint"], cwd=hf, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+)\s+error", out)
    n_err = int(m.group(1)) if m else (0 if r.returncode == 0 else 1)
    mw = re.search(r"(\d+)\s+warning", out)
    n_warn = int(mw.group(1)) if mw else 0
    print(f"  lint：{n_err} error · {n_warn} warning")
    if n_err or r.returncode != 0:
        print("\n".join(out.splitlines()[-30:]), file=sys.stderr)
        if not a.no_render:
            raise SystemExit("lint 没过，先别渲")

    if a.no_render:
        print(f"  → {hf/'index.html'}")
        return

    # 5) 渲
    raw = proj / "out" / "_hf_raw.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PRODUCER_BROWSER_GPU_MODE=os.environ.get(
        "PRODUCER_BROWSER_GPU_MODE", "hardware"))
    cmd = [*HF.split(), "render", "-o", str(raw), "-q", a.quality, "-w", str(a.workers)]
    print(f"  渲染中（{a.quality}, {a.workers} workers）…")
    r = subprocess.run(cmd, cwd=hf, capture_output=True, text=True, env=env)
    if r.returncode != 0 or not raw.exists():
        tail = ((r.stdout or "") + (r.stderr or "")).splitlines()[-25:]
        raise SystemExit("渲染失败：\n" + "\n".join(tail))
    for ln in (r.stdout or "").splitlines():
        if "rendered in" in ln:
            print("  " + ln.strip())

    # 6) 响度：成片必须落在样式包给的值上（本机 hyperframes 混完是 -20 LUFS 左右）
    dst = proj / "out" / "film-hf.mp4"
    au = style.get("audio") or {}
    if a.no_loudnorm:
        shutil.move(raw, dst)
    else:
        I = au.get("loudness_lufs", -14)
        TP = au.get("true_peak_dbtp", -1)
        r = loudnorm(raw, dst, float(I), float(TP))
        if r != 0 or not dst.exists():
            print("  ！响度归一失败，保留原始混音", file=sys.stderr)
            shutil.move(raw, dst)
        else:
            raw.unlink(missing_ok=True)
    print(f"  → {dst}")
    print(f"  下一步：python3 scripts/verify_film.py --project {proj}")


if __name__ == "__main__":
    main()
