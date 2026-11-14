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

### 三次拍板节奏（不审讯、先给猜测再问）

三次的顺序与对象是固定的（BRIEF 原文口径），多一个都不许加——`approval.json.asks` 是唯一登记处，第 4 个必经打断点会直接报错：

1. **拍板 ① · 确认选题**（仅入口 A 需要）：摆出 3 套提案（带原文出处）让用户回复 `1/2/3` 即可，钩子同理；真人故事细节追问**不超过 2 个问题**，且必须先给猜测。
2. **拍板 ② · 确认拍摄任务卡或素材**（必经闸门，两种对象）：
   - 入口 A 生成 `shoot-guide.md` 后 `ideate.py --approve-shots "改了哪几镜"` 签卡，**存卡指纹**；
   - 入口 B 盘点后 `ideate.py --approve-material "原话" [--exclude-clip a.mov:12-18.5]` 签盘点，排除项写回并影响反推选题。
   - 卡或盘点改了字节，这道拍板自动失效——不是一句「认可」永久有效。
3. **拍板 ③ · 确认锁定音轨和低清粗剪预览**（必经闸门）：先 `plan_card.py --preview` 出 540×960 粗剪预览（画面烧「粗剪预览 · 未过门禁」），用户点头后 `--approve` 签发 `lock.json` 才开渲。

渲前闸门按这三拍判：缺 ②/③ 直接退 4 并点名缺哪拍（`--force` 也逐条念出跳过了什么）；① 不硬拦，但每次渲都会打一声。

**只改画面 vs 改口播**：只改画面时音轨指纹（`lock.json` 的 `audio_fp`）一个字符不变；改口播就回到拍板③ 重新锁定，画面按新音轨自动重排。

成片渲出后交付不算一次拍板：直接生成总览页或拉起 `studio.py`，执行 **渲 → 验 → 清**（`tidy.py`），并附上「我替你做的决定」清单（`assumptions.md`），用户一句话就能改。

---

## 二、百万剪辑师网感八大武器（成片标准）

每一条交付成片必须同时具备「抓人网感」与「克制高级感」，严禁粗制滥造：

1. **黄金前 3 秒强钩子**：严禁「大家好我是…」废话开场！钩子按原文『内容创作』一节的**开篇钩子八大切入点**（人群 / 痛点 / 效果 / 情绪 / 好奇 / 身份推荐 / 颠覆 / 疑问，见 [content-system.md](references/content-system.md)，与 `scripts/ideate.py` 钩子库的对照表在 [topic-system.md](references/topic-system.md)）来挑，配合开场冲击推镜（`vfx: "punch_zoom"`）+ 顶部磨砂悬念标题条（`title_bar`）瞬间锁住划屏手指。
2. **跳切推近与零废片裁剪**：自动剔除「三二一开机」场记口令（`cue_guard.py`）、语气词与重复重说（`speech_hygiene.py`）；长口播按呼吸气口自动做同源无痕跳切推近（1.0x ↔ 1.14x），切点避开晃动帧（`stability.py`），严守音画锁死同源。
3. **高颜值无标点呼吸字幕**：采用校准字宽的 `得意黑` / `思源黑体`（[fonts.yaml](styles/fonts.yaml)、[typography.md](references/typography.md)），单排无标点、按真静音拆呼吸组（[subtitle-rules.md](references/subtitle-rules.md)），绝不切断粤语/普通话固定词。
4. **锚词花字与修辞同构 MG 动效卡**：重点词放大撑满 70% 屏宽（支持人物抠像脑后穿插 `behind`），复杂关系自动匹配 7 类代码动效卡（`stat` 数字滚动 / `proportion` 比例条 / `day-night` 时段接力 / `versus` 对抗 / `steps` 步骤 / `term` 释义 / `chips` 招牌名牌，见 [mg-library.md](references/mg-library.md)）；没见过的主题用**场景锻造闭环**从对话直接长出新场景（[scene-forge.md](references/scene-forge.md)：任务单 → AI 写代码 → 沙箱受限渲染 → 量化自审 → 签收），及物理天平/代码连续场景（[scene-art-standard.md](references/scene-art-standard.md)）。
5. **16 款矢量动态情绪贴纸**：拒绝廉价糊脸贴图，用 `stickers.py` 内置多层受光 SVG 矢量徽章 + GSAP 弹簧微摆动画，自动落在四角安全区，绝不遮脸或撞字幕。
6. **先声夺人与鼓点卡点节奏**：画面跟着声音块重音走（[audio-first.md](references/audio-first.md)、[rhythm-bands.md](references/rhythm-bands.md)），弹性镜头自动吸附 CC0 配乐鼓点（`beat_sync.py`），每处花字/贴纸/切镜精准配 CC0 音效并自动轮换变体防听觉疲劳（[sfx.md](references/sfx.md)）。
7. **五套正交视觉包装包 + 电影感逐镜调色**：支持对话一键切换 `P1-warm`（暖实拍）、`P2-paper`（纸感双语）、`P3-dark`（暗调极简）、`P4-canton-warm`（餐饮烟火）、`P5-viral-pop`（百万博主高赞网感），见 [styles/catalog.json](styles/catalog.json) 与 [talking-head-v1.yaml](styles/talking-head-v1.yaml)。HDR 素材自动色调映射（需自编带 zscale 的 ffmpeg：`bash scripts/runtime.sh install-ffmpeg-zimg`；系统 ffmpeg 无 zscale，HDR 会压成偏亮偏灰）并逐镜拉平亮度色温（`shot_match.py`）。
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
# 双轨判定（BRIEF：首次对话先判定用户属于哪一轨）：门店轨看同城咨询/到店，博主轨看 3 秒留存/完播/涨粉
# 先给猜测让用户确认，不问开放题；判定词与命中原文片段都落在 account.json.basis 里
python3 scripts/track.py --project <项目目录>            # 出猜测 + 两个选项
python3 scripts/track.py --project <项目目录> --set 门店   # 用户改判（decided_by=user）

