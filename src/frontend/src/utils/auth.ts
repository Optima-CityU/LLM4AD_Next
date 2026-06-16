import axios from "axios"

let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string) => void
  reject: (error: unknown) => void
}> = []

function processQueue(error: unknown, token: string | null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) {
      reject(error)
    } else {
      resolve(token!)
    }
  })
  failedQueue = []
}

export const AUTH_PAGES = [
  "/login",
  "/signup",
  "/verify-email",
  "/recover-password",
  "/reset-password",
]

export function buildLoginRedirectUrl(): string {
  const currentPath = window.location.pathname
  if (AUTH_PAGES.some((p) => currentPath.startsWith(p))) {
    return "/login"
  }
  const redirectTarget = currentPath + window.location.search
  return `/login?redirect=${encodeURIComponent(redirectTarget)}`
}

export function clearAuthAndRedirect() {
  localStorage.removeItem("access_token")
  localStorage.removeItem("refresh_token")
  document.cookie = "code_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/"
  window.location.href = buildLoginRedirectUrl()
}

async function refreshToken(): Promise<string | null> {
  const refresh = localStorage.getItem("refresh_token")
  if (!refresh) return null

  try {
    const baseUrl = import.meta.env.VITE_API_URL || ""
    const response = await axios.post(`${baseUrl}/api/v1/login/refresh-token`, {
      refresh_token: refresh,
    })
    const { access_token, refresh_token } = response.data
    localStorage.setItem("access_token", access_token)
    localStorage.setItem("refresh_token", refresh_token)
    return access_token
  } catch {
    return null
  }
}

/**
 * Fetch wrapper with automatic 401 retry via token refresh.
 * Use this for SSE/streaming requests that bypass the axios interceptor.
 */
export async function authFetch(
  url: string,
  init?: RequestInit,
): Promise<Response> {
  const token = localStorage.getItem("access_token") || ""
  const headers = new Headers(init?.headers)
  headers.set("Authorization", `Bearer ${token}`)

  const response = await fetch(url, { ...init, headers })

  if (response.status === 401) {
    try {
      const newToken = await handleUnauthorized()
      headers.set("Authorization", `Bearer ${newToken}`)
      return fetch(url, { ...init, headers })
    } catch {
      throw new Error(`HTTP 401`)
    }
  }

  return response
}

export async function handleUnauthorized(): Promise<string> {
  if (isRefreshing) {
    return new Promise<string>((resolve, reject) => {
      failedQueue.push({ resolve, reject })
    })
  }

  isRefreshing = true
  try {
    const newToken = await refreshToken()
    if (!newToken) {
      processQueue(new Error("refresh failed"), null)
      clearAuthAndRedirect()
      return Promise.reject(new Error("refresh failed"))
    }
    processQueue(null, newToken)
    return newToken
  } catch (error) {
    processQueue(error, null)
    clearAuthAndRedirect()
    return Promise.reject(error)
  } finally {
    isRefreshing = false
  }
}
