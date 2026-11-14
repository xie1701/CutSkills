# 全部命令与参数

> 这份文件由 `scripts/gen_cli_ref.py` 从脚本的 `argparse` 生成，**不要手改**。
> 改了参数就重跑：`python3 scripts/gen_cli_ref.py`。

说明书讲的是「为什么这样剪」，这里只有「有什么开关」。查它能干的、说明书没提的，看这里。

## 0 · 环境与跨机分发

### `doctor.py`
*第一次跑产线之前的体检：缺什么、去哪补，一次说完。*

（无参数）

### `pack_dist.py`
*把仓库打成可复制的分发包：CutSkills-<日期>.zip*

| 参数 | 说明 |
|---|---|
| `--out` | （默认 str(Path.home() / 'Downloads' / 'CutSkills-dist')） |

## 其他

### `gen_cli_ref.py`
*把每个脚本的 argparse 扫一遍，生成 `references/cli.md`。*

| 参数 | 说明 |
|---|---|
| `--root` | （默认 str(Path(__file__).resolve().parent.parent)） |
| `--out` | 默认写到 <root>/references/cli.md |

## ★ 仓库自检与单测

### `audit_skill.py`
*审查：说明书说的话，和 scripts/ 干的事，对不对得上。*

| 参数 | 说明 |
|---|---|
| `--root` | （默认 str(Path(__file__).resolve().parent.parent)） |
| `--out` | 默认写到 <root>/audit.md |
| `--project` | 给一个真项目目录：把 timeline-schema.md 写的字段和真 timeline.json 对账 |
| `--library` | 给素材库：把 index-schema.md 和真 index.json 对账 |

### `selftest.py`
*V2 端到端全链路自测：从零造合成素材 → 索引/字表 → V5 时间轴（P5 网感包装+标题条+贴纸+冲击推镜+鼓点吸附）→ 拍板签锁 → HyperFrames 真渲染 → 像素/响度/同窗音效门禁 → 全部离线回归单测。*

| 参数 | 说明 |
|---|---|
| `--dir` | （默认 f'/tmp/cutskills-selftest-{_station()}'） |
| `--keep` | 跑完保留临时目录供查看成片 |

### `test_v2_pipeline.py`
*V2 核心模块离线契约单测：选题引擎 / AI 配音字表 / 鼓点吸附 / 矢量贴纸 / 内置 CC0 兜底 / 同窗音效门禁。*

（无参数）

## 1 · 上游选题、脚本与小白拍片卡

### `ideate.py`
*CutSkills · 上游选题、高网感脚本与小白拍片卡生成器（飞书双流向内容系统）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目目录 |
| `--topic` | 一句话选题想法、产品或行业 |
| `--persona` | 出镜人身份/人设（可选） |
| `--audience` | 给谁看（可选） |
| `--duration` | 期望时长（秒，默认 35）（默认 35） |
| `--pick` | 选定第几套提案（1漏斗/2人设/3转化） |
| `--hook` | 选定该提案的第几个钩子（1-3） |
| `--guide` | 输出/刷新 shoot-guide.md 小白拍片卡 |
| `--to-plan` | 将选定脚本一键转为 brief.json + plan.json |
| `--vo-asset` | 绑定已生成的 AI 配音素材 ID |
| `--show` | 打印 3 套提案概览 |

### `voiceover.py`
*CutSkills · AI 配音合成与精准字级时间戳入库引擎。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目目录 |
| `--library` | 素材库目录（默认自动推导或在项目旁创建） |
| `--from-ideation` | 直接读取项目 ideation.json 的选定脚本 |
| `--lines` | 用 | 分隔的多句台词 |
| `--script-file` | 纯文本台词文件（一行一句） |
| `--audio` | 已有外部配音音频路径（跳过合成，直接切分对齐入库） |
| `--engine` | （默认 'auto'） |
| `--voice` | 音色名（如 Tingting / Sinji / 云端音色 ID）（默认 'Tingting'） |
| `--rate` | 语速（字/分钟，默认 195 干脆口播感）（默认 195） |
| `--breath` | 句间换气停顿秒数（默认 0.16s）（默认 0.16） |

