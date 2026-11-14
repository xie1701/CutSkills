# AGENTS.md · 多工位协作的四条铁律

本仓库支持多个 AI 工具协作开发（Cola / Claude Code / Codex / Cursor / Gemini 等），人类用户只在关键节点拍板；以下四条是硬性准入条件，违反一律打回。

## ① 谁的活谁开
- 别人名下的分支（`ai/<别人>/T-xxx`）与任务，一律不越界修改；接手先向对方确认交接原因、进度与已知问题。一个任务 = 一个分支 = 一个工位。

## ② 验收归别人
- 施工工位自测只能推到 **待复核**，不能自己推到 **判收**；复核方必须**亲自重跑验证命令与原像素测量**，不轻信口头总结。

## ③ 改配置与脚本必须同步主干契约

| 层级 | 命令 |
|---|---|
| 端到端渲染与回归总自检 | `python3 scripts/selftest.py` |
| 说明书与全仓代码对账 | `python3 scripts/audit_skill.py` |
| 项目级渲前/渲后总门禁 | `python3 scripts/gate.py --project <项目>` + `python3 scripts/verify_film.py --project <项目>` |
| 跨机可移植打包自检 | `python3 scripts/pack_dist.py` |

配置面：`scripts/runtime.sh`、`deps.json`、`config.example.json`、`.gitignore`、`references/cli.md`（自动生成物）。参数变动重跑 `gen_cli_ref.py`；脚本增删或行为改变同步 `SKILL.md` / `GETTING-STARTED.md`，并保持 `audit_skill.py` 为 **🔴 0 · 🟡 0**。

## ④ 不许为了让校验通过去改弱产品行为、放宽断言、跳过用例

1. 严禁随意调松阈值、删断言、skip 用例，或把 `--force` 当常规手段换取绿灯；
2. 严禁为了迎合判据而伤害成片观感（例如把字号缩到看不清）；判据本身有缺陷，须立专项修正并附正负样本回归测试。
