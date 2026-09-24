export type RebuttalBaselineConcern = {
  id: string
  reviewerId: string
  label: "W" | "Q" | "M"
  concern: string
  concernType: string
  severity: "low" | "medium" | "high"
  answerSource: string
  draftMove: string
  sourceRefs: string[]
}

export type RebuttalBaselineReviewerSource = {
  reviewId: string
  reviewerId: string
  displayLabel: string
  title: string | null
  sourceSystem: string
  externalId: string | null
  sourceUrl: string | null
  reviewMarkdown: string | null
}

export type RebuttalBaselineReviewerCard = {
  reviewerId: string
  displayLabel: string
  sentiment: string
  movability: string
  attitude: string
  primaryConcerns: string[]
  concernIds: string[]
}

export type RebuttalBaselineView = {
  summary: string
  paperSummary: string
  readyForGeneration: boolean
  findings: string[]
  openQuestions: string[]
  constraints: {
    venue: string | null
    venueYear: number | null
    responseMode: string
    outputFormat: string
  }
  reviewerSources: RebuttalBaselineReviewerSource[]
  concerns: RebuttalBaselineConcern[]
  reviewerCards: RebuttalBaselineReviewerCard[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(asString).filter((item) => item.length > 0)
    : []
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(asRecord).filter((item) => item !== null)
    : []
}

function parseReviewerSource(
  value: Record<string, unknown>,
): RebuttalBaselineReviewerSource | null {
  const reviewId = asString(value.review_id)
  const reviewerId = asString(value.reviewer_id)
  if (!reviewId || !reviewerId) return null

  return {
    reviewId,
    reviewerId,
    displayLabel: asString(value.display_label) || reviewerId,
    title: asString(value.title) || null,
    sourceSystem: asString(value.source_system) || "manual",
    externalId: asString(value.external_id) || null,
    sourceUrl: asString(value.source_url) || null,
    reviewMarkdown: asString(value.review_markdown) || null,
  }
}

function parseConcern(
  value: Record<string, unknown>,
): RebuttalBaselineConcern | null {
  const id = asString(value.id)
  const reviewerId = asString(value.reviewer_id)
  const concern = asString(value.concern)
  const label = asString(value.label)
  const severity = asString(value.severity)
  if (
    !id ||
    !reviewerId ||
    !concern ||
    !["W", "Q", "M"].includes(label) ||
    !["low", "medium", "high"].includes(severity)
  ) {
    return null
  }

  return {
    id,
    reviewerId,
    label: label as RebuttalBaselineConcern["label"],
    concern,
    concernType: asString(value.concern_type),
    severity: severity as RebuttalBaselineConcern["severity"],
    answerSource: asString(value.answer_source),
    draftMove: asString(value.draft_move),
    sourceRefs: asStringArray(value.source_refs),
  }
}

/** Convert the persisted, extensible rebuttal context into a stable UI model. */
export function parseRebuttalBaseline(
  context: Record<string, unknown> | null | undefined,
): RebuttalBaselineView | null {
  const baseline = asRecord(context?.baseline)
  const intake = asRecord(context?.intake)
  const analysis = asRecord(context?.analysis)
  if (!baseline || !intake || !analysis) return null

  const summary = asString(baseline.summary)
  const paperSummary = asString(intake.paper_summary)
  const reviewerSources = asRecordArray(intake.reviewer_sources)
    .map(parseReviewerSource)
    .filter((item) => item !== null)
  const concerns = asRecordArray(analysis.concerns)
    .map(parseConcern)
    .filter((item) => item !== null)
  if (!summary && !paperSummary && concerns.length === 0) return null

  const labelsByReviewer = new Map(
    reviewerSources.map((source) => [source.reviewerId, source.displayLabel]),
  )
  const reviewerCards = asRecordArray(analysis.reviewer_cards)
    .map((value): RebuttalBaselineReviewerCard | null => {
      const reviewerId = asString(value.reviewer_id)
      if (!reviewerId) return null
      return {
        reviewerId,
        displayLabel: labelsByReviewer.get(reviewerId) ?? reviewerId,
        sentiment: asString(value.sentiment),
        movability: asString(value.movability),
        attitude: asString(value.attitude),
        primaryConcerns: asStringArray(value.primary_concerns),
        concernIds: asStringArray(value.concern_ids),
      }
    })
    .filter((item) => item !== null)
  const constraints = asRecord(intake.constraints)
  const venueYear = constraints?.venue_year

  return {
    summary,
    paperSummary,
    readyForGeneration: baseline.ready_for_generation === true,
    findings: asStringArray(baseline.findings),
    openQuestions: asStringArray(intake.open_questions),
    constraints: {
      venue: asString(constraints?.venue) || null,
      venueYear: typeof venueYear === "number" ? venueYear : null,
      responseMode: asString(constraints?.response_mode),
      outputFormat: asString(constraints?.output_format),
    },
    reviewerSources,
    concerns,
    reviewerCards,
  }
}
