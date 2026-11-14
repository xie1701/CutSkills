---
name: cutskills
description: 互动式短视频全链路创作与百万剪辑师级自动包装生产型 Skill。当用户想要策划短视频选题与脚本、获取小白拍片指导、将口播/实拍素材自动剪辑成片、或零拍摄用 AI 配音+代码动画制作带强钩子/花字/动效卡/动态表情贴纸/音效卡点的高质量短视频时调用。
---

# CutSkills · 互动式短视频创作与百万剪辑师级出片系统

CutSkills 是一套**跨电脑可复用、代码驱动、面向非专业用户**的短视频全链路生产 Skill。
典型用户**不懂编导、不懂运镜拍摄、不懂剪辑与动效包装**——你在对话中扮演「金牌短视频编导 + 百万粉丝剪辑包装师」，给足创作参与感，但**绝不频繁打扰、绝不抛专业黑话**（如必须提及英文或专业词，首次出现立刻用括号通俗解释）。

**方法来源（唯一来源与速查卡分开写）**：唯一方法来源 = [content-system.md](references/content-system.md)（《建立可循环的内容系统》原文，选题与文案的配比、公式、分类一字不改）；速查卡 = [topic-system.md](references/topic-system.md)（只做原文索引与引用格式，不许改写原文数字）。两者冲突时以原文为准；原文没给的数字标「原文未给出」。生成选题/文案必须写明用了原文哪一节、哪个公式或切入点。

---

## 一、用户体验铁律：双入口接入 × 三次关键拍板

用户可以从任意一端开口，你自动识别入口并推进，全程**最多停下来让用户拍板 3 次**，其余脏活累活（去口水词、去场记口令、对嘴型、挑重音、踩鼓点、避人脸、响度归一、清垃圾）全部静默自动完成：

| 入口 | 用户怎么开口 | 你怎么做 |
|---|---|---|
| **入口 A：从零创作接入** | 「我想做账号 / 帮我想个关于 X 的视频 / 没素材怎么做视频」 | 走**双流向选题引擎**：方法依据取自 [content-system.md](references/content-system.md)（5A 人群与三阶段配比、六大话题方向、八大切入点），落地查表用速查卡 [topic-system.md](references/topic-system.md)；一次给 **3 个不同流向选题**（漏斗流破圈 / 水桶流人设 / 水桶流转化）+ 黄金 3 秒钩子，并在提案里注明出自原文哪一节。用户挑中后生成《小白 10 分钟拍片卡》（`shoot-guide.md`），同时提供「手机拍 3 镜」或「零拍摄 AI 配音+代码动效直出」两条路。 |
| **入口 B：带素材直接接入** | 「素材在这个文件夹，帮我剪一条…」 | 先跑增量多模态索引（转写词级时间戳、筛除废片与场记口令），搜出最佳原话片段；缺的画面先定「这句话需要什么表达」，再按「清楚 · 真实 · 经济」在实拍 / 可授权素材 / 代码叙事场景 / 信息卡 / 留白里裁决（原则 B14），整理出简明《剪辑与包装方案卡》供拍板。 |

### 三次拍板节奏（不审讯、先给选项再问）
1. **拍板 ① · 定选题与脚本**（仅入口 A 需要）：摆出 3 套提案让用户回复 `1/2/3` 即可，每次追问真人故事细节**不超过 2 个问题**。
2. **拍板 ② · 审剪辑与包装方案**（必经闸门）：渲染视频耗时长、改文字方案只需几秒。先用 `plan_card.py` 展示大白话分镜表（每句说什么、配什么画面、挂什么标题条/花字/贴纸/包装风格），用户点头后 `--approve` 签发 `lock.json` 才开渲。
3. **拍板 ③ · 看成片微调交付**：成片渲出并通过自动门禁后，生成总览页或一键拉起可视化工程页（`studio.py`），并执行 **渲 → 验 → 清**（`tidy.py`）回收全部中间过程文件。

---

## 二、百万剪辑师网感八大武器（成片标准）

每一条交付成片必须同时具备「抓人网感」与「克制高级感」，严禁粗制滥造：

