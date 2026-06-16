import Editor, { loader, type Monaco } from "@monaco-editor/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ChevronLeft,
  ChevronRight,
  File as FileIcon,
  Loader2,
  MapPin,
  Save,
  Tag,
} from "lucide-react"
import * as monaco from "monaco-editor"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { Group, Panel, Separator } from "react-resizable-panels"
import { toast } from "sonner"

import type { FileTreeNode } from "@/client"
import { Llm4AdTasksService } from "@/client"
import { useTheme } from "@/components/theme-provider"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

import FileTreeView from "../FileTreeView"
import type { EvolveBlockPosition } from "./types"
import { getCommentStyle, getLanguageFromPath } from "./types"
import {
  countEvolveBlocksInContent,
  parseEvolveBlocks,
  useEvolveBlocks,
} from "./useEvolveBlocks"

loader.config({ monaco })

interface ManualAnnotationTabProps {
  taskId: string
  readOnly?: boolean
}

export default function ManualAnnotationTab({
  taskId,
  readOnly,
}: ManualAnnotationTabProps) {
  const { t } = useTranslation()
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme !== "light"
  const queryClient = useQueryClient()
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const pendingJumpRef = useRef<EvolveBlockPosition | null>(null)

  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [currentBlockIndex, setCurrentBlockIndex] = useState(0)
  const [selectionRange, setSelectionRange] = useState<{
    startLine: number
    endLine: number
  } | null>(null)
  const [showMarkButton, setShowMarkButton] = useState(false)
  const [badgeCounts, setBadgeCounts] = useState<Record<string, number>>({})

  // Fetch file tree
  const { data: treeData } = useQuery({
    queryKey: ["dataTree", taskId],
    queryFn: () => Llm4AdTasksService.getTaskDataTree({ taskId }),
    select: (res) => (res as { tree: FileTreeNode[] }).tree ?? [],
  })

  // Fetch file content when selected
  const { data: fetchedContent, isLoading: isLoadingFile } = useQuery({
    queryKey: ["dataFile", taskId, selectedPath],
    queryFn: () =>
      Llm4AdTasksService.getTaskDataFile({
        taskId,
        filePath: selectedPath!,
      }),
    enabled: !!selectedPath,
    select: (res) => (res as { content: string }).content ?? "",
  })

  const jumpToBlock = useCallback((block: EvolveBlockPosition) => {
    const editor = editorRef.current
    if (!editor) return
    editor.revealLineInCenter(block.startLine)
    const model = editor.getModel()
    const endCol = model ? model.getLineMaxColumn(block.endLine) : 1
    editor.setSelection(
      new monaco.Selection(block.startLine, 1, block.endLine, endCol),
    )
  }, [])

  useEffect(() => {
    if (fetchedContent !== undefined) {
      setFileContent(fetchedContent)
      setIsDirty(false)
      setCurrentBlockIndex(0)

      // Auto-jump to first evolve block when opening a file
      const firstBlock = parseEvolveBlocks(fetchedContent)[0]
      if (firstBlock) {
        if (editorRef.current) {
          // Editor ready — defer to next tick so Monaco syncs the new content
          setTimeout(() => jumpToBlock(firstBlock), 50)
        } else {
          // Editor not mounted yet — handleEditorMount will pick this up
          pendingJumpRef.current = firstBlock
        }
      } else {
        pendingJumpRef.current = null
      }
    }
  }, [fetchedContent, jumpToBlock])

  // Parse EVOLVE blocks in current file
  const { blocks, count: blockCount } = useEvolveBlocks(fileContent)

  // Compute badge counts from tree (simplified: count from loaded files)
  useEffect(() => {
    if (!treeData) return
    const counts: Record<string, number> = {}
    const collectFiles = (nodes: FileTreeNode[]) => {
      for (const node of nodes) {
        if (node.type === "file") {
          counts[node.path] = 0
        }
        if (node.children) collectFiles(node.children)
      }
    }
    collectFiles(treeData)
    if (selectedPath && fileContent) {
      counts[selectedPath] = countEvolveBlocksInContent(fileContent)
    }
    setBadgeCounts(counts)
  }, [treeData, selectedPath, fileContent])

  // Save mutation
  const saveMutation = useMutation({
    mutationFn: () =>
      Llm4AdTasksService.updateTaskDataFile({
        taskId,
        requestBody: { file_path: selectedPath!, content: fileContent! },
      }),
    onSuccess: () => {
      setIsDirty(false)
      queryClient.invalidateQueries({
        queryKey: ["dataFile", taskId, selectedPath],
      })
      toast.success(t("evolution.evolveAnnotation.saveSuccess"))
    },
    onError: () => {
      toast.error(t("evolution.evolveAnnotation.saveError"))
    },
  })

  const handleSelectFile = (path: string, isDir: boolean) => {
    if (isDir) return
    if (isDirty) {
      // Auto-save before switching
      if (selectedPath && fileContent) {
        saveMutation.mutate()
      }
    }
    setSelectedPath(path)
    setSelectionRange(null)
    setShowMarkButton(false)
  }

  const handleEditorMount = (editor: monaco.editor.IStandaloneCodeEditor) => {
    editorRef.current = editor

    editor.onDidChangeCursorSelection((e) => {
      const sel = e.selection
      if (
        sel.startLineNumber === sel.endLineNumber &&
        sel.startColumn === sel.endColumn
      ) {
        setSelectionRange(null)
        setShowMarkButton(false)
        return
      }

      const startLine = Math.min(sel.startLineNumber, sel.endLineNumber)
      const endLine = Math.max(sel.startLineNumber, sel.endLineNumber)

      if (startLine === endLine) {
        setSelectionRange(null)
        setShowMarkButton(false)
        return
      }

      setSelectionRange({ startLine, endLine })
      setShowMarkButton(true)
    })

    // Handle deferred jump from a file loaded before the editor mounted
    if (pendingJumpRef.current) {
      const block = pendingJumpRef.current
      pendingJumpRef.current = null
      setTimeout(() => jumpToBlock(block), 50)
    }
  }

  const handleMark = useCallback(() => {
    if (!selectionRange || !fileContent || !selectedPath || readOnly) return

    const lines = fileContent.split("\n")
    const { startLine, endLine } = selectionRange

    // Check if selection already contains EVOLVE markers
    const selectedLines = lines.slice(startLine - 1, endLine)
    const hasMarker = selectedLines.some(
      (l) => /EVOLVE[_\s-]START/i.test(l) || /EVOLVE[_\s-]END/i.test(l),
    )
    if (hasMarker) {
      toast.warning(t("evolution.evolveAnnotation.alreadyMarked"))
      return
    }

    const [startMarker, endMarker] = getCommentStyle(selectedPath)

    // Detect indentation of the first selected line
    const indent = lines[startLine - 1]?.match(/^(\s*)/)?.[1] ?? ""

    const newLines = [...lines]
    newLines.splice(endLine, 0, `${indent}${endMarker}`)
    newLines.splice(startLine - 1, 0, `${indent}${startMarker}`)

    const newContent = newLines.join("\n")
    setFileContent(newContent)
    setIsDirty(true)
    setSelectionRange(null)
    setShowMarkButton(false)

    toast.success(t("evolution.evolveAnnotation.markSuccess"))
  }, [selectionRange, fileContent, selectedPath, readOnly, t])

  const handleNavigateBlock = (direction: "prev" | "next") => {
    if (blocks.length === 0 || !editorRef.current) return
    let idx = currentBlockIndex
    if (direction === "next") {
      idx = (idx + 1) % blocks.length
    } else {
      idx = (idx - 1 + blocks.length) % blocks.length
    }
    setCurrentBlockIndex(idx)
    const block = blocks[idx]
    // Select content between markers (inclusive of marker lines)
    editorRef.current.revealLineInCenter(block.startLine)
    const model = editorRef.current.getModel()
    const endCol = model ? model.getLineMaxColumn(block.endLine) : 1
    editorRef.current.setSelection(
      new monaco.Selection(block.startLine, 1, block.endLine, endCol),
    )
  }

  const language = selectedPath
    ? getLanguageFromPath(selectedPath)
    : "plaintext"

  return (
    <div className="flex flex-col h-full px-4 py-2" ref={containerRef}>
      <div className="rounded-lg border overflow-hidden flex-1 min-h-0">
        <Group orientation="horizontal" className="h-full">
          {/* File tree panel */}
          <Panel defaultSize="25%" minSize="15%">
            <div className="h-full flex flex-col border-r bg-card">
              <div className="shrink-0 h-9 flex items-center px-3 border-b bg-muted/30">
                <h4 className="text-xs font-medium text-muted-foreground">
                  {t("evolution.evolveAnnotation.fileTree")}
                </h4>
              </div>
              <div className="flex-1 overflow-y-auto p-1">
                {treeData ? (
                  <FileTreeView
                    tree={treeData}
                    selectedPath={selectedPath}
                    onSelectFile={handleSelectFile}
                    badgeCounts={badgeCounts}
                  />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  </div>
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
              {/* Toolbar */}
              {selectedPath && (
                <div
                  className={`shrink-0 h-9 flex items-center justify-between px-3 border-b ${isDark ? "bg-[#0D1A2D] border-[#1a2d45]" : "bg-muted/30 border-border"}`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="text-xs font-mono truncate">
                      {selectedPath}
                    </span>
                    {isDirty && (
                      <Badge
                        variant="secondary"
                        className="text-[10px] px-1 py-0"
                      >
                        {t("evolution.evolveAnnotation.unsaved")}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {blockCount > 0 && (
                      <>
                        <Badge
                          variant="outline"
                          className="text-[10px] px-1.5 py-0 gap-1"
                        >
                          <Tag className="size-2.5" />
                          {t("evolution.evolveAnnotation.blockCount", {
                            count: blockCount,
                          })}
                        </Badge>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => handleNavigateBlock("prev")}
                            >
                              <ChevronLeft className="size-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {t("evolution.evolveAnnotation.prevBlock")}
                          </TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => handleNavigateBlock("next")}
                            >
                              <ChevronRight className="size-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {t("evolution.evolveAnnotation.nextBlock")}
                          </TooltipContent>
                        </Tooltip>
                      </>
                    )}
                    {!readOnly && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 w-6 p-0"
                            onClick={() => saveMutation.mutate()}
                            disabled={!isDirty || saveMutation.isPending}
                          >
                            {saveMutation.isPending ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Save className="size-3.5" />
                            )}
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          {t("evolution.evolveAnnotation.save")}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                </div>
              )}

              {/* Editor area */}
              {selectedPath ? (
                isLoadingFile ? (
                  <div className="flex-1 flex items-center justify-center">
                    <Loader2 className="size-5 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <div className="flex-1 min-h-0">
                    <Editor
                      value={fileContent ?? ""}
                      language={language}
                      theme={isDark ? "custom-dark" : "custom-light"}
                      onChange={(value) => {
                        setFileContent(value ?? "")
                        setIsDirty(true)
                      }}
                      onMount={handleEditorMount}
                      beforeMount={(m: Monaco) => {
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
                        readOnly,
                        minimap: { enabled: false },
                        fontSize: 13,
                        lineNumbers: "on",
                        wordWrap: "on",
                        scrollBeyondLastLine: false,
                        automaticLayout: true,
                        glyphMargin: true,
                      }}
                    />
                  </div>
                )
              ) : (
                <div className="flex-1 flex items-center justify-center">
                  <div className="text-center text-muted-foreground">
                    <FileIcon className="size-10 mx-auto mb-3 opacity-40" />
                    <p className="text-sm font-medium">
                      {t("evolution.evolveAnnotation.selectFile")}
                    </p>
                    <p className="text-xs mt-1 opacity-70">
                      {t("evolution.evolveAnnotation.selectFileHint")}
                    </p>
                  </div>
                </div>
              )}

              {/* Fixed mark button at bottom-right of editor */}
              {showMarkButton && selectionRange && !readOnly && (
                <Button
                  size="sm"
                  className="absolute z-50 shadow-lg gap-1 h-7 text-xs bottom-4 right-4"
                  onClick={handleMark}
                >
                  <MapPin className="size-3" />
                  {t("evolution.evolveAnnotation.markButton")}
                  <span className="text-muted-foreground/70 ml-0.5">
                    L{selectionRange.startLine}-{selectionRange.endLine}
                  </span>
                </Button>
              )}
            </div>
          </Panel>
        </Group>
      </div>
    </div>
  )
}
