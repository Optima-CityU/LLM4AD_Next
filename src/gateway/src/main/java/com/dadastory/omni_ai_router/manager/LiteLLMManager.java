package com.dadastory.omni_ai_router.manager;

import com.dadastory.omni_ai_router.dto.Result;
import com.dadastory.omni_ai_router.dto.LiteLLMUserQuota;
import com.dadastory.omni_ai_router.entity.AuthUser;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import tools.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Reactive client for LiteLLM administrative APIs used by the gateway.
 */
@Slf4j
@Service
public class LiteLLMManager {

    private static final Set<String> MODEL_WILDCARDS = Set.of(
            "all-proxy-models",
            "all-team-models",
            "no-default-models"
    );

    @Resource
    private WebClient litellmWebClient;

    /**
     * Creates a LiteLLM user and assigns it to the requested team.
     *
     * @param user backend user to create
     * @param teamId team id to assign
     * @return LiteLLM result payload
     */
    public Mono<Result<?>> createUserAndAssignTeam(AuthUser user, String teamId) {
        String userId = user.getId();
        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("user_id", userId);
        requestBody.put("teams", List.of(teamId));
        requestBody.put("user_role", "internal_user_viewer");
        if (StringUtils.hasText(user.getEmail())) {
            requestBody.put("user_email", user.getEmail());
        }
        if (StringUtils.hasText(user.getFullName())) {
            requestBody.put("user_alias", user.getFullName());
        }
        requestBody.put("metadata", Map.of(
                "email_verified", user.isEmailVerified(),
                "is_superuser", user.isSuperuser(),
                "source", "backend-users-me"
        ));

        return litellmWebClient.post()
                .uri("/user/new")
                .bodyValue(requestBody)
                .exchangeToMono(this::handleResponse) // 复用响应处理逻辑
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM create user. user={}, team={}", userId, teamId))
                .onErrorResume(e -> {
                    log.warn("LiteLLM create user request failed. user={}, team={}", userId, teamId, e);
                    return Mono.just(Result.failure(500, "Create User Request failed: " + e.getMessage()));
                });
    }

    /**
     * Regenerates a LiteLLM API key and caps the key budget to the user's
     * currently available quota.
     *
     * @param userInfoResult LiteLLM user info result used to derive remaining quota
     * @param userId LiteLLM user id
     * @param teamId LiteLLM team id
     * @return LiteLLM key generation result, or a failure when quota is exhausted/unavailable
     */
    public Mono<Result<?>> regenerateApiKeyWithinUserBudget(
            Result<?> userInfoResult,
            String userId,
            String teamId
    ) {
        Double remainingBudget = extractRemainingBudget(userInfoResult);
        if (remainingBudget == null) {
            log.warn("Cannot generate LiteLLM key because user quota is unavailable. user={}, team={}", userId, teamId);
            return Mono.just(Result.failure(402,
                    "User quota is unavailable. Cannot generate LiteLLM key without max_budget."));
        }
        if (remainingBudget <= 0) {
            log.warn("Cannot generate LiteLLM key because user quota is exhausted. user={}, team={}, remaining={}",
                    userId, teamId, remainingBudget);
            return Mono.just(Result.failure(402, "User quota is exhausted."));
        }
        return regenerateApiKey(userId, teamId, remainingBudget);
    }

    private Mono<Result<?>> regenerateApiKey(String userId, String teamId, double maxBudget) {
        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("user_id", userId);
        requestBody.put("team_id", teamId);
        requestBody.put("max_budget", maxBudget);

        return litellmWebClient.post()
                .uri("/key/generate")
                .bodyValue(requestBody)
                .exchangeToMono(this::handleResponse) // 复用响应处理逻辑
                .doOnNext(result -> cleanupOtherApiKeysAfterGenerate(result, userId, teamId))
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM regenerate key. user={}, team={}", userId, teamId))
                .onErrorResume(e -> {
                    log.warn("LiteLLM regenerate key request failed. user={}, team={}", userId, teamId, e);
                    return Mono.just(Result.failure(500, "Regenerated User Api Key failed: " + e.getMessage()));
                });
    }

