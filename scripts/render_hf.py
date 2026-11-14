#!/usr/bin/env python3
"""主渲染链路：timeline.json → HyperFrames 合成（HTML + GSAP + SVG）→ mp4。

分工：
- **素材标准化**归 ffmpeg：HDR→bt709 色调映射、旋转、切段、逐镜调色校白平衡。
- **字幕切条与字级对齐**走 `subs.build` + `evidence_guard.chars_of_asset` 单一真值入口。
- **合成、排版、动效、标题条、矢量情绪贴纸、镜头光效**交给浏览器（HyperFrames + GSAP）。

用法：
    python3 scripts/render_hf.py --project <项目> [--library <库>] [--quality looks] [--bgm auto]
"""
from __future__ import annotations

import argparse, html as _html, json, os, re, shutil, subprocess, sys, time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
import yaml  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libs import ffmpeg_bin as _libs_ffmpeg, hf_bin as _libs_hf, resolve_lib  # noqa: E402
from evidence_guard import load_chars as _eg_load_chars  # noqa: E402
import lock as lockmod                                 # noqa: E402
import lexicon                                        # noqa: E402
import subs                                            # noqa: E402
import card_layout                                     # noqa: E402  B7 叠加态布局（T-019）
import stickers as stickers_mod                        # noqa: E402  网感标题条/贴纸/VFX

SKILL = Path(__file__).resolve().parent.parent


def load_chars(lib: Path, aid: str) -> list[tuple[str, float, float]]:
    """统一走 evidence_guard.load_chars 单一真值入口（含 transcript.words / 新键 / legacy_key）。"""
    return _eg_load_chars(lib, aid)


def _resolve_hf() -> str:
    return _libs_hf()


HF = _resolve_hf()


# ------------------------------------------------------------------ 字体

FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
             Path("/System/Library/Fonts")]


def find_font(name: str) -> Path | None:
    """样式包里写的是字体名（NotoSansSC-SemiBold / 得意黑），这里找到真文件。

    别名（得意黑、思源黑体、思源宋体）先过 styles/fonts.yaml——里面还记着宽度比，
    字号靠它反算。直接写文件名也认。

    HTML 不吃 `local()`：lint 会报 `font_family_without_font_face`，
    所以必须有一份能进 @font-face 的文件，复制进项目的 assets/fonts。
    """
    if not name:
        return None
    try:
        import fonts as fonts_mod
        alias = fonts_mod.file_of(name)
        if alias:
            name = alias
    except Exception:                                              # noqa: BLE001
        pass
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
    # 2026-11-09 按参考片逐秒抽帧的数校准过（当时那个量测脚本 compare_tones.py 已退役，
    # 逐镜调色现由 shot_match.py 承担；下面三个数作为标定真值保留，不按饱和度校准）：
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
    # 百万博主通透网感调色：微提对比与质感通透度，肤色干净不发黄溢出
    "cinema_crisp": "eq=saturation=0.96:contrast=1.06:gamma=0.95:brightness=-0.020",
}

