---
name: cutskills
description: |
  素材库驱动的出片流水线：素材文件夹进来一次、索引一次、反复取用，然后按编导意图出片。人只在两处出现——给意图、确认文字稿。四段：A 索引（探针/抽帧/画质/规格与编码/词级转写+语义纠错/分级取用）、B 决定（纸面剪辑 + EDL，唯一人工确认点）、C 成片、D 验收（硬指标 + 切点自检 + 片段级返工）。触发词：「剪这条片」「出片」「素材索引」「素材整理」「素材库怎么管」「这批素材都是什么规格」「横屏竖屏分一下」「转写错字好多」「把素材挪到移动硬盘」，或者丢来一个素材文件夹加一句剪辑需求时。基座是 video-use 的剪辑规则，索引与纸面剪辑由本 skill 自带脚本承担。
metadata:
  category: video
  base: video-use
  stage: 'A/B/C/D implemented (v1 — speech + single_cam_talking)'
---

# CutSkills

**一个素材库，一条产线。** 用户发一句话需求 + 一个素材文件夹，产出一条能发的成片；
库里没有的画面由代码造，风格在开剪之前就选定。先只做单人口播/旁白，其余片型留接口。

四层，按「什么时候可能被推翻」切，不按技术步骤切：

| 层 | 做什么 | 产出 | 谁可能推翻它 |
|---|---|---|---|
| **1 入口** | 对话即需求：给谁看 / 他现在信什么 / 要他怎么变 / 多长 / 什么风格 | `brief.json` | 用户一句话 |
| **2 理解** | 需求 × 素材：三个 3 秒钩子候选、可用 / 不可用 / 缺口三栏 | `hooks.json`、`gaps.md` | 看完钩子 |
| **3 构形** | 结构 → **元素时间轴**（真实素材 / 代码生成 / 图片 / 纯文字 / 空，混排） | `timeline.json` | 看纸面稿 |
| **4 成型** | 渲 × 验 × 良率卡 | `out/film-hf.mp4`、`yield-card.md` | 看片 |

旧的四段（A 索引 / B 决定 / C 成片 / D 验收）**没有废**，它变成了第 4 层内部的分工——
索引层和验收层就是当年拿事故换来的那 2,700 行 + 19 项像素断言，继续用，不重写。
被换掉的是「中间那层」：v2 的 `edl.json` 是一条**镜头列表**，它默认片子是「从库里挑出来的」；
一旦承认素材不足要靠代码造，这个默认就不成立。

## 契约：真值源与视图

| 文件 | 是什么 | 谁来写 |
|---|---|---|
| `index.json` | 素材事实（写一次，增量，可全量重建） | A 段脚本 |
| `brief.json` | 需求四格（唯一真值源，缺格必有 `assumed[]`） | `brief.py` |
| `timeline.json` | **元素时间轴（v4）**：第几秒到第几秒画面上是什么 | `timeline.py` / `board_patch.py` |
| `styles/*.yaml` | 风格包参数（跨片复用，只存参数不存 CSS） | 人 |
| `board/board.js` | **看板视图**（人看、拖、批），从 timeline 导出 | `board_from_timeline.py` |
| `lock.json` | 拍板时的时间轴哈希。渲不渲看它 | `lock.py` |

`paper-edit.md`、`gaps.md`、`verify-report.md`、`yield-card.md`、`decisions.md` 全是**视图**，
从上面几个生成。视图能被手改，手改完和真值源不一致，整条流水线就开始撒谎——视图永远可以删掉重建。

Schema：[index-schema.md](references/index-schema.md) · [timeline-schema.md](references/timeline-schema.md) · [edl-schema.md](references/edl-schema.md)（v2，兼容）。升级旧时间轴分两种：停在 v4 的用 `scripts/schema_upgrade.py --project <slug> --write`（只加字段）；**只有 `edl.json` 的 v2 项目先跑 `scripts/timeline.py --project <slug> --from-edl --library <素材库根>` 再从 v4 升 v5**——`schema_upgrade.py` 自己从无到有造不出来（没有 `timeline.json` 就退出 1），步骤见 [timeline-schema.md](references/timeline-schema.md) 的「v2 项目升级的四步」

台词真值口径（`evidence` 必须是说过的话，不是写顺了的话）见 [plan-ng-2026-11-12.md](references/plan-ng-2026-11-12.md)；字体与字号校准见 [styles/fonts.yaml](styles/fonts.yaml)。

台词真值 / 口播洁净度 / 声音在前，三条判据各有一个脚本和一个合同测试；口播段的「声/画双评级」由 `scripts/visual_grade.py`（`grade_of()` / `gate()`）负责，规则见 [editorial-packaging-principles.md](references/editorial-packaging-principles.md) 的原则 A2：

```bash
python3 scripts/evidence_guard.py --project <项目>      # evidence 必须是说过的话
python3 scripts/speech_hygiene.py --project <项目>      # 口水 / 半句起头 / 接缝重复
python3 scripts/silence.py <素材> <in> <out>            # 量一段窗口里真有多长的静音（相对阈值，底噪高也量得出）
python3 scripts/check_timing.py   --project <项目>      # 卡比锚词晚 0.15s、字幕不早于语音
python3 scripts/pick_take.py --library <素材库> --about 关键词   # 同一个论点选哪个 take（--for talking 挑露脸 / --for vo 只挑声）
python3 scripts/stability.py                            # 画面稳定度曲线（跳切切点避开转头挥手）
python3 scripts/test_av_lock.py                         # 口播段音画同源（画面起点被清零＝嘴形全错）
python3 scripts/test_visual_grade.py                    # 声/画双评级：audio_only（拿稿/看场外）挂 talking 必红，改 broll 留声放行（T-015）
python3 scripts/edge_audit.py --project <项目>          # 提词半截词 / 尾巴挂下一句 / 话停了画面还在演（并列列举、单字虚词、句内排比不判）
python3 scripts/test_edge_audit.py                      # 孤立提示词正负样本：并列列举不红 / 重起句必红（T-037）
python3 scripts/check_cards.py --project <项目>         # 卡：时长 / 词证 / 不复述 / 密度（渲完才跑）
python3 scripts/check_overlay.py --project <项目>       # 叠加态：卡压脸 / 口播格整屏暗场 / 重卡撞字幕（渲前闸门，error 退出码 7）
python3 scripts/broll_split.py --projects <项目...>     # 一组片子：B-roll 与口播不许跨片共用
python3 scripts/test_hygiene_fixtures.py                # 五段真音频夹具（判据不许改松）
python3 scripts/test_content_guard.py                   # 内容守卫负样本（0.00s 糊片 / 四张图配工地 / 夸赞撞生肉血水）
```

**词级真值只有一个入口：`evidence_guard.chars_of_asset(lib, asset_id)`。** 它先按 `h12(id)` 找 ASR 缓存，找不到再按索引里的 `legacy_key`（首次建索引时的路径哈希）找——**两套键都要查**；都没有才退句级，并且把 `source="sentence"` 带回来，调用方必须打一条「该段没有词级真值，已退句级比对，可能漏判」。谁自己拼 `cache/asr/` 路径，谁就会在素材改过名之后悄悄退回句级（T-030 实测：柒造库 102/153 个素材只落在 `legacy_key` 下，四个判据全部假绿）。键的规则见 [index-schema.md](references/index-schema.md) 的「缓存命名」一节。

## v4 主链路：一条命令一层

```bash
source scripts/runtime.sh env                       # 依赖全在这，任务第一件事

# 1 入口：四格 + 片长三档（30 秒系列 / 60–90 秒口播 / 自定）+ 风格挑一个
python3 scripts/brief.py --project <项目>            # 交互；--set 可非交互，--show 看现状

# 2 理解：先定主张（观点先行），再出音频块（声音是时间基准），再出 3 秒钩子
python3 scripts/thesis.py --project <项目> --init    # 写 claim / claims：这一步人做，脚本不代笔
python3 scripts/thesis.py --project <项目> --match --show   # 匹配证据、报缺口、判「要不要倒序」
python3 scripts/audio_map.py --project <项目>        # 声音切块：半句/气口/重音，带能量与填充词
python3 scripts/cue_guard.py --check <项目>          # 场记口令/摄影师指导语/重说：窗口里夹着就拦
python3 scripts/cue_guard.py --scan <素材库>         # 建库时先扫一遍，看哪些段带口令（词级定位）
python3 scripts/arrange.py --project <项目>          # 四套结构方案挑一个（原顺序/结论前置/现场切入/倒序）
python3 scripts/skeleton.py --video <参考片> --out <项目>/skeleton.json   # 节拍骨架
python3 scripts/hooks.py --project <项目> --render   # 2–3 个前 3 秒候选 → hooks/，逐个 3 秒渲出来
# 怎么测、几个人看算过：[references/hook-test.md](references/hook-test.md)

# 3 构形：意图 → 元素时间轴（缺口按兜底链自动补 gen / 章节卡）
python3 scripts/timeline.py --project <项目> --fill --fit-skeleton
python3 scripts/check_timeline.py --project <项目> --library <素材库根>   # error 必改，warning 要能说出理由（含第三方品牌字样排查与核销）；索引没解析到会报一条黄，此时的 error 0 不算数
python3 scripts/content_guard.py --project <项目>      # 内容守卫：B-roll 起尾余量 0.40s / 视觉指代词 / 品类禁忌（check_timeline 已自动调，单跑看细节）

# 3.4 B 轴连续场景：marks 别拍脑袋，按旁白锚词标定
python3 scripts/calibrate_marks.py --project <项目>           # 读 props.anchors 锚词 → 字级时间戳推 marks
python3 scripts/calibrate_marks.py --project <项目> --write   # 采纳写回（锚词缺失/顺序冲突保持原值并如实报）
# 人物小窗：B 轴 scene 格带 props.pip（box 圆角卡默认 / cutout 浮人）；
# 字段说明见 references/timeline-schema.md 的「B 轴专用字段」表。

# 3.5 给人审：出看板 → 人拖完批完 → 补丁落回 timeline → 上锁
python3 scripts/board_from_timeline.py --project <项目>       # 出 board/board.html + 声音轨
python3 scripts/board_check.py --project <项目> --xml --md     # 14 条不变式 + XML 应有对照表
python3 scripts/test_board_clips.py                          # clips.ov 对账回归：overlay 不推歪主轨起点
node scripts/board_smoke.js <项目>/board/board.html <项目>/board   # 真开一次浏览器：报错/截图/导出 XML
pbpaste | python3 scripts/board_patch.py --project <项目> --dry-run  # 粘回来的评论 → 看 diff
python3 scripts/lock.py --project <项目> --lock --note "认可"         # 拍板（哈希钉死时间轴）

# 4 成型：渲 → 验 → 良率卡
python3 scripts/bgm_gen.py --project <项目> --mood auto     # 按 brief 挑情绪（渲染时也会自动调）
python3 scripts/bgm_gen.py --analyze <项目>/bgm.wav         # 量亮度/低频：谱质心 <350Hz 就是闷
python3 scripts/render_hf.py --project <项目> --extractor own --quality looks --bgm auto
python3 scripts/verify_film.py --project <项目>
python3 scripts/test_pip_judge.py                     # PiP 判据三层负样本（T-012：缺键/贴回失败/数量不足）
python3 scripts/test_overlay_judge.py                 # 叠加态判据三条负样本（T-019：压脸/口播整屏暗场/重卡撞字幕）
python3 scripts/sync_check.py --project <项目>              # 同步率 / 断章率 / 卡片锚词只许晚
python3 scripts/check_subs.py --project <项目>              # 字幕：无标点/不断词/≤10 单位/0.8–3.2s（单呼吸组 4.5s）/助词孤行/重叠/硬切 + 残词/口吃重尾/荒谬量级门禁（T-017）
python3 scripts/hooks.py --project <项目> --kit            # 给人看的评审包（2/3 人同选才算过）
python3 scripts/hooks.py --project <项目> --vote B,B,A     # 记真人投票 → hooks.json 的 passed
python3 scripts/hooks.py --project <项目> --check          # 校验前 3 秒策略合规性与通过性（0: OK/WARN, 1: FAIL）
python3 scripts/test_hooks_policy.py                      # 前 3 秒策略真判定回归（正负样本与持久化）
python3 scripts/test_brand_safety.py                      # 品牌安全与第三方字样处置回归（T-007：FESTOOL/PPG 拦截与核销）
python3 scripts/test_semantic_check.py                     # 语义校对闭环回归（T-017：欧阳三片真错字检出/清零/否定字硬拦/零误杀）
python3 scripts/test_hold_breath.py                        # 字幕停留的单呼吸组豁免回归（T-029：3.3/3.6s 连续话音判过，2s 说完赖到 3.4s 仍判红）
python3 scripts/test_breath_split.py                        # 拆条按实测静音的回归（T-032：0.35s 停顿必拆、0.10s 停顿不拆、缺素材不静默、同一窗口走缓存）
python3 scripts/yield_card.py --project <项目> --hook <项目>/hooks.json --blind <项目>/blind.json

# 阈值校准（改判据之前先量参考片自己的读数，别拍脑袋定标准）
python3 scripts/calibrate.py --video <参考片> --tolerance-from 0.85

# 横向：加任何新能力之前先跑回归，别把上周的片弄坏
python3 scripts/golden.py
```

