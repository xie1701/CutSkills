#!/usr/bin/env python3
"""把每个脚本的 argparse 扫一遍，生成 `references/cli.md`。

为什么要有这个：说明书讲的是「为什么这样剪」，装不下所有脚本的全部开关；
不写进说明书，别人就永远不会知道 `search_library --for-cut` 能把「这段口播该配什么
画面」反查出来——功能存在但等于不存在。规模不写死，按实际扫到的脚本/参数现算
（每跑一次就打印在末尾，也写在 cli.md 的抬头里）。

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
    "lexicon", "lint_transcript", "cue_guard",
    "init_project", "brief", "style_menu", "search_library", "pick_take", "compare_ref",
    "audio_map", "skeleton", "hooks", "timeline", "plan_card", "lock",
    "check_timeline", "content_guard", "evidence_guard", "speech_hygiene", "edge_audit",
    "render_hf", "gen_scene", "preview_card", "stickers",
    "gate", "verify_film", "platform_safe", "check_subs", "check_cards", "check_overlay",
    "check_keyword_text", "check_timing", "check_sfx", "check_packs", "sync_check", "review_sheet",
    "showcase", "studio", "tidy", "content_log", "review_week",
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
    "cue_guard": "2 · 素材多模态索引",
    "init_project": "3 · 编导、卡点与方案拍板",
    "brief": "3 · 编导、卡点与方案拍板",
    "style_menu": "3 · 编导、卡点与方案拍板",
    "search_library": "3 · 编导、卡点与方案拍板",
    "pick_take": "3 · 编导、卡点与方案拍板",
    "compare_ref": "3 · 编导、卡点与方案拍板",
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
    "platform_safe": "5 · 质量门禁与交付清理",
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
    "content_log": "5 · 质量门禁与交付清理",
    "review_week": "5 · 质量门禁与交付清理",
    "audit_skill": "★ 仓库自检与单测",
    "selftest": "★ 仓库自检与单测",
    "test_v2_pipeline": "★ 仓库自检与单测",
}

ARG = None
LONG = None

# 章节的固定顺序。STAGE 里的值就是这几节的名字，一节只能出现一次、且按这个顺序；
# 不在 STAGE 里的脚本（单测等）归到最后的「其他」。早先按「ORDER 外按字母接在后面」
# 拼章节，于是 gaps / log_decision 这种「在 STAGE、不在 ORDER」的脚本会在末尾另起一节，
# 把「## 其他」切成四截，章节也跟着错序（##4 跑到 ##5 后面）。
STAGE_ORDER = [
    "0 · 环境与跨机分发",
    "1 · 上游选题、脚本与小白拍片卡",
    "2 · 素材多模态索引",
    "3 · 编导、卡点与方案拍板",
    "4 · 网感包装与成片渲染",
    "5 · 质量门禁与交付清理",
    "★ 仓库自检与单测",
]


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
    rank = {n: i for i, n in enumerate(ORDER)}

    # 按 STAGE_ORDER 分节：节内按 ORDER（流水线顺序）排，ORDER 外的按字母排在节尾
    sections: list[tuple[str, list[str]]] = []
    for stage in STAGE_ORDER:
        group = [n for n in names if STAGE.get(n) == stage]
        group.sort(key=lambda n: (rank.get(n, len(ORDER)), n))
        if group:
            sections.append((stage, group))
    rest = sorted(n for n in names if not STAGE.get(n))
    if rest:
        sections.append(("其他", rest))

    lines = ["# 全部命令与参数", "",
             "> 这份文件由 `scripts/gen_cli_ref.py` 从脚本的 `argparse` 生成，**不要手改**。",
             "> 改了参数就重跑：`python3 scripts/gen_cli_ref.py`。", "",
             "说明书讲的是「为什么这样剪」，这里只有「有什么开关」。查它能干的、说明书没提的，看这里。", ""]
    total = 0
    n_scripts = 0
    for stage, group in sections:
        lines += [f"## {stage}", ""]
        for n in group:
            p = scripts / f"{n}.py"
            n_scripts += 1
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
    lines.insert(4, f"> 当前 {n_scripts} 个脚本 / {total} 个参数（按代码现算）。")

    out = Path(a.out) if a.out else root / "references" / "cli.md"
    out.write_text("\n".join(lines))
    print(f"→ {out}（{n_scripts} 个脚本 / {total} 个参数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
