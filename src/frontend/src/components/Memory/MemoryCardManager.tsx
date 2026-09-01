import {
  Check,
  Database,
  Grid2X2,
  List,
  Loader2,
  Pencil,
  Power,
  PowerOff,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import appI18n from "@/i18n"
import { authFetch } from "@/utils/auth"

import {
  DEFAULT_MEMORY_DRAFT,
  MEMORY_TYPES,
  type MemoryCard,
  type MemoryCardPage,
  type MemoryCardDraft,
  type MemoryScope,
} from "./types"
import {
  MemoryCardDeleteDialog,
  MemoryCardEditorDialog,
  MemoryCardTile,
  memoryCardToDraft,
  memoryTypeLabel,
} from "./MemoryCardPresentation"

type ViewMode = "cards" | "list"
type ExtractionPromptLanguage = "auto" | "ZH" | "EN"
export type OnboardingMemoryDemoPhase =
  | "input"
  | "generating"
  | "preview"
  | "saving"
  | "saved"
  | "disabled"
  | "enabled"
type ExtractionStreamEvent = {
  event?: "progress" | "completed" | "cancelled" | "error" | string
  stage?: string
  message?: string
  message_i18n?: {
    zh?: string
    en?: string
  }
  percent?: number
  preview_id?: string
  items?: MemoryCard[]
}

const EXTRACTION_PROMPT_LANGUAGES: ExtractionPromptLanguage[] = ["auto", "ZH", "EN"]

const DEFAULT_EXTRACTION_EXAMPLE_KEYS = [
  "algorithm",
  "reflection",
  "domain",
  "general",
] as const

const PROJECT_EXTRACTION_EXAMPLE_KEYS = [
  "constraints",
  "evaluation",
  "algorithm",
  "reflection",
] as const


function scopeQuery(scope: MemoryScope, projectId?: string, taskId?: string) {
  const params = new URLSearchParams({ scope })
  if (projectId) params.set("project_id", projectId)
  if (taskId) params.set("task_id", taskId)
  return params.toString()
}

function cardsEndpoint(
  scope: MemoryScope,
  projectId?: string,
  taskId?: string,
  page = 1,
  pageSize = 20,
) {
  const baseUrl = import.meta.env.VITE_API_URL || ""
  const query = new URLSearchParams(scopeQuery(scope, projectId, taskId))
  query.set("page", String(page))
  query.set("page_size", String(pageSize))
  return `${baseUrl}/api/v1/llm4ad/memory/cards?${query.toString()}`
}

async function responseError(response: Response, fallback: string) {
  const text = await response.text().catch(() => "")
  if (!text) return fallback
  try {
    const payload = JSON.parse(text)
    const detail = payload?.detail ?? payload
    if (typeof detail === "string") {
      try {
        const nested = JSON.parse(detail)
        if (nested?.code || nested?.message) {
          return [nested.code, nested.message].filter(Boolean).join("：")
        }
      } catch {
        // Use detail below.
      }
      if (detail.includes("No embed model endpoint configured")) {
        return appI18n.t("memory.cardManager.errors.embeddingMissing")
      }
      if (detail.includes("auth.invalid_api_key")) {
        return appI18n.t("memory.cardManager.errors.gatewayAuthInvalid")
      }
      if (
        detail.includes("Vector dimension error") ||
        detail.includes("expected dim")
      ) {
        return appI18n.t("memory.cardManager.errors.dimensionMismatch")
      }
      return detail
    }
    if (typeof detail?.message === "string" || typeof detail?.code === "string") {
      return [detail.code, detail.message].filter(Boolean).join("：")
    }
  } catch {
    // Use raw response below.
  }
  if (text.includes("No embed model endpoint configured")) {
    return appI18n.t("memory.cardManager.errors.embeddingMissing")
  }
  if (text.includes("Vector dimension error") || text.includes("expected dim")) {
    return appI18n.t("memory.cardManager.errors.dimensionMismatch")
  }
  return text.slice(0, 240) || fallback
}

function requestErrorMessage(error: unknown, fallback: string) {
  if (error instanceof DOMException && error.name === "AbortError") {
    return appI18n.t("memory.cardManager.messages.cancelledWithoutPreview")
  }
  if (error instanceof TypeError && /fetch|networkerror|network/i.test(error.message)) {
    return appI18n.t("memory.cardManager.errors.connectionInterrupted")
  }
  return error instanceof Error ? error.message : fallback
}

async function readSseStream(
  response: Response,
  onEvent: (event: ExtractionStreamEvent) => void,
) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error(appI18n.t("memory.cardManager.errors.streamingUnsupported"))
  const decoder = new TextDecoder()
  let buffer = ""
  let currentEvent = "message"
  let currentData = ""

  const flush = () => {
    if (!currentData) {
      currentEvent = "message"
      return
    }
    try {
      const parsed = JSON.parse(currentData) as ExtractionStreamEvent
      onEvent({ ...parsed, event: currentEvent })
    } finally {
      currentEvent = "message"
      currentData = ""
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) {
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim() || "message"
      } else if (line.startsWith("data:")) {
        currentData += line.slice(5).trim()
      } else if (line.trim() === "") {
        flush()
      }
    }
  }
  flush()
}

