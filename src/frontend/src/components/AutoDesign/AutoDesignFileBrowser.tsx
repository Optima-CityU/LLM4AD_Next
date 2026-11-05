import { useMutation, useQueryClient } from "@tanstack/react-query"
import { RefreshCw, Save } from "lucide-react"
import { lazy, Suspense, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { Group, Panel, Separator, usePanelRef } from "react-resizable-panels"
import { toast } from "sonner"

import { ApiError, Llm4AdAutodesignService } from "@/client"
import SourceNavigator from "@/components/Common/SourceNavigator"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAutoDesignFile, useAutoDesignFiles } from "@/hooks/useAutoDesign"
import { useIsMobile } from "@/hooks/useMobile"
import { autoDesignFileTree, autoDesignKeys } from "@/lib/autoDesign"
import {
  autoDesignProjectFiles,
  autoDesignProjectTree,
} from "@/lib/autoDesignPresentation"

const ArtifactCodeEditor = lazy(
  () => import("@/components/AutoResearch/ArtifactCodeEditor"),
)
const ReportMarkdown = lazy(
  () => import("@/components/Evolution/TaskDetail/ReportMarkdown"),
)

type Buffer = { content: string; original: string; revision: string }

export default function AutoDesignFileBrowser({
  sessionId,
  area,
  disabled,
  onDirtyChange,
  onSavingChange,
  selectedPath,
  onSelectedPathChange,
  onUploadReferences,
  uploading = false,
  projectPath,
}: {
  sessionId: string
  area: "draft" | "references"
  disabled: boolean
  onDirtyChange?: (dirty: boolean) => void
  onSavingChange?: (saving: boolean) => void
  selectedPath?: string | null
  onSelectedPathChange?: (path: string | null) => void
  onUploadReferences?: () => void
  uploading?: boolean
  projectPath?: string | null
}) {
  const { t } = useTranslation()
  const isMobile = useIsMobile()
  const client = useQueryClient()
  const files = useAutoDesignFiles(sessionId, area)
  const [selected, setSelected] = useState<string | null>(null)
  const [buffers, setBuffers] = useState<Record<string, Buffer>>({})
  const [preview, setPreview] = useState(true)
  const [navigatorCollapsed, setNavigatorCollapsed] = useState(false)
  const navigatorRef = usePanelRef()
  const selection = selectedPath === undefined ? selected : selectedPath
  const selectPath = onSelectedPathChange ?? setSelected
  const entries =
    area === "draft"
      ? autoDesignProjectFiles(files.data?.files ?? [], projectPath)
      : (files.data?.files ?? [])
  const path =
    selection && entries.some((entry) => entry.path === selection)
      ? selection
      : (entries[0]?.path ?? "")
  const file = useAutoDesignFile(sessionId, area, path)
  const buffer = buffers[path]
  const dirty = Object.keys(buffers).length > 0
  const tree = useMemo(
    () =>
      area === "draft"
        ? autoDesignProjectTree(files.data?.files ?? [], projectPath)
        : autoDesignFileTree(files.data?.files ?? []),
    [files.data, area, projectPath],
  )
  const sizes = useMemo(
    () =>
      Object.fromEntries(entries.map((entry) => [entry.path, entry.size ?? 0])),
    [entries],
  )
  const remove = useMutation({
    mutationFn: (target: string) =>
      Llm4AdAutodesignService.deleteMaterial({
        sessionId,
        path: target,
      }),
    onSuccess: (_result, target) => {
      if (path === target) selectPath(null)
      void client.invalidateQueries({ queryKey: autoDesignKeys.all })
      toast.success(t("autodesign.referenceDeleted"))
    },
    onError: () => toast.error(t("autodesign.referenceDeleteFailed")),
  })
  const markdown = /\.(md|markdown)$/i.test(path)
  const plainText = /\.(txt|rst)$/i.test(path)
  const readable = markdown || plainText
  const areaTitle = t(
    area === "draft" ? "autodesign.generatedFiles" : "autodesign.materials",
  )
  const save = useMutation({
    mutationFn: (request: {
      path: string
      content: string
      revision: string
    }) =>
      Llm4AdAutodesignService.updateDraftFile({
        sessionId,
        requestBody: request,
      }),
    onSuccess: (saved) => {
      client.setQueryData(
        autoDesignKeys.file(sessionId, "draft", saved.path),
        saved,
      )
      setBuffers((previous) => {
        const next = { ...previous }
        delete next[saved.path]
        return next
      })
      void client.invalidateQueries({ queryKey: autoDesignKeys.all })
      toast.success(t("autodesign.draftSaved"))
    },
    onError: (error) =>
      toast.error(
        t(
          error instanceof ApiError && error.status === 409
            ? "autodesign.editConflict"
            : "autodesign.saveFailed",
        ),
      ),
  })
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])
  useEffect(
    () => onSavingChange?.(save.isPending || remove.isPending),
    [save.isPending, remove.isPending, onSavingChange],
  )
  const busy = disabled || save.isPending || uploading || remove.isPending
  const reload = () => {
    setBuffers((previous) => {
      const next = { ...previous }
      delete next[path]
      return next
    })
    void file.refetch()
    void files.refetch()
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2">
        <div className="min-w-0 flex-1" title={path}>
          <p className="truncate text-sm font-medium">
            {path.split("/").slice(-1)[0] || areaTitle}
          </p>
          {path && (
            <p className="truncate text-xs text-muted-foreground">{path}</p>
          )}
        </div>
        {area !== "draft" && (
          <Badge variant="outline">{t("autodesign.workspace.readOnly")}</Badge>
        )}
        {buffer && <Badge variant="secondary">{t("autodesign.unsaved")}</Badge>}
        {readable && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setPreview(!preview)}
          >
            {t(
              preview
                ? area === "draft"
                  ? "autodesign.editSource"
                  : "autodesign.viewSource"
                : "autodesign.preview",
            )}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={reload}
          aria-label={t(
            buffer ? "autodesign.discardChanges" : "autodesign.refresh",
          )}
          title={t(buffer ? "autodesign.discardChanges" : "autodesign.refresh")}
        >
          <RefreshCw />
        </Button>
        {area === "draft" && (
          <Button
            size="sm"
            disabled={busy || !buffer || file.data?.editable === false}
            onClick={() =>
              buffer &&
              save.mutate({
                path,
                content: buffer.content,
                revision: buffer.revision,
              })
            }
          >
            <Save data-icon="inline-start" />
            {t(save.isPending ? "common.loading" : "common.save")}
          </Button>
        )}
      </div>
      {buffer && file.data?.revision !== buffer.revision && (
        <Alert>
          <AlertDescription>{t("autodesign.draftChanged")}</AlertDescription>
        </Alert>
      )}
      {files.isError && (
        <Alert variant="destructive">
          <AlertDescription>
            {t("autodesign.filesFailed")}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void files.refetch()}
            >
              {t("common.retry")}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {entries.length === 0 && !files.isError ? (
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-5">
          {files.isPending ? (
            <div className="flex w-full flex-col gap-3">
              <Skeleton className="h-6 w-1/2" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : (
            <Card className="w-full max-w-md">
              <CardHeader>
                <CardTitle>
                  {t(
                    area === "references"
                      ? "autodesign.workspace.referencesEmptyTitle"
                      : "autodesign.workspace.projectEmptyTitle",
                  )}
                </CardTitle>
                <CardDescription className="leading-6">
                  {t(
                    area === "references"
                      ? "autodesign.workspace.referencesEmptyHint"
                      : "autodesign.projectFilesPending",
                  )}
                </CardDescription>
              </CardHeader>
              {area === "references" && onUploadReferences && (
                <CardFooter>
                  <Button
                    variant="outline"
                    onClick={onUploadReferences}
                    disabled={busy}
                  >
                    {t("autodesign.uploadMaterials")}
                  </Button>
                </CardFooter>
              )}
            </Card>
          )}
        </div>
      ) : (
        <Group
          orientation={isMobile ? "vertical" : "horizontal"}
          className="min-h-0 flex-1 overflow-hidden"
        >
          <Panel
            panelRef={navigatorRef}
            collapsible
            collapsedSize="40px"
            defaultSize={isMobile ? "26%" : "30%"}
            minSize={isMobile ? "96px" : "140px"}
            onResize={() =>
              setNavigatorCollapsed(
                navigatorRef.current?.isCollapsed() ?? false,
              )
            }
            className="min-h-0 min-w-0"
          >
            <SourceNavigator
              title={areaTitle}
              manifest={entries.map((entry) => entry.path)}
              tree={tree}
              sizeMap={sizes}
              selectedPath={path}
              onSelect={selectPath}
              onDelete={
                area === "references" && !busy ? remove.mutate : undefined
              }
              canDeletePath={(_target, isDirectory) =>
                !isDirectory && area === "references"
              }
              confirmDelete
              onUpload={area === "references" ? onUploadReferences : undefined}
              uploading={uploading}
              locked={busy}
              collapsed={navigatorCollapsed}
              onToggle={() => {
                if (navigatorCollapsed) navigatorRef.current?.expand()
                else navigatorRef.current?.collapse()
              }}
              selectableSuffixes={null}
              uploadLabel={t("autodesign.uploadMaterials")}
            />
          </Panel>
          <Separator
            className={`${isMobile ? "h-1" : "w-1"} bg-border hover:bg-primary/20`}
          />
          <Panel minSize="160px">
            <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
              {!path ? (
                <p className="p-4 text-sm leading-relaxed text-muted-foreground">
                  {t(
                    area === "references"
                      ? "autodesign.referencesPending"
                      : "autodesign.projectFilesPending",
                  )}
                </p>
              ) : file.isError ? (
                <Alert variant="destructive">
                  <AlertDescription>
                    {t("autodesign.fileUnavailable")}
                  </AlertDescription>
                </Alert>
              ) : file.data ? (
                <>
                  {area === "draft" && !file.data.editable && (
                    <p className="px-3 py-2 text-sm text-muted-foreground">
                      {t("autodesign.protectedFile")}
                    </p>
                  )}
                  <Suspense
                    fallback={
                      <p role="status" className="p-4">
                        {t("common.loading")}
                      </p>
                    }
                  >
                    {plainText && preview ? (
                      <div className="min-h-0 flex-1 overflow-auto p-4">
                        <pre className="whitespace-pre-wrap break-words font-sans text-[15px] leading-7 [overflow-wrap:anywhere]">
                          {file.data.content}
                        </pre>
                      </div>
                    ) : markdown && preview ? (
                      <div className="min-h-0 flex-1 overflow-auto p-4">
                        <ReportMarkdown
                          content={buffer?.content ?? file.data.content}
                          className="break-words text-[15px] leading-7 [overflow-wrap:anywhere] prose-p:leading-7 prose-li:leading-7 prose-table:block prose-table:overflow-x-auto"
                        />
                      </div>
                    ) : (
                      <ArtifactCodeEditor
                        key={path}
                        filePath={path}
                        value={buffer?.content ?? file.data.content}
                        readOnly={busy || !file.data.editable}
                        className="min-h-0 flex-1"
                        onChange={(content) => {
                          if (
                            !file.data?.revision ||
                            !file.data.editable ||
                            busy
                          )
                            return
                          setBuffers((previous) => {
                            const original =
                              previous[path]?.original ?? file.data!.content
                            const next = { ...previous }
                            if (content === original) delete next[path]
                            else
                              next[path] = {
                                content,
                                original,
                                revision:
                                  previous[path]?.revision ??
                                  file.data!.revision!,
                              }
                            return next
                          })
                        }}
                      />
                    )}
                  </Suspense>
                </>
              ) : (
                <p role="status" className="p-4">
                  {t("common.loading")}
                </p>
              )}
            </div>
          </Panel>
        </Group>
      )}
    </div>
  )
}
