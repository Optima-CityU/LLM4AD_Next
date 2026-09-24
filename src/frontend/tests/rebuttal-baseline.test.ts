import { expect, test } from "bun:test"

import { parseRebuttalBaseline } from "../src/lib/rebuttalBaseline"

const REVIEW_ID = "4e543020-7f38-407b-a941-78b44c39b94a"

test("published rebuttal baseline is converted into a presentable view", () => {
  const baseline = parseRebuttalBaseline({
    baseline: {
      summary: "Four reviewer reports were analyzed.",
      ready_for_generation: true,
      findings: ["Three reports are partially truncated."],
    },
    intake: {
      paper_summary: "The paper introduces EVE.",
      constraints: {
        venue: "NeurIPS",
        venue_year: 2025,
        response_mode: "per_reviewer",
        output_format: "markdown",
      },
      reviewer_sources: [
        {
          review_id: REVIEW_ID,
          reviewer_id: REVIEW_ID,
          display_label: "Reviewer EoAi",
        },
      ],
      open_questions: ["Confirm the inferred venue."],
    },
    analysis: {
      concerns: [
        {
          id: "4e543020-W1",
          reviewer_id: REVIEW_ID,
          label: "W",
          concern: "Evaluation covers too few task families.",
          concern_type: "evaluation_scope",
          severity: "high",
          answer_source: "Experiments section",
          draft_move: "Acknowledge the scope and clarify evidence.",
          source_refs: ["paper.md#experiments"],
        },
      ],
      reviewer_cards: [
        {
          reviewer_id: REVIEW_ID,
          sentiment: "mixed",
          movability: "swing",
          attitude: "Empirical skeptic",
          primary_concerns: ["Evaluation breadth"],
          concern_ids: ["4e543020-W1"],
        },
      ],
    },
  })

  expect(baseline?.readyForGeneration).toBeTrue()
  expect(baseline?.paperSummary).toBe("The paper introduces EVE.")
  expect(baseline?.concerns).toHaveLength(1)
  expect(baseline?.reviewerCards[0].displayLabel).toBe("Reviewer EoAi")
  expect(baseline?.openQuestions).toEqual(["Confirm the inferred venue."])
})

test("malformed or absent context does not create an empty baseline card", () => {
  expect(parseRebuttalBaseline(undefined)).toBeNull()
  expect(
    parseRebuttalBaseline({ baseline: { ready_for_generation: true } }),
  ).toBeNull()
})

test("conversation-only reviewer remains visible without a saved review file", () => {
  const baseline = parseRebuttalBaseline({
    baseline: {
      summary: "One pasted report analyzed.",
      ready_for_generation: true,
    },
    intake: {
      paper_summary: "An algorithm paper.",
      reviewer_sources: [
        {
          review_id: REVIEW_ID,
          reviewer_id: REVIEW_ID,
          display_label: "Reviewer A",
          source_system: "conversation",
          review_markdown: "## Reviewer A\n\n- Missing comparison.",
        },
      ],
    },
    analysis: {
      concerns: [
        {
          id: "reviewer-a-w1",
          reviewer_id: REVIEW_ID,
          label: "W",
          concern: "Missing comparison.",
          severity: "medium",
        },
      ],
      reviewer_cards: [{ reviewer_id: REVIEW_ID }],
    },
  })

  expect(baseline?.reviewerSources[0].sourceSystem).toBe("conversation")
  expect(baseline?.reviewerSources[0].reviewMarkdown).toBe(
    "## Reviewer A\n\n- Missing comparison.",
  )
  expect(baseline?.reviewerCards[0].displayLabel).toBe("Reviewer A")
  expect(baseline?.concerns[0].reviewerId).toBe(REVIEW_ID)
})
