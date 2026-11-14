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
ORDER = [
    "runtime", "doctor", "pack_dist", "gen_cli_ref", "audit_skill", "selftest", "test_v2_pipeline",
    "ideate", "voiceover", "beat_sync",
    "index_assets", "media_spec", "render_library_index", "rename_assets",
    "lexicon", "lint_transcript", "check_l2", "cue_guard",
    "init_project", "brief", "style_menu", "search_library", "build_takes_packed", "pick_take",
    "audio_map", "skeleton", "hooks", "timeline", "plan_card", "lock",
    "check_timeline", "content_guard", "evidence_guard", "speech_hygiene", "edge_audit",
    "render_hf", "gen_scene", "preview_card", "stickers",
    "gate", "verify_film", "check_subs", "check_cards", "check_overlay", "check_keyword_text",
    "check_timing", "check_sfx", "check_packs", "sync_check", "review_sheet",
    "showcase", "studio", "tidy",
]

STAGE = {
    "runtime": "0 · 环境与跨机分发",
    "doctor": "0 · 环境与跨机分发",
    "pack_dist": "0 · 环境与跨机分发",
    "ideate": "1 · 上游选题、脚本与小白拍片卡",
    "voiceover": "1 · 上游选题、脚本与小白拍片卡",
    "index_assets": "2 · 素材多模态索引",
    "media_spec": "2 · 素材多模态索引",
    "render_library_index": "2 · 素材多模态索引",
    "rename_assets": "2 · 素材多模态索引",
    "lexicon": "2 · 素材多模态索引",
    "lint_transcript": "2 · 素材多模态索引",
    "check_l2": "2 · 素材多模态索引",
    "cue_guard": "2 · 素材多模态索引",
    "init_project": "3 · 编导、卡点与方案拍板",
    "brief": "3 · 编导、卡点与方案拍板",
    "style_menu": "3 · 编导、卡点与方案拍板",
    "search_library": "3 · 编导、卡点与方案拍板",
    "build_takes_packed": "3 · 编导、卡点与方案拍板",
    "pick_take": "3 · 编导、卡点与方案拍板",
    "audio_map": "3 · 编导、卡点与方案拍板",
    "beat_sync": "3 · 编导、卡点与方案拍板",
    "skeleton": "3 · 编导、卡点与方案拍板",
    "hooks": "3 · 编导、卡点与方案拍板",
    "timeline": "3 · 编导、卡点与方案拍板",
    "plan_card": "3 · 编导、卡点与方案拍板",
    "lock": "3 · 编导、卡点与方案拍板",
    "gaps": "3 · 编导、卡点与方案拍板",
    "log_decision": "3 · 编导、卡点与方案拍板",
    "render_hf": "4 · 网感包装与成片渲染",
    "stickers": "4 · 网感包装与成片渲染",
    "gen_scene": "4 · 网感包装与成片渲染",
    "preview_card": "4 · 网感包装与成片渲染",
    "gate": "5 · 质量门禁与交付清理",
    "verify_film": "5 · 质量门禁与交付清理",
    "check_timeline": "5 · 质量门禁与交付清理",
    "content_guard": "5 · 质量门禁与交付清理",
    "evidence_guard": "5 · 质量门禁与交付清理",
    "speech_hygiene": "5 · 质量门禁与交付清理",
    "edge_audit": "5 · 质量门禁与交付清理",
    "check_subs": "5 · 质量门禁与交付清理",
    "check_cards": "5 · 质量门禁与交付清理",
    "check_overlay": "5 · 质量门禁与交付清理",
    "check_keyword_text": "5 · 质量门禁与交付清理",
    "check_timing": "5 · 质量门禁与交付清理",
    "check_sfx": "5 · 质量门禁与交付清理",
    "check_packs": "5 · 质量门禁与交付清理",
    "sync_check": "5 · 质量门禁与交付清理",
    "review_sheet": "5 · 质量门禁与交付清理",
    "showcase": "5 · 质量门禁与交付清理",
    "studio": "5 · 质量门禁与交付清理",
    "tidy": "5 · 质量门禁与交付清理",
    "audit_skill": "★ 仓库自检与单测",
    "selftest": "★ 仓库自检与单测",
    "test_v2_pipeline": "★ 仓库自检与单测",
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
