# G 风险追踪表（评审报告 §8 · 2026-11-14）

> 来源：《CutSkills 产品方向守护与迭代路线总报告》§8。基线 `0980c01`。
> 口径：每项先有**可执行失败样例**证明确实会失败，再修；修完负样本转正、旧产线回归不回退。

| ID | 优先级 | 风险 | 失败样例 | 修复 | 验收证据 |
|---|---|---|---|---|---|
| G-01 | P0 | `render_hf.scene_mp4` 先查内置 SCENES 注册表，项目级新场景没被注册就提前报「场景不认识」 | `test_r0_guards.py::test_g01_new_scene_routes` | 有 `proj/scenes/<name>.py` 就放行进沙箱，不再要求先进注册表 | 新名字场景从 timeline 真渲出 mp4 |
| G-02 | P0 | `gen_scene.register_scenes_dir` 扫目录 import 全部 .py——目录里未审查的同伙文件顶层代码会被执行 | `test_r0_guards.py::test_g02_no_dir_scan`（同伙文件写标记文件） | 沙箱改走 `gen_scene --scene-file`，只加载静态检查过的目标文件，且 SCENE_ID 必须与请求一致 | 同伙文件零执行 |
| G-03 | P0 | 本机没有 `sandbox-exec` 时打警告降级裸跑；资源/输出围栏不完整 | 负样本 `test_g03_no_sandbox_exec_refuses` + 正样本 `test_g03_sandbox_positive_deny`（实测 profile 拒网、拦 /Users 写盘） | 无隔离 → 拒绝执行 AI 场景（exit 5）；显式 `--allow-unsandboxed` 才放行（开发用）。口径注：profile 是「拒网+定点放行写盘」，读/执行仍放开——完整 default-deny 需枚举系统库路径，成本高收益低，已如实标注 | 无围栏不跑代码 |
| G-04 | P0 | `scene_spec.review` 凭 `ok` 字符串签收，与场景代码、音效事件无绑定——签收后改代码签收仍有效 | `test_r0_guards.py::test_g04_sign_binds_hash` | 签收必须绑定场景源码 SHA1 + 音效事件指纹（`scene_spec.py --sign`）；代码/事件变动签收自动失效 | 改一字节即变待审 |
| G-05 | P0 | `gen_props_sfx` 与任务单音效事件不一致只警告，继续以 props 为准渲 | `test_r0_guards.py::test_g05_events_mismatch_hard_fail` | 不一致直接拦截（渲染中止），单一真值源是任务单 | 两边不一致渲不起来 |
| G-06 | P0 | `ideate.py` 钩子与兜底卡仍含无来源数字/效果承诺（「90%的人」「翻倍」） | `test_r0_guards.py::test_g06_no_unsourced_claims` | 全部改成非量化表述（「大多数人」「上一个台阶」） | 源码扫描零命中 |
| G-07 | P0 | `write_sfx_manifest` 对 licence_of=None 回退写死 "CC0 1.0 Universal"；`pick_sfx/pick_bgm` 无条件返回 LICENCE 字符串——目录即证据 | `test_r0_guards.py::test_g07_manifest_no_cc0_fallback` | 许可只认 `licence_of`（留痕文件）；查不到就标 `unverified` + 商用前人工确认；summary 如实计数 | 无留痕 ≠ CC0 |
| G-08 | P1 | 旧内置场景函数在 gen_scene.py 内、场景依赖与 props 可能未入缓存指纹 | — | R1 黄金样片 B 期间验证，必要时补 | 待办 |
| G-09 | P1 | 「新场景语义—观看—版本签收」链条不完整，gate 只看字符串签收 | gate.py:307 走 scene_spec --check；活证据：r1b field-flood 改 timeline dur 0.05s → 签收当场失效（tl_sha1），还原重签全绿（2026-11-15 反证⑤） | 指纹绑场景代码+音效事件+时间线 dur/marks，任一变动 error | 已完成 |
| G-10 | P1 | `test_scene_determinism.py` 只测既有 phase；没有「新场景创作」的端到端测试 | r1b field-flood 锻造留痕（references/r1-r2-eval-2026-11-15.md）+ selftest 场景链 | 通用「新场景创作」回归仍缺，暂以样片 B 留痕兑现 | 部分完成 |

