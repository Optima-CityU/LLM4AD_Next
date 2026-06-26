export type DashboardTone = "ready" | "missing" | "error"

const TASK_STATUS_ORDER = [
  "running",
  "pending",
  "completed",
  "failed",
  "uninitialized",
] as const

const LIVE_TASK_STATUS = new Set(["running", "pending"])

export interface TaskStatusRow {
  key: string
  value: number
  labelKey: string
  active: boolean
}

export interface DashboardOverview {
  range: string
  generated_at?: string
  users: {
    total: number
    active: number
    inactive: number
    superusers: number
    email_verified: number
  }
  projects: { total: number }
  tasks: {
    total: number
    by_status: Record<string, number>
    trend: Array<{ date: string; count: number }>
    top_users: Array<{
      user_id?: string
      full_name?: string | null
      name: string
      email?: string | null
      tasks: number
      projects: number
      active_tasks?: number
      completed_tasks?: number
      failed_tasks?: number
      latest_task_time?: string | null
    }>
  }
  feedback: {
    total: number
    pending: number
    in_progress: number
    resolved: number
    closed: number
    rejected: number
    by_status: Record<string, number>
    by_type: Record<string, number>
    by_priority: Record<string, number>
    recent_items: Array<{
      id?: string
      title: string
      content?: string | null
      type: string
      status: string
      priority: string
      created_time?: string | null
      contact_email?: string | null
      page_url?: string | null
      browser_info?: string | null
      admin_reply?: string | null
      tags?: string | null
      user_full_name?: string | null
      user_email?: string | null
    }>
  }
  providers: {
    total: number
    builtin: number
    custom: number
  }
  litellm: {
    available: boolean
    total_spend: number | null
    total_budget: number | null
    remaining: number | null
    over_budget_users: number
    near_limit_users: number
    top_users: Array<{
      name: string
      email?: string | null
      spend: number
      budget?: number | null
      remaining?: number | null
    }>
    detail?: string
    unavailable_reason?: string
    message?: string
  }
  github: {
    available: boolean
    repository?: string
    stars?: number
    forks?: number
    watchers?: number
    open_issues?: number
    updated_at?: string | null
    recent_issues?: Array<{
      number?: number
      title?: string
      url?: string
      updated_at?: string | null
    }>
    message?: string
  }
  plausible: {
    available: boolean
    api_base_url?: string | null
    site_id?: string | null
    date_range?: string
    metrics?: Record<string, number>
    trend?: Array<{ date: string; visitors: number; pageviews: number }>
    top_pages?: Array<{ name: string; value: number }>
    top_sources?: Array<{ name: string; value: number }>
    countries?: Array<{ code?: string; name: string; value: number }>
    cities?: Array<{
      name: string
      value: number
      country_code?: string
      country_name?: string
    }>
    devices?: Array<{ name: string; value: number }>
    browsers?: Array<{ name: string; value: number }>
    operating_systems?: Array<{ name: string; value: number }>
    errors?: Record<string, string>
    message?: string
  }
}

export interface OperationsSummary {
  generated_at?: string
  users: DashboardOverview["users"]
  projects: DashboardOverview["projects"]
  providers: DashboardOverview["providers"]
}

export type OperationsTasks = DashboardOverview["tasks"] & {
  generated_at?: string
}

export type OperationsFeedback = DashboardOverview["feedback"] & {
  generated_at?: string
}

export type OperationsLiteLLM = DashboardOverview["litellm"] & {
  generated_at?: string
}

export type VisitorsPlausible = DashboardOverview["plausible"] & {
  generated_at?: string
}

export type VisitorsGithub = DashboardOverview["github"] & {
  generated_at?: string
}

