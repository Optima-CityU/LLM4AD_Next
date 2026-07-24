import { useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronUp,
  Loader2,
  MessageSquareDashed,
  Pencil,
  Play,
  Rocket,
  Sparkles,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type {
  ResearchMessageItem,
  ResearchMode,
  ResearchSessionItem,
} from "@/client"
import {
  researchKeys,
  useResearchSessionDetail,
  useResearchSessionMessages,
  useResearchState,
  useRetryResearchTurn,
  useStartCollab,
  useStartResearchTurn,
  useStopResearchTurn,
  useUpdateResearchSession,
} from "@/hooks/useAutoResearch"
import { useAutoResearchHeader } from "@/hooks/useAutoResearchHeader"
import {
  type ResearchStreamEvent,
  useResearchStream,
} from "@/hooks/useResearchStream"
import { cn } from "@/lib/utils"

import BottomComposer, { type RunOverrides } from "./BottomComposer"
import MessageItem from "./MessageItem"
import { StageProgressBar } from "./StageProgress"
import type { StreamLogEntry } from "./StreamLogConsole"
import TurnLogPanel from "./TurnLogPanel"

interface Props {
  session: ResearchSessionItem | null
  onCreateSession: () => void
}

/** 稳定空数组：非活跃轮的 liveLogs，避免每次 render 生成新引用触发子组件重算。 */
const EMPTY_LOGS: StreamLogEntry[] = []

/**
 * 会话主区：Header（会话元信息 + 运行控制）+ 消息列表 + 实时日志 + 输入框。
 *
 * 消息历史走 ``useResearchSessionMessages`` 无限分页（越翻越旧）；实时性来自
 * SSE：关键事件到达即 invalidate messages/state/artifacts，三面板同步刷新。
 */
export default function ChatPanel({ session, onCreateSession }: Props) {
  const { t } = useTranslation()

  if (!session) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-5 text-muted-foreground">
        <div
          className="relative p-6 rounded-full"
          style={{
            background:
              "radial-gradient(circle, color-mix(in srgb, var(--primary) 10%, transparent) 0%, transparent 70%)",
          }}
        >
          <Sparkles
            className="size-14 text-primary/50"
            style={{
              filter:
                "drop-shadow(0 0 16px color-mix(in srgb, var(--primary) 30%, transparent))",
            }}
          />
          <div
            className="absolute inset-0 rounded-full animate-pulse opacity-30"
            style={{
              border:
                "1px solid color-mix(in srgb, var(--primary) 30%, transparent)",
            }}
          />
        </div>
        <div className="text-center space-y-1.5">
          <p className="text-base font-semibold text-foreground/80">
            {t("autoResearch.chat.welcomeTitle")}
          </p>
          <p className="text-xs text-muted-foreground/70 max-w-md">
            {t("autoResearch.chat.welcomeHint")}
          </p>
        </div>
        <button
          type="button"
          onClick={onCreateSession}
          className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 text-primary px-4 py-2 text-xs font-medium hover:bg-primary/20 shadow-[0_0_12px] shadow-primary/20 transition-all"
        >
          <Sparkles className="size-3.5" />
          {t("autoResearch.sidebar.newResearch")}
        </button>
      </div>
    )
  }
  return <ChatPanelInner session={session} />
}

