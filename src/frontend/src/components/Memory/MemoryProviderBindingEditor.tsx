import { Loader2, LockKeyhole, Pencil, Save, X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useEmbeddingProviders, useProviders } from "@/hooks/useProviders"
import { authFetch } from "@/utils/auth"

import type { MemoryProviderBinding } from "./types"

function apiUrl(path: string) {
  return `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad${path}`
}

function providerModels(provider?: { model?: string }) {
  return (provider?.model ?? "")
    .split(";")
    .map((model) => model.trim())
    .filter(Boolean)
}

export default function MemoryProviderBindingEditor({
  binding: initialBinding,
  onSaved,
  readOnly = false,
}: {
  binding?: MemoryProviderBinding | null
  onSaved?: () => void
  readOnly?: boolean
}) {
  const { t } = useTranslation()
  const [isEditing, setIsEditing] = useState(false)
  const providersQuery = useProviders({ enabled: isEditing })
  const embeddingProvidersQuery = useEmbeddingProviders({ enabled: isEditing })
  const providers = providersQuery.data?.items ?? []
  const embeddingProviders = embeddingProvidersQuery.data?.items ?? []

  const [binding, setBinding] = useState<MemoryProviderBinding | null>(initialBinding ?? null)
  const [chatProviderId, setChatProviderId] = useState("")
  const [chatModel, setChatModel] = useState("")
  const [embeddingProviderId, setEmbeddingProviderId] = useState("")
  const [isSaving, setIsSaving] = useState(false)

  const selectedChatProvider = providers.find((provider) => provider.id === chatProviderId)
  const selectedEmbeddingProvider = embeddingProviders.find(
    (provider) => provider.id === embeddingProviderId,
  )
  const models = useMemo(() => providerModels(selectedChatProvider), [selectedChatProvider])

  useEffect(() => {
    setBinding(initialBinding ?? null)
    setChatProviderId(initialBinding?.chat_provider_id ?? "")
    setChatModel(initialBinding?.chat_model ?? "")
    setEmbeddingProviderId(initialBinding?.embedding_provider_id ?? "")
  }, [initialBinding])

  useEffect(() => {
    if (!chatModel && models.length > 0) {
      setChatModel(models[0])
    }
  }, [chatModel, models])

  const changeChatProvider = (providerId: string) => {
    if (readOnly) return
    setChatProviderId(providerId)
    const provider = providers.find((item) => item.id === providerId)
    const nextModels = providerModels(provider)
    setChatModel(nextModels[0] ?? "")
  }

  const save = async () => {
    if (readOnly) return
    if (!chatProviderId || !chatModel || !embeddingProviderId) {
      toast.error(t("memory.providerBinding.validation"))
      return
    }
    setIsSaving(true)
    try {
      const response = await authFetch(apiUrl("/memory/provider-binding"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_provider_id: chatProviderId,
          chat_model: chatModel,
          embedding_provider_id: embeddingProviderId,
        }),
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(payload?.detail || t("memory.providerBinding.saveFailed"))
      }
      const payload = (await response.json()) as MemoryProviderBinding
      setBinding(payload)
      setIsEditing(false)
      toast.success(t("memory.providerBinding.saved"))
      onSaved?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.providerBinding.saveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  const isProviderLoading = providersQuery.isLoading || embeddingProvidersQuery.isLoading
  const providerLoadFailed = providersQuery.isError || embeddingProvidersQuery.isError
  const hasProviderChoices = providers.length > 0 && embeddingProviders.length > 0

  return (
    <div className="space-y-4 rounded-lg border bg-card/60 p-4" data-tour="memory-provider-binding">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{t("memory.providerBinding.title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("memory.providerBinding.description")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {binding?.embedding_locked && (
            <Badge variant="secondary" className="gap-1.5 whitespace-nowrap">
              <LockKeyhole className="size-3" />
              {t("memory.providerBinding.lockedDimensions", { dimensions: binding.embedding_dim })}
            </Badge>
          )}
          {isEditing ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="gap-1.5"
              disabled={readOnly}
              onClick={() => setIsEditing(false)}
            >
              <X className="size-4" />
              {t("memory.common.cancel")}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant={binding?.configured ? "outline" : "default"}
              className="gap-1.5"
              disabled={readOnly}
              onClick={() => setIsEditing(true)}
            >
              <Pencil className="size-4" />
              {binding?.configured ? t("memory.providerBinding.edit") : t("memory.providerBinding.bind")}
            </Button>
          )}
        </div>
      </div>

      {!isEditing && (
        <div className="grid gap-2 rounded-md border bg-muted/20 px-3 py-2 text-xs">
          {binding?.configured ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{t("memory.providerBinding.chatModel")}</span>
                <span className="truncate font-medium">{binding.chat_model || t("memory.common.notRecorded")}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">Embedding</span>
                <span className="truncate font-medium">
                  {binding.embedding_model || t("memory.common.notRecorded")}
                  {binding.embedding_dim ? ` · ${t("memory.providerBinding.dimensions", { dimensions: binding.embedding_dim })}` : ""}
                </span>
              </div>
            </>
          ) : (
            <div className="text-muted-foreground">
              {t("memory.providerBinding.unboundHint")}
            </div>
          )}
        </div>
      )}

      {isEditing && (
        <>
          {isProviderLoading && (
            <div className="flex min-h-[160px] items-center justify-center rounded-md border bg-muted/20 text-sm text-muted-foreground">
              <Loader2 className="mr-2 size-4 animate-spin" />
              {t("memory.providerBinding.loading")}
            </div>
          )}

          {!isProviderLoading && providerLoadFailed && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {t("memory.providerBinding.loadFailed")}
            </div>
          )}

          {!isProviderLoading && !providerLoadFailed && !hasProviderChoices && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              {t("memory.providerBinding.noProviders")}
            </div>
          )}

          {!isProviderLoading && !providerLoadFailed && hasProviderChoices && (
            <>
              <div className="grid gap-3">
                <div className="grid gap-2">
                  <Label>{t("memory.providerBinding.chatProvider")}</Label>
                  <Select value={chatProviderId} onValueChange={changeChatProvider} disabled={readOnly}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("memory.providerBinding.selectChatProvider")} />
                    </SelectTrigger>
                    <SelectContent>
                      {providers.map((provider) => (
                        <SelectItem key={provider.id} value={provider.id}>
                          {provider.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="memory-chat-model">{t("memory.providerBinding.chatModel")}</Label>
                  {models.length > 0 ? (
                    <Select value={chatModel} onValueChange={setChatModel} disabled={readOnly || !chatProviderId}>
                      <SelectTrigger id="memory-chat-model">
                        <SelectValue placeholder={t("memory.providerBinding.selectModel")} />
                      </SelectTrigger>
                      <SelectContent>
                        {models.map((model) => (
                          <SelectItem key={model} value={model}>
                            {model}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <>
                      <Input
                        id="memory-chat-model"
                        value={chatModel}
                        onChange={(event) => setChatModel(event.target.value)}
                        disabled={readOnly || !chatProviderId}
                        placeholder={t("memory.providerBinding.chatModelPlaceholder")}
                      />
                      {chatProviderId && (
                        <p className="text-xs text-muted-foreground">
                          {t("memory.providerBinding.manualChatModelHint")}
                        </p>
                      )}
                    </>
                  )}
                </div>

                <div className="grid gap-2">
                  <Label>{t("memory.providerBinding.embeddingProvider")}</Label>
                  <Select value={embeddingProviderId} onValueChange={setEmbeddingProviderId} disabled={readOnly}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("memory.providerBinding.selectEmbeddingProvider")} />
                    </SelectTrigger>
                    <SelectContent>
                      {embeddingProviders.map((provider) => (
                        <SelectItem key={provider.id} value={provider.id}>
                          {provider.name} · {t("memory.providerBinding.dimensions", { dimensions: provider.dim })}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedEmbeddingProvider && binding?.embedding_locked && (
                    <p className="text-xs text-muted-foreground">
                      {t("memory.providerBinding.lockedHint", {
                        model: binding.embedding_model,
                        dimensions: binding.embedding_dim,
                      })}
                    </p>
                  )}
                </div>
              </div>

              <Button
                type="button"
                className="gap-1.5"
                disabled={readOnly || isSaving}
                onClick={() => void save()}
              >
                {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                {t("memory.providerBinding.save")}
              </Button>
            </>
          )}
        </>
      )}
    </div>
  )
}
