# timeline.json 字段与校验规则（v5）

`timeline.json` 是成片剪辑与网感包装的**唯一时间轴真值源**。每一格可以是真实拍摄视频（`clip`）、图片（`image`）、代码生成的动态画面（`gen`）或刻意留白（`silence`），并统一承载顶部标题条、大字/MG 动效卡、矢量情绪贴纸、镜头特效与卡点配乐。

## 顶层结构示例

```json
{
  "version": 5,
  "slug": "tea-house-01",
  "created": "2026-11-14T16:30:00",
  "duration_s": 36.8,
  "axis": "A",
  "plan": "S1-conclude-fast",
  "pack": "P5-viral-pop",
  "style_pack": "talking-head-v1",
  "bgm_mood": "upbeat",
  "title_bar": { "top": "做餐饮前必看", "main": "300万茶楼的隐形账", "mode": "hook", "dur": 4.5 },
  "stickers": [
    { "id": "sparkle-star", "at": 12.4, "dur": 2.0, "pos": "top-left", "label": "细节拉满" }
  ],
  "beat_sync": { "snapped": 3, "checked_cuts": 4, "on_beat_cuts": 3, "hit_rate": 0.75 },
  "brief": { "audience": "实体店创业者", "change": "把预算花在核心出品上", "length_s": 36, "style_pack": "talking-head-v1", "length_band": "30s" },
  "skeleton": null,
  "bands": { "p10": 1.32, "median": 2.4, "p75": 4.08, "p90": 6.24, "src": "style" },
  "beats": [{ "n": 1, "title": "黄金3秒钩子", "elements": [1], "seconds": 3.4, "roles": ["setup"] }],
  "elements": [
    {
      "n": 1, "kind": "clip", "track": "video",
      "asset": "口播/opening.mov", "in": 1.20, "out": 4.60,
      "audio": { "asset": "口播/opening.mov", "in": 1.20, "out": 4.60 },
      "medium": "talking", "source": "library",
      "beat": 1, "role": "setup",
      "why": "黄金3秒数字反差钩子定住观众",
      "evidence": "花了三百万装六百平三分之一全给了厨房",
      "chars": 18, "grade_at_cut": "must-use", "grade_confidence": 0.95,
      "vfx": "punch_zoom",
      "sticker": { "id": "bang", "pos": "top-right", "label": "真金白银!" },
      "card": { "kind": "keyword", "text": "三分之一给厨房", "on": "厨房" }
    }
  ],
  "_notes": []
}
```

## 顶层字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `version` | ✓ | 固定为 `5` |
| `slug` / `created` / `duration_s` | ✓ | 项目标识、创建时间、主视频轨总时长（秒） |
| `axis` | ✓ | `"A"`（口播/旁白观点主轴）或 `"B"`（连续代码叙事场景主轴） |
| `plan` | ✓ | 剪辑骨架方案（如 `S1-conclude-fast` / `S2-live-mid` / `S3-probe-slow` / `S4-reverse-fast`） |
| `pack` | ✓ | 视觉包装包（`P1-warm` / `P2-paper` / `P3-dark` / `P4-canton-warm` / `P5-viral-pop`） |
| `style_pack` | ✓ | 基础版式兜底（默认 `talking-head-v1`） |
| `bgm_mood` | 否 | 配乐情绪（`upbeat` / `warm` / `clear` / `hopeful` / `tense` / `minimal`） |
| `title_bar` | 否 | 顶部磨砂胶囊标题条：`{top, main, at, dur, mode}`（`mode: "hook"` 前 4.5 秒出现，`"full"` 全程常驻） |
| `stickers` | 否 | 全局时间点矢量贴纸数组：`[{id, at, dur, pos, label}]`（16 款内置 ID 见 `scripts/stickers.py --list`） |
| `vignette` | 否 | 布尔值，是否开启全片柔和电影暗角 |
| `beat_sync` | 否 | `scripts/beat_sync.py --snap` 写入的鼓点踩点统计 |
| `brief` / `skeleton` / `bands` / `beats` / `_notes` | 否 | 需求摘要、节奏带宽、论点汇总与自动裁切备注 |

## 元素（`elements[]`）字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `n` | ✓ | 从 1 递增的元素序号 |
| `kind` | ✓ | `clip`（实拍视频）/ `image`（图片）/ `gen`（代码生成画面）/ `silence`（留白） |
| `track` | ✓ | `video`（主时间轴，占时长）或 `overlay`（浮层卡片，按 `at` 出现、不占主轨时长） |
| `asset` + `in` / `out` | `clip` 必填 | `index.json` 中的素材 `id` 与素材内起止秒数 |
| `dur` | `gen`/`image`/`silence` 必填 | 在成片中持续的秒数 |
| `medium` | `clip` 必填 | `talking`（真人露脸说话，音画锁死同源）/ `broll`（空镜特写）/ `photo` / `screen` |
| `audio` | 跨源配音必填 | `{asset, in, out}`：用于 B-roll 盖口播、或 `gen` 代码画面配 AI 旁白 |
| `template` + `props` | `gen` 必填 | 代码生成模板（`keyword` / `compare` / `balance` / `number` / `steps` / `quote` / `chapter` / `flow` / `layers` / `chart` / `scene`） |
| `evidence` / `chars` | 有声段必填 | 该段实际说出的台词原文（由 `evidence_guard.py` 校验）及字数 |
| `why` / `source` | ✓ | 该格剪辑意图（禁套话）与来源（`library` / `cc0` / `generated`） |
| `beat` / `role` | 建议 | 所属论点编号与论证角色（`setup` / `claim` / `evidence` / `warrant` / `turn` / `payoff`） |
| `card` | 否 | 叠加花字或 MG 动效卡：`keyword` / `behind` / `idcard` / `stat` / `steps` / `versus` / `term` / `proportion` / `day-night` / `chips` |
| `sticker` | 否 | 绑定在本段上的矢量情绪贴纸：`{id, pos, offset, dur, label}` |
| `vfx` | 否 | 镜头特效：`punch_zoom`（冲击推镜+闪白）/ `slow_push`（缓推）/ `flash_cut`（闪切）/ `spotlight`（暗角聚光） |
| `mg_role` / `narrative` / `anchors` / `marks` | B 轴或高级卡可选 | 见 `scripts/check_timeline.py` 与 `scripts/gen_scene.py` |
| `grade_at_cut` / `grade_confidence` / `third_party_action` | 可选 | 素材评级留痕与第三方品牌出镜核销标记 |
