import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link, redirect } from "@tanstack/react-router"
import axios from "axios"
import ReactECharts from "echarts-for-react"
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Eye,
  Fullscreen,
  GitFork,
  Github,
  Globe2,
  MessageSquareWarning,
  MonitorSmartphone,
  RefreshCw,
  Server,
  ShieldAlert,
  Signal,
  Star,
  Users,
  WalletCards,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { OpenAPI, UsersService } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  type DashboardOverview,
  formatCompactNumber,
  formatCurrencyValue,
  getGithubIssueEmptyMessage,
  getPlausibleStatus,
  getTaskStatusRows,
  normalizeDashboardOverview,
} from "@/lib/admin-analytics"

const RANGE_OPTIONS = ["7d", "30d", "91d"] as const

const SCREEN_CSS = `
.ops-screen {
  position: relative;
  min-height: 100%;
  overflow: hidden;
  border-radius: 8px;
  background:
    radial-gradient(circle at 18% 8%, rgba(34, 211, 238, .22), transparent 28%),
    radial-gradient(circle at 82% 18%, rgba(245, 158, 11, .16), transparent 24%),
    linear-gradient(135deg, #07111f 0%, #0b1728 42%, #111827 100%);
  color: #e5f4ff;
}
.ops-screen::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(125, 211, 252, .08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(125, 211, 252, .08) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: linear-gradient(to bottom, rgba(0, 0, 0, .9), rgba(0, 0, 0, .25));
}
.ops-screen::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: -35%;
  height: 32%;
  pointer-events: none;
  background: linear-gradient(to bottom, transparent, rgba(34, 211, 238, .16), transparent);
  animation: ops-scan 5.8s linear infinite;
}
.ops-panel {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(125, 211, 252, .22);
  background: linear-gradient(180deg, rgba(15, 23, 42, .78), rgba(15, 23, 42, .54));
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 18px 50px rgba(0, 0, 0, .24);
}
.ops-panel::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(120deg, transparent 0%, rgba(56, 189, 248, .10) 45%, transparent 65%);
  transform: translateX(-120%);
  animation: ops-sheen 7s ease-in-out infinite;
}
.ops-metric {
  border: 1px solid rgba(45, 212, 191, .28);
  background: linear-gradient(145deg, rgba(8, 47, 73, .72), rgba(15, 23, 42, .70));
  box-shadow: 0 0 0 1px rgba(255,255,255,.04), 0 16px 42px rgba(6, 182, 212, .10);
}
.ops-pulse {
  animation: ops-pulse 1.8s ease-in-out infinite;
}
.ops-marquee {
  animation: ops-marquee 22s linear infinite;
}
.ops-live-bar {
  position: relative;
  overflow: hidden;
}
.ops-live-bar::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.42), transparent);
  transform: translateX(-100%);
  animation: ops-flow 2.4s ease-in-out infinite;
}
.ops-data-pop {
  animation: ops-data-pop .65s ease-out both;
}
@keyframes ops-scan { from { transform: translateY(0); } to { transform: translateY(520%); } }
@keyframes ops-sheen { 0%, 40% { transform: translateX(-120%); } 65%, 100% { transform: translateX(120%); } }
@keyframes ops-pulse { 0%, 100% { opacity: .45; transform: scale(.96); } 50% { opacity: 1; transform: scale(1); } }
@keyframes ops-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes ops-flow { 0%, 35% { transform: translateX(-100%); } 75%, 100% { transform: translateX(100%); } }
@keyframes ops-data-pop { from { opacity: .65; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
`

function authHeaders() {
  const token = localStorage.getItem("access_token") || ""
  return token ? { Authorization: `Bearer ${token}` } : undefined
}

async function fetchDashboardOverview(range: string) {
  const response = await axios.get<DashboardOverview>(
    `${OpenAPI.BASE}/api/v1/admin/analytics/overview`,
    {
      headers: authHeaders(),
      params: { range },
    },
  )
  return normalizeDashboardOverview(response.data)
}

export const Route = createFileRoute("/_layout/analytics")({
  component: AnalyticsAdmin,
  beforeLoad: async () => {
    const user = await UsersService.readUserMe()
    if (!user.is_superuser) {
      throw redirect({ to: "/projects" })
    }
  },
  head: () => ({
    meta: [{ title: "Operations Analytics - LLM4AD_Next" }],
  }),
})

