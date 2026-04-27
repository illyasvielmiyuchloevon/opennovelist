from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from novelist.workflows import novel_adaptation as adaptation_workflow
from novelist.workflows.adaptation import document_generation as adaptation_document_generation_module
from novelist.workflows.adaptation import review as adaptation_review_module

ORIGINAL_ADAPTATION_CALL_DOCUMENT_OPERATION_RESPONSE = adaptation_document_generation_module.call_document_operation_response
ORIGINAL_ADAPTATION_APPLY_DOCUMENT_OPERATION_WITH_REPAIR = adaptation_document_generation_module.apply_document_operation_with_repair


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest(project_root: Path) -> dict[str, object]:
    return {
        "version": 1,
        "source_root": str(project_root / "source"),
        "project_root": str(project_root),
        "new_book_title": "仙道至尊",
        "target_worldview": "玄幻世界观",
        "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
        "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
        "total_volumes": 2,
        "processed_volumes": [],
        "last_processed_volume": None,
    }


def _volume_material(volume_number: str = "001") -> dict[str, object]:
    return {
        "volume_number": volume_number,
        "volume_dir": f"F:/source/{volume_number}",
        "chapters": [],
        "extras": [],
    }


def _seed_adaptation_docs(project_root: Path, volume_number: str = "001") -> dict[str, Path]:
    paths = adaptation_workflow.stage_paths(project_root, volume_number)
    for doc_key in adaptation_workflow.GLOBAL_INJECTION_DOC_ORDER:
        _write_text(paths[doc_key], f"{adaptation_workflow.adaptation_doc_label(doc_key)}：源人物名仍残留。\n")
    _write_text(paths["volume_outline"], "卷级大纲：源人物名仍残留。\n")
    return paths


def _agent_stage_result(
    payload: adaptation_workflow.WorkflowSubmissionPayload,
    response_id: str,
    response_ids: list[str] | None = None,
) -> Mock:
    return Mock(
        submission=payload,
        response_id=response_id,
        response_ids=response_ids or [response_id],
        applications=[],
    )


class AdaptationDocumentPlanTests(unittest.TestCase):
    def test_first_volume_plan_uses_new_generation_order(self) -> None:
        plan = adaptation_workflow.build_document_plan("001")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "volume_outline",
            ],
        )

    def test_later_volume_plan_uses_new_generation_order(self) -> None:
        plan = adaptation_workflow.build_document_plan("002")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "world_model",
                "book_outline",
                "foreshadowing",
                "volume_outline",
            ],
        )


class AdaptationContextSummaryTests(unittest.TestCase):
    def test_generation_payload_does_not_duplicate_target_global_docs_as_existing_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = adaptation_workflow.stage_paths(project_root, "007")
            current_docs = {
                "world_model": "世界模型正文",
                "style_guide": "文风正文",
                "book_outline": "全书大纲正文",
                "foreshadowing": "伏笔正文",
            }
            payload = adaptation_workflow.build_adaptation_generation_agent_request(
                manifest=_manifest(project_root),  # type: ignore[arg-type]
                volume_material=_volume_material("007"),  # type: ignore[arg-type]
                paths=paths,
                document_plan=adaptation_workflow.build_document_plan("007"),
                current_docs=current_docs,
            )

        self.assertEqual(payload["existing_global_docs"], {"style_guide": "文风正文"})
        target_contents = {
            item["file_key"]: item["current_content"]
            for item in payload["target_files"]
        }
        self.assertEqual(target_contents["world_model"], "世界模型正文")
        self.assertEqual(target_contents["book_outline"], "全书大纲正文")
        self.assertEqual(target_contents["foreshadowing"], "伏笔正文")

    def test_payload_input_summary_uses_actual_payload_content_lengths(self) -> None:
        payload = {
            "existing_global_docs": {
                "world_model": "世界模型正文",
            },
            "target_files": [
                {
                    "file_key": "world_model",
                    "label": "世界模型文档",
                    "file_name": "01_world_model.md",
                    "file_path": "F:/project/global_injection/01_world_model.md",
                    "current_content": "已有世界模型",
                    "current_char_count": 999,
                    "preferred_mode": "edit_or_patch",
                }
            ],
            "adaptation_documents": [
                {
                    "file_key": "volume_outline",
                    "label": "卷级大纲",
                    "file_name": "001_volume_outline.md",
                    "file_path": "F:/project/volume_injection/001_volume_outline.md",
                    "current_content": "卷纲正文",
                    "current_char_count": 888,
                }
            ],
        }

        lines = adaptation_workflow.adaptation_payload_input_summary_lines(payload)
        joined = "\n".join(lines)

        self.assertIn("既有全局资料输入：世界模型", joined)
        self.assertIn("字符数约 6", joined)
        self.assertIn("生成目标文件当前内容：世界模型文档", joined)
        self.assertIn("字符数约 6，建议工具=edit_or_patch", joined)
        self.assertIn("卷资料审核文档输入：卷级大纲", joined)
        self.assertIn("字符数约 4", joined)
        self.assertNotIn("字符数约 999", joined)
        self.assertNotIn("字符数约 888", joined)

    def test_source_summary_groups_files_and_keeps_volume_char_count(self) -> None:
        volume_material = {
            "volume_number": "001",
            "chapters": [
                {"chapter_number": "0001", "file_name": "0001.txt", "file_path": "F:/source/001/0001.txt", "source_title": "第一章", "text": "章节正文"},
            ],
            "extras": [
                {"file_name": "intro.md", "file_path": "F:/source/001/intro.md", "label": "intro", "text": "序言"},
            ],
        }
        loaded_files = [
            {"type": "extra", "file_name": "intro.md", "file_path": "F:/source/001/intro.md", "char_count": 2},
            {"type": "chapter", "file_name": "0001.txt", "file_path": "F:/source/001/0001.txt", "chapter_number": "0001", "source_title": "第一章", "char_count": 4},
        ]

        lines = adaptation_workflow.adaptation_source_file_summary_lines(
            volume_material,  # type: ignore[arg-type]
            loaded_files,  # type: ignore[arg-type]
            source_char_count=42,
        )
        joined = "\n".join(lines)

        self.assertIn("source bundle 字符数约 42", joined)
        self.assertIn("参考源文件原文字符数合计约 6", joined)
        self.assertIn("补充源文件[1]：intro.md", joined)
        self.assertIn("章节源文件[1]：0001.txt", joined)
        self.assertNotIn("F:/source/001/0001.txt", joined)
        self.assertNotIn("标题：第一章", joined)


