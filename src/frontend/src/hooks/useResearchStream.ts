import { useCallback, useEffect, useRef, useState } from "react"

import { authFetch } from "@/utils/auth"

/**
 * 一条科研 SSE 事件（backend `push_research_event` 里的 shape）。
 *
 * 事件类型来自 `researchclaw` pipeline 与 `LLM4ADAgentSandbox`：
 *   - stage_transition：22 阶段推进
 *   - evolution_step：LLM4AD 新解通知（stage 12）
 *   - waiting_for_input：ARC gate 等表单回填
 *   - artifact_ready：产物就绪
 *   - llm4ad_final_state：LLM4AD 最终状态
 *   - log：兜底日志（stdout/stderr）
 *   - done / error：终止信号（SSE 会随后自然关闭）
 */
export interface ResearchStreamEvent {
  type: string
  stage?: number
  stage_name?: string
  status?: string
  score?: number
  code_sha256?: string
  file_name?: string
  path?: string
  message?: string
  level?: string
  ts?: string
  /** 业务幂等键（event_key），用于去重（向后兼容）。 */
  event_key?: string
  /** DB 主键（message_id），后端在 SSE 中回填，优先使用它做精确去重。 */
  message_id?: string
  /** per-turn 递增序列号，后端 emit 时回填；与 ts 一起作为稳定排序键。 */
  seq?: number
  /** 本帧的 Redis Stream ID（来自 SSE `id:` 行），断线续传时回传。 */
  _streamId?: string
  // 允许透传后端未来新增字段
  [key: string]: unknown
}

export interface ResearchStreamCallbacks {
  /** 每条事件都会调用（含 log）。用于 UI 想显示所有事件时。 */
  onEvent?: (event: ResearchStreamEvent) => void
  /** 关键事件：stage_transition / evolution_step / waiting_for_input / artifact_ready。
   *  UI 常见做法是这里 invalidate React Query 拉最新消息与 state。 */
  onKeyEvent?: (event: ResearchStreamEvent) => void
  /** 终止：done。会自动 setIsStreaming(false)。 */
  onDone?: () => void
  /** 错误：来自 backend 的 error 帧 或 网络 / auth 错误。 */
  onError?: (msg: string) => void
  /** SSE connected 帧到达时回调，携带后端确认的 turn_id。可多次触发（断线重连）。 */
  onConnected?: (turnId: string) => void
}

/** 视为「关键」的事件类型集合。 */
const KEY_EVENT_TYPES = new Set([
  "stage_transition",
  "evolution_step",
  "waiting_for_input",
  "artifact_ready",
  "llm4ad_final_state",
])

/**
 * 网络意外断开后的重连退避（毫秒）。达到终态 / 主动断开不会重连。
 * 封顶后不再放弃，而是按最后一档（30s）持续重连——后端对已终态 turn 会立即
 * 回 `done` 短路，所以持续重连是自愈的：一旦重连成功收到 done 即停，或 turn
 * 结束使 `enabled` 变 false 时停。避免长会话在 4 次失败后永久静默冻结。
 */
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000, 30000]

/**
 * 科研 SSE 连接 hook。
 *
 * 复用 `useChatTuneStream` 的模式：``fetch + AbortController + reader`` 而不是
 * 浏览器原生 `EventSource`，好处是可以走 `authFetch` 携带 Bearer token
 * （EventSource 不支持自定义 header）。
 *
 * 断线续传（对齐 AUTORESEARCH_API.md 流程 D）：
 *   - 首次连接某 turn：``?last_id=0-0`` 从 Redis Stream 头重放；历史快照与
 *     SSE 重叠部分由调用方按 event_key upsert，避免 REST/SSE 之间出现竞态丢事件。
 *   - 网络意外断开重连：带上最后收到帧的 Redis Stream ID（``last_id=<id>``）
 *     从断点续传，补齐空档。
 *   - 已终态 turn：后端立即回 ``done`` 短路，不空等。
 *
 * 同一 (sessionId, turnId) 只会保持一条连接；参数变化时自动断开重连。
 * `enabled` 为 false 或参数缺失时不连接。
 *
 * `reconnectToken`：显式重连令牌。turn 级重试（``POST /turns/{id}/retry``）会复用
 * 同一 ``turnId``，仅靠 ``turnId`` / ``enabled`` 变化无法可靠触发重连；调用方在重试
 * 成功后自增此令牌，即可强制 effect 重跑、断开旧连接并从流头重连新一轮。
 */
