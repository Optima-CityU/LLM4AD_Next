import assert from "node:assert/strict"
import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { type Browser, chromium, expect, type Page } from "@playwright/test"
import tailwindcss from "@tailwindcss/vite"
import { createServer, transformWithEsbuild, type ViteDevServer } from "vite"
import en from "../src/i18n/locales/en.json" with { type: "json" }
import type { ResearchAgentProgress } from "../src/lib/researchAgentProgress"

let server: ViteDevServer
let browser: Browser
let origin: string
let cacheDir: string
const root = fileURLToPath(new URL("..", import.meta.url))

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "llm4ad-proposal-document-"))
  const route = await readFile(
    join(root, "src/routes/_layout/papers.tsx"),
    "utf8",
  )
  const start = route.indexOf("  const sourceWorkspace = useRef")
  const end = route.indexOf("  const selectProposalPath = async", start)
  assert.ok(start > 0 && end > start)
  const saveModule = await transformWithEsbuild(
    `
    import {useRef} from "react"
    import {proposalDocumentEntryPath} from "@/components/Paper/ProposalDocumentPreview"
    import {proposalChapters} from "@/lib/proposalDocument"
    import {useProposalRender} from "@/hooks/useProposalRender"
    export function useRouteSourceSave({t, workspaceId, workspaceQuery, workspace, activeSource, selectedPath,
      selectedPathEditable, currentSourceDraft, sourceServerSnapshot, submittedSourceDraft, updateSourceFile,
      setSourceAutoSaveError, chapters, proposalEntryPath}) {
      ${route.slice(start, end)}
      return {flushProposalSource, proposalRender}
    }
  `,
    "proposal-route-save.tsx",
    { loader: "tsx", jsx: "automatic" },
  )
  server = await createServer({
    configFile: false,
    root,
    cacheDir,
    optimizeDeps: {
      entries: [
        "tests/proposal-document.fixture.tsx",
        "tests/proposal-route.fixture.tsx",
      ],
      // Prebundle lazy editor dependencies before interactions can be reset by a dev reload.
      include: [
        "@hookform/resolvers/zod",
        "react-hook-form",
        "date-fns",
        "react-dom",
        "@tanstack/react-table",
        "@tanstack/react-virtual",
        "react-icons/si",
        "react-error-boundary",
        "echarts-for-react",
        "echarts-gl",
        "js-yaml",
        "d3",
      ],
    },
    resolve: { alias: { "@": `${root}/src`, "@docs": `${root}/../../docs` } },
    plugins: [
      tailwindcss(),
      {
        name: "proposal-document-fixture",
        resolveId: (id) =>
          id === "virtual:proposal-route-save"
            ? "\0proposal-route-save"
            : undefined,
        load(id) {
          if (id === "\0proposal-route-save") return saveModule.code
          if (id.endsWith("/components/Paper/PaperNativeRuntime.tsx"))
            return `
              import React, { useEffect } from "react";
              export default function Assistant({onAgentProgress, pendingRequest}) {
                useEffect(() => {
                  if (window.proposalTestProgress)
                    onAgentProgress(window.proposalTestProgress);
                }, [onAgentProgress]);
                return React.createElement("textarea", {
                  "aria-label": "Assistant message",
                  "data-request": pendingRequest?.prompt,
                });
              }
            `
          if (
            /\/components\/Paper\/(AlgorithmStagePanel|RebuttalEntriesPanel)\.tsx$/.test(
              id,
            )
          )
            return "export default function UnrelatedPanel(){return null}"

          if (id.endsWith("/components/AutoResearch/ArtifactCodeEditor.tsx"))
            return 'import React from "react"; export default function Editor({value,readOnly,onChange,filePath}) {return React.createElement("textarea", {"aria-label":"Source editor", "data-path":filePath,value,readOnly,onChange:event=>onChange?.(event.target.value)})}'
        },
        configureServer(devServer) {
          devServer.middlewares.use("/__proposal_route__", (_req, res) => {
            res.setHeader("content-type", "text/html")
            res.end(
              '<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Actual proposal route</title></head><body><div id="root"></div><script type="module" src="/tests/proposal-route.fixture.tsx"></script></body></html>',
            )
          })
          devServer.middlewares.use("/__proposal__", (_req, res) => {
            res.setHeader("content-type", "text/html")
            res.end(
              '<!doctype html><html lang="en"><head><title>Proposal</title><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/proposal-document.fixture.tsx"></script></body></html>',
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
    executablePath:
      process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ||
      "/usr/bin/google-chrome",
  })
})
after(async () => {
  await browser?.close()
  await server?.close()
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true })
})

type Options = {
  chapters?: boolean
  legacy?: boolean
  stored?: boolean
  failed?: boolean
  foreign?: boolean
  mismatch?: boolean
  entry?: string
  saveFails?: boolean
  lang?: string
  holdSave?: Promise<void>
}
async function open(page: Page, options: Options = {}) {
  const entry = options.legacy
    ? "proposal.typ"
    : options.chapters
      ? "01-project.md"
      : (options.entry ?? "proposal.md")
  const chapters = options.chapters
    ? Array.from({ length: 9 }, (_, i) => ({
        id: `chapter-${i + 1}`,
        title: `Research chapter ${i + 1}`,
        path: i === 0 ? entry : `0${i + 1}-chapter.md`,
        status: i === 0 ? "not_started" : null,
      }))
    : []
  const entryPaths = chapters.length
    ? chapters.map((chapter) => chapter.path)
    : [entry]
  const state = {
    sourceId: "source-a",
    hash: "hash-a",
    published: false,
    failRender: options.failed ?? false,
    gets: [] as string[],
    renders: [] as {
      id: string
      body: { entry_paths: string[]; expected_source_hash: string }
    }[],
    saves: [] as { id: string; path: string; content: string }[],
    assets: [] as string[],
    resources: [] as { revision: string; path: string }[],
    contents: Object.fromEntries(
      entryPaths.map((path, i) => [
        path,
        options.legacy
          ? "= Legacy proposal\nAuthor source."
          : i > 0
            ? `# Research chapter ${i + 1}\n\nChapter content ${i + 1}.`
            : `# Actual research proposal\n\nSaved author wording with $x^2 + y^2$.\n\n| Method | Result |\n| --- | --- |\n| Baseline | 1 |\n\n$$\nE = mc^2\n$$\n\n![Revision figure](${entry.includes("/") ? "../" : ""}assets/figure.png)\n\n[Measurements](${entry.includes("/") ? "../" : ""}assets/data.csv)\n\n![Invalid image](../../outside.png)`,
      ]),
    ),
  }
  const result = (id: string) => ({
    status: state.failRender ? "failed" : "ready",
    source_version_id: id,
    source_hash: options.mismatch
      ? "wrong-hash"
      : id === "source-a"
        ? "hash-a"
        : "hash-b",
    entry_path: entry,
    entry_paths: entryPaths,
    files: [],
    diagnostics: state.failRender
      ? [
          {
            message: "Unclosed math expression",
            path: entry,
            line: 7,
            severity: "error",
          },
        ]
      : [],
    pdf_url: state.failRender
      ? null
      : `/api/v1/llm4ad/papers/source-versions/${options.foreign ? "foreign-source" : id}/render/proposal.pdf`,
    typst_url: state.failRender
      ? null
      : `/api/v1/llm4ad/papers/source-versions/${id}/render/proposal.typ`,
  })
  await page.addInitScript((language) => {
    if (window !== window.top) return
    localStorage.setItem("llm4ad-language", language)
    localStorage.setItem("access_token", "proposal-test-token")
  }, options.lang ?? "en")
  await page.route("**/api/v1/llm4ad/papers/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    assert.equal(request.headers().authorization, "Bearer proposal-test-token")
    const id = url.pathname.split("/source-versions/")[1]?.split("/")[0]
    if (url.pathname.endsWith("/workspaces/workspace-a"))
      return route.fulfill({
        json: {
          id: "workspace-a",
          mode: "proposal",
          proposal_entry_path: entry,
          proposal_chapters: chapters,
          active_source_version_id: state.sourceId,
          source_versions: [
            {
              id: state.sourceId,
              content_hash: state.hash,
              manifest: [
                ...entryPaths,
                "proposal-state.json",
                "progress.log",
                "styles/proposal.typ",
                "project_context/reference.md",
              ],
            },
          ],
          workflow_available: false,
        },
      })
    if (url.pathname.endsWith("/file")) {
      if (request.method() === "GET") {
        const path = url.searchParams.get("path")!
        return route.fulfill({
          json: {
            source_version_id: id,
            path,
            content: state.contents[path] ?? "Reference material",
          },
        })
      }
      assert.equal(request.method(), "PUT")
      const body = request.postDataJSON()
      state.saves.push({ id, ...body })
      if (options.holdSave) await options.holdSave
      if (options.saveFails)
        return route.fulfill({ status: 500, json: { detail: "Save rejected" } })
      state.contents[body.path] = body.content
      state.sourceId = "source-b"
      state.hash = "hash-b"
      return route.fulfill({
        json: {
          id: state.sourceId,
          content_hash: state.hash,
          manifest: entryPaths,
        },
      })
    }
    if (url.pathname.endsWith("/render")) {
      assert.deepEqual(url.searchParams.getAll("entry_paths"), entryPaths)
      if (request.method() === "GET") {
        state.gets.push(id)
        return route.fulfill({
          json:
            options.stored ||
            state.published ||
            state.renders.some((render) => render.id === id)
              ? result(id)
              : null,
        })
      }
      assert.equal(request.method(), "POST")
      state.renders.push({ id, body: request.postDataJSON() })
      return route.fulfill({ json: result(id) })
    }
    if (url.pathname.endsWith("/proposal.pdf")) {
      state.assets.push(id)
      return route.fulfill({
        contentType: "application/pdf",
        body: `%PDF-1.4\n${id}\n%%EOF`,
      })
    }
    if (url.pathname.endsWith("/proposal.typ"))
      return route.fulfill({
        contentType: "text/plain",
        body: `= Rendered ${id}\nRead-only final source`,
      })
    if (url.pathname.endsWith("/regions"))
      return route.fulfill({
        json: {
          workspace_id: "workspace-a",
          regions: entryPaths.map((path) => ({
            id: `proposal:${path}`,
            kind: "proposal_section",
            title: path,
            content: state.contents[path],
            revision: "r1",
            source_refs: [],
          })),
          requests: [],
        },
      })
    if (
      url.pathname.includes("/history/") &&
      url.pathname.endsWith("/download")
    ) {
      const revision = decodeURIComponent(
        url.pathname.split("/history/")[1].split("/")[0],
      )
      const path = url.searchParams.get("path")!
      state.resources.push({ revision, path })
      if (path === "source/assets/figure.png")
        return route.fulfill({
          contentType: "image/png",
          body: Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aO0cAAAAASUVORK5CYII=",
            "base64",
          ),
        })
      if (path === "source/assets/data.csv")
        return route.fulfill({
          contentType: "text/csv",
          body: "method,result\nbaseline,1\n",
        })
      throw new Error(`Resource escaped source: ${path}`)
    }
    throw new Error(`Unexpected request: ${request.method()} ${url}`)
  })
  await page.goto(`${origin}/__proposal__`)
  await server.waitForRequestsIdle()
  await page.waitForLoadState("networkidle")
  await expect(page.getByTestId("proposal-results-dock")).toBeVisible()
  await expect(
    page.getByRole("button", {
      name: options.lang === "zh" ? "确认并排版" : "Confirm and typeset",
    }),
  ).toBeVisible()
  return state
}

