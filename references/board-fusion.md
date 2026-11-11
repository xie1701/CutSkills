# 制作看板 × CutSkills 融合规格

> 2026-11-09 定案。Jerry 三个假设全认：① 看板只当视图，不当第二份真相；② 允许改模板，不 fork 引擎；
> ③ 66 种风格是规格不是代码。看板交付到**画布连线 + 导出 XML 全套**。

## 0. 一句话

**看板负责人看得见、拖得动、批得回；CutSkills 负责真素材、真秒数、真渲染、可验收。两边只在一个地方交接：`timeline.json`。**

以前方案是文字（`方案.md`），你只能读。现在方案是能按空格播一遍的看板，你拖完批完，补丁落回时间轴，渲之前有时间锁。

## 1. 三层，谁写谁读

| 层 | 文件 | 谁写 | 谁读 | 能手改吗 |
|---|---|---|---|---|
| 意图层 | `board/board.js` → `board.html` | `board_from_timeline.py`（生成）／`board_patch.py`（回写） | 人（浏览器） | 只在浏览器里拖；**不许手改 board.js 当真相** |
| 真值层 | `timeline.json` + `index.json` + `audio-map.json` | 脚本 | 渲染、检查、看板 | 只由 `board_patch.py` / `arrange.py` / `timeline.py` 改 |
| 执行层 | `out/film-hf.mp4` + `verify-report.md` | `render_hf.py` / `verify_film.py` / `check_*.py` | 人（看片） | — |

### 三条铁律

1. **唯一真值是 `timeline.json`。** 看板里的编辑以它自己那段 `【制作看板评论…` 文本回来，由 `board_patch.py` 落地。看板的浏览器存储是缓存，不是事实。
2. **看板不估算时长。** 每一镜都写 `fixed` = 真实秒数。我们的时间轴是绝对秒，看板是「顺排 + gap」，两者可无损互转（`gap = start_i − end_{i−1}`，负数即重叠）。这样预演≈成片，才谈得上「逐镜起点差 ≤0.5s」。
3. **看板不存素材。** 素材在 `index.json`，看板只写文件名。看板里拖进来的真视频是便利，不是资产。

## 2. 字段映射：一格 → 一镜

`timeline.json` v4 的 `elements[]` 是我们的单位。一个 `track: video` 的格子 → 一个 shot；`track: overlay` 的 gen 不占时间轴，挂到它覆盖的那一镜上。

| BOARD 字段 | 从哪来 | 规则 |
|---|---|---|
| `id` | `el.n` | `S01`…；补镜用 `S07a`，不重编号 |
| `ch` | `el.beat` + `role` | 相邻同类合并成章；名字取 `thesis.claims[].text` 的首句 |
| `mode` | `kind` + `medium` | `talking`→`A`（有卡时→`AO`）；`broll`/`image`→`S`；`gen`(video)→`E`；`silence`→ 不成镜 |
| `vo` | `audio-map.json` | 该格全部 block 的 `text` 顺次拼接（ASR 原文，一字不改，含同音字 7灶） |
| `fixed` | `el_dur()` | `clip` = `out−in`；`gen`/`image` = `dur`。**全写** |
| `gap` | `silence` 格或显式空档 | 默认 0；`silence` 格 → `gap = dur` |
| `clips.ov` | overlay gen 落点 | `{ov: [offset, dur]}`，offset 相对本镜开头。**渲染端不读它**——渲染只认 timeline 的 overlay 元素；这个字段是投影，靠 `board_check.py` 逐块对账 |
| `d` | `el.why` + 素材标签 + 卡/场景 | 给剪辑看的排布说明 |
| `m` | 见 §3 | 手绘分镜元素 |
| `rec` | `el.asset` | `{dev: '素材', what: 文件名}`；gen 写成 `{dev: '代码生成', what: 场景一句话}` |
| `br` | gen 全屏 | `{id: 'BR01', k: '全屏', dur, d}` |
| `sfx` | `render_hf.CARD_SFX` | 用 CC0 库真名：`pop-medium-in` / `impact-light-firm` / `appear-materialise` |
| `ref` | 画布连线 + 卡片 kind | 见 §4 |
| `flag` | 人工规则 | 第三方字样、借鉴说法、待核实的事实 |

## 3. 我们的东西 → 手绘元素 `m`

坐标是百分比，竖屏（9:16）元素上下排，标题 `size` 取 8–12；底部 12% 留给字幕，不放字。

