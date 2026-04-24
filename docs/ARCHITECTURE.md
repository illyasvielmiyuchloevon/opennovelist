# 架构说明

本文档面向开发者，说明当前仓库的代码分层、工具体系、Prompt 结构，以及统一工作流如何调度各个工作流入口。

## 1. 仓库分层

当前仓库采用包化结构：

```text
仓库根目录/
├─ novel_workflow.py      # 根目录统一入口包装脚本
├─ start_workflow.bat         # Windows 一键启动脚本
├─ novelist/
│  ├─ workflows/              # 业务工作流
│  │  ├─ split_novel.py
│  │  ├─ novel_adaptation.py
│  │  ├─ novel_chapter_rewrite.py
│  │  └─ novel_workflow.py
│  └─ core/                   # 可复用核心模块
│     ├─ files.py
│     ├─ document_ops.py
│     ├─ novel_source.py
│     ├─ openai_config.py
│     ├─ responses_runtime.py
│     └─ ui.py
├─ docs/
└─ tests/
```

职责划分：

- `novelist/workflows/`
  负责具体业务流程编排。
- `novelist/core/`
  负责可复用能力，不直接承载业务流程。
- 根目录 `novel_workflow.py`
  只是统一入口包装层，方便直接运行。

## 2. 四个工作流入口的职责

### 2.1 `novelist.workflows.split_novel`

负责把原始小说 `.txt` 拆成：

- 按章节编号命名的章节文件
- 每 50 章一个卷目录
- 第一卷中的简介文件

输出是后续工作流的参考源目录。

### 2.2 `novelist.workflows.novel_adaptation`

负责逐卷生成改编规划，包括：

- 世界观设计
- 世界模型
- 文笔写作风格
- 全书大纲
- 伏笔文档
- 全局剧情进程
- 卷级大纲

它面向“卷级规划层”。

### 2.3 `novelist.workflows.novel_chapter_rewrite`

负责逐章重写与审核，包括：

- 章纲
- 正文
- 状态文档更新
- 章级审核
- 组审查
- 卷级审核

它面向“章节生产层”。

### 2.4 `novelist.workflows.novel_workflow`

负责统一调度：

- 自动识别输入类型
- 串联 `split_novel -> adaptation -> chapter_rewrite`
- 断点续跑
- OpenAI / OpenAI Compatible 配置

## 3. `core` 的职责

### 3.1 `novelist.core.files`

文件与文本层工具：

- 路径规范化
- Markdown / JSON 读写
- 文本 patch 的多策略匹配与替换，匹配策略对齐 `.external/edit.ts` 的 oldString/newString 工具
- 行尾兼容

### 3.2 `novelist.core.document_ops`

目标文件写入/patch 工具层：

- 定义章节正文、Markdown 状态文档等目标文件的写入工具 schema
- 定义章节正文、Markdown 状态文档等目标文件的 patch/edit 工具 schema
- 执行多文件、多块 patch
- 应用模型返回的目标文件操作结果

### 3.3 `novelist.core.novel_source`

小说来源数据读取层：

- 卷目录扫描
- 章节文件与补充文件读取
- source bundle 组装
- 上下文裁剪

### 3.4 `novelist.core.openai_config`

OpenAI 配置层：

- 全局配置读写
- provider / protocol 选择
- `api_key / base_url / model` 解析
- 客户端创建

### 3.5 `novelist.core.responses_runtime`

LLM 运行时：

- OpenAI Responses
- OpenAI Compatible Chat Completions
- 流式收事件
- tool call 参数提取
- 错误重试与终止策略

### 3.6 `novelist.core.ui`

交互入口层：

- 进度输出
- 选择输入
- 暂停退出

## 4. 当前总共有几个模型工具

当前对模型暴露的工具总共 **4 个**。

### 4.1 `submit_workflow_result`

这是 `novel_chapter_rewrite` 主工作流使用的统一函数工具。

用途：

- 提交章纲 Markdown
- 提交章节正文
- 提交审核结果
- 提交结构化审核字段

对应代码：

- [novelist/workflows/novel_chapter_rewrite.py](../novelist/workflows/novel_chapter_rewrite.py)
  - `WORKFLOW_SUBMISSION_TOOL_NAME = "submit_workflow_result"`
  - `WorkflowSubmissionPayload`

它不是“说明工具”，而是：
**章节工作流主线使用的统一结果提交工具。**

### 4.2 `submit_document_writes`

用于整篇写入一个或多个文档。

适用场景：

- 文件不存在
- 文件为空
- 确实需要完整新建文档结构

### 4.3 `submit_document_edits`

用于对已有目标文件做精确编辑。目标文件可以是章节正文 `.txt`、Markdown 状态文档或其他工作流文件。

适用场景：

- 已有段落内容改写
- 已有记录局部替换
- 同一文件顺序执行多个 `old_text -> new_text` 编辑
- 模型可以用 `file_key` 指定输入清单里的目标，也可以用 `file_path` 直接指定目标文件路径

### 4.4 `submit_document_patches`

用于对一个或多个目标文件做增量 patch。

适用场景：

- 已有目标文件的局部修改
- 多文件 patch
- 单文件内多个编辑块
- 模型可以用 `file_key` 指定输入清单里的目标，也可以用 `file_path` 直接指定目标文件路径

落盘时不执行 format，也不执行 LSP 检查；只做文本匹配、唯一性校验和文件写入。

对应代码：

- [novelist/core/document_ops.py](../novelist/core/document_ops.py)

## 5. 两条工具链

当前仓库实际上有两条模型工具链：

### 5.1 主工作流工具链

使用：

- `submit_workflow_result`

