# 全部命令与参数

> 这份文件由 `scripts/gen_cli_ref.py` 从脚本的 `argparse` 生成，**不要手改**。
> 改了参数就重跑：`python3 scripts/gen_cli_ref.py`。

说明书讲的是「为什么这样剪」，这里只有「有什么开关」。查它能干的、说明书没提的，看这里。

## 其他

### `gen_cli_ref.py`
*把每个脚本的 argparse 扫一遍，生成 `references/cli.md`。*

| 参数 | 说明 |
|---|---|
| `--root` | （默认 str(Path(__file__).resolve().parent.parent)） |
| `--out` | 默认写到 <root>/references/cli.md |

## A · 索引

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

## A · 索引（库）

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

（无参数）

### `lint_transcript.py`
*CutSkills · 转写出「待纠错清单」给人/大模型判断。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--top` | （默认 40） |

### `check_l2.py`
*CutSkills · L2 记录自检。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--quiet` | 开关 |

## ★ 任何一段之后——自检

### `audit_skill.py`
*审查：说明书说的话，和 scripts/ 干的事，对不对得上。*

| 参数 | 说明 |
|---|---|
| `--root` | （默认 str(Path(__file__).resolve().parent.parent)） |
| `--out` | 默认写到 <root>/audit.md |
| `--project` | 给一个真项目目录：把 edl-schema.md 写的字段和真 edl.json 对账 |
| `--library` | 给素材库：把 index-schema.md 和真 index.json 对账 |

## B · 编导

### `init_project.py`
*CutSkills · 阶段 B 起步：建一个剪辑项目。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--intent` | **必填**　用户原话，别改写 |
| `--slug` | **必填** |
| `--audience` | — |
| `--promise` | 看完要让观众相信 / 做什么 |
| `--duration` | — |
| `--aspect` | — |
| `--avoid` | 不许出现的内容，可多次（默认 []） |

### `intake.py`
*CutSkills · B 段入口检查：这次是「有需求的剪辑」，还是「一堆素材想变成片子」。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--project` | **必填** |
| `--intent` | 用户原话；给了会写进 intent.json |
| `--script` | 用户提供的口播/旁白稿 |
| `--reference` | 已有的成品或剪辑方案 |
| `--derive` | 生成选题工作台 options.md |
| `--verify` | 检查 options.md 里点名的素材是否存在 |

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

### `make_edl.py`
*CutSkills · 把剪辑计划落成 edl.json（自动吸附字界 + 自动留 padding）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--plan` | 剪辑计划；--revert / --list 时不需要 |
| `--library` | 默认取项目往上三层的素材库 |
| `--pad-in` | （默认 0.06） |
| `--pad-out` | （默认 0.09） |
| `--revert` | 回到上一个版本（edl-history 里最新一份） |
| `--list` | 列出历史版本 |

### `preview_plan.py`
*CutSkills · 计划的试算（dry-run）：不落 edl.json，直接看这一版会成什么样的片子。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--project` | **必填** |
| `--plan` | **必填** |
| `--json` | 开关 |
| `--pad-in` | （默认 0.06） |
| `--pad-out` | （默认 0.09） |

### `check_edl.py`
*CutSkills · 阶段 B 闸门：校验 edl.json。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 默认从项目路径往上找 |
| `--strict` | 开关 |

### `render_paper_edit.py`
*CutSkills · 视图生成：edl.json → paper-edit.md（按论点分节的三列稿）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 默认取项目往上三层的素材库 |

### `view_diff.py`
*CutSkills · 对账：视图被手改过没有，计划比上次改了什么。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--plan` | 只看 plan 与历史版本的差异 |

### `gaps.py`
*CutSkills · 缺口清单：这条片子缺什么，写到 gap.md 让人去补。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--blind` | 盲测回答文件，可多次（默认 []） |
| `--library` | 给了就在库里反查：这个缺口是不是真没有画面 |
| `--no-search` | 不查库（快，但缺口只报不验证） |

