package com.dadastory.omni_ai_router.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configuration properties for the LiteLLM service connection.
 */
@ConfigurationProperties(prefix = "litellm")
@Data
public class LiteLLMProperties {
    private String baseUrl;
    private String authToken;
}
