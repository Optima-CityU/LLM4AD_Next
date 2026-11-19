import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { type Browser, chromium, expect, type Page } from "@playwright/test"
import tailwindcss from "@tailwindcss/vite"
import { createServer, type ViteDevServer } from "vite"
import type {
  PaperHistoryComparison,
  PaperHistoryFile,
  PaperHistoryResponse,
  PaperRevisionCandidateResponse,
  PaperSourceVersionResponse,
} from "../src/client"

let server: ViteDevServer
let browser: Browser
let origin: string
let cacheDir: string
const root = fileURLToPath(new URL("..", import.meta.url))
const old: PaperSourceVersionResponse = {
  id: "source-old",
  workspace_id: "workspace-a",
  version: 1,
  source_kind: "source_bundle",
  filename: "proposal.zip",
  object_key: "private-old",
  content_hash: "old-hash",
  content_size: 100,
  manifest: ["sections/methods.typ", "deleted.md", "image.png"],
  parent_version_id: null,
  change_summary: "Initial source",
  created_time: "2026-11-16T00:00:00Z",
}
const current: PaperSourceVersionResponse = {
  ...old,
  id: "source-current",
  version: 2,
  object_key: "private-current",
  content_hash: "current-hash",
  manifest: ["sections/methods.typ", "added.md", "image.png"],
  parent_version_id: old.id,
  change_summary: "Current author changes",
  created_time: "2026-11-17T00:00:00Z",
}
const candidate: PaperRevisionCandidateResponse = {
  id: "candidate-a",
  workspace_id: "workspace-a",
  source_version_id: current.id,
  run_id: null,
  target_id: null,
  title: "Improve methods and references",
  summary: "Scoped source changes awaiting approval.",
  status: "pending",
  accepted_source_version_id: null,
  created_time: "2026-11-17T01:00:00Z",
  updated_time: "2026-11-17T01:00:00Z",
}
const fullPatch = {
  schema_version: "paper-source-diff-v1",
  source_version_id: current.id,
  content_hash: current.content_hash,
  files: [
    {
      path: "sections/methods.typ",
      original: Buffer.from("Keep. Old method. End.").toString("base64"),
      replacement: Buffer.from("Keep. New method. End.").toString("base64"),
    },
    {
      path: "references.bib",
      original: null,
      replacement: Buffer.from("@article{new, title={Evidence}}").toString(
        "base64",
      ),
      original_text: null,
      replacement_text: "@article{new, title={Evidence}}",
    },
    {
      path: "obsolete.md",
      original: Buffer.from("obsolete").toString("base64"),
      replacement: null,
    },
    {
      path: "image.png",
      original: Buffer.from([0, 255]).toString("base64"),
      replacement: Buffer.from([0, 254, 1]).toString("base64"),
    },
  ],
}

