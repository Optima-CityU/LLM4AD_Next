import Editor, { loader } from "@monaco-editor/react"
import {
  useIsFetching,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpToLine,
  Ban,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  CircleAlert,
  Circle as CircleIcon,
  CornerDownLeft,
  File as FileIcon,
  Hash,
  Layers,
  Loader2,
  Maximize2,
  Minimize2,
  Monitor,
  Play,
  RefreshCw,
  Send,
  Settings2,
  Sparkles,
  Square,
  Upload,
  User,
  X,
} from "lucide-react"
import * as monaco from "monaco-editor"
import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

loader.config({ monaco })

import type {
  ChatTuneActiveStage,
  ChatTuneMessageItem,
  ChatTuneSessionItem,
  ChatTuneStageStatus,
  ChatTuneTurnStatus,
  ProviderResponse,
  TaskResponse,
} from "@/client"
import { Llm4AdChatTuneService, Llm4AdTasksService, UtilsService } from "@/client"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import { useTheme } from "@/components/theme-provider"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useChatTuneStream } from "@/hooks/useChatTuneStream"
import { useCopyBeforeRun } from "@/hooks/useCopyBeforeRun"
import useCustomToast from "@/hooks/useCustomToast"
import { useEvolution } from "@/hooks/useEvolution"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { INPUT_LIMITS } from "@/lib/inputLimits"
import { setTaskStatusInCache } from "@/lib/task-queries"
import { cn, handleComposerEnter } from "@/lib/utils"
import { handleError } from "@/utils"
import type {
  AssistantCard,
  CardSubmission,
  ChatStageKey,
  ChoiceOption,
  MultiChoiceCard,
  NumberFormCard,
  TextFormCard,
} from "./chat-tune-mock"
import StorageUsageBadge from "./StorageUsageBadge"
import EvolutionProviderSelect from "./steps/EvolutionProviderSelect"
import FileTreeView from "./steps/FileTreeView"

const DISPLAY_STAGES: ChatStageKey[] = ["data", "evolveAnnotation", "evaluator"]

/** AI build pipeline steps shown in the left progress panel. */
const BUILD_STEPS = ["gathering", "build", "review"] as const
type BuildStepKey = (typeof BUILD_STEPS)[number]
interface BuildStep {
  key: BuildStepKey
  state: ChatTuneStageStatus
  isActive: boolean
}

interface StageSnapshot {
  active: ChatTuneActiveStage | null
  gathering: ChatTuneStageStatus
  build: ChatTuneStageStatus
  review: ChatTuneStageStatus
}

const INITIAL_STAGE_SNAPSHOT: StageSnapshot = {
  active: null,
  gathering: "not_started",
  build: "not_started",
  review: "not_started",
}

function snapshotFromSession(session: ChatTuneSessionItem): StageSnapshot {
  return {
    active: session.active_stage ?? null,
    gathering: session.gathering_status ?? "not_started",
    build: session.build_status ?? "not_started",
    review: session.review_status ?? "not_started",
  }
}

const MAX_FILE_BYTES = 50 * 1024 * 1024
const MAX_TOTAL_BYTES = 100 * 1024 * 1024
const MAX_FILE_COUNT = 100
const MAX_SELECTION_COUNT = 1000
const MAX_FILE_MB = MAX_FILE_BYTES / 1024 / 1024
const MAX_TOTAL_MB = MAX_TOTAL_BYTES / 1024 / 1024

/* ───────────────── Internal message type ──────────────── */

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

const SPECIAL_PROVIDERS = ["", "default", "mock"] as const
function isSpecialProvider(v: string): boolean {
  return SPECIAL_PROVIDERS.includes(v as (typeof SPECIAL_PROVIDERS)[number])
}

interface ChatMessage {
  id: string
  turnId?: string
  role: "user" | "assistant" | "system"
  text?: string
  card?: AssistantCard
  submission?: CardSubmission
  turnStatus?: ChatTuneTurnStatus
  error?: string | null
  createdAt: number
}

/* ─────────────────────────────────────────────────────── */

interface ChatTuneViewProps {
  task: TaskResponse
  readOnly?: boolean
  onSwitchToStepper?: () => void
}

