#!/usr/bin/env python3
"""review_week.py——每周复盘：读 <素材库>/content-log.jsonl，出 <素材库>/insights/week-YYYY-MM-DD.md。

周节奏的那一环。BRIEF 说的是「每条发布后记录数据、每周复盘、调整下周选题」：
记录由 content_log.py 落库，这个脚本只做「一周一次、把账翻成人话 + 下周具体做什么」。

四块内容（缺数据就明说缺，不凑）：
  一、本周发了什么（表）
  二、哪类内容完播更好（按题材 / 包装包 / 节奏分组）
  三、哪条涨粉效率高
  四、下周选题建议（具体到配方与变量，不写「多拍好内容」这种空话）

口径（不许越界的一条）：
  **只给相对信号，不承诺任何播放量。** 数据只来自用户自己回填的发布后台数字。
  样本不够就写「样本不够，不下结论」——硬编结论比没有结论更害人。

数据源只认一份账本：<素材库>/content-log.jsonl（insights.py 现在也优先读它）。
没有账本就退 2 并提示先用 content_log.py --add 记一条，绝不拿 CSV 另起一套账各说各话。

用法：
  python3 scripts/review_week.py --library <素材库>
  python3 scripts/review_week.py --library <素材库> --week 2026-11-09   # 该周周一起算
  python3 scripts/review_week.py --log <某个 content-log.jsonl> --out <输出目录>

时间口径：`--week` 收任意日期，一律归一到它所在那一周的周一；默认上一自然周（周一起算）。
一律本地时区，不用 UTC 裸时间戳。
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from content_log import (  # noqa: E402  账本读写只此一份，别在复盘里重写一套
    LOG_NAME, LogError, latest_by_id, read_log, resolve_project,
)

# 分组维度：账本里的字段 → 报告里的小标题 → 短名
DIMS = (
    ("topic_type", "题材（内容档位 A1–A5）", "题材"),
    ("pack", "包装包", "包装包"),
    ("plan", "节奏（剪辑骨架 plan）", "节奏"),
)

# 样本下限 5：出处 insights.py 的 MIN_SAMPLE（同一条产品边界「证据不足 <5 条就明说样本不够」），
# 不是本脚本自拟的新数字，别单独改——要改先改 insights.py 并同步 references/。
MIN_SAMPLE = 5
# 完播均值的最低条数 3：出处 insights.py「只有 N 条能用的观看占比（<3）→ 完播类信号给不出结论」。
# 少于此数只报条数，不报平均值，更不下结论。
WATCH_FLOOR = 3


# ---------------------------------------------------------------- 周区间

def monday_of(d: date) -> date:
    """所在自然周的周一（周一当天算本周第一天）。"""
    return d - timedelta(days=d.weekday())


def week_bounds(week: str | None) -> tuple[date, date]:
    """--week 归一 → (周一, 周日)，两端都含；不给就看上一自然周。"""
    if week:
        try:
            d = datetime.strptime(week.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise LogError(f"--week 要的是 YYYY-MM-DD（任意一天都行，会归一到那周的周一），"
                           f"收到「{week}」。") from None
    else:
        d = datetime.now().date() - timedelta(days=7)   # 上一自然周
    start = monday_of(d)
    return start, start + timedelta(days=6)


def _rec_day(rec: dict) -> date | None:
    raw = str(rec.get("posted_at") or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------- 统计

def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def group_stats(rows: list[dict], key: str) -> list[dict]:
    """按某个字段分组 → [{key, n, n_watch, watch, view_med, follows, pack, plan, topics}]。"""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        v = r.get(key)
        if v in (None, ""):
            continue
        buckets.setdefault(str(v), []).append(r)
    out: list[dict] = []
    for k, g in buckets.items():
        watches = [w for w in (_num(r.get("avg_watch_pct")) for r in g) if w is not None]
        views = [v for v in (_num(r.get("views")) for r in g) if v is not None]
        follows = sum(f for f in (_num(r.get("follows")) for r in g) if f is not None)

        def modal(field: str) -> str | None:
            vals = [str(r.get(field)) for r in g if r.get(field) not in (None, "")]
            return max(set(vals), key=vals.count) if vals else None

        out.append({
            "key": k, "n": len(g), "n_watch": len(watches),
            "watch": round(sum(watches) / len(watches), 4) if watches else None,
            "view_med": median(views) if views else None,
            "follows": follows,
            "pack": modal("pack"), "plan": modal("plan"),
            "topics": sorted({str(r.get("topic")) for r in g if r.get("topic")}),
        })
    out.sort(key=lambda s: (-(s["watch"] if s["watch"] is not None else -1), -s["n"]))
    return out


def eligible(g: dict) -> bool:
    return g["n"] >= MIN_SAMPLE and g["n_watch"] >= WATCH_FLOOR


# ---------------------------------------------------------------- 排版小件

def _disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    s = str(s)
    return s + " " * max(width - _disp_width(s), 0)


def _cell(v, limit: int = 20) -> str:
    """表格单元格：竖线打断、换行压平、超长截断。"""
    s = "—" if v in (None, "") else str(v)
    s = s.replace("|", "／").replace("\n", " ").strip()
    return s if _disp_width(s) <= limit else s[:limit] + "…"


def _num_s(v) -> str:
    if v is None:
        return "—"
    return f"{v:g}" if isinstance(v, float) and not float(v).is_integer() else f"{int(v)}"


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


# ---------------------------------------------------------------- 报告

def _week_table(rows: list[dict]) -> list[str]:
    L = ["| 发布日 | 项目 | 平台 | 片长 | 包装包 | 节奏 | 播放 | 完播 | 涨粉 | 链接 |",
         "|---|---|---|---:|---|---|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda x: str(x.get("posted_at") or "")):
        L.append("| " + " | ".join([
            str(r.get("posted_at") or "—"),
            _cell(r.get("project"), 16),
            str(r.get("platform") or "—"),
            f"{_num_s(r.get('duration_s'))}s" if _num(r.get("duration_s")) else "—",
            _cell(r.get("pack"), 14),
            _cell(r.get("plan"), 18),
            _num_s(_num(r.get("views"))),
            _pct(_num(r.get("avg_watch_pct"))),
            _num_s(_num(r.get("follows"))),
            _cell(r.get("post_url"), 42),
        ]) + " |")
    return L


def _dim_block(rows: list[dict], key: str, label: str, week_watch: float | None) -> tuple[list[str], list[dict]]:
    """一个分组维度的段落 + 该维度里过样本线的组。"""
    groups = group_stats(rows, key)
    L = [f"### 按{label.split('（')[0]}", ""]
    if not groups:
        L += [f"本周记录里这一项全是空的（项目真值里取不到 `{key}`）——"
              f"先看 `content_log.py --list` 里这列，再决定要不要补。", ""]
        return L, []
    L += ["| 分组 | 条数 | 回填完播 | 平均完播 | 播放中位 | 涨粉合计 | 判定 |",
          "|---|---:|---:|---:|---:|---:|---|"]
    qualified: list[dict] = []
    for g in groups:
        if eligible(g):
            verdict = "过样本线"
            qualified.append(g)
            if g["watch"] is not None and week_watch is not None:
                d = (g["watch"] - week_watch) * 100
                verdict += ("（与全周持平）" if abs(d) < 0.5
                            else f"（{'高' if d >= 0 else '低'} {abs(d):.0f} 个百分点）")
        elif g["n"] < MIN_SAMPLE:
            verdict = f"样本不够（n={g['n']} < {MIN_SAMPLE}），不下结论"
        else:
            verdict = f"条数够但回填完播只有 {g['n_watch']} 条（< {WATCH_FLOOR}），不下结论"
        L.append("| " + " | ".join([
            _cell(g["key"], 30), str(g["n"]), str(g["n_watch"]),
            _pct(g["watch"]), _num_s(g["view_med"]), _num_s(g["follows"]), verdict,
        ]) + " |")
    L.append("")
    return L, qualified


def build_report(rows: list[dict], all_n: int, start: date, end: date,
                 log: Path) -> str:
    watches_all = [w for w in (_num(r.get("avg_watch_pct")) for r in rows) if w is not None]
    week_watch = round(sum(watches_all) / len(watches_all), 4) if watches_all else None
    views_all = [v for v in (_num(r.get("views")) for r in rows) if v is not None]

    # 声明必须落在首行（照 insights.py 的措辞，不另造句子）
    L = ["> 本报告只给相对信号，**不承诺任何播放量**；样本不够的组合一律写「样本不够，不下结论」。",
         "",
         f"# 内容周复盘 · {start.isoformat()} ~ {end.isoformat()}",
         "",
         f"> 账本 `{log}` · 本周 {len(rows)} 条 / 全库 {all_n} 条 · "
         f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}（本地时间）"]

    print_notes: list[str] = []
    missing_views = [r for r in rows if _num(r.get("views")) is None]
    if missing_views:
        print_notes.append(f"本周有 {len(missing_views)} 条还没回填播放数据（`--update --id …`），"
                           f"下面的播放类读数只算已回填的那些")
    if rows and week_watch is not None:
        print_notes.append(f"全周平均完播 {week_watch * 100:.0f}%（{len(watches_all)} 条有完播数据）")
    for note in print_notes:
        L.append(f"> · {note}")
    L.append("")

    # ---- 一、本周发了什么
    L += ["## 一、本周发了什么", ""]
    if not rows:
        L += [f"本周（{start.isoformat()} ~ {end.isoformat()}）账上一条记录都没有。",
              "",
              "先把周一发的链接补记进来，周复盘才有依据：",
              "",
              "```bash",
              f"python3 scripts/content_log.py --add --project <项目> --library {log.parent} \\",
              '    --post-url "<发布链接>" --posted-at ' + start.isoformat() + " --platform 小红书",
              "```",
              ""]
    else:
        L += [f"共 {len(rows)} 条。", ""] + _week_table(rows) + [""]

    # ---- 二、完播
    L += ["## 二、哪类内容完播更好", "",
          f"分组口径：完播 = 发布后台的「平均观看时长占比」，只统计已 `--update` 回填的条目；"
          f"一组要同时满足「条数 ≥ {MIN_SAMPLE}」和「回填完播 ≥ {WATCH_FLOOR}」才下结论。", ""]
    qualified_all: list[tuple[str, dict]] = []
    for key, label, short in DIMS:
        block, qualified = _dim_block(rows, key, label, week_watch)
        L += block
        qualified_all += [(short, g) for g in qualified]
    if rows:
        if qualified_all:
            short, best = qualified_all[0]
            L += [f"**结论**：过样本线的组合里，{short}「{best['key']}」的平均完播最高"
                  f"（{_pct(best['watch'])}，{best['n']} 条）；这是相对信号，不是播放承诺。", ""]
        else:
            biggest = max((g["n"] for key, _l, _s in DIMS for g in group_stats(rows, key)), default=0)
            if biggest >= MIN_SAMPLE:
                L += [f"**结论：条数够、完播数据不够——不下结论。** 最大的组合已有 {biggest} 条"
                      f"（≥ {MIN_SAMPLE} 条线），但回填过完播的不到 {WATCH_FLOOR} 条，平均值算不出来。"
                      f"先把本周那几条的 `--avg-watch-pct` 补上（`--update --id …`），下周同一份报告就能判。", ""]
            else:
                L += [f"**结论：本周没有任何组合过样本线（条数 ≥ {MIN_SAMPLE} 且回填完播 ≥ {WATCH_FLOOR}）**，"
                      f"所以哪类更好——不下结论。先把样本补到 {MIN_SAMPLE} 条，再谈优劣；"
                      f"现在按单条个例改配方，等于赌。", ""]

    # ---- 三、涨粉
    L += ["## 三、哪条涨粉效率高", ""]
    fans = [r for r in rows if (_num(r.get("views")) or 0) > 0 and _num(r.get("follows")) is not None]
    for r in fans:
        r["_fan_per_k"] = round((_num(r.get("follows")) or 0) / max(_num(r.get("views")) or 1, 1) * 1000, 2)
    fans.sort(key=lambda r: -r["_fan_per_k"])
    top_follow = None
    if not rows:
        L += ["本周没有记录，涨粉效率无从谈起——先补一条账再说。", ""]
    elif len(fans) < 3:
        L += [f"样本不够（同时有播放和涨粉数据的只有 {len(fans)} 条，< 3），不下结论。", "",
              "先把 `--views` 与 `--follows` 都回填上——涨粉效率是目前唯一能直接指导「钩子怎么写」的数字。", ""]
    else:
        L += ["| 项目 | 主题 | 播放 | 涨粉 | 每千播放涨粉 | 钩子 |",
              "|---|---|---:|---:|---:|---|"]
        for r in fans[:5]:
            L.append("| " + " | ".join([
                _cell(r.get("project"), 16), _cell(r.get("topic"), 26),
                _num_s(_num(r.get("views"))), _num_s(_num(r.get("follows"))),
                f"{r['_fan_per_k']:g}", _cell(r.get("hook"), 28),
            ]) + " |")
        top_follow = fans[0]
        L += ["", f"**结论**：「{_cell(top_follow.get('topic') or top_follow.get('project'), 30)}」"
                  f"每千播放涨 {top_follow['_fan_per_k']:g} 人，是本周最高的相对值。"
                  f"回看它的结尾 5 秒与人格化表达——涨粉钩子通常不在开头。", ""]

    # ---- 四、建议
    L += ["## 四、下周选题建议", ""]
    L += [f"- {s}" for s in _suggestions(rows, qualified_all, top_follow, start)]
    L += ["", "> 这些建议是**假设**，不是承诺：按下周的建议做片、发布、再记一条账，"
              "看信号是否兑现。连续两轮兑现的组合，才值得固化成你的配比。", ""]
    return "\n".join(L)


def _suggestions(rows: list[dict], qualified_all: list[tuple[str, dict]],
                 top_follow: dict | None, start: date) -> list[str]:
    """具体方向：每条都点名配方 / 变量 / 条数，不写「多发好内容」这种空话。"""
    out: list[str] = []
    n = len(rows)
    if n == 0:
        return [f"本周没有记录：先把 {start.isoformat()} 起发过的每条链接补记进 content-log"
                f"（`content_log.py --add`），这是下周唯一该做的事——没有账，选题只能靠猜。"]

    if qualified_all:
        short, best = qualified_all[0]
        pack = f"`{best['pack']}`" if best.get("pack") else "（记录里没这一项，先补项目真值）"
        plan = f"`{best['plan']}`" if best.get("plan") else "（记录里没这一项，先补项目真值）"
        topics = "、".join(f"「{t}」" for t in best["topics"][:2])
        line = (f"**复制最优配方**：{short}「{best['key']}」本周 {best['n']} 条、"
                f"平均完播 {_pct(best['watch'])}。下周排 2 条同配方——包装包 {pack}、"
                f"节奏 {plan} 一个都不动，**只换钩子文案**（同一套 3 秒开头句式，换题材切口），"
                f"这样才能分清是钩子还是配方在起作用")
        if topics:
            line += f"；题材延续 {topics} 这条线，别换赛道"
        out.append(line + "。")
    else:
        biggest = max((g["n"] for key, _l, _s in DIMS for g in group_stats(rows, key)), default=0)
        max_watch = max((g["n_watch"] for key, _l, _s in DIMS for g in group_stats(rows, key)), default=0)
        if biggest >= MIN_SAMPLE:
            # 条数够了、但完播数据没回填齐：这不是「样本不够」，是「数据没进账」，动作完全不同
            out.append(f"**先把完播数据补齐**：本周 {n} 条，最大的组合已有 {biggest} 条（够 {MIN_SAMPLE} 条线），"
                       f"但回填过完播的只有 {max_watch} 条（< {WATCH_FLOOR}），所以哪类更好算不出来。"
                       f"下周不用改配方：把已发的那几条的 `--avg-watch-pct` 补上（`--update --id …`），"
                       f"再按同包装包、同节奏发 2 条，样本与完播就同时够了。")
        else:
            out.append(f"**锁死配方补样本**：本周 {n} 条，最大的一组只有 {biggest} 条，"
                       f"够不到 {MIN_SAMPLE} 条样本线，什么结论都下不了。"
                       f"下周把包装包与节奏**固定在记录里条数最多的那一档**"
                       f"（先 `content_log.py --list` 看一眼），同一题材连发 {max(MIN_SAMPLE - biggest, 1)} 条，"
                       f"只变钩子——凑到 {MIN_SAMPLE} 条之前，别改整套配方，改了也判不出是谁起的作用。")

    gaps: list[tuple[str, dict]] = []
    for key, label, short in DIMS:
        for g in group_stats(rows, key):
            if 0 < g["n"] < MIN_SAMPLE:
                gaps.append((short, g))
    if gaps:
        gaps.sort(key=lambda x: -x[1]["n"])
        short, g = gaps[0]
        out.append(f"**补样本**：{short}「{g['key']}」只有 {g['n']} 条，判不了。"
                   f"下周至少再发 {MIN_SAMPLE - g['n']} 条同类（同一包装包与节奏，只换题材细节），"
                   f"凑够 {MIN_SAMPLE} 条再判；样本没够就调配比，等于拿噪声当信号。")

    if top_follow:
        hook = top_follow.get("hook")
        topic = top_follow.get("topic") or top_follow.get("project")
        act = (f"**涨粉复刻**：本周每千播放涨粉最高的是「{_cell(topic, 30)}」"
               f"（{top_follow['_fan_per_k']:g} 人/千播放）")
        if hook:
            act += f"，钩子是「{_cell(hook, 40)}」"
        out.append(act + "。下周照它的前 3 秒结构做 1 条：句式沿用，只换题材——"
                         "这是本周唯一有数字支持的涨粉动作。")

    days = sorted({str(r.get("posted_at")) for r in rows if r.get("posted_at")})
    if len(days) < 3:
        out.append(f"**发布节奏**：本周只在 {len(days)} 天有发布（{'、'.join(days) or '无'}）。"
                   f"每周至少 3 天有内容，「每条记录 → 每周复盘」才跑得顺；"
                   f"节奏断了下周样本永远过不了 {MIN_SAMPLE} 条线。")

    stale = [r for r in rows if _num(r.get("views")) is None]
    if stale:
        out.append(f"**先补账**：{len(stale)} 条还没回填播放（`--update --id {stale[0]['id']} --views N …`）。"
                   f"未回填的条目在分组里是「看不见的样本」，补完再看结论要不要改。")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="每周复盘：content-log.jsonl → insights/week-YYYY-MM-DD.md")
    ap.add_argument("--library", default="", help="素材库根（账本与 insights/ 都在这下面）")
    ap.add_argument("--log", default="", help="直接指定 content-log.jsonl 路径")
    ap.add_argument("--week", default="", help="该周任意一天 YYYY-MM-DD（归一为周一）；默认上一自然周")
    ap.add_argument("--out", default="", help="报告输出目录（缺省 <库>/insights）")
    ap.add_argument("--project", default="", help="可选：用某个项目目录反推素材库根")
    a = ap.parse_args()
    try:
        if a.log:
            log = Path(a.log).expanduser()
        else:
            if not a.library and not a.project:
                raise LogError("需要 --library <素材库>（或 --log <content-log.jsonl>）。")
            if a.library:
                lib = Path(a.library).expanduser().resolve()
            else:
                _, lib = resolve_project(a.project, a.library)
            log = lib / LOG_NAME
        if not log.exists():
            raise LogError(
                f"没有 {log}。\n"
                f"  周复盘只认 content-log.jsonl 这一本账——先记一条再来：\n"
                f"  python3 scripts/content_log.py --add --project <项目> --library {log.parent} \\\n"
                f'      --post-url "<发布链接>" --posted-at YYYY-MM-DD')
        records = read_log(log)
        start, end = week_bounds(a.week)
        rows: list[dict] = []
        unparsable = 0
        for rec in latest_by_id(records).values():
            d = _rec_day(rec)
            if d is None:
                unparsable += 1
                continue
            if start <= d <= end:
                rows.append(dict(rec))
        if unparsable:
            print(f"· {unparsable} 条记录的 posted_at 不是 YYYY-MM-DD，已跳过（先修账本那几行）")
        out_dir = Path(a.out).expanduser() if a.out else log.parent / "insights"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"week-{start.isoformat()}.md"
        dest.write_text(build_report(rows, len(latest_by_id(records)), start, end, log),
                        encoding="utf-8")
        print(f"→ {dest}")
        print(f"· {start.isoformat()} ~ {end.isoformat()} 共 {len(rows)} 条")
        if not rows:
            print("· 本周没有记录：报告里写了先补记哪条命令。")
        return 0
    except LogError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