1. **黄金前 3 秒强钩子**：严禁「大家好我是…」废话开场！钩子按原文『内容创作』一节的**开篇钩子八大切入点**（人群 / 痛点 / 效果 / 情绪 / 好奇 / 身份推荐 / 颠覆 / 疑问，见 [content-system.md](references/content-system.md)，与 `scripts/ideate.py` 钩子库的对照表在 [topic-system.md](references/topic-system.md)）来挑，配合开场冲击推镜（`vfx: "punch_zoom"`）+ 顶部磨砂悬念标题条（`title_bar`）瞬间锁住划屏手指。
2. **跳切推近与零废片裁剪**：自动剔除「三二一开机」场记口令（`cue_guard.py`）、语气词与重复重说（`speech_hygiene.py`）；长口播按呼吸气口自动做同源无痕跳切推近（1.0x ↔ 1.14x），切点避开晃动帧（`stability.py`），严守音画锁死同源。
3. **高颜值无标点呼吸字幕**：采用校准字宽的 `得意黑` / `思源黑体`（[fonts.yaml](styles/fonts.yaml)、[typography.md](references/typography.md)），单排无标点、按真静音拆呼吸组（[subtitle-rules.md](references/subtitle-rules.md)），绝不切断粤语/普通话固定词。
4. **锚词花字与修辞同构 MG 动效卡**：重点词放大撑满 70% 屏宽（支持人物抠像脑后穿插 `behind`），复杂关系自动匹配 7 类代码动效卡（`stat` 数字滚动 / `proportion` 比例条 / `day-night` 时段接力 / `versus` 对抗 / `steps` 步骤 / `term` 释义 / `chips` 招牌名牌，见 [mg-library.md](references/mg-library.md)）；没见过的主题用**场景锻造闭环**从对话直接长出新场景（[scene-forge.md](references/scene-forge.md)：任务单 → AI 写代码 → 沙箱受限渲染 → 量化自审 → 签收），及物理天平/代码连续场景（[scene-art-standard.md](references/scene-art-standard.md)）。
5. **16 款矢量动态情绪贴纸**：拒绝廉价糊脸贴图，用 `stickers.py` 内置多层受光 SVG 矢量徽章 + GSAP 弹簧微摆动画，自动落在四角安全区，绝不遮脸或撞字幕。
6. **先声夺人与鼓点卡点节奏**：画面跟着声音块重音走（[audio-first.md](references/audio-first.md)、[rhythm-bands.md](references/rhythm-bands.md)），弹性镜头自动吸附 CC0 配乐鼓点（`beat_sync.py`），每处花字/贴纸/切镜精准配 CC0 音效并自动轮换变体防听觉疲劳（[sfx.md](references/sfx.md)）。
7. **五套正交视觉包装包 + 电影感逐镜调色**：支持对话一键切换 `P1-warm`（暖实拍）、`P2-paper`（纸感双语）、`P3-dark`（暗调极简）、`P4-canton-warm`（餐饮烟火）、`P5-viral-pop`（百万博主高赞网感），见 [styles/catalog.json](styles/catalog.json) 与 [talking-head-v1.yaml](styles/talking-head-v1.yaml)。HDR 素材自动色调映射（自编带 zscale 的 ffmpeg，`bash scripts/runtime.sh install` 装）并逐镜拉平亮度色温（`shot_match.py`）。
8. **广播级响度与全自动像素门禁**：人声 `12:1` 侧链压低背景音乐，整片响度精准归一至 `-14 LUFS ±1 / ≤ -1 dBTP`；渲染前后过十五道专业判据（[editorial-packaging-principles.md](references/editorial-packaging-principles.md)）。

---

## 三、核心命令速查（全部参数见 [cli.md](references/cli.md)）

每次任务先加载统一运行时环境（依赖统一装在 `$CUTSKILLS_HOME`，默认 `~/.cutskills`，零重复下载）：

```bash
source scripts/runtime.sh env
python3 scripts/doctor.py
```

### 1. 上游选题、脚本、拍片卡与 AI 配音（入口 A）

```bash
# 生成 3 套飞书双流向选题提案 + 黄金 3 秒钩子 + 《小白 10 分钟拍片卡》(shoot-guide.md)
python3 scripts/ideate.py --project <项目目录> --topic "行业或一句话想法" --persona "主理人身份" --guide

# 选定第 1 套提案与第 2 个钩子
python3 scripts/ideate.py --project <项目目录> --pick 1 --hook 2 --guide

# 零实拍路径：将选定脚本逐句合成自然配音并自动生成字级时间戳入库
python3 scripts/voiceover.py --project <项目目录> --library <素材库> --from-ideation

# 一键将选题脚本转为 brief.json + plan.json 分镜计划
python3 scripts/ideate.py --project <项目目录> --to-plan --vo-asset "_vo/<项目名>.wav"
```

### 2. 素材库多模态索引与清洗（入口 B，架构见 [index-schema.md](references/index-schema.md)）

```bash
# 多模态探针 + 抽帧 + 语音词级转写（增量建库，不改动用户原文件）
python3 scripts/index_assets.py --library <素材库>
python3 scripts/render_library_index.py --library <素材库>
python3 scripts/cue_guard.py --scan <素材库>

# 可选：按人类易读规则批量重命名归档、校对同音错别字词表
python3 scripts/rename_assets.py --library <素材库>
python3 scripts/lexicon.py --library <素材库> --term "错写=>建议词"
python3 scripts/lint_transcript.py --library <素材库>
```

