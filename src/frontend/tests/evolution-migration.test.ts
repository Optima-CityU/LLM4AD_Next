import { describe, expect, test } from "bun:test"

import {
  collectMigrationAliases,
  resolveMigrationParentIds,
} from "../src/hooks/evolutionMigration"

describe("evolution migration lineage", () => {
  test("collects migrated clone aliases from a structured event", () => {
    const aliases = collectMigrationAliases({
      type: "migration",
      generation: 5,
      migrants: [
        {
          id: "clone-a",
          source_id: "source-a",
          source_island_id: "0",
          target_island_id: "2",
        },
      ],
    })

    expect([...aliases.entries()]).toEqual([["clone-a", "source-a"]])
  })

  test("resolves migrated parents to visible source nodes and deduplicates them", () => {
    const aliases = new Map([
      ["clone-a", "source-a"],
      ["clone-b", "source-a"],
    ])

    expect(
      resolveMigrationParentIds(
        ["clone-a", "ordinary-parent", "clone-b"],
        aliases,
      ),
    ).toEqual(["source-a", "ordinary-parent"])
  })

  test("ignores malformed migration records", () => {
    const aliases = collectMigrationAliases({
      type: "migration",
      migrants: [{ id: "", source_id: "source-a" }, { id: "clone-b" }, null],
    })

    expect(aliases.size).toBe(0)
  })
})
