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

import argparse, html as _html, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_edl import resolve_lib                      # noqa: E402
import lock as lockmod                                 # noqa: E402
import lexicon                                        # noqa: E402
import subs                                            # noqa: E402
from render_film import load_chars                    # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
def _resolve_hf() -> str:
    """先找**本地装好的** hyperframes，找不到才退回 npx。

    以前这里默认 "npx --yes hyperframes"：哪怕 ~/.cutskills 里已经装好，
    每次 lint 还是要连 npm registry（实测握手 43 秒），还会撞 _npx 的锁与
    ENAMETOOLONG。「依赖只装一次」被渲染器一句话击穿，属于配置写一套、跑另一套。
    """
    env = (os.environ.get("CUTSKILLS_HF") or "").strip()
    if env:
        return env
    cands = [Path.home() / ".cutskills/npm/node_modules/.bin/hyperframes",
             SKILL / "node_modules/.bin/hyperframes",
             Path("/opt/homebrew/bin/hyperframes")]
    for c in cands:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    w = shutil.which("hyperframes")
    return w or "npx --yes hyperframes"


HF = _resolve_hf()


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


# ------------------------------------------------ 切片（zscale / 自己转 HDR→SDR）
#
# 为什么不用 `hyperframes measure extract`（2026-11-09）：它把 HLG / 杜比视界
# （bt2020）直接压成 bt709，压的时候高光被削平、彩度掉一半以上。实测同一段素材：
#   measure extract → luma 0.52 / 饱和 0.166（参考片 0.381 / 0.511）
# 而且它压完是 SDR，浏览器那边才不用走 capture_hdr_layered（那条路在本机没有
# zscale 时会把整片文字层丢掉，见 STATUS.md 三条硬规矩第 1 条）。
#
# 所以切片这一步自己来：先 zscale 转线性 + hable 色调映射，再回 bt709。
# 前提是有一支带 libzimg 的 ffmpeg——本机 brew 的 ffmpeg 没有，
# 自己编了一支装在 ~/.cutskills/opt/ffmpeg-zimg，由 runtime.sh 优先放进 PATH。

HDR_TRANSFERS = {"arib-std-b67", "smpte2084", "smpte428"}

# 调色。跟 render_film.py 的 GRADE 是同一条链——**在切片阶段烤进素材**，
# 不进浏览器：CSS filter 会连字幕和字卡一起染，而调色只该作用在画面上。
# 实测（14 段粗剪，未加字）：无调色 luma 0.547 / 饱和 0.130；
# 加这条 A 链 luma 0.374（参考片 0.381）。饱和不去对齐参考片 0.511——
# 那个数是带大字和色卡的成片，跟纯画质不同口径。
GRADE = {
    # 白平衡不在这里做——**每镜分开拉**，见 scripts/shot_match.py 与 `--shot-match`。
    # 一开始这里还挂了 colortemperature + colorchannelmixer，把整片的 B÷R 对齐到 0.471；
    # 结果口播段（室内暖光）被拉到 B÷R 0.353，施工段 0.62，一个平均值糊过去，
    # 每换一镜色温跳一下。参考片是逐镜拉平的，所以这一步也得逐镜做。
    # 2026-11-09 按 compare_tones.py 的数校准过（不按饱和度校准）：
    #   目标 luma 0.381 / G÷R 0.750 / B÷R 0.471（参考片，逐秒抽帧算的）
    #   实测 luma 0.372 / G÷R 0.756 / B÷R 0.478
    # `colorchannelmixer` 那三个增益是这次加的主旋钮：原来只靠 colortemperature，
    # 一暖就把 G÷R 也压下去（0.770 → 0.696），绿红平衡对不上。
    # 改成压 B 通道（×0.785）、G 微调（×0.993）、R 微抬（×1.02），三个数同时落位。
    # 2026-11-09 二改（Jerry：「A-roll 整体画面调色比较失真，可能饱和度过高」）。
    # 量出来的账：源片 S均 0.399 / p90 0.578，加老那条链后 0.707 / **p90 1.000**——
    # 饱和度已经顶到 1.0 在削波，肉眼看到的就是「颜色糊成一片」。根因不是 saturate(1.5)
    # 一项，是 contrast 1.10 + gamma 0.85 把亮度压下去之后**相对彩度被进一步放大**。
    # 参考片同口径：S均 0.485 / p90 0.840 / luma 0.421；这条链落在 0.501 / 0.770 / 0.444。
    "warm_high_sat": "eq=saturation=0.90:contrast=1.00:gamma=0.94:brightness=-0.040",
    # 纸感：低饱和、稍亮、对比略降（纸底要亮，画面要退到纸后面）
    "paper_neutral": "eq=saturation=0.72:contrast=0.96:gamma=1.04:brightness=0.020",
    # 暗调：压暗、抬对比、保住高光（暗底单强调色，画面要沉下去）
    "dark_punch": "eq=saturation=0.86:contrast=1.12:gamma=0.86:brightness=-0.070",
}

# HLG / PQ → 场景线性（npl=100 是把 100% 白当参考）→ hable 压到 SDR 亮度 → bt709。
# desat=0：保留饱和度。默认 desat=2 会把高光洗白，那正是我们要避免的。
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,"
           "zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def ffmpeg_bin() -> str:
    """优先 CUTSKILLS_FFMPEG，其次运行时目录里自编的那支，最后 PATH 上的。"""
    env = os.environ.get("CUTSKILLS_FFMPEG")
    if env and Path(env).exists():
        return env
    own = Path.home() / ".cutskills/opt/ffmpeg-zimg/bin/ffmpeg"
    if own.exists():
        return str(own)
    return shutil.which("ffmpeg") or "ffmpeg"


_ZSCALE: dict[str, bool] = {}


def has_zscale(bin_: str | None = None) -> bool:
    b = bin_ or ffmpeg_bin()
    if b not in _ZSCALE:
        r = subprocess.run([b, "-hide_banner", "-filters"], capture_output=True, text=True)
        _ZSCALE[b] = " zscale " in (r.stdout or "")
    return _ZSCALE[b]


def probe_color(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=color_transfer,color_primaries,pix_fmt",
                        "-of", "json", str(path)], capture_output=True, text=True)
    st = ((json.loads(r.stdout or "{}").get("streams") or [{}])[0]) or {}
    return {"transfer": st.get("color_transfer") or "",
            "primaries": st.get("color_primaries") or "",
            "pix_fmt": st.get("pix_fmt") or ""}


