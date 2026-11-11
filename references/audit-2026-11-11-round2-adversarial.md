# 对抗式审查报告 · 2026-11-11（第二轮）

> 不是「再验一遍」，是**想办法把上几轮的结论打翻**。
> 攻击面紧盯：
> ① 配置写了一套、片子做另一套；
> ② 判据自己给自己打分（闭环自嗨）；
> ③ 判据里藏着旧假设或完全放水；
> ④ 声称的能力在代码里根本不存在或被旁路。
>
> **原则：每条带复现命令、带代码行、带实测数字。「假」= 声称的能力在运行时根本没生效。**

---

## 一、抓到的「假」（声称的能力在代码里被完全旁路）

### 🔴 1. 口播跳切吸附字界：实测脱靶率 100%（全走机械二等分）

- **声称**（`references/fixes-2026-11-11.md` 第一节）：
  > “一个句内每 ~2.4 秒硬切一次，切点吸附到字界（`chars` 的起点，±0.35s 内）”
- **攻击与代码定位**：
  查 `scripts/render_hf.py` 第 1564–1575 行：
  ```python
  aid = (el.get("audio") or {}).get("asset") or el.get("asset")
  starts = [c[1] for c in (chars_cache.get(aid) or [])] if aid else []
  rel = [0.0]
  for j in range(1, k):
      want = d * j / k
      if starts:
          near = min(starts, key=lambda x: abs(x - want))
          if abs(near - want) <= 0.35:
              want = near
  ```
  `starts` 取自 `load_chars`，返回的是 ASR 在原始整段视频里的**绝对时间戳**（例如 `熊工口播3` 中当前 clip 在原始视频的 436.26s 起始，`starts` 里的时间都是 436.xx ~ 441.xx）。
  而 `want = d * j / k` 是从当前切片自身 0.0 开始算的**相对时长（例如 2.46s）**！
  代码直接比对 `abs(near - want)`，差值高达几百秒（26s ~ 430s），导致 `abs(near - want) <= 0.35` **恒为 False**！
- **真项目数据验证**：
  在真项目 `qizao-naming-01`（包含 7 个真实口播与空镜 clip）上实测：
  - clip 1（`d=4.92s, in=436.26s`）：`want=2.46s, near=28.56s`，偏差 `26.10s` → 脱靶
  - clip 3（`d=4.86s, in=392.04s`）：`want=2.43s, near=28.56s`，偏差 `26.13s` → 脱靶
  - 全片 7 个 clip 的切点吸附命中率为 **0/7（0.0%）**！
- **定性**：声称已落地的核心能力在运行时 100% 处于静默失效状态。切片从未吸附到任何字音边界，全凭机械等分。正确做法必须带上切片入点偏移：`want_abs = t_in + want`，吸附后换回相对时间 `near - t_in`。

---

### 🔴 2. `verify_film.py` 潜藏未爆 `NameError`（未声明变量 `p`）

- **声称**（`references/audit-2026-11-11-adversarial.md` A8）：
  > “timeline.py 写时间轴时带上；render_hf 认 brief.pack；verify_film 认 brief.plan。把 demo 时间轴里的 pack/plan 删掉、只留 brief.json，渲出来仍对……”
- **攻击与代码定位**：
  查 `scripts/verify_film.py` 第 101 行：
  ```python
  def load_timeline_or_edl(proj: Path) -> dict:
      ...
      "plan": tl.get("plan") or _brief_plan(p),
  ```
  函数形参为 `proj`，作用域内**压根没有变量 `p`**！
- **复现证明**：
  构造任何 `plan` 为空的 `timeline.json` 运行验收，立即崩溃：
  ```
  NameError: name 'p' is not defined
  ```
  之前未报错纯粹是因为测试样片的 `timeline.json` 恰好显式填了 `"plan": "S2-live-mid"`，触发了 Python 的短路求值。这是一条从未被覆盖到的残缺代码。

---

### 🔴 3. 依赖「只装一次」被渲染器击穿：默认强行走 `npx` 联网

- **声称**（`STATUS.md` 第 54 行）：
  > “依赖只装一次：全部落 `~/.cutskills`（5.5 G），`source scripts/runtime.sh env`”
- **攻击与代码定位**：
  查 `scripts/render_hf.py` 第 32 行：
  ```python
  HF = os.environ.get("CUTSKILLS_HF", "npx --yes hyperframes")
  ```
  即使用户已经通过 `runtime.sh install` 在 `~/.cutskills/npm/node_modules/.bin/hyperframes` 中安装好本地可执行文件，只要调用方未显式 `export CUTSKILLS_HF=hyperframes`，脚本**一律硬编码执行 `npx --yes hyperframes`**。
- **代价与实测**：
  1. 每次 lint 都会强行调用全局 `npm` 访问 npm registry 查源（实测网络握手消耗 43 秒）；
  2. 触发 `~/.cutskills/npm-cache/_npx` 下海量嵌套目录的锁争抢与路径过长告警（`ENAMETOOLONG`），导致进程持续占用 CPU 60+ 秒。

---

## 二、抓到的「闭环自证与放水」（判据自己打分或防御失效）

### 🟡 4. B 轴「场景内微事件间隔」：纯 Paper JSON 循环验证

