import type { AxiosResponse } from "axios"
import axios from "axios"

import { OpenAPI } from "@/client"
import {
  clearAuthAndRedirect,
  ensureAccessToken,
  handleUnauthorized,
} from "./auth"
import { startAuthSessionLifecycle } from "./authLifecycle"
import { showMaintenanceOverlay } from "./maintenance"

let lifecycleCleanup: (() => void) | undefined

export const initOpenApi = () => {
  lifecycleCleanup?.()
  lifecycleCleanup = startAuthSessionLifecycle()
  OpenAPI.BASE = import.meta.env.VITE_API_URL || ""
  OpenAPI.TOKEN = async () => {
    return (await ensureAccessToken()) || ""
  }

  // The legacy generated axios client needs a binary response mode for files.
  OpenAPI.interceptors.request.use((request) => {
    if (
      /\/tasks\/[^/]+\/evaluations\/[^/]+\/artifacts\//.test(request.url ?? "")
    ) {
      request.responseType = "blob"
    }
    return request
  })

  OpenAPI.interceptors.response.use(async (response: AxiosResponse) => {
    if (response.status === 503 && response.headers["x-maintenance"]) {
      showMaintenanceOverlay()
      return response
    }

    if (response.status === 401) {
      const originalRequest = response.config
      if (originalRequest.url?.includes("/login/refresh-token")) {
        return response
      }
      if (originalRequest.url?.includes("/login/access-token")) return response
      const authorization = originalRequest.headers.Authorization
      const failedToken =
        typeof authorization === "string"
          ? authorization.replace(/^Bearer /i, "")
          : null
      const newToken = await handleUnauthorized(failedToken)
      originalRequest.headers.Authorization = `Bearer ${newToken}`
      const retryResponse = await axios.request({
        ...originalRequest,
        validateStatus: () => true,
      })
      if (
        retryResponse.status === 401 &&
        localStorage.getItem("access_token") === newToken
      ) {
        clearAuthAndRedirect()
      }
      return retryResponse
    }
    return response
  })
}
