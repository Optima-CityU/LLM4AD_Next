import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ImagePlus, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { LiveCodesService } from "@/client/sdk.gen"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { cn } from "@/lib/utils"

interface TargetUploadDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  liveCodeId: string
}

const ACCEPT = "image/png,image/jpeg,image/webp,image/gif"

export function TargetUploadDialog({
  open,
  onOpenChange,
  liveCodeId,
}: TargetUploadDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)

  const [files, setFiles] = useState<File[]>([])
  const [previews, setPreviews] = useState<string[]>([])
  const [validDays, setValidDays] = useState("7")
  const [scanLimit, setScanLimit] = useState("")
  const [dragOver, setDragOver] = useState(false)

  // Reset transient state whenever the dialog is (re)opened.
  useEffect(() => {
    if (open) {
      setFiles([])
      setPreviews([])
      setValidDays("7")
      setScanLimit("")
    }
  }, [open])

  // Keep object-URL previews in sync with the selected files.
  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f))
    setPreviews(urls)
    return () => {
      for (const u of urls) URL.revokeObjectURL(u)
    }
  }, [files])

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    const imgs = Array.from(incoming).filter((f) => f.type.startsWith("image/"))
    setFiles((prev) => [...prev, ...imgs])
  }

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx))
  }

  const mutation = useMutation({
    mutationFn: () => {
      const days = Number.parseInt(validDays, 10)
      const limit = scanLimit.trim() ? Number.parseInt(scanLimit, 10) : null
      return LiveCodesService.addTargets({
        liveCodeId,
        formData: {
          files,
          valid_days: Number.isFinite(days) ? days : 7,
          scan_limit: limit && Number.isFinite(limit) ? limit : null,
        },
      })
    },
    onSuccess: () => {
      toast.success(t("liveCode.upload.success"))
      queryClient.invalidateQueries({ queryKey: ["live-code", liveCodeId] })
      queryClient.invalidateQueries({ queryKey: ["live-codes"] })
      onOpenChange(false)
    },
  })

  const handleSubmit = () => {
    if (files.length === 0) {
      toast.error(t("liveCode.upload.atLeastOne"))
      return
    }
    mutation.mutate()
  }

  const daysNum = Number.parseInt(validDays, 10)
  const neverExpire = Number.isFinite(daysNum) && daysNum <= 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-lg max-h-[90vh] overflow-y-auto"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{t("liveCode.upload.title")}</DialogTitle>
          <DialogDescription>
            {t("liveCode.upload.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Drop zone */}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              addFiles(e.dataTransfer.files)
            }}
            className={cn(
              "flex w-full flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors",
              dragOver
                ? "border-primary bg-primary/5"
                : "border-muted-foreground/25 hover:border-muted-foreground/40",
            )}
          >
            <ImagePlus className="size-7 text-muted-foreground" />
            <span className="text-sm font-medium">
              {t("liveCode.upload.dropHere")}
            </span>
            <span className="text-xs text-muted-foreground">
              {t("liveCode.upload.formats")}
            </span>
          </button>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => {
              addFiles(e.target.files)
              e.target.value = ""
            }}
          />

          {/* Selected previews */}
          {files.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                {t("liveCode.upload.selected", { count: files.length })}
              </p>
              <div className="grid grid-cols-4 gap-2">
                {previews.map((src, idx) => (
                  <div
                    key={src}
                    className="group relative aspect-square overflow-hidden rounded-md border bg-muted"
                  >
                    <img src={src} alt="" className="size-full object-cover" />
                    <button
                      type="button"
                      onClick={() => removeFile(idx)}
                      className="absolute right-1 top-1 flex size-5 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Valid days + scan limit */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="valid-days">
                {t("liveCode.upload.validDays")}
              </Label>
              <Input
                id="valid-days"
                type="number"
                min={0}
                value={validDays}
                onChange={(e) => setValidDays(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                {neverExpire
                  ? t("liveCode.upload.neverExpire")
                  : t("liveCode.upload.validDaysHint")}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="scan-limit">
                {t("liveCode.upload.scanLimit")}
              </Label>
              <Input
                id="scan-limit"
                type="number"
                min={1}
                value={scanLimit}
                onChange={(e) => setScanLimit(e.target.value)}
                placeholder={t("liveCode.upload.scanLimitPlaceholder")}
              />
              <p className="text-xs text-muted-foreground">
                {t("liveCode.upload.scanLimitHint")}
              </p>
            </div>
          </div>
        </div>

        <DialogFooter className="pt-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t("common.cancel")}
          </Button>
          <LoadingButton
            onClick={handleSubmit}
            loading={mutation.isPending}
            disabled={files.length === 0}
          >
            {t("liveCode.upload.submit")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
