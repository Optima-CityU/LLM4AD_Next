import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Layers,
  Loader2,
  Save,
  Settings2,
  Sparkles,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { FileTreeNode, TaskResponse } from "@/client"
import { Llm4AdTasksService } from "@/client"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import { useEvolution } from "@/hooks/useEvolution"

import Stepper from "./Stepper"
import StorageUsageBadge from "./StorageUsageBadge"
import { isMemoryEnabled } from "./schema-form/islandStrategyPreview"
import type { JsonSchema } from "./schema-form/resolveSchema"
import { mergeSchemaDefaults } from "./schema-form/resolveSchema"
import AdvancedStep from "./steps/AdvancedStep"
import ConfirmStep from "./steps/ConfirmStep"
import DataPreparationStep from "./steps/DataPreparationStep"
import EvolutionProviderSelect, {
  EvaluatorProviderSelect,
} from "./steps/EvolutionProviderSelect"
import EvolveAnnotationStep from "./steps/EvolveAnnotationStep"
import MemoryConfigStep from "./steps/MemoryConfigStep"
import SchemaStepForm from "./steps/SchemaStepForm"

const STEP_SCHEMA_KEYS: Record<number, string> = {
  2: "evaluator",
  3: "evolution",
  4: "coder",
}

const STEP_DESCRIPTIONS_KEYS: Record<number, string> = {
  2: "evaluator",
  3: "evolution",
  4: "coder",
}

const ADVANCED_KEYS = [
  "providers",
  "logging",
  "workspace",
  "version_control",
  "multimodal",
  "planner",
  "repo_analyzer",
  "project_name",
  "background",
  "run_id",
  "random_seed",
  "base_dir",
]

interface UninitializedViewProps {
  task: TaskResponse
  readOnly?: boolean
  onSwitchToChat?: () => void
}