## 3 · 编导、卡点与方案拍板

### `beat_sync.py`
*CutSkills · BGM 鼓点/瞬态检测与百万剪辑师卡点吸附引擎（Beat Sync）。*

| 参数 | 说明 |
|---|---|
| `--project` | 项目目录 |
| `--bgm` | BGM 音频文件路径，或 auto / upbeat / warm / clear（默认 'auto'） |
| `--duration` | 分析时长上限（秒）（默认 60.0） |
| `--snap` | 将项目 timeline.json 的弹性切点吸附到鼓点 |
| `--max-shift` | 单处最大微调秒数（默认 0.18s）（默认 0.18） |

## 2 · 素材多模态索引

### `index_assets.py`
*CutSkills · 阶段 A：素材索引。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--jobs` | L0 并发（解码是硬资源，别超过 4）（默认 3） |
| `--l1-jobs` | L1 转写并发（coli asr 并发会互相堵）（默认 1） |
| `--stage` | l0=只探针抽帧画质；l1=只转写；rephrase=从转写缓存重算句子（不重跑模型）；finish=只重算分级并出报告；migrate=给老索引补规格与指纹，不重跑任何重活（默认 'all'） |
| `--only` | 只处理路径含该子串的文件 |
| `--merge-l2` | 开关 |
| `--report` | 开关 |
| `--l2-cap` | （默认 24） |
| `--l2-per-asset` | 每支素材最多取几条候选。默认 0 = 不限（长口播会占满队列）；要铺整库命名就设 1，一个文件一条，cap 覆盖的文件数才等于素材数（默认 0） |
| `--l2-cause` | 排哪些候选进 L2 队列（默认 'mix'） |
| `--l2-budget-note` | L2 覆盖率超 15% 时留一句原因（写进 _cutskills/l2_budget.json，跨轮保留） |
| `--force` | 忽略指纹，全部重算 |

### `media_spec.py`
*CutSkills · 规格探测：把「这条素材是什么规格、剪辑时会踩什么坑」变成机器可判的字段。*

（无参数）

### `render_library_index.py`
*CutSkills · 把 index.json 渲染成「人能在 Finder 里直接看」的索引。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |

### `rename_assets.py`
*CutSkills · 批量重命名素材（带映射、可回滚、按内容指纹对账）。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--apply` | 开关 |
| `--revert` | 开关 |
| `--fix-projects` | 开关 |
| `--include` | 只处理路径含该子串的文件 |
| `--max` | 最多改几个（0 = 全部）（默认 0） |

### `lexicon.py`
*CutSkills · 转写语义纠错（词表层）。*

| 参数 | 说明 |
|---|---|
| `--library` | 素材库根（读 <库>/_cutskills/lexicon.json） |
| `--term` | 候选替换，写法「错写=>建议词」，例「效果好=>效果图」 |
| `--allow-unsafe` | 允许应用「不等长且已标 unsafe」的条目（默认拒绝） |
| `--allow-negation-edit` | 允许增删否定字（不/没/别/未/唔/冇）——高危，默认硬拦 |
| `--json` | 开关 |

### `lint_transcript.py`
*CutSkills · 转写出「待纠错清单」给人/大模型判断。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--top` | （默认 40） |
| `--suspects` | 嫌疑词表（默认 scripts/data/suspect-words.txt） |
| `--magnitudes` | 量级判据表（默认 scripts/data/abnormal-magnitudes.json） |
| `--worklist` | 待审工单 JSON 输出路径（默认 <库>/_cutskills/semantic-worklist.json） |

### `check_l2.py`
*CutSkills · L2 记录自检。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--quiet` | 开关 |

### `cue_guard.py`
*场记口令 / 指导语 / 重说 —— 检出并把它们挡在片子外面。*

| 参数 | 说明 |
|---|---|
| `--scan` | — |
| `--check` | — |
| `--fix` | — |
| `--words` | — |

## 3 · 编导、卡点与方案拍板

