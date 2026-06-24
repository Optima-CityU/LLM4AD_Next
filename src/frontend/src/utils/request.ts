import type { AxiosResponse } from "axios"
import axios from "axios"

import { OpenAPI } from "@/client"
import { handleUnauthorized } from "./auth"
import { showMaintenanceOverlay } from "./maintenance"

export const initOpenApi = () => {
  OpenAPI.BASE = import.meta.env.VITE_API_URL
  OpenAPI.TOKEN = async () => {
    return localStorage.getItem("access_token") || ""
  }

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
