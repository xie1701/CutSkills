# index.json 字段与 L2 记录

`<素材库>/_cutskills/index.json` 由 `scripts/index_assets.py` 生成。**人不要手改**——改了下次指纹一致时会保留你的改动，但下次文件变动就被覆盖，改在 L2 记录里才持久。

## 顶层

```json
{
  "version": 2,
  "library": "/abs/path",
  "built_at": "2026-11-06T17:40:00",
  "counts": {"assets": 153, "video": 103, "photo": 47, "with_speech": 62,
             "duration_min": 153.1, "bytes": 13624067727,
             "portrait": 111, "offline": 0, "l2_records": 73,
             "l2_minutes": 30.2, "l2_coverage": 0.197, "l2_assets": 62},
  "assets": [ ... ]
}
```

`offline` 是「路径不在本机」的素材数（进了冷存储或需找回），见 [library-hygiene.md](library-hygiene.md)。

**`l2_coverage` 是预算指标，不是质量指标。** 它 = 跑过 L2 的段总时长 / 全库时长，因为每一段 L2 都要一次视觉调用。**上限 15%，是软上限**：超了不拦，但必须留一句原因——在跑索引时加 `--l2-budget-note "为什么这次多花钱"`，它会写进 `_cutskills/l2_budget.json` 并带入 `counts.l2_budget_note`，下一轮不用重说一遍；人读索引开头也会把它印出来。柒造这一库实测 19.7%（30.2 / 153.1 分钟），原因是把 62 个素材的内容词补进文件名。

## asset

| 字段 | 说明 |
|---|---|
| `uid` | **内容指纹**（大小 + 首尾各 4MB），形如 `a3f9c2e1b704`。改名、搬家都不变；重扫时靠它认人 |
| `id` / `path` | 相对素材库根的 posix 路径，全局唯一，所有 EDL 用它引用 |
| `aliases` | 曾用路径。合并 L2 记录时旧路径也能对上 |
| `original_name` | 第一次改名前的主干（`IMG_1042`）。改名时的「原编号」那格永远取它，不取当前名字，否则第二次跑会把名字越截越短 |
| `legacy_key` / `legacy_path` | 第一次建索引时的路径哈希，用来命名缓存（缩略图/转写/切片）。**不能变**，变了全部转写要重跑 |
| `kind` | `video` / `photo` / `audio` / `doc` / `other` |
| `batch` | 一级目录名，就是"这批是什么"的弱标签 |
| `duration` | 秒 |
| `spec` | **规格层**，见下。比平铺的 width/height 好用在：它把「横竖」「HDR」「旋转」这些会改变处理方式的属性算好了 |
| `orientation` | `portrait` / `landscape` / `square`，**按实际显示方向**（含 rotation 修正） |
| `handling` | 处理要求码列表，如 `["dolby_vision","hdr_hlg","ten_bit","rotated"]`，码表在 `scripts/media_spec.py` 的 `HANDLING` |
| `shoot_date` | 拍摄日期：视频优先本地时间（`com.apple.quicktime.creationdate`），照片读 EXIF 的 `creation`。拿不到就是 `null`，**不要用文件 mtime 顶替**——那是拷进这台机器的时间 |
| `capture` | **拍摄信息**：设备、软件版本、GPS、曝光参数、照片色域。见下 |
| `width` `height` `fps` `vcodec` | 保留的平铺字段，兼容旧脚本 |
| `audio` | `{codec, channels, sample_rate, peak_db}`；`peak_db > -0.1` 说明原音可能已在削波 |
| `quality` | 见下 |
| `silence` | `[[start, end], …]`，-35dB / ≥0.4s 的静音段 |
| `transcript` | `{engine, lang, phrases: [{start, end, text}], word_count, chunked, speech_ratio, lexicon_fixes}`；无语音为 `null` |
| `text_head` | 转写开头的头 80 字，给 `search_library` 和人在 CSV 里扫一眼用；不打开 JSON 也能大概看出这支讲了什么 |
| `bytes` | 字节数（算体积和存储估算用；和 `uid` 里的 size 同源，不要拿它判断变没变） |
| `segments` | 分级取用段；L2 合并后会覆盖 L0 的猜测 |
| `l2_segments` | L2 写完的那些段的 `{in, out}` 区间清单；和 `segments` 分开存，是为了下次重扫 L0 时不把 agent 写的段覆盖掉 |
| `renamed_at` | 最近一次被 `rename_assets.py` 改名的时戳。没改过就是 `null`；有它说明 `original_name` 才是原始编号 |
| `thumbs` | 抽帧路径（2s 一帧，宽 320） |
| `fingerprint` | `size + mtime` 的哈希，只用来判「文件被动过没有」。**不含文件名**——改名不该算内容变动 |
| `uid` | 身份（见上）：决定「是不是同一个素材」，也决定要不要丢缓存。两个判据分工要分清：改名的、拷贝的、被替换的，三种情况分别看 uid 和 fingerprint |
| `notes` | 异常说明（转写失败、解码失败等） |