# HLG / PQ → 场景线性（npl=100 是把 100% 白当参考）→ hable 压到 SDR 亮度 → bt709。
# desat=0：保留饱和度。默认 desat=2 会把高光洗白，那正是我们要避免的。
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,"
           "zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def ffmpeg_bin() -> str:
    """优先 CUTSKILLS_FFMPEG，其次 $CUTSKILLS_HOME 自编 ffmpeg-zimg，最后 PATH。"""
    env = os.environ.get("CUTSKILLS_FFMPEG")
    if env and Path(env).exists():
        return env
    return _libs_ffmpeg()


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
                grade: str = "", aud: tuple[Path, float, float] | None = None,
                vpad: float = 0.0, silent: bool = False) -> None:
    """自己切一段：HDR 走 zscale 色调映射，旋转由 ffmpeg 自动应用。

    `-ss` 放在 `-i` 前面（快，从关键帧起解到位置再丢），`-t` 给时长。
    宽高保持原样——竖屏 4K 进 1080×1920 的缩放交给浏览器/CSS，不做两次重采样。

    `vpad` / `silent` 只给 J-cut 解耦用（画面层单独一条）：
      · `vpad>0`：**画面**多切 `vpad` 秒（下一镜的声音先进、画面后切时，上一镜的画面
        要多留这几帧），声音那一路仍然只读 `dur`——输入级 `-t` 各管各的输入，
        声音的起点与样本数一个都不动；素材不够长时 `tpad` 复制末帧补齐，不留黑。
      · `silent=True`：输出 `-an`，这条文件只供「画面层」。
    两个参数都为默认值时，命令行与解耦前**逐字符相同**（老项目零改动）。
    """
    col = probe_color(src)
    hdr = col["transfer"] in HDR_TRANSFERS
    if hdr and not has_zscale():
        raise SystemExit(
            "素材是 HDR，但这支 ffmpeg 没有 zscale，压出来的片子会偏亮偏灰。\n"
            "装法见 bash scripts/runtime.sh install（依赖版本见 deps.json），"
            "或加 --extractor hf 退回旧路径。")
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
    vpad = max(0.0, float(vpad))
    if vpad > 0:
        vf = vf[:-1] + [vf[-1], f"tpad=stop_mode=clone:stop_duration={vpad:.3f}"]
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t0:.3f}"]
    if vpad > 0:
        # 输入级 -t：只限这一路输入读多长。画面读 dur+vpad；声音另开一路只读 dur。
        cmd += ["-t", f"{round(dur + vpad, 3):.3f}"]
    cmd += ["-i", str(src)]
    if aud and not silent:
        a_src, a0, _a1 = aud
        cmd += ["-ss", f"{a0:.3f}"]
        if vpad > 0:
            cmd += ["-t", f"{dur:.3f}"]
        cmd += ["-i", str(a_src)]
    if vpad <= 0:
        # 老路径：输出级 -t，画面与声音同一个窗口（解耦前就是这么咬死的）
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vf", ",".join(vf),
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709"]
    if aud and not silent:
        # 解耦时不能带 -shortest：那会把画面按声音长度砍回去，vpad 就白要了。
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        if vpad <= 0:
            cmd += ["-shortest"]
    if silent:
        cmd += ["-an"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise SystemExit(f"切片失败：{src.name} {t0}-{t1}\n{(r.stderr or '')[-900:]}")


# ------------------------------------------------------------------ 时间轴


def jcut_leads(segs: list[dict]) -> dict[int, tuple[int, int]]:
    """元素号 → (本格提前量, 下一格提前量)，单位帧。overlay 不占时间轴，跳过。

    `timeline.apply_jcut` 只在**后一句**那个元素上写 `pic_lead`。渲器要的却是
    「本格晚几帧」+「下一格晚几帧」：后者决定这一格的画面要往后多留多久。
    """
    order = [e for e in segs if (e.get("track") or "video") != "overlay"]

    def lead_of(e: dict | None) -> int:
        if not e or e.get("kind") not in ("clip", "image"):
            return 0
        return max(0, int(e.get("pic_lead") or 0))

    out: dict[int, tuple[int, int]] = {}
    for k, e in enumerate(order):
        out[e.get("n")] = (lead_of(e), lead_of(order[k + 1] if k + 1 < len(order) else None))
    return out


def pic_window(t: float, dur: float, lead: int, lead_next: int,
               fps: float = 30.0) -> tuple[float, float, float]:
    """一格的**画面**窗口：`(起点, 时长, 素材入点)`。声音窗口仍是 `(t, dur)`，不在这里。

    三个数字的来历：
      · 起点 = `t + lead/fps`——下一句的声音先进 `lead` 帧，画面才切过来；
      · 素材入点 = `lead/fps`——画面内容与声音**同一时刻对齐**，只是切得晚（不是倒放、
        不是滞后 100ms 的唇形）；
      · 时长 = `dur + (lead_next - lead)/fps`——上一格多留的那几帧从这一格的窗口里减去，
        于是相邻两格画面首尾相接：不留空、不重叠、全片总长一格不多一格不少。
    `scripts/check_timing.picture_bounds` 用的是同一条公式（判据和产线一份）。
    """
    fps = float(fps or 30.0)
    lead = max(0, int(lead))
    lead_next = max(0, int(lead_next))
    return (round(t + lead / fps, 3),
            round(dur + (lead_next - lead) / fps, 3),
            round(lead / fps, 3))


def load_elements(proj: Path) -> tuple[list[dict], str]:
    """读取项目的 timeline.json 元素列表。"""
    tlp = proj / "timeline.json"
    if not tlp.exists():
        raise SystemExit(f"找不到 {tlp}——请先运行 `python3 scripts/timeline.py --project {proj}`")
    tl = json.loads(tlp.read_text())
    els = tl.get("elements") or []
    if not els:
        raise SystemExit(f"{tlp} 里一个元素都没有")
    return els, "timeline.json"


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
.gen .mstack { display:grid; justify-items:center; align-content:center; width:100%; }
.gen .mstack > * { grid-area:1/1; }
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
/* 天平（balance）：一杆横杆 + 两只托盘 + 一组砝码。
   「比的对象换了，结论就变了」这句话库里没有画面能拍——但它是**量的关系**，
   所以用称东西来演：先只比原料价，左边沉下去（差 4–5 倍）；口径换成成品总价，
   右边补上几块砝码，横杆回平。形状即语义：一边沉一边轻 = 不等价。 */
.gen-balance { padding:0 40px; }
.gen-balance .bal-wrap { width:100%; display:flex; flex-direction:column; align-items:center; gap:10px; }
.gen-balance svg { width:100%; height:auto; overflow:visible; }
.gen-balance .bal-note { font-family:"SC-Sub",sans-serif; font-weight:700;
  font-size:34px; color:rgba(255,255,255,.92); letter-spacing:.02em;
  text-shadow:0 2px 10px rgba(0,0,0,.55); }
"""


def gen_child_count(el: dict) -> int:
    """生成物里会「一条一条进场」的子件有几个。参数决定，不吃素材。"""
    p = el.get("props") or {}
    tpl = el.get("template")
    if tpl == "compare":
        return 2
    if tpl == "balance":
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


def _micro_stages(el: dict, tpl_html: str) -> list[str]:
    """micro 文字接力：把 {t,text} 序列渲染成同格叠放的换字层（GSAP 按 data-t 接力换字）。
    长卡的大字按台词气口分几段换着出，避免整段死屏——判据与渲器共用同一份 micro。"""
    out = []
    for it in (el.get("micro") or []):
        if isinstance(it, dict) and it.get("text") is not None:
            out.append(str(tpl_html).replace("{t}", f"{float(it.get('t') or 0):.2f}")
                       .replace("{}", esc(str(it["text"]))))
    return out


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
        _kw_tpl = f'<div class="big" data-t="{{t}}" style="font-size:{int(H * 0.105)}px">{{}}</div>'
        stg = _micro_stages(el, _kw_tpl)
        if stg:
            body = (f'<div class="mstack">{"".join(stg)}</div>'
                    + (f'<div class="sm" style="font-size:{int(H * 0.026)}px;margin-top:26px">'
                       f'{esc(p.get("sub"))}</div>' if p.get("sub") else ""))
        else:
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
    elif tpl == "balance":
        # 天平：SVG 画实体，GSAP 只负责倾/平与砝码落下。坐标是 900×480 的 SVG 单位，
        # 随屏宽等比缩放。
        # 2026-11-14 实测纠错：第一版把托盘与砝码画在横杆**上方**（砝码 383–458、横杆 592），
        # 吊线朝上，整个天平是倒着的——这就是它读起来像图标的直接原因。现在几何链是：
        # 吊线挂杆端（y162）→ 盘沿（y300）→ 砝码坐在盘里 → 往下才是标签。
        # 伪 3D 只有一件事：每个实体的「顶面 + 正面 + 侧面」三块，加接地阴影。
        L = p.get("left") or {}
        R = p.get("right") or {}
        acc = str((p or {}).get("accent") or "#F6EB34")
        BW, BH, BD, STEP = 64, 14, 6, 12      # 砝码宽 / 高 / 进深 / 码垛步距
        # 倾角取负：SVG 里正角是顺时针，正角会把**左**端抬起来。而这张卡的语义是
        # 「只比原料价 → 左边沉」，所以左端必须往下走（2026-11-14 第一版把方向写反了，
        # 结果杆往一头倒、盘往另一头掉，吊线两端差了 72px——量出来的）。
        RIM_Y, TILT, TILT_DY = 300, -7, 36      # 盘沿 y / 倾角 / 杆端纵向位移（≈300·sin7°）
        CX_L, CX_R = 150, 750                 # 两只盘的中心 x（杆半长 300）
        NB = 5                                # 每盘砝码块数

        def blk(cx: float, y: float, late: bool) -> str:
            """一块砝码：接地遮蔽 + 顶/正/侧三面 + 上棱倒角高光 + 底棱暗线。

            三面律是「不像卡片」的下限；再多两条才像实物：块与块之间的接触遮蔽
            （每块底棱一条暗线）和左棱一道窄反光。
            """
            x = cx - BW / 2
            return ('<g class="%s">'
                    '<ellipse cx="%.1f" cy="%.1f" rx="%d" ry="4" fill="#000" opacity=".55"'
                    ' filter="url(#bsoft)"/>'
                    '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="url(#btop)"/>'
                    '<rect x="%.1f" y="%.1f" width="%d" height="%d" fill="url(#bfront)"/>'
                    '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="url(#bside)"/>'
                    '<rect x="%.1f" y="%.1f" width="%d" height="1.6" fill="rgba(255,255,255,.42)"/>'
                    '<rect x="%.1f" y="%.1f" width="%d" height="1.6" fill="rgba(0,0,0,.42)"/>'
                    '<rect x="%.1f" y="%.1f" width="1.4" height="%d" fill="rgba(255,255,255,.18)"/>'
                    '</g>') % (
                "blk late" if late else "blk",
                cx, y + BH + 1, BW // 2, x, y, x + BD, y - BD, x + BW + BD, y - BD, x + BW, y,
                x, y, BW, BH,
                x + BW, y, x + BW + BD, y - BD, x + BW + BD, y - BD + BH, x + BW, y + BH,
                x + 0.5, y + 0.6, BW - 1,
                x, y + BH - 1.6, BW,
                x + 0.6, y + 1.4, BH - 2.8)

        def pan_group(cx: float, title: str, value: str, drop: int) -> str:
            """一只托盘：吊线 + 吊轭 + 盘身（内侧 AO + 受光弧）+ 盘沿（带反光弧）+ 盘内码垛。"""
            stack = ""
            base_top = RIM_Y - BH - 8
            for k in range(NB):
                stack += blk(cx, base_top - k * STEP, bool(drop) and k >= NB - drop)
            return f'''<g class="pan" data-cx="{cx}">
      <line x1="{cx}" y1="176" x2="{cx}" y2="286" stroke="rgba(232,214,170,.48)" stroke-width="2.8"/>
      <line x1="{cx}" y1="286" x2="{cx - 40}" y2="{RIM_Y}" stroke="rgba(232,214,170,.34)" stroke-width="2"/>
      <line x1="{cx}" y1="286" x2="{cx + 40}" y2="{RIM_Y}" stroke="rgba(232,214,170,.34)" stroke-width="2"/>
      <circle cx="{cx}" cy="286" r="3" fill="#8B7C63"/>
      <circle cx="{cx}" cy="170" r="3" fill="#8B7C63"/>
      <path d="M {cx - 64} {RIM_Y} C {cx - 58} {RIM_Y + 34}, {cx - 30} {RIM_Y + 44}, {cx} {RIM_Y + 44}
               C {cx + 30} {RIM_Y + 44}, {cx + 58} {RIM_Y + 34}, {cx + 64} {RIM_Y} Z"
            fill="url(#bbowl)" stroke="rgba(255,240,205,.16)" stroke-width="1.5"/>
      <path d="M {cx - 60} {RIM_Y + 10} C {cx - 54} {RIM_Y + 31}, {cx - 28} {RIM_Y + 39}, {cx} {RIM_Y + 39}"
            fill="none" stroke="rgba(0,0,0,.5)" stroke-width="7" filter="url(#bsoft)"/>
      <path d="M {cx - 64} {RIM_Y} C {cx - 58} {RIM_Y + 34}, {cx - 30} {RIM_Y + 44}, {cx} {RIM_Y + 44}"
            fill="none" stroke="rgba(255,246,222,.32)" stroke-width="2"/>
      <ellipse cx="{cx}" cy="{RIM_Y}" rx="64" ry="13" fill="url(#brim)"/>
      <ellipse cx="{cx}" cy="{RIM_Y + 1}" rx="55" ry="9" fill="#0B0908" opacity=".62"/>
      <path d="M {cx - 62} {RIM_Y - 2} A 62 12 0 0 1 {cx + 22} {RIM_Y - 11}"
            fill="none" stroke="rgba(255,252,240,.5)" stroke-width="2.2"/>
      {stack}
    </g>'''

        def label(cx: float, title: str, value: str) -> str:
            return (f'<text x="{cx}" y="408" text-anchor="middle" fill="rgba(255,255,255,.66)"'
                    f' font-family="SC-Sub" font-size="25" letter-spacing="1.5">{esc(title)}</text>'
                    f'<text x="{cx}" y="450" text-anchor="middle" fill="{acc}"'
                    f' font-family="SC-Big" font-weight="900" font-size="38">{esc(value)}</text>')

        body = f'''<div class="bal-wrap">
    <svg viewBox="0 0 900 480" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="bbar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#F4DC92"/><stop offset=".42" stop-color="#CBA52B"/>
          <stop offset="1" stop-color="#6B520E"/>
        </linearGradient>
        <linearGradient id="btop" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#FBE8AE"/><stop offset="1" stop-color="#D9B44C"/>
        </linearGradient>
        <linearGradient id="bfront" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#D2AC3A"/><stop offset=".6" stop-color="#A9851F"/>
          <stop offset="1" stop-color="#7E6114"/>
        </linearGradient>
        <linearGradient id="bside" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#75590F"/><stop offset="1" stop-color="#3F3108"/>
        </linearGradient>
        <radialGradient id="brim" cx=".38" cy=".30" r=".82">
          <stop offset="0" stop-color="#EFE3C6"/><stop offset=".55" stop-color="#9C9182"/>
          <stop offset="1" stop-color="#4A4238"/>
        </radialGradient>
        <linearGradient id="bbowl" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#6E6459"/><stop offset=".55" stop-color="#3B342C"/>
          <stop offset="1" stop-color="#1A1410"/>
        </linearGradient>
        <linearGradient id="bcol" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#241D17"/><stop offset=".38" stop-color="#6E6357"/>
          <stop offset=".62" stop-color="#8A7E6E"/><stop offset="1" stop-color="#17110D"/>
        </linearGradient>
        <linearGradient id="bbase" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#5E5349"/><stop offset="1" stop-color="#17120E"/>
        </linearGradient>
        <linearGradient id="btop2" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#4B4238"/><stop offset=".5" stop-color="#7D7266"/>
          <stop offset="1" stop-color="#3A322A"/>
        </linearGradient>
        <pattern id="bbrush" width="5" height="3" patternUnits="userSpaceOnUse">
          <rect width="5" height="1" fill="rgba(255,255,255,.07)"/>
        </pattern>
        <!-- 表面颗粒：金属的「不是塑料」靠的是这点不均匀；颗粒只叠在实体上，不进背景 -->
        <pattern id="bgrain" width="7" height="7" patternUnits="userSpaceOnUse">
          <circle cx="1.6" cy="2.2" r=".8" fill="rgba(255,255,255,.05)"/>
          <circle cx="5.1" cy="5.6" r=".7" fill="rgba(0,0,0,.10)"/>
        </pattern>
        <!-- 地面：底座下面有一块比四周略亮的受光地面，物体才「坐」在空间里而不是漂着 -->
        <radialGradient id="bfloor" cx=".5" cy=".5" r=".5">
          <stop offset="0" stop-color="#6B5847" stop-opacity=".55"/>
          <stop offset=".45" stop-color="#3E3128" stop-opacity=".42"/>
          <stop offset="1" stop-color="#120D0A" stop-opacity="0"/>
        </radialGradient>
        <!-- 天空/地面分离：一条几乎看不出、但把「地平线」交代清楚的水平过渡带 -->
        <linearGradient id="bhz" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#000" stop-opacity="0"/>
          <stop offset=".62" stop-color="#000" stop-opacity=".18"/>
          <stop offset="1" stop-color="#000" stop-opacity=".45"/>
        </linearGradient>
        <radialGradient id="bglow" cx=".5" cy=".5" r=".5">
          <stop offset="0" stop-color="{acc}" stop-opacity=".16"/>
          <stop offset="1" stop-color="{acc}" stop-opacity="0"/>
        </radialGradient>
        <filter id="bsoft" x="-60%" y="-160%" width="220%" height="420%">
          <feGaussianBlur stdDeviation="3.2"/>
        </filter>
        <filter id="bshadow" x="-30%" y="-30%" width="160%" height="180%">
          <feDropShadow dx="0" dy="12" stdDeviation="12" flood-color="#000" flood-opacity=".5"/>
        </filter>
        <filter id="bground" x="-40%" y="-120%" width="180%" height="340%">
          <feGaussianBlur stdDeviation="13"/>
        </filter>
      </defs>
      <ellipse cx="450" cy="250" rx="430" ry="200" fill="url(#bglow)"/>
      <!-- 台面：用**梯形**而不是椭圆。椭圆只说明「底下有东西」，梯形有地平线与透视收敛，
           观众才读成「放在一张桌上」。远处窄、近处宽，上沿比下沿淡。 -->
      <path d="M 120 404 L 780 404 L 900 480 L 0 480 Z" fill="url(#bfloor)"/>
      <rect x="0" y="404" width="900" height="76" fill="url(#bhz)"/>
      <!-- 三层接地：外扩软影 + 紧贴硬影 + 倒影；阴影一律往右下偏——
           光在左上，影子不可能正好在正下方（正下方的影子＝贴纸）。 -->
      <ellipse cx="512" cy="446" rx="286" ry="19" fill="#000" opacity=".52"
               filter="url(#bground)" transform="rotate(-2 512 446)"/>
      <ellipse cx="486" cy="437" rx="92" ry="7.5" fill="#000" opacity=".85" filter="url(#bsoft)"/>
      <path d="M 372 438 L 536 438 L 522 455 L 390 455 Z" fill="#7E7064" opacity=".12"
            filter="url(#bsoft)"/>
      <path d="M 386 416 L 514 416 L 532 438 L 368 438 Z" fill="url(#bbase)"/>
      <path d="M 386 416 L 514 416 L 532 438 L 368 438 Z" fill="url(#bgrain)"/>
      <path d="M 386 416 L 514 416 L 521 423 L 379 423 Z" fill="url(#btop2)"/>
      <path d="M 386 416 L 514 416" stroke="rgba(255,246,226,.35)" stroke-width="1.6"/>
      <path d="M 402 416 L 396 428" stroke="rgba(0,0,0,.55)" stroke-width="10" filter="url(#bsoft)"/>
      <polygon points="441,174 459,174 470,416 430,416" fill="url(#bcol)"/>
      <polygon points="441,174 459,174 470,416 430,416" fill="url(#bgrain)"/>
      <line x1="448" y1="180" x2="443" y2="410" stroke="rgba(255,255,255,.13)" stroke-width="3"/>
      <!-- 连接件：柱子直接插进底座会读成「梗在空气里」。垫一圈法兰 + 一道 AO。 -->
      <path d="M 428 396 L 472 396 L 482 416 L 418 416 Z" fill="url(#btop2)"/>
      <path d="M 428 396 L 472 396" stroke="rgba(255,246,226,.30)" stroke-width="1.4"/>
      <path d="M 418 416 L 482 416" stroke="rgba(0,0,0,.55)" stroke-width="2.4"/>
      <ellipse cx="450" cy="415" rx="34" ry="7" fill="#000" opacity=".5" filter="url(#bsoft)"/>
      <!-- 支点：一个轴销 + 一圈垫，横杆才有「能转」的理由 -->
      <circle cx="450" cy="162" r="11" fill="#1B1510"/>
      <circle cx="450" cy="162" r="9" fill="url(#bbar)"/>
      <circle cx="450" cy="162" r="3.4" fill="#20180F"/>
      <circle cx="447.2" cy="158.6" r="2.6" fill="rgba(255,255,255,.55)"/>
      <g class="beam" data-pivot="450,162" data-tilt="{TILT}">
        <rect x="150" y="150" width="600" height="24" rx="11" fill="url(#bbar)" filter="url(#bshadow)"/>
        <rect x="150" y="150" width="600" height="24" rx="11" fill="url(#bbrush)"/>
        <rect x="152" y="151.5" width="596" height="5" rx="2.5" fill="rgba(255,255,255,.34)"/>
        <rect x="152" y="169" width="596" height="3" rx="1.5" fill="rgba(0,0,0,.34)"/>
        <circle cx="150" cy="162" r="8" fill="url(#bbar)" stroke="rgba(0,0,0,.35)" stroke-width="1"/>
        <circle cx="750" cy="162" r="8" fill="url(#bbar)" stroke="rgba(0,0,0,.35)" stroke-width="1"/>
      </g>
      <g class="pan-wrap l" transform="translate(0,{TILT_DY})">{pan_group(CX_L, str(L.get("title") or ""), str(L.get("value") or ""), 0)}</g>
      <g class="pan-wrap r" transform="translate(0,{-TILT_DY})">{pan_group(CX_R, str(R.get("title") or ""), str(R.get("value") or ""), NB - 1)}</g>
      {label(CX_L, str(L.get("title") or ""), str(L.get("value") or ""))}
      {label(CX_R, str(R.get("title") or ""), str(R.get("value") or ""))}
    </svg>
    {'<div class="bal-note">' + esc(p.get('note')) + '</div>' if p.get('note') else ''}
  </div>'''
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
        _qt_tpl = f'<blockquote data-t="{{t}}" style="font-size:{int(H * 0.042)}px">{{}}</blockquote>'
        stg = _micro_stages(el, _qt_tpl)
        _cite = (f'<cite style="font-size:{int(H * 0.024)}px;margin-top:40px">—— {esc(p.get("who"))}</cite>'
                 if p.get("who") else "")
        if stg:
            body = f'<div class="mstack">{"".join(stg)}</div>' + _cite
        else:
            body = (f'<blockquote style="font-size:{int(H * 0.042)}px">“{esc(p.get("text"))}”</blockquote>'
                    + _cite)
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
             FPS: int = 30, grade: str = "", overscan: float = 1.0,
             decouple: bool = False, vpad: float = 0.0) -> dict:
    """把一段切出来。**缓存**：同一段再渲不再切（切一段 6.7s 素材要约 38 秒）。

    切好的片段是"已经标准化的素材"——旋转过的 HEVC 竖屏、杜比视界，到这一步
    都是统一尺寸的 H.264，浏览器只管播。

    extractor：`auto`（有 zscale 就自己切）/ `own` / `hf`（退回 measure extract）。

    `decouple`（J-cut）：多出一条**静画**（`xxx-pic-*.mp4`，画面多留 `vpad` 秒、`-an`）。
    返回的 `path` 仍是解耦前那个文件（一字节未变，缓存照旧命中）。调用方自己挑：
    **画面层**用 `pic`（或 `path`），**声音层**永远用 `path`——同一个文件、同一份音轨。
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
    out = {"path": dst, "dur": round(info["dur"], 3), "w": info["w"], "h": info["h"]}
    if decouple:
        out.update(_decoupled_pair(lib, clips, seg, tag, _want, force=force,
                                   extractor=extractor, W=W, H=H, FPS=FPS,
                                   grade=grade, overscan=overscan, vpad=vpad))
    return out


def _decoupled_pair(lib: Path, clips: Path, seg: dict, tag: str, want: str,
                    *, force: bool, extractor: str, W: int, H: int, FPS: int,
                    grade: str, overscan: float, vpad: float) -> dict:
    """J-cut 用的旁路文件：**画面**（多留 `vpad` 秒、`-an`），声音那条不另做。

    为什么画面要单独一条文件、而不是把旧片段拉长：旧片段是「画面长 == 声音长」的
    产物，一旦拉长，它的**音轨也跟着多出几帧素材**（实测 +3 帧 ⇒ 多 4800 采样 =
    100.0ms，sha256 变），那正是 BRIEF 不许碰的东西。把画面单独拆出去之后，旧片段
    原封不动地留给声音层——`<audio src>` 指的就是它本身，一个采样都没重新编过。

    （试过另编一条 `.m4a` 给 `<audio>`：ADTS 字节与旧音轨完全相同，但渲出来整条片子
    的声音比时间轴早 21.4ms ≈ 1024 采样，m4a 容器的 edit list 在渲器那条路上没被认。
    同一个文件就不会有这个问题——实测与解耦前的成片一样是 +2.0ms。）
    """
    aid = seg["asset"]
    src = lib / aid
    if not src.exists():
        raise SystemExit(f"素材不存在：{src}")
    use_own = extractor == "own" or (extractor == "auto" and has_zscale())
    if not use_own:
        raise SystemExit(
            "J-cut 解耦要自己切素材（--extractor own）；当前走的是 hyperframes "
            "measure extract，它只会输出「画面长 == 声音长」的那一种片段。\n"
            "装上带 zscale 的 ffmpeg（bash scripts/runtime.sh install）或显式 --extractor own。")
    pic = clips / f"{tag}-pic{int(round(vpad * 1000))}-{want}.mp4"
    if force or not pic.exists() or pic.stat().st_size < 4096:
        extract_own(src, float(seg["in"]), float(seg["out"]), pic,
                    W=int(W), H=int(H), fps=int(FPS), overscan=float(overscan),
                    grade=grade or "", silent=True, vpad=vpad)
    return {"pic": pic, "pic_dur": round(probe(pic)["dur"], 3)}


# ---------------------------------------------------------- 代码画的整屏格的声音

def scene_mp4(proj: Path, el: dict, FPS: int, W: int = 1080, H: int = 1920) -> Path:
    """`template: scene` 的整屏格：画面是 gen_scene.py 画出来的，落到 clips/ 缓存。

    为什么场景要预渲染成 mp4，而不是像其他 gen 模板那样写成一层 `<div>`：
    场景是**逐帧画出来的位图**（几十万个多边形、抹痕、液流），用 CSS/GSAP 做不出来。
    它仍是代码画的、可复现的（同一秒永远同一帧），只是渲染发生在浏览器外面。

    参数变了就重画：缓存名带上 scene / dur / fps / 分辨率 / 场景源码哈希 / marks，
    改一个字都不会拿旧片冒充。
    """
    from gen_scene import SCENES
    name = str((el.get("props") or {}).get("scene") or "")
    # 场景源码：项目 scenes/ 优先（AI 迭代中的新场景可覆盖同名内置场景），否则内置。
    # 项目级新场景不要求先进注册表（评审 G-01）：文件在就直接走沙箱，
    # 沙箱里 --scene-file 只加载它自己 + SCENE_ID 必须对得上。
    src_proj = proj / "scenes" / f"{name}.py"
    src_builtin = Path(__file__).with_name("scenes") / f"{name}.py"
    if name not in SCENES and not src_proj.exists() and not src_builtin.exists():
        raise SystemExit(f"格 {el.get('n')} 的场景「{name}」不认识（{'/'.join(sorted(SCENES))}）")
    dur = float(el.get("dur") or 0)
    mk = el.get("marks") or (el.get("props") or {}).get("marks") or ""
    if isinstance(mk, (list, tuple)):
        mk = ",".join(f"{float(x):.4f}" for x in mk)
    # 缓存键必须含场景源代码哈希与分辨率：改了绘图逻辑/换尺寸不改 marks 也要重画，
    # 否则返工会被旧 mp4 假装完成（评审 P0-03）。
    import hashlib
    src = src_proj if src_proj.exists() else src_builtin
    try:
        shash = hashlib.sha1(src.read_bytes()).hexdigest()[:8] if src.exists() else "nosrc"
    except OSError:
        shash = "nosrc"
    tag = (f"scene-{name}-{dur:.2f}s-{FPS}fps-{W}x{H}-{shash}"
           + (f"-m{str(mk).replace('.', 'p').replace(',', '_')}" if mk else ""))
    dst = proj / "hf" / "clips" / f"{tag}.mp4"
    if dst.exists() and dst.stat().st_size > 4096:
        return dst
    marks_args = ["--marks", str(mk)] if mk else []
    if src_proj.exists():
        # AI/项目级场景是刚出生的任意代码：必须走沙箱（拒网 + rlimit + 超时，评审 P1-06），
        # 不在主进程裸跑。被拒绝/超时都会带着原因退出，改代码而不是拆围栏。
        cmd = [sys.executable, str(Path(__file__).with_name("scene_sandbox.py")),
               "--scenes-dir", str(proj / "scenes"), "--scene", name,
               "--out", str(dst), "--dur", f"{dur:.3f}", "--fps", str(FPS),
               "--size", f"{W}x{H}"] + marks_args
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            raise SystemExit(f"场景「{name}」在沙箱里没渲出来（exit {r.returncode}）：\n"
                             f"{(r.stderr or r.stdout)[-800:]}")
        return dst
    cmd = [sys.executable, str(Path(__file__).with_name("gen_scene.py")),
           "--scene", name, "--dur", f"{dur:.3f}", "--fps", str(FPS),
           "--size", f"{W}x{H}", "--out", str(dst)] + marks_args
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
    dst.parent.mkdir(parents=True, exist_ok=True)   # 全新零拍摄项目首次渲染时 clips/ 还不存在
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


def GSAP_CANDIDATES() -> list[Path]:
    """gsap.min.js 的候选位置，按「离仓库近」排序。

    以前这里写死某个 skill 目录里的 vendor 副本——那台机器换了工具链之后
    路径就没了，新建项目一走到这一步直接 `SystemExit("缺 gsap")`，而已有项目因为 hf/assets/ 里
    早拷过一份所以完全看不出问题（2026-11-13 T-018 复核时撞上）。所以改成候选表：
    仓库自带的优先，其次几个 skill 目录里的副本。
    """
    home = Path.home()
    return [
        SKILL / "assets" / "gsap.min.js",
        SKILL / "demo" / "pack-micro" / "hf" / "assets" / "gsap.min.js",
        home / ".cola" / "skills" / "srt-motion-cards" / "assets" / "gsap.min.js",
        home / ".claude" / "skills" / "talking-head-recut" / "assets" / "vendor" / "gsap.min.js",
        home / ".agents" / "skills" / "talking-head-recut" / "assets" / "vendor" / "gsap.min.js",
    ]


def _gsap_src() -> Path | None:
    for p in GSAP_CANDIDATES():
        if p.is_file() and p.stat().st_size > 4096:
            return p
    return None



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
         # 视频那一路也要缩到 1080×1920：alphamerge 要求两路同尺寸，遮罩是按
         # 1080×1920 缩的，而裁出来的片段可能保留原始分辨率（proj79 的熊工口播
         # 是 1296×2304）——尺寸不一致 vp9 直接 Invalid argument，整条渲染断掉。
         "[0:v]scale=1080:1920[bv];[bv][mk]alphamerge,format=yuva420p[o]",
         "-map", "[o]", "-frames:v", str(n),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "18",
         "-auto-alt-ref", "0", "-an", str(dst)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        print(f"  ！alpha 编码失败：{clip.name}\n{(r.stderr or '')[-300:]}", file=sys.stderr)
        return None
    return dst


# ------------------------------------------------------------------ 卡片

# 卡片比锚词晚多少出场。0.10 太小：字幕还没听到字就先看见，读到的和听到的对不上。
# 上游的硬规则是 0.15s（声音在前），照它。
CARD_LAG = 0.15


# 本轮渲卡片时「写了的锚词在词级真值里找不到」的清单（T-034）。
# 以前这种卡只往 stderr 打一句「沿用默认时机」就照画：卡出得早/晚，声音在哪没人管，
# `check_timing` 又只看 lag 数不数得到——负样本 B 里 lag 3880ms 都能判 0 error。
ANCHOR_MISS: list[str] = []


def _entry_push(start: float, seg_start: float, cap: float | None = None) -> float:
    """卡片离切点太近就往后挪一点（防「贴脸」）。返回挪完的起点。

    量的是**离切点多远**，不是「块有多长」。旧写法是
    `min(MG_ENTRY_MAX, (块长 - MG_ENTRY_CALM) * 0.35)`——它跟切点无关，
    于是任何超过 `MG_ENTRY_CALM + MG_ENTRY_MAX/0.35 ≈ 6.07s` 的块都被顶到满格：
    卡在**它锚定的那个词说完之后**才亮，滞后恒等于 `CARD_LAG + MG_ENTRY_MAX` = 1400ms。
    这就是「两个独立样本同一个数」的来源：不是巧合，是饱和
    （实测 qizao-ground-01 三张、dianzan-b 的 stat 卡、自测样片都是 1400）。
    锚词在块内深处时，它本来就离切点很远了，不需要再推。

    `cap`：不许越过这个时刻（锚词收口）——卡必须还在说这个词，
    否则「卡跟着话走」就变成了「卡跟着块长走」。
    """
    if float(start) - float(seg_start) >= MG_ENTRY_MIN:
        return round(float(start), 3)
    want = round(float(seg_start) + MG_ENTRY_MIN, 3)
    if cap is not None:
        want = round(min(want, float(cap)), 3)
    return round(max(float(start), want), 3)


def window_chars(chars, a_in: float, dur_q: float):
    """这一格音频窗口内的字级真值 `[(字, 起, 止)]`。"""
    return [(ch, st, en) for ch, st, en in chars
            if a_in - 0.05 <= st < a_in + dur_q + 0.05]


def find_anchor(chars, a_in: float, dur_q: float, on: str) -> int | None:
    """锚词落在**这一格音频窗口**里的第几个字；找不到返回 `None`。

    找不到就是找不到：不许回落到「重音点 / 段首 + 0.6s」——那是猜的时机，而锚词存在
    的唯一意义就是「卡跟着这句话出来」。调用方拿到 `None` 必须停渲并说清楚（T-034），
    以前只往 stderr 打一句「沿用默认时机」，于是卡随便落、声音在哪没人管。
    """
    if not on:
        return None
    txt = "".join(ch for ch, _, _ in window_chars(chars, a_in, dur_q))
    j = txt.find(on)
    return j if j >= 0 else None


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
    ANCHOR_MISS.clear()
    cards = []
    idc = (style.get("_id_card") or None)
    if idc:
        _idc = {"kind": "idcard", "text": "", "lines": [str(x) for x in idc["lines"]],
                "start": float(idc.get("at") or 0.35),
                "end": round(float(idc.get("at") or 0.35) + float(idc.get("dur") or 9.0), 3)}
        # 身份卡也要有声音依据（规范第 3 条对卡一视同仁）：plan 的 id_card.on 在
        # 片头第一段的口播里找词定锚——身份卡本来就是伴着开场白亮出来的。
        _on = str(idc.get("on") or "").strip()
        if _on and segs:
            _s0 = segs[0]
            _aid = (_s0.get("audio") or {}).get("asset") or _s0["asset"]
            _a_in = float((_s0.get("audio") or {}).get("in", _s0["in"]))
            _ch = chars_cache.get(_aid) or chars_cache.setdefault(_aid, load_chars(lib, _aid))
            _ch = lexicon.fix_items(_ch, lexicon.load(lib / "_cutskills"))
            _j = find_anchor(_ch, _a_in, _s0["_dur_q"], _on)
            if _j is not None:
                _win = window_chars(_ch, _a_in, _s0["_dur_q"])
                _idc["anchor_word"] = _on
                _idc["anchor_t"] = round(_win[_j][1] - _a_in + _s0["_start"], 3)
            else:
                ANCHOR_MISS.append(f"身份卡：锚词「{_on}」不在片头口播（plan 的 id_card.on）里")
        cards.append(_idc)
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
            if float(s.get("_dur_q", 9)) > MG_ENTRY_CALM:
                start = _entry_push(start, s["_start"])
        is_mg = kind in MG_KINDS
        # 大字（四五个字）用 hold_s 的一两秒就够；信息卡要按内容量算，不能被它锁死。
        d = card_hold(kind, c.get("data") or {}) if is_mg else max(
            h0, min(float(c.get("dur") or h1), h1))
        on = str(c.get("on") or "").strip()
        aid = (s.get("audio") or {}).get("asset") or s["asset"]
        a_in = float((s.get("audio") or {}).get("in", s["in"]))
        if on:
            chars = chars_cache.get(aid) or chars_cache.setdefault(aid, load_chars(lib, aid))
            # 锚词比对必须先过词表：卡上写「柒造」、ASR 写「7灶」，不纠就永远对不上。
            chars = lexicon.fix_items(chars, lexicon.load(lib / "_cutskills"))
            j = find_anchor(chars, a_in, s["_dur_q"], on)
            if j is not None:
                win = window_chars(chars, a_in, s["_dur_q"])
                st0 = win[j][1] - a_in + s["_start"] + CARD_LAG
                st1 = win[min(j + len(on) - 1, len(win) - 1)][2] - a_in + s["_start"]
                start = round(max(s["_start"] + 0.15, st0), 3)
                # 块越长，卡片入场越往后推（不贴着切点出）。推的量按**离切点多远**算，
                # 并且不许越过锚词的收口（st1）：卡必须还在说这个词。
                _sd = float(s.get("_dur_q", 9))
                if _sd > MG_ENTRY_CALM:
                    start = _entry_push(start, s["_start"], cap=st1 - 0.05)
                anchor_t = round(win[j][1] - a_in + s["_start"], 3)
                # 锚词只决定「什么时候出」，不决定「放多久」——以前这里用
                # st1-start+0.25 算时长，锚词一短（「十个人」0.5s）卡就只剩 1.0s，
                # 观众刚看清就切走。MG 卡时长归 card_hold，这里只负责保住下限。
                if is_mg:
                    d = card_hold(kind, c.get("data") or {})
                else:
                    d = round(min(h1, max(h0, st1 - start + 0.25)), 3)
            else:
                ANCHOR_MISS.append(
                    f"段 {s['n']} 卡「{str(c.get('text') or '')[:10]}」的锚词「{on}」"
                    f"不在该格词级真值里")
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
        _cd = {"seg": s["n"], "kind": kind, "text": str(c.get("text") or ""),
               "data": c.get("data") or None,
               "start": start, "end": round(start + d, 3),
               # 大字自己的两个开关（T-045）：位置与隔离层归 timeline 写，渲器不自作主张。
               # `top_h` 只在卡自己写了才用（优先级见 card_layout.kw_top_of）；
               # `isolate` 打开才画那把板（样式仍走包，见 packs/*.yaml 的 emphasis.isolate）。
               **({"top_h": float(c["top_h"])} if c.get("top_h") is not None else {}),
               **({"isolate": True} if c.get("isolate") else {}),
               "anchor_word": on or None,
               "anchor_t": round(float(anchor_t), 3) if anchor_t is not None else None,
               "appear_t": round(float(start), 3),
               "lag_ms": (round((start - float(anchor_t)) * 1000)
                          if anchor_t is not None else None)}
        if deferred is not None:
            _cd["deferred_to"] = deferred
        cards.append(_cd)
    return settle_cards(cards)


def settle_cards(cards: list[dict], total: float | None = None) -> list[dict]:
    """按时间顺序排卡：卡能跨格活着，但两张卡不许打架。

    Jerry 2026-11-12：「出现的太短，观众都没有看完就切了」。根因不只是长度下限，
    还有卡被锁在**它所在的那一格**里：一句 2.1 秒的话配一张要 2.6 秒才能读完的卡，
    不管下限怎么调都会漏字。所以卡允许跨切点存活（渲染层是独立的 clip，本来就支持）。

    先来先到：前一张不够长就把后一张往后推，而不是把前一张掐短——被掐短的那张
    就是观众没看完的那张。实在排不进去的（整片只剩不够一格的位置）就丢，并说明。
    """
    if not cards:
        return cards
    cards = sorted(cards, key=lambda c: (float(c["start"]), c.get("seg") or 0))
    lim = float(total) if total else None
    keep: list[dict] = []
    for c in cards:
        want = round(float(c["end"]) - float(c["start"]), 3)
        if keep and float(c["start"]) < float(keep[-1]["end"]) + CARD_GAP:
            # 往前挤：能挤多长算多长，下限还是 CARD_HOLD_MIN
            c["start"] = round(float(keep[-1]["end"]) + CARD_GAP, 3)
        end = round(float(c["start"]) + want, 3)
        if lim is not None:
            end = min(end, round(lim - 0.15, 3))
        held = round(end - float(c["start"]), 3)
        # 下限只对信息卡（stat / steps / versus / term）有意义：卡上同时有标题、
        # 数字、说明、出处，要读完。大字（四五个字）本来就该一两秒——
        # 对它套 2.6s 的下限，结果是一条片的大字全被当成「排不下」撤光。
        if c.get("kind") in MG_KINDS and held < CARD_HOLD_MIN and held < 1.2:
            print(f"  ！卡「{c.get('text') or c.get('kind')}」排不下（只剩 {held:.2f}s），撤掉",
                  file=sys.stderr)
            continue
        c["end"] = end
        keep.append(c)
    return keep


def mask_stats(mdir: Path, W: int, H: int) -> dict | None:
    """量一张遮罩：头顶在哪、人占多宽、软边多少。

    这三个数决定「大字能不能放到人脑后」。放脑后的前提是**人比字窄**——
    真人近景（牛哥那种）人头就占 35%W，字撑 70%W 也躲不开脑袋，抠了也是白抠：
    字被拦腰截断，看起来像技术故障，不像设计。
    """
    import numpy as np
    from PIL import Image
    fs = sorted(mdir.glob("f*.png")) if mdir.exists() else []
    if not fs:
        return None
    arr = np.asarray(Image.open(fs[len(fs) // 2]).convert("L").resize((W, H)))
    ys, _ = np.nonzero(arr > 128)
    if len(ys) == 0:
        return None
    head_top = float(ys.min()) / H
    y0 = int(ys.min())
    y1 = min(H, y0 + int(H * 0.25))
    band = arr[y0:y1]
    step = max(1, band.shape[0] // 20)
    face_w = max(float((band[y] > 128).mean()) for y in range(0, band.shape[0], step))
    soft = float(((arr > 20) & (arr < 235)).mean())
    return {"head_top": round(head_top, 3), "face_w": round(face_w, 3), "soft": round(soft, 4)}


FACE_W_MAX = 0.35      # 人头占屏宽超过这个数，字撑 70%W 也躲不开 → 别放脑后
HEAD_TOP_MIN = 0.20    # 头顶留白不到 20%H，字放上面会顶到平台 UI
SOFT_MAX = 0.03        # 软边超 3%（发丝、逆光），抠出来一圈毛边


def behind_ok(st: dict | None) -> bool:
    if not st:
        return False
    return (st["face_w"] < FACE_W_MAX and st["head_top"] >= HEAD_TOP_MIN
            and st["soft"] < SOFT_MAX)


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
  color:{cap_color}; letter-spacing:{cap_ls}; line-height:{cap_lh}; text-align:center;
  text-shadow:{cap_shadow}; white-space:nowrap; {cap_extra} }}
/* 纸感底衬模板：整条字幕垫一条米色纸带（浅底深字）。模板没开这张纸 → 这里为空，
   老包的 CSS 一个字节都不变（不是「加一层透明」，是根本不输出这行）。 */
{cap_plate_css}
/* 强调：数字与否定/对比词。只染这两类，别的一个字都不动 */
.cap .em {{ color:{cap_em}; font-weight:{cap_em_w}; {cap_em_extra} }}
.scrim {{ position:absolute; left:0; right:0; top:0; pointer-events:none;
         background:linear-gradient(to bottom, rgba(0,0,0,{scrim_a}), rgba(0,0,0,0)); }}
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
/* 大字隔离层（T-045）：字后一块柔化压暗。色/浓度/柔化半径/外扩全由包管
   （emphasis.isolate → 渲器算成这四个 inline 值），这里只管「它是一层不占位的板」。
   为什么不给字加 text-shadow：投影只舔笔画边，隔不开一块高频背景（工地的工具/管子）；
   为什么不整屏暗场：露脸格整屏暗场是 B7 红线，而且它改的是整个画面的曝光。 */
.kwpod {{ position:absolute; pointer-events:none; }}
.idcard {{ position:absolute; left:{idc_x}%; top:{idc_y}%; text-align:left; }}
.idcard span {{ display:block; font-family:"SC-Sub", sans-serif; color:#fff; line-height:1.34; }}
/* MG 卡：按 srt-motion-cards 的设计语言重写——渐变压暗 + 居中排版 + 强对比字号。
   自造那版是「顶部一小块 78% 黑底」，看着像贴了个便签；这一版把卡放进画面中段的一块
   暗场里，字号撑开，读起来是「这一幕在讲这个数」。位置限制在字幕带之上（18%–62%H），
   不压人脸上半张。class 名沿用旧的，GSAP 的入场不用改。 */
/* 叠加态（T-019，原则 B7 图底主权 / B10 局部底衬）：
   卡放在哪、底片让不让位，不再由一条写死的百分比带决定，也不再有「整屏 90% 暗场」。
   scripts/card_layout.py 逐卡算出三种合法态之一（corner / focus_blur / cutaway），
   这里只负责把结果画上去：
     .mgbox   —— 卡本体放在哪、缩多少（承载位置与 scale）
     .mgplate —— **跟着卡走的那一块**底衬（羽化边），不再是全屏
     .mg      —— 卡片本体，GSAP 的入场目标（类名与动画都不动） */
.mgwrap {{ position:absolute; inset:0; }}
.mgbox {{ position:absolute; left:{mg_pad}px; right:{mg_pad}px; top:{mg_lo}%; bottom:{mg_hi}%; }}
.mgplate {{ position:absolute; pointer-events:none; }}
/* 底板外投影（T-019 复核②）：**另起一层**圆角色块 + filter:blur()。
   为什么不能用 box-shadow：底板带羽化 mask，而 box-shadow 属于元素自身绘制，
   同层时会被同一张 mask 一起裁掉——投影与羽化边只能二选一。参数仍取
   tokens.surface.shadow（最大模糊的那条外投影），不另设第二个 token。 */
.mgplate > .mp-glow {{ position:absolute; inset:var(--mg-glow-spread,0px); border-radius:{mg_rad}px;
  background:var(--mg-glow,none); filter:blur(var(--mg-glow-blur,0px));
  transform:translate(var(--mg-glow-shift,0px 0px)); }}
/* 底板本体：四边羽化由 mask 给（两段 linear-gradient 取交集，带子是定宽像素，
   所以在板边界上 alpha 恰好归零——没有任何一条边是被硬切的）；磨砂由 backdrop-filter 给。
   T-042 三层：① --mg-tint（逐卡算出来的底片平均色温，focus_blur 用，见 card_layout.plate_tint）
   ② --surface-light（包的纵向光线：顶部窄亮带 + 底部轻沉）③ --surface-scrim（包的遮罩）。
   顺序不能反：色温在最下、光在中、遮罩在上，否则暖底会把顶部亮带吃掉。 */
.mgplate > .mp-surf {{ position:absolute; inset:0;
  background-image:var(--mg-tint,none), var(--surface-light,none), var(--surface-scrim);
  -webkit-mask-image:var(--mg-plate-mask,none); mask-image:var(--mg-plate-mask,none);
  -webkit-mask-composite:source-in; mask-composite:intersect; }}
/* 景深退后：只对这块底板做背景模糊，画面其余部分保持清晰 */
.mgplate.mg-blur > .mp-surf {{ backdrop-filter:blur({mg_blur}px) saturate(.92);
  -webkit-backdrop-filter:blur({mg_blur}px) saturate(.92); }}
/* 整屏纯底切出（B7 第三种态）：底衬换成包的不透明语域底，不靠半透明板子「差不多盖住」
   ——那是糊，不是切走。两层叠上去：底板色（tokens.surface.plate）压在 palette.ground 上，
   再叠包自己的遮罩。只铺 ground 一层会把整屏压成近黑（实测 L≈0.10，被 blackdetect 判成黑帧），
   底板色那一层既保住语域，也把亮度拉回 0.2 以上——切出的是「品牌底板」，不是「黑屏」。 */
.mgplate.mg-cut > .mp-surf {{ background-color:var(--ground);
  background-image:var(--mg-tint,none), var(--surface-plate), var(--surface-light,none),
    var(--surface-scrim); }}
.mg {{ position:absolute; inset:0;
  display:flex; flex-direction:column; justify-content:center;
  align-items:center; text-align:center; }}
.mg-stat .mg-kick {{ text-shadow:0 2px 10px rgba(0,0,0,.85);
  font-family:"SC-Sub"; font-weight:600; font-size:{mg_src}px;
  letter-spacing:.16em; color:rgba(var(--surface-ink-rgb),.6); margin-bottom:{mg_gap}px; }}
.mg .mg-title {{ font-family:"SC-Sub"; font-weight:700; font-size:{mg_h}px;
  color:var(--surface-ink); letter-spacing:.01em; margin-bottom:{mg_gap}px; text-align:center; }}
/* stat：kicker → 巨数字 → 说明 → 出处，四行都是居中 */
.mg-stat .mg-hero {{ display:flex; align-items:baseline; justify-content:center; gap:{mg_gap2}px; }}
.mg-stat .num {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_num}px;
  line-height:.94; color:var(--surface-ink); letter-spacing:-.035em;
  text-shadow:0 16px 54px rgba(0,0,0,.65); }}
.mg-stat .unit {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_unit}px;
  color:var(--accent); }}
.mg-stat .mg-label {{ font-family:"SC-Sub"; font-weight:700; font-size:{mg_label}px;
  color:var(--surface-ink); opacity:1; margin-top:{mg_gap}px; line-height:1.32;
  text-shadow:0 2px 6px rgba(0,0,0,.9), 0 6px 22px rgba(0,0,0,.7); }}
.mg-stat .mg-src {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_src}px;
  color:var(--surface-ink); opacity:.86; margin-top:{mg_gap2}px; letter-spacing:.06em;
  text-shadow:0 2px 10px rgba(0,0,0,.95); }}
