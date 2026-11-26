import { expect, test } from "bun:test"
import { createInstance } from "i18next"
import { type ComponentProps, createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { I18nextProvider } from "react-i18next"

import type { ProposalStageState } from "../src/client"
import ResearchAgentProgressPanel from "../src/components/Paper/ResearchAgentProgressPanel"
import en from "../src/i18n/locales/en.json"
import zh from "../src/i18n/locales/zh.json"
import { proposalStepResult } from "../src/lib/proposalDocument"
import {
  currentProposalResearchStep,
  mergeResearchAgentProgress,
  type ResearchAgentProgress,
  type ResearchProgressStep,
  type ResearchWorkflowStep,
  researchAgentProgressSchema,
  researchProgressStepKey,
  researchProgressSteps,
  researchWorkflowStepKey,
} from "../src/lib/researchAgentProgress"

const i18n = createInstance()
await i18n.init({
  lng: "zh",
  resources: { zh: { translation: zh }, en: { translation: en } },
  interpolation: { escapeValue: false },
})
function render(
  progress: unknown,
  stageStates: Record<string, ProposalStageState> = {},
  language = "zh",
  options: Partial<ComponentProps<typeof ResearchAgentProgressPanel>> = {},
) {
  return renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n: i18n.cloneInstance({ lng: language }) },
      createElement(ResearchAgentProgressPanel, {
        stages: ["rebuttal_baseline", "autorebuttal", "ac_summary"],
        stageStates,
        progress: progress ? researchAgentProgressSchema.parse(progress) : null,
        onOpenStage: () => {},
        ...options,
      }),
    ),
  )
}
const worker = (
  id = "a",
  status: ResearchProgressStep["status"] = "running",
): ResearchProgressStep => ({
  id,
  parentId: "main",
  stage: "autorebuttal",
  description: `Attempt ${id}`,
  status,
  toolCount: 1,
  currentTool: status === "running" ? "Read" : null,
  tools: [
    {
      id: `read-${id}`,
      name: "Read",
      done: status === "completed",
      failed: status === "failed",
    },
  ],
})
const snapshot = (
  agents: ResearchProgressStep[],
  patch: Partial<ResearchAgentProgress> = {},
): ResearchAgentProgress => ({
  sessionId: "session",
  status: "running",
  currentTool: "Read",
  agents,
  ...patch,
})
const publication = (
  patch: Partial<ProposalStageState> = {},
): ProposalStageState => ({
  status: "ready",
  run_id: "published-run",
  source_version_id: "source",
  updated_time: "2026-11-17T01:00:00Z",
  ...patch,
})
const logical = (
  patch: Partial<ResearchWorkflowStep> = {},
): ResearchWorkflowStep => ({
  id: "evidence-design",
  title: "Design a defensible evaluation",
  goal: "Determine which evidence would support the claim.",
  approach:
    "Compare hypotheses, controls and uncertainty before choosing a design.",
  summary: "A paired comparison isolates the proposed contribution.",
  stage: "methods",
  status: "running",
  outputPaths: [],
  ...patch,
})
const withSteps = (
  steps: ResearchWorkflowStep[],
  patch: Partial<ResearchAgentProgress> = {},
) =>
  snapshot([], {
    runId: "run-1",
    sequence: 4,
    workflowSequence: 1,
    workflowSteps: steps,
    ...patch,
  })

test("logical fields are optional for legacy frames and default to empty report content", () => {
  const legacy = researchAgentProgressSchema.parse(snapshot([worker()]))
  expect(legacy.workflowSteps).toBeUndefined()
  const parsed = researchAgentProgressSchema.parse(
    snapshot([], {
      workflowSteps: [
        { id: "logical", status: "waiting" } as ResearchWorkflowStep,
      ],
    }),
  )
  expect(parsed.workflowSteps?.[0]).toMatchObject({
    title: "",
    goal: "",
    approach: "",
    summary: "",
    stage: "",
    outputPaths: [],
  })
})

test("unstarted stages render no future steps, totals, or invented progress", () => {
  const html = render(null, { methods: publication({ status: "pending" }) })
  expect(html).toContain("准备就绪")
  expect(html).not.toContain("data-step-id")
  expect(html).not.toContain("已发布")
  expect(html).not.toContain("正在执行")
})

