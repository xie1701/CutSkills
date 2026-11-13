#!/usr/bin/env python3
"""CutSkills · T-015 口播「声 / 画双评级」与露脸门禁 · 合同测试。

为什么要有它：A2 的核心是「声音好的镜头，画面穿帮也不能放到脸上」。
这条判据最容易被写成「永远绿的假闸门」——L2 记录改了字段名、门禁读的目录对不上、
或者 gate 根本没接进 check_timeline，都会有「跑完 0 error」的漂亮报告。
所以这里每一组样本都走两条路：

    A 改动前的判据全集 = `check_timeline.check()`（旧规则，门禁挂在 `main()` 管线末段，
      所以 check() 里根本没有这条规则）→ 必须**不拦**；
    B 改动后 = `visual_grade.gate()` 与真跑 `check_timeline.py --project` → 必须**拦**。

负样本（必红）：
  1. L2 记录 `video_grade: audio_only`（手持台本、视线看场外）挂 `medium: talking`；
  2. 同一支素材同一窗口改成 `medium: broll`（画面换掉、声音走 audio 轨）→ 必须放行；
  3. 纸面剪辑在元素 `props.video_grade` 上手写 `audio_only`/`ng`；
  4. 窗口只擦到穿帮段边界 0.1s（L2 框选误差量级）→ warning，不判死；
  5. 声音 `audio_grade: ng` → warning（声音不能用的 take 不因为画面好看就上）。
正样本（必绿）：
  6. `usable` / `usable`；
  7. 两个字段都没有（旧库没评过）→ 一条都不报，不许把「未知」当穿帮。
旁白用途：
  8. `pick_take --for talking` 排除画面穿帮的 take，`--for vo` 照样返回；不带参数老行为不变。

    python3 scripts/test_visual_grade.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import visual_grade as VG                                  # noqa: E402
from check_timeline import check as check_timeline_fn      # noqa: E402

ASSET = "口播/take1.mov"
TEXT = "我哋啲茶全部系纯手工做嘅"
TAKE_IN, TAKE_OUT = 10.0, 20.0
# pick_take 给每个候选取 phrase 起止 ±0.05/0.20 的呼吸位，跟真实现状一致
CAND_IN, CAND_OUT = 9.95, 20.20

FAILS: list[str] = []


def h12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def build_lib(base: Path, l2: dict | None) -> Path:
    """造一个最小素材库：索引 + 词级 ASR 缓存 + 一条 L2 审片记录。"""
    lib = base / "lib"
    sysdir = lib / "_cutskills"
    (sysdir / "l2").mkdir(parents=True)
    (sysdir / "projects" / "t15").mkdir(parents=True)
    (sysdir / "cache" / "asr").mkdir(parents=True)

    asset = {"id": ASSET, "kind": "video", "duration": 30.0, "aliases": [],
             "transcript": {"lang": "yue",
                            "phrases": [{"start": TAKE_IN, "end": TAKE_OUT, "text": TEXT}]}}
    (sysdir / "index.json").write_text(json.dumps(
        {"version": 2, "library": str(lib), "assets": [asset]}, ensure_ascii=False))

    # 词级时间戳：pick_take 靠它算有声覆盖率，缺了它就一条候选都没有
    toks = [c for c in TEXT if c.strip()]
    step = (TAKE_OUT - TAKE_IN) / max(1, len(toks))
    (sysdir / "cache" / "asr" / f"{h12(ASSET)}-v3c0.json").write_text(json.dumps(
        {"tokens": toks, "timestamps": [round(TAKE_IN + step * k, 3) for k in range(len(toks))]},
        ensure_ascii=False))

    if l2:
        rec = {"asset": ASSET, "in": TAKE_IN, "out": TAKE_OUT, "shot": "medium",
               "people": ["speaker"], "grade": "must-use", "confidence": 0.8, **l2}
        (sysdir / "l2" / f"{h12(ASSET)}-{int(TAKE_IN)}.json").write_text(
            json.dumps(rec, ensure_ascii=False))
    return lib


def element(medium: str, i: float = TAKE_IN, o: float = TAKE_OUT,
            props: dict | None = None, n: int = 1) -> dict:
    e = {"n": n, "kind": "clip", "track": "video", "asset": ASSET, "in": i, "out": o,
         "medium": medium, "source": "library", "beat": 1, "role": "warrant",
         "why": "这一段说清茶点为什么只能手工做", "evidence": TEXT}
    # 口播和 B-roll 留声都必须有声画两轨：少一轨 check_timeline 会报别的红，混淆结论
    e["audio"] = {"asset": ASSET, "in": i, "out": o}
    if props:
        e["props"] = props
    return e


def timeline(els: list[dict]) -> dict:
    return {"version": 4, "elements": els,
            "duration_s": round(sum(e["out"] - e["in"] for e in els), 3)}


def pre_change(tl: dict) -> list[tuple[str, str]]:
    """改动前的判据全集。门禁挂在 check_timeline.main() 的末段，所以 check() 就是旧规则。"""
    return check_timeline_fn(tl, {ASSET: {"id": ASSET, "duration": 30.0}})


def run_cli(lib: Path, tl: dict) -> tuple[int, str]:
    proj = lib / "_cutskills" / "projects" / "t15"
    (proj / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1))
    r = subprocess.run([sys.executable, str(HERE / "check_timeline.py"), "--project", str(proj)],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def run_pick(lib: Path, purpose: str | None, as_json: bool = True) -> tuple[int, str]:
    cmd = [sys.executable, str(HERE / "pick_take.py"), "--library", str(lib),
           "--about", "纯手工"]
    if purpose:
        cmd += ["--for", purpose]
    if as_json:
        cmd.append("--json")
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def case(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


BAD_L2 = {"video_grade": "audio_only", "audio_grade": "usable",
          "why": "手持 A4 台本纸露脸，视线一直看场外",
          "flags": ["手持台本", "视线飘画外"]}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vg_t015_") as td:
        base = Path(td)

        print("① 负样本：素材判了 audio_only（拿稿露脸），挂 talking")
        lib = build_lib(base / "neg", BAD_L2)
        tl = timeline([element("talking")])
        old = pre_change(tl)
        case("改动前不拦（旧判据全集里没有这条）",
             not any("穿帮" in m for _, m in old) and not [m for lv, m in old if lv == "error"],
             f"旧规则 {len(old)} 条，0 条提穿帮")
        hits = VG.gate(tl["elements"], lib)
        errs = [m for lv, m in hits if lv == "error"]
        case("改动后 gate 判 error", len(errs) == 1, errs[0] if errs else "没报")
        case("报错说清了原因与出路",
             bool(errs) and "该口播段画面穿帮" in errs[0] and "B-roll 留声" in errs[0]
             and "手持 A4 台本纸" in errs[0])
        code, out = run_cli(lib, tl)
        case("CLI 端到端判红（exit 1）", code == 1 and "穿帮" in out,
             out.strip().splitlines()[0] if out.strip() else "无输出")

        print("② 负样本的正解：同一窗口改 broll 留声 → 必须放行")
        tl_b = timeline([element("broll")])
        case("gate 放行", not VG.gate(tl_b["elements"], lib))
        code_b, out_b = run_cli(lib, tl_b)
        case("CLI 放行（exit 0）", code_b == 0, out_b.strip().splitlines()[-1] if out_b.strip() else "")
        case("旧判据同样放行（说明差别只在新门禁）", not [m for lv, m in pre_change(tl_b) if lv == "error"])

        print("③ 纸面剪辑手写评级（props 覆盖 L2）")
        p_bad = VG.gate([element("talking", props={"video_grade": "audio_only"})], None)
        case("props.video_grade=audio_only 判 error",
             [m for lv, m in p_bad if lv == "error"] != [])
        p_ng = VG.gate([element("talking", props={"video_grade": "ng"})], None)
        case("props.video_grade=ng 判 error", [m for lv, m in p_ng if lv == "error"] != [])
        p_ok = VG.gate([element("talking", props={"video_grade": "usable"})], lib)
        case("props 手写 usable 覆盖 L2 的 audio_only（人不该被机器评级锁死）", not p_ok)

        print("④ 窗口只擦到穿帮段边界（L2 框选误差量级）")
        edge = VG.gate([element("talking", i=19.9, o=28.0)], lib)
        case("贴边 0.10s 只报 warning",
             [lv for lv, _ in edge] == ["warning"] and "只压到边界" in edge[0][1],
             edge[0][1] if edge else "没报")
        real = VG.gate([element("talking", i=18.0, o=28.0)], lib)
        case("真压进去 2.0s 判 error", [lv for lv, _ in real] == ["error"])

        print("⑤ 声音评级 ng（画面再好也不该上）")
        lib_a = build_lib(base / "audio_ng",
                          {"audio_grade": "ng", "video_grade": "usable",
                           "why": "空调噪声压过台词，救不回来"})
        a_hits = VG.gate([element("talking")], lib_a)
        case("audio_grade=ng 报 warning", [lv for lv, _ in a_hits] == ["warning"],
             a_hits[0][1] if a_hits else "没报")
        case("声音 ng 不是 error（画面本身没问题，换 take 即可）",
             not [m for lv, m in a_hits if lv == "error"])

        print("⑥ 正样本：usable / 未知 → 一条都不报")
        lib_ok = build_lib(base / "ok", {"video_grade": "usable", "audio_grade": "usable"})
        case("usable 放行", not VG.gate([element("talking")], lib_ok))
        code_ok, out_ok = run_cli(lib_ok, timeline([element("talking")]))
        case("CLI 全绿（exit 0）", code_ok == 0, out_ok.strip().splitlines()[-1] if out_ok.strip() else "")
        lib_raw = build_lib(base / "unknown", None)
        case("两个字段都没有时不报（旧项目红黄不许暴涨）",
             not VG.gate([element("talking")], lib_raw))
        g = VG.grade_of(lib_raw, ASSET, TAKE_IN, TAKE_OUT)
        case("grade_of 把「没评过」如实返回 None",
             g["video_grade"] is None and g["audio_grade"] is None and g["source"] is None)
        typo = VG.gate([element("talking", props={"video_grade": "half"})], None)
        case("不认识的评级报 warning",
             [lv for lv, _ in typo] == ["warning"] and "不认识" in typo[0][1],
             typo[0][1] if typo else "没报")

        print("⑦ pick_take --for：露脸挑画面，旁白只挑声")
        code_def, out_def = run_pick(lib, None)
        case("不带参数据旧行为返回该 take（0 条也行，但不能崩）", code_def == 0,
             out_def.strip()[:80])
        code_t, out_t = run_pick(lib, "talking", as_json=False)
        case("--for talking 排除画面穿帮的 take", code_t == 1 and "没有能露脸的 take" in out_t,
             out_t.strip().splitlines()[0] if out_t.strip() else "")
        code_tj, out_tj = run_pick(lib, "talking")     # --json 是机器接口：空列表照旧 exit 0
        case("--for talking --json 返回空列表", code_tj == 0 and out_tj.strip() == "[]",
             out_tj.strip()[:40])
        code_v, out_v = run_pick(lib, "vo")
        case("--for vo 照样取这段声音", code_v == 0 and ASSET in out_v)
        code_ok_t, out_ok_t = run_pick(lib_ok, "talking")
        case("画面 usable 的库 --for talking 仍可选", code_ok_t == 0 and ASSET in out_ok_t)

    print()
    if FAILS:
        print(f"❌ 声/画双评级门禁：{len(FAILS)} 组没过：{'；'.join(FAILS)}")
        return 1
    print("✅ 声/画双评级门禁：正负样本全部按预期（audio_only 挂 talking 必红，broll 留声放行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