## spec

```json
"spec": {
  "container": "mov", "vcodec": "hevc", "profile": "Main 10",
  "pix_fmt": "yuv420p10le", "bit_depth": 10,
  "width": 3840, "height": 2160,
  "display_width": 2160, "display_height": 3840,
  "rotation": -90, "orientation": "portrait",
  "fps": 59.94, "avg_fps": 59.94,
  "color": {"primaries": "bt2020", "transfer": "arib-std-b67", "space": "bt2020nc", "range": "tv"},
  "hdr": "dolby_vision", "hdr_base": "hlg",
  "dolby_vision": {"profile": 8, "level": 10, "base": "hlg", "bl_present": true},
  "audio": {"codec": "aac", "channels": 2, "sample_rate": 48000}
}
```

两条判断都不能靠直觉，实测踩过：

1. **横竖屏看 `display_*`，不看 `width/height`。** 柒造库 103 段视频里 101 段带 ±90° rotation，像素存成 1920×1080 但显示是 1080×1920。按宽高判会把 111 个竖屏素材全判成横屏。
2. **杜比视界不是 `color_transfer` 的某个值。** 它是在 HLG 或 PQ 基础层上再加一层动态元数据（`DOVI configuration record`）。只看 `color_transfer` 会把 Profile 8.4（基底 HLG）误认成普通 HDR，处理方式完全不对。

## capture（拍摄信息）

```json
"capture": {
  "device": "Apple iPhone 15 Pro Max",
  "software": "18.0",
  "created_local": "2024-07-29T09:27:47+0800",
  "gps": {"lat": 22.3681, "lon": 113.6202, "alt_m": 42.12, "accuracy_m": 97.2},
  "exposure": {"time_s": 0.01, "f": 1.78, "focal_mm": 6.765, "iso": 64},
  "lens": {"which": "主摄", "focal_mm": 6.765, "f": 1.78},
  "color_profile": "Display P3"
}
```

`created_local` 拿不到时（苹果没写本地时间）会退到 `created_container`——**单独一个字段名**，
因为容器时间经常是错的（柒造的地面素材文件名写 20261017，容器写 2024-07-29）。两格分开存，
后面才分得清哪个能信。

来源分两路，都是**读文件里的元数据，不猜**：

| 素材 | 从哪读 | 能拿到什么 |
|---|---|---|
| 视频 | QuickTime 容器标签 | `com.apple.quicktime.make/model/software`、`creationdate`（当地时间）、`location.ISO6709`（经纬度+海拔）、`location.accuracy.horizontal` |
| 照片 | `mdls`（Spotlight 读 EXIF）+ `sips` | 设备、GPS、快门/光圈/焦距/ISO、色彩配置（P3 还是 sRGB）、本地拍摄时间 |

为什么要这些：

