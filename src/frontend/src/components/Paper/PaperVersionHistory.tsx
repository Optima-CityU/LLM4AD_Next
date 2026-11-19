import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Check,
  Download,
  History,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  RotateCcw,
  X,
} from "lucide-react"
import { lazy, Suspense, useEffect, useId, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import type {
  PaperHistoryComparison,
  PaperHistoryFile,
  PaperHistoryResponse,
  PaperHistoryRevision,
  PaperRevisionCandidateResponse,
  PaperRevisionPatchResponse,
  PaperSourceVersionResponse,
} from "@/client"
import FileTreeView from "@/components/Evolution/TaskDetail/steps/FileTreeView"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { paperWorkspaceKeys } from "@/hooks/usePapers"
import { cn } from "@/lib/utils"
import {
  downloadHistoryFile,
  HistoryError,
  historyRequest,
  paperHistoryUrl,
  revisionFileUrl,
} from "./history/historyApi"
import {
  buildHistoryTree,
  initialHistoryPair,
  parseRevisionDiff,
} from "./history/historyHelpers"
import { useHistoryText } from "./history/historyMessages"

const FileViewer = lazy(() => import("./history/PaperHistoryFileViewer"))

export type PaperVersionHistoryProps = {
  workspaceId: string
  activeSource?: PaperSourceVersionResponse
  sourceVersions: PaperSourceVersionResponse[]
  candidates: PaperRevisionCandidateResponse[]
  onChanged: () => void
}

type Selection = {
  baseId: string
  targetId: string
  expectedHash: string
  expectedRevisionId: string | null
  sourceFingerprint: string
  revisions: PaperHistoryRevision[]
}
const pendingCandidate = (candidate: PaperRevisionCandidateResponse) =>
  ["draft", "pending"].includes(candidate.status) &&
  !candidate.accepted_source_version_id
const historyKey = (workspaceId: string) =>
  ["paper-workspace-history", workspaceId] as const
const candidateUrl = (id: string, action: string) =>
  `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/papers/revision-candidates/${encodeURIComponent(id)}/${action}`
const sourceFingerprint = (source?: PaperSourceVersionResponse) =>
  `${source?.id ?? ""}:${source?.content_hash ?? ""}`

/** Preserve the route's existing props while opening the complete history directly. */
export default function PaperVersionHistory(props: PaperVersionHistoryProps) {
  return <HistoryControl key={props.workspaceId} {...props} />
}

function HistoryControl(props: PaperVersionHistoryProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const pending = props.candidates.filter(
    (item) => item.workspace_id === props.workspaceId && pendingCandidate(item),
  ).length
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!busy) setOpen(next)
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="shrink-0">
          <History className="size-4" />
          {t("paper.history.title", { defaultValue: "Version history" })}
          {pending > 0 && (
            <Badge variant="secondary" className="ml-1 tabular-nums">
              {pending}
            </Badge>
          )}
        </Button>
      </DialogTrigger>
      {open && (
        <HistoryDialog
          {...props}
          busy={busy}
          setBusy={setBusy}
          close={() => setOpen(false)}
        />
      )}
    </Dialog>
  )
}

