import { describe, expect, test } from "bun:test"

import {
  autoDesignFileRevision,
  autoDesignProjectFiles,
  autoDesignProjectTree,
} from "../src/lib/autoDesignPresentation"

test("file updates remain detectable without creating design records", () => {
  const file = {
    path: "project/solver.py",
    size: 40,
    modified_time: "2026-10-30T08:00:00Z",
  }
  expect(autoDesignFileRevision([file])).not.toBe(
    autoDesignFileRevision([
      { ...file, modified_time: "2026-10-30T08:01:00Z" },
    ]),
  )
})

describe("AutoDesign project files", () => {
  const files = [
    { path: "first/README.md", size: 10 },
    { path: "first/solver.py", size: 10 },
    { path: "second/README.md", size: 10 },
    { path: "second/docs/README.md", size: 10 },
  ]

  test("scopes files to the actual selected project", () => {
    expect(
      autoDesignProjectFiles(files, "/workspace/source/draft/first").map(
        (file) => file.path,
      ),
    ).toEqual(["first/README.md", "first/solver.py"])
  })

  test("shows a single project while preserving real API paths", () => {
    const tree = autoDesignProjectTree(files, "/workspace/source/draft/first")
    expect(tree.map((node) => node.name)).toEqual(["README.md", "solver.py"])
    expect(tree.map((node) => node.path)).toEqual([
      "first/README.md",
      "first/solver.py",
    ])
  })
})