class SourceContaminationGuardrailTests(unittest.TestCase):
    def test_common_stage_instructions_forbid_source_names_and_discourse_system(self) -> None:
        instructions = adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS

        self.assertIn("严禁把参考源的人名、地名、姓氏、势力名、事件名、专用术语", instructions)
        self.assertIn("话语体系", instructions)
        self.assertIn("转换成新书自己的命名、设定与表达", instructions)
        self.assertIn("按真实需要编写", instructions)
        self.assertIn("不要为了显得完整、填满结构或覆盖全部素材而硬塞内容", instructions)

    def test_adaptation_review_and_fix_reuse_document_stage_instructions(self) -> None:
        document_instructions = adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS
        review_instructions = adaptation_workflow.COMMON_ADAPTATION_REVIEW_INSTRUCTIONS
        fix_instructions = adaptation_workflow.COMMON_ADAPTATION_REVIEW_FIX_INSTRUCTIONS

        self.assertEqual(review_instructions, document_instructions)
        self.assertEqual(fix_instructions, document_instructions)
        self.assertIn(adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME, review_instructions)
        self.assertIn("Dynamic Request", review_instructions)
        self.assertIn("adaptation_volume_review", review_instructions)
        self.assertIn("write/edit/patch", review_instructions)

    def test_adaptation_review_appends_workflow_tool_after_document_tools(self) -> None:
        result = adaptation_workflow.llm_runtime.MultiFunctionToolResult(
            tool_name=adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
            parsed=adaptation_workflow.WorkflowSubmissionPayload(passed=True, review_md="通过。"),
            response_id="resp_review",
            status="completed",
            output_types=["function_call"],
            preview="review",
            raw_body_text="",
            raw_json={},
        )

        with patch.object(adaptation_review_module.llm_runtime, "call_function_tools", return_value=result) as call_tools:
            review, response_id, _ = adaptation_review_module.call_adaptation_review_response(
                client=Mock(),
                model="test-model",
                instructions=adaptation_workflow.COMMON_ADAPTATION_REVIEW_INSTRUCTIONS,
                user_input="shared prompt\n{}",
                previous_response_id="resp_docs",
                prompt_cache_key="cache-key",
            )

        tool_names = [spec.name for spec in call_tools.call_args.kwargs["tool_specs"]]
        self.assertEqual(
            tool_names,
            [
                adaptation_workflow.document_ops.DOCUMENT_WRITE_TOOL_NAME,
                adaptation_workflow.document_ops.DOCUMENT_EDIT_TOOL_NAME,
                adaptation_workflow.document_ops.DOCUMENT_PATCH_TOOL_NAME,
                adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
            ],
        )
        self.assertTrue(review.passed)
        self.assertEqual(response_id, "resp_review")
        self.assertEqual(call_tools.call_args.kwargs["previous_response_id"], "resp_docs")
        self.assertEqual(
            call_tools.call_args.kwargs["tool_choice"],
            {"type": "function", "name": adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME},
        )

    def test_document_generation_uses_same_stage_tool_prefix(self) -> None:
        write_payload = adaptation_workflow.document_ops.DocumentWritePayload(
            files=[
                adaptation_workflow.document_ops.DocumentWriteFile(
                    file_key="world_model",
                    content="世界模型正文。",
                )
            ]
        )
        result = adaptation_workflow.llm_runtime.MultiFunctionToolResult(
            tool_name=adaptation_workflow.document_ops.DOCUMENT_WRITE_TOOL_NAME,
            parsed=write_payload,
            response_id="resp_world_model",
            status="completed",
            output_types=["function_call"],
            preview="write",
            raw_body_text="",
            raw_json={},
        )

        with patch.object(adaptation_document_generation_module.llm_runtime, "call_function_tools", return_value=result) as call_tools:
            operation, response_id = ORIGINAL_ADAPTATION_CALL_DOCUMENT_OPERATION_RESPONSE(
                client=Mock(),
                model="test-model",
                instructions=adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
                user_input="shared prompt\n{}",
                previous_response_id=None,
                prompt_cache_key="cache-key",
            )

        self.assertEqual(operation.mode, "write")
        self.assertEqual(response_id, "resp_world_model")
        self.assertEqual(
            [spec.name for spec in call_tools.call_args.kwargs["tool_specs"]],
            [
                adaptation_workflow.document_ops.DOCUMENT_WRITE_TOOL_NAME,
                adaptation_workflow.document_ops.DOCUMENT_EDIT_TOOL_NAME,
                adaptation_workflow.document_ops.DOCUMENT_PATCH_TOOL_NAME,
                adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
            ],
        )
        self.assertEqual(call_tools.call_args.kwargs["tool_choice"], "auto")

    def test_adaptation_stage_exposes_only_unified_four_tools(self) -> None:
        tool_names = [spec.name for spec in adaptation_workflow.adaptation_stage_tool_specs()]

        self.assertEqual(
            tool_names,
            [
                adaptation_workflow.document_ops.DOCUMENT_WRITE_TOOL_NAME,
                adaptation_workflow.document_ops.DOCUMENT_EDIT_TOOL_NAME,
                adaptation_workflow.document_ops.DOCUMENT_PATCH_TOOL_NAME,
                adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
            ],
        )
        self.assertNotIn("submit_adaptation_review", tool_names)

    def test_stage_shared_prompt_contains_source_contamination_guardrails(self) -> None:
        prompt = adaptation_workflow.build_stage_shared_prompt(
            manifest=_manifest(Path("F:/project")),
            volume_material=_volume_material("001"),
            loaded_files=[],
            source_bundle="",
            source_char_count=0,
        )

        self.assertIn("严禁把参考源的人名", prompt)
        self.assertIn("话语体系", prompt)
        self.assertIn("功能映射", prompt)
        self.assertIn("资料适配不是从当前卷原文抽取百科资料", prompt)
        self.assertIn("完整卷原文只提供判断依据", prompt)
        self.assertIn("不得写入全局资料", prompt)
        self.assertIn("所有资料都按需要编写", prompt)
        self.assertIn("没有实际用途的信息应当不写、不新增", prompt)
        self.assertIn("不要为了显得完整、填满结构或覆盖全部素材而硬塞内容", prompt)

    def test_document_request_contains_explicit_source_material_boundary(self) -> None:
        for doc_key in [
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "volume_outline",
        ]:
            with self.subTest(doc_key=doc_key):
                request = adaptation_workflow.build_document_request(doc_key)
                boundary = request["source_material_boundary"]
                self.assertIn("参考源只提供", boundary["core_boundary"])
                self.assertIn("参考源功能 -> 新书设计", boundary["mapping_required"])
                self.assertIn("不得把原书人物", "\n".join(boundary["hard_ban"]))
                self.assertIn("新书设定主体必须使用新书自己的姓名", "\n".join(boundary["required_conversion"]))

    def test_every_adaptation_document_generation_payload_has_source_contamination_guardrails(self) -> None:
        captured: dict[str, dict[str, object]] = {}

        def fake_call(*args, **kwargs):
            payload = adaptation_workflow.json.loads(str(args[3]))
            captured[payload["document_request"]["doc_key"]] = payload
            return (object(), None)

        manifest = {
            "new_book_title": "测试书",
            "target_worldview": "测试世界",
            "total_volumes": 2,
            "processed_volumes": ["001"],
            "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
            "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
        }
        current_docs = {
            "style_guide": "文风",
            "book_outline": "全书大纲",
            "world_design": "世界观设计",
            "world_model": "世界模型",
            "foreshadowing": "伏笔管理",
        }

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            for doc_key in [
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "volume_outline",
            ]:
                adaptation_workflow.generate_document_operation(
                    client=None,  # type: ignore[arg-type]
                    model="gpt-test",
                    manifest=manifest,
                    volume_material={"volume_number": "002", "chapters": [], "extras": []},
                    current_docs=current_docs,
                    doc_key=doc_key,
                    output_path=Path(f"F:/novelist/.tmp_{doc_key}_test.md"),
                    stage_shared_prompt="",
                    previous_response_id=None,
                    prompt_cache_key="test-cache-key",
                )

        self.assertEqual(
            set(captured),
            {"world_model", "style_guide", "book_outline", "foreshadowing", "volume_outline"},
        )
        for doc_key, payload in captured.items():
            requirements = "\n".join(payload["requirements"])
            boundary_text = adaptation_workflow.json.dumps(payload["source_material_boundary"], ensure_ascii=False)
            with self.subTest(doc_key=doc_key):
                self.assertEqual(list(payload.keys())[-1], "latest_work_target")
                self.assertEqual(
                    payload["latest_work_target"]["forbidden_tool"],
                    adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
                )
                self.assertIn("最新工作目标", payload["latest_work_target"]["instruction"])
                self.assertIn("write/edit/patch", payload["latest_work_target"]["instruction"])
                self.assertIn("不要调用 submit_workflow_result", payload["latest_work_target"]["instruction"])
                self.assertIn("参考源只提供", boundary_text)
                self.assertIn("不是新书资料正文", boundary_text)
                self.assertIn("参考源功能 -> 新书设计", boundary_text)
                self.assertIn("不得把原书人物", boundary_text)
                self.assertIn("严禁把参考源的人名", requirements)
                self.assertIn("绝不能把原书内容直接写成新书内容", requirements)
                self.assertIn("势力名", requirements)
                self.assertIn("等级名", requirements)
                self.assertIn("话语体系", requirements)
                self.assertIn("新命名、新术语和新表达", requirements)
                self.assertIn("不得把原作实体名保留为新书实体", requirements)
                self.assertIn("残留参考源实体名或话语体系", requirements)
                self.assertIn("明确标注为参考源功能映射或参考源侧说明的信息可以保留", requirements)

        style_scope = captured["style_guide"]["document_request"]["scope"]
        style_requirements = "\n".join(captured["style_guide"]["requirements"])
        self.assertIn("后续章节仿写会反复使用", style_scope)
        self.assertIn("没有稳定规律或后续执行价值的维度不要写", style_scope)
        self.assertIn("不要求每个维度都写", style_requirements)
        self.assertIn("避免风格百科", style_requirements)
        self.assertNotIn("文档必须覆盖写作方式", style_scope)
        self.assertNotIn("必须明确提炼", style_requirements)

        book_requirements = "\n".join(captured["book_outline"]["requirements"])
        self.assertIn("普通章节事件、普通战斗、临时小冲突不进入全书大纲", book_requirements)
        self.assertIn("可以不新增段落", book_requirements)
        self.assertNotIn("6-10 个关键推进点", book_requirements)

        volume_requirements = "\n".join(captured["volume_outline"]["requirements"])
        self.assertIn("没有设计价值的普通事件不写", volume_requirements)
        self.assertIn("不登记章节素材", volume_requirements)
        self.assertIn("不为了覆盖全部原文而补齐所有事件", volume_requirements)
        self.assertNotIn("必要信息必须完整保留", volume_requirements)


