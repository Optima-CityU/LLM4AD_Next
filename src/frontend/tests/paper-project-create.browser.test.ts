import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { type Browser, chromium, expect, type Page } from "@playwright/test"
import tailwindcss from "@tailwindcss/vite"
import { createServer, type ViteDevServer } from "vite"

const root = fileURLToPath(new URL("..", import.meta.url))
let server: ViteDevServer
let browser: Browser
let origin: string
let cacheDir: string

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "paper-project-create-"))
  server = await createServer({
    configFile: false,
    root,
    cacheDir,
    optimizeDeps: {
      entries: ["tests/paper-project-create.fixture.tsx"],
      include: ["react-dom", "react/jsx-runtime", "axios", "radix-ui"],
    },
    resolve: { alias: { "@": `${root}/src`, "@docs": `${root}/../../docs` } },
    plugins: [
      tailwindcss(),
      {
        name: "paper-project-create-fixture",
        load(id) {
          if (id.endsWith("/components/AutoResearch/ArtifactCodeEditor.tsx"))
            return "export default function Editor() { return null }"
        },
        configureServer(devServer) {
          devServer.middlewares.use(
            "/__paper_create__",
            (_request, response) => {
              response.setHeader("content-type", "text/html")
              response.end(
                '<!doctype html><html lang="en"><body><div id="root"></div><script type="module" src="/tests/paper-project-create.fixture.tsx"></script></body></html>',
              )
            },
          )
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

async function mockProject(
  page: Page,
  mode: "proposal" | "manuscript",
  failReviewer?: string,
) {
  page.setDefaultTimeout(10000)
  page.on("pageerror", (error) => process.stderr.write(`${error.stack}\n`))
  const calls: Array<{ step: string; payload?: unknown }> = []
  let rejectedReviewer = failReviewer
  const reviews: Array<{
    id: string
    reviewer_label: string
    source_version_id: string
  }> = []
  let workspace:
    | { id: string; title: string; description: string | null }
    | undefined
  let hasModel = false
  let sourceId = mode === "proposal" ? "proposal-seed" : null
  await page.addInitScript(() => {
    if (window !== window.top) return
    localStorage.setItem("llm4ad-language", "en")
    localStorage.setItem("access_token", "paper-intake-test-token")
    document.addEventListener("paper-test-prompt", (event) => {
      const prompt = (event as CustomEvent<string>).detail
      const prompts = JSON.parse(
        sessionStorage.getItem("test-paper-prompts") ?? "[]",
      )
      sessionStorage.setItem(
        "test-paper-prompts",
        JSON.stringify([...prompts, prompt]),
      )
    })
  })
  await page.route("**/__paper_assistant__", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<script>
        window.addEventListener("message", (event) => {
          if (event.data.type !== "llm4ad:submit-prompt") return;
          parent.document.dispatchEvent(new CustomEvent("paper-test-prompt", {detail: event.data.prompt}));
          parent.postMessage({type: "llm4ad:prompt-accepted", requestId: event.data.requestId}, location.origin);
        });
      </script>`,
    }),
  )
  await page.route("**/api/v1/llm4ad/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith("/providers/")) {
      return route.fulfill({
        json: {
          items: [
            {
              id: "provider-a",
              name: "Provider A",
              type: "anthropic",
              model: "model-a",
            },
          ],
        },
      })
    }
    if (path.endsWith("/workspaces")) {
      if (request.method() === "POST") {
        const payload = request.postDataJSON()
        calls.push({ step: "create", payload })
        workspace = { id: "workspace-new", ...payload }
        return route.fulfill({ status: 201, json: workspace })
      }
      return route.fulfill({
        json: {
          items: workspace
            ? [{ ...workspace, mode, active_source_version_id: sourceId }]
            : [],
        },
      })
    }
    if (path.endsWith("/model-binding") && request.method() !== "GET") {
      calls.push({ step: "model", payload: request.postDataJSON() })
      hasModel = true
      return route.fulfill({ json: { configured: true } })
    }
    if (path.endsWith("/sources")) {
      calls.push({ step: "upload", payload: request.postData() })
      sourceId = "uploaded-source"
      return route.fulfill({ status: 201, json: { id: sourceId } })
    }
    if (path.endsWith("/source-versions/uploaded-source/reviews")) {
      const payload = request.postDataJSON()
      calls.push({ step: "review", payload })
      if (rejectedReviewer === payload.reviewer_label) {
        rejectedReviewer = undefined
        return route.fulfill({
          status: 500,
          json: { detail: "Temporary failure" },
        })
      }
      const review = {
        id: `review-${reviews.length + 1}`,
        reviewer_label: payload.reviewer_label,
        source_version_id: "uploaded-source",
      }
      reviews.push(review)
      return route.fulfill({ status: 201, json: review })
    }
    if (path.endsWith("/workspaces/workspace-new")) {
      return route.fulfill({
        json: {
          ...workspace,
          mode,
          active_source_version_id: sourceId,
          source_versions: sourceId
            ? [{ id: sourceId, manifest: ["paper.md"] }]
            : [],
          proposal_entry_path: mode === "proposal" ? "paper.md" : null,
          workflow_available: true,
          workflow_stages: [
            mode === "proposal" ? "formatting" : "rebuttal_baseline",
          ],
          analysis_provider_id: hasModel ? "provider-a" : null,
          analysis_model_name: hasModel ? "model-a" : null,
          analysis_context_window_tokens: 128000,
          analysis_max_output_tokens: 16384,
          reviews,
        },
      })
    }
    if (path.endsWith("/cloudcli-session")) {
      calls.push({ step: "runtime", payload: request.postDataJSON() })
      return route.fulfill({
        json: {
          session_id: "intake-session",
          runtime_url: "/__paper_assistant__",
        },
      })
    }
    if (path.endsWith("/regions"))
      return route.fulfill({ json: { regions: [], requests: [] } })
    if (path.endsWith("/file"))
      return route.fulfill({ json: { content: "# Draft", path: "paper.md" } })
    return route.fulfill({ json: {} })
  })
  await page.goto(`${origin}/__paper_create__?mode=${mode}`)
  return calls
}

async function openDialog(page: Page) {
  await page
    .getByRole("button", { name: "New Research Project", exact: true })
    .click({ timeout: 20000 })
  return page.getByRole("dialog")
}

async function enterGuidedSetup(dialog: ReturnType<Page["getByRole"]>) {
  await dialog.getByRole("button", { name: "Guided setup" }).click()
  await dialog
    .getByRole("textbox", { name: "Project name" })
    .fill("Research project")
}

function stepAction(dialog: ReturnType<Page["getByRole"]>, name: string) {
  return dialog
    .locator('[data-slot="dialog-footer"]')
    .getByRole("button", { name, exact: true })
}

async function next(dialog: ReturnType<Page["getByRole"]>) {
  await stepAction(dialog, "Next step").click()
}

async function selectModel(page: Page, dialog: ReturnType<Page["getByRole"]>) {
  await dialog.getByRole("combobox", { name: "Provider" }).click()
  await page.getByRole("option", { name: "Provider A" }).click()
  await expect(dialog.getByRole("combobox", { name: "Model" })).toHaveText(
    "model-a",
  )
}

const sentPrompts = (page: Page): Promise<string[]> =>
  page.evaluate(() =>
    JSON.parse(sessionStorage.getItem("test-paper-prompts") ?? "[]"),
  )

async function expectAssistant(page: Page, calls: Array<{ step: string }>) {
  try {
    await expect(
      page.locator('iframe[src="/__paper_assistant__"]'),
    ).toBeVisible()
  } catch (error) {
    console.error(
      "Paper setup requests",
      calls.map(({ step }) => step),
    )
    console.error(await page.locator("body").innerText())
    throw error
  }
}

for (const mode of ["proposal", "manuscript"] as const) {
  test(`${mode} empty project enters the workspace with only a name`, async () => {
    const page = await browser.newPage()
    try {
      const calls = await mockProject(page, mode)
      const dialog = await openDialog(page)
      if (mode === "proposal") {
        await enterGuidedSetup(dialog)
        await dialog
          .getByRole("textbox", { name: "Research direction" })
          .fill("Keep this in the draft")
        await next(dialog)
        await dialog.getByRole("radio", { name: "Add materials" }).check()
        await dialog
          .locator('input[type="file"]:not([webkitdirectory])')
          .setInputFiles({
            name: "reference.md",
            mimeType: "text/markdown",
            buffer: Buffer.from("# Reference"),
          })
        await next(dialog)
        await selectModel(page, dialog)
        await dialog.getByRole("radio", { name: "Start automatically" }).check()
        await stepAction(dialog, "Previous step").click()
        await stepAction(dialog, "Previous step").click()
        await stepAction(dialog, "Back to setup options").click()
      }
      await dialog.getByRole("button", { name: "Empty project" }).click()
      await expect(
        dialog.getByRole("heading", { name: "Empty project" }),
      ).toBeFocused()
      await expect(
        dialog.getByRole("navigation", { name: "Project setup steps" }),
      ).toHaveCount(0)
      await expect(
        dialog.getByRole("combobox", { name: "Provider" }),
      ).toHaveCount(0)
      await dialog
        .getByRole("textbox", { name: "Project name" })
        .fill("A new project")
      assert.deepEqual(calls, [])
      await stepAction(dialog, "Create and open project").click()
      await expect(
        page
          .getByRole("button", { name: "Configure model", exact: true })
          .first(),
      ).toBeVisible()
      assert.deepEqual(calls, [
        { step: "create", payload: { title: "A new project", mode } },
      ])
      assert.deepEqual(await sentPrompts(page), [])
    } finally {
      await page.close()
    }
  })
}

test("guided proposal starts once after skipping materials and binding a model", async () => {
  const page = await browser.newPage()
  try {
    const calls = await mockProject(page, "proposal")
    await expect(
      page
        .getByText("Tell the assistant what you want to study.", {
          exact: false,
        })
        .first(),
    ).toBeVisible()
    const dialog = await openDialog(page)
    await enterGuidedSetup(dialog)
    await next(dialog)
    await expect(
      dialog.getByRole("radio", { name: "Skip for now" }),
    ).toBeChecked()
    await next(dialog)
    await expect(
      dialog.getByRole("radio", { name: "Start manually" }),
    ).toBeChecked()
    await expect(
      dialog.getByRole("button", { name: "Create and open project" }),
    ).toBeDisabled()
    await selectModel(page, dialog)
    await dialog.getByRole("radio", { name: "Start automatically" }).check()
    await stepAction(dialog, "Previous step").click()
    await next(dialog)
    await expect(dialog.getByRole("combobox", { name: "Model" })).toHaveText(
      "model-a",
    )
    await expect(
      dialog.getByRole("radio", { name: "Start automatically" }),
    ).toBeChecked()
    await dialog
      .getByRole("button", { name: "Create and start assistant" })
      .evaluate((button: HTMLButtonElement) => {
        button.click()
        button.click()
      })
    await expectAssistant(page, calls)
    await expect
      .poll(() => sentPrompts(page), { timeout: 15000 })
      .toEqual([expect.stringContaining("ask me")])
    assert.deepEqual(
      calls.slice(0, 3).map(({ step }) => step),
      ["create", "model", "runtime"],
    )
    await page.reload()
    await page.getByRole("button", { name: "Open Research Workspace" }).click()
    await expectAssistant(page, calls)
    assert.equal((await sentPrompts(page)).length, 1)
  } finally {
    await page.close()
  }
})

test("manual proposal saves typed details and references without sending a prompt", async () => {
  const page = await browser.newPage()
  try {
    const calls = await mockProject(page, "proposal")
    const dialog = await openDialog(page)
    await enterGuidedSetup(dialog)
    const modelStep = dialog
      .getByRole("navigation", { name: "Project setup steps" })
      .getByRole("button", { name: "3. Model and startup", exact: true })
    await expect(modelStep).toBeDisabled()
    await dialog
      .getByRole("textbox", { name: "Research direction" })
      .fill("Efficient search")
    await dialog
      .getByRole("textbox", { name: "Application requirements" })
      .fill("One-page summary")
    await next(dialog)
    await dialog.getByRole("radio", { name: "Add materials" }).check()
    const choosingFiles = page.waitForEvent("filechooser")
    await dialog
      .getByRole("button", { name: "Select Files", exact: true })
      .click()
    await (await choosingFiles).setFiles([
      {
        name: "reference.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Reading"),
      },
      {
        name: "archive.zip",
        mimeType: "application/zip",
        buffer: Buffer.from("archive bytes"),
      },
      {
        name: "remove.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Removed"),
      },
    ])
    await dialog.getByRole("button", { name: "Remove remove.md" }).click()
    await stepAction(dialog, "Previous step").click()
    await expect(
      dialog.getByRole("textbox", { name: "Research direction" }),
    ).toHaveValue("Efficient search")
    await next(dialog)
    await expect(
      dialog.getByRole("button", { name: "Remove archive.zip" }),
    ).toBeVisible()
    await modelStep.click()
    await selectModel(page, dialog)
    assert.deepEqual(calls, [])
    await dialog
      .getByRole("button", { name: "Create and open project" })
      .click()
    await expectAssistant(page, calls)
    assert.deepEqual(
      calls.map(({ step }) => step),
      ["create", "model", "upload", "runtime"],
    )
    assert.match(
      JSON.stringify(calls[0].payload),
      /Efficient search.*One-page summary/,
    )
    assert.match(String(calls[2].payload), /project_context\/reference\.md/)
    assert.match(
      String(calls[2].payload),
      /project_context\/archive\/archive\.zip/,
    )
    assert.doesNotMatch(String(calls[2].payload), /remove\.md/)
    assert.deepEqual(await sentPrompts(page), [])
  } finally {
    await page.close()
  }
})

test("rebuttal retries just the unfinished review before starting the assistant", async () => {
  const page = await browser.newPage()
  try {
    const calls = await mockProject(page, "manuscript", "Reviewer B")
    await expect(
      page
        .getByText("Provide your paper and reviewer comments.", {
          exact: false,
        })
        .first(),
    ).toBeVisible()
    const dialog = await openDialog(page)
    await enterGuidedSetup(dialog)
    await dialog
      .getByRole("textbox", { name: "Response goal" })
      .fill("Clarify the benchmark")
    await next(dialog)
    await dialog.getByRole("radio", { name: "Add materials" }).check()
    await dialog
      .getByRole("region", { name: "Paper source files (Markdown or LaTeX)" })
      .locator('input[type="file"]:not([webkitdirectory])')
      .setInputFiles({
        name: "paper.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Paper"),
      })
    for (const [index, label] of ["Reviewer A", "Reviewer B"].entries()) {
      await dialog
        .getByRole("button", { name: "Add reviewer comments" })
        .click()
      await dialog
        .getByRole("textbox", { name: "Reviewer label" })
        .nth(index)
        .fill(label)
      await expect(stepAction(dialog, "Next step")).toBeDisabled()
      await dialog
        .getByRole("textbox", { name: "Reviewer feedback", exact: true })
        .nth(index)
        .fill(`Explain the experiment for ${label}.`)
    }
    await dialog
      .getByRole("textbox", { name: "Review title (optional)" })
      .first()
      .fill("Benchmark")
    await next(dialog)
    await selectModel(page, dialog)
    await dialog.getByRole("radio", { name: "Start automatically" }).check()
    await dialog
      .getByRole("button", { name: "Create and start assistant" })
      .click()
    await expect(dialog.getByRole("alert")).toContainText(
      "Could not save all reviewer comments",
    )
    await expect(
      dialog.getByRole("heading", { name: "Research materials", exact: true }),
    ).toBeVisible()
    const reviewerLabels = dialog.getByRole("textbox", {
      name: "Reviewer label",
    })
    await expect(reviewerLabels.first()).toBeDisabled()
    await expect(reviewerLabels.nth(1)).toHaveValue("Reviewer B")
    await dialog.getByRole("button", { name: "Retry" }).click()
    await expect
      .poll(() => sentPrompts(page), { timeout: 15000 })
      .toEqual([expect.stringContaining("drafting replies")])
    assert.match((await sentPrompts(page))[0], /Clarify the benchmark/)
    assert.equal((calls[3].payload as { title: string }).title, "Benchmark")
    assert.deepEqual(
      calls.map(({ step }) => step),
      ["create", "model", "upload", "review", "review", "review", "runtime"],
    )
    assert.deepEqual(
      calls
        .filter(({ step }) => step === "review")
        .map(
          ({ payload }) =>
            (payload as { reviewer_label: string }).reviewer_label,
        ),
      ["Reviewer A", "Reviewer B", "Reviewer B"],
    )
  } finally {
    await page.close()
  }
})

test("narrow project setup supports keyboard selection and keeps its actions visible with a long file list", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 660 } })
  try {
    const calls = await mockProject(page, "manuscript")
    const dialog = await openDialog(page)
    const guidedCard = dialog.getByRole("button", { name: "Guided setup" })
    await guidedCard.focus()
    await page.keyboard.press("Enter")
    await expect(
      dialog.getByRole("heading", { name: "Basic information" }),
    ).toBeFocused()
    await expect(stepAction(dialog, "Next step")).toBeDisabled()
    await expect(
      dialog.getByRole("textbox", { name: "Response goal" }),
    ).toBeVisible()
    await dialog
      .getByRole("textbox", { name: "Project name" })
      .fill("Narrow project")
    await next(dialog)
    const emptyMaterials = dialog.getByRole("radio", {
      name: "Skip for now",
    })
    await emptyMaterials.focus()
    await page.keyboard.down("ArrowRight")
    await expect(
      dialog.getByRole("radio", { name: "Add materials" }),
    ).toBeChecked()
    await page.keyboard.up("ArrowRight")
    await expect(
      dialog.getByRole("button", { name: "Select Folder" }),
    ).toBeVisible()
    await dialog
      .locator('input[type="file"]:not([webkitdirectory])')
      .evaluate((input) => {
        const items = Array.from({ length: 12 }, (_, index) => {
          const file = new File(["text"], `source-${index}.md`)
          if (index === 0)
            Object.defineProperty(file, "size", {
              value: 100 * 1024 * 1024 + 1,
            })
          return file
        })
        Object.defineProperty(input, "files", {
          configurable: true,
          value: items,
        })
        input.dispatchEvent(new Event("change", { bubbles: true }))
        Reflect.deleteProperty(input, "files")
      })
    const nextStep = stepAction(dialog, "Next step")
    await expect(nextStep).toBeInViewport({ ratio: 0.99 })
    await expect(nextStep).toBeDisabled()
    await expect(dialog.getByRole("alert")).toContainText(
      "exceed the 100 MiB upload limit",
    )
    await dialog.getByRole("button", { name: "Remove source-0.md" }).click()
    await expect(dialog.getByRole("alert")).toHaveCount(0)
    await expect(nextStep).toBeEnabled()
    await expect(nextStep).toBeInViewport({ ratio: 0.99 })
    await next(dialog)
    await expect(
      dialog.getByRole("heading", { name: "Model and startup" }),
    ).toBeVisible()
    assert.deepEqual(calls, [])
  } finally {
    await page.close()
  }
})

test("rebuttal without the paper and reviews opens with guidance instead of starting a turn", async () => {
  const page = await browser.newPage()
  try {
    const calls = await mockProject(page, "manuscript")
    const dialog = await openDialog(page)
    await enterGuidedSetup(dialog)
    await next(dialog)
    await next(dialog)
    await selectModel(page, dialog)
    await dialog.getByRole("radio", { name: "Start automatically" }).check()
    await expect(
      dialog.getByText(
        "Add the paper and reviewer comments in the workspace before drafting a reply.",
      ),
    ).toBeVisible()
    await dialog
      .getByRole("button", { name: "Create and open project" })
      .click()
    await expect(dialog).toBeHidden()
    assert.deepEqual(
      calls.map(({ step }) => step),
      ["create", "model"],
    )
    assert.deepEqual(await sentPrompts(page), [])
  } finally {
    await page.close()
  }
})
