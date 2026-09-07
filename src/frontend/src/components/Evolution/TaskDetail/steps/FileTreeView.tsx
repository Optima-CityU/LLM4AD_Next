import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  Folder,
  FolderOpen,
  Pencil,
  Trash2,
  Upload,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { FileTreeNode } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`
}

function sortTree(nodes: FileTreeNode[]): FileTreeNode[] {
  return [...nodes]
    .sort((a, b) => {
      const aDir = a.type === "directory" ? 0 : 1
      const bDir = b.type === "directory" ? 0 : 1
      if (aDir !== bDir) return aDir - bDir
      return a.name.localeCompare(b.name)
    })
    .map((n) =>
      n.type === "directory" && n.children?.length
        ? { ...n, children: sortTree(n.children) }
        : n,
    )
}

interface FileTreeViewProps {
  tree: FileTreeNode[]
  selectedPath: string | null
  onSelectFile: (path: string, isDir: boolean) => void
  onDeleteFile?: (path: string) => void
  onDeleteFolder?: (path: string) => void
  onRenameFile?: (oldPath: string, newPath: string) => void
  onRenameFolder?: (oldPath: string, newPath: string) => void
  renamingPath?: string | null
  onRenamingChange?: (path: string | null) => void
  onUpload?: () => void
  badgeCounts?: Record<string, number>
  /** 文件相对路径 -> 字节数，用于在文件节点后显示大小 */
  sizeMap?: Record<string, number>
}

export default function FileTreeView({
  tree,
  selectedPath,
  onSelectFile,
  onDeleteFile,
  onDeleteFolder,
  onRenameFile,
  onRenameFolder,
  renamingPath,
  onRenamingChange,
  onUpload,
  badgeCounts,
  sizeMap,
}: FileTreeViewProps) {
  const { t } = useTranslation()
  const [deleteTarget, setDeleteTarget] = useState<{
    path: string
    isDir: boolean
  } | null>(null)
  const sortedTree = useMemo(() => sortTree(tree), [tree])
  if (sortedTree.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-center">
        <Folder className="size-8 text-muted-foreground mb-2" />
        <p className="text-xs text-muted-foreground">
          {t("evolution.dataPrep.noFilesTitle")}
        </p>
        {onUpload && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3 text-xs h-7"
            onClick={onUpload}
          >
            <Upload className="size-3 mr-1" />
            {t("evolution.dataPrep.uploadDir")}
          </Button>
        )}
      </div>
    )
  }

  return (
    <div className="text-sm">
      {sortedTree.map((node) => (
        <TreeNode
          key={node.path}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          onSelectFile={onSelectFile}
          onRequestDelete={
            onDeleteFile || onDeleteFolder
              ? (path, isDir) => {
                  if (isDir && !onDeleteFolder) return
                  if (!isDir && !onDeleteFile) return
                  setDeleteTarget({ path, isDir })
                }
              : undefined
          }
          onRenameFile={onRenameFile}
          onRenameFolder={onRenameFolder}
          renamingPath={renamingPath}
          onRenamingChange={onRenamingChange}
          badgeCounts={badgeCounts}
          sizeMap={sizeMap}
        />
      ))}

      {/* Delete confirmation dialog */}
      <Dialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {deleteTarget?.isDir
                ? t("evolution.dataPrep.confirmDeleteFolderTitle")
                : t("evolution.dataPrep.confirmDeleteFileTitle")}
            </DialogTitle>
            <DialogDescription>
              {deleteTarget?.isDir
                ? t("evolution.dataPrep.confirmDeleteFolderDescription")
                : t("evolution.dataPrep.confirmDeleteFileDescription")}{" "}
              <span className="font-medium text-foreground break-all">
                {deleteTarget?.path}
              </span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleteTarget) {
                  if (deleteTarget.isDir && onDeleteFolder) {
                    onDeleteFolder(deleteTarget.path)
                  } else if (!deleteTarget.isDir && onDeleteFile) {
                    onDeleteFile(deleteTarget.path)
                  }
                }
                setDeleteTarget(null)
              }}
            >
              {t("common.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function TreeNode({
  node,
  depth,
  selectedPath,
  onSelectFile,
  onRequestDelete,
  onRenameFile,
  onRenameFolder,
  renamingPath,
  onRenamingChange,
  badgeCounts,
  sizeMap,
}: {
  node: FileTreeNode
  depth: number
  selectedPath: string | null
  onSelectFile: (path: string, isDir: boolean) => void
  onRequestDelete?: (path: string, isDir: boolean) => void
  onRenameFile?: (oldPath: string, newPath: string) => void
  onRenameFolder?: (oldPath: string, newPath: string) => void
  renamingPath?: string | null
  onRenamingChange?: (path: string | null) => void
  badgeCounts?: Record<string, number>
  sizeMap?: Record<string, number>
}) {
  const [expanded, setExpanded] = useState(depth < 2)
  const isDir = node.type === "directory"
  const isTopLevelDir = isDir && !node.path.includes("/")
  const isSelected =
    node.path === selectedPath || (isDir && selectedPath === `${node.path}/`)
  const isRenaming = node.path === renamingPath
  const renameInputRef = useRef<HTMLInputElement>(null)

  const shouldAutoExpand = isDir && selectedPath?.startsWith(`${node.path}/`)
  useEffect(() => {
    if (shouldAutoExpand) setExpanded(true)
  }, [shouldAutoExpand])

  useEffect(() => {
    if (isRenaming && renameInputRef.current) {
      renameInputRef.current.focus()
      const name = node.name
      const dotIdx = name.lastIndexOf(".")
      renameInputRef.current.setSelectionRange(
        0,
        dotIdx > 0 ? dotIdx : name.length,
      )
    }
  }, [isRenaming, node.name])

  const handleClick = () => {
    if (isDir) {
      setExpanded(!expanded)
    }
    onSelectFile(node.path, isDir)
  }

  const commitRename = (newName: string) => {
    const trimmed = newName.trim()
    if (!trimmed || trimmed === node.name) {
      onRenamingChange?.(null)
      return
    }
    const dirPrefix = node.path.includes("/")
      ? node.path.slice(0, node.path.lastIndexOf("/") + 1)
      : ""
    if (isDir) {
      onRenameFolder?.(node.path, dirPrefix + trimmed)
    } else {
      onRenameFile?.(node.path, dirPrefix + trimmed)
    }
  }

  return (
    <div>
      <div
        className={cn(
          "group relative flex items-center gap-1 rounded px-1 py-0.5 cursor-pointer hover:bg-accent/50 transition-colors",
          isSelected &&
            "bg-primary/10 text-primary font-medium hover:bg-primary/15 before:absolute before:inset-y-0.5 before:left-0 before:w-0.5 before:rounded-full before:bg-primary",
        )}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
        onClick={handleClick}
      >
        {isDir ? (
          <>
            {expanded ? (
              <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            {expanded ? (
              <FolderOpen className="size-3.5 shrink-0 text-primary" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-primary" />
            )}
          </>
        ) : (
          <>
            <span className="w-3.5 shrink-0" />
            <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
          </>
        )}
        {isRenaming ? (
          <input
            ref={renameInputRef}
            className="flex-1 text-xs ml-1 bg-background border border-primary rounded px-1 py-0 outline-none min-w-0"
            defaultValue={node.name}
            maxLength={32}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                commitRename((e.target as HTMLInputElement).value)
              } else if (e.key === "Escape") {
                onRenamingChange?.(null)
              }
            }}
            onBlur={(e) => commitRename(e.target.value)}
          />
        ) : (
          <span className="flex-1 truncate text-xs ml-1" title={node.name}>
            {node.name}
          </span>
        )}
        {badgeCounts && !isDir && (badgeCounts[node.path] ?? 0) > 0 && (
          <span className="shrink-0 px-1.5 py-0 text-[10px] font-medium rounded-full bg-primary/15 text-primary">
            {badgeCounts[node.path]}
          </span>
        )}
        {sizeMap && !isDir && sizeMap[node.path] != null && !isRenaming && (
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/70 group-hover:text-muted-foreground">
            {formatFileSize(sizeMap[node.path])}
          </span>
        )}
        {!isRenaming &&
          onRenamingChange &&
          !isTopLevelDir &&
          ((isDir && onRenameFolder) || (!isDir && onRenameFile)) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="opacity-0 group-hover:opacity-100 h-5 w-5 p-0 text-muted-foreground hover:text-primary shrink-0"
              onClick={(e) => {
                e.stopPropagation()
                onRenamingChange(node.path)
              }}
            >
              <Pencil className="size-3" />
            </Button>
          )}
        {!isRenaming && onRequestDelete && !isTopLevelDir && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="opacity-0 group-hover:opacity-100 h-5 w-5 p-0 text-destructive hover:text-destructive shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onRequestDelete(node.path, isDir)
            }}
          >
            <Trash2 className="size-3" />
          </Button>
        )}
      </div>
      {isDir && expanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
              onRequestDelete={onRequestDelete}
              onRenameFile={onRenameFile}
              onRenameFolder={onRenameFolder}
              renamingPath={renamingPath}
              onRenamingChange={onRenamingChange}
              badgeCounts={badgeCounts}
              sizeMap={sizeMap}
            />
          ))}
        </div>
      )}
    </div>
  )
}
