export type MemoryScope = "user" | "project" | "task"

export type MemoryConfig = {
  id: string
  created_time: string
  updated_time: string
  enabled: boolean
  include_user_memory: boolean
  include_project_memory: boolean
  include_task_memory: boolean
  user_memory_limit: number
  project_memory_limit: number
  task_memory_limit: number
  retrieval_mode: "auto" | "manual"
  pinned_card_ids: string[]
  task_injection_mode: "topk" | "weight" | "random"
  mindmemos_search_strategy: string
  mindmemos_rerank: boolean
  mindmemos_score_threshold?: number | null
  mindmemos_fail_open: boolean
  mindmemos_request_timeout?: number | null
  mindmemos_add_timeout?: number | null
  mindmemos_binding_id?: string | null
  mindmemos_chat_provider_id?: string | null
  mindmemos_chat_model?: string | null
  mindmemos_embedding_provider_id?: string | null
  mindmemos_embedding_model?: string | null
  mindmemos_embedding_dim?: number | null
  system_enabled: boolean
  system_base_url: string
  system_api_key_configured: boolean
  system_chat_configured: boolean
  system_embedding_configured: boolean
  system_embedding_dimensions?: number | null
  system_rerank_enabled: boolean
  system_rerank_configured: boolean
  system_runtime_available: boolean
  user_id?: string
  project_id?: string
}

export type MemoryHealth = {
  ok: boolean
  message: string
  system_runtime_available: boolean
  system_enabled: boolean
  system_chat_configured: boolean
  system_embedding_configured: boolean
  system_api_key_configured: boolean
  system_rerank_enabled: boolean
  system_rerank_configured: boolean
  service_reachable: boolean
  auth_ok: boolean
  error_code?: string | null
  details?: Record<string, unknown>
}

export type MemoryCard = {
  id: string
  type: string
  title: string
  content: string
  structured_content?: MemoryCardStructuredContent | null
  enabled: boolean
  source: string
  tags: string[]
  score?: number | null
  generation?: number | null
  algorithm_id?: string | null
  metadata?: Record<string, unknown>
  "readonly"?: MemoryCardReadonlyInfo
}

export type MemoryCardStructuredContent = {
  description: string
  content: string[]
  artifacts: MemoryCardArtifact[]
}

export type MemoryCardArtifact = {
  artifact_id: string
  type: "code" | "formula" | "table" | "example" | "quote" | "metric"
  content: string
  source_hash: string
  language?: string | null
  source_block_id?: string | null
}

export type MemoryCardReadonlyInfo = {
  source: string
  status: string
  entity_name?: string | null
  property_name?: string | null
  property_time?: string | null
  last_update_at?: string | null
  event_time?: string | null
  source_timestamp?: string | null
}

export type MemoryCardPage = {
  items: MemoryCard[]
  page: number
  page_size: number
  total?: number | null
  has_more: boolean
}

export type MemoryCardExtractionResponse = {
  preview_id: string
  items: MemoryCard[]
  message: string
}

export type MemoryProviderBinding = {
  configured: boolean
  binding_id?: string | null
  project_id: string
  user_id: string
  chat_provider_id?: string | null
  chat_model?: string | null
  embedding_provider_id?: string | null
  embedding_model?: string | null
  embedding_dim?: number | null
  embedding_locked: boolean
  message: string
}

export type MemoryProviderBindingUpdate = {
  chat_provider_id: string
  chat_model: string
  embedding_provider_id: string
}

export type MemoryCardDraft = {
  id?: string
  type: string
  title: string
  content: string
  structured_content: MemoryCardStructuredContent
  enabled: boolean
  tags: string[]
}

export const DEFAULT_MEMORY_DRAFT: MemoryCardDraft = {
  type: "general_insight",
  title: "",
  content: "",
  structured_content: { description: "", content: [], artifacts: [] },
  enabled: true,
  tags: [],
}

export const MEMORY_TYPES = [
  "good_algorithm",
  "error_reflection",
  "domain_knowledge",
  "general_insight",
] as const
