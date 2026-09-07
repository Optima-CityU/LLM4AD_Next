import type { MemoryCard } from "../Memory/types"

export type KnowledgeParseStatus =
  | "unparsed"
  | "pending"
  | "running"
  | "ready"
  | "stale"
  | "failed"
  | "cancelled"

export type KnowledgeSource = {
  id: string
  title: string
  background?: string | null
  source_revision: number
  source_file_count: number
  source_size: number
  parse_status: KnowledgeParseStatus
  active_parse_run_id: string | null
  last_error_code: string | null
  last_error: string | null
  created_time: string
  updated_time: string
}

export type KnowledgeSourceFile = {
  id: string
  source_id: string
  original_filename: string
  content_version: number
  content_size: number
  sort_order: number
  updated_time: string
}

export type KnowledgeDocument = {
  id: string
  source_id: string
  parse_run_id: string
  parent_id: string | null
  document_type: "document" | "main" | "child"
  title: string
  content_version: number
  content_size: number
  estimated_tokens: number
  sort_order: number
  user_modified: boolean
  updated_time: string
}

export type KnowledgeSourceDetail = KnowledgeSource & {
  source_files: KnowledgeSourceFile[]
  documents: KnowledgeDocument[]
}

export type KnowledgeContent = {
  content: string
  content_version: number
  content_hash: string
}

export type KnowledgeParseRun = {
  id: string
  source_id: string
  source_revision: number
  status: Exclude<KnowledgeParseStatus, "unparsed">
  progress: number
  stage: string
  message: string
  parser_name: string
  parser_provider_name: string | null
  parser_model: string | null
  parse_mode: "direct" | "planned" | "refine"
  plan_id: string | null
  plan_strategy_id: string | null
  parent_run_id: string | null
  session_owner_kind: "plan" | "run"
  session_owner_id: string | null
  can_refine: boolean
  generated_memory_ids: string[]
  inserted_document_ids: string[]
  skill_name: string
  skill_version: string
  error_code: string | null
  error: string | null
  stream_cursor?: string | null
  created_time: string
  updated_time: string
}

export type KnowledgeDocumentInsertResult = {
  inserted_document_ids: string[]
  generated_memory_ids: string[]
  generated_memories: MemoryCard[]
}

export type KnowledgeProgressEvent = {
  type?: string
  progress?: number
  stage?: string
  message?: string
  step_id?: string
  step_kind?: "tool" | "model" | "retry" | "context"
  step_status?: "running" | "success" | "failed" | "retrying"
  tool_name?: string
  step_detail?: string
  elapsed_seconds?: number
  attempt?: number
  max_retries?: number
}

export type KnowledgeParsePlanDocument = {
  title: string
  document_type?: "main" | "child" | null
  purpose: string
  source_coverage: string[]
  must_preserve: string[]
}

export type KnowledgeParsePlanStrategy = {
  id: string
  name: string
  description: string
  loss_level: "lossless" | "light" | "lossy"
  document_count: number
  documents: KnowledgeParsePlanDocument[]
  deduplication_policy: string
}

export type KnowledgeParsePlanPendingQuestion = {
  question_id: string
  questions: Array<{
    question: string
    header: string
    options: Array<{ label: string; description: string }>
    multiSelect: boolean
  }>
}

export type KnowledgeParsePlan = {
  id: string
  source_id: string
  source_revision: number
  status: Exclude<KnowledgeParseStatus, "unparsed">
  progress: number
  stage: string
  message: string
  parser_provider_name: string | null
  parser_model: string | null
  interaction_mode: "quick" | "collaborative"
  pending_question: KnowledgeParsePlanPendingQuestion | null
  payload: {
    topic_summary: string
    source_overview: Array<{
      filename: string
      summary: string
      key_sections: string[]
    }>
    recommended_strategy_id: string
    strategies: KnowledgeParsePlanStrategy[]
  } | null
  error_code: string | null
  error: string | null
  retryable: boolean
  retry_action: "persist" | null
  stream_cursor?: string | null
  created_time: string
  updated_time: string
}

export type KnowledgeParserBinding = {
  configured: boolean
  provider_id: string | null
  provider_name: string | null
  provider_type: string | null
  model_name: string | null
  context_window_tokens: number
  max_output_tokens: number
  error_code: string | null
  message: string
}
