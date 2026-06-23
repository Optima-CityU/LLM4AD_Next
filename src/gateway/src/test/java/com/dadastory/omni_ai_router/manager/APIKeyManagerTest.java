package com.dadastory.omni_ai_router.manager;

import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;

class APIKeyManagerTest {

    @Test
    void redisKeyUsesVersionedPrefixToAvoidOldScopedKeys() {
        APIKeyManager manager = new APIKeyManager();

        String key = ReflectionTestUtils.invokeMethod(manager, "buildKey", "team-1", "user-1");

        assertThat(key).isEqualTo("LITELLM_APIKEY:v3:team-1:user-1");
    }
}
