# PR: Stage 1 - 卷级组纲前置与动态章节组计划

## 1. 本阶段目标

把整卷组纲计划生成与审核并入卷资料适配流程，并让章节重写按已审核动态组纲计划推进。

## 2. 完成内容

- 新增 `novelist.core.group_outline_plan`。
- 新增 `novel_adaptation` 的组纲计划、组纲生成、组纲审核阶段。
- 改造 `novel_chapter_rewrite` 的动态分组、组生成、组审和卷审输入。
- 移除章节组生成/审核/卷审中的参考源章节正文依赖。
- 更新 README、USAGE、ARCHITECTURE、PRD、PLAN、QA、REVIEW、DELIVERY。

## 3. 变更文件

- `novelist/core/group_outline_plan.py`
- `novelist/workflows/adaptation/*`
- `novelist/workflows/chapter_rewrite/*`
- `tests/test_novel_adaptation.py`
- `tests/test_novel_chapter_rewrite.py`
- `README.md`
- `docs/*`

## 4. 测试结果

```bash
python -m pytest tests/test_novel_adaptation.py tests/test_novel_chapter_rewrite.py -q
# 83 passed

python -m pytest tests/test_novel_workflow.py tests/test_split_novel_rebalance.py -q
# 36 passed

python -m pytest tests/test_novel_adaptation.py tests/test_novel_chapter_rewrite.py tests/test_novel_workflow.py tests/test_split_novel_rebalance.py -q
# 119 passed
```

## 5. 自动审查结果

- PRD 对齐：通过
- 代码结构：通过
- 安全与稳定性：通过
- 测试质量：通过

## 6. 风险与回滚方案

- 风险：旧工程缺少新组纲计划会被阻断。
- 处理：按设计提示补跑卷资料适配的组纲生成/审核阶段。
- 回滚：恢复章节分组来源到旧逻辑并移除 `group_outline_plan` 接入点。

## 7. 验收标准对照

- [x] 卷资料审核通过后不会立即标记 processed。
- [x] 组纲生成能写出动态组数和每组组纲文件。
- [x] 组纲审核通过后才标记 processed。
- [x] 章节工作流读取动态组纲计划。
- [x] 没有计划时阻断，不回退固定 5 章。
- [x] 组生成、组审、卷审不包含参考源正文输入。
