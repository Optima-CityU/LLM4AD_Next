package com.dadastory.omni_ai_router.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.List;

/**
 * Normalized LiteLLM user quota row returned by gateway admin APIs.
 */
@Data
public class LiteLLMUserQuota {

    @JsonProperty("user_id")
    private String userId;

    @JsonProperty("user_email")
    private String userEmail;

    @JsonProperty("user_alias")
    private String userAlias;

    private Double spend;

    private Double budget;

    private Double remaining;

    private List<String> teams = List.of();

    @JsonProperty("created_at")
    private String createdAt;

    @JsonProperty("updated_at")
    private String updatedAt;
}