export function useResearchStream(
  sessionId: string | null,
  turnId: string | null,
  callbacks: ResearchStreamCallbacks,
  options?: {
    enabled?: boolean
    reconnectToken?: number | string
    /** 首连续传点（Redis Stream ID）：调用方传「已拉取历史的末端」stream_id，
     *  SSE 从该点之后续传、跳过积压，避免首连从 0-0 全量重放上万条。不传则 0-0。
     *  仅影响**首连**；断线重连始终用最后收到帧的 stream id（lastIdRef）。 */
    initialLastId?: string | null
  },
) {
  const enabled = options?.enabled !== false
  const reconnectToken = options?.reconnectToken ?? 0
  const initialLastId = options?.initialLastId ?? null
  const [isStreaming, setIsStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 最近一帧的 Redis Stream ID；断线续传用。切换 turn 时清空。
  const lastIdRef = useRef<string | null>(null)
  // 首连续传点存 ref：每次 render 刷新，connect 时读取。用 ref 而非依赖，避免
  // 该值异步到位（REST 拉完才算得出末端 stream_id）时触发无谓的断连重连。
  const initialLastIdRef = useRef<string | null>(initialLastId)
  initialLastIdRef.current = initialLastId
  const attemptRef = useRef(0)
  // turn 已收到终止信号（done/error/timeout）。置位后不再重连——否则后端会对
  // 已终态 turn 反复短路返回 done，触发无意义的退避重连风暴。切换 turn 时复位。
  const terminatedRef = useRef(false)

  // 把 callbacks 装到 ref 里，避免每次 render 都重连（用户传的对象引用会变）
  const cbRef = useRef(callbacks)
  cbRef.current = callbacks

  const clearTimers = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  const disconnect = useCallback(() => {
    clearTimers()
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
  }, [clearTimers])

  useEffect(() => {
    if (!enabled || !sessionId || !turnId) {
      disconnect()
      return
    }

    // 新的 turn → 重置续传游标与重连计数
    lastIdRef.current = null
    attemptRef.current = 0
    terminatedRef.current = false
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      const abort = new AbortController()
      abortRef.current = abort
      setIsStreaming(true)

      const baseUrl = import.meta.env.VITE_API_URL || ""
      // 续传游标优先级：断线重连用最后收到帧的 Redis ID（lastIdRef）；首连用调用方
      // 给的续传点（initialLastId=已拉取历史末端，跳过积压）；都没有才 0-0 全量 replay。
      const lastId = lastIdRef.current ?? initialLastIdRef.current ?? "0-0"
      const url = `${baseUrl}/api/v1/llm4ad/research/sessions/${sessionId}/turns/${turnId}/stream?last_id=${encodeURIComponent(
        lastId,
      )}`

      ;(async () => {
        try {
          const resp = await authFetch(url, { signal: abort.signal })
          if (!resp.ok) {
            cbRef.current.onError?.(`HTTP ${resp.status}`)
            setIsStreaming(false)
            // 4xx（turn 不存在 / 无权限等）是永久性错误：标记终止不再重连，
            // 否则封顶无限重连会每 30s 死循环打后端。5xx 视为瞬时故障照常重连。
            if (resp.status >= 400 && resp.status < 500) {
              terminatedRef.current = true
              return
            }
            scheduleReconnect()
            return
          }
          const reader = resp.body?.getReader()
          if (!reader) {
            cbRef.current.onError?.("no reader")
            setIsStreaming(false)
            return
          }
          // 连接成功 → 重置退避
          attemptRef.current = 0

          const decoder = new TextDecoder()
          let buffer = ""
          let curEvent = ""
          let curData = ""
          let curId = ""

          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            const lines = buffer.split("\n")
            buffer = lines.pop() ?? ""
            for (const raw of lines) {
              const line = raw.replace(/\r$/, "")
              if (line.startsWith("event:")) {
                curEvent = line.slice(6).trim()
              } else if (line.startsWith("data:")) {
                // 同一帧可有多条 data: 行，按 SSE 规范用换行拼接。
                const chunk = line.slice(5).trim()
                curData = curData ? `${curData}\n${chunk}` : chunk
              } else if (line.startsWith("id:")) {
                curId = line.slice(3).trim()
              } else if (line === "") {
                if (curId) lastIdRef.current = curId
                processFrame(curEvent, curData, curId, cbRef.current, () => {
                  terminatedRef.current = true
                  setIsStreaming(false)
                })
                curEvent = ""
                curData = ""
                curId = ""
              }
            }
          }
          // 流自然结束：可能是 done 已处理，也可能是服务端断开
          setIsStreaming(false)
          scheduleReconnect()
        } catch (err: unknown) {
          if ((err as Error).name === "AbortError") return
          cbRef.current.onError?.(
            (err as Error).message || "Stream connection failed",
          )
          setIsStreaming(false)
          scheduleReconnect()
        }
      })()
    }

    // 意外断开时按退避重连；封顶后维持最大间隔持续重连（不放弃），
    // 靠后端对终态 turn 的 done 短路自愈。已收到终止信号（done/error/timeout）
    // 则不再重连，避免对已终态 turn 反复短路。
    const scheduleReconnect = () => {
      if (cancelled || terminatedRef.current) return
      const attempt = attemptRef.current
      const delay =
        RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)]
      attemptRef.current = attempt + 1
      clearTimers()
      retryTimerRef.current = setTimeout(connect, delay)
    }

    connect()

    return () => {
      cancelled = true
      disconnect()
    }
    // reconnectToken 参与依赖：重试复用同一 turnId 时，靠它强制重连。
  }, [enabled, sessionId, turnId, reconnectToken, disconnect, clearTimers])

  return { isStreaming, disconnect }
}

