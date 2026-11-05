import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Llm4AdEvaluationGatewaysService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import GatewayUserId from "./GatewayUserId"

interface Props {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
}

export default function GatewayPoolPicker({ value, onChange }: Props) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ["evaluationGateways"],
    queryFn: () => Llm4AdEvaluationGatewaysService.listGateways(),
    refetchInterval: 5000,
    retry: 1,
  })
  const gateways = query.data ?? []
  const ids = (value.gateway_ids as string[] | undefined) ?? []
  const first = gateways.find((gateway) => ids.includes(gateway.id))
  const unavailable = ids.filter(
    (id) => !gateways.some((gateway) => gateway.id === id),
  )
  return (
    <section className="space-y-4" aria-label={t("remoteEvaluation.gateways")}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h4 className="text-sm font-semibold">
            {t("remoteEvaluation.gateways")}
          </h4>
          <p className="text-sm text-muted-foreground">
            {t("remoteEvaluation.poolHint")}
          </p>
        </div>
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
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("remoteEvaluation.loadError")}
        </p>
      )}
      {query.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("remoteEvaluation.loading")}
        </p>
      )}
      {gateways.length > 0 && (
        <div className="divide-y rounded-lg border">
          {gateways.map((gateway) => {
            const checked = ids.includes(gateway.id)
            const incompatible =
              !!first && first.contract_key !== gateway.contract_key
            return (
              <label
                key={gateway.id}
                htmlFor={`gateway-${gateway.id}`}
                className="flex cursor-pointer items-start gap-3 px-4 py-3 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50"
              >
                <Checkbox
                  id={`gateway-${gateway.id}`}
                  className="mt-1"
                  checked={checked}
                  disabled={!checked && incompatible}
                  onCheckedChange={(selected) => {
                    const next = selected
                      ? [...ids, gateway.id]
                      : ids.filter((id) => id !== gateway.id)
                    const remaining = gateways.find((item) =>
                      next.includes(item.id),
                    )
                    onChange({
                      ...value,
                      gateway_ids: next,
                      contract: remaining?.contract ?? {},
                    })
                  }}
                />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="break-words font-medium">
                      {gateway.name}
                    </span>
                    <Badge variant={gateway.online ? "secondary" : "outline"}>
                      {t(
                        `remoteEvaluation.${gateway.online ? "online" : "offline"}`,
                      )}
                    </Badge>
                  </span>
                  <span className="block break-words text-xs text-muted-foreground">
                    {String(gateway.contract.evaluator_id)} ·{" "}
                    {String(gateway.contract.version)} ·{" "}
                    {t("remoteEvaluation.capacity", {
                      running: gateway.running,
                      capacity: gateway.capacity,
                      queued: gateway.queued,
                    })}
                  </span>
                  {incompatible && (
                    <span className="block text-xs text-muted-foreground">
                      {t("remoteEvaluation.incompatible")}
                    </span>
                  )}
                </span>
              </label>
            )
          })}
        </div>
      )}
      {unavailable.length > 0 && (
        <div className="space-y-2 rounded-lg border border-destructive/40 p-3">
          <p role="alert" className="text-sm">
            {t("remoteEvaluation.unavailable")}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                ...value,
                gateway_ids: ids.filter((id) => !unavailable.includes(id)),
                contract: first?.contract ?? {},
              })
            }
          >
            {t("remoteEvaluation.removeUnavailable")}
          </Button>
        </div>
      )}
      {!query.isPending && !query.isError && gateways.length === 0 && (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          {t("remoteEvaluation.empty")}
        </p>
      )}
      {ids.length > 0 &&
        !gateways.some(
          (gateway) => ids.includes(gateway.id) && gateway.online,
        ) && (
          <p className="text-sm text-muted-foreground">
            {t("remoteEvaluation.offlineHint")}
          </p>
        )}
      <div className="rounded-lg bg-muted/30 p-4">
        <GatewayUserId />
      </div>
    </section>
  )
}
