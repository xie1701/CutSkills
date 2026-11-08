# 雪藏区 · 包装模板（2026-11-08 停）

Jerry 的原话：「目前这套选包装模板这套东西先雪藏吧，我想其他办法。」

## 停的是什么

从「参考片逆向 → 5 族模板 → 逐支复刻 → 按秒对照」这一整条链。它跑了整整一天，
每一轮都有数字支撑（字幕带 0.6912 vs 0.6928、亮度 0.386 vs 0.381、底噪 -27.5 vs -27.8），
但连续四轮都得到同一句评价：**「还是差很多」「完全不可用」。**

停的理由不是"数字没对齐"，是**数字对齐了也没用**：
今天最后一项——字体——暴露了这套方法的结构性问题。
这些指标全部只量几何和信号（位置、字号、亮度、响度、密度），
**没有一项在量"好不好看"**。而"好看"恰恰是这套东西的全部目的。
四层共用一个标题美术字、花字压在人脸上、结构卡内容对不上参考片——
这些在 18 项绿灯里一项都照不出来。

## 具体停掉的

| 文件 | 行数 | 作用 |
|---|---|---|
| `scripts/pack_style.py` | 409 | 包装模板 → 渲染参数翻译层 |
| `scripts/pick_pack.py` | 494 | 按素材/时长自动选包 |
| `scripts/measure_pack.py` | 380 | 成片像素量 8 个 token 判是否在带内 |
| `scripts/compare_films.py` | 244 | 成片 vs 参考片逐帧对照 |
| `scripts/probe_packaging.py` | 295 | 参考片探针（切点/底带剖面） |
| `scripts/ref_frames.py` | 108 | 参考片取帧做联系表 |
| `scripts/gen_boroll.py` | 586 | 代码画结构卡（grid/panel/stats） |
| `scripts/match_font.py` | 268 | 从参考片反推字体 |
| `scripts/font_sheet.py` | 123 | 字体候选对照卡 |
| `styles/pack-*.yaml` | 6 个 | 五族模板 + 暖知识口播 |
| `styles/set-qizao.yaml` | — | 可组合套 |
| `references/packaging-families.md`、`pack-compose-set.md`、`packtest-2026-11-08/` | — | 全部结论与实测记录 |

合计约 **2,900 行代码**，占仓库 26%。代码**不删**——实测数据都在里面，
将来换思路时这些片子、这些量法还有参考价值——只是**不再进主链路**。

## 主链路现在长什么样

```bash
# A 索引
python3 scripts/index_assets.py --library <库> --stage all
# B 决定
python3 scripts/intake.py --project <项目>
python3 scripts/make_edl.py --project <项目>
python3 scripts/build_takes_packed.py --project <项目>
python3 scripts/render_paper_edit.py --project <项目>
# C 成片（不带 --pack）
python3 scripts/render_film.py --project <项目> --library <库> \
  --style styles/talking-head-v1.yaml
# D 验收
python3 scripts/verify_film.py --project <项目>
```

`render_film.py` 里 `--pack` / `--shell` 两个参数还留着（拆掉会牵动 1200 行），
但**文档不再引导使用**。不带 `--pack` 时走 `styles/talking-head-v1.yaml`：
字幕 1080 宽 80px、居中 0.674H、思源黑体 SemiBold。

## 如果将来要复活

至少要换掉一个前提：**别再用"从参考片量出来的比例"当目标**。
今天证明的是——按比例复刻出来的东西，观众一眼能看出"不是那个味道"，
而味道恰恰不写在比例里。真要做得像，要么找一支**能拆到镜头语法层**的片子
（不是字号和带位），要么干脆放弃"像某支片"，走「一类内容一套固定版式」。

重生之前先回答一个问题：**包装是为了"像谁"，还是为了"让人看懂"？**
这两个目标的验收方式完全不同——今天用前者的问题，配了后者的尺子。
