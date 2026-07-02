"""GitHub star reward status and granting logic."""

from datetime import UTC, datetime

import httpx
from fastapi import HTTPException
from loguru import logger
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.models import StarRewardStatus, StarRewardStatusPublic, User, UserOidcAccount, UserStarReward


def get_star_reward_status(
    *,
    session: Session,
    current_user: User,
    backend_access_token: str,
) -> StarRewardStatusPublic:
    """Check star state and grant the one-time LiteLLM reward when eligible."""
    repo = _normalized_repo()
    amount = float(settings.STAR_REWARD_AMOUNT_USD)
    access_token = _get_github_access_token(session, current_user)
    if not access_token:
        return StarRewardStatusPublic(
            starred=None,
            reward_eligible=False,
            reward_granted=False,
            reward_amount=amount,
            repo=repo,
            message="GitHub login is required to check star reward status.",
        )

    existing = _get_reward(session, current_user, repo)
    if existing and existing.reward_status == StarRewardStatus.GRANTED:
        return StarRewardStatusPublic(
            starred=True,
            reward_eligible=True,
            reward_granted=True,
            reward_amount=existing.reward_amount,
            repo=repo,
            message="Star reward has already been granted.",
        )

    starred = _github_user_has_starred(access_token, repo)
    if not starred:
        return StarRewardStatusPublic(
            starred=False,
            reward_eligible=False,
            reward_granted=False,
            reward_amount=amount,
            repo=repo,
            message="Star this repository to receive the reward.",
        )

    _acquire_star_reward_lock(session, current_user, repo)
    session.expire_all()
    reward = _get_reward(session, current_user, repo)
    if reward and reward.reward_status == StarRewardStatus.GRANTED:
        return StarRewardStatusPublic(
            starred=True,
            reward_eligible=True,
            reward_granted=True,
            reward_amount=reward.reward_amount,
            repo=repo,
            message="Star reward has already been granted.",
        )

    reward = reward or UserStarReward(
        user_id=current_user.id,
        provider="github",
        repo=repo,
        github_user_id=_get_github_subject(session, current_user),
        reward_amount=amount,
        reward_status=StarRewardStatus.PENDING,
    )
    reward.starred_at_checked = datetime.now(UTC)
    reward.reward_amount = amount

    if reward.reward_status != StarRewardStatus.GRANTED:
        try:
            api_key_cache_refreshed = _grant_litellm_reward(backend_access_token, amount)
        except Exception as exc:  # noqa: BLE001 - keep status retryable
            logger.warning("Failed to grant LiteLLM star reward. user={}, repo={}: {}", current_user.id, repo, exc)
            reward.reward_status = StarRewardStatus.FAILED
            reward.failure_reason = str(exc)[:512]
            session.add(reward)
            session.commit()
            return StarRewardStatusPublic(
                starred=True,
                reward_eligible=True,
                reward_granted=False,
                reward_granted_now=False,
                api_key_cache_refreshed=False,
                reward_amount=amount,
                repo=repo,
                message="Reward could not be granted yet and will be retried.",
            )

        reward.reward_status = StarRewardStatus.GRANTED
        reward.granted_at = datetime.now(UTC)
        reward.failure_reason = None
        session.add(reward)
        session.commit()
    else:
        api_key_cache_refreshed = None

    return StarRewardStatusPublic(
        starred=True,
        reward_eligible=True,
        reward_granted=True,
        reward_granted_now=True,
        api_key_cache_refreshed=api_key_cache_refreshed,
        reward_amount=amount,
        repo=repo,
        message="Star reward has been granted.",
    )


def _acquire_star_reward_lock(session: Session, user: User, repo: str) -> None:
    """Serialize reward granting per user/repo across backend workers."""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    session.execute(
        text("select pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"star_reward:{user.id}:{repo}"},
    )


def _get_github_access_token(session: Session, user: User) -> str | None:
    account = session.exec(
        select(UserOidcAccount).where(
            UserOidcAccount.user_id == user.id,
            UserOidcAccount.provider == "github",
        )
    ).first()
    if not account or not account.access_token:
        return None
    return account.access_token


def _get_github_subject(session: Session, user: User) -> str | None:
    account = session.exec(
        select(UserOidcAccount).where(
            UserOidcAccount.user_id == user.id,
            UserOidcAccount.provider == "github",
        )
    ).first()
    return account.provider_subject if account else None


def _get_reward(session: Session, user: User, repo: str) -> UserStarReward | None:
    return session.exec(
        select(UserStarReward).where(
            UserStarReward.user_id == user.id,
            UserStarReward.repo == repo,
        )
    ).first()


def _github_user_has_starred(access_token: str, repo: str) -> bool:
    try:
        with httpx.Client(timeout=max(8.0, float(settings.GITHUB_HTTP_TIMEOUT_SECONDS)), trust_env=False) as client:
            response = client.get(
                f"https://api.github.com/user/starred/{repo}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "LLM4AD-Star-Reward",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Failed to check GitHub star status.") from exc

    if response.status_code == 204:
        return True
    if response.status_code == 404:
        return False
    raise HTTPException(status_code=502, detail="GitHub star status check failed.")


def _grant_litellm_reward(access_token: str, amount: float) -> bool | None:
    gateway_base_url = settings.LITELLM_GATEWAY_BASE_URL.rstrip("/")
    if not gateway_base_url:
        raise ValueError("LiteLLM gateway is not configured.")
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.post(
            f"{gateway_base_url}/internal/litellm/users/me/reward",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"amount": amount},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or int(payload.get("code", 500)) >= 300:
        raise ValueError(str(payload.get("message") if isinstance(payload, dict) else "Unexpected gateway response."))
    data = payload.get("data")
    if isinstance(data, dict):
        refreshed = data.get("api_key_cache_refreshed")
        if isinstance(refreshed, bool):
            return refreshed
    return None


def _normalized_repo() -> str:
    repo = settings.GITHUB_REPOSITORY.strip().strip("/")
    if repo.count("/") != 1:
        raise HTTPException(status_code=503, detail="GitHub repository is not configured correctly.")
    return repo