# 4 周内容日历：按原文三阶段配比出 16 条，每条标 A 层级 / 话题类型 / 钩子类型 / 原文出处
python3 scripts/content_calendar.py --project <项目目录> --weeks 4 --start 2026-11-17

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
# 句间默认 J-cut：画面比声音晚 2–4 帧切（声音一个采样不动），--no-jcut 可对比/回退，--jcut-only 只重算这一层
python3 scripts/timeline.py --project <项目目录> --jcut-only
python3 scripts/audio_map.py --project <项目目录> --library <素材库>
python3 scripts/beat_sync.py --project <项目目录> --snap
python3 scripts/stickers.py --list

# 时间轴静态守卫（台词真值、口播洁净度、起尾余量、品牌出镜提示）
python3 scripts/check_timeline.py --project <项目目录> --library <素材库>
python3 scripts/gaps.py --project <项目目录> --library <素材库>

# 拍板① → ② → ③：三道留痕都在 approval.json，asks 是打断次数的唯一登记处
python3 scripts/ideate.py --project <项目目录> --pick 1 --hook 2 --guide
python3 scripts/ideate.py --project <项目目录> --approve-shots "第3镜改俯拍特写，第4镜不要了"   # 入口 A 签拍摄卡
python3 scripts/ideate.py --project <项目目录> --approve-material "这个库可以，但 a.mov 12-18.5 别用"   # 入口 B 签盘点
python3 scripts/ideate.py --project <项目目录> --gates                                     # 看三拍状态与打断次数（上限 3）

# 生成人类易读方案卡 → 用户确认后签发时间轴哈希锁
python3 scripts/plan_card.py --project <项目目录> --show
python3 scripts/plan_card.py --project <项目目录> --approve "认可"

# 拍板③：先出一版低清粗剪预览给用户看（540×960，画面烧「粗剪预览 · 未过门禁」）
# 人工签锁时若预览与当前时间轴不是同一版会被拒；只改画面时音轨指纹不变
python3 scripts/plan_card.py --project <项目目录> --preview

# 「我替你做的决定」清单：片长/包装/BGM/账号轨/节奏/素材取舍/字幕/音效/调色/响度，每条带来源与一句话改法
# 由 plan_card.py 一并生成，取不到的来源留行并标 ⚠，不许消失
```

### 3b. 素材规整、盘点反推选题、降噪与字幕模板

```bash
# 手机素材多为可变帧率：先统一转 CFR 并保留原始音频，否则剪得越长漂移越大
# 只看实测（帧间隔脱网格占比），不看容器标记；判不了一律输出「判不了」，绝不默认 CFR
python3 scripts/normalize.py --library <素材库> --dry-run      # 先看哪些要转（一个文件都不写）
python3 scripts/normalize.py --library <素材库>                # 真转，产物进 <库>/_cfr/

# 入口 B：素材盘点 → 主动反推最适合的选题（时长/内容/可用片段/画质问题逐类计数）
# 三套选题各自带原文出处 + 素材依据 + 能撑多长；撑不住就只出两条，宁少勿凑
python3 scripts/inventory.py --library <素材库>
python3 scripts/ideate.py --project <项目目录> --from-inventory <素材库>

# 人声降噪：默认不开；赤先 --measure 看这批素材到底该不该降（阈值取本库实测 p75）
python3 scripts/denoise.py --library <素材库> --measure
python3 scripts/render_hf.py --project <项目目录> --denoise      # 只在人声预归一之前作用，碰不到 BGM/音效

# 字幕样式：四套模板任选（极简白字 / 大字重描边 / 纸感底衬 / 冷色细体），字体均 SIL OFL 可商用
# 优先级：项目显式选的模板 > 包装包自带参数 > 默认；不选时行为与旧版逐字节一致
python3 scripts/style_menu.py --subtitles
python3 scripts/style_menu.py --choose paper-plate
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

# 导出剪映草稿（单向、一次性）：供用户手动精修；失效时自动降级为只出 mp4
# 口径：写出的是明文草稿，剪映 6.0+ 打开后自己会就地加密——**工具读不回来**，只能单向导出
python3 scripts/export_jianying.py --project <项目目录>              # 写到临时目录
python3 scripts/export_jianying.py --project <项目目录> --draft-root "~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
# 状态与原因落 out/jianying-export.json：ok / degraded（不写半成品，目录零残留）/ refused（版本不兼容，只出 mp4）

