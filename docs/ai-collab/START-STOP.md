# 开工 / 收工 · 各一句口令

两个口令，都是一句话。**没听到口令就不动业务代码**（读文档、跑校验随时可以）。

---

## 开工 · 口令「开工」

听到「开工」，按顺序做完再写第一行业务代码：

1. **读三份**：`AGENTS.md` → `docs/ai-collab/PROTOCOL.md` → `docs/ai-collab/{HANDOFF,TASKS}.md`。
2. **认工位**：确认自己的分支前缀 `ai/<工具名>/`、端口号段与项目副本后缀。
3. **领任务**：从 `TASKS.md` 的「未开工」里领单，**不许抢 `在跑` 的单**。
4. **开分支**：`git switch master` → `git switch -c ai/<工具名>/T-xxx`（或建独立 `git worktree`）。
5. **记基线**：先跑 `source scripts/runtime.sh env && python3 scripts/audit_skill.py && python3 scripts/selftest.py`，记录当前读数。
6. **开工回执一句话**：`T-xxx 已开跑｜分支 ai/<工具>/T-xxx｜基线 selftest 0 / audit 🔴0🟡0`。

> 铁律 ④ 从这一刻生效：红了就是红了，不许为了绿灯放宽断言、改低阈值或跳过用例。

---

## 收工 · 口令「收工」

听到「收工」，按顺序收完才算收工：

1. **跑完整门禁**：`gen_cli_ref.py` → `audit_skill.py` → `selftest.py` →（如涉分发）`pack_dist.py`。
2. **清理过程残骸**：跑 `python3 scripts/tidy.py --tmp --apply`（动过成片项目再加 `--project <项目> --apply`）。
3. **写留痕**：在 `TASKS.md` 写明「改了什么 + 门禁读数」，状态改 `待复核`。
4. **更交接**：更新 `HANDOFF.md`（分支、SHA、改动摘要、复核命令）。
5. **提交**：`git add -A && git commit -m "<任务号> <一句话>"`。
6. **交接回执一句话**：`T-xxx 收工｜分支 ai/<工具>/T-xxx @ <sha>｜selftest 0 / audit 🔴0🟡0｜待复核`。
