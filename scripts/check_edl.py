#!/usr/bin/env python3
"""CutSkills · 阶段 B 闸门：校验 edl.json。

能机器判的全判掉，判不了的明说判不了。0 error 才算 B 段跑通；warning 要人看一眼。

用法：
  check_edl.py --project "<项目目录>"        # 项目目录指 _cutskills/projects/<slug>
  check_edl.py --project ... --strict        # warning 也算失败
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

BLACKLIST_WHY = {"过渡用", "过渡", "过渡一下", "好看", "感觉好", "随便", "随便剪", "留着吧", "备用"}
VALID_SOURCE = {"library", "cc0", "generated"}
VALID_MEDIUM = {"talking", "broll", "photo", "card", "chart", "screen", "generated", "silence"}
VALID_ROLE = {"setup", "claim", "evidence", "warrant", "turn", "payoff"}
ROLE_CN = {"setup": "交代", "claim": "主张", "evidence": "举证", "warrant": "说理", "turn": "转折", "payoff": "收束"}
EDGE_TOL = 0.030        # 切边离字符边界至少这么远，否则有切字风险
EDGE_LOOSE = 0.200      # 理想上限
MEDIUM_WINDOW = 40.0    # 40s 内必须有介质变化
# 证据段里的「泛述」词：说了等于没说，读的人会当成没证据（盲测里三个人都指了这一类）
VAGUE_EVIDENCE = ("相当一部分", "相当相当一部分", "相当数量", "相当多", "很多", "好多", "大量",
                  "不少", "一些", "若干", "挺多", "比较多", "各种各样的", "都能", "都在做", "一直")


def h12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def tokens(lib: Path, aid: str) -> tuple[list[str], list[float], list[float]] | None:
    """字符 + 起止时间三件套。`token_bounds` 是它的薄包装。

    单要和字数统计都要**按字**来，不能按 ASR 的整句短语来——一句短语横跨 4 秒，
    两段各切一半时短语会同时落进两段，于是「这一段说了什么」看起来在重复，
    查重就成了全假阳性（实测：#6「也做了相当」+ #7「一部分的商业项目」被判成同句重复）。
    """
    d = lib / "_cutskills" / "cache" / "asr"
    if not d.exists():
        return None
    key = h12(aid)
    idx = lib / "_cutskills" / "index.json"
    if idx.exists():
        try:
            for rec in json.loads(idx.read_text()).get("assets", []):
                if rec.get("id") == aid and rec.get("legacy_key"):
                    key = rec["legacy_key"]
                    break
        except json.JSONDecodeError:
            pass
    import re as _re
    chunks = []
    for f in d.glob(f"{key}-v3c*.json"):
        m = _re.search(r"-v3c(\d+)\.json$", f.name)
        if m:
            chunks.append((int(m.group(1)), f))
    chars: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    if chunks:
        for off, f in sorted(chunks):
            try:
                raw = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            ts, toks = raw.get("timestamps") or [], raw.get("tokens") or []
            if not ts or len(ts) != len(toks):
                continue
            st = [float(t) + off for t in ts]
            en = [(st[i + 1] if i + 1 < len(st) else st[i] + 0.3) for i in range(len(st))]
            chars += [str(x) for x in toks]
            starts += [round(x, 3) for x in st]
            ends += [round(x, 3) for x in en]
        return (chars, starts, ends) if starts else None
    f = d / f"{h12(aid)}.json"
    if not f.exists():
        return None
    try:
        raw = json.loads(f.read_text())
    except json.JSONDecodeError:
        return None
    ts, toks = raw.get("timestamps") or [], raw.get("tokens") or []
    if not ts or len(ts) != len(toks):
        return None
    st = [float(t) for t in ts]
    en = [(st[i + 1] if i + 1 < len(st) else st[i] + 0.3) for i in range(len(st))]
    return ([str(x) for x in toks], [round(x, 3) for x in st], [round(x, 3) for x in en])


def token_bounds(lib: Path, aid: str) -> tuple[list[float], list[float]] | None:
    """从 ASR 缓存取字符级 [起点, 终点]。切片过的文件要把各片按偏移拼起来。

    缓存是按「首次建索引时的路径」命名的（legacy_key）。素材改过名之后，
    直接拿当前路径去哈希会一个缓存也找不到——所以要回索引里查一次账。
    """
    t = tokens(lib, aid)
    return (t[1], t[2]) if t else None


def char_spans(starts: list[float], ends: list[float]) -> list[tuple[float, float]]:
    """一个字占的区间：从它起点到下一个字起点，但超过 0.55s 的间隔就当停顿，不算字。"""
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else s + 0.3
        turns_to_gap = (nxt - s) > 0.55
        out.append((s, s + 0.5 if turns_to_gap else nxt))
    return out


def edge_report(edge: float, starts: list[float], ends: list[float]) -> tuple[bool, float]:
    """返回（是否切在字中间，离最近字边界的秒数）。"""
    spans = char_spans(starts, ends)
    inside = any(s + 0.006 < edge < e - 0.006 for s, e in spans)
    dist = min(min(abs(edge - s) for s in starts),
               min(abs(edge - e) for _, e in spans))
    return inside, dist


def side_room(bounds, edge: float, direction: int) -> float:
    """切边这一侧还有多少空隙可用（out 看下一个字起点，in 看上一个字结束）。"""
    if not bounds:
        return 0.0
    starts, ends = bounds
    if direction > 0:
        nxt = min([s for s in starts if s >= edge - 1e-6], default=edge + 9)
        return max(0.0, nxt - edge)
    spans = char_spans(starts, ends)
    pe = max((e for s, e in spans if s < edge - 1e-6), default=0.0)
    return max(0.0, edge - pe)


def pad_edge(bounds, t: float, direction: int, limit: float, dur: float) -> float:
    """把切边放到最近的停顿里，不硬挤。direction=-1 是 in 边，+1 是 out 边。"""
    if not bounds:
        return round(max(0.0, min(dur, t + direction * limit)), 3)
    starts, ends = bounds
    spans = char_spans(starts, ends)
    if direction > 0:                      # 往后留：到下一个字起点之前
        nxt = min([s for s in starts if s >= t - 1e-6], default=t + 9)
        room = nxt - t
        pad = min(limit, max(0.0, room - 0.03)) if room >= 0.12 else max(0.0, room - 0.02)
        v = t + pad
    else:                                  # 往前留：到上一个字结束之后
        prev = [(s, e) for s, e in spans if s < t - 1e-6]
        pe = max((e for _, e in prev), default=0.0)
        room = t - pe
        pad = min(limit, max(0.0, room - 0.03)) if room >= 0.09 else max(0.0, room - 0.02)
        v = t - pad
    return round(max(0.0, min(dur, v)), 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--library", default=None, help="默认从项目路径往上找")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    proj = Path(a.project).expanduser().resolve()
    lib = Path(a.library).expanduser().resolve() if a.library else proj.parents[2]
    errors: list[str] = []
    warns: list[str] = []
    info: list[str] = []

    idx_f = lib / "_cutskills" / "index.json"
    if not idx_f.exists():
        print(f"索引不存在：{idx_f}", file=sys.stderr)
        return 2
    index = {x["id"]: x for x in json.loads(idx_f.read_text())["assets"]}

    intent_f = proj / "intent.json"
    intent = json.loads(intent_f.read_text()) if intent_f.exists() else {}
    for k in ("audience", "promise", "duration_s", "aspect"):
        if not intent.get(k):
            errors.append(f"意图缺 `{k}`：没定这个就剪，等于没有靶子")

    edl_f = proj / "edl.json"
    if not edl_f.exists():
        print(f"edl.json 不存在：{edl_f}", file=sys.stderr)
        return 2
    edl = json.loads(edl_f.read_text())
    segs = edl.get("segments") or []
    if not segs:
        errors.append("segments 为空：EDL 还没写")

    # ---- 参数与风格包：写错了要到 C 段渲染时才炸，不如现在炸
    cs = edl.get("cut_source")
    if cs not in {"speech", "scene", "manual"}:
        errors.append(f"cut_source「{cs}」不在约定值里（speech / scene / manual）")
    elif cs != "speech":
        warns.append(f"cut_source={cs}：B 段只实现了 speech 的切点规则，现在的「切边吸附字界」对它不成立")
    vs = edl.get("visual_selector")
    if vs not in {"single_cam_talking", "speaker_switch", "multicam_pick", "broll_match"}:
        errors.append(f"visual_selector「{vs}」不在约定值里")
    elif vs != "single_cam_talking":
        warns.append(f"visual_selector={vs}：v1 只实现了单机位口播，画面选择规则未校验")
    pack = edl.get("style_pack") or "talking-head-v1"
    pack_f = Path(__file__).resolve().parent.parent / "styles" / f"{pack}.yaml"
    if not pack_f.exists():
        errors.append(f"风格包不存在：styles/{pack}.yaml（C 段要拿它定字幕与节奏参数）")

    # 「不许出现的内容」是意图四项之一，但以前没有任何东西用到它
    avoid = [str(x).strip() for x in (intent.get("avoid") or []) if str(x).strip()]

    # ---- 逐段
    t = 0.0
    rows = []
    prev_asset, prev_out = None, None
    bounds_cache: dict[str, list[float] | None] = {}
    for i, s in enumerate(segs, 1):
        tag = f"#{i}"
        aid = s.get("asset")
        a_rec = index.get(aid)
        if not a_rec:
            errors.append(f"{tag} 素材不在索引里：{aid}")
            continue
        dur = a_rec.get("duration") or 0
        try:
            tin, tout = float(s.get("in")), float(s.get("out"))
        except (TypeError, ValueError):
            errors.append(f"{tag} in/out 不是数字")
            continue
        if tout <= tin:
            errors.append(f"{tag} out ≤ in（{tin}–{tout}）")
            continue
        seg_d = tout - tin
        if seg_d < 0.4:
            warns.append(f"{tag} 只有 {seg_d:.2f}s，可能是误触的闪帧")
        if tin < -0.01 or tout > dur + 0.05:
            errors.append(f"{tag} 超出素材范围：{tin}–{tout} / 素材 {dur:.2f}s")

        medium = s.get("medium")
        if medium not in VALID_MEDIUM:
            warns.append(f"{tag} medium「{medium}」不在约定值里")
        if medium == "silence":
            if s.get("source") != "generated" and medium == "silence":
                pass
        if s.get("source") not in VALID_SOURCE:
            errors.append(f"{tag} source「{s.get('source')}」必须是 {sorted(VALID_SOURCE)}")

        why = (s.get("why") or "").strip()
        if not why:
            errors.append(f"{tag} 没有 why")
        elif why in BLACKLIST_WHY:
            errors.append(f"{tag} why 是套话「{why}」——换成这条推进了什么")
        elif len(why) < 6:
            warns.append(f"{tag} why 太短：「{why}」")

        if medium == "talking" and not s.get("evidence"):
            warns.append(f"{tag} 口播段没有 evidence（回溯原文），后面无法核对取舍")

        # ---- 证据段是不是「泛述」：有段、没数、没名字，读者会当成没证据
        if s.get("role") == "evidence":
            ev_txt = str(s.get("evidence") or "")
            vague = [w for w in VAGUE_EVIDENCE if w in ev_txt]
            if vague and not re.search(r"\d|几\s*\d|十\s*\d", ev_txt):
                info.append(f"{tag} 举证段里有「{vague[0]}」但没有任何数字或具体名字"
                            "——盲测里读者会当成没证据（命中不代表错，看一眼值不值得补一个具体数）")

        # ---- 结构层：节奏能靠时长判，论证只能靠 beat / role 判
        role, beat = s.get("role"), s.get("beat")
        if role is not None and role not in VALID_ROLE:
            errors.append(f"{tag} role「{role}」不在约定值里：{'/'.join(sorted(VALID_ROLE))}")
        if beat is not None and not isinstance(beat, int):
            errors.append(f"{tag} beat 要是整数（论点号），现在是 {beat!r}")

        # avoid 是字面匹配（机器只能做到这）：命中不一定错（可能是“价格”这个词要回避但关键词是别的），
        # 但必须被人看一眼，否则那一条约束等于没提。
        for term in avoid:
            if term in why or term in str(s.get("evidence") or ""):
                warns.append(f"{tag} 出现被列为「不许出现」的「{term}」——确认是引述还是真的用了")

        # ---- 切边吸附字符边界
        if a_rec.get("transcript") and medium in {"talking", None}:
            if aid not in bounds_cache:
                bounds_cache[aid] = token_bounds(lib, aid)
            tb = bounds_cache[aid]
            if tb:
                starts, ends = tb
                for edge, name, d in ((tin, "in", -1), (tout, "out", 1)):
                    inside, dist = edge_report(edge, starts, ends)
                    room = side_room((starts, ends), edge, d)
                    if inside and dist > EDGE_TOL:
                        errors.append(f"{tag} {name}={edge:.3f} 切在一个字中间（离最近字界 {dist*1000:.0f}ms）")
                    elif dist < EDGE_TOL and room >= 0.12:
                        warns.append(f"{tag} {name} 贴着字界（{dist*1000:.0f}ms），"
                                     f"旁边有 {room*1000:.0f}ms 空隙没用上")
                    elif dist > 0.35:
                        info.append(f"{tag} {name} 离字界 {dist*1000:.0f}ms，确认是刻意留白")
            else:
                warns.append(f"{tag} 拿不到字符级时间戳，切边吸附未校验：{aid}")

        # ---- 分轨音频（口播画面换成 B-roll 时，声音继续走原口播）
        au = s.get("audio")
        if au:
            a_rec2 = index.get(au.get("asset"))
            if not a_rec2:
                errors.append(f"{tag} audio.asset 不在索引里：{au.get('asset')}")
            else:
                try:
                    ain, aout = float(au.get("in")), float(au.get("out"))
                except (TypeError, ValueError):
                    errors.append(f"{tag} audio.in/out 不是数字")
                    ain = aout = 0.0
                if aout <= ain:
                    errors.append(f"{tag} audio.out ≤ audio.in")
                elif abs((aout - ain) - seg_d) > 0.15:
                    warns.append(f"{tag} 画面 {seg_d:.2f}s 与声音 {aout-ain:.2f}s 不等长，会漂")
                if aout > (a_rec2.get("duration") or 0) + 0.05:
                    errors.append(f"{tag} audio 超出素材范围")
        elif medium in {"broll", "photo", "card", "chart", "screen"}:
            info.append(f"{tag} {medium} 段没有 audio 指定：会用这段自己的环境音")

        # 只有「顺着往后还重叠」才算回扣。往前倒退 60 秒再回来，是倒序剪辑（开场搬结尾），
        # 不是重复——以前的判据把 A 版开场判成了回扣。
        if (aid == prev_asset and prev_out is not None and tin < prev_out
                and prev_out - tin < 1.0):
            warns.append(f"{tag} 与上一段在同一素材内重叠（{prev_out:.2f} > {tin:.2f}）——是刻意回扣吗")

        rows.append({"n": i, "asset": aid, "in": round(tin, 3), "out": round(tout, 3),
                     "dur": round(seg_d, 3), "medium": medium, "out_start": round(t, 3),
                     "source": s.get("source"), "why": why,
                     "beat": beat, "role": role,
                     "evidence": str(s.get("evidence") or "")})
        t += seg_d
        prev_asset, prev_out = aid, tout

    total = round(t, 2)

    # ---- 结构层：一条片子有没有论点、每个论点有没有证据
    titles = {b.get("n"): (b.get("title") or "") for b in (edl.get("beats") or [])}
    beat_ids = sorted({r["beat"] for r in rows if r.get("beat") is not None})
    struct_lines = []
    if rows and not beat_ids:
        warns.append("所有段都没有 beat/role：结构层是空的——现在能判节奏，判不了「有没有论点、论点有没有证据」"
                     "（plan 里给每段加 beat 与 role，见 references/plan-templates.md）")
    elif beat_ids:
        loose = [r["n"] for r in rows if r.get("beat") is None]
        if loose:
            warns.append("这些段没归到任何论点：" + "、".join(f"#{n}" for n in loose)
                         + "——要么归一个，要么承认它是纯过渡")
        holes = [n for n in range(1, max(beat_ids) + 1) if n not in beat_ids]
        if holes:
            info.append(f"论点号不连续，缺：{holes}")
        for n in beat_ids:
            own = [r for r in rows if r.get("beat") == n]
            secs = round(sum(r["dur"] for r in own), 2)
            has_claim = any(r["role"] == "claim" for r in own)
            has_ev = any(r["role"] == "evidence" for r in own)
            ev_first = has_ev and own[0]["role"] == "evidence"
            struct_lines.append({"n": n, "title": titles.get(n, ""), "segs": [r["n"] for r in own],
                                 "secs": secs, "has_ev": has_ev, "ev_first": ev_first})
            info.append(f"论点 {n}（{titles.get(n) or '无标题'}）：{len(own)} 段 / {secs}s / "
                        + ("有证据" if has_ev else "⚠ 无 evidence 段"))
            if has_claim and not has_ev:
                warns.append(f"论点 {n} 只有主张没有证据（段 {'、'.join('#' + str(r['n']) for r in own)}）"
                             "——观众凭什么信")
            if secs < 3:
                warns.append(f"论点 {n} 只有 {secs}s，展开不了")
            if ev_first and has_claim:
                info.append(f"论点 {n} 先给证据后给主张 —— 确认是刻意的（先看东西再说结论）")
            if has_ev and has_claim and not any(r["role"] == "warrant" for r in own):
                info.append(f"论点 {n} 有证据、没有说理段（warrant）：证据在，但没说为什么它支持这个结论"
                            "——机器判不了这句话该不该有，盲测会给答案（blind_test.py）")

        # ---- 同一句话说两遍：删重复是 B 段的主要工作量，机器以前只查了时间重叠
        def norm(t: str) -> str:
            return "".join(ch for ch in t if ch.isalnum())

        for x in range(len(rows)):
            for y in range(x + 1, len(rows)):
                a1, b1 = norm(rows[x]["evidence"]), norm(rows[y]["evidence"])
                # 同一素材相邻两段（共用字界）本来就首尾相接，不算重复
                if rows[x]["asset"] == rows[y]["asset"] and rows[y]["in"] - rows[x]["out"] < 0.5:
                    continue
                if len(a1) < 8 or len(b1) < 8:
                    continue
                if a1 == b1 or (len(a1) > 8 and a1 in b1) or (len(b1) > 8 and b1 in a1):
                    warns.append(f"#{rows[x]['n']} 与 #{rows[y]['n']} 说的是同一句话（或几乎一样）——"
                                 "留一遍就够，除非刻意回扣")
                elif difflib.SequenceMatcher(None, a1, b1).ratio() >= 0.85:
                    warns.append(f"#{rows[x]['n']} 与 #{rows[y]['n']} 的话有 {difflib.SequenceMatcher(None, a1, b1).ratio() * 100:.0f}%"
                                 "重合——确认是不是重复了")

    # ---- 全局指标
    target = intent.get("duration_s")
    if target:
        dev = (total - target) / target
        if abs(dev) > 0.30:
            errors.append(f"成片 {total:.1f}s 与目标 {target}s 差 {dev*100:+.0f}%（超过 ±30%）")
        elif abs(dev) > 0.15:
            warns.append(f"成片 {total:.1f}s 与目标 {target}s 差 {dev*100:+.0f}%（超过 ±15%）")

    talk = [r["dur"] for r in rows if r["medium"] == "talking"]
    if talk:
        med = round(statistics.median(talk), 2)
        info.append(f"口播段镜头中位数 {med}s（目标 2.0–2.8s）")
        if not 1.6 <= med <= 3.2:
            warns.append(f"口播段镜头中位 {med}s，偏离 2.0–2.8s 太多")
        for r in rows:
            if r["medium"] == "talking" and r["dur"] > 12:
                info.append(f"#{r['n']} 单镜头 {r['dur']:.1f}s（>12s，需在 why 里写明是刻意留白）")

    gen = sum(r["dur"] for r in rows if r["source"] == "generated")
    if total and gen / total > 0.20:
        warns.append(f"生成物占 {gen/total*100:.0f}%（>20%），片子有变模板的风险")

    # ---- 40s 介质变化
    # broll 连段按**素材**算，不按 medium 算：整片口播盖施工现场画面时，medium 从头到尾
    # 都是 broll，按 medium 算这个指标永远是 0（等于这条规则不存在），而真正的疲劳来自
    # 同一支素材连着用了几十秒。
    if rows:
        def _key(r: dict) -> str:
            return f"broll:{r['asset']}" if r["medium"] == "broll" else r["medium"]

        worst = 0.0
        start = rows[0]["out_start"]
        cur = _key(rows[0])
        for r in rows[1:]:
            if _key(r) != cur:
                worst = max(worst, r["out_start"] - start)
                start, cur = r["out_start"], _key(r)
        worst = max(worst, total - start)
        info.append(f"最长同质画面连续 {worst:.1f}s（上限 {MEDIUM_WINDOW:.0f}s；"
                    "broll 按素材算）")
        if worst > MEDIUM_WINDOW:
            warns.append(f"有 {worst:.1f}s 没换过画面类型或素材，观众会疲")

    # ---- 确认点留痕：EDL 有段落，就该有一条「当时人说了什么」的记录
    if rows:
        dec = proj / "decisions.md"
        n_dec = len([ln for ln in dec.read_text().splitlines()
                     if ln.startswith("| 20")]) if dec.exists() else 0
        if n_dec == 0:
            warns.append("确认点没有留痕：decisions.md 里一条记录也没有——人确认过就补一行"
                         "（log_decision.py --said 原话 --did 动作）")
        else:
            info.append(f"确认记录 {n_dec} 条（decisions.md）")
            # 上次确认时的形状记在 decisions.md 的结论格（<sub>N 段 / X.Xs</sub>）；
            # 改到这个程度，人再看一眼比机器说「通过」有用
            shapes = re.findall(r"<sub>(\d+) 段 / ([\d.]+)s</sub>", dec.read_text())
            if shapes:
                n0, s0 = int(shapes[-1][0]), float(shapes[-1][1])
                d_seg = abs(len(rows) - n0) / max(n0, 1)
                d_sec = abs(total - s0) / max(s0, 1e-6)
                if d_seg > 0.20 or d_sec > 0.15:
                    warns.append(f"自上次确认后变成 {len(rows)} 段 / {total:.1f}s（当时 {n0} 段 / {s0:.1f}s，"
                                 f"段数 {d_seg * 100:+.0f}% 时长 {d_sec * 100:+.0f}%）——该再给用户看一次稿子"
                                 "（log_decision.py 记下这一轮）")

    # ---- 画面里的第三方字样：库里已经认过一遍，进片前必须在 EDL 上再提一次
    # 商用片里出现别人的品牌、公司名、手机号，等于替别人打广告；有的是竞品，有的是别人的客户。
    # 机器只能读到 L2 当时写的 flags，具体露多少还是要人看一眼。
    BRAND_HIT = re.compile(r"PPG|东鹏|MYLANDS|格力|GREE|别克|苹果|门窗|EOD|年年机电|手机|电话|"
                           r"logo|标志|品牌字样|第三方|打码|水印")
    brand_hits: list[str] = []
    for r in rows:
        rec = index.get(r["asset"]) or {}
        for g in rec.get("segments") or []:
            if g.get("out", 0) <= r["in"] + 0.05 or g.get("in", 0) >= r["out"] - 0.05:
                continue
            flags = [f for f in (g.get("flags") or []) if BRAND_HIT.search(str(f))]
            if flags:
                brand_hits.append(f"#{r['n']} {g.get('tag') or '—'}：{'；'.join(flags)}")
    if brand_hits:
        warns.append(f"{len(brand_hits)} 段的画面里有第三方字样或需打码的区域，"
                     "商用前必须逐一看（裁切 / 打码 / 换段）：" + " / ".join(brand_hits[:3]))

    # ---- 报告
    status = "FAIL" if errors else ("WARN" if warns else "OK")
    lines = [f"# EDL 校验 · {edl.get('slug','?')}", "",
             f"**{status}** · {len(rows)} 段 · 成片 {total}s"
             + (f" / 目标 {target}s" if target else "") + f" · 生成物 {gen:.1f}s", ""]
    if avoid:
        lines += [f"**不许出现**：{'、'.join(avoid)}（字面检查，需人复核）", ""]
    if struct_lines:
        lines += ["## 结构", "", "| 论点 | 段 | 时长 | 证据 |", "|---|---|---|---|"]
        for b in struct_lines:
            ev = "有" if b["has_ev"] else "⚠ 没有"
            if b["ev_first"]:
                ev += "（先证据后主张）"
            lines.append(f"| {b['n']}. {b['title'] or '—'} | "
                         f"{'、'.join('#' + str(x) for x in b['segs'])} | {b['secs']}s | {ev} |")
        lines += [""]
    if errors:
        lines += ["## 必须修（error）"] + [f"- {e}" for e in errors] + [""]
    if warns:
        lines += ["## 看一眼（warning）"] + [f"- {w}" for w in warns] + [""]
    if info:
        lines += ["## 指标"] + [f"- {i2}" for i2 in info] + [""]
    lines += ["## 时间线", "", "| # | 成片 | 素材 | 素材内 | 时长 | 介质 | 论点 | 角色 | 来源 | 理由 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['n']} | {r['out_start']:.2f}s | {r['asset']} | "
                     f"{r['in']:.2f}–{r['out']:.2f} | {r['dur']:.2f}s | {r['medium']} | "
                     f"{r.get('beat') if r.get('beat') is not None else '—'} | "
                     f"{ROLE_CN.get(r.get('role'), '—')} | "
                     f"{r['source']} | {r['why'].replace('|', '/')} |")
    (proj / "edl-report.md").write_text("\n".join(lines) + "\n")

    print(f"{status}  {len(rows)} 段 / {total}s  ·  error {len(errors)}  warning {len(warns)}")
    for e in errors:
        print(f"  error: {e}")
    for w in warns:
        print(f"  warn : {w}")
    for i2 in info:
        print(f"  info : {i2}")
    print(f"报告 → {proj/'edl-report.md'}")

    if errors:
        return 1
    if a.strict and warns:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
