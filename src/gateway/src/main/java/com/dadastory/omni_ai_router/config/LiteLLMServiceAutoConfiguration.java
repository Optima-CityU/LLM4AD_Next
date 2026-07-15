package com.dadastory.omni_ai_router.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Auto-configuration for the LiteLLM administrative WebClient.
 */
@Configuration
@EnableConfigurationProperties(LiteLLMProperties.class)
public class LiteLLMServiceAutoConfiguration {

    /**
     * Maximum in-memory buffer size (bytes) for decoding LiteLLM responses.
     *
     * <p>LiteLLM's {@code /user/info} embeds the caller's full team payload
     * (all API keys and members). On a large shared team this response can grow
     * past the WebClient default of 256 KiB, which triggers a
     * {@code DataBufferLimitException} and surfaces as a 500/502 to callers.
     * Raise the limit to 16 MiB so quota lookups keep working as the team grows.
     */
    private static final int MAX_IN_MEMORY_SIZE = 16 * 1024 * 1024;

    /**
     * Creates a WebClient preconfigured with the LiteLLM base URL and admin token.
     *
     * @param properties LiteLLM connection properties
     * @return configured LiteLLM WebClient
     */
    @Bean
    public WebClient litellmWebClient(LiteLLMProperties properties) {
        ExchangeStrategies strategies = ExchangeStrategies.builder()
                .codecs(configurer -> configurer.defaultCodecs().maxInMemorySize(MAX_IN_MEMORY_SIZE))
                .build();
        return WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "Bearer " + properties.getAuthToken())
                .defaultHeader("Content-Type", "application/json")
                .exchangeStrategies(strategies)
                .build();
    }

}
