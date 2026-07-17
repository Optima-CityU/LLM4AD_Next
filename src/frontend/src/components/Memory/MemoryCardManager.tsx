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

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
import { authFetch } from "@/utils/auth"

import {
  DEFAULT_MEMORY_DRAFT,
  MEMORY_TYPES,
  type MemoryCard,
  type MemoryCardExtractionResponse,
  type MemoryCardPage,
  type MemoryCardDraft,
  type MemoryScope,
} from "./types"
import TagInput from "./TagInput"

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

const EXTRACTION_STAGE_LABELS: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    accepted: "正在连接 MindMemOS",
    buffering: "正在接收输入",
    chunking: "正在分析内容边界",
    llm_extracting: "正在提取结构化记忆",
    search_fielding: "正在生成检索线索",
    memory_planning: "正在整理记忆结构",
    embedding: "正在生成记忆向量",
    relationship_building: "正在建立记忆关系",
    ready_to_persist: "准备写入记忆",
    persisting: "正在写入记忆",
    finalizing: "正在整理记忆预览",
    completed: "记忆提取完成",
  },
  en: {
    accepted: "Connecting to MindMemOS",
    buffering: "Buffering input",
    chunking: "Analyzing content boundaries",
    llm_extracting: "Extracting structured memory",
    search_fielding: "Generating search hints",
    memory_planning: "Planning memory structure",
    embedding: "Generating memory vectors",
    relationship_building: "Building memory relationships",
    ready_to_persist: "Preparing to persist memory",
    persisting: "Persisting memory",
    finalizing: "Preparing memory preview",
    completed: "Memory extraction completed",
  },
}

const EXTRACTION_PROMPT_LANGUAGES: Array<{
  value: ExtractionPromptLanguage
  label: string
}> = [
  { value: "auto", label: "自动" },
  { value: "ZH", label: "中文" },
  { value: "EN", label: "English" },
]

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

const ONBOARDING_DEMO_CARD: MemoryCard = {
  id: "__onboarding_demo_stability_first__",
  type: "general_insight",
  title: "稳定性优先",
  content: "评估算法时优先比较稳定性、方差与失败率；单次最优结果不能替代重复实验结论。",
  enabled: true,
  source: "onboarding_demo",
  tags: ["稳定性", "评估"],
}
const ONBOARDING_DEMO_PREVIEW_ID = "__onboarding_demo_preview__"
const ONBOARDING_DEMO_CONTENT = "评估算法时优先比较稳定性、方差与失败率；单次最优结果不能替代重复实验结论。"

function toDraft(card: MemoryCard): MemoryCardDraft {
  return {
    id: card.id,
    type: MEMORY_TYPES.includes(card.type as (typeof MEMORY_TYPES)[number])
      ? card.type
      : "general_insight",
    title: card.title,
    content: card.content,
    enabled: card.enabled,
    tags: card.tags,
  }
}

function memoryTypeLabel(type: string) {
  const labels: Record<string, string> = {
    good_algorithm: "优秀算法经验",
    error_reflection: "错误反思",
    domain_knowledge: "领域知识",
    general_insight: "通用经验",
  }
  return labels[type] ?? type
}

function readOnlyRows(card: MemoryCard | null): Array<[string, string]> {
  if (!card) return []
  const info = card.readonly
  const rows: Array<[string, string]> = [
    ["记忆 ID", card.id],
    ["来源", info?.source || card.source],
    ["状态", info?.status || (card.enabled ? "active" : "archived")],
    ["实体", info?.entity_name || ""],
    ["属性", info?.property_name || ""],
    ["属性时间", info?.property_time || ""],
    ["更新时间", info?.last_update_at || ""],
    ["事件时间", info?.event_time || ""],
    ["来源时间", info?.source_timestamp || ""],
  ]
  return rows.filter(([, value]) => value.trim())
}

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
        return "MindMemOS 未配置系统级 Embedding，无法保存更新后的记忆。"
      }
      if (detail.includes("auth.invalid_api_key")) {
        return "MindMemOS 网关认证无效，请检查系统环境配置。"
      }
      if (
        detail.includes("Vector dimension error") ||
        detail.includes("expected dim")
      ) {
        return "MindMemOS 向量维度与当前绑定的 Embedding 模型不一致。请确认首次绑定的模型和维度，必要时重建本地 MindMemOS 向量数据。"
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
    return "MindMemOS 未配置系统级 Embedding，无法保存更新后的记忆。"
  }
  if (text.includes("Vector dimension error") || text.includes("expected dim")) {
    return "MindMemOS 向量维度与当前绑定的 Embedding 模型不一致。请确认首次绑定的模型和维度，必要时重建本地 MindMemOS 向量数据。"
  }
  return text.slice(0, 240) || fallback
}

