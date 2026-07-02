import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models import StarRewardStatus, User, UserOidcAccount, UserStarReward


def _session():
    from app.models import UserDefaultModel, UserOidcAccount, UserStarReward

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(
        engine,
        tables=[User.__table__, UserDefaultModel.__table__, UserOidcAccount.__table__, UserStarReward.__table__],
    )
    return Session(engine)


def _user(session: Session) -> User:
    user = User(
        email="github-user@example.com",
        full_name="GitHub User",
        hashed_password="unused",
        is_active=True,
        email_verified=True,
        privacy_policy_accepted=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_star_reward_status_does_not_grant_when_user_has_not_starred(monkeypatch):
    from app.services import star_reward_service

    session = _session()
    user = _user(session)
    grant_calls: list[tuple[str, float]] = []

    monkeypatch.setattr(star_reward_service.settings, "GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(star_reward_service.settings, "STAR_REWARD_AMOUNT_USD", 10.0)
    monkeypatch.setattr(star_reward_service, "_get_github_access_token", lambda _session, _user: "gh-token")
    monkeypatch.setattr(star_reward_service, "_github_user_has_starred", lambda _token, _repo: False)
    monkeypatch.setattr(
        star_reward_service,
        "_grant_litellm_reward",
        lambda _access_token, amount: grant_calls.append((_access_token, amount)),
    )

    status = star_reward_service.get_star_reward_status(
        session=session,
        current_user=user,
        backend_access_token="backend-token",
    )

    assert status.starred is False
    assert status.reward_eligible is False
    assert status.reward_granted is False
    assert status.reward_amount == 10.0
    assert status.repo == "owner/repo"
    assert grant_calls == []


def test_star_reward_status_grants_reward_only_once(monkeypatch):
    from app.services import star_reward_service

    session = _session()
    user = _user(session)
    grant_calls: list[tuple[str, float]] = []

    monkeypatch.setattr(star_reward_service.settings, "GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(star_reward_service.settings, "STAR_REWARD_AMOUNT_USD", 10.0)
    monkeypatch.setattr(star_reward_service, "_get_github_access_token", lambda _session, _user: "gh-token")
    monkeypatch.setattr(star_reward_service, "_github_user_has_starred", lambda _token, _repo: True)

    def fake_grant(access_token: str, amount: float) -> bool:
        grant_calls.append((access_token, amount))
        return True

    monkeypatch.setattr(star_reward_service, "_grant_litellm_reward", fake_grant)

    first = star_reward_service.get_star_reward_status(
        session=session,
        current_user=user,
        backend_access_token="backend-token",
    )
    second = star_reward_service.get_star_reward_status(
        session=session,
        current_user=user,
        backend_access_token="backend-token",
    )

    assert first.starred is True
    assert first.reward_eligible is True
    assert first.reward_granted is True
    assert first.reward_granted_now is True
    assert first.api_key_cache_refreshed is True
    assert second.reward_granted is True
    assert second.reward_granted_now is False
    assert grant_calls == [("backend-token", 10.0)]


def test_star_reward_status_returns_existing_grant_without_rechecking_github(monkeypatch):
    from app.services import star_reward_service

    session = _session()
    user = _user(session)
    session.add(
        UserStarReward(
            user_id=user.id,
            provider="github",
            repo="owner/repo",
            github_user_id="12345",
            reward_amount=10.0,
            reward_status=StarRewardStatus.GRANTED,
            granted_at=datetime.now(UTC),
        )
    )
    session.commit()
    github_checks = 0
    grant_calls: list[tuple[str, float]] = []

    def fake_star_check(_access_token: str, _repo: str) -> bool:
        nonlocal github_checks
        github_checks += 1
        return False

    monkeypatch.setattr(star_reward_service.settings, "GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(star_reward_service.settings, "STAR_REWARD_AMOUNT_USD", 10.0)
    monkeypatch.setattr(star_reward_service, "_get_github_access_token", lambda _session, _user: "gh-token")
    monkeypatch.setattr(star_reward_service, "_github_user_has_starred", fake_star_check)
    monkeypatch.setattr(
        star_reward_service,
        "_grant_litellm_reward",
        lambda access_token, amount: grant_calls.append((access_token, amount)),
    )

    status = star_reward_service.get_star_reward_status(
        session=session,
        current_user=user,
        backend_access_token="backend-token",
    )

    assert status.starred is True
    assert status.reward_granted is True
    assert status.reward_granted_now is False
    assert status.api_key_cache_refreshed is None
    assert github_checks == 0
    assert grant_calls == []


def test_star_reward_status_rechecks_reward_after_lock_before_granting(monkeypatch):
    from app.services import star_reward_service

    session = _session()
    user = _user(session)
    grant_calls: list[tuple[str, float]] = []
    reward = UserStarReward(
        user_id=user.id,
        provider="github",
        repo="owner/repo",
        github_user_id="12345",
        reward_amount=10.0,
        reward_status=StarRewardStatus.PENDING,
    )
    session.add(reward)
    session.commit()

    monkeypatch.setattr(star_reward_service.settings, "GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(star_reward_service.settings, "STAR_REWARD_AMOUNT_USD", 10.0)
    monkeypatch.setattr(star_reward_service, "_get_github_access_token", lambda _session, _user: "gh-token")
    monkeypatch.setattr(star_reward_service, "_github_user_has_starred", lambda _token, _repo: True)
    monkeypatch.setattr(
        star_reward_service,
        "_grant_litellm_reward",
        lambda access_token, amount: grant_calls.append((access_token, amount)),
    )

    def mark_granted(_session: Session, _user: User, _repo: str) -> None:
        existing = star_reward_service._get_reward(_session, _user, _repo)
        assert existing is not None
        existing.reward_status = StarRewardStatus.GRANTED
        existing.granted_at = datetime.now(UTC)
        _session.add(existing)
        _session.commit()

    monkeypatch.setattr(star_reward_service, "_acquire_star_reward_lock", mark_granted)

    status = star_reward_service.get_star_reward_status(
        session=session,
        current_user=user,
        backend_access_token="backend-token",
    )

    assert status.reward_granted is True
    assert grant_calls == []


def test_build_github_authorize_url_contains_state_scope_and_redirect(monkeypatch):
    from app.services import oidc_service

    monkeypatch.setattr(oidc_service.settings, "OIDC_GITHUB_ENABLED", True)
    monkeypatch.setattr(oidc_service.settings, "OIDC_GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setattr(oidc_service.settings, "OIDC_GITHUB_REDIRECT_URI", "https://app.example.com/api/v1/oidc/github/callback")

    url = oidc_service.build_github_authorize_url(redirect="/projects")

    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=client-id" in url
    assert "scope=read%3Auser+user%3Aemail" in url
    assert "state=" in url


def test_upsert_github_user_creates_verified_user_and_stores_token(monkeypatch):
    from app.services import oidc_service
    from app.services import user_default_model_service

    session = _session()
    monkeypatch.setattr(user_default_model_service, "build_default_init_kwargs", lambda _session, user_id: {"user_id": user_id})
    profile = oidc_service.GitHubProfile(
        github_user_id="12345",
        login="octocat",
        email="octocat@example.com",
        full_name="The Octocat",
    )

    user = oidc_service.upsert_github_user(
        session=session,
        profile=profile,
        token=oidc_service.GitHubToken(
            access_token="gho-token",
            token_type="bearer",
            scope="read:user,user:email",
        ),
    )

    assert user.email == "octocat@example.com"
    assert user.email_verified is True
    assert user.privacy_policy_accepted is True

    account = oidc_service.get_github_account(session=session, user=user)
    assert account is not None
    assert account.provider == "github"
    assert account.provider_subject == "12345"
    assert account.access_token == "gho-token"
    assert isinstance(account.last_login_at, datetime)


def test_upsert_github_user_rejects_replacing_existing_github_binding(monkeypatch):
    from app.services import oidc_service
    from app.services import user_default_model_service

    session = _session()
    user = _user(session)
    session.add(
        UserOidcAccount(
            user_id=user.id,
            provider="github",
            provider_subject="old-github-id",
            provider_username="old-octocat",
            provider_email=user.email,
            access_token="old-token",
        )
    )
    session.commit()
    monkeypatch.setattr(user_default_model_service, "build_default_init_kwargs", lambda _session, user_id: {"user_id": user_id})

    profile = oidc_service.GitHubProfile(
        github_user_id="new-github-id",
        login="new-octocat",
        email=user.email,
        full_name="New Octocat",
    )

    with pytest.raises(Exception) as exc_info:
        oidc_service.upsert_github_user(
            session=session,
            profile=profile,
            token=oidc_service.GitHubToken(
                access_token="new-token",
                token_type="bearer",
                scope="read:user,user:email",
            ),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    account = oidc_service.get_github_account(session=session, user=user)
    assert account is not None
    assert account.provider_subject == "old-github-id"
    assert account.access_token == "old-token"


def test_fetch_github_profile_uses_verified_email_even_when_public_email_exists(monkeypatch):
    from app.services import oidc_service

    clients = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers):
            if url == oidc_service.GITHUB_USER_URL:
                return FakeResponse(
                    {
                        "id": 12345,
                        "login": "octocat",
                        "email": "public@example.com",
                        "name": "The Octocat",
                    }
                )
            if url == oidc_service.GITHUB_EMAILS_URL:
                return FakeResponse(
                    [
                        {"email": "verified@example.com", "primary": True, "verified": True},
                        {"email": "other@example.com", "primary": False, "verified": True},
                    ]
                )
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(oidc_service.settings, "GITHUB_HTTP_TIMEOUT_SECONDS", 120.0)
    monkeypatch.setattr(oidc_service.httpx, "Client", FakeClient)

    profile = oidc_service.fetch_github_profile(
        oidc_service.GitHubToken(access_token="gho-token", token_type="bearer", scope="read:user,user:email")
    )

    assert profile.email == "verified@example.com"
    assert clients[0].kwargs["timeout"] == 120.0
