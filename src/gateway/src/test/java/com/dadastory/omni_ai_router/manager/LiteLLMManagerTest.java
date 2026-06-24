package com.dadastory.omni_ai_router.manager;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.codec.HttpMessageWriter;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.mock.http.client.reactive.MockClientHttpRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.BodyInserter;
import org.springframework.web.reactive.function.client.ClientRequest;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.ExchangeFunction;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class LiteLLMManagerTest {

    @Test
    void regenerateApiKeyDoesNotScopeModelsFromTeamInfo() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            String path = request.url().getPath();
            if (request.method() == HttpMethod.POST && path.equals("/key/generate")) {
                return Mono.just(jsonResponse("""
                        {
                          "key": "new-raw-key",
                          "token": "new-token-hash",
                          "user_id": "user-1",
                          "team_id": "team-1"
                        }
                        """));
            }
            if (request.method() == HttpMethod.GET && path.equals("/key/list")) {
                return Mono.just(jsonResponse("{\"keys\":[],\"total_pages\":1}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.regenerateApiKeyWithinUserBudget(
                        successResult("""
                                {"spend": 1, "max_budget": 10}
                                """),
                        "user-1",
                        "team-1"))
                .expectNextMatches(result -> result.getCode() == 200)
                .verifyComplete();

        assertThat(requests.get(0).path()).isEqualTo("/key/generate");
        assertThat(requests.get(0).body())
                .contains("\"user_id\":\"user-1\"")
                .contains("\"team_id\":\"team-1\"")
                .contains("\"max_budget\":9.0")
                .doesNotContain("\"models\"");
    }

    @Test
    void regenerateApiKeyDoesNotScopeLiteLlmWildcardFromTeamInfo() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            if (request.method() == HttpMethod.POST && request.url().getPath().equals("/key/generate")) {
                return Mono.just(jsonResponse("""
                        {"key":"new-raw-key","token":"new-token-hash"}
                        """));
            }
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/key/info")) {
                return Mono.just(jsonResponse("""
                        {"key":"new-raw-key","token":"new-token-hash","token_id":"new-token-id"}
                        """));
            }
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/key/list")) {
                return Mono.just(jsonResponse("{\"keys\":[],\"total_pages\":1}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.regenerateApiKeyWithinUserBudget(
                        successResult("""
                                {"spend": 0, "max_budget": 10}
                                """),
                        "user-1",
                        "team-1"))
                .expectNextMatches(result -> result.getCode() == 200)
                .verifyComplete();

        assertThat(requests.get(0).body()).doesNotContain("\"models\"");
    }

    @Test
    void regenerateApiKeyOmitsModelsWhenTeamInfoHasNoModels() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            if (request.method() == HttpMethod.POST && request.url().getPath().equals("/key/generate")) {
                return Mono.just(jsonResponse("""
                        {"key":"new-raw-key","token":"new-token-hash"}
                        """));
            }
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/key/list")) {
                return Mono.just(jsonResponse("{\"keys\":[],\"total_pages\":1}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.regenerateApiKeyWithinUserBudget(
                        successResult("""
                                {"spend": 0, "max_budget": 10}
                                """),
                        "user-1",
                        "team-1"))
                .expectNextMatches(result -> result.getCode() == 200)
                .verifyComplete();

        assertThat(requests.get(0).body()).doesNotContain("\"models\"");
    }

    @Test
    void cleanupOtherApiKeysDeletesOnlyOtherKeysForUserAndTeam() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            String path = request.url().getPath();
            if (request.method() == HttpMethod.GET && path.equals("/key/info")) {
                return Mono.just(jsonResponse("""
                        {
                          "key": "new-raw-key",
                          "token": "new-token-hash",
                          "user_id": "user-1",
                          "team_id": "team-1"
                        }
                        """));
            }
            if (request.method() == HttpMethod.GET && path.equals("/key/list")) {
                return Mono.just(jsonResponse("""
                        {
                          "keys": [
                            {"token": "old-token-hash-1", "user_id": "user-1", "team_id": "team-1"},
                            {"token": "new-token-hash", "user_id": "user-1", "team_id": "team-1"},
                            {"token": "other-team-token", "user_id": "user-1", "team_id": "team-2"},
                            {"token": "old-token-hash-2", "user_id": "user-1", "team_id": "team-1"}
                          ],
                          "total_count": 4
                        }
                        """));
            }
            if (request.method() == HttpMethod.POST && path.equals("/key/delete")) {
                return Mono.just(jsonResponse("{\"deleted_keys\":[\"old-token-hash-1\",\"old-token-hash-2\"]}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.cleanupOtherApiKeysForUser("user-1", "team-1", "new-raw-key"))
                .verifyComplete();

        assertThat(requests).hasSize(3);
        assertThat(requests.get(0).method()).isEqualTo(HttpMethod.GET);
        assertThat(requests.get(0).path()).isEqualTo("/key/info");
        assertThat(requests.get(0).query()).contains("key=new-raw-key");
        assertThat(requests.get(1).method()).isEqualTo(HttpMethod.GET);
        assertThat(requests.get(1).path()).isEqualTo("/key/list");
        assertThat(requests.get(1).query()).contains("user_id=user-1", "team_id=team-1");
        assertThat(requests.get(2).method()).isEqualTo(HttpMethod.POST);
        assertThat(requests.get(2).path()).isEqualTo("/key/delete");
        assertThat(requests.get(2).body()).contains("old-token-hash-1", "old-token-hash-2");
        assertThat(requests.get(2).body()).doesNotContain("new-raw-key", "new-token-hash", "other-team-token");
    }

    @Test
    void cleanupOtherApiKeysDeletesOldKeysFromDataListAndKeepsCurrentTokenId() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            String path = request.url().getPath();
            if (request.method() == HttpMethod.GET && path.equals("/key/info")) {
                return Mono.just(jsonResponse("""
                        {
                          "key": "new-raw-key",
                          "token": "new-token-hash",
                          "token_id": "new-token-id",
                          "user_id": "user-1",
                          "team_id": "team-1"
                        }
                        """));
            }
            if (request.method() == HttpMethod.GET && path.equals("/key/list")) {
                return Mono.just(jsonResponse("""
                        {
                          "data": [
                            {"token_id": "old-token-id-1", "user_id": "user-1", "team_id": "team-1"},
                            {"token_id": "new-token-id", "user_id": "user-1", "team_id": "team-1"},
                            {"key": "old-raw-key-2", "user_id": "user-1", "team_id": "team-1"},
                            {"token_id": "other-user-token", "user_id": "user-2", "team_id": "team-1"}
                          ],
                          "total_pages": 1
                        }
                        """));
            }
            if (request.method() == HttpMethod.POST && path.equals("/key/delete")) {
                return Mono.just(jsonResponse("{\"deleted_keys\":[\"old-token-id-1\",\"old-raw-key-2\"]}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.cleanupOtherApiKeysForUser("user-1", "team-1", "new-raw-key"))
                .verifyComplete();

        assertThat(requests).hasSize(3);
        assertThat(requests.get(2).body())
                .contains("old-token-id-1", "old-raw-key-2")
                .doesNotContain("new-token-id", "other-user-token");
    }

    @Test
    void cleanupOtherApiKeysSkipsDeletionWhenCurrentKeyInfoCannotBeResolved() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            String path = request.url().getPath();
            if (request.method() == HttpMethod.GET && path.equals("/key/info")) {
                return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND)
                        .header("Content-Type", "application/json")
                        .body("{\"error\":{\"message\":\"key not found\",\"code\":404}}")
                        .build());
            }
            if (request.method() == HttpMethod.GET && path.equals("/key/list")) {
                return Mono.just(jsonResponse("""
                        {
                          "data": [
                            {"token_id": "new-token-id", "user_id": "user-1", "team_id": "team-1"},
                            {"token_id": "old-token-id", "user_id": "user-1", "team_id": "team-1"}
                          ],
                          "total_pages": 1
                        }
                        """));
            }
            if (request.method() == HttpMethod.POST && path.equals("/key/delete")) {
                return Mono.just(jsonResponse("{\"deleted_keys\":[\"new-token-id\",\"old-token-id\"]}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.cleanupOtherApiKeysForUser("user-1", "team-1", "new-raw-key"))
                .verifyComplete();

        assertThat(requests).hasSize(1);
        assertThat(requests.get(0).method()).isEqualTo(HttpMethod.GET);
        assertThat(requests.get(0).path()).isEqualTo("/key/info");
    }

    @Test
    void cleanupOtherApiKeysKeepsCurrentKeyWhenKeyInfoUsesNestedInfoShape() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            String path = request.url().getPath();
            if (request.method() == HttpMethod.GET && path.equals("/key/info")) {
                return Mono.just(jsonResponse("""
                        {
                          "key": "new-raw-key",
                          "info": {
                            "key_name": "sk-...mVIA",
                            "user_id": "user-1",
                            "team_id": "team-1"
                          }
                        }
                        """));
            }
            if (request.method() == HttpMethod.GET && path.equals("/key/list")) {
                return Mono.just(jsonResponse("""
                        {
                          "keys": [
                            {"token": "new-token-hash", "key_name": "sk-...mVIA", "user_id": "user-1", "team_id": "team-1"},
                            {"token": "old-token-hash", "key_name": "sk-...old", "user_id": "user-1", "team_id": "team-1"}
                          ],
                          "total_pages": 1
                        }
                        """));
            }
            if (request.method() == HttpMethod.POST && path.equals("/key/delete")) {
                return Mono.just(jsonResponse("{\"deleted_keys\":[\"old-token-hash\"]}"));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.cleanupOtherApiKeysForUser("user-1", "team-1", "new-raw-key"))
                .verifyComplete();

        assertThat(requests).hasSize(3);
        assertThat(requests.get(2).body())
                .contains("old-token-hash")
                .doesNotContain("new-token-hash", "sk-...mVIA");
    }

    @Test
    void listUserQuotasNormalizesLiteLlmUserListPayload() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/user/list")) {
                return Mono.just(jsonResponse("""
                        {
                          "users": [
                            {
                              "user_id": "user-1",
                              "user_email": "user@example.com",
                              "user_alias": "User One",
                              "spend": 2.5,
                              "max_budget": 10,
                              "teams": ["team-1"],
                              "created_at": "2026-06-01T00:00:00Z",
                              "updated_at": "2026-06-02T00:00:00Z"
                            }
                          ]
                        }
                        """));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.listUserQuotas())
                .assertNext(quotas -> {
                    assertThat(quotas).hasSize(1);
                    assertThat(quotas.get(0).getUserId()).isEqualTo("user-1");
                    assertThat(quotas.get(0).getUserEmail()).isEqualTo("user@example.com");
                    assertThat(quotas.get(0).getUserAlias()).isEqualTo("User One");
                    assertThat(quotas.get(0).getSpend()).isEqualTo(2.5);
                    assertThat(quotas.get(0).getBudget()).isEqualTo(10.0);
                    assertThat(quotas.get(0).getRemaining()).isEqualTo(7.5);
                    assertThat(quotas.get(0).getTeams()).containsExactly("team-1");
                })
                .verifyComplete();

        assertThat(requests).hasSize(1);
        assertThat(requests.get(0).path()).isEqualTo("/user/list");
    }

    @Test
    void listTeamModelsExpandsWildcardAndFiltersNonChatModels() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/team/info")) {
                return Mono.just(jsonResponse("""
                        {
                          "team_info": {
                            "models": ["all-proxy-models"]
                          }
                        }
                        """));
            }
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/model/info")) {
                return Mono.just(jsonResponse("""
                        {
                          "data": [
                            {"model_name": "jina-embeddings-v4", "model_info": {"mode": "embedding"}},
                            {"model_name": "gpt-4o", "model_info": {"mode": "chat"}},
                            {"model_name": "gpt-4o-mini", "litellm_params": {"mode": "completion"}}
                          ]
                        }
                        """));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.listTeamModels("team-1"))
                .assertNext(models -> assertThat(models).containsExactly("gpt-4o", "gpt-4o-mini"))
                .verifyComplete();

        assertThat(requests).hasSize(2);
        assertThat(requests.get(0).path()).isEqualTo("/team/info");
        assertThat(requests.get(0).query()).isEqualTo("team_id=team-1");
        assertThat(requests.get(1).path()).isEqualTo("/model/info");
    }

    @Test
    void getUserQuotaNormalizesLiteLlmUserInfoPayload() {
        List<CapturedRequest> requests = new ArrayList<>();
        ExchangeFunction exchangeFunction = request -> {
            requests.add(CapturedRequest.from(request));
            if (request.method() == HttpMethod.GET && request.url().getPath().equals("/user/info")) {
                return Mono.just(jsonResponse("""
                        {
                          "user_id": "user-1",
                          "user_email": "user@example.com",
                          "user_alias": "User One",
                          "spend": 3,
                          "max_budget": 10,
                          "teams": ["team-1"]
                        }
                        """));
            }
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        };

        LiteLLMManager manager = newManager(exchangeFunction);

        StepVerifier.create(manager.getUserQuota("user-1"))
                .assertNext(quota -> {
                    assertThat(quota.getUserId()).isEqualTo("user-1");
                    assertThat(quota.getUserEmail()).isEqualTo("user@example.com");
                    assertThat(quota.getUserAlias()).isEqualTo("User One");
                    assertThat(quota.getSpend()).isEqualTo(3.0);
                    assertThat(quota.getBudget()).isEqualTo(10.0);
                    assertThat(quota.getRemaining()).isEqualTo(7.0);
                    assertThat(quota.getTeams()).containsExactly("team-1");
                })
                .verifyComplete();

        assertThat(requests).hasSize(1);
        assertThat(requests.get(0).path()).isEqualTo("/user/info");
        assertThat(requests.get(0).query()).isEqualTo("user_id=user-1");
    }

    private static ClientResponse jsonResponse(String body) {
        return ClientResponse.create(HttpStatus.OK)
                .header("Content-Type", "application/json")
                .body(body)
                .build();
    }

    private static LiteLLMManager newManager(ExchangeFunction exchangeFunction) {
        LiteLLMManager manager = new LiteLLMManager();
        ReflectionTestUtils.setField(
                manager,
                "litellmWebClient",
                WebClient.builder()
                        .baseUrl("http://litellm:4000")
                        .exchangeFunction(exchangeFunction)
                        .build()
        );
        return manager;
    }

    private static com.dadastory.omni_ai_router.dto.Result<?> successResult(String body) {
        try {
            return com.dadastory.omni_ai_router.dto.Result.success(
                    tools.jackson.databind.json.JsonMapper.builder().build().readTree(body)
            );
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }

    private record CapturedRequest(HttpMethod method, String path, String query, String body) {
        static CapturedRequest from(ClientRequest request) {
            String encodedQuery = request.url().getRawQuery();
            return new CapturedRequest(
                    request.method(),
                    request.url().getPath(),
                    encodedQuery == null ? "" : URLDecoder.decode(encodedQuery, StandardCharsets.UTF_8),
                    captureBody(request)
            );
        }

        private static String captureBody(ClientRequest request) {
            MockClientHttpRequest httpRequest = new MockClientHttpRequest(request.method(), request.url());
            BodyInserter.Context context = new BodyInserter.Context() {
                @Override
                public List<HttpMessageWriter<?>> messageWriters() {
                    return ExchangeStrategies.withDefaults().messageWriters();
                }

                @Override
                public Optional<ServerHttpRequest> serverRequest() {
                    return Optional.empty();
                }

                @Override
                public Map<String, Object> hints() {
                    return Map.of();
                }
            };

            request.body().insert(httpRequest, context).block();
            return httpRequest.getBodyAsString().block();
        }
    }
}
