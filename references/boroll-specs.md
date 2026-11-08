# B-roll 图形卡的 spec 字段表

`scripts/gen_boroll.py` 画三种图形卡，输出 **1080×1920 的透明 PNG（RGBA）**，给 `render_film.py`
当叠加层（overlay）用。这份文件只讲「spec 里能写什么」，不讲「为什么要这种卡」——
后者见 `references/packaging-families.md` 的 E 族（图文界面叙事）。

```
gen_boroll.py --kind grid|panel|stats --spec spec.json --out out.png [--canvas 1080x1920]
```

- `--spec` 收 JSON，也收 YAML（`.yaml` / `.yml`，走 PyYAML）。
- `--canvas` 缺省 1080×1920，也可以写在 spec 的 `canvas` 字段里（`"1080x1920"`）。
- 版式距离一律是**比例**（占画幅宽或高），spec 里没有像素单位。
- 字体固定取 `~/Library/Fonts/AlimamaShuHeiTi-Bold.otf`，取不到直接报错退出——
  不做静默替换（fallback），换了字体「黑体」这个前提就不成立了。`--font` 只用来换路径。
- 画完打一行摘要：`kind · 输出路径 · 尺寸/模式 · 版式 · 块数 · 实际字号(占 H) vs 目标`。

## 通用约定

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | str | 命令行 `--kind` 优先；spec 里写不写都行 |
| `canvas` | str | 画幅（canvas）`"1080x1920"`，缺省 1080×1920 |
| `width_ratio` / `height_ratio` / `gap_ratio` / `block_size` / `title_size` | number | 比例值，占画幅宽（W）或高（H），**不是像素** |
| `colors` | list[str] | `#RRGGBB` 或 `#RGB`；不给就用参考片实测的五色 |

颜色写法只认十六进制（hex）。透明度不进 spec 的颜色值，用单独的 `*_alpha`（0–1）字段。

---

## 一 · `grid` 色块章节卡

参考片 70s 那张知识图谱：标题一行、5 列彩色实心块（4–7 行）、列头一行、左侧竖轴标签、
底部说明一行。块是**直角、无圆角、无描边**的实心矩形，靠间距和色相分列。

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `title` | str | — | 标题一行，例 `一堂基本功清单（5.0版）` |
| `kicker` | str | 空 | 英文副题（kicker），小一号灰字压在标题下，例 `YITANG BASIC SKILL LIST` |
| `columns` | list[str] | — | 列头（column header），例 `管自己 / 管项目 / 管团队 / 管业务 / 管公司`；3–6 列可用，实测 5 列 |
| `blocks` | list[list[str]] | — | 与 `columns` **一一对应**的二维数组；每列 4–7 个块，每块 4–7 字的动词短语（verb phrase）。列高不齐是正常的，短列顶部对齐（top-aligned） |
| `axis` | str | 空 | 左侧竖轴（vertical axis）标签，例 `打基础 → 拿结果`；整串逆时针转 90°，**从下往上读**（首词在底） |
| `footnote` | str | 空 | 底部说明小字，例 `说明：浅色块为【研发中】课程` |
| `colors` | list[str] | 实测五色 | 每列一色，数量要 ≥ 列数 |
| `legend` | list[str] | 空 | 每列右下角的角标（legend），一般不用 |
| `card_fill` | str \| null | `#FFFFFF` | 卡片底色；写 `""` 或 `null` 就**不画卡片底**（纯色块直接叠画面，慎用） |
| `card_alpha` | number | 0.94 | 卡片底色不透明度（opacity） |
| `title_color` | str | `#141414` | 标题 / 列头 / 列头字色 |
| `axis_color` | str | `#3A3A3A` | 竖轴标签色 |
| `title_size` | number | 0.032 | 标题字号，占 H（实测档 3.2%） |
| `block_size` | number | 0.019 | 块内字号**上限**，占 H（实测档 1.9%）；放不下会自动压小 |
| `gap_ratio` | number | 0.18 | 块间距 = `gap_ratio × 块宽`（横向）、`gap_ratio × 块高`（纵向） |
| `block_aspect` | number | 0.80 | 块高 / 块宽 |

块内字色不写：按块底色亮度自动选白字或黑字（BT.601 亮度 <150 用白字）。浅绿 `#B5C293`
这类浅块只能配黑字，写死白字会糊掉一整列。

语义限制（会打到 stderr，不拦着画）：每列块数不在 4–7；字被块宽压到目标字号的 70% 以下
（说明这一列字太多，减字或减列，而不是把字缩到看不见）。

```json
{
  "title": "一堂基本功清单（5.0版）",
  "kicker": "YITANG BASIC SKILL LIST",
  "columns": ["管自己", "管项目", "管团队", "管业务", "管公司"],
  "blocks": [["公开演讲", "公开表达", "知识管理", "刻意练习", "深度学习", "时间管理"],
             ["项目复盘", "过程管理", "计划拆解", "方案设计"],
             ["体系沉淀", "会开会", "带团队", "一对一沟通", "新人落地", "招聘盘点", "团队治理"],
             ["创新业务", "算清ROI", "假设驱动", "目标管理"],
             ["财务管理", "合伙股权", "文化基因", "战略规划"]],
  "axis": "打基础 → 拿结果",
  "footnote": "说明：浅色块为【研发中】课程"
}
```

---

## 二 · `panel` 侧栏图形卡

