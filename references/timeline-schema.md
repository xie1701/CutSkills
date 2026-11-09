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
| `source` | ✓ | `library` / `cc0` / `generated`。**生成物必须如实标**，验收算占比 |
| `why` | ✓ | 这一格推进了什么。套话判 error |
| `beat` / `role` | 强烈建议 | 同 v2：`setup/claim/evidence/warrant/turn/payoff` |
| `audio` | B-roll 必填 | `{asset, in, out}`：画面换掉、声音继续走原口播 |
| `card` | 否 | 贴在 clip 上的叠加层：`kind` 取 `behind`（字在人物后）/ `cutout` / `plain` |

## gen 模板表（代码生成，全部参数化）

每个模板的 `minDur` 是**下限不是建议**——低于它观众读不完，宁可不放。

| 模板 | 用途 | minDur | props |
|---|---|---|---|
| `keyword` | 一个词砸在画面上 | 2.0 | `text` ≤6 字、`sub` ≤12 字、`color` |
| `compare` | 两栏对比（口径、报价、做法） | 4.0 | `left`/`right` 各 `{title ≤6, value ≤10}`、`note ≤18` |
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
| warning | 单镜超 12s；`generated` 占比 >25%；40s 内没有介质变化；`warrant` 段缺失；B-roll 没有 `audio` 字段 |
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
