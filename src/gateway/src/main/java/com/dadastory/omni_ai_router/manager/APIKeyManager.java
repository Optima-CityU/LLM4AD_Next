package com.dadastory.omni_ai_router.manager;

import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.security.crypto.encrypt.TextEncryptor;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.List;

/**
 * Manages encrypted LiteLLM API keys stored in Redis.
 */
@Service
@Slf4j
public class APIKeyManager {

    private static final String KEY_PREFIX = "LITELLM_APIKEY:v3:";
    private static final String LOCK_PREFIX = "LITELLM_APIKEY_LOCK:";
    private static final RedisScript<Long> RELEASE_LOCK_SCRIPT = RedisScript.of("""
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            else
                return 0
            end
            """, Long.class);

    @Resource
    private ReactiveStringRedisTemplate reactiveStringRedisTemplate;

    @Resource
    private TextEncryptor textEncryptor;


    @Value("${LITELLM_API_KEY_TTL:86400}")
    private Long validTTL;

    @Value("${LITELLM_API_KEY_LOCK_TTL:30}")
    private Long lockTTL;

    /**
     * Builds the Redis key used for one team/user LiteLLM API key.
     *
     * @param teamId LiteLLM team id
     * @param userId authenticated user id
     * @return Redis key
     */
    private String buildKey(String teamId, String userId) {
        return KEY_PREFIX + teamId + ":" + userId;
    }

    private String buildLockKey(String teamId, String userId) {
        return LOCK_PREFIX + teamId + ":" + userId;
    }

    /**
     * Encrypts and stores an API key in Redis.
     *
     * @param teamId team id bound to the key
     * @param userId user id bound to the key
     * @param rawApiKey raw LiteLLM API key
     * @return Redis write result
     */
    public Mono<Boolean> saveApiKey(String teamId, String userId, String rawApiKey) {
        String key = buildKey(teamId, userId);
        return reactiveStringRedisTemplate.opsForValue()
                .set(key, textEncryptor.encrypt(rawApiKey), Duration.ofSeconds(validTTL))
                .doOnSuccess(result -> log.debug("LiteLLM API Key cached. user={}, team={}, ttl={}",
                        userId, teamId, validTTL));
    }

    /**
     * Loads and decrypts a cached API key from Redis.
     *
     * @param teamId team id bound to the key
     * @param userId user id bound to the key
     * @return decrypted API key when present and valid
     */
    public Mono<String> getApiKey(String teamId, String userId) {
        String key = buildKey(teamId, userId);
        return reactiveStringRedisTemplate.opsForValue()
                .get(key)
                .flatMap(encryptedApiKey -> {
                    if (!isHex(encryptedApiKey)) {
                        log.warn("Cached LiteLLM API Key is not encrypted hex text. user={}, team={}", userId, teamId);
                        return deleteInvalidApiKey(key, teamId, userId);
                    }
                    try {
                        return Mono.just(textEncryptor.decrypt(encryptedApiKey));
                    } catch (Exception e) {
                        log.warn("Failed to decrypt cached LiteLLM API Key. user={}, team={}, reason={}",
                                userId, teamId, e.getMessage());
                        return deleteInvalidApiKey(key, teamId, userId);
                    }
                }).filter(StringUtils::hasText);
    }

    /**
     * Acquires a short-lived Redis lock for LiteLLM key generation.
     *
     * @param teamId team id bound to the generated key
     * @param userId user id bound to the generated key
     * @param lockToken unique owner token used for safe release
     * @return {@code true} when the caller owns the lock
     */
    public Mono<Boolean> acquireApiKeyGenerationLock(String teamId, String userId, String lockToken) {
        if (!StringUtils.hasText(lockToken)) {
            return Mono.just(false);
        }
        String key = buildLockKey(teamId, userId);
        return reactiveStringRedisTemplate.opsForValue()
                .setIfAbsent(key, lockToken, Duration.ofSeconds(lockTTL))
                .map(Boolean.TRUE::equals)
                .doOnSuccess(acquired -> log.debug("LiteLLM API Key generation lock acquire result. user={}, team={}, acquired={}",
                        userId, teamId, acquired));
    }

    /**
     * Releases a Redis generation lock only when the caller still owns it.
     *
     * @param teamId team id bound to the generated key
     * @param userId user id bound to the generated key
     * @param lockToken unique owner token used when acquiring the lock
     * @return {@code true} when the lock key was removed
     */
    public Mono<Boolean> releaseApiKeyGenerationLock(String teamId, String userId, String lockToken) {
        if (!StringUtils.hasText(lockToken)) {
            return Mono.just(false);
        }
        String key = buildLockKey(teamId, userId);
        return reactiveStringRedisTemplate.execute(RELEASE_LOCK_SCRIPT, List.of(key), List.of(lockToken))
                .next()
                .defaultIfEmpty(0L)
                .map(deleted -> deleted > 0)
                .doOnSuccess(released -> log.debug("LiteLLM API Key generation lock release result. user={}, team={}, released={}",
                        userId, teamId, released));
    }

    private Mono<String> deleteInvalidApiKey(String key, String teamId, String userId) {
        return reactiveStringRedisTemplate.delete(key)
                .doOnSuccess(count -> log.info("Invalid LiteLLM API Key cache deleted. user={}, team={}, count={}",
                        userId, teamId, count))
                .then(Mono.empty());
    }

    private boolean isHex(String value) {
        if (!StringUtils.hasText(value) || value.length() % 2 != 0) {
            return false;
        }
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
                return false;
            }
        }
        return true;
    }

    /**
     * Deletes a cached API key from Redis.
     *
     * @param teamId team id bound to the key
     * @param userId user id bound to the key
     * @return number of removed Redis keys
     */
    public Mono<Long> deleteApiKey(String teamId, String userId) {
        String key = buildKey(teamId, userId);
        return reactiveStringRedisTemplate.delete(key)
                .doOnSuccess(count -> log.debug("LiteLLM API Key cache deleted. user={}, team={}, count={}",
                        userId, teamId, count));
    }
}