### `init_project.py`
*CutSkills · 建一个剪辑项目目录（初始化 intent.json / brief.json / decisions.md）。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--intent` | **必填**　用户原话，别改写 |
| `--slug` | **必填** |
| `--audience` | — |
| `--promise` | 看完要让观众相信 / 做什么 |
| `--duration` | — |
| `--aspect` | — |
| `--pack` | 视觉包装包（默认 P1-warm，可选 P5-viral-pop 等）（默认 DEFAULTS['pack']） |
| `--plan` | 剪辑骨架方案（默认 S1-conclude-fast）（默认 DEFAULTS['plan']） |
| `--avoid` | 不许出现的内容，可多次（默认 []） |

### `brief.py`
*CutSkills · 入口第一问：把「对话即需求」压成 brief.json 的四格。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目目录（<项目>/brief.json 落在它下面） |
| `--set` | 非交互写入：audience / audience_present_state / change / length / style（默认 []） |
| `--show` | 打印当前 brief |
| `--reset` | 清空重来（旧文件备份为 brief.json.bak） |

### `style_menu.py`
*CutSkills · 风格菜单：把 4 套剪法 + 3 套包装摆给用户点，而不是让他写配置。*

| 参数 | 说明 |
|---|---|
| `--list` | 开关 |
| `--paths` | 样例给出绝对路径（聊天里要发文件时用） |
| `--bars` | 开关 |
| `--show` | — |
| `--choose` | — |
| `--project` | — |

### `search_library.py`
*CutSkills · 在素材库里搜「哪段能当我这一段画面」。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--query` | 关键词，可多次；任一命中就算（默认 []） |
| `--for-cut` | 反查：这段口播（asset in out）该配什么画面——自动从它的转写里挑关键词 |
| `--grade` | 最低分级（默认 backup）（默认 'backup'） |
| `--kind` | （默认 'any'） |
| `--min-dur` | 这段至少要多长（秒） |
| `--max-dur` | 这段最多多长（秒），超了自动裁到这么长 |
| `--shot` | 只看某个景别：wide/medium/close/detail/pov/screen |
| `--people` | 只看画面里有谁（speaker/host/worker/customer/none） |
| `--plan` | 直接输出能粘进 plan.json 的片段（JSON） |
| `--limit` | （默认 25） |
| `--json` | 开关 |

### `build_takes_packed.py`
*CutSkills · A→B 桥：把 index.json 变成 agent 能读的视图。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--project` | — |
| `--batch` | 只看某个批次 |
| `--include` | 只要路径含该子串的素材，可多次（默认 []） |
| `--min-grade` | （默认 'backup'） |
| `--max-chars` | （默认 60000） |

### `pick_take.py`
*CutSkills · 一个论点有好几个 take 时，选哪个。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--about` | **必填**　要找的论点关键词 |
| `--top` | （默认 5） |
| `--for` | talking=挑露脸镜头（排除画面 audio_only/ng）；vo=只取旁白声音（不管画面评级） |
| `--json` | 开关 |

### `audio_map.py`
*CutSkills · 音频层：把一条片的声音切成「块」，块是所有时间安排的基准。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--out` | — |
| `--show` | 开关 |

### `skeleton.py`
*从参考片抽「节拍骨架」：切点、字幕带、大字、介质变化、推断的 beats、音频底子。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填**　参考片路径 |
| `--out` | **必填**　输出 JSON 路径 |
| `--transcript` | 可选：SRT/VTT/JSON 转写；不给就自动找同名字幕或 _cutskills/index.json |
| `--json-only` | 不打印人类可读的表 |

### `hooks.py`
*CutSkills · 第 2 层：先出前 3 秒的候选，再谈整条。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--style` | — |
| `--render` | 开关 |
| `--choose` | 定案：A / B / C |
| `--note` | — |
| `--show` | 开关 |
| `--kit` | 出一份给人看的评审包（说明+评分表） |
| `--vote` | 记真人投票：逗号分隔，A/B/C 顺序对应看片顺序，如 A,B,C |
| `--check` | 校验前 3 秒策略合规性与通过性 |

