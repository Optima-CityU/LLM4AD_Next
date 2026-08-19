from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from sdk_events import EventState, extract_json_object, recover_json_object, translate_sdk_message


class SdkEventTests(unittest.TestCase):
    def test_translates_initialization_and_safe_tool_steps(self) -> None:
        state = EventState()
        self.assertEqual(
            translate_sdk_message({"type": "system", "subtype": "init", "data": {}}, state),
            [
                {
                    "type": "progress",
                    "progress": 20,
                    "stage": "initializing",
                    "message": "智能解析环境已就绪",
                }
            ],
        )
        self.assertEqual(
            translate_sdk_message(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "content_block": {
                            "type": "tool_use",
                            "id": "read-1",
                            "name": "Read",
                            "input": {"file_path": "/workspace/input/documents/secret.md"},
                        },
                    },
                },
                state,
            ),
            [
                {
                    "type": "step",
                    "progress": 24,
                    "stage": "analyzing",
                    "message": "正在读取原始文档",
                    "step_id": "read-1",
                    "step_kind": "tool",
                    "step_status": "running",
                    "tool_name": "Read",
                    "step_detail": "input/documents/secret.md",
                }
            ],
        )

    def test_keeps_safe_tool_targets_visible_through_completion(self) -> None:
        state = EventState()
        translate_sdk_message(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "write-1",
                        "name": "Write",
                        "input": {
                            "file_path": "/workspace/output/children/guide.md",
                            "content": "private document content",
                        },
                    }
                ],
            },
            state,
        )
        events = translate_sdk_message(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "write-1",
                        "content": "private tool output",
                    }
                ],
            },
            state,
        )

        self.assertEqual(events[0]["step_detail"], "output/children/guide.md")
        self.assertNotIn("private document content", json.dumps(events, ensure_ascii=False))
        self.assertNotIn("private tool output", json.dumps(events, ensure_ascii=False))

    def test_tracks_model_output_without_exposing_it_in_steps(self) -> None:
        state = EventState()
        self.assertEqual(
            translate_sdk_message(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "private output"},
                    },
                },
                state,
                now_ms=10_000,
            ),
            [
                {
                    "type": "output",
                    "progress": 18,
                    "stage": "agent_output",
                    "message": "private output",
                },
                {
                    "type": "step",
                    "progress": 18,
                    "stage": "analyzing",
                    "message": "模型正在生成解析方案",
                    "step_id": "model-response",
                    "step_kind": "model",
                    "step_status": "running",
                    "elapsed_seconds": 0,
                },
            ],
        )

    def test_exposes_structured_output_finalization_without_payload(self) -> None:
        state = EventState(last_progress=68)
        started = translate_sdk_message(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "structured-1",
                        "name": "StructuredOutput",
                        "input": {"private": "generated plan payload"},
                    }
                ],
            },
            state,
        )
        completed = translate_sdk_message(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-1",
                        "content": "private validation result",
                    }
                ],
            },
            state,
        )

        self.assertEqual(started[0]["tool_name"], "StructuredOutput")
        self.assertEqual(started[0]["stage"], "verifying")
        self.assertEqual(started[0]["step_status"], "running")
        self.assertEqual(completed[0]["step_status"], "success")
        self.assertIn("正在校验并保存", completed[0]["message"])
        serialized = json.dumps(started + completed, ensure_ascii=False)
        self.assertNotIn("generated plan payload", serialized)
        self.assertNotIn("private validation result", serialized)

    def test_exposes_plan_checkpoint_tools_without_candidate_content(self) -> None:
        state = EventState(last_progress=40)

        events = translate_sdk_message(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "candidate-1",
                        "name": "mcp__knowledge_plan__upsert_plan_candidate",
                        "input": {"id": "faithful", "description": "private plan details"},
                    }
                ],
            },
            state,
        )

        self.assertEqual(events[0]["tool_name"], "SavePlanCandidate")
        self.assertEqual(events[0]["stage"], "planning")
        self.assertNotIn("private plan details", json.dumps(events, ensure_ascii=False))

    def test_reports_native_compaction_hooks(self) -> None:
        state = EventState(last_progress=46)
        self.assertEqual(
            state.compaction_started(),
            {
                "type": "step",
                "progress": 46,
                "stage": "compacting",
                "message": "上下文接近模型上限，正在压缩后继续解析",
                "step_id": "context-compaction-1",
                "step_kind": "context",
                "step_status": "running",
            },
        )
        self.assertEqual(
            state.compaction_finished(),
            {
                "type": "step",
                "progress": 46,
                "stage": "analyzing",
                "message": "上下文压缩完成，正在继续解析",
                "step_id": "context-compaction-1",
                "step_kind": "context",
                "step_status": "success",
            },
        )

    def test_preserves_api_output_limit_errors_for_user_feedback(self) -> None:
        state = EventState()

        events = translate_sdk_message(
            {
                "type": "assistant",
                "error": "unknown",
                "content": [
                    {
                        "type": "text",
                        "text": "API Error: response exceeded the 6000 output token maximum",
                    }
                ],
            },
            state,
        )
        translate_sdk_message(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "success",
            },
            state,
        )

        self.assertIn("output token maximum", state.result_error)
        self.assertEqual(events[0]["step_status"], "failed")

    def test_recovers_largest_complete_json_object(self) -> None:
        plan = {
            "topic_summary": "demo",
            "strategies": [{"id": "faithful", "documents": [{"title": "main"}]}],
        }
        self.assertEqual(extract_json_object(f"result:\n{json.dumps(plan)}\n"), plan)
        self.assertEqual(
            recover_json_object(f"{json.dumps(plan)}\nThe plan has been saved successfully.", ""),
            plan,
        )
        self.assertIsNone(extract_json_object('{"incomplete":'))


