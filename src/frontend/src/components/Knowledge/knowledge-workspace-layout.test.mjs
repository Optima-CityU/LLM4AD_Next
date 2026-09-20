import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"

const workspace = readFileSync(
  new URL("./KnowledgeWorkspace.tsx", import.meta.url),
  "utf8",
)
const route = readFileSync(
  new URL("../../routes/_layout/knowledge.tsx", import.meta.url),
  "utf8",
)
const apiProxy = readFileSync(
  new URL("../../../nginx-api-proxy.conf", import.meta.url),
  "utf8",
)
const zh = JSON.parse(
  readFileSync(new URL("../../i18n/locales/zh.json", import.meta.url), "utf8"),
)
const en = JSON.parse(
  readFileSync(new URL("../../i18n/locales/en.json", import.meta.url), "utf8"),
)

test("keeps the knowledge workspace inside the available viewport", () => {
  assert.match(
    route,
    /className="flex h-full min-h-0 flex-col gap-4 overflow-hidden"/,
  )
  assert.doesNotMatch(workspace, /h-\[calc\(100vh-9rem\)\]/)
  assert.doesNotMatch(workspace, /min-h-\[620px\]/)
  assert.match(
    workspace,
    /className="flex min-h-0 flex-1 overflow-hidden rounded-xl border/,
  )
})

test("exposes parser model settings from the page header", () => {
  assert.match(workspace, /knowledge\.binding\.title/)
  assert.match(workspace, /setBindingDialogOpen\(true\)/)
  assert.match(workspace, /open=\{bindingDialogOpen\}/)
  assert.doesNotMatch(workspace, /knowledge\.binding\.notConfigured/)
  assert.doesNotMatch(workspace, /max-w-\[320px\]/)
})