### `log_decision.py`
*CutSkills · 确认点留痕：把「人说了什么 → 我们做了什么 → 结果」记进 decisions.md。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--said` | 用户原话，别改写 |
| `--did` | 据此改了什么（段号 + 动作） |
| `--verdict` | （默认 'accepted'） |
| `--show` | 开关 |

## C · 渲染

### `render_film.py`
*CutSkills · C 段：EDL → 成片。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | **必填** |
| `--style` | — |
| `--pack` | 字幕系统模板：pack-safe-talking / pack-paper-brand / pack-bilingual 等 |
| `--shell` | 壳体模板（可选）：pack-hud-monitor。只出壳，字幕系统仍由 --pack 定 |
| `--canvas` | WxH；用 --pack 时生效（默认 '1080x1920'） |
| `--fps` | （默认 30） |
| `--bgm` | 背景音乐（本地文件）。参考片全程铺底、不换曲；没有 BGM 的片，人声一停就是录音棚空场，这是看着不像的一半 |
| `--crf` | （默认 18） |
| `--no-punch` | 不做推镜切：口播长段保持一镜到底（口播中位会超标，只在刻意要长镜头时用） |

## D · 验收

### `verify_film.py`
*CutSkills · D 段验收：拿成片和风格包对账。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--style` | — |
| `--film` | 成片路径（默认 out/film.mp4；HyperFrames 链路是 out/film-hf.mp4） |

### `blind_test.py`
*CutSkills · 盲测：把成片稿交给一个不知道内情的读者，看他读出来的是什么。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--score` | 裁判的回答文件，可多次（默认 []） |

## B/D · 对照

### `ab_open.py`
*CutSkills · A/B 双开场：同一批素材、两种开头、其余完全不动。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | 默认取项目往上三层的素材库 |
| `--variant` | 要生成哪几版，默认 A,B（默认 'A,B'） |
| `--seconds` | 每条开场渲染多长（默认 10s）（默认 10.0） |
| `--no-render` | 开关 |

## 其他

### `arrange.py`
*CutSkills · 结构方案：把「倒不倒序」交给人选，而不是我替他定。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--choose` | — |
| `--show` | 开关 |

### `audio_map.py`
*CutSkills · 音频层：把一条片的声音切成「块」，块是所有时间安排的基准。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--out` | — |
| `--show` | 开关 |

### `bench.py`
*CutSkills · 产线基准：量「产出变得可重复」这件事本身，不量这一条片好不好。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--project` | **必填** |
| `--plan` | 默认用项目里的 plan.json |
| `--render-only` | 只量渲染+验收（改一段的返工成本） |
| `--show` | 只打印历史 |

### `bgm_gen.py`
*CutSkills · 代码合成 BGM：按需求/观点的情绪出一段垫乐，不用去翻素材库。*

| 参数 | 说明 |
|---|---|
| `--project` | — |
| `--mood` | （默认 'auto'） |
| `--dur` | 秒；默认取 timeline 片长 |
| `--out` | — |
| `--moods` | 开关 |
| `--analyze` | 只量一段 wav，不合成 |

### `board_check.py`
*看板数据的不变式检查，外加「导出 XML 后应该看到什么」的对照表。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--xml` | 打出 XML 应有的 clip 表 |
| `--md` | 写成 board/check-report.md |

### `board_from_timeline.py`
*timeline.json v4 → 制作看板（board.js + board.html）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--out` | 输出目录（相对项目，默认 board）（默认 'board'） |
| `--title` | — |
| `--no-build` | 只出 board.js，不生成 html |

