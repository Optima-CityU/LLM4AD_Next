import { createFileRoute } from "@tanstack/react-router"

import KnowledgeWorkspace from "@/components/Knowledge/KnowledgeWorkspace"

export const Route = createFileRoute("/_layout/knowledge")({
  component: KnowledgePage,
  head: () => ({ meta: [{ title: "Knowledge - LLM4AD_Next" }] }),
})

function KnowledgePage() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      <KnowledgeWorkspace />
    </div>
  )
}
