const OVERLAY_ID = "llm4ad-maintenance-overlay"
const MAINTENANCE_URL = "/maintenance.html"

// 维护结束探测：同源轮询 health-check，维护中经网关返回 503(+x-maintenance)，
// 恢复后返回 200。命中恢复即整页刷新，拿到最新静态资源与干净状态。
const HEALTH_CHECK_URL = "/api/v1/utils/health-check/"
const POLL_INTERVAL_MS = 5000
let pollTimer: ReturnType<typeof setInterval> | null = null

/**
 * 遮罩可见期间轮询服务是否恢复。幂等：已在轮询则直接返回，避免叠加。
 * 一旦探测到恢复立即整页 reload —— 遮罩随刷新自然消失。
 */
function startMaintenancePolling(): void {
  if (pollTimer || typeof window === "undefined") {
    return
  }
  let inFlight = false
  pollTimer = setInterval(async () => {
    // 防止慢响应时请求堆叠。
    if (inFlight) {
      return
    }
    inFlight = true
    try {
      // 原生 fetch 而非 OpenAPI client，避开响应拦截器再入与鉴权头。
      const res = await fetch(HEALTH_CHECK_URL, {
        method: "GET",
        cache: "no-store",
      })
      if (res.ok && !res.headers.get("x-maintenance")) {
        if (pollTimer) {
          clearInterval(pollTimer)
          pollTimer = null
        }
        window.location.reload()
      }
    } catch {
      // 网关/服务仍在重启中，忽略本次，等下一轮。
    } finally {
      inFlight = false
    }
  }, POLL_INTERVAL_MS)
}

function stopMaintenancePolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/**
 * Probe whether the backend is currently in maintenance mode.
 *
 * Used for failures that never reach the axios interceptor — most notably
 * dynamic-import / module-preload errors when a lazily-loaded route chunk
 * (e.g. `/assets/_layout_evolution-*.js`) returns 503 behind the gateway.
 *
 * @returns `true` when the gateway answers 503 with the `x-maintenance` header.
 */
export async function checkMaintenance(): Promise<boolean> {
  if (typeof window === "undefined") {
    return false
  }
  try {
    const res = await fetch(HEALTH_CHECK_URL, {
      method: "GET",
      cache: "no-store",
      // Cap the probe so a slow/hung gateway during maintenance doesn't leave
      // this promise pending forever.
      signal: AbortSignal.timeout(8000),
    })
    return res.status === 503 && res.headers.get("x-maintenance") !== null
  } catch {
    return false
  }
}

/**
 * Show a fullscreen maintenance overlay that embeds the static
 * `/maintenance.html` page in a sandboxed iframe.
 *
 * Calling this repeatedly is idempotent: a single overlay is kept instead of
 * stacking multiple iframes.
 */
export function showMaintenanceOverlay(): void {
  if (typeof document === "undefined") {
    return
  }

  if (document.getElementById(OVERLAY_ID)) {
    // 遮罩已存在：确保轮询在跑（幂等），避免“遮罩在但轮询没起”。
    startMaintenancePolling()
    return
  }

  const overlay = document.createElement("div")
  overlay.id = OVERLAY_ID
  Object.assign(overlay.style, {
    position: "fixed",
    inset: "0",
    zIndex: "2147483647",
    background: "#ffffff",
  } satisfies Partial<CSSStyleDeclaration>)

  const iframe = document.createElement("iframe")
  iframe.src = MAINTENANCE_URL
  iframe.setAttribute("title", "Maintenance")
  Object.assign(iframe.style, {
    width: "100%",
    height: "100%",
    border: "0",
  } satisfies Partial<CSSStyleDeclaration>)

  overlay.appendChild(iframe)
  document.body.appendChild(overlay)

  // Prevent the page behind the overlay from scrolling.
  document.documentElement.style.overflow = "hidden"

  // 开始探测维护是否结束，恢复后整页刷新。
  startMaintenancePolling()
}

/** Remove the maintenance overlay if present and restore page scrolling. */
export function hideMaintenanceOverlay(): void {
  if (typeof document === "undefined") {
    return
  }
  stopMaintenancePolling()
  const overlay = document.getElementById(OVERLAY_ID)
  if (overlay) {
    overlay.remove()
    document.documentElement.style.overflow = ""
  }
}
