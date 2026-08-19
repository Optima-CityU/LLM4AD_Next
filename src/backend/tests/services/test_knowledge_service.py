"""Knowledge-library document contracts and task injection tests."""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from llm4ad.planner.memory import Memory
from loguru import logger

from app import models
from app.api.base_routes import users as users_routes
from app.core import redis as redis_core
from app.schemas import knowledge as knowledge_schemas
from app.services import credential_broker, knowledge_service
from app.tasks import knowledge_parser


class _CreateTopicSession:
    def __init__(self) -> None:
        self.added = []
        self.deleted = []

    def add(self, value) -> None:
        self.added.append(value)

    def commit(self) -> None:
        return None

    def delete(self, value) -> None:
        self.deleted.append(value)

    def refresh(self, _value) -> None:
        return None


def _valid_parse_plan_payload() -> dict:
    return {
        "topic_summary": "保留算法约束、实现步骤和示例的高保真整理。",
        "source_overview": [
            {
                "filename": "solver.md",
                "summary": "求解器设计与评估说明",
                "key_sections": ["约束", "评估"],
            }
        ],
        "recommended_strategy_id": "faithful",
        "strategies": [
            {
                "id": "faithful",
                "name": "高保真整理",
                "description": "保留全部关键细节。",
                "loss_level": "lossless",
                "document_count": 1,
                "documents": [
                    {
                        "title": "求解器总览",
                        "document_type": "main",
                        "purpose": "保留完整流程",
                        "source_coverage": ["solver.md#全部"],
                        "must_preserve": ["约束", "示例"],
                    }
                ],
                "deduplication_policy": "仅删除完全重复内容。",
            },
            {
                "id": "source-aligned",
                "name": "按来源整理",
                "description": "保持原始文件组织。",
                "loss_level": "light",
                "document_count": 1,
                "documents": [
                    {
                        "title": "solver.md 整理稿",
                        "document_type": "main",
                        "purpose": "保持来源顺序",
                        "source_coverage": ["solver.md#全部"],
                        "must_preserve": ["全部正文"],
                    }
                ],
                "deduplication_policy": "仅清理导航噪声。",
            },
        ],
    }


def test_user_can_create_an_empty_knowledge_topic_before_uploading_files() -> None:
    request = knowledge_schemas.KnowledgeSourceCreateRequest(title="  Solver Notes  ")
    db = _CreateTopicSession()

    result = knowledge_service.create_source(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        request,
    )

    assert result.title == "Solver Notes"
    assert result.source_file_count == 0
    assert result.source_size == 0
    assert result.parse_status == "unparsed"


def test_user_can_save_background_knowledge_on_a_topic(monkeypatch) -> None:
    source = SimpleNamespace(
        id=uuid.uuid4(),
        title="Solver Notes",
        background=None,
        source_revision=1,
        parse_status="unparsed",
        active_parse_run_id=None,
        last_error_code=None,
        last_error=None,
        created_time=datetime.now(UTC),
        updated_time=datetime.now(UTC),
    )
    db = _CreateTopicSession()
    monkeypatch.setattr(knowledge_service, "_get_owned_source", lambda *_args: source)
    monkeypatch.setattr(knowledge_service, "_list_source_files", lambda *_args: [])

    result = knowledge_service.update_source(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        source.id,
        knowledge_schemas.KnowledgeSourceUpdateRequest(
            background="  Vehicle routing domain\x00  ",
        ),
    )

    assert source.background == "Vehicle routing domain"
    assert source.source_revision == 2
    assert result.background == "Vehicle routing domain"


def test_parse_uses_the_topic_background_when_the_request_does_not_override_it() -> None:
    resolve_background = getattr(knowledge_service, "resolve_parse_background", None)

    assert resolve_background is not None
    assert resolve_background(None, "  Preserve domain terminology.  ") == (
        "Preserve domain terminology."
    )


def test_user_can_remove_the_last_source_file_without_deleting_the_topic(
    monkeypatch,
) -> None:
    source_id = uuid.uuid4()
    source_file = SimpleNamespace(id=uuid.uuid4(), source_id=source_id)
    source = SimpleNamespace(
        id=source_id,
        source_revision=1,
        parse_status="unparsed",
        active_parse_run_id=None,
        last_error_code=None,
        last_error=None,
        updated_time=None,
    )
    db = _CreateTopicSession()
    dispatched = []
    monkeypatch.setattr(
        knowledge_service,
        "_get_owned_source_file",
        lambda *_args: source_file,
    )
    monkeypatch.setattr(
        knowledge_service,
        "_get_owned_source_for_update",
        lambda *_args: source,
    )
    monkeypatch.setattr(
        knowledge_service,
        "_list_source_files",
        lambda *_args: [source_file],
    )
    monkeypatch.setattr(knowledge_service, "ensure_source_deletable", lambda *_args: None)
    monkeypatch.setattr(
        knowledge_service.knowledge_cleanup,
        "run_or_schedule_cleanup",
        lambda job_id: dispatched.append(job_id),
    )

    knowledge_service.delete_source_file(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        source_file.id,
    )

    assert db.deleted == [source_file]
    assert source.source_revision == 2
    cleanup_jobs = [item for item in db.added if isinstance(item, models.KnowledgeCleanupJob)]
    assert len(cleanup_jobs) == 1
    assert dispatched == [cleanup_jobs[0].id]


def test_validate_parser_manifest_accepts_main_and_children() -> None:
    manifest = knowledge_service.validate_parser_manifest(
        {
            "main": {"title": "算法经验总览", "path": "main.md"},
            "children": [
                {"title": "约束条件", "path": "children/constraints.md"},
                {"title": "评估方法", "path": "children/evaluation.md"},
            ],
        }
    )

    assert manifest.main.path == "main.md"
    assert [item.path for item in manifest.children] == [
        "children/constraints.md",
        "children/evaluation.md",
    ]


def test_validate_parser_manifest_accepts_only_global_memory_card_types() -> None:
    manifest = knowledge_service.validate_parser_manifest(
        {
            "cards": [
                {
                    "type": "good_algorithm",
                    "title": "优先保留可行解",
                    "content": "搜索过程中始终保留当前最优可行解，避免后续扰动造成回退。",
                    "tags": ["search", "feasible"],
                },
                {
                    "type": "error_reflection",
                    "title": "区分执行失败与低分结果",
                    "content": "执行器异常时不要把占位分数当成有效评估结果。",
                    "tags": ["evaluation"],
                },
                {
                    "type": "domain_knowledge",
                    "title": "容量约束",
                    "content": "每条路线的累计需求不得超过车辆容量。",
                    "tags": ["constraint"],
                },
                {
                    "type": "general_insight",
                    "title": "先验证再扩大搜索",
                    "content": "先用小规模样例验证评价器，再增加搜索预算。",
                    "tags": [],
                },
            ]
        }
    )

    assert [item.type for item in manifest.cards] == [
        "good_algorithm",
        "error_reflection",
        "domain_knowledge",
        "general_insight",
    ]