async function edit(page: Page, content: string) {
  await page.getByRole("button", { name: "Edit chapter", exact: true }).click()
  await page
    .getByRole("textbox", { name: "Source editor", exact: true })
    .fill(content)
}

async function selectView(page: Page, name: string) {
  const nav = page.getByRole("tablist", { name: "Project navigation" })
  await nav.getByRole("tab", { name: new RegExp(`^${name}`) }).click()
  if (name === "Document") {
    const markdown = page.getByRole("tab", { name: "Markdown", exact: true })
    if (await markdown.isVisible()) await markdown.click()
  }
}

async function showPdf(page: Page) {
  await selectView(page, "Document")
  await page.getByRole("tab", { name: "PDF", exact: true }).click()
}

async function confirm(page: Page) {
  const action = page.getByRole("button", {
    name: "Confirm and typeset",
    exact: true,
  })
  if (!(await action.isVisible())) await selectView(page, "Document")
  await action.click()
}

test("nine ordered chapters have visible navigation with Markdown, math, history and semantic progress", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  try {
    const state = await open(page, { chapters: true })
    const doc = page.getByTestId("proposal-document")
    await expect(
      doc.getByRole("heading", { name: "Actual research proposal" }),
    ).toBeVisible()
    await expect(doc.getByRole("table")).toBeVisible()
    await expect(page.getByText("Not started", { exact: true })).toBeVisible()
    assert.equal(await doc.locator(".katex").count(), 2)
    await expect(page.getByRole("tablist", { name: "Proposal" })).toHaveCount(0)
    const header = await page.getByTestId("proposal-toolbar").boundingBox()
    assert.ok(header && header.height <= 64)
    await expect(
      page.getByRole("tab", { name: /Files|Stage|Results/ }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("tab", { name: "Writing steps" }),
    ).toHaveAttribute("title", "Checking the actual method")
    await expect(
      page.getByRole("button", { name: "History", exact: true }),
    ).toBeVisible()
    await page.getByRole("button", { name: "Chapters", exact: true }).click()
    const chapters = page.getByRole("list", { name: "Chapters" })
    await expect(chapters.getByRole("button")).toHaveCount(9)
    await chapters.getByRole("button", { name: "Research chapter 9" }).click()
    await expect(doc).toContainText("Chapter content 9")
    await page
      .getByText(en.paper.proposalDocument.regionalEditing, { exact: true })
      .click()
    await expect(page.getByTestId("paper-region-workbench")).toBeVisible()
    await selectView(page, "References")
    await selectView(page, "Writing steps")
    await expect(page.getByText(/Only actual semantic progress/)).toBeVisible()
    await selectView(page, "Editor")
    await expect(
      page.getByRole("textbox", { name: "Source editor", exact: true }),
    ).toBeVisible()
    assert.deepEqual(state.renders, [])
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("confirmation saves the current chapter first and POSTs the latest hash and all nine paths in order", async () => {
  const page = await browser.newPage()
  let release!: () => void
  const holdSave = new Promise<void>((resolve) => {
    release = resolve
  })
  try {
    const state = await open(page, { chapters: true, holdSave })
    await edit(
      page,
      "# Latest author change\n\nMust be saved before rendering.",
    )
    await confirm(page)
    await expect.poll(() => state.saves.length).toBe(1)
    assert.deepEqual(state.renders, [])
    release()
    await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible()
    assert.equal(state.saves[0].path, "01-project.md")
    assert.match(state.saves[0].content, /Latest author change/)
    assert.deepEqual(state.renders, [
      {
        id: "source-b",
        body: {
          entry_paths: [
            "01-project.md",
            ...Array.from({ length: 8 }, (_, i) => `0${i + 2}-chapter.md`),
          ],
          expected_source_hash: "hash-b",
        },
      },
    ])
    await selectView(page, "Document")
    await showPdf(page)
    assert.equal(state.renders.length, 1)
  } finally {
    release()
    await page.close()
  }
})

test("confirmation waits for an autosave already in flight without duplicating it", async () => {
  const page = await browser.newPage()
  let release!: () => void
  const holdSave = new Promise<void>((resolve) => {
    release = resolve
  })
  try {
    const state = await open(page, { holdSave })
    await edit(page, "# Pending autosave")
    await page.getByRole("button", { name: "Start autosave" }).click()
    await expect.poll(() => state.saves.length).toBe(1)
    await confirm(page)
    assert.deepEqual(state.renders, [])
    release()
    await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible()
    assert.equal(state.saves.length, 1)
    assert.equal(state.renders[0].id, "source-b")
  } finally {
    release()
    await page.close()
  }
})

test("a failed save blocks rendering and preserves the author draft", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, { saveFails: true })
    await edit(page, "# Unsaved author wording")
    await confirm(page)
    await expect(page.getByRole("alert")).toContainText(
      en.paper.proposalDocument.renderFailed,
    )
    assert.equal(state.saves.length, 1)
    assert.deepEqual(state.renders, [])
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Unsaved author wording",
    )
  } finally {
    await page.close()
  }
})

