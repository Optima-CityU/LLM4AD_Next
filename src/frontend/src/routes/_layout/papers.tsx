import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  ArrowRight,
  BookOpen,
  Calendar,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronUp,
  FileCheck2,
  FilePlus2,
  FileText,
  Folder,
  FolderPlus,
  GitBranch,
  ListChecks,
  Loader2,
  MessagesSquare,
  Microscope,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  PenLine,
  Plus,
  Rocket,
  Save,
  ScanSearch,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Target,
  Trash2,
  Upload,
  WifiOff,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"
import {
  type Layout,
  Group as ResizableGroup,
  Panel as ResizablePanel,
  Separator as ResizableSeparator,
  usePanelRef,
} from "react-resizable-panels"
import { toast } from "sonner"

import type {
  FileTreeNode,
  PaperConversationPreferences,
  ProposalStageState,
  ProviderResponse,
} from "@/client"
import ArtifactCodeEditor from "@/components/AutoResearch/ArtifactCodeEditor"
import WorkflowStepper from "@/components/Common/WorkflowStepper"
import FileTreeView from "@/components/Evolution/TaskDetail/steps/FileTreeView"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import AlgorithmDiscoveryPanel from "@/components/Paper/AlgorithmDiscoveryPanel"
import PaperNativeRuntime from "@/components/Paper/PaperNativeRuntime"
import type { ProposalResultStage } from "@/components/Paper/ProposalResultsDock"
import ProposalResultsDock from "@/components/Paper/ProposalResultsDock"
import RebuttalEntriesPanel from "@/components/Paper/RebuttalEntriesPanel"
import TypstLivePreview from "@/components/Paper/TypstLivePreview"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { usePaperHeader } from "@/hooks/usePaperHeader"
import {
  useCreatePaperWorkspace,
  useDeletePaperSourcePath,
  useDeletePaperWorkspace,
  usePaperSourceFile,
  usePaperWorkspace,
  usePaperWorkspaces,
  useUpdatePaperModelBinding,
  useUpdatePaperSourceFile,
  useUpdatePaperWorkspace,
  useUploadPaperSource,
} from "@/hooks/usePapers"
import { usePersistentState } from "@/hooks/usePersistentState"
import { useProviders } from "@/hooks/useProviders"
import {
  hasProjectContextFiles,
  projectContextUploads,
} from "@/lib/projectContext"
import {
  EMPTY_PROPOSAL_BASICS,
  type ProposalBasics,
  parseProposalBasics,
  serializeProposalBasics,
} from "@/lib/proposalBasics"
import {
  parseResearchWorkspaceMode,
  type ResearchWorkspaceMode,
} from "@/lib/researchWorkspace"
import { cn, formatDate } from "@/lib/utils"

export const Route = createFileRoute("/_layout/papers")({
  component: PaperProjectsPage,
  validateSearch: (search: Record<string, unknown>) => ({
    workspaceId:
      typeof search.workspaceId === "string" ? search.workspaceId : undefined,
    mode: parseResearchWorkspaceMode(search.mode),
  }),
  head: () => ({ meta: [{ title: "Research Studio - LLM4AD Next" }] }),
})

function PaperProjectsPage() {
  const navigate = useNavigate()
  const { mode, workspaceId } = Route.useSearch()

  useEffect(() => {
    if (!workspaceId) return
    void navigate({
      to: "/papers/$workspaceId",
      params: { workspaceId },
      search: { mode },
      replace: true,
    })
  }, [mode, navigate, workspaceId])

  return (
    <PaperProjectManager
      onOpen={(id) =>
        void navigate({
          to: "/papers/$workspaceId",
          params: { workspaceId: id },
          search: { mode },
        })
      }
      mode={mode}
    />
  )
}

type ModelBinding = { providerId: string; modelName: string }
type ConversationPreferencesDraft = Required<PaperConversationPreferences>
const DEFAULT_CONVERSATION_PREFERENCES: ConversationPreferencesDraft = {
  reply_language: "auto",
  additional_guidance: "",
}
type PaperSourceContext = {
  path: string
  role: string
  sections: string[]
}
type PaperTool =
  | "formatting"
  | "literature"
  | "rationale"
  | "objectives"
  | "methods"
  | "innovation_plan"
  | "foundation_feasibility"
  | "final_review"
  | "rebuttal_baseline"
  | "autorebuttal"
  | "ac_summary"
  | "discovery"
  | "reviews"
  | "boundary"
  | "targets"
  | "branch"
type ProposalStage = Extract<
  PaperTool,
  | "formatting"
  | "literature"
  | "rationale"
  | "objectives"
  | "methods"
  | "innovation_plan"
  | "foundation_feasibility"
  | "final_review"
>
type RebuttalStage = Extract<
  PaperTool,
  "rebuttal_baseline" | "autorebuttal" | "ac_summary"
>
const RESEARCH_WORKSPACE_MODE_ICONS: Record<
  ResearchWorkspaceMode,
  typeof Sparkles
> = {
  proposal: Sparkles,
  manuscript: FileText,
  algorithm: GitBranch,
}

const PAPER_WORKFLOW_STEPS = [
  { key: "formatting", icon: Sparkles },
  { key: "literature", icon: BookOpen },
  { key: "rationale", icon: FileText },
  { key: "objectives", icon: Target },
  { key: "methods", icon: GitBranch },
  { key: "innovation_plan", icon: CalendarClock },
  { key: "foundation_feasibility", icon: Microscope },
  { key: "final_review", icon: FileCheck2 },
  { key: "rebuttal_baseline", icon: ScanSearch },
  { key: "autorebuttal", icon: MessagesSquare },
  { key: "ac_summary", icon: PenLine },
  { key: "discovery", icon: GitBranch },
  { key: "reviews", icon: MessagesSquare },
  { key: "boundary", icon: ScanSearch },
  { key: "targets", icon: ListChecks },
  { key: "branch", icon: Rocket },
] as const

const PROPOSAL_STAGES: ProposalStage[] = [
  "formatting",
  "literature",
  "rationale",
  "objectives",
  "methods",
  "innovation_plan",
  "foundation_feasibility",
  "final_review",
]

const PROPOSAL_STAGE_FILES: Record<ProposalStage, string[]> = {
  formatting: [],
  literature: ["sections/02-literature.typ", "references.bib"],
  rationale: ["sections/01-rationale.typ", "sections/03-value.typ"],
  objectives: ["sections/04-objectives.typ"],
  methods: ["sections/05-methods.typ"],
  innovation_plan: ["sections/07-plan.typ"],
  foundation_feasibility: ["sections/06-feasibility.typ"],
  final_review: [],
}
const PROPOSAL_STAGE_PREREQUISITES: Record<ProposalStage, ProposalStage[]> = {
  formatting: [],
  literature: ["formatting"],
  rationale: ["literature"],
  objectives: ["rationale"],
  methods: ["objectives"],
  innovation_plan: ["methods"],
  foundation_feasibility: ["methods"],
  final_review: ["innovation_plan", "foundation_feasibility"],
}
const REBUTTAL_STAGE_PREREQUISITES: Record<RebuttalStage, RebuttalStage[]> = {
  rebuttal_baseline: [],
  autorebuttal: ["rebuttal_baseline"],
  ac_summary: ["autorebuttal"],
}
const PAPER_DIRECTORY_MARKER = ".llm4ad-directory"
const CREATABLE_PAPER_FILE_SUFFIXES = [
  ".typ",
  ".tex",
  ".bib",
  ".md",
  ".markdown",
  ".txt",
  ".csv",
  ".json",
  ".yaml",
  ".yml",
]

function isProposalStage(value: PaperTool): value is ProposalStage {
  return PROPOSAL_STAGES.includes(value as ProposalStage)
}

function isGeneratedProposalPath(
  path: string,
  entryPath: string | null | undefined,
) {
  return (
    path === entryPath ||
    Object.values(PROPOSAL_STAGE_FILES).some((paths) => paths.includes(path))
  )
}

function ProposalBasicsFields({
  basics,
  onChange,
}: {
  basics: ProposalBasics
  onChange: (basics: ProposalBasics) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {(["topic", "funding", "question", "foundation"] as const).map(
        (field) => (
          <div key={field} className="min-w-0 space-y-1.5">
            <Label htmlFor={`proposal-basic-${field}`}>
              {t(`paper.source.basics.${field}`)}
            </Label>
            {field === "topic" || field === "funding" ? (
              <Input
                id={`proposal-basic-${field}`}
                value={basics[field]}
                onChange={(event) =>
                  onChange({ ...basics, [field]: event.target.value })
                }
                placeholder={t(`paper.source.basics.${field}Placeholder`)}
                maxLength={field === "topic" ? 500 : 200}
              />
            ) : (
              <Textarea
                id={`proposal-basic-${field}`}
                value={basics[field]}
                onChange={(event) =>
                  onChange({ ...basics, [field]: event.target.value })
                }
                placeholder={t(`paper.source.basics.${field}Placeholder`)}
                className="min-h-24 resize-y"
                maxLength={field === "question" ? 1000 : 1500}
              />
            )}
          </div>
        ),
      )}
    </div>
  )
}

function PaperWorkflowStepper({
  stages,
  activeStage,
  completedStages,
  stageStates,
  onSelect,
}: {
  stages: PaperTool[]
  activeStage: PaperTool
  completedStages: Set<PaperTool>
  stageStates: Partial<Record<PaperTool, ProposalStageState>>
  onSelect: (stage: PaperTool) => void
}) {
  const { t } = useTranslation()
  return (
    <WorkflowStepper
      label={t("paper.workflow.statusTitle")}
      previousLabel={t("paper.workflow.previousStage")}
      nextLabel={t("paper.workflow.nextStage")}
      value={activeStage}
      onValueChange={onSelect}
      steps={stages.map((key) => {
        const state = stageStates[key]
        const status =
          state?.status === "needs_revision"
            ? "revision"
            : state?.status === "stale"
              ? "stale"
              : completedStages.has(key)
                ? "complete"
                : "pending"
        const statusLabel = t(
          `paper.workflow.${status === "revision" ? "needsRevision" : status === "complete" ? "ready" : status === "stale" ? "stale" : "notStarted"}`,
        )
        return {
          id: key,
          label: t(`paper.workflow.stage.${key}`),
          shortLabel: t(`paper.workflow.stageNav.${key}`, {
            defaultValue: t(`paper.workflow.stage.${key}`),
          }),
          description: t(`paper.workflow.stage.${key}Hint`),
          status,
          statusLabel: state?.iteration
            ? `${statusLabel} · ${t("paper.workflow.iteration", { count: state.iteration })}`
            : statusLabel,
        }
      })}
    />
  )
}

const CONTEXT_PRESETS = [32_000, 64_000, 128_000, 200_000]
const OUTPUT_PRESETS = [4_096, 8_192, 16_384, 32_000]
const EDITABLE_SUFFIXES = [
  ".typ",
  ".tex",
  ".bib",
  ".bst",
  ".cls",
  ".sty",
  ".md",
  ".markdown",
  ".txt",
  ".csv",
  ".json",
  ".yaml",
  ".yml",
]
const PAPER_MARKDOWN_THEME =
  "dark:prose-invert prose-headings:text-foreground prose-p:text-foreground/90 prose-strong:text-foreground prose-li:text-foreground/90 prose-a:text-primary prose-blockquote:border-border prose-blockquote:text-muted-foreground prose-code:text-primary prose-pre:border prose-pre:border-border/50 prose-pre:bg-code-block-bg prose-pre:text-foreground prose-th:text-foreground prose-td:text-foreground prose-hr:border-border/50"
const CLAUDE_RUNTIME_PROVIDER_TYPES = new Set([
  "anthropic",
  "openai",
  "openai_compatible",
])

function providerModels(provider: ProviderResponse | undefined) {
  return (provider?.model ?? "")
    .split(";")
    .map((value) => value.trim())
    .filter(Boolean)
}

function errorMessage(error: unknown, fallback: string) {
  const candidate = error as { body?: { detail?: string }; message?: string }
  return candidate.body?.detail || candidate.message || fallback
}

