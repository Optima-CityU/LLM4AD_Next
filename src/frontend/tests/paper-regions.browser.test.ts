import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { type Browser, chromium, expect, type Page } from "@playwright/test"
import tailwindcss from "@tailwindcss/vite"
import { createServer, type ViteDevServer } from "vite"
import type { PaperRegion, PaperRegionRequest } from "../src/client"

let server: ViteDevServer
let browser: Browser
let origin: string
let cacheDir: string
const root = fileURLToPath(new URL("..", import.meta.url))
const first = {
  id: "server:response/a",
  kind: "reviewer_response",
  title: "First response",
  content: "Keep **before**. Old claim. Keep after.",
  revision: "r1",
  review_id: "review-a",
  source_refs: [
    { path: "legacy/evidence.md", status: "verified" },
    {
      review_id: "review-a",
      version: "v1",
      quote: "Exact historical quote\n  with spaces",
      status: "historical",
    },
  ],
} satisfies PaperRegion
const second = {
  ...first,
  id: "server:response/b",
  title: "Second response",
  content: "Neighbor stays unchanged.",
  source_refs: [],
} satisfies PaperRegion
const candidate = (id: string, region = first): PaperRegionRequest => ({
  id,
  region_id: region.id,
  state: "candidate",
  instruction: "Revise this claim",
  original_text: region.content,
  replacement_text: region.content.replace("Old", "Narrow"),
  base_revision: region.revision,
  created_at: "2026-11-17T00:00:00Z",
  prompt: `Read persisted request ${id}, region ${region.id}`,
})

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "llm4ad-regions-vite-"))
  server = await createServer({
    configFile: false,
    root,
    cacheDir,
    optimizeDeps: { entries: ["tests/paper-regions.fixture.tsx"] },
    resolve: { alias: { "@": `${root}/src`, "@docs": `${root}/../../docs` } },
    plugins: [
      tailwindcss(),
      {
        name: "paper-regions-fixture",
        configureServer(devServer) {
          devServer.middlewares.use("/__regions__", (_req, res) => {
            res.setHeader("content-type", "text/html")
            res.end(
              '<!doctype html><html lang="en"><head><title>Paper regions</title></head><body><div id="root"></div><script type="module" src="/tests/paper-regions.fixture.tsx"></script></body></html>',
            )
          })
        },
      },
    ],
    server: { host: "127.0.0.1", port: 0, watch: null },
  })
  await server.listen()
  origin = server.resolvedUrls!.local[0].replace(/\/$/, "")
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  })
})
after(async () => {
  await browser?.close()
  await server?.close()
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true })
})

