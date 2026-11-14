#!/usr/bin/env python3
"""`track.py` / `content_calendar.py`（双轨判定 + 4 周内容日历）的离线回归。

守的是六件事，别再退回去：
  ① 4 周条数与三阶段配比必须和**原文现解析出来的数字**对得上（原文改一个数字，测试跟着红，
     不硬编码白名单）；
  ② 每条内容都得有 A 层级 / 话题类型 / 钩子出处 / 原文出处 / 选题句 / 发布日，缺一条就失败；
  ③ 钩子 id 必须真在 `ideate.HOOK_BANK` 里，出处锚点必须是原文里真实存在的标题与条目；
  ④ 双轨判定：门店线索→门店轨且带证据原文、博主线索→博主轨、无线索→给猜测且 confirmed=false、
     `--set` 后 decided_by=user；
  ⑤ 两条轨的成功指标不同、且两套都写进 account.json；
  ⑥ 缺输入的时候给人话提示（退出码 3），不许甩 traceback。

只读临时目录、不碰真实素材库、不起渲染，跑一次 5 秒内。

    python3 scripts/test_calendar.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SRC_MD = ROOT / "references" / "content-system.md"
TRACK = SCRIPTS / "track.py"
CAL = SCRIPTS / "content_calendar.py"
sys.path.insert(0, str(SCRIPTS))

import ideate                                       # noqa: E402

# 按路径另起模块名加载（脚本文件名叫 content_calendar，已不与标准库 calendar 撞名）
_spec = importlib.util.spec_from_file_location("cutskills_calendar", CAL)
calendar_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calendar_mod)              # noqa: E402

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)


# ---------------------------------------------------------------- ① 原文现解析（测试自己的实现，不借 content_calendar.py）

TIER_RE = re.compile(r"([\u4e00-\u9fa5]+?型内容)\s*(\d+)\s*%")


def source_stages() -> dict[str, list[tuple[str, int]]]:
    """从原文里现抓「阶段一/二/三」的配比原句，抓不到就让测试直接崩（不许静默跳过）。"""
    lines = SRC_MD.read_text(encoding="utf-8").splitlines()
    out: dict[str, list[tuple[str, int]]] = {}
    for i, ln in enumerate(lines):
        m = re.match(r"^###\s*(阶段[一二三])\s*$", ln)
        if not m:
            continue
        for ln2 in lines[i + 1:]:
            if ln2.startswith("#"):
                break
            if "%" in ln2:
                out[m.group(1)] = [(a, int(b)) for a, b in TIER_RE.findall(ln2)]
                break
    return out


def source_topics() -> set[str]:
    """原文『内容创作』一节 · 漏斗流视频 · 六大话题选题方向 的六个名字。"""
    lines = SRC_MD.read_text(encoding="utf-8").splitlines()
    names, inside = set(), False
    for ln in lines:
        if re.match(r"^-\s*六大话题选题方向\s*$", ln):
            inside = True
            continue
        if not inside:
            continue
        if re.match(r"^-\s*\S", ln):
            break
        m = re.match(r"^ {2}-\s*(\S+)\s*$", ln)
        if m:
            names.add(m.group(1))
    return names


def source_headings() -> set[str]:
    return {m.group(1).strip()
            for m in re.finditer(r"^#{2,4}\s+(.+?)\s*$",
                                 SRC_MD.read_text(encoding="utf-8"), re.M)}


def hamilton(total: int, pcts: list[int]) -> list[int]:
    """最大余数法（测试里独立实现一遍，不 import 被测代码的算法）。"""
    base = [(total * p) // 100 for p in pcts]
    rems = [(total * p) % 100 for p in pcts]
    left = total - sum(base)
    for i in sorted(range(len(pcts)), key=lambda k: (-rems[k], k))[:left]:
        base[i] += 1
    return base


# ---------------------------------------------------------------- 造项目

def mk_project(root: Path, name: str, intent: str = "", ask: str = "") -> Path:
    proj = root / name
    proj.mkdir(parents=True, exist_ok=True)
    if intent:
        (proj / "intent.json").write_text(json.dumps(
            {"raw": intent, "audience": "同城食客", "promise": "到店吃羊排"},
            ensure_ascii=False), encoding="utf-8")
    return proj


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cutskills_cal_"))
    stages = source_stages()
    topics = source_topics()

    print("① 4 周条数与三阶段配比（数字现从原文解析，不硬编码）")
    must("原文三个阶段都解析到配比", sorted(stages) == ["阶段一", "阶段三", "阶段二"],
         f"解析到 {sorted(stages)}")
    must("六个话题方向都解析到", len(topics) == 6, f"解析到 {sorted(topics)}")
    for name, tiers in stages.items():
        must(f"{name} 配比之和为 100%", sum(p for _, p in tiers) == 100, f"{tiers}")
    must("阶段三不含泛流量（原文如此）", all("泛流量" not in t for t, _ in stages["阶段三"]),
         f"{stages['阶段三']}")

    p_shop = mk_project(tmp, "shop", "我在珠海开了家烧烤店，想做同城获客，让更多人到店吃羊排")
    r = run(TRACK, "--project", str(p_shop))
    must("track.py 在门店线索项目上跑通", r.returncode == 0, (r.stderr or "")[-200:])
    r = run(CAL, "--project", str(p_shop))
    must("content_calendar.py 出表", r.returncode == 0 and (p_shop / "calendar.json").exists(),
         (r.stderr or "")[-300:])
    doc = json.loads((p_shop / "calendar.json").read_text(encoding="utf-8"))

    weeks = int(doc.get("weeks") or 0)
    per_week = int(doc.get("per_week") or 0)
    posts = doc.get("posts") or []
    must("默认 4 周", weeks == 4, f"{weeks}")
    must("条数 = 周数 × 每周条数", len(posts) == weeks * per_week,
         f"{len(posts)} vs {weeks}×{per_week}")
    must("四周都在表里", sorted({p["week"] for p in posts}) == [1, 2, 3, 4],
         f"{sorted({p['week'] for p in posts})}")
    per_week_count = {w: sum(1 for p in posts if p["week"] == w) for w in range(1, weeks + 1)}
    must("每周条数一致", set(per_week_count.values()) == {per_week}, f"{per_week_count}")
    month_days = [len({p["date"] for p in posts if p["week"] == w}) for w in range(1, weeks + 1)]
    must("同周内不撞日期", month_days == [per_week] * weeks, f"{month_days}")

    stage_reps = {rep["name"]: rep for rep in doc.get("stages") or []}
    must("三个阶段都排进去了", sorted(stage_reps) == sorted(stages), f"{sorted(stage_reps)}")
    total_stage_posts = 0
    for name, tiers in stages.items():
        rep = stage_reps.get(name) or {}
        want_types = [t for t, _ in tiers]
        got = rep.get("counts") or []
        must(f"{name} 档位与原文一致", [c["type"] for c in got] == want_types,
             f"{[c['type'] for c in got]} vs {want_types}")
        total = int(rep.get("posts") or 0)
        total_stage_posts += total
        exp = hamilton(total, [p for _, p in tiers])
        must(f"{name} 条数按原文配比分配（{total} 条）",
             [c["count"] for c in got] == exp,
             f"实排 {[c['count'] for c in got]} vs 应为 {exp}")
        for c, (t, pct) in zip(got, tiers):
            dev = abs(c["count"] - total * pct / 100)
            must(f"{name}·{t} 与原文比例偏差 < 1 条", dev < 1, f"偏差 {dev:.2f}")
    must("三阶段条数加起来 = 总条数", total_stage_posts == len(posts),
         f"{total_stage_posts} vs {len(posts)}")
    for name, rep in stage_reps.items():
        allowed = {c["type"] for c in rep["counts"]}
        used_layers = {p["content_type"] for p in posts if p["stage"] == name}
        must(f"{name} 只排本阶段的档位（不外溢）", used_layers <= allowed,
             f"{sorted(used_layers)} 超出 {sorted(allowed)}")

    print("② 每条都带 A 层级 / 话题类型 / 钩子出处（缺一条就失败）")
    errs = calendar_mod.validate_calendar(doc)
    must("整份日历过自检", not errs, "；".join(errs[:3]))
    posts_of = {"a_layer": "", "a_layer_text": "", "topic_type": "", "idea": "",
                "basis": "", "date": ""}
    for field in posts_of:
        bad = json.loads(json.dumps(doc))
        for p in bad["posts"]:
            p.pop(field, None)
        got_errs = calendar_mod.validate_calendar(bad)
        must(f"缺 {field} 必须报错", bool(got_errs), "校验没抓住字段缺失")
    for field in ("id", "source"):
        bad = json.loads(json.dumps(doc))
        for p in bad["posts"]:
            p["hook"] = {k: v for k, v in p["hook"].items() if k != field}
        must(f"钩子缺 {field} 必须报错", bool(calendar_mod.validate_calendar(bad)),
             "校验没抓住钩子字段缺失")
    bad_ratio = json.loads(json.dumps(doc))
    bad_ratio["stages"][0]["counts"][0]["count"] += 1
    must("配比被改动必须报错", bool(calendar_mod.validate_calendar(bad_ratio)),
         "配比篡改没被抓住")
    bad_hook = json.loads(json.dumps(doc))
    bad_hook["posts"][0]["hook"]["id"] = "H99"
    must("钩子 id 不存在必须报错", bool(calendar_mod.validate_calendar(bad_hook)),
         "不存在的钩子 id 没被抓住")
    bad_anchor = json.loads(json.dumps(doc))
    bad_anchor["posts"][0]["hook"]["id"] = "H01"
    must("钩子 id 与出处对不上时不报错（H01 出处是真的）",
         not calendar_mod.validate_calendar(bad_anchor), "H01 的出处被误判")
    must("每条的话题类型都是原文六大方向之一",
         all(p["topic_type"] in topics for p in posts),
         f"{sorted({p['topic_type'] for p in posts})}")
    must("话题类型不是单一一个方向", len({p["topic_type"] for p in posts}) >= 3,
         f"{sorted({p['topic_type'] for p in posts})}")
    must("每条 A 层级都是原文 A1–A5 原文表述",
         all(re.match(r"^A[1-5]（.+人群.+型内容）$", p["a_layer_text"]) for p in posts),
         f"{[p['a_layer_text'] for p in posts[:2]]}")
    must("每条都标了用了哪个公式",
         all("公式" in (p.get("basis") or "") and "原文『" in p["basis"] for p in posts),
         f"{posts[0].get('basis')}")

    print("③ 钩子 id 与出处锚点（回 ideate.HOOK_BANK + 原文标题逐级对账）")
    heads = source_headings()
    used: dict[str, int] = {}
    for p in posts:
        used[p["hook"]["id"]] = used.get(p["hook"]["id"], 0) + 1
    must("用到的钩子都真在 HOOK_BANK 里",
         all(h in ideate.HOOK_BANK for h in used), f"{sorted(used)}")
    anchor_bad: list[str] = []
    for hid in sorted(used):
        src = dict(ideate.HOOK_BANK[hid].get("source") or {})
        if src.get("self_built"):
            if not src.get("basis"):
                anchor_bad.append(f"{hid} 自建却没写借的哪一式")
                continue
            src = dict(src["basis"])
        sec = list(src.get("sec") or [])
        if not sec or any(t not in heads for t in sec):
            anchor_bad.append(f"{hid} 章节 {sec} 不是原文标题")
            continue
        body = calendar_mod.body_of(calendar_mod.load_source()[1], sec[-1])
        group, key = src.get("group"), src.get("key") or ""
        if group and not any(re.match(rf"^\s*-\s*{re.escape(group)}\s*$", ln) for ln in body):
            anchor_bad.append(f"{hid} 分组「{group}」不在『{sec[-1]}』里")
        if not key or not any(re.match(rf"^\s*-\s*{re.escape(key)}\s*$", ln) for ln in body):
            anchor_bad.append(f"{hid} 条目「{key}」不在『{sec[-1]}』里")
    must("钩子出处锚点在原文里真实存在", not anchor_bad, "；".join(anchor_bad[:3]))
    must("钩子不是一条用到底（≥3 种）", len(used) >= 3, f"{sorted(used)}")

    print("④ 双轨判定（先猜、再让确认）")
    acc = json.loads((p_shop / "account.json").read_text(encoding="utf-8"))
    must("门店线索 → 门店轨", acc["track"] == "门店", acc["track"])
    must("门店轨带证据原文片段",
         bool(acc["basis"]) and all(b.get("field") and b.get("clue") and b.get("quote")
                                    for b in acc["basis"]), f"{acc['basis'][:1]}")
    must("证据片段里真含命中的线索词",
         all(b["clue"] in b["quote"] for b in acc["basis"]),
         f"{[(b['clue'], b['quote']) for b in acc['basis'][:2]]}")
    must("证据指向具体字段（能翻到出处）",
         all(b["field"].startswith(("intent.json", "brief.json", "--ask")) for b in acc["basis"]),
         f"{[b['field'] for b in acc['basis'][:3]]}")
    must("第一次是猜的（confirmed=false / decided_by=guess）",
         acc["confirmed"] is False and acc["decided_by"] == "guess",
         f"{acc['confirmed']} / {acc['decided_by']}")
    must("猜测是一句人话：我猜你是…，对吗？",
         acc["guess_line"].startswith("我猜你是") and acc["guess_line"].endswith("，对吗？"),
         acc["guess_line"])
    # 两条真选项 + 一条「不答也按猜测走」：BRIEF 要求「其余环节全用默认值推进」，
    # 所以默认接受那一条必须明写出来，而不是靠用户不知道可以不答。
    _real = [o for o in acc["options"] if "不答也行" not in o]
    must("只给两个可改选项（第三条是默认接受，不算新选项）",
         len(_real) == 2 and len(acc["options"]) == 3
         and any("不答也行" in o for o in acc["options"]),
         f"{acc['options']}")

    p_blog = mk_project(tmp, "blog")
    r = run(TRACK, "--project", str(p_blog),
            "--ask", "我做个人IP，想涨粉，账号的3秒留存和完播率一直上不去")
    acc_blog = json.loads((p_blog / "account.json").read_text(encoding="utf-8")) if r.returncode == 0 else {}
    must("博主线索 → 博主轨", acc_blog.get("track") == "博主", f"{acc_blog.get('track')}")
    must("博主轨带证据", bool(acc_blog.get("basis")), "证据为空")

    p_none = mk_project(tmp, "none")
    r = run(TRACK, "--project", str(p_none), "--ask", "帮我做条视频吧，随便拍拍")
    acc_none = json.loads((p_none / "account.json").read_text(encoding="utf-8")) if r.returncode == 0 else {}
    must("无线索也先给猜测（不逼人回答开放题）",
         bool(acc_none.get("guess_line", "").endswith("，对吗？")), f"{acc_none.get('guess_line')}")
    must("无线索时 confirmed=false 且标出这是默认猜测",
         acc_none.get("confirmed") is False and acc_none.get("decided_by") == "guess"
         and acc_none.get("default_guess") is True,
         f"{acc_none.get('confirmed')} / {acc_none.get('decided_by')} / {acc_none.get('default_guess')}")

    r = run(TRACK, "--project", str(p_shop), "--set", "博主")
    acc_set = json.loads((p_shop / "account.json").read_text(encoding="utf-8"))
    must("--set 后 decided_by=user 且 confirmed=true",
         acc_set["track"] == "博主" and acc_set["decided_by"] == "user" and acc_set["confirmed"] is True,
         f"{acc_set['track']} / {acc_set['decided_by']} / {acc_set['confirmed']}")
    must("改判和线索打架时留反向证据（不黑箱）", bool(acc_set.get("dissent")),
         f"{acc_set.get('dissent')}")
    must("--set 值不认识时报错退 2", run(TRACK, "--project", str(p_shop), "--set", "混子").returncode == 2)

    r = run(TRACK, "--project", str(p_shop), "--confirm")
    acc_ok = json.loads((p_shop / "account.json").read_text(encoding="utf-8"))
    must("--confirm 后 confirmed=true",
         acc_ok.get("confirmed") is True and acc_ok.get("decided_by") == "user",
         f"{acc_ok.get('confirmed')} / {acc_ok.get('decided_by')}")

    print("⑤ 两条轨的成功指标（不同且都写进 account.json）")
    tracks = acc_blog.get("tracks") or {}
    metrics_shop = (tracks.get("门店") or {}).get("success_metrics") or []
    metrics_blog = (tracks.get("博主") or {}).get("success_metrics") or []
    must("门店轨口径 = 评论咨询/私信/到店",
         metrics_shop == ["评论区咨询", "私信", "到店"], f"{metrics_shop}")
    must("博主轨口径 = 3 秒留存/完播/涨粉",
         metrics_blog == ["3 秒留存", "完播", "涨粉"], f"{metrics_blog}")
    must("两套指标确实不同", metrics_shop != metrics_blog and bool(metrics_shop and metrics_blog))
    must("当前轨的指标 = account 顶层 success_metrics",
         acc_blog.get("success_metrics") == metrics_blog, f"{acc_blog.get('success_metrics')}")

    p_blog2 = mk_project(tmp, "blog2")
    run(TRACK, "--project", str(p_blog2), "--ask", "我拍探店号，想涨粉，完播率一直很低")
    r = run(CAL, "--project", str(p_blog2))
    doc_blog = json.loads((p_blog2 / "calendar.json").read_text(encoding="utf-8"))
    must("博主轨日历的成功指标是博主口径",
         doc_blog["success_metrics"] == ["3 秒留存", "完播", "涨粉"], f"{doc_blog['success_metrics']}")
    must("博主轨日历里写明双轨只改选题/钩子/指标、不改配比",
         "不改配比数字" in (p_blog2 / "calendar.md").read_text(encoding="utf-8"))
    same_ratio = [[c["count"] for c in rep["counts"]]
                  for rep in doc_blog["stages"]] == [[c["count"] for c in rep["counts"]]
                                                     for rep in doc["stages"]]
    must("换轨后配比条数不变（原文配比是唯一真值）", same_ratio)
    diff_idea = {p["idea"] for p in posts} != {p["idea"] for p in doc_blog["posts"]}
    must("换轨后选题确实变了（双轨影响选题方向）", diff_idea)

    print("⑥ 缺输入给可读提示，不崩")
    p_empty = tmp / "empty"
    p_empty.mkdir()
    r_t = run(TRACK, "--project", str(p_empty))
    out_t = (r_t.stdout or "") + (r_t.stderr or "")
    must("track.py 无原话时退 3 且给人话", r_t.returncode == 3 and "intent.json" in out_t,
         out_t[-200:])
    must("track.py 不打 traceback", "Traceback" not in out_t)
    r_c = run(CAL, "--project", str(p_empty))
    out_c = (r_c.stdout or "") + (r_c.stderr or "")
    must("content_calendar.py 没 account.json 时退 3 且指路 track.py",
         r_c.returncode == 3 and "track.py" in out_c, out_c[-240:])
    must("content_calendar.py 不打 traceback", "Traceback" not in out_c)
    r_bad = run(CAL, "--project", str(p_shop), "--weeks", "1")
    must("--weeks 1 说明三阶段至少 3 周（退 2）", r_bad.returncode == 2, (r_bad.stderr or "")[-160:])
    r_track = run(CAL, "--project", str(p_blog2), "--track", "博主", "--start", "2026-11-17")
    doc_manual = json.loads((p_blog2 / "calendar.json").read_text(encoding="utf-8"))
    must("--start 指定日期生效", doc_manual["start"] == "2026-11-17" and
         doc_manual["posts"][0]["date"] == "2026-11-17", f"{doc_manual['posts'][0]['date']}")
    must("--track 手改轨道可用", r_track.returncode == 0 and doc_manual["track"] == "博主")

    print()
    if fails:
        print(f"❌ 没过（{len(fails)} 项）：{'、'.join(fails)}")
        return 1
    print(f"✅ 双轨判定与 4 周日历全部通过（{len(posts)} 条 × {len(stages)} 阶段 · 现场 {tmp}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
