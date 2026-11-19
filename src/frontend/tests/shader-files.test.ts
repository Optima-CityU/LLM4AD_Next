import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

import {
  getCommentStyle,
  getLanguageFromPath,
} from "../src/components/Evolution/TaskDetail/steps/EvolveAnnotationStep/types.ts"

test("the Genshin example retains valid shader annotation markers", () => {
  const source = readFileSync(
    new URL(
      "../../../examples/applications/hgamey_shader/algorithm/shader.comp",
      import.meta.url,
    ),
    "utf8",
  )
  const markers = getCommentStyle("hgamey_shader/algorithm/shader.comp")
  for (const marker of markers) assert.ok(source.includes(marker))
  assert.equal(
    getLanguageFromPath("hgamey_shader/algorithm/shader.comp"),
    "cpp",
  )
})

test("shader source variants use the existing C-family editor support", () => {
  for (const path of [
    "effect.glsl",
    "MAIN.COMP",
    "src/vertex.vert",
    "src\\pixel.frag",
  ]) {
    assert.equal(getLanguageFromPath(path), "cpp")
    assert.deepEqual(getCommentStyle(path), [
      "// EVOLVE_START",
      "// EVOLVE_END",
    ])
  }
  assert.equal(getLanguageFromPath("shader.spv"), "plaintext")
  assert.deepEqual(getCommentStyle("solution.py"), [
    "# EVOLVE_START",
    "# EVOLVE_END",
  ])
})
