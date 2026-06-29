import { OpenAPI } from "./core/OpenAPI"
import { request as __request } from "./core/request"

export type EmbeddingProviderType =
  | "openai"
  | "jina"
  | "openai_compatible"
  | "mock"
  | "local"

export type EmbeddingMode = "shared" | "split"

export interface EmbeddingProviderCreate {
  name: string
  type?: EmbeddingProviderType
  api_key?: string
  auth_token?: string
  base_url?: string | null
  mode?: EmbeddingMode
  model?: string
  dim?: number
  timeout?: number
  embedding_func_max_async?: number
  text_type?: EmbeddingProviderType
  text_base_url?: string | null
  text_api_key?: string
  text_auth_token?: string
  text_model?: string
  text_task?: string
  code_type?: EmbeddingProviderType
  code_base_url?: string | null
  code_api_key?: string
  code_auth_token?: string
  code_model?: string
  code_task?: string
}

export type EmbeddingProviderUpdate = Partial<EmbeddingProviderCreate>

export interface EmbeddingProviderResponse {
  id: string
  created_time: string
  updated_time: string
  user_id: string | null
  name: string
  type: EmbeddingProviderType
  api_key: string
  auth_token: string
  base_url: string | null
  mode: EmbeddingMode
  model: string
  dim: number
  timeout: number
  embedding_func_max_async: number
  text_type: EmbeddingProviderType
  text_base_url: string | null
  text_api_key: string
  text_auth_token: string
  text_model: string
  text_task: string
  code_type: EmbeddingProviderType
  code_base_url: string | null
  code_api_key: string
  code_auth_token: string
  code_model: string
  code_task: string
  is_builtin: boolean
  visible_to_all: boolean
}

export interface PaginatedEmbeddingProviderResponse {
  items: EmbeddingProviderResponse[]
  total: number
  skip: number
  limit: number
}

export class Llm4AdEmbeddingProvidersService {
  public static createEmbeddingProvider(data: {
    requestBody: EmbeddingProviderCreate
  }) {
    return __request<EmbeddingProviderResponse>(OpenAPI, {
      method: "POST",
      url: "/api/v1/llm4ad/embedding-providers/",
      body: data.requestBody,
      mediaType: "application/json",
      errors: {
        422: "Validation Error",
      },
    })
  }

  public static listEmbeddingProviders(
    data: { skip?: number; limit?: number } = {},
  ) {
    return __request<PaginatedEmbeddingProviderResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/llm4ad/embedding-providers/",
      query: {
        skip: data.skip,
        limit: data.limit,
      },
      errors: {
        422: "Validation Error",
      },
    })
  }

  public static updateEmbeddingProvider(data: {
    providerId: string
    requestBody: EmbeddingProviderUpdate
  }) {
    return __request<EmbeddingProviderResponse>(OpenAPI, {
      method: "PATCH",
      url: "/api/v1/llm4ad/embedding-providers/{provider_id}",
      path: {
        provider_id: data.providerId,
      },
      body: data.requestBody,
      mediaType: "application/json",
      errors: {
        422: "Validation Error",
      },
    })
  }

  public static deleteEmbeddingProvider(data: { providerId: string }) {
    return __request<{ message: string }>(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/llm4ad/embedding-providers/{provider_id}",
      path: {
        provider_id: data.providerId,
      },
      errors: {
        422: "Validation Error",
      },
    })
  }
}
