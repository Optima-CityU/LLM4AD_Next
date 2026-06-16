export type ChangeType =
  | "feat"
  | "fix"
  | "improve"
  | "breaking"
  | "perf"
  | "ref"
  | "add"
  | "other"

export interface Change {
  type: ChangeType
  content: string
  prNumber?: string
  prUrl?: string
}

export interface ChangeCategory {
  name: string
  changes: Change[]
}

export interface MonthRelease {
  kind: "release"
  month: string
  date: string
  summary?: string
  categories: ChangeCategory[]
  isUnreleased?: boolean
}

export interface MarkdownSection {
  kind: "markdown"
  title: string
  content: string
}

export type ChangelogSection = MonthRelease | MarkdownSection

export interface FooterLink {
  label: string
  url: string
  description?: string
}

export interface ChangelogData {
  sections: ChangelogSection[]
  footerLinks: FooterLink[]
}

const changelogLoaders = import.meta.glob("@docs/**/changelog.md", {
  query: "?raw",
  import: "default",
}) as Record<string, () => Promise<string>>

function resolveLoader(lang: string): (() => Promise<string>) | undefined {
  const suffix = `/${lang}/changelog.md`
  const entry = Object.entries(changelogLoaders).find(([k]) =>
    k.endsWith(suffix),
  )
  return entry?.[1]
}

const CHANGE_TYPE_MAP: Record<string, ChangeType> = {
  feat: "feat",
  fix: "fix",
  improve: "improve",
  breaking: "breaking",
  perf: "perf",
  ref: "ref",
  add: "add",
  "fix/perf": "perf",
}

const CHANGE_ITEM_RE = /^- \*\*([^*]+)\*\*[：:]\s*(.+)/

const RELEASE_HEADER_RE = /^## \[?(.+?)\]?\s*[—–-]\s*(.+)/

const MONTH_HEADER_RE = /^## (.+)/

const DATE_LIKE_RE =
  /\d{4}|\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b|[一二三四五六七八九十]+月|\d+\s*月/i

function isDateLikeHeader(text: string): boolean {
  return DATE_LIKE_RE.test(text)
}

function parseChangeItem(line: string): Change | null {
  const m = line.match(CHANGE_ITEM_RE)
  if (!m) return null

  const rawType = m[1]
    .replace(/（.*?）/g, "")
    .replace(/\(.*?\)/g, "")
    .trim()
    .toLowerCase()

  const type = CHANGE_TYPE_MAP[rawType] ?? "other"

  let content = m[2].trim()
  let prNumber: string | undefined
  let prUrl: string | undefined

  const prMatch = content.match(/\[#(\d+)\]\(([^)]+)\)/)
  if (prMatch) {
    prNumber = prMatch[1]
    prUrl = prMatch[2]
    content = content.replace(/\s*\(\[#\d+\]\([^)]+\)\)\s*$/, "").trim()
    content = content.replace(/\s*\[#\d+\]\([^)]+\)\s*$/, "").trim()
  }

  if (content.endsWith("。")) content = content.slice(0, -1)
  if (content.endsWith(".")) content = content.slice(0, -1)

  return { type, content, prNumber, prUrl }
}

function parseChangelog(raw: string): ChangelogData {
  const lines = raw.split("\n")
  const sections: ChangelogSection[] = []
  const footerLinks: FooterLink[] = []
  let current: MonthRelease | null = null
  let currentMd: MarkdownSection | null = null
  let currentCategory: ChangeCategory | null = null
  let collectingSummary = false
  let inFooter = false

  function flush() {
    if (current) {
      sections.push(current)
      current = null
    }
    if (currentMd) {
      currentMd.content = currentMd.content.trim()
      if (currentMd.content) sections.push(currentMd)
      currentMd = null
    }
    currentCategory = null
    collectingSummary = false
  }

  for (const line of lines) {
    if (line.startsWith("# ")) continue

    if (line.startsWith("## 相关链接") || line.startsWith("## See also")) {
      flush()
      inFooter = true
      continue
    }

    if (inFooter) {
      const linkMatch = line.match(/^- \[(.+?)\]\((.+?)\)\s*[—–-]?\s*(.*)/)
      if (linkMatch) {
        footerLinks.push({
          label: linkMatch[1],
          url: linkMatch[2],
          description: linkMatch[3]?.trim() || undefined,
        })
      }
      continue
    }

    const releaseMatch = line.match(RELEASE_HEADER_RE)
    if (releaseMatch) {
      flush()
      const rawTitle = releaseMatch[1].trim()
      const dateStr = releaseMatch[2].trim()
      const isUnreleased = rawTitle === "Unreleased" || rawTitle === "未发布"

      current = {
        kind: "release",
        month: isUnreleased ? dateStr : rawTitle,
        date: dateStr,
        categories: [],
        isUnreleased,
      }
      continue
    }

    const h2Match = line.match(MONTH_HEADER_RE)
    if (h2Match && !line.startsWith("## [")) {
      flush()
      const headerText = h2Match[1].trim()

      if (isDateLikeHeader(headerText)) {
        current = {
          kind: "release",
          month: headerText,
          date: headerText,
          categories: [],
        }
      } else {
        currentMd = {
          kind: "markdown",
          title: headerText,
          content: "",
        }
      }
      continue
    }

    if (currentMd) {
      currentMd.content += `${line}\n`
      continue
    }

    if (!current) continue

    const h3Match = line.match(/^### (.+)/)
    if (h3Match) {
      currentCategory = { name: h3Match[1].trim(), changes: [] }
      current.categories.push(currentCategory)
      collectingSummary = false
      continue
    }

    if (line.startsWith("- ")) {
      const change = parseChangeItem(line)
      if (change) {
        if (currentCategory) {
          currentCategory.changes.push(change)
        } else {
          if (!current.categories.length) {
            currentCategory = { name: "", changes: [] }
            current.categories.push(currentCategory)
          } else {
            currentCategory = current.categories[current.categories.length - 1]
          }
          currentCategory.changes.push(change)
        }
        collectingSummary = false
      }
      continue
    }

    const trimmed = line.trim()
    if (
      trimmed &&
      !trimmed.startsWith("[") &&
      current.categories.length === 0 &&
      !currentCategory
    ) {
      if (current.summary) {
        current.summary += ` ${trimmed}`
      } else {
        current.summary = trimmed
        collectingSummary = true
      }
    } else if (collectingSummary && trimmed) {
      current.summary += ` ${trimmed}`
    }
  }

  flush()

  return { sections, footerLinks }
}

const cache = new Map<string, ChangelogData>()

export async function loadChangelog(lang: string): Promise<ChangelogData> {
  const key = lang === "zh" ? "zh" : "en"
  if (cache.has(key)) return cache.get(key)!

  const loader = resolveLoader(key)
  if (!loader) return { sections: [], footerLinks: [] }

  const raw = await loader()
  const parsed = parseChangelog(raw)
  cache.set(key, parsed)
  return parsed
}