const hash = "a".repeat(64)
const revisions: PaperHistoryResponse = {
  current_revision_id: "revision-current",
  current_state_hash: hash,
  revisions: [
    {
      id: "revision-current",
      label: "Current workspace",
      created_time: "2026-11-17T00:00:00Z",
      origin: "workspace",
      summary: "Current outputs",
      source_version_id: current.id,
      state_hash: hash,
      is_current: true,
    },
    {
      id: "revision-old",
      label: "Version 1",
      created_time: "2026-11-16T00:00:00Z",
      origin: "upload",
      summary: "Saved source and replies",
      source_version_id: old.id,
      state_hash: "0".repeat(64),
      is_current: false,
    },
  ],
}
const inventory: PaperHistoryComparison["files"] = [
  {
    path: "source/sections/methods.typ",
    status: "modified",
    mime_type: "text/plain",
    binary: false,
    before_size: 35,
    after_size: 33,
  },
  {
    path: "source/added.md",
    status: "added",
    mime_type: "text/markdown",
    binary: false,
    before_size: 0,
    after_size: 20,
  },
  {
    path: "source/deleted.md",
    status: "deleted",
    mime_type: "text/markdown",
    binary: false,
    before_size: 10,
    after_size: 0,
  },
  {
    path: "source/unchanged.txt",
    status: "unchanged",
    mime_type: "text/plain",
    binary: false,
    before_size: 4,
    after_size: 4,
  },
  {
    path: "source/docs/paper.md",
    status: "modified",
    mime_type: "text/markdown",
    binary: false,
    before_size: 100,
    after_size: 100,
  },
  {
    path: "source/figure.png",
    status: "modified",
    mime_type: "image/png",
    binary: true,
    before_size: 68,
    after_size: 68,
  },
  {
    path: "source/paper.pdf",
    status: "modified",
    mime_type: "application/pdf",
    binary: true,
    before_size: 100,
    after_size: 100,
  },
  {
    path: "artifacts/data.bin",
    status: "modified",
    mime_type: "application/octet-stream",
    binary: true,
    before_size: 2,
    after_size: 3,
  },
  {
    path: "outputs/rebuttal/reviewer-a.md",
    status: "modified",
    mime_type: "text/markdown",
    binary: false,
    before_size: 30,
    after_size: 100,
  },
  {
    path: "outputs/ac-summary.md",
    status: "modified",
    mime_type: "text/markdown",
    binary: false,
    before_size: 20,
    after_size: 40,
  },
]

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "llm4ad-history-vite-"))
  server = await createServer({
    configFile: false,
    root,
    cacheDir,
    optimizeDeps: { entries: ["tests/paper-version-history.fixture.tsx"] },
    resolve: { alias: { "@": `${root}/src`, "@docs": `${root}/../../docs` } },
    plugins: [
      tailwindcss(),
      {
        name: "version-history-fixture",
        configureServer(devServer) {
          devServer.middlewares.use("/__versions__", (_req, res) => {
            res.setHeader("content-type", "text/html")
            res.end(
              '<!doctype html><html lang="en"><head><title>Version history</title><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/paper-version-history.fixture.tsx"></script></body></html>',
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

async function open(page: Page, patch: unknown = fullPatch) {
  page.setDefaultTimeout(8000)
  const state = {
    activeSource: structuredClone(current),
    sourceVersions: structuredClone([current, old]),
    candidates: structuredClone([candidate]),
    history: structuredClone(revisions),
    mutations: [] as { path: string; body: unknown }[],
    downloads: [] as string[],
    reads: [] as string[],
    comparisons: [] as string[],
    errors: [] as string[],
    conflict: false,
    failFile: "",
    failHistory: false,
    failComparison: false,
  }
  page.on("pageerror", (failure) => state.errors.push(failure.message))
  await page.addInitScript(() => {
    localStorage.setItem("llm4ad-language", "en")
    localStorage.setItem("access_token", "history-fixture-token")
    const objects = {
      created: [] as { url: string; type: string }[],
      revoked: [] as string[],
    }
    Object.assign(window, { __historyObjects: objects })
    const create = URL.createObjectURL.bind(URL),
      revoke = URL.revokeObjectURL.bind(URL)
    URL.createObjectURL = (blob) => {
      const url = create(blob)
      objects.created.push({
        url,
        type: blob instanceof Blob ? blob.type : "worker",
      })
      return url
    }
    URL.revokeObjectURL = (url) => {
      objects.revoked.push(url)
      revoke(url)
    }
  })
  await page.route("**/__history_state__", (route) =>
    route.fulfill({
      json: {
        workspaceId: "workspace-a",
        activeSource: state.activeSource,
        sourceVersions: state.sourceVersions,
        candidates: state.candidates,
      },
    }),
  )
  await page.route("**/api/v1/llm4ad/papers/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = decodeURIComponent(url.pathname)
    assert.equal(
      request.headers().authorization,
      "Bearer history-fixture-token",
    )
    if (request.method() === "POST") {
      state.mutations.push({
        path,
        body: request.postData() ? request.postDataJSON() : null,
      })
      if (state.conflict)
        return route.fulfill({
          status: 409,
          json: { detail: "Workspace changed" },
        })
      if (path.endsWith("/reject")) {
        state.candidates[0].status = "rejected"
        return route.fulfill({
          json: { candidate_id: candidate.id, status: "rejected" },
        })
      }
      state.activeSource = {
        ...current,
        id: "source-derived",
        version: 3,
        parent_version_id: current.id,
      }
      state.sourceVersions.unshift(state.activeSource)
      if (path.endsWith("/accept"))
        state.candidates[0] = {
          ...candidate,
          status: "accepted",
          accepted_source_version_id: state.activeSource.id,
        }
      return route.fulfill({
        json: path.endsWith("/accept")
          ? { candidate_id: candidate.id, source_version: state.activeSource }
          : {
              ...revisions.revisions[0],
              id: "restored-revision",
              label: "Restored version",
            },
      })
    }
    if (path.endsWith("/patch"))
      return route.fulfill({
        json: {
          candidate_id: candidate.id,
          content: typeof patch === "string" ? patch : JSON.stringify(patch),
        },
      })
    if (path.endsWith("/history"))
      return state.failHistory
        ? route.fulfill({ status: 503, json: {} })
        : route.fulfill({ json: state.history })
    if (path.endsWith("/compare")) {
      if (state.failComparison) return route.fulfill({ status: 503, json: {} })
      state.comparisons.push(url.search)
      const files =
        url.searchParams.get("base_id") === url.searchParams.get("target_id")
          ? inventory.map((item) => ({ ...item, status: "unchanged" as const }))
          : inventory
      return route.fulfill({
        json: {
          base_id: url.searchParams.get("base_id")!,
          target_id: url.searchParams.get("target_id")!,
          files,
          counts: Object.fromEntries(
            ["added", "modified", "deleted", "unchanged"].map((status) => [
              status,
              files.filter((item) => item.status === status).length,
            ]),
          ),
        } satisfies PaperHistoryComparison,
      })
    }
    const file = url.searchParams.get("path") ?? ""
    const historical = path.includes("revision-old")
    const item = inventory.find((entry) => entry.path === file)
    if (path.endsWith("/download")) {
      state.downloads.push(`${path}?${url.searchParams}`)
      if (file.endsWith(".png"))
        return route.fulfill({
          contentType: "image/png",
          body: Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jwL8AAAAASUVORK5CYII=",
            "base64",
          ),
        })
      if (file.endsWith(".pdf"))
        return route.fulfill({
          contentType: "application/pdf",
          body: Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"),
        })
      return route.fulfill({
        contentType: "application/octet-stream",
        body: Buffer.from("historical binary bytes"),
      })
    }
    if (path.endsWith("/file")) {
      state.reads.push(`${path}?${url.searchParams}`)
      if (file === state.failFile)
        return route.fulfill({ status: 503, json: {} })
      const exists = !(historical
        ? item?.status === "added"
        : item?.status === "deleted")
      const content = file.endsWith("docs/paper.md")
        ? "# Archived paper\n\n![Evidence](../figure.png)\n\n![Blocked](../../../private/secret.png)"
        : file.endsWith("methods.typ")
          ? historical
            ? "Keep. Historical method. End."
            : "Keep. Current method. End."
          : file.includes("reviewer-a")
            ? historical
              ? "Old reviewer response."
              : "# Revised response\n\n| Claim | Evidence |\n| --- | --- |\n| Narrow | Reproduced |\n\nWe bound $x^2 + y^2$."
            : file.includes("ac-summary")
              ? historical
                ? "Old chair message."
                : "# Chair message\n\nClarified the reviewer concern."
              : file.endsWith("unchanged.txt")
                ? "Same"
                : historical
                  ? "Deleted file content"
                  : "Added file content"
      return route.fulfill({
        json: {
          path: file,
          exists,
          content: !exists || item?.binary ? null : content,
          binary: item?.binary ?? false,
          mime_type: item?.mime_type ?? "text/plain",
          download_url: `${url.pathname.replace(/file$/, "download")}?${url.searchParams}`,
        } satisfies PaperHistoryFile,
      })
    }
    return route.fulfill({ status: 404, json: {} })
  })
  await page.goto(`${origin}/__versions__`)
  await expect(
    page.getByRole("button", { name: "Version history", exact: false }),
  ).toBeVisible({ timeout: 30000 })
  return state
}
const dialog = (page: Page) => page.getByTestId("paper-version-history")
async function showHistory(page: Page) {
  await page
    .getByRole("button", { name: "Version history", exact: false })
    .click()
  await expect(dialog(page).getByLabel("Baseline version")).toHaveValue(
    "revision-old",
  )
  return dialog(page)
}
async function selectFile(page: Page, path: string) {
  const treeButton = dialog(page).getByRole("button", {
    name: "Show file tree",
    exact: true,
  })
  if (await treeButton.count()) await treeButton.click()
  const row = dialog(page).getByRole("button", { name: path, exact: true })
  await row.click()
  await expect(row).toHaveAttribute("aria-pressed", "true")
}
async function showCandidate(page: Page) {
  const view = await showHistory(page)
  await view
    .getByLabel("Pending suggestions", { exact: true })
    .selectOption(candidate.id)
  return view
}

test("history opens directly as a large dialog with lazy files and a real status tree", async () => {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  })
  try {
    const state = await open(page),
      view = await showHistory(page)
    await expect(view.getByLabel("Target version")).toHaveValue(
      "revision-current",
    )
    await expect(
      view.getByTestId("history-text-diff").locator(".monaco-diff-editor"),
    ).toBeVisible({ timeout: 60000 })
    const box = await view.boundingBox()
    assert.ok(box && box.width > 1300 && box.height <= 910 && box.height > 850)
    assert.equal(await page.locator('[data-slot="popover-content"]').count(), 0)
    assert.equal(state.reads.length, 2)
    assert.ok(state.reads.every((request) => request.includes("methods.typ")))
    assert.ok(
      state.comparisons[0].includes("base_id=revision-old") &&
        state.comparisons[0].includes("target_id=revision-current"),
    )
    await expect(
      view.getByRole("button", { name: "source/added.md", exact: true }),
    ).toHaveAttribute("data-history-status", "Added file")
    await expect(
      view.getByRole("button", { name: "source/deleted.md", exact: true }),
    ).toHaveAttribute("data-history-status", "Deleted file")
    await expect(
      view.getByRole("button", { name: "source/unchanged.txt", exact: true }),
    ).toHaveCount(0)
    await view.getByLabel("Changed files only").uncheck()
    await expect(
      view.getByRole("button", { name: "source/unchanged.txt", exact: true }),
    ).toHaveAttribute("data-history-status", "Unchanged")
    assert.equal(state.reads.length, 2)
    await page.screenshot({ path: "/tmp/paper-history-desktop.png" })
    assert.deepEqual(state.errors, [])
  } finally {
    await page.close()
  }
})

test("Monaco is read-only, narrow layouts use inline diffs, and the file tree collapses", async () => {
  const page = await browser.newPage({ viewport: { width: 520, height: 900 } })
  try {
    const state = await open(page),
      view = await showHistory(page)
    await expect(
      view.getByTestId("history-text-diff").locator(".monaco-diff-editor"),
    ).toBeVisible({ timeout: 60000 })
    await expect(view.getByTestId("history-text-diff")).toHaveAttribute(
      "data-inline",
      "true",
    )
    await expect(
      view.getByRole("button", { name: "Show file tree" }),
    ).toBeVisible()
    await selectFile(page, "source/added.md")
    await view.getByRole("button", { name: "Hide file tree" }).click()
    await expect(
      view.getByRole("complementary", { name: "Workspace files" }),
    ).toHaveCount(0)
    const textarea = view.locator(".monaco-editor textarea").first()
    assert.ok(
      await textarea.evaluate((element) => element.hasAttribute("readonly")),
    )
    assert.equal(state.mutations.length, 0)
    assert.ok((await view.boundingBox())!.width < 520)
    await page.screenshot({ path: "/tmp/paper-history-narrow.png" })
  } finally {
    await page.close()
  }
})

test("rendered replies and chair messages retain Markdown tables and math", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    const view = await showHistory(page)
    await selectFile(page, "outputs/rebuttal/reviewer-a.md")
    await view.getByRole("button", { name: "Preview", exact: true }).click()
    await expect(view.getByRole("table")).toContainText("Reproduced")
    await expect(view.locator(".katex")).toHaveCount(1)
    await expect(
      view.getByRole("heading", { name: "Revised response" }),
    ).toBeVisible()
    await selectFile(page, "outputs/ac-summary.md")
    await expect(
      view.getByRole("heading", { name: "Chair message" }),
    ).toBeVisible()
  } finally {
    await page.close()
  }
})

test("image/PDF previews use authenticated blobs and release image URLs when leaving the file", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    await selectFile(page, "source/figure.png")
    await expect(
      view.getByRole("img", { name: "Before — source/figure.png" }),
    ).toHaveAttribute("src", /^blob:/)
    await expect(
      view.getByRole("img", { name: "After — source/figure.png" }),
    ).toHaveAttribute("src", /^blob:/)
    assert.equal(state.downloads.length, 2)
    const imageUrls = await page.evaluate(() =>
      (
        window as unknown as {
          __historyObjects: { created: { url: string; type: string }[] }
        }
      ).__historyObjects.created
        .filter((item) => item.type === "image/png")
        .map((item) => item.url),
    )
    assert.equal(imageUrls.length, 2)
    await selectFile(page, "source/paper.pdf")
    await expect(
      view.locator('iframe[title="Before — source/paper.pdf"]'),
    ).toHaveAttribute("src", /^blob:.*#view=FitH/)
    const revoked = await page.evaluate(
      () =>
        (window as unknown as { __historyObjects: { revoked: string[] } })
          .__historyObjects.revoked,
    )
    assert.ok(imageUrls.every((url) => revoked.includes(url)))
    await page.keyboard.press("Escape")
    await expect(view).toHaveCount(0)
  } finally {
    await page.close()
  }
})

test("other binaries offer authenticated per-side downloads without eager binary reads", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    await selectFile(page, "artifacts/data.bin")
    await expect(
      view.getByText(/A text diff is unavailable for this file/),
    ).toBeVisible()
    assert.equal(state.downloads.length, 0)
    const downloaded = page.waitForEvent("download")
    await view.getByRole("button", { name: "Download before" }).click()
    assert.equal((await downloaded).suggestedFilename(), "data.bin")
    assert.ok(state.downloads[0].includes("revision-old/download"))
  } finally {
    await page.close()
  }
})

