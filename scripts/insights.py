#!/usr/bin/env python3
"""insights.py——内容循环辅助分析（R4）：导入已发布视频的真实数据，生成「下一条改什么」。

产品边界（评审报告 §10.2/§12 一再强调的三条）：
  1. 只做**辅助分析**：给的是「哪类内容该多做/少做」的相对信号，绝不承诺播放量；
  2. 数据必须来自用户**主动授权导入**的发布后台导出 CSV——系统不抓平台、不代运营；
  3. 证据不足（<5 条）就明说样本不够，硬编建议比没有建议更害人。

输入 CSV 列（表头兼容中英，顺序随意）：
  date/日期, title/标题, views/播放, avg_watch_pct/平均观看时长占比, likes/点赞,
  comments/评论, shares/分享, saves/收藏, follows/涨粉, topic_type/内容类型(A1-A5 可选)

产出：
  <项目或库>/insights/report.md      —— 人话版「下一条内容改什么」
  <项目或库>/insights/metrics.json   —— 机器读数（供 Agent 转述）

用法：
  python3 scripts/insights.py --library <库根> --csv 发布数据.csv
  python3 scripts/insights.py --library <库根> --csv 发布数据.csv --topic-type-col 内容类型
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

# CSV 列名兼容表（中英）
COLS = {
    "date": ("date", "日期", "发布时间", "发布日期"),
    "title": ("title", "标题", "题目"),
    "views": ("views", "播放", "播放量", "播放数"),
    "watch": ("avg_watch_pct", "平均观看时长占比", "观看时长占比", "完播", "avg_watch"),
    "likes": ("likes", "点赞", "点赞量"),
    "comments": ("comments", "评论", "评论量", "评论数"),
    "shares": ("shares", "分享", "转发"),
    "saves": ("saves", "收藏"),
    "follows": ("follows", "涨粉", "新增粉丝", "关注"),
    "topic_type": ("topic_type", "内容类型", "类型", "阶段"),
}
MIN_SAMPLE = 5


def _pick(row: dict, key: str) -> str:
    for name in COLS[key]:
        if name in row:
            return (row.get(name) or "").strip()
    return ""


def _num(s: str) -> float | None:
    s = (s or "").strip()
    pct = "%" in s
    s = s.replace("%", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if pct:                      # 「35%」→ 0.35；裸数字按原样（平台导出两种都碰得到）
        v /= 100.0
    return v


def load_rows(csv_path: Path) -> list[dict]:
    """读 CSV → 统一行名的记录列表；解析失败的行如实跳过并计数。"""
    rows, skipped = [], 0
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            views = _num(_pick(raw, "views"))
            title = _pick(raw, "title")
            if views is None or not title:
                skipped += 1
                continue
            rec = {
                "date": _pick(raw, "date"),
                "title": title,
                "views": views,
                "watch": _num(_pick(raw, "watch")),
                "likes": _num(_pick(raw, "likes")) or 0.0,
                "comments": _num(_pick(raw, "comments")) or 0.0,
                "shares": _num(_pick(raw, "shares")) or 0.0,
                "saves": _num(_pick(raw, "saves")) or 0.0,
                "follows": _num(_pick(raw, "follows")) or 0.0,
                "topic_type": _pick(raw, "topic_type") or "",
            }
            rec["inter_rate"] = round((rec["likes"] + rec["comments"] * 2 + rec["shares"] * 3
                                       + rec["saves"] * 2) / max(views, 1) * 100, 2)  # %
            rows.append(rec)
    return rows, skipped


def analyze(rows: list[dict]) -> dict:
    """三条辅助信号，全部基于相对分位（不碰绝对流量承诺）。"""
    out: dict = {"n": len(rows), "signals": [], "warnings": []}
    if len(rows) < MIN_SAMPLE:
        out["warnings"].append(
            f"只有 {len(rows)} 条有效数据（< {MIN_SAMPLE}）：样本不够，"
            "以下读数只能当参考，硬按它改内容比不改更危险。多发几条同口径内容再来。")
        return out

    views_sorted = sorted(r["views"] for r in rows)
    med = statistics.median(views_sorted)
    q = lambda p: views_sorted[min(len(views_sorted) - 1, int(len(views_sorted) * p))]
    hi_cut, lo_cut = q(0.7), q(0.3)
    winners = [r for r in rows if r["views"] >= hi_cut]
    losers = [r for r in rows if r["views"] <= lo_cut]
    out["median_views"] = med
    out["hi_cut"], out["lo_cut"] = hi_cut, lo_cut

    # 信号① 完播分差：赢家 vs 输家的平均观看占比差
    w_watch = [r["watch"] for r in winners if r["watch"] is not None]
    l_watch = [r["watch"] for r in losers if r["watch"] is not None]
    if w_watch and l_watch:
        d = statistics.mean(w_watch) - statistics.mean(l_watch)
        sig = {"name": "完播分差", "value": round(d, 3)}
        if d >= 0.05:
            sig["advice"] = (f"赢的条目平均观看占比高 {d * 100:.0f} 个百分点——前 3 秒与"
                             "信息密度是你当前最值钱的杠杆，下一条优先打磨开画面与开头 3 句。")
        elif d <= -0.05:
            sig["advice"] = ("高播放条目反而看得更浅——标题给预期、内容没接住。"
                             "下一条先解决「标题承诺 vs 画面兑现」的一致性，别追更大的题。")
        else:
            sig["advice"] = "赢家输家完播接近——播放差异主要来自选题与封面，不是节奏。"
        out["signals"].append(sig)

    # 信号② 互动构成：赢家靠什么互动（评论/分享/收藏的相对形状）
    def mix(group: list[dict]) -> dict:
        tot = sum(max(r["likes"] + r["comments"] * 2 + r["shares"] * 3 + r["saves"] * 2, 1)
                  for r in group)
        return {k: round(sum(r[k] * w for r in group) / max(tot, 1) * 100, 1)
                for k, w in (("likes", 1), ("comments", 2), ("shares", 3), ("saves", 2))}
    if winners:
        wm, lm = mix(winners), mix(losers)
        sig = {"name": "互动构成", "winners": wm, "losers": lm}
        for k, label in (("comments", "评论"), ("shares", "分享"), ("saves", "收藏")):
            if wm.get(k, 0) >= lm.get(k, 0) + 3:
                sig["advice"] = (f"赢的条目{label}占比明显更高——{label}是你观众的反应方式，"
                                 f"下一条在内容里主动留{label}钩子（具体提问/可存清单/值得转发）。")
                break
        out["signals"].append(sig)

    # 信号③ 内容类型配比（用户标了 topic_type 才启用）
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        if r["topic_type"]:
            by_type.setdefault(r["topic_type"], []).append(r)
    if len(by_type) >= 2:
        rank = sorted(((t, statistics.median([r["views"] for r in g]), len(g))
                       for t, g in by_type.items()), key=lambda x: -x[1])
        sig = {"name": "类型配比", "ranking": [{"type": t, "median_views": m, "n": n}
                                               for t, m, n in rank]}
        top, tail = rank[0], rank[-1]
        sig["advice"] = (f"「{top[0]}」中位播放是「{tail[0]}」的 "
                         f"{top[1] / max(tail[1], 1):.1f} 倍——下一批多给 {top[0]}，"
                         f"{tail[0]} 只在有新证据时再做。配比是参考不是公式，观众会变。")
        out["signals"].append(sig)

    # 信号④ 涨粉效率（有 follows 数据才说）
    f_rows = [r for r in rows if r["follows"] > 0]
    if len(f_rows) >= 3:
        best = max(f_rows, key=lambda r: r["follows"] / max(r["views"], 1))
        out["signals"].append({
            "name": "涨粉效率",
            "best_title": best["title"],
            "follow_rate": round(best["follows"] / max(best["views"], 1) * 100, 2),
            "advice": f"「{best['title']}」单位播放涨粉最高——回看它的结尾与人格化表达，"
                      "涨粉钩子通常在最后 5 秒。",
        })
    return out


def render_report(an: dict, rows: list[dict]) -> str:
    L = ["# 内容循环 · 下一条改什么（辅助分析）", "",
         f"> 数据 {an['n']} 条 · 生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
         "> 本报告只给相对信号，**不承诺任何播放量**；配比是参考不是公式。", ""]
    for w in an.get("warnings") or []:
        L += [f"⚠️ {w}", ""]
    if an.get("median_views"):
        L += [f"中位播放 {an['median_views']:.0f} · 赢家线 {an['hi_cut']:.0f} · 输家线 {an['lo_cut']:.0f}", ""]
    for sig in an.get("signals") or []:
        L += [f"## {sig['name']}", ""]
        body = sig.get("advice") or json.dumps({k: v for k, v in sig.items()
                                                if k not in ("name",)}, ensure_ascii=False)
        L += [body, ""]
    if rows:
        L += ["## 一页明细（按播放降序，前 10）", "",
              "| 标题 | 播放 | 观看占比 | 互动率% | 类型 |", "|---|---:|---:|---:|---|"]
        for r in sorted(rows, key=lambda x: -x["views"])[:10]:
            w = f"{r['watch'] * 100:.0f}%" if r["watch"] is not None else "—"
            L.append(f"| {r['title'][:24]} | {r['views']:.0f} | {w} | {r['inter_rate']} | "
                     f"{r['topic_type'] or '—'} |")
        L.append("")
    L += ["> 下一步闭环：把建议当**假设**——按下一条的选题做片、发布、再导数据回来，",
          "看信号是否兑现。连续两轮兑现的信号才值得固化成你的配比。", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="内容循环辅助分析（R4）")
    ap.add_argument("--library", default="", help="库根（report 落 <库>/insights/）")
    ap.add_argument("--project", default="", help="或落到某个项目下")
    ap.add_argument("--csv", required=True, help="发布后台导出的数据 CSV（用户主动提供）")
    ap.add_argument("--out", default="", help="显式输出目录（缺省按 --project/--library 推）")
    a = ap.parse_args()
    base = Path(a.out) if a.out else (
        Path(a.project) if a.project else (Path(a.library) if a.library else None))
    if base is None:
        print("✗ 需要 --library 或 --project（决定 report 落点）", file=sys.stderr)
        return 2
    rows, skipped = load_rows(Path(a.csv))
    if skipped:
        print(f"· 跳过 {skipped} 行解析不了的（缺播放或标题）")
    if not rows:
        print("✗ 一条有效数据都没有——检查 CSV 表头（标题/播放）", file=sys.stderr)
        return 2
    an = analyze(rows)
    out_dir = base / "insights"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps(an, ensure_ascii=False, indent=1), encoding="utf-8")
    report = out_dir / "report.md"
    report.write_text(render_report(an, rows), encoding="utf-8")
    print(f"→ {report}")
    print(f"→ {out_dir / 'metrics.json'}")
    for sig in an.get("signals") or []:
        if sig.get("advice"):
            print(f"· {sig['name']}：{sig['advice'][:80]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
