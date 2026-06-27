import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link, redirect } from "@tanstack/react-router"
import axios from "axios"
import { geoCentroid } from "d3"
import "echarts-gl"
import ReactECharts from "echarts-for-react"
import type { EChartsReactProps } from "echarts-for-react"
import type { Feature, FeatureCollection, Geometry } from "geojson"
import { alpha2ToNumeric } from "i18n-iso-countries"
import type { LucideIcon } from "lucide-react"
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  Eye,
  Fullscreen,
  Github,
  Globe2,
  LayoutDashboard,
  MessageSquareWarning,
  Minimize2,
  MonitorSmartphone,
  RefreshCw,
  Server,
  ShieldAlert,
  Users,
  WalletCards,
} from "lucide-react"
import type { ReactNode } from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { feature } from "topojson-client"
import type { GeometryCollection, Topology } from "topojson-specification"
import worldCountries from "world-atlas/countries-110m.json"

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  type DashboardOverview,
  formatCompactNumber,
  formatCurrencyValue,
  getGithubIssueEmptyMessage,
  getPlausibleStatus,
  getTaskStatusRows,
  normalizeOperationsFeedback,
  normalizeOperationsLiteLLM,
  normalizeOperationsSummary,
  normalizeOperationsTasks,
  normalizeVisitorsGithub,
  normalizeVisitorsPlausible,
  type OperationsFeedback,
  type OperationsLiteLLM,
  type OperationsSummary,
  type OperationsTasks,
  type VisitorsGithub,
  type VisitorsPlausible,
} from "@/lib/admin-analytics"

const RANGE_OPTIONS = ["7d", "30d", "91d"] as const
const SCREEN_TABS = ["operations", "visitors"] as const
const REFRESH_OPTIONS = [
  { value: "off", ms: false },
  { value: "30s", ms: 30_000 },
  { value: "1m", ms: 60_000 },
  { value: "5m", ms: 5 * 60_000 },
  { value: "15m", ms: 15 * 60_000 },
] as const

type DashboardTone = "blue" | "emerald" | "amber" | "rose"

const DASHBOARD_TONE_STYLES: Record<
  DashboardTone,
  {
    panel: string
    icon: string
    metric: string
    pill: string
  }
