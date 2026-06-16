import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Ban,
  Check,
  ChevronDown,
  CornerDownLeft,
  Eye,
  Loader2,
  RefreshCw,
  Send,
  Settings2,
  Sparkles,
  Square,
  Target,
  X,
} from "lucide-react"
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import type {
  ChatTuneActiveStage,
  ChatTuneMessageItem,
  ChatTuneTurnStatus,
  ProviderResponse,
} from "@/client"
import { Llm4AdChatTuneService } from "@/client"
import {
  CardRenderer,
  ExpandableError,
  formatUserSubmissionText,
  parsePayloadToCard,
  stripOptionsBlock,
} from "@/components/Evolution/TaskDetail/ChatTuneView"
import type {
  AssistantCard,
  CardSubmission,
} from "@/components/Evolution/TaskDetail/chat-tune-mock"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import { Badge } from "@/components/ui/badge"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useChatTuneStream } from "@/hooks/useChatTuneStream"
import { useEvolution } from "@/hooks/useEvolution"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { INPUT_LIMITS } from "@/lib/inputLimits"
import { cn, handleComposerEnter } from "@/lib/utils"

/* ─────────────────── Helpers ──────────────────── */

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

function isSpecialProvider(v: string): boolean {
  return ["", "default", "mock"].includes(v)
}

/* ─────────────────── Drag hook ──────────────────── */

function useDrag(
  dragRef: React.RefObject<HTMLElement | null>,
  initial: { right: number; bottom: number },
  onClick?: () => void,
) {
  const dragging = useRef(false)
  const dragMoved = useRef(false)
  const offsetRef = useRef({ x: 0, y: 0 })

  const [pos, setPos] = useState(initial)

  const stop = useCallback(() => {
    dragging.current = false
  }, [])

  const onDocumentPointerMove = useCallback(
    (e: PointerEvent) => {
      if (!dragging.current) return
      dragMoved.current = true
      const el = dragRef.current
      if (!el) return
      const elW = el.offsetWidth
      const elH = el.offsetHeight
      const newRight =
        window.innerWidth - e.clientX - (elW - offsetRef.current.x)
      const newBottom =
        window.innerHeight - e.clientY - (elH - offsetRef.current.y)
      setPos({
        right: Math.max(0, Math.min(newRight, window.innerWidth - elW)),
        bottom: Math.max(0, Math.min(newBottom, window.innerHeight - elH)),
      })
    },
    [dragRef],
  )

  const onDocumentPointerUp = useCallback(() => {
    if (!dragging.current) return
    const wasDrag = dragMoved.current
    stop()
    if (!wasDrag && onClick) {
      onClick()
    }
  }, [onClick, stop])

  useEffect(() => {
    document.addEventListener("pointermove", onDocumentPointerMove)
    document.addEventListener("pointerup", onDocumentPointerUp)
    document.addEventListener("pointercancel", stop)
    return () => {
      document.removeEventListener("pointermove", onDocumentPointerMove)
      document.removeEventListener("pointerup", onDocumentPointerUp)
      document.removeEventListener("pointercancel", stop)
    }
  }, [onDocumentPointerMove, onDocumentPointerUp, stop])

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      dragging.current = true
      dragMoved.current = false
      const el = dragRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      offsetRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    },
    [dragRef],
  )

  return { pos, setPos, onPointerDown }
}

/* ─────────────────── Message type ──────────────────── */

interface FloatingMessage {
  id: string
  turnId?: string
  role: "user" | "assistant" | "system"
  content: string
  isStreaming?: boolean
  turnStatus?: ChatTuneTurnStatus
  error?: string | null
  card?: AssistantCard
  submission?: CardSubmission
}

const TARGET_STAGE_OPTIONS: Array<{
  value: ChatTuneActiveStage | null
  key: string
}> = [
  { value: null, key: "auto" },
  { value: "gathering", key: "gathering" },
  { value: "build", key: "build" },
  { value: "review", key: "review" },
]

/* ─────────────────── Main component ──────────────────── */

