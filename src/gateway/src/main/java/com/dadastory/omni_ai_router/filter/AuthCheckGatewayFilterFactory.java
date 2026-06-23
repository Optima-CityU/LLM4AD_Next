package com.dadastory.omni_ai_router.filter;

import com.dadastory.omni_ai_router.config.LiteLLMProperties;
import jakarta.annotation.Resource;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NullMarked;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.net.URI;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Gateway filter that delegates user authentication to the backend auth service.
 *
 * <p>The filter stores authenticated user information in the exchange attributes so
 * downstream filters can reuse the verified identity without calling the auth
 * service again.</p>
 */
@Slf4j
@Component
public class AuthCheckGatewayFilterFactory extends AbstractGatewayFilterFactory<AuthCheckGatewayFilterFactory.Config> {

    private static final Pattern STATIC_FILES = Pattern.compile(".*\\.(js|css|jpg|jpeg|png|gif|svg|ico|woff|woff2|ttf|eot)$", Pattern.CASE_INSENSITIVE);

    @Value("${LITELLM_AUTH_TOKEN:}")
    private String litellmAuthKey;
    @Resource
    private LiteLLMProperties properties;

    public static final String USER_ID_CONTEXT_KEY = "X-User-ID";
    public static final String USER_EMAIL_CONTEXT_KEY = "X-User-Email";
    public static final String IS_ADMIN_ROLE = "X-Is-Admin";

    private final WebClient webClient;

    public AuthCheckGatewayFilterFactory() {
        super(Config.class);
        this.webClient = WebClient.builder().build();
    }

