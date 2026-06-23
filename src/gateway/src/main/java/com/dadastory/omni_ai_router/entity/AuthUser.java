package com.dadastory.omni_ai_router.entity;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

/**
 * User profile returned by the backend auth service.
 */
@Data
public class AuthUser {

    private String email;

    @JsonProperty("is_active")
    private boolean active;

    @JsonProperty("is_superuser")
    private boolean superuser;

    @JsonProperty("email_verified")
    private boolean emailVerified;

    @JsonProperty("full_name")
    private String fullName;

    @JsonProperty("privacy_policy_accepted")
    private boolean privacyPolicyAccepted;

    @JsonProperty("privacy_policy_accepted_at")
    private String privacyPolicyAcceptedAt;

    private String id;

    @JsonProperty("created_at")
    private String createdAt;
}