export default function AIChatFloating() {
  const {
    selectedTask,
    effectiveTaskId,
    isConfiguring,
    isViewingParams,
    isViewingAiBuildHistory,
  } = useEvolution()

  // Hide when no task / no version is selected.
  if (!selectedTask || !effectiveTaskId) return null
  // Hide while the main AI build / config / params panel is open — avoids
  // showing two AI helpers at once.
  if (isConfiguring || isViewingParams || isViewingAiBuildHistory) return null

  // Mirror the main panel's read-only rule: while a turn is in flight (the
  // task is running/pending) users cannot mutate the session.
  const readOnly =
    selectedTask.status === "running" || selectedTask.status === "pending"

  return (
    <FloatingChatImpl
      key={effectiveTaskId}
      taskId={effectiveTaskId}
      taskName={selectedTask.name}
      readOnly={readOnly}
    />
  )
}

function FloatingChatImpl({
  taskId,
  taskName,
  readOnly,
}: {
  taskId: string
  taskName: string
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const { activeTab } = useEvolution()
  const queryClient = useQueryClient()
  const stream = useChatTuneStream()
  // Inject the highlight.js theme so fenced code blocks are colorized.
  useHljsTheme()

  const showIdleAnimation = activeTab !== "ide"

  /* ──── Provider & model ──── */
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
  const [targetStage, setTargetStage] = useState<ChatTuneActiveStage | null>(
    null,
  )

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

  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<FloatingMessage[]>([])
  const [input, setInput] = useState("")
  const [composerFocused, setComposerFocused] = useState(false)
  const floatingTextareaRef = useRef<HTMLTextAreaElement>(null)
  const [isSending, setIsSending] = useState(false)
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null)
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const streamContentRef = useRef("")
  const streamRafId = useRef(0)

  // Share the session query with the main ChatTuneView via the same query key
  // — both panels read the same cache and writes from one refresh the other.
  const { data: sessionDetail, isFetching: isSessionFetching } = useQuery({
    queryKey: ["chatTuneSession", taskId],
    queryFn: () => Llm4AdChatTuneService.getSession({ taskId, limit: 30 }),
    enabled: !!taskId && open,
  })

  const refetchSession = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["chatTuneSession", taskId] })
  }, [queryClient, taskId])

  // Sync messages from cached session whenever it refetches. Stays aligned
  // with the server (and the main panel).
  useEffect(() => {
    if (!sessionDetail?.messages) return
    setMessages(sessionDetail.messages.map(apiToFloatingMessage))
  }, [sessionDetail])

  // Reconnect to an in-flight turn on (re)load
  useEffect(() => {
    if (!sessionDetail || !open) return
    const turnId = sessionDetail.session.active_turn_id
    if (!turnId) return
    const generating = (sessionDetail.messages ?? []).find(
      (m) => m.turn_id === turnId && m.turn_status === "generating",
    )
    if (!generating) return
    setActiveTurnId(turnId)
    setStreamingMsgId(generating.id)
    connectStream(taskId, turnId, generating.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    sessionDetail?.session.id,
    open,
    connectStream,
    sessionDetail.session.active_turn_id,
    taskId,
    sessionDetail.messages,
    sessionDetail,
  ])

  // Auto-scroll messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stream.disconnect()
      cancelAnimationFrame(streamRafId.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.disconnect])

  const connectStream = useCallback(
    (tid: string, turnId: string, msgId: string) => {
      streamContentRef.current = ""

      const scheduleFlush = () => {
        cancelAnimationFrame(streamRafId.current)
        streamRafId.current = requestAnimationFrame(() => {
          const content = streamContentRef.current
          setMessages((prev) =>
            prev.map((m) => (m.id === msgId ? { ...m, content } : m)),
          )
        })
      }

      stream.connect(tid, turnId, {
        onChunk(content) {
          streamContentRef.current += content
          scheduleFlush()
        },
        onPayload(payload) {
          const card = parsePayloadToCard(payload)
          if (card) {
            setMessages((prev) =>
              prev.map((m) => (m.id === msgId ? { ...m, card } : m)),
            )
          }
        },
        onStageUpdate() {
          // Stage status changes affect downstream listeners (main panel).
          // Trigger session refresh so the main panel's stageSnapshot stays
          // aligned without waiting for onDone.
          refetchSession()
        },
        onDone() {
          cancelAnimationFrame(streamRafId.current)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId
                ? {
                    ...m,
                    content: streamContentRef.current || m.content,
                    isStreaming: false,
                    turnStatus: "completed",
                  }
                : m,
            ),
          )
          setStreamingMsgId(null)
          setActiveTurnId(null)
          refetchSession()
          queryClient.invalidateQueries({ queryKey: ["getTask", tid] })
        },
        onCancelled() {
          cancelAnimationFrame(streamRafId.current)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId
                ? {
                    ...m,
                    content: streamContentRef.current || m.content,
                    isStreaming: false,
                    turnStatus: "cancelled",
                  }
                : m,
            ),
          )
          setStreamingMsgId(null)
          setActiveTurnId(null)
          refetchSession()
        },
        onError(error) {
          cancelAnimationFrame(streamRafId.current)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId
                ? {
                    ...m,
                    content: streamContentRef.current || m.content,
                    isStreaming: false,
                    turnStatus: "failed",
                    error,
                  }
                : m,
            ),
          )
          setStreamingMsgId(null)
          setActiveTurnId(null)
          refetchSession()
        },
      })
    },
    [stream, refetchSession, queryClient],
  )

  const sendTurn = useCallback(
    async (
      content?: string,
      respondToMessageId?: string,
      submission?: CardSubmission,
    ) => {
      if (readOnly || isSending || stream.isStreaming) return
      setIsSending(true)

      try {
        const resp = await Llm4AdChatTuneService.startTurn({
          taskId,
          requestBody: {
            content: content || undefined,
            respond_to_message_id: respondToMessageId || undefined,
            submission: submission
              ? (submission as unknown as Record<string, unknown>)
              : undefined,
            provider_id: providerId || undefined,
            model_name: modelName || undefined,
            target_stage: targetStage,
          },
        })

        const userMsg: FloatingMessage = {
          id: resp.user_message.id,
          turnId: resp.turn_id,
          role: "user",
          content: resp.user_message.content,
        }
        const assistantMsg: FloatingMessage = {
          id: resp.assistant_message.id,
          turnId: resp.turn_id,
          role: "assistant",
          content: "",
          isStreaming: true,
          turnStatus: "generating",
        }

        setMessages((prev) => {
          let updated = [...prev]
          if (resp.locked_message) {
            // Mark the card we just replied to as locked so it switches to
            // the read-only "submitted" appearance immediately.
            const locked = resp.locked_message
            updated = updated.map((m) =>
              m.id === locked.id ? apiToFloatingMessage(locked) : m,
            )
          }
          return [...updated, userMsg, assistantMsg]
        })
        setActiveTurnId(resp.turn_id)
        setStreamingMsgId(resp.assistant_message.id)
        // target_stage is a single-turn override; reset after a successful start.
        setTargetStage(null)
        connectStream(taskId, resp.turn_id, resp.assistant_message.id)
      } catch (err: unknown) {
        const status = (err as { status?: number })?.status
        if (status === 409) {
          toast.error(t("evolution.chatTune.error409"))
          refetchSession()
        } else if (status === 429) {
          toast.error(t("evolution.chatTune.error429"))
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
      taskId,
      providerId,
      modelName,
      targetStage,
      connectStream,
      refetchSession,
      t,
    ],
  )

  const handleSend = useCallback(() => {
    const text = input.trim()
    if (!text) return
    setInput("")
    if (floatingTextareaRef.current)
      floatingTextareaRef.current.style.height = "auto"
    sendTurn(text)
  }, [input, sendTurn])

  const handleCardSubmit = useCallback(
    (_card: AssistantCard, messageId: string, submission: CardSubmission) => {
      const userText = formatUserSubmissionText(submission)
      sendTurn(userText, messageId, submission)
    },
    [sendTurn],
  )

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

  const handleFocusComposer = useCallback(() => {
    floatingTextareaRef.current?.focus()
  }, [])

  const handleStop = useCallback(async () => {
    if (!activeTurnId) return
    try {
      await Llm4AdChatTuneService.stopTurn({
        taskId,
        turnId: activeTurnId,
      })
    } catch {
      // SSE will handle state transition
    }
  }, [activeTurnId, taskId])

  const handleRetry = useCallback(
    async (turnId: string) => {
      if (readOnly || isSending || stream.isStreaming) return
      try {
        const resp = await Llm4AdChatTuneService.retryTurn({
          taskId,
          turnId,
        })
        const newMsg: FloatingMessage = {
          id: resp.assistant_message.id,
          turnId: resp.turn_id,
          role: "assistant",
          content: resp.assistant_message.content || "",
          isStreaming: true,
          turnStatus: "generating",
        }
        setMessages((prev) =>
          prev.map((m) => (m.id === resp.assistant_message.id ? newMsg : m)),
        )
        setActiveTurnId(resp.turn_id)
        setStreamingMsgId(resp.assistant_message.id)
        connectStream(taskId, resp.turn_id, resp.assistant_message.id)
      } catch {
        toast.error(t("evolution.chatTune.errorNetwork"))
      }
    },
    [readOnly, isSending, stream.isStreaming, taskId, connectStream, t],
  )

  const floatingAutoResize = useCallback(() => {
    const el = floatingTextareaRef.current
    if (!el) return
    el.style.height = "auto"
    const maxH = 100
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`
    el.style.overflowY = el.scrollHeight > maxH ? "auto" : "hidden"
  }, [])

  useEffect(() => {
    floatingAutoResize()
  }, [floatingAutoResize])

  const isGenerating = stream.isStreaming || !!streamingMsgId

  // Button drag
  const btnRef = useRef<HTMLButtonElement>(null)
  const btnDrag = useDrag(
    btnRef,
    { right: 32, bottom: Math.round(window.innerHeight / 2 - 24) },
    () => setOpen((v) => !v),
  )

  // Dialog drag
  const dialogRef = useRef<HTMLDivElement>(null)
  const dialogDrag = useDrag(dialogRef, { right: 92, bottom: 0 })

  const DIALOG_W = 380
  const DIALOG_H = 520
  const BTN_SIZE = 48
  const GAP = 12

  // Position dialog relative to button on first open
  const dialogPlaced = useRef(false)
  const prevOpen = useRef(false)
  useEffect(() => {
    if (open && !prevOpen.current && !dialogPlaced.current) {
      dialogPlaced.current = true
      const vw = window.innerWidth
      const vh = window.innerHeight

      const btnLeft = vw - btnDrag.pos.right - BTN_SIZE
      const btnTop = vh - btnDrag.pos.bottom - BTN_SIZE
      const btnCenterX = btnLeft + BTN_SIZE / 2
      const btnCenterY = btnTop + BTN_SIZE / 2

      let dlgLeft: number
      if (btnLeft - GAP - DIALOG_W >= 0) {
        dlgLeft = btnLeft - GAP - DIALOG_W
      } else if (btnLeft + BTN_SIZE + GAP + DIALOG_W <= vw) {
        dlgLeft = btnLeft + BTN_SIZE + GAP
      } else {
        dlgLeft = Math.max(
          0,
          Math.min(btnCenterX - DIALOG_W / 2, vw - DIALOG_W),
        )
      }

      let dlgTop: number
      if (btnCenterY + DIALOG_H <= vh) {
        dlgTop = Math.max(0, btnCenterY - 40)
      } else {
        dlgTop = Math.max(0, vh - DIALOG_H)
      }

      dialogDrag.setPos({
        right: Math.max(0, vw - dlgLeft - DIALOG_W),
        bottom: Math.max(0, vh - dlgTop - DIALOG_H),
      })
    }
    prevOpen.current = open
  }, [open, btnDrag.pos, dialogDrag])

  return (
    <>
      {/* Floating button */}
      <button
        ref={btnRef}
        type="button"
        onPointerDown={btnDrag.onPointerDown}
        className={cn(
          "group fixed z-50 flex items-center justify-center size-12 rounded-full cursor-grab active:cursor-grabbing select-none touch-none",
          "bg-gradient-to-br from-primary/20 to-blue-600/20 border border-primary/40",
          "shadow-[0_0_20px] shadow-primary/25 hover:shadow-[0_0_30px] hover:shadow-primary/45",
          "transition-shadow duration-300",
        )}
        style={{ right: btnDrag.pos.right, bottom: btnDrag.pos.bottom }}
        title={t("evolution.aiChat.title")}
      >
        {showIdleAnimation && (
          <>
            <span className="absolute inset-0 rounded-full animate-ping bg-primary/10 duration-[2000ms] opacity-0 group-hover:opacity-100 transition-opacity" />
            <span className="absolute inset-[-3px] rounded-full animate-[spin_8s_linear_infinite] bg-[conic-gradient(from_0deg,transparent_0%,color-mix(in_srgb,var(--primary)_30%,transparent)_25%,transparent_50%)] opacity-0 group-hover:opacity-100 transition-opacity" />
          </>
        )}
        <Sparkles className="size-5 text-primary drop-shadow-[0_0_6px] relative z-10" />
      </button>

      {/* Chat dialog */}
      {open && (
        <div
          ref={dialogRef}
          className="fixed z-50 flex flex-col rounded-xl overflow-hidden
            border border-primary/25 shadow-[0_0_40px] shadow-primary/15
            backdrop-blur-md bg-card/95"
          style={{
            right: dialogDrag.pos.right,
            bottom: dialogDrag.pos.bottom,
            width: DIALOG_W,
            height: DIALOG_H,
          }}
        >
          {/* Header */}
          <div
            onPointerDown={dialogDrag.onPointerDown}
            className="shrink-0 flex flex-col gap-2 px-4 py-3 cursor-grab active:cursor-grabbing select-none touch-none
              border-b border-primary/15"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in srgb, var(--primary) 8%, transparent) 0%, color-mix(in srgb, var(--primary) 5%, transparent) 100%)",
            }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 pointer-events-none min-w-0">
                <span className="flex items-center justify-center size-7 rounded-lg bg-primary/10 border border-primary/25 shrink-0">
                  <Sparkles className="size-3.5 text-primary" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {t("evolution.aiChat.title")}
                  </p>
                  <p
                    className="text-[10px] text-muted-foreground truncate"
                    title={taskName}
                  >
                    {taskName}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => setOpen(false)}
                className="pointer-events-auto p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-accent/40 transition-colors shrink-0"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="pointer-events-auto flex items-center gap-2 flex-wrap">
              <FloatingProviderButton
                providerId={providerId}
                modelName={modelName}
                providers={providerList}
                defaultHint={otherHint}
                availableModels={availableModels}
                open={modelSettingsOpen}
                onToggle={() => setModelSettingsOpen((v) => !v)}
                onProviderChange={handleProviderChange}
                onModelChange={setModelName}
              />
              <FloatingTargetStage
                value={targetStage}
                onChange={setTargetStage}
                disabled={readOnly || isGenerating}
                t={t}
              />
            </div>
          </div>

          {readOnly && (
            <div className="shrink-0 flex items-center gap-1.5 px-4 py-1.5 border-b border-amber-500/30 bg-amber-500/5 text-[10px] text-amber-600 dark:text-amber-400">
              <Eye className="size-3 shrink-0" />
              <span>{t("evolution.aiChat.readOnlyHint")}</span>
            </div>
          )}

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
            {isSessionFetching && messages.length === 0 && (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            )}
            {messages.map((msg) => (
              <FloatingMessageRow
                key={msg.id}
                msg={msg}
                taskId={taskId}
                isGenerating={isGenerating}
                disabled={readOnly}
                onRetry={
                  !readOnly &&
                  (msg.turnStatus === "failed" ||
                    msg.turnStatus === "cancelled") &&
                  msg.turnId
                    ? () => handleRetry(msg.turnId!)
                    : undefined
                }
                onCardSubmit={handleCardSubmit}
                onPayloadUpdate={handlePayloadUpdate}
                onFocusComposer={handleFocusComposer}
                t={t}
              />
            ))}
            <div
              className="transition-[height] duration-300 ease-out"
              style={{ height: isGenerating ? 80 : 0 }}
            />
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <div
            className="shrink-0 px-3 pb-3 pt-2"
            style={{
              background:
                "linear-gradient(to top, color-mix(in srgb, var(--primary) 5%, var(--card)) 0%, transparent 100%)",
            }}
          >
            <div
              className={cn(
                "rounded-xl border-2 backdrop-blur-md transition-all duration-200 ease-out",
                composerFocused
                  ? "shadow-lg shadow-primary/15 border-primary/40 ring-1 ring-primary/20 bg-card"
                  : "shadow-md shadow-primary/8 border-primary/20 bg-card/95 hover:border-primary/30",
              )}
            >
              <div className="px-3 pt-2 pb-1">
                <textarea
                  ref={floatingTextareaRef}
                  value={input}
                  maxLength={INPUT_LIMITS.chatMessage}
                  onChange={(e) => setInput(e.target.value)}
                  onFocus={() => setComposerFocused(true)}
                  onBlur={() => setComposerFocused(false)}
                  onKeyDown={(e) =>
                    handleComposerEnter(e, input, setInput, handleSend)
                  }
                  placeholder={
                    readOnly
                      ? t("evolution.aiChat.readOnlyPlaceholder")
                      : isGenerating
                        ? t("evolution.chatTune.composerGenerating")
                        : t("evolution.aiChat.inputPlaceholder")
                  }
                  disabled={readOnly || isGenerating}
                  className={cn(
                    "w-full resize-none border-0 bg-transparent text-xs leading-relaxed min-h-[28px]",
                    "placeholder:text-muted-foreground/60 focus:outline-none focus-visible:ring-0",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    "transition-[height] duration-150 ease-out overflow-y-hidden",
                  )}
                />
              </div>
              <div className="flex items-center justify-between px-2 pb-2">
                <div className="flex items-center gap-1">
                  {input.length > 0 && (
                    <span className="text-[9px] text-muted-foreground/40 tabular-nums pl-1 animate-in fade-in duration-150">
                      {input.length}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5">
                  {composerFocused && input.trim() && !isGenerating && (
                    <kbd className="inline-flex items-center px-1 py-0.5 rounded border border-border/30 bg-muted/30 text-[8px] text-muted-foreground/40 animate-in fade-in duration-150">
                      <CornerDownLeft className="size-2" />
                    </kbd>
                  )}
                  {isGenerating ? (
                    <button
                      type="button"
                      onClick={handleStop}
                      className="size-7 flex items-center justify-center rounded-lg
                        bg-destructive/10 border border-destructive/30 text-destructive
                        hover:bg-destructive/20 transition-colors"
                    >
                      <Square className="size-3 animate-pulse fill-current" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={handleSend}
                      disabled={readOnly || !input.trim() || isSending}
                      className={cn(
                        "size-7 flex items-center justify-center rounded-lg transition-all duration-200",
                        !readOnly && input.trim() && !isSending
                          ? "bg-primary text-primary-foreground shadow-sm shadow-primary/20 hover:shadow-md hover:shadow-primary/30 hover:scale-105 active:scale-95"
                          : "bg-primary/10 text-primary/30 cursor-not-allowed",
                      )}
                    >
                      <Send className="size-3" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/* ─────────────────── Message row ──────────────────── */

function FloatingMessageRow({
  msg,
  taskId,
  isGenerating,
  disabled,
  onRetry,
  onCardSubmit,
  onPayloadUpdate,
  onFocusComposer,
  t,
}: {
  msg: FloatingMessage
  taskId: string
  isGenerating: boolean
  disabled?: boolean
  onRetry?: () => void
  onCardSubmit: (
    card: AssistantCard,
    messageId: string,
    submission: CardSubmission,
  ) => void
  onPayloadUpdate: (messageId: string, payload: Record<string, unknown>) => void
  onFocusComposer: () => void
  t: (key: string) => string
}) {
  const isUser = msg.role === "user"
  const isSystem = msg.role === "system"
  const isFailed = msg.turnStatus === "failed"
  const isCancelled = msg.turnStatus === "cancelled"
  const cardDisabled = !!msg.submission || isGenerating || !!disabled
  // Hide the [OPTIONS_START]...[OPTIONS_END] block: its choices are already shown
  // in the payload card below. User text never carries these markers.
  const displayContent = isUser ? msg.content : stripOptionsBlock(msg.content)
  const mermaidIdPrefix = useId()
  const markdownComponents = useMemo(
    () =>
      makeMarkdownComponents(mermaidIdPrefix, {
        streaming: msg.isStreaming,
      }),
    [mermaidIdPrefix, msg.isStreaming],
  )

  return (
    <div
      className={cn(
        "flex flex-col gap-1.5",
        isUser ? "items-end" : "items-start",
      )}
    >
      {(displayContent || !msg.card) && (
        <div
          className={cn(
            "max-w-[85%] px-3 py-2 rounded-lg text-xs leading-relaxed [overflow-wrap:anywhere]",
            isUser
              ? "bg-primary/15 text-primary border border-primary/30 whitespace-pre-wrap"
              : isSystem
                ? "bg-muted/50 text-muted-foreground border border-dashed border-muted-foreground/30"
                : "bg-accent text-foreground border border-border/40 min-h-[36px] flex flex-col justify-center",
          )}
        >
          {isUser ? (
            displayContent
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none [overflow-wrap:anywhere] prose-p:my-0.5 prose-p:leading-relaxed prose-pre:my-1.5 prose-pre:rounded-lg prose-pre:text-xs prose-pre:leading-relaxed prose-pre:bg-code-block-bg prose-pre:text-foreground prose-pre:border prose-pre:border-border/50 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
              <Markdown
                remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                components={markdownComponents}
              >
                {displayContent || (msg.isStreaming ? "" : " ")}
              </Markdown>
            </div>
          )}
          {msg.isStreaming && !displayContent && (
            <div className="flex items-center gap-1 py-0.5">
              <span className="size-1 rounded-full bg-primary/60 animate-pulse" />
              <span className="size-1 rounded-full bg-primary/60 animate-pulse [animation-delay:120ms]" />
              <span className="size-1 rounded-full bg-primary/60 animate-pulse [animation-delay:240ms]" />
            </div>
          )}
        </div>
      )}
      {!isUser && msg.card && (
        <div className="w-full">
          <CardRenderer
            card={msg.card}
            messageId={msg.id}
            submission={msg.submission}
            disabled={cardDisabled}
            compactMode
            taskId={taskId}
            onSubmit={onCardSubmit}
            onPayloadUpdate={onPayloadUpdate}
            onFocusComposer={onFocusComposer}
          />
        </div>
      )}
      {!isUser && (isFailed || isCancelled) && (
        <div className="flex flex-wrap items-start gap-x-2 gap-y-1 max-w-[85%]">
          <div
            className={cn(
              "inline-flex items-start gap-1 text-[10px] min-w-0 flex-1",
              isFailed ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {isFailed ? (
              <AlertTriangle className="size-2.5 mt-0.5 shrink-0" />
            ) : (
              <Ban className="size-2.5 mt-0.5 shrink-0" />
            )}
            {isFailed ? (
              <ExpandableError
                text={msg.error || t("evolution.chatTune.failed")}
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
              className="inline-flex items-center gap-1 shrink-0 whitespace-nowrap
                h-5 px-1.5 rounded text-[10px] font-medium
                border border-primary/30 bg-primary/5 text-primary
                hover:bg-primary/10 hover:border-primary/50 transition-colors"
            >
              <RefreshCw className="size-2.5" />
              <span>{t("evolution.chatTune.retry")}</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/* ─────────────────── Target stage selector ──────────────────── */

function FloatingTargetStage({
  value,
  onChange,
  disabled,
  t,
}: {
  value: ChatTuneActiveStage | null
  onChange: (v: ChatTuneActiveStage | null) => void
  disabled: boolean
  t: (key: string) => string
}) {
  const [open, setOpen] = useState(false)
  const current =
    TARGET_STAGE_OPTIONS.find((o) => o.value === value) ??
    TARGET_STAGE_OPTIONS[0]

  return (
    <Popover open={open} onOpenChange={(v) => !disabled && setOpen(v)}>
      <PopoverTrigger asChild>
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          disabled={disabled}
          title={t("evolution.chatTune.targetStage.label")}
          className={cn(
            "flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors",
            value !== null
              ? "bg-primary/10 border border-primary/30 text-primary"
              : "bg-muted/80 border border-border/50 text-muted-foreground hover:border-primary/40",
            disabled && "opacity-60 cursor-not-allowed",
          )}
        >
          <Target className="size-3 shrink-0" />
          <span className="truncate max-w-[80px]">
            {t(`evolution.chatTune.targetStage.${current.key}`)}
          </span>
          <ChevronDown
            className={cn(
              "size-2.5 shrink-0 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[220px] p-1 z-[60]"
        align="start"
        side="bottom"
        sideOffset={4}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider px-2 py-1">
          {t("evolution.chatTune.targetStage.label")}
        </p>
        {TARGET_STAGE_OPTIONS.map((opt) => {
          const isSel = opt.value === value
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => {
                onChange(opt.value)
                setOpen(false)
              }}
              className={cn(
                "w-full flex flex-col items-start gap-0.5 px-2 py-1.5 rounded-md text-left transition-colors",
                isSel ? "bg-primary/10" : "hover:bg-accent",
              )}
            >
              <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                <Check
                  className={cn("size-3 text-primary", !isSel && "opacity-0")}
                />
                {t(`evolution.chatTune.targetStage.${opt.key}`)}
              </span>
              <span className="ml-[18px] text-[10px] text-muted-foreground leading-snug">
                {t(`evolution.chatTune.targetStage.${opt.key}Desc`)}
              </span>
            </button>
          )
        })}
      </PopoverContent>
    </Popover>
  )
}

/* ─────────────────── Provider selector (floating) ──────────────────── */

function FloatingProviderButton({
  providerId,
  modelName,
  providers,
  defaultHint,
  availableModels,
  open,
  onToggle,
  onProviderChange,
  onModelChange,
}: {
  providerId: string
  modelName: string
  providers: ProviderResponse[]
  defaultHint: string
  availableModels: string[]
  open: boolean
  onToggle: () => void
  onProviderChange: (id: string) => void
  onModelChange: (v: string) => void
}) {
  const { t } = useTranslation()
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onToggle()
      }
    }
    document.addEventListener("pointerdown", handler)
    return () => document.removeEventListener("pointerdown", handler)
  }, [open, onToggle])

  const label =
    providerId && providerId !== "default"
      ? (providers.find((p) => p.id === providerId)?.name ?? providerId)
      : ""

  return (
    <div ref={panelRef} className="relative">
      <button
        type="button"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={onToggle}
        className={cn(
          "flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors",
          providerId && providerId !== "default"
            ? "bg-primary/10 border border-primary/30 text-primary"
            : "bg-muted/80 border border-border/50 text-muted-foreground hover:border-primary/40",
        )}
      >
        <Settings2 className="size-3 shrink-0" />
        <span className="truncate max-w-[100px]">
          {label || t("evolution.chatTune.defaultProvider")}
        </span>
        <ChevronDown
          className={cn(
            "size-2.5 shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          className="absolute top-full left-0 mt-1 w-[240px] rounded-lg border border-border/60 bg-popover shadow-lg z-50 p-2 space-y-2"
          onPointerDown={(e) => e.stopPropagation()}
        >
          <div className="space-y-0.5">
            <p className="text-[10px] font-medium text-muted-foreground px-1">
              {t("evolution.chatTune.selectProvider")}
            </p>
            {[
              {
                id: "default",
                label: defaultHint || t("evolution.chatTune.defaultProvider"),
                badge: "DEFAULT",
                badgeClass:
                  "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
              },
              {
                id: "mock",
                label: t("evolution.chatTune.mockProvider"),
                badge: "MOCK",
                badgeClass:
                  "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
              },
              ...providers.map((p) => ({
                id: p.id,
                label: p.name,
                badge: p.type,
                badgeClass: "",
              })),
            ].map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onProviderChange(item.id)}
                className={cn(
                  "w-full flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-colors",
                  providerId === item.id
                    ? "bg-primary/10 text-primary"
                    : "text-foreground hover:bg-accent/60",
                )}
              >
                <Check
                  className={cn(
                    "size-3 shrink-0",
                    providerId === item.id ? "opacity-100" : "opacity-0",
                  )}
                />
                {item.badgeClass ? (
                  <Badge
                    variant="secondary"
                    className={cn(
                      "text-[9px] font-medium px-1 py-0",
                      item.badgeClass,
                    )}
                  >
                    {item.badge}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[9px] font-normal">
                    {item.badge}
                  </Badge>
                )}
                <span className="truncate">{item.label}</span>
              </button>
            ))}
          </div>

          {!isSpecialProvider(providerId) && (
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-muted-foreground px-1">
                {t("evolution.chatTune.modelPlaceholder")}
              </p>
              {availableModels.length > 0 ? (
                <div className="space-y-0.5">
                  {availableModels.map((model) => (
                    <button
                      key={model}
                      type="button"
                      onClick={() => onModelChange(model)}
                      className={cn(
                        "w-full flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-colors",
                        modelName === model
                          ? "bg-primary/10 text-primary"
                          : "text-foreground hover:bg-accent/60",
                      )}
                    >
                      <Check
                        className={cn(
                          "size-3 shrink-0",
                          modelName === model ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <span className="truncate">{model}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <input
                  type="text"
                  value={modelName}
                  maxLength={INPUT_LIMITS.name}
                  onChange={(e) => onModelChange(e.target.value)}
                  placeholder={t("evolution.chatTune.modelPlaceholder")}
                  className="w-full px-2 py-1 text-xs rounded border border-input bg-background focus:outline-none focus:border-primary/50"
                />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ─────────────────── File Helpers ──────────────────── */

function apiToFloatingMessage(msg: ChatTuneMessageItem): FloatingMessage {
  const result: FloatingMessage = {
    id: msg.id,
    turnId: msg.turn_id,
    role: msg.role,
    content: msg.content || "",
    isStreaming: msg.turn_status === "generating",
    turnStatus: msg.turn_status,
    error: msg.error,
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
