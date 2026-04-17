# Novel Workflow Toolkit

一套面向中文网文改编工作流的命令行工具集合，覆盖：

- 原始小说按章节/分卷拆分
- 基于参考源逐卷生成改编规划文档
- 基于规划文档逐章生成仿写正文、状态文档与审核文档
- 统一入口调度完整工作流，并支持断点续跑

项目主要面向 Windows + PowerShell 使用场景，所有 CLI 都支持交互式运行。

## 组件一览

- [novelist/cli/split_novel.py](./novelist/cli/split_novel.py)
  把原始小说 `.txt` 按章节拆分，并按每 50 章归档到卷目录。
- [novelist/cli/novel_adaptation_cli.py](./novelist/cli/novel_adaptation_cli.py)
  读取 `split_novel` 输出后的书名目录，逐卷生成：
  - 全书大纲
  - 世界观设计
  - 文笔写作风格
  - 伏笔文档
  - 卷级大纲
- [novelist/cli/novel_chapter_rewrite_cli.py](./novelist/cli/novel_chapter_rewrite_cli.py)
  读取改编工程目录，逐章生成：
  - 章纲
  - 仿写正文
  - 人物状态卡 / 人物关系链
  - 卷级剧情进程 / 全局剧情进程
  - 世界模型 / 世界状态
  - 章级审核 / 组审查 / 卷级审核
- [novel_workflow_cli.py](./novel_workflow_cli.py)
- 一键启动脚本：[start_workflow.bat](./start_workflow.bat)
  统一入口，自动识别输入类型并串联以上三步。
- [novelist/core](./novelist/core)
  可复用核心模块，包括：
  - OpenAI / OpenAI Compatible 配置
  - Responses / Chat Completions 运行时
  - 文档写入与 patch 工具
  - 文件与路径工具
  - UI 输出

## 推荐用法

推荐直接从统一入口开始：

```powershell
python F:\novelist\novel_workflow_cli.py
```

也可以直接双击仓库根目录下的 `start_workflow.bat` 一键启动。

统一入口支持自动识别以下输入：

- 原始小说 `.txt`
- `split_novel` 产出的书名目录
- 已存在的工程目录
- 上述目录的父目录

启动后会先让你选择：

- 直接进入统一工作流
- 先重新配置 OpenAI 设置，再进入统一工作流
- 只重新配置 OpenAI 设置

如果当前项目里已经存在“已完成适配、但尚未完成章节重写”的卷，统一入口会优先续跑重写，而不会先逼你处理下一卷适配。

## 安装依赖

当前仓库没有单独的 `requirements.txt`，至少需要：

```powershell
pip install openai pydantic
```

如果你使用自己的兼容服务，请确保：

- 提供 OpenAI Compatible 接口
- 能处理 `chat.completions` + `tools`
- 或直接支持 OpenAI `Responses API`

## 快速开始

### 1. 从原始小说开始

```powershell
python F:\novelist\novel_workflow_cli.py "F:\books\我的小说.txt"
```

典型流程：

1. `novelist.cli.split_novel` 先拆分小说
2. `novelist.cli.novel_adaptation_cli` 生成逐卷改编规划
3. `novelist.cli.novel_chapter_rewrite_cli` 生成逐章正文与审核文档

### 2. 从已拆分好的目录开始

```powershell
python F:\novelist\novel_workflow_cli.py "F:\books\我的小说"
```

这里的 `F:\books\我的小说` 指的是 `split_novel` 输出的书名目录，里面通常直接包含：

- `001`
- `002`
- `003`

### 3. 从已有工程继续

```powershell
python F:\novelist\novel_workflow_cli.py "F:\books\新书工程目录"
```

统一入口会自动恢复：

- 已保存的 OpenAI 设置
- 已保存的输入路径
- 已完成/未完成的卷状态
- 已适配但未完成重写的积压卷

## 主要目录结构

### 仓库代码结构

