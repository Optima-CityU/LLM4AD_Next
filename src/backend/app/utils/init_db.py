"""
数据库初始化模块。

提供超级管理员用户的创建和初始化功能，
在系统首次启动时确保管理员账号和角色配置就绪。
"""

from datetime import UTC, datetime

from loguru import logger
from sqlmodel import Session, create_engine, select

from app import models
from app.core.config import settings
from app.crud import user as crud
from app.models import Role, User, UserCreate, UserUpdate

engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI))


def create_superuser(email: str, password: str, change_password=True):
    """创建超级用户
    用于已存在直接改为超级用户并激活，用户不存在则创建后再绑定

    Args:
        email: 邮箱
        password: 明文密码
        change_password: 如果用于已存在是否强制改变密码，初始化时不要改
    """
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if user:
            logger.info(f"用户已存在，开始激活为超管权限 {email}")
            if change_password:
                user_in = UserUpdate(email=email, password=password)
            else:
                user_in = UserUpdate(email=email)
            db_user = crud.update_superuser(session=session, db_user=user, user_in=user_in)
        else:
            logger.info(f"开始创建新超管用户 {email}")
            user_in = UserCreate(email=email, password=password, is_superuser=True)
            db_user = crud.create_user(session=session, user_create=user_in)

        # 绑定到超级管理角色
        has_roles_set = {rl.name for rl in db_user.roles}
        if "superuser" not in has_roles_set:
            super_role = session.exec(select(Role).where(Role.name == "superuser")).first()
            if not super_role:
                # 超管角色不存在则新建一个
                super_role = Role(name="superuser", description="超级管理员角色")
                session.add(super_role)
                session.commit()
                session.refresh(super_role)
            db_user.roles.append(super_role)
            session.add(db_user)
            session.commit()

        # 确保邮件验证已通过
        if not db_user.email_verified:
            db_user.email_verified = True
            session.add(db_user)
            session.commit()

        # 绑定到测试权限
    logger.success(f"创建成功 {db_user}")


def init_superuser():
    """初始化配置中的管理员用户"""
    create_superuser(email=settings.FIRST_SUPERUSER, password=settings.FIRST_SUPERUSER_PASSWORD, change_password=False)


def seed_builtin_provider() -> models.LLMProvider | None:
    """Upsert 内置 LLM 供应商。

    幂等：仅允许存在唯一一个内置供应商。按 is_builtin=True 定位现有记录，
    若存在多条（旧 bug 遗留）则保留第一条、删除其余；已存在则更新其全部
    字段（含 name）；不存在则新建。因此即便 BUILTIN_PROVIDER_NAME 改名，
    也只是改名已有记录而不会新建第二个。

    Returns:
        新建或更新后的 LLMProvider 记录；若配置未填则返回 None。
    """
    if not settings.BUILTIN_PROVIDER_BASE_URL:
        logger.info("BUILTIN_PROVIDER_BASE_URL 未配置，跳过内置供应商 seed")
        return None

    with Session(engine) as session:
        stmt = select(models.LLMProvider).where(
            models.LLMProvider.is_builtin.is_(True),  # type: ignore[union-attr]
        )
        existing = session.exec(stmt).all()
        # 仅允许存在唯一一个内置供应商；清理旧 bug 遗留的多余记录，
        # 否则改名时会撞上 uq_provider_builtin_name 唯一约束。
        provider = existing[0] if existing else None
        for dup in existing[1:]:
            logger.warning(f"删除多余内置供应商 {dup.name} (id={dup.id})")
            session.delete(dup)
        if len(existing) > 1:
            session.flush()
        fields = {
            "name": settings.BUILTIN_PROVIDER_NAME,
            "type": "openai_compatible",
            "base_url": settings.BUILTIN_PROVIDER_BASE_URL,
            "api_key": settings.BUILTIN_PROVIDER_API_KEY,
            "auth_token": "",
            "model": settings.BUILTIN_PROVIDER_MODELS,
            "max_tokens": settings.BUILTIN_PROVIDER_MAX_TOKENS,
        }
        if provider:
            # 仅在字段实际变化时才写入：EncryptedString 每次加密用新 IV，
            # 无条件 setattr 会使密文每次重启都不同，导致无谓的行重写与
            # updated_time 刷新。读取侧已解密为明文，可直接逐字段比较。
            changed = False
            for k, v in fields.items():
                if getattr(provider, k) != v:
                    setattr(provider, k, v)
                    changed = True
            if changed:
                provider.updated_time = datetime.now(UTC)
                logger.info(f"更新内置供应商 {settings.BUILTIN_PROVIDER_NAME} (id={provider.id})")
            else:
                logger.info(f"内置供应商 {settings.BUILTIN_PROVIDER_NAME} 无变化，跳过更新")
        else:
            provider = models.LLMProvider(
                is_builtin=True,
                visible_to_all=True,
                user_id=None,
                **fields,
            )
            session.add(provider)
            logger.info(f"新建内置供应商 {settings.BUILTIN_PROVIDER_NAME}")
        session.commit()
        session.refresh(provider)
        return provider


