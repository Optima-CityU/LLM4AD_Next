import { useQuery } from "@tanstack/react-query"
import { Download, FileText, ImageOff, Maximize2 } from "lucide-react"
import { lazy, Suspense, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import { type EvaluationFile, Llm4AdEvaluationsService } from "@/client"
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
import { Skeleton } from "@/components/ui/skeleton"
import {
  evaluationPreviewKind,
  formatEvaluationBytes,
} from "@/lib/evaluationArtifacts"

const ArtifactCodeEditor = lazy(
  () => import("@/components/AutoResearch/ArtifactCodeEditor"),
)

interface ArtifactProps {
  taskId: string
  evaluationId: string
  file: EvaluationFile
  saved: boolean
}

function useArtifactContent(props: ArtifactProps, visible = true) {
  const { taskId, evaluationId, file, saved } = props
  const kind = evaluationPreviewKind(file)
  const query = useQuery({
    queryKey: ["nodeArtifact", taskId, evaluationId, file.id],
    queryFn: async () => {
      const data = await Llm4AdEvaluationsService.evaluationArtifact({
        taskId,
        evaluationId,
        artifactId: file.id,
        // Raster thumbnails and enlarged views share the authenticated original.
        download: kind === "image",
      })
      const blob = data instanceof Blob ? data : new Blob([data as BlobPart])
      return kind === "text"
        ? await blob.text()
        : blob.slice(0, blob.size, file.media_type)
    },
    enabled: saved && visible && kind !== null,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  })
  const [imageUrl, setImageUrl] = useState<string>()
  useEffect(() => {
    if (!(query.data instanceof Blob)) return
    const url = URL.createObjectURL(query.data)
    setImageUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [query.data])
  return { ...query, imageUrl }
}

function DownloadArtifact(props: ArtifactProps) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const data = await Llm4AdEvaluationsService.evaluationArtifact({
        taskId: props.taskId,
        evaluationId: props.evaluationId,
        artifactId: props.file.id,
        download: true,
      })
      const blob = data instanceof Blob ? data : new Blob([data as BlobPart])
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = props.file.name.split("/").pop() ?? props.file.name
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      toast.error(t("evaluationData.downloadFailed"))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button
      size="icon"
      variant="ghost"
      className="size-8 shrink-0"
      disabled={!props.saved || busy}
      title={t("evaluationData.download")}
      aria-label={t("evaluationData.downloadFile", { name: props.file.name })}
      onClick={() => void download()}
    >
      <Download className={busy ? "animate-pulse" : ""} />
    </Button>
  )
}

function ImageThumbnail(props: ArtifactProps & { onOpen: () => void }) {
  const { t } = useTranslation()
  const ref = useRef<HTMLButtonElement>(null)
  const [visible, setVisible] = useState(false)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!ref.current) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: "160px" },
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  const query = useArtifactContent(props, visible)
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Button
        ref={ref}
        variant="outline"
        className="group relative aspect-[4/3] h-auto w-full overflow-hidden bg-muted/30 p-0"
        onClick={props.onOpen}
        disabled={!props.saved}
        title={props.file.description || props.file.name}
        aria-label={t("evaluationData.viewFile", { name: props.file.name })}
      >
        {!props.saved || query.isError || failed ? (
          <ImageOff className="text-muted-foreground" />
        ) : query.imageUrl ? (
          <img
            src={query.imageUrl}
            alt={props.file.description || props.file.name}
            className="size-full object-contain"
            onError={() => setFailed(true)}
          />
        ) : (
          <Skeleton className="size-full rounded-none" />
        )}
        <Maximize2 className="absolute bottom-1.5 right-1.5 rounded bg-background/80 p-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
      </Button>
      <div className="flex min-w-0 items-center gap-1">
        <span
          className="min-w-0 flex-1 truncate text-xs"
          title={props.file.name}
        >
          {props.file.name.split("/").pop()}
        </span>
        <DownloadArtifact {...props} />
      </div>
    </div>
  )
}

