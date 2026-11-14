# CutSkills · 互动式短视频创作与百万剪辑师级出片系统

**一句话定位**：一套跨电脑可复用、代码驱动、面向非专业用户的短视频全链路生产 Skill。
你说一句模糊想法，或者丢一堆杂乱素材，剩下的——选题、脚本、AI 配音、剪辑、调色、字幕、花字贴纸、音效卡点、响度归一、验收清理——全部由 AI 编导 + 代码自动完成，全流程只打扰你 **3 次**。

> 典型用户：**不懂拍摄、不懂剪辑、不懂包装**的创作者。本 Skill 在对话中扮演「金牌短视频编导 + 百万粉丝剪辑包装师」。

---

## ✨ 核心特性

### 双入口接入 × 三次拍板

| 入口 | 你怎么开口 | 系统做什么 |
|---|---|---|
| **A · 从零创作** | 「我是开茶楼的，帮我想几个能火的选题」 | 双流向选题引擎（漏斗流破圈 / 水桶流人设 / 水桶流转化）一次给 3 套提案 + 黄金 3 秒钩子 + 《小白 10 分钟拍片卡》；可选**零拍摄**路线：AI 配音 + 代码动画直接出片 |
| **B · 带素材接入** | 「素材在这个文件夹，剪一条 45 秒的」 | 多模态索引（ASR 词级时间戳、废片评级、场记口令清洗）→ 挑好句剪废片 → 网感包装成片 |

三次拍板节奏：**① 定选题脚本 → ② 审一页纸剪辑包装方案卡 → ③ 看成片微调**。其余脏活静默自动完成。

### 百万剪辑师网感八大武器

1. **黄金前 3 秒强钩子**——十大钩子模型 + 开场冲击推镜 + 顶部磨砂悬念标题条，严禁「大家好我是…」开场
2. **跳切推近零废片**——自动去场记口令、语气词、重说；长口播无痕跳切，严守音画同源
3. **高颜值无标点字幕**——校准字宽的得意黑/思源黑体，单排硬切，按真实静音拆呼吸组
4. **锚词花字 + 7 类修辞同构动效卡**——数字滚动 / 比例条 / 时段接力 / 对抗 / 步骤 / 释义 / 名牌，重点词放大撑 70% 屏宽
5. **16 款矢量动态情绪贴纸**——多层受光 SVG 徽章 + 弹簧动画，自动落四角安全区不遮脸
6. **先声夺人鼓点卡点**——画面跟声音走，弹性镜头自动吸附 BGM 鼓点，音效按音色频段路由精准踩点
7. **5 套正交视觉包装包**——`P1-warm` 暖实拍 / `P2-paper` 纸感双语 / `P3-dark` 暗调极简 / `P4-canton-warm` 餐饮烟火 / `P5-viral-pop` 百万博主高赞网感
8. **广播级响度 + 15 道像素门禁**——人声侧链压 BGM，整片 `-14 LUFS ±1`（响度单位，广播级网络视频标准），渲前渲后双闸门

### 跨机器零配置开箱

- **内置轻量 CC0 音频包**（5 首 BGM + 24 条网感音效，4.7 MB）：不配外置库也直接出片，全部免版权
- **依赖统一落 `$CUTSKILLS_HOME`**（默认 `~/.cutskills`）：`bash scripts/runtime.sh install` 一条命令装齐，绝不散装污染全局
- **`doctor.py` 一键体检**：缺什么、去哪补，一次说完

---

## 🚀 快速开始

```bash
# 0. 系统前提（macOS）：python3 / node / ffmpeg
brew install python node ffmpeg zimg

# 1. 安装运行时依赖（集中存放）
bash scripts/runtime.sh install

# 2. 体检（可选配置 config.json，不配也能用内置 CC0 音频直接出片）
python3 scripts/doctor.py

# 3. 把本仓库挂载给你的 AI 助手（软链到 skills 目录），用大白话对话即可
ln -s "$(pwd)" ~/.claude/skills/cutskills   # 或 ~/.cola/skills/cutskills
```

### 从一个想法直接出片（零拍摄路线）

```bash
source scripts/runtime.sh env
P=_lib/_cutskills/projects/my-film

python3 scripts/ideate.py --project $P --topic "珠海茶楼" --guide        # ① 3 套选题+钩子+拍片卡
python3 scripts/ideate.py --project $P --pick 1 --hook 2                #    选定后写定脚本
python3 scripts/voiceover.py --project $P --library _lib --from-ideation #    AI 配音+字级时间戳
python3 scripts/ideate.py --project $P --to-plan --vo-asset "_vo/my-film.wav"
python3 scripts/timeline.py --project $P --library _lib --fill --fit-skeleton
python3 scripts/beat_sync.py --project $P --snap                        #    吸附 BGM 鼓点
python3 scripts/plan_card.py --project $P --show                        # ② 方案卡拍板
python3 scripts/plan_card.py --project $P --approve "认可"
python3 scripts/render_hf.py --project $P --library _lib --quality looks --bgm auto  # ③ 真渲
python3 scripts/verify_film.py --project $P && python3 scripts/check_sfx.py --project $P
python3 scripts/tidy.py --project $P --apply                            #    渲→验→清
```

