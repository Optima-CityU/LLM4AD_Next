import {
  AlertTriangle,
  ChevronRight,
  Loader2,
  SquareTerminal,
} from "lucide-react"
import { memo, useState } from "react"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

import {
  type CollabToolCall,
  CollabToolCard,
  summarizeCollabToolInput,
} from "./CollabToolCard"

/** A compact, expandable summary of adjacent calls to one tool. */
function CollabToolGroupInner({ calls }: { calls: CollabToolCall[] }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const failedCount = calls.filter(
    (call) => call.done && !!call.state && call.state !== "success",
  ).length
  const running = calls.some((call) => !call.done)
  const preview = calls
    .slice(0, 2)
    .map((call) => summarizeCollabToolInput(call.input, call.inputRaw))
    .filter(Boolean)
    .join(" · ")

  return (
    <div
      className={cn(
        "min-w-0 overflow-hidden rounded-lg border text-xs",
        failedCount
          ? "border-destructive/40 bg-destructive/5"
          : "border-border/50 bg-background/50",
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full min-w-0 items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-muted/40"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        {running ? (
          <Loader2
            aria-hidden
            className="size-3.5 shrink-0 animate-spin text-primary"
          />
        ) : failedCount ? (
          <AlertTriangle
            aria-hidden
            className="size-3.5 shrink-0 text-destructive"
          />
        ) : (
          <SquareTerminal
            aria-hidden
            className="size-3.5 shrink-0 text-primary"
          />
        )}
        <span className="min-w-0 truncate font-mono font-medium text-foreground">
          {calls[0].name}
        </span>
        <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          ×{calls.length}
        </span>
        {preview && (
          <span className="hidden min-w-0 flex-1 truncate text-muted-foreground sm:block">
            {preview}
          </span>
        )}
        {failedCount > 0 && (
          <span className="shrink-0 text-destructive">
            {t("autoResearch.collab.toolGroupFailed", { count: failedCount })}
          </span>
        )}
        {running && (
          <span className="ml-auto shrink-0 text-primary">
            {t("autoResearch.collab.toolGroupRunning")}
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-1 border-t border-border/40 p-1.5">
          {calls.map((call) => (
            <CollabToolCard key={call.id} call={call} />
          ))}
        </div>
      )}
    </div>
  )
}

export const CollabToolGroup = memo(CollabToolGroupInner)
