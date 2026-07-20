package com.dadastory.omni_ai_router.filter;

import com.dadastory.omni_ai_router.manager.AuthUserManager;
import jakarta.annotation.Resource;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NullMarked;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.OrderedGatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

/**
 * Authenticates MindMemOS requests and converts them to per-user LiteLLM traffic.
 *
 * <p>The binding stores a stable route containing the user ID. The service token
 * is supplied by MindMemOS only at request time and never persisted in Qdrant.</p>
 */
@Slf4j
@Component
public class MemoryServiceGatewayFilterFactory
        extends AbstractGatewayFilterFactory<MemoryServiceGatewayFilterFactory.Config> {

    @Value("${gateway.memory-service-token:}")
    private String memoryServiceToken;

    @Resource
    private AuthUserManager authUserManager;
    @Resource
    private LiteLLMAuthGatewayFilterFactory liteLlmAuth;

    @Data
    @NoArgsConstructor
    public static class Config {
        private String teamIdVariable = "teamId";
        private String userIdVariable = "userId";
    }

    @NullMarked
    @Override
    public GatewayFilter apply(Config config) {
        return new OrderedGatewayFilter((exchange, chain) -> {
            Map<String, String> variables = exchange.getAttribute(ServerWebExchangeUtils.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
            String teamId = variables == null ? null : variables.get(config.getTeamIdVariable());
            String userId = variables == null ? null : variables.get(config.getUserIdVariable());
            if (!StringUtils.hasText(teamId) || !StringUtils.hasText(userId) || !hasValidServiceToken(exchange)) {
                exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
                return exchange.getResponse().setComplete();
            }

            return authUserManager.getActiveUserById(userId)
                    .flatMap(user -> liteLlmAuth.resolveLiteLlmApiKey(user, teamId)
                            .flatMap(apiKey -> proxyWithLiteLlmKey(exchange, chain, apiKey)))
                    .onErrorResume(error -> handleError(exchange, teamId, userId, error));
        }, -10);
    }

    private boolean hasValidServiceToken(ServerWebExchange exchange) {
        if (!StringUtils.hasText(memoryServiceToken)) {
            log.error("Memory gateway service token is not configured.");
            return false;
        }
        String header = exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION);
        if (!StringUtils.hasText(header) || !header.startsWith("Bearer ")) {
            return false;
        }
        byte[] provided = header.substring("Bearer ".length()).getBytes(StandardCharsets.UTF_8);
        byte[] expected = memoryServiceToken.getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(provided, expected);
    }

    private Mono<Void> proxyWithLiteLlmKey(ServerWebExchange exchange, GatewayFilterChain chain, String apiKey) {
        ServerHttpRequest request = exchange.getRequest().mutate()
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + apiKey)
                .build();
        return chain.filter(exchange.mutate().request(request).build());
    }

    private Mono<Void> handleError(ServerWebExchange exchange, String teamId, String userId, Throwable error) {
        if (error instanceof AuthUserManager.UnauthorizedUserException) {
            log.warn("Memory model request rejected. team={}, user={}, reason={}", teamId, userId, error.getMessage());
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }
        if (error instanceof LiteLLMAuthGatewayFilterFactory.InsufficientQuotaException) {
            return liteLlmAuth.writeInsufficientQuotaResponse(exchange);
        }
        log.error("Memory model request failed. team={}, user={}", teamId, userId, error);
        exchange.getResponse().setStatusCode(HttpStatus.INTERNAL_SERVER_ERROR);
        return exchange.getResponse().setComplete();
    }
}
