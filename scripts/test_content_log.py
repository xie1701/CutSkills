#!/usr/bin/env python3
"""`content_log.py` / `review_week.py`（内容循环账本 + 周复盘）的离线回归。

守的是四件事，别再退回去：
  ① 重复 --add 同一项目同一链接必须被拒（否则一本账里同一条发布两份数据；
  ② --update 不存在的 id 必须报错，不许静默造一条新记录；
  ③ 样本 <5 的组必须写「样本不够，不下结论」——凑数的结论比没有结论更害人；
  ④ 周区间边界（跨月、周一当天）与账本损坏时的报错形态：必须是人话，不许 traceback。

只读临时目录、不碰真实素材库、不起渲染，跑一次 2 秒内。

    python3 scripts/test_content_log.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOG = SCRIPTS / "content_log.py"
REVIEW = SCRIPTS / "review_week.py"
sys.path.insert(0, str(SCRIPTS))

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)


def mk_lib(root: Path, slug: str = "demo-01", with_truth: bool = True) -> tuple[Path, Path]:
    """素材库 + 一个项目。with_truth=True 时写入 timeline/brief/intent/ideation/scenes。"""
    lib = root / "lib"
    proj = lib / "_cutskills" / "projects" / slug
    proj.mkdir(parents=True, exist_ok=True)
    if with_truth:
        (proj / "timeline.json").write_text(json.dumps({
            "version": 5, "duration_s": 46.8, "pack": "P5-viral-pop",
            "plan": "S1-conclude-fast",
            "title_bar": {"top": "十年材料老炮实话", "main": "国产vs进口怎么选"},
            "elements": [{"n": 1, "kind": "gen", "props": {"scene": "order_cash"}}],
        }, ensure_ascii=False), encoding="utf-8")
        (proj / "brief.json").write_text(json.dumps(
            {"change": "从站队变成按项目定", "length_s": 46}, ensure_ascii=False), encoding="utf-8")
        (proj / "intent.json").write_text(json.dumps(
            {"raw": "国产和进口的材料怎么选"}, ensure_ascii=False), encoding="utf-8")
        (proj / "ideation.json").write_text(json.dumps({
            "topic_raw": "国产和进口材料怎么选", "selected_proposal": 1,
            "proposals": [{"index": 1, "crowd": "A1 泛流量破圈（占 50%）"}],
        }, ensure_ascii=False), encoding="utf-8")
        (proj / "scenes").mkdir(exist_ok=True)
        (proj / "scenes" / "order_cash.json").write_text('{"id": "order_cash"}', encoding="utf-8")
    return lib, proj


def add(lib: Path, proj: str, url: str, day: str, *extra: str) -> subprocess.CompletedProcess:
    return run(LOG, "--add", "--project", proj, "--library", str(lib),
               "--post-url", url, "--posted-at", day, *extra)


def write_log(lib: Path, specs: list[tuple]) -> Path:
    """直接写一份账本（(发布日, 项目, 播放, 完播占比, 涨粉, 包装包, 节奏)），测周区间与样本口径。"""
    rows = []
    for i, (day, proj, views, watch, follows, pack, plan) in enumerate(specs):
        rows.append({
            "version": 1, "id": f"{day.replace('-', '')}-{proj}-{i:06d}", "project": proj,
            "post_url": f"https://example.com/{proj}/{i}", "posted_at": day, "platform": "小红书",
            "topic": f"题材{proj}", "topic_type": "A1 泛流量破圈（占 50%）", "hook": f"钩子{proj}",
            "duration_s": 45.0, "pack": pack, "plan": plan, "scenes": None,
            "views": views, "avg_watch_pct": watch, "likes": 40, "comments": 9, "shares": 3,
            "saves": 11, "follows": follows, "retention_3s": 0.7,
            "created": "2026-11-14T10:00:00", "updated": None, "truth_src": {},
        })
    log = lib / "content-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return log


# ---------------------------------------------------------------- ① 真值落库 + 重复拒收

def case_add() -> None:
    print("① 追加：项目真值自动带上、重复链接拒收")
    with tempfile.TemporaryDirectory() as td:
        lib, _ = mk_lib(Path(td))
        r = add(lib, "demo-01", "https://www.xiaohongshu.com/explore/abc123", "2026-11-09",
                "--platform", "小红书")
        must("首次 --add 退 0", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])
        log = lib / "content-log.jsonl"
        recs = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        must("落库正好一行", len(recs) == 1, str(len(recs)))
        rec = recs[0]
        must("id = 发布日 + 项目 slug + 链接指纹",
             rec["id"].startswith("20261109-demo-01-") and len(rec["id"]) == len("20261109-demo-01-") + 6,
             rec["id"])
        must("片长取自 timeline.json", rec["duration_s"] == 46.8, str(rec.get("duration_s")))
        must("包装包取自 timeline.json", rec["pack"] == "P5-viral-pop", str(rec.get("pack")))
        must("节奏取自 timeline.json", rec["plan"] == "S1-conclude-fast", str(rec.get("plan")))
        must("钩子文案取自 title_bar", rec["hook"] == "十年材料老炮实话｜国产vs进口怎么选", str(rec.get("hook")))
        must("主题取自 ideation 选题原话", rec["topic"] == "国产和进口材料怎么选", str(rec.get("topic")))
        must("题材取自 5A 档位", rec["topic_type"].startswith("A1"), str(rec.get("topic_type")))
        must("场景 id 列表齐全", rec["scenes"] == ["order_cash"], str(rec.get("scenes")))
        must("每个真值都留了出处", rec["truth_src"].get("pack") == "timeline.json", str(rec.get("truth_src")))
        must("数据先留空（不编数字）",
             all(rec.get(f) is None for f in ("views", "avg_watch_pct", "follows", "retention_3s")),
             str({f: rec.get(f) for f in ("views", "avg_watch_pct", "follows")}))

        must("同一 id 可复算（同输入同 id）",
             rec["id"] == __import__("content_log").make_id("2026-11-09", "demo-01",
                                                            "https://www.xiaohongshu.com/explore/abc123"),
             rec["id"])

        r = add(lib, "demo-01", "https://www.xiaohongshu.com/explore/abc123/", "2026-11-09")
        must("重复 --add 被拒（退 2）", r.returncode == 2, (r.stdout + r.stderr).strip()[-200:])
        must("拒收说明指向 --update", "重复" in r.stderr and "--update" in r.stderr, r.stderr.strip()[-200:])
        must("被拒后账本没多出一行",
             len([ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]) == 1)

        r = add(lib, "demo-01", "https://www.xiaohongshu.com/explore/def456", "2026-11-10")
        must("换链接能记第二条", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])

    with tempfile.TemporaryDirectory() as td:
        lib, proj = mk_lib(Path(td), slug="bare-01", with_truth=False)
        r = add(lib, "bare-01", "https://example.com/x", "2026-11-11")
        rec = json.loads((lib / "content-log.jsonl").read_text(encoding="utf-8").splitlines()[0])
        must("取不到真值就写 null（不编）",
             r.returncode == 0 and all(rec[k] is None for k in
                                       ("duration_s", "pack", "plan", "hook", "topic", "topic_type", "scenes")),
             str(rec))


# ---------------------------------------------------------------- ② update

def case_update() -> None:
    print("② 回填：不存在的 id 报错、同 id 追加修订、非法数字拒收")
    with tempfile.TemporaryDirectory() as td:
        lib, _ = mk_lib(Path(td))
        add(lib, "demo-01", "https://example.com/a", "2026-11-09")
        log = lib / "content-log.jsonl"
        rid = json.loads(log.read_text(encoding="utf-8").splitlines()[0])["id"]

        r = run(LOG, "--update", "--id", "20261101-nope-000000", "--library", str(lib), "--views", "100")
        must("update 不存在的 id 退 2", r.returncode == 2, (r.stdout + r.stderr).strip()[-200:])
        must("报错列出可用 id", "没有 id=" in r.stderr and rid in r.stderr, r.stderr.strip()[-240:])
        must("不存在的 id 没被写进账本",
             len([ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]) == 1)

        r = run(LOG, "--update", "--id", rid, "--library", str(lib), "--views", "12000",
                "--avg-watch-pct", "34%", "--likes", "210", "--follows", "12",
                "--retention-3s", "62%")
        must("回填退 0", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])
        recs = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        must("append-only：同 id 追加修订行", len(recs) == 2 and recs[0]["id"] == recs[1]["id"], str(len(recs)))
        must("占比归一成 0–1", recs[-1]["avg_watch_pct"] == 0.34 and recs[-1]["retention_3s"] == 0.62,
             str((recs[-1]["avg_watch_pct"], recs[-1]["retention_3s"])))
        must("没给的字段保持 null（不拿 0 冒充）", recs[-1]["comments"] is None, str(recs[-1]["comments"]))
        must("修订行带本地时间戳", bool(recs[-1].get("updated")), str(recs[-1].get("updated")))

        r = run(LOG, "--update", "--id", rid, "--library", str(lib), "--avg-watch-pct", "1.2")
        must("1.0–1.5 的裸占比拒收（口径同 insights.py）", r.returncode == 2 and "分不清" in r.stderr,
             r.stderr.strip()[-160:])
        r = run(LOG, "--update", "--id", rid, "--library", str(lib), "--views", "-5")
        must("负播放拒收", r.returncode == 2 and "负数" in r.stderr, r.stderr.strip()[-160:])
        r = run(LOG, "--update", "--id", rid, "--library", str(lib))
        must("一个数字都不给 → 说清要几个参数", r.returncode == 2 and "--views" in r.stderr,
             r.stderr.strip()[-160:])

        r = run(LOG, "--list", "--library", str(lib), "--since", "2026-11-10")
        must("--list --since 过滤掉更早的", r.returncode == 0 and "12000" not in r.stdout,
             r.stdout.strip()[-200:])
        r = run(LOG, "--list", "--library", str(lib))
        must("--list 显示最后一版数据与 id", r.returncode == 0 and rid in r.stdout and "12000" in r.stdout,
             r.stdout.strip()[-240:])
        must("同一 id 只出现一行（取最后一条）", r.stdout.count(rid) == 1, r.stdout.count(rid))


# ---------------------------------------------------------------- ③ 样本 <5

def case_sample_floor() -> None:
    print("③ 样本 <5 拒下结论、5 条才过样本线（正负样本对照）")
    specs = [("2026-11-09", "s1", 12000, 0.42, 18, "P5-viral-pop", "S1-conclude-fast"),
             ("2026-11-10", "s2", 9000, 0.38, 11, "P5-viral-pop", "S1-conclude-fast"),
             ("2026-11-11", "s3", 15000, 0.45, 22, "P5-viral-pop", "S1-conclude-fast")]
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        write_log(lib, specs)
        rows = [json.loads(ln) for ln in
                (lib / "content-log.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows[2]["follows"] = None          # 只留 2 条有涨粉 → 涨粉效率也样本不够
        (lib / "content-log.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-09")
        must("3 条也能出报告（退 0）", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])
        rep = (lib / "insights" / "week-2026-11-09.md").read_text(encoding="utf-8")
        must("首行就声明不承诺播放量", rep.splitlines()[0].startswith("> 本报告只给相对信号")
             and "不承诺任何播放量" in rep.splitlines()[0], rep.splitlines()[0][:80])
        must("四块齐全", all(k in rep for k in ("## 一、本周发了什么", "## 二、哪类内容完播更好",
                                              "## 三、哪条涨粉效率高", "## 四、下周选题建议")))
        must("样本不够明说（组级）", "样本不够（n=3 < 5），不下结论" in rep, rep[-300:])
        must("没有过样本线的组合就不给正面结论",
             "**结论**：过样本线的组合里" not in rep and "所以哪类更好——不下结论" in rep, rep[-400:])
        must("涨粉效率不足 3 条明说不够",
             "样本不够（同时有播放和涨粉数据的只有 2 条，< 3），不下结论" in rep, rep[-400:])
        must("仍给出具体下一步（不写空话）",
             "锁死配方补样本" in rep and "只变钩子" in rep and "条同一题材" not in rep, rep[-300:])

    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        write_log(lib, specs + [("2026-11-12", "s4", 11000, 0.40, 15, "P5-viral-pop", "S1-conclude-fast"),
                                ("2026-11-13", "s5", 8000, 0.36, 9, "P5-viral-pop", "S1-conclude-fast")])
        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-09")
        rep = (lib / "insights" / "week-2026-11-09.md").read_text(encoding="utf-8")
        must("5 条且完播够 → 过样本线（正样本，防「永远说不够」）",
             r.returncode == 0 and "过样本线" in rep, rep[-400:])
        must("过样本线时给出复制配方的具体动作",
             "复制最优配方" in rep and "P5-viral-pop" in rep and "S1-conclude-fast" in rep, rep[-400:])
        must("建议里点名具体题材，不写「多发好内容」", "题材s1" in rep, rep[-300:])

    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        specs = [(f"2026-11-{10 + i:02d}", f"w{i}", 1000 + i * 100, 0.3 + i * 0.01, 3,
                  "P1-warm", "S2-live-mid") for i in range(5)]
        write_log(lib, specs)          # 5 条同组，但一条都没回填完播 → 仍不许算平均
        rows = [json.loads(ln) for ln in (lib / "content-log.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["avg_watch_pct"] = None
        (lib / "content-log.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        run(REVIEW, "--library", str(lib), "--week", "2026-11-09")
        rep = (lib / "insights" / "week-2026-11-09.md").read_text(encoding="utf-8")
        must("条数够但完播回填 <3 也不下结论",
             "条数够但回填完播只有 0 条（< 3），不下结论" in rep
             and "**结论**：过样本线的组合里" not in rep, rep[-500:])
        must("这种情况给的是「补完播数据」而不是「补样本」",
             "先把完播数据补齐" in rep and "锁死配方补样本" not in rep, rep[-400:])


# ---------------------------------------------------------------- ④ 周区间边界

def case_week_bounds() -> None:
    print("④ 周区间边界：跨月、周一当天、给周中任意一天要归一")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        # 2026-10-26 是周一，2026-11-01 是周日，2026-11-02 是下周一
        write_log(lib, [("2026-10-25", "a", 1000, 0.3, 1, "P1-warm", "S1-conclude-fast"),
                        ("2026-10-26", "b", 2000, 0.3, 1, "P1-warm", "S1-conclude-fast"),
                        ("2026-11-01", "c", 3000, 0.3, 1, "P1-warm", "S1-conclude-fast"),
                        ("2026-11-02", "d", 4000, 0.3, 1, "P1-warm", "S1-conclude-fast")])
        r = run(REVIEW, "--library", str(lib), "--week", "2026-10-29")   # 周四 → 归一 10-26
        must("跨月周：任意一天归一到周一", r.returncode == 0
             and (lib / "insights" / "week-2026-10-26.md").exists(), (r.stdout + r.stderr).strip()[-160:])
        rep = (lib / "insights" / "week-2026-10-26.md").read_text(encoding="utf-8")
        must("跨月周两端都算进来（10-26 + 11-01 = 2 条）", "共 2 条" in rep, rep[:600])
        must("周外一天都不算：10-25（上周日）不在表里", "| 2026-10-25 |" not in rep, rep[:900])
        must("周外一天都不算：11-02（下周一）不在表里", "| 2026-11-02 |" not in rep, rep[:900])
        must("跨月的周日确实记进来", "| 2026-11-01 |" in rep, rep[:900])

        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-02")
        rep2 = (lib / "insights" / "week-2026-11-02.md").read_text(encoding="utf-8")
        must("下周一自己那一周只含 1 条", "共 1 条" in rep2, rep2[:600])

        # 周一当天：2026-11-09 是周一
        write_log(lib, [("2026-11-08", "e", 1000, 0.3, 1, "P1-warm", "S1-conclude-fast"),
                        ("2026-11-09", "f", 2000, 0.3, 1, "P1-warm", "S1-conclude-fast"),
                        ("2026-11-15", "g", 3000, 0.3, 1, "P1-warm", "S1-conclude-fast"),
                        ("2026-11-16", "h", 4000, 0.3, 1, "P1-warm", "S1-conclude-fast")])
        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-09")
        rep3 = (lib / "insights" / "week-2026-11-09.md").read_text(encoding="utf-8")
        must("周一当天算本周第一天", "共 2 条" in rep3, rep3[:600])
        must("周日算本周最后一天", "| 2026-11-15 |" in rep3 and "| 2026-11-16 |" not in rep3, rep3[:900])
        must("上周日 11-08 不进本周", "| 2026-11-08 |" not in rep3, rep3[:900])

        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-11")   # 周中 → 同一份
        must("周中任意一天归一到同一周（同一份文件）", r.returncode == 0
             and "week-2026-11-09.md" in r.stdout, (r.stdout + r.stderr).strip()[-160:])

        r = run(REVIEW, "--library", str(lib), "--week", "2026-13-01")
        must("--week 写法不对 → 人话报错退 2", r.returncode == 2 and "YYYY-MM-DD" in r.stderr,
             r.stderr.strip()[-160:])


# ---------------------------------------------------------------- ⑤ 账本损坏

def case_broken_log() -> None:
    print("⑤ 账本损坏：报行号与人话，不许 traceback")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir(parents=True)
        good = {"id": "20261109-x-000001", "project": "x", "post_url": "https://example.com/x",
                "posted_at": "2026-11-09", "views": 1000}
        (lib / "content-log.jsonl").write_text(
            json.dumps(good, ensure_ascii=False) + "\n" + '{"id": "20261110-y-000002", "views": 5\n',
            encoding="utf-8")
        r = run(LOG, "--list", "--library", str(lib))
        must("content_log 读坏账本退 2", r.returncode == 2, (r.stdout + r.stderr).strip()[-200:])
        must("报出第 2 行 + 不是合法 JSON",
             "第 2 行" in r.stderr and "不是合法 JSON" in r.stderr, r.stderr.strip()[-240:])
        must("不甩 traceback", "Traceback" not in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
        must("提示别整份重写", "别整份重写" in r.stderr, r.stderr.strip()[-240:])

        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-09")
        must("review_week 读坏账本退 2 且不 traceback",
             r.returncode == 2 and "Traceback" not in (r.stdout + r.stderr)
             and "不是合法 JSON" in r.stderr, (r.stdout + r.stderr).strip()[-240:])

    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir(parents=True)
        (lib / "content-log.jsonl").write_text('["不是对象"]\n', encoding="utf-8")
        r = run(LOG, "--list", "--library", str(lib))
        must("缺 id 的行也给可读报错", r.returncode == 2 and "缺 id" in r.stderr,
             r.stderr.strip()[-200:])


# ---------------------------------------------------------------- ⑥ 一本账（insights 优先读账本）

def case_single_book() -> None:
    print("⑥ 一本账：insights.py 优先读 content-log，没有账本才要 CSV")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        write_log(lib, [(f"2026-11-{9 + i:02d}", f"m{i}", 1000 + i * 500, 0.3 + i * 0.02, 2 + i,
                         "P1-warm", "S1-conclude-fast") for i in range(5)])
        csv = Path(td) / "d.csv"
        csv.write_text("日期,标题,播放\n2026-11-09,别的来源,999\n", encoding="utf-8")
        r = run(SCRIPTS / "insights.py", "--library", str(lib), "--csv", str(csv))
        must("有账本时 insights 读账本（退 0）", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])
        must("账本优先、CSV 不参与", "content-log.jsonl" in r.stdout and "不参与" in r.stdout,
             (r.stdout + r.stderr).strip()[-200:])
        metrics = json.loads((lib / "insights" / "metrics.json").read_text(encoding="utf-8"))
        must("分析的是账本里的 5 条", metrics["n"] == 5, str(metrics.get("n")))

    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir(parents=True)
        r = run(SCRIPTS / "insights.py", "--library", str(lib))
        must("没账本没 CSV → 提示先 content_log.py --add",
             r.returncode == 2 and "content_log.py --add" in r.stderr, r.stderr.strip()[-200:])
        r = run(REVIEW, "--library", str(lib), "--week", "2026-11-09")
        must("没账本 → review_week 提示先记账（不退 traceback）",
             r.returncode == 2 and "content_log.py --add" in r.stderr
             and "Traceback" not in r.stderr, r.stderr.strip()[-200:])


def main() -> int:
    case_add()
    case_update()
    case_sample_floor()
    case_week_bounds()
    case_broken_log()
    case_single_book()
    print()
    if fails:
        print(f"❌ {len(fails)} 条没过：" + "、".join(fails))
        return 1
    print("✅ 内容账本与周复盘 6 组负样本全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
