#!/usr/bin/env python3
"""`index_assets.merge_l2` 的归属核对：负样本（记到别人头上）+ 正样本（正常合并）。

为什么这条必须是负样本测试：L2 是唯一手写的一环，最容易犯的错是「文件名写的是 A，
内容判的是 B」。旧行为对这种记录**静默 continue**——索引照样合、名字照样改，只有对着
素材本身看才发现判错了。柒造那库真出过：`材料搅拌` 和 `地面涂刷` 两支素材的名字互换。

口径：
- 归属不符 = 文件名里的哈希不是这支素材的 legacy_key / uid。默认**仍继续合并**，但必须
  逐条点名（哪条、为什么不符）；`--strict` 下整轮非零退出，且不写索引。
- 正常记录（哈希对得上、字段齐）必须合进 `l2_segments` 并覆盖 `segments`。

    python3 scripts/test_index_merge.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import index_assets as IA  # noqa: E402

SCRIPTS = ROOT / "scripts"
AID = "批次/材料搅拌.mp4"
KEY = "0123456789ab"                 # 这支素材的 legacy_key
FOREIGN = "ba9876543210"             # 另一支素材的 key（地上涂刷）

fails: list[str] = []


def must(what: str, ok: bool, detail: str = "") -> None:
    print(("  ✅ " if ok else "  ❌ ") + what + (f"｜{detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def record(asset: str = AID, **over) -> dict:
    r = {
        "asset": asset, "in": 0.0, "out": 6.0, "shot": "close", "people": ["主理人"],
        "action": "拧螺丝", "text_on_screen": None, "tag": "材料对比",
        "summary": "拿两支料对着看", "grade": "must-use", "why": "画面干净、口播在讲重点",
        "confidence": 0.8, "flags": [],
    }
    r.update(over)
    return r


def asset(**over) -> dict:
    a = {"id": AID, "path": AID, "kind": "video", "duration": 12.0,
         "legacy_key": KEY, "uid": None}
    a.update(over)
    return a


def write_l2(lib: Path, name: str, rec: dict) -> Path:
    d = lib / IA.SYS_DIR / "l2"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.json"
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return f


def case_positive() -> None:
    print("① 正样本：哈希对得上、字段齐全 → 正常合并，零硬错")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir()
        write_l2(lib, f"{KEY}-0", record())
        assets = [asset()]
        n, errs = IA.merge_l2(lib, assets)
        must("合进 1 条", n == 1, f"n={n}")
        must("没有硬错", errs == [], "；".join(errs))
        must("记录进了 l2_segments", len(assets[0].get("l2_segments") or []) == 1)
        segs = assets[0].get("segments") or []
        must("L2 段覆盖了 L0 段", len(segs) == 1 and segs[0].get("summary") == "拿两支料对着看",
             json.dumps(segs, ensure_ascii=False)[:160])
        must("asset 字段不重复进段记录", "asset" not in (assets[0]["l2_segments"][0]))


def case_ownership() -> None:
    print("② 负样本：文件名哈希是别人的 → 点名报错；默认仍合并，strict 非零退出")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir()
        write_l2(lib, f"{FOREIGN}-0", record())
        assets = [asset()]
        n, errs = IA.merge_l2(lib, assets)
        must("硬错抓到了 1 条", len(errs) == 1, "；".join(errs))
        msg = "；".join(errs)
        must("点名具体那条记录", f"{FOREIGN}-0.json" in msg, msg)
        must("说清为什么不符（两个哈希都在）", FOREIGN in msg and KEY in msg, msg)
        must("说清判给了哪支素材", AID in msg, msg)
        must("默认仍继续合并（不静默丢数据）", n == 1, f"n={n}")

    # strict：CLI 非零退出，且**不写索引**——错的索引比没有索引更坏
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        (lib / "批次").mkdir(parents=True)
        (lib / "批次" / "材料搅拌.mp4").write_bytes(b"\0" * 32)
        sysdir = lib / IA.SYS_DIR
        sysdir.mkdir()
        idx = {"version": 2, "assets": [asset()]}
        idx_f = sysdir / "index.json"
        idx_f.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        write_l2(lib, f"{FOREIGN}-0", record())
        r = subprocess.run([sys.executable, str(SCRIPTS / "index_assets.py"),
                            "--library", str(lib), "--stage", "finish", "--strict"],
                           capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        must("--strict 非零退出", r.returncode != 0, f"rc={r.returncode}｜{out[-300:]}")
        must("stderr 里点名那条记录", f"{FOREIGN}-0.json" in out, out[-300:])
        must("--strict 不写索引", json.loads(idx_f.read_text())["assets"] == [asset()],
             idx_f.read_text()[:200])

        # 同一条记录，去掉 --strict 就该放行并真的把段合进去
        r2 = subprocess.run([sys.executable, str(SCRIPTS / "index_assets.py"),
                             "--library", str(lib), "--stage", "finish"],
                            capture_output=True, text=True)
        must("不加 --strict 退出码 0", r2.returncode == 0, ((r2.stderr or r2.stdout)[-300:]))
        must("不加 --strict 仍留可见告警",
             f"{FOREIGN}-0.json" in ((r2.stdout or "") + (r2.stderr or "")),
             ((r2.stderr or r2.stdout)[-300:]))
        after = json.loads(idx_f.read_text())["assets"][0]
        must("不加 --strict 照常合并", len(after.get("l2_segments") or []) == 1,
             json.dumps(after.get("l2_segments"), ensure_ascii=False)[:200])


def case_no_owner() -> None:
    print("③ 负样本：asset 不在索引里 / 字段与范围出错 → 都点名")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir()
        write_l2(lib, f"{KEY}-0", record(asset="批次/根本没这支.mp4"))
        assets = [asset()]
        n, errs = IA.merge_l2(lib, assets)
        must("没有主人的记录不算合并", n == 0, f"n={n}")
        must("点名「不在索引里」", any("不在索引里" in e for e in errs), "；".join(errs))

    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir()
        write_l2(lib, f"{KEY}-9", record(**{"in": 8.0, "out": 3.0}))
        write_l2(lib, f"{KEY}-11", record(**{"in": 0.0, "out": 99.0}))
        write_l2(lib, f"{KEY}-12", record(grade="也许能用"))
        assets = [asset()]
        _, errs = IA.merge_l2(lib, assets)
        msg = "；".join(errs)
        must("out ≤ in 被抓住", any("不大于" in e for e in errs), msg)
        must("超出素材时长被抓住", any("超出素材时长" in e for e in errs), msg)
        must("grade 不在枚举被抓住", any("grade" in e for e in errs), msg)


def case_warn_only() -> None:
    print("④ 结构瑕疵只提示、不阻断（缺字段 / tag 长度 / confidence 越界）")
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        lib.mkdir()
        rec = record(tag="一", confidence=1.7)
        rec.pop("people")
        write_l2(lib, f"{KEY}-0", rec)
        assets = [asset()]
        n, errs = IA.merge_l2(lib, assets)
        must("照样合并", n == 1, f"n={n}")
        must("结构瑕疵不算硬错", errs == [], "；".join(errs))


def main() -> int:
    case_positive()
    case_ownership()
    case_no_owner()
    case_warn_only()
    print()
    if fails:
        print(f"❌ {len(fails)} 条没过：" + "、".join(fails))
        return 1
    print("✅ merge_l2 归属核对 4 组全过（正样本 1 + 负样本 3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