class WorldModelDefinitionTests(unittest.TestCase):
    def test_world_model_default_sections_are_world_knowledge_only(self) -> None:
        self.assertEqual(len(adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS), 14)
        self.assertIn("世界历史与纪元背景", adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS)
        self.assertIn("世界真相与认知边界", adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS)
        self.assertNotIn("本卷新增或修正世界设定", adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS)
        self.assertNotIn("可扩展世界专题", adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS)
        self.assertNotIn("历史与大事件", adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS)
        self.assertNotIn("已公开真相 / 未公开真相", adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS)

    def test_world_model_scope_text_limits_to_world_knowledge(self) -> None:
        scope = adaptation_workflow.world_model_scope_text()
        self.assertIn("设定唯一来源", scope)
        self.assertIn("默认二级标题只是可选组织参考", scope)
        self.assertIn("完整卷原文只是判断依据，不是待抽取清单", scope)
        self.assertIn("没有稳定世界知识的标题不要硬写", scope)
        self.assertNotIn("多个三级标题", scope)
        self.assertIn("背景", scope)
        self.assertNotIn("角色功能位、故事类型", scope)
        self.assertIn("故事类型、角色功能位", scope)
        self.assertIn("不属于世界模型", scope)
        self.assertIn("世界模型只允许写世界观与世界知识", scope)
        self.assertIn("严禁写卷内已发生大事件", scope)
        self.assertIn("剧情推进清单", scope)
        self.assertIn("新书自己的命名、数值体系、等级体系、术语体系和话语体系", scope)
        self.assertIn("不能与参考源出现相同命名、数值或话语体系", scope)
        self.assertIn("通用语素和玄幻/仙侠常见通用术语可以使用", scope)
        self.assertIn("参考源自造名词、专属组合词、专用话语体系必须改名或重构", scope)
        self.assertIn("如果参考源境界名是“XX境”", scope)
        self.assertIn("不能沿用相同“XX”前缀", scope)
        self.assertIn("“境”作为通用后缀可以保留", scope)
        self.assertIn("不要另建或依赖独立世界观设计文档", scope)

    def test_world_model_generation_payload_declares_unique_new_book_world_source(self) -> None:
        captured: dict[str, object] = {}

        def fake_call(*args, **kwargs):
            captured["user_input"] = args[3]
            return (object(), None)

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            adaptation_workflow.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                },
                volume_material={"volume_number": "001", "chapters": [], "extras": []},
                current_docs={
                    "world_model": "既有世界模型",
                    "style_guide": "文风",
                    "book_outline": "全书大纲",
                    "foreshadowing": "伏笔",
                },
                doc_key="world_model",
                output_path=Path("F:/novelist/.tmp_world_model_test.md"),
                stage_shared_prompt="",
                previous_response_id=None,
                prompt_cache_key="test-cache-key",
            )

        payload = adaptation_workflow.json.loads(str(captured["user_input"]))
        requirements = "\n".join(payload["requirements"])
        self.assertIn("设定唯一来源", requirements)
        self.assertIn("新书自己的命名系统、数值系统、等级体系", requirements)
        self.assertIn("不得沿用参考源的同名实体", requirements)
        self.assertIn("参考源功能 -> 新书世界模型设计", requirements)
        self.assertIn("严禁记录卷内已发生大事件", requirements)
        self.assertIn("主角个人战绩", requirements)
        self.assertIn("治疗进度", requirements)
        self.assertIn("不属于世界模型", requirements)
        self.assertIn("完整卷原文只是世界设计判断依据，不是设定清单", requirements)
        self.assertIn("后续章节会反复使用", requirements)
        self.assertIn("不要把当前卷原文逐项抽取成设定库", requirements)
        self.assertIn("通用语素和玄幻/仙侠常见通用术语可以使用", requirements)
        self.assertIn("专用话语术语", requirements)
        self.assertIn("参考源自造名词、专属组合词、标志性称谓和专用话语体系必须改名或重构", requirements)
        self.assertIn("参考源若使用“XX境”", requirements)
        self.assertIn("新书必须替换“XX”前缀", requirements)
        self.assertIn("“境”这个通用后缀允许继续使用", requirements)