### `timeline.py`
*CutSkills · plan.json + brief.json → timeline.json（元素时间轴 v5）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--plan` | — |
| `--brief` | — |
| `--style` | — |
| `--skeleton` | — |
| `--fill` | 无画面论点自动补章节卡 |
| `--fit-skeleton` | 超长口播段自动吸附字界收紧 |
| `--dry-run` | 只打印，不落盘 |
| `--show` | 查看当前时间轴摘要 |

### `plan_card.py`
*CutSkills · 剪辑与包装方案卡（动手渲染前的人类拍板门禁）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--root` | （默认 str(Path(__file__).resolve().parent.parent)） |
| `--approve` | 确认方案并签发 lock.json，放行 render_hf.py |
| `--show` | 开关 |

### `lock.py`
*方案锁：拍板的那一刻，把时间轴钉死。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--lock` | 开关 |
| `--verify` | 开关 |
| `--status` | 开关 |
| `--note` | — |

## 5 · 质量门禁与交付清理

### `check_timeline.py`
*CutSkills · 校验 timeline.json（v4 元素时间轴）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--timeline` | — |
| `--library` | — |
| `--strict` | 把 warning 当 error |
| `--json` | 开关 |

### `content_guard.py`
*CutSkills · 内容守卫：起尾余量（Handles）、视觉指代词、品类禁忌表。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--timeline` | 默认 <项目>/timeline.json |
| `--library` | 默认从项目目录往上推三层 |
| `--json` | 开关 |

### `evidence_guard.py`
*CutSkills · 台词真值守卫：timeline 里的 evidence 必须能由词级 ASR 复现。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--fix` | 用真值覆盖 evidence |
| `--json` | 开关 |

### `speech_hygiene.py`
*CutSkills · 口播洁净度：进片的声音里不许有片场残渣。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--json` | 开关 |

### `edge_audit.py`
*边界审计：每一格的开头 / 结尾 / 中间，有没有「不干净」的东西。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |

## 4 · 网感包装与成片渲染

### `render_hf.py`
*主渲染链路：timeline.json → HyperFrames 合成（HTML + GSAP + SVG）→ mp4。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--style` | — |
| `--segments` | 只渲前 N 段（0=全部）（默认 0） |
| `--quality` | （默认 'looks'） |
| `--workers` | （默认 '4'） |
| `--grade` | CSS filter，例：saturate(1.5) contrast(1.1) |
| `--no-render` | 只生成 HTML 并 lint |
| `--no-sfx` | 不混卡片音效 |
| `--no-loudnorm` | 开关 |
| `--force` | 方案没拍板也硬渲（正常不该用） |
| `--bgm` | BGM：auto＝用项目里的 bgm.wav（没有就从 CC0 库按情绪挑）；none＝不要；也可给文件（默认 'auto'） |
| `--bgm-mood` | auto＝按 timeline/brief 情绪挑 BGM（默认）；也可指定（默认 'auto'） |
| `--reuse-raw` | 复用 out/_hf_raw.mp4，只重做抠像层贴回与响度（不重跑浏览器渲染） |
| `--hdr` | 按 HDR 出片（本机 ffmpeg 无 zscale，文字层会整片丢失，不建议） |
| `--fg-layer` | 抠像层在哪贴：ffmpeg＝浏览器渲完再用 overlay 贴回去（默认，稳）；browser＝写进 HTML 当 <video>（本机 alpha 会丢，会盖住大字）；none＝不要（默认 'ffmpeg'） |
| `--force-extract` | 开关 |
| `--shot-match` | 逐镜把亮度/白平衡往参考片拉（0=不做，1=全拉）。默认 0.7（默认 0.7） |
| `--extractor` | auto=有 zscale 就自己切（HDR 才色调映射）；hf=退回 hyperframes measure extract（默认 'auto'） |
| `--overscan` | 切片比画布大几倍（默认 1.2）：跳切推近靠 CSS 放大，切片不留富余就会越推越糊。1.0 = 不留富余（默认 1.2） |
| `--punch` | 口播长段拆镜（跳切推近）：默认开。关掉用 --no-punch |
| `--no-punch` | 不拆镜：一条长口播就一个镜头，「口播镜头中位」会顶在 4.9s |
| `--no-cutout` | 不做人物抠像层（大字会压在人前面） |