`golden` 现在仍退出码 1：三支旧样片的红已逐条归因（5 条是旧字幕过期，1 类是慢语速单呼吸组被「停留 3.2s」误判），重渲被「卡缺锚词」与「`proj79` 是 v2」两处闸门挡住。逐条证据与拆单见 [references/fixes-t024-golden.md](references/fixes-t024-golden.md)。

老项目的卡缺 `on` 锚词时，`check_timing` 会直接拦住重渲；逐卡锚词怎么从词级真值里定位、哪张卡根本找不到声音依据，见 [references/fixes-t027-anchors.md](references/fixes-t027-anchors.md)。

### 第 4 层的另一半：声音是时间轴的主人

画面事件不按秒摆，按**音频块**摆。`audio_map.py` 把声音切成半句级块并给出重音点，
`sync_check.py` 量三个数：同步率（视觉事件落在词边界 −152ms~+40ms 的比例，≥85%）、
断章率（一口气里被切刀的比例，≤15%）、大字偏移（大字 vs 重音，≤120ms）。

这三条不是自我感觉——2026-11-09 一上来就量出「切点永远慢 120ms」「大字慢 486ms」
「对比屏压错位置 810ms」，三个都改完才转绿。

**但校准之后要改口**：拿参考片跑同一套算法，它自己的同步率只有 18%（切点 p50 差 418ms）——
因为它的画面切点跟着节奏走、话说一半就切。所以我们这 88% 验的是「自家切点没漂」，
不是「比参考片强」。断章率我们 11%、参考片 24%：想更狠可以放开到 25%，那是口味不是质量。
细节、数字与阈值来由见 [references/audio-first.md](references/audio-first.md)。

观点怎么先定下来（主张 → 证据 → 缺口 → 要不要倒序）见 [references/thesis.md](references/thesis.md)。
卡片为什么「只许晚不许早」、大字怎么压到人物脑后关、每张卡配什么音效，见
[references/audio-first.md](references/audio-first.md)；字幕五条规矩与实现见
[references/subtitle-rules.md](references/subtitle-rules.md)；版式五条硬规则（单行 ≤10 展示单位、
每条 0.8–3.2s、单呼吸组可到 4.5s、助词不孤行、无底条）见 [references/typography.md](references/typography.md) 第五节。

**MG（代码画的动效卡）**是包装的第三块：基础卡 `keyword-big` / `stat` / `steps` / `versus` / `term` 与修辞扩充卡（`proportion` 比例条 / `day-night` 时段接力 / `chips` 并列招牌）。
选哪张由这一格的**话语修辞关系**决定（形状即语义：互补经营不许误用对抗 `VS`，常识菜名不许误用百科词条），不由风格决定；风格只决定它的**视觉语域（Visual Register）**与 `tokens`（餐饮茶楼用暖食烟火底，严禁套冷黑蓝科技弹窗把食物压灰）。
叠加空间只允许三种合法态（轻量挂角 `corner` / 底片模糊退后 `focus_blur` / 整屏纯底切出 `cutaway`），**严禁在清晰露脸口播或美食特写正中糊全屏暗场大卡**；位置不再由固定百分比带决定，由 `scripts/card_layout.py` 逐卡读人脸框（`scripts/native/person_mask --faces`，Vision）算出来，判据落在 `scripts/check_overlay.py`（渲前拦停，error 退出码 7）；重型卡在场期间底部同期声字幕自动退场，每条挂起都记进 `out/subs.json` 的 `suppressed`（卡 id + 理由）。人工要压位置就写时间轴的 `card.layout`。
卡型、字段、选型顺序与硬规矩见 [references/mg-library.md](references/mg-library.md)；影视剪辑反 NG 五原则与广电包装五原则的完整产线准绳见 [references/editorial-packaging-principles.md](references/editorial-packaging-principles.md)。

**现成四套包装包**：`P1-warm`（暖实拍）、`P2-paper`（纸感双语）、`P3-dark`（深色科技，大字即字幕）、`P4-canton-warm`（暖食烟火·广式茶楼：琥珀深茶棕卡面 + 局部暖棕柔化 + 暖金 `#E5A93C`/朱砂 `#B8332A`，严禁冷黑蓝弹窗底与冷荧光黄）；卡面底板/遮罩/描边/字色全写在包的 `tokens.surface` 里，渲器不再自带语域。

**反 NG 与声画一致性硬闸**：单人口播在索引与选句层拆为「声/画双评级」（手持台本纸、看场外、切头切肩、闭眼帧一律标 `audio_only`，只许留声做旁白，禁止挂 `talking` 露脸）；实拍 B-roll 强制预留 ≥0.40s 起尾余量（Handles），禁止从 `0.00s` 按键糊片起切；台词含「四张图/效果图/睇呢度」等视觉指代词而库内无对应画面时，直接报缺口拦停，禁止拿工地/生肉等违和空镜硬顶造成「音画反讽」；字幕上屏前必须过整稿等长语义常识校对。

**别人的话不许进片**：真实素材里夹着场记口令（「三二一」）、摄影师的指导语（「再来一次」
「慢啲讲」）、主播自己的重说。ASR 照声音抄，按句边界切就会把它们带进片子——
画面上是主播在听指挥。`scripts/cue_guard.py` 用词级时间戳定位，检出三类并在
`--check` 时拦下；`--fix` 只让端点让步（口令卡在中间要拆镜或换素材，机器不自作主张）。

### 第 3 层的关键：一格可以是「造出来的」

`timeline.json` 的每一格带 `kind`（`clip` / `image` / `gen` / `silence`）和 `track`（`video` / `overlay`）。
`gen` 是代码画的整屏：文字类 keyword / compare / number / steps / quote / chapter / chart，
**动画类 flow（工序流程，节点逐个亮起）/ layers（剖面，从下往上叠）**，
以及 **scene（场景生成器：一件事在变，见下节）**——后三个顶替库里没有的 B-roll。
参数化、字数有上限、时长有下限，子件按顺序逐条进场（不是一屏同时亮）。

`track: overlay` 是**盖在口播上**的造法：不占时间、不换画面、声音不断。
为什么需要这一档：整屏滑屏要吃掉 4 秒旁白，而口播片里没有 4 秒是空的。
实测过这件事——把对比屏插成独立一格，验收立刻报「最长无字幕空档 4.3s」。

兜底链**写死，不许跨级**：索引里的 clip → 索引里的照片 → 代码生成 → 刻意的空 → 报缺口给用户补拍。
「硬塞一张不相关的图」不是兜底，是骗人。

### 代码画的画面：场景生成器 `scripts/gen_scene.py`

上一条的模板本质都是**整屏图文卡**——一块底、几行字、进出动效，观众看到的是「PPT 翻页」。
真正让画面自己讲事的，是场景函数：`scene_xxx(t, W, H, dur) -> Image`，输入只有「这一帧是第几秒」，
输出就是这帧。确定性（同一秒永远同帧），不依赖任何素材。

| 场景 | 演的是 | 形状 |
|---|---|---|
| `phase` | 液相 → 固相：一滩漆抹平成膜 | 同一摊东西换形态，面积不变 |
| `pour` | 原料 → 成品：三个桶汇成一股，灌进墙 | 三股落到**同一个点**，再从落点向两边铺开 |
| `measure` | 墙面 / 地面 / 任何表面 | 同一块面变三次，每次量一遍，最后整块变金 |

三条都是踩出来的规矩：

1. **同一件东西在变**，不是三张图依次切。视觉复核对第一版的判词是「像三个素材硬拼」。
2. **节拍按 `dur` 算，不写死秒数**。写死过：3 秒版到结尾墙只填了一条边。
3. **用 `--probe` 自己量，别靠眼睛**。它只看两件事：结尾是否比峰值空 40% 以上、结尾是不是几乎全黑。
   第一版 `measure` 的结尾收在一支黑框里，probe 量到亮像素只剩 0.6%——「最像成品的一帧反而最空」。

```bash
python3 scripts/gen_scene.py --scene pour --dur 3.0 --fps 30 --out <项目>/out/preview-pour.mp4
python3 scripts/gen_scene.py --scene measure --probe --dur 3.4 --out /dev/null   # 只看数，不出片
```


---

## 第五层：人能看方案、能动手改（制作看板）

> 规格写在 [references/board-fusion.md](references/board-fusion.md)。这里是入口。

人不能读 `timeline.json`。所以中间加一层**看板**：单个 HTML，能按空格预演、能拖 clip 改时长、
能拖画面元素、能量出「生成动效任务」、能导 FCP XML。**看板是视图，不是真相**：

```
timeline.json  ──I1 board_from_timeline──▶  board/board.html（人看、拖、批）
      ▲                                              │
      └────── I2 board_patch ◀──「【制作看板评论」（粘回来）
                         │
                    I3 lock（拍板 → 哈希）× render_hf（没锁不渲）
```

三条铁律：

1. **看板不估时长。** 每镜都写 `fixed` = 真实秒数（`gap` 由绝对秒反算），所以预演＝成片节奏。
2. **看板不存素材。** 素材在 `index.json`，看板只写文件名。
3. **改动必须经 I2 落地。** 浏览器里拖了什么不会自己变回时间轴。

看板引擎是 vendored 的 `vendor/production-board/`（MIT，见其 `THIRD_PARTY_NOTICES.md`）：
**只换数据块，不改样式和引擎**。`board_check.py` 验 14 条不变式；`board_smoke.js` 用真 Chrome
开一次，报 JS 错误、截图、把导出的 XML 存下来对账——「报告全绿但画面是空」吃过两次亏，不能再只读 JSON。

## 阶段 A · 索引
```bash
python3 <SKILL_DIR>/scripts/index_assets.py --library "<素材库根目录>" --jobs 4
```

产出（都在 `<素材库>/_cutskills/` 下）：`index.json`、`pickup.md`、`l2_queue.json`、`cache/thumbs/`、`cache/audio/`、`cache/asr/`；另外在**素材库根目录**出两份人能直接用的：`素材索引.md`（按批次浏览）、`素材索引.csv`（可排序筛选）。每次跑完会打印 **L2 覆盖率**（跑过 L2 的时长 / 全库时长），它同时写进 `index.json` 的 `counts.l2_coverage` 和 `素材索引.md` 开头——这是花销指标：一段 L2 就是一次视觉调用，参考上限 15%，超了要留一行原因。