function ModelPicker({
  value,
  onChange,
}: {
  value: ModelBinding
  onChange: (value: ModelBinding) => void
}) {
  const { t } = useTranslation()
  const providersQuery = useProviders()
  const providers = (providersQuery.data?.items ?? []).filter((provider) =>
    CLAUDE_RUNTIME_PROVIDER_TYPES.has(provider.type),
  )
  const provider = providers.find((item) => item.id === value.providerId)
  const models = providerModels(provider)
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <Select
          value={value.providerId || undefined}
          disabled={providersQuery.isLoading || providers.length === 0}
          onValueChange={(providerId) => {
            const available = providerModels(
              providers.find((item) => item.id === providerId),
            )
            onChange({
              providerId,
              modelName: available.length === 1 ? available[0] : "",
            })
          }}
        >
          <SelectTrigger className="min-w-0">
            <SelectValue
              placeholder={
                providersQuery.isLoading
                  ? t("paper.model.loadingProviders")
                  : providers.length === 0
                    ? t("paper.model.noProviders")
                    : t("paper.model.provider")
              }
            />
          </SelectTrigger>
          {providers.length > 0 && (
            <SelectContent>
              {providers.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          )}
        </Select>
        <Select
          value={value.modelName || undefined}
          disabled={!provider || models.length === 0}
          onValueChange={(modelName) => onChange({ ...value, modelName })}
        >
          <SelectTrigger className="min-w-0">
            <SelectValue
              placeholder={
                !provider
                  ? t("paper.model.selectProviderFirst")
                  : models.length === 0
                    ? t("paper.model.noModels")
                    : t("paper.model.model")
              }
            />
          </SelectTrigger>
          {models.length > 0 && (
            <SelectContent>
              {models.map((model) => (
                <SelectItem key={model} value={model}>
                  {model}
                </SelectItem>
              ))}
            </SelectContent>
          )}
        </Select>
      </div>
      {providersQuery.isError ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-xs text-destructive">
          {t("paper.model.providersLoadFailed")}
        </p>
      ) : !providersQuery.isLoading && providers.length === 0 ? (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-700 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-300">
          {t("paper.model.noProvidersHint")}
        </p>
      ) : provider && models.length === 0 ? (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-700 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-300">
          {t("paper.model.noModelsHint")}
        </p>
      ) : null}
    </div>
  )
}

