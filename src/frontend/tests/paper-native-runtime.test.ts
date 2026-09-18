import { expect, test } from "bun:test"

import { cssColorToHslChannels } from "../src/lib/embeddedAppearance"

const sidebarSource = await Bun.file(
  new URL("../src/components/Sidebar/AppSidebar.tsx", import.meta.url),
).text()
const layoutSource = await Bun.file(
  new URL("../src/routes/_layout.tsx", import.meta.url),
).text()
const paperRouteSource = await Bun.file(
  new URL("../src/routes/_layout/papers.tsx", import.meta.url),
).text()
const paperLayoutSource = await Bun.file(
  new URL("../src/routes/_layout_paper.tsx", import.meta.url),
).text()
const nativeRuntimeSource = await Bun.file(
  new URL("../src/components/Paper/PaperNativeRuntime.tsx", import.meta.url),
).text()
const paperHooksSource = await Bun.file(
  new URL("../src/hooks/usePapers.ts", import.meta.url),
).text()
const generatedSdkSource = await Bun.file(
  new URL("../src/client/sdk.gen.ts", import.meta.url),
).text()
const generatedTypesSource = await Bun.file(
  new URL("../src/client/types.gen.ts", import.meta.url),
).text()
const runtimeApiSource = await Bun.file(
  new URL("../../backend/app/api/llm4ad/paper_runtime.py", import.meta.url),
).text()
const workflowSource = await Bun.file(
  new URL("../../backend/app/services/paper_workflow.py", import.meta.url),
).text()
const taskDockerfileSource = await Bun.file(
  new URL("../../backend/Dockerfile.task", import.meta.url),
).text()
const nginxApiProxySource = await Bun.file(
  new URL("../nginx-api-proxy.conf", import.meta.url),
).text()
const cloudCliEmptyStateSource = await Bun.file(
  new URL(
    "../../../third_party/CloudCLI/src/modules/chat/transcript/ProviderSelectionEmptyState.tsx",
    import.meta.url,
  ),
).text()
const cloudCliHostContextSource = await Bun.file(
  new URL(
    "../../../third_party/CloudCLI/src/shared/embeddedHostContext.ts",
    import.meta.url,
  ),
).text()
const zh = await Bun.file(
  new URL("../src/i18n/locales/zh.json", import.meta.url),
).json()
const en = await Bun.file(
  new URL("../src/i18n/locales/en.json", import.meta.url),
).json()

test("host CSS colors are converted to CloudCLI HSL channels", () => {
  expect(cssColorToHslChannels("#000000")).toBe("0 0% 0%")
  expect(cssColorToHslChannels("#ffffff")).toBe("0 0% 100%")
  expect(cssColorToHslChannels("#f8fafc")).toBe("210 40% 98.039%")
  expect(cssColorToHslChannels("rgb(37, 99, 235)")).toBe(
    "221.212 83.193% 53.333%",
  )
  expect(cssColorToHslChannels("not-a-color")).toBeNull()
})

test("research projects remain independent from normal project management", () => {
  expect(sidebarSource).toContain("function ResearchWorkspaceNav")
  expect(sidebarSource).toContain('path: "/papers"')
  expect(paperRouteSource).not.toContain("workspace.project_id")
  expect(layoutSource).toContain("<AppSidebar />")
  expect(paperLayoutSource).not.toContain("AppSidebar")
  expect(paperLayoutSource).toContain("h-screen")
})