function AnalyticsAdmin() {
  const { t } = useTranslation()
  const [range, setRange] = useState<(typeof RANGE_OPTIONS)[number]>("30d")
  const [selectedFeedback, setSelectedFeedback] = useState<DashboardOverview["feedback"]["recent_items"][number] | null>(null)
  const screenRef = useRef<HTMLDivElement>(null)

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin", "analytics-overview", range],
    queryFn: () => fetchDashboardOverview(range),
    staleTime: 45 * 1000,
    refetchInterval: 60 * 1000,
  })

  const overview = useMemo(() => normalizeDashboardOverview(data), [data])
  const plausibleStatus = getPlausibleStatus(overview.plausible)
  const taskStatusRows = useMemo(
    () => getTaskStatusRows(overview.tasks.by_status),
    [overview.tasks.by_status],
  )
  const generatedAt = overview.generated_at
    ? new Date(overview.generated_at).toLocaleTimeString()
    : "-"
  const hasTaskTrend = overview.tasks.trend.length > 0
  const hasCountries = (overview.plausible.countries?.length ?? 0) > 0
  const hasDevices = (overview.plausible.devices?.length ?? 0) > 0
  const hasBrowsers = (overview.plausible.browsers?.length ?? 0) > 0
  const cityRows = (overview.plausible.cities ?? []).filter((item) =>
    isKnownDimensionName(item.name),
  )
  const deviceRows = (overview.plausible.devices ?? []).map((item) => ({
    ...item,
    name: formatDimensionName(item.name),
  }))

  const taskTrendOption = useMemo(
    () =>
      createTrendOption({
        labels: overview.tasks.trend.map((item) => item.date),
        series: [
          {
            name: t("adminAnalytics.dashboard.tasks"),
            data: overview.tasks.trend.map((item) => item.count),
            color: "#38bdf8",
          },
        ],
      }),
    [overview.tasks.trend, t],
  )

  const plausibleTrendOption = useMemo(
    () =>
      createTrendOption({
        labels: overview.plausible.trend?.map((item) => item.date) ?? [],
        series: [
          {
            name: "Visitors",
            data: overview.plausible.trend?.map((item) => item.visitors) ?? [],
            color: "#2dd4bf",
          },
          {
            name: "Pageviews",
            data: overview.plausible.trend?.map((item) => item.pageviews) ?? [],
            color: "#f59e0b",
          },
        ],
      }),
    [overview.plausible.trend],
  )

  const countryOption = useMemo(
    () => createHorizontalBarOption(overview.plausible.countries ?? [], "#22d3ee"),
    [overview.plausible.countries],
  )

  const browserOption = useMemo(
    () => createHorizontalBarOption(overview.plausible.browsers ?? [], "#a78bfa"),
    [overview.plausible.browsers],
  )

  const enterFullscreen = async () => {
    if (!screenRef.current || document.fullscreenElement) return
    await screenRef.current.requestFullscreen()
  }

  if (isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-24 w-full" />
        <div className="grid gap-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="h-40 w-full" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTriangle />
        <AlertTitle>{t("adminAnalytics.dashboard.loadFailed")}</AlertTitle>
        <AlertDescription>{String(error)}</AlertDescription>
      </Alert>
    )
  }

  return (
    <div ref={screenRef} className="ops-screen p-4 md:p-5">
      <style>{SCREEN_CSS}</style>
      <div className="relative z-10 flex flex-col gap-4">
        <header className="ops-panel rounded-md px-4 py-3">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex size-10 items-center justify-center rounded-md border border-cyan-300/30 bg-cyan-400/10">
                  <Signal className="size-5 text-cyan-200" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold tracking-wide text-cyan-50">
                    {t("adminAnalytics.dashboard.title")}
                  </h2>
                  <p className="mt-1 text-xs text-cyan-100/70">
                    {t("adminAnalytics.dashboard.subtitle")}
                  </p>
                </div>
                <div className="ml-0 flex items-center gap-2 rounded-full border border-emerald-300/30 bg-emerald-400/10 px-3 py-1 text-xs text-emerald-100 xl:ml-4">
                  <span className="ops-pulse size-2 rounded-full bg-emerald-300" />
                  LIVE DATA
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <div className="flex rounded-md border border-cyan-300/20 bg-slate-950/40 p-1">
                {RANGE_OPTIONS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setRange(option)}
                    className={`h-8 rounded px-3 text-xs font-medium transition-colors ${
                      option === range
                        ? "bg-cyan-300 text-slate-950 shadow-[0_0_18px_rgba(103,232,249,.45)]"
                        : "text-cyan-100/70 hover:text-cyan-50"
                    }`}
                  >
                    {option}
                  </button>
                ))}
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => refetch()}
                className="border-cyan-300/25 bg-slate-950/30 text-cyan-50 hover:bg-cyan-300/10"
              >
                <RefreshCw data-icon="inline-start" className={isFetching ? "animate-spin" : ""} />
                {t("common.refresh")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={enterFullscreen}
                className="border-cyan-300/25 bg-slate-950/30 text-cyan-50 hover:bg-cyan-300/10"
              >
                <Fullscreen data-icon="inline-start" />
                {t("adminAnalytics.dashboard.fullscreen")}
              </Button>
            </div>
          </div>
          <Ticker
            items={[
              `Last sync ${generatedAt}`,
              `Plausible ${plausibleStatus.label}`,
              `${t("adminAnalytics.dashboard.githubStars")} ${formatCompactNumber(overview.github.stars)}`,
              `${t("adminAnalytics.dashboard.totalSpend")} ${formatCurrencyValue(overview.litellm.total_spend)}`,
            ]}
          />
        </header>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricTile
            icon={Users}
            label={t("adminAnalytics.dashboard.totalUsers")}
            value={formatCompactNumber(overview.users.total)}
            detail={`${overview.users.active} ${t("adminAnalytics.dashboard.active")} / ${overview.users.email_verified} ${t("adminAnalytics.dashboard.verified")}`}
            tone="cyan"
          />
          <MetricTile
            icon={Activity}
            label={t("adminAnalytics.dashboard.totalTasks")}
            value={formatCompactNumber(overview.tasks.total)}
            detail={`${overview.tasks.by_status.running ?? 0} ${t("adminAnalytics.dashboard.statusLabels.running")} / ${overview.tasks.by_status.failed ?? 0} ${t("adminAnalytics.dashboard.statusLabels.failed")}`}
            tone="amber"
          />
          <MetricTile
            icon={WalletCards}
            label={t("adminAnalytics.dashboard.totalSpend")}
            value={formatCurrencyValue(overview.litellm.total_spend)}
            detail={`${overview.litellm.over_budget_users} ${t("adminAnalytics.dashboard.overBudget")} / ${overview.litellm.near_limit_users} ${t("adminAnalytics.dashboard.nearLimit")}`}
            tone="emerald"
          />
          <MetricTile
            icon={Github}
            label={t("adminAnalytics.dashboard.githubStars")}
            value={formatCompactNumber(overview.github.stars)}
            detail={`${overview.github.open_issues ?? 0} ${t("adminAnalytics.dashboard.issues")} / ${overview.github.forks ?? 0} ${t("adminAnalytics.dashboard.forks")}`}
            tone="violet"
          />
        </section>

        <section className="grid items-start gap-3 xl:grid-cols-[minmax(0,.9fr)_minmax(0,1.1fr)]">
          <Panel title={t("adminAnalytics.dashboard.topUsers")} icon={Users}>
            <RankList
              compact
              items={overview.tasks.top_users.map((item) => ({
                label: item.name,
                sublabel: item.email || "",
                value: `${item.tasks} ${t("adminAnalytics.dashboard.tasks")}`,
              }))}
              empty={t("adminAnalytics.dashboard.noData")}
            />
          </Panel>
          <Panel title={t("adminAnalytics.dashboard.github")} icon={Github}>
            <CompactGithub
              github={overview.github}
              empty={getGithubIssueEmptyMessage(
                overview.github,
                t("adminAnalytics.dashboard.noOpenIssues"),
              )}
            />
          </Panel>
        </section>

        <section className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,.55fr)]">
          <div className="grid gap-3">
            <Panel title={t("adminAnalytics.dashboard.taskTrend")} icon={BarChart3} right={<Badge className="bg-cyan-300/15 text-cyan-100">{overview.range}</Badge>}>
              {hasTaskTrend ? (
                <div className="h-72">
                  <ReactECharts option={taskTrendOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "canvas" }} notMerge />
                </div>
              ) : (
                <EmptyPanelMessage message={t("adminAnalytics.dashboard.noData")} />
              )}
            </Panel>
            <Panel title={t("adminAnalytics.dashboard.feedback")} icon={MessageSquareWarning}>
              <FeedbackFeed
                items={overview.feedback.recent_items}
                empty={t("adminAnalytics.dashboard.noData")}
                onSelect={setSelectedFeedback}
                translate={(key) => t(key)}
              />
            </Panel>
          </div>

          <div className="grid gap-3">
            <Panel title={t("adminAnalytics.dashboard.realtimeTaskStatus")} icon={Activity}>
              <LiveTaskStatus
                rows={taskStatusRows}
                total={overview.tasks.total}
                empty={t("adminAnalytics.dashboard.noData")}
                translate={(key) => t(key)}
              />
            </Panel>
            <Panel title={t("adminAnalytics.dashboard.system")} icon={Server}>
              <div className="grid grid-cols-2 gap-2">
                <MiniStat compact label={t("adminAnalytics.dashboard.projects")} value={formatCompactNumber(overview.projects.total)} />
                <MiniStat compact label={t("adminAnalytics.dashboard.providers")} value={formatCompactNumber(overview.providers.total)} />
                <MiniStat compact label={t("adminAnalytics.dashboard.verified")} value={formatCompactNumber(overview.users.email_verified)} />
                <MiniStat compact label={t("adminAnalytics.dashboard.builtinProviders")} value={formatCompactNumber(overview.providers.builtin)} />
              </div>
            </Panel>
          </div>
        </section>

        <section className="grid gap-3 xl:grid-cols-[1.15fr_.85fr]">
          <Panel
            title={t("adminAnalytics.dashboard.plausible")}
            icon={Eye}
            right={<StatusBadge tone={plausibleStatus.tone} label={plausibleStatus.label} />}
          >
            <div className="grid gap-3">
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-6">
                {[
                  "visitors",
                  "visits",
                  "pageviews",
                  "views_per_visit",
                  "bounce_rate",
                  "visit_duration",
                ].map((key) => (
                  <MiniStat
                    compact
                    key={key}
                    label={key.replace(/_/g, " ")}
                    value={
                      key === "bounce_rate"
                        ? `${overview.plausible.metrics?.[key] ?? 0}%`
                        : formatCompactNumber(overview.plausible.metrics?.[key])
                    }
                  />
                ))}
              </div>
              {overview.plausible.available ? (
                <div className="h-52">
                  <ReactECharts option={plausibleTrendOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "canvas" }} notMerge />
                </div>
              ) : (
                <PlausibleConfigNotice overview={overview} status={plausibleStatus} />
              )}
            </div>
          </Panel>

          <Panel title={t("adminAnalytics.dashboard.quotaUsers")} icon={WalletCards}>
            <RankList
              compact
              items={overview.litellm.top_users.map((item) => ({
                label: item.name,
                sublabel: item.email || "",
                value: formatCurrencyValue(item.spend),
              }))}
              empty={overview.litellm.message || t("adminAnalytics.dashboard.noData")}
            />
          </Panel>
        </section>

        <section className="grid gap-3 xl:grid-cols-3">
          <Panel title={t("adminAnalytics.dashboard.geoTraffic")} icon={Globe2}>
            <div className="grid gap-3">
              {hasCountries ? (
                <div className="h-44">
                  <ReactECharts option={countryOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "canvas" }} notMerge />
                </div>
              ) : (
                <EmptyPanelMessage message={t("adminAnalytics.dashboard.noData")} />
              )}
              <RankList
                compact
                items={cityRows.slice(0, 4).map((item) => ({
                  label: item.name,
                  sublabel: "City",
                  value: formatCompactNumber(item.value),
                }))}
                empty={t("adminAnalytics.dashboard.noData")}
              />
            </div>
          </Panel>

          <Panel title={t("adminAnalytics.dashboard.devices")} icon={MonitorSmartphone}>
            {hasDevices ? (
              <DistributionList rows={deviceRows} />
            ) : (
              <EmptyPanelMessage message={t("adminAnalytics.dashboard.noData")} />
            )}
          </Panel>

          <Panel title={t("adminAnalytics.dashboard.browsers")} icon={Server}>
            <div className="grid gap-3">
              {hasBrowsers ? (
                <div className="h-40">
                  <ReactECharts option={browserOption} style={{ height: "100%", width: "100%" }} opts={{ renderer: "canvas" }} notMerge />
                </div>
              ) : (
                <EmptyPanelMessage message={t("adminAnalytics.dashboard.noData")} />
              )}
              <RankList
                compact
                items={(overview.plausible.operating_systems ?? []).slice(0, 5).map((item) => ({
                  label: item.name,
                  sublabel: "OS",
                  value: formatCompactNumber(item.value),
                }))}
                empty={t("adminAnalytics.dashboard.noData")}
              />
            </div>
          </Panel>
        </section>
      </div>
      <FeedbackDetailDialog
        feedback={selectedFeedback}
        open={selectedFeedback !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedFeedback(null)
        }}
        translate={(key) => t(key)}
      />
    </div>
  )
}

