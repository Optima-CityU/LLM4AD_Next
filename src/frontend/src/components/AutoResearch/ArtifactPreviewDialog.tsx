import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  FileCode,
  File as FileIcon,
  FileText,
  FolderOpen,
  ImageIcon,
  Languages,
  Loader2,
  MousePointerClick,
  Save,
  Square,
  WandSparkles,
} from "lucide-react"
import {
  useEffect,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import {
  Llm4AdResearchService,
  type FileTreeNode,
  type ResearchArtifactTreeNode,
} from "@/client"
import FileTreeView from "@/components/Evolution/TaskDetail/steps/FileTreeView"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import {
  downloadResearchArtifact,
  fetchResearchArtifact,
  researchKeys,
  useResearchArtifactTree,
} from "@/hooks/useAutoResearch"
import {
  useArtifactTranslation,
  type ArtifactTranslationStatus,
} from "@/hooks/useArtifactTranslation"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"
import { useQueryClient } from "@tanstack/react-query"

import ArtifactCodeEditor from "./ArtifactCodeEditor"

export interface PreviewFile {
  path: string
  name: string
  size: number | null
}

interface FileState {
  loaded: boolean
  loading: boolean
  error: string | null
  saved: string
  draft: string
  saving: boolean
}

const IMAGE_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "bmp",
  "ico",
  "avif",
])
const TEXT_EXTS = new Set([
  "py",
  "js",
  "ts",
  "tsx",
  "jsx",
  "json",
  "yaml",
  "yml",
  "txt",
  "tex",
  "log",
  "jsonl",
  "ndjson",
  "csv",
  "tsv",
  "toml",
  "ini",
  "cfg",
  "conf",
  "sh",
  "bash",
  "xml",
  "html",
  "htm",
  "css",
  "scss",
  "sql",
  "r",
  "java",
  "c",
  "cpp",
  "cc",
  "h",
  "hpp",
  "go",
  "rs",
  "rb",
  "php",
  "kt",
  "swift",
  "env",
  "properties",
])
const TEXT_MAX = 512 * 1024

type PreviewKind =
  | "image"
  | "pdf"
  | "markdown"
  | "text"
  | "binary"
  | "folder"
type ArtifactViewMode = "friendly" | "raw"
type TranslationView = "source" | "translation"

function classifyArtifact(nameOrPath: string): PreviewKind {
  if (nameOrPath.endsWith("/")) return "folder"
  const lower = nameOrPath.toLowerCase()
  const ext = lower.includes(".") ? (lower.split(".").pop() ?? "") : lower
  if (IMAGE_EXTS.has(ext)) return "image"
  if (ext === "pdf") return "pdf"
  if (ext === "md" || ext === "markdown") return "markdown"
  if (TEXT_EXTS.has(ext)) return "text"
  return "binary"
}

