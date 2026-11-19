import { Check, Copy, MessageSquare, Pencil, Send, X } from "lucide-react"
import { useId, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import type { PaperRegion } from "@/client"
import PaperRegionDiff from "@/components/Paper/PaperRegionDiff"
import PaperRegionEvidence from "@/components/Paper/PaperRegionEvidence"
import PaperRegionRequests from "@/components/Paper/PaperRegionRequests"
import { usePaperRegionWorkbench } from "@/components/Paper/PaperRegionWorkbench"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { PaperRegionError } from "@/hooks/usePaperRegions"
import { usePersistentState } from "@/hooks/usePersistentState"
import {
  type PaperRegionSelection,
  paperRegionSection,
  paperRegionSelection,
} from "@/lib/paperRegions"

export const regionMarkdownClass =
  "prose min-w-0 max-w-none break-words text-[15px] leading-7 dark:prose-invert [&_a]:break-all [&_p]:my-2 [&_li]:leading-7 [&_pre]:max-w-full [&_pre]:overflow-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto"

type InstructionDraft = {
  text: string
  baseRevision: PaperRegion["revision"]
  selection?: PaperRegionSelection
}

const systemFieldLabels: Record<string, string> = {
  summary: "summary",
  "paper summary": "paperSummary",
  concern: "concern",
  severity: "severity",
  "concern type": "concernType",
  "answer source": "answerSource",
  "draft move": "draftMove",
  sentiment: "sentiment",
  attitude: "attitude",
  movability: "movability",
  "primary concern": "primaryConcern",
  "author action": "authorAction",
  "open question": "openQuestion",
  "global response": "globalResponse",
  "baseline finding": "baselineFinding",
  "rebuttal finding": "rebuttalFinding",
  "message to chair": "chairMessage",
  "full rebuttal": "fullRebuttal",
  finding: "finding",
}

/** Edit one revision and submit contextual agent work without blocking on agent availability. */
export default function PaperRegionEditor({
  region,
  compact = false,
}: {
  region: PaperRegion
  compact?: boolean
}) {
  const { t } = useTranslation()
  const context = usePaperRegionWorkbench()
  const inputId = useId()
  const storageKey = `paper-region:${encodeURIComponent(context?.workspaceId ?? "unscoped")}:${encodeURIComponent(region.id)}`
  const [draft, setDraft] = usePersistentState<{
    region: PaperRegion
    content: string
  } | null>(`${storageKey}:markdown`, null)
  const [preview, setPreview] = useState(false)
  const [instructionDraft, setInstructionDraft] =
    usePersistentState<InstructionDraft>(`${storageKey}:instruction`, {
      text: "",
      baseRevision: region.revision,
    })
  const instruction = instructionDraft.text
  const selection = instructionDraft.selection
  const setInstruction = (text: string) =>
    setInstructionDraft((previous) => ({
      ...previous,
      text,
      baseRevision:
        previous.text || previous.selection
          ? previous.baseRevision
          : region.revision,
    }))
  const setSelection = (value: PaperRegionSelection | undefined) =>
    setInstructionDraft((previous) => ({
      ...previous,
      selection: value,
      baseRevision:
        previous.text || previous.selection
          ? previous.baseRevision
          : region.revision,
    }))
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)
  const [copied, setCopied] = useState(false)
  const [requestOpen, setRequestOpen] = useState(
    !compact || Boolean(instruction),
  )
  const [savedAttempt, setSavedAttempt] = usePersistentState<{
    payload: string
    key: string
  } | null>(`${storageKey}:attempt`, null)
  const attempt = useRef(savedAttempt)
  const submitting = useRef(false)
  if (!context) return null
  const { actions, onRequestAgent, onOpenFile } = context
  const dirty = Boolean(draft && draft.content !== draft.region.content)
  const conflict = Boolean(draft && draft.region.revision !== region.revision)
  const instructionConflict = Boolean(
    (instruction || selection) &&
      instructionDraft.baseRevision !== region.revision,
  )
  const requestBlocked =
    dirty || instructionConflict || Boolean(selection && conflict)
  const requests = (context.query.data?.requests ?? []).filter(
    (request) => request.region_id === region.id,
  )
  const section = paperRegionSection(region)
  let displayTitle = region.title
  const fieldTitle = region.title.toLowerCase().replace(/_/g, " ")
  const concernField =
    region.kind === "baseline_concern"
      ? region.title.match(/^(.*):\s*([^:]+)$/)
      : null
  const reviewerField =
    region.kind === "reviewer_analysis"
      ? fieldTitle.replace(/^reviewer analysis:\s*/, "")
      : null
  const stageField = /proposal_summary|proposal_finding/.test(region.kind)
    ? region.title.match(/^([a-z_]+):\s*(summary|finding)$/)
    : null
  if (
    concernField &&
    systemFieldLabels[concernField[2].toLowerCase().replace(/_/g, " ")]
  )
    displayTitle = `${concernField[1]}: ${t(`paper.regions.fields.${systemFieldLabels[concernField[2].toLowerCase().replace(/_/g, " ")]}`)}`
  else if (reviewerField && systemFieldLabels[reviewerField])
    displayTitle = t(`paper.regions.fields.${systemFieldLabels[reviewerField]}`)
  else if (stageField)
    displayTitle = `${t(`paper.workflow.stage.${stageField[1]}`, { defaultValue: stageField[1] })} · ${t(`paper.regions.fields.${stageField[2]}`)}`
  else if (
    /^(baseline_summary|baseline_finding|author_needed_issue|rebuttal_global|rebuttal_finding|chair_message|rebuttal_full)$/.test(
      region.kind,
    ) &&
    systemFieldLabels[fieldTitle]
  )
    displayTitle = t(`paper.regions.fields.${systemFieldLabels[fieldTitle]}`)
  const fail = (failure: unknown) =>
    setError(
      t(
        failure instanceof PaperRegionError && failure.status === 409
          ? "paper.regions.conflict"
          : "paper.regions.actionFailed",
      ),
    )
  const save = async () => {
    if (!draft || !dirty || conflict) return
    setError(null)
    try {
      await actions.save.mutateAsync(draft)
      setDraft(null)
      setPreview(false)
      setSelection(undefined)
    } catch (failure) {
      fail(failure)
    }
  }
  const submit = async () => {
    if (!instruction.trim() || submitting.current || requestBlocked) return
    submitting.current = true
    setError(null)
    setSent(false)
    const payload = JSON.stringify([
      region.id,
      region.revision,
      instruction,
      selection,
    ])
    if (attempt.current?.payload !== payload) {
      attempt.current = { payload, key: crypto.randomUUID() }
      setSavedAttempt(attempt.current)
    }
    try {
      const request = await actions.createRequest.mutateAsync({
        region,
        instruction: instruction.trim(),
        answer: section === "author" ? instruction : undefined,
        selection,
        idempotencyKey: attempt.current.key,
      })
      onRequestAgent?.({ id: request.id, prompt: request.prompt })
      setInstructionDraft({ text: "", baseRevision: region.revision })
      setSent(true)
      attempt.current = null
      setSavedAttempt(null)
      void actions.refresh()
    } catch (failure) {
      fail(failure)
    } finally {
      submitting.current = false
    }
  }
  return (
    <article
      className={
        compact
          ? "min-w-0 overflow-hidden border-t bg-background"
          : "min-w-0 overflow-hidden rounded-lg border bg-card"
      }
      data-region-id={region.id}
      data-testid="paper-region-editor"
    >
      <header
        className={`flex flex-wrap items-start justify-between gap-3 ${compact ? "px-4 pt-4" : "border-b bg-muted/15 px-5 py-4"}`}
      >
        <div className="min-w-0 flex-1">
          {!compact && (
            <p className="mb-1 text-sm text-muted-foreground">
              {t(`paper.regions.sections.${section}`)}
            </p>
          )}
          <h4
            className={`break-words font-serif font-semibold leading-7 ${compact ? "text-base" : "text-lg"}`}
          >
            {displayTitle}
          </h4>
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(region.content)
                setCopied(true)
              } catch {
                setError(t("paper.regions.copyFailed"))
              }
            }}
            aria-label={t("paper.regions.copy")}
          >
            <Copy className="size-4" />
            {copied && <Check className="size-4" />}
          </Button>
          {!draft && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setDraft({ region, content: region.content })
                setError(null)
                setSelection(undefined)
              }}
            >
              <Pencil className="size-4" />
              {t("paper.regions.edit")}
            </Button>
          )}
        </div>
      </header>
      <div
        className={`min-w-0 space-y-4 ${compact ? "px-4 py-4" : "px-5 py-5"}`}
      >
        {draft ? (
          <div className="space-y-3">
            <Label htmlFor={`${inputId}-markdown`}>
              {t("paper.regions.markdown")}
            </Label>
            <Textarea
              id={`${inputId}-markdown`}
              value={draft.content}
              className="min-h-48 resize-y font-mono text-[15px] leading-6 md:text-[15px]"
              onChange={(event) => {
                setDraft({ ...draft, content: event.target.value })
                setPreview(false)
                setSelection(undefined)
              }}
              onSelect={(event) => {
                if (!dirty)
                  setSelection(
                    paperRegionSelection(
                      draft.content,
                      event.currentTarget.selectionStart,
                      event.currentTarget.selectionEnd,
                    ),
                  )
              }}
            />
            <p className="text-sm leading-6 text-muted-foreground">
              {t("paper.regions.selectionHint")}
            </p>
            {conflict && (
              <p
                role="alert"
                className="text-sm leading-6 text-amber-800 dark:text-amber-200"
              >
                {t("paper.regions.conflict")}
              </p>
            )}
            {preview && (
              <PaperRegionDiff
                original={draft.region.content}
                replacement={draft.content}
              />
            )}
            <div className="flex flex-wrap gap-2">
              {preview ? (
                <Button
                  size="sm"
                  disabled={conflict || actions.save.isPending}
                  onClick={() => void save()}
                >
                  <Check className="size-4" />
                  {t("paper.regions.confirmEdit")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  disabled={!dirty || conflict}
                  onClick={() => setPreview(true)}
                >
                  {t("paper.regions.reviewEdit")}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft(null)
                  setPreview(false)
                  setSelection(undefined)
                  setError(null)
                }}
                disabled={actions.save.isPending}
              >
                <X className="size-4" />
                {t("paper.regions.discard")}
              </Button>
            </div>
          </div>
        ) : (
          <div className={regionMarkdownClass}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {region.content || t("paper.regions.empty")}
            </ReactMarkdown>
          </div>
        )}
        <PaperRegionEvidence region={region} onOpenFile={onOpenFile} />
        {!requestOpen && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRequestOpen(true)}
          >
            <MessageSquare className="size-4" />
            {t("paper.regions.askAgent")}
          </Button>
        )}
        {requestOpen && (
          <form
            className="space-y-2 border-t pt-4"
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label htmlFor={`${inputId}-request`}>
                {t(
                  section === "author"
                    ? "paper.regions.answerAuthor"
                    : "paper.regions.askAgent",
                )}
              </Label>
              {selection && (
                <Badge variant="outline">
                  {t("paper.regions.selectedText")}
                </Badge>
              )}
            </div>
            {selection && (
              <blockquote className="max-h-28 overflow-auto whitespace-pre-wrap border-l-2 pl-3 text-sm leading-6">
                {selection.original_text}
              </blockquote>
            )}
            {instructionConflict && (
              <div className="space-y-2">
                <p
                  role="alert"
                  className="text-sm leading-6 text-amber-800 dark:text-amber-200"
                >
                  {t("paper.regions.conflict")}
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setInstructionDraft({
                      text: instruction,
                      baseRevision: region.revision,
                    })
                    attempt.current = null
                    setSavedAttempt(null)
                  }}
                >
                  {t("paper.regions.useCurrent")}
                </Button>
              </div>
            )}
            <Textarea
              id={`${inputId}-request`}
              className="min-h-20 resize-y text-[15px] leading-6 md:text-[15px]"
              value={instruction}
              onChange={(event) => {
                setInstruction(event.target.value)
                setSent(false)
              }}
              placeholder={t("paper.regions.requestPlaceholder")}
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p aria-live="polite" className="text-sm text-muted-foreground">
                {dirty
                  ? t("paper.regions.saveFirst")
                  : sent
                    ? t("paper.regions.queued")
                    : t("paper.regions.queueHint")}
              </p>
              <Button
                type="submit"
                size="sm"
                disabled={
                  !instruction.trim() || requestBlocked || submitting.current
                }
              >
                <Send className="size-4" />
                {t("paper.regions.send")}
              </Button>
            </div>
          </form>
        )}
        {error && (
          <p role="alert" className="text-sm leading-6 text-destructive">
            {error}
          </p>
        )}
        <PaperRegionRequests region={region} requests={requests} />
      </div>
    </article>
  )
}
