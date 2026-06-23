package com.dadastory.omni_ai_router.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Auto-configuration for the LiteLLM administrative WebClient.
 */
@Configuration
@EnableConfigurationProperties(LiteLLMProperties.class)
public class LiteLLMServiceAutoConfiguration {

    /**
     * Creates a WebClient preconfigured with the LiteLLM base URL and admin token.
     *
     * @param properties LiteLLM connection properties
     * @return configured LiteLLM WebClient
     */
    @Bean
    public WebClient litellmWebClient(LiteLLMProperties properties) {
        return WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "Bearer " + properties.getAuthToken())
                .defaultHeader("Content-Type", "application/json")
                .build();
    }

}
