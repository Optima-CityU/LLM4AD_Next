import assert from "node:assert/strict"
import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
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
  cacheDir = await mkdtemp(join(tmpdir(), "paper-model-picker-"))
  const route = await readFile(
    join(root, "src/routes/_layout/papers.tsx"),
    "utf8",
  )
  const pickerStart = route.indexOf("function ModelPicker(")
  const pickerEnd = route.indexOf(
    "\nfunction PaperProjectsEmptyState",
    pickerStart,
  )
  const modelsStart = route.indexOf("function providerModels(")
  const modelsEnd = route.indexOf("\nfunction errorMessage", modelsStart)
  const syncBody = route.indexOf(
    "setContextTokens(workspace.analysis_context_window_tokens)",
  )
  const syncStart = route.lastIndexOf("  useEffect(() => {", syncBody)
  const sync = route
    .slice(syncStart)
    .match(/^ {2}useEffect\(\(\) => \{[\s\S]*?\n {2}\}, \[[\s\S]*?\]\)/)?.[0]
  const dialogStart = route.indexOf("<Dialog open={settingsOpen}")
  const dialogEnd = route.indexOf("</Dialog>", dialogStart) + "</Dialog>".length
  const idsStart = route.indexOf("  const runtimeProviderIds = useMemo(")
  const idsEnd = route.indexOf("\n  const navigate =", idsStart)
  assert.ok(
    pickerStart > 0 &&
      pickerEnd > pickerStart &&
      sync &&
      dialogStart > 0 &&
      idsEnd > idsStart,
  )
  const fixture = `
    import { createContext, useContext, useMemo, useCallback, useEffect, useRef, useState } from "react"
    import { createRoot } from "react-dom/client"
    import { useTranslation } from "react-i18next"
    import { toast } from "sonner"
    import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
    import { Button } from "@/components/ui/button"
    import { Label } from "@/components/ui/label"
    import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
    import { CLAUDE_RUNTIME_PROVIDER_TYPES } from "@/lib/researchModels"
    import "@/i18n"
    import "@/index.css"
    const ProviderContext = createContext({})
    function useProviders() { return useContext(ProviderContext) }
    const CONTEXT_PRESETS = [32000,64000,128000,200000]
    const OUTPUT_PRESETS = [4096,8192,16384,32000]
    const catalog = [
      {id:"provider-a",name:"Provider A",type:"openai",model:"model-a1;model-a2"},
      {id:"provider-b",name:"Provider B",type:"anthropic",model:"model-b"},
      {id:"provider-c",name:"Provider C",type:"openai_compatible",model:"model-c1;model-c2"}
    ]
    const unbound = {id:"workspace-a",analysis_provider_id:null,analysis_model_name:null,analysis_context_window_tokens:128000,analysis_max_output_tokens:16384,progress:0}
    const persisted = {...unbound,analysis_provider_id:"provider-b",analysis_model_name:"model-b",analysis_context_window_tokens:64000,analysis_max_output_tokens:8192}
    ${route.slice(modelsStart, modelsEnd)}
    ${route.slice(pickerStart, pickerEnd)}
    function Workbench({workspace,setWorkspace,providers,setProviders}) {
      const {t}=useTranslation()
      const workspaceId=workspace.id
      const activeId=workspaceId
      const [settingsOpen,setSettingsOpen]=useState(false)
      const modelSettingsWorkspaceId=useRef<string | null>(null)
      const [binding,rawSetBinding]=useState({providerId:"",modelName:""})
      const [contextTokens,setContextTokens]=useState(128000)
      const [outputTokens,setOutputTokens]=useState(16384)
      const [trace,setTrace]=useState([])
      const setBinding=useCallback(value=>{setTrace(previous=>[...previous,value]);rawSetBinding(value)},[])
      const providerItems=useProviders().data?.items
      ${route.slice(idsStart, idsEnd)}
      ${sync}
      const updateModel={mutateAsync:async value=>setWorkspace(current=>({...current,analysis_provider_id:value.provider_id,analysis_model_name:value.model_name,analysis_context_window_tokens:value.context_window_tokens,analysis_max_output_tokens:value.max_output_tokens}))}
      return <main className="p-5">
        <button onClick={()=>setSettingsOpen(true)}>Open model settings</button>
        <button onClick={()=>setWorkspace(current=>({...current,progress:current.progress+1}))}>Refresh workspace progress</button>
        <button onClick={()=>setProviders(current=>({...current,data:{items:current.data.items.map(item=>({...item,updated_time:Date.now()}))}}))}>Refresh provider catalog</button>
        <button onClick={()=>setProviders({data:{items:catalog},isLoading:false,isError:false})}>Finish provider loading</button>
        <button onClick={()=>setWorkspace({...persisted,id:"workspace-b"})}>Navigate workspace</button>
        <output data-testid="picker-state">{JSON.stringify({workspaceId,settingsOpen,binding,contextTokens,outputTokens,trace})}</output>
        ${route.slice(dialogStart, dialogEnd)}
      </main>
    }
    function Fixture(){
      const params=new URLSearchParams(location.search)
      const [workspace,setWorkspace]=useState(params.has("bound")?persisted:unbound)
      const [providers,setProviders]=useState(params.has("loading")?{data:undefined,isLoading:true,isError:false}:{data:{items:catalog},isLoading:false,isError:false})
      return <ProviderContext.Provider value={providers}><Workbench workspace={workspace} setWorkspace={setWorkspace} providers={providers} setProviders={setProviders}/></ProviderContext.Provider>
    }
    createRoot(document.getElementById("root")).render(<Fixture />)
  `
  const compiled = await transformWithEsbuild(
    fixture,
    "paper-model-picker-fixture.tsx",
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
        "react-i18next",
        "sonner",
        "radix-ui",
      ],
    },
    resolve: { alias: { "@": `${root}/src`, "@docs": `${root}/../../docs` } },
    plugins: [
      tailwindcss(),
      {
        name: "actual-paper-model-picker",
        resolveId: (id) =>
          id === "/__picker_fixture.js" ? "\0picker-fixture" : undefined,
        load: (id) => (id === "\0picker-fixture" ? compiled.code : undefined),
        configureServer(devServer) {
          devServer.middlewares.use("/__model_picker__", (_req, res) => {
            res.setHeader("content-type", "text/html")
            res.end(
              '<!doctype html><html lang="en"><head><title>Paper model picker</title></head><body><div id="root"></div><script type="module" src="/__picker_fixture.js"></script></body></html>',
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

async function open(page: Page, query = "") {
  page.on("pageerror", (error) => process.stderr.write(`${error.stack}\n`))
  await page.addInitScript(() => localStorage.setItem("llm4ad-language", "en"))
  await page.goto(`${origin}/__model_picker__${query}`)
  await expect(
    page.getByRole("button", { name: "Open model settings" }),
  ).toBeVisible({ timeout: 15000 })
  await page.getByRole("button", { name: "Open model settings" }).click()
  await expect(page.getByRole("dialog")).toBeVisible()
}
async function state(page: Page) {
  return JSON.parse((await page.getByTestId("picker-state").textContent())!)
}
async function control(page: Page, name: string) {
  await page
    .getByRole("button", { name, exact: true, includeHidden: true })
    .evaluate((element: HTMLButtonElement) => element.click())
}
async function select(page: Page, index: number, name: string) {
  await page.getByRole("dialog").getByRole("combobox").nth(index).click()
  await page.getByRole("option", { name, exact: true }).click()
}
async function draft(page: Page) {
  await select(page, 0, "Provider A")
  await expect(
    page.getByRole("dialog").getByRole("combobox").nth(1),
  ).toBeEnabled()
  await select(page, 1, "model-a2")
  await select(page, 2, "200,000")
  await select(page, 3, "32,000")
}
const expectedDraft = {
  binding: { providerId: "provider-a", modelName: "model-a2" },
  contextTokens: 200000,
  outputTokens: 32000,
}
async function assertDraft(page: Page) {
  const actual = await state(page)
  if (!actual.binding.providerId)
    process.stderr.write(
      `${JSON.stringify({ providerLabel: await page.getByRole("dialog").getByRole("combobox").nth(0).textContent(), modelDisabled: await page.getByRole("dialog").getByRole("combobox").nth(1).isDisabled(), state: actual })}\n`,
    )
  assert.deepEqual(
    {
      binding: actual.binding,
      contextTokens: actual.contextTokens,
      outputTokens: actual.outputTokens,
    },
    expectedDraft,
    JSON.stringify(actual),
  )
  await expect(
    page.getByRole("dialog").getByRole("combobox").nth(1),
  ).toBeEnabled()
}

test("first provider/model selection stays usable without any polling", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await draft(page)
    await assertDraft(page)
    const observed = await state(page)
    assert.equal(observed.trace.at(-1).providerId, "provider-a")
  } finally {
    await page.close()
  }
})
test("workspace progress refresh preserves unsaved provider/model and token limits while open", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await draft(page)
    await control(page, "Refresh workspace progress")
    await expect
      .poll(async () => (await state(page)).trace.length)
      .toBeGreaterThan(1)
    await assertDraft(page)
  } finally {
    await page.close()
  }
})
test("provider-list refresh preserves an unbound workspace model draft while open", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await draft(page)
    await control(page, "Refresh provider catalog")
    await assertDraft(page)
  } finally {
    await page.close()
  }
})
test("closing and reopening restores persisted model binding and token limits", async () => {
  const page = await browser.newPage()
  try {
    await open(page, "?bound")
    await draft(page)
    await page.getByRole("button", { name: "Close", exact: true }).click()
    await page.getByRole("button", { name: "Open model settings" }).click()
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-b", modelName: "model-b" })
    const actual = await state(page)
    assert.equal(actual.contextTokens, 64000)
    assert.equal(actual.outputTokens, 8192)
  } finally {
    await page.close()
  }
})
test("workspace navigation never leaks the previous workspace model draft", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await draft(page)
    await control(page, "Navigate workspace")
    await expect
      .poll(async () => (await state(page)).workspaceId)
      .toBe("workspace-b")
    if (!(await state(page)).settingsOpen)
      await page.getByRole("button", { name: "Open model settings" }).click()
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-b", modelName: "model-b" })
  } finally {
    await page.close()
  }
})
test("single- and multi-model provider switches reset only the model selection", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await select(page, 0, "Provider B")
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-b", modelName: "model-b" })
    await select(page, 0, "Provider A")
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-a", modelName: "" })
    await select(page, 1, "model-a1")
    await select(page, 0, "Provider C")
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-c", modelName: "" })
    await select(page, 1, "model-c2")
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-c", modelName: "model-c2" })
  } finally {
    await page.close()
  }
})
test("initial provider loading enables first selection once the catalog arrives", async () => {
  const page = await browser.newPage()
  try {
    await open(page, "?loading")
    await expect(
      page.getByRole("dialog").getByRole("combobox").nth(0),
    ).toBeDisabled()
    await control(page, "Finish provider loading")
    await expect(
      page.getByRole("dialog").getByRole("combobox").nth(0),
    ).toBeEnabled()
    await draft(page)
    await assertDraft(page)
  } finally {
    await page.close()
  }
})

