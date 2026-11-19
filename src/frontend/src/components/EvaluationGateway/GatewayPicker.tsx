import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { useId } from "react"
import { useTranslation } from "react-i18next"
import { Llm4AdEvaluationGatewaysService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { gatewaySelection, selectedGateway } from "@/lib/remoteEvaluation"
import GatewayUserId from "./GatewayUserId"

interface Props {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
}

export default function GatewayPicker({ value, onChange }: Props) {
  const { t } = useTranslation()
  const labelId = useId()
  const query = useQuery({
    queryKey: ["evaluationGateways"],
    queryFn: () => Llm4AdEvaluationGatewaysService.listGateways(),
    refetchInterval: 5000,
    retry: 1,
  })
  const gateways = (query.data ?? []).filter((gateway) => gateway.online)
  const selectedId = selectedGateway(value)
  const selected = gateways.find((gateway) => gateway.id === selectedId)
  const waiting = selectedId && query.isSuccess && !selected
  const reselect =
    value.selection_mode === "auto" ||
    (Array.isArray(value.gateway_ids) && value.gateway_ids.length > 1)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h4 id={labelId} className="text-sm font-semibold">
          {t("remoteEvaluation.gateways")}
        </h4>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          aria-label={t("remoteEvaluation.refresh")}
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw />
        </Button>
      </div>
      {gateways.length > 0 && (
        <div role="radiogroup" aria-labelledby={labelId} className="space-y-2">
          {gateways.map((gateway) => {
            const { evaluator_id, version, data_version } = gateway.contract
            return (
              <label
                key={gateway.id}
                className="flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 hover:bg-muted/40 has-[:checked]:border-primary has-[:checked]:bg-primary/5"
              >
                <input
                  type="radio"
                  name={labelId}
                  value={gateway.id}
                  checked={gateway.id === selectedId}
                  onChange={() => onChange(gatewaySelection(value, gateway.id))}
                  className="mt-0.5 size-4 shrink-0 accent-primary"
                />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="break-words text-sm font-medium">
                      {gateway.name}
                    </span>
                    <Badge variant="secondary">
                      {t("remoteEvaluation.online")}
                    </Badge>
                    <code
                      className="text-xs text-muted-foreground"
                      title={gateway.id}
                    >
                      {gateway.id.slice(0, 8)}
                    </code>
                  </span>
                  <span className="block break-words text-xs text-muted-foreground">
                    {String(evaluator_id)}
                    {" · "}
                    {t("remoteEvaluation.contractVersion", {
                      version,
                      dataVersion: String(data_version ?? "").slice(0, 12),
                    })}
                  </span>
                </span>
              </label>
            )
          })}
        </div>
      )}
      <div className="space-y-1 text-sm text-muted-foreground">
        {query.isPending && (
          <p role="status">{t("remoteEvaluation.loading")}</p>
        )}
        {query.isError && <p role="alert">{t("remoteEvaluation.loadError")}</p>}
        {reselect && <p role="status">{t("remoteEvaluation.reselect")}</p>}
        {waiting && (
          <p role="status">
            {t("remoteEvaluation.offlineHint")}{" "}
            <code title={selectedId}>{selectedId.slice(0, 8)}</code>
          </p>
        )}
        {query.isSuccess && !gateways.length && !waiting && (
          <p role="status">{t("remoteEvaluation.empty")}</p>
        )}
      </div>
      <div className="space-y-2 rounded-lg border p-3">
        <p className="text-sm font-medium">{t("remoteEvaluation.connect")}</p>
        <p className="text-sm text-muted-foreground">
          {t("remoteEvaluation.connectHint")}
        </p>
        <code className="block break-all text-xs">
          llm4ad evaluator-gateway start
        </code>
        <GatewayUserId />
      </div>
    </div>
  )
}