async function open(
  page: Page,
  requests: PaperRegionRequest[] = [],
  regions: PaperRegion[] = [first, second],
  query = "",
  language = "en",
) {
  page.on("pageerror", (error) => process.stderr.write(`${error.stack}\n`))
  const state = {
    regions: structuredClone(regions),
    requests: structuredClone(requests),
    mutations: [] as { path: string; body: Record<string, unknown> }[],
    failNext: false,
  }
  await page.addInitScript((locale) => {
    localStorage.setItem("llm4ad-language", locale)
    localStorage.setItem("access_token", "regions-fixture-token")
  }, language)
  await page.route("**/api/v1/llm4ad/**", async (route) => {
    const req = route.request()
    const path = decodeURIComponent(new URL(req.url()).pathname)
    if (req.method() === "GET") {
      if (path.includes("/region-review-sources/"))
        return route.fulfill({
          contentType: "text/plain",
          body: "Archived original **review**\n  exact historical spaces",
        })
      if (path.endsWith("/regions"))
        return route.fulfill({
          json: { regions: state.regions, requests: state.requests },
        })
      if (path.includes("/reviews/"))
        return route.fulfill({
          json: {
            review_id: "review-a",
            content: "Exact original **review**\n  preserved whitespace",
          },
        })
      return route.fulfill({ json: {} })
    }
    const body = req.postDataJSON()
    state.mutations.push({ path, body })
    if (state.failNext) {
      state.failNext = false
      return route.fulfill({
        status: 503,
        json: { detail: "Temporary failure" },
      })
    }
    const region = state.regions.find((item) => path.includes(item.id))
    if (req.method() === "PATCH" && region) {
      if (region.revision !== body.base_revision)
        return route.fulfill({ status: 409, json: {} })
      region.content = body.content
      region.revision = `${region.revision}-next`
      return route.fulfill({ json: region })
    }
    if (path.endsWith("/requests") && region) {
      const request = {
        ...candidate(`request-${state.requests.length + 1}`, region),
        state: "queued" as const,
        instruction: body.instruction,
        replacement_text: null,
      }
      state.requests.push(request)
      return route.fulfill({ json: request })
    }
    const request = state.requests.find((item) => path.includes(item.id))
    if (request) {
      const target = state.regions.find(
        (item) => item.id === request.region_id,
      )!
      if (path.endsWith("/apply")) {
        target.content = request.replacement_text!
        target.revision = "r2"
        request.state = "applied"
      } else request.state = "rejected"
      return route.fulfill({ json: request })
    }
    return route.fulfill({ status: 404, json: {} })
  })
  await page.goto(`${origin}/__regions__${query}`)
  assert.match(await page.title(), /Paper regions/)
  await expect(page.getByTestId("paper-region-workbench")).toBeVisible({
    timeout: 15000,
  })
  return state
}
const card = (page: Page, region = first) =>
  page.locator(`[data-region-id="${region.id}"]`)

test("manual Markdown edits require a diff confirmation and only patch the selected region", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page)
    const target = card(page)
    await expect(target.locator("strong")).toHaveText("before")
    await target.getByRole("button", { name: "Edit", exact: true }).click()
    await target
      .getByLabel("Markdown", { exact: true })
      .fill("Keep **before**. Manual claim. Keep after.")
    await target
      .getByRole("button", { name: "Review changes", exact: true })
      .click()
    await expect(target.getByTestId("paper-region-diff")).toBeVisible()
    assert.equal(state.mutations.length, 0)
    await target
      .getByRole("button", { name: "Confirm changes", exact: true })
      .click()
    await expect(target.locator(".prose")).toContainText("Manual claim.")
    assert.equal(state.mutations.length, 1)
    assert.equal(state.mutations[0].body.base_revision, "r1")
    assert.ok(state.mutations[0].path.endsWith(first.id))
    assert.equal(state.regions[1].content, second.content)
  } finally {
    await page.close()
  }
})

test("drafts survive server revision changes and stale suggestions cannot apply", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, [candidate("candidate-1")])
    const target = card(page)
    await target.getByRole("button", { name: "Edit", exact: true }).click()
    await target
      .getByLabel("Markdown", { exact: true })
      .fill("My unsaved draft")
    state.regions[0].revision = "r2"
    state.regions[0].content = "New server version"
    await page.getByRole("button", { name: "Refresh regions" }).click()
    await expect(
      target.getByRole("button", { name: "Confirm this suggestion" }),
    ).toBeDisabled()
    await expect(target.getByLabel("Markdown", { exact: true })).toHaveValue(
      "My unsaved draft",
    )
    await expect(
      target.getByRole("button", { name: "Review changes", exact: true }),
    ).toBeDisabled()
    await target.getByRole("button", { name: "Discard changes" }).click()
    await expect(
      target.getByText("New server version", { exact: true }),
    ).toBeVisible()
    assert.equal(state.mutations.length, 0)
  } finally {
    await page.close()
  }
})

