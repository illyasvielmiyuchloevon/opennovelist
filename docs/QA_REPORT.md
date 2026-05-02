# QA Report

## Stage 1 测试记录

### 执行命令

```bash
python -m pytest tests/test_novel_adaptation.py tests/test_novel_chapter_rewrite.py -q
python -m pytest tests/test_novel_workflow.py tests/test_split_novel_rebalance.py -q
python -m pytest tests/test_novel_adaptation.py tests/test_novel_chapter_rewrite.py tests/test_novel_workflow.py tests/test_split_novel_rebalance.py -q
```

### 结果

- 单元测试：通过
- 集成测试：通过
- Lint：未运行
- Typecheck：未运行
- Build：不适用

### 失败与修复记录

| 问题 | 原因 | 修复方式 | 复测结果 |
| --- | --- | --- | --- |
| `rewrite_workflow.write_group_outline_plan_manifest` 未导出 | 测试辅助需要通过章节工作流 facade 写入组纲计划 | 从 `chapter_rewrite._shared` 重新导出核心 helper | 83 个目标测试通过 |
| 章节组审允许改写 `group_outline` | 章节阶段应冻结已审核组纲 | 从组审/卷审返修目标移除组纲，并补充提示词与测试断言 | 83 个目标测试通过 |
| 适配组纲阶段缺少 agent 摘要 helper | 新增组纲阶段复用了 agent 摘要输出，但适配共享层未导出 helper | 在 `adaptation._shared` 补齐 agent 摘要 helper，并新增组纲生成/失败审核测试 | 119 个目标与关键回归测试通过 |

最终复测：119 个目标与关键回归测试通过。