test("explicit research reasoning renders a readable Goal Approach Outcome definition list", () => {
  const html = render(withSteps([logical()]), {}, "en")
  expect(html).toContain("Design a defensible evaluation")
  expect(html).toContain("<dl")
  expect(html).toContain("<dt")
  expect(html).toContain("<dd")
  for (const text of [
    "Goal",
    "Approach &amp; design",
    "Outcome",
    "Determine which evidence",
    "Compare hypotheses",
    "A paired comparison",
  ])
    expect(html).toContain(text)
  expect(html).toContain("text-[15px] leading-7")
  expect(html).not.toContain("View deliverable")
})

test("named legacy workers use localized domain titles instead of attempt descriptions", () => {
  const html = render(snapshot([worker()]))
  expect(html).toContain(zh.paper.workflow.stage.autorebuttal)
  expect(html).toContain(zh.paper.agentProgress.guidance.autorebuttal.goal)
  expect(html).not.toContain("Attempt a")
})

test("raw coordinator and unknown tools never become research cards or right-panel labels", () => {
  const raw = snapshot([
    { ...worker("edit"), stage: "coordinator", description: "Edit" },
    { ...worker("sql"), stage: "unknown-tool", description: "Read" },
  ])
  const html = render(raw, {}, "en")
  expect(researchProgressSteps(raw, {})).toEqual([])
  for (const text of [
    "data-step-id",
    "Read",
    "Edit",
    "tool calls",
    "Research coordinator",
    "Reading materials",
  ])
    expect(html).not.toContain(text)
})

test("legacy workers and publications collapse repeated attempts into one stage card", () => {
  const raw = snapshot([worker("old", "completed"), worker("new")])
  const steps = researchProgressSteps(raw, { autorebuttal: publication() })
  expect(steps).toHaveLength(1)
  expect(steps[0]).toMatchObject({
    id: "stage:autorebuttal",
    status: "running",
  })
  expect(render(raw).match(/data-step-id=/g)).toHaveLength(1)
})

test("two explicit logical goals sharing an adapter stage survive and suppress its legacy fallback", () => {
  const progress = withSteps(
    [
      logical({
        id: "controls",
        title: "Choose valid controls",
        goal: "Separate the causal factors.",
        status: "completed",
      }),
      logical({
        id: "uncertainty",
        title: "Design uncertainty analysis",
        goal: "Quantify the uncertainty.",
      }),
    ],
    { agents: [{ ...worker(), stage: "methods" }] },
  )
  const steps = researchProgressSteps(progress, { methods: publication() })
  expect(steps.map((step) => step.id)).toEqual([
    "step:controls",
    "step:uncertainty",
  ])
  expect(steps.map((step) => step.goal)).toEqual([
    "Separate the causal factors.",
    "Quantify the uncertainty.",
  ])
  const html = render(progress)
  expect(html.match(/data-step-id=/g)).toHaveLength(2)
  expect(html).toContain("Choose valid controls")
  expect(html).toContain("Design uncertainty analysis")
})

test("the same explicit ID is stable across runs while distinct logical goals remain", () => {
  const previous = withSteps([
    logical({ id: "controls", status: "completed" }),
    logical({ id: "uncertainty", status: "completed" }),
  ])
  const merged = mergeResearchAgentProgress(
    previous,
    withSteps([logical({ id: "controls", goal: "Reconsider the controls." })], {
      runId: "run-2",
      sequence: 1,
      workflowSequence: 1,
    }),
  )
  expect(merged.workflowSteps).toHaveLength(2)
  expect(
    merged.workflowSteps?.find((step) => step.id === "controls"),
  ).toMatchObject({
    goal: "Reconsider the controls.",
    runId: "run-2",
    status: "running",
  })
  expect(researchWorkflowStepKey(logical({ runId: "run-1" }))).toBe(
    researchWorkflowStepKey(logical({ runId: "run-2" })),
  )
})

