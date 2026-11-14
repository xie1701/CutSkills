# HANDOFF · 工位交接单

## 当前主干基线（V2.0 · 2026-11-14）

- **架构版本**：CutSkills V2（双入口 × 三次拍板 · HyperFrames v5 唯一渲染主链）
- **历史回滚锚点**：`git tag archive/v1-before-v2-refactor`
- **环境启用**：`source scripts/runtime.sh env`（依赖统一装在 `$CUTSKILLS_HOME`，默认 `~/.cutskills`）
- **门禁基线（全绿）**：
  - `python3 scripts/doctor.py`：核心环境与内置 CC0 音频包全绿
  - `python3 scripts/audit_skill.py`：`OK · 🔴 0 · 🟡 0 · 🟢 8`
  - `python3 scripts/selftest.py`：`exit 0`（端到端真渲染 + 31 支离线与真音频回归单测全过）
  - `python3 scripts/pack_dist.py`：`exit 0`（全仓零私有家目录路径泄漏）

---

## 下一位工位开场提示词（可直接复制）

```text
你在 CutSkills 仓库接一个开发工位。开工前请依次阅读：
1. AGENTS.md（四条铁律与门禁命令）
2. SKILL.md（双入口 × 三次拍板生产流水线与网感包装武器库）
3. docs/ai-collab/PROTOCOL.md 与 docs/ai-collab/TASKS.md

先运行 `source scripts/runtime.sh env && python3 scripts/doctor.py && python3 scripts/audit_skill.py` 确认环境全绿，再按 START-STOP.md 领单开工。
```
