import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { type Browser, chromium, expect, type Page } from "@playwright/test"
import tailwindcss from "@tailwindcss/vite"
import { createServer, type ViteDevServer } from "vite"
import type { EvaluationFile, GatewayView } from "../src/client/types.gen.ts"

let server: ViteDevServer
let browser: Browser
let origin: string
let cacheDir: string
const root = fileURLToPath(new URL("..", import.meta.url))
const contract = {
  evaluator_id: "hgamey-genshin-sr-static",
  version: "1",
  data_version: "images-1",
  metrics: { psnr: "maximize" },
}
const gateway = (
  id: string,
  overrides: Partial<GatewayView> = {},
): GatewayView => ({
  id,
  name: id,
  contract,
  contract_key: "static-v1",
  online: true,
  capacity: 1,
  running: 0,
  queued: 0,
  last_seen: "2026-11-13",
  ...overrides,
})
const first = gateway("static-a")
const changed = gateway("static-v2", {
  contract: { ...contract, version: "2" },
  contract_key: "static-v2",
})
const game = gateway("game-a", {
  contract: { ...contract, evaluator_id: "hgamey-genshin-sr-game" },
  contract_key: "game-v1",
})
const custom = gateway("custom-a", {
  contract: { ...contract, evaluator_id: "research/custom.v2" },
  contract_key: "custom-v1",
})
const imageFile = (id: string): EvaluationFile => ({
  id,
  name: `images/${id}.png`,
  media_type: "image/png",
  size: 68,
  sha256: "a".repeat(64),
})
const files = [
  "reference",
  "baseline",
  "candidate",
  "baseline-2",
  "candidate-2",
  "extra",
].map(imageFile)
const image = (role: string, id: string) => ({
  role,
  artifact: `images/${id}.png`,
})
const manifest = [
  {
    id: "static",
    label: "Static input 1",
    kind: "static",
    images: [
      image("reference", "reference"),
      image("baseline", "baseline"),
      image("candidate", "candidate"),
    ],
  },
  {
    id: "round-1",
    label: "Round 1",
    kind: "game",
    images: [image("baseline", "baseline"), image("candidate", "candidate")],
  },
  {
    id: "round-2",
    label: "Round 2",
    kind: "game",
    images: [
      image("baseline", "baseline-2"),
      image("candidate", "candidate-2"),
    ],
  },
]
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aOZkAAAAASUVORK5CYII=",
  "base64",
)

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "llm4ad-evidence-vite-"))
  server = await createServer({
    configFile: false,
    root,
    cacheDir,
    optimizeDeps: { entries: ["tests/evaluation-evidence.fixture.tsx"] },
    resolve: {
      alias: { "@/hooks/useAuth": "\0fixture-auth", "@": `${root}/src` },
    },
    plugins: [
      tailwindcss(),
      {
        name: "evaluation-evidence-test-fixture",
        resolveId: (id) => (id === "\0fixture-auth" ? id : undefined),
        load: (id) =>
          id === "\0fixture-auth"
            ? "export default () => ({user: null})"
            : undefined,
        configureServer(devServer) {
          devServer.middlewares.use(
            "/__evaluation_evidence__",
            (_request, response) => {
              response.setHeader("content-type", "text/html")
              response.end(
                '<!doctype html><html lang="en"><head><title>Evaluation evidence test</title></head><body><div id="root"></div><script type="module" src="/tests/evaluation-evidence.fixture.tsx"></script></body></html>',
              )
            },
          )
        },
      },
    ],
    server: { host: "127.0.0.1", port: 0 },
  })
  await server.listen()
  origin = server.resolvedUrls!.local[0]
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  })
})

after(async () => {
  await browser?.close()
  await server?.close()
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true })
})

async function openFixture(
  page: Page,
  comparisons: unknown = manifest,
  available = files.map((file) => file.id),
  resultOverrides: Record<string, unknown> = {},
) {
  await page.addInitScript(() => {
    localStorage.setItem("llm4ad-language", "en")
  })
  let gateways = [first, changed, game, custom]
  const artifactRequests: {
    id: string
    download: string | null
    token?: string
  }[] = []
  await page.route("**/api/v1/llm4ad/**", async (route) => {
    const url = new URL(route.request().url())
    const artifactId = url.pathname.split("/artifacts/")[1]
    if (artifactId) {
      artifactRequests.push({
        id: artifactId,
        download: url.searchParams.get("download"),
        token: route.request().headers().authorization,
      })
      await route.fulfill({ contentType: "image/png", body: png })
    } else if (url.pathname.endsWith("evaluation-gateways")) {
      await route.fulfill({ json: gateways })
    } else if (url.pathname.endsWith("/evaluations")) {
      await route.fulfill({
        json: {
          data: [
            {
              id: "record",
              state: "completed",
              storage_state: "saved",
              created_at: "2026-11-13",
            },
          ],
          count: 1,
          page: 1,
          page_size: 20,
        },
      })
    } else {
      await route.fulfill({
        json: {
          record: {
            id: "record",
            state: "completed",
            storage_state: "saved",
            created_at: "2026-11-13",
            result: {
              score: 1.5,
              success: true,
              metadata: {
                evidence_comparisons: comparisons,
                raw_notes: "long metadata",
              },
              ...resultOverrides,
            },
            artifacts: [
              ...files,
              {
                id: "report",
                name: "report.txt",
                size: 5,
                sha256: "a".repeat(64),
              },
            ],
          },
          available_artifact_ids: available,
        },
      })
    }
  })
  await page.goto(`${origin}__evaluation_evidence__`)
  await expect(
    page.getByRole("combobox", { name: "Evaluator kind" }),
  ).toBeEnabled()
  return {
    artifactRequests,
    replaceGateways: (next: GatewayView[]) => {
      gateways = next
    },
  }
}

