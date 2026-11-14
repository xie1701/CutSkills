#!/usr/bin/env python3
"""content_calendar.py——按原文三阶段配比生成 4 周内容日历（calendar.md 人读 + calendar.json 机读）。

唯一方法来源是 `references/content-system.md`，本脚本**不抄数字、只抄原文**：
  阶段一/二/三的配比、5A 五档、六大话题方向、公式与切入点，全部运行时从原文里解析出来。
原文没给的（阶段与周次怎么对应、每周几条、哪天发），一律标「原文未给出」+「自建扩展，非原文」，
不许估算完当原文用。

双轨（门店轨 / 博主轨）由 `track.py` 判出，读 `<项目>/account.json`：
  影响 **选题方向、钩子偏好、成功指标口径**；
  **不影响配比数字**——原文三阶段配比是唯一真值，两条轨用的是同一张配比表。

每条内容必须带：周次、建议发布日、A 层级（A1–A5 原文表述）、话题类型（原文六大话题方向之一）、
钩子类型（`ideate.HOOK_BANK` 的 id + 出处）、选题一句话、用了原文哪一节/哪个公式或切入点。

用法：
  content_calendar.py --project <项目目录>                       # 默认 4 周、下周一开跑
  content_calendar.py --project <项目目录> --weeks 4 --start 2026-11-17 --per-week 4
  content_calendar.py --project <项目目录> --track 博主           # 临时按另一条轨出（不写 account.json）

退出码：0 写成 / 2 用法错 / 3 缺输入（没有 account.json 或原文解析不到配比，给可读提示不崩）
"""
from __future__ import annotations

# 文件名本来叫 `calendar.py`，与标准库撞名：任何走 `datetime.strptime` → `_strptime` →
# `import calendar` 的脚本，都会按 sys.path 命中本文件、把标准库顶掉，报
# 「module 'calendar' has no attribute 'day_abbr'」。当时加了一段「被误 import 就把标准库
# 顶回去」的守卫——那是拿胶带粘窗户。2026-11-15 直接改名 `content_calendar.py`，
# 整类问题连同守卫一起消失。别再叫回 `calendar.py`。

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE_MD = ROOT / "references" / "content-system.md"
sys.path.insert(0, str(HERE))
from track import TRACK_SPEC                       # noqa: E402  双轨成功口径只此一份
from ideate import HOOK_BANK, hook_source, _extract_core   # noqa: E402  钩子库与切片器只此一份

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
# 每周几条、哪几天发：原文未给出（原文只给配比，没给发布频率）→ 自建扩展，非原文
WEEKDAY_PLAN = {1: (0,), 2: (0, 3), 3: (0, 2, 4), 4: (0, 2, 4, 6),
                5: (0, 1, 2, 4, 6), 6: (0, 1, 2, 3, 4, 6), 7: (0, 1, 2, 3, 4, 5, 6)}
DEFAULT_WEEKS = 4
DEFAULT_PER_WEEK = 4
# 三阶段摊到 W 周上的权重：原文未给出 → 自建扩展（40/30/30 让阶段一多铺一周拉新）
STAGE_WEEK_WEIGHTS = (40, 30, 30)

STAGE_PLAN_NOTE = ("自建扩展，非原文：原文『内容配比』一节只给了三阶段的配比数字，"
                   "**没给**「第几周属于哪个阶段」「每阶段跑几周」「切换条件」。"
                   "本仓按阶段一 40% / 阶段二 30% / 阶段三 30% 的周数权重摊周（最大余数取整），"
                   "阶段内按原文比例摊条数。")
RATIO_NOTE = ("自建扩展，非原文：原文只给百分比，没给「每种内容发几条」。"
              "阶段内条数用最大余数法（Hamilton）取整，保证每档偏差 < 1 条；"
              "条数越多越贴近原文比例（每阶段条数是 10 的整数倍时与原文完全相等）。")
TRACK_SCOPE_NOTE = ("双轨只改**选题方向、钩子偏好与成功指标口径**，"
                    "**不改配比数字**——三阶段配比是原文唯一真值，两条轨共用同一张表。")

