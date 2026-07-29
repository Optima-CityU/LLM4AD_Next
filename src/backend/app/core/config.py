"""
应用配置。

使用 pydantic-settings 从环境变量和 .env 文件加载配置，
提供类型安全的配置访问和自动验证。
"""

import secrets
import warnings
from typing import Any, Literal, Self

from pydantic import (
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    """解析 CORS 来源配置，支持逗号分隔字符串或 JSON 数组格式。"""
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    """应用全局配置类。

    从 .env 文件和环境变量中加载配置项，
    支持计算属性和验证器自动推导派生值。
    """

    model_config = SettingsConfigDict(
        env_file="../../docker/.env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # ---- API 基础配置 ----
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 供应商凭据加密专用密钥。强烈建议显式设置（非 local 环境强制）：留空会回退到
    # SECRET_KEY 派生，而 SECRET_KEY 轮换会连带废掉已加密凭据。设置后请勿再变更，
    # 否则已加密的存量凭据将无法解密。
    PROVIDER_ENCRYPTION_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 访问令牌过期时间：8 小时
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 刷新令牌过期时间：7 天
    CODE_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 1  # Code-Server 令牌过期时间：1 天

    # ---- 服务器配置 ----
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    # ---- 项目信息 ----
    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None

    # ---- 数据库配置 ----
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        """根据各数据库配置项拼接完整的 PostgreSQL 连接字符串。"""
        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    # ---- 邮件配置 ----
    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        """默认使用项目名称作为邮件发送者名称。"""
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48  # 密码重置令牌有效期（小时）
    EMAIL_VERIFY_CODE_EXPIRE_MINUTES: int = 15  # 邮箱验证码有效期（分钟）
    SKIP_EMAIL_VERIFICATION: bool = False  # 跳过邮箱验证码校验（部署调试用）

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        """判断邮件功能是否可用（需配置 SMTP 主机和发送邮箱）。"""
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"

    # ---- 初始超级管理员 ----
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        """检查是否使用了默认密钥，非本地环境下禁止使用默认值。"""
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", for security, please change it, at least for deployments.'
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        """强制校验关键密钥不能使用默认值。"""
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret("FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD)
        return self

    @model_validator(mode="after")
    def _enforce_pinned_encryption_key(self) -> Self:
        """校验供应商凭据加密密钥已显式设置。

        加密密钥源优先使用 ``PROVIDER_ENCRYPTION_KEY``，仅在其缺省时回退到
        ``SECRET_KEY`` 派生（便于本地开箱即用）。但 ``SECRET_KEY`` 是多用途共享
        密钥，正常的安全轮换会连带使已加密凭据无法解密；因此非 local 环境强制
        要求显式设置专用的 ``PROVIDER_ENCRYPTION_KEY``，与 ``SECRET_KEY`` 解耦，
        避免轮换 ``SECRET_KEY`` 时静默废掉所有存量凭据。

        非 local 未设置则 fail-closed 报错；local 仅告警，方便本地开发。
        设置后请勿再变更，否则已加密的存量凭据将无法解密。
        """
        if not self.PROVIDER_ENCRYPTION_KEY:
            message = (
                "未设置 PROVIDER_ENCRYPTION_KEY；供应商凭据加密会回退到 SECRET_KEY 派生。"
                "SECRET_KEY 作为多用途密钥一旦轮换、或在裸 python 本地运行时取随机默认值，"
                "都会导致已加密凭据无法解密——表现为后端重启后供应商凭据静默丢失（解密返回空串）。"
                "请在环境变量中显式设置专用且固定的 PROVIDER_ENCRYPTION_KEY。"
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _enforce_llm_proxy_config(self) -> Self:
        """校验启用 LLM 凭据代理时必须配置代理端点。

        ``LLM_PROXY_ENABLE`` 为真却缺 ``LLM_PROXY_BASE_URL`` 是配置不变量错误：
        缺端点则容器无处转发请求。在启动期 fail-closed，使服务拒绝以错误配置启动，
        而非等到第一个任务运行才在请求路径里抛错。
        """
        if self.LLM_PROXY_ENABLE and not self.LLM_PROXY_BASE_URL:
            raise ValueError(
                "LLM_PROXY_ENABLE 已开启但未配置 LLM_PROXY_BASE_URL；"
                "请设置容器内可达的代理基础 URL（如 http://<backend>:8000/api/v1/llm4ad/llmproxy）。"
            )
        return self

    # ---- S3 兼容存储（RustFS）配置 ----
    RUSTFS_ENDPOINT: str
    RUSTFS_ACCESS_KEY: str
    RUSTFS_SECRET_KEY: str

    # ---- Redis 配置 ----
    REDIS_PASSWORD: str
    REDIS_HOST: str
    REDIS_PORT: str

    @computed_field
    @property
    def REDIS_BASE_URL(self) -> str:
        """Redis 基础连接 URL（不含数据库编号）。"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}"

    @computed_field
    @property
    def CELERY_BROKER(self) -> str:
        """Celery 任务队列数据库"""
        return self.REDIS_BASE_URL + "/0"

    @computed_field
    @property
    def CELERY_BACKEND(self) -> str:
        """Celery 结果后端数据库"""
        return self.REDIS_BASE_URL + "/1"

    @property
    def LLM_PROXY_TOKEN_TTL(self) -> int:
        """LLM 代理 token 有效期（秒）。

        从任务硬超时派生：token 在任务开始时签发并倒计时，必须活得比任务全程更久，
        否则跑满时限的任务在末尾请求时 token 已过期（401）。故在硬超时之外预留 1h
        缓冲。任务结束时由 broker 主动吊销，缓冲仅在漏吊销时兜底，不影响安全性。
        """
        return self.TASK_TIME_LIMIT + 3600

    # ---- 内置试用供应商（如 LiteLLM 网关） ----
    BUILTIN_PROVIDER_NAME: str = "builtin-trial"
    BUILTIN_PROVIDER_BASE_URL: str = "" # 为空则跳过 seed，可以包含变量{accessToken}
    BUILTIN_PROVIDER_API_KEY: str = ""
    BUILTIN_PROVIDER_MODELS: str = "" # 多模型分号分隔
    BUILTIN_PROVIDER_MAX_TOKENS: int = 16384
    BUILTIN_PROVIDER_DEFAULT_MODEL: str = "" # 为空则取 MODELS 中的第一个

    # ---- Docker 连接配置 ----
    DOCKER_HOST: str = ""  # Docker daemon address; empty = local socket, e.g. ssh://user@remote-host

    # ---- 文件路径配置 ----
    HOST_PROJECT_HOME: str = r"D:/data/project_home/"  # 宿主机：用户项目目录绝对路径，用于动态创建容器挂载
    DOCKER_PROJECT_HOME: str = "/data/project_home/"  # 容器内：用户项目目录绝对路径

    # ---- 任务容器隔离配置 ----
    TASK_RUNNER_IMAGE: str = "llm4ad-task-runner:latest"  # 任务运行镜像名称
    TASK_CONTAINER_MEMORY_LIMIT: str = "4g"  # 容器内存限制
    TASK_CONTAINER_CPU_LIMIT: float = 2.0                  # 容器 CPU 核心数限制

    # ---- AutoResearch（researchclaw pipeline）隔离容器配置 ----
    # 研究 pipeline 执行 LLM 生成的代码，多用户下必须容器隔离；默认复用任务镜像（一镜多入口）。
    RESEARCH_RUNNER_IMAGE: str = "llm4ad-task-runner:latest"  # 研究运行镜像（默认复用任务镜像）
    RESEARCH_CONTAINER_MEMORY_LIMIT: str = "4g"   # 研究容器内存限制
    RESEARCH_CONTAINER_CPU_LIMIT: float = 2.0     # 研究容器 CPU 核心数限制
    # 研究容器执行硬超时（秒）。不设则 ContainerJobSpec 回落到 TASK_TIME_LIMIT（默认 7 天），
    # 一个卡死的研究容器会挂满 7 天，故此处收一个 backstop。定值须高于健康长任务的内部
    # 墙钟预算之和：experiment.time_budget_sec 默认 7200（2h）+ REFINE 阶段另有 1.5×
    # time_budget_sec（≈3h）的独立墙钟上限，再加 build/figure/9 段文档 stage，健康长任务
    # 可逼近 5~6h。取 12h 作 backstop——远低于 7 天（卡死容器不再挂一周），又舒适高于任何
    # 现实健康 pipeline 的总预算，不会误杀长任务。超时容器被 stop/kill 收 TIMED_OUT。
    RESEARCH_CONTAINER_TIMEOUT: int = 12 * 3600   # 研究容器执行硬超时（秒），默认 12 小时

    # ---- 调参（chat-tune）隔离容器配置 ----
    CHAT_TUNE_CONTAINER_PORT: int = 8800              # 容器内 SSE 服务监听端口（不发布到宿主）
    CHAT_TUNE_CONTAINER_MEMORY_LIMIT: str = "1g"      # 调参容器内存限制
    CHAT_TUNE_CONTAINER_CPU_LIMIT: float = 1.0        # 调参容器 CPU 核心数限制
    CHAT_TUNE_CONTAINER_READY_TIMEOUT: float = 30.0   # 等待容器 SSE 服务就绪的超时（秒）
    CHAT_TUNE_CONTAINER_NETWORK: bool = True           # True=通过 Docker 网络连接容器; False=发布端口到宿主机用 localhost 连接

    # ---- MindMemOS 系统级记忆后端配置 ----
    LLM4AD_MINDMEMOS_ENABLED: bool = False
    LLM4AD_MINDMEMOS_BASE_URL: str = "http://mindmemos-api:8000"
    LLM4AD_MINDMEMOS_API_KEY: str = ""
    LLM4AD_MINDMEMOS_JWT_SECRET: str = "demo-jwt-secret-key"
    LLM4AD_MINDMEMOS_JWT_ISSUER: str = "demo-jwt-gateway"
    LLM4AD_MINDMEMOS_JWT_AUDIENCE: str = "demo-jwt-audience"
    LLM4AD_MINDMEMOS_APP_ID: str = "llm4ad"
    LLM4AD_MINDMEMOS_AGENT_ID: str = "planner"
    LLM4AD_MINDMEMOS_FAIL_OPEN: bool = True
    LLM4AD_MINDMEMOS_REQUEST_TIMEOUT: float = 10.0
    LLM4AD_MINDMEMOS_ADD_TIMEOUT: float = 120.0
    MINDMEMOS_CHAT_MODEL: str = ""
    MINDMEMOS_CHAT_API_BASE: str = ""
    MINDMEMOS_CHAT_API_KEY: str = ""
    MINDMEMOS_CHAT_TIMEOUT: int = 1200
    MINDMEMOS_CHAT_TEMPERATURE: float = 0.0
    MINDMEMOS_EMBED_MODEL: str = ""
    MINDMEMOS_EMBED_API_BASE: str = ""
    MINDMEMOS_EMBED_API_KEY: str = ""
    MINDMEMOS_EMBED_DIMENSIONS: int = 0
    MINDMEMOS_EMBED_TIMEOUT: int = 600
    MINDMEMOS_RERANK_ENABLED: bool = False
    MINDMEMOS_RERANK_MODEL: str = ""
    MINDMEMOS_RERANK_API_BASE: str = ""
    MINDMEMOS_RERANK_API_KEY: str = ""
    MINDMEMOS_RERANK_TIMEOUT: int = 600

    @property
    def mindmemos_chat_configured(self) -> bool:
        """Whether MindMemOS has the fixed system chat model it needs."""
        return bool(
            self.MINDMEMOS_CHAT_MODEL.strip()
            and self.MINDMEMOS_CHAT_API_BASE.strip()
            and self.MINDMEMOS_CHAT_API_KEY.strip()
        )

    @property
    def mindmemos_embedding_configured(self) -> bool:
        """Whether MindMemOS has the fixed system embedding model it needs."""
        return bool(
            self.MINDMEMOS_EMBED_MODEL.strip()
            and self.MINDMEMOS_EMBED_API_BASE.strip()
            and self.MINDMEMOS_EMBED_API_KEY.strip()
            and self.MINDMEMOS_EMBED_DIMENSIONS > 0
        )

    @property
    def mindmemos_rerank_configured(self) -> bool:
        """Whether optional MindMemOS rerank model settings are complete."""
        return bool(
            self.MINDMEMOS_RERANK_ENABLED
            and self.MINDMEMOS_RERANK_MODEL.strip()
            and self.MINDMEMOS_RERANK_API_BASE.strip()
            and self.MINDMEMOS_RERANK_API_KEY.strip()
        )

    @property
    def mindmemos_runtime_available(self) -> bool:
        """Whether LLM4AD should route task memory to MindMemOS."""
        return bool(
            self.LLM4AD_MINDMEMOS_ENABLED
            and self.LLM4AD_MINDMEMOS_BASE_URL.strip()
            and self.LLM4AD_MINDMEMOS_JWT_SECRET.strip()
        )

    # ---- AI 构建 (beta)：AgentScope 单 agent 构建 ----
    # 开启后前端显示 "AI 构建 (Beta)" 入口，beta 轮次走 AgentScope agent
    # （混合：BuildOrchestrator 生成 + agent 用 run_python 验证/重试），
    # 与现有 AI 构建并存。关闭时 beta 入口隐藏、beta 请求回退到旧 AI 构建。
    ENABLE_AI_AGENT_BUILD: bool = True

    # ---- Celery 任务超时 ----
    # 单一来源：Celery 配置与 LLM 代理 token TTL 均由此派生，避免多处魔法数字漂移。
    TASK_TIME_LIMIT: int = 7 * 24 * 3600        # 任务硬超时（秒），默认 7 天
    TASK_SOFT_TIME_LIMIT: int = 1 * 24 * 3600   # 任务软超时（秒），默认 1 天，超时抛 SoftTimeLimitExceeded

    # ---- LLM 凭据代理（credential broker）----
    # 演化任务在隔离容器内 import 执行用户提供的评测脚本，恶意脚本可从环境变量、
    # 配置文件、被递交的 ProviderConfig、进程对象图等多条路径窃取真实 api_key。
    # 开启后：下发给容器的配置里真实 key 被替换为一次性代理 token，base_url 指向
    # 本端点；容器经此反向代理调用大模型，真实凭据只留在 backend 进程。
    LLM_PROXY_ENABLE: bool = False
    # 容器内可达的代理基础 URL（不含末尾斜杠）。生产环境走 Docker 用户网络，按
    # backend 容器名 DNS 解析，例如 http://llm4ad-web-backend-1:8000/api/v1/llm4ad/llmproxy；
    # 本地裸跑可用 http://host.docker.internal:8000/api/v1/llm4ad/llmproxy。
    LLM_PROXY_BASE_URL: str = ""

    # ---- 任务文件存储限制 ----
    TASK_MAX_FILE_SIZE_MB: int = 50   # 单文件上传大小上限（MB）
    TASK_MAX_STORAGE_MB: int = 100    # 单任务输入数据总存储上限（MB）

    # ---- 任务运行时其它限制 ----
    # 容器内依赖安装（uv pip install -r requirements.txt）的超时（秒）
    TASK_DEP_INSTALL_TIMEOUT: int = 600
    # 任务实时日志 Redis Stream 的近似最大长度（XADD MAXLEN）。超出后最早的日志会被裁剪
    TASK_LOGS_MAXLEN: int = 50000

    # ---- Code-Server 空闲清理 ----
    CODE_SERVER_IDLE_TIMEOUT_SECONDS: int = 24 * 60 * 60  # 容器空闲超过该时长则停止
    CODE_SERVER_CLEANUP_INTERVAL_SECONDS: int = 10 * 60  # 后台清理任务执行间隔

    # ---- 首页资讯（GitHub Wiki 拉取） ----
    # 资讯数据源为 GitHub Wiki 的 raw markdown（唯一真源，定期人工发布）。
    NEWS_WIKI_URL_ZH: str = (
        "https://raw.githubusercontent.com/wiki/Optima-CityU/LLM4AD_Next/"
        "News%E2%80%90and%E2%80%90Articles%E2%80%90Index_zh.md"
    )
    NEWS_WIKI_URL_EN: str = (
        "https://raw.githubusercontent.com/wiki/Optima-CityU/LLM4AD_Next/"
        "News%E2%80%90and%E2%80%90Articles%E2%80%90Index_en.md"
    )
    NEWS_REFRESH_INTERVAL_SECONDS: int = 30 * 60  # 后台刷新间隔，默认 30 分钟
    NEWS_HTTP_TIMEOUT: float = 15.0  # 拉取 wiki 的 HTTP 超时（秒）


settings = Settings()  # type: ignore
