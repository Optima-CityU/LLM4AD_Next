import { Loader2, LockKeyhole, Pencil, Save, X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
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
}: {
  binding?: MemoryProviderBinding | null
  onSaved?: () => void
}) {
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
    setChatProviderId(providerId)
    const provider = providers.find((item) => item.id === providerId)
    const nextModels = providerModels(provider)
    setChatModel(nextModels[0] ?? "")
  }

  const save = async () => {
    if (!chatProviderId || !chatModel || !embeddingProviderId) {
      toast.error("请选择 Chat 模型和 Embedding 配置")
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
        throw new Error(payload?.detail || "保存记忆模型绑定失败")
      }
      const payload = (await response.json()) as MemoryProviderBinding
      setBinding(payload)
      setIsEditing(false)
      toast.success("记忆模型绑定已保存")
      onSaved?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存记忆模型绑定失败")
    } finally {
      setIsSaving(false)
    }
  }

  const isProviderLoading = providersQuery.isLoading || embeddingProvidersQuery.isLoading
  const providerLoadFailed = providersQuery.isError || embeddingProvidersQuery.isError
  const hasProviderChoices = providers.length > 0 && embeddingProviders.length > 0

  return (
    <div className="space-y-4 rounded-lg border bg-card/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">记忆模型绑定</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Chat 用于记忆抽取，Embedding 用于检索。Embedding 首次绑定后模型和维度会锁定。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {binding?.embedding_locked && (
            <Badge variant="secondary" className="gap-1.5 whitespace-nowrap">
              <LockKeyhole className="size-3" />
              已锁定 {binding.embedding_dim} 维
            </Badge>
          )}
          {isEditing ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="gap-1.5"
              onClick={() => setIsEditing(false)}
            >
              <X className="size-4" />
              取消
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant={binding?.configured ? "outline" : "default"}
              className="gap-1.5"
              onClick={() => setIsEditing(true)}
            >
              <Pencil className="size-4" />
              {binding?.configured ? "编辑绑定" : "绑定模型"}
            </Button>
          )}
        </div>
      </div>

      {!isEditing && (
        <div className="grid gap-2 rounded-md border bg-muted/20 px-3 py-2 text-xs">
          {binding?.configured ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">Chat 模型</span>
                <span className="truncate font-medium">{binding.chat_model || "未记录"}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">Embedding</span>
                <span className="truncate font-medium">
                  {binding.embedding_model || "未记录"}
                  {binding.embedding_dim ? ` · ${binding.embedding_dim} 维` : ""}
                </span>
              </div>
            </>
          ) : (
            <div className="text-muted-foreground">
              当前用户尚未绑定记忆模型。未绑定时不会加载供应商列表，也不会读取默认注入策略。
            </div>
          )}
        </div>
      )}

      {isEditing && (
        <>
          {isProviderLoading && (
            <div className="flex min-h-[160px] items-center justify-center rounded-md border bg-muted/20 text-sm text-muted-foreground">
              <Loader2 className="mr-2 size-4 animate-spin" />
              正在加载可绑定模型...
            </div>
          )}

          {!isProviderLoading && providerLoadFailed && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              加载模型供应商失败，请先检查模型配置服务是否正常。
            </div>
          )}

          {!isProviderLoading && !providerLoadFailed && !hasProviderChoices && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              还没有可用的 Chat 或 Embedding 配置。请先在模型供应商页面配置后再绑定记忆模型。
            </div>
          )}

          {!isProviderLoading && !providerLoadFailed && hasProviderChoices && (
            <>
              <div className="grid gap-3">
                <div className="grid gap-2">
                  <Label>Chat 供应商</Label>
                  <Select value={chatProviderId} onValueChange={changeChatProvider}>
                    <SelectTrigger>
                      <SelectValue placeholder="选择 Chat 供应商" />
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
                  <Label htmlFor="memory-chat-model">Chat 模型</Label>
                  {models.length > 0 ? (
                    <Select value={chatModel} onValueChange={setChatModel} disabled={!chatProviderId}>
                      <SelectTrigger id="memory-chat-model">
                        <SelectValue placeholder="选择模型" />
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
                        disabled={!chatProviderId}
                        placeholder="输入 Chat 模型名称"
                      />
                      {chatProviderId && (
                        <p className="text-xs text-muted-foreground">
                          当前供应商没有配置模型列表，请手动输入 MindMemOS 调用的 Chat 模型名称。
                        </p>
                      )}
                    </>
                  )}
                </div>

                <div className="grid gap-2">
                  <Label>Embedding 配置</Label>
                  <Select value={embeddingProviderId} onValueChange={setEmbeddingProviderId}>
                    <SelectTrigger>
                      <SelectValue placeholder="选择 Embedding 配置" />
                    </SelectTrigger>
                    <SelectContent>
                      {embeddingProviders.map((provider) => (
                        <SelectItem key={provider.id} value={provider.id}>
                          {provider.name} · {provider.dim} 维
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedEmbeddingProvider && binding?.embedding_locked && (
                    <p className="text-xs text-muted-foreground">
                      当前记忆空间锁定为 {binding.embedding_model} / {binding.embedding_dim} 维；保存时只能更新同一模型和维度的凭据。
                    </p>
                  )}
                </div>
              </div>

              <Button
                type="button"
                className="gap-1.5"
                disabled={isSaving}
                onClick={() => void save()}
              >
                {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                保存绑定
              </Button>
            </>
          )}
        </>
      )}
    </div>
  )
}