**第一次跑一个库，顺序是死的**（跳步会得到「有索引但没内容词」的半个库）：

```bash
index_assets.py --library "<库>" --jobs 3          # ① L0 探针 + L1 转写，全量
index_assets.py --library "<库>" --stage finish    # ② 排 L2 候选队列，出 pickup.md
# ③ 逐条看 l2_queue.json 里的联系表，按 schema 写 _cutskills/l2/<文件名>（agent 干，不是脚本）
index_assets.py --library "<库>" --stage finish --merge-l2   # ④ 合并评分，出 素材索引.md
check_l2.py --library "<库>"                       # ⑤ 结构与归属自检
```

③ 是唯一不能自动的一步：候选段按 `--l2-cause mix` 排，一支素材默认最多进 `--l2-per-asset` 条（默认不限）。**新库第一轮先用 `--l2-cap 60 --l2-per-asset 1`**：每个素材先拿到一条内容词——这已经够 `search_library` 和命名用了，别一上来就想全库精确到秒。

**索引必须有一份人看得见的。** `index.json` 是给机器读的（几十万字符，打开就卡），只交这个等于没交——用户会找不到索引在哪。[render_library_index.py](scripts/render_library_index.py) 把同一个真值源渲染成 Markdown 和 CSV，放在素材库根目录，跟素材文件夹并排。

**素材库约定**

```
素材库/
├── 2026-11-05 珠海探店/     ← 批次名可选，就是"这批是什么"
├── 库里另一批/
└── _cutskills/             ← 系统目录，素材文件只读，绝不写回批次目录
```

批次名是弱标签不是必填；没名字用目录名兜底，分析结果负责补强它。指纹 = `id + size + mtime`（判断「要不要重算」），另有内容指纹 `uid`（判断「是不是同一个素材」）——两者分开的理由见「命名与冷存储」一节。

**三层粒度，按剪辑需要什么定，不按能分析多细定**

| 层 | 内容 | 覆盖 |
|---|---|---|
| **L0** | 元数据 + 规格/编码 + **拍摄信息（设备/位置/EXIF）**、每 2s 抽 1 帧、模糊/过曝/欠曝/黑帧/静帧、静音段、音频峰值 | 全量，秒级 |
| **L1** | 词级转写（中文精确到字）+ 气口 + 语句分段 | 全量，一次，按指纹缓存 |
| **L2** | 多模态结构化描述 + 分级取用（must-use / backup / do-not-use + 理由 + 置信度） | **只跑候选段，≤15% 时长** |
| L3 | 逐帧密集分析、抠像、追踪 | 按需，默认永不跑 |

不做全库逐帧识别：2.5 小时 4K 素材是二十多万帧，全量多模态要几小时和一笔钱，而剪辑实际取用的单位是**镜头段**，不是帧。

**分段跑，别一把梭。** 三个阶段是三种不同的资源，混在一起跑会把机器拖死（2026-11-06 实测：4 路 4K 硬件解码叠 4 路本地 ASR，10 核机器 load 冲到 277，系统重启）：

| 活 | 吃什么 | 安全并发 | 说明 |
|---|---|---|---|
| L0 解码/抽帧 | GPU 硬解 + 磁盘读 | **≤4**（`--jobs 3`） | 4K HEVC 硬解容易把核显和内存带宽吃满 |
| L1 转写 | CPU + 内存（模型加载） | **1–3**（`--l1-jobs 2`） | 每个进程都装一份模型，并发高只是互相堵 |
| 渲染/合成 | CPU + 显存 | 1 | 渲染时别同时跑索引 |

宁可慢一点分两遍跑，也不要一次性把机器打死——重启一次丢失的不只是时间，是「不知道跑到哪了」。

```bash
index_assets.py --library "<库>" --stage l0 --jobs 3       # 解码活，别超过 4 并发
index_assets.py --library "<库>" --stage l1 --l1-jobs 2    # 转写活，模型加载吃 CPU
index_assets.py --library "<库>" --stage rephrase          # 只从缓存重算句子，不重跑模型（秒级）
index_assets.py --library "<库>" --stage migrate           # 老索引补规格/指纹，不解码不转写（秒级）
index_assets.py --library "<库>" --stage finish --merge-l2 # 重算分级 + 出 pickup.md + 队列
```

**素材规格必须先标出来，再决定怎么处理。** 同一个库里往往混着三种世界：相机 8bit SDR BT.709、手机 10bit HLG / 杜比视界 HDR、以及「竖着拍、像素却横着存」的 rotation 片。三条都是「不标出来，就会在交付之后才被发现」的坑。`media_spec.py` 把 ffprobe 输出压成可判断的字段，并给出处理码：

| 字段 | 为什么要 |
|---|---|
| `orientation` | **按实际显示方向判，不是按像素宽高。** 柒造这一库 101/103 段带 rotation，103 条里 111 个素材实际是竖屏——只看 width>height 会全判反 |
| `hdr` / `hdr_base` | 杜比视界不是单纯一个 transfer 值：iPhone 常见 Profile 8.4，基底是 HLG。只按 transfer 判会误认成普通 HDR |
| `handling` | `hdr_hlg` `dolby_vision` `ten_bit` `rotated` `vfr` `non_integer_fps` `hevc` `no_audio` `display_p3` 等，码表在 `media_spec.HANDLING`，逐条写清「会出什么问题、怎么处理」 |
| `capture` | 设备、软件版本、GPS 坐标、**镜头（主摄/超广角/长焦）**、曝光参数、照片色域。来源是 QuickTime 容器标签和 EXIF，**读文件不猜**；150/153 个素材有，149 个带坐标。视频没有镜头和曝光——iPhone 容器里不写这两项，不是漏读 |

这些码不是给人看着安心的，是渲染阶段要逐条执行的：HDR 段不 tone map 直接拼 SDR 段，成片会一段灰一段正常；rotation 丢了，竖屏片会横躺。

**转写 = ASR + 语义纠错，两步都要有。** ASR 只保证「听起来像」，不保证「写对了」。柒造这一库：「瑞加」在语料里 11 次、转写里 **0 次**（被写成瑞家/瑞佳），「石灰基」30 次全写成了「石灰机」。字幕上把客户品牌名写错，比字幕晚半秒严重得多。

三层分工，判断权在大模型，不在脚本：

1. `lint_transcript.py --library "<库>"` → `_cutskills/transcript-fix.md` + `_cutskills/semantic-worklist.json`：脚本只统计，不下结论。清单第一节是**语义常识扫描**（T-017，准绳 A5）：反常物理量级（「大约有十万米高」）、同音残词（「效果好先」「生万鱼池」）、口吃重尾（「讲下下」），并直接导出「待审等长替换工单」；后面几节才是语料对账（关注词表里每个词的**同音竞争**、语料里的多字术语在转写里的缺失、高频片段、异常短语）。同音靠随包带的 `pypinyin`（`scripts/vendor/`）判断。
2. 你（大模型）读语料手册 + 那几行上下文，把决定写进 `_cutskills/lexicon.json`：`terms`（错写→正确写法）、`protect`（必须写对的专名，不参与替换）。**判断必须带背景**——读 `柒造品牌语料手册-v1.2.md` 这种库内语料，别凭空猜。标了「待人工」的条目（「大咧」可能是「大家」也可能是「大间」）必须有人读上下文再拍板。
3. `index_assets.py --stage rephrase`：按词表在字级时间轴上做最长匹配替换，秒级，不重跑 ASR。

**词表守卫（T-017）：判「谁写的」，不是只判「等不等长」。**

- `_cutskills/lexicon.json` 里的条目**是人写定的**，写下去那一次就是显式批准——不等长也照旧应用，只在报告里以 info 点名「字宽会被均摊、时间戳按新字数重分布」。`python3 scripts/lexicon.py --library "<库>"` 把这类逐条列出来（已应用的也列，别让它悄悄发生）。
- **机器扫出来的建议**（`scripts/data/suspect-words.txt`、`--term` 候选）没这个待遇：不等长必须条目自己标 `{"to": "…", "unsafe": true}`，调用方再显式 `--allow-unsafe` 才落地。
- 两条路一样：**增删否定字**（不/没/别/未/唔/冇）一律硬拦，人写的也不放过（会静默反转句意），只有读过上下文、显式 `--allow-negation-edit` 才放。

被拦的条目不会静默消失：`lexicon.py --library` 与 `_cutskills/transcript-fix.md` 会把它们连原因一起列出来。

判据全在数据里，不写死在逻辑里：残词表 `scripts/data/suspect-words.txt`（`错词<TAB>建议词<TAB>等长|不等长<TAB>备注[<TAB>排除后接]`）、量级表 `scripts/data/abnormal-magnitudes.json`（长度/重量的合理量级，分「荒谬」与「可疑」两档）。上屏前 `check_subs.py` 拿同一份表再做一道闸：命中的残词、口吃重尾、荒谬量级一律 error（「可疑」量级只提示，不判红——「三十米高的中庭」是真话）。

词表是库级资产，跟着库走、跨项目复用；新片不用重新纠一遍。

**L2 是 agent 干的活，不是脚本干的。** 脚本排好候选队列（`l2_queue.json`），每条带一张 `sheet`（三帧拼成一张联系表）和 `write_to` 路径。你逐条看那张图，按 schema 写 `_cutskills/l2/<write_to 的文件名>`，再 `--stage finish --merge-l2`。一张图一次视觉调用，别调三次。

写完先自检：`python3 <SKILL_DIR>/scripts/check_l2.py --library "<库>"`。它只查结构和归属（文件名哈希、asset、in/out 是否指向同一支素材、枚举是否合法），**查不了内容对不对**——内容对不对只能靠对着素材本身看一眼。这一条不是形式：柒造出过两支素材名字互换的事故，两条 L2 记录都写得头头是道，只有用 ffmpeg 抽一帧看原始文件才发现判的是隔壁那条。

**长段（>60s）三帧不够。** 三帧只能代表三个瞬间，一段 136 秒的口播里可能是「前半段喝茶、后半段正题」。判 grade 之前对原片抽一两帧定点复核关键证据（`ffmpeg -ss <秒> -i <素材> -frames:v 1 ...`），比再读一遍摘要有用；判不准就在 `flags` 里写「需逐段复核」，别把三帧当全段。

**L2 不是可选项**——L0 只回答“能不能看”，能不能用要靠 L2 说清楚画面里到底是什么。对手持施工素材尤其如此：L0 会告诉你它不糊，但不会告诉你画面里那个人没戴口罩。

## 命名：可以改，但要走这条命令

**原始文件名是相机给的编号（`IMG_1042.MOV`），人看不出内容——所以要改。** 前提是两个：先有索引；改走 `rename_assets.py`（它会同步写映射）。不要用 Finder 批量改名，也不要 `mv` 完不管——那才会把引用弄断。

```
YYMMDD_批次_内容_级别_原编号.EXT
261017_地面施工_地面打磨特写_必用_IMG_1042.MOV
```

| 格 | 从哪来 | 拿不到时怎么办 |
|---|---|---|
| 日期 | 文件名里的日期 > 容器 `creation_time`（视频）> `sips creation`（照片 EXIF） | **留空**，不写「无日期」（容器时钟经常是错的：柒造的地面素材文件名写着 20261017，容器却是 2024-07-29） |
| 批次 | 一级目录名（去掉「素材/视频/照片」后缀） | 根目录素材留空 |
| 内容 | L2 的 `tag`（2–6 字名词短语，如「自流平倒料」）；没有 tag 才退回 `summary` 第一个分句 | 跳过这一格——**编一个比空着更糟** |
| 级别 | `must-use/backup/do-not-use` 的中文 | 跳过 |
| 原编号 | 第一次建索引时的文件名主干（`original_name`） | 必须保留，否则对不回相机 |

