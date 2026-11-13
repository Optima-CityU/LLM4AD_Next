import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { type EvaluationSummary, Llm4AdEvaluationsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { evaluationNeedsPolling } from "@/lib/evaluationArtifacts"
import {
  evaluationRecordScore,
  rawEvaluationFields,
} from "@/lib/evaluationData"
import { formatDateTime } from "@/lib/utils"
import NodeArtifacts from "./NodeArtifacts"
import NodeMetrics from "./NodeMetrics"

function MetadataEntries({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="flex min-w-0 flex-col gap-2 text-sm leading-relaxed">
      {Object.entries(data).map(([name, value]) => (
        <div key={name} className="min-w-0">
          {value !== null && typeof value === "object" ? (
            <>
              <dt className="sr-only">{name}</dt>
              <dd>
                <MetadataObject name={name} value={value} />
              </dd>
            </>
          ) : (
            <>
              <dt className="break-all text-muted-foreground">{name}</dt>
              <dd className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                {value === null ? "null" : String(value)}
              </dd>
            </>
          )}
        </div>
      ))}
    </dl>
  )
}

function MetadataObject({ name, value }: { name: string; value: object }) {
  const [open, setOpen] = useState(false)
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer break-all text-sm font-medium">
        {name}
        <span className="ml-1.5 font-normal text-muted-foreground">
          ({Object.keys(value).length})
        </span>
      </summary>
      {open && (
        <div className="mt-2 min-w-0 border-l pl-3">
          <MetadataEntries data={Object.fromEntries(Object.entries(value))} />
        </div>
      )}
    </details>
  )
}

function ResultMetadata({
  result,
  evaluationState,
  showScore = false,
}: {
  result?: Record<string, unknown>
  evaluationState?: string
  showScore?: boolean
}) {
  const { t } = useTranslation()
  const score = showScore
    ? evaluationRecordScore(result, evaluationState)
    : undefined
  const rawData = rawEvaluationFields(result ?? {}, score)
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {typeof result?.error_message === "string" && result.error_message && (
        <p className="whitespace-pre-wrap break-words text-sm text-destructive">
          {result.error_message}
        </p>
      )}
      {typeof result?.observation === "string" && result.observation && (
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
          {result.observation}
        </p>
      )}
      <NodeMetrics result={result ?? {}} evaluationScore={score} />
      {Object.keys(rawData).length > 0 && (
        <details className="min-w-0" data-node-raw-metadata>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            {t("evaluationData.rawData")}
            <Badge
              variant="secondary"
              className="ml-1.5 px-1.5 py-0 text-[11px]"
            >
              {Object.keys(rawData).length}
            </Badge>
          </summary>
          <div className="pt-2">
            <MetadataEntries data={rawData} />
          </div>
        </details>
      )}
    </div>
  )
}

function StorageWarning({
  state,
  errors = [],
}: {
  state?: string
  errors?: string[]
}) {
  const { t } = useTranslation()
  if ((!state || state === "saved") && errors.length === 0) return null
  const diagnostics = errors.length > 0 && (
    <ul className="max-h-40 space-y-1 overflow-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
      {errors.map((error, index) => (
        <li key={`${index}:${error}`}>{error}</li>
      ))}
    </ul>
  )
  const long = errors.length > 2 || errors.some((error) => error.length > 160)
  return (
    <div
      role="status"
      className="min-w-0 space-y-2 text-sm text-muted-foreground"
    >
      <p>
        {t(
          state === "error" ||
            ((!state || state === "saved") && errors.length > 0)
            ? "evaluationData.storageError"
            : state === "offline"
              ? "evaluationData.storageOffline"
              : state === "unavailable"
                ? "evaluationData.storageUnavailable"
                : "evaluationData.storagePending",
        )}
      </p>
      {long ? (
        <details className="min-w-0" data-node-storage-errors>
          <summary className="cursor-pointer">
            {t("evaluationData.storageDiagnostics", { count: errors.length })}
          </summary>
          <div className="pt-2">{diagnostics}</div>
        </details>
      ) : (
        diagnostics
      )}
    </div>
  )
}