    /**
     * Builds the authentication filter used by protected gateway routes.
     *
     * @param config route-level authentication configuration
     * @return gateway filter that validates the request and enriches the exchange
     */
    @NullMarked
    @Override
    public GatewayFilter apply(Config config) {
        return (exchange, chain) -> {
            String path = exchange.getRequest().getPath().toString();
            // 1. 静态资源直接放行
            if (STATIC_FILES.matcher(path).matches() && config.isSkipStaticFile()) {
                return chain.filter(exchange);
            }

            String rawPath = exchange.getRequest().getURI().getRawPath();
            String rawQuery = exchange.getRequest().getURI().getRawQuery();
            String originalUri = StringUtils.hasText(rawQuery) ? rawPath + "?" + rawQuery : rawPath;

            String remoteAddr = exchange.getRequest().getRemoteAddress() != null ?
                    exchange.getRequest().getRemoteAddress().getAddress().getHostAddress() : "";

            log.debug("Auth check started. path={}, remoteAddr={}, host={}",
                    originalUri, remoteAddr, exchange.getRequest().getHeaders().getFirst(HttpHeaders.HOST));
            exchange.getRequest().getCookies().forEach((cookieName, cookieList) ->
                    log.trace("Client cookie received. name={}, count={}", cookieName, cookieList.size()));
            // 2. 调用 Python 接口进行鉴权
            return webClient.get()
                    .uri(config.getAuthUrl())
                    .headers(httpHeaders -> {
                        httpHeaders.set("X-Original-URI", originalUri);
                        httpHeaders.set("X-Real-IP", remoteAddr);
                        String host = exchange.getRequest().getHeaders().getFirst(HttpHeaders.HOST);
                        if (StringUtils.hasText(host)) {
                            httpHeaders.set(HttpHeaders.HOST, host);
                        }
                    })
                    .cookies(clientCookies -> {
                        exchange.getRequest().getCookies().forEach((cookieName, cookieList) -> {
                            cookieList.forEach(cookie -> {
                                clientCookies.add(cookieName, cookie.getValue());
                            });
                        });
                    })
                    .retrieve()
                    // 异常处理
                    .onStatus(status -> status.is4xxClientError() || status.is5xxServerError(),
                            response -> response.bodyToMono(String.class)
                                    .defaultIfEmpty("【无返回Body】")
                                    .flatMap(body -> {
                                        String errorMsg = "Python Auth Error, Status: " + response.statusCode() + ", Body: " + body;
                                        return Mono.error(new RuntimeException(errorMsg));
                                    })
                    )
                    .toEntity(String.class)
                    .flatMap(response -> {
                        // 3. 从 Python 返回的结果中获取 User-ID
                        String userId = response.getHeaders().getFirst(USER_ID_CONTEXT_KEY);
                        String userEmail = response.getHeaders().getFirst(USER_EMAIL_CONTEXT_KEY);
                        String isAdmin = response.getHeaders().getFirst(IS_ADMIN_ROLE);

                        log.debug("Auth backend validation succeeded. userId={}, userEmail={}, isAdmin={}",
                                userId, userEmail, isAdmin);

                        if (!StringUtils.hasText(userId) || !StringUtils.hasText(userEmail)) {
                            log.warn("Auth backend returned 200 without required identity headers. path={}", originalUri);
                            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
                            return exchange.getResponse().setComplete();
                        }
                        // 管理员权限校验
                        if (config.isRequireAdmin() && !"true".equalsIgnoreCase(isAdmin)) {
                            log.error("【拒绝访问】用户：{}并非管理员尝试访问管理员路由！", userId);
                            exchange.getResponse().setStatusCode(HttpStatus.FORBIDDEN);
                            return exchange.getResponse().setComplete();
                        } else if (config.isRequireAdmin() && "true".equalsIgnoreCase(isAdmin)) {
                            boolean hasLitellmCookie = exchange.getRequest().getCookies().containsKey("token");
                            if (hasLitellmCookie) {
                                log.info("【网关放行】检查到管理员已经有了litellm的有效登录cookie，免检放行");
                                exchange.getAttributes().put(USER_ID_CONTEXT_KEY, userId);
                                return chain.filter(exchange);
                            }
                            // 自动登录逻辑
                            log.info("【管理员访问】管理员：{}访问litellm管理页面，正在尝试静默登录", userId);
                            String baseUrl = properties.getBaseUrl();
                            if (baseUrl.endsWith("/")) {
                                baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
                            }
                            String absoluteLoginUrl = baseUrl + "/v2/login";

                            return webClient.post()
                                    .uri(URI.create(absoluteLoginUrl))
                                    .headers(headers -> headers.remove(HttpHeaders.AUTHORIZATION))
                                    .header(HttpHeaders.CONTENT_TYPE, "application/json")
                                    .bodyValue(Map.of(
                                            "username", "admin",
                                            "password", litellmAuthKey
                                    ))
                                    .exchangeToMono(litellmResponse -> {
                                        String setCookieHeader = litellmResponse.headers().asHttpHeaders()
                                                .getFirst(HttpHeaders.SET_COOKIE);
                                        if (StringUtils.hasText(setCookieHeader)) {
                                            log.info("【网关代理登录】成功获取到V2认证cookie");

                                            if (!setCookieHeader.contains("Path=")) {
                                                setCookieHeader = setCookieHeader + "; Path=/litellm";
                                            }
                                            exchange.getResponse().getHeaders().add(HttpHeaders.SET_COOKIE, setCookieHeader);
                                        } else {
                                            log.warn("【网关代理登录失败】/v2/login接口未返回Set-Cookie头，请检查服务是否正常");
                                        }
                                        exchange.getAttributes().put(USER_ID_CONTEXT_KEY, userId);
                                        return chain.filter(exchange);
                                    });
                        }
                        exchange.getAttributes().put(USER_ID_CONTEXT_KEY, userId);
                        return chain.filter(exchange);

                    })
                    .onErrorResume(e -> {
                        log.warn("Auth check failed. path={}, reason={}", originalUri, e.getMessage());
                        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
                        return exchange.getResponse().setComplete();
                    });
        };
    }

    /**
     * Route-level settings for backend authentication.
     */
    @Data
    @NoArgsConstructor
    public static class Config {
        private String authUrl;
        private boolean requireAdmin = false;
        private boolean skipStaticFile = true;
    }
}