const emptyOverview: DashboardOverview = {
  range: "30d",
  users: {
    total: 0,
    active: 0,
    inactive: 0,
    superusers: 0,
    email_verified: 0,
  },
  projects: { total: 0 },
  tasks: {
    total: 0,
    by_status: {
      uninitialized: 0,
      pending: 0,
      running: 0,
      completed: 0,
      failed: 0,
    },
    trend: [],
    top_users: [],
  },
  feedback: {
    total: 0,
    pending: 0,
    in_progress: 0,
    resolved: 0,
    closed: 0,
    rejected: 0,
    by_status: {},
    by_type: {},
    by_priority: {},
    recent_items: [],
  },
  providers: {
    total: 0,
    builtin: 0,
    custom: 0,
  },
  litellm: {
    available: false,
    total_spend: 0,
    total_budget: null,
    remaining: null,
    over_budget_users: 0,
    near_limit_users: 0,
    top_users: [],
  },
  github: {
    available: false,
    recent_issues: [],
  },
  plausible: {
    available: false,
    trend: [],
    top_pages: [],
    top_sources: [],
    countries: [],
    cities: [],
    devices: [],
    browsers: [],
    operating_systems: [],
    errors: {},
  },
}

export const emptyOperationsSummary: OperationsSummary = {
  users: emptyOverview.users,
  projects: emptyOverview.projects,
  providers: emptyOverview.providers,
}

export const emptyOperationsTasks: OperationsTasks = emptyOverview.tasks

export const emptyOperationsFeedback: OperationsFeedback =
  emptyOverview.feedback

export const emptyOperationsLiteLLM: OperationsLiteLLM = emptyOverview.litellm

export const emptyVisitorsPlausible: VisitorsPlausible = emptyOverview.plausible

export const emptyVisitorsGithub: VisitorsGithub = emptyOverview.github

export function normalizeDashboardOverview(
  payload: Partial<DashboardOverview> | null | undefined,
): DashboardOverview {
  return {
    ...emptyOverview,
    ...(payload ?? {}),
    users: { ...emptyOverview.users, ...(payload?.users ?? {}) },
    projects: { ...emptyOverview.projects, ...(payload?.projects ?? {}) },
    tasks: {
      ...emptyOverview.tasks,
      ...(payload?.tasks ?? {}),
      by_status: {
        ...emptyOverview.tasks.by_status,
        ...(payload?.tasks?.by_status ?? {}),
      },
      trend: payload?.tasks?.trend ?? [],
      top_users: payload?.tasks?.top_users ?? [],
    },
    feedback: {
      ...emptyOverview.feedback,
      ...(payload?.feedback ?? {}),
      by_status: payload?.feedback?.by_status ?? {},
      by_type: payload?.feedback?.by_type ?? {},
      by_priority: payload?.feedback?.by_priority ?? {},
      recent_items: payload?.feedback?.recent_items ?? [],
    },
    providers: {
      ...emptyOverview.providers,
      ...(payload?.providers ?? {}),
    },
    litellm: {
      ...emptyOverview.litellm,
      ...(payload?.litellm ?? {}),
      top_users: payload?.litellm?.top_users ?? [],
    },
    github: {
      ...emptyOverview.github,
      ...(payload?.github ?? {}),
      recent_issues: payload?.github?.recent_issues ?? [],
    },
    plausible: {
      ...emptyOverview.plausible,
      ...(payload?.plausible ?? {}),
      trend: payload?.plausible?.trend ?? [],
      top_pages: payload?.plausible?.top_pages ?? [],
      top_sources: payload?.plausible?.top_sources ?? [],
      countries: payload?.plausible?.countries ?? [],
      cities: payload?.plausible?.cities ?? [],
      devices: payload?.plausible?.devices ?? [],
      browsers: payload?.plausible?.browsers ?? [],
      operating_systems: payload?.plausible?.operating_systems ?? [],
      errors: payload?.plausible?.errors ?? {},
    },
  }
}

export function normalizeOperationsSummary(
  payload: Partial<OperationsSummary> | null | undefined,
): OperationsSummary {
  return {
    generated_at: payload?.generated_at,
    users: { ...emptyOverview.users, ...(payload?.users ?? {}) },
    projects: { ...emptyOverview.projects, ...(payload?.projects ?? {}) },
    providers: { ...emptyOverview.providers, ...(payload?.providers ?? {}) },
  }
}