# 每档位在两条轨上分别取哪些钩子（顺序即首选顺序）；id 全部是 ideate.HOOK_BANK 里的，
# 不另造一套。门店轨偏痛点/说服/售后，博主轨偏好奇/颠覆/泛人群。
TRACK_TIER_HOOKS = {
    "门店": {
        "A1": ["H05", "H04", "H01", "H07"],
        "A2": ["H06", "H12", "H10", "H04"],
        "A3": ["H02", "H13", "H14", "H03", "H08"],
        "A4": ["H11", "H06", "H15", "H04"],
        "A5": ["H13", "H03", "H01"],
    },
    "博主": {
        "A1": ["H05", "H07", "H01", "H04", "H08"],
        "A2": ["H06", "H10", "H04", "H12"],
        "A3": ["H03", "H08", "H02", "H13"],
        "A4": ["H15", "H11", "H04"],
        "A5": ["H13", "H03", "H01"],
    },
}
# 六大话题方向的取用顺序（名称必须真是原文解析出来的那六条）
TRACK_TOPIC_PREF = {
    "门店": ["身份话题", "认知话题", "内幕话题", "情绪话题", "争议话题", "社会话题"],
    "博主": ["社会话题", "情绪话题", "争议话题", "身份话题", "认知话题", "内幕话题"],
}
# 选题一句话：本仓按原文构造式写的话术（自建文案，非原文原句），门店/博主两套偏好
IDEA_BANK = {
    "门店": {
        "A1": ["最近同城都在传的那件事，我们店也遇上了：说清{core}的真相",
               "在本地做{core}，先给你看5秒现场",
               "这条街上关于{core}的老说法，我听了十几年",
               "同城最近在传的那件事，跟我们这行有什么关系，我一条条说",
               "进店的人最近都在问同一个问题：{core}到底怎么样"],
        "A2": ["我开这家店这些年，为什么还在死磕{core}",
               "做{core}我踩过的最大一个坑，今天讲给你听",
               "怎么分辨{core}好不好，我一句话教你",
               "我为什么偏偏选在这个位置开{core}，说个实在的",
               "开这家店之前，我是做什么的（跟{core}有关）"],
        "A3": ["{core}到底值不值这个价，我把成本拆开给你看",
               "客人问得最多的三个问题：人均多少、怎么预约、能不能退",
               "在我们店吃了三年的客人，是怎么评价{core}的",
               "来之前最该问的三个问题：{core}怎么挑、怎么算、怎么退",
               "同样的{core}，为什么这一家贵二十块：把账算给你看"],
        "A4": ["上次店里出的那个问题，我们是怎么补回来的",
               "这周有客人反馈{core}，我把处理过程全说了",
               "谢谢还愿意回来的老客：{core}出问题我们认"],
        "A5": ["这周{core}的新品只先给老客看",
               "评论区喊过我名字的人，这周{core}有福利",
               "把你们当精神股东：{core}下一步做什么，你们定"],
    },
    "博主": {
        "A1": ["为什么最近所有人都在聊{core}，我扒了三个原因",
               "关于{core}，我发现了一个反常识的结论",
               "{core}这件事，普通人最容易理解错的一点",
               "刷屏的那个说法，跟{core}的真相差得有点远",
               "十条视频里九条说错了{core}这件事"],
        "A2": ["做{core}这几年，我最后悔的一步是哪一步",
               "我为什么开始做{core}，说实话有点丢人",
               "没人告诉你的{core}入门常识",
               "我做{core}的第一条视频长什么样，现在回看全是毛病",
               "这三年我踩过的三个坑，{core}新手一个都别踩"],
        "A3": ["{core}的三种做法，为什么我只推荐第二种",
               "{core}怎么判断好坏，教你一招自己看",
               "被问了一百遍：{core}到底要花多少时间",
               "{core}到底该怎么入门：从零到第一条视频",
               "被问最多的三个{core}问题，一次说清"],
        "A4": ["上条视频被问爆了，这次把{core}的后续交代清楚",
               "按我说的做没效果的，问题多半在这一步",
               "评论区的问题我一条条回：{core}最容易翻车的细节"],
        "A5": ["关注我的人，这周{core}的资料我放在评论区",
               "{core}第二期来了，这次按你们的投票做",
               "感谢你们把我顶到这个数：{core}下一期你们说"],
    },
}
TRACK_ANGLE = {
    "门店": "门店轨：选题偏垂直说服与售后关怀，钩子偏痛点/数字/身份推荐，"
            "结尾导「评论区问价 + 私信预约到店」",
    "博主": "博主轨：选题偏泛流量与人设，钩子偏好奇/颠覆/人群，"
            "结尾导「评论 + 关注」，看 3 秒留存与涨粉",
}


# ---------------------------------------------------------------- 原文解析

HEAD_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")


class CalError(Exception):
    """可读的业务错误：main() 接住打一行说明、退 2/3，绝不甩 traceback。"""


def parse_sections(md: str) -> list[dict]:
    """切成 [{level, title, body:[行]}]，body 只含该标题下的直属行（子标题各自成条）。"""
    rows: list[dict] = []
    cur: dict | None = None
    for ln in md.splitlines():
        m = HEAD_RE.match(ln)
        if m:
            cur = {"level": len(m.group(1)), "title": m.group(2).strip(), "body": []}
            rows.append(cur)
        elif cur is not None:
            cur["body"].append(ln)
    return rows


def body_of(rows: list[dict], title: str) -> list[str]:
    for r in rows:
        if r["title"] == title:
            return r["body"]
    return []


def load_source(root: Path | None = None) -> tuple[str, list[dict]]:
    md_p = Path(root) / "references" / "content-system.md" if root else SOURCE_MD
    try:
        md = md_p.read_text(encoding="utf-8")
    except OSError as e:
        raise CalError(f"读不到原文（{md_p}）：{e}") from e
    return md, parse_sections(md)


