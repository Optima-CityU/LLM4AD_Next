import {
  AlertTriangle,
  ArrowLeft,
  BookOpenText,
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  FilePenLine,
  FileText,
  FileUp,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Square,
  Trash2,
  Zap,
} from "lucide-react"
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
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
import { Textarea } from "@/components/ui/textarea"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { useProviders } from "@/hooks/useProviders"
import { cn } from "@/lib/utils"
import { authFetch } from "@/utils/auth"

import {
  appendParseActivity,
  groupParseActivities,
  parseActivityFromEvent,
  type ParseActivity,
} from "./progress"
import type {
  KnowledgeContent,
  KnowledgeDocument,
  KnowledgeParsePlan,
  KnowledgeParseRun,
  KnowledgeParserBinding,
  KnowledgeSource,
  KnowledgeSourceDetail,
  KnowledgeSourceFile,
} from "./types"

type SelectedNode =
  | { kind: "sourceFile"; id: string }
  | { kind: "document"; id: string }
type ViewMode = "preview" | "edit" | "split"

const apiBase = `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/knowledge`
const maxFileBytes = 20 * 1024 * 1024
const maxTopicBytes = 100 * 1024 * 1024
const maxTopicFiles = 20
const progressStreamReconnectDelays = [1000, 2000, 4000]
const progressStreamToastId = "knowledge-progress-stream-interrupted"
const terminalProgressEvents = new Set(["done", "error", "stale", "cancelled"])
const knowledgeLibraryCollapsedKey = "knowledge-library-collapsed"

function storedLibraryCollapsed() {
  if (typeof window === "undefined") return false
  try {
    return window.localStorage.getItem(knowledgeLibraryCollapsedKey) === "true"
  } catch {
    return false
  }
}

async function responseError(response: Response, fallback: string) {
  try {
    const payload = await response.json()
    return typeof payload?.detail === "string" ? payload.detail : fallback
  } catch {
    return fallback
  }
}

async function readProgressStream(
  response: Response,
  onEvent: (event: Record<string, unknown>) => void,
  unsupportedMessage: string,
  onEventId: (eventId: string) => void,
) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error(unsupportedMessage)
  const decoder = new TextDecoder()
  let buffer = ""
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n")
    const frames = buffer.split("\n\n")
    buffer = frames.pop() || ""
    for (const frame of frames) {
      const lines = frame.split("\n")
      const eventId = lines
        .find((line) => line.startsWith("id:"))
        ?.slice(3)
        .trim()
      const eventName = lines
        .find((line) => line.startsWith("event:"))
        ?.slice(6)
        .trim()
      const data = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n")
      if (!data) continue
      try {
        const payload = JSON.parse(data) as Record<string, unknown>
        if (eventName && !payload.type) payload.type = eventName
        if (eventId) onEventId(eventId)
        onEvent(payload)
      } catch {
        // A malformed optional event must not stop the progress stream.
      }
    }
  }
}

function waitForProgressReconnect(delay: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException("Aborted", "AbortError"))
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort)
      resolve()
    }, delay)
    if (signal.aborted) onAbort()
    else signal.addEventListener("abort", onAbort, { once: true })
  })
}

async function followProgressStream(
  url: string,
  controller: AbortController,
  onEvent: (event: Record<string, unknown>) => void,
  unsupportedMessage: string,
  onInterrupted: () => void,
  onRecovered: () => void,
) {
  let lastEventId = "0-0"
  let interrupted = false

  for (let attempt = 0; ; attempt += 1) {
    try {
      const separator = url.includes("?") ? "&" : "?"
      const response = await authFetch(
        `${url}${separator}last_id=${encodeURIComponent(lastEventId)}`,
        { signal: controller.signal },
      )
      if (!response.ok)
        throw new Error(await responseError(response, unsupportedMessage))
      if (interrupted) {
        interrupted = false
        onRecovered()
      }

      let terminal = false
      await readProgressStream(
        response,
        (event) => {
          terminal ||= terminalProgressEvents.has(String(event.type || ""))
          onEvent(event)
        },
        unsupportedMessage,
        (eventId) => {
          lastEventId = eventId
        },
      )
      if (terminal) return
      throw new Error(unsupportedMessage)
    } catch (error) {
      if (
        controller.signal.aborted ||
        (error instanceof DOMException && error.name === "AbortError")
      )
        throw error
      if (attempt >= progressStreamReconnectDelays.length) throw error
      if (!interrupted) {
        interrupted = true
        onInterrupted()
      }
      await waitForProgressReconnect(
        progressStreamReconnectDelays[attempt],
        controller.signal,
      )
    }
  }
}

function statusClass(status: KnowledgeSource["parse_status"]) {
  if (status === "ready")
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
  if (status === "failed")
    return "border-destructive/30 bg-destructive/10 text-destructive"
  if (status === "stale")
    return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
  if (status === "running" || status === "pending")
    return "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300"
  return ""
}