> = {
  blue: {
    panel: "border-l-4 border-l-blue-500",
    icon: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    metric: "text-blue-700 dark:text-blue-300",
    pill: "border-blue-500/25 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  },
  emerald: {
    panel: "border-l-4 border-l-emerald-500",
    icon: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    metric: "text-emerald-700 dark:text-emerald-300",
    pill: "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
  amber: {
    panel: "border-l-4 border-l-amber-500",
    icon: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    metric: "text-amber-700 dark:text-amber-300",
    pill: "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
  rose: {
    panel: "border-l-4 border-l-rose-500",
    icon: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
    metric: "text-rose-700 dark:text-rose-300",
    pill: "border-rose-500/25 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  },
}

const ANALYTICS_AUTOSCROLL_CSS = `
.admin-analytics-autoscroll {
  scrollbar-width: none;
}
.admin-analytics-autoscroll::-webkit-scrollbar {
  display: none;
}
.admin-analytics-autoscroll-track[data-animated="true"] {
  animation: analytics-list-loop var(--analytics-scroll-duration, 28s) linear infinite;
  will-change: transform;
}
.admin-analytics-autoscroll:hover .admin-analytics-autoscroll-track[data-animated="true"],
.admin-analytics-autoscroll:focus-within .admin-analytics-autoscroll-track[data-animated="true"] {
  animation-play-state: paused;
}
@keyframes analytics-list-loop {
  from {
    transform: translateY(0);
  }
  to {
    transform: translateY(calc(var(--analytics-scroll-distance, 0px) * -1));
  }
}
@media (prefers-reduced-motion: reduce) {
  .admin-analytics-autoscroll {
    scrollbar-width: thin;
  }
  .admin-analytics-autoscroll-track[data-animated="true"] {
    animation: none;
    transform: none;
  }
}
`

type ScreenTab = (typeof SCREEN_TABS)[number]
type RefreshValue = (typeof REFRESH_OPTIONS)[number]["value"]
type AnalyticsModule =
  | "summary"
  | "tasks"
  | "feedback"
  | "litellm"
  | "plausible"
  | "github"

const DEFAULT_REFRESH: Record<AnalyticsModule, RefreshValue> = {
  summary: "5m",
  tasks: "30s",
  feedback: "5m",
  litellm: "5m",
  plausible: "5m",
  github: "15m",
}

function authHeaders() {
  const token = localStorage.getItem("access_token") || ""
  return token ? { Authorization: `Bearer ${token}` } : undefined
}

async function fetchJson<T>(path: string, params?: Record<string, string>) {
  const response = await axios.get<T>(`${OpenAPI.BASE}${path}`, {
    headers: authHeaders(),
    params,
  })
  return response.data
}

async function fetchOperationsSummary() {
  return normalizeOperationsSummary(
    await fetchJson<OperationsSummary>(
      "/api/v1/admin/analytics/operations/summary",
    ),
  )
}

async function fetchOperationsTasks() {
  return normalizeOperationsTasks(
    await fetchJson<OperationsTasks>(
      "/api/v1/admin/analytics/operations/tasks",
    ),
  )
}

async function fetchOperationsFeedback() {
  return normalizeOperationsFeedback(
    await fetchJson<OperationsFeedback>(
      "/api/v1/admin/analytics/operations/feedback",
    ),
  )
}

async function fetchOperationsLiteLLM() {
  return normalizeOperationsLiteLLM(
    await fetchJson<OperationsLiteLLM>(
      "/api/v1/admin/analytics/operations/litellm",
    ),
  )
}

async function fetchVisitorsPlausible(range: string) {
  return normalizeVisitorsPlausible(
    await fetchJson<VisitorsPlausible>(
      "/api/v1/admin/analytics/visitors/plausible",
      { range },
    ),
  )
}

async function fetchVisitorsGithub() {
  return normalizeVisitorsGithub(
    await fetchJson<VisitorsGithub>("/api/v1/admin/analytics/visitors/github"),
  )
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
  const [screen, setScreen] = useState<ScreenTab>("operations")
  const [range, setRange] = useState<(typeof RANGE_OPTIONS)[number]>("30d")
  const [selectedFeedback, setSelectedFeedback] = useState<
    OperationsFeedback["recent_items"][number] | null
  >(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const screenRef = useRef<HTMLDivElement>(null)
  const isDark = useIsDarkMode()

  const summaryRefresh = useModuleRefresh("summary")
  const tasksRefresh = useModuleRefresh("tasks")
  const feedbackRefresh = useModuleRefresh("feedback")
  const litellmRefresh = useModuleRefresh("litellm")
  const plausibleRefresh = useModuleRefresh("plausible")
  const githubRefresh = useModuleRefresh("github")

  const summaryQuery = useQuery({
    queryKey: ["admin", "analytics", "operations", "summary"],
    queryFn: fetchOperationsSummary,
    refetchInterval: () => refreshMs(summaryRefresh.value),
    staleTime: 30_000,
  })
  const tasksQuery = useQuery({
    queryKey: ["admin", "analytics", "operations", "tasks"],
    queryFn: fetchOperationsTasks,
    refetchInterval: () => refreshMs(tasksRefresh.value),
    staleTime: 10_000,
  })
  const feedbackQuery = useQuery({
    queryKey: ["admin", "analytics", "operations", "feedback"],
    queryFn: fetchOperationsFeedback,
    refetchInterval: () => refreshMs(feedbackRefresh.value),
    staleTime: 60_000,
  })
  const litellmQuery = useQuery({
    queryKey: ["admin", "analytics", "operations", "litellm"],
    queryFn: fetchOperationsLiteLLM,
    refetchInterval: () => refreshMs(litellmRefresh.value),
    staleTime: 60_000,
  })
  const plausibleQuery = useQuery({
    queryKey: ["admin", "analytics", "visitors", "plausible", range],
    queryFn: () => fetchVisitorsPlausible(range),
    refetchInterval: () => refreshMs(plausibleRefresh.value),
    staleTime: 60_000,
  })
  const githubQuery = useQuery({
    queryKey: ["admin", "analytics", "visitors", "github"],
    queryFn: fetchVisitorsGithub,
    refetchInterval: () => refreshMs(githubRefresh.value),
    staleTime: 5 * 60_000,
  })

  const summary = normalizeOperationsSummary(summaryQuery.data)
  const tasks = normalizeOperationsTasks(tasksQuery.data)
  const feedback = normalizeOperationsFeedback(feedbackQuery.data)
  const litellm = normalizeOperationsLiteLLM(litellmQuery.data)
  const plausible = normalizeVisitorsPlausible(plausibleQuery.data)
  const github = normalizeVisitorsGithub(githubQuery.data)
  const screenTitle =
    screen === "operations"
      ? t("adminAnalytics.dashboard.operationsTitle")
      : t("adminAnalytics.dashboard.visitorsTitle")
  const screenSubtitle =
    screen === "operations"
      ? t("adminAnalytics.dashboard.operationsSubtitle")
      : t("adminAnalytics.dashboard.visitorsSubtitle")

  useEffect(() => {
    const updateFullscreen = () => {
      setIsFullscreen(document.fullscreenElement === screenRef.current)
    }
    updateFullscreen()
    document.addEventListener("fullscreenchange", updateFullscreen)
    return () =>
      document.removeEventListener("fullscreenchange", updateFullscreen)
  }, [])

  const toggleFullscreen = async () => {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
      return
    }
    if (screenRef.current) await screenRef.current.requestFullscreen()
  }

  return (
    <div
      ref={screenRef}
      className="flex h-full min-h-0 flex-col gap-3 overflow-hidden bg-background text-foreground"
    >
      <style>{ANALYTICS_AUTOSCROLL_CSS}</style>
      <header className="flex shrink-0 flex-col gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-sm xl:flex-row xl:items-center xl:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-md border border-border bg-muted">
              <LayoutDashboard className="size-5 text-primary" />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold">{screenTitle}</h2>
              <p className="truncate text-xs text-muted-foreground">
                {screenSubtitle}
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Tabs
            value={screen}
            onValueChange={(value) => setScreen(value as ScreenTab)}
          >
            <TabsList>
              <TabsTrigger value="operations">
                {t("adminAnalytics.dashboard.operationsScreen")}
              </TabsTrigger>
              <TabsTrigger value="visitors">
                {t("adminAnalytics.dashboard.visitorsScreen")}
              </TabsTrigger>
            </TabsList>
          </Tabs>
          {screen === "visitors" && (
            <RangeSelect
              value={range}
              onValueChange={(value) => setRange(value as typeof range)}
            />
          )}
          <Button variant="outline" size="sm" onClick={toggleFullscreen}>
            {isFullscreen ? (
              <Minimize2 data-icon="inline-start" />
            ) : (
              <Fullscreen data-icon="inline-start" />
            )}
            {isFullscreen
              ? t("adminAnalytics.dashboard.exitFullscreen")
              : t("adminAnalytics.dashboard.fullscreen")}
          </Button>
        </div>
      </header>

      <Tabs
        value={screen}
        onValueChange={(value) => setScreen(value as ScreenTab)}
        className="min-h-0 flex-1 gap-0 overflow-hidden"
      >
        <TabsContent value="operations" className="m-0 h-full min-h-0">
          <OperationsScreen
            summary={summary}
            tasks={tasks}
            feedback={feedback}
            litellm={litellm}
            queries={{
              summary: { ...summaryQuery, refresh: summaryRefresh },
              tasks: { ...tasksQuery, refresh: tasksRefresh },
              feedback: { ...feedbackQuery, refresh: feedbackRefresh },
              litellm: { ...litellmQuery, refresh: litellmRefresh },
            }}
            onSelectFeedback={setSelectedFeedback}
            isDark={isDark}
          />
        </TabsContent>
        <TabsContent value="visitors" className="m-0 h-full min-h-0">
          <VisitorsScreen
            plausible={plausible}
            github={github}
            queries={{
              plausible: { ...plausibleQuery, refresh: plausibleRefresh },
              github: { ...githubQuery, refresh: githubRefresh },
            }}
            isDark={isDark}
          />
        </TabsContent>
      </Tabs>

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

function OperationsScreen({
  summary,
  tasks,
  feedback,
  litellm,
  queries,
  onSelectFeedback,
  isDark,
}: {
  summary: OperationsSummary
  tasks: OperationsTasks
  feedback: OperationsFeedback
  litellm: OperationsLiteLLM
  queries: {
    summary: ModuleQuery
    tasks: ModuleQuery
    feedback: ModuleQuery
    litellm: ModuleQuery
  }
  onSelectFeedback: (item: OperationsFeedback["recent_items"][number]) => void
  isDark: boolean
}) {
  const { t } = useTranslation()
  const taskRows = useMemo(
    () => getTaskStatusRows(tasks.by_status),
    [tasks.by_status],
  )
  const taskTrendOption = useMemo(
    () =>
      createTrendOption({
        labels: tasks.trend.map((item) => item.date),
        series: [
          {
            name: t("adminAnalytics.dashboard.tasks"),
            data: tasks.trend.map((item) => item.count),
            color: "#2563eb",
          },
        ],
        isDark,
      }),
    [isDark, tasks.trend, t],
  )

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)_minmax(0,.95fr)] gap-3 overflow-hidden">
      <section className="grid h-full min-h-0 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <ModulePanel
          title={t("adminAnalytics.dashboard.totalUsers")}
          icon={Users}
          query={queries.summary}
          tone="blue"
        >
          <MetricSummary
            tone="blue"
            value={formatCompactNumber(summary.users.total)}
            items={[
              {
                label: t("adminAnalytics.dashboard.active"),
                value: formatCompactNumber(summary.users.active),
              },
              {
                label: t("adminAnalytics.dashboard.verified"),
                value: formatCompactNumber(summary.users.email_verified),
              },
              {
                label: t("adminAnalytics.dashboard.projects"),
                value: formatCompactNumber(summary.projects.total),
              },
            ]}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.totalTasks")}
          icon={Activity}
          query={queries.tasks}
          tone="emerald"
        >
          <MetricSummary
            tone="emerald"
            value={formatCompactNumber(tasks.total)}
            items={[
              {
                label: t("adminAnalytics.dashboard.statusLabels.running"),
                value: formatCompactNumber(tasks.by_status.running),
              },
              {
                label: t("adminAnalytics.dashboard.statusLabels.pending"),
                value: formatCompactNumber(tasks.by_status.pending),
              },
              {
                label: t("adminAnalytics.dashboard.statusLabels.failed"),
                value: formatCompactNumber(tasks.by_status.failed),
              },
            ]}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.totalSpend")}
          icon={WalletCards}
          query={queries.litellm}
          tone="amber"
        >
          {litellm.available ? (
            <MetricSummary
              tone="amber"
              value={formatCurrencyValue(litellm.total_spend)}
              items={[
                {
                  label: t("adminAnalytics.dashboard.overBudget"),
                  value: formatCompactNumber(litellm.over_budget_users),
                },
                {
                  label: t("adminAnalytics.dashboard.nearLimit"),
                  value: formatCompactNumber(litellm.near_limit_users),
                },
                {
                  label: t("adminAnalytics.dashboard.remaining"),
                  value: formatCurrencyValue(litellm.remaining),
                },
              ]}
            />
          ) : (
            <LiteLLMUnavailablePanel litellm={litellm} />
          )}
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.feedback")}
          icon={MessageSquareWarning}
          query={queries.feedback}
          tone="rose"
        >
          <MetricSummary
            tone="rose"
            value={formatCompactNumber(feedback.total)}
            items={[
              {
                label: t("feedback.list.status.pending"),
                value: formatCompactNumber(feedback.pending),
              },
              {
                label: t("feedback.list.status.in_progress"),
                value: formatCompactNumber(feedback.in_progress),
              },
              {
                label: t("feedback.list.status.resolved"),
                value: formatCompactNumber(feedback.resolved),
              },
            ]}
          />
        </ModulePanel>
      </section>

      <section className="grid h-full min-h-0 gap-3 xl:grid-cols-[1.35fr_.65fr]">
        <ModulePanel
          title={t("adminAnalytics.dashboard.taskTrend")}
          icon={BarChart3}
          query={queries.tasks}
        >
          {tasks.trend.length ? (
            <ChartBox>
              <ResponsiveEChart option={taskTrendOption} />
            </ChartBox>
          ) : (
            <EmptyPanelMessage message={t("adminAnalytics.dashboard.noData")} />
          )}
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.realtimeTaskStatus")}
          icon={Activity}
          query={queries.tasks}
        >
          <StatusList
            rows={taskRows}
            total={tasks.total}
            empty={t("adminAnalytics.dashboard.noData")}
          />
        </ModulePanel>
      </section>

      <section className="grid h-full min-h-0 gap-3 xl:grid-cols-3">
        <ModulePanel
          title={t("adminAnalytics.dashboard.topUsers")}
          icon={Users}
          query={queries.tasks}
        >
          <TaskUserRankList
            items={tasks.top_users}
            empty={t("adminAnalytics.dashboard.noData")}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.quotaUsers")}
          icon={WalletCards}
          query={queries.litellm}
        >
          {litellm.available ? (
            <RankList
              items={litellm.top_users.map((item) => ({
                label: item.name,
                sublabel: item.email || "",
                value: formatCurrencyValue(item.spend),
              }))}
              empty={t("adminAnalytics.dashboard.noData")}
            />
          ) : (
            <LiteLLMUnavailablePanel compact litellm={litellm} />
          )}
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.feedback")}
          icon={MessageSquareWarning}
          query={queries.feedback}
        >
          <FeedbackFeed
            items={feedback.recent_items}
            empty={t("adminAnalytics.dashboard.noData")}
            onSelect={onSelectFeedback}
            translate={(key) => t(key)}
          />
        </ModulePanel>
      </section>
    </div>
  )
}

function VisitorsScreen({
  plausible,
  github,
  queries,
  isDark,
}: {
  plausible: VisitorsPlausible
  github: VisitorsGithub
  queries: {
    plausible: ModuleQuery
    github: ModuleQuery
  }
  isDark: boolean
}) {
  const { t } = useTranslation()
  const plausibleStatus = getPlausibleStatus(plausible)
  const plausibleTrendOption = useMemo(
    () =>
      createTrendOption({
        labels: plausible.trend?.map((item) => item.date) ?? [],
        series: [
          {
            name: t("adminAnalytics.dashboard.visitors"),
            data: plausible.trend?.map((item) => item.visitors) ?? [],
            color: "#0f766e",
          },
          {
            name: t("adminAnalytics.dashboard.pageviews"),
            data: plausible.trend?.map((item) => item.pageviews) ?? [],
            color: "#d97706",
          },
        ],
        isDark,
      }),
    [isDark, plausible.trend, t],
  )
  const sourceRows = plausible.top_sources ?? []
  const pageRows = plausible.top_pages ?? []
  const deviceRows = (plausible.devices ?? []).map((item) => ({
    ...item,
    name: formatDimensionName(item.name),
  }))
  const browserRows = plausible.browsers ?? []
  const osRows = plausible.operating_systems ?? []

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)_minmax(0,.9fr)] gap-3 overflow-hidden">
      <section className="grid h-full min-h-0 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <ModulePanel
          title={t("adminAnalytics.dashboard.plausible")}
          icon={Eye}
          query={queries.plausible}
          right={
            plausible.available ? null : (
              <StatusBadge
                tone={plausibleStatus.tone}
                label={plausibleStatus.label}
              />
            )
          }
        >
          <MetricGrid
            items={[
              {
                label: t("adminAnalytics.dashboard.visitors"),
                value: formatCompactNumber(plausible.metrics?.visitors),
              },
              {
                label: t("adminAnalytics.dashboard.visits"),
                value: formatCompactNumber(plausible.metrics?.visits),
              },
            ]}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.pageviews")}
          icon={BarChart3}
          query={queries.plausible}
        >
          <MetricGrid
            items={[
              {
                label: t("adminAnalytics.dashboard.pageviews"),
                value: formatCompactNumber(plausible.metrics?.pageviews),
              },
              {
                label: t("adminAnalytics.dashboard.viewsPerVisit"),
                value: formatDecimal(plausible.metrics?.views_per_visit),
              },
            ]}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.engagement")}
          icon={Activity}
          query={queries.plausible}
        >
          <MetricGrid
            items={[
              {
                label: t("adminAnalytics.dashboard.bounceRate"),
                value: `${formatDecimal(plausible.metrics?.bounce_rate)}%`,
              },
              {
                label: t("adminAnalytics.dashboard.visitDuration"),
                value: formatDuration(plausible.metrics?.visit_duration),
              },
            ]}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.github")}
          icon={Github}
          query={queries.github}
        >
          <MetricGrid
            items={[
              { label: "Stars", value: formatCompactNumber(github.stars) },
              { label: "Forks", value: formatCompactNumber(github.forks) },
            ]}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.issues")}
          icon={ShieldAlert}
          query={queries.github}
        >
          <MetricGrid
            items={[
              {
                label: t("adminAnalytics.dashboard.issues"),
                value: formatCompactNumber(github.open_issues),
              },
              {
                label: "Watchers",
                value: formatCompactNumber(github.watchers),
              },
            ]}
          />
        </ModulePanel>
      </section>

      <section className="grid h-full min-h-0 gap-3 xl:grid-cols-[1.35fr_.65fr]">
        <ModulePanel
          title={t("adminAnalytics.dashboard.visitorTrend")}
          icon={BarChart3}
          query={queries.plausible}
        >
          {plausible.available && plausible.trend?.length ? (
            <ChartBox>
              <ResponsiveEChart option={plausibleTrendOption} />
            </ChartBox>
          ) : (
            <PlausibleConfigNotice
              plausible={plausible}
              status={plausibleStatus}
            />
          )}
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.github")}
          icon={Github}
          query={queries.github}
        >
          <CompactGithub
            github={github}
            empty={getGithubIssueEmptyMessage(
              github,
              t("adminAnalytics.dashboard.noOpenIssues"),
            )}
          />
        </ModulePanel>
      </section>

      <section className="grid h-full min-h-0 gap-3 xl:grid-cols-4">
        <ModulePanel
          title={t("adminAnalytics.dashboard.geoTraffic")}
          icon={Globe2}
          query={queries.plausible}
        >
          <GeoGlobe
            countries={plausible.countries ?? []}
            cities={plausible.cities ?? []}
            isDark={isDark}
            empty={t("adminAnalytics.dashboard.noData")}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.sources")}
          icon={Globe2}
          query={queries.plausible}
        >
          <RankList
            items={sourceRows.map((item) => ({
              label: formatDimensionName(item.name),
              sublabel: t("adminAnalytics.dashboard.source"),
              value: formatCompactNumber(item.value),
            }))}
            empty={t("adminAnalytics.dashboard.noData")}
          />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.devices")}
          icon={MonitorSmartphone}
          query={queries.plausible}
        >
          <DistributionList rows={[...deviceRows, ...browserRows, ...osRows]} />
        </ModulePanel>
        <ModulePanel
          title={t("adminAnalytics.dashboard.topPages")}
          icon={Server}
          query={queries.plausible}
        >
          <RankList
            items={pageRows.map((item) => ({
              label: formatDimensionName(item.name),
              sublabel: t("adminAnalytics.dashboard.page"),
              value: formatCompactNumber(item.value),
            }))}
            empty={t("adminAnalytics.dashboard.noData")}
          />
        </ModulePanel>
      </section>
    </div>
  )
}