function ArtifactPreview(props: ArtifactProps) {
  const { t } = useTranslation()
  const query = useArtifactContent(props)
  const [imageFailed, setImageFailed] = useState(false)
  const kind = evaluationPreviewKind(props.file)
  if (!props.saved)
    return (
      <p className="text-sm text-muted-foreground">
        {t("evaluationData.pendingFile")}
      </p>
    )
  if (!kind)
    return (
      <p className="text-sm text-muted-foreground">
        {t("evaluationData.downloadOnly")}
      </p>
    )
  if (query.isError || imageFailed)
    return (
      <p role="alert" className="text-sm text-muted-foreground">
        {t("evaluationData.previewUnavailable")}
      </p>
    )
  if (query.isPending) return <Skeleton className="h-48 w-full" />
  if (kind === "image")
    return (
      <div className="flex min-h-0 justify-center overflow-auto">
        {query.imageUrl && (
          <img
            src={query.imageUrl}
            alt={props.file.description || props.file.name}
            className="max-h-[65dvh] max-w-full object-contain"
            onError={() => setImageFailed(true)}
          />
        )}
      </div>
    )
  const text = typeof query.data === "string" ? query.data : ""
  return /\.(md|markdown)$/i.test(props.file.name) ? (
    <div className="min-h-0 overflow-auto break-words text-sm leading-relaxed">
      <Markdown
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        components={makeMarkdownComponents(
          `node-${props.evaluationId}-${props.file.id}`,
        )}
      >
        {text}
      </Markdown>
    </div>
  ) : (
    <div className="h-[55dvh] min-h-0 min-w-0">
      <Suspense fallback={<Skeleton className="size-full" />}>
        <ArtifactCodeEditor filePath={props.file.name} value={text} readOnly />
      </Suspense>
    </div>
  )
}

export default function NodeArtifacts({
  taskId,
  evaluationId,
  files,
  availableIds,
}: {
  taskId: string
  evaluationId: string
  files: EvaluationFile[]
  availableIds: string[]
}) {
  const { t } = useTranslation()
  const [openedId, setOpenedId] = useState<string | null>(null)
  const opened = files.find((file) => file.id === openedId)
  const images = files.filter((file) => evaluationPreviewKind(file) === "image")
  const others = files.filter((file) => evaluationPreviewKind(file) !== "image")
  const propsFor = (file: EvaluationFile): ArtifactProps => ({
    taskId,
    evaluationId,
    file,
    saved: availableIds.includes(file.id),
  })
  if (files.length === 0) return null
  return (
    <section
      className="flex min-w-0 flex-col gap-2"
      aria-label={t("evaluationData.attachments")}
    >
      <h4 className="text-sm font-medium">
        {t("evaluationData.files", { count: files.length })}
      </h4>
      {images.length > 0 && (
        <div className="grid min-w-0 grid-cols-2 gap-2">
          {images.map((file) => (
            <ImageThumbnail
              key={file.id}
              {...propsFor(file)}
              onOpen={() => setOpenedId(file.id)}
            />
          ))}
        </div>
      )}
      {others.length > 0 && (
        <ul className="min-w-0 divide-y rounded-md border">
          {others.map((file) => (
            <li
              key={file.id}
              className="flex min-w-0 items-center gap-1 px-2 py-1"
            >
              <Button
                variant="ghost"
                className="h-auto min-w-0 flex-1 justify-start gap-2 px-1 py-1.5 text-left"
                onClick={() => setOpenedId(file.id)}
                title={file.description || file.name}
                aria-label={t("evaluationData.viewFile", { name: file.name })}
              >
                <FileText className="shrink-0 text-muted-foreground" />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm">{file.name}</span>
                  <span className="text-xs font-normal text-muted-foreground">
                    {formatEvaluationBytes(file.size)}
                    {!availableIds.includes(file.id) &&
                      ` · ${t("evaluationData.pending")}`}
                  </span>
                </span>
              </Button>
              <DownloadArtifact {...propsFor(file)} />
            </li>
          ))}
        </ul>
      )}
      <Dialog
        open={!!opened}
        onOpenChange={(open) => !open && setOpenedId(null)}
      >
        <DialogContent className="flex max-h-[90dvh] min-w-0 flex-col overflow-hidden sm:max-w-5xl">
          {opened && (
            <>
              <DialogHeader className="min-w-0 pr-8">
                <div className="flex min-w-0 items-center gap-2">
                  <DialogTitle className="min-w-0 flex-1 break-all text-base leading-relaxed">
                    {opened.name}
                  </DialogTitle>
                  <DownloadArtifact {...propsFor(opened)} />
                </div>
                <DialogDescription className="break-words">
                  {opened.description || formatEvaluationBytes(opened.size)}
                </DialogDescription>
              </DialogHeader>
              <ArtifactPreview key={opened.id} {...propsFor(opened)} />
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}