    /**
     * Queries LiteLLM user metadata.
     *
     * @param userId LiteLLM user id
     * @return LiteLLM result payload
     */
    public Mono<Result<?>> getUserInfo(String userId) {
        return litellmWebClient.get()
                .uri(uriBuilder -> uriBuilder.path("/user/info")
                        .queryParam("user_id", userId)
                        .build())
                .exchangeToMono(this::handleResponse) // 复用响应处理逻辑
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM get user info. user={}", userId))
                .onErrorResume(e -> {
                    log.warn("LiteLLM get user info request failed. user={}", userId, e);
                    return Mono.just(Result.failure(500, "Get User Info Request failed: " + e.getMessage()));
                });
    }

    /**
     * Returns LiteLLM user metadata, creating the user and assigning the configured
     * team when the user does not exist yet.
     *
     * @param user authenticated backend user
     * @param teamId LiteLLM team id to assign when creating the user
     * @return LiteLLM user info result
     */
    public Mono<Result<?>> getOrCreateUserInfo(AuthUser user, String teamId) {
        if (user == null || !StringUtils.hasText(user.getId())) {
            return Mono.just(Result.failure(400, "User id is required."));
        }
        if (!StringUtils.hasText(teamId)) {
            return Mono.just(Result.failure(400, "Team id is required."));
        }

        String userId = user.getId();
        return getUserInfo(userId)
                .flatMap(userInfoResult -> {
                    if (isSuccessResult(userInfoResult)) {
                        return Mono.just(userInfoResult);
                    }
                    log.info("LiteLLM user does not exist or is unavailable. Creating before quota/reward flow. user={}, team={}, result={}",
                            userId, teamId, userInfoResult);
                    return createUserAndAssignTeam(user, teamId)
                            .flatMap(createResult -> {
                                if (isSuccessResult(createResult) || isAlreadyExistsResult(createResult)) {
                                    return getUserInfo(userId);
                                }
                                return Mono.just(createResult);
                            });
                });
    }

    /**
     * Adds a budget amount to the LiteLLM user's max budget.
     *
     * @param userId LiteLLM user id
     * @param amount positive amount to add
     * @return LiteLLM update result
     */
    public Mono<Result<?>> addBudgetToUser(String userId, double amount) {
        if (!StringUtils.hasText(userId)) {
            return Mono.just(Result.failure(400, "User id is required."));
        }
        if (!Double.isFinite(amount) || amount <= 0) {
            return Mono.just(Result.failure(400, "Reward amount must be positive."));
        }

        return getUserInfo(userId)
                .flatMap(result -> {
                    if (!isSuccessResult(result) || !(result.getData() instanceof JsonNode jsonNode)) {
                        String message = result == null ? "Unknown LiteLLM user info error." : result.getMessage();
                        return Mono.just(Result.failure(502, "Cannot read current user budget: " + message));
                    }
                    Double currentBudget = findFirstNumber(jsonNode, List.of("max_budget", "budget", "user_budget"));
                    if (currentBudget == null) {
                        return Mono.just(Result.failure(502, "Current user budget is unavailable."));
                    }
                    double newBudget = currentBudget + amount;
                    Map<String, Object> requestBody = new LinkedHashMap<>();
                    requestBody.put("user_id", userId);
                    requestBody.put("max_budget", newBudget);
                    return litellmWebClient.post()
                            .uri("/user/update")
                            .bodyValue(requestBody)
                            .exchangeToMono(this::handleResponse)
                            .doOnSubscribe(subscription -> log.debug(
                                    "Calling LiteLLM update user budget. user={}, amount={}, newBudget={}",
                                    userId, amount, newBudget
                            ));
                })
                .onErrorResume(e -> {
                    log.warn("LiteLLM update user budget request failed. user={}, amount={}", userId, amount, e);
                    return Mono.just(Result.failure(500, "Update User Budget failed: " + e.getMessage()));
                });
    }

    /**
     * Queries LiteLLM team metadata.
     *
     * @param team_id LiteLLM team id
     * @return LiteLLM result payload
     */
    public Mono<Result<?>> getTeamInfo(String team_id) {
        return litellmWebClient.get()
                .uri(uriBuilder -> uriBuilder.path("/team/info")
                        .queryParam("team_id", team_id)
                        .build())
                .exchangeToMono(this::handleResponse) // 复用响应处理逻辑
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM get team info. team={}", team_id))
                .onErrorResume(e -> {
                    log.warn("LiteLLM get team info request failed. team={}", team_id, e);
                    return Mono.just(Result.failure(500, "Get Team Info Request failed: " + e.getMessage()));
                });
    }

