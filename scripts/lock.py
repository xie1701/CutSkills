#!/usr/bin/env python3
"""方案锁：拍板的那一刻，把时间轴钉死。

为什么不是「一句话批准」：一句话不认账。改完一个字节，那个「批准」还挂在那儿，
渲染照样开工——等于没批。所以批准要落成一个哈希：timeline.json 变一个字节，
锁就自动失效，必须重新拍板。

  lock.py --project <项目> --lock --note "认可"     # 拍板
  lock.py --project <项目> --verify                # 查（渲染前自动跑）
  lock.py --project <项目> --status                # 看锁里写了什么

退出码：0 有效 / 1 失效（时间轴改过）/ 2 还没锁。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

LOCK = "lock.json"


def digest(proj: Path) -> str:
    """只认 timeline.json 的字节。别的文件（字体、素材）变了不该让方案失效。"""
    return hashlib.sha256((proj / "timeline.json").read_bytes()).hexdigest()


def lock(proj: Path, note: str = "") -> dict:
    tl = json.loads((proj / "timeline.json").read_text())
    els = [e for e in tl.get("elements") or [] if (e.get("track") or "video") != "overlay"]
    data = {
        "version": 1,
        "sha256": digest(proj),
        "duration_s": round(float(tl.get("duration_s") or 0), 3),
        "elements": len(els),
        "project": proj.name,
        "locked_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
    (proj / LOCK).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return data


def verify(proj: Path) -> tuple[int, str]:
    p = proj / LOCK
    if not p.exists():
        return 2, "还没拍板（没有 lock.json）"
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return 2, "lock.json 坏了，重新拍板"
    cur = digest(proj)
    if cur != data.get("sha256"):
        return 1, (f"时间轴在拍板之后改过：锁里 {data.get('sha256', '')[:12]}，"
                   f"现在 {cur[:12]}（{data.get('duration_s')}s → "
                   f"{json.loads((proj / 'timeline.json').read_text()).get('duration_s')}s）")
    return 0, (f"已拍板：{data.get('locked_at')} · {data.get('elements')} 格 · "
               f"{data.get('duration_s')}s{' · ' + data['note'] if data.get('note') else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--lock", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    proj = Path(a.project).expanduser().resolve()

    if a.lock:
        d = lock(proj, a.note)
        print(f"已拍板：{d['elements']} 格 · {d['duration_s']}s · sha {d['sha256'][:12]}"
              f"{' · ' + d['note'] if d['note'] else ''}")
        return 0
    code, msg = verify(proj)
    print(("🟢 " if code == 0 else ("🔴 " if code == 1 else "🟡 ")) + msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
