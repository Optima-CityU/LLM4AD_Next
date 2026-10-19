import { Loader2, RotateCw, WifiOff } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { ARL_ROLES, type ARLRole } from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

import ARLPane from "./ARLPane"
import { useARLSession } from "./ARLSessionContext"

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
 * right-hand file panel shares.
 */
export default function ARLPanel() {
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
    commands,
    listCommands,
    sendMessage,
    cancelTurn,
    setMode,
    respondPermission,
    respondPermissions,
    reconnect,
  } = useARLSession()

  // The socket being up is only half the story: the backend is not reading
  // our frames until it has booted both containers and announced
  // `agents_ready`. Report that phase instead of claiming "connected".
  const agentsReady = ARL_ROLES.every((role) => panes[role].ready)
  const booting = connected && !agentsReady

  const statusLabel =
    status === "reconnecting"
      ? t("autoResearch.arl.reconnecting")
      : status === "connecting"
        ? t("autoResearch.arl.connecting")
        : booting
          ? t("autoResearch.arl.starting")
          : status === "connected"
            ? t("autoResearch.arl.connected")
            : status === "offline"
              ? t("autoResearch.arl.offline")
              : t("autoResearch.arl.disconnected")

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Connection strip: the panes render their own per-role status */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-4 py-1.5">
        <span
          className={cn(
            "size-2 rounded-full",
            connected && agentsReady
              ? "bg-emerald-500"
              : connecting || booting
                ? "animate-pulse bg-amber-500"
                : "bg-muted-foreground/40",
          )}
        />
        <span className="text-[12px] text-muted-foreground">{statusLabel}</span>
        {(connecting || booting) && (
          <Loader2 className="size-3 animate-spin text-muted-foreground/70" />
        )}
        {!connected && !connecting && (
          <WifiOff className="size-3 text-muted-foreground/50" />
        )}
        {systemMessage && (
          <span className="ml-1 min-w-0 truncate text-[12px] text-muted-foreground/70">
            · {systemMessage}
          </span>
        )}
        {/* Automatic retries are capped, so once they run out the only way
            back used to be a page refresh. Offer the retry inline instead. */}
        {offline && (
          <Button
            size="sm"
            variant="ghost"
            onClick={reconnect}
            className="ml-auto h-6 gap-1 px-1.5 text-[11.5px] font-normal"
          >
            <RotateCw className="size-3" />
            {t("autoResearch.arl.retryConnect")}
          </Button>
        )}
      </div>

      {/* Validator | Worker. `min-h-0` on the row and on each pane is what
          keeps the history regions scrolling instead of growing. */}
      <div className="flex min-h-0 flex-1">
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
            // Worker only: the Validator is the side that provides the
            // service, so the strip belongs to the side that consumes it.
            validation={role === "worker" ? validation : undefined}
            onSyncValidation={role === "worker" ? syncValidation : undefined}
          />
        ))}
      </div>
    </div>
  )
}
