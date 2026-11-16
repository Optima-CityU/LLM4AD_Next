import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { OpenAPI } from "../src/client"
import GatewayPoolPicker from "../src/components/EvaluationGateway/GatewayPoolPicker"
import NodeEvaluationData from "../src/components/Evolution/NodeEvaluationData"
import { TooltipProvider } from "../src/components/ui/tooltip"
import i18n from "../src/i18n"
import "../src/index.css"
import { initOpenApi } from "../src/utils/request"

initOpenApi()
OpenAPI.BASE = window.location.origin
OpenAPI.TOKEN = "evidence-fixture-token"
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})

function Fixture() {
  const [value, setValue] = useState<Record<string, unknown>>({
    gateway_ids: [],
  })
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <main className="mx-auto grid max-w-6xl items-start gap-8 p-4 md:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-4">
            <h1 className="text-lg font-semibold">Remote evaluation fixture</h1>
            <button
              type="button"
              onClick={() =>
                void i18n.changeLanguage(i18n.language === "en" ? "zh" : "en")
              }
            >
              Switch language
            </button>
            <GatewayPoolPicker value={value} onChange={setValue} />
            <output data-pool-state className="break-all text-sm">
              {JSON.stringify(value)}
            </output>
          </div>
          <NodeEvaluationData taskId="task" nodeId="node" active={false} />
        </main>
      </TooltipProvider>
    </QueryClientProvider>
  )
}

createRoot(document.getElementById("root")!).render(<Fixture />)