test("the current research step is expanded and completed reasoning stays collapsed", () => {
  const html = render(
    withSteps([
      logical({ id: "complete", status: "completed" }),
      logical({ id: "current" }),
    ]),
  )
  expect(html.match(/aria-expanded="false"/g)).toHaveLength(1)
  expect(html.match(/aria-expanded="true"/g)).toHaveLength(1)
  expect(html).toContain('data-step-id="step:complete"')
  expect(html).toContain('data-step-id="step:current"')
  const completed = render(
    withSteps([logical({ status: "completed" })], { status: "completed" }),
  )
  expect(completed).not.toContain('aria-expanded="true"')
})

test("result actions require an actual published stage and a completed logical step", () => {
  const completed = withSteps([
    logical({ status: "completed", outputPaths: ["sections/methods.typ"] }),
  ])
  expect(render(completed, {}, "en")).not.toContain("View deliverable")
  expect(
    render(completed, { methods: publication({ status: "pending" }) }, "en"),
  ).not.toContain("View deliverable")
  expect(render(completed, { methods: publication() }, "en")).toContain(
    "View deliverable",
  )
  expect(
    render(withSteps([logical()]), { methods: publication() }, "en"),
  ).not.toContain("View deliverable")
})

test("proposal resumes the latest interrupted step with its original identity", () => {
  const progress = withSteps(
    [
      logical({
        id: "author-choice",
        status: "waiting",
        updatedAt: "2026-11-18T08:00:00Z",
      }),
      logical({
        id: "thermal-evidence",
        title: "Verify thermal identifiability",
        status: "interrupted",
        updatedAt: "2026-11-18T08:10:00Z",
      }),
    ],
    { status: "idle" },
  )
  const steps = researchProgressSteps(progress, {}, true)
  expect(steps[0].status).toBe("waiting")
  expect(currentProposalResearchStep(steps)?.id).toBe("step:thermal-evidence")
  const html = render(progress, {}, "zh", { onResumeStep: () => {} })
  expect(html).toContain("继续此步骤")
  expect(html).toContain("Verify thermal identifiability")
  expect(html.match(/aria-expanded="true"/g)).toHaveLength(1)
})

test("proposal results open saved chapters and do not expose internal or empty files", () => {
  const draft = {
    id: "S2",
    title: "Related work",
    path: "2-国内外研究现状.md",
    has_content: true,
  }
  const bibliography = { ...draft, id: "S4", path: "4-参考文献.md" }
  const internalPaths = [
    "proposal-state.json",
    "proposal-log.md",
    "styles/nsfc-proposal.typ",
  ]
  const completed = logical({
    status: "completed",
    stage: "literature",
    outputPaths: [...internalPaths, draft.path],
  })
  expect(
    proposalStepResult(
      completed,
      [...internalPaths, draft.path],
      [draft],
      [],
      false,
    ),
  ).toEqual({
    view: "document",
    path: draft.path,
  })
  expect(
    proposalStepResult(completed, internalPaths, [draft], [], false),
  ).toBeNull()
  expect(
    proposalStepResult(
      completed,
      [draft.path],
      [{ ...draft, has_content: false }],
      [],
      false,
    ),
  ).toBeNull()
  expect(
    proposalStepResult(
      { ...completed, outputPaths: [bibliography.path] },
      [bibliography.path],
      [bibliography],
      [],
      false,
    ),
  ).toEqual({
    view: "source",
    path: bibliography.path,
  })
  const finalReview = logical({ status: "completed", stage: "final_review" })
  expect(proposalStepResult(finalReview, [], [], [], false)).toBeNull()
  expect(proposalStepResult(finalReview, [], [], [], true)).toEqual({
    view: "layout",
    path: null,
  })
  const options = { onResumeStep: () => {}, getResultAction: () => null }
  const template = render(null, { formatting: publication() }, "zh", options)
  expect(template).not.toContain("查看成果")
})

test("published legacy artifacts restore only known research stages without future placeholders", () => {
  const states = {
    autorebuttal: publication({ status: "needs_revision" }),
    methods: publication({ status: "pending" }),
    coordinator: publication(),
    unknown: publication(),
  }
  const steps = researchProgressSteps(null, states)
  expect(steps.map((step) => step.stage)).toEqual(["autorebuttal"])
  expect(steps[0].status).toBe("completed")
})

test("an old published result does not overwrite a new legacy worker's failure", () => {
  const steps = researchProgressSteps(snapshot([worker("retry", "failed")]), {
    autorebuttal: publication(),
  })
  expect(steps).toHaveLength(1)
  expect(steps[0].status).toBe("failed")
})