test("arbitrary versions compare and restore creates a guarded new version after confirmation", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    await view.getByLabel("Target version").selectOption("revision-old")
    await expect(view.getByText(/No changed files/)).toBeVisible()
    await view.getByLabel("Changed files only").uncheck()
    await selectFile(page, "source/sections/methods.typ")
    await expect(
      view.getByTestId("history-text-diff").locator(".monaco-diff-editor"),
    ).toBeVisible({ timeout: 60000 })
    await view
      .getByRole("button", { name: "Restore Version 1 as new version" })
      .click()
    assert.equal(state.mutations.length, 0)
    await page
      .getByRole("dialog", { name: "Restore Version 1?", exact: true })
      .getByRole("button", { name: "Confirm restore" })
      .click()
    await expect(page.getByTestId("changes")).toHaveText("1")
    assert.equal(state.mutations.length, 1)
    assert.ok(state.mutations[0].path.endsWith("/history/revision-old/restore"))
    assert.deepEqual(state.mutations[0].body, {
      expected_state_hash: hash,
      expected_revision_id: "revision-current",
    })
  } finally {
    await page.close()
  }
})

test("a source change blocks decisions until explicit refresh and uses the refreshed guard", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    await expect(view.getByTestId("history-text-diff")).toBeVisible({
      timeout: 60000,
    })
    state.history.current_state_hash = "b".repeat(64)
    state.history.revisions[0].state_hash = state.history.current_state_hash
    await page
      .getByRole("button", {
        name: "Advance current source",
        includeHidden: true,
      })
      .evaluate((element: HTMLButtonElement) => element.click())
    await expect(view.getByRole("alert")).toContainText("workspace changed")
    await expect(
      view.getByRole("button", { name: "Restore Version 1 as new version" }),
    ).toBeDisabled()
    await view.getByRole("button", { name: "Refresh history" }).first().click()
    await expect(view.getByRole("alert")).toHaveCount(0)
    await view
      .getByRole("button", { name: "Restore Version 1 as new version" })
      .click()
    await page
      .getByRole("dialog", { name: "Restore Version 1?", exact: true })
      .getByRole("button", { name: "Confirm restore" })
      .click()
    await expect(page.getByTestId("changes")).toHaveText("1")
    assert.deepEqual(state.mutations[0].body, {
      expected_state_hash: "b".repeat(64),
      expected_revision_id: "revision-current",
    })
  } finally {
    await page.close()
  }
})

