"""Independent Markdown knowledge-library API."""

import json
import uuid

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.api.llm4ad.sse_utils import redis_sse_stream, sse_response
from app.core.redis import knowledge_parse_stream_key, read_knowledge_parse_events
from app.schemas import knowledge as schemas
from app.services import knowledge_service

router = APIRouter(prefix="/knowledge", tags=["llm4ad.knowledge"])


@router.get("/parser-binding", response_model=schemas.KnowledgeParserBindingResponse)
def get_parser_binding(db: SessionDep, current_user: CurrentUser):
    return knowledge_service.get_parser_binding(db, current_user)


@router.put("/parser-binding", response_model=schemas.KnowledgeParserBindingResponse)
def update_parser_binding(
    request: schemas.KnowledgeParserBindingUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.upsert_parser_binding(db, current_user, request)


@router.post(
    "/sources",
    response_model=schemas.KnowledgeSourceSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_source(
    request: schemas.KnowledgeSourceCreateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.create_source(db, current_user, request)


@router.post(
    "/sources/{source_id}/files",
    response_model=schemas.KnowledgeSourceDetail,
    status_code=status.HTTP_201_CREATED,
)
async def add_source_files(
    source_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
):
    return await knowledge_service.add_source_files(db, current_user, source_id, files)


@router.get("/sources", response_model=schemas.KnowledgeSourceListResponse)
def list_sources(
    db: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None, max_length=200),
):
    return knowledge_service.list_sources(
        db,
        current_user,
        skip=skip,
        limit=limit,
        search=search,
    )


@router.get("/sources/{source_id}", response_model=schemas.KnowledgeSourceDetail)
def get_source(source_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    return knowledge_service.get_source_detail(db, current_user, source_id)


@router.patch("/sources/{source_id}", response_model=schemas.KnowledgeSourceSummary)
def update_source(
    source_id: uuid.UUID,
    request: schemas.KnowledgeSourceUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.update_source(db, current_user, source_id, request)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, db: SessionDep, current_user: CurrentUser) -> Response:
    knowledge_service.delete_source(db, current_user, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/source-files/{file_id}/content",
    response_model=schemas.KnowledgeContentResponse,
)
def get_source_file_content(file_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    return knowledge_service.get_source_file_content(db, current_user, file_id)


@router.patch("/source-files/{file_id}", response_model=schemas.KnowledgeSourceFileSummary)
def update_source_file(
    file_id: uuid.UUID,
    request: schemas.KnowledgeSourceFileUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.update_source_file(db, current_user, file_id, request)


@router.delete("/source-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source_file(file_id: uuid.UUID, db: SessionDep, current_user: CurrentUser) -> Response:
    knowledge_service.delete_source_file(db, current_user, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/documents/{document_id}/content",
    response_model=schemas.KnowledgeContentResponse,
)
def get_document_content(
    document_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.get_document_content(db, current_user, document_id)


@router.patch(
    "/documents/{document_id}",
    response_model=schemas.KnowledgeDocumentSummary,
)
def update_document(
    document_id: uuid.UUID,
    request: schemas.KnowledgeDocumentUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.update_document(db, current_user, document_id, request)


@router.post(
    "/parse-runs/{run_id}/documents/insert",
    response_model=schemas.KnowledgeDocumentInsertResponse,
)
def insert_document_blocks(
    run_id: uuid.UUID,
    request: schemas.KnowledgeDocumentInsertRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.insert_document_blocks(db, current_user, run_id, request)


@router.post("/parse-runs/{run_id}/documents/insert/stream")
def stream_insert_document_blocks(
    run_id: uuid.UUID,
    request: schemas.KnowledgeDocumentInsertRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    async def stream():
        try:
            async for item in knowledge_service.stream_insert_document_blocks(
                db,
                current_user,
                run_id,
                request,
            ):
                event_name = str(item.get("event") or "progress")
                data = {key: value for key, value in item.items() if key != "event"}
                yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except HTTPException as exc:
            data = {"message": str(exc.detail), "status_code": exc.status_code}
            yield f"event: error\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return sse_response(stream())


@router.get(
    "/parse-runs/{run_id}/generated-memories",
    response_model=list[schemas.MemoryCardResponse],
)
def list_generated_memory_cards(
    run_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    return knowledge_service.list_generated_memory_cards(db, current_user, run_id)


@router.post(
    "/sources/{source_id}/parse",
    response_model=schemas.KnowledgeParseRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_parse(
    source_id: uuid.UUID,
    request: schemas.KnowledgeParseStartRequest,
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
):
    return knowledge_service.start_parse_run(db, current_user, source_id, request, token)


@router.get("/parse-runs/{run_id}", response_model=schemas.KnowledgeParseRunResponse)
def get_parse_run(run_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    return knowledge_service.get_parse_run(db, current_user, run_id)


@router.get(
    "/sources/{source_id}/parse-runs/latest",
    response_model=schemas.KnowledgeParseRunResponse | None,
)
def get_latest_parse_run(source_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    return knowledge_service.get_latest_parse_run(db, current_user, source_id)


@router.get("/parse-runs/{run_id}/events", response_model=list[dict])
def list_parse_run_events(run_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    knowledge_service.get_parse_run(db, current_user, run_id)
    return read_knowledge_parse_events(run_id)


def _parse_entry_handler(_entry_id: str, fields: dict) -> tuple[str, bool] | None:
    entry = json.loads(fields["data"])
    event = str(entry.get("type") or "progress")
    terminal = event in {"done", "error", "stale", "cancelled"}
    return (
        f"event: {event}\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n",
        terminal,
    )


@router.get("/parse-runs/{run_id}/stream")
def stream_parse_run(
    run_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    last_id: str = Query(default="0-0"),
):
    run = knowledge_service.get_parse_run(db, current_user, run_id)
    return sse_response(
        redis_sse_stream(
            redis_key=knowledge_parse_stream_key(run_id),
            connected_data={"run_id": str(run.id), "status": run.status},
            entry_handler=_parse_entry_handler,
            last_id=last_id,
            max_idle=None,
            heartbeat_interval=5.0,
            use_draining=True,
        )
    )


@router.post(
    "/parse-runs/{run_id}/cancel",
    response_model=schemas.KnowledgeParseCancelResponse,
)
def cancel_parse_run(run_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    return knowledge_service.cancel_parse_run(db, current_user, run_id)


@router.post(
    "/parse-runs/{run_id}/continue",
    response_model=schemas.KnowledgeParseRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def continue_parse_run(
    run_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
):
    return knowledge_service.continue_parse_run(
        db,
        current_user,
        run_id,
        token,
    )


@router.post(
    "/parse-runs/{run_id}/refine",
    response_model=schemas.KnowledgeParseRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def refine_parse_run(
    run_id: uuid.UUID,
    request: schemas.KnowledgeParseRefineRequest,
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
):
    return knowledge_service.refine_parse_run(
        db,
        current_user,
        run_id,
        request,
        token,
    )
