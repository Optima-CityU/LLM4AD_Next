import {
  Database,
  Loader2,
  Pencil,
  Plus,
  Power,
  PowerOff,
  Trash2,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
import { Textarea } from "@/components/ui/textarea"
import TagInput from "@/components/Memory/TagInput"
import { cn } from "@/lib/utils"
import { authFetch } from "@/utils/auth"

type MemoryType =
  | "good_algorithm"
  | "error_reflection"
  | "domain_knowledge"
  | "general_insight"

interface MemoryCard {
  id: string
  type: MemoryType | string
  title: string
  content: string
  enabled: boolean
  source: string
  tags: string[]
  score?: number | null
  generation?: number | null
  algorithm_id?: string | null
  metadata?: Record<string, unknown>
  "readonly"?: MemoryCardReadonlyInfo
}

interface MemoryCardReadonlyInfo {
  source: string
  status: string
  entity_name?: string | null
  property_name?: string | null
  property_time?: string | null
  last_update_at?: string | null
  event_time?: string | null
  source_timestamp?: string | null
}

interface MemoryDraft {
  id?: string
  type: MemoryType
  title: string
  content: string
  enabled: boolean
  tags: string[]
}

const DEFAULT_DRAFT: MemoryDraft = {
  type: "general_insight",
  title: "",
  content: "",
  enabled: true,
  tags: [],
}

const TYPE_OPTIONS: MemoryType[] = [
  "good_algorithm",
  "error_reflection",
  "domain_knowledge",
  "general_insight",
]

function toDraft(card: MemoryCard): MemoryDraft {
  return {
    id: card.id,
    type: TYPE_OPTIONS.includes(card.type as MemoryType)
      ? (card.type as MemoryType)
      : "general_insight",
    title: card.title,
    content: card.content,
    enabled: card.enabled,
    tags: card.tags,
  }
}

function readOnlyRows(card: MemoryCard | null): Array<[string, string]> {
  if (!card) return []
  const info = card.readonly
  const rows: Array<[string, string]> = [
    ["记忆 ID", card.id],
    ["来源", info?.source || card.source],
    ["状态", info?.status || (card.enabled ? "active" : "archived")],
    ["实体", info?.entity_name || ""],
    ["属性", info?.property_name || ""],
    ["属性时间", info?.property_time || ""],
    ["更新时间", info?.last_update_at || ""],
    ["事件时间", info?.event_time || ""],
    ["来源时间", info?.source_timestamp || ""],
  ]
  return rows.filter(([, value]) => value.trim())
}

export default function TaskMemoryPanel({ taskId }: { taskId: string }) {
  const { t } = useTranslation()
  const [cards, setCards] = useState<MemoryCard[]>([])
  const [draft, setDraft] = useState<MemoryDraft>(DEFAULT_DRAFT)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const togglingIdsRef = useRef<Set<string>>(new Set())
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set())

  const baseUrl = import.meta.env.VITE_API_URL || ""
  const endpoint = `${baseUrl}/api/v1/llm4ad/tasks/${taskId}/memory`

  const loadCards = useCallback(async () => {
    setIsLoading(true)
    try {
      const response = await authFetch(endpoint)
      if (!response.ok) {
        throw new Error(t("evolution.memory.loadFailed"))
      }
      const payload = await response.json()
      setCards(Array.isArray(payload) ? payload : payload.items ?? [])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("evolution.memory.loadFailed"))
    } finally {
      setIsLoading(false)
    }
  }, [endpoint, t])

  useEffect(() => {
    void loadCards()
  }, [loadCards])

  const sortedCards = useMemo(
    () =>
      [...cards].sort((a, b) => {
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
        return a.title.localeCompare(b.title)
      }),
    [cards],
  )
  const editingCard = useMemo(
    () => (editingId ? cards.find((card) => card.id === editingId) ?? null : null),
    [cards, editingId],
  )

  const resetDraft = () => {
    setDraft(DEFAULT_DRAFT)
    setEditingId(null)
  }

  const replaceCard = (updatedCard: MemoryCard) => {
    setCards((current) => {
      const exists = current.some((card) => card.id === updatedCard.id)
      if (!exists) return [updatedCard, ...current]
      return current.map((card) => (card.id === updatedCard.id ? updatedCard : card))
    })
  }

  const removeCard = (cardId: string) => {
    setCards((current) => current.filter((card) => card.id !== cardId))
  }

  const setCardToggling = (cardId: string, value: boolean) => {
    if (value) {
      togglingIdsRef.current.add(cardId)
    } else {
      togglingIdsRef.current.delete(cardId)
    }
    setTogglingIds(new Set(togglingIdsRef.current))
  }

  const saveDraft = async () => {
    if (!draft.title.trim() || !draft.content.trim()) {
      toast.error(t("evolution.memory.required"))
      return
    }
    setIsSaving(true)
    try {
      const response = await authFetch(
        editingId ? `${endpoint}/${editingId}` : endpoint,
        {
          method: editingId ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: editingId ?? draft.id,
            type: draft.type,
            title: draft.title.trim(),
            content: draft.content.trim(),
            enabled: draft.enabled,
            tags: draft.tags,
          }),
        },
      )
      if (!response.ok) {
        throw new Error(t("evolution.memory.saveFailed"))
      }
      const updatedCard = (await response.json()) as MemoryCard
      toast.success(t("evolution.memory.saved"))
      resetDraft()
      replaceCard(updatedCard)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("evolution.memory.saveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  const deleteCard = async (card: MemoryCard) => {
    try {
      const response = await authFetch(`${endpoint}/${card.id}`, { method: "DELETE" })
      if (!response.ok) {
        throw new Error(t("evolution.memory.deleteFailed"))
      }
      toast.success(t("evolution.memory.deleted"))
      removeCard(card.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("evolution.memory.deleteFailed"))
    }
  }

  const toggleCard = async (card: MemoryCard) => {
    if (togglingIdsRef.current.has(card.id)) {
      return
    }
    setCardToggling(card.id, true)
    try {
      const response = await authFetch(`${endpoint}/${card.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: card.type,
          title: card.title,
          content: card.content,
          enabled: !card.enabled,
          tags: card.tags,
        }),
      })
      if (!response.ok) {
        throw new Error(t("evolution.memory.saveFailed"))
      }
      const updatedCard = (await response.json()) as MemoryCard
      replaceCard(updatedCard)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("evolution.memory.saveFailed"))
    } finally {
      setCardToggling(card.id, false)
    }
  }

  return (
    <div className="h-full overflow-auto rounded-lg border bg-card/60">
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b bg-card/95 px-4 py-3 backdrop-blur">
        <Database className="size-4 text-primary" />
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{t("evolution.memory.title")}</h2>
          <p className="truncate text-xs text-muted-foreground">
            {t("evolution.memory.description")}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="ml-auto gap-1.5"
          onClick={() => {
            setDraft(DEFAULT_DRAFT)
            setEditingId(null)
          }}
        >
          <Plus className="size-3.5" />
          {t("evolution.memory.new")}
        </Button>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-3 rounded-md border bg-background/50 p-3">
          <div className="grid gap-2">
            <Label htmlFor="memory-title">{t("evolution.memory.fields.title")}</Label>
            <Input
              id="memory-title"
              value={draft.title}
              onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
              placeholder={t("evolution.memory.placeholders.title")}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="memory-type">{t("evolution.memory.fields.type")}</Label>
            <Select
              value={draft.type}
              onValueChange={(value) =>
                setDraft((current) => ({ ...current, type: value as MemoryType }))
              }
            >
              <SelectTrigger id="memory-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TYPE_OPTIONS.map((type) => (
                  <SelectItem key={type} value={type}>
                    {t(`evolution.memory.types.${type}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="memory-content">{t("evolution.memory.fields.content")}</Label>
            <Textarea
              id="memory-content"
              className="min-h-28"
              value={draft.content}
              onChange={(event) => setDraft((current) => ({ ...current, content: event.target.value }))}
              placeholder={t("evolution.memory.placeholders.content")}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="memory-tags">{t("evolution.memory.fields.tags")}</Label>
            <TagInput
              id="memory-tags"
              value={draft.tags}
              onChange={(tags) => setDraft((current) => ({ ...current, tags }))}
              placeholder={t("evolution.memory.placeholders.tags")}
            />
          </div>
          {editingCard && readOnlyRows(editingCard).length > 0 && (
            <div className="grid gap-3 rounded-md border bg-muted/20 p-3">
              <div>
                <p className="text-sm font-medium">系统信息</p>
                <p className="text-xs text-muted-foreground">
                  这些字段由 MindMemOS 管理，仅用于检查来源和时间，不会随编辑保存。
                </p>
              </div>
              <div className="grid gap-2">
                {readOnlyRows(editingCard).map(([label, value]) => (
                  <div key={label} className="grid gap-1.5">
                    <Label className="text-xs text-muted-foreground">{label}</Label>
                    <Input value={value} readOnly className="h-8 bg-background/70 text-xs" />
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              className="gap-1.5"
              disabled={isSaving}
              onClick={() => void saveDraft()}
            >
              {isSaving && <Loader2 className="size-3.5 animate-spin" />}
              {editingId ? t("common.save") : t("evolution.memory.add")}
            </Button>
            {editingId && (
              <Button type="button" size="sm" variant="outline" onClick={resetDraft}>
                {t("common.cancel")}
              </Button>
            )}
          </div>
        </div>

        <div className="min-h-[320px] space-y-3">
          {isLoading ? (
            <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 size-4 animate-spin" />
              {t("evolution.memory.loading")}
            </div>
          ) : sortedCards.length === 0 ? (
            <div className="flex h-48 flex-col items-center justify-center rounded-md border border-dashed bg-background/40 text-center">
              <Database className="mb-2 size-5 text-muted-foreground" />
              <p className="text-sm font-medium">{t("evolution.memory.empty")}</p>
              <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                {t("evolution.memory.emptyHint")}
              </p>
            </div>
          ) : (
            sortedCards.map((card) => (
              <article
                key={card.id}
                className={cn(
                  "rounded-md border bg-background/60 p-3 transition-colors",
                  !card.enabled && "opacity-60",
                  togglingIds.has(card.id) && "opacity-80",
                )}
              >
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-sm font-semibold">{card.title}</h3>
                      <Badge variant="outline">{t(`evolution.memory.types.${card.type}`)}</Badge>
                      <Badge variant={card.enabled ? "secondary" : "outline"}>
                        {card.enabled
                          ? t("evolution.memory.enabled")
                          : t("evolution.memory.disabled")}
                      </Badge>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                      {card.content}
                    </p>
                    {card.tags.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {card.tags.map((tag) => (
                          <Badge key={tag} variant="outline">
                            {tag}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      disabled={togglingIds.has(card.id)}
                      aria-label={
                        togglingIds.has(card.id)
                          ? card.enabled
                            ? "正在禁用记忆"
                            : "正在启用记忆"
                          : card.enabled
                            ? "禁用记忆"
                            : "启用记忆"
                      }
                      title={
                        card.enabled
                          ? "禁用：后续任务不会注入这条记忆"
                          : "启用：后续任务可检索并注入这条记忆"
                      }
                      className="transition-opacity"
                      onClick={() => void toggleCard(card)}
                    >
                      {togglingIds.has(card.id) ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : card.enabled ? (
                        <PowerOff className="size-4" />
                      ) : (
                        <Power className="size-4" />
                      )}
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      onClick={() => {
                        setDraft(toDraft(card))
                        setEditingId(card.id)
                      }}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="text-destructive hover:text-destructive"
                      onClick={() => void deleteCard(card)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>
              </article>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