    /**
     * Lists LiteLLM users and normalizes quota fields for the admin UI.
     *
     * @return normalized user quota rows
     */
    public Mono<List<LiteLLMUserQuota>> listUserQuotas() {
        return litellmWebClient.get()
                .uri("/user/list")
                .exchangeToMono(this::handleResponse)
                .flatMap(result -> {
                    if (!isSuccessResult(result) || !(result.getData() instanceof JsonNode jsonNode)) {
                        String message = result == null ? "Unknown LiteLLM user list error." : result.getMessage();
                        return Mono.error(new IllegalStateException(message));
                    }
                    return Mono.just(parseUserQuotas(jsonNode));
                })
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM list users for quota admin page."));
    }

    /**
     * Lists chat models allowed for a LiteLLM team.
     *
     * @param teamId LiteLLM team id
     * @return normalized model ids
     */
    public Mono<List<String>> listTeamModels(String teamId) {
        return litellmWebClient.get()
                .uri(uriBuilder -> uriBuilder.path("/team/info")
                        .queryParam("team_id", teamId)
                        .build())
                .exchangeToMono(this::handleResponse)
                .flatMap(result -> {
                    if (!isSuccessResult(result) || !(result.getData() instanceof JsonNode jsonNode)) {
                        String message = result == null ? "Unknown LiteLLM team info error." : result.getMessage();
                        return Mono.error(new IllegalStateException(message));
                    }
                    List<String> explicitModels = extractDirectAllowedModelIds(jsonNode);
                    if (!explicitModels.isEmpty()) {
                        return Mono.just(explicitModels);
                    }
                    if (directAllowedModelFieldsContainWildcard(jsonNode)) {
                        return listProxyModelInfoModels();
                    }
                    return Mono.just(List.<String>of());
                })
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM list team models. team={}", teamId));
    }

    /**
     * Queries and normalizes the quota for one LiteLLM user.
     *
     * @param userId LiteLLM user id
     * @return normalized quota row
     */
    public Mono<LiteLLMUserQuota> getUserQuota(String userId) {
        return getUserInfo(userId)
                .flatMap(result -> {
                    if (!isSuccessResult(result) || !(result.getData() instanceof JsonNode jsonNode)) {
                        String message = result == null ? "Unknown LiteLLM user info error." : result.getMessage();
                        return Mono.error(new IllegalStateException(message));
                    }
                    return Mono.just(parseUserQuota(jsonNode));
                });
    }

    /**
     * Queries the user's LiteLLM quota, creating the LiteLLM user first when
     * needed so newly registered users can see their initial default budget.
     *
     * @param user authenticated backend user
     * @param teamId LiteLLM team id
     * @return normalized quota row
     */
    public Mono<LiteLLMUserQuota> getOrCreateUserQuota(AuthUser user, String teamId) {
        return getOrCreateUserInfo(user, teamId)
                .flatMap(result -> {
                    if (!isSuccessResult(result) || !(result.getData() instanceof JsonNode jsonNode)) {
                        String message = result == null ? "Unknown LiteLLM user info error." : result.getMessage();
                        return Mono.error(new IllegalStateException(message));
                    }
                    return Mono.just(parseUserQuota(jsonNode));
                });
    }

    /**
     * Checks whether a LiteLLM virtual key still exists and can be inspected by the admin API.
     *
     * @param apiKey LiteLLM virtual key
     * @param userId expected owner user id
     * @param teamId expected team id
     * @return {@code true} when the key is accepted and belongs to the expected owner/team when those fields are present
     */
    public Mono<Boolean> isApiKeyValid(String apiKey, String userId, String teamId) {
        if (!StringUtils.hasText(apiKey)) {
            return Mono.just(false);
        }

        return litellmWebClient.get()
                .uri(uriBuilder -> uriBuilder.path("/key/info")
                        .queryParam("key", apiKey)
                        .build())
                .exchangeToMono(this::handleResponse)
                .map(result -> isKeyInfoValid(result, userId, teamId))
                .doOnSubscribe(subscription -> log.debug("Calling LiteLLM key validation. user={}, team={}",
                        userId, teamId))
                .onErrorResume(e -> {
                    log.warn("LiteLLM key validation request failed. user={}, team={}", userId, teamId, e);
                    return Mono.just(false);
                });
    }

