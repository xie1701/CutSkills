# 第二轮对抗式审查 · 逐条修复记录（2026-11-11）

对应报告：[audit-2026-11-11-round2-adversarial.md](audit-2026-11-11-round2-adversarial.md)。六条，逐条给复现口径与实测数字。

---

## 🔴 1. 跳切吸附字界 100% 脱靶

**病因**：字级时间戳是**原始整段素材的绝对时间**（`熊工口播3` 里当前 clip 从 436.26s 起），
而 `want = d * j / k` 是从这一刀自己 0.0 起算的**相对秒数**。两者直接相减，差值几百秒，
`abs(near - want) <= 0.35` 恒为假。所以「切点吸附到字界」从来没发生过一次。

**修法**（`render_hf.py`）：带上传入点偏移再比。

```python
a_in = float((el.get("audio") or {}).get("in") or el.get("in") or 0.0)
near = min(starts, key=lambda x: abs(x - (a_in + want)))
if abs(near - (a_in + want)) <= 0.35:
    want = round(near - a_in, 3)
```

并且**把命中率打出来**，不再允许「声称已吸附」这种说法：

```
切点吸附字界：6/7（86%）· 其余按机械等分        # qizao-naming-01
切点吸附字界：14/17（82%）· 其余按机械等分      # qizao-ground-01
```

没吸附上的那 1/7 是「最近的词边界在 0.35s 之外」——宁可机械等分，也不为了吸而去切在句子中间。

---

## 🔴 2. `verify_film.py` 潜藏 `NameError: name 'p' is not defined`

**病因**：`load_timeline_or_edl(proj)` 里写成 `_brief_plan(p)`，形参叫 `proj`。
样片的 `timeline.json` 恰好写了 `plan`，`or` 的短路求值把这条残肢藏了整整几轮。

**修法**：改 `_brief_plan(proj)`。

**负样本**：造一个删掉 `plan`/`pack`、也不放 `brief.json` 的项目目录。

```
无 brief.json 时：plan = '' · 没抛 NameError
```

---

## 🔴 3. 「依赖只装一次」被渲染器击穿：默认强行走 npx 联网

**病因**：`HF = os.environ.get("CUTSKILLS_HF", "npx --yes hyperframes")`。
`~/.cutskills/npm/node_modules/.bin/hyperframes` 明明装好了，只要调用方没 export，一律联网。

**修法**：新增 `_resolve_hf()`，顺序是 `CUTSKILLS_HF` → `~/.cutskills/npm/node_modules/.bin/hyperframes`
→ 仓库内 `node_modules/.bin` → `/opt/homebrew/bin` → `shutil.which` → 最后才 `npx`。

**实测**：同一次 `--no-render`（含 lint）从「40 秒量级、常撞 `ECOMPROMISED`」变成 **1.52s**。
当前解析结果：`HF = /Users/jerry/.cutskills/npm/node_modules/.bin/hyperframes`。

---

## 🟡 4. 双语第二行：验收端零防线

**病因**：`verify_film.py` 把 `bilingual` 和 `single-line` 混同处理，只在主字幕带数同色像素。
项目没英文稿、或英文行被推出画面，照样 🟢30。这件事**刚刚真的发生过一次**（英文行整行没渲出来）。

**修法**：加「双语第二行」判据，三件事：

1. 包装声明 `bilingual` 但项目里没有 `subs-en.json` → **红**。
2. 到成片像素上量**字幕块的行跨度**：双语两行会把块撑到 `1.6×字号`，单行只有 `0.9×字号`；
   门槛取 `1.55 × size_px`（72px → 111px）。
3. 只取**没有被整屏代码画面压着**的字幕时刻（代码画面本身满是高对比图形，行剖面跨度能顶到
   181px，量到的是背景）；跨度顶满扫描窗的帧一律丢掉。

**实测**：
- P2 成片：`4/4 帧字幕块高 120–181px`（门槛 111px）🟢
- 拿 P1 成片冒充双语片：`1/2 帧…74–142px` 🔴
- 删掉 `subs-en.json`：`项目里没有 subs-en.json` 🔴

