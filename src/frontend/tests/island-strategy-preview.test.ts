import { describe, expect, test } from "bun:test"

import {
  buildIslandStrategyPreview,
  getDiverseIslandPreviewConfig,
  getIslandPreviewRole,
  isMemoryEnabled,
} from "../src/components/Evolution/TaskDetail/schema-form/islandStrategyPreview"
import * as schemaUtils from "../src/components/Evolution/TaskDetail/schema-form/resolveSchema"

describe("diverse island strategy preview", () => {
  test("shows the three memory anchors for three islands", () => {
    const profiles = buildIslandStrategyPreview({
      numIslands: 3,
      strategyStrength: 1,
      restartRatio: 0.3,
    })

    expect(profiles).toHaveLength(3)
    expect(profiles[0]).toMatchObject({
      position: 0,
      memoryPolicy: "success_only",
      successMemoryRatio: 1,
      errorMemoryRatio: 0,
      restartProbability: 0,
    })
    expect(profiles[1]).toMatchObject({
      position: 0.5,
      memoryPolicy: "corrective",
      successMemoryRatio: 0.6,
      errorMemoryRatio: 0.4,
      restartProbability: 0,
    })
    expect(profiles[2]).toMatchObject({
      position: 1,
      memoryPolicy: "none",
      successMemoryRatio: 0,
      errorMemoryRatio: 0,
      restartProbability: 0.3,
    })
  })

  test("interpolates any island count across one continuous spectrum", () => {
    const profiles = buildIslandStrategyPreview({
      numIslands: 5,
      strategyStrength: 1,
      restartRatio: 0.4,
    })

    expect(profiles.map((profile) => profile.position)).toEqual([
      0, 0.25, 0.5, 0.75, 1,
    ])
    expect(profiles.map((profile) => profile.restartProbability)).toEqual([
      0, 0, 0, 0.2, 0.4,
    ])
  })

  test("collapses roles to the corrective midpoint when strength is zero", () => {
    const profiles = buildIslandStrategyPreview({
      numIslands: 4,
      strategyStrength: 0,
      restartRatio: 1,
    })

    expect(profiles.every((profile) => profile.position === 0.5)).toBe(true)
    expect(
      profiles.every((profile) => profile.memoryPolicy === "corrective"),
    ).toBe(true)
    expect(profiles.every((profile) => profile.restartProbability === 0)).toBe(
      true,
    )
  })

  test("reads values from the selected configuration with safe defaults", () => {
    expect(
      getDiverseIslandPreviewConfig({
        type: "diverse_island_ga",
        num_islands: 7,
        island_strategy_strength: 0.65,
        exploration_restart_ratio: 0.45,
      }),
    ).toEqual({
      numIslands: 7,
      strategyStrength: 0.65,
      restartRatio: 0.45,
    })

    expect(getDiverseIslandPreviewConfig({})).toEqual({
      numIslands: 3,
      strategyStrength: 1,
      restartRatio: 0.3,
    })
  })

  test("treats only an explicit disabled memory configuration as off", () => {
    expect(isMemoryEnabled(undefined)).toBe(true)
    expect(isMemoryEnabled({ enabled: true, type: "local_yaml" })).toBe(true)
    expect(isMemoryEnabled({ enabled: false, type: "local_yaml" })).toBe(false)
  })

  test("does not describe memory-disabled islands as experience-driven", () => {
    const profiles = buildIslandStrategyPreview({
      numIslands: 3,
      strategyStrength: 1,
      restartRatio: 0.3,
    })

    expect(getIslandPreviewRole(profiles[0], false)).toBe("exploitation")
    expect(getIslandPreviewRole(profiles[1], false)).toBe("balanced")
    expect(getIslandPreviewRole(profiles[2], false)).toBe("independent")
    expect(getIslandPreviewRole(profiles[0], true)).toBe("reuse")
  })
})

