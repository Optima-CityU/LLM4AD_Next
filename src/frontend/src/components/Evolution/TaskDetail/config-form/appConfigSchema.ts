import { z } from "zod"

// --- Provider ---
export const providerConfigSchema = z.object({
  name: z.string().default("default"),
  type: z.enum(["openai", "anthropic", "openai_compatible"]).default("openai"),
  api_key: z.string().default(""),
  base_url: z.string().nullable().optional(),
  model: z.string().default("gpt-4"),
  temperature: z.coerce.number().min(0).max(2).default(0.7),
  max_tokens: z.coerce.number().int().positive().default(32768),
  timeout: z.coerce.number().positive().default(60),
  max_retries: z.coerce.number().int().min(0).default(3),
})

// --- Dataset ---
export const datasetConfigSchema = z.object({
  mode: z.enum(["files", "directory", "glob"]).default("files"),
  files: z.array(z.string()).nullable().optional(),
  path: z.string().nullable().optional(),
  recursive: z.boolean().default(true),
  pattern: z.string().nullable().optional(),
})

// --- Evaluator ---
export const evaluatorConfigSchema = z.object({
  module: z.string().min(1, "Module is required"),
  dataset: datasetConfigSchema.optional(),
  metrics: z.array(z.string()).default([]),
  timeout: z.coerce.number().positive().default(60),
  max_retries: z.coerce.number().int().min(0).default(1),
  parallel: z.boolean().default(true),
  batch_size: z.coerce.number().int().positive().default(10),
})

// --- Evolution ---
export const evolutionConfigSchema = z.object({
  type: z.string().default("island_ga"),
  planner_type: z.string().default("llm_evolution"),
  population_size: z.coerce.number().int().min(2).default(50),
  max_generations: z.coerce.number().int().min(1).default(100),
  elite_ratio: z.coerce.number().min(0).max(1).default(0.1),
  mutation_rate: z.coerce.number().min(0).max(1).default(0.3),
  crossover_rate: z.coerce.number().min(0).max(1).default(0.5),
  selection_strategy: z
    .enum(["tournament", "roulette", "rank"])
    .default("tournament"),
  tournament_size: z.coerce.number().int().min(2).default(3),
  planner_provider: z.string().default("default"),
  coder_provider: z.string().default("default"),
  early_stop_patience: z.coerce.number().int().min(1).default(20),
  early_stop_threshold: z.coerce.number().min(0).default(1e-6),
  checkpoint_interval: z.coerce.number().int().min(1).default(10),
  max_checkpoints: z.coerce.number().int().min(1).default(5),
})

// --- Coder: Custom ---
export const customCoderConfigSchema = z.object({
  type: z.literal("custom").default("custom"),
  timeout: z.coerce.number().positive().default(300),
  max_retries: z.coerce.number().int().min(0).default(2),
  log_to_console: z.boolean().default(false),
  prompt_template: z.string().nullable().optional(),
  mutation_prompt_template: z.string().nullable().optional(),
  context_max_tokens: z.coerce.number().int().positive().default(4096),
  temperature: z.coerce.number().min(0).max(2).default(0.7),
  max_gen_tokens: z.coerce.number().int().positive().default(4096),
  default_extension: z.string().default("py"),
})

// --- Coder: ClaudeCode ---
export const claudeCodeConfigSchema = z.object({
  type: z.literal("claude_code").default("claude_code"),
  timeout: z.coerce.number().positive().default(300),
  max_retries: z.coerce.number().int().min(0).default(2),
  log_to_console: z.boolean().default(false),
  anthropic_api_key: z.string().default(""),
  anthropic_auth_token: z.string().default(""),
  anthropic_base_url: z.string().min(1, "Base URL is required"),
  anthropic_model: z.string().min(1, "Model is required"),
})

// --- Coder: OpenCode ---
export const openCodeConfigSchema = z.object({
  type: z.literal("opencode").default("opencode"),
  timeout: z.coerce.number().positive().default(300),
  max_retries: z.coerce.number().int().min(0).default(2),
  log_to_console: z.boolean().default(false),
})

// --- Coder (discriminated union) ---
export const coderConfigSchema = z.discriminatedUnion("type", [
  customCoderConfigSchema,
  claudeCodeConfigSchema,
  openCodeConfigSchema,
])

