import { ChevronDown } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import type { PaperRegion } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { usePaperReviewContent } from "@/hooks/usePapers"
import { paperRegionEvidenceStatus } from "@/lib/paperRegions"
import { authFetch } from "@/utils/auth"

type SourceRef = NonNullable<PaperRegion["source_refs"]>[number]

function EvidenceSource({
  source,
  onOpenFile,
}: {
  source: SourceRef
  onOpenFile?: (path: string) => void
}) {
  const { t } = useTranslation()
  const [opened, setOpened] = useState(false)
  const [archived, setArchived] = useState<string | null>(null)
  const [archiveError, setArchiveError] = useState(false)
  const [archiveLoading, setArchiveLoading] = useState(false)
  const review = usePaperReviewContent(
    opened ? (source.review_id ?? null) : null,
  )
  const status = paperRegionEvidenceStatus(source)
  const archivePath =
    source.path?.startsWith("/api/v1/llm4ad/papers/workspaces/") &&
    source.path.includes("/region-review-sources/")
      ? source.path
      : null
  const loadArchive = async () => {
    if (!archivePath) return
    setArchiveLoading(true)
    setArchiveError(false)
    try {
      const response = await authFetch(
        `${import.meta.env.VITE_API_URL || ""}${archivePath}`,
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const content = response.headers
        .get("content-type")
        ?.includes("application/json")
        ? await response.json()
        : await response.text()
      setArchived(typeof content === "string" ? content : content.content)
    } catch {
      setArchiveError(true)
    } finally {
      setArchiveLoading(false)
    }
  }
  return (
    <div className="min-w-0 space-y-3 border-l-2 border-border pl-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="outline">{t(`paper.regions.evidence.${status}`)}</Badge>
        {source.version != null && (
          <span className="text-muted-foreground">
            {t("paper.regions.version", { version: source.version })}
          </span>
        )}
        {source.review_id && (
          <span className="break-all font-mono text-muted-foreground">
            {source.review_id}
          </span>
        )}
      </div>
      {source.quote && (
        <blockquote className="whitespace-pre-wrap break-words border-l-2 border-primary/30 pl-3 text-[15px] leading-7">
          {source.quote}
        </blockquote>
      )}
      {source.path && (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <code className="break-all text-sm">{source.path}</code>
          {(onOpenFile || archivePath) && (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                archivePath ? void loadArchive() : onOpenFile?.(source.path!)
              }
            >
              {t("paper.regions.openSource")}
            </Button>
          )}
        </div>
      )}
      {archiveLoading && (
        <p role="status" className="text-sm">
          {t("paper.regions.loading")}
        </p>
      )}
      {archiveError && (
        <div role="alert" className="space-y-2 text-sm">
          <p>{t("paper.regions.sourceUnavailable")}</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void loadArchive()}
          >
            {t("paper.regions.retry")}
          </Button>
        </div>
      )}
      {archived !== null && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {t("paper.regions.archivedOriginal", { version: source.version })}
          </p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-4 font-sans text-[15px] leading-7">
            {archived}
          </pre>
        </div>
      )}
      {source.review_id && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setOpened((value) => !value)}
          aria-expanded={opened}
        >
          {t("paper.regions.originalReview")}
        </Button>
      )}
      {opened && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {t("paper.regions.currentOriginal")}
          </p>
          {review.isLoading ? (
            <p role="status">{t("paper.regions.loading")}</p>
          ) : review.isError ? (
            <div role="alert" className="space-y-2 text-sm text-destructive">
              <p>{t("paper.regions.sourceUnavailable")}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void review.refetch()}
              >
                {t("paper.regions.retry")}
              </Button>
            </div>
          ) : (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-4 font-sans text-[15px] leading-7">
              {review.data?.content}
            </pre>
          )}
        </div>
      )}
      {status === "unverified" && (
        <p className="text-sm leading-6 text-muted-foreground">
          {t("paper.regions.unverifiedHint")}
        </p>
      )}
    </div>
  )
}

/** Keep quotes, original reviews, and legacy evidence accessible without inferring verification. */
export default function PaperRegionEvidence({
  region,
  onOpenFile,
}: {
  region: PaperRegion
  onOpenFile?: (path: string) => void
}) {
  const { t } = useTranslation()
  const refs = region.source_refs ?? []
  const sources: SourceRef[] = refs.length
    ? refs
    : region.review_id
      ? [{ review_id: region.review_id, status: "unverified" }]
      : []
  return (
    <details className="group min-w-0 border-t pt-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-sm py-1 text-sm font-medium text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <ChevronDown
          className="size-4 group-open:rotate-180"
          aria-hidden="true"
        />
        {t("paper.regions.sources")}
        <span className="font-normal tabular-nums">
          {refs.length || t("paper.regions.evidence.unverified")}
        </span>
      </summary>
      <div className="mt-3 space-y-5">
        {sources.length ? (
          sources.map((source, index) => (
            <EvidenceSource
              key={`${source.review_id}-${source.path}-${source.version}-${index}`}
              source={source}
              onOpenFile={onOpenFile}
            />
          ))
        ) : (
          <p className="text-sm leading-6 text-muted-foreground">
            {t("paper.regions.noSources")}
          </p>
        )}
      </div>
    </details>
  )
}