def test_legacy_card_output_is_preserved_as_document_blocks_without_insertion(
    tmp_path,
    monkeypatch,
) -> None:
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        source_id=source_id,
        source_revision=1,
        status="running",
        progress=92,
        stage="persisting",
        message="",
        manifest_object_key=None,
        generated_memory_ids=[],
        error_code=None,
        error=None,
    )
    source = SimpleNamespace(
        id=source_id,
        user_id=user_id,
        source_revision=1,
        active_parse_run_id=None,
        parse_status="running",
        last_error_code=None,
        last_error=None,
        updated_time=None,
    )

    class ParserSession(_CreateTopicSession):
        def get(self, model, object_id):
            if model is models.KnowledgeParseRun and object_id == run_id:
                return run
            if model is models.KnowledgeSource and object_id == source_id:
                return source
            return None

    session = ParserSession()
    work_dir = tmp_path / "parser"
    (work_dir / "output").mkdir(parents=True)
    (work_dir / "output" / "manifest.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "type": "domain_knowledge",
                        "title": "闭环距离",
                        "content": "TSP 路径代价包含末节点返回起点的边。",
                        "tags": ["tsp"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    uploads = {}
    monkeypatch.setattr(
        knowledge_parser,
        "get_db_session",
        lambda: contextmanager(lambda: (yield session))(),
    )
    monkeypatch.setattr(
        knowledge_parser.storage,
        "upload",
        lambda key, data, **_kwargs: uploads.setdefault(key, data),
    )
    result = knowledge_parser._persist_parser_output(run_id, user_id, work_dir)

    assert result == "ready"
    assert run.generated_memory_ids == []
    assert run.stage == "review"
    documents = [item for item in session.added if isinstance(item, models.KnowledgeDocument)]
    assert len(documents) == 1
    assert documents[0].title == "闭环距离"
    assert uploads[documents[0].object_key].decode() == "TSP 路径代价包含末节点返回起点的边。"


def test_insert_selected_documents_uses_one_structured_batch(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    source = SimpleNamespace(id=uuid.uuid4(), source_revision=3)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source.id,
        source_revision=3,
        status=models.KnowledgeParseStatus.READY.value,
        inserted_document_ids=[],
        generated_memory_ids=[],
        message="预提取文档块已就绪",
        updated_time=None,
    )
    documents = [
        SimpleNamespace(
            id=uuid.uuid4(),
            source_id=source.id,
            parse_run_id=run.id,
            title="算法约束",
            object_key="knowledge/block-1.md",
            content_version=2,
            content_hash="a" * 64,
            sort_order=0,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            source_id=source.id,
            parse_run_id=run.id,
            title="失败反思",
            object_key="knowledge/block-2.md",
            content_version=1,
            content_hash="b" * 64,
            sort_order=1,
        ),
    ]
    db = _CreateTopicSession()
    calls = []
    monkeypatch.setattr(knowledge_service, "get_parse_run", lambda *_args: run)
    monkeypatch.setattr(knowledge_service, "_get_owned_source", lambda *_args: source)
    monkeypatch.setattr(
        knowledge_service,
        "_get_selected_run_documents",
        lambda *_args, **_kwargs: documents,
    )
    monkeypatch.setattr(
        knowledge_service.storage,
        "download",
        lambda key: {
            "knowledge/block-1.md": "容量约束必须完整保留。".encode(),
            "knowledge/block-2.md": "失败来源与修复方式必须完整保留。".encode(),
        }[key],
    )
    from app.services import memory_service

    monkeypatch.setattr(memory_service, "_require_mindmemos_memory_enabled", lambda: None)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda *_args: None)

    def fake_post(_user, path, payload, *, scopes):
        calls.append((path, payload, scopes))
        if path == "/v1/memory/list":
            return {
                "data": {
                    "memories": [
                        {
                            "id": "memory-1",
                            "memory": "完整保留容量约束。",
                            "entity_type": "llm4ad_memory_card",
                            "property_name": "domain_knowledge",
                            "entity_id": "entity-1",
                            "metadata": {"title": "容量约束"},
                        },
                        {
                            "id": "memory-2",
                            "memory": "保留失败来源和修复方式。",
                            "entity_type": "llm4ad_memory_card",
                            "property_name": "error_reflection",
                            "entity_id": "entity-2",
                            "metadata": {"title": "失败修复"},
                        },
                    ]
                }
            }
        return {
            "data": {
                "memories": [
                    {"memory_id": "memory-1"},
                    {"memory_id": "memory-2"},
                ]
            }
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    result = knowledge_service.insert_document_blocks(
        db,
        user,
        run.id,
        knowledge_schemas.KnowledgeDocumentInsertRequest(
            document_ids=[document.id for document in documents]
        ),
    )

    assert len(calls) == 3
    path, payload, scopes = calls[0]
    assert path == "/v1/memory/add"
    assert scopes == ["memory:write"]
    assert "messages" not in payload
    assert [item["block_id"] for item in payload["document_blocks"]] == [
        str(document.id) for document in documents
    ]
    assert payload["document_blocks"][0]["messages"][0]["content"] == "容量约束必须完整保留。"
    assert payload["document_blocks"][0]["metadata"]["knowledge_source_id"] == str(source.id)
    assert payload["idempotency_key"].startswith("llm4ad-knowledge-batch:")
    assert result.inserted_document_ids == [document.id for document in documents]
    assert result.generated_memory_ids == ["memory-1", "memory-2"]
    assert [card.id for card in result.generated_memories] == ["memory-1", "memory-2"]
    assert result.generated_memories[0].title == "容量约束"
    assert run.inserted_document_ids == [str(document.id) for document in documents]


@pytest.mark.asyncio
async def test_insert_selected_documents_streams_progress_before_committing(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    source = SimpleNamespace(id=uuid.uuid4(), source_revision=3)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source.id,
        source_revision=3,
        status=models.KnowledgeParseStatus.READY.value,
        inserted_document_ids=[],
        generated_memory_ids=[],
        message="预提取文档块已就绪",
        updated_time=None,
    )
    document = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source.id,
        parse_run_id=run.id,
        title="算法约束",
        object_key="knowledge/block-1.md",
        content_version=2,
        content_hash="a" * 64,
        sort_order=0,
    )
    db = _CreateTopicSession()
    calls = []
    monkeypatch.setattr(knowledge_service, "get_parse_run", lambda *_args: run)
    monkeypatch.setattr(knowledge_service, "_get_owned_source", lambda *_args: source)
    monkeypatch.setattr(
        knowledge_service,
        "_get_selected_run_documents",
        lambda *_args, **_kwargs: [document],
    )
    monkeypatch.setattr(
        knowledge_service.storage,
        "download",
        lambda _key: "容量约束必须完整保留。".encode(),
    )
    from app.services import memory_service

    monkeypatch.setattr(memory_service, "_require_mindmemos_memory_enabled", lambda: None)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda *_args: None)

    async def fake_stream(_user, path, payload, *, scopes):
        calls.append((path, payload, scopes))
        yield {
            "event": "progress",
            "stage": "episode_allocating",
            "message": "Collecting blocks into Episode backgrounds.",
            "percent": 35,
        }
        assert run.inserted_document_ids == []
        yield {
            "event": "completed",
            "data": {"memories": [{"memory_id": "memory-1"}]},
        }

    monkeypatch.setattr(memory_service, "_mindmemos_stream_post", fake_stream)

    def fake_post(_user, path, _payload, *, scopes):
        assert path == "/v1/memory/list"
        assert scopes == ["memory:read"]
        return {
            "data": {
                "memories": [
                    {
                        "id": "memory-1",
                        "memory": "完整保留容量约束。",
                        "entity_type": "llm4ad_memory_card",
                        "property_name": "domain_knowledge",
                        "entity_id": "entity-1",
                        "metadata": {"title": "容量约束"},
                    }
                ]
            }
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    events = [
        event
        async for event in knowledge_service.stream_insert_document_blocks(
            db,
            user,
            run.id,
            knowledge_schemas.KnowledgeDocumentInsertRequest(document_ids=[document.id]),
        )
    ]

    assert calls[0][0] == "/v1/memory/add/stream"
    assert calls[0][2] == ["memory:write"]
    assert events[0]["stage"] == "episode_allocating"
    assert events[-1] == {
        "event": "completed",
        "data": {
            "inserted_document_ids": [str(document.id)],
            "generated_memory_ids": ["memory-1"],
            "generated_memories": [
                {
                    "id": "memory-1",
                    "type": "domain_knowledge",
                    "title": "容量约束",
                    "content": "完整保留容量约束。",
                    "structured_content": None,
                    "enabled": True,
                    "source": "mindmemos",
                    "tags": ["容量约束"],
                    "score": None,
                    "generation": None,
                    "algorithm_id": None,
                    "metadata": {
                        "title": "容量约束",
                        "entity_id": "entity-1",
                        "entity_type": "llm4ad_memory_card",
                        "property_name": "domain_knowledge",
                    },
                    "readonly": {
                        "source": "mindmemos",
                        "status": "active",
                        "entity_name": None,
                        "property_name": "domain_knowledge",
                        "property_time": None,
                        "last_update_at": None,
                        "event_time": None,
                        "source_timestamp": None,
                    },
                }
            ],
        },
    }
    assert run.inserted_document_ids == [str(document.id)]


def test_generated_memories_can_be_rehydrated_after_refresh(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    run_id = uuid.uuid4()
    run = SimpleNamespace(generated_memory_ids=["deleted-memory", "memory-1"])
    monkeypatch.setattr(knowledge_service, "get_parse_run", lambda *_args: run)

    from app.services import memory_service

    def fake_post(_user, path, payload, *, scopes):
        assert path == "/v1/memory/list"
        assert scopes == ["memory:read"]
        requested_ids = ((payload.get("filters") or {}).get("memory_id") or {}).get("in")
        if requested_ids:
            assert requested_ids == ["deleted-memory", "memory-1"]
            return {
                "data": {
                    "memories": [
                        {
                            "id": "memory-1",
                            "memory": "刷新后仍可查看。",
                            "entity_type": "llm4ad_memory_card",
                            "property_name": "general_insight",
                            "entity_id": "entity-1",
                            "metadata": {"title": "刷新验证"},
                        }
                    ]
                }
            }
        return {"data": {"memories": []}}

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    cards = knowledge_service.list_generated_memory_cards(
        _CreateTopicSession(),
        user,
        run_id,
    )

    assert [card.id for card in cards] == ["memory-1"]
    assert cards[0].title == "刷新验证"


def test_parser_output_is_saved_as_editable_document_blocks(
    tmp_path,
    monkeypatch,
) -> None:
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        source_id=source_id,
        source_revision=1,
        status="running",
        progress=92,
        stage="persisting",
        message="",
        manifest_object_key=None,
        generated_memory_ids=[],
        error_code=None,
        error=None,
        parse_mode="direct",
        plan_id=None,
        plan_strategy_id=None,
    )
    source = SimpleNamespace(
        id=source_id,
        user_id=user_id,
        source_revision=1,
        active_parse_run_id=None,
        parse_status="running",
        last_error_code=None,
        last_error=None,
        updated_time=None,
    )

    class ParserSession(_CreateTopicSession):
        def get(self, model, object_id):
            if model is models.KnowledgeParseRun and object_id == run_id:
                return run
            if model is models.KnowledgeSource and object_id == source_id:
                return source
            return None

    session = ParserSession()
    work_dir = tmp_path / "parser"
    output = work_dir / "output"
    (output / "documents").mkdir(parents=True)
    (output / "documents" / "block-001.md").write_text(
        "# 容量约束\n\n车辆总载荷不得超过容量上限。",
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "documents": [
                    {"title": "容量约束", "path": "documents/block-001.md"},
                ]
            }
        ),
        encoding="utf-8",
    )
    uploads = {}
    monkeypatch.setattr(
        knowledge_parser,
        "get_db_session",
        lambda: contextmanager(lambda: (yield session))(),
    )
    monkeypatch.setattr(
        knowledge_parser.storage,
        "upload",
        lambda key, data, **_kwargs: uploads.setdefault(key, data),
    )

    result = knowledge_parser._persist_parser_output(run_id, user_id, work_dir)

    documents = [item for item in session.added if isinstance(item, models.KnowledgeDocument)]
    assert result == "ready"
    assert len(documents) == 1
    assert documents[0].title == "容量约束"
    assert uploads[documents[0].object_key].startswith(b"# ")
    assert run.stage == "review"
    assert run.generated_memory_ids == []


