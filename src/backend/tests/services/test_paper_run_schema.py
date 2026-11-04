"""Native research sessions may exist before a paper has been uploaded."""

import uuid
from datetime import UTC, datetime

from app.schemas.paper import PaperAgentRunResponse


def test_native_run_can_be_serialized_without_source():
    """Avoid workspace response 500s for AutoDesign and paperless discovery."""
    run = PaperAgentRunResponse(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), source_version_id=None,
        run_kind="autodesign", status="ready", progress=0, stage="native_session", message="",
        provider_id=None, model_name=None, session_id="native", celery_task_id=None,
        artifact_object_key=None, error_code=None, error=None,
        created_time=datetime.now(UTC), updated_time=datetime.now(UTC),
    )
    assert run.model_dump(mode="json")["source_version_id"] is None