> **把项目搬到另一台机器**：只拷项目目录不够——素材库侧的 `_vo/` 配音资产和
> `_cutskills/index.json` 里的资产条目必须一起过去，否则重渲后词级真值丢失、
> 字幕 0 条（还不报错）。整库拷是最稳的移机方式。

### 从一堆现成素材出片

```bash
source scripts/runtime.sh env
python3 scripts/index_assets.py --library "<素材库>"                    # 多模态索引
python3 scripts/init_project.py --library "<素材库>" --slug my-film --intent "你的原话需求"
python3 scripts/plan_card.py --project "<素材库>/_cutskills/projects/my-film" --show
python3 scripts/plan_card.py --project "<素材库>/_cutskills/projects/my-film" --approve "认可"
python3 scripts/render_hf.py --project "<素材库>/_cutskills/projects/my-film"
python3 scripts/verify_film.py --project "<素材库>/_cutskills/projects/my-film"
python3 scripts/tidy.py --project "<素材库>/_cutskills/projects/my-film" --apply     # 渲→验→清
```

> 完整命令速查（108 个脚本 / 312 个参数）见 [references/cli.md](references/cli.md)，
> 面向 AI 工位的完整说明书见 [SKILL.md](SKILL.md)，人类上手一页纸见 [GETTING-STARTED.md](GETTING-STARTED.md)。

---

## 🏗️ 流水线架构

```
选题创作（ideate.py · 5A 人群双流向 + 十大钩子）
        ↓
脚本定稿 → AI 配音（voiceover.py · 字级时间戳入库）／或实拍素材索引（index_assets.py · ASR 词级真值）
        ↓
编导时间轴（timeline.py v5 · 挑 take / 补代码画面） + 鼓点卡点（beat_sync.py · 严守声画同源）
        ↓
方案卡拍板（plan_card.py · 一页纸分镜表 + lock.json 签锁）
        ↓
HyperFrames 真渲（render_hf.py · 调色/字幕/花字/贴纸/动效卡/音效混音）
        ↓
像素门禁验收（verify_film.py 15 项 + check_sfx.py 同窗净抬升 + gate.py 总编排）
        ↓
交付与清理（showcase.py 产出总表 / studio.py 工程页 / tidy.py 渲→验→清）
```

**判词口径（Measure, Don't Narrate）**：几何靠像素量、动静靠帧差剖面、可读性靠 ΔL，视觉模型只判「读不读得清、有没有穿帮」，绝不目测几何。

---

## 📁 仓库结构

| 目录 | 内容 |
|---|---|
| `scripts/` | 108 个产线脚本（索引 / 编导 / 渲染 / 验收 / 清理 / 自检） |
| `packs/` | 5 套正交视觉包装包（`P1`–`P5`） |
| `plans/` | 4 套剪辑骨架节奏方案（快 / 中 / 慢 / 倒叙） |
| `styles/` | 字体、调色、包装包预览样张 |
| `assets/audio/` | 内置 CC0 音乐与音效迷你包（免版权，可商用） |
| `references/` | 全部规范文档（选题系统 / 字幕规则 / 时间轴 Schema / 15 道判据原则库…） |

## ✅ 质量保障

```bash
python3 scripts/gen_cli_ref.py   # 重新生成 CLI 速查（禁止手改）
python3 scripts/audit_skill.py   # 文档 × CLI × Schema 三向对账，必须 🔴0 🟡0
python3 scripts/selftest.py      # 端到端真渲染自检 + 33 支契约与真音频回归单测
python3 scripts/pack_dist.py     # 分发包完整性 + 全仓私有路径零泄漏扫描
```

所有判据阈值都有出处与标定语料（`references/rhythm-bands.md`），每条判据配负样本回归；改判据必须先看标定、不许为绿灯放宽断言（详见 [editorial-packaging-principles.md](references/editorial-packaging-principles.md) 的 A1–B15 原则库与 [AGENTS.md](AGENTS.md) 四条铁律）。

## 📄 许可

- 本仓库**代码与文档**：私有项目，未附开源许可前默认保留所有权利
- `assets/audio/` 内置音乐音效：**CC0 1.0 Universal**（公有领域贡献，可商用免署名）
