package com.dadastory.omni_ai_router.controller;

import com.dadastory.omni_ai_router.dto.LiteLLMUserQuota;
import com.dadastory.omni_ai_router.dto.Result;
import com.dadastory.omni_ai_router.manager.AuthUserManager;
import com.dadastory.omni_ai_router.manager.LiteLLMManager;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.util.List;

/**
 * Gateway-owned LiteLLM administration endpoints.
 *
 * <p>These endpoints never rely on LiteLLM UI cookies. They validate the
 * backend access token directly and require a superuser before calling LiteLLM
 * admin APIs.</p>
 */
@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/internal/litellm")
public class LiteLLMAdminController {

    private final AuthUserManager authUserManager;
    private final LiteLLMManager liteLLMManager;

    @Value("${TEAM_ID:}")
    private String teamId;

    /**
     * Lists normalized LiteLLM user quota rows for administrators.
     *
     * @param authorization backend access-token authorization header
     * @return normalized quota rows wrapped in the common result envelope
     */
    @GetMapping("/users/quotas")
    public Mono<ResponseEntity<Result<List<LiteLLMUserQuota>>>> listUserQuotas(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorization
    ) {
        String token = extractBearerToken(authorization);
        if (!StringUtils.hasText(token)) {
            return Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Result.<List<LiteLLMUserQuota>>failure(401, "Missing bearer token.")));
        }

        return authUserManager.getCurrentUser(token)
                .flatMap(user -> {
                    if (!user.isSuperuser()) {
                        log.warn("Non-admin user attempted to query LiteLLM user quotas. user={}", user.getId());
                        return Mono.just(ResponseEntity.status(HttpStatus.FORBIDDEN)
                                .body(Result.<List<LiteLLMUserQuota>>failure(
                                        403,
                                        "Only administrators can query LiteLLM user quotas."
                                )));
                    }
                    return liteLLMManager.listUserQuotas()
                            .map(rows -> ResponseEntity.ok(Result.<List<LiteLLMUserQuota>>success(rows)));
                })
                .onErrorResume(AuthUserManager.UnauthorizedUserException.class, error ->
                        Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                                .body(Result.<List<LiteLLMUserQuota>>failure(401, "Access token is invalid."))))
                .onErrorResume(error -> {
                    log.warn("Failed to query LiteLLM user quotas.", error);
                    return Mono.just(ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                            .body(Result.<List<LiteLLMUserQuota>>failure(
                                    502,
                                    "Failed to query LiteLLM user quotas: " + error.getMessage()
                            )));
                });
    }

    /**
     * Lists chat models allowed for the configured LiteLLM team.
     *
     * @param authorization backend access-token authorization header
     * @return normalized model ids wrapped in the common result envelope
     */
    @GetMapping("/team/models")
    public Mono<ResponseEntity<Result<List<String>>>> listTeamModels(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorization
    ) {
        String token = extractBearerToken(authorization);
        if (!StringUtils.hasText(token)) {
            return Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Result.<List<String>>failure(401, "Missing bearer token.")));
        }
        if (!StringUtils.hasText(teamId)) {
            return Mono.just(ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                    .body(Result.<List<String>>failure(502, "LiteLLM team id is not configured.")));
        }

        return authUserManager.getCurrentUser(token)
                .flatMap(user -> liteLLMManager.listTeamModels(teamId)
                        .map(models -> ResponseEntity.ok(Result.<List<String>>success(models))))
                .onErrorResume(AuthUserManager.UnauthorizedUserException.class, error ->
                        Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                                .body(Result.<List<String>>failure(401, "Access token is invalid."))))
                .onErrorResume(error -> {
                    log.warn("Failed to query LiteLLM team models.", error);
                    return Mono.just(ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                            .body(Result.<List<String>>failure(
                                    502,
                                    "Failed to query LiteLLM team models: " + error.getMessage()
                            )));
                });
    }

    /**
     * Queries the current backend user's LiteLLM quota.
     *
     * @param authorization backend access-token authorization header
     * @return normalized quota row wrapped in the common result envelope
     */
    @GetMapping("/users/me/quota")
    public Mono<ResponseEntity<Result<LiteLLMUserQuota>>> getCurrentUserQuota(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorization
    ) {
        String token = extractBearerToken(authorization);
        if (!StringUtils.hasText(token)) {
            return Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Result.<LiteLLMUserQuota>failure(401, "Missing bearer token.")));
        }

        return authUserManager.getCurrentUser(token)
                .flatMap(user -> liteLLMManager.getUserQuota(user.getId())
                        .map(quota -> ResponseEntity.ok(Result.<LiteLLMUserQuota>success(quota))))
                .onErrorResume(AuthUserManager.UnauthorizedUserException.class, error ->
                        Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                                .body(Result.<LiteLLMUserQuota>failure(401, "Access token is invalid."))))
                .onErrorResume(error -> {
                    log.warn("Failed to query current LiteLLM user quota.", error);
                    return Mono.just(ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                            .body(Result.<LiteLLMUserQuota>failure(
                                    502,
                                    "Failed to query current LiteLLM user quota: " + error.getMessage()
                            )));
                });
    }

    private String extractBearerToken(String authorization) {
        if (!StringUtils.hasText(authorization)) {
            return null;
        }
        String prefix = "Bearer ";
        if (!authorization.regionMatches(true, 0, prefix, 0, prefix.length())) {
            return null;
        }
        return authorization.substring(prefix.length()).trim();
    }
}
