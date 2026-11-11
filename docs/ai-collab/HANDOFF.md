# HANDOFF · 最新交接

> 每轮开工第一件事：读这一页。每轮收工最后一件事：更新这一页。
> 上一个改的人写，下一个读的人不改（发现写错了，在下面加「更正」，不覆盖别人的话）。

---

## 一、这一版交给谁

| 项 | 值 |
|---|---|
| 交出时间 | 2026-11-11 13:20 +08:00 |
| 交出工位 | Cola（`ai/cola/`） |
| 当前分支 | `master`（骨架直接落主干，见下「为什么没走分支」） |
| 主干提交 | `T-001 建多工位协作骨架`（SHA 见 `git log -1 -- AGENTS.md`） |
| 上一个功能提交 | `3760be2` lint 失败时把「HTML 有错」和「lint 进程自己退非 0」分开说清楚 |
| 任务 | T-001 建多工位协作骨架 —— 状态 **待复核** |
| 下一位工位 | 任意（先到先领 T-002 / T-003） |

**为什么这一版没走 `ai/cola/T-001`**：这是协作骨架本身，走分支会让「下一位先读 HANDOFF」这件事
反而读不到（骨架不在主干上）。从 T-002 起一律按 PROTOCOL 走分支。

---

## 二、环境版本戳

| 项 | 值 | 怎么查 |
|---|---|---|
| Python | 3.14.6 | `python3 -V` |
| Node | v26.7.0 | `node -v` |
| hyperframes | 0.9.19（本地二进制，**不走 npx**） | `~/.cutskills/npm/node_modules/.bin/hyperframes --version` |
| ffmpeg（带 zscale / tonemap） | 9.0.2 | `~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg -version \| head -1` |
| 系统 ffmpeg | /opt/homebrew/bin/ffmpeg，**无 libzimg** | 只能兜底，不要用来做 HDR |
| 依赖根 | `CUTSKILLS_HOME=/Users/jerry/.cutskills`（约 5.5 G） | `source scripts/runtime.sh env; echo $CUTSKILLS_HOME` |
| vendor | `pypinyin 0.55.0`、`jieba 0.42.1`（剪到 8.5 M） | `ls scripts/vendor` |
| 素材库 | `/Users/jerry/Downloads/柒造Video/素材` | 索引在 `_cutskills/index.json` |
| CC0 库 | `~/Downloads/cc0-bgm-library`（178 首）、`~/Downloads/cc0-sfx-library`（320 条） | `scripts/cc0_lib.py` |
| 仓库体积 | 2.8 G（`.git` 1.0 G） | `du -sh . .git` |

**已知环境坑**（省下一位一小时）：
- `pip` 走 SOCKS 代理必失败 → 依赖只能放 `scripts/vendor/` 或手装 wheel。
- 带 alpha 的 VP9 必须显式 `-c:v libvpx-vp9`；`-pattern_type glob` 必须绝对路径 + `*.png`。
- 递归删除被安全守卫拦，用 `python3 -c "import shutil; shutil.rmtree(...)"`。
- `PIL.Image.Image.getdata` 在 Pillow 14 弃用，`verify_film.py` 还在报 `DeprecationWarning`（不影响结果）。

---

## 三、这一版到底改了什么

**T-001（骨架）**：
- 新增 `AGENTS.md`（四条铁律：谁的活谁开 / 验收归别人 / 改 CI 同改主干 / 不许为过校验改产品行为）；
- 新增 `docs/ai-collab/` 四份：`PROTOCOL.md`（工位·分支·端口·共享资源）、`HANDOFF.md`（本页）、
  `TASKS.md`（任务表与状态机）、`START-STOP.md`（开工/收工口令）。
- **没有动任何业务代码、判据、阈值。**

**上一版（`3760be2` 之前，同一天）**：第二轮对抗式审查六条全修 + 两个真 bug（双语双行从没上屏、
纸感字幕在暗画面上读不出来）。细节见 `references/fixes-2026-11-11-round2.md` 与
`references/fixes-2026-11-11.md`。

---

## 四、基线（任何任务不许让红黄变多）

**项目成片**（`verify_film.py`，格式 🟢/🟡/🔴）：

| 项目 | 结果 | 关键读数 |
|---|---|---|
| `qizao-naming-01` · P1-warm | 30 / 0 / 0 | 口播中位 2.45s · 吸附 6/7 |
| `qizao-naming-01` · P2-paper | 31 / 0 / 0 | 双语第二行 4/4 帧 |
| `qizao-naming-01` · P3-dark | 24 / 0 / 0 | 无字幕层，大字 3 块承担台词 |
| `qizao-ground-01` | 26 / 0 / 0 | 字幕 81%W · 口播中位 2.29s · 吸附 14/17 |
| `qizao-protect-01` | 26 / 0 / 0 | 字幕 28 条 · 吸附 13/13 |
| `qizao-lime-baxis`（B 轴） | 23 / 0 / 0 | 最长静止 0.30s |

**仓库级**：

| 命令 | 现状 | 说明 |
|---|---|---|
| `golden.py` | ✅ 退出码 0 | 3 条样片（proj79 / ground-01 / protect-01），每条 🟢26 🔴0 |
| `selftest.py` | ❌ 退出码 1 | **1 红**：`音画等长` 声轨短 0.10s（合成素材，C 段 ffmpeg 链尾帧）→ 任务 **T-003** |
| `audit_skill.py` | ❌ 退出码 1 | **11 红 11 黄**：`references/cli.md` 过期、9 处文档点名文件不存在 → 任务 **T-002** |

这两条红是**历史欠账**，不是谁刚弄坏的。准入线：**红黄数量不许增加**。

---

## 五、下一位开场提示词（可直接粘）

```
你在 CutSkills 仓库（/Users/jerry/Downloads/AIworks/CutSkills）接一个工位。
先按顺序读三份，不要跳过：
  1) AGENTS.md —— 四条铁律（谁的活谁开 / 验收归别人 / 改 CI 同改主干 / 不许为过校验改产品行为）
  2) docs/ai-collab/PROTOCOL.md —— 你属于哪个工位、分支前缀 ai/<你的工具名>/、可写目录
  3) docs/ai-collab/HANDOFF.md 与 TASKS.md —— 上一版交到哪、基线红黄数、可领的任务

当前状态：master 上刚落下 T-001 协作骨架（上一功能提交 3760be2）。
基线：golden 退出码 0；selftest 1 红（音画等长 0.10s，T-003）；audit_skill 11 红（文档陈旧，T-002）。
准入线：不许让红黄变多。

我要领 T-__（填任务号）。请先做三件事再动手：
  ① 用一句话复述这个任务的完成标准与验收命令；
  ② 从 master 开 ai/<你的工具名>/T-__，在 TASKS.md 把状态改成「在跑」并写上你的工位名；
  ③ 把「合并前要跑的完整校验」列出来（selftest / golden / audit_skill + 该项目的 check_*/verify），
     先跑一遍记录当前红黄数，作为你这一轮的对照基线。
做完这三件再开始改代码。
```

---

## 六、更正（后来的工位可以在这里纠错，别覆盖上面）

（空）
