import { createFileRoute } from "@tanstack/react-router"

import { parseResearchWorkspaceMode } from "@/lib/researchWorkspace"

import { PaperWorkbench } from "../_layout/papers"

export const Route = createFileRoute("/_layout_paper/papers/$workspaceId")({
  component: PaperWorkbenchRoute,
  validateSearch: (search: Record<string, unknown>) => ({
    mode: parseResearchWorkspaceMode(search.mode),
  }),
  head: () => ({ meta: [{ title: "Research Studio - LLM4AD Next" }] }),
})

function PaperWorkbenchRoute() {
  const { workspaceId } = Route.useParams()
  const { mode } = Route.useSearch()
  return <PaperWorkbench workspaceId={workspaceId} entryMode={mode} />
}