def init_all():
    """初始化超级管理员并 seed 内置供应商。"""
    init_superuser()
    seed_builtin_provider()


def backfill_user_default_models() -> tuple[int, int]:
    """为历史用户补齐默认模型配置，并刷新指向内置供应商的槽位。

    两段处理：
    1. 用户没有 UserDefaultModel 行 → 新建一条（四槽全部指向内置）。
    2. 用户已有 UserDefaultModel 行，逐槽位处理：
       - 槽位供应商为空 → 补齐为内置默认（含模型名）。
       - 槽位指向内置供应商 → 将模型名刷新为最新的内置默认模型（即使非空也覆盖）。
       - 槽位指向非内置供应商 → 保持不变，绝不覆盖用户已选。

    若当前数据库没有可用的内置供应商，则跳过对空槽位的补齐与刷新（新建依然可执行，
    但槽位会留空，与 ``build_default_init_kwargs`` 的行为一致）。

    可幂等重复执行。

    Returns:
        (新建 UserDefaultModel 行数, 填补/刷新的槽位数)。
    """
    from app.services.user_default_model_service import build_default_init_kwargs

    if not settings.BUILTIN_PROVIDER_BASE_URL:
        logger.info("BUILTIN_PROVIDER_BASE_URL 未配置，跳过 backfill UserDefaultModel")
        return 0, 0

    slot_prefixes = ("planner", "coder", "report", "other")

    with Session(engine) as session:
        # 1) 没有任何 UserDefaultModel 行的用户
        missing_stmt = (
            select(User)
            .outerjoin(
                models.UserDefaultModel,
                models.UserDefaultModel.user_id == User.id,
            )
            .where(models.UserDefaultModel.id.is_(None))  # type: ignore[union-attr]
        )
        users_missing = session.exec(missing_stmt).all()
        created = 0
        for user in users_missing:
            init_kwargs = build_default_init_kwargs(session, user.id)
            session.add(models.UserDefaultModel(**init_kwargs))
            created += 1

        # 2) 已有行逐槽位处理：补空位 + 刷新内置槽位的模型名
        #    模板的 provider_id / model_name 与 user_id 无关，循环外查一次即可。
        slots_filled = 0
        existing = session.exec(select(models.UserDefaultModel)).all()
        template = (
            build_default_init_kwargs(session, existing[0].user_id)
            if existing
            else {}
        )
        # 内置不存在时模板里没有 *_provider_id 字段，无可补齐/刷新
        if existing and "planner_provider_id" in template:
            builtin_id = template["planner_provider_id"]
            for config in existing:
                for prefix in slot_prefixes:
                    pid = getattr(config, f"{prefix}_provider_id")
                    # 空槽位 → 补齐；指向内置 → 刷新模型名；非内置 → 保持不变
                    if pid is not None and pid != builtin_id:
                        continue
                    new_pid = template[f"{prefix}_provider_id"]
                    new_model = template[f"{prefix}_model_name"]
                    if (
                        pid == new_pid
                        and getattr(config, f"{prefix}_model_name") == new_model
                    ):
                        continue
                    setattr(config, f"{prefix}_provider_id", new_pid)
                    setattr(config, f"{prefix}_model_name", new_model)
                    slots_filled += 1
                session.add(config)

        if created or slots_filled:
            session.commit()
        logger.success(
            f"backfill UserDefaultModel：新建 {created} 行，补齐/刷新 {slots_filled} 个槽位",
        )
        return created, slots_filled