除了原编号一格信息都没有的（logo、说明图），恢复原名——加前缀只是噪声。

```bash
rename_assets.py --library "<库>"                    # 演练：打印将要改成什么，不落地
rename_assets.py --library "<库>" --apply            # 真改（同步索引 + 映射 + l2 引用）
rename_assets.py --library "<库>" --apply --fix-projects   # 顺带改项目里引用的路径
rename_assets.py --library "<库>" --revert           # 撤销最近一次
```

**什么时候改**：等内容词值得进名字了再一次改到位（柒造现在只有 7 段跑过 L2，150 个文件里 143 个内容格是空的）。不然 L2 补上后还得再改一遍名。

安全网是三层身份，改名不会丢东西：

| 字段 | 是什么 | 改名/搬家后 |
|---|---|---|
| `uid` | 内容指纹（大小 + 首尾各 4MB），`a3f9c2e1b704` | **不变**。重扫时按它认人，旧路径进 `aliases` |
| `legacy_key` / `legacy_path` | 首次建索引时的路径哈希 | **不变**，否则一次改名要把全部转写重跑几小时 |
| `id` / `path` | 当前相对路径 | 会变，这是正常的 |

改名记录写在 `_cutskills/renames/<时间戳>.json`，`--revert` 读它回滚。

**改名不是一次的事，映射要按历史合并。** 素材常常改两三遍（先按批次+级别改一遍，补了内容词再改一遍，去掉多余格子又改一遍），于是同一个文件有 `a→b`、`b→c` 两跳。`--fix-projects` 如果只用**最后一份**映射，项目里停在 `b` 的那些路径就永远改不到——症状是 `check_edl` 突然 `FAIL ... 素材不在索引里`，而名字看起来明明是对的。所以现在每次都把 `renames/` 下**全部映射合起来解链**（`a→b`、`b→c` 得 `a→c`）再改写。**就算绕过脚本手工改了名**，跑一次 `--stage finish` 也能按 uid 认回来（`~ 认得是老素材：旧路径 → 新路径`）。

冷存储：素材挪走后，在 `_cutskills/storage.json` 里标一句，索引的「位置」列就会从「本机」变成「冷存储·某盘」：

```json
{"a3f9c2e1b704": {"where": "cold", "volume": "柒造备份盘", "note": "2026-12 归档"}}
```

没标又找不到的显示「缺失（需找回）」——**不静默当成没有**。需要那一段时，索引告诉你去哪块盘取哪个文件，不用重扫整库。

## 依赖：只装一次，全部放在一个地方

每个任务各跑一遍 `npx hyperframes` / `pip install` / whisper，就会攒出十几份依赖：这台机器实测 `~/.npm` 3.5G、`~/.cache/puppeteer` 1.8G、散落的 `node_modules` 十几处，而且下次任务仍然不知道哪份能用。

规则：**下载来的东西只放 `${CUTSKILLS_HOME:-~/.cutskills}/`**，任务里第一件事先把环境导进来。

```bash
source scripts/runtime.sh env      # 幂等：PATH/NODE_PATH/PYTHONPATH/XDG_CACHE_HOME/HF_HOME 全指到这一处
scripts/runtime.sh check           # 装了什么、什么版本、缺什么、占多大
scripts/runtime.sh install         # 只装缺的（读 deps.json，写完记入 manifest.json）
scripts/runtime.sh adopt           # 把已有的旧缓存搬进来并在原位置留软链（省一次重下）
scripts/runtime.sh run <命令...>    # 带环境跑一条命令
```

```
~/.cutskills/
├── npm/          hyperframes 等 node 依赖，装一次用到底
├── npm-cache/    npm 下载缓存（含 npx 的 _npx）
├── py/           pip --target 的 python 包
├── cache/        XDG 缓存：chrome、whisper 权重
├── models/       HF_HOME / TORCH_HOME
├── bin/          自建命令软链
└── manifest.json 装了什么、什么版本、多大——防止重复安装
```

两个真实坑：

1. **`npx` 会按版本各下一套**。装成固定依赖（`deps.json` 里 `hyperframes: latest` 解析后记进 manifest），之后一律用 `runtime.sh run hyperframes ...`，不走 npx。
2. **pip 在 SOCKS 代理下会报 `Missing dependencies for SOCKS support`**。这台机器就是。所以 python 小依赖优先随包 `scripts/vendor/`（`pypinyin` 已带），runtime 的 `py/` 只是给额外包的。

## 阶段 B · 决定（v2 路线，仍可用；v4 主链路见上）

```bash
python3 <SKILL_DIR>/scripts/init_project.py --library "<素材库>" --intent "<用户原话>" --slug qizao-01 \
    [--audience 业主] [--promise "看完知道按项目定"] [--duration 40] [--aspect 9:16] [--avoid 品牌名]
```

生成 `_cutskills/projects/<slug>/`：`intent.json`（四个字段）、`paper-edit.md`（空模板）、`edl.json`（骨架）、`decisions.md`（确认留痕）。四个字段必须抽出来，抽不全就问，**最多两条**：观众是谁 / 看完要相信或做什么 / 时长与画幅 / 不许出现的内容。**抽全了一起带上去**；先建再补，就重跑同一条命令加参数（覆盖 `intent.json`，不碰别的）。

> **项目必须建在 `<库>/_cutskills/projects/<slug>` 下。** `make_edl` / `render_paper_edit` / `ab_open` 默认往上三层找素材库。放在别处（`/tmp`、另一块盘）要显式加 `--library "<库>"`，否则会以「找不到 index.json」退出——这个错以前只抛一条 `FileNotFoundError`，看不出是自己路径错了。

### 第 0 步 · 入口检查：这次是不是「有需求的剪辑」

```bash
python3 <SKILL_DIR>/scripts/intake.py --library "<库>" --project <项目> [--intent "用户原话"] [--script 稿.md] [--reference 参考.mp4]
```

拿到素材就开剪是 B 段最贵的错：方向不对时返工比重问一句贵十倍。脚本判三种：

| 结果 | 含义 | 怎么做 |
|---|---|---|
| `full`（退出码 0） | 有原话 + 稿子或参考片 | 直接进第 1 步 |
| `thin`（3） | 有原话带目标，但没稿、没参考片 | 两条路让用户挑：① 他给稿/参考（十分钟）；② 授权我按库里语料 + A 段索引出 2–3 个选题 |
| `empty`（3） | 连需求都没成形 | 先问，最多两条（给谁看 / 有稿还是让我出方向） |

他说「你来定」时走这条路：

```bash
python3 <SKILL_DIR>/scripts/intake.py --library "<库>" --project <项目> --derive   # 写 options.md：能当证据的段落 + 高频主题 + 六套骨架
```

然后你读 `options.md` 和 [references/plan-templates.md](references/plan-templates.md)，写 2–3 个选题卡（标题 / 骨架 / 库里有几个证据可指 / 大概时长），让他选。**写完必须 `--verify`**：选题里点名的素材要真在索引里，指不到就换模板，不许先许诺后找片。

之后的顺序：

1. `python3 <SKILL_DIR>/scripts/build_takes_packed.py --library ... --project ... [--include 子串]` → 生成 `takes_packed.md`（短语级、带时间码）和 `inventory.md`（无语音素材的可用段清单）。**读这两个文件，不要读 `index.json` 全文**——那是给机器看的，token 太贵。素材多时用 `--include` 只取这次要用的那几支；文件尾部出现「已截断：素材太多」就是被截了，调 `--max-chars` 或改用 `--include`，别以为那批素材没语音。稿子里带 `⚠重复` / `⚠重启` / `⚠疑似修正` 的行是脚本标出的可疑处，删不删自己定。
2. **找画面先搜，别翻清单**：`python3 <SKILL_DIR>/scripts/search_library.py --library ... --query 打磨 --query 涂刷 [--grade must-use] [--min-dur 3 --max-dur 6] [--shot close] [--people speaker] [--plan]`。它同时命中 L2 的 `tag/summary/action/people` 和转写原文，输出带素材内时间码和分级。**`--min-dur/--max-dur` 是你最常用的两个**——「我这段口播有 4 秒，需要一段盖得住的画面」是常态；`--plan` 直接吐能粘进 `plan.json` 的片段（`why` 留空，理由是你的判断，脚本不代写）。只靠翻 `inventory.md` 找画面，就是上一次把「材料搅拌」和「地面涂刷」搞反的现场。
3. 写一份 `plan.json`：每段写“从哪句到哪句”+ **`beat`（论点号）和 `role`（这段在干什么）**，不写 padding。**口播盖画面的段要写两组时间**：`asset`/`in`/`out` 是**画面**（放哪支 broll、取哪一段），`audio_asset`/`audio_in`/`audio_out` 是**声音**（从哪段口播里取话）；只写一组的话，`make_edl` 会把画面素材当声源，切出来是哑的。字段全表见 [references/edl-schema.md](references/edl-schema.md)。`role` 六值：`setup` 交代 / `claim` 主张 / `evidence` 举证 / `warrant` 说理（为什么这条证据能推出这个结论）/ `turn` 转折 / `payoff` 收束。没有这两格，`check_edl` 只能判节奏，判不了「这片子有没有论点、每个论点有没有证据」——那才是观众信不信的原因。写之前先挑一套骨架（[references/plan-templates.md](references/plan-templates.md) 里六套）。
4. `python3 <SKILL_DIR>/scripts/preview_plan.py --library ... --project ... --plan plan.json` → **试算，不落盘**：吸附后时长 vs 目标、每个论点多少段多少秒有没有证据、字数与语速、最长同质画面连续（broll 连段按素材算），超时长时给删段候选。它和 `make_edl` 用同一套算法，预览 71.2s 落盘就是 71.2s。改一条看一眼，别攒着。
5. `python3 <SKILL_DIR>/scripts/make_edl.py --project ... --plan plan.json` → 自动吸附字界、自动留 padding、自动处理相邻段互吃，产出 `edl.json`（`version: 2`，带 `beats[]`）；每次落盘前把旧 `edl.json` 存进 `edl-history/`、把 `plan.json` 存进 `plan-history/`，`--list` 看历史、`--revert` 回上一版。
6. `python3 <SKILL_DIR>/scripts/render_paper_edit.py --project ...` → 生成给人读的**按论点分节**的三列稿。
7. **把文字稿给用户看，等他确认。** 这是唯一必须的人工确认点。人读 2000 字稿子 3 分钟，看 20 分钟粗剪要 20 分钟而且看不出该删什么。
   **确认完立刻记一行**：`python3 <SKILL_DIR>/scripts/log_decision.py --project <项目> --said "用户原话" --did "据此改了什么" [--verdict accepted|rejected]`。`decisions.md` 追加、不改旧行，结论后面挂当时的段数与时长。没记的话 `check_edl` 会 warn——否则一周后没人分得清「他没提这条」和「提了没改」。
   **第三方字样处置**：施工实拍中出现的专业工具/设备/材料品牌（如 FESTOOL 费斯托 RO 150、PPG 大师漆、牧田、博世等）很多时候属于现场真实证据，无需刻意避开。剪辑时应向用户确认是否需要避开：若用户确认保留/无需避开，在 `decisions.md` 中记录留痕（如「#1 FESTOOL 作为真实施工工具证据无需避开，客户认可」）或在时间轴元素标 `third_party_action: keep`（亦支持 `crop`/`blur`），`check_timeline.py` 与 `check_edl.py` 会自动核销告警；若用户要求避开则按指令裁切或换段（否定指令如「不能保留」不会误核销）。