test("a newer workflow sequence updates reasoning even when the raw sequence is unchanged", () => {
  const previous = withSteps([logical({ summary: "Initial design" })])
  const incoming = withSteps(
    [logical({ summary: "Revised design", status: "completed" })],
    { workflowSequence: 2 },
  )
  const merged = mergeResearchAgentProgress(previous, incoming)
  expect(merged.sequence).toBe(4)
  expect(merged.workflowSequence).toBe(2)
  expect(merged.workflowSteps?.[0]).toMatchObject({
    summary: "Revised design",
    status: "completed",
  })
})

test("raw event advancement cannot replace newer logical reports with older workflow data", () => {
  const previous = withSteps([logical({ summary: "Latest research design" })], {
    workflowSequence: 3,
    agents: [worker()],
  })
  const merged = mergeResearchAgentProgress(
    previous,
    withSteps([logical({ summary: "Outdated design" })], {
      sequence: 5,
      workflowSequence: 2,
      agents: [worker("new")],
    }),
  )
  expect(merged.sequence).toBe(5)
  expect(merged.agents.map((step) => step.id)).toEqual(["a", "new"])
  expect(merged.workflowSequence).toBe(3)
  expect(merged.workflowSteps?.[0].summary).toBe("Latest research design")
})

test("raw iframe frames and empty workflow replays cannot erase existing research reasoning", () => {
  const previous = withSteps([logical()], { workflowSequence: 7 })
  const raw = mergeResearchAgentProgress(
    previous,
    snapshot([worker()], { runId: "run-1", sequence: 5 }),
  )
  expect(raw.workflowSteps).toEqual(previous.workflowSteps)
  expect(raw.workflowSequence).toBe(7)
  const empty = mergeResearchAgentProgress(
    raw,
    withSteps([], { sequence: 6, workflowSequence: 8 }),
  )
  expect(empty.workflowSteps?.map((step) => step.id)).toEqual([
    "evidence-design",
  ])
  const nextRun = mergeResearchAgentProgress(
    empty,
    snapshot([], { runId: "run-2", sequence: 1 }),
  )
  expect(nextRun.workflowSteps).toEqual(empty.workflowSteps)
  expect(nextRun.workflowSequence).toBeUndefined()
})

test("a first logical snapshot can attach to already observed raw progress", () => {
  const previous = snapshot([worker()], { runId: "run-1", sequence: 10 })
  const merged = mergeResearchAgentProgress(
    previous,
    withSteps([logical()], { sequence: 9, workflowSequence: 1 }),
  )
  expect(merged.agents).toEqual(previous.agents)
  expect(merged.sequence).toBe(10)
  expect(merged.workflowSteps?.[0].goal).toBe(logical().goal)
})

test.each([
  "failed",
  "interrupted",
] as const)("overall %s marks the active current logical step for display only", (status) => {
  const step = logical({ runId: "run-1" })
  const progress = withSteps([step], { status })
  expect(researchProgressSteps(progress, {})[0].status).toBe(status)
  expect(progress.workflowSteps?.[0].status).toBe("running")
  expect(render(progress)).toContain(`data-step-status="${status}"`)
  const old = withSteps([logical({ runId: "old-run" })], { status })
  expect(researchProgressSteps(old, {})[0].status).toBe("running")
})

test("tool completion and raw completed status do not invent logical completion", () => {
  const progress = withSteps([logical()], {
    status: "completed",
    agents: [worker("done", "completed")],
  })
  expect(
    researchProgressSteps(progress, {}).find(
      (step) => step.id === "step:evidence-design",
    )?.status,
  ).toBe("running")
})

test("blank logical reports use bilingual stage-specific research guidance without invented outcomes", () => {
  const progress = withSteps([
    logical({ title: "", goal: "", approach: "", summary: "" }),
  ])
  for (const [language, locale] of [
    ["en", en],
    ["zh", zh],
  ] as const) {
    const html = render(progress, {}, language)
    expect(html).toContain(
      locale.paper.workflow.stage.methods.replace(/&/g, "&amp;"),
    )
    expect(html).toContain(locale.paper.agentProgress.guidance.methods.goal)
    expect(html).toContain(locale.paper.agentProgress.guidance.methods.approach)
    expect(html).toContain(locale.paper.agentProgress.outcomePending)
  }
})

