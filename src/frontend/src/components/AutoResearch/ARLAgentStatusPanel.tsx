import { Activity, Check, FileDown, Loader2, RotateCw } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { ARL_ROLES } from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

import { useARLSession } from "./ARLSessionContext"
import {
  canSyncValidation,
  connectionStatusKey,
  validationMessageKeys,
} from "./arlAgentMeta"

/**
 * Right-column "agent status" card for ARL sessions, above the file panel.
 *
 * Holds the session-wide state that belongs to neither pane: the socket
 * connection and the Validator → Worker handoff (service detection +
 * `task.md` sync). Per-agent name and status are not repeated here; each
 * pane header already shows them. The sync action is also offered on the
 * seam between the panes, at composer height.
 *
 * The backend polls for the validation service and pushes its state, so the
 * handoff block is purely a view plus the sync trigger.
 */
export default function ARLAgentStatusPanel() {
  const { t } = useTranslation()
  const {
    status,
    connected,
    connecting,
    offline,
    panes,
    systemMessage,
    validation,
    syncValidation,
    reconnect,
  } = useARLSession()

  const agentsReady = ARL_ROLES.every((role) => panes[role].ready)
  const booting = connected && !agentsReady
  const connectionTone =
    connected && agentsReady
      ? "bg-emerald-500"
      : connecting || booting
        ? "bg-amber-500 animate-pulse"
        : "bg-muted-foreground/40"

  // Held back until the Worker is up: during startup "no validation service
  // detected" is true but useless, since nothing can act on it yet.
  const workerReady = connected && panes.worker.ready
  const syncable = canSyncValidation(validation)
  const synced = !!validation.syncedAt
  const messages = validationMessageKeys(validation)

  const steps = [
    { key: "validationStepService", done: validation.configured },
    { key: "validationStepTask", done: validation.hasTask },
    { key: "validationStepSync", done: synced },
  ]

  return (
    <section className="shrink-0 border-b border-border/40">
      {/* Title row: mirrors the file panel's header styling */}
      <div className="flex h-9 items-center gap-2 px-3">
        <Activity className="size-3.5 shrink-0 text-primary/80" />
        <h2 className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wider text-foreground/80">
          {t("autoResearch.arl.agentStatus")}
        </h2>
        <span
          className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
          title={systemMessage ?? undefined}
        >
          <span className={cn("size-1.5 rounded-full", connectionTone)} />
          {t(`autoResearch.arl.${connectionStatusKey(status, booting)}`)}
        </span>
        {offline && (
          <button
            type="button"
            onClick={reconnect}
            title={t("autoResearch.arl.retryConnect")}
            aria-label={t("autoResearch.arl.retryConnect")}
            className="grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
          >
            <RotateCw className="size-3.5" />
          </button>
        )}
      </div>

      <div className="px-2.5 pb-3">
        {/* Validation handoff: Validator → Worker */}
        <div
          className={cn(
            "rounded-lg border px-2.5 py-2.5 transition-colors",
            syncable && !synced
              ? "border-emerald-500/30 bg-emerald-500/[0.06]"
              : "border-border/50 bg-background/60 dark:bg-background/30",
          )}
        >
          <div className="mb-2 flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-wider text-muted-foreground/80">
            {t("autoResearch.arl.validationTitle")}
          </div>

          {!workerReady ? (
            <p className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground/70">
              <Loader2 className="size-3 animate-spin" />
              {t("autoResearch.arl.validationWaiting")}
            </p>
          ) : (
            <>
              {/* Three-step progress: service → task.md → synced */}
              <ol className="mb-2.5 flex items-center">
                {steps.map((step, index) => (
                  <li
                    key={step.key}
                    className={cn(
                      "flex items-center",
                      index < steps.length - 1 && "flex-1",
                    )}
                  >
                    <span
                      className={cn(
                        "flex items-center gap-1 whitespace-nowrap text-[10.5px]",
                        step.done
                          ? "text-emerald-700 dark:text-emerald-400"
                          : "text-muted-foreground/60",
                      )}
                    >
                      <span
                        className={cn(
                          "grid size-3.5 place-items-center rounded-full",
                          step.done
                            ? "bg-emerald-500 text-white"
                            : "ring-1 ring-border ring-inset",
                        )}
                      >
                        {step.done && (
                          <Check className="size-2.5" strokeWidth={3} />
                        )}
                      </span>
                      {t(`autoResearch.arl.${step.key}`)}
                    </span>
                    {index < steps.length - 1 && (
                      <span
                        className={cn(
                          "mx-1.5 h-px flex-1",
                          step.done ? "bg-emerald-500/40" : "bg-border",
                        )}
                      />
                    )}
                  </li>
                ))}
              </ol>

              <p className="text-[12px] font-medium leading-snug text-foreground">
                {t(`autoResearch.arl.${messages.title}`)}
              </p>
              <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                {t(`autoResearch.arl.${messages.hint}`)}
              </p>
              {/* The address is the one piece worth showing: it is what the
                  Worker will be told to call, and seeing it is how a user
                  notices the Validator restarted on a different port. The
                  token is never sent to the browser. */}
              {validation.url && (
                <p
                  className="mt-1.5 truncate rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground"
                  title={validation.url}
                >
                  {validation.url}
                </p>
              )}

              {syncable && (
                <Button
                  size="sm"
                  variant={synced ? "outline" : "default"}
                  disabled={!connected || validation.syncing}
                  onClick={syncValidation}
                  className="mt-2.5 h-7 w-full gap-1.5 text-[11.5px]"
                >
                  {validation.syncing ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <FileDown className="size-3.5" />
                  )}
                  {validation.syncing
                    ? t("autoResearch.arl.validationSyncing")
                    : synced
                      ? t("autoResearch.arl.validationResync")
                      : t("autoResearch.arl.validationSync")}
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
