"""示例模板的发现与应用。

负责扫描 ``examples/applications`` 目录、列出可用模板，以及在创建任务时把
选定模板的文件上传到对象存储并据 ``config.yaml`` 填充 ``input_args``。
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from loguru import logger
from sqlmodel import Session

from app import models
from app.core.config import settings
from app.core.storage import storage

from ._helpers import _check_storage_quota

EXAMPLES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "examples" / "applications"
    if settings.ENVIRONMENT == "local"
    else Path(__file__).resolve().parent.parent.parent.parent / "examples" / "applications"
)


def list_example_templates() -> list[dict]:
    """列出可用的示例模板。

    扫描 examples 目录下的一级模板目录，递归收集路径以 config.yaml 结尾的文件。
    忽略没有配置文件的文件夹。每个配置文件分别读取自身的 description_en/description_zh
    字段，作为前端提示文字。

    Returns:
        按字母排序的模板列表，每项包含 name（文件夹名）和 configs（配置文件列表，
        每项含 name、description_en、description_zh）。
    """
    import yaml

    templates: list[dict] = []
    if not EXAMPLES_DIR.is_dir():
        return templates
    for entry in sorted(EXAMPLES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        config_files = sorted(f for f in entry.rglob("*config.yaml") if f.is_file())
        if not config_files:
            continue
        configs: list[dict] = []
        for config_path in config_files:
            description_en: str | None = None
            description_zh: str | None = None
            try:
                with open(config_path, encoding="utf-8") as f:
                    config_data = yaml.safe_load(f)
                if isinstance(config_data, dict):
                    desc_en = config_data.get("description_en")
                    desc_zh = config_data.get("description_zh")
                    if isinstance(desc_en, str):
                        description_en = desc_en
                    if isinstance(desc_zh, str):
                        description_zh = desc_zh
            except (OSError, yaml.YAMLError) as exc:
                logger.warning(f"读取模板配置 '{entry.name}/{config_path.name}' 失败: {exc}")
            configs.append(
                {
                    "name": config_path.relative_to(entry).as_posix(),
                    "description_en": description_en,
                    "description_zh": description_zh,
                }
            )
        templates.append({"name": entry.name, "configs": configs})
    return templates


def _apply_template(db: Session, task: models.Task, template_name: str, config_name: str = "config.yaml") -> None:
    """上传模板文件到 S3 并根据配置文件更新任务的 input_args。

    Args:
        db: 数据库会话
        task: Task 模型实例（已持久化，拥有 id）
        template_name: 示例模板目录名称
        config_name: 配置文件名，默认为 config.yaml

    Raises:
        HTTPException: 模板不存在或缺少指定配置文件
    """
    import yaml

    template_dir = EXAMPLES_DIR / template_name
    if not template_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"模板 '{template_name}' 不存在")
    config_path = (template_dir / config_name).resolve()
    if not config_path.is_relative_to(template_dir.resolve()) or not config_path.is_file():
        raise HTTPException(status_code=400, detail=f"模板 '{template_name}' 缺少 {config_name}")

    ts = int(datetime.now(UTC).timestamp())
    prefix = f"tasks/{task.id}/{ts}"
    storage.ensure_bucket()

    def is_template_file(path: Path) -> bool:
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            return False
        relative_parts = path.relative_to(template_dir).parts
        if "results" in relative_parts:
            return False
        return path.name.lower() not in {"readme.md", "readme_zh.md", "notice", "notice.md"}

    if config_path.parent == template_dir.resolve():
        template_files = [path for path in template_dir.rglob("*") if is_template_file(path)]
    else:
        case_dir = config_path.parent
        shared_dir = template_dir / "_shared"
        template_files = [path for path in case_dir.rglob("*") if is_template_file(path)]
        if shared_dir.is_dir() and shared_dir != case_dir:
            template_files.extend(path for path in shared_dir.rglob("*") if is_template_file(path))
        template_files.extend(path for path in template_dir.iterdir() if is_template_file(path))
        template_files = sorted(set(template_files))

    template_total_size = sum(path.stat().st_size for path in template_files)
    _check_storage_quota(prefix, template_total_size)

    for file_path in template_files:
        if file_path.name.endswith("config.yaml"):
            continue
        relative = file_path.relative_to(template_dir).as_posix()
        key = f"{prefix}/{template_name}/{relative}"
        content_type = "application/octet-stream"
        if file_path.suffix in (".yaml", ".yml"):
            content_type = "text/yaml"
        elif file_path.suffix == ".json":
            content_type = "application/json"
        elif file_path.suffix == ".py":
            content_type = "text/x-python"
        elif file_path.suffix in (".txt", ".md", ".csv", ".log"):
            content_type = "text/plain"
        storage.upload(key, file_path.read_bytes(), content_type=content_type)

    task.input_data_path = prefix

    with open(config_path, encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    if isinstance(config_data, dict):
        # 前端提示文字字段不参与任务运行，保存到 input_args 前移除
        config_data.pop("description_en", None)
        config_data.pop("description_zh", None)
        if "planner" not in config_data:
            config_data["planner"] = {}
        if "coder" not in config_data:
            config_data["coder"] = {}
        if "evaluator" not in config_data:
            config_data["evaluator"] = {}
        # 除了mock，其它的都使用默认模型
        if config_data["planner"].get("provider") != "mock":
            config_data["planner"]["provider"] = "default"
            config_data["planner"]["provider_model"] = ""  # 用于前端表单绑定
        if config_data["coder"].get("provider") != "mock":
            config_data["coder"]["provider"] = "default"
            config_data["coder"]["provider_model"] = ""
        if config_data["evaluator"].get("provider") != "mock":
            config_data["evaluator"]["provider"] = "default"
            config_data["evaluator"]["provider_model"] = ""
        config_data["providers"] = []
        task.input_args = config_data

    task.updated_time = datetime.now(UTC)
    db.add(task)
