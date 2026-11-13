# T-027 · 老卡补 `on` 锚词：六张卡逐个落在词级真值上，判据一条没动

数据来自 2026-11-13 在 `ai/cola/T-027` 工位上的实测（命令见文末）。
这一单只做一件事：**给缺锚词的卡补 `on`**，让 `check_timing` 的「声音在前」闸门不再白拦重渲。
没有改 `scripts/` 下任何一行，没有放宽任何判据，没有 `--force`，没有动 `golden-baseline.json`。

---

## 一、结论先说

| 片子 | 缺锚词的卡 | 补完之后 | `check_timing` |
|---|---|---|---|
| `qizao-ground-01` | 1 张（格 14） | 6/6 张卡都有 `on` | 改前 error 1 → 改后 **error 0** |
| `qizao-protect-01` | 5 张（格 2/4/8/9/12） | 5/5 张卡都有 `on` | 改前 error 5 → 改后 **error 0** |

**六张卡里五张的锚词在卡面上逐字存在**（`倍` / `遮蔽膜` / `边` / `粘条` / `涂料`）。
**一张不是**：`protect-01` 格 2 的卡面是一句问句「先保哪里」，这句真值里没有「先/保/哪/里」四个字，
锚词只能落在这句给出的答案词「地面」上（详见 3.2）。这一张请复核人特别看。

**两张卡有内容层面的问题，不是这一单能修的**（详见第六节）：
`ground-01` 格 14 的卡面数字（只贵 2–3 倍）与这句口播（原料成本差 4–5 倍）对不上；
`protect-01` 格 2 的卡面字一个都不在真值里。锚词补上只是让「什么时候出」有了声音依据，
**没有、也不能替这两件事拍板**。

**一个必须提前说清的副作用**：`out/cards.json` 一重算，卡的时间就跟着锚词落到新位置，
而两支片子的 `out/film-hf.mp4` 还是 11-11 的旧片 → `verify_film` 的「大字烧进画面」会在
`ground-01` 多 3 条、`protect-01` 多 5 条（3 红→6 红、2 红→5 红），**真渲一次即消**
（T-024 2.1 节已归因过这类「旧片 vs 新卡」，数字与隔离 A/B 见 4.3）。

---

## 二、改了什么

### 2.1 只动了两个项目的 `timeline.json`（+ 让产线重算 `out/cards.json`）

每张缺锚词的卡加两个键，其余字段一个不动：

```json
"card": {
 "kind": "keyword",          ← 新增：渲染器的默认值本来就是 keyword，但 check_cards
 "text": "先保哪里",           拿 timeline 和 cards.json 逐字对账，不写就永远判「两份真值源对不上」
 "on": "地面",               ← 新增：锚词
 "at": null,                 ← 原样保留
 "dur": 2.2
}
```

`kind` 必须显式写：`protect-01` 那 5 张卡原本没有 `kind`，渲染器按默认值 `keyword` 渲，
而 `check_cards.audit()` 第 0 条是把 timeline 的卡和 `out/cards.json` 的卡**逐字对账**
（`str(tc.get("kind") or "") != str(c.get("kind") or "")`）。不补 `kind`，五张卡重渲完
仍然各报一条「kind 两份真值源对不上」，闸门照样过不去。

`ground-01` 格 14 原来的 `"at": 0.6` 保留不动：一旦有 `on`，`render_hf.card_windows()`
走的是锚词那条路（`win.find(on)`），`at` 不再参与计时，留着只是留个来历。

### 2.2 `out/cards.json` 是产线在**副本**上重算后拷回来的

`check_timing` 判的是产物 `out/cards.json`，不是时间轴。时间轴上写 `on` 之后，
必须让 `render_hf.py` 真的重算一次卡（卡的起点、锚点、lag 都是它算的），
「只信自己的解析」不算数。做法：

1. `cp -a` 两个项目到 `/tmp/t027-run/`（活项目一个字节都没在渲染里被碰）；
2. 副本里 `lock.py --lock`（渲器要求拍板锁；锁只落在 `/tmp` 副本，**活项目没有留假锁**）；
3. `render_hf.py --no-render` 跑两遍（`--no-render` 只造 HTML + lint，不真渲视频），
   两遍的 `out/cards.json` **逐字节一致**；