### `gen_scene.py`
*用代码画画面——不是把字卡换个样式，是让画面本身把话讲完。*

| 参数 | 说明 |
|---|---|
| `--scene` | （默认 'phase'） |
| `--out` | **必填** |
| `--dur` | （默认 5.0） |
| `--fps` | 跟成片一致（talking-head-v1 是 30）（默认 30） |
| `--size` | （默认 '1080x1920'） |
| `--marks` | 词级节拍：逗号分隔的**成数**（0.2,0.39,0.42,0.74），由 ASR 字级时间戳算出来。空着就用脚本自己的默认比例——那样画面换形态的那一帧跟台词对不上。 |
| `--probe` | 只量亮度剖面，不出片：每个场景都要「越演越有东西」，结尾比中间空就是节拍写错了（视觉模型判不了这个） |

### `preview_card.py`
*一张卡/一格代码画面的快速样张：改完立刻看，不用重渲整片。*

| 参数 | 说明 |
|---|---|
| `--project` | 项目目录（从 timeline.json 取第 n 格） |
| `--n` | 取第几格（timeline 的 n） |
| `--gen` | 直接给元素 JSON |
| `--pack` | （默认 'P1-warm'） |
| `--at` | 相对卡片起点的秒数，逗号分隔（默认 '1.0'） |
| `--out` | — |
| `--dur` | --gen 模式下的卡片时长（默认 4.0） |

### `stickers.py`
*CutSkills · 百万剪辑师级动态表情贴纸、悬念标题条与镜头特效层（SVG + GSAP）。*

| 参数 | 说明 |
|---|---|
| `--list` | 打印全部 16 款贴纸 ID 与默认短签 |
| `--json` | 以 JSON 输出目录 |
| `--preview` | 生成一页所有贴纸的 HTML 预览卡目录 |

## 5 · 质量门禁与交付清理

### `gate.py`
*CutSkills 项目总门禁：集中运行现有检查器并汇总 error / warning。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 素材库根；默认从项目祖先找 _cutskills/index.json |
| `--projects` | broll_split 互斥检查的同组项目目录；dianzan-* 默认自动发现同组 |

### `verify_film.py`
*CutSkills · D 段验收：拿成片和风格包对账。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 素材库根（字幕窗口里的真静音要用）；默认从祖先目录找 _cutskills/index.json |
| `--style` | — |
| `--film` | 成片路径（默认 out/film.mp4；HyperFrames 链路是 out/film-hf.mp4） |

### `check_subs.py`
*CutSkills · 字幕裁判：去标点 / 呼吸组 / 单行宽度 / 强调色 / 硬切。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 素材库根（量字幕窗口里的真静音要用）；默认从祖先目录找 _cutskills/index.json |
| `--json` | 开关 |

### `check_cards.py`
*CutSkills · 卡准入：一张卡凭什么能进片。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--json` | 开关 |

### `check_overlay.py`
*CutSkills · 叠加态裁判：卡压脸 / 口播整屏暗场 / 重卡撞字幕（原则 B7 + B9）*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--json` | 开关 |

### `check_keyword_text.py`
*CutSkills · 大字（keyword / behind）裁判：压脸 / 可读性 / 单排宽度*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--film` | 顺手对一遍渲出的成片（字框改成从帧里量墨迹 bbox） |
| `--pack-file` | 直接指定包装包 yaml（夹具用；缺省按 timeline.pack 找） |
| `--json` | 开关 |

### `check_timing.py`
*CutSkills · 声音在前（硬规则）：画面里的每一个字，都得等它那句话被说出来。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--json` | 开关 |

### `check_sfx.py`
*音效可听度检查：优先与混音前同包络底轨（stem-nosfx.m4a）同窗相减，按音色真实频段测瞬态净抬升（T-059）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |

### `check_packs.py`
*CutSkills · 包装包裁判：三套「衣服」的合同测试。*

| 参数 | 说明 |
|---|---|
| `--packs` | — |
| `--project` | 给了就顺手验「换 look 不动切点」 |
| `--library` | — |
| `--json` | 开关 |

