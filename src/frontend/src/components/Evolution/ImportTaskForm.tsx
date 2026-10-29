import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, FileArchive, FolderOpen, Upload, X } from "lucide-react"
import { type ReactNode, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import {
  Llm4AdMemoryService,
  Llm4AdTasksService,
  type TaskResponse,
} from "@/client"
import MemoryProviderBindingEditor from "@/components/Memory/MemoryProviderBindingEditor"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import useCustomToast from "@/hooks/useCustomToast"
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

function formatPackageSize(bytes: number) {
  if (bytes < 1024) return bytes + " B"
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB"
  return (bytes / 1024 / 1024).toFixed(1) + " MB"
}

export default function ImportTaskForm({
  projectId,
  name,
  onNameChange,
  buildModeSelector,
  onImported,
  onClose,
  onPendingChange,
}: {
  projectId: string
  name: string
  onNameChange: (name: string) => void
  buildModeSelector: ReactNode
  onImported: (task: TaskResponse) => void
  onClose: () => void
  onPendingChange: (pending: boolean) => void
}) {
  const { t, i18n } = useTranslation()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState("")
  const [isDragging, setIsDragging] = useState(false)
  const [enableMemory, setEnableMemory] = useState(true)
  const [bindingOpen, setBindingOpen] = useState(false)
  const memoryQueryKey = ["projectMemoryConfig", projectId]
  const memoryQuery = useQuery({
    queryKey: memoryQueryKey,
    queryFn: () => Llm4AdMemoryService.getProjectMemoryConfig({ projectId }),
  })
  const bindingQuery = useQuery({
    queryKey: ["memory-provider-binding"],
    queryFn: () => Llm4AdMemoryService.getMemoryProviderBinding(),
    select: (binding) => ({
      ...binding,
      configured: binding.configured ?? false,
      embedding_locked: binding.embedding_locked ?? false,
      message: binding.message ?? "",
    }),
    enabled: bindingOpen,
  })

  const mutation = useMutation({
    mutationFn: (archive: File) =>
      Llm4AdTasksService.importTaskPackage({
        formData: {
          project_id: projectId,
          file: archive,
          name: name.trim() || undefined,
          language: i18n.language.startsWith("zh") ? "zh" : "en",
          enable_memory: enableMemory,
        },
      }),
    onMutate: () => onPendingChange(true),
    onSuccess: (data) => {
      const memory = data.input_args?.memory as
        | { enabled?: boolean; type?: string }
        | undefined
      showSuccessToast(
        t(
          memory?.enabled
            ? memory.type === "mindmemos_cloud"
              ? "evolution.importSuccessCloudMemory"
              : "evolution.importSuccessLocalMemory"
            : "evolution.importSuccess",
        ),
      )
      onImported(data)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => onPendingChange(false),
  })

  const selectFiles = (incoming: FileList | null) => {
    if (!incoming?.length || mutation.isPending) return
    mutation.reset()
    setFileError("")
    if (incoming.length !== 1) {
      setFile(null)
      setFileError(t("evolution.packageSingleRequired"))
      return
    }
    const selected = incoming[0]
    if (!selected.name.toLowerCase().endsWith(".zip")) {
      setFile(null)
      setFileError(t("evolution.packageZipRequired"))
      return
    }
    setFile(selected)
  }

  return (
    <>
      <form
        className="flex min-h-0 flex-col gap-4 pt-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (file && !fileError && !mutation.isPending) mutation.mutate(file)
        }}
      >
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
          <div className="flex flex-col gap-2">
            <Label htmlFor="import-task-name">
              {t("evolution.importTaskName")}
            </Label>
            <Input
              id="import-task-name"
              value={name}
              maxLength={255}
              disabled={mutation.isPending}
              placeholder={t("evolution.importTaskNamePlaceholder")}
              onChange={(event) => onNameChange(event.target.value)}
            />
          </div>

          {buildModeSelector}

          <section
            aria-labelledby="task-package-label"
            className="flex min-w-0 flex-col gap-2.5"
            onDragOver={(event) => {
              event.preventDefault()
              if (!mutation.isPending) setIsDragging(true)
            }}
            onDragLeave={(event) => {
              if (
                !event.currentTarget.contains(
                  event.relatedTarget as Node | null,
                )
              ) {
                setIsDragging(false)
              }
            }}
            onDrop={(event) => {
              event.preventDefault()
              setIsDragging(false)
              selectFiles(event.dataTransfer.files)
            }}
          >
            <Label id="task-package-label" htmlFor="task-package-file">
              {t("evolution.packageFile")}
            </Label>
            <input
              ref={inputRef}
              id="task-package-file"
              type="file"
              accept=".zip,application/zip"
              className="sr-only"
              tabIndex={-1}
              disabled={mutation.isPending}
              aria-describedby={
                fileError
                  ? "task-package-help task-package-error"
                  : "task-package-help"
              }
              aria-invalid={!!fileError}
              onChange={(event) => {
                selectFiles(event.target.files)
                event.target.value = ""
              }}
            />

            {file ? (
              <div
                className={cn(
                  "min-w-0 rounded-lg border bg-primary/5 p-4 transition-colors",
                  isDragging ? "border-primary" : "border-primary/25",
                )}
              >
                <div className="flex min-w-0 items-start gap-3">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <FileArchive className="size-5" />
                  </span>
                  <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                    <p className="break-all text-sm font-medium leading-relaxed">
                      {file.name}
                    </p>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <Badge variant="outline">ZIP</Badge>
                      <span>{formatPackageSize(file.size)}</span>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={mutation.isPending}
                    aria-label={t("evolution.removePackage")}
                    title={t("evolution.removePackage")}
                    onClick={() => {
                      setFile(null)
                      setFileError("")
                      mutation.reset()
                    }}
                  >
                    <X />
                  </Button>
                </div>
                <div className="mt-3 flex items-center justify-between gap-3 border-t border-primary/10 pt-2.5">
                  <span
                    role="status"
                    aria-live="polite"
                    className="text-xs leading-relaxed text-muted-foreground"
                  >
                    {t(
                      mutation.isPending
                        ? "evolution.importingPackage"
                        : "evolution.packageSelected",
                    )}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={mutation.isPending}
                    onClick={() => inputRef.current?.click()}
                  >
                    <FolderOpen />
                    {t("evolution.replacePackage")}
                  </Button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                disabled={mutation.isPending}
                aria-label={t("evolution.choosePackage")}
                aria-describedby="task-package-help"
                onClick={() => inputRef.current?.click()}
                className={cn(
                  "flex w-full flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-5 py-5 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                  isDragging
                    ? "border-primary bg-primary/10"
                    : "border-primary/25 bg-primary/5 hover:border-primary/50 hover:bg-primary/10",
                  fileError && "border-destructive/50",
                )}
              >
                <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Upload className="size-5" />
                </span>
                <span className="text-sm font-medium">
                  {t("evolution.dropPackageHere")}
                </span>
                <span className="inline-flex items-center gap-1.5 text-sm text-primary">
                  <FolderOpen className="size-4" />
                  {t("evolution.choosePackage")}
                </span>
              </button>
            )}
            <p
              id="task-package-help"
              className="text-xs leading-relaxed text-muted-foreground"
            >
              {t("evolution.packageFileHelp")}
            </p>
            {fileError && (
              <p
                id="task-package-error"
                role="alert"
                className="flex items-start gap-2 text-sm text-destructive"
              >
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                {fileError}
              </p>
            )}
            {mutation.isError && (
              <p
                role="alert"
                className="flex items-start gap-2 text-sm text-destructive"
              >
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                {t("evolution.importRetryHint")}
              </p>
            )}
          </section>

          <section className="rounded-lg border p-3">
            <div className="flex items-center gap-2.5">
              <Checkbox
                id="import-enable-memory"
                checked={enableMemory}
                disabled={mutation.isPending}
                onCheckedChange={(checked) => setEnableMemory(checked === true)}
                aria-describedby="import-memory-help"
              />
              <Label htmlFor="import-enable-memory">
                {t("evolution.importEnableMemory")}
              </Label>
            </div>
            <p
              id="import-memory-help"
              className="mt-2 text-sm leading-relaxed text-muted-foreground"
            >
              {t(
                !enableMemory
                  ? "evolution.importMemoryDisabled"
                  : memoryQuery.isPending
                    ? "evolution.importMemoryChecking"
                    : memoryQuery.isError
                      ? "evolution.importMemorySettingsUnavailable"
                      : memoryQuery.data?.system_runtime_available &&
                          memoryQuery.data.mindmemos_binding_id
                        ? "evolution.importMemoryCloud"
                        : "evolution.importMemoryLocal",
              )}
            </p>
            {enableMemory && memoryQuery.data?.system_runtime_available && (
              <Button
                type="button"
                variant="link"
                className="mt-1 h-auto p-0"
                disabled={mutation.isPending}
                onClick={() => setBindingOpen(true)}
              >
                {t("evolution.importConfigureMemory")}
              </Button>
            )}
          </section>

          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("evolution.importModelHint")}
          </p>
        </div>
        <DialogFooter className="shrink-0">
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={mutation.isPending}
          >
            {t("common.cancel")}
          </Button>
          <LoadingButton
            type="submit"
            loading={mutation.isPending}
            disabled={!file || !!fileError}
            className="border border-primary/40 bg-primary/20 text-primary hover:bg-primary/30"
          >
            {!mutation.isPending && <Upload />}
            {t(
              mutation.isPending
                ? "evolution.importingPackage"
                : "evolution.importAndCreate",
            )}
          </LoadingButton>
        </DialogFooter>
      </form>
      <Dialog open={bindingOpen} onOpenChange={setBindingOpen}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("memory.providerBinding.title")}</DialogTitle>
            <DialogDescription>
              {t("evolution.importMemoryBindingHint")}
            </DialogDescription>
          </DialogHeader>
          {bindingQuery.isPending ? (
            <p role="status">{t("common.loading")}</p>
          ) : bindingQuery.isError ? (
            <div className="space-y-3">
              <p role="alert" className="text-sm text-destructive">
                {t("evolution.importMemoryLoadError")}
              </p>
              <Button
                type="button"
                variant="outline"
                onClick={() => bindingQuery.refetch()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : (
            <MemoryProviderBindingEditor
              binding={bindingQuery.data}
              onSaved={() => {
                queryClient.invalidateQueries({
                  queryKey: ["memory-provider-binding"],
                })
                queryClient.invalidateQueries({ queryKey: memoryQueryKey })
                setBindingOpen(false)
              }}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