/* steps：序号是实心圆点，条目左对齐——「先后」靠上下排，不靠居中对齐 */
.mg-steps {{ align-items:stretch !important; text-align:left; }}
.mg-steps ol {{ list-style:none; display:flex; flex-direction:column; gap:{mg_gap}px;
  width:100%; align-items:flex-start; padding:0 {mg_pad2}px; }}
.mg-steps li {{ display:flex; align-items:flex-start; gap:{mg_gap2}px; position:relative; }}
.mg-steps li:not(:last-child)::after {{ content:""; position:absolute; left:{mg_dot_r}px;
  top:100%; height:{mg_gap}px; width:2px; background:#fff; opacity:.22; }}
.mg-steps li b {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_dot}px; color:#0b0d11;
  background:var(--accent); width:{mg_dot_w}px; height:{mg_dot_w}px; border-radius:50%;
  display:flex; align-items:center; justify-content:center; flex:0 0 auto; margin-top:{mg_dot_t}px; }}
.mg-steps li span {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_step}px;
  color:rgba(var(--surface-ink-rgb),.94); line-height:1.3; }}
/* versus：两栏面板 + 圆形 VS 徽标。徽标是「这是对立」的唯一视觉信号 */
.mg-versus .mg-ver {{ display:flex; align-items:stretch; gap:{mg_gap2}px; width:100%; }}
/* 面板要能「浮起来」，不能只是「画在背景上的一块色」：外投影 + 较亮的底 + 一根亮边。
   三样都走 tokens.surface（shadow / plate / border），老包不写就是 none，像素不动。 */
.mg-versus .side {{ flex:1; display:flex; flex-direction:column; gap:{mg_gap2}px;
  padding:{mg_pan}px {mg_pad2}px; border-radius:{mg_rad}px;
  background:var(--surface-plate); border:1px solid var(--surface-border);
  backdrop-filter:var(--surface-blur); box-shadow:var(--surface-shadow); }}
.mg-versus .side b {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_vh}px;
  color:var(--surface-ink); }}
.mg-versus .side span {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_vs}px;
  color:rgba(var(--surface-ink-rgb),.78); line-height:1.32; }}
.mg-versus .vs {{ align-self:center; flex:0 0 auto; width:{mg_badge}px; height:{mg_badge}px;
  border-radius:50%; display:grid; place-items:center; font-family:"SC-Big"; font-weight:900;
  font-size:{mg_badge_t}px; color:#0b0d11; background:var(--accent); }}
.mg-versus .mg-tail {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_step}px;
  color:rgba(var(--surface-ink-rgb),.88); margin-top:{mg_gap}px; }}
/* term：像一张词条卡，左边一条 accent 竖条把「词」和「释义」分开 */
/* term 是一张「词条卡」，要有一块自己的面板——白字直接压在杂树/浅墙上必糊
   （srt-motion-cards 的 term-card 就是一块 .84 的深底，这层别省；具体什么色归包管） */
.mg-term {{ background:var(--surface-plate-term); border:1px solid var(--surface-border-term);
  border-radius:{mg_rad}px; padding:var(--surface-term-pad); text-align:left;
  backdrop-filter:var(--surface-blur); box-shadow:var(--surface-shadow);
  align-items:flex-start !important; display:flex; flex-direction:column; }}
/* 词头**不能**再叫 .mg-term：外层定位容器就是 `mg mg-term`，内外同名时同一个
   `.mg-term` 选择器同时命中两层，卡片被套上一个同样的深框——「黑框套黑框」
   （2026-11-13 T-018 事故：dianzan-b 的 term 卡）。内层一律用 mg-word。 */
.mg-term .mg-word {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_term}px;
  color:var(--surface-ink); line-height:1.04; letter-spacing:-.02em; }}
/* 不加左侧竖条：它只对着释义，看着像列表符号，把版面切散（srt 的 term-card 也没有） */
.mg-term .mg-def {{ font-family:"SC-Sub"; font-weight:500; font-size:{mg_step}px;
  color:rgba(var(--surface-ink-rgb),.95); margin-top:var(--surface-term-gap);
  line-height:1.42; text-align:left;
  text-shadow:0 2px 10px rgba(0,0,0,.9); }}
/* proportion：比例切分条（局部占整体比例，形状即语义） */
.mg-proportion {{ width:100%; display:flex; flex-direction:column; align-items:center; text-align:center; }}
.mg-prop {{ width:100%; display:flex; flex-direction:column; align-items:center; }}
.mg-prop-hero {{ display:flex; align-items:baseline; justify-content:center; gap:{mg_gap2}px; }}
.mg-prop-hero .ratio {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_ratio}px;
  line-height:.92; color:var(--accent); letter-spacing:-.03em;
  text-shadow:0 12px 36px rgba(0,0,0,.65); }}
.mg-prop-hero .prop-tag {{ font-family:"SC-Sub"; font-weight:700; font-size:{mg_label}px;
  color:var(--surface-ink); text-shadow:0 2px 8px rgba(0,0,0,.8); }}
.mg-prop-bar-wrap {{ width:100%; max-width:92%; margin:{mg_gap2}px 0; }}
.mg-prop-track {{ position:relative; width:100%; height:{mg_prop_bar}px;
  background:rgba(var(--surface-ink-rgb),.12); border-radius:{mg_rad}px;
  border:1px solid var(--surface-border); box-shadow:inset 0 2px 6px rgba(0,0,0,.45);
  backdrop-filter:var(--surface-blur); }}
.mg-prop-fill {{ position:relative; height:100%; border-radius:{mg_rad}px;
  background:linear-gradient(90deg, var(--accent2), var(--accent));
  box-shadow:0 0 16px rgba(var(--surface-ink-rgb),.3); }}
.mg-prop-pin {{ position:absolute; right:0; top:50%; transform:translate(50%, -50%);
  width:{mg_dot_t}px; height:{mg_dot_w}px; background:var(--surface-ink);
  border-radius:{mg_dot_t}px; box-shadow:0 2px 10px rgba(0,0,0,.85); }}
.mg-prop-ticks {{ display:flex; justify-content:space-between; margin-top:{mg_gap2}px; width:100%; }}
.mg-prop-ticks .part {{ font-family:"SC-Sub"; font-weight:700; font-size:{mg_src}px;
  color:var(--accent); text-shadow:0 2px 6px rgba(0,0,0,.8); }}
.mg-prop-ticks .total {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_src}px;
  color:rgba(var(--surface-ink-rgb),.82); text-shadow:0 2px 6px rgba(0,0,0,.8); }}
.mg-proportion .mg-label {{ font-family:"SC-Sub"; font-weight:700; font-size:{mg_label}px;
  color:var(--surface-ink); margin-top:{mg_gap}px; line-height:1.32;
  text-shadow:0 2px 6px rgba(0,0,0,.9), 0 6px 22px rgba(0,0,0,.7); }}

/* day-night：早晚时段接力卡（互补经营非死敌擂台） */
.mg-day-night {{ width:100%; }}
.mg-dn {{ width:100%; display:flex; flex-direction:column; align-items:center; }}
.mg-dn-relay {{ display:flex; align-items:stretch; gap:{mg_gap2}px; width:100%; justify-content:center; }}
.mg-dn .side {{ flex:1; display:flex; flex-direction:column; gap:{mg_gap2}px;
  padding:{mg_pan}px {mg_pad2}px; border-radius:{mg_rad}px;
  background:var(--surface-plate); border:1px solid var(--surface-border);
  backdrop-filter:var(--surface-blur); box-shadow:var(--surface-shadow);
  text-align:left; position:relative; }}
.mg-dn .side .dn-tag {{ display:inline-block; align-self:flex-start;
  padding:{mg_dot_t}px {mg_gap2}px; border-radius:{mg_rad}px;
  font-family:"SC-Sub"; font-weight:700; font-size:{mg_src}px;
  background:rgba(var(--surface-ink-rgb),.12); border:1px solid rgba(var(--surface-ink-rgb),.2); }}
.mg-dn .side.day .dn-tag {{ color:var(--accent); }}
.mg-dn .side.night .dn-tag {{ color:var(--accent2); }}
.mg-dn .side b.dn-head {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_vh}px;
  color:var(--surface-ink); line-height:1.1; }}