test("stored layouts use GET only; final PDF and Typst downloads share the saved revision", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, { stored: true })
    await showPdf(page)
    const pdf = page.getByRole("link", { name: "Download PDF" })
    await expect(pdf).toBeVisible()
    const url = await pdf.getAttribute("href")
    assert.equal(
      await page.locator('iframe[title="Proposal PDF"]').getAttribute("src"),
      url,
    )
    const downloaded = page.waitForEvent("download")
    await pdf.click()
    assert.match(
      await readFile((await (await downloaded).path())!, "utf8"),
      /source-a/,
    )
    const typst = page.getByRole("link", { name: "Download Typst" })
    const typstDownload = page.waitForEvent("download")
    await typst.click()
    assert.match(
      await readFile((await (await typstDownload).path())!, "utf8"),
      /Rendered source-a/,
    )
    await expect(
      page
        .getByRole("tablist", { name: "Project navigation" })
        .getByRole("tab", {
          name: "Document",
          exact: true,
        }),
    ).toHaveAttribute("aria-selected", "true")
    await expect(
      page.getByRole("button", { name: "Layout preview" }),
    ).toHaveCount(0)
    await page.getByRole("tab", { name: "Markdown", exact: true }).click()
    await expect(page.getByTestId("proposal-document")).toBeVisible()
    assert.deepEqual(state.renders, [])
    assert.deepEqual(state.gets, ["source-a"])
  } finally {
    await page.close()
  }
})

