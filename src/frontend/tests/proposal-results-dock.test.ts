import { expect, test } from "bun:test"
import { createInstance } from "i18next"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { I18nextProvider } from "react-i18next"

import ProposalResultsDock from "../src/components/Paper/ProposalResultsDock"

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
            pending: "No result yet",
            files: "Stage files",
            editor: "Editor / PDF",
          },
        },
      },
    },
  },
})

const stage = {
  key: "formatting",
  title: "Project foundation",
  hint: "Define the project scope.",
  status: "Ready",
  summary: "The project scope is established.",
  findings: [],
  files: ["proposal.typ"],
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
