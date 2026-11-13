# T-024 · golden 三支样片的红：逐条归因，以及两处挡在重渲前的闸门

数据与结论都来自 2026-11-13 在 `ai/cola/T-024` 上的实测（命令见文末）。
分工先说清：**这一单没有改判据、没有改基线**。原因是诊断到一半就撞上了两处不在本单范围的阻塞，
把红记进基线等于假绿，所以 `python3 scripts/golden.py` 现在仍是退出码 1。

---

## 一、结论先说

- 三支样片共 8 条红，**5 条确系旧产物过期**（用现行 `subs.py` 重算即消），
  **1 类不是过期**：`字幕停留` 在 `ground-01` / `proj79` 重算后仍各留 5 条（3.30–4.08s）。
- 「重新生成字幕并重渲」这一步**在三支片子上都做不下去**：`render_hf.py` 渲染前的
  「声音在前」闸门把它们全拦下（`ground-01` 缺 1 个卡的锚词、`protect-01` 缺 5 个、
  `proj79` 没有 `timeline.json`，闸门自己抛异常）→ 退出码 5。`--force` 能越过，
  但铁律 ④ 不许拿它换绿灯。
- `proj79` 还多一层：它是 v2（只有 `edl.json`），`lock.py` 只认 `timeline.json`，拍不了板；
  成片 `out/film-hf.mp4` 被 `.gitignore` 掉，只存在于主工作树，
  而 `golden-baseline.json` 钉的正是主目录绝对路径。

---

## 二、逐条归因

### 2.1 读数对照（`verify_film.py`）

| 样片 | 现在（旧产物） | 现行 `subs.py` 重算、**未重渲** | 逐条 |
|---|---|---|---|
| `proj79` | 🟢27 🟡0 🔴3 | 🟢28 🟡0 🔴2 | 文字 36/37 → 36/36 ✅<br>孤行 2 → 0 ✅<br>停留 8/37 → 5/36 🔴 |
| `qizao-ground-01` | 🟢26 🟡1 🔴3 | 🟢27 🟡0 🔴3 | 文字 34/35 → 36/36 ✅<br>孤行 4 → 0 ✅<br>单条最长 11 单位 🟡 → 10 ✅<br>停留 4/35 → 5/36 🔴 |
| `qizao-protect-01` | 🟢27 🟡1 🔴2 | 🟢27 🟡1 🔴2 | 文字 ✅（重算后全对得上）<br>超宽 1 → 0 ✅<br>孤行 3 → 1 🔴<br>停留 5/28 → 0 ✅ |

「重算后反而多出一条 `大字烧进画面` 红」不是新问题：`cards.json` 已按现行代码重算、
成片还是 11-11 那条，卡片时间对不上是**必然**；重渲一次即消。所以这张表只能读字符串级那几条
（文字 / 孤行 / 停留 / 超宽），像素级要重渲之后才有意义。

### 2.2 `字幕文字对得上声音` —— 旧产物，任务前提成立

`cbcc3ef` 把「剔拖音」只加在了 **evidence 一侧**，字幕一侧靠 `subs.py` 生成时自己剔
（`text = lexicon.strip_noise(text)`）。于是 11-08/09 生成的旧 `subs.json` 里那句
「都有偏颇嗯」永远对不上剔过噪的 evidence —— `proj79` 36/37、`ground-01` 34/35 就是这么红的。
现行产线重算后 36/36、36/36 全过。**这条不改判据也能消，前提是字幕重产。**

### 2.3 `助词孤行` —— 两条是旧产物，一条是真缺陷

- `ground-01`：4 条 → 0 条（旧断句「的厦门的汕」「最后业主方怎么都觉得」全没了）。
- `proj79`：2 条 → 0 条。
- `qizao-protect-01`：3 条 → **1 条**，剩下这条是
  「的分色纸因为只有」起头是「的」。它的 EDL 段 evidence 本身就是
  「**的分色纸。**因为只有这样它的边角细节才能贴得更直。」——
  **切点落在「的」上**，是上游选句/入点的问题，字幕怎么切都救不了。
  归因：`timeline.json` #8 段入点提前了 0.4s 左右。

### 2.4 `字幕停留` —— 不是过期，是两条自订规则在慢语速上打架

- `protect-01`：5 条 → **0 条**，这条红确实纯属旧产物。
- `ground-01` / `proj79`：重算后仍各 **5 条**（3.30 / 3.30 / 3.54 / 4.08 / 3.36s）。
  这五条不是「赖在屏幕上」：拿 `scripts/silence.py`（相对阈值的真静音探针）
  在这五条窗口里量，**静音 0.00s** —— 说话人真的连着讲了 3.3–4.1 秒才把 8–9 个字说完
  （ASR 把一处停顿记进了字里：「其中有」的「有」占 1689.60–1691.22 = 1.62s）。

  而 `subs.py` 自己的第 ③ 条规则是「一段字幕必须是一个完整呼吸组」「只在有停顿的地方拆」
  （`GAP_SPLIT = 0.12`）。**没有停顿就不许拆**；`check_subs.HOLD_MAX = 3.2` 又是硬红。
  两条规则在慢语速上无解，要归零只有两条路：

  1. 改判据：给「收在句读/宽度上、语音覆盖满整条」的长条留一条判定通道；
  2. 改产线：3.2s 以上强行在词边界拆一刀（`)` `subs.choose_cut` 已有词界打分）。

  两条都会动验收口径或产品行为，按铁律 ④ 不属本单，需要单开任务并配负样本
  （负样本：3.3s 但话在 2.0s 就说完 → 必须仍判红；正样本：语音覆盖 3.3s → 应放行）。

---

