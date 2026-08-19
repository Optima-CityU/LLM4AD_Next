import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { cn } from "@/lib/utils"

import TagInput from "./TagInput"
import {
  MEMORY_TYPES,
  type MemoryCard,
  type MemoryCardDraft,
} from "./types"

export function memoryCardToDraft(card: MemoryCard): MemoryCardDraft {
  const structuredContent = card.structured_content
    ? { ...card.structured_content, artifacts: card.structured_content.artifacts ?? [] }
    : {
        description: card.content.trim(),
        content: card.content.trim() ? [card.content.trim()] : [],
        artifacts: [],
      }
  return {
    id: card.id,
    type: MEMORY_TYPES.includes(card.type as (typeof MEMORY_TYPES)[number])
      ? card.type
      : "general_insight",
    title: card.title,
    content: card.content,
    structured_content: structuredContent,
    enabled: card.enabled,
    tags: card.tags,
  }
}

export function structuredContentText(value: MemoryCardDraft["structured_content"]) {
  const description = value.description.trim()
  const facts = value.content.map((item) => item.trim()).filter(Boolean)
  const artifacts = (value.artifacts ?? []).map((artifact) => {
    if (artifact.type === "code") return `\`\`\`${artifact.language ?? ""}\n${artifact.content}\n\`\`\``
    if (artifact.type === "formula") return `$$\n${artifact.content}\n$$`
    return artifact.content
  })
  return [description, facts.map((fact) => `- ${fact}`).join("\n"), ...artifacts]
    .filter(Boolean)
    .join("\n\n")
}

export function memoryTypeLabel(type: string, t: (key: string) => string) {
  const key = `memory.cardManager.types.${type}`
  const label = t(key)
  return label === key ? type : label
}

function readOnlyRows(card: MemoryCard | null, t: (key: string) => string): Array<[string, string]> {
  if (!card) return []
  const info = card.readonly
  const rows: Array<[string, string]> = [
    [t("memory.cardManager.readonly.id"), card.id],
    [t("memory.cardManager.readonly.source"), info?.source || card.source],
    [t("memory.cardManager.readonly.status"), info?.status || (card.enabled ? "active" : "archived")],
    [t("memory.cardManager.readonly.entity"), info?.entity_name || ""],
    [t("memory.cardManager.readonly.property"), info?.property_name || ""],
    [t("memory.cardManager.readonly.propertyTime"), info?.property_time || ""],
    [t("memory.cardManager.readonly.updatedAt"), info?.last_update_at || ""],
    [t("memory.cardManager.readonly.eventTime"), info?.event_time || ""],
    [t("memory.cardManager.readonly.sourceTime"), info?.source_timestamp || ""],
  ]
  return rows.filter(([, value]) => value.trim())
}