| 我们的层 | BOARD 元素 | 参数 |
|---|---|---|
| 口播格 | `pres` | `[0,0,100,100]` |
| B-roll / 素材 | `photo` | `[0,0,100,100,'<素材简称>']` |
| 大字（`keyword`） | `ttl` | `[50, 30, 文字, '', 10]`——y 直接取 `emphasis.top_h × 100` |
| 大字（`behind`，字在人后） | `ttl` + `note` | note 写「压在人头后，抠像层贴回」；这是给动效 Agent 的关键信息 |
| 代码画面（gen） | `photo` + `note` + `cam` | `['photo',8,14,84,64,'代码画：三桶灌进墙']`、`['cam',10,14,80,64,'push','慢推']` |
| 身份卡 | `tag` / `card` | 右侧三分之一（x ≥ 58），不压人 |
| 对比屏 | 两个 `photo` + `arrow` | 左旧右新 |
| 字幕 | **不画** | 底部 12% 空着 |

`t`（元素入场秒数）取卡片 `appear_t − 镜起点`，与 `CARD_LAG = 0.10s` 一致——看板上看到的入场时刻，就是成片里的时刻。

## 4. 画布连线与风格库

- 我们每个 `styles/*.yaml` 变成 `BOARD.styles` 的一项：`{id: 'qizao-dark-gold', cn: '柒造暗金', pal: ['#F6EB34', '#14110F', …], skill: 'cutskills'}`。
- `BOARD.canvas` 预置连线：代码画面的镜 → `qizao-dark-gold`，`note` 里写死「大字 #F6EB34、顶边 0.30H、锚词晚 100ms」。
- 看板回传的连线（`S03 ← 动效风格：…，说明：…`）由 `board_patch.py` 写进该镜的 `ref`；`ref` 是**渲染端唯一认的风格指针**。
- lemo-opuscar 的 66 种风格：模板内嵌截图，`canvas` 用文件夹 id（如 `dark-keynote`）。我们是「借用规格」，不接管它的渲染——`gen_scene.py` 从 `STYLE.md` 取配色与节奏，自己画。

## 5. 三道接口

### I1 `board_from_timeline.py` — 真值 → 看板

- 输入：项目目录（`timeline.json` / `audio-map.json` / `index.json` / `scenes.json` / `cards.json` / `brief.json` / `thesis.json`）
- 输出：`<项目>/board/board.js`、`board.html`
- 判据：`board_check.py` 全绿（14 条不变式，含 `clips.ov` ↔ 时间轴叠加层逐块对账、`clips` 非 ov 键拦截）；`build_board.py --extract` 与原 `board.js` 逐字相同（往返无损）；镜头数 = 时间轴格数；回归 `scripts/test_board_clips.py`（overlay 夹在主轨中间时镜起点仍等于视频格累加）

### I2 `board_patch.py` — 看板 → 真值

- 输入：粘回来的 `【制作看板评论…` 文本
- 输出：改写后的 `timeline.json` + diff + `decisions.md` 一行
- `clips` 只支持 `ov`：改的是该镜时间轴上已有的那块 overlay（原地改 `at`/`dur`，不新增）；该镜没有 overlay 时只记一条提示，不凭空造卡片。`br` 是看板引擎的并行 B-roll 轨道：本管线只有单视频轨，没有第二视频层，board 的 `clips` 也只有 offset/dur、没有 asset 指针，所以不接——`board_check.py` 第 14 条会拦，回写只提示、不改时间轴（T-011）
- 支持四类：`镜头号：{"gap"/"fixed"/"clips"}`、`镜头号：[…]`（画布元素）、`镜头号 ← 参考对象，说明：…`（连线→`ref`）、`镜头顺序：S01 → S03 …`
- 判据：改完 `check_timeline.py` 与 `check_edl.py` 仍绿；`duration_s` 重算一致

### I3 `lock.py` — 方案锁

- `--lock`：对 `timeline.json` 取 sha256，写 `lock.json`（hash / 时长 / 格数 / 版本 / 时间）
- `render_hf.py` 只认 hash 对得上的 lock；缺 lock 或 hash 不符 → 拒绝渲染（`--force` 可绕，不该用）
- 改一个字节 → 自动失效，必须重新拍板

## 6. 全套范围：哪件谁做