test("candidate confirmation and rejection remain scoped to their region", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, [
      candidate("candidate-1"),
      candidate("candidate-2", second),
    ])
    await card(page, second)
      .getByRole("button", { name: "Reject suggestion" })
      .click()
    await expect(
      card(page, second).getByText("Rejected", { exact: true }),
    ).toBeAttached()
    assert.equal(state.regions[1].content, second.content)
    await card(page)
      .getByRole("button", { name: "Confirm this suggestion" })
      .click()
    await expect(card(page).locator(".prose")).toContainText("Narrow claim.")
    assert.ok(state.mutations[1].path.endsWith("candidate-1/apply"))
    assert.equal(state.mutations[1].body.base_revision, "r1")
    assert.equal(state.regions[1].content, second.content)
  } finally {
    await page.close()
  }
})

test("queued requests rehydrate and new requests dispatch immediately with selected text", async () => {
  const page = await browser.newPage()
  try {
    const queued = { ...candidate("saved-queue"), state: "queued" as const }
    const state = await open(page, [
      queued,
      { ...candidate("not-queued"), state: "processing" },
    ])
    await expect(page.getByTestId("dispatches")).toContainText("saved-queue")
    await expect(page.getByTestId("dispatches")).not.toContainText("not-queued")
    const target = card(page)
    await target.getByRole("button", { name: "Edit", exact: true }).click()
    const editor = target.getByLabel("Markdown", { exact: true })
    await editor.focus()
    await editor.press("ControlOrMeta+Home")
    for (let offset = 0; offset < 17; offset++) await editor.press("ArrowRight")
    for (let offset = 0; offset < 9; offset++)
      await editor.press("Shift+ArrowRight")
    await expect(
      target.getByText("Selected passage", { exact: true }),
    ).toBeVisible()
    await target
      .getByLabel("Reply or request a revision")
      .fill("Please narrow the claim")
    await target.getByRole("button", { name: "Send to agent" }).click()
    await expect(page.getByTestId("dispatches")).toContainText("request-3")
    assert.deepEqual(state.mutations[0].body.selection, {
      start: 17,
      end: 26,
      original_text: first.content.slice(17, 26),
    })
    await card(page, second)
      .getByLabel("Reply or request a revision")
      .fill("Also revise this response")
    await card(page, second)
      .getByRole("button", { name: "Send to agent" })
      .click()
    await expect(page.getByTestId("dispatches")).toContainText("request-4")
    await page.getByRole("button", { name: "Remount regions" }).click()
    await expect(page.getByTestId("dispatches")).toContainText("request-4")
    assert.equal(
      state.requests.filter((item) => item.state === "queued").length,
      3,
    )
  } finally {
    await page.close()
  }
})

test("failed submission preserves text and reuses its idempotency key on retry", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page)
    const target = card(page)
    state.failNext = true
    await target.getByLabel("Reply or request a revision").fill("Keep my input")
    await target.getByRole("button", { name: "Send to agent" }).click()
    await expect(target.getByRole("alert")).toBeVisible()
    await expect(target.getByLabel("Reply or request a revision")).toHaveValue(
      "Keep my input",
    )
    await target.getByRole("button", { name: "Send to agent" }).click()
    await expect(page.getByTestId("dispatches")).toContainText("request-1")
    assert.equal(
      state.mutations[0].body.idempotency_key,
      state.mutations[1].body.idempotency_key,
    )
  } finally {
    await page.close()
  }
})

test("legacy references stay unverified while exact original and historical evidence remain accessible", async () => {
  const page = await browser.newPage({ viewport: { width: 520, height: 900 } })
  try {
    await open(page)
    const target = card(page)
    await target
      .getByText("Sources & original review", { exact: false })
      .click()
    await expect(target.getByText("Unverified", { exact: true })).toBeVisible()
    await expect(
      target.getByText("Historical evidence", { exact: true }),
    ).toBeVisible()
    await target.getByRole("button", { name: "Open source" }).click()
    await expect(page.getByTestId("opened-source")).toHaveText(
      "legacy/evidence.md",
    )
    await target
      .getByRole("button", { name: "Read exact original review" })
      .click()
    await expect(target.locator("pre")).toHaveText(
      "Exact original **review**\n  preserved whitespace",
    )
    assert.equal(
      await target.getByText("Verified reference", { exact: true }).count(),
      0,
    )
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      ),
      false,
    )
  } finally {
    await page.close()
  }
})