- **设备是分组的天然维度。** 两台手机拍的素材，白平衡和动态范围本来就不一样，混剪时同一段色调统一不了是正常的，不是剪辑错。
- **位置是「去年那段在哪拍的」的入口。** ISO6709 那串 `+22.3681+113.6202+042.120/` 看着像乱码，是唯一的答案来源。
- **色域决定照片能不能直接用。** `Display P3` 的照片直接当 sRGB 发出去会变灰（处理码 `display_p3`）。
- **镜头决定「能不能接在一起」。** 主摄 6.765mm f/1.78、超广角 2.22mm、长焦 15.66mm f/2.8——三颗镜头的视角、景深、色彩都不一样。同一段话一半用主摄一半用长焦，剪出来看着「跳」，不是剪辑的问题。iPhone **不写镜头型号**（`kMDItemLensModel` 是空的），但写了焦距和光圈，按焦距分段就够判断：`<3.5mm` 超广角、`3.5–9.5mm` 主摄、`>9.5mm` 长焦。柒造这一库 46 张照片里有 45 张主摄、1 张长焦（`IMG_0901`，15.66mm）。
- **视频没有镜头和曝光。** 这一条是实测结论，不是漏读：iPhone 的视频容器里只有 make / model / software / creationdate / location 五类标签，没有焦距、光圈、快门、ISO。所以视频的 `lens` 和 `exposure` 是空的，别指望补。
- **曝光参数能判素材上限。** 快门 1/100、ISO 64 的片子有大量宽容度可调；1/30、ISO 3200 的片子已经到极限，后期硬提亮只会出噪点。

GPS 是精确坐标。`素材索引.md` 会按 0.01°（约 1 公里）聚类成「几个场地」，但原始精确值也在 CSV 里——**对外发素材库时要把这两列去掉**。

## handling 码表

渲染/合成阶段逐条执行，不靠人记。常见几个：

| 码 | 处理要求 |
|---|---|
| `hdr_hlg` / `hdr_pq` | 先 tone map 到 SDR BT.709，否则与 SDR 段拼接会突然变灰/变亮 |
| `dolby_vision` | 同上，且普通工具只认基础层，剪完颜色会漂；建议先转 SDR 代理 |
| `wide_gamut` | BT.2020 → BT.709，品牌色要重新取值 |
| `ten_bit` | 代理与最终编码都要确认 pix_fmt，防降成 8bit 起色带 |
| `rotated` | 显式 transpose，别依赖元数据（`-c copy`、部分滤镜链会丢） |
| `vfr` / `non_integer_fps` | 转 CFR；或按帧对齐，不按秒 |
| `hevc` | 交付前确认播放端；代理转码更慢 |
| `no_audio` / `mono_audio` / `multichannel` | 决定能否当口播、混音怎么下混 |
| `huge` | 单文件 >1GB，优先进冷存储 |

## quality

```json
"quality": {
  "blur":   {"mean": 5.6, "p10": 5.23, "p90": 5.98, "n": 97},
  "luma":   {"mean": 154.9, "p10": 147.2, "p90": 161.3, "n": 97},
  "motion": {"mean": 20.2, "p10": 9.9, "p90": 31.9, "n": 97},
  "black_total": 0, "freeze_total": 0,
  "samples": 97, "sample_fps": 4
}
```

- `blur` 来自 `blurdetect` 的 `lavfi.blur`，**越大越糊**，而且是原始分不是 0–1 归一化。实测标定（1080p→320 缩放后）：清晰 4K 素材 5–7；`gblur sigma=6` 约 33。阈值：`> 20` 判 do-not-use，`> 10` 判"略糊"。
- `luma` 是 `signalstats.YAVG`（0–255）。`< 28` 判欠曝，`> 232` 判过曝。
- `motion` 是 `signalstats.YDIF` 的均值，当运动量代理：太低说明画面死了，太高说明可能甩镜。
- 指标时间分辨率 = `1/sample_fps` = 0.25s。**够用**：剪辑取用单位是镜头段，不是帧。

## L0 分级（`segments[]` 里的 grade）

L0 只回答"能不能看"。三档：`must-use`（有语音且清晰）/ `backup`（清晰待定）/ `do-not-use`（黑帧、静帧、糊、曝光异常）。

`confidence` 在 L0 阶段最高只有 0.4 —— 这是故意的，别把它当结论用。

