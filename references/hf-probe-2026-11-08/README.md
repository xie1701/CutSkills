# HyperFrames 当渲染层：实测记录 · 2026-11-08

Jerry 的问题：剪辑的核心是不是该用 HyperFrames，而不是 video-use？理由是「可以反复套用不同模板，实现不同效果」。

这一页只放**测出来的数**，判断在对话里。

## 环境

`hyperframes 0.9.23`（`npx`，本机 `/opt/homebrew/bin/hf` 是另一个工具 2.2.0，别混）。
`hyperframes doctor`：Node 26.7、Chrome headless 已就位、ffmpeg 9.0.2、whisper-cpp 有。
TTS / BGM / Docker 未装（都是可选）。

## 测法

取 `references/packtest-2026-11-08/proj79/edl.json` 的**前 3 段**（同一份 EDL、同一批素材、同样的字幕文案），
用 `gen_hf.py` 把它翻成一份 `index.html`，渲成 14.44 秒 mp4，和 ffmpeg 版前 14.44 秒逐帧比。

`gen_hf.py` 做的事：
1. `hyperframes measure extract` 把每段从原素材切出来（带声音）；
2. 从 `_cutskills/index.json` 的短语时间戳切字幕块（≤9 字），按字均分时间；
3. EDL 里的 `card` 翻成一条大字，从字幕里找到那个词的起点、提前 0.12s 出现；
4. 字幕用 `power3.out` 0.16s 滑入，大字用 `back.out(2.2)` 0.34s 弹入。

## 量出来的

| 项 | HyperFrames | 我们的 ffmpeg 管线 |
|---|---|---|
| 14.44 秒成片，`looks` 质量，1 worker | **1m23.8s** | — |
| 同上，`-w 4` | **1m16.7s** | — |
| 同上，`-q draft` | **32.5s** | — |
| 79 秒片（外推） | **≈7 分钟**（draft ≈3 分钟） | **40 秒** |
| 旋转过的 HEVC 竖屏切段 | 自动 1080×1920，不用管 rotation | 手写了约 300 行处理 |
| lint | 0 error 0 warning 可到 | — |
| 亮度 luma | 119.5 | 91.4 |
| 饱和 saturation | 0.334 | 0.587 |
| 暖度 warmth | 39.4 | 65.7 |

**亮度/饱和那三行不是引擎差异**：HyperFrames 那版我没把调色搬过去（只有一层暗角），
ffmpeg 版跑的是 `eq=saturation=1.50:contrast=1.10:gamma=0.85:brightness=-0.10`。
搬过去就是 CSS `filter`，一行。

## 一个必须记下来的教训

我拿视觉模型看对比图，它说「HyperFrames 肤色更自然、色彩更通透、编码伪影更少」。
去量像素：HyperFrames 更亮更灰，ffmpeg 更暗更饱和——差别只是**没加调色**。

和今天下午「视觉模型说字幕偏高偏大、实测差 3 像素」是同一个坑。
视觉模型对这类对比的判断，不能当证据用。

## 它自带的、我们手写过的东西

| 我们写的 | HyperFrames 自带 |
|---|---|
| `probe_packaging.py`（参考片探针） | `measure probe / cuts / events` |
| `ref_frames.py`（取帧联系表） | `measure frames`（overview sheet / strips） |
| `measure_pack.py` 的字幕带 | `measure ink`（文字墨迹的框、字号、行数） |
| `compare_films.py` 的亮度/饱和/暖度 | `measure look --compare` |
| `compare_films.py` 的运动量 | `measure energy`（两条片动不动、什么时候动） |
| 逐帧对照 JS/脚本 | `diff`（对参考片逐帧 SSIM，分段落给结论） |
| 推镜/缓动的拟合 | `measure curve`（每个动作的起点、时长、过冲、最接近的 GSAP ease） |
| `person_mask.swift`（Vision 抠像） | `remove-background` |
| 包装模板 yaml + `pack_style.py` | `templates pull <slug>`（HyperFrames Pro，要订阅） |

最后一行是这次唯一「要花钱且拿不到手」的东西。其余全是本地命令。
