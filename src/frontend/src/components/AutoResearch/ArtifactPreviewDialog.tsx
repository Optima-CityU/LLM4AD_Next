import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileCode,
  File as FileIcon,
  FileText,
  Folder,
  FolderOpen,
  ImageIcon,
  Languages,
  Loader2,
  MousePointerClick,
  Square,
  WandSparkles,
} from "lucide-react"
import { useEffect, useId, useMemo, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import type { ResearchArtifactTreeNode } from "@/client"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import {
  downloadResearchArtifact,
  fetchResearchArtifact,
  useResearchArtifactTree,
} from "@/hooks/useAutoResearch"
import {
  useArtifactTranslation,
  type ArtifactTranslationStatus,
} from "@/hooks/useArtifactTranslation"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

export interface PreviewFile {
  path: string
  name: string
  size: number | null
}

const IMAGE_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "bmp",
  "ico",
  "avif",
])
const TEXT_EXTS = new Set([
  "py",
  "js",
  "ts",
  "tsx",
  "jsx",
  "json",
  "yaml",
  "yml",
  "txt",
  "tex",
  "log",
  "jsonl",
  "ndjson",
  "csv",
  "tsv",
  "toml",
  "ini",
  "cfg",
  "conf",
  "sh",
  "bash",
  "xml",
  "html",
  "htm",
  "css",
  "scss",
  "sql",
  "r",
  "java",
  "c",
  "cpp",
  "cc",
  "h",
  "hpp",
  "go",
  "rs",
  "rb",
  "php",
  "kt",
  "swift",
  "env",
  "properties",
])
const TEXT_MAX = 512 * 1024

type PreviewKind = "image" | "pdf" | "markdown" | "text" | "binary" | "folder"
type PreviewView = "source" | "translation"

function classifyArtifact(nameOrPath: string): PreviewKind {
  if (nameOrPath.endsWith("/")) return "folder"
  const lower = nameOrPath.toLowerCase()
  const ext = lower.includes(".") ? (lower.split(".").pop() ?? "") : lower
  if (IMAGE_EXTS.has(ext)) return "image"
  if (ext === "pdf") return "pdf"
  if (ext === "md" || ext === "markdown") return "markdown"
  if (TEXT_EXTS.has(ext)) return "text"
  return "binary"
}