A5_RE = re.compile(r"^\s*-\s*A([1-5])\s*[（(](.+?)[）)]\s*$")
CROWD_RE = re.compile(r"(\S+?人群)\s+(\S+?型内容)")
TIER_RE = re.compile(r"([\u4e00-\u9fa5]+?型内容)\s*(\d+)\s*%")


def parse_5a(rows: list[dict]) -> dict[str, dict]:
    """原文『内容配比』一节 · 5A人群：A1–A5 的人群名与内容类型（原文表述，不改写）。"""
    out: dict[str, dict] = {}
    for ln in body_of(rows, "5A人群"):
        m = A5_RE.match(ln)
        if not m:
            continue
        raw = m.group(2).strip()
        mm = CROWD_RE.search(raw)
        out[f"A{m.group(1)}"] = {
            "crowd": mm.group(1) if mm else "",
            "type": mm.group(2) if mm else "",
            "text": f"A{m.group(1)}（{raw}）",
        }
    if not out:
        raise CalError("原文『内容配比』一节 · 5A人群 解析不到 A1–A5，"
                       "检查 references/content-system.md 是否被改动")
    return out


def parse_stages(rows: list[dict]) -> list[dict]:
    """原文『内容配比』一节 · 阶段一/二/三：原句里的配比，一位不差地解析出来。"""
    out = []
    for name in ("阶段一", "阶段二", "阶段三"):
        body = body_of(rows, name)
        line = next((ln.strip() for ln in body if "%" in ln), "")
        tiers = [{"type": m.group(1), "pct": int(m.group(2))}
                 for m in TIER_RE.finditer(line)]
        if not tiers:
            raise CalError(f"原文『内容配比』一节 · {name} 解析不到配比（读到：{line or '空'}）")
        if sum(t["pct"] for t in tiers) != 100:
            raise CalError(f"原文 · {name} 配比加起来不是 100%（读到：{line}）")
        out.append({"name": name, "ref": f"原文『内容配比』一节 · {name}",
                    "raw": line, "tiers": tiers})
    return out


def parse_content_forms(rows: list[dict]) -> dict[str, list[str]]:
    """原文『内容配比』一节 · 内容参考：每档原文列的内容形态（热点话题/老板口播/…）。"""
    forms: dict[str, list[str]] = {}
    cur = ""
    for ln in body_of(rows, "内容参考"):
        m = re.match(r"^-\s*A([1-5])\s*$", ln)
        if m:
            cur = f"A{m.group(1)}"
            forms[cur] = []
            continue
        m2 = re.match(r"^ {2}-\s*(\S.*)$", ln)
        if m2 and cur:
            forms[cur].append(m2.group(1).strip())
    return forms


def parse_topics(rows: list[dict]) -> list[dict]:
    """原文『内容创作』一节 · 漏斗流视频 · 六大话题选题方向。"""
    out: list[dict] = []
    inside = False
    for ln in body_of(rows, "漏斗流视频"):
        if re.match(r"^-\s*六大话题选题方向", ln):
            inside = True
            continue
        if not inside:
            continue
        if re.match(r"^-\s*\S", ln):
            break
        m = re.match(r"^ {2}-\s*(\S+)\s*$", ln)
        if m:
            out.append({"name": m.group(1), "construct": "", "cands": []})
            continue
        m2 = re.match(r"^ {4}-\s*(\S.*)$", ln)
        if m2 and out:
            out[-1]["cands"].append(m2.group(1).strip())
    if not out:
        raise CalError("原文『内容创作』一节 · 漏斗流视频 · 六大话题选题方向 解析不到任何方向")
    for t in out:
        cands = t.pop("cands", [])
        # 构造式优先取原文里带「=选题」的那句（那才是构造式，其余是举例）
        t["construct"] = next((c for c in cands if "=选题" in c), cands[0] if cands else "")
    return out


def parse_formulas(rows: list[dict]) -> dict[str, list[str]]:
    """原文『内容创作』一节 · 漏斗流视频公式 / 水桶流视频公式（原句）。"""
    out: dict[str, list[str]] = {}
    for sec, key in (("漏斗流视频", "漏斗流视频公式"), ("水桶流视频", "水桶流视频公式")):
        got: list[str] = []
        inside = False
        for ln in body_of(rows, sec):
            if re.match(rf"^-\s*{key}\s*$", ln):
                inside = True
                continue
            if not inside:
                continue
            if re.match(r"^-\s*\S", ln):
                break
            m = re.match(r"^ {2}-\s*(\S.*)$", ln)
            if m:
                got.append(m.group(1).strip())
        out[key] = got
    return out


# ---------------------------------------------------------------- 配比与铺排

