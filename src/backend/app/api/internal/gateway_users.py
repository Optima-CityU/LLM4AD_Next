"""Gateway-only user lookups used by the internal memory model proxy."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, EmailStr

from app import models
from app.api.deps import SessionDep
from app.core.config import settings

router = APIRouter(prefix="/internal", tags=["internal"])


class GatewayUser(BaseModel):
    """Minimal active-user profile required to provision a LiteLLM key."""

    id: str
    email: EmailStr
    is_active: bool


@router.get("/gateway/users/{user_id}")
def get_gateway_user(
    user_id: uuid.UUID,
    session: SessionDep,
    x_gateway_service_token: Annotated[str | None, Header()] = None,
) -> GatewayUser:
    """Return an active user only to a gateway holding the service credential."""

    expected_token = settings.GATEWAY_BACKEND_SERVICE_TOKEN
    if not expected_token or not hmac.compare_digest(x_gateway_service_token or "", expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway service token")

    user = session.get(models.User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is unavailable")

    return GatewayUser(id=str(user.id), email=user.email, is_active=True)