    /**
     * Deletes previous LiteLLM virtual keys for the same user/team without blocking
     * the current key generation response.
     *
     * @param userId LiteLLM user id
     * @param teamId LiteLLM team id
     * @param currentApiKey newly generated key or token that must be kept
     * @return completion signal after best-effort cleanup
     */
    public Mono<Void> cleanupOtherApiKeysForUser(String userId, String teamId, String currentApiKey) {
        Set<String> currentKeyIdentifiers = new HashSet<>();
        return expandCurrentKeyIdentifiers(currentApiKey, currentKeyIdentifiers)
                .flatMap(expandedIdentifiers -> cleanupOtherApiKeysForUser(userId, teamId, expandedIdentifiers));
    }

    private void cleanupOtherApiKeysAfterGenerate(Result<?> result, String userId, String teamId) {
        if (result == null || result.getCode() < 200 || result.getCode() >= 300) {
            return;
        }
        Object rawData = result.getData();
        if (!(rawData instanceof JsonNode jsonNode)) {
            return;
        }

        Set<String> currentKeyIdentifiers = extractCurrentKeyIdentifiers(jsonNode);
        if (currentKeyIdentifiers.isEmpty()) {
            log.warn("Skipping LiteLLM key cleanup because generated key response has no key identifiers. user={}, team={}",
                    userId, teamId);
            return;
        }

        String currentApiKey = firstTextField(jsonNode, List.of("key", "api_key"));
        Mono<Void> cleanup = StringUtils.hasText(currentApiKey)
                ? expandCurrentKeyIdentifiers(currentApiKey, currentKeyIdentifiers)
                .flatMap(expandedIdentifiers -> cleanupOtherApiKeysForUser(userId, teamId, expandedIdentifiers))
                : cleanupOtherApiKeysForUser(userId, teamId, currentKeyIdentifiers);
        cleanup
                .subscribe(
                        unused -> {
                        },
                        error -> log.warn("Async LiteLLM old key cleanup failed. user={}, team={}",
                                userId, teamId, error)
                );
    }

    private Mono<Void> cleanupOtherApiKeysForUser(String userId, String teamId, Set<String> currentKeyIdentifiers) {
        if (!StringUtils.hasText(userId) || !StringUtils.hasText(teamId) || currentKeyIdentifiers.isEmpty()) {
            return Mono.empty();
        }

        return listApiKeysForDeletion(userId, teamId, currentKeyIdentifiers, 1, new ArrayList<>())
                .flatMap(keysToDelete -> {
                    if (keysToDelete.isEmpty()) {
                        log.debug("No old LiteLLM API keys to delete. user={}, team={}", userId, teamId);
                        return Mono.empty();
                    }
                    Map<String, Object> requestBody = new LinkedHashMap<>();
                    requestBody.put("keys", keysToDelete);
                    return litellmWebClient.post()
                            .uri("/key/delete")
                            .bodyValue(requestBody)
                            .exchangeToMono(this::handleResponse)
                            .doOnSuccess(result -> logApiKeyCleanupResult(result, userId, teamId, keysToDelete.size()))
                            .then();
                })
                .onErrorResume(e -> {
                    log.warn("LiteLLM old API key cleanup request failed. user={}, team={}", userId, teamId, e);
                    return Mono.empty();
                });
    }

    private Mono<Set<String>> expandCurrentKeyIdentifiers(String currentApiKey, Set<String> currentKeyIdentifiers) {
        if (!StringUtils.hasText(currentApiKey)) {
            return Mono.just(currentKeyIdentifiers);
        }

        return litellmWebClient.get()
                .uri(uriBuilder -> uriBuilder.path("/key/info")
                        .queryParam("key", currentApiKey)
                        .build())
                .exchangeToMono(this::handleResponse)
                .map(result -> {
                    if (result != null && result.getCode() >= 200 && result.getCode() < 300
                            && result.getData() instanceof JsonNode jsonNode) {
                        currentKeyIdentifiers.add(currentApiKey);
                        currentKeyIdentifiers.addAll(extractCurrentKeyIdentifiers(jsonNode));
                    } else {
                        log.warn("LiteLLM current key lookup returned failure before cleanup. Skipping cleanup unless generated key identifiers are available. result={}",
                                result);
                    }
                    return currentKeyIdentifiers;
                })
                .onErrorResume(e -> {
                    log.warn("LiteLLM current key lookup failed before cleanup. Skipping cleanup unless generated key identifiers are available.", e);
                    return Mono.just(currentKeyIdentifiers);
                });
    }

