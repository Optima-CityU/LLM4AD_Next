import {
  Check,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Clock3,
  Loader2,
  PencilLine,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import type { TaskResponse } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Separator } from "@/components/ui/separator"
import { autoDesignProjectStatus } from "@/lib/autoDesignPresentation"
import { cn } from "@/lib/utils"

const checkIds = ["config", "dependencies", "sources", "evaluation"] as const
const checkStates = ["pending", "running", "passed", "failed"] as const

type Props = Parameters<typeof autoDesignProjectStatus>[0] & {
  task?: TaskResponse | null
  onRetry: () => void
}

export default function AutoDesignStatusBadge({
  task,
  onRetry,
  ...props
}: Props) {
  const { t } = useTranslation()
  const status = autoDesignProjectStatus(props)
  const label = t(`autodesign.buildState.${status}.label`)
  const busy = status === "running" || status === "loading"
  const Icon = busy
    ? Loader2
    : status === "ready"
      ? CircleCheck
      : status === "failed" || status === "unavailable"
        ? CircleAlert
        : status === "unsaved" || status === "changed"
          ? PencilLine
          : CircleDashed
  const checks = Array.isArray(props.report.checks)
    ? props.report.checks.filter(
        (check): check is Record<string, unknown> =>
          check !== null && typeof check === "object",
      )
    : []
  const showChecks =
    status !== "loading" && status !== "unavailable" && checks.length > 0
  const error =
    status === "failed" && typeof props.report.error === "string"
      ? props.report.error
      : null

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Badge
          asChild
          variant={
            status === "failed" || status === "unavailable"
              ? "destructive"
              : status === "ready"
                ? "default"
                : "outline"
          }
        >
          <button
            type="button"
            className="cursor-pointer hover:opacity-80"
            aria-label={`${t("autodesign.statusDetails.title")}: ${label}`}
          >
            <Icon
              aria-hidden="true"
              className={cn(busy && "animate-spin motion-reduce:animate-none")}
            />
            <span role="status" aria-live="polite">
              {label}
            </span>
            <ChevronDown aria-hidden="true" />
          </button>
        </Badge>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        collisionPadding={12}
        aria-label={t("autodesign.statusDetails.title")}
        className="flex max-h-[70dvh] w-80 max-w-[calc(100vw-1.5rem)] flex-col gap-4 overflow-y-auto"
      >
        <PopoverHeader>
          <PopoverTitle>{label}</PopoverTitle>
          <PopoverDescription>
            {t(`autodesign.buildState.${status}.hint`)}
          </PopoverDescription>
        </PopoverHeader>
        {showChecks && (
          <>
            <Separator />
            <section className="flex flex-col gap-3">
              <h3 className="text-sm font-medium">
                {t(
                  status === "changed" || status === "unsaved"
                    ? "autodesign.statusDetails.previousChecks"
                    : "autodesign.statusDetails.checks",
                )}
              </h3>
              <ul className="flex flex-col gap-3" aria-live="polite">
                {checkIds.map((id) => {
                  const check = checks.find((item) => item.id === id)
                  const state = checkStates.find(
                    (item) => item === check?.status,
                  )
                  const CheckIcon =
                    state === "passed"
                      ? Check
                      : state === "failed"
                        ? CircleAlert
                        : state === "running"
                          ? Loader2
                          : Clock3
                  return (
                    <li key={id} className="flex items-center gap-3 text-sm">
                      <CheckIcon
                        aria-hidden="true"
                        className={cn(
                          "size-4 shrink-0",
                          state === "running" &&
                            "animate-spin motion-reduce:animate-none",
                          state === "failed" && "text-destructive",
                          state === "passed" && "text-primary",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        {t(`autodesign.check.${id}`)}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {t(
                          `autodesign.statusDetails.checkState.${state ?? "unknown"}`,
                        )}
                      </span>
                    </li>
                  )
                })}
              </ul>
              {error && (
                <p className="whitespace-pre-wrap break-words text-sm text-destructive">
                  {error}
                </p>
              )}
            </section>
          </>
        )}
        {error && !showChecks && (
          <p className="whitespace-pre-wrap break-words text-sm text-destructive">
            {error}
          </p>
        )}
        <Separator />
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">
            {t("autodesign.statusDetails.task")}
          </span>
          <Badge
            variant={task?.status === "failed" ? "destructive" : "secondary"}
          >
            {status === "unavailable"
              ? t("autodesign.statusDetails.checkState.unknown")
              : status === "loading"
                ? t("common.loading")
                : task
                  ? t(`evolution.taskStatus.${task.status}`)
                  : t("autodesign.statusDetails.noTask")}
          </Badge>
        </div>
        {status === "unavailable" && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            {t("common.retry")}
          </Button>
        )}
      </PopoverContent>
    </Popover>
  )
}