test("the top workflow navigator hosts one native CloudCLI session", () => {
  expect(paperRouteSource).toContain("<PaperWorkflowStepper")
  expect(paperRouteSource).toContain("<PaperNativeRuntime")
  expect(paperRouteSource).not.toContain("PaperAgentQuestionPanel")
  expect(paperRouteSource).not.toContain("usePaperRunStream")
  expect(nativeRuntimeSource).toContain("<iframe")
  expect(nativeRuntimeSource).toContain("session.data.runtime_url")
  expect(nativeRuntimeSource).toContain(
    'sandbox="allow-forms allow-same-origin allow-scripts"',
  )
  expect(nativeRuntimeSource).not.toContain("allow-downloads")
  expect(nativeRuntimeSource).toContain('type: "llm4ad:appearance"')
  expect(nativeRuntimeSource).toContain("stageTitle")
  expect(nativeRuntimeSource).toContain("stageDescription")
  expect(nativeRuntimeSource).toContain("onConfigureModel")
  expect(nativeRuntimeSource).toContain("artifactPaths")
})

test("every proposal stage offers concrete conversation starters", () => {
  const stages = [
    "formatting",
    "literature",
    "rationale",
    "objectives",
    "methods",
    "innovation_plan",
    "foundation_feasibility",
    "final_review",
  ] as const

  for (const stage of stages) {
    const examples = zh.paper.workflow.stage[`${stage}Examples`]
    expect(examples).toHaveLength(3)
    expect(examples.every((example: string) => example.length > 20)).toBeTrue()
  }
  expect(nativeRuntimeSource).toContain("stageExamples")
  expect(cloudCliHostContextSource).toContain("normalizedTextList")
  expect(cloudCliEmptyStateSource).toContain("applyStageExample")
  expect(cloudCliEmptyStateSource).toContain("setInput(example)")
})

test("only native Anthropic providers can be bound to research sessions", () => {
  expect(paperRouteSource).toContain('provider.type === "anthropic"')
  expect(zh.paper.model.noProvidersHint).toContain("Anthropic Messages")
  expect(en.paper.model.noProvidersHint).toContain("Anthropic Messages")
})

test("the generated client exposes CloudCLI bootstrap and no legacy run API", () => {
  expect(generatedSdkSource).toContain("createRuntimeSession")
  expect(paperHooksSource).toContain("usePaperRuntimeSession")
  expect(paperHooksSource).not.toContain("usePaperAgentRun")
  expect(generatedSdkSource).not.toContain("answerRunInteraction")
  expect(generatedSdkSource).not.toContain("resumeRun")
  expect(generatedSdkSource).not.toContain("cancelRun")
})

test("stage profiles use official Skills, MCP tools, and native questions", () => {
  expect(runtimeApiSource).toContain('"AskUserQuestion"')
  expect(runtimeApiSource).toContain('"Skill"')
  expect(runtimeApiSource).toContain('"mcpServers": mcp_servers')
  expect(runtimeApiSource).toContain("mcp__llm4ad_stage__publish_stage_result")
  expect(runtimeApiSource).toContain("workflow.skills_by_run_kind")
  expect(runtimeApiSource).toContain("contextWindowTokens")
  expect(workflowSource).toContain("skills_by_run_kind")
  expect(workflowSource).toContain('"proposal-literature-evidence"')
})

test("the formatting stage loads the vendored Typst skill and its doc mirror", () => {
  // The stage needs both: `typst-author` owns Typst syntax and ships the local
  // docs mirror, while `proposal-foundation-layout` owns the proposal-specific
  // foundation and write boundary. Dropping the first silently restores the
  // failure it was added for — a stage inventing syntax such as
  // `#numbered-list` (the real function is `enum`) because nothing told it to
  // read the language instead of recalling it.
  expect(workflowSource).toContain('"typst-author"')
  expect(workflowSource).toContain('"proposal-foundation-layout"')
})

test("the vendored Typst skill ships its documentation and attribution", () => {
  // The skill is only useful if its docs actually reach the image: it works by
  // having the agent read `docs/` rather than recall the language, so a build
  // that drops the directory leaves the skill pointing at nothing.
  expect(taskDockerfileSource).toContain("/app/skills/typst-author")
  expect(taskDockerfileSource).not.toContain("typst-proposal-layout")

  // It must instruct the reader to consult the bundled docs, which is the whole
  // mechanism by which it prevents hallucinated syntax.
  expect(vendoredTypstSkillSource).toContain("docs/")
  expect(vendoredTypstAttribution).toContain("apcamargo/typst-skills")

  // The mirror must carry the reference that names list functions, since that
  // is the specific gap that produced the original compile error.
  expect(vendoredTypstSyntaxDoc).toContain("enum")
})