    private Mono<List<String>> listApiKeysForDeletion(
            String userId,
            String teamId,
            Set<String> currentKeyIdentifiers,
            int page,
            List<String> accumulator
    ) {
        return fetchApiKeyListPage(userId, teamId, page)
                .flatMap(keyListPage -> {
                    for (JsonNode keyNode : keyListPage.keys()) {
                        String keyToDelete = extractKeyForDeletion(keyNode, userId, teamId, currentKeyIdentifiers);
                        if (StringUtils.hasText(keyToDelete) && !accumulator.contains(keyToDelete)) {
                            accumulator.add(keyToDelete);
                        }
                    }
                    if (page < keyListPage.totalPages()) {
                        return listApiKeysForDeletion(userId, teamId, currentKeyIdentifiers, page + 1, accumulator);
                    }
                    return Mono.just(accumulator);
                });
    }

    private Mono<KeyListPage> fetchApiKeyListPage(String userId, String teamId, int page) {
        return litellmWebClient.get()
                .uri(uriBuilder -> uriBuilder.path("/key/list")
                        .queryParam("user_id", userId)
                        .queryParam("team_id", teamId)
                        .queryParam("page", page)
                        .queryParam("size", 100)
                        .queryParam("return_full_object", true)
                        .build())
                .exchangeToMono(this::handleResponse)
                .map(result -> parseKeyListPage(result, page));
    }

    private KeyListPage parseKeyListPage(Result<?> result, int page) {
        if (result == null || result.getCode() < 200 || result.getCode() >= 300) {
            return new KeyListPage(List.of(), page);
        }
        Object rawData = result.getData();
        if (!(rawData instanceof JsonNode jsonNode)) {
            return new KeyListPage(List.of(), page);
        }

        List<JsonNode> keys = new ArrayList<>();
        collectArrayItems(keys, jsonNode.get("keys"));
        collectArrayItems(keys, jsonNode.get("data"));

        int totalPages = jsonNode.path("total_pages").asInt(page);
        return new KeyListPage(keys, Math.max(page, totalPages));
    }

    private Set<String> extractCurrentKeyIdentifiers(JsonNode jsonNode) {
        Set<String> identifiers = new HashSet<>();
        addKeyIdentifierFields(identifiers, jsonNode);
        JsonNode infoNode = jsonNode.get("info");
        if (infoNode != null && infoNode.isObject()) {
            addKeyIdentifierFields(identifiers, infoNode);
        }
        return identifiers;
    }

    private void addKeyIdentifierFields(Set<String> identifiers, JsonNode jsonNode) {
        addTextField(identifiers, jsonNode, "key");
        addTextField(identifiers, jsonNode, "token");
        addTextField(identifiers, jsonNode, "api_key");
        addTextField(identifiers, jsonNode, "token_id");
        addTextField(identifiers, jsonNode, "key_alias");
        addTextField(identifiers, jsonNode, "key_name");
    }

    private String extractKeyForDeletion(
            JsonNode keyNode,
            String userId,
            String teamId,
            Set<String> currentKeyIdentifiers
    ) {
        if (keyNode == null || keyNode.isNull()) {
            return null;
        }
        if (keyNode.isTextual()) {
            String key = keyNode.asString();
            return currentKeyIdentifiers.contains(key) ? null : key;
        }

        String keyUserId = keyNode.path("user_id").asString(null);
        if (StringUtils.hasText(keyUserId) && !keyUserId.equals(userId)) {
            return null;
        }

        String keyTeamId = keyNode.path("team_id").asString(null);
        if (StringUtils.hasText(keyTeamId) && !keyTeamId.equals(teamId)) {
            return null;
        }

        if (hasCurrentKeyIdentifier(keyNode, currentKeyIdentifiers)) {
            return null;
        }

        String key = firstTextField(keyNode, List.of("token_id", "token", "key", "api_key", "key_alias", "key_name"));
        if (!StringUtils.hasText(key)) {
            return null;
        }
        return key;
    }

