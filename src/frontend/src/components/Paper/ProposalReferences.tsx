import { useQuery } from "@tanstack/react-query"
import { Download } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"
import SourceNavigator, {
  buildPaperFileTree,
  EDITABLE_SUFFIXES,
} from "@/components/Common/SourceNavigator"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
} from "@/components/markdown/markdownComponents"
import { Button } from "@/components/ui/button"
import { authFetch } from "@/utils/auth"
import { downloadHistoryFile, revisionFileUrl } from "./history/historyApi"
import { useProposalMarkdownComponents } from "./ProposalDocumentPreview"

export function proposalReferenceKind(path: string) {
  const filename = path.toLowerCase()
  if (/\.(md|markdown)$/.test(filename)) return "markdown"
  if (filename.endsWith(".pdf")) return "pdf"
  if (/\.(png|jpe?g|svg)$/.test(filename)) return "image"
  if (EDITABLE_SUFFIXES.some((suffix) => filename.endsWith(suffix)))
    return "text"
  return "download"
}

type Props = {
  workspaceId: string
  sourceVersionId: string
  sourceHash: string
  manifest: string[]
  selectedPath: string | null
  content: string
  loading: boolean
  failed: boolean
  onSelect: (path: string) => void
  onDelete: (path: string) => void
  canDeletePath: (path: string, isDirectory: boolean) => boolean
}

export default function ProposalReferences(props: Props) {
  const { t } = useTranslation()
  const { manifest, selectedPath, onSelect, onDelete, canDeletePath } = props
  const tree = useMemo(
    () => buildPaperFileTree(manifest).flatMap((root) => root.children ?? []),
    [manifest],
  )
  if (!manifest.length)
    return (
      <p className="max-w-md px-5 py-6 text-sm leading-6 text-muted-foreground">
        {t("paper.proposalStart.referenceHint")}
      </p>
    )
  return (
    <div className="grid h-full min-h-0 min-w-0 grid-rows-[minmax(4rem,9rem)_minmax(0,1fr)] overflow-hidden @2xl:grid-cols-[minmax(8rem,26%)_minmax(0,1fr)] @2xl:grid-rows-[minmax(0,1fr)]">
      <div className="min-h-0 min-w-0 overflow-hidden border-b @2xl:border-r @2xl:border-b-0">
        <SourceNavigator
          title={t("paper.proposalDocument.references")}
          manifest={manifest}
          tree={tree}
          selectedPath={selectedPath}
          onSelect={onSelect}
          onDelete={onDelete}
          canDeletePath={canDeletePath}
          selectableSuffixes={null}
          collapsible={false}
          collapsed={false}
          onToggle={() => {}}
          uploading={false}
          locked={false}
        />
      </div>
      {selectedPath ? (
        <ReferencePreview
          key={JSON.stringify([
            props.workspaceId,
            props.sourceVersionId,
            props.sourceHash,
            selectedPath,
          ])}
          {...props}
          path={selectedPath}
        />
      ) : (
        <p className="p-5 text-sm text-muted-foreground">
          {t("paper.editor.selectFile")}
        </p>
      )}
    </div>
  )
}

function ReferencePreview({
  workspaceId,
  sourceVersionId,
  sourceHash,
  path,
  content,
  loading,
  failed,
}: Pick<
  Props,
  | "workspaceId"
  | "sourceVersionId"
  | "sourceHash"
  | "content"
  | "loading"
  | "failed"
> & { path: string }) {
  const { t } = useTranslation()
  const stem = path.split("/").pop() ?? path
  const kind = proposalReferenceKind(path)
  const revision = `source:${sourceVersionId}`
  const components = useProposalMarkdownComponents(
    workspaceId,
    sourceVersionId,
    sourceHash,
    path,
  )

  return (
    <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
      <div className="flex min-w-0 items-center gap-2 border-b p-2 pl-3">
        <h2
          title={path}
          className="min-w-0 flex-1 truncate text-sm font-medium"
        >
          {stem}
        </h2>
        <Button
          size="icon"
          variant="ghost"
          title={t("paper.proposalDocument.downloadReference")}
          aria-label={t("paper.proposalDocument.downloadReference")}
          onClick={() => {
            void downloadHistoryFile(
              workspaceId,
              revision,
              `source/${path}`,
            ).catch(() =>
              toast.error(t("paper.proposalDocument.attachmentUnavailable")),
            )
          }}
        >
          <Download className="size-4" aria-hidden="true" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {kind === "pdf" || kind === "image" ? (
          <ReferenceAsset
            url={revisionFileUrl(workspaceId, revision, `source/${path}`, true)}
            title={stem}
            kind={kind}
          />
        ) : kind === "download" || failed ? (
          <p className="p-4 text-sm text-muted-foreground">
            {t("paper.proposalDocument.referenceDownloadHint")}
          </p>
        ) : loading ? (
          <p role="status" className="p-4 text-sm text-muted-foreground">
            {t("common.loading")}
          </p>
        ) : kind === "markdown" ? (
          <article className="prose prose-sm dark:prose-invert max-w-none break-words p-4 leading-7 [&_.katex-display]:overflow-x-auto">
            <Markdown
              remarkPlugins={MARKDOWN_REMARK_PLUGINS}
              rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
              components={components}
            >
              {content}
            </Markdown>
          </article>
        ) : (
          <pre className="whitespace-pre-wrap break-words p-4 text-sm leading-7">
            {content}
          </pre>
        )}
      </div>
    </section>
  )
}

function ReferenceAsset({
  url,
  title,
  kind,
}: {
  url: string
  title: string
  kind: "pdf" | "image"
}) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState<{ blob: Blob; url: string } | null>(
    null,
  )
  const asset = useQuery({
    queryKey: ["paper-proposal-reference", url],
    queryFn: async ({ signal }) => {
      const response = await authFetch(url, { signal })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const blob = await response.blob()
      return kind === "pdf"
        ? new Blob([blob], { type: "application/pdf" })
        : blob
    },
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  })
  useEffect(() => {
    if (!asset.data) return
    const objectUrl = URL.createObjectURL(asset.data)
    setPreview({ blob: asset.data, url: objectUrl })
    return () => URL.revokeObjectURL(objectUrl)
  }, [asset.data])
  if (asset.isError)
    return (
      <p role="alert" className="p-4 text-sm text-muted-foreground">
        {t("paper.proposalDocument.referenceDownloadHint")}
      </p>
    )
  if (!preview || preview.blob !== asset.data)
    return (
      <p role="status" className="p-4 text-sm text-muted-foreground">
        {t("common.loading")}
      </p>
    )
  return kind === "pdf" ? (
    <iframe
      src={preview.url}
      title={title}
      className="block h-full min-h-48 w-full border-0 bg-white"
    />
  ) : (
    <img
      src={preview.url}
      alt={title}
      className="mx-auto max-h-full max-w-full object-contain p-3"
    />
  )
}
