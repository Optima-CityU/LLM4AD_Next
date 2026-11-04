import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  AlertCircle,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  FileCode2,
  Loader2,
  MessageSquare,
} from "lucide-react"
import {
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import {
  type AutoDesignSessionResponse,
  Llm4AdAutodesignService,
  type TaskResponse,
} from "@/client"
import PaperNativeRuntime from "@/components/Paper/PaperNativeRuntime"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  autoDesignTaskSessionOptions,
  useAutoDesignContext,
  useAutoDesignFiles,
} from "@/hooks/useAutoDesign"
import { usePersistentState } from "@/hooks/usePersistentState"
import {
  AUTO_DESIGN_REFERENCE_ACCEPT,
  autoDesignKeys,
  autoDesignModelProfile,
  autoDesignTaskSearch,
  isAutoDesignExecutionLocked,
  isAutoDesignReferenceName,
} from "@/lib/autoDesign"
import {
  autoDesignFileRevision,
  autoDesignProjectFiles,
} from "@/lib/autoDesignPresentation"
import { taskKeys } from "@/lib/task-queries"
import { cn } from "@/lib/utils"
import AutoDesignFileBrowser from "./AutoDesignFileBrowser"
import AutoDesignMemoryDialog from "./AutoDesignMemoryDialog"
import AutoDesignSettings from "./AutoDesignSettings"
import AutoDesignStatusBadge from "./AutoDesignStatusBadge"

type Props = {
  sessionId?: string
  taskId?: string
  projectId?: string
  onOpenTask?: (task: TaskResponse) => void
  headerAction?: ReactNode
}

export default function AutoDesignPanel({
  sessionId,
  taskId,
  projectId,
  onOpenTask,
  headerAction,
}: Props) {
  const { t } = useTranslation()
  const taskSession = useQuery({
    ...autoDesignTaskSessionOptions(taskId ?? ""),
    enabled: Boolean(taskId && !sessionId),
  })
  const resolvedId = sessionId ?? taskSession.data?.id
  const session = useQuery({
    queryKey: autoDesignKeys.session(resolvedId ?? ""),
    queryFn: () =>
      Llm4AdAutodesignService.getSession({ sessionId: resolvedId! }),
    enabled: Boolean(resolvedId),
    refetchInterval: 5000,
  })
  if (
    (!taskSession.data && taskSession.isError) ||
    (!session.data && session.isError)
  )
    return (
      <div className="space-y-3">
        {headerAction}
        <Alert variant="destructive">
          <AlertCircle />
          <AlertTitle>{t("autodesign.sessionFailed")}</AlertTitle>
          <AlertDescription>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (resolvedId) void session.refetch()
                else void taskSession.refetch()
              }}
            >
              {t("common.retry")}
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    )
  if (!session.data)
    return (
      <div className="space-y-3 p-3">
        {headerAction}
        <p role="status" className="text-sm text-muted-foreground">
          {t("common.loading")}
        </p>
      </div>
    )
  return (
    <SessionPanel
      key={session.data.id}
      session={session.data}
      taskId={taskId}
      projectId={projectId}
      onOpenTask={onOpenTask}
      headerAction={headerAction}
    />
  )
}

