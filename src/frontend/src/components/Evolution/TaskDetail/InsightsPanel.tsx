import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronsUpDown,
  Crosshair,
  FileText,
  Loader2,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Square,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type {
  ReportEntry,
  ReportGenerateRequest,
  ReportType,
  TaskResponse,
} from "@/client"
import { Llm4AdReportsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useEvolution } from "@/hooks/useEvolution"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { useReportStream } from "@/hooks/useReportStream"
import { taskKeys } from "@/lib/task-queries"
import { cn } from "@/lib/utils"
import { resolveCurrentReport } from "./insights-report-state"
import type { GANode } from "./island-ga-mock-data"
import ReportMarkdown from "./ReportMarkdown"

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

const REPORT_TYPES: {
  key: ReportType
  needsNodes: boolean
  needsBestNode: boolean
}[] = [
  { key: "tech_change", needsNodes: false, needsBestNode: false },
  { key: "node_comparison", needsNodes: true, needsBestNode: false },
  { key: "chain_analysis", needsNodes: true, needsBestNode: false },
  { key: "champion_birth", needsNodes: false, needsBestNode: true },
]

interface InsightsPanelProps {
  task: TaskResponse
}

export default function InsightsPanel({ task }: InsightsPanelProps) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const {
    evolutionData,
    selectedNodes,
    toggleSelectedNode,
    insightActiveType,
    setInsightActiveType,
    insightSelectedNodeIds,
    setInsightSelectedNodeIds,
    insightSelectedBestNodeId,
    setInsightSelectedBestNodeId,
    activeTab,
    requestFocusNode,
  } = useEvolution()

  const isPanelActive = activeTab === "insights"

  const [activeType, setActiveType] = useState<ReportType>("tech_change")
  const [providerId, setProviderId] = useState<string>("default")
  const [modelName, setModelName] = useState<string>("")
  const [selectedBestNodeId, setSelectedBestNodeId] = useState<string>("")
  const [streamingType, setStreamingType] = useState<ReportType | null>(null)
  const [promptTemplate, setPromptTemplate] = useState<string>("")
  const [promptExpanded, setPromptExpanded] = useState(false)

  // selectedNodeIds is derived from the shared selection (graph <-> insights sync)
  const selectedNodeIds = useMemo(
    () => selectedNodes.map((n) => n.id),
    [selectedNodes],
  )

  const nodeMap = useMemo(() => {
    const m = new Map<string, GANode>()
    for (const n of evolutionData.nodes) m.set(n.id, n)
    return m
  }, [evolutionData])

  useEffect(() => {
    if (insightActiveType) {
      setActiveType(insightActiveType)
      setInsightActiveType(null)
    }
  }, [insightActiveType, setInsightActiveType])

  // Hand-off: when a navigation signal carries explicit node ids, sync them
  // into the shared selection so the graph reflects the same set.
  useEffect(() => {
    if (insightSelectedNodeIds) {
      const next = insightSelectedNodeIds
        .map((id) => nodeMap.get(id))
        .filter((n): n is GANode => !!n)
        .map((n) => ({
          id: n.id,
          generation: n.generation,
          island: n.island,
          islandId: n.islandId,
          name: n.name,
          fileName: n.fileName,
          score: n.score,
          rawScore: n.rawScore,
          parentIds: n.parentIds,
        }))
      // Replace the current selection with the requested set.
      const currentIds = new Set(selectedNodes.map((n) => n.id))
      const desiredIds = new Set(next.map((n) => n.id))
      // Remove nodes not in desired
      for (const sn of selectedNodes) {
        if (!desiredIds.has(sn.id)) toggleSelectedNode(sn)
      }
      // Add nodes not yet selected
      for (const dn of next) {
        if (!currentIds.has(dn.id)) toggleSelectedNode(dn)
      }
      setInsightSelectedNodeIds(null)
    }
  }, [
    insightSelectedNodeIds,
    setInsightSelectedNodeIds,
    nodeMap,
    selectedNodes,
    toggleSelectedNode,
  ])

  useEffect(() => {
    if (insightSelectedBestNodeId) {
      setSelectedBestNodeId(insightSelectedBestNodeId)
      setInsightSelectedBestNodeId(null)
    }
  }, [insightSelectedBestNodeId, setInsightSelectedBestNodeId])

  const sortedNodes = useMemo(() => {
    return [...evolutionData.nodes].sort((a, b) => b.rawScore - a.rawScore)
  }, [evolutionData.nodes])

  // Champion-birth only accepts the highest-scoring nodes (may be a tie set).
  const championCandidates = useMemo(() => {
    if (sortedNodes.length === 0) return []
    const top = sortedNodes[0].rawScore
    if (typeof top !== "number" || Number.isNaN(top)) return []
    return sortedNodes.filter((n) => n.rawScore === top)
  }, [sortedNodes])
  const championFirstId = championCandidates[0]?.id ?? ""

  // Switching into champion-birth always refreshes the dropdown to the top
  // candidate; staying on it only resets when the current pick goes invalid.
  const prevActiveTypeRef = useRef<ReportType>(activeType)
  useEffect(() => {
    const prev = prevActiveTypeRef.current
    prevActiveTypeRef.current = activeType
    if (activeType !== "champion_birth") return
    const enteringTab = prev !== "champion_birth"
    const stillValid =
      !!selectedBestNodeId &&
      championCandidates.some((n) => n.id === selectedBestNodeId)
    if (enteringTab || !stillValid) {
      setSelectedBestNodeId(championFirstId)
    }
  }, [activeType, championCandidates, championFirstId, selectedBestNodeId])

  // When user picks a single node in the graph, default the champion-birth
  // best-node selector to it — but only if it's a valid champion candidate.
  useEffect(() => {
    if (activeType !== "champion_birth") return
    if (selectedNodes.length !== 1) return
    const id = selectedNodes[0].id
    if (championCandidates.some((n) => n.id === id)) {
      setSelectedBestNodeId(id)
    }
  }, [selectedNodes, activeType, championCandidates])

  useEffect(() => {
    setStreamingType(null)
  }, [])

  const typeConfig = REPORT_TYPES.find((rt) => rt.key === activeType)!

  const reportQuery = useQuery({
    queryKey: taskKeys.report(task.id, activeType),
    queryFn: () =>
      Llm4AdReportsService.getReport({
        taskId: task.id,
        reportType: activeType,
      }),
    staleTime: 30_000,
    enabled: !!task.id && isPanelActive,
  })

  const currentReport: ReportEntry | null = useMemo(() => {
    const fromTask = (task.reports as Record<string, ReportEntry> | null)?.[
      activeType
    ]
    const fromQuery = reportQuery.data?.report
    return resolveCurrentReport(fromTask, fromQuery)
  }, [task.reports, activeType, reportQuery.data])

  const templatesQuery = useQuery({
    queryKey: ["reportTemplates"],
    queryFn: () => Llm4AdReportsService.getReportTemplates(),
    staleTime: 5 * 60_000,
    enabled: isPanelActive,
  })
  const defaultTemplate =
    templatesQuery.data?.templates?.[activeType]?.user_prompt_template ?? ""

  // Sync prompt template: use saved report prompt if available, otherwise fill with default template
  useEffect(() => {
    const savedPrompt = (
      currentReport?.params as Record<string, unknown> | null
    )?.prompt_template
    if (typeof savedPrompt === "string" && savedPrompt) {
      setPromptTemplate(savedPrompt)
    } else {
      setPromptTemplate(defaultTemplate)
    }
  }, [currentReport?.params, defaultTemplate])

  const isGenerating =
    currentReport?.status === "generating" || streamingType === activeType

  const shouldStream = isGenerating && streamingType === activeType
  const {
    content: streamContent,
    status: streamStatus,
    error: streamError,
    isStreaming,
    progress: streamProgress,
    stop: stopStream,
  } = useReportStream(task.id, activeType, shouldStream)

  useEffect(() => {
    if (
      currentReport?.status === "generating" &&
      streamingType !== activeType
    ) {
      setStreamingType(activeType)
    }
  }, [currentReport?.status, activeType, streamingType])

  useEffect(() => {
    if (
      streamStatus === "completed" ||
      streamStatus === "failed" ||
      streamStatus === "cancelled"
    ) {
      queryClient.invalidateQueries({ queryKey: taskKeys.detail(task.id) })
      queryClient.invalidateQueries({
        queryKey: taskKeys.report(task.id, activeType),
      })
    }
  }, [streamStatus, queryClient, task.id, activeType])

  useEffect(() => {
    if (!streamingType) return
    if (currentReport?.status === "completed" && currentReport?.content) {
      setStreamingType(null)
    }
    if (
      currentReport?.status === "failed" ||
      currentReport?.status === "cancelled"
    ) {
      setStreamingType(null)
    }
  }, [streamingType, currentReport])

  const isTaskReady = task.status === "completed" || task.status === "failed"

  const providersQuery = useProviders()
  const providers = providersQuery.data?.items ?? []

  const { data: defaultModels } = useUserDefaultModels()
  const reportHint = [
    defaultModels?.report_provider_name,
    defaultModels?.report_model_name,
  ]
    .filter(Boolean)
    .join(", ")

  const isPromptModified =
    defaultTemplate !== "" && promptTemplate.trim() !== defaultTemplate.trim()

  const selectedProvider = providers.find((p) => p.id === providerId)
  const availableModels = useMemo(
    () => (selectedProvider ? parseModels(selectedProvider.model) : []),
    [selectedProvider],
  )

  const handleProviderChange = useCallback(
    (id: string) => {
      setProviderId(id)
      if (!id || id === "default" || id === "mock") {
        setModelName("")
      } else {
        const p = providers.find((pr) => pr.id === id)
        const models = p ? parseModels(p.model) : []
        setModelName(models.length === 1 ? models[0] : "")
      }
    },
    [providers],
  )

  const generateMutation = useMutation({
    mutationFn: (req: ReportGenerateRequest) =>
      Llm4AdReportsService.generateReport({
        taskId: task.id,
        requestBody: req,
      }),
    onSuccess: () => {
      setStreamingType(activeType)
      toast.success(t("evolution.insights.generateSuccess"))
      queryClient.invalidateQueries({
        queryKey: taskKeys.report(task.id, activeType),
      })
      queryClient.invalidateQueries({
        queryKey: taskKeys.detail(task.id),
      })
    },
    onError: (err: any) => {
      toast.error(
        err?.body?.detail ||
          err?.message ||
          t("evolution.insights.generateFailed"),
      )
    },
  })

  const handleGenerate = useCallback(() => {
    if (!isTaskReady) {
      toast.error(t("evolution.insights.taskNotReady"))
      return
    }
    if (typeConfig.needsNodes && selectedNodeIds.length < 2) {
      toast.error(t("evolution.insights.nodesRequired"))
      return
    }
    if (typeConfig.needsBestNode && !selectedBestNodeId) {
      toast.error(t("evolution.insights.bestNodeRequired"))
      return
    }

    const req: ReportGenerateRequest = {
      report_type: activeType,
      provider_id: providerId || undefined,
      model_name: modelName || undefined,
      prompt_template: promptTemplate.trim() || undefined,
      node_ids: typeConfig.needsNodes ? selectedNodeIds : undefined,
      best_node_id: typeConfig.needsBestNode ? selectedBestNodeId : undefined,
      language: i18n.language.startsWith("zh") ? "zh" : "en",
    }
    generateMutation.mutate(req)
  }, [
    isTaskReady,
    activeType,
    providerId,
    modelName,
    promptTemplate,
    selectedNodeIds,
    selectedBestNodeId,
    typeConfig,
    generateMutation,
    i18n,
    t,
  ])

  const stopMutation = useMutation({
    mutationFn: () =>
      Llm4AdReportsService.stopReport({
        taskId: task.id,
        reportType: activeType,
      }),
    onSuccess: () => {
      stopStream()
      setStreamingType(null)
      toast.success(t("evolution.insights.stopSuccess"))
      queryClient.invalidateQueries({
        queryKey: taskKeys.detail(task.id),
      })
      queryClient.invalidateQueries({
        queryKey: taskKeys.report(task.id, activeType),
      })
    },
    onError: (err: any) => {
      toast.error(
        err?.body?.detail || err?.message || t("evolution.insights.stopFailed"),
      )
    },
  })

  const toggleNodeId = (nodeId: string) => {
    const node = nodeMap.get(nodeId)
    if (!node) return
    toggleSelectedNode({
      id: node.id,
      generation: node.generation,
      island: node.island,
      islandId: node.islandId,
      name: node.name,
      fileName: node.fileName,
      score: node.score,
      rawScore: node.rawScore,
      parentIds: node.parentIds,
    })
  }

  const clearAllNodes = () => {
    for (const sn of selectedNodes) {
      toggleSelectedNode(sn)
    }
  }

  const displayContent =
    shouldStream && streamContent ? streamContent : currentReport?.content
  const displayError = shouldStream
    ? streamError
    : currentReport?.status === "failed"
      ? currentReport.error
      : null
  const displayStatus = shouldStream
    ? streamStatus
    : (currentReport?.status ?? null)

  const isCancelled = displayStatus === "cancelled"
  const progressText = useMemo(() => {
    if (!streamProgress) return t("evolution.insights.generating")
    if (streamProgress.stage === "prepare") {
      return t("evolution.insights.progress.prepare")
    }
    if (streamProgress.stage === "generate") {
      return t("evolution.insights.progress.generate")
    }
    return t(`evolution.insights.progress.${streamProgress.stage}`, {
      round: streamProgress.round ?? 1,
      completed: streamProgress.completed ?? 0,
      total: streamProgress.total ?? 0,
    })
  }, [streamProgress, t])

  return (
    <div className="flex flex-col h-full bg-card/50 overflow-hidden">
      {/* Report type tabs */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border/60 bg-card/80 shrink-0">
        {REPORT_TYPES.map((rt) => {
          const isActive = activeType === rt.key
          return (
            <button
              key={rt.key}
              type="button"
              onClick={() => setActiveType(rt.key)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                isActive
                  ? "bg-primary/15 text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/60"
              }`}
            >
              {t(`evolution.insights.${rt.key}`)}
            </button>
          )
        })}
      </div>

      {/* Toolbar */}
      <div className="border-b border-border/40 bg-card/30 shrink-0">
        {/* Row 1: Provider + Model (equal width) */}
        <div className="grid grid-cols-2 gap-2 px-3 pt-2 pb-1.5">
          {/* Provider selector */}
          <div className="relative">
            <Select
              value={providerId || undefined}
              onValueChange={handleProviderChange}
            >
              <SelectTrigger size="sm" className="w-full text-xs">
                <SelectValue
                  placeholder={t("evolution.insights.selectProvider")}
                />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">
                  <div className="flex items-center gap-1.5">
                    <Badge
                      variant="secondary"
                      className="text-[9px] font-medium px-1 py-0 bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30"
                    >
                      DEFAULT
                    </Badge>
                    <span className="text-muted-foreground">
                      {reportHint || t("evolution.insights.defaultProvider")}
                    </span>
                  </div>
                </SelectItem>
                <SelectItem value="mock">
                  <div className="flex items-center gap-1.5">
                    <Badge
                      variant="secondary"
                      className="text-[9px] font-medium px-1 py-0 bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30"
                    >
                      MOCK
                    </Badge>
                    <span className="text-muted-foreground">
                      {t("evolution.insights.mockProvider")}
                    </span>
                  </div>
                </SelectItem>
                {providers.length > 0 && <SelectSeparator />}
                {providers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    <div className="flex items-center gap-1.5">
                      <span>{p.name}</span>
                      <Badge
                        variant="outline"
                        className="text-[9px] font-normal"
                      >
                        {p.type}
                      </Badge>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {providerId && providerId !== "default" && (
              <button
                type="button"
                className="absolute right-7 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground rounded-sm p-0.5"
                onClick={() => handleProviderChange("default")}
              >
                <X className="size-3" />
              </button>
            )}
          </div>

          {/* Model selector */}
          <InsightModelInput
            value={
              !providerId || providerId === "default" || providerId === "mock"
                ? ""
                : modelName
            }
            onChange={setModelName}
            suggestions={availableModels}
            disabled={
              !providerId || providerId === "default" || providerId === "mock"
            }
            placeholder={
              !providerId || providerId === "default" || providerId === "mock"
                ? t("evolution.insights.defaultModel")
                : t("evolution.insights.inputModel")
            }
          />
        </div>

        {/* Row 2: Node selectors (left) + Prompt & Generate buttons (right) */}
        <div className="flex items-center gap-2 px-3 pb-2">
          {/* Node selectors (left side, conditional) */}
          <div className="flex items-center gap-2 flex-1 min-w-0 flex-wrap">
            {typeConfig.needsNodes && sortedNodes.length > 0 && (
              <NodeMultiSelect
                nodes={sortedNodes}
                selectedIds={selectedNodeIds}
                onToggle={toggleNodeId}
                onClear={clearAllNodes}
                label={t("evolution.insights.selectNodes")}
                selectedLabel={t("evolution.insights.selectedCount", {
                  count: selectedNodeIds.length,
                })}
              />
            )}
            {typeConfig.needsBestNode && championCandidates.length > 0 && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={!selectedBestNodeId}
                  onClick={() => requestFocusNode(selectedBestNodeId)}
                  title={t("evolution.insights.locateBestNode")}
                  className="inline-flex items-center justify-center size-7 rounded-md
                    border border-input bg-transparent text-muted-foreground
                    hover:bg-accent/60 hover:text-foreground transition-colors
                    disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Crosshair className="size-3.5" />
                </button>
                <NodeMultiSelect
                  nodes={championCandidates}
                  selectedIds={selectedBestNodeId ? [selectedBestNodeId] : []}
                  onToggle={(id) =>
                    setSelectedBestNodeId(id === selectedBestNodeId ? "" : id)
                  }
                  onClear={() => setSelectedBestNodeId("")}
                  label={t("evolution.insights.selectBestNode")}
                  selectedLabel={
                    championCandidates.find((n) => n.id === selectedBestNodeId)
                      ?.name ?? ""
                  }
                  singleSelect
                />
              </div>
            )}
          </div>

          {/* Prompt toggle + Generate buttons (right side, always present) */}
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              onClick={() => setPromptExpanded(!promptExpanded)}
              className={cn(
                "inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium transition-colors h-7",
                promptExpanded || promptTemplate.trim()
                  ? "bg-violet-500/15 text-violet-600 dark:text-violet-400 border border-violet-500/30"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/60 border border-input",
              )}
            >
              <ChevronDown
                className={cn(
                  "size-3 transition-transform",
                  promptExpanded && "rotate-180",
                )}
              />
              {t("evolution.insights.promptTemplate")}
              {promptTemplate.trim() && !promptExpanded && (
                <span className="size-1.5 rounded-full bg-violet-500" />
              )}
            </button>

            <button
              type="button"
              disabled={
                generateMutation.isPending || isStreaming || !isTaskReady
              }
              onClick={handleGenerate}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium
                bg-primary/15 text-primary border border-primary/30
                hover:bg-primary/25 transition-colors
                disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {generateMutation.isPending || isStreaming ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : currentReport ? (
                <RefreshCw className="size-3.5" />
              ) : (
                <Sparkles className="size-3.5" />
              )}
              {currentReport
                ? t("evolution.insights.regenerate")
                : t("evolution.insights.generate")}
            </button>
          </div>
        </div>

        {/* Row 3: Prompt template (collapsible) */}
        {promptExpanded && (
          <div className="px-3 pb-2">
            <Textarea
              value={promptTemplate}
              onChange={(e) => setPromptTemplate(e.target.value)}
              placeholder={t("evolution.insights.promptTemplatePlaceholder")}
              className="min-h-[72px] max-h-[280px] text-xs resize-y rounded-md border border-border/60 bg-muted/20"
              disabled={isStreaming || generateMutation.isPending}
            />
            {isPromptModified && (
              <button
                type="button"
                onClick={() => setPromptTemplate(defaultTemplate)}
                className="inline-flex items-center gap-1 mt-1.5 px-2 py-0.5 rounded text-xs
                  text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
              >
                <RotateCcw className="size-3" />
                {t("evolution.insights.resetDefaultTemplate")}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Streaming indicator (fixed, non-scrollable) */}
      {isStreaming && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-primary/20 bg-primary/5 shrink-0">
          <Loader2 className="size-3.5 animate-spin text-primary" />
          <span className="text-xs text-primary">
            {progressText}
          </span>
          <button
            type="button"
            disabled={stopMutation.isPending}
            onClick={() => stopMutation.mutate()}
            className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs
              text-destructive hover:bg-destructive/10 transition-colors
              disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Square className="size-3 fill-current" />
            {t("evolution.insights.stop")}
          </button>
        </div>
      )}

      {/* Content area */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Error state */}
        {displayError && (
          <div className="flex items-center gap-2 px-4 py-3 mx-4 mt-4 rounded-lg border border-destructive/30 bg-destructive/5">
            <AlertCircle className="size-4 text-destructive shrink-0" />
            <span className="text-xs text-destructive">{displayError}</span>
          </div>
        )}

        {/* Content */}
        {displayContent ? (
          <div className="px-6 py-4">
            {displayStatus === "completed" && !isStreaming && (
              <div className="flex items-center gap-1.5 mb-4 text-xs text-emerald-500">
                <CheckCircle2 className="size-3.5" />
                <span>{currentReport?.provider_model ?? ""}</span>
              </div>
            )}
            {isCancelled && !isStreaming && (
              <div className="flex items-center gap-1.5 mb-4 text-xs text-amber-500">
                <Square className="size-3" />
                <span>{t("evolution.insights.stopped")}</span>
              </div>
            )}
            <ReportMarkdown content={displayContent} />
          </div>
        ) : !displayError && !isStreaming ? (
          <EmptyState isTaskReady={isTaskReady} />
        ) : null}
      </div>
    </div>
  )
}

function EmptyState({ isTaskReady }: { isTaskReady: boolean }) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 py-16">
      <div
        className="p-4 rounded-full"
        style={{
          background:
            "radial-gradient(circle, color-mix(in srgb, var(--primary) 6%, transparent) 0%, transparent 70%)",
        }}
      >
        <FileText className="size-10 text-border" />
      </div>
      <div className="text-center space-y-1.5">
        <p className="text-sm text-muted-foreground">
          {isTaskReady
            ? t("evolution.insights.emptyDescription")
            : t("evolution.insights.taskNotReady")}
        </p>
      </div>
    </div>
  )
}

function NodeMultiSelect({
  nodes,
  selectedIds,
  onToggle,
  onClear,
  label,
  selectedLabel,
  singleSelect,
}: {
  nodes: { id: string; name: string; rawScore: number; generation: number }[]
  selectedIds: string[]
  onToggle: (id: string) => void
  onClear?: () => void
  label: string
  selectedLabel: string
  singleSelect?: boolean
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs
          border border-input bg-transparent hover:bg-accent/60 transition-colors h-7"
      >
        <span className="truncate max-w-[160px]">
          {selectedIds.length > 0 ? selectedLabel : label}
        </span>
        {selectedIds.length > 0 && onClear ? (
          <X
            className="size-3 text-muted-foreground hover:text-foreground shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onClear()
            }}
          />
        ) : (
          <ChevronsUpDown className="size-3 text-muted-foreground shrink-0" />
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="absolute z-50 top-full mt-1 left-0 w-72 max-h-64 overflow-y-auto
            rounded-lg border border-border bg-popover shadow-lg p-1"
          >
            {nodes.slice(0, 100).map((node) => {
              const isSelected = selectedIds.includes(node.id)
              return (
                <label
                  key={node.id}
                  className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-xs
                    hover:bg-accent cursor-pointer"
                  onClick={
                    singleSelect
                      ? () => {
                          onToggle(node.id)
                          setOpen(false)
                        }
                      : undefined
                  }
                >
                  {singleSelect ? (
                    <Check
                      className={cn(
                        "size-3 shrink-0",
                        isSelected ? "opacity-100" : "opacity-0",
                      )}
                    />
                  ) : (
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => onToggle(node.id)}
                      className="rounded border-border"
                    />
                  )}
                  <span className="truncate flex-1">{node.name}</span>
                  <span className="text-muted-foreground shrink-0">
                    G{node.generation} | {node.rawScore.toFixed(4)}
                  </span>
                </label>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

function InsightModelInput({
  value,
  onChange,
  suggestions,
  disabled,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  disabled?: boolean
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <Input
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => !disabled && suggestions.length > 0 && setOpen(true)}
        placeholder={placeholder}
        disabled={disabled}
        readOnly={disabled}
        className={cn(
          "w-full text-xs pr-7 rounded-md border border-input shadow-xs h-8",
          disabled && "bg-muted/50 cursor-not-allowed",
        )}
      />
      {!disabled && (
        <button
          type="button"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100 pointer-events-auto"
          onClick={() => {
            setOpen(!open)
            inputRef.current?.focus()
          }}
        >
          <ChevronDown className="size-4" />
        </button>
      )}
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 top-full mt-1 left-0 w-full max-h-48 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg p-1">
          {suggestions.map((model) => (
            <button
              key={model}
              type="button"
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs hover:bg-accent cursor-pointer",
                value === model && "bg-accent",
              )}
              onClick={() => {
                onChange(model)
                setOpen(false)
              }}
            >
              <Check
                className={cn(
                  "size-3 shrink-0",
                  value === model ? "opacity-100" : "opacity-0",
                )}
              />
              {model}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