class ForeshadowingDefinitionTests(unittest.TestCase):
    def test_foreshadowing_request_is_design_index_not_runtime_status(self) -> None:
        request = adaptation_workflow.build_document_request("foreshadowing")
        scope = request["scope"]

        self.assertIn("伏笔设计索引", scope)
        self.assertIn("参考源功能映射", scope)
        self.assertIn("新书伏笔设计", scope)
        self.assertIn("埋设意图", scope)
        self.assertIn("后续呼应方向", scope)
        self.assertIn("伏笔设计索引，只记录真正会跨章、跨卷或贯穿全书回收的伏笔设计", scope)
        self.assertIn("普通剧情细节", scope)
        self.assertIn("都不算全局伏笔", scope)
        self.assertIn("不要判断伏笔是否已经推进或兑现", scope)
        self.assertIn("受保护内容", scope)
        self.assertIn("原样保留", scope)
        for forbidden in ("待埋设", "已埋设", "待回收", "已回收", "回收记录", "状态推进"):
            self.assertNotIn(forbidden, scope)

    def test_foreshadowing_generation_payload_keeps_adaptation_scope(self) -> None:
        captured: dict[str, object] = {}

        def fake_call(*args, **kwargs):
            captured["user_input"] = args[3]
            return (object(), None)

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            adaptation_workflow.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 2,
                    "processed_volumes": ["001"],
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                },
                volume_material={"volume_number": "002", "chapters": [], "extras": []},
                current_docs={
                    "foreshadowing": "# 伏笔设计索引\n\n## 伏笔：旧设计\n",
                    "book_outline": "全书大纲",
                    "world_design": "世界观设计",
                    "world_model": "世界模型",
                },
                doc_key="foreshadowing",
                output_path=Path("F:/novelist/.tmp_foreshadowing_test.md"),
                stage_shared_prompt="",
                previous_response_id=None,
                prompt_cache_key="test-cache-key",
            )

        payload = adaptation_workflow.json.loads(str(captured["user_input"]))
        requirements = "\n".join(payload["requirements"])
        self.assertEqual(payload["required_file"], "04_foreshadowing.md")
        self.assertEqual(payload["target_file"]["preferred_mode"], "edit_or_patch")
        self.assertIn("伏笔设计索引", requirements)
        self.assertIn("参考源承担的功能", requirements)
        self.assertIn("新书对应设计", requirements)
        self.assertIn("后续呼应方向", requirements)
        self.assertIn("伏笔准入门槛", requirements)
        self.assertIn("普通剧情细节", requirements)
        self.assertIn("阶段性战绩", requirements)
        self.assertIn("治疗进度", requirements)
        self.assertIn("埋设内容 -> 后续触发/兑现方向", requirements)
        self.assertIn("世界知识放世界模型", requirements)
        self.assertIn("资料适配阶段只做设计索引", requirements)
        self.assertIn("运行时记录", requirements)
        self.assertIn("不得删除、归并、重命名", requirements)
        for forbidden in ("待埋设", "已埋设", "待回收", "已回收", "回收记录", "状态推进"):
            self.assertNotIn(forbidden, requirements)

    def test_stage_shared_prompt_keeps_foreshadowing_as_design_index(self) -> None:
        prompt = adaptation_workflow.build_stage_shared_prompt(
            manifest=_manifest(Path("F:/project")),
            volume_material=_volume_material("001"),
            loaded_files=[],
            source_bundle="",
            source_char_count=0,
        )

        self.assertIn("伏笔设计索引", prompt)
        self.assertIn("设计意图、功能映射和后续呼应方向", prompt)
        self.assertIn("已有运行时记录必须原样保留", prompt)
        for forbidden in ("待埋设", "已埋设", "待回收", "已回收", "回收记录", "状态推进"):
            self.assertNotIn(forbidden, prompt)


