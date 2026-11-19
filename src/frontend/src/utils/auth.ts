import axios from "axios"

// One renewal owns each refresh credential, including concurrent fetch and SDK calls.
let renewal: { credential: string; promise: Promise<string> } | null = null

export const AUTH_PAGES = [
  "/login",
  "/signup",
  "/verify-email",
  "/recover-password",
  "/reset-password",
]

export function buildLoginRedirectUrl(): string {
  const currentPath = window.location.pathname
  if (AUTH_PAGES.some((p) => currentPath.startsWith(p))) return "/login"
  const redirectTarget =
    currentPath + window.location.search + window.location.hash
  return `/login?redirect=${encodeURIComponent(redirectTarget)}`
}

export function clearAuthAndRedirect() {
  localStorage.removeItem("access_token")
  localStorage.removeItem("refresh_token")
  document.cookie = "code_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/"
  window.location.href = buildLoginRedirectUrl()
}

/** Renew credentials after a 401, reusing a newer token for delayed responses. */
export function handleUnauthorized(
  failedToken?: string | null,
): Promise<string> {
  const currentToken = localStorage.getItem("access_token")
  if (failedToken && currentToken && currentToken !== failedToken) {
    return Promise.resolve(currentToken)
  }
  const credential = localStorage.getItem("refresh_token")
  if (!credential) {
    clearAuthAndRedirect()
    return Promise.reject(new Error("Session expired"))
  }
  if (renewal?.credential === credential) return renewal.promise

  const promise = (async () => {
    try {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await axios.post(
        `${baseUrl}/api/v1/login/refresh-token`,
        {
          refresh_token: credential,
        },
        { timeout: 15_000 },
      )
      // Logout or another tab's login must win over a late renewal response.
      if (localStorage.getItem("refresh_token") !== credential) {
        const replacement = localStorage.getItem("access_token")
        if (replacement) return replacement
        throw new Error("Session changed during renewal")
      }
      const { access_token, refresh_token } = response.data
      if (
        typeof access_token !== "string" ||
        !access_token ||
        typeof refresh_token !== "string" ||
        !refresh_token
      ) {
        throw new Error("Invalid session renewal response")
      }
      localStorage.setItem("access_token", access_token)
      localStorage.setItem("refresh_token", refresh_token)
      return access_token
    } catch (error) {
      if (localStorage.getItem("refresh_token") !== credential) {
        const replacement = localStorage.getItem("access_token")
        if (replacement) return replacement
      } else if (
        axios.isAxiosError(error) &&
        [401, 403].includes(error.response?.status ?? 0)
      ) {
        clearAuthAndRedirect()
      }
      // Network failures, timeouts, maintenance and malformed responses are retryable.
      throw error
    } finally {
      if (renewal?.credential === credential) renewal = null
    }
  })()
  renewal = { credential, promise }
  return promise
}

/** Refresh near-expiry host tokens on activity; decoded claims are only a hint. */
export async function ensureAccessToken(): Promise<string | null> {
  const token = localStorage.getItem("access_token")
  if (!token) return null
  let expiresAt: unknown
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")
    expiresAt = JSON.parse(
      atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, "=")),
    ).exp
  } catch {
    return token
  }
  if (
    typeof expiresAt === "number" &&
    Number.isFinite(expiresAt) &&
    expiresAt * 1000 <= Date.now() + 60_000
  ) {
    return handleUnauthorized(token)
  }
  return token
}

/** Fetch with one auth retry; transport failures never replay a submitted body. */
export async function authFetch(
  url: string,
  init?: RequestInit,
): Promise<Response> {
  const token = localStorage.getItem("access_token") || ""
  const headers = new Headers(init?.headers)
  headers.set("Authorization", `Bearer ${token}`)
  const response = await fetch(url, { ...init, headers })
  if (response.status !== 401) return response

  const newToken = await handleUnauthorized(token)
  init?.signal?.throwIfAborted()
  headers.set("Authorization", `Bearer ${newToken}`)
  const retried = await fetch(url, { ...init, headers })
  if (
    retried.status === 401 &&
    localStorage.getItem("access_token") === newToken
  ) {
    clearAuthAndRedirect()
  }
  return retried
}
