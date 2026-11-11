#!/usr/bin/env python3
"""看板回传的「【制作看板评论…」→ 改写 timeline.json。

看板是视图，真值在这里落地。四类改动都吃：
  时间线  `S03：{"gap":0.1,"fixed":4.2,"clips":{"ov":[0.6,2.4]}}`
  顺序    `镜头顺序：S01 → S03 → S02 …`
  画布    `S03：[["ttl",50,30,"柒造","",10]]`
  连线    `S03 ← 动效风格：…，说明：…`
评论里没法自动落的（「整片节奏太快」这类）单独列出来，写进 decisions.md 等人定。

用法：
  pbpaste | python3 scripts/board_patch.py --project <项目> --dry-run    # 先看 diff
  pbpaste | python3 scripts/board_patch.py --project <项目>              # 真写
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_decision import append_row  # noqa: E402

FIXED_RE = re.compile(r"^\s*(S\d+[a-z]?)\s*[：:]\s*(\{.*\})\s*$")
CANVAS_RE = re.compile(r"^\s*(S\d+[a-z]?)\s*[：:]\s*(\[.*\])\s*$")
LINK_RE = re.compile(r"^\s*(S\d+[a-z]?)\s*←\s*(.+)$")
ORDER_RE = re.compile(r"镜头顺序\s*[：:]\s*(.+)$")


def loose_json(s: str):
    """看板吐出的是 JS 字面量：单引号、尾逗号、花括号里的裸键。按 JSON 宽容地读。"""
    t = s.strip()
    t = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', t)   # 裸键 → 带引号
    t = t.replace("'", '"')                                                  # 单引号 → 双引号
    t = re.sub(r",(\s*[}\]])", r"\1", t)                                     # 尾逗号
    return json.loads(t)


def parse(text: str) -> dict:
    got = {"timeline": {}, "canvas": {}, "links": {}, "order": None, "media": {},
           "labels": {}, "other": []}
    in_order = False
    section = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("【"):
            continue
        # 分区标题：后面的 `S07：xxx` 才能分辨是素材、备注还是画布改动
        if not re.match(r"^\s*S\d", line) and "：" not in line[:6]:
            if "时间线改动" in line:
                section, in_order = "timeline", False
                continue
            if "画布改动" in line:
                section, in_order = "canvas", False
                continue
            if "已到位的素材" in line:
                section, in_order = "media", False
                continue
            if "素材画布连线" in line:
                section, in_order = "links", False
                continue
            if "备注名" in line:
                section, in_order = "labels", False
                continue
            if line.startswith("整片") or "镜头评论" in line or line.startswith("请按下面"):
                section, in_order = "other", False
                continue
        m = ORDER_RE.search(line)
        if m:
            ids = re.findall(r"S\d+[a-z]?", m.group(1))
            if ids:
                got["order"] = ids
            in_order = True
            continue
        m = FIXED_RE.match(line)
        if m:
            try:
                got["timeline"].setdefault(m.group(1), {}).update(loose_json(m.group(2)))
            except json.JSONDecodeError:
                got["other"].append(f"读不懂的时间线行：{line.strip()}")
            in_order = False
            continue
        m = CANVAS_RE.match(line)
        if m:
            try:
                got["canvas"][m.group(1)] = loose_json(m.group(2))
            except json.JSONDecodeError:
                got["other"].append(f"读不懂的画布行：{line.strip()}")
            in_order = False
            continue
        if section == "media":
            m2 = re.match(r"^\s*(S\d+[a-z]?)\s*[：:]\s*(\S.*)$", line)
            if m2:
                got["media"][m2.group(1)] = m2.group(2).strip()
                continue
        if section == "labels":
            m2 = re.match(r"^\s*(S\d+[a-z]?)\s*[：:]\s*(.+)$", line)
            if m2:
                got["labels"][m2.group(1)] = m2.group(2).strip()
                continue
        m = LINK_RE.match(line)
        if m:
            got["links"][m.group(1)] = m.group(2).strip()
            in_order = False
            continue
        if in_order and line.strip():
            ids = re.findall(r"S\d+[a-z]?", line)
            if ids and got["order"] is not None:
                got["order"] += ids
                continue
        if line.strip():
            got["other"].append(line.strip())
    if got["order"]:
        seen, dedup = set(), []
        for i in got["order"]:
            if i not in seen:
                seen.add(i)
                dedup.append(i)
        got["order"] = dedup
    return got


def el_dur(el: dict) -> float:
    if el.get("kind") == "clip":
        return round(float(el["out"]) - float(el["in"]), 3)
    return round(float(el.get("dur") or 0.0), 3)


def set_dur(el: dict, dur: float) -> None:
    """改时长只能改素材的出点，不能凭空改一个 dur 字段——clip 的时长是 out−in 算出来的。"""
    if el.get("kind") == "clip":
        el["out"] = round(float(el["in"]) + dur, 3)
    else:
        el["dur"] = round(dur, 3)


def apply(tl: dict, got: dict) -> tuple[dict, list[str]]:
    els = tl.get("elements") or []
    notes: list[str] = []
    by_id = {f"S{int(e.get('n') or i):02d}": e for i, e in enumerate(els, 1)}
    # 每个格的绝对起点（改完顺序要重算）
    def starts() -> dict[str, float]:
        out, t = {}, 0.0
        for e in els:
            n = f"S{int(e.get('n') or 0):02d}"
            if (e.get("track") or "video") == "overlay":
                continue
            out[n] = round(t, 3)
            t = round(t + el_dur(e), 3)
        return out

    st_before = starts()

    # 1) 顺序
    if got["order"]:
        unknown = [i for i in got["order"] if i not in by_id]
        if unknown:
            notes.append(f"顺序里有不存在的镜头 {unknown}，跳过")
        order = [i for i in got["order"] if i in by_id]
        missing = [i for i in by_id if i not in order]
        if missing:
            notes.append(f"顺序里漏了 {missing}，按原顺序补在末尾")
            order += [i for i in [f"S{int(e.get('n') or 0):02d}" for e in els] if i in missing]
        ov = [e for e in els if (e.get("track") or "video") == "overlay"]
        els[:] = [by_id[i] for i in order] + ov
        notes.append("镜头顺序：" + " → ".join(order))

    # 2) 时长 / gap / clips
    for sid, patch in got["timeline"].items():
        el = by_id.get(sid)
        if not el:
            notes.append(f"{sid} 不在时间轴里")
            continue
        if patch.get("fixed") is not None:
            old = el_dur(el)
            set_dur(el, float(patch["fixed"]))
            notes.append(f"{sid} 时长 {old:.2f}s → {el_dur(el):.2f}s")
        if patch.get("gap") is not None:
            g = float(patch["gap"])
            if g > 0:
                sil = {"n": max((int(e.get("n") or 0) for e in els), default=0) + 1,
                       "kind": "silence", "track": "video", "dur": round(g, 3),
                       "why": "看板里拖出来的空档"}
                idx = els.index(el)
                els.insert(idx, sil)
                notes.append(f"{sid} 前插入 {g:.2f}s 空档（silence 格）")
            elif g < 0:
                notes.append(f"{sid} 要求重叠 {-g:.2f}s —— 本链路时间轴不支持重叠，按 0 处理")
        if patch.get("clips"):
            for track, val in patch["clips"].items():
                if track != "ov":
                    # br / rec / a 是看板里的并行轨道；本管线只有单视频轨 + 一层 overlay，
                    # 没有第二视频层，也没有「另一条轨用哪个素材」的指针（T-011）。
                    # 旧实现把 br 也 append 成一块 overlay，是错的；现在只提示，不改时间轴。
                    notes.append(f"{sid} 的 {track} 回写不了：看板 {track} 是并行轨道，"
                                 f"本管线单视频轨、没有第二视频层/素材指针；只支持 ov")
                    continue
                off, dur = float(val[0]), float(val[1])
                base = starts().get(sid, 0.0)
                at, dur = round(base + off, 3), round(dur, 3)
                # 读这一镜里已有的 overlay 再改它：append 会渲出两块叠在一起，
                # 而且新块没有 template（check_timeline 直接报「模板 None」）。
                inside = [e for e in els
                          if (e.get("track") or "video") == "overlay"
                          and base - 0.02 <= float(e.get("at") or 0.0) < base + el_dur(el) - 0.02]
                if not inside:
                    notes.append(f"{sid} 时间轴上没有叠加层可调——先加卡片，再回看板调时间")
                    continue
                ov_el = min(inside, key=lambda e: abs(float(e.get("at") or 0.0) - at))
                old = f"{float(ov_el.get('at') or 0.0):.2f}s/{float(ov_el.get('dur') or 0.0):.2f}s"
                ov_el["at"], ov_el["dur"] = at, dur
                notes.append(f"{sid} 叠加层 {old} → {at:.2f}s/{dur:.2f}s（改原块，不新增）")
                if len(inside) > 1:
                    notes.append(f"{sid} 有 {len(inside)} 块叠加层，只改了最近的一块；其余用 board_check 对账")
                if off < -0.02 or off + dur > el_dur(el) + 0.02:
                    notes.append(f"{sid} 叠加层超出本镜（{off:.2f}s + {dur:.2f}s vs 镜长 {el_dur(el):.2f}s）")

    # 3) 画布元素 → 存起来；渲染不认 m，认 cards。能对上的大字顺手改文字。
    for sid, m in got["canvas"].items():
        el = by_id.get(sid)
        if not el:
            notes.append(f"{sid} 不在时间轴里（画布改动）")
            continue
        el["board_m"] = m
        texts = [it[3] for it in m if isinstance(it, list) and len(it) > 3
                 and it[0] == "ttl" and isinstance(it[3], str) and it[3].strip()]
        if texts and el.get("card"):
            el["card"]["text"] = texts[0]
            notes.append(f"{sid} 大字改成「{texts[0]}」")
        else:
            notes.append(f"{sid} 画布元素已记录（board_m，{len(m)} 个），渲染暂不消费")

    # 4) 连线 → ref（这是渲染端唯一认的风格指针）
    for sid, txt in got["links"].items():
        el = by_id.get(sid)
        if not el:
            notes.append(f"{sid} 不在时间轴里（连线）")
            continue
        refs = [r for r in (el.get("ref") or [])]
        if txt not in refs:
            refs.append(txt)
        el["ref"] = refs
        notes.append(f"{sid} 连线 → ref")

    # 5) 已到位的素材 / 镜头备注名：只记，不改编号
    for sid, name in got.get("media", {}).items():
        if sid in by_id:
            by_id[sid]["board_media"] = name
            notes.append(f"{sid} 素材已到位：{name}")
    for sid, lab in got.get("labels", {}).items():
        if sid in by_id:
            by_id[sid]["board_label"] = lab
            notes.append(f"{sid} 备注名：{lab}")

    # 重算起点与总时长。**镜号不重排**：S 号是身份，跟着元素走，重排后下一份看板还用同一套号。
    t = 0.0
    for e in els:
        if (e.get("track") or "video") == "overlay":
            continue
        e["_start"] = round(t, 3)
        t = round(t + el_dur(e), 3)
    total = round(t, 3)
    tl["duration_s"] = total
    st_after = starts()
    moved = [f"{k} {st_before.get(k, 0):.2f}→{v:.2f}s" for k, v in st_after.items()
             if abs(st_before.get(k, -1) - v) > 0.02]
    if moved:
        notes.append("起点变了：" + "，".join(moved[:8]) + ("…" if len(moved) > 8 else ""))
    return tl, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--file", default=None, help="评论文本文件；不给就读 stdin")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    text = Path(a.file).read_text() if a.file else sys.stdin.read()
    if "【制作看板评论" not in text:
        print("这段不是看板回传的文本（要以「【制作看板评论」开头）", file=sys.stderr)
        return 2
    got = parse(text)
    tp = proj / "timeline.json"
    tl = json.loads(tp.read_text())
    before = round(float(tl.get("duration_s") or 0), 3)
    tl2, notes = apply(tl, got)

    print(f"读到：时间线 {len(got['timeline'])} 条 · 画布 {len(got['canvas'])} 条 · "
          f"连线 {len(got['links'])} 条 · 素材 {len(got.get('media', {}))} 条 · "
          f"顺序 {'有' if got['order'] else '无'}")
    for n in notes:
        print("  ·", n)
    print(f"总时长 {before}s → {tl2['duration_s']}s（{len(tl2['elements'])} 格）")
    if got["other"]:
        print("落不了、要你定的：")
        for o in got["other"][:10]:
            print("  ？", o)

    if a.dry_run:
        print("（dry-run，没写）")
        return 0
    tp.write_text(json.dumps(tl2, ensure_ascii=False, indent=1) + "\n")
    append_row(proj, "看板回传",
               f"{len(notes)} 处：{notes[0] if notes else '无'}；"
               f"总长 {before}s→{tl2['duration_s']}s，已写入 timeline.json",
               "accepted")
    print(f"已写 {tp}")
    print("下一步：python3 scripts/check_timeline.py --project … && "
          "python3 scripts/board_from_timeline.py --project …（重新出看板）")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import check_timeline
        lib = proj.parent.parent.parent
        probs = check_timeline.check(tl2, check_timeline.load_index(lib))
        errs = [m for lv, m in probs if lv == "error"]
        print(f"check_timeline：{'🔴 ' + str(len(errs)) + ' 错' if errs else '🟢 通过'}")
        for e in errs[:6]:
            print("  ", e)
        return 1 if errs else 0
    except Exception as e:  # 校验器坏了不该把补丁吞掉
        print(f"（自动跑 check_timeline 失败：{e}，请手动跑）")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
