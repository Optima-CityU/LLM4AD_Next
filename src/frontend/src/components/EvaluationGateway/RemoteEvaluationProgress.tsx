import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Llm4AdEvaluationGatewaysService } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  type RemoteEvaluationJob,
  remoteAttemptHistory,
  remoteFailureCategory,
} from "@/lib/remoteEvaluation"

export default function RemoteEvaluationProgress({
  taskId,
  active,
}: {
  taskId: string
  active: boolean
}) {
  const { t, i18n } = useTranslation()
  const query = useQuery({
    queryKey: ["remoteEvaluationJobs", taskId, active],
    queryFn: () => Llm4AdEvaluationGatewaysService.taskJobs({ taskId }),
    refetchInterval: active ? 3000 : false,
    refetchOnMount: "always",
    retry: 1,
  })
  return (
    <section className="h-full space-y-4 overflow-auto p-4">
      <div>
        <h3 className="font-semibold">{t("remoteEvaluation.progress")}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("remoteEvaluation.progressHint")}
        </p>
      </div>
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("remoteEvaluation.loadError")}
        </p>
      )}
      {query.isPending ? (
        <p role="status">{t("remoteEvaluation.loading")}</p>
      ) : query.data?.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t("remoteEvaluation.noJobs")}
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("remoteEvaluation.candidate")}</TableHead>
              <TableHead>{t("remoteEvaluation.gateway")}</TableHead>
              <TableHead>{t("remoteEvaluation.status")}</TableHead>
              <TableHead>{t("remoteEvaluation.progress")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {query.data?.map((job: RemoteEvaluationJob) => {
              const completed = job.report.completed
              const total = job.report.total
              const known =
                typeof total === "number" &&
                total > 0 &&
                typeof completed === "number"
              const waiting =
                !job.gateway_online &&
                ["accepted", "running"].includes(job.state)
              const category = remoteFailureCategory(job.failure_category)
              const history = remoteAttemptHistory(job.attempt_history)
              const hasAttempts =
                typeof job.attempt_count === "number" &&
                Number.isInteger(job.attempt_count) &&
                job.attempt_count >= 0 &&
                typeof job.max_attempts === "number" &&
                Number.isInteger(job.max_attempts) &&
                job.max_attempts > 0
              return (
                <TableRow key={job.id}>
                  <TableCell>
                    <code title={job.id}>{job.id.slice(0, 8)}</code>
                  </TableCell>
                  <TableCell>
                    <span className="break-words">
                      {job.gateway_name ?? "—"}
                    </span>
                    {job.gateway_id && (
                      <code
                        className="ml-2 text-xs text-muted-foreground"
                        title={job.gateway_id}
                      >
                        {job.gateway_id.slice(0, 8)}
                      </code>
                    )}
                    {job.evaluator_id && (
                      <p className="break-words text-xs text-muted-foreground">
                        {job.evaluator_id}
                      </p>
                    )}
                    {job.gateway_name && !job.gateway_online && (
                      <span className="ml-2 text-xs text-muted-foreground">
                        {t("remoteEvaluation.offline")}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge
                        variant={
                          job.state === "failed" ? "destructive" : "outline"
                        }
                      >
                        {t(
                          `remoteEvaluation.states.${waiting ? "reconnecting" : job.state}`,
                          { defaultValue: job.state },
                        )}
                      </Badge>
                      {hasAttempts && (
                        <Badge variant="outline">
                          {t("remoteEvaluation.attemptCount", {
                            count: job.attempt_count,
                            max: job.max_attempts,
                          })}
                        </Badge>
                      )}
                      {category && (
                        <Badge
                          variant={
                            job.state === "failed" ? "destructive" : "secondary"
                          }
                        >
                          {t(`remoteEvaluation.failureCategories.${category}`)}
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-md whitespace-normal">
                    {known && (
                      <div className="flex items-center gap-2">
                        <progress
                          aria-label={t("remoteEvaluation.progress")}
                          className="h-2 w-28 accent-primary"
                          value={Math.max(0, Math.min(completed, total))}
                          max={total}
                        />
                        <span className="text-xs tabular-nums">
                          {completed}/{total}
                        </span>
                      </div>
                    )}
                    {typeof job.report.message === "string" && (
                      <p className="break-words text-sm">
                        {job.report.message}
                      </p>
                    )}
                    {job.state === "completed" &&
                      typeof job.report.score === "number" && (
                        <span className="tabular-nums">
                          {t("remoteEvaluation.score", {
                            score: job.report.score,
                          })}
                        </span>
                      )}
                    {history.length > 0 && (
                      <details className="mt-2 text-sm">
                        <summary className="cursor-pointer text-muted-foreground">
                          {t("remoteEvaluation.attemptHistory", {
                            count: history.length,
                          })}
                        </summary>
                        <ol className="mt-2 flex flex-col gap-2">
                          {history.map((attempt, index) => (
                            <li
                              key={`${attempt.number}-${index}`}
                              className="flex flex-col gap-1 border-l-2 pl-3"
                            >
                              <div className="flex flex-wrap items-center gap-1">
                                <span className="font-medium">
                                  {t("remoteEvaluation.attemptNumber", {
                                    number: attempt.number,
                                  })}
                                </span>
                                <span className="break-all">
                                  ·{" "}
                                  {attempt.gateway ??
                                    t("remoteEvaluation.unknownGateway")}
                                </span>
                                {attempt.category && (
                                  <Badge variant="outline">
                                    {t(
                                      `remoteEvaluation.failureCategories.${attempt.category}`,
                                    )}
                                  </Badge>
                                )}
                                {attempt.state && (
                                  <Badge variant="outline">
                                    {t(
                                      `remoteEvaluation.states.${attempt.state}`,
                                    )}
                                  </Badge>
                                )}
                              </div>
                              {(attempt.startedAt || attempt.finishedAt) && (
                                <p className="text-xs text-muted-foreground">
                                  {attempt.startedAt && (
                                    <time dateTime={attempt.startedAt}>
                                      {new Date(
                                        attempt.startedAt,
                                      ).toLocaleString(i18n.language)}
                                    </time>
                                  )}
                                  {attempt.startedAt &&
                                    attempt.finishedAt &&
                                    " → "}
                                  {attempt.finishedAt && (
                                    <time dateTime={attempt.finishedAt}>
                                      {new Date(
                                        attempt.finishedAt,
                                      ).toLocaleString(i18n.language)}
                                    </time>
                                  )}
                                </p>
                              )}
                              {attempt.message && (
                                <p className="break-words">{attempt.message}</p>
                              )}
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </section>
  )
}
