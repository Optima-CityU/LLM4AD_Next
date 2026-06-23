package com.dadastory.omni_ai_router.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.encrypt.Encryptors;
import org.springframework.security.crypto.encrypt.TextEncryptor;

/**
 * Provides encryption support for sensitive gateway values stored outside memory.
 */
@Configuration
public class CryptoConfig {
    @Value("${GATEWAY_SECURITY_PASSWORD:default-password}")
    private String password;
    @Value("${GATEWAY_SECURITY_SALT:a7e92f8d3c1b6a4e5f8d7c9b0a1d2e3f}")
    private String salt;

    /**
     * Creates the text encryptor used to encrypt cached LiteLLM API keys.
     *
     * @return configured text encryptor
     */
    @Bean
    public TextEncryptor textEncryptor() {
        return Encryptors.text(password, salt);
    }
}
