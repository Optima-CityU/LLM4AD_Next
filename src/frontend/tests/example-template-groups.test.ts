import { describe, expect, test } from "bun:test"

import {
  getTemplateCaseKey,
  getTemplateGroupKey,
  humanizeConfigName,
  splitExampleTemplates,
} from "../src/components/Evolution/exampleTemplateGroups"

describe("example template grouping", () => {
  test("keeps the AlphaEvolve math suite grouped before more cases are added", () => {
    const result = splitExampleTemplates([
      {
        name: "alphaevolve_math_benchmark",
        configs: [{ name: "circle_packing/code_config.yaml" }],
      },
      { name: "sorting_benchmark", configs: [{ name: "config.yaml" }] },
    ])

    expect(result.groups.map((template) => template.name)).toEqual([
      "alphaevolve_math_benchmark",
    ])
    expect(result.standalone.map((template) => template.name)).toEqual([
      "sorting_benchmark",
    ])
  })

  test("automatically treats any multi-case template directory as a group", () => {
    const result = splitExampleTemplates([
      {
        name: "future_benchmark",
        configs: [
          { name: "case_a_config.yaml" },
          { name: "case_b_config.yaml" },
        ],
      },
    ])

    expect(result.groups).toHaveLength(1)
    expect(result.standalone).toHaveLength(0)
  })

  test("provides stable labels for known groups and readable fallback case names", () => {
    expect(getTemplateGroupKey("alphaevolve_math_benchmark")).toBe(
      "alphaEvolveMath",
    )
    expect(
      getTemplateCaseKey(
        "alphaevolve_math_benchmark",
        "circle_packing/code_config.yaml",
      ),
    ).toBe("circlePacking")
    expect(
      getTemplateCaseKey(
        "alphaevolve_math_benchmark",
        "circle_packing/solver_config.yaml",
      ),
    ).toBe("circlePackingSolver")
    expect(
      [
        ["circle_rectangle/config.yaml", "circleRectanglePacking"],
        ["hexagon_packing/config.yaml", "hexagonPacking"],
        ["max_min_distance_ratio/config.yaml", "maxMinDistanceRatio"],
        ["minimum_overlap/config.yaml", "minimumOverlap"],
        ["uncertainty_inequality/config.yaml", "uncertaintyInequality"],
        ["second_autocorrelation/config.yaml", "secondAutocorrelation"],
        ["first_autocorrelation/config.yaml", "firstAutocorrelation"],
        ["sums_differences/config.yaml", "sumsDifferences"],
        ["heilbronn_triangle/config.yaml", "heilbronnTriangle"],
        ["heilbronn_square/config.yaml", "heilbronnSquare"],
      ].map(([configName]) =>
        getTemplateCaseKey("alphaevolve_math_benchmark", configName),
      ),
    ).toEqual([
      "circleRectanglePacking",
      "hexagonPacking",
      "maxMinDistanceRatio",
      "minimumOverlap",
      "uncertaintyInequality",
      "secondAutocorrelation",
      "firstAutocorrelation",
      "sumsDifferences",
      "heilbronnTriangle",
      "heilbronnSquare",
    ])
    expect(humanizeConfigName("circle_packing/code_config.yaml")).toBe("Code")
    expect(humanizeConfigName("config.yaml")).toBe("Default")
  })
})