test("immutable review versions load verbatim through authenticated evidence access", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page)
    state.regions[0].source_refs = [
      {
        review_id: "review-a",
        version: "v0",
        status: "verified",
        path: "/api/v1/llm4ad/papers/workspaces/workspace-a/region-review-sources/review-a/v0",
      },
    ]
    await page.getByRole("button", { name: "Refresh regions" }).click()
    const target = card(page)
    await expect(target.getByText("Version v0", { exact: true })).toBeAttached()
    await target
      .getByText("Sources & original review", { exact: false })
      .click()
    await target.getByRole("button", { name: "Open source" }).click()
    await expect(target.locator("pre")).toHaveText(
      "Archived original **review**\n  exact historical spaces",
    )
    await expect(
      target.getByText("Verified reference", { exact: true }),
    ).toBeVisible()
    await expect(page.getByTestId("opened-source")).toHaveText("")
  } finally {
    await page.close()
  }
})

test("concerns and replies stay visible while metadata, titles, and original reviews collapse", async () => {
  const page = await browser.newPage()
  const response = { ...first, kind: "rebuttal_entry" }
  const concern = (field: string, content: string): PaperRegion => ({
    ...first,
    id: `opaque-${field}`,
    kind: "baseline_concern",
    title: `C1: ${field}`,
    content,
  })
  try {
    const state = await open(
      page,
      [],
      [
        response,
        { ...response, id: "opaque-heading", content: response.title },
        concern("concern", "Please quantify uncertainty"),
        concern("severity", "high"),
        concern("concern_type", "W"),
        concern("answer_source", "Existing evaluation"),
        concern("draft_move", "Narrow the claim"),
        ...["sentiment", "attitude", "movability"].map((field) => ({
          ...first,
          id: `analysis-${field}`,
          kind: "reviewer_analysis",
          title: `Reviewer analysis: ${field}`,
          content: "Requires clarification",
        })),
        {
          ...first,
          id: "opaque-original",
          kind: "review_original",
          title: "Exact source review",
          content: "Original source text",
        },
        {
          ...first,
          id: "opaque-duplicate",
          kind: "rebuttal_full",
          content: "Duplicate full rendered response",
        },
        {
          ...first,
          id: "opaque-summary",
          kind: "baseline_summary",
          content: "Baseline summary belongs elsewhere",
        },
        {
          ...first,
          id: "opaque-author",
          kind: "author_needed_issue",
          content: "Author question belongs elsewhere",
        },
      ],
    )
    await expect(page.getByTestId("paper-region-editor")).toHaveCount(2)
    await expect(
      page.getByText("Duplicate full rendered response"),
    ).not.toBeAttached()
    await expect(
      page.getByText("Baseline summary belongs elsewhere"),
    ).not.toBeAttached()
    await expect(
      page.getByText("Author question belongs elsewhere"),
    ).not.toBeAttached()
    const group = page.getByTestId("paper-region-group").filter({
      has: page.getByRole("heading", { name: "C1: Concern", exact: true }),
    })
    await group
      .getByRole("button", { name: "Details & response plan", exact: false })
      .click()
    const severity = page.locator('[data-region-id="opaque-severity"]')
    await expect(severity.getByText("high", { exact: true })).toBeVisible()
    await severity.getByRole("button", { name: "Edit", exact: true }).click()
    await severity.getByLabel("Markdown", { exact: true }).fill("medium")
    await severity
      .getByRole("button", { name: "Review changes", exact: true })
      .click()
    await severity
      .getByRole("button", { name: "Confirm changes", exact: true })
      .click()
    await expect(severity.locator(".prose")).toHaveText("medium")
    assert.equal(state.mutations[0].path.endsWith("opaque-severity"), true)
    assert.equal(state.regions[0].content, response.content)
    await page
      .getByRole("button", {
        name: "Reviewer context & analysis",
        exact: false,
      })
      .click()
    await expect(page.getByRole("heading", { name: "Sentiment" })).toBeVisible()
  } finally {
    await page.close()
  }
})

