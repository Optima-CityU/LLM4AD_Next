import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { useEffect, useId, useState } from "react"
import { useTranslation } from "react-i18next"
import { Llm4AdEvaluationGatewaysService } from "@/client"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  gatewayPoolState,
  normalizeGatewayPool,
  type PoolGateway,
  selectPoolContract,
  selectPoolEvaluator,
  setPoolSelectionMode,
  togglePoolGateway,
} from "@/lib/gatewayPool"
import GatewayUserId from "./GatewayUserId"

interface Props {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
}

export default function GatewayPoolPicker({ value, onChange }: Props) {
  const { t } = useTranslation()
  const groupInputId = useId()
  const modeInputId = useId()
  const contractInputId = useId()
  const attemptsInputId = useId()
  const leaseInputId = useId()
  const [chosenGroup, setChosenGroup] = useState<string>()
  const normalized = normalizeGatewayPool(value)
  useEffect(() => {
    if (normalized !== value) onChange(normalized)
  }, [normalized, value, onChange])
  const query = useQuery({
    queryKey: ["evaluationGateways"],
    queryFn: () => Llm4AdEvaluationGatewaysService.listGateways(),
    refetchInterval: 5000,
    retry: 1,
  })
  const gateways: PoolGateway[] = query.data ?? []
  const pool = gatewayPoolState(value, gateways, chosenGroup)
  const { mode, ids, group, groups, unavailable, outsideGroup } = pool
  const visibleGateways =
    mode === "auto"
      ? pool.members
      : gateways.filter(
          (gateway) => gateway.contract.evaluator_id === group && group,
        )
  const staleIds = [...unavailable, ...outsideGroup]
  const groupName = (id: string) =>
    t(`remoteEvaluation.evaluators.${id}`, { defaultValue: id })
  const contractLabel = (contract: Record<string, unknown>) => {
    const metrics = Array.isArray(contract.metrics)
      ? contract.metrics
          .flatMap((metric) => {
            if (
              !metric ||
              typeof metric !== "object" ||
              typeof metric.name !== "string"
            )
              return []
            const direction =
              metric.type === "maximize"
                ? "↑"
                : metric.type === "minimize"
                  ? "↓"
                  : ""
            return `${metric.name}${direction}${typeof metric.weight === "number" ? ` ×${metric.weight}` : ""}`
          })
          .sort()
          .join(", ")
      : contract.metrics && typeof contract.metrics === "object"
        ? Object.keys(contract.metrics).sort().join(", ")
        : ""
    return [
      t("remoteEvaluation.contractVersion", {
        version: String(contract.version ?? "—"),
        dataVersion: String(contract.data_version ?? "—"),
      }),
      metrics,
    ]
      .filter(Boolean)
      .join(" · ")
  }
  return (
    <section
      className="flex flex-col gap-4"
      aria-label={t("remoteEvaluation.gateways")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
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
      <div className="flex flex-col gap-2">
        <label htmlFor={modeInputId} className="text-sm font-medium">
          {t("remoteEvaluation.selectionMode")}
        </label>
        <Select
          value={mode}
          onValueChange={(nextMode) => {
            if (nextMode === "auto" || nextMode === "selected")
              onChange(setPoolSelectionMode(value, gateways, nextMode))
          }}
        >
          <SelectTrigger id={modeInputId} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="auto">
                {t("remoteEvaluation.automaticPool")}
              </SelectItem>
              <SelectItem value="selected">
                {t("remoteEvaluation.manualPool")}
              </SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <p className="text-sm text-muted-foreground">
          {t(
            mode === "auto"
              ? "remoteEvaluation.automaticHint"
              : "remoteEvaluation.manualHint",
          )}
        </p>
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor={groupInputId} className="text-sm font-medium">
          {t("remoteEvaluation.evaluatorKind")}
        </label>
        <Select
          value={group ?? ""}
          onValueChange={(nextGroup) => {
            if (nextGroup === group) return
            setChosenGroup(nextGroup)
            onChange(selectPoolEvaluator(value, gateways, nextGroup))
          }}
          disabled={groups.length === 0}
        >
          <SelectTrigger id={groupInputId} className="w-full min-w-0">
            <SelectValue placeholder={t("remoteEvaluation.chooseEvaluator")} />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {groups.map((id) => (
                <SelectItem key={id} value={id}>
                  {groupName(id)}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <p className="text-sm text-muted-foreground">
          {t("remoteEvaluation.groupHint")}
        </p>
      </div>
      {group && (
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={contractInputId} className="text-sm font-medium">
            {t("remoteEvaluation.benchmarkContract")}
          </label>
          <Select
            value={pool.contractKey ?? ""}
            onValueChange={(key) => {
              const option = pool.contracts.find((item) => item.key === key)
              if (option) onChange(selectPoolContract(value, option.contract))
            }}
            disabled={pool.contracts.length === 0}
          >
            <SelectTrigger id={contractInputId} className="w-full min-w-0">
              <SelectValue placeholder={t("remoteEvaluation.chooseContract")} />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {pool.contracts.map((option) => {
                  const label = contractLabel(option.contract)
                  const ambiguous = pool.contracts.some(
                    (other) =>
                      other.key !== option.key &&
                      contractLabel(other.contract) === label,
                  )
                  return (
                    <SelectItem key={option.key} value={option.key}>
                      {label}
                      {ambiguous &&
                        ` · ${option.fingerprint ?? t("remoteEvaluation.configuredContract")}`}
                    </SelectItem>
                  )
                })}
              </SelectGroup>
            </SelectContent>
          </Select>
          {!pool.contract && pool.contracts.length > 1 && (
            <p role="status" className="text-sm text-muted-foreground">
              {t("remoteEvaluation.contractChoiceRequired")}
            </p>
          )}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-2">
          <label htmlFor={attemptsInputId} className="text-sm font-medium">
            {t("remoteEvaluation.maxAttempts")}
          </label>
          <Input
            id={attemptsInputId}
            type="number"
            min={1}
            step={1}
            value={String(normalized.max_attempts)}
            onChange={(event) => {
              const max_attempts = event.currentTarget.valueAsNumber
              if (Number.isInteger(max_attempts) && max_attempts > 0)
                onChange({ ...normalized, max_attempts })
            }}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={leaseInputId} className="text-sm font-medium">
            {t("remoteEvaluation.leaseTimeout")}
          </label>
          <Input
            id={leaseInputId}
            type="number"
            min={0}
            step="any"
            value={String(normalized.lease_timeout)}
            onChange={(event) => {
              const lease_timeout = event.currentTarget.valueAsNumber
              if (Number.isFinite(lease_timeout) && lease_timeout > 0)
                onChange({ ...normalized, lease_timeout })
            }}
          />
        </div>
      </div>
      <p className="text-sm text-muted-foreground">
        {t("remoteEvaluation.dispatchHint")}
      </p>
      {mode === "auto" && pool.contract && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("remoteEvaluation.poolMembers", {
            available: pool.available.length,
            total: pool.members.length,
          })}
        </p>
      )}
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
      {visibleGateways.length > 0 && (
        <div className="divide-y rounded-lg border">
          {visibleGateways.map((gateway) => {
            const checked = ids.includes(gateway.id)
            const incompatible = !pool.compatible(gateway)
            const dataVersion = String(gateway.contract.data_version ?? "—")
            return (
              <div
                key={gateway.id}
                className="flex items-start gap-3 px-4 py-3"
              >
                {mode === "selected" && (
                  <Checkbox
                    id={`${groupInputId}-gateway-${gateway.id}`}
                    className="mt-1"
                    checked={checked}
                    disabled={!checked && incompatible}
                    onCheckedChange={(selected) => {
                      onChange(
                        togglePoolGateway(
                          value,
                          gateways,
                          group,
                          gateway,
                          selected === true,
                        ),
                      )
                    }}
                  />
                )}
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="flex flex-wrap items-center gap-2">
                    {mode === "selected" ? (
                      <label
                        htmlFor={`${groupInputId}-gateway-${gateway.id}`}
                        className="cursor-pointer break-words font-medium"
                      >
                        {gateway.name}
                      </label>
                    ) : (
                      <span className="break-words font-medium">
                        {gateway.name}
                      </span>
                    )}
                    <Badge variant={gateway.online ? "secondary" : "outline"}>
                      {t(
                        `remoteEvaluation.${gateway.online ? "online" : "offline"}`,
                      )}
                    </Badge>
                    <Badge
                      variant={
                        gateway.ready === false ? "outline" : "secondary"
                      }
                    >
                      {t(
                        gateway.ready === false
                          ? "remoteEvaluation.notReady"
                          : "remoteEvaluation.ready",
                      )}
                    </Badge>
                    <Badge variant="outline">
                      {t("remoteEvaluation.protocolVersion", {
                        version: gateway.protocol_version ?? 1,
                      })}
                    </Badge>
                    {gateway.running >= gateway.capacity && (
                      <Badge variant="outline">
                        {t("remoteEvaluation.busy")}
                      </Badge>
                    )}
                  </span>
                  <span className="block break-words text-sm text-muted-foreground">
                    <span title={dataVersion}>
                      {t("remoteEvaluation.contractVersion", {
                        version: String(gateway.contract.version ?? "—"),
                        dataVersion:
                          dataVersion.length > 12
                            ? `${dataVersion.slice(0, 12)}…`
                            : dataVersion,
                      })}
                    </span>{" "}
                    ·{" "}
                    {t("remoteEvaluation.capacity", {
                      running: gateway.running,
                      capacity: gateway.capacity,
                      queued: gateway.queued,
                    })}
                  </span>
                  {incompatible && (
                    <span className="block text-sm text-muted-foreground">
                      {t("remoteEvaluation.incompatible")}
                    </span>
                  )}
                  {mode === "auto" && (gateway.protocol_version ?? 1) < 2 && (
                    <span className="text-sm text-muted-foreground">
                      {t("remoteEvaluation.requiresV2")}
                    </span>
                  )}
                </span>
              </div>
            )
          })}
        </div>
      )}
      {mode === "selected" && query.isSuccess && staleIds.length > 0 && (
        <Alert>
          <AlertDescription>
            <p>
              {t(
                outsideGroup.length > 0
                  ? "remoteEvaluation.outsideGroup"
                  : "remoteEvaluation.unavailable",
              )}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                onChange({
                  ...normalized,
                  gateway_ids: ids.filter((id) => !staleIds.includes(id)),
                  contract: pool.contract ?? {},
                })
              }
            >
              {t("remoteEvaluation.removeUnavailable")}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {pool.contract && (
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm text-muted-foreground">
            {t("remoteEvaluation.pinnedContract")}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setChosenGroup(group)
              onChange({ ...normalized, gateway_ids: [], contract: {} })
            }}
          >
            {t("remoteEvaluation.resetPool")}
          </Button>
        </div>
      )}
      {query.isSuccess &&
        mode === "selected" &&
        group &&
        gateways.length > 0 &&
        !gateways.some(
          (gateway) => gateway.contract.evaluator_id === group,
        ) && (
          <p className="text-sm text-muted-foreground">
            {t("remoteEvaluation.emptyGroup")}
          </p>
        )}
      {!pool.contract &&
        !query.isPending &&
        !query.isError &&
        gateways.length === 0 && (
          <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            {t("remoteEvaluation.empty")}
          </p>
        )}
      {mode === "auto" &&
        pool.contract &&
        query.isSuccess &&
        pool.available.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {t("remoteEvaluation.waitingForPool")}
          </p>
        )}
      {mode === "selected" &&
        ids.length > 0 &&
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