function SavedNodeData({
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
  const query = useQuery({
    queryKey: [
      "nodeEvaluation",
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
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {summary.state && summary.state !== "completed" && (
        <Badge
          className="self-start"
          variant={summary.state === "failed" ? "destructive" : "secondary"}
        >
          {t(`evaluationData.states.${summary.state}`, {
            defaultValue: summary.state,
          })}
        </Badge>
      )}
      {typeof summary.progress?.message === "string" &&
        evaluationNeedsPolling(summary) && (
          <p className="break-words text-sm text-muted-foreground">
            {summary.progress.message}
          </p>
        )}
      {query.isPending && <Skeleton className="h-16 w-full" />}
      {query.isError && (
        <div role="alert" className="text-sm text-muted-foreground">
          {t("evaluationData.loadFailed")}
          <Button variant="link" size="sm" onClick={() => void query.refetch()}>
            {t("evaluationData.refresh")}
          </Button>
        </div>
      )}
      {record && (
        <>
          <ResultMetadata
            result={record.result}
            evaluationState={record.state ?? summary.state}
            showScore
          />
          <StorageWarning
            state={record.storage_state}
            errors={record.storage_errors}
          />
          <NodeArtifacts
            taskId={taskId}
            evaluationId={record.id}
            files={record.artifacts ?? []}
            availableIds={query.data?.available_artifact_ids ?? []}
          />
        </>
      )}
    </div>
  )
}

export default function NodeEvaluationData({
  taskId,
  nodeId,
  active,
  evaluation,
}: {
  taskId: string
  nodeId: string
  active: boolean
  evaluation?: Record<string, unknown>
}) {
  const { t } = useTranslation()
  const [recordId, setRecordId] = useState<string>()
  const query = useInfiniteQuery({
    queryKey: ["nodeEvaluations", taskId, nodeId, active],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      Llm4AdEvaluationsService.listEvaluations({
        taskId,
        algorithmId: nodeId,
        page: pageParam,
        pageSize: 20,
      }),
    getNextPageParam: (last) =>
      last.page * last.page_size < last.count ? last.page + 1 : undefined,
    refetchInterval: (query) =>
      active ||
      query.state.data?.pages.some(
        (page) =>
          page.storage_available === false ||
          page.data.some(evaluationNeedsPolling),
      )
        ? 3000
        : false,
    retry: 1,
  })
  const records = query.data?.pages.flatMap((page) => page.data) ?? []
  const selected =
    records.find((record) => record.id === recordId) ?? records[0]
  const labelFor = (record: EvaluationSummary) =>
    [
      t(`evaluationData.profiles.${record.evaluation_profile}`, {
        defaultValue:
          record.evaluation_profile || t("evaluationData.profiles.standard"),
      }),
      record.instance_id,
      formatDateTime(record.created_at),
    ]
      .filter(Boolean)
      .join(" · ")
  return (
    <div className="flex min-w-0 flex-col gap-3" data-node-metadata={nodeId}>
      <Separator />
      {query.isPending && <Skeleton className="h-12 w-full" />}
      {query.isError && (
        <div role="alert" className="text-sm text-muted-foreground">
          {t("evaluationData.loadFailed")}
          <Button variant="link" size="sm" onClick={() => void query.refetch()}>
            {t("evaluationData.refresh")}
          </Button>
        </div>
      )}
      {selected ? (
        <>
          {records.length > 1 ? (
            <Select value={selected.id} onValueChange={setRecordId}>
              <SelectTrigger
                className="w-full min-w-0"
                size="sm"
                aria-label={t("evaluationData.recordSelection")}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {records.map((record) => (
                    <SelectItem key={record.id} value={record.id}>
                      {labelFor(record)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          ) : (
            <p className="break-words text-xs text-muted-foreground">
              {labelFor(selected)}
            </p>
          )}
          {query.hasNextPage && (
            <Button
              variant="link"
              size="sm"
              className="h-auto self-start p-0"
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {t("evaluationData.moreRecords")}
            </Button>
          )}
          <SavedNodeData
            key={selected.id}
            taskId={taskId}
            summary={selected}
            active={active}
            storageAvailable={
              query.data?.pages.every(
                (page) => page.storage_available !== false,
              ) ?? true
            }
          />
        </>
      ) : (
        <ResultMetadata result={evaluation} />
      )}
    </div>
  )
}
