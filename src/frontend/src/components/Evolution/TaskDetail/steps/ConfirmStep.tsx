import Editor, { loader } from "@monaco-editor/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import yaml from "js-yaml"
import { AlertTriangle, Download, Play, Upload } from "lucide-react"
import * as monaco from "monaco-editor"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { useTheme } from "@/components/theme-provider"

loader.config({ monaco })

import type { TaskResponse } from "@/client"
import { Llm4AdTasksService } from "@/client"
import { Button } from "@/components/ui/button"
import { LoadingButton } from "@/components/ui/loading-button"
import { useCopyBeforeRun } from "@/hooks/useCopyBeforeRun"
import useCustomToast from "@/hooks/useCustomToast"
import { useEvolution } from "@/hooks/useEvolution"
import { setTaskStatusInCache } from "@/lib/task-queries"
import { handleError } from "@/utils"

const MINDMEMOS_RUNTIME_KEYS = [
  "mindmemos_base_url",
  "mindmemos_api_key",
  "mindmemos_user_id",
  "mindmemos_app_id",
  "mindmemos_agent_id",
  "mindmemos_session_id",
  "mindmemos_project_id",
]

function sanitizeConfigForYaml(obj: Record<string, unknown>): Record<string, unknown> {
  const next = { ...obj }
  const memory = next.memory
  if (memory && typeof memory === "object" && !Array.isArray(memory)) {
    const cleanMemory = { ...(memory as Record<string, unknown>) }
    for (const key of MINDMEMOS_RUNTIME_KEYS) {
      delete cleanMemory[key]
    }
    next.memory = cleanMemory
  }
  return next
}

function dumpYaml(obj: Record<string, unknown>): string {
  return yaml.dump(sanitizeConfigForYaml(obj), { indent: 2, lineWidth: -1, noRefs: true })
}

interface ConfirmStepProps {
  taskId: string
  task: TaskResponse
  configValues: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void
  onBack: () => void
  readOnly?: boolean
}

