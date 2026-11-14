#!/usr/bin/env python3
"""剪映草稿单向导出的合同测试。

要抓的四类错，每一类都曾经在别的工具里真的把草稿写坏：

  ① 目录/文件不成套 → 剪映里看不到草稿；meta 缺 `draft_materials` → 时间轴变红框空素材
  ② `source.duration ≠ target.duration × speed` → 剪映自己「修」一下，画面就错位
  ③ 时长不在帧网格上 → 首尾互相啃掉一帧，越剪越短
  ④ 字幕 `range` 按字符串长度算 → 一条带 emoji 的字幕后半句全丢样式（emoji 是代理对，
     UTF-16 算 2 个 code unit，Python 的 len() 只算 1）

另外三条是「宁可不出，也不出坏草稿」的底线：
  ⑤ 素材路径漏在草稿目录外 → 剪映 Mac 沙盒报「暂无访问权限」
  ⑥ 本机版本指纹 version > 360000 → 状态必须是 refused，且一个目录都不许留
  ⑦ 结构自检不过 → 半成品目录必须删干净（不能污染剪映的草稿列表）
  ⑧ --dry-run → 一行都不落盘

    python3 scripts/test_jianying_export.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_jianying as EJ  # noqa: E402

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def sh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


VIDEO_A = "batch/a_claim.mp4"
AUDIO_M = "batch/bed.wav"
EMOJI_CUE = "我打十个样😀看手感"          # 😀 是代理对：UTF-16 比 len() 多 1


def make_lib(td: Path) -> tuple[Path, Path]:
    """造一个「真素材库形状」的最小库：索引 + 两个真文件 + 一个项目。

    真文件而不是空壳：导出要复制素材、要抽封面帧，空文件会把这两步测成假绿。
    """
    lib = td / "lib"
    (lib / "_cutskills").mkdir(parents=True)
    media = lib / "batch"
    media.mkdir()
    # 真视频（0.5s、1080×1920、30fps）：封面抽帧得有真帧可抽
    r = sh(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "color=c=0x334455:s=540x960:d=0.5:r=30",
            "-pix_fmt", "yuv420p", str(media / "a_claim.mp4")])
    if r.returncode != 0 or not (media / "a_claim.mp4").exists():
        raise SystemExit(f"造测试视频失败：{(r.stderr or '')[-400:]}")
    wav = sh(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
              "-i", "sine=frequency=440:duration=6.0", str(media / "bed.wav")])
    if wav.returncode != 0:
        raise SystemExit(f"造测试音频失败：{(wav.stderr or '')[-400:]}")

    index = {"version": 2, "library": str(lib), "counts": {}, "assets": [
        {"id": VIDEO_A, "path": VIDEO_A, "kind": "video", "duration": 6.0,
         "width": 1080, "height": 1920, "fps": 30.0},
        {"id": AUDIO_M, "path": AUDIO_M, "kind": "audio", "duration": 6.0},
    ]}
    (lib / "_cutskills" / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")

    proj = lib / "_cutskills" / "projects" / "demo"
    (proj / "out").mkdir(parents=True)
    # 项目真值：1 条视频 + 1 条音频（视频自己没原声，声音走独立音轨）
    (proj / "timeline.json").write_text(json.dumps({
        "version": 5, "duration_s": 4.0, "pack": "P1-warm", "axis": "A",
        "canvas": {"width": 1080, "height": 1920},
        "elements": [
            {"n": 1, "kind": "clip", "track": "video", "asset": VIDEO_A,
             "in": 0.02, "out": 2.52, "medium": "broll", "source": "library",
             "why": "测试用第一格",
             "audio": {"asset": AUDIO_M, "in": 0.0, "out": 2.5}},
            {"n": 2, "kind": "clip", "track": "video", "asset": VIDEO_A,
             "in": 2.5, "out": 4.0, "medium": "broll", "source": "library",
             "why": "测试用第二格（自带原声，不该另起音轨）",
             "audio": {"asset": VIDEO_A, "in": 2.5, "out": 4.0}},
        ]}, ensure_ascii=False), encoding="utf-8")
    (proj / "out" / "subs.json").write_text(json.dumps({
        "band": [1034, 1554], "size": 82, "font": "得意黑",
        "subtitle_system": "single-line", "suppressed": [],
        "cues": [
            {"start": 0.0, "end": 1.2, "text": EMOJI_CUE, "chars": len(EMOJI_CUE),
             "units": 8.0, "sf": 0, "ef": 36, "el": 1},
            {"start": 1.2, "end": 2.6, "text": "三个厂家一起比，。", "chars": 7,
             "units": 7.0, "sf": 36, "ef": 78, "el": 1},
        ]}, ensure_ascii=False), encoding="utf-8")
    return lib, proj


def run(proj: Path, out: Path, root: Path, *extra: str) -> subprocess.CompletedProcess:
    return sh([sys.executable, str(HERE / "export_jianying.py"),
               "--project", str(proj), "--out", str(out),
               "--draft-root", str(root), *extra])


def segs(draft: Path) -> dict[str, list[dict]]:
    content = json.loads((draft / "draft_info.json").read_text(encoding="utf-8"))
    return {tr["type"]: tr["segments"] for tr in content["tracks"]}


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix="jytest-"))
    lib, proj = make_lib(td)
    root = td / "draftroot"
    root.mkdir()
    draft = td / "drafts" / "demo"

    print("① 正常导出：目录成套、两轨三段、封面在")
    r = run(proj, draft, root)
    must("导出退出码 0", r.returncode == 0, (r.stderr or r.stdout).strip()[-500:])
    must("draft_info.json 存在", (draft / "draft_info.json").exists())
    must("draft_meta_info.json 存在", (draft / "draft_meta_info.json").exists())
    must("draft_cover.jpg 存在", (draft / "draft_cover.jpg").exists()
         and (draft / "draft_cover.jpg").stat().st_size > 0)
    rep = json.loads((proj / "out" / "jianying-export.json").read_text(encoding="utf-8"))
    must("状态 ok", rep.get("status") == "ok", json.dumps(rep, ensure_ascii=False)[:400])
    tr = segs(draft)
    must("video 轨 2 段", len(tr.get("video") or []) == 2, str({k: len(v) for k, v in tr.items()}))
    must("audio 轨 1 段（自带原声的那格不另起音轨）", len(tr.get("audio") or []) == 1,
         str({k: len(v) for k, v in tr.items()}))
    must("text 轨 2 段", len(tr.get("text") or []) == 2)

    print("② 源/目标时长不变式：source.duration == target.duration × speed")
    bad = []
    for ttype, segs_ in tr.items():
        for s in segs_:
            td_us = s["target_timerange"]["duration"]
            sd_us = s["source_timerange"]["duration"]
            sp = s.get("speed", 1.0)
            if abs(sd_us - td_us * sp) > 1:
                bad.append(f"{ttype}: source={sd_us} target={td_us} speed={sp}")
    must("每个片段 source == target × speed（容差 1µs）", not bad, "；".join(bad[:3]))

    print("③ 帧网格：所有 target.duration 是 1/fps 的整数倍")
    off = []
    for ttype, segs_ in tr.items():
        for s in segs_:
            d = s["target_timerange"]["duration"]
            n = round(d * 30 / 1_000_000)
            if abs(d - n * 1_000_000 / 30) > 1:
                off.append(f"{ttype}@{s['target_timerange']['start']}={d}µs")
    must("全部时长贴帧", not off, "；".join(off[:3]))

    print("④ 字幕 range 按 UTF-16 code unit 算（含 emoji 的代理对）")
    content = json.loads((draft / "draft_info.json").read_text(encoding="utf-8"))
    texts = content["materials"]["texts"]
    got = {}
    for m in texts:
        payload = json.loads(m["content"])
        got[payload["text"]] = payload["styles"][0]["range"]
    want_u16 = len(EMOJI_CUE.encode("utf-16-le")) // 2
    must("emoji 那条 range 结束位 = UTF-16 长度",
         got.get(EMOJI_CUE, [None, None])[1] == want_u16,
         f"range={got.get(EMOJI_CUE)} 期望 {want_u16}（len()={len(EMOJI_CUE)}）")
    must("range 没按 len() 算（代理对没被漏掉）", want_u16 != len(EMOJI_CUE),
         "这条字幕必须有代理对，否则测了个寂寞")
    must("range 起点是 0 且覆盖全文",
         all(r[0] == 0 for r in got.values()), str(got))
    must("字幕文字里的标点被去掉（沿用 subs.PUNCT 口径）",
         "三个厂家一起比" in got and "，" not in "".join(got), str(list(got)))

    print("⑤ 素材全在草稿目录内（沙盒能读到）")
    outside = []
    for kind in ("videos", "audios", "images"):
        for m in content["materials"].get(kind) or []:
            p = Path(m["path"])
            if p.is_absolute() or not (draft / p).exists():
                outside.append(f"{kind}:{m['path']}")
    must("主文件里没有外部绝对路径", not outside, "；".join(outside[:3]))
    meta = json.loads((draft / "draft_meta_info.json").read_text(encoding="utf-8"))
    dm = meta["draft_materials"][0]["value"]
    must("draft_materials(type 0) 登记了导入素材",
         len(dm) == 3 and all((draft / it["file_Path"]).exists() for it in dm),
         f"{len(dm)} 条")
    must("meta.draft_fold_path 指向草稿目录",
         Path(meta["draft_fold_path"]).resolve() == draft.resolve())

    print("⑥ 版本指纹 version > 360000 → refused，且不留半成品目录")
    bad_root = td / "draftroot-future"
    old = bad_root / "2026草稿"
    old.mkdir(parents=True)
    (old / "draft_info.json").write_text(json.dumps({
        "version": 380000, "platform": {"app_version": "9.9.9", "app_source": "lv"}},
        ensure_ascii=False), encoding="utf-8")
    (old / "draft_meta_info.json").write_text(json.dumps(
        {"tm_draft_modified": 1794681110100622}), encoding="utf-8")
    future = td / "drafts" / "demo-future"
    r6 = run(proj, future, bad_root)
    must("退出码 2（refused）", r6.returncode == 2, r6.stdout.strip()[-300:])
    rep6 = json.loads((proj / "out" / "jianying-export.json").read_text(encoding="utf-8"))
    must("状态 refused", rep6.get("status") == "refused", str(rep6.get("status")))
    must("reason 里给了 mp4 人工精修替代路径",
         "mp4" in str(rep6.get("reason")), str(rep6.get("reason"))[:200])
    must("version 记进报告", rep6.get("version") == 380000, str(rep6.get("version")))
    must("没有留下草稿目录", not future.exists(), str(list(future.iterdir())
                                                  if future.exists() else []))

    print("⑦ 结构自检失败 → 半成品目录删干净")
    orig = EJ.structure_check
    draft7 = td / "drafts" / "demo-broken"
    EJ.structure_check = lambda *a, **k: ["注入的自检失败"]
    try:
        code7 = EJ.main(["--project", str(proj), "--out", str(draft7),
                         "--draft-root", str(root), "--force"])
    finally:
        EJ.structure_check = orig
    rep7 = json.loads((proj / "out" / "jianying-export.json").read_text(encoding="utf-8"))
    must("退出码 1（degraded）", code7 == 1, str(code7))
    must("状态 degraded", rep7.get("status") == "degraded", str(rep7.get("status")))
    must("坏草稿目录已删除", not draft7.exists(), str(draft7))
    must("报告里带失败项", bool(rep7.get("failed_checks")), str(rep7.get("failed_checks")))

    print("⑧ --dry-run 一行都不落盘")
    dry = td / "drafts" / "demo-dry"
    before = (proj / "out" / "jianying-export.json").stat().st_mtime_ns
    r8 = run(proj, dry, root, "--dry-run")
    must("dry-run 退出码 0", r8.returncode == 0, r8.stdout.strip()[-300:])
    must("dry-run 没建草稿目录", not dry.exists())
    must("dry-run 没改结果文件",
         (proj / "out" / "jianying-export.json").stat().st_mtime_ns == before)
    must("dry-run 打印了计划", "将写入草稿" in (r8.stdout or ""), (r8.stdout or "")[:200])

    print("⑨ 探测不到本机草稿根 → 保守指纹 + 明确标注")
    rep_ok = json.loads((proj / "out" / "jianying-export.json").read_text(encoding="utf-8"))
    must("探测结果写进报告", "probed" in rep_ok, str(list(rep_ok)))
    empty_root = td / "empty-root"
    empty_root.mkdir()
    fp, notes = EJ.probe_fingerprint(empty_root)
    must("空草稿根探不到指纹", fp == {}, str(fp))
    must("标注「未探测到本机版本，按保守值写入」",
         any("未探测到本机版本" in n for n in notes), str(notes))
    must("保守值不越线", EJ.CONSERVATIVE_FP["version"] <= EJ.MAX_VERSION,
         str(EJ.CONSERVATIVE_FP))

    print()
    if fails:
        print(f"❌ 未通过（现场 {td}）：" + "、".join(fails))
        return 1
    print(f"✅ 剪映草稿导出 9 组契约全部通过")
    import shutil
    shutil.rmtree(td, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
