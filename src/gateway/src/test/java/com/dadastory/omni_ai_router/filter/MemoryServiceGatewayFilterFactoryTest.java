package com.dadastory.omni_ai_router.filter;

import com.dadastory.omni_ai_router.entity.AuthUser;
import com.dadastory.omni_ai_router.manager.AuthUserManager;
import org.junit.jupiter.api.Test;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class MemoryServiceGatewayFilterFactoryTest {

    @Test
    void validServiceTokenUsesPathUserForLiteLlmKey() {
        AuthUser user = new AuthUser();
        user.setId("user-1");
        user.setEmail("memory@example.com");
        user.setActive(true);
        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMAuthGatewayFilterFactory liteLlmAuth = mock(LiteLLMAuthGatewayFilterFactory.class);
        when(authUserManager.getActiveUserById("user-1")).thenReturn(Mono.just(user));
        when(liteLlmAuth.resolveLiteLlmApiKey(user, "team-1")).thenReturn(Mono.just("user-litellm-key"));

        MemoryServiceGatewayFilterFactory filter = filter("memory-service-token", authUserManager, liteLlmAuth);
        MockServerWebExchange exchange = exchange("Bearer memory-service-token");
        AtomicReference<String> authorization = new AtomicReference<>();
        GatewayFilterChain chain = chained -> {
            authorization.set(chained.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION));
            return Mono.empty();
        };

        StepVerifier.create(filter.apply(new MemoryServiceGatewayFilterFactory.Config()).filter(exchange, chain))
                .verifyComplete();

        assertThat(authorization.get()).isEqualTo("Bearer user-litellm-key");
    }

    @Test
    void invalidServiceTokenReturnsUnauthorizedWithoutProxying() {
        MemoryServiceGatewayFilterFactory filter = filter(
                "memory-service-token",
                mock(AuthUserManager.class),
                mock(LiteLLMAuthGatewayFilterFactory.class)
        );
        MockServerWebExchange exchange = exchange("Bearer invalid-token");
        GatewayFilterChain chain = ignored -> Mono.error(new AssertionError("request should not be proxied"));

        StepVerifier.create(filter.apply(new MemoryServiceGatewayFilterFactory.Config()).filter(exchange, chain))
                .verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    private static MemoryServiceGatewayFilterFactory filter(
            String serviceToken,
            AuthUserManager authUserManager,
            LiteLLMAuthGatewayFilterFactory liteLlmAuth
    ) {
        MemoryServiceGatewayFilterFactory filter = new MemoryServiceGatewayFilterFactory();
        ReflectionTestUtils.setField(filter, "memoryServiceToken", serviceToken);
        ReflectionTestUtils.setField(filter, "authUserManager", authUserManager);
        ReflectionTestUtils.setField(filter, "liteLlmAuth", liteLlmAuth);
        return filter;
    }

    private static MockServerWebExchange exchange(String authorization) {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/litellm_memory_proxy/team-1/user-1/v1/models")
                        .header(HttpHeaders.AUTHORIZATION, authorization)
                        .build()
        );
        exchange.getAttributes().put(
                ServerWebExchangeUtils.URI_TEMPLATE_VARIABLES_ATTRIBUTE,
                Map.of("teamId", "team-1", "userId", "user-1")
        );
        return exchange;
    }
}
