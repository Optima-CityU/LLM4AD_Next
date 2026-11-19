import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { OpenAPI } from "../src/client"
import { ThemeProvider } from "../src/components/theme-provider"
import { TooltipProvider } from "../src/components/ui/tooltip"
import { PaperWorkbench } from "../src/routes/_layout/papers"
import "../src/i18n"
import "../src/index.css"

OpenAPI.BASE = location.origin
OpenAPI.TOKEN = "proposal-test-token"
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})
function Fixture() {
  const [workspaceId, setWorkspaceId] = useState("workspace-a")
  return (
    <main className="flex h-screen flex-col">
      <nav className="flex gap-3 p-2">
        <button type="button" onClick={() => setWorkspaceId("workspace-a")}>
          Workspace A
        </button>
        <button type="button" onClick={() => setWorkspaceId("workspace-b")}>
          Workspace B
        </button>
        <button
          type="button"
          onClick={() => {
            void client.invalidateQueries()
          }}
        >
          Refresh workspace
        </button>
      </nav>
      <div className="min-h-0 flex-1">
        <PaperWorkbench workspaceId={workspaceId} />
      </div>
    </main>
  )
}
const route = createRootRoute({ component: Fixture })
const router = createRouter({
  routeTree: route,
  history: createMemoryHistory({ initialEntries: ["/"] }),
})
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>,
)