function providerModels(provider?: { model?: string }) {
  return (provider?.model || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
}

function isActiveParseStatus(status?: string | null) {
  return status === "pending" || status === "running"
}

function activityFromJob(
  job: Pick<KnowledgeParsePlan, "status" | "progress" | "stage" | "message"> | null,
): ParseActivity | null {
  if (!job?.message?.trim()) return null
  return {
    type:
      job.status === "failed"
        ? "error"
        : job.status === "cancelled"
          ? "cancelled"
          : "progress",
    progress: job.progress,
    stage: job.stage,
    message: job.message,
  }
}

function shortMinutes(milliseconds: number) {
  return milliseconds < 60_000
    ? "<1"
    : String(Math.max(1, Math.floor(milliseconds / 60_000)))
}

export default function KnowledgeWorkspace() {
  const { t } = useTranslation()
  const markdownId = useId()
  useHljsTheme()
  const markdownComponents = useMemo(
    () => makeMarkdownComponents(`knowledge-${markdownId}`),
    [markdownId],
  )
  const providersQuery = useProviders()
  const providers = providersQuery.data?.items ?? []
  const addInputRef = useRef<HTMLInputElement>(null)
  const streamAbortRef = useRef<AbortController | null>(null)

  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [detail, setDetail] = useState<KnowledgeSourceDetail | null>(null)
  const [selectedNode, setSelectedNode] = useState<SelectedNode | null>(null)
  const [content, setContent] = useState<KnowledgeContent | null>(null)
  const [draft, setDraft] = useState("")
  const [titleDraft, setTitleDraft] = useState("")
  const [topicTitleDraft, setTopicTitleDraft] = useState("")
  const [viewMode, setViewMode] = useState<ViewMode>("preview")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [parseRun, setParseRun] = useState<KnowledgeParseRun | null>(null)
  const [parsePlan, setParsePlan] = useState<KnowledgeParsePlan | null>(null)
  const [parseEvents, setParseEvents] = useState<ParseActivity[]>([])
  const [parseBackground, setParseBackground] = useState("")
  const [parseMode, setParseMode] = useState<"direct" | "planned">("planned")
  const [planInteractionMode, setPlanInteractionMode] = useState<
    "quick" | "collaborative"
  >("quick")
  const [planAnswers, setPlanAnswers] = useState<Record<string, string>>({})
  const [planAnswering, setPlanAnswering] = useState(false)
  const [selectedStrategyId, setSelectedStrategyId] = useState("")
  const [planAdjustment, setPlanAdjustment] = useState("")
  const [parsePreparing, setParsePreparing] = useState(false)
  const [parseStarting, setParseStarting] = useState(false)
  const [planStarting, setPlanStarting] = useState(false)
  const [planRetrying, setPlanRetrying] = useState(false)
  const [parseStopping, setParseStopping] = useState(false)
  const [progressClock, setProgressClock] = useState(() => Date.now())
  const [libraryCollapsed, setLibraryCollapsed] = useState(
    storedLibraryCollapsed,
  )

  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [uploadTitle, setUploadTitle] = useState("")

  const [binding, setBinding] = useState<KnowledgeParserBinding | null>(null)
  const [providerId, setProviderId] = useState("")
  const [modelName, setModelName] = useState("")
  const [bindingDialogOpen, setBindingDialogOpen] = useState(false)
  const [bindingSaving, setBindingSaving] = useState(false)
  const showProgressStreamInterrupted = useCallback(
    () =>
      toast.warning(t("knowledge.parseWorkspace.streamInterrupted"), {
        id: progressStreamToastId,
        duration: 5000,
      }),
    [t],
  )
  const dismissProgressStreamInterrupted = useCallback(
    () => toast.dismiss(progressStreamToastId),
    [],
  )
  const selectedProvider = providers.find((item) => item.id === providerId)
  const availableModels = useMemo(
    () => providerModels(selectedProvider),
    [selectedProvider],
  )
  const selectedPlanStrategy =
    parsePlan?.payload?.strategies.find(
      (strategy) => strategy.id === selectedStrategyId,
    ) ??
    parsePlan?.payload?.strategies.find(
      (strategy) => strategy.id === parsePlan.payload?.recommended_strategy_id,
    )
  const parseActivityGroups = useMemo(
    () => groupParseActivities(parseEvents),
    [parseEvents],
  )

  const loadSources = useCallback(
    async (preferredId?: string) => {
      const response = await authFetch(`${apiBase}/sources?limit=100`)
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.load")),
        )
      const payload = (await response.json()) as { items: KnowledgeSource[] }
      setSources(payload.items)
      setSelectedSourceId((current) => {
        const next = preferredId || current
        return next && payload.items.some((item) => item.id === next)
          ? next
          : payload.items[0]?.id || null
      })
    },
    [t],
  )

  const loadDetail = useCallback(
    async (sourceId: string) => {
      const response = await authFetch(`${apiBase}/sources/${sourceId}`)
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.load")),
        )
      const next = (await response.json()) as KnowledgeSourceDetail
      setDetail(next)
      setTopicTitleDraft(next.title)
      setSelectedNode((current) => {
        if (
          current?.kind === "sourceFile" &&
          next.source_files.some((item) => item.id === current.id)
        )
          return current
        if (
          current?.kind === "document" &&
          next.documents.some((item) => item.id === current.id)
        )
          return current
        return null
      })
      return next
    },
    [t],
  )

  const loadBinding = useCallback(async () => {
    const response = await authFetch(`${apiBase}/parser-binding`)
    if (!response.ok)
      throw new Error(
        await responseError(response, t("knowledge.binding.loadFailed")),
      )
    const next = (await response.json()) as KnowledgeParserBinding
    setBinding(next)
    setProviderId(next.provider_id || "")
    setModelName(next.model_name || "")
  }, [t])

  const loadParseRun = useCallback(
    async (runId: string) => {
      const response = await authFetch(`${apiBase}/parse-runs/${runId}`)
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.parse")),
        )
      const next = (await response.json()) as KnowledgeParseRun
      setParseRun(next)
      return next
    },
    [t],
  )

  const loadLatestParseRun = useCallback(
    async (sourceId: string) => {
      const response = await authFetch(
        `${apiBase}/sources/${sourceId}/parse-runs/latest`,
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.parse")),
        )
      const next = (await response.json()) as KnowledgeParseRun | null
      setParseRun(next)
      return next
    },
    [t],
  )

  const loadParseHistory = useCallback(
    async (kind: "plan" | "run", jobId: string) => {
      const path =
        kind === "plan"
          ? `/parse-plans/${jobId}/events`
          : `/parse-runs/${jobId}/events`
      const response = await authFetch(`${apiBase}${path}`)
      if (!response.ok) return
      const history = (await response.json()) as Array<Record<string, unknown>>
      setParseEvents(history.reduce<ParseActivity[]>((current, event) => {
        const activity = parseActivityFromEvent(event)
        return activity ? appendParseActivity(current, activity) : current
      }, []))
    },
    [],
  )

  const loadParsePlan = useCallback(
    async (planId: string) => {
      const response = await authFetch(`${apiBase}/parse-plans/${planId}`)
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.plan")),
        )
      const next = (await response.json()) as KnowledgeParsePlan
      setParsePlan(next)
      setPlanInteractionMode(next.interaction_mode)
      if (next.payload)
        setSelectedStrategyId(
          (current) => current || next.payload?.recommended_strategy_id || "",
        )
      return next
    },
    [t],
  )

  const loadLatestParsePlan = useCallback(
    async (sourceId: string) => {
      const response = await authFetch(
        `${apiBase}/sources/${sourceId}/parse-plans/latest`,
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.plan")),
        )
      const next = (await response.json()) as KnowledgeParsePlan | null
      setParsePlan(next)
      if (next) setPlanInteractionMode(next.interaction_mode)
      setSelectedStrategyId(next?.payload?.recommended_strategy_id || "")
      return next
    },
    [t],
  )

  const loadContent = useCallback(
    async (node: SelectedNode) => {
      const path =
        node.kind === "sourceFile"
          ? `/source-files/${node.id}/content`
          : `/documents/${node.id}/content`
      const response = await authFetch(`${apiBase}${path}`)
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.loadContent")),
        )
      const next = (await response.json()) as KnowledgeContent
      setContent(next)
      setDraft(next.content)
    },
    [t],
  )

  useEffect(() => {
    Promise.all([loadSources(), loadBinding()])
      .catch((error) =>
        toast.error(
          error instanceof Error ? error.message : t("knowledge.errors.load"),
        ),
      )
      .finally(() => setLoading(false))
  }, [loadBinding, loadSources, t])

  useEffect(() => {
    if (!selectedSourceId) {
      setDetail(null)
      setSelectedNode(null)
      return
    }
    setParseRun(null)
    setParsePlan(null)
    setParseEvents([])
    setParseBackground("")
    setParseMode("planned")
    setPlanInteractionMode("quick")
    setPlanAnswers({})
    setSelectedStrategyId("")
    setPlanAdjustment("")
    setParsePreparing(false)
    setParseStarting(false)
    setPlanStarting(false)
    void (async () => {
      try {
        const [, latestPlan, latestRun] = await Promise.all([
          loadDetail(selectedSourceId),
          loadLatestParsePlan(selectedSourceId),
          loadLatestParseRun(selectedSourceId),
        ])
        if (isActiveParseStatus(latestPlan?.status))
          await loadParseHistory("plan", latestPlan.id)
        else if (isActiveParseStatus(latestRun?.status))
          await loadParseHistory("run", latestRun.id)
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t("knowledge.errors.load"),
        )
      }
    })()
  }, [
    loadDetail,
    loadLatestParsePlan,
    loadLatestParseRun,
    loadParseHistory,
    selectedSourceId,
    t,
  ])

  const selectedSourceFile: KnowledgeSourceFile | undefined =
    selectedNode?.kind === "sourceFile"
      ? detail?.source_files.find((item) => item.id === selectedNode.id)
      : undefined
  const selectedDocument: KnowledgeDocument | undefined =
    selectedNode?.kind === "document"
      ? detail?.documents.find((item) => item.id === selectedNode.id)
      : undefined

  useEffect(() => {
    if (!selectedNode) {
      setContent(null)
      return
    }
    setTitleDraft(
      selectedNode.kind === "sourceFile"
        ? selectedSourceFile?.original_filename || ""
        : selectedDocument?.title || "",
    )
    void loadContent(selectedNode).catch((error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("knowledge.errors.loadContent"),
      ),
    )
  }, [
    loadContent,
    selectedDocument?.title,
    selectedNode,
    selectedSourceFile?.original_filename,
    t,
  ])

  useEffect(
    () => () => {
      streamAbortRef.current?.abort()
      toast.dismiss(progressStreamToastId)
    },
    [],
  )

  useEffect(() => {
    try {
      window.localStorage.setItem(
        knowledgeLibraryCollapsedKey,
        String(libraryCollapsed),
      )
    } catch {
      // Storage can be disabled by the browser; keep the in-memory preference.
    }
  }, [libraryCollapsed])

  useEffect(() => {
    const activity = activityFromJob(parsePlan)
    if (activity)
      setParseEvents((current) => appendParseActivity(current, activity))
  }, [parsePlan?.message, parsePlan?.progress, parsePlan?.stage, parsePlan?.status])

  useEffect(() => {
    const activity = activityFromJob(parseRun)
    if (activity)
      setParseEvents((current) => appendParseActivity(current, activity))
  }, [parseRun?.message, parseRun?.progress, parseRun?.stage, parseRun?.status])

  useEffect(() => {
    if (
      !parsePlan ||
      (parsePlan.status !== "pending" && parsePlan.status !== "running")
    )
      return
    const timer = window.setInterval(() => {
      void loadParsePlan(parsePlan.id).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [loadParsePlan, parsePlan])

  useEffect(() => {
    if (!parseRun || !isActiveParseStatus(parseRun.status)) return
    const timer = window.setInterval(() => {
      void loadParseRun(parseRun.id)
        .then((next) => {
          if (!isActiveParseStatus(next.status) && detail)
            return Promise.all([loadSources(detail.id), loadDetail(detail.id)])
          return undefined
        })
        .catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [detail, loadDetail, loadParseRun, loadSources, parseRun])

  const selectedTitle =
    selectedNode?.kind === "sourceFile"
      ? selectedSourceFile?.original_filename
      : selectedDocument?.title
  const dirty = Boolean(
    content &&
      (draft !== content.content || titleDraft !== (selectedTitle || "")),
  )
  const topicDirty = Boolean(detail && topicTitleDraft !== detail.title)
  const bindingDirty = Boolean(
    !binding ||
      providerId !== (binding.provider_id || "") ||
      modelName !== (binding.model_name || ""),
  )
  const activeParseStatus = parseRun?.status ?? detail?.parse_status
  const parseRunBusy = parseStarting || isActiveParseStatus(activeParseStatus)
  const planBusy =
    planStarting || planRetrying || isActiveParseStatus(parsePlan?.status)
  const parseBusy = parseRunBusy || planBusy
  const activeJob = planBusy ? parsePlan : parseRun
  const activeJobUpdatedAt = activeJob?.updated_time
    ? new Date(activeJob.updated_time).getTime()
    : progressClock
  const activeJobCreatedAt = activeJob?.created_time
    ? new Date(activeJob.created_time).getTime()
    : progressClock
  const progressQuietFor = Math.max(0, progressClock - activeJobUpdatedAt)
  const progressElapsedFor = Math.max(0, progressClock - activeJobCreatedAt)
  const progressQuietTooLong =
    parseBusy && !parsePlan?.pending_question && progressQuietFor >= 60_000

  useEffect(() => {
    if (!parseBusy) return
    setProgressClock(Date.now())
    const timer = window.setInterval(() => setProgressClock(Date.now()), 10_000)
    return () => window.clearInterval(timer)
  }, [parseBusy])

  const handleSelectDocument = (documentId: string) => {
    setContent(null)
    setDraft("")
    setSelectedNode({ kind: "document", id: documentId })
    setViewMode("split")
  }

  const validateFiles = (files: File[], existing?: KnowledgeSourceDetail) => {
    if (files.length === 0) return false
    if (files.some((file) => !/\.(md|markdown)$/i.test(file.name))) {
      toast.error(t("knowledge.errors.fileType"))
      return false
    }
    if (files.some((file) => file.size > maxFileBytes)) {
      toast.error(t("knowledge.errors.fileSize"))
      return false
    }
    const totalCount = files.length + (existing?.source_file_count || 0)
    if (totalCount > maxTopicFiles) {
      toast.error(t("knowledge.errors.fileCount"))
      return false
    }
    const totalBytes =
      files.reduce((sum, file) => sum + file.size, 0) +
      (existing?.source_size || 0)
    if (totalBytes > maxTopicBytes) {
      toast.error(t("knowledge.errors.topicSize"))
      return false
    }
    return true
  }

  const handleCreateTopic = async () => {
    if (!uploadTitle.trim()) return
    setUploading(true)
    try {
      const response = await authFetch(`${apiBase}/sources`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: uploadTitle.trim() }),
      })
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.upload")),
        )
      const source = (await response.json()) as KnowledgeSource
      setUploadDialogOpen(false)
      setUploadTitle("")
      await loadSources(source.id)
      toast.success(t("knowledge.messages.topicCreated"))
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.upload"),
      )
    } finally {
      setUploading(false)
    }
  }

  const handleAddFiles = async (files: File[]) => {
    if (!binding?.configured || !detail || !validateFiles(files, detail)) return
    const data = new FormData()
    for (const file of files) data.append("files", file)
    setUploading(true)
    try {
      const response = await authFetch(
        `${apiBase}/sources/${detail.id}/files`,
        {
          method: "POST",
          body: data,
        },
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.upload")),
        )
      await loadSources(detail.id)
      await loadDetail(detail.id)
      await loadLatestParsePlan(detail.id)
      toast.success(t("knowledge.messages.filesAdded", { count: files.length }))
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.upload"),
      )
    } finally {
      setUploading(false)
      if (addInputRef.current) addInputRef.current.value = ""
    }
  }

  const handleSave = async () => {
    if (!selectedNode || !draft.trim() || !titleDraft.trim()) return
    setSaving(true)
    try {
      const isSourceFile = selectedNode.kind === "sourceFile"
      const path = isSourceFile
        ? `/source-files/${selectedNode.id}`
        : `/documents/${selectedNode.id}`
      const body = isSourceFile
        ? { original_filename: titleDraft.trim(), content: draft }
        : { title: titleDraft.trim(), content: draft }
      const response = await authFetch(`${apiBase}${path}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.save")),
        )
      if (selectedSourceId) {
        await loadSources(selectedSourceId)
        await loadDetail(selectedSourceId)
        if (isSourceFile) await loadLatestParsePlan(selectedSourceId)
      }
      await loadContent(selectedNode)
      toast.success(
        isSourceFile
          ? t("knowledge.messages.sourceSaved")
          : t("knowledge.messages.documentSaved"),
      )
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.save"),
      )
    } finally {
      setSaving(false)
    }
  }

  const handleSaveTopic = async () => {
    if (!detail || !topicTitleDraft.trim() || !topicDirty) return
    setSaving(true)
    try {
      const response = await authFetch(`${apiBase}/sources/${detail.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: topicTitleDraft.trim() }),
      })
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.save")),
        )
      await loadSources(detail.id)
      await loadDetail(detail.id)
      toast.success(t("knowledge.messages.topicSaved"))
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.save"),
      )
    } finally {
      setSaving(false)
    }
  }

  const handleSaveBinding = async () => {
    if (!providerId || !modelName) {
      toast.error(t("knowledge.binding.required"))
      return
    }
    setBindingSaving(true)
    try {
      const response = await authFetch(`${apiBase}/parser-binding`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider_id: providerId,
          model_name: modelName,
        }),
      })
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.binding.saveFailed")),
        )
      const next = (await response.json()) as KnowledgeParserBinding
      setBinding(next)
      setProviderId(next.provider_id || "")
      setModelName(next.model_name || "")
      setBindingDialogOpen(false)
      toast.success(t("knowledge.binding.saved"))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t("knowledge.binding.saveFailed"),
      )
    } finally {
      setBindingSaving(false)
    }
  }

  const handleGeneratePlan = async () => {
    if (!detail || parseBusy || !binding?.configured || bindingDirty) return
    setPlanStarting(true)
    let plan: KnowledgeParsePlan
    try {
      const response = await authFetch(
        `${apiBase}/sources/${detail.id}/parse-plans`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            background: parseBackground.trim() || null,
            interaction_mode: planInteractionMode,
          }),
        },
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.plan")),
        )
      plan = (await response.json()) as KnowledgeParsePlan
    } catch (error) {
      setPlanStarting(false)
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.plan"),
      )
      return
    }
    setParsePlan(plan)
    setPlanAnswers({})
    setSelectedStrategyId("")
    setPlanStarting(false)
    setParseEvents([
      {
        type: "progress",
        progress: plan.progress,
        stage: plan.stage,
        message: plan.message || t("knowledge.progressStages.queued"),
      },
    ])
    const controller = new AbortController()
    streamAbortRef.current?.abort()
    streamAbortRef.current = controller
    try {
      await followProgressStream(
        `${apiBase}/parse-plans/${plan.id}/stream`,
        controller,
        (event) => {
          const type = String(event.type || "progress")
          const progress = Number(event.progress || 0)
          const stage = String(event.stage || "planning")
          const message = String(event.message || "")
          const activity = parseActivityFromEvent(event)
          if (activity)
            setParseEvents((current) =>
              appendParseActivity(current, activity),
            )
          setParsePlan((current) =>
            current
              ? {
                  ...current,
                  status:
                    type === "done"
                      ? "ready"
                      : type === "error"
                        ? "failed"
                        : type === "cancelled"
                          ? "cancelled"
                        : type === "stale"
                          ? "stale"
                          : "running",
                  progress: progress || current.progress,
                  stage: stage || current.stage,
                  message: message || current.message,
                  error_code: event.error_code
                    ? String(event.error_code)
                    : current.error_code,
                  pending_question:
                    type === "question" && event.question_id && event.questions
                      ? {
                          question_id: String(event.question_id),
                          questions: event.questions as NonNullable<
                            KnowledgeParsePlan["pending_question"]
                          >["questions"],
                        }
                      : type === "resume"
                        ? null
                        : current.pending_question,
                }
              : current,
          )
        },
        t("knowledge.errors.streamUnsupported"),
        showProgressStreamInterrupted,
        dismissProgressStreamInterrupted,
      )
      dismissProgressStreamInterrupted()
      const latestPlan = await loadParsePlan(plan.id)
      if (latestPlan.status === "ready") {
        setSelectedStrategyId(latestPlan.payload?.recommended_strategy_id || "")
        toast.success(t("knowledge.messages.planReady"))
      } else if (latestPlan.status === "stale") {
        toast.warning(t("knowledge.plan.stale"))
      } else if (latestPlan.status === "failed") {
        toast.error(
          latestPlan.error_code
            ? t(`knowledge.parserErrors.${latestPlan.error_code}`)
            : t("knowledge.errors.plan"),
        )
      }
    } catch (error) {
      let reconciledPlan: KnowledgeParsePlan | null = null
      try {
        reconciledPlan = await loadParsePlan(plan.id)
      } catch {
        // Keep the original stream/request error when reconciliation also fails.
      }
      void loadParseHistory("plan", plan.id).catch(() => undefined)
      if (!(error instanceof DOMException && error.name === "AbortError"))
        if (isActiveParseStatus(reconciledPlan?.status))
          showProgressStreamInterrupted()
        else
          toast.error(
            reconciledPlan?.error_code
              ? t(`knowledge.parserErrors.${reconciledPlan.error_code}`)
              : t("knowledge.errors.plan"),
          )
    }
  }

  const handleRetryPlan = async () => {
    if (!parsePlan?.retryable || parsePlan.retry_action !== "persist") return
    setPlanRetrying(true)
    try {
      const response = await authFetch(
        `${apiBase}/parse-plans/${parsePlan.id}/retry`,
        { method: "POST" },
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.planRetry")),
        )
      const next = (await response.json()) as KnowledgeParsePlan
      setParsePlan(next)
      setParseEvents((current) => {
        const activity = activityFromJob(next)
        return activity ? appendParseActivity(current, activity) : current
      })
      if (next.status === "ready") {
        setSelectedStrategyId(next.payload?.recommended_strategy_id || "")
        toast.success(t("knowledge.messages.planReady"))
      } else if (next.status === "stale") {
        toast.warning(t("knowledge.plan.stale"))
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.planRetry"),
      )
      void loadParsePlan(parsePlan.id).catch(() => undefined)
    } finally {
      setPlanRetrying(false)
    }
  }

  const handleAnswerPlanQuestion = async () => {
    const pending = parsePlan?.pending_question
    if (!parsePlan || !pending || planAnswering) return
    const answers = Object.fromEntries(
      pending.questions.map((question) => [
        question.question,
        (planAnswers[question.question] || "").trim(),
      ]),
    )
    if (Object.values(answers).some((answer) => !answer)) {
      toast.error(t("knowledge.plan.answerRequired"))
      return
    }
    setPlanAnswering(true)
    try {
      const response = await authFetch(
        `${apiBase}/parse-plans/${parsePlan.id}/answer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question_id: pending.question_id,
            answers,
          }),
        },
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.planAnswer")),
        )
      const next = (await response.json()) as KnowledgeParsePlan
      setParsePlan(next)
      setPlanAnswers({})
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.planAnswer"),
      )
      void loadParsePlan(parsePlan.id).catch(() => undefined)
    } finally {
      setPlanAnswering(false)
    }
  }

  const selectPlanAnswer = (
    question: string,
    label: string,
    multiSelect: boolean,
  ) => {
    setPlanAnswers((current) => {
      if (!multiSelect) return { ...current, [question]: label }
      const selected = new Set(
        (current[question] || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      )
      if (selected.has(label)) selected.delete(label)
      else selected.add(label)
      return { ...current, [question]: [...selected].join(", ") }
    })
  }

  const handleParse = async () => {
    if (!detail || parseBusy || !binding?.configured || bindingDirty) return
    if (
      parseMode === "planned" &&
      (!parsePlan || parsePlan.status !== "ready" || !selectedStrategyId)
    )
      return
    setParseStarting(true)
    let run: KnowledgeParseRun
    try {
      const response = await authFetch(
        `${apiBase}/sources/${detail.id}/parse`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            parseMode === "planned" && parsePlan
              ? {
                  mode: "planned",
                  plan_id: parsePlan.id,
                  strategy_id: selectedStrategyId,
                  adjustment: planAdjustment.trim() || null,
                }
              : {
                  mode: "direct",
                  background: parseBackground.trim() || null,
                },
          ),
        },
      )
      if (!response.ok)
        throw new Error(
          await responseError(response, t("knowledge.errors.parse")),
        )
      run = (await response.json()) as KnowledgeParseRun
    } catch (error) {
      setParseStarting(false)
      toast.error(
        error instanceof Error ? error.message : t("knowledge.errors.parse"),
      )
      return
    }
    setParseRun(run)
    setParsePreparing(false)
    setParseStarting(false)
    setParseEvents([
      {
        type: "progress",
        progress: run.progress,
        stage: run.stage,
        message: run.message || t("knowledge.progressStages.queued"),
      },
    ])
    setSources((current) =>
      current.map((item) =>
        item.id === detail.id ? { ...item, parse_status: "pending" } : item,
      ),
    )
    const controller = new AbortController()
    streamAbortRef.current?.abort()
    streamAbortRef.current = controller
    try {
      await followProgressStream(
        `${apiBase}/parse-runs/${run.id}/stream`,
        controller,
        (event) => {
          const type = String(event.type || "progress")
          const progress = Number(event.progress || 0)
          const stage = String(event.stage || "analyzing")
          const message = String(event.message || "")
          const activity = parseActivityFromEvent(event)
          if (activity)
            setParseEvents((current) =>
              appendParseActivity(current, activity),
            )
          setParseRun((current) =>
            current
              ? {
                  ...current,
                  status:
                    type === "done"
                      ? "ready"
                      : type === "error"
                        ? "failed"
                        : type === "cancelled"
                          ? "cancelled"
                        : type === "stale"
                          ? "stale"
                          : "running",
                  progress: progress || current.progress,
                  stage: stage || current.stage,
                  message: message || current.message,
                  error_code: event.error_code
                    ? String(event.error_code)
                    : current.error_code,
                }
              : current,
          )
        },
        t("knowledge.errors.streamUnsupported"),
        showProgressStreamInterrupted,
        dismissProgressStreamInterrupted,
      )
      dismissProgressStreamInterrupted()
      const latestRun = await loadParseRun(run.id)
      await loadSources(detail.id)
      await loadDetail(detail.id)
      if (latestRun.status === "failed")
        toast.error(
          latestRun.error_code
            ? t(`knowledge.parserErrors.${latestRun.error_code}`)
            : t("knowledge.errors.parse"),
        )
      else if (latestRun.status === "stale")
        toast.warning(t("knowledge.staleHint"))
      else if (latestRun.status === "ready") {
        setParseBackground("")
        setPlanAdjustment("")
        toast.success(t("knowledge.messages.parsed"))
      }
    } catch (error) {
      let reconciledRun: KnowledgeParseRun | null = null
      try {
        reconciledRun = await loadParseRun(run.id)
        await loadSources(detail.id)
        await loadDetail(detail.id)
      } catch {
        // Keep the original stream/request error when reconciliation also fails.
      }
      void loadParseHistory("run", run.id).catch(() => undefined)
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        if (isActiveParseStatus(reconciledRun?.status))
          showProgressStreamInterrupted()
        else
          toast.error(
            reconciledRun?.status === "failed" && reconciledRun.error_code
              ? t(`knowledge.parserErrors.${reconciledRun.error_code}`)
              : t("knowledge.errors.parse"),
          )
      }
    }
  }

  const handleStopParse = async () => {
    if (parseStopping) return
    const stoppingPlan = parsePlan && isActiveParseStatus(parsePlan.status)
    const stoppingRun = parseRun && isActiveParseStatus(parseRun.status)
    if (!stoppingPlan && !stoppingRun) return

    setParseStopping(true)
    try {
      const response = stoppingPlan
        ? await authFetch(`${apiBase}/parse-plans/${parsePlan.id}/cancel`, {
            method: "POST",
          })
        : await authFetch(`${apiBase}/parse-runs/${parseRun!.id}/cancel`, {
            method: "POST",
          })
      if (!response.ok)
        throw new Error(
          await responseError(
            response,
            t("knowledge.parseWorkspace.stopFailed"),
          ),
        )

      streamAbortRef.current?.abort()
      streamAbortRef.current = null
      setPlanStarting(false)
      setParseStarting(false)
      const stoppedActivity: ParseActivity = {
        type: "cancelled",
        progress: (stoppingPlan ? parsePlan.progress : parseRun!.progress) || 0,
        stage: "cancelled",
        message: t("knowledge.parseWorkspace.stopped"),
      }
      setParseEvents((current) => appendParseActivity(current, stoppedActivity))
      if (stoppingPlan)
        setParsePlan((current) =>
          current
            ? {
                ...current,
                status: "cancelled",
                stage: "cancelled",
                message: stoppedActivity.message,
              }
            : current,
        )
      else
        setParseRun((current) =>
          current
            ? {
                ...current,
                status: "cancelled",
                stage: "cancelled",
                message: stoppedActivity.message,
              }
            : current,
        )
      if (detail) {
        await loadSources(detail.id)
        await loadDetail(detail.id)
      }
      toast.success(t("knowledge.parseWorkspace.stopped"))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t("knowledge.parseWorkspace.stopFailed"),
      )
    } finally {
      setParseStopping(false)
    }
  }

  const handleDeleteTopic = async () => {
    if (!detail || parseBusy) return
    if (!window.confirm(t("knowledge.deleteConfirm", { title: detail.title })))
      return
    const response = await authFetch(`${apiBase}/sources/${detail.id}`, {
      method: "DELETE",
    })
    if (!response.ok) {
      toast.error(await responseError(response, t("knowledge.errors.delete")))
      return
    }
    setSelectedSourceId(null)
    await loadSources()
    toast.success(t("knowledge.messages.deleted"))
  }

  const handleDeleteSourceFile = async (sourceFile: KnowledgeSourceFile) => {
    if (!detail || parseBusy) return
    if (
      !window.confirm(
        t("knowledge.deleteFileConfirm", {
          name: sourceFile.original_filename,
        }),
      )
    )
      return
    const response = await authFetch(
      `${apiBase}/source-files/${sourceFile.id}`,
      {
        method: "DELETE",
      },
    )
    if (!response.ok) {
      toast.error(await responseError(response, t("knowledge.errors.delete")))
      return
    }
    await loadSources(detail.id)
    await loadDetail(detail.id)
    await loadLatestParsePlan(detail.id)
    toast.success(t("knowledge.messages.fileDeleted"))
  }

  return (
    <>
      <div className="flex h-full min-h-0 w-full flex-col gap-2 overflow-hidden">
        <div className="flex h-10 shrink-0 items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <BookOpenText className="hidden size-4 shrink-0 text-primary sm:block" />
            <h1 className="shrink-0 text-base font-semibold tracking-tight">
              {t("knowledge.title")}
            </h1>
            <span className="hidden truncate border-l pl-3 text-xs text-muted-foreground xl:block">
              {t("knowledge.description")}
            </span>
            {libraryCollapsed && (
              <Select
                value={selectedSourceId || undefined}
                onValueChange={setSelectedSourceId}
              >
                <SelectTrigger
                  className="ml-1 h-9 w-[min(42vw,320px)] bg-background"
                  aria-label={t("knowledge.workspace.topicSwitcher")}
                >
                  <SelectValue
                    placeholder={t("knowledge.workspace.topicSwitcher")}
                  />
                </SelectTrigger>
                <SelectContent>
                  {sources.map((source) => (
                    <SelectItem key={source.id} value={source.id}>
                      {source.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setUploadDialogOpen(true)}
              disabled={uploading}
              aria-label={t("knowledge.createTopic")}
            >
              {uploading ? (
                <Loader2 className="mr-1 size-4 animate-spin" />
              ) : (
                <Plus className="mr-1 size-4" />
              )}
              <span className="hidden sm:inline">
                {t("knowledge.createTopic")}
              </span>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setBindingDialogOpen(true)}
              aria-label={t("knowledge.binding.title")}
            >
              <Bot className="mr-1 size-4" />
              <span className="hidden sm:inline">
                {t("knowledge.binding.title")}
              </span>
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center rounded-xl border bg-background">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 overflow-hidden rounded-xl border bg-background shadow-sm">
            <aside
              className="min-h-0 shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out"
              style={{ width: libraryCollapsed ? 0 : 260 }}
              {...(libraryCollapsed ? { inert: true } : {})}
            >
              <div className="flex h-full w-[260px] min-h-0 flex-col bg-muted/20">
                <div className="flex items-center justify-between border-b px-3 py-3">
                  <div>
                    <p className="text-sm font-semibold">
                      {t("knowledge.library")}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t("knowledge.topicCount", { count: sources.length })}
                    </p>
                  </div>
                </div>
                <ScrollArea className="min-h-0 flex-1">
                  <div className="space-y-1 p-2">
                    {sources.length === 0 ? (
                      <button
                        type="button"
                        className="flex w-full flex-col items-center rounded-lg border border-dashed px-4 py-10 text-center text-muted-foreground hover:border-primary/40 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                        onClick={() => setUploadDialogOpen(true)}
                        disabled={uploading}
                      >
                        <FileUp className="mb-3 size-8" />
                        <span className="text-sm font-medium">
                          {t("knowledge.emptyTitle")}
                        </span>
                        <span className="mt-1 text-xs">
                          {t("knowledge.emptyDescription")}
                        </span>
                      </button>
                    ) : (
                      sources.map((source) => (
                        <button
                          key={source.id}
                          type="button"
                          className={cn(
                            "w-full rounded-lg px-3 py-2.5 text-left transition",
                            selectedSourceId === source.id
                              ? "bg-primary/10 text-primary"
                              : "hover:bg-muted",
                          )}
                          onClick={() => setSelectedSourceId(source.id)}
                        >
                          <div className="flex items-start gap-2">
                            <BookOpenText className="mt-0.5 size-4 shrink-0" />
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium">
                                {source.title}
                              </p>
                              <div className="mt-1 flex items-center gap-1.5">
                                <Badge
                                  variant="outline"
                                  className={cn(
                                    "h-5 px-1.5 text-[10px]",
                                    statusClass(source.parse_status),
                                  )}
                                >
                                  {t(`knowledge.status.${source.parse_status}`)}
                                </Badge>
                                <span className="text-[10px] text-muted-foreground">
                                  {t("knowledge.fileCount", {
                                    count: source.source_file_count,
                                  })}
                                </span>
                              </div>
                            </div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </ScrollArea>
              </div>
            </aside>

            <button
              type="button"
              onClick={() => setLibraryCollapsed((current) => !current)}
              className="group relative z-10 flex w-6 shrink-0 items-center justify-center border-x border-border/60 bg-muted/20 text-muted-foreground transition-colors hover:border-primary/30 hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              aria-label={t(
                libraryCollapsed
                  ? "knowledge.workspace.expandLibrary"
                  : "knowledge.workspace.collapseLibrary",
              )}
              title={t(
                libraryCollapsed
                  ? "knowledge.workspace.expandLibrary"
                  : "knowledge.workspace.collapseLibrary",
              )}
            >
              <span className="grid size-5 place-items-center rounded-full border border-border/60 bg-background shadow-sm transition-colors group-hover:border-primary/40 group-hover:bg-primary/10">
                {libraryCollapsed ? (
                  <ChevronRight className="size-3.5" />
                ) : (
                  <ChevronLeft className="size-3.5" />
                )}
              </span>
            </button>

            <main className="flex min-h-0 min-w-0 flex-1 flex-col">
              {!detail ? (
                <div className="flex flex-1 flex-col items-center justify-center text-muted-foreground">
                  <FileText className="mb-3 size-10" />
                  <p>{t("knowledge.selectSource")}</p>
                </div>
              ) : parseBusy ? (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-muted/10">
                  <div className="shrink-0 border-b bg-background px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <Sparkles className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <h2 className="truncate text-sm font-semibold">
                            {t(
                              planBusy
                                ? "knowledge.planWorkspace.title"
                                : "knowledge.parseWorkspace.title",
                            )}
                          </h2>
                          <Badge variant="outline" className="hidden sm:flex">
                            {activeJob
                              ? t(
                                  `knowledge.progressStages.${activeJob.stage}`,
                                  { defaultValue: activeJob.message },
                                )
                              : t("knowledge.progressStages.queued")}
                          </Badge>
                        </div>
                        <p className="truncate text-xs text-muted-foreground">
                          {t("knowledge.parseWorkspace.compactSummary", {
                            topic: detail.title,
                            model:
                              binding?.model_name || activeJob?.parser_model,
                            count: detail.source_file_count,
                          })}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <p className="text-xl font-semibold tabular-nums text-primary">
                          {activeJob?.progress || 0}%
                        </p>
                        <Button
                          size="sm"
                          variant="outline"
                          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          disabled={
                            parseStopping ||
                            (!isActiveParseStatus(parsePlan?.status) &&
                              !isActiveParseStatus(parseRun?.status))
                          }
                          onClick={() => void handleStopParse()}
                        >
                          {parseStopping ? (
                            <Loader2 className="mr-1 size-3.5 animate-spin" />
                          ) : (
                            <Square className="mr-1 size-3.5 fill-current" />
                          )}
                          {t(
                            parseStopping
                              ? "knowledge.parseWorkspace.stopping"
                              : "knowledge.parseWorkspace.stop",
                          )}
                        </Button>
                      </div>
                    </div>
                    <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out"
                        style={{ width: `${activeJob?.progress || 0}%` }}
                      />
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                      <span>
                        {t("knowledge.parseWorkspace.elapsed", {
                          minutes: shortMinutes(progressElapsedFor),
                        })}
                      </span>
                      <span aria-hidden="true">·</span>
                      <span>
                        {t("knowledge.parseWorkspace.lastUpdate", {
                          minutes: shortMinutes(progressQuietFor),
                        })}
                      </span>
                      {progressQuietTooLong && (
                        <span className="flex basis-full items-start gap-1 text-amber-700 dark:text-amber-300">
                          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                          {t("knowledge.parseWorkspace.quietHint")}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="min-h-0 flex-1 overflow-y-auto">
                    {parsePlan?.pending_question && (
                      <section className="m-4 overflow-hidden rounded-xl border border-primary/25 bg-background shadow-sm">
                        <div className="border-b bg-primary/[0.035] px-5 py-4">
                          <div className="flex items-start gap-3">
                            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                              <Sparkles className="size-4" />
                            </span>
                            <div className="min-w-0">
                              <h3 className="text-sm font-semibold">
                                {t("knowledge.plan.questionTitle")}
                              </h3>
                              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                {t("knowledge.plan.questionDescription")}
                              </p>
                            </div>
                          </div>
                        </div>
                        <div className="space-y-5 p-5">
                          {parsePlan.pending_question.questions.map(
                            (question) => (
                              <div key={question.question} className="space-y-3">
                                <div>
                                  <Badge variant="outline">
                                    {question.header}
                                  </Badge>
                                  <p className="mt-2 text-sm font-medium leading-6">
                                    {question.question}
                                  </p>
                                </div>
                                <div className="grid gap-2 sm:grid-cols-2">
                                  {question.options.map((option) => {
                                    const selected = (
                                      planAnswers[question.question] || ""
                                    )
                                      .split(",")
                                      .map((item) => item.trim())
                                      .includes(option.label)
                                    return (
                                      <Button
                                        key={option.label}
                                        type="button"
                                        variant={selected ? "secondary" : "outline"}
                                        className="h-auto min-h-14 justify-start whitespace-normal px-3 py-2 text-left"
                                        onClick={() =>
                                          selectPlanAnswer(
                                            question.question,
                                            option.label,
                                            question.multiSelect,
                                          )
                                        }
                                      >
                                        <span>
                                          <span className="block text-sm font-medium">
                                            {option.label}
                                          </span>
                                          <span className="mt-0.5 block text-xs font-normal leading-5 text-muted-foreground">
                                            {option.description}
                                          </span>
                                        </span>
                                      </Button>
                                    )
                                  })}
                                </div>
                                <Input
                                  value={planAnswers[question.question] || ""}
                                  maxLength={1000}
                                  placeholder={t("knowledge.plan.customAnswer")}
                                  onChange={(event) =>
                                    setPlanAnswers((current) => ({
                                      ...current,
                                      [question.question]: event.target.value,
                                    }))
                                  }
                                />
                              </div>
                            ),
                          )}
                          <div className="flex justify-end border-t pt-4">
                            <Button
                              size="sm"
                              disabled={
                                planAnswering ||
                                parsePlan.pending_question.questions.some(
                                  (question) =>
                                    !planAnswers[question.question]?.trim(),
                                )
                              }
                              onClick={() => void handleAnswerPlanQuestion()}
                            >
                              {planAnswering && (
                                <Loader2 className="mr-1 size-4 animate-spin" />
                              )}
                              {t("knowledge.plan.submitAnswer")}
                            </Button>
                          </div>
                        </div>
                      </section>
                    )}
                    <section className="m-4 min-h-[calc(100%-2rem)] overflow-hidden rounded-xl border bg-background shadow-sm">
                      <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
                        <h3 className="text-sm font-semibold">
                          {t("knowledge.parseWorkspace.activity")}
                        </h3>
                        <Loader2 className="size-4 animate-spin text-primary" />
                      </div>
                      <div className="p-4">
                        {parseEvents.length === 0 ? (
                          <p className="px-2 py-8 text-center text-sm text-muted-foreground">
                            {t("knowledge.parseWorkspace.activityEmpty")}
                          </p>
                        ) : (
                          <ol className="relative space-y-1">
                            {parseActivityGroups.map((group, index) => {
                              const isLatest =
                                index === parseActivityGroups.length - 1
                              const failed =
                                group.type === "error" ||
                                group.stage === "failed"
                              const running = isLatest && parseBusy && !failed
                              const stageTitle = t(
                                `knowledge.progressStages.${group.stage}`,
                                { defaultValue: group.stage },
                              )
                              return (
                                <li
                                  key={`${group.stage}-${index}`}
                                  className="relative grid grid-cols-[32px_minmax(0,1fr)] gap-3 pb-3 last:pb-0"
                                  aria-current={running ? "step" : undefined}
                                >
                                  {index < parseActivityGroups.length - 1 && (
                                    <span className="absolute bottom-0 left-[15px] top-8 w-px bg-border" />
                                  )}
                                  <span
                                    className={cn(
                                      "relative z-10 flex size-8 items-center justify-center rounded-full border bg-background",
                                      failed
                                        ? "border-destructive/40 text-destructive"
                                        : running
                                          ? "border-primary/40 bg-primary/5 text-primary"
                                          : "border-emerald-500/30 text-emerald-600 dark:text-emerald-300",
                                    )}
                                  >
                                    {failed ? (
                                      <AlertTriangle className="size-4" />
                                    ) : running ? (
                                      <Loader2 className="size-4 animate-spin" />
                                    ) : (
                                      <Check className="size-4" />
                                    )}
                                  </span>
                                  <div
                                    className={cn(
                                      "min-w-0 rounded-lg border px-3.5 py-2.5",
                                      running
                                        ? "border-primary/20 bg-primary/[0.035]"
                                        : failed
                                          ? "border-destructive/20 bg-destructive/[0.035]"
                                          : "border-transparent bg-muted/25",
                                    )}
                                  >
                                    <div className="flex items-center justify-between gap-3">
                                      <p className="truncate text-sm font-medium">
                                        {stageTitle}
                                      </p>
                                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                                        {group.progress}%
                                      </span>
                                    </div>
                                    {group.summary?.message &&
                                      group.summary.message !== stageTitle && (
                                      <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">
                                        {group.summary.message}
                                      </p>
                                    )}
                                    {group.steps.length > 0 && (
                                      <ul className="mt-2 space-y-1.5 border-t pt-2">
                                        {group.steps.map((step) => {
                                          const stepFailed =
                                            step.step_status === "failed"
                                          const stepRunning =
                                            step.step_status === "running" ||
                                            step.step_status === "retrying"
                                          const stepTitle =
                                            step.step_kind === "tool" &&
                                            step.tool_name
                                              ? t(
                                                  `knowledge.parseWorkspace.tools.${step.tool_name}`,
                                                )
                                              : t(
                                                  `knowledge.parseWorkspace.steps.${step.step_kind}`,
                                                )
                                          return (
                                            <li
                                              key={step.step_id}
                                              className="flex items-start gap-2 rounded-md bg-background/70 px-2.5 py-2"
                                            >
                                              <span
                                                className={cn(
                                                  "mt-0.5 flex size-5 shrink-0 items-center justify-center",
                                                  stepFailed
                                                    ? "text-destructive"
                                                    : stepRunning
                                                      ? "text-primary"
                                                      : "text-emerald-600 dark:text-emerald-300",
                                                )}
                                              >
                                                {stepFailed ? (
                                                  <AlertTriangle className="size-3.5" />
                                                ) : stepRunning ? (
                                                  step.step_kind === "retry" ? (
                                                    <RefreshCw className="size-3.5 animate-spin" />
                                                  ) : (
                                                    <Loader2 className="size-3.5 animate-spin" />
                                                  )
                                                ) : step.step_kind === "model" ? (
                                                  <Bot className="size-3.5" />
                                                ) : step.step_kind === "tool" ? (
                                                  <FileText className="size-3.5" />
                                                ) : (
                                                  <Check className="size-3.5" />
                                                )}
                                              </span>
                                              <div className="min-w-0 flex-1">
                                                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                                                  <span className="text-xs font-medium">
                                                    {stepTitle}
                                                  </span>
                                                  <span className="text-[11px] text-muted-foreground">
                                                    {t(
                                                      `knowledge.parseWorkspace.stepStatus.${step.step_status}`,
                                                    )}
                                                    {step.elapsed_seconds !==
                                                      undefined &&
                                                      step.elapsed_seconds > 0 &&
                                                      ` · ${t("knowledge.parseWorkspace.stepElapsed", { seconds: step.elapsed_seconds })}`}
                                                  </span>
                                                </div>
                                                <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                                                  {step.message}
                                                  {step.step_kind === "retry" &&
                                                    step.attempt !== undefined &&
                                                    ` · ${t("knowledge.parseWorkspace.retryAttempt", { attempt: step.attempt, max: step.max_retries || "-" })}`}
                                                </p>
                                              </div>
                                            </li>
                                          )
                                        })}
                                      </ul>
                                    )}
                                  </div>
                                </li>
                              )
                            })}
                          </ol>
                        )}
                      </div>
                    </section>
                  </div>
                </div>
              ) : parsePreparing ? (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-muted/10">
                  <div className="shrink-0 border-b bg-background px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <Sparkles className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <h2 className="truncate text-sm font-semibold">
                          {t("knowledge.parseSetup.title")}
                        </h2>
                        <p className="truncate text-xs text-muted-foreground">
                          {t("knowledge.parseSetup.compactSummary", {
                            topic: detail.title,
                            model: binding?.model_name,
                            count: detail.source_file_count,
                          })}
                        </p>
                      </div>
                      <Badge variant="outline" className="hidden md:flex">
                        {binding?.provider_name}
                      </Badge>
                    </div>
                  </div>

                  <div className="min-h-0 flex-1 overflow-y-auto">
                    <div className="space-y-4 p-4">
                      <div className="flex flex-col gap-2 rounded-xl border bg-background p-3 sm:flex-row sm:items-center">
                        <div
                          role="radiogroup"
                          aria-label={t("knowledge.parseSetup.modeLabel")}
                          className="grid shrink-0 grid-cols-2 rounded-lg bg-muted p-1"
                        >
                          <button
                            type="button"
                            role="radio"
                            aria-checked={parseMode === "planned"}
                            onClick={() => setParseMode("planned")}
                            className={cn(
                              "flex h-9 items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium transition-all",
                              parseMode === "planned"
                                ? "border-primary/30 bg-background text-primary shadow-sm"
                                : "border-transparent text-muted-foreground hover:text-foreground",
                            )}
                          >
                            <Sparkles className="size-4" />
                            {t("knowledge.parseSetup.plannedMode")}
                          </button>
                          <button
                            type="button"
                            role="radio"
                            aria-checked={parseMode === "direct"}
                            onClick={() => setParseMode("direct")}
                            className={cn(
                              "flex h-9 items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium transition-all",
                              parseMode === "direct"
                                ? "border-amber-500/40 bg-background text-amber-700 shadow-sm dark:text-amber-300"
                                : "border-transparent text-muted-foreground hover:text-foreground",
                            )}
                          >
                            <Zap className="size-4" />
                            {t("knowledge.parseSetup.directMode")}
                          </button>
                        </div>
                        <p className="min-w-0 flex-1 text-xs leading-5 text-muted-foreground">
                          {t(
                            parseMode === "planned"
                              ? "knowledge.parseSetup.plannedDescription"
                              : "knowledge.parseSetup.directDescription",
                          )}{" "}
                          <span className="font-medium text-foreground">
                            {t(
                              parseMode === "planned"
                                ? "knowledge.parseSetup.plannedFlow"
                                : "knowledge.parseSetup.directFlow",
                            )}
                          </span>
                        </p>
                      </div>

                      {parseMode === "direct" ? (
                        <section className="flex min-h-[320px] flex-col overflow-hidden rounded-xl border bg-background">
                          <div className="shrink-0 border-b px-5 py-4">
                            <div className="flex items-center justify-between gap-3">
                              <div>
                                <Label
                                  htmlFor="knowledge-parse-background"
                                  className="text-sm font-semibold"
                                >
                                  {t("knowledge.background.label")}
                                </Label>
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {t(
                                    "knowledge.parseSetup.backgroundDescription",
                                  )}
                                </p>
                              </div>
                              <Badge variant="outline">
                                {t("knowledge.parseSetup.optional")}
                              </Badge>
                            </div>
                          </div>
                          <Textarea
                            id="knowledge-parse-background"
                            value={parseBackground}
                            onChange={(event) =>
                              setParseBackground(event.target.value)
                            }
                            maxLength={8000}
                            placeholder={t("knowledge.background.placeholder")}
                            className="min-h-56 flex-1 resize-none rounded-none border-0 p-5 text-sm leading-6 shadow-none focus-visible:ring-0"
                            autoFocus
                          />
                          <div className="flex shrink-0 items-start justify-between gap-4 border-t bg-muted/20 px-5 py-3">
                            <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
                              {t("knowledge.background.securityHint")}
                            </p>
                            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                              {parseBackground.length}/8000
                            </span>
                          </div>
                        </section>
                      ) : (
                        <section className="overflow-hidden rounded-xl border bg-background">
                          {!parsePlan?.payload && (
                            <div className="border-b bg-muted/10 px-5 py-4">
                              <Label className="text-sm font-semibold">
                                {t("knowledge.plan.interactionMode")}
                              </Label>
                              <div
                                role="radiogroup"
                                aria-label={t("knowledge.plan.interactionMode")}
                                className="mt-3 grid gap-2 sm:grid-cols-2"
                              >
                                {(
                                  ["quick", "collaborative"] as const
                                ).map((mode) => (
                                  <button
                                    key={mode}
                                    type="button"
                                    role="radio"
                                    aria-checked={planInteractionMode === mode}
                                    onClick={() => setPlanInteractionMode(mode)}
                                    className={cn(
                                      "rounded-lg border px-4 py-3 text-left transition-colors",
                                      planInteractionMode === mode
                                        ? "border-primary/40 bg-primary/5"
                                        : "hover:border-primary/25 hover:bg-muted/30",
                                    )}
                                  >
                                    <span className="block text-sm font-medium">
                                      {t(`knowledge.plan.${mode}Mode`)}
                                    </span>
                                    <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                                      {t(`knowledge.plan.${mode}Description`)}
                                    </span>
                                  </button>
                                ))}
                              </div>
                            </div>
                          )}
                          {parsePlan?.payload ? (
                            <>
                              <div className="shrink-0 border-b px-5 py-4">
                                <div className="flex items-start justify-between gap-4">
                                  <div className="min-w-0">
                                    <h3 className="text-sm font-semibold">
                                      {t("knowledge.plan.reviewTitle")}
                                    </h3>
                                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                                      {parsePlan.payload.topic_summary}
                                    </p>
                                  </div>
                                  <Badge
                                    variant={
                                      parsePlan.status === "ready"
                                        ? "secondary"
                                        : "outline"
                                    }
                                    className="shrink-0"
                                  >
                                    {t(`knowledge.status.${parsePlan.status}`)}
                                  </Badge>
                                </div>
                              </div>
                              <div>
                                <div className="space-y-4 p-5">
                                  <details className="group rounded-xl border bg-muted/15">
                                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
                                      <span>
                                        {t("knowledge.plan.showSourceDetails", {
                                          count:
                                            parsePlan.payload.source_overview
                                              .length,
                                        })}
                                      </span>
                                      <ChevronRight className="size-4 text-muted-foreground transition-transform group-open:rotate-90" />
                                    </summary>
                                    <div className="grid gap-2 border-t p-3 lg:grid-cols-2">
                                      {parsePlan.payload.source_overview.map(
                                        (source) => (
                                          <div
                                            key={source.filename}
                                            className="rounded-lg border bg-background p-3"
                                          >
                                            <p className="truncate text-sm font-medium">
                                              {source.filename}
                                            </p>
                                            <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                                              {source.summary}
                                            </p>
                                          </div>
                                        ),
                                      )}
                                    </div>
                                  </details>
                                  <div className="grid gap-3 md:grid-cols-2">
                                    {parsePlan.payload.strategies.map(
                                      (strategy) => (
                                        <button
                                          key={strategy.id}
                                          type="button"
                                          disabled={
                                            parsePlan.status !== "ready"
                                          }
                                          onClick={() =>
                                            setSelectedStrategyId(strategy.id)
                                          }
                                          className={cn(
                                            "w-full rounded-xl border p-4 text-left transition disabled:cursor-not-allowed",
                                            selectedStrategyId === strategy.id
                                              ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                                              : "hover:border-primary/40",
                                          )}
                                        >
                                          <div className="flex h-full items-start justify-between gap-4">
                                            <div className="min-w-0 flex-1">
                                              <div className="flex flex-wrap items-center gap-2">
                                                <h4 className="truncate font-semibold">
                                                  {strategy.name}
                                                </h4>
                                                {strategy.id ===
                                                  parsePlan.payload
                                                    ?.recommended_strategy_id && (
                                                  <Badge>
                                                    {t(
                                                      "knowledge.plan.recommended",
                                                    )}
                                                  </Badge>
                                                )}
                                                <Badge variant="outline">
                                                  {t(
                                                    `knowledge.plan.loss.${strategy.loss_level}`,
                                                  )}
                                                </Badge>
                                              </div>
                                              <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted-foreground">
                                                {strategy.description}
                                              </p>
                                            </div>
                                            <div className="shrink-0 rounded-lg bg-primary/10 px-3 py-2 text-center text-primary">
                                              <p className="text-xl font-semibold tabular-nums">
                                                {strategy.document_count}
                                              </p>
                                              <p className="text-[11px]">
                                                {t(
                                                  "knowledge.plan.expectedDocuments",
                                                )}
                                              </p>
                                            </div>
                                          </div>
                                        </button>
                                      ),
                                    )}
                                  </div>
                                  {selectedPlanStrategy && (
                                    <div className="space-y-3 rounded-xl border p-4">
                                      <div className="flex items-center justify-between gap-3">
                                        <div className="min-w-0">
                                          <p className="text-xs font-medium text-muted-foreground">
                                            {t(
                                              "knowledge.plan.selectedStructure",
                                            )}
                                          </p>
                                          <h4 className="mt-1 truncate font-semibold">
                                            {selectedPlanStrategy.name}
                                          </h4>
                                        </div>
                                        <Badge variant="secondary">
                                          {selectedPlanStrategy.document_count}{" "}
                                          {t(
                                            "knowledge.plan.expectedDocuments",
                                          )}
                                        </Badge>
                                      </div>
                                      <div className="space-y-2">
                                        {selectedPlanStrategy.documents.map(
                                          (document) => (
                                            <details
                                              key={`${selectedPlanStrategy.id}-${document.document_type}-${document.title}`}
                                              className="group rounded-lg border bg-muted/10"
                                            >
                                              <summary className="flex cursor-pointer list-none items-start gap-3 p-3 [&::-webkit-details-marker]:hidden">
                                                <Badge
                                                  variant="outline"
                                                  className="mt-0.5 shrink-0"
                                                >
                                                  {t(
                                                    `knowledge.plan.documentType.${document.document_type}`,
                                                  )}
                                                </Badge>
                                                <div className="min-w-0 flex-1">
                                                  <p className="truncate text-sm font-medium">
                                                    {document.title}
                                                  </p>
                                                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                                                    {document.purpose}
                                                  </p>
                                                  <p className="mt-1 text-[11px] text-muted-foreground">
                                                    {t(
                                                      "knowledge.plan.showDocumentDetails",
                                                    )}
                                                  </p>
                                                </div>
                                                <ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
                                              </summary>
                                              <div className="space-y-2 border-t px-3 py-3 text-xs leading-5">
                                                <p>
                                                  <span className="font-medium">
                                                    {t(
                                                      "knowledge.plan.coverage",
                                                    )}
                                                    :{" "}
                                                  </span>
                                                  {document.source_coverage.join(
                                                    ", ",
                                                  )}
                                                </p>
                                                {document.must_preserve.length >
                                                  0 && (
                                                  <p>
                                                    <span className="font-medium">
                                                      {t(
                                                        "knowledge.plan.mustPreserve",
                                                      )}
                                                      :{" "}
                                                    </span>
                                                    {document.must_preserve.join(
                                                      ", ",
                                                    )}
                                                  </p>
                                                )}
                                              </div>
                                            </details>
                                          ),
                                        )}
                                      </div>
                                      <details className="group border-t pt-3 text-xs text-muted-foreground">
                                        <summary className="flex cursor-pointer list-none items-center gap-2 font-medium [&::-webkit-details-marker]:hidden">
                                          <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
                                          {t("knowledge.plan.advancedDetails")}
                                        </summary>
                                        <p className="mt-2 leading-5">
                                          <span className="font-medium text-foreground">
                                            {t("knowledge.plan.deduplication")}:{" "}
                                          </span>
                                          {
                                            selectedPlanStrategy.deduplication_policy
                                          }
                                        </p>
                                      </details>
                                    </div>
                                  )}
                                  {parsePlan.status === "ready" && (
                                    <div className="space-y-2">
                                      <Label htmlFor="knowledge-plan-adjustment">
                                        {t("knowledge.plan.adjustment")}
                                      </Label>
                                      <Textarea
                                        id="knowledge-plan-adjustment"
                                        value={planAdjustment}
                                        onChange={(event) =>
                                          setPlanAdjustment(event.target.value)
                                        }
                                        maxLength={4000}
                                        placeholder={t(
                                          "knowledge.plan.adjustmentPlaceholder",
                                        )}
                                        className="min-h-24 resize-none"
                                      />
                                    </div>
                                  )}
                                </div>
                              </div>
                            </>
                          ) : (
                            <>
                              <div className="shrink-0 border-b px-5 py-4">
                                <h3 className="text-sm font-semibold">
                                  {t("knowledge.plan.prepareTitle")}
                                </h3>
                                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                  {t("knowledge.plan.prepareDescription")}
                                </p>
                              </div>
                              <Textarea
                                value={parseBackground}
                                onChange={(event) =>
                                  setParseBackground(event.target.value)
                                }
                                maxLength={8000}
                                placeholder={t(
                                  "knowledge.background.placeholder",
                                )}
                                className="min-h-56 resize-none rounded-none border-0 p-5 text-sm leading-6 shadow-none focus-visible:ring-0"
                              />
                              {parsePlan?.status === "failed" && (
                                <div className="shrink-0 space-y-1 border-t px-5 py-3">
                                  <p className="text-sm text-destructive">
                                    {parsePlan.error_code
                                      ? t(
                                          `knowledge.parserErrors.${parsePlan.error_code}`,
                                        )
                                      : t("knowledge.errors.plan")}
                                  </p>
                                  {parsePlan.retryable &&
                                    parsePlan.retry_action === "persist" && (
                                      <p className="text-xs leading-5 text-muted-foreground">
                                        {t("knowledge.plan.retryPersistHint")}
                                      </p>
                                    )}
                                </div>
                              )}
                            </>
                          )}
                        </section>
                      )}
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center justify-between gap-4 border-t bg-background px-4 py-3">
                    <p className="hidden min-w-0 flex-1 truncate text-xs text-muted-foreground lg:block">
                      {t(
                        parseMode === "planned"
                          ? "knowledge.plan.submitHint"
                          : "knowledge.parseSetup.submitHint",
                      )}
                    </p>
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => setParsePreparing(false)}
                      >
                        {t("knowledge.parseSetup.cancel")}
                      </Button>
                      {parseMode === "planned" &&
                      parsePlan?.status !== "ready" ? (
                        <Button
                          type="button"
                          size="sm"
                          disabled={planStarting || planRetrying}
                          onClick={() =>
                            void (parsePlan?.retryable &&
                            parsePlan.retry_action === "persist"
                              ? handleRetryPlan()
                              : handleGeneratePlan())
                          }
                        >
                          {planStarting || planRetrying ? (
                            <Loader2 className="mr-1 size-4 animate-spin" />
                          ) : (
                            <Sparkles className="mr-1 size-4" />
                          )}
                          {t(
                            parsePlan?.retryable &&
                              parsePlan.retry_action === "persist"
                              ? "knowledge.plan.retryPersist"
                              : "knowledge.plan.generate",
                          )}
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          disabled={parseStarting}
                          onClick={() => void handleParse()}
                        >
                          {parseStarting ? (
                            <Loader2 className="mr-1 size-4 animate-spin" />
                          ) : (
                            <Sparkles className="mr-1 size-4" />
                          )}
                          {t(
                            parseMode === "planned"
                              ? "knowledge.plan.execute"
                              : "knowledge.parseSetup.start",
                          )}
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              ) : !selectedNode ? (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-muted/10">
                  <div className="shrink-0 border-b bg-background px-4 py-2.5">
                    <div className="flex items-start justify-between gap-5">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <Input
                            id="knowledge-topic-title"
                            value={topicTitleDraft}
                            onChange={(event) =>
                              setTopicTitleDraft(event.target.value)
                            }
                            className="h-8 max-w-xl border-0 px-0 text-base font-semibold shadow-none focus-visible:ring-0"
                          />
                          <Button
                            size="icon"
                            variant="ghost"
                            disabled={
                              !topicDirty || !topicTitleDraft.trim() || saving
                            }
                            onClick={() => void handleSaveTopic()}
                            aria-label={t("knowledge.saveTopic")}
                          >
                            <Save className="size-4" />
                          </Button>
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <Badge
                            variant="outline"
                            className={statusClass(detail.parse_status)}
                          >
                            {t(`knowledge.status.${detail.parse_status}`)}
                          </Badge>
                          <span>
                            {t("knowledge.fileCount", {
                              count: detail.source_file_count,
                            })}
                          </span>
                          <span>·</span>
                          <span>
                            {Math.ceil(
                              detail.source_size / 1024,
                            ).toLocaleString()}{" "}
                            {t("knowledge.kilobytes")}
                          </span>
                          {binding?.configured && (
                            <>
                              <span>·</span>
                              <span className="truncate">
                                {binding.provider_name} · {binding.model_name}
                              </span>
                            </>
                          )}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <input
                          ref={addInputRef}
                          type="file"
                          multiple
                          accept=".md,.markdown,text/markdown"
                          className="hidden"
                          disabled={!binding?.configured}
                          onChange={(event) =>
                            void handleAddFiles(
                              Array.from(event.target.files || []),
                            )
                          }
                        />
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={
                            uploading ||
                            !binding?.configured ||
                            detail.source_file_count >= maxTopicFiles
                          }
                          onClick={() => addInputRef.current?.click()}
                        >
                          {uploading ? (
                            <Loader2 className="mr-1 size-4 animate-spin" />
                          ) : (
                            <FileUp className="mr-1 size-4" />
                          )}
                          {t("knowledge.addFiles")}
                        </Button>
                        <Button
                          size="sm"
                          disabled={
                            detail.source_file_count === 0 ||
                            parseBusy ||
                            topicDirty ||
                            bindingDirty ||
                            !binding?.configured
                          }
                          onClick={() => setParsePreparing(true)}
                        >
                          {detail.parse_status === "ready" ||
                          detail.parse_status === "stale" ||
                          detail.parse_status === "failed" ? (
                            <RefreshCw className="mr-1 size-4" />
                          ) : (
                            <Sparkles className="mr-1 size-4" />
                          )}
                          {detail.parse_status === "ready" ||
                          detail.parse_status === "stale" ||
                          detail.parse_status === "failed"
                            ? t("knowledge.reparse")
                            : t("knowledge.parse")}
                        </Button>
                      </div>
                    </div>
                    {!binding?.configured && (
                      <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-700 dark:text-amber-300">
                        {t("knowledge.topicOverview.bindingHint")}
                      </p>
                    )}
                    {detail.parse_status === "stale" && (
                      <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-700 dark:text-amber-300">
                        {t("knowledge.staleHint")}
                      </p>
                    )}
                    {detail.last_error && (
                      <p className="mt-2 break-words rounded-lg bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
                        {detail.last_error_code
                          ? t(
                              `knowledge.parserErrors.${detail.last_error_code}`,
                            )
                          : detail.last_error}
                      </p>
                    )}
                  </div>

                  <ScrollArea className="min-h-0 flex-1">
                    <div className="space-y-5 p-6">
                      <section className="overflow-hidden rounded-xl border bg-background shadow-sm">
                        <div className="flex items-center justify-between border-b px-5 py-4">
                          <div className="flex items-center gap-3">
                            <span className="flex size-9 items-center justify-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-300">
                              <FilePenLine className="size-4" />
                            </span>
                            <div>
                              <h3 className="text-sm font-semibold">
                                {t("knowledge.topicOverview.sourceTitle")}
                              </h3>
                              <p className="mt-0.5 text-xs text-muted-foreground">
                                {t("knowledge.topicOverview.sourceDescription")}
                              </p>
                            </div>
                          </div>
                          <Badge variant="secondary">
                            {detail.source_files.length}
                          </Badge>
                        </div>
                        {detail.source_files.length === 0 ? (
                          <button
                            type="button"
                            className="m-5 flex w-[calc(100%-2.5rem)] flex-col items-center rounded-xl border border-dashed px-6 py-10 text-center text-muted-foreground transition hover:border-primary/40 hover:bg-primary/[0.02] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                            disabled={!binding?.configured || uploading}
                            onClick={() => addInputRef.current?.click()}
                          >
                            <FileUp className="mb-3 size-8" />
                            <span className="text-sm font-medium">
                              {t("knowledge.topicOverview.emptySourceTitle")}
                            </span>
                            <span className="mt-1 text-xs">
                              {t(
                                "knowledge.topicOverview.emptySourceDescription",
                              )}
                            </span>
                          </button>
                        ) : (
                          <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-3">
                            {detail.source_files.map((sourceFile) => (
                              <div
                                key={sourceFile.id}
                                className="group relative overflow-hidden rounded-lg border bg-muted/10 transition hover:border-primary/30 hover:bg-primary/[0.02]"
                              >
                                <button
                                  type="button"
                                  className="flex w-full items-start gap-3 p-4 pr-11 text-left"
                                  onClick={() =>
                                    setSelectedNode({
                                      kind: "sourceFile",
                                      id: sourceFile.id,
                                    })
                                  }
                                >
                                  <FileText className="mt-0.5 size-5 shrink-0 text-blue-600 dark:text-blue-300" />
                                  <span className="min-w-0">
                                    <span className="block truncate text-sm font-medium">
                                      {sourceFile.original_filename}
                                    </span>
                                    <span className="mt-1 block text-xs text-muted-foreground">
                                      {t("knowledge.version", {
                                        version: sourceFile.content_version,
                                      })}{" "}
                                      ·{" "}
                                      {Math.max(
                                        1,
                                        Math.ceil(
                                          sourceFile.content_size / 1024,
                                        ),
                                      )}{" "}
                                      {t("knowledge.kilobytes")}
                                    </span>
                                  </span>
                                </button>
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  className="absolute right-2 top-2 size-8 text-muted-foreground opacity-70 hover:text-destructive group-hover:opacity-100"
                                  onClick={() =>
                                    void handleDeleteSourceFile(sourceFile)
                                  }
                                  disabled={parseBusy}
                                  aria-label={t("knowledge.deleteFile")}
                                >
                                  <Trash2 className="size-4" />
                                </Button>
                              </div>
                            ))}
                          </div>
                        )}
                      </section>

                      <section className="overflow-hidden rounded-xl border bg-background shadow-sm">
                        <div className="flex items-center justify-between border-b px-5 py-4">
                          <div className="flex items-center gap-3">
                            <span className="flex size-9 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-300">
                              <Sparkles className="size-4" />
                            </span>
                            <div>
                              <h3 className="text-sm font-semibold">
                                {t("knowledge.topicOverview.parsedTitle")}
                              </h3>
                              <p className="mt-0.5 text-xs text-muted-foreground">
                                {t("knowledge.topicOverview.parsedDescription")}
                              </p>
                            </div>
                          </div>
                          <Badge variant="secondary">
                            {detail.documents.length}
                          </Badge>
                        </div>
                        {detail.documents.length === 0 ? (
                          <div className="flex flex-col items-center px-6 py-10 text-center text-muted-foreground">
                            <Sparkles className="mb-3 size-8" />
                            <p className="text-sm font-medium">
                              {t("knowledge.topicOverview.emptyParsedTitle")}
                            </p>
                            <p className="mt-1 max-w-lg text-xs leading-5">
                              {t(
                                "knowledge.topicOverview.emptyParsedDescription",
                              )}
                            </p>
                          </div>
                        ) : (
                          <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-3">
                            {detail.documents.map((document) => (
                              <button
                                key={document.id}
                                type="button"
                                className="flex items-start gap-3 rounded-lg border bg-muted/10 p-4 text-left transition hover:border-primary/30 hover:bg-primary/[0.02]"
                                onClick={() =>
                                  handleSelectDocument(document.id)
                                }
                              >
                                <FileText className="mt-0.5 size-5 shrink-0 text-emerald-600 dark:text-emerald-300" />
                                <span className="min-w-0 flex-1">
                                  <span className="flex items-center gap-2">
                                    <span className="truncate text-sm font-medium">
                                      {document.title}
                                    </span>
                                    <Badge
                                      variant="outline"
                                      className="shrink-0 text-[10px]"
                                    >
                                      {document.document_type === "main"
                                        ? t("knowledge.mainDocument")
                                        : t("knowledge.childDocument")}
                                    </Badge>
                                  </span>
                                  <span className="mt-1 block text-xs text-muted-foreground">
                                    {t("knowledge.version", {
                                      version: document.content_version,
                                    })}{" "}
                                    ·{" "}
                                    {Math.max(
                                      1,
                                      Math.ceil(document.content_size / 1024),
                                    )}{" "}
                                    {t("knowledge.kilobytes")}
                                    {document.user_modified
                                      ? ` · ${t("knowledge.userModified")}`
                                      : ""}
                                  </span>
                                </span>
                              </button>
                            ))}
                          </div>
                        )}
                      </section>

                      <div className="flex justify-end">
                        <Button
                          variant="ghost"
                          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          onClick={() => void handleDeleteTopic()}
                          disabled={parseBusy}
                        >
                          <Trash2 className="mr-1 size-4" />
                          {t("knowledge.delete")}
                        </Button>
                      </div>
                    </div>
                  </ScrollArea>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <button
                          type="button"
                          className="flex items-center gap-1 hover:text-foreground"
                          onClick={() => setSelectedNode(null)}
                        >
                          <ArrowLeft className="size-3" />
                          <span>{t("knowledge.topicOverview.back")}</span>
                        </button>
                        <ChevronRight className="size-3" />
                        <span>
                          {selectedNode.kind === "sourceFile"
                            ? t("knowledge.original")
                            : selectedDocument?.document_type === "main"
                              ? t("knowledge.mainDocument")
                              : t("knowledge.childDocument")}
                        </span>
                      </div>
                      <Input
                        value={titleDraft}
                        onChange={(event) => setTitleDraft(event.target.value)}
                        className="mt-1 h-8 border-0 px-0 text-base font-semibold shadow-none focus-visible:ring-0"
                      />
                    </div>
                    <div className="flex shrink-0 items-center gap-1 rounded-lg border bg-muted/30 p-1">
                      {(["preview", "edit", "split"] as ViewMode[]).map(
                        (mode) => (
                          <Button
                            key={mode}
                            size="sm"
                            variant={viewMode === mode ? "secondary" : "ghost"}
                            className="h-7 px-2"
                            onClick={() => setViewMode(mode)}
                          >
                            {t(`knowledge.view.${mode}`)}
                          </Button>
                        ),
                      )}
                    </div>
                  </div>
                  <div
                    className={cn(
                      "grid min-h-0 flex-1",
                      viewMode === "split" && "grid-cols-2 divide-x",
                    )}
                  >
                    {(viewMode === "edit" || viewMode === "split") && (
                      <Textarea
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        className="h-full min-h-0 resize-none rounded-none border-0 p-5 font-mono text-sm leading-6 focus-visible:ring-0"
                      />
                    )}
                    {(viewMode === "preview" || viewMode === "split") && (
                      <ScrollArea className="h-full min-h-0">
                        <article className="prose prose-slate max-w-none p-6 dark:prose-invert">
                          <Markdown
                            remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                            rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                            components={markdownComponents}
                          >
                            {draft}
                          </Markdown>
                        </article>
                      </ScrollArea>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-4 border-t px-4 py-2">
                    <div className="min-w-0 text-xs text-muted-foreground">
                      <span>
                        {t("knowledge.version", {
                          version: content?.content_version || 1,
                        })}
                        {selectedDocument?.user_modified
                          ? ` · ${t("knowledge.userModified")}`
                          : ""}
                      </span>
                      {selectedNode.kind === "document" && (
                        <span className="ml-2 hidden lg:inline">
                          · {t("knowledge.editPersistenceHint")}
                        </span>
                      )}
                    </div>
                    <Button
                      size="sm"
                      disabled={
                        !dirty || saving || !draft.trim() || !titleDraft.trim()
                      }
                      onClick={() => void handleSave()}
                    >
                      {saving ? (
                        <Loader2 className="mr-1 size-4 animate-spin" />
                      ) : (
                        <Save className="mr-1 size-4" />
                      )}
                      {t("knowledge.save")}
                    </Button>
                  </div>
                </>
              )}
            </main>
          </div>
        )}
      </div>

      <Dialog
        open={bindingDialogOpen}
        onOpenChange={(open) => {
          if (bindingSaving) return
          setBindingDialogOpen(open)
          if (!open) {
            setProviderId(binding?.provider_id || "")
            setModelName(binding?.model_name || "")
          }
        }}
      >
        <DialogContent preventOutsideClose={bindingSaving}>
          <DialogHeader>
            <DialogTitle>{t("knowledge.binding.title")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.binding.description")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="knowledge-parser-provider">
                {t("knowledge.binding.provider")}
              </Label>
              <Select
                value={providerId}
                onValueChange={(value) => {
                  setProviderId(value)
                  setModelName(
                    providerModels(
                      providers.find((item) => item.id === value),
                    )[0] || "",
                  )
                }}
              >
                <SelectTrigger id="knowledge-parser-provider">
                  <SelectValue placeholder={t("knowledge.binding.provider")} />
                </SelectTrigger>
                <SelectContent>
                  {providers.map((provider) => (
                    <SelectItem key={provider.id} value={provider.id}>
                      {provider.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="knowledge-parser-model">
                {t("knowledge.binding.model")}
              </Label>
              <Select
                value={modelName}
                onValueChange={setModelName}
                disabled={!providerId || availableModels.length === 0}
              >
                <SelectTrigger id="knowledge-parser-model">
                  <SelectValue placeholder={t("knowledge.binding.model")} />
                </SelectTrigger>
                <SelectContent>
                  {availableModels.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {providersQuery.isError && (
              <p className="text-xs text-destructive">
                {t("knowledge.binding.providersLoadFailed")}
              </p>
            )}
            {binding && !binding.configured && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {binding.error_code
                  ? t(`knowledge.parserErrors.${binding.error_code}`)
                  : binding.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={bindingSaving}
              onClick={() => {
                setProviderId(binding?.provider_id || "")
                setModelName(binding?.model_name || "")
                setBindingDialogOpen(false)
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              disabled={
                bindingSaving || !providerId || !modelName || !bindingDirty
              }
              onClick={() => void handleSaveBinding()}
            >
              {bindingSaving && (
                <Loader2 className="mr-1 size-4 animate-spin" />
              )}
              {t("knowledge.binding.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={uploadDialogOpen}
        onOpenChange={(open) => {
          if (uploading) return
          setUploadDialogOpen(open)
          if (!open) setUploadTitle("")
        }}
      >
        <DialogContent preventOutsideClose={uploading}>
          <DialogHeader>
            <DialogTitle>{t("knowledge.uploadDialog.title")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.uploadDialog.description")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="knowledge-new-topic-title">
              {t("knowledge.topicTitle")}
            </Label>
            <Input
              id="knowledge-new-topic-title"
              value={uploadTitle}
              onChange={(event) => setUploadTitle(event.target.value)}
              maxLength={255}
              autoFocus
              placeholder={t("knowledge.uploadDialog.placeholder")}
              onKeyDown={(event) => {
                if (event.key === "Enter" && uploadTitle.trim())
                  void handleCreateTopic()
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={uploading}
              onClick={() => setUploadDialogOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              disabled={uploading || !uploadTitle.trim()}
              onClick={() => void handleCreateTopic()}
            >
              {uploading && <Loader2 className="mr-1 size-4 animate-spin" />}
              {t("knowledge.createTopic")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
