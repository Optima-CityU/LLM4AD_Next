import {
  BookOpenText,
  Bot,
  ChevronLeft,
  ChevronRight,
  FilePenLine,
  FileText,
  FileUp,
  Loader2,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Save,
  Search,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react"
import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import {
  ApiError,
  type KnowledgeSourceDetail as ApiKnowledgeSourceDetail,
  type KnowledgeParseRunResponse,
  type KnowledgeParserBindingResponse,
  Llm4AdKnowledgeService,
  Llm4AdMemoryService,
  type MemoryCardResponse,
} from "@/client"
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
import { useProviders } from "@/hooks/useProviders"
import { cn } from "@/lib/utils"
import { authFetch } from "@/utils/auth"
import type { MemoryCard } from "../Memory/types"
import KnowledgeDocumentBlockReview from "./KnowledgeDocumentBlockReview"
import KnowledgeProgressTimeline from "./KnowledgeProgressTimeline"
import type {
  KnowledgeContent,
  KnowledgeDocument,
  KnowledgeDocumentInsertResult,
  KnowledgeParseRun,
  KnowledgeParserBinding,
  KnowledgeProgressEvent,
  KnowledgeSource,
  KnowledgeSourceDetail,
  KnowledgeSourceFile,
} from "./types"

const apiBase = `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/knowledge`
const pageSize = 10
const maxFileBytes = 20 * 1024 * 1024
const maxTopicBytes = 100 * 1024 * 1024
const maxTopicFiles = 20
const MAX_KNOWLEDGE_INSERT_STREAM_ATTEMPTS = 3
const modelCapacityPresets = [
  { id: "32k-4k", contextWindowTokens: 32_768, maxOutputTokens: 4_096 },
  { id: "64k-8k", contextWindowTokens: 65_536, maxOutputTokens: 8_192 },
  { id: "128k-16k", contextWindowTokens: 128_000, maxOutputTokens: 16_384 },
  { id: "200k-32k", contextWindowTokens: 200_000, maxOutputTokens: 32_768 },
  { id: "256k-64k", contextWindowTokens: 262_144, maxOutputTokens: 65_536 },
  { id: "400k-128k", contextWindowTokens: 400_000, maxOutputTokens: 131_072 },
  { id: "1m-64k", contextWindowTokens: 1_000_000, maxOutputTokens: 65_536 },
  { id: "2m-128k", contextWindowTokens: 2_000_000, maxOutputTokens: 131_072 },
] as const

type KnowledgeInsertStreamEvent = {
  event: string
  stage?: string
  message?: string
  percent?: number
  data?: KnowledgeDocumentInsertResult
}

class KnowledgeInsertTerminalError extends Error {}

async function readKnowledgeInsertStream(
  response: Response,
  onEvent: (event: KnowledgeInsertStreamEvent) => void,
): Promise<KnowledgeDocumentInsertResult> {
  const reader = response.body?.getReader()
  if (!reader) throw new Error("Streaming response is unavailable")
  const decoder = new TextDecoder()
  let buffer = ""
  let eventName = "message"
  let dataLines: string[] = []
  const terminal: {
    completed?: KnowledgeDocumentInsertResult
    error?: string
  } = {}

  const flush = () => {
    if (dataLines.length === 0) {
      eventName = "message"
      return
    }
    const payload = JSON.parse(dataLines.join("\n")) as Omit<
      KnowledgeInsertStreamEvent,
      "event"
    >
    const event = { ...payload, event: eventName }
    onEvent(event)
    if (event.event === "completed" && event.data)
      terminal.completed = event.data
    if (event.event === "error" || event.event === "cancelled") {
      terminal.error = event.message || "Structured document insertion failed"
    }
    eventName = "message"
    dataLines = []
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) {
      if (line.startsWith("event:"))
        eventName = line.slice(6).trim() || "message"
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim())
      else if (line.trim() === "") flush()
    }
  }
  buffer += decoder.decode()
  if (buffer.startsWith("data:")) dataLines.push(buffer.slice(5).trim())
  flush()
  if (terminal.error) throw new KnowledgeInsertTerminalError(terminal.error)
  if (!terminal.completed)
    throw new Error(
      "Structured document insertion stream ended before completion",
    )
  return terminal.completed
}

function formatTokenLimit(value: number) {
  if (value >= 1_000_000) return `${value / 1_000_000}M`
  if (value % 1_000 === 0) return `${value / 1_000}K`
  return `${Math.round(value / 1_024)}K`
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => null)
  return typeof payload?.detail === "string" ? payload.detail : fallback
}

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | undefined)?.detail
    if (typeof detail === "string") return detail
    if (Array.isArray(detail) && typeof detail[0]?.msg === "string")
      return detail[0].msg
  }
  return error instanceof Error && error.message ? error.message : fallback
}

function normalizeSourceDetail(
  payload: ApiKnowledgeSourceDetail,
): KnowledgeSourceDetail {
  return {
    ...payload,
    source_files: payload.source_files ?? [],
    documents: payload.documents ?? [],
  }
}

function normalizeParseRun(
  payload: KnowledgeParseRunResponse,
): KnowledgeParseRun {
  return {
    ...payload,
    can_refine: payload.can_refine ?? false,
    generated_memory_ids: payload.generated_memory_ids ?? [],
    inserted_document_ids: payload.inserted_document_ids ?? [],
  }
}

function normalizeParserBinding(
  payload: KnowledgeParserBindingResponse,
): KnowledgeParserBinding {
  return {
    ...payload,
    provider_id: payload.provider_id ?? null,
    provider_name: payload.provider_name ?? null,
    provider_type: payload.provider_type ?? null,
    model_name: payload.model_name ?? null,
    context_window_tokens: payload.context_window_tokens ?? 128_000,
    max_output_tokens: payload.max_output_tokens ?? 16_384,
    error_code: payload.error_code ?? null,
    message: payload.message ?? "",
  }
}

function normalizeMemoryCard(payload: MemoryCardResponse): MemoryCard {
  return {
    ...payload,
    enabled: payload.enabled ?? true,
    source: payload.source ?? "",
    tags: payload.tags ?? [],
    readonly: payload.readonly
      ? {
          ...payload.readonly,
          source: payload.readonly.source ?? "",
          status: payload.readonly.status ?? "",
        }
      : payload.readonly,
    structured_content: payload.structured_content
      ? {
          ...payload.structured_content,
          artifacts: payload.structured_content.artifacts ?? [],
        }
      : payload.structured_content,
  }
}

