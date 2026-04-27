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
│  │  ├─ novel_adaptation.py       # 兼容入口 facade
│  │  ├─ novel_chapter_rewrite.py  # 兼容入口 facade
│  │  ├─ novel_workflow.py         # 兼容入口 facade
│  │  ├─ adaptation/               # 资料适配内部实现
│  │  ├─ chapter_rewrite/          # 章节重写内部实现
│  │  ├─ unified/                  # 统一入口内部实现
│  │  ├─ document_repair.py        # 跨工作流 document_ops 修复辅助
│  │  └─ prompt_summary.py         # 跨工作流请求摘要辅助
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
  负责具体业务流程编排。`novel_*.py` 文件是旧命令与旧导入的兼容 facade，主要实现位于对应子包。
- `novelist/core/`
  负责可复用能力，不直接承载业务流程。
- 根目录 `novel_workflow.py`
  只是统一入口包装层，方便直接运行。

## 2. 四个工作流入口的职责

### 2.1 `novelist.workflows.split_novel`

负责把原始小说 `.txt` 拆成：

- 按章节编号命名的章节文件
- 默认最多 50 章一个卷目录
- 按 150k source bundle 字符预算自适应减少超大卷章数，并把卷尾章节顺延到后续卷
- 第一卷中的简介文件

输出是后续工作流的参考源目录。

### 2.2 `novelist.workflows.novel_adaptation`

负责逐卷生成改编规划，包括：

- 世界模型（合并承载世界观设计）
- 文笔写作风格
- 全书大纲
- 伏笔文档
- 卷级大纲

它面向“卷级规划层”。

启动后会先检查当前未完成卷及后续卷是否超过参考源注入预算；如果需要，会冻结 `processed_volumes` 中已完成卷，只重排当前未完成卷及后续卷，并备份受影响的旧源卷和未完成产物。

内部实现位于 `novelist.workflows.adaptation`，按项目/素材/prompt/资料生成/审核/runner 拆分；外部仍可使用旧模块名运行或导入。

### 2.3 `novelist.workflows.novel_chapter_rewrite`

负责按最多五章一组重写与审核；当前卷最后不足五章时按短组处理，不跨卷补章。包括：

- 组纲（一个文件内包含当前组每章细纲）
- 当前组正文
- 状态文档更新
- 组审查
- 卷级审核

它面向“章节生产层”。

章节工作流不会直接重排参考源卷；如果发现当前或后续源卷需要自适应重分卷，会停止并提示先回到 `novel_adaptation`，避免章节正文使用已经失效的卷级资料。

内部实现位于 `novelist.workflows.chapter_rewrite`，按项目状态、文档目录、prompt、Responses 调用、document repair、审核、章节/卷 runner 拆分；外部旧模块名保持兼容。

### 2.4 `novelist.workflows.novel_workflow`

负责统一调度：

- 自动识别输入类型
- 串联 `split_novel -> adaptation -> chapter_rewrite`
- 断点续跑
- OpenAI / OpenAI Compatible 配置

内部实现位于 `novelist.workflows.unified`；旧 `novelist.workflows.novel_workflow` 仍是兼容入口。

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
- token usage 标准化统计：发送、接收、推理、缓存命中、缓存写入
- 错误重试与终止策略

全局文档用于长期复用的索引、规则和映射；全书故事走向统一收敛到全书大纲，卷内推进留给卷级剧情进程和章节状态文档。

### 3.6 `novelist.core.ui`

交互入口层：

- 进度输出
- 选择输入
- 暂停退出

## 4. 当前总共有几个模型工具

当前对模型暴露的工具总共 **4 个**。

### 4.1 `submit_workflow_result`

这是两个主工作流共同使用的统一阶段提交工具。

用途：

- 生成阶段结束时提交完成摘要和已处理文件清单
- 审核阶段结束时提交审核结果
- 提交结构化审核字段

对应代码：

- [novelist/core/workflow_tools.py](../novelist/core/workflow_tools.py)
  - `WORKFLOW_SUBMISSION_TOOL_NAME`
  - `WorkflowSubmissionPayload`

它不是“说明工具”，而是：
**agent 阶段完成时使用的统一结果提交工具。**

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

## 5. 统一工具系统

