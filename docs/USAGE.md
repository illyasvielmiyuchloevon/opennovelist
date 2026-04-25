# 使用说明与完整教程

本文档面向实际操作者，按“从 0 到完整工作流”的顺序介绍这个仓库的使用方法。

## 1. 这个项目能做什么

这套工具把小说工作流拆成三层：

1. `novelist.workflows.split_novel`
   把一整本小说按章节拆开，并按每 50 章自动分卷。
2. `novelist.workflows.novel_adaptation`
   基于参考源逐卷生成改编规划文档。
3. `novelist.workflows.novel_chapter_rewrite`
   基于规划文档逐章生成仿写正文和配套审核文档。

如果你不想手动串联，可以直接使用：

4. `novel_workflow.py`
   统一入口，自动调度以上三步。

## 2. 运行前准备

### 2.0 仓库代码位置说明

如果你是直接使用工具，可以只关心根目录这两个入口：

- `novel_workflow.py`
- `start_workflow.bat`

如果你要查看或修改源码，现在的代码结构是：

```text
novelist/
├─ workflows/
│  ├─ novel_adaptation.py       # 兼容入口 facade
│  ├─ novel_chapter_rewrite.py  # 兼容入口 facade
│  ├─ novel_workflow.py         # 兼容入口 facade
│  ├─ adaptation/               # 资料适配内部实现
│  ├─ chapter_rewrite/          # 章节重写内部实现
│  └─ unified/                  # 统一入口内部实现
└─ core/   # 可复用核心模块
```

也就是说，源码路径应优先看：

- `novelist/workflows/adaptation/...`
- `novelist/workflows/chapter_rewrite/...`
- `novelist/workflows/unified/...`
- `novelist/core/...`

`novelist/workflows/novel_*.py` 仍然可以运行，也仍然兼容旧导入；它们现在只是薄入口层。

### 2.1 Python 依赖

至少安装：

```powershell
pip install openai pydantic
```

### 2.2 你需要准备的东西

- 原始小说 `.txt`，或者已经拆分好的书名目录
- 可用的 LLM 接口：
  - OpenAI 官方
  - 或 OpenAI Compatible 服务
- 对应的：
  - API Key
  - base_url
  - model

## 3. 最推荐的用法：统一入口

直接运行：

```powershell
python F:\novelist\novel_workflow.py
```

或者直接双击仓库根目录下的 `start_workflow.bat`。

启动后会先询问：

### 3.1 启动方式

- `直接进入统一工作流`
- `先重新配置 OpenAI 设置，再进入统一工作流`
- `只重新配置 OpenAI 设置`

如果你是第一次运行，推荐先选：

- `先重新配置 OpenAI 设置，再进入统一工作流`

### 3.2 配置提供商与协议

统一入口会让你依次选择：

- API 提供商
  - `OpenAI 官方`
  - `OpenAI Compatible（兼容提供商 / 自定义服务）`
- 协议
  - `OpenAI Responses API`
  - `OpenAI Compatible（兼容 OpenAI 接口）`

然后输入：

- `API Key`
- `base_url`
- `model`

这些设置会保存到：

```text
%USERPROFILE%\.novel_adaptation\config.json
```

如果旧版本已经保存过 `%USERPROFILE%\.novel_adaptation_cli\config.json`，新入口会在首次读取配置时自动迁移到新目录。

下次会自动复用。

## 4. 输入路径支持什么

统一入口支持以下输入：

- 原始小说 `.txt`
- `split_novel` 输出后的书名目录
- 已有工程目录
- 上述目录的父目录

例如你只输入：

```text
F:\huoying
```

统一入口会自动尝试从里面识别：

- 原始小说 `.txt`
- `split_novel` 产物
- 已存在工程

如果同级有多个候选，它会按优先级自动选择：

1. 已有工程目录
2. `split_novel` 的书名目录
3. 原始小说 `.txt`

## 5. 从原始小说开始的完整流程

### 5.1 启动统一入口

```powershell
python F:\novelist\novel_workflow.py "F:\books\我的小说.txt"
```

### 5.2 统一入口自动执行的三步

#### 第一步：拆分小说

统一入口会调用 `novelist.workflows.split_novel`：

- 识别章节标题
- 按章拆分为：
  - `0001.txt`
  - `0002.txt`
  - ...
- 每 50 章放进一个卷目录：
  - `001`
  - `002`
  - ...
- 首章前的内容会作为“简介”单独写入 `001` 卷，文件名就是书名本身

#### 第二步：逐卷改编规划

统一入口会调用 `novelist.workflows.novel_adaptation`，逐卷生成：

