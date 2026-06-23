package com.dadastory.omni_ai_router.routes;

import com.dadastory.omni_ai_router.filter.AuthCheckGatewayFilterFactory;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Registers the LiteLLM admin UI route guarded by gateway authentication.
 */
@Configuration
@Slf4j
public class LiteLLMAdminRouteConfig {

    @Value("${LITELLM_BASE_URL:http://litellm:4000}")
    private String liteLLMBaseUrl;

    @Value("${GATEWAY_AUTH_URL:http://backend:8000/api/v1/llm4ad/tasks/code_auth/}")
    private String authBackendUrl;

    /**
     * Creates the LiteLLM admin route.
     *
     * @param builder route locator builder
     * @param authCheckGatewayFilterFactory backend authentication filter
     * @return LiteLLM admin routes
     */
    @Bean
    public RouteLocator liteLLMAdminRoutes(RouteLocatorBuilder builder,
                                           AuthCheckGatewayFilterFactory authCheckGatewayFilterFactory) {
        log.info("Registering LiteLLM admin route. target={}, authUrl={}", liteLLMBaseUrl, authBackendUrl);
        return builder.routes()
                .route("litellm_admin_routes", r ->
                        r.path("/litellm/**")
                                .filters(f ->
                                        f.filter(authCheckGatewayFilterFactory.apply(config -> {
                                            config.setAuthUrl(authBackendUrl);
                                            config.setRequireAdmin(true);
                                            config.setSkipStaticFile(true);
                                        })).preserveHostHeader())
                                .uri(liteLLMBaseUrl)
                ).build();
    }
}
