import {
  AUTO_DISCOVERY_ENABLED,
  AUTO_REBUTTAL_ENABLED,
} from "@/lib/frontendFeatures"

export type ResearchWorkspaceMode = "proposal" | "manuscript" | "algorithm"

export function parseResearchWorkspaceMode(
  value: unknown,
  autoRebuttalEnabled = AUTO_REBUTTAL_ENABLED,
  autoDiscoveryEnabled = AUTO_DISCOVERY_ENABLED,
): ResearchWorkspaceMode {
  if (value === "algorithm" && autoDiscoveryEnabled) return value
  if (value === "manuscript" && autoRebuttalEnabled) return value
  return "proposal"
}