test("proposal skills are grouped and retain OpenAIR_proposal attribution", () => {
  expect(openAirProposalAttribution).toContain(
    "https://github.com/Maxine-1520/OpenAIR_proposal",
  )
  expect(openAirProposalAttribution).toContain("write-key-problems")
  expect(taskDockerfileSource).toContain(
    "skills/openair-proposal/proposal-innovation-plan",
  )
  expect(taskDockerfileSource).toContain(
    "skills/openair-proposal/proposal-foundation-feasibility",
  )
  expect(workflowSource).toContain('"proposal_innovation_plan"')
  expect(workflowSource).toContain('"proposal_foundation_feasibility"')
})

test("blocking final review returns to the owning stage without failing publication", () => {
  expect(generatedTypesSource).toContain(
    "'ready' | 'stale' | 'needs_revision'",
  )
  expect(nativeRuntimeSource).toContain('status === "needs_revision"')
  expect(nativeRuntimeSource).toContain("stageState.findings")
  expect(paperRouteSource).toContain("paper.workflow.needsRevision")
  expect(paperRouteSource).toContain("paper.workflow.iteration")
  expect(paperRouteSource).toContain("stagePublished")
  expect(openAirProposalAttribution).toContain("review-and-return loop")
})

test("the paper runtime websocket is upgraded before the general API proxy", () => {
  const websocketLocation = nginxApiProxySource.indexOf("cloudcli/ws$")
  const generalApiLocation = nginxApiProxySource.indexOf("location /api {")

  expect(websocketLocation).toBeGreaterThan(-1)
  expect(websocketLocation).toBeLessThan(generalApiLocation)
  expect(nginxApiProxySource).toContain(
    "proxy_set_header Upgrade $http_upgrade;",
  )
  expect(nginxApiProxySource).toContain(
    "proxy_set_header Connection $connection_upgrade;",
  )
})

test("the task image contains only the native runtime bridge", () => {
  expect(taskDockerfileSource).toContain("/app/cloudcli/dist-server")
  expect(taskDockerfileSource).toContain("/app/paper-agent/stage_bridge.py")
  expect(taskDockerfileSource).toContain(
    "/app/paper-agent/workspace-entrypoint.sh",
  )
  expect(taskDockerfileSource).not.toContain("/app/paper-agent/runner.py")
  expect(taskDockerfileSource).not.toContain("/app/paper-agent/interaction.py")
})

const typstFontsSource = await Bun.file(
  new URL("../src/components/Paper/typstFonts.ts", import.meta.url),
).text()
const typstPreviewSource = await Bun.file(
  new URL("../src/components/Paper/TypstLivePreview.tsx", import.meta.url),
).text()
const vendoredTypstSkillSource = await Bun.file(
  new URL("../../../skills/typst-author/SKILL.md", import.meta.url),
).text()
const vendoredTypstAttribution = await Bun.file(
  new URL("../../../skills/typst-author/ATTRIBUTION.md", import.meta.url),
).text()
const openAirProposalAttribution = await Bun.file(
  new URL("../../../skills/openair-proposal/ATTRIBUTION.md", import.meta.url),
).text()
const vendoredTypstSyntaxDoc = await Bun.file(
  new URL(
    "../../../skills/typst-author/docs/reference/language/syntax.md",
    import.meta.url,
  ),
).text()

test("typst preview registers extra fonts with the compiler", () => {
  // The preview compiles in the browser through typst.ts, which reads no system
  // fonts. A font the templates name but the compiler never registers renders a
  // fallback and logs `unknown font family`, so the registration is what makes
  // the declared fallback chains real.
  expect(typstPreviewSource).toContain("TYPST_EXTRA_FONTS")
  expect(typstPreviewSource).toContain("loadFonts")
  // Added alongside the defaults, not instead of them.
  expect(typstPreviewSource).toContain("preloadFontAssets")
})

