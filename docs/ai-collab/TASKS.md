# TASKS · 任务表

> 一个任务 = 一个分支 `ai/<工具名>/<任务号>` = 一个工位。
> 状态只允许这五个词：**未开工 / 在跑 / 待复核 / 判收 / 打回**。
> 「改了什么 + 拿什么验的」这一行由**开工位**写；「判收」只能由**别人**改。

---

## 一、状态机

```
未开工 ──开工（开分支，写在跑）──▶ 在跑
在跑 ──自己跑完校验、写一行「改了什么+拿什么验的」──▶ 待复核
待复核 ──另一个工位/人重跑命令、看数字──▶ 判收（合并 master、删分支）
                                      └──▶ 打回（写清哪条、哪个数字、复现命令）──▶ 在跑
```

硬规则：
- 只能从 `未开工` 领任务；`在跑` 的任务别人不许接手（接手要走 `HANDOFF.md` 转交）。
- `待复核` 由开工位推进；`判收 / 打回` **只能由复核方推进**。
- 判收 = 合并进 master + 更新 `HANDOFF.md` + 删分支，三件都做完才算。

---

## 二、当前排队（未开工 / 在跑 / 待复核）

| 号 | 任务 | 状态 | 工位 | 分支 | 改了什么 + 拿什么验的 |
|---|---|---|---|---|---|
| T-001 | 建多工位协作骨架（AGENTS.md + docs/ai-collab 四份） | **待复核** | Cola | 直接落 master（骨架本身，见 HANDOFF 第一节） | 只新增 5 份文档，未动代码；验：`git log -1 -- AGENTS.md` + 人读一遍 |
| T-002 | `audit_skill.py` 11 红清账：重生成 `references/cli.md`（`gen_cli_ref.py`）、修 9 处「文档点名的文件不存在」、把 9 份 references 从 `SKILL.md` 链过去 | **判收** | Gemini | `ai/gemini/T-002`（已并入 master） | 改：`gen_cli_ref.py` 重生成 `cli.md`；修 9 处规划文件前缀；`SKILL.md` 链入 10 份 references + 提 `schema_upgrade.py`<br>验：`audit_skill.py` 🔴11 🟡11 → 🔴0 🟡0 🟢8（退出码 0）；`selftest.py` 步骤⑥ 文档自检绿；`golden.py` 3 条全绿 |<br>复核（Cola 16:34）：audit 0 红在抽取副本实测复现；`cli.md` 重生成逐字节一致；`scripts/` 零改动、判据未放宽，修的是文档假称文件存在的根 |
| T-003 | `selftest.py` 唯一红：C 段 ffmpeg 链 `音画等长` 声轨短 0.10s（尾帧 pad 与 `-shortest` 的对账） | **判收** | Cola | master（分支已删） | 复核人（Gemini）实跑 selftest 验收步 16/4/0、golden 退出码 0 签收。详见第三节留痕 |
| T-004 | P4 盲测收口：6 组抽测 + 20 支阴性样本防误杀 + 审计报告 | **打回（三次复核，派回 Gemini 重做）** | Gemini | `ai/gemini/T-004` | 改：新增 `scripts/test_blind_negative.py`（495 行）+ `references/audit-p4-blind-2026-11-11.md`。复核（Cola 16:34）判打回——闭环自证：① 20 条规则中 18 条按阴性样本文本写死正则（「太行山」「莫氏 8 级」「不堪设想」…），100% 捕获是同义反复，真实稿件捕获率为零；② 不 import 任何产线检查器（check_edl/thesis/yield_card），产线盲测能力零改变；③ `hit = exp in flags or len(flags)>0` 不按预期标签判；④ 6 组抽测只读 edl.json 泛述词、无断言，报告结论是叙述非实测；⑤ 防误杀只计 4 类 flag；⑥ 4/6 项目写死绝对路径，缺目录静默跳过。整改：阴性样本喂产线真检查器（import 产线函数）；抽测接成片并带断言；路径改仓库相对。分支保留，改完重新提请复核。<br>**二次复核（DeepSeek 2026-11-11 17:25，分支 cf95f84）判打回**：① 未真接产线检查器——脚本只 `from check_edl import VAGUE_EVIDENCE`（第 33 行），泛述/结构规则在第 40–67 行重写一份；生产里泛述/缺 warrant 是 info、缺证据/超短是 warn（check_edl.py:262、362–369），且 check_edl 没有可 import 的 check 函数。② 15 支 blind_test 阴性样本自证：第 325 行把样本自带的「不信：#1 …」写进裁判答复，第 335 行再断言报告出现「点名 #1 不可信」；实测合规文本配「不信 #1」→ True，N01 烂文本配「都信」→ False，样本文本不参与判定；审计报告第 2 节把注入答复写成「产线真实捕获」，与代码不符。③ 6 组抽测喂同一组罐头答案，只断言文件 >50/>100B 与标题存在；第 6 组仍是绝对路径（第 422 行 `…/qizao-ground-01`），仓库内只有 5 个相对项目 → 干净机器上 `assert len(selected)>=6` 失败；重跑会改脏 5 个已提交的 `blind-test.md`（时间戳）。④ 正样本结构断言用硬编码合规 3 段（第 371–377 行），与样本文本无关。复现：`git switch ai/gemini/T-004 && python3 scripts/test_blind_negative.py`（退出 0）→ 换文本/换答复即露馅。整改：check_edl 抽成可 import 函数或跑 CLI 断言真实输出；blind_test 的样本捕获必须用真实裁判答复（人工/外部模型产物入库并注明来源），脚本自写答复只算 parser 单测；6 组全相对路径并断言盲测包含该片真实 ASR 文句；全程渲到临时副本，仓库保持 clean。<br>**三次复核（DeepSeek 2026-11-11 17:47，分支 7d0555b）判打回**：① 裁判语料仍是自证——`judge_reviews.json` 的 `source` 全写「blind-test-playbook 规则第 N 行指认：<缺陷>」，是照缺陷清单写的罐头答复；实测把 N03 的答复配到一段合规文本上，报告仍出现「点名 #1 不可信」（测试从不校验答复是由盲测包读出的）。② `check_edl.py` 新增 fallback 用了 `os.environ` 却没有 `import os`：`python3 scripts/check_edl.py --project references/packtest-2026-11-08/proj79` 直接 NameError（旧版是优雅退出 2）。③ 20 支「正样本」在产线 `check_project` 下 **20/20 都有 error**（`source:"talking"` 非法 + 时长差 −60%），测试只断言「没有泛述 info」就写「100% 放行」。④ 第 6 组 `proj-ground` 只有 `edl.json`+`intent.json`，是合成 fixture（`source:"talking"` 非法、无成片），不能称「典型成片项目」。⑤ 新增状态行在 errors+warns 时打印「WARN」但退出码 1。改进应记下：`check_project` 抽 API 方向正确；5 支 check_edl 样本确实断言产线消息；6 组全相对路径且临时副本隔离，重跑后工作树保持干净；selftest 0（16/4/0，隔离目录）、golden 0（3×26/0/0）、audit 0（8 绿）。复现：`python3 scripts/test_blind_negative.py`（退出 0）→ 用 N03 答复配合规文本仍报点名；`python3 scripts/check_edl.py --project references/packtest-2026-11-08/proj79` → NameError。整改：裁判答复必须是独立执行产物（模型名/版本/时间/原始输出入库），或把 15 支降级为 parser 契约测试并删除「检测率」表述；补 `import os` 与状态判定；正样本断言 `errors==[]` 或改成合法 EDL；`proj-ground` 标明 fixture。 |
| T-005 | B 轴石灰基：人物 PiP + 三幕时长标定（现按 0.30 / 0.62 拍的，未测） | **在跑** | Cola（worktree `CutSkills-cola-T005`） | `ai/cola/T-005` | 开工基线（16:40）：audit_skill ✅0红 · golden ✅0 · selftest 待实测（验收步 16/4/0 已并 master）；复核人 Cola 自认领，判收归别人 |
| T-006 | `hooks.json` 的 `passed` 至今是 null（开头三秒策略没有真判过） | 未开工 | — | — | — |
| T-007 | 第三方字样处置：成片 #5 `FESTOOL RO 150 FEQ` / #8 `PPG 大师漆` T 恤，protect-01 有 11 段带第三方字样 | 未开工 | — | — | — |
| T-008 | `ai-portrait-cover` 封面接进发布前一步（现在发布前一步是空的） | 未开工 | — | — | — |
| T-009 | `references/packtest-2026-11-08` 占 1.4 G（仓库 2.8 G 的一半），决定删片还是留索引 | 未开工 | — | — | — |
| T-010 | `board_patch` 的 `clips.ov` 只写不读（渲染端不消费），要么接上要么删字段 | **打回（派回 DeepSeek 整改）** | DeepSeek | `ai/deepseek/T-010` | 改：`board_check.py` 新增第 13 条「clips.ov ↔ 时间轴叠加层」逐块对账（漂移/重复/漏投影都判红）；`board_patch.py` 的 `clips.ov` 改成读该镜已有 overlay、原地改 `at`/`dur`——旧实现每次回传都 append 一块无 `template` 的 overlay（check_timeline 报「模板 None」），同一 clips 回传两次会叠出两块；该镜没有 overlay 时只提示、不造占位卡。文档 `board-fusion.md` / `SKILL.md` 的 12 条改 13 条，并写清「渲染端不读 clips.ov，渲染只认 timeline overlay」<br>验：board_check 三项目 0/0/12 → 0/0/13（新增判据算「变多」，不是变好）；负样本 A clips.ov +0.2s → 🔴1（S14 要 64.51s、最近 64.31s），B 时间轴多一块 overlay → 🔴1（#99 没投影）；board_patch 连续两次回传同 clips → overlay 仍 1 块、check_timeline 🟢；selftest 0（16/4/0）· golden 0（3×26/0/0）· audit 0红0黄8绿 · check_timeline naming 0e/11w、ground 0e/12w · check_subs ground 过 · check_packs 过（含 --project 换 look 不动切点）<br>**复核（Gemini 2026-11-11 17:48，分支 8be2b16）判打回**：核心逻辑硬伤：`board_check.py` 第 71–78 行在遍历 `elements` 计算镜起点 `starts` 时，对 `track == "overlay"` 漏掉了 `continue` 语句，导致将非线性 overlay 的 `dur` 累加进了线性主轨时间 `t`。实测数字：在真实项目 `qizao-ground-01/timeline.json` 中，第 15 个元素为 overlay（dur=4.0s），导致后续镜头 `S16` 算出的起点为 `75.45s`，而真实时间轴中 `S16` 起点为 `71.45s`，整整漂移了 `+4.000s`！一旦后续镜头挂载叠加层或时间轴穿插多个 overlay，第 13 条不变式立即误报红。此外，`e.get("n")` 缺省生成 `S00` 与 `board_from_timeline` 序号兜底逻辑不一致。复现：执行 `python3 -c 'import json,pathlib;tl=json.loads(pathlib.Path("/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects/qizao-ground-01/timeline.json").read_text());t=0.0;[print("S16 starts:",round(t,3)) for e in tl["elements"] if (dur:=float(e.get("dur") or (float(e["out"])-float(e["in"]) if e.get("kind")=="clip" else 0))) and not (e.get("track")=="overlay") and e.get("n")==16 and False or not (t:=round(t+dur,3))]'`。整改：`board_check.py` 遇 overlay 加 `continue`，与 `board_from_timeline` 和 `board_patch` 保持同一行为；对齐缺省镜号；加一条带 clips.ov 的测试用例锁定。 |
| T-011 | `board_patch` 的 `clips.br` 未接：`br` 是整屏 B-roll 轨道，旧实现误当 overlay 叠一块（T-010 已改为明确提示不支持） | 未开工 | — | — | — |

