import type { PaperProposalChapter } from "@/client"
import type { ResearchWorkflowStep } from "@/lib/researchAgentProgress"

export type ProposalChapter = PaperProposalChapter

export type ProposalRenderRevision = {
  sourceVersionId: string
  sourceHash: string
  entryPaths: string[]
}

/** Preserve the server's chapter order and keep legacy documents intact. */
export function proposalChapters(
  manifest: string[],
  entryPath: string,
  chapters?: ProposalChapter[],
): ProposalChapter[] {
  if (!/\.(md|markdown)$/i.test(entryPath)) return []
  const seen = new Set<string>()
  const available = (chapters ?? []).filter((chapter) => {
    if (
      !manifest.includes(chapter.path) ||
      !/\.(md|markdown)$/i.test(chapter.path) ||
      seen.has(chapter.path)
    )
      return false
    seen.add(chapter.path)
    return true
  })
  return available.length
    ? available
    : [
        {
          id: entryPath,
          title: entryPath,
          path: entryPath,
          status: null,
          has_content: true,
        },
      ]
}

export function proposalRenderKey(
  workspaceId: string,
  revision: ProposalRenderRevision,
) {
  return [
    "paper-proposal-render",
    workspaceId,
    revision.sourceVersionId,
    revision.sourceHash,
    ...revision.entryPaths,
  ]
}

/** Empty upstream templates are navigation scaffolds, not readable drafts. */
export function proposalDraftChapters(chapters: ProposalChapter[]) {
  return chapters.filter(
    (chapter) => chapter.has_content || chapter.id === "document",
  )
}

/** Resolve only readable results belonging to this logical step and revision. */
export function proposalStepResult(
  step: ResearchWorkflowStep,
  manifest: string[],
  chapters: ProposalChapter[],
  readableSourcePaths: string[],
  layoutReady: boolean,
): { view: "document" | "source" | "layout"; path: string | null } | null {
  if (step.status !== "completed") return null
  for (const path of step.outputPaths) {
    if (
      !manifest.includes(path) ||
      path === "proposal-state.json" ||
      path === "proposal-log.md"
    )
      continue
    const chapter = chapters.find(
      (item) => item.path === path && item.has_content,
    )
    if (chapter)
      return { view: chapter.id === "S4" ? "source" : "document", path }
    if (readableSourcePaths.includes(path)) return { view: "source", path }
  }
  return step.stage === "final_review" && layoutReady
    ? { view: "layout", path: null }
    : null
}
