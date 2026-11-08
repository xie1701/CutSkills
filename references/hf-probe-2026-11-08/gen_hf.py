#!/usr/bin/env python3
"""把 edl.json 的若干段翻成一份 HyperFrames 合成（html），渲成 mp4。

只做验证用：证明「同一批素材、同一份 EDL」能不能交给 HTML 渲染管线。
"""
import json, subprocess, sys, pathlib

LIB = pathlib.Path("/Users/jerry/Downloads/柒造Video/素材")
IDX = LIB / "_cutskills/index.json"
MAX_CHARS = 9          # 单条字幕最多几个字
CAP_CENTER = 0.692     # 字幕带中心，占画高
W, H, FPS = 1080, 1920, 30


def phrases_of(asset):
    d = json.load(open(IDX))
    for a in d["assets"]:
        if a["id"] == asset:
            return a["transcript"]["phrases"]
    raise SystemExit(f"库里没有 {asset}")


def chunks(phrases, tin, tout):
    """把落在 [tin,tout] 里的短语按字数切块，按字均分时间。返回 film 时间轴上的 (t0,t1,text)。"""
    out = []
    for p in phrases:
        s, e, text = p["start"], p["end"], p["text"]
        if e <= tin + 0.02 or s >= tout - 0.02:
            continue
        text = text.strip()
        if not text:
            continue
        # 按标点断句后再按字数切
        buf, parts = "", []
        for ch in text:
            buf += ch
            if ch in "，。？！、；：" or len(buf) >= MAX_CHARS:
                w = buf.strip("，。？！、；： ")
                if w:
                    parts.append(w)
                buf = ""
        if buf.strip("，。？！、；： "):
            parts.append(buf.strip("，。？！、；： "))
        total = sum(len(x) for x in parts) or 1
        span = max(e - s, 0.2)
        cursor = s
        for part in parts:
            d = span * len(part) / total
            a, b = max(cursor, tin), min(cursor + d, tout)
            if b - a >= 0.12:
                out.append((a - tin, b - tin, part))
            cursor += d
    return out


def main(edl_path, out_dir, n_segs=3):
    edl = json.load(open(edl_path))
    segs = edl["segments"][:n_segs]
    out = pathlib.Path(out_dir)
    (out / "assets/clips").mkdir(parents=True, exist_ok=True)

    # 1) 切素材（已存在就跳过）
    films, t_cursor = [], 0.0
    for i, s in enumerate(segs, 1):
        clip = out / f"assets/clips/s{i}.mp4"
        if not clip.exists():
            src = LIB / s["asset"]
            subprocess.run(
                ["npx", "--yes", "hyperframes", "measure", "extract", str(src),
                 "--from", str(s["in"]), "--to", str(s["out"]), "--audio",
                 "--out", str(clip)], check=True, capture_output=True)
        dur = round(s["out"] - s["in"], 3)
        films.append(dict(i=i, src=f"assets/clips/s{i}.mp4", start=round(t_cursor, 3), dur=dur))
        t_cursor += dur
    total = round(t_cursor, 3)

    # 2) 字幕
    caps = []
    for i, s in enumerate(segs, 1):
        base = films[i - 1]["start"]
        for (a, b, text) in chunks(phrases_of(s["asset"]), s["in"], s["out"]):
            caps.append(dict(start=round(base + a, 3), dur=round(b - a, 3), text=text))

    # 3) 大字（取第一段 card 和它的词位置：从字幕里找包含该词的那条）
    kw = None
    card = (segs[0].get("card") or {})
    if card.get("text"):
        hit = [c for c in caps if card.get("on") and card["on"][:2] in c["text"]]
        at = (hit[0]["start"] - 0.12) if hit else 0.4
        kw = dict(text=card["text"], at=max(0.1, round(at, 3)), dur=1.3)

    html = render_html(films, caps, kw, total)
    (out / "index.html").write_text(html)
    print(f"wrote {out/'index.html'}  total={total}s  caps={len(caps)}  kw={kw}")