export default function MemoryCardManager({
  scope,
  projectId,
  taskId,
  title,
  description,
  disabled = false,
  disabledReason,
  loadEnabled = true,
  className,
  embedded = false,
  refreshSignal,
  onCountChange,
  defaultExtractionPromptLanguage = "auto",
  promotionProjectId,
  onboardingDemoActive = false,
  onboardingLocked = false,
  onboardingDemoPhase = null,
  onOnboardingDemoComplete,
}: {
  scope: MemoryScope
  projectId?: string
  taskId?: string
  title: string
  description: string
  disabled?: boolean
  disabledReason?: string
  loadEnabled?: boolean
  className?: string
  embedded?: boolean
  refreshSignal?: number | string | null
  onCountChange?: (count: number | null) => void
  defaultExtractionPromptLanguage?: ExtractionPromptLanguage
  promotionProjectId?: string
  onboardingDemoActive?: boolean
  /** Lock ordinary card management while a parent walkthrough is active. */
  onboardingLocked?: boolean
  onboardingDemoPhase?: OnboardingMemoryDemoPhase | null
  onOnboardingDemoComplete?: (phase: "preview" | "saved") => void
}) {
  const { t, i18n } = useTranslation()
  const uiLang: "zh" | "en" = i18n.language?.startsWith("zh") ? "zh" : "en"
  const [cards, setCards] = useState<MemoryCard[]>([])
  const [draft, setDraft] = useState<MemoryCardDraft>(DEFAULT_MEMORY_DRAFT)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [isEditorOpen, setIsEditorOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<MemoryCard | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>(embedded ? "list" : "cards")
  const [searchText, setSearchText] = useState("")
  const [typeFilter, setTypeFilter] = useState("all")
  const [statusFilter, setStatusFilter] = useState("all")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState<number | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isExtractionOpen, setIsExtractionOpen] = useState(false)
  const [extractionContent, setExtractionContent] = useState("")
  const [extractionPromptLanguage, setExtractionPromptLanguage] =
    useState<ExtractionPromptLanguage>("auto")
  const [previewItems, setPreviewItems] = useState<MemoryCard[]>([])
  const [isGeneratingPreview, setIsGeneratingPreview] = useState(false)
  const [isCancellingPreview, setIsCancellingPreview] = useState(false)
  const [extractionProgress, setExtractionProgress] = useState<{
    stage: string
    message: string
    percent?: number
  } | null>(null)
  const [extractionLogs, setExtractionLogs] = useState<string[]>([])
  const [isCommittingPreview, setIsCommittingPreview] = useState(false)
  const [selectedPromotionIds, setSelectedPromotionIds] = useState<string[]>([])
  const [isPromotionMode, setIsPromotionMode] = useState(false)
  const togglingIdsRef = useRef<Set<string>>(new Set())
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set())
  const extractionAbortRef = useRef<AbortController | null>(null)
  const onboardingDemoTimerRef = useRef<number | null>(null)

  const endpoint = cardsEndpoint(scope, projectId, taskId, page, pageSize)
  const mutationEndpoint = `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/memory/cards`
  const extractionEndpoint = `${mutationEndpoint}/extractions`
  const query = scopeQuery(scope, projectId, taskId)
  const scopeKey = `${scope}:${projectId ?? ""}:${taskId ?? ""}`
  const canPromoteTaskCards = scope === "task" && Boolean(promotionProjectId && taskId)
  const interactionLocked = onboardingDemoActive || onboardingLocked
  const normalizedDefaultExtractionPromptLanguage = (
    ["auto", "ZH", "EN"].includes(defaultExtractionPromptLanguage)
      ? defaultExtractionPromptLanguage
      : "auto"
  ) as ExtractionPromptLanguage
  const extractionPromptLanguages = useMemo(
    () => (
      EXTRACTION_PROMPT_LANGUAGES.map((item) => ({
        value: item,
        label: t(`memory.cardManager.extraction.languages.${item}`),
      }))
    ),
    [i18n.language, t],
  )
  const onboardingDemoCard = useMemo<MemoryCard>(() => ({
    id: "__onboarding_demo_stability_first__",
    type: "general_insight",
    title: t("memory.cardManager.onboarding.title"),
    content: t("memory.cardManager.onboarding.content"),
    enabled: true,
    source: "onboarding_demo",
    tags: [t("memory.cardManager.onboarding.tags.stability"), t("memory.cardManager.onboarding.tags.evaluation")],
  }), [i18n.language, t])
  const extractionStageLabel = (stage?: string) => {
    const normalized = stage || "accepted"
    const key = `memory.cardManager.stages.${normalized}`
    const label = t(key)
    return label === key ? normalized : label
  }

  const extractionExamples = useMemo(() => {
    const group = scope === "project" ? "project" : "default"
    const keys = group === "project"
      ? PROJECT_EXTRACTION_EXAMPLE_KEYS
      : DEFAULT_EXTRACTION_EXAMPLE_KEYS
    return keys.map((key) => ({
      label: t(`memory.cardManager.extraction.examples.${group}.${key}.label`),
      content: t(`memory.cardManager.extraction.examples.${group}.${key}.content`),
    }))
  }, [i18n.language, scope, t])

  const loadCards = useCallback(async () => {
    if (!loadEnabled) {
      setCards([])
      setTotal(null)
      setHasMore(false)
      setIsLoading(false)
      onCountChange?.(null)
      return
    }
    setIsLoading(true)
    try {
      const response = await authFetch(endpoint)
      if (!response.ok) throw new Error(await responseError(response, t("memory.cardManager.messages.loadFailed")))
      const payload = (await response.json()) as MemoryCardPage
      const items = payload.items ?? []
      setCards(items)
      setTotal(payload.total ?? items.length)
      onCountChange?.(payload.total ?? items.length)
      setHasMore(items.length > 0 && payload.has_more)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.cardManager.messages.loadFailed"))
    } finally {
      setIsLoading(false)
    }
  }, [endpoint, loadEnabled, onCountChange, t])

  const refreshFirstPage = useCallback(() => {
    setSearchText("")
    setTypeFilter("all")
    setStatusFilter("all")
    setPage(1)
    if (page === 1) {
      void loadCards()
    }
  }, [loadCards, page])

  useEffect(() => {
    setCards([])
    setTotal(null)
    setHasMore(false)
    setPage(1)
    setSelectedPromotionIds([])
  }, [scopeKey])

  useEffect(() => {
    void loadCards()
  }, [loadCards])

  useEffect(() => {
    if (refreshSignal === undefined || refreshSignal === null) return
    void loadCards()
  }, [refreshSignal, loadCards])

  const visibleCards = useMemo(() => {
    const needle = searchText.trim().toLowerCase()
    return [...cards]
      .filter((card) => {
        if (typeFilter !== "all" && card.type !== typeFilter) return false
        if (statusFilter === "enabled" && !card.enabled) return false
        if (statusFilter === "disabled" && card.enabled) return false
        if (!needle) return true
        return (
          card.title.toLowerCase().includes(needle) ||
          card.content.toLowerCase().includes(needle) ||
          card.tags.some((tag) => tag.toLowerCase().includes(needle))
        )
      })
      .sort((a, b) => {
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
        return a.title.localeCompare(b.title)
      })
  }, [cards, searchText, statusFilter, typeFilter])
  const hasLocalFilters =
    searchText.trim().length > 0 || typeFilter !== "all" || statusFilter !== "all"
  const listGridColumns = embedded
    ? "grid-cols-[minmax(0,1fr)_86px_72px_108px]"
    : "grid-cols-[minmax(180px,1.2fr)_130px_90px_minmax(140px,1fr)_120px]"
  const editingCard = useMemo(
    () => (editingId ? cards.find((card) => card.id === editingId) ?? null : null),
    [cards, editingId],
  )
  const isExtractionBusy = isGeneratingPreview || isCommittingPreview
  const isPersistingPreview = extractionProgress?.stage === "persisting"
  const isExtractionCompleted = extractionProgress?.stage === "completed"
  const extractionEventMessage = (event: ExtractionStreamEvent) => {
    const stage = event.stage || event.event || "progress"
    return (
      event.message_i18n?.[uiLang] ||
      extractionStageLabel(stage) ||
      event.message ||
      extractionStageLabel("accepted")
    )
  }

  const resetExtraction = useCallback(() => {
    setExtractionContent("")
    setExtractionPromptLanguage(normalizedDefaultExtractionPromptLanguage)
    setPreviewItems([])
    setIsGeneratingPreview(false)
    setIsCancellingPreview(false)
    setExtractionProgress(null)
    setExtractionLogs([])
    extractionAbortRef.current = null
    setIsCommittingPreview(false)
    setIsPromotionMode(false)
    setSelectedPromotionIds([])
  }, [normalizedDefaultExtractionPromptLanguage, scope])

  const replaceCard = useCallback((updatedCard: MemoryCard) => {
    setCards((current) => {
      const exists = current.some((card) => card.id === updatedCard.id)
      if (!exists) {
        setTotal((value) => (value === null ? value : value + 1))
        return [updatedCard, ...current]
      }
      return current.map((card) => (card.id === updatedCard.id ? updatedCard : card))
    })
  }, [])

  const removeCard = useCallback((cardId: string) => {
    setCards((current) => {
      const next = current.filter((card) => card.id !== cardId)
      if (next.length !== current.length) {
        setTotal((value) => (value === null ? value : Math.max(0, value - 1)))
      }
      return next
    })
  }, [])

  const mergeCards = useCallback((items: MemoryCard[]) => {
    if (items.length === 0) return
    setCards((current) => {
      const existingIds = new Set(current.map((card) => card.id))
      const incomingById = new Map(items.map((item) => [item.id, item]))
      const inserted = items.filter((item) => !existingIds.has(item.id))
      if (inserted.length > 0) {
        setTotal((value) => (value === null ? value : value + inserted.length))
      }
      return [
        ...inserted,
        ...current.map((card) => incomingById.get(card.id) ?? card),
      ]
    })
  }, [])

  const openCreate = () => {
    if (disabled || interactionLocked) return
    resetExtraction()
    setIsExtractionOpen(true)
  }

  const togglePromotionSelection = (cardId: string, checked: boolean) => {
    setSelectedPromotionIds((current) => (
      checked
        ? Array.from(new Set([...current, cardId]))
        : current.filter((id) => id !== cardId)
    ))
  }

  const openPromotion = () => {
    if (interactionLocked || !canPromoteTaskCards || !promotionProjectId || !taskId) return
    if (selectedPromotionIds.length === 0) {
      toast.error(t("memory.cardManager.messages.selectTaskMemory"))
      return
    }
    const selectedIds = selectedPromotionIds
    resetExtraction()
    setSelectedPromotionIds(selectedIds)
    setIsPromotionMode(true)
    setIsExtractionOpen(true)
  }

  const openEdit = (card: MemoryCard) => {
    if (disabled || interactionLocked) return
    setDraft(memoryCardToDraft(card))
    setEditingId(card.id)
    setIsEditorOpen(true)
  }

  const closeEditor = () => {
    setDraft(DEFAULT_MEMORY_DRAFT)
    setEditingId(null)
    setIsEditorOpen(false)
  }

  const setCardToggling = (cardId: string, value: boolean) => {
    if (value) {
      togglingIdsRef.current.add(cardId)
    } else {
      togglingIdsRef.current.delete(cardId)
    }
    setTogglingIds(new Set(togglingIdsRef.current))
  }

  const closeExtractionImmediately = useCallback(() => {
    resetExtraction()
    setIsExtractionOpen(false)
  }, [resetExtraction])

  const requestCloseExtraction = useCallback(() => {
    if (isGeneratingPreview) {
      if (isPersistingPreview) {
        toast.info(t("memory.cardManager.messages.cannotCancelPersisting"))
        return
      }
      setIsCancellingPreview(true)
      extractionAbortRef.current?.abort()
      toast.info(t("memory.cardManager.messages.addCancelled"))
      closeExtractionImmediately()
      return
    }
    if (isCommittingPreview) {
      toast.info(t("memory.cardManager.messages.savingInProgress"))
      return
    }
    closeExtractionImmediately()
  }, [
    closeExtractionImmediately,
    isCommittingPreview,
    isGeneratingPreview,
    isPersistingPreview,
    t,
  ])

  const handleExtractionOpenChange = (open: boolean) => {
    if (open) {
      setIsExtractionOpen(true)
      return
    }
    requestCloseExtraction()
  }

  const generatePreview = async () => {
    const content = extractionContent.trim()
    if (!isPromotionMode && !content) {
      toast.error(t("memory.cardManager.messages.enterContent"))
      return
    }
    if (isPromotionMode && (!promotionProjectId || selectedPromotionIds.length === 0)) {
      toast.error(t("memory.cardManager.messages.selectTaskMemory"))
      return
    }
    if (disabled) {
      toast.error(disabledReason || t("memory.cardManager.messages.unavailable"))
      return
    }
    setIsGeneratingPreview(true)
    setIsCancellingPreview(false)
    const initialMessage = extractionStageLabel("accepted")
    setExtractionProgress({ stage: "accepted", message: initialMessage, percent: 1 })
    setExtractionLogs([initialMessage])
    const abortController = new AbortController()
    extractionAbortRef.current = abortController
    try {
      const requestBody = isPromotionMode
        ? {
            project_id: promotionProjectId,
            task_id: taskId,
            memory_ids: selectedPromotionIds,
            ...(extractionPromptLanguage === "auto"
              ? {}
              : { prompt_language: extractionPromptLanguage }),
          }
        : extractionPromptLanguage === "auto"
          ? { content }
          : { content, prompt_language: extractionPromptLanguage }
      const streamUrl = isPromotionMode
        ? `${mutationEndpoint}/promotions/stream`
        : `${extractionEndpoint}/stream?${query}`
      const response = await authFetch(streamUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
        signal: abortController.signal,
      })
      if (!response.ok) throw new Error(await responseError(response, t("memory.cardManager.messages.previewFailed")))
      let completed = false
      await readSseStream(response, (event) => {
        const stage = event.stage || event.event || "progress"
        const message = extractionEventMessage(event)
        if (event.event === "progress") {
          setExtractionProgress({ stage, message, percent: event.percent })
          setExtractionLogs((current) => [...current.slice(-5), message])
          return
        }
        if (event.event === "heartbeat") {
          setExtractionProgress((current) => ({
            stage: current?.stage ?? stage,
            message,
            percent: current?.percent,
          }))
          return
        }
        if (event.event === "cancelled") {
          setExtractionProgress({ stage, message: message || t("memory.cardManager.messages.cancelled") })
          setExtractionLogs((current) => [...current.slice(-5), message || t("memory.cardManager.messages.cancelled")])
          return
        }
        if (event.event === "error") {
          throw new Error(message || t("memory.cardManager.messages.previewFailed"))
        }
        if (event.event === "completed") {
          completed = true
          const items = event.items ?? []
          setPreviewItems(items)
          if (!isPromotionMode) {
            mergeCards(items)
            refreshFirstPage()
          }
          setExtractionProgress({ stage: "completed", message, percent: event.percent ?? 100 })
          setExtractionLogs((current) => [...current.slice(-5), message])
          if (items.length === 0) {
            toast.info(event.message || t("memory.cardManager.messages.noExtractedMemory"))
          }
        }
      })
      if (!completed && !abortController.signal.aborted) {
        throw new Error(t("memory.cardManager.messages.streamEnded"))
      }
    } catch (error) {
      if (abortController.signal.aborted) {
        toast.info(t("memory.cardManager.messages.cancelledWithoutPreview"))
      } else {
        console.error(error)
        toast.error(requestErrorMessage(error, t("memory.cardManager.messages.previewFailed")))
      }
    } finally {
      setIsGeneratingPreview(false)
      setIsCancellingPreview(false)
      extractionAbortRef.current = null
    }
  }

  const saveDraft = async () => {
    if (!draft.title.trim() || !draft.structured_content.description.trim() || draft.structured_content.content.length === 0) {
      toast.error(t("memory.cardManager.messages.titleAndContentRequired"))
      return
    }
    if (disabled) {
      toast.error(disabledReason || t("memory.cardManager.messages.unavailable"))
      return
    }
    setIsSaving(true)
    try {
      const response = await authFetch(
        editingId ? `${mutationEndpoint}/${editingId}?${query}` : endpoint,
        {
          method: editingId ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            type: draft.type,
            title: draft.title.trim(),
            content: draft.content.trim(),
            structured_content: draft.structured_content,
            enabled: draft.enabled,
            tags: draft.tags,
          }),
        },
      )
      if (!response.ok) throw new Error(await responseError(response, t("memory.cardManager.messages.saveFailed")))
      const updatedCard = (await response.json()) as MemoryCard
      toast.success(t("memory.cardManager.messages.saved"))
      closeEditor()
      replaceCard(updatedCard)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.cardManager.messages.saveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  const deleteCard = async () => {
    if (!deleteTarget) return
    if (disabled) {
      toast.error(disabledReason || t("memory.cardManager.messages.unavailable"))
      return
    }
    try {
      const response = await authFetch(
        `${mutationEndpoint}/${deleteTarget.id}?${query}`,
        { method: "DELETE" },
      )
      if (!response.ok) throw new Error(await responseError(response, t("memory.cardManager.messages.deleteFailed")))
      toast.success(t("memory.cardManager.messages.deleted"))
      removeCard(deleteTarget.id)
      setDeleteTarget(null)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.cardManager.messages.deleteFailed"))
    }
  }

  const toggleCard = async (card: MemoryCard) => {
    if (interactionLocked) return
    if (disabled) {
      toast.error(disabledReason || t("memory.cardManager.messages.unavailable"))
      return
    }
    if (togglingIdsRef.current.has(card.id)) {
      return
    }
    setCardToggling(card.id, true)
    try {
      const response = await authFetch(
        `${mutationEndpoint}/${card.id}/status?${query}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !card.enabled }),
        },
      )
      if (!response.ok) throw new Error(await responseError(response, t("memory.cardManager.messages.saveFailed")))
      const updatedCard = (await response.json()) as MemoryCard
      toast.success(card.enabled ? t("memory.cardManager.messages.disabled") : t("memory.cardManager.messages.enabled"))
      replaceCard(updatedCard)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.cardManager.messages.saveFailed"))
    } finally {
      setCardToggling(card.id, false)
    }
  }

  useEffect(() => {
    const clearTimer = () => {
      if (onboardingDemoTimerRef.current !== null) {
        window.clearTimeout(onboardingDemoTimerRef.current)
        onboardingDemoTimerRef.current = null
      }
    }
    const showPreview = () => {
      setExtractionContent(onboardingDemoCard.content)
      setExtractionPromptLanguage("ZH")
      setIsPromotionMode(false)
      setIsExtractionOpen(true)
      setPreviewItems([onboardingDemoCard])
      setIsGeneratingPreview(false)
      setIsCommittingPreview(false)
      setExtractionProgress({ stage: "completed", message: extractionStageLabel("completed"), percent: 100 })
      setExtractionLogs([extractionStageLabel("chunking"), extractionStageLabel("llm_extracting"), extractionStageLabel("completed")])
    }

    clearTimer()
    if (onboardingDemoPhase === null) {
      removeCard(onboardingDemoCard.id)
      resetExtraction()
      setIsExtractionOpen(false)
      return clearTimer
    }

    if (onboardingDemoPhase === "input") {
      removeCard(onboardingDemoCard.id)
      resetExtraction()
      setExtractionContent(onboardingDemoCard.content)
      setExtractionPromptLanguage("ZH")
      setIsExtractionOpen(true)
      return clearTimer
    }

    if (onboardingDemoPhase === "generating") {
      setIsPromotionMode(false)
      setIsExtractionOpen(true)
      setExtractionContent(onboardingDemoCard.content)
      setExtractionPromptLanguage("ZH")
      setPreviewItems([])
      setIsGeneratingPreview(true)
      setExtractionProgress({ stage: "llm_extracting", message: extractionStageLabel("llm_extracting"), percent: 58 })
      setExtractionLogs([extractionStageLabel("chunking"), extractionStageLabel("llm_extracting")])
      onboardingDemoTimerRef.current = window.setTimeout(() => {
        onboardingDemoTimerRef.current = null
        showPreview()
        onOnboardingDemoComplete?.("preview")
      }, 650)
      return clearTimer
    }

    if (onboardingDemoPhase === "preview") {
      showPreview()
      return clearTimer
    }

    if (onboardingDemoPhase === "saving") {
      showPreview()
      setIsCommittingPreview(true)
      onboardingDemoTimerRef.current = window.setTimeout(() => {
        onboardingDemoTimerRef.current = null
        mergeCards([onboardingDemoCard])
        resetExtraction()
        setIsExtractionOpen(false)
        onOnboardingDemoComplete?.("saved")
      }, 350)
      return clearTimer
    }

    if (onboardingDemoPhase === "saved") {
      mergeCards([onboardingDemoCard])
      setIsExtractionOpen(false)
      return clearTimer
    }

    replaceCard({ ...onboardingDemoCard, enabled: onboardingDemoPhase === "enabled" })
    setIsExtractionOpen(false)
    return clearTimer
  }, [
    mergeCards,
    onboardingDemoPhase,
    onOnboardingDemoComplete,
    removeCard,
    replaceCard,
    resetExtraction,
    scope,
    onboardingDemoCard,
  ])

  useEffect(() => {
    if (!interactionLocked) return
    setIsEditorOpen(false)
    setDeleteTarget(null)
  }, [interactionLocked])

  const iconAction = (
    label: string,
    button: ReactNode,
  ) => (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )

  const renderActions = (card: MemoryCard) => {
    const isToggling = togglingIds.has(card.id)
    const toggleLabel = card.enabled
      ? t("memory.cardManager.actions.disableHint")
      : t("memory.cardManager.actions.enableHint")
    const toggleAriaLabel = isToggling
      ? card.enabled
        ? t("memory.cardManager.actions.disabling")
        : t("memory.cardManager.actions.enabling")
      : card.enabled
        ? t("memory.cardManager.actions.disable")
        : t("memory.cardManager.actions.enable")

    return (
      <div className="flex shrink-0 items-center gap-1">
        {iconAction(
          toggleLabel,
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled || isToggling || onboardingDemoActive}
            aria-label={toggleAriaLabel}
            className="transition-opacity"
            data-tour={card.id === onboardingDemoCard.id ? "memory-onboarding-toggle" : undefined}
            onClick={() => void toggleCard(card)}
          >
            {isToggling ? (
              <Loader2 className="size-4 animate-spin" />
            ) : card.enabled ? (
              <PowerOff className="size-4" />
            ) : (
              <Power className="size-4" />
            )}
          </Button>,
        )}
        {iconAction(
          t("memory.cardManager.actions.edit"),
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled || onboardingDemoActive}
            aria-label={t("memory.cardManager.actions.edit")}
            onClick={() => openEdit(card)}
          >
            <Pencil className="size-4" />
          </Button>,
        )}
        {iconAction(
          t("memory.cardManager.actions.delete"),
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled || onboardingDemoActive}
            aria-label={t("memory.cardManager.actions.delete")}
            className="text-destructive hover:text-destructive"
            onClick={() => setDeleteTarget(card)}
          >
            <Trash2 className="size-4" />
          </Button>,
        )}
      </div>
    )
  }

  return (
    <div
      inert={interactionLocked}
      className={cn(
        embedded ? "min-h-0 bg-transparent" : "rounded-lg border bg-card/60",
        className,
      )}
    >
      {!embedded && (
        <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
          <Database className="size-4 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold">{title}</h2>
            <p className="text-xs text-muted-foreground">{description}</p>
          </div>
          {iconAction(
            t("memory.cardManager.actions.refresh"),
            <Button
              type="button"
              size="icon"
              variant="ghost"
              disabled={!loadEnabled}
              aria-label={t("memory.cardManager.actions.refresh")}
              onClick={() => void loadCards()}
            >
              <RefreshCw className="size-4" />
            </Button>,
          )}
          <Button
            type="button"
            size="sm"
            className="gap-1.5"
            disabled={disabled || onboardingDemoActive}
            onClick={openCreate}
            data-tour="memory-add-button"
          >
            <Sparkles className="size-3.5" />
            {t("memory.cardManager.actions.add")}
          </Button>
        </div>
      )}

      <div className={cn("space-y-3", embedded ? "p-0" : "p-4")}>
        {embedded && (
          <div className="flex items-center justify-end gap-1">
            {canPromoteTaskCards && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 gap-1.5 px-2 text-xs"
                disabled={disabled || selectedPromotionIds.length === 0}
                onClick={openPromotion}
                data-tour="task-memory-promotion"
              >
                <Sparkles className="size-3.5" />
                {t("memory.cardManager.actions.promote")}{selectedPromotionIds.length > 0 ? ` (${selectedPromotionIds.length})` : ""}
              </Button>
            )}
            {iconAction(
              t("memory.cardManager.actions.refresh"),
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-8"
                disabled={!loadEnabled}
                aria-label={t("memory.cardManager.actions.refresh")}
                onClick={() => void loadCards()}
              >
                <RefreshCw className="size-3.5" />
              </Button>,
            )}
            <Button
              type="button"
              size="sm"
              className="h-8 gap-1.5 text-xs"
              disabled={disabled}
              onClick={openCreate}
            >
              <Sparkles className="size-3.5" />
              {t("memory.cardManager.actions.add")}
            </Button>
          </div>
        )}
        {disabled && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
            {disabledReason || t("memory.cardManager.messages.unavailable")}
          </div>
        )}
        <div
          className={cn(
            "gap-2",
            embedded
              ? "flex flex-wrap items-center"
              : "grid lg:grid-cols-[minmax(220px,1fr)_150px_140px_auto]",
          )}
        >
          <div className={cn("relative", embedded && "min-w-40 flex-1")}>
            <Search
              className={cn(
                "pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground",
                embedded ? "left-2.5 size-3.5" : "left-3 size-4",
              )}
            />
            <Input
              value={searchText}
              onChange={(event) => {
                setPage(1)
                setSearchText(event.target.value)
              }}
              className={cn(embedded ? "h-8 pl-8 text-xs" : "pl-9")}
              placeholder={t("memory.cardManager.filters.searchPlaceholder")}
            />
          </div>
          <Select
            value={typeFilter}
            onValueChange={(value) => {
              setPage(1)
              setTypeFilter(value)
            }}
          >
            <SelectTrigger className={cn(embedded && "h-8 w-[118px] text-xs")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("memory.cardManager.filters.allTypes")}</SelectItem>
              {MEMORY_TYPES.map((type) => (
                <SelectItem key={type} value={type}>
                  {memoryTypeLabel(type, t)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={statusFilter}
            onValueChange={(value) => {
              setPage(1)
              setStatusFilter(value)
            }}
          >
            <SelectTrigger className={cn(embedded && "h-8 w-[108px] text-xs")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("memory.cardManager.filters.allStatuses")}</SelectItem>
              <SelectItem value="enabled">{t("memory.cardManager.status.injectable")}</SelectItem>
              <SelectItem value="disabled">{t("memory.cardManager.status.disabled")}</SelectItem>
            </SelectContent>
          </Select>
          <div className={cn("flex rounded-md border", embedded ? "h-8 p-0.5" : "p-1")}>
            {iconAction(
              t("memory.cardManager.view.cards"),
              <Button
                type="button"
                size="sm"
                variant={viewMode === "cards" ? "secondary" : "ghost"}
                aria-label={t("memory.cardManager.view.cards")}
                className={cn(embedded && "h-7 px-2")}
                onClick={() => setViewMode("cards")}
              >
                <Grid2X2 className={cn(embedded ? "size-3.5" : "size-4")} />
              </Button>,
            )}
            {iconAction(
              t("memory.cardManager.view.list"),
              <Button
                type="button"
                size="sm"
                variant={viewMode === "list" ? "secondary" : "ghost"}
                aria-label={t("memory.cardManager.view.list")}
                className={cn(embedded && "h-7 px-2")}
                onClick={() => setViewMode("list")}
              >
                <List className={cn(embedded ? "size-3.5" : "size-4")} />
              </Button>,
            )}
          </div>
        </div>

        <div
          className={cn(
            "flex gap-3 text-xs text-muted-foreground",
            embedded ? "flex-wrap items-center justify-between" : "items-center justify-between",
          )}
        >
          <span className={cn(embedded && "text-[11px]")}>
            {t("memory.cardManager.pagination.page", { page })}
            {total !== null ? t("memory.cardManager.pagination.total", { total }) : ""}
            {hasLocalFilters ? t("memory.cardManager.pagination.matches", { count: visibleCards.length }) : ""}
          </span>
          <div className={cn("flex items-center gap-2", embedded && "flex-wrap")}>
            <span className={cn("text-xs text-muted-foreground", embedded && "text-[11px]")}>{t("memory.cardManager.pagination.perPage")}</span>
            <Select
              value={String(pageSize)}
              onValueChange={(value) => {
                setPage(1)
                setPageSize(Number(value))
              }}
            >
              <SelectTrigger className={cn("h-8 w-[76px]", embedded && "w-[68px] text-xs")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[10, 20, 50].map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className={cn(embedded && "h-8 px-2 text-xs")}
              disabled={page <= 1 || isLoading}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              {t("memory.cardManager.pagination.previous")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className={cn(embedded && "h-8 px-2 text-xs")}
              disabled={!hasMore || isLoading}
              onClick={() => setPage((current) => current + 1)}
            >
              {t("memory.cardManager.pagination.next")}
            </Button>
          </div>
        </div>

        {isLoading ? (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            {t("memory.cardManager.loading")}
          </div>
        ) : visibleCards.length === 0 ? (
          <div className="flex h-40 flex-col items-center justify-center rounded-md border border-dashed text-center">
            <Database className="mb-2 size-5 text-muted-foreground" />
            <p className="text-sm font-medium">{hasLocalFilters ? t("memory.cardManager.empty.filteredTitle") : t("memory.cardManager.empty.title")}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {hasLocalFilters ? t("memory.cardManager.empty.filteredDescription") : t("memory.cardManager.empty.description")}
            </p>
          </div>
        ) : viewMode === "list" ? (
          <div className="overflow-hidden rounded-md border">
            <div
              className={cn(
                "grid gap-3 border-b bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground",
                listGridColumns,
                embedded && "gap-2 px-2 py-1.5 text-[11px]",
              )}
            >
              <span>{canPromoteTaskCards ? t("memory.cardManager.columns.selectionAndTitle") : t("memory.cardManager.columns.title")}</span>
              <span>{t("memory.cardManager.columns.type")}</span>
              <span>{t("memory.cardManager.columns.status")}</span>
              {!embedded && <span>{t("memory.cardManager.columns.tags")}</span>}
              <span className="text-right">{t("memory.cardManager.columns.actions")}</span>
            </div>
            {visibleCards.map((card) => (
              <div
                key={card.id}
                data-tour={card.id === onboardingDemoCard.id ? "memory-onboarding-card" : undefined}
                className={cn(
                  "grid items-center gap-3 border-b px-3 py-2 last:border-b-0",
                  listGridColumns,
                  embedded && "gap-2 px-2 py-1.5",
                  !card.enabled && "opacity-60",
                  togglingIds.has(card.id) && "opacity-80",
                )}
              >
                <div className="flex min-w-0 items-center gap-2">
                  {canPromoteTaskCards && (
                    <Checkbox
                      checked={selectedPromotionIds.includes(card.id)}
                      aria-label={t("memory.cardManager.actions.select", { title: card.title })}
                      disabled={disabled}
                      onCheckedChange={(value) => togglePromotionSelection(card.id, value === true)}
                    />
                  )}
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => openEdit(card)}
                  >
                    <div className="truncate text-sm font-medium">{card.title}</div>
                    <div className="truncate text-xs text-muted-foreground">{card.content}</div>
                  </button>
                </div>
                <Badge variant="outline" className="w-fit">{memoryTypeLabel(card.type, t)}</Badge>
                <Badge variant={card.enabled ? "secondary" : "outline"} className="w-fit">
                  {card.enabled ? t("memory.cardManager.status.injectable") : t("memory.cardManager.status.disabled")}
                </Badge>
                {!embedded && (
                  <div className="flex min-w-0 flex-wrap gap-1">
                    {card.tags.slice(0, 3).map((tag) => (
                      <Badge
                        key={tag}
                        variant="secondary"
                        className="max-w-28 truncate text-[10px]"
                      >
                        {tag}
                      </Badge>
                    ))}
                  </div>
                )}
                <div className="ml-auto">{renderActions(card)}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className={cn("grid gap-3", embedded ? "grid-cols-1" : "md:grid-cols-2 xl:grid-cols-3")}>
            {visibleCards.map((card) => (
              <MemoryCardTile
                key={card.id}
                card={card}
                embedded={embedded}
                dataTour={card.id === onboardingDemoCard.id ? "memory-onboarding-card" : undefined}
                leading={canPromoteTaskCards ? (
                  <Checkbox
                    checked={selectedPromotionIds.includes(card.id)}
                    aria-label={t("memory.cardManager.actions.select", { title: card.title })}
                    disabled={disabled}
                    onCheckedChange={(value) => togglePromotionSelection(card.id, value === true)}
                  />
                ) : undefined}
                actions={renderActions(card)}
                className={cn(
                  canPromoteTaskCards && selectedPromotionIds.includes(card.id) && "border-primary/60 bg-primary/5",
                  togglingIds.has(card.id) && "opacity-80",
                )}
              />
            ))}
          </div>
        )}
      </div>

      <Dialog open={isExtractionOpen} onOpenChange={handleExtractionOpenChange}>
        <DialogContent
          className="max-h-[92vh] overflow-y-auto sm:max-w-4xl"
          showCloseButton={false}
          data-tour="memory-extraction-dialog"
          inert={onboardingDemoActive}
          onEscapeKeyDown={(event) => {
            event.preventDefault()
          }}
          onInteractOutside={(event) => {
            event.preventDefault()
          }}
        >
          <button
            type="button"
            aria-label={t("memory.cardManager.extraction.close")}
            disabled={isExtractionBusy || onboardingDemoActive}
            onClick={requestCloseExtraction}
            className="ring-offset-background focus:ring-ring absolute top-4 right-4 rounded-xs opacity-70 transition-opacity hover:opacity-100 focus:ring-2 focus:ring-offset-2 focus:outline-hidden disabled:pointer-events-none disabled:opacity-30 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0"
          >
            <X />
            <span className="sr-only">{t("memory.cardManager.extraction.close")}</span>
          </button>
          <DialogHeader>
            <DialogTitle>
              {isPromotionMode ? t("memory.cardManager.preview.promoteTitle") : t("memory.cardManager.extraction.title")}
            </DialogTitle>
          </DialogHeader>

          <div className="grid gap-5">
            <div className="rounded-md border bg-muted/30 p-3">
              <div className="flex items-start gap-2">
                <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />
                <div className="space-y-1 text-sm">
                  <p className="font-medium">
                    {isPromotionMode ? t("memory.cardManager.preview.promoteGuideTitle") : t("memory.cardManager.extraction.guideTitle")}
                  </p>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {isPromotionMode
                      ? t("memory.cardManager.preview.promoteGuideDescription", { count: selectedPromotionIds.length })
                      : t("memory.cardManager.extraction.guideDescription")}
                  </p>
                </div>
              </div>
            </div>

            {!isPromotionMode && (
              <div className="grid gap-2">
                <Label htmlFor={`${scope}-memory-extraction-content`}>
                  {t("memory.cardManager.extraction.contentLabel")}
                </Label>
                <Textarea
                  id={`${scope}-memory-extraction-content`}
                  aria-label={t("memory.cardManager.extraction.contentLabel")}
                  className="min-h-40 resize-y leading-6"
                  value={extractionContent}
                  onChange={(event) => setExtractionContent(event.target.value)}
                  disabled={onboardingDemoActive || isExtractionBusy || previewItems.length > 0}
                  data-tour="memory-extraction-content"
                  placeholder={t("memory.cardManager.extraction.placeholder")}
                />
              </div>
            )}

            <div className="grid gap-2 sm:max-w-xs">
              <Label htmlFor={`${scope}-memory-extraction-language`}>
                {t("memory.cardManager.extraction.languageLabel")}
              </Label>
              <Select
                value={extractionPromptLanguage}
                onValueChange={(value) => setExtractionPromptLanguage(value as ExtractionPromptLanguage)}
                disabled={onboardingDemoActive || previewItems.length > 0 || isExtractionBusy}
              >
                <SelectTrigger id={`${scope}-memory-extraction-language`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {extractionPromptLanguages.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {!isPromotionMode && (
              <div className="grid gap-2">
                <div className="text-xs font-medium text-muted-foreground">
                  {t("memory.cardManager.extraction.examplesTitle")}
                </div>
                <div className="grid gap-2 md:grid-cols-3">
                  {extractionExamples.map((example) => (
                    <button
                      key={example.label}
                      type="button"
                      disabled={onboardingDemoActive || isExtractionBusy || previewItems.length > 0}
                      className="rounded-md border bg-background p-3 text-left transition hover:border-primary/50 hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => setExtractionContent(example.content)}
                    >
                      <div className="text-sm font-medium">{example.label}</div>
                      <div className="mt-1 line-clamp-3 text-xs leading-5 text-muted-foreground">
                        {example.content}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {(isGeneratingPreview || extractionProgress) && (
              <div
                className="grid gap-3 rounded-md border border-primary/20 bg-primary/5 px-3 py-3"
                data-tour="memory-extraction-progress"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    {isGeneratingPreview ? (
                      <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
                    ) : (
                      <Check className="size-4 shrink-0 text-primary" />
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {extractionProgress?.message || t("memory.cardManager.stages.accepted")}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t("memory.cardManager.preview.stage", { stage: extractionStageLabel(extractionProgress?.stage) })}
                      </p>
                    </div>
                  </div>
                  {typeof extractionProgress?.percent === "number" && (
                    <Badge variant="secondary">{extractionProgress.percent}%</Badge>
                  )}
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-background">
                  <div
                    className="h-full rounded-full bg-primary transition-all duration-300"
                    style={{ width: `${Math.max(4, extractionProgress?.percent ?? 10)}%` }}
                  />
                </div>
                {extractionLogs.length > 0 && (
                  <div className="grid gap-1 text-xs text-muted-foreground">
                    <div className="font-medium text-foreground/80">{t("memory.cardManager.preview.logs")}</div>
                    {extractionLogs.slice(-3).map((log, index) => (
                      <div key={`${log}-${index}`} className="truncate">
                        {log}
                      </div>
                    ))}
                  </div>
                )}
                {isPersistingPreview && (
                  <p className="text-xs text-muted-foreground">
                    {t("memory.cardManager.messages.cannotCancelPersisting")}
                  </p>
                )}
              </div>
            )}

            {isExtractionCompleted && previewItems.length === 0 && (
              <div className="rounded-md border border-dashed bg-muted/30 px-4 py-5">
                <p className="text-sm font-medium">{t("memory.cardManager.preview.emptyTitle")}</p>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  {t("memory.cardManager.preview.emptyDescription")}
                </p>
              </div>
            )}

            {previewItems.length > 0 && (
              <div className="grid gap-3" data-tour="memory-extraction-preview">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">
                      {isPromotionMode ? t("memory.cardManager.preview.projectTitle") : t("memory.cardManager.preview.title")}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {isPromotionMode
                        ? t("memory.cardManager.preview.projectDescription")
                        : t("memory.cardManager.preview.description")}
                    </p>
                  </div>
                  <Badge variant="secondary">{previewItems.length}</Badge>
                </div>
                <div className="grid gap-2">
                  {previewItems.map((item) => (
                    <div
                      key={item.id}
                      className="flex items-start gap-3 rounded-md border bg-background p-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="min-w-0 truncate text-sm font-semibold">{item.title}</p>
                          <Badge variant="outline">{memoryTypeLabel(item.type, t)}</Badge>
                          {item.operation && (
                            <Badge variant="secondary">
                              {t(`memory.cardManager.operations.${item.operation}`)}
                            </Badge>
                          )}
                        </div>
                        <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                          {item.content}
                        </p>
                        {item.tags.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {item.tags.map((tag) => (
                              <Badge key={tag} variant="secondary" className="text-[10px]">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <DialogFooter className="gap-2">
            {!isExtractionCompleted && (
              <Button
                type="button"
                variant="outline"
                disabled={onboardingDemoActive || isCommittingPreview || isCancellingPreview || isPersistingPreview}
                onClick={requestCloseExtraction}
              >
                {isGeneratingPreview || isCancellingPreview ? (
                  <Loader2 className="mr-1 size-4 animate-spin" />
                ) : (
                  <X className="mr-1 size-4" />
                )}
                {isGeneratingPreview
                  ? isCancellingPreview
                    ? t("memory.cardManager.actions.cancelling")
                    : t("memory.cardManager.actions.cancelGeneration")
                  : isCommittingPreview
                    ? t("memory.cardManager.actions.saving")
                    : t("memory.common.cancel")}
              </Button>
            )}
            {!isExtractionCompleted ? (
              <Button
                type="button"
                disabled={onboardingDemoActive || isGeneratingPreview || disabled}
                onClick={() => void generatePreview()}
              >
                {isGeneratingPreview && <Loader2 className="mr-1 size-4 animate-spin" />}
                {isPromotionMode ? t("memory.cardManager.actions.promote") : t("memory.cardManager.actions.extractAndSave")}
              </Button>
            ) : (
              <Button
                type="button"
                disabled={onboardingDemoActive || isCommittingPreview}
                onClick={requestCloseExtraction}
              >
                <Check className="mr-1 size-4" />
                {t("memory.common.close")}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <MemoryCardEditorDialog
        open={isEditorOpen}
        scopeId={scope}
        draft={draft}
        editingCard={editingCard}
        saving={isSaving}
        disabled={disabled}
        interactionLocked={onboardingDemoActive}
        onOpenChange={setIsEditorOpen}
        onDraftChange={setDraft}
        onCancel={closeEditor}
        onSave={() => void saveDraft()}
      />

      <MemoryCardDeleteDialog
        card={deleteTarget}
        interactionLocked={onboardingDemoActive}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        onConfirm={() => void deleteCard()}
      />
    </div>
  )
}
