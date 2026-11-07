# edl.json 字段与校验规则

`edl.json` 是**唯一真值源**。`paper-edit.md`、`verify-report.md` 都可以删掉重建，删了之后从它生成。

## 结构

```json
{
  "version": 2,
  "slug": "qizao-01",
  "created": "2026-11-06T18:02:00",
  "cut_source": "speech",
  "visual_selector": "single_cam_talking",
  "style_pack": "talking-head-v1",
  "beats": [
    {"n": 1, "title": "两种说法各摆一边", "segs": [1, 2, 3, 4], "seconds": 16.6,
     "roles": ["claim", "evidence"]}
  ],
  "segments": [
    {
      "n": 1,
      "asset": "熊工工地素材/Flow_VID_20261017_082159_02_069.MOV",
      "in": 3.96,
      "out": 12.60,
      "medium": "talking",
      "source": "library",
      "why": "开场立人：自报身份 + 今天在哪个工地做什么，观众第一次知道这是谁",
      "evidence": "你平时每天几点钟出门呢",
      "chars": 41,
      "beat": 1,
      "role": "claim",
      "grade_at_cut": "must-use",
      "grade_confidence": 0.82
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `asset` | ✓ | `index.json` 里的 `id`，相对素材库的路径 |
| `in` / `out` | ✓ | **素材内**时间（秒），不是成片时间。成片时间由前后段累加得出 |
| `medium` | ✓ | `talking` / `broll` / `photo` / `card` / `chart` / `screen` / `generated` / `silence` |
| `source` | ✓ | `library` / `cc0` / `generated`。**生成物必须如实标**，验收会算占比 |
| `why` | ✓ | 这段推进了什么。套话会被 check 直接判 error |
| `evidence` | 口播段建议填 | 能回溯的原文一句，方便人和机器核对取舍 |
| `audio` | B-roll 段必填 | `{asset, in, out}`：画面换成 B-roll 时，声音继续走原口播。缺了这层，口播片就只能一镜到底 |
| `grade_at_cut` | 否 | 取用时的分级，由脚本从索引里按区间重叠取（不是人手写），便于事后统计"系统判断准不准" |
| `grade_confidence` | 否 | 同一段的 L2 置信度透传。**没有这一格就是「这段没被 L2 看过」**，不是 0 分，也不是 1 分 |
| `chars` | 否 | 这段里说了几个字（脚本算的），给「语速」和字幕密度用 |
| `beat` | 强烈建议 | 论点号（整数）。**没有它，EDL 只有时间线没有结构**：`check_edl` 能判节奏，判不了「有没有论点、论点有没有证据」 |
| `role` | 强烈建议 | 这段在论证里干什么：`setup` 交代 / `claim` 主张 / `evidence` 举证 / **`warrant` 说理** / `turn` 转折 / `payoff` 收束。前四值来自 `stage-b-playbook.md` 的四条选段判据，`warrant` 借自论证学（Toulmin）：**证据不等于证明，中间那半步是「为什么这条证据支持这个结论」**——观众不信的往往不是证据，是这半步 |
| `beats[]` | 否 | 顶层摊平的论点表（`n` / `title` / `segs` / `seconds` / `roles`），由脚本从 `beat` 汇总，人手不用写 |

**顺序即时间线**：`segments` 数组顺序就是成片顺序，没有空隙。要留白就插一个 `medium: "silence"` 的段（`asset` 指向任意静音素材或用 `source: generated`）。

## plan.json 与 EDL 的关系

`plan.json` 是**意图**，`edl.json` 是**结果**。两者之间只有一层换算（`make_edl.build_edl`），
`preview_plan.py` 用的是同一份函数——所以预览说 71.2s，落盘就是 71.2s，不会出现「两个算法各说一套」。

计划两种写法都行：

```json
[ {"asset": "...", "in": 231.66, "out": 234.36, "medium": "talking", "beat": 1, "role": "claim", "why": "..."} ]
```

```json
{"beats": [{"n": 1, "title": "两种说法各摆一边"}],
 "segments": [ {"...": "...", "beat": 1, "role": "claim"} ]}