test("explicit model-authored titles are preserved for novel logical research steps", () => {
  const html = render(
    withSteps([
      logical({
        stage: "study-design",
        title: "Disentangle the two research hypotheses",
      }),
    ]),
    {},
    "en",
  )
  expect(html).toContain("Disentangle the two research hypotheses")
  expect(html).not.toContain("paper.workflow.stage.study-design")
})

test("an older step revision cannot overwrite its latest reasoning", () => {
  const previous = withSteps([
    logical({ revision: 3, summary: "Current reasoning" }),
  ])
  const merged = mergeResearchAgentProgress(
    previous,
    withSteps([logical({ revision: 2, summary: "Old reasoning" })], {
      workflowSequence: 2,
    }),
  )
  expect(merged.workflowSteps?.[0].summary).toBe("Current reasoning")
})

// Raw event-history regression coverage remains independent of logical presentation.
test("snapshot merging retains history and ignores out-of-order durable replay", () => {
  const previous = snapshot([worker("one", "completed")], {
    runId: "run-1",
    sequence: 3,
  })
  const stale = snapshot([worker("one")], { runId: "run-1", sequence: 2 })
  expect(mergeResearchAgentProgress(previous, stale)).toBe(previous)
  const merged = mergeResearchAgentProgress(
    previous,
    snapshot([worker("two")], { runId: "run-1", sequence: 4 }),
  )
  expect(merged.agents.map((step) => step.id)).toEqual(["one", "two"])
  expect(merged.agents[0].status).toBe("completed")
})

test("reconnect and truncated snapshots cannot revive or erase completed tools", () => {
  const previous = snapshot([worker("one", "completed"), worker("two")])
  const merged = mergeResearchAgentProgress(previous, snapshot([worker("one")]))
  expect(merged.agents).toHaveLength(2)
  expect(merged.agents[0].status).toBe("completed")
  expect(merged.agents[0].tools[0].done).toBe(true)
})

test("identical tool-call IDs in distinct coordinator runs remain distinct", () => {
  const previous = snapshot([worker()], { runId: "run-1" })
  const merged = mergeResearchAgentProgress(
    previous,
    snapshot([worker()], { runId: "run-2" }),
  )
  expect(merged.agents).toHaveLength(2)
  expect(new Set(merged.agents.map(researchProgressStepKey)).size).toBe(2)
})

test.each([
  "waiting",
  "failed",
  "interrupted",
  "completed",
] as const)("schema and panel preserve actual %s events", (status) => {
  const progress = researchAgentProgressSchema.parse(
    snapshot([worker("a", status)], { status }),
  )
  expect(render(progress)).toContain(`data-step-status="${status}"`)
})

test("the host rejects malformed statuses and invalid logical sequences", () => {
  expect(
    researchAgentProgressSchema.safeParse({
      sessionId: "s",
      status: "complete",
      agents: [],
    }).success,
  ).toBe(false)
  expect(
    researchAgentProgressSchema.safeParse(
      withSteps([logical()], { workflowSequence: -1 }),
    ).success,
  ).toBe(false)
})

test("a queued publication identity does not imply work or a published artifact", () => {
  expect(
    researchProgressSteps(null, {
      methods: { status: "pending", run_id: "queued-run" },
    }),
  ).toEqual([])
})

test.each([
  "ready",
  "idle",
  "completed",
] as const)("active logical reasoning overrides a lagging raw %s header without completing work", (rawStatus) => {
  for (const status of ["running", "waiting"] as const) {
    const html = render(
      withSteps([logical({ status })], { status: rawStatus }),
      {},
      "en",
    )
    const header = html.match(/<header[\s\S]*?<\/header>/)?.[0]
    expect(header).toContain(en.paper.agentProgress.status[status])
    expect(html).toContain(`data-step-status="${status}"`)
  }
})

test.each([
  "failed",
  "interrupted",
] as const)("overall %s keeps priority over the logical header", (status) => {
  const html = render(withSteps([logical()], { status }), {}, "en")
  expect(html.match(/<header[\s\S]*?<\/header>/)?.[0]).toContain(
    en.paper.agentProgress.status[status],
  )
})
