import { expect, test } from "bun:test"

import {
  hasProjectContextFiles,
  projectContextUploads,
} from "../src/lib/projectContext"

test("project reference uploads are isolated from proposal source files", () => {
  const files = [
    new File(["rules"], "funder-rules.md"),
    new File(["notes"], "project-notes.txt"),
  ]
  const uploads = projectContextUploads(files)

  expect(uploads.map(({ relativePath }) => relativePath)).toEqual([
    "project_context/funder-rules.md",
    "project_context/project-notes.txt",
  ])
  expect(hasProjectContextFiles(["proposal.typ"])).toBe(false)
  expect(
    hasProjectContextFiles(["proposal.typ", uploads[0].relativePath]),
  ).toBe(true)
})