async function chooseEvaluator(page: Page, name: string | RegExp) {
  await page
    .getByRole("combobox", { name: /Evaluator kind|评估器类型/ })
    .click()
  await page.getByRole("option", { name }).click()
}

test("picker requires explicit groups, pins contracts through gateway loss, and clears stale IDs on group change", async () => {
  const page = await browser.newPage()
  try {
    const fixture = await openFixture(page)
    await expect(page.getByRole("checkbox")).toHaveCount(0)
    await expect(
      page.getByRole("combobox", { name: "Device pool", exact: true }),
    ).toContainText("Automatic pool")
    await page
      .getByRole("combobox", { name: "Device pool", exact: true })
      .click()
    await page.getByRole("option", { name: "Selected gateways" }).click()
    await chooseEvaluator(page, /Static images/)
    await expect(page.getByRole("checkbox")).toHaveCount(2)
    await page.getByRole("checkbox", { name: /static-a/ }).check()
    await expect(
      page.getByRole("checkbox", { name: /static-v2/ }),
    ).toBeDisabled()
    fixture.replaceGateways([changed, game, custom])
    await page.getByRole("button", { name: "Refresh gateways" }).click()
    await expect(page.getByRole("alert")).toContainText("no longer available")
    await expect(
      page.getByRole("checkbox", { name: /static-v2/ }),
    ).toBeDisabled()
    await page
      .getByRole("button", { name: "Remove unavailable gateways" })
      .click()
    await expect(page.locator("[data-pool-state]")).toContainText(
      '"gateway_ids":[]',
    )
    await expect(
      page.getByRole("checkbox", { name: /static-v2/ }),
    ).toBeDisabled()
    await chooseEvaluator(
      page,
      "HGamey · Genshin super-resolution · Real-game A/B",
    )
    await expect(page.getByRole("checkbox")).toHaveCount(1)
    await expect(
      page.getByRole("checkbox", { name: /game-a/ }),
    ).not.toBeChecked()
    await expect(page.locator("[data-pool-state]")).toContainText(
      `"contract":${JSON.stringify(game.contract)}`,
    )
    await expect(page.locator("[data-pool-state]")).toContainText(
      '"gateway_ids":[]',
    )
    await page.getByRole("checkbox", { name: /game-a/ }).check()
    await chooseEvaluator(page, "research/custom.v2")
    await expect(
      page.getByRole("checkbox", { name: /custom-a/ }),
    ).not.toBeChecked()
    await page.getByRole("checkbox", { name: /custom-a/ }).check()
    await page.getByRole("button", { name: "Switch language" }).click()
    await expect(
      page.getByRole("combobox", { name: "评估器类型" }),
    ).toContainText("research/custom.v2")
    await chooseEvaluator(page, "HGamey · 原神超分辨率 · 实机 A/B")
    await expect(
      page.getByRole("checkbox", { name: /game-a/ }),
    ).not.toBeChecked()
    await chooseEvaluator(page, /静态图像/)
    await expect(page.locator("[data-pool-state]")).toContainText(
      '"gateway_ids":[]',
    )
    await expect(page.getByRole("option", { name: /elite|精英/i })).toHaveCount(
      0,
    )
  } finally {
    await page.close()
  }
})

