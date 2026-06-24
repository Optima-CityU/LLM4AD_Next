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
import { ThemeProvider } from "./components/theme-provider"
import { Toaster } from "./components/ui/sonner"
import "./index.css"
import { setupAnalytics } from "./lib/analytics"
import { routeTree } from "./routeTree.gen"
import { checkMaintenance, showMaintenanceOverlay } from "./utils/maintenance"
import { initOpenApi } from "./utils/request"

initOpenApi()

// Lazily-loaded route chunks are fetched via dynamic import(), which bypasses
// the axios interceptor. When such a chunk 503s behind the maintenance gateway,
// Vite fires `vite:preloadError` instead. preventDefault() must run
// synchronously to swallow the default rethrow (which would surface the router
// error page); we then probe the cause:
//   - maintenance  -> show the overlay
//   - stale deploy -> reload to pick up the new chunk hashes. Throttled by a
//     timestamp (not a one-shot flag) so a later deploy in the same tab can
//     still self-heal, while a tight reload loop is still prevented.
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault()
  void checkMaintenance().then((isMaintenance) => {
    if (isMaintenance) {
      showMaintenanceOverlay()
      return
    }
    const RELOAD_KEY = "llm4ad-chunk-reload-ts"
    const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0)
    if (Date.now() - last > 10_000) {
      sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
      window.location.reload()
    }
  })
})

const handleApiError = (error: Error) => {
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
setupAnalytics(router)

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
        <Toaster richColors closeButton position="top-center" expand gap={6} />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
