from datetime import timedelta
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from starlette.requests import Request

from app.core.security import create_access_token
from app.models import User


_code_auth_path_from_env = os.environ.get("CODE_AUTH_PATH")
if _code_auth_path_from_env:
    _CODE_AUTH_PATH = Path(_code_auth_path_from_env)
else:
    _CODE_AUTH_PATH = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "task_service"
        / "code_auth.py"
    )


def _load_code_auth_module():
    spec = importlib.util.spec_from_file_location("code_auth_under_test", _CODE_AUTH_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeDB:
    def __init__(self, user: User):
        self.user = user

    def get(self, _model, _user_id):
        return self.user


def test_verify_code_auth_returns_identity_headers(monkeypatch):
    code_auth = _load_code_auth_module()
    user_id = uuid4()
    user = User(
        id=user_id,
        email="test@admin.com",
        hashed_password="not-used",
        is_active=True,
    )
    token = create_access_token(user_id, timedelta(minutes=5), scope="code")
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"code_token={token}".encode())],
        }
    )
    monkeypatch.setattr(
        code_auth, "touch_code_user_active", lambda _user_id: None
    )

    response = code_auth.verify_code_auth(request, _FakeDB(user))

    assert response.headers["X-User-ID"] == str(user_id)
    assert response.headers["X-User-Email"] == "test@admin.com"
