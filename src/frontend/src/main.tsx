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
import { initOpenApi } from "./utils/request"

initOpenApi()

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