test("restore conflict retains the history, alerts, and never repeats the mutation", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    await expect(
      view.getByRole("button", { name: "Restore Version 1 as new version" }),
    ).toBeEnabled()
    state.conflict = true
    await view
      .getByRole("button", { name: "Restore Version 1 as new version" })
      .click()
    await page
      .getByRole("dialog", { name: "Restore Version 1?", exact: true })
      .getByRole("button", { name: "Confirm restore" })
      .click()
    await expect(view.getByRole("alert").first()).toContainText(
      "workspace changed",
    )
    await expect(
      view.getByRole("button", { name: "Restore Version 1 as new version" }),
    ).toBeDisabled()
    assert.equal(state.mutations.length, 1)
    assert.equal(state.activeSource.id, current.id)
  } finally {
    await page.close()
  }
})

test("candidate review preserves added/deleted/binary patches and approval remains explicit", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showCandidate(page)
    await selectFile(page, "sections/methods.typ")
    await expect(
      view.getByTestId("history-text-diff").locator(".monaco-diff-editor"),
    ).toBeVisible({ timeout: 60000 })
    await selectFile(page, "references.bib")
    await expect(view.getByText("Added file", { exact: true })).toBeVisible()
    await selectFile(page, "obsolete.md")
    await expect(view.getByText("Deleted file", { exact: true })).toBeVisible()
    await selectFile(page, "image.png")
    await expect(view.getByText("2 → 3 B")).toBeVisible()
    assert.equal(state.mutations.length, 0)
    await view.getByRole("button", { name: "Approve all changes" }).click()
    await expect(page.getByTestId("changes")).toHaveText("1")
    assert.ok(state.mutations[0].path.endsWith("candidate-a/accept"))
  } finally {
    await page.close()
  }
})

