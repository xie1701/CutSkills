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
    "warm_high_sat": "eq=saturation=1.50:contrast=1.10:gamma=0.85:brightness=-0.086",
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
                crf: int = 16, W: int = 1080, H: int = 1920,
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
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
          f"crop={W}:{H}"]
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
       justify-content:center; align-items:center; background:#14110f; color:#fff;
       padding:0 88px; text-align:center; }
.gen .big { font-family:"SC-Big",sans-serif; font-weight:900; line-height:1.04;
            letter-spacing:0.01em; }
.gen .sm { font-family:"SC-Sub",sans-serif; font-weight:600; line-height:1.4;
           color:rgba(255,255,255,0.82); }
.gen .cols { display:flex; width:100%; gap:40px; align-items:stretch; }
.gen .col { flex:1; display:flex; flex-direction:column; justify-content:center;
            gap:18px; padding:48px 24px; border-radius:18px; background:rgba(255,255,255,0.06); }
.gen .col.hi { background:rgba(246,235,52,0.14); outline:2px solid rgba(246,235,52,0.55); }
.gen .row { display:flex; align-items:baseline; gap:26px; width:100%; }
.gen .bar { height:38px; border-radius:6px; background:#F6EB34; }
.gen .chn { font-family:"SC-Big",sans-serif; font-weight:900; color:#F6EB34;
            opacity:0.92; line-height:1; }
.gen .num { font-family:"SC-Big",sans-serif; font-weight:900; color:#F6EB34; line-height:1; }
.gen blockquote { font-family:"SC-Big",sans-serif; font-weight:900; line-height:1.3; }
.gen cite { font-family:"SC-Sub",sans-serif; font-style:normal; opacity:0.7; }
.gen ol { list-style:none; width:100%; display:flex; flex-direction:column; gap:26px; }
.gen ol li { display:flex; gap:26px; align-items:baseline; }
.gen ol li b { font-family:"SC-Big",sans-serif; color:#F6EB34; }
.gen .fcol { display:flex; flex-direction:column; gap:34px; width:100%; align-items:flex-start; }
.gen .fnode { display:flex; align-items:center; gap:24px; position:relative; }
.gen .fnode .dot { width:26px; height:26px; border-radius:50%; background:#F6EB34; flex:0 0 26px;
                   box-shadow:0 0 0 6px rgba(246,235,52,0.16); }
.gen .fnode::after { content:""; position:absolute; left:12px; top:26px; width:2px; height:34px;
                     background:linear-gradient(180deg,rgba(246,235,52,.55),rgba(246,235,52,0));
                     transform-origin:top; }
.gen .fnode:last-child::after { display:none; }
.gen .lstack { display:flex; flex-direction:column; gap:12px; width:100%; }
.gen .layer { border-radius:14px; padding:26px 26px; text-align:left; color:#fff;
              background:linear-gradient(160deg,rgba(255,255,255,0.16),rgba(255,255,255,0.07));
              border:1px solid rgba(255,255,255,0.10); }
.gen .layer .ltxt { font-family:"SC-Sub",sans-serif; font-weight:600; }
.gen-ov { inset:auto 56px auto 56px; top:50%; transform:translateY(-50%);
          background:rgba(14,12,10,0.88); border-radius:26px; padding:64px 48px;
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
    head = (f'<div class="{cls} gen-{tpl}" data-start="{start}" data-duration="{dur}"{ct_attr}>')
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
             FPS: int = 30, grade: str = "") -> dict:
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
                        W=int(W), H=int(H), fps=int(FPS), grade=grade or "", aud=aud)
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
        if isinstance(c.get("at"), (int, float)) and not peaks_of:
            start = round(s["_start"] + float(c["at"]), 3)
        else:
            # 没写 at 就找最近的块内重音点，提前 0.12s 起（字要比声音早一点点才不显慢）
            cand = [p_ for p_ in peaks_of.get(int(s["n"]), [])
                    if s["_start"] - 0.05 <= p_ <= s["_start"] + s.get("_dur_q", 9) + 0.05]
            start = round(max(s["_start"] + 0.2, (min(cand) if cand else s["_start"] + 0.6) - 0.12), 3)
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
               "start": start, "end": round(start + d, 3)}
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
{gen_css}
</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0"
       data-width="{W}" data-height="{H}" data-fps="{FPS}" data-duration="{total}">
{videos}
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
  tl.fromTo(c, {{ opacity: 0, y: 24 }},
    {{ opacity: 1, y: 0, duration: 0.32, ease: "power2.out", immediateRender: false }}, t);
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
               fg_by_seg=None, fg_mode="browser", gens=None):
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
    # immediateRender:false 是必须的。GSAP 的 fromTo 默认立刻渲染 from 状态，
    # 于是整条时间轴在 t=0 就被设成 opacity:0；一旦浏览器的捕获路径某一段没推进
    # GSAP（长片分段渲染时实测会这样），这些层就**永远停在不可见**——
    # 79 秒那次整片 7 块卡一块都没出来，而 snapshot 同一秒是好的。
    # 加上这个参数，元素保持 CSS 里"看得见"的状态，动效只是锦上添花：失败也不会黑掉。
    tween = ('tl.fromTo(h, { y: 16, opacity: 0 }, '
             '{ y: 0, opacity: 1, duration: 0.18, ease: "power3.out", '
             'immediateRender: false }, t);' if entry != "hard_cut" else
             'tl.set(h, { y: 0, opacity: 1 }, t);')
    idcard = next((c for c in cards if c["kind"] == "idcard"), None)
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
        videos=v_html, caps=c_html, cards="\n".join(card_html), fg=fg_html,
        gens="\n".join(gens or []), gen_css=GEN_CSS,
        cap_entry=entry, cap_tween=tween)


