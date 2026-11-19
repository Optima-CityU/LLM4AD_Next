import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { I18nextProvider } from "react-i18next"
import { OpenAPI } from "../src/client"
import PaperRegionWorkbench, {
  PaperRegionProvider,
} from "../src/components/Paper/PaperRegionWorkbench"
import { paperRegionKeys } from "../src/hooks/usePaperRegions"
import i18n from "../src/i18n"
import type {
  PaperRegionAgentRequest,
  PaperRegionView,
} from "../src/lib/paperRegions"
import "../src/index.css"

OpenAPI.BASE = window.location.origin
OpenAPI.TOKEN = "regions-fixture-token"
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})

function Fixture() {
  const queryClient = useQueryClient()
  const [mount, setMount] = useState(0)
  const [dispatches, setDispatches] = useState<PaperRegionAgentRequest[]>([])
  const [source, setSource] = useState("")
  const params = new URLSearchParams(window.location.search)
  const workspaceId = params.get("workspace") ?? "workspace-a"
  return (
    <I18nextProvider i18n={i18n}>
      <main className="mx-auto max-w-3xl space-y-4 p-5">
        <button
          type="button"
          onClick={() =>
            void queryClient.invalidateQueries({
              queryKey: paperRegionKeys.snapshot(workspaceId),
            })
          }
        >
          Refresh regions
        </button>
        <button type="button" onClick={() => setMount((value) => value + 1)}>
          Remount regions
        </button>
        <output data-testid="dispatches">{JSON.stringify(dispatches)}</output>
        <output data-testid="opened-source">{source}</output>
        <PaperRegionProvider
          key={mount}
          workspaceId={workspaceId}
          onRequestAgent={(request) =>
            setDispatches((current) =>
              current.some((item) => item.id === request.id)
                ? current
                : [...current, request],
            )
          }
          onOpenFile={setSource}
        >
          <PaperRegionWorkbench
            view={(params.get("view") ?? "reviews") as PaperRegionView}
            stage={params.get("stage") ?? undefined}
            paths={params.get("paths")?.split("|")}
            reviewerLabels={{ "review-a": "Reviewer A" }}
          >
            <p>Published fallback</p>
          </PaperRegionWorkbench>
        </PaperRegionProvider>
      </main>
    </I18nextProvider>
  )
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <Fixture />
  </QueryClientProvider>,
)
