# 授权口径：什么算「可商用」，什么必须人工确认

这份文件只写**判据与口径**，不写具体素材条目——条目由脚本每次从项目现场算出来：

```bash
python3 scripts/licensing.py --project <项目>      # → <项目>/out/licensing.json + licensing.md
python3 scripts/licensing.py --library <素材库>     # 素材库总表（默认只打印，加 --write 落盘）
```

产物是给人看、也是给下一个接手的人核的：一条片子到底用了什么、许可写了什么、证据在哪个文件里。
BRIEF 的要求是「商用前可核验」，所以本文的每一条最后都要落到**一个能点开的文件或一段能重跑的代码**上。

---

## 0. 一句话判据

> **「应该是 CC0 / 应该是开源」不算授权，只有文件里的证据算。**

落到表里只有三种状态，第四种是特殊情况：

| 状态 | 含义 | 商用 |
|---|---|---|
| `ok` | 有许可名称 + 有留痕文件（许可文本 / 目录清单 / 字体文件自带的 name 表），能逐条复核 | 可以 |
| `unverified` | 查不到许可证据，或文件里的许可与记录冲突 | **不行**，必须人工确认后再用 |
| `partial` | 记录里有许可，但本机读不到原件（字体文件没装、库根没留痕目录），文件级核验做不了 | 出片前补齐核验；批量交付按 `unverified` 处理 |
| `procedural` | 本地代码合成的（如音效由代码生成），没有第三方素材 | 可以（无第三方权利） |

**写 `unverified` 比写「应该是 CC0」好**：前者会逼人去查，后者会让下一个人以为已经查过了。
这就是 `scripts/licensing.py` 与 `scripts/cc0_lib.py` 里所有「查不到就返回 None / unverified」的由来。

---

## 1. 音频（BGM / 音效）：CC0 与「实际录音授权」不是一回事

### 1.1 两层权利，别混

一首曲子有两层权利，可以分属不同人：

1. **词曲（composition）**——作曲者/词作者的著作权；
2. **录音（master / sound recording）**——这一版演奏、这一份录音制品的邻接权。

「作曲 CC0」不等于「这份录音 CC0」。真实世界里最常见的事故是：曲库标着 CC0，但里面那条 mp3 是
别人从付费专辑里转出来的录音。所以**判据只能逐文件**，不能按目录、按曲风、按 URL 前缀推断。

### 1.2 本仓的音频判据（唯一出处，别另起一套）

- 逐文件许可的唯一入口是 `scripts/cc0_lib.py` 的 `licence_of()`：**只有库根存在许可留痕
  （`LICENSE-CC0.txt` 或 `SOURCES.md`）才按 CC0 认定**，否则返回 `None`；
- 成片留痕由 `scripts/render_hf.py` 的 `write_sfx_manifest()` 写进 `out/sfx_manifest.json`
  （审核项 G-07）：逐文件记 `file` / `licence` / `sha256_16`（版本可核验）；
- `scripts/licensing.py` **只读不判**：有 `out/sfx_manifest.json` 就照它读数，没有才退回
  `bgm.json` + `out/sfx.json` 重算，且仍然调 `cc0_lib.licence_of()` 与 `render_hf.sfx_spec()`。
  两头都不存在时，音频那一栏直接报「没有留痕，无法核验」，不猜。

### 1.3 内置包的已知局限（如实写在这里，不粉饰）

`assets/audio/bgm/SOURCES.md` 记的是**整库口径 + 文件哈希**：逐曲的原始发布页 URL 与作者署名
没有随音频文件一起保存。所以：

- 内部自用、平台发布：整库 CC0 声明 + 哈希足够回答「从哪来的」；
- 商用批量交付、甲方要逐曲证明：**必须**在外置库里补齐 per-file 元数据
  （`source_url` / `author` / `retrieved_at`），否则按 `unverified` 处理。

