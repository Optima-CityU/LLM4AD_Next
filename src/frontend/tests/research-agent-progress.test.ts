import { expect, test } from "bun:test"
import { createInstance } from "i18next"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { I18nextProvider } from "react-i18next"

import ResearchAgentProgressPanel from "../src/components/Paper/ResearchAgentProgressPanel"
import zh from "../src/i18n/locales/zh.json"
import { researchAgentProgressSchema } from "../src/lib/researchAgentProgress"

const i18n = createInstance()
await i18n.init({
  lng: "zh",
  resources: { zh: { translation: zh } },
  interpolation: { escapeValue: false },
})
function render(progress: unknown, stageStates = {}) {
  return renderToStaticMarkup(
    createElement(
      I18nextProvider,
      { i18n },
      createElement(ResearchAgentProgressPanel, {
        stages: ["rebuttal_baseline", "autorebuttal", "ac_summary"],
        stageStates,
        progress: progress ? researchAgentProgressSchema.parse(progress) : null,
        onOpenStage: () => {},
      }),
    ),
  )
}

test("unstarted stages do not invent progress or completion", () => {
  const html = render(null)
  expect(html).toContain("准备就绪")
  expect(html).toContain("已发布 0 / 3")
  expect(html).not.toContain("正在执行")
})
test("actual workers remain visible without claiming the artifact is published", () => {
  const html = render({
    sessionId: "s",
    status: "running",
    currentTool: "Read",
    agents: [
      {
        id: "a",
        parentId: "main",
        stage: "autorebuttal",
        description: "逐条整理审稿人回复",
        status: "running",
        toolCount: 1,
        currentTool: "Read",
        tools: [{ name: "Read", done: false, failed: false }],
      },
    ],
  })
  expect(html).toContain("逐条整理审稿人回复")
  expect(html).toContain("正在执行")
  expect(html).toContain("已发布 0 / 3")
})
test("the host rejects malformed child states instead of rendering untrusted progress", () => {
  expect(
    researchAgentProgressSchema.safeParse({
      sessionId: "s",
      status: "complete",
      agents: [],
    }).success,
  ).toBe(false)
})