test("bound workspace provider loading late preserves its saved binding while open", async () => {
  const page = await browser.newPage()
  try {
    await open(page, "?bound&loading")
    await expect(
      page.getByRole("dialog").getByRole("combobox").nth(0),
    ).toBeDisabled()
    await control(page, "Finish provider loading")
    await expect(
      page.getByRole("dialog").getByRole("combobox").nth(1),
    ).toBeEnabled()
    await expect
      .poll(async () => (await state(page)).binding)
      .toEqual({ providerId: "provider-b", modelName: "model-b" })
    const actual = await state(page)
    assert.equal(actual.contextTokens, 64000)
    assert.equal(actual.outputTokens, 8192)
  } finally {
    await page.close()
  }
})
test("workspace refresh before choosing the first model keeps its provider and model control enabled", async () => {
  const page = await browser.newPage()
  try {
    await open(page)
    await select(page, 0, "Provider A")
    await expect(
      page.getByRole("dialog").getByRole("combobox").nth(1),
    ).toBeEnabled()
    await control(page, "Refresh workspace progress")
    const actual = await state(page)
    const label = await page
      .getByRole("dialog")
      .getByRole("combobox")
      .nth(0)
      .textContent()
    const disabled = await page
      .getByRole("dialog")
      .getByRole("combobox")
      .nth(1)
      .isDisabled()
    assert.equal(
      actual.binding.providerId,
      "provider-a",
      JSON.stringify({ label, disabled, state: actual }),
    )
    await expect(
      page.getByRole("dialog").getByRole("combobox").nth(1),
    ).toBeEnabled()
  } finally {
    await page.close()
  }
})