CC0 1.0 的实际含义：权利人放弃著作权与邻接权，**可商用、可再分发、无需署名**。
但「无需署名」不等于「无来源」——本仓仍然留哈希与库根声明，是为了能证明「当时用的是这一份」。

---

## 2. 字体：嵌入成片 ≠ 分发字体文件

### 2.1 三件不同的事

1. **把字体渲染进视频/图片**（成片里的字形）：几乎所有开源字体许可都允许，是「使用」；
2. **把字体文件嵌进 HTML/CSS 或 PDF**（@font-face、字体子集）：要看你把产物交给谁；
3. **把字体文件本身随包分发**（放进安装包、传给客户）：这才是许可里限制最多的动作。

本仓的字体走的是第 1 条（HyperFrames 渲成视频，字体只在本机参与排版；`render_hf.py` 会把
字体复制进 `hf/assets/fonts/`，那是中间物，`.gitignore` 已排除）。**成片商用不受 OFL 限制**，
但第 3 条动作（把字库传给客户）要按许可原文办。

### 2.2 现行三只字体的依据（写在 `styles/fonts.yaml` 的 `license` / `license_evidence` 里）

判据不是「大家都说它是开源」，而是**字体文件自带 name 表**：

- name ID 13 = 许可描述原文；name ID 14 = 许可全文地址；name ID 11 = 作者主页；ID 0 = 版权行。
- `scripts/fonts.py --verify <字体>` 会现场把这几条读出来，跟 `styles/fonts.yaml` 的记录对账；
  对不上报 mismatch（`status: unverified`），读不到报「无法核验」（`status: partial`），
  都不给绿灯。

SIL Open Font License 1.1 的三条义务，商用交付时最容易踩：

- **保留版权与许可声明**：随字体分发的产物要带 OFL 文本与版权行；
- **保留字体名（Reserved Font Name）**：思源系列的保留名是 `Source`，改名后的衍生字库
  不许再叫这个名字；
- **不许单独售卖字体本身**（把它渲染进片子卖，不叫卖字体）。

### 2.3 新加字体时的规矩

1. 先拿到官方许可页（不是二手博客），把许可名 + 许可页式证据写进 `styles/fonts.yaml`；
2. 跑 `python3 scripts/fonts.py --verify <字体>`，确认文件 name 表对得上；
3. 对不上或读不到 → `license: unverified`，并在授权总表里进「需人工确认」栏。
   **不许为了让表变绿而改写许可名。**

---

## 3. 贴纸：代码自绘怎么声明

本仓 16 款情绪贴纸是 `scripts/stickers.py` 里 `STICKER_CATALOG[...]["svg"]` 的**内联 SVG 字符串**，
没有对应的图片文件——这不是「声明」，是代码事实，`stickers.py --licence` 会在运行时扫一遍：

- 逐款检查 svg 字符串里有没有 `<image>` / `href=` / `url(http…)` / `data:image`；
- 一条都没有 → 该款标 `origin: code_drawn`（代码内自绘，本仓库原创，无第三方素材风险）；
- 一旦出现外链/内嵌位图 → 该款翻成 `origin: external_asset`，整份声明 `third_party_risk: true`，
  并进授权总表的「需人工确认」栏。

**为什么要这么写**：自绘素材的风险不在「版权归谁」（归本仓库），而在「有人后来往里塞了一张
网上找的图而没人发现」。声明函数就是那个哨兵。

如果以后要用外部贴纸包（GIF/PNG/Lottie）：

1. 不许直接塞进 `STICKER_CATALOG` 的 svg 字段冒充自绘；
2. 走时间轴里的新 id，并在项目 `scenes/` 或素材库里留一份授权说明；
3. `licensing.py` 见到不认识的贴纸 id 会直接标 `unverified` + 「可能是外部贴纸图」——
   别绕过它，把它当检查清单用。

---

## 4. 代码场景：作者就是本仓库

`template: scene` 的整屏画面由 `scripts/scenes/*.py`（或项目内 `scenes/*.py`）逐帧画出来，
不是图片素材。授权总表里给它记：

