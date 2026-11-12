import { Loader2, RotateCw } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { useARLSession } from "./ARLSessionContext"
import {
  canRestartValidation,
  isValidationReady,
  workerWaitKey,
} from "./arlAgentMeta"

/**
 * Right-column validation status line for ARL sessions, above the file panel.
 *
 * Deliberately minimal: one line saying whether the Validator's validation
 * service is usable, plus a hint pointing to the Validator conversation when
 * it is not. Connection state and the reconnect button live in the center
 * panel, so they are not repeated here.
 *
 * When the service was set up but is not running (the container slept and
 * Paseo does not bring service scripts back), the hint carries a restart
 * button. It only shows in that state, so it cannot be used to start a
 * service that does not exist yet; while a start is in flight it turns into
 * a spinner, and the backend de-duplicates restarts on its side too.
 *
 * The backend polls for the validation service and pushes its state, so this
 * is otherwise a pure view.
 */
export default function ARLAgentStatusPanel() {
  const { t } = useTranslation()
  const { connected, panes, validation, restartValidation } = useARLSession()
  // Our own request in flight; `validation.starting` covers starts issued by
  // the backend itself or from another tab.
  const [restarting, setRestarting] = useState(false)

  // Held back until the Worker is up: during startup "not ready" is true but
  // useless, since nothing can act on it yet.
  const workerReady = connected && panes.worker.ready
  const ready = isValidationReady(validation)
  const starting = !ready && (validation.starting || restarting)
  const stopped = !ready && !starting && validation.declared

  const handleRestart = async () => {
    if (restarting || !canRestartValidation(validation)) return
    setRestarting(true)
    try {
      const result = await restartValidation()
      if (!result.ok) {
        const key =
          result.action === "not_declared"
            ? "autoResearch.arl.validationRestartNotDeclared"
            : result.action === "disconnected"
              ? "autoResearch.arl.validationRestartDisconnected"
              : "autoResearch.arl.validationRestartFailed"
        toast.error(t(key), {
          description: result.action === "failed" ? result.message : undefined,
        })
      }
    } finally {
      setRestarting(false)
    }
  }

  const statusKey = !workerReady
    ? "validationWaiting"
    : ready
      ? "validationOk"
      : starting
        ? "validationStarting"
        : stopped
          ? "validationStopped"
          : "validationNotReady"

  return (
    <section
      data-tour="arl-agent-status"
      className="shrink-0 border-b border-border/40 px-3 py-2"
    >
      <div className="flex items-center gap-2 text-[12px]">
        {!workerReady || starting ? (
          <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground/70" />
        ) : (
          <span
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              ready ? "bg-emerald-500" : "bg-amber-500",
            )}
          />
        )}
        <span className="text-muted-foreground">
          {t("autoResearch.arl.validationLabel")}
        </span>
        <span
          className={cn(
            "ml-auto truncate",
            !workerReady || starting
              ? "text-muted-foreground/70"
              : ready
                ? "text-emerald-700 dark:text-emerald-400"
                : "text-amber-700 dark:text-amber-400",
          )}
        >
          {t(`autoResearch.arl.${statusKey}`)}
        </span>
      </div>
      {workerReady && !ready && (
        <div className="mt-1 flex items-start gap-2">
          <p className="min-w-0 flex-1 text-[11px] leading-snug text-muted-foreground">
            {starting
              ? t("autoResearch.arl.validationStartingHint")
              : stopped
                ? t("autoResearch.arl.validationStoppedHint")
                : t(workerWaitKey(panes.validator.items.length > 0, validation))}
          </p>
          {stopped && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleRestart}
              disabled={restarting}
              className="h-6 shrink-0 gap-1 rounded-full border-amber-500/40 px-2.5 text-[11.5px] font-normal text-amber-700 hover:bg-amber-500/10 hover:text-amber-800 dark:text-amber-400 dark:hover:bg-amber-500/15 dark:hover:text-amber-300"
            >
              <RotateCw className="size-3" />
              {t("autoResearch.arl.validationRestart")}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