test("typst extra fonts point at the pinned asset tag", () => {
  // The two asset repositories do not hold the same files, and their main branch
  // holds more than the pinned tag does. A face listed from the wrong repo or
  // tag 404s at compile time and never loads, silently reintroducing the
  // warnings this list exists to remove.
  const urls = [
    ...typstFontsSource.matchAll(
      /\$\{(TYPST_ASSETS|TYPST_DEV_ASSETS)\}([^`]+)`/g,
    ),
  ]
  expect(urls.length).toBeGreaterThan(0)

  const bases: Record<string, string> = {
    TYPST_ASSETS:
      "https://cdn.jsdelivr.net/gh/typst/typst-assets@v0.13.1/files/fonts/",
    TYPST_DEV_ASSETS:
      "https://cdn.jsdelivr.net/gh/typst/typst-dev-assets@v0.13.1/files/fonts/",
  }
  for (const match of urls) {
    const base = bases[match[1]]
    expect(base, `unknown base ${match[1]}`).toBeTruthy()
    // Every entry must be absolute, version-pinned, and end in a font file.
    expect(base).toContain("@v")
    expect(match[2]).toMatch(/\.(otf|ttf)$/)
  }
  // Bold CJK is the specific gap in the typst.ts defaults, so its absence would
  // mean Chinese headings fall back again.
  expect(typstFontsSource).toContain("NotoSerifCJKsc-Bold.otf")
})

test("the document dock keeps the editor collapsed until asked for it", () => {
  // Reading the preview is the common case and changing the source is the
  // exception, so the editor must not claim space by default. It also has to be
  // re-openable and re-sizable, which is what the collapse toggle and the
  // group's separator provide.
  expect(paperRouteSource).toContain("editorCollapsed")
  expect(paperRouteSource).toContain("collapsible")
  expect(paperRouteSource).toContain("collapsedSize")
  // Remembered per workspace, so one proposal's layout does not leak into another.
  expect(paperRouteSource).toContain("editor-collapsed")
  expect(paperRouteSource).toContain("editor-height")
  // The collapse state is mirrored from the panel, which can also collapse by a
  // drag; deriving it only from the toggle would desync the two.
  expect(paperRouteSource).toContain("isCollapsed()")
})

test("the document dock file tree collapses independently of the editor", () => {
  // The tree and the editor are separate surfaces with separate reasons to be
  // open, so one collapsing must not change the other, and each remembers its
  // own state per workspace.
  expect(paperRouteSource).toContain("files-collapsed")
  expect(paperRouteSource).toContain("filesCollapsed")
  // The rail has to carry the control that reopens it, so the tree is collapsed
  // by width rather than unmounted.
  expect(paperRouteSource).toContain("collapseFiles")
  expect(paperRouteSource).toContain("expandFiles")
})

test("the collapsed editor unmounts its code editor instead of hiding it", () => {
  // A collapsed panel is one header tall, so an editor left mounted inside it
  // would hold a CodeMirror instance and its document for a surface nobody is
  // looking at.
  expect(paperRouteSource).toContain("{!editorCollapsed && (")
})

test("native runtime boundary states are translated", () => {
  for (const locale of [zh, en]) {
    expect(locale.paper.runtime.title).toBeTruthy()
    expect(locale.paper.runtime.starting).toBeTruthy()
    expect(locale.paper.runtime.modelTitle).toBeTruthy()
    expect(locale.paper.runtime.prerequisiteTitle).toBeTruthy()
    expect(locale.paper.runtime.unavailableTitle).toBeTruthy()
    expect(locale.paper.runtime.stageHint).toBeTruthy()
    expect(locale.paper.runtime.artifactCount).toBeTruthy()
    expect(locale.paper.runtime.artifactPending).toBeTruthy()
  }
})