class AdaptationInjectionOrderTests(unittest.TestCase):
    def test_build_injected_global_docs_uses_requested_generation_order(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "book_outline": "全书大纲",
                "world_design": "世界观设计",
                "style_guide": "文笔写作风格",
                "world_model": "世界模型",
                "foreshadowing": "伏笔管理",
            }
        )
        self.assertEqual(
            list(injected.keys()),
            [
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
            ],
        )

    def test_build_injected_global_docs_excludes_current_target_document(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "book_outline": "全书大纲",
                "world_design": "世界观设计",
                "style_guide": "文笔写作风格",
                "world_model": "世界模型",
                "foreshadowing": "伏笔管理",
            },
            exclude_keys={"style_guide"},
        )
        self.assertNotIn("style_guide", injected)

    def test_build_injected_global_docs_keeps_full_content(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "foreshadowing": "伏" * 20000,
            }
        )

        self.assertEqual(injected["foreshadowing"], "伏" * 20000)

    def test_style_guide_generation_does_not_duplicate_existing_content(self) -> None:
        captured: dict[str, object] = {}

        def fake_call(*args, **kwargs):
            captured["instructions"] = args[2]
            captured["user_input"] = args[3]
            return (object(), None)

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            adaptation_workflow.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                },
                volume_material={"volume_number": "001", "chapters": [], "extras": []},
                current_docs={
                    "style_guide": "已有风格正文",
                    "book_outline": "全书大纲",
                    "world_design": "世界观设计",
                    "world_model": "世界模型",
                    "foreshadowing": "伏笔管理",
                },
                doc_key="style_guide",
                output_path=Path("F:/novelist/.tmp_style_guide_test.md"),
                stage_shared_prompt="",
                previous_response_id=None,
                prompt_cache_key="test-cache-key",
            )

        user_input = captured["user_input"]
        self.assertIsInstance(user_input, str)
        payload = adaptation_workflow.json.loads(str(user_input))
        self.assertIn("target_file", payload)
        self.assertEqual(payload["target_file"]["preferred_mode"], "edit_or_patch")
        self.assertIn("按修改意图选择工具", payload["target_file"]["tool_selection_policy"])
        self.assertNotIn("existing_style_guide", payload)
        self.assertNotIn("style_guide", payload["injected_global_docs"])