4. 把 `out/cards.json` 拷回活项目，`check_timing` / `check_cards` 重跑确认。

---

## 三、逐卡表

锚词全部用**词级真值**（`<素材库>/_cutskills/cache/asr/*-v3c*.json`）定位，
取词窗口复刻渲器自己的逻辑（`render_hf.card_windows()`）：
`a_in - 0.05 ≤ 字起点 < a_in + (out-in) + 0.05`，字表先过一遍 `lexicon.fix_items()`。
「卡面有该词」一列，指的是锚词这串字在卡面 `text` 里逐字出现。

| 项目 | 格 | 卡型 | 卡面文字 | 锚词 `on` | 锚词在真值里的时间（素材绝对秒） | 卡面有该词 | 改前 `at` | 改后 `on` | 产线算出的卡区间 |
|---|---|---|---|---|---|---|---|---|---|
| ground-01 | 14 | keyword | 报价只贵 2–3 倍 | `倍` | 1732.62（「差到**4倍**四到5倍」的第一处「倍」） | ✅ | `0.6`（保留） | `倍` | 70.50–71.50s，lag 1400ms |
| protect-01 | 2 | keyword | 先保哪里 | `地面` | 2.52（「局部的**地面**还有一些重要的地柜」） | ❌ 见 3.2 | `null` | `地面` | 4.963–5.963s，lag 913ms |
| protect-01 | 4 | keyword | 立面用遮蔽膜 | `遮蔽膜` | 14.10（「…就可以使用**遮蔽膜**」） | ✅ | `null` | `遮蔽膜` | 16.56–17.56s，lag 1013ms |
| protect-01 | 8 | keyword | 边缘要贴直 | `边` | 34.80（「…它的**边**角细节才能贴得更直」） | ✅ | `null` | `边` | 32.008–33.008s，lag 1247ms |
| protect-01 | 9 | keyword | 不是靠粘条 | `粘条` | 40.74（「…自带的那个**粘条**」） | ✅ | `null` | `粘条` | 37.38–38.38s，lag 686ms |
| protect-01 | 12 | keyword | 做涂料的人来做 | `涂料` | 58.20（「…让做艺术**涂料**的功能」） | ✅ | `null` | `涂料` | 52.301–53.301s，lag 1400ms |

### 3.1 挑锚词的口径（写死，免得下一个人再猜）

1. **先在卡面文字里找**：把这句真值里所有 1–4 字的连续片段和卡面过一遍，
   取**最长**的重合片段；
2. 同长度的候选里取**离块重音点最近**的（`audio-map.json` 的 `peak_at`）；
3. 卡面一个都对不上时，取**这句真值里卡面所指代的那个实词**，并在下面如实标注；
4. 语义明显冲突的候选要否掉——`protect-01` 格 12 里「来做」（+0.42s，离重音 0.2s）
   比「涂料」（+4.26s，离重音 0.94s）更贴重音，但那个「来做」属于「**杂工**来做」，
   跟卡面「做涂料的人来做」指的正好相反，所以取「涂料」。

### 3.2 三张卡的判断依据（复核时重点看这三行）

- **ground-01 格 14**：这句口播是「虽然实事求是的讲原料成本可能差到 4 倍四到 5 倍」。
  卡面的「报价」「只贵」「2」「3」**一个字都没有**，能在真值里逐字对上的只有「倍」。
  「倍」也是这句话的重心（他就是在这里说倍数），所以锚词落在「4 倍」的那一下。
- **protect-01 格 2**：卡面「先保哪里」是这张卡自己提的问句，真值里只有答句
  「局部的地面还有一些重要的地柜」。按 3.1 第 3 条，锚词取答句的第一个实词「地面」。
  **这是全部六张里唯一一张卡面字对不上真值的**——复核人若认为问句卡该另找落点
  （或干脆删卡），改 `on` 即可，判据不用动。
