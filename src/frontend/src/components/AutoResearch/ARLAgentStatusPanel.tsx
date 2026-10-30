import { Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

import { useARLSession } from "./ARLSessionContext"
import { canSyncValidation, workerWaitKey } from "./arlAgentMeta"

/**
 * Right-column validation status line for ARL sessions, above the file panel.
 *
 * Deliberately minimal: one line saying whether the Validator's validation
 * service is usable, plus a hint pointing to the Validator conversation when
 * it is not. Connection state, the reconnect button and the sync action live
 * in the center panel, so they are not repeated here.
 *
 * The backend polls for the validation service and pushes its state, so this
 * is a pure view.
 */
export default function ARLAgentStatusPanel() {
  const { t } = useTranslation()
  const { connected, panes, validation } = useARLSession()

  // Held back until the Worker is up: during startup "not ready" is true but
  // useless, since nothing can act on it yet.
  const workerReady = connected && panes.worker.ready
  const ready = canSyncValidation(validation)

  return (
    <section className="shrink-0 border-b border-border/40 px-3 py-2">
      <div className="flex items-center gap-2 text-[12px]">
        {workerReady ? (
          <span
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              ready ? "bg-emerald-500" : "bg-amber-500",
            )}
          />
        ) : (
          <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground/70" />
        )}
        <span className="text-muted-foreground">
          {t("autoResearch.arl.validationLabel")}
        </span>
        <span
          className={cn(
            "ml-auto truncate",
            !workerReady
              ? "text-muted-foreground/70"
              : ready
                ? "text-emerald-700 dark:text-emerald-400"
                : "text-amber-700 dark:text-amber-400",
          )}
        >
          {!workerReady
            ? t("autoResearch.arl.validationWaiting")
            : ready
              ? t("autoResearch.arl.validationOk")
              : t("autoResearch.arl.validationNotReady")}
        </span>
      </div>
      {workerReady && !ready && (
        <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
          {t(workerWaitKey(panes.validator.items.length > 0))}
        </p>
      )}
    </section>
  )
}