export function FileKindIcon({ name }: { name: string }) {
  const ext = name.split(".").pop()?.toLowerCase() ?? ""
  const cls = "size-3.5 shrink-0"
  if (["png", "jpg", "jpeg", "gif", "svg", "webp", "pdf"].includes(ext))
    return <ImageIcon className={cn(cls, "text-purple-500")} />
  if (["py", "js", "ts", "json", "yaml", "yml"].includes(ext))
    return <FileCode className={cn(cls, "text-cyan-500")} />
  if (["md", "txt", "tex", "log"].includes(ext))
    return <FileText className={cn(cls, "text-muted-foreground")} />
  return <FileIcon className={cn(cls, "text-muted-foreground")} />
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`
}

/** 去掉尾部斜杠，得到归一化路径。 */
function normalizePath(p: string): string {
  return p.replace(/\/+$/, "")
}

/** 路径最后一段（文件名 / 目录名）。 */
function lastSegment(p: string): string {
  const norm = normalizePath(p)
  return norm.includes("/") ? (norm.split("/").pop() ?? norm) : norm
}

/** 按 path 在树中精确定位节点。 */
function findNodeByPath(
  root: ResearchArtifactTreeNode,
  target: string,
): ResearchArtifactTreeNode | null {
  if (root.path === target) return root
  for (const c of root.children ?? []) {
    const hit = findNodeByPath(c, target)
    if (hit) return hit
  }
  return null
}

/** 收集节点及其所有子孙目录的 path（默认展开用）。 */
function collectDirPaths(node: ResearchArtifactTreeNode): string[] {
  if (!node.is_dir) return []
  const out = [node.path]
  for (const c of node.children ?? []) out.push(...collectDirPaths(c))
  return out
}

/**
 * 依据传入 path 计算左侧目录树的显示范围与初始激活文件。
 *
 * - 取 path 的「第一层目录」作为范围根：展示该目录下的所有文件（若第一层本身
 *   是文件，则只展示该文件）。
 * - path 指向文件 → 直接激活该文件；指向目录 → 不激活，右侧提示请选择文件。
 * - 树未加载时用扩展名/尾斜杠兜底判断文件/目录。
 */
function computeScope(
  root: ResearchArtifactTreeNode | null,
  path: string,
): {
  nodes: ResearchArtifactTreeNode[]
  scopeName: string | null
  initialFile: PreviewFile | null
  expandInit: Set<string>
} {
  const norm = normalizePath(path)
  const name = lastSegment(norm)
  const firstSeg = norm.includes("/") ? norm.split("/")[0] : norm

  if (!root) {
    // 树未就绪：用尾斜杠 / 扩展名兜底判断是否文件。
    const looksFile = !path.endsWith("/") && name.includes(".")
    return {
      nodes: [],
      scopeName: null,
      initialFile: looksFile ? { path: norm, name, size: null } : null,
      expandInit: new Set(),
    }
  }

  const node = findNodeByPath(root, norm)
  const isFile = node ? !node.is_dir : !path.endsWith("/") && name.includes(".")

  // 范围根 = 第一层节点（与顶层同名）。
  const scopeNode =
    (root.children ?? []).find((c) => c.path === firstSeg) ?? null

  let nodes: ResearchArtifactTreeNode[]
  let scopeName: string | null
  if (scopeNode?.is_dir) {
    nodes = scopeNode.children ?? []
    scopeName = scopeNode.name
  } else if (scopeNode) {
    nodes = [scopeNode]
    scopeName = null
  } else {
    nodes = root.children ?? []
    scopeName = null
  }

  // 默认展开范围内所有目录，方便浏览与定位激活文件。
  const expandInit = new Set<string>()
  for (const n of nodes) for (const p of collectDirPaths(n)) expandInit.add(p)

  return {
    nodes,
    scopeName,
    initialFile: isFile ? { path: norm, name, size: node?.size ?? null } : null,
    expandInit,
  }
}

function TranslationStatusBadge({
  status,
  isStreaming,
  t,
}: {
  status: ArtifactTranslationStatus
  isStreaming: boolean
  t: ReturnType<typeof useTranslation>["t"]
}) {
  if (status === "idle") return null

  const config: Record<
    Exclude<ArtifactTranslationStatus, "idle">,
    { label: string; className: string; icon?: ReactNode }
  > = {
    cached: {
      label: t("autoResearch.artifacts.translationCached"),
      className: "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    },
    translating: {
      label: isStreaming
        ? t("autoResearch.artifacts.translationStreaming")
        : t("autoResearch.artifacts.translationStarting"),
      className: "border-blue-500/20 bg-blue-500/10 text-blue-600 dark:text-blue-400",
      icon: <Loader2 className="size-3 animate-spin" />,
    },
    completed: {
      label: t("autoResearch.artifacts.translationReady"),
      className: "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
      icon: <Check className="size-3" />,
    },
    failed: {
      label: t("autoResearch.artifacts.translationFailed"),
      className: "border-destructive/20 bg-destructive/10 text-destructive",
      icon: <AlertTriangle className="size-3" />,
    },
    cancelled: {
      label: t("autoResearch.artifacts.translationCancelled"),
      className: "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400",
      icon: <Square className="size-3 fill-current" />,
    },
  }

  const current = config[status]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        current.className,
      )}
    >
      {current.icon}
      {current.label}
    </span>
  )
}

function ArtifactTextContent({
  kind,
  content,
  mdId,
}: {
  kind: PreviewKind
  content: string
  mdId: string
}) {
  if (kind === "markdown") {
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none p-4 prose-headings:text-foreground prose-headings:font-semibold prose-h1:text-lg prose-h2:text-base prose-pre:bg-transparent prose-pre:p-0">
        <Markdown
          remarkPlugins={MARKDOWN_REMARK_PLUGINS}
          rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
          components={makeMarkdownComponents(mdId)}
        >
          {content}
        </Markdown>
      </div>
    )
  }

  return (
    <pre className="overflow-x-auto p-4 text-xs leading-relaxed font-mono text-foreground/90 whitespace-pre">
      {content}
    </pre>
  )
}

/** 左侧目录树的单个节点：目录可折叠，文件点击激活。 */
function ArtifactTreeNode({
  node,
  depth,
  selectedPath,
  expanded,
  onToggle,
  onSelect,
  sessionId,
}: {
  node: ResearchArtifactTreeNode
  depth: number
  selectedPath: string | null
  expanded: Set<string>
  onToggle: (path: string) => void
  onSelect: (file: PreviewFile) => void
  sessionId: string
}) {
  const { t } = useTranslation()
  const pad = { paddingLeft: `${depth * 12 + 4}px` }

  if (node.is_dir) {
    const open = expanded.has(node.path)
    return (
      <li>
        <button
          type="button"
          style={pad}
          onClick={() => onToggle(node.path)}
          className="w-full flex items-center gap-1 py-1 pr-1 rounded hover:bg-primary/[0.06] text-xs text-foreground/80 text-left"
        >
          {open ? (
            <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
          )}
          {open ? (
            <FolderOpen className="size-3.5 shrink-0 text-amber-500" />
          ) : (
            <Folder className="size-3.5 shrink-0 text-amber-500" />
          )}
          <span className="truncate">{node.name}</span>
        </button>
        {open && (node.children?.length ?? 0) > 0 && (
          <ul className="relative space-y-0.5">
            <span
              aria-hidden
              className="pointer-events-none absolute inset-y-0 w-px bg-border/60 dark:bg-border/40"
              style={{ left: `${depth * 12 + 11}px` }}
            />
            {node.children?.map((c) => (
              <ArtifactTreeNode
                key={c.path}
                node={c}
                depth={depth + 1}
                selectedPath={selectedPath}
                expanded={expanded}
                onToggle={onToggle}
                onSelect={onSelect}
                sessionId={sessionId}
              />
            ))}
          </ul>
        )}
      </li>
    )
  }

  const active = node.path === selectedPath
  return (
    <li>
      <div
        style={pad}
        className={cn(
          "group flex items-center gap-1 py-1 pr-1 rounded text-xs transition-colors",
          active ? "bg-primary/15 text-primary" : "hover:bg-primary/[0.06]",
        )}
      >
        <button
          type="button"
          onClick={() =>
            onSelect({
              path: node.path,
              name: node.name,
              size: node.size ?? null,
            })
          }
          className="flex-1 min-w-0 flex items-center gap-1 text-left"
        >
          <span className="w-3 shrink-0" />
          <FileKindIcon name={node.name} />
          <span
            className={cn(
              "truncate flex-1",
              active ? "font-medium" : "text-foreground/80",
            )}
            title={node.path}
          >
            {node.name}
          </span>
          {node.size != null && (
            <span className="text-[10px] text-muted-foreground/60 shrink-0 tabular-nums">
              {formatSize(node.size)}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={() =>
            void downloadResearchArtifact(sessionId, node.path).catch(
              (err: unknown) =>
                toast.error((err as Error)?.message ?? "download failed"),
            )
          }
          title={t("autoResearch.artifacts.download")}
          className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-muted-foreground hover:text-primary shrink-0 transition-opacity"
        >
          <Download className="size-3.5" />
        </button>
      </div>
    </li>
  )
}

/**
 * 右侧预览面板：按文件类型渲染（图片 / PDF / markdown / 文本 / 二进制），
 * 文本与 markdown 额外提供原文 · 译文切换与复制。``file`` 为 null 时提示请选择文件。
 */
function PreviewPane({
  sessionId,
  file,
}: {
  sessionId: string
  file: PreviewFile | null
}) {
  const { t } = useTranslation()
  useHljsTheme()
  const mdId = useId()
  const [copiedText, copy] = useCopyToClipboard()
  const kind = file ? classifyArtifact(file.path || file.name) : "binary"
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [tooLarge, setTooLarge] = useState(false)
  const [activeView, setActiveView] = useState<PreviewView>("source")
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)

  const canTranslate =
    !!file &&
    !tooLarge &&
    (kind === "text" || kind === "markdown") &&
    !loading &&
    !error &&
    text != null

  const translation = useArtifactTranslation({
    sessionId,
    filePath: canTranslate ? (file?.path ?? null) : null,
    enabled: !!file && canTranslate,
  })

  useEffect(() => {
    if (!file) return
    let objectUrl: string | null = null
    let cancelled = false
    setError(null)
    setText(null)
    setUrl(null)
    setTooLarge(false)
    setLoading(false)

    if (kind === "binary" || kind === "folder") return

    if (
      (kind === "text" || kind === "markdown") &&
      file.size != null &&
      file.size > TEXT_MAX
    ) {
      setTooLarge(true)
      return
    }

    setLoading(true)
    ;(async () => {
      try {
        const resp = await fetchResearchArtifact(sessionId, file.path)
        if (cancelled) return
        if (kind === "text" || kind === "markdown") {
          const txt = await resp.text()
          if (!cancelled) setText(txt)
        } else {
          const blob = await resp.blob()
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setUrl(objectUrl)
        }
      } catch (e) {
        if (!cancelled) setError((e as Error)?.message ?? "error")
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [file, kind, sessionId])

  useEffect(() => {
    setActiveView("source")
    setShowConfirmDialog(false)
  }, [file?.path])

  useEffect(() => {
    if (!canTranslate && activeView === "translation") {
      setActiveView("source")
    }
  }, [activeView, canTranslate])

  // 切换到译文标签时自动触发翻译
  useEffect(() => {
    if (activeView === "translation" && canTranslate && !translation.hasTranslation && !translation.isStreaming) {
      const textLength = text?.length ?? 0
      if (textLength > 10000) {
        setShowConfirmDialog(true)
      } else {
        void handleTranslate(false)
      }
    }
  }, [activeView, canTranslate])

  const triggerDownload = () => {
    if (!file) return
    void downloadResearchArtifact(sessionId, file.path).catch((err: unknown) =>
      toast.error((err as Error)?.message ?? "download failed"),
    )
  }

  const handleTranslate = async (force = false) => {
    if (!canTranslate) return
    setShowConfirmDialog(false)
    try {
      await translation.startTranslation({ force })
    } catch (err) {
      toast.error((err as Error)?.message ?? t("autoResearch.artifacts.translationFailed"))
    }
  }

  const handleConfirmTranslation = () => {
    void handleTranslate(false)
  }

  const handleCancelTranslation = () => {
    setShowConfirmDialog(false)
    setActiveView("source")
  }

  const copyContent = useMemo(() => {
    if (activeView === "translation") return translation.content
    return text ?? ""
  }, [activeView, text, translation.content])

  const canCopy = (kind === "markdown" || kind === "text") && copyContent.length > 0

  const handleCopy = async () => {
    const ok = await copy(copyContent)
    if (ok) {
      toast.success(
        activeView === "translation"
          ? t("autoResearch.artifacts.copyTranslationSuccess")
          : t("autoResearch.artifacts.copySourceSuccess"),
      )
    } else {
      toast.error(t("autoResearch.artifacts.copyFailed"))
    }
  }

  // 未选择文件：提示从左侧选择。
  if (!file) {
    return (
      <div className="flex flex-1 min-w-0 flex-col items-center justify-center gap-3 rounded-md border border-border/50 bg-muted/20 px-6 text-center">
        <MousePointerClick className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {t("autoResearch.artifacts.selectFile")}
        </p>
      </div>
    )
  }

  return (
    <div className="flex-1 min-w-0 rounded-md border border-border/50 bg-muted/20 overflow-hidden">
      {/* 翻译确认对话框 */}
      {showConfirmDialog && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="space-y-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="size-5 shrink-0 text-amber-500 mt-0.5" />
                <div className="space-y-1">
                  <h3 className="text-sm font-semibold text-foreground">
                    {t("autoResearch.artifacts.confirmTranslationTitle")}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {t("autoResearch.artifacts.confirmTranslationMessage", {
                      chars: text?.length ?? 0,
                    })}
                  </p>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={handleCancelTranslation}
                  className="px-3 py-1.5 rounded-md text-xs font-medium border border-border bg-background hover:bg-muted transition-colors"
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="button"
                  onClick={handleConfirmTranslation}
                  className="px-3 py-1.5 rounded-md text-xs font-medium border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                >
                  {t("common.confirm")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {canTranslate ? (
        <Tabs
          value={activeView}
          onValueChange={(value) => setActiveView(value as PreviewView)}
          className="h-full gap-0"
        >
          <div className="flex items-center justify-between gap-3 border-b border-border/50 bg-background/80 px-3 py-2 backdrop-blur-sm">
            <div className="flex min-w-0 items-center gap-2">
              <TabsList className="h-8">
                <TabsTrigger value="source" className="text-xs px-3">
                  {t("autoResearch.artifacts.source")}
                </TabsTrigger>
                <TabsTrigger value="translation" className="text-xs px-3">
                  {t("autoResearch.artifacts.translation")}
                </TabsTrigger>
              </TabsList>
              <TranslationStatusBadge
                status={translation.status}
                isStreaming={translation.isStreaming}
                t={t}
              />
            </div>

            <div className="flex shrink-0 items-center gap-2">
              {translation.isStreaming && (
                <button
                  type="button"
                  onClick={translation.stop}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
                >
                  <Square className="size-3.5 fill-current" />
                  {t("autoResearch.artifacts.stopTranslation")}
                </button>
              )}

              <button
                type="button"
                onClick={() => void handleCopy()}
                disabled={!canCopy}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                {copiedText === copyContent && copyContent ? (
                  <Check className="size-3.5 text-emerald-500" />
                ) : (
                  <Copy className="size-3.5" />
                )}
                {activeView === "translation"
                  ? t("autoResearch.artifacts.copyTranslation")
                  : t("autoResearch.artifacts.copySource")}
              </button>
            </div>
          </div>

          <TabsContent value="source" className="mt-0 h-full overflow-auto">
            {text != null ? <ArtifactTextContent kind={kind} content={text} mdId={mdId} /> : null}
          </TabsContent>

          <TabsContent value="translation" className="mt-0 h-full overflow-auto">
            {translation.content ? (
              <ArtifactTextContent
                kind={kind}
                content={translation.content}
                mdId={`${mdId}-translation`}
              />
            ) : translation.status === "failed" ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                <AlertTriangle className="size-8 text-destructive" />
                <div className="space-y-1">
                  <p className="text-sm font-medium text-foreground">
                    {t("autoResearch.artifacts.translationFailed")}
                  </p>
                  {translation.error && (
                    <p className="text-xs text-muted-foreground break-all">
                      {translation.error}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleTranslate(false)}
                    className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                  >
                    <Languages className="size-3.5" />
                    {t("common.retry")}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleTranslate(true)}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
                  >
                    <WandSparkles className="size-3.5" />
                    {t("autoResearch.artifacts.forceRetranslate")}
                  </button>
                </div>
              </div>
            ) : translation.status === "cancelled" ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                <Square className="size-8 fill-current text-amber-500" />
                <div className="space-y-1">
                  <p className="text-sm font-medium text-foreground">
                    {t("autoResearch.artifacts.translationCancelled")}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t("autoResearch.artifacts.translationCancelledHint")}
                  </p>
                </div>
              </div>
            ) : translation.isStreaming ? (
              <div className="flex h-full flex-col">
                {translation.content ? (
                  <ArtifactTextContent
                    kind={kind}
                    content={translation.content}
                    mdId={`${mdId}-translation`}
                  />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                    <Loader2 className="size-8 animate-spin text-primary" />
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-foreground">
                        {t("autoResearch.artifacts.translationStreaming")}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t("autoResearch.artifacts.translationStreamingHint")}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                <Languages className="size-8 text-muted-foreground/50" />
                <div className="space-y-1">
                  <p className="text-sm font-medium text-foreground">
                    {t("autoResearch.artifacts.noTranslationYet")}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t("autoResearch.artifacts.noTranslationYetHint")}
                  </p>
                </div>
              </div>
            )}
          </TabsContent>
        </Tabs>
      ) : loading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {t("autoResearch.artifacts.previewLoading")}
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-2 py-16 px-4 text-center text-sm text-destructive">
          <AlertTriangle className="size-6" />
          {t("autoResearch.artifacts.previewError")}
          <span className="text-xs text-muted-foreground break-all">{error}</span>
        </div>
      ) : tooLarge || kind === "binary" ? (
        <div className="flex flex-col items-center gap-3 py-16 px-4 text-center">
          <FileIcon className="size-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            {tooLarge
              ? t("autoResearch.artifacts.previewTooLarge")
              : t("autoResearch.artifacts.previewUnsupported")}
          </p>
          <button
            type="button"
            onClick={triggerDownload}
            className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 transition-colors"
          >
            <Download className="size-3.5" />
            {t("autoResearch.artifacts.download")}
          </button>
        </div>
      ) : kind === "image" && url ? (
        <div className="flex items-center justify-center p-4 h-full overflow-auto">
          <img
            src={url}
            alt={file.name}
            className="max-w-full max-h-[64vh] object-contain"
          />
        </div>
      ) : kind === "pdf" && url ? (
        <iframe
          src={url}
          title={file.name ?? "pdf"}
          className="w-full h-[70vh] bg-white"
        />
      ) : kind === "markdown" && text != null ? (
        <div className="h-full overflow-auto">
          <ArtifactTextContent kind={kind} content={text} mdId={mdId} />
        </div>
      ) : text != null ? (
        <div className="h-full overflow-auto">
          <ArtifactTextContent kind={kind} content={text} mdId={mdId} />
        </div>
      ) : null}
    </div>
  )
}

/**
 * 公共产物预览弹框：左侧目录树 + 右侧预览。
 *
 * 三个入口共用（顶部阶段抽屉的文件 / 右侧产物树的文件 / 中间门控卡片的阶段产物）。
 * 只需传 ``sessionId`` 与目标 ``path``（文件或目录）：
 * - 组件自行拉取整棵产物树，按 ``path`` 的第一层目录过滤出左侧树；
 * - ``path`` 指向文件则直接激活预览，指向目录则等待用户从左侧选择。
 */
export default function ArtifactPreviewDialog({
  sessionId,
  path,
  onClose,
}: {
  sessionId: string
  /** 目标文件或目录路径；null = 关闭弹框。 */
  path: string | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const treeQ = useResearchArtifactTree(path ? sessionId : null)
  const root = treeQ.data?.root ?? null

  const scope = useMemo(
    () => (path ? computeScope(root, path) : null),
    [root, path],
  )

  const [selected, setSelected] = useState<PreviewFile | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // 每次打开（path 变化）或树首次到位时：定位初始激活文件 + 展开范围内目录。
  // 以 path + 树是否就绪为键，避免用户后续手动切换文件被重置。
  const treeReady = !!root
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在 path / 树就绪变化时重置
  useEffect(() => {
    if (!path || !scope) {
      setSelected(null)
      setExpanded(new Set())
      return
    }
    setSelected(scope.initialFile)
    setExpanded(scope.expandInit)
  }, [path, treeReady])

  const toggleDir = (p: string) =>
    setExpanded((s) => {
      const n = new Set(s)
      if (n.has(p)) n.delete(p)
      else n.add(p)
      return n
    })

  const nodes = scope?.nodes ?? []
  const hasTree = nodes.length > 0

  return (
    <Dialog open={!!path} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="gap-3 w-[92vw] sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 min-w-0">
            {selected ? (
              <>
                <FileKindIcon name={selected.name} />
                <span className="truncate text-sm">{selected.name}</span>
                {selected.size != null && (
                  <span className="shrink-0 text-[11px] font-normal text-muted-foreground tabular-nums">
                    {formatSize(selected.size)}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() =>
                    void downloadResearchArtifact(
                      sessionId,
                      selected.path,
                    ).catch((err: unknown) =>
                      toast.error((err as Error)?.message ?? "download failed"),
                    )
                  }
                  title={t("autoResearch.artifacts.download")}
                  className="shrink-0 text-muted-foreground hover:text-primary transition-colors"
                >
                  <Download className="size-4" />
                </button>
              </>
            ) : (
              <>
                <FolderOpen className="size-4 shrink-0 text-amber-500" />
                <span className="truncate text-sm">
                  {scope?.scopeName ?? t("autoResearch.artifacts.fileList")}
                </span>
              </>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="flex gap-3 h-[74vh] min-h-[420px] min-w-0">
          {/* 左：目录树 */}
          <div className="w-60 shrink-0 flex flex-col rounded-md border border-border/50 bg-muted/20">
            <div className="shrink-0 border-b border-border/50 bg-muted/40 px-3 py-2 backdrop-blur-sm">
              <span className="text-xs font-semibold text-muted-foreground">
                {t("autoResearch.artifacts.fileList")}
              </span>
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {treeQ.isLoading ? (
                <div className="flex items-center gap-2 py-3 px-1 text-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" />
                  {t("autoResearch.artifacts.loading")}
                </div>
              ) : !hasTree ? (
                <p className="py-3 px-1 text-xs text-muted-foreground/60">
                  {t("autoResearch.artifacts.empty")}
                </p>
              ) : (
                <ul className="space-y-0.5">
                  {nodes.map((node) => (
                    <ArtifactTreeNode
                      key={node.path}
                      node={node}
                      depth={0}
                      selectedPath={selected?.path ?? null}
                      expanded={expanded}
                      onToggle={toggleDir}
                      onSelect={setSelected}
                      sessionId={sessionId}
                    />
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* 右：预览 */}
          <PreviewPane sessionId={sessionId} file={selected} />
        </div>
      </DialogContent>
    </Dialog>
  )
}
