# PROTOCOL · 多工位分支、端口与共享资源协议

> 一句话：**每个 AI 工位一套分支前缀 + 一段端口 + 一份项目副本；共享依赖区只读，真值源文件谁认领谁写。**

---

## 一、工位表

| 工位 | 分支前缀 | 端口区间（预留） | 项目副本后缀 | 备注 |
|---|---|---|---|---|
| Cola | `ai/cola/` | 7801–7809 | `-cola` | 主工位 |
| Claude Code | `ai/claude/` | 7811–7819 | `-claude` | |
| Codex | `ai/codex/` | 7821–7829 | `-codex` | |
| Cursor | `ai/cursor/` | 7831–7839 | `-cursor` | |
| Gemini CLI | `ai/gemini/` | 7841–7849 | `-gemini` | |
| DeepSeek | `ai/deepseek/` | 7851–7859 | `-deepseek` | |
| 人（维护者） | 直接提 `master` 或走任一工位 | — | — | 拍板与最终判收 |

新工位接入 = 在表里加一行（分支前缀 + 端口 + 副本后缀），**不占别人的号段**。
需要启动 HyperFrames Studio 工程页时，优先走 `scripts/studio.py` 或在自己号段内指定 `--port`。

---

## 二、分支与任务号

```
ai/<工具名>/<任务号>          例：ai/cola/T-101
```

- 任务号在 `docs/ai-collab/TASKS.md` 里顺序分配，**只增不复用**。
- 分支从 `master` 开，一个分支只干一个任务；判收合并后删除分支与临时 worktree。
- 提交信息第一行写 `<任务号> <动词> <对象>`。
- 顺手发现的无关问题：**不要顺手改**，在 `TASKS.md` 新挂一行任务。
- **共享状态文件不走分支**：`TASKS.md` / `HANDOFF.md` 的状态行由认领人在 `master` 直接更新，避免多工位看到过期状态。

---

## 三、并发与真值源文件

**本仓库无数据库，真值源全是文件：**

| 真值源 | 位置 | 在 git 里吗 |
|---|---|---|
| 素材索引 | `<素材库>/_cutskills/index.json` | ✗（外部目录） |
| 上游选题与配音 | `<项目>/ideation.json`、`voiceover.json` | ✗ |
| 项目时间轴（v5） | `<项目>/timeline.json` | ✗ |
| 需求与拍板锁 | `<项目>/brief.json`、`approval.json`、`lock.json` | ✗ |
| 剪辑骨架 / 包装包 | 仓库内 `plans/*.yaml`、`packs/*.yaml` | ✓ |
| 内置 CC0 音频包 | 仓库内 `assets/audio/{bgm,sfx}` | ✓ |
| 渲染产物 | `<项目>/out/`、`<项目>/hf/` | ✗（可重建，收工跑 `tidy.py`） |

1. **同一项目同一时刻只允许一个工位写。** 两个工位要改同一支片子 → 各自复制 `<slug>-<tool>` 副本。
2. **共享只读区**：`~/.cutskills/`（或 `$CUTSKILLS_HOME`）由 `bash scripts/runtime.sh install` 统一安装维护，严禁手删手改。

---

## 四、合并前必须跑通的完整门禁

```bash
cd <仓库根>
source scripts/runtime.sh env

python3 scripts/gen_cli_ref.py       # 动过脚本 CLI 参数时先重生成 references/cli.md
python3 scripts/audit_skill.py       # 文档 × CLI × Schema 双向对账（必须 🔴0 🟡0）
python3 scripts/selftest.py          # V2 端到端真渲染 + 31 支契约与回归单测（必须 exit 0）
python3 scripts/pack_dist.py         # 分发包完整性与零私有路径泄漏自检（必须 exit 0）

# 若动到具体成片项目，额外跑该项目的全套门禁：
P="<素材库>/_cutskills/projects/<slug>"
python3 scripts/gate.py        --project "$P"
python3 scripts/verify_film.py --project "$P"
python3 scripts/check_sfx.py   --project "$P"
```

---

## 五、四条铁律（AGENTS.md 同款，一字不差）

1. **谁的活谁开**：每个工位只在自己的 `ai/<工具名>/<任务号>` 分支或独立 worktree 上改动，不碰别人的在跑分支。
2. **验收归别人**：自己的口头总结不算验收；必须贴出门禁命令真实退出码与读数，由另一工位或人复核判收。
3. **改配置与脚本必须同改主干说明书**：动了 CLI 参数、Schema 或运行环境，同一次提交必须跑 `gen_cli_ref.py` 并保证 `audit_skill.py` 零红零黄。
4. **严禁为过校验放宽断言**：红灯就是真问题——只许修代码或修素材切点，绝不许删测试、降阈值、写假核销。
