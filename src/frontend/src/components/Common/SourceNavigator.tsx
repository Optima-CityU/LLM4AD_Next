import {
  FilePlus2,
  FolderPlus,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Upload,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { FileTreeNode } from "@/client"
import FileTreeView from "@/components/Evolution/TaskDetail/steps/FileTreeView"
import { Button } from "@/components/ui/button"
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
import { ScrollArea } from "@/components/ui/scroll-area"

export const PAPER_DIRECTORY_MARKER = ".llm4ad-directory"
const CREATABLE_PAPER_FILE_SUFFIXES = [
  ".typ",
  ".tex",
  ".bib",
  ".md",
  ".markdown",
  ".txt",
  ".csv",
  ".json",
  ".yaml",
  ".yml",
]

export const EDITABLE_SUFFIXES = [
  ".typ",
  ".tex",
  ".bib",
  ".bst",
  ".cls",
  ".sty",
  ".md",
  ".markdown",
  ".txt",
  ".csv",
  ".json",
  ".yaml",
  ".yml",
]

function errorMessage(error: unknown, fallback: string) {
  const candidate = error as { body?: { detail?: string }; message?: string }
  return candidate.body?.detail || candidate.message || fallback
}

function buildPaperFileTree(paths: string[]): FileTreeNode[] {
  const root: FileTreeNode[] = []
  for (const sourcePath of paths) {
    const parts = sourcePath.split("/").filter(Boolean)
    let children = root
    let currentPath = ""
    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part
      const isFile = index === parts.length - 1
      if (isFile && part === PAPER_DIRECTORY_MARKER) return
      let node = children.find((item) => item.name === part)
      if (!node) {
        node = {
          name: part,
          path: currentPath,
          type: isFile ? "file" : "directory",
          children: isFile ? null : [],
        }
        children.push(node)
      }
      if (!isFile) {
        node.children ??= []
        children = node.children
      }
    })
  }
  return root
}