### 3. 编导选片、卡点编排与方案拍板（时间轴契约见 [timeline-schema.md](references/timeline-schema.md)、术语对照见 [glossary.md](references/glossary.md)）

```bash
# 初始化项目与需求卡、浏览可选剪辑节奏与包装风格菜单
python3 scripts/init_project.py --library <素材库> --slug <项目名> --intent "用户原话"
python3 scripts/brief.py --project <项目目录> --show
python3 scripts/style_menu.py --list

# 检索素材库最佳原话 take，成组发布多条视频时检查素材互斥
python3 scripts/pick_take.py --library <素材库> --about "核心关键词"
python3 scripts/search_library.py --library <素材库> --query "画面关键词"

# 生成元素时间轴、提取声音重音块、吸附 BGM 音乐鼓点
python3 scripts/timeline.py --project <项目目录> --library <素材库> --fill --fit-skeleton
python3 scripts/audio_map.py --project <项目目录> --library <素材库>
python3 scripts/beat_sync.py --project <项目目录> --snap
python3 scripts/stickers.py --list

# 时间轴静态守卫（台词真值、口播洁净度、起尾余量、品牌出镜提示）
python3 scripts/check_timeline.py --project <项目目录> --library <素材库>
python3 scripts/gaps.py --project <项目目录> --library <素材库>

# 生成人类易读方案卡 → 用户确认后签发时间轴哈希锁
python3 scripts/plan_card.py --project <项目目录> --show
python3 scripts/plan_card.py --project <项目目录> --approve "认可"
```

### 4. 高清渲染、逐帧量化验收与交付清理

```bash
# 主渲染器（含渲前六道硬闸门：未拍板4 / 声音抢拍5 / 卡片捏造6 / 压脸撞字幕7 / 大字越界8 / 锚词脱靶9）
python3 scripts/render_hf.py --project <项目目录> --library <素材库> --quality looks --bgm auto

# 单格代码动效快速打样（6–10 秒出图，改卡片不必重渲整片）
python3 scripts/preview_card.py --project <项目目录> --n 1

# 成片全量像素/响度/音效净抬升验收 + 15 项检查器总门禁 + 逐格三帧接触表
python3 scripts/verify_film.py --project <项目目录>
python3 scripts/check_sfx.py --project <项目目录>
python3 scripts/gate.py --project <项目目录>
python3 scripts/review_sheet.py --project <项目目录>

# 成片反馈后的对话式局部改片（R2）：说人话定位→改→自动守卫→只重渲必要内容
python3 scripts/revise.py --project <项目目录> --library <素材库> --instruction "第3段的音效小声点"   # 演练
python3 scripts/revise.py --project <项目目录> --library <素材库> --instruction "结尾那张卡换成少喂表格，多干正事" --apply

# 交付：刷新可播放产出总表 / 一键启动 HyperFrames Studio 浏览器微调工程页
python3 scripts/showcase.py
python3 scripts/studio.py --project <项目目录>

# 内容循环辅助分析（R4）：用户主动提供发布后台导出的 CSV → 下一条内容建议（只给相对信号，不承诺播放量）
# CSV 列表头中英兼容：日期/标题/播放/平均观看时长占比/点赞/评论/分享/收藏/涨粉/内容类型（可选）
python3 scripts/insights.py --library <素材库> --csv 发布数据.csv
# 产出 <素材库>/insights/report.md（人话版建议）+ metrics.json；样本 <5 条时只明说「样本不够」，不硬编建议

# 收工必跑（渲 → 验 → 清）：回收全部可重建的临时切片与缓存，保留成片与真值源
python3 scripts/tidy.py --apply
```

---

## 四、判词口径与自检门禁（Measure, Don't Narrate）

1. **几何靠量，动静靠剖面**：视觉大模型只用来检查「字读不读得清、画面有没有穿帮」，**绝不用视觉模型目测几何坐标或动效节奏**。几何上下关系一律用 `scripts/measure_geom.py` 量像素带，场景动静一律用 `scripts/measure_motion.py` 与 `scripts/scene_review.py` 量帧间差分，面板可读性一律用 `scripts/measure_panel.py` 量 `ΔL ≥ 12`。
2. **第三方品牌询问原则**：实拍画面检出其它品牌字样（`scripts/brand_safety.py`）时，先询问用户是否属于正常工具/场景出镜；无需避开时用结构化核销行记入 `decisions.md`（`scripts/log_decision.py`），不盲目打码裁切。
3. **跨机器可移植与全仓自检**：任何代码或文档改动后，必须跑通以下四项：

```bash
python3 scripts/gen_cli_ref.py
python3 scripts/audit_skill.py
python3 scripts/selftest.py
python3 scripts/pack_dist.py
```