export function normalizeOperationsTasks(
  payload: Partial<OperationsTasks> | null | undefined,
): OperationsTasks {
  return {
    ...emptyOverview.tasks,
    ...(payload ?? {}),
    by_status: {
      ...emptyOverview.tasks.by_status,
      ...(payload?.by_status ?? {}),
    },
    trend: payload?.trend ?? [],
    top_users: payload?.top_users ?? [],
  }
}

export function normalizeOperationsFeedback(
  payload: Partial<OperationsFeedback> | null | undefined,
): OperationsFeedback {
  return {
    ...emptyOverview.feedback,
    ...(payload ?? {}),
    by_status: payload?.by_status ?? {},
    by_type: payload?.by_type ?? {},
    by_priority: payload?.by_priority ?? {},
    recent_items: payload?.recent_items ?? [],
  }
}

export function normalizeOperationsLiteLLM(
  payload: Partial<OperationsLiteLLM> | null | undefined,
): OperationsLiteLLM {
  return {
    ...emptyOverview.litellm,
    ...(payload ?? {}),
    top_users: payload?.top_users ?? [],
  }
}

export function normalizeVisitorsPlausible(
  payload: Partial<VisitorsPlausible> | null | undefined,
): VisitorsPlausible {
  return {
    ...emptyOverview.plausible,
    ...(payload ?? {}),
    trend: payload?.trend ?? [],
    top_pages: payload?.top_pages ?? [],
    top_sources: payload?.top_sources ?? [],
    countries: payload?.countries ?? [],
    cities: payload?.cities ?? [],
    devices: payload?.devices ?? [],
    browsers: payload?.browsers ?? [],
    operating_systems: payload?.operating_systems ?? [],
    errors: payload?.errors ?? {},
  }
}

export function normalizeVisitorsGithub(
  payload: Partial<VisitorsGithub> | null | undefined,
): VisitorsGithub {
  return {
    ...emptyOverview.github,
    ...(payload ?? {}),
    recent_issues: payload?.recent_issues ?? [],
  }
}

export function formatCompactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-"
  return Intl.NumberFormat(undefined, {
    notation: Math.abs(value) >= 1000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(value) >= 1000 ? 1 : 0,
  }).format(value)
}

export function formatCurrencyValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-"
  return Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value >= 10 ? 1 : 3,
  }).format(value)
}

export function getPlausibleStatus(plausible: DashboardOverview["plausible"]): {
  tone: DashboardTone
  label: string
  message: string
} {
  if (plausible.available) {
    return {
      tone: "ready",
      label: "API connected",
      message: plausible.message || "Plausible data is up to date.",
    }
  }
  return {
    tone: plausible.message ? "error" : "missing",
    label: plausible.message ? "API not configured" : "Not configured",
    message: plausible.message || "Plausible analytics are not configured.",
  }
}

export function getGithubIssueEmptyMessage(
  github: Pick<
    DashboardOverview["github"],
    "available" | "message" | "recent_issues"
  >,
  noOpenIssuesText: string,
): string {
  if (!github.available && github.message) return github.message
  return noOpenIssuesText
}

export function getTaskStatusRows(
  values: Record<string, number>,
): TaskStatusRow[] {
  const knownRows = TASK_STATUS_ORDER.map((key) => ({
    key,
    value: values[key] ?? 0,
    labelKey: `adminAnalytics.dashboard.statusLabels.${key}`,
    active: LIVE_TASK_STATUS.has(key),
  })).filter((item) => item.value > 0)

  const customRows = Object.entries(values)
    .filter(
      ([key, value]) =>
        !TASK_STATUS_ORDER.includes(
          key as (typeof TASK_STATUS_ORDER)[number],
        ) && value > 0,
    )
    .map(([key, value]) => ({
      key,
      value,
      labelKey: key,
      active: false,
    }))

  return [...knownRows, ...customRows]
}