type ModuleQuery = {
  dataUpdatedAt?: number
  isLoading: boolean
  isFetching: boolean
  error: unknown
  refetch: () => void
  refresh: {
    value: RefreshValue
    setValue: (value: RefreshValue) => void
  }
}

function ModulePanel({
  title,
  icon: Icon,
  query,
  right,
  tone,
  children,
}: {
  title: string
  icon: LucideIcon
  query: ModuleQuery
  right?: ReactNode
  tone?: DashboardTone
  children: ReactNode
}) {
  const { t } = useTranslation()
  const toneStyles = tone ? DASHBOARD_TONE_STYLES[tone] : null
  return (
    <section
      className={`flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm ${toneStyles?.panel ?? ""}`}
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`flex size-6 shrink-0 items-center justify-center rounded-md ${toneStyles?.icon ?? "text-primary"}`}
          >
            <Icon className="size-4" />
          </span>
          <h3 className="truncate text-sm font-semibold">{title}</h3>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {right}
          <span className="hidden items-center gap-1 text-[11px] text-muted-foreground 2xl:flex">
            <Clock3 className="size-3" />
            {formatUpdatedAt(
              query.dataUpdatedAt,
              t("adminAnalytics.dashboard.notUpdated"),
            )}
          </span>
          <RefreshSelect
            value={query.refresh.value}
            onValueChange={query.refresh.setValue}
          />
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => query.refetch()}
          >
            <RefreshCw
              className={`size-4 ${query.isFetching ? "animate-spin" : ""}`}
            />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden p-3">
        {query.isLoading ? (
          <ModuleSkeleton />
        ) : query.error ? (
          <Alert variant="destructive" className="h-full">
            <AlertTriangle className="size-4" />
            <AlertTitle>
              {t("adminAnalytics.dashboard.moduleLoadFailed")}
            </AlertTitle>
            <AlertDescription className="line-clamp-3">
              {String(query.error)}
            </AlertDescription>
          </Alert>
        ) : (
          children
        )}
      </div>
    </section>
  )
}