def allocate(total: int, pcts: list[int]) -> list[int]:
    """最大余数法（Hamilton）：条数为整数，且每档与原文比例的偏差 < 1 条。"""
    base, rems = [], []
    for pct in pcts:
        num = total * pct
        base.append(num // 100)
        rems.append(num % 100)
    left = total - sum(base)
    order = sorted(range(len(pcts)), key=lambda i: (-rems[i], i))
    for i in order[:left]:
        base[i] += 1
    return base


def split_weeks(weeks: int, n_stages: int) -> list[int]:
    """三阶段各占几周（原文未给出 → 自建权重 + 每阶段至少一周）。"""
    alloc = allocate(weeks, list(STAGE_WEEK_WEIGHTS[:n_stages]))
    # 周数够多却没分到周的阶段：从最大的那个挪一周，保证每阶段都跑得到
    while weeks >= n_stages and min(alloc) == 0:
        i = alloc.index(max(alloc))
        alloc[i] -= 1
        alloc[alloc.index(0)] += 1
    return alloc


def interleave(layers: list[str], counts: dict[str, int]) -> list[str]:
    """阶段内把档位摊开（轮转），不把 4 条泛流量堆在同一周。"""
    left = {k: v for k, v in counts.items() if v > 0}
    out: list[str] = []
    order = [k for k in layers if k in left]
    while any(left.values()):
        for k in order:
            if left.get(k, 0) > 0:
                out.append(k)
                left[k] -= 1
    return out


def next_monday(today: date) -> date:
    delta = (7 - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


CORE_PREFIX = re.compile(r"^(我想做|帮我做|帮我|关于|拍一条|做一条|聊聊|讲讲)")
CORE_SUFFIX = re.compile(r"(的视频|短视频|口播|脚本|选题|讲讲|聊聊)$")


def _clip_core(text: str) -> str:
    """把一句原话压成选题核心（钩子/标题里要能直接嵌进去，不能整句照搬）。

    三步：去动词头尾 → 只取第一个分句 → 太长就借 ideate 抓中心语，再砍掉「为什么/怎么…」
    之后的部分，把一句「看完知道我们的羊排为什么值得排队」压成「羊排」。
    """
    t = CORE_PREFIX.sub("", str(text or "").strip())
    t = CORE_SUFFIX.sub("", t).strip(" ，。！？、")
    clause = re.split(r"[，。；！？、,.;!?]", t)[0].strip(" ，。！？、")
    if not clause:
        return "这件事"
    if len(clause) > 12:
        try:
            clause = _extract_core(clause) or clause
        except Exception:
            clause = clause[:12]
    head = re.split(r"(为什么|怎么|到底|如何|是什么)", clause)[0].strip(" ，。！？、")
    while True:                     # 「一条关于瓷砖」这种叠着的口头前缀要连着剥干净
        stripped = re.sub(r"^(一条|一支|一个|一期|关于|讲讲|说说)",
                          "", head).strip(" ，。！？、的")
        if stripped == head:
            break
        head = stripped
    core = head if len(head) >= 2 else clause
    return (core or "这件事")[:16]


def _core_of(proj: Path, explicit: str = "") -> tuple[str, str]:
    """选题核心（{core}）：--topic > ideation.json > brief.json > intent.json，一律压成短句。"""
    if explicit.strip():
        return _clip_core(explicit), "--topic"
    for name, fields in (("ideation.json", ("core", "topic_raw")),
                         ("intent.json", ("promise", "raw")),
                         ("brief.json", ("change",))):
        p = proj / name
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for f in fields:
            v = str(d.get(f) or "").strip()
            if v:
                return _clip_core(v), f"{name}:{f}"
    return "这件事", "默认值"


# ---------------------------------------------------------------- 生成

def _hook_for(layer: str, track: str, idx: int) -> dict:
    pool = list((TRACK_TIER_HOOKS.get(track) or {}).get(layer) or [])
    hid = pool[idx % len(pool)] if pool else "H01"
    hk = HOOK_BANK[hid]
    src = hk.get("source") or {}
    return {
        "id": hid,
        "name": hk["name"],
        "self_built": bool(src.get("self_built")),
        "source": hook_source(hk),
        "formula": hk.get("formula") or "",
    }


def _ref_str(sec: str) -> str:
    """「内容创作 · 水桶流视频 · 水桶流视频公式」→「原文『内容创作』一节 · 水桶流视频 · 水桶流视频公式」。"""
    parts = [p for p in sec.split(" · ") if p]
    return f"原文『{parts[0]}』一节" + (" · " + " · ".join(parts[1:]) if len(parts) > 1 else "")


def _metrics_of(account: dict | None, track: str) -> list[str]:
    """成功指标只认这一条轨的口径（两条轨口径不同，不许互相顶替）：
    account.json 里有就用它，没有（--track 临时指定、还没跑 track.py）就用 track.py 的 BRIEF 口径。"""
    acc = account or {}
    got = ((acc.get("tracks") or {}).get(track) or {}).get("success_metrics")
    if got:
        return list(got)
    if acc.get("track") == track and acc.get("success_metrics"):
        return list(acc["success_metrics"])
    return list((TRACK_SPEC.get(track) or {}).get("success_metrics") or [])


def build_calendar(proj: Path, *, track: str, weeks: int, per_week: int,
                   start: date, core: str = "", core_src: str = "",
                   account: dict | None = None) -> dict:
    _, rows = load_source()
    five_a = parse_5a(rows)
    stages = parse_stages(rows)
    forms = parse_content_forms(rows)
    topics_src = parse_topics(rows)
    formulas = parse_formulas(rows)
    layer_of_type = {v["type"]: k for k, v in five_a.items() if v["type"]}

    topic_constructs = {t["name"]: t["construct"] for t in topics_src}
    topic_names = [t["name"] for t in topics_src]
    pref = [n for n in (TRACK_TOPIC_PREF.get(track) or []) if n in topic_names]
    topic_cycle = pref + [n for n in topic_names if n not in pref]
    if not topic_cycle:
        raise CalError("原文六大话题方向与双轨偏好表对不上，检查 references/content-system.md")

    week_split = split_weeks(weeks, len(stages))
    stage_weeks: list[list[int]] = []
    wk = 1
    for n in week_split:
        stage_weeks.append(list(range(wk, wk + n)))
        wk += n

    posts: list[dict] = []
    stage_report: list[dict] = []
    n = 0
    for st, wlist in zip(stages, stage_weeks):
        layers = [layer_of_type.get(t["type"], "") for t in st["tiers"]]
        if "" in layers:
            raise CalError(f"{st['ref']} 里的「{st['tiers'][layers.index('')]['type']}」"
                           f"在 5A人群 里找不到对应档位，原文可能被改动")
        total = per_week * len(wlist)
        counts = dict(zip(layers, allocate(total, [t["pct"] for t in st["tiers"]])))
        seq = interleave(layers, counts)
        rep = {
            "name": st["name"], "ref": st["ref"], "raw": st["raw"],
            "weeks": wlist, "posts": total,
            "ratio": [{"type": t["type"], "pct": t["pct"], "layer": layers[i]}
                      for i, t in enumerate(st["tiers"])],
            "counts": [{"type": t["type"], "layer": layers[i],
                        "count": counts[layers[i]],
                        "expected": round(total * t["pct"] / 100, 3),
                        "deviation": round(abs(counts[layers[i]] - total * t["pct"] / 100), 3)}
                       for i, t in enumerate(st["tiers"])],
        }
        stage_report.append(rep)

        for wi, wnum in enumerate(wlist):
            base = start + timedelta(days=7 * (wnum - 1))
            for k in range(per_week):
                layer = seq[wi * per_week + k]
                spec = five_a[layer]
                topic = topic_cycle[n % len(topic_cycle)]
                day = base + timedelta(days=WEEKDAY_PLAN[per_week][k])
                layer_idx = sum(1 for p in posts if p["a_layer"] == layer)
                hook = _hook_for(layer, track, layer_idx)
                if layer == "A1":
                    fx = formulas.get("漏斗流视频公式") or []
                    formula = fx[-1] if fx else ""
                    sec = "内容创作 · 漏斗流视频 · 漏斗流视频公式"
                else:
                    fx = formulas.get("水桶流视频公式") or []
                    formula = fx[0] if fx else ""
                    sec = "内容创作 · 水桶流视频 · 水桶流视频公式"
                form_pool = forms.get(layer) or []
                form = form_pool[n % len(form_pool)] if form_pool else ""
                idea_tpl = IDEA_BANK[track][layer]
                idea = idea_tpl[layer_idx % len(idea_tpl)].format(core=core)
                posts.append({
                    "n": n + 1,
                    "week": wnum,
                    "date": day.isoformat(),
                    "weekday": WEEKDAYS[day.weekday()],
                    "stage": st["name"],
                    "stage_ref": st["ref"],
                    "a_layer": layer,
                    "a_layer_text": spec["text"],
                    "content_type": spec["type"],
                    "topic_type": topic,
                    "topic_construct": topic_constructs.get(topic, ""),
                    "topic_ref": f"原文『内容创作』一节 · 漏斗流视频 · 六大话题选题方向 · {topic}",
                    "content_form": form,
                    "content_form_ref": f"原文『内容配比』一节 · 内容参考 · {layer}",
                    "hook": hook,
                    "idea": idea,
                    "basis": _ref_str(sec) + (f"（{formula}）" if formula else ""),
                    "basis_parts": {
                        "section": sec,
                        "formula": formula,
                        "hook": hook["source"],
                        "topic": f"六大话题选题方向 · {topic}",
                        "ratio": st["ref"],
                    },
                    "track_angle": TRACK_ANGLE[track],
                })
                n += 1

    doc = {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "project": str(proj),
        "source": "references/content-system.md",
        "source_abs": str(SOURCE_MD),
        "core": core,
        "core_source": core_src,
        "track": track,
        "track_scope_note": TRACK_SCOPE_NOTE,
        "success_metrics": _metrics_of(account, track),
        "success_metrics_source": ("account.json（track.py 判定）" if (account or {}).get("track")
                                   else "track.py 内置 BRIEF 口径（本目录还没有 account.json）"),
        "account": {k: (account or {}).get(k) for k in
                    ("track", "track_label", "confirmed", "decided_by", "success_metrics")},
        "weeks": weeks,
        "per_week": per_week,
        "start": start.isoformat(),
        "stage_week_weights": list(STAGE_WEEK_WEIGHTS[:len(stages)]),
        "stage_plan_note": STAGE_PLAN_NOTE,
        "allocation_note": RATIO_NOTE,
        "stages": stage_report,
        "posts": posts,
    }
    return doc


# ---------------------------------------------------------------- 校验（测试也吃这一份判据）

BULLET = r"^\s*-\s*{name}\s*$"


def source_anchor_errors(hook_id: str, rows: list[dict]) -> list[str]:
    """钩子出处必须能在原文里点到：章节标题是真标题，切入点名真是那一节下的条目。"""
    errs: list[str] = []
    hk = HOOK_BANK.get(hook_id)
    if not hk:
        return [f"{hook_id} 不在 ideate.HOOK_BANK 里"]
    src = dict(hk.get("source") or {})
    if src.get("self_built"):
        src = dict(src.get("basis") or {})
        if not src:
            return [f"{hook_id} 标了自建却没写借的是哪一式（basis 为空）"]
    sec = list(src.get("sec") or [])
    if not sec:
        return [f"{hook_id} 没有出处章节"]
    for title in sec:
        if not any(r["title"] == title for r in rows):
            errs.append(f"{hook_id} 的出处章节「{title}」在原文里不是标题")
    body = body_of(rows, sec[-1])
    if src.get("group") and not any(re.match(BULLET.format(name=re.escape(src["group"])), ln)
                                    for ln in body):
        errs.append(f"{hook_id} 的出处分组「{src['group']}」在原文『{sec[-1]}』一节里不存在")
    key = src.get("key") or ""
    if not key:
        errs.append(f"{hook_id} 没有出处条目名")
    elif not any(re.match(BULLET.format(name=re.escape(key)), ln) for ln in body):
        errs.append(f"{hook_id} 的出处条目「{key}」在原文『{sec[-1]}』一节里不存在")
    return errs


def validate_calendar(doc: dict, root: Path | None = None) -> list[str]:
    """缺一条就报错（不许静默放过）：A 层级 / 话题类型 / 钩子出处 / 出处 / 配比。"""
    _, rows = load_source(root)
    five_a = parse_5a(rows)
    stages = parse_stages(rows)
    topics = {t["name"] for t in parse_topics(rows)}
    errs: list[str] = []
    posts = doc.get("posts") or []
    if not posts:
        errs.append("日历里一条内容都没有")
    for p in posts:
        tag = f"第{p.get('n')}条（周{p.get('week')}）"
        layer = p.get("a_layer") or ""
        if layer not in five_a:
            errs.append(f"{tag} 的 A 层级「{layer}」不在原文 5A人群 里")
        if not (p.get("a_layer_text") or "").startswith(layer):
            errs.append(f"{tag} 缺 A 层级的原文表述")
        if not p.get("topic_type"):
            errs.append(f"{tag} 缺话题类型")
        elif p["topic_type"] not in topics:
            errs.append(f"{tag} 的话题类型「{p['topic_type']}」不在原文六大话题方向里")
        hook = p.get("hook") or {}
        if hook.get("id") not in HOOK_BANK:
            errs.append(f"{tag} 的钩子 id「{hook.get('id')}」不在 ideate.HOOK_BANK 里")
        elif not (hook.get("source") or "").strip():
            errs.append(f"{tag} 的钩子缺出处")
        else:
            errs += [f"{tag}：{e}" for e in source_anchor_errors(hook["id"], rows)]
        if not (p.get("basis") or "").strip():
            errs.append(f"{tag} 缺「用了原文哪一节/公式」的出处")
        elif not (p.get("basis_parts") or {}).get("formula"):
            errs.append(f"{tag} 的出处里没有原文公式（原文『…公式』一节没解析到）")
        if not (p.get("idea") or "").strip():
            errs.append(f"{tag} 缺选题一句话")
        if not p.get("date"):
            errs.append(f"{tag} 缺建议发布日")
    # 配比：逐阶段拿原文比例重新算一遍整数分配，条数必须对得上
    stage_of = {s["name"]: s for s in stages}
    for rep in doc.get("stages") or []:
        st = stage_of.get(rep.get("name") or "")
        if not st:
            errs.append(f"阶段「{rep.get('name')}」不在原文里")
            continue
        want_types = [t["type"] for t in st["tiers"]]
        got = rep.get("counts") or []
        if [c["type"] for c in got] != want_types:
            errs.append(f"{rep['name']} 的档位与原文不一致：{want_types}")
            continue
        total = rep.get("posts") or 0
        exp = allocate(total, [t["pct"] for t in st["tiers"]])
        for c, e in zip(got, exp):
            if c["count"] != e:
                errs.append(f"{rep['name']}·{c['type']} 实排 {c['count']} 条，"
                            f"按原文配比应为 {e} 条")
        in_stage = [p for p in posts if p.get("stage") == rep["name"]]
        if len(in_stage) != total:
            errs.append(f"{rep['name']} 标称 {total} 条，实际 {len(in_stage)} 条")
    if sum(r.get("posts", 0) for r in doc.get("stages") or []) != len(posts):
        errs.append("各阶段条数加起来与总条数不等")
    return errs


# ---------------------------------------------------------------- 人读表

def render_md(doc: dict) -> str:
    acc = doc.get("account") or {}
    L = [
        f"# 内容日历 · {doc['core']}（{doc['weeks']} 周）",
        "",
        f"- **账号轨**：{doc['track']}轨"
        + (f"（account.json 记录，decided_by={acc.get('decided_by')}，"
           f"confirmed={acc.get('confirmed')}）" if acc.get("track") else "（--track 手动指定）"),
        f"- **成功指标**：{' / '.join(doc.get('success_metrics') or []) or '（缺）'}"
        f"（口径来源：{doc.get('success_metrics_source') or '（缺）'}）",
        f"- **起跑日**：{doc['start']}"
        f"（{WEEKDAYS[date.fromisoformat(doc['start']).weekday()]}）· 每周 {doc['per_week']} 条 · "
        f"共 {len(doc['posts'])} 条",
        f"- **选题核心**：{doc['core']}（来源 {doc['core_source']}）",
        "",
        "## 双轨怎么影响这张表",
        "",
        f"- {TRACK_SCOPE_NOTE}",
        f"- {TRACK_ANGLE[doc['track']]}",
        "- 换轨只换选题方向、钩子偏好与看数的口径；阶段配比照抄原文，不动一个数字。",
        "",
        "## 配比口径（数字全部来自原文，未改写）",
        "",
        f"- {STAGE_PLAN_NOTE}",
        f"- {RATIO_NOTE}",
        "",
        "| 阶段 | 原文原句 | 占周 | 条数 | 实排（对比原文比例） | 出处 |",
        "|---|---|---|---|---|---|",
    ]
    for rep in doc["stages"]:
        wk = "、".join(f"W{w}" for w in rep["weeks"]) or "—"
        got = "＋".join(f"{c['layer']} {c['count']}" for c in rep["counts"])
        exp = "＋".join(f"{c['layer']} {c['expected']:g}" for c in rep["counts"])
        L.append(f"| {rep['name']} | {rep['raw']} | {wk} | {rep['posts']} 条 | "
                 f"{got}（原文 {exp}） | {rep['ref']} |")

    zero = [f"{rep['name']}·{c['layer']}" for rep in doc["stages"]
            for c in rep["counts"] if not c["count"]]
    if zero:
        L += ["", "> 取整提醒：" + "、".join(zero) + " 在这一版条数下取整成 0 条"
                  "（自建取整规则的必然结果，非原文）；想覆盖到就把 `--weeks` 或 `--per-week` 调大。"]
    L += [
        "",
        "## 日历表",
        "",
        "| 周次 | 建议发布日 | A 层级 | 话题类型（原文六大话题方向） | 钩子类型（id + 出处） | 选题一句话 | 用了原文哪一节 / 公式或切入点 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in doc["posts"]:
        hook = p["hook"]
        hook_txt = (f"{hook['id']} {hook['name']}"
                    + ("（自建扩展，非原文）" if hook.get("self_built") else "")
                    + f"<br>出处：{hook['source']}")
        topic_txt = f"{p['topic_type']}"
        if p.get("topic_construct"):
            topic_txt += f"<br>构造式：{p['topic_construct']}"
        form_txt = f"<br>形态：{p['content_form']}" if p.get("content_form") else ""
        L.append(f"| W{p['week']} | {p['date']} {p['weekday']} | {p['a_layer_text']} | "
                 f"{topic_txt}{form_txt} | {hook_txt} | {p['idea']} | {p['basis']}<br>"
                 f"话题出处：{p['topic_ref']} |")

    forms: dict[str, list[str]] = {}
    for p in doc["posts"]:
        if p.get("content_form"):
            forms.setdefault(p["a_layer"], [])
            if p["content_form"] not in forms[p["a_layer"]]:
                forms[p["a_layer"]].append(p["content_form"])
    if forms:
        L += ["", "## 每档的拍摄形态（原文『内容参考』一节的归类）", ""]
        for layer in sorted(forms):
            L.append(f"- **{layer}**：{' / '.join(forms[layer])}"
                     f"（原文『内容配比』一节 · 内容参考）")

    L += [
        "",
        "## 原文没给出的地方（本表怎么补的，一条条标明）",
        "",
        f"- 发布频率（每周几条）：**原文未给出** → 本仓默认每周 {DEFAULT_PER_WEEK} 条"
        f"（自建扩展，非原文），`--per-week` 可改（本次每周 {doc['per_week']} 条）。",
        "- 星期几发：**原文未给出** → 自建扩展，非原文"
        f"（每周 {doc['per_week']} 条时，按起跑日往后 "
        + "/".join(f"+{d} 天" for d in WEEKDAY_PLAN[doc['per_week']]) + "）。",
        "- 三阶段各占几周、什么时候进下一阶段：**原文未给出** → 自建扩展，非原文（按 "
        + " / ".join(f"{n} {w}%" for n, w in zip(("阶段一", "阶段二", "阶段三"),
                                                 doc["stage_week_weights"])) + " 的周数权重摊周）。",
        "- 每种内容发几条：**原文未给出** → 自建扩展，非原文（最大余数法取整，偏差 < 1 条）。",
        "- 选题一句话的措辞：本仓按原文构造式生成的示例文案，非原文原句；"
        "钩子与公式的出处逐条标在表里。",
        "",
        f"> 方法来源：`{doc['source']}`（唯一方法来源，本表不另立一套）。"
        "本表任何数字与原文冲突，一律以原文为准。",
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------- 入口

def main() -> int:
    ap = argparse.ArgumentParser(description="按原文三阶段配比生成 4 周内容日历")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--weeks", type=int, default=DEFAULT_WEEKS, help="周数（默认 4）")
    ap.add_argument("--per-week", type=int, default=DEFAULT_PER_WEEK, help="每周条数（默认 4）")
    ap.add_argument("--start", default=None, help="起跑日 YYYY-MM-DD（默认下周一）")
    ap.add_argument("--track", default=None, help="门店 / 博主（默认读 account.json）")
    ap.add_argument("--topic", default="", help="选题核心（可选，默认从 brief/ideation/intent 取）")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    if not proj.is_dir():
        print(f"项目目录不存在：{proj}\n  先跑 python3 scripts/track.py --project <项目目录> 建账号档",
              file=sys.stderr)
        return 3
    if a.weeks < 3:
        print("周数至少 3 周：原文三阶段（阶段一/二/三）每阶段至少要占一周。", file=sys.stderr)
        return 2
    if not 1 <= a.per_week <= 7:
        print("--per-week 只收 1–7（一周最多 7 天）。", file=sys.stderr)
        return 2

    acc_p = proj / "account.json"
    account = {}
    if acc_p.exists():
        try:
            account = json.loads(acc_p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"account.json 读不动（{acc_p}）：{e}\n  重跑 python3 scripts/track.py "
                  f"--project \"{proj}\"", file=sys.stderr)
            return 3
    track = (a.track or "").strip().rstrip("轨") or str(account.get("track") or "").strip()
    if track not in ("门店", "博主"):
        print(f"还不知道这个账号在哪条轨：{proj} 里没有可用的 account.json。\n"
              f"  先跑一次判定（先猜、你点头就确认）：\n"
              f"    python3 scripts/track.py --project \"{proj}\""
              f" [--ask \"你的原话\"]\n"
              f"  或者这次临时指定：--track 门店 / --track 博主", file=sys.stderr)
        return 3
    if a.track and account.get("track") and track != account["track"]:
        print(f"注意：本次按 --track {track} 出表，account.json 记的是 "
              f"{account['track']} 轨（未改动账号档）。", file=sys.stderr)

    if a.start:
        try:
            start = date.fromisoformat(a.start)
        except ValueError:
            print(f"--start 要 YYYY-MM-DD（收到「{a.start}」）", file=sys.stderr)
            return 2
    else:
        start = next_monday(date.today())

    core, core_src = _core_of(proj, a.topic)
    try:
        doc = build_calendar(proj, track=track, weeks=a.weeks, per_week=a.per_week,
                             start=start, core=core, core_src=core_src, account=account)
    except CalError as e:
        print(str(e), file=sys.stderr)
        return 3

    errs = validate_calendar(doc)
    if errs:
        print("日历自检没过（先别用）：", file=sys.stderr)
        for e in errs[:10]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    md_p, js_p = proj / "calendar.md", proj / "calendar.json"
    md_p.write_text(render_md(doc), encoding="utf-8")
    js_p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"→ {md_p}\n→ {js_p}")
    print(f"  {track}轨 · {a.weeks} 周 × 每周 {a.per_week} 条 = {len(doc['posts'])} 条 · "
          f"起跑 {start} · 成功指标 {' / '.join(doc.get('success_metrics') or [])}")
    for rep in doc["stages"]:
        got = "＋".join(f"{c['layer']} {c['count']}" for c in rep["counts"])
        print(f"  {rep['name']}（W{'、W'.join(str(w) for w in rep['weeks'])}）：{got} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
