#!/usr/bin/env python3
"""生成一张「看片导航页」：所有成片一处点开，顺带把命名规则和改片的正确姿势写在同一页上。

为什么要它：片子散在三个库、六个目录里，文件名（`_hf_bed`、`P1-warm`、`lock.json`、
`film-hf`）全是给机器看的。人找一次问一次，就该有个页面替人记住。

    python3 scripts/showcase.py                          # 默认写到 ~/Downloads/CutSkills-产出/index.html
    python3 scripts/showcase.py --out <目录>
    python3 scripts/showcase.py --library <库根> ...      # 额外/替换要扫的库

页面是静态 HTML，双击就能开；视频用相对路径直接播（`file://` 下 Chrome 允许播本地 mp4）。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LIBS = [
    "/Users/jerry/Downloads/ouyangTestCut",              # 欧阳 · 点赞茶楼 dianzan-a/b/c
    "/Users/jerry/Downloads/柒造Video/素材",              # 柒造 · qizao-*
    str(REPO / "references" / "packtest-2026-11-08"),    # 打包试验 proj79
]
HERE = Path(__file__).resolve().parent

# 项目名 → 一句话（名字是给人看的，缩写在库里，含义写在这儿）
PROJECT_NOTES = {
    "dianzan": "「点赞茶楼」· 珠海金湾 600㎡ 粤语口播（牛哥）",
    "qizao-ground": "柒造 · 地面施工（熊工讲进口 vs 国产的漆）",
    "qizao-protect": "柒造 · 保护与收边（边角细节怎么贴）",
    "qizao-naming": "柒造 · 「柒造」这名字的来历",
    "qizao-lime-baxis": "柒造 · B 轴试验片（代码画的连续场景 + 人物画中画，13.9s）",
    "proj79": "打包试验片 · 六个包装 token 的对照（79s）",
    "selftest": "自检用的合成素材片（不交付，验证链路没断）",
}


def _pick(text: str) -> str:
    """按项目名前缀找一句人话说明。"""
    for key in sorted(PROJECT_NOTES, key=len, reverse=True):
        if text.startswith(key):
            return PROJECT_NOTES[key]
    return ""


def find_projects(libs: list[str]) -> list[Path]:
    out: list[Path] = []
    for lib in libs:
        base = Path(lib).expanduser()
        if not base.is_dir():
            continue
        cand = base / "_cutskills" / "projects"
        if cand.is_dir():
            out += [d for d in sorted(cand.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        if (base / "timeline.json").exists() or (base / "out").is_dir():
            out.append(base)
        else:
            for sub in sorted(base.iterdir()):
                if sub.is_dir() and (sub / "out").is_dir() and (sub / "timeline.json").exists():
                    out.append(sub)
    # 同一项目可能被两个入口扫到
    seen, uniq = set(), []
    for p in out:
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        uniq.append(p)
    return uniq


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def film_of(proj: Path) -> Path | None:
    for name in ("film-hf.mp4", "film.mp4"):
        q = proj / "out" / name
        if q.exists():
            return q
    return None


def readings(proj: Path) -> str:
    txt = ""
    try:
        txt = (proj / "verify-report.md").read_text(encoding="utf-8")[:2000]
    except OSError:
        pass
    m = re.search(r"🟢\s*(\d+)\s*·\s*🟡\s*(\d+)\s*·\s*🔴\s*(\d+)", txt)
    return f"🟢{m.group(1)} 🟡{m.group(2)} 🔴{m.group(3)}" if m else ""


def card(proj: Path, page: Path) -> str:
    film = film_of(proj)
    meta = read_json(proj / "hf" / "meta.json")
    brief = read_json(proj / "brief.json")
    intent = read_json(proj / "intent.json")
    lock = read_json(proj / "lock.json")

    name = proj.name
    note = _pick(name)
    thesis = (brief.get("change") or "").strip()
    raw = (intent.get("raw") or "").strip()

    dur = meta.get("duration_s")
    bits = []
    if dur:
        bits.append(f"{float(dur):.2f}s")
    bits.append(f"风格包 {meta.get('style') or '—'}")
    if meta.get("captions"):
        bits.append(f"字幕 {meta['captions']} 条")
    if meta.get("cards") is not None:
        bits.append(f"卡 {meta['cards']} 块")
    if meta.get("lufs") is not None:
        bits.append(f"{float(meta['lufs']):.2f} LUFS")
    rd = readings(proj)
    if rd:
        bits.append(rd)

    rel = os.path.relpath(film, page.parent) if film else ""
    media = (f'<video controls preload="metadata" playsinline src="{html.escape(rel)}"></video>'
             if film else '<div class="nofilm">没有成片（out/ 里只有中间物）</div>')

    links = []
    if (proj / "hf" / "index.html").exists():
        links.append(
            f'<div class="cmd"><code>cd "{proj / "hf"}" &amp;&amp; hyperframes preview --open</code>'
            '<span>工程页：能拉时间轴看每一帧、能当场改</span></div>')
    if (proj / "board").is_dir() and (proj / "board" / "index.html").exists():
        links.append(f'<a class="lnk" href="{html.escape(os.path.relpath(proj / "board" / "index.html", page.parent))}">看板（改片的表）</a>')
    for label, rel_p in (("时间轴真值源", "timeline.json"), ("验收报告", "verify-report.md"),
                         ("决策留痕", "decisions.md")):
        q = proj / rel_p
        if q.exists():
            links.append(f'<a class="lnk" href="{html.escape(os.path.relpath(q, page.parent))}">{label}</a>')

    lockline = ""
    if lock.get("sha256"):
        lockline = (f'<div class="lock">已拍板 · 锁 {lock["sha256"][:12]}'
                    f'{" · " + html.escape(lock.get("note", "")[:60]) if lock.get("note") else ""}</div>')

    return f"""
    <article class="card">
      {media}
      <div class="meta">
        <h3>{html.escape(name)}<span class="rd">{html.escape(rd)}</span></h3>
        <p class="note">{html.escape(note)}</p>
        {f'<p class="thesis">主张：{html.escape(thesis)}</p>' if thesis else ''}
        {f'<p class="raw">原始需求：{html.escape(raw[:120])}{"…" if len(raw) > 120 else ""}</p>' if raw else ''}
        <p class="bits">{html.escape(" · ".join(str(b) for b in bits))}</p>
        {lockline}
        <div class="links">{''.join(links)}</div>
        <div class="path">{html.escape(str(proj))}</div>
      </div>
    </article>"""


CSS = """
:root { --bg:#12100E; --card:#1C1917; --ink:#F4EFE7; --dim:#A79B8C; --accent:#F6EB34; --line:#332D28; }
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--ink); font:15px/1.7 -apple-system,"PingFang SC","Helvetica Neue",sans-serif;
       padding:48px 40px 96px; max-width:1680px; margin:0 auto; }
