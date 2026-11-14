#!/usr/bin/env python3
"""T-045 回归：`check_keyword_text` 的三条大字判据必须判红，且不许误杀合格卡。

病是「判据只给 MG 卡量脸」：`dianzan-c` 10.74s「四张图，介绍布局」的黄字实测
`y 585–679`，而那一帧（`素材/2026-10-16 144023.mov` 第 0.63s）有一张脸
`x 0.314–0.526 / y 0.279–0.398`——字正压在眼睛、鼻子、颧骨上；同一片 6.75s 的黄字压
在杂乱工地上，`verify_film` 照样绿（它只数花字色占比）。

判据只有一条路能证明自己有用：**给负样本，它红了**。这里造迷你项目，直接跑命令行：

  A 压脸（B-roll 格，字框在人脸上）      → ① error
  B 同一张卡拉到头顶上方（top_h）          → 全绿（先红后绿）
  C 卡上没字（扒不出墨迹）                → ② error（不是静默通过）
  D 亮背景（黄字压近白墙，ΔL < 12）       → ② error
  E 同一帧 + 隔离层（卡打开 isolate）      → 转绿（隔离层到底有没有用，能量出来）
  F 单排宽 95%W（fit_width 0.95）         → ③ error
  H 空格/间隔号/数字的文本（T-046）        → 字号按 em 宽反算，墨迹填到 0.62–0.78；
                                           包真把它压窄（fit_width 0.55）仍旧 error
  I behind 放不到脑后（T-046）              → 按**渲器自己宣称的落点**（`card_layout.kw_head_override`，
                                           平台安全区抬上来了就不再是 5.5%H）量，且把顶部渐变算进 ΔL；
                                           不合渐变时同一块地方必须 < 12（反证）
  G 真项目对一遍（有真片才跑）            → 0 error，且成片墨迹 bbox 与预测差 ≤ 12px

用法：python3 scripts/test_keyword_text.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE / "check_keyword_text.py"
W, H = 1080, 1920
# 真案几何（dianzan-c 10.74s 那张）：字框 0.30H 起，脸在这块地方
FACE = [0.368, 0.294, 0.194, 0.109]
TEXT = "四张图，介绍布局"
DUR = 1.0
from libs import find_project  # noqa: E402
REAL = find_project("dianzan-c") or Path("/nonexistent/dianzan-c")

BASE_PACK = """
name: {name}
label: fixture
canvas: {{width: 1080, height: 1920, fps: 30}}
emphasis:
  font: NotoSansSC-Black
  keyword_color: '#F6EB34'
  big_keyword_px: [133, 304]
  fit_width: {fit_w}
  top_h: 0.3
{isolate}
"""


def pack(root: Path, *, name="fixture", fit_w=0.70, isolate=None) -> Path:
    iso = ""
    if isolate:
        iso = "  isolate:\n" + "".join(f"    {k}: {v}\n" for k, v in isolate.items())
    p = root / f"{name}.yaml"
    p.write_text(BASE_PACK.format(name=name, fit_w=fit_w, isolate=iso))
    return p


def clip(root: Path, name: str, *, color: str) -> Path:
    """造一段纯色片段（真像素：判可读性必须能解出帧来）。"""
    d = root / "hf" / "clips"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={color}:s={W}x{H}:d={DUR}:r=30",
                        "-pix_fmt", "yuv420p", str(p)], capture_output=True, text=True)
    assert r.returncode == 0 and p.exists(), f"造片段失败：{r.stderr[-300:]}"
    return p


def project(root: Path, *, asset="素材/clipX.mov", text=TEXT, top_h=None,
            face=FACE, color="0x141210", kind="keyword", isolate=False) -> Path:
    """最小可判的项目：timeline（作者数据）+ cards.json（渲染器算的时长）+ 片段 + 人脸。"""
    sys.path.insert(0, str(HERE))
    import check_keyword_text as KT

    el = {"n": 1, "kind": "clip", "track": "video", "asset": asset, "medium": "broll",
          "in": 0.0, "out": DUR,
          "card": {"kind": kind, "text": text, "on": None}}
    (root / "out").mkdir(parents=True, exist_ok=True)
    if top_h is not None:
        el["card"]["top_h"] = top_h
    if isolate:
        el["card"]["isolate"] = True
    (root / "timeline.json").write_text(json.dumps(
        {"pack": "_tmp", "elements": [el]}, ensure_ascii=False))
    card = {"seg": 1, "mg": "mg-01", "kind": kind, "text": text, "start": 0.1,
            "end": round(0.1 + DUR - 0.2, 3)}
    if top_h is not None:
        card["top_h"] = top_h
    (root / "out" / "cards.json").write_text(json.dumps({"cards": [card]}, ensure_ascii=False))
    cp = clip(root, KT.clip_tag(el), color=color)
    fdir = root / "hf" / "work" / "faces"
    fdir.mkdir(parents=True, exist_ok=True)
    rows = [] if face is None else [
        {"t": round(i * 0.5, 3), "x": face[0], "y": face[1], "w": face[2], "h": face[3],
         "conf": 0.9} for i in range(int(DUR * 2) + 1)]
    (fdir / f"{cp.stem}.json").write_text(json.dumps(rows))   # 写晚于片段：缓存才算「新」
    return root


def run(proj: Path, pk: Path, *extra) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CLI), "--project", str(proj),
                        "--pack-file", str(pk), *extra],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def has(out: str, *needles) -> bool:
    return all(n in out for n in needles)


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="kwtext-"))
    pk = pack(base, name="p1", fit_w=0.70)

    # A 压脸：B-roll 格，字框（0.30H 起）落在人脸上
    pa = project(base / "A", face=FACE)
    rc, out = run(pa, pk)
    assert rc == 1 and has(out, "压脸", "mg-01"), f"A 压脸本该判红：\n{out}"

    # B 同一张卡拉到头顶上方 → 全绿（先红后绿；证明 A 的红是位置造成的）
    pb = project(base / "B", face=FACE, top_h=0.165)
    rc, out = run(pb, pk)
    assert rc == 0 and "全部通过" in out, f"B 挪开之后该全绿：\n{out}"

    # C 卡上没字：扒不出墨迹 → 仍红（不许静默放行）
    pc = project(base / "C", text="", face=None)
    rc, out = run(pc, pk)
    assert rc == 1 and has(out, "根本没上屏"), f"C 空字必须红（且要说清是没上屏）：\n{out}"

    # D 亮背景（近白墙）上的黄字：ΔL < 12 → 红
    pd = project(base / "D", color="0xEDE8DC", face=None)
    rc, out = run(pd, pk)
    assert rc == 1 and has(out, "可读性", "字被背景吃掉"), f"D 亮背景本该判红：\n{out}"

    # E 同一帧 + 隔离层（包里浓度给足）→ 转绿
    pe = project(base / "E", color="0xEDE8DC", face=None, isolate=True)
    pk_iso = pack(base, name="p1iso", fit_w=0.70,
                  isolate={"fill": "'rgba(18,15,12,.82)'", "feather_px": 56,
                           "pad_x": 0.05, "pad_y": 0.42, "radius_px": 26})
    rc, out = run(pe, pk_iso)
    assert rc == 0 and "含隔离层" in out, f"E 加了隔离层该转绿：\n{out}"

    # F 单排 95%W：越界 → 红
    pk_wide = pack(base, name="p1wide", fit_w=0.95)
    pf = project(base / "F", face=None)
    rc, out = run(pf, pk_wide)
    assert rc == 1 and has(out, "越界"), f"F 95%W 本该判红：\n{out}"

    # H 空格 / 间隔号 / 数字不是一个汉字宽（T-046）：分母按字数算的话，
    #   「3 个是国产」6 个字只给到 56.5%W、越 62% 下限——而画面上根本没有一个字是小的。
    #   字号改成按真字体 em 宽反算后它自己填到 69.9%W；下限一个数都没动：
    #   包真把字压窄（fit_width 0.55）时这条仍要判红。
    ph = project(base / "H", text="3 个是国产", face=None)
    rc, out = run(ph, pk)
    assert rc == 0 and has(out, "全部通过", "156px"), \
        f"H 空格文本该自己填到 70%W（字号按 em 反算）：\n{out}"
    pk_narrow = pack(base, name="p1narrow", fit_w=0.55)
    rc, out = run(ph, pk_narrow)
    assert rc == 1 and has(out, "越界"), f"H 下限没被放宽：真窄了必须仍判红：\n{out}"

    # I behind 卡到底被画在哪（T-046）：抠像放不到脑后时，渲器把字放到「头顶」
    #   （`card_layout.kw_head_override`：max(5.5%H, 平台安全区上沿, 头顶 − 9.5%H)）
    #   并自带一层顶部渐变。判据必须量在**那个位置**、并且把那层渐变合上去——
    #   拿原始画面量同一块地方，ΔL 只有个位数。
    #   落点**不许在这里写数字**：平台 UI 安全区把上沿从 5.5%H 抬到 10.6%H 的那一刀，
    #   测试里硬编码的 5.5%H 就过期了（这就是这条断言往红里掉的原因）。
    #   所以从渲器真源读，然后仍然强断言：判据量到的上沿 / 字框必须与渲器宣称的一致。
    #   副产物：这条也钉死「不许拿没上屏的中间态当判据」。
    pi = project(base / "I", text="售楼部 · 酒店", face=None, color="0xEDE8DC", kind="behind")
    rc, out = run(pi, pk)
    assert rc == 0, f"I behind 该全绿：\n{out}"
    _want_top, _measured, _box = _behind_landing(out)
    assert abs(_measured - _want_top) <= 0.0015, (
        f"I 判据量的位置 {_measured * 100:.2f}%H ≠ 渲器落点 {_want_top * 100:.2f}%H"
        f"（差 {abs(_measured - _want_top) * 100:.2f}%H）：\n{out}")
    assert has(out, "头顶大字", "自带顶部渐变"), \
        f"I behind 该说清「为什么长这样」（头顶大字 + 自带顶部渐变）：\n{out}"
    # 字框也得真的在那个高度上：墨迹上沿不得高于渲器落点（1px 容差）。
    assert _box[1] >= _want_top * H - 1, (
        f"I 字框墨迹上沿 {_box[1]}px 在渲器落点 {_want_top * H:.0f}px 之上：\n{out}")
    _raw_dl(pi, pk)          # 反证：不合渐变，同一块地方 ΔL < 12（红）

    # G 真项目：现在必须 0 error；成片帧的墨迹 bbox 与预测差 ≤ 12px
    if (REAL / "out" / "film-hf.mp4").exists() and (REAL / "out" / "cards.json").exists():
        rc, out = run_real(REAL)
        assert rc == 0, f"G 真项目 dianzan-c 该是 0 error：\n{out}"
        _red = [ln for ln in out.splitlines() if ln.startswith("✗")]
        assert not _red, "G 真项目还有 error：\n" + "\n".join(_red)
        bad = [ln for ln in out.splitlines() if "横向差" in ln and _dev(ln) > 12]
        assert not bad, "G 成片墨迹 bbox 与预测差超过 12px（字体模型漂了）：\n" + "\n".join(bad)
    else:
        print("  · 跳过 G（本机没有 dianzan-c 的成片）")

    shutil.rmtree(base, ignore_errors=True)
    print("✅ T-045 大字判据回归：A 压脸红 / B 挪开绿 / C 空字红 / D 亮底红 / "
          "E 加隔离绿 / F 越界红 / H 窄字文本填满且真窄仍红 / I behind 量在渲器落点且算渐变"
          + (" / G 真片对齐" if (REAL / "out" / "film-hf.mp4").exists() else ""))
    return 0


def _behind_landing(out: str) -> tuple[float, float, list[int]]:
    """渲器宣称的落点 / 判据量到的落点 / 判据算出的字框（画幅比例 + 像素）。

    渲器那一边读 `card_layout.kw_head_override({})`——`render_hf` 写 `kw_top_override`
    用的就是它（没有遮罩统计时 head 退回 0.15，判据侧的 `place_of` 同一条路）。
    判据这一边读它自己的输出行：`字框 [x, y, w, h] … 上沿 X%H·…`。
    两边差超过 1.5px 就是「判据在量一个渲器没画的地方」。
    """
    sys.path.insert(0, str(HERE))
    import card_layout as CL
    import re
    m = re.search(r"字框 \[(\d+), (\d+), (\d+), (\d+)\].*?上沿 ([\d.]+)%H", out)
    assert m, f"判据没吐字框与上沿：\n{out}"
    box = [int(m.group(i)) for i in range(1, 5)]
    measured = round(float(m.group(5)) / 100.0, 4)
    return CL.kw_head_override({}), measured, box


def _raw_dl(proj: Path, pk: Path) -> None:
    """反证用：同一个字框、**不合顶部渐变**的原始画面ΔL 必须 < 12。

    没有这条，I 那条绿说明不了「渐变是被算进去的」——也可能只是那个位置本身背景就暗。
    """
    sys.path.insert(0, str(HERE))
    import card_layout as CL
    import check_keyword_text as KT
    tl = json.loads((proj / "timeline.json").read_text())
    text = tl["elements"][0]["card"]["text"]
    cp = sorted((proj / "hf" / "clips").glob("*.mp4"))[0]
    fr = KT.frame_of(cp, 0.1, W, H, {})
    assert fr is not None
    import yaml
    style = yaml.safe_load(pk.read_text())
    mask, box = CL.ink_mask(text, style, W, H, CL.kw_head_override({}))
    r = KT.readable_dl(fr, mask, box["px"], 91.0)
    assert r["dl_p10"] is not None and r["dl_p10"] < CL.KW_DL_MIN, \
        f"反证失败：不合渐变时量到 ΔL {r['dl_p10']}，不是「被背景吃掉」的读数"


def run_real(proj: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CLI), "--project", str(proj), "--film"],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _dev(line: str) -> int:
    """从「横向差 +7px」里取绝对值。"""
    import re
    m = re.search(r"横向差\s*([+-]?\d+)px", line)
    return abs(int(m.group(1))) if m else 999


if __name__ == "__main__":
    raise SystemExit(main())