test("source publication marks the retained layout old without compiling; a later failed render retains earlier exports", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, { stored: true })
    await showPdf(page)
    const pdf = page.getByRole("link", { name: "Download PDF" })
    await expect(pdf).toBeVisible()
    const previous = await pdf.getAttribute("href")
    state.sourceId = "source-b"
    state.hash = "hash-b"
    // The next revision has no stored output until the explicit attempt.
    state.published = false
    await page.route("**/source-versions/source-b/render?**", async (route) => {
      if (route.request().method() === "GET")
        return route.fulfill({ json: null })
      await route.fallback()
    })
    await page.getByRole("button", { name: "Receive publication" }).click()
    await expect(
      page.getByText(en.paper.proposalDocument.oldPreview),
    ).toBeVisible()
    await expect(pdf).toHaveAttribute("href", previous!)
    assert.deepEqual(state.renders, [])
    state.failRender = true
    await confirm(page)
    await expect(page.getByRole("alert")).toContainText(
      en.paper.proposalDocument.renderFailed,
    )
    await expect(pdf).toHaveAttribute("href", previous!)
    await expect(page.getByText("Unclosed math expression")).toBeVisible()
    assert.equal(state.renders.length, 1)
    await selectView(page, "Writing steps")
    await showPdf(page)
    assert.equal(state.renders.length, 1)
    state.failRender = false
    await page
      .getByRole("button", { name: "Retry PDF generation", exact: true })
      .click()
    await expect.poll(() => state.renders.length).toBe(2)
    await expect(
      page.getByText(en.paper.proposalDocument.oldPreview),
    ).toHaveCount(0)
    await expect(page.getByText("hash-b", { exact: true })).toBeVisible()
  } finally {
    await page.close()
  }
})

for (const scenario of ["mismatch", "foreign"] as const)
  test(`${scenario} render never exposes a PDF for another revision`, async () => {
    const page = await browser.newPage()
    try {
      const state = await open(page, { [scenario]: true })
      await confirm(page)
      await expect(page.getByRole("alert")).toContainText(
        en.paper.proposalDocument.renderFailed,
      )
      await expect(
        page.getByRole("link", { name: "Download PDF" }),
      ).toHaveCount(0)
      assert.deepEqual(state.assets, [])
    } finally {
      await page.close()
    }
  })

for (const entry of ["proposal.md", "sections/proposal.md"])
  test(`legacy single Markdown ${entry} retains authenticated images, attachments and passage editing`, async () => {
    const page = await browser.newPage()
    try {
      const state = await open(page, { entry })
      const image = page.getByRole("img", {
        name: "Revision figure",
        exact: true,
      })
      await expect(image).toHaveAttribute("src", /^blob:/)
      await expect
        .poll(() =>
          image.evaluate((element: HTMLImageElement) => element.naturalWidth),
        )
        .toBe(1)
      assert.deepEqual(state.resources, [
        { revision: "source:source-a", path: "source/assets/figure.png" },
      ])
      const downloaded = page.waitForEvent("download")
      await page
        .getByRole("link", { name: "Measurements", exact: true })
        .click()
      const attachment = await downloaded
      assert.equal(attachment.suggestedFilename(), "data.csv")
      assert.match(
        await readFile((await attachment.path())!, "utf8"),
        /baseline,1/,
      )
      await expect(
        page.getByRole("img", { name: "Invalid image", exact: true }),
      ).toContainText("unavailable")
      assert.deepEqual(
        JSON.parse((await page.getByTestId("asset-paths").textContent())!),
        [
          "source/assets/figure.png",
          "source/assets/figure.png",
          "source/sections/plot one.svg",
          null,
          null,
          null,
        ],
      )
      await page.getByRole("button", { name: "Chapters", exact: true }).click()
      await expect(
        page.getByRole("list", { name: "Chapters" }).getByRole("button"),
      ).toHaveCount(1)
      assert.deepEqual(state.renders, [])
    } finally {
      await page.close()
    }
  })

test("legacy Typst opens its source editor without stage cards and retains explicit layout", async () => {
  const page = await browser.newPage()
  try {
    const state = await open(page, { legacy: true })
    await expect(
      page.getByRole("textbox", { name: "Existing Typst editor" }),
    ).toHaveValue("= Legacy proposal\nAuthor source.")
    await expect(page.getByText(/Stage 0|Legacy formatting/)).toHaveCount(0)
    assert.deepEqual(state.renders, [])
    await confirm(page)
    await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible()
    assert.deepEqual(state.renders[0].body.entry_paths, ["proposal.typ"])
  } finally {
    await page.close()
  }
})

test("Chinese navigation and confirmation use localized labels on a narrow screen", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  try {
    const state = await open(page, { lang: "zh", chapters: true })
    await expect(page.getByRole("tablist", { name: "申请书" })).toHaveCount(0)
    const header = await page.getByTestId("proposal-toolbar").boundingBox()
    assert.ok(header && header.height <= 104)
    const navigation = page.getByRole("tablist", { name: "项目导航" })
    for (const label of ["正文", "参考资料", "写作步骤", "编辑器"])
      await expect(navigation.getByRole("tab", { name: label })).toBeVisible()
    await expect(navigation.getByRole("tab", { name: "排版预览" })).toHaveCount(
      0,
    )
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    )
    assert.deepEqual(state.renders, [])
    if (process.env.PROPOSAL_SCREENSHOT)
      await page.screenshot({
        path: process.env.PROPOSAL_SCREENSHOT,
        fullPage: true,
      })
  } finally {
    await page.close()
  }
})

function deferred() {
  let resolve!: () => void
  return {
    promise: new Promise<void>((done) => {
      resolve = done
    }),
    resolve: () => resolve(),
  }
}