class AdaptationVolumeReviewTests(unittest.TestCase):
    def test_adaptation_review_targets_recommend_tools_by_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = _seed_adaptation_docs(project_root, "001")

            snapshot = adaptation_workflow.adaptation_review_target_snapshot(
                {"world_model": paths["world_model"]}
            )

        self.assertEqual(snapshot[0]["preferred_mode"], "edit_or_patch")
        self.assertIn("替换已有内容", snapshot[0]["tool_selection_policy"])
        self.assertIn("按 Markdown 标题", snapshot[0]["tool_selection_policy"])

    def test_apply_document_operation_with_repair_retries_bad_generation_old_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            paths["world_model"].write_text("第一段。\n\n第二段原文。\n", encoding="utf-8")
            failed_operation = adaptation_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_bad",
                status="completed",
                output_types=["function_call"],
                preview="bad edit",
                raw_body_text="",
                raw_json={},
                edit_payload=adaptation_workflow.document_ops.DocumentEditPayload(
                    files=[
                        adaptation_workflow.document_ops.DocumentEditFile(
                            file_key="world_model",
                            edits=[
                                adaptation_workflow.document_ops.DocumentEditEdit(
                                    old_text="第二段模型误写。",
                                    new_text="第二段修订后。",
                                )
                            ],
                        )
                    ]
                ),
            )
            repaired_payload = adaptation_workflow.document_ops.DocumentEditPayload(
                files=[
                    adaptation_workflow.document_ops.DocumentEditFile(
                        file_key="world_model",
                        edits=[
                            adaptation_workflow.document_ops.DocumentEditEdit(
                                old_text="第二段原文。",
                                new_text="第二段修订后。",
                            )
                        ],
                    )
                ]
            )
            repaired_result = adaptation_workflow.llm_runtime.MultiFunctionToolResult(
                tool_name=adaptation_workflow.document_ops.DOCUMENT_EDIT_TOOL_NAME,
                parsed=repaired_payload,
                response_id="resp_repair",
                status="completed",
                output_types=["function_call"],
                preview="fixed edit",
                raw_body_text="",
                raw_json={},
            )

            with patch.object(
                adaptation_document_generation_module.llm_runtime,
                "call_function_tools",
                return_value=repaired_result,
            ) as call_tools:
                applied, response_id, repair_response_ids = ORIGINAL_ADAPTATION_APPLY_DOCUMENT_OPERATION_WITH_REPAIR(
                    client=Mock(),
                    model="test-model",
                    instructions=adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
                    shared_prompt="shared prompt\n",
                    operation=failed_operation,
                    allowed_files={"world_model": paths["world_model"]},
                    previous_response_id="resp_bad",
                    prompt_cache_key="cache-key",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                    repair_phase_key="adaptation_stage_document_locator_repair",
                    repair_role="资深网络小说改编规划编辑",
                    repair_task="修正定位文本。",
                )

            self.assertEqual(response_id, "resp_repair")
            self.assertEqual(repair_response_ids, ["resp_repair"])
            self.assertEqual(applied.changed_keys, ["world_model"])
            self.assertIn("第二段修订后。", paths["world_model"].read_text(encoding="utf-8"))
            call_tools.assert_called_once()
            self.assertEqual(
                [spec.name for spec in call_tools.call_args.kwargs["tool_specs"]],
                [spec.name for spec in adaptation_workflow.adaptation_stage_tool_specs()],
            )
            repair_input = call_tools.call_args.kwargs["user_input"]
            self.assertIn("第二段模型误写。", repair_input)
            self.assertIn("第二段原文。", repair_input)
            self.assertIn("逐字复制", repair_input)

    def test_generation_resume_state_uses_current_batch_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = _seed_adaptation_docs(project_root, "002")
            plan = adaptation_workflow.build_document_plan("002")
            adaptation_workflow.write_markdown_data(
                paths["stage_manifest"],
                title="Stage Status 002",
                payload={
                    "status": "generating_document",
                    "processed_volume": "002",
                    "current_batch": 4,
                    "current_batch_range": "volume_outline",
                },
                summary_lines=["status: generating_document"],
            )

            resume_state = adaptation_workflow.load_document_generation_resume_state(paths, plan)

            self.assertEqual(
                resume_state["completed_keys"],
                ["world_model", "book_outline", "foreshadowing"],
            )
            self.assertEqual(
                [item["key"] for item in resume_state["generated_documents"]],
                ["world_model", "book_outline", "foreshadowing"],
            )

    def test_generation_resume_state_recovers_corrupted_stage_from_file_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            manifest["processed_volumes"] = ["001"]
            manifest["last_processed_volume"] = "001"
            paths_001 = adaptation_workflow.stage_paths(project_root, "001")
            paths_002 = adaptation_workflow.stage_paths(project_root, "002")
            plan = adaptation_workflow.build_document_plan("002")

            _write_text(paths_001["stage_manifest"], "previous volume completed\n")
            previous_done = 1_700_000_000
            os.utime(paths_001["stage_manifest"], (previous_done, previous_done))

            _write_text(paths_002["world_model"], "第二卷世界模型已生成。\n")
            _write_text(paths_002["book_outline"], "第二卷全书大纲已生成。\n")
            _write_text(paths_002["foreshadowing"], "旧伏笔文档，还不能算本轮完成。\n")
            os.utime(paths_002["world_model"], (previous_done + 1010, previous_done + 1010))
            os.utime(paths_002["book_outline"], (previous_done + 1020, previous_done + 1020))
            os.utime(paths_002["foreshadowing"], (previous_done + 100, previous_done + 100))

            adaptation_workflow.write_markdown_data(
                paths_002["stage_manifest"],
                title="Stage Status 002",
                payload={
                    "status": "generating_document",
                    "processed_volume": "002",
                    "current_batch": 1,
                    "current_batch_range": "world_model",
                    "api_calls": [],
                    "generated_document_keys": [],
                },
                summary_lines=["status: generating_document"],
            )

            resume_state = adaptation_workflow.load_document_generation_resume_state(
                paths_002,
                plan,
                manifest=manifest,  # type: ignore[arg-type]
                volume_number="002",
            )

            self.assertEqual(resume_state["resume_source"], "file_mtime_prefix")
            self.assertEqual(resume_state["completed_keys"], ["world_model", "book_outline"])
            self.assertEqual(
                [item["key"] for item in resume_state["generated_documents"]],
                ["world_model", "book_outline"],
            )
            self.assertTrue(all(item["resumed"] for item in resume_state["generated_documents"]))

    def test_stage_status_snapshot_persists_generated_document_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")

            adaptation_workflow.write_stage_status_snapshot(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                status="document_generated",
                note="世界模型文档已生成，断点已保存。",
                total_batches=5,
                current_batch=1,
                current_batch_range="world_model",
                generated_documents=[
                    {
                        "index": 1,
                        "key": "world_model",
                        "label": "世界模型文档",
                        "response_id": "resp_world",
                        "output_path": str(paths["world_model"]),
                    }
                ],
                previous_response_id="resp_world",
            )

            payload = adaptation_workflow.load_stage_manifest_payload(paths["stage_manifest"])
            self.assertEqual(payload["generated_document_keys"], ["world_model"])
            self.assertEqual(payload["last_response_id"], "resp_world")

    def test_failed_stage_snapshot_can_preserve_document_resume_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            generated_documents = [
                {
                    "index": 1,
                    "key": "world_model",
                    "label": "世界模型文档",
                    "response_id": "resp_world",
                    "output_path": str(paths["world_model"]),
                }
            ]

            adaptation_workflow.write_stage_status_snapshot(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                status="failed",
                note="阶段执行失败，等待人工排查。",
                total_batches=5,
                error_message="接口请求失败",
                generated_documents=generated_documents,
                previous_response_id="resp_world",
            )

            resume_state = adaptation_workflow.load_document_generation_resume_state(
                paths,
                adaptation_workflow.build_document_plan("001"),
            )
            self.assertEqual(resume_state["completed_keys"], ["world_model"])
            self.assertEqual(resume_state["last_response_id"], "resp_world")

    def test_stage_outputs_wait_for_review_before_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")

            adaptation_workflow.write_stage_outputs(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                generated_documents=[{"key": "world_model", "label": "世界模型文档"}],
                source_char_count=123,
                loaded_file_count=1,
            )

            self.assertEqual(manifest["processed_volumes"], [])
            stage_manifest = paths["stage_manifest"].read_text(encoding="utf-8")
            self.assertIn('"status": "review_pending"', stage_manifest)
            self.assertIn("001_adaptation_review.md", stage_manifest)

            review_result = adaptation_workflow.AdaptationReviewResult(
                payload=adaptation_workflow.AdaptationReviewPayload(passed=True, review_md="审核通过。"),
                response_ids=["resp_review"],
                review_path=str(paths["adaptation_review"]),
                fix_attempts=0,
            )
            adaptation_workflow.mark_volume_processed_after_review(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                generated_documents=[{"key": "world_model", "label": "世界模型文档"}],
                source_char_count=123,
                loaded_file_count=1,
                review_result=review_result,
            )

            self.assertEqual(manifest["processed_volumes"], ["001"])
            self.assertEqual(manifest["last_processed_volume"], "001")
            stage_manifest = paths["stage_manifest"].read_text(encoding="utf-8")
            self.assertIn('"status": "completed"', stage_manifest)
            self.assertIn('"status": "passed"', stage_manifest)

    def test_review_payload_for_later_volume_includes_existing_style_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "002")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("002"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

            file_keys = [item["file_key"] for item in request["adaptation_documents"]]
            self.assertEqual(
                file_keys,
                [
                    "world_model",
                    "style_guide",
                    "book_outline",
                    "foreshadowing",
                    "volume_outline",
                ],
            )
            self.assertIn("后续卷审核本卷更新文档，并带上已存在的文笔写作风格文档", request["review_scope"]["document_set_policy"])
            self.assertIn("后续卷只能读取与审核，不得把 style_guide 写入 rewrite_targets", "\n".join(request["requirements"]))

    def test_later_volume_review_fix_targets_exclude_style_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = _seed_adaptation_docs(project_root, "002")

            review_files = adaptation_workflow.adaptation_review_document_files(paths)
            allowed_fix_files = adaptation_workflow.adaptation_review_allowed_files(paths, volume_number="002")

        self.assertIn("style_guide", review_files)
        self.assertNotIn("style_guide", allowed_fix_files)

    def test_review_and_fix_current_goals_are_trailing_dynamic_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "001")
            review_request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("001"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )
            failed_review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过。",
                blocking_issues=["世界模型残留参考源话语体系"],
                rewrite_targets=["world_model"],
            )
            fix_request = adaptation_workflow.build_adaptation_review_fix_request(
                review=failed_review,
                allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths),
            )
            repair_request = adaptation_workflow.build_document_operation_repair_payload(
                apply_error=ValueError("未找到 old_text"),
                failed_operation=adaptation_workflow.document_ops.DocumentOperationCallResult(
                    mode="edit",
                    response_id="resp_failed",
                    status="completed",
                    output_types=["function_call"],
                    preview="failed edit",
                    raw_body_text="",
                    raw_json={},
                ),
                allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths),
            )

        self.assertEqual(list(review_request.keys())[-1], "latest_work_target")
        self.assertEqual(review_request["latest_work_target"]["required_tool"], adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME)
        self.assertIn("调用 submit_workflow_result", review_request["latest_work_target"]["instruction"])

        self.assertEqual(list(fix_request.keys())[-1], "latest_work_target")
        self.assertEqual(fix_request["latest_work_target"]["forbidden_tool"], adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME)
        self.assertIn("必须调用 write/edit/patch", fix_request["latest_work_target"]["instruction"])

        self.assertEqual(list(repair_request.keys())[-1], "latest_work_target")
        self.assertEqual(repair_request["latest_work_target"]["forbidden_tool"], adaptation_workflow.WORKFLOW_SUBMISSION_TOOL_NAME)
        self.assertIn("最新工作目标", repair_request["latest_work_target"]["instruction"])

    def test_review_payload_protects_chapter_runtime_foreshadowing_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "002")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("002"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

        requirements = "\n".join(request["requirements"])
        self.assertIn("运行时记录", requirements)
        self.assertIn("受保护内容", requirements)
        self.assertIn("不得要求删除或改写", requirements)
        self.assertIn("不得仅因其存在判定资料适配不通过", requirements)

    def test_review_payload_rejects_plot_details_as_global_foreshadowing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "001")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("001"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

        requirements = "\n".join(request["requirements"])
        self.assertIn("伏笔准入门槛", requirements)
        self.assertIn("普通剧情细节", requirements)
        self.assertIn("阶段性战绩", requirements)
        self.assertIn("治疗进度", requirements)
        self.assertIn("未来触发、反转、兑现或呼应方向", requirements)
        self.assertIn("如果只能说明已经发生的剧情事实，就不应写入伏笔文档", requirements)

    def test_review_payload_checks_source_names_and_discourse_system(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "001")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("001"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

        requirements = "\n".join(request["requirements"])
        self.assertIn("参考源人物名、地名、姓氏、势力名", requirements)
        self.assertIn("标志性台词", requirements)
        self.assertIn("话语体系", requirements)
        self.assertIn("不得直接照搬", requirements)
        self.assertIn("所有被审核的资料文档主体都必须是新书资料", requirements)
        self.assertIn("参考源只允许作为功能映射依据", requirements)
        self.assertIn("不得作为新书正文事实、设定正文、剧情正文或话语体系出现", requirements)
        self.assertIn("必须区分“参考源侧功能映射”和“新书设定主体”", requirements)
        self.assertIn("明确标注在参考源功能映射、参考源侧、原书功能等语境中", requirements)
        self.assertIn("不应仅因出现参考源内容就判定不通过", requirements)
        self.assertIn("只有它们被写成新书设定主体", requirements)
        self.assertIn("必须交叉检查 adaptation_documents 中所有资料的一致性", requirements)
        self.assertIn("同一新书世界、时间线、故事方向、实体命名", requirements)
        self.assertIn("不能一处使用新书设定、一处沿用参考源设定", requirements)
        self.assertIn("非功能映射语境中仍以参考源世界", requirements)
        self.assertIn("对应 file_key 写入 rewrite_targets", requirements)
        self.assertIn("设定唯一来源", requirements)
        self.assertIn("新书自己的命名系统、数值系统、等级体系", requirements)
        self.assertIn("不得与参考源出现相同命名、数值体系或概念话语体系", requirements)
        self.assertIn("通用语素和玄幻/仙侠常见通用术语可以使用，不应判为污染", requirements)
        self.assertIn("专用话语术语", requirements)
        self.assertIn("参考源自造名词、专属组合词、标志性称谓和专用话语体系必须改名或重构", requirements)
        self.assertIn("“境”这个通用后缀允许继续使用，不应因后缀相同判定为污染", requirements)
        self.assertIn("只包含世界观与世界知识", requirements)
        self.assertIn("卷内已发生大事件", requirements)
        self.assertIn("剧情推进清单", requirements)

    def test_review_failure_repairs_documents_then_passes_without_marking_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            failed_review = adaptation_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="不通过，世界模型还残留参考源人物名。",
                blocking_issues=["世界模型残留参考源人物名"],
                rewrite_targets=["world_model"],
            )
            passed_review = adaptation_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="审核通过。",
            )

            def fake_run_agent_stage(*args, **kwargs):
                if fake_run_agent_stage.call_count == 0:
                    _write_text(paths["world_model"], "世界模型：新书主角名已替换。\n")
                    kwargs["on_tool_result"](
                        Mock(
                            tool_name="submit_document_edits",
                            output="ok",
                            applied=Mock(changed_keys=["world_model"]),
                        )
                    )
                    fake_run_agent_stage.call_count += 1
                    result = _agent_stage_result(failed_review, "resp_review_1", ["resp_fix_1", "resp_review_1"])
                    result.applications = [
                        Mock(
                            tool_name="submit_document_edits",
                            output="ok",
                            applied=Mock(changed_keys=["world_model"]),
                        )
                    ]
                    return result
                fake_run_agent_stage.call_count += 1
                return _agent_stage_result(passed_review, "resp_review_2")

            fake_run_agent_stage.call_count = 0

            with (
                patch.object(
                    adaptation_review_module,
                    "run_agent_stage",
                    side_effect=fake_run_agent_stage,
                ) as agent_call,
                patch.object(adaptation_review_module, "print_progress") as progress_call,
            ):
                result, response_id = adaptation_review_module.run_adaptation_review_until_passed(
                    client=Mock(),
                    model="test-model",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                    stage_shared_prompt="shared\n",
                    previous_response_id="resp_docs",
                    prompt_cache_key="cache-key",
                )

            self.assertTrue(result.payload.passed)
            self.assertEqual(response_id, "resp_review_2")
            self.assertEqual(agent_call.call_count, 2)
            self.assertEqual(agent_call.call_args_list[0].kwargs["instructions"], adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS)
            self.assertEqual(agent_call.call_args_list[1].kwargs["instructions"], adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS)
            self.assertIsNone(agent_call.call_args_list[0].kwargs["previous_response_id"])
            self.assertIsNone(agent_call.call_args_list[1].kwargs["previous_response_id"])
            self.assertTrue(callable(agent_call.call_args_list[0].kwargs["on_tool_result"]))
            self.assertIn("新书主角名已替换", agent_call.call_args_list[1].kwargs["user_input"])
            progress_text = "\n".join(str(call.args[0]) for call in progress_call.call_args_list if call.args)
            self.assertIn("卷资料审核 agent 工具已应用：submit_document_edits，变更=world_model", progress_text)
            self.assertIn("卷资料审核 agent 本轮执行文档工具 1 次，累计变更=world_model", progress_text)
            self.assertIn("卷资料审核 agent 提交审核结论：未通过", progress_text)
            self.assertEqual(
                sorted(agent_call.call_args_list[0].kwargs["allowed_files"]),
                sorted(adaptation_workflow.adaptation_review_allowed_files(paths)),
            )
            self.assertEqual(manifest["processed_volumes"], [])
            self.assertIn("新书主角名已替换", paths["world_model"].read_text(encoding="utf-8"))
            self.assertTrue(paths["adaptation_review"].exists())

    def test_adaptation_review_allows_five_total_review_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            failed_review = adaptation_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="仍不通过。",
                blocking_issues=["世界模型残留参考源话语体系"],
                rewrite_targets=["world_model"],
            )

            with (
                self.assertRaises(adaptation_workflow.llm_runtime.ModelOutputError),
                patch.object(
                    adaptation_review_module,
                    "run_agent_stage",
                    side_effect=[
                        _agent_stage_result(failed_review, f"resp_review_{index}")
                        for index in range(1, 6)
                    ],
                ) as agent_call,
            ):
                adaptation_review_module.run_adaptation_review_until_passed(
                    client=Mock(),
                    model="test-model",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                    stage_shared_prompt="shared\n",
                    previous_response_id="resp_docs",
                    prompt_cache_key="cache-key",
                )

            self.assertEqual(adaptation_workflow.MAX_ADAPTATION_REVIEW_ATTEMPTS, 5)
            self.assertEqual(adaptation_workflow.MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS, 4)
            self.assertEqual(agent_call.call_count, 5)
            self.assertTrue(
                all(call.kwargs["previous_response_id"] is None for call in agent_call.call_args_list)
            )

    def test_adaptation_review_context_compaction_drops_previous_response_id(self) -> None:
        self.assertIsNone(
            adaptation_workflow.compact_adaptation_review_previous_response_id("resp_docs")
        )
        self.assertIsNone(
            adaptation_workflow.compact_adaptation_review_previous_response_id(None)
        )
        self.assertIn(
            "沿用卷资料审核逻辑会话",
            adaptation_workflow.adaptation_review_compaction_session_status("resp_docs"),
        )
        self.assertIn(
            "不沿用 previous_response_id=resp_docs",
            adaptation_workflow.adaptation_review_compaction_session_status("resp_docs"),
        )

    def test_reviewing_snapshot_preserves_document_session_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            generated_documents = [
                {
                    "index": 1,
                    "key": "world_model",
                    "label": "世界模型文档",
                    "response_id": "resp_world",
                    "output_path": str(paths["world_model"]),
                }
            ]

            adaptation_workflow.write_stage_status_snapshot(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                status="document_generated",
                note="世界模型文档已生成，断点已保存。",
                total_batches=5,
                current_batch=1,
                current_batch_range="world_model",
                generated_documents=generated_documents,
                previous_response_id="resp_world",
            )
            adaptation_workflow.write_stage_status_snapshot(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                status="adaptation_reviewing",
                note="正在进行卷资料审核。",
                previous_response_id="resp_world",
            )

            payload = adaptation_workflow.load_stage_manifest_payload(paths["stage_manifest"])

        self.assertEqual(payload["last_response_id"], "resp_world")
        self.assertEqual(payload["generated_document_keys"], ["world_model"])
        self.assertEqual(payload["api_calls"][0]["response_id"], "resp_world")

    def test_review_fix_rejects_unauthorized_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过。",
                blocking_issues=["越权测试"],
                rewrite_targets=["world_model"],
            )
            bad_payload = adaptation_workflow.document_ops.DocumentPatchPayload(
                files=[
                    adaptation_workflow.document_ops.DocumentPatchFile(
                        file_path=str(project_root / "outside.md"),
                        edits=[
                            adaptation_workflow.document_ops.DocumentPatchEdit(
                                action="append",
                                new_text="越权内容",
                            )
                        ],
                    )
                ]
            )
            bad_result = adaptation_workflow.llm_runtime.MultiFunctionToolResult(
                tool_name=adaptation_workflow.document_ops.DOCUMENT_PATCH_TOOL_NAME,
                parsed=bad_payload,
                response_id="resp_bad",
                status="completed",
                output_types=["function_call"],
                preview="bad",
                raw_body_text="",
                raw_json={},
            )

            with (
                self.assertRaises(ValueError),
                patch.object(adaptation_workflow, "MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS", 0),
                patch.object(adaptation_review_module.llm_runtime, "call_function_tools", return_value=bad_result),
            ):
                adaptation_workflow.apply_adaptation_review_fix_with_repair(
                    client=Mock(),
                    model="test-model",
                    shared_prompt="shared\n",
                    review=review,
                    allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths),
                    previous_response_id="resp_review",
                    prompt_cache_key="cache-key",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                )

            self.assertEqual(manifest["processed_volumes"], [])
            self.assertTrue(paths["response_debug"].exists())
            self.assertIn("未授权文件路径", paths["response_debug"].read_text(encoding="utf-8"))

    def test_later_volume_review_fix_rejects_style_guide_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("002")
            paths = _seed_adaptation_docs(project_root, "002")
            review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过，但错误地要求修改文风。",
                blocking_issues=["文风问题"],
                rewrite_targets=["style_guide"],
            )

            with self.assertRaises(adaptation_workflow.llm_runtime.ModelOutputError):
                adaptation_workflow.apply_adaptation_review_fix_with_repair(
                    client=Mock(),
                    model="test-model",
                    shared_prompt="shared\n",
                    review=review,
                    allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths, volume_number="002"),
                    previous_response_id="resp_review",
                    prompt_cache_key="cache-key",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                )

            self.assertTrue(paths["response_debug"].exists())
            debug_text = paths["response_debug"].read_text(encoding="utf-8")
            self.assertIn("不允许原地修复的目标", debug_text)
            self.assertIn("style_guide", debug_text)


if __name__ == "__main__":
    unittest.main()
