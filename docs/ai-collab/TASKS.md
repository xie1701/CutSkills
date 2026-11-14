# TASKS · CutSkills V2 任务看板

> V1 阶段历史任务（T-001 ～ T-061）已完成使命并封存于 Git 标签 `archive/v1-before-v2-refactor`。
> V2 重构后新任务从 `T-101` 起顺序编号，只增不复用。

| 任务号 | 目标与验收口径 | 状态 | 工位 | 分支 / 提交 | 改动与门禁读数 |
|---|---|---|---|---|---|
| T-100 | **CutSkills V2 整体重构**：砍除 V1 冗余管线/看板/盲测/旧报告（68 项 764MB），落地双入口×三次拍板生产型 Skill（`ideate.py` 上游 5A 选题与十大钩子、`voiceover.py` AI 配音字级入库、`beat_sync.py` 鼓点卡点、`stickers.py` 16 款矢量花字贴纸、`P5-viral-pop` 网感包装包、内置 CC0 音乐音效迷你包、T-059 同窗净抬升音效门禁、跨机零私有路径分发包） | **已完成** | Cola | `master` | `audit_skill` 🔴0 🟡0 🟢8；`selftest` 全绿（含真渲染 + 31 支单测）；`pack_dist` 全仓零私有路径泄漏通过 |
