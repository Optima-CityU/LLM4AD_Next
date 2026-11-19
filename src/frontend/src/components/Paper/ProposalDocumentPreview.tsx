import { useQuery } from "@tanstack/react-query"
import { type ReactNode, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown, { type Components } from "react-markdown"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
} from "@/components/markdown/markdownComponents"
import PaperRegionWorkbench from "@/components/Paper/PaperRegionWorkbench"
import { ProposalChapterPicker } from "@/components/Paper/ProposalResultsDock"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { useProposalRender } from "@/hooks/useProposalRender"
import type { ProposalChapter } from "@/lib/proposalDocument"
import { authFetch } from "@/utils/auth"

/** Prefer the canonical document while retaining existing Typst entry points. */
export function proposalDocumentEntryPath(
  manifest: string[],
  configuredEntry?: string | null,
) {
  return (
    (configuredEntry && manifest.includes(configuredEntry)
      ? configuredEntry
      : null) ??
    manifest.find((path) => path === "proposal.md") ??
    manifest.find(
      (path) => !path.includes("/") && /\.(md|markdown)$/i.test(path),
    ) ??
    manifest.find((path) => /\.typ$/i.test(path)) ??
    manifest.find((path) => /\.(md|markdown)$/i.test(path)) ??
    "proposal.md"
  )
}

/** Resolve authenticated render assets only inside the selected source revision. */
function renderAssetUrl(path: string, sourceVersionId: string) {
  const api = new URL(import.meta.env.VITE_API_URL || window.location.origin)
  const url = new URL(path, api)
  if (
    url.origin !== api.origin ||
    !url.pathname.startsWith(
      `/api/v1/llm4ad/papers/source-versions/${encodeURIComponent(sourceVersionId)}/`,
    )
  )
    throw new Error("Invalid render asset")
  return url.toString()
}

type Props = {
  workspaceId: string
  sourceVersionId: string
  sourceHash: string
  entryPath: string
  content: string
  loading?: boolean
  chapters?: ProposalChapter[]
  onSelectChapter?: (path: string) => Promise<boolean>
  onEditChapter?: (path: string) => void
  saving?: boolean
  dirty?: boolean
  saveError?: boolean
}