| 能力 | 谁做 | 我们提供 | 怎么验 |
|---|---|---|---|
| 剪辑台拖动改时长/顺序 | 看板（浏览器） | `fixed`/`gap`/`clips` 真值 | I2 回写后跑 `check_timeline` |
| 预览画布拖元素 | 看板 | `m` 数组 | I2 把 `m` 写回 `cards`/布局 |
| 素材画布连线 | 看板 | `BOARD.canvas` 预置 + `styles` 条目 | 连线回来 → `ref` |
| 生成动效任务 | 看板（复制 md） | 风格规格 + 每镜 `fixed`/`m`/`sfx` | 任务 md 直接喂 `gen_scene.py` |
| 导出 FCP XML | 看板引擎（xmeml v5） | `meta.fps/ar/res`、`fixed`、`clips`、到位素材名 | `board_check.py --xml` 打出 XML 应有的 clip 表，导入后逐条对 |
| 对比成片（预演 vs 成片） | 看板 | `out/film-hf.mp4` | 拖进看板，擦开对比 |
| 按空格预演带声 | 看板 | 音效名 + 时刻 | 耳朵 |

> XML 与画布都是浏览器里的交互，脚本碰不到。我们能保证的是**喂给引擎的数是真数**，并把「导出后应该看到什么」打成一张表供人工对账——这条边界写在 `board_check.py` 的输出里，不假装验过。

## 7. 落地顺序

| 步 | 做什么 | 验什么 | 状态 |
|---|---|---|---|
| 1 | I1 `board_from_timeline.py` + `vendor/production-board` | 真看板能开、能预演、往返无损 | ✅ 三条片全绿 |
| 2 | `board_check.py`（14 条不变式）+ `board_smoke.js`（真 Chrome） | 无 JS 错误、XML 能导出 | ✅ |
| 3 | I2 `board_patch.py` | 改时长/空档/顺序/画布/连线 → timeline 变 → 检查仍绿 | ✅ |
| 4 | I3 `lock.py` + 接进 `render_hf` / `plan_card --approve` | 改一个字节 → 渲染被拒（exit 4） | ✅ |
| 5 | `gen_scene` 补两处场景 + 画布连线 | 看板上「生成动效任务」直接给出可执行命令 | ✅ 三处均有样片 |
| 6 | XML 对账 | 导出的 XML 与 `board_check --xml` 表逐条一致 | ✅ 机器侧已对，待你在达芬奇里点一次 |

## 8. 这次跑出来的读数（2026-11-09）

| 片 | 镜 | 时长 | 不变式 | 引擎 | XML |
|---|---|---|---|---|---|
| `qizao-naming-01` | 8 | 41.86s | 🟢12 | 无 JS 错误，9 clip | 8 镜，帧级对齐 |
| `qizao-protect-01` | 13 | 61.10s | 🟢12 | — | — |
| `qizao-ground-01` | 15 | 78.63s | 🟢12 | 无 JS 错误，17 clip | 序列 2359 帧 = 78.63s，15 画面 clip + 2 叠加贴 |

两个真问题是被检查抓出来的，不是靠看：

1. `protect-01` 的 `duration_s` 写 61.17s，各格加起来 61.10s——渲出来就是 61.1s。真值是后者，已改。
2. 看板引擎把 AO 镜的大字自动变成 V4 叠加贴 clip（如 `S05_叠加贴.mov`）。这是对的，但意味着 XML 里的画面 clip 数会比镜数多——对账时按 `board_check --xml` 的表对，别按镜数对。


## 9. 验收裁判

| 裁判 | 判据 | 现在 |
|---|---|---|
| 一轮改动的时间 | 批完到新看板可看 ≤10 分钟 | 未测 |
| 人工干预次数 | 60s 片从 brief 到成片 ≤3 次 | 10+ |
| 预演与成片贴合 | 逐镜起点差 ≤0.5s | 结构上保证（`fixed` 全是真秒数），待实测 |
| 代码画面占比 | 每条片 ≥3 处由代码生成且承担叙事 | 3 处有样片（尚未接进时间轴） |
| 陌生人可跑 | 照文档从零到出片，卡点 ≤2 | 上次 4 |

## 10. 代价

- 看板的评论/拖动存在浏览器里，换设备不同步；换浏览器要重开同一个文件。
- 模板 755KB（内嵌 70 张风格截图），已 vendored 到 `vendor/production-board/`，MIT，署名见其 `THIRD_PARTY_NOTICES.md`。不 fork 引擎，只换数据块。
- lemo 的 66 种风格给的是 `STYLE.md` + 截图，不是能直接渲的代码；接进 `gen_scene.py` 仍需自己实现。
- 竖屏元素的排布规则要在 9:16 下重新过一遍（I1 已按 `top_h` 换算，仍可能有 1–2 处要手调）。
