import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { ResearchArtifactTranslateRequest } from "@/client"
import { authFetch } from "@/utils/auth"

import {
  buildResearchArtifactTranslateStreamUrl,
  translateResearchArtifact,
} from "./useAutoResearch"

export type ArtifactTranslationLanguage = "zh" | "en"
export type ArtifactTranslationStatus =
  | "idle"
  | "cached"
  | "translating"
  | "completed"
  | "failed"
  | "cancelled"

interface UseArtifactTranslationOptions {
  sessionId: string
  filePath: string | null
  enabled: boolean
}

interface StartOptions {
  targetLanguage?: ArtifactTranslationLanguage
  force?: boolean
  providerId?: string | null
  modelName?: string | null
}

interface UseArtifactTranslationReturn {
  content: string
  error: string | null
  status: ArtifactTranslationStatus
  targetLanguage: ArtifactTranslationLanguage
  isStreaming: boolean
  hasTranslation: boolean
  startTranslation: (opts?: StartOptions) => Promise<void>
  stop: () => void
  reset: () => void
}

const MAX_RETRIES = 2
const RETRY_DELAY = 1500

/**
 * 产物翻译请求 + SSE 流：统一处理 cached / translating / retry / abort / reset。
 *
 * 状态严格绑定到当前 ``filePath``；文件切换或 ``enabled=false`` 时会自动清空，
 * 防止不同产物之间串内容。
 */
export function useArtifactTranslation({
  sessionId,
  filePath,
  enabled,
}: UseArtifactTranslationOptions): UseArtifactTranslationReturn {
  const [content, setContent] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<ArtifactTranslationStatus>("idle")
  const [targetLanguage, setTargetLanguage] =
    useState<ArtifactTranslationLanguage>("zh")
  const [isStreaming, setIsStreaming] = useState(false)

  const contentBuffer = useRef("")
  const rafId = useRef<number>(0)
  const abortRef = useRef<AbortController | null>(null)
  const streamKeyRef = useRef<string | null>(null)
  const prevFilePathRef = useRef<string | null>(null)

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    cancelAnimationFrame(rafId.current)
    contentBuffer.current = ""
    streamKeyRef.current = null
    setContent("")
    setError(null)
    setStatus("idle")
    setIsStreaming(false)
  }, [])

  useEffect(() => {
    if (!enabled || !filePath) {
      prevFilePathRef.current = filePath
      reset()
      return
    }
    if (prevFilePathRef.current !== filePath) {
      prevFilePathRef.current = filePath
      reset()
    }
    return () => {
      cancelAnimationFrame(rafId.current)
    }
  }, [enabled, filePath, reset])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    cancelAnimationFrame(rafId.current)
    setIsStreaming(false)
    setStatus((prev) => (prev === "translating" ? "cancelled" : prev))
  }, [])

  const hasTranslation = useMemo(
    () => !!content || status === "cached" || status === "completed",
    [content, status],
  )

  const startTranslation = useCallback(
    async (opts?: StartOptions) => {
      if (!enabled || !filePath) return

      try {
        const lang = opts?.targetLanguage ?? "zh"
        setTargetLanguage(lang)
        setError(null)

        abortRef.current?.abort()
        abortRef.current = null
        cancelAnimationFrame(rafId.current)
        contentBuffer.current = ""
        setContent("")
        setIsStreaming(false)

        const body: ResearchArtifactTranslateRequest = {
          target_language: lang,
          force: opts?.force ?? false,
          provider_id: opts?.providerId ?? undefined,
          model_name: opts?.modelName ?? undefined,
        }

        const response = await translateResearchArtifact(sessionId, filePath, body)
        if (response.target_language === "zh" || response.target_language === "en") {
          setTargetLanguage(response.target_language)
        }

        if (response.status === "cached") {
          contentBuffer.current = response.content ?? ""
          setContent(contentBuffer.current)
          setStatus("cached")
          setIsStreaming(false)
          return
        }

        const streamUrl = buildResearchArtifactTranslateStreamUrl(
          sessionId,
          response.source_hash,
          response.target_language || lang,
        )
        streamKeyRef.current = `${response.source_hash}:${response.target_language || lang}`
        setStatus("translating")
        setIsStreaming(true)

        const abortController = new AbortController()
        abortRef.current = abortController
        let cancelled = false

        const scheduleFlush = () => {
          cancelAnimationFrame(rafId.current)
          rafId.current = requestAnimationFrame(() => {
            if (!cancelled) setContent(contentBuffer.current)
          })
        }

        try {
          for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            if (cancelled) return
            try {
              const resp = await authFetch(streamUrl, {
                signal: abortController.signal,
              })
              if (!resp.ok) {
                if (attempt < MAX_RETRIES) {
                  await new Promise((r) => setTimeout(r, RETRY_DELAY))
                  continue
                }
                throw new Error(`HTTP ${resp.status}`)
              }

              const reader = resp.body?.getReader()
              if (!reader) throw new Error("empty stream body")
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
                    const chunk = line.slice(5).trim()
                    currentData = currentData ? `${currentData}\n${chunk}` : chunk
                  } else if (line.trim() === "") {
                    if (currentEvent === "connected" || currentEvent === "heartbeat") {
                      currentEvent = ""
                      currentData = ""
                      continue
                    }
                    if (currentEvent === "done") {
                      setStatus("completed")
                      setIsStreaming(false)
                      return
                    }
                    if (currentEvent === "timeout") {
                      setStatus("failed")
                      setError("stream timeout")
                      setIsStreaming(false)
                      return
                    }
                    if (currentData) {
                      try {
                        const parsed = JSON.parse(currentData)
                        if (parsed.type === "chunk" && parsed.content) {
                          contentBuffer.current += parsed.content
                          scheduleFlush()
                        } else if (parsed.type === "done") {
                          setStatus("completed")
                          setIsStreaming(false)
                          return
                        } else if (parsed.type === "cancelled") {
                          setStatus("cancelled")
                          setIsStreaming(false)
                          return
                        } else if (parsed.type === "error") {
                          setStatus("failed")
                          setError(parsed.error || "translation failed")
                          setIsStreaming(false)
                          return
                        }
                      } catch {
                        // skip malformed JSON frames
                      }
                    }
                    currentEvent = ""
                    currentData = ""
                  }
                }
              }
              throw new Error("translation stream ended unexpectedly")
            } catch (err) {
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
        } finally {
          cancelled = true
          abortRef.current = null
          cancelAnimationFrame(rafId.current)
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return
        setError((err as Error).message || "translation failed")
        setStatus("failed")
        setIsStreaming(false)
      }
    },
    [enabled, filePath, sessionId],
  )

  return {
    content,
    error,
    status,
    targetLanguage,
    isStreaming,
    hasTranslation,
    startTranslation,
    stop,
    reset,
  }
}
