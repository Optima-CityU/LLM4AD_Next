# MindMemOS 动态网关鉴权设计

## 问题

MindMemOS provider binding 会持久化路由配置并在实际模型请求时直接使用其中的 `api_base`。若将用户短期 access token 展开并写入该 URL，token 过期后异步或后台记忆任务会失败；请求前同步 PATCH 无法覆盖所有运行时调用。

## 目标

内置供应商的记忆请求始终经过统一 gateway；provider binding 不持久化用户 access token、服务凭据或其展开结果。gateway 必须在每个模型请求时检查服务身份、用户状态，并复用现有按用户的 LiteLLM key、团队和额度控制。

## 架构

1. LLM4AD 后端为内置聊天/嵌入供应商写入声明式 binding：`api_base` 使用 `http://gateway:9090/litellm_memory_proxy/{teamId}/{userId}/v1`，其中用户 ID 在 MindMemOS 执行请求时从 `MemoryRequestContext` 注入。
2. MindMemOS 在解析 binding 后，运行时替换 `{userId}`，并从容器环境变量读取 `MINDMEMOS_GATEWAY_SERVICE_TOKEN` 作为模型客户端 API key；持久化 binding 中不包含该 token。
3. gateway 新增 memory proxy 过滤器。它验证服务 token，读取用户 ID 和团队 ID，调用 backend 的内部用户查询接口确认用户存在且启用，再复用 `LiteLLMAuthGatewayFilterFactory` 的 key/额度逻辑注入 LiteLLM bearer key。
4. backend 的内部用户查询接口要求 gateway 服务密钥。该密钥只在 backend/gateway 容器环境变量中存在，接口不对浏览器暴露。

## 安全与错误处理

- gateway 对错误的服务 token、未知/禁用用户返回 401；额度耗尽维持现有 402 JSON 响应。
- MindMemOS binding 只存稳定路由与模型身份；Qdrant 中不存 JWT 或 gateway 服务 token。
- 使用常量时间比较验证共享密钥；Compose 通过独立环境变量注入两个方向的服务身份。
- gateway 的用户查询每次模型请求执行，用户禁用即时生效；LiteLLM key 仍由 gateway 的 Redis/LiteLLM 逻辑动态取得。

## 验证

- 后端单测：内置 binding 使用 memory proxy URL，不调用用户 access-token 生成/刷新逻辑。
- MindMemOS 单测：请求上下文的用户 ID 与环境服务 token 仅在运行时注入。
- gateway 单测：有效服务 token 转发并按目标用户注入 LiteLLM key；无效 token 和禁用用户被拒绝。
- Docker Compose：以已过期的浏览器 JWT 创建旧 binding 后，使用稳定 memory route 的请求仍可经 MindMemOS、gateway 和 LiteLLM 完成；禁用用户请求返回 401。
