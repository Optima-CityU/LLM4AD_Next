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

export default function RemoteEvaluationProgress({
  taskId,
  active,
}: {
  taskId: string
  active: boolean
}) {
  const { t } = useTranslation()
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
            {query.data?.map((job) => {
              const completed = job.report.completed
              const total = job.report.total
              const known =
                typeof total === "number" &&
                total > 0 &&
                typeof completed === "number"
              const waiting =
                !job.gateway_online &&
                ["accepted", "running"].includes(job.state)
              return (
                <TableRow key={job.id}>
                  <TableCell>
                    <code title={job.id}>{job.id.slice(0, 8)}</code>
                  </TableCell>
                  <TableCell>
                    <span className="break-words">
                      {job.gateway_name ?? "—"}
                    </span>
                    {job.gateway_name && !job.gateway_online && (
                      <span className="ml-2 text-xs text-muted-foreground">
                        {t("remoteEvaluation.offline")}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
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