.mg-dn .side span.dn-body {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_vs}px;
  color:rgba(var(--surface-ink-rgb),.88); line-height:1.32; }}
.mg-dn .side span.dn-sub {{ font-family:"SC-Sub"; font-weight:500; font-size:{mg_src}px;
  color:var(--accent); opacity:.9; }}
.mg-dn .dn-flow {{ align-self:center; flex:0 0 auto; display:flex; flex-direction:column;
  align-items:center; justify-content:center; gap:{mg_dot_t}px; }}
.mg-dn .dn-flow .dn-pill {{ padding:{mg_dot_t}px {mg_gap2}px; border-radius:{mg_rad}px;
  font-family:"SC-Big"; font-weight:900; font-size:{mg_flow_pill}px; color:#0b0d11;
  background:var(--accent); box-shadow:0 4px 14px rgba(0,0,0,.5); letter-spacing:.04em; }}
.mg-dn .dn-flow .dn-arrow {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_badge_t}px;
  color:var(--accent); line-height:1; }}
.mg-dn .mg-tail {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_step}px;
  color:rgba(var(--surface-ink-rgb),.88); margin-top:{mg_gap}px; }}

/* chips：招牌名牌卡（2–4枚名牌踩词逐枚点亮） */
.mg-chips {{ width:100%; }}
.mg-chips-wrap {{ width:100%; display:flex; flex-direction:column; align-items:center; }}
.chips-grid {{ display:flex; gap:{mg_gap2}px; justify-content:center; align-items:stretch;
  width:100%; }}
.chips-grid .chip {{ flex:1; min-width:0; display:flex; flex-direction:column;
  align-items:center; justify-content:center; padding:{mg_pan}px {mg_gap2}px;
  border-radius:{mg_rad}px; background:var(--surface-plate);
  border:1px solid var(--surface-border); backdrop-filter:var(--surface-blur);
  box-shadow:var(--surface-shadow); text-align:center; }}
.chips-grid .chip-num {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_chip_num}px;
  color:var(--accent); margin-bottom:{mg_dot_t}px; letter-spacing:.04em; }}
.chips-grid .chip-name {{ font-family:"SC-Big"; font-weight:900; font-size:{mg_chip_name}px;
  color:var(--surface-ink); line-height:1.15; word-break:break-all;
  text-shadow:0 2px 10px rgba(0,0,0,.7); }}
.chips-grid .chip-tag {{ font-family:"SC-Sub"; font-weight:700; font-size:{mg_src}px;
  color:var(--accent2); margin-top:{mg_dot_t}px; padding:2px {mg_dot_t}px;
  border-radius:4px; background:rgba(var(--surface-ink-rgb),.08); }}
