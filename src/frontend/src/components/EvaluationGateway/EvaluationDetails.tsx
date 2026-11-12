import { useQuery } from "@tanstack/react-query"
import { lazy, Suspense, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import {
  type EvaluationFile,
  type EvaluationSummary,
  Llm4AdEvaluationsService,
} from "@/client"
import FileTreeView from "@/components/Evolution/TaskDetail/steps/FileTreeView"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useEvolution } from "@/hooks/useEvolution"
import {
  evaluationFileTree,
  evaluationNeedsPolling,
  evaluationPreviewKind,
  formatEvaluationBytes,
} from "@/lib/evaluationArtifacts"
import { cn, formatDateTime } from "@/lib/utils"

const ArtifactCodeEditor = lazy(
  () => import("@/components/AutoResearch/ArtifactCodeEditor"),
)

function CodePreview({ filePath, value }: { filePath: string; value: string }) {
  const { t } = useTranslation()
  return (
    <Suspense
      fallback={
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t("evaluationData.loading")}
        </p>
      }
    >
      <ArtifactCodeEditor filePath={filePath} value={value} readOnly />
    </Suspense>
  )
}

function StateBadge({ summary }: { summary: EvaluationSummary }) {
  const { t } = useTranslation()
  return (
    <Badge variant={summary.state === "failed" ? "destructive" : "outline"}>
      {t(`evaluationData.states.${summary.state}`, {
        defaultValue: summary.state,
      })}
    </Badge>
  )
}

function FilePreview({
  taskId,
  evaluationId,
  file,
  saved,
}: {
  taskId: string
  evaluationId: string
  file: EvaluationFile
  saved: boolean
}) {
  const { t } = useTranslation()
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [imageFailed, setImageFailed] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const kind = evaluationPreviewKind(file)
  const query = useQuery({
    queryKey: ["evaluationFile", taskId, evaluationId, file.id],
    queryFn: async () => {
      const data = await Llm4AdEvaluationsService.evaluationArtifact({
        taskId,
        evaluationId,
        artifactId: file.id,
        download: false,
      })
      const blob = data instanceof Blob ? data : new Blob([data as BlobPart])
      return kind === "text" ? await blob.text() : blob
    },
    enabled: saved && kind !== null,
    retry: false,
    gcTime: 0,
  })
  useEffect(() => {
    if (!(query.data instanceof Blob)) {
      setImageUrl(null)
      return
    }
    const url = URL.createObjectURL(query.data)
    setImageFailed(false)
    setImageUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [query.data])

  const download = async () => {
    setDownloading(true)
    try {
      const data = await Llm4AdEvaluationsService.evaluationArtifact({
        taskId,
        evaluationId,
        artifactId: file.id,
      })
      const blob = data instanceof Blob ? data : new Blob([data as BlobPart])
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = file.name.split("/").pop() ?? file.name
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      toast.error(t("evaluationData.downloadFailed"))
    } finally {
      setDownloading(false)
    }
  }
  const text = typeof query.data === "string" ? query.data : ""
  const markdown = /\.(md|markdown)$/i.test(file.name)
  return (
    <section className="min-w-0 overflow-hidden rounded-lg border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b p-3">
        <div className="min-w-0 basis-48 grow">
          <p className="break-all text-sm font-medium">{file.name}</p>
          <p className="text-xs text-muted-foreground">
            {formatEvaluationBytes(file.size)}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={download}
          disabled={!saved || downloading}
        >
          {t(
            downloading
              ? "evaluationData.downloading"
              : "evaluationData.download",
          )}
        </Button>
      </div>
      {file.description && (
        <p className="px-4 pt-3 text-sm leading-relaxed text-muted-foreground">
          {file.description}
        </p>
      )}
      {!saved ? (
        <p className="p-5 text-sm text-muted-foreground">
          {t("evaluationData.pendingFile")}
        </p>
      ) : !kind ? (
        <p className="p-5 text-sm text-muted-foreground">
          {t("evaluationData.downloadOnly")}
        </p>
      ) : query.isPending ? (
        <p role="status" className="p-5 text-sm">
          {t("evaluationData.loading")}
        </p>
      ) : query.isError ? (
        <p role="alert" className="p-5 text-sm text-muted-foreground">
          {t("evaluationData.previewUnavailable")}
        </p>
      ) : imageFailed ? (
        <p role="alert" className="p-5 text-sm text-muted-foreground">
          {t("evaluationData.previewUnavailable")}
        </p>
      ) : imageUrl ? (
        <div className="flex min-h-64 items-center justify-center p-4">
          <img
            src={imageUrl}
            alt={file.description || file.name}
            className="max-h-[60vh] max-w-full object-contain"
          />
        </div>
      ) : markdown ? (
        <div className="max-h-[60vh] overflow-auto break-words p-5 text-sm leading-relaxed">
          <Markdown
            remarkPlugins={MARKDOWN_REMARK_PLUGINS}
            rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
            components={makeMarkdownComponents(
              `evaluation-${evaluationId}-${file.id}`,
            )}
          >
            {text}
          </Markdown>
        </div>
      ) : (
        <div className="h-[420px] min-w-0">
          <CodePreview filePath={file.name} value={text} />
        </div>
      )}
    </section>
  )
}

function Measurements({ result }: { result: Record<string, unknown> }) {
  const { t } = useTranslation()
  const groups = ["metrics", "monitor_metrics"] as const
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("evaluationData.metric")}</TableHead>
          <TableHead>{t("evaluationData.value")}</TableHead>
          <TableHead>{t("evaluationData.kind")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.flatMap((group) => {
          const values = result[group]
          return values && typeof values === "object"
            ? Object.entries(values).map(([name, value]) => (
                <TableRow key={`${group}:${name}`}>
                  <TableCell className="break-all font-medium">
                    {name}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {String(value)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {t(`evaluationData.${group}`)}
                  </TableCell>
                </TableRow>
              ))
            : []
        })}
      </TableBody>
    </Table>
  )
}