function requestErrorMessage(error: unknown, fallback: string) {
  if (error instanceof DOMException && error.name === "AbortError") {
    return "已取消新增记忆，本次不会继续生成预览。"
  }
  if (error instanceof TypeError && /fetch|networkerror|network/i.test(error.message)) {
    return "请求连接被中断，MindMemOS 可能仍在后台写入；请稍后刷新列表或重试。"
  }
  return error instanceof Error ? error.message : fallback
}

async function readSseStream(
  response: Response,
  onEvent: (event: ExtractionStreamEvent) => void,
) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error("当前浏览器不支持流式响应")
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
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [previewItems, setPreviewItems] = useState<MemoryCard[]>([])
  const [selectedPreviewIds, setSelectedPreviewIds] = useState<string[]>([])
  const [isGeneratingPreview, setIsGeneratingPreview] = useState(false)
  const [isCancellingPreview, setIsCancellingPreview] = useState(false)
  const [extractionProgress, setExtractionProgress] = useState<{
    stage: string
    message: string
    percent?: number
  } | null>(null)
  const [extractionLogs, setExtractionLogs] = useState<string[]>([])
  const [isCommittingPreview, setIsCommittingPreview] = useState(false)
  const [isExtractionCloseConfirmOpen, setIsExtractionCloseConfirmOpen] = useState(false)
  const [selectedPromotionIds, setSelectedPromotionIds] = useState<string[]>([])
  const [isPromotionMode, setIsPromotionMode] = useState(false)
  const [previewTargetScope, setPreviewTargetScope] = useState<MemoryScope>(scope)
  const togglingIdsRef = useRef<Set<string>>(new Set())
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set())
  const skipNextPreviewDiscard = useRef(false)
  const extractionAbortRef = useRef<AbortController | null>(null)
  const onboardingDemoTimerRef = useRef<number | null>(null)

  const endpoint = cardsEndpoint(scope, projectId, taskId, page, pageSize)
  const mutationEndpoint = `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/memory/cards`
  const extractionEndpoint = `${mutationEndpoint}/extractions`
  const query = scopeQuery(scope, projectId, taskId)
  const projectPreviewQuery = scopeQuery("project", promotionProjectId)
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
        ...item,
        label: t(`memory.cardManager.extraction.languages.${item.value}`),
      }))
    ),
    [i18n.language, t],
  )

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
      if (!response.ok) throw new Error(await responseError(response, "加载记忆失败"))
      const payload = (await response.json()) as MemoryCardPage
      const items = payload.items ?? []
      setCards(items)
      setTotal(payload.total ?? items.length)
      onCountChange?.(payload.total ?? items.length)
      setHasMore(items.length > 0 && payload.has_more)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载记忆失败")
    } finally {
      setIsLoading(false)
    }
  }, [endpoint, loadEnabled, onCountChange])

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

  useEffect(() => {
    if (previewItems.length === 0 || isCommittingPreview) return
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", handleBeforeUnload)
    return () => window.removeEventListener("beforeunload", handleBeforeUnload)
  }, [isCommittingPreview, previewItems.length])

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
  const extractionStageLabel = (stage: string | undefined) => {
    if (!stage) return EXTRACTION_STAGE_LABELS[uiLang].accepted
    return EXTRACTION_STAGE_LABELS[uiLang][stage] ?? stage
  }
  const extractionEventMessage = (event: ExtractionStreamEvent) => {
    const stage = event.stage || event.event || "progress"
    return (
      event.message_i18n?.[uiLang] ||
      EXTRACTION_STAGE_LABELS[uiLang][stage] ||
      event.message ||
      EXTRACTION_STAGE_LABELS[uiLang].accepted
    )
  }

  const resetExtraction = useCallback(() => {
    setExtractionContent("")
    setExtractionPromptLanguage(normalizedDefaultExtractionPromptLanguage)
    setPreviewId(null)
    setPreviewItems([])
    setSelectedPreviewIds([])
    setIsGeneratingPreview(false)
    setIsCancellingPreview(false)
    setExtractionProgress(null)
    setExtractionLogs([])
    extractionAbortRef.current = null
    setIsCommittingPreview(false)
    setIsExtractionCloseConfirmOpen(false)
    setIsPromotionMode(false)
    setPreviewTargetScope(scope)
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

  const removeCards = useCallback((cardIds: string[]) => {
    const ids = new Set(cardIds)
    if (ids.size === 0) return
    setCards((current) => {
      const next = current.filter((card) => !ids.has(card.id))
      const removedCount = current.length - next.length
      if (removedCount > 0) {
        setTotal((value) => (value === null ? value : Math.max(0, value - removedCount)))
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
      toast.error("请先选择至少一条任务记忆")
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
    setDraft(toDraft(card))
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

  const discardPreview = useCallback(async () => {
    const ids = previewItems.map((item) => item.id)
    if (!previewId || ids.length === 0) {
      resetExtraction()
      setIsExtractionOpen(false)
      return
    }
    try {
      const response = await authFetch(
        `${extractionEndpoint}/${previewId}?${previewTargetScope === "project" ? projectPreviewQuery : query}`,
        {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ memory_ids: ids }),
        },
      )
      if (!response.ok) throw new Error(await responseError(response, "删除本次生成记忆失败"))
      if (previewTargetScope !== "project") removeCards(ids)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除本次生成记忆失败")
    } finally {
      resetExtraction()
      setIsExtractionOpen(false)
    }
  }, [
    extractionEndpoint,
    previewId,
    previewItems,
    previewTargetScope,
    projectPreviewQuery,
    query,
    removeCards,
    resetExtraction,
  ])

  const closeExtractionImmediately = useCallback(() => {
    resetExtraction()
    setIsExtractionOpen(false)
  }, [resetExtraction])

  const keepGeneratedCardsAndClose = useCallback(() => {
    closeExtractionImmediately()
  }, [closeExtractionImmediately])

  const requestCloseExtraction = useCallback(() => {
    if (isGeneratingPreview) {
      if (isPersistingPreview) {
        toast.info("记忆正在写入，当前阶段暂不可取消。")
        return
      }
      setIsCancellingPreview(true)
      extractionAbortRef.current?.abort()
      toast.info("已取消新增记忆")
      closeExtractionImmediately()
      return
    }
    if (isCommittingPreview) {
      toast.info("记忆正在保存，请等待当前操作完成。")
      return
    }
    if (previewId && previewItems.length > 0) {
      setIsExtractionCloseConfirmOpen(true)
      return
    }
    closeExtractionImmediately()
  }, [
    closeExtractionImmediately,
    isCommittingPreview,
    isGeneratingPreview,
    isPersistingPreview,
    previewId,
    previewItems.length,
  ])

  const handleExtractionOpenChange = (open: boolean) => {
    if (open) {
      skipNextPreviewDiscard.current = false
      setIsExtractionOpen(true)
      return
    }
    if (skipNextPreviewDiscard.current) skipNextPreviewDiscard.current = false
    else requestCloseExtraction()
  }

  const generatePreview = async () => {
    const content = extractionContent.trim()
    if (!isPromotionMode && !content) {
      toast.error("请先输入内容")
      return
    }
    if (isPromotionMode && (!promotionProjectId || selectedPromotionIds.length === 0)) {
      toast.error("请先选择至少一条任务记忆")
      return
    }
    if (disabled) {
      toast.error(disabledReason || "当前记忆管理不可用")
      return
    }
    setIsGeneratingPreview(true)
    setIsCancellingPreview(false)
    const initialMessage = EXTRACTION_STAGE_LABELS[uiLang].accepted
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
      if (!response.ok) throw new Error(await responseError(response, "生成记忆预览失败"))
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
          setExtractionProgress({ stage, message: message || "已取消" })
          setExtractionLogs((current) => [...current.slice(-5), message || "已取消"])
          return
        }
        if (event.event === "error") {
          throw new Error(message || "生成记忆预览失败")
        }
        if (event.event === "completed") {
          completed = true
          const items = event.items ?? []
          setPreviewId(event.preview_id ?? null)
          setPreviewItems(items)
          setSelectedPreviewIds(items.map((item) => item.id))
          setPreviewTargetScope(isPromotionMode ? "project" : scope)
          if (!isPromotionMode) {
            mergeCards(items)
          }
          if (items.length > 0 && !isPromotionMode) {
            refreshFirstPage()
          }
          setExtractionProgress({ stage: "completed", message, percent: event.percent ?? 100 })
          setExtractionLogs((current) => [...current.slice(-5), message])
          if (items.length === 0) {
            toast.info(event.message || "MindMemOS 没有提取出可保存的记忆")
          }
        }
      })
      if (!completed && !abortController.signal.aborted) {
        throw new Error("MindMemOS 流式响应提前结束")
      }
    } catch (error) {
      if (abortController.signal.aborted) {
        toast.info("已取消新增记忆，本次没有生成预览")
      } else {
        console.error(error)
        toast.error(requestErrorMessage(error, "生成记忆预览失败"))
      }
    } finally {
      setIsGeneratingPreview(false)
      setIsCancellingPreview(false)
      extractionAbortRef.current = null
    }
  }

  const commitPreview = async () => {
    if (!previewId) return
    if (selectedPreviewIds.length === 0) {
      toast.error("请至少选择一条要保存的记忆")
      return
    }
    setIsCommittingPreview(true)
    try {
      const response = await authFetch(
        `${extractionEndpoint}/${previewId}/commit?${previewTargetScope === "project" ? projectPreviewQuery : query}`,
        {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selected_ids: selectedPreviewIds,
          all_ids: previewItems.map((item) => item.id),
        }),
        },
      )
      if (!response.ok) throw new Error(await responseError(response, "启用选中记忆失败"))
      const payload = (await response.json()) as MemoryCardExtractionResponse
      toast.success("选中记忆已启用")
      skipNextPreviewDiscard.current = true
      setIsExtractionOpen(false)
      resetExtraction()
      if (previewTargetScope !== "project") {
        mergeCards(payload.items ?? [])
        refreshFirstPage()
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启用选中记忆失败")
    } finally {
      setIsCommittingPreview(false)
    }
  }

  const saveDraft = async () => {
    if (!draft.title.trim() || !draft.content.trim()) {
      toast.error("标题和内容不能为空")
      return
    }
    if (disabled) {
      toast.error(disabledReason || "当前记忆管理不可用")
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
            enabled: draft.enabled,
            tags: draft.tags,
          }),
        },
      )
      if (!response.ok) throw new Error(await responseError(response, "保存记忆失败"))
      const updatedCard = (await response.json()) as MemoryCard
      toast.success("记忆已保存")
      closeEditor()
      replaceCard(updatedCard)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存记忆失败")
    } finally {
      setIsSaving(false)
    }
  }

  const deleteCard = async () => {
    if (!deleteTarget) return
    if (disabled) {
      toast.error(disabledReason || "当前记忆管理不可用")
      return
    }
    try {
      const response = await authFetch(
        `${mutationEndpoint}/${deleteTarget.id}?${query}`,
        { method: "DELETE" },
      )
      if (!response.ok) throw new Error(await responseError(response, "删除记忆失败"))
      toast.success("记忆已删除")
      removeCard(deleteTarget.id)
      setDeleteTarget(null)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除记忆失败")
    }
  }

  const toggleCard = async (card: MemoryCard) => {
    if (interactionLocked) return
    if (disabled) {
      toast.error(disabledReason || "当前记忆管理不可用")
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
      if (!response.ok) throw new Error(await responseError(response, "保存记忆失败"))
      const updatedCard = (await response.json()) as MemoryCard
      toast.success(card.enabled ? "已禁用记忆" : "已启用记忆")
      replaceCard(updatedCard)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存记忆失败")
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
      setExtractionContent(ONBOARDING_DEMO_CONTENT)
      setExtractionPromptLanguage("ZH")
      setIsPromotionMode(false)
      setIsExtractionOpen(true)
      setPreviewId(ONBOARDING_DEMO_PREVIEW_ID)
      setPreviewItems([ONBOARDING_DEMO_CARD])
      setSelectedPreviewIds([ONBOARDING_DEMO_CARD.id])
      setPreviewTargetScope(scope)
      setIsGeneratingPreview(false)
      setIsCommittingPreview(false)
      setExtractionProgress({ stage: "completed", message: "记忆提取完成", percent: 100 })
      setExtractionLogs(["正在分析内容边界", "正在提取结构化记忆", "记忆提取完成"])
    }

    clearTimer()
    if (onboardingDemoPhase === null) {
      removeCard(ONBOARDING_DEMO_CARD.id)
      resetExtraction()
      setIsExtractionOpen(false)
      return clearTimer
    }

    if (onboardingDemoPhase === "input") {
      removeCard(ONBOARDING_DEMO_CARD.id)
      resetExtraction()
      setExtractionContent(ONBOARDING_DEMO_CONTENT)
      setExtractionPromptLanguage("ZH")
      setIsExtractionOpen(true)
      return clearTimer
    }

    if (onboardingDemoPhase === "generating") {
      setIsPromotionMode(false)
      setIsExtractionOpen(true)
      setExtractionContent(ONBOARDING_DEMO_CONTENT)
      setExtractionPromptLanguage("ZH")
      setPreviewId(null)
      setPreviewItems([])
      setSelectedPreviewIds([])
      setIsGeneratingPreview(true)
      setExtractionProgress({ stage: "llm_extracting", message: "正在提取结构化记忆", percent: 58 })
      setExtractionLogs(["正在分析内容边界", "正在提取结构化记忆"])
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
        mergeCards([ONBOARDING_DEMO_CARD])
        resetExtraction()
        setIsExtractionOpen(false)
        onOnboardingDemoComplete?.("saved")
      }, 350)
      return clearTimer
    }

    if (onboardingDemoPhase === "saved") {
      mergeCards([ONBOARDING_DEMO_CARD])
      setIsExtractionOpen(false)
      return clearTimer
    }

    replaceCard({ ...ONBOARDING_DEMO_CARD, enabled: onboardingDemoPhase === "enabled" })
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
  ])

  useEffect(() => {
    if (!interactionLocked) return
    setIsEditorOpen(false)
    setDeleteTarget(null)
    setIsExtractionCloseConfirmOpen(false)
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
      ? "禁用：后续任务不会注入这条记忆"
      : "启用：后续任务可检索并注入这条记忆"
    const toggleAriaLabel = isToggling
      ? card.enabled
        ? "正在禁用记忆"
        : "正在启用记忆"
      : card.enabled
        ? "禁用记忆"
        : "启用记忆"

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
            data-tour={card.id === ONBOARDING_DEMO_CARD.id ? "memory-onboarding-toggle" : undefined}
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
          "编辑记忆",
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled || onboardingDemoActive}
            aria-label="编辑记忆"
            onClick={() => openEdit(card)}
          >
            <Pencil className="size-4" />
          </Button>,
        )}
        {iconAction(
          "删除记忆",
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled || onboardingDemoActive}
            aria-label="删除记忆"
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
            "刷新记忆列表",
            <Button
              type="button"
              size="icon"
              variant="ghost"
              disabled={!loadEnabled}
              aria-label="刷新记忆列表"
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
            新增记忆
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
                提升到项目记忆{selectedPromotionIds.length > 0 ? ` (${selectedPromotionIds.length})` : ""}
              </Button>
            )}
            {iconAction(
              "刷新记忆列表",
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-8"
                disabled={!loadEnabled}
                aria-label="刷新记忆列表"
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
              新增记忆
            </Button>
          </div>
        )}
        {disabled && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
            {disabledReason || "当前记忆管理不可用"}
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
              placeholder="搜索标题、内容或标签"
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
              <SelectItem value="all">全部类型</SelectItem>
              {MEMORY_TYPES.map((type) => (
                <SelectItem key={type} value={type}>
                  {memoryTypeLabel(type)}
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
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="enabled">可注入</SelectItem>
              <SelectItem value="disabled">已禁用</SelectItem>
            </SelectContent>
          </Select>
          <div className={cn("flex rounded-md border", embedded ? "h-8 p-0.5" : "p-1")}>
            {iconAction(
              "卡片视图",
              <Button
                type="button"
                size="sm"
                variant={viewMode === "cards" ? "secondary" : "ghost"}
                aria-label="卡片视图"
                className={cn(embedded && "h-7 px-2")}
                onClick={() => setViewMode("cards")}
              >
                <Grid2X2 className={cn(embedded ? "size-3.5" : "size-4")} />
              </Button>,
            )}
            {iconAction(
              "列表视图",
              <Button
                type="button"
                size="sm"
                variant={viewMode === "list" ? "secondary" : "ghost"}
                aria-label="列表视图"
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
            第 {page} 页{total !== null ? `，共 ${total} 条` : ""}
            {hasLocalFilters ? `，本页匹配 ${visibleCards.length} 条` : ""}
          </span>
          <div className={cn("flex items-center gap-2", embedded && "flex-wrap")}>
            <span className={cn("text-xs text-muted-foreground", embedded && "text-[11px]")}>每页</span>
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
              上一页
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className={cn(embedded && "h-8 px-2 text-xs")}
              disabled={!hasMore || isLoading}
              onClick={() => setPage((current) => current + 1)}
            >
              下一页
            </Button>
          </div>
        </div>

        {isLoading ? (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            正在加载记忆...
          </div>
        ) : visibleCards.length === 0 ? (
          <div className="flex h-40 flex-col items-center justify-center rounded-md border border-dashed text-center">
            <Database className="mb-2 size-5 text-muted-foreground" />
            <p className="text-sm font-medium">{hasLocalFilters ? "暂无匹配记忆" : "暂无记忆"}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {hasLocalFilters ? "调整搜索或筛选条件后继续查看" : "新增记忆后继续查看"}
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
              <span>{canPromoteTaskCards ? "选择与标题" : "标题"}</span>
              <span>类型</span>
              <span>状态</span>
              {!embedded && <span>标签</span>}
              <span className="text-right">操作</span>
            </div>
            {visibleCards.map((card) => (
              <div
                key={card.id}
                data-tour={card.id === ONBOARDING_DEMO_CARD.id ? "memory-onboarding-card" : undefined}
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
                      aria-label={`选择记忆：${card.title}`}
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
                <Badge variant="outline" className="w-fit">{memoryTypeLabel(card.type)}</Badge>
                <Badge variant={card.enabled ? "secondary" : "outline"} className="w-fit">
                  {card.enabled ? "可注入" : "已禁用"}
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
              <div
                key={card.id}
                data-tour={card.id === ONBOARDING_DEMO_CARD.id ? "memory-onboarding-card" : undefined}
                className={cn(
                  "flex flex-col rounded-md border bg-background/70 p-3 transition hover:border-primary/40",
                  embedded ? "min-h-32" : "min-h-48",
                  !card.enabled && "opacity-60",
                  canPromoteTaskCards && selectedPromotionIds.includes(card.id) && "border-primary/60 bg-primary/5",
                  togglingIds.has(card.id) && "opacity-80",
                )}
              >
                <div className="flex items-start gap-2">
                  {canPromoteTaskCards && (
                    <Checkbox
                      checked={selectedPromotionIds.includes(card.id)}
                      aria-label={`选择记忆：${card.title}`}
                      disabled={disabled}
                      onCheckedChange={(value) => togglePromotionSelection(card.id, value === true)}
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate text-sm font-semibold">{card.title}</h3>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <Badge variant="outline">{memoryTypeLabel(card.type)}</Badge>
                      {!card.enabled && <Badge variant="secondary">已禁用</Badge>}
                    </div>
                  </div>
                  {renderActions(card)}
                </div>
                <p className={cn("mt-3 whitespace-pre-wrap text-sm text-muted-foreground", embedded ? "line-clamp-3" : "line-clamp-5")}>
                  {card.content}
                </p>
                {card.tags.length > 0 && (
                  <div className="mt-auto flex flex-wrap gap-1 pt-3">
                    {card.tags.map((tag) => (
                      <Badge
                        key={tag}
                        variant="secondary"
                        className="text-[10px]"
                      >
                        {tag}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
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
              {isPromotionMode ? "提升到项目记忆" : t("memory.cardManager.extraction.title")}
            </DialogTitle>
          </DialogHeader>

          <div className="grid gap-5">
            <div className="rounded-md border bg-muted/30 p-3">
              <div className="flex items-start gap-2">
                <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />
                <div className="space-y-1 text-sm">
                  <p className="font-medium">
                    {isPromotionMode ? "将已选任务经验归纳为项目记忆" : t("memory.cardManager.extraction.guideTitle")}
                  </p>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {isPromotionMode
                      ? `将使用 ${selectedPromotionIds.length} 条已选任务记忆生成项目记忆预览。原始卡片不会被修改。`
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
                        {extractionProgress?.message || "正在调用 MindMemOS 提取"}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        阶段：{extractionStageLabel(extractionProgress?.stage)}
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
                    <div className="font-medium text-foreground/80">处理记录</div>
                    {extractionLogs.slice(-3).map((log, index) => (
                      <div key={`${log}-${index}`} className="truncate">
                        {log}
                      </div>
                    ))}
                  </div>
                )}
                {isPersistingPreview && (
                  <p className="text-xs text-muted-foreground">
                    正在写入记忆，当前阶段暂不可取消。
                  </p>
                )}
              </div>
            )}

            {isExtractionCompleted && previewItems.length === 0 && (
              <div className="rounded-md border border-dashed bg-muted/30 px-4 py-5">
                <p className="text-sm font-medium">本次没有生成可保存的记忆</p>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  MindMemOS 已完成处理，但输入内容没有匹配到 LLM4AD 的记忆卡片结构。可以尝试补充更具体的算法经验、错误反思或领域知识。
                </p>
              </div>
            )}

            {previewItems.length > 0 && (
              <div className="grid gap-3" data-tour="memory-extraction-preview">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">
                      {isPromotionMode ? "项目记忆预览" : "提取结果预览"}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {isPromotionMode
                        ? "生成后已默认保存为已禁用项目记忆，启用后会在项目范围参与注入。"
                        : "生成后已默认保存为已禁用记忆，启用后才会参与注入。"}
                    </p>
                  </div>
                  <Badge variant="secondary">{selectedPreviewIds.length}/{previewItems.length} 已选择</Badge>
                </div>
                <div className="grid gap-2">
                  {previewItems.map((item) => {
                    const checked = selectedPreviewIds.includes(item.id)
                    return (
                      <label
                        key={item.id}
                        className={cn(
                          "flex cursor-pointer items-start gap-3 rounded-md border bg-background p-3 transition",
                          checked ? "border-primary/50" : "opacity-70",
                        )}
                      >
                          <Checkbox
                            checked={checked}
                            disabled={onboardingDemoActive}
                          onCheckedChange={(value) => {
                            setSelectedPreviewIds((current) =>
                              value === true
                                ? Array.from(new Set([...current, item.id]))
                                : current.filter((id) => id !== item.id),
                            )
                          }}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="min-w-0 truncate text-sm font-semibold">{item.title}</p>
                            <Badge variant="outline">{memoryTypeLabel(item.type)}</Badge>
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
                      </label>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          <DialogFooter className="gap-2">
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
                  ? "取消中"
                  : "取消生成"
                : isCommittingPreview
                  ? "保存中"
                  : "取消"}
            </Button>
            {previewItems.length === 0 ? (
              <Button
                type="button"
                disabled={onboardingDemoActive || isGeneratingPreview || disabled}
                onClick={() => void generatePreview()}
              >
                {isGeneratingPreview && <Loader2 className="mr-1 size-4 animate-spin" />}
                {isPromotionMode ? "生成项目预览" : "生成预览"}
              </Button>
            ) : (
              <Button
                type="button"
                disabled={onboardingDemoActive || isCommittingPreview || disabled || selectedPreviewIds.length === 0}
                onClick={() => void commitPreview()}
              >
                {isCommittingPreview ? (
                  <Loader2 className="mr-1 size-4 animate-spin" />
                ) : (
                  <Check className="mr-1 size-4" />
                )}
                启用选中
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={isExtractionCloseConfirmOpen}
        onOpenChange={setIsExtractionCloseConfirmOpen}
      >
        <AlertDialogContent inert={onboardingDemoActive}>
          <AlertDialogHeader>
            <AlertDialogTitle>{isPromotionMode ? "关闭项目记忆提升" : "关闭新增记忆"}</AlertDialogTitle>
            <AlertDialogDescription>
              {isPromotionMode
                ? "本次生成的项目记忆已经保存为已禁用状态。可以保留它们继续在项目记忆中管理，也可以删除本次生成的记忆。"
                : "本次生成的记忆已经保存为已禁用状态。可以保留它们继续在列表中管理，也可以删除本次生成的记忆。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <Button type="button" variant="outline" onClick={keepGeneratedCardsAndClose}>
              保留为已禁用
            </Button>
            <AlertDialogAction onClick={() => void discardPreview()}>
              删除本次生成
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={isEditorOpen} onOpenChange={setIsEditorOpen}>
        <DialogContent
          className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"
          inert={onboardingDemoActive}
        >
          <DialogHeader>
            <DialogTitle>{editingId ? "编辑记忆" : "新增记忆"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor={`${scope}-memory-title`}>标题</Label>
              <Input
                id={`${scope}-memory-title`}
                value={draft.title}
                onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                placeholder="简短、可扫描的记忆标题"
              />
            </div>
            <div className="grid gap-2 sm:grid-cols-[220px_1fr]">
              <div className="grid gap-2">
                <Label htmlFor={`${scope}-memory-type`}>类型</Label>
                <Select
                  value={draft.type}
                  onValueChange={(value) => setDraft((current) => ({ ...current, type: value }))}
                >
                  <SelectTrigger id={`${scope}-memory-type`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MEMORY_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {memoryTypeLabel(type)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor={`${scope}-memory-tags`}>标签</Label>
                <TagInput
                  id={`${scope}-memory-tags`}
                  value={draft.tags}
                  onChange={(tags) => setDraft((current) => ({ ...current, tags }))}
                  placeholder="输入标签后回车，如 TSP、参数调优"
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor={`${scope}-memory-content`}>内容</Label>
              <Textarea
                id={`${scope}-memory-content`}
                className="min-h-64 resize-y leading-6"
                value={draft.content}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, content: event.target.value }))
                }
                placeholder="写入可复用的经验、约束、反思或领域知识。建议使用清晰的短段落，方便后续检查和修改。"
              />
            </div>
            {editingCard && readOnlyRows(editingCard).length > 0 && (
              <div className="grid gap-3 rounded-md border bg-muted/20 p-3">
                <div>
                  <p className="text-sm font-medium">系统信息</p>
                  <p className="text-xs text-muted-foreground">
                    这些字段由 MindMemOS 管理，仅用于检查来源和时间，不会随编辑保存。
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  {readOnlyRows(editingCard).map(([label, value]) => (
                    <div key={label} className="grid gap-1.5">
                      <Label className="text-xs text-muted-foreground">{label}</Label>
                      <Input value={value} readOnly className="h-8 bg-background/70 text-xs" />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={closeEditor}>
              取消
            </Button>
            <Button type="button" disabled={isSaving || disabled} onClick={() => void saveDraft()}>
              {isSaving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {editingId ? "保存修改" : "添加记忆"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent inert={onboardingDemoActive}>
          <AlertDialogHeader>
            <AlertDialogTitle>永久删除记忆</AlertDialogTitle>
            <AlertDialogDescription>
              这会从 MindMemOS 中真正删除该记忆，删除后不会出现在管理页，也不能重新启用。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void deleteCard()}>永久删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