当前两个工作流阶段统一暴露同一组 4 个工具：

- `submit_workflow_result`
- `submit_document_writes`
- `submit_document_edits`
- `submit_document_patches`

文档工具主要用于所有“需要落盘目标文件”的场景，包括新建、局部编辑、追加、按标题替换小节。

主要包括：

- `novel_adaptation` 生成阶段：同一个 agent 会话覆盖当前卷所有规划目标，可多轮调用工具落盘
- `novel_adaptation` 审核阶段：允许先返修再提交审核结论
- `novel_chapter_rewrite` 组生成阶段：同一个 agent 会话覆盖组纲、当前组正文和状态文档，可多轮调用工具落盘
- 组审 / 卷审阶段：允许先返修组纲、章节正文、状态文档、审核文档，再提交审核结论

agent 运行时按 OpenCode 风格维护本地 transcript：首轮发送阶段完整上下文，工具轮会把本阶段大上下文、已发生的工具调用和工具结果一起重新组装发送；`previous_response_id` 只作为历史记录保存，不作为 agent 工具轮的唯一上下文来源。

`novel_adaptation` 的卷资料审核阶段会在同一个审核逻辑会话内做上下文压缩：每次审核请求都重新发送稳定前缀、当前卷参考源和最新落盘资料文档，但 provider 请求不沿用生成阶段或上一轮审核的旧 `previous_response_id`。这对应 OpenCode 在同一个 session 中用压缩后的消息历史继续运行：逻辑会话不断，发给模型的旧版卷资料和旧工具历史被替换掉。

运行时由 `novelist/core/agent_runtime.py` 统一循环处理文档工具调用，并在最终收到 `submit_workflow_result` 后结束阶段。

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

组生成：

- `build_five_chapter_generation_shared_prompt()`

组审查：

- `build_five_chapter_review_shared_prompt()`

卷级审核：

- `build_volume_review_shared_prompt()`

共享前缀中通常放：

- 项目上下文
- 当前卷/当前组定位
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
- 旧单章章纲只作为兼容输入，不再作为新目标注入

这部分尽量稳定，放在动态 payload 的前段。

### 7.2 请求核心：`request_fields`

通常包括：

- `document_request`
  - `phase`
  - `role`
  - `task`
  - `required_file` / `required_group_outline_file`
- `reference_chapter_metrics`
- `requirements`

这部分决定“当前到底在做什么”。

### 7.3 后段滚动内容：`trailing_doc_fields`

通常包括：

- `rolling_injected_global_docs`
- `rolling_injected_volume_docs`
- `rolling_injected_group_docs`
- `current_group_outline`
- `review_skill_reference`
- `update_target_files`
- `current_generated_chapter`
- `rewritten_chapters`

这一层变化最大，因此放在后段。

## 8. 审核步骤中的 skill 注入顺序

`skill/chapter_review/SKILL.md` 现在只注入到：

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

`skill/chapter_writing/SKILL.md` 主要注入到：

- `group_generation`
- 旧兼容路径中的 `phase2_chapter_text`

并且它当前的位置是：

- 不放进 `instructions`
- 不放进组共享前缀 `shared_prompt`
- 放在 `request_fields`
- 也就是正文仿写阶段原本的主写作规则位置

原因：

- 它不是审核补充资料，而是正文生成阶段的核心写作约束
- 它需要和 `document_request`、`reference_chapter_metrics`、`requirements` 一起定义“这一阶段怎么写”
- 它只在正文仿写 / 组生成阶段按需加载，不会进入审核阶段

## 9. `submit_workflow_result` 的设计目的

这个工具的设计目标是：

- 让组生成 / 审核统一走一条 agent 工具链
- 让文件写入统一交给 write/edit/patch，而阶段完成统一交给 `submit_workflow_result`
- 降低不同阶段之间工具名过多造成的兼容问题
- 让运行时只维护一套工具提取与文档落盘循环

它当前承载的字段较多，因此运行时还额外做了：

- 审核结果归一化
- `passed` 推断
- `chapters_to_revise` 提取
- 审核 Markdown 固定骨架重建

新流程不再新建章级审核；旧章纲 / 章审文件只作为兼容遗留文件存在。

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