8. 确认后再跑一次：

```bash
python3 <SKILL_DIR>/scripts/check_edl.py --project <项目目录>   # 必须 0 error 才算过；报告写 edl-report.md
python3 <SKILL_DIR>/scripts/gaps.py --project <项目目录>        # 缺什么，写成给人看的 gap.md
python3 <SKILL_DIR>/scripts/view_diff.py --project <项目目录>   # 稿子被手改过没有、计划比上次改了什么
```

9. **盲测（B 段唯一的外部裁判）**：

```bash
python3 <SKILL_DIR>/scripts/blind_test.py --project <项目目录>                # 出盲测包，只有文字、没有理由和角色
python3 <SKILL_DIR>/scripts/blind_test.py --project <项目目录> --score 回答.md
```

把 `blind-test.md` 交给**另一个人或另一个模型**（不给它 `intent.json`，不给它 why），让它回答三问：主张是什么 / 拿什么当证据 / 哪里不信。作者读自己稿子读的是「我想说的」，陌生人读的是「实际说出来的」——两者差多少，只有盲测测得出。**不许让写稿的那一方给自己打分**，所以脚本只报数（证据几条、裁判点了哪几段），不给「通过」。

**裁判至少两位，够不上就换成真人。** 理想是三种身份（普通观众 / 目标客户 / 同行）、并且换模型家族或档位——同一个模型会用同一套宽容度读你的稿子。一个人操作时：两个不同档位的模型 + 一个真人（同事、客户、家里人都行，只给他 `blind-test.md`）。**只有一位裁判时不要下结论**，那只是一个人的意见。

`--score` 收的是**裁判的原话文件**，`--score` 可以给多次：

```bash
python3 <SKILL_DIR>/scripts/blind_test.py --project P --score 裁判1.md --score 裁判2.md
```

格式没要求，整段回答原样存成 `.md` 就行；脚本只做三件事——和 `intent.promise` 对字数、抽出裁判点到的段号、把「≥2 位裁判点到同一段」单列出来。那几条才是高信号。改完**再跑一次**，看被点过的段消失了没有——这是这套机制唯一的成绩单。全流程见 [references/blind-test-playbook.md](references/blind-test-playbook.md)；盲测与防误杀自动化回归套件见 `scripts/test_blind_negative.py`（包含 20 支阴性样本与 20 支对照组断言，详见 [references/audit-p4-blind-2026-11-11.md](references/audit-p4-blind-2026-11-11.md)）。

10. **A/B 双开场**（要对照开场效果时才做）：

```bash
python3 <SKILL_DIR>/scripts/ab_open.py --project <项目目录>
```

只换开场：A「结论前置」把片子中段那句最硬的主张搬到最前，B「现场切入」先给一个能看见的现场；其余段逐字节相同，所以差别只可能来自开场。产出 `ab/compare.md`、两条 8–12 秒的开场视频，以及 `ab/blind/甲.mp4`、`ab/blind/乙.mp4`——**随机标成甲乙，不告诉对方哪版是哪个**，只问一句「哪条让你更想往下看」。

**顺序不能反**：先出稿给人看，再落 EDL 校验。反过来就会出现“机器说通过、人却不认”的情况。

详细的 B 段工作方法（怎么选段、留白多少、什么算好理由）见 [references/stage-b-playbook.md](references/stage-b-playbook.md)。

## 阶段 C · 成片（v2/v4 共用的渲染细节）

**主链路是 HTML 渲染（HyperFrames），不是 ffmpeg 合成。**

```bash
python3 <SKILL_DIR>/scripts/render_hf.py --project <项目目录> --library "<素材库>"
```

EDL 进，`hf/index.html` 出，渲成 `out/film-hf.mp4`。三件事：
**切段（ffmpeg 干）→ 排版与动效（浏览器干）→ 响度（ffmpeg 干）**。

```bash
# 先只出 HTML 并 lint，不渲（最快的一步，1 秒）
python3 <SKILL_DIR>/scripts/render_hf.py --project <项目> --segments 3 --no-render

# 出一版粗的看节奏（draft 比 looks 快一倍）
python3 <SKILL_DIR>/scripts/render_hf.py --project <项目> --quality draft

# 只渲前 N 段（换素材/调字幕时的常规动作）
python3 <SKILL_DIR>/scripts/render_hf.py --project <项目> --segments 3
```

为什么要换成 HTML（2026-11-08 决定，实测记录在
[references/hf-probe-2026-11-08/README.md](references/hf-probe-2026-11-08/README.md)）：
ffmpeg 那层**没有布局引擎**。当天所有坑都是同一件事的变体——用 PIL 量墨迹高反算字号、
没编 libass 只能画 PNG 再 overlay、静止图要 `-loop 1`、叠加不钉 `-t` 会漂 12 秒、
渐变参数穿三层函数丢了一句错都不报。HTML 里这些是 CSS 和 GSAP 曲线。

**分工不许越界**：

| 谁干 | 干什么 | 为什么不能交给对方 |
|---|---|---|
| `render_hf.py` | 从 EDL 生成 `hf/index.html`、切段缓存、调 `hyperframes render`、落 `out/subs.json` 与 `out/cards.json` | 它是唯一知道"哪一段配哪句话、卡什么时候出"的一层 |
| `render_hf.extract_own()` | 切段与标准化（HDR→709、旋转、缩放到画布） | 自带 zscale 的 ffmpeg 才能把 HLG 正确转 SDR，`measure extract` 会把彩度压掉一半（见下）|
| `render_film.build_cues` | 字幕切条与逐字对齐（导入，不重写） | 那段逻辑是踩出来的：按**字**裁段窗口、逐字对**字**、换气收口、纯标点不成条 |
| 浏览器 | 排版、字体、动效、图层顺序 | 这是换引擎的全部理由 |

### 计时（真跑出来的，别按感觉估）

| 动作 | 耗时 |
|---|---|
| 只生成 HTML + lint | ~1 秒 |
| 切 14 段（自己切，4K 素材带 HDR 色调映射） | **98 秒**；之后走缓存 |
| 切 14 段（`--extractor hf` 旧路径） | 6 分 54 秒（4K 上跑 zscale/缩放） |
| 渲 14.5 秒片（`looks`, 4 workers） | 35–105 秒（机器负载决定） |
| 渲 79 秒片（段已转 SDR，`draft` + 4 workers） | **59.6 秒**（整条命令 5 分 13 秒，含切段和响度）|
| 同一条片（HDR 素材没转 SDR 时） | 10–12 分钟 |

比 ffmpeg 那条链慢约一个数量级。**日常迭代靠 `hyperframes preview` 在浏览器里看，不靠 render。**

### 调色分两层：全局一刀 + 逐镜校正

**第一层（全局）**：`styles/talking-head-v1.yaml` 的 `grade: warm_high_sat`，在切片时
用 ffmpeg `eq` 烤进素材（`saturation=1.50:contrast=1.10:gamma=0.85:brightness=-0.086`）。
只染画面，不染字——所以不走 CSS filter。

**第二层（逐镜）**：`scripts/shot_match.py`，`render_hf.py --shot-match 0.7` 自动跑。
每镜量 luma / G÷R / B÷R，算一组通道增益往参考片拉一部分（strength 0.7），
增益夹在 [0.65, 1.35] 之间。

为什么要第二层：**整片平均对上不等于看着对**。第一版整片 luma 0.377（参考 0.381）、
G÷R 0.755（0.750）、B÷R 0.477（0.471）三个数全中，但逐段一看，口播段 luma 0.346 /
B÷R 0.372（又暗又暖），施工段 0.487 / 0.621（又亮又冷）——每换一镜色温跳一下。
参考片是逐镜拉平的，所以这一步必须逐镜做。

**对比工具**：`scripts/compare_tones.py --a <我们的片> --b <参考片> --md <报告>`，
输出 luma / G÷R / B÷R 三项，带 p10 / p90 分布，并直接给"要乘多少"。判据：
luma ±0.02、G÷R ±0.03、B÷R ±0.03。第一次跑出来的完整对比（含逐镜表）：
[references/tone-compare-2026-11-09.md](references/tone-compare-2026-11-09.md)。

**饱和度不进判据**。参考片的 0.511（旧口径）/ 0.257（逐秒抽帧口径）都是带大字和
色卡的整片均值，我们量的是画面，两个口径不同；按饱和调只会把画面拉成霓虹。

调色只动两个旋钮，一次一个：**B÷R 差 → 通道增益（或 colorchannelmixer）**，
**luma 差 → eq 的 gamma / brightness**。改完重跑 `compare_tones.py`，别靠眼睛记。

### 调色在切片阶段烤进素材，不走 CSS

2026-11-09 改的。原来调色靠 `--grade` 传 CSS filter，问题是 **CSS 会把字幕和字卡一起染**，
而调色只该作用在画面上。现在 `styles/talking-head-v1.yaml` 里写 `grade: warm_high_sat`，
`render_hf.extract_own()` 在切段时用 ffmpeg `eq` 烤进去（跟 `render_film.py` 的 GRADE
是同一条链：`saturation=1.50:contrast=1.10:gamma=0.85:brightness=-0.10` +
`colortemperature=4900:mix=0.45`）。

实测（14 段粗剪，未加字）：无调色 luma **0.547** / 饱和 0.130；
加这条链 luma **0.374**，参考片 0.381。

**饱和别拿参考片的 0.511 当目标**：那是带大字和色卡的整片，纯画面只有 0.13 起步，
硬拉只会拉成霓虹。要比就比 luma 和 G/R、B/R 两个比值。

`--grade`（CSS filter）还留着，但只用来做临时对比，不要写进正式出片。

### 素材是 HDR 时必须先转 SDR，否则整片文字层会没

柒造库 153 个素材里有 **45 个是 HLG / 杜比视界（bt2020）**。两个后果，都必须靠
带 zscale 的 ffmpeg 解决（装法见 [references/ffmpeg-zimg.md](references/ffmpeg-zimg.md)

抠像为什么不用 `hyperframes remove-background`（权重已经下好了，是**因为它更慢更糙**）：
[references/background-removal.md](references/background-removal.md)。
motion-video 那套 71 个控件的规格怎么接（引擎接不了，接规格）：
[references/motion-video-integration.md](references/motion-video-integration.md)。）：

1. **颜色**：`measure extract` 直接压成 bt709，彩度掉一半。同一帧实测饱和
   **0.082（旧）→ 0.154（zscale+tonemap）**。
2. **文字层**：HyperFrames 检测到 HDR 就走 `capture_hdr_layered`，本机 ffmpeg 没有
   zscale 时**整片字幕/身份卡/大一个字都不出**，而 20 秒以内的窗口渲染又完全正常。
   现在切片阶段就转成 SDR，这条分支根本不会被触发。

```bash
# 默认 auto：有 zscale 就自己切；没有会报错并告诉你装法
python3 <SKILL_DIR>/scripts/render_hf.py --project <项目> --extractor auto
# 退回旧路径（只在排查时用）
python3 <SKILL_DIR>/scripts/render_hf.py --project <项目> --extractor hf
```

---

## 阶段 C · 旧链路（ffmpeg 合成，保留）

`render_film.py` 还在，能用，但不再是主路径。留着它的理由有两个：一是不依赖 Chrome 和网络，
二是今天换引擎这件事可能还要退回来。它的产物是 `out/film.mp4`。

### ⛔ 包装模板层已雪藏（2026-11-08）

下面这一整节留作记录，**不要再用**。停的原因和实测数据在 [SHELVED.md](SHELVED.md)：
按参考片量出来的比例复刻，18 项绿灯、四轮迭代，得到的仍是「完全不可用」——
因为那些指标只量几何和信号，量不了"好不好看"。

