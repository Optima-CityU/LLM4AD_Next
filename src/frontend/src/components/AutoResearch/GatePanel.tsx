import { ChevronDown, Cpu, Download, FileText, FlaskConical, Pencil, X } from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchMessageItem, ResearchMode } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { downloadResearchArtifact } from "@/hooks/useAutoResearch"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { cn } from "@/lib/utils"

import ArtifactPreviewDialog from "./ArtifactPreviewDialog"
import ProviderModelPicker from "./ProviderModelPicker"
import { MODE_OPTIONS } from "./shared"
import { stageNameByLang } from "./tech"

interface GateFormPayload {
  kind?: string
  prompt?: string
  reason?: string
  context_summary?: string
  output_files?: string[]
  stage?: number
  stage_name?: string
  available_actions?: Array<string | { value: string; label?: string }>
  rollback_default?: number | null
  rollback_default_name?: string
}

const ACTION_ORDER = ["approve", "reject", "skip", "inject", "abort"]
const HIDDEN = new Set(["collaborate", "take_over", "resume", "edit"])

/** 目录产物一般以 ``/`` 结尾（下载/编辑端点均不支持，仅可提示）。 */
const isFolderEntry = (name: string) => name.endsWith("/")

function intentClass(value: string): string {
  // approve 是唯一主操作：主色实心。其余（reject/skip 等）为中性次要按钮，
  // 避免与真正的危险操作 abort（右侧文字链）抢用红色，保证主次清晰。
  if (value === "approve")
    return "bg-primary text-primary-foreground border-primary hover:bg-primary/90 shadow-sm shadow-primary/20"
  return "bg-background/60 text-foreground/80 border-border/60 hover:bg-accent hover:border-primary/40 hover:text-foreground"
}

interface Props {
  message: ResearchMessageItem
  sessionId: string
  disabled?: boolean
  mode: ResearchMode
  onModeChange: (mode: ResearchMode) => void
  onAction: (value: string) => void
  /** 输入框插槽：渲染在「上下文/产物」与「决策按钮」之间——先写理由，再点决策。 */
  children?: ReactNode
  /** 模型配置 */
  provider: string
  model: string
  onProviderModelChange: (provider: string, model: string) => void
}

/**
 * 门控操作条：贴在底部输入框上方。命中门控时自上而下展示
 * 阶段上下文/产物 → 模式选择 → 输入框（children）→ 决策按钮
 * （approve / reject / edit / abort …）。理由由输入框统一提供，
 * 本组件只上报点了哪个 action，submission 的组装/校验交给父组件。
 */