h1 { font-size:30px; letter-spacing:.01em; }
h1 small { display:block; font-size:14px; font-weight:400; color:var(--dim); margin-top:8px; }
h2 { font-size:19px; margin:52px 0 6px; padding-bottom:10px; border-bottom:1px solid var(--line); }
h2 span { font-size:13px; font-weight:400; color:var(--dim); margin-left:10px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:26px; margin-top:26px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; display:flex; flex-direction:column; }
.card video { width:100%; max-height:520px; background:#000; display:block; }
.nofilm { padding:40px 16px; text-align:center; color:var(--dim); font-size:13px; }
.meta { padding:16px 18px 18px; display:flex; flex-direction:column; gap:8px; }
h3 { font-size:16px; display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.rd { font-size:12px; color:var(--dim); white-space:nowrap; font-variant-numeric:tabular-nums; }
.note { font-size:13px; color:var(--accent); }
.thesis { font-size:13px; color:var(--ink); opacity:.9; }
.raw { font-size:12px; color:var(--dim); }
.bits { font-size:12px; color:var(--dim); font-variant-numeric:tabular-nums; }
.lock { font-size:12px; color:#8FBF7F; }
.links { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
.lnk { font-size:12px; color:var(--ink); text-decoration:none; border:1px solid var(--line);
       border-radius:6px; padding:3px 9px; }
.lnk:hover { border-color:var(--accent); }
.cmd { width:100%; }
.cmd code { cursor:copy; display:block; font-size:11.5px; background:#0C0B0A; border:1px solid var(--line); border-radius:6px;
            padding:7px 9px; color:#D8CFA6; overflow-x:auto; white-space:pre; }
.cmd span { font-size:11px; color:var(--dim); }
.path { font-size:11px; color:#6E655C; word-break:break-all; }
table { border-collapse:collapse; width:100%; margin-top:18px; font-size:13.5px; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--dim); font-weight:500; font-size:12.5px; }
td code { background:#0C0B0A; border-radius:4px; padding:1px 5px; font-size:12.5px; color:#D8CFA6; }
ul { margin:10px 0 0 20px; font-size:13.5px; }
li { margin:5px 0; }
.foot { margin-top:44px; font-size:12.5px; color:var(--dim); border-top:1px solid var(--line); padding-top:18px; }
"""


COPY_JS = """<script>
document.querySelectorAll(".cmd code").forEach(el => {
  el.title = "点一下复制命令";
  el.addEventListener("click", () => {
    navigator.clipboard.writeText(el.textContent.trim()).then(() => {
      const old = el.style.color; el.style.color = "#8FBF7F";
      setTimeout(() => { el.style.color = old; }, 600);
    }).catch(() => {});
  });
});
</script>
"""


def build(projects: list[Path], page: Path) -> str:
    cards = []
    for p in projects:
        if p.name.startswith("selftest"):
            continue
        cards.append(card(p, page))

    names = """
    <table>
      <tr><th>你会看到的名字</th><th>什么意思</th></tr>
      <tr><td><code>dianzan-a / -b / -c</code></td><td>「点赞茶楼」同一批素材剪出的三支片（a 讲厅与厨房、b 讲点心与桑拿鸡、c 讲装修进度）。<b>a/b 不许共用 B-roll</b>——两条都要发出去，撞素材会被观众认出来。</td></tr>
      <tr><td><code>qizao-ground / protect / naming</code></td><td>柒造三支：地面施工、保护收边、「柒造」名字的来历。<code>-01</code> 是同一主题的第几版。</td></tr>
      <tr><td><code>qizao-lime-baxis</code></td><td>B 轴试验片：用代码画一段连续场景、人物做画中画。<code>baxis</code> = B 轴。</td></tr>
      <tr><td><code>proj79</code></td><td>打包试验项目（79 秒），用来比六个包装 token 的效果，不对外发。</td></tr>
      <tr><td><code>timeline.json</code></td><td><b>唯一真值源</b>：哪一格用哪段素材、从几秒到几秒、配哪句话、挂哪张卡。改片就改它。</td></tr>
      <tr><td><code>brief.json</code> / <code>intent.json</code></td><td>需求：<code>intent</code> 是你原话，<code>brief</code> 是把原话问清楚之后的五问答案（受众、要改变什么、时长）。</td></tr>
      <tr><td><code>plan.json</code> / <code>thesis.json</code></td><td>剪辑方案（骨架与节奏）与主张（这条片只讲一件事，那件事是什么）。</td></tr>
      <tr><td><code>hf/</code></td><td>HyperFrames 工程：<code>index.html</code> 是合成页（HTML + GSAP 时间线），<code>clips/</code> 是逐格渲好的片段，<code>meta.json</code> 记了风格包与响度。</td></tr>
      <tr><td><code>out/film-hf.mp4</code></td><td><b>成片</b>（hf = HyperFrames 渲的）。旧的 <code>film.mp4</code> 是老 ffmpeg 链的残留，已经被清掉。</td></tr>
      <tr><td><code>out/_hf_raw</code> / <code>_hf_bed</code> / <code>_hf_fg</code></td><td>渲片时的中间层（无字幕底片 / 垫乐 / 抠像层），能重渲出来，所以定期清。</td></tr>
      <tr><td><code>out/subs.json</code> <code>cards.json</code> <code>shots.json</code></td><td>渲器真正落盘的字幕表 / 卡片表 / 镜头表。<b>验收读的是这几份</b>，不是时间轴。</td></tr>
      <tr><td><code>lock.json</code></td><td>拍板锁：时间轴改过没改过、渲的是哪一版。没它渲片会被闸门拦。</td></tr>
      <tr><td><code>verify-report.md</code></td><td>验收报告：🟢🟡🔴 三项计数与逐条读数。</td></tr>
      <tr><td><code>P1-warm / P2-paper / P3-dark / P4-canton-warm</code></td><td>风格包：一套「骨架穿的衣服」（底色、卡面、字重、调色）。换它不改剪辑。</td></tr>
      <tr><td>素材名 <code>261002_茶桌访谈_必用_兰姐口播1.mov</code></td><td>日期_批次_内容_级别_原编号。<b>级别</b>：必用 / 备用 / 不用——「不用」的不进片。</td></tr>
      <tr><td>片段名 <code>地面_备用_IMG_1770_0p50-7p75.mp4</code></td><td>内容_级别_设备编号_入点-出点（<code>p</code> 是小数点，避免文件名里出现点）。</td></tr>
    </table>"""

    howto = """
    <ul>
      <li><b>只想看片</b>：上面点播放。想改时间线就点「工程页」那行命令（<b>点一下就复制</b>），粘到终端跑，浏览器会开 studio。</li>
      <li><b>要改剪辑</b>：改 <code>timeline.json</code>（改格子、换 take、挪卡），然后跑
          <code>python3 scripts/render_hf.py --project &lt;项目&gt; --library &lt;库&gt;</code> 重渲。</li>
      <li><b>要换包装风格</b>：改 <code>hf/meta.json</code> 隔壁的 <code>timeline.json</code> 里 <code>pack</code> 字段（或渲染时 <code>--style P2-paper</code>），骨架不动。</li>
      <li><b>在 studio 里改了两笔</b>：studio 改的是 <code>hf/index.html</code>，<b>下次重渲会被覆盖</b>。
          想留住就把改动回写到 <code>timeline.json</code>，或者单独另存一份工程当试验。</li>
      <li><b>收工清理</b>：<code>python3 scripts/tidy.py --library &lt;库&gt; --apply</code>，只删能重建的中间物。</li>
    </ul>"""

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CutSkills 产出总表</title><style>{CSS}</style></head>
<body>
<h1>CutSkills 产出总表
  <small>八支片一处点开 · 名字含义都写在下面 · 生成时间见文件修改时间</small>
</h1>

<h2>片子<span>点开就播，不用找目录</span></h2>
<div class="grid">{''.join(cards)}</div>

<h2>这些名字什么意思<span>项目名 / 文件名 / 素材名</span></h2>
{names}

<h2>想动手改<span>三条路，各管一件事</span></h2>
{howto}

<p class="foot">这张页由 <code>scripts/showcase.py</code> 扫库生成，不是手写的——片子重渲、读数变了，重跑一次就同步。
片子路径都是相对路径，所以整页和成片要一起搬动。「工程页」那行命令<b>点一下就复制</b>。</p>
{COPY_JS}
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成看片导航页")
    ap.add_argument("--out", default="~/Downloads/CutSkills-产出", help="输出目录")
    ap.add_argument("--library", action="append", help="要扫的库根（可多次）")
    a = ap.parse_args(argv)

    libs = a.library or DEFAULT_LIBS
    projects = find_projects(libs)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(build(projects, page), encoding="utf-8")
    n = sum(1 for p in projects if not p.name.startswith("selftest") and film_of(p))
    print(f"→ {page}（{n} 支片 · 扫了 {len(libs)} 个库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
