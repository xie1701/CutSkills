#!/usr/bin/env python3
"""把每个脚本的 argparse 扫一遍，生成 `references/cli.md`。

为什么要有这个：说明书只有 429 行，装不下 22 个脚本一共 100 多个参数；
不写进说明书，别人就永远不会知道 `search_library --for-cut` 能把「这段口播该配什么
画面」反查出来——功能存在但等于不存在。

**任何人不该手改 cli.md。** 改了脚本参数就重跑这个脚本。这样「文档说有、代码没有」
这种腐烂就不可能发生了：文档是从代码生成的。

    gen_cli_ref.py [--root .]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# 按流水线顺序排，不按字母——照文档跑的人关心的是「我现在在哪一段」
ORDER = ["runtime", "gen_cli_ref", "index_assets", "media_spec", "render_library_index", "rename_assets",
         "lexicon", "lint_transcript", "check_l2", "audit_skill",
         "init_project", "intake", "search_library", "build_takes_packed",
         "make_edl", "preview_plan", "check_edl", "render_paper_edit", "view_diff",
         "gaps", "log_decision",
         "render_film", "verify_film", "blind_test", "ab_open"]

STAGE = {
    "runtime": "⚠ 每件事之前——环境",
    "index_assets": "A · 索引",
    "media_spec": "A · 索引（库）",
    "render_library_index": "A · 索引（库）",
    "rename_assets": "A · 索引（库）",
    "lexicon": "A · 索引（库）",
    "lint_transcript": "A · 索引（库）",
    "check_l2": "A · 索引（库）",
    "audit_skill": "★ 任何一段之后——自检",
    "init_project": "B · 编导",
    "intake": "B · 编导",
    "search_library": "B · 编导",
    "build_takes_packed": "B · 编导",
    "make_edl": "B · 编导",
    "preview_plan": "B · 编导",
    "check_edl": "B · 编导",
    "render_paper_edit": "B · 编导",
    "view_diff": "B · 编导",
    "gaps": "B · 编导",
    "log_decision": "B · 编导",
    "render_film": "C · 渲染",
    "verify_film": "D · 验收",
    "blind_test": "D · 验收",
    "ab_open": "B/D · 对照",
}

ARG = None
LONG = None


def docstring(path: Path) -> str:
    """模块 docstring 的第一行。用 ast 读，手写正则会被多行字符串骗。"""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return ""
    d = ast.get_docstring(tree) or ""
    for ln in d.strip().splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def _clean(s: str) -> str:
    return " ".join(str(s).replace("%%", "%").split())


def args_of(path: Path) -> list[tuple[str, str, str]]:
    """扫 `add_argument(...)` 调用，读它真正的关键字参数。"""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    out: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not getattr(node.func, "attr", "") == "add_argument":
            continue
        flags = [x.value for x in node.args
                 if isinstance(x, ast.Constant) and isinstance(x.value, str)
                 and x.value.startswith("--")]
        if not flags:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        help_s = kw.get("help")
        note = _clean(ast.unparse(help_s).strip("'\"")) if help_s else ""
        required = isinstance(kw.get("required"), ast.Constant) and kw["required"].value is True
        action = ast.unparse(kw["action"]) if "action" in kw else ""
        if required:
            note = "**必填**　" + note
        elif "store_true" in action:
            note = note or "开关"
        elif "default" in kw:
            d = ast.unparse(kw["default"])
            if d not in ("None", "''", '""'):
                note += f"（默认 {d}）"
        out.append((flags[0], "required" if required else "", note.strip() or "—"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--out", default=None, help="默认写到 <root>/references/cli.md")
    a = ap.parse_args()
    root = Path(a.root).expanduser().resolve()
    scripts = root / "scripts"

    names = [p.stem for p in sorted(scripts.glob("*.py"))]
    ordered = [n for n in ORDER if n in names] + [n for n in sorted(names) if n not in ORDER]

    lines = ["# 全部命令与参数", "",
             "> 这份文件由 `scripts/gen_cli_ref.py` 从脚本的 `argparse` 生成，**不要手改**。",
             "> 改了参数就重跑：`python3 scripts/gen_cli_ref.py`。", "",
             "说明书讲的是「为什么这样剪」，这里只有「有什么开关」。查它能干的、说明书没提的，看这里。", ""]
    total = 0
    cur = None
    for n in ordered:
        p = scripts / f"{n}.py"
        stage = STAGE.get(n, "")
        if stage != cur:
            lines += [f"## {stage or '其他'}", ""]
            cur = stage
        args = args_of(p)
        total += len(args)
        lines.append(f"### `{n}.py`")
        d = docstring(p)
        if d:
            lines.append(f"*{d}*")
            lines.append("")
        if not args:
            lines += ["（无参数）", ""]
            continue
        lines += ["| 参数 | 说明 |", "|---|---|"]
        for flag, _, note in args:
            lines.append(f"| `{flag}` | {note or '—'} |")
        lines.append("")

    out = Path(a.out) if a.out else root / "references" / "cli.md"
    out.write_text("\n".join(lines))
    print(f"→ {out}（{len(ordered)} 个脚本 / {total} 个参数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
