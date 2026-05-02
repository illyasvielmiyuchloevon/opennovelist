# PLAN

## 总体目标

把整卷组纲计划生成与审核前置到卷资料适配阶段，并让章节重写完全按已审核动态组纲计划推进。

## 阶段划分总览

| 阶段 | 目标 | 任务数 | 状态 |
| --- | --- | ---: | --- |
| Stage 1 | 卷级组纲前置与动态章节组计划 | 4 | [x] |

---

## Stage 1: 卷级组纲前置与动态章节组计划

### 阶段目标

完成从卷资料适配到章节重写的分组语义迁移：卷资料审核后生成并审核整卷组纲计划；章节阶段只消费已审核组纲计划。

### 复杂任务

- [x] Task 1: 建立组纲计划领域模型与路径契约
  - 交付物：`novelist/core/group_outline_plan.py`
  - 验收标准：可写入、读取、校验动态组纲计划和组纲路径。
- [x] Task 2: 接入 `novel_adaptation` 的组纲生成与审核阶段
  - 交付物：`novelist/workflows/adaptation/group_outlines.py` 及 runner/project/prompt 集成。
  - 验收标准：卷资料审核通过后继续组纲生成与审核，组纲审核通过后才标记 processed。
- [x] Task 3: 改造 `novel_chapter_rewrite` 的动态分组与无参考源章节输入
  - 交付物：chapter rewrite catalog/group runner/review/prompt/state/runner 更新。
  - 验收标准：章节组来自组纲计划；组生成、组审、卷级审核不读取参考源章节正文；组纲只读冻结。
- [x] Task 4: 补齐测试、文档和交付记录
  - 交付物：测试更新、README/USAGE/ARCHITECTURE/PRD/PLAN/QA/REVIEW/DELIVERY/PR 文档。
  - 验收标准：目标测试与关键回归测试通过，文档不再描述固定 5 章作为运行分组依据。

### 阶段测试

- [x] 运行单元测试
- [x] 运行集成测试或关键流程测试
- [ ] 运行 lint / typecheck / build

### 阶段自动审查

- [x] 代码结构审查
- [x] 安全风险审查
- [x] 冗余与可维护性审查
- [x] PRD 对齐审查

### 阶段 PR

- [ ] 创建阶段分支
- [ ] 提交 commit
- [x] 创建 PR 或生成 PR 文档

---