- `origin: code_drawn`、`author: 本仓库（CutSkills）`；
- `source_file` 指到那个 `.py`（这就是「授权依据」：代码本身）；
- 如果时间轴引用了某个场景 id，而仓内与项目内都找不到它的代码 → 标 `unverified`，
  因为那意味着这一格画面可能来自外部图片或手放的截图，必须人工确认。

场景代码里如果写死了字体文件（PIL 制图不吃 @font-face，直接开 ttf），`licensing.py` 会把
那只字体一并算进「这条片子用到的字体」里——画面里出现的字，也该在授权表里。

---

## 5. 实拍素材：授权在库里，不在代码里

实拍素材的授权来自拍摄方、客户或素材平台，代码无法替你认定。约定：

- 把授权写进 `<素材库>/_cutskills/rights.json`（或库根的 `LICENSES.md`），键是素材 id；
- `licensing.py --library <素材库>` 读到就当 `ok`，读不到一律 `unverified` + 提示人工确认。

「素材库里有这个文件」不构成授权证据，跟音频那一节同一条规矩。

---

## 6. 运行时依赖（非素材，但要能答）

贴纸与包装层的动画由 `assets/gsap.min.js` 驱动。它不是素材（不进入成片版权链），但甲方问
「动效哪来的」时要有据可查。授权总表末尾单列一节：版本、许可名、许可页地址，以及
**「条款全文是否随仓保存」**——现在是不在仓内，所以商用前请按许可页原文核对，别拿表里的
一行字当法律意见。

---

## 7. 什么时候必须人工确认（不许自己拍板）

出现下面任何一条，就必须停下来找人核，而不是「先用着」：

1. 表里状态是 `unverified` 或 `partial` 的**任何**一条素材；
2. 外部 BGM / 音效（不在已配置的 CC0 库里）——哪怕来源写着「朋友给的」；
3. 时间轴里出现内置 16 款之外的贴纸 id；
4. 场景 id 找不到对应代码；
5. 字体表里没有许可记录，或 `fonts.py --verify` 报 mismatch；
6. 客户要求「逐曲/逐素材许可证明」，而库里只有整库声明（见 1.3）。

---

## 8. 与封面文案的关系（G-06）

授权表管「素材能不能用」，封面标题管「话能不能说」。两者共用一条纪律：**不许编**。

- 封面主标题 ≤12 字、不带标点、**不出现原文没有的数字或承诺**；
- 发布标题可以带钩子感，但同一条纪律，零无来源数字/承诺；
- 三个主标题都必须能指回片子里的口播原话（`timeline.json` 的 `elements[].evidence`）
  或选题钩子的具体切入点，`cover.py` 会把出处与原话一起写进 `cover.json` / `cover.md`。

```bash
python3 scripts/cover.py --project <项目>                 # 3 个主标题 + 副文案 + 发布标题
python3 scripts/cover.py --project <项目> --pick 2        # 选定并记进 decisions.md
python3 scripts/cover.py --project <项目> --render        # 出 1080×1920 封面图（需要 PIL）
```

渲染要 PIL：先 `source scripts/runtime.sh env`。字号不是估的——`--render` 会报实测最宽行像素，
缩到最小字号仍超宽时如实说「需要人工改短标题」，不会硬塞进画面。

---

## 9. 边界

本表只回答三件事：**素材从哪来、许可写了什么、证据在哪个文件**。它不构成法律意见：
平台规则、客户合同、地区差异都不在这里判。真到对外交付，把 `out/licensing.json` 与
相关许可原件一起交给法务/甲方核——这张表的价值是让他们不用再去翻仓库。

改动约定：`scripts/licensing.py`、`scripts/cover.py`、`scripts/test_licensing.py` 的回归在
`scripts/test_licensing.py`（正负样本各一组：未核验必须被标出来、自绘贴纸不许被误标成外部素材）。
改判据先改测试，别改结论。
