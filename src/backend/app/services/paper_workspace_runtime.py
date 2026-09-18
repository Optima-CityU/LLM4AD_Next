"""Persistent project-scoped container runtime for research workspaces."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from docker.errors import ImageNotFound, NotFound
from loguru import logger
from sqlmodel import select

from app import models
from app.core.config import settings
from app.core.constants import DOCKER_NETWORK_NAME
from app.core.db import get_db_session
from app.core.docker import get_docker_client
from app.core.redis import pop_idle_paper_workspaces, touch_paper_workspace_active

_CONTAINER_WORKSPACE = "/workspace"
_PAPER_WORKSPACE_LABEL = "llm4ad.paper-workspace"
_PAPER_WORKSPACE_OWNER_LABEL = "llm4ad.paper-workspace-owner"
_PAPER_WORKSPACE_NAME_PREFIX = "llm4ad-paper-workspace-"
_CLOUDCLI_PORT = 3001

# Container paths that a research stage reads besides its own source tree.
# Exported because the runtime profile and the container environment both have
# to agree on them: the profile decides what the stage's file tools may open,
# and the environment decides where those things actually live. If the two
# drift, the stage is denied the very files it was given.
CONTAINER_SOURCE_ROOT = f"{_CONTAINER_WORKSPACE}/source"
CONTAINER_RUNTIME_HOME = f"{_CONTAINER_WORKSPACE}/.cloudcli"
CONTAINER_CLAUDE_CONFIG_DIR = f"{CONTAINER_RUNTIME_HOME}/.claude"
CONTAINER_RESEARCH_ROOT = f"{_CONTAINER_WORKSPACE}/.research"
CONTAINER_SKILLS_ROOT = "/app/skills"

# Roots every research stage may read. Writing stays limited to the paths each
# stage owns; reading is broad because a stage must build on the earlier stages'
# output, follow its own skill files, and consult the documents the runtime
# hands it — none of which live under the source tree it was assigned.
READABLE_ROOTS = (
    CONTAINER_RUNTIME_HOME,
    CONTAINER_RESEARCH_ROOT,
    CONTAINER_SKILLS_ROOT,
)


def paper_workspace_container_name(workspace_id: uuid.UUID | str) -> str:
    """Return the stable Docker container name for one research workspace."""
    return f"{_PAPER_WORKSPACE_NAME_PREFIX}{str(workspace_id).replace('-', '')}"


def paper_workspace_runtime_token(workspace_id: uuid.UUID | str) -> str:
    """Derive the private service token for one workspace runtime."""
    message = f"paper-runtime:{workspace_id}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperWorkspaceExecSpec:
    """Describe one persistent native research workspace container."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    host_workspace: str


def _container_mount_matches(container, host_workspace: str) -> bool:
    expected = str(Path(host_workspace).resolve())
    for mount in container.attrs.get("Mounts", []):
        if mount.get("Destination") == _CONTAINER_WORKSPACE:
            try:
                return str(Path(str(mount.get("Source") or "")).resolve()) == expected
            except OSError:
                return False
    return False


def _create_workspace_container(spec: PaperWorkspaceExecSpec, client, image):
    health_command = (
        "node -e \"fetch('http://127.0.0.1:3001/health')"
        ".then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))\""
    )
    return client.containers.run(
        image.id,
        name=paper_workspace_container_name(spec.workspace_id),
        command=["/app/paper-agent/workspace-entrypoint.sh"],
        working_dir=CONTAINER_SOURCE_ROOT,
        user="65534:65534",
        detach=True,
        network=DOCKER_NETWORK_NAME,
        restart_policy={"Name": "unless-stopped"},
        security_opt=["no-new-privileges"],
        mem_limit=settings.KNOWLEDGE_PARSER_MEMORY_LIMIT,
        nano_cpus=int(settings.KNOWLEDGE_PARSER_CPU_LIMIT * 1e9),
        environment={
            "CLAUDE_CONFIG_DIR": CONTAINER_CLAUDE_CONFIG_DIR,
            "DATABASE_PATH": f"{CONTAINER_RUNTIME_HOME}/auth.db",
            "HOME": CONTAINER_RUNTIME_HOME,
            "LLM4AD_CLAUDE_CONFIG_DIR": CONTAINER_CLAUDE_CONFIG_DIR,
            "LLM4AD_EMBEDDED_MODE": "true",
            "LLM4AD_RUNTIME_TOKEN": paper_workspace_runtime_token(spec.workspace_id),
            "LLM4AD_SKILLS_ROOT": CONTAINER_SKILLS_ROOT,
            "LLM4AD_WORKSPACE_NAME": "Research workspace",
            "LLM4AD_WORKSPACE_ROOT": CONTAINER_SOURCE_ROOT,
            "SERVER_PORT": str(_CLOUDCLI_PORT),
            "VITE_IS_PLATFORM": "true",
            "WORKSPACES_ROOT": CONTAINER_SOURCE_ROOT,
        },
        healthcheck={
            "test": ["CMD-SHELL", health_command],
            "interval": 10_000_000_000,
            "timeout": 3_000_000_000,
            "retries": 6,
            "start_period": 20_000_000_000,
        },
        volumes={
            str(Path(spec.host_workspace).resolve()): {
                "bind": _CONTAINER_WORKSPACE,
                "mode": "rw",
            }
        },
        labels={
            _PAPER_WORKSPACE_LABEL: str(spec.workspace_id),
            _PAPER_WORKSPACE_OWNER_LABEL: str(spec.user_id),
        },
    )


