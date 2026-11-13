export type EvaluationMetricEntry = [string, unknown]

function entries(value: unknown): EvaluationMetricEntry[] {
  return value && typeof value === "object" && !Array.isArray(value)
    ? Object.entries(value)
    : []
}

export function groupEvaluationMetrics(result: Record<string, unknown>) {
  const metrics = entries(result.metrics)
  const monitors = entries(result.monitor_metrics)
  const same = (
    list: EvaluationMetricEntry[],
    [key, value]: EvaluationMetricEntry,
  ) =>
    list.some(
      ([otherKey, otherValue]) =>
        key === otherKey && Object.is(value, otherValue),
    )
  const objectives = metrics.filter((entry) => !same(monitors, entry))
  const primary = objectives.length > 0 ? objectives : metrics
  return {
    primary,
    monitoring: monitors.filter((entry) => !same(primary, entry)),
  }
}

export function describeEvaluationMetric(
  name: string,
  value: unknown,
  locale: string,
) {
  const numeric = typeof value === "number" && Number.isFinite(value)
  const units: Record<string, string> = {
    ns: "ns",
    us: "µs",
    ms: "ms",
    pct: "%",
    percent: "%",
    db: "dB",
    fps: "FPS",
    hz: "Hz",
    watt: "W",
  }
  const suffix = name.match(/_([^_]+)$/)?.[1]?.toLowerCase() ?? ""
  const unit = numeric ? (units[suffix] ?? "") : ""
  const stem = unit ? name.slice(0, -(suffix.length + 1)) : name
  const label = stem
    .replace(/([a-z\d])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/\b(cpu|gpu|fps|psnr|ssim|mse|rmse|mae|auc)\b/gi, (word) =>
      word.toUpperCase(),
    )
  let display: string
  if (numeric) {
    const magnitude = Math.abs(value)
    display = new Intl.NumberFormat(locale, {
      maximumSignificantDigits: 4,
      notation:
        magnitude >= 1e6 || (magnitude > 0 && magnitude < 0.001)
          ? "scientific"
          : "standard",
    }).format(Object.is(value, -0) ? 0 : value)
  } else if (value == null || typeof value === "number") {
    display = "—"
  } else {
    display =
      typeof value === "string"
        ? value
        : (JSON.stringify(value) ?? String(value))
  }
  return {
    label,
    unit,
    display,
    exact: typeof value === "object" ? JSON.stringify(value) : String(value),
  }
}