- **protect-01 格 8**：卡面「边缘要贴直」，真值说的是「边角细节才能贴得更直」，
  「边」「贴」「直」三个字都在，但「贴直」不是连续片段，所以只能取单字。
  取「边」（从头压住这句话的主语），不取「贴」（+4.08s，卡会顶到切点）。

---

## 四、读数：改前 → 改后

命令：`python3 scripts/<check>.py --project <项目>`（在 `ai/cola/T-027` 工位上跑）。

### 4.1 `check_timing.py`（「声音在前」闸门）

| 片子 | 改前 | 改后 |
|---|---|---|
| `qizao-ground-01` | error 1 · warning 0 | **error 0 · warning 0**（rc 0） |
| `qizao-protect-01` | error 5 · warning 0 | **error 0 · warning 0**（rc 0） |

改前那 6 条 error 的原文（`references/fixes-t024-golden.md` 3.1 节记的那六条，逐字复现）：

```
🔴 卡5（keyword「报价只贵 2–3」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
🔴 卡1（keyword「先保哪里」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
🔴 卡2（keyword「立面用遮蔽膜」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
🔴 卡3（keyword「边缘要贴直」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
🔴 卡4（keyword「不是靠粘条」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
🔴 卡5（keyword「做涂料的人来做」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
```

改后：两支片子都 `声音在前：error 0 · warning 0`，`render_hf.py --no-render`
在副本上 **rc 0** 穿过两道闸门（不再打「！声音在前没过，拒绝渲染」/「！卡准入没过」）。

> 注意报错文案：`check_timing` 的「卡 N」是 `out/cards.json` 里的**序号**（0 起），
> 不是时间轴的格号。`protect-01` 格 2/4/8/9/12 对应卡 1/2/3/4/5。

### 4.2 `check_cards.py`（卡准入）

| 片子 | 改前 | 改后 |
|---|---|---|
| `qizao-ground-01` | error 1 · warning 4 | **error 0 · warning 5** |
| `qizao-protect-01` | error 5 · warning 0 | **error 0 · warning 5** |

**条数没变多，红的都变成了同一类黄——但必须说清楚变的是哪一半**：

- 改前 `ground-01` 那条红是「#14 的卡 kind 两份真值源对不上（timeline「」/已渲「keyword」）」，
  改后随 `kind` 补上、`cards.json` 重算而消失；腾出来的位置让 #14 进了词证判据，
  而它和另外 4 张一样查不到真值 → 多一条「窗口里没有词级真值」的黄。
- 改前 `protect-01` 那 5 条红是「#0 已渲的卡在时间轴上已经没有了」（11-11 的 `cards.json`
  连 `seg` 键都没有），重算后清零，换成 5 条同类的「窗口里没有词级真值」黄。

那 5 条黄是**既有盲区，不是卡有问题**：`check_cards.words_of_window() → evidence_guard.words_of()`
按 `h12(asset)` 找 ASR 缓存，而这批素材的缓存是按 `index.json` 里的 `legacy_key` 落的
（`render_film.load_chars()` 才读 `legacy_key`）。两支片子这 5 段全都取不到词 → 判据说
「关联查不了」。**这一单不许改 `scripts/`，所以没修**，建议单开任务（见第六节）。

### 4.3 其余门禁（都不是本单判据，列出来防止误读成「变好」）

下表「改前」= 本单开工时的盘上状态（11-11 渲出来的 `cards.json` + 当时那份 `subs.json`）。
注意 `protect-01` 的 `subs.json` 在 09:51:53 被另一个工位（T-026，已判收 `085612e`）重算过，
所以它的 `check_subs` / `verify_film` 变好与变坏都**不全是本单的事**，逐条拆在下面。

