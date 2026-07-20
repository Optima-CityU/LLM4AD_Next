package com.dadastory.omni_ai_router.filter;

import com.dadastory.omni_ai_router.dto.Result;
import com.dadastory.omni_ai_router.entity.AuthUser;
import com.dadastory.omni_ai_router.manager.APIKeyManager;
import com.dadastory.omni_ai_router.manager.AuthUserManager;
import com.dadastory.omni_ai_router.manager.LiteLLMManager;
import jakarta.annotation.Resource;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NullMarked;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.OrderedGatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import tools.jackson.databind.JsonNode;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;

/**
 * Injects a LiteLLM API key into model proxy requests.
 *
 * <p>The key is loaded from Redis when available. On cache miss, the filter asks
 * LiteLLM to generate or recover a user key and stores the encrypted value in
 * Redis for later requests.</p>
 */
@Slf4j
@Component
public class LiteLLMAuthGatewayFilterFactory extends AbstractGatewayFilterFactory<LiteLLMAuthGatewayFilterFactory.Config> {

    private static final Duration API_KEY_LOCK_WAIT_INTERVAL = Duration.ofMillis(100);
    private static final int API_KEY_LOCK_WAIT_ATTEMPTS = 50;

    @Resource
    private APIKeyManager apiKeyManager;
    @Resource
    private LiteLLMManager liteLLMManager;
    @Resource
    private AuthUserManager authUserManager;

    /**
     * Route-level path variable names.
     */
    @Data
    @NoArgsConstructor
    public static class Config {
        private String teamIdVariable = "teamId";
        private String accessTokenVariable = "accessToken";
    }

    /**
     * Builds the filter that resolves the LiteLLM key and injects it as a bearer token.
     *
     * @param config route-level LiteLLM auth configuration
     * @return ordered gateway filter that runs before the request is proxied
     */
    @NullMarked
    @Override
    public GatewayFilter apply(Config config) {
        return new OrderedGatewayFilter((exchange, chain) -> {

            Map<String, String> uriVariables = exchange
                    .getAttribute(ServerWebExchangeUtils.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
            if (uriVariables == null) {
                log.warn("Request is missing path variables, skipping API Key injection.");
                return chain.filter(exchange);
            }

            String teamId = uriVariables.get(config.getTeamIdVariable());
            String accessToken = uriVariables.get(config.getAccessTokenVariable());

            if (!StringUtils.hasText(accessToken) || !StringUtils.hasText(teamId)) {
                log.warn("Request is missing access token or Team ID, skipping API Key injection.");
                return chain.filter(exchange);
            }

            return authUserManager.getCurrentUser(accessToken)
                    .flatMap(user -> resolveLiteLlmApiKey(user, teamId)
                            .flatMap(apiKey -> {
                                ServerHttpRequest mutatedRequest = exchange.getRequest().mutate()
                                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + apiKey)
                                        .build();

                                ServerWebExchange mutatedExchange = exchange.mutate().request(mutatedRequest).build();
                                return chain.filter(mutatedExchange);
                            }))
                    .onErrorResume(e -> {
                        if (e instanceof AuthUserManager.UnauthorizedUserException) {
                            log.warn("Model request rejected by backend user validation. team={}, reason={}",
                                    teamId, e.getMessage());
                            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
                            return exchange.getResponse().setComplete();
                        }
                        if (e instanceof InsufficientQuotaException) {
                            log.warn("Model request rejected because user quota is exhausted or unavailable. team={}, reason={}",
                                    teamId, e.getMessage());
                            return writeInsufficientQuotaResponse(exchange);
                        }
                        log.error("Failed to process API Key for team: {}", teamId, e);
                        exchange.getResponse().setStatusCode(HttpStatus.INTERNAL_SERVER_ERROR);
                        return exchange.getResponse().setComplete();
                    });

        }, -10);
    }

