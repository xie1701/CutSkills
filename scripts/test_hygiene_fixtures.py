#!/usr/bin/env python3
"""负样本夹具的**真音频**验证：判据在真声音上还能不能抓住。

`test_speech_hygiene.py` 用的是文字真值（秒级、无依赖），跑得快；
这个用 references/negative-samples/hygiene/ 里的真音频走一遍 ASR，慢（每段约 10 秒），
所以只在 `selftest --slow` 或手动跑。

它守的是另一件事：**判据不是只会认我自己写的例句**。ASR 把「唔系唔系」抄成什么样、
「o k o k」被切成一串单字母，这些只有真跑才知道。

用法：
  test_hygiene_fixtures.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import speech_hygiene as SH  # noqa: E402

FIX = ROOT / "references" / "negative-samples" / "hygiene"


def asr(wav: Path) -> list[tuple[str, float]]:
    p = subprocess.run(["coli", "asr", "-j", "--model", "sensevoice", "--language", "yue", str(wav)],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        return []
    r = json.loads(p.stdout)
    return [(t, float(s)) for t, s in zip(r.get("tokens") or [], r.get("timestamps") or [])]


def el_of(ws: list[tuple[str, float]], n: int = 1) -> dict:
    return {"n": n, "kind": "clip",
            "audio": {"asset": "x", "in": 0, "out": (ws[-1][1] + 0.2) if ws else 1.0}}


def main() -> int:
    man = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))
    names = man.get("expect_names") or {}
    bad = 0
    total = 0
    for s in man["samples"]:
        total += 1
        want = names.get(s["expect"]) or [s["expect"]]
        ws = asr(FIX / s["file"]) if s.get("file") else []
        if s.get("files"):
            # 接缝：两段各自成元素，交给 cross()
            els, allw = [], {}
            for k, f in enumerate(s["files"]):
                w = asr(FIX / f)
                els.append(el_of(w, k + 1))
                allw[k + 1] = w
            got = [m for lv, m in SH.cross(els, allw)]
        else:
            got = [m for lv, m in SH.per_element(el_of(ws), ws)]
        hit = any(k in g for g in got for k in want)
        print(f"  {'✓' if hit else '✗'} {s.get('file') or '+'.join(s['files'])}"
              f"（期望「{s['expect']}」）")
        if not hit:
            for g in got:
                print(f"      实得：{g}")
            bad += 1
    print(f"\n夹具（真音频）：{total - bad}/{total} 过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
