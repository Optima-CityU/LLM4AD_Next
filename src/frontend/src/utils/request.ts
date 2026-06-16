import type { AxiosResponse } from "axios"
import axios from "axios"

import { OpenAPI } from "@/client"
import { handleUnauthorized } from "./auth"
import { notifyRefresh } from "./updateNotify"

/** Gateway statuses that indicate the backend is restarting during a deploy. */
const DEPLOYING_STATUSES = new Set([502, 503, 504])

export const initOpenApi = () => {
  OpenAPI.BASE = import.meta.env.VITE_API_URL
  OpenAPI.TOKEN = async () => {
    return localStorage.getItem("access_token") || ""
  }

  OpenAPI.interceptors.response.use(async (response: AxiosResponse) => {
    if (DEPLOYING_STATUSES.has(response.status)) {
      notifyRefresh("deploying")
      return response
    }
    if (response.status === 401) {
      const originalRequest = response.config
      if (originalRequest.url?.includes("/login/refresh-token")) {
        return response
      }
      try {
        const newToken = await handleUnauthorized()
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        const retryResponse = await axios.request(originalRequest)
        return retryResponse
      } catch {
        return response
      }
    }
    return response
  })
}