    /** Resolve the cached or provisioned LiteLLM key for one active user and team. */
    public Mono<String> resolveLiteLlmApiKey(AuthUser user, String teamId) {
        String userId = user.getId();
        return getCachedValidApiKey(userId, teamId)
                .switchIfEmpty(Mono.defer(() -> fetchAndSaveApiKeyWithLock(user, teamId)));
    }

    Mono<Void> writeInsufficientQuotaResponse(ServerWebExchange exchange) {
        String body = """
                {"error":"builtin_provider_quota_exhausted","message":"内置模型免费额度已用尽，请联系管理员增加额度或切换其他供应商。"}
                """;
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponse().setStatusCode(HttpStatus.PAYMENT_REQUIRED);
        exchange.getResponse().getHeaders().setContentType(MediaType.APPLICATION_JSON);
        return exchange.getResponse().writeWith(Mono.just(exchange.getResponse().bufferFactory().wrap(bytes)));
    }

    private Mono<String> getCachedValidApiKey(String userId, String teamId) {
        return apiKeyManager.getApiKey(teamId, userId)
                .flatMap(apiKey -> liteLLMManager.isApiKeyValid(apiKey, userId, teamId)
                        .flatMap(valid -> {
                            if (valid) {
                                log.info("LiteLLM API Key cache hit and validated. user={}, team={}", userId, teamId);
                                return Mono.just(apiKey);
                            }
                            log.info("Cached LiteLLM API Key is invalid. Regenerating. user={}, team={}", userId, teamId);
                            return apiKeyManager.deleteApiKey(teamId, userId)
                                    .then(Mono.empty());
                        }));
    }

    private Mono<String> fetchAndSaveApiKeyWithLock(AuthUser user, String teamId) {
        String userId = user.getId();
        String lockToken = UUID.randomUUID().toString();
        return apiKeyManager.acquireApiKeyGenerationLock(teamId, userId, lockToken)
                .flatMap(acquired -> {
                    if (acquired) {
                        return fetchAndSaveApiKeyAfterLockAcquired(user, teamId, lockToken);
                    }
                    log.info("LiteLLM API Key generation is locked by another request. Waiting for cache. user={}, team={}",
                            userId, teamId);
                    return waitForCachedApiKey(userId, teamId)
                            .switchIfEmpty(Mono.defer(() -> {
                                log.warn("Timed out waiting for LiteLLM API Key cache after lock contention. Retrying lock. user={}, team={}",
                                        userId, teamId);
                                return fetchAndSaveApiKeyWithLock(user, teamId);
                            }));
                });
    }

    private Mono<String> fetchAndSaveApiKeyAfterLockAcquired(AuthUser user, String teamId, String lockToken) {
        String userId = user.getId();
        Mono<String> fetch = getCachedValidApiKey(userId, teamId)
                .switchIfEmpty(Mono.defer(() -> fetchAndSaveApiKey(user, teamId)));

        return fetch
                .flatMap(apiKey -> releaseApiKeyGenerationLock(teamId, userId, lockToken).thenReturn(apiKey))
                .onErrorResume(error -> releaseApiKeyGenerationLock(teamId, userId, lockToken)
                        .then(Mono.error(error)));
    }

    private Mono<String> waitForCachedApiKey(String userId, String teamId) {
        return Flux.interval(API_KEY_LOCK_WAIT_INTERVAL)
                .take(API_KEY_LOCK_WAIT_ATTEMPTS)
                .concatMap(ignored -> getCachedValidApiKey(userId, teamId))
                .next();
    }

    private Mono<Boolean> releaseApiKeyGenerationLock(String teamId, String userId, String lockToken) {
        return apiKeyManager.releaseApiKeyGenerationLock(teamId, userId, lockToken)
                .onErrorResume(error -> {
                    log.warn("Failed to release LiteLLM API Key generation lock. user={}, team={}",
                            userId, teamId, error);
                    return Mono.just(false);
                });
    }