function ChatPanelInner({ session }: { session: ResearchSessionItem }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const detail = useResearchSessionDetail(session.id, {
    // SSE 实时推送，不需要轮询
    refetchInterval: false,
  })
  const activeTurn = detail.data?.active_turn ?? null
  const activeTurnId = activeTurn?.id ?? session.active_turn_id ?? null

  const startMut = useStartResearchTurn()
  const stopMut = useStopResearchTurn()
  const retryMut = useRetryResearchTurn()
  const collabMut = useStartCollab()

  // 当前活跃的 collab turn（agent 一轮）。startCollab 返回后置入，用于订阅其 SSE；
  // 收到 done 后清空。与 pipeline turn 独立并存。
  const [collabTurnId, setCollabTurnId] = useState<string | null>(null)
  // collab agent 流式回复的实时拼接文本（本轮内存，done 后由 messages 接管）。
  const [collabStreamText, setCollabStreamText] = useState("")
  const [collabToolHint, setCollabToolHint] = useState<string | null>(null)

  // 顶部常驻进度条的数据源（22 阶段快照）。SSE 实时推送，不需要轮询。
  const stateQ = useResearchState(session.id, {
    refetchInterval: false,
  })
  const displayStages = stateQ.data?.stages ?? []

  // 运行配置（provider / model / mode / 起始阶段）提升到此，让底部运行工具行与
  // 顶部阶段轨的「从此步运行」共用同一份参数——从阶段点运行 == 设好起始阶段再点运行。
  const [runProvider, setRunProvider] = useState(session.provider_id ?? "")
  const [runModel, setRunModel] = useState(session.model_name ?? "")
  const [runMode, setRunMode] = useState<ResearchMode>(
    (session.mode as ResearchMode) ?? "co-pilot",
  )
  const [runFromStage, setRunFromStage] = useState("")
  // 切换会话时重置运行配置：默认从最后一个允许的阶段开始。显式带上 session.id，
  // 即便新旧会话 provider/model/mode 相同也要重置起始阶段（否则会串上一个会话）。
  // biome-ignore lint/correctness/useExhaustiveDependencies: session.id 用于会话切换重置
  useEffect(() => {
    setRunProvider(session.provider_id ?? "")
    setRunModel(session.model_name ?? "")
    setRunMode((session.mode as ResearchMode) ?? "co-pilot")
    setRunFromStage(
      displayStages.length > 0
        ? String(displayStages[displayStages.length - 1].stage)
        : "",
    )
  }, [
    session.id,
    session.provider_id,
    session.model_name,
    session.mode,
    displayStages,
  ])

  // 实时消息叠加层：SSE 持久化事件按 event_key upsert，避免等待 API refetch。
  const [liveMessages, setLiveMessages] = useState<ResearchMessageItem[]>([])

  // ── 消息历史：两个独立分页查询，各自翻页、互不饥饿 ──────────────────
  // 主消息列表排除 log（保留对话与里程碑），避免长跑时成百上千 log 把对话挤到
  // 分页深处；每轮日志改由内联折叠的 TurnLogPanel 按 turn 懒加载。日志面板
  // （底部抽屉）仍走 logQ 独立查询做全局总览。
  const msgQ = useResearchSessionMessages(session.id, {
    pageSize: 200,
    kind: "chat",
    excludeEventType: ["log"],
  })
  const logQ = useResearchSessionMessages(session.id, {
    pageSize: 200,
    kind: "log",
    eventType: ["log"],
  })

  // 切换 turn 时清空实时消息
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在切换 turn 时清空
  useEffect(() => {
    setLiveMessages([])
  }, [activeTurnId])

  const messages = useMemo(() => {
    const pages = msgQ.data?.pages ?? []
    // pages[0] 最新页、pages[n] 更旧页；反转后拼接 = 升序全量
    const flat: ResearchMessageItem[] = []
    for (let i = pages.length - 1; i >= 0; i--) {
      for (const m of pages[i].messages ?? []) flat.push(m)
    }
    const messageMap = new Map<string, ResearchMessageItem>()
    for (const message of flat) {
      const key = message.event_key
        ? `${message.turn_id}:${message.event_key}`
        : `${message.turn_id}:id:${message.id}`
      messageMap.set(key, message)
    }
    for (const liveMsg of liveMessages) {
      const key = liveMsg.event_key
        ? `${liveMsg.turn_id}:${liveMsg.event_key}`
        : `${liveMsg.turn_id}:id:${liveMsg.id}`
      messageMap.set(key, { ...messageMap.get(key), ...liveMsg })
    }
    return [...messageMap.values()].sort((a, b) =>
      `${a.created_time}:${a.id}`.localeCompare(`${b.created_time}:${b.id}`),
    )
  }, [msgQ.data, liveMessages])

  // 当前待回复的门控 form 消息：paused 时最后一条未锁定的 form。移到底部
  // GatePanel 操作；消息流里不再重复渲染这一条（见 renderedMessages）。
  const gateMessage = useMemo(() => {
    if (session.status !== "paused") return null
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      const kind = (m.payload as { kind?: string } | null)?.kind
      if (kind === "form" && !m.payload_locked) return m
    }
    return null
  }, [messages, session.status])

  // 活跃门控从消息流里剔除（它由底部 GatePanel 承载操作，避免重复展示）。
  const renderedMessages = useMemo(
    () =>
      gateMessage ? messages.filter((m) => m.id !== gateMessage.id) : messages,
    [messages, gateMessage],
  )

  const LOG_CAP = 500
  const [streamLogs, setStreamLogs] = useState<StreamLogEntry[]>([])
  const logSeqRef = useRef(0)
  // SSE connected 帧确认的 turn_id（可能多次 connected，始终取最新值）。
  // 用于 onEvent 归入正确分组，比 REST activeTurnId 更及时。
  const sseTurnIdRef = useRef<string | null>(null)
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在切换 turn 时清空
  useEffect(() => {
    setStreamLogs([])
    logSeqRef.current = 0
    sseTurnIdRef.current = null
  }, [activeTurnId])

  // 按 turn 分组（仅非 log 消息；log 由各 turn 的 TurnLogPanel 懒加载）。Map 保序，
  // renderedMessages 已按 created_time:id 升序，故组顺序即 turn 时序，末组为最新轮。
  const groupedMessages = useMemo(() => {
    const groups = new Map<
      string,
      { turnId: string; messages: ResearchMessageItem[] }
    >()
    for (const message of renderedMessages) {
      let group = groups.get(message.turn_id)
      if (!group) {
        group = { turnId: message.turn_id, messages: [] }
        groups.set(message.turn_id, group)
      }
      group.messages.push(message)
    }
    // 活跃轮即便暂无非 log 消息，也保底建组，让 TurnLogPanel 承载实时日志 tail。
    if (activeTurnId && !groups.has(activeTurnId)) {
      groups.set(activeTurnId, { turnId: activeTurnId, messages: [] })
    }
    return [...groups.values()]
  }, [activeTurnId, renderedMessages])

  const lastTurnId = groupedMessages[groupedMessages.length - 1]?.turnId ?? null

  // ── SSE：关键事件到达时按类型分流失效相关查询 ─────────────────────────
  const sseEnabled =
    !!activeTurnId &&
    (session.status === "running" || session.status === "paused")

  // 去抖合并突发失效（evolution_step 在 stage 12 可能高频到达）。按需累积要
  // 失效的 query key 组，一个 tick 内合并成一次 invalidate。
  const pendingRef = useRef<Set<string>>(new Set())
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flushInvalidations = useCallback(() => {
    const groups = pendingRef.current
    pendingRef.current = new Set()
    flushTimerRef.current = null
    if (groups.has("detail"))
      qc.invalidateQueries({ queryKey: researchKeys.sessionDetail(session.id) })
    if (groups.has("messages"))
      qc.invalidateQueries({
        queryKey: researchKeys.sessionMessages(session.id),
        // 匹配所有 kind（chat + log）
        exact: false,
      })
    if (groups.has("state"))
      qc.invalidateQueries({ queryKey: researchKeys.state(session.id) })
    if (groups.has("artifacts")) {
      qc.invalidateQueries({ queryKey: researchKeys.artifacts(session.id) })
      qc.invalidateQueries({ queryKey: researchKeys.artifactTree(session.id) })
    }
  }, [qc, session.id])
  const scheduleInvalidate = useCallback(
    (groups: string[]) => {
      for (const g of groups) pendingRef.current.add(g)
      if (flushTimerRef.current) return
      flushTimerRef.current = setTimeout(flushInvalidations, 300)
    },
    [flushInvalidations],
  )
  // 全量失效（终止事件时用）：立即，不走去抖。
  const invalidateAll = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    pendingRef.current = new Set()
    qc.invalidateQueries({ queryKey: researchKeys.sessionDetail(session.id) })
    qc.invalidateQueries({
      queryKey: researchKeys.sessionMessages(session.id),
      exact: false,
    })
    qc.invalidateQueries({ queryKey: researchKeys.state(session.id) })
    qc.invalidateQueries({ queryKey: researchKeys.artifacts(session.id) })
    qc.invalidateQueries({ queryKey: researchKeys.artifactTree(session.id) })
  }, [qc, session.id])

  /** 按事件类型决定要失效哪些查询组，避免高频事件全量失效。 */
  const invalidateForEvent = useCallback(
    (type: string) => {
      switch (type) {
        case "stage_transition":
          // 阶段转场：立即刷新 state（更新顶部进度条），延迟刷新 detail
          qc.invalidateQueries({ queryKey: researchKeys.state(session.id) })
          scheduleInvalidate(["detail"])
          break
        case "evolution_step":
          // 新解只更新状态快照与消息里程碑，不动产物树
          scheduleInvalidate(["state", "messages"])
          break
        case "artifact_ready":
          scheduleInvalidate(["artifacts", "messages"])
          break
        case "waiting_for_input":
          // 进 gate：需要立即刷新 detail（turn 变 paused_gate、session 变 paused）
          // + 消息拿到表单
          scheduleInvalidate(["detail", "messages", "state"])
          break
        default: // llm4ad_final_state 等
          scheduleInvalidate(["state", "detail", "messages"])
      }
    },
    [scheduleInvalidate, qc, session.id],
  )

  // 从持久化日志消息还原历史日志（logQ 只拉 event_type==="log"）。log 事件
  // 持久化时 payload 即事件本体，含 level/message/module/source/ts，与实时 SSE
  // 同源。日志走独立分页查询，不再受对话消息分页影响。
  const historyLogs = useMemo<StreamLogEntry[]>(() => {
    const pages = logQ.data?.pages ?? []
    const out: StreamLogEntry[] = []
    // pages[0] 最新页、pages[n] 更旧页；反转拼接 = 升序
    for (let i = pages.length - 1; i >= 0; i--) {
      for (const m of pages[i].messages ?? []) {
        const p = (m.payload ?? {}) as Record<string, unknown>
        out.push({
          id: `h:${m.id}`,
          eventKey: m.event_key || undefined,
          level: (p.level as string) || "INFO",
          message: (p.message as string) ?? m.content ?? "",
          module: (p.module as string | undefined) ?? undefined,
          source: (p.source as string | undefined) ?? undefined,
          ts: (p.ts as string | undefined) ?? m.created_time,
        })
      }
    }
    return out
  }, [logQ.data])

  // 历史 + 实时按 event_key 合并；没有 event_key 的旧数据才退回 ts+message。
  const mergedLogs = useMemo<StreamLogEntry[]>(() => {
    const seen = new Set<string>()
    const out: StreamLogEntry[] = []
    const push = (e: StreamLogEntry) => {
      const sig = e.eventKey ?? `${e.ts ?? ""}|${e.message}`
      if (seen.has(sig)) return
      seen.add(sig)
      out.push(e)
    }
    for (const e of historyLogs) push(e)
    for (const e of streamLogs) push(e)
    return out.length > LOG_CAP ? out.slice(-LOG_CAP) : out
  }, [historyLogs, streamLogs])

  // 卸载时清掉去抖定时器，避免对已卸载组件触发 invalidate
  useEffect(() => {
    return () => {
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current)
    }
  }, [])

  useResearchStream(
    session.id,
    activeTurnId,
    {
      onConnected: (connectedTurnId) => {
        sseTurnIdRef.current = connectedTurnId
      },
      onKeyEvent: (evt: ResearchStreamEvent) => {
        invalidateForEvent(evt.type)
      },
      onDone: () => {
        invalidateAll()
        // 覆盖所有会话列表（文件夹分页 / 搜索分页 / 旧扁平）与文件夹计数
        qc.invalidateQueries({ queryKey: researchKeys.all })
      },
      onError: (msg) => {
        if (msg && msg !== "idle_timeout") {
          // eslint-disable-next-line no-console
          console.debug("[research SSE]", msg)
        }
      },
      onEvent: (evt: ResearchStreamEvent) => {
        // SSE connected 帧确认的 turn_id 优先；回退到 REST activeTurnId。
        const currentTurnId = sseTurnIdRef.current ?? activeTurnId
        // log 不进 liveMessages：实时由带上限的 streamLogs 承载、历史由 msgQ 覆盖，
        // 这里再收会让 liveMessages 无上限膨胀（首连 0-0 全量重放时尤甚）。
        if (evt.event_key && evt.type !== "log" && currentTurnId) {
          const nowIso = evt.ts ?? new Date().toISOString()
          const eventPayload = { ...evt }
          delete eventPayload._streamId
          setLiveMessages((prev) => {
            const key = `${currentTurnId}:${evt.event_key}`
            const existing = prev.find(
              (message) =>
                `${message.turn_id}:${message.event_key}` === key,
            )
            const liveMessage: ResearchMessageItem = {
              id: existing?.id ?? `live:${key}`,
              session_id: session.id,
              turn_id: currentTurnId,
              role: "system",
              content: (evt.message as string) ?? "",
              turn_status: "running",
              error: null,
              payload: eventPayload,
              payload_locked: false,
              payload_locked_at: null,
              payload_submission: null,
              stage: evt.stage ?? null,
              event_type: evt.type,
              event_key: evt.event_key as string,
              created_time: nowIso,
              updated_time: nowIso,
            }
            return existing
              ? prev.map((message) =>
                  message.id === existing.id ? liveMessage : message,
                )
              : [...prev, liveMessage]
          })
        }
        if (evt.type !== "log") return
        const entry: StreamLogEntry = {
          id: `l:${++logSeqRef.current}`,
          eventKey: evt.event_key as string | undefined,
          level: (evt.level as string) || "INFO",
          message: (evt.message as string) || "",
          module: (evt.module as string | undefined) ?? undefined,
          source: (evt.source as string | undefined) ?? undefined,
          ts: evt.ts,
        }
        setStreamLogs((prev) => {
          const next =
            prev.length >= LOG_CAP ? prev.slice(-(LOG_CAP - 1)) : prev
          return [...next, entry]
        })
      },
    },
    { enabled: sseEnabled },
  )

  // ── collab agent 的 SSE：独立于 pipeline turn，订阅活跃 collab turn ──────
  useResearchStream(
    session.id,
    collabTurnId,
    {
      onDone: () => {
        setCollabTurnId(null)
        setCollabStreamText("")
        setCollabToolHint(null)
        qc.invalidateQueries({
          queryKey: researchKeys.sessionMessages(session.id),
        })
        qc.invalidateQueries({ queryKey: researchKeys.artifacts(session.id) })
        qc.invalidateQueries({
          queryKey: researchKeys.artifactTree(session.id),
        })
        qc.invalidateQueries({
          queryKey: researchKeys.sessionDetail(session.id),
        })
      },
      onError: (msg) => {
        // 不因瞬时网络错误清 collabTurnId：清了会让 enabled 变 false、正在进行的
        // 退避重连被取消（useResearchStream 会先 onError 再重连最多 4 次）。保留
        // turnId 让 hook 自行重连；真正结束由 onDone 清理，idle_timeout 也不清
        // （collab 容器可能仍在跑，靠 messages invalidate 兜底补最终回复）。
        if (msg && msg !== "idle_timeout") {
          // eslint-disable-next-line no-console
          console.debug("[collab SSE]", msg)
        }
      },
      onEvent: (evt: ResearchStreamEvent) => {
        if (evt.type === "collab_message" && typeof evt.delta === "string") {
          setCollabStreamText((prev) => prev + evt.delta)
        } else if (evt.type === "collab_tool") {
          setCollabToolHint((evt.tool as string) || null)
        }
      },
    },
    { enabled: !!collabTurnId },
  )

  // ── 滚动锚点：最新消息 id 变化或 collab 流式输出增长时，若用户已贴近底部则滚动到底 ─
  const scrollAnchor = useRef<HTMLDivElement | null>(null)
  const scrollBox = useRef<HTMLDivElement | null>(null)
  const lastId = messages[messages.length - 1]?.id
  // biome-ignore lint/correctness/useExhaustiveDependencies: 消息 id 或 collab 流式文本变化时触发
  useEffect(() => {
    const box = scrollBox.current
    // 运行中用户上滚回看历史时不强行拽回底部；离底部超过 ~120px 就不自动跟随。
    if (box && box.scrollHeight - box.scrollTop - box.clientHeight > 120) {
      return
    }
    scrollAnchor.current?.scrollIntoView({ behavior: "smooth" })
  }, [lastId, collabStreamText])

  // 切换 session 或初次加载完成时强制滚到底部。
  // biome-ignore lint/correctness/useExhaustiveDependencies: session 切换或加载完成时触发
  useEffect(() => {
    if (msgQ.isLoading) return
    scrollAnchor.current?.scrollIntoView({ behavior: "instant" })
  }, [session.id, msgQ.isLoading])

  // ── 运行控制 ────────────────────────────────────────────────────────
  const toastErr = (err: unknown) => {
    const d =
      (err as { body?: { detail?: string } })?.body?.detail ??
      (err as Error)?.message ??
      "error"
    toast.error(d)
  }

  const handleRun = (overrides: RunOverrides) => {
    startMut.mutate(
      {
        sessionId: session.id,
        body: {
          content: null,
          provider_id: overrides.provider_id ?? null,
          model_name: overrides.model_name ?? null,
          mode: overrides.mode,
          from_stage: overrides.from_stage ?? null,
        } as never,
      },
      { onError: toastErr },
    )
  }

  // 从顶部阶段轨的某一步运行：等价于「把底部起始阶段设为该步 + 点运行」，
  // provider / model / mode 沿用底部运行工具行的当前配置。
  const handleRunFromStage = (stage: number) => {
    setRunFromStage(String(stage))
    handleRun({
      provider_id: runProvider.trim() || undefined,
      model_name: runModel.trim() || undefined,
      mode: runMode,
      from_stage: String(stage),
    })
  }

  const handleCollabSend = (message: string) => {
    setCollabStreamText("")
    setCollabToolHint(null)
    collabMut.mutate(
      { sessionId: session.id, body: { message } },
      {
        onSuccess: (resp) => setCollabTurnId(resp.turn.id),
        onError: toastErr,
      },
    )
  }

  const handleStop = () => {
    // collab 在跑：停 collab turn；否则停 pipeline turn。
    const target = collabTurnId ?? activeTurnId
    if (!target) return
    const isCollab = target === collabTurnId
    stopMut.mutate(
      { sessionId: session.id, turnId: target },
      {
        onSuccess: () => {
          if (isCollab) {
            setCollabTurnId(null)
            setCollabStreamText("")
            setCollabToolHint(null)
          }
        },
        onError: toastErr,
      },
    )
  }

  const handleRetry = () => {
    if (!activeTurnId) return
    retryMut.mutate(
      { sessionId: session.id, turnId: activeTurnId, body: {} },
      { onError: toastErr },
    )
  }

  // 滚动到顶部/底部
  const scrollToTop = () => {
    if (scrollBox.current) {
      scrollBox.current.scrollTo({ top: 0, behavior: "smooth" })
    }
  }

  const scrollToBottom = () => {
    if (scrollBox.current) {
      scrollBox.current.scrollTo({
        top: scrollBox.current.scrollHeight,
        behavior: "smooth",
      })
    }
  }

  const handleFormSubmit = (
    messageId: string,
    submission: Record<string, unknown>,
  ) => {
    startMut.mutate(
      {
        sessionId: session.id,
        body: {
          content: null,
          respond_to_message_id: messageId,
          submission,
          mode: runMode,
        } as never,
      },
      { onError: toastErr },
    )
  }

  const collabBusy = !!collabTurnId || collabMut.isPending
  const busy = startMut.isPending || retryMut.isPending
  const canRetry =
    !!activeTurnId &&
    (session.status === "failed" || session.status === "cancelled")
  // 终态才允许「从某步重跑」（与底部起始阶段选择器的显示条件一致）。
  const terminal =
    session.status === "completed" ||
    session.status === "failed" ||
    session.status === "cancelled"
  // 允许作为起点的阶段集合：即底部起始阶段选择器列出的阶段（真实快照 displayStages），
  // 顶部阶段轨仅对这些阶段显示「从此步运行」按钮，合成的 pending 阶段不显示。
  const runnableStages = useMemo(
    () => new Set(displayStages.map((s) => s.stage)),
    [displayStages],
  )

  // ── 顶栏中间列：注入「当前会话标题」（仅标题，状态/阶段在下方进度轨体现） ──
  const { setHeaderCenter } = useAutoResearchHeader()
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在标题变化时更新
  useEffect(() => {
    setHeaderCenter(
      <span
        className="text-sm font-semibold truncate max-w-[420px] text-foreground/90"
        title={session.title}
      >
        {session.title}
      </span>,
    )
    return () => setHeaderCenter(null)
  }, [session.title])

  // ── 合并显示数据 ──
  // 顶部进度条：始终使用 state 接口的权威数据，不使用 SSE 实时叠加层
  const displayActiveStage = stateQ.data?.active_stage ?? session.active_stage

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 阶段进度轨：随中间对话区顶部（不再全宽 portal，让左右边栏顶到 header 下） */}
      <StageProgressBar
        sessionId={session.id}
        stages={displayStages}
        activeStage={displayActiveStage}
        canRunFromStage={terminal && !busy}
        runnableStages={runnableStages}
        onRunFromStage={handleRunFromStage}
      />

      {/* Messages：relative 容器包裹滚动区，滚动按钮悬浮其上（不随内容滚动） */}
      <div className="relative flex-1 min-h-0">
        <div ref={scrollBox} className="h-full overflow-y-auto">
          {/* 阅读宽度约束：宽屏下消息流居中，行宽随屏幕逐级放宽（小屏舒适、大屏不浪费） */}
          <div className="mx-auto w-full max-w-3xl xl:max-w-4xl 2xl:max-w-5xl">
            {msgQ.hasNextPage && (
              <div className="flex justify-center py-2">
                <button
                  type="button"
                  onClick={() => void msgQ.fetchNextPage()}
                  disabled={msgQ.isFetchingNextPage}
                  className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
                >
                  {msgQ.isFetchingNextPage ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <ChevronUp className="size-3" />
                  )}
                  {t("autoResearch.chat.loadMore")}
                </button>
              </div>
            )}

            {msgQ.isLoading ? (
              <div className="flex items-center justify-center py-10 text-xs text-muted-foreground">
                <Loader2 className="size-4 animate-spin mr-2" />
                {t("autoResearch.chat.loading")}
              </div>
            ) : messages.length === 0 ? (
              <EmptyState session={session} onRun={handleRun} running={busy} />
            ) : (
              <div className="py-3 space-y-1">
                {groupedMessages.map((group) => (
                  <div
                    key={group.turnId}
                    className="group/turn relative pl-3 before:content-[''] before:absolute before:left-0 before:top-0 before:w-1.5 before:h-[2px] before:bg-border/20 before:transition-colors hover:before:bg-primary/50 after:content-[''] after:absolute after:left-0 after:bottom-0 after:w-1.5 after:h-[2px] after:bg-border/20 after:transition-colors hover:after:bg-primary/50"
                  >
                    {/* 左侧竖线：hover 时主色 + 流光从上而下 */}
                    <span
                      aria-hidden
                      className="absolute left-0 top-0 bottom-0 w-[2px] bg-border/20 group-hover/turn:bg-primary/40 transition-colors overflow-hidden"
                    >
                      <span className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-transparent via-primary/70 to-transparent opacity-0 group-hover/turn:opacity-100 group-hover/turn:[animation:flow-down_10s_ease-in-out_infinite]" />
                    </span>
                    {group.messages.map((m) => (
                      <MessageItem
                        key={m.id}
                        message={m}
                        sessionId={session.id}
                        stale
                      />
                    ))}
                    <TurnLogPanel
                      sessionId={session.id}
                      turnId={group.turnId}
                      defaultOpen={group.turnId === lastTurnId}
                      liveLogs={
                        group.turnId === activeTurnId ? streamLogs : EMPTY_LOGS
                      }
                    />
                  </div>
                ))}
                {collabBusy && (
                  <div className="flex justify-start px-4 py-1">
                    <div className="w-full rounded-2xl rounded-tl-sm bg-primary/[0.06] border border-primary/25 px-4 py-2 text-sm whitespace-pre-wrap break-words">
                      {collabStreamText || (
                        <span className="italic opacity-70 inline-flex items-center gap-1.5">
                          <Loader2 className="size-3 animate-spin" />
                          {collabToolHint
                            ? t("autoResearch.collab.usingTool", {
                                tool: collabToolHint,
                              })
                            : t("autoResearch.collab.thinking")}
                        </span>
                      )}
                    </div>
                  </div>
                )}
                <div ref={scrollAnchor} />
              </div>
            )}

            {session.status === "failed" && session.error && (
              <div className="mx-4 my-2 px-3 py-2 border border-destructive/40 bg-destructive/10 text-destructive text-xs rounded-md">
                <AlertTriangle className="size-3 inline mr-1" />
                {session.error}
              </div>
            )}
          </div>
        </div>

        {/* 滚动到顶部/底部按钮：悬浮在消息区右下角，默认半透明不遮挡内容 */}
        {messages.length > 0 && (
          <div className="absolute bottom-4 right-4 flex flex-col gap-1.5 z-10 pointer-events-none">
            <button
              type="button"
              onClick={scrollToTop}
              title={t("autoResearch.chat.scrollToTop", {
                defaultValue: "回到顶部",
              })}
              className="size-7 grid place-items-center rounded-full bg-background/40 backdrop-blur border border-border/50 shadow-md hover:bg-background/90 hover:border-primary/50 transition-all pointer-events-auto"
            >
              <ArrowUp className="size-3.5" />
            </button>
            <button
              type="button"
              onClick={scrollToBottom}
              title={t("autoResearch.chat.scrollToBottom", {
                defaultValue: "回到底部",
              })}
              className="size-7 grid place-items-center rounded-full bg-background/40 backdrop-blur border border-border/50 shadow-md hover:bg-background/90 hover:border-primary/50 transition-all pointer-events-auto"
            >
              <ArrowDown className="size-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* pending 状态不显示底部操作区，由 EmptyState 承载启动入口 */}
      {session.status !== "pending" && (
        <BottomComposer
          session={session}
          gateMessage={gateMessage}
          collabBusy={collabBusy}
          running={session.status === "running"}
          paused={session.status === "paused"}
          sending={busy}
          stages={displayStages}
          canRetry={canRetry}
          provider={runProvider}
          model={runModel}
          mode={runMode}
          fromStage={runFromStage}
          onProviderModelChange={(p, m) => {
            setRunProvider(p)
            setRunModel(m)
          }}
          onModeChange={setRunMode}
          onFromStageChange={setRunFromStage}
          onCollabSend={handleCollabSend}
          onRun={handleRun}
          onStop={handleStop}
          onRetry={handleRetry}
          onGateSubmit={handleFormSubmit}
          logs={mergedLogs}
          logsActive={sseEnabled}
          logsHasMore={logQ.hasNextPage}
          logsFetchingMore={logQ.isFetchingNextPage}
          onLoadMoreLogs={() => void logQ.fetchNextPage()}
        />
      )}
    </div>
  )
}