function MetricGrid({
  items,
}: {
  items: Array<{ label: string; value: string }>
}) {
  return (
    <div className="grid h-full grid-cols-2 gap-2">
      {items.map((item) => (
        <div
          key={item.label}
          className="min-w-0 rounded-md border border-border bg-muted/35 p-2.5"
        >
          <p className="truncate text-[11px] text-muted-foreground">
            {item.label}
          </p>
          <p className="mt-1 truncate text-xl font-semibold tabular-nums">
            <AnimatedMetricValue value={item.value} />
          </p>
        </div>
      ))}
    </div>
  )
}

function MetricSummary({
  tone,
  value,
  items,
}: {
  tone: DashboardTone
  value: string
  items: Array<{ label: string; value: string }>
}) {
  const toneStyles = DASHBOARD_TONE_STYLES[tone]
  return (
    <div className="flex h-full min-h-[76px] flex-col justify-between gap-3">
      <p
        className={`truncate text-2xl font-semibold tabular-nums ${toneStyles.metric}`}
      >
        <AnimatedMetricValue value={value} />
      </p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <span
            key={item.label}
            className={`inline-flex max-w-full items-center gap-1 rounded border px-2 py-1 text-[11px] ${toneStyles.pill}`}
          >
            <span className="truncate opacity-75">{item.label}</span>
            <span className="shrink-0 font-semibold tabular-nums">
              {item.value}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

function LiteLLMUnavailablePanel({
  litellm,
  compact = false,
}: {
  litellm: OperationsLiteLLM
  compact?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="flex h-full min-h-0 flex-col justify-center rounded-md border border-dashed border-border bg-muted/25 p-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {t("adminAnalytics.dashboard.litellmUnavailable")}
          </p>
          {!compact && (
            <p className="mt-1 text-xs text-muted-foreground">
              {litellm.message || t("adminAnalytics.dashboard.noData")}
            </p>
          )}
          {litellm.detail && (
            <p className="mt-1 line-clamp-2 break-all text-[11px] text-muted-foreground">
              {litellm.detail}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function AnimatedMetricValue({ value }: { value: string }) {
  const reduceMotion = usePrefersReducedMotion()
  const parsed = useMemo(() => parseAnimatedMetric(value), [value])
  const [displayValue, setDisplayValue] = useState(value)

  useEffect(() => {
    if (!parsed || reduceMotion) {
      setDisplayValue(value)
      return
    }

    let frame = 0
    const duration = 780
    const startedAt = performance.now()
    const animate = (now: number) => {
      const progress = Math.min((now - startedAt) / duration, 1)
      const eased = 1 - (1 - progress) ** 3
      setDisplayValue(formatAnimatedMetric(parsed, parsed.target * eased))
      if (progress < 1) frame = window.requestAnimationFrame(animate)
    }
    frame = window.requestAnimationFrame(animate)
    return () => window.cancelAnimationFrame(frame)
  }, [parsed, reduceMotion, value])

  return (
    <span className="inline-block min-w-[4ch] transition-colors duration-300">
      {displayValue}
    </span>
  )
}

function LoopScrollList({
  children,
  className,
  resetKey,
}: {
  children: ReactNode
  className?: string
  resetKey: string | number
}) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const [scrollState, setScrollState] = useState({
    overflowing: false,
    distance: 0,
    duration: 28,
  })
  const [reduceMotion, setReduceMotion] = useState(false)

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)")
    const updatePreference = () => setReduceMotion(media.matches)
    updatePreference()
    media.addEventListener("change", updatePreference)
    return () => media.removeEventListener("change", updatePreference)
  }, [])

  const updateOverflow = useCallback(() => {
    const viewport = viewportRef.current
    const content = contentRef.current
    if (!viewport || !content) return
    const contentHeight = content.scrollHeight
    const viewportHeight = viewport.clientHeight
    const gap = 12
    const distance = contentHeight + gap
    setScrollState({
      overflowing: contentHeight > viewportHeight + 2,
      distance,
      duration: Math.max(18, Math.round(distance / 12)),
    })
  }, [])

  useEffect(() => {
    const viewport = viewportRef.current
    const content = contentRef.current
    if (!viewport || !content) return

    updateOverflow()

    const observer = new ResizeObserver(updateOverflow)
    observer.observe(viewport)
    observer.observe(content)
    return () => observer.disconnect()
  }, [updateOverflow])

  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    void resetKey
    viewport.scrollTop = 0
    window.requestAnimationFrame(updateOverflow)
  }, [resetKey, updateOverflow])

  const shouldAnimate = scrollState.overflowing && !reduceMotion

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-md">
      <section
        ref={viewportRef}
        aria-label="Analytics list"
        className={`admin-analytics-autoscroll min-h-0 flex-1 overscroll-contain pr-1 ${reduceMotion ? "overflow-y-auto" : "overflow-hidden"}`}
      >
        <div
          className="admin-analytics-autoscroll-track"
          data-animated={shouldAnimate}
          style={
            {
              "--analytics-scroll-distance": `${scrollState.distance}px`,
              "--analytics-scroll-duration": `${scrollState.duration}s`,
            } as React.CSSProperties
          }
        >
          <div ref={contentRef} className={`py-px ${className ?? ""}`}>
            {children}
          </div>
          {shouldAnimate && (
            <div aria-hidden className={`mt-3 py-px ${className ?? ""}`}>
              {children}
            </div>
          )}
        </div>
      </section>
      {shouldAnimate && (
        <>
          <div className="pointer-events-none absolute inset-x-0 top-0 h-5 bg-gradient-to-b from-card to-transparent" />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-5 bg-gradient-to-t from-card to-transparent" />
        </>
      )}
    </div>
  )
}

