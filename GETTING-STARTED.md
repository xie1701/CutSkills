# 上手一页纸 · 把 CutSkills 复制到任何电脑即刻出片

**一句话定位**：一套面向**不懂拍摄、不懂剪辑、不懂包装**创作者的 AI 互动式短视频创作与剪辑生产系统。
无论你**只有一个模糊想法**（从零出选题、写脚本、教你怎么拿手机拍，甚至直接 AI 配音 + 代码画画面），还是**已经拍好一堆杂乱素材**，都能通过跟 AI 对话做出百万剪辑师级网感成片。

---

## 第 0 步 · 系统前提（macOS）

| 依赖 | 用途 | 安装命令 |
|---|---|---|
| `python3 ≥ 3.10` | 编导、识别与验收脚本 | `brew install python` |
| `node ≥ 20` | HyperFrames 浏览器动效渲染 | `brew install node` |
| `ffmpeg ≥ 6` | 音视频切段、调色与响度混音 | `brew install ffmpeg zimg`（若有 iPhone HDR 素材，按 `references/ffmpeg-zimg.md` 编译带 zscale 版更佳） |

## 第 1 步 · 一键安装运行时依赖（集中存放，绝不污染全局）

```bash
bash scripts/runtime.sh install   # 自动安装 hyperframes / pillow / numpy 等至 ~/.cutskills
bash scripts/runtime.sh check     # 检查各组件状态
```

## 第 2 步 · 可选路径配置与体检

本仓库**已内置精选 CC0 免版权配乐与 24 条高频网感音效**（`assets/audio/`），零配置即可直接出片！若你有自己的大容量素材库或外置音乐库，复制一份配置：

```bash
cp config.example.json config.json   # 已加入 .gitignore，绝不泄漏私人路径
python3 scripts/doctor.py            # 体检全 ✅ 即可开工
```

## 第 3 步 · 两种打开方式（直接把仓库挂载给你的 AI 助手即可）

将本目录软链或复制到 AI 工具的 skills 目录（如 `~/.cola/skills/cutskills` 或 `~/.claude/skills/cutskills`），直接用大白话跟 AI 对话：

### 方式 A：从「一个想法」开始（上游选题 → 脚本 → 拍片指导或零拍摄直出）
> 你说：「我是做茶楼/装修/工作室的，帮我想几个能火的短视频选题，教我怎么拍，或者直接帮我做一条！」

后台对应流水线：
```bash
source scripts/runtime.sh env
# 0. `<素材库>` 没有现成的？随便指定一个新目录即可（如 `~/CutSkills_lib`），
#    voiceover 会自动建库并写入 index.json——零拍摄路线不需要任何素材
# 1. 出 3 套双流向选题 + 黄金3秒钩子 + 《小白 10 分钟拍片卡》(shoot-guide.md)
python3 scripts/ideate.py --project <项目目录> --topic "你的行业或想法" --guide
# 2. 若选择零拍摄直接出片：AI 配音自动合成并生成字级时间戳 → 转时间轴 → 卡点 → 渲染
python3 scripts/voiceover.py --project <项目目录> --library <素材库> --from-ideation
python3 scripts/ideate.py --project <项目目录> --to-plan --vo-asset "_vo/<项目名>.wav"
python3 scripts/timeline.py --project <项目目录> --library <素材库>
python3 scripts/beat_sync.py --project <项目目录> --snap
python3 scripts/plan_card.py --project <项目目录> --approve "认可"
python3 scripts/render_hf.py --project <项目目录> --library <素材库>
```

### 方式 B：从「一堆现成素材」开始（多模态索引 → 挑好句剪废片 → 网感包装成片）
> 你说：「我把拍好的视频放在 `/path/to/素材` 了，帮我剪一条 45 秒强网感的口播成片。」

后台对应流水线：
```bash
source scripts/runtime.sh env
python3 scripts/index_assets.py --library "<素材库>"
python3 scripts/init_project.py --library "<素材库>" --slug my-film --intent "你的原话需求"
# AI 编导挑 take、排分镜、生成方案卡等你点头，确认后渲染 + 验收 + 清理过程垃圾
python3 scripts/plan_card.py --project "<素材库>/_cutskills/projects/my-film" --approve "认可"
python3 scripts/render_hf.py --project "<素材库>/_cutskills/projects/my-film"
python3 scripts/verify_film.py --project "<素材库>/_cutskills/projects/my-film"
python3 scripts/tidy.py --project "<素材库>/_cutskills/projects/my-film" --apply
```

### 把项目搬到另一台机器（移机）
> 项目目录和素材库是一对：只拷项目过去，重渲不会报错、但字幕会变 0 条（词级真值丢了）。
> 必须把素材库侧的 `_vo/`（配音）和 `_cutskills/index.json` 里的资产条目一起带过去；
> **最稳的做法是整个素材库目录一起拷**（含 `_cutskills/` 与 `_vo/`）。
> 搬完先跑 `python3 scripts/doctor.py` 再继续改。


## 第 4 步 · 一键端到端自检与打包分享给朋友

```bash
python3 scripts/selftest.py     # 自动合成测试语音与画面、真渲染一条成片并跑完全部离线单测
python3 scripts/pack_dist.py    # 打出零私人路径的干净 zip 分发包
```