function EmptyPanelMessage({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed border-cyan-300/20 bg-slate-950/30 px-4 py-3 text-sm text-cyan-100/60">
      {message}
    </div>
  )
}

function MetricTile({
  icon: Icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: LucideIcon
  label: string
  value: string
  detail: string
  tone: "cyan" | "amber" | "emerald" | "violet"
}) {
  const toneClasses = {
    cyan: "text-cyan-200 bg-cyan-300/10 border-cyan-300/30",
    amber: "text-amber-200 bg-amber-300/10 border-amber-300/30",
    emerald: "text-emerald-200 bg-emerald-300/10 border-emerald-300/30",
    violet: "text-violet-200 bg-violet-300/10 border-violet-300/30",
  }[tone]
  return (
    <div className="ops-metric rounded-md p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[.18em] text-cyan-100/60">{label}</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums text-white">{value}</p>
          <p className="mt-1 truncate text-xs text-slate-300/70">{detail}</p>
        </div>
        <div className={`flex size-10 shrink-0 items-center justify-center rounded-md border ${toneClasses}`}>
          <Icon className="size-5" />
        </div>
      </div>
    </div>
  )
}

function Panel({
  title,
  icon: Icon,
  right,
  children,
}: {
  title: string
  icon: LucideIcon
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="ops-panel min-w-0 rounded-md p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="size-4 text-cyan-200" />
          <h3 className="truncate text-sm font-semibold uppercase tracking-[.14em] text-cyan-50">{title}</h3>
        </div>
        {right}
      </div>
      {children}
    </div>
  )
}