function HistoryDialog({
  workspaceId,
  activeSource,
  candidates,
  onChanged,
  busy,
  setBusy,
  close,
}: PaperVersionHistoryProps & {
  busy: boolean
  setBusy: (value: boolean) => void
  close: () => void
}) {
  const { t, i18n } = useTranslation()
  const text = useHistoryText()
  const client = useQueryClient()
  const selectId = useId()
  const [selection, setSelection] = useState<Selection | null>(null)
  const [candidateId, setCandidateId] = useState("")
  const [requestedPath, setRequestedPath] = useState("")
  const [changedOnly, setChangedOnly] = useState(true)
  const [treeOpen, setTreeOpen] = useState(
    () => window.matchMedia("(min-width: 768px)").matches,
  )
  const [view, setView] = useState<"diff" | "preview">("diff")
  const [error, setError] = useState<string | null>(null)
  const [forceStale, setForceStale] = useState(false)
  const [confirmRestore, setConfirmRestore] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const currentFingerprint = sourceFingerprint(
    activeSource?.workspace_id === workspaceId ? activeSource : undefined,
  )
  const history = useQuery({
    queryKey: historyKey(workspaceId),
    queryFn: ({ signal }) =>
      historyRequest<PaperHistoryResponse>(paperHistoryUrl(workspaceId), {
        signal,
      }),
    staleTime: 0,
    retry: false,
  })
  const capture = (data: PaperHistoryResponse, retainPair = false) => {
    const pair =
      retainPair &&
      selection &&
      data.revisions.some((item) => item.id === selection.baseId) &&
      data.revisions.some((item) => item.id === selection.targetId)
        ? selection
        : initialHistoryPair(data)
    setSelection({
      baseId: pair.baseId,
      targetId: pair.targetId,
      expectedHash: data.current_state_hash,
      expectedRevisionId: data.current_revision_id,
      sourceFingerprint: currentFingerprint,
      revisions: data.revisions,
    })
    setForceStale(false)
  }
  useEffect(() => {
    if (history.data && !selection && !history.isFetching) {
      const pair = initialHistoryPair(history.data)
      setSelection({
        ...pair,
        expectedHash: history.data.current_state_hash,
        expectedRevisionId: history.data.current_revision_id,
        sourceFingerprint: currentFingerprint,
        revisions: history.data.revisions,
      })
    }
  }, [history.data, history.isFetching, selection, currentFingerprint])
  const stale =
    forceStale ||
    Boolean(
      selection &&
        (currentFingerprint !== selection.sourceFingerprint ||
          (history.data &&
            (history.data.current_state_hash !== selection.expectedHash ||
              history.data.current_revision_id !==
                selection.expectedRevisionId))),
    )
  const revisions = selection?.revisions ?? []
  const base = revisions.find((item) => item.id === selection?.baseId)
  const target = revisions.find((item) => item.id === selection?.targetId)
  const pending = candidates.filter(
    (item) => item.workspace_id === workspaceId && pendingCandidate(item),
  )
  const candidate = pending.find((item) => item.id === candidateId)
  const patch = useQuery({
    queryKey: ["paper-history-candidate", workspaceId, candidateId],
    queryFn: async ({ signal }) =>
      parseRevisionDiff(
        (
          await historyRequest<PaperRevisionPatchResponse>(
            candidateUrl(candidateId, "patch"),
            { signal },
          )
        ).content,
      ),
    enabled: Boolean(candidate),
    retry: false,
  })
  const candidateStale = Boolean(
    candidate &&
      (!activeSource ||
        candidate.source_version_id !== activeSource.id ||
        (patch.data?.sourceVersionId &&
          patch.data.sourceVersionId !== activeSource.id) ||
        (patch.data?.contentHash &&
          patch.data.contentHash !== activeSource.content_hash)),
  )
  const comparison = useQuery({
    queryKey: [
      "paper-history-comparison",
      workspaceId,
      selection?.baseId,
      base?.state_hash,
      selection?.targetId,
      target?.state_hash,
    ],
    queryFn: ({ signal }) =>
      historyRequest<PaperHistoryComparison>(
        paperHistoryUrl(
          workspaceId,
          `/compare?${new URLSearchParams({ base_id: selection!.baseId, target_id: selection!.targetId })}`,
        ),
        { signal },
      ),
    enabled: Boolean(selection?.baseId && selection.targetId && !candidate),
    retry: false,
  })
  const entries = useMemo<PaperHistoryComparison["files"]>(
    () =>
      candidate
        ? (patch.data?.files ?? []).map((file) => ({
            path: file.path,
            status: (
              file.binary
                ? file.originalBytes === 0
                : file.original === null
            )
              ? "added"
              : (
                    file.binary
                      ? file.replacementBytes === 0
                      : file.replacement === null
                  )
                ? "deleted"
                : "modified",
            mime_type: file.binary
              ? "application/octet-stream"
              : /\.(md|markdown)$/i.test(file.path)
                ? "text/markdown"
                : "text/plain",
            binary: file.binary,
            before_size:
              file.originalBytes ??
              (file.original === null
                ? 0
                : new TextEncoder().encode(file.original).length),
            after_size:
              file.replacementBytes ??
              (file.replacement === null
                ? 0
                : new TextEncoder().encode(file.replacement).length),
          }))
        : (comparison.data?.files ?? []),
    [candidate, patch.data, comparison.data],
  )
  const shownEntries = entries.filter(
    (item) => !changedOnly || item.status !== "unchanged",
  )
  const path = shownEntries.some((item) => item.path === requestedPath)
    ? requestedPath
    : ((
        shownEntries.find((item) => item.status === "modified") ??
        shownEntries[0]
      )?.path ?? "")
  const entry = entries.find((item) => item.path === path)
  const candidateFile = patch.data?.files.find((item) => item.path === path)
  const tree = useMemo(() => buildHistoryTree(shownEntries), [shownEntries])
  const before = useQuery({
    queryKey: [
      "paper-history-file",
      workspaceId,
      base?.id,
      base?.state_hash,
      path,
    ],
    queryFn: ({ signal }) =>
      historyRequest<PaperHistoryFile>(
        revisionFileUrl(workspaceId, base!.id, path),
        { signal },
      ),
    enabled: Boolean(!candidate && base && path && entry?.status !== "added"),
    retry: false,
  })
  const after = useQuery({
    queryKey: [
      "paper-history-file",
      workspaceId,
      target?.id,
      target?.state_hash,
      path,
    ],
    queryFn: ({ signal }) =>
      historyRequest<PaperHistoryFile>(
        revisionFileUrl(workspaceId, target!.id, path),
        { signal },
      ),
    enabled: Boolean(
      !candidate && target && path && entry?.status !== "deleted",
    ),
    retry: false,
  })
  const statuses = Object.fromEntries(
    entries.map((item) => [
      item.path,
      {
        label:
          item.status === "unchanged"
            ? text("unchanged")
            : t(`paper.history.${item.status}`, { defaultValue: item.status }),
        marker: { added: "A", modified: "M", deleted: "D", unchanged: "=" }[
          item.status
        ],
        className: {
          added: "text-emerald-700 dark:text-emerald-400",
          modified: "text-amber-700 dark:text-amber-400",
          deleted: "text-rose-700 dark:text-rose-400",
          unchanged: "text-muted-foreground",
        }[item.status],
      },
    ]),
  )
  const refresh = async () => {
    setError(null)
    const result = await history.refetch()
    if (result.data && !result.error) {
      capture(result.data, true)
      await client.invalidateQueries({
        queryKey: ["paper-history-comparison", workspaceId],
      })
      await client.invalidateQueries({
        queryKey: ["paper-history-file", workspaceId],
      })
      if (candidateId) await patch.refetch()
    } else setError(text("historyLoadFailed"))
  }
  const finish = () => {
    void client.invalidateQueries({ queryKey: historyKey(workspaceId) })
    void client.invalidateQueries({
      queryKey: paperWorkspaceKeys.detail(workspaceId),
    })
    void client.invalidateQueries({ queryKey: ["paper-regions", workspaceId] })
    void client.invalidateQueries({ queryKey: ["paper-source-file"] })
    onChanged()
    close()
  }
  const restoreRevision =
    target?.id === selection?.expectedRevisionId ? base : target
  const decide = async (action: "restore" | "accept" | "reject") => {
    if (
      busy ||
      !selection ||
      (action !== "reject" && (stale || candidateStale))
    )
      return
    setBusy(true)
    setError(null)
    try {
      if (action !== "reject") {
        const latest = await historyRequest<PaperHistoryResponse>(
          paperHistoryUrl(workspaceId),
        )
        if (
          latest.current_state_hash !== selection.expectedHash ||
          latest.current_revision_id !== selection.expectedRevisionId
        )
          throw new HistoryError(409)
      }
      const url =
        action === "restore"
          ? paperHistoryUrl(
              workspaceId,
              `/${encodeURIComponent(restoreRevision!.id)}/restore`,
            )
          : candidateUrl(candidateId, action)
      await historyRequest<unknown>(url, {
        method: "POST",
        ...(action === "restore"
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                expected_state_hash: selection.expectedHash,
                expected_revision_id: selection.expectedRevisionId,
              }),
            }
          : {}),
      })
      finish()
    } catch (failure) {
      if (failure instanceof HistoryError && failure.status === 409) {
        setForceStale(true)
        setConfirmRestore(false)
        setError(text("workspaceChanged"))
      } else setError(text("actionFailed"))
    } finally {
      setBusy(false)
    }
  }
  const download = async (revision: PaperHistoryRevision) => {
    if (downloading || !path) return
    setDownloading(true)
    setError(null)
    try {
      await downloadHistoryFile(workspaceId, revision.id, path)
    } catch {
      setError(text("downloadFailed"))
    } finally {
      setDownloading(false)
    }
  }
  const loading = candidate ? patch.isPending : comparison.isPending
  const failed = candidate ? patch.isError : comparison.isError
  const fileLoading =
    !candidate &&
    path &&
    ((entry?.status !== "added" && before.isPending) ||
      (entry?.status !== "deleted" && after.isPending))
  const fileFailed =
    !candidate &&
    ((entry?.status !== "added" && before.isError) ||
      (entry?.status !== "deleted" && after.isError))
  const beforeFile =
    !candidate && entry?.status !== "added" ? before.data : undefined
  const afterFile =
    !candidate && entry?.status !== "deleted" ? after.data : undefined
  const original = candidate
    ? (candidateFile?.original ?? null)
    : (beforeFile?.content ?? null)
  const modified = candidate
    ? (candidateFile?.replacement ?? null)
    : (afterFile?.content ?? null)
  const chooseVersion = (field: "baseId" | "targetId", value: string) => {
    setSelection((current) => current && { ...current, [field]: value })
    setCandidateId("")
    setRequestedPath("")
    setError(null)
  }
  const date = (value: string) => {
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime())
      ? ""
      : new Intl.DateTimeFormat(i18n.language, {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(parsed)
  }
  return (
    <DialogContent
      className="flex h-[90dvh] w-[94vw] max-w-[1600px] min-h-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-[1600px]"
      preventOutsideClose={busy}
      showCloseButton={!busy}
      data-testid="paper-version-history"
    >
      <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12 text-left">
        <div className="flex items-center gap-3">
          <History className="size-5 text-muted-foreground" />
          <DialogTitle className="font-serif text-xl">
            {t("paper.history.title", { defaultValue: "Version history" })}
          </DialogTitle>
          <Badge variant="outline" className="hidden sm:inline-flex">
            {text("allOutputs")}
          </Badge>
        </div>
        <DialogDescription>{text("workspaceHint")}</DialogDescription>
      </DialogHeader>
      <div className="grid shrink-0 grid-cols-2 gap-3 border-b bg-muted/20 px-5 py-3 lg:grid-cols-[1fr_1fr_auto_auto]">
        {(["baseId", "targetId"] as const).map((field) => (
          <label
            key={field}
            className="min-w-0 space-y-1 text-xs font-medium text-muted-foreground"
            htmlFor={`${selectId}-${field}`}
          >
            <span>
              {text(field === "baseId" ? "baselineLabel" : "targetLabel")}
            </span>
            <select
              id={`${selectId}-${field}`}
              className="h-9 w-full min-w-0 truncate rounded-md border bg-background px-2 text-sm text-foreground"
              value={selection?.[field] ?? ""}
              disabled={!selection || busy}
              onChange={(event) => chooseVersion(field, event.target.value)}
            >
              {revisions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                  {item.is_current ? ` · ${text("currentLabel")}` : ""} ·{" "}
                  {date(item.created_time)}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label
          className="flex min-w-0 items-end"
          htmlFor={`${selectId}-candidate`}
        >
          <span className="sr-only">{text("pendingSuggestions")}</span>
          <select
            id={`${selectId}-candidate`}
            aria-label={text("pendingSuggestions")}
            className="h-9 w-full min-w-0 max-w-72 truncate rounded-md border bg-background px-2 text-sm"
            value={candidateId}
            disabled={!pending.length || busy}
            onChange={(event) => {
              setCandidateId(event.target.value)
              setRequestedPath("")
              setError(null)
            }}
          >
            <option value="">
              {text("pendingSuggestions")} ({pending.length})
            </option>
            {pending.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="outline"
          size="sm"
          className="self-end"
          disabled={busy || history.isFetching}
          onClick={() => void refresh()}
        >
          <RefreshCw
            className={cn("size-3.5", history.isFetching && "animate-spin")}
          />
          {text("refresh")}
        </Button>
      </div>
      {stale && (
        <div
          role="alert"
          className="flex shrink-0 items-center justify-between gap-3 border-b border-amber-500/30 bg-amber-500/10 px-5 py-2 text-sm text-amber-900 dark:text-amber-100"
        >
          <p>{text("workspaceChanged")}</p>
          <Button
            variant="outline"
            size="sm"
            disabled={busy || history.isFetching}
            onClick={() => void refresh()}
          >
            {text("refresh")}
          </Button>
        </div>
      )}
      {error && (
        <p
          role="alert"
          className="shrink-0 border-b bg-destructive/5 px-5 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      {history.isPending || (!selection && history.isFetching) ? (
        <div
          role="status"
          className="grid flex-1 place-items-center text-sm text-muted-foreground"
        >
          {text("rendering")}
        </div>
      ) : history.isError ? (
        <div role="alert" className="grid flex-1 place-items-center p-5">
          <div className="space-y-3 text-center">
            <p>{text("historyLoadFailed")}</p>
            <Button variant="outline" onClick={() => void refresh()}>
              {text("retry")}
            </Button>
          </div>
        </div>
      ) : !revisions.length ? (
        <p className="p-5 text-sm text-muted-foreground">
          {text("emptyHistory")}
        </p>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-3 py-2 text-xs">
            <div className="flex min-w-0 items-center gap-2">
              <Button
                size="icon"
                variant="ghost"
                className="size-7"
                aria-label={text(treeOpen ? "hideFiles" : "showFiles")}
                aria-expanded={treeOpen}
                onClick={() => setTreeOpen((value) => !value)}
              >
                {treeOpen ? (
                  <PanelLeftClose className="size-4" />
                ) : (
                  <PanelLeftOpen className="size-4" />
                )}
              </Button>
              <span className="truncate font-medium">
                {candidate ? candidate.title : text("historyTab")}
              </span>
              {candidate && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setCandidateId("")
                    setRequestedPath("")
                  }}
                >
                  {text("historyTab")}
                </Button>
              )}
            </div>
            <span className="text-muted-foreground" aria-live="polite">
              {text("summaryCounts", {
                added: entries.filter((item) => item.status === "added").length,
                modified: entries.filter((item) => item.status === "modified")
                  .length,
                deleted: entries.filter((item) => item.status === "deleted")
                  .length,
              })}
            </span>
          </div>
          <div className="flex min-h-0 flex-1 overflow-hidden">
            {treeOpen && (
              <aside
                className="flex w-64 max-w-[55vw] shrink-0 flex-col border-r bg-muted/10"
                aria-label={text("filesLabel")}
              >
                <div className="space-y-3 border-b p-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {text("filesLabel")}{" "}
                    <span className="ml-1 font-normal tabular-nums">
                      {shownEntries.length}
                    </span>
                  </p>
                  <label className="flex cursor-pointer items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={changedOnly}
                      onChange={(event) => setChangedOnly(event.target.checked)}
                      className="accent-primary"
                    />
                    {text("changedOnly")}
                  </label>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-2">
                  {!shownEntries.length && !loading ? (
                    <p className="p-2 text-xs leading-6 text-muted-foreground">
                      {text("emptyComparison")}
                    </p>
                  ) : (
                    <FileTreeView
                      tree={tree}
                      selectedPath={path}
                      onSelectFile={(next, isDir) => {
                        if (!isDir) setRequestedPath(next)
                      }}
                      fileStatuses={statuses}
                    />
                  )}
                </div>
              </aside>
            )}
            <main
              className="flex min-h-0 min-w-0 flex-1 flex-col"
              aria-label={text("reviewTab")}
            >
              {candidateStale && (
                <p
                  role="alert"
                  className="border-b bg-amber-500/10 px-4 py-2 text-sm"
                >
                  {text("candidateConflict")}
                </p>
              )}
              {candidate?.summary && (
                <p className="shrink-0 border-b px-4 py-2 text-sm text-muted-foreground">
                  {candidate.summary}
                </p>
              )}
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-muted/10 px-4 py-2">
                <p className="min-w-0 truncate font-mono text-xs" title={path}>
                  {path || text("noFile")}
                </p>
                <div className="flex shrink-0 items-center gap-1">
                  {entry && (
                    <Badge variant="outline">{statuses[path]?.label}</Badge>
                  )}
                  <div
                    className="ml-1 inline-flex rounded-md border bg-background p-0.5"
                    role="group"
                    aria-label={text("preview")}
                  >
                    {(["diff", "preview"] as const).map((mode) => (
                      <Button
                        key={mode}
                        variant={view === mode ? "secondary" : "ghost"}
                        size="sm"
                        className="h-7 px-2 text-xs"
                        aria-pressed={view === mode}
                        onClick={() => setView(mode)}
                      >
                        {text(mode)}
                      </Button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                {loading || fileLoading ? (
                  <p
                    role="status"
                    className="p-5 text-sm text-muted-foreground"
                  >
                    {text("rendering")}
                  </p>
                ) : failed || fileFailed ? (
                  <div role="alert" className="space-y-3 p-5 text-sm">
                    <p>
                      {text(
                        candidate ? "candidateLoadFailed" : "fileLoadFailed",
                      )}
                    </p>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        if (candidate) void patch.refetch()
                        else {
                          if (comparison.isError) void comparison.refetch()
                          else {
                            if (entry?.status !== "added") void before.refetch()
                            if (entry?.status !== "deleted")
                              void after.refetch()
                          }
                        }
                      }}
                    >
                      {text("retry")}
                    </Button>
                  </div>
                ) : path && entry ? (
                  <Suspense
                    fallback={
                      <p role="status" className="p-5 text-sm">
                        {text("rendering")}
                      </p>
                    }
                  >
                    <FileViewer
                      key={`${candidateId}:${base?.id}:${target?.id}:${path}`}
                      workspaceId={workspaceId}
                      path={path}
                      original={original}
                      modified={modified}
                      binary={entry.binary}
                      mimeType={entry.mime_type}
                      view={view}
                      before={
                        base && beforeFile
                          ? { revision: base, file: beforeFile }
                          : undefined
                      }
                      after={
                        target && afterFile
                          ? { revision: target, file: afterFile }
                          : undefined
                      }
                      beforeSize={entry.before_size ?? undefined}
                      afterSize={entry.after_size ?? undefined}
                    />
                  </Suspense>
                ) : (
                  <p className="p-5 text-sm text-muted-foreground">
                    {text("noFile")}
                  </p>
                )}
              </div>
            </main>
          </div>
          <DialogFooter className="shrink-0 flex-row flex-wrap items-center justify-between gap-2 border-t px-4 py-3 sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              {!candidate && path && (
                <>
                  {base && beforeFile?.exists && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={downloading || busy}
                      onClick={() => void download(base)}
                    >
                      <Download className="size-3.5" />
                      {text("downloadBefore")}
                    </Button>
                  )}
                  {target && afterFile?.exists && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={downloading || busy}
                      onClick={() => void download(target)}
                    >
                      <Download className="size-3.5" />
                      {text("downloadAfter")}
                    </Button>
                  )}
                </>
              )}
            </div>
            {candidate ? (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void decide("reject")}
                >
                  <X className="size-3.5" />
                  {text("reject")}
                </Button>
                <Button
                  size="sm"
                  disabled={
                    busy ||
                    !selection ||
                    stale ||
                    candidateStale ||
                    !patch.data ||
                    patch.isFetching ||
                    patch.isError
                  }
                  onClick={() => void decide("accept")}
                >
                  {busy ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Check className="size-3.5" />
                  )}
                  {text("approve")}
                </Button>
              </div>
            ) : (
              <Button
                size="sm"
                disabled={
                  busy ||
                  stale ||
                  !restoreRevision ||
                  restoreRevision.id === selection?.expectedRevisionId ||
                  comparison.isError ||
                  comparison.isFetching
                }
                onClick={() => setConfirmRestore(true)}
              >
                <RotateCcw className="size-3.5" />
                {text("restoreVersion", {
                  label: restoreRevision?.label ?? "",
                })}
              </Button>
            )}
          </DialogFooter>
        </>
      )}
      <Dialog
        open={confirmRestore}
        onOpenChange={(next) => {
          if (!busy) setConfirmRestore(next)
        }}
      >
        <DialogContent preventOutsideClose={busy} showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>
              {text("restoreConfirmTitle", {
                label: restoreRevision?.label ?? "",
              })}
            </DialogTitle>
            <DialogDescription>{text("restoreConfirmHint")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setConfirmRestore(false)}
            >
              {text("cancel")}
            </Button>
            <Button
              disabled={busy || stale}
              onClick={() => void decide("restore")}
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              {text("confirmRestore")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DialogContent>
  )
}