export default function ConfirmStep({
  taskId,
  task,
  configValues,
  onChange,
  onBack,
  readOnly = false,
}: ConfirmStepProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { projectId, setActiveTab, resetTaskData, setIsConfiguring, setIsViewingParams, updateTaskStatus } =
    useEvolution()
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme !== "light"
  const importInputRef = useRef<HTMLInputElement>(null)
  const { withCopyIfNeeded, isCopying } = useCopyBeforeRun(task)

  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: ["taskDataTree", taskId],
    queryFn: () => Llm4AdTasksService.getTaskDataTree({ taskId }),
    enabled: !!taskId,
  })
  const hasFile = (nodes: { type: string; children?: unknown[] | null }[]): boolean =>
    nodes.some((n) => n.type === "file" || (n.children && hasFile(n.children as typeof nodes)))
  const dataEmpty = !treeLoading && !hasFile(treeData?.tree ?? [])

  // --- YAML editor state ---
  const lastParsedRef = useRef<Record<string, unknown>>(configValues)
  const [editorText, setEditorText] = useState(() => dumpYaml(configValues))
  const [parseError, setParseError] = useState<string | null>(null)

  // Sync editor when configValues changes externally (not from our own edit)
  useEffect(() => {
    if (
      JSON.stringify(configValues) !== JSON.stringify(lastParsedRef.current)
    ) {
      lastParsedRef.current = configValues
      setEditorText(dumpYaml(configValues))
      setParseError(null)
    }
  }, [configValues])

  const handleEditorChange = (v: string | undefined) => {
    const text = v ?? ""
    setEditorText(text)
    try {
      const parsed = yaml.load(text)
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const obj = sanitizeConfigForYaml(parsed as Record<string, unknown>)
        lastParsedRef.current = obj
        onChange(obj)
        setParseError(null)
      }
    } catch (e) {
      setParseError((e as Error).message)
    }
  }

  // --- Mutations ---
  const mutation = useMutation({
    mutationFn: async () => {
      await withCopyIfNeeded(async (effectiveId) => {
        const sanitizedConfig = sanitizeConfigForYaml(configValues)
        await Llm4AdTasksService.updateTask({
          taskId: effectiveId,
          requestBody: { input_args: sanitizedConfig },
        })
        const result = await Llm4AdTasksService.runTask({ taskId: effectiveId })
        const nextStatus = result.status ?? "pending"
        setTaskStatusInCache(queryClient, effectiveId, nextStatus, projectId)
        updateTaskStatus(nextStatus)
      })
    },
    onSuccess: () => {
      showSuccessToast(t("evolution.confirmStep.submitSuccess"))
      setIsConfiguring(false)
      setIsViewingParams(false)
      setActiveTab("overview")
      resetTaskData()
    },
    onError: handleError.bind(showErrorToast),
  })

  const handleExportYaml = () => {
    try {
      const blob = new Blob([editorText], { type: "text/yaml;charset=utf-8" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `task-config-${taskId}.yaml`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err: unknown) {
      handleError.call(showErrorToast, err as import("@/client").ApiError)
    }
  }

  const handleImportYaml = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = async (ev) => {
      const text = ev.target?.result as string
      try {
        const parsed = yaml.load(text)
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          const obj = sanitizeConfigForYaml(parsed as Record<string, unknown>)
          lastParsedRef.current = obj
          setEditorText(dumpYaml(obj))
          setParseError(null)
          onChange(obj)
          await Llm4AdTasksService.updateTask({
            taskId,
            requestBody: { input_args: obj },
          })
          queryClient.invalidateQueries({ queryKey: ["getTask", taskId] })
          showSuccessToast(t("evolution.confirmStep.importSuccess"))
        } else {
          showErrorToast(t("evolution.confirmStep.importFormatError"))
        }
      } catch (err) {
        showErrorToast(
          t("evolution.confirmStep.importParseError", {
            error: (err as Error).message,
          }),
        )
      }
    }
    reader.readAsText(file)
    e.target.value = ""
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 flex flex-col space-y-4">
        <div className="flex items-center justify-end gap-2">
          {!readOnly && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => importInputRef.current?.click()}
              className="h-7 gap-1 text-xs"
            >
              <Upload className="size-3" />
              {t("evolution.confirmStep.importYaml")}
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleExportYaml}
            className="h-7 gap-1 text-xs"
          >
            <Download className="size-3" />
            {t("evolution.confirmStep.exportYaml")}
          </Button>
          {!readOnly && (
            <input
              ref={importInputRef}
              type="file"
              accept=".yaml,.yml"
              className="hidden"
              onChange={handleImportYaml}
            />
          )}
        </div>

        <div className="rounded-lg border overflow-hidden flex flex-col flex-1 min-h-0">
          {/* Toolbar */}
          <div
            className={`flex items-center justify-between px-3 py-1.5 border-b ${isDark ? "bg-[#0D1A2D] border-[#1a2d45]" : "bg-muted border-border"}`}
          >
            <span
              className={`text-xs ${isDark ? "text-[#cccccc]" : "text-muted-foreground"}`}
            >
              {t("evolution.confirmStep.configYaml")}
            </span>
            {parseError && (
              <span className="text-xs text-red-400 truncate max-w-[60%] ml-2">
                {t("evolution.confirmStep.yamlSyntaxError")}: {parseError}
              </span>
            )}
          </div>

          {/* Monaco Editor */}
          <div
            className={`flex-1 ${isDark ? "bg-[#0D1A2D]" : "bg-background"}`}
          >
            <Editor
              height="100%"
              language="yaml"
              theme={isDark ? "custom-dark" : "custom-light"}
              value={editorText}
              onChange={handleEditorChange}
              beforeMount={(m) => {
                m.editor.defineTheme("custom-dark", {
                  base: "vs-dark",
                  inherit: true,
                  rules: [],
                  colors: {
                    "editor.background": "#0D1A2D",
                  },
                })
                m.editor.defineTheme("custom-light", {
                  base: "vs",
                  inherit: true,
                  rules: [],
                  colors: {
                    "editor.background": "#ffffff",
                  },
                })
              }}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                automaticLayout: true,
                tabSize: 2,
                readOnly,
              }}
              loading={
                <div className="flex items-center justify-center h-full">
                  <p className="text-xs text-muted-foreground">
                    {t("evolution.dataPrep.loadingEditor")}
                  </p>
                </div>
              }
            />
          </div>
        </div>
      </div>

      <div className="shrink-0 py-2">
        {!readOnly && dataEmpty && (
          <div className="flex items-center gap-2 mb-3 px-4 py-2.5 rounded-md bg-yellow-500/10 border border-yellow-500/30 text-yellow-400 text-sm">
            <AlertTriangle className="size-4 shrink-0" />
            <span>{t("evolution.confirmStep.noDataWarning")}</span>
          </div>
        )}
        <div className="flex justify-center gap-6">
          <Button type="button" variant="outline" onClick={onBack}>
            {t("common.previousStep")}
          </Button>
          {!readOnly && (
            <LoadingButton
              loading={mutation.isPending || isCopying}
              onClick={() => mutation.mutate()}
              disabled={!!parseError || dataEmpty || isCopying}
              className="gap-1"
            >
              <Play className="size-3.5" />
              {t("evolution.confirmStep.submitRun")}
            </LoadingButton>
          )}
        </div>
      </div>
    </div>
  )
}