// --- Memory ---
export const memoryConfigSchema = z.object({
  type: z.string().default("local_yaml"),
  module: z.string().nullable().optional(),
  enabled: z.boolean().default(true),
  embedding_dim: z.coerce.number().int().min(1).default(768),
  max_entries: z.coerce.number().int().min(1).default(10000),
  similarity_threshold: z.coerce.number().min(0).max(1).default(0.8),
  decay_factor: z.coerce.number().min(0).max(1).default(0.99),
  mindmemos_base_url: z.string().default(""),
  mindmemos_api_key: z.string().default(""),
  mindmemos_user_id: z.string().default(""),
  mindmemos_app_id: z.string().default("llm4ad"),
  mindmemos_agent_id: z.string().default("planner"),
  mindmemos_session_id: z.string().default(""),
  mindmemos_project_id: z.string().default(""),
  mindmemos_search_strategy: z.string().default("fast"),
  mindmemos_rerank: z.boolean().default(false),
  mindmemos_score_threshold: z.coerce.number().min(0).max(1).nullable().default(0.65),
  mindmemos_fail_open: z.boolean().default(true),
  mindmemos_request_timeout: z.coerce.number().min(0).default(60),
  mindmemos_add_timeout: z.coerce.number().min(0).default(120),
  mindmemos_extraction_prompt_language: z.enum(["auto", "ZH", "EN"]).default("auto"),
  mindmemos_sync_static_cards: z.boolean().default(false),
  include_user_memory: z.boolean().default(false),
  include_project_memory: z.boolean().default(false),
  include_task_memory: z.boolean().default(true),
  user_memory_limit: z.coerce.number().int().min(0).default(0),
  project_memory_limit: z.coerce.number().int().min(0).default(0),
  task_memory_limit: z.coerce.number().int().min(0).default(5),
})

// --- Logging ---
export const loggingConfigSchema = z.object({
  level: z
    .enum(["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    .default("TRACE"),
  format: z.string().nullable().optional(),
  file: z.string().nullable().optional(),
  console: z.boolean().default(true),
  json_format: z.boolean().default(false),
})

// --- Workspace ---
export const workspaceConfigSchema = z.object({
  auto_create: z.boolean().default(true),
  subdirs: z.record(z.string(), z.string()).default({}),
})

// --- Version Control ---
export const versionControlConfigSchema = z.object({
  enabled: z.boolean().default(true),
  type: z.enum(["git_worktree", "none"]).default("git_worktree"),
  local_path: z.string().nullable().optional(),
  remote_url: z.string().nullable().optional(),
  revision: z.string().nullable().optional(),
  auto_initialize: z.boolean().default(true),
  auto_cleanup: z.boolean().default(true),
  max_worktree_age_days: z.coerce.number().min(0).default(7),
  max_worktrees: z.coerce.number().int().min(1).default(100),
  default_branch: z.string().default("main"),
  commit_message_template: z
    .string()
    .default("Algorithm {algorithm_id}: {description}"),
})

// --- Sampler ---
export const samplerConfigSchema = z.object({
  name: z.string().min(1, "Sampler name is required"),
  config: z.record(z.string(), z.unknown()).default({}),
})

// --- RepoAnalyzer ---
export const repoAnalyzerConfigSchema = z.object({
  type: z.string().default("evolve_detector"),
  context_lines_before: z.coerce.number().int().min(0).default(5),
  context_lines_after: z.coerce.number().int().min(0).default(5),
  include: z
    .array(z.string())
    .default([
      "*.py",
      "*.cpp",
      "*.cc",
      "*.c",
      "*.h",
      "*.hpp",
      "*.java",
      "*.js",
      "*.ts",
      "*.go",
      "*.rs",
      "*.rb",
      "*.sh",
      "*.php",
      "*.html",
      "*.xml",
    ]),
  exclude: z
    .array(z.string())
    .default([
      ".git/**",
      ".gitignore",
      "__pycache__/**",
      "*.pyc",
      "node_modules/**",
      "build/**",
      "dist/**",
      "*.egg-info/**",
      ".venv/**",
      "venv/**",
      ".idea/**",
      ".vscode/**",
      "*.log",
      "*.tmp",
      "*.bak",
    ]),
})

// --- Planner ---
export const plannerConfigSchema = z.object({
  type: z.string().default("llm_evolution"),
  samplers: z.array(samplerConfigSchema).default([]),
  selection_strategy: z
    .enum(["random", "weighted", "tournament", "roulette"])
    .default("weighted"),
})

// --- AppConfig (top-level) ---
export const appConfigSchema = z.object({
  project_name: z.string().default("llm4ad"),
  background: z.string().default(""),
  run_id: z.string().optional(),
  random_seed: z.coerce.number().int().default(42),
  base_dir: z.string().default("./runs"),
  providers: z.array(providerConfigSchema).default([]),
  evaluator: evaluatorConfigSchema,
  evolution: evolutionConfigSchema.optional(),
  coder: coderConfigSchema.optional(),
  memory: memoryConfigSchema.optional(),
  logging: loggingConfigSchema.optional(),
  workspace: workspaceConfigSchema.optional(),
  version_control: versionControlConfigSchema.optional(),
  repo_analyzer: repoAnalyzerConfigSchema.nullable().optional(),
  planner: plannerConfigSchema.nullable().optional(),
})

export type AppConfig = z.infer<typeof appConfigSchema>
