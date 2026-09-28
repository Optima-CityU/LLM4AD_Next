export type ProposalBasics = {
  topic: string
  funding: string
  question: string
  foundation: string
}

export const EMPTY_PROPOSAL_BASICS: ProposalBasics = {
  topic: "",
  funding: "",
  question: "",
  foundation: "",
}

const PROPOSAL_BRIEF_HEADINGS: Record<keyof ProposalBasics, string> = {
  topic: "Research topic",
  funding: "Funding program",
  question: "Core research question",
  foundation: "Available foundation",
}
const PROPOSAL_BRIEF_MARKER = "<!-- llm4ad:proposal-basics -->"

/** Read a saved proposal brief while retaining older free-form descriptions. */
export function parseProposalBasics(
  description: string | null | undefined,
): ProposalBasics {
  if (!description?.trim()) return { ...EMPTY_PROPOSAL_BASICS }
  if (!description.startsWith(PROPOSAL_BRIEF_MARKER)) {
    return { ...EMPTY_PROPOSAL_BASICS, topic: description.trim() }
  }
  const sections = description.split(/^## /m).slice(1)
  if (!sections.length) {
    return { ...EMPTY_PROPOSAL_BASICS, topic: description.trim() }
  }
  const basics = { ...EMPTY_PROPOSAL_BASICS }
  for (const section of sections) {
    const breakAt = section.indexOf("\n")
    if (breakAt < 0) continue
    const heading = section.slice(0, breakAt).trim()
    const field = (
      Object.keys(PROPOSAL_BRIEF_HEADINGS) as (keyof ProposalBasics)[]
    ).find((key) => PROPOSAL_BRIEF_HEADINGS[key] === heading)
    if (field) basics[field] = section.slice(breakAt + 1).trim()
  }
  return basics
}

/** Persist the author brief as readable Markdown for later proposal stages. */
export function serializeProposalBasics(basics: ProposalBasics): string {
  const sections = (
    Object.keys(PROPOSAL_BRIEF_HEADINGS) as (keyof ProposalBasics)[]
  )
    .filter((key) => basics[key].trim())
    .map((key) => `## ${PROPOSAL_BRIEF_HEADINGS[key]}\n${basics[key].trim()}`)
    .join("\n\n")
  return sections ? `${PROPOSAL_BRIEF_MARKER}\n\n${sections}` : ""
}
