import Editor, { loader } from "@monaco-editor/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import * as monaco from "monaco-editor"
import { useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { Group, Panel, Separator } from "react-resizable-panels"

loader.config({ monaco })

import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FilePlus,
  Folder,
  FolderOpen,
  FolderPlus,
  Loader2,
  RefreshCw,
  Save,
  Upload,
} from "lucide-react"

import { Llm4AdTasksService } from "@/client"
import { useTheme } from "@/components/theme-provider"
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
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import {
  getLanguageFromPath as getSourceLanguageFromPath,
  SHADER_FILE_EXTENSIONS,
} from "./EvolveAnnotationStep/types"
import FileTreeView from "./FileTreeView"

/* ── Common text file extensions (allowed for selection / preview) ── */

const TEXT_FILE_EXTENSIONS: ReadonlySet<string> = new Set([
  // Plain / docs
  "txt",
  "md",
  "markdown",
  "rst",
  "tex",
  "log",
  "csv",
  "tsv",
  // Configs
  "json",
  "json5",
  "jsonc",
  "yaml",
  "yml",
  "toml",
  "ini",
  "cfg",
  "conf",
  "env",
  "properties",
  "xml",
  "plist",
  // Web
  "html",
  "htm",
  "css",
  "scss",
  "sass",
  "less",
  "vue",
  "svelte",
  "astro",
  // JS / TS family
  "js",
  "mjs",
  "cjs",
  "jsx",
  "ts",
  "tsx",
  // Python
  "py",
  "pyi",
  "pyx",
  "ipynb",
  // JVM
  "java",
  "kt",
  "kts",
  "scala",
  "groovy",
  "clj",
  "cljs",
  // C family
  "c",
  "h",
  "cc",
  "cpp",
  "cxx",
  "hh",
  "hpp",
  "hxx",
  ...SHADER_FILE_EXTENSIONS,
  "cs",
  "m",
  "mm",
  // Systems
  "go",
  "rs",
  "swift",
  "zig",
  "d",
  // Scripting
  "sh",
  "bash",
  "zsh",
  "fish",
  "bat",
  "cmd",
  "ps1",
  "lua",
  "pl",
  "pm",
  "rb",
  "php",
  "tcl",
  // Data / DB
  "sql",
  "graphql",
  "gql",
  "proto",
  // ML / data science
  "r",
  "jl",
  // Functional
  "ex",
  "exs",
  "erl",
  "hrl",
  "hs",
  "lhs",
  "ml",
  "mli",
  "fs",
  "fsx",
  "elm",
  // Misc text-based
  "dart",
  "vb",
  "asm",
  "s",
  "diff",
  "patch",
  "dockerfile",
  "gitignore",
  "gitattributes",
  "editorconfig",
  "lock",
  "txt",
])

function isTextFileName(name: string): boolean {
  const lower = name.toLowerCase()
  // Common dotfiles / ext-less filenames that are text
  const TEXT_BASENAMES = new Set([
    "dockerfile",
    "makefile",
    "readme",
    "license",
    "changelog",
    "authors",
    "contributors",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".env",
    ".npmrc",
    ".nvmrc",
    ".prettierrc",
    ".eslintrc",
    ".babelrc",
  ])
  if (TEXT_BASENAMES.has(lower)) return true
  const dotIndex = lower.lastIndexOf(".")
  if (dotIndex === -1 || dotIndex === lower.length - 1) return false
  const ext = lower.slice(dotIndex + 1)
  return TEXT_FILE_EXTENSIONS.has(ext)
}

/* ── Upload size / count limits (must match backend settings) ── */

const MAX_FILE_BYTES = 50 * 1024 * 1024 // 50 MB per file
const MAX_TOTAL_BYTES = 100 * 1024 * 1024 // 100 MB per upload
const MAX_FILE_COUNT = 100 // matches backend `len(files) > 100` check
const WARN_FILE_COUNT = 50 // soft warning threshold
// Hard cap on a single directory pick. Selecting a drive root (e.g. C:\) yields
// hundreds of thousands of files; building/rendering that tree freezes the tab,
// so reject the selection up front before any heavy processing.
const MAX_SELECTION_COUNT = 1000

const MAX_FILE_MB = MAX_FILE_BYTES / 1024 / 1024
const MAX_TOTAL_MB = MAX_TOTAL_BYTES / 1024 / 1024

