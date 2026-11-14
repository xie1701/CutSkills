#!/usr/bin/env python3
"""CutSkills · 素材盘点：这批料能拍出什么片子（入口 2「带素材进来」的第 0 步）。

用户的顺序不是「先给选题、再找素材」，而是「我先给了一堆素材，你告诉我能拍什么」。
所以这一步不问他，先把库盘清：有多少料、讲了什么、能直接进片多少、毛病在哪，
再**反推** 3 套最合适的选题（方法来源只认 references/content-system.md 原文）。

三条硬规矩（BRIEF 的「必须真算，不许糊」）：

  ① 每个数字都能从 `_cutskills/index.json` 或探针读数复算，报告里不出现拍脑袋的估算；
  ② 画质/口令/品牌/底噪的判据一律**复用**既有实现，不另写一套平行判据：
     `index_assets` 的 L0 阈值（过暗/过亮/失焦）、`normalize.judge`（可变帧率）、
     `brand_safety.BRAND_PATTERN`（其它品牌字样）、`cue_guard.find_cues`（场记口令）、
     `denoise.py --measure`（底噪）。工具还没落地就**跳过并在报告里写清原因**，不许假装量过；
  ③ 口播主题的关键词必须能在转写原文里找到出处（每个词都带「哪个文件、哪句原话、第几个字」），
     找不到出处的词直接丢掉。

探针结果缓存在 `<库>/_cutskills/probe_cache.json`（按内容指纹认人，改名搬家不重测）。

用法：
  inventory.py --library "<素材库>"                     # 出盘点：inventory.json + inventory.md
  inventory.py --library "<素材库>" --vfr-limit 20      # 只测前 20 支帧率（报告会写明只测了多少）
  inventory.py --library "<素材库>" --audio-limit 20    # 只量前 20 支底噪
  python3 scripts/ideate.py --project <项目> --from-inventory "<素材库>"   # 接着出 3 套选题
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brand_safety                    # noqa: E402  第三方字样：判据只有这一份
import cue_guard                       # noqa: E402  场记口令：判据只有这一份
import index_assets as IA              # noqa: E402  L0 阈值：过暗/过亮/失焦只有一个真值源
from libs import ffmpeg_bin            # noqa: E402  ffmpeg 的唯一解析入口（别写死 PATH）
import normalize                       # noqa: E402  可变帧率判定：不许重写

SYS_DIR = "_cutskills"
CACHE_NAME = "probe_cache.json"
REPORT_MD = "inventory.md"
REPORT_JSON = "inventory.json"
SIGNATURE = "CutSkills 素材盘点"        # 认自己写过的报告（别覆盖别人手写的同名文件）

TARGET_W, TARGET_H = 1080, 1920        # 交付目标：竖屏 1080×1920
# 过暗/过亮/失焦直接取 index_assets 的 L0 阈值，不在这里另立一套
LUMA_DARK, LUMA_BRIGHT = IA.LUMA_DARK, IA.LUMA_BRIGHT
BLUR_SOFT, BLUR_HARD = IA.BLUR_SOFT, IA.BLUR_DO_NOT_USE

LOW_PEAK_DB = -20.0                    # 峰值低于此值 = 「音量过低」（对白要贴在 -6~-3 dBFS）
TOO_SHORT_S = 1.0                      # 短于 1 秒的段基本剪不进去
GRAIN_WINDOWS = (0.3, 0.65)            # 采样窗位置（占全片比例）
GRAIN_WIN_S = 1.5                      # 每窗长度
# 高频颗粒读数阈值。读数 = 画面与自身 1.2px 高斯模糊之差的平均亮度（0–255 标度）：
# 颗粒/噪点越重读数越高，但**镜头本身的高频细节也会推高读数**，所以这条只作「该复核」的
# 提示级判据，不当硬拦截。4.0 取自柒造库实测分布（0.9–4.5，p90 附近）。
GRAIN_HOT = 4.0

USABLE_GRADES = ("must-use", "backup")           # 能进片的分档
GRADE_ORDER = {"must-use": 0, "backup": 1, "do-not-use": 2}
PHOTO_SECONDS = 3.0                              # 一张照片按 3 秒进片的编排假设（写进报告口径）


# ---------------------------------------------------------------- 小工具

def _num(v):
    """数字 + NaN/Inf 清洗：索引里真有 `blur.mean = NaN`（滤镜没吐读数），不能带进 JSON。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def hms(sec) -> str:
    """秒 → 人话时长（老板不看 9188.4 秒）。"""
    s = int(round(float(sec or 0)))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分"
    if m:
        return f"{m} 分 {ss} 秒" if ss else f"{m} 分"
    return f"{ss} 秒"


def segs_of(asset: dict) -> list[dict]:
    """素材 → 段（in/out/grade/……）。照片的段是 0 秒，但也有分级。"""
    out = []
    for s in asset.get("segments") or []:
        out.append({
            "in": _num(s.get("in")) or 0.0,
            "out": _num(s.get("out")) or 0.0,
            "grade": str(s.get("grade") or ""),
            "shot": s.get("shot"),
            "tag": (str(s.get("tag") or "").strip() or None),
            "summary": s.get("summary"),
            "action": s.get("action"),
            "why": s.get("why"),
            "flags": [str(f) for f in (s.get("flags") or [])],
        })
    return out


def span_of(s: dict) -> tuple[float, float]:
    return float(s["in"]), max(float(s["in"]), float(s["out"]))


def seg_seconds(asset: dict, s: dict) -> float:
    if asset.get("kind") != "video":
        return 0.0
    a, b = span_of(s)
    return max(0.0, b - a)


def usable_spans(asset: dict) -> list[tuple[float, float]]:
    """可进片的时间区间（must-use + backup），合并成不相交的段。"""
    raw = [span_of(s) for s in segs_of(asset) if s["grade"] in USABLE_GRADES]
    return _merged(raw)


def _merged(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _overlap(a0: float, a1: float, spans: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(a1, b) - max(a0, a)) for a, b in spans)


def load_index(lib: Path) -> dict:
    p = lib / SYS_DIR / "index.json"
    if not p.exists():
        raise SystemExit(f"这个库里没有索引：{p}\n先把料收进来：python3 scripts/index_assets.py "
                         f"--library \"{lib}\"")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"索引读不出来（{e}）：{p}")


# ---------------------------------------------------------------- ① 总量

def compute_totals(assets: list[dict]) -> dict:
    kinds = Counter(a.get("kind") for a in assets)
    dur = 0.0
    usable = backup = direct = 0.0
    photo_usable = 0
    for a in assets:
        dur += _num(a.get("duration")) or 0.0
        for s in segs_of(a):
            sec = seg_seconds(a, s)
            if s["grade"] in USABLE_GRADES:
                usable += sec
                if s["grade"] == "backup":
                    backup += sec
                else:
                    direct += sec
        if a.get("kind") == "photo" and any(s["grade"] in USABLE_GRADES for s in segs_of(a)):
            photo_usable += 1

    by_date: dict[str, dict] = {}
    for a in assets:
        key = str(a.get("shoot_date") or "日期未知")
        g = by_date.setdefault(key, {"date": key, "assets": 0, "duration_s": 0.0, "usable_s": 0.0})
        g["assets"] += 1
        g["duration_s"] += _num(a.get("duration")) or 0.0
        g["usable_s"] += sum(seg_seconds(a, s) for s in segs_of(a) if s["grade"] in USABLE_GRADES)

    by_scene: dict[str, dict] = {}
    for a in assets:
        scenes = [s["tag"] for s in segs_of(a) if s["tag"]]
        key = Counter(scenes).most_common(1)[0][0] if scenes else \
            f"未标场景（{a.get('batch') or '根目录'}）"
        g = by_scene.setdefault(key, {"scene": key, "assets": 0, "duration_s": 0.0,
                                     "usable_s": 0.0, "speech_s": 0.0})
        g["assets"] += 1
        g["duration_s"] += _num(a.get("duration")) or 0.0
        g["usable_s"] += sum(seg_seconds(a, s) for s in segs_of(a) if s["grade"] in USABLE_GRADES)
        g["speech_s"] += sum(max(0.0, (_num(p.get("end")) or 0) - (_num(p.get("start")) or 0))
                             for p in ((a.get("transcript") or {}).get("phrases") or []))

    r2 = lambda x: round(float(x), 1)  # noqa: E731
    return {
        "assets": len(assets),
        "video": kinds.get("video", 0),
        "photo": kinds.get("photo", 0),
        "audio": kinds.get("audio", 0),
        "doc": kinds.get("doc", 0),
        "duration_s": r2(dur),
        "usable_s": r2(usable),
        "direct_use_s": r2(direct),
        "backup_s": r2(backup),
        "photo_usable": photo_usable,
        "by_date": [dict(v, duration_s=r2(v["duration_s"]), usable_s=r2(v["usable_s"]))
                    for _, v in sorted(by_date.items())],
        "by_scene": [dict(v, duration_s=r2(v["duration_s"]), usable_s=r2(v["usable_s"]),
                          speech_s=r2(v["speech_s"]))
                     for _, v in sorted(by_scene.items(), key=lambda kv: -kv[1]["duration_s"])],
    }


# ---------------------------------------------------------------- ② 口播与主题

# 关键词的「功能字 / 口水字」过滤：带这些字的 n-gram 不是主题，是语法。
# 只做成分过滤，不做语义联想——语义靠词频与「同一说法跨多支素材出现」。
STOP_CHARS = set("的了吗呢吧啊呀哦嗯呃着过们和与及而且但就也都很是不没我你他她它这那哪个些"
                 "把被给对从到会能要想说做有在一二三四五六七八九十零两秒分钟块点次遍"
                 # 粤语虚词/语气词：柒造与欧阳两个真库都是粤语口播，这些字出现在词里就说明
                 # 抽到的是语法，不是主题（"哋嘅""嗰度"曾经排在主题词第一位）
                 "哋嘅咗咁嚟喺嗰冇啲唔係乜咩噉囉喇啦㗎咋嘛喎啩")
# 主题词的停用词表：中文没有分词器也不做词性标注，靠一份常见虚词/泛动词/副词表把语法成分挡掉，
# 剩下的靠「跨多支素材反复出现」筛成主题。这张表只做成分过滤，不做语义联想。
STOP_WORDS = set("""
或者 如果 但是 因为 所以 而且 虽然 不过 然后 就是 还是 只是 还有 以及 并且 于是 因此
其实 可能 应该 需要 可以 没有 不是 我们 你们 他们 她们 它们 自己 大家 别人 人家
什么 怎么 为什么 哪个 哪些 这个 那个 这样 那样 这种 那种 这些 那些 这里 那里 这边 那边
里面 外面 上面 下面 前面 后面 中间 之间 以后 之前 之后 当时 现在 刚才 以前 未来
一直 一定 一起 一样 一般 一些 一点 一下 一边 非常 特别 十分 比较 更加 真的 确实 实在
简直 当然 居然 竟然 到底 究竟 已经 正在 曾经 刚刚 马上 立刻 经常 常常 总是 有时 偶尔
大概 也许 或是 要不然 否则 反而 甚至 除了 关于 对于 由于 根据 按照 通过 为了 除非
只要 只有 无论 不管 即使 哪怕 既然 尽管 因而 接着 最后 首先 其次 再次 另外 此外
同时 等等 出来 进去 过来 回来 起来 下来 上来 出去 看到 知道 觉得 认为 感觉 希望 打算
开始 结束 继续 完成 出现 变成 成为 属于 包括 使用 进行 有点 有些 很多 不少 全部 所有
每个 各个 各种 第一 第二 第三 一个 两个 三个 四个 五个 一半 一天 一年 一次 一遍
本身 简单 朋友 东西 事情 时候 地方 问题 内容 视频 意思 地点 情况 状态
肯定 完全 后来 比如 作为 实际上 任何 就像 好像 比如说 然后呢 对不对 是不是
会不会 能不能 要不要 有没有 这样吧 那么 这一块 那一块 而已 的话 为什 多少 一样
# 粤语口播里的虚词/口头禅（欧阳那个库是粤语，不挡掉的话主题词全是"同埋""好似"）
同埋 好似 好多 咩样 边度 呢度 嗰度 唔系 有啲 咁样 即系 梗系 而家 依家 一齐 好正
我哋 你哋 佢哋 睇下 倾下 呢个 嗰个 咩嘢 好多嘢 其实咧 就系 都系 唔知 唔好 点样
""".split())


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def keyword_evidence(assets: list[dict], kw: str, limit: int = 3) -> list[dict]:
    """关键词 → 转写原文里的出处。找不到就是找不到（返回空表），绝不编。

    每条出处写全三样：哪个素材、哪句原话、在这句里的第几个字。
    测试会拿它对拍索引里的转写原文（反向断言：原文没有的词必须返回空）。
    """
    if not kw:
        return []
    hits: list[dict] = []
    for a in assets:
        for p in ((a.get("transcript") or {}).get("phrases") or []):
            text = str(p.get("text") or "")
            at = text.find(kw)
            if at < 0:
                continue
            hits.append({"asset": a.get("id"), "text": text, "at": at,
                         "start": _num(p.get("start")), "end": _num(p.get("end"))})
            break                       # 一支素材给一句原话就够举证
        if len(hits) >= limit:
            break
    return hits


