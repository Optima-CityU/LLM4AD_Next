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
import type { ReactNode } from "react"
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
  useResearchLogs,
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
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

import BottomComposer, { type RunOverrides } from "./BottomComposer"
import MessageItem from "./MessageItem"
import { ML_VISION_PROFILE } from "./shared"
import { StageProgressBar } from "./StageProgress"
import type { StreamLogEntry } from "./StreamLogConsole"
import StageTimeline, {
  type StageEntry,
  type StageStatus,
} from "./StageTimeline"
import TurnLogPanel from "./TurnLogPanel"

interface Props {
  session: ResearchSessionItem | null
  onCreateSession: () => void
}

/** 稳定空数组：非活跃轮的 liveLogs，避免每次 render 生成新引用触发子组件重算。 */
const EMPTY_LOGS: StreamLogEntry[] = []

/** 实时消息叠加层上限：更旧的实时事件由 REST 分页兜底，防止长跑内存膨胀。 */
const LIVE_MSG_CAP = 300

/**
 * 比较两个 Redis Stream entry id（形如 ``"<ms>-<seq>"``），返回**较小**者。
 *
 * 按 ``ms`` 数值、再 ``seq`` 数值比较——不能用字符串序（``"10-0" < "9-0"`` 会误判）。
 * 任一为空视为「该源无续传约束」，直接返回另一个；都为空返回 undefined。
 * 用于把 message / log 两源各自已落库末端取 min 作 SSE 首连续传点，保证不跳空。
 */
function minStreamId(a?: string | null, b?: string | null): string | undefined {
  if (!a) return b ?? undefined
  if (!b) return a ?? undefined
  const [ma, sa] = a.split("-")
  const [mb, sb] = b.split("-")
  const nma = Number(ma)
  const nmb = Number(mb)
  if (nma !== nmb) return nma < nmb ? a : b
  return Number(sa ?? 0) <= Number(sb ?? 0) ? a : b
}

/**
 * 消息流渲染项：普通消息，或「一次阶段访问的时间轴条目」（含叠加的多个状态）。
 */
type RenderItem =
  | { kind: "msg"; message: ResearchMessageItem }
  | { kind: "stage"; entry: StageEntry }

/** 从消息中取阶段号（message.stage 优先，回退 payload.stage）。 */
function stageOf(m: ResearchMessageItem): number | null {
  const p = (m.payload ?? {}) as { stage?: number }
  return m.stage ?? p.stage ?? null
}

/** 阶段事件的状态串（running/waiting/done/failed）。 */
function statusOf(m: ResearchMessageItem): string {
  return String((m.payload as { status?: unknown })?.status ?? "")
}

/**
 * 折叠某一轮内**相邻且同阶段**的 stage_transition 为一个时间轴条目，多个状态
 * （如 running→done）按时序叠加进 `statuses`；右侧即可展示各状态发生的时刻。
 *
 * 合并规则「仅相邻」（后端已保证去重后时序正确）：
 * - `阶段1开始 → 阶段1结束`（紧邻）→ 合并成一条，statuses=[running, done]。
 * - `阶段1开始 → 阶段2开始 → 阶段1结束`→ 不合并（中间隔了阶段2），三条独立。
 * - 任何非 stage_transition 事件（assistant 文本 / evolution_step 等）也会打断
 *   相邻性：其后的同阶段事件另起一条。
 */