# ------------------------------------------------------------------ 主流程

def mix_bgm(film: Path, bgm: Path, dst: Path, total: float) -> int:
    """把 BGM 垫到人声下面。

    两步的顺序是硬规则：**先归一 BGM 到 -24 LUFS，再用人声去压它**。
    以前反过来（先混再归一），音效和 BGM 全被归一器抹平，实测 whoosh 只涨 1–3 dB。
    """
    band = film.with_name("_bgm_norm.wav")
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(bgm), "-af",
                        "loudnorm=I=-24:TP=-2:LRA=11", "-ar", "48000", "-ac", "2", str(band)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 1
    fc = ("[1:a]atrim=0:" + f"{total:.3f}" + ",asetpts=N/SR/TB[bg];"
          "[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=10:"
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


def cover_fg(raw: Path, dst: Path, fgs: list[tuple[Path, float, float]], crf: int = 17) -> int:
    """浏览器渲完后把抠像层贴回去。

    fgs: [(webm 路径, 起始秒, 时长秒)]。用 overlay 的 enable 窗口开关，
    每个抠像层只在自己的段里出现，其余时间不参与合成。
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
        parts.append(f"{prev}[{i}:v]overlay=0:0:eof_action=pass:"
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
    ap.add_argument("--no-loudnorm", action="store_true")
    ap.add_argument("--bgm", default=None, help="BGM 文件；给 auto 就用项目里的 bgm.wav")
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
    ap.add_argument("--no-cutout", action="store_true",
                    help="不做人物抠像层（大字会压在人前面）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = resolve_lib(proj, a.library)
    elements, tl_src = load_elements(proj)

    name = a.style or json.loads((proj / ("timeline.json" if tl_src == "timeline.json"
                                           else "edl.json")).read_text()).get("style_pack") \
        or "talking-head-v1"
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
                                  W=W, H=H, FPS=FPS, grade=gd)
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
            gens.append(gen_html(el, H, ct=gen_child_times(el, amap)))
            if (el.get("track") or "video") == "overlay":
                el["_start"] = round(float(el.get("at") or 0.0), 3)
                el.pop("_pads", None)
            else:
                t = round(t + dur, 3)
        elif kind == "silence":
            t = round(t + dur, 3)
        else:
            raise SystemExit(f"格 {el.get('n')} 的 kind「{kind}」不认识（clip/image/gen/silence）")
    total = round(t, 3)
    if not videos and not gens:
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
    active = [s for s in clips_like if s.get("_start") is not None]
    caps = build_cues(active, by_id, chars_cache, lib, hard_c, soft_c)
    caps = [c for c in caps if c["start"] < total]
    for c in caps:
        c["end"] = min(round(c["end"], 3), total)

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

    # 1b) 逐镜校对亮度与白平衡（整片一个平均值不够——口播段和工地段差一倍）
    if a.shot_match > 0 and videos:
        stamp = hf / "clips" / f".shot-match-{a.shot_match:.2f}"
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
                          fg_mode=a.fg_layer, gens=gens)
    (hf / "index.html").write_text(html_txt)
    (hf / "meta.json").write_text(json.dumps(
        {"from": tl_src, "style": name, "elements": len(segs),
         "clips": len(videos), "gen": len(gens), "total_s": total,
         "captions": len(caps), "cards": len(cards),
         "fonts": fonts, "engine": "hyperframes"}, ensure_ascii=False, indent=1))

    kinds: dict[str, int] = {}
    for el in segs:
        kinds[el.get("kind") or "clip"] = kinds.get(el.get("kind") or "clip", 0) + 1
    print(f"  格子 {len(segs)}（" + " · ".join(f"{k} {v}" for k, v in kinds.items())
          + f"）· {total}s · 字幕 {len(caps)} 条 · 卡 {len(cards)} 块"
          + (f" · 没造出来 {n_fail} 格" if n_fail else ""))

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
        if cover_fg(raw, fgd, fgs) == 0:
            raw = fgd
        else:
            print("  ！抠像层没贴上，成片不带大字在人物背后", file=sys.stderr)

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
            subprocess.run([sys.executable, str(Path(__file__).with_name("bgm_gen.py")),
                            "--project", str(proj), "--mood", a.bgm_mood], check=False)
            try:
                mood_now = json.loads(stamp.read_text()).get("mood") if stamp.exists() else a.bgm_mood
            except (json.JSONDecodeError, OSError):
                mood_now = a.bgm_mood
            print(f"  BGM 重新合成：{mood_now}")
        bgm_p = cand if cand.exists() else None
    elif a.bgm:
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
            print(f"  BGM {bgm_p.name}（-24 LUFS 归一后由人声压住）")
        else:
            print("  ！BGM 没混上，成片不带垫乐", file=sys.stderr)

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