| 片子 | `check_timeline` | `check_subs` | `verify_film` |
|---|---|---|---|
| `ground-01` 改前 | error 2 · warning 20 | 没过（助词孤行 4 条） | 🟢26 🟡1 🔴3 |
| `ground-01` 改后 | error 2 · warning 20（不变） | 没过（同上，`subs.json` 没动） | 🟢23 🟡1 **🔴6** |
| `protect-01` 改前 | error 7 · warning 14（T-026 之前的基准） | 没过（孤行 3 条） | 🟢27 🟡1 🔴2 |
| `protect-01` 改后 | error 3 · warning 15（见下） | **过**（不是本单的功劳，见下） | 🟢24 🟡1 **🔴5** |

三条要解释清楚，否则会被当成我「顺手改好了」：

1. **两支片子的 `verify_film` 红都变多了，全部是同一类：「大字烧进画面」**——
   卡的出场时间变了（`ground-01` #9 28.63→29.88s、#11 44.43→45.68s、#12 53.78→55.03s，
   再加 #14；`protect-01` 五张卡全部换到锚词上），而 `out/film-hf.mp4` 还是 11-11 那条旧片，
   花的黄字当然不在新位置。**这正是 T-024 2.1 节写过的「重渲一次即消」**，
   也是「闸门一放开就该重渲」的原因：渲器本来就是先写 `out/cards.json` 再开渲，
   这一份就是它自己会写出来的东西，不是新判据、不是新缺陷。
   `protect-01` 这一处做过隔离 A/B（同一份今天的材料，只换 `cards.json`）：
   旧卡 🟢29 🟡1 🔴0 → 新卡 🟢24 🟡1 🔴5，五条红就是五张卡各一条。
   **真渲一次即清；在那之前，这两支片子的 `verify_film` / `golden.py` 读数会带着这 8 条红。**
2. **`protect-01` 的 `check_subs` 由「没过」变「过」**：**不是本单的功劳**。
   这一单没碰 `out/subs.json`（一个字节都没写）。是 T-026 在 09:51:53 用改过的入点重算了字幕，
   孤行/停留因此清零（T-026 判收记录：`verify 🟢29🔴0`）。
   同一批材料下把本单新增的 `kind`/`on` 从时间轴剥掉再跑，`check_timeline` / `check_cards`
   读数与带改动时**逐字相同**（`ground-01` 2e/20w、`protect-01` 3e/15w），
   即本单对这两条判据的贡献是 **0**。
3. **`protect-01` `check_timeline` 的 7e→3e** 也是 T-026 与 `085612e`（判据浮点假红修正）的功劳，
   不是本单：剩下 3 条正是 `TASKS.md` 里挂着未开工的 T-031（#6 / #12 音画差 0.02s、#1 起幅 0.00s）。
   那条「`#8` 台词里「分色」音频没说（2 处）」的黄属于 T-030（`evidence_guard` 认 `h12` 不认 `legacy_key`），
   与 4.2 节那 5 条同源。

---

## 五、负样本：判据一条没放宽

AGENTS.md 铁律 ④：「不许为了让校验通过去改产品行为、放宽断言、跳过用例」。判据没错，就不许为了让片子渲出来去放宽。以下是三条实测证据，
全部在 `/tmp/t027-run/qizao-ground-01`（活项目的副本）上做，做完即还原。
**没有用过 `--force`**，渲器的闸门原样生效。

### 5.1 负样本 A：删掉 `on` → 仍然判 error（闸门没被拆）

```
$ python3 scripts/render_hf.py --project /tmp/t027-run/qizao-ground-01 --library <素材库> --no-render
！声音在前没过，拒绝渲染（用 --force 可越过）：
   · 卡5（keyword「报价只贵 2–3」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
rc=5

$ python3 scripts/check_timing.py --project /tmp/t027-run/qizao-ground-01
  🔴 卡5（keyword「报价只贵 2–3」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
声音在前：error 1 · warning 0
rc=1
```

### 5.3 负样本 C：卡比锚词只晚 50ms → 仍然判「抢拍」

```
$ python3 scripts/check_timing.py --project /tmp/t027-run/qizao-ground-01   # start 手改到 anchor_t+0.05
  🔴 卡5（keyword「报价」）比锚词只晚 50ms（要 ≥150ms）——抢拍
声音在前：error 1 · warning 0
rc=1
```

