import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertCircle,
  Check,
  ChevronsUpDown,
  Loader2,
  RefreshCw,
  Wand2,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { FileTreeNode } from "@/client"
import { Llm4AdReportsService, Llm4AdTasksService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"

import AnnotationDiffView from "./AnnotationDiffView"
import RecommendationCard from "./RecommendationCard"
import RecommendDetailDialog from "./RecommendDetailDialog"
import type {
  AdvisorResult,
  BlockRecommendation,
  DroppedCandidate,
  RepoRecommendations,
} from "./types"
import { getCommentStyle } from "./types"

const EVOLVE_MARKER_RE =
  /^\s*(?:#|\/\/|\/\*|<!--)\s*EVOLVE[_\s-](?:START|END)\b.*?(?:\*\/|-->)?\s*$/i

function stripEvolveMarkers(content: string): string {
  return content
    .split("\n")
    .filter((line) => !EVOLVE_MARKER_RE.test(line))
    .join("\n")
}

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

function hasFileNodes(nodes: FileTreeNode[]): boolean {
  for (const node of nodes) {
    if (node.type === "file") return true
    if (node.children && hasFileNodes(node.children)) return true
  }
  return false
}

interface RecommendTabProps {
  taskId: string
  readOnly?: boolean
}

export default function RecommendTab({ taskId, readOnly }: RecommendTabProps) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [providerId, setProviderId] = useState<string>("default")
  const [modelName, setModelName] = useState<string>("")
  const [goal, setGoal] = useState<string>("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [applyingId, setApplyingId] = useState<string | null>(null)
  const [detailRec, setDetailRec] = useState<BlockRecommendation | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [isPolling, setIsPolling] = useState(false)
  const [showFullDiff, setShowFullDiff] = useState(false)

  const { data: treeData } = useQuery({
    queryKey: ["dataTree", taskId],
    queryFn: () => Llm4AdTasksService.getTaskDataTree({ taskId }),
    select: (res) => (res as { tree: FileTreeNode[] }).tree ?? [],
  })
  const hasData = useMemo(
    () => !!treeData && hasFileNodes(treeData),
    [treeData],
  )

  const providersQuery = useProviders()
  const providers = providersQuery.data?.items ?? []

  const { data: defaultModels } = useUserDefaultModels()

  const reportHint = [
    defaultModels?.report_provider_name,
    defaultModels?.report_model_name,
  ]
    .filter(Boolean)
    .join(", ")

  const selectedProvider = providers.find((p) => p.id === providerId)
  const availableModels = useMemo(
    () => (selectedProvider ? parseModels(selectedProvider.model) : []),
    [selectedProvider],
  )

  const recommendQuery = useQuery({
    queryKey: ["recommend", taskId],
    queryFn: () => Llm4AdReportsService.getRecommend({ taskId }),
    enabled: !!taskId,
    staleTime: 10_000,
  })

  const result = recommendQuery.data as
    | AdvisorResult<RepoRecommendations>
    | undefined
  const status = result?.status ?? null
  const recommendations = result?.data ?? null
  const error = result?.error ?? null

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    setIsPolling(true)
    pollRef.current = setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ["recommend", taskId] })
    }, 3000)
  }, [taskId, queryClient])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    setIsPolling(false)
  }, [])

  useEffect(() => {
    if (status === "generating" && !isPolling) {
      startPolling()
    } else if (status !== "generating" && isPolling) {
      stopPolling()
    }
  }, [status, isPolling, startPolling, stopPolling])

  useEffect(() => {
    return () => stopPolling()
  }, [stopPolling])

  const allRecommendations = useMemo(() => {
    if (!recommendations) return []
    const list: BlockRecommendation[] = []
    if (recommendations.core) list.push(recommendations.core)
    list.push(...(recommendations.expanded ?? []))
    list.push(...(recommendations.alternatives ?? []))
    return list
  }, [recommendations])

  const selectedRec = useMemo(
    () => allRecommendations.find((r) => getRecId(r) === selectedId) ?? null,
    [allRecommendations, selectedId],
  )

  const { data: fileContent } = useQuery({
    queryKey: ["dataFile", taskId, selectedRec?.file_path],
    queryFn: () =>
      Llm4AdTasksService.getTaskDataFile({
        taskId,
        filePath: selectedRec!.file_path,
      }),
    enabled: !!selectedRec,
    select: (res) => (res as { content: string }).content ?? "",
  })

  const strippedContent = useMemo(() => {
    if (!fileContent) return ""
    return stripEvolveMarkers(fileContent)
  }, [fileContent])

  const modifiedContent = useMemo(() => {
    if (!selectedRec || !strippedContent) return ""
    return insertEvolveMarkers(
      strippedContent,
      selectedRec.file_path,
      selectedRec.line_start,
      selectedRec.line_end,
    )
  }, [selectedRec, strippedContent])

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

  const handleGenerate = async () => {
    setIsSubmitting(true)
    try {
      await Llm4AdReportsService.generateRecommend({
        taskId,
        requestBody: {
          goal: goal.trim(),
          language: i18n.language.startsWith("zh") ? "zh" : "en",
          provider_id:
            !providerId || providerId === "default" ? null : providerId,
          model_name: modelName || null,
        },
      })
      startPolling()
      queryClient.invalidateQueries({ queryKey: ["recommend", taskId] })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error(msg)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleApplyRec = useCallback(
    async (rec: BlockRecommendation) => {
      const recId = getRecId(rec)
      setApplyingId(recId)
      try {
        const res = await Llm4AdTasksService.getTaskDataFile({
          taskId,
          filePath: rec.file_path,
        })
        const content = (res as { content: string }).content ?? ""
        const stripped = stripEvolveMarkers(content)
        const modified = insertEvolveMarkers(
          stripped,
          rec.file_path,
          rec.line_start,
          rec.line_end,
        )
        await Llm4AdTasksService.updateTaskDataFile({
          taskId,
          requestBody: {
            file_path: rec.file_path,
            content: modified,
          },
        })
        toast.success(t("evolution.evolveAnnotation.applySuccess"))
      } catch {
        toast.error(t("evolution.evolveAnnotation.applyError"))
      } finally {
        setApplyingId(null)
      }
    },
    [taskId, t],
  )

  const isGenerating = status === "generating"
  const hasResult = status === "completed" && recommendations
  const hasFailed = status === "failed"
  const isEmpty = !status

  return (
    <div className="flex flex-col h-full px-4 py-2">
      {/* Config toolbar */}
      <div className="shrink-0 space-y-2 pb-3 border-b mb-2">
        <div className="grid grid-cols-2 gap-2">
          <div className="relative">
            <Select
              value={providerId || undefined}
              onValueChange={handleProviderChange}
            >
              <SelectTrigger className="w-full text-xs h-8">
                <SelectValue
                  placeholder={t("evolution.providerSelect.selectProvider")}
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
                      {reportHint || t("evolution.providerSelect.useDefault")}
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
                      {t("evolution.providerSelect.useMock")}
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
            {providerId && (
              <button
                type="button"
                className="absolute right-8 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground rounded-sm p-0.5"
                onClick={(e) => {
                  e.stopPropagation()
                  handleProviderChange("")
                }}
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>

          <ModelInput
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
                ? t("evolution.providerSelect.noModelNeeded")
                : t("evolution.providerSelect.inputModel")
            }
          />
        </div>

        <div className="flex items-center gap-2">
          <Input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder={t("evolution.evolveAnnotation.goalPlaceholder")}
            className="flex-1 h-8 text-xs"
            disabled={!hasData}
          />
          <Button
            size="sm"
            className="h-8 shrink-0"
            onClick={handleGenerate}
            disabled={isGenerating || isSubmitting || readOnly || !hasData}
            title={
              !hasData
                ? t("evolution.evolveAnnotation.dataNotReady")
                : undefined
            }
          >
            {isGenerating || isSubmitting ? (
              <Loader2 className="size-3.5 mr-1.5 animate-spin" />
            ) : hasResult || hasFailed ? (
              <RefreshCw className="size-3.5 mr-1.5" />
            ) : (
              <Wand2 className="size-3.5 mr-1.5" />
            )}
            {isGenerating || isSubmitting
              ? t("evolution.evolveAnnotation.generating")
              : hasResult || hasFailed
                ? t("evolution.evolveAnnotation.regenerate")
                : t("evolution.evolveAnnotation.getRecommendations")}
          </Button>
        </div>
      </div>

      {/* Data not ready warning */}
      {!hasData && (
        <div className="shrink-0 flex items-center gap-2 rounded-md px-3 py-2 mb-2 bg-amber-50 border border-amber-200 text-amber-700 dark:bg-amber-950/20 dark:border-amber-800/40 dark:text-amber-300">
          <AlertCircle className="size-3.5 shrink-0" />
          <span className="text-xs">
            {t("evolution.evolveAnnotation.dataNotReady")}
          </span>
        </div>
      )}

      {/* Content area */}
      <div className="rounded-lg border overflow-hidden flex-1 min-h-0">
        {isGenerating && (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <Loader2 className="size-8 animate-spin text-primary mx-auto mb-3" />
              <p className="text-sm text-muted-foreground">
                {t("evolution.evolveAnnotation.recommendingHint")}
              </p>
            </div>
          </div>
        )}

        {!isGenerating && isEmpty && (
          <div className="h-full flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <Wand2 className="size-10 mx-auto mb-3 opacity-40" />
              <p className="text-sm font-medium">
                {t("evolution.evolveAnnotation.recommendEmpty")}
              </p>
              <p className="text-xs mt-1 opacity-70">
                {t("evolution.evolveAnnotation.recommendEmptyHint")}
              </p>
            </div>
          </div>
        )}

        {!isGenerating && hasFailed && (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <AlertCircle className="size-10 mx-auto mb-3 text-destructive opacity-60" />
              <p className="text-sm font-medium text-destructive">
                {t("evolution.evolveAnnotation.recommendFailed")}
              </p>
              <p className="text-xs mt-2 text-muted-foreground max-w-md whitespace-pre-wrap">
                {error}
              </p>
            </div>
          </div>
        )}

        {!isGenerating && hasResult && recommendations && (
          <div className="h-full flex flex-col">
            <div className="flex-1 min-h-0 flex">
              {/* Left: recommendation list */}
              <div className="w-80 shrink-0 border-r overflow-y-auto p-3 space-y-2 bg-card">
                {allRecommendations.map((rec) => (
                  <RecommendationCard
                    key={getRecId(rec)}
                    recommendation={rec}
                    selected={getRecId(rec) === selectedId}
                    onSelect={() => setSelectedId(getRecId(rec))}
                    onApply={() => handleApplyRec(rec)}
                    onViewDetail={() => setDetailRec(rec)}
                    isApplying={applyingId === getRecId(rec)}
                    readOnly={readOnly}
                  />
                ))}

                {/* Dropped candidates */}
                {recommendations.dropped_candidates &&
                  recommendations.dropped_candidates.length > 0 && (
                    <DroppedCandidatesSection
                      candidates={recommendations.dropped_candidates}
                    />
                  )}
              </div>

              {/* Right: diff view */}
              <div className="flex-1 min-w-0">
                {selectedRec && strippedContent ? (
                  <AnnotationDiffView
                    filePath={selectedRec.file_path}
                    originalContent={strippedContent}
                    modifiedContent={modifiedContent}
                    showFull={showFullDiff}
                    onShowFullChange={setShowFullDiff}
                    contentKey={selectedId ?? undefined}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center text-muted-foreground">
                    <p className="text-sm">
                      {t("evolution.evolveAnnotation.selectToPreview")}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <RecommendDetailDialog
        recommendation={detailRec}
        open={!!detailRec}
        onOpenChange={(open) => !open && setDetailRec(null)}
      />
    </div>
  )
}

function DroppedCandidatesSection({
  candidates,
}: {
  candidates: DroppedCandidate[]
}) {
  const { t } = useTranslation()
  return (
    <div className="mt-3 pt-3 border-t">
      <h4 className="text-[11px] font-medium text-muted-foreground mb-2 flex items-center gap-1.5">
        <X className="size-3" />
        {t("evolution.evolveAnnotation.droppedCandidates")}
      </h4>
      <div className="space-y-1.5">
        {candidates.map((c, i) => (
          <div
            key={i}
            className="text-[11px] text-muted-foreground/70 bg-muted/30 rounded px-2 py-1.5"
          >
            <span className="font-mono">
              {c.file_path.split("/").pop()} L{c.line_start}-{c.line_end}
            </span>
            <span className="ml-2 text-[10px]">
              (
              {t(
                `evolution.evolveAnnotation.droppedReason.${c.reason}`,
                c.reason,
              )}
              )
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ModelInput({
  value,
  onChange,
  suggestions,
  disabled,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  disabled: boolean
  placeholder: string
}) {
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  return (
    <div ref={containerRef} className="relative">
      <Input
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => !disabled && suggestions.length > 0 && setOpen(true)}
        disabled={disabled}
        readOnly={disabled}
        placeholder={placeholder}
        className="h-8 text-xs pr-7"
      />
      {!disabled && (
        <button
          type="button"
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          onClick={() => {
            setOpen(!open)
            inputRef.current?.focus()
          }}
        >
          <ChevronsUpDown className="size-3.5" />
        </button>
      )}
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 top-full mt-1 left-0 w-full max-h-48 overflow-y-auto rounded-md border bg-popover p-1 shadow-md">
          {suggestions.map((model) => (
            <button
              key={model}
              type="button"
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs hover:bg-accent"
              onClick={() => {
                onChange(model)
                setOpen(false)
              }}
            >
              <Check
                className={`size-3.5 shrink-0 ${value === model ? "opacity-100" : "opacity-0"}`}
              />
              {model}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function getRecId(rec: BlockRecommendation): string {
  return `${rec.file_path}:${rec.line_start}-${rec.line_end}`
}

function insertEvolveMarkers(
  content: string,
  filePath: string,
  lineStart: number,
  lineEnd: number,
): string {
  const lines = content.split("\n")
  const [startMarker, endMarker] = getCommentStyle(filePath)

  const targetLine = lines[lineStart - 1] ?? ""
  const indent = targetLine.match(/^(\s*)/)?.[1] ?? ""

  lines.splice(lineStart - 1, 0, `${indent}${startMarker}`)
  lines.splice(lineEnd + 1, 0, `${indent}${endMarker}`)

  return lines.join("\n")
}