export default function GatePanel({
  message,
  sessionId,
  disabled,
  mode,
  onModeChange,
  onAction,
  children,
  provider,
  model,
  onProviderModelChange,
}: Props) {
  const { t, i18n } = useTranslation()
  const payload = (message.payload ?? {}) as GateFormPayload

  const [preview, setPreview] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 模型选择器数据
  const { data: providersData } = useProviders()
  const { data: defaultModels } = useUserDefaultModels()

  const providerList = providersData?.items ?? []
  const selectedProvider = providerList.find((p) => p.id === provider)
  const defaultProviderName = defaultModels?.planner_provider_name || t("autoResearch.provider.default")
  const defaultModelName = defaultModels?.planner_model_name || ""

  const providerLabel = (() => {
    if (!provider || provider === "default") {
      const label = defaultProviderName
      return defaultModelName ? `${label} / ${defaultModelName}` : label
    }
    if (provider === "mock") {
      return t("autoResearch.provider.mock")
    }
    const label = selectedProvider?.name || provider
    return model ? `${label} / ${model}` : label
  })()

  // 一次 memo 算出 actions（排序、去隐藏）与 canEdit，键在 payload.available_actions
  // 上而非每次 render 新建的中间数组，避免无谓重算。
  const { actions, canEdit } = useMemo(() => {
    const raw = (payload.available_actions ?? []).map((a) =>
      typeof a === "string" ? a : a.value,
    )
    const vals = raw
      .filter((v) => !HIDDEN.has(v))
      .sort((a, b) => {
        const ia = ACTION_ORDER.indexOf(a)
        const ib = ACTION_ORDER.indexOf(b)
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
      })
    return { actions: vals, canEdit: raw.includes("edit") }
  }, [payload.available_actions])

  const stageDir =
    payload.stage != null
      ? `stage-${String(payload.stage).padStart(2, "0")}`
      : null

  const stage = payload.stage ?? message.stage ?? null
  const outputFiles = payload.output_files ?? []
  const editableFiles = useMemo(
    () => outputFiles.filter((f) => !isFolderEntry(f)),
    [outputFiles],
  )
  const canEditFiles = canEdit && !!stageDir && editableFiles.length > 0
  const editablePaths = useMemo(
    () =>
      stageDir ? editableFiles.map((file) => `${stageDir}/${file}`) : undefined,
    [editableFiles, stageDir],
  )

  const downloadFile = async (name: string) => {
    if (!stageDir) return
    try {
      await downloadResearchArtifact(sessionId, `${stageDir}/${name}`)
    } catch {
      toast.error(t("autoResearch.form.downloadFailed"))
    }
  }

  const openEdit = (name?: string) => {
    if (!stageDir) return
    setPreview(`${stageDir}/${name ?? editableFiles[0] ?? ""}`)
  }

  const openFile = (name: string) => {
    if (!stageDir) return
    setPreview(`${stageDir}/${name}`)
  }

  return (
    <div className="bg-amber-500/[0.06]">
      <div className="flex items-center gap-2 px-3.5 pt-2.5 pb-1.5">
        <FlaskConical className="size-3.5 shrink-0 text-amber-500" />
        <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">
          {stage != null
            ? t("autoResearch.gate.awaitingStage", {
                stage,
                name:
                  stageNameByLang(stage, i18n.language) ||
                  payload.stage_name ||
                  "",
              })
            : t("autoResearch.gate.awaiting")}
        </span>

        {/* 右侧：仅「结束运行」（X），放最右上角符合「关闭弹框」习惯。
            日志入口随输入框，模式开关在底部决策行左侧。 */}
        <div className="ml-auto flex items-center gap-1.5">
          {actions.includes("abort") && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <button
                  type="button"
                  disabled={disabled}
                  title={t("autoResearch.form.actions.abort", "abort")}
                  className="grid size-6 place-items-center rounded-md text-muted-foreground/70 hover:text-destructive hover:bg-destructive/10 disabled:opacity-60 transition-colors"
                >
                  <X className="size-3.5" />
                </button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    {t("autoResearch.gate.abortTitle", "结束本次运行？")}
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    {t("autoResearch.gate.abortDesc")}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                  <AlertDialogAction
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                    onClick={() => onAction("abort")}
                  >
                    {t("autoResearch.form.actions.abort", "abort")}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </div>

      <div className="px-3.5 pb-2 space-y-2">
        {/* 产物文件列表 */}
        {outputFiles.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
              {t("autoResearch.form.outputFiles")}
            </span>
            {outputFiles.map((f) => (
              <div
                key={f}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-border/60 bg-background/50 text-[11px] text-foreground"
              >
                <button
                  type="button"
                  disabled={!stageDir}
                  onClick={() => openFile(f)}
                  title={
                    canEditFiles
                      ? t("autoResearch.form.editFile")
                      : t("autoResearch.form.previewFile")
                  }
                  className="inline-flex items-center gap-1 min-w-0 hover:text-primary transition-colors"
                >
                  <FileText className="size-3 shrink-0" />
                  <span className="truncate max-w-[160px]">{f}</span>
                </button>
                {/* 目录无下载端点，隐藏下载按钮避免 404。 */}
                {!isFolderEntry(f) && (
                  <button
                    type="button"
                    onClick={() => downloadFile(f)}
                    className="shrink-0 text-muted-foreground/60 hover:text-primary transition-colors"
                  >
                    <Download className="size-2.5" />
                  </button>
                )}
              </div>
            ))}
            {canEditFiles && (
              <button
                type="button"
                disabled={disabled}
                onClick={() => openEdit()}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-primary/40 bg-primary/10 text-primary text-[11px] font-medium hover:bg-primary/20 disabled:opacity-60 transition-colors"
              >
                <Pencil className="size-3" />
                {t("autoResearch.form.editFile")}
              </button>
            )}
          </div>
        )}

        {/* 操作说明：安静的行内提示（琥珀色仅保留在标题作为唯一状态信号，
            此处走 muted，避免二次琥珀框与产物/按钮抢注意力）。 */}
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("autoResearch.gate.confirmHint")}
        </p>

        {/* 打回回退点提示：告知用户「打回」将回到哪一步重做（对齐原生 arc
            GATE_ROLLBACK，回退目标由后端下发，不可修改）。 */}
        {payload.rollback_default != null && (
          <p className="text-[11px] leading-relaxed text-muted-foreground/80">
            {t("autoResearch.gate.rollbackHint", {
              stage: payload.rollback_default,
              name:
                stageNameByLang(payload.rollback_default, i18n.language) ||
                payload.rollback_default_name ||
                "",
            })}
          </p>
        )}
      </div>

      {/* 输入框插槽：先写理由 */}
      {children}

      {/* 决策行：左侧模式开关（全自动/协作两态分段），主决策按钮相对整行居中，
          右侧模型选择器（左右开关绝对定位，不占居中计算）；结束运行已上移为右上角 X。 */}
      <div className="relative flex items-center px-3.5 pt-2 pb-2.5">
        {/* 模式开关：绝对定位靠左 */}
        <div
          role="group"
          aria-label={t("autoResearch.create.modeLabel")}
          className="absolute left-3.5 top-1/2 -translate-y-1/2 inline-flex items-center rounded-lg border border-border/60 bg-background/50 p-0.5"
        >
          {MODE_OPTIONS.map((m) => (
            <button
              key={m}
              type="button"
              disabled={disabled}
              onClick={() => onModeChange(m)}
              className={cn(
                "px-2 py-1 rounded-md text-[11px] font-medium transition-colors disabled:opacity-60",
                mode === m
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(`autoResearch.mode.${m}`)}
            </button>
          ))}
        </div>

        {/* 决策按钮：整行居中 */}
        <div className="flex flex-1 flex-wrap items-center justify-center gap-2">
          {actions
            .filter((v) => v !== "abort")
            .map((v) => (
              <button
                key={v}
                type="button"
                disabled={disabled}
                onClick={() => onAction(v)}
                className={cn(
                  "px-4 py-2 rounded-lg text-sm font-medium border transition-all disabled:opacity-60",
                  intentClass(v),
                )}
              >
                {t(`autoResearch.form.actions.${v}`, v)}
              </button>
            ))}
        </div>

        {/* 模型选择器：绝对定位靠右 */}
        <div className="absolute right-3.5 top-1/2 -translate-y-1/2">
          <Popover open={settingsOpen} onOpenChange={setSettingsOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                disabled={disabled}
                className={cn(
                  "inline-flex h-6 items-center gap-1 px-1.5 rounded-md text-[11px] font-medium transition-colors disabled:opacity-60",
                  provider && provider !== "default"
                    ? "text-primary hover:bg-primary/10"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
                )}
              >
                <Cpu className="size-3 shrink-0" />
                <span className="max-w-[160px] truncate">{providerLabel}</span>
                <ChevronDown className="size-3 shrink-0 opacity-60" />
              </button>
            </PopoverTrigger>
            <PopoverContent
              className="w-[420px] p-3"
              align="end"
              side="top"
              sideOffset={8}
            >
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5 px-0.5">
                {t("autoResearch.create.providerLabel")} / Model
              </p>
              <ProviderModelPicker
                provider={provider}
                model={model}
                onChange={onProviderModelChange}
              />
            </PopoverContent>
          </Popover>
        </div>
      </div>

      <ArtifactPreviewDialog
        sessionId={sessionId}
        path={preview}
        onClose={() => setPreview(null)}
        readOnly={!canEditFiles}
        editablePaths={editablePaths}
        onConfirm={
          canEditFiles
            ? () => {
                setPreview(null)
                onAction("approve")
              }
            : undefined
        }
        confirmLabel={t("autoResearch.form.editConfirm")}
      />
    </div>
  )
}
