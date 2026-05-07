# 使用说明

本文档按实际操作顺序说明当前工作流。

## 1. 工作流总览

1. `novelist.workflows.split_novel`
   拆分原始小说，并按参考源注入预算自适应分卷。
2. `novelist.workflows.novel_adaptation`
   逐卷生成资料文档，随后进行卷资料审核。审核通过后写入轻量章节组范围计划，该卷资料适配完成。
3. `novelist.workflows.novel_chapter_rewrite`
   章节组模式只确定运行范围，组内仍逐章执行单章工作流：章纲、正文、配套状态文档、章级审核。章节组完成后运行组审查，整卷完成后运行卷级审核。
4. `novel_workflow.py`
   统一入口，自动识别输入、配置模型、续跑断点并串联以上步骤。

## 2. 准备

安装依赖：

```powershell
pip install openai pydantic
```

准备：

- 原始小说 `.txt`，或已经拆分好的书名目录，或已有工程目录。
- OpenAI、OpenAI Compatible，或 OpenCode Go 接口。
- `api_key`、`base_url`、`model`。

配置会保存到：

```text
%USERPROFILE%\.novel_adaptation\config.json
```

旧配置目录 `%USERPROFILE%\.novel_adaptation_cli\config.json` 会自动迁移。

如果使用 `OpenAI Compatible` 协议并且上游兼容服务支持提示词缓存，还可以在同一个全局配置文件里补充 provider-specific 参数：

```json
{
  "openai_compatible_extra_body": {
    "prompt_cache_key": "{{prompt_cache_key}}"
  },
  "openai_compatible_extra_headers": {
    "x-prompt-cache-key": "{{prompt_cache_key}}"
  },
  "openai_compatible_cache_read_paths": [
    "prompt_tokens_details.cached_tokens",
    "nim_cache.hit_tokens"
  ],
  "openai_compatible_cache_write_paths": [
    "input_tokens_details.cache_write_tokens",
    "nim_cache.write_tokens"
  ],
  "openai_compatible_transport": "stream",
  "openai_compatible_reasoning_effort": "high"
}
```

说明：

- `OpenAI Compatible` 协议本身没有统一的提示词缓存请求字段，是否支持缓存取决于上游兼容服务。
- `openai_compatible_extra_body` / `openai_compatible_extra_headers` 会原样并入 Chat Completions 请求；兼容协议现在直接走 HTTP `/chat/completions`，所以 `extra_body` 会合并到最终 JSON 顶层，而不是继续嵌成 SDK 的 `extra_body` 字段。其中 `{{prompt_cache_key}}` 会在运行时替换成当前章节或审核阶段的缓存键。
- `openai_compatible_cache_read_paths` / `openai_compatible_cache_write_paths` 用于告诉 CLI 去哪里读取兼容提供商返回的缓存命中和缓存写入 token 统计。
- `openai_compatible_transport` 可选 `stream` 或 `nonstream`。默认是 `stream`，与 opencode 主会话链路一致；如果某个兼容服务的 SSE 特别不稳定，再显式切到 `nonstream`。当请求体很大且兼容网关在首选 transport 上直接断连时，运行时会自动再试另一种 transport 一次。
- `openai_compatible_reasoning_effort` 会透传为顶层 `reasoning_effort`。DeepSeek V4 默认会落到 `high`；如果 base URL 是 `https://api.deepseek.com`，运行时还会补 `thinking: {"type": "enabled"}`。
- 如果不配置这些字段，兼容服务只有在本身返回标准缓存 usage 字段时，CLI 才能显示非 0 的缓存命中。

OpenCode Go 说明（官方文档）：`https://opencode.ai/docs/go/`

- `--provider opencode_go` 时默认 `protocol=openai_compatible`。
- 默认 `base_url`：`https://opencode.ai/zen/go/v1`（不需要手动输入，运行时会自动拼接 `/chat/completions`）。
- 默认会透传 `prompt_cache_key`（对齐 opencode 的缓存键策略，提升提示词缓存命中稳定性）。
- 配置 API Key 后会调用 `https://opencode.ai/zen/go/v1/models` 获取可用模型并让你选择，不再要求手输模型名。
- 官方文档标记为 `/messages`（Anthropic 协议）的模型会在当前运行时自动隐藏（当前运行时仅走 `chat/completions`）。
- 官方模型列表端点：`https://opencode.ai/zen/go/v1/models`

## 3. 统一入口

推荐直接运行：

```powershell
python F:\novelist\novel_workflow.py
```