当前主链路是**不带 `--pack`** 的那条：`--style styles/talking-head-v1.yaml`
（字幕 1080 宽 80px、居中 0.674H、思源黑体 SemiBold）。下面关于 `--pack` / `--shell` /
`measure_pack` / `compare_films` / `pick_pack` / `gen_boroll` 的内容，全部按"已停"读。

### 包装模板：用 `--pack` / `--shell`，不要手改像素

```bash
# 只出字幕系统（A 族）
python3 <SKILL_DIR>/scripts/render_film.py --project <项目> --library "<库>" \
  --pack pack-safe-talking --canvas 1080x1920 --fps 30

# 壳 + 字幕系统（C 族的顶条/角标/时间码叠在 A 的字幕上）
python3 <SKILL_DIR>/scripts/render_film.py --project <项目> --library "<库>" \
  --pack pack-safe-talking --shell pack-hud-monitor --canvas 1080x1920 --fps 30
```

`styles/` 里有 5 族模板（从 20 支参考片反解出来的，见 [references/packaging-families.md](references/packaging-families.md)），
外加一套给本地商家内容用的可组合套 [styles/set-qizao.yaml](styles/set-qizao.yaml)。分层是硬的：

| 角色 | 谁能当 | 说明 |
|---|---|---|
| 字幕系统（`--pack`） | A 安全口播 / B 纸感品牌 / D 双语双行 | 二选一，不许同片混两支 |
| 壳（`--shell`） | C 监控壳体 | 可选，0 或 1 |
| 卡（EDL 里的 `card`） | E 图文界面叙事 | 可选，跟着段走 |

**C 和 E 不能传给 `--pack`**——它们没有字幕系统，`pack_style.to_style()` 会直接停下并告诉你该配哪支。这条不是靠文档守，是靠代码守。

模板里存的是**比例**（字高占画幅 4.5%、顶条占 5%H），`scripts/pack_style.py` 负责翻成像素：
字号不是 `4.5%×1920`，而是用 PIL 量一次真字形墨迹高度再反算（直接乘会小 20–30%），
所以"按模板做"和"照参考片做"能对到一起。改完模板先跑一行自检，比渲一遍便宜：

```bash
python3 <SKILL_DIR>/scripts/pack_style.py --pack pack-safe-talking --canvas 1080x1920
```

包到什么程度算复刻到位：**换素材之后声明的区间还在带内**，这一条有实测存档——
[references/packtest-2026-11-08/report.md](references/packtest-2026-11-08/report.md)（10 秒、同素材换模板、8 个 token、7 个搬得走 1 个搬不动）。

| 步 | 做法 | 为什么这么做 |
|---|---|---|
| 逐段渲 | 每段单独编码到 `work/v**.mp4`（1080×1920 / 30fps / x264 CRF18），再 `concat -c copy` | 单通道 filtergraph 一次合成，改一段就得整片重编码 |
| 画幅 | `scale=...:increase,crop=1080:1920,setsar=1` | 4K 竖拍（2160×3840）和 1080p 竖拍混在一起，必须统一到画布，不能只在播放器里"看着对" |
| HDR | `colorspace=all=bt709:iall=bt2020:fast=1,eq=contrast=1.10:gamma=1.06:saturation=1.06` | 杜比视界/HLG 段直接当 SDR 拼，成片会一段灰一段正常。这台机器的 ffmpeg 没编 `zscale`，所以不走标准 tonemap，而是色彩空间转换 + 轻微对比补偿（按真实帧三版对照定下来的） |
| 声音 | 每段取自己的声源（口播段用自己的，broll 段取 EDL 里 `audio` 指定的那一支），30ms 淡入淡出，`concat -c copy` 拼 PCM | 每个切点不做淡变，必爆音；声源跟画面不是同一支素材时，必须走 EDL 的 `audio` 字段，不能想当然用画面那条 |
| 响度 | 两遍 `loudnorm`（先量后归）→ -14 LUFS / 真峰 ≤ -1 dBTP | 一遍过会因动态范围变化而偏；两遍才稳 |
| 字幕 | PIL 用真字体（阿里妈妈数黑）画透明 PNG，按 `enable='between(t,a,b)'` overlay | **这台机器的 ffmpeg 没编 libass/drawtext**（`-h filter=ass` 报 Unknown filter），`subtitles=` 和 `drawtext=` 都用不了。PIL 画出来字形不走样，还能逐条看完再改 |

**叠加层的两张牌，别混。**

| 类型 | 跟什么走 | 写在哪 | 长什么样 |
|---|---|---|---|
| **字幕** | 话音，一个字一个字 | 从 ASR 自动生成 | 底部 80px 白字，一行不超 12 字 |
| **字卡** | 事实，一整句静止 | EDL 段里的 `card` 字段 | 字幕带上方的黑底圆角块，一句话 ≤24 字 |

字卡装的是**口播里没说、但观众需要看见的东西**：数字、对比、专名。它不是字幕的加强版——字幕跟着话音走，字卡是写上去的一块牌子。观众听到「原料成本差四到五倍」，同时看到「客户实际报价：进口比国产贵 2–3 倍」，这两句话才合起来回答了「贵多少」。

```json
{"medium": "broll", "asset": "...", "audio_in": 1727.82, "audio_out": 1734.96,
 "card": {"text": "客户实际报价：进口比国产贵 2–3 倍", "at": 0.6, "dur": 5.6}}
```

**贴字的正确做法：按镜头贴，每个输出钉死时长。** 这一条是 2026-11-07 连踩四个坑才定下来的形状，别改回去：

| 坑 | 症状 | 原因 | 做法 |
|---|---|---|---|
| 静止 PNG 不循环 | 前 20 秒有字，70 秒以后一个像素都没有 | 一张 PNG 只有 t=0 一帧，`eof_action=repeat` 撑不到那个 `enable` 窗口 | 输入加 `-loop 1 -framerate 30` |
| 三十多张图一起循环 | ffmpeg 被 SIGKILL（返回码 -9、stderr 全空），脚本还以为成功 | 无限循环解码把所有帧灌进滤镜队列 | **按镜头贴**：每次只有 1–4 个输入、两三秒画面 |
| 叠加输出不钉时长 | 成片比 EDL 长 12 秒，后面每个镜头的时间点全错位，字卡在自己的时间里怎么找都找不到 | 循环输入的帧比画面多，每个贴图层都长一点，累计漂移 | 每个贴图输出都加 `-t <镜头时长>` |
| 外部命令失败不停 | 脚本打印「成片 79.07s 字幕 38 条」，而那个 mp4 连 moov atom 都没有 | `run()` 只看 stderr 不看返回码 | `run()` 失败即 `SystemExit`，绝不带着坏文件往下走 |
| 字幕念着已经剪掉的半句 | 声音里没有「相当相当」，字幕上有；条数、时长、密度全对 | 字幕按整句 phrase 收字，没按每个字的时间戳裁音窗 | 按字裁 + 逐字对字对齐，D 段查「字幕文字是否出自本段 evidence」 |

**判据只认像素。** 上面四条每一条都会让「`subs.json` 里有 38 条字幕、渲染命令里也有 38 个 overlay、报告一切正常」和「画面上一个字都没有」同时成立。所以 D 段必须抽帧数像素（见下）。

字幕时间：`output = 字.start - seg_in + seg_start`（硬规则第 6 条）。断句认标点和 ≥0.35s 的停顿，一条不跨句；单条 ≤12 字，超了断句不缩字号。

**字幕取字要按字裁、不能按句裁。** 2026-11-07 的坑：一句 ASR phrase 只要和段的音窗沾边，就被整句收进字幕，于是成片把「进口的艺术」「相当相当」剪掉了、字幕上还留着——**声音对、条数对、时长对，四条 JSON 级检查全绿，只有读字幕的人能看出它在念一句已经被删掉的话**。两条规则：

- 每个字必须满足 `seg.audio.in ≤ 字.start < seg.audio.out` 才能进字幕；
- 句文本对字时间要**逐字对字**（在候选字里找一个能和文本完全对上的连续切片），不能只比字数——±0.08 的容差常在句尾多咬一个字，一旦字数不符就掉到「按字数均分」，那个近似会把窗口外的字重新算进来。

D 段有一条专门查这个：每条字幕的文字必须能在它所属那一段的 `evidence` 里找到，找不到判红。

产出：`out/film.mp4`、`out/subs.json`（字幕轨，可改）、`work/`（逐段中间件，改一段只重渲那一段）、`render-report.md`。

## 阶段 D · 验收

```bash
python3 <SKILL_DIR>/scripts/verify_film.py --project <项目目录> [--style <styles 里的某个 .yaml>]
```

`--style` 吃两种：渲染用的样式包，或 `styles/` 下的**包装模板**（`pack-*.yaml`）。后者会先走渲染时的同一个翻译层 `pack_style.to_style()` 再验——两边各读一套字段，迟早出现「渲的时候 0.098H、验的时候 0.070H」。模板画幅写成 `canvas.aspect`（`[9:16, 16:9]`）时，画幅/帧率一律以**成片自己的流信息**为准，模板在这两项上不发言。

在**成片**上量，不在 EDL 上量。红黄绿一页，红=必须改。

其中「字体分层」只挡得住结构错（字幕用了标题美术字、字幕和花字同一个字体），
**挡不住"这个字体丑"**——丑只能人看。新模板上线前拿 `scripts/font_sheet.py` 把候选排成
一张对照卡过一眼；想从参考片反推字体用 `scripts/match_font.py`（IoU 模板匹配，判到字体族，
源片字高不足 30px 时认不出是常态，脚本会直接说"别硬认"）。

**但「在成片上量」不等于「读 JSON」。** 字幕和字卡要抽帧看像素：`subs.json` 里有 42 条字幕、渲染命令里也有 42 个 overlay，成片上照样可以一个字都没有（见 C 段那个 `-loop 1` 的坑）。所以 verify 会抽 3 条字幕的中点看字幕带的白像素占比、抽字卡窗口的中点看深色占比，低于阈值直接判红——**读 JSON 的验收会把这种事全判成绿灯。**

| 指标 | 判据 | 为什么 |
|---|---|---|
| 画幅 / 帧率 | 与风格包一致 | 平台按画幅裁切，差一点就黑边 |
| 整体响度 | -14 LUFS ±1 | 平台会重压，进压之前差 2 dB 就等于白调 |
| 真峰 | ≤ -1 dBTP | 超了必削波，且是"听不出来但已经坏了"的那种坏 |
| 字幕密度 | 45–58 条/分钟 | 低于 40 显拖，高于 58 读不完 |
| 单条最长 | ≤12 字 | 竖屏两行字就压脸了 |
| 口播镜头中位 | 2.4s ±0.8 | 偏离太多不是风格问题，是没剪 |
| 最长同介质连续 | ≤40s | 一条片 40 秒不换介质，观众会走 |
| 黑帧 | 0 段（刻意留白除外） | 黑帧多半是渲染断了 |

### C 段踩过的坑（都是真机实测）

1. **每段时长必须落在整帧上。** `-t 4.133` 这种值每段会多出半帧，23 个镜头攒出 0.55s，成片比声轨长，字幕和口型全漂。现在先按 `round(时长×fps)/fps` 量化，再按量化值重算时间轴，最后那次编码再用 `-t total` 统一裁齐。
2. **拼容器会有余量，别拿中间件长度当成片长度。** 23 个 mp4 `concat -c copy` 出来 71.40s，而声轨是 71.13s。判等长只看**成片**，中间件偏差只提示。
3. **冷暖不统一，靠全局加暖治不好。** 口播是暖光室内、工地是日光灰，全局加暖会把本来正常的镜头一起染黄。做法是每镜缩到 1×1 量 R/B，统一到口播段的中位数，增益夹在 0.90–1.12。实测 R/B 从 1.15–1.35 收到 1.32–1.44。
4. **单机位长口播，靠"推镜切"过节奏关。** 同一段画面按 1.0× / 1.12× 交替、切点吸到字界，声音不拆——口播中位从 4.95s 降到 2.43s，听感不断。
5. **字幕密度这个指标依赖语速。** 参考片 8 字/秒才有 45–58 条/分钟；这条片子 3.7 字/秒、每条 7–9 字，期望值就是 29 条/分钟。所以 D 段判的是"和期望值差多少"，绝对数字照样写进说明。

