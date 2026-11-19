export interface EvolveBlockPosition {
  startLine: number
  endLine: number
  startMarker: string
  endMarker: string
}

export interface SummaryResult {
  block_summary: string
  feasibility: Feasibility
  feasibility_reason: string
  significance: Significance
  significance_reason: string
  concerns: string[]
  suggestions: string[]
  rationale: string
  score: number | null
  block_ref: BlockRef | null
}

export interface BlockRef {
  file_path: string
  line_start: number
  line_end: number
  block_name: string
  language: string
}

export type Tier = "core" | "expanded" | "alternative"
export type Significance = "high" | "medium" | "low"
export type Feasibility = "yes" | "partial" | "no"

export interface BlockAdvice {
  block_summary: string
  feasibility: Feasibility
  feasibility_reason: string
  significance: Significance
  significance_reason: string
  concerns: string[]
  suggestions: string[]
  rationale: string
  score: number | null
  block_ref: BlockRef | null
}

export interface BlockRecommendation {
  file_path: string
  line_start: number
  line_end: number
  tier: Tier
  variant_index: number | null
  size_lines: number
  discovery_rationale: string
  advice: BlockAdvice | null
  advice_error: string | null
}

export interface DroppedCandidate {
  file_path: string
  line_start: number
  line_end: number
  tier: string
  reason: string
}

export interface RepoRecommendations {
  goal: string
  repo_path?: string
  core: BlockRecommendation | null
  expanded: BlockRecommendation[]
  alternatives: BlockRecommendation[]
  unreadable_files?: string[]
  dropped_candidates?: DroppedCandidate[]
  discovery_raw?: unknown
}

export type AdvisorStatus = "generating" | "completed" | "failed" | null

export interface AdvisorResult<T> {
  task_id: string
  advisor_type: string
  status: AdvisorStatus
  data: T | null
  error: string | null
  updated_at: string | null
}

export interface AdviseResultData<T> {
  results: T[]
}

export interface AdviseResult<T> {
  task_id: string
  advisor_type: string
  status: AdvisorStatus
  data: AdviseResultData<T> | null
  error: string | null
  updated_at: string | null
}

export const SHADER_FILE_EXTENSIONS = new Set(["comp", "glsl", "vert", "frag"])

export const COMMENT_STYLES: Record<string, [string, string]> = {
  py: ["# EVOLVE_START", "# EVOLVE_END"],
  pyw: ["# EVOLVE_START", "# EVOLVE_END"],
  js: ["// EVOLVE_START", "// EVOLVE_END"],
  ts: ["// EVOLVE_START", "// EVOLVE_END"],
  tsx: ["// EVOLVE_START", "// EVOLVE_END"],
  jsx: ["// EVOLVE_START", "// EVOLVE_END"],
  cpp: ["// EVOLVE_START", "// EVOLVE_END"],
  cc: ["// EVOLVE_START", "// EVOLVE_END"],
  c: ["// EVOLVE_START", "// EVOLVE_END"],
  h: ["// EVOLVE_START", "// EVOLVE_END"],
  hpp: ["// EVOLVE_START", "// EVOLVE_END"],
  java: ["// EVOLVE_START", "// EVOLVE_END"],
  go: ["// EVOLVE_START", "// EVOLVE_END"],
  rs: ["// EVOLVE_START", "// EVOLVE_END"],
  rb: ["# EVOLVE_START", "# EVOLVE_END"],
  sh: ["# EVOLVE_START", "# EVOLVE_END"],
  php: ["// EVOLVE_START", "// EVOLVE_END"],
  html: ["<!-- EVOLVE_START -->", "<!-- EVOLVE_END -->"],
  xml: ["<!-- EVOLVE_START -->", "<!-- EVOLVE_END -->"],
  vue: ["<!-- EVOLVE_START -->", "<!-- EVOLVE_END -->"],
  svelte: ["<!-- EVOLVE_START -->", "<!-- EVOLVE_END -->"],
}

export function getCommentStyle(filePath: string): [string, string] {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? ""
  if (SHADER_FILE_EXTENSIONS.has(ext))
    return ["// EVOLVE_START", "// EVOLVE_END"]
  return COMMENT_STYLES[ext] ?? ["# EVOLVE_START", "# EVOLVE_END"]
}

export function getLanguageFromPath(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? ""
  // Monaco's existing C-family tokenizer also handles GLSL source syntax.
  if (SHADER_FILE_EXTENSIONS.has(ext)) return "cpp"
  const map: Record<string, string> = {
    py: "python",
    pyw: "python",
    js: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    cpp: "cpp",
    cc: "cpp",
    c: "c",
    h: "c",
    hpp: "cpp",
    java: "java",
    go: "go",
    rs: "rust",
    rb: "ruby",
    sh: "shell",
    php: "php",
    html: "html",
    xml: "xml",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    toml: "toml",
    md: "markdown",
  }
  return map[ext] ?? "plaintext"
}