- 世界观设计
- 世界模型
- 文笔写作风格
- 全书大纲
- 伏笔文档
- 全书故事线蓝图
- 卷级大纲

#### 第三步：逐章重写

统一入口会调用 `novelist.workflows.novel_chapter_rewrite`，生成：

- 章纲
- 仿写正文
- 状态类文档
- 章级审核
- 组审查
- 卷级审核

## 6. 也可以单独运行三个工作流入口

### 6.1 单独拆分小说

```powershell
python -m novelist.workflows.split_novel "F:\books\我的小说.txt"
```

### 6.2 单独跑改编规划

输入必须是 `split_novel` 输出后的书名目录，或者已有工程目录：

```powershell
python -m novelist.workflows.novel_adaptation "F:\books\我的小说"
```

支持的核心参数：

```powershell
python -m novelist.workflows.novel_adaptation "F:\books\我的小说" `
  --new-title "玄幻忍者" `
  --target-worldview "玄幻修仙" `
  --run-mode stage
```

`--run-mode` 支持：

- `stage`
- `book`

### 6.3 单独跑章节重写

输入必须是已有工程目录，或者可解析到已有工程的来源目录：

```powershell
python -m novelist.workflows.novel_chapter_rewrite "F:\books\玄幻忍者"
```

支持的核心参数：

```powershell
python -m novelist.workflows.novel_chapter_rewrite "F:\books\玄幻忍者" `
  --run-mode group `
  --volume 001
```

`--run-mode` 支持：

- `chapter`
- `group`
- `volume`

## 7. 统一入口的运行方式说明

统一入口会分别询问两件事：

### 7.1 `novel_adaptation` 的运行方式

- `按阶段运行`
  - 一次处理 1 卷
- `按全书运行`
  - 自动连续处理后续卷

### 7.2 `novel_chapter_rewrite` 的运行方式

- `按章节运行`
- `按组运行`
- `按卷运行`

## 8. 断点续跑机制

这是这个项目里非常重要的一部分。

### 8.1 改编规划的断点

`novelist.workflows.novel_adaptation` 会在工程目录里维护：

- `00_project_manifest.md`

里面会记录：

- 已处理卷
- 最后处理卷
- 来源目录
- 新书名
- 世界观

### 8.2 章节重写的断点

`novelist.workflows.novel_chapter_rewrite` 会维护：

- `00_chapter_rewrite_manifest.md`

里面会记录：

- 已通过卷
- 最后处理卷 / 章
- 每章状态
- 每组组审查状态
- 卷级审核状态

### 8.3 统一入口的断点

统一入口现在会优先检测：

- 有没有“资料适配尚未完成”的卷
- 有没有“已经完成适配，但尚未完成章节重写”的卷

如果有，并且是交互式启动、没有显式传入路径、没有通过 `--skip-adaptation` / `--skip-rewrite` 指定阶段：

- 会先列出可继续的断点：资料适配断点、章节重写断点
- 选择继续资料适配断点时，会从第一个未适配卷继续 `novel_adaptation`
- 选择继续章节重写断点时，会续跑这些卷的章节重写
- 选择重新选择时，可以选择完整流程、只跑资料适配、只跑章节重写

如果是显式传入路径或非交互运行：

- 仍保持旧行为，有章节重写断点时自动优先续跑章节重写
- 如果只有资料适配断点，会按完整流程继续
- 不会用新菜单打断脚本自动化

这能避免：

- 上次适配做了一半
- 这次只是想继续当前卷重写
- 却被自动推进到下一卷适配

同时也允许你在重启后直接续跑资料适配，不会被固定带进 `novel_chapter_rewrite`。

## 9. 目录结构详解

### 9.1 `split_novel` 输出

```text
书名/
├─ 001/
│  ├─ 书名.txt
│  ├─ 0001.txt
│  ├─ 0002.txt
│  └─ ...
├─ 002/
└─ ...
```

### 9.2 工程目录

```text
工程目录/
├─ 00_project_manifest.md
├─ 00_chapter_rewrite_manifest.md
├─ global_injection/
├─ volume_injection/
├─ group_injection/
└─ rewritten_novel/
```

### 9.3 `global_injection`

全局长期注入文档，典型包括：

- `01_world_design.md`
- `02_world_model.md`
- `03_style_guide.md`
- `04_book_outline.md`
- `05_foreshadowing.md`
- `06_storyline_blueprint.md`
- `07_character_status_cards.md`
- `08_character_relationship_graph.md`
- `09_world_state.md`

### 9.4 `volume_injection`

每卷一个目录：

```text
volume_injection/
└─ 001_volume_injection/
   ├─ 001_volume_outline.md
   ├─ 001_volume_plot_progress.md
   ├─ 001_volume_review.md
   ├─ 00_source_digest.md
   ├─ 00_stage_manifest.md
   └─ 0001_chapter_outline/
      ├─ 0001_chapter_outline.md
      ├─ 0001_chapter_review.md
      └─ 00_stage_manifest.md
