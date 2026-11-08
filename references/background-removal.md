# 抠像：u2net 权重已就位，但主链路用的是 macOS Vision

2026-11-09

## 结论先说

- **`hyperframes remove-background` 现在能用了。** 之前卡在 `Download failed: HTTP 504`，
  是 GitHub Releases 那一下不通，不是链接写错。权重手动下到缓存目录就行（见下）。
- **但主链路不用它。** 对柒造这批素材，macOS Vision 的 `person_mask` 抠得更准，
  而且快得多（Vision 逐帧毫秒级，u2net 一帧 2.7 秒）。它留着备用：单张图、
  或者 Vision 哪天判不出来的时候。

## 权重怎么放

```
~/.cache/hyperframes/background-removal/models/u2net_human_seg.onnx     # 168 MB
```

来源是 `https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx`，
`render_hf.py` 那条记录里的 `MODEL_URLS` 就是这个地址。用 `curl -L -C -` 支持断点续传，
网络抽风重试几次就下来了。

```sh
mkdir -p ~/.cache/hyperframes/background-removal/models
curl -L --retry 5 -C - -o ~/.cache/hyperframes/background-removal/models/u2net_human_seg.onnx \
  https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx
npx --yes hyperframes@0.9.23 remove-background --info   # Weights cached: yes
```

本机有 **CoreML** provider（`Available providers: cpu, coreml`），会自动选中。

## 两个方案实测对比

同一帧（熊工口播，1080×1920），同一张洋红底合成：

| | 边缘 | 主体完整度 | 速度 |
|---|---|---|---|
| u2net_human_seg (CoreML) | 生硬、有锯齿，衣领处有背景色残留 | 差：桌面的绿植和深色盆沿被并进人像 | **2.7 s/帧** |
| macOS Vision `person_mask` | 平滑、头发边缘干净 | 好：人和植物分得开，植物半透明过渡 | 毫秒级 |

2.7 s/帧意味着一段 4 秒的镜头（120 帧）要 **5 分钟**，一条 79 秒的片子里
有五段要抠像 → 光抠像 40 分钟以上。Vision 那条几十秒就完了。

所以 `render_hf.make_fg()` 继续走 `scripts/native/person_mask`（Vision），
不用 `hyperframes remove-background`。这个选择不是因为装不上，是因为**装上了也不好用**。

## 用 Vision 时的三个坑（保留在这里，别再踩）

- Vision 不认旋转：先把素材标准化（同尺寸、无 rotation metadata）再做。
- 遮罩要**二值化**（`lut=y='if(gt(val,140),255,0)'`）再内缩 2px，否则边缘一圈半透明发亮。
- 遮罩编成灰度 h264 视频交给 ffmpeg 做 `alphamerge`；读带 alpha 的 VP9 时
  **必须显式 `-c:v libvpx-vp9`**，ffmpeg 自带的 vp9 解码器不解 alpha。
