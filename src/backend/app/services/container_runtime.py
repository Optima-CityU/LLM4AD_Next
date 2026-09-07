"""通用容器作业 Runner。

与 Celery / Redis / DB 解耦的容器生命周期封装：启动、等待（``events.jsonl``
事件流 tail 与 stdout 透传）、停止。可承载两类通信模型：

- 批处理：``start()`` → ``wait()`` → ``stop()``，或一步式 :meth:`ContainerJob.run`。
- 短驻服务：``start()`` 取 :class:`ContainerHandle`（含可达地址），调用方自行经
  HTTP/SSE 交互，结束后 ``stop()``；不使用 ``wait()``。

结构化输出走挂载盘上的 NDJSON 文件，事件内容保持领域无关（仅原样回调，类型路由
由调用方负责）。场景专属前置（如配置加密、数据下载）由调用方处理。
"""

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from docker.errors import APIError, ImageNotFound, NotFound
from loguru import logger

from app.core.config import settings
from app.core.constants import DOCKER_NETWORK_NAME
from app.core.docker import get_docker_client

# 容器退出状态轮询与事件文件 tail 的默认间隔（秒）。
_DEFAULT_EXIT_POLL_INTERVAL = 1.0
_DEFAULT_EVENTS_POLL_INTERVAL = 0.3

# 线程在停止时的 join 超时（秒）。
_THREAD_JOIN_TIMEOUT = 10

# 未显式指定 name 时自动生成的容器名前缀。
_AUTO_NAME_PREFIX = "container-job-"

# 本模块创建的容器统一打的标签，用于按标签批量回收孤儿容器。
MANAGED_LABEL_KEY = "llm4ad.managed-by"
MANAGED_LABEL_VALUE = "container_runtime"


