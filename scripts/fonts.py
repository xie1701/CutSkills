#!/usr/bin/env python3
"""字体表：别名 → 文件，以及**字号反算**。

字号不能写死在一个包里。同一句 10 个汉字，得意黑比思源窄 22%（678px vs 847px @77px），
照抄一个 77px 给两只字体，得意黑出来的字幕会小一整圈。所以字号由「目标宽度 ÷ 宽度比」反算。

宽度比 = 该字体 1 个汉字宽 ÷ 字号。等宽字体（思源黑/宋）是 1.0，得意黑是 0.881。

用法：
  fonts.py --list
  fonts.py --size 得意黑 --width 1080
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))
import yaml  # noqa: E402

TABLE = ROOT / "styles" / "fonts.yaml"


def load() -> dict:
    if not TABLE.exists():
        return {"target_unit_w": 0.074, "fonts": {}, "default": None}
    return yaml.safe_load(TABLE.read_text(encoding="utf-8")) or {}


def resolve(name: str | None) -> dict | None:
    """别名或文件名 → {name, file, ratio, role}。"""
    if not name:
        return None
    t = load()
    fonts = t.get("fonts") or {}
    if name in fonts:
        return {"name": name, **fonts[name]}
    for k, v in fonts.items():
        if v.get("file") == name:
            return {"name": k, **v}
    return None


def default() -> str | None:
    t = load()
    return t.get("default")


def file_of(name: str | None) -> str | None:
    r = resolve(name)
    return r.get("file") if r else None


def size_px(name: str | None, width: int, units: float = 10.0,
            fallback: int = 72) -> int:
    """让 `units` 个展示单位撑满 width × target_unit_w。"""
    r = resolve(name)
    if not r:
        return fallback
    t = load()
    target = float(t.get("target_unit_w") or 0.074) * float(width)
    ratio = float(r.get("ratio") or 1.0)
    return max(20, int(round(target / max(0.10, ratio))))


def main() -> int:
    ap = argparse.ArgumentParser(description="字体表")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--size", default=None)
    ap.add_argument("--width", type=int, default=1080)
    a = ap.parse_args()
    if a.list:
        t = load()
        print(f"默认：{t.get('default')}　目标单位宽：{t.get('target_unit_w')}")
        for k, v in (t.get("fonts") or {}).items():
            print(f"  {k:<8} {v['file']:<30} ratio {v['ratio']:<6} "
                  f"10 单位 = {size_px(k, a.width)}px")
        return 0
    if a.size:
        print(size_px(a.size, a.width))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