    private boolean hasCurrentKeyIdentifier(JsonNode keyNode, Set<String> currentKeyIdentifiers) {
        for (String fieldName : List.of("token_id", "token", "key", "api_key", "key_alias", "key_name")) {
            String value = keyNode.path(fieldName).asString(null);
            if (StringUtils.hasText(value) && currentKeyIdentifiers.contains(value)) {
                return true;
            }
        }
        return false;
    }

    private void collectArrayItems(List<JsonNode> items, JsonNode arrayNode) {
        if (arrayNode == null || !arrayNode.isArray()) {
            return;
        }
        for (JsonNode item : arrayNode) {
            items.add(item);
        }
    }

    private List<LiteLLMUserQuota> parseUserQuotas(JsonNode payload) {
        List<JsonNode> userNodes = new ArrayList<>();
        collectUserListNodes(userNodes, payload);

        List<LiteLLMUserQuota> quotas = new ArrayList<>();
        for (JsonNode userNode : userNodes) {
            quotas.add(parseUserQuota(userNode));
        }
        return quotas;
    }

    private LiteLLMUserQuota parseUserQuota(JsonNode userNode) {
        LiteLLMUserQuota quota = new LiteLLMUserQuota();
        quota.setUserId(firstTextField(userNode, List.of("user_id", "id")));
        quota.setUserEmail(firstTextField(userNode, List.of("user_email", "email")));
        quota.setUserAlias(firstTextField(userNode, List.of("user_alias", "alias", "name")));
        quota.setSpend(findFirstNumber(userNode, List.of("spend", "user_spend")));
        quota.setBudget(findFirstNumber(userNode, List.of("max_budget", "budget", "user_budget")));
        if (quota.getSpend() != null && quota.getBudget() != null) {
            quota.setRemaining(quota.getBudget() - quota.getSpend());
        }
        quota.setTeams(extractTeams(userNode));
        quota.setCreatedAt(firstTextField(userNode, List.of("created_at", "created_time", "created")));
        quota.setUpdatedAt(firstTextField(userNode, List.of("updated_at", "updated_time", "updated")));
        return quota;
    }

    private Mono<List<String>> listProxyModelInfoModels() {
        return litellmWebClient.get()
                .uri("/model/info")
                .exchangeToMono(this::handleResponse)
                .flatMap(result -> {
                    if (!isSuccessResult(result) || !(result.getData() instanceof JsonNode jsonNode)) {
                        String message = result == null ? "Unknown LiteLLM model info error." : result.getMessage();
                        return Mono.error(new IllegalStateException(message));
                    }
                    return Mono.just(extractLlmModelIds(jsonNode));
                });
    }

    private List<String> extractDirectAllowedModelIds(JsonNode payload) {
        List<String> rawModels = new ArrayList<>();
        for (JsonNode candidate : wrappedObjectCandidates(payload, List.of("team_info", "team", "data"))) {
            collectDirectModelFieldValues(rawModels, candidate);
            if (!rawModels.isEmpty()) {
                return normalizeModelIds(rawModels);
            }
        }
        return List.of();
    }

    private boolean directAllowedModelFieldsContainWildcard(JsonNode payload) {
        List<String> rawModels = new ArrayList<>();
        for (JsonNode candidate : wrappedObjectCandidates(payload, List.of("team_info", "team", "data"))) {
            collectDirectModelFieldValues(rawModels, candidate);
            for (String modelId : rawModels) {
                if (isModelWildcard(modelId)) {
                    return true;
                }
            }
            rawModels.clear();
        }
        return false;
    }

    private List<JsonNode> wrappedObjectCandidates(JsonNode payload, List<String> wrapperKeys) {
        if (payload == null || !payload.isObject()) {
            return List.of();
        }
        List<JsonNode> candidates = new ArrayList<>();
        candidates.add(payload);
        for (String key : wrapperKeys) {
            JsonNode child = payload.get(key);
            if (child != null && child.isObject()) {
                candidates.add(child);
            }
        }
        return candidates;
    }

    private void collectDirectModelFieldValues(List<String> rawModels, JsonNode payload) {
        for (String key : List.of("models", "allowed_models", "model_names", "team_models")) {
            JsonNode value = payload.get(key);
            if (value == null || !value.isArray()) {
                continue;
            }
            for (JsonNode item : value) {
                String modelId = extractModelId(item);
                if (StringUtils.hasText(modelId)) {
                    rawModels.add(modelId);
                }
            }
        }
    }

