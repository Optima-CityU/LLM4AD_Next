import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  Bug,
  Calendar,
  ExternalLink,
  Gauge,
  Package,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Tag,
  Wrench,
  Zap,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import type {
  ChangeCategory,
  ChangelogData,
  ChangelogSection,
  ChangeType,
  MonthRelease,
} from "@/data/changelog"
import { loadChangelog } from "@/data/changelog"

export const Route = createFileRoute("/_layout/changelog")({
  component: ChangelogPage,
  head: () => ({
    meta: [{ title: "Changelog - LLM4AD_Next" }],
  }),
})

const CHANGE_TYPE_CONFIG: Record<
  ChangeType,
  { icon: typeof Sparkles; className: string }
> = {
  feat: { icon: Sparkles, className: "text-emerald-500 dark:text-emerald-400" },
  fix: { icon: Bug, className: "text-red-500 dark:text-red-400" },
  improve: { icon: Zap, className: "text-blue-500 dark:text-blue-400" },
  breaking: {
    icon: AlertTriangle,
    className: "text-amber-500 dark:text-amber-400",
  },
  perf: { icon: Gauge, className: "text-purple-500 dark:text-purple-400" },
  ref: { icon: RefreshCw, className: "text-cyan-500 dark:text-cyan-400" },
  add: { icon: Plus, className: "text-teal-500 dark:text-teal-400" },
  other: { icon: Wrench, className: "text-gray-500 dark:text-gray-400" },
}

