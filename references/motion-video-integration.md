# motion-video 能不能当模板层 · 2026-11-08

Jerry 的问题：「看看接这套 `~/.cola/skills/motion-video` 来当模板呢」。

结论先说：**引擎不接，规格接，控件先搬 6 个试。**

---

## 这套东西是什么

- 入口 `bin/motion.py`，所有命令吐单个 JSON，退出码 0/2/3/4。
- 4 种风格：`classic`（modern 图文，1080×1920@60）/ `poster`（大字冲击）/ `break`（破框，1440×1920@60）/
  `kit2`（口播动效，1080×1920@30，**固定 36.5 秒编排**）。
- 两条路线：**native**（26 组作者镜头，必须真人出镜 + 人脸跟踪）与 **motion-kit**（71 个控件，只要文案）。
- 本机 `doctor` 全绿：Playwright 1.58.2、音频库就绪、OpenCV 人脸检测器、自带 GB2312 字体。
- 引擎是**可编辑源码**（`engine/`），没有签名校验。

## 为什么不能直接换引擎

| 它的前提 | 我们的实际情况 |
|---|---|
| native 每组镜头最短 **6–24 秒**（`intents.md` 里最长的一组 24.2s） | EDL 的段是 **2–4 秒**，一行口播就切 |
| native **全部**要求人脸跟踪 | vlog / 工地 B-roll / 结构卡没有人脸 |
| `kit2` 是**固定 36.5 秒**的编排，只换锚词和字幕 | 片子长度由 EDL 决定，70–90 秒常见 |
| 自带画布与编排，`renderAt(t)` 世界只有一个 `#stage` | 我们已经有一层时间轴（HyperFrames 的 `data-*`） |
| 竖屏 1080×1920，**帧率固定 60** | 我们 30fps |

硬撞的话，第一条就过不去：`needs at least N frames`。

## 能接的是哪一层

**它的控件规格表，71 个。**

`engine/motion-kit/src/controls/*.spec.json` 里每个控件长这样：

```json
{"controls": {"kinetic": {
  "label": "逐行动态排版",
  "use": "一句有节奏的观点拆成 2–5 个短行，跟着口播一行一行冲出来",
  "props": {"lines": {"type": "array", "maxItems": 5, "minItems": 2, "required": true,
                      "label": "每行文字（短，2–10 字）"}},
  "cues": {"count": "lines.length", "meaning": ["每行出现"]},
  "minDur": 2.5, "tail": 1.2,
  "variants": ["stack", "beat", "wave"]}}}
```

这四样东西正好是我们**没有**的：

| 它有 | 我们现在 |
|---|---|
| `minDur`（这个版式最短几秒才成立） | 没有。卡的时长是 `hold_s` 夹出来的，1.2 秒也能塞一个三行对比 |
| `props` 带 `max` 字数与 `required` | 没有。`card.text` 是一个自由字符串 |
| `variants`（同一个控件三种画法） | 没有。一种卡一种画法 |
| `cues`（这段里该有几次"节拍"） | 没有。卡的出场点只有一个 |
| `use`（一句话说这个控件解决什么问题） | 没有。选卡靠感觉 |

**这一层可以立刻接**，而且它不跟"用 HTML 渲染"冲突——它是一张**类型表和约束**，不是渲染器。

## 建议接法（三步，第一步今天就能做完）

### 第 1 步：EDL 的 `card` 从 `{text, kind}` 扩成 `{control, props, variant}`

```json
{"card": {"control": "compare", "variant": "side",
          "props": {"left": {"label": "国产", "value": "1×"},
                    "right": {"label": "进口", "value": "2–3×"},
                    "verdict": "售价差两三倍"},
          "on": "成本比较高"}}
```

`control` 的取值来自那 71 个（先只放开 6 个，见第 3 步）。
`props` 直接按对应 `.spec.json` 校验。

### 第 2 步：把 `minDur` / `max` 字数 / `cues` 变成 `check_edl.py` 的检查

这是**新增的一整类检查**，不是重写：

- 卡的实际停留时长 < `minDur` → warn（`on` 那个词说得太快时最容易踩）；
- `props` 里必填项缺了 → error；
- 文字超过 `max` → warn「这句会被缩字号或者溢出，改断句」；
- `cues` 声明 5 个节拍、但这段只剩 1.2 秒 → warn「节奏挤不进去」。

### 第 3 步：落地 6 个控件（CSS 组件，风格仍由 `styles/*.yaml` 定）

按我们对口播片真正用得上的顺序：

| control | minDur | 用在哪 |
|---|---|---|
| `keyword` | 2s | 现在的大字（已经在用，只是没规格） |
| `compare` | 4s | 「进口 2–3 倍」这类对比，今天裁判点名的缺口 |
| `number` | 3s | 一个数撑一屏 |
| `quote` | 3s | 引一句客户原话 |
| `steps` | 3.5s | 三步做法（plan 模板里的「三 · 三步做法」正好要它） |
| `chapter` | 1.8s | 章节卡（每 20–30 秒一次介质变化） |

## 更远的可能：把它的控件代码也搬过来

技术上**成立**，但要试验，不是配置：

- `engine/motion-kit/src/kit/timeline.js` 里 `window.renderAt = t => {...}` 是**纯 seek 函数**，
  和 HyperFrames 的"确定性渲染"要求同构（`data-*` 也是纯 seek）。
- 控件是 `K.control(name, {build(slot, props, ctx) → inst, minDur, sound})`，
  `inst.update(t)` 只由 `t` 求值——**没有 render-time clock**。
- 它的画布层是 canvas（`bgx/fgx/tfx`，每帧 `clearRect`），意味着**可以是透明的**，
  理论上能当"叠在我们实拍上面的一层"。

要验证的三件事：① 它的 `#stage` 能不能挂进我们的 `#root` 且宽度对齐 1080；
② 主题/字体能不能换成我们的思源黑体（它自带 GB2312 woff2）；
③ 它的 `S('whoosh')` 音效登记和 HyperFrames 的音频轨能不能对齐。

**先不做。** 手工落地 6 个 CSS 组件的工作量，比打通两套 runtime 小得多；
而且我们真正缺的是"卡有哪些类型、每类至少几秒"，不是"卡怎么画"。

## 一句话

它是一套**好的版式词表和一套不对口的渲染器**。
接词表（`props` / `minDur` / `variants` / `cues`），别接渲染器。
