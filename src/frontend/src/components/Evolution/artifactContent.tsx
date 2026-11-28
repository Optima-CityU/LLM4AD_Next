import { useQuery } from "@tanstack/react-query"
import { Download } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { type EvaluationFile, Llm4AdEvaluationsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  evaluationDownloadName,
  evaluationPreviewKind,
} from "@/lib/evaluationArtifacts"
import { cn } from "@/lib/utils"

export interface ArtifactProps {
  taskId: string
  evaluationId: string
  file: EvaluationFile
  saved: boolean
}

export function useArtifactContent(
  props: ArtifactProps,
  visible = true,
  variant: "original" | "thumb" = "original",
) {
  const { taskId, evaluationId, file, saved } = props
  const kind = evaluationPreviewKind(file)
  const query = useQuery({
    queryKey: ["nodeArtifact", taskId, evaluationId, file.id, variant],
    queryFn: async () => {
      const data = await Llm4AdEvaluationsService.evaluationArtifact({
        taskId,
        evaluationId,
        artifactId: file.id,
        // Enlarged views use the authenticated original; grid cells use a
        // server-generated thumbnail (falls back to the original if unavailable).
        download: kind === "image",
        variant,
      })
      const blob = data instanceof Blob ? data : new Blob([data as BlobPart])
      if (kind === "text") return await blob.text()
      // Thumbnails arrive as image/webp. Original downloads are served as
      // octet-stream, so restore the artifact's own media type for them.
      return blob.type.startsWith("image/")
        ? blob
        : blob.slice(0, blob.size, file.media_type)
    },
    enabled: saved && visible && kind !== null,
    retry: false,
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
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

export function DownloadArtifact(
  props: ArtifactProps & { className?: string },
) {
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
      link.download = evaluationDownloadName(props.file)
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
      className={cn("size-8 shrink-0", props.className)}
      disabled={!props.saved || busy}
      title={t("evaluationData.download")}
      aria-label={t("evaluationData.downloadFile", { name: props.file.name })}
      onClick={() => void download()}
    >
      <Download className={busy ? "animate-pulse" : ""} />
    </Button>
  )
}
