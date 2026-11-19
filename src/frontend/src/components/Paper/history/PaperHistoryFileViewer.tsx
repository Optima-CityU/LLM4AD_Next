import { DiffEditor, loader } from "@monaco-editor/react"
import * as monaco from "monaco-editor"
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker"
import { useEffect, useId, useMemo, useRef, useState } from "react"
import Markdown, { type Components } from "react-markdown"
import rehypeKatex from "rehype-katex"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"

import type { PaperHistoryFile, PaperHistoryRevision } from "@/client"
import { getLanguageFromPath } from "@/components/Evolution/TaskDetail/steps/EvolveAnnotationStep/types"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { authFetch } from "@/utils/auth"
import { HistoryError, revisionFileUrl } from "./historyApi"
import { useHistoryText } from "./historyMessages"
import { resolveHistoryImagePath } from "./historyHelpers"
import "katex/dist/katex.min.css"

// Use the bundled editor and worker, including when the application runs offline.
loader.config({ monaco })
self.MonacoEnvironment = {
  ...self.MonacoEnvironment,
  getWorker: self.MonacoEnvironment?.getWorker ?? (() => new EditorWorker()),
}

type Side = { revision: PaperHistoryRevision; file: PaperHistoryFile }
type Props = {
  workspaceId: string
  path: string
  original: string | null
  modified: string | null
  binary: boolean
  mimeType: string
  before?: Side
  after?: Side
  view: "diff" | "preview"
  beforeSize?: number
  afterSize?: number
}

function AuthenticatedPreview({
  workspaceId,
  path,
  side,
  title,
  pdf,
  inlineImage = false,
}: {
  workspaceId: string
  path: string
  side: Side
  title: string
  pdf: boolean
  inlineImage?: boolean
}) {
  const text = useHistoryText()
  const [asset, setAsset] = useState<{ url?: string; error?: boolean }>({})
  const [retry, setRetry] = useState(0)
  const { id, state_hash: hash } = side.revision
  const mimeType = inlineImage ? "" : side.file.mime_type
  const Container = inlineImage ? "span" : "div"
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl: string | undefined
    setAsset({})
    void (async () => {
      const response = await authFetch(
        revisionFileUrl(workspaceId, id, path, true),
        { signal: controller.signal },
      )
      if (!response.ok) throw new HistoryError(response.status)
      const blob = await response.blob()
      if (controller.signal.aborted) return
      objectUrl = URL.createObjectURL(
        new Blob([blob], { type: mimeType || blob.type }),
      )
      setAsset({ url: objectUrl })
    })().catch(() => {
      if (!controller.signal.aborted) setAsset({ error: true })
    })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [workspaceId, id, hash, path, mimeType, retry])
  if (asset.error)
    return (
      <Container role="alert" className="space-y-3 p-5 text-sm">
        <span className="block">{text("fileLoadFailed")}</span>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setRetry((value) => value + 1)}
        >
          {text("retry")}
        </Button>
      </Container>
    )
  if (!asset.url)
    return (
      <Container role="status" className="p-5 text-sm text-muted-foreground">
        {text("rendering")}
      </Container>
    )
  // Reuse the native PDF viewer used by the existing research preview surfaces.
  return pdf ? (
    <iframe
      title={title}
      src={`${asset.url}#view=FitH&toolbar=1`}
      className="h-full min-h-80 w-full border-0 bg-white"
    />
  ) : (
    <Container
      className={
        inlineImage
          ? "inline-block max-w-full"
          : "flex min-h-40 items-center justify-center overflow-auto p-5"
      }
    >
      <img
        src={asset.url}
        alt={title}
        className="max-h-[60dvh] max-w-full object-contain"
      />
    </Container>
  )
}

