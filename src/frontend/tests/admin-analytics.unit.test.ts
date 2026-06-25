import assert from "node:assert/strict"
import { describe, test } from "node:test"

import {
  formatCompactNumber,
  getGithubIssueEmptyMessage,
  getTaskStatusRows,
  getPlausibleStatus,
  normalizeDashboardOverview,
} from "../src/lib/admin-analytics.ts"

describe("admin analytics helpers", () => {
  test("formats compact dashboard numbers", () => {
    assert.equal(formatCompactNumber(950), "950")
    assert.equal(formatCompactNumber(1250), "1.3K")
    assert.equal(formatCompactNumber(1_200_000), "1.2M")
    assert.equal(formatCompactNumber(null), "-")
  })

  test("reports Plausible API configuration errors", () => {
    assert.deepEqual(
      getPlausibleStatus({
        available: false,
        message: "Plausible API is not configured.",
      }),
      {
        tone: "error",
        label: "API not configured",
        message: "Plausible API is not configured.",
      },
    )
  })

  test("normalizes missing dashboard sections", () => {
    const normalized = normalizeDashboardOverview({ range: "30d" })

    assert.equal(normalized.users.total, 0)
    assert.equal(normalized.tasks.by_status.completed, 0)
    assert.deepEqual(normalized.feedback.recent_items, [])
    assert.equal(normalized.github.available, false)
    assert.equal(normalized.plausible.available, false)
    assert.deepEqual(normalized.plausible.countries, [])
    assert.deepEqual(normalized.plausible.devices, [])
  })

  test("keeps feedback detail fields for dashboard drilldown", () => {
    const normalized = normalizeDashboardOverview({
      range: "30d",
      feedback: {
        total: 1,
        pending: 1,
        in_progress: 0,
        resolved: 0,
        closed: 0,
        rejected: 0,
        by_status: { pending: 1 },
        by_type: { bug: 1 },
        by_priority: { high: 1 },
        recent_items: [
          {
            id: "feedback-1",
            title: "Crash",
            content: "Dashboard crashes",
            type: "bug",
            status: "pending",
            priority: "high",
            page_url: "https://example.com/analytics",
            browser_info: "Safari",
            contact_email: "user@example.com",
            admin_reply: "Checking",
            tags: "dashboard",
          },
        ],
      },
    })

    assert.equal(normalized.feedback.recent_items[0]?.id, "feedback-1")
    assert.equal(normalized.feedback.recent_items[0]?.content, "Dashboard crashes")
    assert.equal(normalized.feedback.recent_items[0]?.page_url, "https://example.com/analytics")
    assert.equal(normalized.feedback.recent_items[0]?.browser_info, "Safari")
  })

  test("keeps Plausible breakdown dimensions when present", () => {
    const normalized = normalizeDashboardOverview({
      range: "30d",
      plausible: {
        available: true,
        countries: [{ name: "China", value: 10 }],
        cities: [{ name: "Hong Kong", value: 4 }],
        devices: [{ name: "Desktop", value: 7 }],
        browsers: [{ name: "Safari", value: 3 }],
        operating_systems: [{ name: "macOS", value: 2 }],
      },
    })

    assert.equal(normalized.plausible.countries[0]?.name, "China")
    assert.equal(normalized.plausible.cities[0]?.value, 4)
    assert.equal(normalized.plausible.operating_systems[0]?.name, "macOS")
  })

  test("builds ordered task status rows with translation keys", () => {
    assert.deepEqual(
      getTaskStatusRows({
        failed: 2,
        running: 3,
        completed: 4,
        pending: 0,
        custom: 5,
      }),
      [
        {
          key: "running",
          value: 3,
          labelKey: "adminAnalytics.dashboard.statusLabels.running",
          active: true,
        },
        {
          key: "completed",
          value: 4,
          labelKey: "adminAnalytics.dashboard.statusLabels.completed",
          active: false,
        },
        {
          key: "failed",
          value: 2,
          labelKey: "adminAnalytics.dashboard.statusLabels.failed",
          active: false,
        },
        {
          key: "custom",
          value: 5,
          labelKey: "custom",
          active: false,
        },
      ],
    )
  })

  test("does not show GitHub success messages as empty issue text", () => {
    assert.equal(
      getGithubIssueEmptyMessage(
        { available: true, message: "success", recent_issues: [] },
        "No open issues",
      ),
      "No open issues",
    )
    assert.equal(
      getGithubIssueEmptyMessage(
        { available: false, message: "GitHub unavailable", recent_issues: [] },
        "No open issues",
      ),
      "GitHub unavailable",
    )
  })
})
