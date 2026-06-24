package com.dadastory.omni_ai_router.controller;

import com.dadastory.omni_ai_router.dto.LiteLLMUserQuota;
import com.dadastory.omni_ai_router.entity.AuthUser;
import com.dadastory.omni_ai_router.manager.AuthUserManager;
import com.dadastory.omni_ai_router.manager.LiteLLMManager;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class LiteLLMAdminControllerTest {

    @Test
    void rejectsMissingBearerToken() {
        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);
        LiteLLMAdminController controller = new LiteLLMAdminController(authUserManager, liteLLMManager);

        StepVerifier.create(controller.listUserQuotas(null))
                .expectNextMatches(response -> response.getStatusCode() == HttpStatus.UNAUTHORIZED)
                .verifyComplete();

        verifyNoInteractions(authUserManager, liteLLMManager);
    }

    @Test
    void rejectsNonAdminUser() {
        AuthUser user = new AuthUser();
        user.setId("user-1");
        user.setEmail("user@example.com");
        user.setActive(true);
        user.setSuperuser(false);

        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);
        when(authUserManager.getCurrentUser("normal-token")).thenReturn(Mono.just(user));
        LiteLLMAdminController controller = new LiteLLMAdminController(authUserManager, liteLLMManager);

        StepVerifier.create(controller.listUserQuotas("Bearer normal-token"))
                .expectNextMatches(response -> response.getStatusCode() == HttpStatus.FORBIDDEN)
                .verifyComplete();

        verifyNoInteractions(liteLLMManager);
    }

    @Test
    void returnsLiteLlmUserQuotasForAdmin() {
        AuthUser admin = new AuthUser();
        admin.setId("admin-1");
        admin.setEmail("admin@example.com");
        admin.setActive(true);
        admin.setSuperuser(true);

        LiteLLMUserQuota quota = new LiteLLMUserQuota();
        quota.setUserId("user-1");
        quota.setUserEmail("user@example.com");
        quota.setSpend(2.5);
        quota.setBudget(10.0);
        quota.setRemaining(7.5);

        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);
        when(authUserManager.getCurrentUser("admin-token")).thenReturn(Mono.just(admin));
        when(liteLLMManager.listUserQuotas()).thenReturn(Mono.just(List.of(quota)));
        LiteLLMAdminController controller = new LiteLLMAdminController(authUserManager, liteLLMManager);

        StepVerifier.create(controller.listUserQuotas("Bearer admin-token"))
                .assertNext(response -> {
                    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
                    assertThat(response.getBody()).isNotNull();
                    assertThat(response.getBody().getData()).containsExactly(quota);
                })
                .verifyComplete();
    }

    @Test
    void returnsTeamModelsForAuthenticatedUser() {
        AuthUser user = new AuthUser();
        user.setId("user-1");
        user.setEmail("user@example.com");
        user.setActive(true);

        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);
        when(authUserManager.getCurrentUser("user-token")).thenReturn(Mono.just(user));
        when(liteLLMManager.listTeamModels("team-1")).thenReturn(Mono.just(List.of("gpt-4o")));
        LiteLLMAdminController controller = new LiteLLMAdminController(authUserManager, liteLLMManager);
        ReflectionTestUtils.setField(controller, "teamId", "team-1");

        StepVerifier.create(controller.listTeamModels("Bearer user-token"))
                .assertNext(response -> {
                    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
                    assertThat(response.getBody()).isNotNull();
                    assertThat(response.getBody().getData()).containsExactly("gpt-4o");
                })
                .verifyComplete();
    }

    @Test
    void returnsCurrentUserQuotaForAuthenticatedUser() {
        AuthUser user = new AuthUser();
        user.setId("user-1");
        user.setEmail("user@example.com");
        user.setActive(true);

        LiteLLMUserQuota quota = new LiteLLMUserQuota();
        quota.setUserId("user-1");
        quota.setUserEmail("user@example.com");
        quota.setSpend(2.0);
        quota.setBudget(5.0);
        quota.setRemaining(3.0);

        AuthUserManager authUserManager = mock(AuthUserManager.class);
        LiteLLMManager liteLLMManager = mock(LiteLLMManager.class);
        when(authUserManager.getCurrentUser("user-token")).thenReturn(Mono.just(user));
        when(liteLLMManager.getUserQuota("user-1")).thenReturn(Mono.just(quota));
        LiteLLMAdminController controller = new LiteLLMAdminController(authUserManager, liteLLMManager);

        StepVerifier.create(controller.getCurrentUserQuota("Bearer user-token"))
                .assertNext(response -> {
                    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
                    assertThat(response.getBody()).isNotNull();
                    assertThat(response.getBody().getData()).isEqualTo(quota);
                })
                .verifyComplete();
    }
}