/** Keep the image component identity stable across editor and layout updates. */
function RevisionMarkdown({
  workspaceId,
  path,
  side,
  content,
}: {
  workspaceId: string
  path: string
  side?: Side
  content: string
}) {
  const text = useHistoryText()
  const revision = side?.revision
  const file = side?.file
  const components = useMemo<Components>(
    () => ({
      img: ({ src, alt }) => {
        if (typeof src === "string" && /^https?:\/\//i.test(src))
          return (
            <img
              src={src}
              alt={alt ?? ""}
              loading="lazy"
              referrerPolicy="no-referrer"
            />
          )
        const imagePath =
          typeof src === "string" ? resolveHistoryImagePath(path, src) : null
        if (!revision || !file || !imagePath)
          return (
            <span
              className="text-xs text-muted-foreground"
              title={text("imageUnavailable")}
            >
              {alt || text("imageUnavailable")}
            </span>
          )
        return (
          <AuthenticatedPreview
            workspaceId={workspaceId}
            path={imagePath}
            side={{ revision, file }}
            title={alt || imagePath}
            pdf={false}
            inlineImage
          />
        )
      },
    }),
    [workspaceId, path, revision, file, text],
  )
  return (
    <Markdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={components}
    >
      {content}
    </Markdown>
  )
}

/** Read-only Monaco comparison or rendered previews for the selected history file. */
export default function PaperHistoryFileViewer(props: Props) {
  const { path, original, modified, binary, mimeType, view } = props
  const text = useHistoryText()
  const { resolvedTheme } = useTheme()
  const container = useRef<HTMLDivElement>(null)
  const modelId = useId()
  const [wide, setWide] = useState(false)
  useEffect(() => {
    if (!container.current) return
    const observer = new ResizeObserver(([entry]) =>
      setWide(entry.contentRect.width >= 800),
    )
    observer.observe(container.current)
    return () => observer.disconnect()
  }, [])
  const image = mimeType.startsWith("image/")
  const pdf = mimeType === "application/pdf" || /\.pdf$/i.test(path)
  const markdown =
    /\.(md|markdown)$/i.test(path) || mimeType === "text/markdown"
  const asset =
    (pdf || (image && (binary || view === "preview"))) &&
    Boolean(props.before || props.after)
  if (view === "diff" && !binary && !asset)
    return (
      <div
        ref={container}
        className="h-full min-h-0"
        data-testid="history-text-diff"
        data-inline={!wide}
        role="region"
        aria-label={path}
      >
        <DiffEditor
          height="100%"
          original={original ?? ""}
          modified={modified ?? ""}
          originalModelPath={`history://${encodeURIComponent(props.workspaceId)}/${encodeURIComponent(modelId)}/before/${encodeURIComponent(props.before?.revision.id ?? "candidate")}/${path}`}
          modifiedModelPath={`history://${encodeURIComponent(props.workspaceId)}/${encodeURIComponent(modelId)}/after/${encodeURIComponent(props.after?.revision.id ?? "candidate")}/${path}`}
          keepCurrentOriginalModel
          keepCurrentModifiedModel
          onMount={(editor) => {
            const models = editor.getModel()
            // Monaco must detach the diff widget before its text models are released.
            editor.onDidDispose(() =>
              queueMicrotask(() => {
                models?.original.dispose()
                models?.modified.dispose()
              }),
            )
          }}
          language={getLanguageFromPath(path)}
          theme={resolvedTheme === "light" ? "vs" : "vs-dark"}
          loading={
            <p role="status" className="p-5 text-sm">
              {text("rendering")}
            </p>
          }
          options={{
            readOnly: true,
            domReadOnly: true,
            originalEditable: false,
            renderSideBySide: wide,
            useInlineViewWhenSpaceIsLimited: true,
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: "on",
            wordWrap: "on",
            automaticLayout: true,
            scrollBeyondLastLine: false,
            renderOverviewRuler: false,
            padding: { top: 16 },
            hideUnchangedRegions: {
              enabled: true,
              contextLineCount: 4,
              minimumLineCount: 8,
            },
          }}
        />
      </div>
    )
  if (binary && !asset)
    return (
      <div ref={container} className="grid h-full place-items-center p-8">
        <div className="max-w-md space-y-3 text-center text-sm text-muted-foreground">
          <p>{text("binaryDownload")}</p>
          {props.beforeSize != null && props.afterSize != null && (
            <p className="font-mono tabular-nums">
              {props.beforeSize} → {props.afterSize} B
            </p>
          )}
        </div>
      </div>
    )
  return (
    <div
      ref={container}
      className={`grid h-full min-h-0 overflow-auto ${wide ? "grid-cols-2" : "grid-cols-1"}`}
      data-testid="history-file-preview"
    >
      {(["before", "after"] as const).map((name) => {
        const side = props[name]
        const content = name === "before" ? original : modified
        const exists = side ? side.file.exists : content !== null
        return (
          <section
            key={name}
            className="flex min-h-0 min-w-0 flex-col border-b last:border-b-0 md:border-r last:border-r-0"
            aria-label={text(name)}
          >
            <h3 className="sticky top-0 z-10 border-b bg-muted/80 px-5 py-2 text-xs font-medium backdrop-blur">
              {text(name)}
              {side && (
                <span className="ml-2 text-muted-foreground">
                  {side.revision.label}
                </span>
              )}
            </h3>
            {!exists ? (
              <p className="p-5 text-sm text-muted-foreground">
                {text("missingFile")}
              </p>
            ) : asset && side ? (
              <AuthenticatedPreview
                key={`${side.revision.id}:${path}`}
                workspaceId={props.workspaceId}
                path={path}
                side={side}
                title={`${text(name)} — ${path}`}
                pdf={pdf}
              />
            ) : markdown ? (
              <article className="prose prose-sm dark:prose-invert max-w-none break-words p-5 [&_.katex-display]:overflow-x-auto [&_img]:max-w-full">
                <RevisionMarkdown
                  workspaceId={props.workspaceId}
                  path={path}
                  side={side}
                  content={content ?? ""}
                />
              </article>
            ) : (
              <pre className="overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-sm leading-6">
                {content ?? ""}
              </pre>
            )}
          </section>
        )
      })}
    </div>
  )
}
