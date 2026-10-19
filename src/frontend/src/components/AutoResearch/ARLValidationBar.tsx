import { CheckCircle2, CircleAlert, FileDown, Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import type { ARLValidationState } from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

interface Props {
  state: ARLValidationState
  /** Disabled while the socket is down: the sync runs on the backend. */
  connected: boolean
  onSync: () => void
}

/**
 * Validation handoff strip, rendered above the Worker's history.
 *
 * The ARL flow is Validator-first, which is not visible from the Worker pane:
 * a user who starts on the right sees an ordinary chat and no reason to touch
 * the left side. This strip makes that dependency explicit — it either points
 * them at the Validator or offers the one action that completes the handoff.
 *
 * The backend polls for the service and pushes the state, so this is purely a
 * view: the only local concern is the in-flight sync.
 */
export default function ARLValidationBar({
  state,
  connected,
  onSync,
}: Props) {
  const { t } = useTranslation()

  // Sync needs both halves: an address to write into CLAUDE.md and a task.md
  // to copy. They land at different times, so each gets its own message.
  const canSync = state.configured && state.hasTask
  const title = !state.configured
    ? t("autoResearch.arl.validationMissing")
    : state.hasTask
      ? t("autoResearch.arl.validationReady")
      : t("autoResearch.arl.validationNoTask")
  const hint = !state.configured
    ? t("autoResearch.arl.validationMissingHint")
    : state.hasTask
      ? t("autoResearch.arl.validationReadyHint")
      : t("autoResearch.arl.validationNoTaskHint")

  return (
    <div
      className={cn(
        "flex shrink-0 items-start gap-2 border-b px-4 py-2",
        canSync
          ? "border-emerald-500/20 bg-emerald-500/[0.06]"
          : "border-border/60 bg-muted/30",
      )}
    >
      {canSync ? (
        <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/60" />
      )}

      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] font-medium leading-snug text-foreground">
          {title}
        </p>
        <p className="text-[11.5px] leading-snug text-muted-foreground">
          {hint}
        </p>
        {/* The address is the one piece worth showing: it is what the Worker
            will be told to call, and seeing it is how a user notices the
            Validator restarted on a different port. The token is never sent
            to the browser. */}
        {state.url && (
          <p className="mt-0.5 truncate font-mono text-[10.5px] text-muted-foreground/50">
            {state.url}
          </p>
        )}
      </div>

      {canSync && (
        <Button
          size="sm"
          variant={state.syncedAt ? "ghost" : "secondary"}
          disabled={!connected || state.syncing}
          onClick={onSync}
          className="h-7 shrink-0 gap-1.5 px-2 text-[11.5px]"
        >
          {state.syncing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <FileDown className="size-3.5" />
          )}
          {state.syncing
            ? t("autoResearch.arl.validationSyncing")
            : state.syncedAt
              ? t("autoResearch.arl.validationResync")
              : t("autoResearch.arl.validationSync")}
        </Button>
      )}
    </div>
  )
}