function collapseTurn(messages: ResearchMessageItem[]): RenderItem[] {
  const items: RenderItem[] = []
  let lastStage: number | null = null
  let lastIdx = -1
  // 整轮累计每个阶段号的「第几次访问」：每新建一条 stage entry 时 +1。计数跨越
  // 非阶段消息的打断（不重置），故一轮内重跑的阶段序号连续，与时间轴分段无关。
  const occByStage = new Map<number, number>()
  for (const m of messages) {
    const eventType = m.event_type ?? "log"
    const stage = stageOf(m)
    if (eventType !== "stage_transition" || stage == null) {
      items.push({ kind: "msg", message: m })
      lastStage = null // 非阶段事件打断相邻性
      continue
    }
    const st: StageStatus = {
      status: statusOf(m),
      time: m.created_time,
      error:
        (m.payload as { error?: string } | null)?.error ?? m.error ?? undefined,
    }
    if (stage === lastStage && lastIdx >= 0) {
      // 与上一条同阶段且紧邻 → 状态叠加
      ;(items[lastIdx] as Extract<RenderItem, { kind: "stage" }>).entry.statuses.push(
        st,
      )
    } else {
      const occurrence = (occByStage.get(stage) ?? 0) + 1
      occByStage.set(stage, occurrence)
      items.push({
        kind: "stage",
        entry: { id: `stage:${m.id}`, stage, statuses: [st], occurrence },
      })
      lastStage = stage
      lastIdx = items.length - 1
    }
  }
  return items
}

/**
 * 把一轮的渲染项铺开：连续的 stage 条目合并成一个 `StageTimeline`（竖向时间轴），
 * 普通消息用 `MessageItem`。这样阶段流不再是一堆独立胶囊，而是一条连贯时间轴，
 * 被非阶段消息打断处自然分段。
 */