### 包装层能做的四件事（C 段）

EDL 里每段的 `card` 决定往上贴什么。四种 `kind`，各管一件事：

| `kind` | 是什么 | 关键字段 |
|---|---|---|
| `card` | 带底衬的牌子（默认） | `text` / `at` / `dur` |
| `keyword` | 花字：从字幕里抽出来、放大、上抬的那一句 | `text` / `on` |
| `behind` | 大字压在人**背后**（人物抠像叠在大字之上） | `text` / `on`；换底用 `cutout` + `bg` |
| `boroll` | 用代码画出来的信息卡（色块章节卡 / 侧栏 / 对比条） | `spec`（JSON 相对项目目录）+ `boroll_kind` |

**`on` 是时机，不是偏移。** 写这句话里的一个词（几个字），脚本去字级时间戳里找它的起止，提前 0.12s 起、停留到该词说完为止（夹在模板 1.0–1.5s）。固定「段内 0.6s」出来的大字，对不上任何一个重音——这就是「大字出现的时机不对」。

**抠像不用下载模型**：macOS 自带 Vision `VNGeneratePersonSegmentationRequest`，`swiftc -O` 现编（`scripts/native/person_mask.swift`），首次用到时自动编译。三件事记住：

- 必须在**标准化后的 `work/vNNN.mp4`** 上抠。原始素材是横向编码 + rotation 元数据，Vision 不做旋转，抠出来人形整个跑到画面右边。
- 遮罩存**灰度视频**而不是 PNG 序列（90 张 1080×1920 PNG ≈ 300MB）。
- 遮罩要**二值化**再用（`lut=y='if(gt(val,140),255,0)'`）。软边羽化压在亮字上会透出一圈黄，看着像人物长白毛。

**整屏信息卡要铺底、字幕要让位**（卡片参数见 [references/boroll-specs.md](references/boroll-specs.md)）。 `spec` 里写 `backdrop: "#15100C"` 让 `gen_boroll.py` 铺满整幅；卡片窗口内的口播字幕整条不画——同一屏两套字，读的人当场就乱。

**字体四层各自指定**（细节见 [references/typography.md](references/typography.md)）：模板里 `subtitle.font` / `emphasis.font` /
`fonts.card` 三个字段，不写就落到默认（SemiBold / Black / Bold）。换字体**必须重算字号**——
`size_for_glyph()` 量的是真字形墨迹高，思源黑体 0.88em、阿里妈妈数黑 0.75em，沿用旧 `size_px` 会小一圈。
ttc 要写 face 序号，苹方第 0 个是 HK Regular，不是你要的那个。

**BGM 全程铺底**（`--bgm <文件>`）：先归一到 -24 LUFS 再混，人声处 sidechain 让路，混完整轨压一次
（`acompressor ratio 3.5`）再归一到 -14 LUFS。三个数对着参考片量：底噪 -27.8 dB、动态差 14.0 dB、
静音 0.2%。**没有 BGM 的片子，人声一停就是录音棚空场**——观众说不出哪不对，但会觉得"没做完"。

**音效**（`gen_sfx.py` 合成 + `mix_sfx.py` 混，细节见 [references/sfx.md](references/sfx.md)）：只给**换介质**的切点和**大字/卡出场**，再按模板 `sfx_density_per_min` 截断。每刀都给是最典型的音效疲劳（参考片实测：95 个切点只有 13 个带瞬态，还低于随机基线）。混音用 numpy 直接相加，**不要用 ffmpeg amix**——amix 按输入数归一，人声会被压下去。

### 按秒对照参考片：`compare_films.py`

```bash
python3 <SKILL_DIR>/scripts/compare_films.py --ref <参考片> --ours out/film.mp4 --out cmp/
```

`measure_pack.py` 是**静态对账**（字幕带在 0.69H 吗），它全绿的时候片子照样可以"不像"。
差的那部分在**时间轴上**：什么时候亮、什么时候静、什么时候切。这个脚本把两条片子抽成 2fps 的
曲线一起摆：亮度 / 饱和度 / 暖度 / 帧间运动 / 字幕覆盖 / 信息层覆盖 / 切点，外加同位置的配对帧。

判读：**差 5% 以上才动手**，差 1% 别理。最容易漏的三项是亮度、底噪、动态差——
它们都不在 8 个 token 里，只能这么量出来。

### 按包装模板量：`measure_pack.py`

```bash
python3 <SKILL_DIR>/scripts/measure_pack.py --video out/film.mp4 \
  --pack pack-safe-talking [--shell pack-hud-monitor] --json out/measure.json
```

只看**成片像素**，不读渲染器的 JSON；量 8 个 token 和模板声明的区间对账（字幕带中心 / 字形高 / 下沿距底 /
镜头中位 / 音效密度 / 顶条高 / 顶条字高 / 角标高）。判读只有三种：`在带内` / `带外` / `未声明`。

四个口径上的坑，量之前先知道：
- **字幕带取最靠下的那一段，不是方差最大的那一段。** 花字、章节卡比字幕更亮更跳，往方差最大的方向找会一路吃到上方那几层，把「花字 + 字幕」合成一条 9%H 的带。参考片和成片曾**同时**报出 0.0906H（正好撞 9% 上限）——两支完全不同的片子撞同一个上限，说明是尺子坏了。
- **音效密度这一项，凡带人声的片子都量不准**，只作参考、不参与判定。黑盒测音效靠能量突跳，而人声每个词都是一次突跳：参考片 126s 被测成 0.0，我们手打 9 处（6.8/分，模板 4.0–8.5）被测成 12.9。同一个数字往两个方向错，它就不是尺子。要判只有一条路：数混音用的 `cues.json`。
- **字形高度含描边。** 模板写的是字的墨迹高，实测是"字 + 2×描边"。7px 描边会让 0.045H 量成 0.052H，别当成超限。
- **白像素不能用来找字幕。** 白字压亮底（白墙、白水泥）时整行白像素和背景同量级，判据直接失效（实测报"没有字幕"）。改成判**横向边缘能量随时间的起伏**才稳；角标同理，判"同一镜头内两帧像素差 <5"（叠加层是画上去的，不动），按亮度判会被亮地面骗到 24.7%H。

**D 只覆盖能量出来的东西。** 节奏顺不顺、话说清没清，得人看一遍——报告末尾就写着这句，别让人误以为绿灯等于能发。

## 硬规则（从事故里换来的，别改）

1. **字幕最后贴**，在所有覆盖层之后，否则字幕被压在下面。
2. **切点吸附字符边界**，永不切在一个字中间。
3. **每条切边留 30–200ms**，吸收时间戳漂移（快节奏取紧，纪录感取松）。
4. **每个切点 30ms 音频淡变**，否则必爆音。
5. **逐段提取 + 无损 concat**，不要单通道 filtergraph 一次性合成——加覆盖层时会二次编码。
6. **字幕用输出时间轴偏移**：`output_time = word.start - seg_in + seg_offset`，否则拼接后全错位。
7. **转写按文件指纹缓存**，不要重复转写。
8. **词级时间戳必须与画面同源**，不许拿转写的相对时间去对画面的绝对时间。
9. **字体按层分配，四层不许共用一个。** 字幕走正文黑体（思源黑体 SemiBold），花字才允许上重字（思源黑体 Black），卡面走 Bold。标题美术字（阿里妈妈数黑、方正兰亭大黑）**小字号上会变成脏点**——2026-11-08 整片四层共用阿里妈妈数黑，位置字高四项全绿，Jerry 一眼看出"不堪入目"。细节见 [references/typography.md](references/typography.md)。

## 已知的坑（都是真踩过的）

1. **长音频必须切片，且必须重编码切片。** `coli asr` 对超长音频会返回空结果；用 `-c copy` 切 mp3 会把第一段重复写满所有分片。现在脚本用 240s + `libmp3lame` 重编码。
2. **切片偏移必须进缓存键。** 否则每片都会读到第一片的缓存，长口播的转写会凭空重复一遍——这种错在成片里看不出来，只会让内容莫名重复。
3. **转写里的纯标点要丢掉**（噪声会“听”出 `。`），并且整段语音占比 < 6% 时判为无语音，不然手持 B-roll 的环境声会变成假字幕。
4. **ElevenLabs 的 key 2026-11-06 实测因订阅欠费 401**，不要默认它能用。本地 `coli asr --model sensevoice` 出的是**字级**时间戳，中文比词级更准，速度快得多。
5. **L0 的模糊阈值在干净素材上几乎不触发**（柒造 103 段素材 blur 中位 5.1、最高 8.3，阈值 20）。这不是 bug：L0 只负责扔掉黑帧、静帧、严重失焦，判断“能不能用”是 L2 的事。
6. **这台 ffmpeg 没有 libass、没有 drawtext、没有 zscale。** Homebrew 的 9.0.2 只带 libx264/libx265/libvpx 这些，滤镜里没有 `ass`/`subtitles`/`drawtext`/`zscale`。所以字幕走 PIL 画 PNG + `overlay`，HDR 走 `colorspace` + `eq` 补偿。换机器先跑 `ffmpeg -h filter=ass`、`-h filter=zscale` 确认一遍，别照抄参数然后报"命令失败"。
7. **联系表必须按段内抽帧。** 早先按整支视频的 15/50/85% 抽，同一素材的每一段都拿到同样三帧（七段口播的联系表 MD5 完全一样），判出来的「段级差异」全是幻觉。现在取在每段自己的 25/55/85%。
8. **素材名字可能是错的，而且错得很有说服力。** 柒造这一库 `材料搅拌` 和 `地面涂刷` 两支互换过：名字当时来自两条写得很具体的 L2 记录，摘要、理由、置信度都齐，但判的是隔壁那条。修正靠的是对原始文件抽一帧直接看。所以凡是"名字/摘要断言了画面内容"的地方，抽查一条原始素材，比再读一遍索引有用得多。
9. **结论不要写成"自动出片"。** 现在的意思是：素材能索引、取舍能成 EDL、EDL 能出片、成片能被机器验。但"能出片"不等于"能发"——D 段绿了只说明量化的东西达标，内容好不好还得人看。
10. **指纹只判「文件被动过没有」，判「是不是同一个素材」的是 `uid`。** 两个判据混用会出事：旧版把文件名混进指纹，改一次名全部素材看起来都变了；反过来，内容被替换（大小变了）时 `uid` 变、指纹也变，这时必须丢掉旧记录重算。现在的分工：`uid` 决定认不认人、丢不丢缓存，`fingerprint`（size+mtime，不含文件名）只用来写一条 note。
11. **分级不要写死。** `make_edl.py` 曾经把每段 `grade_at_cut` 写死成 `must-use`，于是「系统判断准不准」这个统计永远是 100% 准确——等于这个字段不存在。现在是按区间重叠从索引里取（L2 段优先，没有就退到这支素材最好的一段）。

## 素材缺口四级（优先级写死，不许静默兜底）