function PaperProjectsEmptyState({
  filtered,
  mode,
  onClear,
  onCreate,
}: {
  filtered: boolean
  mode: ResearchWorkspaceMode
  onClear: () => void
  onCreate: () => void
}) {
  const { t } = useTranslation()
  const ModeIcon = RESEARCH_WORKSPACE_MODE_ICONS[mode]

  if (filtered) {
    return (
      <section className="grid min-h-72 place-items-center rounded-2xl border border-dashed bg-muted/15 px-6 text-center">
        <div className="max-w-md">
          <span className="mx-auto grid size-12 place-items-center rounded-full border bg-background text-muted-foreground shadow-sm">
            <Search className="size-5" />
          </span>
          <h2 className="mt-4 text-base font-semibold">
            {t("paper.workspace.noResultsTitle")}
          </h2>
          <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
            {t("paper.workspace.noResultsDescription")}
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <Button size="sm" variant="outline" onClick={onClear}>
              <X className="size-3.5" />
              {t("paper.workspace.clearSearch")}
            </Button>
            <Button size="sm" onClick={onCreate}>
              <Plus className="size-3.5" />
              {t("paper.workspace.create")}
            </Button>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="relative isolate min-h-[28rem] overflow-hidden rounded-2xl border bg-card px-6 py-10 shadow-sm sm:px-10">
      <div
        className="pointer-events-none absolute inset-0 -z-10 opacity-30 [background-image:linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] [background-size:32px_32px] [mask-image:linear-gradient(to_bottom,black,transparent_82%)]"
        aria-hidden="true"
      />
      <div
        className="pointer-events-none absolute -right-24 -top-24 -z-10 size-72 rounded-full bg-primary/10 blur-3xl"
        aria-hidden="true"
      />
      <div className="mx-auto flex max-w-4xl flex-col items-center text-center">
        <span className="inline-flex items-center gap-2 rounded-full border bg-background/85 px-3 py-1 text-[11px] font-medium text-muted-foreground shadow-sm backdrop-blur">
          <Sparkles className="size-3.5 text-primary" />
          {t(`paper.workspace.navigation.${mode}`)}
        </span>
        <span className="mt-6 grid size-16 place-items-center rounded-2xl border border-primary/20 bg-primary/10 text-primary shadow-lg shadow-primary/10">
          <ModeIcon className="size-8" />
        </span>
        <h2 className="mt-5 text-balance text-2xl font-semibold tracking-tight sm:text-3xl">
          {t("paper.workspace.modeEmptyTitle", {
            mode: t(`paper.workspace.navigation.${mode}`),
          })}
        </h2>
        <p className="mt-3 max-w-2xl text-pretty text-sm leading-6 text-muted-foreground sm:text-base">
          {t(`paper.workspace.modeDescription.${mode}`)}
        </p>
        <Button className="mt-6" onClick={onCreate}>
          <Plus className="size-4" />
          {t("paper.workspace.emptyCreate")}
        </Button>
      </div>
    </section>
  )
}

function PaperProjectManager({
  mode,
  onOpen,
}: {
  mode: ResearchWorkspaceMode
  onOpen: (id: string) => void
}) {
  const { t } = useTranslation()
  const [search, setSearch] = useState("")
  const [createOpen, setCreateOpen] = useState(false)
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const workspacesQuery = usePaperWorkspaces(search)
  const workspaces = (workspacesQuery.data?.items ?? []).filter(
    (item) => item.mode === mode,
  )
  const createWorkspace = useCreatePaperWorkspace()
  const deleteWorkspace = useDeletePaperWorkspace()
  const ModeIcon = RESEARCH_WORKSPACE_MODE_ICONS[mode]

  return (
    <>
      <main className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 flex-wrap items-start gap-3 pb-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2.5">
              <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-primary/20 bg-primary/10 text-primary shadow-sm">
                <ModeIcon className="size-5" />
              </span>
              <h1 className="text-xl font-semibold tracking-tight">
                {t(`paper.workspace.navigation.${mode}`)}
              </h1>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {t(`paper.workspace.modeDescription.${mode}`)}
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> {t("paper.workspace.create")}
          </Button>
        </div>
        <div className="relative mb-4 max-w-sm shrink-0">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("paper.workspace.search")}
            className="pl-9"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1">
          {workspacesQuery.isLoading ? (
            <div className="grid min-h-64 place-items-center">
              <Loader2 className="size-6 animate-spin text-primary" />
            </div>
          ) : workspacesQuery.isError ? (
            <div className="grid min-h-64 place-items-center rounded-xl border border-dashed text-center">
              <div>
                <p className="text-sm text-muted-foreground">
                  {t("paper.workspace.loadFailed")}
                </p>
                <Button
                  className="mt-3"
                  size="sm"
                  variant="outline"
                  onClick={() => void workspacesQuery.refetch()}
                >
                  {t("common.retry")}
                </Button>
              </div>
            </div>
          ) : workspaces.length ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {workspaces.map((item) => (
                <article
                  key={item.id}
                  className="group relative flex min-h-48 flex-col overflow-hidden rounded-xl border bg-card p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/60 hover:shadow-lg hover:shadow-primary/10"
                >
                  <div
                    className="pointer-events-none absolute -right-12 -top-12 size-32 rounded-full bg-primary/10 opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100"
                    aria-hidden="true"
                  />
                  <div className="flex items-start gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-md bg-primary/10 text-primary transition-transform duration-300 group-hover:rotate-3 group-hover:scale-110">
                      <Microscope className="size-5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2
                        className="line-clamp-2 font-semibold"
                        title={item.title}
                      >
                        {item.title}
                      </h2>
                      <Badge
                        variant={
                          item.active_source_version_id
                            ? "secondary"
                            : "outline"
                        }
                        className="mt-2 text-[10px]"
                      >
                        {item.active_source_version_id
                          ? t("paper.workspace.hasSource")
                          : t("paper.workspace.noSource")}
                      </Badge>
                      <Badge
                        variant="outline"
                        className="ml-1 mt-2 text-[10px]"
                      >
                        {t(`paper.workspace.mode.${item.mode}`)}
                      </Badge>
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-8 opacity-0 transition-opacity group-hover:opacity-100"
                      title={t("common.delete")}
                      onClick={(event) => {
                        event.stopPropagation()
                        if (!window.confirm(t("paper.workspace.deleteConfirm")))
                          return
                        void deleteWorkspace.mutateAsync(item.id)
                      }}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                  <p className="mt-3 line-clamp-2 min-h-10 text-sm text-muted-foreground">
                    {item.description || t("paper.workspace.noDescription")}
                  </p>
                  <div className="mt-auto flex items-center justify-between gap-3 pt-4 text-xs text-muted-foreground">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <Calendar className="size-3 shrink-0" />
                      <span className="truncate">
                        {formatDate(item.updated_time)}
                      </span>
                    </span>
                    <button
                      type="button"
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border bg-background px-3 py-1.5 font-medium transition-colors group-hover:border-primary group-hover:bg-primary group-hover:text-primary-foreground"
                      onClick={() => onOpen(item.id)}
                    >
                      {item.active_source_version_id
                        ? t("paper.workspace.open")
                        : t("paper.workspace.addSource")}
                      <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <PaperProjectsEmptyState
              filtered={Boolean(search.trim())}
              mode={mode}
              onClear={() => setSearch("")}
              onCreate={() => setCreateOpen(true)}
            />
          )}
        </div>
      </main>
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("paper.workspace.createTitle")}</DialogTitle>
            <DialogDescription>
              {t("paper.workspace.createDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t("paper.workspace.name")}</Label>
              <Input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={t("paper.workspace.namePlaceholder")}
                maxLength={255}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("paper.workspace.description")}</Label>
              <Textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder={t("paper.workspace.descriptionPlaceholder")}
                className="min-h-24 resize-none"
                maxLength={4000}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={!title.trim() || createWorkspace.isPending}
              onClick={async () => {
                const created = await createWorkspace.mutateAsync({
                  title: title.trim(),
                  description: description.trim() || null,
                  mode,
                })
                setCreateOpen(false)
                setTitle("")
                setDescription("")
                onOpen(created.id)
              }}
            >
              {t("paper.workspace.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function buildPaperFileTree(paths: string[]): FileTreeNode[] {
  const root: FileTreeNode[] = []
  for (const sourcePath of paths) {
    const parts = sourcePath.split("/").filter(Boolean)
    let children = root
    let currentPath = ""
    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part
      const isFile = index === parts.length - 1
      if (isFile && part === PAPER_DIRECTORY_MARKER) return
      let node = children.find((item) => item.name === part)
      if (!node) {
        node = {
          name: part,
          path: currentPath,
          type: isFile ? "file" : "directory",
          children: isFile ? null : [],
        }
        children.push(node)
      }
      if (!isFile) {
        node.children ??= []
        children = node.children
      }
    })
  }
  return root
}

function SourceNavigator({
  manifest,
  selectedPath,
  onSelect,
  onDelete,
  onCreateFile,
  onCreateFolder,
  onUpload,
  uploading,
  locked,
  collapsed,
  onToggle,
  collapsible = true,
  canDeletePath,
}: {
  manifest: string[]
  selectedPath: string | null
  onSelect: (path: string) => void
  onDelete: (path: string) => void
  onCreateFile: (path: string) => Promise<void>
  onCreateFolder: (path: string) => Promise<void>
  onUpload: () => void
  uploading: boolean
  locked: boolean
  collapsed: boolean
  onToggle: () => void
  collapsible?: boolean
  canDeletePath?: (path: string, isDirectory: boolean) => boolean
}) {
  const { t } = useTranslation()
  const [treeTarget, setTreeTarget] = useState<{
    path: string
    isDirectory: boolean
  } | null>(null)
  const [createKind, setCreateKind] = useState<"file" | "folder" | null>(null)
  const [newEntryName, setNewEntryName] = useState("")
  const tree = useMemo(() => buildPaperFileTree(manifest), [manifest])
  useEffect(() => {
    if (selectedPath) setTreeTarget({ path: selectedPath, isDirectory: false })
  }, [selectedPath])
  const parentPath = treeTarget
    ? treeTarget.isDirectory
      ? treeTarget.path
      : treeTarget.path.includes("/")
        ? treeTarget.path.slice(0, treeTarget.path.lastIndexOf("/"))
        : ""
    : ""
  const trimmedEntryName = newEntryName.trim()
  const targetEntryPath = parentPath
    ? `${parentPath}/${trimmedEntryName}`
    : trimmedEntryName
  const entryExists =
    manifest.includes(targetEntryPath) ||
    manifest.some((path) => path.startsWith(`${targetEntryPath}/`))
  const invalidEntryName =
    !trimmedEntryName ||
    trimmedEntryName === "." ||
    trimmedEntryName === ".." ||
    trimmedEntryName === PAPER_DIRECTORY_MARKER ||
    /[/\\]/.test(trimmedEntryName) ||
    entryExists ||
    (createKind === "file" &&
      !CREATABLE_PAPER_FILE_SUFFIXES.some((suffix) =>
        trimmedEntryName.toLowerCase().endsWith(suffix),
      ))
  const createEntry = async () => {
    if (!createKind || invalidEntryName) return
    try {
      if (createKind === "file") await onCreateFile(targetEntryPath)
      else await onCreateFolder(targetEntryPath)
      toast.success(
        t(
          createKind === "file"
            ? "paper.source.fileCreated"
            : "paper.source.folderCreated",
        ),
      )
      setCreateKind(null)
      setNewEntryName("")
    } catch (error) {
      toast.error(errorMessage(error, t("paper.source.createFailed")))
    }
  }
  if (collapsed && collapsible) {
    return (
      <section className="flex h-full flex-col items-center border-r bg-muted/10 py-2">
        <Button
          size="icon"
          variant="ghost"
          className="size-8"
          title={t("paper.editor.expandFiles")}
          onClick={onToggle}
        >
          <PanelLeftOpen className="size-4" />
        </Button>
      </section>
    )
  }
  return (
    <>
      <section className="flex h-full w-full min-w-0 flex-col overflow-hidden border-r bg-muted/10">
        <div className="flex h-11 items-center justify-between border-b px-2.5">
          <span className="truncate text-xs font-semibold text-foreground">
            {t("paper.editor.files")}
          </span>
          <div className="flex shrink-0 items-center gap-0.5">
            <Button
              size="icon"
              variant="ghost"
              className="size-7 shrink-0 text-primary"
              title={t("paper.source.createFile")}
              disabled={uploading || locked}
              onClick={() => {
                setNewEntryName("section.typ")
                setCreateKind("file")
              }}
            >
              <FilePlus2 className="size-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-7 shrink-0 text-primary"
              title={t("paper.source.createFolder")}
              disabled={uploading || locked}
              onClick={() => {
                setNewEntryName("")
                setCreateKind("folder")
              }}
            >
              <FolderPlus className="size-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-7 shrink-0 text-muted-foreground"
              title={t("paper.source.import")}
              disabled={uploading || locked}
              onClick={onUpload}
            >
              <Upload className="size-3.5" />
            </Button>
            {collapsible && (
              <Button
                size="icon"
                variant="ghost"
                className="size-7 shrink-0"
                title={t("paper.editor.collapseFiles")}
                onClick={onToggle}
              >
                <PanelLeftClose className="size-3.5" />
              </Button>
            )}
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1 [&>[data-radix-scroll-area-viewport]>div]:!block [&>[data-radix-scroll-area-viewport]>div]:!w-full">
          <div className="min-h-full p-2 text-xs">
            <FileTreeView
              tree={tree}
              selectedPath={treeTarget?.path ?? selectedPath}
              onSelectFile={(path, isDirectory) => {
                setTreeTarget({ path, isDirectory })
                if (
                  !isDirectory &&
                  EDITABLE_SUFFIXES.some((suffix) =>
                    path.toLowerCase().endsWith(suffix),
                  )
                )
                  onSelect(path)
              }}
              onDeleteFile={locked ? undefined : onDelete}
              onDeleteFolder={locked ? undefined : onDelete}
              protectTopLevelDirectories={false}
              confirmDelete={false}
              canDeletePath={canDeletePath}
            />
          </div>
        </ScrollArea>
      </section>
      <Dialog
        open={Boolean(createKind)}
        onOpenChange={(open) => {
          if (!open) {
            setCreateKind(null)
            setNewEntryName("")
          }
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {t(
                createKind === "folder"
                  ? "paper.source.createFolder"
                  : "paper.source.createFile",
              )}
            </DialogTitle>
            <DialogDescription>
              {parentPath
                ? t("paper.source.createIn", { path: parentPath })
                : t("paper.source.createAtRoot")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Label htmlFor="paper-source-entry-name">
              {t("paper.source.name")}
            </Label>
            <Input
              id="paper-source-entry-name"
              autoFocus
              value={newEntryName}
              placeholder={
                createKind === "folder"
                  ? t("paper.source.folderPlaceholder")
                  : t("paper.source.filePlaceholder")
              }
              onChange={(event) => setNewEntryName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createEntry()
              }}
            />
            {entryExists ? (
              <p className="text-xs text-destructive">
                {t("paper.source.nameExists")}
              </p>
            ) : createKind === "file" && invalidEntryName && newEntryName ? (
              <p className="text-xs text-muted-foreground">
                {t("paper.source.fileNameHint")}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              disabled={invalidEntryName || uploading}
              onClick={() => void createEntry()}
            >
              {uploading && <Loader2 className="size-4 animate-spin" />}
              {t("common.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function PaperSourceContextPanel({
  context,
  onSectionSelect,
}: {
  context: PaperSourceContext
  onSectionSelect?: (section: string) => void
}) {
  const { t } = useTranslation()

  return (
    <aside className="not-prose mx-5 mt-5 min-w-0 rounded-xl border border-sky-500/20 bg-sky-500/[0.045] p-4 dark:border-sky-400/25 dark:bg-sky-400/10">
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-sky-700 dark:text-sky-300">
        {t("paper.editor.sourceContext")}
      </p>
      <BoundaryRichText
        id={`paper-source-context-${context.path.replace(/[^a-zA-Z0-9_-]/g, "-")}`}
        content={context.role}
        className="mt-2 text-sm"
      />
      {context.sections.length > 0 && (
        <div className="mt-3 flex min-w-0 flex-wrap gap-2">
          {context.sections.map((section, index) => (
            <button
              key={`${section}-${index}`}
              type="button"
              disabled={!onSectionSelect}
              title={
                onSectionSelect
                  ? t("paper.editor.locateSection", { section })
                  : undefined
              }
              className="max-w-full rounded-md border bg-background/80 px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 disabled:cursor-default disabled:opacity-80"
              onClick={() => onSectionSelect?.(section)}
            >
              <span className="mr-1.5 text-muted-foreground">
                {t("paper.editor.mappedSection")}
              </span>
              <span className="break-words [overflow-wrap:anywhere]">
                {section}
              </span>
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}

function normalizeSectionLabel(value: string) {
  return value
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
}

function normalizeSourcePath(value: string) {
  return value.replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\//, "")
}

function initialPaperFileContent(path: string) {
  const suffix = path.toLowerCase().slice(path.lastIndexOf("."))
  if (suffix === ".json") return "{}\n"
  if (suffix === ".typ") return "// New Typst document.\n"
  if ([".tex", ".bib", ".bst", ".cls", ".sty"].includes(suffix))
    return "% New research source file.\n"
  return "# New research document\n"
}

function paperSourceContexts(value: unknown): PaperSourceContext[] {
  if (!Array.isArray(value)) return []

  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return []

    const source = item as Record<string, unknown>
    const path = typeof source.path === "string" ? source.path.trim() : ""
    if (!path) return []

    return [
      {
        path,
        role: typeof source.role === "string" ? source.role.trim() : "",
        sections: stringList(source.sections),
      },
    ]
  })
}

function PaperSourceManager({
  manifest,
  sourceVersionId,
  sourceHash,
  selectedPath,
  sourceContent,
  sourceContext,
  sourceLoading,
  typstPreview = false,
  typstEntryPath,
  typstDownloadEnabled = false,
  editing,
  editable = true,
  sourceDraft,
  highlightStartLine,
  highlightEndLine,
  sidebarExpanded,
  saving,
  onSelect,
  onDelete,
  onCreateFile,
  onCreateFolder,
  onUpload,
  uploading,
  locked = false,
  onSidebarChange,
  onEdit,
  onCancelEdit,
  onDraftChange,
  onSave,
  showNavigator = true,
  canDeletePath,
}: {
  manifest: string[]
  sourceVersionId: string
  sourceHash: string
  selectedPath: string | null
  sourceContent: string
  sourceContext: PaperSourceContext | null
  sourceLoading: boolean
  typstPreview?: boolean
  typstEntryPath?: string | null
  typstDownloadEnabled?: boolean
  editing: boolean
  editable?: boolean
  sourceDraft: string
  highlightStartLine?: number | null
  highlightEndLine?: number | null
  sidebarExpanded: boolean
  saving: boolean
  onSelect: (path: string) => void
  onDelete: (path: string) => void
  onCreateFile: (path: string) => Promise<void>
  onCreateFolder: (path: string) => Promise<void>
  onUpload: () => void
  uploading: boolean
  locked?: boolean
  onSidebarChange: (expanded: boolean) => void
  onEdit: () => void
  onCancelEdit: () => void
  onDraftChange: (value: string) => void
  onSave: () => void
  showNavigator?: boolean
  canDeletePath?: (path: string, isDirectory: boolean) => boolean
}) {
  const { t } = useTranslation()
  const suffix = selectedPath?.toLowerCase() ?? ""
  const isMarkdown = suffix.endsWith(".md") || suffix.endsWith(".markdown")
  const isTypst = suffix.endsWith(".typ")
  const continuousEditing = Boolean(typstPreview && selectedPath && editable)
  const editorActive = editing || continuousEditing
  const sourceDirty = editorActive && sourceDraft !== sourceContent
  const previewRef = useRef<HTMLElement>(null)
  const previewComponents = useMemo(
    () =>
      makeMarkdownComponents(
        `paper-source-${selectedPath?.replace(/[^a-zA-Z0-9_-]/g, "-") ?? "preview"}`,
      ),
    [selectedPath],
  )
  const locateSection = (section: string) => {
    const targetLabel = normalizeSectionLabel(section)
    const heading = Array.from(
      previewRef.current?.querySelectorAll("h1, h2, h3, h4, h5, h6") ?? [],
    ).find((candidate) => {
      const candidateLabel = normalizeSectionLabel(candidate.textContent ?? "")
      return (
        candidateLabel === targetLabel ||
        candidateLabel.includes(targetLabel) ||
        targetLabel.includes(candidateLabel)
      )
    })
    heading?.scrollIntoView({ behavior: "smooth", block: "start" })
  }

  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden bg-background">
      {showNavigator && (
        <div
          className={cn(
            "shrink-0 overflow-hidden transition-[width] duration-200 ease-out",
            sidebarExpanded ? "w-60" : "w-11",
          )}
        >
          <SourceNavigator
            manifest={manifest}
            selectedPath={selectedPath}
            collapsed={!sidebarExpanded}
            onToggle={() => onSidebarChange(!sidebarExpanded)}
            canDeletePath={canDeletePath}
            onSelect={onSelect}
            onDelete={onDelete}
            onCreateFile={onCreateFile}
            onCreateFolder={onCreateFolder}
            onUpload={onUpload}
            uploading={uploading}
            locked={locked}
          />
        </div>
      )}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex min-h-12 shrink-0 items-center justify-end gap-2 border-b px-4">
          {!continuousEditing && selectedPath && !editing && (
            <Button
              size="sm"
              variant="ghost"
              disabled={locked || !editable}
              title={!editable ? t("paper.workflow.readOnlyStage") : undefined}
              onClick={onEdit}
            >
              <Pencil className="size-3.5" /> {t("paper.editor.edit")}
            </Button>
          )}
          {editing && !continuousEditing && (
            <Button size="sm" variant="ghost" onClick={onCancelEdit}>
              <X className="size-3.5" /> {t("common.cancel")}
            </Button>
          )}
          {editorActive && (
            <Button
              size="sm"
              disabled={!sourceDirty || !sourceDraft || saving || locked}
              onClick={onSave}
            >
              {saving ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Save className="size-3.5" />
              )}
              {t("common.save")}
            </Button>
          )}
        </div>

        {!selectedPath ? (
          <div className="grid min-h-0 flex-1 place-items-center p-8">
            <div className="max-w-md text-center text-sm text-muted-foreground">
              <FileText className="mx-auto mb-3 size-8 opacity-60" />
              {t("paper.editor.selectFile")}
            </div>
          </div>
        ) : isTypst && typstPreview ? (
          <ResizableGroup
            id="proposal-typst-editor-preview"
            orientation="horizontal"
            className="min-h-0 flex-1 overflow-hidden"
            // Widen the library's own resize target instead of overlaying an
            // extra hit area: a custom overlay advertises a drag band the
            // library does not act on, so grabbing there does nothing.
            resizeTargetMinimumSize={RESIZE_TARGET_MINIMUM_SIZE}
          >
            <ResizablePanel
              id="typst-editor"
              defaultSize="48%"
              minSize="320px"
              className="min-h-0 min-w-0 overflow-hidden"
            >
              {sourceLoading ? (
                <div className="grid h-full place-items-center">
                  <Loader2 className="size-5 animate-spin text-primary" />
                </div>
              ) : (
                <ArtifactCodeEditor
                  filePath={selectedPath}
                  value={editorActive ? sourceDraft : sourceContent}
                  readOnly={!editorActive || locked || !editable}
                  onChange={onDraftChange}
                  className="h-full min-h-0"
                />
              )}
            </ResizablePanel>
            <ResizableSeparator className="group relative w-px bg-border outline-none transition-colors hover:bg-primary focus-visible:bg-primary" />
            <ResizablePanel
              id="typst-preview"
              defaultSize="52%"
              minSize="360px"
              className="min-h-0 min-w-0 overflow-hidden"
            >
              <TypstLivePreview
                sourceVersionId={sourceVersionId}
                sourceHash={sourceHash}
                activePath={selectedPath}
                entryPath={typstEntryPath || selectedPath}
                source={editorActive ? sourceDraft : sourceContent}
                sourceLoading={sourceLoading}
                downloadName="research-proposal.pdf"
                downloadEnabled={typstDownloadEnabled}
              />
            </ResizablePanel>
          </ResizableGroup>
        ) : (
          <Tabs
            key={selectedPath}
            defaultValue={
              editing ? "source" : isMarkdown ? "preview" : "source"
            }
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="border-b px-4 py-1.5">
              <TabsList className="h-8">
                {isMarkdown && (
                  <TabsTrigger value="preview" className="text-xs">
                    {t("paper.editor.preview")}
                  </TabsTrigger>
                )}
                <TabsTrigger value="source" className="text-xs">
                  {t("paper.editor.source")}
                </TabsTrigger>
              </TabsList>
            </div>
            {isMarkdown && (
              <TabsContent
                value="preview"
                className="m-0 min-h-0 flex-1 overflow-hidden"
              >
                <ScrollArea className="h-full">
                  {sourceContext && (
                    <PaperSourceContextPanel
                      context={sourceContext}
                      onSectionSelect={locateSection}
                    />
                  )}
                  <article
                    ref={previewRef}
                    className={cn(
                      "prose prose-sm w-full max-w-none px-5 py-6",
                      PAPER_MARKDOWN_THEME,
                    )}
                  >
                    <ReactMarkdown
                      remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                      rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                      components={previewComponents}
                    >
                      {sourceContent}
                    </ReactMarkdown>
                  </article>
                </ScrollArea>
              </TabsContent>
            )}
            <TabsContent
              value="source"
              className="m-0 min-h-0 flex-1 overflow-hidden"
            >
              {editorActive ? (
                <ArtifactCodeEditor
                  filePath={selectedPath}
                  value={sourceDraft}
                  readOnly={locked || !editable}
                  onChange={onDraftChange}
                  className="h-full min-h-0"
                />
              ) : (
                <ScrollArea className="h-full">
                  {sourceLoading ? (
                    <div className="grid h-full place-items-center">
                      <Loader2 className="size-5 animate-spin text-primary" />
                    </div>
                  ) : (
                    <>
                      {sourceContext && (
                        <PaperSourceContextPanel context={sourceContext} />
                      )}
                      <pre className="min-h-full whitespace-pre-wrap break-words p-6 font-mono text-xs leading-6">
                        {sourceContent.split("\n").map((line, index) => {
                          const lineNumber = index + 1
                          const highlighted =
                            Boolean(highlightStartLine) &&
                            lineNumber >= (highlightStartLine ?? 0) &&
                            lineNumber <=
                              (highlightEndLine ?? highlightStartLine ?? 0)
                          return (
                            <span
                              key={`${lineNumber}-${line.slice(0, 24)}`}
                              className={cn(
                                "block min-h-6 rounded-sm px-2",
                                highlighted &&
                                  "-mx-2 border-l-2 border-amber-500 bg-amber-500/10 dark:border-amber-400 dark:bg-amber-400/10",
                              )}
                            >
                              {line || " "}
                            </span>
                          )
                        })}
                      </pre>
                    </>
                  )}
                </ScrollArea>
              )}
            </TabsContent>
          </Tabs>
        )}
      </section>
    </div>
  )
}

/** Id of the dock's source panel, which also keys its entry in the layout. */
const EDITOR_PANEL_ID = "paper-dock-editor"

/**
 * Grab band around a separator. The library default is only 10px for a mouse —
 * a few pixels either side of the hairline — which reads as "the handle does
 * not work" whenever the pointer lands slightly off it.
 */
const RESIZE_TARGET_MINIMUM_SIZE = { coarse: 28, fine: 16 }

function PaperDocumentDock({
  manifest,
  sourceVersionId,
  sourceHash,
  selectedPath,
  sourceContent,
  sourceContext,
  sourceLoading,
  sourceDraft,
  sourceDirty,
  sourceSaveError,
  sourceSaving,
  typstEntryPath,
  typstDownloadEnabled,
  typstPreview = true,
  editable,
  locked,
  highlightStartLine,
  highlightEndLine,
  workspaceKey,
  onSelect,
  onDelete,
  onCreateFile,
  onCreateFolder,
  onUpload,
  uploading,
  canDeletePath,
  onDraftChange,
  embedded = false,
}: {
  manifest: string[]
  sourceVersionId: string
  sourceHash: string
  selectedPath: string | null
  sourceContent: string
  sourceContext: PaperSourceContext | null
  sourceLoading: boolean
  sourceDraft: string
  sourceDirty: boolean
  sourceSaveError: boolean
  sourceSaving: boolean
  typstEntryPath: string | null
  typstDownloadEnabled: boolean
  typstPreview?: boolean
  editable: boolean
  locked: boolean
  highlightStartLine?: number | null
  highlightEndLine?: number | null
  /** Scopes the remembered panel state to one research workspace. */
  workspaceKey: string
  onSelect: (path: string) => void
  onDelete: (path: string) => void
  onCreateFile: (path: string) => Promise<void>
  onCreateFolder: (path: string) => Promise<void>
  onUpload: () => void
  uploading: boolean
  canDeletePath?: (path: string, isDirectory: boolean) => boolean
  onDraftChange: (value: string) => void
  embedded?: boolean
}) {
  const { t } = useTranslation()
  const suffix = selectedPath?.toLowerCase() ?? ""
  const isMarkdown = suffix.endsWith(".md") || suffix.endsWith(".markdown")
  const isTypst = suffix.endsWith(".typ")
  const showTypstPreview = typstPreview && isTypst
  const previewComponents = useMemo(
    () =>
      makeMarkdownComponents(
        `paper-dock-${selectedPath?.replace(/[^a-zA-Z0-9_-]/g, "-") ?? "preview"}`,
      ),
    [selectedPath],
  )

  // The editor starts collapsed and remembers what the reader did with it.
  // "Reading the preview" is the common case and "changing the source" is the
  // exception, so the editor should not claim half the panel by default — but a
  // reader who did open it should not have to reopen it on every visit.
  const editorPanelRef = usePanelRef()
  const [editorCollapsed, setEditorCollapsed] = usePersistentState(
    `paper:${workspaceKey}:editor-collapsed`,
    true,
  )
  const [editorHeight, setEditorHeight] = usePersistentState(
    `paper:${workspaceKey}:editor-height`,
    "38%",
  )
  // The file tree remembers its own state independently of the editor panel:
  // collapsing the editor should not also throw away the tree arrangement, and
  // a reader who keeps the tree open while the editor is shut should find it
  // that way on the next visit.
  const [filesCollapsed, setFilesCollapsed] = usePersistentState(
    `paper:${workspaceKey}:files-collapsed`,
    false,
  )

  // The panel's own collapse state is the source of truth once mounted — it can
  // also collapse from a drag, not only from the toggle — so mirror it out
  // rather than assuming the toggle is the only way in.
  const syncEditorCollapsed = useCallback(() => {
    const panel = editorPanelRef.current
    if (!panel) return
    setEditorCollapsed(panel.isCollapsed())
  }, [editorPanelRef, setEditorCollapsed])

  // The panel always lays out at `defaultSize`, so the mirror above can start
  // out disagreeing with it (the remembered value says "collapsed" while the
  // panel is open) and nothing would correct it until some resize event
  // happened to fire. Until then the separator stays disabled and the enlarged
  // drag target is not even rendered, so the reader's first drag does nothing.
  useEffect(() => {
    syncEditorCollapsed()
  }, [syncEditorCollapsed])

  // Remember the dragged height for an open editor only: a collapsed panel
  // reports its placeholder height, and storing that would reopen it as a
  // sliver. This runs after the layout settled rather than on every resize
  // event, because feeding the live drag position back into `defaultSize` makes
  // the library re-apply the default layout — the panel then stops following
  // the pointer and a drag only moves it a few pixels.
  const rememberEditorHeight = useCallback(
    (layout: Layout) => {
      if (editorPanelRef.current?.isCollapsed()) return
      // Rounded so a sub-pixel drag does not churn localStorage on every frame.
      const percent = `${Math.round((layout[EDITOR_PANEL_ID] ?? 0) * 10) / 10}%`
      setEditorHeight((current) => (current === percent ? current : percent))
    },
    [editorPanelRef, setEditorHeight],
  )

  return (
    <aside
      className={cn(
        "hidden h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background lg:flex",
        !embedded && "border-l",
      )}
    >
      <ResizableGroup
        id={`paper-dock-${workspaceKey}`}
        orientation="vertical"
        className="min-h-0 flex-1 overflow-hidden"
        onLayoutChanged={rememberEditorHeight}
        // Widen the library's own resize target instead of overlaying an extra
        // hit area: a custom overlay advertises a drag band the library does not
        // act on, so grabbing just outside the separator would do nothing.
        resizeTargetMinimumSize={RESIZE_TARGET_MINIMUM_SIZE}
      >
        <ResizablePanel
          id="paper-dock-preview"
          minSize="8rem"
          className="min-h-0 min-w-0 overflow-hidden"
        >
          <section className="flex h-full min-h-0 flex-col overflow-hidden">
            <div className="min-h-0 flex-1 overflow-hidden bg-muted/10">
              {!selectedPath ? (
                <div className="grid h-full place-items-center p-6 text-center text-xs text-muted-foreground">
                  <div>
                    <FileText className="mx-auto mb-2 size-7 opacity-60" />
                    {t("paper.editor.selectFile")}
                  </div>
                </div>
              ) : sourceLoading && !showTypstPreview ? (
                <div className="grid h-full place-items-center">
                  <Loader2 className="size-5 animate-spin text-primary" />
                </div>
              ) : showTypstPreview ? (
                <TypstLivePreview
                  sourceVersionId={sourceVersionId}
                  sourceHash={sourceHash}
                  activePath={selectedPath}
                  entryPath={typstEntryPath || selectedPath}
                  source={sourceContent}
                  sourceLoading={sourceLoading}
                  downloadName="research-proposal.pdf"
                  downloadEnabled={typstDownloadEnabled}
                />
              ) : isMarkdown ? (
                <ScrollArea className="h-full bg-background">
                  {sourceContext && (
                    <PaperSourceContextPanel context={sourceContext} />
                  )}
                  <article
                    className={cn(
                      "prose prose-sm w-full max-w-none px-5 py-5",
                      PAPER_MARKDOWN_THEME,
                    )}
                  >
                    <ReactMarkdown
                      remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                      rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                      components={previewComponents}
                    >
                      {sourceContent}
                    </ReactMarkdown>
                  </article>
                </ScrollArea>
              ) : (
                <ScrollArea className="h-full bg-background">
                  <pre className="min-h-full whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-5">
                    {sourceContent.split("\n").map((line, index) => {
                      const lineNumber = index + 1
                      const highlighted =
                        Boolean(highlightStartLine) &&
                        lineNumber >= (highlightStartLine ?? 0) &&
                        lineNumber <=
                          (highlightEndLine ?? highlightStartLine ?? 0)
                      return (
                        <span
                          key={`${lineNumber}-${line.slice(0, 24)}`}
                          className={cn(
                            "block rounded-sm px-1",
                            highlighted &&
                              "-mx-1 border-l-2 border-amber-500 bg-amber-500/10",
                          )}
                        >
                          {line || " "}
                        </span>
                      )
                    })}
                  </pre>
                </ScrollArea>
              )}
            </div>
          </section>
        </ResizablePanel>

        <ResizableSeparator
          // Only meaningful while the editor is open. While collapsed the header
          // below is the only affordance, and a drag target there would compete
          // with the toggle button sitting on top of it.
          className={cn(
            "group relative h-px bg-border outline-none transition-colors focus-visible:bg-primary",
            !editorCollapsed && "hover:bg-primary",
          )}
          disabled={editorCollapsed}
        >
          {!editorCollapsed && (
            <span className="absolute inset-x-0 -top-1.5 h-4 cursor-row-resize" />
          )}
        </ResizableSeparator>

        <ResizablePanel
          id={EDITOR_PANEL_ID}
          panelRef={editorPanelRef}
          collapsible
          collapsedSize="2.75rem"
          defaultSize={editorHeight}
          minSize="10rem"
          onResize={() => {
            syncEditorCollapsed()
          }}
          className="min-h-0 min-w-0 overflow-hidden"
        >
          <section className="flex h-full min-h-0 flex-col overflow-hidden border-t">
            <button
              type="button"
              onClick={() => {
                if (editorCollapsed) editorPanelRef.current?.expand()
                else editorPanelRef.current?.collapse()
              }}
              aria-expanded={!editorCollapsed}
              title={
                editorCollapsed
                  ? t("paper.editor.expandEditor")
                  : t("paper.editor.collapseEditor")
              }
              className="flex h-11 w-full shrink-0 items-center gap-2 border-b bg-muted/20 px-2.5 text-left transition-colors hover:bg-muted/40"
            >
              {editorCollapsed ? (
                <ChevronUp className="size-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 truncate text-xs font-semibold text-foreground">
                {t("paper.editor.sourceEditor")}
              </span>
              {editorCollapsed && (
                <span className="min-w-0 flex-1 truncate text-[10px] text-muted-foreground">
                  {selectedPath
                    ? t("paper.editor.editorCollapsedHint")
                    : t("paper.editor.selectFile")}
                </span>
              )}
              {selectedPath && editable && !editorCollapsed && (
                <span
                  className={cn(
                    "ml-auto flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground",
                    sourceSaveError && "text-destructive",
                  )}
                >
                  {sourceSaving ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : sourceSaveError ? (
                    <WifiOff className="size-3" />
                  ) : sourceDirty ? (
                    <CalendarClock className="size-3" />
                  ) : (
                    <Check className="size-3 text-emerald-600 dark:text-emerald-400" />
                  )}
                  {t(
                    sourceSaving
                      ? "paper.editor.autoSaving"
                      : sourceSaveError
                        ? "paper.editor.saveFailed"
                        : sourceDirty
                          ? "paper.editor.savePending"
                          : "paper.editor.savedState",
                  )}
                </span>
              )}
            </button>
            {/* Unmounted while collapsed: the panel is only a header tall, and
                keeping a hidden editor alive would hold a CodeMirror instance
                (and its document) for a panel nobody is looking at. It
                remounts with the draft intact, which lives in the parent. */}
            {!editorCollapsed && (
              <div className="flex min-h-0 flex-1 overflow-hidden">
                {/* The tree collapses to a rail rather than unmounting: the rail
                    carries the toggle that brings it back, and the width is
                    transitioned so the editor beside it slides rather than jumps.
                    No border here — SourceNavigator draws its own in both
                    states, and adding one would double it. */}
                <div
                  className={cn(
                    "min-h-0 min-w-0 shrink-0 overflow-hidden transition-[width] duration-200 ease-out",
                    filesCollapsed ? "w-11" : "w-[12.5rem]",
                  )}
                >
                  <SourceNavigator
                    manifest={manifest}
                    selectedPath={selectedPath}
                    collapsed={filesCollapsed}
                    onToggle={() => setFilesCollapsed((current) => !current)}
                    onSelect={onSelect}
                    onDelete={onDelete}
                    onCreateFile={onCreateFile}
                    onCreateFolder={onCreateFolder}
                    onUpload={onUpload}
                    uploading={uploading}
                    locked={locked}
                    canDeletePath={canDeletePath}
                  />
                </div>
                <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
                  <div className="min-h-0 flex-1 overflow-hidden">
                    {sourceLoading ? (
                      <div className="grid h-full place-items-center">
                        <Loader2 className="size-4 animate-spin text-primary" />
                      </div>
                    ) : selectedPath && editable ? (
                      <ArtifactCodeEditor
                        filePath={selectedPath}
                        value={sourceDraft}
                        readOnly={locked}
                        onChange={onDraftChange}
                        className="h-full min-h-0"
                      />
                    ) : (
                      <div className="grid h-full place-items-center px-4 text-center text-[11px] leading-5 text-muted-foreground">
                        {t("paper.editor.sourceUnavailable")}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
            {/* Nothing renders below the header while collapsed: the panel is
                exactly one header tall, so any hint placed here would be
                clipped rather than shown. The toggle's title carries it. */}
          </section>
        </ResizablePanel>
      </ResizableGroup>
    </aside>
  )
}

function BoundaryRichText({
  content,
  id,
  className,
}: {
  content: string
  id: string
  className?: string
}) {
  const components = useMemo(() => makeMarkdownComponents(id), [id])

  return (
    <div
      className={cn(
        "prose prose-sm min-w-0 max-w-none break-words text-[15px] leading-7 text-foreground [overflow-wrap:anywhere]",
        PAPER_MARKDOWN_THEME,
        "[&_p]:my-0 [&_ul]:my-2 [&_ol]:my-2 [&_li]:my-1",
        "[&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto",
        "[&_.katex-display]:max-w-full [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function PaperWorkbenchHeader({
  title,
  onConfigureModel,
  onConfigurePreferences,
  onEditBasics,
}: {
  title: string
  onConfigureModel?: () => void
  onConfigurePreferences?: () => void
  onEditBasics?: () => void
}) {
  const { t } = useTranslation()
  const { setHeaderCenter, setHeaderRight } = usePaperHeader()

  const center = useMemo(
    () => (
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="min-w-0 cursor-help text-center">
            <h1 className="truncate text-sm font-semibold tracking-wide text-foreground">
              {title}
            </h1>
          </div>
        </TooltipTrigger>
        <TooltipContent
          side="bottom"
          sideOffset={8}
          className="max-w-[min(32rem,80vw)] break-words"
        >
          {title}
        </TooltipContent>
      </Tooltip>
    ),
    [title],
  )
  const right = useMemo(
    () =>
      onConfigureModel || onConfigurePreferences || onEditBasics ? (
        <div className="flex items-center gap-2">
          {onEditBasics && (
            <Button size="sm" variant="ghost" onClick={onEditBasics}>
              <FileText className="size-3.5" />
              {t("paper.source.basicsTitle")}
            </Button>
          )}
          {onConfigureModel && (
            <Button size="sm" variant="outline" onClick={onConfigureModel}>
              <Settings2 className="size-3.5" />
              {t("paper.model.configure")}
            </Button>
          )}
          {onConfigurePreferences && (
            <Button
              size="sm"
              variant="outline"
              onClick={onConfigurePreferences}
            >
              <SlidersHorizontal className="size-3.5" />
              {t("paper.preferences.configure")}
            </Button>
          )}
        </div>
      ) : null,
    [onConfigureModel, onConfigurePreferences, onEditBasics, t],
  )

  useEffect(() => {
    setHeaderCenter(center)
    setHeaderRight(right)
    return () => {
      setHeaderCenter(null)
      setHeaderRight(null)
    }
  }, [center, right, setHeaderCenter, setHeaderRight])

  return null
}

export function PaperWorkbench({
  workspaceId,
  entryMode = "proposal",
}: {
  workspaceId: string
  entryMode?: ResearchWorkspaceMode
}) {
  const { t } = useTranslation()
  useHljsTheme()
  const providerItems = useProviders().data?.items
  const runtimeProviderIds = useMemo(
    () =>
      new Set(
        (providerItems ?? [])
          .filter((provider) =>
            CLAUDE_RUNTIME_PROVIDER_TYPES.has(provider.type),
          )
          .map((provider) => provider.id),
      ),
    [providerItems],
  )
  const navigate = useNavigate()
  const activeId = workspaceId
  const [activeStage, setActiveStage] = usePersistentState<PaperTool>(
    `paper:${workspaceId}:tool`,
    "formatting",
  )
  const [selectedPath, setSelectedPath] = usePersistentState<string | null>(
    `paper:${workspaceId}:selected-path`,
    null,
  )
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadPurpose, setUploadPurpose] = useState<
    "source" | "project_context"
  >("source")
  const [projectContextSkipped, setProjectContextSkipped] = usePersistentState(
    `paper:${workspaceId}:project-context-skipped`,
    false,
  )
  const [proposalEditorState, setProposalEditorState] = useState({
    workspaceId,
    stage: null as PaperTool | null,
    open: false,
  })
  const [settingsOpen, setSettingsOpen] = useState(false)
  const openModelSettings = useCallback(() => setSettingsOpen(true), [])
  const [preferencesOpen, setPreferencesOpen] = useState(false)
  const [preferencesDraft, setPreferencesDraft] =
    useState<ConversationPreferencesDraft>(DEFAULT_CONVERSATION_PREFERENCES)
  const [basicsOpen, setBasicsOpen] = useState(false)
  const openBasics = useCallback(() => setBasicsOpen(true), [])
  const [proposalBasics, setProposalBasics] = useState<ProposalBasics>({
    ...EMPTY_PROPOSAL_BASICS,
  })
  const updateWorkspace = useUpdatePaperWorkspace(workspaceId)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const openSourceUpload = () => {
    setUploadPurpose("source")
    setPendingFiles([])
    setUploadOpen(true)
  }
  const openProjectContextUpload = () => {
    setUploadPurpose("project_context")
    setPendingFiles([])
    setUploadOpen(true)
  }
  const [binding, setBinding] = useState<ModelBinding>({
    providerId: "",
    modelName: "",
  })
  const [contextTokens, setContextTokens] = useState(128_000)
  const [outputTokens, setOutputTokens] = useState(16_384)
  const [editingSource, setEditingSource] = useState(false)
  const [sourceDraft, setSourceDraft] = useState("")
  const [sourceAutoSaveError, setSourceAutoSaveError] = useState<string | null>(
    null,
  )
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const lastAutoSelectedProposalStage = useRef<string | null>(null)
  const sourceServerSnapshot = useRef<{
    path: string
    content: string
  } | null>(null)
  const submittedSourceDraft = useRef<{
    path: string
    content: string
  } | null>(null)

  const workspaceQuery = usePaperWorkspace(activeId)
  const workspace = workspaceQuery.data
  const openPreferences = useCallback(() => {
    setPreferencesDraft({
      reply_language:
        workspace?.conversation_preferences?.reply_language ?? "auto",
      additional_guidance:
        workspace?.conversation_preferences?.additional_guidance ?? "",
    })
    setPreferencesOpen(true)
  }, [
    workspace?.conversation_preferences?.reply_language,
    workspace?.conversation_preferences?.additional_guidance,
  ])
  const preferencesConfigured = Boolean(
    (workspace?.conversation_preferences?.reply_language ?? "auto") !==
      "auto" ||
      workspace?.conversation_preferences?.additional_guidance?.trim(),
  )
  useEffect(() => {
    if (!workspace?.id) return
    setProposalBasics(parseProposalBasics(workspace?.description))
  }, [workspace?.id, workspace?.description])
  const openInitialUpload = async () => {
    if (workspace?.mode === "proposal") {
      const description = serializeProposalBasics(proposalBasics)
      if (description !== (workspace.description ?? "")) {
        try {
          await updateWorkspace.mutateAsync({ description })
        } catch {
          toast.error(t("paper.source.basicsSaveFailed"))
          return
        }
      }
    }
    openSourceUpload()
  }
  const workflowStages = useMemo(
    () =>
      (workspace?.workflow_stages ?? []).filter((stage): stage is PaperTool =>
        PAPER_WORKFLOW_STEPS.some(({ key }) => key === stage),
      ),
    [workspace?.workflow_stages],
  )
  const nativeStage = workflowStages.includes(activeStage)
    ? activeStage
    : (workflowStages[0] ?? "formatting")
  const proposalEditorOpen =
    proposalEditorState.workspaceId === workspaceId &&
    proposalEditorState.stage === nativeStage &&
    proposalEditorState.open
  const setProposalEditorOpen = (open: boolean) =>
    setProposalEditorState({ workspaceId, stage: nativeStage, open })
  const activeSource = useMemo(
    () =>
      workspace?.active_source_version_id
        ? workspace.source_versions?.find(
            (item) => item.id === workspace.active_source_version_id,
          )
        : undefined,
    [workspace],
  )
  const projectContextAvailable = hasProjectContextFiles(
    activeSource?.manifest ?? [],
  )
  const showProjectContextPrompt =
    workspace?.mode === "proposal" &&
    nativeStage === "formatting" &&
    !workspace.proposal_stage_states?.formatting &&
    !projectContextAvailable &&
    !projectContextSkipped
  const sourceContexts = useMemo(
    () => paperSourceContexts(workspace?.boundary?.content.source_map),
    [workspace?.boundary?.content],
  )
  const selectedSourceContext = useMemo(() => {
    if (!selectedPath) return null

    const normalizedSelectedPath = normalizeSourcePath(selectedPath)
    return (
      sourceContexts.find(
        (source) => normalizeSourcePath(source.path) === normalizedSelectedPath,
      ) ?? null
    )
  }, [selectedPath, sourceContexts])
  const sourceFile = usePaperSourceFile(
    activeSource?.id ?? null,
    selectedPath,
    activeSource?.content_hash,
  )
  const completedStages = useMemo(() => {
    const completed = new Set<PaperTool>()
    Object.entries(workspace?.proposal_stage_states ?? {}).forEach(
      ([stage, state]) => {
        if (
          workflowStages.includes(stage as PaperTool) &&
          state.status === "ready"
        )
          completed.add(stage as PaperTool)
      },
    )
    if ((workspace?.reviews?.length ?? 0) > 0) completed.add("reviews")
    if (workspace?.boundary?.status === "confirmed") completed.add("boundary")
    if ((workspace?.targets?.length ?? 0) > 0) completed.add("targets")
    if (
      workspace?.proposals?.some((proposal) => Boolean(proposal.task_id)) ||
      workspace?.revision_candidates?.some((candidate) =>
        Boolean(candidate.accepted_source_version_id),
      )
    )
      completed.add("branch")
    return completed
  }, [workspace, workflowStages])
  const proposalEntryPath =
    workspace?.proposal_entry_path ??
    (selectedPath?.toLowerCase().endsWith(".typ") ? selectedPath : null) ??
    activeSource?.manifest.find((path) =>
      path.toLowerCase().endsWith(".typ"),
    ) ??
    "proposal.typ"
  const proposalResultStage = useMemo<ProposalResultStage>(() => {
    const stage = isProposalStage(nativeStage) ? nativeStage : "formatting"
    const state = workspace?.proposal_stage_states?.[stage]
    const candidateFiles =
      stage === "formatting" || stage === "final_review"
        ? [proposalEntryPath]
        : PROPOSAL_STAGE_FILES[stage]
    return {
      key: stage,
      title: t(`paper.workflow.stage.${stage}`),
      hint: t(`paper.workflow.stage.${stage}Hint`),
      status: state
        ? t(
            `paper.workflow.${state.status === "ready" ? "ready" : state.status === "stale" ? "stale" : "needsRevision"}`,
          )
        : t("paper.workflow.notStarted"),
      summary: state?.summary ?? null,
      findings: state?.findings ?? [],
      files: state
        ? candidateFiles.filter((path) => activeSource?.manifest.includes(path))
        : [],
    }
  }, [
    activeSource?.manifest,
    nativeStage,
    proposalEntryPath,
    t,
    workspace?.proposal_stage_states,
  ])
  const nativeArtifactPaths = useMemo(() => {
    const stagePublished =
      completedStages.has(nativeStage) ||
      (isProposalStage(nativeStage) &&
        workspace?.proposal_stage_states?.[nativeStage]?.status ===
          "needs_revision")
    if (!activeSource || !stagePublished) return []
    if (!isProposalStage(nativeStage)) return []
    const candidates =
      nativeStage === "formatting" || nativeStage === "final_review"
        ? [proposalEntryPath]
        : PROPOSAL_STAGE_FILES[nativeStage]
    return candidates.filter((path) => activeSource.manifest.includes(path))
  }, [
    activeSource,
    completedStages,
    nativeStage,
    proposalEntryPath,
    workspace?.proposal_stage_states,
  ])
  const canDeleteProposalPath = (path: string, isDirectory: boolean) => {
    if (workspace?.mode !== "proposal") return true
    const generatedPaths = [
      proposalEntryPath,
      ...Object.values(PROPOSAL_STAGE_FILES).flat(),
    ]
    return !generatedPaths.some(
      (generatedPath) =>
        isGeneratedProposalPath(generatedPath, proposalEntryPath) &&
        (generatedPath === path ||
          (isDirectory && generatedPath.startsWith(`${path}/`))),
    )
  }
  const proposalActiveStage = isProposalStage(activeStage) ? activeStage : null
  const rebuttalActiveStage =
    activeStage in REBUTTAL_STAGE_PREREQUISITES
      ? (activeStage as RebuttalStage)
      : null
  const selectedPathEditable = Boolean(
    selectedPath &&
      EDITABLE_SUFFIXES.some((suffix) =>
        selectedPath.toLowerCase().endsWith(suffix),
      ),
  )
  const sourceDirty =
    Boolean(selectedPath) &&
    selectedPathEditable &&
    sourceDraft !== (sourceFile.data?.content ?? "")
  const workflowPrerequisitesReady =
    (!proposalActiveStage ||
      PROPOSAL_STAGE_PREREQUISITES[proposalActiveStage].every(
        (stage) =>
          workspace?.proposal_stage_states?.[stage]?.status === "ready",
      )) &&
    (!rebuttalActiveStage ||
      REBUTTAL_STAGE_PREREQUISITES[rebuttalActiveStage].every((stage) => {
        const status = workspace?.proposal_stage_states?.[stage]?.status
        return rebuttalActiveStage === "ac_summary" && stage === "autorebuttal"
          ? status === "ready" || status === "needs_revision"
          : status === "ready"
      }))
  const modelReady = Boolean(
    workspace?.analysis_provider_id &&
      workspace.analysis_model_name &&
      runtimeProviderIds.has(workspace.analysis_provider_id),
  )
  const uploadSource = useUploadPaperSource(activeId)
  const deletePath = useDeletePaperSourcePath(activeId)
  const updateModel = useUpdatePaperModelBinding(activeId)
  const updateSourceFile = useUpdatePaperSourceFile(activeId)

  useEffect(() => {
    if (
      workspace?.workflow_available &&
      workflowStages.length > 0 &&
      !workflowStages.includes(activeStage)
    ) {
      setActiveStage(workflowStages[0])
    }
  }, [
    activeStage,
    setActiveStage,
    workflowStages,
    workspace?.workflow_available,
  ])

  useEffect(() => {
    if (!activeSource) {
      setSelectedPath(null)
      return
    }
    if (!selectedPath || !activeSource.manifest.includes(selectedPath)) {
      setSelectedPath(
        activeSource.manifest.find((path) =>
          EDITABLE_SUFFIXES.some((suffix) =>
            path.toLowerCase().endsWith(suffix),
          ),
        ) ?? null,
      )
    }
  }, [activeSource, selectedPath, setSelectedPath])

  useEffect(() => {
    if (
      workspace?.mode !== "proposal" ||
      !activeSource ||
      !isProposalStage(activeStage)
    )
      return
    const stageKey = `${workspace.id}:${activeStage}`
    if (lastAutoSelectedProposalStage.current === stageKey) return
    lastAutoSelectedProposalStage.current = stageKey
    const entryPath = workspace.proposal_entry_path
    const ownedPaths =
      activeStage === "formatting" || activeStage === "final_review"
        ? entryPath
          ? [entryPath]
          : activeSource.manifest.filter((path) =>
              path.toLowerCase().endsWith(".typ"),
            )
        : PROPOSAL_STAGE_FILES[activeStage]
    const firstAvailable = ownedPaths.find((path) =>
      activeSource.manifest.includes(path),
    )
    if (firstAvailable) setSelectedPath(firstAvailable)
  }, [activeSource, activeStage, setSelectedPath, workspace])

  useEffect(() => {
    if (!workspace) return
    const analysisSupported = Boolean(
      workspace.analysis_provider_id &&
        runtimeProviderIds.has(workspace.analysis_provider_id),
    )
    setBinding({
      providerId: analysisSupported ? workspace.analysis_provider_id || "" : "",
      modelName: analysisSupported ? workspace.analysis_model_name || "" : "",
    })
    setContextTokens(workspace.analysis_context_window_tokens)
    setOutputTokens(workspace.analysis_max_output_tokens)
  }, [runtimeProviderIds, workspace])

  useEffect(() => {
    if (!selectedPath || !sourceFile.data) return
    const incoming = sourceFile.data.content
    const previous = sourceServerSnapshot.current
    const submitted = submittedSourceDraft.current

    setSourceDraft((current) => {
      if (previous?.path !== selectedPath) return incoming
      if (submitted?.path === selectedPath && submitted.content === incoming) {
        return current === previous.content ? incoming : current
      }
      return current === previous.content ? incoming : current
    })
    sourceServerSnapshot.current = { path: selectedPath, content: incoming }
    if (submitted?.path === selectedPath && submitted.content === incoming) {
      submittedSourceDraft.current = null
    }
    setSourceAutoSaveError(null)
    setEditingSource(false)
  }, [selectedPath, sourceFile.data])

  useEffect(() => {
    if (
      !workspace ||
      !activeSource ||
      !selectedPath ||
      !sourceFile.data ||
      !selectedPathEditable ||
      !sourceDirty ||
      updateSourceFile.isPending ||
      sourceAutoSaveError === sourceDraft
    )
      return

    const timer = window.setTimeout(() => {
      const submitted = { path: selectedPath, content: sourceDraft }
      submittedSourceDraft.current = submitted
      updateSourceFile.mutate(
        {
          sourceVersionId: activeSource.id,
          body: {
            path: selectedPath,
            content: sourceDraft,
            workflow_stage:
              workspace.mode === "proposal" && proposalActiveStage
                ? proposalActiveStage
                : undefined,
          },
        },
        {
          onError: (error) => {
            submittedSourceDraft.current = null
            setSourceAutoSaveError(submitted.content)
            toast.error(errorMessage(error, t("paper.editor.saveFailed")))
          },
        },
      )
    }, 1_000)

    return () => window.clearTimeout(timer)
  }, [
    activeSource,
    proposalActiveStage,
    selectedPath,
    selectedPathEditable,
    sourceAutoSaveError,
    sourceDraft,
    sourceDirty,
    sourceFile.data,
    t,
    updateSourceFile,
    workspace,
  ])

  const removeSourcePath = async (path: string) => {
    if (!window.confirm(t("paper.source.deleteConfirm", { path }))) return
    try {
      await deletePath.mutateAsync({
        sourceVersionId: activeSource!.id,
        path,
        workflowStage:
          workspace?.mode === "proposal" && proposalActiveStage
            ? proposalActiveStage
            : undefined,
      })
      if (selectedPath === path || selectedPath?.startsWith(`${path}/`)) {
        setSelectedPath(null)
      }
      toast.success(t("paper.source.deleted"))
    } catch (error) {
      toast.error(errorMessage(error, t("paper.source.deleteFailed")))
    }
  }

  const createSourceFile = async (path: string) => {
    const filename = path.split("/").pop() || "section.typ"
    await uploadSource.mutateAsync([
      {
        file: new File([initialPaperFileContent(path)], filename, {
          type: "text/plain",
        }),
        relativePath: path,
      },
    ])
    setSelectedPath(path)
  }

  const createSourceFolder = async (path: string) => {
    await uploadSource.mutateAsync([
      {
        file: new File(["managed directory\n"], PAPER_DIRECTORY_MARKER, {
          type: "text/plain",
        }),
        relativePath: `${path}/${PAPER_DIRECTORY_MARKER}`,
      },
    ])
  }

  const saveSourceDraft = async () => {
    if (!workspace || !activeSource || !selectedPath) return
    await updateSourceFile.mutateAsync({
      sourceVersionId: activeSource.id,
      body: {
        path: selectedPath,
        content: sourceDraft,
        workflow_stage:
          workspace.mode === "proposal" && proposalActiveStage
            ? proposalActiveStage
            : undefined,
      },
    })
    setEditingSource(false)
    toast.success(t("paper.editor.saved"))
  }

  if (!workspace) {
    if (workspaceQuery.isError) {
      return (
        <div className="grid h-full min-h-0 place-items-center p-6">
          <div className="max-w-md text-center">
            <p className="text-sm text-muted-foreground">
              {t("paper.workspace.detailLoadFailed")}
            </p>
            <div className="mt-4 flex justify-center gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  void navigate({
                    to: "/papers",
                    search: { mode: entryMode, workspaceId: undefined },
                  })
                }
              >
                {t("paper.workspace.back")}
              </Button>
              <Button onClick={() => void workspaceQuery.refetch()}>
                {t("common.retry")}
              </Button>
            </div>
          </div>
        </div>
      )
    }
    return (
      <div className="grid h-full min-h-0 place-items-center">
        <Loader2 className="size-6 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-background">
      <PaperWorkbenchHeader
        title={workspace.title}
        onConfigurePreferences={
          !activeSource && workspace.mode !== "algorithm"
            ? openPreferences
            : undefined
        }
        onConfigureModel={
          activeSource && workspace.mode !== "algorithm"
            ? openModelSettings
            : undefined
        }
        onEditBasics={
          activeSource && workspace.mode === "proposal" ? openBasics : undefined
        }
      />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {!activeSource ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-8 sm:px-6 sm:py-12">
            <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center">
              <div className="mb-5">
                <h2 className="font-serif text-2xl font-semibold tracking-tight sm:text-3xl">
                  {t("paper.source.prepareTitle")}
                </h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {t(
                    workspace.mode === "proposal"
                      ? "paper.source.prepareProposalDescription"
                      : "paper.source.prepareDescription",
                  )}
                </p>
              </div>

              <div className="overflow-hidden border bg-card/70 shadow-sm">
                <section className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:p-5">
                  <span
                    aria-hidden="true"
                    className="grid size-10 shrink-0 place-items-center border border-primary/25 bg-primary/5 font-serif text-lg text-primary"
                  >
                    01
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-serif text-lg font-semibold">
                        {t("paper.source.modelStepTitle")}
                      </h3>
                      {modelReady && (
                        <span className="inline-flex min-w-0 items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                          <Check
                            aria-hidden="true"
                            className="size-3.5 shrink-0"
                          />
                          <span className="truncate">
                            {t("paper.source.modelConfigured", {
                              model: workspace.analysis_model_name,
                            })}
                          </span>
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      {t("paper.source.modelStepDescription")}
                    </p>
                  </div>
                  <Button
                    variant={modelReady ? "outline" : "default"}
                    className="w-full shrink-0 sm:w-auto"
                    onClick={openModelSettings}
                  >
                    <Settings2 data-icon="inline-start" />
                    {t("paper.model.configure")}
                  </Button>
                </section>

                {workspace.mode === "proposal" && (
                  <section className="border-t p-4 sm:p-5">
                    <div className="flex items-start gap-4">
                      <span
                        aria-hidden="true"
                        className="grid size-10 shrink-0 place-items-center border bg-muted/40 font-serif text-lg text-muted-foreground"
                      >
                        02
                      </span>
                      <div className="min-w-0">
                        <h3 className="font-serif text-lg font-semibold">
                          {t("paper.source.basicsTitle")}
                        </h3>
                        <p className="mt-1 text-sm leading-6 text-muted-foreground">
                          {t("paper.source.basicsDescription")}
                        </p>
                      </div>
                    </div>
                    <div className="mt-4">
                      <ProposalBasicsFields
                        basics={proposalBasics}
                        onChange={setProposalBasics}
                      />
                    </div>
                    <div className="mt-4 flex justify-end">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          updateWorkspace.isPending ||
                          serializeProposalBasics(proposalBasics) ===
                            (workspace.description ?? "")
                        }
                        onClick={async () => {
                          try {
                            await updateWorkspace.mutateAsync({
                              description:
                                serializeProposalBasics(proposalBasics),
                            })
                            toast.success(t("paper.source.basicsSaved"))
                          } catch {
                            toast.error(t("paper.source.basicsSaveFailed"))
                          }
                        }}
                      >
                        {updateWorkspace.isPending && (
                          <Loader2 className="size-3.5 animate-spin" />
                        )}
                        {t("paper.source.basicsSave")}
                      </Button>
                    </div>
                  </section>
                )}

                {workspace.mode !== "algorithm" && (
                  <section className="flex flex-col gap-4 border-t p-4 sm:flex-row sm:items-center sm:p-5">
                    <span
                      aria-hidden="true"
                      className="grid size-10 shrink-0 place-items-center border bg-muted/40 font-serif text-lg text-muted-foreground"
                    >
                      {workspace.mode === "proposal" ? "03" : "02"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-serif text-lg font-semibold">
                          {t("paper.preferences.stepTitle")}
                        </h3>
                        {preferencesConfigured && (
                          <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">
                            {t("paper.preferences.configured")}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-sm leading-6 text-muted-foreground">
                        {t("paper.preferences.stepDescription")}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      className="w-full shrink-0 sm:w-auto"
                      onClick={openPreferences}
                    >
                      <SlidersHorizontal data-icon="inline-start" />
                      {t("paper.preferences.configure")}
                    </Button>
                  </section>
                )}

                <section className="flex flex-col gap-4 border-t p-4 sm:flex-row sm:items-center sm:p-5">
                  <span
                    aria-hidden="true"
                    className="grid size-10 shrink-0 place-items-center border bg-muted/40 font-serif text-lg text-muted-foreground"
                  >
                    {workspace.mode === "proposal" ? "04" : "03"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="font-serif text-lg font-semibold">
                      {t("paper.source.dropTitle")}
                    </h3>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      {t("paper.source.dropDescription")}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    className="w-full shrink-0 sm:w-auto"
                    disabled={updateWorkspace.isPending}
                    onClick={() => void openInitialUpload()}
                  >
                    <Upload data-icon="inline-start" />
                    {t("paper.source.upload")}
                  </Button>
                </section>
              </div>
            </div>
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(28rem,1fr)]">
            <section className="flex h-full min-w-0 flex-col overflow-hidden bg-muted/[0.07]">
              {workspace.workflow_available &&
                workspace.mode !== "algorithm" && (
                  <PaperWorkflowStepper
                    stages={workflowStages}
                    activeStage={nativeStage}
                    completedStages={completedStages}
                    stageStates={workspace.proposal_stage_states ?? {}}
                    onSelect={(stage) => {
                      setActiveStage(stage)
                      if (workspace.mode === "proposal")
                        setProposalEditorState({
                          workspaceId,
                          stage,
                          open: false,
                        })
                    }}
                  />
                )}
              {workspace.workflow_available ? (
                <PaperNativeRuntime
                  workspaceId={workspace.id}
                  stage={nativeStage}
                  modelReady={modelReady}
                  prerequisiteReady={workflowPrerequisitesReady}
                  artifactPaths={nativeArtifactPaths}
                  stageState={workspace.proposal_stage_states?.[nativeStage]}
                  modelName={workspace.analysis_model_name}
                  onConfigureModel={openModelSettings}
                  onConfigurePreferences={openPreferences}
                  showProjectContextPrompt={showProjectContextPrompt}
                  onUploadProjectContext={openProjectContextUpload}
                  onSkipProjectContext={() => setProjectContextSkipped(true)}
                  onOpenArtifact={(path) => {
                    setSelectedPath(path)
                    if (workspace.mode === "proposal")
                      setProposalEditorOpen(true)
                  }}
                  profileKey={[
                    workspace.analysis_provider_id,
                    workspace.analysis_model_name,
                    workspace.analysis_context_window_tokens,
                    workspace.analysis_max_output_tokens,
                    workspace.mode === "proposal" ? workspace.description : "",
                    JSON.stringify(workspace.conversation_preferences ?? {}),
                  ].join(":")}
                />
              ) : (
                <div className="grid min-h-0 flex-1 place-items-center p-6 text-center">
                  <div className="max-w-sm rounded-xl border border-dashed bg-background p-8">
                    <Sparkles className="mx-auto size-8 text-primary" />
                    <h2 className="mt-3 text-sm font-semibold">
                      {t(`paper.workspace.mode.${workspace.mode}`)}
                    </h2>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      {t("paper.workspace.workflowReserved")}
                    </p>
                  </div>
                </div>
              )}
            </section>
            {workspace.mode === "manuscript" ? (
              <aside className="hidden h-full min-h-0 min-w-0 flex-col overflow-hidden border-l bg-card/30 lg:flex">
                <Tabs
                  defaultValue="rebuttal"
                  className="flex min-h-0 flex-1 flex-col"
                >
                  <div className="shrink-0 border-b bg-background/65 px-5 pt-2">
                    <TabsList className="grid h-11 w-full grid-cols-2 rounded-none bg-transparent p-0">
                      <TabsTrigger
                        value="rebuttal"
                        className="rounded-none border-0 border-b-2 border-transparent bg-transparent px-3 font-serif text-base shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none dark:data-[state=active]:border-primary dark:data-[state=active]:bg-transparent"
                      >
                        <MessagesSquare aria-hidden="true" />
                        {t("paper.rebuttal.tabs.entries")}
                      </TabsTrigger>
                      <TabsTrigger
                        value="source"
                        className="rounded-none border-0 border-b-2 border-transparent bg-transparent px-3 font-serif text-base shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none dark:data-[state=active]:border-primary dark:data-[state=active]:bg-transparent"
                      >
                        <FileText aria-hidden="true" />
                        {t("paper.rebuttal.tabs.source")}
                      </TabsTrigger>
                    </TabsList>
                  </div>
                  <TabsContent
                    value="rebuttal"
                    className="mt-0 min-h-0 flex-1 overflow-hidden"
                  >
                    <RebuttalEntriesPanel
                      workspaceId={workspace.id}
                      sourceVersionId={activeSource.id}
                      activeStage={nativeStage as RebuttalStage}
                      reviews={workspace.reviews ?? []}
                      entries={workspace.rebuttal_entries ?? []}
                      rebuttalOutput={workspace.rebuttal_output}
                      chairMessage={workspace.chair_message}
                      chairStale={
                        workspace.proposal_stage_states?.ac_summary?.status ===
                        "stale"
                      }
                      baselineContext={workspace.rebuttal_context}
                      baselineStale={
                        workspace.proposal_stage_states?.rebuttal_baseline
                          ?.status === "stale"
                      }
                      readyForSubmission={
                        workspace.proposal_stage_states?.autorebuttal
                          ?.status === "ready"
                      }
                      stale={
                        workspace.proposal_stage_states?.autorebuttal
                          ?.status === "stale"
                      }
                    />
                  </TabsContent>
                  <TabsContent
                    value="source"
                    className="mt-0 min-h-0 flex-1 overflow-hidden"
                  >
                    <PaperDocumentDock
                      embedded
                      manifest={activeSource.manifest}
                      sourceVersionId={activeSource.id}
                      sourceHash={activeSource.content_hash}
                      selectedPath={selectedPath}
                      sourceContent={sourceFile.data?.content ?? ""}
                      sourceContext={selectedSourceContext}
                      sourceLoading={sourceFile.isLoading}
                      sourceDraft={sourceDraft}
                      sourceDirty={sourceDirty}
                      sourceSaveError={sourceAutoSaveError === sourceDraft}
                      sourceSaving={updateSourceFile.isPending}
                      typstEntryPath={null}
                      typstDownloadEnabled={false}
                      typstPreview={false}
                      editable={selectedPathEditable}
                      locked={false}
                      highlightStartLine={null}
                      highlightEndLine={null}
                      workspaceKey={workspace.id}
                      onSelect={setSelectedPath}
                      onDelete={(path) => void removeSourcePath(path)}
                      onCreateFile={createSourceFile}
                      onCreateFolder={createSourceFolder}
                      onUpload={openSourceUpload}
                      uploading={uploadSource.isPending}
                      canDeletePath={canDeleteProposalPath}
                      onDraftChange={(value) => {
                        setSourceAutoSaveError(null)
                        setSourceDraft(value)
                      }}
                    />
                  </TabsContent>
                </Tabs>
              </aside>
            ) : workspace.mode === "algorithm" ? (
              <aside className="hidden h-full min-h-0 min-w-0 flex-col overflow-hidden border-l bg-background lg:flex">
                <Tabs
                  defaultValue="algorithms"
                  className="flex min-h-0 flex-1 flex-col"
                >
                  <div className="shrink-0 border-b px-3 py-2">
                    <TabsList className="grid w-full grid-cols-2">
                      <TabsTrigger value="algorithms">
                        {t("paper.discovery.tabs.algorithms")}
                      </TabsTrigger>
                      <TabsTrigger value="source">
                        {t("paper.discovery.tabs.source")}
                      </TabsTrigger>
                    </TabsList>
                  </div>
                  <TabsContent
                    value="algorithms"
                    className="mt-0 min-h-0 flex-1 overflow-hidden"
                  >
                    <AlgorithmDiscoveryPanel
                      workspaceId={workspace.id}
                      sourceVersionId={activeSource.id}
                      proposals={
                        workspace.proposal_stage_states?.discovery?.status ===
                        "ready"
                          ? (workspace.proposals ?? [])
                          : []
                      }
                    />
                  </TabsContent>
                  <TabsContent
                    value="source"
                    className="mt-0 min-h-0 flex-1 overflow-hidden"
                  >
                    <PaperDocumentDock
                      embedded
                      manifest={activeSource.manifest}
                      sourceVersionId={activeSource.id}
                      sourceHash={activeSource.content_hash}
                      selectedPath={selectedPath}
                      sourceContent={sourceFile.data?.content ?? ""}
                      sourceContext={selectedSourceContext}
                      sourceLoading={sourceFile.isLoading}
                      sourceDraft={sourceDraft}
                      sourceDirty={sourceDirty}
                      sourceSaveError={sourceAutoSaveError === sourceDraft}
                      sourceSaving={updateSourceFile.isPending}
                      typstEntryPath={null}
                      typstDownloadEnabled={false}
                      typstPreview={false}
                      editable={selectedPathEditable}
                      locked={false}
                      highlightStartLine={null}
                      highlightEndLine={null}
                      workspaceKey={workspace.id}
                      onSelect={setSelectedPath}
                      onDelete={(path) => void removeSourcePath(path)}
                      onCreateFile={createSourceFile}
                      onCreateFolder={createSourceFolder}
                      onUpload={openSourceUpload}
                      uploading={uploadSource.isPending}
                      canDeletePath={canDeleteProposalPath}
                      onDraftChange={(value) => {
                        setSourceAutoSaveError(null)
                        setSourceDraft(value)
                      }}
                    />
                  </TabsContent>
                </Tabs>
              </aside>
            ) : (
              <ProposalResultsDock
                stage={proposalResultStage}
                editorOpen={proposalEditorOpen}
                onEditorOpenChange={setProposalEditorOpen}
                onOpenFile={setSelectedPath}
                onUploadProjectContext={openProjectContextUpload}
              >
                <PaperDocumentDock
                  embedded
                  manifest={activeSource.manifest}
                  sourceVersionId={activeSource.id}
                  sourceHash={activeSource.content_hash}
                  selectedPath={selectedPath}
                  sourceContent={sourceFile.data?.content ?? ""}
                  sourceContext={selectedSourceContext}
                  sourceLoading={sourceFile.isLoading}
                  sourceDraft={sourceDraft}
                  sourceDirty={sourceDirty}
                  sourceSaveError={sourceAutoSaveError === sourceDraft}
                  sourceSaving={updateSourceFile.isPending}
                  typstEntryPath={workspace.proposal_entry_path}
                  typstDownloadEnabled={
                    workspace.proposal_stage_states?.final_review?.status ===
                    "ready"
                  }
                  editable={selectedPathEditable}
                  locked={false}
                  highlightStartLine={null}
                  highlightEndLine={null}
                  workspaceKey={workspace.id}
                  onSelect={setSelectedPath}
                  onDelete={(path) => void removeSourcePath(path)}
                  onCreateFile={createSourceFile}
                  onCreateFolder={createSourceFolder}
                  onUpload={openSourceUpload}
                  uploading={uploadSource.isPending}
                  canDeletePath={canDeleteProposalPath}
                  onDraftChange={(value) => {
                    setSourceAutoSaveError(null)
                    setSourceDraft(value)
                  }}
                />
              </ProposalResultsDock>
            )}
          </div>
        )}
      </main>

      {workspace.mode === "proposal" && (
        <Dialog
          open={basicsOpen}
          onOpenChange={(open) => {
            setBasicsOpen(open)
            if (!open)
              setProposalBasics(parseProposalBasics(workspace.description))
          }}
        >
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>{t("paper.source.basicsTitle")}</DialogTitle>
              <DialogDescription>
                {t("paper.source.basicsDescription")}
              </DialogDescription>
            </DialogHeader>
            <ProposalBasicsFields
              basics={proposalBasics}
              onChange={setProposalBasics}
            />
            <DialogFooter>
              <Button
                disabled={
                  updateWorkspace.isPending ||
                  serializeProposalBasics(proposalBasics) ===
                    (workspace.description ?? "")
                }
                onClick={async () => {
                  try {
                    await updateWorkspace.mutateAsync({
                      description: serializeProposalBasics(proposalBasics),
                    })
                    setBasicsOpen(false)
                    toast.success(t("paper.source.basicsSaved"))
                  } catch {
                    toast.error(t("paper.source.basicsSaveFailed"))
                  }
                }}
              >
                {updateWorkspace.isPending && (
                  <Loader2 className="size-3.5 animate-spin" />
                )}
                {t("paper.source.basicsSave")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {activeSource && (
        <Dialog
          open={editingSource}
          onOpenChange={(open) => {
            if (open) {
              setEditingSource(true)
              return
            }
            setSourceDraft(sourceFile.data?.content ?? "")
            setEditingSource(false)
          }}
        >
          <DialogContent className="flex h-[min(88vh,56rem)] max-w-[min(94vw,88rem)] flex-col gap-0 overflow-hidden p-0">
            <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12">
              <DialogTitle className="truncate text-sm">
                {selectedPath ?? t("paper.editor.selectFile")}
              </DialogTitle>
              <DialogDescription>
                {t("paper.editor.dialogDescription")}
              </DialogDescription>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-hidden">
              <PaperSourceManager
                manifest={activeSource.manifest}
                sourceVersionId={activeSource.id}
                sourceHash={activeSource.content_hash}
                selectedPath={selectedPath}
                sourceContent={sourceFile.data?.content ?? ""}
                sourceContext={selectedSourceContext}
                sourceLoading={sourceFile.isLoading}
                typstPreview={workspace.mode === "proposal"}
                typstEntryPath={workspace.proposal_entry_path}
                typstDownloadEnabled={
                  workspace.proposal_stage_states?.final_review?.status ===
                  "ready"
                }
                editing={editingSource}
                editable={selectedPathEditable}
                sourceDraft={sourceDraft}
                highlightStartLine={null}
                highlightEndLine={null}
                sidebarExpanded={false}
                saving={updateSourceFile.isPending}
                onSelect={setSelectedPath}
                onDelete={(path) => void removeSourcePath(path)}
                onCreateFile={createSourceFile}
                onCreateFolder={createSourceFolder}
                onUpload={openSourceUpload}
                uploading={uploadSource.isPending}
                locked={false}
                canDeletePath={canDeleteProposalPath}
                onSidebarChange={() => undefined}
                onEdit={() => setEditingSource(true)}
                onCancelEdit={() => {
                  setSourceDraft(sourceFile.data?.content ?? "")
                  setEditingSource(false)
                }}
                onDraftChange={setSourceDraft}
                onSave={() => void saveSourceDraft()}
                showNavigator={false}
              />
            </div>
          </DialogContent>
        </Dialog>
      )}

      <SourceUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        purpose={uploadPurpose}
        files={pendingFiles}
        setFiles={setPendingFiles}
        fileInput={fileInput}
        folderInput={folderInput}
        pending={uploadSource.isPending}
        onUpload={async () => {
          if (
            uploadPurpose === "project_context" &&
            pendingFiles.some((file) =>
              file.name.toLowerCase().endsWith(".zip"),
            )
          ) {
            toast.error(t("paper.source.projectContext.archiveUnsupported"))
            return
          }
          try {
            await uploadSource.mutateAsync(
              uploadPurpose === "project_context"
                ? projectContextUploads(pendingFiles)
                : pendingFiles,
            )
            setPendingFiles([])
            setUploadOpen(false)
            toast.success(
              t(
                uploadPurpose === "project_context"
                  ? "paper.source.projectContext.uploaded"
                  : "paper.source.uploaded",
              ),
            )
          } catch (error) {
            toast.error(
              errorMessage(
                error,
                t("paper.source.projectContext.uploadFailed"),
              ),
            )
          }
        }}
      />

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("paper.model.dialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("paper.model.dialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 rounded-lg border p-3">
            <Label>{t("paper.model.analysis")}</Label>
            <ModelPicker value={binding} onChange={setBinding} />
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-2">
                <Label>{t("paper.model.context")}</Label>
                <Select
                  value={String(contextTokens)}
                  onValueChange={(value) => setContextTokens(Number(value))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CONTEXT_PRESETS.map((value) => (
                      <SelectItem key={value} value={String(value)}>
                        {value.toLocaleString()}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t("paper.model.output")}</Label>
                <Select
                  value={String(outputTokens)}
                  onValueChange={(value) => setOutputTokens(Number(value))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {OUTPUT_PRESETS.map((value) => (
                      <SelectItem key={value} value={String(value)}>
                        {value.toLocaleString()}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={!binding.providerId || !binding.modelName}
              onClick={async () => {
                await updateModel.mutateAsync({
                  provider_id: binding.providerId,
                  model_name: binding.modelName,
                  context_window_tokens: contextTokens,
                  max_output_tokens: outputTokens,
                })
                setSettingsOpen(false)
                toast.success(t("paper.model.saved"))
              }}
            >
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {workspace.mode !== "algorithm" && (
        <Dialog open={preferencesOpen} onOpenChange={setPreferencesOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>{t("paper.preferences.dialogTitle")}</DialogTitle>
              <DialogDescription>
                {t("paper.preferences.dialogDescription")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-5 py-2">
              <div className="space-y-2">
                <Label htmlFor="paper-reply-language">
                  {t("paper.preferences.language")}
                </Label>
                <Select
                  value={preferencesDraft.reply_language}
                  onValueChange={(
                    value: ConversationPreferencesDraft["reply_language"],
                  ) =>
                    setPreferencesDraft((current) => ({
                      ...current,
                      reply_language: value,
                    }))
                  }
                >
                  <SelectTrigger id="paper-reply-language" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">
                      {t("paper.preferences.languageAuto")}
                    </SelectItem>
                    <SelectItem value="zh">
                      {t("paper.preferences.languageChinese")}
                    </SelectItem>
                    <SelectItem value="en">
                      {t("paper.preferences.languageEnglish")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="paper-additional-guidance">
                  {t("paper.preferences.additionalGuidance")}
                </Label>
                <Textarea
                  id="paper-additional-guidance"
                  value={preferencesDraft.additional_guidance}
                  onChange={(event) =>
                    setPreferencesDraft((current) => ({
                      ...current,
                      additional_guidance: event.target.value,
                    }))
                  }
                  placeholder={t("paper.preferences.guidancePlaceholder")}
                  maxLength={4000}
                  className="min-h-28 resize-y"
                />
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("paper.preferences.scopeHint")}
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setPreferencesOpen(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                disabled={updateWorkspace.isPending}
                onClick={async () => {
                  try {
                    await updateWorkspace.mutateAsync({
                      conversation_preferences: {
                        reply_language: preferencesDraft.reply_language,
                        additional_guidance:
                          preferencesDraft.additional_guidance.trim(),
                      },
                    })
                    setPreferencesOpen(false)
                    toast.success(t("paper.preferences.saved"))
                  } catch {
                    toast.error(t("paper.preferences.saveFailed"))
                  }
                }}
              >
                {updateWorkspace.isPending && (
                  <Loader2 className="size-3.5 animate-spin" />
                )}
                {t("common.save")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}

function SourceUploadDialog({
  open,
  onOpenChange,
  purpose,
  files,
  setFiles,
  fileInput,
  folderInput,
  pending,
  onUpload,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  purpose: "source" | "project_context"
  files: File[]
  setFiles: (files: File[]) => void
  fileInput: React.RefObject<HTMLInputElement | null>
  folderInput: React.RefObject<HTMLInputElement | null>
  pending: boolean
  onUpload: () => void
}) {
  const { t } = useTranslation()
  const append = (incoming: FileList | null) => {
    if (!incoming) return
    const indexed = new Map(
      files.map((file) => [file.webkitRelativePath || file.name, file]),
    )
    for (const file of Array.from(incoming))
      indexed.set(file.webkitRelativePath || file.name, file)
    setFiles([...indexed.values()])
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="min-w-0 max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {t(
              purpose === "project_context"
                ? "paper.source.projectContext.dialogTitle"
                : "paper.source.dialogTitle",
            )}
          </DialogTitle>
          <DialogDescription>
            {t(
              purpose === "project_context"
                ? "paper.source.projectContext.dialogDescription"
                : "paper.source.dialogDescription",
            )}
          </DialogDescription>
        </DialogHeader>
        <input
          ref={fileInput}
          type="file"
          multiple
          className="hidden"
          accept={
            purpose === "project_context"
              ? ".typ,.md,.markdown,.tex,.bib,.txt,.csv,.json,.yaml,.yml,.png,.jpg,.jpeg,.pdf,.svg"
              : ".typ,.md,.markdown,.tex,.bib,.bst,.cls,.sty,.txt,.csv,.json,.yaml,.yml,.png,.jpg,.jpeg,.pdf,.eps,.svg,.zip"
          }
          onChange={(event) => {
            append(event.target.files)
            event.currentTarget.value = ""
          }}
        />
        <input
          ref={folderInput}
          type="file"
          multiple
          className="hidden"
          {...({ webkitdirectory: "", directory: "" } as Record<
            string,
            string
          >)}
          onChange={(event) => {
            append(event.target.files)
            event.currentTarget.value = ""
          }}
        />
        <div
          className={cn(
            "grid gap-3",
            purpose === "project_context" ? "grid-cols-1" : "grid-cols-2",
          )}
        >
          <Button variant="outline" onClick={() => fileInput.current?.click()}>
            <FileText className="size-4" /> {t("paper.source.selectFiles")}
          </Button>
          {purpose === "source" && (
            <Button
              variant="outline"
              onClick={() => folderInput.current?.click()}
            >
              <Folder className="size-4" /> {t("paper.source.selectFolder")}
            </Button>
          )}
        </div>
        <ScrollArea className="h-56 min-w-0 rounded-lg border [&>[data-radix-scroll-area-viewport]]:overflow-x-hidden [&>[data-radix-scroll-area-viewport]>div]:!block [&>[data-radix-scroll-area-viewport]>div]:!w-full">
          <div className="min-w-0 space-y-1 p-2">
            {files.map((file) => {
              const path = file.webkitRelativePath || file.name
              return (
                <div
                  key={path}
                  className="flex min-w-0 items-center gap-2 overflow-hidden rounded px-2 py-1.5 text-xs hover:bg-muted"
                >
                  <FileText className="size-3.5 shrink-0" />
                  <span className="block min-w-0 flex-1 truncate" title={path}>
                    {path}
                  </span>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-7 shrink-0"
                    onClick={() =>
                      setFiles(
                        files.filter(
                          (item) =>
                            (item.webkitRelativePath || item.name) !== path,
                        ),
                      )
                    }
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              )
            })}
            {!files.length && (
              <p className="p-6 text-center text-xs text-muted-foreground">
                {t("paper.source.noSelection")}
              </p>
            )}
          </div>
        </ScrollArea>
        <DialogFooter>
          <Button disabled={!files.length || pending} onClick={onUpload}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {t("paper.source.uploadSelection")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