或双击：

```text
start_workflow.bat
```

可选 provider 参数：

```powershell
python F:\novelist\novel_workflow.py --provider opencode_go --protocol openai_compatible
```

统一入口可以识别：

- 原始小说 `.txt`
- `split_novel` 输出目录
- 已有工程目录
- 上述目录的父目录

交互式启动时，如果检测到未完成资料适配卷或未完成章节重写卷，会先询问继续哪个断点。旧工程里已经完成资料适配的卷会直接进入章节重写候选，不需要补额外规划文件。

## 4. 从原始小说开始

```powershell
python F:\novelist\novel_workflow.py "F:\books\我的小说.txt"
```

统一入口会依次执行：

1. 拆分章节。
2. 逐卷资料适配和卷资料审核。
3. 章节重写、组审查和卷级审核。

`split_novel` 默认最多 50 章一卷，同时估算 source bundle 字符数；超过 150k 字符预算时，会把卷尾章节顺延到后续卷。资料适配启动前也会检查当前未完成卷及后续卷，已完成卷被冻结，未完成卷会备份后重排。

## 5. 单独运行资料适配

```powershell
python -m novelist.workflows.novel_adaptation "F:\books\我的小说" --run-mode stage
```

`--run-mode`：

- `stage`：每次处理 1 卷。
- `book`：自动连续处理后续卷。

资料适配生成：

- `01_world_model.md`
- `02_style_guide.md`
- `03_book_outline.md`
- `04_foreshadowing.md`
- `<volume>_volume_outline.md`
- `<volume>_adaptation_review.md`
- `00_chapter_group_plan.md`

第 001 卷生成并定稿文风文档；后续卷只读取和审核这份文风文档。

## 6. 单独运行章节重写

```powershell
python -m novelist.workflows.novel_chapter_rewrite "F:\books\新书工程目录" --run-mode group
```

`--run-mode`：

- `group`：按当前章节组范围运行，组内逐章生成，组末做组审查。
- `volume`：跑完整卷，包含所有章节、所有组审查和卷级审核。

可配合：

```powershell
--volume 001 --chapter 0007
```

`--chapter` 会定位该章所在章节组；旧参数 `--run-mode chapter` 会兼容为 `group`。

章节组范围来源：

- 如果存在 `group_injection/<volume>_group_injection/00_chapter_group_plan.md`，按其中的 `chapter_numbers` 或 `chapter_count` 划分。该文件由资料适配审核通过后按源章节字符预算生成。
- 如果不存在该文件，按旧版节奏每 5 章一组。

章节组只负责范围和组审查，不替代单章章纲。每章仍读取当前章参考源、卷级注入和全局注入，按单章流程生成与审核。

## 7. 目录结构

资料适配：

```text
工程目录/
├─ 00_project_manifest.md
├─ global_injection/
│  ├─ 01_world_model.md
│  ├─ 02_style_guide.md
│  ├─ 03_book_outline.md
│  └─ 04_foreshadowing.md
└─ volume_injection/
   └─ 001_volume_injection/
      ├─ 001_volume_outline.md
      ├─ 001_adaptation_review.md
      ├─ 00_source_digest.md
      └─ 00_stage_manifest.md
```

章节重写：

```text
工程目录/
├─ 00_chapter_rewrite_manifest.md
├─ global_injection/
│  ├─ 05_character_status_cards.md
│  ├─ 06_character_relationship_graph.md
│  └─ 07_world_state.md
├─ volume_injection/
│  └─ 001_volume_injection/
│     ├─ 001_volume_plot_progress.md
│     └─ 001_volume_review.md
├─ group_injection/
│  └─ 001_group_injection/
│     ├─ 00_chapter_group_plan.md
│     └─ 0001_0005_group_injection/
│        ├─ 0001_0005_group_review.md
│        └─ 00_group_stage_manifest.md
└─ rewritten_novel/
   └─ 001/
      ├─ 0001.txt
      └─ ...
```

## 8. 断点续跑

- 资料适配断点由 `00_stage_manifest.md`、资料文档和项目 manifest 共同恢复。
- 章节重写断点由 `00_chapter_rewrite_manifest.md`、章节状态、组审查状态和卷审状态恢复。
- 统一入口会优先识别已有工程，避免重复拆分和重复生成。

## 9. dry-run

```powershell
python F:\novelist\novel_workflow.py "F:\books\新书工程目录" --dry-run
```

dry-run 只识别路径、卷状态、运行模式和待处理目标，不调用 API。