**已知脆弱点**：如果一支片里能被干净量到的字幕帧不足 2 帧（长时间全屏代码画面），这条不出结论——
宁可不判，也不拿背景冒充字。

---

## 🟡 5. B 轴微事件是 JSON 自证

**病因**：验收读 `timeline.json` 里手填的 `micro` 数组算差值，而 `gen_scene.py` 从来没读过 `micro`
（它按自己的关键帧在动）。渲染端不知道 `micro` 是什么，验收端对着它算加减法判绿——闭环自证。

**修法**：改成在成片上逐帧量（10 fps / 270px 宽），判**最长静止**：

```
帧差中位 med；静止阈值 eps = max(0.25, 0.35×med)
最长连续帧差 < eps 的时长 → 超过 2s 判黄
```

为什么不是「事件间隔」：慢转场、缓推镜头每帧只差一点点，拿「帧差超中位两倍」当事件，
会把一段正常的长溶解判成 2.6s 空档（实测过，假红）。真正的病是**一模一样**挂在那里。

**实测**：
- `qizao-lime-baxis`：最长静止 **0.30s**、帧差中位 0.81 → 🟢
- 负样本（8 秒纯色静帧冒充 B 轴片）：最长静止 **7.90s**、帧差中位 0.00 → 🟡（同时另一条
  「代码画面不是静图」判红 7.83s）

---

## 🔴 6. `golden.py` 在 master 上直接失败

两个毛病：

**a) `qizao-ground-01` 真有一条红**：`字幕最宽 111%W`。
根因是旧的 `subs.json` 还是 15 字一条（像素 1200 > 1080），渲染器换了但项目没重跑。
按新链路重渲后：**35 条字幕、中位 7.6 字、最宽 81%W**，`字幕最宽` 🟢。

**b) 对比逻辑会「数字往上走、话往下说」**：基线只存 `green`，于是「绿灯 13 → 23、但多了一条红」
被打印成「绿灯从 13 掉到 23」。修法：基线连 `red` 一起存，两个方向分开报。

```
proj79:           🟢 26（基线 26）· 🟡 0 · 🔴 0（基线 0）  OK
qizao-ground-01:  🟢 26（基线 26）· 🟡 0 · 🔴 0（基线 0）  OK
回归：2 条样片 · OK      退出码 0
```

**负样本**（把 ground 基线临时改成 40）：`绿灯 40 → 26（掉了 14 条）`，退出码 **1**。

---

## 顺带修掉的

- `qizao-ground-01` 的 `#15 compare` 缺数字出处（`check_timeline` 唯一 error）：
  补 `approx: true` + `source_note`（价差来自项目方口径，未逐品牌核价）+ `mg_role: calibration` +
  `micro`。`check_timeline` 现在 **error 0**。
- `qizao-protect-01` 按新链路重渲（旧片同样卡在 15 字宽 + 4.82s 口播中位）。

---

## 复跑总表（本次全部重跑）

| 项目 | 验收 | 备注 |
|---|---|---|
| naming-01 · P1-warm | 🟢30 🟡0 🔴0 | 口播中位 2.45s |
| naming-01 · P2-paper | 🟢31 🟡0 🔴0 | 多出的一条是「双语第二行」，4/4 帧 |
| naming-01 · P3-dark | 🟢24 🟡0 🔴0 | 无字幕层，靠大字承担台词 |
| ground-01 | 🟢26 🟡0 🔴0 | 字幕 81%W、口播中位 2.29s、吸附 14/17 |
| protect-01 | 🟢26 🟡0 🔴0 | 字幕 28 条、吸附 13/13、口播中位 2.37s（旧片卡在 15 字宽 + 4.82s） |
| lime-baxis（B 轴） | 🟢23 🟡0 🔴0 | 最长静止 0.30s |
| golden 回归 | 退出码 0 | **3 条样片**（proj79 / ground-01 / protect-01，全 🟢26 🔴0） |