### `board_patch.py`
*看板回传的「【制作看板评论…」→ 改写 timeline.json。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--file` | 评论文本文件；不给就读 stdin |
| `--dry-run` | 开关 |

### `brand_safety.py`
*CutSkills · 品牌安全与第三方字样检测与处置引擎。*

（无参数）

### `brief.py`
*CutSkills · 入口第一问：把「对话即需求」压成 brief.json 的四格。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目目录（<项目>/brief.json 落在它下面） |
| `--set` | 非交互写入：audience / audience_present_state / change / length / style（默认 []） |
| `--show` | 打印当前 brief |
| `--reset` | 清空重来（旧文件备份为 brief.json.bak） |

### `broll_split.py`
*CutSkills · 一组片子的素材互斥：同一批素材剪两条，发出去不能是同一支片。*

| 参数 | 说明 |
|---|---|
| `--projects` | **必填** |
| `--json` | 开关 |

### `calibrate.py`
*CutSkills · 用参考片校准「声画同步」的阈值。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填** |
| `--skeleton` | skeleton.json；不给就现场跑 skeleton.py |
| `--cache` | （默认 str(Path.home() / '.cutskills/cache/calib')） |
| `--tolerance` | （默认 80） |
| `--tolerance-from` | 反推：要多宽的窗口才能让这支片自己达到这个同步率 |
| `--json` | 开关 |

### `calibrate_marks.py`
*三幕时长标定：把 B 轴场景格的 marks 从「拍的比例」变成「跟着旁白走的」。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目 slug（--root 下的目录名） |
| `--root` | （默认 os.environ.get('CUTSKILLS_PROJECTS', DEF_ROOT)） |
| `--lib` | 素材根目录，默认 --root 的上两级 |
| `--write` | 把采纳的新 marks 写回 timeline.json |

### `cc0_lib.py`
*从 CC0 库里挑 BGM 和音效——不再自己合成。*

| 参数 | 说明 |
|---|---|
| `--bgm` | — |
| `--moods` | 开关 |
| `--sfx` | — |
| `--dur` | （默认 60.0） |

### `check_cards.py`
*CutSkills · 卡准入：一张卡凭什么能进片。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--json` | 开关 |

### `check_packs.py`
*CutSkills · 包装包裁判：三套「衣服」的合同测试。*

| 参数 | 说明 |
|---|---|
| `--packs` | — |
| `--project` | 给了就顺手验「换 look 不动切点」 |
| `--library` | — |
| `--json` | 开关 |

### `check_sfx.py`
*音效到底听不听得见——量出来，别靠嘴说。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |

### `check_subs.py`
*CutSkills · 字幕裁判：去标点 / 呼吸组 / 单行宽度 / 强调色 / 硬切。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--json` | 开关 |

### `check_timeline.py`
*CutSkills · 校验 timeline.json（v4 元素时间轴）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--timeline` | — |
| `--library` | — |
| `--strict` | 把 warning 当 error |
| `--json` | 开关 |

### `check_timing.py`
*CutSkills · 声音在前（硬规则）：画面里的每一个字，都得等它那句话被说出来。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--json` | 开关 |

### `compare_films.py`
*成片 vs 参考片 · 逐帧对照台。*

| 参数 | 说明 |
|---|---|
| `--ref` | **必填** |
| `--ours` | **必填** |
| `--out` | （默认 'comparison'） |

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

### `content_guard.py`
*CutSkills · 内容守卫：起尾余量（Handles）、视觉指代词、品类禁忌表。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--timeline` | 默认 <项目>/timeline.json |
| `--library` | 默认从项目目录往上推三层 |
| `--json` | 开关 |

### `cue_guard.py`
*场记口令 / 指导语 / 重说 —— 检出并把它们挡在片子外面。*

| 参数 | 说明 |
|---|---|
| `--scan` | — |
| `--check` | — |
| `--fix` | — |
| `--words` | — |

### `edge_audit.py`
*边界审计：每一格的开头 / 结尾 / 中间，有没有「不干净」的东西。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |

### `evidence_guard.py`
*CutSkills · 台词真值守卫：timeline 里的 evidence 必须能由词级 ASR 复现。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--fix` | 用真值覆盖 evidence |
| `--json` | 开关 |

### `font_sheet.py`
*font_sheet.py — 把同一句字幕排成 N 种字体的一张对照卡，让人眼当场判。*

| 参数 | 说明 |
|---|---|
| `--text` | （默认 '不管珠海的、广州的、上海的'） |
| `--w` | 画幅宽（按成片宽度给）（默认 1080） |
| `--glyph` | 目标墨迹高（像素）。1080 宽的下沿字幕约 54（默认 54） |
| `--stroke` | 描边宽 / 墨迹高，默认 0.052（默认 0.052） |
| `--bg` | 背景色 #RRGGBB；不给就上下两种（中灰 + 近黑） |
| `--ref` | 参考片字幕裁图，贴在最上面做基准 |
| `--out` | （默认 Path('/tmp/font-sheet.png')） |

### `fonts.py`
*字体表：别名 → 文件，以及**字号反算**。*

| 参数 | 说明 |
|---|---|
| `--list` | 开关 |
| `--size` | — |
| `--width` | （默认 1080） |

### `gen_boroll.py`
*CutSkills · B-roll 图形卡生成器：三种可参数化的图形卡 → 透明 PNG 叠加层。*

| 参数 | 说明 |
|---|---|
| `--kind` | **必填** |
| `--spec` | **必填**　JSON 或 YAML 参数文件（字段表见 references/boroll-specs.md） |
| `--out` | **必填**　输出 PNG（RGBA，画幅外全透明） |
| `--canvas` | WxH，默认 1080x1920；spec 里也能写 canvas |
| `--font` | f'字体，默认 {FONT_DEFAULT}（默认 str(FONT_DEFAULT)） |

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

### `gen_sfx.py`
*CutSkills · 合成音效素材：一次算完落盘，之后只读复用。*

| 参数 | 说明 |
|---|---|
| `--name` | 只做这一个 |
| `--force` | 已存在也重做 |
| `--dir` | 输出目录（默认 $CUTSKILLS_HOME/assets/sfx） |

### `golden.py`
*CutSkills · golden 回归：加新能力之前，先证明旧样片没退化。*

| 参数 | 说明 |
|---|---|
| `--project` | 要纳入的项目目录，可多次（默认 []） |
| `--add` | 加进清单（写进基线文件）（默认 []） |
| `--only` | — |
| `--init` | 用当前读数覆盖基线 |

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

### `lock.py`
*方案锁：拍板的那一刻，把时间轴钉死。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--lock` | 开关 |
| `--verify` | 开关 |
| `--status` | 开关 |
| `--note` | — |

### `match_font.py`
*match_font.py — 认一支片子的字幕/花字用的是哪一种字体。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填** |
| `--t` | **必填**　取帧时间（秒） |
| `--text` | **必填**　该帧的字幕文字；多行用 | 分隔 |
| `--band` | 只在画幅的这个纵向区间找字（比例）。默认 0.55–1.0（默认 [0.55, 1.0]） |
| `--top` | （默认 6） |
| `--crop-out` | 把参考字幕行裁出来存成图，方便人眼复核 |

### `measure_pack.py`
*读像素的包装裁判：量出成片**实际长什么样**，和包装模板声明的区间对账。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填** |
| `--pack` | **必填** |
| `--shell` | — |
| `--json` | — |

### `mix_sfx.py`
*CutSkills · 按时间点把音效混进一条人声轨。*

| 参数 | 说明 |
|---|---|
| `--audio` | **必填**　人声轨 wav（混音的底） |
| `--cues` | **必填**　cues.json：[{t, name, gain_db}] |
| `--out` | **必填**　输出 wav |
| `--sfx-dir` | 音效目录（默认 $CUTSKILLS_HOME/assets/sfx） |
| `--ceiling-db` | f'混后峰值硬顶（默认 {CEILING_DB} dBFS）。人声轨若已经过 loudnorm，它的样本峰本身就贴着 -1dBTP，硬顶设得更严只会把音效压没（默认 CEILING_DB） |

