import { useEffect, useRef, useState } from "react"

import { authFetch } from "@/utils/auth"

/**
 * 科研工作区「模型网关健康」事件流（SSE）。
 *
 * 后端 llm_proxy 在该工作区运行时的模型请求失败/恢复时推送
 * ``proxy_error`` / ``proxy_recovered`` 事件；``connected`` 帧携带当前失败
 * 快照，供刷新页面后立即恢复提示状态。连接意外断开按退避重连。
 */

export interface RuntimeFailureState {
  /** 连续失败次数。 */
  count: number
  /** 稳定原因码：rate_limited / auth / upstream_error / timeout / disconnected / other。 */
  reason: string
  status: number | null
  message: string
  ts: number
}

const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000, 30000]

interface ConnectedSnapshot {
  state?: "ok" | "failing"
  failures?: number
  reason?: string
  status?: number
  message?: string
  ts?: number
}

interface RuntimeFrame {
  type?: string
  count?: number
  reason?: string
  status?: number | null
  message?: string
  ts?: number
  state?: ConnectedSnapshot
}

export function useRuntimeEvents(
  workspaceId: string | null,
  enabled: boolean,
): { failure: RuntimeFailureState | null } {
  const [failure, setFailure] = useState<RuntimeFailureState | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const attemptRef = useRef(0)

  useEffect(() => {
    if (!enabled || !workspaceId) {
      setFailure(null)
      return
    }
    let cancelled = false

    const scheduleReconnect = () => {
      if (cancelled) return
      const attempt = attemptRef.current
      const delay =
        RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)]
      attemptRef.current = attempt + 1
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current)
      }
      retryTimerRef.current = setTimeout(connect, delay)
    }

    const applyFrame = (event: string, payload: RuntimeFrame) => {
      if (event === "connected" && payload.state?.state === "failing") {
        setFailure({
          count: payload.state.failures ?? 0,
          reason: payload.state.reason ?? "other",
          status: payload.state.status ?? null,
          message: payload.state.message ?? "",
          ts: payload.state.ts ?? 0,
        })
        return
      }
      if (event !== "runtime") return
      if (payload.type === "proxy_error") {
        setFailure({
          count: payload.count ?? 0,
          reason: payload.reason ?? "other",
          status: payload.status ?? null,
          message: payload.message ?? "",
          ts: payload.ts ?? 0,
        })
      } else if (payload.type === "proxy_recovered") {
        setFailure(null)
      }
    }

    const connect = () => {
      if (cancelled) return
      const abort = new AbortController()
      abortRef.current = abort
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const url = `${baseUrl}/api/v1/llm4ad/papers/workspaces/${encodeURIComponent(
        workspaceId,
      )}/runtime-events`

      ;(async () => {
        try {
          const resp = await authFetch(url, { signal: abort.signal })
          if (!resp.ok) {
            scheduleReconnect()
            return
          }
          attemptRef.current = 0
          const reader = resp.body?.getReader()
          if (!reader) {
            scheduleReconnect()
            return
          }
          const decoder = new TextDecoder()
          let buffer = ""
          let curEvent = ""
          let curData = ""
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
                const chunk = line.slice(5).trim()
                curData = curData ? `${curData}\n${chunk}` : chunk
              } else if (line === "") {
                try {
                  applyFrame(
                    curEvent,
                    JSON.parse(curData || "{}") as RuntimeFrame,
                  )
                } catch {
                  // 忽略无法解析的帧
                }
                curEvent = ""
                curData = ""
              }
            }
          }
          scheduleReconnect()
        } catch (err: unknown) {
          if ((err as Error).name === "AbortError") return
          scheduleReconnect()
        }
      })()
    }

    connect()
    return () => {
      cancelled = true
      abortRef.current?.abort()
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current)
      }
    }
  }, [workspaceId, enabled])

  return { failure }
}
