# PROTOCOL · 工位、分支、端口、共享资源

> 一句话：**每个工位一套分支前缀 + 一段端口 + 一份项目副本；共享的东西只读，要写就走同一个入口。**

---

## 一、工位表

| 工位 | 分支前缀 | 端口区间（预留） | 项目副本后缀 | 备注 |
|---|---|---|---|---|
| Cola | `ai/cola/` | 7801–7809 | `-cola` | 当前主要工位 |
| Claude Code | `ai/claude/` | 7811–7819 | `-claude` | |
| Codex | `ai/codex/` | 7821–7829 | `-codex` | |
| Cursor | `ai/cursor/` | 7831–7839 | `-cursor` | |
| Gemini CLI | `ai/gemini/` | 7841–7849 | `-gemini` | |
| DeepSeek | `ai/deepseek/` | 7851–7859 | `-deepseek` | 2026-11-11 接入（首个任务 T-010） |
| 人（Jerry） | 直接提 master 或走任一工位 | — | — | 只做拍板与判收 |

新工位接入 = 在表里加一行（分支前缀 + 端口 + 副本后缀），**不占别人的号段**。

**端口说明**：当前链路**不监听任何端口**（渲染是本地进程，`hyperframes render` 跑完即退）。
端口区间是给将来本地服务（预览页、看板编辑服务、MCP 服务）预留的，谁要用就从自己号段里取，
不许借用别人的号段。要用端口必须在 `HANDOFF.md` 里登记「工位 / 端口 / 用途」。

---

## 二、分支与任务号

```
ai/<工具名>/<任务号>          例：ai/cola/T-004
```

- 任务号从 `T-001` 起，**只增不复用**；号在 `TASKS.md` 里分配，不许自造。
- 分支从 `master` 开，一个分支只干一个任务；干完合并后删除分支。
- 提交信息第一行写 `<任务号> <动词> <对象>`，例：`T-004 加 20 支阴性样本防误杀`。
- 一个任务里顺手发现的相关问题：**不要顺手改**，在 `TASKS.md` 新开一个任务挂上去。
- **共享状态文件不走分支**：`TASKS.md` / `HANDOFF.md` 的状态行（在跑 / 待复核 / 判收 / 打回 + 留痕）由认领人**直接提交到 master**，不等判收合并——否则状态要等合并那一刻才可见，其它工位看到的永远是旧值（2026-11-11 T-005 实撞：Cola 已推待复核，主目录切到别的分支后看回去还是「在跑」，被误认为被改回去）。分支里只带业务代码与该任务自己的文档；合并时这两份文件按「谁的行谁写」逐行解，以 master 为准。

---

## 三、并发与「数据库」

**本仓库没有数据库。** 真值源全是文件：

| 真值源 | 位置 | 在 git 里吗 |
|---|---|---|
| 素材索引 | `<素材库>/_cutskills/index.json` | ✗（外部目录） |
| 项目时间轴 | `<素材库>/_cutskills/projects/<slug>/timeline.json` | ✗ |
| 需求卡 | 同上 `brief.json` | ✗ |
| 包装 / 骨架 | 仓库内 `packs/*.yaml`、`plans/*.yaml` | ✓ |
| 方案拍板锁 | `<slug>/lock.json`（`scripts/lock.py`） | ✗ |
| 渲染产物 | `<slug>/out/`、`<slug>/hf/`、`<slug>/work/` | ✗（可重建） |

**有并发才分库 → 现在没有库，所以分的是「写权」：**

1. **同一个项目同一时刻只允许一个工位写。** 这条靠三样东西守：项目副本后缀、`lock.json`、
   `TASKS.md` 里「在跑」的行。两个工位要动同一个片子 → 各用一份副本（`<slug>-<tool>`），
   并在 `TASKS.md` 写明「这是副本，不动原片」。
2. **共享只读区**：`~/.cutskills/`（依赖 + npm 缓存 + 工具的 bin）。**只允许**
   `source scripts/runtime.sh install` 写，谁都不许手删手改（5.5 G，重装一次很贵）。
3. **可以放心共享的缓存**：`<素材库>/_cutskills/cache/asr/`（按素材 uid 分块命名，幂等）、
   `~/.cutskills/npm-cache`（npm 自己的锁会打架，撞上就重试，不要删目录）。
4. **一定会打架的**：`<slug>/hf/clips/`（片段缓存，按 `.grade` 指纹命名，但同一目录两进程
   同时写会互相覆盖）、`<slug>/out/film-hf.mp4`。→ 这就是第 1 条的原因。
5. **将来接库**（SQLite / 服务端）：按工位分文件或分 schema，`<workstation>.db`，
   共享表只允许「追加 + 读取」，改历史记录必须走一个工位的迁移脚本。

---

## 四、一轮完整流程（每个任务都走这条）

```
读 HANDOFF.md / TASKS.md
  → 从 master 开 ai/<tool>/T-xxx
  → 领任务（TASKS.md 状态改「在跑」，写「谁、哪台、几点」）
  → 干活
  → 跑完整校验（见下）
  → TASKS.md 写一行「改了什么 + 拿什么验的」
  → 状态改「待复核」，推分支，更新 HANDOFF.md
  → 另一个工位/人重跑命令复核 → 判收 或 打回
  → 判收后合并 master，删分支，TASKS.md 状态改「判收（已完成）」
```

### 合并前必须跑通的完整校验

```bash
cd <仓库根>
source scripts/runtime.sh env        # 让 ffmpeg(zscale)/hyperframes 走本地，不联网

python3 scripts/selftest.py          # 链路没断（合成素材，约 45s）
python3 scripts/golden.py            # 旧样片没退化（3 条真片，退出码必须 0）
python3 scripts/audit_skill.py       # 文档与代码对账（退出码必须 0）

# 动到哪条片子，就跑那条片子的全套：
P="<素材库>/_cutskills/projects/<slug>"
python3 scripts/check_timeline.py --project "$P"
python3 scripts/check_subs.py    --project "$P"
python3 scripts/check_packs.py   --project "$P"
python3 scripts/verify_film.py   --project "$P"
```

**当前基线不是全绿**（见 `HANDOFF.md` 的「基线」一节）：
`selftest` 1 红、`audit_skill` 11 红，都是历史欠账、已挂成任务。
准入线是「**不许让红黄变多**」，不是「必须变全绿」——想变全绿要单开任务，按任务的方式修。

### 交接义务

- **每次合并都要更新 `HANDOFF.md`**：分支、提交 SHA、环境版本戳、这一版改了什么、下一位开场提示词。
- 没合并但一轮结束（被打回、被卡住、跨天）：也要更新 `HANDOFF.md`，写清「卡在哪、下一步」。
- 谁的活谁写；不许替别人写交接内容。

---

## 五、红线（重复一遍，因为最容易破）

1. 自己开的工位自己开分支；别人的分支不碰。
2. 自己的报告不算验收。
3. 改脚本/判据/配置 → 同一次合并里改文档、重生成 `cli.md`、校准基线。
4. 不许为了绿灯改产品行为、放宽断言、跳过用例。