    private List<String> extractLlmModelIds(JsonNode payload) {
        JsonNode data = payload != null && payload.isObject() && payload.get("data") != null
                ? payload.get("data")
                : payload;
        if (data == null || !data.isArray()) {
            return List.of();
        }
        List<String> rawModels = new ArrayList<>();
        for (JsonNode item : data) {
            String modelId = extractModelId(item);
            if (StringUtils.hasText(modelId) && !isEmbeddingOrNonLlmModel(item, modelId)) {
                rawModels.add(modelId);
            }
        }
        return normalizeModelIds(rawModels);
    }

    private List<String> normalizeModelIds(List<String> rawModels) {
        List<String> models = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (String rawModel : rawModels) {
            if (!StringUtils.hasText(rawModel)) {
                continue;
            }
            String modelId = rawModel.trim();
            if (isModelWildcard(modelId) || !seen.add(modelId)) {
                continue;
            }
            models.add(modelId);
        }
        return models;
    }

    private String extractModelId(JsonNode item) {
        if (item == null || item.isNull()) {
            return "";
        }
        if (item.isTextual()) {
            String value = item.asString(null);
            return StringUtils.hasText(value) ? value.trim() : "";
        }
        if (!item.isObject()) {
            return "";
        }
        return firstTextField(item, List.of("id", "model_name", "model"));
    }

    private boolean isModelWildcard(String modelId) {
        return StringUtils.hasText(modelId) && MODEL_WILDCARDS.contains(modelId.trim().toLowerCase());
    }

    private boolean isEmbeddingOrNonLlmModel(JsonNode item, String modelId) {
        List<String> modes = new ArrayList<>();
        collectModeValues(modes, item);
        for (String mode : modes) {
            if (Set.of("embedding", "embeddings", "rerank", "image", "audio").contains(mode)) {
                return true;
            }
        }
        String lowered = modelId.toLowerCase();
        for (String keyword : List.of("embedding", "embeddings", "embed", "rerank", "bge-reranker", "jina-embeddings")) {
            if (lowered.contains(keyword)) {
                return true;
            }
        }
        return false;
    }

    private void collectModeValues(List<String> modes, JsonNode item) {
        if (item == null || !item.isObject()) {
            return;
        }
        for (String key : List.of("mode", "model_type", "type")) {
            String value = item.path(key).asString(null);
            if (StringUtils.hasText(value)) {
                modes.add(value.toLowerCase());
            }
        }
        for (String nestedKey : List.of("litellm_params", "model_info", "metadata")) {
            JsonNode nested = item.get(nestedKey);
            if (nested == null || !nested.isObject()) {
                continue;
            }
            for (String key : List.of("mode", "model_type", "type")) {
                String value = nested.path(key).asString(null);
                if (StringUtils.hasText(value)) {
                    modes.add(value.toLowerCase());
                }
            }
        }
    }

    private void collectUserListNodes(List<JsonNode> userNodes, JsonNode payload) {
        if (payload == null || payload.isNull()) {
            return;
        }
        if (payload.isArray()) {
            collectArrayItems(userNodes, payload);
            return;
        }
        for (String fieldName : List.of("users", "data", "results", "items")) {
            JsonNode arrayNode = payload.get(fieldName);
            if (arrayNode != null && arrayNode.isArray()) {
                collectArrayItems(userNodes, arrayNode);
                return;
            }
        }
    }

    private List<String> extractTeams(JsonNode userNode) {
        JsonNode teamsNode = userNode.get("teams");
        if (teamsNode == null || teamsNode.isNull()) {
            return List.of();
        }
        List<String> teams = new ArrayList<>();
        if (teamsNode.isArray()) {
            for (JsonNode teamNode : teamsNode) {
                String teamId = teamNode.isObject()
                        ? firstTextField(teamNode, List.of("team_id", "id", "team_alias", "name"))
                        : teamNode.asString(null);
                if (StringUtils.hasText(teamId)) {
                    teams.add(teamId);
                }
            }
            return teams;
        }
        String singleTeam = teamsNode.asString(null);
        return StringUtils.hasText(singleTeam) ? List.of(singleTeam) : List.of();
    }

    private void logApiKeyCleanupResult(Result<?> result, String userId, String teamId, int requestedCount) {
        if (result == null || result.getCode() < 200 || result.getCode() >= 300) {
            log.warn("LiteLLM old API key cleanup returned failure. user={}, team={}, requested={}, result={}",
                    userId, teamId, requestedCount, result);
            return;
        }
        log.info("Submitted old LiteLLM API key cleanup. user={}, team={}, requested={}",
                userId, teamId, requestedCount);
    }