export default function UninitializedView({
  task,
  readOnly = false,
  onSwitchToChat,
}: UninitializedViewProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { setIsConfiguring, setIsViewingParams } = useEvolution()
  const [currentStep, setCurrentStep] = useState(0)
  const [isSaving, setIsSaving] = useState(false)

  // Data tree query for step 0 validation
  const { data: dataTreeResponse } = useQuery({
    queryKey: ["taskDataTree", task.id],
    queryFn: () => Llm4AdTasksService.getTaskDataTree({ taskId: task.id }),
    enabled: !!task.id,
  })
  const dataStepCompleted = useMemo(() => {
    const tree = dataTreeResponse?.tree
    if (!tree || tree.length === 0) return false
    const hasFile = (nodes: FileTreeNode[]): boolean => {
      for (const node of nodes) {
        if (node.type === "file") return true
        if (node.children && hasFile(node.children)) return true
      }
      return false
    }
    return hasFile(tree)
  }, [dataTreeResponse])

  const STEP_GUIDE_KEYS = [
    "dataPreparation",
    "evolveAnnotation",
    "evaluator",
    "evolution",
    "coder",
    "memory",
    "advanced",
    "confirmation",
  ]

  const steps = useMemo(
    () => [
      {
        label: t("evolution.steps.dataPreparation"),
        completed: dataStepCompleted,
      },
      { label: t("evolution.steps.evolveAnnotation") },
      { label: t("evolution.steps.evaluator") },
      { label: t("evolution.steps.evolutionAlgorithm") },
      { label: t("evolution.steps.codeGeneration") },
      { label: t("evolution.steps.memoryModule") },
      { label: t("evolution.steps.advancedComponents") },
      { label: t("evolution.steps.parameterConfirmation") },
    ],
    [t, dataStepCompleted],
  )

  // Step guidance: warning | success | default
  const currentGuideKey = STEP_GUIDE_KEYS[currentStep]
  const getStepStatus = (): "warning" | "success" | "default" => {
    if (currentStep === 0) return dataStepCompleted ? "success" : "warning"
    if (currentStep === 1) return dataStepCompleted ? "default" : "warning"
    return "default"
  }
  const stepStatus = getStepStatus()
  const stepGuideText = (() => {
    if (stepStatus === "warning") {
      return t(`evolution.stepGuide.${currentGuideKey}Warning`)
    }
    if (stepStatus === "success") {
      return t(`evolution.stepGuide.${currentGuideKey}Success`)
    }
    return t(`evolution.stepGuide.${currentGuideKey}`)
  })()

  const [configValues, setConfigValues] = useState<Record<string, unknown>>(
    () =>
      task.input_args && Object.keys(task.input_args).length > 0
        ? { ...task.input_args }
        : {},
  )

  // Fetch config schema once
  const { data: schemaResponse, isLoading: schemaLoading } = useQuery({
    queryKey: ["configSchema", task.id],
    queryFn: () => Llm4AdTasksService.getConfigSchema({ taskId: task.id }),
    enabled: !!task.id,
  })

  const rootSchema = schemaResponse?.config_schema as JsonSchema | undefined

  // Initialize defaults from schema when it first loads
  useEffect(() => {
    if (!rootSchema) return
    setConfigValues((prev) => {
      const merged = mergeSchemaDefaults(rootSchema, rootSchema, prev)
      if (!merged || typeof merged !== "object") return prev
      const next = merged as Record<string, unknown>
      return JSON.stringify(prev) === JSON.stringify(next) ? prev : next
    })
  }, [rootSchema])

  // Sync from task.input_args when it updates externally
  useEffect(() => {
    if (task.input_args && Object.keys(task.input_args).length > 0) {
      const merged = rootSchema
        ? (mergeSchemaDefaults(
            rootSchema,
            rootSchema,
            task.input_args,
          ) as Record<string, unknown>)
        : { ...task.input_args }
      setConfigValues((prev) => {
        if (JSON.stringify(prev) !== JSON.stringify(merged)) {
          return merged
        }
        return prev
      })
    }
  }, [task.input_args, rootSchema])

  // Auto-save config on step change via updateTask
  const saveConfig = useCallback(async () => {
    if (Object.keys(configValues).length === 0) return
    try {
      await Llm4AdTasksService.updateTask({
        taskId: task.id,
        requestBody: { input_args: configValues },
      })
      queryClient.invalidateQueries({ queryKey: ["getTask", task.id] })
    } catch {
      // silent save
    }
  }, [task.id, configValues, queryClient])

  const handleSave = useCallback(async () => {
    setIsSaving(true)
    try {
      await saveConfig()
      toast.success(t("evolution.saveConfigSuccess"))
    } catch {
      // saveConfig already handles errors silently
    } finally {
      setIsSaving(false)
    }
  }, [saveConfig, t])

  const goToStep = useCallback(
    (step: number) => {
      if (!readOnly) saveConfig()
      setCurrentStep(step)
    },
    [saveConfig, readOnly],
  )

  const goBack = useCallback(
    () => goToStep(currentStep - 1),
    [currentStep, goToStep],
  )

  /** Get schema slice for a step property key from config_schema.properties */
  const getSchemaSlice = (key: string): JsonSchema | null => {
    if (!rootSchema?.properties) return null
    return rootSchema.properties[key] ?? null
  }

  /** Build schema for advanced step (step 6) */
  const getAdvancedSchema = (): JsonSchema => {
    if (!rootSchema?.properties) return { type: "object", properties: {} }
    const properties: Record<string, JsonSchema> = {}
    for (const key of ADVANCED_KEYS) {
      if (rootSchema.properties[key]) {
        properties[key] = rootSchema.properties[key]
      }
    }
    return { type: "object", properties }
  }

  const handleSchemaChange = (key: string, value: unknown) => {
    setConfigValues((prev) => ({ ...prev, [key]: value }))
  }

  const handleAdvancedChange = (value: Record<string, unknown>) => {
    setConfigValues((prev) => ({ ...prev, ...value }))
  }

  const renderStep = () => {
    // Step 1: Data preparation
    if (currentStep === 0) {
      return (
        <DataPreparationStep
          taskId={task.id}
          onNext={() => goToStep(1)}
          readOnly={readOnly}
        />
      )
    }

    // Step 2: EVOLVE Block Annotation
    if (currentStep === 1) {
      return (
        <EvolveAnnotationStep
          taskId={task.id}
          onBack={goBack}
          onNext={() => goToStep(2)}
          readOnly={readOnly}
        />
      )
    }

    // Step 8: Confirmation
    if (currentStep === 7) {
      return (
        <ConfirmStep
          taskId={task.id}
          task={task}
          configValues={configValues}
          onChange={setConfigValues}
          onBack={goBack}
          readOnly={readOnly}
        />
      )
    }

    // Step 6: Memory
    if (currentStep === 5) {
      return (
        <MemoryConfigStep
          projectId={task.project_id}
          value={configValues.memory}
          onChange={(v) => handleSchemaChange("memory", v)}
          onBack={goBack}
          onNext={() => goToStep(6)}
          readOnly={readOnly}
        />
      )
    }

    // Steps 3-5: Schema-driven forms
    const schemaKey = STEP_SCHEMA_KEYS[currentStep]
    if (schemaKey && rootSchema) {
      const slice = getSchemaSlice(schemaKey)
      if (!slice) {
        return (
          <div className="flex items-center justify-center py-16">
            <p className="text-muted-foreground">
              {t("evolution.schemaNotFound", { key: schemaKey })}
            </p>
          </div>
        )
      }
      return (
        <SchemaStepForm
          key={schemaKey}
          schemaKey={schemaKey}
          schema={slice}
          root={rootSchema}
          value={configValues[schemaKey]}
          onChange={(v) => handleSchemaChange(schemaKey, v)}
          memoryEnabled={isMemoryEnabled(
            configValues.memory as Record<string, unknown> | undefined,
          )}
          title={steps[currentStep].label}
          description={
            STEP_DESCRIPTIONS_KEYS[currentStep]
              ? t(
                  `evolution.stepDescriptions.${STEP_DESCRIPTIONS_KEYS[currentStep]}`,
                )
              : undefined
          }
          onBack={goBack}
          onNext={() => goToStep(currentStep + 1)}
          headerContent={
            schemaKey === "evolution" ? (
              <EvolutionProviderSelect
                plannerProvider={
                  ((configValues.planner as Record<string, unknown>)
                    ?.provider as string) || ""
                }
                plannerModel={
                  ((configValues.planner as Record<string, unknown>)
                    ?.provider_model as string) || ""
                }
                coderProvider={
                  ((configValues.coder as Record<string, unknown>)
                    ?.provider as string) || ""
                }
                coderModel={
                  ((configValues.coder as Record<string, unknown>)
                    ?.provider_model as string) || ""
                }
                onPlannerChange={(provider, model) => {
                  const planner = {
                    ...((configValues.planner as Record<string, unknown>) ??
                      {}),
                    provider,
                    provider_model: model,
                  }
                  handleSchemaChange("planner", planner)
                }}
                onCoderChange={(provider, model) => {
                  const coder = {
                    ...((configValues.coder as Record<string, unknown>) ?? {}),
                    provider,
                    provider_model: model,
                  }
                  handleSchemaChange("coder", coder)
                }}
              />
            ) : schemaKey === "evaluator" ? (
              <EvaluatorProviderSelect
                evaluatorProvider={
                  ((configValues.evaluator as Record<string, unknown>)
                    ?.provider as string) || ""
                }
                evaluatorModel={
                  ((configValues.evaluator as Record<string, unknown>)
                    ?.provider_model as string) || ""
                }
                onEvaluatorChange={(provider, model) => {
                  const evaluator = {
                    ...((configValues.evaluator as Record<string, unknown>) ??
                      {}),
                    provider,
                    provider_model: model,
                  }
                  handleSchemaChange("evaluator", evaluator)
                }}
              />
            ) : undefined
          }
        />
      )
    }

    // Step 7: Advanced
    if (currentStep === 6 && rootSchema) {
      const advancedValues: Record<string, unknown> = {}
      for (const key of ADVANCED_KEYS) {
        if (configValues[key] !== undefined) {
          advancedValues[key] = configValues[key]
        }
      }
      return (
        <AdvancedStep
          schema={getAdvancedSchema()}
          root={rootSchema}
          value={advancedValues}
          onChange={handleAdvancedChange}
          onBack={goBack}
          onNext={() => goToStep(7)}
        />
      )
    }

    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <OnboardingTour
        tourId="evolution-task-config"
        startDelay={400}
        steps={[
          {
            selector: '[data-tour="task-stepper"]',
            title: t("tour.evolutionTaskConfig.stepperTitle"),
            content: t("tour.evolutionTaskConfig.stepperContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="task-stepper"]',
            title: t("tour.evolutionTaskConfig.submitTitle"),
            content: t("tour.evolutionTaskConfig.submitContent"),
            placement: "bottom",
          },
        ]}
      />
      {/* Header: mode indicator + actions */}
      <div className="shrink-0 flex items-center justify-between px-1 pb-2 border-b mb-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <span
            className="flex items-center justify-center size-7 rounded-lg shrink-0 shadow-[0_0_12px] shadow-cyan-500/25"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in srgb, #06b6d4 22%, transparent), color-mix(in srgb, #14b8a6 22%, transparent))",
              border: "1px solid color-mix(in srgb, #06b6d4 40%, transparent)",
            }}
          >
            {readOnly ? (
              <AlertTriangle className="size-3.5 text-amber-500" />
            ) : (
              <Settings2 className="size-3.5 text-cyan-500" />
            )}
          </span>
          <div className="flex flex-col min-w-0">
            <span
              className={`text-sm font-semibold leading-tight truncate ${readOnly ? "text-amber-600 dark:text-amber-400" : "text-foreground"}`}
            >
              {readOnly
                ? t("evolution.viewParamsTitle")
                : t("evolution.adjustParamsTitle")}
            </span>
            {!readOnly && (
              <span className="text-[11px] text-muted-foreground leading-tight truncate">
                {t("evolution.adjustParamsHint")}
              </span>
            )}
          </div>
          {readOnly && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/10 border border-amber-500/30 text-xs font-medium text-amber-600 dark:text-amber-400 shrink-0">
              <AlertTriangle className="size-3" />
              {t("evolution.readOnlyHint")}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {task.storage_usage && (
            <StorageUsageBadge storage={task.storage_usage} />
          )}
          {onSwitchToChat && (
            <div
              role="tablist"
              aria-label={t("evolution.chatTune.modeSwitchLabel")}
              className="inline-flex items-center gap-0.5 p-0.5 rounded-md border border-primary/40 bg-primary/5 shadow-sm"
            >
              <button
                type="button"
                role="tab"
                aria-selected="false"
                onClick={onSwitchToChat}
                className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
                title={t("evolution.chatTune.switchChatTitle")}
              >
                <Sparkles className="size-3.5" />
                <span>{t("evolution.chatTune.modeAi")}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected="true"
                className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium bg-primary text-primary-foreground shadow-sm"
              >
                <Layers className="size-3.5" />
                <span>{t("evolution.chatTune.modeManual")}</span>
              </button>
            </div>
          )}
          {!readOnly && currentStep > 1 && (
            <button
              type="button"
              disabled={isSaving}
              onClick={handleSave}
              className="p-1.5 rounded-md text-muted-foreground
                hover:text-primary hover:bg-primary/10 transition-colors
                disabled:opacity-50"
              title={t("evolution.saveConfig")}
            >
              {isSaving ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
            </button>
          )}
          {!(task.status === "uninitialized" && !readOnly) && (
            <button
              type="button"
              onClick={() => {
                if (readOnly) {
                  setIsViewingParams(false)
                } else {
                  setIsConfiguring(false)
                }
              }}
              className="p-1.5 rounded-md text-muted-foreground
                hover:text-foreground hover:bg-accent/60 transition-colors"
              title={t("evolution.exitConfig")}
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      </div>

      {/* Stepper */}
      <div className="shrink-0 pb-3" data-tour="task-stepper">
        <Stepper
          steps={steps}
          currentStep={currentStep}
          onStepClick={goToStep}
        />
      </div>

      {/* Step guidance */}
      <div
        className={`shrink-0 mb-3 flex items-center gap-2 rounded-md px-3 py-2 text-xs ${
          stepStatus === "warning"
            ? "bg-amber-50 border border-amber-200 text-amber-700 dark:bg-amber-950/20 dark:border-amber-800/40 dark:text-amber-300"
            : stepStatus === "success"
              ? "bg-emerald-50 border border-emerald-200 text-emerald-700 dark:bg-emerald-950/20 dark:border-emerald-800/40 dark:text-emerald-300"
              : "bg-muted/50 text-muted-foreground"
        }`}
      >
        {stepStatus === "warning" ? (
          <AlertTriangle className="size-3.5 shrink-0 text-amber-500" />
        ) : stepStatus === "success" ? (
          <CheckCircle2 className="size-3.5 shrink-0 text-emerald-500" />
        ) : (
          <Info className="size-3.5 shrink-0 text-muted-foreground/60" />
        )}
        <span className="truncate">{stepGuideText}</span>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {schemaLoading && currentStep > 1 ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-6 animate-spin text-muted-foreground mr-2" />
            <span className="text-muted-foreground">
              {t("evolution.loadingConfigTemplate")}
            </span>
          </div>
        ) : (
          renderStep()
        )}
      </div>
    </div>
  )
}