### 5.2 负样本 B：`on` 写成窗口外的词 —— **这一条闸门抓不到，如实记**

```
$ # 把 ground-01 格 14 的 on 从「倍」改成「报价」（卡面有这两个字，这句话里没说过）
$ python3 scripts/render_hf.py --project /tmp/t027-run/qizao-ground-01 --library <素材库> --no-render
  ！大字对不上原话「报价」（段 14），沿用默认时机
rc=0                       ← 闸门放行

$ python3 -c "…out/cards.json[-1]…"
{"seg": 14, "kind": "keyword", "text": "报价只贵 2–3 倍", "anchor_word": "报价",
 "anchor_t": 64.43, "start": 68.31, "lag_ms": 3880, …}

$ python3 scripts/check_timing.py --project /tmp/t027-run/qizao-ground-01
声音在前：error 0 · warning 0
rc=0
```

**结论**：`render_hf.card_windows()` 找不到 `on` 时只往 stderr 打一句「大字对不上原话」，
然后**沿用默认时机**，并把 `anchor_word` 照样写成那个对不上的词、`anchor_t` 填成重音点。
`check_timing` 只判「有没有 anchor_word」「start 是否晚于 anchor_t」，**不回头核这个锚词在不在
这句真值里**——所以窗口外的 `on` 能混成绿灯。

这不是本单改判据造成的（`scripts/` 零改动），是既有口子。**修它要动 `render_hf.py`（T-018 占用中，
本单明令不许碰）或给 `check_timing` 加一条「锚词必须在本格真值里找得到」的判据（改判据必须单开任务）**，
两条都在本单边界外，见第六节。本单能保证的是：**我补的六个 `on` 全部逐字落在本格真值里**
（第三节的复查脚本逐个 `find() ≥ 0`，`check_cards` 的词证也没有因此新增红）。

---

## 六、如实报：这一单没解决、也不该由它解决的问题

1. **`ground-01` 格 14 的卡面与口播矛盾**：口播「原料成本可能差到 4 倍四到 5 倍」，
   卡面「报价只贵 2–3 倍」。2–3 这个数**全片任何一段真值里都没说过**（16 格逐格查过）。
   卡是有来历的（格 15 的 overlay 正是在解释「比原料价 vs 比成品总价」的口径差），
   但「2–3 倍」在片子里的出处只有项目方口径，不在口播里。锚词只能落「倍」。
   → 内容是编导口径，请复核人拍板：换词、换格、还是补出处。
2. **`protect-01` 格 2 的卡面「先保哪里」在真值里一个字都没有**（见 3.2），锚词取答案词「地面」。
3. **`check_cards` 的 5 条「窗口里没有词级真值」**（两支片子各 5 段）：根因是
   `evidence_guard.words_of()` 按 `h12(asset)` 找缓存，而这批素材的 ASR 缓存按 `legacy_key` 落盘，
   `render_film.load_chars()` 认 `legacy_key`、`evidence_guard` 不认。**卡的内容其实有真值可比，
   只是判据读不到**。这条已经由复核人挂成 **T-030**（`TASKS.md`「未开工」），本单不再重复开。
4. **`verify_film` 两支片子各多出来的「大字烧进画面」红** 是旧成片 vs 新卡时间的必然中间态
   （见 4.3 第 1 条），只有真渲一次才会消。本单只负责把闸门打开，重渲与 `golden.py --init`
   是后续任务的事。
5. **`render_hf` 对「`on` 落在窗口外」静默回落**（见 5.2）——**这条还没有任务号**，建议单开：
   要么让 `render_hf` 在找不到锚词时直接不写 `anchor_word`（让闸门自己拦），
   要么给 `check_timing` 加一条「锚词必须在本格词级真值里 find() 得到」。
   负样本就是 5.2 那段（`on="报价"` → 应该判红）。本单明令不许碰 `render_hf.py`，所以只记不动。
