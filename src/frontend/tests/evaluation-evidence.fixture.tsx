import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { FormProvider, useForm, useWatch } from "react-hook-form"
import { OpenAPI } from "../src/client"
import EvaluatorSection from "../src/components/Evolution/TaskDetail/config-form/EvaluatorSection"
import type { JsonSchema } from "../src/components/Evolution/TaskDetail/schema-form/resolveSchema"
import SchemaField from "../src/components/Evolution/TaskDetail/schema-form/SchemaField"
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

const initial = {
  type: "remote",
  timeout: 60,
  batch_size: 10,
  parallel: true,
  candidate_paths: ["shader.comp"],
}
const evaluatorSchema: JsonSchema = {
  discriminator: { propertyName: "type" },
  oneOf: [
    {
      type: "object",
      properties: {
        type: { const: "remote" },
        gateway_id: { type: "string", title: "Gateway ID" },
        contract: {
          type: "object",
          title: "Contract",
          additionalProperties: true,
        },
        selection_mode: { type: "string", title: "Device pool" },
        max_attempts: { type: "integer", title: "Attempts" },
        lease_timeout: { type: "number", title: "Lease timeout" },
        timeout: {
          type: "number",
          title: "Timeout (seconds)",
          exclusiveMinimum: 0,
        },
        batch_size: { type: "integer", title: "Batch size", minimum: 1 },
        parallel: { type: "boolean", title: "Parallel evaluation" },
        candidate_paths: {
          type: "array",
          title: "Candidate file patterns",
          items: { type: "string" },
        },
        parameters: {
          type: "object",
          title: "Evaluator parameters",
          additionalProperties: true,
        },
      },
    },
  ],
}

function Fixture() {
  const fallback = new URLSearchParams(location.search).has("fallback")
  const [value, setValue] = useState<unknown>(initial)
  const form = useForm({ defaultValues: { evaluator: initial } })
  const formValue = useWatch({ control: form.control, name: "evaluator" })
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
            {fallback ? (
              <FormProvider {...form}>
                <EvaluatorSection />
              </FormProvider>
            ) : (
              <SchemaField
                name="evaluator"
                schema={evaluatorSchema}
                root={evaluatorSchema}
                value={value}
                onChange={setValue}
              />
            )}
            <output data-evaluator-state className="break-all text-sm">
              {JSON.stringify(fallback ? formValue : value)}
            </output>
          </div>
          <NodeEvaluationData taskId="task" nodeId="node" active={false} />
        </main>
      </TooltipProvider>
    </QueryClientProvider>
  )
}

createRoot(document.getElementById("root")!).render(<Fixture />)
