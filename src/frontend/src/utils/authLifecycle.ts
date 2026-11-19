import axios from "axios"

import { ensureAccessToken } from "./auth"

/** Install wake listeners without polling or touching idle runtime containers. */
export function startAuthSessionLifecycle(): () => void {
  const wake = () => {
    if (document.visibilityState === "hidden") return
    void ensureAccessToken().catch(() => {
      // Keep credentials through offline periods; online/focus will retry.
    })
  }
  const events = ["focus", "pageshow", "online", "storage"] as const
  for (const event of events) window.addEventListener(event, wake)
  document.addEventListener("visibilitychange", wake)
  return () => {
    for (const event of events) window.removeEventListener(event, wake)
    document.removeEventListener("visibilitychange", wake)
  }
}

/** Renew only the trusted iframe's runtime ticket; no credentials cross frames. */
export function installEmbeddedAuthBridge(
  getFrameWindow: () => Window | null | undefined,
  renewSession: () => Promise<unknown>,
): () => void {
  let active = true
  let pending: Promise<unknown> | null = null
  const receive = async (event: MessageEvent) => {
    const target = getFrameWindow()
    if (
      !target ||
      event.source !== target ||
      event.origin !== window.location.origin ||
      event.data?.type !== "llm4ad:auth-renew" ||
      typeof event.data.requestId !== "string"
    )
      return
    const requestId = event.data.requestId
    let success = false
    let status: number | undefined
    try {
      if (!pending) {
        pending = (async () => {
          if (!(await ensureAccessToken()))
            throw new Error("Host session unavailable")
          return renewSession()
        })().finally(() => {
          pending = null
        })
      }
      await pending
      success = true
    } catch (error) {
      status = axios.isAxiosError(error)
        ? error.response?.status
        : (error as { status?: number } | null)?.status
    }
    if (active && getFrameWindow() === target) {
      target.postMessage(
        {
          type: "llm4ad:auth-renewed",
          requestId,
          success,
          ...(status ? { status } : {}),
        },
        event.origin,
      )
    }
  }
  window.addEventListener("message", receive)
  return () => {
    active = false
    window.removeEventListener("message", receive)
  }
}
