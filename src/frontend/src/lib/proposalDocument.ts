import type { PaperProposalChapter } from "@/client"

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
