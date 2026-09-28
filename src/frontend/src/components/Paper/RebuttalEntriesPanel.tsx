import {
  Check,
  ChevronDown,
  CircleAlert,
  Clipboard,
  Edit3,
  Lightbulb,
  ListTodo,
  Loader2,
  MessageSquarePlus,
  MessagesSquare,
  PenLine,
  ScanSearch,
  ShieldAlert,
  Trash2,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { toast } from "sonner"

import type {
  PaperReviewResponse,
  RebuttalEntry,
  RebuttalOutput,
} from "@/client"
import RebuttalBaselinePanel from "@/components/Paper/RebuttalBaselinePanel"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import {
  useAttachReviewerFeedback,
  useDeleteReviewerFeedback,
  usePaperReviewContent,
  useUpdateReviewerFeedback,
} from "@/hooks/usePapers"
import type { RebuttalBaselineReviewerSource } from "@/lib/rebuttalBaseline"
import { parseRebuttalBaseline } from "@/lib/rebuttalBaseline"

type RebuttalEntriesPanelProps = {
  workspaceId: string
  sourceVersionId: string
  reviews: PaperReviewResponse[]
  entries: RebuttalEntry[]
  rebuttalOutput?: RebuttalOutput | null
  baselineContext?: Record<string, unknown> | null
  baselineStale: boolean
  readyForSubmission: boolean
  stale: boolean
  activeStage: "rebuttal_baseline" | "autorebuttal" | "ac_summary"
  chairMessage?: string | null
  chairStale: boolean
}

type ReviewEditorMode = "create" | "edit"
type RebuttalWorkspaceView = "baseline" | "reviews" | "chair"

function viewForStage(
  stage: RebuttalEntriesPanelProps["activeStage"],
): RebuttalWorkspaceView {
  return stage === "rebuttal_baseline"
    ? "baseline"
    : stage === "autorebuttal"
      ? "reviews"
      : "chair"
}

type ReviewerDisplayGroup = {
  reviewerId: string
  displayLabel: string
  title: string | null
  sourceSystem: string
  sourceUrl: string | null
  review: PaperReviewResponse | null
  baselineSource: RebuttalBaselineReviewerSource | null
}

type GuidanceCardProps = {
  accent: "required" | "suggestion"
  index: number
  item: string
  fallbackTitle: string
  label: string
}

function splitGuidanceItem(item: string, fallbackTitle: string) {
  const separator = item.search(/[:：]/)
  if (separator <= 0 || separator > 80) {
    return { title: fallbackTitle, body: item }
  }
  return {
    title: item.slice(0, separator).trim(),
    body: item.slice(separator + 1).trim(),
  }
}

function GuidanceCard({
  accent,
  index,
  item,
  fallbackTitle,
  label,
}: GuidanceCardProps) {
  const { title, body } = splitGuidanceItem(item, fallbackTitle)
  const required = accent === "required"

  return (
    <article
      className={`min-w-0 overflow-hidden border border-l-[3px] border-border/70 bg-background ${
        required ? "border-l-amber-500/70" : "border-l-sky-500/65"
      }`}
    >
      <div className="flex items-start gap-4 px-4 py-4 sm:px-5">
        <span
          className={`grid size-9 shrink-0 place-items-center font-serif text-base font-semibold tabular-nums ${
            required
              ? "bg-amber-500/12 text-amber-800 dark:text-amber-200"
              : "bg-sky-500/10 text-sky-800 dark:text-sky-200"
          }`}
        >
          {String(index + 1).padStart(2, "0")}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-start gap-2.5">
            {required ? (
              <CircleAlert
                aria-hidden="true"
                className="mt-1 size-4 shrink-0 text-amber-700 dark:text-amber-300"
              />
            ) : (
              <Lightbulb
                aria-hidden="true"
                className="mt-1 size-4 shrink-0 text-sky-700 dark:text-sky-300"
              />
            )}
            <span className="sr-only">{label}</span>
            <h4 className="min-w-0 font-serif text-lg font-semibold leading-7 text-pretty">
              {title}
            </h4>
          </div>
          <div className="prose mt-2 min-w-0 max-w-none break-words text-base leading-7 text-foreground/85 dark:prose-invert [&_li]:leading-7 [&_p]:my-0 [&_p]:leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
          </div>
        </div>
      </div>
    </article>
  )
}

export default function RebuttalEntriesPanel({
  workspaceId,
  sourceVersionId,
  reviews,
  entries,
  rebuttalOutput,
  baselineContext,
  baselineStale,
  readyForSubmission,
  stale,
  activeStage,
  chairMessage,
  chairStale,
}: RebuttalEntriesPanelProps) {
  const { t, i18n } = useTranslation()
  const [activeView, setActiveView] = useState<RebuttalWorkspaceView>(() =>
    viewForStage(activeStage),
  )
  useEffect(() => {
    setActiveView(viewForStage(activeStage))
  }, [activeStage])
  const [editorMode, setEditorMode] = useState<ReviewEditorMode>("create")
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingReviewId, setEditingReviewId] = useState<string | null>(null)
  const [expandedReviewId, setExpandedReviewId] = useState<string | null>(null)
  const hasAutoExpandedReview = useRef(false)
  const [reviewToDelete, setReviewToDelete] =
    useState<PaperReviewResponse | null>(null)
  const [reviewerLabel, setReviewerLabel] = useState("")
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const attachReview = useAttachReviewerFeedback(workspaceId)
  const updateReview = useUpdateReviewerFeedback(workspaceId)
  const deleteReview = useDeleteReviewerFeedback(workspaceId)
  const [copiedText, copy] = useCopyToClipboard()
  const baseline = useMemo(
    () => parseRebuttalBaseline(baselineContext),
    [baselineContext],
  )
  const reviewerGroups = useMemo<ReviewerDisplayGroup[]>(() => {
    const baselineSources = baseline?.reviewerSources ?? []
    const sourcesById = new Map(
      baselineSources.flatMap((source) => [
        [source.reviewId, source] as const,
        [source.reviewerId, source] as const,
      ]),
    )
    const groups: ReviewerDisplayGroup[] = reviews.map((review) => {
      const source = sourcesById.get(review.id) ?? null
      return {
        reviewerId: source?.reviewerId ?? review.id,
        displayLabel: source?.displayLabel ?? review.reviewer_label,
        title: source?.title ?? review.title,
        sourceSystem: source?.sourceSystem ?? review.source_system,
        sourceUrl: source?.sourceUrl ?? null,
        review,
        baselineSource: source,
      }
    })
    const knownIds = new Set(
      groups.flatMap((group) => [group.reviewerId, group.review?.id ?? ""]),
    )
    for (const source of baselineSources) {
      if (knownIds.has(source.reviewerId) || knownIds.has(source.reviewId)) {
        continue
      }
      groups.push({
        reviewerId: source.reviewerId,
        displayLabel: source.displayLabel,
        title: source.title,
        sourceSystem: source.sourceSystem,
        sourceUrl: source.sourceUrl,
        review: null,
        baselineSource: source,
      })
      knownIds.add(source.reviewerId)
      knownIds.add(source.reviewId)
    }
    return groups
  }, [baseline, reviews])
  const expandedReviewerGroup = reviewerGroups.find(
    (group) => group.reviewerId === expandedReviewId,
  )
  const expandedReview = expandedReviewerGroup?.review ?? null
  const reviewContent = usePaperReviewContent(expandedReview?.id ?? null)
  const groupedEntries = useMemo(
    () =>
      entries.reduce<Record<string, RebuttalEntry[]>>((groups, entry) => {
        const reviewerEntries = groups[entry.reviewer_id] ?? []
        reviewerEntries.push(entry)
        groups[entry.reviewer_id] = reviewerEntries
        return groups
      }, {}),
    [entries],
  )
  const reviewerConcernCounts = useMemo(() => {
    if (!baseline) return new Map<string, number>()
    const counts = new Map(
      baseline.reviewerSources.map((source) => [source.reviewerId, 0]),
    )
    for (const concern of baseline.concerns) {
      counts.set(concern.reviewerId, (counts.get(concern.reviewerId) ?? 0) + 1)
    }
    return counts
  }, [baseline])
  const authorActions = rebuttalOutput?.open_placeholders ?? []
  const agentSuggestions = rebuttalOutput?.findings ?? []
  const guidanceCount = authorActions.length + agentSuggestions.length
  useEffect(() => {
    if (
      activeView !== "reviews" ||
      hasAutoExpandedReview.current ||
      entries.length === 0
    )
      return
    if (expandedReviewId) {
      hasAutoExpandedReview.current = true
      return
    }

    const firstReviewWithResponses = reviewerGroups.find(
      (group) => (groupedEntries[group.reviewerId]?.length ?? 0) > 0,
    )
    if (!firstReviewWithResponses) return

    hasAutoExpandedReview.current = true
    setExpandedReviewId(firstReviewWithResponses.reviewerId)
  }, [
    activeView,
    entries.length,
    expandedReviewId,
    groupedEntries,
    reviewerGroups,
  ])
  const dateFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }),
    [i18n.language],
  )
  const editorPending = attachReview.isPending || updateReview.isPending

  const resetForm = () => {
    setEditingReviewId(null)
    setReviewerLabel("")
    setTitle("")
    setContent("")
  }

  const openCreateEditor = () => {
    resetForm()
    setEditorMode("create")
    setEditorOpen(true)
  }

  const toggleReview = (reviewerId: string) => {
    setExpandedReviewId((current) =>
      current === reviewerId ? null : reviewerId,
    )
  }

  const openEditEditor = () => {
    if (!expandedReview || !reviewContent.data) return
    setEditorMode("edit")
    setEditingReviewId(expandedReview.id)
    setReviewerLabel(expandedReview.reviewer_label)
    setTitle(expandedReview.title ?? "")
    setContent(reviewContent.data.content)
    setEditorOpen(true)
  }

  const submitReview = async () => {
    if (!reviewerLabel.trim() || !content.trim()) return
    try {
      if (editorMode === "edit" && editingReviewId) {
        await updateReview.mutateAsync({
          reviewId: editingReviewId,
          body: {
            reviewer_label: reviewerLabel.trim(),
            title: title.trim() || null,
            content: content.trim(),
          },
        })
        toast.success(t("paper.rebuttal.reviewUpdated"))
      } else {
        await attachReview.mutateAsync({
          sourceVersionId,
          body: {
            reviewer_label: reviewerLabel.trim(),
            title: title.trim() || null,
            source_system: "manual",
            content: content.trim(),
          },
        })
        toast.success(t("paper.rebuttal.reviewAdded"))
      }
      setEditorOpen(false)
      resetForm()
    } catch {
      toast.error(
        t(
          editorMode === "edit"
            ? "paper.rebuttal.reviewUpdateFailed"
            : "paper.rebuttal.reviewAddFailed",
        ),
      )
    }
  }

  const confirmDelete = async () => {
    if (!reviewToDelete) return
    try {
      await deleteReview.mutateAsync(reviewToDelete.id)
      if (expandedReviewId === reviewToDelete.id) setExpandedReviewId(null)
      setReviewToDelete(null)
      toast.success(t("paper.rebuttal.reviewDeleted"))
    } catch {
      toast.error(t("paper.rebuttal.reviewDeleteFailed"))
    }
  }

  return (
    <div
      data-testid="rebuttal-dossier"
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background"
    >
      <h2 className="sr-only">{t("paper.rebuttal.title")}</h2>

      <Tabs
        value={activeView}
        onValueChange={(value) => setActiveView(value as RebuttalWorkspaceView)}
        className="min-h-0 flex-1 gap-0"
      >
        <div className="shrink-0 border-b bg-background px-5 sm:px-6">
          <TabsList className="grid h-12 w-full grid-cols-3 rounded-none bg-transparent p-0 shadow-none">
            <TabsTrigger
              value="baseline"
              className="h-12 gap-2 rounded-none border-b-2 border-transparent font-serif text-sm shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              <ScanSearch aria-hidden="true" />
              <span>{t("paper.rebuttal.views.baseline")}</span>
              {baseline && (
                <span className="tabular-nums text-xs text-muted-foreground">
                  {baseline.concerns.length}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger
              value="reviews"
              className="h-12 gap-2 rounded-none border-b-2 border-transparent font-serif text-sm shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              <MessagesSquare aria-hidden="true" />
              <span>{t("paper.rebuttal.views.reviews")}</span>
              <span className="tabular-nums text-xs text-muted-foreground">
                {reviewerGroups.length}
              </span>
            </TabsTrigger>
            <TabsTrigger
              value="chair"
              className="h-12 gap-2 rounded-none border-b-2 border-transparent font-serif text-sm shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              <PenLine aria-hidden="true" />
              <span>{t("paper.rebuttal.views.chair")}</span>
            </TabsTrigger>
          </TabsList>
        </div>

        {stale && (entries.length > 0 || rebuttalOutput) && (
          <div className="flex shrink-0 gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-900 dark:text-amber-100">
            <ShieldAlert
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0"
            />
            <span className="leading-5">{t("paper.rebuttal.stale")}</span>
          </div>
        )}

        <TabsContent
          value="reviews"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <ScrollArea className="h-full min-h-0 min-w-0">
            <div className="flex w-full min-w-0 flex-col gap-5 overflow-x-hidden p-3 sm:p-4">
              {rebuttalOutput?.global_response && (
                <section className="min-w-0 overflow-hidden border border-primary/20 border-l-4 border-l-primary/45 bg-primary/[0.02]">
                  <header className="relative border-b border-primary/15 px-5 py-4 pr-14">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-serif text-lg font-semibold">
                        {rebuttalOutput.global_response.title}
                      </h3>
                      <Badge variant="secondary" className="font-normal">
                        {t("paper.rebuttal.globalResponse")}
                      </Badge>
                    </div>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      {t("paper.rebuttal.globalResponseDescription")}
                    </p>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      className="absolute top-2.5 right-2.5"
                      title={t("paper.rebuttal.copy")}
                      aria-label={t("paper.rebuttal.copy")}
                      onClick={async () => {
                        const response =
                          rebuttalOutput.global_response?.response
                        if (response && (await copy(response))) {
                          toast.success(t("paper.rebuttal.copied"))
                        } else {
                          toast.error(t("paper.rebuttal.copyFailed"))
                        }
                      }}
                    >
                      {copiedText ===
                      rebuttalOutput.global_response.response ? (
                        <Check />
                      ) : (
                        <Clipboard />
                      )}
                    </Button>
                  </header>
                  <article className="prose min-w-0 max-w-none break-words px-5 py-5 text-[15px] leading-7 text-foreground/90 dark:prose-invert [&_a]:break-all [&_li]:leading-7 [&_p]:leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {rebuttalOutput.global_response.response}
                    </ReactMarkdown>
                  </article>
                </section>
              )}

              {rebuttalOutput && guidanceCount > 0 && (
                <details
                  data-testid="rebuttal-guidance-layout"
                  className="group min-w-0 border border-amber-500/20 bg-amber-500/[0.035]"
                >
                  <summary className="flex min-w-0 cursor-pointer list-none items-center gap-3 px-4 py-3 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
                    <ListTodo
                      aria-hidden="true"
                      className="size-4 shrink-0 text-amber-700 dark:text-amber-300"
                    />
                    <span className="min-w-0 flex-1 font-serif text-base font-semibold">
                      {t(
                        authorActions.length > 0
                          ? "paper.rebuttal.guidanceTitle"
                          : "paper.rebuttal.guidanceReadyTitle",
                      )}
                    </span>
                    <Badge variant="outline" className="shrink-0 tabular-nums">
                      {guidanceCount}
                    </Badge>
                    <ChevronDown
                      aria-hidden="true"
                      className="size-4 shrink-0 transition-transform group-open:rotate-180 motion-reduce:transition-none"
                    />
                  </summary>
                  <div className="space-y-5 border-t px-4 py-4">
                    {authorActions.length > 0 && (
                      <section className="min-w-0 space-y-3">
                        <h3 className="font-serif text-lg font-semibold">
                          {t("paper.rebuttal.authorActions")}
                        </h3>
                        <div
                          data-testid="rebuttal-author-action-list"
                          className="space-y-3"
                        >
                          {authorActions.map((item, index) => (
                            <GuidanceCard
                              key={`required-${index}-${item}`}
                              accent="required"
                              index={index}
                              item={item}
                              fallbackTitle={t(
                                "paper.rebuttal.authorActionFallback",
                                { index: index + 1 },
                              )}
                              label={t("paper.rebuttal.authorActionLabel")}
                            />
                          ))}
                        </div>
                      </section>
                    )}
                    {agentSuggestions.length > 0 && (
                      <section className="min-w-0 space-y-3">
                        <h3 className="font-serif text-lg font-semibold">
                          {t("paper.rebuttal.agentSuggestions")}
                        </h3>
                        <div
                          data-testid="rebuttal-agent-suggestion-list"
                          className="space-y-3"
                        >
                          {agentSuggestions.map((item, index) => (
                            <GuidanceCard
                              key={`suggestion-${index}-${item}`}
                              accent="suggestion"
                              index={index}
                              item={item}
                              fallbackTitle={t(
                                "paper.rebuttal.agentSuggestionFallback",
                                { index: index + 1 },
                              )}
                              label={t("paper.rebuttal.agentSuggestionLabel")}
                            />
                          ))}
                        </div>
                      </section>
                    )}
                  </div>
                </details>
              )}

              {reviewerGroups.length > 0 && (
                <section
                  data-testid="review-feedback-groups"
                  className="flex min-w-0 max-w-full flex-col gap-2.5"
                >
                  <div className="flex flex-wrap items-end justify-between gap-3 px-0.5">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-serif text-lg font-semibold">
                          {t("paper.rebuttal.attachedReviews")}
                        </h3>
                        {readyForSubmission && (
                          <Badge variant="secondary" className="gap-1 text-xs">
                            <Check aria-hidden="true" className="size-3" />
                            {t("paper.rebuttal.ready")}
                          </Badge>
                        )}
                      </div>
                      <p className="mt-1 text-sm leading-5 text-muted-foreground">
                        {t("paper.rebuttal.reviewResponseRelationship")}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={openCreateEditor}
                    >
                      <MessageSquarePlus data-icon="inline-start" />
                      {t("paper.rebuttal.addReview")}
                    </Button>
                  </div>
                  <div className="flex min-w-0 max-w-full flex-col gap-2">
                    {reviewerGroups.map((reviewerGroup, index) => {
                      const reviewerEntries =
                        groupedEntries[reviewerGroup.reviewerId] ?? []
                      const expanded =
                        expandedReviewId === reviewerGroup.reviewerId
                      const noResponseNeeded =
                        reviewerConcernCounts.get(reviewerGroup.reviewerId) ===
                        0
                      const reviewerConcerns =
                        baseline?.concerns.filter(
                          (concern) =>
                            concern.reviewerId === reviewerGroup.reviewerId,
                        ) ?? []

                      return (
                        <Card
                          key={reviewerGroup.reviewerId}
                          className={`min-w-0 max-w-full gap-0 overflow-hidden rounded-none py-0 shadow-none transition-colors ${
                            expanded
                              ? "border-foreground/20 bg-muted/10"
                              : "hover:border-foreground/20"
                          }`}
                        >
                          <button
                            type="button"
                            className="group flex w-full min-w-0 items-center gap-3 p-3 text-left transition-colors hover:bg-muted/35 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset focus-visible:outline-none"
                            aria-expanded={expanded}
                            aria-controls={`review-content-${reviewerGroup.reviewerId}`}
                            title={t(
                              expanded
                                ? "paper.rebuttal.collapseReview"
                                : "paper.rebuttal.expandReview",
                            )}
                            onClick={() =>
                              toggleReview(reviewerGroup.reviewerId)
                            }
                          >
                            <span className="grid w-12 shrink-0 place-items-center self-stretch border-r bg-muted/20 font-serif text-xl font-semibold text-muted-foreground">
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            <span className="flex min-w-0 flex-1 flex-col gap-1">
                              <span className="flex min-w-0 items-center gap-2">
                                <span className="truncate text-sm font-semibold">
                                  {reviewerGroup.displayLabel}
                                </span>
                                <span className="shrink-0 text-xs text-muted-foreground">
                                  {reviewerGroup.sourceSystem}
                                </span>
                              </span>
                              <span className="truncate text-xs text-muted-foreground">
                                {reviewerGroup.title ||
                                  t("paper.rebuttal.untitledReview")}
                              </span>
                              {reviewerGroup.review && (
                                <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                                  {dateFormatter.format(
                                    new Date(reviewerGroup.review.created_time),
                                  )}
                                </span>
                              )}
                            </span>
                            <Badge
                              variant={
                                reviewerEntries.length > 0
                                  ? "secondary"
                                  : "outline"
                              }
                              className="shrink-0 text-xs font-normal"
                            >
                              {noResponseNeeded
                                ? t("paper.rebuttal.noResponseNeeded")
                                : t("paper.rebuttal.entryCount", {
                                    count: reviewerEntries.length,
                                  })}
                            </Badge>
                            <span className="shrink-0 text-xs font-medium text-muted-foreground">
                              {t(
                                expanded
                                  ? "paper.rebuttal.collapseReviewAction"
                                  : "paper.rebuttal.expandReviewAction",
                              )}
                            </span>
                            <ChevronDown
                              aria-hidden="true"
                              className={`size-4 shrink-0 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none ${
                                expanded ? "rotate-180" : ""
                              }`}
                            />
                          </button>
                          {expanded && (
                            <div
                              id={`review-content-${reviewerGroup.reviewerId}`}
                              className="flex w-full min-w-0 max-w-full flex-col overflow-hidden border-t bg-background/70"
                            >
                              <section className="min-w-0 max-w-full overflow-hidden border-b">
                                <div className="flex items-center justify-between gap-3 border-b bg-muted/10 px-5 py-4">
                                  <h4 className="font-serif text-base font-semibold text-foreground">
                                    {t("paper.rebuttal.originalReview")}
                                  </h4>
                                  {reviewerGroup.review && (
                                    <div className="flex shrink-0 items-center gap-1">
                                      <Button
                                        size="icon-sm"
                                        variant="ghost"
                                        className="text-destructive hover:text-destructive"
                                        title={t("common.delete")}
                                        aria-label={t("common.delete")}
                                        onClick={() =>
                                          setReviewToDelete(
                                            reviewerGroup.review,
                                          )
                                        }
                                      >
                                        <Trash2 />
                                      </Button>
                                      <Button
                                        size="icon-sm"
                                        variant="ghost"
                                        disabled={!reviewContent.data}
                                        title={t("common.edit")}
                                        aria-label={t("common.edit")}
                                        onClick={openEditEditor}
                                      >
                                        <Edit3 />
                                      </Button>
                                    </div>
                                  )}
                                </div>
                                <div className="min-w-0 max-w-full overflow-hidden px-5 py-6">
                                  {!reviewerGroup.review &&
                                  reviewerGroup.baselineSource
                                    ?.reviewMarkdown ? (
                                    <article className="prose min-w-0 max-w-full break-words whitespace-pre-wrap text-[15px] leading-7 text-foreground/90 dark:prose-invert [&_a]:break-all [&_img]:max-w-full [&_li]:leading-7 [&_p]:leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:whitespace-pre [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
                                      <ReactMarkdown
                                        remarkPlugins={[remarkGfm]}
                                      >
                                        {
                                          reviewerGroup.baselineSource
                                            .reviewMarkdown
                                        }
                                      </ReactMarkdown>
                                    </article>
                                  ) : !reviewerGroup.review ? (
                                    <div className="space-y-5">
                                      <div className="border-l-2 border-primary/35 pl-4">
                                        <p className="text-[15px] leading-7 text-muted-foreground">
                                          {t(
                                            "paper.rebuttal.baselineOnlySourceDescription",
                                          )}
                                        </p>
                                        {reviewerGroup.sourceUrl && (
                                          <a
                                            href={reviewerGroup.sourceUrl}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="mt-2 inline-flex text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                          >
                                            {t(
                                              "paper.rebuttal.viewReviewSource",
                                            )}
                                          </a>
                                        )}
                                      </div>
                                      {reviewerConcerns.length > 0 && (
                                        <ol className="divide-y border-y">
                                          {reviewerConcerns.map(
                                            (concern, concernIndex) => (
                                              <li
                                                key={concern.id}
                                                className="flex gap-4 py-4 text-[15px] leading-7"
                                              >
                                                <span className="w-7 shrink-0 font-serif text-base text-muted-foreground tabular-nums">
                                                  {String(
                                                    concernIndex + 1,
                                                  ).padStart(2, "0")}
                                                </span>
                                                <span className="min-w-0 break-words">
                                                  {concern.concern}
                                                </span>
                                              </li>
                                            ),
                                          )}
                                        </ol>
                                      )}
                                    </div>
                                  ) : reviewContent.isLoading ? (
                                    <div className="flex flex-col gap-3">
                                      <Skeleton className="h-4 w-full" />
                                      <Skeleton className="h-4 w-5/6" />
                                      <Skeleton className="h-4 w-2/3" />
                                    </div>
                                  ) : reviewContent.isError ? (
                                    <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
                                      {t("paper.rebuttal.reviewLoadFailed")}
                                    </div>
                                  ) : (
                                    <article className="prose min-w-0 max-w-full break-words whitespace-pre-wrap text-[15px] leading-7 text-foreground/90 dark:prose-invert [&_a]:break-all [&_img]:max-w-full [&_li]:leading-7 [&_p]:leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:whitespace-pre [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
                                      <ReactMarkdown
                                        remarkPlugins={[remarkGfm]}
                                      >
                                        {reviewContent.data?.content}
                                      </ReactMarkdown>
                                    </article>
                                  )}
                                </div>
                              </section>

                              <section className="min-w-0 max-w-full overflow-hidden bg-primary/[0.015]">
                                <div className="flex items-center justify-between gap-3 border-b bg-primary/[0.025] px-5 py-4">
                                  <h4 className="font-serif text-base font-semibold text-foreground">
                                    {t("paper.rebuttal.reviewerRebuttal")}
                                  </h4>
                                  <Badge
                                    variant="outline"
                                    className="text-xs font-normal"
                                  >
                                    {t("paper.rebuttal.entryCount", {
                                      count: reviewerEntries.length,
                                    })}
                                  </Badge>
                                </div>
                                <div className="flex min-w-0 max-w-full flex-col gap-4 overflow-hidden p-5">
                                  {reviewerEntries.length === 0 ? (
                                    <div className="rounded-lg border border-dashed bg-muted/15 px-4 py-4 text-center">
                                      <p className="text-sm font-medium">
                                        {t(
                                          noResponseNeeded
                                            ? "paper.rebuttal.noResponseNeededTitle"
                                            : "paper.rebuttal.noReviewerEntries",
                                        )}
                                      </p>
                                      <p className="mt-1 text-sm leading-6 text-muted-foreground">
                                        {t(
                                          noResponseNeeded
                                            ? "paper.rebuttal.noResponseNeededDescription"
                                            : "paper.rebuttal.noEntriesDescription",
                                        )}
                                      </p>
                                    </div>
                                  ) : (
                                    reviewerEntries.map((entry, entryIndex) => (
                                      <Card
                                        key={entry.id}
                                        className="min-w-0 max-w-full gap-0 overflow-hidden rounded-none border-l-2 border-l-primary/50 py-0 shadow-none"
                                      >
                                        <CardHeader className="relative min-w-0 gap-1.5 border-b bg-muted/15 px-4 py-3 pr-12">
                                          <CardTitle className="flex min-w-0 items-start gap-3 font-serif text-base leading-6">
                                            <span className="font-serif text-base text-muted-foreground">
                                              {String(entryIndex + 1).padStart(
                                                2,
                                                "0",
                                              )}
                                            </span>
                                            <span className="min-w-0 flex-1">
                                              {entry.title}
                                            </span>
                                          </CardTitle>
                                          <CardDescription className="flex flex-wrap items-center gap-2 text-xs">
                                            <Badge
                                              variant="outline"
                                              className="font-mono"
                                            >
                                              {entry.label}
                                            </Badge>
                                            <span className="min-w-0 break-all">
                                              {entry.concern_ids.join(" · ")}
                                            </span>
                                          </CardDescription>
                                          <Button
                                            size="icon-sm"
                                            variant="ghost"
                                            className="absolute top-2 right-2"
                                            title={t("paper.rebuttal.copy")}
                                            aria-label={t(
                                              "paper.rebuttal.copy",
                                            )}
                                            onClick={async () => {
                                              if (await copy(entry.response)) {
                                                toast.success(
                                                  t("paper.rebuttal.copied"),
                                                )
                                              } else {
                                                toast.error(
                                                  t(
                                                    "paper.rebuttal.copyFailed",
                                                  ),
                                                )
                                              }
                                            }}
                                          >
                                            {copiedText === entry.response ? (
                                              <Check />
                                            ) : (
                                              <Clipboard />
                                            )}
                                          </Button>
                                        </CardHeader>
                                        <CardContent className="px-5 py-5">
                                          <p className="break-words whitespace-pre-wrap text-[15px] leading-7 text-foreground/90 [overflow-wrap:anywhere]">
                                            {entry.response}
                                          </p>
                                          {(entry.source_refs?.length ?? 0) >
                                            0 && (
                                            <div className="mt-3 flex flex-wrap gap-1.5">
                                              {entry.source_refs?.map(
                                                (sourceRef) => (
                                                  <Badge
                                                    key={sourceRef}
                                                    variant="outline"
                                                    className="max-w-full truncate font-mono text-xs font-normal"
                                                  >
                                                    {sourceRef}
                                                  </Badge>
                                                ),
                                              )}
                                            </div>
                                          )}
                                        </CardContent>
                                        <CardFooter className="border-t bg-muted/10 px-4 py-2.5 text-xs text-muted-foreground">
                                          <Badge
                                            variant="secondary"
                                            className="font-normal"
                                          >
                                            {t(
                                              `paper.rebuttal.evidence.${entry.evidence_status}`,
                                            )}
                                          </Badge>
                                        </CardFooter>
                                      </Card>
                                    ))
                                  )}
                                </div>
                              </section>
                            </div>
                          )}
                        </Card>
                      )
                    })}
                  </div>
                </section>
              )}

              {reviewerGroups.length === 0 && (
                <section className="border border-dashed bg-muted/15 p-8 text-center">
                  <MessageSquarePlus className="mx-auto size-6 text-muted-foreground" />
                  <p className="mt-2 text-sm font-medium">
                    {t("paper.rebuttal.noReviewsTitle")}
                  </p>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    {t("paper.rebuttal.noReviewsDescription")}
                  </p>
                  <Button className="mt-3" size="sm" onClick={openCreateEditor}>
                    {t("paper.rebuttal.addFirstReview")}
                  </Button>
                </section>
              )}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent
          value="baseline"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <ScrollArea className="h-full min-h-0 min-w-0">
            <div className="w-full min-w-0 overflow-x-hidden p-3 sm:p-4">
              <div className="mb-3 flex justify-end">
                <Button size="sm" variant="outline" onClick={openCreateEditor}>
                  <MessageSquarePlus data-icon="inline-start" />
                  {t("paper.rebuttal.addReview")}
                </Button>
              </div>
              {baseline ? (
                <RebuttalBaselinePanel
                  baseline={baseline}
                  stale={baselineStale}
                />
              ) : (
                <section className="border border-dashed bg-muted/15 px-6 py-12 text-center">
                  <ScanSearch
                    aria-hidden="true"
                    className="mx-auto size-7 text-muted-foreground"
                  />
                  <h3 className="mt-3 text-sm font-semibold">
                    {t("paper.rebuttal.noBaselineTitle")}
                  </h3>
                  <p className="mx-auto mt-1.5 max-w-sm text-sm leading-6 text-muted-foreground">
                    {t("paper.rebuttal.noBaselineDescription")}
                  </p>
                </section>
              )}
            </div>
          </ScrollArea>
        </TabsContent>
        <TabsContent
          value="chair"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <ScrollArea className="h-full min-h-0 min-w-0">
            <div className="w-full min-w-0 overflow-x-hidden p-3 sm:p-4">
              {chairMessage ? (
                <section
                  data-testid="rebuttal-chair-message"
                  className="min-w-0 overflow-hidden border border-border/80 bg-card/70"
                >
                  <header className="flex min-w-0 flex-wrap items-start justify-between gap-3 border-b px-5 py-4 sm:px-6">
                    <div className="min-w-0 flex-1">
                      <h3 className="font-serif text-xl font-semibold text-pretty">
                        {t("paper.rebuttal.chair.title")}
                      </h3>
                      <p className="mt-1 text-sm leading-6 text-muted-foreground">
                        {t("paper.rebuttal.chair.description")}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        if (await copy(chairMessage)) {
                          toast.success(t("paper.rebuttal.copied"))
                        } else {
                          toast.error(t("paper.rebuttal.copyFailed"))
                        }
                      }}
                    >
                      {copiedText === chairMessage ? (
                        <Check aria-hidden="true" />
                      ) : (
                        <Clipboard aria-hidden="true" />
                      )}
                      {t("paper.rebuttal.chair.copy")}
                    </Button>
                  </header>
                  {chairStale && (
                    <p className="border-b border-amber-500/25 bg-amber-500/10 px-5 py-3 text-sm leading-6 text-amber-900 dark:text-amber-100">
                      {t("paper.rebuttal.chair.stale")}
                    </p>
                  )}
                  <article className="prose min-w-0 max-w-none break-words px-5 py-5 text-base leading-7 text-foreground/90 dark:prose-invert sm:px-6 [&_a]:break-all [&_li]:leading-7 [&_p]:leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {chairMessage}
                    </ReactMarkdown>
                  </article>
                </section>
              ) : (
                <section className="border border-dashed bg-muted/15 px-6 py-12 text-center">
                  <PenLine
                    aria-hidden="true"
                    className="mx-auto size-7 text-muted-foreground"
                  />
                  <h3 className="mt-3 font-serif text-lg font-semibold">
                    {t("paper.rebuttal.chair.emptyTitle")}
                  </h3>
                  <p className="mx-auto mt-1.5 max-w-md text-sm leading-6 text-muted-foreground">
                    {t("paper.rebuttal.chair.emptyDescription")}
                  </p>
                </section>
              )}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>

      <Dialog
        open={editorOpen}
        onOpenChange={(open) => {
          if (!open && editorPending) return
          setEditorOpen(open)
          if (!open) resetForm()
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {t(
                editorMode === "edit"
                  ? "paper.rebuttal.editReviewTitle"
                  : "paper.rebuttal.addReviewTitle",
              )}
            </DialogTitle>
            <DialogDescription>
              {t(
                editorMode === "edit"
                  ? "paper.rebuttal.editReviewDescription"
                  : "paper.rebuttal.addReviewDescription",
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-2">
              <Label htmlFor="rebuttal-reviewer">
                {t("paper.rebuttal.reviewerLabel")}
              </Label>
              <Input
                id="rebuttal-reviewer"
                value={reviewerLabel}
                onChange={(event) => setReviewerLabel(event.target.value)}
                placeholder={t("paper.rebuttal.reviewerPlaceholder")}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="rebuttal-review-title">
                {t("paper.rebuttal.reviewTitle")}
              </Label>
              <Input
                id="rebuttal-review-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="rebuttal-review-content">
                {t("paper.rebuttal.reviewContent")}
              </Label>
              <Textarea
                id="rebuttal-review-content"
                className="min-h-56 resize-y"
                value={content}
                onChange={(event) => setContent(event.target.value)}
                placeholder={t("paper.rebuttal.reviewContentPlaceholder")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={editorPending}
              onClick={() => setEditorOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              disabled={
                !reviewerLabel.trim() || !content.trim() || editorPending
              }
              onClick={() => void submitReview()}
            >
              {editorPending && <Loader2 className="animate-spin" />}
              {t(
                editorMode === "edit"
                  ? "paper.rebuttal.saveChanges"
                  : "paper.rebuttal.saveReview",
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={Boolean(reviewToDelete)}
        onOpenChange={(open) => {
          if (!open && !deleteReview.isPending) setReviewToDelete(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("paper.rebuttal.deleteReviewTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("paper.rebuttal.deleteReviewDescription", {
                reviewer: reviewToDelete?.reviewer_label,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteReview.isPending}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: "destructive" })}
              disabled={deleteReview.isPending}
              onClick={(event) => {
                event.preventDefault()
                void confirmDelete()
              }}
            >
              {deleteReview.isPending && <Loader2 className="animate-spin" />}
              {t("paper.rebuttal.deleteReviewConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
