# AGENTS.md · 多工位协作的四条铁律

本仓库支持多个 AI 工具协作开发与维护（Cola / Claude Code / Codex / Cursor / Gemini 等），人类用户只在关键节点拍板。
以下四条是硬性准入条件，违反的改动一律打回。

---

## ① 谁的活谁开

- 别人名下的分支（`ai/<别人>/T-xxx`）、`docs/ai-collab/TASKS.md` 里挂在别人名下的任务，一律不越界修改。
- 接手需先在 `docs/ai-collab/HANDOFF.md` 写明交接原因、进度与已知问题。
- 一个任务 = 一个分支 = 一个工位。

## ② 验收归别人

- 施工工位自测只能推到 **待复核**，不能自己推到 **判收**。
- 复核方必须**亲自重跑验证命令与原像素测量**，不轻信口头总结。

## ③ 改配置与脚本必须同步主干契约

本仓库的自动化门禁由自身校验脚本承担：

| 层级 | 命令 |
|---|---|
| 端到端渲染与回归总自检 | `python3 scripts/selftest.py` |
| 说明书与全仓代码对账 | `python3 scripts/audit_skill.py` |
| 项目级渲前/渲后总门禁 | `python3 scripts/gate.py --project <项目>` + `python3 scripts/verify_film.py --project <项目>` |
| 跨机可移植打包自检 | `python3 scripts/pack_dist.py` |

配置面：`scripts/runtime.sh`、`deps.json`、`config.example.json`、`.gitignore`、`references/cli.md`（自动生成物）。

- 脚本参数变动 → 必须重跑 `python3 scripts/gen_cli_ref.py` 刷新 `references/cli.md`（严禁手改）；
- 脚本增删或行为改变 → 必须同步更新 `SKILL.md` / `GETTING-STARTED.md`，并确保 `audit_skill.py` 为 **🔴 0 · 🟡 0**。

## ④ 不许为了让校验通过去改弱产品行为、放宽断言、跳过用例

1. 严禁随意调松阈值、删断言、skip 用例，或把 `--force` 当常规手段换取绿灯；
2. 严禁为了迎合判据而伤害成片观感（例如把字号缩到看不清）；
3. 若判据本身有缺陷，须立专项修正并附上正负样本回归测试。