test("author questions have a dedicated editable view", async () => {
  const page = await browser.newPage()
  try {
    await open(
      page,
      [],
      [
        first,
        {
          ...first,
          id: "author-only",
          kind: "author_needed_issue",
          title: "Provide evaluation counts",
          content: "How many runs?",
        },
      ],
      "?view=author",
    )
    await expect(page.getByTestId("paper-region-editor")).toHaveCount(1)
    await expect(
      page.getByRole("heading", { name: "Provide evaluation counts" }),
    ).toBeVisible()
    await expect(
      page.getByLabel("Provide author input or request a revision"),
    ).toBeVisible()
    await expect(
      page.getByRole("heading", { name: "First response" }),
    ).not.toBeAttached()
  } finally {
    await page.close()
  }
})

test("proposal stages filter summaries and keep unrelated source files in a labeled disclosure", async () => {
  const page = await browser.newPage()
  try {
    await open(
      page,
      [],
      [
        {
          ...first,
          id: "methods-summary",
          kind: "proposal_summary",
          title: "methods: summary",
          content: "Methods summary",
        },
        {
          ...first,
          id: "literature-summary",
          kind: "proposal_summary",
          title: "literature: summary",
          content: "Literature summary",
        },
        {
          ...first,
          id: "methods-file",
          kind: "proposal_section",
          title: "sections/05-methods.typ",
          content: "Methods source file",
        },
        {
          ...first,
          id: "literature-file",
          kind: "proposal_section",
          title: "sections/02-literature.typ",
          content: "Literature source file",
        },
      ],
      "?view=proposal&stage=methods&paths=sections/05-methods.typ",
    )
    await expect(page.getByTestId("paper-region-editor")).toHaveCount(2)
    await expect(
      page.getByText("Methods summary", { exact: true }),
    ).toBeVisible()
    await expect(
      page.getByText("Literature summary", { exact: true }),
    ).not.toBeAttached()
    await expect(
      page.getByText("Literature source file", { exact: true }),
    ).not.toBeAttached()
    await page.getByRole("button", { name: "Other source files (1)" }).click()
    await expect(
      page.getByRole("heading", { name: "sections/02-literature.typ" }),
    ).toBeVisible()
    await expect(
      page.getByText("Literature source file", { exact: true }),
    ).toBeVisible()
  } finally {
    await page.close()
  }
})

test("Markdown and instruction drafts survive remount and reload with their original revision", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page)
    const target = card(page)
    await target.getByRole("button", { name: "Edit", exact: true }).click()
    await target
      .getByLabel("Markdown", { exact: true })
      .fill("Unsaved author Markdown")
    await target
      .getByLabel("Reply or request a revision")
      .fill("Keep this instruction too")
    await page.getByRole("button", { name: "Remount regions" }).click()
    await expect(target.getByLabel("Markdown", { exact: true })).toHaveValue(
      "Unsaved author Markdown",
    )
    await expect(target.getByLabel("Reply or request a revision")).toHaveValue(
      "Keep this instruction too",
    )
    state.regions[0].revision = "r2"
    state.regions[0].content = "Updated server text"
    await page.reload()
    await expect(target.getByLabel("Markdown", { exact: true })).toHaveValue(
      "Unsaved author Markdown",
    )
    await expect(
      target.getByRole("button", { name: "Review changes" }),
    ).toBeDisabled()
    await expect(
      target.getByRole("button", { name: "Send to agent" }),
    ).toBeDisabled()
    await target.getByRole("button", { name: "Discard changes" }).click()
    await expect(target.getByLabel("Reply or request a revision")).toHaveValue(
      "Keep this instruction too",
    )
    await target
      .getByRole("button", { name: "Use the current section" })
      .click()
    await target.getByRole("button", { name: "Send to agent" }).click()
    await expect(page.getByTestId("dispatches")).toContainText("request-1")
    assert.equal(state.mutations[0].body.base_revision, "r2")
  } finally {
    await page.close()
  }
})

