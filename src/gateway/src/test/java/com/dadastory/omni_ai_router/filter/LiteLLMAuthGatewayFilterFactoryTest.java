package com.dadastory.omni_ai_router.filter;

import com.dadastory.omni_ai_router.dto.Result;
import com.dadastory.omni_ai_router.entity.AuthUser;
import com.dadastory.omni_ai_router.manager.APIKeyManager;
import com.dadastory.omni_ai_router.manager.AuthUserManager;
import com.dadastory.omni_ai_router.manager.LiteLLMManager;
import org.junit.jupiter.api.Test;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class LiteLLMAuthGatewayFilterFactoryTest {

    private static final JsonMapper JSON = new JsonMapper();

    @Test
    void concurrentCacheMissesGenerateOnlyOneLiteLlmKey() throws Exception {
        AuthUser user = new AuthUser();
        user.setId("user-1");

        APIKeyManager apiKeyManager = mock(APIKeyManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);

        AtomicInteger generateCount = new AtomicInteger();
        when(apiKeyManager.getApiKey(eq("team-1"), eq("user-1")))
                .thenReturn(Mono.empty(), Mono.empty(), Mono.empty(), Mono.just("generated-key"));
        when(apiKeyManager.saveApiKey(eq("team-1"), eq("user-1"), eq("generated-key")))
                .thenReturn(Mono.just(true));
        when(apiKeyManager.acquireApiKeyGenerationLock(eq("team-1"), eq("user-1"), anyString()))
                .thenReturn(Mono.just(true), Mono.just(false));
        when(apiKeyManager.releaseApiKeyGenerationLock(eq("team-1"), eq("user-1"), anyString()))
                .thenReturn(Mono.just(true));
        when(liteLLMManager.getTeamInfo(eq("team-1")))
                .thenReturn(Mono.just(Result.success(json("{}"))));
        when(liteLLMManager.getUserInfo(eq("user-1")))
                .thenReturn(Mono.just(Result.success(json("""
                        {"spend": 0, "max_budget": 10}
                        """))));
        when(liteLLMManager.regenerateApiKeyWithinUserBudget(
                org.mockito.ArgumentMatchers.any(),
                eq("user-1"),
                eq("team-1")))
                .thenAnswer(invocation -> Mono.delay(Duration.ofMillis(50))
                        .doOnNext(ignored -> generateCount.incrementAndGet())
                        .thenReturn(Result.success(json("""
                                {"key": "generated-key"}
                                """))));
        when(liteLLMManager.isApiKeyValid(eq("generated-key"), eq("user-1"), eq("team-1")))
                .thenReturn(Mono.just(true));

        LiteLLMAuthGatewayFilterFactory filter = new LiteLLMAuthGatewayFilterFactory();
        ReflectionTestUtils.setField(filter, "apiKeyManager", apiKeyManager);
        ReflectionTestUtils.setField(filter, "liteLLMManager", liteLLMManager);
        ReflectionTestUtils.setField(filter, "authUserManager", mock(AuthUserManager.class));

        Mono<String> first = ReflectionTestUtils.invokeMethod(filter, "getValidApiKey", user, "team-1");
        Mono<String> second = ReflectionTestUtils.invokeMethod(filter, "getValidApiKey", user, "team-1");

        StepVerifier.create(Mono.zip(first, second))
                .expectNextMatches(tuple -> tuple.getT1().equals("generated-key")
                        && tuple.getT2().equals("generated-key"))
                .verifyComplete();

        assertThat(generateCount.get()).isEqualTo(1);
    }

    @Test
    void cacheMissForNewUserDoesNotClearTeamMemberModelRestrictions() throws Exception {
        AuthUser user = new AuthUser();
        user.setId("user-1");

        APIKeyManager apiKeyManager = mock(APIKeyManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);

        when(apiKeyManager.getApiKey(eq("team-1"), eq("user-1")))
                .thenReturn(Mono.empty());
        when(apiKeyManager.saveApiKey(eq("team-1"), eq("user-1"), eq("generated-key")))
                .thenReturn(Mono.just(true));
        when(apiKeyManager.acquireApiKeyGenerationLock(eq("team-1"), eq("user-1"), anyString()))
                .thenReturn(Mono.just(true));
        when(apiKeyManager.releaseApiKeyGenerationLock(eq("team-1"), eq("user-1"), anyString()))
                .thenReturn(Mono.just(true));
        when(liteLLMManager.getTeamInfo(eq("team-1")))
                .thenReturn(Mono.just(Result.success(json("{}"))));
        when(liteLLMManager.getUserInfo(eq("user-1")))
                .thenReturn(
                        Mono.just(Result.failure(404, "user not found")),
                        Mono.just(Result.success(json("""
                                {"spend": 0, "max_budget": 10}
                                """))));
        when(liteLLMManager.createUserAndAssignTeam(eq(user), eq("team-1")))
                .thenReturn(Mono.just(Result.success(json("{}"))));
        when(liteLLMManager.regenerateApiKeyWithinUserBudget(
                org.mockito.ArgumentMatchers.any(),
                eq("user-1"),
                eq("team-1")))
                .thenReturn(Mono.just(Result.success(json("""
                        {"key": "generated-key"}
                        """))));
        when(liteLLMManager.isApiKeyValid(eq("generated-key"), eq("user-1"), eq("team-1")))
                .thenReturn(Mono.just(true));

        LiteLLMAuthGatewayFilterFactory filter = new LiteLLMAuthGatewayFilterFactory();
        ReflectionTestUtils.setField(filter, "apiKeyManager", apiKeyManager);
        ReflectionTestUtils.setField(filter, "liteLLMManager", liteLLMManager);
        ReflectionTestUtils.setField(filter, "authUserManager", mock(AuthUserManager.class));

        Mono<String> generated = ReflectionTestUtils.invokeMethod(filter, "getValidApiKey", user, "team-1");

        StepVerifier.create(generated)
                .expectNext("generated-key")
                .verifyComplete();
    }

    @Test
    void insufficientQuotaReturnsFriendlyJsonBody() {
        AuthUser user = new AuthUser();
        user.setId("user-1");

        APIKeyManager apiKeyManager = mock(APIKeyManager.class);
        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);

        when(authUserManager.getCurrentUser(eq("access-token"))).thenReturn(Mono.just(user));
        when(apiKeyManager.getApiKey(eq("team-1"), eq("user-1"))).thenReturn(Mono.empty());
        when(apiKeyManager.acquireApiKeyGenerationLock(eq("team-1"), eq("user-1"), anyString()))
                .thenReturn(Mono.just(true));
        when(apiKeyManager.releaseApiKeyGenerationLock(eq("team-1"), eq("user-1"), anyString()))
                .thenReturn(Mono.just(true));
        when(liteLLMManager.getTeamInfo(eq("team-1"))).thenReturn(Mono.just(Result.success(json("{}"))));
        when(liteLLMManager.getUserInfo(eq("user-1"))).thenReturn(Mono.just(Result.success(json("""
                {"spend": 10, "max_budget": 10}
                """))));
        when(liteLLMManager.regenerateApiKeyWithinUserBudget(
                org.mockito.ArgumentMatchers.any(),
                eq("user-1"),
                eq("team-1")))
                .thenReturn(Mono.just(Result.failure(402, "User quota is exhausted.")));

        LiteLLMAuthGatewayFilterFactory filter = new LiteLLMAuthGatewayFilterFactory();
        ReflectionTestUtils.setField(filter, "apiKeyManager", apiKeyManager);
        ReflectionTestUtils.setField(filter, "liteLLMManager", liteLLMManager);
        ReflectionTestUtils.setField(filter, "authUserManager", authUserManager);

        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/litellm_proxy/team-1/access-token/v1/models").build()
        );
        exchange.getAttributes().put(
                ServerWebExchangeUtils.URI_TEMPLATE_VARIABLES_ATTRIBUTE,
                Map.of("teamId", "team-1", "accessToken", "access-token")
        );
        GatewayFilterChain chain = ignored -> Mono.error(new AssertionError("request should not be proxied"));

        StepVerifier.create(filter.apply(new LiteLLMAuthGatewayFilterFactory.Config()).filter(exchange, chain))
                .verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.PAYMENT_REQUIRED);
        assertThat(exchange.getResponse().getBodyAsString().block())
                .contains("builtin_provider_quota_exhausted")
                .contains("内置模型免费额度已用尽");
    }

    private static JsonNode json(String raw) {
        try {
            return JSON.readTree(raw);
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }
}
