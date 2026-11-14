#!/usr/bin/env python3
"""`licensing.py` / `cover.py` 的离线回归：授权总表与封面文案的正负样本。

守的是四件事，别再退回去：
  ① **未核验的素材必须被标出来**（负样本）：外部 BGM、字体表里没有 license 的字体、
     时间轴里不认识的贴纸 id、找不到代码的场景 id —— 一条都不许漏，而且必须出现在
     总表**顶部**的「需人工确认」栏里，不许埋进明细等人自己翻；
  ② **代码自绘不许被误标成外部素材**（正样本）：16 款内置贴纸是 `stickers.py` 里的内联
     SVG，必须标 `code_drawn` 且**不进**需确认清单——把它标成「侵权风险」等于让人
     去核一个不存在的第三方；
  ③ **不许猜授权**：许可查不到就是 `unverified`，字体文件里读不到 name 表就报「无法核验」
     而不是报「清白」；
  ④ **封面标题守 G-06**：≤12 字、无标点、不出现原文没有的数字/承诺；每条标题都能指回
     片子里的原话（正负样本各一组）。

只读临时目录 + 仓内已有的代码；不碰真实素材库、不起渲染，跑一次 2 秒内。

    python3 scripts/test_licensing.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


# ---------------------------------------------------------------- 夹具

VO = [
    (1, "setup", "国产和进口的材料，差的不只是价格（十年了我敢讲）"),
    (2, "claim", "订单多了，老板更忙了，最后现金却更紧。"),
    (3, "payoff", "所以别站队，按项目定。该用在哪，就用在哪。"),
]


def mk_audio_lib(root: Path, proof: bool = True, tag: str = "sfx") -> Path:
    """一个迷你音效库：catalog.csv + 两个 wav +（可选）许可留痕文件。"""
    lib = root / f"{tag}-lib"
    (lib / "sounds").mkdir(parents=True, exist_ok=True)
    (lib / "catalog.csv").write_text(
        "id,name,file,duration_s,sync_at_s,under_voiceover_db,use_zh\n"
        + "".join(f"{i},n,sounds/{i}.wav,0.4,0.2,-6,卡点\n"
                  for i in ("pop-medium-in", "whoosh-medium-soft", "impact-light-firm")),
        encoding="utf-8")
    for i in ("pop-medium-in", "whoosh-medium-soft", "impact-light-firm"):
        (lib / "sounds" / f"{i}.wav").write_bytes(b"RIFF0000WAVE")
    if proof:
        (lib / "LICENSE-CC0.txt").write_text("CC0 1.0 Universal\n", encoding="utf-8")
    return lib


def mk_proj(root: Path, *, clean: bool, sfx_proof: bool = True) -> Path:
    """造一个项目：clean=True 时音频/贴纸/场景/字体全部有据；False 时四处各埋一个坑。"""
    proj = root / "lib" / "_cutskills" / "projects" / "t-lic"
    (proj / "out").mkdir(parents=True, exist_ok=True)
    stickers = [{"id": "sparkle-star", "at": 12.4, "dur": 2, "pos": "top-left"}]
    if not clean:
        stickers.append({"id": "brand-logo-external", "at": 30.0, "dur": 2, "pos": "bottom-right"})
    tl = {
        "version": 5, "slug": "t-lic", "duration_s": 46.8, "pack": "P5-viral-pop",
        "style_pack": "talking-head-v1",
        "title_bar": {"top": "做材料前必看", "main": "国产进口怎么选", "mode": "hook", "dur": 4.5},
        "stickers": stickers,
        "elements": [
            {"n": n, "kind": "clip", "track": "video", "asset": f"v/{n}.mov", "in": 0.8,
             "out": 4.2, "medium": "talking", "role": role, "evidence": text}
            for n, role, text in VO
        ] + [{"n": 4, "kind": "gen", "track": "video", "dur": 8.0, "template": "scene",
              "props": {"scene": "order_cash" if clean else "no_such_scene_xyz"},
              "role": "evidence", "evidence": "画面自己说话"}],
    }
    (proj / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    (proj / "ideation.json").write_text(json.dumps({
        "topic_raw": "国产和进口材料怎么选", "proposed": 1, "selected_proposal": 1,
        "proposals": [{"index": 1, "hooks": [{"id": "H01", "line": "进口材料贵一倍，贵在哪你真知道吗？"}]}],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (proj / "brief.json").write_text(json.dumps({
        "slug": "t-lic", "audience": "正在选材的业主", "change": "从站队变成按项目定",
        "length_s": 45, "pack": "P5-viral-pop", "style_pack": "talking-head-v1"},
        ensure_ascii=False, indent=1), encoding="utf-8")

    lib = mk_audio_lib(root, proof=sfx_proof)
    if clean:
        bgm = lib / "sounds" / "pop-medium-in.wav"
    else:                       # 外部文件：不在任何带留痕的库根里
        bgm = root / "external-client-track.mp3"
        bgm.write_bytes(b"\x00" * 64)
    (proj / "bgm.json").write_text(json.dumps(
        {"source": "cc0" if clean else "external", "title": "客户参考曲", "file": str(bgm)},
        ensure_ascii=False), encoding="utf-8")
    (proj / "out" / "sfx.json").write_text(json.dumps({"events": [
        {"t": 0.1, "name": "whoosh", "nth": 0}, {"t": 4.2, "name": "pop", "nth": 0}]},
        ensure_ascii=False), encoding="utf-8")
    return proj


def patch_audio(lib: Path):
    """把 cc0_lib 的库根指到临时库——licence_of 认的是「库根有没有留痕」，不是路径长得像。"""
    import cc0_lib
    old = (cc0_lib.BGM_LIB, cc0_lib.SFX_LIB)
    cc0_lib.BGM_LIB = cc0_lib.SFX_LIB = lib
    cc0_lib._cache.clear()
    return old


def restore_audio(old) -> None:
    import cc0_lib
    cc0_lib.BGM_LIB, cc0_lib.SFX_LIB = old
    cc0_lib._cache.clear()


# ---------------------------------------------------------------- ① 正样本

def case_clean() -> None:
    print("① 正样本：音频/贴纸/场景/字体全部有据 → 总表不该报需确认")
    import licensing
    with tempfile.TemporaryDirectory() as td:
        proj = mk_proj(Path(td), clean=True)
        old = patch_audio(Path(td) / "sfx-lib")
        try:
            rep = licensing.scan_project(proj)
        finally:
            restore_audio(old)
        must("结论是「可商用」", rep["summary"]["verdict"].startswith("全部条目"), rep["summary"]["verdict"])
        must("需确认清单为空", rep["summary"]["needs_review"] == 0, str(rep["summary"]["review_items"]))
        must("BGM 逐文件判成 CC0", any(r["status"] == "ok" and r["kind"] == "bgm"
                                      for r in rep["audio"]["rows"]),
             json.dumps(rep["audio"]["rows"], ensure_ascii=False)[:200])
        must("音效按文件名去重后每条都有许可",
             all(r["status"] in ("ok", "procedural") for r in rep["audio"]["rows"]),
             str([(r["id"], r["status"]) for r in rep["audio"]["rows"]]))
        md = licensing.render_md(rep)
        must("人读表第一栏就是需确认栏", "## 一、需要人工确认的条目（0 条）" in md)
        must("正样本不出现「unverified」字样", "unverified" not in md.split("## 二、")[0], md[:400])


# ---------------------------------------------------------------- ② 负样本

def case_dirty() -> None:
    print("② 负样本：外部 BGM / 未登记贴纸 / 找不到代码的场景 → 必须全部标出来")
    import licensing
    with tempfile.TemporaryDirectory() as td:
        proj = mk_proj(Path(td), clean=False, sfx_proof=False)
        old = patch_audio(Path(td) / "sfx-lib")
        try:
            rep = licensing.scan_project(proj)
        finally:
            restore_audio(old)
        items = rep["summary"]["review_items"]
        classes = {(i["class"], str(i["item"])) for i in items}
        must("外部 BGM 进了需确认清单",
             any(c == "音频" for c, _ in classes), str(classes))
        must("未登记贴纸 id 进了需确认清单",
             any(c == "贴纸" and "brand-logo-external" in i for c, i in classes), str(classes))
        must("找不到代码的场景进了需确认清单",
             any(c == "代码场景" and "no_such_scene_xyz" in i for c, i in classes), str(classes))
        must("无留痕库根的音频判成 unverified（不猜 CC0）",
             any(r["status"] == "unverified" for r in rep["audio"]["rows"]),
             str([(r["id"], r["status"], r["licence"]) for r in rep["audio"]["rows"]]))
        md = licensing.render_md(rep)
        head = md.split("## 二、")[0]
        must("需确认清单在总表顶部（第一栏，早于明细）",
             "## 一、需要人工确认的条目（" in head and head.index("需要人工确认") < len(head))
        for needle in ("brand-logo-external", "no_such_scene_xyz"):
            must(f"顶部汇总里点名了 {needle}", needle in head, head[:400])
        must("顶部结论是红的（❌ 需人工确认）", "❌" in head, head[:200])
        # 顶部栏必须在「音频/字体/贴纸/场景」明细之前
        i_review = md.index("## 一、需要人工确认的条目")
        i_detail = min(md.index("## 二、"), md.index("## 三、"))
        must("需确认栏排在全部明细之前", i_review < i_detail)


# ---------------------------------------------------------------- ③ 自绘贴纸

def case_stickers() -> None:
    print("③ 自绘贴纸：必须标 code_drawn，且不许被误标成外部素材侵权风险")
    import licensing
    import stickers
    decl = stickers.licensing_declaration()
    must("声明里写的是代码自绘", decl["kind"] == "code_drawn_svg" and "自绘" in decl["origin"],
         str(decl["kind"]))
    must("16 款全部是自绘", all(i["origin"] == "code_drawn" for i in decl["items"]),
         str([i["id"] for i in decl["items"] if i["origin"] != "code_drawn"]))
    must("外链位图扫描为空（没偷放外部素材）", decl["external_refs"] == [], str(decl["external_refs"]))
    must("声明不把自绘算成第三方风险", decl["third_party_risk"] is False)
    with tempfile.TemporaryDirectory() as td:
        proj = mk_proj(Path(td), clean=True)
        tl = json.loads((proj / "timeline.json").read_text())
        block = licensing.stickers_block(tl)
        must("片里出场的贴纸标成 code_drawn",
             all(r["origin"] == "code_drawn" for r in block["rows"]), str(block["rows"]))
        must("自绘贴纸不进需确认清单", block["needs_review"] is False, str(block["rows"]))
        must("码里出现「自绘 SVG」这句人话", "自绘" in block["rows"][0]["why"], block["rows"][0]["why"])
        # 负样本：一旦有外链图形，声明必须翻脸
        old = dict(stickers.STICKER_CATALOG["bang"])
        try:
            stickers.STICKER_CATALOG["bang"] = {
                **old, "svg": old["svg"].replace("<circle", '<image href="https://x/y.png"/><circle', 1)}
            bad = stickers.licensing_declaration()
        finally:
            stickers.STICKER_CATALOG["bang"] = old
        must("负样本：塞进外链位图后third_party_risk 翻成 True", bad["third_party_risk"] is True)
        must("负样本：那一款被判成 external_asset",
             any(i["origin"] == "external_asset" for i in bad["items"]))
        must("负样本：清单里点名了它", "bang" in (bad["needs_review"] or []))


# ---------------------------------------------------------------- ④ 字体授权

def case_fonts() -> None:
    print("④ 字体：表里没 license → unverified；读到文件才算核验；读不到报「无法核验」")
    import fonts
    with tempfile.TemporaryDirectory() as td:
        tbl = Path(td) / "fonts.yaml"
        tbl.write_text(
            "target_unit_w: 0.074\nfonts:\n"
            "  有授权字:\n    file: NotoSansSC-Black.ttf\n    ratio: 1.0\n"
            "    file_prefixes: [NotoSansSC]\n    license: SIL Open Font License 1.1\n"
            "  无授权字:\n    file: Foo-Bold.ttf\n    ratio: 1.0\n"
            "  有授权无文件:\n    file: NoSuchFont-900.ttf\n    ratio: 1.0\n"
            "    license: SIL Open Font License 1.1\n"
            "default: 有授权字\n", encoding="utf-8")
        old = fonts.TABLE
        fonts.TABLE = tbl
        try:
            ok = fonts.licence_of("有授权字")
            none = fonts.licence_of("无授权字")
            nofile = fonts.licence_of("有授权无文件")
            unknown = fonts.licence_of("根本不存在的字体")
        finally:
            fonts.TABLE = old
        must("没有 license 字段 → unverified",
             none["status"] == "unverified" and none["license"] == "unverified", none["status"])
        must("没有 license 字段 → 提示人工确认", "人工确认" in none["note"], none["note"])
        must("表里没有的字体 → unverified", unknown["status"] == "unverified", unknown["status"])
        must("有 license 但本机没文件 → partial（不许报 ok）",
             nofile["status"] == "partial", nofile["status"])
        must("partial 也不当「已核验」", nofile["verified_in_file"] is None, str(nofile["verified_in_file"]))
        must("有授权字：文件在就核验、不在就 partial，绝不谎报",
             ok["status"] in ("ok", "partial"), ok["status"])
        must("同族变体也能认（NotoSansSC-SemiBold → 思源黑体那条）",
             fonts.entry_of("NotoSansSC-SemiBold")[0] is not None,
             str(fonts.entry_of("NotoSansSC-SemiBold")))
        # 仓库真表：三只字体都得填 license 与依据（不许空着等下一个来猜）
        real = fonts.load().get("fonts") or {}
        must("styles/fonts.yaml 每只字体都填了 license",
             bool(real) and all(str(v.get("license") or "").strip() for v in real.values()),
             str([k for k, v in real.items() if not str(v.get("license") or "").strip()]))
        must("styles/fonts.yaml 每只字体都写了许可依据",
             all(str(v.get("license_evidence") or "").strip() for v in real.values()),
             str([k for k, v in real.items() if not str(v.get("license_evidence") or "").strip()]))
        # 读不出 name 表时必须是 None（= 无法核验），不是空字典糊过去
        junk = Path(td) / "junk.ttf"
        junk.write_bytes(b"not a font at all")
        must("垃圾文件读 name 表返回 None（读不出 ≠ 没授权，也 ≠ 清白）",
             fonts.read_name_table(junk) is None)
        real_font = fonts.file_path("得意黑") or fonts.file_path("思源黑体")
        if real_font:
            nt = fonts.read_name_table(real_font) or {}
            must("真实字体能读出自带的许可描述（ID 13）",
                 "open font license" in str(nt.get("license") or "").lower(),
                 str(nt.get("license"))[:80])
            must("真实字体的核验结果与表里记录一致",
                 fonts.licence_of("得意黑")["verified_in_file"] in (True, None))
        else:
            print("  ⏭ 本机没装得意黑/思源黑体，跳过「读文件核验」这一条")


# ---------------------------------------------------------------- ⑤ 复用 G-07 口径

def case_manifest_reuse() -> None:
    print("⑤ 音频判据不重造：有 out/sfx_manifest.json 就照它读数")
    import licensing
    with tempfile.TemporaryDirectory() as td:
        proj = mk_proj(Path(td), clean=False)
        (proj / "out" / "sfx_manifest.json").write_text(json.dumps({
            "licence_summary": "存在未核验来源",
            "bgm": {"source": "unverified", "title": "客户参考曲", "file": None,
                    "licence": None, "note": "许可未核验：商用发布前必须人工确认授权"},
            "sfx": [{"name": "pop", "id": "pop-medium-in", "file": "/nope/pop.wav",
                     "source": "unverified", "licence": None, "sha256_16": "abc123"},
                    {"name": "gen", "source": "procedural", "licence": None, "file": None}],
        }, ensure_ascii=False), encoding="utf-8")
        rep = licensing.scan_project(proj)
        a = rep["audio"]
        must("读数出处指向 sfx_manifest.json", a["reached_by"] == "out/sfx_manifest.json", str(a["reached_by"]))
        must("manifest 里的 row 数原样复用（1 BGM + 2 音效）", len(a["rows"]) == 3, str(len(a["rows"])))
        proc = [r for r in a["rows"] if r["status"] == "procedural"]
        must("procedural（本地合成）不算未核验", len(proc) == 1 and proc[0]["needs_review"] is False,
             str([(r["status"], r["needs_review"]) for r in a["rows"]]))
        must("manifest 里 licence=None 的行被判 unverified",
             sum(1 for r in a["rows"] if r["status"] == "unverified") == 2,
             str([(r["id"], r["status"]) for r in a["rows"]]))
        must("sha256 留痕带过来了", any(r.get("sha256_16") == "abc123" for r in a["rows"]))


# ---------------------------------------------------------------- ⑥ 封面文案

def case_cover() -> None:
    print("⑥ 封面文案：3 个主标题可追溯、≤12 字、零无来源数字/承诺")
    import cover
    # 守卫本身的正负样本
    must("负样本：「省一半」被拦", cover.unsafe("省一半的预算") is not None)
    must("负样本：「进口贵一倍」被拦（数字+量词）", cover.unsafe("进口贵一倍") is not None)
    must("负样本：阿拉伯数字被拦", cover.unsafe("7 天装完") is not None)
    must("负样本：「必看/最强」营销腔被拦", cover.unsafe("装修必看") is not None)
    must("正样本：口播原话「差的不只是价格」放行", cover.unsafe("差的不只是价格") is None)
    must("正样本：「按项目定」放行", cover.unsafe("按项目定") is None)

    with tempfile.TemporaryDirectory() as td:
        proj = mk_proj(Path(td), clean=True)
        rep = cover.build(proj)
        titles = rep["titles"]
        must("生成 3 个主标题", len(titles) == 3, str([t["text"] for t in titles]))
        must("每个都 ≤12 字", all(t["chars"] <= 12 for t in titles), str([t["chars"] for t in titles]))
        must("每个都不带标点", all(not any(ch in t["text"] for ch in cover.PUNCT) for t in titles),
             str([t["text"] for t in titles]))
        must("每个都过 G-06 守卫（零无来源数字/承诺）",
             all(cover.unsafe(t["text"]) is None for t in titles),
             str([(t["text"], cover.unsafe(t["text"])) for t in titles]))
        vo_text = "".join(t for _n, _r, t in VO)
        must("每条都能指回原话（片段必须真的在口播里）",
             all(t["source"].get("quote") in vo_text and t["source"]["clause"] in t["text"]
                 for t in titles),
             str([(t["text"], t["source"]) for t in titles]))
        must("三条来自三处不同的口播（不是同一句的三种写法）",
             len({t["source"].get("element") for t in titles}) == 3,
             str([t["source"].get("element") for t in titles]))
        pub = rep["publish_title"]
        must("有发布标题且守 G-06", bool(pub.get("text")) and cover.unsafe(pub["text"]) is None,
             str(pub.get("text")) + "｜" + str(pub.get("note")))
        must("带数字的钩子被拦并留了记录",
             any("一倍" in r["text"] for r in (pub.get("guard_rejected") or [])),
             str(pub.get("guard_rejected")))
        must("副文案 ≤20 字且可追溯",
             bool(rep["subtitle"].get("text")) and rep["subtitle"]["chars"] <= 20
             and bool(rep["subtitle"].get("source")), str(rep["subtitle"]))
        md = cover.render_md(rep)
        must("人读表里写了原话（可追溯）", "原话" in md and VO[0][2][:6] in md)
        # 空项目：不许编，要明说追不到
        empty = Path(td) / "empty-proj"
        empty.mkdir()
        rep2 = cover.build(empty)
        must("没有口播真值时标题为空、且带警告（不编文案）",
             rep2["titles"] == [] and rep2["warnings"], str(rep2["warnings"]))


def main() -> int:
    case_clean()
    case_dirty()
    case_stickers()
    case_fonts()
    case_manifest_reuse()
    case_cover()
    print()
    if fails:
        print(f"❌ {len(fails)} 条不过：" + "、".join(fails))
        return 1
    print("✅ 授权总表与封面文案的正负样本全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
