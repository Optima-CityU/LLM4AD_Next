import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { test } from "node:test"
import { createInstance } from "i18next"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import * as ts from "typescript"
import type {
  EvaluationRecord,
  EvaluationSummary,
} from "../src/client/types.gen.ts"
import * as evaluationData from "../src/lib/evaluationData.ts"
import * as evaluationMetrics from "../src/lib/evaluationMetrics.ts"

const require = createRequire(import.meta.url)
const element = React.createElement
const passthrough = ({ children }: { children?: React.ReactNode }) =>
  element("div", null, children)

/** Render real component branches with deterministic API responses and UI wrappers. */
async function renderRecord(record: EvaluationRecord, locale = "en") {
  const i18n = createInstance()
  await i18n.init({
    lng: locale,
    resources: {
      [locale]: {
        translation: JSON.parse(
          readFileSync(
            new URL(`../src/i18n/locales/${locale}.json`, import.meta.url),
            "utf8",
          ),
        ),
      },
    },
    interpolation: { escapeValue: false },
  })
  const summary: EvaluationSummary = {
    id: record.id,
    state: record.state,
    storage_state: record.storage_state,
    created_at: record.created_at,
  }
  const mocks: Record<string, unknown> = {
    "@tanstack/react-query": {
      useInfiniteQuery: () => ({ data: { pages: [{ data: [summary] }] } }),
      useQuery: () => ({
        data: { record, available_artifact_ids: ["diagnostic"] },
      }),
    },
    "react-i18next": {
      useTranslation: () => ({
        t: i18n.t.bind(i18n),
        i18n,
      }),
    },
    "@/client": { Llm4AdEvaluationsService: {} },
    "@/components/ui/badge": { Badge: passthrough },
    "@/components/ui/button": { Button: passthrough },
    "@/components/ui/select": Object.fromEntries(
      [
        "Select",
        "SelectContent",
        "SelectGroup",
        "SelectItem",
        "SelectTrigger",
        "SelectValue",
      ].map((name) => [name, passthrough]),
    ),
    "@/components/ui/separator": { Separator: () => element("hr") },
    "@/components/ui/skeleton": {
      Skeleton: () => element("span", null, "loading"),
    },
    "@/components/ui/tooltip": {
      Tooltip: passthrough,
      TooltipTrigger: passthrough,
      TooltipContent: passthrough,
    },
    "@/lib/evaluationArtifacts": { evaluationNeedsPolling: () => false },
    "@/lib/evaluationData": evaluationData,
    "@/lib/evaluationMetrics": evaluationMetrics,
    "@/lib/utils": {
      formatDateTime: (date: string) => date,
      cn: (...parts: unknown[]) => parts.filter(Boolean).join(" "),
    },
    "./NodeArtifacts": {
      default: ({
        files,
        availableIds,
      }: {
        files: { name: string }[]
        availableIds: string[]
      }) =>
        element(
          "div",
          { "data-available": availableIds.join(",") },
          files.map((file) => file.name).join(","),
        ),
    },
  }
  function loadComponent(path: string) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8")
    const js = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }).outputText
    const module = { exports: {} as { default: React.ComponentType<any> } }
    new Function("require", "exports", "module", js)(
      (id: string) => (id in mocks ? mocks[id] : require(id)),
      module.exports,
      module,
    )
    return module.exports
  }
  mocks["./NodeMetrics"] = loadComponent(
    "../src/components/Evolution/NodeMetrics.tsx",
  )
  const Component = loadComponent(
    "../src/components/Evolution/NodeEvaluationData.tsx",
  ).default
  return renderToStaticMarkup(
    element(Component, {
      taskId: "task",
      nodeId: "failed-node",
      active: false,
      evaluation: { score: 123456, metrics: { aggregate: 123456 } },
    }),
  )
}

const base: EvaluationRecord = {
  id: "record",
  state: "completed",
  storage_state: "saved",
  created_at: "2026-11-13",
  result: {
    score: -1.2345678912345,
    success: true,
    metrics: { latency_ms: 1 },
  },
}

test("selected record shows its own compact score and exact precision rather than aggregate fitness", async () => {
  const html = await renderRecord(base)
  assert.match(html, /This evaluation/)
  assert.match(html, /-1\.235/)
  assert.match(html, /-1\.2345678912345/)
  assert.doesNotMatch(html, /123456/)
})

test("failed records retain feedback, filenames and storage errors without a valid-score badge", async () => {
  const html = await renderRecord({
    ...base,
    state: "failed",
    storage_state: "error",
    result: {
      score: 0,
      success: false,
      error_message: "compile failed",
      evolution_feedback: { stage: "compile" },
      metadata: { device: "test" },
    },
    storage_errors: ["report.png: file not found"],
    artifacts: [
      {
        id: "diagnostic",
        name: "failure.txt",
        size: 123,
        sha256: "a".repeat(64),
      },
    ],
  })
  assert.match(html, /compile failed/)
  assert.match(html, /evolution_feedback/)
  assert.match(html, /metadata/)
  assert.match(html, /report\.png: file not found/)
  assert.match(html, /failure\.txt/)
  assert.match(html, /data-available="diagnostic"/)
  assert.doesNotMatch(html, /This evaluation/)
})

test("long storage diagnostics are collapsible while retaining the complete reason", async () => {
  const reason = `nested/report.png: ${"directory unavailable ".repeat(30)}`
  const html = await renderRecord({
    ...base,
    storage_state: "error",
    storage_errors: [reason],
  })
  assert.match(html, /<details[^>]*data-node-storage-errors/)
  assert.match(html, /nested\/report\.png:/)
  assert.ok(html.includes(reason))
})

test("score labels use the active locale and render zero only for successful records", async () => {
  const zh = await renderRecord(base, "zh")
  assert.match(zh, /本次评估/)
  assert.doesNotMatch(zh, /This evaluation/)
  const zero = await renderRecord({
    ...base,
    result: { score: 0, success: true },
  })
  assert.match(zero, /This evaluation/)
  assert.match(zero, /data-node-metric="score"/)
  const missing = await renderRecord({
    ...base,
    result: { success: true, metadata: {} },
  })
  assert.doesNotMatch(missing, /This evaluation/)
})

test("storage diagnostics preserve unavailable state and escape untrusted filenames", async () => {
  const html = await renderRecord({
    ...base,
    storage_state: "offline",
    storage_errors: ["report<script>.png: <img src=x onerror=alert(1)>"],
  })
  assert.match(html, /storage is temporarily unavailable/)
  assert.match(html, /report&lt;script&gt;\.png:/)
  assert.doesNotMatch(html, /<script>|<img src=x/)
})
