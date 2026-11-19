import assert from "node:assert/strict"
import { test } from "node:test"
import type { EvaluationFile } from "../src/client/types.gen.ts"
import { resolveEvidenceComparisons } from "../src/lib/evidenceComparisons.ts"

const file = (
  id: string,
  name = `images/${id}.png`,
  media_type = "image/png",
): EvaluationFile => ({
  id,
  name,
  media_type,
  size: 100,
  sha256: "a".repeat(64),
})
const files = [
  file("ref"),
  file("base"),
  file("candidate"),
  file("base-2"),
  file("candidate-2"),
]
const image = (role: string, index: number) => ({
  role,
  artifact: files[index].name,
})
const staticGroup = {
  id: "static",
  label: "Static input 1",
  kind: "static",
  images: [image("candidate", 2), image("reference", 0), image("baseline", 1)],
}
const gameGroup = {
  id: "round-1",
  label: "Round 1",
  kind: "game",
  images: [image("candidate", 2), image("baseline", 1)],
}

test("static comparisons resolve exact logical names and always order reference, baseline and candidate", () => {
  const [group] = resolveEvidenceComparisons([staticGroup], files)
  assert.equal(group.id, "static")
  assert.equal(group.kind, "static")
  assert.deepEqual(
    group.images.map(({ role, file }) => [role, file.id]),
    [
      ["reference", "ref"],
      ["baseline", "base"],
      ["candidate", "candidate"],
    ],
  )
  assert.deepEqual(
    resolveEvidenceComparisons(
      [
        {
          ...staticGroup,
          images: [
            { role: "reference", artifact: "ref" },
            image("baseline", 1),
            image("candidate", 2),
          ],
        },
      ],
      files,
    ),
    [],
  )
})

test("game rounds retain manifest order and share artifacts without inventing synchronized frames", () => {
  const next = {
    ...gameGroup,
    id: "round-2",
    label: "Round 2",
    images: [image("baseline", 3), image("candidate", 4)],
  }
  const groups = resolveEvidenceComparisons([gameGroup, next], files)
  assert.deepEqual(
    groups.map((group) => group.id),
    ["round-1", "round-2"],
  )
  assert.deepEqual(
    groups[0].images.map((entry) => entry.role),
    ["baseline", "candidate"],
  )
})

test("invalid or missing manifests and incomplete groups leave images available for gallery fallback", () => {
  for (const manifest of [null, undefined, {}, "bad", [null, 2, []]])
    assert.deepEqual(resolveEvidenceComparisons(manifest, files), [])
  const invalid = [
    { ...staticGroup, id: "" },
    { ...staticGroup, label: " " },
    { ...staticGroup, kind: "unknown" },
    { ...staticGroup, images: [] },
    {
      ...staticGroup,
      images: [null, image("baseline", 1), image("candidate", 2)],
    },
    { ...staticGroup, images: [image("reference", 0), image("baseline", 1)] },
    { ...gameGroup, images: [image("baseline", 1), image("baseline", 2)] },
    { ...gameGroup, images: [image("baseline", 1), image("candidate", 1)] },
    {
      ...gameGroup,
      images: [
        image("baseline", 1),
        { role: "candidate", artifact: "missing.png" },
      ],
    },
  ]
  for (const group of invalid)
    assert.deepEqual(resolveEvidenceComparisons([group], files), [])
  assert.equal(
    resolveEvidenceComparisons([...invalid, staticGroup], files).length,
    1,
  )
})

test("duplicate group IDs, ambiguous artifact names and unsafe image formats cannot create duplicate previews", () => {
  assert.equal(
    resolveEvidenceComparisons([staticGroup, staticGroup], files).length,
    1,
  )
  assert.equal(
    resolveEvidenceComparisons([staticGroup], [...files, files[0]]).length,
    1,
  )
  assert.deepEqual(
    resolveEvidenceComparisons(
      [staticGroup],
      [...files, file("different-id", files[0].name)],
    ),
    [],
  )
  const svg = file("ref", files[0].name, "image/svg+xml")
  assert.deepEqual(
    resolveEvidenceComparisons([staticGroup], [svg, ...files.slice(1)]),
    [],
  )
})