test("malformed candidates block approval but remain rejectable", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, "not JSON"),
      view = await showCandidate(page)
    await expect(view.getByRole("alert")).toContainText(
      "Could not load a readable diff",
    )
    await expect(
      view.getByRole("button", { name: "Approve all changes" }),
    ).toBeDisabled()
    await view.getByRole("button", { name: "Reject suggestion" }).click()
    await expect(page.getByTestId("changes")).toHaveText("1")
    assert.equal(state.activeSource.id, current.id)
    assert.ok(state.mutations[0].path.endsWith("candidate-a/reject"))
  } finally {
    await page.close()
  }
})

test("failed selected-file reads retry without changing the version pair or other files", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    state.failFile = "outputs/ac-summary.md"
    await selectFile(page, state.failFile)
    await expect(view.getByRole("alert")).toContainText(
      "Could not load this file",
    )
    state.failFile = ""
    await view.getByRole("button", { name: "Retry", exact: true }).click()
    await expect(view.getByTestId("history-text-diff")).toBeVisible()
    await expect(view.getByLabel("Baseline version")).toHaveValue(
      "revision-old",
    )
    assert.equal(state.mutations.length, 0)
  } finally {
    await page.close()
  }
})

test("legacy fragment candidates remain reviewable and source changes block only approval", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, {
      source_path: "sections/methods.typ",
      original_text: "Fragment before",
      replacement_text: "Fragment after",
    })
    const view = await showCandidate(page)
    await expect(view.getByTestId("history-text-diff")).toBeVisible({
      timeout: 60000,
    })
    await view.getByRole("button", { name: "Preview", exact: true }).click()
    await expect(
      view.getByText("Fragment after", { exact: true }),
    ).toBeVisible()
    await page
      .getByRole("button", {
        name: "Advance current source",
        includeHidden: true,
      })
      .evaluate((element: HTMLButtonElement) => element.click())
    await expect(
      view.getByRole("button", { name: "Approve all changes" }),
    ).toBeDisabled()
    await expect(
      view.getByRole("button", { name: "Reject suggestion" }),
    ).toBeEnabled()
    await view.getByRole("button", { name: "Reject suggestion" }).click()
    await expect(page.getByTestId("changes")).toHaveText("1")
    assert.ok(state.mutations[0].path.endsWith("candidate-a/reject"))
  } finally {
    await page.close()
  }
})