function MiniStat({
  label,
  value,
  icon: Icon = Activity,
  compact = false,
}: {
  label: string
  value: string
  icon?: LucideIcon
  compact?: boolean
}) {
  return (
    <div className={`rounded-md border border-cyan-300/15 bg-slate-950/35 ${compact ? "p-2.5" : "p-3"}`}>
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-[.12em] text-cyan-100/60">
        <Icon className="size-3.5" />
        <span className="truncate">{label}</span>
      </div>
      <p className={`ops-data-pop mt-2 font-semibold tabular-nums text-cyan-50 ${compact ? "text-base" : "text-lg"}`}>{value}</p>
    </div>
  )
}

function CompactGithub({
  github,
  empty,
}: {
  github: DashboardOverview["github"]
  empty: string
}) {
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-3 gap-2">
        <MiniStat compact label="Stars" value={formatCompactNumber(github.stars)} icon={Star} />
        <MiniStat compact label="Forks" value={formatCompactNumber(github.forks)} icon={GitFork} />
        <MiniStat compact label="Issues" value={formatCompactNumber(github.open_issues)} icon={ShieldAlert} />
      </div>
      <RankList
        compact
        items={(github.recent_issues ?? []).slice(0, 3).map((issue) => ({
          label: `#${issue.number} ${issue.title}`,
          sublabel: issue.updated_at ? formatDate(issue.updated_at) : github.repository || "",
          value: "open",
          href: issue.url,
        }))}
        empty={empty}
      />
    </div>
  )
}