def render_html(films, caps, kw, total):
    vids = "\n".join(
        f'    <video id="v{f["i"]}" class="clip" src="{f["src"]}" '
        f'data-start="{f["start"]}" data-duration="{f["dur"]}" data-media-start="0" '
        f'data-track-index="0" playsinline data-has-audio="true"></video>'
        for f in films)
    caps_html = "\n".join(
        f'      <div id="cap-{i:02d}" class="clip cap" data-start="{c["start"]}" data-duration="{c["dur"]}">'
        f'<span>{c["text"]}</span></div>' for i, c in enumerate(caps, 1))
    kw_html = ""
    if kw:
        kw_html = (
            f'      <div class="clip kw" id="kw" data-start="{kw["at"]}" data-duration="{kw["dur"]}">'
            f'<span id="kwt">{kw["text"]}</span></div>')

    return f'''<!doctype html>
<html lang="zh" data-resolution="portrait">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={W}, height={H}" />
<title>hf-test</title>
<style>
@font-face {{ font-family: "SC Sub"; src: url("assets/fonts/NotoSansSC-SemiBold.ttf"); font-weight: 600; }}
@font-face {{ font-family: "SC Black"; src: url("assets/fonts/NotoSansSC-Black.ttf"); font-weight: 900; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{W}px; height:{H}px; overflow:hidden; background:#000; }}
#root {{ position:relative; width:100%; height:100%; }}
.clip {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
#grade {{ position:absolute; inset:0; background:radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0) 42%, rgba(30,18,8,0.55) 100%); }}
.cap {{ position:absolute; left:0; right:0; top:{CAP_CENTER*100:.2f}%; display:flex; justify-content:center; }}
.cap span {{ font-family:"SC Sub", sans-serif; font-weight:600; font-size:54px; color:#fff;
  letter-spacing:0.01em; text-shadow:0 3px 10px rgba(0,0,0,0.85), 0 0 2px rgba(0,0,0,0.9); }}
.kw {{ position:absolute; left:0; right:0; top:46%; display:flex; justify-content:center; }}
.kw span {{ font-family:"SC Black", sans-serif; font-weight:900; font-size:188px; line-height:1;
  background:linear-gradient(180deg,#FFE53B 0%,#FFB000 100%); -webkit-background-clip:text;
  color:transparent; -webkit-text-stroke:0; filter:drop-shadow(0 8px 24px rgba(0,0,0,0.55)); }}
</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-width="{W}" data-height="{H}" data-fps="{FPS}" data-duration="{total}">
{vids}
    <div id="grade"></div>
    <div id="kwlayer">
{kw_html}
    </div>
    <div id="caplayer">
{caps_html}
    </div>
  </div>
<script src="assets/gsap.min.js"></script>
<script>
const tl = gsap.timeline({{ paused: true }});
// 每条字幕：从下 14px 滑上来 + 淡入，60ms 内完成——ffmpeg 那条链做不到的曲线
document.querySelectorAll(".cap").forEach((c) => {{
  const t = parseFloat(c.dataset.start);
  tl.fromTo(c.querySelector("span"),
    {{ y: 14, opacity: 0 }},
    {{ y: 0, opacity: 1, duration: 0.16, ease: "power3.out" }}, t);
}});
if (document.getElementById("kwt")) {{
  const box = document.getElementById("kw");
  const t = parseFloat(box.dataset.start);
  tl.fromTo("#kwt",
    {{ scale: 0.72, opacity: 0, rotate: -3 }},
    {{ scale: 1, opacity: 1, rotate: 0, duration: 0.34, ease: "back.out(2.2)" }}, t);
  tl.to("#kwt", {{ opacity: 0, duration: 0.18, ease: "power1.in" }}, t + parseFloat(box.dataset.duration) - 0.18);
}}
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body>
</html>
'''


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