/**
 * 消息为空时的占位：pending（会话已建、待开跑）与已选会话无消息两种文案，
 * 配图标 + 引导语，避免单薄的一行灰字。pending 状态显示可编辑 topic + 运行按钮。
 */
function EmptyState({
  session,
  onRun,
  running,
}: {
  session: ResearchSessionItem
  onRun: (overrides: RunOverrides) => void
  running: boolean
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const updateMut = useUpdateResearchSession()
  const isPending = session.status === "pending"
  const [editing, setEditing] = useState(false)
  const [topic, setTopic] = useState(session.topic)
  const [error, setError] = useState("")

  const TOPIC_MIN = 1
  const TOPIC_MAX = 500

  const handleSave = async () => {
    const trimmed = topic.trim()
    if (trimmed.length < TOPIC_MIN) {
      setError(
        t("autoResearch.chat.topicTooShort", {
          defaultValue: "主题至少需要 {{min}} 个字符",
          min: TOPIC_MIN,
        }),
      )
      return
    }
    if (trimmed.length > TOPIC_MAX) {
      setError(
        t("autoResearch.chat.topicTooLong", {
          defaultValue: "主题最多 {{max}} 个字符",
          max: TOPIC_MAX,
        }),
      )
      return
    }

    try {
      await updateMut.mutateAsync({
        sessionId: session.id,
        body: { topic: trimmed },
      })
      setEditing(false)
      setError("")
      // 刷新详情，确保 topic 同步
      qc.invalidateQueries({ queryKey: researchKeys.sessionDetail(session.id) })
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err as Error)?.message ??
        "error"
      toast.error(detail)
    }
  }

  const handleCancel = () => {
    setTopic(session.topic)
    setEditing(false)
    setError("")
  }

  const Icon = isPending ? Rocket : MessageSquareDashed

  if (isPending) {
    const charCount = topic.length
    const isOverLimit = charCount > TOPIC_MAX

    return (
      <div className="flex flex-col items-center justify-center gap-6 py-12 px-6 text-center max-w-3xl mx-auto">
        {/* 顶部图标区：带动画的火箭 + 光晕效果 */}
        <div className="relative">
          <div
            className="absolute inset-0 rounded-full animate-pulse opacity-30 blur-2xl"
            style={{
              background:
                "radial-gradient(circle, var(--primary) 0%, transparent 70%)",
            }}
          />
          <div
            className="relative grid size-20 place-items-center rounded-2xl backdrop-blur-sm border border-primary/20"
            style={{
              background:
                "radial-gradient(circle, color-mix(in srgb, var(--primary) 12%, transparent) 0%, transparent 70%)",
            }}
          >
            <Rocket className="size-10 text-primary" />
          </div>
        </div>

        {/* 标题与副标题 */}
        <div className="space-y-2">
          <h3 className="text-xl font-bold text-foreground">
            {t("autoResearch.chat.pendingTitle", "准备就绪")}
          </h3>
        </div>

        {/* 主题卡片：渐变边框 + 悬浮效果 */}
        <div className="w-full max-w-2xl">
          <div
            className="relative rounded-xl p-[1px] transition-all duration-300"
            style={{
              background: editing
                ? "linear-gradient(135deg, var(--primary) 0%, color-mix(in srgb, var(--primary) 60%, transparent) 100%)"
                : "linear-gradient(135deg, color-mix(in srgb, var(--primary) 40%, transparent) 0%, color-mix(in srgb, var(--primary) 20%, transparent) 100%)",
            }}
          >
            <div className="rounded-xl bg-card backdrop-blur-xl">
              {/* 标签栏：主题 + 编辑按钮 */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-border/40">
                <div className="flex items-center gap-2">
                  <Sparkles className="size-4 text-primary" />
                  <span className="text-sm font-semibold text-foreground">
                    {t("autoResearch.create.topicLabel", "研究主题")}
                  </span>
                </div>
                {!editing && (
                  <button
                    type="button"
                    onClick={() => setEditing(true)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium text-primary/80 hover:text-primary hover:bg-primary/10 transition-all"
                  >
                    <Pencil className="size-3" />
                    {t("autoResearch.chat.editTopic", "编辑")}
                  </button>
                )}
              </div>

              {/* 内容区 */}
              <div className="p-4">
                {editing ? (
                  <div className="space-y-3">
                    <div className="relative">
                      <textarea
                        value={topic}
                        onChange={(e) => {
                          setTopic(e.target.value)
                          if (error) setError("")
                        }}
                        rows={4}
                        className={cn(
                          "w-full rounded-lg border bg-background/80 px-4 py-3 text-sm leading-relaxed resize-none focus:outline-none focus:ring-2 transition-all placeholder:text-muted-foreground/50",
                          error || isOverLimit
                            ? "border-destructive focus:ring-destructive/20"
                            : "border-border/60 focus:border-primary focus:ring-primary/20",
                        )}
                        placeholder={t(
                          "autoResearch.create.topicPlaceholder",
                          "描述你想研究的问题...",
                        )}
                        autoFocus
                      />
                      {/* 字数统计角标 */}
                      <div
                        className={cn(
                          "absolute bottom-2 right-2 px-2 py-0.5 rounded text-[10px] font-mono tabular-nums backdrop-blur-sm",
                          isOverLimit
                            ? "bg-destructive/90 text-destructive-foreground"
                            : charCount > TOPIC_MAX * 0.9
                              ? "bg-amber-500/90 text-white"
                              : "bg-muted/80 text-muted-foreground",
                        )}
                      >
                        {charCount} / {TOPIC_MAX}
                      </div>
                    </div>

                    {/* 错误提示 */}
                    {error && (
                      <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-destructive/10 border border-destructive/20">
                        <AlertTriangle className="size-4 text-destructive shrink-0 mt-0.5" />
                        <p className="text-xs text-destructive text-left flex-1">
                          {error}
                        </p>
                      </div>
                    )}

                    {/* 操作按钮 */}
                    <div className="flex items-center gap-2 justify-end pt-1">
                      <button
                        type="button"
                        onClick={handleCancel}
                        disabled={updateMut.isPending}
                        className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border/60 hover:bg-muted/60 transition-colors disabled:opacity-50"
                      >
                        <X className="size-3.5" />
                        {t("common.cancel", "取消")}
                      </button>
                      <button
                        type="button"
                        onClick={handleSave}
                        disabled={
                          updateMut.isPending || !topic.trim() || isOverLimit
                        }
                        className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 shadow-sm"
                      >
                        {updateMut.isPending ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Check className="size-3.5" />
                        )}
                        {t("common.save", "保存")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground text-left w-full min-h-[56px] max-h-[20vh] overflow-y-auto">
                    {session.topic}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 引导步骤 */}
        {!editing && (
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              <div className="flex size-5 items-center justify-center rounded-full bg-primary/10 text-primary font-semibold">
                1
              </div>
              <span>{t("autoResearch.chat.step1", "确认研究主题")}</span>
            </div>
            <div className="w-8 h-px bg-border/40" />
            <div className="flex items-center gap-2">
              <div className="flex size-5 items-center justify-center rounded-full bg-muted text-muted-foreground font-semibold">
                2
              </div>
              <span>{t("autoResearch.chat.step2", "点击开始研究")}</span>
            </div>
          </div>
        )}

        {/* 开始按钮 */}
        {!editing && (
          <button
            type="button"
            onClick={() => onRun({})}
            disabled={running}
            className="group relative inline-flex items-center gap-2.5 rounded-xl border-2 border-primary/40 bg-gradient-to-br from-primary to-primary/80 px-8 py-3.5 text-base font-semibold text-primary-foreground shadow-lg shadow-primary/25 transition-all hover:shadow-xl hover:shadow-primary/30 hover:scale-105 disabled:opacity-60 disabled:hover:scale-100 disabled:cursor-not-allowed"
          >
            {running ? (
              <>
                <Loader2 className="size-5 animate-spin" />
                {t("autoResearch.chat.starting", "启动中...")}
              </>
            ) : (
              <>
                <Play className="size-5 group-hover:scale-110 transition-transform" />
                {t("autoResearch.chat.startResearch", "开始研究")}
              </>
            )}
            {/* 光晕效果 */}
            <div className="absolute inset-0 rounded-xl bg-gradient-to-r from-transparent via-white/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity blur-sm" />
          </button>
        )}

        {/* 底部提示文案 */}
        {!editing && (
          <p className="text-xs text-muted-foreground/60 max-w-lg">
            {t(
              "autoResearch.chat.pendingHint",
              "AI 将按照 22 个阶段自动执行研究流程，您可以随时查看进度或介入调整",
            )}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <div
        className="relative grid size-14 place-items-center rounded-2xl"
        style={{
          background:
            "radial-gradient(circle, color-mix(in srgb, var(--primary) 10%, transparent) 0%, transparent 70%)",
        }}
      >
        <Icon className="size-7 text-primary/50" />
      </div>
      <p className="text-sm font-medium text-foreground/70">
        {t("autoResearch.chat.emptyTitle", "还没有对话")}
      </p>
      <p className="max-w-xs text-xs leading-relaxed text-muted-foreground/70">
        {t("autoResearch.chat.emptyPickSession")}
      </p>
    </div>
  )
}
