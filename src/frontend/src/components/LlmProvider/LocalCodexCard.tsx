import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CheckCircle2,
  Loader2,
  RefreshCw,
  Terminal,
  Unplug,
} from "lucide-react"
import { useId, useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdProvidersService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { localCodexService } from "@/lib/localCodex"
import { handleError } from "@/utils"

export default function LocalCodexCard() {
  const { t } = useTranslation()
  const modelId = useId()
  const defaultsId = useId()
  const [modelInput, setModel] = useState<string | undefined>()
  const [manualModel, setManualModel] = useState(false)
  const [setDefaults, setSetDefaults] = useState(true)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const status = useQuery({
    queryKey: ["local-codex-status"],
    queryFn: localCodexService.status,
    staleTime: 15_000,
    retry: false,
  })
  const model = modelInput ?? status.data?.model ?? ""
  const models = status.data?.models ?? []
  const useManualModel =
    manualModel || (!!model && !models.some((item) => item.id === model))
  const defaultLabel = status.data?.default_model
    ? t("llmProvider.codex.followDefaultModel", {
        model: status.data.default_model,
      })
    : t("llmProvider.codex.followDefault")
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["local-codex-status"] })
    queryClient.invalidateQueries({ queryKey: ["providers"] })
    queryClient.invalidateQueries({ queryKey: ["user-default-models"] })
  }
  const bind = useMutation({
    mutationFn: () =>
      localCodexService.bind({
        model: model.trim(),
        set_defaults: setDefaults,
      }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.codex.boundSuccess"))
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const unbind = useMutation({
    mutationFn: (id: string) =>
      Llm4AdProvidersService.deleteProvider({ providerId: id }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.codex.unboundSuccess"))
      setModel(undefined)
      setManualModel(false)
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const state = status.data
  const busy = bind.isPending || unbind.isPending
  const reason = status.isError ? "unreachable" : (state?.reason ?? "checking")

  return (
    <section
      className="mb-5 rounded-xl border bg-card p-5 shadow-sm"
      aria-label={t("llmProvider.codex.title")}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Terminal className="size-5" />
          </div>
          <div>
            <h2 className="font-semibold">{t("llmProvider.codex.title")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("llmProvider.codex.description")}
            </p>
          </div>
        </div>
        <Badge
          variant={state?.connected ? "default" : "secondary"}
          className="gap-1.5"
        >
          {state?.connected ? (
            <CheckCircle2 className="size-3.5" />
          ) : (
            <Unplug className="size-3.5" />
          )}
          {t(
            "llmProvider.codex." +
              (state?.provider_id
                ? "bound"
                : state?.connected
                  ? "available"
                  : "disconnected"),
          )}
        </Badge>
      </div>

      <div
        className="mt-4 flex flex-wrap items-center gap-2 text-sm"
        role="status"
        aria-live="polite"
      >
        {status.isFetching && <Loader2 className="size-4 animate-spin" />}
        <span
          className={
            state?.connected
              ? "text-emerald-700 dark:text-emerald-400"
              : "text-muted-foreground"
          }
        >
          {t("llmProvider.codex.status." + reason)}
        </span>
        {state?.version && (
          <span className="text-xs text-muted-foreground">
            · {state.version}
          </span>
        )}
        {state?.default_for_experiments && (
          <Badge variant="outline">
            {t("llmProvider.codex.defaultsActive")}
          </Badge>
        )}
      </div>
      {reason === "not_logged_in" && (
        <p className="mt-2 text-sm text-muted-foreground">
          {t("llmProvider.codex.loginHint")}{" "}
          <code className="rounded bg-muted px-1.5 py-0.5">codex login</code>
        </p>
      )}
      {["not_configured", "unreachable"].includes(reason) && (
        <p className="mt-2 text-sm text-muted-foreground">
          {t("llmProvider.codex.serviceHint")}
        </p>
      )}

      <div className="mt-4 grid gap-4 md:grid-cols-[minmax(220px,1fr)_auto] md:items-end">
        <div className="space-y-2">
          <Label htmlFor={modelId}>{t("llmProvider.codex.model")}</Label>
          <Select
            value={useManualModel ? "__manual__" : model || "codex-default"}
            onValueChange={(value) => {
              setManualModel(value === "__manual__")
              if (value !== "__manual__")
                setModel(value === "codex-default" ? "" : value)
            }}
            disabled={busy}
          >
            <SelectTrigger id={modelId} className="w-full">
              <SelectValue placeholder={t("llmProvider.codex.selectModel")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="codex-default">{defaultLabel}</SelectItem>
              {models.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
              <SelectItem value="__manual__">
                {t("llmProvider.codex.customModel")}
              </SelectItem>
            </SelectContent>
          </Select>
          {useManualModel && (
            <Input
              aria-label={t("llmProvider.codex.customModel")}
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder={t("llmProvider.codex.modelPlaceholder")}
              disabled={busy}
              maxLength={255}
            />
          )}
          <p className="text-xs text-muted-foreground">
            {t(
              "llmProvider.codex." +
                (state?.models_available ? "modelsHint" : "modelsUnavailable"),
            )}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={status.isFetching || busy}
            onClick={() => status.refetch()}
          >
            <RefreshCw
              className={
                "mr-2 size-4 " + (status.isFetching ? "animate-spin" : "")
              }
            />
            {t("llmProvider.codex.check")}
          </Button>
          <Button
            disabled={!state?.connected || busy || /[;\r\n]/.test(model)}
            onClick={() => bind.mutate()}
          >
            {bind.isPending && <Loader2 className="mr-2 size-4 animate-spin" />}
            {t("llmProvider.codex." + (state?.provider_id ? "update" : "bind"))}
          </Button>
          {state?.provider_id && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => unbind.mutate(state.provider_id!)}
            >
              {t("llmProvider.codex.unbind")}
            </Button>
          )}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Checkbox
          id={defaultsId}
          checked={setDefaults}
          disabled={busy}
          onCheckedChange={(value) => setSetDefaults(value === true)}
        />
        <Label htmlFor={defaultsId} className="text-sm font-normal">
          {t("llmProvider.codex.setDefaults")}
        </Label>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {t("llmProvider.codex.usageHint")}
      </p>
    </section>
  )
}
