import assert from "node:assert/strict"
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { basename, join } from "node:path"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { type Browser, chromium, expect, type Page } from "@playwright/test"
import tailwindcss from "@tailwindcss/vite"
import { createServer, transformWithEsbuild, type ViteDevServer } from "vite"

let server: ViteDevServer
let browser: Browser
let origin: string
let cacheDir: string
const root = fileURLToPath(new URL("..", import.meta.url))

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "paper-archive-upload-"))
  const route = await readFile(
    join(root, "src/routes/_layout/papers.tsx"),
    "utf8",
  )
  const uploadStart = route.indexOf(
    "onUpload={async () => {",
    route.indexOf("<PaperSourceUploadDialog"),
  )
  const handlerStart = uploadStart + "onUpload={".length
  const handlerEnd = route.indexOf("\n        }}\n      />", handlerStart)
  assert.ok(uploadStart > 0 && handlerEnd > handlerStart)
  // Render the shared dialog with the workbench upload handler.
  const fixture = `
    import { useState } from "react"
    import { createRoot } from "react-dom/client"
    import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
    import { useTranslation } from "react-i18next"
    import { toast, Toaster } from "sonner"
    import { OpenAPI } from "@/client"
    import { PaperSourceUploadDialog } from "@/components/Paper/PaperSourceFilePicker"
    import { useUploadPaperSource } from "@/hooks/usePapers"
    import "@/i18n"
    import "@/index.css"
    OpenAPI.BASE = window.location.origin
    OpenAPI.TOKEN = "archive-upload-token"
    const client = new QueryClient()
    function Fixture() {
      const { t } = useTranslation()
      const params = new URLSearchParams(location.search)
      const workspace = { mode: params.get("mode") || "proposal" }
      const uploadPurpose = params.get("purpose") || "source"
      const [uploadOpen, setUploadOpen] = useState(true)
      const [pendingFiles, setPendingFiles] = useState([])
      const uploadSource = useUploadPaperSource("archive-" + workspace.mode)
      const setAlgorithmIntakeOpen = () => undefined
      const errorMessage = (_error, fallback) => fallback
      const onUpload = ${route.slice(handlerStart, handlerEnd)}
      }
      return <main><Toaster />{!uploadOpen && <p>Upload complete</p>}<PaperSourceUploadDialog open={uploadOpen} onOpenChange={setUploadOpen} purpose={uploadPurpose} files={pendingFiles} setFiles={setPendingFiles} pending={uploadSource.isPending} onUpload={onUpload} /></main>
    }
    createRoot(document.getElementById("root")).render(<QueryClientProvider client={client}><Fixture /></QueryClientProvider>)
  `
  const compiled = await transformWithEsbuild(
    fixture,
    "paper-upload-fixture.tsx",
    { loader: "tsx", jsx: "automatic" },
  )
  server = await createServer({
    configFile: false,
    root,
    cacheDir,
    optimizeDeps: {
      include: [
        "react",
        "react-dom/client",
        "react/jsx-runtime",
        "@tanstack/react-query",
        "react-i18next",
        "lucide-react",
        "sonner",
        "radix-ui",
        "axios",
      ],
    },
    resolve: { alias: { "@": `${root}/src`, "@docs": `${root}/../../docs` } },
    plugins: [
      tailwindcss(),
      {
        name: "actual-paper-upload-fixture",
        resolveId: (id) =>
          id === "/__paper_upload_fixture.js"
            ? "\0paper-upload-fixture"
            : undefined,
        load: (id) =>
          id === "\0paper-upload-fixture" ? compiled.code : undefined,
        configureServer(devServer) {
          devServer.middlewares.use(
            "/__paper_upload__",
            (_request, response) => {
              response.setHeader("content-type", "text/html")
              response.end(
                '<!doctype html><html lang="en"><head><title>Paper archive upload</title></head><body><div id="root"></div><script type="module" src="/__paper_upload_fixture.js"></script></body></html>',
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
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  })
})
after(async () => {
  await browser?.close()
  await server?.close()
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true })
})

async function openUpload(page: Page, mode: string, purpose: string) {
  const uploads: { names: string[]; paths: string[]; contents: string[] }[] = []
  await page.addInitScript(() => localStorage.setItem("llm4ad-language", "en"))
  await page.route("**/api/v1/llm4ad/papers/**", async (route) => {
    const request = route.request()
    if (request.method() !== "POST") return route.fulfill({ json: {} })
    assert.equal(request.headers().authorization, "Bearer archive-upload-token")
    assert.ok(request.url().includes(`/workspaces/archive-${mode}/`))
    const form = await new Request(request.url(), {
      method: "POST",
      headers: request.headers(),
      body: new Uint8Array(request.postDataBuffer()!),
    }).formData()
    const files = form.getAll("files") as File[]
    uploads.push({
      names: files.map((file) => file.name),
      paths: form.getAll("relative_paths").map(String),
      contents: await Promise.all(files.map((file) => file.text())),
    })
    return route.fulfill({
      json: {
        id: "source-next",
        workspace_id: `archive-${mode}`,
        manifest: uploads[0].paths,
      },
    })
  })
  await page.goto(`${origin}/__paper_upload__?mode=${mode}&purpose=${purpose}`)
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 15_000 })
  return uploads
}