6. **并发留痕（不是本单改的）**：做这一单的同一时段，另一个工位在做 `protect-01` #8 入点
   （`timeline.json` 09:48:41、`out/subs.json` 09:51:53，即已判收的 `085612e` T-026），
   并在 09:5x 把 `out/cards.json` 还原成 11-11 的旧版本一次（本单的写入被盖掉，10:01 又写回）。
   现盘上 `protect-01` 的 `cards.json` 是带 `anchor_word` 的产线重算版，`check_timing` 0 error 复核过两次。
   两个工位同时写同一个项目本来不该发生（PROTOCOL 第三节），这里如实记下来。

---

## 七、复现命令

```bash
cd /Users/jerry/Downloads/AIworks/CutSkills-wsT027     # ai/cola/T-027
source scripts/runtime.sh env
P=/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects

# 1) 闸门：两支片子都该是 error 0
python3 scripts/check_timing.py --project "$P/qizao-ground-01"
python3 scripts/check_timing.py --project "$P/qizao-protect-01"

# 2) 卡准入：error 0，各 5 条「窗口里没有词级真值」的黄（既有盲区，见 6.3）
python3 scripts/check_cards.py  --project "$P/qizao-ground-01"
python3 scripts/check_cards.py  --project "$P/qizao-protect-01"

# 3) 锚词有没有真落在本格词级真值里（六个 on 逐个 find()）
python3 - <<'PY'
import json, sys
sys.path.insert(0, "scripts")
import lexicon
from render_film import load_chars
from pathlib import Path
LIB = Path("/Users/jerry/Downloads/柒造Video/素材"); P = LIB/"_cutskills/projects"
for slug, ns in {"qizao-ground-01": [14], "qizao-protect-01": [2, 4, 8, 9, 12]}.items():
    tl = json.loads((P/slug/"timeline.json").read_text()); lex = lexicon.load(LIB/"_cutskills")
    for el in tl["elements"]:
        if el["n"] not in ns: continue
        c = el["card"]; au = el.get("audio") or {}
        a_in = float(au.get("in", el.get("in") or 0))
        dur = float(el.get("out") or 0) - float(el.get("in") or 0)
        win = [(ch, st) for ch, st, _ in lexicon.fix_items(load_chars(LIB, au.get("asset") or el.get("asset")), lex)
               if a_in - 0.05 <= st < a_in + dur + 0.05]
        txt = "".join(ch for ch, _ in win); j = txt.find(c["on"])
        print(f"{slug} 格{el['n']} on=「{c['on']}」 在窗口内={j >= 0} 词起点={win[j][1] if j >= 0 else None}")
PY

# 4) 负样本 A：删掉一张卡的 on，闸门必须仍然拦
cp -a "$P/qizao-ground-01" /tmp/neg-A   # 删掉格 14 的 on、lock.py --lock 之后
python3 scripts/render_hf.py --project /tmp/neg-A --library "/Users/jerry/Downloads/柒造Video/素材" --no-render
# → 「！声音在前没过，拒绝渲染」，rc=5
```

---

## 八、这一单没做的事（免得被当成已做）

- 没改 `scripts/` 任何文件（`render_hf.py` 由 T-018 占用，本单明令不许碰）；
  没改 `check_timing` / `check_cards` / `evidence_guard` 的判据。
- 没碰 `TASKS.md` / `HANDOFF.md` / `AGENTS.md` / `SKILL.md` / `references/cli.md`。
- 没真渲成片、没动 `golden-baseline.json`（重渲要等 T-029 封 `proj79` 那一半）。
- **已知的 CI 副作用（合并前必须看一眼）**：新增本文件后 `python3 scripts/audit_skill.py`
  在工作树上从 🔴0 🟡1（`fixes-t024-golden.md` 未链）变成 🔴0 🟡2，多出来的就是本文件：
  「`references/fixes-t027-anchors.md` 没有从 SKILL.md 链过去」。
  补法是在 `SKILL.md` 的 references 清单里加一行链接——但 `SKILL.md` 在本单的禁改清单里，
  所以留给合并时一起补一行（否则合进主干后会多一条黄）。
  （`fixes-t024-golden.md` 那条已在 `085612e` 之前的主干上补过链，本条只算我这一份。）
