"""管理员运营统计聚合服务。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import case, desc
from sqlmodel import Session, func, select

from app.core.config import settings
from app.models import Feedback, LLMProvider, Project, Task, TaskStatus, User
from app.services import provider_service

_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def build_analytics_overview(
    db: Session,
    access_token: str,
    date_range: str = "30d",
) -> dict[str, Any]:
    """Build the administrator dashboard payload."""
    normalized_range = _normalize_range(date_range)
    return {
        "range": normalized_range,
        "generated_at": datetime.now(UTC).isoformat(),
        "users": _build_user_stats(db),
        "projects": _build_project_stats(db),
        "tasks": _build_task_stats(db),
        "feedback": _build_feedback_stats(db),
        "providers": _build_provider_stats(db),
        "litellm": fetch_litellm_quota_summary(db, access_token),
        "github": fetch_github_summary(),
        "plausible": fetch_plausible_summary(normalized_range),
    }


def build_operations_summary(db: Session) -> dict[str, Any]:
    """Build stable operations inventory statistics."""
    return {
        "users": _build_user_stats(db),
        "projects": _build_project_stats(db),
        "providers": _build_provider_stats(db),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_operations_tasks(db: Session) -> dict[str, Any]:
    """Build task activity statistics."""
    result = _build_task_stats(db)
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


def build_operations_feedback(db: Session) -> dict[str, Any]:
    """Build feedback statistics."""
    result = _build_feedback_stats(db)
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


def build_operations_litellm(db: Session, access_token: str) -> dict[str, Any]:
    """Build LiteLLM quota and spend statistics."""
    result = fetch_litellm_quota_summary(db, access_token)
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


def build_visitors_plausible(date_range: str) -> dict[str, Any]:
    """Build visitor analytics statistics."""
    normalized_range = _normalize_range(date_range)
    result = fetch_plausible_summary(normalized_range)
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


def build_visitors_github() -> dict[str, Any]:
    """Build GitHub repository statistics."""
    result = fetch_github_summary()
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


def _build_user_stats(db: Session) -> dict[str, Any]:
    total = _one(db, select(func.count()).select_from(User))
    active = _one(db, select(func.count()).select_from(User).where(User.is_active.is_(True)))
    superusers = _one(db, select(func.count()).select_from(User).where(User.is_superuser.is_(True)))
    verified = _one(db, select(func.count()).select_from(User).where(User.email_verified.is_(True)))
    return {
        "total": total,
        "active": active,
        "inactive": max(total - active, 0),
        "superusers": superusers,
        "email_verified": verified,
    }


def _build_project_stats(db: Session) -> dict[str, Any]:
    return {"total": _one(db, select(func.count()).select_from(Project))}


def _build_task_stats(db: Session) -> dict[str, Any]:
    total = _one(db, select(func.count()).select_from(Task))
    by_status = {status.value: 0 for status in TaskStatus}
    for status, count in db.exec(select(Task.status, func.count()).group_by(Task.status)).all():
        key = status.value if hasattr(status, "value") else str(status)
        by_status[key] = int(count or 0)

    trend = [
        {"date": str(day), "count": int(count or 0)}
        for day, count in db.exec(
            select(func.date(Task.created_time), func.count())
            .group_by(func.date(Task.created_time))
            .order_by(func.date(Task.created_time))
        ).all()
        if day is not None
    ]

    task_count = func.count(Task.id)
    active_count = func.sum(case((Task.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]), 1), else_=0))
    completed_count = func.sum(case((Task.status == TaskStatus.COMPLETED, 1), else_=0))
    failed_count = func.sum(case((Task.status == TaskStatus.FAILED, 1), else_=0))
    top_users = []
    for (
        user_id,
        full_name,
        email,
        user_task_count,
        project_count,
        active_tasks,
        completed_tasks,
        failed_tasks,
        latest_task_time,
    ) in db.exec(
        select(
            User.id,
            User.full_name,
            User.email,
            task_count,
            func.count(func.distinct(Project.id)),
            active_count,
            completed_count,
            failed_count,
            func.max(Task.created_time),
        )
        .select_from(Task)
        .join(Project, Task.project_id == Project.id)
        .join(User, Project.user_id == User.id)
        .group_by(User.id, User.full_name, User.email)
        .order_by(desc(task_count))
        .limit(8)
    ).all():
        top_users.append(
            {
                "user_id": str(user_id),
                "full_name": full_name,
                "name": full_name or email or "Unknown",
                "email": email,
                "tasks": int(user_task_count or 0),
                "projects": int(project_count or 0),
                "active_tasks": int(active_tasks or 0),
                "completed_tasks": int(completed_tasks or 0),
                "failed_tasks": int(failed_tasks or 0),
                "latest_task_time": latest_task_time.isoformat()
                if hasattr(latest_task_time, "isoformat")
                else (str(latest_task_time) if latest_task_time else None),
            }
        )

    return {
        "total": total,
        "by_status": by_status,
        "trend": trend,
        "top_users": top_users,
    }


def _build_feedback_stats(db: Session) -> dict[str, Any]:
    total = _one(db, select(func.count()).select_from(Feedback))
    by_status = _rows_to_count_map(db.exec(select(Feedback.status, func.count()).group_by(Feedback.status)).all())
    by_type = _rows_to_count_map(db.exec(select(Feedback.type, func.count()).group_by(Feedback.type)).all())
    by_priority = _rows_to_count_map(db.exec(select(Feedback.priority, func.count()).group_by(Feedback.priority)).all())
    recent_items = [
        {
            "id": str(id_),
            "title": title,
            "content": content,
            "type": type_.value if hasattr(type_, "value") else str(type_),
            "status": status.value if hasattr(status, "value") else str(status),
            "priority": priority.value if hasattr(priority, "value") else str(priority),
            "created_time": created_time.isoformat() if hasattr(created_time, "isoformat") else str(created_time),
            "contact_email": contact_email,
            "page_url": page_url,
            "browser_info": browser_info,
            "admin_reply": admin_reply,
            "tags": tags,
            "user_full_name": full_name,
            "user_email": email,
        }
        for (
            id_,
            title,
            content,
            type_,
            status,
            priority,
            created_time,
            contact_email,
            page_url,
            browser_info,
            admin_reply,
            tags,
            full_name,
            email,
        ) in db.exec(
            select(
                Feedback.id,
                Feedback.title,
                Feedback.content,
                Feedback.type,
                Feedback.status,
                Feedback.priority,
                Feedback.created_time,
                Feedback.contact_email,
                Feedback.page_url,
                Feedback.browser_info,
                Feedback.admin_reply,
                Feedback.tags,
                User.full_name,
                User.email,
            )
            .select_from(Feedback)
            .join(User, Feedback.user_id == User.id, isouter=True)
            .order_by(Feedback.created_time.desc())
            .limit(8)
        ).all()
    ]
    return {
        "total": total,
        "pending": by_status.get("pending", 0),
        "in_progress": by_status.get("in_progress", 0),
        "resolved": by_status.get("resolved", 0),
        "closed": by_status.get("closed", 0),
        "rejected": by_status.get("rejected", 0),
        "by_status": by_status,
        "by_type": by_type,
        "by_priority": by_priority,
        "recent_items": recent_items,
    }


def _build_provider_stats(db: Session) -> dict[str, Any]:
    total = _one(db, select(func.count()).select_from(LLMProvider))
    builtin = _one(db, select(func.count()).select_from(LLMProvider).where(LLMProvider.is_builtin.is_(True)))
    return {
        "total": total,
        "builtin": builtin,
        "custom": max(total - builtin, 0),
    }


def fetch_litellm_quota_summary(db: Session, access_token: str) -> dict[str, Any]:
    result = provider_service.fetch_litellm_user_quotas_via_gateway(db, access_token)
    items = result.get("items") if isinstance(result, dict) else None
    if not result.get("available") or not isinstance(items, list):
        detail = result.get("message", "LiteLLM quota is unavailable.") if isinstance(result, dict) else ""
        return {
            "available": False,
            "total_spend": None,
            "total_budget": None,
            "remaining": None,
            "over_budget_users": 0,
            "near_limit_users": 0,
            "top_users": [],
            "message": "LiteLLM quota data is temporarily unavailable.",
            "detail": detail,
            "unavailable_reason": "gateway_unavailable",
        }

    total_spend = sum(_num(item.get("spend")) or 0 for item in items if isinstance(item, dict))
    budgets = [_num(item.get("budget")) for item in items if isinstance(item, dict)]
    usable_budgets = [value for value in budgets if value is not None]
    total_budget = sum(usable_budgets) if usable_budgets else None
    remaining = total_budget - total_spend if total_budget is not None else None

    over_budget = 0
    near_limit = 0
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        spend = _num(item.get("spend")) or 0.0
        budget = _num(item.get("budget"))
        item_remaining = _num(item.get("remaining"))
        if item_remaining is None and budget is not None:
            item_remaining = budget - spend
        if item_remaining is not None and item_remaining <= 0:
            over_budget += 1
        elif budget and item_remaining is not None and item_remaining <= budget * 0.1:
            near_limit += 1
        normalized_items.append(
            {
                "name": item.get("full_name") or item.get("user_alias") or item.get("user_email") or "Unknown",
                "email": item.get("user_email"),
                "spend": spend,
                "budget": budget,
                "remaining": item_remaining,
            }
        )

    return {
        "available": True,
        "total_spend": total_spend,
        "total_budget": total_budget,
        "remaining": remaining,
        "over_budget_users": over_budget,
        "near_limit_users": near_limit,
        "top_users": sorted(normalized_items, key=lambda item: item["spend"], reverse=True)[:8],
        "message": result.get("message", "success"),
    }


def fetch_github_summary() -> dict[str, Any]:
    cached = _get_cached("github", settings.GITHUB_REPOSITORY)
    if cached is not None:
        return cached

    repository = settings.GITHUB_REPOSITORY.strip()
    if "/" not in repository:
        return {"available": False, "message": "GitHub repository is not configured."}

    headers = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    try:
        with httpx.Client(timeout=8.0, trust_env=False, headers=headers) as client:
            repo_response = client.get(f"https://api.github.com/repos/{repository}")
            repo_response.raise_for_status()
            repo = repo_response.json()
            issues_response = client.get(
                f"https://api.github.com/repos/{repository}/issues",
                params={"state": "open", "sort": "updated", "per_page": 6},
            )
            issues_response.raise_for_status()
            issues = [
                issue for issue in issues_response.json()
                if isinstance(issue, dict) and "pull_request" not in issue
            ][:5]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "message": f"GitHub statistics unavailable: {exc}"}

    summary = {
        "available": True,
        "repository": repository,
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "watchers": int(repo.get("subscribers_count") or repo.get("watchers_count") or 0),
        "open_issues": int(repo.get("open_issues_count") or 0),
        "updated_at": repo.get("updated_at"),
        "recent_issues": [
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "url": issue.get("html_url"),
                "updated_at": issue.get("updated_at"),
            }
            for issue in issues
        ],
        "message": "success",
    }
    _set_cached("github", repository, summary)
    return summary


def fetch_plausible_summary(date_range: str) -> dict[str, Any]:
    normalized_range = _normalize_range(date_range)
    site_id = _resolve_plausible_site_id()
    api_base_url = _resolve_plausible_api_base_url()
    if not site_id:
        return {
            "available": False,
            "api_base_url": api_base_url,
            "site_id": None,
            "date_range": normalized_range,
            "message": "Plausible site_id is not configured.",
        }
    if not settings.PLAUSIBLE_API_KEY:
        return {
            "available": False,
            "api_base_url": api_base_url,
            "site_id": site_id,
            "date_range": normalized_range,
            "message": "Plausible Stats API key is not configured.",
        }

    cache_key = f"{api_base_url}:{site_id}:{normalized_range}"
    cached = _get_cached("plausible", cache_key)
    if cached is not None:
        return cached

    errors: dict[str, str] = {}
    aggregate = _safe_plausible_query(
        api_base_url,
        {
            "site_id": site_id,
            "metrics": ["visitors", "visits", "pageviews", "views_per_visit", "bounce_rate", "visit_duration"],
            "date_range": normalized_range,
        },
        errors,
        "metrics",
    )
    timeseries = _safe_plausible_query(
        api_base_url,
        {
            "site_id": site_id,
            "metrics": ["visitors", "pageviews"],
            "date_range": normalized_range,
            "dimensions": ["time:day"],
        },
        errors,
        "trend",
    )
    pages = _plausible_breakdown(api_base_url, site_id, normalized_range, ["event:page"], errors, "top_pages")
    sources = _plausible_breakdown(api_base_url, site_id, normalized_range, ["visit:source"], errors, "top_sources")
    countries = _plausible_breakdown(
        api_base_url,
        site_id,
        normalized_range,
        ["visit:country", "visit:country_name"],
        errors,
        "countries",
        _parse_plausible_country_row,
    )
    cities = _plausible_breakdown(
        api_base_url,
        site_id,
        normalized_range,
        ["visit:country", "visit:country_name", "visit:city", "visit:city_name"],
        errors,
        "cities",
        _parse_plausible_city_row,
    )
    devices = _plausible_breakdown(api_base_url, site_id, normalized_range, ["visit:device"], errors, "devices")
    browsers = _plausible_breakdown(api_base_url, site_id, normalized_range, ["visit:browser"], errors, "browsers")
    operating_systems = _plausible_breakdown(
        api_base_url, site_id, normalized_range, ["visit:os"], errors, "operating_systems"
    )

    has_data = bool(aggregate or timeseries or pages or sources or countries or cities or devices or browsers or operating_systems)

    summary = {
        "available": has_data,
        "api_base_url": api_base_url,
        "site_id": site_id,
        "date_range": normalized_range,
        "metrics": _extract_plausible_metrics(
            aggregate or {},
            ["visitors", "visits", "pageviews", "views_per_visit", "bounce_rate", "visit_duration"],
        ),
        "trend": _extract_plausible_series(timeseries or {}),
        "top_pages": pages,
        "top_sources": sources,
        "countries": countries,
        "cities": cities,
        "devices": devices,
        "browsers": browsers,
        "operating_systems": operating_systems,
        "errors": errors,
        "message": "success" if has_data else "Plausible statistics unavailable.",
    }
    _set_cached("plausible", cache_key, summary)
    return summary


def _plausible_breakdown(
    api_base_url: str,
    site_id: str,
    date_range: str,
    dimensions: list[str],
    errors: dict[str, str],
    section: str,
    row_parser: Any | None = None,
) -> list[dict[str, Any]]:
    payload = _safe_plausible_query(
        api_base_url,
        {
            "site_id": site_id,
            "metrics": ["visitors"],
            "date_range": date_range,
            "dimensions": dimensions,
            "pagination": {"limit": 8, "offset": 0},
        },
        errors,
        section,
    )
    results = payload.get("results") if isinstance(payload, dict) else []
    rows = []
    for row in results or []:
        dimensions = row.get("dimensions") or []
        metrics = row.get("metrics") or []
        value = metrics[0] if metrics else 0
        if row_parser is not None:
            rows.append(row_parser(dimensions, value))
            continue
        name = str(dimensions[0]).strip() if dimensions else ""
        rows.append({"name": name or "(not set)", "value": value})
    return rows


def _parse_plausible_country_row(dimensions: list[Any], value: Any) -> dict[str, Any]:
    code = str(dimensions[0]).strip().upper() if len(dimensions) > 0 and dimensions[0] else ""
    name = str(dimensions[1]).strip() if len(dimensions) > 1 and dimensions[1] else code
    return {
        "code": code,
        "name": name or "(not set)",
        "value": value,
    }


def _parse_plausible_city_row(dimensions: list[Any], value: Any) -> dict[str, Any]:
    country_code = str(dimensions[0]).strip().upper() if len(dimensions) > 0 and dimensions[0] else ""
    country_name = str(dimensions[1]).strip() if len(dimensions) > 1 and dimensions[1] else country_code
    city_name = str(dimensions[3]).strip() if len(dimensions) > 3 and dimensions[3] else ""
    if not city_name and len(dimensions) > 2 and dimensions[2]:
        city_name = str(dimensions[2]).strip()
    return {
        "country_code": country_code,
        "country_name": country_name or "(not set)",
        "name": city_name or "(not set)",
        "value": value,
    }


def _safe_plausible_query(
    api_base_url: str,
    body: dict[str, Any],
    errors: dict[str, str],
    section: str,
) -> dict[str, Any] | None:
    try:
        return _plausible_query(api_base_url, body)
    except Exception as exc:  # noqa: BLE001 - dashboard sections degrade independently
        errors[section] = str(exc)
        return None


def _plausible_query(api_base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    url = api_base_url.rstrip("/") + "/api/v2/query"
    headers = {"Authorization": f"Bearer {settings.PLAUSIBLE_API_KEY}"}
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.json()


def _resolve_plausible_api_base_url() -> str:
    configured = settings.PLAUSIBLE_API_BASE_URL.strip()
    if configured:
        return configured.rstrip("/")
    return "https://plausible.io"


def _resolve_plausible_site_id() -> str:
    configured = settings.PLAUSIBLE_SITE_ID.strip()
    if configured:
        return configured
    return ""


def _extract_plausible_metrics(payload: dict[str, Any], metric_names: list[str]) -> dict[str, float]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        return dict.fromkeys(metric_names, 0)
    metrics = results[0].get("metrics") if isinstance(results[0], dict) else []
    return {
        name: float(metrics[index] or 0) if index < len(metrics) else 0
        for index, name in enumerate(metric_names)
    }


def _extract_plausible_series(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results") if isinstance(payload, dict) else []
    series = []
    for row in results or []:
        dimensions = row.get("dimensions") or []
        metrics = row.get("metrics") or []
        series.append(
            {
                "date": dimensions[0] if dimensions else "",
                "visitors": metrics[0] if len(metrics) > 0 else 0,
                "pageviews": metrics[1] if len(metrics) > 1 else 0,
            }
        )
    return series


def _rows_to_count_map(rows: list[Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, count in rows:
        name = key.value if hasattr(key, "value") else str(key)
        result[name] = int(count or 0)
    return result


def _one(db: Session, statement: Any) -> int:
    return int(db.exec(statement).one() or 0)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_range(value: str | None) -> str:
    return value if value in {"7d", "30d", "91d"} else settings.PLAUSIBLE_DATE_RANGE


def _get_cached(section: str, key: str) -> dict[str, Any] | None:
    cached = _cache.get((section, key))
    if cached is None:
        return None
    expires_at, value = cached
    if expires_at <= time.monotonic():
        _cache.pop((section, key), None)
        return None
    return value


def _set_cached(section: str, key: str, value: dict[str, Any]) -> None:
    _cache[(section, key)] = (
        time.monotonic() + max(settings.ADMIN_ANALYTICS_CACHE_TTL_SECONDS, 1),
        value,
    )
