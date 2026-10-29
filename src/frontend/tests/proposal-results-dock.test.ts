import { expect, test } from "bun:test"
import { createInstance } from "i18next"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { I18nextProvider } from "react-i18next"
import type { ProposalResultStage } from "../src/components/Paper/ProposalResultsDock"
import ProposalResultsDock, {
  getStagePageWindow,
} from "../src/components/Paper/ProposalResultsDock"

const i18n = createInstance()
await i18n.init({
  lng: "en",
  resources: {
    en: {
      translation: {
        paper: {
          source: {
            projectContext: { upload: "Upload references" },
          },
          proposalResults: {
            title: "Current stage result",
            stageIndex: "Stage {{current}} / {{total}}",
            summary: "Stage summary",
            foundation: "Project foundation",
            foundationHint: "Current shared foundation",
            sourceRefs: "Sources",
            pageRange: "{{from}}–{{to}} of {{total}}",
            previousPage: "Previous page",
            nextPage: "Next page",
            paginationLabel: "{{section}} pages",
            pendingTitle: "Awaiting stage result",
            pending: "No result yet",
            files: "Stage files",
            openFile: "Open {{path}} in the editor",
            findingTitles: {
              formatting: "Stage decisions",
              literature: "Evidence and literature threads",
              rationale: "Research case",
              objectives: "Objectives and key questions",
              methods: "Methodological route",
              innovation_plan: "Innovation and implementation",
              foundation_feasibility: "Foundation and feasibility",
              final_review: "Final review observations",
            },
            editor: "Editor / PDF",
          },
        },
      },
    },
  },
})

const stage: ProposalResultStage = {
  key: "formatting",
  title: "Project foundation",
  hint: "Define the project scope.",
  status: "Ready",
  summary: "The project scope is established.",
  findings: [],
  files: ["proposal.typ"],
  foundationBlocks: [
    {
      key: "scope",
      title: "Research scope",
      content: "Focus on **robust optimization**.",
      source_refs: ["author notes"],
    },
  ],
}

test("stage results are the default view and the editor is an alternative tab", () => {
  const markup = renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n },
      createElement(
        ProposalResultsDock,
        {
          stage,
          editorOpen: false,
          onEditorOpenChange: () => undefined,
          onOpenFile: () => undefined,
          onUploadProjectContext: () => undefined,
        },
        createElement("div", { "data-testid": "pdf-content" }, "PDF"),
      ),
    ),
  )

  expect(markup).toContain("The project scope is established.")
  expect(markup).toContain("Research scope")
  expect(markup).toContain("<strong>robust optimization</strong>")
  expect(markup).toContain("author notes")
  expect(markup).toContain("Stage 01 / 08")
  expect(markup).toContain("proposal.typ")
  expect(markup).toContain("Editor / PDF")
  expect(markup).not.toContain('data-testid="pdf-content"')

  const expandedMarkup = renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n },
      createElement(
        ProposalResultsDock,
        {
          stage,
          editorOpen: true,
          onEditorOpenChange: () => undefined,
          onOpenFile: () => undefined,
          onUploadProjectContext: () => undefined,
        },
        createElement("div", { "data-testid": "pdf-content" }, "PDF"),
      ),
    ),
  )
  expect(expandedMarkup).toContain('data-testid="pdf-content"')
  expect(expandedMarkup).toContain("Upload references")
  expect(expandedMarkup).not.toContain("The project scope is established.")
})

test("method findings use a stage-specific route layout and preserve Markdown", () => {
  const markup = renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n },
      createElement(
        ProposalResultsDock,
        {
          stage: {
            ...stage,
            key: "methods",
            title: "Research methods",
            summary: null,
            findings: [
              "Validate with **three** benchmarks",
              "Compare against baseline",
            ],
            files: ["sections/05-methods.typ"],
          },
          editorOpen: false,
          onEditorOpenChange: () => undefined,
          onOpenFile: () => undefined,
          onUploadProjectContext: () => undefined,
        },
        null,
      ),
    ),
  )

  expect(markup).toContain('data-stage="methods"')
  expect(markup).toContain("Methodological route")
  expect(markup).toContain("<strong>three</strong>")
  expect(markup).toContain("Stage 05 / 08")
  expect(markup).not.toContain("Research scope")
})

test("an unpublished stage shows a clear empty state", () => {
  const markup = renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n },
      createElement(
        ProposalResultsDock,
        {
          stage: {
            ...stage,
            key: "literature",
            summary: null,
            findings: [],
            files: [],
            foundationBlocks: [],
          },
          editorOpen: false,
          onEditorOpenChange: () => undefined,
          onOpenFile: () => undefined,
          onUploadProjectContext: () => undefined,
        },
        null,
      ),
    ),
  )

  expect(markup).toContain("Awaiting stage result")
  expect(markup).not.toContain("Research scope")
})

test("large stage lists render only their first page", () => {
  const markup = renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n },
      createElement(
        ProposalResultsDock,
        {
          stage: {
            ...stage,
            summary: null,
            foundationBlocks: Array.from({ length: 11 }, (_, index) => ({
              key: `block-${index}`,
              title: `Foundation ${String(index + 1).padStart(2, "0")}`,
              content: `Foundation content ${index + 1}`,
            })),
            findings: Array.from(
              { length: 25 },
              (_, index) => `Finding ${String(index + 1).padStart(2, "0")}`,
            ),
          },
          editorOpen: false,
          onEditorOpenChange: () => undefined,
          onOpenFile: () => undefined,
          onUploadProjectContext: () => undefined,
        },
        null,
      ),
    ),
  )

  expect(markup).toContain("Foundation 08")
  expect(markup).not.toContain("Foundation 09")
  expect(markup).toContain("Finding 20")
  expect(markup).not.toContain("Finding 21")
  expect(markup).toContain("1–8 of 11")
  expect(markup).toContain("1–20 of 25")
  expect(markup).toContain("Next page")
})

test("stage pagination reaches later entries and clamps after results shrink", () => {
  expect(getStagePageWindow(25, 1, 20)).toEqual({
    page: 1,
    pageCount: 2,
    start: 20,
    end: 25,
  })
  expect(getStagePageWindow(3, 9, 20)).toEqual({
    page: 0,
    pageCount: 1,
    start: 0,
    end: 3,
  })
})
