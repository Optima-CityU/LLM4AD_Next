import type { EvaluationFile } from "../client/types.gen"
import { evaluationPreviewKind } from "./evaluationArtifacts.ts"

type EvidenceRole = "reference" | "baseline" | "candidate"

export interface EvidenceComparison {
  id: string
  label: string
  kind: "static" | "game"
  images: { role: EvidenceRole; file: EvaluationFile }[]
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

/** Resolve complete comparison groups by exact logical artifact name. */
export function resolveEvidenceComparisons(
  manifest: unknown,
  files: EvaluationFile[],
): EvidenceComparison[] {
  if (!Array.isArray(manifest)) return []
  const names = new Map<string, EvaluationFile[]>()
  for (const file of files) {
    const matches = names.get(file.name) ?? []
    if (!matches.some((item) => item.id === file.id)) matches.push(file)
    names.set(file.name, matches)
  }
  const groups: EvidenceComparison[] = []
  for (const item of manifest) {
    const group = asRecord(item)
    if (
      !group ||
      typeof group.id !== "string" ||
      !group.id.trim() ||
      typeof group.label !== "string" ||
      !group.label.trim() ||
      (group.kind !== "static" && group.kind !== "game") ||
      !Array.isArray(group.images) ||
      groups.some((existing) => existing.id === group.id)
    )
      continue
    const roles: EvidenceRole[] =
      group.kind === "static"
        ? ["reference", "baseline", "candidate"]
        : ["baseline", "candidate"]
    if (group.images.length !== roles.length) continue
    const images: EvidenceComparison["images"] = []
    for (const role of roles) {
      const matches = group.images
        .map(asRecord)
        .filter((image) => image?.role === role)
      if (matches.length !== 1 || typeof matches[0]?.artifact !== "string")
        break
      const artifacts = names.get(matches[0].artifact)
      const file = artifacts?.length === 1 ? artifacts[0] : undefined
      if (
        !file ||
        evaluationPreviewKind(file) !== "image" ||
        images.some((image) => image.file.id === file.id)
      )
        break
      images.push({ role, file })
    }
    if (images.length === roles.length)
      groups.push({
        id: group.id,
        label: group.label,
        kind: group.kind,
        images,
      })
  }
  return groups
}
