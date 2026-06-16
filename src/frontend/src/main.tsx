import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { ApiError } from "./client"
import { clearAuthAndRedirect } from "./utils/auth"
import "./i18n"
import { UpdateNotifyDialog } from "./components/Common/UpdateNotifyDialog"
import { ThemeProvider } from "./components/theme-provider"
import { Toaster } from "./components/ui/sonner"
import "./index.css"
import { routeTree } from "./routeTree.gen"
import { initOpenApi } from "./utils/request"
import { registerStaleAssetDetection } from "./utils/staleAssets"
import { notifyRefresh } from "./utils/updateNotify"

initOpenApi()
registerStaleAssetDetection()

/** Gateway statuses that indicate the backend is restarting during a deploy. */
const DEPLOYING_STATUSES = new Set([502, 503, 504])

/**
 * Whether an error is a network-layer failure (no HTTP response received),
 * e.g. the backend container is down and the connection is refused.
 */
const isNetworkError = (error: unknown): boolean => {
  const code = (error as { code?: string } | null)?.code
  return code === "ERR_NETWORK" || code === "ECONNABORTED"
}

const handleApiError = (error: Error) => {
  // Backend redeploy: gateway 5xx or a refused connection -> prompt refresh.
  if (
    (error instanceof ApiError && DEPLOYING_STATUSES.has(error.status)) ||
    isNetworkError(error)
  ) {
    notifyRefresh("deploying")
    return
  }
  if (error instanceof ApiError && error.status === 403) {
    // 检查是否是隐私协议相关的403错误
    const errorDetail = (error.body as any)?.detail || ""
    if (
      errorDetail.includes("隐私协议") ||
      errorDetail.includes("Privacy Policy")
    ) {
      // 隐私协议错误不清除登录状态，由登录页面处理
      return
    }
    clearAuthAndRedirect()
  }
}
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
    mutations: {
      retry: false,
    },
  },
  queryCache: new QueryCache({
    onError: handleApiError,
  }),
  mutationCache: new MutationCache({
    onError: handleApiError,
  }),
})

const router = createRouter({ routeTree })
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      storageKey="llm4ad-theme"
      enableSystem={false}
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <UpdateNotifyDialog />
        <Toaster richColors closeButton position="top-center" expand gap={6} />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
