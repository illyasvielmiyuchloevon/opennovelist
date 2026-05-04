# 架构说明

本文档说明当前仓库的代码分层、工作流边界和模型工具。

## 1. 代码分层

```text
仓库根目录/
├─ novel_workflow.py
├─ start_workflow.bat
├─ novelist/
│  ├─ workflows/
│  │  ├─ split_novel.py
│  │  ├─ novel_adaptation.py
│  │  ├─ novel_chapter_rewrite.py
│  │  ├─ novel_workflow.py
│  │  ├─ adaptation/
│  │  ├─ chapter_rewrite/
│  │  └─ unified/
│  └─ core/
│     ├─ files.py
│     ├─ document_ops.py
│     ├─ novel_source.py
│     ├─ openai_config.py
│     ├─ responses_runtime.py
│     └─ ui.py
├─ docs/
└─ tests/
```

`novelist/workflows/novel_*.py` 是兼容 facade；主要实现位于对应子包。根目录 `novel_workflow.py` 只是统一入口包装层。

## 2. 工作流边界

### 2.1 `split_novel`

负责把原始小说拆成章节文件和卷目录。它会按 150k source bundle 字符预算自适应减少超大卷章数，已完成适配的卷在后续资料适配检查中被冻结。

### 2.2 `novel_adaptation`

负责卷级资料适配：

- 世界模型
- 文笔写作风格
- 全书大纲
- 伏笔管理
- 卷级大纲
- 卷资料审核

卷资料审核通过后，该卷写入 `processed_volumes`。旧工程里已完成适配的卷不会再被要求补额外规划文件，也不会在章节工作流入口触发参考源重排。

审核通过时会同步写入 `group_injection/<volume>_group_injection/00_chapter_group_plan.md`。它只是章节组范围清单，由源章节字符预算和最大组内章数本地计算，不会增加新的模型阶段。

内部实现位于 `novelist.workflows.adaptation`，按项目状态、素材读取、prompt、资料生成、审核和 runner 拆分。

### 2.3 `novel_chapter_rewrite`

负责章节生产层：

- 单章章纲
- 单章正文
- 配套状态文档
- 章级审核
- 组审查
- 卷级审核

章节组模式只决定范围。组内仍逐章调用单章工作流，因此质量控制回到旧版单章粒度。组审查只在当前章节组全部章节通过章级审核后执行。

章节组范围来源：

- `group_injection/<volume>_group_injection/00_chapter_group_plan.md`
- 若不存在，按旧版 5 章一组回退。

章节工作流不会直接执行参考源自适应分卷检查，避免旧工程已完成卷被重新拆动。

### 2.4 `novel_workflow`

统一入口负责：

- 自动识别输入类型
- 串联 `split_novel -> adaptation -> chapter_rewrite`
- 检测和续跑断点
- 统一 OpenAI / Compatible 配置

内部实现位于 `novelist.workflows.unified`。

## 3. 核心模块

- `novelist.core.files`
  Markdown / JSON 读写、路径规范化、文本替换与 patch 辅助。
- `novelist.core.document_ops`
  文档写入、编辑、patch 工具 schema 与应用逻辑。
- `novelist.core.novel_source`
  卷目录扫描、章节/补充文件读取、source bundle 组装。
- `novelist.core.openai_config`
  provider、protocol、api key、base url、model 配置与客户端创建。
- `novelist.core.responses_runtime`
  Responses / Chat Completions 调用、流事件解析、tool call 提取、token usage 标准化和错误重试。
- `novelist.core.ui`
  CLI 进度输出、选择输入和暂停退出。

## 4. 模型工具

当前工作流对模型暴露通用工具：

- `submit_workflow_result`
  阶段完成或审核完成时提交结构化结果。
- `submit_document_writes`
  新建或整篇写入目标文件。
- `submit_document_edits`
  对已有目标文件做精确编辑。
- `submit_document_patches`
  对已有目标文件做结构化 patch。

章节重写的正文修订、状态文档更新、章审/组审/卷审返修都复用这组通用文档工具。

## 5. 状态文件

资料适配：

- `00_project_manifest.md`
- `volume_injection/<volume>_volume_injection/00_stage_manifest.md`
- `volume_injection/<volume>_volume_injection/<volume>_adaptation_review.md`

章节重写：

- `00_chapter_rewrite_manifest.md`
- `volume_injection/<volume>_volume_injection/<chapter>_chapter_outline/00_stage_manifest.md`
- `group_injection/<volume>_group_injection/<range>_group_injection/00_group_stage_manifest.md`
- `group_injection/<volume>_group_injection/<range>_group_injection/<range>_group_review.md`
- `volume_injection/<volume>_volume_injection/<volume>_volume_review.md`

## 6. Prompt 和缓存

资料适配生成阶段使用本地 agent transcript：工具轮会重发本阶段完整上下文和工具历史。卷资料审核阶段会重新组装最新落盘文档、稳定前缀和当前卷参考源，保证审核看到真实文件状态。

章节重写按单章会话组织：章纲、正文、状态文档、章级审核连续使用同一个章节缓存键。组审查和卷级审核使用各自审核缓存键，并在失败返修后沿用审核状态中的 response id。

## 7. 兼容原则

- 旧工程已经完成资料适配的卷直接进入章节重写候选。
- 章节组计划是可选范围输入，不是内容规划文件。
- 没有章节组计划时按 5 章一组运行。
- 章节组模式不跳过单章章纲和章级审核。