function StatusList({
  rows,
  total,
  empty,
}: {
  rows: ReturnType<typeof getTaskStatusRows>
  total: number
  empty: string
}) {
  const { t } = useTranslation()
  if (!rows.length) return <EmptyPanelMessage message={empty} />
  const max = Math.max(...rows.map((item) => item.value), 1)
  const resetKey = rows.map((row) => `${row.key}:${row.value}`).join("|")
  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_1fr] gap-3">
      <MetricGrid
        items={[
          {
            label: t("adminAnalytics.dashboard.totalTasks"),
            value: formatCompactNumber(total),
          },
          {
            label: t("adminAnalytics.dashboard.liveTasks"),
            value: formatCompactNumber(
              rows
                .filter((item) => item.active)
                .reduce((sum, item) => sum + item.value, 0),
            ),
          },
        ]}
      />
      <LoopScrollList className="space-y-2" resetKey={resetKey}>
        {rows.map((row) => {
          const width = `${Math.max((row.value / max) * 100, row.value ? 6 : 0)}%`
          return (
            <div
              key={row.key}
              className="rounded-md border border-border bg-muted/25 px-3 py-2"
            >
              <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
                <span className="truncate font-medium">
                  {row.labelKey.includes(".") ? t(row.labelKey) : row.labelKey}
                </span>
                <span className="font-semibold tabular-nums">
                  {formatCompactNumber(row.value)}
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width }}
                />
              </div>
            </div>
          )
        })}
      </LoopScrollList>
    </div>
  )
}

function DistributionList({
  rows,
}: {
  rows: Array<{ name: string; value: number }>
}) {
  const { t } = useTranslation()
  if (!rows.length) {
    return <EmptyPanelMessage message={t("adminAnalytics.dashboard.noData")} />
  }
  const total = rows.reduce((sum, item) => sum + item.value, 0) || 1
  const max = Math.max(...rows.map((item) => item.value), 1)
  return (
    <LoopScrollList className="space-y-2" resetKey={rows.length}>
      {rows.map((item, index) => {
        const percent = (item.value / total) * 100
        const width = `${Math.max((item.value / max) * 100, item.value ? 5 : 0)}%`
        return (
          <div
            key={`${item.name}-${index}`}
            className="rounded-md border border-border bg-muted/25 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="truncate font-medium">
                {formatDimensionName(item.name)}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatCompactNumber(item.value)} · {percent.toFixed(1)}%
              </span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width }}
              />
            </div>
          </div>
        )
      })}
    </LoopScrollList>
  )
}

