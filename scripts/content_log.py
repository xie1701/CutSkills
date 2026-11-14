#!/usr/bin/env python3
"""content_log.py——内容循环的账本：一条已发布内容一条记录，落在 <素材库>/content-log.jsonl。

为什么要有这个脚本：insights.py 只吃用户手导的 CSV，出完一份报告就散了——没有落库、
没有周节奏。BRIEF 的「每条发布后记录数据、每周复盘、调整下周选题」缺的正是这一层：
先把「发了什么、带了哪些项目真值、跑了多少」一行一行记下来，review_week.py 才有账可复。

产品边界（与 insights.py 同一条线，不许越界）：
  1. 只记账、只算相对信号，**不承诺任何播放量**；
  2. 数据只来自用户自己从发布后台抄来的数字——本脚本不抓平台、不代运营；
  3. 项目真值能取到才带，取不到写 null，**不许编**（每个字段都留了 truth_src 出处）。

用法：
  # ① 发布后追加一条（项目真值自动带上：片长/包装包/节奏/钩子文案/主题/场景 id）
  python3 scripts/content_log.py --add --project <项目目录或 slug> --library <素材库> \\
      --post-url "https://www.xiaohongshu.com/explore/xxxx" --posted-at 2026-11-09 --platform 小红书

  # ② 回填发布后台数据（同 id 追加一条修订记录；读取时按 id 取最后一条）
  python3 scripts/content_log.py --update --id 20261109-dianzan-a-3f2a1c --library <素材库> \\
      --views 12000 --avg-watch-pct 34% --likes 210 --comments 18 --shares 6 \\
      --saves 25 --follows 12 --retention-3s 62%

  # ③ 人读表格
  python3 scripts/content_log.py --list --library <素材库> [--since 2026-11-01]

落库格式：一行一条 JSON（jsonl）。**追加只增不改**——`--add` 一条，`--update` 追加一条
同 id 的修订版，读取时按 id 取最后一条。谁在什么时候改了什么，翻文件就看得见。
id = 发布日（YYYYMMDD）+ 项目 slug + 链接指纹前 6 位，完全可复算：
同一项目同一链接，不论谁跑、跑几次，id 都是同一个。

时间口径：一律本地时区（naive local time），不用 UTC 裸时间戳。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

LOG_NAME = "content-log.jsonl"
PLATFORMS = ("小红书", "抖音", "视频号")

# 回填字段口径：
#   绝对数（播放/点赞…）非负数字，平台导出里的「1.2万」「12,345」都收；
#   占比（完播占比 / 3 秒留存）归一到 0–1，规则照抄 insights.py 的 _num(pct_col=True)：
#   带 % 直接除 100，裸数 >1.5 按百分数（34 → 0.34），1.0–1.5 之间不可能是有效占比 → 拒收。
ABS_FIELDS = ("views", "likes", "comments", "shares", "saves", "follows")
PCT_FIELDS = ("avg_watch_pct", "retention_3s")
METRIC_FIELDS = ABS_FIELDS + PCT_FIELDS


class LogError(Exception):
    """可读的业务错误：main() 接住后打一行说明、退 2，绝不甩 traceback。"""


# ---------------------------------------------------------------- 地址解析

def _lib_from(start: Path) -> Path | None:
    """沿祖先目录找 `<库>/_cutskills`，命中的那一级就是素材库根。"""
    for up in (start, *start.parents):
        if (up / "_cutskills").is_dir():
            return up
    return None


def resolve_project(project: str, library: str = "") -> tuple[Path, Path]:
    """--project 收「项目目录」或「slug」→ (项目目录, 素材库根)。"""
    raw = Path(project).expanduser()
    if raw.is_dir():
        proj = raw.resolve()
        lib = Path(library).expanduser().resolve() if library else _lib_from(proj)
        if lib is None:
            raise LogError(
                f"「{proj}」不在任何素材库里（往上找不到 _cutskills 目录）。\n"
                f"  账本要落在 <素材库>/content-log.jsonl，请加 --library <素材库根目录>。")
        return proj, lib
    if not library:
        raise LogError(
            f"找不到项目目录「{project}」。\n"
            f"  传项目绝对路径，或用 --library <素材库> + 项目 slug。")
    lib = Path(library).expanduser().resolve()
    proj = lib / "_cutskills" / "projects" / project
    if not proj.is_dir():
        raise LogError(f"{lib} 下没有项目「{project}」（应为 {proj}）。\n"
                       f"  先 init_project.py 建项目，或改用正确的项目路径。")
    return proj, lib


# ---------------------------------------------------------------- 账本读写

def read_log(path: Path) -> list[dict]:
    """读 jsonl → 记录列表。**坏行必须报成人话**：这个文件是发布过的账，不能静默丢。"""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise LogError(f"读不了 {path}：{e.strerror or e}") from None
    except UnicodeDecodeError:
        raise LogError(f"{path} 不是 UTF-8 文本——中间被别的工具改坏了吧？"
                       f"确认里面是 jsonl 再跑。") from None
    out: list[dict] = []
    for i, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except json.JSONDecodeError as e:
            head = s[:60] + ("…" if len(s) > 60 else "")
            raise LogError(
                f"{path} 第 {i} 行不是合法 JSON（{e.msg}，第 {e.colno} 列）。\n"
                f"  这一行开头是：{head}\n"
                f"  修好或删掉那一行再跑——别整份重写，里面是已经发布过的账。") from None
        if not isinstance(rec, dict) or not str(rec.get("id") or "").strip():
            head = s[:60] + ("…" if len(s) > 60 else "")
            raise LogError(
                f"{path} 第 {i} 行不是一条内容记录（缺 id）：{head}\n"
                f"  每条记录至少要带 id / project / post_url / posted_at。") from None
        out.append(rec)
    return out


def latest_by_id(records: list[dict]) -> dict[str, dict]:
    """按 id 取最后一条（`--update` 是追加修订版，最后一条才是现状）。"""
    out: dict[str, dict] = {}
    for rec in records:
        out[str(rec["id"])] = rec
    return out


def append_record(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def now_local() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 派生 id / 归一

def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def make_id(posted_at: str, slug: str, url: str) -> str:
    """时间戳（发布日）+ 项目 slug + 链接指纹 → 稳定、可复算的 id。"""
    digest = hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:6]
    return f"{posted_at.replace('-', '')}-{slug}-{digest}"


def parse_date(raw: str, flag: str) -> str:
    s = (raw or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise LogError(f"{flag} 要的是 YYYY-MM-DD（本地日期），收到「{raw}」。") from None


# ---------------------------------------------------------------- 数字归一

def _scaled(s: str) -> float | None:
    """「1.2万」→ 12000、「12,345」→ 12345、裸数原样；不认识返回 None。"""
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    mul = 1.0
    for unit, k in (("亿", 1e8), ("万", 1e4)):
        if s.endswith(unit):
            s, mul = s[:-1].strip(), k
            break
    try:
        return float(s) * mul
    except ValueError:
        return None


def parse_metric(field: str, raw: str) -> float | int:
    """把一个回填数字洗成账本口径；洗不干净就拒收（宁可报错，不可记错）。"""
    s = (raw or "").strip()
    if not s:
        raise LogError(f"--{field.replace('_', '-')} 是空值；不填就不传这个参数。")
    pct = "%" in s
    v = _scaled(s.replace("%", ""))
    if v is None:
        raise LogError(f"--{field.replace('_', '-')} 收不了「{raw}」：要纯数字（可带「万」「%」）。")
    if field in ABS_FIELDS:
        if v < 0:
            raise LogError(f"--{field.replace('_', '-')} 不能是负数（收到 {raw}）——坏数据不许进账。")
        return int(round(v)) if float(v).is_integer() else round(v, 2)
    # 占比：归一 0–1（与 insights.py 的 pct_col 口径一致）
    if pct:
        v /= 100.0
    elif v > 1.5:                 # 裸「34」按百分数
        v /= 100.0
    elif v > 1.0:                 # 1.0–1.5 之间不可能是有效占比（1%–1.5% 请写成「1.2%」）
        raise LogError(f"--{field.replace('_', '-')} 的「{raw}」分不清是 1.x 还是 1.x%："
                       f"占比只收 0–1 或百分数（如 62%）。")
    if not (0.0 <= v <= 1.0):
        raise LogError(f"--{field.replace('_', '-')} 归一后是 {v}，超出 0–100%：坏数据不许进账。")
    return round(v, 4)


# ---------------------------------------------------------------- 项目真值

def _read_json(path: Path, warns: list[str]) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        warns.append(f"{path.name} 读不了（{e.__class__.__name__}），相关字段记 null")
        return None
    return data if isinstance(data, dict) else None


def _pick_crowd(idea: dict | None) -> str | None:
    """选题档位 A1–A5：取 ideation.json 里被选中的那套提案的 crowd，例如「A1 泛流量破圈（占 50%）」。"""
    if not idea:
        return None
    props = idea.get("proposals") or []
    sel = int(idea.get("selected_proposal") or 1)
    prop = next((p for p in props if p.get("index") == sel), props[0] if props else None)
    if not isinstance(prop, dict):
        return None
    crowd = str(prop.get("crowd") or "").strip()
    if not crowd:
        return None
    return crowd


def project_truth(proj: Path) -> tuple[dict, dict, list[str]]:
    """从项目真值源里取「这条片子是什么」——能取到才带，取不到 null，每个值记出处。"""
    warns: list[str] = []
    tl = _read_json(proj / "timeline.json", warns)
    br = _read_json(proj / "brief.json", warns)
    it = _read_json(proj / "intent.json", warns)
    idea = _read_json(proj / "ideation.json", warns)
    truth: dict = {"duration_s": None, "pack": None, "plan": None, "hook": None,
                   "topic": None, "topic_type": None, "scenes": None}
    src: dict = {}

    def first(field: str, cands) -> None:     # cands: [(出处, 值), ...] 先到先得
        for name, val in cands:
            if val is None or val == "":
                continue
            truth[field], src[field] = val, name
            return

    # 片长：timeline 是成片真值，缺了才退 brief/intent 的计划值
    first("duration_s", [
        ("timeline.json", (tl or {}).get("duration_s")),
        ("brief.json", (br or {}).get("length_s")),
        ("intent.json", (it or {}).get("duration_s")),
    ])
    if isinstance(truth["duration_s"], (int, float)):
        truth["duration_s"] = round(float(truth["duration_s"]), 2)

    first("pack", [("timeline.json", (tl or {}).get("pack")),
                   ("brief.json", (br or {}).get("pack")),
                   ("intent.json", (it or {}).get("pack"))])
    # 节奏＝剪辑骨架 plan（S1-conclude-fast 这类），timeline 里就是成片用的那套
    first("plan", [("timeline.json", (tl or {}).get("plan")),
                   ("brief.json", (br or {}).get("plan")),
                   ("intent.json", (it or {}).get("plan"))])

    # 钩子文案＝顶部标题条的 top｜main（timeline 是成片真值；没渲过才退 ideation 提案）
    tb = (tl or {}).get("title_bar") or {}
    hook = "｜".join(str(tb.get(k)).strip() for k in ("top", "main") if str(tb.get(k) or "").strip())
    if hook:
        truth["hook"], src["hook"] = hook, "timeline.json"
    else:
        props = (idea or {}).get("proposals") or []
        sel = int((idea or {}).get("selected_proposal") or 1)
        prop = next((p for p in props if p.get("index") == sel), props[0] if props else None)
        tb2 = (prop or {}).get("title_bar") or {}
        hook2 = "｜".join(str(tb2.get(k)).strip() for k in ("top", "main")
                          if str(tb2.get(k) or "").strip())
        if hook2:
            truth["hook"], src["hook"] = hook2, "ideation.json"

    # 主题：先用户选题原话（ideation），再项目意图原话（intent），最后 brief 的「看完改变什么」
    first("topic", [("ideation.json", (idea or {}).get("topic_raw")),
                    ("intent.json", (it or {}).get("raw")),
                    ("brief.json", (br or {}).get("change"))])
    # 题材＝内容档位（A1–A5，references/topic-system.md 的 5A 分类），只在 ideation 选过提案时才有
    crowd = _pick_crowd(idea)
    if crowd:
        truth["topic_type"], src["topic_type"] = crowd, "ideation.json"

    # 场景 id：项目 scenes/*.json 的任务单 + timeline 里真正画出来的 gen 元素
    scenes: set[str] = set()
    sdir = proj / "scenes"
    if sdir.is_dir():
        scenes.update(p.stem for p in sdir.glob("*.json"))
    for el in (tl or {}).get("elements") or []:
        props = (el or {}).get("props") or {}
        for k in ("scene", "scene_spec"):
            if props.get(k):
                scenes.add(str(props[k]))
    if scenes:
        truth["scenes"] = sorted(scenes)
        src["scenes"] = "scenes/ + timeline.json"
    return truth, src, warns


# ---------------------------------------------------------------- 输出排版

def _disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _cut(s: str, width: int) -> str:
    out, w = "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if w + cw > width:
            return out + "…" if len(out) < len(s) else out
        out, w = out + c, w + cw
    return out


def _pad(s: str, width: int) -> str:
    s = str(s)
    pad = width - _disp_width(s)
    return s + " " * max(pad, 0)


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and not float(v).is_integer():
        return f"{v:g}"
    return f"{int(v)}"


def _fmt_pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


# ---------------------------------------------------------------- 三个动作

def cmd_add(a: argparse.Namespace) -> int:
    proj, lib = resolve_project(a.project, a.library)
    log = Path(a.log).expanduser() if a.log else lib / LOG_NAME
    posted = parse_date(a.posted_at, "--posted-at")
    url = (a.post_url or "").strip()
    if not url:
        raise LogError("--post-url 不能是空的：账本靠链接定位哪一条发布。")
    platform = (a.platform or "").strip() or None
    if platform and platform not in PLATFORMS:
        raise LogError(f"--platform 只收 {' / '.join(PLATFORMS)}，收到「{platform}」。")

    slug = proj.name
    rid = make_id(posted, slug, url)
    known = latest_by_id(read_log(log))
    for prev_id, rec in known.items():
        if (str(rec.get("project") or "") == slug
                and normalize_url(str(rec.get("post_url") or "")) == normalize_url(url)):
            raise LogError(
                f"重复：{slug} 的这个链接已经记过了（id {prev_id}，发布日 {rec.get('posted_at')}）。\n"
                f"  回填数据用：content_log.py --update --id {prev_id} …\n"
                f"  同一个项目发了第二条、链接不同，那就换链接再加。")

    truth, src, warns = project_truth(proj)
    for w in warns:
        print(f"· {w}")
    if not any(truth[k] is not None for k in ("duration_s", "pack", "plan", "hook", "topic")):
        print(f"· {proj.name} 里没有 timeline/brief/intent/ideation："
              f"片长/包装包/节奏/钩子/主题一律记 null（不编）")

    rec = {
        "version": 1,
        "id": rid,
        "project": slug,
        "post_url": url,
        "posted_at": posted,
        "platform": platform,
        "topic": truth["topic"],
        "topic_type": truth["topic_type"],
        "hook": truth["hook"],
        "duration_s": truth["duration_s"],
        "pack": truth["pack"],
        "plan": truth["plan"],
        "scenes": truth["scenes"],
        "views": None,
        "avg_watch_pct": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "follows": None,
        "retention_3s": None,
        "created": now_local(),
        "updated": None,
        "truth_src": src,
    }
    append_record(log, rec)
    print(f"→ {log}")
    print(f"· id {rid}")
    print(f"· 片长 {_fmt_num(truth['duration_s'])}s · 包装包 {truth['pack'] or 'null'} · "
          f"节奏 {truth['plan'] or 'null'} · 题材 {truth['topic_type'] or 'null'}")
    print(f"· 钩子 {truth['hook'] or 'null'}")
    print(f"· 主题 {truth['topic'] or 'null'}")
    print(f"· 场景 {'、'.join(truth['scenes']) if truth['scenes'] else 'null'}")
    print(f"· 数据还是空的：发布后台数据回来后用 --update --id {rid} … 回填。")
    return 0


def cmd_update(a: argparse.Namespace) -> int:
    if a.log:
        log = Path(a.log).expanduser()
        lib = log.parent
    else:
        if not a.library:
            raise LogError("--update 需要 --library <素材库>（或 --log <content-log.jsonl> 直接指路径）。")
        lib = Path(a.library).expanduser().resolve()
        log = lib / LOG_NAME
    known = latest_by_id(read_log(log))
    rid = (a.id or "").strip()
    if rid not in known:
        if not known:
            raise LogError(f"{log} 里还没有任何记录。先 --add 记一条，再回填数据。")
        recent = sorted(known.values(), key=lambda r: str(r.get("posted_at") or ""), reverse=True)[:5]
        avail = "\n".join(f"  - {r['id']}（{r.get('project')} · {r.get('posted_at')}）" for r in recent)
        raise LogError(f"没有 id={rid} 的记录。最近的几条是：\n{avail}\n"
                       f"  用 --list 看全表。")
    given = {f: getattr(a, f) for f in METRIC_FIELDS if getattr(a, f) is not None}
    if not given:
        raise LogError("至少给一个数据参数：" + " / ".join("--" + f.replace("_", "-") for f in METRIC_FIELDS))

    prev = known[rid]
    rec = {k: v for k, v in prev.items()}
    for f, raw in given.items():
        rec[f] = parse_metric(f, raw)
    rec["updated"] = now_local()
    append_record(log, rec)
    filled = {f: rec[f] for f in METRIC_FIELDS if rec.get(f) is not None}
    print(f"→ {log}（id {rid} 追加修订记录）")
    print("· " + " · ".join(f"{f}={_fmt_pct(v) if f in PCT_FIELDS else _fmt_num(v)}"
                            for f, v in filled.items()))
    missing = [f for f in METRIC_FIELDS if rec.get(f) is None]
    if missing:
        print("· 还没回填：" + " / ".join("--" + f.replace("_", "-") for f in missing))
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    if a.log:
        log = Path(a.log).expanduser()
    else:
        if not a.library:
            raise LogError("--list 需要 --library <素材库>（或 --log <content-log.jsonl> 直接指路径）。")
        log = Path(a.library).expanduser().resolve() / LOG_NAME
    records = list(latest_by_id(read_log(log)).values())
    if a.since:
        since = parse_date(a.since, "--since")
        records = [r for r in records if str(r.get("posted_at") or "") >= since]
    records.sort(key=lambda r: (str(r.get("posted_at") or ""), str(r.get("id") or "")), reverse=True)
    print(f"# {log} · {len(records)} 条")
    if not records:
        print("（还没有记录。发布后用 --add 记一条。）")
        return 0
    widths = [("发布日", 10), ("项目", 16), ("平台", 8), ("播放", 9),
              ("完播", 7), ("涨粉", 6), ("id", 28), ("链接", 38)]
    print("  ".join(_pad(n, w) for n, w in widths))
    print("  ".join("-" * w for _, w in widths))
    for r in records:
        cells = [
            str(r.get("posted_at") or "—"),
            _cut(str(r.get("project") or "—"), 16),
            str(r.get("platform") or "—"),
            _fmt_num(r.get("views")),
            _fmt_pct(r.get("avg_watch_pct")),
            _fmt_num(r.get("follows")),
            str(r["id"]),
            _cut(str(r.get("post_url") or "—"), 38),
        ]
        print("  ".join(_pad(c, w) for c, (_, w) in zip(cells, widths)))
    no_views = [r["id"] for r in records if r.get("views") is None]
    if no_views:
        print(f"\n· {len(no_views)} 条还没回填播放（--update --id <id> --views N）：{no_views[0]}"
              + (" …" if len(no_views) > 1 else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="内容循环账本：一条已发布内容一条记录（<素材库>/content-log.jsonl）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--add", action="store_true", help="追加一条刚发布的内容")
    g.add_argument("--update", action="store_true", help="回填发布后台数据（按 id 追加修订）")
    g.add_argument("--list", action="store_true", help="人读表格")
    ap.add_argument("--library", default="", help="素材库根（账本落 <库>/content-log.jsonl）")
    ap.add_argument("--log", default="", help="直接指定 content-log.jsonl 路径（缺省按库推）")
    ap.add_argument("--project", default="", help="--add：项目目录或 slug")
    ap.add_argument("--post-url", default="", help="--add：发布后的链接")
    ap.add_argument("--posted-at", default="", help="--add：发布日 YYYY-MM-DD（本地日期）")
    ap.add_argument("--platform", default="", help="--add：" + " / ".join(PLATFORMS))
    ap.add_argument("--id", default="", help="--update：要回填的记录 id（--list 可查）")
    ap.add_argument("--since", default="", help="--list：只看这天及以后发布的")
    ap.add_argument("--views", default=None, help="播放量")
    ap.add_argument("--avg-watch-pct", dest="avg_watch_pct", default=None,
                    help="平均观看时长占比（34%% 或 0.34）")
    ap.add_argument("--likes", default=None, help="点赞")
    ap.add_argument("--comments", default=None, help="评论")
    ap.add_argument("--shares", default=None, help="分享")
    ap.add_argument("--saves", default=None, help="收藏")
    ap.add_argument("--follows", default=None, help="涨粉")
    ap.add_argument("--retention-3s", dest="retention_3s", default=None,
                    help="3 秒留存率（62%% 或 0.62）")
    a = ap.parse_args()
    try:
        if a.add:
            if not a.project or not a.post_url or not a.posted_at:
                raise LogError("--add 需要 --project、--post-url、--posted-at 三样齐全。")
            return cmd_add(a)
        if a.update:
            if not a.id:
                raise LogError("--update 需要 --id（先 --list 查 id）。")
            return cmd_update(a)
        return cmd_list(a)
    except LogError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