function SessionPanel({
  session,
  taskId,
  projectId,
  onOpenTask,
  headerAction,
}: Omit<Props, "sessionId"> & { session: AutoDesignSessionResponse }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const context = useAutoDesignContext(session.id, taskId)
  const files = useAutoDesignFiles(session.id, "draft")
  const references = useAutoDesignFiles(session.id, "references")
  const isChildTask = Boolean(context.data?.task?.parent_id)
  const isAnalysis = Boolean(
    context.data?.task && context.data.task.status !== "uninitialized",
  )
  const guidance = useMemo(() => {
    if (!taskId && !session.task_id) return undefined
    return {
      title: t(
        isAnalysis
          ? "autodesign.analysis.title"
          : isChildTask
            ? "autodesign.tuning.childTitle"
            : "autodesign.tuning.title",
      ),
      description: t(
        isAnalysis
          ? "autodesign.analysis.description"
          : "autodesign.tuning.description",
      ),
      examples: ["explain", "diagnose", "apply"].map((key) =>
        t(`autodesign.${isAnalysis ? "analysis" : "tuning"}.examples.${key}`),
      ),
    }
  }, [taskId, session.task_id, isChildTask, isAnalysis, t])
  useEffect(
    () => () => {
      // Reopen the persisted conversation through a fresh authenticated bootstrap.
      void queryClient.invalidateQueries({
        queryKey: ["paper-runtime-session", session.id],
        refetchType: "none",
      })
    },
    [queryClient, session.id],
  )
  const [settings, setSettings] = useState<"model" | "preferences" | null>(null)
  const uploadInput = useRef<HTMLInputElement>(null)
  const [mobileView, setMobileView] = useState("conversation")
  const [rightCollapsed, setRightCollapsed] = usePersistentState(
    "autodesign:rightCollapsed",
    false,
  )
  const projectPanelId = useId()
  // Choose the initial material once; generated files must not interrupt reading.
  const initialPreviewSelected = useRef(false)
  const [previewView, setPreviewView] = useState<"draft" | "references">(
    "references",
  )
  const [openedBrowsers, setOpenedBrowsers] = useState({
    draft: false,
    references: true,
  })
  const [draftPath, setDraftPath] = useState<string | null>(null)
  const [referencePath, setReferencePath] = useState<string | null>(null)
  const [filesDirty, setFilesDirty] = useState(false)
  const [filesSaving, setFilesSaving] = useState(false)
  const [referencesSaving, setReferencesSaving] = useState(false)
  const unsaved = filesDirty
  const openPreview = (view: "draft" | "references") => {
    initialPreviewSelected.current = true
    setRightCollapsed(false)
    setOpenedBrowsers((previous) => ({ ...previous, [view]: true }))
    setPreviewView(view)
    setMobileView("project")
  }
  const upload = useMutation({
    mutationFn: (selectedFiles: File[]) =>
      Llm4AdAutodesignService.uploadMaterials({
        sessionId: session.id,
        formData: {
          files: selectedFiles,
          relative_paths: selectedFiles.map(
            (file) => file.webkitRelativePath || file.name,
          ),
        },
      }),
    onSuccess: (_result, selectedFiles) => {
      const first = selectedFiles[0]
      if (first) setReferencePath(first.webkitRelativePath || first.name)
      openPreview("references")
      void queryClient.invalidateQueries({ queryKey: autoDesignKeys.all })
      toast.success(t("autodesign.materialsUploaded"))
    },
    onError: () => toast.error(t("autodesign.materialsFailed")),
  })
  const saving = filesSaving || referencesSaving || upload.isPending
  useEffect(() => {
    if (!unsaved) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [unsaved])
  const familyRevision = JSON.stringify(
    (context.data?.family ?? []).map((member) => [
      member.id,
      member.name,
      member.status,
      member.active_child_id,
      member.input_args,
    ]),
  )
  const previousFamilyRevision = useRef<string | null>(null)
  useEffect(() => {
    if (!context.data || previousFamilyRevision.current === familyRevision)
      return
    previousFamilyRevision.current = familyRevision
    // Canonical task queries retain their full configuration and credentials.
    void queryClient.invalidateQueries({ queryKey: taskKeys.allTasks })
    for (const member of context.data.family ?? [])
      void queryClient.invalidateQueries({
        queryKey: taskKeys.detail(member.id),
      })
    if (context.data.root_task_id)
      void queryClient.invalidateQueries({
        queryKey: taskKeys.tree(context.data.root_task_id),
      })
  }, [context.data, familyRevision, queryClient])
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: autoDesignKeys.all })
  const report =
    context.data?.validation_report ?? session.validation_report ?? {}
  const current = Boolean(
    context.data?.validation_current && !context.data?.draft_conflicts?.length,
  )
  const projectFiles = autoDesignProjectFiles(
    files.data?.files ?? [],
    context.data?.selected_project_path,
  )
  useEffect(() => {
    if (
      initialPreviewSelected.current ||
      !files.isSuccess ||
      !context.isSuccess
    )
      return
    initialPreviewSelected.current = true
    if (projectFiles.length > 0) {
      setOpenedBrowsers((previous) => ({ ...previous, draft: true }))
      setPreviewView("draft")
    }
  }, [context.isSuccess, files.isSuccess, projectFiles.length])
  const previousTitle = useRef(session.title)
  useEffect(() => {
    if (previousTitle.current === session.title) return
    previousTitle.current = session.title
    void queryClient.invalidateQueries({ queryKey: autoDesignKeys.sessions })
  }, [session.title, queryClient])
  const draftRevision = files.isSuccess
    ? autoDesignFileRevision(projectFiles)
    : undefined
  const referenceRevision = references.isSuccess
    ? autoDesignFileRevision(references.data.files)
    : undefined
  const [seenRevisions, setSeenRevisions] = useState<
    Partial<Record<"draft" | "references", string>>
  >({})
  useEffect(() => {
    const revisions = {
      draft: draftRevision,
      references: referenceRevision,
    }
    setSeenRevisions((previous) => {
      const next = { ...previous }
      let changed = false
      for (const area of ["draft", "references"] as const) {
        const revision = revisions[area]
        if (
          revision !== undefined &&
          (previous[area] === undefined || previewView === area) &&
          previous[area] !== revision
        ) {
          next[area] = revision
          changed = true
        }
      }
      return changed ? next : previous
    })
  }, [draftRevision, referenceRevision, previewView])
  const navigation = [
    {
      value: "draft" as const,
      label: t("autodesign.workspace.artifacts"),
      count: projectFiles.length,
      updated:
        draftRevision !== undefined &&
        seenRevisions.draft !== undefined &&
        draftRevision !== seenRevisions.draft,
    },
    {
      value: "references" as const,
      label: t("autodesign.materials"),
      count: references.data?.files.length ?? 0,
      updated:
        referenceRevision !== undefined &&
        seenRevisions.references !== undefined &&
        referenceRevision !== seenRevisions.references,
    },
  ]
  const openProjectFiles = (path?: string) => {
    if (path) setDraftPath(path)
    openPreview("draft")
  }
  const configure = useMutation({
    mutationFn: () =>
      Llm4AdAutodesignService.prepareTask({
        sessionId: session.id,
        requestBody: {
          project_id: projectId ?? session.project_id ?? undefined,
          task_id:
            context.data?.task?.status === "uninitialized"
              ? context.data.task.id
              : undefined,
        },
      }),
    onSuccess: async (result) => {
      queryClient.setQueryData(
        autoDesignKeys.session(session.id),
        result.session,
      )
      await Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: taskKeys.allTasks }),
        queryClient.invalidateQueries({ queryKey: ["listProjects"] }),
        queryClient.invalidateQueries({ queryKey: ["getTask"] }),
      ])
      toast.success(t("autodesign.readyToConfigure"))
      if (onOpenTask) onOpenTask(result.task)
      else
        void navigate({
          to: "/evolution",
          search: autoDesignTaskSearch(result.task),
        })
    },
    onError: () => {
      toast.error(t("autodesign.actionFailed"))
      void refresh()
    },
  })
  const openedRun = useRef<string | null>(null)
  const runningTask = context.data?.family?.find(
    (member) => member.id === context.data?.running_task_id,
  )
  useEffect(() => {
    if (taskId || !runningTask || openedRun.current === runningTask.id) return
    if (!isAutoDesignExecutionLocked(runningTask.status)) return
    openedRun.current = runningTask.id
    if (onOpenTask) onOpenTask(runningTask)
    else
      void navigate({
        to: "/evolution",
        search: autoDesignTaskSearch(runningTask),
      })
  }, [taskId, runningTask, onOpenTask, navigate])
  const locked = Boolean(
    context.data?.editing_locked ||
      isAutoDesignExecutionLocked(context.data?.task?.status),
  )
  const projectStatus = (
    <AutoDesignStatusBadge
      loading={context.isPending || files.isPending}
      unavailable={context.isError || files.isError}
      unsaved={unsaved}
      hasFiles={projectFiles.length > 0}
      validationCurrent={current}
      report={report}
      task={
        context.data?.family?.find(
          (member) => member.id === context.data?.running_task_id,
        ) ?? context.data?.task
      }
      onRetry={() => void refresh()}
    />
  )
  if (locked) {
    const runningId = context.data?.running_task_id ?? context.data?.task?.id
    return (
      <Alert>
        <AlertCircle />
        <AlertTitle className="flex flex-wrap items-center gap-2">
          {t("autodesign.runningLockedTitle")}
          {projectStatus}
        </AlertTitle>
        <AlertDescription>
          {headerAction}
          {t("autodesign.runningLockedHint")}
          {runningId && session.project_id && (
            <Button variant="outline" asChild>
              <Link
                to="/evolution"
                search={{ projectId: session.project_id, taskId: runningId }}
              >
                {t("autodesign.openTask")}
              </Link>
            </Button>
          )}
        </AlertDescription>
      </Alert>
    )
  }
  return (
    <Tabs
      value={mobileView}
      onValueChange={setMobileView}
      className="h-full min-h-0 gap-0 overflow-hidden rounded-lg border bg-background"
    >
      <TabsList className="mx-4 my-3 shrink-0 lg:hidden">
        <TabsTrigger value="conversation">
          <MessageSquare />
          {t("autodesign.agent")}
        </TabsTrigger>
        <TabsTrigger value="project">
          <FileCode2 />
          {t("autodesign.workspace.title")}
        </TabsTrigger>
      </TabsList>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <TabsContent
          value="conversation"
          forceMount
          className="m-0 flex min-h-0 min-w-0 flex-1 flex-col data-[state=inactive]:hidden lg:data-[state=inactive]:flex"
        >
          {context.isPending ? (
            <div className="space-y-3 p-3">
              {headerAction}
              <p role="status">{t("common.loading")}</p>
            </div>
          ) : context.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {headerAction}
                {t("autodesign.contextFailed")}
                <Button
                  variant="outline"
                  onClick={() => void context.refetch()}
                >
                  {t("common.retry")}
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <PaperNativeRuntime
              workspaceId={session.id}
              stage="autodesign"
              modelReady={Boolean(
                session.analysis_provider_id && session.analysis_model_name,
              )}
              prerequisiteReady
              profileKey={autoDesignModelProfile(session)}
              artifactPaths={projectFiles.map((file) => file.path)}
              modelName={session.analysis_model_name}
              modelProviderId={session.analysis_provider_id}
              contextWindowTokens={session.analysis_context_window_tokens}
              maxOutputTokens={session.analysis_max_output_tokens}
              conversationPreferences={session.conversation_preferences ?? {}}
              onConfigureModel={() => setSettings("model")}
              onConfigurePreferences={() => setSettings("preferences")}
              onOpenArtifact={openProjectFiles}
              onConversationRewind={() => void refresh()}
              headerAction={
                <>
                  <AutoDesignMemoryDialog
                    task={context.data?.task}
                    projectId={projectId ?? session.project_id}
                  />
                  {headerAction}
                </>
              }
              guidance={guidance}
            />
          )}
        </TabsContent>
        <Button
          type="button"
          variant="ghost"
          className="hidden h-full w-6 shrink-0 rounded-none border-x border-border/40 p-0 text-muted-foreground hover:bg-primary/10 hover:text-primary lg:flex"
          aria-controls={projectPanelId}
          aria-expanded={!rightCollapsed}
          aria-label={t(
            rightCollapsed
              ? "evolution.expandPanel"
              : "evolution.collapsePanel",
          )}
          title={t(
            rightCollapsed
              ? "evolution.expandPanel"
              : "evolution.collapsePanel",
          )}
          onClick={() => setRightCollapsed((previous) => !previous)}
        >
          {rightCollapsed ? (
            <ChevronLeft aria-hidden="true" />
          ) : (
            <ChevronRight aria-hidden="true" />
          )}
        </Button>
        <TabsContent
          value="project"
          forceMount
          className={cn(
            "m-0 flex min-h-0 min-w-0 flex-1 flex-col data-[state=inactive]:hidden lg:flex-none",
            rightCollapsed
              ? "lg:hidden lg:data-[state=inactive]:hidden"
              : "lg:w-[54%] lg:data-[state=inactive]:flex",
          )}
        >
          <aside
            id={projectPanelId}
            className="flex h-full min-h-0 flex-col"
            aria-label={t("autodesign.workspace.title")}
          >
            <header className="flex shrink-0 items-center gap-2 px-4 py-3">
              <h2
                className="min-w-0 flex-1 truncate text-sm font-semibold"
                title={session.title}
              >
                {session.title}
              </h2>
              {projectStatus}
            </header>
            <Input
              ref={uploadInput}
              type="file"
              multiple
              accept={AUTO_DESIGN_REFERENCE_ACCEPT}
              className="hidden"
              aria-label={t("autodesign.uploadMaterials")}
              disabled={saving || configure.isPending}
              onChange={(event) => {
                const selectedFiles = Array.from(
                  event.currentTarget.files ?? [],
                )
                event.currentTarget.value = ""
                if (
                  selectedFiles.some(
                    (file) => !isAutoDesignReferenceName(file.name),
                  )
                )
                  toast.error(t("autodesign.textDocumentsOnly"))
                else if (selectedFiles.length) upload.mutate(selectedFiles)
              }}
            />
            <Tabs
              value={previewView}
              onValueChange={(value) =>
                openPreview(value as typeof previewView)
              }
              className="min-h-0 flex-1 gap-0"
            >
              <div className="shrink-0 border-b px-4 pb-3">
                <TabsList
                  aria-label={t("autodesign.workspace.navigation")}
                  className="w-full"
                >
                  {navigation.map((item) => (
                    <TabsTrigger
                      key={item.value}
                      value={item.value}
                      className="min-w-0 gap-1"
                      title={
                        item.value === "draft" && item.updated && item.count > 0
                          ? t("autodesign.workspace.viewNewArtifacts", {
                              count: item.count,
                            })
                          : undefined
                      }
                    >
                      {item.label}
                      {item.count > 0 && (
                        <span className="text-xs tabular-nums text-muted-foreground">
                          {item.count}
                        </span>
                      )}
                      {item.updated &&
                      item.value === "draft" &&
                      item.count > 0 ? (
                        <Badge variant="secondary">
                          {t("autodesign.workspace.newArtifacts")}
                        </Badge>
                      ) : item.updated ? (
                        <Badge
                          className="size-1.5 shrink-0 rounded-full p-0"
                          aria-label={t("autodesign.workspace.updated")}
                          title={t("autodesign.workspace.updated")}
                        />
                      ) : null}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </div>
              <TabsContent
                value="draft"
                forceMount
                className="m-0 flex min-h-0 flex-col data-[state=inactive]:hidden"
              >
                {openedBrowsers.draft && (
                  <AutoDesignFileBrowser
                    sessionId={session.id}
                    area="draft"
                    projectPath={context.data?.selected_project_path}
                    selectedPath={draftPath}
                    onSelectedPathChange={setDraftPath}
                    disabled={
                      saving ||
                      report.status === "running" ||
                      configure.isPending
                    }
                    onDirtyChange={setFilesDirty}
                    onSavingChange={setFilesSaving}
                  />
                )}
              </TabsContent>
              <TabsContent
                value="references"
                forceMount
                className="m-0 flex min-h-0 flex-col data-[state=inactive]:hidden"
              >
                {openedBrowsers.references && (
                  <AutoDesignFileBrowser
                    sessionId={session.id}
                    area="references"
                    selectedPath={referencePath}
                    onSelectedPathChange={setReferencePath}
                    disabled={saving || configure.isPending}
                    onSavingChange={setReferencesSaving}
                    onUploadReferences={() => uploadInput.current?.click()}
                    uploading={upload.isPending}
                  />
                )}
              </TabsContent>
            </Tabs>
            <Separator />
            <footer className="flex shrink-0 items-center justify-end gap-3 px-4 py-3">
              <Button
                className="shrink-0"
                disabled={
                  !current ||
                  unsaved ||
                  saving ||
                  report.status === "running" ||
                  configure.isPending ||
                  context.isError ||
                  context.isPending
                }
                onClick={() => configure.mutate()}
              >
                {configure.isPending ? (
                  <Loader2
                    data-icon="inline-start"
                    className="animate-spin motion-reduce:animate-none"
                  />
                ) : null}
                {t(
                  configure.isPending
                    ? "common.loading"
                    : "autodesign.workspace.configure",
                )}
                {!configure.isPending && <ArrowRight data-icon="inline-end" />}
              </Button>
            </footer>
          </aside>
        </TabsContent>
      </div>
      {settings && (
        <AutoDesignSettings
          session={session}
          kind={settings}
          onClose={() => setSettings(null)}
        />
      )}
    </Tabs>
  )
}