test("does not allow parsing without a configured parser model", () => {
  assert.match(workspace, /!binding\?\.configured/)
  assert.match(
    workspace,
    /if \(!detail \|\| parseBusy \|\| !binding\?\.configured/,
  )
})

test("disables document upload entry points until a parser model is configured", () => {
  assert.match(
    workspace,
    /type="file"[\s\S]*disabled=\{!binding\?\.configured\}/,
  )
  assert.match(
    workspace,
    /uploading \|\|\s*!binding\?\.configured \|\|\s*detail\.source_file_count/,
  )
  assert.match(
    workspace,
    /if \(!binding\?\.configured \|\| !detail \|\| !validateFiles/,
  )
})

test("creates an empty topic before files are uploaded into it", () => {
  assert.match(
    workspace,
    /JSON\.stringify\(\{ title: uploadTitle\.trim\(\) \}\)/,
  )
  assert.doesNotMatch(
    workspace,
    /for \(const file of uploadFiles\) data\.append\("files", file\)/,
  )
  assert.doesNotMatch(workspace, /uploadFiles\.length === 0/)
})

test("shows source and parsed documents as separate topic overview sections", () => {
  assert.match(workspace, /knowledge\.topicOverview\.sourceTitle/)
  assert.match(workspace, /knowledge\.topicOverview\.parsedTitle/)
  assert.match(workspace, /setSelectedNode\(null\)/)
  assert.doesNotMatch(workspace, /grid-cols-\[260px_minmax\(0,1fr\)_320px\]/)
})

test("opens parsed documents in an editable view and explains when edits are replaced", () => {
  assert.match(workspace, /const handleSelectDocument = \(documentId: string\)/)
  assert.match(
    workspace,
    /setSelectedNode\(\{ kind: "document", id: documentId \}\)[\s\S]*setViewMode\("split"\)/,
  )
  assert.match(workspace, /handleSelectDocument\(document\.id\)/)
  assert.match(workspace, /knowledge\.editPersistenceHint/)
})

test("reconciles terminal parse state so a failed run can be retried", () => {
  assert.match(workspace, /await loadParseRun\(run\.id\)/)
  assert.match(
    workspace,
    /const activeParseStatus = parseRun\?\.status \?\? detail\?\.parse_status/,
  )
  assert.match(workspace, /detail\.parse_status === "failed"/)
})

test("disables knowledge deletion while parsing", () => {
  const disabledDeleteButtons = workspace.match(/disabled=\{parseBusy\}/g) || []
  assert.ok(disabledDeleteButtons.length >= 2)
  assert.match(workspace, /if \(!detail \|\| parseBusy\) return/)
})

test("uses the main workspace for readable live parsing progress", () => {
  assert.match(workspace, /const \[parseEvents, setParseEvents\]/)
  assert.match(workspace, /knowledge\.parseWorkspace\.title/)
  assert.match(workspace, /knowledge\.parseWorkspace\.activity/)
  assert.match(workspace, /parseActivityGroups\.map/)
  assert.match(workspace, /parseBusy \? \(/)
  assert.match(workspace, /parseActivityFromEvent/)
  assert.match(workspace, /groupParseActivities/)
  assert.match(workspace, /group\.steps\.map/)
  assert.match(workspace, /<ol className="relative/)
  assert.match(workspace, /knowledge\.progressStages\.\$\{group\.stage\}/)
  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.equal(typeof knowledgeCopy.parseWorkspace.steps.model, "string")
    assert.equal(typeof knowledgeCopy.parseWorkspace.steps.retry, "string")
    assert.equal(typeof knowledgeCopy.parseWorkspace.stepStatus.running, "string")
  }
})

test("keeps knowledge UI copy vendor neutral", () => {
  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.doesNotMatch(
      JSON.stringify(knowledgeCopy),
      /claude|agent sdk|writing-plans/i,
    )
    assert.equal(typeof knowledgeCopy.progressStages.generating, "string")
    assert.equal(typeof knowledgeCopy.progressStages.ready, "string")
  }
})

test("opens a full parse preparation workspace before dispatch", () => {
  assert.match(workspace, /const \[parsePreparing, setParsePreparing\]/)
  assert.match(workspace, /\) : parsePreparing \? \(/)
  assert.match(workspace, /knowledge\.parseSetup\.title/)
  assert.match(workspace, /onClick=\{\(\) => setParsePreparing\(true\)\}/)
  assert.match(workspace, /onClick=\{\(\) => void handleParse\(\)\}/)
})

test("collects optional per-run background context in the preparation workspace", () => {
  assert.match(workspace, /const \[parseBackground, setParseBackground\]/)
  assert.match(workspace, /background: parseBackground\.trim\(\) \|\| null/)
  assert.match(workspace, /maxLength=\{8000\}/)
  assert.match(workspace, /knowledge\.background\.securityHint/)
  assert.doesNotMatch(
    workspace,
    /knowledge\.parserDescription[\s\S]*knowledge-parse-background[\s\S]*setParsePreparing\(true\)/,
  )
})

test("offers direct and planned parsing with visible output document counts", () => {
  assert.match(workspace, /const \[parseMode, setParseMode\]/)
  assert.match(workspace, /"direct" \| "planned"/)
  assert.match(workspace, /\/parse-plans/)
  assert.match(workspace, /strategy\.document_count/)
  assert.match(workspace, /selectedPlanStrategy\.documents\.map/)
  assert.match(workspace, /selectedStrategyId/)
  assert.match(workspace, /plan_id: parsePlan\.id/)
  assert.match(workspace, /strategy_id: selectedStrategyId/)
})

test("visually distinguishes planned parsing from direct parsing", () => {
  assert.match(workspace, /role="radiogroup"/)
  assert.match(workspace, /aria-checked=\{parseMode === "planned"\}/)
  assert.match(workspace, /knowledge\.parseSetup\.plannedDescription/)
  assert.match(workspace, /knowledge\.parseSetup\.plannedFlow/)
  assert.match(workspace, /aria-checked=\{parseMode === "direct"\}/)
  assert.match(workspace, /knowledge\.parseSetup\.directDescription/)
  assert.match(workspace, /knowledge\.parseSetup\.directFlow/)
  assert.match(workspace, /border-amber-500/)
})

test("progressively reveals compact plan details", () => {
  assert.match(workspace, /const selectedPlanStrategy =/)
  assert.match(workspace, /selectedPlanStrategy\.documents\.map/)
  assert.match(workspace, /knowledge\.plan\.showSourceDetails/)
  assert.match(workspace, /knowledge\.plan\.showDocumentDetails/)
  assert.match(workspace, /knowledge\.plan\.selectedStructure/)
  assert.match(workspace, /<details/)
  assert.match(workspace, /line-clamp-2/)
})

test("collapses the topic library into a top topic switcher", () => {
  assert.match(workspace, /const \[libraryCollapsed, setLibraryCollapsed\]/)
  assert.match(workspace, /setLibraryCollapsed\(\(current\) => !current\)/)
  assert.match(workspace, /knowledge\.workspace\.collapseLibrary/)
  assert.match(workspace, /knowledge\.workspace\.expandLibrary/)
  assert.match(workspace, /libraryCollapsed && \(/)
  assert.match(workspace, /onValueChange=\{setSelectedSourceId\}/)
  assert.match(workspace, /knowledge\.workspace\.topicSwitcher/)

  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.equal(typeof knowledgeCopy.workspace.collapseLibrary, "string")
    assert.equal(typeof knowledgeCopy.workspace.expandLibrary, "string")
    assert.equal(typeof knowledgeCopy.workspace.topicSwitcher, "string")
  }
})

test("animates the topic library and keeps a visible edge toggle", () => {
  assert.match(workspace, /transition-\[width\] duration-300 ease-in-out/)
  assert.match(workspace, /width: libraryCollapsed \? 0 : 260/)
  assert.match(workspace, /libraryCollapsed \? \{ inert: true \} : \{\}/)
  assert.match(workspace, /w-6[\s\S]*knowledge\.workspace\.collapseLibrary/)
  assert.doesNotMatch(workspace, /!libraryCollapsed && \(/)
})

test("persists the topic library collapse preference across refreshes", () => {
  assert.match(workspace, /knowledgeLibraryCollapsedKey/)
  assert.match(workspace, /localStorage\.getItem\(knowledgeLibraryCollapsedKey\)/)
  assert.match(
    workspace,
    /localStorage\.setItem\([\s\S]*knowledgeLibraryCollapsedKey/,
  )
})

test("moves the parse progress bar without pulsing", () => {
  assert.match(workspace, /transition-\[width\] duration-500 ease-out/)
  assert.doesNotMatch(
    workspace,
    /className="[^"]*animate-pulse[^"]*"[\s\S]{0,120}activeJob\?\.progress/,
  )
})

test("gives parsing content the main vertical scrolling surface", () => {
  assert.doesNotMatch(
    workspace,
    /grid min-h-0 flex-1 grid-rows-\[auto_minmax\(0,1fr\)\]/,
  )
  assert.doesNotMatch(workspace, /flex min-h-24 items-start gap-3/)
  assert.match(workspace, /knowledge\.parseSetup\.compactSummary/)
  assert.match(workspace, /knowledge\.parseWorkspace\.compactSummary/)
  assert.match(workspace, /className="min-h-0 flex-1 overflow-y-auto"/)
})

test("restores visible progress after refresh and allows stopping active jobs", () => {
  assert.match(workspace, /function activityFromJob/)
  assert.match(workspace, /function loadLatestParseRun|const loadLatestParseRun/)
  assert.match(workspace, /\/sources\/\$\{sourceId\}\/parse-runs\/latest/)
  assert.match(workspace, /const handleStopParse = async/)
  assert.match(workspace, /\/parse-plans\/\$\{parsePlan\.id\}\/cancel/)
  assert.match(workspace, /\/parse-runs\/\$\{parseRun!?\.id\}\/cancel/)
  assert.match(workspace, /knowledge\.parseWorkspace\.stop/)

  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.equal(typeof knowledgeCopy.parseWorkspace.stop, "string")
    assert.equal(typeof knowledgeCopy.parseWorkspace.stopped, "string")
    assert.equal(typeof knowledgeCopy.parseWorkspace.stopFailed, "string")
  }
})

test("continues a failed plan from its saved checkpoint", () => {
  assert.match(workspace, /const handleRetryPlan = async/)
  assert.match(workspace, /\/parse-plans\/\$\{parsePlan\.id\}\/retry/)
  assert.match(workspace, /parsePlan\.retryable/)
  assert.match(workspace, /knowledge\.plan\.retryPersist/)
  assert.match(workspace, /knowledge\.plan\.retryPersistHint/)

  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.equal(typeof knowledgeCopy.plan.retryPersist, "string")
    assert.equal(typeof knowledgeCopy.plan.retryPersistHint, "string")
  }
})

test("offers fast and collaborative planning with native clarification questions", () => {
  assert.match(workspace, /const \[planInteractionMode, setPlanInteractionMode\]/)
  assert.match(workspace, /"quick" \| "collaborative"/)
  assert.match(workspace, /interaction_mode: planInteractionMode/)
  assert.match(workspace, /`knowledge\.plan\.\$\{mode\}Mode`/)
  assert.match(workspace, /`knowledge\.plan\.\$\{mode\}Description`/)
  assert.match(workspace, /parsePlan\?\.pending_question/)
  assert.match(workspace, /const handleAnswerPlanQuestion = async/)
  assert.match(workspace, /\/parse-plans\/\$\{parsePlan\.id\}\/answer/)
  assert.match(workspace, /knowledge\.plan\.submitAnswer/)

  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.equal(typeof knowledgeCopy.plan.quickMode, "string")
    assert.equal(typeof knowledgeCopy.plan.collaborativeMode, "string")
    assert.equal(typeof knowledgeCopy.plan.submitAnswer, "string")
    assert.equal(typeof knowledgeCopy.progressStages.waiting_user, "string")
  }
})

test("does not expose a transient browser stream error while the job is still active", () => {
  assert.match(workspace, /function isActiveParseStatus/)
  assert.match(workspace, /knowledge\.parseWorkspace\.streamInterrupted/)
  assert.doesNotMatch(workspace, /reconciledPlan\?\.error_code[\s\S]{0,260}error\.message/)
})

test("reconnects interrupted progress streams without leaving a persistent warning", () => {
  assert.match(workspace, /function followProgressStream/)
  assert.match(workspace, /last_id=\$\{encodeURIComponent\(lastEventId\)\}/)
  assert.match(workspace, /const progressStreamReconnectDelays = \[1000, 2000, 4000\]/)
  assert.match(workspace, /duration: 5000/)
  assert.match(workspace, /toast\.dismiss\(progressStreamToastId\)/)
})

test("uses an HTTP\/1.1 upstream for long-lived API streams", () => {
  assert.match(apiProxy, /location \/api[\s\S]*proxy_http_version 1\.1;/)
  assert.match(apiProxy, /location \/api[\s\S]*proxy_set_header Connection "";/)
})

test("replays persisted progress history after refresh", () => {
  assert.match(workspace, /const loadParseHistory = useCallback/)
  assert.match(workspace, /\/parse-plans\/\$\{jobId\}\/events/)
  assert.match(workspace, /\/parse-runs\/\$\{jobId\}\/events/)
  assert.match(workspace, /setParseEvents\(history\.reduce/)
  assert.match(workspace, /isActiveParseStatus\(latestPlan\?\.status\)/)
  assert.match(workspace, /loadParseHistory\("plan", latestPlan\.id\)/)
})

test("explains long quiet parser steps with elapsed and last-update status", () => {
  assert.match(workspace, /const \[progressClock, setProgressClock\]/)
  assert.match(workspace, /const progressQuietTooLong =/)
  assert.match(workspace, /knowledge\.parseWorkspace\.lastUpdate/)
  assert.match(workspace, /knowledge\.parseWorkspace\.quietHint/)
  assert.match(workspace, /knowledge\.parseWorkspace\.elapsed/)

  for (const knowledgeCopy of [zh.knowledge, en.knowledge]) {
    assert.equal(typeof knowledgeCopy.parseWorkspace.lastUpdate, "string")
    assert.equal(typeof knowledgeCopy.parseWorkspace.quietHint, "string")
    assert.equal(typeof knowledgeCopy.parseWorkspace.elapsed, "string")
  }
})
