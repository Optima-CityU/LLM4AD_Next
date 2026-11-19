import type { PaperRegion, PaperRegionRequest } from "@/client"

export type PaperRegionAgentRequest = { id: string; prompt: string }
export type PaperRegionSelection = {
  start: number
  end: number
  original_text: string
}
export type PaperRegionSection =
  | "review"
  | "analysis"
  | "reply"
  | "global"
  | "chair"
  | "author"
  | "proposal"

/** Classify server kinds without depending on generated region identifiers. */
export function paperRegionSection(
  region: Pick<PaperRegion, "kind">,
): PaperRegionSection {
  const kind = region.kind.toLowerCase()
  if (/chair|ac_summary/.test(kind)) return "chair"
  if (/author|placeholder|open_question|missing_input/.test(kind))
    return "author"
  if (/global/.test(kind)) return "global"
  if (/original_review|review_original|review_source|^review$/.test(kind))
    return "review"
  if (
    /baseline|analysis|concern|sentiment|movability|attitude|paper_summary|response_plan|draft_move|answer_source/.test(
      kind,
    )
  )
    return "analysis"
  if (/rebuttal|reply|response/.test(kind)) return "reply"
  return "proposal"
}

/** Match existing blocks conservatively; ambiguous content never picks a target. */
export function findPaperRegion(
  regions: PaperRegion[],
  target: { kind?: string; reviewId?: string; content?: string },
): PaperRegion | undefined {
  const candidates = regions.filter(
    (region) =>
      (!target.kind || region.kind === target.kind) &&
      (!target.reviewId || region.review_id === target.reviewId),
  )
  const exact =
    target.content === undefined
      ? candidates
      : candidates.filter((region) => region.content === target.content)
  return exact.length === 1 ? exact[0] : undefined
}

/** Show the smallest changed span while retaining nearby context. */
export function paperRegionDiff(original: string, replacement: string) {
  let start = 0
  while (
    start < original.length &&
    start < replacement.length &&
    original[start] === replacement[start]
  )
    start++
  let end = 0
  while (
    end < original.length - start &&
    end < replacement.length - start &&
    original[original.length - end - 1] ===
      replacement[replacement.length - end - 1]
  )
    end++
  return {
    prefix: original.slice(Math.max(0, start - 100), start),
    removed: original.slice(start, original.length - end),
    added: replacement.slice(start, replacement.length - end),
    suffix: original.slice(original.length - end, original.length - end + 100),
  }
}

/** Preserve browser UTF-16 offsets and the exact selected text for the API. */
export function paperRegionSelection(
  content: string,
  start: number,
  end: number,
): PaperRegionSelection | undefined {
  if (end <= start) return undefined
  return {
    start,
    end,
    original_text: content.slice(start, end),
  }
}

/** Restrict patch approval to a still-current region revision and text. */
export function canApplyPaperRegionRequest(
  region: PaperRegion,
  request: PaperRegionRequest,
) {
  return (
    request.region_id === region.id &&
    request.base_revision === region.revision &&
    request.replacement_text != null &&
    ["candidate", "proposed", "ready", "completed", "suggested"].includes(
      request.state,
    )
  )
}

export function isPendingPaperRegionRequest(request: PaperRegionRequest) {
  return ["queued", "pending", "running", "processing"].includes(request.state)
}

export type PaperRegionGroup = {
  key: string
  title: string
  primary: PaperRegion[]
  secondary: PaperRegion[]
  section: PaperRegionSection
}

/** Group visible prose with its editable metadata without inventing write targets. */
export function groupPaperRegions(regions: PaperRegion[]): PaperRegionGroup[] {
  const groups = new Map<string, PaperRegionGroup>()
  for (const region of regions) {
    const section = paperRegionSection(region)
    const concernField = /concern/.test(region.kind)
      ? region.title.match(
          /^(.*):\s*(concern|answer[_ ]source|draft[_ ]move|concern[_ ]type|severity)$/i,
        )
      : null
    const isTitle =
      region.content === region.title || /(?:^|[:/])title$/.test(region.id)
    const secondary =
      section === "review" ||
      /reviewer_analysis|rebuttal_finding/.test(region.kind) ||
      Boolean(concernField && concernField[2] !== "concern") ||
      isTitle
    const key = concernField
      ? `concern:${concernField[1]}`
      : section === "review"
        ? "original-reviews"
        : /reviewer_analysis|rebuttal_finding/.test(region.kind)
          ? "reviewer-context"
          : /baseline_summary/.test(region.kind)
            ? "baseline-summary"
            : `${region.kind}:${region.title}`
    const group = groups.get(key) ?? {
      key,
      title: concernField?.[1] ?? region.title,
      primary: [],
      secondary: [],
      section,
    }
    if (secondary || (key === "baseline-summary" && group.primary.length))
      group.secondary.push(region)
    else group.primary.push(region)
    groups.set(key, group)
  }
  return [...groups.values()]
}

export type PaperRegionView =
  | "baseline"
  | "reviews"
  | "author"
  | "chair"
  | "proposal"

/** Keep stage summaries and author questions in their own views. */
export function paperRegionVisible(
  region: PaperRegion,
  view: PaperRegionView,
  stage?: string,
) {
  const section = paperRegionSection(region)
  if (view === "chair") return section === "chair"
  if (view === "author") return section === "author"
  if (view === "baseline") return section === "review" || section === "analysis"
  if (view === "reviews")
    return (
      (["review", "reply", "global"].includes(section) &&
        !/rebuttal_full|rendered|full_rebuttal/.test(region.kind)) ||
      /concern|reviewer_analysis/.test(region.kind)
    )
  if (section !== "proposal") return false
  if (/proposal_context/.test(region.kind))
    return !stage || stage === "formatting"
  if (/proposal_summary|proposal_finding/.test(region.kind))
    return !stage || region.title.startsWith(`${stage}:`)
  return true
}

/** A reference is verified only when its status and inspectable evidence agree. */
export function paperRegionEvidenceStatus(ref: {
  status?: string | null
  quote?: string | null
  path?: string | null
  review_id?: string | null
  version?: string | number | null
}) {
  const inspectable = Boolean(
    (ref.quote && (ref.path || ref.review_id)) ||
      (ref.version && ref.path?.includes("/region-review-sources/")),
  )
  if (ref.status === "verified" && inspectable) return "verified"
  if (ref.status === "historical" || ref.status === "stale") return "historical"
  if (ref.status === "current" && inspectable) return "current"
  return "unverified"
}