export default function ChatTuneView({
  task,
  readOnly = false,
  onSwitchToStepper,
}: ChatTuneViewProps) {
  const { t, i18n } = useTranslation()
  const {
    setIsConfiguring,
    setIsViewingParams,
    isViewingAiBuildHistory,
    setIsViewingAiBuildHistory,
  } = useEvolution()
  const queryClient = useQueryClient()
  const stream = useChatTuneStream()
  // Inject the highlight.js theme so fenced code blocks are colorized.
  useHljsTheme()

  const PAGE_SIZE = 50

  /* ──────────── Provider & model ──────────── */

  const { data: providersData } = useProviders()
  const { data: defaultModels } = useUserDefaultModels()
  const providerList = providersData?.items ?? []
  const otherHint = [
    defaultModels?.other_provider_name,
    defaultModels?.other_model_name,
  ]
    .filter(Boolean)
    .join(", ")

  const [providerId, setProviderId] = useState("default")
  const [modelName, setModelName] = useState("")
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false)

  // Feature flags (e.g. whether the AI build Beta entry is enabled). Cached
  // long — flags only change on backend redeploy.
  const { data: _featureFlags } = useQuery({
    queryKey: ["featureFlags"],
    queryFn: () => UtilsService.featureFlags(),
    staleTime: 5 * 60 * 1000,
  })

  const selectedProvider = providerList.find((p) => p.id === providerId)
  const availableModels = useMemo(
    () => (selectedProvider ? parseModels(selectedProvider.model) : []),
    [selectedProvider],
  )

  const handleProviderChange = useCallback(
    (id: string) => {
      setProviderId(id)
      if (isSpecialProvider(id)) {
        setModelName("")
      } else {
        const p = providerList.find((pr) => pr.id === id)
        const models = p ? parseModels(p.model) : []
        setModelName(models.length === 1 ? models[0] : "")
      }
    },
    [providerList],
  )

  /* ──────────── Session query ──────────── */

  const {
    data: sessionDetail,
    isLoading: isSessionLoading,
    refetch: refetchSession,
  } = useQuery({
    queryKey: ["chatTuneSession", task.id],
    queryFn: () =>
      Llm4AdChatTuneService.getSession({ taskId: task.id, limit: PAGE_SIZE }),
  })

  /* ──────────── Local state ──────────── */

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessionInited, setSessionInited] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [freeText, setFreeText] = useState("")
  const [composerFocused, setComposerFocused] = useState(false)
  const [composerExpanded, setComposerExpanded] = useState(false)
  const [composerHighlight, setComposerHighlight] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [isSending, setIsSending] = useState(false)
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null)
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const needScrollToBottom = useRef(true)
  const streamContentRef = useRef("")
  const streamRafId = useRef(0)
  // Flipped to false in the unmount cleanup so any in-flight async handler
  // (handleSend/handleRetry) can skip its post-await state updates and avoid
  // opening an orphan SSE connection on a component that is no longer mounted.
  const mountedRef = useRef(true)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [showScrollTopBtn, setShowScrollTopBtn] = useState(false)
  const [compactMode, setCompactMode] = useState(false)

  // Authoritative source = session response; stream pushes override locally
  // until the next refetch reconciles. Build status is the trigger for some
  // downstream refreshes (file tree).
  const [stageSnapshot, setStageSnapshot] = useState<StageSnapshot>(
    INITIAL_STAGE_SNAPSHOT,
  )
  const lastBuildStatusRef = useRef<ChatTuneStageStatus>("not_started")

  /* ──────────── Sync messages + reconnect active turn ──────────── */

  // Re-sync from sessionDetail every time it changes — not just on first
  // load. When the user toggles to manual build and back while a turn is in
  // flight, react-query returns the stale cached session synchronously, then
  // refetches in the background. Without this re-sync, the second update
  // (with the new turn's messages and active_turn_id) was dropped on the
  // floor because `sessionInited` was already true, leaving the just-sent
  // messages invisible and the SSE stream unreconnected.
  useEffect(() => {
    if (!sessionDetail?.messages) return
    const fresh = sessionDetail.messages.map(mapApiMessageToChatMessage)
    setMessages((prev) => {
      if (!streamingMsgId) return fresh
      // Preserve any locally-only state for the message we're actively
      // streaming. The server view may still be empty for an in-flight turn:
      // - `text` accumulates via onChunk
      // - `card` is injected via onPayload
      // Without this, an unrelated refetch landing mid-stream would blank out
      // either of them until the next chunk arrives.
      const localStreaming = prev.find((m) => m.id === streamingMsgId)
      if (!localStreaming) return fresh
      return fresh.map((m) =>
        m.id === streamingMsgId
          ? {
              ...m,
              text: localStreaming.text ?? m.text,
              card: localStreaming.card ?? m.card,
            }
          : m,
      )
    })
    setHasMore(!!sessionDetail.has_more)
    if (!sessionInited) {
      setSessionInited(true)
      needScrollToBottom.current = true
    }

    // Reconnect to any active turn surfaced by the latest payload. Search
    // `fresh` (not the closure `messages`) — the setMessages above is async,
    // so the closure still holds the prior, possibly-stale set.
    const activeTurn = sessionDetail.session.active_turn_id ?? null
    if (!activeTurn || activeTurn === activeTurnId || stream.isStreaming)
      return
    const generatingMsg = fresh.find(
      (m) =>
        m.role === "assistant" &&
        m.turnId === activeTurn &&
        m.turnStatus === "generating",
    )
    if (!generatingMsg) return
    setActiveTurnId(activeTurn)
    setStreamingMsgId(generatingMsg.id)
    connectToStream(task.id, activeTurn, generatingMsg.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionDetail])

  /* ──────────── Auto-scroll ──────────── */

  useEffect(() => {
    if (needScrollToBottom.current && messages.length > 0) {
      needScrollToBottom.current = false
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "instant" })
      })
    }
  }, [messages])

  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const onScroll = () => {
      const gap = el.scrollHeight - el.scrollTop - el.clientHeight
      setShowScrollBtn(gap > 120 && messages.length > 0)
      setShowScrollTopBtn(el.scrollTop > 200 && messages.length > 0)
    }
    el.addEventListener("scroll", onScroll, { passive: true })
    return () => el.removeEventListener("scroll", onScroll)
  }, [messages.length])

  /* ──────────── Cleanup on unmount ──────────── */

  useEffect(() => {
    // Reset on every mount (not just rely on the useRef initial value):
    // React StrictMode mounts → cleans up → mounts again, and the second
    // mount keeps the `false` written by the first cleanup unless we
    // explicitly flip it back here.
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      stream.disconnect()
      cancelAnimationFrame(streamRafId.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ──────────── Sync stage statuses from session ──────────── */

  useEffect(() => {
    if (!sessionDetail?.session) return
    const next = snapshotFromSession(sessionDetail.session)
    setStageSnapshot(next)
    // First time we observe build transitioning into completed, refresh the
    // generated file list — every subsequent review turn already invalidates
    // the tree query, so don't duplicate the work.
    if (
      next.build === "completed" &&
      lastBuildStatusRef.current !== "completed"
    ) {
      queryClient.invalidateQueries({ queryKey: ["taskDataTree", task.id] })
    }
    lastBuildStatusRef.current = next.build
  }, [sessionDetail, task.id, queryClient])

  /* ──────────── Stream connection ──────────── */

  const connectToStream = useCallback(
    (taskId: string, turnId: string, assistantMessageId: string) => {
      streamContentRef.current = ""

      const scheduleFlush = () => {
        cancelAnimationFrame(streamRafId.current)
        streamRafId.current = requestAnimationFrame(() => {
          const content = streamContentRef.current
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? { ...m, text: content || undefined }
                : m,
            ),
          )
          messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
        })
      }

      stream.connect(taskId, turnId, {
        onChunk(content) {
          streamContentRef.current += content
          scheduleFlush()
        },
        onPayload(payload) {
          const card = parsePayloadToCard(payload)
          if (card) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId ? { ...m, card } : m,
              ),
            )
          }
        },
        onStageUpdate(update) {
          setStageSnapshot((prev) => {
            const next: StageSnapshot = {
              active:
                update.active_stage !== undefined
                  ? update.active_stage
                  : prev.active,
              gathering: update.gathering_status ?? prev.gathering,
              build: update.build_status ?? prev.build,
              review: update.review_status ?? prev.review,
            }
            if (
              next.build === "completed" &&
              lastBuildStatusRef.current !== "completed"
            ) {
              queryClient.invalidateQueries({
                queryKey: ["taskDataTree", taskId],
              })
            }
            lastBuildStatusRef.current = next.build
            return next
          })
        },
        onDone() {
          cancelAnimationFrame(streamRafId.current)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? {
                    ...m,
                    turnStatus: "completed",
                    text: streamContentRef.current || m.text,
                  }
                : m,
            ),
          )
          setStreamingMsgId(null)
          setActiveTurnId(null)
          needScrollToBottom.current = true
          refetchSession()
          // Refresh task info every turn so any side-effects (status changes,
          // generated files) propagate without waiting for SSE.
          queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
          if (lastBuildStatusRef.current === "completed") {
            queryClient.invalidateQueries({
              queryKey: ["taskDataTree", taskId],
            })
          }
        },
        onCancelled() {
          cancelAnimationFrame(streamRafId.current)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? {
                    ...m,
                    turnStatus: "cancelled",
                    text: streamContentRef.current || m.text,
                  }
                : m,
            ),
          )
          setStreamingMsgId(null)
          setActiveTurnId(null)
        },
        onError(error) {
          cancelAnimationFrame(streamRafId.current)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? {
                    ...m,
                    turnStatus: "failed",
                    error,
                    text: streamContentRef.current || m.text,
                  }
                : m,
            ),
          )
          setStreamingMsgId(null)
          setActiveTurnId(null)
        },
      })
    },
    [stream, refetchSession, queryClient],
  )

  /* ──────────── Load older messages ──────────── */

  const loadOlderMessages = useCallback(async () => {
    if (isLoadingMore || !hasMore || messages.length === 0) return
    setIsLoadingMore(true)
    const scroller = scrollerRef.current
    const prevScrollHeight = scroller?.scrollHeight ?? 0
    const prevScrollTop = scroller?.scrollTop ?? 0
    try {
      const oldestId = messages[0].id
      const resp = await queryClient.fetchQuery({
        queryKey: ["chatTuneSession", task.id, "before", oldestId],
        queryFn: () =>
          Llm4AdChatTuneService.getSession({
            taskId: task.id,
            before: oldestId,
            limit: PAGE_SIZE,
          }),
        staleTime: 0,
      })
      const olderMessages = (resp.messages ?? []).map(
        mapApiMessageToChatMessage,
      )
      setHasMore(!!resp.has_more)
      if (olderMessages.length > 0) {
        setMessages((prev) => [...olderMessages, ...prev])
        requestAnimationFrame(() => {
          if (scroller) {
            const newScrollHeight = scroller.scrollHeight
            scroller.scrollTop =
              prevScrollTop + (newScrollHeight - prevScrollHeight)
          }
        })
      }
    } finally {
      setIsLoadingMore(false)
    }
  }, [isLoadingMore, hasMore, messages, task.id, queryClient])

  /* ──────────── Send message / submit card ──────────── */

  const handleSend = useCallback(
    async (
      content?: string,
      respondToMessageId?: string,
      submission?: CardSubmission,
    ) => {
      if (readOnly || isSending || stream.isStreaming) return
      setIsSending(true)
      try {
        const resp = await Llm4AdChatTuneService.startTurn({
          taskId: task.id,
          requestBody: {
            content: content || undefined,
            respond_to_message_id: respondToMessageId || undefined,
            submission: submission
              ? (submission as unknown as Record<string, unknown>)
              : undefined,
            provider_id: providerId || undefined,
            model_name: modelName || undefined,
            language: i18n.language?.startsWith("zh") ? "zh" : "en",
          },
        })
        // The backend has accepted the turn; if the user has since switched
        // tasks/versions/modes, bail out so we don't open an SSE that nobody
        // owns. The next mount will pick the turn back up via the session
        // refetch -> reconnect path.
        if (!mountedRef.current) return

        const userMsg = mapApiMessageToChatMessage(resp.user_message)
        const assistantMsg = mapApiMessageToChatMessage(resp.assistant_message)

        setMessages((prev) => {
          let updated = [...prev]
          if (resp.locked_message) {
            const lockedMsg = mapApiMessageToChatMessage(resp.locked_message)
            updated = updated.map((m) =>
              m.id === resp.locked_message!.id ? lockedMsg : m,
            )
          }
          return [...updated, userMsg, assistantMsg]
        })

        needScrollToBottom.current = true
        setActiveTurnId(resp.turn_id)
        setStreamingMsgId(resp.assistant_message.id)
        connectToStream(task.id, resp.turn_id, resp.assistant_message.id)
      } catch (err: unknown) {
        if (!mountedRef.current) return
        const status = (err as { status?: number })?.status
        const body = (err as { body?: { detail?: string } })?.body
        if (status === 409) {
          toast.error(t("evolution.chatTune.error409"))
          refetchSession()
        } else if (status === 429) {
          toast.error(t("evolution.chatTune.error429"))
        } else if (status === 400 || status === 422) {
          toast.error(body?.detail || t("evolution.chatTune.errorValidation"))
        } else if (status === 404) {
          toast.error(t("evolution.chatTune.error404"))
          refetchSession()
        } else {
          toast.error(t("evolution.chatTune.errorNetwork"))
        }
      } finally {
        setIsSending(false)
      }
    },
    [
      readOnly,
      isSending,
      stream.isStreaming,
      task.id,
      providerId,
      modelName,
      connectToStream,
      refetchSession,
      i18n.language,
      t,
    ],
  )

  const handleCardSubmit = useCallback(
    (_card: AssistantCard, messageId: string, submission: CardSubmission) => {
      if (readOnly) return
      const userText = formatUserSubmissionText(submission)
      handleSend(userText, messageId, submission)
    },
    [handleSend, readOnly],
  )

  const handleSendFreeText = useCallback(() => {
    const text = freeText.trim()
    if (!text || readOnly) return
    setFreeText("")
    setComposerExpanded(false)
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
    handleSend(text)
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
    })
  }, [freeText, handleSend, readOnly])

  const handlePayloadUpdate = useCallback(
    (messageId: string, payload: Record<string, unknown>) => {
      const card = parsePayloadToCard(payload)
      if (card) {
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, card } : m)),
        )
      }
    },
    [],
  )

  const composerHighlightTimer = useRef<ReturnType<typeof setTimeout>>(null)

  const handleFocusComposer = useCallback(() => {
    textareaRef.current?.focus()
    setComposerHighlight(true)
    if (composerHighlightTimer.current) {
      clearTimeout(composerHighlightTimer.current)
    }
    composerHighlightTimer.current = setTimeout(() => {
      setComposerHighlight(false)
    }, 2000)
  }, [])

  const autoResize = useCallback(() => {
    const el = textareaRef.current
    if (!el || composerExpanded) return
    el.style.height = "auto"
    const maxH = 200
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`
    el.style.overflowY = el.scrollHeight > maxH ? "auto" : "hidden"
  }, [composerExpanded])

  useEffect(() => {
    autoResize()
  }, [autoResize])

  /* ──────────── Stop generation ──────────── */

  const handleStop = useCallback(async () => {
    if (!activeTurnId) return
    try {
      await Llm4AdChatTuneService.stopTurn({
        taskId: task.id,
        turnId: activeTurnId,
      })
    } catch {
      // SSE will handle the state transition
    }
  }, [activeTurnId, task.id])

  /* ──────────── Retry failed turn ──────────── */

  const handleRetry = useCallback(
    async (turnId: string) => {
      if (readOnly || isSending || stream.isStreaming) return
      try {
        const resp = await Llm4AdChatTuneService.retryTurn({
          taskId: task.id,
          turnId,
          requestBody: {
            provider_id: providerId || undefined,
            model_name: modelName || undefined,
          },
        })
        if (!mountedRef.current) return
        const newMsg = mapApiMessageToChatMessage(resp.assistant_message)
        setMessages((prev) =>
          prev.map((m) => (m.id === resp.assistant_message.id ? newMsg : m)),
        )
        setActiveTurnId(resp.turn_id)
        setStreamingMsgId(resp.assistant_message.id)
        connectToStream(task.id, resp.turn_id, resp.assistant_message.id)
      } catch (err: unknown) {
        if (!mountedRef.current) return
        const status = (err as { status?: number })?.status
        const body = (err as { body?: { detail?: string } })?.body
        if (status === 409) {
          toast.error(t("evolution.chatTune.error409"))
          refetchSession()
        } else if (status === 429) {
          toast.error(t("evolution.chatTune.error429"))
        } else if (status === 400 || status === 422) {
          toast.error(body?.detail || t("evolution.chatTune.errorValidation"))
        } else if (status === 404) {
          toast.error(t("evolution.chatTune.error404"))
          refetchSession()
        } else {
          toast.error(t("evolution.chatTune.errorNetwork"))
        }
      }
    },
    [
      readOnly,
      isSending,
      stream.isStreaming,
      task.id,
      providerId,
      modelName,
      connectToStream,
      refetchSession,
      t,
    ],
  )

  /* ──────────── Reset session ──────────── */

  const handleReset = useCallback(async () => {
    stream.disconnect()
    try {
      await Llm4AdChatTuneService.resetSession({ taskId: task.id })
      setMessages([])
      setStreamingMsgId(null)
      setActiveTurnId(null)
      setSessionInited(false)
      setHasMore(false)
      toast.success(t("evolution.chatTune.resetSuccess"))
      queryClient.removeQueries({
        queryKey: ["chatTuneSession", task.id],
      })
      refetchSession()
    } catch {
      toast.error(t("evolution.chatTune.errorNetwork"))
    }
  }, [stream, task.id, queryClient, refetchSession, t])

  /* ──────────── Derived state ──────────── */

  const activeCardMessage = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.role === "assistant" && m.card && !m.submission) return m
    }
    return null
  }, [messages])

  const activeStage: ChatStageKey | null =
    activeCardMessage?.card?.stage ?? null

  const buildCompleted = stageSnapshot.build === "completed"
  const buildSteps = useMemo<BuildStep[]>(
    () =>
      BUILD_STEPS.map((key) => ({
        key,
        state: stageSnapshot[key],
        isActive: stageSnapshot.active === key,
      })),
    [stageSnapshot],
  )

  const isGenerating = stream.isStreaming || !!streamingMsgId
  const canSend = !readOnly && !isSending && !isGenerating

  // Ensure the streaming assistant card (and the matching user message of the
  // active turn) always render below all historical messages, regardless of
  // how the server ordered them or how local insertions interleaved.
  const orderedMessages = useMemo(() => {
    if (!streamingMsgId && !activeTurnId) return messages
    const tail: ChatMessage[] = []
    const head: ChatMessage[] = []
    for (const m of messages) {
      const isStreamingMsg = m.id === streamingMsgId
      const isActiveTurnMsg = !!activeTurnId && m.turnId === activeTurnId
      if (isStreamingMsg || isActiveTurnMsg) tail.push(m)
      else head.push(m)
    }
    if (tail.length === 0) return messages
    // Within the tail, keep the streaming assistant message last so the live
    // output sits beneath its companion user message.
    tail.sort((a, b) => {
      if (a.id === streamingMsgId) return 1
      if (b.id === streamingMsgId) return -1
      return a.createdAt - b.createdAt
    })
    return [...head, ...tail]
  }, [messages, streamingMsgId, activeTurnId])

  /* ──────────── Render ──────────── */

  return (
    <div className="flex flex-col h-full">
      <OnboardingTour
        tourId="evolution-ai-build"
        enabled={!readOnly}
        startDelay={500}
        steps={[
          {
            selector: '[data-tour="ai-composer"]',
            title: t("tour.aiBuild.composerTitle"),
            content: t("tour.aiBuild.composerContent"),
            placement: "top",
          },
          {
            selector: '[data-tour="ai-preview"]',
            title: t("tour.aiBuild.previewTitle"),
            content: t("tour.aiBuild.previewContent"),
            placement: "right",
          },
          {
            selector: '[data-tour="ai-run"]',
            title: t("tour.aiBuild.runTitle"),
            content: t("tour.aiBuild.runContent"),
            placement: "top",
          },
        ]}
      />
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-1 pb-2 border-b mb-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <span
            className="flex items-center justify-center size-7 rounded-lg shrink-0 shadow-[0_0_12px] shadow-primary/30"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in srgb, var(--primary) 25%, transparent), color-mix(in srgb, #6366f1 25%, transparent))",
              border:
                "1px solid color-mix(in srgb, var(--primary) 40%, transparent)",
            }}
          >
            <Sparkles className="size-3.5 text-primary" />
          </span>
          <div className="flex flex-col min-w-0">
            <span
              className={cn(
                "text-sm font-semibold leading-tight truncate",
                readOnly
                  ? "text-amber-600 dark:text-amber-400"
                  : "text-foreground",
              )}
            >
              {readOnly
                ? t("evolution.chatTune.headerReadOnlyTitle")
                : t("evolution.chatTune.headerTitle")}
            </span>
            <span className="text-[11px] text-muted-foreground leading-tight truncate">
              {readOnly
                ? t("evolution.chatTune.headerReadOnlyHint")
                : t("evolution.chatTune.headerHint")}
            </span>
          </div>
          {readOnly && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/10 border border-amber-500/30 text-xs font-medium text-amber-600 dark:text-amber-400 shrink-0">
              {t("evolution.chatTune.readOnlyBadge")}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {task.storage_usage && (
            <StorageUsageBadge storage={task.storage_usage} />
          )}
          {onSwitchToStepper && (
            <div
              role="tablist"
              aria-label={t("evolution.chatTune.modeSwitchLabel")}
              className="inline-flex items-center gap-0.5 p-0.5 rounded-md border border-primary/40 bg-primary/5 shadow-sm"
            >
              <button
                type="button"
                role="tab"
                aria-selected="true"
                className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium bg-primary text-primary-foreground shadow-sm"
              >
                <Sparkles className="size-3.5" />
                <span>{t("evolution.chatTune.modeAi")}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected="false"
                onClick={onSwitchToStepper}
                className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
                title={t("evolution.chatTune.switchStepperTitle")}
              >
                <Layers className="size-3.5" />
                <span>{t("evolution.chatTune.modeManual")}</span>
              </button>
            </div>
          )}
          {!(task.status === "uninitialized" && !readOnly) && (
            <button
              type="button"
              onClick={() => {
                if (isViewingAiBuildHistory) setIsViewingAiBuildHistory(false)
                else if (readOnly) setIsViewingParams(false)
                else setIsConfiguring(false)
              }}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
              title={t("evolution.exitConfig")}
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      </div>

      {/* Body: preview (left, 1/3) + chat (right, 2/3) */}
      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-4">
        {/* Preview column */}
        <PreviewPanel
          task={task}
          steps={buildSteps}
          isGenerating={isGenerating}
          buildCompleted={buildCompleted}
          readOnly={readOnly}
        />

        {/* Chat column */}
        <div className="min-h-0 flex flex-col rounded-xl border border-border/60 bg-linear-to-b from-card/80 to-card overflow-hidden">
          <div className="relative flex-1 min-h-0">
            <div
              ref={scrollerRef}
              className="absolute inset-0 overflow-y-auto px-4 py-4 space-y-4"
            >
              {messages.length === 0 && !isSessionLoading && (
                <WelcomeScreen t={t} />
              )}
              {isSessionLoading && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="size-5 animate-spin text-muted-foreground" />
                </div>
              )}
              {hasMore && !isSessionLoading && (
                <div className="flex justify-center pb-2">
                  <button
                    type="button"
                    disabled={isLoadingMore}
                    onClick={loadOlderMessages}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                    text-muted-foreground hover:text-foreground bg-muted/60 hover:bg-muted
                    border border-border/50 transition-colors disabled:opacity-50"
                  >
                    {isLoadingMore ? (
                      <>
                        <Loader2 className="size-3 animate-spin" />
                        <span>{t("evolution.chatTune.loadingMore")}</span>
                      </>
                    ) : (
                      <span>{t("evolution.chatTune.loadMore")}</span>
                    )}
                  </button>
                </div>
              )}
              {orderedMessages.map((msg) => (
                <ChatMessageRow
                  key={msg.id}
                  message={msg}
                  disabled={readOnly || !!msg.submission || isGenerating}
                  isStreaming={msg.id === streamingMsgId}
                  compactMode={compactMode}
                  taskId={task.id}
                  onSubmit={handleCardSubmit}
                  onPayloadUpdate={handlePayloadUpdate}
                  onFocusComposer={handleFocusComposer}
                  onRetry={
                    (msg.turnStatus === "failed" ||
                      msg.turnStatus === "cancelled") &&
                    msg.turnId
                      ? () => handleRetry(msg.turnId!)
                      : undefined
                  }
                />
              ))}
              {isSending && !streamingMsgId && <TypingIndicator />}
              <div style={{ height: isGenerating ? 120 : 0 }} />
              <div ref={messagesEndRef} />
            </div>
            <div className="absolute top-0 left-0 right-0 h-6 bg-gradient-to-b from-card to-transparent pointer-events-none z-10" />
            {/* Display mode toggle */}
            <button
              type="button"
              onClick={() => setCompactMode((v) => !v)}
              className="absolute top-2 right-3 z-20 size-7 flex items-center justify-center rounded-lg
                bg-card/80 backdrop-blur-sm border border-border/40 shadow-sm
                text-muted-foreground/60 hover:text-foreground hover:bg-card hover:border-primary/30
                hover:shadow-md transition-all duration-200"
              title={
                compactMode
                  ? t("evolution.chatTune.normalMode")
                  : t("evolution.chatTune.compactMode")
              }
            >
              {compactMode ? (
                <Maximize2 className="size-3.5" />
              ) : (
                <Minimize2 className="size-3.5" />
              )}
            </button>
            <div className="absolute bottom-3 right-3 z-20 flex flex-col gap-1.5">
              <button
                type="button"
                disabled={!showScrollTopBtn}
                onClick={() =>
                  scrollerRef.current?.scrollTo({ top: 0, behavior: "smooth" })
                }
                className="size-8 flex items-center justify-center rounded-full
                  bg-card/90 backdrop-blur-sm border border-border/50 shadow-md
                  text-muted-foreground/70 hover:text-foreground hover:bg-card
                  hover:shadow-lg hover:border-primary/30
                  disabled:opacity-30 disabled:pointer-events-none
                  transition-all duration-200"
                title={t("evolution.chatTune.scrollToTop")}
              >
                <ArrowUpToLine className="size-3.5" />
              </button>
              <button
                type="button"
                disabled={!showScrollBtn}
                onClick={() =>
                  messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
                }
                className="size-8 flex items-center justify-center rounded-full
                  bg-card/90 backdrop-blur-sm border border-border/50 shadow-md
                  text-muted-foreground/70 hover:text-foreground hover:bg-card
                  hover:shadow-lg hover:border-primary/30
                  disabled:opacity-30 disabled:pointer-events-none
                  transition-all duration-200"
                title={t("evolution.chatTune.scrollToBottom")}
              >
                <ArrowDownToLine className="size-3.5" />
              </button>
            </div>
          </div>

          {/* Composer */}
          <div
            className="shrink-0 px-3 pb-3 pt-2"
            style={{
              background:
                "linear-gradient(to top, color-mix(in srgb, var(--primary) 4%, var(--card)) 0%, transparent 100%)",
            }}
          >
            <div
              data-tour="ai-composer"
              className={cn(
                "relative rounded-2xl border-2 backdrop-blur-md transition-all duration-200 ease-out",
                readOnly
                  ? "border-border/20 bg-card/80 opacity-60"
                  : composerHighlight
                    ? "shadow-xl shadow-primary/25 border-primary ring-2 ring-primary/30 bg-card animate-pulse"
                    : composerFocused
                      ? "shadow-xl shadow-primary/15 border-primary/40 ring-1 ring-primary/20 bg-card"
                      : "shadow-lg shadow-primary/8 border-primary/20 bg-card/95 hover:border-primary/30 hover:shadow-xl hover:shadow-primary/12",
              )}
            >
              {/* Top bar: provider chip */}
              {!readOnly && (
                <div className="flex items-center justify-between px-4 pt-2.5 pb-0.5">
                  <Popover
                    open={modelSettingsOpen}
                    onOpenChange={setModelSettingsOpen}
                  >
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className={cn(
                          "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-all",
                          providerId && providerId !== "default"
                            ? "bg-primary/10 text-primary border border-primary/25 shadow-sm shadow-primary/10"
                            : "bg-muted/60 text-muted-foreground border border-border/40 hover:border-primary/30 hover:text-foreground",
                        )}
                      >
                        <Settings2 className="size-3" />
                        <span className="max-w-[180px] truncate">
                          {providerId && providerId !== "default"
                            ? (providerList.find((p) => p.id === providerId)
                                ?.name ?? providerId) +
                              (modelName ? ` / ${modelName}` : "")
                            : t("evolution.chatTune.defaultProvider")}
                        </span>
                        <ChevronDown
                          className={cn(
                            "size-3 opacity-50 transition-transform",
                            modelSettingsOpen && "rotate-180",
                          )}
                        />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      className="w-[420px] p-3"
                      align="start"
                      side="top"
                      sideOffset={8}
                    >
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5 px-0.5">
                            {t("evolution.chatTune.selectProvider")}
                          </p>
                          <ChatTuneProviderSelect
                            value={providerId}
                            onChange={handleProviderChange}
                            providers={providerList}
                            defaultHint={otherHint}
                            t={t}
                          />
                        </div>
                        <div>
                          <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5 px-0.5">
                            Model
                          </p>
                          <ChatTuneModelInput
                            value={
                              isSpecialProvider(providerId) ? "" : modelName
                            }
                            onChange={setModelName}
                            suggestions={availableModels}
                            disabled={isSpecialProvider(providerId)}
                            placeholder={
                              isSpecialProvider(providerId)
                                ? t("evolution.chatTune.modelNotNeeded")
                                : t("evolution.chatTune.modelPlaceholder")
                            }
                          />
                        </div>
                      </div>
                    </PopoverContent>
                  </Popover>
                  <div className="flex items-center gap-1.5">
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <button
                          type="button"
                          className="size-6 flex items-center justify-center rounded-md text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 transition-colors"
                          title={t("evolution.chatTune.resetSession")}
                        >
                          <RefreshCw className="size-3" />
                        </button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>
                            {t("evolution.chatTune.resetConfirmTitle")}
                          </AlertDialogTitle>
                          <AlertDialogDescription>
                            {t("evolution.chatTune.resetConfirmMessage")}
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>
                            {t("evolution.chatTune.resetConfirmCancel")}
                          </AlertDialogCancel>
                          <AlertDialogAction
                            onClick={handleReset}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                          >
                            {t("evolution.chatTune.resetConfirmOk")}
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                </div>
              )}

              {/* Textarea */}
              <div className="px-4 pt-2 pb-1">
                <textarea
                  ref={textareaRef}
                  value={freeText}
                  maxLength={INPUT_LIMITS.chatMessage}
                  onChange={(e) => {
                    setFreeText(e.target.value)
                    if (composerHighlight) setComposerHighlight(false)
                  }}
                  onFocus={() => setComposerFocused(true)}
                  onBlur={() => setComposerFocused(false)}
                  onKeyDown={(e) =>
                    handleComposerEnter(
                      e,
                      freeText,
                      setFreeText,
                      handleSendFreeText,
                    )
                  }
                  placeholder={
                    readOnly
                      ? t("evolution.chatTune.composerReadOnly")
                      : composerHighlight
                        ? t("evolution.chatTune.composerCustomHint")
                        : isGenerating
                          ? t("evolution.chatTune.composerGenerating")
                          : activeStage
                            ? t("evolution.chatTune.composerActive")
                            : t("evolution.chatTune.composerIdle")
                  }
                  disabled={readOnly || isGenerating}
                  className={cn(
                    "w-full resize-none border-0 bg-transparent text-sm leading-relaxed",
                    "placeholder:text-muted-foreground/60 focus:outline-none focus-visible:ring-0",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    "transition-[height] duration-150 ease-out",
                    composerExpanded
                      ? "min-h-[200px] overflow-y-auto"
                      : "min-h-[40px] overflow-y-hidden",
                  )}
                  style={composerExpanded ? { height: 200 } : undefined}
                />
              </div>

              {/* Bottom toolbar */}
              <div className="flex items-center justify-between px-3 pb-2.5">
                {/* Left: expand + char count */}
                <div className="flex items-center gap-1.5">
                  {!readOnly && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => setComposerExpanded((v) => !v)}
                          className="size-7 flex items-center justify-center rounded-lg text-muted-foreground/50 hover:text-foreground/70 hover:bg-muted/60 transition-colors"
                        >
                          {composerExpanded ? (
                            <ChevronsDownUp className="size-3.5" />
                          ) : (
                            <ChevronsUpDown className="size-3.5" />
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {composerExpanded
                          ? t("evolution.chatTune.composerCollapse")
                          : t("evolution.chatTune.composerExpand")}
                      </TooltipContent>
                    </Tooltip>
                  )}
                  {freeText.length > 0 && (
                    <>
                      <div className="w-px h-3.5 bg-border/30" />
                      <span className="text-[10px] text-muted-foreground/50 tabular-nums animate-in fade-in duration-200">
                        {t("evolution.chatTune.composerCharCount", {
                          count: freeText.length,
                        })}
                      </span>
                    </>
                  )}
                </div>

                {/* Right: keyboard hint + send/stop */}
                <div className="flex items-center gap-2">
                  {/* Keyboard hints — visible when focused & has text */}
                  {composerFocused &&
                    freeText.trim() &&
                    !isGenerating &&
                    !readOnly && (
                      <div className="flex items-center gap-1.5 animate-in fade-in duration-150">
                        <span className="flex items-center gap-1 text-[10px] text-muted-foreground/40">
                          <kbd className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-border/40 bg-muted/40 font-mono text-[9px] leading-none">
                            <CornerDownLeft className="size-2.5" />
                          </kbd>
                          {t("evolution.chatTune.composerHintEnter")}
                        </span>
                      </div>
                    )}

                  {isGenerating ? (
                    <button
                      type="button"
                      onClick={handleStop}
                      className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-xl
                        bg-destructive/10 border border-destructive/30 text-destructive
                        hover:bg-destructive/20 transition-all text-xs font-medium"
                    >
                      <Square className="size-3 animate-pulse fill-current" />
                      <span>{t("evolution.chatTune.stopGeneration")}</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={handleSendFreeText}
                      disabled={!canSend || !freeText.trim()}
                      className={cn(
                        "inline-flex items-center gap-1.5 h-8 px-4 rounded-xl text-xs font-medium transition-all duration-200",
                        canSend && freeText.trim()
                          ? "bg-primary text-primary-foreground shadow-md shadow-primary/25 hover:shadow-lg hover:shadow-primary/30 hover:scale-[1.02] active:scale-[0.98]"
                          : "bg-primary/10 text-primary/40 cursor-not-allowed",
                      )}
                    >
                      <Send className="size-3.5" />
                      <span>{t("evolution.chatTune.send")}</span>
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────── Sub-components ─────────────────────────── */

const ChatMessageRow = memo(
  function ChatMessageRow({
    message,
    disabled,
    isStreaming,
    compactMode,
    taskId,
    onSubmit,
    onPayloadUpdate,
    onFocusComposer,
    onRetry,
  }: {
    message: ChatMessage
    disabled: boolean
    isStreaming: boolean
    compactMode?: boolean
    taskId: string
    onSubmit: (
      card: AssistantCard,
      messageId: string,
      submission: CardSubmission,
    ) => void
    onPayloadUpdate: (
      messageId: string,
      payload: Record<string, unknown>,
    ) => void
    onFocusComposer: () => void
    onRetry?: () => void
  }) {
    const { t } = useTranslation()
    const isUser = message.role === "user"
    const isSystem = message.role === "system"
    // Hide the raw [OPTIONS_START]...[OPTIONS_END] block: its choices are already
    // surfaced in the payload card below. User text never carries these markers.
    // Memoized so the regex passes don't re-run on every render/streaming token.
    const displayText = useMemo(
      () => (isUser ? message.text : stripOptionsBlock(message.text)),
      [isUser, message.text],
    )

    if (isSystem) {
      return (
        <div className="flex gap-2 animate-in fade-in slide-in-from-bottom-1 duration-200">
          <div className="shrink-0 flex items-center justify-center size-7 rounded-lg bg-muted border border-border/50">
            <Monitor className="size-3.5 text-muted-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <div
              className={cn(
                "rounded-2xl rounded-tl-sm border border-dashed border-muted-foreground/30 bg-muted/40 px-3.5 py-2 text-sm leading-relaxed text-muted-foreground",
                compactMode &&
                  "max-h-[160px] overflow-y-auto overscroll-contain",
              )}
            >
              <ChatMarkdown content={message.text ?? ""} />
            </div>
          </div>
          <div className="shrink-0 size-7 invisible" />
        </div>
      )
    }

    return (
      <div className="flex gap-2 animate-in fade-in slide-in-from-bottom-1 duration-200">
        {/* Left avatar: AI or placeholder */}
        <div
          className={cn(
            "shrink-0 size-7 rounded-lg",
            isUser && "invisible",
          )}
        >
          {!isUser && (
            <div className="size-full flex items-center justify-center bg-primary/10 border border-primary/20 rounded-lg">
              <Bot className="size-3.5 text-primary" />
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1 flex flex-col gap-2">
          {/* Text content */}
          {displayText && (
            <div
              className={cn(
                "rounded-2xl px-3.5 py-2 text-sm leading-relaxed shadow-sm min-w-0 [overflow-wrap:anywhere]",
                isUser
                  ? "bg-primary text-primary-foreground rounded-tr-sm whitespace-pre-wrap"
                  : "bg-gradient-to-br from-muted to-muted/60 border border-border/50 text-foreground rounded-tl-sm min-h-[44px] flex flex-col justify-center",
                compactMode &&
                  "max-h-[200px] overflow-y-auto overscroll-contain",
              )}
            >
              {isUser ? (
                displayText
              ) : (
                <ChatMarkdown content={displayText} streaming={isStreaming} />
              )}
            </div>
          )}

          {/* Streaming indicator when content is empty */}
          {isStreaming && !displayText && !message.card && <TypingDots />}

          {/* Card payload */}
          {message.card && (
            <CardRenderer
              card={message.card}
              messageId={message.id}
              submission={message.submission}
              disabled={disabled}
              compactMode={compactMode}
              taskId={taskId}
              onSubmit={onSubmit}
              onPayloadUpdate={onPayloadUpdate}
              onFocusComposer={onFocusComposer}
            />
          )}

          {/* Status badge + retry for cancelled / failed turns. Wraps the
           * retry button to a new line when the error text is long so it
           * never ends up vertically centered against multi-line content. */}
          {!isUser &&
            (message.turnStatus === "cancelled" ||
              message.turnStatus === "failed") && (
              <div className="flex flex-wrap items-start gap-x-2 gap-y-1.5 pt-1.5">
                <div
                  className={cn(
                    "inline-flex items-start gap-1.5 text-xs min-w-0 flex-1",
                    message.turnStatus === "failed"
                      ? "text-destructive"
                      : "text-muted-foreground",
                  )}
                >
                  {message.turnStatus === "failed" ? (
                    <AlertTriangle className="size-3 mt-0.5 shrink-0" />
                  ) : (
                    <Ban className="size-3 mt-0.5 shrink-0" />
                  )}
                  {message.turnStatus === "failed" ? (
                    <ExpandableError
                      text={message.error || t("evolution.chatTune.failed")}
                    />
                  ) : (
                    <span className="break-words leading-snug">
                      {t("evolution.chatTune.cancelled")}
                    </span>
                  )}
                </div>
                {onRetry && (
                  <button
                    type="button"
                    onClick={onRetry}
                    className={cn(
                      "inline-flex items-center gap-1 shrink-0 whitespace-nowrap",
                      "h-6 px-2 rounded-md text-xs font-medium",
                      "border border-primary/30 bg-primary/5 text-primary",
                      "hover:bg-primary/10 hover:border-primary/50 transition-colors",
                    )}
                  >
                    <RefreshCw className="size-3" />
                    <span>{t("evolution.chatTune.retry")}</span>
                  </button>
                )}
              </div>
            )}
        </div>

        {/* Right avatar: User or placeholder */}
        <div
          className={cn(
            "shrink-0 size-7 rounded-lg",
            !isUser && "invisible",
          )}
        >
          {isUser && (
            <div className="size-full flex items-center justify-center bg-secondary border border-border/50 rounded-lg">
              <User className="size-3.5 text-foreground/70" />
            </div>
          )}
        </div>
      </div>
    )
  },
  (prev, next) =>
    prev.message === next.message &&
    prev.disabled === next.disabled &&
    prev.isStreaming === next.isStreaming &&
    prev.compactMode === next.compactMode,
)

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 animate-in fade-in duration-200">
      <div className="shrink-0 flex items-center justify-center size-7 rounded-lg bg-primary/10 border border-primary/20">
        <Bot className="size-3.5 text-primary" />
      </div>
      <TypingDots />
    </div>
  )
}

function TypingDots() {
  return (
    <div className="rounded-2xl rounded-tl-sm border border-border/40 bg-muted px-3.5 py-2 flex items-center gap-1.5 min-h-[44px]">
      <span className="size-1.5 rounded-full bg-primary/60 animate-pulse" />
      <span className="size-1.5 rounded-full bg-primary/60 animate-pulse [animation-delay:120ms]" />
      <span className="size-1.5 rounded-full bg-primary/60 animate-pulse [animation-delay:240ms]" />
    </div>
  )
}

function WelcomeScreen({ t }: { t: (key: string) => string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 animate-in fade-in zoom-in-95 duration-500">
      <div
        className="size-14 rounded-2xl flex items-center justify-center shadow-lg shadow-primary/15 mb-5"
        style={{
          background:
            "linear-gradient(135deg, color-mix(in srgb, var(--primary) 20%, transparent), color-mix(in srgb, #6366f1 15%, transparent))",
          border:
            "1px solid color-mix(in srgb, var(--primary) 30%, transparent)",
        }}
      >
        <Sparkles className="size-6 text-primary" />
      </div>
      <h3 className="text-base font-semibold text-foreground mb-1">
        {t("evolution.chatTune.welcomeTitle")}
      </h3>
      <p className="text-sm text-muted-foreground text-center max-w-[300px] leading-relaxed mb-5">
        {t("evolution.chatTune.welcomeDesc")}
      </p>
      <div className="flex flex-wrap justify-center gap-1.5">
        {DISPLAY_STAGES.map((stage) => (
          <span
            key={stage}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-muted/50 border border-border/30 text-[11px] text-muted-foreground"
          >
            {t(`evolution.chatTune.stages.${stage}`)}
          </span>
        ))}
      </div>
    </div>
  )
}

/* ───────────────────────────── Cards ─────────────────────────────── */

/* ──────────────── Expandable error text ──────────────── */

/**
 * Error message renderer. Trims trailing whitespace, preserves newlines, and
 * collapses to the first line when the text spans multiple lines, with a
 * toggle to expand/collapse.
 */
export function ExpandableError({ text }: { text: string }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const trimmed = text.replace(/\s+$/g, "")
  const lines = trimmed.split(/\r?\n/)
  const multiline = lines.length > 1

  if (!multiline) {
    return <span className="break-words leading-snug">{trimmed}</span>
  }

  return (
    <span className="break-words leading-snug min-w-0">
      <span className="whitespace-pre-wrap">
        {expanded ? trimmed : lines[0]}
      </span>{" "}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="inline-flex items-center text-[10px] font-medium
          underline-offset-2 hover:underline whitespace-nowrap opacity-80 hover:opacity-100"
      >
        {expanded
          ? t("evolution.chatTune.collapseError", { defaultValue: "收起" })
          : t("evolution.chatTune.expandError", {
              defaultValue: "展开 ({{n}} 行)",
              n: lines.length,
            })}
      </button>
    </span>
  )
}

export function CardRenderer({
  card,
  messageId,
  submission,
  disabled,
  compactMode,
  taskId,
  onSubmit,
  onPayloadUpdate,
  onFocusComposer,
}: {
  card: AssistantCard
  messageId: string
  submission?: CardSubmission
  disabled: boolean
  compactMode?: boolean
  taskId: string
  onSubmit: (
    card: AssistantCard,
    messageId: string,
    submission: CardSubmission,
  ) => void
  onPayloadUpdate: (messageId: string, payload: Record<string, unknown>) => void
  onFocusComposer: () => void
}) {
  const { t } = useTranslation()
  const locked = !!submission

  return (
    <div
      className={cn(
        "w-full rounded-xl border bg-card/90 backdrop-blur-sm transition-colors",
        locked
          ? "border-emerald-500/30 bg-emerald-500/5"
          : "border-primary/30 shadow-[0_0_24px_-12px] shadow-primary/30",
      )}
    >
      <div className="flex flex-col gap-1 px-3.5 py-2.5 border-b border-border/40 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <div
            className={cn(
              "shrink-0 size-5 rounded-md flex items-center justify-center",
              locked
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                : "bg-primary/15 text-primary",
            )}
          >
            {locked ? (
              <Check className="size-3" />
            ) : (
              <Sparkles className="size-3" />
            )}
          </div>
          <p
            className="flex-1 min-w-0 truncate text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            title={t(`evolution.chatTune.stages.${card.stage}`)}
          >
            {t(`evolution.chatTune.stages.${card.stage}`)}
          </p>
        </div>
        <div className="text-sm text-foreground">
          <ChatMarkdown content={card.prompt} />
        </div>
        {card.hint && !locked && (
          <p className="text-xs text-muted-foreground leading-relaxed">
            {card.hint}
          </p>
        )}
      </div>

      <div
        className={cn(
          "px-3.5 py-3",
          compactMode && "max-h-[240px] overflow-y-auto overscroll-contain",
        )}
      >
        {card.kind === "choice" && (
          <ChoiceCardBody
            card={card}
            disabled={disabled}
            submission={submission}
            taskId={taskId}
            messageId={messageId}
            onSubmit={(s) => onSubmit(card, messageId, s)}
            onPayloadUpdate={(payload) => onPayloadUpdate(messageId, payload)}
            onFocusComposer={onFocusComposer}
          />
        )}
        {card.kind === "multichoice" && (
          <MultiChoiceCardBody
            card={card}
            disabled={disabled}
            submission={submission}
            onSubmit={(s) => onSubmit(card, messageId, s)}
          />
        )}
        {card.kind === "number" && (
          <NumberCardBody
            card={card}
            disabled={disabled}
            submission={submission}
            onSubmit={(s) => onSubmit(card, messageId, s)}
          />
        )}
        {card.kind === "text" && (
          <TextCardBody
            card={card}
            disabled={disabled}
            submission={submission}
            onSubmit={(s) => onSubmit(card, messageId, s)}
          />
        )}
        {card.kind === "summary" && (
          <SummaryCardBody
            disabled={disabled}
            submission={submission}
            onSubmit={(s) => onSubmit(card, messageId, s)}
          />
        )}
        {card.kind === "run" && (
          <RunCardBody
            disabled={disabled}
            submission={submission}
            onSubmit={(s) => onSubmit(card, messageId, s)}
          />
        )}
      </div>
    </div>
  )
}

function ChoiceCardBody({
  card,
  disabled,
  submission,
  taskId,
  messageId,
  onSubmit,
  onPayloadUpdate,
  onFocusComposer,
}: {
  card: Extract<AssistantCard, { kind: "choice" }>
  disabled: boolean
  submission?: CardSubmission
  taskId: string
  messageId: string
  onSubmit: (submission: CardSubmission) => void
  onPayloadUpdate: (payload: Record<string, unknown>) => void
  onFocusComposer: () => void
}) {
  const { t } = useTranslation()
  const selectedValue =
    submission && submission.kind === "choice" ? submission.value : undefined
  const [activeUploadOpt, setActiveUploadOpt] = useState<string | null>(null)
  const [customActiveValue, setCustomActiveValue] = useState<string | null>(
    null,
  )
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dirInputRef = useRef<HTMLInputElement>(null)

  const setDirInputRefEl = useCallback((el: HTMLInputElement | null) => {
    if (el) el.setAttribute("webkitdirectory", "")
    dirInputRef.current = el
  }, [])

  const validateFiles = useCallback(
    (files: File[], isDir: boolean): File[] | null => {
      if (isDir && files.length > MAX_SELECTION_COUNT) {
        toast.error(
          t("evolution.chatTune.tooManySelected", {
            count: files.length,
            max: MAX_FILE_COUNT,
          }),
        )
        return null
      }
      const sized = files.filter((f) => f.size <= MAX_FILE_BYTES)
      if (sized.length < files.length) {
        toast.error(
          t("evolution.chatTune.fileTooLarge", { maxMB: MAX_FILE_MB }),
        )
      }
      if (sized.length === 0) return null
      if (isDir && sized.length > MAX_FILE_COUNT) {
        toast.error(
          t("evolution.chatTune.tooManyFiles", { max: MAX_FILE_COUNT }),
        )
        return null
      }
      const total = sized.reduce((s, f) => s + f.size, 0)
      if (total > MAX_TOTAL_BYTES) {
        toast.error(
          t("evolution.chatTune.totalSizeExceeded", { maxMB: MAX_TOTAL_MB }),
        )
        return null
      }
      return sized
    },
    [t],
  )

  const handleUpload = useCallback(
    async (fileList: FileList, opt: ChoiceOption) => {
      const isDir = !!opt.ask_for_dir
      const files = validateFiles(Array.from(fileList), isDir)
      if (!files) return

      const optionIndex = card.options.indexOf(opt)
      setIsUploading(true)
      try {
        let resp: { payload: Record<string, unknown> }
        if (isDir) {
          resp = await Llm4AdChatTuneService.chatTuneUploadData({
            taskId,
            messageId,
            cardId: card.cardId,
            optionIndex,
            formData: { files },
          })
        } else {
          resp = await Llm4AdChatTuneService.chatTuneUploadFile({
            taskId,
            messageId,
            cardId: card.cardId,
            optionIndex,
            formData: { files },
          })
        }
        onPayloadUpdate(resp.payload)
        toast.success(t("evolution.chatTune.uploadSuccess"))
      } catch {
        toast.error(t("evolution.chatTune.uploadFailed"))
      } finally {
        setIsUploading(false)
      }
    },
    [taskId, messageId, card.cardId, card.options, onPayloadUpdate, validateFiles, t],
  )

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>, opt: ChoiceOption) => {
      if (e.target.files && e.target.files.length > 0) {
        handleUpload(e.target.files, opt)
      }
      e.target.value = ""
    },
    [handleUpload],
  )

  const handleConfirm = useCallback(
    (opt: ChoiceOption) => {
      const path = opt.file_path || opt.dir_path
      onSubmit({
        kind: "choice",
        value: opt.value,
        label: opt.label,
        filePath: path || undefined,
      })
    },
    [onSubmit],
  )

  const needsUpload = (opt: ChoiceOption) => opt.ask_for_path || opt.ask_for_dir
  const getUploadedPath = (opt: ChoiceOption) => opt.file_path || opt.dir_path

  return (
    <div className="max-h-64 overflow-y-auto grid grid-cols-1 sm:grid-cols-2 gap-2 pr-1">
      {card.options.map((opt) => {
        const isUploadOpt = needsUpload(opt)
        const optUploadedPath = isUploadOpt ? getUploadedPath(opt) : undefined
        const optHasPath = !!optUploadedPath
        const isActive = activeUploadOpt === opt.value
          || (optHasPath && !activeUploadOpt && !customActiveValue)

        const hasPendingAction = !!activeUploadOpt || !!customActiveValue
        const isSelected = selectedValue === opt.value && !hasPendingAction

        if (isUploadOpt && !disabled) {
          return (
            <div
              key={opt.value}
              role="button"
              tabIndex={0}
              onClick={() => {
                setCustomActiveValue(null)
                setActiveUploadOpt(isActive ? null : opt.value)
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault()
                  setCustomActiveValue(null)
                  setActiveUploadOpt(isActive ? null : opt.value)
                }
              }}
              className={cn(
                "text-left rounded-lg border px-3 py-2 transition-all cursor-pointer",
                isSelected
                  ? "border-emerald-500/50 bg-emerald-500/10"
                  : isActive
                    ? "border-primary/50 bg-primary/5"
                    : "border-border/60 hover:border-primary/60 hover:bg-primary/5",
              )}
            >
              <div className="flex items-center gap-2">
                <div
                  className={cn(
                    "shrink-0 size-4 rounded-full border flex items-center justify-center",
                    isSelected
                      ? "border-emerald-500 bg-emerald-500 text-white"
                      : isActive
                        ? "border-primary bg-primary/20"
                        : "border-muted-foreground/40",
                  )}
                >
                  {isSelected ? (
                    <Check className="size-3" />
                  ) : (
                    <CircleIcon className="size-2 fill-transparent text-transparent" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <span
                    className="inline-block text-xs font-mono font-medium px-1.5 py-0.5 rounded bg-muted text-foreground break-all text-left"
                  >
                    {opt.label}
                  </span>
                  {opt.description && (
                    <p className="text-xs text-muted-foreground mt-1 break-all">
                      {opt.description}
                    </p>
                  )}
                </div>
              </div>

              {isActive && (
                <div className="mt-2 ml-6 space-y-2" onClick={(e) => e.stopPropagation()}>
                  {optUploadedPath && (
                    <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-emerald-500/10 border border-emerald-500/20">
                      <CheckCircle2 className="size-3 text-emerald-500 shrink-0" />
                      <span className="text-xs text-foreground break-all font-mono">
                        {optUploadedPath}
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={isUploading}
                      onClick={() => {
                        if (opt.ask_for_dir) {
                          dirInputRef.current?.click()
                        } else {
                          fileInputRef.current?.click()
                        }
                      }}
                      className={cn(
                        "inline-flex items-center gap-1.5 h-7 px-3 rounded-md text-xs font-medium transition-all",
                        "border border-border/60 bg-background hover:bg-muted",
                        isUploading && "opacity-60 cursor-not-allowed",
                      )}
                    >
                      {isUploading ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <Upload className="size-3" />
                      )}
                      <span>
                        {isUploading
                          ? t("evolution.chatTune.uploadingFile")
                          : optUploadedPath
                            ? t("evolution.chatTune.reupload")
                            : opt.ask_for_dir
                              ? t("evolution.chatTune.uploadDir")
                              : t("evolution.chatTune.uploadFile")}
                      </span>
                    </button>
                    {optUploadedPath && (
                      <button
                        type="button"
                        disabled={isUploading}
                        onClick={() => handleConfirm(opt)}
                        className="inline-flex items-center gap-1.5 h-7 px-3 rounded-md text-xs font-medium transition-all bg-primary text-primary-foreground hover:bg-primary/90"
                      >
                        <Check className="size-3" />
                        <span>{t("evolution.chatTune.confirmPath")}</span>
                      </button>
                    )}
                  </div>

                  <input
                    ref={opt.ask_for_dir ? undefined : fileInputRef}
                    type="file"
                    className="hidden"
                    onChange={(e) => handleFileChange(e, opt)}
                  />
                  {opt.ask_for_dir && (
                    <input
                      ref={setDirInputRefEl}
                      type="file"
                      className="hidden"
                      multiple
                      onChange={(e) => handleFileChange(e, opt)}
                    />
                  )}
                </div>
              )}
            </div>
          )
        }

        if (opt.is_custom && !disabled) {
          const isCustomActive = customActiveValue === opt.value
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                setActiveUploadOpt(null)
                setCustomActiveValue(opt.value)
                onFocusComposer()
              }}
              className={cn(
                "group text-left rounded-lg border px-3 py-2 transition-all",
                isCustomActive
                  ? "border-primary/60 bg-primary/10"
                  : "border-border/60 hover:border-primary/60 hover:bg-primary/5",
              )}
            >
              <div className="flex items-center gap-2">
                <div
                  className={cn(
                    "shrink-0 size-4 rounded-full border flex items-center justify-center",
                    isCustomActive
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-muted-foreground/40",
                  )}
                >
                  {isCustomActive ? (
                    <Check className="size-3" />
                  ) : (
                    <CircleIcon className="size-2 fill-transparent text-transparent" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "inline-block text-xs font-mono font-medium px-1.5 py-0.5 rounded text-foreground break-all",
                      isCustomActive ? "bg-transparent" : "bg-muted",
                    )}
                  >
                    {opt.label}
                  </span>
                  <p className="text-xs text-muted-foreground mt-1 break-all">
                    {opt.description
                      ? `${opt.description} — ${t("evolution.chatTune.customOptionHint")}`
                      : t("evolution.chatTune.customOptionHint")}
                  </p>
                </div>
              </div>
            </button>
          )
        }

        return (
          <button
            key={opt.value}
            type="button"
            disabled={disabled}
            onClick={() => {
              setActiveUploadOpt(null)
              setCustomActiveValue(null)
              onSubmit({ kind: "choice", value: opt.value, label: opt.label })
            }}
            className={cn(
              "group text-left rounded-lg border px-3 py-2 transition-all",
              "disabled:cursor-not-allowed",
              isSelected
                ? "border-emerald-500/50 bg-emerald-500/10"
                : disabled
                  ? "border-border/40 bg-muted/30 opacity-60"
                  : "border-border/60 hover:border-primary/60 hover:bg-primary/5",
            )}
          >
            <div className="flex items-center gap-2">
              <div
                className={cn(
                  "shrink-0 size-4 rounded-full border flex items-center justify-center",
                  isSelected
                    ? "border-emerald-500 bg-emerald-500 text-white"
                    : "border-muted-foreground/40",
                )}
              >
                {isSelected ? (
                  <Check className="size-3" />
                ) : (
                  <CircleIcon className="size-2 fill-transparent text-transparent" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <span
                  className={cn(
                    "inline-block text-xs font-mono font-medium px-1.5 py-0.5 rounded text-foreground break-all",
                    isSelected ? "bg-transparent" : "bg-muted",
                  )}
                >
                  {opt.label}
                </span>
                {opt.description && (
                  <p className="text-xs text-muted-foreground mt-1 break-all">
                    {opt.description}
                  </p>
                )}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}

function MultiChoiceCardBody({
  card,
  disabled,
  submission,
  onSubmit,
}: {
  card: MultiChoiceCard
  disabled: boolean
  submission?: CardSubmission
  onSubmit: (submission: CardSubmission) => void
}) {
  const { t } = useTranslation()
  const lockedValues =
    submission && submission.kind === "multichoice" ? submission.values : null
  const [selected, setSelected] = useState<string[]>(
    () => lockedValues ?? card.defaultSelected ?? [],
  )

  const toggle = (value: string) => {
    if (disabled) return
    setSelected((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
  }

  const handleConfirm = () => {
    const labels = card.options
      .filter((o) => selected.includes(o.value))
      .map((o) => o.label)
    onSubmit({ kind: "multichoice", values: selected, labels })
  }

  return (
    <div className="space-y-2.5">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {card.options.map((opt) => {
          const checked = selected.includes(opt.value)
          return (
            <button
              key={opt.value}
              type="button"
              disabled={disabled}
              onClick={() => toggle(opt.value)}
              className={cn(
                "text-left rounded-lg border px-3 py-2 transition-colors",
                "disabled:cursor-not-allowed",
                checked
                  ? "border-primary/60 bg-primary/10"
                  : disabled
                    ? "border-border/40 bg-muted/30 opacity-60"
                    : "border-border/60 hover:border-primary/40 hover:bg-primary/5",
              )}
            >
              <div className="flex items-center gap-2">
                <div
                  className={cn(
                    "shrink-0 size-4 rounded-lg border flex items-center justify-center",
                    checked
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-muted-foreground/40",
                  )}
                >
                  {checked && <Check className="size-3" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">
                    {opt.label}
                  </p>
                  {opt.description && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {opt.description}
                    </p>
                  )}
                </div>
              </div>
            </button>
          )
        })}
      </div>
      {!disabled && (
        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={handleConfirm}>
            <Check className="size-3.5" />
            <span>{t("evolution.chatTune.submitChoiceLabel")}</span>
          </Button>
        </div>
      )}
    </div>
  )
}

function NumberCardBody({
  card,
  disabled,
  submission,
  onSubmit,
}: {
  card: NumberFormCard
  disabled: boolean
  submission?: CardSubmission
  onSubmit: (submission: CardSubmission) => void
}) {
  const { t } = useTranslation()
  const lockedValue =
    submission && submission.kind === "number" ? submission.value : null
  const [value, setValue] = useState<number>(lockedValue ?? card.defaultValue)

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-3">
        <Hash className="size-3.5 text-muted-foreground" />
        <input
          type="range"
          min={card.min ?? 0}
          max={card.max ?? 100}
          step={card.step ?? 1}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(Number(e.target.value))}
          className="flex-1 accent-primary disabled:cursor-not-allowed disabled:opacity-60"
        />
        <input
          type="number"
          value={value}
          min={card.min}
          max={card.max}
          step={card.step}
          disabled={disabled}
          onChange={(e) => setValue(Number(e.target.value))}
          className={cn(
            "w-20 rounded-md border border-input bg-background px-2 py-1 text-sm text-right",
            "focus:outline-none focus:border-primary/50",
            "disabled:cursor-not-allowed disabled:opacity-60",
          )}
        />
        {card.unit && (
          <span className="text-xs text-muted-foreground">{card.unit}</span>
        )}
      </div>
      {!disabled && (
        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            onClick={() => onSubmit({ kind: "number", value })}
          >
            <Check className="size-3.5" />
            <span>{t("evolution.chatTune.confirmLabel")}</span>
          </Button>
        </div>
      )}
    </div>
  )
}

function TextCardBody({
  card,
  disabled,
  submission,
  onSubmit,
}: {
  card: TextFormCard
  disabled: boolean
  submission?: CardSubmission
  onSubmit: (submission: CardSubmission) => void
}) {
  const { t } = useTranslation()
  const lockedValue =
    submission && submission.kind === "text" ? submission.value : null
  const [value, setValue] = useState(lockedValue ?? card.defaultValue ?? "")

  return (
    <div className="space-y-2.5">
      <input
        type="text"
        value={value}
        placeholder={card.placeholder}
        disabled={disabled}
        maxLength={INPUT_LIMITS.cardText}
        onChange={(e) => setValue(e.target.value)}
        className={cn(
          "w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
          "focus:outline-none focus:border-primary/50",
          "disabled:cursor-not-allowed disabled:opacity-60",
        )}
      />
      {!disabled && (
        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            onClick={() => onSubmit({ kind: "text", value: value.trim() })}
            disabled={!value.trim()}
          >
            <Check className="size-3.5" />
            <span>{t("evolution.chatTune.confirmLabel")}</span>
          </Button>
        </div>
      )}
    </div>
  )
}

function SummaryCardBody({
  disabled,
  submission,
  onSubmit,
}: {
  disabled: boolean
  submission?: CardSubmission
  onSubmit: (submission: CardSubmission) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {t("evolution.chatTune.summaryHint")}
      </p>
      {!disabled && !submission && (
        <Button
          type="button"
          size="sm"
          onClick={() => onSubmit({ kind: "summary", ack: true })}
        >
          <CheckCircle2 className="size-3.5" />
          <span>{t("evolution.chatTune.summaryConfirm")}</span>
        </Button>
      )}
    </div>
  )
}

function RunCardBody({
  disabled,
  submission,
  onSubmit,
}: {
  disabled: boolean
  submission?: CardSubmission
  onSubmit: (submission: CardSubmission) => void
}) {
  const { t } = useTranslation()
  if (submission) {
    return (
      <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400 text-sm">
        <CheckCircle2 className="size-4" />
        <span>{t("evolution.chatTune.runSubmitted")}</span>
      </div>
    )
  }
  return (
    <div className="flex items-center justify-end">
      {!disabled && (
        <Button
          type="button"
          size="sm"
          onClick={() => onSubmit({ kind: "run", ack: true })}
        >
          <Play className="size-3.5" />
          <span>{t("evolution.chatTune.submitRun")}</span>
        </Button>
      )}
    </div>
  )
}

/* ───────────────────────── Preview panel ─────────────────────────── */

function PreviewPanel({
  task,
  steps,
  isGenerating,
  buildCompleted,
  readOnly,
}: {
  task: TaskResponse
  steps: BuildStep[]
  isGenerating: boolean
  buildCompleted: boolean
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const taskId = task.id
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [previewPath, setPreviewPath] = useState<string | null>(null)

  const total = steps.length
  const completedCount = steps.filter((s) => s.state === "completed").length
  const progressPct = total > 0 ? Math.round((completedCount / total) * 100) : 0
  const failedCount = steps.filter((s) => s.state === "failed").length

  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: ["taskDataTree", taskId],
    queryFn: () => Llm4AdTasksService.getTaskDataTree({ taskId }),
    enabled: !!taskId,
  })
  const isFetchingTree = useIsFetching({ queryKey: ["taskDataTree", taskId] }) > 0
  const tree = treeData?.tree ?? []

  return (
    <div
      data-tour="ai-preview"
      className="min-h-0 flex flex-col rounded-xl border border-border/60 bg-card/70 overflow-hidden"
    >
      {/* Progress bar */}
      <div className="shrink-0 px-4 py-3 border-b border-border/50 bg-muted/30">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("evolution.chatTune.preview.progress")}
          </span>
          <span className="text-xs font-semibold text-foreground">
            {completedCount} / {total}
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className={cn(
              "h-full transition-[width] duration-400",
              failedCount > 0
                ? "bg-linear-to-r from-primary to-red-500"
                : "bg-linear-to-r from-primary to-emerald-400",
            )}
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Build step checklist */}
      <div className="shrink-0 px-4 py-3 border-b border-border/50">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          {t("evolution.chatTune.preview.stages")}
        </p>
        <ul className="relative space-y-1">
          <div
            className="absolute left-4 top-4 bottom-4 w-px bg-border/40"
            aria-hidden="true"
          />
          {steps.map(({ key, state, isActive }) => (
            <li
              key={key}
              className={cn(
                "relative flex items-center gap-2 px-2 py-1.5 rounded-md text-sm transition-colors",
                state === "failed"
                  ? "bg-destructive/10 text-destructive"
                  : isActive
                    ? "bg-primary/10 text-primary"
                    : state === "completed"
                      ? "text-foreground"
                      : "text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "shrink-0 size-4 rounded-full flex items-center justify-center ring-2 ring-card",
                  state === "completed"
                    ? "bg-emerald-500 text-white"
                    : state === "failed"
                      ? "bg-destructive text-white"
                      : state === "running"
                        ? "bg-primary text-primary-foreground"
                        : isActive
                          ? "bg-primary/30 text-primary"
                          : "bg-muted text-muted-foreground",
                )}
              >
                {state === "completed" ? (
                  <Check className="size-2.5" />
                ) : state === "failed" ? (
                  <X className="size-2.5" />
                ) : state === "running" ? (
                  <Loader2 className="size-2.5 animate-spin" />
                ) : isActive ? (
                  <ChevronRight className="size-2.5" />
                ) : (
                  <CircleIcon className="size-2 fill-transparent text-transparent" />
                )}
              </span>
              <span className="truncate">
                {t(`evolution.chatTune.buildSteps.${key}`)}
              </span>
              <span className="ml-auto shrink-0 inline-flex items-center gap-1 text-[10px] font-medium">
                {state === "running" ? (
                  <span className="inline-flex items-center gap-1 text-primary">
                    {isGenerating && (
                      <Loader2 className="size-2.5 animate-spin" />
                    )}
                    {t("evolution.chatTune.buildStepRunning")}
                  </span>
                ) : state === "failed" ? (
                  <span className="text-destructive">
                    {t("evolution.chatTune.buildStepFailed")}
                  </span>
                ) : state === "completed" ? (
                  <span className="text-emerald-500/80">
                    {t("evolution.chatTune.buildStepCompleted")}
                  </span>
                ) : isActive ? (
                  <span className="text-primary/70">
                    {t("evolution.chatTune.buildStepActive")}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {/* Generated file list */}
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="shrink-0 h-9 flex items-center justify-between px-3 border-b border-border/50 bg-muted/30">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("evolution.chatTune.preview.files")}
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                disabled={isFetchingTree}
                className={cn(
                  "p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors",
                  isFetchingTree && "cursor-not-allowed opacity-60 hover:bg-transparent hover:text-muted-foreground",
                )}
                onClick={() =>
                  queryClient.invalidateQueries({
                    queryKey: ["taskDataTree", taskId],
                  })
                }
              >
                <RefreshCw
                  className={cn("size-3.5", isFetchingTree && "animate-spin")}
                />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t("common.refresh")}</TooltipContent>
          </Tooltip>
        </div>
        <div className="flex-1 min-h-[20vh] max-h-[45vh] overflow-y-auto p-2">
          {treeLoading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          ) : tree.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground px-3">
              <FileIcon className="size-8 mb-2 opacity-30" />
              <p className="text-xs font-medium">
                {t("evolution.dataPrep.noFilesTitle")}
              </p>
            </div>
          ) : (
            <FileTreeView
              tree={tree}
              selectedPath={selectedPath}
              onSelectFile={(path, isDir) => {
                if (!isDir) {
                  setSelectedPath(path)
                  setPreviewPath(path)
                }
              }}
            />
          )}
        </div>
      </div>

      {/* Planner / coder providers — same UX as manual step 4 so the user can
       * override AI-picked providers before submitting the run. Lives below
       * the file list so the build progress + outputs stay primary. */}
      <AiBuildProviders task={task} readOnly={readOnly} />

      {!readOnly && (
        <AiBuildRunButton task={task} buildCompleted={buildCompleted} />
      )}

      <FilePreviewDialog
        taskId={taskId}
        path={previewPath}
        onClose={() => setPreviewPath(null)}
      />
    </div>
  )
}

/* ───────────────────────── AI build run button ───────────────────── */

/* ───────────────── AI build planner/coder providers ───────────────── */

function AiBuildProviders({
  task,
  readOnly,
}: {
  task: TaskResponse
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)

  const args = (task.input_args ?? {}) as Record<string, unknown>
  const planner = (args.planner ?? {}) as Record<string, unknown>
  const coder = (args.coder ?? {}) as Record<string, unknown>
  const plannerProvider = (planner.provider as string) || ""
  const plannerModel = (planner.provider_model as string) || ""
  const coderProvider = (coder.provider as string) || ""
  const coderModel = (coder.provider_model as string) || ""

  const customized = !!(
    plannerProvider ||
    plannerModel ||
    coderProvider ||
    coderModel
  )

  const updateMutation = useMutation({
    mutationFn: async (next: Record<string, unknown>) => {
      await Llm4AdTasksService.updateTask({
        taskId: task.id,
        requestBody: { input_args: next },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["getTask", task.id] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const applyUpdate = useCallback(
    (prefix: "planner" | "coder", provider: string, providerModel: string) => {
      const base = (task.input_args ?? {}) as Record<string, unknown>
      const section = (base[prefix] ?? {}) as Record<string, unknown>
      const nextSection = {
        ...section,
        provider,
        provider_model: providerModel,
      }
      updateMutation.mutate({ ...base, [prefix]: nextSection })
    },
    [task.input_args, updateMutation],
  )

  return (
    <div className="shrink-0 border-t border-border/50">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-accent/40 transition-colors"
      >
        <ChevronRight
          className={cn(
            "size-3 text-muted-foreground shrink-0 transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("evolution.providerSelect.title")}
        </span>
        {customized && (
          <span className="ml-1 inline-flex items-center gap-1 px-1.5 py-0 rounded text-[9px] font-medium bg-primary/10 text-primary border border-primary/25">
            {t("evolution.chatTune.providerCustomized", {
              defaultValue: "已自定义",
            })}
          </span>
        )}
        {updateMutation.isPending && (
          <Loader2 className="ml-auto size-3 animate-spin text-muted-foreground" />
        )}
      </button>
      {open && (
        <div
          className={cn(
            "px-3 pb-2.5 pt-1",
            readOnly && "opacity-60 pointer-events-none",
          )}
        >
          <EvolutionProviderSelect
            plannerProvider={plannerProvider}
            plannerModel={plannerModel}
            coderProvider={coderProvider}
            coderModel={coderModel}
            onPlannerChange={(p, m) => applyUpdate("planner", p, m)}
            onCoderChange={(p, m) => applyUpdate("coder", p, m)}
            compact
          />
        </div>
      )}
    </div>
  )
}

function AiBuildRunButton({
  task,
  buildCompleted,
}: {
  task: TaskResponse
  buildCompleted: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { projectId, setActiveTab, resetTaskData, setIsConfiguring, setIsViewingParams, updateTaskStatus } =
    useEvolution()
  const { withCopyIfNeeded, isCopying } = useCopyBeforeRun(task)

  const mutation = useMutation({
    mutationFn: async () => {
      await withCopyIfNeeded(async (effectiveId) => {
        const result = await Llm4AdTasksService.runTask({ taskId: effectiveId })
        const nextStatus = result.status ?? "pending"
        setTaskStatusInCache(queryClient, effectiveId, nextStatus, projectId)
        updateTaskStatus(nextStatus)
      })
    },
    onSuccess: () => {
      showSuccessToast(t("evolution.confirmStep.submitSuccess"))
      setIsConfiguring(false)
      setIsViewingParams(false)
      setActiveTab("overview")
      resetTaskData()
    },
    onError: handleError.bind(showErrorToast),
  })

  const noData = !task.input_data_path
  // The button stays visible but disabled until the AI build has produced a
  // config and data is present, so the next action is always discoverable.
  const hint = !buildCompleted
    ? t("evolution.chatTune.runHintNeedBuild")
    : noData
      ? t("evolution.confirmStep.noDataWarning")
      : null

  return (
    <div
      data-tour="ai-run"
      className="shrink-0 px-3 py-2.5 border-t border-border/50 space-y-2"
    >
      {hint && (
        <div
          className={cn(
            "flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[11px]",
            buildCompleted
              ? "bg-yellow-500/10 border border-yellow-500/30 text-yellow-500"
              : "bg-muted/60 border border-border/50 text-muted-foreground",
          )}
        >
          {buildCompleted ? (
            <AlertTriangle className="size-3 shrink-0" />
          ) : (
            <CircleAlert className="size-3 shrink-0" />
          )}
          <span>{hint}</span>
        </div>
      )}
      <LoadingButton
        loading={mutation.isPending || isCopying}
        onClick={() => mutation.mutate()}
        disabled={!buildCompleted || noData || isCopying}
        className="w-full gap-1"
      >
        <Play className="size-3.5" />
        {t("evolution.confirmStep.submitRun")}
      </LoadingButton>
    </div>
  )
}

/* ───────────────────────── File preview dialog ───────────────────── */

function getLanguageFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? ""
  const map: Record<string, string> = {
    py: "python",
    js: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    md: "markdown",
    txt: "plaintext",
    csv: "plaintext",
    sh: "shell",
    bash: "shell",
    toml: "plaintext",
    cfg: "ini",
    ini: "ini",
    xml: "xml",
    html: "html",
    css: "css",
  }
  return map[ext] ?? "plaintext"
}

function FilePreviewDialog({
  taskId,
  path,
  onClose,
}: {
  taskId: string
  path: string | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme !== "light"

  const { data, isLoading, isError } = useQuery({
    queryKey: ["taskDataFile", taskId, path],
    queryFn: () =>
      Llm4AdTasksService.getTaskDataFile({ taskId, filePath: path! }),
    enabled: !!path,
  })

  return (
    <Dialog
      open={!!path}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="sm:max-w-3xl max-h-[80vh] flex flex-col gap-0 p-0 overflow-hidden">
        <DialogHeader className="px-4 py-3 border-b text-left">
          <DialogTitle className="text-sm font-mono break-all">
            {path}
          </DialogTitle>
          <DialogDescription className="sr-only">{path}</DialogDescription>
        </DialogHeader>
        <div
          className={cn(
            "h-[60vh] w-full",
            isDark ? "bg-[#0D1A2D]" : "bg-background",
          )}
        >
          {isLoading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : isError ? (
            <div className="flex items-center justify-center h-full px-6 text-center">
              <p className="text-sm text-muted-foreground">
                {t("evolution.dataPrep.unsupportedPreviewTitle")}
              </p>
            </div>
          ) : (
            <Editor
              height="100%"
              language={getLanguageFromPath(path ?? "")}
              theme={isDark ? "custom-dark" : "custom-light"}
              value={data?.content ?? ""}
              beforeMount={(m) => {
                m.editor.defineTheme("custom-dark", {
                  base: "vs-dark",
                  inherit: true,
                  rules: [],
                  colors: { "editor.background": "#0D1A2D" },
                })
                m.editor.defineTheme("custom-light", {
                  base: "vs",
                  inherit: true,
                  rules: [],
                  colors: { "editor.background": "#ffffff" },
                })
              }}
              options={{
                minimap: { enabled: false },
                fontSize: 12,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                automaticLayout: true,
                tabSize: 2,
                readOnly: true,
                domReadOnly: true,
                renderLineHighlight: "none",
                scrollbar: { verticalScrollbarSize: 6 },
              }}
              loading={
                <div className="flex items-center justify-center h-full">
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                </div>
              }
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

/* ──────────────── Provider / Model selectors ──────────────── */

function ChatTuneProviderSelect({
  value,
  onChange,
  providers,
  defaultHint,
  t,
}: {
  value: string
  onChange: (v: string) => void
  providers: ProviderResponse[]
  defaultHint?: string
  t: (key: string) => string
}) {
  return (
    <div className="relative">
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger className="w-full text-xs h-9">
          <SelectValue placeholder={t("evolution.chatTune.selectProvider")} />
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
              <span className="text-muted-foreground text-xs">
                {defaultHint || t("evolution.chatTune.defaultProvider")}
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
              <span className="text-muted-foreground text-xs">
                {t("evolution.chatTune.mockProvider")}
              </span>
            </div>
          </SelectItem>
          {providers.length > 0 && <SelectSeparator />}
          {providers.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              <div className="flex items-center gap-1.5">
                <span className="text-xs">{p.name}</span>
                <Badge variant="outline" className="text-[9px] font-normal">
                  {p.type}
                </Badge>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {value && value !== "default" && (
        <button
          type="button"
          className="absolute right-7 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground rounded-sm p-0.5"
          onClick={(e) => {
            e.stopPropagation()
            onChange("default")
          }}
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}

/* ──────────────── Model input component ──────────────── */

function ChatTuneModelInput({
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
          "w-full text-xs pr-7 h-9",
          disabled && "bg-muted/50 cursor-not-allowed",
        )}
      />
      {!disabled && suggestions.length > 0 && (
        <button
          type="button"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100"
          onClick={() => {
            setOpen(!open)
            inputRef.current?.focus()
          }}
        >
          <ChevronDown className="size-3.5" />
        </button>
      )}
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 bottom-full mb-1 left-0 w-full max-h-48 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg p-1">
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

/* ───────────────────────── Helpers ─────────────────────────── */

function ChatMarkdown({
  content,
  streaming,
}: {
  content: string
  streaming?: boolean
}) {
  const mermaidIdPrefix = useId()
  const components = useMemo(
    () => makeMarkdownComponents(mermaidIdPrefix, { streaming }),
    [mermaidIdPrefix, streaming],
  )
  return (
    <div
      className={cn(
        `prose prose-sm dark:prose-invert max-w-none [overflow-wrap:anywhere]
        prose-p:my-1 prose-p:leading-relaxed
        prose-headings:my-2 prose-headings:font-semibold
        prose-ul:my-1 prose-ol:my-1 prose-li:my-0
        prose-code:text-[1em] prose-code:bg-black/10 prose-code:dark:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
        prose-pre:my-2 prose-pre:rounded-lg prose-pre:text-[13px] prose-pre:leading-relaxed prose-pre:bg-code-block-bg prose-pre:text-foreground prose-pre:border prose-pre:border-border/50
        prose-a:text-primary prose-a:no-underline hover:prose-a:underline
        prose-blockquote:my-2 prose-blockquote:border-primary/30
        [&>*:first-child]:mt-0 [&>*:last-child]:mb-0`,
        streaming && "chat-streaming-cursor",
      )}
    >
      <Markdown
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        components={components}
      >
        {content}
      </Markdown>
    </div>
  )
}

export function formatUserSubmissionText(submission: CardSubmission): string {
  switch (submission.kind) {
    case "choice":
      return submission.filePath
        ? `${submission.label} (${submission.filePath})`
        : submission.label
    case "multichoice":
      if (submission.labels.length === 0) return "\u2014"
      return submission.labels.join(", ")
    case "number":
      return `${submission.value}`
    case "text":
      return submission.value
    case "summary":
      return "\u2713"
    case "run":
      return "\u25b6"
    default:
      return ""
  }
}

function mapApiMessageToChatMessage(msg: ChatTuneMessageItem): ChatMessage {
  const result: ChatMessage = {
    id: msg.id,
    turnId: msg.turn_id,
    role: msg.role,
    text: msg.content || undefined,
    turnStatus: msg.turn_status,
    error: msg.error,
    createdAt: new Date(msg.created_time).getTime(),
  }

  if (msg.payload) {
    const card = parsePayloadToCard(msg.payload)
    if (card) {
      result.card = card
      if (msg.payload_locked && msg.payload_submission) {
        result.submission = msg.payload_submission as unknown as CardSubmission
      }
    }
  }

  return result
}

// Matches a complete options block, markers included. The backend parses these
// choices into the payload card rendered below the bubble, so the raw block must
// be hidden from the displayed message text.
const OPTIONS_BLOCK_RE = /\[OPTIONS_START\][\s\S]*?\[OPTIONS_END\]/gi
// Matches a dangling, still-unclosed options block (mid-stream, before the
// closing marker has arrived) so the raw markers never flash on screen.
const OPTIONS_OPEN_RE = /\[OPTIONS_START\][\s\S]*$/i

/**
 * Remove `[OPTIONS_START]...[OPTIONS_END]` blocks from assistant message text.
 *
 * The options inside these markers are extracted by the backend into the
 * interactive payload card, so showing them again as raw text in the bubble is
 * redundant. Also strips an unclosed block during streaming.
 *
 * @param content - Raw assistant message text.
 * @returns The text with any options block(s) removed and trailing whitespace trimmed.
 */
export function stripOptionsBlock(content: string): string
export function stripOptionsBlock(content: string | undefined): string | undefined
export function stripOptionsBlock(content: string | undefined): string | undefined {
  if (!content) return content
  return content.replace(OPTIONS_BLOCK_RE, "").replace(OPTIONS_OPEN_RE, "").trimEnd()
}

export function parsePayloadToCard(
  payload: Record<string, unknown>,
): AssistantCard | undefined {
  const kind = payload.kind as string | undefined
  if (!kind) return undefined
  return payload as unknown as AssistantCard
}
