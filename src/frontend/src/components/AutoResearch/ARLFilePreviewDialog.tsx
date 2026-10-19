import {
  AlertTriangle,
  Code,
  Copy,
  Eye,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
} from "lucide-react"
import {
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { ARLRole } from "@/hooks/useARLWebSocket"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import { cn } from "@/lib/utils"
import { useARLFiles } from "./ARLSessionContext"
import ArtifactCodeEditor from "./ArtifactCodeEditor"
import { FileKindIcon, formatSize } from "./ArtifactPreviewDialog"
import { fmtDateTimeSeconds } from "./ArtifactTree"
import type { ARLFsFile, ARLFsWriteResult } from "./arlFiles"

/** Which file is being previewed, and in which container. */
export interface ARLPreviewTarget {
  role: ARLRole
  path: string
}

/** Longest markdown rendered as-is; past this the tail is cut to keep the DOM light. */
const TEXT_RENDER_MAX = 400_000

/** Version the next save must match; the daemon rejects it if the file moved on. */
interface ExpectedVersion {
  modifiedAt: string
  revision?: string
}

function isMarkdown(path: string): boolean {
  return /\.(md|markdown)$/i.test(path)
}

/**
 * Modal preview and editor for one file in a container workspace.
 *
 * Text opens in the shared Monaco editor (markdown opens rendered; its source
 * toggle switches to the editor), images inline, other binaries as a notice.
 * Oversized files are refused by the daemon and show its message.
 *
 * The agents may rewrite these files at any time, so saving is guarded by the
 * version read when the file was opened: if it changed since, the daemon
 * reports a conflict and the user picks overwrite or reload, instead of one
 * side silently clobbering the other.
 */
export default function ARLFilePreviewDialog({
  target,
  onOpenChange,
  onSaved,
}: {
  target: ARLPreviewTarget | null
  onOpenChange: (open: boolean) => void
  /** Called after a successful save, e.g. to refresh size / mtime in the tree. */
  onSaved?: (target: ARLPreviewTarget) => void
}) {
  const { t } = useTranslation()
  const { request } = useARLFiles()
  const [, copy] = useCopyToClipboard()
  const mdId = useId()
  const [file, setFile] = useState<ARLFsFile | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showSource, setShowSource] = useState(false)
  /** Unsaved edits; null means the editor shows the file as read. */
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  /** Disk version found by a rejected save; set while the conflict bar shows. */
  const [conflict, setConflict] = useState<ExpectedVersion | null>(null)

  const role = target?.role
  const path = target?.path

  // Cancels the in-flight read. Every load (effect or manual reload) goes
  // through here, so a late reply for a previous target never lands.
  const cancelLoadRef = useRef<() => void>(() => {})

  const load = useCallback(() => {
    cancelLoadRef.current()
    if (!role || !path) {
      cancelLoadRef.current = () => {}
      return cancelLoadRef.current
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    request(role, "read", { path })
      .then((data) => {
        if (cancelled) return
        setFile((data.file as ARLFsFile) ?? null)
        setDraft(null)
        setConflict(null)
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setFile(null)
          setError(err.message)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    const cancel = () => {
      cancelled = true
    }
    cancelLoadRef.current = cancel
    return cancel
  }, [request, role, path])

  useEffect(() => {
    setFile(null)
    setDraft(null)
    setConflict(null)
    setShowSource(false)
    return load()
  }, [load])

  const name = path ? (path.split("/").pop() ?? path) : ""
  const markdown = !!path && isMarkdown(path)
  const text =
    file?.kind === "text" && typeof file.content === "string"
      ? file.content
      : null
  const value = draft ?? text ?? ""
  const dirty = draft !== null && draft !== text
  const rendered = markdown && !showSource
  const truncated = rendered && value.length > TEXT_RENDER_MAX

  const confirmDiscard = () =>
    !dirty || window.confirm(t("autoResearch.arl.files.edit.discardConfirm"))

  const reload = () => {
    if (confirmDiscard()) load()
  }

  const save = async (expected?: ExpectedVersion) => {
    if (!role || !path || !file || draft === null || saving) return
    const content = draft
    const version = expected ?? {
      modifiedAt: file.modifiedAt,
      revision: file.revision,
    }
    setSaving(true)
    try {
      const data = await request(role, "write", {
        path,
        content,
        expected_modified_at: version.modifiedAt,
        expected_revision: version.revision,
      })
      const result = data.write as ARLFsWriteResult | undefined
      if (result?.status === "conflict") {
        setConflict({
          modifiedAt: result.version.modifiedAt ?? "",
          revision: result.version.revision,
        })
        return
      }
      if (result?.status !== "written") throw new Error("unexpected response")
      setFile({
        ...file,
        content,
        size: result.size,
        modifiedAt: result.modifiedAt,
        revision: result.revision,
      })
      // Typing may have continued during the save: keep any newer draft.
      setDraft((cur) => (cur === content ? null : cur))
      setConflict(null)
      toast.success(t("autoResearch.arl.files.edit.saved"))
      onSaved?.({ role, path })
    } catch (err) {
      toast.error(
        `${t("autoResearch.arl.files.edit.saveFailed")}: ${(err as Error).message}`,
      )
    } finally {
      setSaving(false)
    }
  }

  // 捕获阶段拦 Ctrl/Cmd+S：Monaco 不处理它，放过去就成了浏览器的「另存网页」。
  const onEditorKeyDown = (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault()
      if (dirty && !conflict) void save()
    }
  }

  return (
    <Dialog
      open={!!target}
      onOpenChange={(open) => {
        if (open || confirmDiscard()) onOpenChange(open)
      }}
    >
      <DialogContent className="flex h-[80vh] max-h-[80vh] flex-col gap-3 p-4 sm:max-w-4xl">
        <DialogHeader className="min-w-0 pr-8">
          <DialogTitle className="flex min-w-0 items-center gap-2 text-sm">
            <FileKindIcon name={name} />
            <span className="truncate">{name}</span>
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
              {role ? t(`autoResearch.arl.${role}`) : ""}
            </span>
            {dirty && (
              <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-normal text-amber-600 dark:text-amber-400">
                <AlertTriangle className="size-3" />
                {t("autoResearch.arl.files.edit.unsaved")}
              </span>
            )}
          </DialogTitle>
          <DialogDescription className="flex min-w-0 items-center gap-2 text-[11px]">
            <span className="min-w-0 truncate font-mono">{path}</span>
            {file && (
              <span className="shrink-0 tabular-nums text-muted-foreground/70">
                {formatSize(file.size)}
                {file.modifiedAt &&
                  ` · ${fmtDateTimeSeconds(file.modifiedAt) ?? ""}`}
              </span>
            )}
            <span className="ml-auto flex shrink-0 items-center gap-0.5">
              {markdown && text !== null && (
                <HeaderButton
                  onClick={() => setShowSource((v) => !v)}
                  label={
                    showSource
                      ? t("autoResearch.arl.files.preview.rendered")
                      : t("autoResearch.arl.files.preview.source")
                  }
                >
                  {showSource ? (
                    <Eye className="size-3.5" />
                  ) : (
                    <Code className="size-3.5" />
                  )}
                </HeaderButton>
              )}
              <HeaderButton
                onClick={() => {
                  if (!path) return
                  void copy(path).then((ok) =>
                    ok
                      ? toast.success(t("autoResearch.arl.files.pathCopied"))
                      : undefined,
                  )
                }}
                label={t("autoResearch.arl.files.actions.copyPath")}
              >
                <Copy className="size-3.5" />
              </HeaderButton>
              <HeaderButton
                onClick={reload}
                disabled={loading || saving}
                label={t("autoResearch.arl.files.refresh")}
              >
                <RefreshCw
                  className={cn("size-3.5", loading && "animate-spin")}
                />
              </HeaderButton>
            </span>
          </DialogDescription>
        </DialogHeader>

        {conflict && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
            <AlertTriangle className="size-3.5 shrink-0" />
            <span className="min-w-0 flex-1">
              {t("autoResearch.arl.files.edit.conflict")}
            </span>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={saving}
              onClick={() => void save(conflict)}
            >
              {t("autoResearch.arl.files.edit.overwrite")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={saving || loading}
              onClick={() => load()}
            >
              {t("autoResearch.arl.files.edit.reloadLatest")}
            </Button>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-border/60 bg-muted/20">
          {loading && !file ? (
            <div className="grid h-full place-items-center text-xs text-muted-foreground">
              <span className="flex items-center gap-2">
                <Loader2 className="size-4 animate-spin" />
                {t("autoResearch.arl.files.preview.loading")}
              </span>
            </div>
          ) : error ? (
            <div className="grid h-full place-items-center p-6 text-center text-xs text-destructive">
              <span className="flex max-w-md items-start gap-2">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <span className="break-words">
                  {t("autoResearch.arl.files.preview.failed")}: {error}
                </span>
              </span>
            </div>
          ) : file?.kind === "image" && file.content ? (
            <div className="grid min-h-full place-items-center p-4">
              <img
                src={`data:${file.mimeType || "image/png"};base64,${file.content}`}
                alt={name}
                className="max-h-full max-w-full object-contain"
              />
            </div>
          ) : text !== null && rendered ? (
            <div className="prose prose-sm dark:prose-invert max-w-none break-words px-5 py-4">
              <Markdown
                remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                components={makeMarkdownComponents(mdId)}
              >
                {truncated ? value.slice(0, TEXT_RENDER_MAX) : value}
              </Markdown>
            </div>
          ) : text !== null && path ? (
            <div className="h-full" onKeyDownCapture={onEditorKeyDown}>
              <ArtifactCodeEditor
                key={path}
                filePath={path}
                value={value}
                onChange={setDraft}
                className="min-h-0"
              />
            </div>
          ) : file ? (
            <div className="grid h-full place-items-center p-6 text-center text-xs text-muted-foreground">
              {t("autoResearch.arl.files.preview.binary", {
                size: formatSize(file.size),
              })}
            </div>
          ) : null}
        </div>

        {truncated && (
          <p className="text-[11px] text-muted-foreground">
            {t("autoResearch.arl.files.preview.truncated")}
          </p>
        )}
        {text !== null && !rendered && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-muted-foreground">
              {t("autoResearch.arl.files.edit.hint")}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1 text-xs"
                disabled={!dirty || saving}
                onClick={() => {
                  setDraft(null)
                  setConflict(null)
                }}
              >
                <RotateCcw className="size-3.5" />
                {t("autoResearch.arl.files.edit.revert")}
              </Button>
              <Button
                size="sm"
                className="h-7 gap-1 text-xs"
                disabled={!dirty || saving || !!conflict}
                onClick={() => void save()}
              >
                {saving ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Save className="size-3.5" />
                )}
                {t("autoResearch.arl.files.edit.save")}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Small icon button in the dialog header. */
function HeaderButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-primary/10 hover:text-primary disabled:opacity-40"
    >
      {children}
    </button>
  )
}
