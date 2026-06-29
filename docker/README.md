# Docker 本地启动与部署

本目录提供两类 Docker 工作流：

- **贡献者本地开发**：macOS/Linux 使用 `./dev.sh infra`，Windows PowerShell 使用 `.\dev.ps1 infra`，启动 PostgreSQL、Redis、RustFS、mailcatcher、code-server proxy 等基础设施，后端和前端在宿主机运行。
- **本地完整栈 / 部署调试**：macOS/Linux 使用 `./dev.sh full`，Windows PowerShell 使用 `.\dev.ps1 full`，从本地源码构建完整栈；也可以使用 `./start.sh --debug` 运行已发布镜像并暴露调试端口。

完整双语说明见：

- English: [Docker Local Startup](../docs/en/contributing/docker-local.md)
- 中文： [Docker 本地启动](../docs/zh/contributing/docker-local.md)

## 使用

### 安装 Docker

本地启动依赖 Docker Engine 和 Docker Compose v2。推荐安装方式：

- Windows：安装 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)，使用 WSL 2 Linux backend，启动 Docker Desktop 后在 PowerShell 运行 `docker compose version` 验证。
- macOS：安装 [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)，启动 Docker Desktop 后运行 `docker compose version` 验证。
- Linux：按发行版安装 [Docker Engine](https://docs.docker.com/engine/install/) 和 [Docker Compose plugin](https://docs.docker.com/compose/install/)。

Windows 用户请使用 PowerShell 运行 `.\dev.ps1 ...`；macOS/Linux 用户使用 `./dev.sh ...`。

### 配置

首次部署请复制示例文件并按需修改：

```bash
cp .env.develop.local.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.develop.local.example .env
```

`.env` 中以下变量**必须**显式配置（未设置时 compose 会直接报错或服务无法正常工作）：

**安全密钥**

- `SECRET_KEY`：应用密钥，可通过 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成
- `PROVIDER_ENCRYPTION_KEY`：供应商凭据加密专用密钥，务必显式设置且设置后不要再变更（留空会回退到 `SECRET_KEY` 派生，`SECRET_KEY` 轮换后会导致已加密的供应商凭据无法解密）。生成方式同上

**管理员账户**

- `FIRST_SUPERUSER`：首个超级管理员邮箱
- `FIRST_SUPERUSER_PASSWORD`：超管密码，至少 8 位

**基础服务凭据**

- `POSTGRES_PASSWORD`：PostgreSQL 数据库密码
- `REDIS_PASSWORD`：Redis 密码
- `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`：RustFS 对象存储凭据

**项目目录**

- `HOST_PROJECT_HOME`：宿主机上的项目工作目录，必须使用绝对路径，例如 `/srv/llm4ad/app-data` 或 `D:\data\project_home`，不能使用 `./app-data`。
- `DOCKER_PROJECT_HOME`：容器内项目工作目录，默认 `/data/project_home/`；Compose 会把 `HOST_PROJECT_HOME` 挂载到该路径。

其余变量（端口、镜像名、SMTP、APT/PyPI 镜像源等）可沿用示例文件中的默认值，完整说明见 `.env.develop.local.example`。

## 本地开发

首次本地开发推荐只启动基础设施：

```bash
./dev.sh infra
```

Windows PowerShell：

```powershell
.\dev.ps1 infra
```

该命令会使用 `compose.yml` + `compose.override.yml`，并启用 `debug` profile。应用容器会被禁用，你可以在宿主机运行：

```bash
# 后端
cd ../src/backend
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py

# 前端
cd ../src/frontend
bun install
bun run dev
```

需要从本地源码构建并运行完整栈时：

```bash
./dev.sh full
```

Windows PowerShell：

```powershell
.\dev.ps1 full
```

常用命令：

```bash
./dev.sh logs backend worker
./dev.sh ps
./dev.sh stop
./dev.sh remove
```

Windows PowerShell：

```powershell
.\dev.ps1 logs backend worker
.\dev.ps1 ps
.\dev.ps1 stop
.\dev.ps1 remove
```

## 镜像部署

镜像部署默认读取 `docker/version`：

```bash
VERSION=latest
CN_REGISTRY=registry.cn-hangzhou.aliyuncs.com/noah2012
DOCKER_REGISTRY=docker.io/noah2012
```

未显式传入 `TAG` 时使用 `VERSION`；未显式传入 `--mirrors` 或 `SWR_REGISTRY` 时使用 `DOCKER_REGISTRY`。`CN_REGISTRY` 作为国内镜像源地址，可通过 `--mirrors` 显式使用。

- 启动指定版本（示例：`v1.0.0`）
```shell
TAG=v1.0.0 ./start.sh start
```

- 启动默认版本（`latest`）
```shell
./start.sh start
```

`start` 是默认命令，也可以直接执行 `./start.sh`。启动脚本会先拉取当前版本所需镜像，以及后端运行时动态容器所需镜像，再启动服务。

- 使用指定镜像仓库或加速地址
```shell
./start.sh start --mirrors registry.cn-hangzhou.aliyuncs.com/noah2012
./start.sh start --mirrors docker.io/noah2012
./start.sh start --mirrors docker.1ms.run/noah2012
```

- 停止服务和后端动态容器
```shell
./start.sh stop
```

- 移除服务和后端动态容器
```shell
./start.sh remove
```

- 升级到指定版本
```shell
TAG=v1.0.1 ./start.sh upgrade
```

- 以 debug 模式启动部署镜像并暴露基础服务端口
```shell
TAG=v1.0.0 ./start.sh start --debug
```

`--debug` 会引入 `compose.deploy.debug.yml` 并启用 `debug` profile，额外暴露 PostgreSQL、Redis、RustFS、backend、Adminer 和 Flower 端口。