## L2 记录（agent 写，脚本合并）

**唯一手写的地方。** 一条记录写成一个文件：`_cutskills/l2/<asset-hash>-<int(in)>.json`（hash 和路径由 `l2_queue.json` 的 `write_to` 给出，别自己算）。

```json
{
  "asset": "熊工工地素材/Flow_VID_xxx.MOV",
  "in": 12.4,
  "out": 27.9,
  "shot": "medium",
  "people": ["speaker"],
  "action": "边走边说，指店内招牌",
  "text_on_screen": false,
  "tag": "店门口外景",
  "summary": "店门口介绍位置与营业时间",
  "grade": "must-use",
  "why": "唯一同时拍到招牌和人脸的一条",
  "confidence": 0.82,
  "flags": ["轻微抖动"]
}
```

字段规矩：

- `asset` / `in` / `out` 必须与队列里那条一致，否则合并时对不上。
- `shot`：`wide` / `medium` / `close` / `detail` / `pov` / `screen`（拍屏幕）/ `unknown`。
- `people`：能认出来的角色，用索引里一致的叫法（`speaker`、`host`、`worker`、`customer`、`none`）。**这一项是后面"从库里取本人照片做封面"的依据**，别乱填。
- `action` 写"在做什么"，不写"很有感觉"。
- `text_on_screen`：画面里有没有可读文字（招牌、价签、屏幕 UI）。有字要标，剪辑时要避开压在字幕区。
- `tag`：2–6 字的**名词短语**，是这段画面的名字，会被直接写进素材文件名（`20261002_茶桌访谈_必用_兰姐口播1.mov` 里的「茶桌访谈」）。写的是"画面里是什么"，不是判断（「自流平倒料」「色卡墙」「材料包装」）。不写这一格，改名只能退回截断 `summary`，出来就是「低机位中全景拍室」这种断在半截上的片段。
- `summary` 一句话说这段拍的是什么，供以后检索命中。
- `grade` / `why` / `confidence`：理由要能让人据此判断能不能用，不是形容词。
- `flags`：`抖动` / `过曝` / `失焦` / `遮挡` / `有人入镜` / `口型不同步` / `生成物` 之类。

**抽帧密度**：队列每条给 3 帧，取在这一段自己时长的 25% / 55% / 85% 处（按整支视频取会让同一素材的每段拿到同样三帧）。看 3 帧够判"是什么、能不能用"；判不了就在 `flags` 里写 `需逐帧看`，不要硬猜。

## 阈值标定（柒造 103 段素材实测）

| 指标 | 中位 | p90 | 最大 | 阈值 | 触发次数 |
|---|---|---|---|---|---|
| `blur` | 5.1 | 7.2 | 8.3 | >20 判弃用 / >10 判略糊 | 0 / 0 |
| `luma` | 133 | 166 | 166 | <28 欠曝 / >232 过曝 | 0 / 0 |
| `motion` | 18.7 | — | — | 只作参考 | — |

结论：**手艺正常的素材库，L0 几乎不会淘汰任何东西。** 这不是脚本失灵，是 L0 的定位——它只负责把黑帧、静帧、严重失焦挡在门外。判断“这段能不能用”必须靠 L2。看到 L0 全 backup，不要以为索引没用。

## 已知边界

- `coli asr` 的 sensevoice 模型对专有名词容易听错（"柒造"可能变成"七造"）。**成片字幕要人工过一遍专名**，或者把品牌词表喂进去。
- 不做内嵌字幕检测（2026-11-07 砍）：画面里有没有字看 L2 的 `text_on_screen`；字在哪、能不能避开是 D 段构图的事。代价：素材本身烧了字幕的，竖屏重排时去不掉。
- 长音频会被切成 240s 分片（`chunked: true`），偏移会拼回去；但跨片边界的一句话可能被切成两条短语，选段时避开跨界处。
- 单声道 16k 提取后转写，现场环境噪声大时会丢句；丢句的地方 `phrases` 会出现空档，选段时避开。
- 目前不识别说话人，多人对话的 `people` 只能靠 L2 的眼睛。
