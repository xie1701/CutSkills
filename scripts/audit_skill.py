#!/usr/bin/env python3
"""审查：说明书说的话，和 scripts/ 干的事，对不对得上。

为什么需要这个脚本：这套 skill 的规模已经过了「人肉读一遍就能确认一致」的线。
38800 字的 SKILL.md 说要跑 `--l2-budget-note`，脚本里那个参数被改名了，人不会发现；
文档说「字幕按字断句」，代码按整句收字，也没人会读出来——2026-11-07 就是这么
让字幕念了一整晚已经被剪掉的句子。**能机器查的，就别靠人眼。**

机器能查的五类（这就是全部范围，别指望它判文笔）：

1. 文档里点名的每个 `scripts/x.py` / `references/x.md` / `styles/x.yaml` 是否真存在
2. 文档命令里的每个 `--flag`，那个脚本是否真接受（反之亦然：脚本有、文档没写）
3. `scripts/` 里每个脚本是否在文档里出现过（没出现 = 别人永远不会跑它）
4. frontmatter 是不是合法 YAML、必填字段在不在
5. 全部脚本能不能编译

查不了的：那些「应该这样做」的散文。那要人读，或者让一个没参加过讨论的人照文档跑一遍。

    audit_skill.py [--root .] [--out audit.md]
"""
from __future__ import annotations

import argparse
import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path

FLAG_IN_CMD = re.compile(r"(?<![\w-])(--[A-Za-z0-9][\w-]*)")
FLAG_IN_SRC = re.compile(r"add_argument\(\s*[\"'](--[A-Za-z0-9][\w-]*)[\"']")
PATH_MENTION = re.compile(r"(?<![\w/.-])((?:scripts|references|styles|py)/[A-Za-z0-9_./-]+)")
SCRIPT_IN_CMD = re.compile(r"(?:scripts/)?([a-z0-9_]+\.py)")
FENCE = re.compile(r"```([a-zA-Z]*)\n(.*?)```", re.S)


def required_flags(src: str) -> set[str]:
    """不带这个参数就会报错的那些参数（`required=True`）。

    文档里少写一个 required 参数，别人照着敲就是一条报错——这种错机器一眼就能查出来。
    """
    out: set[str] = set()
    for m in re.finditer(r"add_argument\s*\(([^)]*)\)", src, re.S):
        body = m.group(1)
        if "required=True" not in body.replace(" ", ""):
            continue
        f = FLAG_IN_SRC.search("add_argument(" + body + ")")
        if f:
            out.add(f.group(1))
    return out


def schema_fields(md: str) -> dict[str, set[str]]:
    """schema 文档里出现过的字段名：代码块里的 JSON key + 正文（含表格）里反引号括起来的标识符。

    判据是「这个字段名在这份文档里被提及过」，不是「写得像一条正式定义」——
    保守一点只会让检查变成一堵假红灯墙，然后所有人学会忽略它。
    """
    names: set[str] = set()
    for m in FENCE.finditer(md):
        for ln in m.group(2).splitlines():
            ln = ln.strip().strip(",")
            k = re.match(r"\"?([a-z_][a-z0-9_]*)\"?\s*:", ln)
            if k:
                names.add(k.group(1))
    for k in re.findall(r"`([a-z_][a-z0-9_]*)(?:\.[a-z_][a-z0-9_]*)?`", md):
        names.add(k)
    return {"fields": names}


def read_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, "文档没有 frontmatter"
    end = text.find("\n---", 3)
    if end < 0:
        return {}, "frontmatter 没有收尾的 ---"
    raw = text[3:end]
    try:
        import yaml
        got = yaml.safe_load(raw)
        return (got if isinstance(got, dict) else {}), ""
    except Exception as e:                                    # noqa: BLE001
        return {}, f"frontmatter 不是合法 YAML：{e}"


def flags_of(script: Path) -> set[str]:
    try:
        src = script.read_text()
    except OSError:
        return set()
    return set(FLAG_IN_SRC.findall(src))


