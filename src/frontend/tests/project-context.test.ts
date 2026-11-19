import assert from "node:assert/strict"
import { test } from "node:test"

import { projectContextUploads } from "../src/lib/projectContext.ts"

function folderFile(name: string, relativePath: string) {
  const file = new File([`Original contents: ${name}`], name)
  Object.defineProperty(file, "webkitRelativePath", { value: relativePath })
  return file
}

test("project reference uploads stay isolated from source files", () => {
  const files = [
    new File(["rules"], "funder-rules.md"),
    new File(["notes"], "project-notes.txt"),
  ]
  const uploads = projectContextUploads(files)
  assert.deepEqual(
    uploads.map((item) => item.relativePath),
    ["project_context/funder-rules.md", "project_context/project-notes.txt"],
  )
})

test("ZIP, TAR, TAR.GZ and TGZ archives get individual extraction parents", () => {
  const files = [
    "references.ZIP",
    "rules.tar",
    "guidelines.TAR.GZ",
    "figures.tgz",
  ].map((name) => new File(["archive bytes"], name))
  assert.deepEqual(
    projectContextUploads(files).map((item) => item.relativePath),
    [
      "project_context/references/references.ZIP",
      "project_context/rules/rules.tar",
      "project_context/guidelines/guidelines.TAR.GZ",
      "project_context/figures/figures.tgz",
    ],
  )
})

test("mixed archives and Unicode references retain their original File objects and bytes", async () => {
  const files = [
    new File([new Uint8Array([0, 255, 80, 75])], "研究资料 📚.tar.gz", {
      type: "application/gzip",
    }),
    new File(["准确的作者说明"], "项目说明.md"),
    new File(["image bytes"], "结果.png"),
    new File(["zip bytes"], "附件.zip"),
  ]
  const uploads = projectContextUploads(files)
  assert.deepEqual(
    uploads.map((item) => item.relativePath),
    [
      "project_context/研究资料 📚/研究资料 📚.tar.gz",
      "project_context/项目说明.md",
      "project_context/结果.png",
      "project_context/附件/附件.zip",
    ],
  )
  for (let index = 0; index < files.length; index++) {
    assert.equal(uploads[index].file, files[index])
    assert.deepEqual(
      await uploads[index].file.arrayBuffer(),
      await files[index].arrayBuffer(),
    )
  }
})

test("folder references preserve their full relative paths and archive parents", () => {
  const uploads = projectContextUploads([
    folderFile("notes.md", "研究材料/笔记/notes.md"),
    folderFile("evidence.TAR.GZ", "研究材料/实验/evidence.TAR.GZ"),
    folderFile("dataset.zip", "研究材料/数据/dataset.zip"),
    folderFile("notes.md", "研究材料/其他/notes.md"),
  ])
  assert.deepEqual(
    uploads.map((item) => item.relativePath),
    [
      "project_context/研究材料/笔记/notes.md",
      "project_context/研究材料/实验/evidence/evidence.TAR.GZ",
      "project_context/研究材料/数据/dataset/dataset.zip",
      "project_context/研究材料/其他/notes.md",
    ],
  )
})

test("folder separators normalize while dotted names and non-archive suffixes stay intact", () => {
  const uploads = projectContextUploads([
    folderFile("notes.zip.md", "资料\\draft.v2\\notes.zip.md"),
    new File(["data"], "package.v2.TaR.gZ"),
    new File(["data"], "ordinary.gz"),
  ])
  assert.deepEqual(
    uploads.map((item) => item.relativePath),
    [
      "project_context/资料/draft.v2/notes.zip.md",
      "project_context/package.v2/package.v2.TaR.gZ",
      "project_context/ordinary.gz",
    ],
  )
})

test("duplicate upload paths remain in the batch for backend validation", () => {
  const files = [
    new File(["first archive"], "same.zip"),
    new File(["different archive"], "same.zip"),
  ]
  const uploads = projectContextUploads(files)
  assert.equal(uploads.length, 2)
  assert.equal(uploads[0].relativePath, uploads[1].relativePath)
  assert.notEqual(uploads[0].file, uploads[1].file)
})

test("reference paths reject absolute paths, traversal, empty segments and controls", () => {
  for (const path of [
    "../escape.zip",
    "/absolute/file.md",
    "C:\\absolute\\file.md",
    "folder/../escape.md",
    "folder//file.md",
    "folder/./file.md",
    "folder/\0file.md",
  ]) {
    assert.throws(
      () => projectContextUploads([folderFile("file.md", path)]),
      /safe relative paths/,
    )
  }
})

test("archives with an empty or traversal-like stem use a safe extraction folder", () => {
  assert.deepEqual(
    projectContextUploads([
      new File(["zip"], ".ZIP"),
      new File(["tar"], "..tar"),
    ]).map((item) => item.relativePath),
    ["project_context/archive/.ZIP", "project_context/archive/..tar"],
  )
})