function processFrame(
  event: string,
  data: string,
  streamId: string,
  cb: ResearchStreamCallbacks,
  markDone: () => void,
) {
  // SSE 「命名事件」：connected / heartbeat / done / timeout。
  // 业务事件（stage_transition 等）都走匿名 `data:` 帧。
  switch (event) {
    case "connected": {
      // connected 帧的 data 含 {"turn_id": "..."} 等元信息；解析后回调，多次重连均触发。
      if (data) {
        try {
          const meta = JSON.parse(data) as Record<string, unknown>
          if (typeof meta.turn_id === "string") cb.onConnected?.(meta.turn_id)
        } catch {
          // data 非 JSON 时忽略
        }
      }
      return
    }
    case "heartbeat":
      return
    case "timeout":
      cb.onError?.("idle_timeout")
      markDone()
      return
    case "done":
      cb.onDone?.()
      markDone()
      return
  }

  if (!data) return
  let parsed: ResearchStreamEvent
  try {
    parsed = JSON.parse(data)
  } catch {
    return
  }
  if (!parsed || typeof parsed !== "object") return
  if (streamId) parsed._streamId = streamId

  // 数据帧里的 done/error（业务级）也是终止
  if (parsed.type === "done") {
    cb.onEvent?.(parsed)
    cb.onDone?.()
    markDone()
    return
  }
  if (parsed.type === "error") {
    cb.onEvent?.(parsed)
    cb.onError?.((parsed.message as string) || "Unknown error")
    markDone()
    return
  }

  cb.onEvent?.(parsed)
  if (KEY_EVENT_TYPES.has(parsed.type)) {
    cb.onKeyEvent?.(parsed)
  }
}