test("comparisons load authenticated originals above raw data, switch rounds, enlarge, and avoid duplicate gallery images", async () => {
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } })
  try {
    const { artifactRequests } = await openFixture(page)
    const comparison = page.getByRole("region", {
      name: "Image comparison",
      exact: true,
    })
    await expect(comparison.getByRole("img")).toHaveCount(3)
    await expect(
      page
        .getByRole("region", { name: "Image evidence", exact: true })
        .getByRole("button", { name: /^Preview/ }),
    ).toHaveCount(1)
    assert.ok(
      await page
        .locator("[data-node-raw-metadata]")
        .evaluate((raw) =>
          Boolean(
            raw.compareDocumentPosition(
              document.querySelector('[aria-label="Image comparison"]')!,
            ) & Node.DOCUMENT_POSITION_PRECEDING,
          ),
        ),
    )
    await expect(page.locator("[data-node-metric=score]")).toContainText("1.5")
    await expect(
      page.getByText("report.txt", { exact: true }),
    ).not.toBeVisible()
    await comparison
      .getByRole("button", { name: "Preview images/reference.png" })
      .click()
    await expect(page.getByRole("dialog").getByRole("img")).toHaveCount(1)
    await page.keyboard.press("Escape")
    await page.getByRole("button", { name: "Next comparison" }).click()
    await expect(comparison).toContainText("not synchronized frames")
    await expect(
      comparison.getByRole("heading", { name: "Baseline screenshot" }),
    ).toBeVisible()
    await page
      .getByRole("combobox", { name: "Select comparison image or game round" })
      .click()
    await page.getByRole("option", { name: "Round 2" }).click()
    await expect(comparison.getByRole("img")).toHaveCount(2)
    await expect(
      comparison.getByRole("button", {
        name: "Preview images/candidate-2.png",
      }),
    ).toBeVisible()
    if (process.env.EVIDENCE_SCREENSHOT_PATH)
      await page.screenshot({
        path: process.env.EVIDENCE_SCREENSHOT_PATH,
        fullPage: true,
      })
    assert.ok(artifactRequests.length >= 6)
    assert.ok(
      artifactRequests.every(
        (request) =>
          request.download === "true" &&
          request.token === "Bearer evidence-fixture-token",
      ),
    )
    await page.getByRole("button", { name: "Switch language" }).click()
    await expect(
      page.getByRole("region", { name: "图像对比", exact: true }),
    ).toContainText("并非同步帧")
    await page.setViewportSize({ width: 375, height: 900 })
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    )
  } finally {
    await page.close()
  }
})

test("malformed comparison metadata falls back to the gallery and unavailable originals are not requested", async () => {
  const page = await browser.newPage()
  try {
    const { artifactRequests } = await openFixture(
      page,
      [{ ...manifest[0], images: [image("baseline", "missing")] }],
      [],
    )
    await expect(
      page.getByRole("region", { name: "Image comparison", exact: true }),
    ).toHaveCount(0)
    const gallery = page.getByRole("region", {
      name: "Image evidence",
      exact: true,
    })
    await expect(gallery.getByRole("button", { name: /^Preview/ })).toHaveCount(
      6,
    )
    await expect(
      gallery.getByRole("button", { name: "Preview images/reference.png" }),
    ).toBeDisabled()
    assert.deepEqual(artifactRequests, [])
  } finally {
    await page.close()
  }
})

for (const [status, en, zh, reason] of [
  [
    "unverified",
    "Image quality unverified; automatically scored low",
    "画质未确认，已自动记低分",
    "Official quality measurements unavailable",
  ],
  [
    "beyond_reference",
    "Image quality gap; automatically scored low",
    "画质差距，已自动记低分",
    "Quality gap exceeds the reference line",
  ],
]) {
  test(`${status} quality shows a compact automatic penalty badge with collapsible reasons`, async () => {
    const page = await browser.newPage()
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    try {
      await openFixture(page, manifest, undefined, {
        score: -100,
        success: true,
        metrics: { game_score: -100 },
        metadata: {
          evidence_comparisons: manifest,
          quality_assessment: {
            status,
            penalized: true,
            penalty_score: -100,
            reasons: [reason],
          },
        },
      })
      const badge = page.locator("[data-node-quality-penalty]")
      await expect(badge).toHaveText(en)
      await expect(badge).toBeVisible()
      await expect(page.locator("[data-node-metric=score]")).toContainText(
        "-100",
      )
      await expect(page.locator("[data-node-metric=game_score]")).toContainText(
        "Game weighted score",
      )
      await expect(page.locator("[data-node-metric=game_score]")).toContainText(
        "-100",
      )
      const details = page.locator("[data-node-quality-reasons]")
      await expect(details).not.toHaveAttribute("open")
      await expect(details.getByText(reason, { exact: true })).not.toBeVisible()
      await details.locator("summary").click()
      await expect(details.getByText(reason, { exact: true })).toBeVisible()
      await expect(page.getByRole("alert")).toHaveCount(0)
      await expect(page.getByRole("dialog")).toHaveCount(0)
      await expect(
        page.locator("[data-node-raw-metadata]"),
      ).not.toHaveAttribute("open")
      assert.ok(
        await badge.evaluate((element) =>
          Boolean(
            element.compareDocumentPosition(
              document.querySelector('[aria-label="Image comparison"]')!,
            ) & Node.DOCUMENT_POSITION_PRECEDING,
          ),
        ),
      )
      await page.getByRole("button", { name: "Switch language" }).click()
      await expect(badge).toHaveText(zh)
      await expect(page.locator("[data-node-metric=game_score]")).toContainText(
        "游戏加权分",
      )
      await page.setViewportSize({ width: 375, height: 900 })
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      )
      assert.deepEqual(errors, [])
    } finally {
      await page.close()
    }
  })
}
