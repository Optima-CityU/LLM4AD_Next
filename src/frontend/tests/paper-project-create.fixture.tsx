import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { createRoot } from "react-dom/client"
import { Toaster } from "sonner"
import { OpenAPI } from "@/client"
import { ThemeProvider } from "@/components/theme-provider"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Route as ProjectsRoute } from "@/routes/_layout/papers"
import { Route as WorkbenchRoute } from "@/routes/_layout_paper/papers.$workspaceId"
import "@/i18n"
import "@/index.css"

OpenAPI.BASE = location.origin
OpenAPI.TOKEN = "paper-intake-test-token"

const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})
const mode = new URLSearchParams(location.search).get("mode") ?? "proposal"
const root = createRootRoute()
const router = createRouter({
  routeTree: root.addChildren([
    ProjectsRoute.update({
      id: "/papers",
      path: "/papers",
      getParentRoute: () => root,
    } as Parameters<typeof ProjectsRoute.update>[0]),
    WorkbenchRoute.update({
      id: "/papers/$workspaceId",
      path: "/papers/$workspaceId",
      getParentRoute: () => root,
    } as Parameters<typeof WorkbenchRoute.update>[0]),
  ]),
  history: createMemoryHistory({ initialEntries: [`/papers?mode=${mode}`] }),
})

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>,
)