def test_parse_plan_contract_exposes_candidate_document_counts() -> None:
    plan = knowledge_service.validate_parse_plan_payload(
        {
            "topic_summary": "保留算法约束、实现步骤和示例的高保真整理。",
            "source_overview": [
                {
                    "filename": "solver.md",
                    "summary": "求解器设计与评估说明",
                    "key_sections": ["约束", "伪代码", "评估"],
                }
            ],
            "recommended_strategy_id": "faithful-restructure",
            "strategies": [
                {
                    "id": "faithful-restructure",
                    "name": "高保真主题重组",
                    "description": "按主题拆分，避免压缩原始细节。",
                    "loss_level": "lossless",
                    "document_count": 2,
                    "documents": [
                        {
                            "title": "求解器总览",
                            "document_type": "main",
                            "purpose": "保留整体流程和跨章节关系",
                            "source_coverage": ["solver.md#约束", "solver.md#伪代码"],
                            "must_preserve": ["约束", "伪代码"],
                        },
                        {
                            "title": "评估方法",
                            "document_type": "child",
                            "purpose": "独立保留指标、参数和示例",
                            "source_coverage": ["solver.md#评估"],
                            "must_preserve": ["参数", "示例"],
                        },
                    ],
                    "deduplication_policy": "仅删除完全重复的段落。",
                },
                {
                    "id": "source-aligned",
                    "name": "按来源组织",
                    "description": "基本保持原文件边界。",
                    "loss_level": "light",
                    "document_count": 1,
                    "documents": [
                        {
                            "title": "solver.md 整理稿",
                            "document_type": "main",
                            "purpose": "保持来源顺序",
                            "source_coverage": ["solver.md#全部"],
                            "must_preserve": ["全部正文"],
                        }
                    ],
                    "deduplication_policy": "仅清理导航噪声。",
                },
            ],
        }
    )

    assert plan.recommended_strategy_id == "faithful-restructure"
    assert plan.strategies[0].document_count == 2
    assert len(plan.strategies[0].documents) == 2


def test_parse_plan_contract_accepts_a_dynamic_number_of_distinct_strategies() -> None:
    payload = _valid_parse_plan_payload()
    payload["strategies"] = [payload["strategies"][0]]

    single = knowledge_service.validate_parse_plan_payload(payload)

    assert [strategy.id for strategy in single.strategies] == ["faithful"]

    payload = _valid_parse_plan_payload()
    payload["strategies"].append(
        {
            **payload["strategies"][0],
            "id": "reference-manual",
            "name": "参考手册",
        }
    )

    multiple = knowledge_service.validate_parse_plan_payload(payload)

    assert len(multiple.strategies) == 3


def test_parse_plan_candidate_recovers_valid_json_before_trailing_prose() -> None:
    parser = getattr(knowledge_parser, "parse_plan_candidate_text", None)
    assert callable(parser)
    expected = _valid_parse_plan_payload()

    parsed = parser(json.dumps(expected, ensure_ascii=False) + "\n解析方案已经保存，接下来可以选择策略。")

    assert parsed.model_dump() == expected


def test_plan_persist_failure_exposes_checkpoint_retry_action() -> None:
    resolver = getattr(knowledge_service, "parse_plan_retry_action", None)
    assert callable(resolver)

    action = resolver(
        SimpleNamespace(
            status="failed",
            error_code="plan_persist_failed",
            plan_object_key="knowledge/user/source/plans/plan/plan.json",
        )
    )

    assert action == "persist"


def test_plan_generation_only_accepts_collaborative_plan_mode() -> None:
    assert knowledge_schemas.KnowledgeParsePlanCreateRequest().interaction_mode == "collaborative"
    assert (
        models.KnowledgeParsePlan(
            source_id=uuid.uuid4(),
            source_revision=1,
        ).interaction_mode
        == "collaborative"
    )
    with pytest.raises(ValueError):
        knowledge_schemas.KnowledgeParsePlanCreateRequest(interaction_mode="quick")


def test_plan_question_answers_must_match_the_pending_question() -> None:
    validator = getattr(knowledge_service, "validate_plan_question_answers", None)
    assert callable(validator)
    pending = {
        "question_id": "question-1",
        "questions": [
            {
                "question": "希望如何组织示例？",
                "header": "示例组织",
                "options": [
                    {"label": "集中整理", "description": "放入单独文档"},
                    {"label": "原位保留", "description": "保留在对应章节"},
                ],
                "multiSelect": False,
            }
        ],
    }

    answers = validator(
        pending,
        knowledge_schemas.KnowledgeParsePlanAnswerRequest(
            question_id="question-1",
            answers={"希望如何组织示例？": "原位保留"},
        ),
    )

    assert answers == {"希望如何组织示例？": "原位保留"}
    with pytest.raises(HTTPException, match="已失效"):
        validator(
            pending,
            knowledge_schemas.KnowledgeParsePlanAnswerRequest(
                question_id="stale-question",
                answers={"希望如何组织示例？": "原位保留"},
            ),
        )


def test_pending_parse_plan_response_exposes_interaction_without_duplicate_defaults() -> None:
    plan = models.KnowledgeParsePlan(
        source_id=uuid.uuid4(),
        source_revision=1,
        source_snapshot=[],
        interaction_mode="collaborative",
    )

    response = knowledge_service._plan_response(plan, 1)

    assert response.status == "pending"
    assert response.interaction_mode == "collaborative"
    assert response.retryable is False