function RecordDetail({
  taskId,
  summary,
  active,
  storageAvailable,
}: {
  taskId: string
  summary: EvaluationSummary
  active: boolean
  storageAvailable: boolean
}) {
  const { t } = useTranslation()
  const [fileId, setFileId] = useState<string | null>(null)
  const [detailTab, setDetailTab] = useState("results")
  const [extraOpen, setExtraOpen] = useState(false)
  const query = useQuery({
    queryKey: [
      "evaluationDetail",
      taskId,
      summary.id,
      summary.state,
      summary.storage_state,
      summary.transfer_pending,
      storageAvailable,
    ],
    queryFn: () =>
      Llm4AdEvaluationsService.evaluationDetail({
        taskId,
        evaluationId: summary.id,
      }),
    refetchInterval:
      active || evaluationNeedsPolling(summary) || !storageAvailable
        ? 3000
        : false,
    retry: 1,
  })
  const record = query.data?.record
  const result = record?.result ?? {}
  const files = record?.artifacts ?? []
  const file = files.find((item) => item.id === fileId) ?? files[0]
  const tree = useMemo(() => evaluationFileTree(files), [files])
  const metadata = Object.fromEntries(
    Object.entries(result).filter(
      ([name]) =>
        ![
          "score",
          "metrics",
          "monitor_metrics",
          "success",
          "error_message",
          "duration_ms",
          "observation",
        ].includes(name),
    ),
  )
  const progress = summary.progress ?? {}
  const total = Number(progress.total ?? 0)
  const completed = Number(progress.completed ?? 0)
  return (
    <div className="@container/record min-w-0 space-y-5 p-4">
      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="break-words text-lg font-semibold">
              {summary.algorithm_name || t("evaluationData.unnamed")}
            </h3>
            <p className="mt-1 break-all text-sm text-muted-foreground">
              {formatDateTime(summary.created_at)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">
              {t(`evaluationData.profiles.${summary.evaluation_profile}`)}
            </Badge>
            <StateBadge summary={summary} />
          </div>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
          <span>{t(`evaluationData.origins.${summary.origin}`)}</span>
          {summary.instance_id && (
            <span>
              {t("evaluationData.instance")}: {summary.instance_id}
            </span>
          )}
          {summary.source_fingerprint && (
            <span title={summary.source_fingerprint}>
              {t("evaluationData.source")}:{" "}
              <code>{summary.source_fingerprint.slice(0, 12)}</code>
            </span>
          )}
          {summary.gateway_name && (
            <span>
              {summary.gateway_name}
              {summary.gateway_online === false &&
                ` · ${t("evaluationData.states.reconnecting")}`}
            </span>
          )}
        </div>
        {typeof progress.message === "string" && (
          <p className="break-words text-sm leading-relaxed">
            {progress.message}
          </p>
        )}
        {total > 0 && Number.isFinite(completed) && (
          <div className="flex items-center gap-3">
            <progress
              className="h-2 w-full accent-primary"
              value={Math.max(0, Math.min(total, completed))}
              max={total}
              aria-label={t("evaluationData.progress")}
            />
            <span className="shrink-0 text-sm tabular-nums">
              {completed}/{total}
            </span>
          </div>
        )}
      </header>
      {query.isPending && <p role="status">{t("evaluationData.loading")}</p>}
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("evaluationData.loadFailed")}
        </p>
      )}
      {record && (
        <>
          {record.storage_state !== "saved" && (
            <div
              className="rounded-md border p-3 text-sm leading-relaxed text-muted-foreground"
              role="status"
            >
              {t(
                record.storage_state === "error"
                  ? "evaluationData.storageError"
                  : record.storage_state === "offline"
                    ? "evaluationData.storageOffline"
                    : record.storage_state === "unavailable"
                      ? "evaluationData.storageUnavailable"
                      : "evaluationData.storagePending",
              )}
              {record.storage_errors?.map((error, index) => (
                <p className="mt-2 break-words" key={`${index}:${error}`}>
                  {error}
                </p>
              ))}
            </div>
          )}
          <Tabs
            value={detailTab}
            onValueChange={setDetailTab}
            className="min-w-0"
          >
            <TabsList>
              <TabsTrigger value="results">
                {t("evaluationData.results")}
              </TabsTrigger>
              <TabsTrigger value="files">
                {t("evaluationData.files", { count: files.length })}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="results" className="space-y-5 pt-3">
              {typeof result.error_message === "string" && (
                <p className="rounded-md border border-destructive/30 p-3 text-sm leading-relaxed text-destructive">
                  {result.error_message}
                </p>
              )}
              {typeof result.score === "number" && result.success !== false && (
                <p className="text-sm text-muted-foreground">
                  {t("evaluationData.score")}{" "}
                  <strong className="ml-2 text-xl font-semibold tabular-nums text-foreground">
                    {result.score}
                  </strong>
                </p>
              )}
              <Measurements result={result} />
              {typeof result.observation === "string" && (
                <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                  {result.observation}
                </p>
              )}
              {Object.values(metadata).some(
                (value) =>
                  value !== null &&
                  (typeof value !== "object" || Object.keys(value).length > 0),
              ) && (
                <details
                  open={extraOpen}
                  onToggle={(event) => setExtraOpen(event.currentTarget.open)}
                  className="rounded-lg border p-3"
                >
                  <summary className="cursor-pointer text-sm font-medium">
                    {t("evaluationData.metadata")}
                  </summary>
                  <div className="mt-3 h-80 min-w-0">
                    {extraOpen && (
                      <CodePreview
                        filePath="metadata.json"
                        value={JSON.stringify(metadata, null, 2)}
                      />
                    )}
                  </div>
                </details>
              )}
            </TabsContent>
            <TabsContent value="files" className="pt-3">
              {file ? (
                <div className="grid min-w-0 gap-4 @min-[760px]/record:grid-cols-[210px_minmax(0,1fr)]">
                  <div className="min-w-0 overflow-auto rounded-lg border p-2 @min-[760px]/record:max-h-[550px]">
                    <FileTreeView
                      tree={tree}
                      selectedPath={file.name}
                      sizeMap={Object.fromEntries(
                        files.map((item) => [item.name, item.size]),
                      )}
                      onSelectFile={(path, isDir) => {
                        if (!isDir)
                          setFileId(
                            files.find((item) => item.name === path)?.id ??
                              null,
                          )
                      }}
                    />
                  </div>
                  <FilePreview
                    key={`${record.id}:${file.id}`}
                    taskId={taskId}
                    evaluationId={record.id}
                    file={file}
                    saved={
                      query.data?.available_artifact_ids?.includes(file.id) ??
                      false
                    }
                  />
                </div>
              ) : (
                <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                  {t("evaluationData.noFiles")}
                </p>
              )}
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  )
}

