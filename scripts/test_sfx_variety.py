#!/usr/bin/env python3
"""离线自足测试：事件级音效（props.events）+ 同音色疲劳轮换（AI 视听方案 2026-11-14 P2）。

不渲整片、不需要真 ffmpeg：CC0 音效库指到临时造的 fixture catalog，
时间映射与轮换走 render_hf 里抽出的纯函数（gen_props_sfx / rotate_sfx）。

    python3 scripts/test_sfx_variety.py   # 退出码 0 = 全绿
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SKILL = Path(__file__).resolve().parent.parent

# cc0_lib 在 import 时读环境变量定库路径——先指到 fixture 再 import
_tmp = tempfile.mkdtemp(prefix="sfx-fixture-")
FIXTURE = Path(_tmp)
(FIXTURE / "LICENSE-CC0.txt").write_text("fixture", encoding="utf-8")
import os  # noqa: E402
os.environ["CUTSKILLS_SFX_LIB"] = str(FIXTURE)
os.environ.setdefault("CUTSKILLS_BGM_LIB", str(FIXTURE))  # 测试不碰 BGM

import cc0_lib  # noqa: E402
cc0_lib.SFX_LIB = FIXTURE          # 双保险：环境变量 + 模块属性都指向 fixture
cc0_lib._cache.clear()

# fixture：三个 family，catalog 顺序即轮换顺序；appear 家族只有一条（轮换应循环回自身）
CATALOG = """id,name,category,file,duration_s,use_zh,under_voiceover_db,sync_at_s
pop-medium-in,Pop medium,transitions,sounds/pop-medium-in.wav,0.30,中档泡泡,-6,0.05
pop-soft-in,Pop soft,transitions,sounds/pop-soft-in.wav,0.28,轻泡泡,-2,0.04
pop-hard-in,Pop hard,transitions,sounds/pop-hard-in.wav,0.35,重泡泡,-4,0.06
whoosh-medium-soft,Whoosh,transitions,sounds/whoosh-medium-soft.wav,0.55,转场呼啸,-3,0.20
whoosh-fast-hard,Whoosh fast,transitions,sounds/whoosh-fast-hard.wav,0.25,快呼啸,-4,0.09
appear-materialise,Appear,transitions,sounds/appear-materialise.wav,0.40,素材浮现,-5,0.10
"""
(FIXTURE / "catalog.csv").write_text(CATALOG, encoding="utf-8")

import render_hf  # noqa: E402

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


# ---------------------------------------------------------------- 1) pick_sfx
print("[1] pick_sfx：nth=0 与旧行为一致 + 轮换确定性")
a0 = cc0_lib.pick_sfx("whoosh")
b0 = cc0_lib.pick_sfx("whoosh")
check("nth=0 精确命中 SFX_MAP 目标", a0 and a0["id"] == "whoosh-medium-soft",
      str(a0))
check("同 (name,nth) 两次调用同结果（nth=0）", a0 == b0)
check("nth=1 取 family 下一条", cc0_lib.pick_sfx("whoosh", 1)["id"] == "whoosh-fast-hard",
      str(cc0_lib.pick_sfx("whoosh", 1)))
check("nth 超出 family 长度按取模循环", cc0_lib.pick_sfx("pop", 3)["id"] == "pop-medium-in",
      str(cc0_lib.pick_sfx("pop", 3)))
d1 = cc0_lib.pick_sfx("pop", 2)
d2 = cc0_lib.pick_sfx("pop", 2)
check("同 (name,nth) 两次调用同结果（nth=2）", d1 == d2 and d1["id"] == "pop-hard-in")
check("负数 nth 退回旧路径（精确匹配）",
      cc0_lib.pick_sfx("whoosh", -1)["id"] == "whoosh-medium-soft")
check("单条 family 轮换循环回自身",
      cc0_lib.pick_sfx("gen", 5)["id"] == "appear-materialise")

# ------------------------------------------------------- 2) rotate_sfx 规则
print("[2] 轮换规则：连续同名递增，隔了别的音效重置")
e3 = [{"t": i, "name": "pop"} for i in range(3)]
render_hf.rotate_sfx(e3)
ids = [cc0_lib.pick_sfx(e["name"], e["nth"])["id"] for e in e3]
check("连续 3 次同名 → nth 0,1,2", [e["nth"] for e in e3] == [0, 1, 2],
      str([e["nth"] for e in e3]))
check("连续 3 次同名 → 3 个不同文件", len(set(ids)) == 3, str(ids))
mixed = [{"t": 0.0, "name": "pop"}, {"t": 1.0, "name": "pop"},
         {"t": 2.0, "name": "whoosh"}, {"t": 3.0, "name": "pop"}]
render_hf.rotate_sfx(mixed)
check("隔了别的音效 → 重置 nth=0", [e["nth"] for e in mixed] == [0, 1, 0, 0],
      str([e["nth"] for e in mixed]))
one = [{"t": 0.0, "name": "whoosh"}]
render_hf.rotate_sfx(one)
check("同名连续 ≤1 次 → nth=0（与现状一致）", one[0]["nth"] == 0)

# ------------------------------------------- 3) gen_props_sfx 时间轴映射
print("[3] props.events：overlay at 分支 / video _start 分支")
ov = {"kind": "gen", "track": "overlay", "at": 12.5, "n": 3,
      "props": {"events": [{"t": 1.0, "name": "pop"},
                           {"t": 2.5, "name": "behind"}]}}
evs = render_hf.gen_props_sfx(ov)
check("overlay：t_global = at + t", [e["t"] for e in evs] == [13.5, 15.0],
      str([e["t"] for e in evs]))
check("gain 缺省：CARD_SFX 有档用档，没有用 -6",
      [e["gain_db"] for e in evs] == [-6.0, render_hf.CARD_SFX["behind"][1]],
      str([e["gain_db"] for e in evs]))
check("for 字段写 ev-{name}", all(e["for"] == f"ev-{e['name']}" for e in evs))
vid = {"kind": "gen", "_start": 30.0, "n": 7,
       "props": {"events": [{"t": 0.5, "name": "keyword", "gain_db": -3.0}]}}
vevs = render_hf.gen_props_sfx(vid)
check("video：t_global = _start + t", len(vevs) == 1 and vevs[0]["t"] == 30.5,
      str(vevs))
check("显式 gain_db 优先", vevs[0]["gain_db"] == -3.0)

# ------------------------------------------------------- 4) 防呆不炸渲
print("[4] props.events 防呆：坏类型警告并跳过")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    bad = render_hf.gen_props_sfx({"kind": "gen", "at": 5.0, "track": "overlay",
                                   "props": {"events": "不是列表"}})
out = err.getvalue()
check("props.events 非 list → 返回空且有警告", bad == [] and "不是 list" in out, out)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    partial = render_hf.gen_props_sfx(
        {"kind": "gen", "at": 5.0, "track": "overlay",
         "props": {"events": [{"t": 0, "name": ""},
                              {"t": -1.0, "name": "pop"},
                              {"t": "x", "name": "pop"},
                              "裸字符串",
                              {"t": 0.2, "name": "pop"}]}})
out = err.getvalue()
check("坏条目全部跳过，只留合法那条",
      len(partial) == 1 and partial[0]["t"] == 5.2 and partial[0]["name"] == "pop",
      f"out={out} evs={partial}")
check("每种坏数据都有警告",
      out.count("⚠") >= 4 and "t<0" in out and "没写 name" in out and "不是数字" in out,
      out)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    nonev = render_hf.gen_props_sfx({"kind": "gen", "at": 5.0, "track": "overlay"})
check("没写 props.events → 安静返回空", nonev == [] and err.getvalue() == "")

# ------------------------------------- 5) 排序+轮换+manifest 留痕（端到端纯本地）
print("[5] 排序后轮换 + manifest 按实际文件留痕")
events = ([{"t": 10.0, "name": "whoosh", "gain_db": -1.0, "for": "gen-scene-1"},
           {"t": 3.0, "name": "pop", "gain_db": -6.0, "for": "ev-pop"},
           {"t": 3.4, "name": "pop", "gain_db": -6.0, "for": "ev-pop"},
           {"t": 8.0, "name": "whoosh", "gain_db": -1.0, "for": "gen-overlay-2"}]
          + [{"t": 20.0 + i * 0.4, "name": "pop", "gain_db": -6.0} for i in range(3)])
events = [e for e in events if 0 <= e["t"] <= 30]
events.sort(key=lambda e: float(e["t"]))
render_hf.rotate_sfx(events)
check("乱序输入排序后按时间轴轮换",
      # pop,pop 连续→0,1；两个 whoosh 排序后相邻→0,1；结尾三个 pop→0,1,2
      [e["nth"] for e in events] == [0, 1, 0, 1, 0, 1, 2],
      str([(e["t"], e["name"], e["nth"]) for e in events]))
proj = Path(tempfile.mkdtemp(prefix="sfx-manifest-"))
mf = render_hf.write_sfx_manifest(proj, events)
rows = json.loads(mf.read_text(encoding="utf-8"))["sfx"]
pop_rows = [r for r in rows if r["name"] == "pop"]
whoosh_rows = [r for r in rows if r["name"] == "whoosh"]
check("manifest：name 保留原始事件名", all(r["name"] == "pop" for r in pop_rows))
check("manifest：轮换出的 3 个 pop 变体都进清单",
      {r["id"] for r in pop_rows} == {"pop-medium-in", "pop-soft-in", "pop-hard-in"},
      str(pop_rows))
check("manifest：whoosh 两个变体都进清单",
      {r["id"] for r in whoosh_rows} == {"whoosh-medium-soft", "whoosh-fast-hard"},
      str(whoosh_rows))
check("manifest：总行数 = 实际用到的不重复文件数",
      len(rows) == len({(r["name"], r["id"]) for r in rows}), str(len(rows)))

print()
if FAILS:
    print(f"✗ {len(FAILS)} 项失败：{FAILS}")
    raise SystemExit(1)
print("✓ 全部通过")