```text
仓库根目录/
├─ novel_workflow_cli.py      # 根目录统一入口包装脚本
├─ start_workflow.bat         # Windows 一键启动脚本
├─ novelist/
│  ├─ cli/                    # 业务 CLI
│  │  ├─ split_novel.py
│  │  ├─ novel_adaptation_cli.py
│  │  ├─ novel_chapter_rewrite_cli.py
│  │  └─ novel_workflow_cli.py
│  └─ core/                   # 可复用核心模块
├─ docs/
└─ tests/
```

如果你要查看或编辑源码，请优先打开 `novelist/cli/...` 和 `novelist/core/...`，而不是旧的根目录 `core/...` 路径。

### `split_novel` 输出

```text
书名/
├─ 001/
│  ├─ 书名.txt        # 简介
│  ├─ 0001.txt
│  ├─ 0002.txt
│  └─ ...
├─ 002/
└─ ...
```

### `novel_adaptation_cli` 工程输出

```text
工程目录/
├─ 00_project_manifest.md
├─ global_injection/
│  ├─ 01_book_outline.md
│  ├─ 02_world_design.md
│  ├─ 03_style_guide.md
│  └─ 04_foreshadowing.md
└─ volume_injection/
   ├─ 001_volume_injection/
   │  ├─ 001_volume_outline.md
   │  ├─ 00_source_digest.md
   │  └─ 00_stage_manifest.md
   └─ 002_volume_injection/
```

### `novel_chapter_rewrite_cli` 追加输出

```text
工程目录/
├─ 00_chapter_rewrite_manifest.md
├─ global_injection/
│  ├─ 05_character_status_cards.md
│  ├─ 06_character_relationship_graph.md
│  ├─ 07_global_plot_progress.md
│  ├─ 08_world_model.md
│  └─ 09_world_state.md
├─ volume_injection/
│  └─ 001_volume_injection/
│     ├─ 001_volume_plot_progress.md
│     ├─ 001_volume_review.md
│     └─ 0001_chapter_outline/
│        ├─ 0001_chapter_outline.md
│        ├─ 0001_chapter_review.md
│        └─ 00_stage_manifest.md
├─ group_injection/
│  └─ 001_group_injection/
│     └─ 0001_0005_group_injection/
│        └─ 0001_0005_group_review.md
└─ rewritten_novel/
   └─ 001/
      ├─ 0001.txt
      ├─ 0002.txt
      └─ ...
```

## 运行模式

### `novel_adaptation_cli`

- `stage`
  每次处理 1 卷
- `book`
  自动连续处理后续卷

### `novel_chapter_rewrite_cli`

- `chapter`
  按章节推进
- `group`
  按 5 章一组推进，并包含组审查
- `volume`
  跑完整卷，包含章审、组审查、卷审查

### 统一入口

统一入口会分别让你选择：

- `novel_adaptation_cli` 的运行方式
- `novel_chapter_rewrite_cli` 的运行方式

并在流程完成后回到启动菜单，允许继续下一轮工作。

## OpenAI 与兼容协议

项目支持：

- OpenAI 官方
- OpenAI Compatible（自定义 `base_url`）

支持协议：

- OpenAI Responses API
- OpenAI Compatible（兼容 OpenAI 接口）

重新配置后会记住：

- provider
- protocol
- base_url
- api_key
- model

全局配置默认保存在：

```text
%USERPROFILE%\.novel_adaptation_cli\config.json
```

## 常见命令

### 只拆分小说

```powershell
python -m novelist.cli.split_novel "F:\books\我的小说.txt"
```

### 只跑卷级改编

```powershell
python -m novelist.cli.novel_adaptation_cli "F:\books\我的小说"
```

### 只跑章节重写

```powershell
python -m novelist.cli.novel_chapter_rewrite_cli "F:\books\新书工程目录"
```

### 统一入口 dry-run

```powershell
python F:\novelist\novel_workflow_cli.py "F:\books\新书工程目录" --dry-run
```

## 详细教程

更完整的操作步骤、断点续跑说明、目录结构解释、配置与常见问题，请看：

- [docs/USAGE.md](./docs/USAGE.md)

## 许可证

本项目使用 [MIT License](./LICENSE)。
