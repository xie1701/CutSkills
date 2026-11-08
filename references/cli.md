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
| `--crf` | （默认 18） |
| `--no-punch` | 不做推镜切：口播长段保持一镜到底（口播中位会超标，只在刻意要长镜头时用） |

## D · 验收

### `verify_film.py`
*CutSkills · D 段验收：拿成片和风格包对账。*

| 参数 | 说明 |
|---|---|
| `--project` | **必填** |
| `--style` | — |

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

### `bench.py`
*CutSkills · 产线基准：量「产出变得可重复」这件事本身，不量这一条片好不好。*

| 参数 | 说明 |
|---|---|
| `--library` | **必填** |
| `--project` | **必填** |
| `--plan` | 默认用项目里的 plan.json |
| `--render-only` | 只量渲染+验收（改一段的返工成本） |
| `--show` | 只打印历史 |

### `gen_boroll.py`
*CutSkills · B-roll 图形卡生成器：三种可参数化的图形卡 → 透明 PNG 叠加层。*

| 参数 | 说明 |
|---|---|
| `--kind` | **必填** |
| `--spec` | **必填**　JSON 或 YAML 参数文件（字段表见 references/boroll-specs.md） |
| `--out` | **必填**　输出 PNG（RGBA，画幅外全透明） |
| `--canvas` | WxH，默认 1080x1920；spec 里也能写 canvas |
| `--font` | f'字体，默认 {FONT_DEFAULT}（默认 str(FONT_DEFAULT)） |

### `gen_sfx.py`
*CutSkills · 合成音效素材：一次算完落盘，之后只读复用。*

| 参数 | 说明 |
|---|---|
| `--name` | 只做这一个 |
| `--force` | 已存在也重做 |
| `--dir` | 输出目录（默认 $CUTSKILLS_HOME/assets/sfx） |

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

### `selftest.py`
*端到端自测：从零造一个小素材库，把 A→B→C→D 整条链跑一遍。*

| 参数 | 说明 |
|---|---|
| `--dir` | （默认 '/tmp/cutskills-selftest'） |
| `--keep` | 跑完不删（想看成片时用） |