- **声称**（`references/fixes-2026-11-11.md` 第三节）：
  > “验收 🟢23 0红：场景内微事件间隔 2.30s 🟢”
- **攻击与代码定位**：
  查 `scripts/verify_film.py` 第 684–708 行：
  ```python
  _tlp = proj / "timeline.json"
  _els = (json.loads(_tlp.read_text()).get("elements") or []) if _tlp.exists() else []
  ...
  for _it in _ms:
      ...
  add("场景内微事件间隔", f"最大 {_mx:.2f}s", "green" if _mx <= 2.5 else "yellow")
  ```
  验收脚本自称“在成片像素上量，不在配置上量”，但在这一项上，它**纯粹是去读 `timeline.json` 里用户手动填写的 `micro` 数组**，然后计算两个相邻浮点数相减！
  再查渲染器：`scripts/gen_scene.py` 全文没有任何一处读取或处理 `micro` 参数。
- **定性**：
  渲染端在按硬编码写死的关键帧在动，根本不知道 `micro` 为何物；验收端对着用户自己写的 JSON 数组算加减法并宣称“成片合格”。典型的闭环自证。

---

### 🟡 5. P2 双语双行字幕：验收端处于「零防线裸奔」

- **声称**（`references/fixes-2026-11-11.md` 补记）：
  > “双语双行从来没上过屏……修好后实测 HTML 里 18 个 cap-bi 块 / 18 行英文”
- **攻击与代码定位**：
  查 `scripts/verify_film.py`：
  整个验收脚本里，**没有任何一条断言在检查第二行英文的存在性、清晰度或版面位置**！
  `verify_film.py` 将 `bilingual` 与 `single-line` 混同处理，仅在单条主字幕带内采集像素比率。
- **事实与隐患**：
  1. 如果一个项目配置了 P2（声明 bilingual），但目录下根本没有 `subs-en.json`，成片只渲染出单行中文，`verify_film.py` 依然给出 **🟢 30 满分全绿**；
  2. 如果由于样式调整，英文行再次被推到屏幕外或发生遮挡，验收体系完全失明，无法起到把关作用。

---

## 三、基线破坏（真实 Regression）

### 🔴 6. `golden.py` 回归套件在 master 上直接失败（退出码 1）

- **声称**（`STATUS.md` 第 24 行）：
  > “2 条样片进基线（proj79 19 绿、qizao 13 绿），改脚本后跑一遍就知道退化没有”
- **攻击与执行结果**：
  在项目根目录下直接执行 `python3 scripts/golden.py`：
  ```
    proj79: 🟢 26（基线 19）· 🟡 0 · 🔴 0  OK
    qizao-ground-01: 🟢 23（基线 13）· 🟡 2 · 🔴 1  REGRESSED

  回归：2 条样片 · FAIL
    ✗ qizao-ground-01: 绿灯从 13 掉到 23，红灯 1
  ```
  `qizao-ground-01` 报真红：
  `🔴 字幕最宽（像素） 111%W 上限 92%W（80px × 15 字 = 1200px / 1080px）——超了会被画幅切掉两头`
- **定性**：
  新增的像素级宽度硬拦截规则打翻了历史样片的合法性，导致回归基线处于持续破损（FAIL）状态，未经修复即被宣告“主链路全绿”。

---

## 四、经受住攻击的基石（审查后仍成立）

1. **纸感包字幕反色描边（`cap-light` / `cap-ink`）**：在实拍暗画面与浅底代码画面之间的字色自适应与描边渲染真实生效，通过不同背景下的像素灰度对比实测。
2. **多包装切点一致性**：`check_packs --project` 在 P1/P2/P3 三套截然不同的包装渲染下，抽取出的 9 格切点时间戳逐帧对齐，且跑完后能可靠还原原始 `hf/index.html`。
3. **杜比视界色彩映射**：Profile 8.4 杜比视界素材在 `CUTSKILLS_FFMPEG` 链路上被压至 BT.709 后的色彩饱和度与明度经成片直检确认无误。

---

## 五、建议整改清单

| 编号 | 严重度 | 修复目标 | 验收口径 |
|---|---|---|---|
| 1 | 🔴 紧急 | 修复 `render_hf.py` 跳切吸附的基准时间戳偏移（引入 `t_in` 修正） | 真项目 `qizao-naming-01` 口播 clip 吸附命中率达到真实有效 |
| 2 | 🔴 紧急 | 修复 `verify_film.py` line 101 的 `_brief_plan(p)` 变量名错误 | 空 plan timeline 读取时不抛 NameError |
| 3 | 🔴 紧急 | 修复 `render_hf.py` 的本地 `hyperframes` 嗅探逻辑（`shutil.which`） | 本地有二进制时不触发 npx 联网调用 |
| 4 | 🟡 重要 | 补充 `verify_film.py` 对双语第二行英文的成片像素级校验 | 无英文稿或英文出画时必须判黄/红 |
| 5 | 🟡 重要 | 修正 B 轴微事件判断，杜绝仅做 JSON 差值运算的自闭环 | 引入成片局部帧差变化作为动态依据 |
| 6 | 🔴 紧急 | 处置 `qizao-ground-01` 字幕超宽并校准基线 | `python3 scripts/golden.py` 退出码恢复为 0 |