function ChangelogPage() {
  const { t, i18n } = useTranslation()
  const [search, setSearch] = useState("")
  const [data, setData] = useState<ChangelogData>({
    sections: [],
    footerLinks: [],
  })
  const [loading, setLoading] = useState(true)

  const lang = i18n.language?.startsWith("zh") ? "zh" : "en"

  useEffect(() => {
    setLoading(true)
    loadChangelog(lang).then((d) => {
      setData(d)
      setLoading(false)
    })
  }, [lang])

  const releases = useMemo(
    () => data.sections.filter((s): s is MonthRelease => s.kind === "release"),
    [data.sections],
  )

  const totalChanges = useMemo(
    () =>
      releases.reduce(
        (acc, r) =>
          acc + r.categories.reduce((a, c) => a + c.changes.length, 0),
        0,
      ),
    [releases],
  )

  const filtered = useMemo(() => {
    if (!search.trim()) return data.sections
    const q = search.toLowerCase()
    return data.sections
      .map((section) => {
        if (section.kind === "markdown") {
          if (
            section.title.toLowerCase().includes(q) ||
            section.content.toLowerCase().includes(q)
          ) {
            return section
          }
          return null
        }
        const r = section
        const matchMonth =
          r.month.toLowerCase().includes(q) ||
          r.summary?.toLowerCase().includes(q)
        const filteredCats = r.categories
          .map((cat) => ({
            ...cat,
            changes: cat.changes.filter(
              (c) =>
                c.content.toLowerCase().includes(q) ||
                c.type.toLowerCase().includes(q) ||
                cat.name.toLowerCase().includes(q),
            ),
          }))
          .filter((cat) => cat.changes.length > 0)

        if (matchMonth && filteredCats.length === 0) return r
        if (filteredCats.length > 0) return { ...r, categories: filteredCats }
        return null
      })
      .filter(Boolean) as ChangelogSection[]
  }, [data.sections, search])

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <div className="flex items-center justify-between gap-2">
          <Skeleton className="h-5 w-64" />
          <Skeleton className="h-8 w-64" />
        </div>
        <div className="flex flex-col gap-6">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex gap-6">
              <div className="hidden md:flex md:w-[30px] md:justify-center">
                <Skeleton className="mt-6 size-3 rounded-full" />
              </div>
              <div className="flex-1 space-y-3">
                <Skeleton className="h-6 w-48" />
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 pb-8">
      {/* Toolbar */}
      <div className="shrink-0 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
          <p>{t("changelog.subtitle")}</p>
          <span className="flex items-center gap-1.5">
            <Calendar className="size-3.5" />
            {t("changelog.totalMonths", { count: releases.length })}
          </span>
          <span className="flex items-center gap-1.5">
            <Tag className="size-3.5" />
            {t("changelog.totalChanges", { count: totalChanges })}
          </span>
        </div>
        <div className="relative w-full sm:w-64">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("changelog.searchPlaceholder")}
            className="h-8 pl-8 text-sm"
          />
        </div>
      </div>

      {/* Timeline */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Package className="mb-3 size-10 text-muted-foreground/40" />
          <p className="text-sm font-medium text-muted-foreground">
            {t("changelog.noResults")}
          </p>
        </div>
      ) : (
        <div className="relative">
          <div className="absolute left-[15px] top-2 bottom-0 hidden w-0.5 bg-border md:block" />

          <div className="flex flex-col gap-6">
            {filtered.map((section, idx) =>
              section.kind === "release" ? (
                <MonthCard
                  key={section.month}
                  release={section}
                  isFirst={idx === 0}
                  search={search}
                />
              ) : (
                <MarkdownCard key={section.title} section={section} />
              ),
            )}
          </div>
        </div>
      )}

      {/* Footer links */}
      {data.footerLinks.length > 0 && (
        <div className="border-t border-border pt-4 pb-2">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            {t("changelog.seeAlso")}
          </h4>
          <ul className="space-y-1.5">
            {data.footerLinks.map((link) => (
              <li key={link.url} className="flex items-center gap-2 text-sm">
                <ExternalLink className="size-3.5 text-muted-foreground shrink-0" />
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  {link.label}
                </a>
                {link.description && (
                  <span className="text-muted-foreground">
                    — {link.description}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function MarkdownCard({
  section,
}: {
  section: { title: string; content: string }
}) {
  return (
    <div className="relative flex gap-6">
      <div className="relative z-10 hidden shrink-0 md:flex md:w-[30px] md:justify-center">
        <div className="mt-6 flex flex-col items-center">
          <div className="size-3 rounded-full bg-muted-foreground/30 shadow-[0_0_0_4px] shadow-muted-foreground/10 dark:shadow-muted-foreground/15" />
        </div>
      </div>

      <Card className="flex-1 transition-all hover:shadow-md hover:border-border/80">
        <CardHeader className="gap-1 pb-3">
          <h3 className="text-sm font-medium text-foreground">
            {section.title}
          </h3>
        </CardHeader>
        <CardContent className="pt-0 prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-li:my-0.5 prose-a:text-primary">
          <Markdown remarkPlugins={[remarkGfm]}>{section.content}</Markdown>
        </CardContent>
      </Card>
    </div>
  )
}

function MonthCard({
  release,
  isFirst,
  search,
}: {
  release: MonthRelease
  isFirst: boolean
  search: string
}) {
  const { t } = useTranslation()

  return (
    <div className="relative flex gap-6">
      {/* Timeline dot */}
      <div className="relative z-10 hidden shrink-0 md:flex md:w-[30px] md:justify-center">
        <div className="mt-6 flex flex-col items-center">
          <div
            className={`size-3 rounded-full ${
              release.isUnreleased
                ? "bg-primary shadow-[0_0_0_4px] shadow-primary/20 dark:shadow-primary/30"
                : "bg-muted-foreground/40 shadow-[0_0_0_4px] shadow-muted-foreground/10 dark:shadow-muted-foreground/15"
            }`}
          />
        </div>
      </div>

      {/* Card */}
      <Card
        className={`flex-1 transition-all hover:shadow-md ${
          isFirst
            ? "border-primary/30 shadow-sm dark:border-primary/20"
            : "hover:border-border/80"
        }`}
      >
        <CardHeader className="gap-2 pb-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              <Calendar className="size-3.5 text-muted-foreground" />
              {release.month}
            </span>
            {release.isUnreleased && (
              <Badge className="bg-primary/15 text-primary border-primary/30 text-[10px] leading-none">
                {t("changelog.unreleased")}
              </Badge>
            )}
            {isFirst && !release.isUnreleased && (
              <Badge className="bg-primary/15 text-primary border-primary/30 text-[10px] leading-none">
                {t("changelog.latest")}
              </Badge>
            )}
          </div>
          {release.summary && (
            <p className="text-sm text-muted-foreground leading-relaxed">
              {highlightText(release.summary, search)}
            </p>
          )}
        </CardHeader>
        <CardContent className="pt-0 space-y-4">
          {release.categories.map((cat, i) => (
            <CategorySection key={i} category={cat} search={search} />
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

function CategorySection({
  category,
  search,
}: {
  category: ChangeCategory
  search: string
}) {
  const { t } = useTranslation()

  return (
    <div>
      {category.name && (
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
          {category.name}
        </h4>
      )}
      <ul className="space-y-1.5">
        {category.changes.map((change, i) => {
          const config =
            CHANGE_TYPE_CONFIG[change.type] ?? CHANGE_TYPE_CONFIG.other
          const Icon = config.icon
          return (
            <li key={i} className="flex items-start gap-2.5">
              <span
                className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded ${config.className} bg-current/[0.08]`}
              >
                <Icon className={`size-3 ${config.className}`} />
              </span>
              <span className="text-sm text-foreground/85 leading-relaxed">
                <span
                  className={`mr-1.5 inline-block rounded px-1 py-0.5 text-[10px] font-medium leading-none ${config.className} bg-current/[0.08]`}
                >
                  {t(`changelog.changeType.${change.type}`)}
                </span>
                {highlightText(change.content, search)}
                {change.prNumber && change.prUrl && (
                  <a
                    href={change.prUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-1 inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-primary transition-colors"
                  >
                    #{change.prNumber}
                    <ExternalLink className="size-2.5" />
                  </a>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function highlightText(text: string, search: string) {
  if (!search.trim()) return text
  const regex = new RegExp(
    `(${search.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`,
    "gi",
  )
  const parts = text.split(regex)
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark
        key={i}
        className="rounded bg-primary/20 px-0.5 text-primary dark:bg-primary/30"
      >
        {part}
      </mark>
    ) : (
      part
    ),
  )
}