### `pack_style.py`
*包装模板 → 渲染参数 + 壳体叠加层。*

| 参数 | 说明 |
|---|---|
| `--pack` | **必填** |
| `--canvas` | （默认 '1080x1920'） |
| `--fps` | （默认 30） |

### `pick_pack.py`
*CutSkills · 包装选择：按实测规则，从一套包装里挑出「字幕系统 + 壳 + 卡」。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填**　项目目录（_cutskills/projects/<slug>） |
| `--library` | 素材库根；不给就按项目路径往上找 |
| `--set` | 包装套 yaml，默认 styles/set-qizao.yaml |
| `--json` | 打印机器可读结果 |
| `--no-write` | 不写 pack-choice.md |

### `pick_take.py`
*CutSkills · 一个论点有好几个 take 时，选哪个。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--about` | **必填**　要找的论点关键词 |
| `--top` | （默认 5） |
| `--for` | talking=挑露脸镜头（排除画面 audio_only/ng）；vo=只取旁白声音（不管画面评级） |
| `--json` | 开关 |

### `plan_card.py`
*剪辑 + 包装方案：动手之前先让人看一遍、拍一次板。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--root` | （默认 str(Path(__file__).resolve().parent.parent)） |
| `--approve` | 人看过之后拍板：写 approval.json，render 才放行 |
| `--show` | 开关 |

### `probe_packaging.py`
*CutSkills · 包装探针：从参考片里量出「包装」这一层的规格。*

| 参数 | 说明 |
|---|---|
| `--dir` | **必填** |
| `--out` | 默认 <dir>/_packaging |
| `--only` | 只跑文件名含该子串的 |

### `ref_frames.py`
*CutSkills · 参考片取样：把一支片子的「包装信息量最大的那些帧」拼成联系表，交给视觉模型看。*

| 参数 | 说明 |
|---|---|
| `--dir` | **必填** |
| `--only` | — |

### `refit_text.py`
*把每一格的 `evidence` 按 ASR 字流重算——**改过剪切就必须重跑这一步**。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--check` | 只报差异，不写回 |

### `render_hf.py`
*C 段主链路：edl.json → HyperFrames 合成（HTML）→ mp4。*

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
| `--bgm-mood` | auto＝按 brief 里讲的事挑情绪（默认）；也可指定（默认 'auto'） |
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

### `render_style_preview.py`
*CutSkills · 包装样张：用 token 画四张「同一条片不同层」的示意帧，30 秒出全套。*

| 参数 | 说明 |
|---|---|
| `--pack` | **必填** |
| `--out` | — |

### `review_sheet.py`
*CutSkills · 目判工具：把成片拆成「能看的一页」，逼人（和我）真的看图。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--film` | — |
| `--all` | 一次出全套：总览 + 全片 24 帧 + 两版变体 |
| `--sheet` | 全片均匀 N 帧拼一张 |
| `--stills` | 原尺寸帧时刻 |
| `--motion` | 转场区间 t0 t1 |
| `--variants` | mute,subs-only |
| `--cols` | （默认 5） |

### `schema_upgrade.py`
*CutSkills · timeline.json v4 → v5 迁移。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--timeline` | （默认 'timeline.json'） |
| `--write` | 开关 |
| `--plan` | — |
| `--pack` | — |

### `selftest.py`
*端到端自测：从零造一个小素材库，把 A→B→C→D 整条链跑一遍。*

| 参数 | 说明 |
|---|---|
| `--dir` | （默认 f'/tmp/cutskills-selftest-{_station()}'） |
| `--keep` | 跑完不删（想看成片时用） |

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

### `skeleton.py`
*从参考片抽「节拍骨架」：切点、字幕带、大字、介质变化、推断的 beats、音频底子。*

| 参数 | 说明 |
|---|---|
| `--video` | **必填**　参考片路径 |
| `--out` | **必填**　输出 JSON 路径 |
| `--transcript` | 可选：SRT/VTT/JSON 转写；不给就自动找同名字幕或 _cutskills/index.json |
| `--json-only` | 不打印人类可读的表 |

### `snap_cuts.py`
*CutSkills · 切点吸附：把一个切点的 100ms 级漂移，用「挪声音」而不是「裁画面」修掉。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--apply` | 真写回 timeline.json |
| `--max-delta` | （默认 0.25） |