async function actualRoute(
  page: Page,
  options: {
    holdGet?: ReturnType<typeof deferred>
    holdPost?: ReturnType<typeof deferred>
    holdSave?: ReturnType<typeof deferred>
    failPost?: boolean
    empty?: boolean
    bibliographyOnly?: boolean
    references?: boolean
    assistant?: boolean
    additionalChapter?: boolean
    progress?: ResearchAgentProgress
  } = {},
) {
  const sources = {
    "workspace-a": {
      id: "a-1",
      hash: "hash-a-1",
      content: options.empty
        ? "# Workspace A chapter\n\n<!-- Pending -->"
        : "# Workspace A chapter\n\nAuthor A content.",
    },
    "workspace-b": {
      id: "b-1",
      hash: "hash-b-1",
      content: "# Workspace B chapter\n\nAuthor B content.",
    },
  }
  const state = {
    sources,
    hasDraft: !options.empty,
    hasReferences: options.references ?? true,
    hasPublishedStep: false,
    unavailableOutput: false,
    rejectSave: false,
    referenceAssetReads: [] as string[],
    posts: [] as { id: string; hash: string }[],
    saves: [] as { id: string; content: string; path: string }[],
    gets: [] as string[],
    fileReads: [] as string[],
    ready: false,
    failPost: options.failPost ?? false,
  }
  const result = (id: string) => ({
    status: "ready",
    source_version_id: id,
    source_hash: `hash-${id}`,
    entry_path: "01-chapter.md",
    entry_paths: ["01-chapter.md"],
    diagnostics: [],
    files: [],
    pdf_url: `/api/v1/llm4ad/papers/source-versions/${id}/render/proposal.pdf`,
    typst_url: `/api/v1/llm4ad/papers/source-versions/${id}/render/proposal.typ`,
  })
  await page.addInitScript((progress) => {
    if (window !== window.top) return
    localStorage.setItem("llm4ad-language", "en")
    localStorage.setItem("access_token", "proposal-test-token")
    Object.assign(window, { proposalTestProgress: progress })
  }, options.progress)
  await page.route("**/api/v1/llm4ad/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url())
    if (url.pathname.endsWith("/providers/"))
      return route.fulfill({
        json: {
          items: options.progress
            ? [{ id: "proposal-provider", type: "anthropic" }]
            : [],
          total: options.progress ? 1 : 0,
        },
      })
    const workspaceId = url.pathname.match(
      /\/workspaces\/(workspace-[ab])/,
    )?.[1] as keyof typeof sources | undefined
    if (workspaceId && url.pathname.endsWith(workspaceId)) {
      const source = sources[workspaceId]
      return route.fulfill({
        json: {
          id: workspaceId,
          title: workspaceId,
          mode: "proposal",
          workflow_available: options.assistant ?? false,
          workflow_stages: ["formatting"],
          active_source_version_id: source.id,
          proposal_entry_path: "01-chapter.md",
          proposal_chapters: options.empty
            ? Array.from({ length: 9 }, (_, index) => ({
                id: `S${index + 1}`,
                title: `Research chapter ${index + 1}`,
                path:
                  index === 0 ? "01-chapter.md" : `0${index + 1}-chapter.md`,
                status: "not_started",
                has_content:
                  index === 0
                    ? state.hasDraft
                    : index === 3 && Boolean(options.bibliographyOnly),
              }))
            : [
                {
                  id: "chapter",
                  title: "Research chapter",
                  path: "01-chapter.md",
                  status: null,
                  has_content: true,
                },
                ...(options.additionalChapter
                  ? [
                      {
                        id: "methods",
                        title: "Research methods",
                        path: "methods.md",
                        status: "draft_done",
                        has_content: true,
                      },
                    ]
                  : []),
              ],
          source_versions: [
            {
              id: source.id,
              workspace_id: workspaceId,
              content_hash: source.hash,
              manifest: [
                "01-chapter.md",
                ...(options.additionalChapter ? ["methods.md"] : []),
                ...(options.empty
                  ? Array.from({ length: 8 }, (_, i) => `0${i + 2}-chapter.md`)
                  : []),
                ...(state.hasReferences
                  ? [
                      "project_context/reference.md",
                      "project_context/reference.pdf",
                    ]
                  : []),
              ],
            },
          ],
          proposal_stage_states: state.hasPublishedStep
            ? {
                formatting: {
                  status: "ready",
                  run_id: "intake-run",
                  summary: "Application requirements confirmed",
                },
              }
            : {},
          revision_candidates: [],
          analysis_provider_id: options.progress ? "proposal-provider" : null,
          analysis_model_name: options.progress ? "proposal-model" : null,
          analysis_context_window_tokens: 128000,
          analysis_max_output_tokens: 16384,
        },
      })
    }
    if (url.pathname.endsWith("/regions"))
      return route.fulfill({
        json: { workspace_id: workspaceId, regions: [], requests: [] },
      })
    if (
      url.pathname.includes("/history/source%3A") &&
      url.pathname.endsWith("/download")
    ) {
      assert.equal(
        request.headers().authorization,
        "Bearer proposal-test-token",
      )
      state.referenceAssetReads.push(url.searchParams.get("path")!)
      return route.fulfill({
        contentType: "application/pdf",
        body: "%PDF-1.4\n%%EOF",
      })
    }
    const id = url.pathname.match(/\/source-versions\/([^/]+)/)?.[1]
    const owner = id?.startsWith("a-") ? "workspace-a" : "workspace-b"
    if (url.pathname.endsWith("/file")) {
      if (request.method() === "GET") {
        state.fileReads.push(url.searchParams.get("path")!)
        const path = url.searchParams.get("path")
        if (path === "methods.md" && state.unavailableOutput)
          return route.fulfill({
            status: 404,
            json: { detail: "File unavailable" },
          })
        return route.fulfill({
          json: {
            source_version_id: id,
            path,
            content:
              path === "project_context/reference.md"
                ? "# Reference material\nOwned reference wording."
                : path === "methods.md"
                  ? "# Research methods\nIndependent chapter text."
                  : sources[owner].content,
          },
        })
      }
      const body = request.postDataJSON()
      state.saves.push({ id: id!, ...body })
      if (state.rejectSave)
        return route.fulfill({
          status: 409,
          json: { detail: "Source changed" },
        })
      if (options.holdSave && state.saves.length === 1)
        await options.holdSave.promise
      const next = `${id![0]}-${Number(id!.slice(2)) + 1}`
      sources[owner] = { id: next, hash: `hash-${next}`, content: body.content }
      return route.fulfill({
        json: { ...sources[owner], content_hash: sources[owner].hash },
      })
    }
    if (url.pathname.endsWith("/render")) {
      if (request.method() === "GET") {
        state.gets.push(id!)
        if (options.holdGet && id === "a-1") {
          await options.holdGet.promise
          return route.fulfill({ json: result(id!) })
        }
        return route.fulfill({ json: state.ready ? result(id!) : null })
      }
      state.posts.push({
        id: id!,
        hash: request.postDataJSON().expected_source_hash,
      })
      if (options.holdPost && id!.startsWith("a-"))
        await options.holdPost.promise
      if (state.failPost)
        return route.fulfill({
          status: 500,
          json: { detail: "Compile failed" },
        })
      return route.fulfill({ json: result(id!) })
    }
    if (url.pathname.endsWith("/proposal.pdf"))
      return route.fulfill({
        contentType: "application/pdf",
        body: `%PDF-1.4\n${id}\n%%EOF`,
      })
    if (url.pathname.endsWith("/proposal.typ"))
      return route.fulfill({ body: `= ${id}` })
    throw new Error(
      `Unexpected actual route request ${request.method()} ${url}`,
    )
  })
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.stack || error.message))
  await page.goto(`${origin}/__proposal_route__`)
  if (options.assistant)
    await expect(
      page.getByRole("textbox", { name: "Assistant message" }),
    ).toBeVisible()
  else await expect(page.getByTestId("research-agent-progress")).toBeVisible()
  return { state, errors }
}