test("reads slider bounds from schema UI metadata", () => {
  const getUiSlider = (
    schemaUtils as typeof schemaUtils & {
      getUiSlider?: (schema: {
        ui?: Record<string, unknown>
      }) => { min: number; max: number; step: number } | undefined
    }
  ).getUiSlider

  expect(typeof getUiSlider).toBe("function")
  if (!getUiSlider) return
  expect(
    getUiSlider({
      ui: {
        widget: "slider",
        slider: { min: 0, max: 1, step: 0.05 },
      },
    }),
  ).toEqual({ min: 0, max: 1, step: 0.05 })
  expect(
    getUiSlider({
      ui: { widget: "input", slider: { min: 0, max: 1, step: 1 } },
    }),
  ).toBeUndefined()
})

test("keeps existing out-of-range values visible instead of silently clamping them", () => {
  const fitSliderToValue = (
    schemaUtils as typeof schemaUtils & {
      fitSliderToValue?: (
        slider: { min: number; max: number; step: number },
        value: number,
      ) => { min: number; max: number; step: number }
    }
  ).fitSliderToValue

  expect(typeof fitSliderToValue).toBe("function")
  if (!fitSliderToValue) return
  expect(fitSliderToValue({ min: 1, max: 50, step: 1 }, 80)).toEqual({
    min: 1,
    max: 80,
    step: 1,
  })
  expect(fitSliderToValue({ min: 1, max: 50, step: 1 }, 0)).toEqual({
    min: 0,
    max: 50,
    step: 1,
  })
})

test("the diverse island form renders a live per-island behavior preview", async () => {
  const discriminatedFieldSource = await Bun.file(
    new URL(
      "../src/components/Evolution/TaskDetail/schema-form/SchemaDiscriminatedField.tsx",
      import.meta.url,
    ),
  ).text()
  const previewSource = await Bun.file(
    new URL(
      "../src/components/Evolution/TaskDetail/schema-form/DiverseIslandStrategyPreview.tsx",
      import.meta.url,
    ),
  ).text()

  expect(discriminatedFieldSource).toContain(
    'selectedOption.value === "diverse_island_ga"',
  )
  expect(discriminatedFieldSource).toContain("<DiverseIslandStrategyPreview")
  expect(discriminatedFieldSource).toContain("ISLAND_BEHAVIOR_FIELDS")
  expect(discriminatedFieldSource).toContain('renderField(entry, "inline")')
  expect(discriminatedFieldSource).toContain(
    'data-testid="island-behavior-settings"',
  )
  expect(discriminatedFieldSource).not.toContain(
    "lg:grid-cols-[minmax(0,1fr)_minmax(340px,0.9fr)]",
  )
  expect(previewSource).toContain('data-testid="island-strategy-preview"')
  expect(previewSource).toContain('data-testid="island-profile-chip"')
  expect(previewSource).toContain('data-testid="island-generation-source"')
  expect(previewSource).toContain('data-testid="island-memory-injection"')
  expect(previewSource).toContain('memoryEnabled ? "" : "opacity-50 grayscale"')
  expect(previewSource).toContain("buildIslandStrategyPreview")
  expect(previewSource).toContain(
    'numberValue(value, "migration_interval", 3)',
  )
  expect(previewSource).toContain("parentAndRestart")
  expect(previewSource).toContain("TooltipContent")
  expect(previewSource).toContain("memoryDisabled")
})

test("the evolution step passes the task memory state into the island preview", async () => {
  const viewSource = await Bun.file(
    new URL(
      "../src/components/Evolution/TaskDetail/UninitializedView.tsx",
      import.meta.url,
    ),
  ).text()
  const schemaStepSource = await Bun.file(
    new URL(
      "../src/components/Evolution/TaskDetail/steps/SchemaStepForm.tsx",
      import.meta.url,
    ),
  ).text()

  expect(viewSource).toContain("memoryEnabled={isMemoryEnabled(")
  expect(schemaStepSource).toContain("memoryEnabled={memoryEnabled}")
})
