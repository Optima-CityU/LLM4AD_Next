package com.dadastory.omni_ai_router.filter;

import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NullMarked;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.OrderedGatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;

/**
 * Allows a model request to override the target base URL through a configured header.
 */
@Component
@Slf4j
public class DynamicModelRequestFilterFactory extends AbstractGatewayFilterFactory<DynamicModelRequestFilterFactory.Config> {

    /**
     * Builds the dynamic routing filter.
     *
     * @param config header configuration used to read the target base URL
     * @return ordered gateway filter that runs late enough to override the route URI
     */
    @NullMarked
    @Override
    public GatewayFilter apply(Config config) {
        GatewayFilter filter = (exchange, chain) -> {
            String headerName = config.getHeaderName();
            String customBaseUrl = exchange.getRequest().getHeaders().getFirst(headerName);
            if (customBaseUrl != null && !customBaseUrl.isEmpty()) {
                try {
                    URI originalUri = exchange.getRequest().getURI();
                    URI newROutingUri = UriComponentsBuilder.fromUriString(customBaseUrl)
                            .path(originalUri.getPath())
                            .query(originalUri.getQuery())
                            .build(true)
                            .toUri();
                    exchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_REQUEST_URL_ATTR, newROutingUri);
                    log.debug("Dynamic model route applied. header={}, target={}", headerName, newROutingUri);
                } catch (Exception e) {
                    log.warn("Invalid dynamic base URL in header: {}", customBaseUrl, e);
                }
            }
            return chain.filter(exchange);
        };
        return new OrderedGatewayFilter(filter, 10001);
    }

    /**
     * Route-level configuration for dynamic model routing.
     */
    @Data
    @NoArgsConstructor
    public static class Config {
        private String headerName = "X-Route-Base-Url";
    }
}
