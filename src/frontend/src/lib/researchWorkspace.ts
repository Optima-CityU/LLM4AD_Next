export type ResearchWorkspaceMode = "proposal" | "manuscript" | "algorithm"

export function parseResearchWorkspaceMode(
  value: unknown,
): ResearchWorkspaceMode {
  if (value === "algorithm" || value === "manuscript") return value
  return "proposal"
}
