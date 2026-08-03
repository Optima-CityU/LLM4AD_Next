import { Download, FileText, FlaskConical, Pencil, X } from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchMessageItem } from "@/client"
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
import { downloadResearchArtifact } from "@/hooks/useAutoResearch"

import ArtifactPreviewDialog from "./ArtifactPreviewDialog"
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

/**
 * 从门控 payload 解析出「排序去隐藏后的动作列表」与「是否可编辑产物」。
 *
 * 抽成纯函数导出：决策按钮现由统一工具栏（BottomComposer）渲染，本函数供其
 * 与 {@link GateHeader} 共用同一份动作口径，避免两处各算一遍。
 */
export function getGateActions(
  payload: { available_actions?: Array<string | { value: string; label?: string }> } | null,
): { actions: string[]; canEdit: boolean } {
  const raw = (payload?.available_actions ?? []).map((a) =>
    typeof a === "string" ? a : a.value,
  )
  const actions = raw
    .filter((v) => !HIDDEN.has(v))
    .sort((a, b) => {
      const ia = ACTION_ORDER.indexOf(a)
      const ib = ACTION_ORDER.indexOf(b)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  return { actions, canEdit: raw.includes("edit") }
}

/**
 * 门控决策按钮的意图配色。approve 是唯一主操作（主色实心）；其余
 * （reject/skip/inject）为中性次要按钮，避免与危险操作 abort（右上角 ✕）抢红色。
 */
export function gateActionClass(value: string): string {
  if (value === "approve")
    return "bg-primary text-primary-foreground border-primary hover:bg-primary/90 shadow-sm shadow-primary/20"
  return "bg-background/60 text-foreground/80 border-border/60 hover:bg-accent hover:border-primary/40 hover:text-foreground"
}

interface Props {
  message: ResearchMessageItem
  sessionId: string
  disabled?: boolean
  /** 上报点了哪个 action（abort，或编辑产物后确认走 approve）。 */
  onAction: (value: string) => void
}

/**
 * 门控头部：统一底部卡片的「头部槽」内容。自上而下展示
 * 阶段标签 + 结束运行（✕）→ 产物文件（预览/下载/编辑）→ 操作说明/回退提示。
 *
 * 相较旧实现，本组件不再自带模式开关、模型选择器与居中决策按钮——这些已上移到
 * BottomComposer 的固定工具栏，保证三态控件槽位一致。理由输入与决策按钮均由父层
 * 承载，本组件只负责「这一轮门控要看什么、能编辑什么」。
 */
export default function GateHeader({
  message,
  sessionId,
  disabled,
  onAction,
}: Props) {
  const { t, i18n } = useTranslation()
  const payload = (message.payload ?? {}) as GateFormPayload

  const [preview, setPreview] = useState<string | null>(null)

  const { canEdit } = useMemo(() => getGateActions(payload), [payload])
  const hasAbort = useMemo(
    () => getGateActions(payload).actions.includes("abort"),
    [payload],
  )

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
    <div className="px-3.5 pt-2.5 pb-1.5">
      {/* 标题行：阶段 + 结束运行（✕）。琥珀色只保留在此作为唯一状态信号。 */}
      <div className="flex items-center gap-2">
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

        {hasAbort && (
          <div className="ml-auto">
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
          </div>
        )}
      </div>

      {/* 产物文件：预览/下载/编辑 */}
      {outputFiles.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
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
                <span className="truncate max-w-40">{f}</span>
              </button>
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

      {/* 操作说明 + 回退提示：安静的行内 muted 文案，不与产物/按钮抢注意力。 */}
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {t("autoResearch.gate.confirmHint")}
        {payload.rollback_default != null && (
          <span className="text-muted-foreground/80">
            {" · "}
            {t("autoResearch.gate.rollbackHint", {
              stage: payload.rollback_default,
              name:
                stageNameByLang(payload.rollback_default, i18n.language) ||
                payload.rollback_default_name ||
                "",
            })}
          </span>
        )}
      </p>

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
