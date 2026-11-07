#!/usr/bin/env python3
"""CutSkills · 批量重命名素材（带映射、可回滚、按内容指纹对账）。

为什么可以改：改名之后「这是哪个文件」不再靠路径，而靠 index.json 里的 `uid`
（文件内容指纹）。改完跑一次 `index_assets.py --stage finish`，索引自己认回来。
所以改名不是风险操作，前提是**先有索引、改名走这个脚本**（它会同步写映射）。

命名规则（顺序即阅读顺序）：

    YYMMDD_批次_内容_级别_原编号.EXT
    261017_地面施工_打磨特写_必用_IMG_1042.MOV

- 日期取容器里的拍摄时间；没有就写 `无日期`——不猜
- 批次取一级目录名（去掉「素材/视频/照片」这类后缀）
- 内容只有跑过 L2 的素材才有，没有就跳过这一格（宁缺不编）
- 级别是 ①必用 / ②备用 / ③不用 的文字版
- 原编号保留在末尾，任何时候都能对回相机/手机上的原始文件

默认只演练不落地。真改：加 `--apply`。改错了：`--revert`。

用法：
  rename_assets.py --library "<库>"                 # 演练，打印将要改成什么
  rename_assets.py --library "<库>" --apply         # 真改（同时更新索引与映射）
  rename_assets.py --library "<库>" --revert        # 撤销最近一次
  rename_assets.py --library "<库>" --apply --fix-projects   # 顺带改项目里引用的路径
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BAD = '\\/:*?"<>|\n\r\t'
GRADE = {"must-use": "必用", "backup": "备用", "do-not-use": "不用"}
BATCH_SUFFIX = ("素材", "视频", "照片", "影像")
MAX_NAME = 110          # 留余量，APFS 单段上限 255 字节，中文一个字 3 字节


def clean(s: str, limit: int = 0) -> str:
    s = s.strip()
    for ch in BAD:
        s = s.replace(ch, "-")
    s = s.replace("：", "-").replace("，", "").replace("。", "")
    s = re.sub(r"\s+", "", s)
    return s[:limit] if limit else s


def batch_tag(aid: str) -> str:
    if "/" not in aid:
        return "根目录"
    top = aid.split("/")[0]
    for suf in BATCH_SUFFIX:
        if top.endswith(suf) and len(top) > len(suf):
            top = top[: -len(suf)]
            break
    return clean(top, 8) or "批次"


def content_tag(rec: dict) -> str:
    """只有 L2 说过话的素材才给内容格。没跑 L2 的留空——编一个比空着更糟。

    优先用 L2 里人写下的 `tag`（2–6 字的名词短语，比如「自流平倒料」「色卡墙」）。
    没有 tag 才退回 summary 的第一个分句截断——那条路会截出「低机位中全景拍室」这种
    断在半截上的片段，能不用就不用。
    """
    segs = rec.get("segments") or []
    for want in ("must-use", "backup", "do-not-use"):
        for seg in segs:
            if seg.get("tag") and seg.get("grade") == want:
                return clean(str(seg["tag"]), 8)
    if any(seg.get("tag") for seg in segs):
        return ""
    for seg in rec.get("segments") or []:
        for key in ("summary", "action"):
            v = seg.get(key)
            if v:
                # 先按标点切出第一个分句，再截字——否则「地面打磨特写，机器与基面」
                # 会被压成「地面打磨特写机器与基」这种断在半句上的样子
                first = re.split(r"[，。、；：！？,.;:!?（）()「」]", str(v))[0]
                return clean(first, 8)
    return ""


def date_tag(rec: dict) -> str:
    """拍摄日期：文件名里的日期 > 容器 creation_time > 无。

    容器时间不可全信：iPhone/相机时钟可能被设错。柒造这一库的地面施工素材，
    文件名明明是 20261017，容器里却写着 2024-07-29——文件名是当时起的，更可信。
    """
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", Path(rec["id"]).name)
    if m:
        return m.group(1) + m.group(2) + m.group(3)
    # 拿不到就留空，不写「无日期」——名字里塞一个否定词只是噪声
    return (rec.get("shoot_date") or "").replace("-", "")


def desired_name(rec: dict) -> str:
    p = Path(rec["id"])
    # 已改过名的素材要用第一次的原始文件名：「原编号」那格不能取当前名字，
    # 否则第二次跑就会把已经加过前缀的名字再截断一次，变成越改越短。
    stem = rec.get("original_name") or p.stem
    ext = p.suffix
    # 文件级级别取**最好的一段**，不取第一段：长口播里开头往往是喝茶闲聊（do-not-use），
    # 中间才是能用的正片。取第一段会把整支素材标成「不用」，索引里那行就废了。
    _order = {"must-use": 0, "backup": 1, "do-not-use": 2}
    _grades = [s.get("grade") for s in (rec.get("segments") or []) if s.get("grade")]
    grade = GRADE.get(min(_grades, key=lambda g: _order.get(g, 9)), "") if _grades else ""
    parts = [date_tag(rec), batch_tag(rec["id"]) if batch_tag(rec["id"]) != "根目录" else "",
             content_tag(rec), grade, clean(stem, 24)]
    parts = [x for x in parts if x]
    # 除原编号外一格信息都没有（比如 logo、说明图）：名字加前缀只是噪声，恢复原名
    if len(parts) <= 1:
        return clean(stem, 24) + ext
    base = "_".join(parts)
    return base[:MAX_NAME] + ext


def compose_maps(maps_dir: Path) -> dict:
    """把历史上所有改名记录压成一张 from→to 表，并解开链式改名。

    改过两轮的名字会出现 a→b、b→c，项目里可能还写着 a。只应用最后一份映射
    就会漏——所以每次都把全部历史合起来解一遍。
    """
    mp: dict[str, str] = {}
    for f in sorted(maps_dir.glob("*.json")):
        try:
            for e in json.loads(f.read_text()).get("entries", []):
                mp[e["from"]] = e["to"]
        except (json.JSONDecodeError, KeyError):
            continue
    resolved = {}
    for k in mp:
        v, guard = mp[k], 0
        while v in mp and guard < 20:
            v = mp[v]
            guard += 1
        resolved[k] = v
    return resolved


def walk_replace(obj, mapping: dict):
    """把 JSON 里任何字符串值里的旧路径替换成新路径（EDL、L2 记录都会引用 asset）。"""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [walk_replace(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: walk_replace(v, mapping) for k, v in obj.items()}
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--fix-projects", action="store_true")
    ap.add_argument("--include", default=None, help="只处理路径含该子串的文件")
    ap.add_argument("--max", type=int, default=0, help="最多改几个（0 = 全部）")
    a = ap.parse_args()

    lib = Path(a.library).expanduser().resolve()
    sysdir = lib / "_cutskills"
    idx_path = sysdir / "index.json"
    if not idx_path.exists():
        print(f"没有索引，先跑：index_assets.py --library \"{lib}\" --stage finish", file=sys.stderr)
        return 2
    index = json.loads(idx_path.read_text())
    assets = index["assets"]
    maps_dir = sysdir / "renames"
    maps_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 撤销
    if a.revert:
        maps = sorted(maps_dir.glob("*.json"))
        if not maps:
            print("没有可撤销的改名记录", file=sys.stderr)
            return 2
        rec = json.loads(maps[-1].read_text())
        back = {}
        for e in rec["entries"]:
            src = lib / e["to"]
            dst = lib / e["from"]
            if src.exists() and not dst.exists():
                src.rename(dst)
                back[e["to"]] = e["from"]
        for r in assets:
            if r["id"] in back:
                r["aliases"] = sorted(set((r.get("aliases") or []) + [r["id"]]))
                r["id"] = r["path"] = back[r["id"]]
                r.pop("renamed_at", None)
        idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1))
        print(f"已撤销 {len(back)} 个改名（依据 {maps[-1].name}）")
        return 0

    # ---------- 规划
    used: set[str] = set()
    for p in lib.rglob("*"):
        if p.is_file() and "_cutskills" not in p.parts:
            used.add(str(p.relative_to(lib)))

    plan = []
    for rec in sorted(assets, key=lambda x: x["id"]):
        if rec.get("kind") not in {"video", "photo", "audio"}:
            continue          # 文档（语料手册、方案）本来就能读，不动
        if a.include and a.include not in rec["id"]:
            continue
        src = lib / rec["id"]
        if not src.exists():
            continue
        want = desired_name(rec)
        dst_rel = str(Path(rec["id"]).parent / want) if "/" in rec["id"] else want
        if dst_rel == rec["id"]:
            continue
        # 撞名就编号，绝不允许覆盖
        if dst_rel in used:
            stem, ext = Path(want).stem, Path(want).suffix
            n = 2
            while True:
                cand = f"{stem}-{n}{ext}"
                cand_rel = str(Path(rec["id"]).parent / cand) if "/" in rec["id"] else cand
                if cand_rel not in used and not (lib / cand_rel).exists():
                    dst_rel = cand_rel
                    break
                n += 1
        used.add(dst_rel)
        plan.append({"uid": rec.get("uid"), "from": rec["id"], "to": dst_rel,
                     "asset": rec})
        if a.max and len(plan) >= a.max:
            break

    if not plan:
        # 没有要改的名，但可能还需要把历史改名同步到项目文里
        if a.fix_projects and maps_dir.exists():
            latest = sorted(maps_dir.glob("*.json"))
            if latest:
                mp = compose_maps(maps_dir)
                n = 0
                for proj in (sysdir / "projects").iterdir():
                    for jf in proj.glob("*.json"):
                        try:
                            d = json.loads(jf.read_text())
                        except json.JSONDecodeError:
                            continue
                        nd = walk_replace(d, mp)
                        if nd != d:
                            jf.write_text(json.dumps(nd, ensure_ascii=False, indent=1))
                            n += 1
                print(f"文件名无需调整；改写了 {n} 个项目文件里的素材路径（依据 {latest[-1].name}）")
                return 0
        print("没有需要改名的素材（已经符合命名规则）")
        return 0

    print(f"{'演练' if not a.apply else '执行'}：{len(plan)} 个文件")
    for e in plan[:20]:
        print(f"  {e['from']}")
        print(f"    → {e['to']}")
    if len(plan) > 20:
        print(f"  … 另有 {len(plan) - 20} 个")

    blind = sum(1 for e in plan if "无日期" in e["to"])
    no_content = sum(1 for e in plan if content_tag(e["asset"]) == "")
    print(f"\n其中：无拍摄日期 {blind} 个（容器里没写 creation_time）；"
          f"没跑 L2 所以没有内容词 {no_content} 个")
    if not a.apply:
        print("\n演练结束。确认要改就加 --apply；改完跑 --stage finish 让索引对账。")
        return 0

    # ---------- 落地
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    mapping: dict[str, str] = {}
    entries = []
    for e in plan:
        src, dst = lib / e["from"], lib / e["to"]
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        mapping[e["from"]] = e["to"]
        entries.append({"uid": e["uid"], "from": e["from"], "to": e["to"]})
        rec = e["asset"]
        if not rec.get("original_name"):
            rec["original_name"] = Path(e["from"]).stem
        rec["aliases"] = sorted(set((rec.get("aliases") or []) + [e["from"]]))
        rec["id"] = rec["path"] = e["to"]
        rec["renamed_at"] = stamp

    (maps_dir / f"{stamp}.json").write_text(
        json.dumps({"at": stamp, "count": len(entries), "entries": entries},
                   ensure_ascii=False, indent=1))
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1))
    print(f"\n已改 {len(entries)} 个；映射存在 _cutskills/renames/{stamp}.json")

    # L2 记录里的 asset 字段也要跟上，否则重新合并时会找不到
    n_l2 = 0
    for f in (sysdir / "l2").glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if d.get("asset") in mapping:
            d["asset"] = mapping[d["asset"]]
            f.write_text(json.dumps(d, ensure_ascii=False, indent=1))
            n_l2 += 1
    if n_l2:
        print(f"同步了 {n_l2} 条 L2 记录的素材引用")

    if a.fix_projects:
        n = 0
        for proj in (sysdir / "projects").iterdir() if (sysdir / "projects").exists() else []:
            for jf in proj.glob("*.json"):
                try:
                    d = json.loads(jf.read_text())
                except json.JSONDecodeError:
                    continue
                nd = walk_replace(d, mapping)
                if nd != d:
                    jf.write_text(json.dumps(nd, ensure_ascii=False, indent=1))
                    n += 1
        print(f"改写了 {n} 个项目文件里的素材路径")

    print("下一步：index_assets.py --stage finish（按内容指纹对账，不会重算）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
