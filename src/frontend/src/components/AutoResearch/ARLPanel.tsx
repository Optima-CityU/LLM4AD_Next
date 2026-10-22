import { Loader2, WifiOff } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { ResearchSessionItem } from "@/client"
import { ARL_ROLES, useARLWebSocket } from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

import ARLPane from "./ARLPane"

interface Props {
  session: ResearchSessionItem
}

/**
 * ARL session view: the Worker and Validator containers side by side.
 *
 * Each half is a self-contained chat page (history above, composer below)
 * rather than a shared event log, because the two agents run independent
 * conversations. A single WebSocket carries both; the backend stamps every
 * frame with a `role`, which is what routes events into the correct pane.
 */
export default function ARLPanel({ session }: Props) {
  const { t } = useTranslation()
  const {
    connected,
    connecting,
    panes,
    systemMessage,
    sendMessage,
    cancelTurn,
    setMode,
    respondPermission,
    respondPermissions,
  } = useARLWebSocket({ session })

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Connection strip: the panes render their own per-role status */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-4 py-1.5">
        <span
          className={cn(
            "size-2 rounded-full",
            connected
              ? "bg-emerald-500"
              : connecting
                ? "animate-pulse bg-amber-500"
                : "bg-muted-foreground/40",
          )}
        />
        <span className="text-[12px] text-muted-foreground">
          {connecting
            ? t("autoResearch.arl.connecting")
            : connected
              ? t("autoResearch.arl.connected")
              : t("autoResearch.arl.disconnected")}
        </span>
        {!connected && connecting && (
          <Loader2 className="size-3 animate-spin text-muted-foreground/70" />
        )}
        {!connected && !connecting && (
          <WifiOff className="size-3 text-muted-foreground/50" />
        )}
        {systemMessage && (
          <span className="ml-1 truncate text-[12px] text-muted-foreground/70">
            · {systemMessage}
          </span>
        )}
      </div>

      {/* Worker | Validator. `min-h-0` on the row and on each pane is what
          keeps the history regions scrolling instead of growing. */}
      <div className="flex min-h-0 flex-1">
        {ARL_ROLES.map((role) => (
          <ARLPane
            key={role}
            role={role}
            state={panes[role]}
            connected={connected}
            onSend={sendMessage}
            onCancel={cancelTurn}
            onSetMode={setMode}
            onPermission={respondPermission}
            onPermissions={respondPermissions}
          />
        ))}
      </div>
    </div>
  )
}
