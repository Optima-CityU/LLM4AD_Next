import { useState } from "react"
import { useTranslation } from "react-i18next"

import type { PaperRegion, PaperRegionRequest } from "@/client"
import PaperRegionDiff from "@/components/Paper/PaperRegionDiff"
import { usePaperRegionWorkbench } from "@/components/Paper/PaperRegionWorkbench"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { PaperRegionError } from "@/hooks/usePaperRegions"
import { canApplyPaperRegionRequest } from "@/lib/paperRegions"

function RegionRequest({
  region,
  request,
}: {
  region: PaperRegion
  request: PaperRegionRequest
}) {
  const { t } = useTranslation()
  const context = usePaperRegionWorkbench()
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  if (!context) return null
  const candidate =
    request.replacement_text != null &&
    ["candidate", "proposed", "ready", "completed", "suggested"].includes(
      request.state,
    )
  const applicable = canApplyPaperRegionRequest(region, request)
  const terminal = ["applied", "rejected", "cancelled"].includes(request.state)
  const decide = async (action: "apply" | "reject") => {
    setPending(true)
    setError(null)
    try {
      await context.actions.decideRequest.mutateAsync({
        request,
        action,
        revision: request.base_revision,
      })
    } catch (failure) {
      setError(
        t(
          failure instanceof PaperRegionError && failure.status === 409
            ? "paper.regions.conflict"
            : "paper.regions.actionFailed",
        ),
      )
    } finally {
      setPending(false)
    }
  }
  return (
    <article
      className="min-w-0 space-y-3 border-t pt-4"
      data-request-id={request.id}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{t("paper.regions.localRequest")}</p>
        <Badge variant="outline">
          {t(`paper.regions.states.${request.state}`, {
            defaultValue: request.state,
          })}
        </Badge>
      </div>
      <p className="whitespace-pre-wrap break-words text-[15px] leading-7">
        {request.instruction}
      </p>
      {request.answer && (
        <blockquote className="whitespace-pre-wrap border-l-2 pl-3 text-sm leading-6">
          {request.answer}
        </blockquote>
      )}
      {request.message && (
        <p className="whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
          {request.message}
        </p>
      )}
      {candidate && (
        <>
          <PaperRegionDiff
            original={request.original_text}
            replacement={request.replacement_text!}
          />
          {!applicable && (
            <p className="text-sm leading-6 text-amber-800 dark:text-amber-200">
              {t("paper.regions.conflict")}
            </p>
          )}
        </>
      )}
      {!terminal && (
        <div className="flex flex-wrap gap-2">
          {candidate && (
            <Button
              size="sm"
              disabled={!applicable || pending}
              onClick={() => void decide("apply")}
            >
              {t("paper.regions.applySuggestion")}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={pending}
            onClick={() => void decide("reject")}
          >
            {t(
              candidate
                ? "paper.regions.rejectSuggestion"
                : "paper.regions.cancelRequest",
            )}
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </article>
  )
}

/** Keep active candidates visible and place completed requests in a source-like foldout. */
export default function PaperRegionRequests({
  region,
  requests,
}: {
  region: PaperRegion
  requests: PaperRegionRequest[]
}) {
  const { t } = useTranslation()
  const active = requests.filter(
    (request) => !["applied", "rejected", "cancelled"].includes(request.state),
  )
  const history = requests.filter((request) =>
    ["applied", "rejected", "cancelled"].includes(request.state),
  )
  return (
    <div className="space-y-4">
      {active.map((request) => (
        <RegionRequest key={request.id} region={region} request={request} />
      ))}
      {history.length > 0 && (
        <details>
          <summary className="cursor-pointer py-2 text-sm font-medium text-muted-foreground">
            {t("paper.regions.history", { count: history.length })}
          </summary>
          <div className="space-y-4">
            {history.map((request) => (
              <RegionRequest
                key={request.id}
                region={region}
                request={request}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