```

### 9.5 `group_injection`

每 5 章一组的组审查文档：

```text
group_injection/
└─ 001_group_injection/
   └─ 0001_0005_group_injection/
      └─ 0001_0005_group_review.md
```

### 9.6 `rewritten_novel`

最终仿写正文：

```text
rewritten_novel/
└─ 001/
   ├─ 0001.txt
   ├─ 0002.txt
   └─ ...
```

## 10. 文档更新策略

这个仓库不是简单地每次“整篇重写”文档。

当前设计是：

- 新建文档时允许整篇写入
- 已存在的长期知识文档，默认优先 patch
- 尽量保留未变化内容
- 只有必要时才更新对应文档

特别是这些文档：

- 人物状态卡
- 人物关系链
- 全书故事线蓝图
- 卷级剧情进程
- 世界模型
- 世界状态

都已经偏向“按需更新、增量 patch”的工作流。

其中：

- `全书故事线蓝图`
  现在由 `novelist.workflows.novel_adaptation` 按故事线与分卷蓝图区块增量维护，已处理卷区块不会被后续卷摘要化替换
- `人物状态卡 / 人物关系链 / 世界状态`
  仍由 `novelist.workflows.novel_chapter_rewrite` 在章节流程中按需维护

## 11. 常见工作方式示例

### 11.1 从零开始跑完整流程

```powershell
python F:\novelist\novel_workflow.py "F:\books\我的小说.txt"
```

### 11.2 已经拆分完成，从书名目录开始

```powershell
python F:\novelist\novel_workflow.py "F:\books\我的小说"
```

### 11.3 已经有工程，只继续跑章节重写

```powershell
python F:\novelist\novel_workflow.py "F:\books\玄幻忍者" --skip-adaptation
```

### 11.4 只做配置，不进工作流

```powershell
python F:\novelist\novel_workflow.py --startup-mode configure_only
```

### 11.5 统一入口 dry-run

```powershell
python F:\novelist\novel_workflow.py "F:\books\玄幻忍者" --dry-run
```

## 12. OpenAI 与 OpenAI Compatible

### 12.1 官方 OpenAI

如果你使用官方 OpenAI，优先推荐：

- 提供商：`OpenAI 官方`
- 协议：`OpenAI Responses API`

### 12.2 OpenAI Compatible

如果你使用第三方兼容服务：

- 提供商：`OpenAI Compatible`
- 协议：`OpenAI Compatible`

项目运行时已经兼容了：

- 流式 `chat.completions`
- `tool_calls`
- 旧版 `function_call`
- 不同 `tool_choice` 形状回退
- token usage 输出；服务端返回 usage 时会显示发送、接收、缓存命中、缓存写入和推理 token

如果兼容服务本身不稳定，仍然可能出现：

- `500`
- `Database error`
- 网关断连

这时通常不是本地解析问题，而是兼容服务端本身的问题。

## 13. 排错建议

### 13.1 无法识别输入路径

请确认输入的是以下三类之一：

- 原始小说 `.txt`
- `split_novel` 输出的书名目录
- 已有工程目录

### 13.2 想重新配置 API

直接运行：

```powershell
python F:\novelist\novel_workflow.py
```

然后选择：

- `先重新配置 OpenAI 设置，再进入统一工作流`
或
- `只重新配置 OpenAI 设置`

### 13.3 某章/某卷失败

优先查看这些文件：

- `00_stage_manifest.md`
- `00_source_digest.md`
- `00_last_response_debug.md`
- `00_volume_review_debug.md`

### 13.4 工程中断后如何继续

直接重新运行统一入口或对应子流程：

```powershell
python F:\novelist\novel_workflow.py
```

项目会自动读取：

- `00_project_manifest.md`
- `00_chapter_rewrite_manifest.md`

并尽量恢复到上一次工作位置。

## 14. 建议的日常使用方式

如果你是长期使用，推荐这个习惯：

1. 平时只运行统一入口
2. 让统一入口负责识别当前项目状态
3. 只在需要单独调试某一步时再运行单独工作流入口
4. 定期检查：
   - `global_injection`
   - `volume_injection`
   - `rewritten_novel`
   - manifest 文件

## 15. 许可证

本项目使用 MIT License：

- [LICENSE](../LICENSE)
