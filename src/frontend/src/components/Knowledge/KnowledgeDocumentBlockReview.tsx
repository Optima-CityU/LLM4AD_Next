import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Loader2,
  Pencil,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

import type { KnowledgeDocument, KnowledgeContent } from "./types"
import {
  MemoryCardDeleteDialog,
  MemoryCardEditorDialog,
  MemoryCardTile,
  memoryCardToDraft,
} from "../Memory/MemoryCardPresentation"
import { DEFAULT_MEMORY_DRAFT, type MemoryCard, type MemoryCardDraft } from "../Memory/types"

type DocumentBlockPatch = { title: string; content: string }

export default function KnowledgeDocumentBlockReview({
  documents,
  generatedMemories,
  busy,
  insertProgress,
  canRefine,
  onLoadContent,
  onSave,
  onRefine,
  onRestart,
  insertedDocumentIds,
  onInsert,
  onEditMemory,
  onDeleteMemory,
}: {
  documents: KnowledgeDocument[]
  generatedMemories: MemoryCard[]
  busy: boolean
  insertProgress: { stage: string; percent: number; message: string } | null
  canRefine: boolean
  onLoadContent: (id: string) => Promise<KnowledgeContent>
  onSave: (id: string, patch: DocumentBlockPatch) => Promise<void>
  onRefine: (instruction: string) => Promise<void>
  onRestart: () => void
  insertedDocumentIds: string[]
  onInsert: (ids: string[]) => Promise<void>
  onEditMemory: (id: string, patch: Pick<MemoryCard, "type" | "title" | "content" | "structured_content" | "tags">) => Promise<void>
  onDeleteMemory: (id: string) => Promise<void>
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [contents, setContents] = useState<Record<string, string>>({})
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<DocumentBlockPatch | null>(null)
  const [refineOpen, setRefineOpen] = useState(false)
  const [refinement, setRefinement] = useState("")
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [editingMemory, setEditingMemory] = useState<MemoryCard | null>(null)
  const [memoryDraft, setMemoryDraft] = useState<MemoryCardDraft>(DEFAULT_MEMORY_DRAFT)
  const [deleteMemory, setDeleteMemory] = useState<MemoryCard | null>(null)
  const [memoryMutationBusy, setMemoryMutationBusy] = useState(false)

  useEffect(() => {
    setExpanded(new Set())
    setContents({})
    setEditingId(null)
    setDraft(null)
    setSelected((current) => new Set(
      [...current].filter((id) =>
        documents.some((item) => item.id === id) && !insertedDocumentIds.includes(id),
      ),
    ))
  }, [documents.map((item) => item.id).join(","), insertedDocumentIds.join(",")])

  const selectable = documents.filter((item) => !insertedDocumentIds.includes(item.id))

  const loadContent = async (id: string) => {
    if (contents[id] !== undefined) return contents[id]
    setLoadingId(id)
    try {
      const payload = await onLoadContent(id)
      setContents((current) => ({ ...current, [id]: payload.content }))
      return payload.content
    } finally {
      setLoadingId((current) => (current === id ? null : current))
    }
  }

  const toggleDocument = async (id: string) => {
    if (expanded.has(id)) {
      setExpanded((current) => {
        const next = new Set(current)
        next.delete(id)
        return next
      })
      return
    }
    try {
      await loadContent(id)
      setExpanded((current) => new Set(current).add(id))
    } catch {
      // The parent presents the API error.
    }
  }

  const beginEdit = async (item: KnowledgeDocument) => {
    try {
      const content = await loadContent(item.id)
      setExpanded((current) => new Set(current).add(item.id))
      setEditingId(item.id)
      setDraft({ title: item.title, content })
    } catch {
      // The parent presents the API error.
    }
  }

  const save = async () => {
    if (!editingId || !draft || !draft.title.trim() || !draft.content.trim()) return
    try {
      const patch = { title: draft.title.trim(), content: draft.content }
      await onSave(editingId, patch)
      setContents((current) => ({ ...current, [editingId]: patch.content }))
      setEditingId(null)
      setDraft(null)
    } catch {
      // Keep the editor open so the user can retry.
    }
  }

  const beginMemoryEdit = (memory: MemoryCard) => {
    setEditingMemory(memory)
    setMemoryDraft(memoryCardToDraft(memory))
  }

  const saveMemory = async () => {
    if (!editingMemory || !memoryDraft.title.trim() || !memoryDraft.structured_content.description.trim() || memoryDraft.structured_content.content.length === 0) return
    setMemoryMutationBusy(true)
    try {
      await onEditMemory(editingMemory.id, {
        type: memoryDraft.type,
        title: memoryDraft.title.trim(),
        content: memoryDraft.content.trim(),
        structured_content: memoryDraft.structured_content,
        tags: memoryDraft.tags,
      })
      setEditingMemory(null)
    } catch {
      // The parent presents the API error and the editor stays open for retry.
    } finally {
      setMemoryMutationBusy(false)
    }
  }

  const confirmDeleteMemory = async () => {
    if (!deleteMemory) return
    setMemoryMutationBusy(true)
    try {
      await onDeleteMemory(deleteMemory.id)
      setDeleteMemory(null)
    } catch {
      // The parent presents the API error and the confirmation stays open.
    } finally {
      setMemoryMutationBusy(false)
    }
  }

  return (
    <div className="w-full min-w-0 max-w-full space-y-3" data-testid="knowledge-document-block-review">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-56">
          <p className="text-xs font-semibold">{t("knowledge.documentBlocks.title")}</p>
          <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
            {t("knowledge.documentBlocks.description")}
          </p>
        </div>
        <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-1">
          <Button
            data-testid="knowledge-restart-extraction"
            size="sm"
            variant="ghost"
            onClick={onRestart}
            disabled={busy}
          >
            <RotateCcw className="mr-1 size-3.5" />
            {t("knowledge.documentBlocks.restart")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setRefineOpen(true)}
            disabled={busy || !canRefine}
          >
            <Sparkles className="mr-1 size-3.5" />
            {t("knowledge.documentBlocks.refine")}
          </Button>
        </div>
      </div>

      {documents.map((item, index) => {
        const isExpanded = expanded.has(item.id)
        const editing = editingId === item.id && draft
        const inserted = insertedDocumentIds.includes(item.id)
        return (
          <div key={item.id} className="overflow-hidden rounded-lg border bg-background">
            <div className="flex items-center gap-2 px-3 py-2.5">
              <Checkbox
                checked={inserted || selected.has(item.id)}
                disabled={busy || inserted}
                aria-label={t("knowledge.documentBlocks.select", { title: item.title })}
                onCheckedChange={(checked) => setSelected((current) => {
                  const next = new Set(current)
                  if (checked === true) next.add(item.id)
                  else next.delete(item.id)
                  return next
                })}
              />
              <button
                type="button"
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                onClick={() => void toggleDocument(item.id)}
                disabled={editingId === item.id}
              >
                <FileText className="size-3.5 shrink-0 text-primary" />
                <span className="truncate text-xs font-medium" title={item.title}>
                  {index + 1}. {item.title}
                </span>
                <Badge variant="secondary" className="ml-auto shrink-0 text-[10px] font-normal">
                  ≈ {item.estimated_tokens.toLocaleString()} tokens
                </Badge>
                {inserted && (
                  <Badge variant="outline" className="shrink-0 border-emerald-500/30 text-[10px] text-emerald-600">
                    <CheckCircle2 className="mr-1 size-3" />
                    {t("knowledge.documentBlocks.inserted")}
                  </Badge>
                )}
                {isExpanded ? <ChevronUp className="size-3.5 shrink-0" /> : <ChevronDown className="size-3.5 shrink-0" />}
              </button>
              <Button
                size="icon"
                variant="ghost"
                className="size-7 shrink-0"
                onClick={() => void beginEdit(item)}
                disabled={busy || loadingId === item.id}
                aria-label={t("knowledge.documentBlocks.edit")}
                title={t("knowledge.documentBlocks.edit")}
              >
                {loadingId === item.id ? <Loader2 className="size-3.5 animate-spin" /> : <Pencil className="size-3.5" />}
              </Button>
            </div>

            {isExpanded && (
              <div className="border-t bg-muted/10 p-3">
                {editing ? (
                  <div className="space-y-2">
                    <Input
                      value={draft.title}
                      maxLength={255}
                      onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                    />
                    <Textarea
                      value={draft.content}
                      maxLength={20 * 1024 * 1024}
                      className="min-h-56 resize-y font-mono text-xs leading-5"
                      onChange={(event) => setDraft({ ...draft, content: event.target.value })}
                    />
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => { setEditingId(null); setDraft(null) }}>
                        <X className="mr-1 size-3.5" />{t("common.cancel")}
                      </Button>
                      <Button size="sm" onClick={() => void save()} disabled={busy || !draft.title.trim() || !draft.content.trim()}>
                        <Save className="mr-1 size-3.5" />{t("knowledge.save")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                    {contents[item.id]}
                  </p>
                )}
              </div>
            )}
          </div>
        )
      })}

      {generatedMemories.length > 0 && (
        <section className="min-w-0 space-y-2 border-t pt-3" data-testid="knowledge-generated-memory-results">
          <div>
            <p className="text-xs font-semibold">{t("knowledge.documentBlocks.generatedTitle")}</p>
            <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
              {t("knowledge.documentBlocks.generatedDescription", { count: generatedMemories.length })}
            </p>
          </div>
          {generatedMemories.map((memory) => (
            <MemoryCardTile
              key={memory.id}
              card={memory}
              embedded
              className="w-full min-w-0 max-w-full"
              actions={(
                <div className="flex max-w-full shrink-0 flex-wrap justify-end gap-1">
                  <Button
                    data-testid="knowledge-generated-memory-edit"
                    size="icon"
                    variant="ghost"
                    className="size-7"
                    disabled={busy || memoryMutationBusy}
                    onClick={() => beginMemoryEdit(memory)}
                    aria-label={t("knowledge.documentBlocks.generatedEdit")}
                    title={t("knowledge.documentBlocks.generatedEdit")}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    data-testid="knowledge-generated-memory-delete"
                    size="icon"
                    variant="ghost"
                    className="size-7 text-destructive"
                    disabled={busy || memoryMutationBusy}
                    onClick={() => setDeleteMemory(memory)}
                    aria-label={t("knowledge.documentBlocks.generatedDelete")}
                    title={t("knowledge.documentBlocks.generatedDelete")}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              )}
            />
          ))}
        </section>
      )}

      {selectable.length > 0 && (
        <div className="sticky bottom-0 space-y-2 border-t bg-background/95 py-2 backdrop-blur">
          {insertProgress && (
            <div data-testid="knowledge-insert-progress" className="space-y-1.5 rounded-md border bg-muted/30 px-3 py-2">
              <div className="flex items-center justify-between gap-3 text-[11px]">
                <span className="min-w-0 truncate text-muted-foreground">{insertProgress.message}</span>
                <span className="shrink-0 font-medium tabular-nums text-primary">{insertProgress.percent}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-300"
                  style={{ width: `${insertProgress.percent}%` }}
                />
              </div>
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              className="text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => setSelected(
                selected.size === selectable.length
                  ? new Set()
                  : new Set(selectable.map((item) => item.id)),
              )}
            >
              {selected.size === selectable.length
                ? t("knowledge.documentBlocks.clearSelection")
                : t("knowledge.documentBlocks.selectAll")}
            </button>
            <Button
              data-testid="knowledge-insert-selected"
              size="sm"
              disabled={busy || selected.size === 0}
              onClick={() => void onInsert([...selected])
                .then(() => setSelected(new Set()))
                .catch(() => undefined)}
            >
              {busy ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <Sparkles className="mr-1 size-3.5" />}
              {t("knowledge.documentBlocks.insertSelected", { count: selected.size })}
            </Button>
          </div>
        </div>
      )}

      <Dialog open={refineOpen} onOpenChange={setRefineOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("knowledge.documentBlocks.refineTitle")}</DialogTitle>
            <DialogDescription>{t("knowledge.documentBlocks.refineDescription")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="knowledge-document-refinement">{t("knowledge.documentBlocks.refineInstruction")}</Label>
            <Textarea
              id="knowledge-document-refinement"
              value={refinement}
              maxLength={8000}
              className="min-h-36 resize-y"
              placeholder={t("knowledge.documentBlocks.refinePlaceholder")}
              onChange={(event) => setRefinement(event.target.value)}
            />
            <p className="text-right text-xs tabular-nums text-muted-foreground">{refinement.length}/8000</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRefineOpen(false)}>{t("common.cancel")}</Button>
            <Button
              disabled={busy || !refinement.trim()}
              onClick={() => void onRefine(refinement.trim()).then(() => {
                setRefineOpen(false)
                setRefinement("")
              }).catch(() => undefined)}
            >
              <Sparkles className="mr-1 size-4" />{t("knowledge.documentBlocks.startRefine")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <MemoryCardEditorDialog
        open={editingMemory !== null}
        scopeId="knowledge-generated"
        draft={memoryDraft}
        editingCard={editingMemory}
        saving={memoryMutationBusy}
        disabled={busy}
        onOpenChange={(open) => { if (!open) setEditingMemory(null) }}
        onDraftChange={setMemoryDraft}
        onCancel={() => setEditingMemory(null)}
        onSave={() => void saveMemory()}
      />

      <MemoryCardDeleteDialog
        card={deleteMemory}
        busy={memoryMutationBusy}
        onOpenChange={(open) => { if (!open) setDeleteMemory(null) }}
        onConfirm={() => void confirmDeleteMemory()}
      />
    </div>
  )
}