```

带 `beats` 的写法能让论点有标题，纸面稿按论点分节，读稿的人看见的是「这个论点立没立住」，
而不是 13 行平铺的句子。`plan.json` 每次落盘前存一份进 `plan-history/`——EDL 能回滚、计划不能，
等于只能回到「另一版结果」，回不到「当时的意图」。

## check_edl.py 判什么

报告写进 `edl-report.md`。**`verify-report.md` 归 D 段（成片验收）**，两个阶段不抢同一个文件名——以前会抢，B 的报告被 D 覆盖，回头查"当时为什么判断能过"就查不到了。

| 检查 | 级别 | 依据 |
|---|---|---|
| 意图四字段（观众/承诺/时长/画幅）齐 | error | 没靶子不许剪 |
| `cut_source` / `visual_selector` / `style_pack` 合法 | error | 写错了要到渲染才炸，不如现在炸（`styles/<pack>.yaml` 不存在也报） |
| 「不许出现」的词命中 `why` / `evidence` | warn | 字面匹配，机器只能做到这；命中不一定错，但必须人看一眼 |
| 素材在索引里、in/out 在素材范围内 | error | — |
| `out > in`，且 ≥ 0.4s | error / warn | 太短多半是误触 |
| `source` 在枚举内 | error | — |
| `why` 非空、不是套话、≥ 6 字 | error / warn | 黑名单：过渡用 / 好看 / 随便 / 备用… |
| 口播段有 `evidence` | warn | 没证据无法复核取舍 |
| `beat` 是整数、`role` 在五值内 | error | 结构层字段写错，等于没有结构 |
| 每段的 `beat` 都归属某个论点 | warn | 没归的段要么归一个，要么承认它是纯过渡 |
| 每个论点里有 `claim` 也要有 `evidence` | warn | 「只有主张没有证据」——观众凭什么信 |
| 论点时长 ≥3s | warn | 一段的论点等于没有展开 |
| 论点有 `claim` + `evidence` 但没 `warrant` | info | 说理那半步缺席。机器判不了该不该有，盲测会给答案 |
| 两段说的是同一句话 | warn | 跨段跨素材比对转写原文（≥85% 重合）。同一素材相邻段共用字界的不算 |
| 论证结构（论点表） | info | 论点号 / 标题 / 段 / 时长 / 有没有证据，进报告第一屏 |
| 自上次确认后改动幅度 | warn | 比对 `decisions.md` 最近一条记的段数与时长：段数变 >20% 或时长变 >15%，该再给人看一次稿 |
| **切边不落在字中间**，离字界 ≥ 30ms | error | 拿 ASR 字符级时间戳判；拿不到时降为 warn，不许假装通过 |
| 切边离字界 30–200ms | warn（<30ms 太贴，>350ms 要确认是否刻意留白） | 吸收时间戳漂移 |
| 成片时长 vs 目标 | error >30% / warn >15% | — |
| 口播段镜头中位 1.6–3.2s（目标 2.0–2.8） | warn | 口播片的节奏底座 |
| 单镜头 > 12s | info | 需要 `why` 里说明是刻意长镜 |
| 40s 内至少一次介质变化 | warn | 观众会疲 |
| 生成物时长占比 ≤ 20% | warn | 超了片子变模板 |
| 同一素材内前后段重叠 | warn | 可能是刻意回扣，也可能重复 |

退出码：有 error → 1；`--strict` 时 warning 也算 1；否则 0。**0 error 才算阶段 B 跑通。**

## 为什么这些是硬检查而不是建议

切在字中间、切边不留 padding、字幕算错偏移——这三样在预览里都看不出来，只有发出去之后有人指出"这里怪怪的"。机器能判的东西就不要留给眼睛。

尤其是：**相邻两段共用同一个字界时，两边的 padding 会互相吃掉对方 60–90ms**。`make_edl.py` 里做了夹取，手写时间码几乎必犯。

## 别手写时间码

用 `make_edl.py`：计划里只写“从哪句到哪句”，吸附、padding、相邻让位、B-roll 与声音等长，全交给脚本。人写时间码的出错率接近 100%。

## C 段会从这里读的东西

渲染时按 `segments` 顺序逐段 `-ss/-to` 提取（**不要**用单通道 filtergraph 一次性合成），加完覆盖层后无损 concat，字幕最后贴，字幕时间用 `word.start - seg.in + seg.out_start` 换算。
