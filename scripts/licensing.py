#!/usr/bin/env python3
"""CutSkills · 授权总表：一条片子（或一个素材库）到底用了什么，能不能商用，证据在哪。

## 为什么要有这个

以前授权是散的：音频有 `assets/audio/sfx/LICENSE-CC0.txt` + `bgm/SOURCES.md` + `catalog.csv`，
成片旁有 `out/sfx_manifest.json`（G-07 的逐文件口径）；字体只在 `styles/fonts.yaml` 里记了
文件与宽度比，**没有 license 字段**；16 款贴纸是代码自绘还是外部素材，没有任何声明。
商用交付时甲方问一句「这字体能商用吗、这贴纸哪来的」，得靠人去翻仓。

这个脚本把三件事压成**一张表**：

  1. 逐条列出**这条成片实际用到的**素材（不是仓里有什么，是片里用了什么）；
  2. 每条都指到留痕文件（许可文本 / 目录清单 / 代码位置），能现场复核；
  3. 缺授权或核验不了的，**在表顶部单独汇总**——不埋在明细里等人自己发现。

## 三条硬口径（不许为了好看放宽）

* **不猜授权。** 查不到证据一律 `unverified` + 「商用前必须人工确认」。写「应该是 CC0」
  比不写更坏：它会让下一个人以为已经核过了。
* **不重造音频判据。** 音频的许可口径唯一出处是 `out/sfx_manifest.json`（render_hf 的
  `write_sfx_manifest()`，评审 G-07）与 `cc0_lib.licence_of()`；本脚本只读、只汇总，
  不自己判断「这个目录是不是 CC0」。
* **代码自绘 ≠ 外部素材。** 贴纸是 `scripts/stickers.py` 里的内联 SVG 字符串，代码场景是
  `scripts/scenes/*.py`，都是本仓库自己的代码，标 `code_drawn`；只有出现外链位图/未知 id
  时才升级成「需核验」。

## 用法

  licensing.py --project <项目目录>              # → <项目>/out/licensing.json + licensing.md
  licensing.py --project <项目目录> --json       # 只打印 JSON
  licensing.py --library <素材库> [--write]      # 素材库总表（默认只打印）
  licensing.py --library <音频库> --json

口径说明（什么算可商用、字体嵌入、CC0 与实际录音授权的区别）写在 `references/licensing.md`。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from libs import REPO  # noqa: E402

AUDIO_PROOFS = ("LICENSE-CC0.txt", "SOURCES.md", "catalog.csv")
# 素材库自己的授权留痕文件：实拍素材的授权来自棚/客户，只能由人写进库里，代码不替它编
LIB_RIGHTS_FILES = ("_cutskills/rights.json", "LICENSES.md", "rights.json")

NEEDS_REVIEW_TEXT = "商用前必须人工确认授权"


# ------------------------------------------------------------------ 小工具

def _read_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _disp(p: Path | str | None) -> str | None:
    """显示用路径：仓内给相对路径，仓外把家目录折成 ~（别把私人绝对路径写进产物）。"""
    if p is None:
        return None
    q = Path(p).expanduser()
    try:
        rp = q.resolve()
    except OSError:
        return str(q)
    try:
        return rp.relative_to(REPO).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + rp.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(rp)


def _walk_fonts(node, path: str = "") -> list[tuple[str, str]]:
    """在 yaml 树里找字体字段：键叫 font / *_font 的都算，值必须是字符串。"""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            key = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and (k == "font" or str(k).endswith("_font")):
                out.append((key, v))
            else:
                out.extend(_walk_fonts(v, key))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_fonts(v, f"{path}[{i}]"))
    return out


def _load_yaml(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:                                                   # noqa: BLE001
        return {}


# ------------------------------------------------------------------ 音频

def _audio_provenance(path: str | Path | None) -> dict:
    """一个音频文件属于哪个库根，那个根里有哪些许可留痕文件（逐条可点开复核）。"""
    if not path:
        return {"library_root": None, "proof_files": [],
                "note": "没有文件路径（本地合成或缺失），无第三方素材可查"}
    import cc0_lib
    try:
        rp = Path(path).resolve()
    except OSError:
        return {"library_root": None, "proof_files": [], "note": "路径解析失败"}
    for root in (cc0_lib.BGM_LIB, cc0_lib.SFX_LIB, cc0_lib.BUILTIN_BGM, cc0_lib.BUILTIN_SFX):
        try:
            if root.exists() and rp.is_relative_to(root.resolve()):
                proofs = [_disp(root / f) for f in AUDIO_PROOFS if (root / f).exists()]
                return {"library_root": _disp(root), "proof_files": proofs,
                        "note": ("库根有许可留痕，逐文件许可按 cc0_lib.licence_of() 认定"
                                 if proofs else "库根没有任何许可留痕文件 → 不能认定 CC0")}
        except OSError:
            continue
    return {"library_root": None, "proof_files": [],
            "note": "文件不在任何已配置的音乐/音效库里——外部素材，授权必须人工确认"}


def _audio_from_manifest(proj: Path) -> tuple[list[dict], str] | None:
    """优先复用 render_hf 写下的 sfx_manifest.json（G-07 口径），不重造判据。"""
    m = _read_json(proj / "out" / "sfx_manifest.json")
    if m is None:
        return None
    rows: list[dict] = []
    bgm = m.get("bgm") or None
    if isinstance(bgm, dict):
        rows.append(_norm_audio_row(bgm, "bgm", proj))
    for r in m.get("sfx") or []:
        if isinstance(r, dict):
            rows.append(_norm_audio_row(r, "sfx", proj))
    return rows, "out/sfx_manifest.json"


def _norm_audio_row(r: dict, kind: str, proj: Path) -> dict:
    lic = r.get("licence")
    src = r.get("source")
    if src == "procedural":
        status, why = "procedural", "本地代码合成，无第三方素材风险"
    elif lic:
        status, why = "ok", f"逐文件留痕：{lic}"
    elif src == "missing":
        status, why = "unverified", "时间轴要的是这条音效，但库里找不到文件——是否用过无法核验"
    else:
        status, why = "unverified", (r.get("note") or "许可未核验：库根没有许可留痕文件")
    row = {
        "kind": kind,
        "id": r.get("id") or r.get("name") or r.get("title"),
        "name": r.get("name"),
        "title": r.get("title"),
        "file": r.get("file"),
        "source": src,
        "original_source": r.get("source"),
        "licence": lic,
        "status": status,
        "why": why,
        "sha256_16": r.get("sha256_16"),
        "needs_review": status == "unverified",
    }
    row.update(_audio_provenance(r.get("file")))
    return row


def _audio_from_render_sidecars(proj: Path) -> tuple[list[dict], str] | None:
    """没有 manifest 时，用成片旁的 bgm.json / sfx.json + 同一套 cc0_lib 口径重算。

    sfx.json 是 render_hf 落的事件表（name + nth），文件与许可仍走
    `render_hf.sfx_spec()` / `cc0_lib.licence_of()`——判据还是那一份，不另起一套。
    """
    bgm = _read_json(proj / "bgm.json")
    sfxd = _read_json(proj / "out" / "sfx.json")
    if bgm is None and sfxd is None:
        return None
    import cc0_lib
    rows: list[dict] = []
    if bgm:
        f = bgm.get("file")
        lic = cc0_lib.licence_of(f) if f else None
        prov = _audio_provenance(f)
        row = {"kind": "bgm", "id": bgm.get("title"), "name": None,
               "title": bgm.get("title"), "file": f,
               "source": bgm.get("source") or "unknown",
               "original_source": bgm.get("source"),
               "licence": lic, "status": "ok" if lic else "unverified",
               "why": f"逐文件留痕：{lic}" if lic else (prov.get("note") or NEEDS_REVIEW_TEXT),
               "sha256_16": None, "needs_review": not lic}
        row.update(prov)
        rows.append(row)
    try:
        import render_hf
    except ImportError:
        render_hf = None
    seen: set = set()
    for e in (sfxd or {}).get("events") or []:
        name = str((e or {}).get("name") or "")
        if not name or render_hf is None:
            continue
        spec = render_hf.sfx_spec(name, int(e.get("nth") or 0))
        if not spec:
            key = (name, None)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"kind": "sfx", "id": name, "name": name, "title": None, "file": None,
                         "source": "missing", "original_source": "missing", "licence": None,
                         "status": "unverified", "why": "库里找不到这条音效，实际用过什么无法核验",
                         "sha256_16": None, "needs_review": True,
                         "library_root": None, "proof_files": []})
            continue
        p = spec.get("path") or ""
        key = (name, p)
        if key in seen:
            continue
        seen.add(key)
        lic = cc0_lib.licence_of(p) if p else None
        prov = _audio_provenance(p)
        row = {"kind": "sfx", "id": spec.get("id") or name, "name": name, "title": None,
               "file": p, "source": "procedural" if not p else ("cc0" if lic else "unverified"),
               "original_source": None, "licence": lic,
               "status": "procedural" if not p else ("ok" if lic else "unverified"),
               "why": ("本地代码合成，无第三方素材风险" if not p
                       else (f"逐文件留痕：{lic}" if lic
                             else (prov.get("note") or NEEDS_REVIEW_TEXT))),
               "sha256_16": None, "needs_review": bool(p) and not lic}
        row.update(prov)
        rows.append(row)
    return rows, "bgm.json + out/sfx.json"


def audio_block(proj: Path) -> dict:
    got = _audio_from_manifest(proj)
    reached_by = "out/sfx_manifest.json"
    if got is None:
        got = _audio_from_render_sidecars(proj)
        reached_by = "bgm.json + out/sfx.json（未渲或未写 manifest）"
    if got is None:
        return {"status": "no_evidence", "reached_by": None, "rows": [],
                "note": ("这条片子还没有音频留痕（out/sfx_manifest.json 与 bgm.json/out/sfx.json "
                         "都不存在）：片中到底用了哪首 BGM、哪几条音效无法核验。"
                         "先跑一次 render_hf.py，它会把逐文件许可写进 out/sfx_manifest.json。"),
                "needs_review": True}
    rows, src = got
    return {"status": "ok" if rows else "empty", "reached_by": src, "rows": rows,
            "note": "" if rows else "成片没有使用 BGM/音效（全片无配乐）",
            "needs_review": any(r.get("needs_review") for r in rows)}


# ------------------------------------------------------------------ 字体

FONT_FILE_RE = re.compile(r"[\w\-]+\.(?:ttf|otf|ttc)", re.I)


def fonts_block(proj: Path, tl: dict, scenes: list[dict]) -> dict:
    """这条片子实际用到的字体：样式包 + 包装包里的 font 字段，加上场景代码里写死的字体文件。"""
    import fonts as fonts_mod
    used: dict[str, dict] = {}

    def add(name: str, where: str) -> None:
        if not name or not isinstance(name, str):
            return
        key = fonts_mod.entry_of(name)[0] or name
        slot = used.setdefault(key, {"name": key, "as_written": set(), "used_by": []})
        slot["as_written"].add(name)
        if where not in slot["used_by"]:
            slot["used_by"].append(where)

    packs = []
    if tl.get("pack"):
        packs.append(("pack", REPO / "packs" / f"{tl['pack']}.yaml", f"packs/{tl['pack']}.yaml"))
    style_name = tl.get("style_pack") or ((tl.get("brief") or {}).get("style_pack"))
    if style_name:
        packs.append(("style", REPO / "styles" / f"{style_name}.yaml",
                      f"styles/{style_name}.yaml"))
    for _kind, f, label in packs:
        for key, val in _walk_fonts(_load_yaml(f)):
            add(val, f"{label} {key}")

    # 时间轴里如果显式指定了字体（props.font / card.font），也算这条片子用到了
    for el in tl.get("elements") or []:
        for key, val in _walk_fonts(el.get("props") or {}, f"elements[n={el.get('n')}].props"):
            add(val, f"timeline.json {key}")
        for key, val in _walk_fonts(el.get("card") or {}, f"elements[n={el.get('n')}].card"):
            add(val, f"timeline.json {key}")

    # 代码场景里写死的字体文件（PIL 制图不吃 @font-face，直接开文件）
    for sc in scenes:
        src = sc.get("source_file")
        if not src:
            continue
        try:
            text = (REPO / src).read_text(encoding="utf-8")
        except OSError:
            continue
        for fn in sorted(set(FONT_FILE_RE.findall(text))):
            add(fn, f"{src}（场景代码内写死）")

    rows = []
    for key in sorted(used):
        info = fonts_mod.licence_of(key)
        slot = used[key]
        row = {**info,
               "as_written": sorted(slot["as_written"]),
               "used_by": slot["used_by"],
               "needs_review": info.get("status") != "ok"}
        rows.append(row)
    return {"status": "ok" if rows else "empty", "rows": rows,
            "note": "" if rows else "没有解析到字体引用（缺 pack / style_pack，或两包都没写 font 字段）",
            "needs_review": any(r.get("needs_review") for r in rows)}


# ------------------------------------------------------------------ 贴纸

def stickers_block(tl: dict) -> dict:
    import stickers as stickers_mod
    decl = stickers_mod.licensing_declaration()
    catalog = {i["id"]: i for i in decl["items"]}
    used: dict[str, dict] = {}
    for s in tl.get("stickers") or []:
        sid = str((s or {}).get("id") or "")
        if sid:
            slot = used.setdefault(sid, {"id": sid, "at": [], "elements": []})
            slot["at"].append(s.get("at"))
    for el in tl.get("elements") or []:
        st = el.get("sticker") or {}
        sid = str(st.get("id") or "")
        if sid:
            slot = used.setdefault(sid, {"id": sid, "at": [], "elements": []})
            at = el.get("at") if el.get("at") is not None else el.get("_start")
            if at is None:
                slot["elements"].append(el.get("n"))
            else:
                slot["at"].append(at)
    rows = []
    for sid in sorted(used):
        hit = catalog.get(sid)
        slot = used[sid]
        where = [a for a in slot["at"] if a is not None]
        where += [f"元素#{n}" for n in slot["elements"] if n is not None]
        if hit is None:
            rows.append({"id": sid, "origin": "unknown", "label": None,
                         "license": "unverified",
                         "why": ("时间轴里的贴纸 id 不在内置 16 款里：可能是外部贴纸图 / "
                                 "未登记的扩展，授权必须人工确认"),
                         "at": where, "needs_review": True})
            continue
        rows.append({"id": sid, "origin": "code_drawn", "label": hit.get("label"),
                     "license": decl["license"],
                     "why": "代码内自绘 SVG（scripts/stickers.py 内联字符串，无外部素材文件）",
                     "at": where, "needs_review": False})
    return {"declaration": {k: v for k, v in decl.items() if k != "items"},
            "rows": rows, "unused_count": decl["count"] - len(rows),
            "note": "" if rows else "本片没有使用贴纸（16 款内置贴纸全部未出场）",
            "needs_review": any(r["needs_review"] for r in rows) or decl["third_party_risk"]}


# ------------------------------------------------------------------ 代码场景

def scenes_block(proj: Path, tl: dict) -> dict:
    ids: set[str] = set()
    for el in tl.get("elements") or []:
        props = el.get("props") or {}
        for k in ("scene", "scene_spec"):
            if props.get(k):
                ids.add(str(props[k]))
    sdir = proj / "scenes"
    if sdir.is_dir():
        ids.update(p.stem for p in sdir.glob("*.json"))

    rows = []
    for sid in sorted(ids):
        repo_py = REPO / "scripts" / "scenes" / f"{sid}.py"
        proj_py = proj / "scenes" / f"{sid}.py"
        row = {"id": sid, "author": "本仓库（CutSkills）", "origin": "code_drawn",
               "license": "本仓库代码（自定义场景随仓声明，见 README「📄 许可」）",
               "needs_review": False}
        if repo_py.exists():
            row["source_file"] = _disp(repo_py)
            row["evidence"] = "场景代码在本仓 scripts/scenes/ 下（逐帧由代码画，无外部图片）"
        elif proj_py.exists():
            row["source_file"] = _disp(proj_py)
            row["evidence"] = "场景代码在项目内（本项目自绘代码，不随仓分发）"
        else:
            builtin = _builtin_scene_ids()
            if sid in builtin:
                row["source_file"] = _disp(REPO / "scripts" / "gen_scene.py")
                row["evidence"] = "gen_scene.py 内置模板（代码绘制）"
            else:
                row.update({"origin": "unknown", "author": None,
                            "license": "unverified", "needs_review": True,
                            "evidence": ("时间轴引用了场景 id 却找不到它的代码（仓内"
                                         "scripts/scenes/ 与项目 scenes/ 都没有）——可能是外部"
                                         "素材或手放的画，授权必须人工确认")})
        rows.append(row)
    return {"status": "ok" if rows else "empty", "rows": rows,
            "note": "" if rows else "本片没有代码叙事场景（无 template: scene 的整屏画面）",
            "needs_review": any(r["needs_review"] for r in rows)}


_BUILTIN_SCENES: set[str] | None = None


def _builtin_scene_ids() -> set[str]:
    global _BUILTIN_SCENES
    if _BUILTIN_SCENES is None:
        try:
            import gen_scene
            _BUILTIN_SCENES = set(getattr(gen_scene, "SCENES", {}) or {})
        except Exception:                                               # noqa: BLE001
            _BUILTIN_SCENES = set()
    return _BUILTIN_SCENES


# ------------------------------------------------------------------ 运行时依赖

def runtime_block() -> dict:
    """非素材的代码依赖：只如实抄依赖文件自己写的许可，条款全文没随仓保存的就说没保存。"""
    rows = []
    g = REPO / "assets" / "gsap.min.js"
    if g.exists():
        head = g.read_text(encoding="utf-8", errors="replace")[:400]
        ver = re.search(r"GSAP\s+([\d.]+)", head)
        lic = re.search(r"@license\s*(.+)", head)
        url = (re.search(r"(https?://[\w./-]*standard-license[\w./-]*)", head)
               or re.search(r"(https?://[\w./-]*gsap[\w./-]*)", head))
        quote = lic.group(1).strip() if lic else ""
        rows.append({
            "package": "gsap",
            "version": ver.group(1) if ver else None,
            "license": "GreenSock Standard License" if "standard-license" in head else (quote or "unverified"),
            "license_url": url.group(1).rstrip(".,;)") if url else None,
            "quote": quote or None,
            "evidence": "assets/gsap.min.js 文件头注释",
            "terms_saved_in_repo": False,
            "note": ("许可名与版权方从依赖文件自己的头部注释抄来（原文见 quote），条款全文没有"
                     "随仓保存；贴纸动画要用它，商用前请到许可页核对条款原文"),
        })
    return {"rows": rows,
            "note": ("这一节不算素材授权：是渲染期 JS 依赖。列出来只是因为它在片子里动了画面，"
                     "甲方问「动效哪来的」时要有据可查")}


# ------------------------------------------------------------------ 汇总

def _review_items(report: dict) -> list[dict]:
    items: list[dict] = []
    a = report["audio"]
    if a.get("needs_review"):
        if a["status"] == "no_evidence":
            items.append({"class": "音频", "item": "（全片 BGM/音效）", "status": "unverified",
                          "why": "没有音频留痕文件，无法核验用了什么",
                          "how": "先渲一次 render_hf.py，再重跑 licensing.py"})
        for r in a.get("rows") or []:
            if r.get("needs_review"):
                items.append({"class": "音频", "item": r.get("id") or r.get("name"),
                              "status": r.get("status"), "why": r.get("why"),
                              "how": NEEDS_REVIEW_TEXT + "（补齐库根许可留痕或换素材）"})
    for f in report["fonts"].get("rows") or []:
        if f.get("needs_review"):
            items.append({"class": "字体", "item": f"{f.get('name')} / {f.get('file')}",
                          "status": f.get("status"),
                          "why": f.get("note") or "许可查不到证据",
                          "how": NEEDS_REVIEW_TEXT + "（查到官方许可页后补进 styles/fonts.yaml）"})
    for s in report["stickers"].get("rows") or []:
        if s.get("needs_review"):
            items.append({"class": "贴纸", "item": s.get("id"), "status": "unverified",
                          "why": s.get("why"), "how": NEEDS_REVIEW_TEXT})
    if report["stickers"]["declaration"].get("third_party_risk"):
        items.append({"class": "贴纸", "item": "（内置 SVG 里发现外链图形）", "status": "unverified",
                      "why": "内置贴纸的 svg 字符串里出现 <image>/href/url(http…)，不再是纯自绘",
                      "how": NEEDS_REVIEW_TEXT})
    for sc in report["scenes"].get("rows") or []:
        if sc.get("needs_review"):
            items.append({"class": "代码场景", "item": sc.get("id"), "status": "unverified",
                          "why": sc.get("evidence"), "how": NEEDS_REVIEW_TEXT})
    return items


def _counts(report: dict) -> dict:
    c = {"ok": 0, "procedural": 0, "unverified": 0, "total": 0}
    for r in report["audio"].get("rows") or []:
        c["total"] += 1
        c["procedural" if r["status"] == "procedural" else
          ("ok" if r["status"] == "ok" else "unverified")] += 1
    for key in ("fonts", "scenes"):
        for r in report[key].get("rows") or []:
            c["total"] += 1
            c["ok" if not r.get("needs_review") else "unverified"] += 1
    for r in report["stickers"].get("rows") or []:
        c["total"] += 1
        c["ok" if not r.get("needs_review") else "unverified"] += 1
    return c


def scan_project(proj: Path) -> dict:
    proj = Path(proj).expanduser().resolve()
    tl = _read_json(proj / "timeline.json") or {}
    brief = _read_json(proj / "brief.json") or {}
    # 字体看的是「这条片子用了哪个包装包」：timeline 是成片真值，没渲过/没写过就退需求卡
    pack = tl.get("pack") or brief.get("pack")
    style_pack = tl.get("style_pack") or brief.get("style_pack")
    tl_fonts = {**tl, "pack": pack, "style_pack": style_pack}
    scenes = scenes_block(proj, tl)
    report = {
        "version": 1,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "scope": "project",
        "project": {
            "slug": tl.get("slug") or proj.name,
            "path": _disp(proj),
            "pack": pack,
            "style_pack": style_pack,
            "duration_s": tl.get("duration_s") or brief.get("length_s"),
        },
        "audio": audio_block(proj),
        "fonts": fonts_block(proj, tl_fonts, scenes["rows"]),
        "stickers": stickers_block(tl),
        "scenes": scenes,
        "runtime": runtime_block(),
    }
    items = _review_items(report)
    c = _counts(report)
    report["summary"] = {
        **c,
        "needs_review": len(items),
        "review_items": items,
        "verdict": ("全部条目有授权留痕，可商用（%d 条）" % c["total"] if not items
                    else "❌ 有 %d 条需人工确认后才能商用" % len(items)),
        "headline": _headline(items, c),
        "boundary": ("本表只回答「素材从哪来、许可写了什么、证据在哪」，不构成法律意见；"
                     "CC0/公有领域与「实际录音授权」的区别见 references/licensing.md"),
    }
    return report


def _headline(items: list[dict], c: dict) -> str:
    if not items:
        return (f"✅ {c['total']} 条素材全部有许可留痕（其中本地代码合成 {c['procedural']} 条），"
                "可商用")
    by: dict[str, int] = {}
    for it in items:
        by[it["class"]] = by.get(it["class"], 0) + 1
    part = "、".join(f"{k} {v} 条" for k, v in by.items())
    return f"⚠ 商用前必须人工确认：{part}（共 {len(items)} 条，详见下表第一栏）"


# ------------------------------------------------------------------ 素材库

def scan_library(lib: Path) -> dict:
    """素材库总表：音频库给逐文件许可；实拍素材库给「哪些有授权留痕、哪些没有」。"""
    import cc0_lib
    lib = Path(lib).expanduser().resolve()
    audio_rows: list[dict] = []
    reached_by = None
    cat = lib / "catalog.csv"
    if cat.exists():
        import csv
        # 许可判据还是 cc0_lib.licence_of（「库根有留痕才算 CC0」），只是把库根临时指向
        # --library 指定的那个目录：同一个判据换一棵树跑，不是另写一套规则。
        old = (cc0_lib.BGM_LIB, cc0_lib.SFX_LIB)
        try:
            cc0_lib.BGM_LIB = cc0_lib.SFX_LIB = lib
            cc0_lib._cache.clear()
            with cat.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    p = lib / (r.get("file") or "")
                    lic = cc0_lib.licence_of(p)
                    kind = "sfx" if r.get("id") else "bgm"
                    row = {"kind": kind, "id": r.get("id") or r.get("title") or r.get("file"),
                           "file": _disp(p), "licence": lic,
                           "status": "ok" if lic else "unverified",
                           "why": f"逐文件留痕：{lic}" if lic else
                                  ("库根没有许可留痕文件（LICENSE-CC0.txt / SOURCES.md）："
                                   + NEEDS_REVIEW_TEXT)}
                    row.update(_audio_provenance(p))
                    audio_rows.append(row)
            reached_by = "catalog.csv + 库根许可留痕"
        finally:
            cc0_lib.BGM_LIB, cc0_lib.SFX_LIB = old
            cc0_lib._cache.clear()

    assets: list[dict] = []
    idx = _read_json(lib / "_cutskills" / "index.json")
    rights: dict = {}
    rights_file = None
    for name in LIB_RIGHTS_FILES:
        cand = lib / name
        if cand.exists():
            rights_file = _disp(cand)
            doc = _read_json(cand)
            if doc:
                rights = {str(k): v for k, v in doc.items() if isinstance(v, (str, dict))}
            break
    if isinstance(idx, dict):
        raw_assets = idx.get("assets")
        seq = (raw_assets.items() if isinstance(raw_assets, dict)
               else [(str((x or {}).get("id") or i), x) for i, x in enumerate(raw_assets or [])])
        for aid, a in seq:
            got = rights.get(str(aid))
            lic = None
            if isinstance(got, dict):
                lic = got.get("license") or got.get("licence")
            elif isinstance(got, str):
                lic = got
            assets.append({"id": str(aid), "file": _disp((a or {}).get("path") or (a or {}).get("file")),
                           "licence": lic, "status": "ok" if lic else "unverified",
                           "why": (f"库内授权留痕（{rights_file}）" if lic else
                                   "实拍素材的授权来自拍摄方/客户，仓里查不到证据：" + NEEDS_REVIEW_TEXT)})

    items = [{"class": "音频", "item": r["id"], "status": r["status"], "why": r["why"],
              "how": NEEDS_REVIEW_TEXT} for r in audio_rows if r["status"] != "ok"]
    items += [{"class": "实拍素材", "item": r["id"], "status": "unverified", "why": r["why"],
               "how": NEEDS_REVIEW_TEXT + "（把授权写进 <素材库>/_cutskills/rights.json）"}
              for r in assets if r["status"] != "ok"]
    c = {"ok": sum(1 for r in audio_rows + assets if r["status"] == "ok"),
         "unverified": sum(1 for r in audio_rows + assets if r["status"] != "ok"),
         "total": len(audio_rows) + len(assets), "procedural": 0}
    empty = c["total"] == 0
    return {
        "version": 1, "generated": datetime.now().isoformat(timespec="seconds"),
        "scope": "library", "library": {"path": _disp(lib), "name": lib.name,
                                        "index": _disp(lib / "_cutskills" / "index.json"),
                                        "rights_file": rights_file},
        "audio": {"status": "ok" if audio_rows else "empty", "rows": audio_rows,
                  "reached_by": reached_by,
                  "note": "" if audio_rows else "这个目录没有 catalog.csv（不是音频库，或还没建清单）"},
        "assets": {"rows": assets, "rights_file": rights_file,
                   "note": ("" if assets else "素材库没有 index.json，或索引里没有素材")},
        "summary": {**c, "needs_review": len(items), "review_items": items,
                    "verdict": ("目录里没有可核验的东西（无 catalog.csv / index.json）" if empty
                                else "全部条目有授权留痕" if not items
                                else "❌ 有 %d 条需人工确认后才能商用" % len(items)),
                    "headline": ("⚠ 这个目录里没有可统计的素材：没有 catalog.csv，也没有 _cutskills/index.json"
                                 if empty else _headline(items, c)),
                    "boundary": ("实拍素材的授权由素材持有方负责；本表只如实记录「库里有/没有"
                                 "授权留痕」，不替任何人认定可用")},
    }


# ------------------------------------------------------------------ 渲染成人读表

def _cell(v) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "／").replace("\n", " ").strip() or "—"


def render_md(report: dict) -> str:
    s = report["summary"]
    L: list[str] = []
    # 章节号按实际出现的节递增：项目表有字体/贴纸/场景，素材库表没有——别让编号跳号
    NUM = "一二三四五六七八九十"
    sec_no = [0]

    def sec(title: str) -> str:
        sec_no[0] += 1
        return f"## {NUM[min(sec_no[0], len(NUM)) - 1]}、{title}"

    if report["scope"] == "project":
        p = report["project"]
        L.append(f"# 授权总表 · {p['slug']}")
        L.append("")
        L.append(f"> {s['headline']}")
        L.append(">")
        L.append(f"> 包装包 `{p.get('pack') or '—'}` · 风格包 `{p.get('style_pack') or '—'}` · "
                 f"片长 {p.get('duration_s') or '—'}s · 生成于 {report['generated']}")
    else:
        lib = report["library"]
        L.append(f"# 授权总表 · 素材库 {lib['name']}")
        L.append("")
        L.append(f"> {s['headline']}")
        L.append(">")
        L.append(f"> 路径 `{lib['path']}` · 生成于 {report['generated']}")
    L.append("")

    # ---- 第一栏就是需人工确认清单（BRIEF：不许埋在明细里）
    L.append(sec(f"需要人工确认的条目（{s['needs_review']} 条）"))
    L.append("")
    if s["review_items"]:
        L.append("| 类别 | 条目 | 状态 | 为什么 | 怎么办 |")
        L.append("|---|---|---|---|---|")
        for it in s["review_items"]:
            L.append(f"| {_cell(it['class'])} | {_cell(it['item'])} | {_cell(it['status'])} | "
                     f"{_cell(it['why'])} | {_cell(it['how'])} |")
    else:
        L.append("无。这个库里出现的每一个素材都有许可留痕。")
    L.append("")
    L.append(f"**结论**：{s['verdict']}")
    L.append("")

    # ---- 音频
    a = report["audio"]
    L.append(sec(f"音频（BGM / 音效）· {len(a.get('rows') or [])} 条"))
    L.append("")
    L.append(f"读数出处：`{a.get('reached_by') or '无'}`")
    L.append("")
    if a.get("rows"):
        L.append("| 类型 | id / 名称 | 文件 | 许可 | 状态 | 留痕文件 |")
        L.append("|---|---|---|---|---|---|")
        for r in a["rows"]:
            proofs = "、".join(r.get("proof_files") or []) or "—"
            L.append(f"| {_cell(r.get('kind'))} | {_cell(r.get('id'))} | {_cell(_disp(r.get('file')))} | "
                     f"{_cell(r.get('licence'))} | {_cell(r.get('status'))} | {_cell(proofs)} |")
        L.append("")
        L.append("逐条为什么这么判：")
        L.append("")
        for r in a["rows"]:
            L.append(f"- `{r.get('id')}` → {_cell(r.get('why'))}"
                     + (f"；sha256 前 16 位 `{r['sha256_16']}`" if r.get("sha256_16") else ""))
    else:
        L.append(a.get("note") or "无音频条目")
    L.append("")

    # ---- 字体
    f = report.get("fonts")
    if f is not None:
        L.append(sec(f"字体 · {len(f.get('rows') or [])} 条"))
        L.append("")
        if f.get("rows"):
            L.append("| 字体 | 族 | 文件 | license | 状态 | 用在哪 |")
            L.append("|---|---|---|---|---|---|")
            for r in f["rows"]:
                L.append(f"| {_cell(r.get('name'))} | {_cell(r.get('family'))} | {_cell(r.get('file'))} | "
                         f"{_cell(r.get('license'))} | {_cell(r.get('status'))} | "
                         f"{_cell('、'.join(r.get('used_by') or []))} |")
            L.append("")
            for r in f["rows"]:
                L.append(f"- `{r.get('name')}`：{(r.get('license_evidence') or '—')}"
                         + (f"（{r['note']}）" if r.get("note") else ""))
        else:
            L.append(f.get("note") or "无字体条目")
        L.append("")

    # ---- 贴纸
    st = report.get("stickers")
    if st is not None:
        decl = st.get("declaration") or {}
        L.append(sec(f"贴纸 · {len(st.get('rows') or [])} 条出场"))
        L.append("")
        L.append(f"**声明**：{_cell(decl.get('origin'))}")
        L.append("")
        L.append(f"- 作者：{_cell(decl.get('author'))}")
        L.append(f"- 许可：{_cell(decl.get('license'))}")
        L.append(f"- 第三方图形素材：{'有，见上表需确认清单' if decl.get('third_party_risk') else '无（外链位图扫描 0 命中）'}")
        L.append(f"- 动画引擎：{_cell(decl.get('animation_engine'))}")
        L.append("")
        if st.get("rows"):
            L.append("| 出场贴纸 | 短签 | 来源 | 出现在 |")
            L.append("|---|---|---|---|")
            for r in st["rows"]:
                L.append(f"| {_cell(r.get('id'))} | {_cell(r.get('label'))} | {_cell(r.get('origin'))} | "
                         f"{_cell('、'.join(str(x) for x in (r.get('at') or [])))} |")
            L.append("")
            L.append(f"其余 {st.get('unused_count')} 款内置贴纸本片未出场。{st.get('note') or ''}")
        else:
            L.append(st.get("note") or "无贴纸")
        L.append("")

    # ---- 代码场景
    sc = report.get("scenes")
    if sc is not None:
        L.append(sec(f"代码场景 · {len(sc.get('rows') or [])} 条"))
        L.append("")
        if sc.get("rows"):
            L.append("| 场景 id | 作者 | 代码出处 | 授权 | 状态 |")
            L.append("|---|---|---|---|---|")
            for r in sc["rows"]:
                L.append(f"| {_cell(r.get('id'))} | {_cell(r.get('author'))} | {_cell(r.get('source_file'))} | "
                         f"{_cell(r.get('license'))} | {'unverified' if r.get('needs_review') else 'ok'} |")
            L.append("")
            for r in sc["rows"]:
                L.append(f"- `{r.get('id')}`：{_cell(r.get('evidence'))}")
        else:
            L.append(sc.get("note") or "无代码场景")
        L.append("")

    # ---- 素材库的实拍素材
    if report["scope"] == "library":
        L.append(sec(f"实拍素材 · {len(report['assets'].get('rows') or [])} 条"))
        L.append("")
        if report["assets"].get("rows"):
            L.append("| 素材 id | 文件 | 授权 | 状态 |")
            L.append("|---|---|---|---|")
            for r in report["assets"]["rows"]:
                L.append(f"| {_cell(r.get('id'))} | {_cell(r.get('file'))} | {_cell(r.get('licence'))} | "
                         f"{_cell(r.get('status'))} |")
            L.append("")
            L.append(f"授权留痕文件：`{report['assets'].get('rights_file') or '无'}`")
        else:
            L.append(report["assets"].get("note") or "无素材")
        L.append("")

    # ---- 运行时依赖
    rt = report.get("runtime")
    if rt and rt.get("rows"):
        L.append("## 附、运行时依赖（非素材）")
        L.append("")
        L.append(f"> {rt['note']}")
        L.append("")
        L.append("| 包 | 版本 | 许可（照抄依赖文件头） | 条款全文在仓内 | 证据 |")
        L.append("|---|---|---|---|---|")
        for r in rt["rows"]:
            L.append(f"| {_cell(r.get('package'))} | {_cell(r.get('version'))} | {_cell(r.get('license'))} | "
                     f"{'是' if r.get('terms_saved_in_repo') else '否'} | {_cell(r.get('evidence'))} |")
        L.append("")
        for r in rt["rows"]:
            L.append(f"- {_cell(r.get('package'))}：{_cell(r.get('note'))}"
                     + (f"｜许可页 {r['license_url']}" if r.get("license_url") else "")
                     + (f"｜文件头原文「{r['quote']}」" if r.get("quote") else ""))
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"{s['boundary']}")
    L.append("")
    return "\n".join(L)


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "licensing.json"
    m = out_dir / "licensing.md"
    j.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    m.write_text(render_md(report), encoding="utf-8")
    return j, m


def main() -> int:
    ap = argparse.ArgumentParser(description="授权总表：这条成片/这个素材库用了什么，能不能商用")
    ap.add_argument("--project", default=None, help="项目目录")
    ap.add_argument("--library", default=None, help="素材库目录（给素材库出总表）")
    ap.add_argument("--json", action="store_true", help="打印 JSON（不打印人读表）")
    ap.add_argument("--write", action="store_true", help="素材库模式也落盘到库内 _cutskills/")
    a = ap.parse_args()

    if bool(a.project) == bool(a.library):
        print("--project 和 --library 二选一", file=sys.stderr)
        return 2

    if a.project:
        proj = Path(a.project).expanduser().resolve()
        if not proj.is_dir():
            print(f"✗ 项目目录不存在：{proj}", file=sys.stderr)
            return 2
        report = scan_project(proj)
        j, m = write_report(report, proj / "out")
        if not a.json:
            s = report["summary"]
            print(f"{s['headline']}")
            for it in s["review_items"]:
                print(f"  ⚠ {it['class']}｜{it['item']}：{it['why']}")
            print(f"→ {_disp(j)}\n→ {_disp(m)}")
        else:
            print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    lib = Path(a.library).expanduser().resolve()
    if not lib.is_dir():
        print(f"✗ 素材库目录不存在：{lib}", file=sys.stderr)
        return 2
    report = scan_library(lib)
    if a.write:
        j, m = write_report(report, lib / "_cutskills")
        if not a.json:
            print(f"→ {_disp(j)}\n→ {_disp(m)}")
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_md(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
