package com.dadastory.omni_ai_router.entity;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.List;

/**
 * Request/response model for LiteLLM team metadata.
 */
@Data
public class LiteLLMTeam {

    @JsonProperty("team_alias")
    private String teamAlias;                      // 团队别名（建议必填）

    @JsonProperty("team_id")
    private String teamId;                         // 团队ID（可选，不传则自动生成）

    @JsonProperty("members_with_roles")
    private List<MemberWithRole> membersWithRoles; // 成员及角色（admin/user）

    @JsonProperty("max_budget")
    private Double maxBudget;                      // 团队总预算上限

    @JsonProperty("budget_duration")
    private String budgetDuration;                 // 预算重置周期，如 "1d", "1mo"

    @JsonProperty("rpm_limit")
    private Integer rpmLimit;                      // 团队总 RPM 限制

    @JsonProperty("tpm_limit")
    private Integer tpmLimit;                      // 团队总 TPM 限制

    private List<String> models;                   // 允许使用的模型列表（空=所有模型）

    private Boolean blocked;                       // 是否禁用团队

    private List<String> tags;                     // 标签（可选，用于追踪/路由）

    /**
     * LiteLLM team member with a role assignment.
     */
    @Data
    public static class MemberWithRole {
        private String role;
        @JsonProperty("user_id")
        private String userId;

        /**
         * Creates a team member role mapping.
         *
         * @param role LiteLLM role name
         * @param userId LiteLLM user id
         */
        public MemberWithRole(String role, String userId) {
            this.role = role;
            this.userId = userId;
        }
    }
}