参考片 60–62s 的分屏：画面一侧是面板（图形 + 大字 + 要点），**另一侧完全透明**留给人物。
贴边满高（full-bleed）时面板圆角为 0——圆角贴边会露出一圈背景，分屏立刻假。

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `side` | `left` \| `right` | `left` | 面板在哪一侧；另一侧保持透明 |
| `width_ratio` | number | 0.42 | 面板宽，占 W；只允许 0.20–0.60（再宽没人物位） |
| `height_ratio` | number | 1.00 | 面板高，占 H；1.0 = 满高 |
| `margin_x` | number | 0.0 | 面板离画幅边的距离，占 W；0 = 贴边 |
| `radius` | number | 0.0 | 面板圆角，占 W |
| `title` | str | — | 大字（big title）一行 |
| `points` | list[str] | — | 要点，**3–5 行**（超出直接报错退出）。别名 `bullets` |
| `graphic` | `square` \| `circle` \| `bar` \| `none` | `square` | 面板顶部那个几何图形（graphic）；不用图标（icon），图标要字体或素材，两样都不许联网拿 |
| `graphic_size` | number | 0.075 | 图形边长，占 W |
| `bullet_style` | `number` \| `square` | `number` | 要点前缀：序号（1 2 3…）或小方块 |
| `fill` | str | `#141414` | 面板底色 |
| `fill_alpha` | number | 0.90 | 面板不透明度 |
| `accent` | str | `#FFD400` | 强调色（accent）：图形、序号、标题下那根线 |
| `text` | str | `#FFFFFF` | 要点字色 |
| `title_color` | str | = `accent` | 大字字色 |
| `title_size` | number | 0.045 | 大字字号，占 H（比 grid 的标题大，它是全卡主角） |
| `bullet_size` | number | 0.019 | 要点字号，占 H（与 grid 块内同档） |
| `bullet_lead` | number | 0.030 | 要点行距（leading），占 H |

```json
{"side": "right", "width_ratio": 0.42, "graphic": "square",
 "title": "进口镜头贵在哪",
 "points": ["关税加一层", "渠道抽两道", "售后没得换", "起订量五倍"]}
```

---

## 三 · `stats` 横向对比条

2–4 根横向条，按最大值归一化（normalize）长度：画「进口贵 2–3 倍」这类**事实**，
条长即倍数。满量程画成条底下一条细基线（baseline），不画整条底槽（track）——
底槽和实心条并排会被读成「两种颜色堆叠」，观众量到的是段而不是长度。

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `title` | str | 空 | 标题一行 |
| `items` | list[obj] | — | **2–4 根**（超出直接报错退出）。别名 `bars` |
| `items[].label` | str | — | 条上标签 |
| `items[].value` | number | — | 数值，用于算条长；最大值那根吃满可用宽度 |
| `items[].text` | str | 空 | 显示文本；不写就用 `value_format` 拼 |
| `items[].color` | str | 循环取五色 | 这根条的颜色 |
| `unit` | str | 空 | 单位后缀，例 `倍` / `秒` / `万` |
| `value_format` | str | `{value:g}{unit}` | 数值文本模板，可用 `{value}` 和 `{unit}` |
| `max` | number | = 最大 value | 手动指定满量程；不写就取最大值 |
| `card_fill` / `fill` | str | `#FFFFFF` | 卡底色；写 `""` 就不画卡底 |
| `fill_alpha` | number | 0.94 | 卡底色不透明度 |
| `text` | str | `#141414` | 标签 / 数值 / 标题字色 |
| `title_size` / `label_size` / `value_size` | number | 0.032 / 0.019 / 0.030 | 三档字号，占 H |
| `bar_h` | number | 0.028 | 条高，占 H |
| `label_gap` | number | 0.008 | 标签 → 条，占 H |
| `row_gap` | number | 0.026 | 条与条之间，占 H |
| `title_gap` | number | 0.020 | 标题 → 第一条，占 H |
| `track_alpha` | number | 0.22 | 满量程细基线的不透明度；0 = 不画 |
| `value_reserve` | number | 0.16 | 右侧给数值留的宽度，占卡宽 |

```json
{"title": "同规格镜头的到手价",
 "items": [{"label": "国产 A 厂", "value": 1, "text": "1.0 倍"},
           {"label": "进口 B 厂", "value": 2.3, "text": "2.3 倍"},
           {"label": "进口 C 厂", "value": 3.1, "text": "3.1 倍"}]}
```

---

## 实测色板（grid 五列 / 其余卡循环取用）

| 列 | 颜色 | 实测取样 |
|---|---|---|
| 红 | `#D4674E` | `#d46645`–`#dc694e` |
| 黄 | `#CDA239` | `#cda239` |
| 绿 | `#B5C293` | `#b5c293`（偏浅，块内自动转黑字） |
| 浅蓝（青） | `#45C3B5` | `#3fc6b7` |
| 深蓝 | `#3579B7` | `#317bb8` |

取样来自 `参考包装视频/创业者如何一年顶三年…mp4` 70s 帧（成片已调色，饱和度 0.504）。
换成干净原色会让这张卡从参考片的暖调里跳出来——**要跳出来就显式在 spec 里写 `colors`**。

## 已知边界

- **5 列 + 4 个字**在 1080 宽上刚好能顶到 0.019H 的字；5 字以上必然被块宽压小
  （实测 0.0146H）。这是几何约束（geometry constraint），不是 bug：要大字就减列或减字。
- 只出 **静态** PNG。入场 / 离场动效（entrance / exit）不在这里做，由渲染层给
  （族模板的 `hold_s` 4–7s、`entry: pop`）。
- 卡必须**在调色之后**叠（族模板铁律 4），否则卡里的红色跟画面一起被抽灰。