function RankList({
  items,
  empty,
  compact = false,
}: {
  items: Array<{
    label: string
    sublabel: string
    value: string
    href?: string
  }>
  empty: string
  compact?: boolean
}) {
  if (!items.length) return <EmptyPanelMessage message={empty} />
  return (
    <LoopScrollList
      className={compact ? "space-y-1" : "space-y-1.5"}
      resetKey={items.length}
    >
      {items.map((item, index) => {
        const content = (
          <>
            <div className="flex size-6 shrink-0 items-center justify-center rounded bg-muted text-xs text-muted-foreground">
              {index + 1}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{item.label}</p>
              <p className="truncate text-xs text-muted-foreground">
                {item.sublabel}
              </p>
            </div>
            <Badge variant="secondary" className="shrink-0">
              {item.value}
            </Badge>
          </>
        )
        const className =
          "flex items-center gap-3 rounded-md border border-border bg-muted/25 px-3 py-2 transition-colors hover:bg-muted"
        return item.href ? (
          <a
            key={`${item.label}-${index}`}
            href={item.href}
            target="_blank"
            rel="noreferrer"
            className={className}
          >
            {content}
          </a>
        ) : (
          <div key={`${item.label}-${index}`} className={className}>
            {content}
          </div>
        )
      })}
    </LoopScrollList>
  )
}

