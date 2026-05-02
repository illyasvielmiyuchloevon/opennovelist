# Delivery Report

## 1. 交付概览

已完成“卷级组纲前置与动态章节组计划”：卷资料适配流程新增整卷组纲计划生成、组纲生成和组纲审核；章节重写流程改为按已审核组纲计划推进。

## 2. 已实现功能

- 新增组纲计划 manifest 与路径 helper。
- `novel_adaptation` 在卷资料审核后继续组纲计划、组纲生成和组纲审核。
- 组纲审核通过后才把当前卷写入 `processed_volumes`。
- `novel_chapter_rewrite` 从组纲计划读取动态章节组，不再固定 5 章。
- 章节组生成、组审、卷级审核不再读取参考源章节正文。
- 章节阶段冻结已审核组纲。
- README、USAGE、ARCHITECTURE 和交付文档已同步。

## 3. 未实现 / 延后内容

- 遗留函数名中的 `five_chapter` 尚未重命名，以避免破坏旧导入和测试兼容。
- 未实现旧工程自动迁移；缺少组纲计划时会阻断并提示补跑卷资料适配组纲阶段。

## 4. 运行方式

```powershell
python -m novelist.workflows.novel_adaptation "F:\books\我的小说"
python -m novelist.workflows.novel_chapter_rewrite "F:\books\新书工程目录" --run-mode volume
```

## 5. 测试与质量结果

- `python -m pytest tests/test_novel_adaptation.py tests/test_novel_chapter_rewrite.py -q`：83 passed
- `python -m pytest tests/test_novel_workflow.py tests/test_split_novel_rebalance.py -q`：36 passed
- `python -m pytest tests/test_novel_adaptation.py tests/test_novel_chapter_rewrite.py tests/test_novel_workflow.py tests/test_split_novel_rebalance.py -q`：119 passed

## 6. PR 列表或 PR 文档列表

- `docs/pr/stage-1.md`

## 7. 风险、限制与后续建议

- 历史工程必须补跑新组纲计划，不能直接进入章节重写。
- 后续可把兼容命名 `five_chapter` 迁移为 `group`，但建议单独处理，避免扩大本次改动面。