    /**
     * 判断用户是否存在 -> 选择生成Key / 创建用户 -> 从 JsonNode 提取 -> 存入 Redis -> 返回 key
     * 前置检查：先确认 Team 是否存在，不存在则直接返回错误
     *
     * @param user authenticated backend user
     * @param teamId LiteLLM team id from the route
     * @return generated or recovered LiteLLM API key
     */
    private Mono<String> fetchAndSaveApiKey(AuthUser user, String teamId) {
        String userId = user.getId();
        // 0. 先校验 Team 是否存在
        return liteLLMManager.getTeamInfo(teamId)
                .flatMap(teamInfoResult -> {
                    if (!isSuccessResult(teamInfoResult)) {
                        log.warn("LiteLLM team check failed. user={}, team={}, result={}", userId, teamId, teamInfoResult);
                        return Mono.error(new RuntimeException(
                                "Team " + teamId + " does not exist. Cannot fetch or generate API key."));
                    }
                    // Team 存在，继续原有流程
                    log.info("API Key not found in Redis. Checking if User exists before fetching key. User: {}, Team: {}",
                            userId, teamId);

                    // 1. 查询用户信息是否存在
                    return liteLLMManager.getUserInfo(userId)
                            .flatMap(userInfoResult -> {
                                // 2. 根据用户信息查询结果，决定是重新生成 Key 还是创建用户
                                if (isSuccessResult(userInfoResult)) {
                                    log.info("User {} already exists. Proceeding to regenerate API Key with remaining quota.",
                                            userId);
                                    return liteLLMManager.regenerateApiKeyWithinUserBudget(
                                            userInfoResult,
                                            userId,
                                            teamId);
                                } else {
                                    log.info("User {} does not exist. Proceeding to create user and assign team.", userId);
                                    return liteLLMManager.createUserAndAssignTeam(user, teamId)
                                            .flatMap(createResult -> {
                                                if (!isSuccessResult(createResult)) {
                                                    return Mono.just(createResult);
                                                }
                                                return liteLLMManager.getUserInfo(userId)
                                                        .flatMap(createdUserInfo ->
                                                                liteLLMManager.regenerateApiKeyWithinUserBudget(
                                                                        createdUserInfo,
                                                                        userId,
                                                                        teamId));
                                            });
                                }
                            })
                            .flatMap(result -> {
                                // 3. 统一处理"生成Key"或"创建用户"的返回结果
                                if (isSuccessResult(result)) {
                                    Object rawData = result.getData();
                                    if (rawData instanceof JsonNode jsonNode) {
                                        String apiKey = extractApiKey(jsonNode);
                                        if (StringUtils.hasText(apiKey)) {
                                            log.info("Successfully fetched/generated API Key from LiteLLM for User: {}",
                                                    userId);
                                            return apiKeyManager.saveApiKey(teamId, userId, apiKey)
                                                    .thenReturn(apiKey);
                                        }
                                    }
                                }
                                if (result != null && result.getCode() == 402) {
                                    return Mono.error(new InsufficientQuotaException(result.getMessage()));
                                }
                                return Mono.error(new RuntimeException(
                                        "LiteLLM response error or missing valid Key. Detail: " + result));
                            });
                });
    }

    /**
     * 解析 JSON，提取 key 字段
     *
     * @param jsonNode LiteLLM response payload
     * @return extracted API key, or {@code null} if the payload does not contain one
     */
    private String extractApiKey(JsonNode jsonNode) {
        if (jsonNode.has("key") && !jsonNode.get("key").isNull()) {
            return jsonNode.get("key").asString();
        }
        return null; // 会在调用方触发 Mono.error
    }

    /**
     * 判断 Result 对象是否表示成功
     *
     * @param result result wrapper returned by LiteLLM manager
     * @return {@code true} when the wrapped code is 200
     */
    private boolean isSuccessResult(Result<?> result) {
        return result != null && result.getCode() == 200;
    }

    static class InsufficientQuotaException extends RuntimeException {
        InsufficientQuotaException(String message) {
            super(message);
        }
    }
}
