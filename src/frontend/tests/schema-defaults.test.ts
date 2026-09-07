import { expect, test } from "bun:test"

import type { JsonSchema } from "../src/components/Evolution/TaskDetail/schema-form/resolveSchema"
import * as schemaUtils from "../src/components/Evolution/TaskDetail/schema-form/resolveSchema"

test("fills newly added schema defaults without overwriting saved task values", () => {
  const root: JsonSchema = {
    type: "object",
    properties: {
      embedding: { $ref: "#/$defs/EmbeddingConfig" },
      evolution: {
        oneOf: [{ $ref: "#/$defs/DiverseIslandGAConfig" }],
        discriminator: { propertyName: "type" },
      },
    },
    $defs: {
      DiverseIslandGAConfig: {
        type: "object",
        properties: {
          type: {
            const: "diverse_island_ga",
            default: "diverse_island_ga",
          },
          adaptive_migration: { type: "boolean", default: true },
          migration_stagnation_threshold: { type: "integer", default: 2 },
          short_task_max_migrations: { type: "integer", default: 1 },
          novelty_survivor_ratio: { type: "number", default: 0.2 },
          exploration_restart_ratio: { type: "number", default: 0.3 },
          future_required_label: { type: "string" },
        },
      },
      EmbeddingConfig: {
        type: "object",
        properties: {
          type: {
            anyOf: [
              { type: "string", enum: ["openai", "jina"] },
              { type: "null" },
            ],
            default: null,
          },
          dim: { type: "integer", default: 3072 },
        },
      },
    },
  }
  const saved = {
    embedding: {
      type: "",
    },
    evolution: {
      type: "diverse_island_ga",
      adaptive_migration: false,
      short_task_max_migrations: 0,
    },
  }
  const mergeSchemaDefaults = (
    schemaUtils as typeof schemaUtils & {
      mergeSchemaDefaults?: (
        schema: JsonSchema,
        root: JsonSchema,
        current: unknown,
      ) => unknown
    }
  ).mergeSchemaDefaults

  expect(typeof mergeSchemaDefaults).toBe("function")
  if (!mergeSchemaDefaults) return
  expect(mergeSchemaDefaults(root, root, saved)).toEqual({
    embedding: {
      type: null,
      dim: 3072,
    },
    evolution: {
      type: "diverse_island_ga",
      adaptive_migration: false,
      migration_stagnation_threshold: 2,
      short_task_max_migrations: 0,
      novelty_survivor_ratio: 0.2,
      exploration_restart_ratio: 0.3,
    },
  })
})
