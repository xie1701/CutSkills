#!/usr/bin/env python3
"""CutSkills · 把 index.json 渲染成「人能在 Finder 里直接看」的索引。

index.json 是给机器读的（78 万字符，打开就卡）；这个脚本出两份人能用的：
  · 素材索引.md —— 放在素材库根目录，按批次分组，带画幅/规格/分级/一句话说明
  · 素材索引.csv —— 用 Excel/Numbers 打开，可排序筛选

生成物别手改，每次重建索引都会覆盖。要改内容去写 L2 记录（_cutskills/l2/*.json）。

用法：
  render_library_index.py --library "<素材库>"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_spec  # noqa: E402

GRADE = {"must-use": "① 必用", "backup": "② 备用", "do-not-use": "③ 不用"}
GRADE_ORDER = {"must-use": 0, "backup": 1, "do-not-use": 2}
ORIENT = {"portrait": "竖", "landscape": "横", "square": "方", None: "—"}


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in {"B", "KB"} else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def spec_badge(rec: dict) -> str:
    """一列读完「这条素材什么规格」，只写会影响判断的东西。"""
    s = rec.get("spec") or {}
    if rec.get("kind") == "doc":
        return "文档"
    if rec.get("kind") == "photo":
        w, h = s.get("width"), s.get("height")
        return f"{w}×{h}" if w and h else "照片"
    bits = []
    dh = s.get("display_height") or s.get("height") or 0
    dw = s.get("display_width") or s.get("width") or 0
    short = min(dh, dw) if dh and dw else 0
    if short >= 2000:
        bits.append("4K")
    elif short >= 1000:
        bits.append("1080p")
    elif short:
        bits.append(f"{short}p")
    hdr = s.get("hdr")
    bits.append({"hlg": "HLG", "pq": "HDR10", "dolby_vision": "杜比视界"}.get(hdr, "SDR"))
    if s.get("bit_depth", 8) > 8:
        bits.append(f"{s['bit_depth']}bit")
    fps = s.get("fps") or 0
    if fps:
        bits.append(f"{fps:g}fps")
    return "·".join(bits)


def device_short(cap: dict) -> str:
    d = (cap or {}).get("device") or ""
    return d.replace("Apple ", "").strip() or "—"


def capture_cell(rec: dict) -> str:
    """一格里说清「什么设备拍的、有没有位置」。设备和位置是检索的第一入口。"""
    cap = rec.get("capture") or {}
    if not cap:
        return "—"
    bits = [device_short(cap)]
    which = (cap.get("lens") or {}).get("which")
    if which:
        bits.append(which)
    if cap.get("gps"):
        bits.append("📍")
    prof = cap.get("color_profile")
    if prof:
        bits.append(prof)
    return "·".join(bits)


def content_of(rec: dict) -> str:
    """优先用 L2 写过的摘要；没有就留空——不猜。"""
    for seg in rec.get("segments") or []:
        if seg.get("summary"):
            return str(seg["summary"]).replace("|", "/")
    for seg in rec.get("segments") or []:
        if seg.get("action"):
            return str(seg["action"]).replace("|", "/")
    if rec.get("kind") == "doc":
        head = (rec.get("text_head") or "").strip().splitlines()
        return (head[0][:40] if head else "文档").replace("|", "/")
    return ""


def grade_of(rec: dict) -> str:
    grades = [s.get("grade") for s in (rec.get("segments") or []) if s.get("grade")]
    if not grades:
        return "—"
    return min(grades, key=lambda g: GRADE_ORDER.get(g, 9))


def storage_of(lib: Path, rec: dict, overrides: dict) -> str:
    p = lib / rec["id"]
    if p.exists():
        return "本机"
    ov = overrides.get(rec.get("uid") or "") or overrides.get(rec["id"]) or {}
    if ov.get("where") == "cold":
        return f"冷存储·{ov.get('volume', '外置盘')}"
    if ov.get("where") == "cloud":
        return f"网盘·{ov.get('service', '')}".rstrip("·")
    return "缺失（需找回）"


def render(lib: Path, assets: list[dict]) -> Path:
    lib = Path(lib)
    sysdir = lib / "_cutskills"
    ov_path = sysdir / "storage.json"
    overrides = json.loads(ov_path.read_text()) if ov_path.exists() else {}

    total_bytes = sum(a.get("bytes") or 0 for a in assets)
    total_min = sum(a.get("duration") or 0 for a in assets) / 60
    # L2 覆盖：真正看过画面的时长占比。这是花销指标（一段一次视觉调用），不是质量指标。
    l2_s = sum((float(s.get("out") or 0) - float(s.get("in") or 0))
               for a in assets for s in (a.get("l2_segments") or []))
    l2_cov = (l2_s / 60 / total_min) if total_min else 0.0
    n_l2_assets = sum(1 for a in assets if a.get("l2_segments"))
    # 超软上限的原因存在 index.json 里（脚本先写它、再调这里，所以能读到）
    l2_note = ""
    idx_f = sysdir / "index.json"
    if idx_f.exists():
        try:
            l2_note = (json.loads(idx_f.read_text()).get("counts") or {}).get("l2_budget_note") or ""
        except json.JSONDecodeError:
            l2_note = ""
    n_portrait = sum(1 for a in assets if a.get("orientation") == "portrait")
    n_landscape = sum(1 for a in assets if a.get("orientation") == "landscape")

    by_batch: dict[str, list[dict]] = {}
    for a in assets:
        by_batch.setdefault(a.get("batch") or "根目录", []).append(a)

    L: list[str] = []
    L += [f"# 素材索引 · {lib.name}", "",
          "> 由 `scripts/render_library_index.py` 生成，别手改；重建索引时会整体覆盖。",
          "> 要补内容说明，写 `_cutskills/l2/*.json`；要标冷存储，写 `_cutskills/storage.json`。", "",
          f"生成于 {datetime.now():%Y-%m-%d %H:%M} · **{len(assets)} 个素材** · "
          f"{total_min:.0f} 分钟 · {human_bytes(total_bytes)} · "
          f"竖屏 {n_portrait} / 横屏 {n_landscape}", "",
          f"看过画面的（L2）{l2_s/60:.1f} 分钟 / {n_l2_assets} 个素材 = **覆盖 {l2_cov*100:.1f}%**"
          + (f"（超软上限 15%，原因：{l2_note}）" if l2_cov > 0.15 and l2_note
             else "（超软上限 15%，未留原因——跑索引时加 `--l2-budget-note`）" if l2_cov > 0.15
             else ""), ""]

    # ---- 怎么读
    L += ["## 怎么读这张表", "",
          "| 列 | 含义 |", "|---|---|",
          "| 编号 | 内容指纹前 12 位。**改名、搬到移动硬盘都不会变**，跨设备找同一个素材靠它 |",
          "| 画幅 | 竖 / 横。**按实际显示方向判**，不是按像素宽高——手机竖拍的片子在文件里是横的 1920×1080，只靠 rotation 元数据转过来 |",
          "| 规格 | 分辨率·动态范围·色深·帧率。HLG / HDR10 / 杜比视界 是三种高动态格式，剪之前要统一转成 SDR |",
          "| 分级 | ① 必用 / ② 备用 / ③ 不用。① 来自看过画面的 L2 判断，② 是只看过技术指标的初判 |",
          "| 拍摄 | 拍摄设备·哪颗镜头（主摄／超广角／长焦）·📍表示有 GPS 坐标·照片附带色域。设备和地点是「去年那段在哪拍的」这类问题的入口 |",
          "| 内容 | 一句话说明画面里是什么。空着的表示还没跑 L2，不确定，不是「没内容」 |", ""]

    # ---- 设备与地点
    dev: dict[str, int] = {}
    lensn: dict[str, int] = {}
    places: dict[tuple[float, float], int] = {}
    with_gps = 0
    for a in assets:
        cap = a.get("capture") or {}
        if not cap:
            continue
        dev[device_short(cap)] = dev.get(device_short(cap), 0) + 1
        which = (cap.get("lens") or {}).get("which")
        if which:
            lensn[which] = lensn.get(which, 0) + 1
        g = cap.get("gps") or {}
        if g.get("lat") is not None and g.get("lon") is not None:
            with_gps += 1
            key = (round(float(g["lat"]), 2), round(float(g["lon"]), 2))
            places[key] = places.get(key, 0) + 1
    if dev:
        L += ["## 拍摄设备与地点", "",
              f"有拍摄信息的素材 {sum(dev.values())} 个，其中带 GPS 坐标 {with_gps} 个。",
              "",
              "| 设备 | 素材数 |", "|---|---|"]
        for d, n in sorted(dev.items(), key=lambda kv: -kv[1]):
            L.append(f"| {d} | {n} |")
        L.append("")
        if lensn:
            L += [f"按镜头分（{'、'.join(f'{k} {v}' for k, v in sorted(lensn.items(), key=lambda kv: -kv[1]))}）："
                  "同一组素材换了镜头，视角和景深就不一样，混在一起会看着「跳」。"
                  "**视频没有这一项**——iPhone 的容器里不写镜头和曝光，不是漏读。", ""]
        if places:
            L += ["按坐标聚类（四舍五入到 0.01°，约 1 公里，粗略看得出是几个场地）：", "",
                  "| 概略坐标 | 素材数 | 地图 |", "|---|---|---|"]
            for (lat, lon), n in sorted(places.items(), key=lambda kv: -kv[1]):
                L.append(f"| {lat:.2f}, {lon:.2f} | {n} | "
                         f"[打开](https://maps.apple.com/?ll={lat},{lon}&z=15) |")
            L.append("")
            L += ["> GPS 是从文件元数据里读的，不是猜的。这份索引含精确坐标，"
                  "**对外发送素材库时注意**：要么删掉这两列，要么整包只给内部。", ""]

    # ---- 需要特别注意
    flag_rows: dict[str, list[dict]] = {}
    for a in assets:
        for code in a.get("handling") or []:
            flag_rows.setdefault(code, []).append(a)
    if flag_rows:
        L += ["## 需要特别注意的素材", "",
              "这些是「规格本身会带来处理差异」的素材，不是质量问题。码表见文末。", "",
              "| 标记 | 数量 | 涉及素材 | 影响 |", "|---|---|---|---|"]
        for code, items in sorted(flag_rows.items(), key=lambda kv: -len(kv[1])):
            sample = "、".join(sorted({i["id"].split("/")[-1] for i in items})[:4])
            more = f" 等 {len(items)} 个" if len(items) > 4 else ""
            L.append(f"| `{code}` | {len(items)} | {sample}{more} | "
                     f"{media_spec.HANDLING.get(code, '—').split('。')[0]} |")
        L.append("")

    # ---- 按批次
    L += ["## 按批次浏览", ""]
    for batch, items in sorted(by_batch.items()):
        mins = sum(i.get("duration") or 0 for i in items) / 60
        L += [f"### {batch}/ · {len(items)} 个 · {mins:.1f} 分钟 · "
              f"{human_bytes(sum(i.get('bytes') or 0 for i in items))}", "",
              "| 编号 | 文件 | 画幅 | 规格 | 拍摄 | 时长 | 分级 | 位置 | 内容 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for a in sorted(items, key=lambda x: x["id"]):
            dur = a.get("duration") or 0
            L.append("| {uid} | {name} | {o} | {spec} | {cam} | {dur} | {g} | {loc} | {c} |".format(
                uid=a.get("uid") or "—",
                name=(a["id"].split("/", 1)[1] if "/" in a["id"] else a["id"]).replace("|", "/"),
                o=ORIENT.get(a.get("orientation"), "—"),
                spec=spec_badge(a),
                cam=capture_cell(a),
                dur=f"{dur:.0f}s" if dur else "—",
                g=GRADE.get(grade_of(a), "—"),
                loc=storage_of(lib, a, overrides),
                c=content_of(a)))
        L.append("")

    # ---- 码表
    L += ["## 规格标记与处理方式", "", "| 标记 | 意味着什么 · 怎么处理 |", "|---|---|"]
    used = sorted(flag_rows) or ["hevc"]
    for code in used:
        L.append(f"| `{code}` | {media_spec.HANDLING.get(code, '—')} |")
    L.append("")

    out = lib / "素材索引.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---- CSV
    csv_path = lib / "素材索引.csv"
    cols = ["uid", "id", "batch", "kind", "orientation", "display_width", "display_height",
            "vcodec", "bit_depth", "hdr", "fps", "duration", "bytes", "grade",
            "spec", "handling", "shoot_date", "device", "lens", "software", "gps_lat", "gps_lon",
            "alt_m", "color_profile", "exposure", "storage", "content", "aliases"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for a in sorted(assets, key=lambda x: (x.get("batch") or "", x["id"])):
            s = a.get("spec") or {}
            w.writerow([
                a.get("uid"), a["id"], a.get("batch") or "", a.get("kind"),
                a.get("orientation"), s.get("display_width") or s.get("width"),
                s.get("display_height") or s.get("height"),
                s.get("vcodec"), s.get("bit_depth"), s.get("hdr"), s.get("fps"),
                a.get("duration"), a.get("bytes"), grade_of(a),
                spec_badge(a), " ".join(a.get("handling") or []),
                a.get("shoot_date") or "",
                (a.get("capture") or {}).get("device") or "",
                " ".join(str(v) for v in (((a.get("capture") or {}).get("lens") or {}).get("which"),
                                          ((a.get("capture") or {}).get("lens") or {}).get("model")) if v),
                (a.get("capture") or {}).get("software") or "",
                ((a.get("capture") or {}).get("gps") or {}).get("lat", ""),
                ((a.get("capture") or {}).get("gps") or {}).get("lon", ""),
                ((a.get("capture") or {}).get("gps") or {}).get("alt_m", ""),
                (a.get("capture") or {}).get("color_profile") or "",
                " ".join(f"{k}={v}" for k, v in ((a.get("capture") or {}).get("exposure") or {}).items()),
                storage_of(lib, a, overrides),
                content_of(a), ";".join(a.get("aliases") or []),
            ])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    a = ap.parse_args()
    lib = Path(a.library).expanduser().resolve()
    idx = lib / "_cutskills" / "index.json"
    if not idx.exists():
        print(f"还没建索引：{idx}", file=sys.stderr)
        return 2
    assets = json.loads(idx.read_text())["assets"]
    out = render(lib, assets)
    print(f"已生成 {out}")
    print(f"        {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