def test_parse_plan_contract_rejects_mismatched_document_count() -> None:
    with pytest.raises(ValueError, match="document_count"):
        knowledge_service.validate_parse_plan_payload(
            {
                "topic_summary": "主题",
                "source_overview": [],
                "recommended_strategy_id": "faithful-restructure",
                "strategies": [
                    {
                        "id": "faithful-restructure",
                        "name": "高保真主题重组",
                        "description": "保留细节",
                        "loss_level": "lossless",
                        "document_count": 2,
                        "documents": [
                            {
                                "title": "总览",
                                "document_type": "main",
                                "purpose": "总览",
                                "source_coverage": ["source.md#全部"],
                                "must_preserve": ["全部正文"],
                            }
                        ],
                        "deduplication_policy": "仅删除完全重复内容",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "path",
    ["../secret.md", "/etc/passwd", "children/not-markdown.txt", "main.md/child"],
)
def test_validate_parser_manifest_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        knowledge_service.validate_parser_manifest({"main": {"title": "Unsafe", "path": path}, "children": []})


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_build_task_knowledge_cards_pins_requested_content_versions(monkeypatch) -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    rows = {
        first_id: SimpleNamespace(
            id=first_id,
            title="Main guide",
            content_version=3,
            object_key="knowledge/u/s/parses/r/main.v3.md",
            source_id=uuid.uuid4(),
        ),
        second_id: SimpleNamespace(
            id=second_id,
            title="Constraints",
            content_version=1,
            object_key="knowledge/u/s/parses/r/children/c.v1.md",
            source_id=uuid.uuid4(),
        ),
    }

    monkeypatch.setattr(
        knowledge_service,
        "_get_owned_document",
        lambda _db, _user_id, document_id: rows[document_id],
    )
    monkeypatch.setattr(
        knowledge_service.storage,
        "download",
        lambda key: {
            rows[first_id].object_key: b"# Main\nUse stable seeds.",
            rows[second_id].object_key: b"# Constraints\nNo network.",
        }[key],
    )

    cards = knowledge_service.build_task_knowledge_cards(
        None,
        user_id=uuid.uuid4(),
        refs=[
            {"document_id": str(first_id), "content_version": 3},
            {"document_id": str(second_id), "content_version": 1},
        ],
    )

    assert [card["title"] for card in cards] == ["Main guide", "Constraints"]
    assert cards[0]["content"] == "# Main\nUse stable seeds."
    assert cards[0]["metadata"]["content_version"] == 3


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_build_task_knowledge_cards_keeps_pinned_older_version(monkeypatch) -> None:
    document_id = uuid.uuid4()
    row = SimpleNamespace(
        id=document_id,
        title="Changed",
        content_version=4,
        object_key="knowledge/u/s/parses/r/documents/d/v4.md",
        source_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        knowledge_service,
        "_get_owned_document",
        lambda _db, _user_id, _document_id: row,
    )

    monkeypatch.setattr(
        knowledge_service.storage,
        "download",
        lambda key: b"# Version 3" if key.endswith("/v3.md") else b"# Version 4",
    )

    cards = knowledge_service.build_task_knowledge_cards(
        None,
        user_id=uuid.uuid4(),
        refs=[{"document_id": str(document_id), "content_version": 3}],
    )

    assert cards[0]["content"] == "# Version 3"
    assert cards[0]["metadata"]["content_version"] == 3


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_task_knowledge_cards_include_estimated_token_count(monkeypatch) -> None:
    document_id = uuid.uuid4()
    row = SimpleNamespace(
        id=document_id,
        title="Constraints",
        content_version=1,
        object_key="knowledge/u/s/parses/r/documents/d/v1.md",
        source_id=uuid.uuid4(),
        estimated_tokens=7,
    )
    monkeypatch.setattr(
        knowledge_service,
        "_get_owned_document",
        lambda _db, _user_id, _document_id: row,
    )
    monkeypatch.setattr(knowledge_service.storage, "download", lambda _key: "约束条件".encode())

    cards = knowledge_service.build_task_knowledge_cards(
        None,
        user_id=uuid.uuid4(),
        refs=[{"document_id": str(document_id), "content_version": 1}],
    )

    expected = knowledge_service.estimate_knowledge_tokens("约束条件")
    assert cards[0]["estimated_tokens"] == expected
    assert cards[0]["metadata"]["estimated_tokens"] == expected


def test_token_estimate_belongs_to_parsed_documents_not_source_files() -> None:
    assert "estimated_tokens" in models.KnowledgeDocument.model_fields
    assert "estimated_tokens" not in models.KnowledgeSourceFile.model_fields


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_selected_documents_are_injected_without_enabling_task_memory() -> None:
    memory = Memory(
        {
            "include_task_memory": False,
            "knowledge_documents": [
                {
                    "title": "Solver constraints",
                    "content": "Use deterministic seeds and keep feasibility.",
                }
            ],
        }
    )

    context = memory.get_prompt_context()

    assert "# Selected Document Knowledge" in context
    assert "## Solver constraints" in context
    assert "Use deterministic seeds" in context


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_selected_document_injection_emits_metadata_without_document_bodies() -> None:
    events: list[dict] = []
    sink_id = logger.add(lambda message: events.append(dict(message.record["extra"])))
    try:
        memory = Memory(
            {
                "include_task_memory": False,
                "knowledge_documents": [
                    {
                        "document_id": str(uuid.uuid4()),
                        "title": "Solver constraints",
                        "content": "Sensitive full document body",
                        "estimated_tokens": 7,
                    }
                ],
            }
        )
        memory.get_prompt_context()
    finally:
        logger.remove(sink_id)

    event = next(
        item for item in events if item.get("event_type") == "knowledge_documents_injected"
    )
    assert event["document_count"] == 1
    assert event["document_titles"] == ["Solver constraints"]
    assert event["estimated_tokens"] == 7
    assert "Sensitive full document body" not in json.dumps(event)


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_selected_documents_are_reloaded_from_runtime_file(tmp_path) -> None:
    memory = Memory(
        {
            "include_task_memory": False,
            "knowledge_documents": [{"title": "Initial", "content": "Initial body"}],
        }
    )
    memory.set_memory_dir(tmp_path)
    runtime_path = tmp_path / "pinned_knowledge.json"
    runtime_path.write_text(
        json.dumps(
            {
                "revision": 2,
                "documents": [
                    {
                        "document_id": str(uuid.uuid4()),
                        "content_version": 1,
                        "title": "Updated",
                        "content": "Updated body",
                        "estimated_tokens": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    context = memory.get_prompt_context()

    assert "## Updated" in context
    assert "Updated body" in context
    assert "Initial body" not in context


def test_parser_manifest_accepts_flat_documents() -> None:
    manifest = knowledge_service.validate_parser_manifest(
        {
            "documents": [
                {"title": "Overview", "path": "documents/overview.md"},
                {"title": "Constraints", "path": "documents/constraints.md"},
            ]
        }
    )

    assert [item.title for item in manifest.documents] == ["Overview", "Constraints"]


@pytest.mark.skip(reason="task-level knowledge document picker was removed")
def test_picker_paginates_knowledge_topics_and_returns_selected_metadata() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    source = models.KnowledgeSource(
        user_id=user.id,
        title="Routing knowledge",
        parse_status=models.KnowledgeParseStatus.READY.value,
    )
    run_id = uuid.uuid4()
    source.active_parse_run_id = run_id
    document = models.KnowledgeDocument(
        source_id=source.id,
        parse_run_id=run_id,
        parent_id=None,
        document_type=models.KnowledgeDocumentType.DOCUMENT.value,
        title="Constraints",
        object_key="knowledge/u/s/parses/r/documents/d/v1.md",
        content_hash="0" * 64,
        content_size=40,
        estimated_tokens=10,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def one(self):
            return self.value

        def all(self):
            return self.value

    class _Session:
        def __init__(self):
            self.results = iter(
                [
                    _Result(3),
                    _Result([source]),
                    _Result([(document, source.title)]),
                    _Result([(document, source.title)]),
                ]
            )

        def exec(self, _statement):
            return next(self.results)

    result = knowledge_service.list_picker_documents(
        _Session(),
        user,
        skip=0,
        limit=10,
        search="constraint",
        selected_document_ids=[document.id],
    )

    assert result.total == 3
    assert result.items[0].id == source.id
    assert result.items[0].documents[0].id == document.id
    assert result.selected_documents[0].estimated_tokens == 10


def test_parse_plan_accepts_flat_document_descriptions() -> None:
    payload = _valid_parse_plan_payload()
    for strategy in payload["strategies"]:
        for document in strategy["documents"]:
            document.pop("document_type")

    parsed = knowledge_service.validate_parse_plan_payload(payload)

    assert parsed.strategies[0].documents[0].title == "求解器总览"


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_runtime_resolution_does_not_mutate_persisted_memory_config(monkeypatch) -> None:
    document_id = uuid.uuid4()
    persisted_memory = {"knowledge_document_refs": [{"document_id": str(document_id), "content_version": 1}]}
    input_args = {"memory": persisted_memory}
    monkeypatch.setattr(
        knowledge_service,
        "build_task_knowledge_cards",
        lambda *_args, **_kwargs: [{"title": "Resolved", "content": "Body"}],
    )

    _apply_knowledge_runtime_config(
        None,
        input_args,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert input_args["memory"] is not persisted_memory
    assert "knowledge_documents" in input_args["memory"]
    assert "knowledge_documents" not in persisted_memory


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_runtime_knowledge_selection_is_written_and_saved_for_rerun(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.task_service import pinned_knowledge

    user = SimpleNamespace(id=uuid.uuid4())
    task_id = uuid.uuid4()
    document_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        input_args={"memory": {"enabled": True}, "max_generations": 10},
    )

    class _Session:
        committed = False

        def add(self, _value):
            return None

        def commit(self):
            self.committed = True

    db = _Session()
    monkeypatch.setattr(pinned_knowledge, "get_task_with_auth", lambda *_args: task)
    monkeypatch.setattr(pinned_knowledge.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    monkeypatch.setattr(
        knowledge_service,
        "build_task_knowledge_cards",
        lambda *_args, **_kwargs: [
            {
                "document_id": str(document_id),
                "source_id": str(uuid.uuid4()),
                "title": "Constraints",
                "content": "Keep the solution feasible.",
                "content_version": 2,
                "estimated_tokens": 6,
                "metadata": {},
            }
        ],
    )

    selection = pinned_knowledge.set_task_pinned_knowledge(
        db,
        task_id,
        user,
        [{"document_id": str(document_id), "content_version": 2}],
    )

    runtime_path = (
        tmp_path
        / f"code_user-{user.id}"
        / ".task_runtime"
        / str(task_id)
        / "memory"
        / "pinned_knowledge.json"
    )
    payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert payload["documents"][0]["title"] == "Constraints"
    assert selection["total_estimated_tokens"] == 6
    assert task.input_args["memory"]["knowledge_document_refs"] == [
        {"document_id": str(document_id), "content_version": 2}
    ]
    assert task.input_args["max_generations"] == 10
    assert db.committed is True


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_deleted_documents_are_removed_from_live_runtime_selection(monkeypatch, tmp_path) -> None:
    from app.services.task_service import pinned_knowledge

    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    deleted_document_id = uuid.uuid4()
    kept_document_id = uuid.uuid4()
    monkeypatch.setattr(pinned_knowledge.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    runtime_path = pinned_knowledge.task_knowledge_runtime_path_for_ids(task_id, user_id)
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        json.dumps(
            {
                "revision": 4,
                "documents": [
                    {
                        "document_id": str(deleted_document_id),
                        "title": "Deleted",
                        "estimated_tokens": 8,
                    },
                    {
                        "document_id": str(kept_document_id),
                        "title": "Kept",
                        "estimated_tokens": 5,
                    },
                ],
                "total_estimated_tokens": 13,
            }
        ),
        encoding="utf-8",
    )

    pinned_knowledge.scrub_deleted_runtime_documents(
        user_id=user_id,
        task_ids=[task_id],
        document_ids={deleted_document_id},
    )

    payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert payload["revision"] == 5
    assert [item["document_id"] for item in payload["documents"]] == [str(kept_document_id)]
    assert payload["total_estimated_tokens"] == 5


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_evolution_container_mounts_stable_knowledge_runtime(monkeypatch, tmp_path) -> None:
    from app.services import evolution_runner
    from app.services.task_service import pinned_knowledge

    monkeypatch.setattr(
        evolution_runner,
        "resolve_host_path",
        lambda value: str(value),
    )
    monkeypatch.setattr(pinned_knowledge.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    task_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run_dir = tmp_path / f"code_user-{user_id}" / str(task_id)

    spec = evolution_runner._build_spec(
        {
            "task_id": task_id,
            "user_id": user_id,
            "run_dir": str(run_dir),
        },
        "secret",
    )

    stable_dir = tmp_path / f"code_user-{user_id}" / ".task_runtime" / str(task_id) / "memory"
    assert spec.mounts[str(stable_dir)] == "/task/knowledge-runtime"
    assert spec.env["LLM4AD_KNOWLEDGE_RUNTIME_DIR"] == "/task/knowledge-runtime"


def test_knowledge_topic_has_multiple_versioned_source_files() -> None:
    source_file_model = getattr(models, "KnowledgeSourceFile", None)

    assert source_file_model is not None
    first = source_file_model(
        source_id=uuid.uuid4(),
        original_filename="intro.md",
        content_version=1,
        object_key="knowledge/u/topic/sources/f/v1.md",
        content_hash="0" * 64,
        content_size=10,
        sort_order=0,
    )
    second = source_file_model(
        source_id=first.source_id,
        original_filename="constraints.md",
        content_version=2,
        object_key="knowledge/u/topic/sources/g/v2.md",
        content_hash="1" * 64,
        content_size=20,
        sort_order=1,
    )

    assert first.source_id == second.source_id
    assert [first.original_filename, second.original_filename] == [
        "intro.md",
        "constraints.md",
    ]


def test_markdown_batch_validation_keeps_all_files_and_rejects_duplicate_names() -> None:
    validator = getattr(knowledge_service, "validate_markdown_batch", None)
    assert callable(validator)

    batch = validator(
        [
            ("intro.md", b"# Intro\nOverview"),
            ("constraints.markdown", b"# Constraints\nBe deterministic"),
        ]
    )

    assert [item.filename for item in batch] == ["intro.md", "constraints.markdown"]
    with pytest.raises(HTTPException, match="同名"):
        validator([("same.md", b"one"), ("same.md", b"two")])


def test_parser_binding_requires_an_accessible_provider_and_declared_model() -> None:
    validator = getattr(knowledge_service, "validate_parser_binding", None)
    assert callable(validator)
    user_id = uuid.uuid4()
    provider = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        is_builtin=False,
        visible_to_all=False,
        type=models.ProviderType.OPENAI,
        model="claude-sonnet-4-5;claude-opus-4-1",
    )

    assert validator(provider, user_id, "claude-sonnet-4-5") is provider
    with pytest.raises(HTTPException, match="模型"):
        validator(provider, user_id, "missing-model")


def test_parser_binding_keeps_context_and_output_limits_independent() -> None:
    request = knowledge_schemas.KnowledgeParserBindingUpdate(
        provider_id=uuid.uuid4(),
        model_name="gpt-5",
        context_window_tokens=400_000,
        max_output_tokens=128_000,
    )

    assert request.context_window_tokens == 400_000
    assert request.max_output_tokens == 128_000

    with pytest.raises(ValueError, match="max_output_tokens"):
        knowledge_schemas.KnowledgeParserBindingUpdate(
            provider_id=uuid.uuid4(),
            model_name="small-model",
            context_window_tokens=8_192,
            max_output_tokens=16_384,
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("credit balance is too low", "quota_exceeded"),
        ("401 invalid api key", "authentication_failed"),
        ("model claude-x does not exist", "model_unavailable"),
        ("429 rate limit exceeded", "rate_limited"),
        ("503 system memory overloaded", "upstream_overloaded"),
        ("request timed out", "request_timeout"),
        ("user_response_timeout: no answer was received", "user_response_timeout"),
        (
            "max_tokens is too large: 32000; this model supports at most 16384 completion tokens",
            "model_output_limit_exceeded",
        ),
        (
            "Claude's response exceeded the 6000 output token maximum",
            "model_output_limit_exceeded",
        ),
        ("max_output_tokens", "model_output_limit_exceeded"),
        (
            "Claude Code returned an error result: Reached maximum number of turns (3)",
            "agent_turn_limit_exceeded",
        ),
        (
            "Claude Agent SDK did not generate a complete manifest.json and main.md",
            "invalid_parser_output",
        ),
        ("Extra data: line 126 column 2", "invalid_parse_plan"),
        (
            "--dangerously-skip-permissions cannot be used with root/sudo privileges",
            "parser_runtime_failed",
        ),
        (
            "ENOENT: no such file or directory, open '/workspace/.parser-runtime/.claude/settings.json'",
            "parser_runtime_failed",
        ),
        ("cc-switch protocol adapter command not found", "protocol_adapter_failed"),
        ("cc-switch command failed", "protocol_adapter_failed"),
        ("unexpected parser exit", "parser_failed"),
    ],
)
def test_parser_failures_are_classified_for_user_facing_feedback(raw: str, expected: str) -> None:
    classifier = getattr(knowledge_parser, "classify_parser_failure", None)
    assert callable(classifier)

    code, message = classifier(raw)

    assert code == expected
    assert message
    assert "sk-" not in message


def test_parser_output_tokens_follow_the_bound_provider_limit() -> None:
    resolver = getattr(knowledge_parser, "effective_parser_output_tokens", None)
    assert callable(resolver)

    assert resolver(16384) == 16384
    assert resolver(8192) == 8192
    assert resolver(None) == 32000


def test_plan_generation_has_an_independent_small_output_budget() -> None:
    token_resolver = getattr(knowledge_parser, "effective_parser_output_tokens", None)
    assert callable(token_resolver)

    assert token_resolver(16384, job_mode="plan") == 16384
    assert token_resolver(4096, job_mode="plan") == 4096
    assert token_resolver(None, job_mode="plan") == 32000
    assert token_resolver(16384, job_mode="execute") == 16384


def test_parser_container_receives_separate_context_and_output_limits(tmp_path) -> None:
    spec = knowledge_parser._make_container_spec(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        work_dir=tmp_path,
        proxy_token="ephemeral-token",
        model="gpt-5",
        upstream_api_format="openai_chat",
        context_window_tokens=400_000,
        max_output_tokens=128_000,
        job_mode="plan",
    )

    assert spec.env["KNOWLEDGE_MODEL_CONTEXT_TOKENS"] == "400000"
    assert spec.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"


def test_parser_progress_payload_preserves_only_safe_step_metadata() -> None:
    sanitizer = getattr(knowledge_parser, "parser_progress_payload", None)
    assert callable(sanitizer)

    payload = sanitizer(
        {
            "type": "step",
            "progress": 24,
            "stage": "analyzing",
            "message": "正在读取原始文档",
            "step_id": "tool-read-1",
            "step_kind": "tool",
            "step_status": "running",
            "tool_name": "Read",
            "elapsed_seconds": 12.4,
            "step_detail": "input/documents/source.md",
            "input": {"file_path": "/private/source.md"},
            "content": "secret document text",
        }
    )

    assert payload == {
        "type": "step",
        "progress": 24,
        "stage": "analyzing",
        "message": "正在读取原始文档",
        "step_id": "tool-read-1",
        "step_kind": "tool",
        "step_status": "running",
        "tool_name": "Read",
        "step_detail": "input/documents/source.md",
        "elapsed_seconds": 12,
    }
    assert "input" not in payload
    assert "content" not in payload

    unsafe = sanitizer(
        {
            "type": "step",
            "progress": 24,
            "stage": "analyzing",
            "message": "reading",
            "step_id": "unsafe-read",
            "step_kind": "tool",
            "step_status": "running",
            "tool_name": "Read",
            "step_detail": ".parser-runtime/provider.json",
        }
    )
    assert "step_detail" not in unsafe


def test_parser_progress_payload_keeps_context_compaction_steps() -> None:
    payload = knowledge_parser.parser_progress_payload(
        {
            "type": "step",
            "progress": 46,
            "stage": "compacting",
            "message": "上下文接近模型上限，正在压缩后继续解析",
            "step_id": "context-compaction-1",
            "step_kind": "context",
            "step_status": "running",
        }
    )

    assert payload["step_kind"] == "context"
    assert payload["step_id"] == "context-compaction-1"


def test_parser_progress_payload_keeps_structured_output_without_payload() -> None:
    payload = knowledge_parser.parser_progress_payload(
        {
            "type": "step",
            "progress": 84,
            "stage": "verifying",
            "message": "结构化方案已生成，正在校验并保存",
            "step_id": "structured-1",
            "step_kind": "tool",
            "step_status": "success",
            "tool_name": "StructuredOutput",
            "step_detail": "private generated payload",
            "content": "private generated payload",
        }
    )

    assert payload["tool_name"] == "StructuredOutput"
    assert "step_detail" not in payload
    assert "content" not in payload


@pytest.mark.parametrize("parse_status", ["pending", "running"])
def test_parsing_knowledge_source_cannot_be_deleted(parse_status: str) -> None:
    guard = getattr(knowledge_service, "ensure_source_deletable", None)
    assert callable(guard)

    with pytest.raises(HTTPException, match="正在解析") as exc_info:
        guard(None, SimpleNamespace(parse_status=parse_status))

    assert exc_info.value.status_code == 409


def test_terminal_knowledge_source_can_be_deleted() -> None:
    guard = getattr(knowledge_service, "ensure_source_deletable", None)
    assert callable(guard)

    class _Result:
        def first(self):
            return None

    class _Session:
        def exec(self, _statement):
            return _Result()

    guard(_Session(), SimpleNamespace(id=uuid.uuid4(), parse_status="failed"))


def test_source_edit_keeps_active_parse_status_until_run_finishes() -> None:
    source = SimpleNamespace(
        source_revision=1,
        parse_status="running",
        active_parse_run_id=uuid.uuid4(),
        last_error_code=None,
        last_error=None,
        updated_time=None,
    )

    knowledge_service._mark_source_changed(source)

    assert source.source_revision == 2
    assert source.parse_status == "running"


def test_parse_background_removes_unsafe_control_characters() -> None:
    normalizer = getattr(knowledge_service, "normalize_parse_background", None)
    assert callable(normalizer)

    assert normalizer("  Domain context\x00\x07\nKeep examples\tvisible  ") == (
        "Domain context\nKeep examples\tvisible"
    )
    assert normalizer(None) == ""


def test_user_can_cancel_a_running_parse_plan(monkeypatch) -> None:
    plan_id = uuid.uuid4()
    source_id = uuid.uuid4()
    plan = SimpleNamespace(
        id=plan_id,
        source_id=source_id,
        status="running",
        progress=70,
        stage="analyzing",
        message="正在分析",
        container_id="parser-container",
        error_code=None,
        error=None,
        updated_time=None,
    )
    db = _CreateTopicSession()
    stopped = []
    events = []
    monkeypatch.setattr(knowledge_service, "_get_owned_parse_plan", lambda *_args: plan)
    monkeypatch.setattr(
        knowledge_service,
        "_stop_knowledge_job_runtime",
        lambda job_id, container_id: stopped.append((job_id, container_id)),
        raising=False,
    )
    monkeypatch.setattr(
        "app.core.redis.push_knowledge_parse_event",
        lambda job_id, event: events.append((job_id, event)),
    )

    result = knowledge_service.cancel_parse_plan(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        plan_id,
    )

    assert result.status == "cancelled"
    assert plan.status == "cancelled"
    assert plan.stage == "cancelled"
    assert stopped == [(plan_id, "parser-container")]
    assert events[-1][1]["type"] == "cancelled"


def test_user_can_cancel_a_running_parse_run_and_restore_source_state(monkeypatch) -> None:
    run_id = uuid.uuid4()
    source_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        source_id=source_id,
        status="running",
        progress=40,
        stage="analyzing",
        message="正在分析",
        container_id="parser-container",
        error_code=None,
        error=None,
        updated_time=None,
    )
    source = SimpleNamespace(
        id=source_id,
        source_revision=1,
        parse_status="running",
        active_parse_run_id=None,
        last_error_code=None,
        last_error=None,
        updated_time=None,
    )
    db = _CreateTopicSession()
    monkeypatch.setattr(knowledge_service, "get_parse_run", lambda *_args: run)
    monkeypatch.setattr(knowledge_service, "_get_owned_source", lambda *_args: source)
    monkeypatch.setattr(knowledge_service, "_stop_knowledge_job_runtime", lambda *_args: None, raising=False)
    monkeypatch.setattr("app.core.redis.push_knowledge_parse_event", lambda *_args: None)

    result = knowledge_service.cancel_parse_run(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        run_id,
    )

    assert result.status == "cancelled"
    assert run.status == "cancelled"
    assert source.parse_status == "unparsed"


def test_resumable_parser_workspace_is_kept_after_success_for_refinement(tmp_path) -> None:
    session_path = tmp_path / ".parser-runtime" / "session-id"
    session_path.parent.mkdir(parents=True)
    session_path.write_text("session-123", encoding="utf-8")

    assert knowledge_parser.should_preserve_parser_workspace(tmp_path)


def test_ready_parse_run_can_create_a_refinement_on_the_same_session(monkeypatch) -> None:
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    parent = SimpleNamespace(
        id=parent_id,
        source_id=source_id,
        source_revision=3,
        source_snapshot=[{"id": str(uuid.uuid4()), "object_key": "source.md"}],
        status="ready",
        parser_provider_id=provider_id,
        parser_provider_name="Provider",
        parser_model="model-a",
        plan_id=None,
        plan_strategy_id=None,
        session_owner_kind="run",
        session_owner_id=parent_id,
    )
    source = SimpleNamespace(
        id=source_id,
        source_revision=3,
        parse_status="ready",
        active_parse_run_id=parent_id,
        last_error_code=None,
        last_error=None,
        updated_time=None,
    )
    provider = SimpleNamespace(id=provider_id, name="Provider")
    db = _CreateTopicSession()
    queued = []
    contexts = []

    monkeypatch.setattr(knowledge_service, "get_parse_run", lambda *_args: parent)
    monkeypatch.setattr(knowledge_service, "_get_owned_source", lambda *_args: source)
    monkeypatch.setattr(knowledge_service, "_require_resumable_parser_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        knowledge_service,
        "_pinned_parser_provider",
        lambda *_args: (provider, "model-a", 128_000, 16_384),
    )
    monkeypatch.setattr(
        knowledge_service,
        "_issue_parser_token",
        lambda **_kwargs: ("proxy-token", "openai_chat"),
    )
    monkeypatch.setattr(
        "app.core.redis.store_knowledge_parse_context",
        lambda *args: contexts.append(args),
    )
    monkeypatch.setattr(
        "app.tasks.knowledge_parser.run_knowledge_parse.apply_async",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    refinement = knowledge_service.refine_parse_run(
        db,
        SimpleNamespace(id=user_id),
        parent_id,
        knowledge_schemas.KnowledgeParseRefineRequest(
            instruction="保留全部内容，但补充每个代码示例的适用条件。",
        ),
        "access-token",
    )

    assert refinement.parent_run_id == parent_id
    assert refinement.session_owner_kind == "run"
    assert refinement.session_owner_id == parent_id
    assert refinement.parse_mode == "refine"
    assert source.active_parse_run_id == parent_id
    assert source.parse_status == "pending"
    assert contexts
    assert queued


def test_parse_run_workspace_follows_its_session_owner(tmp_path, monkeypatch) -> None:
    user_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    run_id = uuid.uuid4()
    monkeypatch.setattr(knowledge_parser.settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    plan_workspace = knowledge_parser.parse_run_workspace(
        user_id,
        SimpleNamespace(
            id=run_id,
            session_owner_kind="plan",
            session_owner_id=plan_id,
        ),
    )
    run_workspace = knowledge_parser.parse_run_workspace(
        user_id,
        SimpleNamespace(
            id=run_id,
            session_owner_kind="run",
            session_owner_id=run_id,
        ),
    )

    assert plan_workspace == tmp_path / f"code_user-{user_id}" / "knowledge_plan" / str(plan_id)
    assert run_workspace == tmp_path / f"code_user-{user_id}" / "knowledge_parse" / str(run_id)


def test_refinement_may_adjust_the_original_plan_document_shape() -> None:
    assert knowledge_parser.should_enforce_plan_document_count(
        SimpleNamespace(parse_mode="planned", plan_id=uuid.uuid4(), plan_strategy_id="faithful")
    )
    assert not knowledge_parser.should_enforce_plan_document_count(
        SimpleNamespace(parse_mode="refine", plan_id=uuid.uuid4(), plan_strategy_id="faithful")
    )


def test_failed_refinement_keeps_the_previous_result_active() -> None:
    source = SimpleNamespace(active_parse_run_id=uuid.uuid4())

    assert (
        knowledge_parser.source_status_after_run_failure(
            SimpleNamespace(parse_mode="refine"),
            source,
        )
        == "ready"
    )
    assert (
        knowledge_parser.source_status_after_run_failure(
            SimpleNamespace(parse_mode="direct"),
            source,
        )
        == "failed"
    )


@pytest.mark.skip(reason="task-level knowledge document picker was removed")
def test_active_picker_result_remains_available_while_refinement_is_running() -> None:
    assert "ready" in knowledge_service.PICKER_VISIBLE_PARSE_STATUSES
    assert "pending" in knowledge_service.PICKER_VISIBLE_PARSE_STATUSES
    assert "running" in knowledge_service.PICKER_VISIBLE_PARSE_STATUSES


def test_new_parser_phase_truncates_only_the_local_event_relay(tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"stage":"old"}\n', encoding="utf-8")

    knowledge_parser.prepare_parser_event_stream(tmp_path)

    assert events.read_text(encoding="utf-8") == ""
    assert events.stat().st_mode & 0o666 == 0o666


def test_parser_failure_does_not_overwrite_a_cancelled_plan(monkeypatch) -> None:
    plan = SimpleNamespace(status="cancelled")

    class _Session:
        def get(self, *_args):
            return plan

    @contextmanager
    def _session():
        yield _Session()

    events = []
    monkeypatch.setattr(knowledge_parser, "get_db_session", _session)
    monkeypatch.setattr(
        knowledge_parser,
        "push_knowledge_parse_event",
        lambda *_args: events.append(True),
    )

    knowledge_parser._fail_plan(uuid.uuid4(), "parser_failed", "should not replace cancel")

    assert plan.status == "cancelled"
    assert events == []


def test_missing_parser_metadata_stops_plan_and_run_workers(monkeypatch) -> None:
    class _Session:
        def get(self, *_args):
            return None

    @contextmanager
    def _session():
        yield _Session()

    monkeypatch.setattr(knowledge_parser, "get_db_session", _session)

    assert knowledge_parser._parse_plan_cancelled(uuid.uuid4())
    assert knowledge_parser._parse_run_cancelled(uuid.uuid4())


def test_source_delete_guard_rejects_an_active_parse_plan() -> None:
    active_plan = SimpleNamespace(status="running")

    class _Result:
        def first(self):
            return active_plan

    class _Session:
        def exec(self, _statement):
            return _Result()

    source = SimpleNamespace(id=uuid.uuid4(), parse_status="unparsed")

    with pytest.raises(HTTPException, match="正在生成解析方案"):
        knowledge_service.ensure_source_deletable(_Session(), source)


def test_cleanup_payload_is_user_scoped_and_deduplicates_workspaces() -> None:
    cleanup = getattr(knowledge_service, "knowledge_cleanup", None)
    assert cleanup is not None
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    direct_run_id = uuid.uuid4()
    planned_run_id = uuid.uuid4()

    payload = cleanup.build_cleanup_payload(
        user_id=user_id,
        source_id=source_id,
        plans=[SimpleNamespace(id=plan_id, container_id="plan-container")],
        runs=[
            SimpleNamespace(
                id=direct_run_id,
                container_id="run-container",
                session_owner_kind="run",
                session_owner_id=direct_run_id,
            ),
            SimpleNamespace(
                id=planned_run_id,
                container_id=None,
                session_owner_kind="plan",
                session_owner_id=plan_id,
            ),
        ],
    )

    assert payload["user_id"] == str(user_id)
    assert payload["object_prefixes"] == [f"knowledge/{user_id}/{source_id}/"]
    assert payload["parser_jobs"] == [
        {"id": str(plan_id), "container_id": "plan-container"},
        {"id": str(direct_run_id), "container_id": "run-container"},
        {"id": str(planned_run_id), "container_id": None},
    ]
    assert payload["workspaces"] == [
        {"kind": "plan", "id": str(plan_id)},
        {"kind": "run", "id": str(direct_run_id)},
    ]


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_deleted_document_references_are_removed_without_mutating_other_task_config() -> None:
    cleanup = getattr(knowledge_service, "knowledge_cleanup", None)
    assert cleanup is not None
    deleted_id = uuid.uuid4()
    kept_id = uuid.uuid4()
    original = {
        "memory": {
            "enabled": True,
            "knowledge_document_refs": [
                {"document_id": str(deleted_id), "content_version": 1},
                {"document_id": str(kept_id), "content_version": 2},
            ],
        },
        "max_generations": 10,
    }

    updated = cleanup.remove_document_refs(original, {deleted_id})

    assert updated is not original
    assert original["memory"]["knowledge_document_refs"][0]["document_id"] == str(deleted_id)
    assert updated["memory"]["knowledge_document_refs"] == [
        {"document_id": str(kept_id), "content_version": 2}
    ]
    assert updated["memory"]["enabled"] is True
    assert updated["max_generations"] == 10


def test_cleanup_job_model_keeps_a_durable_payload_after_owner_deletion() -> None:
    cleanup_model = getattr(models, "KnowledgeCleanupJob", None)
    assert cleanup_model is not None

    job = cleanup_model(
        user_id=uuid.uuid4(),
        payload={"object_prefixes": ["knowledge/user/source/"]},
    )

    assert job.status == "pending"
    assert job.attempts == 0


def test_cleanup_payload_removes_only_scoped_objects_and_parser_workspaces(
    tmp_path,
    monkeypatch,
) -> None:
    cleanup = getattr(knowledge_service, "knowledge_cleanup", None)
    assert cleanup is not None
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    run_id = uuid.uuid4()
    plan_workspace = tmp_path / f"code_user-{user_id}" / "knowledge_plan" / str(plan_id)
    run_workspace = tmp_path / f"code_user-{user_id}" / "knowledge_parse" / str(run_id)
    unrelated = tmp_path / f"code_user-{user_id}" / "task-data"
    for path in (plan_workspace, run_workspace, unrelated):
        path.mkdir(parents=True)
        (path / "sentinel").write_text("keep or delete", encoding="utf-8")

    stopped = []
    deleted_keys = []
    listed = []
    prefix = f"knowledge/{user_id}/{source_id}/"
    monkeypatch.setattr(cleanup.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup, "stop_parser_job", lambda job: stopped.append(job))
    def _list_objects(value):
        listed.append(value)
        return [f"{value}document.md"] if len(listed) == 1 else []

    monkeypatch.setattr(cleanup.storage, "list_objects", _list_objects)
    monkeypatch.setattr(cleanup.storage, "delete_many", lambda keys: deleted_keys.extend(keys))

    cleanup.cleanup_payload(
        {
            "user_id": str(user_id),
            "object_prefixes": [prefix],
            "parser_jobs": [{"id": str(run_id), "container_id": "container-id"}],
            "workspaces": [
                {"kind": "plan", "id": str(plan_id)},
                {"kind": "run", "id": str(run_id)},
            ],
            "all_user_workspaces": False,
        }
    )

    assert stopped == [{"id": str(run_id), "container_id": "container-id"}]
    assert deleted_keys == [f"{prefix}document.md"]
    assert not plan_workspace.exists()
    assert not run_workspace.exists()
    assert unrelated.exists()


def test_cleanup_rejects_an_object_prefix_outside_the_owning_user(monkeypatch) -> None:
    cleanup = getattr(knowledge_service, "knowledge_cleanup", None)
    assert cleanup is not None
    user_id = uuid.uuid4()
    monkeypatch.setattr(cleanup, "stop_parser_job", lambda _job: None)

    with pytest.raises(ValueError, match="object prefix"):
        cleanup.cleanup_payload(
            {
                "user_id": str(user_id),
                "object_prefixes": ["knowledge/another-user/"],
                "parser_jobs": [],
                "workspaces": [],
                "all_user_workspaces": False,
            }
        )


def test_cleanup_job_is_deleted_only_after_external_cleanup_succeeds(monkeypatch) -> None:
    cleanup = knowledge_service.knowledge_cleanup
    job = models.KnowledgeCleanupJob(user_id=uuid.uuid4(), payload={"user_id": str(uuid.uuid4())})

    class _Session(_CreateTopicSession):
        def get(self, _model, job_id):
            return job if job_id == job.id else None

    db = _Session()
    cleaned = []
    monkeypatch.setattr(cleanup, "cleanup_payload", lambda payload: cleaned.append(payload))

    cleanup.run_cleanup_job(db, job.id)

    assert cleaned == [job.payload]
    assert db.deleted == [job]
    assert job.attempts == 1


def test_cleanup_job_is_retained_for_retry_when_external_cleanup_fails(monkeypatch) -> None:
    cleanup = knowledge_service.knowledge_cleanup
    job = models.KnowledgeCleanupJob(user_id=uuid.uuid4(), payload={"user_id": str(uuid.uuid4())})

    class _Session(_CreateTopicSession):
        def get(self, _model, job_id):
            return job if job_id == job.id else None

        def rollback(self):
            return None

    db = _Session()

    def _fail(_payload):
        raise RuntimeError("rustfs unavailable")

    monkeypatch.setattr(cleanup, "cleanup_payload", _fail)

    with pytest.raises(RuntimeError, match="rustfs unavailable"):
        cleanup.run_cleanup_job(db, job.id)

    assert db.deleted == []
    assert job.status == "failed"
    assert job.attempts == 1
    assert "rustfs unavailable" in job.error


@pytest.mark.skip(reason="task-level knowledge document injection was removed")
def test_source_delete_records_durable_cleanup_and_scrubs_task_refs(monkeypatch) -> None:
    from app.services.task_service import pinned_knowledge

    cleanup = knowledge_service.knowledge_cleanup
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    document_id = uuid.uuid4()
    task_id = uuid.uuid4()
    source = SimpleNamespace(id=source_id, parse_status="ready")
    job = models.KnowledgeCleanupJob(
        user_id=user_id,
        payload={"user_id": str(user_id), "object_prefixes": [f"knowledge/{user_id}/{source_id}/"]},
    )
    db = _CreateTopicSession()
    scrubbed = []
    dispatched = []
    monkeypatch.setattr(knowledge_service, "_get_owned_source_for_update", lambda *_args: source)
    monkeypatch.setattr(knowledge_service, "ensure_source_deletable", lambda *_args: None)
    monkeypatch.setattr(cleanup, "prepare_source_cleanup_job", lambda *_args: job)
    monkeypatch.setattr(
        cleanup,
        "scrub_source_document_refs",
        lambda *_args: (scrubbed.append(True) or {document_id}, [task_id]),
    )
    live_scrubs = []
    monkeypatch.setattr(
        pinned_knowledge,
        "scrub_deleted_runtime_documents",
        lambda **kwargs: live_scrubs.append(kwargs),
    )
    monkeypatch.setattr(cleanup, "run_or_schedule_cleanup", lambda job_id: dispatched.append(job_id))

    knowledge_service.delete_source(db, SimpleNamespace(id=user_id), source_id)

    assert db.added == [job]
    assert db.deleted == [source]
    assert scrubbed == [True]
    assert live_scrubs == [
        {
            "user_id": user_id,
            "task_ids": [task_id],
            "document_ids": {document_id},
        }
    ]
    assert dispatched == [job.id]


def test_user_delete_records_cleanup_before_removing_the_user(monkeypatch) -> None:
    cleanup = knowledge_service.knowledge_cleanup
    user = SimpleNamespace(id=uuid.uuid4(), is_superuser=False)
    job = models.KnowledgeCleanupJob(
        user_id=user.id,
        payload={"user_id": str(user.id), "all_user_workspaces": True},
    )
    db = _CreateTopicSession()
    dispatched = []
    monkeypatch.setattr(cleanup, "prepare_user_cleanup_job", lambda *_args: job)
    monkeypatch.setattr(cleanup, "run_or_schedule_cleanup", lambda job_id: dispatched.append(job_id))

    users_routes.delete_user_me(db, user)

    assert db.added == [job]
    assert db.deleted == [user]
    assert dispatched == [job.id]


def test_credential_broker_indexes_tokens_by_user_for_account_cleanup(monkeypatch) -> None:
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    calls = []

    class _Pipeline:
        def set(self, *args, **kwargs):
            calls.append(("set", args, kwargs))
            return self

        def sadd(self, *args):
            calls.append(("sadd", args, {}))
            return self

        def expire(self, *args):
            calls.append(("expire", args, {}))
            return self

        def execute(self):
            calls.append(("execute", (), {}))
            return []

    class _Redis:
        def pipeline(self):
            return _Pipeline()

    monkeypatch.setattr(credential_broker, "get_sync_redis", lambda: _Redis())
    monkeypatch.setattr(credential_broker, "encrypt_secret", lambda value: value)

    credential_broker.issue_token(
        user_id=user_id,
        task_id=task_id,
        ttl=60,
        provider_type="openai",
        base_url="https://example.invalid/v1",
        api_key="secret",
    )

    assert any(
        call[0] == "sadd"
        and call[1][0] == f"llmproxy:user:{user_id}"
        for call in calls
    )


def test_user_token_revocation_removes_every_indexed_proxy_token(monkeypatch) -> None:
    user_id = uuid.uuid4()
    deleted = []

    class _Pipeline:
        def delete(self, *keys):
            deleted.extend(keys)
            return self

        def execute(self):
            return []

    class _Redis:
        def smembers(self, _key):
            return {"token-a", "token-b"}

        def pipeline(self):
            return _Pipeline()

    monkeypatch.setattr(credential_broker, "get_sync_redis", lambda: _Redis())

    count = credential_broker.revoke_user_tokens(user_id)

    assert count == 2
    assert "llmproxy:token:token-a" in deleted
    assert "llmproxy:token:token-b" in deleted
    assert f"llmproxy:user:{user_id}" in deleted


def test_cleanup_celery_task_delegates_to_the_durable_job_runner(monkeypatch) -> None:
    job_id = uuid.uuid4()
    delegated = []

    @contextmanager
    def _session():
        yield "db-session"

    monkeypatch.setattr(knowledge_parser, "get_db_session", _session)
    monkeypatch.setattr(
        knowledge_service.knowledge_cleanup,
        "run_cleanup_job",
        lambda db, value: delegated.append((db, value)),
    )

    knowledge_parser.run_knowledge_cleanup.run(str(job_id))

    assert delegated == [("db-session", job_id)]


def test_startup_requeues_cleanup_jobs_left_by_a_previous_failure(monkeypatch) -> None:
    cleanup = knowledge_service.knowledge_cleanup
    jobs = [
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(id=uuid.uuid4()),
    ]

    class _Result:
        def all(self):
            return jobs

    class _Session:
        def exec(self, _statement):
            return _Result()

    @contextmanager
    def _session():
        yield _Session()

    queued = []
    monkeypatch.setattr("app.core.db.get_db_session", _session)
    monkeypatch.setattr(
        knowledge_parser.run_knowledge_cleanup,
        "apply_async",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    cleanup.recover_pending_cleanup_jobs()

    assert [call[0][0][0] for call in queued] == [str(job.id) for job in jobs]


def test_knowledge_parse_history_reads_persisted_stream_events(monkeypatch) -> None:
    class _Redis:
        def xrange(self, _key):
            return [
                ("1-0", {"data": '{"type":"progress","stage":"prepared","message":"ready"}'}),
                ("2-0", {"data": '{"type":"progress","stage":"analyzing","message":"reading"}'}),
            ]

    monkeypatch.setattr(redis_core, "get_sync_redis", lambda: _Redis())

    events = redis_core.read_knowledge_parse_events(uuid.uuid4())

    assert [event["stage"] for event in events] == ["prepared", "analyzing"]