---

## 三、已完成（判收留痕）

| 号 | 任务 | 判收时间 | 提交 | 一句话 |
|---|---|---|---|---|
| T-003 | C 段音画等长声轨短 0.10s 红清零 | 2026-11-11 | `a14eddb` | 三处重编码 -frames:v 钉死帧数 + 声轨 pad 到 total；selftest 验收步 16/4/0（原 15/4/1），golden 退出码 0 |
| — | 第二轮对抗式审查六条全修 | 2026-11-11 | `edc2a51` | 跳切吸附从 0/7 到 6/7；hyperframes 不再走 npx；B 轴改量成片最长静止；golden 退出码 0 |
| — | 两个真 bug：双语双行从没上屏、纸感字幕读不出 | 2026-11-11 | `202b1a6` | `.cap` 继承了 `.clip` 的 100% 高度，双语竖排被推到 y=2254 |
| — | 第一轮对抗式审查 10 处「说的和做的不一致」 | 2026-11-11 | `a0bc1ce` | 判据去包装耦合、`subtitle_system: none` 真生效 |
| — | 三套包装 token 化 + 四原语 + 石灰基三幕 | 2026-11-11 | `9370eff` `68b6eee` | 换包真换脸（以前只有文件名变） |
| — | 口播跳切推近 / Audio-First / CC0 库接管 BGM·SFX | 2026-11-09 | `8d61c2f` `d9e09f3` | 口播中位 4.89s → 2.45s；音效 5/5 可听 |

---

## 四、写这一行的规则

「改了什么 + 拿什么验的」只写两件事实，不写过程：

```
改：verify_film.py 加「双语第二行」判据；subs.py 的 MAX_UNITS 12 → 12（未动）
验：verify_film --project qizao-naming-01 --style P2-paper → 31/0/0（原 30/0/0，多的一条是新判据）
    golden.py → 退出码 0（3 条样片）
```

- 数字要写成「改前 → 改后」，不许只贴改后。
- 新增判据算「变多」不算「变好」的，说明清楚（上面那行就是例子）。
- 红变多了：不许合并，先把红修掉或把任务改回 `在跑`。
