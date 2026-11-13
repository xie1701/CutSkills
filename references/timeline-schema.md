# timeline.json 字段与校验规则（v4）

`timeline.json` 是**唯一真值源**。它取代了 v2 的 `edl.json`。

## 为什么换

v2 的 `edl.json` 是一条**镜头列表**——它默认片子是「从库里挑出来的」。
一旦承认素材不足时要靠代码造，这条默认就不成立了：成片的某一格可以是真实素材、
代码生成的动效、一张图、一段纯文字节奏，甚至一段刻意的空。

所以 v4 把 `segments[]` 换成 `elements[]`，每一格多两个字段：**`track`** 和 **`kind`**。
结构（论点、角色、时长）不放在这里，放在 `plan.json`——`timeline` 只回答「第几秒到第几秒，
画面上是什么、声音是什么」。

## 结构

```json
{
  "version": 4,
  "slug": "qizao-ground-01",
  "created": "2026-11-09T13:02:00",
  "duration_s": 79.15,
  "style_pack": "talking-head-v1",
  "brief": {"audience": "自己找师傅的业主", "change": "先问口径再比价", "length_s": 79, "style": "talking-head-v1"},
  "skeleton": "skeleton.json",
  "elements": [
    {
      "n": 1, "kind": "clip", "track": "video",
      "asset": "熊工工地素材/Flow_VID_20261017_082159_02_069.MOV",
      "in": 3.96, "out": 12.60,
      "medium": "talking", "source": "library",
      "beat": 1, "role": "claim",
      "why": "开场立人：自报身份 + 今天在哪个工地做什么",
      "evidence": "你平时每天几点钟出门呢",
      "card": {"kind": "behind", "text": "一年顶三年"}
    },
    {
      "n": 9, "kind": "gen", "track": "video",
      "template": "compare", "dur": 4.0,
      "props": {"left": {"title": "同样的样板", "value": "喊价 380"},
                "right": {"title": "同样的样板", "value": "含基层 620"},
                "note": "差的不是漆，是基层做没做"},
      "why": "库里的素材讲不出「口径」这件事，只能造",
      "source": "generated", "beat": 2, "role": "warrant"
    },
    { "n": 10, "kind": "silence", "track": "video", "dur": 0.8, "why": "转场留白" }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `kind` | ✓ | `clip` / `image` / `gen` / `silence`。这是 v4 的核心：**同一时间轴上允许混排** |
| `track` | ✓ | `video` / `audio`。现在只出 `video` 轨；`audio` 轨留给「B-roll 盖住口播」这一层 |
| `dur` | gen/image/silence 必填 | 该元素在成片里占几秒。clip 用 `in`/`out`，成片时长 = `out-in` |
| `asset` + `in`/`out` | clip/image 必填 | `index.json` 里的 `id`；`in`/`out` 是**素材内**时间 |
| `template` | gen 必填 | 生成模板名，见下表 |
| `props` | gen 必填 | 模板参数，字数上限由模板定，超了 `check_timeline` 直接判 error |
| `medium` | clip 必填 | `talking` / `broll` / `photo` / `screen` |
| `props.video_grade` / `props.audio_grade` | 否 | 纸面剪辑时手写的「露脸 / 声音」评级：`video_grade` 取 `usable` / `audio_only` / `ng`，`audio_grade` 取 `usable` / `ng`。见下方「声/画双评级」 |
| `source` | ✓ | `library` / `cc0` / `generated`。**生成物必须如实标**，验收算占比 |
| `why` | ✓ | 这一格推进了什么。套话判 error |
| `beat` / `role` | 强烈建议 | 同 v2：`setup/claim/evidence/warrant/turn/payoff` |
| `audio` | B-roll 必填 | `{asset, in, out}`：画面换掉、声音继续走原口播 |
| `card` | 否 | 贴在 clip 上的叠加层。`kind` 取 `behind` / `cutout` / `plain`（都是大字），或 MG 卡型 `stat` / `steps` / `versus` / `term` / `proportion` / `day-night` / `chips`；MG 卡另带 `data`，见 [mg-library.md](mg-library.md) |
| `third_party_action` | 否 | 画面中出现第三方品牌字样时的处置标记：`keep`（施工实拍真实工具/场景无需避开，保留出镜） / `crop`（裁切避开） / `blur`（打码） / `acknowledged`（已核验）。标注后 `check_timeline` 自动核销告警 |

### card 字段

大字（`keyword-big` / `behind` / `cutout`）：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `text` | ✓ | 一屏 4–9 字，必须是台词里的原词 |
| `kind` | 否 | 缺省大字；`behind` 是压在人物脑后露一半 |
| `on` | 强烈建议 | 锚词：卡要晚于它发音 +0.10s 出现 |
| `dur` | 否 | 卡停多久，缺省夹在 `emphasis.hold_s` |

MG 卡（`stat` / `steps` / `versus` / `term` / `proportion` / `day-night` / `chips`）：除 `on` 外，内容全放 `data`。

| 卡型 | `data` |
| --- | --- |
| `stat` | `value` `unit` `label`；`source`（谁说的、哪一天）或 `approx: true`（标「示意」） |
| `steps` | `title`；`items`（2–3 条） |
| `versus` | `left{head,body}` `right{head,body}`；可选 `tail` |
| `term` | `term` `def` |
| `proportion` | `value` `total`（或 `ratio`）`label`；可选 `unit` `part_label` `total_label` `kicker` `percent` |
| `day-night` | `day{tag,head,body,sub}` `night{tag,head,body,sub}`；可选 `relay` `tail` `title` |
| `chips` | `items`（2–4 枚，支持字符串或 `{name, tag}`）；可选 `title` `tail` |

规矩：同一卡型一条片里不重复超过 2 次；每条片至少一张内容专属卡（有数字用 `stat`，
有对比用 `versus`，有流程用 `steps`）。数字没有出处就必须 `approx: true`。

#### `card.layout`：叠加态的人工信手覆写（2026-11-13 T-019）

卡的**位置**正常由渲染器算（读这一格的人脸框 + 人物遮罩，见 [mg-library.md](mg-library.md)
的「叠加态」一节）。但导演有时知道机器不知道的事（「这张卡必须压左下，我要留出右上给产品」），
所以留一个字段。写了就按写的来，**但闸门不豁免**：压脸、口播格整屏暗场、重卡撞字幕照样判红。

```json
"card": {
  "kind": "stat", "on": "六百平",
  "data": {"value": "600", "unit": "㎡", "label": "三百万换六百平"},
  "layout": {"mode": "corner", "box": [0.06, 0.62, 0.55, 0.24],
             "plate": [0.04, 0.60, 0.59, 0.28], "why": "右上留给产品特写"}
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `mode` | ✓ | `corner`（轻量挂角，不压暗全屏）/ `focus_blur`（底片模糊 22px 退后 + 局部底板）/ `cutaway`（整屏纯底切出）。其它值一律不认，落回自动布局 |
| `box` | 否 | 卡的可视框 `[x, y, w, h]`，画幅比例、原点左上。不写就按这个 mode 自动找一个避脸的位置 |
| `plate` | 否 | 底衬框（跟卡走的那一块，带羽化边）。不写则由 `box` 外扩算出 |
| `why` | 强烈建议 | 为什么这么摆。它会原样进 `out/cards.json` 的 `layout.why`，被 `check_overlay` 判红时一并打出 |

红线不因覆写而放宽：`medium: talking` 且人占画面 ≥15% 的格子写 `mode: cutaway` → `check_overlay`
判 error（渲前退出码 7）；卡与任一帧人脸框 IoU ≥2% → 同样判 error。

### props（实拍/素材元素也认这三个键）

`props` 原来只给 `gen` 模板用。2026-11-13 起，实拍元素（`kind: clip` / `image`）也可以带
下面三个键。它们回答的是同一个问题：**这一格画面能不能证明这句话。**
判据落在 `scripts/content_guard.py`（`check_timeline.py` 会自动调）。

| 字段 | 用在哪 | 说明 |
|---|---|---|
| `handles_exempt` | clip + `medium: broll` | 起尾余量豁免的**理由**（必须是非空字符串，否则不认）。缺省不豁免：`in < 0.40s` 判 error（那是按键/对焦未稳的糊片），`duration - out < 0.40s` 也判 error（尾段是收机甩镜、松气低头）。确实必须贴头/贴尾（素材本身就只有 1 秒、或动作就在开头半秒）时写一句理由，守卫改报 warning，并把这句理由原样带到报告里——豁免是人的判断，但必须留得下痕供复核 |
| `visual_ref` | 任意元素 | 视觉佐证：这一格画面就是台词里「呢张图 / 四张图 / 效果图 / 睇下呢度」指的那个东西。填素材 id 或一句能认人的说明。台词命中指代词而这一格没有它（且 `kind` 不是 image/gen、素材 L2 标签也没命中图纸/效果图/截图类）→ error，同时把缺口写进项目 `gap.md`（带时间戳） |
| `taboo_tags` | 任意元素 | 这一格画面自带的品类负向标签（`raw_meat` / `kitchen_trash` / `heavy_construction_trash` / `crew_leak` / `unappetizing_dark`），与素材 L2 的 `taboo_tags` 取并集。正向夸赞台词（舒服/正宗/好食/靓/享受/招牌/新鲜/干净/卫生…）撞上 → error；写错的标签名会当 warning 报出来（`raw-meat` 这种认不出，静默放行比报错更危险） |

为什么不把这些搬到顶层字段：它们是**因人而异、大多数格子没有**的现场标注。放顶层会让每个元素
都多一行 `null`，人写的时候反而容易漏掉 `evidence` 这些真正必填的东西。
### 声/画双评级（`medium: talking` 的露脸门禁）

原则 A2：**台词说得好、但画面穿帮的素材不扔掉，降级成「只留声」**。所以口播段有两个正交评级：

| 字段 | 取值 | 意思 |
|---|---|---|
| `video_grade` | `usable` / `audio_only` / `ng` | 画面能不能露脸。`audio_only` = 声音可用、画面只能被 B-roll / 代码画面盖住 |
| `audio_grade` | `usable` / `ng` | 声音能不能用（噪声压过台词、爆音、半句起头太多） |

评级从哪来（前面的覆盖后面的）：

1. 元素 `props.video_grade` / `props.audio_grade`——**纸面剪辑时手写**，同一支素材不同窗口的最终裁定在这一层；
2. L2 审片记录 `_cutskills/l2/*.json` 的 `audio_grade` / `video_grade`（见 [index-schema.md](index-schema.md)），窗口重叠时取最差的一档。

`medium: talking` 的元素引用了 `video_grade` 为 `audio_only` / `ng` 的窗口 → `check_timeline.py` 判 **error**（真穿帮）或 warning（只擦到边界 < 0.3s，L2 框选误差量级），报错会写明原因：

```
#3 该口播段画面穿帮（手持 A4 台本纸露脸，视线一直看场外），改铺 B-roll 留声或换 take
```

正解只有两条：**改铺 B-roll 留声**（`medium: broll` + `audio` 指向原口播窗口），或换一个 take。
同一段声音换成 `medium: broll` 不会被拦——那正是 A2 要求做的事。
两格都没写＝没评过，门禁一条都不报（旧项目不会因为升级突然红黄暴涨）。

## gen 模板表（代码生成，全部参数化）

每个模板的 `minDur` 是**下限不是建议**——低于它观众读不完，宁可不放。

| 模板 | 用途 | minDur | props |
|---|---|---|---|
| `keyword` | 一个词砸在画面上 | 2.0 | `text` ≤6 字、`sub` ≤12 字、`color` |
| `compare` | 两栏对比（口径、报价、做法） | 4.0 | `left`/`right` 各 `{title ≤6, value ≤10}`、`note ≤18` |
| `balance` | **天平**：量的关系画成称东西（先左沉、口径换了右盘补砝码回平）。用于「口径变了，结论就变了」这类没有实拍的关系 | 3.5 | `left`/`right` 各 `{title ≤6, value ≤12}`、`note ≤18` |
| `number` | 一个大数字 + 一句解释 | 3.0 | `value ≤6`、`label ≤14` |
| `steps` | 三步做法 | 3.5 | `items` 3–4 条，每条 ≤10 字 |
| `quote` | 一句引语/客户原话 | 3.0 | `text ≤24`、`who ≤8` |
| `chapter` | 章节卡（01/02/03） | 1.8 | `n`、`title ≤8` |
| `chart` | 一条数据图（条形/折线，代码画） | 3.0 | `bars[]` 各 `{label ≤6, value}` |
| `scene` | **场景生成器画的整屏**（`gen_scene.py`）：一件事在变，不是字卡 | 2.0 | `scene`（`phase`/`pour`/`measure`）、`label ≤8`；格子本身还可带 `marks` |

`scene` 跟其他模板不是一类东西：别的模板是 DOM + GSAP 画的（文字、色块），
`scene` 是逐帧画出来的位图（液流、抹痕、多边形），所以它落成 mp4 播，
**必须另给 `audio`**（`{asset, in, out}`）——画面换掉、声音一秒都不能断，
漏了 `audio` 时 `check_timeline` 直接判 error，渲完还有一条「这几秒是不是哑的」的实测。

`marks` 是词级节拍：`[0.169, 0.365, 0.400, 0.731]` 这种成数，由 ASR 字级时间戳算出来，
意思是「念到第几个词的时候画面该变形态」。不写就退回脚本自己的比例——那样画面对不上词。

### B 轴（axis: "B"）专用字段

`scene` 格可以升成 B 轴叙事主体（连续场景 + 人物 PiP），此时格子额外认这些字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `axis` | ✓ | `"B"`：这格是叙事主体，不再服务论点（准入三条见 plan-v3：有叙事内容 / ≥8s / `narrative{subject,turn,change}`） |
| `props.scene` | ✓ | 用哪个场景生成器（现 `lime`） |
| `narrative` | ✓ | `{subject, turn, change}`：谁、遇到什么、怎么变；验收读它 |
| `anchors` | 建议 | 如 `["煅烧", "水化"]`：声明旁白里的锚词，`calibrate_marks.py` 据此从字级时间戳推 `marks`；锚词缺失或叙述顺序与工序冲突时保持原值并如实报，不悄悄钳位 |
| `props.pip` | 建议 | 人物小窗。`false` 关；`{width, right, bottom_edge, mode}` 调（都小数占比；`bottom_edge` 是小窗**底边** y 占比，默认 0.62，压在字幕带上方；`mode`: `box` 圆角卡（默认）/ `cutout` 浮人抠像——Vision 遮罩在白 T 恤/杂背景上会撕裂，切 cutout 前先目检） |
| `marks` | 建议 | B 转场的成数；`anchors` 声明后由 `calibrate_marks.py` 标定 |

验收三层（`verify_film.py`「B 轴人物 PiP」）：声明层（timeline 想要几格 vs `hf/meta.json` 有几格）、
管线层（`pips_applied`——只有 ffmpeg 贴回成功才写 true）、像素层（小窗区域 std ≥ 6）。
任何一层不满足都判红。

排版由模板自己决定，风格包只给**字号档位、颜色、字体**——模板不写死 CSS 数值。

## 缺口兜底链

库里没有可用画面时，按这个顺序找，**不许跨级跳到「硬塞一张不相关的图」**：

1. `clip`：索引里命中（`search_library.py`）
2. `image`：索引里的照片/截图
3. `gen`：代码生成（本文件的模板表）
4. `silence`：刻意的空
5. 都补不上 → 写进 `gaps.md`，作为**拍摄清单**交给用户，并在 `check_timeline` 里留 warning

`timeline.py --fill` 会按这条链自动补；每一格补了什么、为什么，写进 `decisions.md`。

## 校验（check_timeline.py）

| 级别 | 规则 |
|---|---|
| error | `kind`/`track` 缺失或非法；clip 缺 `in`/`out` 或 `asset` 不在索引；gen 缺 `template`/`props`；`dur` 低于模板 `minDur`；props 字数超限；`why` 是套话；总时长与 `brief.length_s` 差 >10% |
| error | 元素首尾相接有空隙或重叠（时间轴必须连续） |
| error | 内容守卫（`content_guard.py`）：实拍 B-roll `in < 0.40s` 或距素材尾 < 0.40s（除 `props.handles_exempt` 带理由）；台词命中视觉指代词而这一格没有视觉佐证；正向夸赞台词撞 `taboo_tags` 负向画面 |
| error | `medium: talking` 引用了 `video_grade` 为 `audio_only` / `ng` 的窗口（声/画双评级，原则 A2）——改铺 B-roll 留声或换 take |
| warning | 穿帮窗口只擦到该格边界不足 0.3s（L2 框选误差量级）、`audio_grade: ng`、或评级值打错字 |
| warning | 单镜超 12s；`generated` 占比 >25%；40s 内没有介质变化；`warrant` 段缺失；B-roll 没有 `audio` 字段；画面出现未处置的第三方品牌字样（提示向用户确认是否需要避开，若无需避开留痕核销） |
| info | 各 kind 占比、各 beat 时长分布 |

## 与 plan.json 的关系

`plan.json` 是**意图**（有哪些 beat、每个 beat 要说什么、素材从哪来），
`timeline.json` 是**结果**（第几秒到第几秒画面上是什么）。
中间只有一层换算（`timeline.build_timeline`），`--dry-run` 走的也是同一份函数。

## 与 v2 edl.json 的关系

`render_hf.py` 优先读 `timeline.json`，没有时回落到 `edl.json`（v2 兼容）。
**v2 不废，但不再是主路径**——它表达不了 `gen` 和 `silence`。
停在 v2 的项目想升级：`timeline.py --from-edl` 会把 `segments[]` 原样搬成 `elements[]`（全部 `kind: clip`），
之后再手工把该造的那几格改成 `gen`。

### v2 项目升级的四步（以 `packtest-2026-11-08/proj79` 为例，T-028）

有些老工具只认 `timeline.json`，它们对 v2 项目是硬失败，不是降级：`lock.py --lock` 直接
`FileNotFoundError`（签不了锁），`check_cards.py` / `check_timing.py` 同样硬读。升级走这四步：

```bash
P="references/packtest-2026-11-08/proj79"
python3 scripts/timeline.py        --project "$P" --from-edl --library <素材库根>   # ① v2 → v4
python3 scripts/schema_upgrade.py  --project "$P" --write                          # ② v4 → v5（只加字段）
python3 scripts/check_timeline.py  --project "$P" --library <素材库根>              # ③ 看它真实缺什么
python3 scripts/lock.py            --project "$P" --lock --note "<为什么拍板>"       # ④ 签锁
```

① 必须带 `--library`：项目不在 `<库>/_cutskills/projects/<slug>` 下时，往上三层解析不到索引。
`--from-edl` 只搬**真值**：`kind: clip` / `track: video` / `medium` 全取自 `edl.json`，
B-roll 格的 `audio`（指向盖在它下面的口播段）和 `card` 的 `on`（锚词）也原样跟着走；
它**不会**替你补 `props`（没有 `gen` 格就不需要）、更不会编台词。

② `schema_upgrade.py` **不能从无到有**：没有 `timeline.json` 时它只打印「没有 …/timeline.json」并以 1 退出，
所以第 ① 步不能省。它推不出来的字段（`plan` / `pack`）会写默认值并在屏幕上说「推不出来」——
**默认值不是事实**：`plan` 会一路带进 `verify_film.py` 的骨架档判定（`S2-live-mid` = 2.2–2.7s），
旧片本来中位 3.37s，写进去立刻多两条 🟡。没有 `brief.json` / `order.json` / `arrange.json` /
`decisions.md` 的开场选择的项目，把这两个字段**留空**，在 `_notes` 里写清「推不出来、需要人定」。

③ v2 升上来的时间轴全部是 `kind: clip`，缺的只可能是内容层的真值，不是结构：
`check_timeline` 会如实报「窗口里没有词级真值」「台词里的字音频没说」这类，这类要人判，
**不许**为了让 error 归零去改 `evidence` 或放宽判据。

没有时间轴的项目跑 `check_timing.py` 不再崩：它报一行
`🟡 该项目没有时间轴（v2），声音在前判据跳过` 并以 0 退出（不静默、也不把不依赖时间轴的卡判据一起放过）；
但 `render_hf.py` 渲完会拿它当硬判定，卡缺 `on` 仍会被拦——**要渲就去升级，不要去关闸门**。