def extract_own(src: Path, t0: float, t1: float, dst: Path, *, fps: int = 30,
                crf: int = 16, W: int = 1080, H: int = 1920, overscan: float = 1.0,
                grade: str = "", aud: tuple[Path, float, float] | None = None) -> None:
    """自己切一段：HDR 走 zscale 色调映射，旋转由 ffmpeg 自动应用。

    `-ss` 放在 `-i` 前面（快，从关键帧起解到位置再丢），`-t` 给时长。
    宽高保持原样——竖屏 4K 进 1080×1920 的缩放交给浏览器/CSS，不做两次重采样。
    """
    col = probe_color(src)
    hdr = col["transfer"] in HDR_TRANSFERS
    if hdr and not has_zscale():
        raise SystemExit(
            "素材是 HDR，但这支 ffmpeg 没有 zscale，压出来的片子会偏亮偏灰。\n"
            "装法见 references/ffmpeg-zimg.md，或加 --extractor hf 退回旧路径。")
    # 先在原分辨率上缩放到画布大小再裁（`object-fit: cover` 的等价操作），
    # 顺序很重要：zscale 在 4K 上跑一段要 60 秒，缩到 1080×1920 之后是十几秒，
    # 而且裁掉的边本来就是浏览器里看不见的。
    # overscan：切片比画布大一圈（默认 1.2 倍）。因为「跳切推近」是用 CSS 放大实现的，
    # 若切片就是 1080×1920，放大 1.14 倍＝把画面拽糊（视觉复核原话：「面部出现数字模糊」）。
    # 切大一圈之后，CSS 放大到 1.2 之前都还有真实像素可用。
    OW, OH = max(2, int(round(W * overscan / 2)) * 2), max(2, int(round(H * overscan / 2)) * 2)
    vf = [f"scale={OW}:{OH}:force_original_aspect_ratio=increase:flags=lanczos",
          f"crop={OW}:{OH}"]
    vf.append(TONEMAP if hdr else "format=yuv420p")
    if grade:
        vf.append(grade)
    vf.append(f"fps={fps}")
    # **声音可以从另一支素材来**（`audio` 字段）。这是整条链路最容易被忽略的一层：
    # B-roll 的画面是打磨机，声音必须是他的口播，不是打磨机的环境声。
    # 以前 HTML 链路没做这件事，结果整片 B-roll 段只剩环境声 + 字幕——
    # Jerry 一看片就发现了（「只有字幕和原 B-roll 片段的声音」）。
    dur = round(t1 - t0, 3)
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t0:.3f}", "-i", str(src)]
    if aud:
        a_src, a0, _a1 = aud
        cmd += ["-ss", f"{a0:.3f}", "-i", str(a_src)]
    cmd += ["-t", f"{dur:.3f}", "-vf", ",".join(vf),
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709"]
    if aud:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise SystemExit(f"切片失败：{src.name} {t0}-{t1}\n{(r.stderr or '')[-900:]}")


# ------------------------------------------------------------------ 时间轴

def load_elements(proj: Path) -> tuple[list[dict], str]:
    """读时间轴。**优先 timeline.json（v4）**，没有就回落到 edl.json（v2）。

    v2 是「镜头列表」，表达不了代码生成的格子和刻意的空。v4 把每一格叫 element，
    带 kind（clip / image / gen / silence）——渲的时候就不再假设「一定有个视频」。
    """
    tlp = proj / "timeline.json"
    if tlp.exists():
        tl = json.loads(tlp.read_text())
        els = tl.get("elements") or []
        if not els:
            raise SystemExit(f"{tlp} 里一个元素都没有")
        return els, "timeline.json"
    edl = json.loads((proj / "edl.json").read_text())
    els = []
    for s in edl["segments"]:
        el = {k: v for k, v in s.items() if k != "n"}
        el.update({"n": s["n"], "kind": "clip", "track": "video"})
        els.append(el)
    return els, "edl.json"


def el_dur(el: dict) -> float:
    if el.get("kind") == "clip":
        return round(float(el["out"]) - float(el["in"]), 3)
    return round(float(el.get("dur") or 0.0), 3)


# ------------------------------------------------------------------ 生成元素（代码画的格子）

GEN_CSS = """
.gen { position:absolute; inset:0; display:flex; flex-direction:column;
       justify-content:center; align-items:center; background:var(--ground,#14110f); color:var(--ink,#fff);
       padding:0 88px; text-align:center; }
.gen .big { font-family:"SC-Big",sans-serif; font-weight:900; line-height:1.04;
            letter-spacing:0.01em; }
.gen .sm { font-family:"SC-Sub",sans-serif; font-weight:600; line-height:1.4;
           color:var(--ink,#fff); opacity:0.78; }
.gen .cols { display:flex; width:100%; gap:40px; align-items:stretch; }
.gen .col { flex:1; display:flex; flex-direction:column; justify-content:center;
            gap:18px; padding:48px 24px; border-radius:var(--radius,18px);
            background:color-mix(in srgb, var(--ink,#fff) 8%, transparent); }
.gen .col.hi { outline:var(--stroke,2px) solid var(--accent,#F6EB34); outline-offset:0; }
.gen .row { display:flex; align-items:baseline; gap:26px; width:100%; }
.gen .bar { height:38px; border-radius:var(--radius,6px); background:var(--accent,#F6EB34); }
.gen .chn { font-family:"SC-Big",sans-serif; font-weight:900; color:var(--accent,#F6EB34);
            opacity:0.92; line-height:1; }
.gen .num { font-family:"SC-Big",sans-serif; font-weight:900; color:var(--accent,#F6EB34); line-height:1; }
.gen blockquote { font-family:"SC-Big",sans-serif; font-weight:900; line-height:1.3; }
.gen cite { font-family:"SC-Sub",sans-serif; font-style:normal; opacity:0.7; }
.gen ol { list-style:none; width:100%; display:flex; flex-direction:column; gap:26px; }
.gen ol li { display:flex; gap:26px; align-items:baseline; }
.gen ol li b { font-family:"SC-Big",sans-serif; color:var(--accent,#F6EB34); }
.gen .fcol { display:flex; flex-direction:column; gap:34px; width:100%; align-items:flex-start; }
.gen .fnode { display:flex; align-items:center; gap:24px; position:relative; }
.gen .fnode .dot { width:26px; height:26px; border-radius:50%; background:var(--accent,#F6EB34);
                   flex:0 0 26px; box-shadow:0 0 0 6px rgba(246,235,52,0.16); }
.gen .fnode::after { content:""; position:absolute; left:12px; top:26px; width:2px; height:34px;
                     background:var(--accent,#F6EB34); opacity:.32;
                     transform-origin:top; }
.gen .fnode:last-child::after { display:none; }
.gen .lstack { display:flex; flex-direction:column; gap:12px; width:100%; }
.gen .layer { border-radius:var(--radius,14px); padding:26px 26px; text-align:left; color:var(--ink,#fff);
              background:linear-gradient(160deg,rgba(255,255,255,0.16),rgba(255,255,255,0.07));
              border:1px solid rgba(255,255,255,0.10); }
.gen .layer .ltxt { font-family:"SC-Sub",sans-serif; font-weight:600; }
.gen-ov { inset:auto 56px auto 56px; top:50%; transform:translateY(-50%);
          background:var(--ground,#0e0c0a); opacity:.94; border-radius:var(--radius,26px); padding:64px 48px;
          box-shadow:0 24px 60px rgba(0,0,0,0.55); backdrop-filter:blur(2px); }
"""


def gen_child_count(el: dict) -> int:
    """生成物里会「一条一条进场」的子件有几个。参数决定，不吃素材。"""
    p = el.get("props") or {}
    tpl = el.get("template")
    if tpl == "compare":
        return 2
    if tpl == "steps":
        return len(p.get("items") or [])
    if tpl == "chart":
        return len(p.get("values") or [])
    if tpl == "flow":
        return len(p.get("nodes") or [])
    if tpl == "layers":
        return len(p.get("layers") or [])
    return 1


def gen_child_times(el: dict, amap: dict | None) -> list[float]:
    """子件什么时候进场：跟着这格覆盖到的那几个**音频块的重音点**走。

    原来按「元素时长均分」——话说到第三个词时第二条才弹出来，节奏是死的。
    现在每个子件挂一句真话：读第 i 条的时候，第 i 个子件亮。
    返回相对这格起点的秒数（按时间升序、去重、限制在格内）。
    """
    n = gen_child_count(el)
    if not amap or n <= 1:
        return []
    t0 = float(el.get("at") if el.get("track") == "overlay" else (el.get("_start") or 0))
    d = float(el.get("dur") or 0)
    peaks = []
    for b in amap.get("blocks") or []:
        if b.get("is_filler") or b.get("peak_at") is None:
            continue
        pk = round(float(b["t"]) + (float(b["peak_at"]) - float(b["src_start"])), 3)
        if t0 - 0.05 <= pk <= t0 + d + 0.05:
            peaks.append(round(pk - t0, 3))
    peaks = sorted(set(peaks))
    if len(peaks) < n:
        return []                                  # 话不够，退回均分，不硬凑
    if len(peaks) > n:                             # 均匀取样，别把前几句全挤在开头
        step = (len(peaks) - 1) / (n - 1)
        peaks = [peaks[round(i * step)] for i in range(n)]
    return [max(0.0, min(round(d - 0.25, 3), p_)) for p_ in peaks]


def gen_html(el: dict, H: int, total: float = 0.0, ct: list[float] | None = None) -> str:
    """一张代码画出来的整屏。**只吃参数，不吃素材**——库里没有的画面靠它补。"""
    tpl = el.get("template")
    p = el.get("props") or {}
    ov = el.get("track") == "overlay"
    start = round(float(el.get("at") if ov else (el.get("_start") or 0)), 3)
    dur = round(float(el.get("dur") or 2), 3)
    cls = "gen gen-ov" if ov else "clip gen"
    ct_attr = (f' data-ct="{",".join(f"{x:.2f}" for x in ct)}"' if ct else "")
    carry_attr = ' data-carry="1"' if el.get("carry") else ""
    head = (f'<div class="{cls} gen-{tpl}" data-start="{start}" data-duration="{dur}"'
            f'{ct_attr}{carry_attr}>')
    body = ""
    if tpl == "keyword":
        body = (f'<div class="big" style="font-size:{int(H * 0.105)}px">{esc(p.get("text"))}</div>'
                + (f'<div class="sm" style="font-size:{int(H * 0.026)}px;margin-top:26px">'
                   f'{esc(p.get("sub"))}</div>' if p.get("sub") else ""))
    elif tpl == "compare":
        def fit_px(txt, max_px, max_chars=6, floor_px=0.028):
            """按字数缩字号：11 个字塞进半屏宽，用 0.052H 会折成五行。"""
            n = max(1, len(str(txt or "").strip()))
            return int(max(H * floor_px, max_px * (max_chars / n)))

        def col(side, hi):
            s = p.get(side) or {}
            v = s.get("value")
            return (f'<div class="col{" hi" if hi else ""}">'
                    f'<div class="sm" style="font-size:{int(H * 0.022)}px">{esc(s.get("title"))}</div>'
                    f'<div class="big" style="font-size:{fit_px(v, H * 0.052)}px">{esc(v)}</div></div>')
        body = (f'<div class="cols">{col("left", False)}{col("right", True)}</div>'
                + (f'<div class="sm ct-last" style="font-size:{int(H * 0.024)}px;'
                   f'margin-top:44px">{esc(p.get("note"))}</div>' if p.get("note") else ""))
    elif tpl == "number":
        body = (f'<div class="num" style="font-size:{int(H * 0.16)}px">{esc(p.get("value"))}'
                f'<span style="font-size:{int(H * 0.05)}px">{esc(p.get("unit") or "")}</span></div>'
                f'<div class="sm" style="font-size:{int(H * 0.028)}px;margin-top:30px">{esc(p.get("label"))}</div>')
    elif tpl == "steps":
        items = p.get("items") or []
        lis = "".join(f'<li><b style="font-size:{int(H * 0.03)}px">{i:02d}</b>'
                       f'<span style="font-size:{int(H * 0.034)}px">{esc(t)}</span></li>'
                       for i, t in enumerate(items, 1))
        body = ((f'<div class="sm" style="font-size:{int(H * 0.024)}px;margin-bottom:44px">'
                 f'{esc(p.get("title"))}</div>' if p.get("title") else "") + f'<ol>{lis}</ol>')
    elif tpl == "quote":
        body = (f'<blockquote style="font-size:{int(H * 0.042)}px">“{esc(p.get("text"))}”</blockquote>'
                + (f'<cite style="font-size:{int(H * 0.024)}px;margin-top:40px">—— {esc(p.get("who"))}</cite>'
                   if p.get("who") else ""))
    elif tpl == "chapter":
        body = (f'<div class="chn" style="font-size:{int(H * 0.19)}px">{esc(p.get("n"))}</div>'
                f'<div class="big" style="font-size:{int(H * 0.042)}px;margin-top:18px">{esc(p.get("title"))}</div>')
    elif tpl in ("flow", "layers"):
        items = p.get("items") or []
        if tpl == "flow":
            rows = "".join(
                f'<div class="fnode" data-i="{i}"><span class="dot"></span>'
                f'<span class="ftxt" style="font-size:{int(H * 0.030)}px">{esc(t)}</span></div>'
                for i, t in enumerate(items))
            body = ((f'<div class="sm" style="font-size:{int(H * 0.024)}px;margin-bottom:34px">'
                     f'{esc(p.get("title"))}</div>' if p.get("title") else "")
                    + f'<div class="fcol">{rows}</div>'
                    + (f'<div class="sm" style="font-size:{int(H * 0.022)}px;margin-top:30px;'
                       f'opacity:.75">{esc(p.get("foot"))}</div>' if p.get("foot") else ""))
        else:
            # 剖面：从下往上叠，最底下一层先出现（讲「基层」这种东西时，顺序就是内容）。
            # 注意这里必须先建 list 再 reversed——之前是对拼好的字符串做 split("</div>")，
            # 结果标签全丢，后面那格不算渲出来还被塞进坏结构里（实测 6s 之后整屏黑）。
            rows = [
                f'<div class="layer" data-i="{i}"><div class="sm" style="font-size:{int(H * 0.020)}px;'
                f'opacity:.72;margin-bottom:8px">{"面层" if i == len(items) - 1 else ("中层" if i else "基层")}</div>'
                f'<div class="ltxt" style="font-size:{int(H * 0.028)}px">{esc(t)}</div></div>'
                for i, t in enumerate(items)
            ]
            body = ((f'<div class="sm" style="font-size:{int(H * 0.024)}px;margin-bottom:34px">'
                     f'{esc(p.get("title"))}</div>' if p.get("title") else "")
                    + '<div class="lstack">' + "".join(reversed(rows)) + '</div>'
                    + (f'<div class="sm" style="font-size:{int(H * 0.022)}px;margin-top:30px;'
                       f'opacity:.75">{esc(p.get("foot"))}</div>' if p.get("foot") else ""))
    elif tpl == "chart":
        bars = p.get("bars") or []
        top = max([float(b.get("value") or 0) for b in bars] + [1.0])
        rows = "".join(
            f'<div class="row"><span class="sm" style="font-size:{int(H * 0.022)}px;'
            f'min-width:{int(H * 0.11)}px;text-align:left">{esc(b.get("label"))}</span>'
            f'<div class="bar" style="width:{max(0.03, float(b.get("value") or 0) / top) * 62:.1f}%"></div>'
            f'<span class="sm" style="font-size:{int(H * 0.022)}px">{esc(b.get("value"))}</span></div>'
            for b in bars)
        body = ((f'<div class="sm" style="font-size:{int(H * 0.024)}px;margin-bottom:36px">'
                 f'{esc(p.get("title"))}</div>' if p.get("title") else "")
                + f'<div style="width:100%;display:flex;flex-direction:column;gap:26px">{rows}</div>')
    return head + body + "</div>"


def get_photo(lib: Path, proj: Path, el: dict, *, W: int, H: int, FPS: int,
              grade: str = "") -> dict:
    """一张照片变成一段静帧。HEIC 先转 JPEG——这台 ffmpeg 不认 HEIC。"""
    clips = proj / "hf" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    aid = el["asset"]
    dur = max(0.4, float(el.get("dur") or 3.0))
    tag = "photo_" + "".join(c if c.isalnum() else "_" for c in Path(aid).stem)[-14:]
    dst = clips / f"{tag}_{str(dur).replace('.', 'p')}.mp4"
    if not dst.exists() or dst.stat().st_size < 4096:
        src = lib / aid
        if not src.exists():
            raise SystemExit(f"素材不存在：{src}")
        img = src
        if src.suffix.lower() in (".heic", ".heif"):
            jpg = clips / f"{tag}.jpg"
            if not jpg.exists():
                r = subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(jpg)],
                                   capture_output=True, text=True)
                if r.returncode != 0 or not jpg.exists():
                    raise SystemExit(f"HEIC 转码失败：{src.name}\n{(r.stderr or '')[-400:]}")
            img = jpg
        vf = [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
              f"crop={W}:{H}", "format=yuv420p"]
        if grade:
            vf.append(grade)
        vf += [f"fps={FPS}", "setsar=1"]
        cmd = [ffmpeg_bin(), "-y", "-v", "error", "-loop", "1", "-i", str(img),
               "-t", f"{dur:.3f}", "-vf", ",".join(vf), "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
               str(dst)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            raise SystemExit(f"照片转片段失败：{src.name}\n{(r.stderr or '')[-600:]}")
    info = probe(dst)
    return {"path": dst, "dur": round(info["dur"], 3), "w": info["w"], "h": info["h"]}


def get_clip(lib: Path, proj: Path, seg: dict, force: bool = False,
             extractor: str = "auto", W: int = 1080, H: int = 1920,
             FPS: int = 30, grade: str = "", overscan: float = 1.0) -> dict:
    """把一段切出来。**缓存**：同一段再渲不再切（切一段 6.7s 素材要约 38 秒）。

    切好的片段是"已经标准化的素材"——旋转过的 HEVC 竖屏、杜比视界，到这一步
    都是统一尺寸的 H.264，浏览器只管播。

    extractor：`auto`（有 zscale 就自己切）/ `own` / `hf`（退回 measure extract）。
    """
    clips = proj / "hf" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    aid = seg["asset"]
    tag = f"{Path(aid).stem[-14:]}_{seg['in']:.2f}-{seg['out']:.2f}".replace(".", "p")
    tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
    dst = clips / f"{tag}.mp4"
    # 片段缓存必须知道自己是在哪个 GRADE 下切的。2026-11-09 踩过：调色改了，但缓存
    # 命中，剪出来的还是老画面；紧接着逐镜检查又按新 GRADE 重算增益，在老画面上
    # 再压一遍——颜色被连着校了三次，越校越暗越偏（luma 0.346 → 0.316 → 仍在降），
    # 而日志每行都说成功。
    import hashlib
    _want = hashlib.sha1(f"{grade}|{W}x{H}@{FPS}|os{overscan:.2f}".encode()).hexdigest()[:10]
    _stamp = clips / ".grade"
    if _stamp.exists() and _stamp.read_text().strip() != _want:
        force = True
    if force or not dst.exists() or dst.stat().st_size < 4096:
        src = lib / aid
        if not src.exists():
            raise SystemExit(f"素材不存在：{src}")
        use_own = extractor == "own" or (extractor == "auto" and has_zscale())
        aud = None
        a = seg.get("audio") or {}
        if a.get("asset") and (a["asset"] != aid or float(a.get("in", 0)) != float(seg["in"])):
            a_src = lib / a["asset"]
            if a_src.exists():
                aud = (a_src, float(a.get("in") or 0), float(a.get("out") or 0))
        if use_own:
            extract_own(src, float(seg["in"]), float(seg["out"]), dst,
                        W=int(W), H=int(H), fps=int(FPS), overscan=float(overscan),
                        grade=grade or "", aud=aud)
            _stamp.write_text(_want)
        else:
            r = subprocess.run(
                [*HF.split(), "measure", "extract", str(src),
                 "--from", str(seg["in"]), "--to", str(seg["out"]),
                 "--audio", "--out", str(dst)],
                capture_output=True, text=True)
            if r.returncode != 0 or not dst.exists():
                raise SystemExit(f"切段失败：{aid} {seg['in']}-{seg['out']}\n{r.stderr[-800:]}")
    info = probe(dst)
    return {"path": dst, "dur": round(info["dur"], 3), "w": info["w"], "h": info["h"]}


# ---------------------------------------------------------- 代码画的整屏格的声音

def scene_mp4(proj: Path, el: dict, FPS: int, W: int = 1080, H: int = 1920) -> Path:
    """`template: scene` 的整屏格：画面是 gen_scene.py 画出来的，落到 clips/ 缓存。

    为什么场景要预渲染成 mp4，而不是像其他 gen 模板那样写成一层 `<div>`：
    场景是**逐帧画出来的位图**（几十万个多边形、抹痕、液流），用 CSS/GSAP 做不出来。
    它仍是代码画的、可复现的（同一秒永远同一帧），只是渲染发生在浏览器外面。

    参数变了就重画：缓存名带上 scene / dur / marks，改一个字都不会拿旧片冒充。
    """
    from gen_scene import SCENES
    name = str((el.get("props") or {}).get("scene") or "")
    if name not in SCENES:
        raise SystemExit(f"格 {el.get('n')} 的场景「{name}」不认识（{'/'.join(sorted(SCENES))}）")
    dur = float(el.get("dur") or 0)
    mk = el.get("marks") or (el.get("props") or {}).get("marks") or ""
    if isinstance(mk, (list, tuple)):
        mk = ",".join(f"{float(x):.4f}" for x in mk)
    tag = f"scene-{name}-{dur:.2f}s-{FPS}fps" + (f"-m{str(mk).replace('.', 'p').replace(',', '_')}"
                                                 if mk else "")
    dst = proj / "hf" / "clips" / f"{tag}.mp4"
    if dst.exists() and dst.stat().st_size > 4096:
        return dst
    cmd = [sys.executable, str(Path(__file__).with_name("gen_scene.py")),
           "--scene", name, "--dur", f"{dur:.3f}", "--fps", str(FPS),
           "--size", f"{W}x{H}", "--out", str(dst)]
    if mk:
        cmd += ["--marks", str(mk)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise SystemExit(f"场景没画出来：{name} {dur}s\n{(r.stderr or r.stdout)[-600:]}")
    return dst


def get_audio_cell(lib: Path, proj: Path, seg: dict) -> dict | None:
    """整屏格把画面换掉了，**声音还得接着播**——不然那几秒是哑的。

    实测过 HyperFrames 的音频也走 clip 时间轴：`<audio data-start=2 data-duration=2>`
    在成片里正好 2–4 秒有声、前后静音（/tmp/aud-test）。所以抽一条小 m4a 挂上去。
    """
    a = seg.get("audio") or {}
    aid = a.get("asset") or seg.get("asset")
    if not aid:
        return None
    s_in, s_out = float(a.get("in", seg.get("in") or 0)), float(a.get("out", seg.get("out") or 0))
    if s_out - s_in <= 0.02:
        return None
    tag = f"aud-{Path(aid).stem[-14:]}_{s_in:.2f}-{s_out:.2f}".replace(".", "p")
    tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
    dst = proj / "hf" / "clips" / f"{tag}.m4a"
    if not dst.exists() or dst.stat().st_size < 2048:
        src = lib / aid
        if not src.exists():
            raise SystemExit(f"声音素材不存在：{src}")
        r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error",
                            "-ss", f"{s_in:.3f}", "-t", f"{s_out - s_in:.3f}", "-i", str(src),
                            "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                            str(dst)], capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            raise SystemExit(f"抽声音失败：{aid} {s_in}-{s_out}\n{r.stderr[-500:]}")
    return {"path": dst, "dur": round(s_out - s_in, 3)}


# ---------------------------------------------------------- B 轴人物 PiP

def pip_clip(lib: Path, proj: Path, el: dict, FPS: int, W: int, H: int,
             grade: str = "", crf: int = 17) -> dict | None:
    """B 轴人物 PiP：把口播素材里当前这段人抠出来，缩成小窗浮在代码场景上。

    走 ffmpeg 后贴，**不进 HTML**：Chrome 把带 alpha 的 webm 交给硬件视频管线
    时会丢 alpha（fg 层踩过，见 build_html 里那段注释），PiP 同样是 alpha 层，
    只信 ffmpeg。人像框不猜——取 Vision 遮罩首/中/尾三帧的并集外接框再外扩，
    框落在人身上，不落在脸上猜的位置。

    样式默认「box」（bbox 内不透明画面 + 圆角卡，遮罩只参与取景）；
    `props.pip.mode: "cutout"` 才是浮人抠像——Vision 遮罩在白 T 恤/杂背景
    素材上会撕裂（fg 层看不出来，PiP 换背景就全露，2026-11-11 品红底目检）。

    关掉：`props.pip: false`。调窗：`props.pip: {width, right, bottom_edge}`，
    都是小数占比；bottom_edge 是小窗**底边**的 y 占比，默认 0.62——字幕带顶边
    约 0.646H，小窗底边压在它上面，不抢字幕。
    """
    if str(el.get("axis") or "") != "B":
        return None
    pip = (el.get("props") or {}).get("pip")
    if pip is False:
        return None
    a = el.get("audio") or {}
    aid = a.get("asset") or el.get("asset")
    if not aid:
        return None
    s_in = float(a.get("in", el.get("in") or 0))
    s_out = float(a.get("out", el.get("out") or 0))
    dur = s_out - s_in
    if dur <= 0.30:
        return None
    pip = dict(pip) if isinstance(pip, dict) else {}
    wp = float(pip.get("width") or 0.26)
    mr = float(pip.get("right") or 0.04)
    bed = float(pip.get("bottom_edge") or 0.62)
    mode = str(pip.get("mode") or "box")
    pipw = max(2, int(W * wp) // 2 * 2)
    # 缓存键必须带全部窗口参数（T-005 复核⑥：只按素材+in/out 命中旧缓存，
    # 调 width/right/bottom_edge/mode 不生效，白改）。
    tag = (f"pip-{Path(aid).stem[-14:]}_{s_in:.2f}-{s_out:.2f}"
           f"_{int(wp * 100)}_{int(mr * 100)}_{int(bed * 100)}_{mode}").replace(".", "p")
    tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
    dst = proj / "hf" / "clips" / f"{tag}.webm"
    side = dst.with_suffix(".json")
    if dst.exists() and dst.stat().st_size > 4096 and side.exists():
        try:
            g = json.loads(side.read_text())
            return {"path": dst, "dur": round(dur, 3), "w": g["w"], "h": g["h"],
                    "x": g["x"], "y": g["y"]}
        except (json.JSONDecodeError, KeyError):
            pass
    if not MASK_BIN.exists():
        print("  ！缺 scripts/native/person_mask（swiftc -O person_mask.swift -o person_mask），"
              "B 轴 PiP 跳过", file=sys.stderr)
        return None
    # 复用切片链：HDR/旋转/GRADE 都在里面处理过，PiP 的人跟 A-roll 一个色。
    seg = get_clip(lib, proj, {"asset": aid, "in": s_in, "out": s_out},
                   W=W, H=H, FPS=FPS, grade=grade)
    clip = seg["path"]
    mdir = proj / "hf" / "work" / "mask" / f"pip-{tag}"
    r = subprocess.run([str(MASK_BIN), str(clip), "0", f"{dur:.3f}", str(mdir), str(FPS)],
                       capture_output=True, text=True)
    masks = sorted(mdir.glob("f*.png")) if r.returncode == 0 else []
    if not masks:
        print(f"  ！PiP 遮罩失败：{clip.name}\n{(r.stderr or '')[-200:]}", file=sys.stderr)
        return None
    # 遮罩后处理在 numpy 里做，不走 ffmpeg 滤镜链（目检 2026-11-11）：
    # ① 白 T 恤在 Vision 遮罩里是低置信区（40–150），阈值 150 会把胸口掏出大洞——
    #    fg 层看不出来是因为底下贴的是同一帧画面，PiP 换了背景就全露。
    #    阈值降到 40 把弱区收回来，再闭运算补小洞。
    # ② 人的下缘遮罩本来就有截断，硬切看着像撕裂——裁剪后底部 12% 做 alpha 渐隐，
    #    截断变成刻意的「浮出」。
    # ③ 缩放用 LANCZOS：二值遮罩缩出的是软边，比硬边自然。
    import numpy as np
    from PIL import Image

    def _close(b: "np.ndarray", k: int = 3) -> "np.ndarray":
        for _ in range(k):
            d = b.copy()
            d[1:, :] |= b[:-1, :]; d[:-1, :] |= b[1:, :]
            d[:, 1:] |= b[:, :-1]; d[:, :-1] |= b[:, 1:]
            b = d
        for _ in range(k):
            e = ~b
            d = e.copy()
            d[1:, :] |= e[:-1, :]; d[:-1, :] |= e[1:, :]
            d[:, 1:] |= e[:, :-1]; d[:, :-1] |= e[:, 1:]
            b = ~d
        return b

    vw, vh = seg["w"], seg["h"]
    closed: list[np.ndarray] = []
    box = None
    for f in (masks[0], masks[len(masks) // 2], masks[-1]):
        arr = np.asarray(Image.open(f).convert("L"))
        b = _close(arr > 40)
        closed.append(b)
        ys, xs = np.nonzero(b)
        if len(xs) == 0:
            continue
        bb = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        box = bb if box is None else (min(box[0], bb[0]), min(box[1], bb[1]),
                                      max(box[2], bb[2]), max(box[3], bb[3]))
    if box is None:
        print("  ！PiP 遮罩里没有像素，跳过", file=sys.stderr)
        return None
    pad = int(min(vw, vh) * 0.04)
    raw_h = max(1, box[3] - box[1])
    x0 = max(0, box[0] - pad)
    y0 = max(0, box[1] - pad - int(raw_h * 0.08))  # 头顶留白：头顶贴框看着压抑
    x1 = min(vw - 1, box[2] + pad)
    y1 = min(vh - 1, box[3] + pad)
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    if cw < vw * 0.12 or ch < vh * 0.12:
        print(f"  ！PiP 人像框可疑（{cw}x{ch}，画面 {vw}x{vh}），跳过", file=sys.stderr)
        return None
    ph = max(2, int(round(pipw * ch / cw)) // 2 * 2)
    mode = str(pip.get("mode") or "box")
    if mode != "cutout":
        # 圆角卡样式（默认）：bbox 内的不透明画面，alpha 只提供圆角。
        # 为什么默认 box 而不是 cutout：这个素材的 Vision 遮罩在白 T 恤区域
        # 低置信（躯干被撕开、右腋还沾着背景杂物）——fg 层看不出来是因为
        # 底下贴的是同一帧画面，PiP 换了背景就全露（2026-11-11 品红底目检）。
        # box 不依赖遮罩质量，遮罩只参与取景。要浮人用 props.pip.mode="cutout"。
        from PIL import ImageDraw
        rad = max(8, pipw // 14)
        mm = Image.new("L", (pipw, ph), 0)
        ImageDraw.Draw(mm).rounded_rectangle([0, 0, pipw - 1, ph - 1], radius=rad, fill=255)
        mpng = mdir.with_name(mdir.name + "-rrect.png")
        mm.save(mpng)
        r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(clip),
                            "-loop", "1", "-framerate", str(FPS), "-i", str(mpng),
                            "-filter_complex",
                            f"[0:v]crop={cw}:{ch}:{x0}:{y0},scale={pipw}:{ph}[pc];"
                            "[1:v]format=gray[mk];"
                            "[pc][mk]alphamerge,format=yuva420p[o]",
                            "-map", "[o]", "-frames:v", str(len(masks)),
                            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
                            "-crf", "18", "-auto-alt-ref", "0", "-an", str(dst)],
                           capture_output=True, text=True)
    else:
        # cutout 浮人：逐帧裁剪 + 缩放 + 底部渐隐，写成遮罩序列（小图，PNG 很快）。
        pdir = mdir.with_name(mdir.name + "-crop")
        pdir.mkdir(parents=True, exist_ok=True)
        _fade_from = int(ph * 0.88)
        _ramp = np.ones((ph, pipw), dtype=np.float32)
        if _fade_from < ph:
            _ramp[_fade_from:, :] = np.linspace(1.0, 0.0, ph - _fade_from)[:, None]
        _n_done = 0
        it = iter(closed)
        for i, f in enumerate(masks):
            b = next(it) if i in (0, len(masks) // 2, len(masks) - 1) else \
                _close(np.asarray(Image.open(f).convert("L")) > 40)
            crop = b[y0:y1 + 1, x0:x1 + 1]
            m = Image.fromarray((crop * 255).astype(np.uint8)).resize((pipw, ph), Image.LANCZOS)
            ma = np.asarray(m, dtype=np.float32) * _ramp
            Image.fromarray(ma.astype(np.uint8)).save(pdir / f"f{i:04d}.png")
            _n_done += 1
        r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(clip),
                            "-framerate", str(FPS), "-i", str(pdir / "f%04d.png"),
                            "-filter_complex",
                            f"[0:v]crop={cw}:{ch}:{x0}:{y0},scale={pipw}:{ph}[pc];"
                            "[1:v]format=gray[mk];"
                            "[pc][mk]alphamerge,format=yuva420p[o]",
                            "-map", "[o]", "-frames:v", str(_n_done),
                            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
                            "-crf", "18", "-auto-alt-ref", "0", "-an", str(dst)],
                           capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        print(f"  ！PiP alpha 编码失败：{clip.name}\n{(r.stderr or '')[-300:]}", file=sys.stderr)
        return None
    x = max(0, int(W * (1 - mr)) - pipw)
    y = max(16, int(H * bed) - ph)
    side.write_text(json.dumps({"w": pipw, "h": ph, "x": x, "y": y}))
    return {"path": dst, "dur": round(dur, 3), "w": pipw, "h": ph, "x": x, "y": y}


def pip_overlay(raw: Path, dst: Path, pips: list[dict], crf: int = 17) -> int:
    """把 PiP 层贴回成片。与 cover_fg 同一套纪律：
    ① 读 alpha webm 必须显式 `-c:v libvpx-vp9`（自带解码器不解 alpha）；
    ② PTS 整体挪到成片里的起点（只靠 enable 窗口，到窗口时输入早就 EOF 了）；
    ③ 音频照抄底片（`-map 0:a? -c:a copy`），PiP 本身不发声。"""
    if not pips:
        return 1
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(raw)]
    for p in pips:
        cmd += ["-c:v", "libvpx-vp9", "-i", str(p["path"])]
    parts, prev = [], "[0:v]"
    for i, p in enumerate(pips, 1):
        parts.append(f"[{i}:v]setpts=PTS+{p['start']:.3f}/TB,"
                     f"scale={p['w']}:{p['h']}[pp{i}]")
        parts.append(f"{prev}[pp{i}]overlay=x={p['x']}:y={p['y']}:eof_action=pass:"
                     f"enable='between(t,{p['start']:.3f},{p['start'] + p['dur']:.3f})'[pv{i}]")
        prev = f"[pv{i}]"
    parts.append(f"{prev}format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
            "-c:a", "copy", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ！PiP 贴回失败：" + (r.stderr or "").strip().splitlines()[-1], file=sys.stderr)
        return r.returncode
    return 0


# ------------------------------------------------------------------ 抠像

MASK_BIN = Path(__file__).resolve().parent / "native" / "person_mask"


def make_fg(proj: Path, clip: Path, dur: float, fps: int) -> Path | None:
    """做一层"只有人、背景透明"的视频，用来把大字压到人后面。

    为什么不用 `hyperframes remove-background`：它要下 u2net 的 onnx 权重，
    这台机器下载报 HTTP 504（重试两次都是）。macOS 自带 Vision 的
    人像分割不需要下载任何东西，而且我们早就有这条链（`person_mask.swift`）。
    遮罩 → alphamerge → VP9(yuva420p) webm，WebM 的 alpha_mode 标记就位，
    浏览器能直接当带透明通道的 <video> 播。
    """
    if not MASK_BIN.exists():
        print("  ！缺 scripts/native/person_mask（swiftc -O person_mask.swift -o person_mask），"
              "大字只能压在人前面", file=sys.stderr)
        return None
    dst = clip.with_name(clip.stem + "-fg.webm")
    if dst.exists() and dst.stat().st_mtime > clip.stat().st_mtime and dst.stat().st_size > 4096:
        return dst
    mdir = proj / "hf" / "work" / "mask" / clip.stem
    r = subprocess.run([str(MASK_BIN), str(clip), "0", f"{dur}", str(mdir), str(fps)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not any(mdir.glob("f*.png")):
        print(f"  ！抠像失败：{clip.name}\n{(r.stderr or '')[-300:]}", file=sys.stderr)
        return None
    # 遮罩序列的长度跟着片段走，短一帧就补不了——按实际帧数重取
    n = len(list(mdir.glob("f*.png")))
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-framerate", str(fps),
         "-i", str(mdir / "f%04d.png"),
         "-filter_complex",
         # 遮罩先缩 1px（erosion）：边界像素的颜色是原背景，不缩会在人身边缘
         # 留一圈发虚的色边——底图是同一帧，缩 1px 看不出来，色边却没了。
         # 二值化 + 缩 2px：软遮罩在 4:2:0 的 VP9 里会在人身边缘糊出一圈光晕，
         # 硬边反而干净——底图是同一帧，边界往里收 2px 看不出来，光晕却没了。
         f"[1:v]format=gray,scale={1080}:{1920},lutyuv=y='if(gt(val,150),255,0)',"
         "erosion,erosion[tmp];[tmp]erosion,erosion[mk];"
         "[0:v][mk]alphamerge,format=yuva420p[o]",
         "-map", "[o]", "-frames:v", str(n),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "18",
         "-auto-alt-ref", "0", "-an", str(dst)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        print(f"  ！alpha 编码失败：{clip.name}\n{(r.stderr or '')[-300:]}", file=sys.stderr)
        return None
    return dst


# ------------------------------------------------------------------ 卡片

CARD_LAG = 0.10     # 卡片比它锚定的那个词晚多少秒出场（声音先到）


def card_windows(segs, lib, chars_cache, style, amap=None, ov_win=()):
    """EDL 的 card → (text, start, end, kind)。

    大字什么时候出不由我们拍板，由这句话什么时候被说出来决定：`on` 写这类词里的
    几个字，去逐字时间戳里找它的起止，提前 0.12s 起、停到说完，夹在模板的 hold 区间。
    """
    emph = style.get("emphasis") or {}
    hold = emph.get("hold_s") or style.get("hold_pair") or [1.0, 1.5]
    # 音频层：每段里每个块的「重音点」= 块内能量峰值。大字该落在这儿。
    # 以前大字落在「段首 + 0.6s」，实测离重音 486ms——看着就是「字比话慢半拍」。
    peaks_of: dict[int, list[float]] = {}
    for b in ((amap or {}).get("blocks") or []):
        if b.get("is_filler") or b.get("peak_at") is None:
            continue
        peaks_of.setdefault(int(b["el"]), []).append(
            round(float(b["t"]) + (float(b["peak_at"]) - float(b["src_start"])), 3))
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
        if kind in ("boroll", "card") and not c.get("text"):
            # 代码画的结构卡（gen_boroll）属于已雪藏的那一层，HTML 链路不画它。
            # 悄悄当空卡渲出去会得到一个空的大字 div，所以明确跳过并说一声。
            print(f"  ！段 {s['n']} 卡的类型「{kind}」HTML 链路不支持，跳过", file=sys.stderr)
            continue
        anchor_t = None
        if isinstance(c.get("at"), (int, float)) and not peaks_of:
            start = round(s["_start"] + float(c["at"]), 3)
            anchor_t = round(start - CARD_LAG, 3)      # 手写 at 时锚点＝它自己的起点减一个滞后
        else:
            # 没写 at 就找段内最早的重音点，作为锚点（卡片比它晚 CARD_LAG 出场）
            cand = [p_ for p_ in peaks_of.get(int(s["n"]), [])
                    if s["_start"] - 0.05 <= p_ <= s["_start"] + s.get("_dur_q", 9) + 0.05]
            anchor_t = min(cand) if cand else round(s["_start"] + 0.6, 3)
            # +0.10s：声音先出，画面跟着。提前出现就是「抢拍」——
            # 观众会在词还没说出来的时候先看见字，读到的和听到的对不上。
            start = round(max(s["_start"] + 0.2, anchor_t + CARD_LAG), 3)
        d = max(h0, min(float(c.get("dur") or h1), h1))
        on = str(c.get("on") or "").strip()
        aid = (s.get("audio") or {}).get("asset") or s["asset"]
        a_in = float((s.get("audio") or {}).get("in", s["in"]))
        if on:
            chars = chars_cache.get(aid) or chars_cache.setdefault(aid, load_chars(lib, aid))
            # 锚词比对必须先过词表：卡上写「柒造」、ASR 写「7灶」，不纠就永远对不上，
            # 于是「沿用默认时机」——卡出得比声音早，正是 Jerry 说的「抢拍」。
            chars = lexicon.fix_items(chars, lexicon.load(lib / "_cutskills"))
            win = [(ch, st, en) for ch, st, en in chars
                   if a_in - 0.05 <= st < a_in + s["_dur_q"] + 0.05]
            txt = "".join(ch for ch, _, _ in win)
            j = txt.find(on)
            if j >= 0:
                st0 = win[j][1] - a_in + s["_start"] + CARD_LAG
                st1 = win[min(j + len(on) - 1, len(win) - 1)][2] - a_in + s["_start"]
                start = round(max(s["_start"] + 0.15, st0), 3)
                anchor_t = round(win[j][1] - a_in + s["_start"], 3)
                d = round(min(h1, max(h0, st1 - start + 0.25)), 3)
            else:
                print(f"  ！大字对不上原话「{on}」（段 {s['n']}），沿用默认时机",
                      file=sys.stderr)
        # 一屏只讲一件事：跟 overlay 生成物撞了，大字让开、挪到卡片之后说。
        # 直接删掉会让整片一块大字都没有（实测「卡 0 块」），所以先挪、挪不下才删。
        deferred = None
        for a0, b0 in ov_win:
            if start < b0 - 0.05 and start + d > a0 + 0.05 and kind in ("keyword", "behind", "cutout"):
                seg_end = s["_start"] + s.get("_dur_q", 9)
                shifted = round(max(b0, start), 3)
                room = seg_end - shifted
                if room < 0.8:
                    print(f"  ！大字「{c.get('text')}」段内没地方让给 overlay 卡，撤掉", file=sys.stderr)
                    start, d = None, 0
                else:
                    print(f"  · 大字「{c.get('text')}」让开 overlay 卡："
                          f"{start}→{shifted}s", file=sys.stderr)
                    deferred = round(b0, 3)   # 让位目标：overlay 卡结束的那一刻
                    start, d = shifted, round(min(float(hold[1]), room), 3)
                break
        if start is None:
            continue
        _cd = {"kind": kind, "text": str(c.get("text") or ""),
               "start": start, "end": round(start + d, 3),
               "anchor_word": on or None,
               "anchor_t": round(float(anchor_t), 3) if anchor_t is not None else None,
               "appear_t": round(float(start), 3),
               "lag_ms": (round((start - float(anchor_t)) * 1000)
                          if anchor_t is not None else None)}
        if deferred is not None:
            _cd["deferred_to"] = deferred
        cards.append(_cd)
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
/* 包装 token → CSS 变量。只有一条规矩：变量只喂「穿什么」，不喂「什么时候」。
   代码画出来的格子（.gen）以前颜色是写死的 #F6EB34，换包不换脸——那是假的换装。 */
:root {{ {pack_vars} }}
@font-face {{ font-family: "SC-Sub"; src: url("assets/fonts/{fr}"); font-weight: 600; }}
@font-face {{ font-family: "SC-Big"; src: url("assets/fonts/{fb}"); font-weight: 900; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{W}px; height:{H}px; overflow:hidden; background:#000; }}
#root {{ position:relative; width:100%; height:100%; }}
.clip {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
#grade {{ position:absolute; inset:0; {grade} }}
/* .cap 同时带 .clip，而 .clip 是 inset:0 + height:100% —— 字幕框被撑成整屏高。
   单行时看不出（行盒在框顶），双语竖排时 align-items:center 把整块垂直居中，
   直接推到画面外（实测 y=2254，比 1920 还低一半）：双语双行「从来没出现过」。
   所以这里必须显式收掉 bottom 与 height。 */
.cap {{ position:absolute; left:0; right:0; top:{cap_top}%; bottom:auto; height:auto;
        display:flex; justify-content:center; align-items:flex-start;
        padding:0 {cap_pad}px; }}
/* light_text：奶白字 + 细描边。试过「暗晕」和「纸底」两条路，都不行——
   暗晕压在复杂背景上像一块擦不掉的脏斑（26.0s 那一帧），纸底被 .clip 的
   100% 高度推到画面外。描边是唯一不看背景脸色、又不改版面的做法。 */
.cap.cap-light span, .cap.cap-light .en {{
  paint-order:stroke fill; -webkit-text-stroke:2px rgba(0,0,0,0.45); }}
/* 压在浅底代码画面上：换深墨字，描边也跟着反过来 */
.cap.cap-ink span, .cap.cap-ink .en {{
  color:var(--ink,#1F1B16) !important; -webkit-text-stroke:2px rgba(255,255,255,0.35); }}
.cap.cap-bi {{ flex-direction:column; align-items:center; gap:{cap_en_gap}px; }}
.cap.cap-bi .en {{ font-family:"SC-Sub", sans-serif; font-style:normal; font-weight:600;
  letter-spacing:0.02em; white-space:nowrap; opacity:0.92;
  text-shadow:0 1px 0 rgba(255,255,255,.35); }}
.cap span {{ font-family:"SC-Sub", sans-serif; font-weight:600; font-size:{cap_px}px;
  color:{cap_color}; letter-spacing:0.01em; line-height:1.28; text-align:center;
  text-shadow:{cap_shadow}; white-space:nowrap; {cap_extra} }}
/* 强调：数字与否定/对比词。只染这两类，别的一个字都不动 */
.cap .em {{ color:{cap_em}; font-weight:800; }}
.kw {{ position:absolute; left:0; right:0; top:{kw_top}%; display:flex; justify-content:center;
       padding:0 {cap_pad}px; }}
/* behind：字压在人物脑袋后面，露一半。位置按样式的 top_h，字更大一号，
   下半截交给抠像层盖住——这是「人物前后层级」唯一能被眼睛验证的地方。 */
/* 不给负 z-index：遮挡靠 ffmpeg 的抠像层贴回来实现，DOM 里给它负层会被整块压到视频下面，
   结果就是「字根本没出现」（2026-11-09 实测一整段看不见）。 */
.kw-behind {{ top:{kwb_top}%; }}
.kw-behind span {{ font-size:{kwb_px}px; }}
.kw span {{ font-family:"SC-Big", sans-serif; font-weight:900; font-size:{kw_px}px;
  line-height:1.06; text-align:center; color:{kw_color}; white-space:nowrap;
  text-shadow:0 6px 22px rgba(0,0,0,0.6); {kw_extra} }}
.idcard {{ position:absolute; left:{idc_x}%; top:{idc_y}%; text-align:left; }}
.idcard span {{ display:block; font-family:"SC-Sub", sans-serif; color:#fff; line-height:1.34; }}
{gen_css}
</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0"
       data-width="{W}" data-height="{H}" data-fps="{FPS}" data-duration="{total}">
{videos}
{s_html}
{a_html}
    <div id="grade"></div>
{gens}
{cards}
{fg}
    <div id="caplayer">
{caps}
    </div>
  </div>
<script src="assets/gsap.min.js"></script>
<script>
const tl = gsap.timeline({{ paused: true }});
{punch_js}
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
    {{ scale: 1, opacity: 1, rotate: 0, duration: 0.34, ease: "back.out(2.2)",
       immediateRender: false }}, t);
  tl.to(c.querySelector("span"), {{ opacity: 0, duration: 0.16, ease: "power1.in" }},
        t + Math.max(0, d - 0.16));
}});
document.querySelectorAll(".idcard > span").forEach((el, i) => {{
  tl.fromTo(el, {{ x: -18, opacity: 0 }},
    {{ x: 0, opacity: 1, duration: 0.42, ease: "power3.out",
       immediateRender: false }}, 0.42 + i * 0.08);
}});
// 代码生成的整屏：淡入 + 上移，撤场只降透明。不许有布局动画——
// 整屏的文本量比字幕大得多，一缩放就糊。
// 生成元素的子件按顺序进场：一句一条，不是一屏同时亮
document.querySelectorAll(".gen").forEach((c) => {{
  const t = parseFloat(c.dataset.start);
  // morph_carry：这一格是**接着上一格**的（同一块东西没换），所以不许重新入场。
  // 重新淡入＝观众看到「又一页」，那正是「像 PPT 翻页」的来源。
  const carry = c.dataset.carry === "1";
  const d = parseFloat(c.dataset.duration);
  const kids = c.querySelectorAll(".col, ol li, .bar, .chn, blockquote, .num, .fnode, .layer");
  // 每条子件的入场时刻：优先用「这句台词的重音点」（data-ct），没有才均分。
  const ct = (c.dataset.ct || "").split(",").filter(Boolean).map(Number);
  // 收尾那一行（比如对比屏下面的结论）永远最后出：它得等两栏都比完了才有意义。
  const last = c.querySelector(".ct-last");
  if (last) {{
    const at = (ct.length ? Math.max(...ct) + 0.5 : d * 0.55);
    tl.set(last, {{ opacity: 0 }}, t);
    tl.fromTo(last, {{ opacity: 0 }}, {{ opacity: 1, duration: 0.24, immediateRender: false }},
              t + Math.min(Math.max(0.2, at), Math.max(0.3, d - 0.45)));
  }}
  kids.forEach((k, i) => {{
    const when = (ct.length === kids.length) ? t + ct[i]
      : t + 0.12 + i * Math.min(0.34, Math.max(0.12, (d - 0.5) / Math.max(1, kids.length)));
    tl.set(k, {{ opacity: 0, y: 18 }}, t);          // 轮到之前必须是不可见，否则一开场全亮
    tl.fromTo(k, {{ opacity: 0, y: 18 }},
      {{ opacity: 1, y: 0, duration: 0.28, ease: "power2.out",
         immediateRender: false }}, when);
  }});
  if (carry) {{
    tl.set(c, {{ opacity: 1, y: 0 }}, t);
  }} else {{
    tl.fromTo(c, {{ opacity: 0, y: 24 }},
      {{ opacity: 1, y: 0, duration: 0.32, ease: "power2.out", immediateRender: false }}, t);
  }}
  if (d > 0.8) {{
    tl.to(c, {{ opacity: 0, duration: 0.2, ease: "power1.in" }}, t + d - 0.2);
  }}
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


def build_html(*, slug, W, H, FPS, total, videos, caps, cards, style, fonts, grade,
               punch_map=None,
               fg_by_seg=None, fg_mode="browser", gens=None, scenes=None, audios=None,
               gen_spans=None):
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
    # 「脑后露出一半」：顶边压到 0.30H 附近、字再放大约 15%，让脑袋吃掉字的下半截
    kwb_top = round(float(emph.get("behind_top_h") or 0.285) * 100, 2)
    kwb_px = int(kw_px * float(emph.get("behind_scale") or 1.15))
    fit_w = float(emph.get("fit_width") or 0.70)   # 大字单排占多宽
    entry = str(sub.get("entry") or "fade")
    # immediateRender:false 是必须的。GSAP 的 fromTo 默认立刻渲染 from 状态，
    # 于是整条时间轴在 t=0 就被设成 opacity:0；一旦浏览器的捕获路径某一段没推进
    # GSAP（长片分段渲染时实测会这样），这些层就**永远停在不可见**——
    # 79 秒那次整片 7 块卡一块都没出来，而 snapshot 同一秒是好的。
    # 加上这个参数，元素保持 CSS 里"看得见"的状态，动效只是锦上添花：失败也不会黑掉。
    # 整段硬切：不做位移、淡入只给 100ms（>120ms 就是「漂」而不是「切」）
    tween = ('tl.fromTo(h, { opacity: 0 }, '
             '{ opacity: 1, duration: 0.10, ease: "none", '
             'immediateRender: false }, t);' if entry != "hard_cut" else
             'tl.set(h, { opacity: 1 }, t);')
    idcard = next((c for c in cards if c["kind"] == "idcard"), None)
    # 包装 token → CSS 变量串。缺省值取 warming 那套（P1），所以老样式包不写 tokens 也照跑。
    _tk = style.get("tokens") or {}
    _pal = _tk.get("palette") or {}
    pack_vars = " ".join([
        f"--ground:{_pal.get('ground') or '#1A1410'};",
        f"--ink:{_pal.get('ink') or '#FFFFFF'};",
        f"--accent:{_pal.get('accent') or emph.get('keyword_color') or '#F6EB34'};",
        f"--accent2:{_pal.get('accent2') or '#C1121F'};",
        f"--radius:{int(_tk.get('radius') or 18)}px;",
        f"--stroke:{max(1, int(_tk.get('stroke') or 2))}px;",
    ])
    idc_x = round(float((style.get("_id_card") or {}).get("x") or 0.055) * 100, 2)
    idc_y = round(float((style.get("_id_card") or {}).get("y") or 0.155) * 100, 2)

    v_html = "\n".join(
        f'    <video id="v{v["n"]}" class="clip" src="{v["src"]}" data-start="{v["start"]}" '
        f'data-duration="{v["dur"]}" data-media-start="0" data-track-index="0" '
        f'playsinline data-has-audio="true"></video>'
        for i, v in enumerate(videos, 1))
    # fg_mode="ffmpeg" 时这里不输出 <video>，抠像层改由本脚本在浏览器渲完后用
    # ffmpeg overlay 贴回去。原因：Chrome 把带 alpha 的 webm 交给硬件视频管线时
    # alpha 会被丢掉——画面上看不出（抠像视频和底片是同一帧，画成不透明正好等于原画），
    # 但它会盖住下面所有层：大字、身份卡一块都不出来。实测 snapshot 有、render 没有。
    fg_html = "" if fg_mode != "browser" else "\n".join(
        f'    <video id="fg{i}" class="clip" src="{fg_by_seg[i]}" data-start="{v["start"]}" '
        f'data-duration="{v["dur"]}" data-media-start="0" data-track-index="1" '
        f'playsinline muted></video>'
        for i, v in enumerate(videos, 1) if fg_by_seg and i in fg_by_seg)
    # 代码画的整屏：位图是哑的，所以 muted；声音由下面单独的 <audio> 接着放。
    s_html = "\n".join(
        f'    <video id="s{v["n"]}" class="clip" src="{v["src"]}" data-start="{v["start"]}" '
        f'data-duration="{v["dur"]}" data-media-start="0" data-track-index="0" '
        f'playsinline muted></video>'
        for v in (scenes or []))
    a_html = "\n".join(
        f'    <audio id="a{v["n"]}" src="{v["src"]}" data-start="{v["start"]}" '
        f'data-duration="{v["dur"]}" data-media-start="0" data-track-index="3" '
        f'data-has-audio="true"></audio>'
        for v in (audios or []))
    # 双语双行：中文主行 + 英文小字。第二行只在 cue 真有 en 时才出现——
    # 没英文稿就还是单行（`subtitle_system: bilingual` 不等于「自动有英文」）。
    _sy = style.get("subtitle_system") or {}
    _sy_id = (_sy.get("id") if isinstance(_sy, dict) else str(_sy)) or "single-line"
    _en_px = int((_sy.get("en_size_px") if isinstance(_sy, dict) else 0) or 34)
    # 英文行跟着中文字色走：暗画面上深灰英文等于没有（实测和背景糊在一起）
    _en_col = ((_sy.get("en_color") if isinstance(_sy, dict) else "") or
               ("rgba(246,241,231,0.86)" if (_sy.get("light_text") if isinstance(_sy, dict) else False)
                else "#8A8178"))
    # 代码画面自带 --ground 底色：浅底包（纸感）的奶白字幕压上去等于没有。
    # 按 tokens.palette.ground 的亮度判一次，重叠的 cue 换深墨字——不改版面，只换字色。
    def _ground_lum() -> float:
        g = (((style.get("tokens") or {}).get("palette") or {}).get("ground") or "")
        g = str(g).lstrip("#")
        if len(g) != 6:
            return 0.0
        try:
            r, gg, b = (int(g[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except ValueError:
            return 0.0
        return 0.2126 * r + 0.7152 * gg + 0.0722 * b

    _light_ground = _ground_lum() > 0.55

    def _over_gen(c) -> bool:
        if not (gen_spans and _light_ground and sub.get("light_text")):
            return False
        for s0, d0 in gen_spans:
            ov = min(c["end"], s0 + d0) - max(c["start"], s0)
            if ov > 0.15:
                return True
        return False

    def _cap_inner(c) -> str:
        s = f'<span>{c.get("html") or esc(c["text"])}</span>'
        if _sy_id == "bilingual" and c.get("en"):
            s += (f'<i class="en" style="font-size:{_en_px}px;color:{_en_col}">'
                  f'{esc(c["en"])}</i>')
        return s

    c_html = "\n".join(
        f'      <div id="cap-{i:03d}" class="clip cap'
        f'{" cap-bi" if (_sy_id == "bilingual" and c.get("en")) else ""}'
        f'{" cap-light" if sub.get("light_text") else ""}'
        f'{" cap-ink" if _over_gen(c) else ""}" '
        f'data-start="{c["start"]}" '
        f'data-duration="{round(c["end"] - c["start"], 3)}">'
        + _cap_inner(c)
        + '</div>'
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
            # 大字一律单排、撑到画幅宽度的 70%（Jerry 2026-11-09：「字体还要放大 2-3 倍
            # 能撑满屏幕宽度的 70%，但所有的大字应该控制在一排出现，要进行适当的缩放」）。
            # 做法：先按「70% 宽 ÷ 字数」给字号，再用高度上限兜住 1–2 个字的极端情况。
            # 老做法是「全局字号 163px，超宽才缩」，2 个字的「柒造」只占 37% 宽，显得小气。
            _n = max(1, len(c["text"]))
            _px = min(int(round(W * fit_w / _n)), int(H * 0.34))
            card_html.append(
                f'    <div id="kw-{i:02d}" class="clip kw'
                f'{" kw-behind" if c["kind"] == "behind" else ""}" '
                f'data-start="{c["start"]}" '
                f'data-duration="{d}"><span style="font-size:{_px}px">'
                f'{esc(c["text"])}</span></div>')

    # 跳切推近：硬切式换倍率（不是缓推）。缓推看不出「换了一镜」，
    # 而观众读到换镜靠的就是那一下跳变。transform-origin 在中心，只放大不位移。
    punch_js = ""
    if punch_map:
        for n, plist in punch_map.items():
            for item in plist:
                tt, sc = item[0], item[1]
                dx = item[2] if len(item) > 2 else 0.0
                punch_js += (f'  tl.set("#v{n}", {{ scale: {sc}, x: "{dx}%", '
                             f'transformOrigin: "50% 50%" }}, {tt});\n')
        punch_js = ('// 口播跳切推近：同一段画面换景别，声音不断、时间轴不动\n'
                    + punch_js)

    return CSS_TMPL.format(
        res="portrait" if H > W else "landscape", W=W, H=H, FPS=FPS, total=total, slug=slug,
        fr=fonts["sub"], fb=fonts["big"],
        grade=grade or "background:transparent;",
        cap_top=cap_top, cap_pad=cap_pad, cap_px=cap_px,
        # 纸感包是深墨字压在暗画面上读不出来（实测字幕带 p98 只有 152、字 30）→
        # subtitle.light_text: true 就把字换成奶白 + 深影。换字色不改版面，比垫纸底稳。
        cap_color=("#F6F1E7" if sub.get("light_text") else (sub.get("color") or "#FFFFFF")),
        cap_shadow=("0 3px 12px rgba(0,0,0,0.80)" if sub.get("light_text")
                    else (sub.get("shadow") or "0 3px 8px rgba(0,0,0,.75)")),
        cap_extra="",
        cap_em=sub.get("em_color") or (emph.get("keyword_color") or "#FFD400"),
        kw_top=kw_top, kw_px=kw_px, kwb_top=kwb_top, kwb_px=kwb_px,
        kw_color=emph.get("keyword_color") or "#FFD400",
        kw_extra=("background:linear-gradient(180deg,%s 0%%,%s 100%%);"
                  "-webkit-background-clip:text;color:transparent;" %
                  tuple((emph.get("gradient") or ["#FFE53B", "#FFB000"])[:2])
                  if emph.get("gradient") else ""),
        pack_vars=pack_vars, punch_js=punch_js, cap_en_gap=max(6, int(cap_px * 0.10)),
        idc_x=idc_x, idc_y=idc_y,
        videos=v_html, caps=c_html, cards="\n".join(card_html), fg=fg_html,
        s_html=s_html, a_html=a_html,
        gens="\n".join(gens or []), gen_css=GEN_CSS,
        cap_entry=entry, cap_tween=tween)



# 卡片入场配什么音效：按卡片职责分，别每张都「嗖」一下
# 音效一律取自 CC0 库（Jerry 2026-11-09：「用现有的素材，不要自己生成」）。
# 名字对应 cc0_lib.SFX_MAP；增益由库里的 under_voiceover_db 给，不再拍脑袋写 -11。
# 增益在库给的值上**再抬 1 dB**：垫乐进来之后同一处音效更难听出来（实测同一条
# whoosh 在无垫乐时抬 18 dB、有垫乐时只剩 8–9 dB）。90ms duck 只能管住入场那一瞬。
CARD_SFX = {"keyword": ("pop-medium-in", 0.0), "behind": ("impact-light-firm", 0.0),
            "cutout": ("impact-light-firm", 0.0), "idcard": ("whoosh-medium-soft", 0.0),
            "gen": ("appear-materialise", -1.0)}

# 音效落在连续人声上，光靠调增益听不出来（实测只涨 1–3 dB）：得让声音给它让位。
# 90ms、-6dB，耳朵听不出人声被压，但音效的瞬态就露出来了。
# −6 dB / 90ms。试过 −12 dB / 120ms，结果更差：量出来的「抬升」主要来自底音，
# 把人声压狠了，抬升反而掉 6 dB（实测 24.12s 从 −7.3 掉到 −13.8）。让位要轻。
DUCK_DB = 0.5
DUCK_S = 0.09


def sfx_spec(name: str) -> dict | None:
    """取一条音效的规格：文件、同步点、人声下的增益。

    优先 CC0 库（带 sync_at 与 under_voiceover_db，插进去就能对上画面）；
    库不在才退回本地合成的那三条，并且明确说一声。
    """
    try:
        import cc0_lib
    except ImportError:
        cc0_lib = None
    if cc0_lib and cc0_lib.sfx_available():
        return cc0_lib.pick_sfx(name)
    home = Path(os.environ.get("CUTSKILLS_HOME") or (Path.home() / ".cutskills"))
    d = home / "assets" / "sfx"
    alias = {"pop-medium-in": "pop", "impact-light-firm": "impact",
             "whoosh-medium-soft": "whoosh", "appear-materialise": "whoosh"}
    f = d / f"{alias.get(name, name)}.wav"
    if not f.exists():
        subprocess.run([sys.executable, str(Path(__file__).with_name("gen_sfx.py"))],
                       check=False, capture_output=True, text=True)
    if not f.exists():
        return None
    print(f"  ⚠ 音效库没接上，用本地合成的 {f.name}", file=sys.stderr)
    return {"path": str(f), "id": name, "sync_at": 0.0, "gain_db": -9.0}


def mix_sfx(film: Path, events: list[dict], dst: Path) -> int:
    """把音效按时间点混进成片音轨。**必须发生在响度归一之前**——
    归一之后再混，音效会被一起抹平（实测 whoosh 只涨 1–3 dB，等于没打）。"""
    if not events:
        return 1
    have = []
    for e in events:
        spec = sfx_spec(str(e.get("name") or "keyword"))
        if not spec:
            continue
        p = Path(spec["path"])
        if p.exists():
            have.append((e, p, spec))
    if not have:
        return 1
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(film)]
    for _e, p, _s in have:
        cmd += ["-i", str(p)]
    fc, mixins = [], []
    # 底音（人声+BGM）在每条音效前后让位 90ms
    duck = ",".join(
        f"volume=volume={DUCK_DB}:enable='between(t,{max(0.0, float(e['t']) - 0.02):.3f},"
        f"{max(0.0, float(e['t']) - 0.02) + DUCK_S:.3f})'"
        for e, _p, _s in have)
    fc.append(f"[0:a]{duck or 'anull'}[bed]")
    mixins.append("[bed]")
    for i, (e, _p, spec) in enumerate(have, 1):
        # 起始时刻 = 画面时刻 − sync_at：库里的 sync_at 是「最该对上画面的那一点」，
        # 直接按画面时刻起播会让打击点晚半拍（whoosh 能晚 200ms 以上）。
        start = max(0.0, float(e["t"]) - float(spec.get("sync_at") or 0))
        ms = int(round(start * 1000))
        gain = float(e.get("gain_db") if e.get("gain_db") is not None
                     else (spec.get("gain_db") or -9.0))
        fc.append(f"[{i}:a]adelay={ms}|{ms},volume={gain}dB[s{i}]")
        mixins.append(f"[s{i}]")
    fc.append(f"{''.join(mixins)}amix=inputs={len(mixins)}:duration=first:normalize=0[aout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-ar", "48000", "-ac", "2", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        return 1
    return 0 if probe(dst).get("w") else 1


# ------------------------------------------------------------------ 主流程

def mix_bgm(film: Path, bgm: Path, dst: Path, total: float) -> int:
    """把 BGM 垫到人声下面。

    两步的顺序是硬规则：**先归一 BGM 到 -28 LUFS，再用人声去压它**。
    以前反过来（先混再归一），音效和 BGM 全被归一器抹平，实测 whoosh 只涨 1–3 dB。
    """
    band = film.with_name("_bgm_norm.wav")
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(bgm), "-af",
                        "loudnorm=I=-28:TP=-3:LRA=11", "-ar", "48000", "-ac", "2", str(band)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 1
    fc = ("[1:a]atrim=0:" + f"{total:.3f}" + ",asetpts=N/SR/TB[bg];"
          "[bg][0:a]sidechaincompress=threshold=0.015:ratio=12:attack=8:"
          "release=300:makeup=1[bgd];"
          "[0:a][bgd]amix=inputs=2:duration=first:normalize=0[mix]")
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(film),
                        "-stream_loop", "-1", "-i", str(band),
                        "-filter_complex", fc, "-map", "0:v:0", "-map", "[mix]",
                        "-c:v", "copy", "-ar", "48000", "-ac", "2", str(dst)],
                       capture_output=True, text=True)
    band.unlink(missing_ok=True)
    if r.returncode != 0 or not dst.exists():
        return 1
    # 只 map 音频会把视频丢掉（实测出片 1.9MB，画幅 0×0）——所以这里再验一次有没有画面
    info = probe(dst)
    return 0 if info.get("w") else 1


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

    这条路是踩出来的，三条结论：**

    1. **不能用 `loudnorm` 单遍冲目标**。混音一进来是 −19.8 LUFS / 真峰 −0.92（口播慢、
       句子之间还有 BGM 垫底），要涨 6 dB；loudnorm 一动真峰就顶穿，于是它自己缩回去，
       实测停在 −15.5，差 1.5 dB 还报绿——因为验收那边量不到（见 verify_film 的注释）。
    2. **`alimiter` 必须写 `level=false`**。默认它自带「自动电平补偿」，把限幅又抵消掉，
       限 0.63 出来的真峰照样 +0.3 dBTP。同一批实验里加上这个参数，真峰立刻老实。
    3. 所以做法是**逐遍增益 + 砖墙限幅**：量一次、补差值、限一次，最多 8 遍。
       限幅点取 0.56（−5 dBFS）：AAC 编码的回峰在 −2 dB 限幅点能爬到 +0.7 dBTP，
       实测只有压到 −5 才稳得住（AAC 后 −14.29 LUFS / −2.68 dBTP）。收敛判据 abs(dev) ≤ 0.3。
    """
    def measure(p: Path) -> tuple[float, float]:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
             "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, text=True)
        try:
            t = r.stderr
            j = json.loads(t[t.rindex("{"):t.rindex("}") + 1])
            return float(j["input_i"]), float(j["input_tp"])
        except (ValueError, KeyError):
            return -99.0, 0.0

    cur = src
    last = measure(src)
    for k in range(8):
        dev = I - last[0]
        if abs(dev) <= 0.3 and last[1] <= TP:
            break
        # 往上推的时候多给 60%：限幅会吃掉一部分增益，按差值给永远差一口气（实测每遍只涨 1 dB）。
        g = dev if dev < 0 else dev * 1.6
        nxt = dst.with_suffix(f".ln{k}.wav")
        rc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(cur), "-vn",
             "-af", f"volume={g:.2f}dB,alimiter=limit=0.56:level=false:attack=1:release=50",
             "-c:a", "pcm_s16le", "-ar", "48000", str(nxt)],
            capture_output=True, text=True).returncode
        if rc != 0:
            return rc
        cur = nxt
        last = measure(cur)

    # 最后一次：把处理好的音轨和原画面拼回去（画面直接 copy，不重编）
    rc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(cur),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(dst)],
        capture_output=True, text=True).returncode
    if cur != src:
        cur.unlink(missing_ok=True)
    if rc != 0:
        return rc
    # AAC 后的回峰再量一次：真的超了就带着偏差补一遍（只降不升）
    got = measure(dst)
    if got[1] > TP + 0.3:
        fix = dst.with_suffix(".fix.m4a")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(dst),
             "-af", f"volume={min(0.0, TP - got[1]):.2f}dB,alimiter=limit=0.79:level=false",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(fix)],
            capture_output=True, text=True)
        if fix.exists():
            fix.replace(dst)
            got = measure(dst)
    last = got
    print(f"  响度 {last[0]:.2f} LUFS · 真峰 {last[1]:.2f} dBTP（目标 {I} / {TP}）")
    return 0

def cover_fg(raw: Path, dst: Path, fgs: list[tuple[Path, float, float]], crf: int = 17,
             crop_h: int = 0) -> int:
    """浏览器渲完后把抠像层贴回去。

    fgs: [(webm 路径, 起始秒, 时长秒)]。用 overlay 的 enable 窗口开关，
    每个抠像层只在自己的段里出现，其余时间不参与合成。

    `crop_h`：抠像层只留这么高（从顶上算）。**字幕带必须留在裁剪线以下**——
    2026-11-09 Jerry 指出「字幕不应该应用抠像放在人物后面」：抠像层是在浏览器渲完之后
    整帧贴回去的，它连字幕一起盖住了。抠像本来只为「大字压在脑袋后面」这一件事存在，
    字幕带以下的人体本来就是同一帧原画面，裁掉零损失。
    """
    if not fgs:
        shutil.move(str(raw), str(dst))
        return 0
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(raw)]
    for f, _, _ in fgs:
        # 必须显式指定 libvpx-vp9。ffmpeg 自带的 vp9 解码器**不解 alpha**，
        # 抠像层会整帧变不透明——画面上看不出（和底片是同一帧），但它把下面
        # 的大字和身份卡全盖住。实测：不加这个参数，洋红底色一点透不出来（0%）；
        # 加上之后 87% 正常透出。
        cmd += ["-c:v", "libvpx-vp9", "-i", str(f)]
    parts, prev = [], "[0:v]"
    for i, (_, t0, dur) in enumerate(fgs, 1):
        lab = f"[o{i}]"
        # 抠像层的 PTS 必须整体挪到这一段在成片里的起点。只靠 enable 窗口不管用：
        # 抠像层只有这一段那么长（实测 8.58s），到第 28 秒 overlay 去读它时早就
        # EOF 了，eof_action=pass 就把这一格直接放过——成片看着毫无变化（实测
        # 与底片差异 0.17%，全是编码噪声），而日志照样打印「抠像层贴回 1 段」。
        _crop = f"crop=iw:{crop_h}:0:0," if crop_h else ""
        parts.append(f"[{i}:v]{_crop}setpts=PTS+{t0:.3f}/TB[f{i}]")
        parts.append(f"{prev}[f{i}]overlay=0:0:eof_action=pass:"
                     f"enable='between(t,{t0:.3f},{t0 + dur:.3f})'{lab}")
        prev = lab
    parts.append(f"{prev}format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-c:a", "copy", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ！抠像层贴回失败：" + (r.stderr or "").strip().splitlines()[-1], file=sys.stderr)
        return r.returncode
    print(f"  抠像层贴回 {len(fgs)} 段")
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
    ap.add_argument("--no-sfx", action="store_true", help="不混卡片音效")
    ap.add_argument("--no-loudnorm", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="方案没拍板也硬渲（正常不该用）")
    # 默认**开**着：`auto`＝项目里没有就按情绪从 CC0 库挑一首。
    # 原来默认 None，等于悄悄不出垫乐——连着三条片渲完都没人说，因为报告里没这一项。
    ap.add_argument("--bgm", default="auto",
                    help="BGM：auto＝用项目里的 bgm.wav（没有就从 CC0 库按情绪挑）；none＝不要；也可给文件")
    ap.add_argument("--bgm-mood", default="auto",
                    choices=["auto", "clear", "warm", "minimal", "tense", "hopeful"],
                    help="auto＝按 brief 里讲的事挑情绪（默认）；也可指定")
    ap.add_argument("--reuse-raw", action="store_true",
                    help="复用 out/_hf_raw.mp4，只重做抠像层贴回与响度（不重跑浏览器渲染）")
    ap.add_argument("--hdr", dest="sdr", action="store_false",
                    help="按 HDR 出片（本机 ffmpeg 无 zscale，文字层会整片丢失，不建议）")
    ap.add_argument("--fg-layer", choices=["ffmpeg", "browser", "none"], default="ffmpeg",
                    help="抠像层在哪贴：ffmpeg＝浏览器渲完再用 overlay 贴回去（默认，稳）；"
                         "browser＝写进 HTML 当 <video>（本机 alpha 会丢，会盖住大字）；none＝不要")
    ap.add_argument("--force-extract", action="store_true")
    ap.add_argument("--shot-match", type=float, default=0.7, metavar="STRENGTH",
                    help="逐镜把亮度/白平衡往参考片拉（0=不做，1=全拉）。默认 0.7")
    ap.add_argument("--extractor", choices=["auto", "own", "hf"], default="auto",
                    help="auto=有 zscale 就自己切（HDR 才色调映射）；hf=退回 hyperframes measure extract")
    ap.add_argument("--overscan", type=float, default=1.2, metavar="K",
                    help="切片比画布大几倍（默认 1.2）：跳切推近靠 CSS 放大，"
                         "切片不留富余就会越推越糊。1.0 = 不留富余")
    ap.add_argument("--punch", dest="punch", action="store_true", default=True,
                    help="口播长段拆镜（跳切推近）：默认开。关掉用 --no-punch")
    ap.add_argument("--no-punch", dest="punch", action="store_false",
                    help="不拆镜：一条长口播就一个镜头，「口播镜头中位」会顶在 4.9s")
    ap.add_argument("--no-cutout", action="store_true",
                    help="不做人物抠像层（大字会压在人前面）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    # 方案闸门：Jerry 2026-11-09 定——剪辑和包装方案要先给人看过、被认可，再动手。
    # 片子改一次就是几分钟机器 + 他一次完整观看；方案是文字，改一行几秒钟。
    # 2026-11-09 又叠了一层：认可要绑在**具体时间轴版本**上（lock.json 的哈希），
    # 否则改完一个字节那个「批准」还挂着，等于没批。
    code, why = lockmod.verify(proj)
    ok = code == 0
    if not ok and (proj / "approval.json").exists():
        # 旧项目只有 approval.json：给一条读得懂的提示，不静默放行
        why += "；approval.json 是旧闸门，改跑 `lock.py --lock` 换上锁"
    if not ok and not a.force:
        print(f"还没拍板：{why}。\n"
              "  先跑 `python3 scripts/plan_card.py --project …` 出方案，"
              "人看过之后 `--approve`（会顺手写 lock.json）。\n"
              "  （确实要硬渲就加 --force。）", file=sys.stderr)
        return 4
    if ok:
        print(f"  {why}", file=sys.stderr)
    lib = resolve_lib(proj, a.library)
    elements, tl_src = load_elements(proj)

    # 三正交里的「包装」：timeline.pack → packs/<id>.yaml；老项目没有 pack 就退回 styles/<style_pack>.yaml。
    # 这条顺序很重要——包装换了只换分层参数，**不许碰时间**（判据 41：换 look 切点序列不变）。
    tl_doc = json.loads((proj / ("timeline.json" if tl_src == "timeline.json"
                                 else "edl.json")).read_text())
    pack_id = str(tl_doc.get("pack") or "").strip()
    if not pack_id:                      # 菜单选过但还没回写时间轴：认 brief.json 的
        _bp = proj / "brief.json"
        if _bp.exists():
            try:
                pack_id = str((json.loads(_bp.read_text()) or {}).get("pack") or "").strip()
            except json.JSONDecodeError:
                pack_id = ""
    name = a.style or (pack_id if pack_id and (SKILL / "packs" / f"{pack_id}.yaml").exists()
                       else None) or tl_doc.get("style_pack") or "talking-head-v1"
    style_path = (SKILL / "packs" / f"{name}.yaml") if (SKILL / "packs" / f"{name}.yaml").exists() \
        else (SKILL / "styles" / f"{name}.yaml")
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

    segs = elements[:a.segments] if a.segments else elements

    # 1) 逐格造画面（带缓存）。**只有 clip / image 才切素材**：gen 是代码画的，
    # silence 是什么都不给（黑底），它们只占时间。v4 之前这里默认每一格都有视频。
    hf = proj / "hf"
    (hf / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    # 音频层要早于格循环载入：生成物的子件入场时刻靠它（重音点）
    amap = None
    amp = proj / "audio-map.json"
    if amp.exists():
        try:
            amap = json.loads(amp.read_text())
        except json.JSONDecodeError:
            amap = None
    videos, gens, clips_like, t, n_fail = [], [], [], 0.0, 0
    gen_spans = []   # 代码画面占用的时间段：字幕压上去要换字色
    scenes, audios, pips = [], [], []   # 整屏代码格 + 口播原声 + B 轴人物小窗
    gd = GRADE.get(str(style.get("grade") or ""), "")
    for el in segs:
        kind = el.get("kind") or "clip"
        dur = el_dur(el)
        if dur <= 0:
            print(f"  ！格 {el.get('n')} 时长 {dur}，跳过", file=sys.stderr)
            n_fail += 1
            continue
        if not (kind == "gen" and (el.get("track") or "video") == "overlay"):
            el["_start"] = round(t, 3)
        if kind in ("clip", "image"):
            try:
                if kind == "image":
                    cl = get_photo(lib, proj, el, W=W, H=H, FPS=FPS, grade=gd)
                else:
                    cl = get_clip(lib, proj, el, force=a.force_extract, extractor=a.extractor,
                                  W=W, H=H, FPS=FPS, grade=gd, overscan=a.overscan)
            except SystemExit as e:
                print(f"  ！格 {el.get('n')} 造不出来：{e}", file=sys.stderr)
                n_fail += 1
                continue
            videos.append({"n": el["n"], "src": f"clips/{cl['path'].name}",
                           "start": round(t, 3), "dur": cl["dur"]})
            el["_dur_q"] = cl["dur"]
            clips_like.append(el)
            t = round(t + cl["dur"], 3)
        elif kind == "gen":
            if (el.get("track") or "video") == "overlay":
                el["_start"] = round(float(el.get("at") or 0.0), 3)
                gen_spans.append((el["_start"], round(float(el.get("dur") or 0.0), 3)))
                gens.append(gen_html(el, H, ct=gen_child_times(el, amap)))
                el["_start"] = round(float(el.get("at") or 0.0), 3)
                el.pop("_pads", None)
            elif str(el.get("template") or "") == "scene":
                # 场景生成器画的整屏：位图片子，走 <video>；口播原声单独挂 <audio>，
                # 否则换画面的那几秒会哑掉（口播片的画面可以换，声音一秒都不能断）。
                try:
                    sp = scene_mp4(proj, el, FPS, W, H)
                except SystemExit as e:
                    print(f"  ！格 {el['n']} 的场景没画出来：{e}", file=sys.stderr)
                    n_fail += 1
                    continue
                sdur = round(probe(sp)["dur"], 3)
                scenes.append({"n": el["n"], "src": f"clips/{sp.name}",
                               "start": round(t, 3), "dur": sdur})
                gen_spans.append((round(t, 3), sdur))
                el["_dur_q"] = sdur
                ac = get_audio_cell(lib, proj, el)
                if ac:
                    audios.append({"n": el["n"], "src": f"clips/{ac['path'].name}",
                                   "start": round(t, 3), "dur": ac["dur"]})
                else:
                    print(f"  ！格 {el['n']} 是整屏代码画面，却没给 audio："
                          f"这几秒会没有声音", file=sys.stderr)
                pc = pip_clip(lib, proj, el, FPS, W, H, grade=a.grade)
                if pc:
                    pips.append({"n": el["n"], "path": str(pc["path"]),
                                 "start": round(t, 3), "dur": pc["dur"],
                                 "w": pc["w"], "h": pc["h"], "x": pc["x"], "y": pc["y"]})
                    print(f"  PiP 格 {el['n']}  小窗 {pc['w']}x{pc['h']} @ ({pc['x']},{pc['y']})")
                t = round(t + sdur, 3)
            else:
                gens.append(gen_html(el, H, ct=gen_child_times(el, amap)))
                gen_spans.append((round(t, 3), round(dur, 3)))
                t = round(t + dur, 3)
        elif kind == "silence":
            t = round(t + dur, 3)
        else:
            raise SystemExit(f"格 {el.get('n')} 的 kind「{kind}」不认识（clip/image/gen/silence）")
    total = round(t, 3)
    # scenes 也算画面：B 轴片（整支都是代码画的连续场景）就是只有 scenes 的情况，
    # 以前漏了它，于是「一块石灰基的一生」这种片子直接报「一格画面都没造出来」。
    if not videos and not gens and not scenes:
        raise SystemExit("一格画面都没造出来")

    index = json.loads((lib / "_cutskills" / "index.json").read_text())
    by_id = {x["id"]: x for x in index["assets"]}
    by_alias = {al: x for x in index["assets"] for al in (x.get("aliases") or [])}
    chars_cache: dict[str, list] = {}

    sub = style.get("subtitle") or {}
    if sub.get("chars_soft"):
        soft_c, hard_c = int(sub["chars_soft"]), int(sub["max_chars_per_line"])
    else:
        soft_c, hard_c = 6, 9
    # 字幕和卡片的「说话人」不只是 clip：整屏代码画面盖住的那几秒，人还在说话，
    # 字幕必须接着走（否则 4–5 秒没有字幕，验收会报「无字幕空档」）。
    carriers = [s for s in clips_like if s.get("_start") is not None]
    for el in segs:
        if (el.get("kind") == "gen" and el.get("track") != "overlay"
                and el.get("_start") is not None and el.get("audio")):
            au = el["audio"]
            carriers.append({"n": el["n"], "_start": el["_start"],
                             "_dur_q": el.get("_dur_q") or el_dur(el),
                             "asset": au.get("asset") or el.get("asset"),
                             "in": au.get("in"), "out": au.get("out"),
                             "audio": au, "medium": "talking"})
    active = sorted(carriers, key=lambda s: s["_start"])
    # 2026-11-09 换实现：按呼吸组切、去标点、单行、数字与否定词变色（scripts/subs.py）。
    # render_film.build_cues 是保留路径，仍按字数软断——主链路不再用它。
    # 重点词变色：Jerry 2026-11-09 取消（「字幕的重点词变色还是取消，不应该只作用于否定词和数字」）。
    # 样式里 subtitle.colorize_emphasis 控制；关掉之后一条字幕里一个字都不变色。
    colorize = bool(sub.get("colorize_emphasis", True))
    # 字幕体系是包装包的一等公民（`subtitle_system.id`）：
    #   single-line —— 现行：单行白字幕
    #   bilingual   —— 双语双行：**要真有一行英文**。没有英文稿时跑出来还是单行，
    #                  不许假装（渲染会打一行提示，check_packs 也会判黄）
    #   none        —— 关掉字幕层（P3 暗调：大字即字幕）
    # 2026-11-11 对抗式审查抓到的：这个字段以前只有样张脚本和检查器读，渲染器根本不认，
    # 于是「无字幕层」这套包渲出来照样有字幕——配置写了一件事，片子做了另一件事。
    # 双语第二行的**唯一来源**是项目里的 `subs-en.json`（{"1": "No grand theories, but..."}，
    # 键是字幕序号）。没有它就没有第二行——不许拿机翻糊上去，也不许假装有。
    _en_p = proj / "subs-en.json"
    _en_map = {}
    if _en_p.exists():
        try:
            _en_map = json.loads(_en_p.read_text()) or {}
        except json.JSONDecodeError:
            _en_map = {}
    _ss = style.get("subtitle_system") or {}
    ss_id = (_ss.get("id") if isinstance(_ss, dict) else str(_ss)) or "single-line"
    caps = subs.build(active, by_id, chars_cache, lib, load_chars, colorize=colorize, fps=FPS)
    caps = [c for c in caps if c["start"] < total]
    # 英文挂到 cue 上（必须在 caps 建好之后——挂早了一个变量都没定义就崩了）
    if _en_map:
        for _i, _c in enumerate(caps, 1):
            _e = (_en_map.get(str(_i)) or _en_map.get(_c.get("text") or "") or "").strip()
            if _e:
                _c["en"] = _e
    # subs.json 记的是**这句片子里真实存在的字幕**，不是「这一轮渲了几条」。
    # 以前这里直接 caps = []，于是拿 P3 试渲一次，项目的 18 条字幕就被写成 0 条——
    # 换个包装看一眼，数据没了（2026-11-11 对抗式审查抓到，且真的踩了一次）。
    caps_json = caps
    caps_suppressed = False
    if ss_id == "none":
        print(f"  字幕体系 none：关掉字幕层（{len(caps)} 条不渲，靠大字/卡片承担台词）",
              file=sys.stderr)
        caps, caps_suppressed = [], True
    elif ss_id == "bilingual" and caps and not any(c.get("en") for c in caps):
        print("  字幕体系 bilingual：但字幕里没有英文，这一版渲出来仍是单行"
              "（要双语得先有英文稿——subs.py 认 cue 的 en 字段）", file=sys.stderr)
    for c in caps:
        c["end"] = min(round(c["end"], 3), total)

    # 口播长段拆镜（跳切推近）—— HyperFrames 链一直缺这一步，所以「口播镜头中位」
    # 永远顶在 4.9s（目标 2.4s）。做法不是再切素材，而是**同一段画面换景别**：
    # 在一个句内 2–2.8s 处硬切一次，倍率换一档（1.0 → 1.12）。眼睛看到的是换镜，
    # 声音一点没断，时间轴一格都没动（所以包装/骨架都不会因此错位）。
    # 20 支参考片就是这么干的：口播中位 2.83s、画面变化中位 2.19s 几乎相等。
    _plan_id = str(tl_doc.get("plan") or "").strip()
    _pspec = {}
    _pp = SKILL / "plans" / f"{_plan_id}.yaml"
    if _pp.exists():
        try:
            _pspec = yaml.safe_load(_pp.read_text()) or {}
        except yaml.YAMLError:
            _pspec = {}
    _pl_tempo = _pspec.get("tempo") or {}
    _pl_shot = _pspec.get("shot_logic") or {}
    _punch_target = float(_pl_tempo.get("target_median_s")
                          or (style.get("shots") or {}).get("talking_median_s") or 2.4)
    _punch_pair = _pl_shot.get("punch_scale") or [1.0, 1.06]
    _jump = str(_pl_shot.get("jump_cut") or "normal")
    _punch_ratio = {"dense": 1.25, "normal": 1.45, "sparse": 1.8}.get(_jump, 1.45)
    shots_out, punch_map = [], {}
    snap_hit = snap_try = 0   # 吸附命中/尝试：0/7 这种数字必须打出来，别再说「已吸附」
    for v in videos:
        el = next((e for e in segs if int(e.get("n") or 0) == int(v["n"])), {})
        medium = str(el.get("medium") or "")
        d = float(v["dur"])
        cuts = []
        # B-roll 也拆：参考片的画面变化中位是 2.19s，不分口播还是空镜。
        # 我们 long B-roll 一镜 5–7.6s，只拆口播的话「全部镜头」中位仍顶在 3.5s。
        # 拆法一样是换景别（不换素材、不动时间轴）；代码画面不拆——那是连续场景，
        # 中间切一刀正好把它要讲的「一件事在变」打断。
        if (a.punch and medium in ("talking", "broll", "photo")
                and d > _punch_target * _punch_ratio):
            k = max(2, int(round(d / _punch_target)))
            aid = (el.get("audio") or {}).get("asset") or el.get("asset")
            starts = [c[1] for c in (chars_cache.get(aid) or [])] if aid else []
            # 字级时间戳是**原始整段素材**的绝对时间（例如 436.26s 起），
            # 而 want 是从这一刀自己 0.0 起算的相对秒数。以前直接拿两者比大小，
            # 差值几百秒，吸附条件恒为假 → 声称的「吸附字界」100% 静默失效（实测 0/7）。
            a_in = float((el.get("audio") or {}).get("in") or el.get("in") or 0.0)
            rel = [0.0]
            for j in range(1, k):
                want = d * j / k
                if starts:
                    # 切点吸附到字界：切在词中间最刺眼（画面换的那一下刚好切掉半个字）
                    near = min(starts, key=lambda x: abs(x - (a_in + want)))
                    if abs(near - (a_in + want)) <= 0.35:
                        want = round(near - a_in, 3)
                        snap_hit += 1
                snap_try += 1
                if want - rel[-1] >= 0.8 and d - want >= 0.8:
                    rel.append(round(want, 3))
            rel.append(round(d, 3))
            cuts = rel[1:-1]
        # 带一点横向位移：纯中心放大读作「缓慢推镜头」，加 2% 位移才读作「换了一镜」。
        # 位移交替方向，别让整支片往同一边偏。
        punches = [(round(float(v["start"]) + r, 3),
                    float(_punch_pair[(i + 1) % len(_punch_pair)]),
                    (2.0 if (i % 2 == 0) else -2.0))
                   for i, r in enumerate(cuts)]
        if punches:
            punch_map[int(v["n"])] = punches
        bnds = [0.0] + cuts + [round(d, 3)]
        for i in range(len(bnds) - 1):
            shots_out.append({"n": len(shots_out) + 1, "seg": int(v["n"]),
                              "in": round(bnds[i], 3), "out": round(bnds[i + 1], 3),
                              "dur": round(bnds[i + 1] - bnds[i], 3),
                              "zoom": (_punch_pair[i % len(_punch_pair)]
                                       if cuts else 1.0),
                              "medium": medium or "unknown"})
    # 整屏代码画面也是「观众看到换了画面」，得进镜头清单：否则「最长同介质连续」
    # 会把两段口播之间的代码画面当成没换过介质，越判越松。
    for v in (scenes or []):
        shots_out.append({"n": len(shots_out) + 1, "seg": int(v["n"]),
                          "in": 0.0, "out": round(float(v["dur"]), 3),
                          "dur": round(float(v["dur"]), 3), "zoom": 1.0, "medium": "gen"})
    if snap_try:
        print(f"  切点吸附字界：{snap_hit}/{snap_try}（{snap_hit / snap_try * 100:.0f}%）"
              f"· 其余按机械等分", file=sys.stderr)
    shots_out.sort(key=lambda x: (x["seg"], x["in"]))
    for i, s_ in enumerate(shots_out, 1):
        s_["n"] = i
    if shots_out:
        (proj / "out").mkdir(parents=True, exist_ok=True)
        (proj / "out" / "shots.json").write_text(
            json.dumps(shots_out, ensure_ascii=False, indent=1))
        _tk = [s for s in shots_out if s["medium"] == "talking"]
        if _tk:
            _dm = sorted(x["dur"] for x in _tk)[len(_tk) // 2]
            print(f"  口播拆镜：{len(_tk)} 个镜头，中位 {_dm:.2f}s（目标 {_punch_target}s）"
                  f"· 只换景别，时间轴未动")

    # overlay 生成物占着的那几秒，大字必须先让开（同屏只讲一件事：
    # 实测黄字压在对比屏上，卡里的小字全糊成一团）。
    ov_win = [(float(el.get("at") or 0.0), round(float(el.get("at") or 0.0) + el_dur(el), 3))
              for el in segs if el.get("kind") == "gen"
              and (el.get("track") or "video") == "overlay"]
    cards = card_windows(active, lib, chars_cache, style, amap=amap, ov_win=ov_win)

    # 大字放人物背后：给这些段做一层带 alpha 的人像，叠在卡上面、字幕下面。
    # 层级：底(全画幅) → 卡 → 人像(透明背景) → 字幕。
    fg_by_seg = {}
    if not a.no_cutout:
        want = {int(s_["n"]): s_ for s_ in active
                if str((s_.get("card") or {}).get("kind") or "") in ("behind", "cutout")}
        for n_, s_ in want.items():
            cl = next((v for v in videos if v["n"] == n_), None)
            if not cl:
                continue
            if a.fg_layer == "none":
                continue
            fgp = make_fg(proj, proj / "hf" / cl["src"], cl["dur"], FPS)
            if fgp:
                fg_by_seg[n_] = f"clips/{fgp.name}"
                print(f"  抠像 段 {n_}（{fgp.stat().st_size / 1e6:.1f} MB）")

    # 字幕轨落盘：verify_film.py 读的是 out/subs.json，两条链路必须吐同一个东西，
    # 否则 D 段会拿 ffmpeg 那版留下的旧字幕去算密度（实测 136 条/分钟）。
    (proj / "out").mkdir(parents=True, exist_ok=True)
    # 语速这两个数必须与「这套包装有没有字幕」无关：P3（大字即字幕）渲一次，
    # 以前会把 chars_per_s 写成 0，导致别的成片验收时被判「字幕密度 26 条/分钟、期望 0」。
    _stat_cues = caps_json or caps
    speech_chars = sum(len(c["text"]) for c in _stat_cues)
    speech_s = sum(min(c["end"], total) - c["start"] for c in _stat_cues)
    ctr = float(sub.get("center_y_px") or H * 0.674)
    (proj / "out" / "subs.json").write_text(json.dumps({
        "cues": caps_json,
        "suppressed": caps_suppressed,
        "subtitle_system": ss_id,
        "band": [max(0, int(ctr) - 260), min(H, int(ctr) + 260)],
        "font": fonts_hint(style), "size": int(sub.get("size_px") or 0),
        "overflow": [],
        "speech_chars": speech_chars,
        "avg_chars": round(speech_chars / len(_stat_cues), 2) if _stat_cues else 0,
        "chars_per_s": round(speech_chars / speech_s, 2) if speech_s else 0,
        "colorize": colorize,
        "engine": "hyperframes",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (proj / "out" / "cards.json").write_text(json.dumps(
        {"cards": cards, "engine": "hyperframes"}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # 1b) 逐镜校对亮度与白平衡（整片一个平均值不够——口播段和工地段差一倍）
    if a.shot_match > 0 and videos:
        # 戳里带上 GRADE 的指纹：调色一改，上一轮的逐镜增益就作废了。
        # 只按 strength 命名会一直复用旧增益（量的是旧画面、套在新画面上）。
        import hashlib
        import shot_match as _sm
        _g = (GRADE.get(str(style.get("grade") or "")) or "") + repr(_sm.GLOBAL_GAIN)
        stamp = hf / "clips" / (f".shot-match-{a.shot_match:.2f}-"
                                f"{hashlib.sha1(_g.encode()).hexdigest()[:8]}")
        if not stamp.exists() or a.force_extract:
            r = subprocess.run([sys.executable, str(Path(__file__).with_name("shot_match.py")),
                                "--clips", str(hf / "clips"),
                                "--strength", str(a.shot_match), "--apply"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  ！逐镜校对失败（不影响出片，会把色温跳动留着）：\n{r.stderr[-500:]}",
                      file=sys.stderr)
            else:
                print("  " + (r.stdout.strip().splitlines() or [""])[-1])
                stamp.write_text("ok")

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
    html_txt = build_html(slug=proj.name, W=W, H=H, FPS=FPS,
                          total=total, videos=videos, caps=caps, cards=cards,
                          style=style, fonts=fonts, grade=a.grade, fg_by_seg=fg_by_seg,
                          punch_map=punch_map,
                          fg_mode=a.fg_layer, gens=gens, scenes=scenes, audios=audios,
                          gen_spans=gen_spans)
    (hf / "index.html").write_text(html_txt)
    (hf / "meta.json").write_text(json.dumps(
        {"from": tl_src, "style": name, "elements": len(segs),
         "clips": len(videos), "gen": len(gens), "total_s": total,
         "captions": len(caps), "cards": len(cards),
         "pips": pips,
         # 先落 false，贴回成功（5.6 步）才改 true——中途崩了验收必须红，不能假绿
         "pips_applied": False,
         "fonts": fonts, "engine": "hyperframes"}, ensure_ascii=False, indent=1))

    kinds: dict[str, int] = {}
    for el in segs:
        kinds[el.get("kind") or "clip"] = kinds.get(el.get("kind") or "clip", 0) + 1
    print(f"  格子 {len(segs)}（" + " · ".join(f"{k} {v}" for k, v in kinds.items())
          + f"）· {total}s · 字幕 {len(caps)} 条 · 卡 {len(cards)} 块"
          + (f" · 代码画面 {len(scenes)} 格" if scenes else "")
          + (f" · 没造出来 {n_fail} 格" if n_fail else ""))

    # 4) lint
    # `npx` 在两条链路同时跑时会撞「Lock compromised / ECOMPROMISED」——那不是 HTML 的错，
    # 是 npm 自己的缓存锁。以前这条会被当成「lint 1 error，先别渲」，查半天 HTML。
    # 现在认出这个错就等一会儿重试，重试仍失败才认。
    out = ""
    for attempt in range(3):
        r = subprocess.run([*HF.split(), "lint"], cwd=hf, capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        if "ECOMPROMISED" not in out and "Lock compromised" not in out:
            break
        time.sleep(4 + 4 * attempt)
        r.returncode = 0
    m = re.search(r"(\d+)\s+error", out)
    n_err = int(m.group(1)) if m else (0 if r.returncode == 0 else 1)
    mw = re.search(r"(\d+)\s+warning", out)
    n_warn = int(mw.group(1)) if mw else 0
    print(f"  lint：{n_err} error · {n_warn} warning")
    if n_err or r.returncode != 0:
        print("\n".join(out.splitlines()[-30:]), file=sys.stderr)
        if not a.no_render:
            # 说清是「HTML 有错」还是「lint 进程自己退非 0」——这两种要查的地方完全不同。
            # 2026-11-11 有一次 0 error 却退出码 1，屏幕上只留下「lint 没过」四个字，
            # 读到的人根本分不清是片子的问题还是工具的问题。
            why = (f"lint 报了 {n_err} 个 error" if n_err
                   else f"lint 0 error 但进程退出码 {r.returncode}"
                        f"（不是 HTML 的错；npx 缓存锁 / 二进制异常都会这样，重试一次）")
            raise SystemExit(f"lint 没过，先别渲：{why}")

    if a.no_render:
        print(f"  → {hf/'index.html'}")
        return

    # 5) 渲
    raw = proj / "out" / "_hf_raw.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PRODUCER_BROWSER_GPU_MODE=os.environ.get(
        "PRODUCER_BROWSER_GPU_MODE", "hardware"))
    cmd = [*HF.split(), "render", "-o", str(raw), "-q", a.quality, "-w", str(a.workers)]
    # 必须 --sdr。素材里有 45 段是 HLG/杜比视界（bt2020），HyperFrames 一旦判定
    # hasHdrContent 就走 capture_hdr_layered 那条路：本机 ffmpeg 没有 zscale，
    # 结果是**整片一个字都不出来**——字幕、身份卡、大字全丢（1 秒窗口反而正常，
    # 所以一开始被误判成「分段渲染太长的偶发问题」）。加 --sdr 走普通截屏路径，
    # 同一秒字幕白像素 0.0% → 1.28%、大字黄 0.03% → 6.98%。
    if a.sdr:
        cmd.append("--sdr")
    if a.reuse_raw and raw.exists():
        print(f"  复用 {raw.name}（跳过浏览器渲染）")
        r = None
    else:
        print(f"  渲染中（{a.quality}, {a.workers} workers）…")
        r = subprocess.run(cmd, cwd=hf, capture_output=True, text=True, env=env)
    if r is not None and (r.returncode != 0 or not raw.exists()):
        tail = ((r.stdout or "") + (r.stderr or "")).splitlines()[-25:]
        raise SystemExit("渲染失败：\n" + "\n".join(tail))
    for ln in ((r.stdout if r else "") or "").splitlines():
        if "rendered in" in ln:
            print("  " + ln.strip())

    # 5.5) 抠像层：ffmpeg 贴回
    if a.fg_layer == "ffmpeg" and fg_by_seg:
        fgs = [(hf / fg_by_seg[v["n"]], v["start"], v["dur"])
               for v in videos if v["n"] in fg_by_seg]
        fgd = proj / "out" / "_hf_fg.mp4"
        # 裁剪线压在字幕带顶边之上：字幕中心 0.674H、字号 80px，顶边约 0.65H。
        _ctr = float(sub.get("center_y_px") or H * 0.674)
        _sz = float(sub.get("size_px") or 80)
        _crop_h = max(2, int(round(_ctr - _sz * 0.95)) // 2 * 2)
        if cover_fg(raw, fgd, fgs, crop_h=_crop_h) == 0:
            raw = fgd
        else:
            print("  ！抠像层没贴上，成片不带大字在人物背后", file=sys.stderr)

    # 5.6) B 轴人物 PiP：ffmpeg 贴回。alpha 层不过浏览器，理由同 5.5 的 fg 层；
    # 位置/尺寸在 pip_clip 里按遮罩框定死，这里只照单贴。
    # meta 里 pips_applied 只有贴回成功后才写 true（T-005 复核①：原来 meta 在
    # 贴回之前就写 pips，贴失败验收还拿着声明当真值）。
    if pips:
        pd = proj / "out" / "_hf_pip.mp4"
        _mp = hf / "meta.json"
        _mj = json.loads(_mp.read_text())
        if pip_overlay(raw, pd, pips) == 0:
            raw = pd
            _mj["pips_applied"] = True
            print(f"  PiP 贴回 {len(pips)} 格人物小窗")
        else:
            _mj["pips_applied"] = False
            print("  ！PiP 没贴上，成片只有代码场景没有人物小窗", file=sys.stderr)
        _mp.write_text(json.dumps(_mj, ensure_ascii=False, indent=1))

    # 5.8) BGM：先归一，再让人声压住它
    bgm_p = None
    if a.bgm == "auto":
        cand = proj / "bgm.wav"
        stamp = proj / "bgm.json"
        # 只判「文件在不在」会一直用上一版垫乐——2026-11-09 就撞过：情绪换了一整套，
        # bgm.wav 还是旧的无人机嗡鸣，渲染照旧混那一条，谁都没发现。
        want = None
        if stamp.exists():
            try:
                want = json.loads(stamp.read_text())
            except json.JSONDecodeError:
                want = None
        need = (not cand.exists() or not want
                or want.get("mood") != a.bgm_mood
                or abs(float(want.get("dur") or 0) - total) > 0.05)
        if need:
            # 垫乐从 CC0 库里挑（Jerry 2026-11-09：「用现有的素材，不要自己生成」）。
            # 合成那条路留着，只在库没接上时兜底，并且必须说出来——悄悄换回合成
            # 会让人以为「库里挑的那首不好听」，其实是根本没接上。
            picked = None
            try:
                import cc0_lib
                if cc0_lib.bgm_available():
                    picked = cc0_lib.pick_bgm(a.bgm_mood, total, seed=f"{proj.name}-{a.bgm_mood}")
            except ImportError:
                picked = None
            if picked:
                r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", picked["path"],
                                    "-t", f"{total:.3f}", "-ar", "48000", "-ac", "2",
                                    str(cand)], capture_output=True, text=True)
                if r.returncode == 0 and cand.exists():
                    stamp.write_text(json.dumps(
                        {"mood": a.bgm_mood, "dur": total, "source": "cc0",
                         "title": picked["title"], "file": picked["path"],
                         "why": picked["why"]}, ensure_ascii=False, indent=1))
                    print(f"  BGM 选自 CC0 库：{picked['title']}"
                          f"（{picked['category']}｜{picked['why']}）")
                else:
                    picked = None
            if not picked:
                subprocess.run([sys.executable, str(Path(__file__).with_name("bgm_gen.py")),
                                "--project", str(proj), "--mood", a.bgm_mood], check=False)
                print("  ⚠ BGM 库没接上，退回代码合成", file=sys.stderr)
        bgm_p = cand if cand.exists() else None
    elif a.bgm and a.bgm != "none":
        bgm_p = Path(a.bgm).expanduser()
        # 版权风险只提示、不阻断（Jerry 2026-11-09 定：给风险，但别拦着干活）
        synth_made = (bgm_p.exists() and bgm_p.name == "bgm.wav")
        if not synth_made:
            print(f"  ⚠ BGM 用的是外部文件「{bgm_p.name}」：发布前自己确认授权，"
                  f"商用平台的版权识别会盯这一段。风险我记进 decisions.md，不拦你。",
                  file=sys.stderr)
            try:
                from log_decision import append_row
                append_row(proj, said=f"BGM 指定外部文件：{bgm_p.name}",
                           did="按指定混入；版权风险已提示，未做授权核验", verdict="pending")
            except Exception:
                pass
    if bgm_p and bgm_p.exists():
        bed = proj / "out" / "_hf_bed.mp4"
        if mix_bgm(raw, bgm_p, bed, total) == 0:
            raw = bed
            print(f"  BGM {bgm_p.name}（垫乐 -28 LUFS，人声 12:1 压住——只给底，不抢话）")
        else:
            print("  ！BGM 没混上，成片不带垫乐", file=sys.stderr)

    # 5.9) 卡片音效：每张卡入场一个，落在卡片入场 ±40ms 内
    sfx_events = []
    for c in cards:
        name, gain = CARD_SFX.get(str(c.get("kind") or "keyword"), CARD_SFX["keyword"])
        sfx_events.append({"t": float(c["start"]), "name": name, "gain_db": gain,
                           "for": str(c.get("text") or "")[:12]})
    for el in segs:
        if el.get("kind") != "gen":
            continue
        # overlay 的生成物按它的 `at` 出声；整屏代码画面（含 scene）按它落在时间轴上的位置出声。
        # 漏掉 scene 这句，两处整屏画面会硬邦邦地「啪」一下换过去——画面动了，耳朵没收到。
        if (el.get("track") or "video") == "overlay":
            tt = float(el.get("at") or 0)
        elif el.get("_start") is not None:
            tt = float(el["_start"])
        else:
            continue
        # 整屏代码画面是**换镜头**，不是弹出一张小卡：配 whoosh 而不是 appear-materialise。
        # 实测 appear-materialise 落在这个切点上，2–8 kHz 只抬 2.4 dB（听不出来），
        # 同样的位置换 whoosh 才够。
        nm = ("whoosh-medium-soft" if el.get("template") == "scene" else CARD_SFX["gen"][0])
        sfx_events.append({"t": tt, "name": nm,
                           "gain_db": CARD_SFX["gen"][1],
                           "for": f"gen-{el.get('template') or 'overlay'}-{el.get('n')}"})
    sfx_events = [e for e in sfx_events if 0 <= e["t"] <= total]
    if sfx_events and not a.no_sfx:
        sxd = proj / "out" / "_hf_sfx.mp4"
        if mix_sfx(raw, sfx_events, sxd) == 0:
            raw = sxd
            print(f"  音效 {len(sfx_events)} 处（每张卡入场一个，先混再归一）")
        else:
            print("  ！音效没混上，成片不带音效", file=sys.stderr)
    (proj / "out" / "sfx.json").write_text(json.dumps(
        {"events": sfx_events}, ensure_ascii=False, indent=1), encoding="utf-8")

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
    raise SystemExit(main())
