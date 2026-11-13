"""场景模块包：往 `gen_scene.SCENES` 里加新画法的唯一入口。

每个模块导出四样东西，`gen_scene._register_scene_package()` 自动收进来：

    SCENE_ID     场景名（时间轴上 `props.scene` 写这个）
    scene(t, W, H, dur) -> Image   纯 t 驱动：同一秒永远同一帧（`test_scene_determinism.py` 的合同）
    EVENTS       场景内音效锚点 [{"id","t","name","intent"}]，name 必须是 CC0 库里的音效 id
    STORY_BEATS  三拍描述（setup / change / result）——先讲故事，再写代码

**准入先过原则 B14「有实拍就不画」**：库里能拍到的画面、本来就有的真实素材，
不许拿代码画去替。只有「库里没有、也拍不出来」的关系（抽象口径、因果链、
量的对比、空间关系）才进这里，并且先在现有 gen 模板里找（`compare` 两栏 /
`balance` 天平 / `flow` 流程 / `layers` 剖面）。

2026-11-14 凌晨那次实验就是反例：两套场景（排队打勾 / 双盘计数）画出来都是
「PPT 图形」，根因是选错了要替的那一格（那一格本来有真实拍的样板特写）。
判据和过程记录在 `references/editorial-packaging-principles.md` 原则 B14。
"""
