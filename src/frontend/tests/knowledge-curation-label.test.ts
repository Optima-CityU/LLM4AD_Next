import { expect, test } from "bun:test"

const zh = await Bun.file(
  new URL("../src/i18n/locales/zh.json", import.meta.url),
).json()
const en = await Bun.file(
  new URL("../src/i18n/locales/en.json", import.meta.url),
).json()

test("knowledge document management is presented as knowledge curation", () => {
  expect(zh.knowledge.title).toBe("知识沉淀")
  expect(zh.memory.page.documentsTab).toBe("知识沉淀")
  expect(en.knowledge.title).toBe("Knowledge Curation")
  expect(en.memory.page.documentsTab).toBe("Knowledge Curation")
})