def reconcile_paper_workspace_containers() -> int:
    """Remove workspace containers left running a superseded image.

    Called once at backend startup. Every one of these containers serves the
    embedded research UI, whose client and server bundles are baked into the
    image rather than mounted, so a container that outlives its image keeps
    serving the code it was created from — a rebuild alone changes nothing for
    a workspace the user already opened.

    `ensure_paper_workspace_container` already recreates a workspace when the
    image id changes, but it only runs when a session is opened. Deploying a new
    image while nobody happens to open a session leaves stale containers around
    indefinitely, and the next person to open one gets the old bundles with no
    indication anything is out of date. Reconciling here makes the rebuild take
    effect at deploy time instead of depending on that timing.

    Containers whose image still matches are left untouched, so a routine
    restart does not interrupt work in progress. Runs are therefore only
    discarded when the code they are running has actually been replaced, which
    is the case where they could not have finished correctly anyway.

    Returns:
        How many containers were removed.
    """
    try:
        client = get_docker_client()
    except Exception:
        logger.exception("科研工作区容器对账失败：无法连接 Docker")
        return 0

    try:
        current_image = client.images.get(settings.KNOWLEDGE_PARSER_IMAGE)
    except ImageNotFound:
        # Nothing to compare against, and no container can be stale relative to
        # an image that does not exist yet. The next session start reports the
        # missing image, which is the clearer error for the user to act on.
        logger.warning("科研工作区容器对账跳过：镜像 {} 不存在", settings.KNOWLEDGE_PARSER_IMAGE)
        return 0

    removed = 0
    try:
        containers = client.containers.list(
            all=True,
            filters={"name": _PAPER_WORKSPACE_NAME_PREFIX},
        )
        for container in containers:
            if container.attrs.get("Image") == current_image.id:
                continue
            logger.info("科研工作区容器 {} 运行的是已淘汰的镜像，正在重建", container.name)
            container.remove(force=True, v=True)
            removed += 1
    except Exception:
        logger.exception("科研工作区容器对账失败")
    if removed:
        logger.info("已重建 {} 个科研工作区容器", removed)
    return removed


def ensure_paper_workspace_container(spec: PaperWorkspaceExecSpec):
    """Start or recreate the stable container for one research workspace."""
    client = get_docker_client()
    try:
        image = client.images.get(settings.KNOWLEDGE_PARSER_IMAGE)
    except ImageNotFound as exc:
        raise ImageNotFound(f"Paper workspace image {settings.KNOWLEDGE_PARSER_IMAGE} is not available") from exc

    name = paper_workspace_container_name(spec.workspace_id)
    try:
        container = client.containers.get(name)
        container.reload()
        labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
        valid_identity = labels.get(_PAPER_WORKSPACE_LABEL) == str(spec.workspace_id) and labels.get(
            _PAPER_WORKSPACE_OWNER_LABEL
        ) == str(spec.user_id)
        current_image_id = container.attrs.get("Image")
        if (
            not valid_identity
            or current_image_id != image.id
            or not _container_mount_matches(container, spec.host_workspace)
        ):
            container.remove(force=True, v=True)
            container = _create_workspace_container(spec, client, image)
        elif container.status != "running":
            container.start()
            container.reload()
    except NotFound:
        container = _create_workspace_container(spec, client, image)
    return container


def stop_paper_workspace_container(
    workspace_id: uuid.UUID | str,
    *,
    remove: bool = False,
) -> bool:
    """Stop a project container and optionally remove its disposable shell."""
    try:
        container = get_docker_client().containers.get(paper_workspace_container_name(workspace_id))
    except NotFound:
        return False
    try:
        if remove:
            container.remove(force=True, v=True)
        elif container.status == "running":
            container.stop(timeout=5)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Could not stop paper workspace container {}", workspace_id)
        return False