## R3/R4 兑现记录（2026-11-15 凌晨）

> 术语速查：**净抬升**＝音效真实可听度（成片 vs 底音同窗相减，≥2.5dB 过线，见 references/sfx.md）；**词级真值**＝每个字的精确时间区间（见 references/glossary.md）；**重签**＝拍板锁重新盖章。

- **R3 跨机移植验收**（`/tmp/r3machine`，CUTSKILLS_HOME 全新）：① 首装 doctor ❌0 ✅8；②③ 零素材环境选题→成片（r3-oldphone，gate 0 error 16w）；④ 移机续改成功——verify 🟢37 🟡3 🔴0、字幕 16 条全过、重签正常。
  - **新发现的真边界**：「项目移机」必须连素材库侧的 `_vo` 配音资产与 `index.json` 资产条目一起搬，否则重渲后词级真值丢失、字幕 0 条。这是移植口径的硬要求，陌生人文档需讲清。
  - **产品语义补丁**：用户主动要求「音效小声点」可能把净抬升压到可听度门禁线以下——`revise.py` 已加提醒，渲后必须把 check_sfx 读数念给用户拍板，不许静默回改。
- **R4 内容循环辅助分析**：新 `scripts/insights.py`——用户主动授权导入发布后台 CSV（表头中英兼容），产出 `insights/report.md`（人话版「下一条改什么」）+ `metrics.json`。四条信号：完播分差、互动构成、类型配比、涨粉效率；**<5 条样本如实说不够，不硬编建议**；全报告只给相对信号、不承诺播放量（守住评审 §10.2 红线）。

## 对抗审查①（产品方向）结论与收尾（2026-11-15 00:5x，commit 本页所在）

审查①裁定「没有 P0，方向总体没跑偏但有两次实质性缩水」。逐条处置：

**已修**：
- R2 缩水：报告 §11 指定三类指令（前3秒不抓人 / 中间说明太慢 / 结尾换我自己的话）已全部支持，另有文案正则字符类 bug 与节奏方向反转两个真 bug 修复；
- G-06 残留：H03「三万/三千」钩子重写 + 守卫加金额模式扫描（负样本进测试）；
- G-03 只测负样本：补正样本实测拒网/拦写盘；profile 非 default-deny 的边界如实写进 G-03 行；
- 签收不绑时间线（§12.2 反证⑤）：scene_sig 加 tl_sha1（dur/marks），r1b 活证据实证；
- R1/R2 验收证据不进仓：新 `references/r1-r2-eval-2026-11-15.md`；
- tracker 记录与代码不同步：G-09/G-10 状态更新；
- insights.py：返回类型标注、watch 列三种口径归一化。

**如实标注为未完成（不冒充）**：
- R3 里程碑缩水一半：本轮做的是**同一台 Mac 上全新 CUTSKILLS_HOME 的移植模拟**；报告要求的「至少 2 台独立配置电脑」「真实用户试用」「独立审美盲评」**未达成**——真实用户与盲评需要 Jerry 或真实观众参与，等真人。
- R1 严格口径的「真实自然语言交互启动」与「观众理解证据」未达成（三条样片的拍板均为假设输入，approval.json 留痕）。
- G-01/G-06 缺「先造失败样例」的负样本：G-06 已补金额负样本；G-01 的旧代码失败样例无法在修复后补造，如实记录不再冒充。

**顺序偏差承认**：R4 在 R1 盲评证据为零时提前做了。insights.py 本身克制（相对信号、小样本拒判），但 R4 后续扩展冻结，优先补 R1 的真人证据。