主要被 `novel_chapter_rewrite` 的这些步骤使用：

- 章纲
- 正文
- 章审
- 组审查
- 卷审查

### 5.2 目标文件操作工具链

使用：

- `submit_document_writes`
- `submit_document_edits`
- `submit_document_patches`

主要用于所有“已有目标文件需要局部更新”的场景，包括章节正文 `.txt` 修订和长期状态文档更新。

主要被：

- `novel_adaptation`
- `novel_chapter_rewrite` 的正文修订阶段
- `novel_chapter_rewrite` 的状态文档更新阶段

使用。

## 6. Prompt 结构总览

当前 `novel_chapter_rewrite` 的 prompt 由四层组成。

### 6.1 `instructions`

这是最上层系统级规则。

按阶段使用不同常量：

- `COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS`
- `COMMON_SUPPORT_UPDATE_INSTRUCTIONS`
- `COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS`
- `COMMON_VOLUME_REVIEW_INSTRUCTIONS`

主要承载：

- 角色定位
- 当前任务边界
- 必须通过函数工具提交结果
- patch / write 的行为规则

### 6.2 `shared_prompt`

这是共享前缀层，主要为缓存服务。

章节工作流：

- `build_chapter_shared_prompt()`

组审查：

- `build_five_chapter_review_shared_prompt()`

卷级审核：

- `build_volume_review_shared_prompt()`

共享前缀中通常放：

- 项目上下文
- 当前卷/当前章/当前组定位
- 固定 workflow rules
- 当前 source bundle 或章节清单

### 6.3 `Dynamic Request`

这是共享前缀之后的动态 payload。

统一由：

- `build_payload_with_cache_layers(...)`

组装。

内部顺序固定为：

1. `shared_prefix_fields`
2. `request_fields`
3. `trailing_doc_fields`

### 6.4 工具层

工具 schema 不在文本 prompt 里，但和请求一起发送，也会影响缓存与行为。

## 7. `Dynamic Request` 的具体顺序

### 7.1 前段稳定注入：`shared_prefix_fields`

通常包括：

- `stable_injected_global_docs`
- `stable_injected_volume_docs`
- `stable_injected_chapter_docs`

这部分尽量稳定，放在动态 payload 的前段。

### 7.2 请求核心：`request_fields`

通常包括：

- `document_request`
  - `phase`
  - `role`
  - `task`
  - `required_file`
- `reference_chapter_metrics`
- `requirements`

这部分决定“当前到底在做什么”。

### 7.3 后段滚动内容：`trailing_doc_fields`

通常包括：

- `rolling_injected_global_docs`
- `rolling_injected_volume_docs`
- `rolling_injected_chapter_docs`
- `rolling_injected_group_docs`
- `review_skill_reference`
- `update_target_files`
- `current_generated_chapter`
- `rewritten_chapters`

这一层变化最大，因此放在后段。

## 8. 审核步骤中的 skill 注入顺序

`skill/chapter_review/SKILL.md` 现在只注入到：

- 章级审核
- 组审查
- 卷级审核

并且它当前的位置是：

- 不放进 `instructions`
- 不放进审核 `shared_prompt`
- 不放进 `request_fields`
- 只放进 `trailing_doc_fields`
- 并且排在滚动文档后面

原因：

- 审核发生时，当前章/组/卷的滚动文档已经相对稳定
- 先让稳定/滚动文档保持更靠前
- 再把 skill 当作附加审核准则放到后面
- 这样更利于提示词缓存命中

`skill/chapter_writing/SKILL.md` 只注入到：

- `phase2_chapter_text`

并且它当前的位置是：

- 不放进 `instructions`
- 不放进章节共享前缀 `shared_prompt`
- 放在 `request_fields`
- 也就是正文仿写阶段原本的主写作规则位置

原因：

- 它不是审核补充资料，而是正文生成阶段的核心写作约束
- 它需要和 `document_request`、`reference_chapter_metrics`、`requirements` 一起定义“这一阶段怎么写”
- 它只在正文仿写阶段按需加载，不会进入审核或状态更新阶段

## 9. `submit_workflow_result` 的设计目的

这个工具的设计目标是：

- 让章纲 / 正文 / 审核统一走一条主工具链
- 降低不同阶段之间工具名过多造成的兼容问题
- 让运行时只维护一套章节主流程的工具提取逻辑

它当前承载的字段较多，因此运行时还额外做了：

- 审核结果归一化
- `passed` 推断
- `chapters_to_revise` 提取
- 审核 Markdown 固定骨架重建

如果未来要进一步提升稳定性，最可能的方向是：

- 保留统一主工具思想
- 但把章审 / 组审查 / 卷审查拆成更小的 schema

## 10. 统一工作流如何调度

统一入口的大致顺序是：

1. 识别输入类型
2. 如需要，先 `split_novel`
3. 运行 `novel_adaptation`
4. 再运行 `novel_chapter_rewrite`
5. 结束后回到统一入口菜单

同时它支持：

- adaptation 已完成但 rewrite 未完成时优先续跑 rewrite
- workflow-controlled 子流程模式
- 重新配置 OpenAI 设置

## 11. 开发建议

如果你后续继续改这里，建议优先遵守这几条：

1. 业务流程改动尽量放在 `novelist/workflows/`
2. 工具 schema、运行时兼容、文件 patch 能力尽量放在 `novelist/core/`
3. 新增 prompt 资料时优先考虑它属于：
   - `instructions`
   - `shared_prompt`
   - `request_fields`
   - `trailing_doc_fields`
4. 凡是可能影响缓存的新增注入内容，优先往后放
5. 凡是可能导致解析不稳定的新输出格式，优先补测试