function TaskUserRankList({
  items,
  empty,
}: {
  items: OperationsTasks["top_users"]
  empty: string
}) {
  const { t } = useTranslation()
  if (!items.length) return <EmptyPanelMessage message={empty} />
  return (
    <LoopScrollList className="space-y-1.5" resetKey={items.length}>
      {items.map((item, index) => {
        const displayName = formatTaskUserDisplayName(item)
        const shortId = formatShortId(item.user_id)
        return (
          <div
            key={`${item.user_id || item.email || item.name}-${index}`}
            className="rounded-md border border-border bg-muted/25 px-3 py-2"
          >
            <div className="flex items-start gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-xs font-semibold text-primary">
                {formatUserInitials(displayName)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {displayName}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {item.email || t("adminAnalytics.dashboard.noEmail")}
                      {shortId ? ` · ${shortId}` : ""}
                    </p>
                  </div>
                  <Badge variant="secondary" className="shrink-0">
                    {formatCompactNumber(item.tasks)}{" "}
                    {t("adminAnalytics.dashboard.tasks")}
                  </Badge>
                </div>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {formatCompactNumber(item.projects)}{" "}
                  {t("adminAnalytics.dashboard.projects")}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                  <StatusPill
                    label={t("adminAnalytics.dashboard.statusLabels.running")}
                    value={item.active_tasks ?? 0}
                  />
                  <StatusPill
                    label={t("adminAnalytics.dashboard.statusLabels.completed")}
                    value={item.completed_tasks ?? 0}
                  />
                  <StatusPill
                    label={t("adminAnalytics.dashboard.statusLabels.failed")}
                    value={item.failed_tasks ?? 0}
                    destructive
                  />
                </div>
                {item.latest_task_time && (
                  <p className="mt-1 truncate text-[11px] text-muted-foreground">
                    {t("adminAnalytics.dashboard.latestTask")}:{" "}
                    {formatDate(item.latest_task_time)}
                  </p>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </LoopScrollList>
  )
}

function StatusPill({
  label,
  value,
  destructive = false,
}: {
  label: string
  value: number
  destructive?: boolean
}) {
  return (
    <span
      className={`rounded border px-1.5 py-0.5 tabular-nums ${
        destructive
          ? "border-destructive/30 text-destructive"
          : "border-border text-muted-foreground"
      }`}
    >
      {label} {formatCompactNumber(value)}
    </span>
  )
}

function FeedbackFeed({
  items,
  empty,
  onSelect,
  translate,
}: {
  items: OperationsFeedback["recent_items"]
  empty: string
  onSelect?: (item: OperationsFeedback["recent_items"][number]) => void
  translate: (key: string) => string
}) {
  if (!items.length) return <EmptyPanelMessage message={empty} />
  return (
    <LoopScrollList className="space-y-2" resetKey={items.length}>
      {items.map((item, index) => (
        <button
          type="button"
          key={`${item.id || item.title}-${index}`}
          onClick={() => onSelect?.(item)}
          className="grid w-full grid-cols-[1fr_auto] gap-3 rounded-md border border-border bg-muted/25 px-3 py-2 text-left transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{item.title}</p>
            <p className="truncate text-xs text-muted-foreground">
              {item.user_full_name || item.user_email || "Anonymous"} |{" "}
              {formatDate(item.created_time)}
            </p>
            {item.content && (
              <p className="mt-1 line-clamp-1 text-xs leading-5 text-muted-foreground">
                {item.content}
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <Badge variant="outline">
              {translate(`feedback.list.priority.${item.priority}`)}
            </Badge>
            <Badge variant="secondary">
              {translate(`feedback.list.status.${item.status}`)}
            </Badge>
          </div>
        </button>
      ))}
    </LoopScrollList>
  )
}

function CompactGithub({
  github,
  empty,
}: {
  github: VisitorsGithub
  empty: string
}) {
  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-3">
      <MetricGrid
        items={[
          { label: "Stars", value: formatCompactNumber(github.stars) },
          { label: "Forks", value: formatCompactNumber(github.forks) },
          { label: "Watchers", value: formatCompactNumber(github.watchers) },
          { label: "Issues", value: formatCompactNumber(github.open_issues) },
        ]}
      />
      <RankList
        items={(github.recent_issues ?? []).map((issue) => ({
          label: `#${issue.number} ${issue.title}`,
          sublabel: issue.updated_at
            ? formatDate(issue.updated_at)
            : github.repository || "",
          value: "open",
          href: issue.url,
        }))}
        empty={empty}
      />
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
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="pr-8">{feedback.title}</DialogTitle>
          <DialogDescription>
            {feedback.user_full_name || feedback.user_email || "Anonymous"} |{" "}
            {formatDate(feedback.created_time)}
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[68vh] pr-3">
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">
                {translate(
                  `feedback.submit.typeOptions.${feedback.type || "other"}`,
                )}
              </Badge>
              <Badge variant="outline">
                {translate(`feedback.list.priority.${feedback.priority}`)}
              </Badge>
              <Badge variant="outline">
                {translate(`feedback.list.status.${feedback.status}`)}
              </Badge>
            </div>

            <DetailField
              label={translate("adminAnalytics.dashboard.feedbackContent")}
            >
              <p className="whitespace-pre-wrap leading-6">
                {feedback.content || "-"}
              </p>
            </DetailField>

            <div className="grid gap-3 md:grid-cols-2">
              <DetailField
                label={translate("adminAnalytics.dashboard.feedbackContact")}
              >
                {feedback.contact_email || feedback.user_email || "-"}
              </DetailField>
              <DetailField
                label={translate("adminAnalytics.dashboard.feedbackPage")}
              >
                {feedback.page_url ? (
                  <a
                    href={feedback.page_url}
                    target="_blank"
                    rel="noreferrer"
                    className="break-all text-primary hover:underline"
                  >
                    {feedback.page_url}
                  </a>
                ) : (
                  "-"
                )}
              </DetailField>
              <DetailField
                label={translate("adminAnalytics.dashboard.feedbackBrowser")}
              >
                {feedback.browser_info || "-"}
              </DetailField>
              <DetailField
                label={translate("adminAnalytics.dashboard.feedbackTags")}
              >
                {feedback.tags || "-"}
              </DetailField>
            </div>

            {feedback.admin_reply && (
              <DetailField
                label={translate("adminAnalytics.dashboard.feedbackReply")}
              >
                <p className="whitespace-pre-wrap leading-6">
                  {feedback.admin_reply}
                </p>
              </DetailField>
            )}

            <div className="flex justify-end">
              <Button asChild>
                <Link to="/feedback">
                  {translate("adminAnalytics.dashboard.openFeedback")}
                </Link>
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
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <p className="mb-1 text-[11px] font-medium text-muted-foreground">
        {label}
      </p>
      <div className="text-sm">{children}</div>
    </div>
  )
}

function PlausibleConfigNotice({
  plausible,
  status,
}: {
  plausible: VisitorsPlausible
  status: ReturnType<typeof getPlausibleStatus>
}) {
  return (
    <div className="flex h-full flex-col justify-center rounded-md border border-dashed border-border bg-muted/25 p-4">
      <p className="text-sm font-medium">{status.label}</p>
      <p className="mt-1 text-sm text-muted-foreground">{status.message}</p>
      <p className="mt-3 text-xs text-muted-foreground">
        API base: {plausible.api_base_url || "-"} | Site:{" "}
        {plausible.site_id || "-"}
      </p>
    </div>
  )
}

function StatusBadge({ tone, label }: { tone: string; label: string }) {
  if (tone === "ready")
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">
        {label}
      </Badge>
    )
  if (tone === "error") return <Badge variant="destructive">{label}</Badge>
  return <Badge variant="secondary">{label}</Badge>
}

function EmptyPanelMessage({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center rounded-md border border-dashed border-border bg-muted/25 px-4 py-3 text-sm text-muted-foreground">
      {message}
    </div>
  )
}

function ModuleSkeleton() {
  return (
    <div className="grid h-full gap-2">
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-2/3" />
    </div>
  )
}

function ChartBox({ children }: { children: ReactNode }) {
  return <div className="h-full min-h-0 w-full">{children}</div>
}

function ResponsiveEChart({
  option,
  className,
}: {
  option: EChartsReactProps["option"]
  className?: string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReactECharts>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const resize = () => {
      chartRef.current?.getEchartsInstance()?.resize()
    }

    resize()
    const observer = new ResizeObserver(() => {
      window.requestAnimationFrame(resize)
    })
    observer.observe(container)

    return () => observer.disconnect()
  }, [])

  return (
    <div ref={containerRef} className={`h-full min-h-0 w-full ${className ?? ""}`}>
      <ReactECharts
        ref={chartRef}
        option={option}
        style={{ height: "100%", width: "100%" }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
    </div>
  )
}

function GeoGlobe({
  countries,
  cities,
  isDark,
  empty,
}: {
  countries: NonNullable<VisitorsPlausible["countries"]>
  cities: NonNullable<VisitorsPlausible["cities"]>
  isDark: boolean
  empty: string
}) {
  const globeData = useMemo(
    () => buildGeoGlobeData(countries, cities),
    [countries, cities],
  )
  const option = useMemo(
    () => createGeoGlobeOption(globeData.points, globeData.lines, isDark),
    [globeData, isDark],
  )
  const fallbackItems = globeData.unmapped.slice(0, 8).map((item) => ({
    label: formatDimensionName(item.name),
    sublabel: item.type,
    value: formatCompactNumber(item.value),
  }))

  if (!globeData.points.length && !fallbackItems.length) {
    return <EmptyPanelMessage message={empty} />
  }

  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_minmax(0,.58fr)] gap-2">
      {globeData.points.length ? (
        <ResponsiveEChart option={option} />
      ) : (
        <EmptyPanelMessage message={empty} />
      )}
      <RankList compact items={fallbackItems} empty={empty} />
    </div>
  )
}

function RefreshSelect({
  value,
  onValueChange,
}: {
  value: RefreshValue
  onValueChange: (value: RefreshValue) => void
}) {
  const { t } = useTranslation()
  return (
    <Select
      value={value}
      onValueChange={(next) => onValueChange(next as RefreshValue)}
    >
      <SelectTrigger size="sm" className="hidden w-[92px] text-xs sm:flex">
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        {REFRESH_OPTIONS.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {t(`adminAnalytics.dashboard.refreshOptions.${option.value}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function RangeSelect({
  value,
  onValueChange,
}: {
  value: string
  onValueChange: (value: string) => void
}) {
  return (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger size="sm" className="w-[92px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        {RANGE_OPTIONS.map((option) => (
          <SelectItem key={option} value={option}>
            {option}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function useModuleRefresh(module: AnalyticsModule) {
  const key = `adminAnalytics.refresh.${module}`
  const [value, setValueState] = useState<RefreshValue>(() => {
    if (typeof window === "undefined") return DEFAULT_REFRESH[module]
    const stored = window.localStorage.getItem(key) as RefreshValue | null
    return isRefreshValue(stored) ? stored : DEFAULT_REFRESH[module]
  })
  const setValue = (next: RefreshValue) => {
    setValueState(next)
    window.localStorage.setItem(key, next)
  }
  return { value, setValue }
}

function useIsDarkMode() {
  const [isDark, setIsDark] = useState(() =>
    typeof document !== "undefined"
      ? document.documentElement.classList.contains("dark")
      : false,
  )
  useEffect(() => {
    const target = document.documentElement
    const observer = new MutationObserver(() => {
      setIsDark(target.classList.contains("dark"))
    })
    observer.observe(target, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])
  return isDark
}

function usePrefersReducedMotion() {
  const [reduceMotion, setReduceMotion] = useState(false)
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)")
    const updatePreference = () => setReduceMotion(media.matches)
    updatePreference()
    media.addEventListener("change", updatePreference)
    return () => media.removeEventListener("change", updatePreference)
  }, [])
  return reduceMotion
}

function refreshMs(value: RefreshValue) {
  return REFRESH_OPTIONS.find((item) => item.value === value)?.ms ?? false
}

function isRefreshValue(value: string | null): value is RefreshValue {
  return REFRESH_OPTIONS.some((item) => item.value === value)
}

function formatUpdatedAt(value: number | undefined, fallback: string) {
  if (!value) return fallback
  return new Date(value).toLocaleTimeString()
}

function formatDecimal(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-"
  return Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(
    value,
  )
}

function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-"
  if (value < 60) return `${Math.round(value)}s`
  return `${Math.round(value / 60)}m`
}

function formatTaskUserDisplayName(user: OperationsTasks["top_users"][number]) {
  return (
    user.full_name ||
    user.name ||
    user.email ||
    formatShortId(user.user_id) ||
    "Unknown"
  )
}

function formatUserInitials(value: string) {
  const normalized = value.trim()
  if (!normalized) return "?"
  const parts = normalized.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase()
  }
  return normalized.slice(0, 2).toUpperCase()
}

function formatShortId(value?: string | null) {
  if (!value) return ""
  const normalized = value.trim()
  if (!normalized) return ""
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized
}

type AnimatedMetricParts = {
  prefix: string
  suffix: string
  target: number
  decimals: number
}

function parseAnimatedMetric(value: string): AnimatedMetricParts | null {
  const match = value.match(
    /^([^0-9+-]*)([+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?)(.*)$/,
  )
  if (!match) return null
  const [, prefix, numericValue, suffix] = match
  const target = Number(numericValue.replace(/,/g, ""))
  if (!Number.isFinite(target)) return null
  const decimals = numericValue.includes(".")
    ? numericValue.split(".")[1]?.length || 0
    : 0
  return { prefix, suffix, target, decimals }
}

function formatAnimatedMetric(parts: AnimatedMetricParts, value: number) {
  const formatter = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: parts.decimals,
    maximumFractionDigits: parts.decimals,
  })
  return `${parts.prefix}${formatter.format(value)}${parts.suffix}`
}

type GeoPoint = {
  name: string
  value: [number, number]
  count: number
  code: string
  symbolSize: number
}

type GeoLine = {
  coords: [[number, number], [number, number]]
  value: number
}

type WorldFeature = Feature<Geometry, Record<string, unknown>>
type WorldTopology = Topology<{
  countries: GeometryCollection<Record<string, unknown>>
}>

const GLOBE_ORIGIN: [number, number] = [114.17, 22.32]
const WORLD_COUNTRY_FEATURES = createWorldCountryFeatureIndex()

function createWorldCountryFeatureIndex() {
  const topology = worldCountries as unknown as WorldTopology
  const collection = feature(
    topology,
    topology.objects.countries,
  ) as FeatureCollection<Geometry, Record<string, unknown>>
  const index = new Map<string, WorldFeature>()
  for (const country of collection.features) {
    if (country.id === undefined || country.id === null) continue
    const id = String(country.id)
    index.set(id.padStart(3, "0"), country)
    index.set(String(Number(id)), country)
  }
  return index
}

function buildGeoGlobeData(
  countries: NonNullable<VisitorsPlausible["countries"]>,
  cities: NonNullable<VisitorsPlausible["cities"]>,
) {
  const countryRows = countries.filter(
    (item) => isKnownDimensionName(item.name) && item.value > 0,
  )
  const cityRows = cities.filter(
    (item) => isKnownDimensionName(item.name) && item.value > 0,
  )
  const max = Math.max(...countryRows.map((item) => item.value), 1)
  const points: GeoPoint[] = []
  const lines: GeoLine[] = []
  const unmapped: Array<{ name: string; value: number; type: string }> = []

  for (const item of countryRows) {
    const coord = getCountryCentroid(item.code)
    if (!coord) {
      unmapped.push({ name: item.name, value: item.value, type: "Country" })
      continue
    }
    const symbolSize = Math.max(8, Math.min(28, 8 + (item.value / max) * 20))
    points.push({
      name: formatDimensionName(item.name),
      value: coord,
      count: item.value,
      code: item.code || "",
      symbolSize,
    })
    lines.push({
      coords: [GLOBE_ORIGIN, coord],
      value: item.value,
    })
  }

  return {
    points: points.slice(0, 18),
    lines: lines.slice(0, 18),
    unmapped: [
      ...unmapped,
      ...cityRows.map((item) => ({
        name: formatDimensionName(item.name),
        value: item.value,
        type: item.country_name || item.country_code || "City",
      })),
    ],
  }
}

function getCountryCentroid(code?: string | null): [number, number] | null {
  if (!code) return null
  const numeric = alpha2ToNumeric(code.trim().toUpperCase())
  if (!numeric) return null
  const country = WORLD_COUNTRY_FEATURES.get(numeric.padStart(3, "0"))
  if (!country) return null
  const [longitude, latitude] = geoCentroid(country)
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null
  return [longitude, latitude]
}

function createGeoGlobeOption(
  points: GeoPoint[],
  lines: GeoLine[],
  isDark: boolean,
) {
  const labelColor = isDark ? "#e5e7eb" : "#111827"
  return {
    backgroundColor: "transparent",
    tooltip: {
      formatter: (params: { data?: GeoPoint }) => {
        const data = params.data
        if (!data) return ""
        return `${data.name}<br/>${data.code}: ${formatCompactNumber(data.count)}`
      },
    },
    globe: {
      baseColor: isDark ? "#0f172a" : "#dbeafe",
      shading: "lambert",
      realisticMaterial: { roughness: 0.82, metalness: 0 },
      atmosphere: { show: true, color: isDark ? "#38bdf8" : "#2563eb" },
      light: {
        ambient: { intensity: 0.82 },
        main: { intensity: 0.75, shadow: false },
      },
      viewControl: {
        autoRotate: true,
        autoRotateSpeed: 2,
        distance: 150,
        alpha: 26,
        beta: 150,
      },
    },
    series: [
      {
        type: "lines3D",
        coordinateSystem: "globe",
        blendMode: "lighter",
        data: lines,
        effect: {
          show: true,
          period: 4,
          trailWidth: 2,
          trailLength: 0.26,
          trailOpacity: 0.78,
        },
        lineStyle: {
          color: isDark ? "#38bdf8" : "#2563eb",
          width: 1,
          opacity: 0.34,
        },
      },
      {
        type: "scatter3D",
        coordinateSystem: "globe",
        data: points,
        symbolSize: (_value: unknown, params: { data?: GeoPoint }) =>
          params.data?.symbolSize ?? 10,
        itemStyle: {
          color: isDark ? "#fbbf24" : "#dc2626",
          opacity: 0.92,
        },
        label: {
          show: points.length <= 8,
          formatter: "{b}",
          color: labelColor,
          distance: 2,
          fontSize: 10,
        },
      },
    ],
  }
}

function createTrendOption({
  labels,
  series,
  isDark,
}: {
  labels: string[]
  series: Array<{ name: string; data: number[]; color: string }>
  isDark: boolean
}) {
  const axisColor = isDark ? "rgba(229,231,235,.72)" : "rgba(75,85,99,.75)"
  const gridColor = isDark ? "rgba(148,163,184,.18)" : "rgba(148,163,184,.32)"
  return {
    backgroundColor: "transparent",
    animationDurationUpdate: 450,
    grid: { left: 42, right: 18, top: 30, bottom: 30 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 8, textStyle: { color: axisColor } },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: gridColor } },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: gridColor } },
      axisLabel: { color: axisColor },
    },
    series: series.map((item) => ({
      name: item.name,
      type: "line",
      smooth: true,
      showSymbol: false,
      data: item.data,
      lineStyle: { width: 2.5, color: item.color },
      itemStyle: { color: item.color },
      areaStyle: { color: `${item.color}18` },
    })),
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
