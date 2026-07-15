"""
环境自动检测工具

自动判断当前是否在 Docker 容器内运行，无需手动配置
"""
import os
from pathlib import Path


def is_running_in_docker() -> bool:
    """检测当前进程是否在 Docker 容器内运行

    检查方法：
    1. 检查 /.dockerenv 文件（Docker 创建的标记文件）
    2. 检查 /proc/1/cgroup 是否包含 docker 关键字
    3. 检查环境变量 DOCKER_CONTAINER

    Returns:
        True if running inside Docker, False otherwise
    """
    # 方法 1: 检查 /.dockerenv 文件
    if Path("/.dockerenv").exists():
        return True

    # 方法 2: 检查 /proc/1/cgroup
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            if "docker" in content or "containerd" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass

    # 方法 3: 检查环境变量
    if os.getenv("DOCKER_CONTAINER") == "true":
        return True

    return False


def get_project_home(host_home: str, docker_home: str) -> str:
    """根据运行环境自动选择正确的项目根路径

    Args:
        host_home: 宿主机路径（如 C:/path/to/app-data/）
        docker_home: 容器内路径（如 /data/project_home/）

    Returns:
        当前环境应该使用的路径（统一为正斜杠，末尾无斜杠）
    """
    if is_running_in_docker():
        # 在容器内，使用容器路径
        path = docker_home
    else:
        # 在宿主机，使用宿主机路径
        path = host_home

    # 标准化路径：移除末尾斜杠，转换为正斜杠
    path = path.rstrip("/\\").replace("\\", "/")
    return path


def use_container_network() -> bool:
    """判断连接子容器（如调参容器）时是否应走 Docker 内部网络。

    - backend 运行在容器内（Docker 部署）：通过 Docker 网络 + 容器名 DNS 连接，
      子容器无需发布端口。
    - backend 运行在宿主机（本地开发）：子容器发布端口到宿主机，用 localhost 连接。

    与 :func:`is_running_in_docker` 保持一致，无需手动配置。

    Returns:
        True 表示走 Docker 网络（容器名 DNS），False 表示走 localhost。
    """
    return is_running_in_docker()
