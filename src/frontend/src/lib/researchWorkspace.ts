export type ResearchWorkspaceMode = "proposal" | "manuscript" | "algorithm"

export function parseResearchWorkspaceMode(
  value: unknown,
): ResearchWorkspaceMode {
  return value === "manuscript" || value === "algorithm" ? value : "proposal"
}
