import { expect, test } from "bun:test"

import {
  GITHUB_ISSUES_URL,
  GITHUB_PROJECT_URL,
  getSiteMetadata,
} from "../src/lib/siteMetadata"

test("exposes the public repository and issue chooser URLs", () => {
  expect(GITHUB_PROJECT_URL).toBe(
    "https://github.com/Optima-CityU/LLM4AD_Next",
  )
  expect(GITHUB_ISSUES_URL).toBe(
    "https://github.com/Optima-CityU/LLM4AD_Next/issues/new/choose",
  )
})

test("normalizes optional footer metadata and uses a develop version fallback", () => {
  expect(getSiteMetadata({})).toEqual({
    version: "develop",
    beian: undefined,
  })
  expect(
    getSiteMetadata({
      VITE_APP_VERSION: "v1.2.3",
      VITE_FOOTER_BEIAN: "  粤ICP备12345678号  ",
    }),
  ).toEqual({
    version: "v1.2.3",
    beian: "粤ICP备12345678号",
  })
})