/* ── Local file tree types & helpers ── */

interface LocalFileTreeNode {
  name: string
  path: string
  isDir: boolean
  children: LocalFileTreeNode[]
  fileIndex?: number
}

function buildFileTree(files: File[]): LocalFileTreeNode[] {
  const root: LocalFileTreeNode[] = []
  const dirMap = new Map<string, LocalFileTreeNode>()

  const getOrCreateDir = (dirPath: string): LocalFileTreeNode => {
    const existing = dirMap.get(dirPath)
    if (existing) return existing
    const parts = dirPath.split("/")
    const name = parts[parts.length - 1]
    const node: LocalFileTreeNode = {
      name,
      path: dirPath,
      isDir: true,
      children: [],
    }
    dirMap.set(dirPath, node)
    if (parts.length === 1) {
      root.push(node)
    } else {
      const parentPath = parts.slice(0, -1).join("/")
      const parent = getOrCreateDir(parentPath)
      parent.children.push(node)
    }
    return node
  }

  files.forEach((f, i) => {
    const relativePath = f.webkitRelativePath || f.name
    const parts = relativePath.split("/")
    const fileName = parts[parts.length - 1]
    const fileNode: LocalFileTreeNode = {
      name: fileName,
      path: relativePath,
      isDir: false,
      children: [],
      fileIndex: i,
    }
    if (parts.length === 1) {
      root.push(fileNode)
    } else {
      const dirPath = parts.slice(0, -1).join("/")
      const parent = getOrCreateDir(dirPath)
      parent.children.push(fileNode)
    }
  })

  // Sort: directories first, then alphabetical
  const sortChildren = (nodes: LocalFileTreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    for (const n of nodes) {
      if (n.isDir) sortChildren(n.children)
    }
  }
  sortChildren(root)
  return root
}

/** Collect all file indices under a node (recursively). */
function collectFileIndices(node: LocalFileTreeNode): number[] {
  if (!node.isDir) return node.fileIndex !== undefined ? [node.fileIndex] : []
  return node.children.flatMap(collectFileIndices)
}

/* ── Upload tree node component ── */

function UploadTreeNode({
  node,
  depth,
  checkedIndices,
  onToggle,
}: {
  node: LocalFileTreeNode
  depth: number
  checkedIndices: Set<number>
  onToggle: (indices: number[], checked: boolean) => void
}) {
  const [expanded, setExpanded] = useState(depth < 2)

  if (node.isDir) {
    const allIndices = collectFileIndices(node)
    const checkedCount = allIndices.filter((i) => checkedIndices.has(i)).length
    const allChecked =
      allIndices.length > 0 && checkedCount === allIndices.length
    const someChecked = checkedCount > 0 && !allChecked
    const checkState: boolean | "indeterminate" = allChecked
      ? true
      : someChecked
        ? "indeterminate"
        : false

    return (
      <div>
        <div
          className="flex items-center gap-1.5 py-0.5 px-1 rounded hover:bg-muted/80 cursor-pointer"
          style={{ paddingLeft: `${depth * 16 + 4}px` }}
          onClick={() => setExpanded(!expanded)}
        >
          <Checkbox
            checked={checkState}
            onCheckedChange={() => onToggle(allIndices, !allChecked)}
            onClick={(e) => e.stopPropagation()}
            className="size-3.5"
          />
          {expanded ? (
            <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
          )}
          {expanded ? (
            <FolderOpen className="size-3.5 shrink-0 text-primary" />
          ) : (
            <Folder className="size-3.5 shrink-0 text-primary" />
          )}
          <span className="text-xs truncate ml-0.5">{node.name}</span>
        </div>
        {expanded && (
          <div>
            {node.children.map((child) => (
              <UploadTreeNode
                key={child.path}
                node={child}
                depth={depth + 1}
                checkedIndices={checkedIndices}
                onToggle={onToggle}
              />
            ))}
          </div>
        )}
      </div>
    )
  }

  // File node
  const checked =
    node.fileIndex !== undefined && checkedIndices.has(node.fileIndex)
  return (
    <label
      className="flex items-center gap-1.5 py-0.5 px-1 rounded hover:bg-muted/80 cursor-pointer"
      style={{ paddingLeft: `${depth * 16 + 4}px` }}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={(c) => {
          if (node.fileIndex !== undefined) onToggle([node.fileIndex], !!c)
        }}
        className="size-3.5"
      />
      <span className="w-3 shrink-0" />
      <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="text-xs text-muted-foreground truncate ml-0.5">
        {node.name}
      </span>
    </label>
  )
}