def commands_in(text: str) -> list[tuple[int, str]]:
    """把代码块里的命令行拆出来（含 `\\` 续行），带原文行号。"""
    out: list[tuple[int, str]] = []
    for m in FENCE.finditer(text):
        body, base = m.group(2), text[:m.start()].count("\n") + 1
        lines = body.splitlines()
        buf, start = "", 0
        for i, ln in enumerate(lines):
            s = ln.rstrip()
            if not buf and not s.strip():
                continue
            if not buf:
                start = base + i
            if s.endswith("\\"):
                buf += s[:-1] + " "
                continue
            buf += s
            if buf.strip():
                out.append((start, buf.strip()))
            buf = ""
        if buf.strip():
            out.append((start, buf.strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--out", default=None, help="默认写到 <root>/audit.md")
    ap.add_argument("--project", default=None,
                    help="给一个真项目目录：把 edl-schema.md 写的字段和真 edl.json 对账")
    ap.add_argument("--library", default=None, help="给素材库：把 index-schema.md 和真 index.json 对账")
    a = ap.parse_args()
    root = Path(a.root).expanduser().resolve()
    doc_path = root / "SKILL.md"
    if not doc_path.exists():
        print(f"没有说明书：{doc_path}", file=sys.stderr)
        return 2
    doc = doc_path.read_text()
    refs = sorted((root / "references").glob("*.md")) if (root / "references").exists() else []
    ref_text = {p.name: p.read_text() for p in refs}
    corpus = doc + "\n" + "\n".join(ref_text.values())

    red: list[str] = []
    yellow: list[str] = []
    green: list[str] = []

    # ---- 1. frontmatter
    fm, fm_err = read_frontmatter(doc)
    if fm_err:
        red.append(f"frontmatter 坏了：{fm_err}")
    else:
        for k in ("name", "description"):
            if not fm.get(k):
                red.append(f"frontmatter 缺 `{k}`")
        if fm.get("name") and fm["name"] != root.name.lower():
            yellow.append(f"frontmatter name「{fm['name']}」和目录名「{root.name}」不一样"
                          "（挂载后靠 name 找 skill，对不上会加载不到）")
        if not fm_err:
            green.append("frontmatter 合法，name / description 都在")

    # ---- 2. 文档点名的文件是否存在
    missing: list[str] = []
    for m in PATH_MENTION.finditer(corpus):
        rel = m.group(1).rstrip(".,，。）)")
        if "<" in rel or "*" in rel or rel.endswith("/"):
            continue
        if rel.startswith("py/"):                       # PYTHONPATH 里的包目录，不是本仓文件
            continue
        if not (root / rel).exists():
            line = corpus[:m.start()].count("\n") + 1
            missing.append(f"`{rel}`（第 {line} 行附近）")
    for x in sorted(set(missing)):
        red.append(f"文档点名的文件不存在：{x}")
    if not missing:
        green.append("文档点名的 scripts / references / styles 文件全部存在")

    # ---- 3. 命令行里的参数是否被脚本接受
    unknown: list[str] = []
    used: dict[str, set[str]] = {}
    for line_no, cmd in commands_in(doc):
        if "scripts/" not in cmd and "runtime.sh" not in cmd:
            continue
        m = SCRIPT_IN_CMD.search(cmd)
        if not m:
            continue
        name = m.group(1)
        cand = root / "scripts" / name
        if name == "runtime.sh":
            cand = root / "scripts" / "runtime.sh"
        if not cand.exists():
            continue
        if name not in used:
            used[name] = flags_of(cand)
        known = used[name]
        for f in FLAG_IN_CMD.findall(cmd):
            if "--" in f and f not in known:
                unknown.append(f"`{name} {f}`（SKILL.md 第 {line_no} 行）")
    for x in sorted(set(unknown)):
        red.append(f"文档让跑的参数，脚本不认：{x}")
    if not unknown:
        green.append("文档命令里的参数，脚本全部接受")

    # ---- 3b. 文档命令有没有漏掉 required 参数（少写一个就是一条报错）
    lack: list[str] = []
    for line_no, cmd in commands_in(doc):
        m = SCRIPT_IN_CMD.search(cmd)
        if not m or "scripts/" not in cmd:
            continue
        cand = root / "scripts" / m.group(1)
        if not cand.exists():
            continue
        for f in sorted(required_flags(cand.read_text()) - set(FLAG_IN_CMD.findall(cmd))):
            lack.append(f"`{m.group(1)}` 这条命令少了必填参数 `{f}`（SKILL.md 第 {line_no} 行）")
    for x in sorted(set(lack)):
        red.append(f"照文档敲会报错：{x}")
    if not lack:
        green.append("文档命令的必填参数都在")

    # ---- 3c. cli.md 是不是代码的最新快照
    cli_f = root / "references" / "cli.md"
    if cli_f.exists():
        tmp = Path("/tmp") / ".cutskills-cli-check.md"
        r = subprocess.run([sys.executable, str(root / "scripts" / "gen_cli_ref.py"),
                            "--root", str(root), "--out", str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            red.append(f"cli.md 重新生成失败：{(r.stderr or r.stdout).strip().splitlines()[-1][:120]}")
        elif tmp.read_text() != cli_f.read_text():
            red.append("`references/cli.md` 过期了：脚本参数变了但没重跑 `gen_cli_ref.py`"
                       "——那份表就是别人找功能的入口，过期等于误导")
        else:
            green.append("references/cli.md 与脚本参数一致")
    else:
        red.append("缺 `references/cli.md`：101 个参数只写在代码里，没人找得到")

    # ---- 4. 脚本有、文档从没提过的参数（死参数 / 隐藏能力）
    orphan_flags: list[str] = []
    cli_text = cli_f.read_text() if cli_f.exists() else ""
    for p in sorted((root / "scripts").glob("*.py")):
        docd = set()
        for _, cmd in commands_in(doc):
            if p.name in cmd:
                docd |= set(FLAG_IN_CMD.findall(cmd))
        for f in sorted(flags_of(p) - docd):
            if f in ("--help",):
                continue
            if f"`{f}`" in cli_text:          # cli.md 里写了，就算「有人能查到」
                continue
            orphan_flags.append(f"`{p.name} {f}`")
    if orphan_flags:
        yellow.append(f"{len(orphan_flags)} 个参数连 cli.md 都没有（脚本读参数的方式跟 add_argument 不一样？）："
                      + "、".join(orphan_flags[:8]) + ("…" if len(orphan_flags) > 8 else ""))

    # ---- 5. scripts/ 里的脚本有没有被文档提过
    dead: list[str] = []
    for p in sorted((root / "scripts").glob("*.py")):
        if p.name not in corpus:
            dead.append(p.name)
    for x in dead:
        red.append(f"脚本 `{x}` 在文档里从没出现——写了没人会跑")
    if not dead:
        green.append("scripts/ 里每个脚本都在文档里出现过")

    # ---- 6. references/ 有没有被文档链接
    orphan_ref: list[str] = []
    for p in refs:
        if p.name not in doc:
            orphan_ref.append(p.name)
    for x in orphan_ref:
        yellow.append(f"`references/{x}` 没有从 SKILL.md 链过去——它的话没人会读到")

    # ---- 7. 能不能编译
    bad: list[str] = []
    for p in sorted((root / "scripts").glob("*.py")):
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(Path("/tmp") / f"{p.stem}.pyc"))
        except py_compile.PyCompileError as e:
            bad.append(f"{p.name}：{str(e).splitlines()[-1][:120]}")
    for x in bad:
        red.append(f"编译不过：{x}")
    if not bad:
        green.append(f"{len(list((root/'scripts').glob('*.py')))} 个脚本全部编译通过")

    # ---- 8. schema 文档 vs 真产物：双向对账
    if a.project:
        proj = Path(a.project).expanduser().resolve()
        edl_f = proj / "edl.json"
        sc = root / "references" / "edl-schema.md"
        if edl_f.exists() and sc.exists():
            edl = json.loads(edl_f.read_text())
            known = schema_fields(sc.read_text())["fields"]
            real: set[str] = set()
            for s in edl.get("segments") or []:
                real |= set(s.keys())
            real |= set(edl.keys())
            extra = sorted(real - known)
            if extra:
                yellow.append(f"edl.json 里有 {len(extra)} 个字段 schema 没写："
                              + "、".join(f"`{x}`" for x in extra))
            else:
                green.append("edl.json 的字段 schema 全写了")
        if edl_f.exists():
            missing_ev = [s.get("n") for s in (json.loads(edl_f.read_text()).get("segments") or [])
                          if not s.get("evidence")]
            if missing_ev:
                yellow.append(f"EDL 段 {missing_ev} 没有 `evidence`——纸编辑那列会是空的，人看不到这段说什么")

    if a.library:
        lib = Path(a.library).expanduser().resolve()
        idx_f = lib / "_cutskills" / "index.json"
        sc = root / "references" / "index-schema.md"
        if idx_f.exists() and sc.exists():
            idx = json.loads(idx_f.read_text())
            known = schema_fields(sc.read_text())["fields"]
            real: set[str] = set()
            for x in idx.get("assets") or []:
                real |= set(x.keys())
            extra = sorted(real - known)
            if extra:
                yellow.append(f"index.json 里有 {len(extra)} 个字段 schema 没写："
                              + "、".join(f"`{x}`" for x in extra))
            else:
                green.append("index.json 的字段 schema 全写了")

    # ---- 9. 文档里那些「必须 / 不许」的硬规则条数（只报数，判不了对错）
    rules = re.findall(r"^\s*(\d+)\.\s+\*\*", doc, re.M)
    if rules:
        green.append(f"硬规则 {len(rules)} 条（编号 {rules[0]}–{rules[-1]}）")

    status = "FAIL" if red else ("WARN" if yellow else "OK")
    lines = [f"# 说明书自检 · {root.name}", "",
             f"**{status}** · 🔴 {len(red)} · 🟡 {len(yellow)} · 🟢 {len(green)}", "",
             "只查「文档说的和代码做的对不对得上」。文笔、判断、策略，一行都没查。", ""]
    for title, items, icon in (("必须修", red, "🔴"), ("看一眼", yellow, "🟡"), ("通过", green, "🟢")):
        lines.append(f"## {icon} {title}")
        lines += [f"- {x}" for x in items] or ["- —"]
        lines.append("")
    out = Path(a.out) if a.out else root / "audit.md"
    out.write_text("\n".join(lines))
    print(f"审查：{status} · 🔴 {len(red)} · 🟡 {len(yellow)} · 🟢 {len(green)}")
    for x in red:
        print(f"  🔴 {x}")
    for x in yellow:
        print(f"  🟡 {x}")
    print(f"→ {out}")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
