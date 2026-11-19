import { ChevronDown } from "lucide-react"
import { Collapsible } from "radix-ui"
import { createContext, type ReactNode, useContext, useEffect } from "react"
import { useTranslation } from "react-i18next"

import type { PaperRegion } from "@/client"
import PaperRegionEditor from "@/components/Paper/PaperRegionEditor"
import { Button } from "@/components/ui/button"
import { usePaperRegionActions, usePaperRegions } from "@/hooks/usePaperRegions"
import {
  groupPaperRegions,
  type PaperRegionAgentRequest,
  type PaperRegionGroup,
  type PaperRegionView,
  paperRegionSection,
  paperRegionVisible,
} from "@/lib/paperRegions"

type PaperRegionProviderProps = {
  workspaceId?: string
  onRequestAgent?: (request: PaperRegionAgentRequest) => void
  onOpenFile?: (path: string) => void
  children: ReactNode
}

const RegionContext = createContext<{
  workspaceId: string
  query: ReturnType<typeof usePaperRegions>
  actions: ReturnType<typeof usePaperRegionActions>
  onRequestAgent?: PaperRegionProviderProps["onRequestAgent"]
  onOpenFile?: PaperRegionProviderProps["onOpenFile"]
} | null>(null)

function ConnectedProvider({
  workspaceId,
  onRequestAgent,
  onOpenFile,
  children,
}: PaperRegionProviderProps) {
  const query = usePaperRegions(workspaceId)
  const actions = usePaperRegionActions(workspaceId)
  useEffect(() => {
    for (const request of query.data?.requests ?? []) {
      if (request.state === "queued")
        onRequestAgent?.({ id: request.id, prompt: request.prompt })
    }
  }, [query.data, onRequestAgent])
  return (
    <RegionContext.Provider
      value={{
        workspaceId: workspaceId!,
        query,
        actions,
        onRequestAgent,
        onOpenFile,
      }}
    >
      {children}
    </RegionContext.Provider>
  )
}

/** Reuse one snapshot and mutation cache across every card in a paper panel. */
export function PaperRegionProvider(props: PaperRegionProviderProps) {
  const inherited = useContext(RegionContext)
  if (inherited || !props.workspaceId) return props.children
  return <ConnectedProvider key={props.workspaceId} {...props} />
}

export function usePaperRegionWorkbench() {
  return useContext(RegionContext)
}

type Props = {
  view: PaperRegionView
  stage?: string
  paths?: string[]
  sourceOnly?: boolean
  reviewerLabels?: Record<string, string>
  toolbar?: ReactNode
  children?: ReactNode
}

function RegionGroup({ group }: { group: PaperRegionGroup }) {
  const { t } = useTranslation()
  const context = usePaperRegionWorkbench()
  const secondaryIds = new Set(group.secondary.map((region) => region.id))
  const attention =
    context?.query.data?.requests.some(
      (request) =>
        secondaryIds.has(request.region_id) && request.state === "candidate",
    ) ?? false
  const disclosure =
    group.key === "original-reviews"
      ? "paper.regions.sections.review"
      : group.key === "reviewer-context"
        ? "paper.regions.reviewerContext"
        : "paper.regions.details"
  return (
    <div className="min-w-0 space-y-2" data-testid="paper-region-group">
      {group.primary.map((region) => (
        <PaperRegionEditor key={region.id} region={region} />
      ))}
      {group.secondary.length > 0 && (
        <Collapsible.Root
          defaultOpen={attention}
          className="min-w-0 overflow-hidden rounded-md border bg-muted/10"
        >
          <Collapsible.Trigger className="group flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
            <span>
              {t(disclosure)}{" "}
              <span className="ml-1 font-normal tabular-nums text-muted-foreground">
                {group.secondary.length}
              </span>
              {attention && (
                <span className="ml-2 text-primary">
                  {t("paper.regions.states.candidate")}
                </span>
              )}
            </span>
            <ChevronDown
              className="size-4 shrink-0 group-data-[state=open]:rotate-180"
              aria-hidden="true"
            />
          </Collapsible.Trigger>
          <Collapsible.Content>
            {group.secondary.map((region) => (
              <PaperRegionEditor key={region.id} region={region} compact />
            ))}
          </Collapsible.Content>
        </Collapsible.Root>
      )}
    </div>
  )
}

