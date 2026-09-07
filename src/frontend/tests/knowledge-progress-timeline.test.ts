import { expect, test } from "bun:test"
import i18n from "i18next"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { initReactI18next } from "react-i18next"

import KnowledgeProgressTimeline from "../src/components/Knowledge/KnowledgeProgressTimeline"
import type { KnowledgeParseRun } from "../src/components/Knowledge/types"

await i18n.use(initReactI18next).init({
  lng: "en",
  resources: { en: { translation: {} } },
})

function run(
  status: KnowledgeParseRun["status"],
  stage: string,
): KnowledgeParseRun {
  return {
    id: "run-1",
    source_id: "source-1",
    source_revision: 1,
    status,
    progress: 42,
    stage,
    message: status === "cancelled" ? "Parsing stopped" : "Parsing",
    parser_name: "claude",
    parser_provider_name: "provider",
    parser_model: "model",
    parse_mode: "direct",
    plan_id: null,
    plan_strategy_id: null,
    parent_run_id: null,
    session_owner_kind: "run",
    session_owner_id: "run-1",
    can_refine: false,
    generated_memory_ids: [],
    inserted_document_ids: [],
    skill_name: "knowledge-parser",
    skill_version: "1",
    error_code: null,
    error: null,
    created_time: "2026-08-18T00:00:00Z",
    updated_time: "2026-08-18T00:00:01Z",
  }
}

test("a cancelled parse run never renders an active loading spinner", () => {
  const markup = renderToStaticMarkup(
    createElement(KnowledgeProgressTimeline, {
      run: run("cancelled", "cancelled"),
      events: [],
    }),
  )

  expect(markup).not.toContain("animate-spin")
})

test("a running parse run still renders its active loading spinner", () => {
  const markup = renderToStaticMarkup(
    createElement(KnowledgeProgressTimeline, {
      run: run("running", "analyzing"),
      events: [],
    }),
  )

  expect(markup).toContain("animate-spin")
})