### `sync_check.py`
*CutSkills · 声画同步裁判：Audio-First 唯一能算的那三个数。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--map` | — |
| `--timeline` | — |
| `--tolerance` | （默认 TOL_MS） |
| `--json` | 开关 |

### `review_sheet.py`
*生成逐格 IN/MID/OUT 审片接触表与卡片原像素裁图。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目目录，只读时间轴与已渲产物 |
| `--force` | 更新已有 review/sheet.html 与同名生成帧 |

### `showcase.py`
*生成一张「看片导航页」：所有成片一处点开，顺带把命名规则和改片的正确姿势写在同一页上。*

| 参数 | 说明 |
|---|---|
| `--out` | 输出目录（默认取 config.json 的 out_dir）（默认 config_out_dir()） |
| `--library` | 要扫的库根（可多次） |

### `studio.py`
*一键起工程页：把 `cd <项目>/hf && hyperframes preview` 收成一条命令。*

| 参数 | 说明 |
|---|---|
| `--project` | 项目名（dianzan-a / qizao-ground-01 …）或目录 |
| `--library` | 要搜的库根（可多次） |
| `--port` | 指定端口（默认 3100 起找空位） |
| `--no-open` | 不自动开浏览器 |
| `--print-cmd` | 只打印要跑的命令，不真起 |
| `--list` | 列出开着的 studio |
| `--stop` | 关掉某个项目的 studio |
| `--stop-all` | 关掉全部 |
| `--emit-launchers` | 给每支片写一个 .command |

### `tidy.py`
*收工清理：把「能重建的过程文件」和「必须留的交付物」分开，只删前者。*

| 参数 | 说明 |
|---|---|
| `--project` | 单个项目目录 |
| `--library` | 库根（含 _cutskills/projects） |
| `--tmp` | 只清 /tmp 残骸 |
| `--apply` | 真删（默认演练） |

## 其他

### `brand_safety.py`
*CutSkills · 品牌安全与第三方字样检测与处置引擎。*

（无参数）

### `broll_split.py`
*CutSkills · 一组片子的素材互斥：同一批素材剪两条，发出去不能是同一支片。*

| 参数 | 说明 |
|---|---|
| `--projects` | **必填** |
| `--json` | 开关 |

### `card_layout.py`
*CutSkills · 卡的叠加态布局器（原则 B7 图底主权 / B10 局部底衬）*

（无参数）

### `cc0_lib.py`
*从 CC0 库挑 BGM 和音效——外置全量库优先，未配置时自动退回随仓迷你包。*

| 参数 | 说明 |
|---|---|
| `--bgm` | — |
| `--moods` | 开关 |
| `--sfx` | — |
| `--dur` | （默认 45.0） |

### `compare_tones.py`
*两条片子的影调对比：luma / G÷R / B÷R，不拿饱和度当指标。*

| 参数 | 说明 |
|---|---|
| `--a` | **必填**　我们的成片 |
| `--b` | **必填**　参考片（对标目标） |
| `--fps` | 抽帧频率（每秒几帧）（默认 1.0） |
| `--size` | 抽帧后缩放尺寸，两条片必须一样（默认 '270x480'） |
| `--md` | 把报告写到这个 md 文件 |
| `--json` | 开关 |

### `fetch_coli.py`
*把 Cola.app 里打包的 `@marswave/coli` 抽成能跑的 CLI（T-023）。*

（无参数）

### `fonts.py`
*字体表：别名 → 文件，以及**字号反算**。*

| 参数 | 说明 |
|---|---|
| `--list` | 开关 |
| `--size` | — |
| `--width` | （默认 1080） |

## 3 · 编导、卡点与方案拍板

### `gaps.py`
*CutSkills · 缺口清单：把时间轴里缺证据、用代码补画、或台词点图缺画面的地方汇总成 gap.md。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 给了就在库里反查：这个缺口是不是真没有画面 |
| `--no-search` | 不查库（快） |

## 其他

### `libs.py`
*库、项目与运行环境的唯一解析入口。*

（无参数）

## 3 · 编导、卡点与方案拍板

### `log_decision.py`
*CutSkills · 确认点留痕：把「人说了什么 → 我们做了什么 → 结果」记进 decisions.md。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--said` | 用户原话，别改写 |
| `--did` | 据此改了什么（段号 + 动作） |
| `--verdict` | （默认 'accepted'） |
| `--show` | 开关 |

