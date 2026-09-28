import { expect, test } from "bun:test"

import {
  parseProposalBasics,
  serializeProposalBasics,
} from "../src/lib/proposalBasics"

test("proposal basics survive a save and reload", () => {
  const basics = {
    topic: "Adaptive optimization",
    funding: "General research grant",
    question: "How can the search remain reliable?",
    foundation: "Existing benchmark data and pilot results.",
  }

  expect(parseProposalBasics(serializeProposalBasics(basics))).toEqual(basics)
})

test("older free-form project descriptions remain editable", () => {
  const legacyDescription =
    "## Background\nA proposal about robust optimization."
  expect(parseProposalBasics(legacyDescription)).toEqual({
    topic: legacyDescription,
    funding: "",
    question: "",
    foundation: "",
  })
})

test("empty basics do not manufacture a project brief", () => {
  expect(serializeProposalBasics(parseProposalBasics(null))).toBe("")
})