class ContainerJobStatus(StrEnum):
    """容器作业终态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    OOM = "oom"


@dataclass(frozen=True)
class ContainerJobSpec:
    """容器作业的运行规格（不可变）。

    路径语义：``mounts`` 的 key 为宿主机路径（传给 Docker 卷绑定），``events_file``
    为当前进程可见的事件文件路径（tailer 在本进程内打开）；二者在容器化部署下可能
    不同，由调用方负责转换。

    Attributes:
        name: 容器名称，需全局唯一。留空则自动生成；仅当需要“重跑覆盖同名残留”
            的幂等语义时才必须显式指定。
        image: 镜像名称（必填）。
        command: 启动命令；``None`` 使用镜像 CMD。
        working_dir: 容器内工作目录；``None`` 使用镜像默认。
        user: 容器运行用户（Docker ``user`` 格式）；``None`` 使用镜像默认。
        mounts: ``{宿主机路径: 容器内路径}`` 卷绑定（rw）。
        env: 注入容器的环境变量。
        mem_limit: 内存限制（如 ``"4g"``）；``None`` 取 ``settings.TASK_CONTAINER_MEMORY_LIMIT``。
        nano_cpus: CPU 限制（纳核，1 核 = 1e9）；``None`` 取 ``settings.TASK_CONTAINER_CPU_LIMIT`` 换算。
        network: Docker 网络名；``None`` 取项目默认网络 ``DOCKER_NETWORK_NAME``，
            ``""`` 表示不加入自定义网络（用于发布端口的本地开发）。
        ports: 端口发布映射（Docker SDK 格式）；``None`` 不发布。
        security_opt: 安全选项，默认 ``["no-new-privileges"]``。
        labels: 追加到容器的标签，与管理标签合并。
        events_file: NDJSON 事件文件路径；``None`` 不 tail。
        timeout: 等待硬超时（秒）；``None`` 取 ``settings.TASK_TIME_LIMIT``。
    """

    name: str = ""
    image: str = ""
    command: list[str] | None = None
    working_dir: str | None = None
    user: str | None = None
    mounts: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    mem_limit: str | None = None
    nano_cpus: int | None = None
    network: str | None = None
    ports: dict | None = None
    security_opt: list[str] = field(default_factory=lambda: ["no-new-privileges"])
    labels: dict[str, str] = field(default_factory=dict)
    events_file: str | None = None
    timeout: int | None = None

    def __post_init__(self) -> None:
        if not self.image or not self.image.strip():
            raise ValueError("ContainerJobSpec.image 不能为空")
        # name 留空则自动生成；frozen=True 下经 object.__setattr__ 写入。
        if self.name == "":
            object.__setattr__(self, "name", f"{_AUTO_NAME_PREFIX}{uuid.uuid4().hex[:16]}")
        elif not self.name.strip():
            raise ValueError("ContainerJobSpec.name 不能为纯空白")


@dataclass(frozen=True)
class ContainerHandle:
    """已启动容器的句柄。

    Attributes:
        container_id: Docker 容器 ID。
        name: 容器名称。
        host: 供需要连入容器的场景使用的可达地址：加入网络时为容器名（DNS），
            发布端口时为 ``127.0.0.1``，否则为 ``None``。
    """

    container_id: str
    name: str
    host: str | None = None


@dataclass(frozen=True)
class ContainerJobResult:
    """容器作业终态结果。

    Attributes:
        status: 终态枚举。
        exit_code: 容器退出码；取消/超时/容器消失时为 -1。
        error: 失败/异常时的简要说明；成功时为 ``None``。
        container_id: 对应容器 ID（便于调用方追溯）。
    """

    status: ContainerJobStatus
    exit_code: int
    error: str | None = None
    container_id: str | None = None


@dataclass
class ContainerJobCallbacks:
    """容器作业生命周期回调（均可选）。

    回调内部抛出的异常会被捕获并记录，不影响 runner 流程。

    Attributes:
        on_started: 容器启动成功后回调，入参 :class:`ContainerHandle`。
        on_event: ``events_file`` 中每条 NDJSON 事件的回调（需配合 ``spec.events_file``）。
        on_stdout: 容器 stdout/stderr 每一行（已去尾换行、原样透传）的回调。
        on_exit: 容器达到终态后回调一次，入参 :class:`ContainerJobResult`。
        check_cancelled: 轮询期间调用，返回 ``True`` 时停止容器并以 ``CANCELLED`` 收尾。
    """

    on_started: Callable[[ContainerHandle], None] | None = None
    on_event: Callable[[dict], None] | None = None
    on_stdout: Callable[[str], None] | None = None
    on_exit: Callable[[ContainerJobResult], None] | None = None
    check_cancelled: Callable[[], bool] | None = None


class ContainerJob:
    """单个容器作业的生命周期封装。

    典型用法（批处理一步到位）::

        result = ContainerJob(spec, callbacks).run()

    或分步控制（短驻服务）::

        job = ContainerJob(spec, callbacks)
        handle = job.start()
        # ... 调用方自行与 handle.host 交互 ...
        job.stop()

    实例非线程安全：一个实例只应驱动一个容器的一次生命周期。``start()`` 仅可调用
    一次；``stop()`` 幂等。
    """

    def __init__(
        self,
        spec: ContainerJobSpec,
        callbacks: ContainerJobCallbacks | None = None,
        *,
        client=None,
        exit_poll_interval: float = _DEFAULT_EXIT_POLL_INTERVAL,
        events_poll_interval: float = _DEFAULT_EVENTS_POLL_INTERVAL,
    ) -> None:
        """初始化容器作业。

        Args:
            spec: 容器运行规格。
            callbacks: 生命周期回调；省略时为空回调集。
            client: 可选的 Docker 客户端（便于测试注入）；省略时使用共享单例。
            exit_poll_interval: 容器退出状态轮询间隔（秒）。
            events_poll_interval: 事件文件 tail 间隔（秒）。
        """
        self._spec = spec
        self._callbacks = callbacks or ContainerJobCallbacks()
        self._client = client or get_docker_client()
        self._exit_poll_interval = exit_poll_interval
        self._events_poll_interval = events_poll_interval

        self._handle: ContainerHandle | None = None
        self._stop_event = threading.Event()
        self._stopped = False
        self._stop_lock = threading.Lock()

    # ---- 只读属性 ----

    @property
    def spec(self) -> ContainerJobSpec:
        """返回运行规格。"""
        return self._spec

    @property
    def handle(self) -> ContainerHandle | None:
        """返回容器句柄；未启动时为 ``None``。"""
        return self._handle

    @property
    def container_id(self) -> str | None:
        """返回容器 ID；未启动时为 ``None``。"""
        return self._handle.container_id if self._handle else None

    # ---- 生命周期 ----

    def start(self) -> ContainerHandle:
        """创建并启动容器。

        先移除同名残留容器、校验镜像存在，再以 detached 方式启动。启动成功后触发
        ``on_started`` 回调。

        Returns:
            已启动容器的句柄。

        Raises:
            RuntimeError: 重复启动。
            docker.errors.ImageNotFound: 镜像不存在。
            docker.errors.APIError: Docker API 调用失败。
        """
        if self._handle is not None:
            raise RuntimeError(f"容器作业 {self._spec.name} 已启动，不能重复启动")

        self._remove_stale()
        self._ensure_image()

        try:
            container = self._client.containers.run(**self._build_run_kwargs())
        except APIError:
            logger.exception(f"启动容器失败: name={self._spec.name}")
            raise

        self._handle = ContainerHandle(
            container_id=container.id,
            name=self._spec.name,
            host=self._resolve_host(),
        )
        logger.info(f"容器作业已启动: name={self._spec.name}, id={container.id[:12]}")
        self._safe_call(self._callbacks.on_started, self._handle)
        return self._handle

    def wait(self, *, poll_interval: float | None = None) -> ContainerJobResult:
        """阻塞等待容器结束（批处理场景）。

        期间按需启动两个后台线程：tail ``events_file`` 并回调 ``on_event``；透传
        stdout/stderr 并回调 ``on_stdout``。容器退出/取消/超时/OOM 后停止线程、
        做最终事件 drain，并以 ``on_exit`` 回调一次结果。

        Args:
            poll_interval: 退出状态轮询间隔（秒）；省略时用构造参数。

        Returns:
            作业终态结果。

        Raises:
            RuntimeError: 容器尚未启动。
        """
        if self._handle is None:
            raise RuntimeError("容器尚未启动，无法等待")

        interval = poll_interval if poll_interval is not None else self._exit_poll_interval
        self._stop_event.clear()
        threads = self._start_relay_threads()
        try:
            result = self._poll_until_exit(interval)
        finally:
            self._stop_event.set()
            for t in threads:
                t.join(timeout=_THREAD_JOIN_TIMEOUT)
            self._final_drain_events()

        self._safe_call(self._callbacks.on_exit, result)
        return result

    def run(self, *, poll_interval: float | None = None) -> ContainerJobResult:
        """批处理便捷入口：``start()`` → ``wait()`` → 自动 ``stop()``。

        Args:
            poll_interval: 退出状态轮询间隔（秒）。

        Returns:
            作业终态结果。
        """
        with self:
            self.start()
            return self.wait(poll_interval=poll_interval)

    def stop(self, *, kill: bool = False) -> None:
        """停止并移除容器（幂等）。

        Args:
            kill: ``True`` 时先 ``SIGKILL`` 立即终止再移除（用于需快速响应的取消）。
        """
        self._stop_event.set()
        with self._stop_lock:
            if self._handle is None or self._stopped:
                return
            self._stopped = True

        cid = self._handle.container_id
        try:
            container = self._client.containers.get(cid)
            if kill:
                try:
                    container.kill()
                except Exception:  # noqa: BLE001 - 已在退出/不可 kill 时忽略
                    logger.debug(f"kill 容器 {cid[:12]} 失败（可能已退出）", exc_info=True)
            container.remove(force=True, v=True)
            logger.info(f"容器 {cid[:12]} 已清理")
        except NotFound:
            pass
        except Exception as e:  # noqa: BLE001 - 清理失败不应向上抛出
            logger.error(f"清理容器 {cid[:12]} 失败: {e}")

    # ---- 上下文管理器 ----

    def __enter__(self) -> "ContainerJob":
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # ---- 内部：启动相关 ----

    def _build_run_kwargs(self) -> dict:
        """根据 spec 构造 ``containers.run`` 关键字参数（仅传入已设置项）。"""
        kwargs: dict = {
            "image": self._spec.image,
            "name": self._spec.name,
            "detach": True,
            "environment": dict(self._spec.env),
            "security_opt": list(self._spec.security_opt),
            "labels": {MANAGED_LABEL_KEY: MANAGED_LABEL_VALUE, **self._spec.labels},
        }
        if self._spec.command is not None:
            kwargs["command"] = self._spec.command
        if self._spec.working_dir:
            kwargs["working_dir"] = self._spec.working_dir
        if self._spec.user:
            kwargs["user"] = self._spec.user
        if self._spec.mounts:
            kwargs["volumes"] = {
                host.rstrip("/").rstrip("\\"): {"bind": container, "mode": "rw"}
                for host, container in self._spec.mounts.items()
            }
        # 资源限额未显式指定时回退到项目默认，避免起不受限的容器。
        mem_limit = (
            self._spec.mem_limit
            if self._spec.mem_limit is not None
            else settings.TASK_CONTAINER_MEMORY_LIMIT
        )
        if mem_limit:
            kwargs["mem_limit"] = mem_limit
        nano_cpus = (
            self._spec.nano_cpus
            if self._spec.nano_cpus is not None
            else int(settings.TASK_CONTAINER_CPU_LIMIT * 1e9)
        )
        if nano_cpus:
            kwargs["nano_cpus"] = nano_cpus
        network = self._effective_network()
        if network:
            kwargs["network"] = network
        if self._spec.ports:
            kwargs["ports"] = self._spec.ports
        return kwargs

    def _effective_network(self) -> str | None:
        """实际加入的网络。

        ``None`` → 回退到项目默认网络 ``DOCKER_NETWORK_NAME``；空串 ``""`` → 显式
        不加入自定义网络（Docker 默认 bridge）；其余 → 原样使用。
        """
        if self._spec.network is None:
            return DOCKER_NETWORK_NAME
        return self._spec.network or None

    def _resolve_host(self) -> str | None:
        """推断容器的可达地址（best-effort）。

        加入网络时返回容器名（同网络内 DNS 可解析）；否则若发布了端口返回
        ``127.0.0.1``；都没有则返回 ``None``。调用方可据自身部署形态覆盖。
        """
        if self._effective_network():
            return self._spec.name
        if self._spec.ports:
            return "127.0.0.1"
        return None

    def _remove_stale(self) -> None:
        """移除可能存在的同名残留容器。"""
        try:
            old = self._client.containers.get(self._spec.name)
            logger.warning(f"发现同名残留容器 {self._spec.name}，正在移除")
            old.remove(force=True, v=True)
        except NotFound:
            pass

    def _ensure_image(self) -> None:
        """校验镜像存在，给出可操作的报错。"""
        try:
            self._client.images.get(self._spec.image)
        except ImageNotFound:
            raise ImageNotFound(
                f"容器镜像 {self._spec.image} 不存在，请先构建后再运行。"
            )

    # ---- 内部：等待与转发 ----

    def _start_relay_threads(self) -> list[threading.Thread]:
        """按回调需要启动事件 tail / stdout 透传线程。"""
        threads: list[threading.Thread] = []
        if self._spec.events_file and self._callbacks.on_event:
            t = threading.Thread(
                target=self._tail_events_loop,
                name=f"evt-tail-{self._spec.name}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        if self._callbacks.on_stdout:
            t = threading.Thread(
                target=self._relay_stdout,
                name=f"stdout-{self._spec.name}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        return threads

    def _poll_until_exit(self, poll_interval: float) -> ContainerJobResult:
        """轮询容器直至退出/取消/超时，返回映射后的终态结果。"""
        timeout = self._spec.timeout if self._spec.timeout is not None else settings.TASK_TIME_LIMIT
        cid = self._handle.container_id  # type: ignore[union-attr]
        start = time.monotonic()

        while True:
            try:
                container = self._client.containers.get(cid)
                container.reload()
            except NotFound:
                logger.warning(f"容器 {cid[:12]} 已不存在")
                return ContainerJobResult(ContainerJobStatus.FAILED, -1, "容器已消失", cid)

            if self._check_cancelled():
                logger.info(f"作业被取消，正在停止容器 {cid[:12]}")
                self._safe_container_action(container, "stop", timeout=10)
                return ContainerJobResult(ContainerJobStatus.CANCELLED, -1, "已取消", cid)

            if container.status in ("exited", "dead"):
                state = container.attrs.get("State", {})
                exit_code = state.get("ExitCode", -1)
                if state.get("OOMKilled", False):
                    logger.warning(f"容器 {cid[:12]} 因内存超限被终止")
                    return ContainerJobResult(ContainerJobStatus.OOM, exit_code, "内存超限被终止", cid)
                if exit_code == 0:
                    logger.info(f"容器 {cid[:12]} 正常退出")
                    return ContainerJobResult(ContainerJobStatus.COMPLETED, 0, None, cid)
                logger.warning(f"容器 {cid[:12]} 异常退出: exit_code={exit_code}")
                return ContainerJobResult(ContainerJobStatus.FAILED, exit_code, f"异常退出，退出码 {exit_code}", cid)

            if time.monotonic() - start > timeout:
                logger.warning(f"容器 {cid[:12]} 执行超时 ({timeout}s)，正在终止")
                self._safe_container_action(container, "kill")
                return ContainerJobResult(ContainerJobStatus.TIMED_OUT, -1, f"执行超时（{timeout}s）", cid)

            time.sleep(poll_interval)

    def _tail_events_loop(self) -> None:
        """循环 tail 事件文件，直至收到停止信号。"""
        path = self._spec.events_file
        assert path is not None  # 调用前已校验
        self._events_offset = 0
        self._events_buffer = b""
        while not self._stop_event.is_set():
            self._drain_events_once(path)
            self._stop_event.wait(self._events_poll_interval)

    def _final_drain_events(self) -> None:
        """停止后做一次最终 drain，捕获最后一轮轮询到退出之间写入的事件。"""
        path = self._spec.events_file
        if not path or not self._callbacks.on_event:
            return
        # 线程可能未启动（无 on_event）；确保偏移/缓冲已初始化。
        if not hasattr(self, "_events_offset"):
            self._events_offset = 0
            self._events_buffer = b""
        self._drain_events_once(path)

    def _drain_events_once(self, path: str) -> None:
        """读取新增字节并按完整行回调 ``on_event``；末尾不完整行留待下次。"""
        try:
            if not os.path.isfile(path):
                return
            size = os.path.getsize(path)
            if size <= self._events_offset:
                return
            with open(path, "rb") as f:
                f.seek(self._events_offset)
                chunk = f.read()
            self._events_offset += len(chunk)
            self._events_buffer += chunk
            while b"\n" in self._events_buffer:
                line, self._events_buffer = self._events_buffer.split(b"\n", 1)
                self._emit_event_line(line)
        except Exception:  # noqa: BLE001 - tail 失败不应影响主流程
            logger.debug(f"读取事件文件失败: {path}", exc_info=True)

    def _emit_event_line(self, raw: bytes) -> None:
        """解析单行 NDJSON 并回调；非 JSON 行跳过。"""
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            logger.debug(f"跳过非 JSON 事件行: {text[:200]}")
            return
        self._safe_call(self._callbacks.on_event, event)

    def _relay_stdout(self) -> None:
        """按行回调容器 stdout/stderr，原样透传不做加工。

        以字节缓冲、按整行再 decode，避免多字节 UTF-8 字符跨 chunk 边界被拆坏。
        """
        cid = self._handle.container_id  # type: ignore[union-attr]
        try:
            container = self._client.containers.get(cid)
            buffer = b""
            for chunk in container.logs(stream=True, follow=True):
                if self._stop_event.is_set():
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
                    if line.strip():
                        self._safe_call(self._callbacks.on_stdout, line)
            tail = buffer.rstrip(b"\r").decode("utf-8", errors="replace")
            if tail.strip():
                self._safe_call(self._callbacks.on_stdout, tail)
        except Exception:  # noqa: BLE001 - 透传线程异常仅记录
            logger.debug(f"stdout 透传线程结束: {cid[:12]}", exc_info=True)

    # ---- 内部：安全包装 ----

    @staticmethod
    def _safe_call(callback, *args) -> None:
        """调用回调并吞掉其异常（仅记录），避免污染 runner 流程。"""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - 回调异常隔离
            logger.exception("容器作业回调执行失败")

    def _check_cancelled(self) -> bool:
        """安全调用取消检查回调；异常时视为未取消。"""
        callback = self._callbacks.check_cancelled
        if callback is None:
            return False
        try:
            return bool(callback())
        except Exception:  # noqa: BLE001 - 检查异常不应误判为取消
            logger.exception("check_cancelled 回调执行失败")
            return False

    @staticmethod
    def _safe_container_action(container, action: str, **kwargs) -> None:
        """安全执行容器动作（stop/kill），忽略其异常。"""
        try:
            getattr(container, action)(**kwargs)
        except Exception:  # noqa: BLE001 - 终止动作尽力而为
            logger.debug(f"容器 {action} 动作失败", exc_info=True)


def run_container_job(
    spec: ContainerJobSpec,
    callbacks: ContainerJobCallbacks | None = None,
    *,
    client=None,
    poll_interval: float | None = None,
) -> ContainerJobResult:
    """批处理便捷函数：构造 :class:`ContainerJob` 并执行 ``start→wait→stop``。

    Args:
        spec: 容器运行规格。
        callbacks: 生命周期回调。
        client: 可选 Docker 客户端（测试注入）。
        poll_interval: 退出状态轮询间隔（秒）。

    Returns:
        作业终态结果。
    """
    return ContainerJob(spec, callbacks, client=client).run(poll_interval=poll_interval)


def cleanup_orphaned_containers(
    client=None,
    *,
    extra_filters: dict | None = None,
    exclude_names: set[str] | None = None,
) -> int:
    """按管理标签清理本模块创建的遗留容器。

    供进程/worker 重启时调用，回收上次异常退出后未及 ``stop()`` 的孤儿容器。
    单个容器移除失败不影响其余容器，仅记录日志。

    Args:
        client: 可选 Docker 客户端（测试注入）；省略时使用共享单例。
        extra_filters: 追加的 Docker 过滤条件（与管理标签合并），用于缩小范围，
            例如 ``{"label": "run_id=xxx"}``。
        exclude_names: 需保留的容器名集合（不清理）。多 worker 部署时，别的活
            worker 正在跑的容器名会传进来，避免 worker 启动误杀在跑的容器。

    Returns:
        成功移除的容器数量。
    """
    cli = client or get_docker_client()
    # 管理标签是安全网：只回收本模块创建的容器。extra_filters 里若也带 "label"，
    # 必须与管理标签**合并成列表**（docker 对 label 做 AND），而不是 dict.update
    # 直接覆盖——否则安全网被丢，会误伤任何命中 extra label 的容器。
    filters: dict = {"label": f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}"}
    if extra_filters:
        for key, value in extra_filters.items():
            if key == "label":
                filters["label"] = [filters["label"], value]
            else:
                filters[key] = value

    try:
        containers = cli.containers.list(all=True, filters=filters)
    except Exception:  # noqa: BLE001 - 列举失败不应向上抛出
        logger.error("列出孤儿容器失败", exc_info=True)
        return 0

    removed = 0
    for container in containers:
        identifier = getattr(container, "name", None) or getattr(container, "id", "?")
        if exclude_names and getattr(container, "name", None) in exclude_names:
            # 别的活 worker 正在跑的容器：跳过，不误杀。
            continue
        try:
            container.remove(force=True, v=True)
            removed += 1
            logger.warning(f"已清理孤儿容器: {identifier}")
        except Exception as e:  # noqa: BLE001 - 单个失败不影响其余
            logger.error(f"清理孤儿容器失败 {identifier}: {e}")
    return removed
