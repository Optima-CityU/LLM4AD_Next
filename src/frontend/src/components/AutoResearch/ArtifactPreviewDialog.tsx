import {
  AlertTriangle,
  Download,
  FileCode,
  File as FileIcon,
  FileText,
  FolderOpen,
  ImageIcon,
  Loader2,
} from "lucide-react"
import { useEffect, useId, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

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
  downloadResearchArtifact,
  fetchResearchArtifact,
} from "@/hooks/useAutoResearch"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

/** 被点击预览的产物文件（下载端点回字节，前端按类型渲染）。 */
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
// 文本 / Markdown 内联预览的大小上限（超出提示下载，避免卡渲染）。
const TEXT_MAX = 512 * 1024

type PreviewKind = "image" | "pdf" | "markdown" | "text" | "binary" | "folder"

/** 按文件名推断预览方式；以 ``/`` 结尾视为目录（下载端点不支持，仅提示）。 */
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

/** 产物文件的类型小图标（文件树 / 预览标题共用）。 */
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

/** 字节数格式化（B / K / M）。 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`
}

/**
 * 产物预览弹框：按类型内联渲染文本 / 代码 / Markdown / 图片 / PDF。
 *
 * 复用下载端点（后端回文件字节）拉取内容——无需专门的预览接口。无法内联的
 * 二进制或超大文本退化为「仅下载」。右侧产物树与对话内的「本阶段产物」共用。
 *
 * 支持左侧文件列表：传入 fileList 可在预览时切换同组文件，不传则只显示当前文件。
 */
export default function ArtifactPreviewDialog({
  sessionId,
  file,
  fileList,
  onClose,
  onFileChange,
}: {
  sessionId: string
  file: PreviewFile | null
  fileList?: PreviewFile[]
  onClose: () => void
  onFileChange?: (file: PreviewFile) => void
}) {
  const { t } = useTranslation()
  useHljsTheme()
  const mdId = useId()
  // 用 path 判目录（以 / 结尾）；扩展名仍按 name 推断。
  const kind = file ? classifyArtifact(file.path || file.name) : "binary"
  const isFolder = kind === "folder"
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [tooLarge, setTooLarge] = useState(false)

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

  const triggerDownload = () => {
    if (!file) return
    void downloadResearchArtifact(sessionId, file.path).catch((err: unknown) =>
      toast.error((err as Error)?.message ?? "download failed"),
    )
  }

  const hasFileList = fileList && fileList.length > 1

  return (
    <Dialog open={!!file} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className={cn(
          "gap-3 w-[92vw]",
          hasFileList ? "sm:max-w-6xl" : "sm:max-w-5xl",
        )}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 min-w-0">
            <FileKindIcon name={file?.name ?? ""} />
            <span className="truncate text-sm">{file?.name}</span>
            {!isFolder && file?.size != null && (
              <span className="shrink-0 text-[11px] font-normal text-muted-foreground tabular-nums">
                {formatSize(file.size)}
              </span>
            )}
            {/* 目录无下载端点，隐藏下载按钮避免 404。 */}
            {!isFolder && (
              <button
                type="button"
                onClick={triggerDownload}
                title={t("autoResearch.artifacts.download")}
                className="shrink-0 text-muted-foreground hover:text-primary transition-colors"
              >
                <Download className="size-4" />
              </button>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className={cn("flex gap-3 h-[74vh] min-h-[420px] min-w-0")}>
          {/* 左侧文件列表 */}
          {hasFileList && (
            <div className="w-56 shrink-0 flex flex-col rounded-md border border-border/50 bg-muted/20">
              <div className="shrink-0 border-b border-border/50 bg-muted/40 px-3 py-2 backdrop-blur-sm">
                <span className="text-xs font-semibold text-muted-foreground">
                  {t("autoResearch.artifacts.fileList", "文件列表")} (
                  {fileList.length})
                </span>
              </div>
              <ul className="flex-1 overflow-y-auto p-2 space-y-0.5">
                {fileList.map((f) => (
                  <li key={f.path}>
                    <button
                      type="button"
                      onClick={() => onFileChange?.(f)}
                      className={cn(
                        "w-full flex items-center gap-1.5 rounded px-2 py-1.5 text-left text-xs transition-colors",
                        f.path === file?.path
                          ? "bg-primary/15 text-primary font-medium"
                          : "text-foreground/80 hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      <FileKindIcon name={f.name} />
                      <span className="flex-1 truncate" title={f.name}>
                        {f.name}
                      </span>
                      {f.size != null && (
                        <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/60">
                          {formatSize(f.size)}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 右侧预览区域 */}
          <div className="flex-1 min-w-0 overflow-auto rounded-md border border-border/50 bg-muted/20">
            {isFolder ? (
              <div className="flex flex-col items-center gap-3 py-16 px-4 text-center">
                <FolderOpen className="size-8 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                  {t("autoResearch.artifacts.previewFolder")}
                </p>
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                {t("autoResearch.artifacts.previewLoading")}
              </div>
            ) : error ? (
              <div className="flex flex-col items-center gap-2 py-16 px-4 text-center text-sm text-destructive">
                <AlertTriangle className="size-6" />
                {t("autoResearch.artifacts.previewError")}
                <span className="text-xs text-muted-foreground break-all">
                  {error}
                </span>
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
              <div className="flex items-center justify-center p-4">
                <img
                  src={url}
                  alt={file?.name}
                  className="max-w-full max-h-[64vh] object-contain"
                />
              </div>
            ) : kind === "pdf" && url ? (
              <iframe
                src={url}
                title={file?.name ?? "pdf"}
                className="w-full h-[70vh] bg-white"
              />
            ) : kind === "markdown" && text != null ? (
              <div className="prose prose-sm dark:prose-invert max-w-none p-4 prose-headings:text-foreground prose-headings:font-semibold prose-h1:text-lg prose-h2:text-base prose-pre:bg-transparent prose-pre:p-0">
                <Markdown
                  remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                  rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
                  components={makeMarkdownComponents(mdId)}
                >
                  {text}
                </Markdown>
              </div>
            ) : text != null ? (
              <pre className="overflow-x-auto p-4 text-xs leading-relaxed font-mono text-foreground/90 whitespace-pre">
                {text}
              </pre>
            ) : null}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