test("history metadata failures can retry and empty history remains safe", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page)
    state.failHistory = true
    await page
      .getByRole("button", { name: "Version history", exact: false })
      .click()
    await expect(dialog(page).getByRole("alert")).toContainText(
      "Could not load version history",
    )
    state.failHistory = false
    await dialog(page).getByRole("button", { name: "Retry" }).click()
    await expect(dialog(page).getByLabel("Baseline version")).toHaveValue(
      "revision-old",
    )
    await page.keyboard.press("Escape")
    state.history.revisions = []
    state.history.current_revision_id = null
    await page
      .getByRole("button", { name: "Version history", exact: false })
      .click()
    await expect(
      dialog(page).getByText("No versions are available yet.", { exact: true }),
    ).toBeVisible()
    assert.equal(state.mutations.length, 0)
  } finally {
    await page.close()
  }
})

test("the editor follows the existing theme and never uses a CDN worker", async () => {
  const page = await browser.newPage()
  try {
    const requests: string[] = []
    page.on("request", (request) => requests.push(request.url()))
    await open(page)
    const view = await showHistory(page)
    await expect(
      view.getByTestId("history-text-diff").locator(".monaco-diff-editor"),
    ).toBeVisible({ timeout: 60000 })
    await page
      .getByRole("button", { name: "Dark theme", includeHidden: true })
      .evaluate((element: HTMLButtonElement) => element.click())
    await expect(view.locator(".monaco-editor.vs-dark").first()).toBeVisible()
    assert.ok(
      !requests.some((url) => /cdn\.jsdelivr|unpkg\.com|cdnjs/.test(url)),
    )
    assert.ok(
      requests.some((url) =>
        /editor[./]editor\.worker|editor\.worker/.test(url),
      ),
    )
  } finally {
    await page.close()
  }
})