/** Resolve project resources relative to the entry without escaping source/. */
export function proposalAssetPath(entryPath: string, raw: string) {
  if (!raw || /^(?:[a-z][a-z\d+.-]*:|\/\/|#|\?)/i.test(raw)) return null
  let relative: string
  try {
    relative = decodeURIComponent(raw.split(/[?#]/)[0]).replace(/\\/g, "/")
  } catch {
    return null
  }
  if (
    !relative ||
    [...relative].some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    return null
  const parts = relative.startsWith("/")
    ? []
    : entryPath.split("/").slice(0, -1)
  for (const part of relative.split("/")) {
    if (!part || part === ".") continue
    if (part === "..") {
      if (!parts.length) return null
      parts.pop()
    } else parts.push(part)
  }
  return parts.length ? `source/${parts.join("/")}` : null
}

function sourceAssetUrl(
  workspaceId: string,
  sourceVersionId: string,
  path: string,
) {
  return `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/papers/workspaces/${encodeURIComponent(workspaceId)}/history/${encodeURIComponent(`source:${sourceVersionId}`)}/download?${new URLSearchParams({ path })}`
}

function ProposalImage({
  workspaceId,
  sourceVersionId,
  sourceHash,
  entryPath,
  src,
  alt,
  title,
}: Omit<Props, "content"> & {
  src: string
  alt?: string
  title?: string
}) {
  const { t } = useTranslation()
  const path = proposalAssetPath(entryPath, src)
  const revision = JSON.stringify([
    workspaceId,
    sourceVersionId,
    sourceHash,
    path,
  ])
  const [object, setObject] = useState<{
    revision: string
    url: string
  } | null>(null)
  const asset = useQuery({
    queryKey: [
      "paper-proposal-asset",
      workspaceId,
      sourceVersionId,
      sourceHash,
      path,
    ],
    queryFn: async ({ signal }) => {
      const response = await authFetch(
        sourceAssetUrl(workspaceId, sourceVersionId, path!),
        { signal },
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.blob()
    },
    enabled: Boolean(path),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    if (!asset.data) return
    const url = URL.createObjectURL(asset.data)
    setObject({ revision, url })
    return () => URL.revokeObjectURL(url)
  }, [asset.data, revision])
  if (/^(?:https?:)?\/\//i.test(src))
    return <img src={src} alt={alt ?? ""} title={title} />
  if (!path || asset.isError)
    return (
      <span
        role="img"
        aria-label={
          alt ||
          t("paper.proposalDocument.imageUnavailable", {
            defaultValue: "Image unavailable",
          })
        }
        className="inline-block rounded border px-3 py-2 text-sm text-muted-foreground"
      >
        {alt ? `${alt} · ` : ""}
        {t("paper.proposalDocument.imageUnavailable", {
          defaultValue: "Image unavailable",
        })}
      </span>
    )
  return object?.revision === revision ? (
    <img src={object.url} alt={alt ?? ""} title={title} />
  ) : (
    <span role="status" className="text-sm text-muted-foreground">
      {alt || t("common.loading")}
    </span>
  )
}

function ProposalAttachment({
  workspaceId,
  sourceVersionId,
  path,
  title,
  children,
}: {
  workspaceId: string
  sourceVersionId: string
  path: string
  title?: string
  children: ReactNode
}) {
  const { t } = useTranslation()
  const [failed, setFailed] = useState(false)
  const [pending, setPending] = useState(false)
  const url = sourceAssetUrl(workspaceId, sourceVersionId, path)
  return (
    <span>
      <a
        href={url}
        title={title}
        aria-busy={pending}
        onClick={async (event) => {
          event.preventDefault()
          if (pending) return
          setFailed(false)
          setPending(true)
          try {
            const response = await authFetch(url)
            if (!response.ok) throw new Error(`HTTP ${response.status}`)
            const objectUrl = URL.createObjectURL(await response.blob())
            const anchor = document.createElement("a")
            anchor.href = objectUrl
            anchor.download = path.split("/").pop() || "attachment"
            anchor.click()
            setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
          } catch {
            setFailed(true)
          } finally {
            setPending(false)
          }
        }}
      >
        {children}
      </a>
      {failed && (
        <span role="alert" className="ml-2 text-sm text-muted-foreground">
          {t("paper.proposalDocument.attachmentUnavailable", {
            defaultValue: "Attachment unavailable",
          })}
        </span>
      )}
    </span>
  )
}

/** Fetch local figure and attachment bytes through the owned history file route. */
export function useProposalMarkdownComponents(
  workspaceId: string,
  sourceVersionId: string,
  sourceHash: string,
  entryPath: string,
) {
  return useMemo<Components>(
    () => ({
      img: ({ src, alt, title }) => (
        <ProposalImage
          workspaceId={workspaceId}
          sourceVersionId={sourceVersionId}
          sourceHash={sourceHash}
          entryPath={entryPath}
          src={typeof src === "string" ? src : ""}
          alt={alt}
          title={title}
        />
      ),
      a: ({ href, children, title }) => {
        const path = proposalAssetPath(entryPath, href ?? "")
        if (!path)
          return /^(?:[a-z][a-z\d+.-]*:|\/\/|#|\?)/i.test(href ?? "") ? (
            <a href={href} title={title}>
              {children}
            </a>
          ) : (
            <span>{children}</span>
          )
        return (
          <ProposalAttachment
            workspaceId={workspaceId}
            sourceVersionId={sourceVersionId}
            path={path}
            title={title}
          >
            {children}
          </ProposalAttachment>
        )
      },
    }),
    [workspaceId, sourceVersionId, sourceHash, entryPath],
  )
}

/** Read one chapter and open its source in the shared file editor. */
export default function ProposalDocumentPreview({
  workspaceId,
  sourceVersionId,
  sourceHash,
  entryPath,
  content,
  loading = false,
  chapters = [],
  onSelectChapter,
  onEditChapter,
  saving,
  dirty,
  saveError,
}: Props) {
  const { t } = useTranslation()
  const components = useProposalMarkdownComponents(
    workspaceId,
    sourceVersionId,
    sourceHash,
    entryPath,
  )
  const chapter = chapters.find((item) => item.path === entryPath)
  return (
    <section className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2">
        {onSelectChapter && chapters.length > 0 && (
          <ProposalChapterPicker
            chapters={chapters}
            chapterPath={entryPath}
            onSelect={onSelectChapter}
          />
        )}
        {chapter?.status && (
          <span className="text-xs text-muted-foreground">
            {t(`paper.proposalDocument.chapterStatus.${chapter.status}`, {
              defaultValue: chapter.status,
            })}
          </span>
        )}
        {onEditChapter && (
          <Button
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => onEditChapter(entryPath)}
          >
            {t("paper.proposalDocument.editChapter")}
          </Button>
        )}
        <span
          role="status"
          className={
            saveError
              ? "text-xs text-destructive"
              : "text-xs text-muted-foreground"
          }
        >
          {t(
            saveError
              ? "paper.editor.saveFailed"
              : saving
                ? "paper.editor.autoSaving"
                : dirty
                  ? "paper.editor.savePending"
                  : "paper.editor.savedState",
          )}
        </span>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {loading ? (
          <p role="status" className="p-6 text-sm text-muted-foreground">
            {t("common.loading")}
          </p>
        ) : (
          <div className="mx-auto max-w-3xl px-6 py-8 sm:px-10 sm:py-10">
            <article
              data-testid="proposal-document"
              aria-label={t("paper.proposalDocument.savedDocument")}
              className="prose prose-neutral max-w-none break-words text-[15px] leading-8 dark:prose-invert prose-headings:font-serif prose-headings:text-foreground prose-p:leading-8 [&_pre]:overflow-x-auto [&_table]:block [&_table]:overflow-x-auto [&_.katex-display]:max-w-full [&_.katex-display]:overflow-x-auto"
            >
              <ReactMarkdown
                components={components}
                remarkPlugins={MARKDOWN_REMARK_PLUGINS}
                rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
              >
                {content}
              </ReactMarkdown>
            </article>
            <details className="mt-10 border-t pt-4">
              <summary className="cursor-pointer text-sm font-medium text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
                {t("paper.proposalDocument.regionalEditing")}
              </summary>
              <div className="pt-5">
                <PaperRegionWorkbench
                  view="proposal"
                  paths={[entryPath]}
                  sourceOnly
                />
              </div>
            </details>
          </div>
        )}
      </ScrollArea>
    </section>
  )
}

/** Preview immutable exports without compiling when this view mounts. */
export function ProposalLayoutPreview({
  render,
  dirty,
}: {
  render: ReturnType<typeof useProposalRender>
  dirty?: boolean
}) {
  const { t } = useTranslation()
  const result = render.result
  const revision = JSON.stringify([
    result?.source_version_id,
    result?.source_hash,
    result?.entry_paths ?? result?.entry_path,
  ])
  const [object, setObject] = useState<{
    revision: string
    url: string
  } | null>(null)
  const pdf = useQuery({
    queryKey: ["paper-proposal-pdf", revision, result?.pdf_url],
    queryFn: async ({ signal }) => {
      const response = await authFetch(
        renderAssetUrl(result!.pdf_url!, result!.source_version_id),
        { signal },
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.blob()
    },
    enabled: Boolean(result?.pdf_url),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const typst = useQuery({
    queryKey: ["paper-proposal-typst", revision, result?.typst_url],
    queryFn: async ({ signal }) => {
      const response = await authFetch(
        renderAssetUrl(result!.typst_url!, result!.source_version_id),
        { signal },
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.text()
    },
    enabled: Boolean(result?.typst_url),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    if (!pdf.data) return
    const url = URL.createObjectURL(pdf.data)
    setObject({ revision, url })
    return () => URL.revokeObjectURL(url)
  }, [pdf.data, revision])
  const pdfUrl = object?.revision === revision ? object.url : null
  return (
    <section className="flex h-full min-h-0 flex-col">
      {(render.stale || dirty) && result && (
        <p role="status" className="border-b bg-amber-500/10 px-4 py-3 text-sm">
          {t("paper.proposalDocument.oldPreview")}
        </p>
      )}
      {(render.failed || pdf.isError) && (
        <div role="alert" className="space-y-2 border-b px-4 py-3 text-sm">
          <p>{t("paper.proposalDocument.renderFailed")}</p>
          <Button
            size="sm"
            variant="outline"
            disabled={render.pending}
            onClick={() => {
              if (pdf.isError) void pdf.refetch()
              else void render.confirm()
            }}
          >
            {t("paper.proposalDocument.retryPdf")}
          </Button>
        </div>
      )}
      {render.pending && (
        <p
          role="status"
          className="border-b px-4 py-3 text-sm text-muted-foreground"
        >
          {t("paper.proposalDocument.preparingPdf")}
        </p>
      )}
      {(result || pdfUrl) && (
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          {result && (
            <span
              className="mr-auto min-w-0 truncate font-mono text-xs text-muted-foreground"
              title={`${result.source_version_id} · ${result.source_hash}`}
            >
              {result.source_hash.slice(0, 12)}
            </span>
          )}
          {pdfUrl && (
            <Button size="sm" variant="outline" asChild>
              <a href={pdfUrl} download="research-proposal.pdf">
                {t("paper.proposalDocument.downloadPdf")}
              </a>
            </Button>
          )}
          {typst.data !== undefined && (
            <Button size="sm" variant="outline" asChild>
              <a
                href={`data:text/plain;charset=utf-8,${encodeURIComponent(typst.data)}`}
                download="proposal.typ"
              >
                {t("paper.proposalDocument.downloadTypst")}
              </a>
            </Button>
          )}
        </div>
      )}
      {pdfUrl ? (
        <iframe
          src={pdfUrl}
          title={t("paper.proposalDocument.pdfTitle")}
          className="min-h-0 w-full flex-1 border-0"
        />
      ) : (
        !render.pending &&
        !render.failed && (
          <p role="status" className="p-6 text-sm text-muted-foreground">
            {t(
              pdf.isLoading
                ? "common.loading"
                : "paper.proposalDocument.noPreview",
            )}
          </p>
        )
      )}
      {Boolean(render.diagnostics.length) && (
        <ul
          aria-live="polite"
          className="max-h-40 shrink-0 overflow-y-auto border-t px-5 py-3 text-sm"
        >
          {render.diagnostics.map((diagnostic, index) => (
            <li
              key={`${diagnostic.path}:${diagnostic.line}:${index}`}
              className="break-words py-1"
            >
              {diagnostic.path && (
                <span className="mr-2 font-mono text-xs">
                  {diagnostic.path}
                  {diagnostic.line ? `:${diagnostic.line}` : ""}
                </span>
              )}
              {diagnostic.message}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
