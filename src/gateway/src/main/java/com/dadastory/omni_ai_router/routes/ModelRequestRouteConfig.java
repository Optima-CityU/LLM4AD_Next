package com.dadastory.omni_ai_router.routes;

import com.dadastory.omni_ai_router.filter.LiteLLMAuthGatewayFilterFactory;
import com.dadastory.omni_ai_router.filter.MemoryServiceGatewayFilterFactory;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.stereotype.Component;

/**
 * Registers routes that proxy model requests to LiteLLM with per-user API keys.
 */
@Component
@Slf4j
public class ModelRequestRouteConfig {

    @Value("${LITELLM_BASE_URL:http://litellm:4000}")
    private String liteLLMBaseUrl;

    /**
     * Creates the model proxy route for paths containing team id and backend access token.
     *
     * @param builder route locator builder
     * @param liteLLMAuthGatewayFilterFactory filter that injects the LiteLLM API key
     * @return model proxy routes
     */
    @Bean
    public RouteLocator modelRequestRouteRoutes(RouteLocatorBuilder builder,
                                                LiteLLMAuthGatewayFilterFactory liteLLMAuthGatewayFilterFactory,
                                                MemoryServiceGatewayFilterFactory memoryServiceGatewayFilterFactory) {
        LiteLLMAuthGatewayFilterFactory.Config litellmConfig = new LiteLLMAuthGatewayFilterFactory.Config();
        MemoryServiceGatewayFilterFactory.Config memoryConfig = new MemoryServiceGatewayFilterFactory.Config();
        log.info("Registering model proxy route. target={}", liteLLMBaseUrl);
        return builder.routes()
                .route("customer_model_routes", r ->
                        r.path("/litellm_proxy/{teamId}/{accessToken}/**")
                                .filters(f ->
                                        f.stripPrefix(3)
                                                .filter(liteLLMAuthGatewayFilterFactory.apply(litellmConfig))
                                ).uri(liteLLMBaseUrl))
                .route("memory_model_routes", r ->
                        r.path("/litellm_memory_proxy/{teamId}/{userId}/**")
                                .filters(f ->
                                        f.stripPrefix(3)
                                                .filter(memoryServiceGatewayFilterFactory.apply(memoryConfig))
                                ).uri(liteLLMBaseUrl))
                .build();
    }
}