## 三、重渲为什么做不下去（两处闸门）

### 3.1 声音在前闸门（`check_timing.py`，`6a696be` 起）

`render_hf.py` 写完 `subs.json`/`cards.json` 之后、开始渲染之前跑 `check_timing`，
有 error 直接 `return 5`：

```
$ python3 scripts/check_timing.py --project <qizao-ground-01>
  🔴 卡5（keyword「报价只贵 2–3」）没有锚词——这张卡没有声音依据，先写 `on` 再渲
声音在前：error 1 · warning 0

$ python3 scripts/check_timing.py --project <qizao-protect-01>
  🔴 卡1（keyword「先保哪里」）没有锚词 …
  🔴 卡2 … 卡3 … 卡4 … 卡5 …
声音在前：error 5 · warning 0

$ python3 scripts/render_hf.py --project <proj79>
！声音在前检查没跑起来：[Errno 2] No such file or directory: …/proj79/timeline.json
（退出码 5；check_timing.check() 第 56 行硬读 timeline.json）
```

`ground-01` / `protect-01` 的卡是 11-11 的写法（`{"text": …, "at": null, "dur": …}`，
`ground-01` 卡 5 是 `{"text": "报价只贵 2–3 倍", "at": 0.6, "dur": 2.6}`），都没有 `on`。
补 `on` 是编导口径（决定这块大字压在哪句话上），改闸门是改判据 —— 两者都不在本单。

### 3.2 `proj79` 的形态问题

- 没有 `timeline.json`（v2 老样片，只有 `edl.json`）→ `lock.py --lock` 直接 `FileNotFoundError`，
  签不了锁；`check_timing` 同样抛异常。
- `references/packtest-2026-11-08/.gitignore` 里 `proj79/out/*.mp4` 被忽略，
  它的 `film-hf.mp4` 只存在于主工作树；`golden-baseline.json` 里 `path` 钉的是主目录绝对路径，
  所以 golden 从任何 worktree 跑，读的都是主目录那一份。在本单「不碰主目录」的前提下，
  proj79 的成片无法在分支里更新。

---

## 四、这一单实际做了什么 / 没做什么

**做了**
- 三支样片的字幕全部按现行 `subs.py` 重算过并逐条比对（数字见 2.1–2.4）；
  五条停留红的窗口逐条用静音探针证伪了「赖在屏幕上」。
- `ground-01` / `protect-01` 用过 `lock.py --lock --note "T-024 golden 基线重渲"`，
  随后因为渲染无法进行，**已删除这两个 `lock.json`**（不留假拍板）。
- 诊断渲染（`render_hf.py`，未加 `--force`）在两个库项目上跑到闸门即退出，
  `out/{subs,shots,cards}.json` 被重算过：`subs.json` / `shots.json` 已按原字节还原；
  `cards.json` 的原始字节被我覆盖丢失，现按成片实测的字块出现区间重建
  （cards 9/11/12 的 `start` 从 29.877 / 45.684 / 55.027 回到 anchor+0.15
  = 28.627 / 44.434 / 53.777，与成片 28.7–29.9 / 44.5–45.3 / 53.8–55.8 对上）。
  还原后 `verify_film` 读数回到 🟢26 🟡1 🔴3，与开工前一致。

**没做（故意）**
- 没有改 `golden-baseline.json` —— 把 8 条红记进基线就是假绿。
- 没有动 `verify_film.py` / `check_subs.py` / `subs.py` / `render_hf.py` / `lock.py`。
- 没有写 `TASKS.md` / `HANDOFF.md` / `AGENTS.md`。
- 没有在主工作树里改任何文件（`golden.py` 自己会往 `proj79/verify-report.md` 写，这是它原有行为）。

---

## 五、下一步（建议单开任务，按依赖顺序）

1. **内容**：`qizao-protect-01` #8 段入点收 0.4s，去掉起头的「的」（这条孤行是真缺陷）。
2. **闸门**：老项目的卡没有 `on`。要么给「`at` 卡片」一条显式通道（写明它没有声音依据、
   由人签），要么批量补 `on`（编导口径，会动卡片出场时机）。这一步不清，两支库样片永远渲不出来。
3. **形态**：`proj79` 没有 `timeline.json`。升级到 v4，或让 `lock.py` / `check_timing` 回落 `edl.json`。
4. **判据**：`字幕停留` 的 3.2s 硬上限对「单呼吸组慢语速」误报 —— 必须配负样本
   （话 2.0s 就说完、条撑 3.3s 的假长条仍要判红）。
5. 1–4 清掉之后，本单原计划（三支重渲 → `golden.py --init` → `golden.py` 退出码 0）才跑得动。

---

## 六、复现命令

```bash
cd <worktree>
source scripts/runtime.sh env
P=/Users/jerry/Downloads/柒造Video/素材/_cutskills/projects

python3 scripts/verify_film.py    --project references/packtest-2026-11-08/proj79
python3 scripts/verify_film.py    --project "$P/qizao-ground-01"
python3 scripts/verify_film.py    --project "$P/qizao-protect-01"
python3 scripts/check_subs.py     --project "$P/qizao-ground-01"     # 停留/孤行 的同一份判据
python3 scripts/check_timing.py   --project "$P/qizao-ground-01"     # 卡没有锚词 → 渲不动

# 字幕重算（会在「声音在前」闸门退出，退出码 5；subs.json 在闸门之前就已落盘）
python3 scripts/render_hf.py --project "$P/qizao-ground-01"
python3 scripts/render_hf.py --project "$P/qizao-protect-01"

python3 scripts/golden.py            # 退出码 1：0→3 / 0→3 / 0→2 条红
```