    private void addTextField(Set<String> values, JsonNode node, String fieldName) {
        String value = node.path(fieldName).asString(null);
        if (StringUtils.hasText(value)) {
            values.add(value);
        }
    }

    private String firstTextField(JsonNode node, List<String> fieldNames) {
        for (String fieldName : fieldNames) {
            String value = node.path(fieldName).asString(null);
            if (StringUtils.hasText(value)) {
                return value;
            }
        }
        return null;
    }

    private boolean isKeyInfoValid(Result<?> result, String userId, String teamId) {
        if (result == null || result.getCode() < 200 || result.getCode() >= 300) {
            return false;
        }

        Object rawData = result.getData();
        if (!(rawData instanceof JsonNode jsonNode)) {
            return false;
        }

        String keyUserId = jsonNode.path("user_id").asString(null);
        if (StringUtils.hasText(keyUserId) && !keyUserId.equals(userId)) {
            return false;
        }

        String keyTeamId = jsonNode.path("team_id").asString(null);
        if (StringUtils.hasText(keyTeamId) && !keyTeamId.equals(teamId)) {
            return false;
        }

        if (jsonNode.has("blocked") && jsonNode.get("blocked").asBoolean(false)) {
            return false;
        }

        Double keyBudget = findFirstNumber(jsonNode, List.of("max_budget", "budget"));
        return keyBudget != null && keyBudget > 0;
    }

    private boolean isSuccessResult(Result<?> result) {
        return result != null && result.getCode() >= 200 && result.getCode() < 300;
    }

    private boolean isAlreadyExistsResult(Result<?> result) {
        if (result == null) {
            return false;
        }
        String message = result.getMessage();
        return result.getCode() == 409
                || (StringUtils.hasText(message) && message.toLowerCase().contains("already"));
    }

    private Double extractRemainingBudget(Result<?> userInfoResult) {
        if (userInfoResult == null || userInfoResult.getCode() < 200 || userInfoResult.getCode() >= 300) {
            return null;
        }
        Object rawData = userInfoResult.getData();
        if (!(rawData instanceof JsonNode jsonNode)) {
            return null;
        }

        Double spend = findFirstNumber(jsonNode, List.of("spend", "user_spend"));
        Double budget = findFirstNumber(jsonNode, List.of("max_budget", "budget", "user_budget"));
        if (spend == null || budget == null) {
            return null;
        }
        return budget - spend;
    }

    private Double findFirstNumber(JsonNode node, List<String> keys) {
        if (node == null || node.isNull()) {
            return null;
        }

        for (String key : keys) {
            JsonNode value = node.get(key);
            Double parsed = parseNumber(value);
            if (parsed != null) {
                return parsed;
            }
        }

        for (JsonNode child : node) {
            Double parsed = findFirstNumber(child, keys);
            if (parsed != null) {
                return parsed;
            }
        }
        return null;
    }

    private Double parseNumber(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }

        String raw = value.asString(null);
        if (!StringUtils.hasText(raw)) {
            return null;
        }

        try {
            double parsed = Double.parseDouble(raw);
            return Double.isFinite(parsed) ? parsed : null;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /**
     * 统一处理 WebClient 响应数据的公共方法
     *
     * @param response LiteLLM HTTP response
     * @return normalized result wrapper
     */
    private Mono<Result<?>> handleResponse(ClientResponse response) {
        return response.bodyToMono(JsonNode.class)
                .map(jsonNode -> {
                    if (jsonNode.has("error") || response.statusCode().isError()) {
                        JsonNode errorNode = jsonNode.has("error") ? jsonNode.get("error") : jsonNode;
                        String message = errorNode.path("message").asString("Unknown error");
                        int code = errorNode.path("code").asInt(response.statusCode().value());
                        log.warn("LiteLLM returned error response. status={}, code={}, message={}",
                                response.statusCode(), code, message);
                        return Result.failure(code, message);
                    }
                    return Result.success(jsonNode);
                })
                .defaultIfEmpty(Result.failure(response.statusCode().value(), "No response body"));
    }

    private record KeyListPage(List<JsonNode> keys, int totalPages) {
    }
}