def extract_keywords(assets: list[dict], topn: int = 10, min_assets: int = 3) -> list[dict]:
    """从转写里抽主题词：n-gram（2–4 字）按「跨多少支素材出现」排序，再做长词吸收。

    长词吸收是关键：`艺术涂料` 和 `艺术`、`涂料` 的覆盖面一样，但只有长词是主题，
    所以当某个更长的词把短词的覆盖面全占了，短词丢掉。这样报告里不会出现一堆碎片。
    """
    df: Counter = Counter()            # 词 → 出现它的素材数
    hits: Counter = Counter()          # 词 → 总出现次数
    per_asset: list[Counter] = []
    for a in assets:
        phrases = ((a.get("transcript") or {}).get("phrases") or [])
        if not phrases:
            continue
        cnt: Counter = Counter()
        for p in phrases:
            text = "".join(ch for ch in str(p.get("text") or "") if _is_cjk(ch))
            for n in (2, 3, 4):
                for i in range(len(text) - n + 1):
                    g = text[i:i + n]
                    if any(ch in STOP_CHARS for ch in g):
                        continue
                    cnt[g] += 1
        per_asset.append(cnt)
        for g, c in cnt.items():
            df[g] += 1
            hits[g] += c

    cands = [g for g, d in df.items() if d >= min_assets and hits[g] >= max(6, min_assets)]
    # 长词吸收：短词的素材覆盖被某个包含它的长词吃下（≥80%）→ 短词不是主题。
    # 拿 80% 而不是 100%，是因为 ASR 的错字/切词会让长词的覆盖略低于它的碎片
    # （柒造库里「艺术涂料」df=5，碎片「术涂」df=6）；吸收池用**全量** n-gram，
    # 停用词只用来挡输出，不挡吸收，否则「实际上」被挡掉后「际上」就会漏出来。
    multi_stop = [w for w in STOP_WORDS if len(w) >= 2]
    keep = []
    for g in cands:
        # 整词命中停用词，或**包含**一个停用词（"大家可以" 里含 "可以"）→ 是语法不是主题
        if g in STOP_WORDS or any(w in g for w in multi_stop):
            continue
        absorbed = any(len(h) > len(g) and g in h and df[h] >= df[g] * 0.8 for h in df)
        if not absorbed:
            keep.append(g)
    # 排序：覆盖面（df）为纲，长词给一点优先（长 = 更具体：艺术涂料 比 涂料 更像个主题）
    keep.sort(key=lambda g: (-(df[g] + max(0, len(g) - 2) * 1.2), -hits[g], g))

    out = []
    for g in keep[:topn]:
        ev = keyword_evidence(assets, g)
        if not ev:
            continue                    # 出处都举不出来，宁可不要这个词
        out.append({"keyword": g, "assets": df[g], "hits": hits[g],
                    "evidence": ev})
    return out


def speech_stats(assets: list[dict]) -> dict:
    n = 0
    total = usable = 0.0
    rows = []
    for a in assets:
        phrases = ((a.get("transcript") or {}).get("phrases") or [])
        if not phrases:
            continue
        n += 1
        us = usable_spans(a)
        tot = use = 0.0
        for p in phrases:
            s = _num(p.get("start")) or 0.0
            e = _num(p.get("end")) or 0.0
            if e <= s:
                continue
            tot += e - s
            use += _overlap(s, e, us)
        total += tot
        usable += use
        rows.append({"asset": a.get("id"), "speech_s": round(tot, 1),
                     "usable_s": round(use, 1), "phrases": len(phrases)})
    rows.sort(key=lambda r: -r["speech_s"])
    return {"assets_with_speech": n,
            "speech_total_s": round(total, 1),
            "speech_usable_s": round(usable, 1),
            "top_assets": rows[:8]}


# ---------------------------------------------------------------- ③ 可用片段

def compute_usable(assets: list[dict]) -> dict:
    grades: dict[str, dict] = {g: {"segments": 0, "seconds": 0.0} for g in GRADE_ORDER}
    clips = []
    for a in assets:
        for s in segs_of(a):
            g = s["grade"] if s["grade"] in GRADE_ORDER else "unknown"
            d = grades.setdefault(g, {"segments": 0, "seconds": 0.0})
            d["segments"] += 1
            d["seconds"] += seg_seconds(a, s)
            if g in USABLE_GRADES:
                a0, b0 = span_of(s)
                clips.append({
                    "asset": a.get("id"), "kind": a.get("kind"),
                    "in": round(a0, 2), "out": round(b0, 2),
                    "seconds": round(seg_seconds(a, s), 1),
                    "grade": g, "shot": s["shot"], "tag": s["tag"],
                    "summary": (s["summary"] or s["action"] or "").strip() or None,
                    "why": (s["why"] or "").strip() or None,
                })
    for d in grades.values():
        d["seconds"] = round(d["seconds"], 1)
    clips.sort(key=lambda c: (GRADE_ORDER.get(c["grade"], 9), -c["seconds"]))
    return {"grades": grades,
            "direct_use_s": grades.get("must-use", {}).get("seconds", 0.0),
            "with_backup_s": round(grades.get("must-use", {}).get("seconds", 0.0)
                                   + grades.get("backup", {}).get("seconds", 0.0), 1),
            "top": clips[:12]}


# ---------------------------------------------------------------- ④ 画质与音频问题

def _cache_path(lib: Path) -> Path:
    return lib / SYS_DIR / CACHE_NAME


def load_cache(lib: Path) -> dict:
    p = _cache_path(lib)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cache(lib: Path, cache: dict, assets: list[dict]) -> None:
    """只留还在索引里的素材（素材删了/改名了，缓存跟着瘦身）。"""
    live = {str(a.get("uid")) for a in assets if a.get("uid")}
    for sec in ("vfr", "grain", "denoise"):
        cache[sec] = {k: v for k, v in (cache.get(sec) or {}).items() if k in live}
    _cache_path(lib).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _cache_key(asset: dict) -> str:
    return str(asset.get("uid") or asset.get("id"))


def _stale(asset: dict, cached: dict | None) -> bool:
    if not cached:
        return True
    return cached.get("fingerprint") != asset.get("fingerprint")


def vfr_of(asset: dict, lib: Path, cache: dict) -> dict:
    """可变帧率：直接调 normalize.judge（判据只有这一份，不重写）。"""
    key = _cache_key(asset)
    c = (cache.get("vfr") or {}).get(key)
    if not _stale(asset, c):
        return c
    path = lib / str(asset.get("id"))
    if not path.exists():
        rec = {"verdict": "unknown", "reason": "文件不在库里，判不了"}
    else:
        try:
            j = normalize.judge(path)
            rec = {"verdict": j.get("verdict"), "reason": j.get("reason")}
            basis = j.get("basis") or {}
            rec["r_fps"] = _num(basis.get("r_fps"))
            rec["avg_fps"] = _num(basis.get("avg_fps"))
        except Exception as e:                                   # noqa: BLE001
            rec = {"verdict": "unknown", "reason": f"帧率判定没跑起来（{e}）"}
    rec["fingerprint"] = asset.get("fingerprint")
    cache.setdefault("vfr", {})[key] = rec
    return rec


_GRAIN_VF = ("scale=480:-2,format=gray,split[a][b];[b]gblur=sigma=1.2[c];"
             "[a][c]blend=all_mode=difference,signalstats,"
             "metadata=mode=print:file={meta}")


def _grain_window(path: Path, ss: float, win_s: float) -> list[float]:
    with tempfile.TemporaryDirectory(prefix="grain_") as td:
        meta = Path(td) / "m.txt"
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-y",
               "-ss", f"{max(0.0, ss):.2f}", "-t", f"{win_s:.2f}", "-i", str(path),
               "-vf", _GRAIN_VF.format(meta=meta), "-f", "null", "-"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if not meta.exists():
            return []
        vals = []
        for line in meta.read_text(errors="ignore").splitlines():
            if "lavfi.signalstats.YAVG=" in line:
                try:
                    vals.append(float(line.rsplit("=", 1)[1]))
                except ValueError:
                    pass
        return vals


