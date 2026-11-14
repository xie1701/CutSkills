#!/usr/bin/env python3
"""字体表：别名 → 文件，以及**字号反算**。

字号不能写死在一个包里。同一句 10 个汉字，得意黑比思源窄 22%（678px vs 847px @77px），
照抄一个 77px 给两只字体，得意黑出来的字幕会小一整圈。所以字号由「目标宽度 ÷ 宽度比」反算。

宽度比 = 该字体 1 个汉字宽 ÷ 字号。等宽字体（思源黑/宋）是 1.0，得意黑是 0.881。

授权：字体表和授权表是同一份文件（`styles/fonts.yaml` 的 license / license_evidence）。
`licence_of()` 给授权总表（`licensing.py`）用：表里查不到 → 一律返回 `unverified`，
绝不用「应该是开源的」蒙过去。`--verify` 再往下一层：直接读字体文件的 name 表
ID 13/14（字体自带的许可描述与许可地址），跟表里写的对账，不一致就报 mismatch。

用法：
  fonts.py --list
  fonts.py --size 得意黑 --width 1080
  fonts.py --licence 得意黑          # 授权口径（JSON）
  fonts.py --verify 思源黑体         # 表里记录 × 字体文件 name 表对账
"""
from __future__ import annotations

import argparse
import json
import struct
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


# ------------------------------------------------------------------ 授权

# 字体文件在哪些目录里找。跟 render_hf.find_font 同一批目录：
# 字体文件不进仓（各自有各自的分发条件），所以「文件在不在本机」本身也是核验的一部分。
FONT_DIRS = (Path.home() / "Library/Fonts", Path("/Library/Fonts"),
             Path("/System/Library/Fonts"))

# name 表里跟授权有关的字段（OpenType 规范 1.7，name ID 语义）
NAME_FIELDS = {0: "copyright", 1: "family", 2: "style", 4: "full_name",
               5: "version", 11: "homepage", 13: "license", 14: "license_url"}

# 许可名 → name 表 ID 13 里该出现的特征串。只有这里列出的家族才做字符串比对；
# 不在表里的许可（自撰条款、商业授权函）只能人读，代码不替它点头。
VERIFY_RULES = (("open font license", "open font license"),
                ("apache", "apache license"),
                ("mit license", "mit license"),
                ("cc0", "cc0"))

UNVERIFIED = "unverified"


def file_path(name: str | None) -> Path | None:
    """别名 / 文件名 / 绝对路径 → 真实字体文件；找不到返回 None（找不到 ≠ 没授权）。"""
    if not name:
        return None
    p = Path(name).expanduser()
    if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.exists():
        return p
    r = resolve(name)
    want = (r or {}).get("file") or name
    for d in FONT_DIRS:
        if not d.exists():
            continue
        cands = sorted(d.glob(f"{want}.*")) or sorted(
            p2 for p2 in d.glob(f"*{want}*") if p2.suffix.lower() in (".ttf", ".otf"))
        if cands:
            return cands[0]
    return None