.mg-chips-wrap .mg-tail {{ font-family:"SC-Sub"; font-weight:600; font-size:{mg_step}px;
  color:rgba(var(--surface-ink-rgb),.88); margin-top:{mg_gap}px; }}
{gen_css}
{pkg_css}
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
{pkg_html}
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
// 大字隔离层：跟大字同进同退（它只是字底下一块板，晚一帧出现就变成「闪一下」）
document.querySelectorAll(".kwpod").forEach((c) => {{
  const t = parseFloat(c.dataset.start);
  const d = parseFloat(c.dataset.duration);
  tl.set(c, {{ opacity: 0 }}, 0);
  tl.set(c, {{ opacity: 1 }}, t);
  tl.to(c, {{ opacity: 0, duration: 0.16, ease: "power1.in" }},
        t + Math.max(0, d - 0.16));
}});
// MG 卡：件与件之间要有先后，否则就是一页 PPT 硬切上来。
document.querySelectorAll(".mgwrap").forEach((w) => {{
  // 时间窗在 .mgwrap 上（它是 .clip），视觉主体在里面那层 .mg
  const c = w.querySelector(".mg") || w;
  const t = parseFloat(w.dataset.start);
  const d = parseFloat(w.dataset.duration);
  tl.set(w, {{ opacity: 0 }}, t);
  tl.set(w, {{ opacity: 1 }}, t);
  tl.fromTo(c, {{ opacity: 0, y: 30, scale: 0.97 }},
    {{ opacity: 1, y: 0, scale: 1, duration: 0.32, ease: "power2.out", immediateRender: false }}, t);
  // 数字卡：de-blur + 轻微放大入场，同时数字从 0 滚上去。
  // 曲线照 srt-motion-cards 的 stat-in：scale .82 / blur 10px → 1 / 0，cubic-bezier(.16,1,.3,1)。
  // 这一下是「高级感」的主要来源——硬切上来就是贴便签，模糊化开才像镜头语言。
  const num = c.querySelector(".num");
  if (num) {{
    const raw = num.dataset.to || num.textContent;
    const target = parseFloat(String(raw).replace(/[^0-9.]/g, ""));
    if (!isNaN(target)) {{
      const o = {{ v: 0 }};
      const dec = /\\d[.,]\\d/.test(String(raw)) ? 1 : 0;
      tl.fromTo(num, {{ scale: 0.82, filter: "blur(10px)", opacity: 0 }},
        {{ scale: 1, filter: "blur(0px)", opacity: 1, duration: 0.8,
           ease: "expo.out", immediateRender: false }}, t + 0.04);
      tl.fromTo(o, {{ v: 0 }}, {{
        v: target, duration: 0.5, ease: "power2.out", immediateRender: false,
        onUpdate: () => {{ num.textContent = dec ? o.v.toFixed(1) : String(Math.round(o.v)); }},
        onComplete: () => {{ num.textContent = raw; }}
      }}, t + 0.04);
      const unit = num.parentElement.querySelector(".unit");
      if (unit) {{
        tl.fromTo(unit, {{ opacity: 0, y: 8 }},
          {{ opacity: 1, y: 0, duration: 0.4, ease: "expo.out", immediateRender: false }}, t + 0.42);
      }}
      const kick = c.querySelector(".mg-kick");
      if (kick) {{
        tl.fromTo(kick, {{ opacity: 0, y: 8 }},
          {{ opacity: 1, y: 0, duration: 0.4, ease: "expo.out", immediateRender: false }}, t + 0.12);
      }}
      const lab = c.querySelector(".mg-label"), sr = c.querySelector(".mg-src");
      if (lab) tl.fromTo(lab, {{ opacity: 0, y: 10 }},
        {{ opacity: 1, y: 0, duration: 0.45, ease: "expo.out", immediateRender: false }}, t + 0.3);
      if (sr) tl.fromTo(sr, {{ opacity: 0 }},
        {{ opacity: 0.52, duration: 0.4, immediateRender: false }}, t + 0.46);
    }}
  }}
  // 条目逐条进来（步骤卡 / 对比卡的两栏 / 术语卡的释义 / 名牌卡的各枚名牌）
  const kids = c.querySelectorAll("li, .side, .chip, .mg-tail");
  kids.forEach((k, i) => {{
    const when = t + 0.2 + i * 0.22;
    tl.set(k, {{ opacity: 0 }}, t);
    tl.fromTo(k, {{ opacity: 0, y: 16 }},
      {{ opacity: 1, y: 0, duration: 0.24, ease: "power2.out", immediateRender: false }}, when);
  }});
  const vs = c.querySelector(".vs");
  if (vs) {{
    tl.fromTo(vs, {{ scale: 0.4, opacity: 0 }},
      {{ scale: 1, opacity: 0.9, duration: 0.26, ease: "back.out(3)",
         immediateRender: false }}, t + 0.42);
  }}
  const dnFlow = c.querySelector(".dn-flow");
  if (dnFlow) {{
    tl.fromTo(dnFlow, {{ scale: 0.5, opacity: 0 }},
      {{ scale: 1, opacity: 1, duration: 0.26, ease: "back.out(2.5)",
         immediateRender: false }}, t + 0.38);
  }}
  const propFill = c.querySelector(".mg-prop-fill");
  if (propFill) {{
    const targetW = propFill.style.width || "33.3%";
    tl.fromTo(propFill, {{ width: "0%" }},
      {{ width: targetW, duration: 0.68, ease: "power2.out", immediateRender: false }}, t + 0.22);
  }}
  const propRatio = c.querySelector(".mg-prop-hero .ratio");
  if (propRatio) {{
    tl.fromTo(propRatio, {{ scale: 0.84, opacity: 0, filter: "blur(8px)" }},
      {{ scale: 1, opacity: 1, filter: "blur(0px)", duration: 0.55, ease: "expo.out", immediateRender: false }}, t + 0.08);
  }}
  const def = c.querySelector(".mg-def");
  if (def) {{
    tl.fromTo(def, {{ opacity: 0, y: 12 }},
      {{ opacity: 1, y: 0, duration: 0.26, immediateRender: false }}, t + 0.3);
  }}
  tl.to(w, {{ opacity: 0, duration: 0.18, ease: "power1.in" }}, t + Math.max(0.2, d - 0.18));
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
  // 文字接力（micro）：长卡的大字按台词气口分几段换着出，每段 data-t 由编导按字数占比算好。
  // 老段换新段时先收后弹，跟 MG 卡的进场同一条缓动，避免硬切换字。
  const mstack = c.querySelector(".mstack");
  if (mstack) {{
    const stg = Array.from(mstack.children);
    stg.forEach((s, i) => {{
      const at = t + parseFloat(s.dataset.t || 0);
      tl.set(s, {{ opacity: 0 }}, t);
      if (i > 0) tl.set(stg[i - 1], {{ opacity: 0 }}, at);
      tl.fromTo(s, {{ opacity: 0, y: 18 }},
        {{ opacity: 1, y: 0, duration: 0.3, ease: "power2.out", immediateRender: false }}, at);
    }});
  }}
  // 长卡的呼吸感：子件都进场后，均匀铺一整卡周期的 8% 轻亮度脉冲（0.36s 完成）。
  // 试过 1.2% 慢缩放——太温和，验收的帧差地板（0.6）量不出来，照样判「不是画面」；
  // 试过 floor((d-1.0)/1.6) 从头铺——尾部会剩 ≥2s 空窗（实测 8.6s 卡尾静止 2.0s 被判红），
  // 所以改成 round(d/1.6) 颗均匀铺满：首尾空窗都 ≤0.9s。
  if (d > 3.0) {{
    const pulses = Math.max(1, Math.round(d / 1.6));
    const spacing = d / pulses;
    for (let k = 0; k < pulses; k++) {{
      tl.to(c, {{ opacity: 0.92, duration: 0.18, yoyo: true, repeat: 1,
                  ease: "sine.inOut", immediateRender: false }}, t + 0.9 + k * spacing);
    }}
  }}
  // 天平：横杆先向下倾（只比原料价 → 左边沉），口径换成成品总价时，
  // 右边补上后落的矶码，横杆回平。形状即语义：一边沉一边轻＝不等价。
  const beam = c.querySelector(".beam");
  if (beam) {{
    const panL = c.querySelector(".pan-wrap.l");
    const panR = c.querySelector(".pan-wrap.r");
    const lateBlk = c.querySelectorAll(".blk.late");
    // 回平时刻不能听凭音频峰点——实测过一次：ct[1] 落在 3.2s，横杆 68.2s 才回平，
    // 卡 68.31s 就没了，等于没演完（像素量出来的，不是看出来的）。夹到卡的 45–60% 区间：
    // 前一半让观众看清「左边沉」，后一半留出回平与读结论的时间。
    const tipRaw = (ct.length > 1) ? ct[1] : d * 0.5;
    const tip = t + Math.min(Math.max(tipRaw, 1.0), Math.max(1.2, d * 0.55));
    // 倾角与杆端位移都从标记里读，不在 JS 重写一份几何：杆半长 300、7°→≈36px。
    // 杆与两只盘必须同步走完同一条缓动，否则吊线会从杆端脱开（第一版就脱了 7px）。
    const til = parseFloat(beam.dataset.tilt || "7");
    const dy = Math.round(300 * Math.sin(til * Math.PI / 180));
    tl.set(beam, {{ rotation: til, transformOrigin: "50% 50%" }}, t);
    if (panL) tl.set(panL, {{ attr: {{ transform: "translate(0," + dy + ")" }} }}, t);
    if (panR) tl.set(panR, {{ attr: {{ transform: "translate(0," + (-dy) + ")" }} }}, t);
    lateBlk.forEach((b) => tl.set(b, {{ opacity: 0, y: -34 }}, t));
    tl.to(beam, {{ rotation: 0, duration: 0.9, ease: "power2.out" }}, tip);
    if (panL) tl.to(panL, {{ attr: {{ transform: "translate(0,0)" }}, duration: 0.9, ease: "power2.out" }}, tip);
    if (panR) tl.to(panR, {{ attr: {{ transform: "translate(0,0)" }}, duration: 0.9, ease: "power2.out" }}, tip);
    lateBlk.forEach((b, i) => tl.to(b, {{ opacity: 1, y: 0, duration: 0.24, ease: "power2.out" }},
                                     tip - 0.12 + i * 0.08));
    // 结论行（比的对象换了，结论就变了）：横杆落定之后再出，留到卡片最后——
    // 它挂通用 .ct-last 时被压到卡末 0.45s（实测帧里根本没出现），这行是这句话的落点，不能省。
    const note = c.querySelector(".bal-note");
    if (note) {{
      tl.set(note, {{ opacity: 0, y: 10 }}, t);
      tl.to(note, {{ opacity: 1, y: 0, duration: 0.34, ease: "power2.out" }}, tip + 0.55);
    }}
  }}
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
{pkg_js}
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body>
</html>
"""


def fonts_hint(style) -> str:
    f = (style.get("subtitle") or {}).get("font")
    return str(f) if f else ""


# ---------------------------------------------------------------- MG（代码画动效卡）

# 五子系统里的 mg：这是**内容形态**决定的，不是风格决定的。
# 一句话结论 → 大字；数字/规模/时长 → stat；两三个步骤或并列条件 → steps；
# 两个方案对比 → versus；名词解释 → term；
# 局部占整体比例 → proportion；早晚时段接力 → day-night；并列名牌逐枚点亮 → chips。风格包只决定它长什么样（tokens）。
MG_KINDS = card_layout.MG_KINDS

# MG 卡的最短放映时长：按内容条数算，不是一律 1.5s。
# 原来所有卡都夹在 emphasis.hold_s（默认 1.0–1.5s），三条内容的 steps 卡
# 在 1.5 秒里读完＝一闪而过——这就是「MG 出现时间过短」的真因。
MG_MIN_S = {
    "stat": 1.8,
    "term": 2.2,
    "versus": 2.4,
    "proportion": 2.2,
    "day-night": 2.6,
    "chips": 2.4,
}
MG_ENTRY_MIN = 0.35       # 卡片至少离开切点这么远（再近就是「贴脸」）
MG_ENTRY_CALM = 2.5       # 块短于这个秒数就贴着块首出，不再往后推

# 卡要「看得完」。1.0–1.5 秒是一句大字（四五个字）的读速，不是一张信息卡的。
# 一张卡上同时有标题 + 数字 + 单位 + 说明 + 出处，按 srt-motion-cards 的展示
# 单位算法（中文每字 1 单位、每 3 个拉丁字母 1 单位、约 6–8 单位/秒的阅读速度），
# 20 单位上下就要 2.6 秒以上。所以卡的下限不再借用大字的 hold_s。
CARD_HOLD_MIN = 2.6       # 读完一张卡的下限
CARD_HOLD_MAX = 4.5       # 再长就是「卡在屏幕上赖着不走」
CARD_GAP = 0.20           # 相邻两张卡之间必须空这么久，否则画面在打架


def mg_min_s(kind: str, data: dict | None) -> float:
    if kind == "steps":
        n = len((data or {}).get("items") or [])
        return 0.6 + 0.7 * min(3, max(1, n))
    if kind == "chips":
        n = len((data or {}).get("items") or [])
        return 1.6 + 0.25 * min(4, max(2, n))
    return float(MG_MIN_S.get(kind) or 0.0)


def card_hold(kind: str, data: dict | None) -> float:
    """这张卡至少要放多久：内容越多越久，但夹在 [CARD_HOLD_MIN, CARD_HOLD_MAX]。

    按卡上真正要读的字数估：标题/数字/说明/出处都算，拉丁字 3 个折算 1 单位。
    估出来再和种类下限取大——宁可多留半秒，也不要观众追着字跑。
    """
    d = data or {}
    parts: list[str] = []
    for k in ("kicker", "value", "unit", "label", "title", "term", "def", "source",
              "total", "ratio", "part_label", "total_label", "relay", "tail"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    for side in ("left", "right", "day", "night"):
        s = d.get(side) or {}
        parts.extend(str(s.get(k) or "") for k in ("head", "body", "sub", "tag"))
    for it in (d.get("items") or []):
        if isinstance(it, dict):
            parts.extend(str(v) for v in it.values() if isinstance(v, str) and v.strip())
        elif isinstance(it, str) and it.strip():
            parts.append(it)
    units = 0.0
    for p in parts:
        cjk = sum(1 for ch in p if "\u4e00" <= ch <= "\u9fff")
        latin = len([ch for ch in p if ch.isascii() and ch.isalnum()])
        units += cjk + latin / 3.0
    guess = units / 7.0 + 0.6                      # 7 单位/秒 的读速 + 入场
    return round(max(mg_min_s(kind, d), min(CARD_HOLD_MAX, max(CARD_HOLD_MIN, guess))), 3)


def plate_glow(surface: dict | None):
    """tokens.surface.shadow → (颜色, 模糊半径, (x 偏移, y 偏移), 扩散)。

    一件事：底板投影与卡面板投影**共用同一条 token**，不另设第二个字段——
    两个字段早晚会漂。取的是其中**模糊半径最大的那条外投影**（`inset` 是面板的
    顶光，不是投影，跳过）；包没写 shadow 就不投（返回 None，像素不动）。

    为什么不明写 `box-shadow`：底板带羽化 mask，box-shadow 属于元素自身绘制，
    同层时会被同一张 mask 一起裁掉。“羽化边”与“硬投影边”只能二选一，
    所以投影单独一层：一块同尺寸圆角色块，用 filter:blur() 自己化成光晕。
    """
    css = str((surface or {}).get("shadow") or "")
    best = None
    for part in re.split(r",(?![^()]*\))", css):
        p = part.strip()
        if not p or p.startswith("inset"):
            continue
        col = re.search(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\))", p)
        if not col:
            continue
        # 先把颜色摘出去，剩下的按位置读长度：x y [模糊 [扩散]]。
        # 不能只抓「带 px 的数」——`0 26px 64px` 里第一个 0 没有单位，
        # 那样读出来会把模糊当成第二个偏移（实测：投影直接消失）。
        vals = re.findall(r"-?(?:\d+\.?\d*|\.\d+)",
                          p[:col.start()] + " " + p[col.end():])
        if len(vals) < 2:
            continue
        dx, dy = float(vals[0]), float(vals[1])
        blur = float(vals[2]) if len(vals) > 2 else 0.0
        spread = float(vals[3]) if len(vals) > 3 else 0.0
        if best is None or blur > best[0]:
            best = (blur, dx, dy, spread, col.group(1))
    if not best or best[0] <= 0:
        return None, 0.0, (0.0, 0.0), 0.0
    blur, dx, dy, spread, col = best
    return col, blur, (dx, dy), spread


def _mg_html(c: dict, i: int, style: dict | None = None,
             surface: dict | None = None) -> str:
    """一张 MG 卡 → DOM。所有内容来自元素上的 card.data，渲染器不编内容。"""
    d = c.get("data") or {}
    kind = c["kind"]
    dur = round(c["end"] - c["start"], 3)

    if kind == "stat":
        num = str(d.get("value") or "")
        unit = str(d.get("unit") or "")
        label = str(d.get("label") or c.get("text") or "")
        src = str(d.get("source") or "")
        approx = bool(d.get("approx"))
        body = ''
        if d.get("kicker"):
            body += f'      <div class="mg-kick">{esc(str(d["kicker"]))}</div>\n'
        body += (f'      <div class="mg-hero"><span class="num" data-to="{esc(num)}">{esc(num)}</span>'
                 f'<span class="unit">{esc(unit)}</span></div>')
        if label:
            body += f'\n      <div class="mg-label">{esc(label)}</div>'
        if src or approx:
            body += ('\n      <div class="mg-src">'
                     + (f'{esc(src)}' if src else '示意')
                     + ('　示意' if (approx and src) else '') + '</div>')
    elif kind == "steps":
        title = str(d.get("title") or c.get("text") or "")
        items = [str(x) for x in (d.get("items") or [])][:3]
        body = (f'      <div class="mg-title">{esc(title)}</div>' if title else '')
        body += '\n      <ol class="mg-steps">' + "".join(
            f'<li><b>{esc(str(k + 1).zfill(2))}</b><span>{esc(t)}</span></li>'
            for k, t in enumerate(items)) + '</ol>'
    elif kind == "versus":
        L = d.get("left") or {}
        R = d.get("right") or {}
        body = ('      <div class="mg-ver">'
                f'<div class="side l"><b>{esc(str(L.get("head") or ""))}</b>'
                f'<span>{esc(str(L.get("body") or ""))}</span></div>'
                '<div class="vs">VS</div>'
                f'<div class="side r"><b>{esc(str(R.get("head") or ""))}</b>'
                f'<span>{esc(str(R.get("body") or ""))}</span></div></div>')
        if d.get("tail"):
            body += f'\n      <div class="mg-tail">{esc(str(d["tail"]))}</div>'
    elif kind == "proportion":
        val = str(d.get("value") or "")
        tot = str(d.get("total") or "")
        unit = str(d.get("unit") or "")
        ratio = str(d.get("ratio") or "")
        part_label = str(d.get("part_label") or "")
        total_label = str(d.get("total_label") or "")
        label = str(d.get("label") or c.get("text") or "")
        kicker = str(d.get("kicker") or "")
        pct = 33.3
        if "percent" in d:
            try:
                pct = float(d["percent"])
            except (ValueError, TypeError):
                pass
        elif val and tot:
            try:
                v_n = float(re.sub(r"[^\d.]", "", val))
                t_n = float(re.sub(r"[^\d.]", "", tot))
                if t_n > 0:
                    pct = (v_n / t_n) * 100.0
            except (ValueError, TypeError):
                pass
        elif ratio and "/" in ratio:
            try:
                p_a, p_b = ratio.split("/", 1)
                pct = (float(p_a) / float(p_b)) * 100.0
            except (ValueError, TypeError):
                pass
        pct = max(8.0, min(92.0, pct))
        body = '      <div class="mg-prop">\n'
        if kicker:
            body += f'        <div class="mg-kick">{esc(kicker)}</div>\n'
        hero_tag = part_label or (f"占 {round(pct):d}%" if not ratio else "")
        body += (f'        <div class="mg-prop-hero"><span class="ratio">{esc(ratio or (val + "/" + tot))}</span>'
                 + (f'<span class="prop-tag">{esc(hero_tag)}</span>' if hero_tag else '')
                 + '</div>\n')
        part_tick = (f"{val}{unit}" if not part_label else f"{part_label} {val}{unit}").strip()
        total_tick = (f"总计 {tot}{unit}" if not total_label else f"{total_label} {tot}{unit}").strip()
        body += (f'        <div class="mg-prop-bar-wrap">\n'
                 f'          <div class="mg-prop-track"><div class="mg-prop-fill" style="width:{pct:.1f}%">'
                 f'<span class="mg-prop-pin"></span></div></div>\n'
                 f'          <div class="mg-prop-ticks"><span class="tick part">{esc(part_tick)}</span>'
                 f'<span class="tick total">{esc(total_tick)}</span></div>\n'
                 f'        </div>\n')
        if label:
            body += f'        <div class="mg-label">{esc(label)}</div>\n'
        body += '      </div>'
    elif kind == "day-night":
        day = d.get("day") or {}
        night = d.get("night") or {}
        relay = str(d.get("relay") or "接力")
        tail = str(d.get("tail") or c.get("text") or "")
        title = str(d.get("title") or "")
        body = '      <div class="mg-dn">\n'
        if title:
            body += f'        <div class="mg-title">{esc(title)}</div>\n'
        body += ('        <div class="mg-dn-relay">\n'
                 f'          <div class="side day">\n'
                 f'            <div class="dn-tag">{esc(str(day.get("tag") or "早市"))}</div>\n'
                 f'            <b class="dn-head">{esc(str(day.get("head") or "朝早"))}</b>\n'
                 f'            <span class="dn-body">{esc(str(day.get("body") or ""))}</span>\n'
                 + (f'            <span class="dn-sub">{esc(str(day["sub"]))}</span>\n' if day.get("sub") else '')
                 + '          </div>\n'
                 f'          <div class="dn-flow"><div class="dn-pill">{esc(relay)}</div>'
                 f'<div class="dn-arrow">→</div></div>\n'
                 f'          <div class="side night">\n'
                 f'            <div class="dn-tag">{esc(str(night.get("tag") or "晚市"))}</div>\n'
                 f'            <b class="dn-head">{esc(str(night.get("head") or "晚上"))}</b>\n'
                 f'            <span class="dn-body">{esc(str(night.get("body") or ""))}</span>\n'
                 + (f'            <span class="dn-sub">{esc(str(night["sub"]))}</span>\n' if night.get("sub") else '')
                 + '          </div>\n        </div>\n')
        if tail:
            body += f'        <div class="mg-tail">{esc(tail)}</div>\n'
        body += '      </div>'
    elif kind == "chips":
        title = str(d.get("title") or c.get("text") or "")
        raw_items = d.get("items") or []
        items = []
        for x in raw_items[:4]:
            if isinstance(x, dict):
                items.append((str(x.get("name") or ""), str(x.get("tag") or "")))
            else:
                items.append((str(x), ""))
        tail = str(d.get("tail") or "")
        body = '      <div class="mg-chips-wrap">\n'
        if title:
            body += f'        <div class="mg-title">{esc(title)}</div>\n'
        body += '        <div class="chips-grid">\n'
        for idx, (name, tag) in enumerate(items, 1):
            body += (f'          <div class="chip">\n'
                     f'            <span class="chip-num">{str(idx).zfill(2)}</span>\n'
                     f'            <span class="chip-name">{esc(name)}</span>\n'
                     + (f'            <span class="chip-tag">{esc(tag)}</span>\n' if tag else '')
                     + '          </div>\n')
        body += '        </div>\n'
        if tail:
            body += f'        <div class="mg-tail">{esc(tail)}</div>\n'
        body += '      </div>'
    else:  # term
        term = str(d.get("term") or c.get("text") or "")
        # 内层词头叫 mg-word：外层容器就是 mg mg-term，同名会让 `.mg-term` 同时命中两层（框套框）
        body = (f'      <div class="mg-word">{esc(term)}</div>'
                f'\n      <div class="mg-def">{esc(str(d.get("def") or ""))}</div>')
    # 外面裹一层定位层：位置与缩放来自 card_layout.py 算出的叠加态（T-019）。
    # 旧版是「一条固定百分比带」（21%–62%H）+ 整屏暗场——那条带落在口播近景上
    # 正好压在眼睛与鼻梁之间，第五轮审查就是拿这个打回的。没算过布局时
    # （老项目 / 兜底路径）仍退回 CSS 里那条带，行为与以前一致，不静默改变。
    lay = c.get("layout") or {}
    if lay.get("plate_px"):
        px_, py_, pw_, ph_ = lay["plate_px"]
        bx, by = (lay.get("box_px") or [0, 0])[:2]
        nw, nh = lay.get("box_nat_px") or [0, 0]
        mode = str(lay.get("mode") or "corner")
        _fp = max(0, int(lay.get("feather_px") or 0))
        # 羽化：两段**定宽**的 linear-gradient 取交集 = 四边各一条 FEATHER 像素宽的透明
        # 过渡带，并在板的边界上恰好降到 0。以前那版径向渐变要么够不到边（中间一段实心、
        # 边上硬切），要么越过边（羽化带落在板外，等于没羽化）——两种都不算羽化。
        mask = ("none" if (mode == "cutaway" or _fp <= 0) else
                f"linear-gradient(to right, rgba(0,0,0,0) 0px, #000 {_fp}px, "
                f"#000 calc(100% - {_fp}px), rgba(0,0,0,0) 100%), "
                f"linear-gradient(to bottom, rgba(0,0,0,0) 0px, #000 {_fp}px, "
                f"#000 calc(100% - {_fp}px), rgba(0,0,0,0) 100%)")
        # 底板外投影：同一条 tokens.surface.shadow 的最大模糊外投影，画成一层自模糊色块。
        # 整屏切出态不投影——那是“换了一张底”，不是“一块板压在上面”。
        g_col, g_blur, (g_dx, g_dy), g_sp = ((None, 0.0, (0.0, 0.0), 0.0)
                                             if mode == "cutaway"
                                             else plate_glow(surface))
        glow_css = ("" if not g_col else
                    f"--mg-glow:{g_col};--mg-glow-blur:{g_blur:g}px;"
                    f"--mg-glow-shift:{g_dx:g}px {g_dy:g}px;"
                    f"--mg-glow-spread:{(-g_sp if g_sp else 0.0):g}px;")
        # 底衬色温跟着底片走（T-042）：卡片拿到的 `plate_tint` 是 card_layout.plate_tint()
        # 从这一格中点帧实测出来的平均值，暖就暖、冷就冷。只有 focus_blur 用——
        # 那一种态「底片退后、板子承光」，色温不跟就会在两个语域之间各错一次。
        # 变量里要放 <image>：渲器把它当前面一层叠在光线与 scrim 之下。
        _tint = str(lay.get("plate_tint") or "") if mode == "focus_blur" else ""
        tint_css = (f"--mg-tint:linear-gradient({_tint},{_tint});" if _tint else "")
        plate_style = (f"left:{px_}px;top:{py_}px;width:{pw_}px;height:{ph_}px;"
                       f"--mg-plate-mask:{mask};{glow_css}{tint_css}")
        box_style = (f"left:{bx}px;top:{by}px;right:auto;bottom:auto;"
                     f"width:{nw}px;height:{nh}px;"
                     f"transform:scale({lay.get('scale')});transform-origin:top left;")
        blur_cls = (" mg-blur" if lay.get("blur_px") else "") + \
                   (" mg-cut" if mode == "cutaway" else "")
    else:
        plate_style = "left:0;top:0;width:100%;height:100%;--mg-plate-mask:none;"
        box_style, blur_cls, mode = "", "", "legacy"
    return (f'    <div id="mg-{i:02d}" class="clip mgwrap" '
            f'data-start="{c["start"]}" data-duration="{dur}" data-layout="{mode}">\n'
            f'      <div class="mgplate{blur_cls}" style="{plate_style}">'
            f'<i class="mp-glow"></i><i class="mp-surf"></i></div>\n'
            f'      <div class="mgbox" style="{box_style}">\n'
            f'        <div class="mg mg-{kind}">\n{body}\n        </div>\n'
            f'      </div>\n'
            f'    </div>')


def esc(t: str) -> str:
    return _html.escape(str(t), quote=False)


def build_html(*, slug, W, H, FPS, total, videos, caps, cards, style, fonts, grade,
               punch_map=None, pkg=None,
               fg_by_seg=None, fg_mode="browser", gens=None, scenes=None, audios=None,
               gen_spans=None, kw_top_override=None,
               mg_lo=None, mg_hi=None, mg_pad=None, mg_pad2=None, mg_gap=None,
               mg_gap2=None, mg_num=None, mg_unit=None, mg_label=None, mg_src=None,
               mg_h=None, mg_step=None, mg_term=None, mg_vh=None, mg_vs=None,
               mg_badge=None, mg_badge_t=None, mg_rad=None, mg_pan=None, mg_dot=None,
               mg_dot_w=None, mg_dot_r=None, mg_dot_t=None):
    sub = style.get("subtitle") or {}
    emph = style.get("emphasis") or {}
    # 字号按字体宽度反算：得意黑比思源窄 22%，同一个 80px 出来的字幕小一整圈。
    # 写死的 size_px 只当兜底。
    # 例外：字幕模板（`font_size_locked`）的字号**就是设计值**，按它画并按画幅等比缩放——
    # 模板的四套各自按自己的字体宽度比校准过字号（见 styles/subtitles.yaml 抬头）。
    if sub.get("font_size_locked") and sub.get("size_px"):
        cap_px = max(20, int(round(float(sub["size_px"]) * (W / 1080.0))))
    else:
        try:
            import fonts as fonts_mod
            cap_px = fonts_mod.size_px(sub.get("font"), W, fallback=int(sub.get("size_px") or 72))
        except Exception:                                          # noqa: BLE001
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
    # 卡面底板 / 遮罩 / 描边 / 字色（tokens.surface）：以前写死在下面这坨 CSS 里——
    # 冷黑蓝弹窗底（#0A0D13 / #0C0F15）加 90% 全屏黑遮罩，换包换不掉，餐饮素材
    # 被压成生冷灰暗色（T-018 事故）。现在只认包装包；包没写就退成**中性**深灰黑——
    # 渲染器不许再自己选语域，中性色至少不会替谁表态。
    _surf = _tk.get("surface") or {}
    # 底板可以是一条**纵向光线渐变**（T-042），也可以是老包那种单色。单色必须先包成
    # 同色渐变再往下走：`background-image` 的层只吃 <image>，一个裸颜色会让整层被丢弃
    # （`.mgplate.mg-cut > .mp-surf` 就是叠层的写法），而 `background:` 简写两种都吃。
    # 所以变量里**永远是 <image>**，单色 = 一段「两端同色」的渐变，像素差别为零。
    def _grad(v: str) -> str:
        s = str(v or "").strip()
        return s if "gradient(" in s else f"linear-gradient(180deg, {s}, {s})"

    _plate = _grad(_surf.get("plate") or "rgba(0,0,0,.62)")
    _border = str(_surf.get("border") or "rgba(255,255,255,.14)")
    _scrim = str(_surf.get("scrim") or
                 "radial-gradient(76% 56% at 50% 42%, rgba(0,0,0,.78) 0%, "
                 "rgba(0,0,0,.62) 46%, rgba(0,0,0,.28) 82%, rgba(0,0,0,.08) 100%)")
    _sink = str(_surf.get("ink") or "#FFFFFF")
    # rgba(var(--surface-ink-rgb),.78) 靠它推导次要字色
    try:
        _srgb = f"{int(_sink[1:3], 16)},{int(_sink[3:5], 16)},{int(_sink[5:7], 16)}"
    except (ValueError, IndexError):
        _sink, _srgb = "#FFFFFF", "255,255,255"
    try:
        _blur = float(_surf.get("blur_ness") or 0)
    except (TypeError, ValueError):
        _blur = 0.0
    # 「浮起」也是语域的一部分：一块板子压在食物上，没投影就像贴纸。
    # 缺省 none，即不收这一行——老包（P1/P2/P3）像素一个不动。
    _shadow = str(_surf.get("shadow") or "none")
    # 内侧 1px 倒角（T-042）：[顶光, 底暗]。它必须和 shadow **分开写**——
    # plate_glow() 是从 shadow 里挑「模糊最大的外投影」去做底板光晕的，倒角混进去
    # 会让两个用途抢同一个字段。缺字段就不加这两行（单值回退）。
    _edge = _surf.get("edge")
    _edge_css = (f"inset 0 1px 0 {_edge[0]}, inset 0 -1px 0 {_edge[1]}"
                 if isinstance(_edge, (list, tuple)) and len(_edge) == 2
                 and all(str(x or "").strip() for x in _edge) else "")
    _shadow_css = ", ".join(x for x in (_shadow if _shadow.strip().lower() != "none" else "",
                                        _edge_css) if x) or "none"
    # 底板（.mgplate > .mp-surf）的光线层：只加光，不另外压暗。缺字段 = none（不收这一层）。
    _light = str(_surf.get("light") or "").strip()
    _light_css = _light if "gradient(" in _light else "none"
    # 词条卡自己的内边距与行间：去掉的是「第二层框」，不是面板的呼吸。
    # 缺省值就是旧硬编码值（30/26 与 22），所以不写 = 视觉不变。
    _pxv = lambda v: max(12, int(round(float(v) * (W / 1080.0))))          # noqa: E731
    _tpad = _surf.get("term_pad")
    _tpad_css = (f"{_pxv(_tpad[0])}px {_pxv(_tpad[1])}px"
                 if isinstance(_tpad, (list, tuple)) and len(_tpad) == 2
                 else f"{_pxv(30)}px {_pxv(26)}px")                        # 旧值：mg_pan / mg_pad2
    _tgap = _surf.get("term_gap")
    _tgap_css = (f"{_pxv(_tgap)}px" if isinstance(_tgap, (int, float))
                 else f"{_pxv(22)}px")                                     # 旧值：mg_gap
    pack_vars = " ".join([
        f"--ground:{_pal.get('ground') or '#1A1410'};",
        f"--ink:{_pal.get('ink') or '#FFFFFF'};",
        f"--accent:{_pal.get('accent') or emph.get('keyword_color') or '#F6EB34'};",
        f"--accent2:{_pal.get('accent2') or '#C1121F'};",
        f"--radius:{int(_tk.get('radius') or 18)}px;",
        f"--stroke:{max(1, int(_tk.get('stroke') or 2))}px;",
        f"--surface-plate:{_plate};",
        f"--surface-plate-term:{_grad(_surf.get('plate_term') or _plate)};",
        f"--surface-border:{_border};",
        f"--surface-border-term:{_surf.get('border_term') or _border};",
        f"--surface-scrim:{_scrim};",
        f"--surface-ink:{_sink};",
        f"--surface-ink-rgb:{_srgb};",
        # blur_ness=0 → none：等于不收这一行，老包的像素一个都不动
        f"--surface-blur:{'none' if _blur <= 0 else f'blur({_blur:g}px)'};",
        f"--surface-shadow:{_shadow_css};",
        f"--surface-light:{_light_css};",
        f"--surface-term-pad:{_tpad_css};",
        f"--surface-term-gap:{_tgap_css};",
    ])
    # MG 卡的上沿：默认 0.21H，压在字幕带之上；pack 可用 card.mg_top_h 调
    mg_top = round(float((style.get("card") or {}).get("mg_top_h") or 0.21) * 100, 2)
    # 卡片区的上下沿（占屏高百分比）。上限压在字幕带之上：字幕中心 0.693H，
    # 字高约 0.03H，所以 0.62H 以下一定不撞字幕。
    _mgtop = float((style.get("card") or {}).get("mg_top_h") or 0.21)
    mg_lo = round(_mgtop * 100, 2)
    mg_hi = round((1.0 - 0.38) * 100, 2)
    # 全部尺寸按屏宽 1080 标定，换分辨率时整体等比
    _u = W / 1080.0
    _px = lambda v: max(12, int(round(v * _u)))          # noqa: E731
    mg_pad, mg_pad2, mg_gap, mg_gap2 = _px(28), _px(26), _px(22), _px(14)
    # 出处行 30px 在 1080 上偏小、压在暗背景上读不出来（原像素复核确认）→ 34px + 不透明
    mg_num, mg_unit, mg_label, mg_src = _px(214), _px(76), _px(48), _px(34)
    mg_h, mg_step, mg_term = _px(56), _px(46), _px(92)
    mg_vh, mg_vs, mg_badge, mg_badge_t = _px(50), _px(40), _px(104), _px(38)
    mg_rad, mg_pan = _px(26), _px(30)
    mg_dot, mg_dot_w, mg_dot_r, mg_dot_t = _px(34), _px(58), _px(29), _px(8)
    mg_prop_bar, mg_ratio = _px(28), _px(160)
    mg_flow_pill, mg_chip_num, mg_chip_name = _px(24), _px(22), _px(48)
    idc_x = round(float((style.get("_id_card") or {}).get("x") or 0.055) * 100, 2)
    idc_y = round(float((style.get("_id_card") or {}).get("y") or 0.155) * 100, 2)

    # 画面层与声音层可以是两个窗口（J-cut，`timeline.py` 写的 `pic_lead`）：
    # `pstart/pdur/pmedia` 是**画面**的起播/时长/媒体入点，`start/dur` 仍是声音窗口。
    # 没解耦的格子三个键都不存在，输出与改动前逐字符一致。
    def _pic(v: dict) -> tuple[float, float, float]:
        return (v.get("pstart", v["start"]), v.get("pdur", v["dur"]),
                v.get("pmedia", 0.0))

    def _v_tag(v: dict, vid: str, track: int, extra: str = "", src: str | None = None) -> str:
        p0, pd, pm = _pic(v)
        if v.get("decoupled"):
            # 解耦格：这条只管画面（`muted`，声音走下面单独的 <audio>）。
            # `data-media-start` 就是这一格在**它自己素材**里的入点，画面内容与
            # 声音仍同一时刻对齐——变的只是「什么时候切」这件事。
            # `data-has-audio` 必须去掉：lint 的 `video_muted_with_declared_audio` 会报，
            # 而且声音本来就不走这条（走 `<audio>`，字节与它自己一模一样）。
            extra = extra.replace(' data-has-audio="true"', "")
            return (f'    <video id="{vid}" class="clip" src="{src or v["src"]}" data-start="{p0}" '
                    f'data-duration="{pd}" data-media-start="{pm}" data-track-index="{track}" '
                    f'playsinline muted{extra}></video>')
        return (f'    <video id="{vid}" class="clip" src="{src or v["src"]}" data-start="{v["start"]}" '
                f'data-duration="{v["dur"]}" data-media-start="0" data-track-index="{track}" '
                f'playsinline{extra}></video>')

    v_html = "\n".join(
        _v_tag(v, f'v{v["n"]}', 0, ' data-has-audio="true"')
        for v in videos)
    # fg_mode="ffmpeg" 时这里不输出 <video>，抠像层改由本脚本在浏览器渲完后用
    # ffmpeg overlay 贴回去。原因：Chrome 把带 alpha 的 webm 交给硬件视频管线时
    # alpha 会被丢掉——画面上看不出（抠像视频和底片是同一帧，画成不透明正好等于原画），
    # 但它会盖住下面所有层：大字、身份卡一块都不出来。实测 snapshot 有、render 没有。
    fg_html = "" if fg_mode != "browser" else "\n".join(
        _v_tag(v, f"fg{i}", 1, " muted", src=fg_by_seg[i])
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
        f'{" cap-plate" if sub.get("cap_plate") else ""}'
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
        elif c["kind"] in MG_KINDS:
            card_html.append(_mg_html(c, i, style, _surf))
        else:
            # 大字一律单排、撑到画幅宽度的 70%（Jerry 2026-11-09：「字体还要放大 2-3 倍
            # 能撑满屏幕宽度的 70%，但所有的大字应该控制在一排出现，要进行适当的缩放」）。
            # 字号与上沿走 card_layout 那一份（T-045）：`check_keyword_text` 判的就是同一个
            # 字框，两边各写一套公式，判据量到的和画上去的早跑了。
            _px = card_layout.kw_font_px(c["text"], style, W, H)
            # 放脑后做不到干净时，改「头顶大字」：位置按人像头顶反算，
            # 顺手挂一层顶部渐变——不加的话，画面顶部的梁柱管道会从字缝里透出来。
            _top_h, _want_scrim, _own_top = card_layout.kw_top_of(
                c, style, W, H, override=(kw_top_override or {}).get(c.get("seg")))
            if _own_top:
                _inline = f' style="top:{_top_h * 100:.2f}%"'
                if _want_scrim:
                    card_html.append(
                        f'    <div class="scrim" data-start="{c["start"]}" data-duration="{d}"'
                        f' style="height:{card_layout.kw_scrim_h(_top_h) * 100:.1f}%"></div>')
            else:
                _inline = ""
            # 大字隔离层（T-045）：字压在杂乱背景上时，得有一层把「字底下的那几行像素」
            # 压下去的东西。包决定它长什么样（emphasis.isolate），卡自己开关（card.isolate）。
            # 单独一层而不是给字加 text-shadow：投影只舔笔画边，隔不开一个高频背景；
            # 也不要整屏暗场（口播格整屏暗场是 B7 红线）。
            if c.get("isolate"):
                _iso = card_layout.kw_isolate(
                    style, card_layout.kw_ink_box(c["text"], style, W, H, _top_h)["px"], W, H)
                _x, _y, _w2, _h2 = _iso["px"]
                card_html.append(
                    f'    <div class="kwpod" data-start="{c["start"]}" data-duration="{d}" '
                    f'style="left:{_x}px;top:{_y}px;width:{_w2}px;height:{_h2}px;'
                    f'border-radius:{_iso["radius_px"]}px;background:{_iso["rgba"]};'
                    f'filter:blur({_iso["feather_px"]}px)"></div>')
            card_html.append(
                f'    <div id="kw-{i:02d}" class="clip kw'
                f'{" kw-behind" if c["kind"] == "behind" else ""}" '
                f'data-start="{c["start"]}" '
                f'data-duration="{d}"{_inline}><span style="font-size:{_px}px">'
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

    # 模板才有的三样：字距/行距、强调的「加粗/放大/底衬」、整条纸带底衬。
    # 一律按「有没有这个键」输出：模板没写就是旧 CSS 的原值（1.28 / 0.01em / 800 / 无纸带），
    # 所以老包（P1–P5、talking-head-v1）出来的 HTML 与改动前逐字节一致。
    _ls = sub.get("letter_spacing")
    cap_ls = f"{float(_ls):g}em" if isinstance(_ls, (int, float)) else "0.01em"
    _lh = sub.get("line_spacing")
    cap_lh = f"{float(_lh):g}" if isinstance(_lh, (int, float)) else "1.28"
    cap_em_w = "900" if sub.get("em_bold") else "800"
    _eme: list[str] = []
    _sc = sub.get("em_scale")
    if isinstance(_sc, (int, float)) and abs(float(_sc) - 1.0) > 1e-6:
        _eme.append(f"display:inline-block;transform:scale({float(_sc):g});")
    if sub.get("em_plate"):
        # 强调块的留白**对称**且比纸带内缩一档（em_plate_pad）：贴边 + 齐高就成了第二块底板，
        # 观众读到的是「割裂」而不是「标记」。
        _ep = list(sub.get("em_plate_pad") or [0.04, 0.16])[:2]
        _e0 = float(_ep[0]) if _ep else 0.04
        _e1 = float(_ep[1]) if len(_ep) > 1 else 0.16
        # 字色由下面那条 `.cap .em` 统一给（这里再写一次就是两行同值，读起来像笔误）
        _eme.append(f"background:{sub['em_plate']};padding:{_e0:g}em {_e1:g}em;"
                    f"border-radius:.10em;")
    cap_em_extra = "".join(_eme)
    cap_plate_css = ""
    _cp = sub.get("cap_plate")
    if isinstance(_cp, dict):
        _pad = list(_cp.get("pad_em") or [0.26, 0.34])[:2]
        _p0 = float(_pad[0]) if _pad else 0.26
        _p1 = float(_pad[1]) if len(_pad) > 1 else 0.34
        _edge = str(_cp.get("edge") or "").strip()
        cap_plate_css = (f".cap.cap-plate span {{ background:{_cp.get('fill')};"
                         f"padding:{_p0:g}em {_p1:g}em;"
                         f"border-radius:{float(_cp.get('radius_em') or 0.16):g}em;"
                         + ("" if not _edge or _edge.lower() == "none"
                            else f"box-shadow:{_edge};")
                         # 这段是**普通字符串**（不是 f-string），所以收尾写一个 } 而不是 }}
                         + "text-shadow:none;-webkit-text-stroke:0; }")

    return CSS_TMPL.format(
        res="portrait" if H > W else "landscape", W=W, H=H, FPS=FPS, total=total, slug=slug,
        cap_ls=cap_ls, cap_lh=cap_lh, cap_em_w=cap_em_w,
        cap_em_extra=cap_em_extra, cap_plate_css=cap_plate_css,
        fr=fonts["sub"], fb=fonts["big"],
        grade=grade or "background:transparent;",
        cap_top=cap_top, cap_pad=cap_pad, cap_px=cap_px,
        # 纸感包是深墨字压在暗画面上读不出来（实测字幕带 p98 只有 152、字 30）→
        # subtitle.light_text: true 就把字换成奶白 + 深影。换字色不改版面，比垫纸底稳。
        cap_color=("#F6F1E7" if sub.get("light_text") else (sub.get("color") or "#FFFFFF")),
        cap_shadow=("0 3px 12px rgba(0,0,0,0.80)" if sub.get("light_text")
                    else (sub.get("shadow") or "0 3px 8px rgba(0,0,0,.75)")),
        cap_extra=(f'-webkit-text-stroke:{sub.get("stroke")};paint-order:stroke fill;'
                   if sub.get("stroke") else ""),
        cap_em=sub.get("em_color") or (emph.get("keyword_color") or "#FFD400"),
        kw_top=kw_top, kw_px=kw_px, kwb_top=kwb_top, kwb_px=kwb_px, mg_top=mg_top,
        mg_lo=mg_lo, mg_hi=mg_hi, mg_pad=mg_pad, mg_pad2=mg_pad2, mg_gap=mg_gap,
        mg_blur=card_layout.BLUR_PX,
        mg_gap2=mg_gap2, mg_num=mg_num, mg_unit=mg_unit, mg_label=mg_label,
        mg_src=mg_src, mg_h=mg_h, mg_step=mg_step, mg_term=mg_term, mg_vh=mg_vh,
        mg_vs=mg_vs, mg_badge=mg_badge, mg_badge_t=mg_badge_t, mg_rad=mg_rad,
        mg_pan=mg_pan, mg_dot=mg_dot, mg_dot_w=mg_dot_w, mg_dot_r=mg_dot_r,
        mg_dot_t=mg_dot_t,
        mg_prop_bar=mg_prop_bar, mg_ratio=mg_ratio,
        mg_flow_pill=mg_flow_pill, mg_chip_num=mg_chip_num, mg_chip_name=mg_chip_name,
        kw_color=emph.get("keyword_color") or "#FFD400",
        scrim_a=card_layout.KW_SCRIM_ALPHA,
        kw_extra=("background:linear-gradient(180deg,%s 0%%,%s 100%%);"
                  "-webkit-background-clip:text;color:transparent;" %
                  tuple((emph.get("gradient") or ["#FFE53B", "#FFB000"])[:2])
                  if emph.get("gradient") else ""),
        pack_vars=pack_vars, punch_js=punch_js, cap_en_gap=max(6, int(cap_px * 0.10)),
        idc_x=idc_x, idc_y=idc_y,
        videos=v_html, caps=c_html, cards="\n".join(card_html), fg=fg_html,
        s_html=s_html, a_html=a_html,
        gens="\n".join(gens or []), gen_css=GEN_CSS,
        pkg_css=(pkg or {}).get("css") or "",
        pkg_html=(pkg or {}).get("html") or "",
        pkg_js=(pkg or {}).get("js") or "",
        cap_entry=entry, cap_tween=tween)



# 卡片入场配什么音效：按卡片职责分，别每张都「嗖」一下
# 音效一律取自 CC0 库（Jerry 2026-11-09：「用现有的素材，不要自己生成」）。
# 名字对应 cc0_lib.SFX_MAP；增益由库里的 under_voiceover_db 给，不再拍脑袋写 -11。
# 增益在库给的值上**再抬 1 dB**：垫乐进来之后同一处音效更难听出来（实测同一条
# whoosh 在无垫乐时抬 18 dB、有垫乐时只剩 8–9 dB）。90ms duck 只能管住入场那一瞬。
CARD_SFX = {"keyword": ("pop-medium-in", 2.5), "behind": ("impact-light-firm", 1.0),
            "cutout": ("impact-light-firm", 1.0), "idcard": ("whoosh-medium-soft", 0.5),
            # gen 卡入场常落在人声起句上，shimmer 类（appear-*）能量散、被口播盖住
            # （实测 6.30s 净抬升只有 +0.9dB），换瞬态硬的 knock 类才穿得出来。
            "gen": ("impact-light-firm", 3.5)}

# 音效落在连续人声上，光靠调增益听不出来（实测只涨 1–3 dB）：得让声音给它让位。
# 90ms、-6dB，耳朵听不出人声被压，但音效的瞬态就露出来了。
# −6 dB / 90ms。试过 −12 dB / 120ms，结果更差：量出来的「抬升」主要来自底音，
# 把人声压狠了，抬升反而掉 6 dB（实测 24.12s 从 −7.3 掉到 −13.8）。让位要轻。
DUCK_DB = 0.35   # −9dB：评审实测 −6dB 的让位在限幅墙+同窗对账下几乎量不到，瞬态还是被口播起句盖住
DUCK_S = 0.09


def _fnum(x) -> float:
    """宽容转 float：转不出就返回 nan（nan ≠ nan，对账时自然判不一致）。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def gen_props_sfx(el: dict, proj: Path | None = None) -> list[dict]:
    """把 gen 元素的 ``props.events``（AI 视听方案 P2 音效编排）转成音效事件。

    每条是 ``{"t", "name", "gain_db"?}``，t 是**场景内相对秒**；这里换算到全片
    时间轴：overlay 轨用 ``el["at"]``，video 轨用 ``el["_start"]``（与卡片音效
    入场同一套分支），``t_global = start + t``。
    gain_db 缺省用该事件名在 CARD_SFX 里的档位，不在表里就 -6。
    单一真值源：任务单 ``scenes/<scene_spec>.json`` 的 ``audio.events`` 是锚点的
    第一出处——props.events 没写时由任务单编译，两边都写了就逐项对账（评审 P0-02）。
    防呆：props.events 不是 list / t<0 / name 为空（或 t 不是数）→ 打印警告、
    跳过那一条，**不炸渲**；返回值只含合法事件，保持确定性（按原顺序）。
    """
    evs = (el.get("props") or {}).get("events")
    sid = (el.get("props") or {}).get("scene_spec")
    spec_pairs = None
    spec_events = None
    if sid and proj is not None:
        sp = Path(proj) / "scenes" / f"{sid}.json"
        if sp.exists():
            try:
                raw = [e for e in ((json.loads(sp.read_text()).get("audio") or {}).get("events") or [])
                       if isinstance(e, dict)]
                spec_events = [{"t": _fnum(e.get("t")), "name": str(e.get("name") or ""),
                                **({"gain_db": e["gain_db"]}
                                   if e.get("gain_db") is not None else {})}
                               for e in raw]
                spec_pairs = [(s["t"], s["name"]) for s in spec_events]
            except Exception as e:                        # noqa: BLE001
                # 任务单存在却读不出 = 唯一真值源坏了。原来只打警告就退回 props.events
                # 照渲，等于给 G-05 留了一条旁路（2026-11-15 对抗审查 P2）。
                raise SystemExit(
                    f"✗ 任务单 scenes/{sid}.json 存在但读不出（{e}）——渲停。\n"
                    "  音效事件的唯一真值源坏了，不能拿 props.events 顶替（那正是 G-05 要拦的）")
    if evs is None:
        if spec_events:
            evs = spec_events
            print(f"  · props.events 未写，音效事件由任务单 scenes/{sid}.json 编译（{len(evs)} 条）")
        else:
            return []
    elif spec_pairs is not None:
        try:
            own_pairs = [(_fnum(e.get("t")), str(e.get("name") or ""))
                         for e in evs if isinstance(e, dict)]
            if own_pairs != spec_pairs:
                # 评审 G-05：事件唯一出处是任务单。两边不一致不再是警告后照渲——
                # 音效锚点是画面的动作节点，拿 props 自说自话就是拿旧动作骗签收。
                raise SystemExit(
                    f"✗ props.events 与任务单 scenes/{sid}.json 的 audio.events 不一致"
                    f"（props {len(own_pairs)} 条 / 任务单 {len(spec_pairs)} 条）——渲停。\n"
                    f"  改了动作锚点就同步任务单；或删掉 props.events 改由任务单编译。")
        except SystemExit:
            raise
        except Exception:                                 # noqa: BLE001
            pass
    if not isinstance(evs, list):
        print("  ⚠ props.events 不是 list，整段音效事件忽略", file=sys.stderr)
        return []
    if (el.get("track") or "video") == "overlay":
        start = float(el.get("at") or 0)
    elif el.get("_start") is not None:
        start = float(el["_start"])
    else:
        print("  ⚠ gen 元素没有 at/_start，props.events 没法落到时间轴，忽略",
              file=sys.stderr)
        return []
    out = []
    for ev in evs:
        if not isinstance(ev, dict):
            print(f"  ⚠ props.events 里有一条不是对象，跳过：{ev!r}", file=sys.stderr)
            continue
        name = str(ev.get("name") or "").strip()
        if not name:
            print("  ⚠ props.events 里有一条没写 name，跳过", file=sys.stderr)
            continue
        try:
            t = float(ev.get("t"))
        except (TypeError, ValueError):
            print(f"  ⚠ props.events[{name}] 的 t 不是数字，跳过", file=sys.stderr)
            continue
        if t < 0:
            print(f"  ⚠ props.events[{name}] 的 t<0（{t}），跳过", file=sys.stderr)
            continue
        gain = ev.get("gain_db")
        if gain is None:
            gain = CARD_SFX.get(name, (None, -6.0))[1]
        else:
            try:
                gain = float(gain)
            except (TypeError, ValueError):
                print(f"  ⚠ props.events[{name}] 的 gain_db 不是数字，用缺省档",
                      file=sys.stderr)
                gain = CARD_SFX.get(name, (None, -6.0))[1]
        # 对话式改片（R2）：元素级音效增减档「这段音效小声点」——
        # revise.py 写 props.sfx_gain_offset，不改任务单、不破签收指纹。
        off = (el.get("props") or {}).get("sfx_gain_offset")
        if off is not None:
            try:
                gain = round(gain + float(off), 2)
            except (TypeError, ValueError):
                pass
        out.append({"t": start + t, "name": name, "gain_db": gain,
                    "for": f"ev-{name}"})
    return out


def rotate_sfx(events: list[dict]) -> list[dict]:
    """同音色疲劳轮换：给排序后的 sfx_events 就地标 ``nth``。

    方案原则：同一场景限制重复相似音色。规则写死——只轮换**连续**同名：
    按 t 排序后，同名连续出现的第 1 次 nth=0，第 2 次 nth=1，第 3 次 nth=2…；
    中间隔了任何别的音效就重新从 nth=0 算。同名连续 ≤1 次时 nth=0，
    sfx_spec 回到旧路径，行为与现状完全一致。事件顺序本身是确定性的
    （构建顺序 + 稳定排序），所以同一时间轴永远同一轮换序列。
    """
    prev, run = None, 0
    for e in events:
        name = e.get("name")
        if name is not None and name == prev:
            run += 1
        else:
            run = 0
        e["nth"] = run
        prev = name
    return events


def sfx_spec(name: str, nth: int = 0) -> dict | None:
    """取一条音效的规格：文件、同步点、人声下的增益（外置 CC0 库优先，自动回退内置 CC0 包）。"""
    import cc0_lib
    if cc0_lib.sfx_available():
        return cc0_lib.pick_sfx(name, nth)
    return None


def coalesce_sfx(events: list[dict], min_gap: float = 0.25) -> list[dict]:
    """合并 250ms 内扎堆的自动包装音效（如 0.0s 开场推镜 + 0.0s 标题条 + 0.22s 贴纸），
    避免同一拍叠三声打成一团并连续压低人声。场景显式事件（ev-*）一律保留不合并。"""
    prio = {"punch": 5, "impact": 4, "impact-light-firm": 4, "flash": 4,
            "whoosh": 3, "whoosh-medium-soft": 3, "title": 2, "money": 2,
            "celebration": 2, "ding": 1, "pop": 1, "pop-medium-in": 1}
    kept: list[dict] = []
    for e in sorted(events, key=lambda x: float(x["t"])):
        is_explicit = str(e.get("for") or "").startswith("ev-")
        if not kept or is_explicit or str(kept[-1].get("for") or "").startswith("ev-"):
            kept.append(e)
            continue
        if float(e["t"]) - float(kept[-1]["t"]) >= min_gap:
            kept.append(e)
            continue
        # 250ms 内撞车：留优先级更高的那一声
        p_new = prio.get(str(e.get("name") or ""), 1)
        p_old = prio.get(str(kept[-1].get("name") or ""), 1)
        if p_new > p_old:
            kept[-1] = e
    return kept


@lru_cache(maxsize=128)
def _sfx_peak_db(path_s: str) -> float:
    """实测音效文件自身峰值电平（dBFS），消除不同 CC0 录音之间差出 12–16 dB 的原始电平差。"""
    import array
    import math
    import wave
    try:
        with wave.open(path_s, "rb") as wf:
            sw = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if sw == 2 and raw:
            arr = array.array("h")
            arr.frombytes(raw)
            pk = max((abs(x) for x in arr), default=0) / 32768.0
            if pk > 1e-6:
                return 20.0 * math.log10(pk)
    except Exception:
        pass
    # 非 16-bit WAV 兜底走 ffmpeg 解码单声道 s16le
    try:
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path_s,
                            "-f", "s16le", "-ac", "1", "-ar", "24000", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            import array
            import math
            arr = array.array("h")
            arr.frombytes(r.stdout[:len(r.stdout) - (len(r.stdout) % 2)])
            pk = max((abs(x) for x in arr), default=0) / 32768.0
            if pk > 1e-6:
                return 20.0 * math.log10(pk)
    except Exception:
        pass
    return -6.0


def mix_sfx(film: Path, events: list[dict], dst: Path, stem_out: Path | None = None) -> int:
    """把音效按时间点混进成片音轨。**必须发生在响度归一之前**——
    归一之后再混，音效会被一起抹平（实测 whoosh 只涨 1–3 dB，等于没打）。
    每条音效先按实测峰值对齐至 -3.0 dBFS 基准再加上事件微调增益，避免 -17 dBFS 的轻音被口播吞没；
    若传入 `stem_out`，同步导出经过相同让位包络的干净底音轨，供 check_sfx 同窗净抬升测量。"""
    if not events:
        return 1
    have = []
    for e in events:
        spec = sfx_spec(str(e.get("name") or "keyword"), int(e.get("nth") or 0))
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
    if stem_out is not None:
        fc.append(f"[0:a]{duck or 'anull'},asplit=2[bed][bedref]")
    else:
        fc.append(f"[0:a]{duck or 'anull'}[bed]")
    mixins.append("[bed]")
    for i, (e, p, spec) in enumerate(have, 1):
        # 起始时刻 = 画面时刻 − sync_at：库里的 sync_at 是「最该对上画面的那一点」，
        # 直接按画面时刻起播会让打击点晚半拍（whoosh 能晚 200ms 以上）。
        start = max(0.0, float(e["t"]) - float(spec.get("sync_at") or 0))
        ms = int(round(start * 1000))
        rel_gain = float(e.get("gain_db") if e.get("gain_db") is not None
                         else (spec.get("gain_db") or -3.0))
        pk_db = _sfx_peak_db(str(p))
        # 对齐至 -2.5 dBFS 基准（封顶补 +18 dB 防底噪放大），适配 -18 LUFS 人声底轨
        norm_boost = max(-12.0, min(18.0, -2.5 - pk_db))
        gain = round(norm_boost + rel_gain, 2)
        fc.append(f"[{i}:a]adelay={ms}|{ms},volume={gain}dB[s{i}]")
        mixins.append(f"[s{i}]")
    fc.append(f"{''.join(mixins)}amix=inputs={len(mixins)}:duration=first:normalize=0[aout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-ar", "48000", "-ac", "2", str(dst)]
    if stem_out is not None:
        cmd += ["-map", "[bedref]", "-vn", "-c:a", "aac", "-b:a", "128k", str(stem_out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        return 1
    return 0 if probe(dst).get("w") else 1


# ------------------------------------------------------------------ 人声降噪
#
# BRIEF 第 8 步「混音：人声降噪」——本仓长期以来只有侧链闪避与响度归一，降噪一直是空的。
# 参数依据、失真闸、量法全部在 scripts/denoise.py，这里只负责**接在链路里**。

def voice_denoise(film: Path, proj: Path) -> tuple[Path, dict | None]:
    """把「人声底轨」过一遍降噪，带失真闸；闸不过就原样退回，绝不交一条变闷的片。

    为什么接在 5.7（人声预归一）之前：
      · 此刻 `film` 的音轨**只有人声**——BGM 还没混（5.8）、音效还没打（5.9），
        降噪在物理上碰不到 BGM/音效，天然满足「只作用在人声段」；
      · 5.7 的 loudnorm 与 5.8 的 sidechaincompress 都拿这条人声当基准，
        底噪先下去，闪避的开启阈值才不会被嘶声顶着走。
    为什么不会破坏「音画同源」：`-c:v copy`，视频流一帧都不重编，画面侧字节未变；
    音频只走「同长度 PCM → 滤镜 → AAC」，`denoise.run_apply` 里两条硬守卫：
    **样本数逐样本相等**、**对齐滞后必须为 0**（afftdn 有 1200 样本固定延迟，靠 apad/atrim 补掉）。
    """
    import denoise as dn                                  # 与 --measure 同一份实现，不重复写
    if not dn.has_afftdn():
        print("  ！ffmpeg 里没有 afftdn 滤镜，跳过人声降噪（其余混音照旧）", file=sys.stderr)
        return film, None
    out = proj / "out"
    ref = out / "_hf_vo.wav"
    try:
        dn.to_ref_wav(film, ref, ch=1)
        measured = dn.public_view(dn.analyze(dn.decode_pcm(ref)))
    except Exception as e:                                 # noqa: BLE001
        print(f"  ！人声底噪量不出来（{e}），跳过降噪", file=sys.stderr)
        return film, None
    finally:
        ref.unlink(missing_ok=True)
    if not dn.need_denoise(measured):
        # 干净素材明确不动手：这才是「不无谓损伤」的证据，也写在报告里
        print(f"  人声降噪：停顿段残余 {measured['pause_residual_dbfs']:.1f} dBFS 本身干净"
              f"（阀值 {dn.NEED_DENOISE_PAUSE_DBFS:g}），不降")
        return film, {"skipped": "clean", **measured}
    dn_wav = out / "_hf_vo_dn.wav"
    try:
        ev = dn.run_apply(film, dn_wav, {"_name": "standard"}, ch=2)
    except dn.GateRejected as e:
        print(f"  ！人声降噪被失真闸拦下，用原始人声：{e}", file=sys.stderr)
        return film, {"rejected": str(e), **e.evidence}
    except Exception as e:                                 # noqa: BLE001
        print(f"  ！人声降噪没跑完（{e}），用原始人声", file=sys.stderr)
        return film, None
    # 塞回视频：视频流 copy，音频重编码成 AAC（后续 5.7 还要再过一遍 loudnorm，不能留 PCM）
    dst = out / "_hf_dn.mp4"
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(film),
                        "-i", str(dn_wav), "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                        "-ar", "48000", "-ac", "2", str(dst)],
                       capture_output=True, text=True)
    dn_wav.unlink(missing_ok=True)
    if r.returncode != 0 or not dst.exists() or not probe(dst).get("w"):
        print("  ！降噪音轨没塞回去，用原始人声", file=sys.stderr)
        dst.unlink(missing_ok=True)
        return film, None
    if abs(probe(dst)["dur"] - probe(film)["dur"]) > 0.02:   # 片长守卫
        print("  ！降噪后片长变了，放弃这次降噪", file=sys.stderr)
        dst.unlink(missing_ok=True)
        return film, None
    print(f"  人声降噪（{ev['chain']}）：停顿段 {ev['pause_before_dbfs']:.1f} → "
          f"{ev['pause_after_dbfs']:.1f} dBFS（降 {ev['pause_drop_db']:+.1f} dB）· "
          f"相关 {ev['corr']:.4f} · 2–6 kHz {ev['presence_rel_db']:+.1f} dB · "
          f"对齐滞后 {ev['lag_samples']} 样本")
    # 我替你做的决定：降噪是显式开关，但「阀值 / 参数 / 为什么要降」得留一行痕迹，
    # 写进 decisions.md（assumptions.py 读的就是它，不另实现一套）。
    try:
        from log_decision import append_row
        append_row(proj, said="混音：人声降噪（render_hf --denoise）",
                   did=(f"人声底轨 {ev['chain']}：停顿段 {ev['pause_before_dbfs']:.1f}→"
                        f"{ev['pause_after_dbfs']:.1f} dBFS，相关 {ev['corr']:.4f}，"
                        f"样本数与对齐滞后均过闸"),
                   verdict="accepted")
    except Exception:                                      # noqa: BLE001
        pass
    return dst, ev


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
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    try:
        j = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
        return float(j["input_i"]), float(j["input_tp"])
    except (ValueError, KeyError):
        return -99.0, 0.0


LAST_LOUDNESS: dict[str, float] = {}


def write_sfx_manifest(proj: Path, sfx_events: list[dict]) -> Path | None:
    """把这条片用了哪些声音、什么许可写进 out/sfx_manifest.json（T-055 授权留痕）。

    发布平台只验成片本身，但商用交付要能答「这段 BGM/音效从哪来的、能不能商用」。
    以往 BGM 只在 bgm.json 记了选曲、音效只记了时间点——文件、许可、哈希全链路无留痕。
    来源：CC0 库 → 许可 CC0 1.0（公有领域，可商用无需署名）；
    本地合成 → source=procedural，无第三方风险；
    外部文件 → licence=null，必须人工确认后才能商用。
    """
    import hashlib
    out = proj / "out"
    out.mkdir(parents=True, exist_ok=True)

    def _sha(p: str | Path) -> str | None:
        try:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except OSError:
            return None

    try:
        import cc0_lib
    except ImportError:
        cc0_lib = None

    bgm_row = None
    stamp = proj / "bgm.json"
    if stamp.exists():
        try:
            b = json.loads(stamp.read_text())
            if b.get("source") == "cc0" and b.get("file"):
                # 评审 G-07：许可只认 licence_of（库根有留痕文件才认 CC0）。
                # 查不到留痕就如实标未核验，绝不回退写死 "CC0 1.0 Universal"。
                lic = (cc0_lib.licence_of(b["file"]) if cc0_lib else None)
                bgm_row = {"source": "cc0" if lic else "unverified",
                           "title": b.get("title"), "file": b.get("file"),
                           "licence": lic, "sha256_16": _sha(b["file"]), "why": b.get("why"),
                           **({} if lic else {"note": "许可未核验：商用发布前必须人工确认授权"})}
            else:
                # 合成的（无 file）与外部指定的（有 file 无许可）都要如实写
                bgm_row = {"source": b.get("source") or "unknown", "title": b.get("title"),
                           "file": b.get("file"), "licence": None,
                           "sha256_16": _sha(b["file"]) if b.get("file") else None,
                           "note": "许可未核验：商用发布前必须人工确认授权"}
        except (json.JSONDecodeError, OSError):
            pass

    sfx_rows, seen = [], set()
    for e in sfx_events or []:
        name = e.get("name") or ""
        if not name:
            continue
        # 轮换后同一个 name 可能对应多个变体文件：按「实际用到的文件」去重留痕，
        # name 写原始事件名，id 写实际变体 id。
        spec = sfx_spec(name, int(e.get("nth") or 0))
        key = (name, (spec or {}).get("path") if spec else None)
        if key in seen:
            continue
        seen.add(key)
        if not spec:
            sfx_rows.append({"name": name, "source": "missing", "licence": None})
            continue
        p = spec.get("path") or ""
        # 评审 G-07：许可只认 licence_of 逐文件核验；spec 里的默认字符串不算证据。
        lic = (cc0_lib.licence_of(p) if (cc0_lib and p) else None)
        row = {"name": name, "id": spec.get("id"), "file": p,
               "licence": lic, "sha256_16": _sha(p) if p else None}
        if not p:
            row["source"] = "procedural"
            row["note"] = "本地代码合成，无第三方素材风险"
        elif lic is None:
            row["source"] = "unverified"
            row["note"] = "逐文件许可未核验：商用发布前必须人工确认授权"
        else:
            row["source"] = "cc0"
        sfx_rows.append(row)

    rows_all = [r for r in ([bgm_row] + sfx_rows) if r]
    n_unverified = sum(1 for r in rows_all if r.get("licence") is None
                       and r.get("source") != "procedural")
    lic_all = {r.get("licence") for r in rows_all if r.get("source") != "procedural"}
    if not lic_all:
        summary = "无可核验素材（全片音效/音乐均为本地代码合成）"
    elif lic_all == {"CC0 1.0 Universal"}:
        summary = ("全部 CC0 1.0 Universal（公有领域：可商用、可再分发、无需署名）")
    else:
        summary = (f"存在未核验或非 CC0 来源（{n_unverified} 条 licence=None）："
                   "商用发布前逐条人工确认")
    m = {"licence_summary": summary, "bgm": bgm_row, "sfx": sfx_rows}
    dst = out / "sfx_manifest.json"
    dst.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


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
    # 限幅点是**自适应**的，不是一个常数。
    #
    # 峰值和均值差得远的时候（b 片：输入 −18.9 LUFS，真峰却已经 −0.92），
    # 要涨 4.9 dB 就得压掉尖峰；限幅点钉在 0.56（−5 dBFS）时，压掉的能量
    # 和抬上去的一样多，8 遍下来只停在 −15.3，怎么迭代都不收敛。
    #
    # 所以从紧到松试：0.56 起步（AAC 编码的回峰最稳），压不上去就逐步放开到 0.72。
    # 真峰的上限仍然是 TP（−1 dBTP），放开的只是限幅器的工作点——
    # 能不能用在某一支片上，由最后量出来的真峰说了算，不由这个常数说了算。
    for limit in (0.56, 0.62, 0.68, 0.72):
        cur, last = src, measure(src)
        for k in range(8):
            dev = I - last[0]
            if abs(dev) <= 0.3 and last[1] <= TP:
                break
            # 往上推的时候多给 60%：限幅会吃掉一部分增益，按差值给永远差一口气。
            g = dev if dev < 0 else dev * 1.6
            nxt = dst.with_suffix(f".ln{k}.wav")
            rc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(cur), "-vn",
                 "-af", f"volume={g:.2f}dB,alimiter=limit={limit}:level=false:"
                        "attack=1:release=50",
                 "-c:a", "pcm_s16le", "-ar", "48000", str(nxt)],
                capture_output=True, text=True).returncode
            if rc != 0:
                return rc
            if cur != src:
                cur.unlink(missing_ok=True)
            cur = nxt
            last = measure(cur)
        if abs(I - last[0]) <= 0.3 and last[1] <= TP:
            break
        if cur != src:
            cur.unlink(missing_ok=True)

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
    # 量到的值要留痕：以前 meta.json 只记「渲了几格」，响度只在验收报告里出现，
    # 报告一被覆盖（换包试渲、重跑判据）就无处可查了。取 **AAC 之后**那一次，
    # 与 verify_film 量的同一支文件——两处读两套数迟早会打架。
    global LAST_LOUDNESS
    LAST_LOUDNESS = {"lufs": round(last[0], 2), "true_peak": round(last[1], 2)}
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
    # 人声降噪默认**关**：老项目读数一个字节都不许变（见 scripts/test_denoise.py ⑥）。
    # 开了才走 scripts/denoise.py 的「先量后降 + 失真闸」，且只作用于人声底轨。
    ap.add_argument("--denoise", action="store_true",
                    help="人声降噪（afftdn，带失真闸；只动 5.7 之前那条纯人声底轨）")
    ap.add_argument("--force", action="store_true",
                    help="方案没拍板也硬渲（正常不该用）")
    # 默认**开**着：`auto`＝项目里没有就按情绪从 CC0 库挑一首。
    # 原来默认 None，等于悄悄不出垫乐——连着三条片渲完都没人说，因为报告里没这一项。
    ap.add_argument("--bgm", default="auto",
                    help="BGM：auto＝用项目里的 bgm.wav（没有就从 CC0 库按情绪挑）；none＝不要；也可给文件")
    ap.add_argument("--bgm-mood", default="auto",
                    choices=["auto", "upbeat", "clear", "warm", "minimal", "tense", "hopeful"],
                    help="auto＝按 timeline/brief 情绪挑 BGM（默认）；也可指定")
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
        # approval.json 里是「谁在哪一拍点了头」，不是渲放行：放行只认 lock.json
        why += "；approval.json 只记拍板留痕，放过渲的是 lock.json（`plan_card.py --approve` 会一并写）"
    # 拍板②（Jerry 的 BRIEF「交互约束」）：确认拍摄任务卡或素材盘点。
    # 有确认对象却没留痕（或确认之后卡/盘点又改过）= 没签，不放行。
    # 零实拍、也没带素材盘点进来的片子（纯代码画面）没有要确认的对象，不算缺。
    # 判据只有一份：ideate.gate_state（留痕与失效都在那里判，这里不另写一套）。
    import ideate as gates                                    # noqa: PLC0415
    _st = gates.gate_state(proj)
    shots_wait = "" if _st[gates.GATE_SHOTS]["ok"] else _st[gates.GATE_SHOTS]["why"]
    lock_wait = "" if ok else why
    if (shots_wait or lock_wait) and not a.force:
        if lock_wait:
            print(f"还没拍板：{lock_wait}。\n"
                  "  先跑 `python3 scripts/plan_card.py --project …` 出方案，"
                  "人看过之后 `--approve`（会顺手写 lock.json）。", file=sys.stderr)
        if shots_wait:
            print(f"拍板② 还没签：{shots_wait}。", file=sys.stderr)
        print("  三次拍板：①选题 ②拍摄卡或素材 ③锁定音轨——缺的那几拍先签掉再渲。\n"
              "  （确实要硬渲就加 --force：它会把这几次拍板当成「跳过」写进日志。）",
              file=sys.stderr)
        return 4
    if ok:
        print(f"  {why}", file=sys.stderr)
    if a.force and (shots_wait or lock_wait):
        # --force 是逃逸口，不是默认路径：跳过了哪一拍必须自己报出来，不能“硬渲完没人知道”
        if lock_wait:
            print(f"  🔴 --force 跳过了拍板③：{lock_wait}", file=sys.stderr)
        if shots_wait:
            print(f"  🔴 --force 跳过了拍板②：{shots_wait}", file=sys.stderr)
    if not _st[gates.GATE_TOPIC]["ok"]:
        # ①不硬拦：老项目根本没有「--pick」这一步的历史，拦了等于让所有旧片渲不了。
        # 但每渲一次都把「这拍没留痕」念出来（并在方案卡上标红），不靠人自己去扒文件。
        print("  🟡 拍板①（确认选题）没有留痕：按默认第 1 套往下做——"
              "要留痕跑 `ideate.py --project … --pick N`", file=sys.stderr)
    lib = resolve_lib(proj, a.library)
    elements, tl_src = load_elements(proj)

    # 三正交里的「包装」：timeline.pack → packs/<id>.yaml；老项目没有 pack 就退回 styles/<style_pack>.yaml。
    # 这条顺序很重要——包装换了只换分层参数，**不许碰时间**（判据 41：换 look 切点序列不变）。
    tl_doc = json.loads((proj / "timeline.json").read_text())
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

    # 字幕模板（styles/subtitles.yaml）：优先级 = 项目显式选的模板 > 包装包自带的
    # subtitle 参数 > 默认模板。选了模板就把这套参数整段换掉（字号/字色/描边/阴影/
    # 纵位置/行宽/强调方案一起换），**没选就一个键都不碰**——老项目像素一致。
    try:
        sub_cfg = subs.subtitle_config(proj, style)
    except subs.SubtitleTemplateError as e:
        raise SystemExit(f"字幕模板配置有问题：{e}")
    if sub_cfg.get("template"):
        style["subtitle"] = subs.template_to_subtitle(sub_cfg["raw"])
        print(f"  字幕模板：{sub_cfg['name']}（{sub_cfg['id']}）· {sub_cfg['font']} "
              f"{sub_cfg['size_px']}px · 授权 {sub_cfg['font_license']}", file=sys.stderr)

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
    # J-cut（BRIEF 剪辑表第 4 步）：`timeline.py` 在**后一句**那个元素上写 `pic_lead`
    # （整数帧）。渲器只认这个数：画面窗口整体晚 `lead` 帧，声音窗口**一秒不动**。
    # 窗口怎么算（这也是 scripts/check_timing.py 的口径）：
    #   声音：[t, t+该格时长)        ← 与解耦前完全一样
    #   画面：[t+L_i, t+L_{i+1})     ← 画面内容仍然与声音同一时刻对齐（media-start = L_i）
    #        于是上一镜的画面自然多留 L_{i+1} 帧，下一镜的声音先进、画面后切。
    # 首格的 L 一定是 0（从 0 秒开始，不会黑屏）；末格的 L 也不写（片尾不会被截断），
    # 两条都由 timeline.apply_jcut 的位置闸和测试钉住。
    _jcut = jcut_leads(segs)
    _frm = 1.0 / float(FPS)
    # 画面层多要的尾巴：全片最大的 lead。只决定「切片时多切几帧」，不决定布局。
    _vpad = round(max((v[1] for v in _jcut.values()), default=0) * _frm, 3)
    if _vpad > 0:
        print(f"  J-cut：画面层比声音层晚 "
              f"{sorted({v[0] for v in _jcut.values() if v[0]})} 帧（切片多留 {_vpad * 1000:.0f}ms）")
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
            _ld, _ldn = _jcut.get(el.get("n"), (0, 0))
            _dec = bool(_ld or _ldn)
            try:
                if kind == "image":
                    cl = get_photo(lib, proj, el, W=W, H=H, FPS=FPS, grade=gd)
                else:
                    # 只有「画面要往后多留几帧」的那一格才需要旁路的静画文件；
                    # 只是「自己晚进场」的那一格，画面还在它自己那个片段里。
                    cl = get_clip(lib, proj, el, force=a.force_extract, extractor=a.extractor,
                                  W=W, H=H, FPS=FPS, grade=gd, overscan=a.overscan,
                                  decouple=bool(_ldn), vpad=_vpad)
            except SystemExit as e:
                print(f"  ！格 {el.get('n')} 造不出来：{e}", file=sys.stderr)
                n_fail += 1
                continue
            _t0 = round(t, 3)
            if _dec:
                _p0, _pd, _pm = pic_window(_t0, cl["dur"], _ld, _ldn, FPS)
                _src = cl["pic"] if _ldn else cl["path"]
                _avail = round(float(cl.get("pic_dur") or 0) - _pm, 3)
                if _ldn and _avail < _pd:
                    # 素材只剩这么多画面：明说一声再缩，不静默。
                    print(f"  ！格 {el.get('n')} 画面要 {_pd}s，素材只到 {_avail}s——"
                          f"画面尾巴缺 {round((_pd - _avail) * 1000)}ms", file=sys.stderr)
                    _pd = _avail
                videos.append({"n": el["n"], "src": f"clips/{_src.name}",
                               "asrc": f"clips/{cl['path'].name}",
                               "start": _t0, "dur": cl["dur"],
                               "pstart": _p0, "pdur": _pd, "pmedia": _pm,
                               "decoupled": True})
                # 声音层单独一条：**指回解耦前那个片段本身**——起点/时长与解耦前一模一样，
                # 连文件都是同一个（所以音轨不是「听起来一样」，是同一个文件）。
                audios.append({"n": el["n"], "src": f"clips/{cl['path'].name}",
                               "start": _t0, "dur": cl["dur"]})
            else:
                videos.append({"n": el["n"], "src": f"clips/{cl['path'].name}",
                               "start": _t0, "dur": cl["dur"]})
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
                el["_dur_q"] = round(dur, 3)
                if el.get("audio"):
                    ac = get_audio_cell(lib, proj, el)
                    if ac:
                        audios.append({"n": el["n"], "src": f"clips/{ac['path'].name}",
                                       "start": round(t, 3), "dur": ac["dur"]})
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
                             "audio": au, "medium": "talking",
                             "card": el.get("card"),
                             "evidence": el.get("evidence")})
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
    # 行宽与断词规则：模板会把行宽收窄、把 case_rules 里的词锁成原子；
    # 没选模板时传 None → subs 用回共用的 10 单位/字表（老项目一字不差）。
    caps = subs.build(active, by_id, chars_cache, lib, load_chars, colorize=colorize, fps=FPS,
                      max_units=(sub_cfg.get("max_units_per_line")
                                 if sub_cfg.get("template") else None),
                      case_rules=(sub_cfg.get("case_rules")
                                  if sub_cfg.get("template") else None))
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
            # 稳定度曲线：切点不仅要落在字界，还得落在「人站定了」的那半秒。
            # 光对字界会出现「字没错、但切进一张转头中间帧」，观众读作废镜头。
            _cur: list[float] = []
            try:
                _sa = (el.get("audio") or {}).get("asset") or el.get("asset")
                _sp = lib / _sa if _sa else None
                if _sp and _sp.exists():
                    import stability as _st
                    _cur = _st.curve(_sp, proj / "hf" / "work")
            except Exception:
                _cur = []
            rel = [0.0]
            for j in range(1, k):
                want = d * j / k
                if starts:
                    # 切点吸附到字界：切在词中间最刺眼（画面换的那一下刚好切掉半个字）
                    near = min(starts, key=lambda x: abs(x - (a_in + want)))
                    if abs(near - (a_in + want)) <= 0.35:
                        want = round(near - a_in, 3)
                        snap_hit += 1
                if _cur:
                    # 在 want ±0.28 里找最稳的时刻；稳得差不多时仍优先字界
                    # （所以先扣掉字界优惠，而不是二选一）。
                    cands = [round(want + off, 3) for off in
                             (-0.28, -0.20, -0.12, -0.06, 0.0, 0.06, 0.12, 0.20, 0.28)]
                    best, best_s = want, None
                    for c in cands:
                        if c - rel[-1] < 0.8 or d - c < 0.8:
                            continue
                        s = _st.motion_at(_cur, a_in + c) + (0.0 if abs(c - want) < 0.01 else 0.8)
                        if best_s is None or s < best_s:
                            best, best_s = c, s
                    want = best
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
    # 锚词对不上就停（T-034）。以前这里只往 stderr 打一句「沿用默认时机」，
    # 卡落在一个和这句话无关的时刻，卡准入 / 声音在前都看不出来（负样本 B：lag 3880ms 仍 0 error）。
    # 退出码 9：和 5（声音在前）、6（卡准入）、7（叠加态）、8（大字）分开。
    if ANCHOR_MISS and not getattr(a, "force", False):
        print("！卡的锚词在词级真值里找不到，拒绝渲染（用 --force 可越过）：", file=sys.stderr)
        for _m in ANCHOR_MISS[:8]:
            print(f"   · {_m}", file=sys.stderr)
        return 9
    # 卡能跨格活着，但不能和 overlay 生成物同屏抢位（同屏只讲一件事）。
    cards = [c for c in cards
             if not any(c["start"] < b0 - 0.05 and c["end"] > a0 + 0.05 for a0, b0 in ov_win)]
    for _i, _c in enumerate(cards, 1):
        _c["mg"] = f"mg-{_i:02d}"          # 卡的稳定 id：闸门报错、退字幕留痕都认它

    # 叠加态（T-019 / B7）：卡放哪、底片让不让位，逐卡算。
    # 以前这里只有一种态——整屏暗场 + 卡钉在 21%–62%H，口播近景上正好压在鼻梁。
    _med_of = {int(s["n"]): str(s.get("medium") or "") for s in segs}
    _ov_map = {str(s.get("n")): (s.get("card") or {}).get("layout")
               for s in segs if (s.get("card") or {}).get("layout")}
    _sctr = float(sub.get("center_y_px") or H * 0.674)
    _band_n = (round(max(0.0, (_sctr - 180) / H), 4), round(min(1.0, 360 / H), 4))
    if cards:
        # 卡的布局量的是「卡这一屏底下有什么」：人脸/主体遮罩/亮度。它按 `videos` 的
        # start/dur 去片段里取帧，所以**必须给声音时钟**（解耦后画面层晚了几帧，
        # 拿画面时钟去取帧会抽到 100ms 后的那一帧，卡的落位跟着变——那不是 J-cut
        # 要的东西：J-cut 只动「什么时候切」，不该改卡）。
        _v_audio = [{k: val for k, val in v.items()
                     if k not in ("pstart", "pdur", "pmedia", "decoupled")}
                    for v in videos]
        # 解耦格的画面文件可能是那条「多留 100ms」的旁路文件；取帧要用解耦前那条，
        # 否则同一张卡在不同时长/不同抽帧网格的文件上量出不同的主体占比。
        for _v in _v_audio:
            _v["src"] = next((x.get("asrc") for x in videos
                              if x["n"] == _v["n"] and x.get("asrc")), _v["src"])
        cards = card_layout.layout_cards(cards, proj=proj, videos=_v_audio, style=style,
                                        W=W, H=H, sub_band=_band_n, subs_on=False,
                                        media_of=lambda n: _med_of.get(int(n), ""),
                                        overrides=_ov_map)
        for _c in cards:
            _ly = _c.get("layout")
            if not _ly:
                continue
            print(f"  {_c['mg']} {_c['kind']:<7} {_c['start']:.2f}–{_c['end']:.2f}s · "
                  f"{_ly['mode']:<10} 框 {_ly['box_px']} · 人脸 IoU {_ly['face_iou'] * 100:.2f}%"
                  f" · {_ly['why']}", file=sys.stderr)

    # 双通道去重（B9）：重型 MG 卡在场期间，底部同期声字幕自动挂起。
    # 一屏只留一处文字焦点——卡上已经有数字/两栏/三步，底下再跳一句字幕，眼睛上下撕裂。
    # 挂起要留痕（subs.json 的 suppressed 列表）：不记就变成「悄悄漏字幕」，
    # 而它本来该是「 chars 说了但这一屏故意不写」。
    _suppressed: list[dict] = []
    if caps and ss_id != "none":
        _keep = []
        for _c in caps:
            _hit = None
            for _k in cards:
                _ly = _k.get("layout") or {}
                if _k.get("kind") not in card_layout.HEAVY_KINDS or not _ly:
                    continue
                _ov = min(float(_c["end"]), float(_k["end"])) - \
                    max(float(_c["start"]), float(_k["start"]))
                if _ov >= 0.15:
                    _hit = (_k, _ov)
                    break
            if _hit is None:
                _keep.append(_c)
                continue
            _k, _ov = _hit
            _suppressed.append({
                "card": _k.get("mg"), "kind": _k["kind"],
                "card_window": [round(_k["start"], 3), round(_k["end"], 3)],
                "cue": {"start": round(_c["start"], 3), "end": round(_c["end"], 3),
                        "text": _c.get("text")},
                "overlap_s": round(_ov, 3),
                "reason": "重型 MG 卡在场，同期声字幕挂起（B9 同屏单焦点）",
            })
        caps = _keep
        if _suppressed:
            print(f"  重卡退字幕：{len(_suppressed)} 条挂起（记入 out/subs.json 的 suppressed）",
                  file=sys.stderr)

    # 大字放人物背后：给这些段做一层带 alpha 的人像，叠在卡上面、字幕下面。
    # 层级：底(全画幅) → 卡 → 人像(透明背景) → 字幕。
    fg_by_seg = {}
    kw_top_override: dict[int, float] = {}
    if not a.no_cutout:
        want = {int(s_["n"]): s_ for s_ in active
                if str((s_.get("card") or {}).get("kind") or "") in ("behind", "cutout")}
        for n_, s_ in want.items():
            cl = next((v for v in videos if v["n"] == n_), None)
            if not cl:
                continue
            if a.fg_layer == "none":
                continue
            # 抠像层得盖满这一格的**画面窗口**（解耦格比声音窗口长 lead 帧），
            # 盖短了最后几帧人会从抠像层下面露出来。
            fgp = make_fg(proj, proj / "hf" / cl["src"], float(cl.get("pdur") or cl["dur"]), FPS)
            mdir = proj / "hf" / "work" / "mask" / Path(cl["src"]).stem
            st = mask_stats(mdir, W, H) if fgp else None
            if fgp and behind_ok(st):
                fg_by_seg[n_] = f"clips/{fgp.name}"
                print(f"  抠像 段 {n_}（{fgp.stat().st_size / 1e6:.1f} MB）"
                      f"· 头顶 {st['head_top']:.0%}H、人占 {st['face_w']:.0%}W")
            else:
                # 放脑后做不到干净，就走「头顶大字」：字底压在头顶上方 1.5%H，
                # 顶部加渐变压暗。这是实测过的路子——不加渐变的话，画面
                # 顶部那些梁柱管道会从字缝里透出来。
                kw_top_override[n_] = card_layout.kw_head_override(st)
                print(f"  · 段 {n_} 人像占 {((st or {}).get('face_w') or 0):.0%}W、"
                      f"头顶 {(st or {}).get('head_top', 0):.0%}H → 不放脑后，改头顶大字", file=sys.stderr)

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
        # suppressed 是**列表**：被重卡挂起的那几条字幕（含卡的 id 与理由）。
        # B9 要求「记入 suppressed 留痕」——留痕的是「哪一条、被哪张卡压住、为什么」，
        # 而不是一个布尔。整层字幕关掉（subtitle_system: none）单列 subtitle_suppressed。
        "suppressed": _suppressed,
        "subtitle_suppressed": caps_suppressed,
        "subtitle_system": ss_id,
        "band": [max(0, int(ctr) - 260), min(H, int(ctr) + 260)],
        "font": fonts_hint(style), "size": int(sub.get("size_px") or 0),
        "overflow": [],
        "speech_chars": speech_chars,
        "avg_chars": round(speech_chars / len(_stat_cues), 2) if _stat_cues else 0,
        "chars_per_s": round(speech_chars / speech_s, 2) if speech_s else 0,
        "colorize": colorize,
        # 生效的字幕口径：check_subs 按这一份判字号/行宽/强调色，不再自己猜
        "subtitle_source": sub_cfg.get("source"),
        "subtitle_template": sub_cfg.get("id"),
        "subtitle_max_units": sub_cfg.get("max_units_per_line"),
        "subtitle_size_px": sub_cfg.get("size_px"),
        "subtitle_font": sub_cfg.get("font"),
        "em_color": sub_cfg.get("em_color"),
        "engine": "hyperframes",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    # 大字自己落在哪，落到 cards.json 里（T-045）。`build_html` 画字用的是同一个函数，
    # 这里只是把结果记下来——`verify_film` 判「大字烧进画面」时要按**这张卡自己的位置**
    # 取样（卡一挪到头顶，固定中段带就量空，报假红）。判据自己再估一遍 = 两份几何，
    # 迟早漂（T-041 那条假红就是「带子写死在判据里」造成的）。
    for _c in cards:
        if str(_c.get("kind") or "") not in card_layout.KW_KINDS:
            continue
        _th, _sc, _own = card_layout.kw_top_of(
            _c, style, W, H, override=kw_top_override.get(int(_c.get("seg") or 0)))
        _kb = card_layout.kw_ink_box(str(_c.get("text") or ""), style, W, H, _th)
        if _kb:
            _c["kw_box_px"] = _kb["px"]
            _c["kw_top_h"] = _th
            _c["kw_font_px"] = _kb["font_px"]
    (proj / "out" / "cards.json").write_text(json.dumps(
        {"cards": cards, "engine": "hyperframes"}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # 声音在前（硬规则）：卡比锚词晚得不够、或者压根没有锚词，就不许往下渲。
    # 上游的做法是「生成后自动 check、有 error 直接退出，--force 才能越过」——
    # 渲完再发现抢拍，等于把几个小时的渲染白扔。
    if not getattr(a, "force", False):
        try:
            import check_timing
            _tv = [m for lv, m in check_timing.check(proj, lib) if lv == "error"]
            if _tv:
                print("！声音在前没过，拒绝渲染（用 --force 可越过）：", file=sys.stderr)
                for m in _tv[:6]:
                    print(f"   · {m}", file=sys.stderr)
                return 5
        except Exception as _e:                                    # noqa: BLE001
            print(f"！声音在前检查没跑起来：{_e}", file=sys.stderr)
            return 5

    # 凭空捏造的卡不许进片。check_timing 只管「卡比词晚不晚」，不管「卡上说的是真的吗」；
    # 两件不同的事，都得拦在渲之前——渲完再发现卡片自己编了个「明年开分店」，最贵。
    if not getattr(a, "force", False):
        try:
            import check_cards as _cc
            _cv = [m for lv, m in _cc.audit(proj, lib) if lv == "error"]
            if _cv:
                print("！卡准入没过，拒绝渲染（用 --force 可越过）：", file=sys.stderr)
                for m in _cv[:6]:
                    print(f"   · {m}", file=sys.stderr)
                return 6
        except Exception as _e:                                    # noqa: BLE001
            print(f"！卡准入检查没跑起来：{_e}", file=sys.stderr)
            return 6

    # 叠加态（B7 / B9）：卡压脸、口播格整屏暗场、重卡与字幕同屏，三件都不许进片。
    # 退出码 7：和 5（声音在前）、6（卡准入）分开，看日志就知道卡在哪一道。
    if not getattr(a, "force", False):
        try:
            import check_overlay as _co
            _ov_ = [(lv, m) for lv, m in _co.audit(proj) if lv == "error"]
            if _ov_:
                print("！叠加态没过（压脸 / 口播整屏暗场 / 重卡撞字幕），拒绝渲染：",
                      file=sys.stderr)
                for _lv, m in _ov_[:8]:
                    print(f"   · {m}", file=sys.stderr)
                return 7
        except Exception as _e:                                    # noqa: BLE001
            print(f"！叠加态检查没跑起来：{_e}", file=sys.stderr)
            return 7

    # 大字（keyword / behind）：压脸 / 可读性 / 单排宽度（T-045）。
    # 为什么这一道必须和上面三道并列而不是塞进 check_overlay：`check_overlay` 明说了
    # 「大字走 .kw 层，不在本判据内」——于是大字是唯一没有人量过位置的一层，
    # 成片里才有「黄字压在眼睛上」「字被杂乱背景吃掉」两件同时发生还全绿。
    # 退出码 8：和 5（声音在前）、6（卡准入）、7（叠加态）分开，看日志就知道卡在哪一道。
    if not getattr(a, "force", False):
        try:
            import check_keyword_text as _kt
            _kv = [(lv, m) for lv, m in _kt.audit(proj) if lv == "error"]
            if _kv:
                print("！大字没过（压脸 / 可读性 / 单排宽度），拒绝渲染：", file=sys.stderr)
                for _lv, m in _kv[:8]:
                    print(f"   · {m}", file=sys.stderr)
                return 8
        except Exception as _e:                                    # noqa: BLE001
            print(f"！大字检查没跑起来：{_e}", file=sys.stderr)
            return 8

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
    gsap_dst = hf / "assets" / "gsap.min.js"
    if not gsap_dst.exists():
        gsap_src = _gsap_src()
        if gsap_src is None:
            raise SystemExit("缺 gsap：找遍下面这些地方都没有 gsap.min.js——\n  "
                             + "\n  ".join(str(p) for p in GSAP_CANDIDATES())
                             + "\n装法：把任意一份 gsap.min.js 放到本仓 assets/gsap.min.js 即可。")
        shutil.copy2(gsap_src, gsap_dst)

    # 3) 收集网感包装层（顶部悬念标题条 / 矢量动态情绪贴纸 / 镜头特效）并写 HTML
    #    落角要避开平台 UI 遮挡区（BRIEF 自动质检），安全区与 verify_film 读同一份：
    #    platform_safe.load_spec —— 默认值 → 项目 platform-safe.json / brief.json。
    import platform_safe as _psafe
    _pspec, _ = _psafe.load_spec(proj)
    pkg = stickers_mod.collect_packaging(tl_doc, segs, total, W=W, H=H, spec=_pspec)
    html_txt = build_html(kw_top_override=kw_top_override, slug=proj.name, W=W, H=H, FPS=FPS,
                          total=total, videos=videos, caps=caps, cards=cards,
                          style=style, fonts=fonts, grade=a.grade, fg_by_seg=fg_by_seg,
                          punch_map=punch_map, pkg=pkg,
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
        fgs = [(hf / fg_by_seg[v["n"]], v.get("pstart", v["start"]), v.get("pdur", v["dur"]))
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

    # 5.65) 人声降噪：**必须在 5.7 预归一之前**——此刻音轨里只有人声，BGM/音效都还没混进来，
    # 所以降噪只可能作用在人声段上，碰不到垫乐和音效；视频流 `-c:v copy`，画面侧零改动。
    if a.denoise:
        raw, dn_report = voice_denoise(raw, proj)
        if dn_report is not None:
            try:
                (proj / "out" / "denoise.json").write_text(
                    json.dumps(dn_report, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass

    # 5.7) 人声底轨预归一至 -18 LUFS：无论来源是低电平领夹麦（-30 LUFS）还是高电平 TTS（-6 LUFS），
    # 进入 BGM 侧链闪避（-28 LUFS）与音效混音（0 dB 档）前统一电平基准，彻底消除音效被大声 TTS 吞没。
    if not a.no_loudnorm:
        vnorm = proj / "out" / "_hf_voc.mp4"
        r_vn = subprocess.run(
            [ffmpeg_bin(), "-y", "-v", "error", "-i", str(raw),
             "-c:v", "copy", "-af", "loudnorm=I=-18:TP=-2:LRA=11",
             "-ar", "48000", "-ac", "2", str(vnorm)],
            capture_output=True, text=True,
        )
        if r_vn.returncode == 0 and vnorm.exists() and probe(vnorm).get("w"):
            raw = vnorm

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
                r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-stream_loop", "-1",
                                    "-i", picked["path"],
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
        # gen 元素还能自带 props.events（场景内相对秒的音效事件，方案 P2），
        # 防呆在 gen_props_sfx 里：坏数据警告后跳过，不炸渲。
        sfx_events.extend(gen_props_sfx(el, proj))
    sfx_events.extend(pkg.get("sfx") or [])
    sfx_events = [e for e in sfx_events if 0 <= e["t"] <= total]
    sfx_events = coalesce_sfx(sfx_events, min_gap=0.25)
    # 同音色疲劳轮换要先按全片时间轴排好再算连续：稳定排序，同一构建顺序
    # 永远同一轮换序列（规则见 rotate_sfx）。
    sfx_events.sort(key=lambda e: float(e["t"]))
    rotate_sfx(sfx_events)
    if sfx_events and not a.no_sfx:
        nosfx_stem = proj / "out" / "stem-nosfx.m4a"
        sxd = proj / "out" / "_hf_sfx.mp4"
        if mix_sfx(raw, sfx_events, sxd, stem_out=nosfx_stem) == 0:
            raw = sxd
            print(f"  音效 {len(sfx_events)} 处（每张卡/贴纸/光效精准卡点，先混再归一）")
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
    for _tmp_name in ("_hf_voc.mp4", "_hf_sfx.mp4", "_hf_dn.mp4"):
        (proj / "out" / _tmp_name).unlink(missing_ok=True)
    # 响度写回 meta.json：lock.json 里记的是「拍板时的时长/格数」，响度只有渲完才知道。
    _mp = proj / "hf" / "meta.json"
    if _mp.exists():
        try:
            _m = json.loads(_mp.read_text())
            _m["duration_s"] = round(total, 2)
            if LAST_LOUDNESS:
                _m["lufs"] = LAST_LOUDNESS.get("lufs")
                _m["true_peak"] = LAST_LOUDNESS.get("true_peak")
            if dst.exists():
                _m["bytes"] = dst.stat().st_size
            _mp.write_text(json.dumps(_m, ensure_ascii=False, indent=1))
        except (json.JSONDecodeError, OSError):
            pass
    print(f"  → {dst}")
    _mf = write_sfx_manifest(proj, sfx_events)
    if _mf:
        print(f"  授权留痕 → {_mf}")
    print(f"  下一步：python3 scripts/verify_film.py --project {proj}")


if __name__ == "__main__":
    raise SystemExit(main())