/** Group editable server regions into readable vertical reviewer dossiers. */
export default function PaperRegionWorkbench({
  view,
  stage,
  paths = [],
  sourceOnly = false,
  reviewerLabels = {},
  toolbar,
  children,
}: Props) {
  const { t } = useTranslation()
  const context = usePaperRegionWorkbench()
  if (!context) return children
  const { query } = context
  const regions = (query.data?.regions ?? []).filter((region) =>
    sourceOnly
      ? region.kind === "proposal_section" && paths.includes(region.title)
      : paperRegionVisible(region, view, stage),
  )
  const visibleRegions = regions.filter(
    (region) =>
      view !== "proposal" ||
      region.kind !== "proposal_section" ||
      paths.includes(region.title),
  )
  const otherFiles = regions.filter(
    (region) =>
      view === "proposal" &&
      region.kind === "proposal_section" &&
      !paths.includes(region.title),
  )
  if (!regions.length)
    return (
      <>
        {query.isError && (
          <div
            role="alert"
            className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-amber-500/30 p-4 text-sm"
          >
            <span>{t("paper.regions.loadFailed")}</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void query.refetch()}
            >
              {t("paper.regions.retry")}
            </Button>
          </div>
        )}
        {children}
      </>
    )
  const groups = new Map<string, PaperRegion[]>()
  for (const region of visibleRegions) {
    const key = region.review_id || ""
    groups.set(key, [...(groups.get(key) ?? []), region])
  }
  const sectionOrder = [
    "review",
    "analysis",
    "reply",
    "global",
    "author",
    "chair",
    "proposal",
  ]
  return (
    <div className="min-w-0 space-y-7" data-testid="paper-region-workbench">
      <p className="text-sm leading-6 text-muted-foreground">
        {t("paper.regions.hint")}
      </p>
      {toolbar && <div className="flex justify-end">{toolbar}</div>}
      {Array.from(groups, ([reviewId, items]) => (
        <section
          key={reviewId}
          className="min-w-0 space-y-4"
          aria-label={reviewerLabels[reviewId] || t("paper.regions.shared")}
        >
          <header className="flex min-w-0 flex-wrap items-baseline justify-between gap-2 border-b pb-3">
            <h3 className="break-words font-serif text-xl font-semibold">
              {reviewerLabels[reviewId] ||
                (reviewId
                  ? t("paper.regions.reviewer", { id: reviewId })
                  : t("paper.regions.shared"))}
            </h3>
            <span className="text-sm tabular-nums text-muted-foreground">
              {t("paper.regions.regionCount", { count: items.length })}
            </span>
          </header>
          {groupPaperRegions(
            [...items].sort(
              (a, b) =>
                sectionOrder.indexOf(paperRegionSection(a)) -
                sectionOrder.indexOf(paperRegionSection(b)),
            ),
          ).map((group) => (
            <RegionGroup key={group.key} group={group} />
          ))}
        </section>
      ))}
      {otherFiles.length > 0 && (
        <Collapsible.Root className="min-w-0 overflow-hidden rounded-lg border">
          <Collapsible.Trigger className="group flex w-full items-center justify-between gap-3 px-5 py-4 text-left text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
            <span>
              {t(
                paths.length
                  ? "paper.regions.otherFiles"
                  : "paper.regions.allFiles",
                { count: otherFiles.length },
              )}
            </span>
            <ChevronDown
              className="size-4 shrink-0 group-data-[state=open]:rotate-180"
              aria-hidden="true"
            />
          </Collapsible.Trigger>
          <Collapsible.Content className="space-y-4 border-t p-4">
            {groupPaperRegions(otherFiles).map((group) => (
              <RegionGroup key={group.key} group={group} />
            ))}
          </Collapsible.Content>
        </Collapsible.Root>
      )}
    </div>
  )
}
