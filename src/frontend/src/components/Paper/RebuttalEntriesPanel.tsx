import {
  Check,
  ChevronRight,
  Clipboard,
  Edit3,
  Eye,
  Loader2,
  MessageSquarePlus,
  MoreHorizontal,
  ShieldAlert,
  Trash2,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { toast } from "sonner"

import type { PaperReviewResponse, RebuttalEntry } from "@/client"
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
  CardAction,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  useAttachReviewerFeedback,
  useDeleteReviewerFeedback,
  usePaperReviewContent,
  useUpdateReviewerFeedback,
} from "@/hooks/usePapers"

type RebuttalEntriesPanelProps = {
  workspaceId: string
  sourceVersionId: string
  reviews: PaperReviewResponse[]
  entries: RebuttalEntry[]
  readyForSubmission: boolean
  stale: boolean
}

type ReviewEditorMode = "create" | "edit"

export default function RebuttalEntriesPanel({
  workspaceId,
  sourceVersionId,
  reviews,
  entries,
  readyForSubmission,
  stale,
}: RebuttalEntriesPanelProps) {
  const { t, i18n } = useTranslation()
  const [editorMode, setEditorMode] = useState<ReviewEditorMode>("create")
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingReviewId, setEditingReviewId] = useState<string | null>(null)
  const [detailReviewId, setDetailReviewId] = useState<string | null>(null)
  const [reviewToDelete, setReviewToDelete] =
    useState<PaperReviewResponse | null>(null)
  const [reviewerLabel, setReviewerLabel] = useState("")
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const attachReview = useAttachReviewerFeedback(workspaceId)
  const updateReview = useUpdateReviewerFeedback(workspaceId)
  const deleteReview = useDeleteReviewerFeedback(workspaceId)
  const detailReview = reviews.find((review) => review.id === detailReviewId)
  const reviewContent = usePaperReviewContent(detailReviewId)
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

  const openDetails = (review: PaperReviewResponse) => {
    setDetailReviewId(review.id)
  }

  const openEditEditor = () => {
    if (!detailReview || !reviewContent.data) return
    setEditorMode("edit")
    setEditingReviewId(detailReview.id)
    setReviewerLabel(detailReview.reviewer_label)
    setTitle(detailReview.title ?? "")
    setContent(reviewContent.data.content)
    setDetailReviewId(null)
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
      if (detailReviewId === reviewToDelete.id) setDetailReviewId(null)
      setReviewToDelete(null)
      toast.success(t("paper.rebuttal.reviewDeleted"))
    } catch {
      toast.error(t("paper.rebuttal.reviewDeleteFailed"))
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <div className="flex shrink-0 items-center gap-3 border-b px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">
              {t("paper.rebuttal.title")}
            </h2>
            {readyForSubmission && (
              <Badge variant="secondary" className="gap-1 text-[10px]">
                <Check className="size-3" />
                {t("paper.rebuttal.ready")}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("paper.rebuttal.reviewCount", { count: reviews.length })}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={openCreateEditor}>
          <MessageSquarePlus data-icon="inline-start" />
          {t("paper.rebuttal.addReview")}
        </Button>
      </div>

      {stale && entries.length > 0 && (
        <div className="flex shrink-0 gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-900 dark:text-amber-100">
          <ShieldAlert className="mt-0.5 size-4 shrink-0" />
          <span className="leading-5">{t("paper.rebuttal.stale")}</span>
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-6 p-4">
          {reviews.length > 0 && (
            <section className="flex flex-col gap-2.5">
              <div className="flex items-end justify-between gap-3 px-0.5">
                <div>
                  <h3 className="text-xs font-semibold tracking-wide">
                    {t("paper.rebuttal.attachedReviews")}
                  </h3>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {t("paper.rebuttal.reviewListHint")}
                  </p>
                </div>
                <Badge variant="outline" className="font-mono text-[10px]">
                  {String(reviews.length).padStart(2, "0")}
                </Badge>
              </div>
              <div className="flex flex-col gap-2">
                {reviews.map((review, index) => (
                  <Card
                    key={review.id}
                    className="gap-0 overflow-hidden py-0 shadow-none transition-colors hover:border-foreground/20"
                  >
                    <div className="grid min-w-0 grid-cols-[1fr_auto]">
                      <button
                        type="button"
                        className="group flex min-w-0 items-center gap-3 p-3 text-left outline-none transition-colors hover:bg-muted/35 focus-visible:bg-muted/50"
                        onClick={() => openDetails(review)}
                      >
                        <span className="grid size-9 shrink-0 place-items-center rounded-md border bg-muted/40 font-mono text-xs font-semibold text-muted-foreground">
                          R{index + 1}
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col gap-1">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-semibold">
                              {review.reviewer_label}
                            </span>
                            <span className="shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                              {review.source_system}
                            </span>
                          </span>
                          <span className="truncate text-xs text-muted-foreground">
                            {review.title || t("paper.rebuttal.untitledReview")}
                          </span>
                          <span className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                            <span>
                              {t("paper.rebuttal.baselineCount", {
                                count: review.baseline_dimensions.length,
                              })}
                            </span>
                            <span>
                              {dateFormatter.format(
                                new Date(review.created_time),
                              )}
                            </span>
                          </span>
                        </span>
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                      </button>
                      <div className="flex items-center border-l px-2">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              size="icon"
                              variant="ghost"
                              aria-label={t("paper.rebuttal.reviewActions")}
                            >
                              <MoreHorizontal />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuGroup>
                              <DropdownMenuItem
                                onSelect={() => openDetails(review)}
                              >
                                <Eye />
                                {t("paper.rebuttal.viewReview")}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                variant="destructive"
                                onSelect={() => setReviewToDelete(review)}
                              >
                                <Trash2 />
                                {t("common.delete")}
                              </DropdownMenuItem>
                            </DropdownMenuGroup>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            </section>
          )}

          {reviews.length === 0 && (
            <section className="rounded-xl border border-dashed bg-muted/20 p-5 text-center">
              <MessageSquarePlus className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">
                {t("paper.rebuttal.noReviewsTitle")}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t("paper.rebuttal.noReviewsDescription")}
              </p>
              <Button className="mt-3" size="sm" onClick={openCreateEditor}>
                {t("paper.rebuttal.addFirstReview")}
              </Button>
            </section>
          )}

          {reviews.length > 0 && entries.length === 0 && (
            <section className="rounded-xl border border-dashed bg-muted/20 p-5 text-center">
              <ShieldAlert className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">
                {t("paper.rebuttal.noEntriesTitle")}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t("paper.rebuttal.noEntriesDescription")}
              </p>
            </section>
          )}

          {Object.entries(groupedEntries).map(
            ([reviewerId, reviewerEntries]) => (
              <section key={reviewerId} className="flex flex-col gap-2.5">
                <div className="flex items-center justify-between gap-2 px-0.5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {reviewerId}
                  </h3>
                  <span className="text-[11px] text-muted-foreground">
                    {t("paper.rebuttal.entryCount", {
                      count: reviewerEntries.length,
                    })}
                  </span>
                </div>
                {reviewerEntries.map((entry, index) => (
                  <Card
                    key={entry.id}
                    className="gap-0 overflow-hidden border-l-2 border-l-primary/50 py-0 shadow-none"
                  >
                    <CardHeader className="gap-1.5 border-b bg-muted/15 px-4 py-3">
                      <CardTitle className="flex min-w-0 items-start gap-2 text-sm leading-5">
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <span className="min-w-0 flex-1">{entry.title}</span>
                      </CardTitle>
                      <CardDescription className="flex flex-wrap items-center gap-2 text-[11px]">
                        <Badge variant="outline" className="font-mono">
                          {entry.label}
                        </Badge>
                        <span>{entry.concern_ids.join(" · ")}</span>
                      </CardDescription>
                      <CardAction>
                        <Button
                          size="icon"
                          variant="ghost"
                          title={t("paper.rebuttal.copy")}
                          aria-label={t("paper.rebuttal.copy")}
                          onClick={() => {
                            void navigator.clipboard.writeText(entry.response)
                            toast.success(t("paper.rebuttal.copied"))
                          }}
                        >
                          <Clipboard />
                        </Button>
                      </CardAction>
                    </CardHeader>
                    <CardContent className="px-4 py-4">
                      <p className="whitespace-pre-wrap text-sm leading-6 text-foreground/90">
                        {entry.response}
                      </p>
                    </CardContent>
                    <CardFooter className="justify-between gap-2 border-t bg-muted/10 px-4 py-2.5 text-[11px] text-muted-foreground">
                      <Badge variant="secondary" className="font-normal">
                        {t(`paper.rebuttal.evidence.${entry.evidence_status}`)}
                      </Badge>
                      <span className="font-mono">
                        {t("paper.rebuttal.characters", {
                          count: entry.character_count,
                        })}
                      </span>
                    </CardFooter>
                  </Card>
                ))}
              </section>
            ),
          )}
        </div>
      </ScrollArea>

      <Dialog
        open={Boolean(detailReviewId)}
        onOpenChange={(open) => {
          if (!open) setDetailReviewId(null)
        }}
      >
        <DialogContent className="flex max-h-[min(82vh,760px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
          <DialogHeader className="border-b px-6 py-5 pr-12">
            <div className="flex items-center gap-2">
              <Badge variant="outline">{detailReview?.source_system}</Badge>
              <span className="text-xs text-muted-foreground">
                {detailReview &&
                  dateFormatter.format(new Date(detailReview.created_time))}
              </span>
            </div>
            <DialogTitle className="mt-2">
              {detailReview?.reviewer_label}
            </DialogTitle>
            <DialogDescription>
              {detailReview?.title || t("paper.rebuttal.untitledReview")}
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="min-h-0 flex-1">
            <div className="px-6 py-5">
              {reviewContent.isLoading ? (
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
                <article className="prose prose-sm max-w-none text-foreground/90 dark:prose-invert">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {reviewContent.data?.content}
                  </ReactMarkdown>
                </article>
              )}
            </div>
          </ScrollArea>
          <DialogFooter className="border-t bg-muted/15 px-6 py-4 sm:justify-between">
            <Button
              variant="ghost"
              className="text-destructive hover:text-destructive"
              disabled={!detailReview}
              onClick={() => detailReview && setReviewToDelete(detailReview)}
            >
              <Trash2 data-icon="inline-start" />
              {t("common.delete")}
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setDetailReviewId(null)}>
                {t("common.close")}
              </Button>
              <Button disabled={!reviewContent.data} onClick={openEditEditor}>
                <Edit3 data-icon="inline-start" />
                {t("common.edit")}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
