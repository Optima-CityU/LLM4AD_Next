import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { ResearchArtifactTranslateRequest } from "@/client"
import { authFetch } from "@/utils/auth"

import {
  buildResearchArtifactTranslateStreamUrl,
  stopTranslateResearchArtifact,
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
 * 每次 ``startTranslation`` / ``reset`` / ``stop`` / 文件切换都会递增内部 runId 令牌，
 * 使上一轮订阅遗留的 rAF 回调、状态更新、拼接逻辑全部失效——彻底避免快速切换文件
 * 时旧流把内容写回、或与新流争抢同一缓冲区导致「显示上次结果」的串内容问题。
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

  // 每次订阅递增，旧闭包捕获的 runId 与之不符即视为已失效。
  const runIdRef = useRef(0)
  const rafId = useRef<number>(0)
  const abortRef = useRef<AbortController | null>(null)
  const prevFilePathRef = useRef<string | null>(null)
  // 当前在跑翻译的键（source_hash + lang）；停止时据此通知后端取消，非流式时为 null。
  const activeStreamRef = useRef<{
    sourceHash: string
    targetLanguage: string
  } | null>(null)

  /** 让当前所有在途订阅失效：递增令牌、中止请求、取消待执行的 rAF。 */
  const invalidate = useCallback(() => {
    runIdRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    activeStreamRef.current = null
    cancelAnimationFrame(rafId.current)
  }, [])

  const reset = useCallback(() => {
    invalidate()
    setContent("")
    setError(null)
    setStatus("idle")
    setIsStreaming(false)
  }, [invalidate])

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
    // 组件卸载 / 弹框关闭时，中止离开文件的流订阅。
    return () => {
      invalidate()
    }
  }, [enabled, filePath, reset, invalidate])

  const stop = useCallback(() => {
    // 先取出在跑流的键（invalidate 会清空 ref），再通知后端协作式取消：
    // 只关本地 SSE 无法中断后台翻译协程，会继续烧 token 并落缓存。
    const active = activeStreamRef.current
    invalidate()
    setIsStreaming(false)
    setStatus((prev) => (prev === "translating" ? "cancelled" : prev))
    if (active) {
      void stopTranslateResearchArtifact(
        sessionId,
        active.sourceHash,
        active.targetLanguage,
      ).catch(() => {
        // 取消是尽力而为：后端幂等（无在跑任务返回 idle），失败不影响本地终止。
      })
    }
  }, [invalidate, sessionId])

  const hasTranslation = useMemo(
    () => !!content || status === "cached" || status === "completed",
    [content, status],
  )

  const startTranslation = useCallback(
    async (opts?: StartOptions) => {
      if (!enabled || !filePath) return

      // 开新订阅：先失效上一轮，再登记本轮 runId，后续所有写入都以此为准。
      invalidate()
      const runId = runIdRef.current
      const isCurrent = () => runIdRef.current === runId

      // 每轮独立缓冲区，绝不与其它订阅共享，从根上杜绝串内容。
      let buffer = ""

      try {
        const lang = opts?.targetLanguage ?? "zh"
        setTargetLanguage(lang)
        setError(null)
        // 订阅前清空当前译文渲染，避免旧结果残留。
        setContent("")
        setIsStreaming(false)

        const body: ResearchArtifactTranslateRequest = {
          target_language: lang,
          force: opts?.force ?? false,
          provider_id: opts?.providerId ?? undefined,
          model_name: opts?.modelName ?? undefined,
        }

        const response = await translateResearchArtifact(sessionId, filePath, body)
        if (!isCurrent()) return
        if (response.target_language === "zh" || response.target_language === "en") {
          setTargetLanguage(response.target_language)
        }

        if (response.status === "cached") {
          buffer = response.content ?? ""
          setContent(buffer)
          setStatus("cached")
          setIsStreaming(false)
          return
        }

        const streamUrl = buildResearchArtifactTranslateStreamUrl(
          sessionId,
          response.source_hash,
          response.target_language || lang,
        )
        setStatus("translating")
        setIsStreaming(true)
        // 记录在跑流的键：stop 时据此通知后端取消后台协程。
        activeStreamRef.current = {
          sourceHash: response.source_hash,
          targetLanguage: response.target_language || lang,
        }

        const abortController = new AbortController()
        abortRef.current = abortController

        const scheduleFlush = () => {
          cancelAnimationFrame(rafId.current)
          rafId.current = requestAnimationFrame(() => {
            if (isCurrent()) setContent(buffer)
          })
        }

        const flushContent = () => {
          cancelAnimationFrame(rafId.current)
          if (isCurrent()) setContent(buffer)
        }

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
          if (!isCurrent()) return
          try {
            const resp = await authFetch(streamUrl, {
              signal: abortController.signal,
            })
            if (!isCurrent()) return
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
            let raw = ""
            let currentEvent = ""
            let currentData = ""

            while (true) {
              const { done, value } = await reader.read()
              if (done) break
              if (!isCurrent()) return

              raw += decoder.decode(value, { stream: true })
              const lines = raw.split("\n")
              raw = lines.pop() ?? ""

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
                    flushContent()
                    if (isCurrent()) {
                      setStatus("completed")
                      setIsStreaming(false)
                    }
                    return
                  }
                  if (currentEvent === "timeout") {
                    flushContent()
                    if (isCurrent()) {
                      setStatus("failed")
                      setError("stream timeout")
                      setIsStreaming(false)
                    }
                    return
                  }
                  if (currentData) {
                    try {
                      const parsed = JSON.parse(currentData)
                      if (parsed.type === "chunk" && parsed.content) {
                        buffer += parsed.content
                        scheduleFlush()
                      } else if (parsed.type === "done") {
                        flushContent()
                        if (isCurrent()) {
                          setStatus("completed")
                          setIsStreaming(false)
                        }
                        return
                      } else if (parsed.type === "cancelled") {
                        flushContent()
                        if (isCurrent()) {
                          setStatus("cancelled")
                          setIsStreaming(false)
                        }
                        return
                      } else if (parsed.type === "error") {
                        flushContent()
                        if (isCurrent()) {
                          setStatus("failed")
                          setError(parsed.error || "translation failed")
                          setIsStreaming(false)
                        }
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
            flushContent()
            throw new Error("translation stream ended unexpectedly")
          } catch (err) {
            if (!isCurrent() || (err as Error).name === "AbortError") return
            if (attempt < MAX_RETRIES) {
              await new Promise((r) => setTimeout(r, RETRY_DELAY))
              continue
            }
            flushContent()
            setError((err as Error).message || "connection failed")
            setStatus("failed")
            setIsStreaming(false)
          }
        }
      } catch (err) {
        if (!isCurrent() || (err as Error).name === "AbortError") return
        setContent(buffer)
        setError((err as Error).message || "translation failed")
        setStatus("failed")
        setIsStreaming(false)
      } finally {
        if (isCurrent() && abortRef.current) {
          abortRef.current = null
        }
      }
    },
    [enabled, filePath, sessionId, invalidate],
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