test("actual route: a late stored GET cannot reveal workspace A's preview in workspace B", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  const holdGet = deferred()
  try {
    const { state, errors } = await actualRoute(page, { holdGet })
    await expect.poll(() => state.gets.includes("a-1")).toBe(true)
    await page.getByRole("button", { name: "Workspace B", exact: true }).click()
    await expect(page.getByTestId("proposal-results-dock")).toHaveAttribute(
      "data-view",
      "progress",
    )
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Author B content",
    )
    holdGet.resolve()
    await expect.poll(() => state.gets.includes("b-1")).toBe(true)
    await expect(
      page.getByRole("tab", { name: "Layout preview", exact: true }),
    ).toHaveCount(0)
    assert.deepEqual(state.posts, [])
    assert.deepEqual(errors, [])
  } finally {
    holdGet.resolve()
    await page.close()
  }
})

test("actual route: a late POST cannot retain workspace A's render or pending state in workspace B", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  const holdPost = deferred()
  try {
    const { state, errors } = await actualRoute(page, { holdPost })
    await confirm(page)
    await expect.poll(() => state.posts.length).toBe(1)
    await page.getByRole("button", { name: "Workspace B", exact: true }).click()
    await expect(page.getByTestId("proposal-results-dock")).toHaveAttribute(
      "data-view",
      "progress",
    )
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Author B content",
    )
    await expect(
      page.getByRole("button", { name: "Confirm and typeset" }),
    ).toBeEnabled()
    holdPost.resolve()
    await expect(
      page.getByRole("tab", { name: "Layout preview", exact: true }),
    ).toHaveCount(0)
    await confirm(page)
    await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible()
    await expect(page.getByText("hash-b-1", { exact: true })).toBeVisible()
    assert.deepEqual(errors, [])
  } finally {
    holdPost.resolve()
    await page.close()
  }
})

test("actual route: edits during a pending save are serialized against the latest active source before rendering", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  const holdSave = deferred()
  try {
    const { state, errors } = await actualRoute(page, { holdSave })
    await selectView(page, "Document")
    await edit(page, "# First author edit")
    await expect.poll(() => state.saves.length).toBe(1)
    await confirm(page)
    await selectView(page, "Document")
    await edit(page, "# New wording while saving")
    holdSave.resolve()
    await expect.poll(() => state.posts.length).toBe(1)
    assert.deepEqual(
      state.saves.map((save) => save.id),
      ["a-1", "a-2"],
    )
    assert.equal(state.saves[1].content, "# New wording while saving")
    assert.deepEqual(state.posts, [{ id: "a-3", hash: "hash-a-3" }])
    await expect(
      page.getByRole("textbox", { name: "Source editor" }),
    ).toHaveValue("# New wording while saving")
    assert.deepEqual(errors, [])
  } finally {
    holdSave.resolve()
    await page.close()
  }
})

test("actual route: edits during compilation are preserved and mark the completed older preview stale", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  const holdPost = deferred()
  try {
    const { state, errors } = await actualRoute(page, { holdPost })
    await confirm(page)
    await expect.poll(() => state.posts.length).toBe(1)
    await selectView(page, "Document")
    await edit(page, "# Author edit during compilation")
    await expect.poll(() => state.saves.length).toBe(1)
    await expect.poll(() => state.sources["workspace-a"].id).toBe("a-2")
    holdPost.resolve()
    await expect(
      page.getByRole("textbox", { name: "Source editor" }),
    ).toHaveValue("# Author edit during compilation")
    await showPdf(page)
    await expect(
      page.getByText(en.paper.proposalDocument.oldPreview),
    ).toBeVisible()
    assert.deepEqual(state.posts, [{ id: "a-1", hash: "hash-a-1" }])
    assert.deepEqual(errors, [])
  } finally {
    holdPost.resolve()
    await page.close()
  }
})

