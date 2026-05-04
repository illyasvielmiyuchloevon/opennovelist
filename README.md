# Novel Workflow Toolkit

一套面向中文网文改编的命令行工作流工具，覆盖原文拆分、卷资料适配、逐章仿写、章节组审查、卷级审核和统一入口续跑。

项目主要面向 Windows + PowerShell 使用，所有入口都支持交互式运行。

## 组件

- [novelist/workflows/split_novel.py](./novelist/workflows/split_novel.py)
  把原始小说 `.txt` 拆成章节文件，并按参考源注入预算自适应分卷。
- [novelist/workflows/novel_adaptation.py](./novelist/workflows/novel_adaptation.py)
  兼容入口；主要实现位于 `novelist/workflows/adaptation/`。逐卷生成世界模型、文风、全书大纲、伏笔管理和卷级大纲，并在卷资料审核通过后写入轻量章节组范围计划、把该卷记为已完成。
- [novelist/workflows/novel_chapter_rewrite.py](./novelist/workflows/novel_chapter_rewrite.py)
  兼容入口；主要实现位于 `novelist/workflows/chapter_rewrite/`。章节组模式只决定本轮处理范围，组内仍逐章执行旧版单章工作流：章纲、正文、配套状态文档、章级审核，然后再做组审查或卷级审核。
- [novel_workflow.py](./novel_workflow.py)
  根目录统一入口，自动识别输入并串联拆分、资料适配和章节重写。
- [start_workflow.bat](./start_workflow.bat)
  Windows 一键启动脚本。

## 推荐用法

```powershell
python F:\novelist\novel_workflow.py
```

统一入口支持自动识别：

- 原始小说 `.txt`
- `split_novel` 产出的书名目录
- 已存在的工程目录
- 上述目录的父目录

如果工程中存在未完成资料适配卷，或已适配但未完成章节重写的卷，交互式启动会先询问继续哪个断点。旧工程里已经写入 `processed_volumes` 的卷不会再被要求补额外规划文件，也不会因为章节工作流启动而触发参考源重分卷。

## 依赖

```powershell
pip install openai pydantic
```

项目支持 OpenAI Responses API，也支持 OpenAI Compatible 服务。全局配置默认保存在：

```text
%USERPROFILE%\.novel_adaptation\config.json
```

旧配置 `%USERPROFILE%\.novel_adaptation_cli\config.json` 会在首次读取时自动迁移。

## 典型流程

从原始小说开始：

```powershell
python F:\novelist\novel_workflow.py "F:\books\我的小说.txt"
```

流程为：

1. `split_novel` 拆分章节，并按 150k source bundle 字符预算自适应分卷。
2. `novel_adaptation` 逐卷生成资料文档并完成卷资料审核。
3. `novel_chapter_rewrite` 按章节组范围逐章生成正文与审核文档。

## 工程输出结构

资料适配输出：

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

章节重写追加输出：

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
│     ├─ 00_chapter_group_plan.md      # 动态章节组范围来源
│     └─ 0001_0005_group_injection/
│        ├─ 0001_0005_group_review.md
│        └─ 00_group_stage_manifest.md
└─ rewritten_novel/
   └─ 001/
      ├─ 0001.txt
      ├─ 0002.txt
      └─ ...
```

`00_chapter_group_plan.md` 只记录章节组范围，由资料适配审核通过后按源章节字符预算生成。没有该文件时，章节重写会按旧版 5 章一组的节奏运行组审查。

## 运行模式

`novel_adaptation`：

- `stage`：每次处理 1 卷。
- `book`：自动连续处理后续卷。

`novel_chapter_rewrite`：

- `group`：处理当前章节所在组，组内逐章跑完整单章工作流，组末做组审查。
- `volume`：跑完整卷，包含所有单章工作流、组审查和卷级审核。

旧参数 `chapter` 会兼容为 `group`。

## 常见命令

```powershell
python -m novelist.workflows.split_novel "F:\books\我的小说.txt"
python -m novelist.workflows.novel_adaptation "F:\books\我的小说"
python -m novelist.workflows.novel_chapter_rewrite "F:\books\新书工程目录"
python F:\novelist\novel_workflow.py "F:\books\新书工程目录" --dry-run
```

更详细说明见：

- [docs/USAGE.md](./docs/USAGE.md)
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)

## License

[MIT License](./LICENSE)