for (const mode of ["proposal", "rebuttal"]) {
  for (const purpose of ["source", "project_context"]) {
    test(`${mode} ${purpose} uploader submits mixed archives and ordinary files`, async () => {
      const page = await browser.newPage()
      try {
        const uploads = await openUpload(page, mode, purpose)
        const input = page.locator('input[type="file"]:not([webkitdirectory])')
        const accept = (await input.getAttribute("accept"))!.split(",")
        for (const suffix of [".zip", ".tar", ".tar.gz", ".tgz"])
          assert.ok(accept.includes(suffix))
        await expect(page.getByRole("dialog")).toContainText(
          "extracted automatically",
        )
        const names = [
          "references.ZIP",
          "results.TAR.GZ",
          "figures.tar",
          "supplement.tgz",
          "作者说明.md",
        ]
        await input.setInputFiles(
          names.map((name) => ({
            name,
            mimeType: "application/octet-stream",
            buffer: Buffer.from(`original payload ${name}`),
          })),
        )
        await page
          .getByRole("button", { name: "Upload Selected Files" })
          .click()
        await expect(
          page.getByText("Upload complete", { exact: true }),
        ).toBeVisible()
        assert.equal(uploads.length, 1)
        assert.deepEqual(uploads[0].names, names)
        assert.deepEqual(
          uploads[0].contents,
          names.map((name) => `original payload ${name}`),
        )
        assert.deepEqual(
          uploads[0].paths,
          purpose === "source"
            ? names
            : [
                "project_context/references/references.ZIP",
                "project_context/results/results.TAR.GZ",
                "project_context/figures/figures.tar",
                "project_context/supplement/supplement.tgz",
                "project_context/作者说明.md",
              ],
        )
      } finally {
        await page.close()
      }
    })
  }
}

test("selected reference folders retain nested archive and file locations in multipart uploads", async () => {
  const page = await browser.newPage()
  const selected = join(cacheDir, "作者资料")
  await mkdir(join(selected, "实验"), { recursive: true })
  await mkdir(join(selected, "笔记"), { recursive: true })
  await writeFile(
    join(selected, "实验", "run.TAR.GZ"),
    "unchanged archive bytes",
  )
  await writeFile(join(selected, "笔记", "说明.md"), "exact author notes")
  try {
    const uploads = await openUpload(page, "proposal", "project_context")
    await page.locator("input[webkitdirectory]").setInputFiles(selected)
    await page.getByRole("button", { name: "Upload Selected Files" }).click()
    await expect(
      page.getByText("Upload complete", { exact: true }),
    ).toBeVisible()
    assert.deepEqual(
      [...uploads[0].paths].sort(),
      [
        `project_context/${basename(selected)}/实验/run/run.TAR.GZ`,
        `project_context/${basename(selected)}/笔记/说明.md`,
      ].sort(),
    )
  } finally {
    await page.close()
  }
})

async function selectMockSizes(page: Page, sizes: number[]) {
  await page
    .locator('input[type="file"]:not([webkitdirectory])')
    .evaluate((element, fileSizes) => {
      const files = fileSizes.map((size, index) => {
        const file = new File(["small test payload"], `archive-${index}.zip`)
        Object.defineProperty(file, "size", { value: size })
        return file
      })
      Object.defineProperty(element, "files", {
        configurable: true,
        value: files,
      })
      element.dispatchEvent(new Event("change", { bubbles: true }))
      Reflect.deleteProperty(element, "files")
    }, sizes)
}

for (const [mode, purpose, sizes] of [
  ["proposal", "project_context", [100 * 1024 * 1024 + 1]],
  ["rebuttal", "source", [60 * 1024 * 1024, 40 * 1024 * 1024 + 1]],
] as const) {
  test(`${mode} ${purpose} rejects oversized selection before sending bytes`, async () => {
    const page = await browser.newPage()
    try {
      const uploads = await openUpload(page, mode, purpose)
      await expect(page.getByRole("dialog")).toContainText(
        "Upload ≤100 MiB · Extracted ≤500 MiB · Up to 2,000 files",
      )
      await selectMockSizes(page, [...sizes])
      await expect(
        page.getByRole("button", { name: "Upload Selected Files" }),
      ).toBeDisabled()
      await expect(
        page.getByText(
          "The selected files exceed the 100 MiB upload limit. Remove files or split the upload.",
          { exact: true },
        ),
      ).toBeVisible()
      await expect(page.getByRole("dialog")).toBeVisible()
      assert.equal(uploads.length, 0)
    } finally {
      await page.close()
    }
  })
}

test("exactly 100 MiB remains within the client upload limit", async () => {
  const page = await browser.newPage()
  try {
    const uploads = await openUpload(page, "proposal", "source")
    await selectMockSizes(page, [100 * 1024 * 1024])
    await page.getByRole("button", { name: "Upload Selected Files" }).click()
    await expect(
      page.getByText("Upload complete", { exact: true }),
    ).toBeVisible()
    assert.equal(uploads.length, 1)
  } finally {
    await page.close()
  }
})