export default function SourceNavigator({
  manifest,
  selectedPath,
  onSelect,
  onDelete,
  onCreateFile,
  onCreateFolder,
  onUpload,
  uploading,
  locked,
  collapsed,
  onToggle,
  collapsible = true,
  canDeletePath,
  tree: suppliedTree,
  sizeMap,
  selectableSuffixes = EDITABLE_SUFFIXES,
  uploadLabel,
  title,
  confirmDelete = false,
}: {
  manifest: string[]
  selectedPath: string | null
  onSelect: (path: string) => void
  onDelete?: (path: string) => void
  onCreateFile?: (path: string) => Promise<void>
  onCreateFolder?: (path: string) => Promise<void>
  onUpload?: () => void
  uploading: boolean
  locked: boolean
  collapsed: boolean
  onToggle: () => void
  collapsible?: boolean
  canDeletePath?: (path: string, isDirectory: boolean) => boolean
  tree?: FileTreeNode[]
  sizeMap?: Record<string, number>
  selectableSuffixes?: readonly string[] | null
  uploadLabel?: string
  title?: string
  confirmDelete?: boolean
}) {
  const { t } = useTranslation()
  const [treeTarget, setTreeTarget] = useState<{
    path: string
    isDirectory: boolean
  } | null>(null)
  const [createKind, setCreateKind] = useState<"file" | "folder" | null>(null)
  const [newEntryName, setNewEntryName] = useState("")
  const tree = useMemo(
    () => suppliedTree ?? buildPaperFileTree(manifest),
    [manifest, suppliedTree],
  )
  useEffect(() => {
    if (selectedPath) setTreeTarget({ path: selectedPath, isDirectory: false })
  }, [selectedPath])
  const parentPath = treeTarget
    ? treeTarget.isDirectory
      ? treeTarget.path
      : treeTarget.path.includes("/")
        ? treeTarget.path.slice(0, treeTarget.path.lastIndexOf("/"))
        : ""
    : ""
  const trimmedEntryName = newEntryName.trim()
  const targetEntryPath = parentPath
    ? `${parentPath}/${trimmedEntryName}`
    : trimmedEntryName
  const entryExists =
    manifest.includes(targetEntryPath) ||
    manifest.some((path) => path.startsWith(`${targetEntryPath}/`))
  const invalidEntryName =
    !trimmedEntryName ||
    trimmedEntryName === "." ||
    trimmedEntryName === ".." ||
    trimmedEntryName === PAPER_DIRECTORY_MARKER ||
    /[/\\]/.test(trimmedEntryName) ||
    entryExists ||
    (createKind === "file" &&
      !CREATABLE_PAPER_FILE_SUFFIXES.some((suffix) =>
        trimmedEntryName.toLowerCase().endsWith(suffix),
      ))
  const createEntry = async () => {
    if (!createKind || invalidEntryName) return
    try {
      if (createKind === "file") await onCreateFile?.(targetEntryPath)
      else await onCreateFolder?.(targetEntryPath)
      toast.success(
        t(
          createKind === "file"
            ? "paper.source.fileCreated"
            : "paper.source.folderCreated",
        ),
      )
      setCreateKind(null)
      setNewEntryName("")
    } catch (error) {
      toast.error(errorMessage(error, t("paper.source.createFailed")))
    }
  }
  if (collapsed && collapsible) {
    return (
      <section className="flex h-full flex-col items-center border-r bg-muted/10 py-2">
        <Button
          size="icon"
          variant="ghost"
          className="size-8"
          title={t("paper.editor.expandFiles")}
          onClick={onToggle}
        >
          <PanelLeftOpen className="size-4" />
        </Button>
      </section>
    )
  }
  return (
    <>
      <section className="flex h-full w-full min-w-0 flex-col overflow-hidden border-r bg-muted/10">
        <div className="flex h-11 items-center justify-between border-b px-2.5">
          <span className="truncate text-xs font-semibold text-foreground">
            {title ?? t("paper.editor.files")}
          </span>
          <div className="flex shrink-0 items-center gap-0.5">
            {onCreateFile && (
              <Button
                size="icon"
                variant="ghost"
                className="size-7 shrink-0 text-primary"
                title={t("paper.source.createFile")}
                disabled={uploading || locked}
                onClick={() => {
                  setNewEntryName("section.typ")
                  setCreateKind("file")
                }}
              >
                <FilePlus2 className="size-3.5" />
              </Button>
            )}
            {onCreateFolder && (
              <Button
                size="icon"
                variant="ghost"
                className="size-7 shrink-0 text-primary"
                title={t("paper.source.createFolder")}
                disabled={uploading || locked}
                onClick={() => {
                  setNewEntryName("")
                  setCreateKind("folder")
                }}
              >
                <FolderPlus className="size-3.5" />
              </Button>
            )}
            {onUpload && (
              <Button
                size="icon"
                variant="ghost"
                className="size-7 shrink-0 text-muted-foreground"
                title={uploadLabel ?? t("paper.source.import")}
                aria-label={uploadLabel ?? t("paper.source.import")}
                disabled={uploading || locked}
                onClick={onUpload}
              >
                <Upload className="size-3.5" />
              </Button>
            )}
            {collapsible && (
              <Button
                size="icon"
                variant="ghost"
                className="size-7 shrink-0"
                title={t("paper.editor.collapseFiles")}
                onClick={onToggle}
              >
                <PanelLeftClose className="size-3.5" />
              </Button>
            )}
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1 [&>[data-radix-scroll-area-viewport]>div]:!block [&>[data-radix-scroll-area-viewport]>div]:!w-full">
          <div className="min-h-full p-2 text-xs">
            <FileTreeView
              tree={tree}
              selectedPath={treeTarget?.path ?? selectedPath}
              onSelectFile={(path, isDirectory) => {
                setTreeTarget({ path, isDirectory })
                if (
                  !isDirectory &&
                  (selectableSuffixes === null ||
                    selectableSuffixes.some((suffix) =>
                      path.toLowerCase().endsWith(suffix),
                    ))
                )
                  onSelect(path)
              }}
              onDeleteFile={locked ? undefined : onDelete}
              onDeleteFolder={locked ? undefined : onDelete}
              protectTopLevelDirectories={false}
              confirmDelete={confirmDelete}
              canDeletePath={canDeletePath}
              sizeMap={sizeMap}
            />
          </div>
        </ScrollArea>
      </section>
      <Dialog
        open={Boolean(createKind)}
        onOpenChange={(open) => {
          if (!open) {
            setCreateKind(null)
            setNewEntryName("")
          }
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {t(
                createKind === "folder"
                  ? "paper.source.createFolder"
                  : "paper.source.createFile",
              )}
            </DialogTitle>
            <DialogDescription>
              {parentPath
                ? t("paper.source.createIn", { path: parentPath })
                : t("paper.source.createAtRoot")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Label htmlFor="paper-source-entry-name">
              {t("paper.source.name")}
            </Label>
            <Input
              id="paper-source-entry-name"
              autoFocus
              value={newEntryName}
              placeholder={
                createKind === "folder"
                  ? t("paper.source.folderPlaceholder")
                  : t("paper.source.filePlaceholder")
              }
              onChange={(event) => setNewEntryName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createEntry()
              }}
            />
            {entryExists ? (
              <p className="text-xs text-destructive">
                {t("paper.source.nameExists")}
              </p>
            ) : createKind === "file" && invalidEntryName && newEntryName ? (
              <p className="text-xs text-muted-foreground">
                {t("paper.source.fileNameHint")}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              disabled={invalidEntryName || uploading}
              onClick={() => void createEntry()}
            >
              {uploading && <Loader2 className="size-4 animate-spin" />}
              {t("common.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
