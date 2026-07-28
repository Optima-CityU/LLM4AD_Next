import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import type { ReportStatus, ReportType } from "@/client"
import { taskKeys } from "@/lib/task-queries"
import { authFetch } from "@/utils/auth"

interface UseReportStreamReturn {
  content: string
  status: ReportStatus | null
  error: string | null
  isStreaming: boolean
  progress: ReportProgress | null
  stop: () => void
}

export interface ReportProgress {
  stage: "prepare" | "summarize" | "merge" | "generate"
  round?: number
  completed?: number
  total?: number
}

const MAX_RETRIES = 2
const RETRY_DELAY = 1500

export function useReportStream(
  taskId: string,
  reportType: ReportType,
  enabled: boolean,
): UseReportStreamReturn {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const [content, setContent] = useState("")
  const [status, setStatus] = useState<ReportStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [progress, setProgress] = useState<ReportProgress | null>(null)
  const contentBuffer = useRef("")
  const rafId = useRef<number>(0)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!enabled) {
      setContent("")
      setStatus(null)
      setError(null)
      setIsStreaming(false)
      setProgress(null)
      contentBuffer.current = ""
      return
    }

    setIsStreaming(true)
    setError(null)
    setContent("")
    setStatus("generating")
    setProgress(null)
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

    const baseUrl = import.meta.env.VITE_API_URL || ""
    const streamUrl = `${baseUrl}/api/v1/llm4ad/tasks/${taskId}/reports/${reportType}/stream`

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
                currentData = line.slice(5).trim()
              } else if (line.trim() === "") {
                switch (currentEvent) {
                  case "connected":
                    break

                  case "heartbeat":
                    break

                  case "timeout":
                    if (!cancelled) {
                      setStatus("failed")
                      setError(t("evolution.reportStream.timeout"))
                      setIsStreaming(false)
                    }
                    currentEvent = ""
                    currentData = ""
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
                      queryClient.invalidateQueries({
                        queryKey: taskKeys.detail(taskId),
                      })
                    }
                    currentEvent = ""
                    currentData = ""
                    return
                  }

                  case "data":
                  case "message":
                  case "": {
                    if (!currentData) break
                    try {
                      const parsed = JSON.parse(currentData)
                      if (parsed.type === "chunk" && parsed.content) {
                        contentBuffer.current += parsed.content
                        scheduleFlush()
                      } else if (parsed.type === "progress") {
                        setProgress({
                          stage: parsed.stage,
                          round: parsed.round,
                          completed: parsed.completed,
                          total: parsed.total,
                        })
                      } else if (parsed.type === "done") {
                        if (!cancelled) {
                          setStatus("completed")
                          setIsStreaming(false)
                          queryClient.invalidateQueries({
                            queryKey: taskKeys.detail(taskId),
                          })
                        }
                        currentEvent = ""
                        currentData = ""
                        return
                      } else if (parsed.type === "error") {
                        if (!cancelled) {
                          setStatus("failed")
                          setError(
                            parsed.error ||
                              t("evolution.reportStream.generateFailed"),
                          )
                          setIsStreaming(false)
                        }
                        currentEvent = ""
                        currentData = ""
                        return
                      }
                    } catch {
                      // skip malformed JSON
                    }
                    break
                  }

                  default:
                    break
                }

                currentEvent = ""
                currentData = ""
              }
            }
          }
          return
        } catch (err: unknown) {
          if (cancelled || (err as Error).name === "AbortError") return
          if (attempt < MAX_RETRIES) {
            await new Promise((r) => setTimeout(r, RETRY_DELAY))
            continue
          }
          setError(
            (err as Error).message ||
              t("evolution.reportStream.connectionFailed"),
          )
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
  }, [taskId, reportType, enabled, queryClient, t])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
    setStatus("cancelled")
  }, [])

  return { content, status, error, isStreaming, progress, stop }
}