def grain_of(asset: dict, lib: Path, cache: dict) -> dict:
    """颗粒/噪点读数（补量项：索引里的探针不产这项读数）。

    读数 = 画面与自身 1.2px 高斯模糊之差的平均亮度。取两个采样窗的中位数，
    避免把一次转场糊成「整条高噪点」。
    """
    key = _cache_key(asset)
    c = (cache.get("grain") or {}).get(key)
    if not _stale(asset, c):
        return c
    path = lib / str(asset.get("id"))
    dur = _num(asset.get("duration")) or 0.0
    rec: dict = {"reading": None, "windows": 0,
                 "fingerprint": asset.get("fingerprint")}
    if not path.exists() or dur <= 0.2:
        rec["reason"] = "文件不在库或时长几乎为零，量不了"
    else:
        vals = []
        for frac in GRAIN_WINDOWS:
            ss = max(0.0, dur * frac - GRAIN_WIN_S / 2)
            win = _grain_window(path, ss, min(GRAIN_WIN_S, max(0.4, dur)))
            if win:
                vals.append(sum(win) / len(win))
        if vals:
            vals.sort()
            rec["reading"] = round(vals[len(vals) // 2], 2)
            rec["windows"] = len(vals)
        else:
            rec["reason"] = "抽不到画面帧，量不了"
    cache.setdefault("grain", {})[key] = rec
    return rec


def measure_noise_floor(lib: Path, assets: list[dict], cache: dict, limit: int = 0) -> dict:
    """底噪/音量：复用 `denoise.py --measure`（工具没落地就跳过，并在报告里写清原因）。"""
    status = cache.get("denoise_status") or {}
    need = []
    for a in assets:
        if a.get("kind") not in {"video", "audio"} or not a.get("audio"):
            continue
        key = _cache_key(a)
        c = (cache.get("denoise") or {}).get(key)
        if _stale(a, c):
            p = lib / str(a.get("id"))
            if p.exists():
                need.append((a, p))
    if limit:
        need = need[:limit]
    if need:
        with tempfile.TemporaryDirectory(prefix="dn_") as td:
            out = Path(td) / "denoise.json"
            cmd = [sys.executable, str(HERE / "denoise.py"), "--measure",
                   *[str(p) for _, p in need], "--out", str(out)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
                err = (r.stderr or "").strip().splitlines()
                tail = err[-1] if err else ""
            except (subprocess.SubprocessError, OSError) as e:     # noqa: BLE001
                r, tail = None, f"{type(e).__name__}: {e}"
            doc = None
            if r is not None and out.exists():
                try:
                    doc = json.loads(out.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    doc = None
            if doc:
                by_path = {str(p.resolve()): a for a, p in need}
                for row in doc.get("samples") or []:
                    a = by_path.get(str(Path(row.get("file") or "").resolve()))
                    if not a:
                        continue
                    cache.setdefault("denoise", {})[_cache_key(a)] = {
                        "fingerprint": a.get("fingerprint"),
                        "pause_residual_dbfs": _num(row.get("pause_residual_dbfs")),
                        "noise_floor_dbfs": _num(row.get("noise_floor_dbfs")),
                        "snr_db": _num(row.get("snr_db")),
                        "need_denoise": bool(row.get("need_denoise")),
                        "threshold_dbfs": _num(doc.get("threshold_dbfs")),
                    }
                status = {"ok": True, "reason": "",
                          "measured_at": str(doc.get("measured_at") or ""),
                          # 阈值取工具报告里那一份，不在这里抄死一个数
                          "threshold_dbfs": _num(doc.get("threshold_dbfs"))}
                # 个别素材的音频解码器缺失（如 iPhone 空间音频 apple_apac）会让 denoise
                # 非零退出，但它已经量到的都写进报告了：这种要如实记成「这几支没量到」，
                # 不能把整项判成「工具坏了」。
                got = len(doc.get("samples") or [])
                if got < len(need):
                    status["note"] = (f"这一轮 {len(need)} 支里有 {len(need) - got} 支没量到："
                                      f"{tail[:120]}")
            elif r is not None and r.returncode == 0 and not out.exists():
                status = {"ok": False, "reason": "denoise 没写出报告文件（--out 路径没生成）"}
            else:
                rc = "启动失败" if r is None else f"退出码 {r.returncode}"
                if cache.get("denoise"):
                    # 本轮补量的这几支全解不出来（解码器缺失），但历史读数还在：
                    # 表照用缓存，同时把「本轮补量失败」写清楚，别让人以为全都量过了
                    status = {"ok": True, "reason": "",
                              "threshold_dbfs": (status.get("threshold_dbfs")
                                                 or next((v.get("threshold_dbfs")
                                                          for v in cache["denoise"].values()
                                                          if v.get("threshold_dbfs") is not None), None)),
                              "note": (f"本轮补量 {len(need)} 支失败（{rc}）：{tail[:120]}；"
                                       f"表里用的是之前的读数")}
                else:
                    status = {"ok": False,
                              "reason": f"`denoise.py --measure` 跑不起来（{rc}）：{tail[:200]}"}
    cache["denoise_status"] = status
    if not status:
        # 一次也没量成（也没有历史读数）：如实记下为什么，不许默认「没问题」
        status = {"ok": False, "reason": "没有可测的音轨文件（库里文件缺失或全部无音轨）"}
        cache["denoise_status"] = status
    # 有音轨、文件在库、却没有读数的那几支（解码器缺失之类）：单列，不混进「没问题」
    unmeasured = [a.get("id") for a in assets
                  if a.get("kind") in {"video", "audio"} and a.get("audio")
                  and (lib / str(a.get("id"))).exists()
                  and not (cache.get("denoise") or {}).get(_cache_key(a))]
    return {"status": status, "measured": len(cache.get("denoise") or {}),
            "unmeasured": unmeasured}


def compute_issues(lib: Path, assets: list[dict], cache: dict,
                   limits: dict | None = None) -> dict:
    limits = limits or {}
    videos = [a for a in assets if a.get("kind") == "video"]
    pictures = [a for a in assets if a.get("kind") in {"video", "photo"}]

    # ---- 竖/横混拍
    ori = Counter()
    landscape = []
    for a in pictures:
        o = str(a.get("orientation") or "unknown")
        ori[o] += 1
        if o == "landscape":
            sp = a.get("spec") or {}
            landscape.append({"asset": a.get("id"),
                              "display": f"{sp.get('display_width') or sp.get('width')}×"
                                         f"{sp.get('display_height') or sp.get('height')}"})

    # ---- 分辨率与 1080×1920 的差距
    below = []
    for a in pictures:
        sp = a.get("spec") or {}
        w = _num(sp.get("display_width")) or _num(sp.get("width"))
        h = _num(sp.get("display_height")) or _num(sp.get("height"))
        if not w or not h:
            continue
        short, long_ = min(w, h), max(w, h)
        factor = max(TARGET_W / short, TARGET_H / long_) if short and long_ else 0
        if factor > 1.001:
            below.append({"asset": a.get("id"), "width": int(w), "height": int(h),
                          "need_scale": round(factor, 2)})
    below.sort(key=lambda x: -x["need_scale"])

    # ---- 可变帧率（复用 normalize.judge）
    vfr_checked = 0
    vfr_hits, cfr, unknown = [], 0, []
    for a in videos:
        if limits.get("vfr") and vfr_checked >= int(limits["vfr"]):
            break
        r = vfr_of(a, lib, cache)
        vfr_checked += 1
        v = r.get("verdict")
        if v == "vfr":
            vfr_hits.append({"asset": a.get("id"), "reason": r.get("reason"),
                             "r_fps": r.get("r_fps"), "avg_fps": r.get("avg_fps")})
        elif v == "cfr":
            cfr += 1
        else:
            unknown.append({"asset": a.get("id"), "reason": r.get("reason")})

    # ---- 过暗 / 过亮 / 失焦（索引里的 L0 探针读数）
    dark, bright, blur_soft, blur_hard = [], [], [], []
    for a in videos:
        q = a.get("quality") or {}
        luma = _num((q.get("luma") or {}).get("mean"))
        blur = _num((q.get("blur") or {}).get("mean"))
        if luma is not None and luma < LUMA_DARK:
            dark.append({"asset": a.get("id"), "luma": round(luma, 1)})
        if luma is not None and luma > LUMA_BRIGHT:
            bright.append({"asset": a.get("id"), "luma": round(luma, 1)})
        if blur is not None and blur > BLUR_SOFT:
            item = {"asset": a.get("id"), "blur": round(blur, 2)}
            (blur_hard if blur > BLUR_HARD else blur_soft).append(item)
    dark.sort(key=lambda x: x["luma"])
    bright.sort(key=lambda x: -x["luma"])
    blur_hard.sort(key=lambda x: -x["blur"])
    blur_soft.sort(key=lambda x: -x["blur"])

    # ---- 颗粒/噪点（补量项）
    grain_checked = 0
    grain_hot = []
    grain_none = 0
    for a in videos:
        if limits.get("grain") and grain_checked >= int(limits["grain"]):
            break
        r = grain_of(a, lib, cache)
        grain_checked += 1
        val = _num(r.get("reading"))
        if val is None:
            grain_none += 1
            continue
        if val > GRAIN_HOT:
            grain_hot.append({"asset": a.get("id"), "grain": val})
    grain_hot.sort(key=lambda x: -x["grain"])

    # ---- 其它品牌字样（复用 brand_safety 的判据）
    # 判据就用 brand_safety.BRAND_PATTERN，不改词表。两件事只影响「怎么展示」：
    #   ① 「无品牌字样 / 没有 logo」这类**否定表述**是干净的，不能算命中；
    #   ② 这个模式为了保守把「手机 / 打码 / 门窗」也收进来了，所以命中的再分两桶：
    #      命中词是品牌/商标类的算「疑似真品牌」，其余算「宽判据顺带命中」，逐条列原文让人自己看。
    WIDE_TOKENS = {"手机", "打码", "电话", "联系方式", "水印", "门窗"}
    brand = []
    for a in assets:
        for s in segs_of(a):
            hits_tok: list[str] = []
            for f in s["flags"]:
                for m in brand_safety.BRAND_PATTERN.finditer(f):
                    before = f[max(0, m.start() - 3):m.start()]
                    if any(w in before for w in ("无", "没有", "未见", "不带", "不含", "不算")):
                        continue          # 「无品牌字样」是干净的
                    hits_tok.append(m.group(0))
            if not hits_tok:
                continue
            specific = any(t not in WIDE_TOKENS for t in hits_tok)
            brand.append({"asset": a.get("id"), "in": round(s["in"], 2),
                          "out": round(s["out"], 2), "flags": s["flags"],
                          "tokens": sorted(set(hits_tok)), "specific": specific})
            break
    brand.sort(key=lambda x: (not x["specific"], x["asset"]))

    # ---- 音轨缺失 / 音量过低
    no_audio = [{"asset": a.get("id")} for a in videos if not a.get("audio")]
    low_peak = []
    for a in videos:
        peak = _num((a.get("audio") or {}).get("peak_db"))
        if peak is not None and peak < LOW_PEAK_DB:
            low_peak.append({"asset": a.get("id"), "peak_db": round(peak, 1)})
    low_peak.sort(key=lambda x: x["peak_db"])

    # ---- 明显底噪（复用 denoise --measure）
    nf = measure_noise_floor(lib, assets, cache, limit=int(limits.get("audio") or 0))
    nf_items = []
    for a in assets:
        c = (cache.get("denoise") or {}).get(_cache_key(a))
        if not c:
            continue
        if c.get("need_denoise"):
            nf_items.append({"asset": a.get("id"),
                             "pause_residual_dbfs": c.get("pause_residual_dbfs"),
                             "snr_db": c.get("snr_db")})
    nf_items.sort(key=lambda x: -(x.get("pause_residual_dbfs") or -99))

    # ---- 时长过短
    short = [{"asset": a.get("id"), "duration": round(_num(a.get("duration")) or 0.0, 2)}
             for a in videos if 0 < (_num(a.get("duration")) or 0.0) < TOO_SHORT_S]

    # ---- 场记口令污染（复用 cue_guard.find_cues）
    cue_items = []
    cue_checked = cue_unjudgeable = 0
    for a in assets:
        if a.get("kind") not in {"video", "audio"}:
            continue
        words, truth = cue_guard.words_and_source(a, lib)
        if not words:
            cue_unjudgeable += 1
            continue
        cue_checked += 1
        cues = cue_guard.find_cues(words)
        if cues:
            cue_items.append({"asset": a.get("id"),
                              "cues": [{"kind": c.get("kind"), "t0": round(float(c.get("t0") or 0), 2),
                                        "text": c.get("text")} for c in cues[:5]]})

    r2 = lambda x: round(float(x), 2)  # noqa: E731
    return {
        "orientation": {"portrait": ori.get("portrait", 0), "landscape": ori.get("landscape", 0),
                        "square": ori.get("square", 0), "unknown": ori.get("unknown", 0),
                        "mixed": bool(ori.get("portrait") and ori.get("landscape")),
                        "items": landscape},
        "resolution": {"target": f"{TARGET_W}×{TARGET_H}", "below_target": len(below),
                       "checked": len(pictures), "items": below},
        "vfr": {"checked": vfr_checked, "total": len(videos), "vfr": len(vfr_hits),
                "cfr": cfr, "unknown": len(unknown),
                "limited": bool(limits.get("vfr") and vfr_checked < len(videos)),
                "items": vfr_hits, "unknown_items": unknown[:6]},
        "dark": {"count": len(dark), "threshold_luma": LUMA_DARK, "items": dark},
        "bright": {"count": len(bright), "threshold_luma": LUMA_BRIGHT, "items": bright},
        "blur": {"soft": len(blur_soft), "hard": len(blur_hard),
                 "threshold_soft": BLUR_SOFT, "threshold_hard": BLUR_HARD,
                 "items": (blur_hard + blur_soft)[:12]},
        "grain": {"checked": grain_checked, "total": len(videos), "hot": len(grain_hot),
                  "unreadable": grain_none, "threshold": GRAIN_HOT,
                  "limited": bool(limits.get("grain") and grain_checked < len(videos)),
                  "items": grain_hot},
        "brand": {"count": len(brand),
                  "specific": sum(1 for x in brand if x["specific"]),
                  "items": brand},
        "audio_missing": {"count": len(no_audio), "items": no_audio},
        "audio_low": {"count": len(low_peak), "threshold_peak_db": LOW_PEAK_DB,
                      "items": low_peak},
        # 有读数（本轮新量或缓存里的历史读数）就算量过；一条都没有就跳过并写清原因
        "noise_floor": {"status": "measured" if nf["measured"] else "skipped",
                        "reason": nf["status"].get("reason") or "",
                        "note": nf["status"].get("note") or "",
                        "threshold_dbfs": nf["status"].get("threshold_dbfs")
                        or next((v.get("threshold_dbfs") for v in (cache.get("denoise") or {}).values()
                                 if v.get("threshold_dbfs") is not None), None),
                        "measured": nf["measured"], "count": len(nf_items),
                        "unmeasured": nf["unmeasured"], "items": nf_items},
        "too_short": {"count": len(short), "threshold_s": TOO_SHORT_S, "items": short},
        "cue": {"count": len(cue_items), "checked": cue_checked,
                "unjudgeable": cue_unjudgeable, "items": cue_items},
        "seconds": {"photo_assumption_s": PHOTO_SECONDS},
    }


# ---------------------------------------------------------------- ⑤ 反推选题
#
# 选题方向**不是三个固定槽**。先量这个库到底撑得住哪些方向（口播里反复在说什么样的判断、
# 有没有工序特写、有没有成品静帧、有没有前后对比），再按支撑度往下取；撑不住的方向不占坑。
# 标题只用库里真有的东西拼（L2 场景标签 / 主题词）——标题里出现的每个名词都能在索引里
# 找到出处，所以两个不同生意的库不可能只换主体名就得出同一组题目。
#
# 两条不凑数的硬规矩（三套选题≠凑满三套）：
#   ① 一个锚点（画面标签/主题词）只能当一次招牌，用过的在后面降权、用完了就少出一条；
#   ② 标题句式按「方向 + 这段素材的具体内容」从原文招式里挑，同一库三条不重样。

# 六大话题选题方向的语言特征（方向名照抄 references/topic-system.md §3，判据是本仓自建）
DIRECTION_CUES = {
    "认知话题": re.compile(r"其实|以为|相反|反而|恰恰|并不是|不是.{0,8}而是|都以为|都觉得|都说"),
    "内幕话题": re.compile(r"内行|秘密|不会告诉|门道|偷工|工艺|工序|做法|底料|配方|手艺|猫腻|套路|步骤"),
    "身份话题": re.compile(r"预算|报价|多少钱|价钱|价格|性价比|值不值|成本|几百|几千|几万|一平"),
    "情绪话题": re.compile(r"后悔|心疼|窝火|受气|憋屈|白花|白做|返工|踩坑|被坑|糟心"),
    "争议话题": re.compile(r"有人说|对不对|该不该|谁对|对立|内行外行|扯皮|到底哪个"),
    "社会话题": re.compile(r"时代|城市|社会|热点|事件|政策|新闻|热搜|全网"),
}
# 每个方向在画面上靠什么撑（bucket, 关键词）：没有这类画面，就只能靠口播，支撑度自然低
DIRECTION_CLIPS = {
    "认知话题": ("broll", ("对比", "前后", "样板")),
    "内幕话题": ("detail", ("工序", "工艺", "打磨", "施工", "做工", "切", "处理")),
    "身份话题": ("still", ("展厅", "成品", "样板", "色卡", "实景", "全景")),
    "情绪话题": ("broll", ("遮挡", "逆光", "失焦", "失败")),
    "争议话题": ("broll", ("两种", "对比", "说法")),
    "社会话题": ("broll", ("街", "城市", "现场", "门面", "商业")),
}
# 方向与三条流（ideate.STREAMS）的亲和度：泛流量槽放认知/争议/社会，人设槽放内幕/情绪，
# 转化槽放身份。骨架本身仍由 ideate.STREAMS 出（原文中间结构），这里只决定谁坐哪个槽。
DIRECTION_SLOT = {"认知话题": 0, "争议话题": 0, "社会话题": 0,
                  "内幕话题": 1, "情绪话题": 1, "身份话题": 2}

# ---------------------------------------------------------------- 标题句式骨架
#
# 三套选题的标题不能是「同一句话换个主体名」。措辞骨架**只有这一份表**，每一条都点得出
# 原文里的名字：要么是 references/content-system.md『内容创作 · 水桶流视频 · 开篇钩子八大
# 切入点』的某一式，要么是『文案框架内容 · 开篇技巧』里的某一招——表里只写模板，不另立
# 一套修辞清单。模板里的变量全是这个库里真有的字符串：
#   {a}  锚点（索引里那一段画面的人工标签，或退到的口播主题词）
#   {k1} 主语主题词、{k2} 第二个主题词（都在转写原文里有出处）
#   {n}  锚点那一段的实测秒数（索引里的 out-in，不是估的）
_TITLE_CT = ("内容创作", "水桶流视频")          # 开篇钩子八大切入点所在章节
_TITLE_KT = ("文案框架内容", "开篇技巧")         # 开篇技巧所在章节
_TITLE_POINTS = "开篇钩子八大切入点"

TITLE_FRAMES = {
    "reversal": {
        "sec": _TITLE_CT, "group": _TITLE_POINTS, "key": "颠覆切入",
        "cue": r"其实|相反|反而|恰恰|并不是|都以为|都觉得|好多人都",
        "tpl": "「{a}」这一镜，把{k1}里最老的常识倒了过来"},
    "open_doubt": {
        "sec": _TITLE_CT, "group": _TITLE_POINTS, "key": "疑问切入",
        "cue": r"为什么|点解|究竟|到底|怎么",
        "tpl": "为什么{k1}非要做到「{a}」这一步"},
    "curiosity": {
        "sec": _TITLE_CT, "group": _TITLE_POINTS, "key": "好奇切入",
        "cue": "",
        "tpl": "「{a}」这一下，外人看不出{k1}里的门道"},
    "conflict": {
        "sec": _TITLE_KT, "key": "冲突开篇",
        "cue": r"明明|光是|终于|半年|三天|整了",
        "tpl": "明明卖的是{k1}，「{a}」这道活最费事"},
    "enemy": {
        "sec": _TITLE_KT, "key": "制造敌人",
        "cue": r"有人|别人|外面|同行|好多师傅|都说",
        "tpl": "外面都说{k1}靠的是材料，「{a}」这一段我不同意"},
    "insider_id": {
        "sec": _TITLE_CT, "group": _TITLE_POINTS, "key": "身份推荐切入",
        "cue": r"我做|我们做|多年|十年|师傅|从业|干了",
        "tpl": "做{k1}这些年，「{a}」这种活从来不写在明面上"},
    "info_gap": {
        "sec": _TITLE_KT, "key": "信息缺口",
        "cue": r"看完|后来才|才知道|原来|算下来|亏",
        "tpl": "看完「{a}」才算清：{k1}的钱花在哪一层"},
    "mouthpiece": {
        "sec": _TITLE_KT, "key": "做嘴替",
        "cue": r"客户|客人|问价|多少钱|好多人问|经常问",
        "tpl": "客户总问{k1}贵在哪，「{a}」就是答案"},
    "digit_line": {
        "sec": _TITLE_KT, "key": "数字开篇",
        "cue": r"[0-9０-９]|几千|几万|几百|三千|六百|两百",
        "tpl": "「{a}」这 {n} 秒，就定了{k1}的钱怎么花"},
    "pain_point": {
        "sec": _TITLE_CT, "group": _TITLE_POINTS, "key": "痛点切入",
        "cue": r"后悔|心疼|憋屈|白做|白花|返工|踩坑|窝火",
        "tpl": "做{k1}最憋屈的一刻，就在「{a}」里"},
    "emotion_wave": {
        "sec": _TITLE_CT, "group": _TITLE_POINTS, "key": "情绪切入",
        "cue": r"难受|气人|舍不得|不容易|委屈",
        "tpl": "「{a}」看完心里堵：{k1}这行真不容易"},
    "both_sides": {
        "sec": _TITLE_KT, "key": "对立面开篇",
        "cue": r"有的|一种|两边|都对|说法",
        "tpl": "「{a}」里两种做法都在，{k1}到底该听谁的"},
    "counter_ask": {
        "sec": _TITLE_KT, "key": "对赌开篇",
        "cue": r"你信不信|敢不敢|赌",
        "tpl": "你信不信把「{a}」放一遍，{k1}的争论就有答案"},
    "scene_density": {
        "sec": _TITLE_KT, "key": "信息密度开篇",
        "cue": r"今年|现在|时代|城市|街|现场",
        "tpl": "{k1}今年被问得最多的一件事，就拍在「{a}」里"},
}
# 一个方向备哪几条骨架：家族互不重叠，所以同一个库的三条选题不可能拿到同一个骨架。
FRAME_FAMILY = {
    "认知话题": ["reversal", "open_doubt", "curiosity"],
    "内幕话题": ["conflict", "enemy", "insider_id"],
    "身份话题": ["info_gap", "mouthpiece", "digit_line"],
    "情绪话题": ["pain_point", "emotion_wave"],
    "争议话题": ["both_sides", "counter_ask"],
    "社会话题": ["scene_density"],
}


def _phrase_of(assets: list[dict], pat: re.Pattern) -> dict[str, str]:
    """哪些素材的口播出现过某类说法 → {asset: 一句原话}。这是「方向有没有口播支撑」的唯一判据。"""
    hits: dict[str, str] = {}
    for a in assets:
        for p in ((a.get("transcript") or {}).get("phrases") or []):
            t = str(p.get("text") or "")
            if pat.search(t):
                hits[a.get("id")] = t
                break
    return hits


def _cue_text(assets: list[dict], pat: re.Pattern, limit: int = 12) -> str:
    """这一类说法在这个库里到底是怎么讲的（最多 12 支，每支一句）。

    标题句式要按「素材里实际说了什么」挑（原文『内容创作』一节 · 开篇钩子八大切入点各有一式），
    所以得看见比 `quotes`（只留 3 句给人读）更多一点的原话。这只是把已有的转写拼起来，不编。
    """
    out: list[str] = []
    for a in assets:
        for p in ((a.get("transcript") or {}).get("phrases") or []):
            t = str(p.get("text") or "")
            if pat.search(t):
                out.append(t)
                break
        if len(out) >= limit:
            break
    return " ".join(out)


def direction_support(assets: list[dict], pool: dict, keywords: list[str]) -> list[dict]:
    """量这个库撑得住哪些话题方向，按支撑度排序返回六条（分低的也留着，只是不占坑）。

    支撑度 = 口播里出现这类说法的素材数 × 3 + 画面侧证据数（工序特写/成品静帧/对比镜头，封顶 6）。
    两个真库跑下来分得很开：柒造 认知 39 / 内幕 24 / 身份 24；欧阳 认知 15 / 内幕 10 / 身份 6。
    """
    out = []
    for kind, pat in DIRECTION_CUES.items():
        quotes = _phrase_of(assets, pat)
        bucket, words = DIRECTION_CLIPS[kind]
        clips = []
        for c in (pool.get(bucket) or []):
            blob = " ".join(str(c.get(k) or "") for k in ("tag", "summary", "why", "say"))
            if any(w in blob for w in words) or (kind == "内幕话题"
                                                and str(c.get("shot") or "") in {"detail", "close"}):
                clips.append(c)
        score = 3.0 * len(quotes) + 1.0 * min(len(clips), 6)
        out.append({"kind": kind, "score": round(score, 1),
                    "quote_assets": sorted(quotes)[:6],
                    "quotes": [quotes[k] for k in sorted(quotes)[:3]],
                    "cue_text": _cue_text(assets, pat),
                    "clip_count": len(clips),
                    "clips": [{"asset": c["asset"], "in": c.get("in"), "out": c.get("out"),
                               "tag": c.get("tag"), "summary": c.get("summary"),
                               "kind_of_shot": c.get("kind_of_shot")} for c in clips[:4]],
                    "kw_hits": [k for k in keywords[:6]
                                if any(k in q for q in quotes.values())]})
    out.sort(key=lambda d: (-d["score"], list(DIRECTION_CUES).index(d["kind"])))
    return out


def _anchor_buckets(kind: str) -> list[str]:
    """锚点先翻这个方向「靠什么画面撑」的那一筐（DIRECTION_CLIPS），再翻其余的。

    身份话题靠成品静帧、内幕话题靠工序特写——按方向自家声明的画面类型取锚点，
    比固定顺序取更能找到那段真能当招牌的画面。
    """
    if kind == "认知话题":
        return ["talking", "broll", "detail", "still"]      # 认知话题得从「讲」的那一镜起
    head = (DIRECTION_CLIPS.get(kind) or ("broll", ()))[0]
    return [head] + [b for b in ("detail", "still", "talking", "broll") if b != head]


def _anchor_for(kind: str, direc: dict, pool: dict, k2: str,
                used: set[str] | None = None,
                words: list[str] | None = None) -> dict | None:
    """这道题的「锚点」：优先用一段真画面的人写场景标签（L2），没有就用口播主题词。

    标题里出现的锚点必须是索引里真有的字符串，所以不同库写出来必然不一样。
    `used` 是同一份交付里已经被别的选题当招牌用过的标签/主题词：**一个画面只能当一次招牌**，
    用过的跳过；三代候选（方向对口标签 → 同类标签 → 口播主题词）都分不到就返回 None，
    调用方据此少出一套，而不是拿同一个画面凑数。
    """
    used = used or set()
    quotes = set(direc.get("quote_assets") or [])
    _, hits = DIRECTION_CLIPS.get(kind, ("broll", ()))
    for b in _anchor_buckets(kind):
        scored = []
        for i, c in enumerate(pool.get(b) or []):
            tag = str(c.get("tag") or "")
            if not tag or tag in used:
                continue
            if kind == "认知话题" and quotes and str(c.get("asset")) not in quotes:
                continue
            blob = " ".join(str(c.get(k) or "") for k in ("tag", "summary"))
            # 先上「标签本身就对得上这个方向」的画面（成品静帧之于身份、工序特写之于内幕），
            # 其次才是内容描述里提到过（标签是招牌，招牌上得写对这个方向）
            rank = 0 if any(w in tag for w in hits) else (
                1 if any(w in blob for w in hits) else 2)
            scored.append((rank, i, c))
        scored.sort(key=lambda t: (t[0], t[1]))
        if scored:
            c = scored[0][2]
            return {"kind": "tag", "text": str(c["tag"]), "asset": c.get("asset"),
                    "in": c.get("in"), "out": c.get("out"),
                    "kind_of_shot": c.get("kind_of_shot")}
    # 没有可用的 L2 标签：退到口播主题词（这里不塞 ASR 原话——口播错字会被当成标题的错字）。
    # 主题词退到第二、第三个（k2 之外的候选由调用方给），一样不许和前面两套撞。
    evidence = (direc.get("quote_assets") or [None])[0]
    for w in [k2] + [x for x in (words or []) if x != k2]:
        if w and w not in used:
            return {"kind": "topic", "text": w, "asset": evidence, "in": None, "out": None,
                    "kind_of_shot": None}
    return None


def _title_number(anchor: dict) -> int | None:
    """标题里那个数字：锚点那一段的实测秒数（索引里的 out-in）。

    量不出真秒数就返回 None（静帧、没有时间码）——「数字开篇」那条骨架就不用，
    绝拿「画面证据段数」冒充秒数，也不拿编的数字开场。
    """
    a_in, a_out = _num(anchor.get("in")), _num(anchor.get("out"))
    if a_in is None or a_out is None or a_out - a_in < 2.0:
        return None
    return int(round(a_out - a_in))


def _frame_fits(fid: str, direc: dict, anchor: dict, num: int | None) -> bool:
    """这条骨架用不用得上，看素材的实际形态：几句口播、几段画面、有没有确数。

    判据挂在骨架上，是因为公式本身就有前提——没有画面对得上的库，「这一镜把常识倒过来」
    就是句空话；反过来，只有画面没有口播的库也只能从画面本身起句。
    """
    n_q = len(direc.get("quote_assets") or [])
    n_c = int(direc.get("clip_count") or 0)
    if fid == "reversal":
        return n_q >= 2 and n_c >= 3
    if fid == "open_doubt":
        return n_q >= 1 and n_c == 0
    if fid == "curiosity":
        return n_q == 0 and n_c >= 1
    if fid == "conflict":
        return n_c >= 6
    if fid in ("enemy", "insider_id", "mouthpiece", "pain_point", "counter_ask"):
        return n_q >= 1
    if fid == "info_gap":
        return n_c >= 6 and n_q >= 1
    if fid == "digit_line":
        return num is not None and n_c >= 1
    if fid == "both_sides":
        return n_c >= 1
    return True


def _cue_hits(fid: str, text: str) -> int:
    pat = TITLE_FRAMES[fid].get("cue") or ""
    return len(re.findall(pat, text)) if pat else 0


def _frame_candidates(kind: str, direc: dict, anchor: dict,
                      num: int | None) -> list[str]:
    """这个方向备着哪几条原文句式，按「素材撑不撑得住」+「素材里真在说什么」排序。

    先看形态（`_frame_fits`），再看口播里有没有那套话（骨架自带的 cue）：同一个方向在两个
    不同生意的库里，口播讲的不是一回事（一个说「其实都以为」，一个说「客户总问多少钱」），
    挑出来的句式就跟着变——不是给同一句话换主体名。
    """
    text = " ".join([str(direc.get("cue_text") or "")]
                    + [str(q) for q in (direc.get("quotes") or [])]
                    + [str(anchor.get("text") or "")])
    fam = FRAME_FAMILY.get(kind) or []
    return sorted(fam, key=lambda fid: (0 if _frame_fits(fid, direc, anchor, num) else 1,
                                        -_cue_hits(fid, text), fam.index(fid)))


def _render_title(fid: str, anchor: dict, k1: str, k2: str, num: int | None) -> str:
    """把骨架渲染成标题：变量全部来自索引（锚点标签 / 口播主题词 / 实测秒数）。"""
    tpl = TITLE_FRAMES[fid]["tpl"]
    return tpl.format(a=anchor["text"], k1=k1, k2=k2,
                      n=num if num is not None else 1)


def _frame_when(fid: str, direc: dict, anchor: dict) -> str:
    """为什么这道题的标题用这条句式（人读的一句依据，跟 `frame_source` 一起写进报告）。"""
    f = TITLE_FRAMES[fid]
    n_q = len(direc.get("quote_assets") or [])
    n_c = int(direc.get("clip_count") or 0)
    a_kind = "画面标签「%s」" % anchor.get("text") if anchor.get("kind") == "tag" \
        else "口播主题词「%s」" % anchor.get("text")
    if fid == "curiosity":
        return f"锚点是{a_kind}，库里对这个方向只有画面没有口播判断，所以用画面本身起句（好奇切入）。"
    if fid == "open_doubt":
        return (f"锚点是{a_kind}：口播里有 {n_q} 支在讲这类判断，画面侧一段都对不上，"
                f"所以用问句起，不硬写「这一镜把常识倒过来」（疑问切入）。")
    if fid == "reversal":
        return (f"锚点是{a_kind}：口播 {n_q} 支 + 画面 {n_c} 段都撑得住「先立常识再推翻」这个说法（颠覆切入）。")
    if fid == "conflict":
        return f"锚点是{a_kind}：库里工序/成品镜头有 {n_c} 段，可以正面撞「明明卖的是…却…」（冲突开篇）。"
    if fid == "info_gap":
        return f"锚点是{a_kind}：口播在算钱、画面 {n_c} 段又真能拍出花在哪儿，用「看完才明白」（信息缺口）。"
    if fid == "mouthpiece":
        return (f"锚点是{a_kind}：口播 {n_q} 支里客户在问价，画面不够拍成「一层层拆」，"
                f"就用替客户把话问出来（做嘴替）。")
    if fid in ("enemy", "insider_id"):
        t = "库里口播在讲「别人怎么说、我们怎么做」，先立一个对立说法再反驳（制造敌人）" \
            if fid == "enemy" else "口播是内行站在自己经验上说的，用身份起句（身份推荐切入）"
        return f"锚点是{a_kind}：{t}。"
    if fid == "digit_line":
        return f"锚点是{a_kind}：量得出确数，口播又没提供别的句式，用数字起句（数字开篇）。"
    return f"锚点是{a_kind}：按这个方向的支撑形态选了「{f['key']}」这一式。"


def _find_clip(pool: dict, asset: str | None, at: float | None) -> dict | None:
    """在分好筐的片段池里找回那道题的关键镜头（按素材 + 进场时间）。"""
    if not asset:
        return None
    for b in ("talking", "detail", "broll", "still"):
        for c in (pool.get(b) or []):
            if str(c.get("asset")) != str(asset):
                continue
            if at is None or abs(float(c.get("in") or 0.0) - float(at)) < 0.05:
                return c
    return None


def _title_note(anchor: dict, k1: str, k2: str) -> str:
    """标题里那个名词是从哪来的：让人能一路点回去看那一段。"""
    if anchor["kind"] == "tag":
        return (f"「{anchor['text']}」是索引里这一段画面的人工标签（{anchor['asset']} "
                f"{float(anchor.get('in') or 0):.1f}–{float(anchor.get('out') or 0):.1f}s），"
                f"不是编的修辞。")
    ev = f"（证据段：{anchor['asset']}）" if anchor.get("asset") else ""
    return (f"这个库的 L2 内容标签还没铺到这一段，标题退到口播主题词「{k2}」{ev}；"
            f"主题词在转写原文里有出处（{k1} 以外的第二个反复出现的词）。")


def _essential_why(kind: str, anchor: dict, title: str, direc: dict) -> str:
    """为什么这一段是「换了就撑不起这个题目」的关键素材。"""
    if anchor["kind"] == "tag":
        base = (f"「{anchor['text']}」是这道题唯一能落地的画面：库里对得上「{kind}」的"
                f"镜头一共 {direc.get('clip_count', 0)} 段，换掉它，题目就只剩一句空话。")
    elif anchor.get("asset"):
        base = (f"这道题的口播证据就落在这条素材上（{anchor['asset']}）：这个库还没有 L2 画面"
                f"标签，换掉它，「{title}」没有别的地方可以说。")
    else:
        base = (f"这道题的台词只能从这段口播里出（这个库还没有 L2 画面标签），"
                f"换掉它，「{title}」没有别的地方可以说。")
    qs = direc.get("quotes") or []
    if qs:
        base += f" 口播原话举例：「{str(qs[0])[:26]}」"
    return base


def _support_sentence(kind: str, direc: dict, anchor: dict, k2: str) -> str:
    """「为什么给这个库出这道题」——支撑度是量出来的，不是感觉。"""
    n_q = len(direc.get("quote_assets") or [])
    n_c = int(direc.get("clip_count") or 0)
    bits = [f"这个库撑得住「{kind}」的支撑度 {direc.get('score')}"]
    bits.append(f"口播里有 {n_q} 支在讲这类判断" if n_q else "口播里几乎没有这类判断")
    if n_c:
        bits.append(f"画面侧有 {n_c} 段能对上的镜头")
    else:
        bits.append("画面侧没有直接对得上的镜头，只能靠口播撑")
    qs = direc.get("quotes") or []
    if qs:
        bits.append(f"例：「{str(qs[0])[:24]}」")
    if anchor["kind"] == "tag":
        bits.append(f"标题里的「{anchor['text']}」就是支撑它的那段画面")
    else:
        bits.append(f"标题退到口播主题「{k2}」（这个库还没有 L2 画面标签）")
    return "；".join(bits) + "。"


def _topic_refs(kind: str, slot: int) -> dict:
    """给一套提案取原文出处：话题方向 + 流公式（都在『内容创作』一节里）。"""
    import ideate
    topic = ideate._src(("内容创作", "漏斗流视频"), kind, group="六大话题选题方向")
    if slot == 0:
        formula = ideate._src(("内容创作", "漏斗流视频"),
                              "一个自带流量的话题+和话题强相关的内容+自然的植入产品",
                              group="漏斗流视频公式")
    else:
        formula = ideate._src(("内容创作", "水桶流视频"),
                              "精准人群钩子/开篇钩子+细节展示+人格化表达+价值说明+转化指令",
                              group="水桶流视频公式")
    return {"topic": topic, "formula": formula}


def _clip_why(c: dict) -> str:
    """一段素材的「为什么是它」：先用索引里的内容描述，没有就引它这段的原话，
    再没有就如实说「还没做内容标注」——不拿 L0 的评级理由冒充内容。"""
    head = str(c.get("tag") or "").strip()
    body = str(c.get("summary") or c.get("action") or "").strip()
    if body:
        return f"{head}：{body[:46]}" if head else body[:46]
    say = str(c.get("say") or "").strip()
    if say:
        return f"这段他说的是「{say}」"
    if head:
        return head
    why = str(c.get("why") or "").strip()
    if why.startswith("有可用语音") or why.startswith("L0 清晰可用") or why.startswith("过短"):
        return "索引里还没做内容标注（这一档只有 L0 的清晰度评级）"
    return why[:46] or str(c.get("asset") or "")


def _candidates(assets: list[dict], cue_ids: set[str] | None = None,
                brand_ids: set[str] | None = None) -> dict:
    """按用途把可进片的段分好筐：口播 / 画面 / 细节 / 静帧。

    `cue_ids` 是带场记口令（三二一/再来一次）的素材，`brand_ids` 是带第三方品牌字样的：
    都不排除，但排在后面——拿一句「系三二一组我系牛哥」当开场台词，或者拿一个印着同行
    招牌的镜头当钩子，比技术参数难看多了。
    """
    cue_ids = cue_ids or set()
    brand_ids = brand_ids or set()
    talk, broll, detail, still = [], [], [], []
    for a in assets:
        is_photo = a.get("kind") == "photo"
        us = usable_spans(a)
        for s in segs_of(a):
            if s["grade"] not in USABLE_GRADES:
                continue
            a0, b0 = span_of(s)
            sec = seg_seconds(a, s)
            if is_photo:
                if sec <= 0:
                    still.append({"asset": a.get("id"), "kind": "photo", "in": 0.0, "out": 0.0,
                                  "seconds": PHOTO_SECONDS, "grade": s["grade"],
                                  "shot": s["shot"], "tag": s["tag"], "summary": s["summary"],
                                  "why": s["why"], "kind_of_shot": "still"})
                continue
            has_speech = False
            say = None
            for p in ((a.get("transcript") or {}).get("phrases") or []):
                ps, pe = _num(p.get("start")) or 0.0, _num(p.get("end")) or 0.0
                if _overlap(ps, pe, [(a0, b0)]) >= 1.2:
                    # 口播段自带一句原话：素材依据里直接写「他这段说的是…」，比技术字段好看
                    if say is None:
                        say = str(p.get("text") or "").strip()[:40] or None
                    has_speech = True
            rec = {"asset": a.get("id"), "kind": a.get("kind"), "in": round(a0, 2),
                   "out": round(b0, 2), "seconds": round(sec, 1), "grade": s["grade"],
                   "shot": s["shot"], "tag": s["tag"], "summary": s["summary"],
                   "why": s["why"], "say": say,
                   "cue": a.get("id") in cue_ids,
                   "brand": a.get("id") in brand_ids,
                   "kind_of_shot": "talking" if has_speech else "broll"}
            if has_speech:
                talk.append(rec)
            else:
                broll.append(rec)
                if str(s.get("shot") or "") in {"detail", "close"}:
                    detail.append(rec)
    for lst in (talk, broll, detail):
        lst.sort(key=lambda c: (GRADE_ORDER.get(c["grade"], 9), -c["seconds"]))
    still.sort(key=lambda c: GRADE_ORDER.get(c["grade"], 9))
    return {"talking": talk, "broll": broll, "detail": detail or broll, "still": still}


def _take(bucket: list[dict], used: set[str], kw: list[str], limit: int = 1,
          rotate: int = 0, avoid: set[str] | None = None, want: float = 0.0) -> list[dict]:
    """从筐里挑段。优先序：**够不够长**（能不能填满这一拍的时长）→ 关键词对不对得上 →
    这条素材本轮是否已用过 → 轮换位置。绝不重复挑同一段。

    「够长」排在「关键词」前面，是因为一个 3.7 秒的镜头填不满 9 秒的举证拍，
    选了也是假的素材依据。`rotate` 让三套选题不从同一段开拍；`avoid` 让整份报告
    尽量不复用同一条素材（三张提案长得一模一样，用户看不出区别）。
    """
    if not bucket:
        return []
    n = len(bucket)
    scored = []
    for idx, c in enumerate(bucket):
        # 关键词对得上：看内容描述，也看这一段他到底说了什么（没做 L2 的库只靠原话）
        blob = " ".join(str(c.get(k) or "") for k in ("tag", "summary", "why", "say", "asset"))
        fit = 0 if float(c.get("seconds") or 0) + 0.01 >= want else 1
        miss = 0 if any(k in blob for k in kw) else 1
        cue = 1 if c.get("cue") else 0
        brand = 1 if c.get("brand") else 0
        seen = 0 if (avoid is None or str(c.get("asset")) not in avoid) else 1
        # 顺序：本份报告还没用过这条素材 → 够不够长 → 关键词对不对得上 → 没口令/没品牌 → 轮换位置
        scored.append((seen, fit, miss, cue, brand, (idx - rotate) % max(1, n), idx, c))
    scored.sort(key=lambda t: t[:7])
    out: list[dict] = []
    for *_rest, c in scored:
        key = f"{c['asset']}|{c['in']}"
        if key in used:
            continue
        used.add(key)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _clip_basis(c: dict, role: str, want_s: float) -> dict:
    """一拍要多久，就从这段里切多久（不拿 57 秒的长段去当 3 秒钩子）。"""
    if c.get("kind") == "photo" or float(c.get("seconds") or 0) <= 0:
        return {"role": role, "asset": c["asset"], "in": 0.0, "out": 0.0,
                "seconds": PHOTO_SECONDS, "pool_seconds": PHOTO_SECONDS,
                "grade": c.get("grade"), "tag": c.get("tag"),
                "why": _clip_why(c), "say": c.get("say"), "shot": c.get("shot")}
    span = float(c.get("seconds") or 0)
    sec = round(min(max(2.0, want_s), span), 2)
    cin = float(c.get("in") or 0.0)
    return {"role": role, "asset": c["asset"], "in": round(cin, 2),
            "out": round(cin + sec, 2), "seconds": sec, "pool_seconds": round(span, 1),
            "grade": c.get("grade"), "tag": c.get("tag"),
            "why": _clip_why(c), "say": c.get("say"), "shot": c.get("shot")}


_GAP_HOW = {
    "talking": "补拍口播：竖屏、面对镜头、单段 8–12 秒（这一拍要露脸说话，库里没有可用口播）",
    "broll": "补拍空镜：交代场面的环境/过程镜头（不拍人脸也行）",
    "detail": "补拍特写：手部/材料/成品细节，俯拍 45°，5 秒起",
    "still": "补拍或截一帧成品图当静帧画面",
}


def _plan_beats(prop: dict, buckets: dict, keywords: list[str],
                rotate: int = 0, avoid: set[str] | None = None,
                must_use: dict | None = None,
                must_why: str = "") -> tuple[list[dict], list[dict], list[dict]]:
    """按这条选题的每一拍（ideate 的 beats）向库里取片。

    `must_use` 是这道选题的**关键素材**（换了它这个题目就撑不住）：只要某一拍的视觉方式
    容得下它的类型，就优先把这一拍给它，并在素材依据里标成 essential——不给「关键素材」
    留位置，报告就分不清「这段可以换」和「这段换了题目就废」。

    取片的优先顺序跟着 beat 自己声明的视觉方式走（talking / broll_or_gen / gen / …），
    不去猜。代码生成的那一拍不算「缺画面」—— 它本来就该由卡片承担。
    真缺的只有「要露脸说话，但库里没有可用口播」这种，如实写进 missing_shots。
    """
    used: set[str] = set()
    basis: list[dict] = []
    gaps: list[dict] = []
    generated: list[dict] = []
    must = dict(must_use) if must_use else None
    prefs_of = {"talking": ["talking"],
                "broll_or_gen": ["broll", "detail"],
                "talking_or_gen": ["talking", "broll", "still"],
                "gen": []}
    for i, beat in enumerate(prop.get("beats") or []):
        role = str(beat.get("phase") or f"第 {i + 1} 拍")
        want = float(beat.get("dur_s") or 3.0)
        mode = str(beat.get("visual_mode") or "talking_or_gen")
        prefs = prefs_of.get(mode, ["talking", "broll", "still"])
        if not prefs:
            generated.append({"role": role,
                              "how": "代码生成卡片（这一拍 ideate 自带 code_fallback，本来就免拍）",
                              "template": (beat.get("code_fallback") or {}).get("template")})
            continue
        picked, kind, essential = None, None, False
        if must and str(must.get("kind_of_shot")) in prefs:
            used.add(f"{must['asset']}|{must.get('in')}")
            picked, kind, essential = must, str(must.get("kind_of_shot")), True
            must = None
        else:
            for k in prefs:
                got = _take(buckets.get(k) or [], used, keywords, limit=1, rotate=rotate + i,
                            avoid=avoid, want=want)
                if got:
                    picked, kind = got[0], k
                    break
        if picked is None:
            gaps.append({"role": role, "need": "/".join(prefs), "how": _GAP_HOW[prefs[0]]})
            continue
        if avoid is not None:
            avoid.add(str(picked.get("asset")))
        entry = _clip_basis(picked, role, want)
        if essential:
            entry["essential"] = True
            entry["essential_why"] = must_why
        basis.append(entry)
    if must:
        # 关键素材没有能装下它的那一拍：照样列进素材依据（换掉它题目就不成立），单列一行
        entry = _clip_basis(must, "关键举证（必须用上）", 6.0)
        entry["essential"] = True
        entry["essential_why"] = must_why
        basis.append(entry)
        if avoid is not None:
            avoid.add(str(must.get("asset")))
    return basis, gaps, generated


def proposals_from_inventory(rep: dict, *, duration_s: int = 35, persona: str = "",
                             audience: str = "") -> dict:
    """盘点结果 → 3 套选题。骨架与钩子库全部来自 ideate（不另写一套平行实现）。"""
    import ideate
    totals = rep.get("totals") or {}
    usable = rep.get("usable_clips") or {}
    speech = rep.get("speech") or {}
    keywords = [t["keyword"] for t in (rep.get("keywords") or [])]
    usable_s = float(usable.get("with_backup_s") or 0.0)
    direct_s = float(usable.get("direct_use_s") or 0.0)
    speech_s = float(speech.get("speech_usable_s") or 0.0)

    missing_gap = []
    if direct_s <= 0 and usable_s <= 0:
        issues = rep.get("issues") or {}
        if not totals.get("assets"):
            missing_gap.append("库里没有任何素材文件（索引是空的）")
        if (issues.get("dark") or {}).get("count"):
            missing_gap.append(f"{(issues['dark'])['count']} 支过暗（luma 低于 "
                               f"{issues['dark'].get('threshold_luma')}），拍的时候补光")
        if (issues.get("blur") or {}).get("hard"):
            missing_gap.append(f"{(issues['blur'])['hard']} 支对焦/运动模糊过重，撑不起主镜头")
        if (issues.get("audio_missing") or {}).get("count"):
            missing_gap.append(f"{(issues['audio_missing'])['count']} 支没有音轨，口播段用不了")
        if speech_s <= 0:
            missing_gap.append("可用口播 0 秒：需要至少补录一段 20–30 秒的口播（竖屏、面对镜头）")
        if not missing_gap:
            missing_gap.append("可用画面 0 秒：L2 全部判成 do-not-use，得重拍主镜头")
        return {"ok": False,
                "verdict": "这个库拍不出片。",
                "missing": missing_gap,
                "library": rep.get("library"),
                "usable_s": usable_s, "speech_usable_s": speech_s,
                "proposals": []}

    kw_core, core_phrase = _core_keywords(keywords)
    k1 = kw_core[0]
    k2 = kw_core[1] if len(kw_core) > 1 else kw_core[0]
    who = persona.strip() or "这家店的老板"
    aud = audience.strip() or f"正在了解「{core_phrase}」的人"
    buckets = rep.get("clip_pool") or _candidates(rep.get("assets_index") or [])

    # 骨架、钩子、脚本全部走 ideate.build_proposals（一次出三套），这里只决定「这道题问什么」。
    # 传进去的 topic 只放主题词，是为了让钩子模板里的 {core} 落在这个词上
    # （传整句选题会被 _extract_core 截断成「最容易被外人搞反的一件事」，钩子就念歪了）。
    raw = ideate.build_proposals(core_phrase, who, aud, duration_s)
    dires = rep.get("directions") or direction_support(rep.get("assets_index") or [],
                                                       buckets, keywords)
    out = []
    taken: set[str] = set()          # 三套选题之间不抢同一条素材（抢了就看不出区别）
    used_anchors: set[str] = set()   # 一个画面/主题词只能当一次招牌，不许三套用同一个锚点
    used_frames: set[str] = set()    # 同一个库里三条标题的句式骨架互不相同
    dropped: list[dict] = []         # 没出的那条：为什么没出（不许默默凑数）
    picked: list[dict] = []          # 真占坑的方向（给 verdict 用，不列没出题的）
    free_slots = [0, 1, 2]
    # 方向按支撑度走，但能不能占坑要看两件事：撑不撑得住（score ≥ 1），
    # 以及分不分得到一块**没被别人用过**的招牌（锚点）。分不到就不出，宁可少一条。
    topic = core_phrase
    for direc in dires:
        if not free_slots or len(out) >= 3:
            break
        kind = direc["kind"]
        if float(direc.get("score") or 0) < 1:
            dropped.append({"kind": kind, "why": f"支撑度 {direc.get('score')}（口播画面都没撑住）"})
            continue
        anchor = _anchor_for(kind, direc, buckets, k2, used=used_anchors,
                             words=[w for w in keywords if w != k1])
        if anchor is None:
            dropped.append({"kind": kind,
                            "why": "能当招牌的画面标签/主题词都被前面的选题用完了"})
            continue
        used_anchors.add(anchor["text"])
        want = DIRECTION_SLOT.get(kind, 0)
        slot = want if want in free_slots else free_slots[0]
        free_slots.remove(slot)
        rot = slot
        st = ideate.STREAMS[slot]
        refs = _topic_refs(kind, slot)
        num = _title_number(anchor)
        frame = next((f for f in _frame_candidates(kind, direc, anchor, num)
                      if f not in used_frames),
                     _frame_candidates(kind, direc, anchor, num)[0])
        used_frames.add(frame)
        topic = _render_title(frame, anchor, k1, k2, num)
        must = _find_clip(buckets, anchor.get("asset"), anchor.get("in"))
        must_why = _essential_why(kind, anchor, topic, direc)
        prop = next(p for p in raw["proposals"] if p["id"] == st["id"])

        basis, gaps, generated = _plan_beats(prop, buckets, keywords, rotate=rot, avoid=taken,
                                             must_use=must, must_why=must_why)
        used_windows = round(sum(b["seconds"] for b in basis), 1)
        pool_s = round(sum(b["pool_seconds"] for b in basis), 1)
        # 代码生成的拍也占片长（卡片在成片里是真实秒数），上限要把它加上
        gen_roles = {str(g.get("role")) for g in generated}
        gen_s = round(sum(float(b.get("dur_s") or 0) for b in prop["beats"]
                          if str(b.get("phase")) in gen_roles), 1)
        beat_floor = int(round(sum(float(b.get("dur_s") or 0) for b in prop["beats"])))
        # 片长口径（实算，不拍脑袋）：
        #   下限 = 骨架每一拍的 dur_s 之和（讲完这件事最短要多少秒）
        #   上限 = 真人/画面窗口合计（受全库可用口播约束）+ 代码卡片时长
        dur_max = int(round((min(used_windows, speech_s) if speech_s > 0 else used_windows) + gen_s))
        enough = dur_max >= beat_floor
        prop["topic"] = topic
        prop["topic_kind"] = f"六大话题选题方向之 {kind}"
        prop["topic_ref"] = refs["topic"]          # 结构化出处：测试拿原文标题逐级对账
        prop["formula_ref"] = refs["formula"]
        prop["topic_source"] = ideate._ref_text(refs["topic"])
        prop["topic_why"] = _support_sentence(kind, direc, anchor, k2)
        prop["formula_source"] = ideate._ref_text(refs["formula"])
        prop["title_basis"] = {"anchor": anchor, "frame": frame,
                               "frame_point": TITLE_FRAMES[frame]["key"],
                               "frame_ref": dict(TITLE_FRAMES[frame]),
                               "frame_source": ideate._ref_text(TITLE_FRAMES[frame]),
                               "frame_when": _frame_when(frame, direc, anchor),
                               "k1": k1, "k2": k2, "n": num,
                               "note": _title_note(anchor, k1, k2)}
        prop["topic_direction"] = kind
        prop["support"] = {"score": direc["score"],
                           "quote_assets": len(direc.get("quote_assets") or []),
                           "clips": direc.get("clip_count", 0),
                           "evidence_quotes": (direc.get("quotes") or [])[:2]}
        prop["material_basis"] = basis
        prop["generated_roles"] = generated
        prop["missing_shots"] = gaps
        prop["duration_range"] = {
            "min_s": beat_floor,
            "max_s": dur_max,
            "clip_seconds": used_windows,
            "pool_seconds": pool_s,
            "speech_pool_s": round(speech_s, 1),
            "basis": (f"骨架 {len(prop['beats'])} 拍共 {beat_floor} 秒"
                      f"（{'/'.join(str(b.get('phase')) for b in prop['beats'])}）"
                      f"｜每拍从库里切出的窗口合计 {used_windows} 秒"
                      f"（这几段原素材就有 {pool_s} 秒）"
                      f"｜全库可用口播 {hms(speech_s)}"),
            "enough": enough,
        }
        prop["keyword_evidence"] = [t for t in (rep.get("keywords") or [])
                                    if t["keyword"] in topic][:2]
        out.append(prop)
        picked.append(direc)

    # 编号按实际出片套数重排（1..N）：少出一条时 `--pick 2` 仍然接得住
    for i, p in enumerate(out, 1):
        p["index"] = i
    top = "、".join(f"{d['kind']}（{d['score']}）" for d in picked)
    if len(out) >= 3:
        verdict = f"按支撑度排下来，这个库最该做这 {len(out)} 个方向：{top}"
    else:
        why = "；".join(f"{d['kind']}：{d['why']}" for d in dropped[:3])
        verdict = (f"按支撑度排下来，这个库最该做这 {len(out)} 个方向：{top}。"
                   f"再往下凑不出第三套——{why or '后面的方向支撑度不够'}。"
                   f"所以只出 {len(out)} 条，宁少勿凑：第三套要成立，得先补一段能当招牌的素材。")
    return {"ok": True,
            "verdict": verdict,
            "library": rep.get("library"),
            "keywords": keywords[:6],
            "persona": who, "audience": aud,
            "core": core_phrase,
            "topic_raw": topic,
            "proposals": out,
            "anchors": [p["title_basis"]["anchor"]["text"] for p in out],
            "skeletons": [p["title_basis"]["frame"] for p in out],
            "dropped": dropped,
            "missing": []}


def _core_keywords(keywords: list[str]) -> tuple[list[str], str]:
    """选题用哪两个主题词：优先具体的长词（≥ 3 字），不拿一个词去套另一个词。

    「颜色 / 客户」这种两字泛词当主语读起来像废话；「艺术涂料」这种三字以上的具体词
    才是库里真在讲的东西。两个词要互不包含，否则「做艺术涂料…死磕的涂料细节」会重复。
    """
    pool = keywords[:10] or ["这批素材"]
    specific = [k for k in pool if len(k) >= 3]
    k1 = (specific or pool)[0]
    k2 = next((k for k in pool if k not in k1 and k1 not in k), k1)
    return [k1, k2], k1


def build_ideation(rep: dict, *, duration_s: int = 35, persona: str = "",
                   audience: str = "") -> dict:
    """盘点 → ideation.json 的完整结构（字段与 ideate.build_proposals 完全一致，
    只是多了素材依据/片长区间；所以 `--pick` / `--to-plan` 照旧能用）。"""
    import ideate
    built = proposals_from_inventory(rep, duration_s=duration_s,
                                     persona=persona, audience=audience)
    if not built["ok"]:
        return {"_refused": True, **{k: built[k] for k in ("verdict", "missing", "library")}}
    questions = [
        f"「{', '.join(built['keywords'][:3])}」这几个词是你最想让人记住的吗？"
        "（换掉主题词，钩子会跟着变）",
    ]
    missing_all = [g["how"] for p in built["proposals"] for g in p["missing_shots"]]
    if missing_all:
        questions.append(f"还缺 {len(missing_all)} 个画面：" + "；".join(sorted(set(missing_all))[:3]))
    return {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "topic_raw": built["topic_raw"],
        "core": built.get("core") or (built["keywords"][0] if built["keywords"] else "这批素材"),
        "persona": built["persona"],
        "audience": built["audience"],
        "target_duration_s": duration_s,
        "follow_up_questions": questions,
        "selected_proposal": 1,
        "from_inventory": {"library": rep.get("library"), "built_at": rep.get("built_at"),
                           "usable_s": rep.get("totals", {}).get("usable_s"),
                           "speech_usable_s": rep.get("speech", {}).get("speech_usable_s"),
                           "keywords": built["keywords"]},
        "proposals": built["proposals"],
    }


def load_report(p: Path) -> dict:
    """`--from-inventory` 收三种入参：inventory.json 本体 / 素材库目录 / 项目目录。"""
    p = Path(p).expanduser()
    cands = ([p / SYS_DIR / REPORT_JSON, p / REPORT_JSON] if p.is_dir() else [p])
    for c in cands:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SystemExit(f"盘点报告读不出来（{e}）：{c}")
    raise SystemExit(f"找不到盘点报告：{p}\n先跑 python3 scripts/inventory.py --library \"{p}\"")


# ---------------------------------------------------------------- 报告（给人看）

def _top_line(rep: dict) -> str:
    t, u, s = rep["totals"], rep["usable_clips"], rep["speech"]
    iss = rep["issues"]
    bits = [f"盘了 {t['assets']} 个文件、{hms(t['duration_s'])}的料",
            f"能直接进片的画面 {hms(u.get('direct_use_s', 0))}"
            f"（放宽到备用镜头 {hms(u.get('with_backup_s', 0))}）",
            f"有口播的素材 {s['assets_with_speech']} 支、可用口播 {hms(s['speech_usable_s'])}"]
    head = "，".join(bits) + "。"
    probs: list[str] = []
    if iss["orientation"]["mixed"]:
        probs.append(f"竖横混拍（竖 {iss['orientation']['portrait']} / 横 "
                     f"{iss['orientation']['landscape']}）")
    if iss["vfr"]["vfr"]:
        probs.append(f"{iss['vfr']['vfr']} 支手机可变帧率（剪之前要规整）")
    if iss["audio_low"]["count"]:
        probs.append(f"{iss['audio_low']['count']} 支声音偏小")
    if iss["noise_floor"].get("count"):
        probs.append(f"{iss['noise_floor']['count']} 支进片前该先清底噪")
    if iss["brand"]["count"]:
        probs.append(f"{iss['brand']['count']} 段可能有别人的品牌/商标字样（需逐条复核）")
    tail = ("要注意的是：" + "、".join(probs) + "。") if probs else "画质上没有硬伤。"
    return head + tail


def _md_issues(rep: dict) -> list[str]:
    iss = rep["issues"]
    L: list[str] = []

    def block(title: str, head: str, items: list[str], note: str = "") -> None:
        L.append(f"### {title}")
        L.append(head)
        if items:
            L.append("")
            L.extend(f"- {x}" for x in items)
        if note:
            L.append("")
            L.append(note)
        if not items and not note:
            L.append("")
            L.append("这一项这批料没有问题。")
        L.append("")

    o = iss["orientation"]
    block("竖屏还是横屏", f"竖 {o['portrait']} · 横 {o['landscape']} · 方 {o['square']}"
          f" · 判不出 {o['unknown']}"
          + ("（**混拍**：横的这 %d 个进竖屏片要裁掉两边一大块，或者只当背景）" % o["landscape"]
             if o["mixed"] else ""),
          [f"横拍：{x['asset']}（{x['display']}）" for x in o["items"][:10]],
          (f"横拍还有 {max(0, len(o['items']) - 10)} 个没列，见 inventory.json。"
           if len(o["items"]) > 10 else ""))

    r = iss["resolution"]
    block("清晰度够不够 1080×1920", f"量了 {r['checked']} 张/支画面，其中 {r['below_target']} 个"
          f"比交付要求小（放大后会软）",
          [f"{x['asset']}：{x['width']}×{x['height']}，要放大 {x['need_scale']}×" for x in r["items"][:8]])

    v = iss["vfr"]
    if v["vfr"]:
        vnote = "变帧的片子直接按帧剪会音画不同步，先跑 scripts/normalize.py 规整再进片。"
    elif v["checked"] and v["unknown"] >= v["checked"]:
        vnote = "这一项**没量成**（全部判不了，见上面每条的原因）：不算「没问题」，只是没结论。"
    else:
        vnote = ""
    if v.get("limited"):
        vnote += f"（只测了前 {v['checked']} 支，因为带了 --vfr-limit；剩下的没量，报告不替它们下结论）"
    block("手机可变帧率（VFR）", f"判了 {v['checked']} 支（变帧 {v['vfr']} · 恒定 {v['cfr']}"
          f" · 判不了 {v['unknown']}）",
          [f"{x['asset']}：{x.get('reason')}" for x in v["items"][:8]]
          + [f"判不了：{x['asset']}（{x.get('reason')}）" for x in v.get("unknown_items", [])[:3]],
          vnote)

    block("过暗中 / 过亮", f"过暗 {iss['dark']['count']} 支 · 过亮 {iss['bright']['count']} 支"
          f"（判据：全片平均亮度低于 {iss['dark']['threshold_luma']} 或高于 "
          f"{iss['bright']['threshold_luma']}）",
          [f"过暗：{x['asset']}（亮度 {x['luma']}）" for x in iss["dark"]["items"][:6]]
          + [f"过亮：{x['asset']}（亮度 {x['luma']}）" for x in iss["bright"]["items"][:6]])

    b = iss["blur"]
    block("糊 / 没对焦", f"糊过线 {b['hard']} 支 · 偏糊 {b['soft']} 支"
          f"（判据：清晰度读数 > {b['threshold_soft']} 算偏糊，> {b['threshold_hard']} 算报废）",
          [f"{x['asset']}（读数 {x['blur']}）" for x in b["items"][:8]])

    g = iss["grain"]
    gnote = ("读数已经把镜头本身的高频细节一起算进去了，所以命中的先当「该复核」看，"
             "不是一票否决。")
    if g.get("limited"):
        gnote += f"（只测了前 {g['checked']} 支）"
    block("颗粒 / 噪点", f"量了 {g['checked']} 支，读数超过 {g['threshold']} 的 {g['hot']} 支",
          [f"{x['asset']}（颗粒读数 {x['grain']}）" for x in g["items"][:8]], gnote)

    br = iss["brand"]
    specific = [x for x in br["items"] if x.get("specific")]
    wide = [x for x in br["items"] if not x.get("specific")]
    block("别人的品牌字样",
          f"疑似真品牌/商标 {len(specific)} 段；另有 {len(wide)} 段是宽判据顺带命中"
          f"（判据用 `brand_safety.py` 的词表，为了保守把「手机 / 打码」这类词也收了，"
          f"所以逐条列原文让你自己看）",
          [f"`{x['asset']}` {x['in']}–{x['out']}s：{'；'.join(x['flags'])}（命中："
           f"{'、'.join(x['tokens'][:3])}）" for x in (specific + wide)[:12]],
          "这几段进片前要么裁切/模糊，要么在 decisions 里写明客户已授权。")

    au = iss["audio_missing"]
    block("没有音轨", f"{au['count']} 支没有声音（只能当画面用）",
          [x["asset"] for x in au["items"][:10]])

    al = iss["audio_low"]
    block("声音偏小", f"{al['count']} 支峰值低于 {al['threshold_peak_db']} dBFS（口播贴不到台词的响度）",
          [f"{x['asset']}（峰值 {x['peak_db']} dBFS）" for x in al["items"][:8]])

    nf = iss["noise_floor"]
    if nf["status"] == "skipped":
        block("底噪", "**该项跳过**",
              [], f"原因：{nf.get('reason') or 'denoise.py 还没落地'}"
              "——没量过就不报数，不拿别的读数假装。")
    else:
        thr = nf.get("threshold_dbfs")
        thr_txt = f"停顿段残余 > {thr} dBFS" if thr is not None else "denoise.py 的 need_denoise 判据"
        block("底噪偏重", f"量了 {nf['measured']} 支，其中 {nf['count']} 支按判据该先清一遍"
              f"（判据来自 denoise.py --measure：{thr_txt}）",
              [f"{x['asset']}（停顿残余 {x['pause_residual_dbfs']} dBFS · SNR {x['snr_db']} dB）"
               for x in nf["items"][:8]],
              "这不是「放着不能用」，是「进片前先跑一遍 scripts/denoise.py --apply」的清单。"
              + (f"另有 {len(nf['unmeasured'])} 支没量到：{'、'.join(nf['unmeasured'][:3])}"
                 f"（音频解码器缺失之类，没量过就不算它没问题）" if nf.get("unmeasured") else "")
              + (f"｜{nf['note']}" if nf.get("note") else ""))

    ts = iss["too_short"]
    block("太短（剪不进去）", f"{ts['count']} 支不到 {ts['threshold_s']} 秒",
          [f"{x['asset']}（{x['duration']}s）" for x in ts["items"][:8]])

    c = iss["cue"]
    block("场记口令（三二一 / 再来一次）", f"{c['count']} 支命中；另有 {c['unjudgeable']} 支没有词级"
          f"转写，判不了（不当作干净）",
          [f"{x['asset']}：" + "；".join(f"{y['kind']}「{y['text']}」@{y['t0']}s"
                                       for y in x["cues"][:3]) for x in c["items"][:8]])
    return L


def render_md(rep: dict) -> str:
    t, u, s = rep["totals"], rep["usable_clips"], rep["speech"]
    L: list[str] = []
    L.append(f"# {SIGNATURE} · {Path(rep['library']).name}")
    L.append("")
    L.append(f"盘于 {rep['built_at']} · 数据源 `{rep['source_index']}`")
    L.append("")
    L.append("## 一句话结论")
    L.append("")
    L.append(_top_line(rep))
    L.append("")

    # —— 有多少料
    L.append("## 1. 你手上有多少料")
    L.append("")
    L.append(f"共 {t['assets']} 个文件：视频 {t['video']} 支、照片 {t['photo']} 张"
             + (f"、文档 {t['doc']} 份" if t["doc"] else "")
             + (f"、音频 {t['audio']} 段" if t["audio"] else "")
             + f"。总时长 {hms(t['duration_s'])}。")
    L.append("")
    photo_note = (f"，另有 {t['photo_usable']} 张照片可用"
                  f"（照片按 {rep['issues']['seconds']['photo_assumption_s']:.0f} 秒/张编排）"
                  if t.get("photo_usable") else "")
    L.append(f"其中**能进片**的视频画面 {hms(t['usable_s'])}"
             f"（评级 must-use 的 {hms(t['direct_use_s'])}，往后翻有分档）{photo_note}。")
    L.append("")
    L.append("**按拍摄日期**")
    L.append("")
    L.append("| 日期 | 文件数 | 总时长 | 能进片 |")
    L.append("|---|---|---|---|")
    for d in t["by_date"]:
        L.append(f"| {d['date']} | {d['assets']} | {hms(d['duration_s'])} | {hms(d['usable_s'])} |")
    L.append("")
    L.append("**按场景**（场景名取自索引里 L2 的内容标签；没标过的按所在文件夹归类）")
    L.append("")
    L.append("| 场景 | 文件数 | 总时长 | 能进片 | 其中口播 |")
    L.append("|---|---|---|---|---|")
    for sc in t["by_scene"][:12]:
        L.append(f"| {sc['scene']} | {sc['assets']} | {hms(sc['duration_s'])} | "
                 f"{hms(sc['usable_s'])} | {hms(sc['speech_s'])} |")
    L.append("")

    # —— 讲了什么
    L.append("## 2. 这些料讲了什么（口播内容）")
    L.append("")
    L.append(f"有口播的素材 {s['assets_with_speech']} 支，口播总时长 {hms(s['speech_total_s'])}，"
             f"其中落在可用段里的有 {hms(s['speech_usable_s'])}。"
             "下面这些词是从转写原文里数出来的主题，每个词都附一句原话当出处：")
    L.append("")
    if rep["keywords"]:
        L.append("| 主题词 | 覆盖几段 | 出现几次 | 一句原话（出处） |")
        L.append("|---|---|---|---|")
        for k in rep["keywords"]:
            ev = k["evidence"][0]
            quote = str(ev["text"])[:34]
            L.append(f"| {k['keyword']} | {k['assets']} | {k['hits']} | "
                     f"「{quote}」— {ev['asset']} |")
    else:
        L.append("这批料里没有反复出现、能当主题的词（口播太少或都在说别的），"
                 "选题只能靠你补一句「你最想讲的到底是什么」。")
    L.append("")
    if s["top_assets"]:
        L.append("口播最多的几支：" + "；".join(
            f"{r['asset']}（{hms(r['speech_s'])}，其中可用 {hms(r['usable_s'])}）"
            for r in s["top_assets"][:4]) + "。")
        L.append("")

    # —— 可用片段
    L.append("## 3. 能直接进片的片段")
    L.append("")
    g = u["grades"]
    L.append("| 分档 | 段数 | 时长 | 用法 |")
    L.append("|---|---|---|---|")
    L.append(f"| must-use | {g.get('must-use', {}).get('segments', 0)} | "
             f"{hms(g.get('must-use', {}).get('seconds', 0))} | 直接进片 |")
    L.append(f"| backup | {g.get('backup', {}).get('segments', 0)} | "
             f"{hms(g.get('backup', {}).get('seconds', 0))} | 补位/过渡，内容对得上才用 |")
    L.append(f"| do-not-use | {g.get('do-not-use', {}).get('segments', 0)} | "
             f"{hms(g.get('do-not-use', {}).get('seconds', 0))} | 不进片 |")
    if g.get("unknown"):
        L.append(f"| 未评级 | {g['unknown']['segments']} | {hms(g['unknown']['seconds'])} | "
                 f"索引里没写分级，先当「不可用」 |")
    L.append("")
    L.append(f"**可直接进片的可用时长：{hms(u['direct_use_s'])}**"
             f"（must-use）；加上 backup 是 {hms(u['with_backup_s'])}。")
    L.append("")
    if u["top"]:
        L.append("挑几段给你看（这几段是「这批料里最能用的」）：")
        L.append("")
        for c in u["top"][:6]:
            tc = f"{c['in']:.1f}–{c['out']:.1f}s" if c["kind"] == "video" else "静帧"
            L.append(f"- `{c['asset']}` {tc}（{c['grade']}）{_clip_why(c)}")
        L.append("")

    # —— 问题
    L.append("## 4. 这批料的问题（逐类，带文件名与读数）")
    L.append("")
    L.extend(_md_issues(rep))

    # —— 选题
    L.append("## 5. 这些素材最适合做的选题")
    L.append("")
    tp = rep.get("topics") or {}
    if not tp.get("ok"):
        L.append(f"**{tp.get('verdict') or '这个库拍不出片。'}**")
        L.append("")
        L.append("缺的是：")
        L.append("")
        for m in tp.get("missing") or []:
            L.append(f"- {m}")
        L.append("")
        L.append("这种库不硬凑选题：先按上面几条把料补齐，再回来盘一次。")
        L.append("")
    else:
        L.append(f"{tp.get('verdict')}")
        L.append("")
        L.append("方向不是预先占好的三个槽：先数这个库里有多少段口播在讲同一类判断、有多少段画面"
                 "能对上，再按支撑度（口播素材数 × 3 + 画面证据数）排。撑得住的方向才出题目，"
                 "所以两个不同生意的库不会拿到同一组标题。")
        L.append("")
        L.append("三条选题的**招牌（锚点）互不相同**：一个画面标签/主题词只能当一回招牌，"
                 "用过的在后面降权、用完就少出一条——宁少勿凑。**标题句式也各不相同**："
                 "每套标题写明用的是 references/content-system.md 里的哪一式，同库三条不重样。")
        L.append("")
        for p in tp["proposals"]:
            d = p["duration_range"]
            L.append(f"### 选题 {p['index']} · {p['topic']}")
            L.append("")
            L.append(f"- **定位**：{p['label']}（{p['crowd']}）")
            L.append(f"- **话题类型**：{p['topic_kind']}（出处：{p['topic_source']}）")
            tb = p.get("title_basis") or {}
            if tb.get("frame"):
                L.append(f"- **标题句式**：`{tb['frame']}`（{tb.get('frame_point')}，出处："
                         f"{tb.get('frame_source')}）——{tb.get('frame_when')}")
            if tb.get("note"):
                L.append(f"- **为什么是这个题目**：{tb['note']}")
            L.append(f"- **为什么这条适合你**：{p['topic_why']}")
            L.append(f"- **用的公式**：{p['formula_source']}")
            L.append(f"- **开篇钩子**：「{p['hooks'][0]['line']}」（{p['hooks'][0]['type']}，"
                     f"出处：{p['hooks'][0]['source']}）")
            L.append(f"- **片子骨架**：{p['structure']}（{p['structure_source']}）")
            if d.get("enough"):
                L.append(f"- **能撑多长**：{d['min_s']}–{d['max_s']} 秒。{d['basis']}")
            else:
                L.append(f"- **能撑多长**：素材只够 {d['max_s']} 秒，这套骨架要 {d['min_s']} 秒"
                         f"（差 {d['min_s'] - d['max_s']} 秒）。{d['basis']}")
                L.append("  - ⚠️ 差的那几秒得补拍，或者砍掉一拍再开拍——硬拉长只会灌水。")
            L.append("")
            if p["material_basis"]:
                L.append("**用库里这几段**（时间码是这条片里实际要切的那一小段）：")
                L.append("")
                L.append("| 用途 | 素材 | 取哪一段 | 为什么是它 |")
                L.append("|---|---|---|---|")
                for b in p["material_basis"]:
                    tc = (f"{b['in']:.1f}–{b['out']:.1f}s（{b['seconds']:.1f}s）"
                          if b.get("out") else "静帧")
                    mark = "**【关键】** " if b.get("essential") else ""
                    L.append(f"| {b['role']} | {mark}`{b['asset']}` | {tc} | {b['why']} |")
                L.append("")
                for b in p["material_basis"]:
                    if b.get("essential"):
                        L.append(f"关键素材 = `{b['asset']}`（{b['role']}）：{b['essential_why']}")
                        L.append("")
            if p.get("generated_roles"):
                L.append("**这几拍不用拍，代码出卡**：" + "；".join(
                    f"{g['role']}（{g.get('template') or '卡片'}）" for g in p["generated_roles"]))
                L.append("")
            if p["missing_shots"]:
                L.append("**还缺什么（补拍 / 代码生成）**：")
                L.append("")
                for m in p["missing_shots"]:
                    L.append(f"- {m['role']}：{m['how']}")
                L.append("")
            elif p.get("material_basis"):
                L.append("这条选题**不缺画面**：四拍都能从现有素材里凑齐，直接能剪。")
                L.append("")
            L.append(f"选定就跑：`python3 scripts/ideate.py --project <项目目录> "
                     f"--pick {p['index']} --guide`")
            L.append("")

    # —— 下一步
    L.append("## 6. 下一步")
    L.append("")
    L.append("1. 挑一条选题：`python3 scripts/ideate.py --project <项目目录> "
             "--from-inventory \"" + str(rep["library"]) + "\"`，再 `--pick N --guide` 出拍片卡；")
    L.append("2. 该规整的料先规整：变帧/横拍的素材在进片前处理掉，别带进成片；")
    L.append("3. 缺的画面按上面「还缺什么」补拍，或让代码生成卡片顶上。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("口径说明：所有数字都能从 `_cutskills/index.json` 与探针读数复算；"
             "画质阈值取 `scripts/index_assets.py` 的 L0 阈值，可变帧率取 `scripts/normalize.py` "
             "的判定，品牌字样取 `scripts/brand_safety.py`，场记口令取 `scripts/cue_guard.py`，"
             "底噪取 `scripts/denoise.py --measure`。探针结果缓存在 `_cutskills/probe_cache.json`。")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 主流程

def build_report(lib: Path, *, duration_s: int = 35, persona: str = "", audience: str = "",
                 limits: dict | None = None, with_topics: bool = True) -> dict:
    index = load_index(lib)
    assets = index.get("assets") or []
    cache = load_cache(lib)
    totals = compute_totals(assets)
    speech = speech_stats(assets)
    keywords = extract_keywords(assets)
    usable = compute_usable(assets)
    issues = compute_issues(lib, assets, cache, limits)
    save_cache(lib, cache, assets)
    pool = _candidates(
        assets,
        {str(x["asset"]) for x in (issues["cue"]["items"] or [])},
        {str(x["asset"]) for x in (issues["brand"]["items"] or [])})
    rep = {
        "version": 1,
        "library": str(lib),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source_index": str(lib / SYS_DIR / "index.json"),
        "source_index_built_at": index.get("built_at"),
        "target": {"width": TARGET_W, "height": TARGET_H},
        "totals": totals,
        "speech": speech,
        "keywords": keywords,
        "usable_clips": usable,
        "issues": issues,
        "limits": {"vfr": (limits or {}).get("vfr"), "grain": (limits or {}).get("grain"),
                   "audio": (limits or {}).get("audio")},
        # 选题要按段挑片：分好筐的片段池跟着报告落盘，`--from-inventory` 不必重读整份索引
        "clip_pool": pool,
        # 六个方向各自的支撑度（口播素材数 + 画面证据数）：选题按它排序，不按固定槽位
        "directions": direction_support(assets, pool, [k["keyword"] for k in keywords]),
        "assets_index": assets,        # 只在本进程内用（选题挑片），不落盘
    }
    if with_topics:
        built = proposals_from_inventory(rep, duration_s=duration_s,
                                         persona=persona, audience=audience)
        rep["topics"] = {"ok": built["ok"], "verdict": built["verdict"],
                         "missing": built["missing"], "proposals": built["proposals"],
                         "anchors": built.get("anchors") or [],
                         "skeletons": built.get("skeletons") or [],
                         "dropped": built.get("dropped") or [],
                         "keywords": built.get("keywords") or []}
    else:
        rep["topics"] = {"ok": False, "verdict": "本次未反推选题（--no-topics）",
                         "missing": [], "proposals": []}
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="素材盘点：这批料能拍出什么片子")
    ap.add_argument("--library", required=True, help="素材库目录（须已建索引）")
    ap.add_argument("--persona", default="", help="出镜人身份（可选，写进选题定位）")
    ap.add_argument("--audience", default="", help="给谁看（可选）")
    ap.add_argument("--duration", type=int, default=35, help="期望片长（秒），用于骨架最短时长")
    ap.add_argument("--vfr-limit", type=int, default=0, help="最多判几支帧率（0=全部）")
    ap.add_argument("--grain-limit", type=int, default=0, help="最多量几支颗粒读数（0=全部）")
    ap.add_argument("--audio-limit", type=int, default=0, help="最多量几支底噪（0=全部）")
    ap.add_argument("--no-topics", action="store_true", help="只盘点，不反推选题")
    ap.add_argument("--json", action="store_true", help="顺带把关键读数打到 stdout")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    if not lib.is_dir():
        print(f"素材库不存在：{lib}", file=sys.stderr)
        return 2
    limits = {"vfr": a.vfr_limit, "grain": a.grain_limit, "audio": a.audio_limit}

    print(f"盘点 {lib}")
    rep = build_report(lib, duration_s=a.duration, persona=a.persona, audience=a.audience,
                       limits=limits, with_topics=not a.no_topics)

    sysdir = lib / SYS_DIR
    sysdir.mkdir(parents=True, exist_ok=True)
    md_p = sysdir / REPORT_MD
    if md_p.exists() and SIGNATURE not in md_p.read_text(errors="ignore")[:400]:
        # 同名的旧文件不是我们写的（老版本的「可用画面」表）：留个备份再覆盖，别直接抹掉
        bak = md_p.with_name(f"{REPORT_MD}.legacy-{datetime.now():%Y%m%d-%H%M%S}")
        md_p.replace(bak)
        print(f"  旧报告不是本工具生成，已备份到 {bak.name}")

    md_p.write_text(render_md(rep), encoding="utf-8")
    (sysdir / REPORT_JSON).write_text(
        json.dumps({k: v for k, v in rep.items() if k != "assets_index"},
                   ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    t, u, s, iss = rep["totals"], rep["usable_clips"], rep["speech"], rep["issues"]
    print(f"  {t['assets']} 个文件（视频 {t['video']} / 照片 {t['photo']}），总时长 {hms(t['duration_s'])}")
    print(f"  可用画面 {hms(u['with_backup_s'])}（must-use {hms(u['direct_use_s'])}）· "
          f"可用口播 {hms(s['speech_usable_s'])}")
    print(f"  问题：横拍 {iss['orientation']['landscape']} · 变帧 {iss['vfr']['vfr']}/已判 "
          f"{iss['vfr']['checked']}（判不了 {iss['vfr']['unknown']}）"
          f" · 过暗 {iss['dark']['count']} · 偏糊 {iss['blur']['soft'] + iss['blur']['hard']}"
          f" · 颗粒 {iss['grain']['hot']} · 品牌字样 {iss['brand']['count']}"
          f"（其中疑似真品牌 {iss['brand']['specific']}）"
          f" · 无音轨 {iss['audio_missing']['count']} · 声音偏小 {iss['audio_low']['count']}"
          f" · 短于1秒 {iss['too_short']['count']} · 口令 {iss['cue']['count']}")
    if iss["noise_floor"]["status"] == "skipped":
        print(f"  底噪：跳过（{iss['noise_floor'].get('reason') or '工具尚未落地'}）")
    else:
        print(f"  底噪：量了 {iss['noise_floor']['measured']} 支，偏重 {iss['noise_floor']['count']} 支")
    tp = rep["topics"]
    if tp["ok"]:
        print(f"  选题：{len(tp['proposals'])} 套 —— " + "；".join(
            p["topic"] for p in tp["proposals"]))
    else:
        print(f"  选题：{tp['verdict']}")
        for m in tp["missing"]:
            print(f"    - {m}")
    print(f"→ 报告 {md_p}")
    print(f"→ 读数 {sysdir / REPORT_JSON}")
    if a.json:
        print(json.dumps({"totals": t, "speech": {k: s[k] for k in
                                                  ("assets_with_speech", "speech_total_s",
                                                   "speech_usable_s")},
                          "usable": {k: u[k] for k in ("direct_use_s", "with_backup_s")},
                          "issues": {k: (v.get("count") if isinstance(v, dict) else None)
                                     for k, v in iss.items()}},
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