## 其他

### `measure_geom.py`
*几何靠量，不靠视觉模型描述。*

| 参数 | 说明 |
|---|---|
| `--frame` | **必填** |
| `--what` | （默认 'gold,order,shift'） |
| `--band` | 只量这条 y 带：y0:y1 |
| `--ends` | 量两端位置：两个 x 中心，如 200,880 |
| `--yrange` | 两端模式的 y 限制：y0:y1 |
| `--crop` | 只分析这块：x0,y0,x1,y1 |

### `measure_motion.py`
*帧差剖面：一段画面到底有没有在动、有没有卡住。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填** |
| `--fps` | （默认 DEF_FPS） |
| `--from` | — |
| `--dur` | — |
| `--json` | — |
| `--median-band` | 帧差中位的合格区间 y0:y1。代码场景默认 1.2:6.0；实拍/手持段天然更大（是镜头在动，不是问题），给 6:20 之类 |

### `measure_panel.py`
*CutSkills · 卡面板可读性实测：文字像素 vs **紧邻**底板的 ΔL*

| 参数 | 说明 |
|---|---|
| `--image` | **必填** |
| `--box` | x,y,w,h（1080 原像素坐标，裁到卡面内） |
| `--json` | 开关 |

### `render_style_preview.py`
*CutSkills · 包装样张：用 token 画四张「同一条片不同层」的示意帧，30 秒出全套。*

| 参数 | 说明 |
|---|---|
| `--pack` | **必填** |
| `--out` | — |
| `--mg` | 另外真渲三张 MG 卡（stat/term/versus），需要 hyperframes |

### `scene_review.py`
*场景自审：给一段场景视频（或成片里的一格）出一份「每条判据都带读数」的复核报告。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填** |
| `--dur` | **必填**　时间轴上这一格的 dur（秒） |
| `--events` | JSON 数组：[{t,name}]（事件锚点，场景内相对秒） |
| `--out` | 产物目录（默认取视频所在目录） |
| `--json` | 报告路径（默认 <out>/scene-review/review.json） |
| `--median-band` | 帧差中位的合格区间 y0:y1，默认 1.2:6.0（代码场景口径）。实拍/手持/推镜段给 6:20——底片帧差天然 8–13，用场景口径会恒假红 |

