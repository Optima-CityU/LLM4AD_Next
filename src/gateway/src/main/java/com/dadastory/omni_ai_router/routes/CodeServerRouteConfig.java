package com.dadastory.omni_ai_router.routes;

import com.dadastory.omni_ai_router.filter.AuthCheckGatewayFilterFactory;
import com.dadastory.omni_ai_router.filter.CodeServerFilterFactory;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Registers routes for per-user code-server instances.
 */
@Configuration
@Slf4j
public class CodeServerRouteConfig {

    @Value("${GATEWAY_AUTH_URL:http://backend:8000/api/v1/llm4ad/tasks/code_auth/}")
    private String authBackendUrl;

    /**
     * Creates the code-server route guarded by gateway authentication.
     *
     * @param builder route locator builder
     * @param authCheckGatewayFilterFactory backend authentication filter
     * @param codeServerFilterFactory code-server target rewrite filter
     * @return code-server routes
     */
    @Bean
    public RouteLocator codeServerRoutes(RouteLocatorBuilder builder,
                                         AuthCheckGatewayFilterFactory authCheckGatewayFilterFactory,
                                         CodeServerFilterFactory codeServerFilterFactory) {
        log.info("Registering code-server route. authUrl={}", authBackendUrl);
        return builder.routes()
                .route("code_server_routes", r ->
                        r.path("/code_ide/**")
                                .filters(f ->
                                        f.stripPrefix(1)
                                                .filter(authCheckGatewayFilterFactory.apply(config -> {
                                                    config.setAuthUrl(authBackendUrl);
                                                    config.setSkipStaticFile(false);
                                                })).filter(codeServerFilterFactory.apply(new Object()))
                                )
                                .uri("no://op")
                ).build();
    }
}
