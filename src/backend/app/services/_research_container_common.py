"""研究类容器运行适配层的共享工具。

供 :mod:`app.services.research_pipeline_runner` 与
:mod:`app.services.research_collab_runner` 复用的场景专属前置逻辑；容器生命周期
本身仍由领域无关的 :class:`app.services.container_runtime.ContainerJob` 负责。
"""

from __future__ import annotations

import os

from app.core.constants import APP_CONFIG_FILENAME
from app.tasks import task_config_crypto


def write_encrypted_config(payload: dict, run_dir: str) -> str:
    """整体加密容器配置写入 ``run_dir``，返回一次性解密密钥。

    整体加密（而非逐字段）避免遗漏藏在 base_url / api_key 等字段的凭据；密钥经
    环境变量传入容器，容器解密后立即删除（见各容器入口的 ``main``）。
    """
    key = task_config_crypto.generate_key()
    token = task_config_crypto.encrypt_config(payload, key)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, APP_CONFIG_FILENAME), "w", encoding="utf-8") as f:
        f.write(token)
    return key