export function FileKindIcon({ name }: { name: string }) {
  const ext = name.split(".").pop()?.toLowerCase() ?? ""
  const cls = "size-3.5 shrink-0"
  if (["png", "jpg", "jpeg", "gif", "svg", "webp", "pdf"].includes(ext))
    return <ImageIcon className={cn(cls, "text-purple-500")} />
  if (["py", "js", "ts", "json", "yaml", "yml"].includes(ext))
    return <FileCode className={cn(cls, "text-cyan-500")} />
  if (["md", "txt", "tex", "log"].includes(ext))
    return <FileText className={cn(cls, "text-muted-foreground")} />
  return <FileIcon className={cn(cls, "text-muted-foreground")} />
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`
}

function normalizePath(p: string): string {
  return p.replace(/\/+$/, "")
}

function lastSegment(p: string): string {
  const norm = normalizePath(p)
  return norm.includes("/") ? (norm.split("/").pop() ?? norm) : norm
}

function findNodeByPath(
  root: ResearchArtifactTreeNode,
  target: string,
): ResearchArtifactTreeNode | null {
  if (root.path === target) return root
  for (const c of root.children ?? []) {
    const hit = findNodeByPath(c, target)
    if (hit) return hit
  }
  return null
}

function collectDirPaths(node: ResearchArtifactTreeNode): string[] {
  if (!node.is_dir) return []
  const out = [node.path]
  for (const c of node.children ?? []) out.push(...collectDirPaths(c))
  return out
}

function computeScope(
  root: ResearchArtifactTreeNode | null,
  path: string,
): {
  nodes: ResearchArtifactTreeNode[]
  scopeName: string | null
  initialFile: PreviewFile | null
  expandInit: Set<string>
} {
  const norm = normalizePath(path)
  const name = lastSegment(norm)
  const firstSeg = norm.includes("/") ? norm.split("/")[0] : norm

  if (!root) {
    const looksFile = !path.endsWith("/") && name.includes(".")
    return {
      nodes: [],
      scopeName: null,
      initialFile: looksFile ? { path: norm, name, size: null } : null,
      expandInit: new Set(),
    }
  }

  const node = findNodeByPath(root, norm)
  const isFile = node ? !node.is_dir : !path.endsWith("/") && name.includes(".")

  const scopeNode =
    (root.children ?? []).find((c) => c.path === firstSeg) ?? null

  let nodes: ResearchArtifactTreeNode[]
  let scopeName: string | null
  if (scopeNode) {
    nodes = [scopeNode]
    scopeName = scopeNode.is_dir ? scopeNode.name : null
  } else {
    nodes = root.children ?? []
    scopeName = null
  }

  const expandInit = new Set<string>()
  for (const n of nodes) {
    for (const p of collectDirPaths(n)) expandInit.add(p)
  }

  return {
    nodes,
    scopeName,
    initialFile: isFile ? { path: norm, name, size: node?.size ?? null } : null,
    expandInit,
  }
}

function adaptResearchNode(node: ResearchArtifactTreeNode): FileTreeNode {
  return {
    name: node.name,
    path: node.path,
    type: node.is_dir ? "directory" : "file",
    children: node.children?.map(adaptResearchNode),
  }
}

function adaptScopeNodes(nodes: ResearchArtifactTreeNode[]): FileTreeNode[] {
  return nodes.map(adaptResearchNode)
}

function preferRawFriendly(path: string): boolean {
  const fileName = lastSegment(path).toLowerCase()
  if (fileName === "decision.json") return false
  const ext = path.split(".").pop()?.toLowerCase() ?? ""
  return ext === "json" || ext === "jsonl" || ext === "ndjson"
}

function hasNamedFriendlyRenderer(path: string): boolean {
  const fileName = lastSegment(path).toLowerCase()
  return fileName === "decision.json" || fileName === "hardware_profile.json"
}

function TranslationStatusBadge({
  status,
  isStreaming,
  t,
}: {
  status: ArtifactTranslationStatus
  isStreaming: boolean
  t: ReturnType<typeof useTranslation>["t"]
}) {
  if (status === "idle") return null

  const config: Record<
    Exclude<ArtifactTranslationStatus, "idle">,
    { label: string; className: string; icon?: ReactNode }
  > = {
    cached: {
      label: t("autoResearch.artifacts.translationCached"),
      className:
        "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    },
    translating: {
      label: isStreaming
        ? t("autoResearch.artifacts.translationStreaming")
        : t("autoResearch.artifacts.translationStarting"),
      className:
        "border-blue-500/20 bg-blue-500/10 text-blue-600 dark:text-blue-400",
      icon: <Loader2 className="size-3 animate-spin" />,
    },
    completed: {
      label: t("autoResearch.artifacts.translationReady"),
      className:
        "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
      icon: <Check className="size-3" />,
    },
    failed: {
      label: t("autoResearch.artifacts.translationFailed"),
      className: "border-destructive/20 bg-destructive/10 text-destructive",
      icon: <AlertTriangle className="size-3" />,
    },
    cancelled: {
      label: t("autoResearch.artifacts.translationCancelled"),
      className:
        "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400",
      icon: <Square className="size-3 fill-current" />,
    },
  }

  const current = config[status]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        current.className,
      )}
    >
      {current.icon}
      {current.label}
    </span>
  )
}

function safeParseJson(content: string): unknown {
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

interface DecisionArtifactData {
  stage_id?: string | null
  run_id?: string | null
  status?: string | null
  decision?: string | null
  output_artifacts?: string[] | null
  evidence_refs?: string[] | null
  error?: string | null
  ts?: string | null
  next_stage?: number | null
}

function DecisionArtifactView({ data }: { data: DecisionArtifactData }) {
  const { t } = useTranslation()

  const rows = [
    {
      label: t("autoResearch.artifacts.decisionFields.stageId"),
      value: data.stage_id || "—",
    },
    {
      label: t("autoResearch.artifacts.decisionFields.runId"),
      value: data.run_id || "—",
      mono: true,
    },
    {
      label: t("autoResearch.artifacts.decisionFields.status"),
      value: data.status || "—",
      pill: true,
    },
    {
      label: t("autoResearch.artifacts.decisionFields.decision"),
      value: data.decision || "—",
      pill: true,
      accent: data.decision === "proceed",
    },
    {
      label: t("autoResearch.artifacts.decisionFields.timestamp"),
      value: data.ts || "—",
      mono: true,
    },
    {
      label: t("autoResearch.artifacts.decisionFields.nextStage"),
      value:
        data.next_stage == null ? "—" : `#${String(data.next_stage)}`,
    },
    {
      label: t("autoResearch.artifacts.decisionFields.error"),
      value: data.error || "—",
      destructive: !!data.error,
    },
  ]

  return (
    <div className="p-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((row) => (
          <div
            key={row.label}
            className="rounded-lg border border-border/50 bg-background/70 px-3 py-2.5"
          >
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
              {row.label}
            </div>
            {row.pill ? (
              <div className="mt-2">
                <span
                  className={cn(
                    "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium",
                    row.destructive
                      ? "border-destructive/20 bg-destructive/10 text-destructive"
                      : row.accent
                        ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                        : "border-border/60 bg-muted/40 text-foreground/80",
                  )}
                >
                  {row.value}
                </span>
              </div>
            ) : (
              <div
                className={cn(
                  "mt-1.5 text-sm text-foreground break-all",
                  row.mono && "font-mono text-[12px]",
                  row.destructive && "text-destructive",
                )}
              >
                {row.value}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-lg border border-border/50 bg-background/70 px-3 py-3">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
            {t("autoResearch.artifacts.decisionFields.outputArtifacts")}
          </div>
          {data.output_artifacts?.length ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {data.output_artifacts.map((item) => (
                <span
                  key={item}
                  className="inline-flex items-center rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-xs text-foreground/85"
                >
                  {item}
                </span>
              ))}
            </div>
          ) : (
            <div className="mt-2 text-sm text-muted-foreground">—</div>
          )}
        </div>

        <div className="rounded-lg border border-border/50 bg-background/70 px-3 py-3">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
            {t("autoResearch.artifacts.decisionFields.evidenceRefs")}
          </div>
          {data.evidence_refs?.length ? (
            <div className="mt-2 space-y-1.5">
              {data.evidence_refs.map((item) => (
                <div
                  key={item}
                  className="rounded-md bg-muted/40 px-2.5 py-1.5 font-mono text-[12px] text-foreground/85 break-all"
                >
                  {item}
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-2 text-sm text-muted-foreground">—</div>
          )}
        </div>
      </div>
    </div>
  )
}

interface HardwareProfileArtifactData {
  has_gpu?: boolean | null
  gpu_type?: string | null
  gpu_name?: string | null
  vram_mb?: number | null
  tier?: string | null
  warning?: string | null
}

function HardwareProfileArtifactView({
  data,
}: {
  data: HardwareProfileArtifactData
}) {
  const { t } = useTranslation()

  const rows = [
    {
      label: t("autoResearch.artifacts.hardwareFields.hasGpu"),
      value: data.has_gpu == null ? "—" : data.has_gpu ? t("common.yes") : t("common.no"),
      pill: true,
      accent: !!data.has_gpu,
      muted: data.has_gpu === false,
    },
    {
      label: t("autoResearch.artifacts.hardwareFields.gpuType"),
      value: data.gpu_type || "—",
      pill: true,
    },
    {
      label: t("autoResearch.artifacts.hardwareFields.gpuName"),
      value: data.gpu_name || "—",
    },
    {
      label: t("autoResearch.artifacts.hardwareFields.vram"),
      value:
        data.vram_mb == null ? "—" : `${String(data.vram_mb)} MB`,
      mono: true,
    },
    {
      label: t("autoResearch.artifacts.hardwareFields.tier"),
      value: data.tier || "—",
      pill: true,
      muted: data.tier === "cpu_only",
    },
  ]

  return (
    <div className="p-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((row) => (
          <div
            key={row.label}
            className="rounded-lg border border-border/50 bg-background/70 px-3 py-2.5"
          >
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
              {row.label}
            </div>
            {row.pill ? (
              <div className="mt-2">
                <span
                  className={cn(
                    "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium",
                    row.accent
                      ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                      : row.muted
                        ? "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400"
                        : "border-border/60 bg-muted/40 text-foreground/80",
                  )}
                >
                  {row.value}
                </span>
              </div>
            ) : (
              <div
                className={cn(
                  "mt-1.5 text-sm text-foreground break-all",
                  row.mono && "font-mono text-[12px]",
                )}
              >
                {row.value}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-amber-500/20 bg-amber-500/8 px-4 py-3">
        <div className="text-[11px] font-medium uppercase tracking-wider text-amber-700/90 dark:text-amber-300/90">
          {t("autoResearch.artifacts.hardwareFields.warning")}
        </div>
        <div className="mt-2 text-sm leading-relaxed text-foreground/85 whitespace-pre-wrap">
          {data.warning || "—"}
        </div>
      </div>
    </div>
  )
}

function ArtifactFriendlyContent({
  filePath,
  kind,
  content,
  mdId,
}: {
  filePath: string
  kind: PreviewKind
  content: string
  mdId: string
}) {
  const fileName = lastSegment(filePath).toLowerCase()

  if (fileName === "decision.json") {
    const parsed = safeParseJson(content)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return <DecisionArtifactView data={parsed as DecisionArtifactData} />
    }
  }

  if (fileName === "hardware_profile.json") {
    const parsed = safeParseJson(content)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return (
        <HardwareProfileArtifactView
          data={parsed as HardwareProfileArtifactData}
        />
      )
    }
  }

  if (kind === "markdown") {
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none p-4 prose-headings:text-foreground prose-headings:font-semibold prose-h1:text-lg prose-h2:text-base prose-pre:bg-transparent prose-pre:p-0">
        <Markdown
          remarkPlugins={MARKDOWN_REMARK_PLUGINS}
          rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
          components={makeMarkdownComponents(mdId)}
        >
          {content}
        </Markdown>
      </div>
    )
  }

  return (
    <pre className="overflow-x-auto p-4 text-xs leading-relaxed font-mono text-foreground/90 whitespace-pre">
      {content}
    </pre>
  )
}

function PreviewPane({
  sessionId,
  file,
  state,
  onDraftChange,
  onSave,
  readOnly,
  editable,
  allowSave,
  onConfirm,
  confirmLabel,
}: {
  sessionId: string
  file: PreviewFile | null
  state?: FileState
  onDraftChange: (path: string, draft: string) => void
  onSave: (path: string) => Promise<void>
  readOnly: boolean
  editable: boolean
  allowSave: boolean
  onConfirm?: () => void
  confirmLabel?: string
}) {
  const { t } = useTranslation()
  useHljsTheme()
  const mdId = useId()
  const [copiedText, copy] = useCopyToClipboard()
  const kind = file ? classifyArtifact(file.path || file.name) : "binary"
  const namedFriendly = !!file && hasNamedFriendlyRenderer(file.path)
  const forceRawFriendly = !!file && preferRawFriendly(file.path)
  const canFriendly = namedFriendly || kind === "markdown" || kind === "text"
  const canRaw = kind === "markdown" || kind === "text"
  const canEdit = editable && !readOnly && canRaw
  const [viewMode, setViewMode] = useState<ArtifactViewMode>("friendly")
  const [translationView, setTranslationView] = useState<TranslationView>("source")
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)

  const text = state?.loaded ? state.saved : null
  const draft = state?.loaded ? state.draft : null
  const loading = state?.loading ?? false
  const error = state?.error ?? null
  const saving = state?.saving ?? false
  const dirty = !!state && state.loaded && state.saved !== state.draft
  const tooLarge = !!file &&
    (kind === "text" || kind === "markdown") &&
    file.size != null &&
    file.size > TEXT_MAX

  const canTranslate =
    !!file &&
    !tooLarge &&
    (kind === "text" || kind === "markdown") &&
    !loading &&
    !error &&
    text != null

  const translation = useArtifactTranslation({
    sessionId,
    filePath: canTranslate ? (file?.path ?? null) : null,
    enabled: !!file && canTranslate,
  })

  useEffect(() => {
    if (!file) return
    if (canEdit) {
      setViewMode("raw")
      return
    }
    if (forceRawFriendly) {
      setViewMode("raw")
      return
    }
    if (canFriendly) {
      setViewMode("friendly")
      return
    }
    if (canRaw) {
      setViewMode("raw")
      return
    }
    setViewMode("friendly")
  }, [file?.path, canEdit, canFriendly, canRaw, forceRawFriendly])

  useEffect(() => {
    setTranslationView("source")
    setShowConfirmDialog(false)
  }, [file?.path])

  useEffect(() => {
    if (!canTranslate && translationView === "translation") {
      setTranslationView("source")
    }
  }, [translationView, canTranslate])

  useEffect(() => {
    if (
      translationView === "translation" &&
      canTranslate &&
      !translation.hasTranslation &&
      !translation.isStreaming
    ) {
      const textLength = text?.length ?? 0
      if (textLength > 10000) {
        setShowConfirmDialog(true)
      } else {
        void handleTranslate(false)
      }
    }
  }, [translationView, canTranslate])

  const triggerDownload = () => {
    if (!file) return
    void downloadResearchArtifact(sessionId, file.path).catch((err: unknown) =>
      toast.error((err as Error)?.message ?? "download failed"),
    )
  }

  const targetLanguage = "zh"
  const isTranslationTargetCurrent =
    translation.targetLanguage === targetLanguage && translation.hasTranslation

  const handleTranslate = async (force = false) => {
    if (!canTranslate) return
    setShowConfirmDialog(false)
    try {
      await translation.startTranslation({
        force,
        targetLanguage,
      })
    } catch (err) {
      toast.error(
        (err as Error)?.message ?? t("autoResearch.artifacts.translationFailed"),
      )
    }
  }

  const handleCopy = async () => {
    const content = currentTextContent
    if (!content) return
    const ok = await copy(content)
    if (ok) {
      toast.success(
        translationView === "translation"
          ? t("autoResearch.artifacts.copyTranslationSuccess")
          : t("autoResearch.artifacts.copySourceSuccess"),
      )
    } else {
      toast.error(t("autoResearch.artifacts.copyFailed"))
    }
  }

  const sourceContent = canEdit ? (draft ?? text ?? "") : (text ?? "")
  const translatedContent =
    translationView === "translation" && isTranslationTargetCurrent
      ? translation.content
      : ""
  const currentTextContent =
    translationView === "translation" ? translatedContent : sourceContent

  if (!file) {
    return (
      <div className="flex flex-1 min-w-0 flex-col items-center justify-center gap-3 rounded-md border border-border/50 bg-muted/20 px-6 text-center">
        <MousePointerClick className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {t("autoResearch.artifacts.selectFile")}
        </p>
      </div>
    )
  }

  return (
    <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-md border border-border/50 bg-muted/20">
      {showConfirmDialog && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="space-y-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="size-5 shrink-0 text-amber-500 mt-0.5" />
                <div className="space-y-1">
                  <h3 className="text-sm font-semibold text-foreground">
                    {t("autoResearch.artifacts.confirmTranslationTitle")}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {t("autoResearch.artifacts.confirmTranslationMessage", {
                      chars: text?.length ?? 0,
                    })}
                  </p>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setShowConfirmDialog(false)
                    setTranslationView("source")
                  }}
                  className="px-3 py-1.5 rounded-md text-xs font-medium border border-border bg-background hover:bg-muted transition-colors"
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="button"
                  onClick={() => void handleTranslate(false)}
                  className="px-3 py-1.5 rounded-md text-xs font-medium border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                >
                  {t("common.confirm")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 border-b border-border/50 bg-background/80 px-3 py-2 backdrop-blur-sm">
        <div className="flex min-w-0 items-center gap-2">
          <div className="inline-flex items-center rounded-md border border-border/60 bg-background/60 p-0.5">
            {canFriendly && !forceRawFriendly && (
              <button
                type="button"
                onClick={() => setViewMode("friendly")}
                className={cn(
                  "rounded px-2 py-1 text-[11px] font-medium transition-colors",
                  viewMode === "friendly"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t("autoResearch.artifacts.viewFriendly")}
              </button>
            )}
            {canRaw && (
              <button
                type="button"
                onClick={() => setViewMode("raw")}
                className={cn(
                  "rounded px-2 py-1 text-[11px] font-medium transition-colors",
                  viewMode === "raw"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t("autoResearch.artifacts.viewRaw")}
              </button>
            )}
          </div>

          {canTranslate && (
            <>
              <div className="h-4 w-px bg-border/60" />
              <Tabs
                value={translationView}
                onValueChange={(value) =>
                  setTranslationView(value as TranslationView)
                }
                className="gap-0"
              >
                <TabsList className="h-8">
                  <TabsTrigger value="source" className="text-xs px-3">
                    {t("autoResearch.artifacts.source")}
                  </TabsTrigger>
                  <TabsTrigger value="translation" className="text-xs px-3">
                    {t("autoResearch.artifacts.translation")}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              <TranslationStatusBadge
                status={translation.status}
                isStreaming={translation.isStreaming}
                t={t}
              />
            </>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {canTranslate && translation.isStreaming && (
            <button
              type="button"
              onClick={translation.stop}
              className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-500/15 dark:text-amber-300"
            >
              <Square className="size-3.5 fill-current" />
              {t("autoResearch.artifacts.stopTranslation")}
            </button>
          )}

          {canTranslate && (
            <button
              type="button"
              onClick={() => void handleTranslate(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              <WandSparkles className="size-3.5" />
              {t("autoResearch.artifacts.forceRetranslate")}
            </button>
          )}

          {(kind === "text" || kind === "markdown") && currentTextContent ? (
            <button
              type="button"
              onClick={() => void handleCopy()}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              {copiedText === currentTextContent && currentTextContent ? (
                <Check className="size-3.5 text-emerald-500" />
              ) : (
                <Copy className="size-3.5" />
              )}
              {translationView === "translation"
                ? t("autoResearch.artifacts.copyTranslation")
                : t("autoResearch.artifacts.copySource")}
            </button>
          ) : null}

          <button
            type="button"
            onClick={triggerDownload}
            title={t("autoResearch.artifacts.download")}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          >
            <Download className="size-3.5" />
            {t("autoResearch.artifacts.download")}
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {loading ? (
          <div className="flex h-full items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {t("autoResearch.artifacts.previewLoading")}
          </div>
        ) : error ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 py-16 px-4 text-center text-sm text-destructive">
            <AlertTriangle className="size-6" />
            {t("autoResearch.artifacts.previewError")}
            <span className="text-xs text-muted-foreground break-all">{error}</span>
          </div>
        ) : tooLarge || kind === "binary" ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-16 px-4 text-center">
            <FileIcon className="size-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              {tooLarge
                ? t("autoResearch.artifacts.previewTooLarge")
                : t("autoResearch.artifacts.previewUnsupported")}
            </p>
          </div>
        ) : kind === "folder" ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-16 px-4 text-center">
            <FolderOpen className="size-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              {t("autoResearch.artifacts.previewFolder")}
            </p>
          </div>
        ) : kind === "image" ? (
          <ImagePreview sessionId={sessionId} path={file.path} alt={file.name} />
        ) : kind === "pdf" ? (
          <PdfPreview sessionId={sessionId} path={file.path} title={file.name} />
        ) : viewMode === "raw" && currentTextContent ? (
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center gap-2 border-b border-border/50 bg-background/60 px-3 py-2">
              <span className="truncate text-[11px] font-mono text-muted-foreground">
                {file.path}
              </span>
              {canEdit && dirty && (
                <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="size-3" />
                  {t("autoResearch.artifacts.unsaved")}
                </span>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <ArtifactCodeEditor
                filePath={file.path}
                value={canEdit ? (draft ?? "") : currentTextContent}
                readOnly={!canEdit}
                onChange={
                  canEdit
                    ? (next) => onDraftChange(file.path, next)
                    : undefined
                }
              />
            </div>
            {(canEdit || onConfirm) && (
              <div className="flex items-center justify-between gap-2 border-t border-border/50 bg-background/60 px-3 py-2">
                <span className="text-[11px] text-muted-foreground">
                  {canEdit
                    ? t("autoResearch.artifacts.editableFile")
                    : t("autoResearch.artifacts.readOnlyFile")}
                </span>
                <div className="flex items-center gap-2">
                  {canEdit && allowSave && (
                    <button
                      type="button"
                      onClick={() => void onSave(file.path)}
                      disabled={!dirty || saving}
                      className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
                    >
                      {saving ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Save className="size-3.5" />
                      )}
                      {t("autoResearch.form.editSave")}
                    </button>
                  )}
                  {onConfirm && (
                    <button
                      type="button"
                      onClick={onConfirm}
                      disabled={dirty || saving}
                      title={t("autoResearch.form.editConfirmHint")}
                      className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
                    >
                      <Check className="size-3.5" />
                      {confirmLabel ?? t("autoResearch.form.editConfirm")}
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        ) : currentTextContent ? (
          <div className="h-full overflow-auto">
            <ArtifactFriendlyContent
              filePath={file.path}
              kind={kind}
              content={currentTextContent}
              mdId={
                translationView === "translation"
                  ? `${mdId}-translation`
                  : mdId
              }
            />
          </div>
        ) : translationView === "translation" && translation.status === "failed" ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <AlertTriangle className="size-8 text-destructive" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                {t("autoResearch.artifacts.translationFailed")}
              </p>
              {translation.error && (
                <p className="text-xs text-muted-foreground break-all">
                  {translation.error}
                </p>
              )}
            </div>
          </div>
        ) : translationView === "translation" && translation.status === "cancelled" ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <Square className="size-8 fill-current text-amber-500" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                {t("autoResearch.artifacts.translationCancelled")}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("autoResearch.artifacts.translationCancelledHint")}
              </p>
            </div>
          </div>
        ) : translationView === "translation" && translation.isStreaming ? (
          <div className="flex h-full flex-col">
            {translation.content ? (
              viewMode === "raw" ? (
                <ArtifactCodeEditor
                  filePath={file.path}
                  value={translation.content}
                  readOnly
                />
              ) : (
                <ArtifactFriendlyContent
                  filePath={file.path}
                  kind={kind}
                  content={translation.content}
                  mdId={`${mdId}-translation`}
                />
              )
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                <Loader2 className="size-8 animate-spin text-primary" />
                <div className="space-y-1">
                  <p className="text-sm font-medium text-foreground">
                    {t("autoResearch.artifacts.translationStreaming")}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t("autoResearch.artifacts.translationStreamingHint")}
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : translationView === "translation" ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <Languages className="size-8 text-muted-foreground/50" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                {t("autoResearch.artifacts.noTranslationYet")}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("autoResearch.artifacts.noTranslationYetHint")}
              </p>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ImagePreview({
  sessionId,
  path,
  alt,
}: {
  sessionId: string
  path: string
  alt: string
}) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    ;(async () => {
      try {
        const resp = await fetchResearchArtifact(sessionId, path)
        const blob = await resp.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      } catch {
        setUrl(null)
      }
    })()

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [sessionId, path])

  if (!url) {
    return (
      <div className="flex h-full items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex h-full items-center justify-center p-4 overflow-auto">
      <img src={url} alt={alt} className="max-w-full max-h-[64vh] object-contain" />
    </div>
  )
}

function PdfPreview({
  sessionId,
  path,
  title,
}: {
  sessionId: string
  path: string
  title: string
}) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    ;(async () => {
      try {
        const resp = await fetchResearchArtifact(sessionId, path)
        const blob = await resp.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      } catch {
        setUrl(null)
      }
    })()

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [sessionId, path])

  if (!url) {
    return (
      <div className="flex h-full items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
      </div>
    )
  }

  return <iframe src={url} title={title} className="w-full h-full bg-white" />
}

export default function ArtifactPreviewDialog({
  sessionId,
  path,
  onClose,
  readOnly = true,
  allowSave,
  editablePaths,
  onConfirm,
  confirmLabel,
}: {
  sessionId: string
  path: string | null
  onClose: () => void
  readOnly?: boolean
  allowSave?: boolean
  editablePaths?: string[]
  onConfirm?: () => void
  confirmLabel?: string
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const treeQ = useResearchArtifactTree(path ? sessionId : null)
  const root = treeQ.data?.root ?? null

  const scope = useMemo(
    () => (path ? computeScope(root, path) : null),
    [root, path],
  )
  const treeNodes = useMemo(
    () => adaptScopeNodes(scope?.nodes ?? []),
    [scope?.nodes],
  )

  const [selected, setSelected] = useState<PreviewFile | null>(null)
  const [states, setStates] = useState<Record<string, FileState>>({})

  const editableSet = useMemo(
    () => new Set(editablePaths?.map(normalizePath) ?? []),
    [editablePaths],
  )

  const treeReady = !!root
  useEffect(() => {
    if (!path || !scope) {
      setSelected(null)
      return
    }
    setSelected(scope.initialFile)
  }, [path, treeReady])

  useEffect(() => {
    if (!path) {
      setStates({})
    }
  }, [path])

  const patch = (filePath: string, p: Partial<FileState>) => {
    setStates((prev) => {
      const base: FileState = prev[filePath] ?? {
        loaded: false,
        loading: false,
        error: null,
        saved: "",
        draft: "",
        saving: false,
      }
      return {
        ...prev,
        [filePath]: {
          ...base,
          ...p,
        },
      }
    })
  }

  useEffect(() => {
    if (!selected) return
    const kind = classifyArtifact(selected.path)
    if (kind !== "text" && kind !== "markdown") return
    const current = states[selected.path]
    if (current?.loaded || current?.loading) return
    if (selected.size != null && selected.size > TEXT_MAX) return

    let cancelled = false
    patch(selected.path, { loading: true, error: null })
    ;(async () => {
      try {
        const resp = await fetchResearchArtifact(sessionId, selected.path)
        const txt = await resp.text()
        if (cancelled) return
        patch(selected.path, {
          loaded: true,
          loading: false,
          saved: txt,
          draft: txt,
        })
      } catch (e) {
        if (cancelled) return
        patch(selected.path, {
          loading: false,
          error: (e as Error)?.message ?? "error",
        })
      }
    })()

    return () => {
      cancelled = true
    }
  }, [selected?.path, sessionId])

  const saveFile = async (filePath: string) => {
    const current = states[filePath]
    if (!current || !current.loaded || current.saved === current.draft || current.saving)
      return
    patch(filePath, { saving: true })
    try {
      await Llm4AdResearchService.writeArtifact({
        sessionId,
        path: filePath,
        requestBody: { content: current.draft },
      })
      patch(filePath, { saving: false, saved: current.draft })
      qc.invalidateQueries({ queryKey: researchKeys.artifacts(sessionId) })
      qc.invalidateQueries({ queryKey: researchKeys.artifactTree(sessionId) })
      toast.success(t("autoResearch.form.editSaved"))
    } catch (e) {
      patch(filePath, { saving: false })
      toast.error(
        (e as Error)?.message ?? t("autoResearch.form.editSaveFailed"),
      )
    }
  }

  const selectedState = selected ? states[selected.path] : undefined
  const currentEditable =
    !!selected && !readOnly && editableSet.has(normalizePath(selected.path))
  const effectiveAllowSave = allowSave ?? !readOnly

  return (
    <Dialog open={!!path} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="gap-3 w-[92vw] sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 min-w-0">
            {selected ? (
              <>
                <FileKindIcon name={selected.name} />
                <span className="truncate text-sm">{selected.name}</span>
                {selected.size != null && (
                  <span className="shrink-0 text-[11px] font-normal text-muted-foreground tabular-nums">
                    {formatSize(selected.size)}
                  </span>
                )}
              </>
            ) : (
              <>
                <FolderOpen className="size-4 shrink-0 text-amber-500" />
                <span className="truncate text-sm">
                  {scope?.scopeName ?? t("autoResearch.artifacts.fileList")}
                </span>
              </>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="flex gap-3 h-[74vh] min-h-[420px] min-w-0">
          <div className="w-64 shrink-0 flex flex-col rounded-md border border-border/50 bg-muted/20 overflow-hidden">
            <div className="shrink-0 border-b border-border/50 bg-muted/40 px-3 py-2 backdrop-blur-sm">
              <span className="text-xs font-semibold text-muted-foreground">
                {t("autoResearch.artifacts.fileList")}
              </span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {treeQ.isLoading ? (
                <div className="flex items-center gap-2 py-3 px-1 text-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" />
                  {t("autoResearch.artifacts.loading")}
                </div>
              ) : treeNodes.length === 0 ? (
                <p className="py-3 px-1 text-xs text-muted-foreground/60">
                  {t("autoResearch.artifacts.empty")}
                </p>
              ) : (
                <FileTreeView
                  tree={treeNodes}
                  selectedPath={selected?.path ?? null}
                  onSelectFile={(nextPath, isDir) => {
                    if (isDir) return
                    const node = root ? findNodeByPath(root, nextPath) : null
                    setSelected({
                      path: nextPath,
                      name: node?.name ?? lastSegment(nextPath),
                      size: node?.size ?? null,
                    })
                  }}
                />
              )}
            </div>
          </div>

          <PreviewPane
            sessionId={sessionId}
            file={selected}
            state={selectedState}
            onDraftChange={(filePath, draft) => patch(filePath, { draft })}
            onSave={saveFile}
            readOnly={readOnly}
            editable={currentEditable}
            allowSave={effectiveAllowSave}
            onConfirm={onConfirm}
            confirmLabel={confirmLabel}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
