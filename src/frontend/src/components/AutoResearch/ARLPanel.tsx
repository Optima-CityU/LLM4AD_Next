import { ArrowRight, Check, Loader2, RotateCw, WifiOff } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { ARL_ROLES, type ARLRole } from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

import ARLPane from "./ARLPane"
import { useARLSession } from "./ARLSessionContext"
import {
  canSyncValidation,
  connectionStatusKey,
  validationMessageKeys,
} from "./arlAgentMeta"

/**
 * Left-to-right order of the panes.
 *
 * Kept separate from `ARL_ROLES`, which is the protocol order used for
 * readiness and reducer iteration: changing the layout should not change
 * what "both agents ready" iterates over.
 */
const PANE_ORDER: ARLRole[] = ["validator", "worker"]

/**
 * ARL session view: the Validator and Worker containers side by side.
 *
 * Each half is a self-contained chat page (history above, composer below)
 * rather than a shared event log, because the two agents run independent
 * conversations. A single WebSocket carries both; the backend stamps every
 * frame with a `role`, which is what routes events into the correct pane.
 * The socket itself is owned by {@link ARLSessionProvider}, which the
 * right-hand panels share.
 *
 * Session-wide status (connection, validation handoff) lives in the right
 * column's agent status card. This view only surfaces the connection when it
 * is down, because that is the one state that blocks both panes at once.
 */
export default function ARLPanel() {
  const { t } = useTranslation()
  const {
    status,
    connected,
    connecting,
    offline,
    panes,
    validation,
    syncValidation,
    commands,
    listCommands,
    sendMessage,
    cancelTurn,
    setMode,
    respondPermission,
    respondPermissions,
    loadOlder,
    fetchSubagentTimeline,
    resetAgent,
    reconnect,
  } = useARLSession()

  const agentsReady = ARL_ROLES.every((role) => panes[role].ready)
  const booting = connected && !agentsReady

  // Handoff button on the seam: Validator → Worker. Same action as the one
  // in the status card, placed where the user's eyes already are (the
  // composers) so the cross-pane step is not missed.
  const synced = !!validation.syncedAt
  const syncable = canSyncValidation(validation)
  const syncing = validation.syncing
  const handoffTitle = syncing
    ? t("autoResearch.arl.validationSyncing")
    : syncable
      ? t(
          synced
            ? "autoResearch.arl.validationResync"
            : "autoResearch.arl.validationSync",
        )
      : t(`autoResearch.arl.${validationMessageKeys(validation).title}`)

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {/* Only shown while the socket is down. Automatic retries are capped,
          so once they run out the retry button is the way back. */}
      {!connected && (
        <div className="pointer-events-none absolute inset-x-0 top-3 z-30 flex justify-center">
          <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-border/50 bg-background/85 py-1 pr-1 pl-3 text-[12px] text-muted-foreground shadow-lg backdrop-blur">
            {connecting ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <WifiOff className="size-3" />
            )}
            {t(`autoResearch.arl.${connectionStatusKey(status, booting)}`)}
            {offline ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={reconnect}
                className="h-6 gap-1 rounded-full px-2 text-[11.5px] font-normal"
              >
                <RotateCw className="size-3" />
                {t("autoResearch.arl.retryConnect")}
              </Button>
            ) : (
              <span className="pr-2" />
            )}
          </div>
        </div>
      )}

      {/* Validator | Worker. `min-h-0` on the row and on each pane is what
          keeps the history regions scrolling instead of growing. */}
      <div className="relative flex min-h-0 flex-1">
        {PANE_ORDER.map((role) => (
          <ARLPane
            key={role}
            role={role}
            state={panes[role]}
            connected={connected}
            connecting={connecting}
            onSend={sendMessage}
            commands={commands[role]}
            onListCommands={listCommands}
            onCancel={cancelTurn}
            onSetMode={setMode}
            onPermission={respondPermission}
            onPermissions={respondPermissions}
            validation={validation}
            onSyncValidation={syncValidation}
            validatorStarted={panes.validator.items.length > 0}
            onLoadOlder={loadOlder}
            onFetchSubagentTimeline={fetchSubagentTimeline}
            onReset={resetAgent}
          />
        ))}

        {/* Seam between the panes: a gradient hairline that fades out at
            both ends, with the handoff button at composer height. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-6 left-1/2 w-px -translate-x-1/2 bg-linear-to-b from-transparent via-border to-transparent"
        />
        <button
          type="button"
          onClick={syncValidation}
          disabled={!connected || !syncable || syncing}
          title={handoffTitle}
          aria-label={handoffTitle}
          className={cn(
            "group absolute bottom-[60px] left-1/2 z-10 grid size-8 -translate-x-1/2 translate-y-1/2 place-items-center rounded-full border bg-background shadow-md transition-[color,border-color,transform,box-shadow]",
            "enabled:hover:scale-110 enabled:active:scale-95 disabled:cursor-not-allowed",
            synced
              ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400"
              : syncable
                ? "border-amber-500/50 text-amber-600 shadow-amber-500/20 dark:text-amber-400"
                : "border-border/70 text-muted-foreground/40 shadow-sm",
          )}
        >
          {syncing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : synced ? (
            <>
              <Check
                className="size-3.5 group-hover:hidden"
                strokeWidth={2.5}
              />
              <RotateCw className="hidden size-3.5 group-hover:block" />
            </>
          ) : (
            <ArrowRight className="size-3.5" />
          )}
          {/* Ready but never synced: ping to pull the eye over. */}
          {syncable && !synced && !syncing && (
            <span className="pointer-events-none absolute inset-0 animate-ping rounded-full border border-amber-500/60" />
          )}
        </button>
      </div>
    </div>
  )
}
