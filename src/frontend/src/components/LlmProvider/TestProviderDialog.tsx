import { useMutation } from "@tanstack/react-query"
import { CheckCircle2, XCircle } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import {
  Llm4AdProvidersService,
  type ProviderTestResponse,
  type ProviderType,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export interface TestProviderParams {
  type: ProviderType
  api_key?: string
  auth_token?: string
  base_url?: string
  model?: string
  /**
   * Stored provider id. When set (e.g. builtin providers whose credentials are
   * masked), connectivity is tested via the stored provider endpoint using only
   * the provider id and the selected model name.
   */
  providerId?: string
}

interface TestProviderDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  params: TestProviderParams
}

function maskSecret(value?: string): string {
  if (!value) return "-"
  if (value.length <= 8) return "****"
  return `${value.slice(0, 4)}****${value.slice(-4)}`
}

const providerTypeLabels: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI Compatible",
  mock: "Mock",
}

export function TestProviderDialog({
  open,
  onOpenChange,
  params,
}: TestProviderDialogProps) {
  const { t } = useTranslation()

  const models = useMemo(
    () =>
      (params.model ?? "")
        .split(";")
        .map((s) => s.trim())
        .filter(Boolean),
    [params.model],
  )

  const [selectedModel, setSelectedModel] = useState(models[0] ?? "")
  const [result, setResult] = useState<ProviderTestResponse | null>(null)

  useEffect(() => {
    if (open) {
      setSelectedModel(models[0] ?? "")
      setResult(null)
    }
  }, [open, models])

  const mutation = useMutation({
    mutationFn: () =>
      params.providerId
        ? Llm4AdProvidersService.testStoredProvider({
            providerId: params.providerId,
            requestBody: {
              model: selectedModel,
              // 字段级覆盖：表单填了就用表单值，空则后端回退到库中真实凭据。
              // 内置供应商后端会忽略这些覆盖。
              api_key: params.api_key || undefined,
              auth_token: params.auth_token || undefined,
              base_url: params.base_url || undefined,
            },
          })
        : Llm4AdProvidersService.testProvider({
            requestBody: {
              type: params.type,
              model: selectedModel,
              api_key: params.api_key || undefined,
              auth_token: params.auth_token || undefined,
              base_url: params.base_url || undefined,
            },
          }),
    onSuccess: (data) => setResult(data),
    onError: () =>
      setResult({
        success: false,
        message: t("llmProvider.test.requestError"),
      }),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>{t("llmProvider.test.title")}</DialogTitle>
          <DialogDescription>
            {t("llmProvider.test.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 py-2">
          {/* Read-only params */}
          <div className="rounded-lg border border-border/60 bg-muted/30 px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div>
              <span className="text-xs text-muted-foreground">
                {t("llmProvider.providerType")}
              </span>
              <p className="font-medium">
                {providerTypeLabels[params.type] ?? params.type}
              </p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground">Base URL</span>
              <p className="font-medium truncate">{params.base_url || "-"}</p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground">API Key</span>
              <p className="font-medium font-mono text-xs">
                {maskSecret(params.api_key)}
              </p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground">Auth Token</span>
              <p className="font-medium font-mono text-xs">
                {maskSecret(params.auth_token)}
              </p>
            </div>
          </div>

          {/* Model selector */}
          <div className="space-y-1.5">
            <Label className="text-sm">
              {t("llmProvider.test.selectModel")}
            </Label>
            {models.length > 0 ? (
              <Select value={selectedModel} onValueChange={setSelectedModel}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("llmProvider.test.noModels")}
              </p>
            )}
          </div>

          {/* Test result */}
          {result && (
            <div
              className={`rounded-lg border p-4 ${
                result.success
                  ? "border-green-500/40 bg-green-500/5"
                  : "border-destructive/40 bg-destructive/5"
              }`}
            >
              <div className="flex items-center gap-2 mb-2">
                {result.success ? (
                  <CheckCircle2 className="size-4 text-green-500 shrink-0" />
                ) : (
                  <XCircle className="size-4 text-destructive shrink-0" />
                )}
                <span
                  className={`text-sm font-medium ${result.success ? "text-green-600 dark:text-green-400" : "text-destructive"}`}
                >
                  {result.success
                    ? t("llmProvider.test.success")
                    : t("llmProvider.test.failed")}
                </span>
              </div>
              <p className="text-sm text-muted-foreground break-all">
                {result.message}
              </p>
              {result.success && result.data != null && (
                <div className="mt-3 rounded-md bg-muted p-3 max-h-40 overflow-y-auto">
                  <Label className="text-xs text-muted-foreground mb-1 block">
                    {t("llmProvider.test.response")}
                  </Label>
                  <p className="text-sm whitespace-pre-wrap">
                    {String(result.data)}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            {t("common.close")}
          </Button>
          <LoadingButton
            onClick={() => mutation.mutate()}
            loading={mutation.isPending}
            disabled={!selectedModel}
          >
            {t("llmProvider.test.run")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
