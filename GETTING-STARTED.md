# 上手一页纸 · 复制这套仓库到你的机器

**一句话**：一个素材文件夹 + 一句话需求 → 索引一次 → 纸面剪辑 → 代码补缺口 → 渲染出带包装的成片。
你（人）只在两处出现：**给意图**、**确认方案**。其余由脚本和你的 AI 工位完成。

建议的用法：把这一页连同仓库一起交给你的 AI 编码助手，让它照着带你走完前四步。

---

## 第 0 步 · 系统前提（macOS）

| 依赖 | 用途 | 装法 |
|---|---|---|
| python3 ≥ 3.9 | 产线本体 | 自带或 `brew install python` |
| node ≥ 22 | 渲染层 hyperframes | `brew install node` |
| ffmpeg ≥ 6 | 抽帧 / 调色 / 混音 | `brew install ffmpeg zimg`（HDR 素材要自编带 zimg 的版本，见 `references/ffmpeg-zimg.md`；没有 HDR 素材可先用 brew 版） |

## 第 1 步 · 装脚本依赖（全落一个目录，不散装）

```bash
bash scripts/runtime.sh install   # hyperframes / pillow / numpy 等，装进 ~/.cutskills
bash scripts/runtime.sh check     # 真跑一遍 -version，看缺什么
```

## 第 2 步 · 填你自己的路径（唯一要改的配置）

```bash
cp config.example.json config.json   # 已 gitignore，不会进包
```

四个字段：`libs`（素材库根目录列表）、`bgm_lib` / `sfx_lib`（CC0 音乐/音效库，没有可先留空）、`out_dir`（产出总表目录）。

然后体检：

```bash
python3 scripts/doctor.py            # 全 ✅ 就开工；❌ 会告诉你去哪补
```

## 第 3 步 · 素材库索引一次，反复取用

把原始素材（随便什么文件夹结构）放进一个「素材库根目录」，然后：

```bash
python3 scripts/index_assets.py --library "<素材库根目录>"
```

它会逐个素材探规格、抽帧、词级转写、分级标注（哪些可用 / 不可用 / 缺什么），
全部写进 `<库>/_cutskills/index.json`。**素材文件夹本身不需要重命名**。

## 第 4 步 · 从一句话需求到成片

```bash
# ① 建项目：五问——给谁看 / 他现在信什么 / 要他怎么变 / 多长 / 什么风格
python3 scripts/init_project.py --library "<素材库>" --slug 我的片 \
    --intent "你的一句原话，别改写" [--duration 45] [--avoid "竞品品牌名"]

# ② 纸面剪辑 → 方案卡 → 你批准后才动第一帧（渲染前无批准会拒绝执行）
python3 scripts/render_hf.py --project "<库>/_cutskills/projects/我的片" --segments 3 --no-render

# ③ 批准方案后渲染
python3 scripts/render_hf.py --project "<库>/_cutskills/projects/我的片"

# ④ 验收（硬指标 + 逐格接触表 + 总门禁）
python3 scripts/verify_film.py --project "<库>/_cutskills/projects/我的片"
python3 scripts/gate.py         --project "<库>/_cutskills/projects/我的片"

# ⑤ 收工清理（渲 → 验 → 清；只删可重建物，成片与真值源全留）
python3 scripts/tidy.py --apply
```

## 还不用真素材？先跑通链路

```bash
python3 scripts/selftest.py
```

用**合成素材**从头到尾出一条片并跑完 30 项自测（约 1–2 分钟 + 首次装依赖）。
它证明的是「链路没断」，不是「片子好看」。

## 想给 AI 工位用？

把整个仓库（含本文件）放进你的 skill 目录（如 `~/.claude/skills/cutskills/`）。
`SKILL.md` 是工位读的完整说明书：四层流程、判据口径、渲前九道闸门、出错码表。

## 常见坑（都是真撞过的）

- **HDR / 杜比视界素材渲出来偏亮偏灰**：系统 ffmpeg 没有 zscale。自编带 zimg 的版本，`scripts/runtime.sh check` 会替你验证自编那份还能不能跑。
- **本地转写（coli）不在 PATH**：有 Cola.app 就 `python3 scripts/fetch_coli.py` 重抽；没有它渲染退到 whisper，慢且丢词级时间戳（字幕断句变差）。
- **渲染被拒绝（退出码 4–9）**：这是九道渲前闸门在工作——方案未批准(4) / 声音在前(5) / 卡准入(6) / 叠加态(7) / 大字压脸(8) / 锚词对不上(9)。看报错里的那一行，改对应的东西，**不要绕过**。
- **字幕/卡面/音效的阈值看着像拍脑袋**：它们全部有出处（参考片标定或行业换算），口径集中在各脚本头部注释；改阈值前先读那几行。
- **换机器后某天突然全坏**：先跑 `bash scripts/runtime.sh check`——多半是 brew 把 ffmpeg 的动态库换掉了。

## 目录速览

```
scripts/        产线本体（约 130 个脚本，每个头部注释写清为什么存在）
styles/ plans/ packs/   风格层：字幕/调色 token、节奏档位、包装子系统
references/     判据出处、事故复盘、美术标准（scene-art-standard.md 值得先读）
vendor/         第三方工程借鉴（production-board 看板）
assets/         gsap 等前端运行时
```

极简主义条款：**配置只有 config.json 一处；依赖只有 $CUTSKILLS_HOME 一处；
脚本里不许出现任何人的家目录。** 发现违反这三条的，当场修。
