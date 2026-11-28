import { FileText, ImageOff, Maximize2 } from "lucide-react"
import { lazy, Suspense, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"

import type { EvaluationFile } from "@/client"
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
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  evaluationPreviewKind,
  formatEvaluationBytes,
  visibleEvaluationFiles,
} from "@/lib/evaluationArtifacts"
import { resolveEvidenceComparisons } from "@/lib/evidenceComparisons"

import {
  type ArtifactProps,
  DownloadArtifact,
  useArtifactContent,
} from "./artifactContent"
import ImageLightbox from "./ImageLightbox"

const ArtifactCodeEditor = lazy(
  () => import("@/components/AutoResearch/ArtifactCodeEditor"),
)

function ImageThumbnail(
  props: ArtifactProps & { onOpen: () => void; eager?: boolean },
) {
  const { t } = useTranslation()
  const ref = useRef<HTMLButtonElement>(null)
  const [visible, setVisible] = useState(props.eager ?? false)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (props.eager || !ref.current) return
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
  }, [props.eager])
  const query = useArtifactContent(props, visible, "thumb")
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
          className="min-w-0 flex-1 truncate text-sm"
          title={props.file.name}
        >
          {props.file.name.split("/").pop()}
        </span>
        <DownloadArtifact {...props} />
      </div>
      {!props.saved && (
        <p className="text-sm text-muted-foreground">
          {t("evaluationData.pendingFile")}
        </p>
      )}
      {props.saved && (query.isError || failed) && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("evaluationData.previewUnavailable")}
        </p>
      )}
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
  comparisons,
}: {
  taskId: string
  evaluationId: string
  files: EvaluationFile[]
  availableIds: string[]
  comparisons?: unknown
}) {
  const { t } = useTranslation()
  const [openedId, setOpenedId] = useState<string | null>(null)
  const [comparisonId, setComparisonId] = useState<string>()
  // Keep explicitly grouped evidence intact, even when it has a behavior alias.
  const uniqueFiles = comparisons
    ? [...new Map(files.map((file) => [file.id, file])).values()]
    : visibleEvaluationFiles(files)
  const opened = files.find((file) => file.id === openedId)
  const openedKind = opened ? evaluationPreviewKind(opened) : null
  const groups = resolveEvidenceComparisons(comparisons, uniqueFiles)
  const selectedIndex = Math.max(
    0,
    groups.findIndex((group) => group.id === comparisonId),
  )
  const selected = groups[selectedIndex]
  const comparedIds = new Set(
    groups.flatMap((group) => group.images.map((image) => image.file.id)),
  )
  const images = uniqueFiles.filter(
    (file) =>
      evaluationPreviewKind(file) === "image" && !comparedIds.has(file.id),
  )
  const others = uniqueFiles.filter(
    (file) => evaluationPreviewKind(file) !== "image",
  )
  const propsFor = (file: EvaluationFile): ArtifactProps => ({
    taskId,
    evaluationId,
    file,
    saved: availableIds.includes(file.id),
  })
  if (files.length === 0) return null
  return (
    <section
      className="@container flex min-w-0 flex-col gap-3"
      aria-label={t("evaluationData.attachments")}
    >
      {selected && (
        <section
          className="flex min-w-0 flex-col gap-3"
          aria-label={t("evaluationData.comparisons.title")}
        >
          <h4 className="text-sm font-semibold">
            {t("evaluationData.comparisons.title")}
          </h4>
          {groups.length > 1 ? (
            <div className="flex min-w-0 items-center gap-2">
              <Select value={selected.id} onValueChange={setComparisonId}>
                <SelectTrigger
                  className="min-w-0 flex-1"
                  aria-label={t("evaluationData.comparisons.selectGroup")}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {groups.map((group) => (
                      <SelectItem key={group.id} value={group.id}>
                        {group.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={selectedIndex === 0}
                onClick={() => setComparisonId(groups[selectedIndex - 1].id)}
                aria-label={t("evaluationData.comparisons.previous")}
              >
                {t("evaluationData.previous")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={selectedIndex === groups.length - 1}
                onClick={() => setComparisonId(groups[selectedIndex + 1].id)}
                aria-label={t("evaluationData.comparisons.next")}
              >
                {t("evaluationData.next")}
              </Button>
            </div>
          ) : (
            <p className="break-words text-sm font-medium">{selected.label}</p>
          )}
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t(`evaluationData.comparisons.${selected.kind}Hint`)}
          </p>
          <div
            className={
              selected.kind === "static"
                ? "grid min-w-0 grid-cols-1 gap-3 @sm:grid-cols-3"
                : "grid min-w-0 grid-cols-1 gap-3 @xs:grid-cols-2"
            }
          >
            {selected.images.map(({ role, file }) => (
              <div
                key={`${selected.id}:${role}:${file.id}`}
                className="flex min-w-0 flex-col gap-2"
              >
                <h5 className="text-sm font-medium">
                  {t(
                    `evaluationData.comparisons.${selected.kind}Roles.${role}`,
                  )}
                </h5>
                <ImageThumbnail
                  {...propsFor(file)}
                  eager
                  onOpen={() => setOpenedId(file.id)}
                />
              </div>
            ))}
          </div>
        </section>
      )}
      {images.length > 0 && (
        <section
          className="flex min-w-0 flex-col gap-2"
          aria-label={t("evaluationData.comparisons.gallery")}
        >
          <h4 className="text-sm font-medium">
            {t("evaluationData.comparisons.gallery")}
          </h4>
          <div className="grid min-w-0 grid-cols-2 gap-2">
            {images.map((file) => (
              <ImageThumbnail
                key={file.id}
                {...propsFor(file)}
                onOpen={() => setOpenedId(file.id)}
              />
            ))}
          </div>
        </section>
      )}
      {others.length > 0 && (
        <details
          className="min-w-0"
          open={selected || images.length > 0 ? undefined : true}
        >
          <summary className="cursor-pointer text-sm font-medium">
            {t("evaluationData.files", { count: others.length })}
          </summary>
          <ul className="mt-2 min-w-0 divide-y rounded-md border">
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
                    <span className="text-sm font-normal text-muted-foreground">
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
        </details>
      )}
      {/* Full-screen lightbox for images */}
      {opened && openedKind === "image" && (
        <ImageLightbox
          files={uniqueFiles.filter(
            (f) => evaluationPreviewKind(f) === "image",
          )}
          openedId={opened.id}
          taskId={taskId}
          evaluationId={evaluationId}
          availableIds={availableIds}
          onClose={() => setOpenedId(null)}
        />
      )}

      {/* Regular dialog for text / non-previewable files */}
      <Dialog
        open={!!opened && openedKind !== "image"}
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
