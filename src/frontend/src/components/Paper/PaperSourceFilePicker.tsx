import { AlertCircle, FileText, Folder, Loader2, X } from "lucide-react"
import { useRef } from "react"
import { useTranslation } from "react-i18next"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  type PaperSourcePurpose,
  paperSourceRelativePath,
  paperSourceUploadTooLarge,
} from "@/lib/paperSourceUpload"
import { cn } from "@/lib/utils"

const REFERENCE_FILES =
  ".typ,.md,.markdown,.tex,.bib,.txt,.csv,.json,.yaml,.yml,.png,.jpg,.jpeg,.pdf,.svg,.zip,.tar,.tar.gz,.tgz"
const SOURCE_FILES = `${REFERENCE_FILES},.bst,.cls,.sty,.eps`

type FileSelection = {
  purpose: PaperSourcePurpose
  files: File[]
  setFiles: (files: File[]) => void
}

export function PaperSourceFilePicker({
  purpose,
  files,
  setFiles,
  disabled = false,
}: FileSelection & { disabled?: boolean }) {
  const { t } = useTranslation()
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const append = (incoming: FileList | null) => {
    if (!incoming) return
    const selected = new Map(
      files.map((file) => [paperSourceRelativePath(file), file]),
    )
    for (const file of Array.from(incoming)) {
      selected.set(paperSourceRelativePath(file), file)
    }
    setFiles([...selected.values()])
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        disabled={disabled}
        accept={purpose === "project_context" ? REFERENCE_FILES : SOURCE_FILES}
        onChange={(event) => {
          append(event.currentTarget.files)
          event.currentTarget.value = ""
        }}
      />
      <input
        ref={folderInput}
        type="file"
        multiple
        hidden
        disabled={disabled}
        {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
        onChange={(event) => {
          append(event.currentTarget.files)
          event.currentTarget.value = ""
        }}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => fileInput.current?.click()}
        >
          <FileText data-icon="inline-start" />
          {t("paper.source.selectFiles")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => folderInput.current?.click()}
        >
          <Folder data-icon="inline-start" />
          {t("paper.source.selectFolder")}
        </Button>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        {t("paper.source.uploadLimits")}
      </p>
      {files.length > 0 ? (
        <ScrollArea
          className={cn(
            "min-w-0 rounded-lg border [&>[data-radix-scroll-area-viewport]>div]:block! [&>[data-radix-scroll-area-viewport]>div]:w-full!",
            files.length > 4 && "h-36",
          )}
        >
          <ul className="flex min-w-0 flex-col divide-y">
            {files.map((file) => {
              const path = paperSourceRelativePath(file)
              return (
                <li
                  key={path}
                  className="flex min-w-0 items-center gap-2 px-2 py-1 text-sm"
                >
                  <FileText
                    className="size-4 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1 truncate" title={path}>
                    {path}
                  </span>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    disabled={disabled}
                    aria-label={t("paper.source.removeSelectedFile", {
                      name: path,
                    })}
                    onClick={() =>
                      setFiles(files.filter((item) => item !== file))
                    }
                  >
                    <X />
                  </Button>
                </li>
              )
            })}
          </ul>
        </ScrollArea>
      ) : (
        <p className="rounded-lg border border-dashed p-3 text-center text-sm text-muted-foreground">
          {t("paper.source.noSelection")}
        </p>
      )}
      {paperSourceUploadTooLarge(files) && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>
            {t("paper.source.uploadTooLarge")}
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}

export function PaperSourceUploadDialog({
  open,
  onOpenChange,
  purpose,
  files,
  setFiles,
  pending,
  onUpload,
}: FileSelection & {
  open: boolean
  onOpenChange: (open: boolean) => void
  pending: boolean
  onUpload: () => void
}) {
  const { t } = useTranslation()
  const context = purpose === "project_context"
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="min-w-0 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {t(
              context
                ? "paper.source.projectContext.dialogTitle"
                : "paper.source.dialogTitle",
            )}
          </DialogTitle>
          <DialogDescription>
            {t(
              context
                ? "paper.source.projectContext.dialogDescription"
                : "paper.source.dialogDescription",
            )}
          </DialogDescription>
        </DialogHeader>
        <PaperSourceFilePicker
          purpose={purpose}
          files={files}
          setFiles={setFiles}
          disabled={pending}
        />
        <DialogFooter>
          <Button
            disabled={
              !files.length || pending || paperSourceUploadTooLarge(files)
            }
            onClick={onUpload}
          >
            {pending && (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            )}
            {t("paper.source.uploadSelection")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