test("actual route: a successful stored GET clears an earlier manual error, and references return to the current chapter", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  try {
    const { state, errors } = await actualRoute(page, { failPost: true })
    await confirm(page)
    await expect(page.getByRole("alert")).toContainText(
      en.paper.proposalDocument.renderFailed,
    )
    state.ready = true
    await page
      .getByRole("button", { name: "Refresh workspace", exact: true })
      .click()
    await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible()
    await expect(page.getByRole("alert")).toHaveCount(0)
    await selectView(page, "References")
    await expect(
      page.getByRole("heading", { name: "Reference material", exact: true }),
    ).toBeVisible()
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Author A content",
    )
    assert.equal(state.posts.length, 1)
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("actual route: confirming from a selected binary reference renders all chapters without saving or looping", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  try {
    const { state, errors } = await actualRoute(page)
    await selectView(page, "References")
    await expect(
      page.getByRole("heading", { name: "Reference material", exact: true }),
    ).toBeVisible()
    await page.getByText("reference.pdf", { exact: true }).click()
    await expect(page.locator('iframe[title="reference.pdf"]')).toHaveAttribute(
      "src",
      /^blob:/,
    )
    assert.deepEqual(state.referenceAssetReads, [
      "source/project_context/reference.pdf",
    ])
    assert.ok(!state.fileReads.includes("project_context/reference.pdf"))
    await confirm(page)
    await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible()
    assert.deepEqual(state.posts, [{ id: "a-1", hash: "hash-a-1" }])
    assert.deepEqual(state.saves, [])
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Author A content",
    )
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("a blank proposal offers optional reference uploads before and during drafting", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  try {
    const { state, errors } = await actualRoute(page, {
      empty: true,
      references: false,
    })
    const progress = page.getByTestId("research-agent-progress")
    await expect(progress).toContainText(en.paper.proposalStart.stepsHint)
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-start-guide")).toContainText(
      "Your draft will appear here",
    )
    await selectView(page, "Writing steps")
    const nav = page.getByRole("tablist", { name: "Project navigation" })
    await expect(nav.getByRole("tab", { name: "References" })).toBeVisible()
    const uploadButton = page
      .getByTestId("proposal-toolbar")
      .getByRole("button", { name: "Upload references" })
    await uploadButton.click()
    const upload = page.getByRole("dialog", {
      name: "Upload project reference materials",
    })
    await expect(upload).toBeVisible()
    await page.keyboard.press("Escape")
    await expect(upload).toBeHidden()
    await expect(page.getByRole("button", { name: "Chapters" })).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: "Confirm and typeset", exact: true }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("tablist", { name: "Proposal", exact: true }),
    ).toHaveCount(0)
    await expect(page.getByTestId("proposal-document")).toHaveCount(0)
    state.hasReferences = true
    state.hasPublishedStep = true
    state.sources["workspace-a"].id = "a-2"
    state.sources["workspace-a"].hash = "hash-a-2"
    await page
      .getByRole("button", { name: "Refresh workspace", exact: true })
      .click()
    await expect(progress.locator("[data-step-status=completed]")).toHaveCount(
      1,
    )
    await expect(
      nav.getByRole("tab", { name: "Writing steps" }),
    ).toHaveAttribute("title", /Application requirements confirmed/)
    await expect(
      nav.getByRole("tab", { name: "Writing steps" }),
    ).toHaveAttribute("aria-selected", "true")
    await expect(uploadButton).toBeVisible()
    await nav.getByRole("tab", { name: /References.*2/ }).click()
    await expect(
      page.getByRole("heading", { name: "Reference material", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Confirm and typeset", exact: true }),
    ).toHaveCount(0)
    assert.deepEqual(state.posts, [])
    assert.deepEqual(state.saves, [])
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("a proposal opens the reported chapter and resumes the same step after reload", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  try {
    const priorStep = {
      goal: "",
      approach: "",
      summary: "",
      stage: "methods",
      outputPaths: [],
    }
    const { state, errors } = await actualRoute(page, {
      additionalChapter: true,
      assistant: true,
      progress: {
        sessionId: "proposal-session",
        workflowSequence: 3,
        status: "idle",
        currentTool: null,
        agents: [],
        workflowSteps: [
          {
            ...priorStep,
            id: "author-choice",
            title: "Choose a hypothesis",
            status: "waiting",
            updatedAt: "2026-11-18T08:00:00Z",
          },
          {
            ...priorStep,
            id: "methods-draft",
            title: "Draft the methods",
            status: "completed",
            outputPaths: ["proposal-log.md", "methods.md"],
            updatedAt: "2026-11-18T08:10:00Z",
          },
          {
            ...priorStep,
            id: "evidence",
            title: "Assess the evidence",
            status: "interrupted",
            updatedAt: "2026-11-18T08:20:00Z",
          },
        ],
      },
    })
    const deliverable = page.getByRole("button", { name: "View deliverable" })
    await expect(deliverable).toHaveCount(1)
    state.unavailableOutput = true
    const unavailable = page.waitForResponse(
      (response) =>
        response.url().includes("path=methods.md") && response.status() === 404,
    )
    await deliverable.click()
    await unavailable
    await expect(page.getByTestId("research-agent-progress")).toBeVisible()
    await expect(page.getByTestId("proposal-document")).toHaveCount(0)
    state.unavailableOutput = false
    await deliverable.click()
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Independent chapter text.",
    )
    assert.ok(state.fileReads.includes("methods.md"))
    await page.reload()
    const assistant = page.getByRole("textbox", { name: "Assistant message" })
    await expect(assistant).not.toHaveAttribute("data-request")
    const resume = page
      .locator('[data-step-id="step:evidence"]')
      .getByRole("button", { name: "Continue this step" })
    await resume.click()
    await expect(assistant).toHaveAttribute("data-request", /evidence/)
    assert.deepEqual(state.posts, [])
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("a saved draft is available on demand without replacing the default steps view", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  try {
    const { state, errors } = await actualRoute(page, { empty: true })
    state.hasDraft = true
    state.sources["workspace-a"] = {
      id: "a-2",
      hash: "hash-a-2",
      content: "# First draft\n\nSaved argument for author review.",
    }
    await page
      .getByRole("button", { name: "Refresh workspace", exact: true })
      .click()
    await expect(page.getByTestId("research-agent-progress")).toBeVisible()
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Saved argument for author review",
    )
    await page.getByRole("button", { name: "Chapters", exact: true }).click()
    await expect(
      page.getByRole("list", { name: "Chapters" }).getByRole("button"),
    ).toHaveCount(1)
    await page.reload()
    await expect(page.getByTestId("research-agent-progress")).toBeVisible()
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Saved argument for author review",
    )
    assert.deepEqual(state.posts, [])
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("a bibliography alone keeps the conversation guide and new draft publication does not interrupt reference reading", async () => {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 1000 },
  })
  try {
    const { state, errors } = await actualRoute(page, {
      empty: true,
      bibliographyOnly: true,
    })
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-start-guide")).toBeVisible()
    await selectView(page, "References")
    await expect(
      page.getByRole("heading", { name: "Reference material", exact: true }),
    ).toBeVisible()
    state.hasDraft = true
    state.sources["workspace-a"] = {
      id: "a-2",
      hash: "hash-a-2",
      content: "# First draft\n\nA newly published passage.",
    }
    await page
      .getByRole("button", { name: "Refresh workspace", exact: true })
      .click()
    await expect(
      page.getByRole("heading", { name: "Reference material", exact: true }),
    ).toBeVisible()
    await selectView(page, "Document")
    await expect(page.getByTestId("proposal-document")).toContainText(
      "A newly published passage",
    )
    assert.deepEqual(state.posts, [])
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("desktop proposal navigation stays above the right pane at the shared workbench width", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  try {
    const { errors } = await actualRoute(page, { assistant: true })
    const toolbar = page.getByTestId("proposal-toolbar")
    const dock = page.getByTestId("proposal-results-dock")
    await expect(dock).toHaveAttribute("data-view", "progress")
    await selectView(page, "Document")
    const composer = page.getByRole("textbox", { name: "Assistant message" })
    const toolbarBox = await toolbar.boundingBox()
    const dockBox = await dock.boundingBox()
    const composerBox = await composer.boundingBox()
    const documentBox = await page
      .getByTestId("proposal-document")
      .boundingBox()
    assert.ok(toolbarBox && dockBox && composerBox && documentBox)
    assert.ok(Math.abs(toolbarBox.y - dockBox.y) < 2)
    assert.ok(toolbarBox.y + toolbarBox.height <= documentBox.y)
    assert.ok(composerBox.x + composerBox.width <= toolbarBox.x + 1)
    assert.ok(toolbarBox.height <= 104)
    assert.ok(Math.abs(dockBox.width - 1280 / 2) < 2)
    await expect(
      dock.getByRole("tablist", { name: "Project navigation" }),
    ).toBeVisible()
    if (process.env.PROPOSAL_DESKTOP_SCREENSHOT)
      await page.screenshot({ path: process.env.PROPOSAL_DESKTOP_SCREENSHOT })
    await page.setViewportSize({ width: 1050, height: 900 })
    await selectView(page, "References")
    await page.getByText("reference.pdf", { exact: true }).click()
    const preview = page.locator('iframe[title="reference.pdf"]')
    await expect(preview).toBeVisible()
    const narrowDock = await dock.boundingBox()
    const previewBox = await preview.boundingBox()
    assert.ok(narrowDock && previewBox)
    assert.ok(previewBox.width >= narrowDock.width - 2)
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("mobile workbench switches between proposal and chat without losing the composer", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  try {
    const { errors } = await actualRoute(page, { empty: true, assistant: true })
    const composer = page.getByRole("textbox", { name: "Assistant message" })
    await composer.fill("A research idea still being written")
    await expect(page.getByTestId("proposal-results-dock")).toBeHidden()
    await page.getByRole("button", { name: "Proposal", exact: true }).click()
    await expect(page.getByTestId("research-agent-progress")).toBeVisible()
    await page.getByRole("button", { name: "Upload references" }).click()
    await expect(
      page.getByRole("dialog", { name: "Upload project reference materials" }),
    ).toBeVisible()
    await page.keyboard.press("Escape")
    await page
      .getByRole("button", { name: "Conversation", exact: true })
      .click()
    await expect(composer).toHaveValue("A research idea still being written")
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    )
    assert.deepEqual(errors, [])
  } finally {
    await page.close()
  }
})

test("visible chapter navigation keeps unsaved edits when saving fails and allows a retry", async () => {
  const page = await browser.newPage({ viewport: { width: 1400, height: 920 } })
  try {
    const { state } = await actualRoute(page, { additionalChapter: true })
    state.rejectSave = true
    await selectView(page, "Document")
    await edit(page, "# Edited research brief")
    await expect(
      page.getByRole("textbox", { name: "Source editor" }),
    ).toHaveAttribute("data-path", "01-chapter.md")
    await selectView(page, "Document")
    const chapterPicker = page.getByRole("button", {
      name: "Chapters",
      exact: true,
    })
    await chapterPicker.click()
    const methods = page
      .getByRole("list", { name: "Chapters" })
      .getByRole("button", { name: "Research methods" })
    await methods.click()
    await expect(
      page.getByRole("status").filter({ hasText: en.paper.editor.saveFailed }),
    ).toBeVisible()
    await expect(page.getByTestId("proposal-document")).toContainText(
      "Edited research brief",
    )
    state.rejectSave = false
    await methods.click()
    await expect(chapterPicker).toHaveAttribute("aria-expanded", "false")
    await chapterPicker.click()
    await expect(methods).toHaveAttribute("aria-current", "location")
    await page.keyboard.press("Escape")
    await page
      .getByRole("button", { name: "Edit chapter", exact: true })
      .click()
    await expect(
      page.getByRole("textbox", { name: "Source editor" }),
    ).toHaveAttribute("data-path", "methods.md")
    await expect(
      page.getByRole("textbox", { name: "Source editor" }),
    ).toHaveValue("# Research methods\nIndependent chapter text.")
    assert.equal(state.saves.at(-1)?.path, "01-chapter.md")
    assert.equal(state.saves.at(-1)?.content, "# Edited research brief")
    assert.deepEqual(state.posts, [])
  } finally {
    await page.close()
  }
})
