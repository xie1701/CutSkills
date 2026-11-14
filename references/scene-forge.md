# 场景锻造 · AI 从对话到叙事场景的闭环（R2 落地）

> 目标：**用户提出没见过的主题，AI 不需要程序员帮忙就能产出叙事场景并自我修正。**
> 三个内置场景（`queue_block` / `order_cash` / `quote_diff`）只是参考实现与回归案例，
> 不是模板库——新主题走这条闭环，不在 `scripts/scenes/` 里继续堆手写场景。

## 一、闭环七步（每步都有机器判据，不许跳）

| 步 | 做什么 | 用什么命令 | 过线条件 |
|---|---|---|---|
| ① 建任务单 | `--init` 生成空白 spec | `python3 scripts/scene_spec.py --init <项目> <scene_id> <kind>` | 文件出现在 `<项目>/scenes/<scene_id>.json` |
| ② 答六问 | subject/action/change/camera/audience/sound，一句人话一拍 | 直接改 spec | 六问全非空；`claim_source.evidence` 是**口播原句** |
| ③ 写代码 | `<项目>/scenes/<scene_id>.py`，导出 `SCENE_ID` / `scene(t,W,H,dur)`，**纯 t 驱动、自包含** | 对话里让 AI 写 | 不 import 白名单外模块（沙箱会拒） |
| ④ 试渲 | 沙箱里渲：拒网 + rlimit + 超时 | `python3 scripts/scene_sandbox.py --scenes-dir <项目>/scenes --scene <id> --out <mp4> --dur --fps --size` | exit 0；被拒就改代码，不拆围栏 |
| ⑤ 量动静 | 帧差剖面 + `--probe` 亮度节拍 | `python3 scripts/gen_scene.py --scene <id> --scenes-dir <项目>/scenes --probe --dur …`；`scene_review.py --video … --events …` | 动帧 ≥0.85、最长静止 <1.2s、帧差中位 1.2–6.0 |
| ⑥ 修 | 按读数改代码，回到 ④ | — | 每轮修改记进 spec 的 `attempts` |
| ⑦ 签收 | 量完三项 review 写进 spec | 手改 `review.visual/audio/truth` 为 `ok` | `scene_spec.py --check` 0 error → gate 才放行 |

## 二、硬约束（沙箱与门禁会替你执行，别绕）

1. **场景代码自包含**：只准 import `numpy / PIL / math / colorsys / json / pathlib / typing` 等白名单模块；`socket / subprocess / os / sys / eval / exec` 一律拒绝。
2. **纯 t 驱动**：`scene(t, W, H, dur)` 同一 t 永远同一帧（确定性合同，`test_scene_determinism.py` 会抓）。
3. **渲染必经沙箱**：渲器对项目级场景自动走 `scene_sandbox.py`；手工调试也请走沙箱，别裸跑 `gen_scene`。
4. **没有签收的场景渲不进成片**：`template: scene` 的元素必须有 `props.scene_spec`，且 review 三项不是 pending——否则 `scene_spec --check` 与 `gate` 都判 error（2026-11-14 收紧）。
5. **事件单一真值源**：spec 的 `audio.events` 是锚点第一出处；`props.events` 没写时渲器自动从 spec 编译，两边都写则逐项对账、漂移必报。

## 三、缓存与迭代

场景 mp4 的缓存键含**场景源码 SHA1 + 分辨率 + 时长 + fps + marks**——改一行代码就会重渲，
不存在「改了没生效」。调试期想强制重画，删 `<项目>/hf/clips/scene-<name>-*.mp4` 即可。