1. 本库检索（命中 L2 的 `summary` / `action` / `people`）
2. 免版权库检索，记录来源与许可
3. 代码生成替代：**只生成图表、示意图、文字动效、抽象背景**，不生成实拍人像和真实场景；EDL 里必须标 `source: generated`
4. 反馈用户：一张缺口清单——缺什么、在哪一段、为什么别的东西替代不了、不加会怎样

任何一级被用上，都要在交付说明里留一行。生成物时长占比 >20% 要警告，否则片子会变成模板片。

## 题材扩展 = 两个参数

| 参数 | v1 实现 | 后续 |
|---|---|---|
| `cut_source` | `speech`：句子边界 ∪ 气口 ≥400ms ∪ BGM 重拍 | `scene`（vlog）、`manual` |
| `visual_selector` | `single_cam_talking` | `speaker_switch`（访谈）、`multicam_pick`、`broll_match` |

加题材是加一组参数值 + 一条验证清单，A–D 不动。

## 自检：四条命令

```bash
python3 <SKILL_DIR>/scripts/selftest.py            # 全链路 20 秒跑通
python3 <SKILL_DIR>/scripts/audit_skill.py --project <项目目录> --library "<库>"
python3 <SKILL_DIR>/scripts/gen_cli_ref.py
python3 <SKILL_DIR>/scripts/bench.py --library "<库>" --project <项目目录>   # 产线基准
```

**第一条 `selftest.py` 管「链路断没断」。** 它用系统 `say` 造三支带语音的合成素材（20 秒的库），把 A→B→C→D 整条链跑一遍：索引要有转写、EDL 零 error、能渲、验收无红。另外顺带跑 `test_board_clips.py`，锁住看板 `clips.ov` 对账的镜起点不被 overlay 推歪。还会查 `references/**/work/` 有没有被 git 跟踪——生成中间物不许进仓（T-014）。改一行 `index_assets` 或 `render_film`，跑它就知道坏没坏——不用等 153 分钟真素材跑完才发现自己改坏了什么。**它证明的是「链路没断」，不是「片子好看」**：合成素材验不出节奏、冷暖、换介质。

**第二条管「说明书和代码对不对得上」。** 只查机器能查的五类：文档点名的文件存不存在、文档命令里的参数脚本认不认、每个脚本有没有被文档提过、frontmatter 合不合法、schema 字段和真产物对没对上。它跑不起来的东西（文笔、判断、策略）一行都没查。为什么要有它：前四天里两次「文档说 A、代码做 B」都是人读出来的——`grade_at_cut` 写死、字幕按整句收字。能机器查的，别靠人眼。🔴 为 0 才算完。

**第三条重新生成 [references/cli.md](references/cli.md)**——全部脚本、101 个参数，从 `argparse` 扫出来，**不要手改**。说明书装不下 101 个参数，不写下来的参数等于不存在（`search_library --for-cut` 能把「这段口播该配什么画面」反查出来，没写进文档之前谁也不知道）。`audit_skill` 会顺手检查这份表过期没有。

**第四条 `bench.py` 管「这套东西有没有变快」。** 它跑一遍 `plan → EDL → 纸面稿 → 成片 → 验收`，记下机器分钟数、段级缓存命中、人工确认次数，追加进 `_cutskills/bench-history.jsonl`，同一个项目下次再跑就能并排比。**它量的是产线，不是片子**——片子的好坏归 `verify_film` 和盲测管，这两件事混在一起，就会出现「12 项绿灯、下一次照样从零开始」。

实测基线（柒造库 153 个素材 / 153.1 分钟）：

| 动作 | 实测 | 说明 |
|---|---|---|
| 暖缓存重索引全库 | **3.8s** | 第二次跑的索引成本；两次结果除 `built_at` 完全一致 |
| `edl → 出片 → 验收`（14 段全命中缓存） | **39–46s** | 改完再跑的代价 |
| 一条 79 秒片的人工确认 | **6 次** | `BRIEF` 写的是 ≤2 次，实际 6 次——差距记在 `bench.md` 里 |
| 冷启动（新库、新需求、从零到成片） | **没有基线** | `l0_seconds` 2026-11-07 才加，第一遍全库的老记录全是 null；要补只能拿第二块库冷跑 |

**这张表里最该看的是最后一行没有数字。** 「60 分钟素材 → 机器 ≤45 分钟端到端」这条指标，用现有一个项目是证不出来的：项目已经存在、plan 已经写好、缓存已经暖了。真正能证明它的只有拿一块新库从零跑一遍。

查完机器能查的，剩下的只能靠**一个没参加过讨论的人照文档跑一遍**：给他 SKILL.md 和一句需求，让他做到文字粗剪，只问「哪里卡住了」。这个裁判比任何自评都准——它不知道你心里那些没说出口的规矩。

## 边界

- **不写稿。** CutSkills 处理内容，不生产内容。一旦它开始写稿，就从"剪辑"扩成"内容工厂"，那是另一个技能。
- **不虚构素材。** 索引里没有的东西不假装有。
- **不替用户改约束。** 期限、时长、不许出现的内容，那是地面不是天花板。

## 依赖

装一次、放一处，不在任务里各装一遍：

```bash
source scripts/runtime.sh env && scripts/runtime.sh install
```

| 依赖 | 用途 | 何来 |
|---|---|---|
| `ffmpeg` / `ffprobe` | 解码、切点、混音、规格探测 | 系统装（brew） |
| `coli asr --model sensevoice` | 本地转写，离线、字级时间戳 | **不是**系统命令：从 `/Applications/Cola.app/Contents/Resources/app.asar` 抽出的 `@marswave/coli 0.0.20`，在 `~/.cutskills/opt/coli-app`，由 `~/.cutskills/opt/bin/coli` 软链暴露（`runtime.sh env` 把这个目录排 PATH 最前） |
| `python3` + `pypinyin` | 同音纠错检测 | 随包 `scripts/vendor/`（pip 走 SOCKS 代理会失败，不靠它） |
| `node` / `hyperframes` | C 段合成 | `runtime.sh install` 装进 `~/.cutskills/npm` |
| `sips` | HEIC 转 JPG | macOS 自带 |

**转写只有一条路：`coli asr --model sensevoice`**（本地、字级时间戳、最快）。它挂了就停下来告诉用户，不要照着一条不存在的降级链去试：ElevenLabs Scribe 的 key 2026-11-06 实测 401（订阅欠费）、`hyperframes transcribe` 实测 TLS 断流，两条都不能用。以后要加兜底，先把通路跑通再写进文档。

**`coli` 的来路与断法**（2026-11-13 T-023）：它以前由 Cola 暴露成系统命令，Cola 升级后 `which coli` 直接为空，转写整条链一起哑。现在它是从 Cola 安装包里抽出来的：

```bash
# 先看探针：路径/版本/能不能真跑/模型在不在
scripts/runtime.sh check
# 缺了或 App 升级换了 asar，就从 Cola 安装包里重抽一次（只读 App，不联网）
python3 scripts/fetch_coli.py
```

模型不在包里，首次 `coli asr` 自己下 155.5 MB 到 `~/.coli/models`；代理断流时用 `curl -C - --retry 20 --retry-all-errors` 续传到 `~/.coli/models/<dirName>.tar.bz2`，sha256 对得上再 `tar xf` 进去，coli 会认账不再下。细节见 `deps.json` 的 `system.coli` 与 `models.asr`。

## 参考文件

- [STATUS.md](STATUS.md) — 这套东西现在到底能做什么、缺什么、下一步优化什么
- [SHELVED.md](SHELVED.md) — 已雪藏的包装模板层，为什么停、怎么复活

- [references/index-schema.md](references/index-schema.md) — `index.json` v2 字段（uid/spec/handling/storage/l2_coverage）、L2 记录字段、阈值标定数据
- [references/glossary.md](references/glossary.md) — 术语释义：ASR/HLG/PQ/LUFS/EDL… 对着用户解释时先查这里
- [references/library-hygiene.md](references/library-hygiene.md) — 素材库怎么管：命名、冷存储、迁移、去重
- [references/edl-schema.md](references/edl-schema.md) — `edl.json` 字段与校验规则
- [references/plan-templates.md](references/plan-templates.md) — 六套剪辑方案骨架（beats + role），以及怎么按库里的素材挑一套
- [references/blind-test-playbook.md](references/blind-test-playbook.md) — 盲测怎么落地：三种身份、同源偏差七条缓解、怎么判读、机器能查的 vs 只有裁判能查的
- [references/stage-b-playbook.md](references/stage-b-playbook.md) — B 段怎么选段、怎么写理由、什么时候该砍
- [references/packaging-families.md](references/packaging-families.md) — 20 支参考片反解出的 5 族包装语言 + 四条跨族铁律（含 A 族的「让位变体」）
- [references/pack-compose-set.md](references/pack-compose-set.md) — 可组合套怎么选：手动挑 / `pick_pack.py` 自动判 / 沿用上一次
- [references/packtest-2026-11-08/report.md](references/packtest-2026-11-08/report.md) — **换素材的 10 秒：模板和临摹的分界线**，8 个 token 的实测与三个新坑
- [references/cli.md](references/cli.md) — **全部脚本的全部参数**（从代码生成，不要手改）
- [references/production-line.md](references/production-line.md) — 这套东西当**产线**看时的验收：要量的四个数、现在各有多少、为什么「一条片 12 项绿灯」证明不了可复用
- [references/board-fusion.md](references/board-fusion.md) — **制作看板与 CutSkills 的融合规格**：三层、字段映射（timeline → BOARD）、三道接口、看板能做什么/做不了什么
- [references/stranger-run-2026-11-07/卡点.md](references/stranger-run-2026-11-07/卡点.md) — 一个没参加过讨论的人照这份说明书跑一遍的卡点清单，以及为此改了什么。**下次大改文档后再找一个人跑一遍**，这是唯一能查出「你以为写清了」的裁判
- [references/audit-a-b-2026-11-07.md](references/audit-a-b-2026-11-07.md) — A/B 段需求和实现的对账存档：41 条里哪些真做了、哪些只是写在纸上、为什么当时这么判
- [references/rhythm-bands.md](references/rhythm-bands.md) — 四套骨架的节奏目标区间（S1–S4）与速度标定
- [references/p3-2026-11-11.md](references/p3-2026-11-11.md) — P3-dark 包装设计备忘：大字即字幕、暗色调视觉与动效
- [references/plan-v3-2026-11-11.md](references/plan-v3-2026-11-11.md) — 方案骨架 v3 设计
- [references/capability-2026-11-10.md](references/capability-2026-11-10.md) — CutSkills 能力图谱
- [references/benchmark-2026-11-10.md](references/benchmark-2026-11-10.md) — 六家开源/商用竞品拆解与融合借鉴清单
- [references/fusion-plan-2026-11-10.md](references/fusion-plan-2026-11-10.md) — 制作看板与产线融合执行计划
- [references/audit-2026-11-11-adversarial.md](references/audit-2026-11-11-adversarial.md) — 第一轮对抗式审查复盘（抓到的 10 处假与自证）
- [references/fixes-2026-11-11.md](references/fixes-2026-11-11.md) — 第一轮审查后的修复与真实读数
- [references/audit-2026-11-11-round2-adversarial.md](references/audit-2026-11-11-round2-adversarial.md) — 第二轮对抗式审查报告（抓到的 6 处假与自证）
- [references/fixes-2026-11-11-round2.md](references/fixes-2026-11-11-round2.md) — 第二轮审查后的修复记录
- [references/audit-p4-blind-2026-11-11.md](references/audit-p4-blind-2026-11-11.md) — P4 盲测收口与阴性样本防误杀审计报告：20 支阴性样本 + 20 支对照组防误杀 + 6 组项目抽测
- [BRIEF.md](BRIEF.md) — 完整需求 v1.1（硬指标数字、版本切分、砍掉了什么及为什么）
