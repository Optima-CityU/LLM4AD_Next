# MindMemOS Dynamic Gateway Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MindMemOS built-in-provider model traffic use a stable gateway route without storing expiring user access tokens in provider bindings.

**Architecture:** Provider bindings store only the stable memory gateway route and model metadata. MindMemOS resolves user identity and service credentials at request time; gateway validates that service identity, verifies the target user with backend, then reuses its existing per-user LiteLLM key and quota flow.

**Tech Stack:** Python/FastAPI/pytest, MindMemOS Python package, Spring Cloud Gateway/JUnit/Mockito, Docker Compose.

---

### Task 1: Backend internal user lookup

**Files:**
- Modify: `src/backend/app/core/config.py`
- Modify: `src/backend/app/api/main.py`
- Create: `src/backend/app/api/internal/gateway_users.py`
- Test: `src/backend/tests/api/internal/test_gateway_users.py`

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_gateway_user_lookup_accepts_valid_service_token(client, user):
    response = client.get(f"/api/v1/internal/gateway/users/{user.id}", headers={"X-Gateway-Service-Token": "test-token"})
    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)

def test_gateway_user_lookup_rejects_invalid_service_token(client, user):
    response = client.get(f"/api/v1/internal/gateway/users/{user.id}", headers={"X-Gateway-Service-Token": "wrong"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run the endpoint tests and observe 404**

Run: `pytest tests/api/internal/test_gateway_users.py -q`

- [ ] **Step 3: Add the protected route and config**

```python
GATEWAY_BACKEND_SERVICE_TOKEN: str = ""

@router.get("/gateway/users/{user_id}")
def get_gateway_user(user_id: UUID, session: SessionDep, x_gateway_service_token: Annotated[str | None, Header()] = None) -> GatewayUser:
    if not hmac.compare_digest(x_gateway_service_token or "", settings.GATEWAY_BACKEND_SERVICE_TOKEN):
        raise HTTPException(status_code=401)
    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401)
    return GatewayUser(id=str(user.id), email=user.email, is_active=True)
```

- [ ] **Step 4: Re-run endpoint tests**

Run: `pytest tests/api/internal/test_gateway_users.py -q`

### Task 2: Stable provider binding declarations

**Files:**
- Modify: `src/backend/app/services/memory_service.py`
- Modify: `src/backend/tests/services/test_memory_provider_binding_service.py`

- [ ] **Step 1: Write failing binding test**

```python
def test_memory_provider_routers_use_stable_gateway_memory_route(user, builtin_provider):
    routers = _memory_provider_routers(user, builtin_provider, "model", builtin_embedding)
    assert routers["chat_model_router"]["endpoints"][0]["api_base"] == "http://gateway:9090/litellm_memory_proxy/team-1/{userId}/v1"
    assert "accessToken" not in str(routers)
```

- [ ] **Step 2: Run it and observe the old tokenized route**

Run: `pytest tests/services/test_memory_provider_binding_service.py -q`

- [ ] **Step 3: Write memory route templates instead of JWT-expanded URLs**

```python
def _memory_gateway_base_url(base_url: str | None) -> str | None:
    return base_url.replace("/litellm_proxy/", "/litellm_memory_proxy/").replace("/{accessToken}/", "/{userId}/") if base_url else None
```

- [ ] **Step 4: Re-run binding tests**

Run: `pytest tests/services/test_memory_provider_binding_service.py -q`

### Task 3: MindMemOS runtime hydration

**Files:**
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/provider_bindings.py`
- Modify: `third_party/MindMemOS/tests/test_provider_bindings.py`

- [ ] **Step 1: Write a failing runtime hydration test**

```python
async def test_resolver_hydrates_memory_route_from_context_and_environment(monkeypatch):
    monkeypatch.setenv("MINDMEMOS_GATEWAY_SERVICE_TOKEN", "service-token")
    routers = await resolver.resolve(context_with_user("user-1"))
    endpoint = routers["chat_model_router"]["endpoints"][0]
    assert endpoint["api_base"].endswith("/user-1/v1")
    assert endpoint["api_key"] == "service-token"
```

- [ ] **Step 2: Run the MindMemOS test and observe the unresolved route**

Run: `pytest tests/test_provider_bindings.py -q`

- [ ] **Step 3: Hydrate only the memory route at resolver return time**

```python
def hydrate_memory_gateway_routers(routers: dict[str, Any], ctx: MemoryRequestContext) -> dict[str, Any]:
    hydrated = deepcopy(routers)
    for endpoint in all_model_endpoints(hydrated):
        if "/litellm_memory_proxy/" in str(endpoint.get("api_base", "")):
            endpoint["api_base"] = endpoint["api_base"].replace("{userId}", ctx.user_id)
            endpoint["api_key"] = required_env("MINDMEMOS_GATEWAY_SERVICE_TOKEN")
    return hydrated
```

- [ ] **Step 4: Re-run the MindMemOS test**

Run: `pytest tests/test_provider_bindings.py -q`

### Task 4: Gateway memory route and authentication

**Files:**
- Create: `src/gateway/src/main/java/com/dadastory/omni_ai_router/filter/MemoryServiceGatewayFilterFactory.java`
- Modify: `src/gateway/src/main/java/com/dadastory/omni_ai_router/manager/AuthUserManager.java`
- Modify: `src/gateway/src/main/java/com/dadastory/omni_ai_router/routes/ModelRequestRouteConfig.java`
- Modify: `src/gateway/src/main/resources/application.yaml`
- Test: `src/gateway/src/test/java/com/dadastory/omni_ai_router/filter/MemoryServiceGatewayFilterFactoryTest.java`

- [ ] **Step 1: Write failing gateway filter tests**

```java
@Test
void validMemoryServiceTokenUsesPathUserForLiteLlmKey() { /* assert injected key belongs to user-1 */ }

@Test
void invalidMemoryServiceTokenReturnsUnauthorized() { /* assert 401 and no proxy */ }
```

- [ ] **Step 2: Run Maven tests and observe missing filter class**

Run: `mvn -q -f src/gateway/src/pom.xml test -Dtest=MemoryServiceGatewayFilterFactoryTest`

- [ ] **Step 3: Add route, constant-time service-token check, backend user lookup, and LiteLLM key reuse**

```java
r.path("/litellm_memory_proxy/{teamId}/{userId}/**")
 .filters(f -> f.stripPrefix(3).filter(memoryServiceGatewayFilterFactory.apply(config)))
 .uri(liteLLMBaseUrl);
```

- [ ] **Step 4: Re-run gateway unit tests**

Run: `mvn -q -f src/gateway/src/pom.xml test -Dtest=MemoryServiceGatewayFilterFactoryTest`

### Task 5: Compose configuration and end-to-end verification

**Files:**
- Modify: `docker/compose-cloud.yml`
- Modify: `docker/compose.mindmemos.yml`
- Modify: `docker/.env.develop.local.example`
- Modify: `docker/mindmemos/config/dev.yaml`

- [ ] **Step 1: Add test configuration for the two service credentials**

```dotenv
GATEWAY_BACKEND_SERVICE_TOKEN=change-me
MINDMEMOS_GATEWAY_SERVICE_TOKEN=change-me
```

- [ ] **Step 2: Build and start the affected Compose services**

Run: `docker compose -f docker/compose.yml -f docker/compose-cloud.yml -f docker/compose.mindmemos.yml up --build -d backend gateway mindmemos-api litellm`

- [ ] **Step 3: Run focused backend, MindMemOS, and gateway suites in containers**

Run: `docker compose -f docker/compose.yml -f docker/compose-cloud.yml -f docker/compose.mindmemos.yml exec backend pytest tests/services/test_memory_provider_binding_service.py tests/api/internal/test_gateway_users.py -q`

- [ ] **Step 4: Verify the stable binding contains no JWT and the gateway rejects an invalid service key**

Run: `docker compose -f docker/compose.yml -f docker/compose-cloud.yml -f docker/compose.mindmemos.yml exec gateway sh -lc 'curl -i http://localhost:9090/litellm_memory_proxy/team-1/user-1/v1/models'`

- [ ] **Step 5: Commit the implementation**

```bash
git add src/backend src/gateway docker third_party/MindMemOS docs/superpowers
git commit -m "fix(memory): resolve builtin gateway auth at request time"
```
