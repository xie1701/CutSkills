# Audio-First：先声夺人（声音与鼓点驱动画面节奏）

短视频剪辑的第一铁律：**画面事件不按拍脑袋的秒数摆，而是跟着人声气口、重音峰值与音乐鼓点走。**

## 一、人声切块与重音锚定（`scripts/audio_map.py`）

| 步骤 | 做法 | 作用 |
|---|---|---|
| 单一词级真值 | 统一调用 `evidence_guard.chars_of_asset`（兼容 AI 配音词表、新键与 `legacy_key`） | 拿到每个字的精确毫秒起止 |
| 半句切块 | 字间停顿 `≥ 240ms`、满 9 字或遇标点切块 | 还原真人「一口气」的说话节奏单元 |
| 重音寻峰 | 解码 16kHz 单声道，20ms 帧计算 RMS 能量峰值 | 大字花字、动效子件跟着重音峰值亮出 |
| 废片标记 | 自动标出填充词（嗯/啊/那个）与重说口误 | 配合 `speech_hygiene.py` 与 `cue_guard.py` 自动剔除 |

## 二、AI 配音字级对齐（`scripts/voiceover.py`）

当用户不方便露脸或只想提供文案时，`scripts/voiceover.py` 按分镜逐句合成自然人声（支持云端 TTS、`coli tts`、macOS 系统音色及外部配音音频导入），自动切除首尾死静音、留 `0.16s` 自然换气口，并将字级时间戳直接注册进 `_cutskills/index.json` 的 `transcript.words`——让无实拍片享受到与真人口播完全一致的逐字字幕与锚词卡点精度。

## 三、BGM 鼓点检测与切点吸附（`scripts/beat_sync.py`）

`scripts/beat_sync.py` 对所选 CC0 配乐计算 10ms 级瞬态能量正差分（Onset Flux），提取鼓点与重拍并估算 BPM：
- **真人说话段锁死声画同源**：`medium: "talking"` 绝不拉伸画面，确保嘴形分毫不差；
- **弹性画面段踩点吸附**：代码生成画面（`gen`）、照片（`image`）与纯画面镜头在 `±0.18s` 范围内自动微调时长，让画面切换干脆踩在音乐鼓点上。

## 四、声音在前与同屏单焦点守卫

1. **卡片只许晚、不许抢拍**（`scripts/check_timing.py`）：每张大字或 MG 动效卡绑定台词锚词 `card.on`，入场时间 = 锚词发声起点 `+ 0.15s`。声音先入耳、画面紧跟砸下，观众眼耳合一。
2. **重卡在场时字幕自动退让**（`render_hf.py` + `scripts/check_overlay.py`）：重型信息卡（如比例条、对比卡、步骤卡）展示期间，重叠的底部字幕自动挂起并记入 `out/subs.json` 的 `suppressed`，保证同屏只有一个视觉焦点。
3. **同步率检验**（`scripts/sync_check.py`）：实测视觉切点、卡片入场与语音边界的同步率与偏移量。
