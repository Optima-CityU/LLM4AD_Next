import { expect, test } from "bun:test"

import { cssColorToHslChannels } from "../src/lib/embeddedAppearance"
import { parseResearchWorkspaceMode } from "../src/lib/researchWorkspace"

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
const autoResearchLayoutSource = await Bun.file(
  new URL("../src/routes/_layout_autoresearch.tsx", import.meta.url),
).text()
const nativeRuntimeSource = await Bun.file(
  new URL("../src/components/Paper/PaperNativeRuntime.tsx", import.meta.url),
).text()
const paperHooksSource = await Bun.file(
  new URL("../src/hooks/usePapers.ts", import.meta.url),
).text()
const rebuttalPanelSource = await Bun.file(
  new URL("../src/components/Paper/RebuttalEntriesPanel.tsx", import.meta.url),
).text()
const proposalResultsDockSource = await Bun.file(
  new URL("../src/components/Paper/ProposalResultsDock.tsx", import.meta.url),
).text()
const rebuttalBaselinePanelSource = await Bun.file(
  new URL("../src/components/Paper/RebuttalBaselinePanel.tsx", import.meta.url),
).text()
const textareaSource = await Bun.file(
  new URL("../src/components/ui/textarea.tsx", import.meta.url),
).text()
const discoveryPanelSource = await Bun.file(
  new URL(
    "../src/components/Paper/AlgorithmDiscoveryPanel.tsx",
    import.meta.url,
  ),
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
const cloudCliChatInterfaceSource = await Bun.file(
  new URL(
    "../../../third_party/CloudCLI/src/modules/chat/ChatInterface.tsx",
    import.meta.url,
  ),
).text()
const cloudCliComposerSource = await Bun.file(
  new URL(
    "../../../third_party/CloudCLI/src/modules/chat/hooks/useChatComposerState.ts",
    import.meta.url,
  ),
).text()
const cloudCliToolRendererSource = await Bun.file(
  new URL(
    "../../../third_party/CloudCLI/src/modules/chat/tools/ToolRenderer.tsx",
    import.meta.url,
  ),
).text()
const stagePublicationSkill = await Bun.file(
  new URL(
    "../../../skills/research-stage-publication/SKILL.md",
    import.meta.url,
  ),
).text()
const zh = await Bun.file(
  new URL("../src/i18n/locales/zh.json", import.meta.url),
).json()
const en = await Bun.file(
  new URL("../src/i18n/locales/en.json", import.meta.url),
).json()
const cloudCliZh = await Bun.file(
  new URL(
    "../../../third_party/CloudCLI/src/modules/i18n/locales/zh-CN/chat.json",
    import.meta.url,
  ),
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

test("autoresearch back navigation returns to the project list", () => {
  expect(autoResearchLayoutSource).toContain('to="/projects"')
  expect(autoResearchLayoutSource).toContain(
    'title={t("autoResearch.header.backToProjects")}',
  )
})

test("autorebuttal and autodiscovery are always available", () => {
  expect(parseResearchWorkspaceMode("manuscript")).toBe("manuscript")
  expect(parseResearchWorkspaceMode("algorithm")).toBe("algorithm")
  expect(sidebarSource).toContain('mode: "manuscript"')
  expect(sidebarSource).toContain('mode: "algorithm"')
  expect(sidebarSource).not.toContain("AUTO_REBUTTAL_ENABLED")
  expect(sidebarSource).not.toContain("AUTO_DISCOVERY_ENABLED")
  expect(paperRouteSource).not.toContain("AUTO_REBUTTAL_ENABLED")
  expect(paperRouteSource).not.toContain("AUTO_DISCOVERY_ENABLED")
})

test("autorebuttal presents reviewer responses and author guidance without duplicate final text", () => {
  expect(rebuttalPanelSource).toContain("source_refs")
  expect(rebuttalPanelSource).toContain("concern_ids")
  expect(rebuttalPanelSource).toContain("rebuttalOutput.global_response")
  expect(rebuttalPanelSource).toContain("rebuttalOutput?.open_placeholders")
  expect(rebuttalPanelSource).toContain("rebuttalOutput?.findings")
  expect(rebuttalPanelSource).not.toContain("rebuttalOutput.text")
  expect(rebuttalPanelSource).not.toContain("rebuttalOutput.total_limit")
  expect(rebuttalPanelSource).not.toContain("rebuttalOutput.reviewer_counts")
  expect(rebuttalPanelSource).not.toContain("URL.createObjectURL")
  expect(rebuttalPanelSource).not.toContain('value="actions"')
  expect(paperRouteSource).toContain(
    "rebuttalOutput={workspace.rebuttal_output}",
  )
  expect(rebuttalPanelSource).toContain(
    'data-testid="rebuttal-guidance-layout"',
  )
  expect(rebuttalPanelSource).toContain(
    'data-testid="rebuttal-author-action-list"',
  )
  expect(rebuttalPanelSource).toContain(
    'data-testid="rebuttal-agent-suggestion-list"',
  )
  expect(rebuttalPanelSource).not.toContain("max-w-3xl")
})

test("rebuttal guidance uses readable full-width rows at every panel width", () => {
  const guidance = rebuttalPanelSource.slice(
    rebuttalPanelSource.indexOf('data-testid="rebuttal-guidance-layout"'),
  )
  expect(guidance).not.toContain("@min-[40rem]:grid-cols-2")
  expect(guidance).toContain('data-testid="rebuttal-author-action-list"')
  expect(guidance).toContain('data-testid="rebuttal-agent-suggestion-list"')
  expect(rebuttalPanelSource).toContain("text-base leading-7")
})

test("autorebuttal presents the published concern baseline as a separate readable section", () => {
  expect(paperRouteSource).toContain(
    "baselineContext={workspace.rebuttal_context}",
  )
  expect(paperRouteSource).toContain("baselineStale={")
  expect(rebuttalPanelSource).toContain("parseRebuttalBaseline")
  expect(rebuttalPanelSource).toContain("<RebuttalBaselinePanel")
  expect(rebuttalPanelSource).toContain('data-testid="review-feedback-groups"')
  expect(rebuttalPanelSource).toContain('value="reviews"')
  expect(rebuttalPanelSource).toContain('value="baseline"')
  expect(rebuttalPanelSource).toContain('value="chair"')
  expect(rebuttalPanelSource).toContain("viewForStage(activeStage)")
  expect(rebuttalPanelSource).not.toContain("concernsForReview")
  expect(rebuttalPanelSource).toContain("reviewerConcerns")
  expect(rebuttalPanelSource).toContain("baselineSource")
  expect(rebuttalBaselinePanelSource).toContain(
    'data-testid="rebuttal-baseline"',
  )
  expect(rebuttalBaselinePanelSource).toContain("baseline.concerns")
  expect(rebuttalBaselinePanelSource).toContain("paper.rebuttal.responsePlan")
  expect(rebuttalBaselinePanelSource).toContain("aria-pressed={selected}")
  expect(rebuttalBaselinePanelSource).toContain("selectedGroup.concerns.map")
  expect(rebuttalBaselinePanelSource).toContain("grid-cols-3")
  expect(rebuttalBaselinePanelSource).not.toContain("open\n")
  expect(rebuttalBaselinePanelSource).not.toContain("text-[10px]")
  expect(rebuttalBaselinePanelSource).not.toContain("text-[11px]")
})

test("AC message is a separate third stage with a copy-ready result", () => {
  expect(workflowSource).toContain(
    '"ac_summary": ("ac-summary", _REVISION_PUBLICATION_SKILL)',
  )
  expect(paperRouteSource).toContain("chairMessage={workspace.chair_message}")
  expect(rebuttalPanelSource).toContain('data-testid="rebuttal-chair-message"')
  expect(rebuttalPanelSource).toContain("copy(chairMessage)")
  expect(rebuttalPanelSource).toContain("viewForStage(activeStage)")
  expect(zh.paper.workflow.stage.ac_summary).toBeTruthy()
  expect(en.paper.workflow.stage.ac_summary).toBeTruthy()
})

test("rebuttal copy actions stay inside their response card", () => {
  expect(rebuttalPanelSource).not.toContain("<CardAction>")
  expect(rebuttalPanelSource).toContain("absolute top-2 right-2")
  expect(rebuttalPanelSource).toContain("useCopyToClipboard")
})

test("reviewer feedback and rebuttals expand together without overflowing", () => {
  expect(rebuttalPanelSource).toContain("aria-expanded={expanded}")
  expect(rebuttalPanelSource).toContain("aria-controls={`review-content-")
  expect(rebuttalPanelSource).toContain(
    "groupedEntries[reviewerGroup.reviewerId]",
  )
  expect(rebuttalPanelSource).toContain("hasAutoExpandedReview")
  expect(rebuttalPanelSource).toContain(
    "paper.rebuttal.reviewResponseRelationship",
  )
  expect(rebuttalPanelSource).toContain("paper.rebuttal.expandReviewAction")
  expect(rebuttalPanelSource).toContain("paper.rebuttal.noResponseNeeded")
  expect(rebuttalPanelSource).toContain("reviewerConcernCounts")
  expect(rebuttalPanelSource).not.toContain("Object.entries(groupedEntries)")
  expect(rebuttalPanelSource).not.toContain("lg:grid-cols-[minmax")
  expect(rebuttalPanelSource).toContain("<ReactMarkdown")
  expect(rebuttalPanelSource).toContain("remarkPlugins={[remarkGfm]}")
  expect(rebuttalPanelSource).toContain("overflow-x-hidden")
  expect(rebuttalPanelSource).toContain("[&_table]:overflow-x-auto")
  expect(rebuttalPanelSource).toContain("paper.rebuttal.reviewerRebuttal")
  expect(rebuttalPanelSource).not.toContain("Boolean(detailReviewId)")
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

test("proposal stage navigation stays in the left pane and the right panel switches views", () => {
  const splitLayout = paperRouteSource.indexOf(
    'className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-',
  )
  const leftPane = paperRouteSource.indexOf(
    '<section className="flex h-full min-w-0 flex-col overflow-hidden bg-muted/[0.07]">',
    splitLayout,
  )
  expect(splitLayout).toBeLessThan(leftPane)
  expect(leftPane).toBeLessThan(
    paperRouteSource.indexOf("<PaperWorkflowStepper", leftPane),
  )
  expect(paperRouteSource).toContain("<ProposalResultsDock")
  expect(paperRouteSource).toContain("stage={proposalResultStage}")
  expect(proposalResultsDockSource).not.toContain("stages.map(")
  expect(proposalResultsDockSource).toContain('<TabsTrigger value="result">')
  expect(proposalResultsDockSource).toContain('<TabsTrigger value="editor">')
  expect(paperRouteSource).toContain("proposalEditorOpen")
  expect(paperRouteSource).toContain("setProposalEditorOpen(true)")
  expect(paperRouteSource).toMatch(
    /setProposalEditorState\(\{\s+workspaceId,\s+stage,\s+open: false/,
  )
  expect(proposalResultsDockSource).toContain("onEditorOpenChange(true)")
  expect(proposalResultsDockSource).toContain("stage.summary")
  expect(proposalResultsDockSource).toContain("stage.files.map")
})

test("proposal onboarding saves author basics for later stage prompts", () => {
  expect(paperRouteSource).toContain("paper.source.basicsTitle")
  expect(paperRouteSource).toContain("serializeProposalBasics(proposalBasics)")
  expect(paperRouteSource).toContain("updateWorkspace.mutateAsync")
  expect(paperRouteSource).toContain("void openInitialUpload()")
  expect(paperRouteSource).toContain("onEditBasics=")
  expect(paperRouteSource).toMatch(
    /workspace\.mode === "proposal"\s*\? workspace\.description/,
  )
  expect(runtimeApiSource).toContain("Author-supplied proposal brief")
  expect(runtimeApiSource).toContain("project_context/")
  expect(paperRouteSource).toContain("projectContextUploads(pendingFiles)")
  expect(paperRouteSource).toContain(
    "showProjectContextPrompt={showProjectContextPrompt}",
  )
  expect(nativeRuntimeSource).toContain("paper.source.projectContext.title")
  expect(proposalResultsDockSource).toContain("onUploadProjectContext")
  expect(proposalResultsDockSource).toContain("{editorOpen &&")
})

test("stage blockers stay compact and use stage-specific guidance", () => {
  expect(nativeRuntimeSource).toContain("<PopoverContent")
  expect(nativeRuntimeSource).toContain("paper.runtime.revisionDetails")
  expect(nativeRuntimeSource).toContain(
    "paper.runtime.rebuttalBaselineRevisionRequired",
  )
  expect(nativeRuntimeSource).toContain('stage !== "autorebuttal"')
  expect(nativeRuntimeSource).not.toContain(
    "paper.runtime.autorebuttalRevisionRequired",
  )
  expect(nativeRuntimeSource).not.toContain('className="flex max-h-40 shrink-0')
  expect(zh.paper.runtime.revisionRequired).not.toContain("终审")
})

test("editing a prior turn invalidates only the displayed stage revision", () => {
  expect(cloudCliComposerSource).toContain(
    "type: 'llm4ad:conversation-rewound'",
  )
  expect(nativeRuntimeSource).toContain('"llm4ad:conversation-rewound"')
  expect(nativeRuntimeSource).toContain("useInvalidatePaperWorkflowStage")
  expect(nativeRuntimeSource).toContain(
    "expected_iteration: stageState?.iteration",
  )
})

test("embedded research assistants hide unsupported conversation forking", () => {
  expect(cloudCliChatInterfaceSource).toContain(
    "!IS_LLM4AD_EMBEDDED && supportsSessionForking",
  )
})

test("embedded research tool records cannot navigate the iframe", () => {
  expect(cloudCliToolRendererSource).toContain(
    "allowNavigation={!IS_LLM4AD_EMBEDDED}",
  )
  expect(cloudCliToolRendererSource).toContain(
    "interactive={!IS_LLM4AD_EMBEDDED}",
  )
})

test("autodiscovery builds validated tasks before project import", () => {
  expect(workflowSource).toContain('stages=("discovery",)')
  expect(workflowSource).toContain('"algorithm-discovery"')
  expect(workflowSource).toContain('"llm4ad-task-builder"')
  expect(taskDockerfileSource).toContain(
    "skills/autodiscovery/algorithm-discovery",
  )
  expect(paperRouteSource).toContain('workspace.mode !== "algorithm"')
  expect(paperRouteSource).toContain("<AlgorithmDiscoveryPanel")
  expect(discoveryPanelSource).toContain("useCreatePaperProposalTasks")
  expect(discoveryPanelSource).toContain("proposal_ids: [proposal.id]")
  expect(discoveryPanelSource).toContain(
    'validation_report?.status === "passed"',
  )
  expect(discoveryPanelSource).toContain("proposal.package_manifest")
  expect(runtimeApiSource).toContain("mcp__llm4ad_stage__build_algorithm_task")
  expect(workflowSource).toContain("complete runnable task package")
  expect(discoveryPanelSource).toContain('to: "/evolution"')
  expect(zh.paper.workflow.stage.discoveryExamples).toHaveLength(3)
  expect(en.paper.workflow.stage.discoveryExamples).toHaveLength(3)
})

test("all paper workflows show model settings below the setup guidance", () => {
  expect(nativeRuntimeSource).toContain(
    "if (!modelReady || !prerequisiteReady)",
  )
  expect(nativeRuntimeSource).toContain("!modelReady &&")
  expect(nativeRuntimeSource).toContain("onClick={onConfigureModel}")
  expect(nativeRuntimeSource).toContain('t("paper.model.configure")')
  expect(paperRouteSource).toContain("modelReady={modelReady}")
  expect(paperRouteSource).toContain("onConfigureModel={openModelSettings}")
})

test("proposal and rebuttal expose conversation preferences separately from deliverables", () => {
  expect(nativeRuntimeSource).toContain("onConfigurePreferences")
  expect(nativeRuntimeSource).toContain('t("paper.preferences.configure")')
  expect(paperRouteSource).toContain("conversation_preferences:")
  expect(paperRouteSource).toContain(
    "JSON.stringify(workspace.conversation_preferences ?? {})",
  )
  expect(paperRouteSource).toContain('workspace.mode !== "algorithm" && (')
  expect(generatedTypesSource).toContain(
    "export type PaperConversationPreferences",
  )
  expect(zh.paper.preferences.languageChinese).toBeTruthy()
  expect(en.paper.preferences.languageEnglish).toBeTruthy()
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

test("Anthropic and OpenAI providers can be bound through the protocol adapter", () => {
  expect(paperRouteSource).toContain("CLAUDE_RUNTIME_PROVIDER_TYPES")
  expect(paperRouteSource).toContain('"anthropic"')
  expect(paperRouteSource).toContain('"openai"')
  expect(paperRouteSource).toContain('"openai_compatible"')
  expect(zh.paper.model.noProvidersHint).toContain("OpenAI")
  expect(en.paper.model.noProvidersHint).toContain("OpenAI")
})

test("the generated client exposes CloudCLI bootstrap and no legacy run API", () => {
  expect(generatedSdkSource).toContain("createRuntimeSession")
  expect(generatedSdkSource).toContain("invalidateWorkflowStage")
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
  expect(workflowSource).toContain('"research-stage-publication"')
  expect(stagePublicationSkill).toContain(
    "edits and replaces an earlier conversation turn",
  )
  expect(stagePublicationSkill).toContain("call `publish_stage_result` once")
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

test("autorebuttal accepts complete review text without artificial limits", () => {
  expect(rebuttalBaselineSkill).toContain(
    "Only set `ready_for_generation` false",
  )
  expect(rebuttalInputContract).toContain("Default to `per_reviewer`")
  expect(rebuttalInputContract).toContain("Default to `markdown`")
  expect(rebuttalInputContract).toContain("numeric limit")
  expect(rebuttalBaselineSkill).not.toContain("truncated")
  expect(rebuttalInputContract).not.toContain("truncated")
  expect(textareaSource).not.toContain("maxLength = 2000")
})

test("autorebuttal publishes useful author guidance instead of compliance logs", () => {
  expect(autoRebuttalSkill).toContain("author-facing guidance")
  expect(rebuttalDraftingGuide).toContain("internal audit log")
  expect(rebuttalArtifactContracts).toContain("concrete next step")
  expect(rebuttalArtifactContracts).toContain("specific author input")
})

test("blocking final review returns to the owning stage without failing publication", () => {
  expect(generatedTypesSource).toContain("'ready' | 'stale' | 'needs_revision'")
  expect(nativeRuntimeSource).toContain('status === "needs_revision"')
  expect(nativeRuntimeSource).toContain("stageState.findings")
  expect(paperRouteSource).toContain('"needsRevision"')
  expect(nativeRuntimeSource).toContain(
    "expected_iteration: stageState?.iteration",
  )
  expect(paperRouteSource).toContain("stagePublished")
  expect(openAirProposalAttribution).toContain("review-and-return loop")
})

test("the paper runtime websocket is upgraded before the general API proxy", () => {
  const websocketLocation = nginxApiProxySource.indexOf("cloudcli/ws$")
  const generalApiLocation = nginxApiProxySource.indexOf("location /api {")

  expect(websocketLocation).toBeGreaterThan(-1)
  expect(websocketLocation).toBeLessThan(generalApiLocation)
  expect(nginxApiProxySource).not.toContain("openreview-browser/cast")
  expect(nginxApiProxySource).toContain(
    "proxy_set_header Upgrade $http_upgrade;",
  )
  expect(nginxApiProxySource).toContain(
    "proxy_set_header Connection $connection_upgrade;",
  )
})

test("rebuttal intake guides pasted text or saved manual review without a browser", () => {
  expect(nativeRuntimeSource).not.toContain("useOpenReviewBrowser")
  expect(paperHooksSource).not.toContain("useOpenReviewBrowser")
  expect(rebuttalPanelSource).toContain("openCreateEditor")
  expect(zh.paper.workflow.stage.rebuttal_baselineHint).toContain("粘贴")
  expect(zh.paper.rebuttal.noReviewsDescription).toContain("右侧")
  expect(en.paper.workflow.stage.rebuttal_baselineHint).toContain("Paste")
  expect(en.paper.rebuttal.noReviewsDescription).toContain("panel")
  expect(rebuttalBaselineSkill).toContain("review text the author pasted")
  expect(rebuttalBaselineSkill).not.toContain("openreview.get_forum")
  expect(rebuttalInputContract).toContain('source_system: "conversation"')
  expect(cloudCliEmptyStateSource).toContain("<textarea")
  expect(cloudCliEmptyStateSource).toContain("reviewPastePrompt")
  expect(cloudCliEmptyStateSource).not.toContain('type="url"')
  expect(cloudCliZh.session.continue.reviewPasteDescription).toContain(
    "多位审稿人",
  )
  expect(cloudCliZh.session.continue).not.toHaveProperty("openReviewPrompt")
  expect(rebuttalPanelSource).toMatch(/baselineSource\s*\??\.reviewMarkdown/)
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
const typstFontFetchScript = await Bun.file(
  new URL("../scripts/fetch-typst-fonts.sh", import.meta.url),
).text()
const frontendDockerfileSource = await Bun.file(
  new URL("../Dockerfile", import.meta.url),
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
const rebuttalBaselineSkill = await Bun.file(
  new URL(
    "../../../skills/autorebuttal/rebuttal-baseline/SKILL.md",
    import.meta.url,
  ),
).text()
const rebuttalInputContract = await Bun.file(
  new URL(
    "../../../skills/autorebuttal/shared/references/input-contract.md",
    import.meta.url,
  ),
).text()
const autoRebuttalSkill = await Bun.file(
  new URL(
    "../../../skills/autorebuttal/autorebuttal/SKILL.md",
    import.meta.url,
  ),
).text()
const rebuttalDraftingGuide = await Bun.file(
  new URL(
    "../../../skills/autorebuttal/shared/references/drafting-and-compliance.md",
    import.meta.url,
  ),
).text()
const rebuttalArtifactContracts = await Bun.file(
  new URL(
    "../../../skills/autorebuttal/shared/references/artifact-contracts.md",
    import.meta.url,
  ),
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

test("typst extra fonts are self-hosted from pinned upstream assets", () => {
  // The two asset repositories do not hold the same files, and their main branch
  // holds more than the pinned tag does. A face listed from the wrong repo or
  // tag 404s at compile time and never loads, silently reintroducing the
  // warnings this list exists to remove.
  const urls = [...typstFontsSource.matchAll(/\$\{TYPST_FONT_BASE\}([^`]+)`/g)]
  expect(urls.length).toBeGreaterThan(0)
  expect(typstFontsSource).toContain(
    'export const TYPST_FONT_BASE = "/typst-fonts/"',
  )
  for (const match of urls) {
    expect(match[1]).toMatch(/\.(otf|ttf)$/)
  }
  expect(typstFontFetchScript).toContain("typst-assets@v0.13.1")
  expect(typstFontFetchScript).toContain("typst-dev-assets@v0.13.1")
  expect(typstFontFetchScript).toContain("sha256sum")
  // Bold CJK is the specific gap in the typst.ts defaults, so its absence would
  // mean Chinese headings fall back again.
  expect(typstFontsSource).toContain("NotoSerifCJKsc-Bold.otf")
})

test("frontend builds reuse the verified Typst font cache", () => {
  expect(typstFontFetchScript).toContain("TYPST_FONT_CACHE_DIR")
  expect(frontendDockerfileSource).toContain(
    "COPY src/frontend/scripts/fetch-typst-fonts.sh /app/scripts/",
  )
  expect(frontendDockerfileSource).toContain("id=llm4ad-typst-fonts")
  expect(frontendDockerfileSource).toContain("sharing=locked")
  expect(
    frontendDockerfileSource.indexOf("id=llm4ad-typst-fonts"),
  ).toBeLessThan(frontendDockerfileSource.indexOf("COPY ./src/frontend /app"))
})

test("frontend starts nginx directly without the stock entrypoint hooks", () => {
  expect(frontendDockerfileSource).toContain("ENTRYPOINT []")
  expect(frontendDockerfileSource).toContain(
    'CMD ["nginx", "-g", "daemon off;"]',
  )
  expect(frontendDockerfileSource).not.toContain(
    "10-listen-on-ipv6-by-default.sh",
  )
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