export default function EvaluationDetails({
  taskId,
  active,
}: {
  taskId: string
  active: boolean
}) {
  const { t } = useTranslation()
  const { selectedNodes, evolutionData } = useEvolution()
  const [candidate, setCandidate] = useState("all")
  const [profile, setProfile] = useState("all")
  const [page, setPage] = useState(1)
  const [recordId, setRecordId] = useState<string | null>(null)
  const selectedId = selectedNodes.length === 1 ? selectedNodes[0].id : null
  const candidates = useMemo(
    () => new Map(evolutionData.nodes.map((node) => [node.id, node.name])),
    [evolutionData.nodes],
  )
  const linkedCandidate =
    selectedId && candidates.has(selectedId) ? selectedId : "all"
  useEffect(() => {
    setCandidate(linkedCandidate)
    setPage(1)
    setRecordId(null)
  }, [linkedCandidate])
  const query = useQuery({
    queryKey: ["taskEvaluations", taskId, candidate, profile, page],
    queryFn: () =>
      Llm4AdEvaluationsService.listEvaluations({
        taskId,
        algorithmId: candidate === "all" ? undefined : candidate,
        evaluationProfile: profile === "all" ? undefined : profile,
        page,
        pageSize: 20,
      }),
    refetchInterval: (query) =>
      active ||
      query.state.data?.storage_available === false ||
      query.state.data?.data?.some(evaluationNeedsPolling)
        ? 3000
        : false,
    refetchOnMount: "always",
    retry: 1,
  })
  const records = query.data?.data ?? []
  const candidateOptions = new Map(candidates)
  for (const item of records) {
    if (item.algorithm_id)
      candidateOptions.set(
        item.algorithm_id,
        item.algorithm_name || item.algorithm_id,
      )
  }
  const selected = records.find((item) => item.id === recordId) ?? records[0]
  const firstRecordId = records[0]?.id
  useEffect(() => {
    if (recordId === null && firstRecordId) setRecordId(firstRecordId)
  }, [recordId, firstRecordId])
  const groups = new Map<string, EvaluationSummary[]>()
  for (const item of records) {
    const id = item.algorithm_id || "unknown"
    const group = groups.get(id) ?? []
    group.push(item)
    groups.set(id, group)
  }
  return (
    <section className="@container/evaluation flex h-full min-h-0 flex-col overflow-hidden rounded-lg border bg-card">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b p-3">
        <h2 className="mr-auto font-semibold">{t("evaluationData.title")}</h2>
        <Select
          value={candidate}
          onValueChange={(value) => {
            setCandidate(value)
            setPage(1)
            setRecordId(null)
          }}
        >
          <SelectTrigger
            className="w-full sm:w-52"
            aria-label={t("evaluationData.candidate")}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">
              {t("evaluationData.allCandidates")}
            </SelectItem>
            {[...candidateOptions].map(([id, name]) => (
              <SelectItem key={id} value={id}>
                {name || id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={profile}
          onValueChange={(value) => {
            setProfile(value)
            setPage(1)
            setRecordId(null)
          }}
        >
          <SelectTrigger
            className="w-full sm:w-40"
            aria-label={t("evaluationData.profile")}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["all", "standard", "elite"].map((value) => (
              <SelectItem key={value} value={value}>
                {t(`evaluationData.profiles.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void query.refetch()
          }}
          disabled={query.isFetching}
        >
          {t("evaluationData.refresh")}
        </Button>
      </div>
      {query.data?.storage_available === false && records.length > 0 && (
        <p
          role="status"
          className="border-b px-4 py-3 text-sm text-muted-foreground"
        >
          {t("evaluationData.storageOffline")}
        </p>
      )}
      {query.isPending ? (
        <p role="status" className="p-6 text-sm">
          {t("evaluationData.loading")}
        </p>
      ) : query.isError ? (
        <p role="alert" className="p-6 text-sm text-destructive">
          {t("evaluationData.loadFailed")}
        </p>
      ) : records.length === 0 ? (
        <div className="m-5 rounded-lg border border-dashed p-8 text-sm leading-relaxed text-muted-foreground">
          {t(
            query.data?.storage_available === false
              ? "evaluationData.storageOffline"
              : "evaluationData.empty",
          )}
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-rows-[minmax(140px,35%)_minmax(0,1fr)] @min-[680px]/evaluation:grid-cols-[260px_minmax(0,1fr)] @min-[680px]/evaluation:grid-rows-1">
          <aside
            className="min-h-0 overflow-auto border-b @min-[680px]/evaluation:border-b-0 @min-[680px]/evaluation:border-r"
            aria-label={t("evaluationData.evaluations")}
          >
            {[...groups].map(([id, items]) => (
              <div key={id}>
                <h3 className="sticky top-0 z-10 break-words border-b bg-muted px-4 py-2 text-sm font-medium">
                  {items[0].algorithm_name || t("evaluationData.unnamed")}
                </h3>
                {items.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    onClick={() => setRecordId(item.id)}
                    aria-pressed={selected?.id === item.id}
                    className={cn(
                      "w-full space-y-2 border-b px-4 py-3 text-left transition-colors hover:bg-muted/50",
                      selected?.id === item.id &&
                        "bg-primary/10 shadow-[inset_3px_0_0_var(--primary)]",
                    )}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium">
                        {t(
                          `evaluationData.profiles.${item.evaluation_profile}`,
                        )}
                      </span>
                      <StateBadge summary={item} />
                    </div>
                    {item.instance_id && (
                      <p className="break-all text-sm text-muted-foreground">
                        {item.instance_id}
                      </p>
                    )}
                    <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
                      <span>{formatDateTime(item.created_at)}</span>
                      <span>
                        {t("evaluationData.fileCount", {
                          count: item.artifact_count ?? 0,
                        })}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            ))}
          </aside>
          <section
            aria-label={t("evaluationData.results")}
            className="min-h-0 min-w-0 overflow-auto"
          >
            {selected && (
              <RecordDetail
                key={`${taskId}:${selected.id}`}
                taskId={taskId}
                summary={selected}
                active={active}
                storageAvailable={query.data?.storage_available ?? true}
              />
            )}
          </section>
        </div>
      )}
      {(query.data?.count ?? 0) > 20 && (
        <div className="flex shrink-0 items-center justify-end gap-3 border-t p-2 text-sm">
          <Button
            variant="ghost"
            size="sm"
            disabled={page === 1}
            onClick={() => setPage(page - 1)}
          >
            {t("evaluationData.previous")}
          </Button>
          <span>
            {page} / {Math.ceil((query.data?.count ?? 0) / 20)}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={page * 20 >= (query.data?.count ?? 0)}
            onClick={() => setPage(page + 1)}
          >
            {t("evaluationData.next")}
          </Button>
        </div>
      )}
    </section>
  )
}