test("author answers and retry keys survive reload while transient errors do not", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(
      page,
      [],
      [{ ...first, kind: "author_needed_issue", title: "Author action" }],
      "?view=author",
    )
    const target = card(page)
    await target
      .getByLabel("Provide author input or request a revision")
      .fill("Five runs; exact standard deviation attached.")
    state.failNext = true
    await target.getByRole("button", { name: "Send to agent" }).click()
    await expect(target.getByRole("alert")).toBeVisible()
    await page.reload()
    await expect(
      target.getByLabel("Provide author input or request a revision"),
    ).toHaveValue("Five runs; exact standard deviation attached.")
    await expect(target.getByRole("alert")).not.toBeAttached()
    await target.getByRole("button", { name: "Send to agent" }).click()
    await expect(page.getByTestId("dispatches")).toContainText("request-1")
    assert.equal(
      state.mutations[0].body.idempotency_key,
      state.mutations[1].body.idempotency_key,
    )
    assert.equal(
      state.mutations[1].body.answer,
      "Five runs; exact standard deviation attached.",
    )
    await page.reload()
    await expect(
      target.getByLabel("Provide author input or request a revision"),
    ).toHaveValue("")
  } finally {
    await page.close()
  }
})

test("persistent instructions are isolated by workspace and region", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await card(page)
      .getByLabel("Reply or request a revision")
      .fill("Workspace A, first region")
    await card(page, second)
      .getByLabel("Reply or request a revision")
      .fill("Workspace A, second region")
    await page.goto(`${origin}/__regions__?workspace=workspace-b`)
    await expect(
      card(page).getByLabel("Reply or request a revision"),
    ).toHaveValue("")
    await card(page)
      .getByLabel("Reply or request a revision")
      .fill("Workspace B only")
    await page.goto(`${origin}/__regions__`)
    await expect(
      card(page).getByLabel("Reply or request a revision"),
    ).toHaveValue("Workspace A, first region")
    await expect(
      card(page, second).getByLabel("Reply or request a revision"),
    ).toHaveValue("Workspace A, second region")
  } finally {
    await page.close()
  }
})

test("known system field labels localize without translating author titles", async () => {
  const page = await browser.newPage()
  try {
    await open(
      page,
      [],
      [
        {
          ...first,
          kind: "rebuttal_entry",
          title: "Author supplied English title",
        },
        {
          ...first,
          id: "localized-concern",
          kind: "baseline_concern",
          title: "C1: concern",
          content: "Original concern prose",
        },
        {
          ...first,
          id: "localized-severity",
          kind: "baseline_concern",
          title: "C1: severity",
          content: "high",
        },
        {
          ...first,
          id: "localized-sentiment",
          kind: "reviewer_analysis",
          title: "Reviewer analysis: sentiment",
          content: "Neutral",
        },
      ],
      "",
      "zh",
    )
    await expect(
      page.getByRole("heading", { name: "Author supplied English title" }),
    ).toBeVisible()
    await expect(
      page.getByRole("heading", { name: "C1: 关注点" }),
    ).toBeVisible()
    await page
      .getByRole("button", { name: "详细信息与回复计划", exact: false })
      .click()
    await expect(
      page.getByRole("heading", { name: "C1: 严重程度" }),
    ).toBeVisible()
    await page
      .getByRole("button", { name: "审稿人背景与分析", exact: false })
      .click()
    await expect(
      page.getByRole("heading", { name: "倾向", exact: true }),
    ).toBeVisible()
  } finally {
    await page.close()
  }
})
