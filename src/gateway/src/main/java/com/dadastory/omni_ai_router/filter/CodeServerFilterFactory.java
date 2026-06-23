package com.dadastory.omni_ai_router.filter;

import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NullMarked;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.OrderedGatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;

/**
 * Routes authenticated code-server traffic to the per-user code-server instance.
 *
 * <p>The filter also normalizes Origin and Host handling so WebSocket upgrades
 * from code-server remain valid after passing through Spring Cloud Gateway.</p>
 */
@Component
@Slf4j
public class CodeServerFilterFactory extends AbstractGatewayFilterFactory<Object> {

    /**
     * Builds the code-server routing filter.
     *
     * @param config unused filter configuration
     * @return ordered gateway filter that rewrites the target host and scheme
     */
    @NullMarked
    @Override
    public GatewayFilter apply(Object config) {
        GatewayFilter filter = (exchange, chain) -> {

            String userId = exchange.getAttribute(AuthCheckGatewayFilterFactory.USER_ID_CONTEXT_KEY);

            if (userId == null || userId.isEmpty()) {
                log.warn("Code-server routing failed because user id is missing from exchange attributes.");
                exchange.getResponse().setStatusCode(HttpStatus.INTERNAL_SERVER_ERROR);
                return exchange.getResponse().setComplete();
            }

            ServerHttpRequest request = exchange.getRequest();

            // ===========================================================
            // 核心修复 1：动态求取真实 Origin 参数
            // ===========================================================
            String reqScheme = request.getHeaders().getFirst("X-Forwarded-Proto");
            if (reqScheme == null) {
                reqScheme = request.getURI().getScheme();
            }

            String reqHost = request.getHeaders().getFirst(HttpHeaders.HOST);
            if (reqHost == null) {
                reqHost = request.getURI().getAuthority();
            }

            String mockOrigin = reqScheme + "://" + reqHost;

            // ===========================================================
            // 核心修复 2：使用 set 强行覆盖，而不是 header 追加！
            // 防止产生畸形的 "Origin: url, url" 导致 WebSocket 400 握手断开
            // ===========================================================
            ServerHttpRequest mutatedRequest = request.mutate()
                    .headers(httpHeaders -> {
                        // 移除所有的旧 Origin 防止残留
                        httpHeaders.remove(HttpHeaders.ORIGIN);
                        // set 为保证有且仅有这唯一的合法 Origin
                        httpHeaders.set(HttpHeaders.ORIGIN, mockOrigin);
                    })
                    .build();

            ServerWebExchange mutatedExchange = exchange.mutate().request(mutatedRequest).build();

            // ===========================================================
            // 核心修复 3：在网关内核直接强声明“保留 Host 真实头”！
            // 防止网关覆写 Host 导致与刚好修好的 Origin 不匹配，引发 WS 403 握手断开
            // ===========================================================
            mutatedExchange.getAttributes().put(ServerWebExchangeUtils.PRESERVE_HOST_HEADER_ATTRIBUTE, true);

            try {
                URI currentGatewayUrl = mutatedExchange.getAttribute(ServerWebExchangeUtils.GATEWAY_REQUEST_URL_ATTR);
                if (currentGatewayUrl == null) {
                    currentGatewayUrl = mutatedRequest.getURI();
                }

                // --- 保持你之前正确的 WebSocket 识别逻辑 ---
                String scheme = "http";
                String upgradeHeader = mutatedRequest.getHeaders().getUpgrade();
                if ("websocket".equalsIgnoreCase(upgradeHeader)) {
                    scheme = "ws"; // 遇到 VS Code 发起的 WebSocket 连接，必定转为 ws
                }

                URI targetUri = UriComponentsBuilder.fromUri(currentGatewayUrl)
                        .scheme(scheme)  // 动态使用 http 或 ws
                        .host("code_user-" + userId)
                        .port(8080)
                        .build(true)
                        .toUri();

                mutatedExchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_REQUEST_URL_ATTR, targetUri);
                log.debug("Code-server request routed. user={}, scheme={}, targetHost={}",
                        userId, scheme, targetUri.getHost());

            } catch (Exception e) {
                log.error("Code-server route rewrite failed. user={}", userId, e);
                mutatedExchange.getResponse().setStatusCode(HttpStatus.INTERNAL_SERVER_ERROR);
                return mutatedExchange.getResponse().setComplete();
            }

            return chain.filter(mutatedExchange);
        };
        // 挂载点优先级不变
        return new OrderedGatewayFilter(filter, 10001);
    }
}
