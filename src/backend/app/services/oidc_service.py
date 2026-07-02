"""GitHub-backed OIDC/OAuth login helpers."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException
from jwt.exceptions import InvalidTokenError
from loguru import logger
from sqlmodel import Session, select

from app.core import security
from app.core.config import settings
from app.crud import user as crud_user
from app.models import User, UserCreate, UserOidcAccount, UserUpdate

OIDC_STATE_SCOPE = "oidc_state"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


@dataclass(frozen=True)
class GitHubToken:
    access_token: str
    token_type: str
    scope: str


@dataclass(frozen=True)
class GitHubProfile:
    github_user_id: str
    login: str
    email: str
    full_name: str | None


def build_github_authorize_url(redirect: str | None = None) -> str:
    """Build the GitHub authorization URL with a signed state token."""
    if not settings.OIDC_GITHUB_ENABLED:
        raise HTTPException(status_code=503, detail="GitHub login is not enabled.")
    if not settings.OIDC_GITHUB_CLIENT_ID:
        raise HTTPException(status_code=503, detail="GitHub login is not configured.")
    if not settings.OIDC_GITHUB_REDIRECT_URI:
        raise HTTPException(status_code=503, detail="GitHub redirect URI is not configured.")

    query = {
        "client_id": settings.OIDC_GITHUB_CLIENT_ID,
        "redirect_uri": settings.OIDC_GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": create_oidc_state(redirect=redirect),
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(query)}"


def create_oidc_state(redirect: str | None = None) -> str:
    """Create a short-lived signed state token."""
    safe_redirect = redirect if redirect and redirect.startswith("/") and not redirect.startswith("//") else "/projects"
    now = datetime.now(UTC)
    payload = {
        "scope": OIDC_STATE_SCOPE,
        "redirect": safe_redirect,
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


def decode_oidc_state(state: str) -> dict[str, Any]:
    """Validate and decode a state token."""
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=[security.ALGORITHM])
    except InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail="OIDC state is invalid or expired.") from exc
    if payload.get("scope") != OIDC_STATE_SCOPE:
        raise HTTPException(status_code=400, detail="OIDC state scope is invalid.")
    return payload


def exchange_github_code(code: str) -> GitHubToken:
    """Exchange a GitHub callback code for an OAuth access token."""
    if not settings.OIDC_GITHUB_ENABLED:
        raise HTTPException(status_code=503, detail="GitHub login is not enabled.")
    if not settings.OIDC_GITHUB_CLIENT_ID or not settings.OIDC_GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="GitHub login is not configured.")

    try:
        with httpx.Client(timeout=_github_http_timeout(), trust_env=False) as client:
            response = client.post(
                GITHUB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.OIDC_GITHUB_CLIENT_ID,
                    "client_secret": settings.OIDC_GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": settings.OIDC_GITHUB_REDIRECT_URI,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("GitHub token exchange failed: {}", exc)
        raise HTTPException(status_code=502, detail="GitHub token exchange failed.") from exc

    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub did not return an access token.")
    return GitHubToken(
        access_token=access_token,
        token_type=str(payload.get("token_type") or "bearer"),
        scope=str(payload.get("scope") or ""),
    )


def fetch_github_profile(token: GitHubToken) -> GitHubProfile:
    """Fetch GitHub identity and a verified email address."""
    headers = _github_headers(token.access_token)
    try:
        with httpx.Client(timeout=_github_http_timeout(), trust_env=False) as client:
            user_response = client.get(GITHUB_USER_URL, headers=headers)
            user_response.raise_for_status()
            user_payload = user_response.json()
            email = _fetch_primary_verified_email(client, headers)
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch GitHub profile: {}", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch GitHub profile.") from exc

    if not email:
        raise HTTPException(status_code=400, detail="GitHub account has no verified email.")
    github_user_id = user_payload.get("id")
    login = user_payload.get("login")
    if github_user_id is None or not login:
        raise HTTPException(status_code=400, detail="GitHub profile is missing identity fields.")

    return GitHubProfile(
        github_user_id=str(github_user_id),
        login=str(login),
        email=str(email),
        full_name=user_payload.get("name") or login,
    )


def _fetch_primary_verified_email(client: httpx.Client, headers: dict[str, str]) -> str | None:
    response = client.get(GITHUB_EMAILS_URL, headers=headers)
    response.raise_for_status()
    emails = response.json()
    if not isinstance(emails, list):
        return None
    for item in emails:
        if not isinstance(item, dict):
            continue
        if item.get("primary") and item.get("verified") and item.get("email"):
            return str(item["email"])
    for item in emails:
        if isinstance(item, dict) and item.get("verified") and item.get("email"):
            return str(item["email"])
    return None


def upsert_github_user(*, session: Session, profile: GitHubProfile, token: GitHubToken) -> User:
    """Create or update the local user and GitHub account binding."""
    account = session.exec(
        select(UserOidcAccount).where(
            UserOidcAccount.provider == "github",
            UserOidcAccount.provider_subject == profile.github_user_id,
        )
    ).first()
    user = session.get(User, account.user_id) if account else None
    if user is None:
        user = crud_user.get_user_by_email(session=session, email=profile.email)

    if user is None:
        user_create = UserCreate(
            email=profile.email,
            password=secrets.token_urlsafe(32),
            full_name=profile.full_name,
            email_verified=True,
            privacy_policy_accepted=True,
            privacy_policy_accepted_at=datetime.now(UTC),
        )
        user = crud_user.create_user(session=session, user_create=user_create)
    else:
        user_update = UserUpdate(
            email=user.email,
            full_name=user.full_name or profile.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            email_verified=True,
            privacy_policy_accepted=True,
            privacy_policy_accepted_at=user.privacy_policy_accepted_at or datetime.now(UTC),
        )
        user = crud_user.update_user(session=session, db_user=user, user_in=user_update)

    _upsert_github_account(session=session, user=user, profile=profile, token=token)
    return user


def get_github_account(*, session: Session, user: User) -> UserOidcAccount | None:
    """Return the user's GitHub account binding, if any."""
    return session.exec(
        select(UserOidcAccount).where(
            UserOidcAccount.user_id == user.id,
            UserOidcAccount.provider == "github",
        )
    ).first()


def _upsert_github_account(
    *,
    session: Session,
    user: User,
    profile: GitHubProfile,
    token: GitHubToken,
) -> UserOidcAccount:
    account = get_github_account(session=session, user=user)
    if account is not None and account.provider_subject != profile.github_user_id:
        raise HTTPException(
            status_code=409,
            detail="This local user is already linked to another GitHub account.",
        )
    now = datetime.now(UTC)
    fields = {
        "user_id": user.id,
        "provider": "github",
        "provider_subject": profile.github_user_id,
        "provider_username": profile.login,
        "provider_email": profile.email,
        "access_token": token.access_token,
        "token_type": token.token_type,
        "scope": token.scope,
        "last_login_at": now,
    }
    if account is None:
        account = UserOidcAccount(**fields)
    else:
        account.sqlmodel_update(fields)
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _github_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LLM4AD-Star-Reward",
    }


def _github_http_timeout() -> float:
    return max(8.0, float(settings.GITHUB_HTTP_TIMEOUT_SECONDS))
