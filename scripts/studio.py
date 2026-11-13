#!/usr/bin/env python3
"""一键起工程页：把 `cd <项目>/hf && hyperframes preview` 收成一条命令。

为什么要它：工程页藏在 `_cutskills/projects/<项目>/hf/`，端口、`--open`、后台运行都各有讲究；
每条片都让人自己去拼路径，等于等于没有入口。

    python3 scripts/studio.py --list                      # 现在开着哪些，端口多少
    python3 scripts/studio.py --project dianzan-a         # 起（已经开着就只报地址并打开）
    python3 scripts/studio.py --project dianzan-a --no-open
    python3 scripts/studio.py --stop dianzan-a            # 关掉这一条
    python3 scripts/studio.py --stop-all
    python3 scripts/studio.py --print-cmd --project qizao-ground-01    # 只打印要跑的命令
    python3 scripts/studio.py --emit-launchers <目录>       # 给每支片写一个双击就开的 .command

工程页里改的是渲染产物 `hf/index.html`——**下次重渲会被覆盖**。要留住改动就回写 `timeline.json`。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from libs import DEFAULT_LIBS, find_projects  # noqa: E402  同一套库解析，不另写一份

PORT_BASE = 3100
PORT_TRIES = 40
BOOT_WAIT = 30.0          # 起一个 studio 的实测上限：冷启 5–9s，给它余量
LIST_RE = re.compile(r"Port\s+(\d+)\s+\S+\s+(.+?)\s+\(PID\s+(\d+)\)")


def hf_bin() -> str:
    """hyperframes 的绝对路径：先找本机钉住的安装，再退回 PATH。"""
    cand = Path.home() / ".cutskills" / "npm" / "node_modules" / ".bin" / "hyperframes"
    if cand.exists():
        return str(cand)
    found = shutil.which("hyperframes")
    if not found:
        raise SystemExit("找不到 hyperframes——先跑 `source scripts/runtime.sh env && scripts/runtime.sh install`")
    return found


def hf_dir(proj: Path) -> Path | None:
    d = proj / "hf"
    return d if (d / "index.html").exists() else None


def running() -> list[dict]:
    """当前活着的 studio：直接读 hyperframes 自己的登记，不维护第二本账。"""
    try:
        out = subprocess.run([hf_bin(), "preview", "--list"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for m in LIST_RE.finditer(out):
        port, d, pid = m.group(1), m.group(2), m.group(3)
        rows.append({"port": int(port), "dir": Path(d), "pid": int(pid),
                     "url": f"http://127.0.0.1:{port}/studio"})
    return rows


def running_for(proj: Path) -> dict | None:
    d = hf_dir(proj)
    if not d:
        return None
    for r in running():
        if r["dir"].resolve() == d.resolve():
            return r
    return None


def alive(url: str) -> bool:
    """裸 socket 问一句 200——不走 urllib，免得本机的代理环境变量把 localhost 也代理掉。"""
    m = re.match(r"http://([^:/]+):(\d+)", url)
    if not m:
        return False
    host, port = m.group(1), int(m.group(2))
    try:
        with socket.create_connection((host, port), timeout=3) as s:
            s.sendall(f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode())
            head = s.recv(64).decode("latin-1", "replace")
        return " 200 " in head
    except OSError:
        return False


def free_port(taken: set[int]) -> int:
    for port in range(PORT_BASE, PORT_BASE + PORT_TRIES):
        if port in taken:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"{PORT_BASE}–{PORT_BASE + PORT_TRIES} 都被占了，用 --port 指定一个")


def resolve(name: str, libs: list[str]) -> list[Path]:
    """按项目名找项目：先精确、再唯一前缀、最后当路径。"""
    if os.sep in name or name.startswith("~"):
        p = Path(name).expanduser().resolve()
        return [p] if hf_dir(p) else []
    projects = [p for p in find_projects(libs) if hf_dir(p)]
    exact = [p for p in projects if p.name == name]
    if exact:
        return exact
    hits = [p for p in projects if p.name.startswith(name) or name in p.name]
    return hits


def cmd_of(proj: Path, port: int | None = None) -> str:
    d = hf_dir(proj)
    return f'cd "{d}" && "{hf_bin()}" preview . --open' + (f" --port {port}" if port else "")


def start(proj: Path, port: int | None = None, open_browser: bool = True, wait: bool = True) -> dict:
    d = hf_dir(proj)
    if not d:
        raise SystemExit(f"{proj} 里没有 hf/index.html——这条片还没渲过工程页")
    cur = running_for(proj)
    if cur:
        if open_browser:
            subprocess.run(["open", cur["url"]])
        return cur
    if port is None:
        port = free_port({r["port"] for r in running()})
    subprocess.run([hf_bin(), "preview", str(d), "--port", str(port), "--background", "--no-open"],
                   capture_output=True, text=True, timeout=120)
    url = f"http://127.0.0.1:{port}/studio"
    if wait:
        t0 = time.time()
        while time.time() - t0 < BOOT_WAIT:
            if alive(url):
                break
            time.sleep(0.5)
        else:
            raise SystemExit(f"studio 起了 {BOOT_WAIT:.0f}s 还没响应：{cmd_of(proj, port)}")
    if open_browser:
        subprocess.run(["open", url])
    return {"port": port, "dir": d, "pid": -1, "url": url}


def stop(proj: Path) -> bool:
    d = hf_dir(proj)
    if not d:
        return False
    r = subprocess.run([hf_bin(), "preview", str(d), "--stop"], capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def emit_launchers(outdir: Path, libs: list[str]) -> list[Path]:
    """给每支片写一个双击就开的 `.command`（macOS Finder 里双击 = 起工程页）。"""
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    for p in find_projects(libs):
        d = hf_dir(p)
        if not d:
            continue
        f = outdir / f"启动工程-{p.name}.command"
        f.write_text('#!/bin/bash\n# 双击就起工程页；关掉这个终端窗口 = 关掉 studio\n'
                     f'cd "{d}" || exit 1\n"{hf_bin()}" preview . --open\n', encoding="utf-8")
        f.chmod(0o755)
        made.append(f)
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一键起工程页（HyperFrames studio）")
    ap.add_argument("--project", help="项目名（dianzan-a / qizao-ground-01 …）或目录")
    ap.add_argument("--library", action="append", help="要搜的库根（可多次）")
    ap.add_argument("--port", type=int, help="指定端口（默认 3100 起找空位）")
    ap.add_argument("--no-open", action="store_true", help="不自动开浏览器")
    ap.add_argument("--print-cmd", action="store_true", help="只打印要跑的命令，不真起")
    ap.add_argument("--list", action="store_true", help="列出开着的 studio")
    ap.add_argument("--stop", metavar="项目", help="关掉某个项目的 studio")
    ap.add_argument("--stop-all", action="store_true", help="关掉全部")
    ap.add_argument("--emit-launchers", metavar="目录", help="给每支片写一个 .command")
    a = ap.parse_args(argv)

    libs = a.library or DEFAULT_LIBS

    if a.list or (not any([a.project, a.stop, a.stop_all, a.emit_launchers])):
        rows = running()
        if not rows:
            print("现在没有开着的工程页。起一个：python3 scripts/studio.py --project dianzan-a")
            return 0
        print(f"{'项目':<22}{'端口':<7}{'状态':<6}地址")
        for r in rows:
            print(f"{r['dir'].parent.name:<22}{r['port']:<7}{'🟢 在跑' if alive(r['url']) else '🔴 无响应':<6}{r['url']}")
        return 0

    if a.stop_all:
        subprocess.run([hf_bin(), "preview", "--kill-all"], capture_output=True, text=True, timeout=120)
        print("已关掉全部 studio。")
        return 0

    if a.emit_launchers:
        made = emit_launchers(Path(a.emit_launchers).expanduser(), libs)
        for f in made:
            print(f"→ {f}")
        print(f"{len(made)} 个双击入口")
        return 0

    if a.stop:
        hits = resolve(a.stop, libs)
        if len(hits) != 1:
            print(f"「{a.stop}」对不上唯一项目" + (f"（命中 {len(hits)} 个）" if hits else "（没有命中）"), file=sys.stderr)
            return 2
        ok = stop(hits[0])
        print(("已关掉 " if ok else "关的时候没成功：") + hits[0].name)
        return 0 if ok else 1

    hits = resolve(a.project, libs)
    if not hits:
        print(f"找不到项目「{a.project}」；先跑 python3 scripts/showcase.py 看有哪些片", file=sys.stderr)
        return 2
    if len(hits) > 1:
        print(f"「{a.project}」命中 {len(hits)} 个项目，说全一点：", file=sys.stderr)
        for h in hits:
            print(f"  {h.name}", file=sys.stderr)
        return 2
    proj = hits[0]

    if a.print_cmd:
        print(cmd_of(proj, a.port))
        return 0

    r = start(proj, a.port, open_browser=not a.no_open)
    print(f"{proj.name} · 工程页 {r['url']}（Ctrl-C 不会关掉它；关：--stop {proj.name}）")
    print("  改的是渲染产物 hf/index.html，下次重渲会被覆盖——要留住就回写 timeline.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
