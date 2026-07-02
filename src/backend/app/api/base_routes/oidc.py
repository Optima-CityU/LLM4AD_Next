"""OIDC/OAuth login routes."""

from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.api.deps import SessionDep
from app.core import security
from app.core.config import settings
from app.models import Token
from app.services import oidc_service

router = APIRouter(prefix="/oidc", tags=["oidc"])


@router.get("/github/authorize")
def github_authorize(redirect: str | None = None) -> RedirectResponse:
    """Redirect the browser to GitHub's OAuth authorization page."""
    return RedirectResponse(oidc_service.build_github_authorize_url(redirect=redirect), status_code=302)


@router.get("/github/callback")
def github_callback(session: SessionDep, code: str, state: str) -> RedirectResponse:
    """Handle GitHub OAuth callback, create/login user, and redirect to frontend."""
    state_payload = oidc_service.decode_oidc_state(state)
    github_token = oidc_service.exchange_github_code(code)
    profile = oidc_service.fetch_github_profile(github_token)
    user = oidc_service.upsert_github_user(session=session, profile=profile, token=github_token)

    token = Token(
        access_token=security.create_access_token(
            user.id,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        ),
        refresh_token=security.create_access_token(
            user.id,
            expires_delta=timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
            scope="refresh",
        ),
    )
    redirect_target = str(state_payload.get("redirect") or "/projects")
    frontend_callback = f"{settings.FRONTEND_HOST.rstrip('/')}/oidc/callback"
    query = urlencode(
        {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": token.token_type,
            "redirect": redirect_target,
        }
    )
    return RedirectResponse(f"{frontend_callback}?{query}", status_code=302)
