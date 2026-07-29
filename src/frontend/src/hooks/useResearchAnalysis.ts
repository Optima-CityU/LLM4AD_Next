/**
 * 自动科研（AutoResearch）结果分析相关的 React Query hooks + SSE 流。
 *
 * 三块能力：
 *  - `useResearchAnalysis`：拉取结构化聚合数据 + 最近一次 LLM 报告（`getAnalysis`）。
 *  - `useGenerateAnalysis` / `useStopAnalysis`：触发 / 停止 LLM 报告后台生成。
 *  - `useAnalysisStream`：订阅 `/analysis/stream` SSE，实时拼接报告增量内容
 *    （镜像 `useReportStream` 的解析/批渲染逻辑，端点换成科研分析流）。
 */

import {
  type UseQueryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"

import {
  Llm4AdResearchService,
  type ResearchAnalysisDetailResponse,
  type ResearchAnalysisGenerateRequest,
} from "@/client"
import { authFetch } from "@/utils/auth"

import { researchKeys } from "./useAutoResearch"

/** 结构化聚合数据 + 最近一次 LLM 报告。 */
export function useResearchAnalysis(
  sessionId: string | null,
  opts?: Omit<
    UseQueryOptions<ResearchAnalysisDetailResponse>,
    "queryKey" | "queryFn" | "enabled"
  >,
) {
  return useQuery({
    queryKey: sessionId ? researchKeys.analysis(sessionId) : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.getAnalysis({ sessionId: sessionId as string }),
    enabled: !!sessionId,
    staleTime: 10_000,
    ...opts,
  })
}

/** 触发结果分析报告的 LLM 后台生成（202 受理）。 */
export function useGenerateAnalysis() {
  return useMutation({
    mutationFn: ({
      sessionId,
      body,
    }: {
      sessionId: string
      body: ResearchAnalysisGenerateRequest
    }) =>
      Llm4AdResearchService.generateAnalysis({
        sessionId,
        requestBody: body,
      }),
  })
}

/** 停止进行中的分析报告生成。 */
export function useStopAnalysis() {
  return useMutation({
    mutationFn: (sessionId: string) =>
      Llm4AdResearchService.stopAnalysis({ sessionId }),
  })
}

type StreamStatus = "generating" | "completed" | "failed" | "cancelled" | null

interface UseAnalysisStreamReturn {
  content: string
  status: StreamStatus
  error: string | null
  isStreaming: boolean
  stop: () => void
}

const MAX_RETRIES = 2
const RETRY_DELAY = 1500

/**
 * 订阅分析报告 SSE 流，实时拼接增量内容。
 *
 * `enabled` 由「是否处于生成态」控制：置真时建立连接并从零累积，置假时清空复位。
 * 解析逻辑与 `useReportStream` 一致（event/data 行 + chunk/done/error 事件），
 * 用 requestAnimationFrame 批量刷新 content 避免高频 setState。流结束（done）后
 * 失效 analysis 查询，让持久化的最终报告回填。
 */
export function useAnalysisStream(
  sessionId: string,
  enabled: boolean,
): UseAnalysisStreamReturn {
  const queryClient = useQueryClient()
  const [content, setContent] = useState("")
  const [status, setStatus] = useState<StreamStatus>(null)
  const [error, setError] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const contentBuffer = useRef("")
  const rafId = useRef<number>(0)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!enabled || !sessionId) {
      setContent("")
      setStatus(null)
      setError(null)
      setIsStreaming(false)
      contentBuffer.current = ""
      return
    }

    setIsStreaming(true)
    setError(null)
    setContent("")
    setStatus("generating")
    contentBuffer.current = ""

    const abortController = new AbortController()
    abortRef.current = abortController
    let cancelled = false

    const scheduleFlush = () => {
      cancelAnimationFrame(rafId.current)
      rafId.current = requestAnimationFrame(() => {
        if (!cancelled) setContent(contentBuffer.current)
      })
    }

    const invalidate = () =>
      queryClient.invalidateQueries({
        queryKey: researchKeys.analysis(sessionId),
      })

    const baseUrl = import.meta.env.VITE_API_URL || ""
    const streamUrl = `${baseUrl}/api/v1/llm4ad/research/sessions/${sessionId}/analysis/stream`

    ;(async () => {
      for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        if (cancelled) return
        try {
          const response = await authFetch(streamUrl, {
            signal: abortController.signal,
          })

          if (!response.ok) {
            if (attempt < MAX_RETRIES) {
              await new Promise((r) => setTimeout(r, RETRY_DELAY))
              continue
            }
            throw new Error(`HTTP ${response.status}`)
          }

          const reader = response.body!.getReader()
          const decoder = new TextDecoder()
          let buffer = ""
          let currentEvent = ""
          let currentData = ""

          while (true) {
            const { done, value } = await reader.read()
            if (done) break

            buffer += decoder.decode(value, { stream: true })
            const lines = buffer.split("\n")
            buffer = lines.pop() ?? ""

            for (const line of lines) {
              if (line.startsWith("event:")) {
                currentEvent = line.slice(6).trim()
              } else if (line.startsWith("data:")) {
                // 同一帧可有多条 data: 行，按 SSE 规范用换行拼接。
                const chunk = line.slice(5).trim()
                currentData = currentData ? `${currentData}\n${chunk}` : chunk
              } else if (line.trim() === "") {
                switch (currentEvent) {
                  case "connected":
                  case "heartbeat":
                    break

                  case "timeout":
                    if (!cancelled) {
                      setStatus("failed")
                      setError("stream timeout")
                      setIsStreaming(false)
                    }
                    return

                  case "done": {
                    if (!cancelled) {
                      try {
                        const parsed = JSON.parse(currentData)
                        if (parsed.content) {
                          contentBuffer.current = parsed.content
                          scheduleFlush()
                        }
                      } catch {
                        // ignore
                      }
                      setStatus("completed")
                      setIsStreaming(false)
                      invalidate()
                    }
                    return
                  }

                  default: {
                    if (!currentData) break
                    try {
                      const parsed = JSON.parse(currentData)
                      if (parsed.type === "chunk" && parsed.content) {
                        contentBuffer.current += parsed.content
                        scheduleFlush()
                      } else if (parsed.type === "done") {
                        if (!cancelled) {
                          setStatus("completed")
                          setIsStreaming(false)
                          invalidate()
                        }
                        return
                      } else if (parsed.type === "cancelled") {
                        if (!cancelled) {
                          setStatus("cancelled")
                          setIsStreaming(false)
                        }
                        return
                      } else if (parsed.type === "error") {
                        if (!cancelled) {
                          setStatus("failed")
                          setError(parsed.error || "generation failed")
                          setIsStreaming(false)
                        }
                        return
                      }
                    } catch {
                      // skip malformed JSON
                    }
                    break
                  }
                }

                currentEvent = ""
                currentData = ""
              }
            }
          }
          // 流干净关闭却未收到任何终态帧（done/timeout/error/cancelled）：
          // 收到终态帧的分支都在 switch 内直接 return，能走到这里说明连接被
          // 意外掐断。抛错交给下方 catch 的 retry/failed 兜底，避免永久卡在
          // “生成中”。
          throw new Error("analysis stream ended unexpectedly")
        } catch (err: unknown) {
          if (cancelled || (err as Error).name === "AbortError") return
          if (attempt < MAX_RETRIES) {
            await new Promise((r) => setTimeout(r, RETRY_DELAY))
            continue
          }
          setError((err as Error).message || "connection failed")
          setStatus("failed")
          setIsStreaming(false)
        }
      }
    })()

    return () => {
      cancelled = true
      abortController.abort()
      abortRef.current = null
      cancelAnimationFrame(rafId.current)
    }
  }, [sessionId, enabled, queryClient])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
    setStatus("cancelled")
  }, [])

  return { content, status, error, isStreaming, stop }
}