def read_name_table(path: str | Path) -> dict | None:
    """读字体自带 name 表里跟授权有关的几条（纯 stdlib，不引第三方字体库）。

    读不出（文件不在、格式不认识、表被裁掉）一律返回 None——**读不出不等于没授权**，
    调用方据此报「无法核验」，不是报「清白」。
    """
    p = Path(path)
    try:
        b = p.read_bytes()
    except OSError:
        return None
    if b[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"):
        return None
    base = 0
    if b[:4] == b"ttcf":                     # 字体集合：取第一只
        if len(b) < 16:
            return None
        base = struct.unpack(">I", b[12:16])[0]
    try:
        num = struct.unpack(">H", b[base + 4:base + 6])[0]
        name_off = name_len = None
        for i in range(num):
            off = base + 12 + i * 16
            tag = b[off:off + 4]
            if tag == b"name":
                _, name_off, name_len = struct.unpack(">III", b[off + 4:off + 16])
                break
        if name_off is None or not name_len:
            return None
        nt = b[name_off:name_off + name_len]
        count, str_off = struct.unpack(">HH", nt[2:6])
        out: dict = {}
        for i in range(count):
            pid, _eid, _lid, nid, ln, off2 = struct.unpack(">HHHHHH", nt[6 + i * 12:18 + i * 12])
            if nid not in NAME_FIELDS or nid in out:
                continue
            raw = nt[str_off + off2:str_off + off2 + ln]
            out[nid] = (raw.decode("utf-16-be", "replace") if pid in (0, 3)
                        else raw.decode("latin-1", "replace"))
        return {NAME_FIELDS[k]: v.strip() for k, v in out.items()}
    except (struct.error, IndexError, ValueError):
        return None


def _evidence_matches(license_str: str, said: str) -> bool | None:
    """表里写的许可，在字体文件自带的许可描述里能不能找到 → True/False；不可判 → None。"""
    lic, txt = (license_str or "").lower(), (said or "").lower()
    if not lic or not txt:
        return None
    for needle, want in VERIFY_RULES:
        if needle in lic:
            return want in txt
    return None


def _table_label() -> str | None:
    """字体表的位置说明：仓内给相对路径，别处（测试用的临时表）只给文件名。"""
    if not TABLE.exists():
        return None
    try:
        return TABLE.relative_to(ROOT).as_posix()
    except ValueError:
        return TABLE.name


def entry_of(name: str | None) -> tuple[str | None, dict | None, str | None]:
    """别名 / 文件名 / 同族变体名（如 NotoSansSC-SemiBold）→ (条目名, 条目, 匹配方式)。

    同族变体靠 yaml 里的 `file_prefixes` 认：字体授权是按**族**给的（OFL 约束的是整只
    字库），所以 NotoSansSC-SemiBold 跟 NotoSansSC-Black 是同一份许可。
    注意：只给授权用，不接管 `resolve()`——字号反算仍按逐字体真实宽度比，不能拿族的比例顶替。
    """
    if not name:
        return None, None, None
    r = resolve(name)
    if r:
        return r.get("name"), r, "alias" if r.get("name") == name else "file"
    low = name.lower()
    for alias, v in (load().get("fonts") or {}).items():
        for pref in (v.get("file_prefixes") or []):
            if low == str(pref).lower() or low.startswith(str(pref).lower() + "-"):
                return alias, {"name": alias, **v}, "family_prefix"
    return None, None, None


def licence_of(name: str | None) -> dict:
    """字体授权口径。表里没有 / 没填 license → `unverified` + 提示人工确认（绝不猜）。

    返回的 `status`：
      ok          表里有 license，且（如能读到文件）文件 name 表对得上
      partial     表里有 license，但本机读不到字体文件，文件级核验没做
      unverified  查不到许可证据，或文件里的许可与表里写的冲突
    """
    _alias, r, how = entry_of(name)
    if not r:
        return {"name": name, "family": None, "file": None, "role": [],
                "license": UNVERIFIED, "license_evidence": None, "copyright": None,
                "homepage": None, "evidence_source": "styles/fonts.yaml 无此条目",
                "file_found": False, "verified_in_file": None, "status": UNVERIFIED,
                "note": "字体表里没有这只字体，也没有随仓许可证据——商用前必须人工确认授权"}
    lic = str(r.get("license") or "").strip()
    out = {"name": r.get("name"), "family": r.get("family"),
           "file": r.get("file"), "role": list(r.get("role") or []),
           "matched_by": how,
           "license": lic or UNVERIFIED,
           "license_evidence": r.get("license_evidence"),
           "copyright": r.get("copyright"), "homepage": r.get("homepage"),
           "evidence_source": _table_label(),
           "file_found": False, "verified_in_file": None, "status": UNVERIFIED,
           "note": ""}
    if not lic:
        out["note"] = ("字体表里没有 license 字段——按未核验处理，商用前必须人工确认"
                       "（查到官方许可页再补进 styles/fonts.yaml）")
        return out
    p = file_path(r.get("file"))
    out["file_found"] = bool(p)
    if not p:
        out["status"] = "partial"
        out["note"] = ("本机找不到字体文件，文件级核验未做：表里的许可是记录值，"
                       "换机器出片前请在装有该字体的机器上跑 fonts.py --verify")
        return out
    nt = read_name_table(p)
    said = (nt or {}).get("license") or ""
    hit = _evidence_matches(lic, said) if nt else None
    out["verified_in_file"] = hit
    if hit is False:
        out["status"] = UNVERIFIED
        out["note"] = (f"字体文件自带的许可描述与字体表记录冲突：文件说「{said[:80]}」，"
                       f"表里写「{lic}」——先查清再商用")
    elif hit is None:
        out["status"] = "partial"
        out["note"] = ("字体文件读不到 name 表里的许可描述（或该许可不在自动比对名单里），"
                       "需要人读许可全文确认")
    else:
        out["status"] = "ok"
        out["note"] = "表内记录与字体文件 name 表 ID 13/14 一致"
    return out


def verify(name: str | None) -> dict:
    """表内记录 × 字体文件 name 表：逐字段摆出来，给人工复核用。"""
    _alias, r, _how = entry_of(name)
    lic = licence_of(name)
    p = file_path((r or {}).get("file") or name)
    return {"font": lic, "file_path": str(p) if p else None,
            "name_table": read_name_table(p) if p else None}


def main() -> int:
    ap = argparse.ArgumentParser(description="字体表")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--size", default=None)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--licence", default=None, help="打印某字体的授权口径（JSON）")
    ap.add_argument("--verify", default=None, help="表内授权 × 字体文件 name 表对账")
    a = ap.parse_args()
    if a.licence:
        print(json.dumps(licence_of(a.licence), ensure_ascii=False, indent=1))
        return 0
    if a.verify:
        print(json.dumps(verify(a.verify), ensure_ascii=False, indent=1))
        return 0
    if a.list:
        t = load()
        print(f"默认：{t.get('default')}　目标单位宽：{t.get('target_unit_w')}")
        for k, v in (t.get("fonts") or {}).items():
            print(f"  {k:<8} {v['file']:<30} ratio {v['ratio']:<6} "
                  f"10 单位 = {size_px(k, a.width)}px　授权：{v.get('license') or UNVERIFIED}")
        return 0
    if a.size:
        print(size_px(a.size, a.width))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
