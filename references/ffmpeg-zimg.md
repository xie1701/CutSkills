# 自编带 zscale 的 ffmpeg（2026-11-09）

系统 brew 装的 ffmpeg 是个精简配置，**没有 libzimg**，所以没有 `zscale` / `tonemap`
这两个滤镜。后果有两个，都很难看：

1. 素材里的 HLG / 杜比视界（bt2020）没法正确转 SDR。`hyperframes measure extract`
   直接压成 bt709，高光被削平、彩度掉一半：实测同一段素材 luma **0.52 / 饱和 0.166**，
   而参考片是 **0.381 / 0.511**。在成片上再拉饱和，到 0.35 就顶住了（像素已经没了）。
2. HyperFrames 判定 `hasHdrContent` 后会走 `capture_hdr_layered`，那条路要 zscale，
   没有就把**整片文字层丢掉**（见 STATUS.md 三条硬规矩第 1 条）。以前的绕法是加
   `--sdr`，现在切片阶段就把片子转成 SDR，这条分支根本不会被触发。

## 装在哪

`~/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg`（跟其它依赖一样落在 `~/.cutskills`，
不覆盖系统 `/opt/homebrew/bin/ffmpeg`）。`scripts/runtime.sh` 会把它排到 PATH 前面，
并导出 `CUTSKILLS_FFMPEG`；`render_hf.ffmpeg_bin()` 也认这个变量。

## 复现步骤

```sh
brew install zimg libvorbis          # 其余依赖（x264/x265/libvpx/lame/libass/freetype…）brew 里已有
curl -sL -o ffmpeg-9.0.2.tar.xz https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
tar xf ffmpeg-9.0.2.tar.xz && cd ffmpeg-9.0.2

export PKG_CONFIG_PATH=/opt/homebrew/lib/pkgconfig
./configure --prefix=$HOME/.cutskills/opt/ffmpeg-zimg \
  --extra-cflags="-I/opt/homebrew/include" --extra-ldflags="-L/opt/homebrew/lib" \
  --enable-gpl --enable-version3 --enable-libzimg --enable-libass --enable-libfreetype \
  --enable-libharfbuzz --enable-libfontconfig --enable-libx264 --enable-libx265 \
  --enable-libvpx --enable-libmp3lame --enable-libopus --enable-libvorbis --enable-libwebp \
  --enable-libdav1d --enable-libsvtav1 --enable-libvmaf \
  --enable-videotoolbox --enable-audiotoolbox --disable-doc
make -j$(sysctl -n hw.ncpu) && make install
```

两个坑：

- **`lame/lame.h` 找不到**：`lame.pc` 里的 `includedir` 已经是 `.../include/lame`，
  ffmpeg 却按 `#include <lame/lame.h>` 找，所以要补 `--extra-cflags=-I/opt/homebrew/include`。
- **`vorbis not found`**：brew 的 `ffmpeg` 不带 libvorbis 依赖，单独 `brew install libvorbis`。

## 有没有装上

```sh
$HOME/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg -hide_banner -filters | grep -E " zscale | tonemap "
$HOME/.cutskills/opt/ffmpeg-zimg/bin/ffmpeg -version | head -1
```

## 色调映射链

```
zscale=t=linear:npl=100,format=gbrpf32le,
zscale=p=bt709,tonemap=hable:desat=0,
zscale=t=bt709:m=bt709:r=tv,format=yuv420p
```

`desat=0` 是关键：`tonemap` 默认 `desat=2`，会把高光压成白色——那正是"看着发灰、
颜色没了"的原因。

这套链只在**源是 HDR**（`color_transfer` 为 `arib-std-b67` / `smpte2084`）时才上，
SDR 素材只做 `format=yuv420p` + 偶数尺寸 + `fps=30`。
`render_hf.py --extractor {auto,own,hf}` 控制：`auto` 有 zscale 就自己切。