def _workspaces_with_inflight_runs(workspace_ids: list[str]) -> set[str]:
    """Return which of the given workspace ids have a stage run in flight.

    A run's container must never be reclaimed underneath it: stopping the
    container kills the agent process inside it, and the work is lost with no
    way to resume. The activity timestamp cannot carry this on its own — it is
    written when a stage opens and when a result is published, so a stage that
    runs long enough to cross the idle threshold would be stopped mid-run.

    Returns an empty set when the lookup fails, which deliberately favours
    leaving containers running over stopping one that is mid-stage.
    """
    if not workspace_ids:
        return set()
    try:
        parsed = [uuid.UUID(value) for value in workspace_ids]
    except ValueError:
        logger.warning("科研工作区空闲回收：跳过无法解析的工作区 ID {}", workspace_ids)
        return set()
    try:
        with get_db_session() as db:
            rows = db.exec(
                select(models.PaperAgentRun.workspace_id).where(
                    models.PaperAgentRun.workspace_id.in_(parsed),
                    models.PaperAgentRun.status.in_(
                        [
                            models.PaperAgentRunStatus.PENDING.value,
                            models.PaperAgentRunStatus.RUNNING.value,
                        ]
                    ),
                )
            ).all()
    except Exception:  # noqa: BLE001
        logger.exception("科研工作区空闲回收失败：无法查询进行中的阶段")
        # Fail safe: treat every candidate as busy rather than stopping a run
        # that might be in progress.
        return set(workspace_ids)
    return {str(workspace_id) for workspace_id in rows}


def stop_idle_paper_workspace_containers() -> int:
    """Stop containers for research workspaces that have gone quiet.

    One container per workspace is inherent to the design — the source root, the
    stage write boundary and the session directory are all fixed when the
    container is created, so several workspaces cannot share one. The count
    therefore grows with the number of topics a user keeps, and nothing else
    reclaims them: removal only happens when a workspace is deleted.

    Stopping is the right reclamation, not removing. The source tree is a bind
    mount, so nothing is lost, and `ensure_paper_workspace_container` already
    restarts a stopped container rather than recreating it, which keeps the
    user's session. A stopped container costs no memory and no CPU, so an
    inactive topic stops consuming the budget its idle limit reserves.

    Returns:
        How many containers were stopped.
    """
    threshold = time.time() - settings.PAPER_WORKSPACE_IDLE_TIMEOUT_SECONDS
    try:
        idle_workspaces = pop_idle_paper_workspaces(threshold)
    except Exception:  # noqa: BLE001
        logger.exception("科研工作区空闲回收失败：无法读取活跃时间")
        return 0

    # A workspace whose stage is still running is busy no matter what its
    # activity timestamp says, so it is re-tracked as active and left alone.
    busy = _workspaces_with_inflight_runs(idle_workspaces)
    for workspace_id in sorted(busy):
        try:
            touch_paper_workspace_active(workspace_id)
        except Exception:  # noqa: BLE001
            logger.exception("科研工作区空闲回收：重新标记活跃失败 {}", workspace_id)

    stopped = 0
    for workspace_id in idle_workspaces:
        if workspace_id in busy:
            continue
        try:
            container = get_docker_client().containers.get(paper_workspace_container_name(workspace_id))
        except NotFound:
            # Already gone (deleted workspace, or never created). Nothing to stop
            # and nothing to re-track: the next session start recreates it.
            continue
        except Exception:  # noqa: BLE001
            logger.exception("科研工作区空闲回收失败：无法查询容器 {}", workspace_id)
            continue
        if container.status != "running":
            continue
        try:
            container.stop(timeout=5)
        except Exception:  # noqa: BLE001
            logger.exception("停止空闲科研工作区容器失败: {}", container.name)
            continue
        stopped += 1
        logger.info("已停止空闲科研工作区容器: {}", container.name)
    return stopped


async def run_paper_workspace_idle_cleanup_loop() -> None:
    """Periodically stop idle research workspace containers.

    Runs as a background task started at application startup and cancelled on
    shutdown, mirroring the code-server cleanup loop. Exceptions are logged and
    swallowed so a transient Docker or Redis failure cannot kill the loop.
    """
    interval = settings.PAPER_WORKSPACE_CLEANUP_INTERVAL_SECONDS
    logger.info(
        "启动科研工作区空闲清理循环（间隔 {}s，空闲阈值 {}s）",
        interval,
        settings.PAPER_WORKSPACE_IDLE_TIMEOUT_SECONDS,
    )
    while True:
        try:
            await asyncio.to_thread(stop_idle_paper_workspace_containers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("科研工作区空闲清理任务异常: {}", exc)
        await asyncio.sleep(interval)


def handle_host(workspace_id: uuid.UUID | str) -> str:
    """Return the project container's stable Docker-network hostname."""
    return paper_workspace_container_name(workspace_id)