### `scene_spec.py`
*scene_spec.json——单个补镜画面的生产任务单（AI 视听方案 2026-11-14 · P0）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--check` | 校验项目里全部 scene_spec 引用 |
| `--init` | — |

### `shot_match.py`
*逐镜归一：把每一镜的亮度与白平衡往目标值拉一部分，别整片一个平均值糊过去。*

| 参数 | 说明 |
|---|---|
| `--clips` | **必填**　切好的片段目录（hf/clips） |
| `--strength` | 0=不动，1=完全拉到目标（默认 0.6） |
| `--apply` | 真的重编码（不加只出报告） |
| `--crf` | （默认 16） |
| `--json` | 把每镜的增益写到这个文件 |

### `silence.py`
*真实静音探针：给定素材与窗口，量出里面**真有**几段没声音。*

（无参数）

### `stability.py`
*画面稳定度曲线：某一秒的这个人，是站定了在说话，还是正在转头挥手。*

（无参数）

### `subs.py`
*CutSkills · 字幕：按呼吸组切、去标点、单行、强调词变色。*

（无参数）

### `test_anchor_miss.py`
*锚词对不上时的合同测试（T-034）：**不许静默回落**。*

（无参数）

### `test_av_lock.py`
*口播段音画同源：画面裁切点必须和声音裁切点逐帧一致。*

（无参数）

### `test_behind_band.py`
*T-041（追加）回归：behind / cutout 大字的「取样带 + 基线」缺一条就假红。*

（无参数）

### `test_brand_safety.py`
*CutSkills · T-007 / T-036 品牌安全与第三方字样处置回归测试*

（无参数）

### `test_breath_split.py`
*T-032 合同测试：拆条按**实测静音**判「换没换一口气」，不是按 ASR 的词起点间隔。*

（无参数）

### `test_cards_meta.py`
*MG 卡「剧组建事本」判据回归：谁说的、哪天拍的、什么设备——一律不许上屏。*

（无参数）

### `test_content_guard.py`
*content_guard 的合同测试：三条新判据该红时红、该绿时绿。*

（无参数）

### `test_cue_guard.py`
*cue_guard 查表口径的合同测试（T-038）：口令检查到底有没有真在跑。*

（无参数）

### `test_edge_audit.py`
*edge_audit ① 孤立提示词的合同测试：假红要灭，真案要留。*

（无参数）

### `test_evidence_guard.py`
*evidence_guard 的合同测试：判据会不会在**该红的时候红、该绿的时候绿**。*

（无参数）

### `test_hold_breath.py`
*T-029 合同测试：「字幕停留」的单呼吸组豁免，放行该放行的、别放过该抓的。*

（无参数）

### `test_hooks_policy.py`
*CutSkills · T-006 钩子策略真判定回归测试*

（无参数）

### `test_hygiene_fixtures.py`
*负样本夹具的**真音频**验证：判据在真声音上还能不能抓住。*

（无参数）

### `test_ink_diff.py`
*T-044 合同单：大字墨迹新口径（字形掩膜 × 帧差）的六条断言。*

（无参数）

### `test_keyword_text.py`
*T-045 回归：`check_keyword_text` 的三条大字判据必须判红，且不许误杀合格卡。*

（无参数）

### `test_measure_geom.py`
*measure_geom 的负样本：判据必须能把画错的那一版抓出来。*

（无参数）

### `test_mg_kinds.py`
*MG 新卡型（proportion / day-night / chips）结构与词证/修辞误用回归（T-020）。*

（无参数）

### `test_overlay_judge.py`
*T-019 回归：check_overlay 的三条判据必须判红，且**不许误杀合法卡**。*

（无参数）

### `test_panel_light.py`
*T-042 回归：面板光感是「加光」，不是「减读」。*

（无参数）

### `test_pip_judge.py`
*T-012 回归：PiP 判据的三层负样本必须红（缺键 / 贴回失败 / 数量不足）。*

（无参数）

### `test_scene_determinism.py`
*T-055 回归：代码场景必须可重复渲染（相同输入 → 逐帧一致）。*

（无参数）

### `test_scene_review.py`
*契约测试：`scene_review.py` 的四条判据真的抓得住——自造样例，不依赖真片。*

（无参数）

### `test_semantic_check.py`
*T-017 合同测试：上屏前的语义常识校对闭环（准绳 A5）。*

（无参数）

### `test_sfx_variety.py`
*离线自足测试：事件级音效（props.events）+ 同音色疲劳轮换（AI 视听方案 2026-11-14 P2）。*

（无参数）

### `test_speech_hygiene.py`
*speech_hygiene 的合同测试：五类残渣必须各自被抓到。*

（无参数）

### `test_studio.py`
*`studio.py`（一键工程页）的离线回归：项目解析、命令拼装、双击入口。*

（无参数）

### `test_sub_density.py`
*T-041 回归：`verify_film` 的「字幕密度」两侧必须同分母（整片分钟），且真判密度。*

（无参数）

### `test_tidy.py`
*`tidy.py` 负样本：它必须删掉可重建的过程文件，且**一个真值源都不许碰**。*

（无参数）

### `test_timing_subs.py`
*check_timing 字幕后置判据的合同测试（T-040）：同音放行能放、编造照抓。*

（无参数）

### `test_visual_grade.py`
*CutSkills · T-015 口播「声 / 画双评级」与露脸门禁 · 合同测试。*

（无参数）

### `visual_grade.py`
*CutSkills · 口播段「声 / 画」双评级与露脸门禁（T-015 · 原则 A2）。*

（无参数）