function LiveTaskStatus({
  rows,
  total,
  empty,
  translate,
}: {
  rows: ReturnType<typeof getTaskStatusRows>
  total: number
  empty: string
  translate: (key: string) => string
}) {
  if (!rows.length) {
    return (
      <div className="rounded-md border border-dashed border-cyan-300/20 bg-slate-950/30 p-4 text-sm text-cyan-100/60">
        {empty}
      </div>
    )
  }
  const max = Math.max(...rows.map((item) => item.value), 1)
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <MiniStat compact label={translate("adminAnalytics.dashboard.totalTasks")} value={formatCompactNumber(total)} />
        <MiniStat
          compact
          label={translate("adminAnalytics.dashboard.liveTasks")}
          value={formatCompactNumber(rows.filter((item) => item.active).reduce((sum, item) => sum + item.value, 0))}
        />
      </div>
      <div className="space-y-2">
        {rows.map((row) => {
          const tone = statusTone(row.key)
          const width = `${Math.max((row.value / max) * 100, row.value ? 6 : 0)}%`
          return (
            <div
              key={row.key}
              className="rounded-md border border-cyan-300/15 bg-slate-950/35 px-3 py-2"
            >
              <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
                <div className="flex min-w-0 items-center gap-2">
                  <span className={`size-2 rounded-full ${tone.dot} ${row.active ? "ops-pulse" : ""}`} />
                  <span className="truncate font-medium text-cyan-50">{translate(row.labelKey)}</span>
                </div>
                <span className="font-semibold tabular-nums text-cyan-50">{formatCompactNumber(row.value)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-800/80">
                <div
                  className={`h-full rounded-full ${tone.bar} ${row.active ? "ops-live-bar" : ""}`}
                  style={{ width }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function statusTone(key: string) {
  if (key === "running") return { dot: "bg-cyan-300", bar: "bg-cyan-300 shadow-[0_0_14px_rgba(103,232,249,.55)]" }
  if (key === "pending") return { dot: "bg-amber-300", bar: "bg-amber-300 shadow-[0_0_14px_rgba(252,211,77,.45)]" }
  if (key === "completed") return { dot: "bg-emerald-300", bar: "bg-emerald-300 shadow-[0_0_14px_rgba(110,231,183,.45)]" }
  if (key === "failed") return { dot: "bg-rose-300", bar: "bg-rose-300 shadow-[0_0_14px_rgba(253,164,175,.45)]" }
  return { dot: "bg-slate-300", bar: "bg-slate-300" }
}

function DistributionList({ rows }: { rows: Array<{ name: string; value: number }> }) {
  const total = rows.reduce((sum, item) => sum + item.value, 0) || 1
  const max = Math.max(...rows.map((item) => item.value), 1)
  return (
    <div className="space-y-2">
      {rows.slice(0, 6).map((item, index) => {
        const percent = (item.value / total) * 100
        const width = `${Math.max((item.value / max) * 100, item.value ? 5 : 0)}%`
        return (
          <div
            key={`${item.name}-${index}`}
            className="rounded-md border border-cyan-300/15 bg-slate-950/30 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="truncate font-medium text-cyan-50">{item.name}</span>
              <span className="shrink-0 tabular-nums text-cyan-100/70">
                {formatCompactNumber(item.value)} · {percent.toFixed(1)}%
              </span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800/80">
              <div
                className="ops-live-bar h-full rounded-full bg-cyan-300 shadow-[0_0_14px_rgba(103,232,249,.45)]"
                style={{ width }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function RankList({
  items,
  empty,
  compact = false,
}: {
  items: Array<{ label: string; sublabel: string; value: string; href?: string }>
  empty: string
  compact?: boolean
}) {
  if (!items.length) {
    return (
      <div className="rounded-md border border-dashed border-cyan-300/20 bg-slate-950/30 p-4 text-sm text-cyan-100/60">
        {empty}
      </div>
    )
  }
  return (
    <div className={compact ? "space-y-1.5" : "space-y-2"}>
      {items.slice(0, compact ? 6 : 5).map((item, index) => {
        const content = (
          <>
            <div className="flex size-6 shrink-0 items-center justify-center rounded bg-cyan-300/15 text-xs text-cyan-100">
              {index + 1}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-cyan-50">{item.label}</p>
              <p className="truncate text-xs text-cyan-100/45">{item.sublabel}</p>
            </div>
            <Badge className="border-cyan-300/20 bg-cyan-300/10 text-cyan-50">{item.value}</Badge>
          </>
        )
        const className =
          "flex items-center gap-3 rounded-md border border-cyan-300/15 bg-slate-950/30 px-3 py-2 transition-colors hover:bg-cyan-300/10"
        return item.href ? (
          <a key={`${item.label}-${index}`} href={item.href} target="_blank" rel="noreferrer" className={className}>
            {content}
          </a>
        ) : (
          <div key={`${item.label}-${index}`} className={className}>
            {content}
          </div>
        )
      })}
    </div>
  )
}

function FeedbackFeed({
  items,
  empty,
  compact = false,
  onSelect,
  translate,
}: {
  items: DashboardOverview["feedback"]["recent_items"]
  empty: string
  compact?: boolean
  onSelect?: (item: DashboardOverview["feedback"]["recent_items"][number]) => void
  translate: (key: string) => string
}) {
  if (!items.length) {
    return (
      <div className="rounded-md border border-dashed border-cyan-300/20 bg-slate-950/30 p-4 text-sm text-cyan-100/60">
        {empty}
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {items.slice(0, compact ? 3 : 6).map((item, index) => (
        <button
          type="button"
          key={`${item.id || item.title}-${index}`}
          onClick={() => onSelect?.(item)}
          className="grid w-full grid-cols-[1fr_auto] gap-3 rounded-md border border-cyan-300/15 bg-slate-950/30 px-3 py-2 text-left transition-colors hover:bg-cyan-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-cyan-50">{item.title}</p>
            <p className="truncate text-xs text-cyan-100/45">
              {item.user_full_name || item.user_email || "Anonymous"} | {formatDate(item.created_time)}
            </p>
            {item.content && (
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-cyan-100/60">
                {item.content}
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <Badge className="bg-amber-300/15 text-amber-100">{translate(`feedback.list.priority.${item.priority}`)}</Badge>
            <Badge className="bg-cyan-300/15 text-cyan-100">{translate(`feedback.list.status.${item.status}`)}</Badge>
            <span className="text-[10px] uppercase tracking-[.12em] text-cyan-100/40">
              {translate(`feedback.submit.typeOptions.${item.type || "other"}`)}
            </span>
          </div>
        </button>
      ))}
    </div>
  )
}

function FeedbackDetailDialog({
  feedback,
  open,
  onOpenChange,
  translate,
}: {
  feedback: DashboardOverview["feedback"]["recent_items"][number] | null
  open: boolean
  onOpenChange: (open: boolean) => void
  translate: (key: string) => string
}) {
  if (!feedback) return null
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl border-cyan-300/25 bg-slate-950 text-cyan-50">
        <DialogHeader>
          <DialogTitle className="pr-8 text-cyan-50">{feedback.title}</DialogTitle>
          <DialogDescription className="text-cyan-100/55">
            {feedback.user_full_name || feedback.user_email || "Anonymous"} | {formatDate(feedback.created_time)}
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[68vh] pr-3">
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-2">
              <Badge className="bg-cyan-300/15 text-cyan-100">
                {translate(`feedback.submit.typeOptions.${feedback.type || "other"}`)}
              </Badge>
              <Badge className="bg-amber-300/15 text-amber-100">
                {translate(`feedback.list.priority.${feedback.priority}`)}
              </Badge>
              <Badge className="bg-emerald-300/15 text-emerald-100">
                {translate(`feedback.list.status.${feedback.status}`)}
              </Badge>
            </div>

            <DetailField label={translate("adminAnalytics.dashboard.feedbackContent")}>
              <p className="whitespace-pre-wrap leading-6 text-cyan-50/90">
                {feedback.content || "-"}
              </p>
            </DetailField>

            <div className="grid gap-3 md:grid-cols-2">
              <DetailField label={translate("adminAnalytics.dashboard.feedbackContact")}>
                {feedback.contact_email || feedback.user_email || "-"}
              </DetailField>
              <DetailField label={translate("adminAnalytics.dashboard.feedbackPage")}>
                {feedback.page_url ? (
                  <a href={feedback.page_url} target="_blank" rel="noreferrer" className="break-all text-cyan-200 hover:text-cyan-100">
                    {feedback.page_url}
                  </a>
                ) : "-"}
              </DetailField>
              <DetailField label={translate("adminAnalytics.dashboard.feedbackBrowser")}>
                {feedback.browser_info || "-"}
              </DetailField>
              <DetailField label={translate("adminAnalytics.dashboard.feedbackTags")}>
                {feedback.tags || "-"}
              </DetailField>
            </div>

            {feedback.admin_reply && (
              <DetailField label={translate("adminAnalytics.dashboard.feedbackReply")}>
                <p className="whitespace-pre-wrap leading-6">{feedback.admin_reply}</p>
              </DetailField>
            )}

            <div className="flex justify-end">
              <Button asChild className="bg-cyan-300 text-slate-950 hover:bg-cyan-200">
                <Link to="/feedback">{translate("adminAnalytics.dashboard.openFeedback")}</Link>
              </Button>
            </div>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

function DetailField({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="rounded-md border border-cyan-300/15 bg-slate-900/70 p-3">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-[.14em] text-cyan-100/45">{label}</p>
      <div className="text-sm text-cyan-50/80">{children}</div>
    </div>
  )
}

function PlausibleConfigNotice({
  overview,
  status,
}: {
  overview: DashboardOverview
  status: ReturnType<typeof getPlausibleStatus>
}) {
  return (
    <div className="rounded-md border border-dashed border-amber-300/30 bg-amber-300/10 p-4">
      <p className="text-sm font-medium text-amber-100">{status.label}</p>
      <p className="mt-1 text-sm text-amber-50/70">{status.message}</p>
      <p className="mt-3 text-xs text-amber-50/50">
        API base: {overview.plausible.api_base_url || "-"} | Site: {overview.plausible.site_id || "-"}
      </p>
    </div>
  )
}

function StatusBadge({ tone, label }: { tone: string; label: string }) {
  const className =
    tone === "ready"
      ? "bg-emerald-300/15 text-emerald-100 border-emerald-300/25"
      : "bg-amber-300/15 text-amber-100 border-amber-300/25"
  return <Badge className={className}>{label}</Badge>
}

function Ticker({ items }: { items: string[] }) {
  const doubled = [...items, ...items]
  return (
    <div className="mt-3 overflow-hidden border-t border-cyan-300/15 pt-2 text-xs text-cyan-100/60">
      <div className="ops-marquee flex w-max gap-8">
        {doubled.map((item, index) => (
          <span key={`${item}-${index}`} className="whitespace-nowrap">
            <span className="mr-2 text-cyan-300">*</span>
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}

function createTrendOption({
  labels,
  series,
}: {
  labels: string[]
  series: Array<{ name: string; data: number[]; color: string }>
}) {
  return {
    backgroundColor: "transparent",
    animationDurationUpdate: 700,
    animationEasingUpdate: "cubicOut",
    grid: { left: 40, right: 16, top: 28, bottom: 28 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 8, textStyle: { color: "#bae6fd" } },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: "rgba(186,230,253,.62)" },
      axisLine: { lineStyle: { color: "rgba(125,211,252,.24)" } },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(125,211,252,.14)" } },
      axisLabel: { color: "rgba(186,230,253,.62)" },
    },
    series: series.map((item) => ({
      name: item.name,
      type: "line",
      smooth: true,
      showSymbol: true,
      symbolSize: 5,
      data: item.data,
      lineStyle: { width: 3, color: item.color },
      itemStyle: { color: item.color },
      areaStyle: { color: `${item.color}22` },
    })),
  }
}

function createHorizontalBarOption(rows: Array<{ name: string; value: number }>, color: string) {
  const data = rows.slice(0, 8).reverse()
  return {
    backgroundColor: "transparent",
    grid: { left: 92, right: 20, top: 8, bottom: 12 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(125,211,252,.12)" } },
      axisLabel: { color: "rgba(186,230,253,.62)" },
    },
    yAxis: {
      type: "category",
      data: data.map((item) => item.name),
      axisLabel: { color: "rgba(186,230,253,.72)", width: 84, overflow: "truncate" },
    },
    series: [
      {
        type: "bar",
        data: data.map((item) => item.value),
        itemStyle: { color, borderRadius: [0, 4, 4, 0] },
      },
    ],
  }
}

function isKnownDimensionName(value?: string | null) {
  if (!value) return false
  const normalized = value.trim().toLowerCase()
  return normalized !== "(not set)" && normalized !== "unknown"
}

function formatDimensionName(value?: string | null) {
  return isKnownDimensionName(value) ? value?.trim() || "Unknown" : "Unknown"
}

function formatDate(value?: string | null) {
  if (!value) return "-"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}
