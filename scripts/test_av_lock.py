#!/usr/bin/env python3
"""口播段音画同源：画面裁切点必须和声音裁切点逐帧一致。

2026-11-12 的真事故：改时间轴时长时顺手把画面起点写成了 0，
声音还在第 90 秒、脸回到第 0 秒——嘴形全错，而素材开头正是场记口令和重来。
这条判据锁死它，谁再改坏就直接报红。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check_timeline.py"


def run(tl: dict) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "timeline.json"
        p.write_text(json.dumps(tl, ensure_ascii=False, indent=2))
        r = subprocess.run([sys.executable, str(CHECK), "--project", td],
                           capture_output=True, text=True)
        return (r.stdout or "") + (r.stderr or "")


def el(n, asset, vin, vout, ain, aout, aasset=None):
    return {"n": n, "kind": "clip", "track": "video", "asset": asset, "in": vin, "out": vout,
            "medium": "talking", "evidence": "x",
            "audio": {"asset": aasset or asset, "in": ain, "out": aout}}


CASES = [
    # (名字, 元素, 期望命中, 期望是 error)
    ("口播对得上", el(1, "a.mov", 2.64, 8.10, 2.64, 8.10), "", False),
    ("口播画面起点被清零（原案）", el(1, "a.mov", 0.0, 8.10, 2.64, 8.10), "音画不同源", True),
    ("口播画面整体平移 3 秒", el(1, "a.mov", 5.64, 11.10, 2.64, 8.10), "音画不同源", True),
    ("B-roll 跨素材不算（画面本来就不是这段话）",
     el(1, "b.mov", 0.0, 5.46, 2.64, 8.10, aasset="a.mov"), "", False),
    ("B-roll 画面比声音短（后半段画面缺失）",
     el(1, "b.mov", 0.0, 4.80, 2.64, 8.10, aasset="a.mov"), "声画时长", True),
    ("B-roll 时长误差 0.04s 内（帧取整的正常范围）",
     el(1, "b.mov", 0.0, 5.44, 2.64, 8.10, aasset="a.mov"), "", False),
]


def main() -> int:
    bad = 0
    for name, e, want, want_err in CASES:
        out = run({"title": "av", "elements": [e], "duration_s": round(e["out"] - e["in"], 3)})
        hit = want in out if want else not any(k in out for k in ("音画不同源", "声画时长"))
        got_err = ("音画不同源" in out) or ("声画时长" in out)
        ok = hit and got_err == want_err
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            print("      " + out.strip().replace("\n", "\n      "))
        bad += 0 if ok else 1
    print(f"\n口播音画同源：{len(CASES) - bad}/{len(CASES)} 过")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