### `speech_hygiene.py`
*CutSkills · 口播洁净度：进片的声音里不许有片场残渣。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--json` | 开关 |

### `stability.py`
*画面稳定度曲线：某一秒的这个人，是站定了在说话，还是正在转头挥手。*

（无参数）

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

### `subs.py`
*CutSkills · 字幕：按呼吸组切、去标点、单行、强调词变色。*

（无参数）

### `sync_check.py`
*CutSkills · 声画同步裁判：Audio-First 唯一能算的那三个数。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--map` | — |
| `--timeline` | — |
| `--tolerance` | （默认 TOL_MS） |
| `--json` | 开关 |

### `test_av_lock.py`
*口播段音画同源：画面裁切点必须和声音裁切点逐帧一致。*

（无参数）

### `test_blind_negative.py`
*CutSkills · 盲测收口与阴性防误杀测试套件 (P4)*

（无参数）

### `test_board_clips.py`
*T-010 回归：clips.ov 对账不许被 overlay 推歪（主轨起点只看视频格）。*

（无参数）

### `test_brand_safety.py`
*CutSkills · T-007 品牌安全与第三方字样处置回归测试*

（无参数）

### `test_content_guard.py`
*content_guard 的合同测试：三条新判据该红时红、该绿时绿。*

（无参数）

### `test_evidence_guard.py`
*evidence_guard 的合同测试：判据会不会在**该红的时候红、该绿的时候绿**。*

（无参数）

### `test_hooks_policy.py`
*CutSkills · T-006 钩子策略真判定回归测试*

（无参数）

### `test_hygiene_fixtures.py`
*负样本夹具的**真音频**验证：判据在真声音上还能不能抓住。*

（无参数）

### `test_pip_judge.py`
*T-012 回归：PiP 判据的三层负样本必须红（缺键 / 贴回失败 / 数量不足）。*

（无参数）

### `test_speech_hygiene.py`
*speech_hygiene 的合同测试：五类残渣必须各自被抓到。*

（无参数）

### `test_visual_grade.py`
*CutSkills · T-015 口播「声 / 画双评级」与露脸门禁 · 合同测试。*

（无参数）

### `thesis.py`
*CutSkills · 观点先行：先定「一句话讲完什么」，再回头挑素材。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--lib` | 素材库根目录；默认从项目路径往上找 _cutskills |
| `--init` | 开关 |
| `--match` | 开关 |
| `--show` | 开关 |

### `timeline.py`
*CutSkills · plan.json + brief.json → timeline.json（元素时间轴 v4）。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--library` | — |
| `--plan` | — |
| `--brief` | — |
| `--style` | — |
| `--skeleton` | — |
| `--fill` | 没有画面的论点插章节卡补位 |
| `--fit-skeleton` | 长过骨架 p90 的元素裁到字界 |
| `--from-edl` | 把 v2 edl.json 升成 v4 |
| `--dry-run` | 只打印，不落盘 |
| `--show` | 开关 |

### `visual_grade.py`
*CutSkills · 口播段「声 / 画」双评级与露脸门禁（T-015 · 原则 A2）。*

（无参数）

### `yield_card.py`
*CutSkills · 良率卡：一条片出片时打一张卡，累积成趋势。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--style` | — |
| `--hook` | hooks.json（前 3 秒 A/B 结果） |
| `--blind` | blind.json（盲测聚合结果） |
| `--no-verify` | 不重跑 verify_film，读旧报告 |
| `--json` | 开关 |