export function MemoryCardTile({
  card,
  actions,
  leading,
  embedded = false,
  className,
  dataTour,
}: {
  card: MemoryCard
  actions?: ReactNode
  leading?: ReactNode
  embedded?: boolean
  className?: string
  dataTour?: string
}) {
  const { t } = useTranslation()
  return (
    <article
      data-tour={dataTour}
      className={cn(
        "min-w-0 overflow-hidden flex flex-col rounded-md border bg-background/70 p-3 transition hover:border-primary/40",
        embedded ? "min-h-32" : "min-h-48",
        !card.enabled && "opacity-60",
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-start gap-2">
        {leading}
        <div className="min-w-32 flex-1">
          <h3 className="truncate text-sm font-semibold" title={card.title}>{card.title}</h3>
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge variant="outline">{memoryTypeLabel(card.type, t)}</Badge>
            {!card.enabled && <Badge variant="secondary">{t("memory.cardManager.status.disabled")}</Badge>}
          </div>
        </div>
        {actions && <div className="ml-auto max-w-full shrink-0">{actions}</div>}
      </div>
      {card.structured_content ? (
        <div className={cn("mt-3 break-words text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]", embedded ? "line-clamp-6" : "line-clamp-5")}>
          <p>{card.structured_content.description}</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {card.structured_content.content.map((fact) => <li key={fact}>{fact}</li>)}
          </ul>
        </div>
      ) : (
        <p className={cn("mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]", embedded ? "line-clamp-6" : "line-clamp-5")}>
          {card.content}
        </p>
      )}
      {card.tags.length > 0 && (
        <div className="mt-auto flex flex-wrap gap-1 pt-3">
          {card.tags.map((tag) => (
            <Badge key={tag} variant="secondary" className="text-[10px]">{tag}</Badge>
          ))}
        </div>
      )}
      {(card.structured_content?.artifacts ?? []).length > 0 && (
        <details
          data-testid="memory-card-source-artifacts"
          className={cn("min-w-0 max-w-full border-t pt-3", card.tags.length > 0 ? "mt-3" : "mt-auto")}
        >
          <summary className="max-w-full cursor-pointer select-none truncate text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
            {t("memory.cardManager.editor.sourceArtifactsCollapsed", {
              count: card.structured_content?.artifacts?.length ?? 0,
            })}
          </summary>
          <div className="min-w-0 max-w-full overflow-hidden">
            <div className="mt-3 grid min-w-0 max-w-full gap-3">
              {(card.structured_content?.artifacts ?? []).map((artifact) => (
                <div key={`${artifact.artifact_id}-${artifact.source_hash}`} className="min-w-0 max-w-full">
                  <div className="mb-1 flex flex-wrap items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground/80">
                    <span>{t(`memory.cardManager.artifacts.${artifact.type}`)}</span>
                    {artifact.language && <span>· {artifact.language}</span>}
                  </div>
                  <pre className="w-full min-w-0 max-w-full overflow-x-auto whitespace-pre rounded-md border bg-muted/40 p-3 text-xs leading-5 text-foreground">
                    <code>{artifact.content}</code>
                  </pre>
                </div>
              ))}
            </div>
          </div>
        </details>
      )}
    </article>
  )
}

export function MemoryCardEditorDialog({
  open,
  scopeId,
  draft,
  editingCard,
  saving,
  disabled,
  interactionLocked = false,
  onOpenChange,
  onDraftChange,
  onCancel,
  onSave,
}: {
  open: boolean
  scopeId: string
  draft: MemoryCardDraft
  editingCard: MemoryCard | null
  saving: boolean
  disabled: boolean
  interactionLocked?: boolean
  onOpenChange: (open: boolean) => void
  onDraftChange: (draft: MemoryCardDraft) => void
  onCancel: () => void
  onSave: () => void
}) {
  const { t } = useTranslation()
  const rows = readOnlyRows(editingCard, t)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"
        inert={interactionLocked || undefined}
      >
        <DialogHeader>
          <DialogTitle>{editingCard ? t("memory.cardManager.editor.editTitle") : t("memory.cardManager.editor.addTitle")}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor={`${scopeId}-memory-title`}>{t("memory.cardManager.editor.title")}</Label>
            <Input
              id={`${scopeId}-memory-title`}
              value={draft.title}
              onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
              placeholder={t("memory.cardManager.editor.titlePlaceholder")}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-[220px_1fr]">
            <div className="grid gap-2">
              <Label htmlFor={`${scopeId}-memory-type`}>{t("memory.cardManager.editor.type")}</Label>
              <Select value={draft.type} onValueChange={(value) => onDraftChange({ ...draft, type: value })}>
                <SelectTrigger id={`${scopeId}-memory-type`}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MEMORY_TYPES.map((type) => (
                    <SelectItem key={type} value={type}>{memoryTypeLabel(type, t)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor={`${scopeId}-memory-tags`}>{t("memory.cardManager.editor.tags")}</Label>
              <TagInput
                id={`${scopeId}-memory-tags`}
                value={draft.tags}
                onChange={(tags) => onDraftChange({ ...draft, tags })}
                placeholder={t("memory.cardManager.editor.tagsPlaceholder")}
              />
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor={`${scopeId}-memory-description`}>{t("memory.cardManager.editor.description")}</Label>
            <Textarea
              id={`${scopeId}-memory-description`}
              className="min-h-24 resize-y leading-6"
              value={draft.structured_content.description}
              onChange={(event) => {
                const structured_content = { ...draft.structured_content, description: event.target.value }
                onDraftChange({ ...draft, structured_content, content: structuredContentText(structured_content) })
              }}
              placeholder={t("memory.cardManager.editor.descriptionPlaceholder")}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor={`${scopeId}-memory-facts`}>{t("memory.cardManager.editor.facts")}</Label>
            <Textarea
              id={`${scopeId}-memory-facts`}
              className="min-h-52 resize-y leading-6"
              value={draft.structured_content.content.join("\n")}
              onChange={(event) => {
                const structured_content = {
                  ...draft.structured_content,
                  content: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean),
                }
                onDraftChange({ ...draft, structured_content, content: structuredContentText(structured_content) })
              }}
              placeholder={t("memory.cardManager.editor.factsPlaceholder")}
            />
          </div>
          {(draft.structured_content.artifacts ?? []).length > 0 && (
            <div data-testid="memory-card-editor-source-artifacts" className="grid gap-2 border-t pt-4">
              <Label>{t("memory.cardManager.editor.sourceArtifacts")}</Label>
              <p className="text-xs text-muted-foreground">
                {t("memory.cardManager.editor.sourceArtifactsDescription")}
              </p>
              <div className="grid gap-3">
                {(draft.structured_content.artifacts ?? []).map((artifact) => (
                  <div key={`${artifact.artifact_id}-${artifact.source_hash}`} className="min-w-0 rounded-md border bg-muted/20 p-3">
                    <div className="mb-2 text-xs font-medium">
                      {t(`memory.cardManager.artifacts.${artifact.type}`)}
                      {artifact.language ? ` · ${artifact.language}` : ""}
                    </div>
                    <pre className="max-h-72 max-w-full overflow-auto whitespace-pre rounded bg-background/80 p-3 text-xs leading-5">
                      <code>{artifact.content}</code>
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}
          {rows.length > 0 && (
            <div className="grid gap-3 rounded-md border bg-muted/20 p-3">
              <div>
                <p className="text-sm font-medium">{t("memory.cardManager.editor.systemInfo")}</p>
                <p className="text-xs text-muted-foreground">{t("memory.cardManager.editor.systemInfoDescription")}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {rows.map(([label, value]) => (
                  <div key={label} className="grid gap-1.5">
                    <Label className="text-xs text-muted-foreground">{label}</Label>
                    <Input value={value} readOnly className="h-8 bg-background/70 text-xs" />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <DialogFooter className="gap-2">
          <Button type="button" variant="outline" onClick={onCancel}>{t("memory.common.cancel")}</Button>
          <Button type="button" disabled={saving || disabled || !draft.title.trim() || !draft.structured_content.description.trim() || draft.structured_content.content.length === 0} onClick={onSave}>
            {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
            {editingCard ? t("memory.cardManager.editor.saveChanges") : t("memory.cardManager.editor.add")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function MemoryCardDeleteDialog({
  card,
  busy = false,
  interactionLocked = false,
  onOpenChange,
  onConfirm,
}: {
  card: MemoryCard | null
  busy?: boolean
  interactionLocked?: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}) {
  const { t } = useTranslation()
  return (
    <AlertDialog open={card !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent inert={interactionLocked || undefined}>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("memory.cardManager.delete.title")}</AlertDialogTitle>
          <AlertDialogDescription>{t("memory.cardManager.delete.description")}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{t("memory.common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={busy}
            onClick={(event) => {
              event.preventDefault()
              onConfirm()
            }}
          >
            {busy && <Loader2 className="mr-1 size-4 animate-spin" />}
            {t("memory.cardManager.delete.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
