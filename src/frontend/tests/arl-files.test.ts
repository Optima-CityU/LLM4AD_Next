import assert from "node:assert/strict"
import { test } from "node:test"

import {
  type ARLFsDirectory,
  ARLFsRequests,
  flattenFsTree,
  joinFsPath,
  normalizeDirectory,
  normalizeFsPath,
  parentFsPath,
  pruneFsSubtree,
  validateFsName,
} from "../src/components/AutoResearch/arlFiles.ts"

const dir = (
  path: string,
  entries: [string, "file" | "directory", number?, string?][],
): ARLFsDirectory =>
  normalizeDirectory({
    path,
    entries: entries.map(([name, kind, size = 0, modifiedAt = ""]) => ({
      name,
      kind,
      size,
      modifiedAt,
      path: joinFsPath(path, name),
    })),
  })

test("path helpers normalize root and nested paths", () => {
  assert.equal(normalizeFsPath(""), ".")
  assert.equal(normalizeFsPath("./"), ".")
  assert.equal(normalizeFsPath("./src/"), "src")
  assert.equal(normalizeFsPath("a\\b"), "a/b")
  assert.equal(joinFsPath(".", "a.py"), "a.py")
  assert.equal(joinFsPath("src", "a.py"), "src/a.py")
  assert.equal(parentFsPath("a.py"), ".")
  assert.equal(parentFsPath("src/lib/a.py"), "src/lib")
})

test("validateFsName accepts one segment only", () => {
  assert.equal(validateFsName("  "), "empty")
  assert.equal(validateFsName(".."), "invalid")
  assert.equal(validateFsName("a/b"), "invalid")
  assert.equal(validateFsName("a\\b"), "invalid")
  assert.equal(validateFsName(".env"), null)
  assert.equal(validateFsName("notes.md"), null)
})

test("normalizeDirectory drops junk and rebuilds missing paths", () => {
  const out = normalizeDirectory({
    path: "./src",
    entries: [
      { name: "a.py", kind: "file", size: 3 },
      { name: "" },
      null,
      { name: "lib", kind: "directory" },
    ],
  })
  assert.equal(out.path, "src")
  assert.deepEqual(
    out.entries.map((e) => [e.path, e.kind]),
    [
      ["src/a.py", "file"],
      ["src/lib", "directory"],
    ],
  )
  assert.deepEqual(normalizeDirectory(undefined), { path: ".", entries: [] })
})

test("flattenFsTree nests expanded dirs, sorts, and hides dotfiles", () => {
  const directories = new Map([
    [
      ".",
      dir(".", [
        ["b.txt", "file", 10, "2026-01-02"],
        ["src", "directory"],
        [".git", "directory"],
        ["a.txt", "file", 99, "2026-01-01"],
      ]),
    ],
    ["src", dir("src", [["main.py", "file"]])],
  ])
  const rows = (
    sort: "name" | "modified" | "size",
    expanded: string[],
    showHidden = false,
  ) =>
    flattenFsTree({
      directories,
      expanded: new Set(expanded),
      sort,
      showHidden,
    }).map((r) => `${"  ".repeat(r.depth)}${r.entry.name}`)

  assert.deepEqual(rows("name", []), ["src", "a.txt", "b.txt"])
  assert.deepEqual(rows("name", ["src"]), [
    "src",
    "  main.py",
    "a.txt",
    "b.txt",
  ])
  assert.deepEqual(rows("modified", []), ["src", "b.txt", "a.txt"])
  assert.deepEqual(rows("size", []), ["src", "a.txt", "b.txt"])
  assert.deepEqual(rows("name", [], true), [".git", "src", "a.txt", "b.txt"])
  // Expanded but not loaded yet: no children, no crash.
  assert.deepEqual(rows("name", [".git"], true), [
    ".git",
    "src",
    "a.txt",
    "b.txt",
  ])
})

test("pruneFsSubtree forgets a renamed or deleted folder", () => {
  const directories = new Map([
    [".", dir(".", [["src", "directory"]])],
    ["src", dir("src", [["lib", "directory"]])],
    ["src/lib", dir("src/lib", [])],
    ["srcx", dir("srcx", [])],
  ])
  const out = pruneFsSubtree(
    directories,
    new Set(["src", "src/lib", "srcx"]),
    "src",
  )
  assert.deepEqual([...out.directories.keys()], [".", "srcx"])
  assert.deepEqual([...out.expanded], ["srcx"])
})

test("ARLFsRequests pairs results by request_id and settles once", async () => {
  const requests = new ARLFsRequests()
  const a = requests.begin()
  const b = requests.begin()
  assert.notEqual(a.id, b.id)

  assert.equal(
    requests.settle({ request_id: b.id, ok: false, message: "nope" }),
    true,
  )
  assert.equal(requests.settle({ request_id: a.id, ok: true, path: "x" }), true)
  assert.equal((await a.promise).path, "x")
  await assert.rejects(b.promise, /nope/)
  // Late duplicate reply is ignored.
  assert.equal(requests.settle({ request_id: a.id, ok: true }), false)
  assert.equal(requests.size, 0)
})

test("ARLFsRequests rejects on timeout and on socket drop", async () => {
  const timers: (() => void)[] = []
  const requests = new ARLFsRequests({
    setTimer: (fn) => timers.push(fn),
    clearTimer: () => {},
  })
  const timed = requests.begin()
  const dropped = requests.begin()

  timers[0]()
  await assert.rejects(timed.promise, /timed out/)
  requests.failAll("disconnected")
  await assert.rejects(dropped.promise, /disconnected/)
  assert.equal(requests.size, 0)
})