# 封面与发布标题：从口播原话出 3 个主标题 + 1 句副文案 + 1 条发布标题（零无来源数字，可 --render 出 1080×1920 封面图）
python3 scripts/cover.py --project <项目目录>
python3 scripts/cover.py --project <项目目录> --pick 2 --render

# 授权总表：这条成片实际用到的音频 / 字体 / 贴纸 / 代码场景逐条列授权，未核验的排在最前面（口径见 [licensing.md](references/licensing.md)）
python3 scripts/licensing.py --project <项目目录> --write

# 内容循环账本：发布后记一笔，之后回填后台数据（周复盘与建议都读这本账）
python3 scripts/content_log.py --library <素材库> --add --project <项目目录> --post-url "<链接>" --posted-at 2026-11-14 --platform 小红书
python3 scripts/review_week.py --library <素材库>

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

---

## 五、脚本清单：上面命令段没写到的那些

第三节只列了「每条片子都会跑」的命令。`scripts/` 里剩下的脚本分三类，一个都不许野在外面——没人知道存在的功能等于不存在。全部参数见 [cli.md](references/cli.md)。

**被 import 的库模块（别直接跑）**：`libs.py`（库 / 项目 / 运行环境的唯一解析入口）、`fonts.py`（字体别名与字号反算）、`visual_grade.py`（口播段「声 / 画」双评级与露脸门禁）、`hooks.py`（前 3 秒钩子候选与政策）、`lock.py`（方案锁签发与校验）、`broll_split.py`（一组片子的素材互斥）、`check_keyword_text.py`（大字裁判：压脸 / 可读性 / 单排宽度）、`platform_safe.py`（平台 UI 遮挡区求交：字幕、贴纸进没进安全区）、`content_log.py`（内容循环账本 `content-log.jsonl` 的唯一读写入口）、`first_frame.py`（首帧信息密度：边缘 / 熵 / 显著区域，抓空底板与黑场开场）、`assumptions.py`（「我替你做的决定」清单，由 `plan_card.py` 调用）。

**独立小工具（按需手动跑）**：`fetch_coli.py`（把 Cola.app 里打包的 coli 抽成能跑的 CLI）、`compare_ref.py`（成片 vs 对标视频逐秒结构对比）、`review_week.py`（每周复盘：读账本出 `insights/week-*.md`）、`licensing.py`（授权总表：`out/licensing.json` + `.md`，口径见 [licensing.md](references/licensing.md)）、`cover.py`（封面主标题 / 副文案 / 发布标题，可渲 1080×1920 封面图）、`track.py`（双轨判定：门店轨 / 博主轨，先给猜测再确认）、`content_calendar.py`（4 周内容日历：三阶段配比运行时从原文解析）、`normalize.py`（VFR→CFR 规整与漂移换算成人话）、`inventory.py`（素材盘点与反推选题）、`denoise.py`（人声降噪：`--measure` 只量、`--apply` 渲前预处理，带人声失真闸）、`shoot_diagram.py`（拍摄任务卡的简笔机位示意，由 `ideate.py --guide` 调用）。

**离线回归单测**（`selftest.py` 逐支挂载，也可以单跑 `python3 scripts/<名字>`）：`test_v2_pipeline.py`、`test_r0_guards.py`、`test_hooks_policy.py`、`test_pip_judge.py`、`test_brand_safety.py`、`test_semantic_check.py`、`test_av_lock.py`、`test_visual_grade.py`、`test_edge_audit.py`、`test_timing_subs.py`、`test_cue_guard.py`、`test_sub_density.py`、`test_behind_band.py`、`test_keyword_text.py`、`test_anchor_miss.py`、`test_ink_diff.py`、`test_mg_kinds.py`、`test_tidy.py`、`test_studio.py`、`test_cards_meta.py`、`test_evidence_guard.py`、`test_speech_hygiene.py`、`test_content_guard.py`、`test_overlay_judge.py`、`test_panel_light.py`、`test_hold_breath.py`、`test_breath_split.py`、`test_scene_determinism.py`、`test_measure_geom.py`、`test_sfx_variety.py`、`test_scene_review.py`、`test_scene_sandbox.py`、`test_hygiene_fixtures.py`、`test_content_log.py`、`test_qc_brief.py`、`test_index_merge.py`、`test_licensing.py`、`test_calendar.py`、`test_opening_changes.py`、`test_jianying_export.py`、`test_assumptions.py`、`test_normalize.py`、`test_first_frame.py`、`test_subtitle_templates.py`、`test_denoise.py`、`test_platform.py`、`test_inventory.py`、`test_jcut.py`、`test_av_decouple.py`、`test_three_gates.py`。

**新增脚本的规矩**：新脚本除了写进 `references/cli.md`（重跑 `gen_cli_ref.py` 自动生成），还必须在上面任一处出现——`audit_skill.py` 只认手写文档（`SKILL.md` / `README.md` / `references/*.md`），`cli.md` 是自己生成的，不算数；否则门禁会点名「写了没人会跑」。
