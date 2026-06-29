"""
API 路由聚合器。

将所有子模块的路由注册到统一的 api_router 上，
由 FastAPI 应用主入口引用。
"""

from fastapi import APIRouter

from app.api.base_routes import (
    admin_analytics,
    code_server,
    feedback,
    live_codes,
    live_qr,
    login,
    permission,
    privacy_policy,
    testing,
    users,
    utils,
)
from app.api.llm4ad import chat_tune as llm4ad_chat_tune
from app.api.llm4ad import embedding_providers as llm4ad_embedding_providers
from app.api.llm4ad import llm_proxy as llm4ad_llm_proxy
from app.api.llm4ad import projects as llm4ad_projects
from app.api.llm4ad import providers as llm4ad_providers
from app.api.llm4ad import reports as llm4ad_reports
from app.api.llm4ad import tasks as llm4ad_tasks
from app.api.llm4ad import user_default_models as llm4ad_user_default_models

api_router = APIRouter()

# ---- 基础路由 ----
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(admin_analytics.router)
api_router.include_router(code_server.router)
api_router.include_router(testing.router)
api_router.include_router(permission.router)
api_router.include_router(privacy_policy.router)
api_router.include_router(feedback.router)
api_router.include_router(live_codes.router)
api_router.include_router(live_qr.router)

# ---- LLM4AD 业务路由 ----
api_router.include_router(llm4ad_projects.router, prefix="/llm4ad")
api_router.include_router(llm4ad_tasks.router, prefix="/llm4ad")
api_router.include_router(llm4ad_providers.router, prefix="/llm4ad")
api_router.include_router(llm4ad_embedding_providers.router, prefix="/llm4ad")
api_router.include_router(llm4ad_reports.router, prefix="/llm4ad")
api_router.include_router(llm4ad_user_default_models.router, prefix="/llm4ad")
api_router.include_router(llm4ad_chat_tune.router, prefix="/llm4ad")
api_router.include_router(llm4ad_llm_proxy.router, prefix="/llm4ad")
