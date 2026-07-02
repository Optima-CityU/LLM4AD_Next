"""GitHub star reward routes."""

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.models import StarRewardStatusPublic
from app.services import star_reward_service

router = APIRouter(prefix="/star-reward", tags=["star-reward"])


@router.get("/status", response_model=StarRewardStatusPublic)
def read_star_reward_status(
    session: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
) -> StarRewardStatusPublic:
    """Return current user's star reward state and grant the reward when eligible."""
    return star_reward_service.get_star_reward_status(
        session=session,
        current_user=current_user,
        backend_access_token=token,
    )
