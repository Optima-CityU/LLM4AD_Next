import type { ExampleTemplateItem } from "@/client"

const KNOWN_TEMPLATE_GROUPS = {
  alphaevolve_math_benchmark: "alphaEvolveMath",
} as const

const KNOWN_TEMPLATE_CASES = {
  "alphaevolve_math_benchmark/circle_packing/code_config.yaml": "circlePacking",
  "alphaevolve_math_benchmark/circle_packing/solver_config.yaml":
    "circlePackingSolver",
  "alphaevolve_math_benchmark/circle_rectangle/config.yaml":
    "circleRectanglePacking",
  "alphaevolve_math_benchmark/hexagon_packing/config.yaml": "hexagonPacking",
  "alphaevolve_math_benchmark/max_min_distance_ratio/config.yaml":
    "maxMinDistanceRatio",
  "alphaevolve_math_benchmark/minimum_overlap/config.yaml": "minimumOverlap",
  "alphaevolve_math_benchmark/uncertainty_inequality/config.yaml":
    "uncertaintyInequality",
  "alphaevolve_math_benchmark/second_autocorrelation/config.yaml":
    "secondAutocorrelation",
  "alphaevolve_math_benchmark/first_autocorrelation/config.yaml":
    "firstAutocorrelation",
  "alphaevolve_math_benchmark/sums_differences/config.yaml": "sumsDifferences",
  "alphaevolve_math_benchmark/heilbronn_triangle/config.yaml":
    "heilbronnTriangle",
  "alphaevolve_math_benchmark/heilbronn_square/config.yaml": "heilbronnSquare",
} as const

export type TemplateGroupKey =
  (typeof KNOWN_TEMPLATE_GROUPS)[keyof typeof KNOWN_TEMPLATE_GROUPS]
export type TemplateCaseKey =
  (typeof KNOWN_TEMPLATE_CASES)[keyof typeof KNOWN_TEMPLATE_CASES]

export function getTemplateGroupKey(
  name: string,
): TemplateGroupKey | undefined {
  return KNOWN_TEMPLATE_GROUPS[name as keyof typeof KNOWN_TEMPLATE_GROUPS]
}

export function getTemplateCaseKey(
  templateName: string,
  configName: string,
): TemplateCaseKey | undefined {
  const key = `${templateName}/${configName}`
  return KNOWN_TEMPLATE_CASES[key as keyof typeof KNOWN_TEMPLATE_CASES]
}

export function isExampleTemplateGroup(template: ExampleTemplateItem): boolean {
  return (
    Boolean(getTemplateGroupKey(template.name)) ||
    (template.configs?.length ?? 0) > 1
  )
}

export function splitExampleTemplates(templates: ExampleTemplateItem[]) {
  return templates.reduce<{
    groups: ExampleTemplateItem[]
    standalone: ExampleTemplateItem[]
  }>(
    (result, template) => {
      if (isExampleTemplateGroup(template)) {
        result.groups.push(template)
      } else {
        result.standalone.push(template)
      }
      return result
    },
    { groups: [], standalone: [] },
  )
}

export function humanizeConfigName(name: string): string {
  const basename = name
    .replace(/^.*\//, "")
    .replace(/\.ya?ml$/i, "")
    .replace(/_config$/i, "")
    .replace(/[-_]+/g, " ")
    .trim()

  if (!basename || basename.toLowerCase() === "config") return "Default"

  return basename.replace(/\b\w/g, (letter) => letter.toUpperCase())
}