test("Markdown images resolve against each archived revision and cannot escape the source project", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page),
      view = await showHistory(page)
    await selectFile(page, "source/docs/paper.md")
    await view.getByRole("button", { name: "Preview", exact: true }).click()
    await expect(
      view.getByRole("img", { name: "Evidence", exact: true }),
    ).toHaveCount(2)
    for (const image of await view
      .getByRole("img", { name: "Evidence", exact: true })
      .all())
      await expect(image).toHaveAttribute("src", /^blob:/)
    assert.equal(state.downloads.length, 2)
    assert.ok(
      state.downloads.some((request) =>
        request.includes("revision-old/download"),
      ),
    )
    assert.ok(
      state.downloads.some((request) =>
        request.includes("revision-current/download"),
      ),
    )
    assert.ok(
      state.downloads.every(
        (request) =>
          new URL(`${origin}${request}`).searchParams.get("path") ===
          "source/figure.png",
      ),
    )
    assert.ok(!state.reads.some((request) => request.includes("secret")))
    assert.deepEqual(state.errors, [])
  } finally {
    await page.close()
  }
})

test("comparison retries fetch only the selected file after metadata succeeds", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page)
    state.failComparison = true
    const view = await showHistory(page)
    await expect(view.getByRole("alert")).toContainText(
      "Could not load this file",
    )
    assert.equal(state.reads.length, 0)
    state.failComparison = false
    await view.getByRole("button", { name: "Retry", exact: true }).click()
    await expect(view.getByTestId("history-text-diff")).toBeVisible({
      timeout: 60000,
    })
    assert.equal(state.reads.length, 2)
    assert.ok(
      state.reads.every(
        (request) =>
          new URL(`${origin}${request}`).searchParams.get("path") ===
          "source/sections/methods.typ",
      ),
    )
    assert.equal(state.mutations.length, 0)
  } finally {
    await page.close()
  }
})
