#!/usr/bin/env python3
"""把 Cola.app 里打包的 `@marswave/coli` 抽成能跑的 CLI（T-023）。

为什么需要这一脚：2026-11-13 实测 `which coli` 为空——Cola 升级后不再把 coli
暴露成系统命令，而本地转写（`coli asr --model sensevoice`）只有这一条路，
它一没，selftest ②–⑤、`test_hygiene_fixtures.py`、任何新素材首次 ASR 一起哑。

coli 本体没丢，它在 Cola 的安装包里：
  /Applications/Cola.app/Contents/Resources/app.asar → node_modules/@marswave/coli
但 asar 是打包容器的格式，系统 node 读不了里面的文件，所以这里按 asar 头把
coli 及其依赖闭包解出来，落进 `$CUTSKILLS_HOME/opt/coli-app`，再软链
`$CUTSKILLS_HOME/opt/bin/coli`（`scripts/runtime.sh env` 把该目录排 PATH 最前）。

只读 Cola.app，不联网、不碰 `~/.cutskills` 以外的任何东西。
模型不在这里：首次 `coli asr` 自己下 155.5 MB 到 `~/.coli/models`（见 deps.json models.asr）。

用法：
  python3 scripts/fetch_coli.py            # 抽（或 App 升级后重抽）
  python3 scripts/fetch_coli.py --check    # 只报现在能不能用，不动文件
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys

APP = pathlib.Path("/Applications/Cola.app/Contents/Resources")
ASAR = APP / "app.asar"
UNPACKED = APP / "app.asar.unpacked"
HOME = pathlib.Path(os.environ.get("CUTSKILLS_HOME") or pathlib.Path.home() / ".cutskills")
DEST = HOME / "opt" / "coli-app"
BIN = HOME / "opt" / "bin" / "coli"
ROOT_PKG = "@marswave/coli"


class Asar:
    """asar 的最小读取器：头部是 JSON 目录树，数据段在头部之后按 4 字节对齐。"""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        with open(path, "rb") as f:
            hdr = f.read(16)
            (json_len,) = struct.unpack("<I", hdr[12:16])
            self.tree = json.loads(f.read(json_len).split(b"\x00")[0].decode("utf-8"))
            # 数据段起点：8 + 头部 pickle 长度（不是 16 + json 长度——差 3 字节会把包读花）
            self.base = 8 + struct.unpack("<I", hdr[4:8])[0]

    def node_modules(self) -> dict:
        return self.tree["files"]["node_modules"]["files"]

    def lookup(self, name: str) -> dict | None:
        node: dict = {"files": self.node_modules()}
        for part in name.split("/"):
            node = (node.get("files") or {}).get(part)  # type: ignore[assignment]
            if node is None:
                return None
        return node

    def read(self, entry: dict) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(self.base + int(entry["offset"]))
            return f.read(int(entry["size"]))


def write_tree(asar: Asar, node: dict, dest: pathlib.Path, rel: str) -> None:
    if "files" in node:
        dest.mkdir(parents=True, exist_ok=True)
        for name, child in node["files"].items():
            write_tree(asar, child, dest / name, f"{rel}/{name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if node.get("unpacked"):
        # 原生扩展（sherpa-onnx.node 等）本来就在 asar 外面，直接搬
        shutil.copy2(UNPACKED / rel, dest)
    else:
        dest.write_bytes(asar.read(node))


def extract(asar: Asar, name: str) -> bool:
    node = asar.lookup(name)
    if node is None:
        print(f"  ! asar 里没有 {name}")
        return False
    write_tree(asar, node, DEST / "node_modules" / name, f"node_modules/{name}")
    return True


def deps_of(pkg_dir: pathlib.Path) -> dict:
    pj = pkg_dir / "package.json"
    try:
        return (json.loads(pj.read_text()) or {}).get("dependencies") or {}
    except Exception:
        return {}


def resolvable(name: str, base: pathlib.Path) -> bool:
    """按 node 的规则往上找 node_modules/<name>。"""
    cur = base.resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "node_modules" / name).is_dir():
            return True
    return False


def installed_packages() -> list[pathlib.Path]:
    nm = DEST / "node_modules"
    return [p.parent for p in nm.rglob("package.json") if p.parent.name != "node_modules"]


def main() -> int:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        return 0
    check_only = "--check" in sys.argv[1:]
    version = None
    cli = DEST / "node_modules" / ROOT_PKG / "distribution" / "source" / "cli.js"
    if cli.exists():
        try:
            version = json.loads((cli.parents[2] / "package.json").read_text()).get("version")
        except Exception:
            version = None
    if check_only:
        ok = bool(shutil.which("coli") or (BIN.exists() and cli.exists()))
        print(f"{'ok  ' if ok else '缺失'} {BIN if BIN.exists() else '（未装）'}  coli {version or '?'}")
        return 0 if ok else 1

    if not ASAR.exists():
        print(f"⛔ 找不到 {ASAR}——Cola 没装在这台机器上？")
        return 2
    asar = Asar(ASAR)
    if asar.lookup(ROOT_PKG) is None:
        print(f"⛔ {ASAR} 里没有 {ROOT_PKG}——Cola 版本变了，包的布局要重新找。")
        return 2

    print(f"抽 {ROOT_PKG} → {DEST}")
    DEST.mkdir(parents=True, exist_ok=True)
    extracted = [ROOT_PKG]
    if not extract(asar, ROOT_PKG):
        return 2
    # 依赖闭包：coli 的依赖有些装在顶层 node_modules（commander / ky / sherpa-onnx-node），
    # 少一个就是运行时 ERR_MODULE_NOT_FOUND，所以循环补到不再缺为止。
    for _ in range(20):
        missing: set[str] = set()
        for pkg_dir in installed_packages():
            for dep in deps_of(pkg_dir):
                if not resolvable(dep, pkg_dir):
                    missing.add(dep)
        if not missing:
            break
        for dep in sorted(missing):
            if extract(asar, dep):
                extracted.append(dep)
    else:
        print("  ! 依赖闭包 20 轮还没收敛，可能还有缺的")

    cli.chmod(0o755)
    BIN.parent.mkdir(parents=True, exist_ok=True)
    if BIN.is_symlink() or BIN.exists():
        BIN.unlink()
    BIN.symlink_to(cli)
    ver = json.loads((DEST / "node_modules" / ROOT_PKG / "package.json").read_text()).get("version")
    print(f"  coli {ver}，共 {len(extracted)} 个包")
    print(f"  软链 {BIN} → {cli}")

    # 与 runtime.sh check 一个口径：清单里写着有 ≠ 跑得起来，真跑一次
    env = dict(os.environ, PATH=f"{BIN.parent}:{os.environ.get('PATH', '')}")
    r = subprocess.run(["coli", "--help"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"⛔ 抽出后跑不起来（退出 {r.returncode}）：{(r.stderr or r.stdout).strip()[:300]}")
        return 1
    print(f"  ok  {(r.stdout or '').splitlines()[0] if r.stdout else 'coli --help 通过'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