function renderTurnItems(
  items: RenderItem[],
  sessionId: string,
  live: boolean,
): ReactNode[] {
  const out: ReactNode[] = []
  let run: StageEntry[] = []
  const flush = () => {
    if (run.length === 0) return
    out.push(
      <StageTimeline key={`tl:${run[0].id}`} entries={run} live={live} />,
    )
    run = []
  }
  for (const it of items) {
    if (it.kind === "stage") {
      run.push(it.entry)
    } else {
      flush()
      out.push(
        <MessageItem
          key={it.message.id}
          message={it.message}
          sessionId={sessionId}
          stale
        />,
      )
    }
  }
  flush()
  return out
}

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
  // pipeline turn 的显式重连令牌：retry 复用同一 turn_id，需手动 bump 让 SSE 重连。
  const [streamReconnectToken, setStreamReconnectToken] = useState(0)

  // 页面加载/会话切换时，自动恢复正在进行的 collaborating turn。
  // 数据源直接用 session detail 的 active_collab_turn（后端已单独暴露活跃协作轮，
  // 无需再翻 /turns 列表）。使用 ref 记录是否已初始化，避免协作完成后 detail 刷新
  // 重复设置导致的循环。
  const collabInitializedRef = useRef(false)
  useEffect(() => {
    if (collabInitializedRef.current) return
    const collab = detail.data?.active_collab_turn
    if (collab && collab.status === "collaborating") {
      setCollabTurnId(collab.id)
      collabInitializedRef.current = true
    }
  }, [detail.data?.active_collab_turn])

  // 会话切换时重置初始化标记
  useEffect(() => {
    collabInitializedRef.current = false
  }, [session.id])

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
  // 分页深处；每轮日志改由内联折叠的 TurnLogPanel 按 turn 懒加载。全局日志总览
  // 移到右侧面板的 ResearchLogDrawer（独立 listLogs 双端游标窗口）。
  const msgQ = useResearchSessionMessages(session.id, {
    pageSize: 200,
    kind: "chat",
  })

  // 切换 turn 时清空实时消息
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在切换 turn 时清空
  useEffect(() => {
    setLiveMessages([])
  }, [activeTurnId])

  const messages = useMemo(() => {
    const pages = msgQ.data?.pages ?? []
    // pages[0] 最新页、pages[n] 更旧页；反转后拼接 = 升序全量（后端已排序）
    const flat: ResearchMessageItem[] = []
    for (let i = pages.length - 1; i >= 0; i--) {
      for (const m of pages[i].items ?? []) flat.push(m)
    }
    // 去重策略：优先用 message_id（DB 主键）去重，再用 (turn_id, event_key) 兜底。
    // SSE 推送的事件后端已回填 message_id（streaming.py:226），与 DB 记录一致时靠
    // 主键即可精确去重；但个别事件类型若未回填 message_id，live 会退化成合成 id
    // （live:turn:event_key，见 upsert 处），与 REST 真 id 对不上 → 主键去重漏掉、
    // 两条都显示。故再建 (turn_id, event_key) 索引兜底：event_key 在后端受
    // uq_research_message_turn_role_event 约束、按轮唯一，跨轮加 turn_id 前缀防撞。
    const messageMap = new Map<string, ResearchMessageItem>()
    const restEventKeys = new Set<string>()
    for (const message of flat) {
      messageMap.set(message.id, message)
      if (message.event_key) {
        restEventKeys.add(`${message.turn_id}::${message.event_key}`)
      }
    }
    // SSE 实时数据：仅补充 REST 尚未包含的新事件。
    // 注意 REST 是权威源——一旦某事件已由 REST 返回（无论按 message_id 还是
    // event_key 命中），就不能再用 live 的合成字段覆盖它：live 消息里的
    // payload_locked/turn_status/content/error 都是硬编码近似值（见 upsert 处），
    // 反向覆盖会导致已 locked 的门控表单被误判为「待回填」、里程碑内容显示不全。
    for (const liveMsg of liveMessages) {
      if (messageMap.has(liveMsg.id)) continue
      if (
        liveMsg.event_key &&
        restEventKeys.has(`${liveMsg.turn_id}::${liveMsg.event_key}`)
      ) {
        continue
      }
      messageMap.set(liveMsg.id, liveMsg)
    }
    // 兜底稳定排序：后端已按 (created_time, seq, id) 排序，且现在 REST 与 SSE live
    // 用同一发射时刻 + 同一 per-turn seq 计数器（不再有落库时刻倒挂 / seq=0 撞车），
    // 但 live 与 REST 两路合并后的插入顺序仍可能因 refetch 时序错位。故这里按同一
    // 三元组键再稳定排序一次，与后端语义完全对齐，彻底消除「最新状态排到前面」。
    return [...messageMap.values()].sort((a, b) => {
      if (a.created_time !== b.created_time) {
        return a.created_time < b.created_time ? -1 : 1
      }
      const sa = a.seq ?? 0
      const sb = b.seq ?? 0
      if (sa !== sb) return sa - sb
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    })
  }, [msgQ.data, liveMessages])

  // 当前待回复的门控 form 消息：turn 进入 paused_gate 时最后一条未锁定的 form。
  // 移到底部 GatePanel 操作；消息流里不再重复渲染这一条（见 renderedMessages）。
  //
  // 门槛用 turn 级状态（activeTurn.status === "paused_gate"）而非 session.status
  // ——与 SSE 订阅门槛 turnLive 同源：session.status 经 prop 透传、滞后刷新，
  // waiting_for_input 到达后它常慢一拍，会让底部 GatePanel 迟迟不出现。turn 的
  // paused_gate 对应 session 的 paused，语义一致且刷新时序与 activeTurnId 对齐。
  const gateMessage = useMemo(() => {
    if (activeTurn?.status !== "paused_gate") return null
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      const kind = (m.payload as { kind?: string } | null)?.kind
      if (kind === "form" && !m.payload_locked) return m
    }
    return null
  }, [messages, activeTurn?.status])

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
  // 切换 turn 或重试（streamReconnectToken 变化）时清空实时日志缓冲：重试复用同一
  // turn_id，仅靠 activeTurnId 不会触发，需带上重连令牌，避免上一轮失败日志残留。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在切换 turn / 重试时清空
  useEffect(() => {
    setStreamLogs([])
    logSeqRef.current = 0
    sseTurnIdRef.current = null
  }, [activeTurnId, streamReconnectToken])

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
    // 每轮内独立折叠：相邻同阶段状态叠加成一个时间轴条目（重跑会新起 turn）。
    return [...groups.values()].map((g) => ({
      turnId: g.turnId,
      items: collapseTurn(g.messages),
    }))
  }, [activeTurnId, renderedMessages])

  const lastTurnId = groupedMessages[groupedMessages.length - 1]?.turnId ?? null

  // ── SSE：关键事件到达时按类型分流失效相关查询 ─────────────────────────
  // 订阅门槛用 turn 级状态（与 activeTurnId 同源自 detail query），而非 session.status
  // 那个经 prop 透传、滞后刷新的字段——重试复用同一 turn_id 时 session.status 常慢一拍，
  // 会导致 sseEnabled 迟迟不翻 true、SSE 不重连。
  //
  // 只认 running：paused_gate **不订阅**。命中硬门控时后端已 emit done 关流、worker
  // 释放（backend streaming.persist_gate_pause：落 waiting_for_input 表单后主循环
  // emit done），该 turn 的 Redis Stream 不会再有新事件。门控提交走**新建 turn**
  // （backend turns._reply_to_gate：旧 paused 轮落 COMPLETED、新 turn 带新 turn_id
  // 入队），新事件写到新 stream key，靠 activeTurnId 变化自然重连新流。故 paused_gate
  // 保持订阅只会连上一条只剩 done 的死流（触发 onDone 空跑）或空连 idle→timeout→退避
  // 重连周期性打后端，无任何收益。门控表单本身由 REST messages 兜底（已落库），不依赖 SSE。
  const turnLive = !!activeTurnId && activeTurn?.status === "running"

  // SSE 首连续传点探针：只取活跃轮**最新一条** log 的 stream_id（order=desc、limit=1，
  // 极轻量）。log 是量最大的源（一轮可上万条），据其末端续传能把首连从「0-0 全量
  // 重放上万条」降到「只补探针之后的增量」。仅活跃轮启用；非活跃轮无 SSE、无需探针。
  // 注意：探针只依赖 turnLive，**不**依赖 sseEnabled——它必须先于 SSE 跑完，
  // 好让 sseEnabled 能等它 settle。二者互为前提会死锁，故探针挂在更靠前的 turnLive 上。
  const logProbe = useResearchLogs(session.id, {
    turnId: activeTurnId,
    limit: 1,
    enabled: turnLive,
  })

  // SSE 订阅门槛：turn 存活，且两个历史源都已「取过一次」（settle，空也算）。
  // 必须等两源 settle 再连——否则 status 先到、REST 未回时抢连，initialLastId 仍空
  // → 退化成 last_id=0-0 全量重放（正是要消除的慢路径）。两源任一为空（新轮无历史）
  // 时 initialLastId=undefined，SSE 正常从 0-0 连，符合预期、不会卡住。
  const historyReady = logProbe.hasLoaded && msgQ.isFetched
  const sseEnabled = turnLive && historyReady

  // 两源各自已落库末端 stream_id，取较小值作首连续传点（保证不跳空，重叠交由
  // TurnLogPanel / messages 的去重兜底）。message 源末端从升序 messages 里向后找
  // 首个带 stream_id 且属活跃轮者；log 源末端取探针最新一条。任一缺失回退到另一个，
  // 都缺失则 undefined → useResearchStream 回退 0-0 全量重放（无历史的新轮即此情形）。
  const initialLastId = useMemo(() => {
    if (!activeTurnId) return undefined
    let msgStreamId: string | undefined
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.turn_id === activeTurnId && m.stream_id) {
        msgStreamId = m.stream_id
        break
      }
    }
    const logEntries = logProbe.entries
    const logStreamId =
      logEntries.length > 0
        ? (logEntries[logEntries.length - 1].stream_id ?? undefined)
        : undefined
    return minStreamId(msgStreamId, logStreamId)
  }, [activeTurnId, messages, logProbe.entries])

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
  // 返回 messages 的失效 Promise（refetch 落定即 resolve），供 onDone 等 REST
  // 权威数据到位后再清 liveMessages——避免 done 瞬间清空导致 REST 尚未返回时闪空。
  const invalidateAll = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    pendingRef.current = new Set()
    qc.invalidateQueries({ queryKey: researchKeys.sessionDetail(session.id) })
    const messagesSettled = qc.invalidateQueries({
      queryKey: researchKeys.sessionMessages(session.id),
      exact: false,
    })
    qc.invalidateQueries({ queryKey: researchKeys.state(session.id) })
    qc.invalidateQueries({ queryKey: researchKeys.artifacts(session.id) })
    qc.invalidateQueries({ queryKey: researchKeys.artifactTree(session.id) })
    return messagesSettled
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
        // 等 messages 失效并 refetch 落定后再清 liveMessages：此时 REST 已成为
        // 权威源，清空实时叠加层可彻底避免「同一事件 live 版残留」引发的重复，
        // 并保证结束后 100% 以持久化 messages 为准。清空前 REST 已到位，无闪空。
        invalidateAll().finally(() => {
          setLiveMessages([])
        })
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

          // 特殊处理：waiting_for_input 事件需要在 payload 中添加 kind: "form"
          // 以便 gateMessage 判断逻辑能找到门控表单
          if (evt.type === "waiting_for_input") {
            eventPayload.kind = "form"
          }

          // 后端已在 SSE data 中回填 message_id（streaming.py:226），优先使用它作为
          // DB 主键；未提供时回退到 event_key 合成临时 ID（向后兼容）。
          const messageId =
            (evt.message_id as string | undefined) ?? `live:${currentTurnId}:${evt.event_key}`
          setLiveMessages((prev) => {
            const existing = prev.find((message) => message.id === messageId)
            const liveMessage: ResearchMessageItem = {
              id: messageId,
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
              // 后端 emit 时回填的 per-turn seq（与 REST 同一计数器），供稳定排序；
              // 个别旧事件可能无 seq，回退 0。
              seq: evt.seq ?? 0,
              created_time: nowIso,
              updated_time: nowIso,
            }
            return existing
              ? prev.map((message) =>
                  message.id === existing.id ? liveMessage : message,
                )
              : // 追加新事件时封顶：evolution_step 等高频事件的 event_key 各不相同，
                // 长跑会让 liveMessages 无上限膨胀。只保留最近 LIVE_MSG_CAP 条，
                // 更旧的实时事件由 REST 分页（msgQ）兜底承载。
                [...prev, liveMessage].slice(-LIVE_MSG_CAP)
          })
        }
        if (evt.type !== "log") return
        const entry: StreamLogEntry = {
          id: `l:${++logSeqRef.current}`,
          eventKey: evt.event_key as string | undefined,
          streamId: evt._streamId,
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
    {
      enabled: sseEnabled,
      reconnectToken: streamReconnectToken,
      initialLastId,
    },
  )

  // ── collab agent 的 SSE：独立于 pipeline turn，订阅活跃 collab turn ──────
  // 使用 ref 防止 onDone 重复调用（SSE 会发送两次 done：type="done" 消息帧 + event:done 流结束帧）
  const collabDoneRef = useRef(false)
  useResearchStream(
    session.id,
    collabTurnId,
    {
      onDone: () => {
        // 防止重复调用（SSE 的 type="done" 和 event:done 都会触发 onDone）
        if (collabDoneRef.current) return
        collabDoneRef.current = true

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

  // collabTurnId 变化时重置 done 标记
  useEffect(() => {
    collabDoneRef.current = false
  }, [collabTurnId])

  // ── 滚动锚点：最新消息 id 变化或 collab 流式输出增长时，若用户已贴近底部则滚动到底 ─
  const scrollAnchor = useRef<HTMLDivElement | null>(null)
  const scrollBox = useRef<HTMLDivElement | null>(null)
  const lastId = messages[messages.length - 1]?.id
  const collabBusy = !!collabTurnId || collabMut.isPending
  // biome-ignore lint/correctness/useExhaustiveDependencies: 消息 id 或 collab 流式文本变化时触发
  useEffect(() => {
    const box = scrollBox.current
    // 运行中用户上滚回看历史时不强行拽回底部；离底部超过 ~120px 就不自动跟随。
    // 但如果 collabBusy 刚变为 true（刚发送消息），则强制滚动到底部
    if (box && box.scrollHeight - box.scrollTop - box.clientHeight > 120 && !collabBusy) {
      return
    }
    scrollAnchor.current?.scrollIntoView({ behavior: "smooth" })
  }, [lastId, collabStreamText, collabBusy])

  // 切换 session 或初次加载完成时强制滚到底部。
  // biome-ignore lint/correctness/useExhaustiveDependencies: session 切换或加载完成时触发
  useEffect(() => {
    if (msgQ.isLoading) return
    // 延迟滚动，确保消息和 TurnLogPanel 都渲染完成
    setTimeout(() => {
      scrollAnchor.current?.scrollIntoView({ behavior: "instant" })
    }, 150)
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
      {
        onError: toastErr,
        onSuccess: scrollToBottomDelayed,
      },
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
        onSuccess: (resp) => {
          setCollabTurnId(resp.turn.id)
          scrollToBottomDelayed()
        },
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
      {
        // 重试复用同一 turn_id：bump 重连令牌，强制 useResearchStream 断旧连、
        // 从流头重连新一轮（否则 turnId 不变，SSE 不会自动重订）。
        onSuccess: () => setStreamReconnectToken((n) => n + 1),
        onError: toastErr,
      },
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

  // 延迟滚动到底部（等待 DOM 更新后再滚动）
  const scrollToBottomDelayed = useCallback(() => {
    // 使用 setTimeout 确保在 React 重新渲染后执行
    setTimeout(() => {
      scrollAnchor.current?.scrollIntoView({ behavior: "smooth" })
    }, 100)
  }, [])

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
          provider_id: runProvider.trim() || undefined,
          model_name: runModel.trim() || undefined,
        } as never,
      },
      {
        onError: toastErr,
        onSuccess: scrollToBottomDelayed,
      },
    )
  }

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
      <HoverCard openDelay={300} closeDelay={100}>
        <HoverCardTrigger asChild>
          <span className="text-sm font-semibold truncate max-w-[420px] text-foreground/90 cursor-default">
            {session.title}
          </span>
        </HoverCardTrigger>
        <HoverCardContent
          side="bottom"
          align="center"
          className="max-w-[min(48rem,90vw)] p-4 text-[13px] leading-relaxed"
        >
          <div className="whitespace-pre-wrap break-words text-foreground/90">
            {session.topic || session.title}
          </div>
        </HoverCardContent>
      </HoverCard>,
    )
    return () => setHeaderCenter(null)
  }, [session.title, session.topic])

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
        hideLlm4ad={session.profile === ML_VISION_PROFILE}
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
              <div className={cn(
                "py-3 space-y-1",
                // 运行中或协作中时，添加底部内边距，确保内容不被遮挡
                (busy || collabBusy) && "pb-24"
              )}>
                {groupedMessages.map((group) => (
                  <div
                    key={group.turnId}
                    className="group/turn relative pl-3"
                  >
                    {/* 左侧竖线：hover 时高亮 */}
                    <span
                      aria-hidden
                      className="absolute left-0 top-0 bottom-0 w-0.5 rounded-full bg-border/30 transition-all duration-300 group-hover/turn:w-[3px] group-hover/turn:bg-primary/60 group-hover/turn:shadow-[0_0_8px_0] group-hover/turn:shadow-primary/30"
                    />
                    {/* 渲染：相邻的阶段条目成组画成竖向时间轴，普通消息按原样。 */}
                    {renderTurnItems(
                      group.items,
                      session.id,
                      group.turnId === activeTurnId &&
                        (activeTurn?.status === "running" ||
                          activeTurn?.status === "paused_gate"),
                    )}
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
              <div className="mx-4 my-2 flex items-start gap-1.5 px-3 py-2 border border-destructive/40 bg-destructive/10 text-destructive text-xs rounded-md">
                <AlertTriangle className="mt-px size-3 shrink-0" />
                <span className="min-w-0 break-words whitespace-pre-wrap">
                  {session.error}
                </span>
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
              className="size-7 grid place-items-center rounded-full bg-background/40 backdrop-blur border border-border/50 shadow-md hover:bg-background/90 hover:border-primary/50 transition-all pointer-events-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            >
              <ArrowUp className="size-3.5" />
            </button>
            <button
              type="button"
              onClick={scrollToBottom}
              title={t("autoResearch.chat.scrollToBottom", {
                defaultValue: "回到底部",
              })}
              className="size-7 grid place-items-center rounded-full bg-background/40 backdrop-blur border border-border/50 shadow-md hover:bg-background/90 hover:border-primary/50 transition-all pointer-events-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
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
