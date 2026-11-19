import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  describeEvaluationMetric,
  type EvaluationMetricEntry,
  groupEvaluationMetrics,
} from "@/lib/evaluationMetrics"
import { cn } from "@/lib/utils"

function MetricTags({
  entries,
  primary = false,
  label: labelOverride,
}: {
  entries: EvaluationMetricEntry[]
  primary?: boolean
  label?: string
}) {
  const { t, i18n } = useTranslation()
  return (
    <dl className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(100%,8rem),1fr))] gap-1.5">
      {entries.map(([name, value]) => {
        const metric = describeEvaluationMetric(
          name,
          value,
          i18n.resolvedLanguage || "en",
        )
        const label =
          labelOverride ??
          t(`evaluationData.metricNames.${name}`, {
            defaultValue: metric.label,
          })
        return (
          <Tooltip key={name}>
            <TooltipTrigger asChild>
              <Badge
                asChild
                variant="outline"
                className={cn(
                  "w-full min-w-0 justify-between gap-2 rounded-md px-2 py-1.5 font-normal",
                  primary ? "border-primary/20 bg-primary/5" : "bg-muted/20",
                )}
              >
                {/* biome-ignore lint/a11y/noNoninteractiveTabindex: Static metrics need keyboard focus to reveal their full name and precision. */}
                <div tabIndex={0} data-node-metric={name}>
                  <dt className="min-w-0 truncate text-xs text-muted-foreground">
                    {label}
                  </dt>
                  <dd className="flex min-w-0 shrink-0 items-baseline gap-1 tabular-nums">
                    <span
                      className={cn(
                        "max-w-28 truncate text-sm font-semibold",
                        primary && "text-primary",
                      )}
                    >
                      {metric.display}
                    </span>
                    {metric.unit && (
                      <span className="text-[11px] text-muted-foreground">
                        {metric.unit}
                      </span>
                    )}
                  </dd>
                </div>
              </Badge>
            </TooltipTrigger>
            <TooltipContent
              side="left"
              className="flex max-w-[min(22rem,calc(100vw-2rem))] flex-col gap-1 break-words [overflow-wrap:anywhere]"
            >
              <span className="font-medium">{label}</span>
              <code className="text-xs">{name}</code>
              <span className="tabular-nums">
                {metric.exact}
                {metric.unit && ` ${metric.unit}`}
              </span>
            </TooltipContent>
          </Tooltip>
        )
      })}
    </dl>
  )
}

export default function NodeMetrics({
  result,
  evaluationScore,
}: {
  result: Record<string, unknown>
  evaluationScore?: number
}) {
  const { t } = useTranslation()
  const { primary, monitoring } = groupEvaluationMetrics(result)
  if (
    evaluationScore === undefined &&
    primary.length === 0 &&
    monitoring.length === 0
  )
    return null
  return (
    <section
      className="flex min-w-0 flex-col gap-2"
      aria-label={t("evaluationData.metrics")}
    >
      {evaluationScore !== undefined && (
        <MetricTags
          entries={[["score", evaluationScore]]}
          label={t("evaluationData.thisEvaluation")}
          primary
        />
      )}
      {primary.length > 0 && (
        <>
          <h4 className="text-sm font-medium">{t("evaluationData.metrics")}</h4>
          <MetricTags entries={primary} primary />
        </>
      )}
      {monitoring.length > 0 && (
        <details className="min-w-0" data-node-monitoring>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            {t("evaluationData.monitor_metrics")}
            <Badge
              variant="secondary"
              className="ml-1.5 px-1.5 py-0 text-[11px]"
            >
              {monitoring.length}
            </Badge>
          </summary>
          <div className="pt-2">
            <MetricTags entries={monitoring} />
          </div>
        </details>
      )}
    </section>
  )
}