interface DataPreparationStepProps {
  taskId: string
  onNext: () => void
  readOnly?: boolean
}

function getLanguageFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? ""
  const map: Record<string, string> = {
    py: "python",
    js: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    md: "markdown",
    txt: "plaintext",
    csv: "plaintext",
    sh: "shell",
    bash: "shell",
    toml: "plaintext",
    cfg: "ini",
    ini: "ini",
    xml: "xml",
    html: "html",
    css: "css",
  }
  return map[ext] ?? getSourceLanguageFromPath(path)
}

export default function DataPreparationStep({
  taskId,
  onNext,
  readOnly = false,
}: DataPreparationStepProps) {
  const { t } = useTranslation()
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme !== "light"
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [activePath, setActivePath] = useState<string | null>(null)
  const [activeIsDir, setActiveIsDir] = useState(false)
  const [editorContent, setEditorContent] = useState<string>("")
  const editorContentRef = useRef<string>("")
  const [isDragging, setIsDragging] = useState(false)
  const [localFiles, setLocalFiles] = useState<File[]>([])
  const [checkedIndices, setCheckedIndices] = useState<Set<number>>(new Set())
  const [uploading, setUploading] = useState(false)
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [renamingPath, setRenamingPath] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const treeQueryKey = ["taskDataTree", taskId]

  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: treeQueryKey,
    queryFn: () => Llm4AdTasksService.getTaskDataTree({ taskId }),
    enabled: !!taskId,
  })

  const selectedIsPreviewable = selectedFile
    ? isTextFileName(selectedFile.split("/").pop() ?? selectedFile)
    : false

  const fileQuery = useQuery({
    queryKey: ["taskDataFile", taskId, selectedFile],
    queryFn: () =>
      Llm4AdTasksService.getTaskDataFile({
        taskId,
        filePath: selectedFile!,
      }),
    enabled: !!taskId && !!selectedFile && selectedIsPreviewable,
  })

  // When file loads, set editor content
  const fileContent = fileQuery.data?.content
  const prevFileRef = useRef<string | null>(null)
  if (
    selectedFile !== prevFileRef.current &&
    fileContent !== undefined &&
    !fileQuery.isFetching
  ) {
    prevFileRef.current = selectedFile
    setEditorContent(fileContent)
    editorContentRef.current = fileContent
  }

  const handleEditorChange = (v: string | undefined) => {
    const val = v ?? ""
    setEditorContent(val)
    editorContentRef.current = val
  }

  const saveMutation = useMutation({
    mutationFn: ({
      filePath,
      content,
    }: {
      filePath: string
      content: string
    }) =>
      Llm4AdTasksService.updateTaskDataFile({
        taskId,
        requestBody: { file_path: filePath, content },
      }),
    onSuccess: (_res, { filePath, content }) => {
      showSuccessToast(t("evolution.dataPrep.fileSaveSuccess"))
      // Avoid an unnecessary refetch when switching back to this file shortly
      // after saving — the editor content is already what we just persisted.
      queryClient.setQueryData<{ file_path: string; content: string }>(
        ["taskDataFile", taskId, filePath],
        (old) => (old ? { ...old, content } : { file_path: filePath, content }),
      )
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteMutation = useMutation({
    mutationFn: (filePath: string) =>
      Llm4AdTasksService.deleteTaskDataFile({ taskId, filePath }),
    onSuccess: async () => {
      showSuccessToast(t("evolution.dataPrep.fileDeleteSuccess"))
      if (selectedFile) setSelectedFile(null)
      queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteFolderMutation = useMutation({
    mutationFn: (folderPath: string) =>
      Llm4AdTasksService.deleteTaskDataFolder({ taskId, folderPath }),
    onSuccess: async (_res, folderPath) => {
      showSuccessToast(t("evolution.dataPrep.folderDeleteSuccess"))
      if (
        selectedFile &&
        (selectedFile === folderPath ||
          selectedFile.startsWith(`${folderPath}/`))
      ) {
        setSelectedFile(null)
      }
      if (
        activePath &&
        (activePath === folderPath || activePath.startsWith(`${folderPath}/`))
      ) {
        setActivePath(null)
        setActiveIsDir(false)
      }
      queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const createFolderMutation = useMutation({
    mutationFn: (parentPath: string) =>
      Llm4AdTasksService.createTaskDataFolder({
        taskId,
        requestBody: { path: parentPath },
      }),
    onSuccess: async (res) => {
      showSuccessToast(t("evolution.dataPrep.folderCreateSuccess"))
      await queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
      setActivePath(res.folder_path)
      setActiveIsDir(true)
    },
    onError: handleError.bind(showErrorToast),
  })

  const createFileMutation = useMutation({
    mutationFn: (dirPath: string | null) =>
      Llm4AdTasksService.createTaskDataFile({
        taskId,
        requestBody: { path: dirPath },
      }),
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
      const createdPath = res.file_path
      setSelectedFile(createdPath)
      setActivePath(createdPath)
      setActiveIsDir(false)
      setEditorContent("")
      prevFileRef.current = null
      setRenamingPath(createdPath)
    },
    onError: handleError.bind(showErrorToast),
  })

  const renameMutation = useMutation({
    mutationFn: ({ oldPath, newPath }: { oldPath: string; newPath: string }) =>
      Llm4AdTasksService.renameTaskDataFile({
        taskId,
        requestBody: { old_path: oldPath, new_path: newPath },
      }),
    onSuccess: async (_res, { newPath }) => {
      await queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
      setSelectedFile(newPath)
      setActivePath(newPath)
      setActiveIsDir(false)
      prevFileRef.current = null
      setRenamingPath(null)
    },
    onError: (err) => {
      handleError.call(showErrorToast, err as import("@/client").ApiError)
      setRenamingPath(null)
    },
  })

  const renameFolderMutation = useMutation({
    mutationFn: ({ oldPath, newPath }: { oldPath: string; newPath: string }) =>
      Llm4AdTasksService.renameTaskDataFolder({
        taskId,
        requestBody: { old_path: oldPath, new_path: newPath },
      }),
    onSuccess: async (_res, { oldPath, newPath }) => {
      await queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
      if (
        selectedFile &&
        (selectedFile === oldPath || selectedFile.startsWith(`${oldPath}/`))
      ) {
        const suffix = selectedFile.slice(oldPath.length)
        const remapped = newPath + suffix
        setSelectedFile(remapped)
        prevFileRef.current = null
      }
      if (
        activePath &&
        (activePath === oldPath || activePath.startsWith(`${oldPath}/`))
      ) {
        const suffix = activePath.slice(oldPath.length)
        setActivePath(newPath + suffix)
      }
      setRenamingPath(null)
    },
    onError: (err) => {
      handleError.call(showErrorToast, err as import("@/client").ApiError)
      setRenamingPath(null)
    },
  })

  // set webkitdirectory via ref
  const setInputRef = (el: HTMLInputElement | null) => {
    if (el) el.setAttribute("webkitdirectory", "")
    inputRef.current = el
  }

  const addFiles = (fileList: FileList) => {
    // Reject oversized selections (e.g. a drive root) before any heavy work,
    // otherwise building and rendering the file tree freezes the UI.
    if (fileList.length > MAX_SELECTION_COUNT) {
      showErrorToast(
        t("evolution.dataPrep.tooManySelected", {
          count: fileList.length,
          max: MAX_FILE_COUNT,
        }),
      )
      return
    }
    const allFiles = Array.from(fileList)
    // Drop files exceeding the per-file size limit (backend would reject them).
    const sized = allFiles.filter((f) => f.size <= MAX_FILE_BYTES)
    const tooLarge = allFiles.length - sized.length
    if (tooLarge > 0) {
      showErrorToast(
        t("evolution.dataPrep.fileTooLargeSkipped", {
          count: tooLarge,
          maxMB: MAX_FILE_MB,
        }),
      )
    }
    if (sized.length === 0) return
    // Default-check files greedily until the total-size limit is reached.
    const checked = new Set<number>()
    let runningTotal = 0
    sized.forEach((f, i) => {
      if (runningTotal + f.size <= MAX_TOTAL_BYTES) {
        checked.add(i)
        runningTotal += f.size
      }
    })
    if (checked.size < sized.length) {
      showErrorToast(
        t("evolution.dataPrep.totalSizeExceeded", {
          maxMB: MAX_TOTAL_MB,
          count: checked.size,
        }),
      )
    }
    setLocalFiles(sized)
    setCheckedIndices(checked)
    setUploadDialogOpen(true)
  }

  const handleUpload = async () => {
    const filesToUpload = localFiles.filter((_, i) => checkedIndices.has(i))
    if (filesToUpload.length === 0 || filesToUpload.length > MAX_FILE_COUNT) {
      return
    }
    const totalBytes = filesToUpload.reduce((sum, f) => sum + f.size, 0)
    if (totalBytes > MAX_TOTAL_BYTES) {
      showErrorToast(
        t("evolution.dataPrep.totalSizeExceededOnUpload", {
          totalMB: (totalBytes / 1024 / 1024).toFixed(1),
          maxMB: MAX_TOTAL_MB,
        }),
      )
      return
    }
    setUploading(true)
    try {
      await Llm4AdTasksService.uploadTaskData({
        taskId,
        formData: { files: filesToUpload },
      })
      showSuccessToast(
        t("evolution.dataPrep.uploadSuccess", { count: filesToUpload.length }),
      )
      setLocalFiles([])
      setCheckedIndices(new Set())
      setUploadDialogOpen(false)
      setSelectedFile(null)
      setEditorContent("")
      editorContentRef.current = ""
      prevFileRef.current = null
      queryClient.invalidateQueries({ queryKey: treeQueryKey })
      queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
    } catch (err: unknown) {
      handleError.call(showErrorToast, err as import("@/client").ApiError)
    } finally {
      setUploading(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files)
    }
  }

  const handleSelectFile = (path: string, isDir: boolean) => {
    setActivePath(path)
    setActiveIsDir(isDir)
    if (!isDir) {
      if (path === selectedFile) return
      setSelectedFile(path)
      setEditorContent("")
      // Reset so the content sync block re-fills the editor for the new file.
      // Without this, switching to a non-previewable file (which never updates
      // prevFileRef) and back leaves prevFileRef === selectedFile, so the editor
      // stays blank even though the fetched content is correct.
      prevFileRef.current = null
    }
  }

  const tree = useMemo(() => treeData?.tree ?? [], [treeData?.tree])

  const uploadTree = useMemo(() => buildFileTree(localFiles), [localFiles])

  const checkedTotalBytes = useMemo(() => {
    let total = 0
    localFiles.forEach((f, i) => {
      if (checkedIndices.has(i)) total += f.size
    })
    return total
  }, [localFiles, checkedIndices])
  const checkedTotalExceeded = checkedTotalBytes > MAX_TOTAL_BYTES
  const checkedCountExceeded = checkedIndices.size > MAX_FILE_COUNT

  const handleToggleIndices = (indices: number[], checked: boolean) => {
    setCheckedIndices((prev) => {
      const next = new Set(prev)
      for (const i of indices) {
        if (checked) next.add(i)
        else next.delete(i)
      }
      return next
    })
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 flex flex-col space-y-4">
        {/* Upload confirmation dialog */}
        <Dialog
          open={uploadDialogOpen}
          onOpenChange={(open) => {
            if (!uploading) setUploadDialogOpen(open)
          }}
        >
          <DialogContent className="sm:max-w-lg max-h-[80vh] flex flex-col">
            <DialogHeader>
              <DialogTitle>
                {t("evolution.dataPrep.confirmUploadTitle")}
              </DialogTitle>
              <DialogDescription>
                {t("evolution.dataPrep.confirmUploadDescription", {
                  count: localFiles.length,
                })}
              </DialogDescription>
            </DialogHeader>
            {/* Select all / none */}
            <div className="flex items-center gap-2 px-1">
              <Checkbox
                id="upload-select-all"
                checked={
                  checkedIndices.size === localFiles.length
                    ? true
                    : checkedIndices.size > 0
                      ? "indeterminate"
                      : false
                }
                onCheckedChange={(checked) => {
                  if (checked) {
                    setCheckedIndices(new Set(localFiles.map((_, i) => i)))
                  } else {
                    setCheckedIndices(new Set())
                  }
                }}
              />
              <label
                htmlFor="upload-select-all"
                className="text-xs text-muted-foreground cursor-pointer select-none"
              >
                {t("evolution.dataPrep.selectAll", {
                  selected: checkedIndices.size,
                  total: localFiles.length,
                })}
              </label>
            </div>
            {/* File count warnings */}
            {checkedCountExceeded && (
              <div className="flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2">
                <AlertTriangle className="size-4 text-destructive shrink-0" />
                <span className="text-xs text-destructive">
                  {t("evolution.dataPrep.fileLimitWarning", {
                    count: checkedIndices.size,
                    max: MAX_FILE_COUNT,
                  })}
                </span>
              </div>
            )}
            {!checkedCountExceeded &&
              checkedIndices.size >= WARN_FILE_COUNT && (
                <div className="flex items-center gap-2 rounded-md border border-yellow-500/50 bg-yellow-500/10 px-3 py-2">
                  <AlertTriangle className="size-4 text-yellow-500 shrink-0" />
                  <span className="text-xs text-yellow-500">
                    {t("evolution.dataPrep.fileLargeWarning", {
                      count: checkedIndices.size,
                    })}
                  </span>
                </div>
              )}
            {/* Total size warning */}
            {checkedTotalExceeded && (
              <div className="flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2">
                <AlertTriangle className="size-4 text-destructive shrink-0" />
                <span className="text-xs text-destructive">
                  {t("evolution.dataPrep.totalSizeExceededOnUpload", {
                    totalMB: (checkedTotalBytes / 1024 / 1024).toFixed(1),
                    maxMB: MAX_TOTAL_MB,
                  })}
                </span>
              </div>
            )}
            {/* File tree */}
            <div className="min-h-0 flex-1 overflow-y-auto rounded border bg-muted/50 p-2">
              {uploadTree.map((node) => (
                <UploadTreeNode
                  key={node.path}
                  node={node}
                  depth={0}
                  checkedIndices={checkedIndices}
                  onToggle={handleToggleIndices}
                />
              ))}
            </div>
            <DialogFooter className="gap-3">
              <Button
                variant="outline"
                disabled={uploading}
                onClick={() => {
                  setLocalFiles([])
                  setCheckedIndices(new Set())
                  setUploadDialogOpen(false)
                }}
              >
                {t("common.cancel")}
              </Button>
              <LoadingButton
                loading={uploading}
                onClick={handleUpload}
                disabled={
                  checkedIndices.size === 0 ||
                  checkedCountExceeded ||
                  checkedTotalExceeded
                }
              >
                <Upload className="size-3.5 mr-1" />
                {t("evolution.dataPrep.confirmUploadButton", {
                  count: checkedIndices.size,
                })}
              </LoadingButton>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Upload toolbar */}
        {!readOnly && (
          <div className="shrink-0 flex justify-end mb-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => inputRef.current?.click()}
                >
                  <Upload className="size-3.5 mr-1.5" />
                  {t("evolution.dataPrep.uploadLocalDir")}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs text-left">
                <p>
                  {t("evolution.dataPrep.uploadTooltip", {
                    maxFileMB: MAX_FILE_MB,
                    maxTotalMB: MAX_TOTAL_MB,
                    maxCount: MAX_FILE_COUNT,
                  })}
                </p>
              </TooltipContent>
            </Tooltip>
          </div>
        )}

        {/* Main: Tree + Editor */}
        <div
          className="rounded-lg border overflow-hidden flex-1 min-h-0"
          onDragOver={
            readOnly
              ? undefined
              : (e) => {
                  e.preventDefault()
                  setIsDragging(true)
                }
          }
          onDragLeave={readOnly ? undefined : () => setIsDragging(false)}
          onDrop={readOnly ? undefined : handleDrop}
        >
          <Group orientation="horizontal" className="h-full">
            {/* File tree panel */}
            <Panel defaultSize="25%" minSize="15%" maxSize="40%">
              <div className="h-full flex flex-col border-r bg-card">
                <div className="shrink-0 h-9 flex items-center justify-between px-3 border-b bg-muted/30">
                  <div className="flex items-center gap-1">
                    <h4 className="text-xs font-medium text-muted-foreground mr-1">
                      {t("evolution.dataPrep.fileTreeTitle", {
                        defaultValue: t("evolution.evolveAnnotation.fileTree"),
                      })}
                    </h4>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                          onClick={() =>
                            queryClient.invalidateQueries({
                              queryKey: treeQueryKey,
                            })
                          }
                        >
                          <RefreshCw className="size-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        {t("common.refresh")}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                  <div className="flex items-center gap-1">
                    {!readOnly && activePath && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            className="p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
                            disabled={createFileMutation.isPending}
                            onClick={() => {
                              // If a file is selected, create the new file in
                              // its parent directory — not "inside" the file.
                              // The backend would otherwise treat extensionless
                              // names (Jenkinsfile, Dockerfile, Makefile…) as
                              // directories and nest the new file under them.
                              let path: string
                              if (activeIsDir) {
                                path = activePath.endsWith("/")
                                  ? activePath
                                  : `${activePath}/`
                              } else if (activePath.includes("/")) {
                                path = `${activePath.slice(0, activePath.lastIndexOf("/"))}/`
                              } else {
                                path = ""
                              }
                              createFileMutation.mutate(path)
                            }}
                          >
                            <FilePlus className="size-3.5" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="bottom">
                          {t("evolution.dataPrep.createFileTooltip")}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    {!readOnly && activePath && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            className="p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
                            disabled={createFolderMutation.isPending}
                            onClick={() => {
                              const parent = activeIsDir
                                ? activePath
                                : activePath.includes("/")
                                  ? activePath.slice(
                                      0,
                                      activePath.lastIndexOf("/"),
                                    )
                                  : ""
                              if (!parent) {
                                showErrorToast(
                                  t("evolution.dataPrep.cannotCreateTopFolder"),
                                )
                                return
                              }
                              createFolderMutation.mutate(parent)
                            }}
                          >
                            <FolderPlus className="size-3.5" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="bottom">
                          {t("evolution.dataPrep.createFolderTooltip")}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                </div>
                {!readOnly && (
                  <input
                    ref={setInputRef}
                    type="file"
                    className="hidden"
                    multiple
                    onChange={(e) => {
                      if (e.target.files) addFiles(e.target.files)
                      e.target.value = ""
                    }}
                  />
                )}
                <div className="flex-1 overflow-y-auto p-2">
                  {treeLoading ? (
                    <p className="text-xs text-muted-foreground p-2">
                      {t("common.loading")}
                    </p>
                  ) : tree.length === 0 ? (
                    <div className="flex items-center justify-center h-full">
                      <div className="text-center text-muted-foreground px-3">
                        <FileIcon className="size-8 mx-auto mb-2 opacity-30" />
                        <p className="text-xs font-medium">
                          {t("evolution.dataPrep.noFilesTitle")}
                        </p>
                        <p className="text-[11px] mt-1 opacity-70">
                          {t("evolution.dataPrep.noFilesDescription")}
                        </p>
                      </div>
                    </div>
                  ) : (
                    <FileTreeView
                      tree={tree}
                      selectedPath={activePath}
                      onSelectFile={handleSelectFile}
                      onDeleteFile={
                        readOnly
                          ? undefined
                          : (path) => deleteMutation.mutate(path)
                      }
                      onDeleteFolder={
                        readOnly
                          ? undefined
                          : (path) => deleteFolderMutation.mutate(path)
                      }
                      onRenameFile={
                        readOnly
                          ? undefined
                          : (oldPath, newPath) =>
                              renameMutation.mutate({ oldPath, newPath })
                      }
                      onRenameFolder={
                        readOnly
                          ? undefined
                          : (oldPath, newPath) =>
                              renameFolderMutation.mutate({ oldPath, newPath })
                      }
                      renamingPath={renamingPath}
                      onRenamingChange={readOnly ? undefined : setRenamingPath}
                      onUpload={
                        readOnly ? undefined : () => inputRef.current?.click()
                      }
                    />
                  )}
                </div>
              </div>
            </Panel>

            <Separator className="w-1 bg-border hover:bg-primary/20 transition-colors" />

            {/* Editor panel */}
            <Panel defaultSize="75%" minSize="50%">
              <div
                className={`h-full flex flex-col relative ${isDark ? "bg-[#0D1A2D]" : "bg-background"}`}
              >
                {isDragging && (
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-primary/10 border-2 border-dashed border-primary">
                    <p className="text-sm text-primary font-medium">
                      {t("evolution.dataPrep.dropHint")}
                    </p>
                  </div>
                )}
                {selectedFile ? (
                  !selectedIsPreviewable ? (
                    <>
                      <div
                        className={`shrink-0 h-9 flex items-center px-3 border-b ${isDark ? "bg-[#0D1A2D] border-[#1a2d45]" : "bg-muted/30 border-border"}`}
                      >
                        <span
                          className={`text-xs truncate ${isDark ? "text-[#cccccc]" : "text-muted-foreground"}`}
                        >
                          {selectedFile}
                        </span>
                      </div>
                      <div className="flex-1 flex items-center justify-center bg-card">
                        <div className="text-center text-muted-foreground px-4">
                          <FileIcon className="size-10 mx-auto mb-3 opacity-40" />
                          <p className="text-sm font-medium">
                            {t("evolution.dataPrep.unsupportedPreviewTitle")}
                          </p>
                          <p className="text-xs mt-1 opacity-70">
                            {t(
                              "evolution.dataPrep.unsupportedPreviewDescription",
                            )}
                          </p>
                        </div>
                      </div>
                    </>
                  ) : fileQuery.isFetching &&
                    prevFileRef.current !== selectedFile ? (
                    <div className="flex-1 flex items-center justify-center">
                      <Loader2 className="size-5 animate-spin text-muted-foreground mr-2" />
                      <span className="text-sm text-muted-foreground">
                        {t("evolution.dataPrep.loadingEditor")}
                      </span>
                    </div>
                  ) : (
                    <>
                      <div
                        className={`shrink-0 h-9 flex items-center justify-between px-3 border-b ${isDark ? "bg-[#0D1A2D] border-[#1a2d45]" : "bg-muted/30 border-border"}`}
                      >
                        <span
                          className={`text-xs truncate ${isDark ? "text-[#cccccc]" : "text-muted-foreground"}`}
                        >
                          {selectedFile}
                        </span>
                        {!readOnly && (
                          <LoadingButton
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs gap-1"
                            loading={saveMutation.isPending}
                            onClick={() =>
                              selectedFile &&
                              saveMutation.mutate({
                                filePath: selectedFile,
                                content: editorContentRef.current,
                              })
                            }
                          >
                            <Save className="size-3" />
                            {t("common.save")}
                          </LoadingButton>
                        )}
                      </div>
                      <div className="flex-1 min-h-0">
                        <Editor
                          height="100%"
                          language={getLanguageFromPath(selectedFile)}
                          theme={isDark ? "custom-dark" : "custom-light"}
                          value={editorContent}
                          onChange={handleEditorChange}
                          beforeMount={(m) => {
                            m.editor.defineTheme("custom-dark", {
                              base: "vs-dark",
                              inherit: true,
                              rules: [],
                              colors: {
                                "editor.background": "#0D1A2D",
                              },
                            })
                            m.editor.defineTheme("custom-light", {
                              base: "vs",
                              inherit: true,
                              rules: [],
                              colors: {
                                "editor.background": "#ffffff",
                              },
                            })
                          }}
                          options={{
                            minimap: { enabled: false },
                            fontSize: 13,
                            lineNumbers: "on",
                            scrollBeyondLastLine: false,
                            wordWrap: "on",
                            automaticLayout: true,
                            readOnly,
                          }}
                          loading={
                            <div className="flex items-center justify-center h-full">
                              <p className="text-xs text-muted-foreground">
                                {t("evolution.dataPrep.loadingEditor")}
                              </p>
                            </div>
                          }
                        />
                      </div>
                    </>
                  )
                ) : (
                  <div className="flex-1 flex items-center justify-center bg-card">
                    <div className="text-center text-muted-foreground">
                      <FileIcon className="size-10 mx-auto mb-3 opacity-40" />
                      <p className="text-sm font-medium">
                        {t("evolution.dataPrep.selectFileTitle")}
                      </p>
                      <p className="text-xs mt-1 opacity-70">
                        {t("evolution.dataPrep.selectFileDescription")}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </Panel>
          </Group>
        </div>
      </div>

      <div className="shrink-0 py-2">
        <div className="flex justify-center">
          <Button onClick={onNext}>{t("common.nextStep")}</Button>
        </div>
      </div>
    </div>
  )
}
