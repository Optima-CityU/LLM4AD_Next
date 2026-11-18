import type { ProviderResponse } from "@/client"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request } from "@/client/core/request"

export interface LocalCodexStatus {
  enabled: boolean
  connected: boolean
  installed: boolean
  authenticated: boolean
  version: string
  model: string
  provider_id: string | null
  default_for_experiments: boolean
  reason: string
  models: { id: string; name: string }[]
  default_model: string
  models_available: boolean
}

export const localCodexService = {
  status: () =>
    request<LocalCodexStatus>(OpenAPI, {
      method: "GET",
      url: "/api/v1/llm4ad/providers/local-codex/status",
    }),
  bind: (body: { model: string; set_defaults: boolean }) =>
    request<ProviderResponse>(OpenAPI, {
      method: "POST",
      url: "/api/v1/llm4ad/providers/local-codex/bind",
      body,
      mediaType: "application/json",
    }),
}