function providerModels(provider?: { model?: string }) {
  return (provider?.model || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
}

function isActive(status?: string | null) {
  return status === "pending" || status === "running"
}

function statusClass(status: KnowledgeSource["parse_status"]) {
  if (status === "ready")
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
  if (status === "failed")
    return "border-destructive/30 bg-destructive/10 text-destructive"
  if (status === "running" || status === "pending")
    return "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300"
  if (status === "stale")
    return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
  return ""
}

export default function KnowledgeMemoryImportWorkspace() {
  const { t } = useTranslation()
  const uploadInputRef = useRef<HTMLInputElement>(null)
  const providersQuery = useProviders()
  const providers = providersQuery.data?.items ?? []

  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [searchInput, setSearchInput] = useState("")
  const search = useDeferredValue(searchInput.trim())
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [detail, setDetail] = useState<KnowledgeSourceDetail | null>(null)
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null)
  const [content, setContent] = useState<KnowledgeContent | null>(null)
  const [contentDraft, setContentDraft] = useState("")
  const [filenameDraft, setFilenameDraft] = useState("")
  const [editingFile, setEditingFile] = useState(false)
  const [editingTopic, setEditingTopic] = useState(false)
  const [topicTitleDraft, setTopicTitleDraft] = useState("")
  const [backgroundDraft, setBackgroundDraft] = useState("")
  const [backgroundOpen, setBackgroundOpen] = useState(false)
  const [run, setRun] = useState<KnowledgeParseRun | null>(null)
  const [events, setEvents] = useState<KnowledgeProgressEvent[]>([])
  const [documentBlocks, setDocumentBlocks] = useState<KnowledgeDocument[]>([])
  const [generatedMemories, setGeneratedMemories] = useState<MemoryCard[]>([])
  const [draftsLoading, setDraftsLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [insertProgress, setInsertProgress] = useState<{
    stage: string
    percent: number
    message: string
  } | null>(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [prepareOpen, setPrepareOpen] = useState(false)
  const [restartOpen, setRestartOpen] = useState(false)
  const [extractionInstruction, setExtractionInstruction] = useState("")
  const [newTitle, setNewTitle] = useState("")
  const [bindingOpen, setBindingOpen] = useState(false)
  const [binding, setBinding] = useState<KnowledgeParserBinding | null>(null)
  const [providerId, setProviderId] = useState("")
  const [modelName, setModelName] = useState("")
  const [contextWindowTokens, setContextWindowTokens] = useState(128_000)
  const [maxOutputTokens, setMaxOutputTokens] = useState(16_384)
  const [customLimitsSelected, setCustomLimitsSelected] = useState(false)
  const [libraryCollapsed, setLibraryCollapsed] = useState(() =>
    typeof window !== "undefined"
      ? window.localStorage.getItem("knowledge-topic-sidebar-collapsed") ===
        "true"
      : false,
  )
  const selectedProvider = providers.find((item) => item.id === providerId)
  const models = useMemo(
    () => providerModels(selectedProvider),
    [selectedProvider],
  )
  const selectedFile = detail?.source_files.find(
    (item) => item.id === selectedFileId,
  )
  const selectedLimitPreset = customLimitsSelected
    ? "custom"
    : modelCapacityPresets.find(
        (item) =>
          item.contextWindowTokens === contextWindowTokens &&
          item.maxOutputTokens === maxOutputTokens,
      )?.id || "custom"
  const bindingLimitsInvalid =
    contextWindowTokens < 4_096 ||
    contextWindowTokens > 2_000_000 ||
    maxOutputTokens < 256 ||
    maxOutputTokens > 256_000 ||
    maxOutputTokens > contextWindowTokens
  const markdownComponents = useMemo(
    () => makeMarkdownComponents("knowledge-memory-import"),
    [],
  )

  const loadSources = useCallback(
    async (preferredId?: string | null) => {
      const payload = await Llm4AdKnowledgeService.listSources({
        skip: page * pageSize,
        limit: pageSize,
        search: search || null,
      })
      setSources(payload.items)
      setTotal(payload.total)
      setSelectedSourceId((current) => {
        const desired = preferredId ?? current
        return desired && payload.items.some((item) => item.id === desired)
          ? desired
          : payload.items[0]?.id || null
      })
    },
    [page, search],
  )

  const loadDetail = useCallback(async (sourceId: string) => {
    const payload = normalizeSourceDetail(
      await Llm4AdKnowledgeService.getSource({ sourceId }),
    )
    setDetail(payload)
    setTopicTitleDraft(payload.title)
    setBackgroundDraft(payload.background || "")
    setSelectedFileId((current) =>
      current && payload.source_files.some((item) => item.id === current)
        ? current
        : payload.source_files[0]?.id || null,
    )
    return payload
  }, [])

  const toggleLibrary = () => {
    setLibraryCollapsed((current) => {
      const next = !current
      window.localStorage.setItem(
        "knowledge-topic-sidebar-collapsed",
        String(next),
      )
      return next
    })
  }

  const loadRun = useCallback(async (sourceId: string) => {
    const latest = await Llm4AdKnowledgeService.getLatestParseRun({
      sourceId,
    })
    const payload = latest ? normalizeParseRun(latest) : null
    setRun(payload)
    if (payload) {
      const nextEvents = await Llm4AdKnowledgeService.listParseRunEvents({
        runId: payload.id,
      })
      setEvents(nextEvents as KnowledgeProgressEvent[])
    } else {
      setEvents([])
    }
    return payload
  }, [])

  const loadDocumentBlocks = useCallback(
    async (sourceId: string) => {
      const payload = await loadDetail(sourceId)
      setDocumentBlocks(payload.documents)
      return payload.documents
    },
    [loadDetail],
  )

  const loadGeneratedMemories = useCallback(async (runId: string) => {
    const payload = (
      await Llm4AdKnowledgeService.listGeneratedMemoryCards({ runId })
    ).map(normalizeMemoryCard)
    setGeneratedMemories(payload)
    return payload
  }, [])

  const loadBinding = useCallback(async () => {
    const payload = normalizeParserBinding(
      await Llm4AdKnowledgeService.getParserBinding(),
    )
    setBinding(payload)
    setProviderId(payload.provider_id || "")
    setModelName(payload.model_name || "")
    setContextWindowTokens(payload.context_window_tokens || 128_000)
    setMaxOutputTokens(payload.max_output_tokens || 16_384)
  }, [])

  useEffect(() => {
    Promise.all([loadSources(), loadBinding()])
      .catch((error) =>
        toast.error(apiErrorMessage(error, t("knowledge.errors.load"))),
      )
      .finally(() => setLoading(false))
  }, [loadBinding, loadSources, t])

  useEffect(() => {
    if (!selectedSourceId) {
      setDetail(null)
      setRun(null)
      setEvents([])
      setDocumentBlocks([])
      setGeneratedMemories([])
      setDraftsLoading(false)
      return
    }
    setContent(null)
    setEditingFile(false)
    setEditingTopic(false)
    setDocumentBlocks([])
    setGeneratedMemories([])
    setDraftsLoading(false)
    Promise.all([
      loadDetail(selectedSourceId),
      loadRun(selectedSourceId),
    ]).catch((error) =>
      toast.error(apiErrorMessage(error, t("knowledge.errors.load"))),
    )
  }, [loadDetail, loadRun, selectedSourceId, t])

  useEffect(() => {
    if (!selectedFileId) {
      setContent(null)
      return
    }
    const file = detail?.source_files.find((item) => item.id === selectedFileId)
    Llm4AdKnowledgeService.getSourceFileContent({ fileId: selectedFileId })
      .then((payload) => {
        setContent(payload)
        setContentDraft(payload.content)
        setFilenameDraft(file?.original_filename || "")
      })
      .catch((error) =>
        toast.error(apiErrorMessage(error, t("knowledge.errors.loadContent"))),
      )
  }, [detail?.source_files, selectedFileId, t])

  useEffect(() => {
    if (!selectedSourceId || !isActive(run?.status)) return
    const timer = window.setInterval(() => {
      void loadRun(selectedSourceId).then((next) => {
        if (next?.status === "ready") {
          void Promise.all([
            loadSources(selectedSourceId),
            loadDocumentBlocks(selectedSourceId),
          ])
        }
      })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [loadDocumentBlocks, loadRun, loadSources, run?.status, selectedSourceId])

  useEffect(() => {
    if (run?.status !== "ready") return
    setDraftsLoading(true)
    void loadDocumentBlocks(run.source_id)
      .catch((error) =>
        toast.error(
          apiErrorMessage(error, t("knowledge.documentBlocks.loadFailed")),
        ),
      )
      .finally(() => setDraftsLoading(false))
  }, [loadDocumentBlocks, run?.source_id, run?.status, t])

  useEffect(() => {
    if (run?.status !== "ready") return
    if ((run.generated_memory_ids || []).length === 0) {
      setGeneratedMemories([])
      return
    }
    void loadGeneratedMemories(run.id).catch((error) =>
      toast.error(
        apiErrorMessage(
          error,
          t("knowledge.documentBlocks.generatedLoadFailed"),
        ),
      ),
    )
  }, [
    loadGeneratedMemories,
    run?.generated_memory_ids,
    run?.id,
    run?.status,
    t,
  ])

  const createTopic = async () => {
    if (!newTitle.trim()) return
    setBusy(true)
    try {
      const source = await Llm4AdKnowledgeService.createSource({
        requestBody: { title: newTitle.trim() },
      })
      setCreateOpen(false)
      setNewTitle("")
      setPage(0)
      setSearchInput("")
      await loadSources(source.id)
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.upload")))
    } finally {
      setBusy(false)
    }
  }

  const addFiles = async (files: File[]) => {
    if (!detail || files.length === 0) return
    if (files.some((file) => !/\.(md|markdown)$/i.test(file.name)))
      return toast.error(t("knowledge.errors.fileType"))
    if (files.some((file) => file.size > maxFileBytes))
      return toast.error(t("knowledge.errors.fileSize"))
    if (detail.source_file_count + files.length > maxTopicFiles)
      return toast.error(t("knowledge.errors.fileCount"))
    if (
      detail.source_size + files.reduce((sum, file) => sum + file.size, 0) >
      maxTopicBytes
    )
      return toast.error(t("knowledge.errors.topicSize"))
    setBusy(true)
    try {
      await Llm4AdKnowledgeService.addSourceFiles({
        sourceId: detail.id,
        formData: { files },
      })
      await Promise.all([loadDetail(detail.id), loadSources(detail.id)])
      toast.success(t("knowledge.messages.filesAdded", { count: files.length }))
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.upload")))
    } finally {
      setBusy(false)
      if (uploadInputRef.current) uploadInputRef.current.value = ""
    }
  }

  const saveFile = async () => {
    if (!selectedFile || !filenameDraft.trim() || !contentDraft.trim()) return
    setBusy(true)
    try {
      await Llm4AdKnowledgeService.updateSourceFile({
        fileId: selectedFile.id,
        requestBody: {
          original_filename: filenameDraft.trim(),
          content: contentDraft,
        },
      })
      await Promise.all([
        loadDetail(selectedFile.source_id),
        loadSources(selectedFile.source_id),
      ])
      setEditingFile(false)
      toast.success(t("knowledge.messages.sourceSaved"))
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.save")))
    } finally {
      setBusy(false)
    }
  }

  const beginFileEdit = () => {
    if (!selectedFile || !content) return
    setFilenameDraft(selectedFile.original_filename)
    setContentDraft(content.content)
    setEditingFile(true)
  }

  const cancelFileEdit = () => {
    setFilenameDraft(selectedFile?.original_filename || "")
    setContentDraft(content?.content || "")
    setEditingFile(false)
  }

  const saveTopic = async () => {
    if (!detail || !topicTitleDraft.trim()) return
    setBusy(true)
    try {
      await Llm4AdKnowledgeService.updateSource({
        sourceId: detail.id,
        requestBody: { title: topicTitleDraft.trim() },
      })
      await Promise.all([loadDetail(detail.id), loadSources(detail.id)])
      setEditingTopic(false)
      toast.success(t("knowledge.messages.topicSaved"))
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.save")))
    } finally {
      setBusy(false)
    }
  }

  const saveBackground = async () => {
    if (!detail) return
    setBusy(true)
    try {
      await Llm4AdKnowledgeService.updateSource({
        sourceId: detail.id,
        requestBody: { background: backgroundDraft.trim() || null },
      })
      await Promise.all([loadDetail(detail.id), loadSources(detail.id)])
      setBackgroundOpen(false)
      toast.success(t("knowledge.background.saved"))
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.save")))
    } finally {
      setBusy(false)
    }
  }

  const deleteFile = async (file: KnowledgeSourceFile) => {
    if (
      !window.confirm(
        t("knowledge.deleteFileConfirm", { name: file.original_filename }),
      )
    )
      return
    try {
      await Llm4AdKnowledgeService.deleteSourceFile({ fileId: file.id })
      if (detail)
        await Promise.all([loadDetail(detail.id), loadSources(detail.id)])
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.delete")))
    }
  }

  const deleteTopic = async () => {
    if (
      !detail ||
      !window.confirm(
        t("knowledge.memoryImport.deleteTopicConfirm", { title: detail.title }),
      )
    )
      return
    try {
      await Llm4AdKnowledgeService.deleteSource({ sourceId: detail.id })
      setSelectedSourceId(null)
      await loadSources(null)
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.delete")))
    }
  }

  const saveBinding = async () => {
    if (!providerId || !modelName)
      return toast.error(t("knowledge.binding.required"))
    if (bindingLimitsInvalid)
      return toast.error(t("knowledge.binding.limitInvalid"))
    setBusy(true)
    try {
      const payload = await Llm4AdKnowledgeService.updateParserBinding({
        requestBody: {
          provider_id: providerId,
          model_name: modelName,
          context_window_tokens: contextWindowTokens,
          max_output_tokens: maxOutputTokens,
        },
      })
      setBinding(normalizeParserBinding(payload))
      setBindingOpen(false)
      toast.success(t("knowledge.binding.saved"))
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.binding.saveFailed")))
    } finally {
      setBusy(false)
    }
  }

  const startExtraction = async () => {
    if (!detail || !binding?.configured || detail.source_file_count === 0)
      return
    setBusy(true)
    try {
      const payload = await Llm4AdKnowledgeService.startParse({
        sourceId: detail.id,
        requestBody: {
          mode: "direct",
          instruction: extractionInstruction.trim() || null,
        },
      })
      const next = normalizeParseRun(payload)
      setRun(next)
      setEvents([])
      setDocumentBlocks([])
      setDraftsLoading(false)
      setPrepareOpen(false)
      setExtractionInstruction("")
    } catch (error) {
      toast.error(apiErrorMessage(error, t("knowledge.errors.parse")))
    } finally {
      setBusy(false)
    }
  }

  const loadDocumentContent = async (documentId: string) => {
    return Llm4AdKnowledgeService.getDocumentContent({ documentId })
  }

  const saveDocumentBlock = async (
    documentId: string,
    patch: { title: string; content: string },
  ) => {
    setBusy(true)
    try {
      const updated = await Llm4AdKnowledgeService.updateDocument({
        documentId,
        requestBody: patch,
      })
      setDocumentBlocks((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      )
      setRun((current) =>
        current
          ? {
              ...current,
              inserted_document_ids: current.inserted_document_ids.filter(
                (id) => id !== documentId,
              ),
              can_refine:
                current.inserted_document_ids.filter((id) => id !== documentId)
                  .length === 0 && current.generated_memory_ids.length === 0,
            }
          : current,
      )
      toast.success(t("knowledge.documentBlocks.saved"))
    } catch (error) {
      toast.error(
        apiErrorMessage(error, t("knowledge.documentBlocks.saveFailed")),
      )
      throw error
    } finally {
      setBusy(false)
    }
  }

  const refineDocumentBlocks = async (instruction: string) => {
    if (!run) return
    setBusy(true)
    try {
      const payload = await Llm4AdKnowledgeService.refineParseRun({
        runId: run.id,
        requestBody: { instruction },
      })
      setRun(normalizeParseRun(payload))
      setEvents([])
      setDocumentBlocks([])
      setDraftsLoading(false)
    } catch (error) {
      toast.error(
        apiErrorMessage(error, t("knowledge.documentBlocks.refineFailed")),
      )
      throw error
    } finally {
      setBusy(false)
    }
  }

  const insertDocumentBlocks = async (documentIds: string[]) => {
    if (!run || documentIds.length === 0) return
    setBusy(true)
    setInsertProgress({
      stage: "starting",
      percent: 1,
      message: t("knowledge.documentBlocks.insertStages.starting"),
    })
    try {
      const requestBody = JSON.stringify({ document_ids: documentIds })
      let result: KnowledgeDocumentInsertResult | null = null
      for (
        let attempt = 1;
        attempt <= MAX_KNOWLEDGE_INSERT_STREAM_ATTEMPTS;
        attempt += 1
      ) {
        try {
          const response = await authFetch(
            `${apiBase}/parse-runs/${run.id}/documents/insert/stream`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Accept: "text/event-stream",
              },
              body: requestBody,
            },
          )
          if (!response.ok) {
            throw new KnowledgeInsertTerminalError(
              await responseError(
                response,
                t("knowledge.documentBlocks.insertFailed"),
              ),
            )
          }
          result = await readKnowledgeInsertStream(response, (event) => {
            if (!event.stage && typeof event.percent !== "number") return
            const stage = event.stage || "waiting"
            setInsertProgress((current) => {
              const previousPercent = current?.percent ?? 1
              const reportedPercent =
                typeof event.percent === "number"
                  ? Math.max(1, Math.min(100, event.percent))
                  : previousPercent
              return {
                stage,
                percent: Math.max(previousPercent, reportedPercent),
                message: t(`knowledge.documentBlocks.insertStages.${stage}`, {
                  defaultValue:
                    event.message ||
                    t("knowledge.documentBlocks.insertStages.waiting"),
                }),
              }
            })
          })
          break
        } catch (error) {
          if (error instanceof KnowledgeInsertTerminalError) throw error
          if (attempt === MAX_KNOWLEDGE_INSERT_STREAM_ATTEMPTS) {
            throw new Error(
              t("knowledge.documentBlocks.insertStreamInterrupted"),
            )
          }
          setInsertProgress((current) => ({
            stage: "reconnecting",
            percent: current?.percent ?? 1,
            message: t("knowledge.documentBlocks.insertStages.reconnecting", {
              attempt: attempt + 1,
              total: MAX_KNOWLEDGE_INSERT_STREAM_ATTEMPTS,
            }),
          }))
        }
      }
      if (!result)
        throw new Error(t("knowledge.documentBlocks.insertStreamInterrupted"))
      setRun((current) =>
        current
          ? {
              ...current,
              inserted_document_ids: result.inserted_document_ids,
              generated_memory_ids: result.generated_memory_ids,
              can_refine: result.inserted_document_ids.length === 0,
            }
          : current,
      )
      setGeneratedMemories(result.generated_memories || [])
      toast.success(
        t("knowledge.documentBlocks.insertedCount", {
          count: documentIds.length,
        }),
      )
    } catch (error) {
      toast.error(
        apiErrorMessage(error, t("knowledge.documentBlocks.insertFailed")),
      )
      throw error
    } finally {
      setBusy(false)
      setInsertProgress(null)
    }
  }

  const editGeneratedMemory = async (
    memoryId: string,
    patch: Pick<
      MemoryCard,
      "type" | "title" | "content" | "structured_content" | "tags"
    >,
  ) => {
    const current = generatedMemories.find((item) => item.id === memoryId)
    if (!current) return
    try {
      const payload = await Llm4AdMemoryService.updateMemoryCard({
        memoryId,
        scope: "user",
        requestBody: {
          type: patch.type,
          title: patch.title,
          content: patch.content,
          structured_content: patch.structured_content,
          enabled: current.enabled,
          tags: patch.tags,
        },
      })
      const updated = normalizeMemoryCard(payload)
      setGeneratedMemories((items) =>
        items.map((item) => (item.id === memoryId ? updated : item)),
      )
      toast.success(t("knowledge.documentBlocks.generatedEdited"))
    } catch (error) {
      const message = apiErrorMessage(
        error,
        t("knowledge.documentBlocks.generatedEditFailed"),
      )
      toast.error(message)
      throw new Error(message)
    }
  }

  const deleteGeneratedMemory = async (memoryId: string) => {
    try {
      await Llm4AdMemoryService.deleteMemoryCard({ memoryId, scope: "user" })
      setGeneratedMemories((items) =>
        items.filter((item) => item.id !== memoryId),
      )
      toast.success(t("knowledge.documentBlocks.generatedDeleted"))
    } catch (error) {
      const message = apiErrorMessage(
        error,
        t("knowledge.documentBlocks.generatedDeleteFailed"),
      )
      toast.error(message)
      throw new Error(message)
    }
  }

  const stopExtraction = async () => {
    if (!run || !isActive(run.status)) return
    setBusy(true)
    try {
      await Llm4AdKnowledgeService.cancelParseRun({ runId: run.id })
      if (detail) {
        await Promise.all([
          loadRun(detail.id),
          loadDetail(detail.id),
          loadSources(detail.id),
        ])
      }
    } catch (error) {
      toast.error(
        apiErrorMessage(error, t("knowledge.parseWorkspace.stopFailed")),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden rounded-xl border bg-background shadow-sm">
      <input
        ref={uploadInputRef}
        type="file"
        multiple
        accept=".md,.markdown,text/markdown"
        className="hidden"
        onChange={(event) =>
          void addFiles(Array.from(event.target.files || []))
        }
      />

      <aside
        className={cn(
          "flex shrink-0 flex-col border-r bg-muted/20 transition-[width] duration-300 ease-out",
          libraryCollapsed ? "w-12" : "w-60",
        )}
      >
        {libraryCollapsed ? (
          <div className="flex h-full flex-col items-center gap-2 py-2">
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              onClick={toggleLibrary}
              aria-label={t("knowledge.workspace.expandLibrary")}
              title={t("knowledge.workspace.expandLibrary")}
            >
              <PanelLeftOpen className="size-4" />
            </Button>
            <div className="h-px w-6 bg-border" />
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              onClick={() => setCreateOpen(true)}
              aria-label={t("knowledge.createTopic")}
              title={t("knowledge.createTopic")}
            >
              <Plus className="size-4" />
            </Button>
            <BookOpenText className="mt-auto mb-1 size-4 text-muted-foreground" />
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between border-b px-3 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">
                  {t("knowledge.library")}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("knowledge.topicCount", { count: total })}
                </p>
              </div>
              <div className="flex items-center gap-0.5">
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-8"
                  onClick={() => setCreateOpen(true)}
                >
                  <Plus className="size-4" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-8"
                  onClick={toggleLibrary}
                  aria-label={t("knowledge.workspace.collapseLibrary")}
                  title={t("knowledge.workspace.collapseLibrary")}
                >
                  <PanelLeftClose className="size-4" />
                </Button>
              </div>
            </div>
            <div className="border-b p-2">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchInput}
                  onChange={(event) => {
                    setSearchInput(event.target.value)
                    setPage(0)
                  }}
                  className="h-8 pl-8 pr-8 text-xs"
                  placeholder={t("knowledge.searchPlaceholder")}
                />
                {searchInput && (
                  <button
                    type="button"
                    onClick={() => setSearchInput("")}
                    className="absolute right-2 top-1/2 -translate-y-1/2"
                  >
                    <X className="size-3" />
                  </button>
                )}
              </div>
            </div>
            <ScrollArea className="min-h-0 flex-1 overscroll-contain">
              <div className="space-y-1 p-2">
                {loading ? (
                  <Loader2 className="mx-auto mt-10 size-5 animate-spin" />
                ) : sources.length === 0 ? (
                  <button
                    type="button"
                    className="w-full rounded-lg border border-dashed p-8 text-center text-xs text-muted-foreground"
                    onClick={() => setCreateOpen(true)}
                  >
                    <FileUp className="mx-auto mb-2 size-7" />
                    {t("knowledge.emptyTitle")}
                  </button>
                ) : (
                  sources.map((source) => (
                    <button
                      key={source.id}
                      type="button"
                      title={source.title}
                      onClick={() => setSelectedSourceId(source.id)}
                      className={cn(
                        "w-full rounded-lg px-3 py-2.5 text-left transition-colors",
                        selectedSourceId === source.id
                          ? "bg-primary/10 text-primary"
                          : "hover:bg-muted",
                      )}
                    >
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
                    </button>
                  ))
                )}
              </div>
            </ScrollArea>
            <div className="flex items-center justify-between border-t p-2">
              <Button
                size="icon"
                variant="outline"
                className="size-7"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                <ChevronLeft className="size-3" />
              </Button>
              <span className="text-[11px] text-muted-foreground">
                {t("knowledge.pageOf", {
                  page: page + 1,
                  total: Math.max(1, Math.ceil(total / pageSize)),
                })}
              </span>
              <Button
                size="icon"
                variant="outline"
                className="size-7"
                disabled={(page + 1) * pageSize >= total}
                onClick={() => setPage((value) => value + 1)}
              >
                <ChevronRight className="size-3" />
              </Button>
            </div>
          </>
        )}
      </aside>

      {!detail ? (
        <div className="flex flex-1 flex-col items-center justify-center text-sm text-muted-foreground">
          <BookOpenText className="mb-3 size-10" />
          {t("knowledge.memoryImport.selectTopic")}
        </div>
      ) : (
        <>
          <section className="@container/source-pane flex min-w-0 flex-[1.35] flex-col border-r">
            <div className="flex items-center gap-2 border-b px-3 py-2">
              <div
                data-testid="knowledge-topic-heading"
                className="w-48 min-w-0 shrink-0"
              >
                <p className="truncate text-[10px] font-medium tracking-wide text-muted-foreground">
                  {t("knowledge.topicOverview.topicLabel")} ·{" "}
                  {t("knowledge.sourceCount", {
                    count: detail.source_file_count,
                  })}
                </p>
                <button
                  type="button"
                  title={detail.title}
                  className="group -ml-1 mt-0.5 flex max-w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-muted"
                  onClick={() => {
                    setTopicTitleDraft(detail.title)
                    setEditingTopic(true)
                  }}
                >
                  <span className="truncate text-[15px] font-semibold leading-5">
                    {detail.title}
                  </span>
                  <span className="flex size-5 shrink-0 items-center justify-center rounded border bg-background text-muted-foreground transition-colors group-hover:border-primary/40 group-hover:text-primary">
                    <Pencil
                      data-testid="knowledge-topic-edit-icon"
                      className="size-3"
                    />
                  </span>
                </button>
              </div>
              <div className="h-6 w-px shrink-0 bg-border" />
              <div
                data-testid="knowledge-document-actions"
                className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden pr-1"
              >
                {detail.source_files.length > 0 &&
                  (editingFile ? (
                    <Input
                      value={filenameDraft}
                      onChange={(event) => setFilenameDraft(event.target.value)}
                      className="h-8 min-w-0 max-w-[240px] flex-1 text-xs"
                    />
                  ) : (
                    <Select
                      value={selectedFileId || undefined}
                      onValueChange={(value) => {
                        setSelectedFileId(value)
                        setEditingFile(false)
                      }}
                    >
                      <SelectTrigger
                        data-testid="knowledge-source-switcher"
                        size="sm"
                        title={selectedFile?.original_filename || ""}
                        className="min-w-0 max-w-[240px] flex-1 overflow-hidden bg-muted/20 text-xs"
                      >
                        <FileText className="size-3.5" />
                        <SelectValue
                          placeholder={t("knowledge.memoryImport.selectSource")}
                        />
                      </SelectTrigger>
                      <SelectContent align="start">
                        {detail.source_files.map((file) => (
                          <SelectItem key={file.id} value={file.id}>
                            {file.original_filename}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ))}
                <Button
                  data-testid="knowledge-add-document"
                  size="sm"
                  variant="outline"
                  className="h-8 shrink-0 px-2"
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={busy || editingFile}
                  aria-label={t("knowledge.addFiles")}
                  title={t("knowledge.addFiles")}
                >
                  <Plus className="size-3.5" />
                  <span className="ml-1 hidden @3xl/source-pane:inline">
                    {t("knowledge.addMoreFiles")}
                  </span>
                </Button>
                {selectedFile && (
                  <>
                    <Button
                      size="icon"
                      variant={editingFile ? "default" : "ghost"}
                      className="size-8 shrink-0"
                      onClick={() =>
                        editingFile ? void saveFile() : beginFileEdit()
                      }
                      disabled={busy || !content}
                      aria-label={
                        editingFile
                          ? t("knowledge.save")
                          : t("knowledge.view.edit")
                      }
                      title={
                        editingFile
                          ? t("knowledge.save")
                          : t("knowledge.view.edit")
                      }
                    >
                      {editingFile ? (
                        <Save className="size-3.5" />
                      ) : (
                        <Pencil className="size-3.5" />
                      )}
                    </Button>
                    {editingFile && (
                      <Button
                        size="icon"
                        variant="ghost"
                        className="size-8 shrink-0"
                        onClick={cancelFileEdit}
                        disabled={busy}
                        aria-label={t("common.cancel")}
                        title={t("common.cancel")}
                      >
                        <X className="size-3.5" />
                      </Button>
                    )}
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-8 shrink-0 text-destructive"
                      onClick={() => void deleteFile(selectedFile)}
                      disabled={busy || editingFile || isActive(run?.status)}
                      aria-label={t("knowledge.deleteFile")}
                      title={t("knowledge.deleteFile")}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </>
                )}
              </div>
              <div className="h-6 w-px shrink-0 bg-border" />
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  data-testid="knowledge-background-action"
                  size="sm"
                  variant={detail.background ? "secondary" : "outline"}
                  className="px-2"
                  onClick={() => setBackgroundOpen(true)}
                  disabled={isActive(run?.status)}
                  title={
                    detail.background || t("knowledge.background.notConfigured")
                  }
                >
                  <MessageSquareText className="size-3.5" />
                  <span className="ml-1 hidden @3xl/source-pane:inline">
                    {t("knowledge.background.button")}
                  </span>
                  {detail.background && (
                    <span className="ml-1 size-1.5 rounded-full bg-emerald-500" />
                  )}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-8 text-destructive"
                  onClick={() => void deleteTopic()}
                  disabled={isActive(run?.status)}
                  aria-label={t("knowledge.memoryImport.deleteTopicConfirm", {
                    title: detail.title,
                  })}
                  title={t("knowledge.memoryImport.deleteTopicConfirm", {
                    title: detail.title,
                  })}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
            <div className="flex min-h-0 flex-1 flex-col">
              {selectedFile && content ? (
                editingFile ? (
                  <div className="min-h-0 flex-1">
                    <Textarea
                      data-testid="knowledge-source-editor"
                      value={contentDraft}
                      onChange={(event) => setContentDraft(event.target.value)}
                      maxLength={maxFileBytes}
                      className="h-full min-h-0 w-full resize-none rounded-none border-0 p-4 font-mono text-xs focus-visible:ring-0"
                    />
                  </div>
                ) : (
                  <div
                    data-testid="knowledge-source-preview"
                    className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
                  >
                    <article className="prose prose-sm w-full min-w-0 max-w-full break-words p-5 dark:prose-invert [&_pre]:max-w-full [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
                      <Markdown
                        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                        components={markdownComponents}
                      >
                        {content.content}
                      </Markdown>
                    </article>
                  </div>
                )
              ) : (
                <div className="flex flex-1 flex-col items-center justify-center text-xs text-muted-foreground">
                  <FilePenLine className="mb-2 size-8" />
                  {t("knowledge.memoryImport.selectSource")}
                </div>
              )}
            </div>
          </section>

          <section className="flex min-w-[360px] basis-[42%] flex-col bg-muted/10 2xl:basis-[46%]">
            <div className="flex items-center justify-between gap-2 border-b bg-background px-3 py-2.5">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-semibold">
                  <Sparkles className="size-4 shrink-0 text-primary" />
                  <span className="truncate">
                    {t("knowledge.memoryImport.title")}
                  </span>
                </p>
                <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                  {t("knowledge.memoryImport.description")}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="max-w-36 shrink-0"
                onClick={() => setBindingOpen(true)}
              >
                <Bot className="mr-1 size-3.5 shrink-0" />
                <span className="truncate">
                  {binding?.model_name || t("knowledge.binding.title")}
                </span>
              </Button>
            </div>
            <div
              data-testid="knowledge-review-scroller"
              className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
            >
              <div className="w-full min-w-0 space-y-4 p-3">
                {run && run.status !== "ready" && (
                  <div className="space-y-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-xs font-semibold">
                          {t(`knowledge.progressStages.${run.stage}`, {
                            defaultValue: run.message,
                          })}
                        </p>
                        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                          {run.message}
                        </p>
                      </div>
                      <span className="text-lg font-semibold tabular-nums text-primary">
                        {run.progress}%
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary transition-[width] duration-500"
                        style={{ width: `${run.progress}%` }}
                      />
                    </div>
                    <KnowledgeProgressTimeline run={run} events={events} />
                    {run.status === "failed" && (
                      <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                        {run.error || t("knowledge.errors.parse")}
                      </p>
                    )}
                  </div>
                )}
                {run?.status === "ready" && draftsLoading && (
                  <div className="flex min-h-40 items-center justify-center text-xs text-muted-foreground">
                    <Loader2 className="mr-2 size-4 animate-spin" />
                    {t("knowledge.documentBlocks.loading")}
                  </div>
                )}
                {run?.status === "ready" &&
                  !draftsLoading &&
                  documentBlocks.length > 0 && (
                    <KnowledgeDocumentBlockReview
                      documents={documentBlocks}
                      generatedMemories={generatedMemories}
                      busy={busy}
                      insertProgress={insertProgress}
                      canRefine={run.can_refine}
                      insertedDocumentIds={run.inserted_document_ids || []}
                      onLoadContent={loadDocumentContent}
                      onSave={saveDocumentBlock}
                      onRefine={refineDocumentBlocks}
                      onInsert={insertDocumentBlocks}
                      onEditMemory={editGeneratedMemory}
                      onDeleteMemory={deleteGeneratedMemory}
                      onRestart={() => setRestartOpen(true)}
                    />
                  )}
                {!run && (
                  <div className="flex min-h-48 flex-col items-center justify-center text-center text-xs text-muted-foreground">
                    <Sparkles className="mb-2 size-8 text-primary/50" />
                    <p className="font-medium text-foreground">
                      {t("knowledge.documentBlocks.emptyTitle")}
                    </p>
                    <p className="mt-1 max-w-64 leading-5">
                      {t("knowledge.documentBlocks.emptyDescription")}
                    </p>
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-center border-t bg-background p-3">
              {isActive(run?.status) ? (
                <Button
                  variant="outline"
                  className="text-destructive"
                  onClick={() => void stopExtraction()}
                  disabled={busy}
                >
                  <Square className="mr-1 size-3.5 fill-current" />
                  {t("knowledge.parseWorkspace.stop")}
                </Button>
              ) : draftsLoading ? (
                <span className="text-xs text-muted-foreground">
                  {t("knowledge.documentBlocks.loading")}
                </span>
              ) : run?.status === "ready" && documentBlocks.length > 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("knowledge.documentBlocks.reviewHint")}
                </p>
              ) : (
                <Button
                  onClick={() => setPrepareOpen(true)}
                  disabled={
                    busy ||
                    !binding?.configured ||
                    detail.source_file_count === 0 ||
                    editingFile
                  }
                >
                  {busy ? (
                    <Loader2 className="mr-1 size-4 animate-spin" />
                  ) : (
                    <Sparkles className="mr-1 size-4" />
                  )}
                  {run?.status === "ready"
                    ? t("knowledge.memoryImport.extractAgain")
                    : t("knowledge.memoryImport.extract")}
                </Button>
              )}
            </div>
          </section>
        </>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("knowledge.uploadDialog.title")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.uploadDialog.description")}
            </DialogDescription>
          </DialogHeader>
          <Input
            value={newTitle}
            onChange={(event) => setNewTitle(event.target.value)}
            placeholder={t("knowledge.uploadDialog.placeholder")}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void createTopic()}
              disabled={busy || !newTitle.trim()}
            >
              {t("knowledge.createTopic")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={editingTopic}
        onOpenChange={(open) => {
          setEditingTopic(open)
          if (!open) setTopicTitleDraft(detail?.title || "")
        }}
      >
        <DialogContent
          data-testid="knowledge-topic-title-dialog"
          className="sm:max-w-md"
        >
          <DialogHeader>
            <DialogTitle>{t("knowledge.editTopicTitle")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.editTopicDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="knowledge-topic-title">
              {t("knowledge.topicTitle")}
            </Label>
            <Input
              id="knowledge-topic-title"
              value={topicTitleDraft}
              onChange={(event) => setTopicTitleDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && topicTitleDraft.trim())
                  void saveTopic()
              }}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setTopicTitleDraft(detail?.title || "")
                setEditingTopic(false)
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void saveTopic()}
              disabled={busy || !topicTitleDraft.trim()}
            >
              {busy ? (
                <Loader2 className="mr-1 size-4 animate-spin" />
              ) : (
                <Save className="mr-1 size-4" />
              )}
              {t("knowledge.saveTopic")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={prepareOpen} onOpenChange={setPrepareOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("knowledge.memoryPrepare.title")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.memoryPrepare.description")}
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            {t("knowledge.memoryPrepare.summary", {
              topic: detail?.title,
              model: binding?.model_name,
              count: detail?.source_file_count || 0,
            })}
          </div>
          <div className="space-y-2">
            <Label htmlFor="knowledge-extraction-instruction">
              {t("knowledge.memoryPrepare.instruction")}
            </Label>
            <Textarea
              id="knowledge-extraction-instruction"
              value={extractionInstruction}
              onChange={(event) => setExtractionInstruction(event.target.value)}
              placeholder={t("knowledge.memoryPrepare.placeholder")}
              maxLength={8000}
              className="min-h-40 resize-y"
            />
            <div className="flex items-start justify-between gap-3 text-xs text-muted-foreground">
              <p>{t("knowledge.memoryPrepare.hint")}</p>
              <span className="shrink-0 tabular-nums">
                {extractionInstruction.length}/8000
              </span>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPrepareOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void startExtraction()} disabled={busy}>
              {busy ? (
                <Loader2 className="mr-1 size-4 animate-spin" />
              ) : (
                <Sparkles className="mr-1 size-4" />
              )}
              {t("knowledge.memoryPrepare.start")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={restartOpen} onOpenChange={setRestartOpen}>
        <DialogContent
          data-testid="knowledge-restart-confirm-dialog"
          className="sm:max-w-md"
        >
          <DialogHeader>
            <DialogTitle>
              {t("knowledge.documentBlocks.restartTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("knowledge.documentBlocks.restartDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRestartOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => {
                setRestartOpen(false)
                setExtractionInstruction("")
                setPrepareOpen(true)
              }}
            >
              {t("knowledge.documentBlocks.restartConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={backgroundOpen}
        onOpenChange={(open) => {
          setBackgroundOpen(open)
          if (!open) setBackgroundDraft(detail?.background || "")
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("knowledge.background.title")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.background.description")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="knowledge-topic-background">
              {t("knowledge.background.label")}
            </Label>
            <Textarea
              id="knowledge-topic-background"
              value={backgroundDraft}
              onChange={(event) => setBackgroundDraft(event.target.value)}
              placeholder={t("knowledge.background.placeholder")}
              className="min-h-44 resize-y"
              maxLength={8000}
            />
            <div className="flex items-start justify-between gap-3 text-xs text-muted-foreground">
              <p>{t("knowledge.background.securityHint")}</p>
              <span className="shrink-0 tabular-nums">
                {backgroundDraft.length}/8000
              </span>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setBackgroundDraft(detail?.background || "")
                setBackgroundOpen(false)
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void saveBackground()} disabled={busy}>
              {busy ? (
                <Loader2 className="mr-1 size-4 animate-spin" />
              ) : (
                <Save className="mr-1 size-4" />
              )}
              {t("knowledge.background.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={bindingOpen} onOpenChange={setBindingOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("knowledge.binding.title")}</DialogTitle>
            <DialogDescription>
              {t("knowledge.memoryImport.bindingDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{t("knowledge.binding.provider")}</Label>
              <Select
                value={providerId}
                onValueChange={(value) => {
                  setProviderId(value)
                  setModelName("")
                }}
              >
                <SelectTrigger>
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
              <Label>{t("knowledge.binding.model")}</Label>
              <Select value={modelName} onValueChange={setModelName}>
                <SelectTrigger>
                  <SelectValue placeholder={t("knowledge.binding.model")} />
                </SelectTrigger>
                <SelectContent>
                  {models.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{t("knowledge.binding.limitPreset")}</Label>
              <Select
                value={selectedLimitPreset}
                onValueChange={(value) => {
                  if (value === "custom") {
                    setCustomLimitsSelected(true)
                    return
                  }
                  const preset = modelCapacityPresets.find(
                    (item) => item.id === value,
                  )
                  if (!preset) return
                  setCustomLimitsSelected(false)
                  setContextWindowTokens(preset.contextWindowTokens)
                  setMaxOutputTokens(preset.maxOutputTokens)
                }}
              >
                <SelectTrigger data-testid="knowledge-limit-preset">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {modelCapacityPresets.map((preset) => (
                    <SelectItem key={preset.id} value={preset.id}>
                      {formatTokenLimit(preset.contextWindowTokens)} /{" "}
                      {formatTokenLimit(preset.maxOutputTokens)}
                    </SelectItem>
                  ))}
                  <SelectItem value="custom">
                    {t("knowledge.binding.customLimit")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            {selectedLimitPreset === "custom" && (
              <div className="grid grid-cols-2 gap-3 rounded-lg border bg-muted/20 p-3">
                <div className="space-y-2">
                  <Label>{t("knowledge.binding.contextWindow")}</Label>
                  <Input
                    type="number"
                    min={4_096}
                    max={2_000_000}
                    value={contextWindowTokens}
                    onChange={(event) =>
                      setContextWindowTokens(Number(event.target.value))
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("knowledge.binding.maxOutput")}</Label>
                  <Input
                    type="number"
                    min={256}
                    max={256_000}
                    value={maxOutputTokens}
                    onChange={(event) =>
                      setMaxOutputTokens(Number(event.target.value))
                    }
                  />
                </div>
              </div>
            )}
            <p className="text-xs leading-5 text-muted-foreground">
              {t("knowledge.binding.limitHint")}
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBindingOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void saveBinding()}
              disabled={
                busy || !providerId || !modelName || bindingLimitsInvalid
              }
            >
              {t("knowledge.binding.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
