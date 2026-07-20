package com.dadastory.omni_ai_router.manager;

import com.dadastory.omni_ai_router.entity.AuthUser;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * Reactive client for resolving access tokens through the backend auth service.
 */
@Slf4j
@Service
public class AuthUserManager {

    private final WebClient webClient;

    @Value("${gateway.user-me-url:http://backend/api/v1/users/me}")
    private String userMeUrl;
    @Value("${gateway.user-by-id-url:http://backend:8000/api/v1/internal/gateway/users}")
    private String userByIdUrl;
    @Value("${gateway.backend-service-token:}")
    private String backendServiceToken;

    public AuthUserManager() {
        this.webClient = WebClient.builder().build();
    }

    /**
     * Resolves an access token to the current backend user profile.
     *
     * @param accessToken access token from the model proxy path
     * @return active user profile
     */
    public Mono<AuthUser> getCurrentUser(String accessToken) {
        if (!StringUtils.hasText(accessToken)) {
            return Mono.error(new IllegalArgumentException("Access token is empty."));
        }

        return webClient.get()
                .uri(userMeUrl)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                .retrieve()
                .onStatus(status -> status.is4xxClientError() || status.is5xxServerError(),
                        response -> response.bodyToMono(String.class)
                                .defaultIfEmpty("No response body")
                                .flatMap(body -> {
                                    log.warn("Backend user lookup failed. status={}, body={}",
                                            response.statusCode(), body);
                                    int statusCode = response.statusCode().value();
                                    if (statusCode == 401 || statusCode == 403) {
                                        return Mono.error(new UnauthorizedUserException("Access token is invalid."));
                                    }
                                    return Mono.error(new RuntimeException("Backend user lookup failed."));
                                }))
                .bodyToMono(AuthUser.class)
                .flatMap(user -> {
                    if (user == null || !StringUtils.hasText(user.getId()) || !StringUtils.hasText(user.getEmail())) {
                        return Mono.error(new UnauthorizedUserException("Backend user profile is missing identity fields."));
                    }
                    if (!user.isActive()) {
                        return Mono.error(new UnauthorizedUserException("Backend user is inactive."));
                    }
                    return Mono.just(user);
                })
                .doOnSuccess(user -> log.debug("Backend access token resolved. user={}, email={}",
                        user.getId(), user.getEmail()));
    }

    /** Resolve an active user through the gateway-only backend endpoint. */
    public Mono<AuthUser> getActiveUserById(String userId) {
        if (!StringUtils.hasText(userId) || !StringUtils.hasText(backendServiceToken)) {
            return Mono.error(new UnauthorizedUserException("Gateway service identity is unavailable."));
        }

        String url = userByIdUrl.replaceAll("/$", "") + "/" + userId;
        return webClient.get()
                .uri(url)
                .header("X-Gateway-Service-Token", backendServiceToken)
                .retrieve()
                .onStatus(status -> status.is4xxClientError() || status.is5xxServerError(),
                        response -> response.bodyToMono(String.class)
                                .defaultIfEmpty("No response body")
                                .flatMap(body -> {
                                    log.warn("Backend gateway user lookup failed. user={}, status={}, body={}",
                                            userId, response.statusCode(), body);
                                    int statusCode = response.statusCode().value();
                                    if (statusCode == 401 || statusCode == 403 || statusCode == 404) {
                                        return Mono.error(new UnauthorizedUserException("Target user is unavailable."));
                                    }
                                    return Mono.error(new RuntimeException("Backend gateway user lookup failed."));
                                }))
                .bodyToMono(AuthUser.class)
                .flatMap(user -> {
                    if (user == null || !StringUtils.hasText(user.getId()) || !StringUtils.hasText(user.getEmail())
                            || !user.isActive()) {
                        return Mono.error(new UnauthorizedUserException("Target user is unavailable."));
                    }
                    return Mono.just(user);
                });
    }

    public static class UnauthorizedUserException extends RuntimeException {
        public UnauthorizedUserException(String message) {
            super(message);
        }
    }
}