class PythonRunnerContractTests(unittest.TestCase):
    def test_protocol_adapter_state_is_ephemeral_across_resumed_phases(self) -> None:
        import runner

        self.assertEqual(
            runner.CC_SWITCH_CONFIG_DIR,
            Path("/tmp/llm4ad-knowledge-parser-cc-switch"),
        )
        self.assertNotEqual(
            runner.CC_SWITCH_CONFIG_DIR,
            runner.RUNTIME_HOME / ".cc-switch",
        )

    def test_refinement_is_a_supported_parser_job_mode(self) -> None:
        import runner

        with mock.patch.multiple(
            runner,
            JOB_MODE="refine",
            PLAN_INTERACTION_MODE="collaborative",
            UPSTREAM_BASE_URL="http://backend:8000/api/v1/llm4ad/llmproxy/v1",
            UPSTREAM_API_KEY="ephemeral-token",
            UPSTREAM_MODEL="model-a",
            UPSTREAM_API_FORMAT="openai_chat",
            PLAN_QUESTION_TIMEOUT_SECONDS=1,
            MODEL_CONTEXT_TOKENS=128_000,
        ):
            runner.validate_configuration()

    def test_planning_uses_an_in_process_mcp_plan_store(self) -> None:
        directory = Path(__file__).resolve().parent
        runner = (directory / "runner.py").read_text(encoding="utf-8")
        plan_store = directory / "plan_store.py"

        self.assertTrue(plan_store.exists(), "plan_store.py must own durable plan artifacts")
        self.assertIn("create_sdk_mcp_server", runner)
        self.assertIn("upsert_plan_candidate", runner)
        self.assertIn("finalize_plan_set", runner)

        from plan_store import PlanStore

        with self.subTest("candidate artifacts are assembled without a fixed count"):
            import tempfile

            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                store = PlanStore(output)
                store.save_source_analysis(
                    {
                        "topic_summary": "完整整理求解器知识",
                        "source_overview": [
                            {
                                "filename": "solver.md",
                                "summary": "求解器说明",
                                "key_sections": ["约束"],
                            }
                        ],
                    }
                )
                candidate = {
                    "id": "faithful",
                    "name": "高保真整理",
                    "description": "保留全部细节",
                    "loss_level": "lossless",
                    "document_count": 1,
                    "documents": [
                        {
                            "title": "总览",
                            "document_type": "main",
                            "purpose": "完整整理",
                            "source_coverage": ["solver.md#全部"],
                            "must_preserve": ["约束"],
                        }
                    ],
                    "deduplication_policy": "仅删除完全重复内容",
                }
                store.upsert_plan_candidate(candidate)
                store.upsert_plan_candidate({**candidate, "id": "source-aligned", "name": "按来源整理"})
                payload = store.finalize_plan_set("faithful")

                self.assertEqual(len(payload["strategies"]), 2)
                self.assertTrue((output / "planning/candidates/faithful.json").is_file())
                self.assertEqual(
                    json.loads((output / "plan.json").read_text(encoding="utf-8")),
                    payload,
                )

    def test_parser_execution_is_owned_by_python_sdk(self) -> None:
        directory = Path(__file__).resolve().parent
        runner = (directory / "runner.py").read_text(encoding="utf-8")
        dockerfile = (directory.parent.parent / "src/backend/Dockerfile.task").read_text(encoding="utf-8")
        task = (directory.parent.parent / "src/backend/app/tasks/knowledge_parser.py").read_text(encoding="utf-8")

        self.assertIn("from claude_agent_sdk import", runner)
        self.assertIn("ClaudeAgentOptions(", runner)
        self.assertIn("query(prompt=self.prompt_stream(prompt)", runner)
        self.assertIn("settings=str(sdk_settings_path)", runner)
        self.assertIn('"autoCompactEnabled": True', runner)
        self.assertIn('"PreCompact"', runner)
        self.assertIn("PermissionResultAllow", runner)
        self.assertIn("cc-switch", runner)
        self.assertNotIn('output_format={"type": "json_schema"', runner)
        self.assertNotIn("max_turns=", runner)
        self.assertNotIn("KNOWLEDGE_MAX_TURNS", runner)
        self.assertNotIn("KNOWLEDGE_MAX_TURNS", task)
        self.assertIn("SESSION_ID_PATH", runner)
        self.assertIn("resume=resume_session_id", runner)
        self.assertIn("persist_session_id(message)", runner)
        self.assertIn('KNOWLEDGE_PLAN_INTERACTION_MODE", "collaborative"', runner)
        self.assertIn("REFINEMENT_PROMPT.md", runner)
        self.assertIn("refinement.txt", runner)
        self.assertTrue((directory / "REFINEMENT_PROMPT.md").is_file())
        self.assertNotIn("@anthropic-ai/claude-agent-sdk", dockerfile)
        self.assertNotIn("claude-agent-sdk-runtime", dockerfile)
        self.assertIn("runner.py", dockerfile)
        self.assertIn("plan_store.py", dockerfile)
        self.assertIn("REFINEMENT_PROMPT.md", dockerfile)
        self.assertIn('["python", "/app/knowledge-parser/runner.py"]', task)


if __name__ == "__main__":
    unittest.main()
