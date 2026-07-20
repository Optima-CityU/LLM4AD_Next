import { useTranslation } from "react-i18next"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useEvolution } from "@/hooks/useEvolution"

/**
 * Live indicator: shows the SSE / log-stream health for the currently active
 * task. Hidden for terminal tasks where there's no stream to monitor.
 */
export default function ConnectionStatus() {
  const { t } = useTranslation()
  const { effectiveStatus, logError, logLoading } = useEvolution()

  const isActive =
    effectiveStatus === "pending" || effectiveStatus === "running"
  if (!isActive) return null

  let color: string
  let label: string
  if (logError && logLoading) {
    color = "#f59e0b"
    label = t("evolution.connection.reconnecting", {
      defaultValue: "连接断开，正在重连…",
    })
  } else if (logError) {
    color = "#ef4444"
    label = t("evolution.connection.disconnected", {
      defaultValue: "连接断开",
    })
  } else if (logLoading) {
    color = "#f59e0b"
    label = t("evolution.connection.connecting", {
      defaultValue: "连接中…",
    })
  } else {
    color = "#10b981"
    label = t("evolution.connection.live", {
      defaultValue: "实时同步中",
    })
  }

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-md cursor-default"
            aria-label={label}
          >
            <span className="relative inline-flex size-2">
              <span
                className="absolute inset-0 rounded-full opacity-50 animate-ping"
                style={{ backgroundColor: color }}
              />
              <span
                className="relative inline-flex size-2 rounded-full"
                style={{ backgroundColor: color }}
              />
            </span>
          </div>
        </TooltipTrigger>
        <TooltipContent side="bottom">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
