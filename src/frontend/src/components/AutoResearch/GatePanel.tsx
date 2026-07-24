import {
  ChevronDown,
  Download,
  FileText,
  FlaskConical,
  Pencil,
  Settings2,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchMessageItem, ResearchMode } from "@/client"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { downloadResearchArtifact } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import ArtifactPreviewDialog from "./ArtifactPreviewDialog"
import GateEditDialog from "./GateEditDialog"
import { MODE_OPTIONS } from "./shared"

interface GateFormPayload {
  kind?: string
  prompt?: string
  reason?: string
  context_summary?: string
  output_files?: string[]
  stage?: number
  stage_name?: string
  available_actions?: Array<string | { value: string; label?: string }>
}

const ACTION_ORDER = ["approve", "reject", "skip", "inject", "abort"]
const HIDDEN = new Set(["collaborate", "take_over", "resume", "edit"])

/** 目录产物一般以 ``/`` 结尾（下载/编辑端点均不支持，仅可提示）。 */
const isFolderEntry = (name: string) => name.endsWith("/")

function intentClass(value: string): string {
  if (value === "approve")
    return "bg-primary text-primary-foreground border-primary hover:bg-primary/90 shadow-[0_0_12px] shadow-primary/25"
  if (value === "reject" || value === "abort")
    return "bg-transparent text-destructive/90 border-destructive/30 hover:bg-destructive/10 hover:border-destructive/50 hover:text-destructive"
  return "bg-background/60 text-foreground/80 border-border/60 hover:bg-accent hover:border-primary/30"
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
  /** 标题行右上角插槽（如日志入口）。 */
  headerRight?: ReactNode
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
  headerRight,
}: Props) {
  const { t } = useTranslation()
  const payload = (message.payload ?? {}) as GateFormPayload

  const [detailOpen, setDetailOpen] = useState(true)
  const [preview, setPreview] = useState<string | null>(null)

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
  // 目录不可编辑，只有普通文件能进编辑弹框。
  const editableFiles = useMemo(
    () => outputFiles.filter((f) => !isFolderEntry(f)),
    [outputFiles],
  )
  // 编辑需要具体文件才有意义：无可编辑文件时不显示编辑入口（否则点了没弹窗）。
  const canEditFiles = canEdit && !!stageDir && editableFiles.length > 0

  const [editOpen, setEditOpen] = useState(false)
  // 打开编辑弹框时默认激活的文件（点具体文件 → 激活该文件；点「编辑产物」→ null 走第一个）。
  const [editFile, setEditFile] = useState<string | null>(null)
  const summary = payload.context_summary ?? ""

  const downloadFile = async (name: string) => {
    if (!stageDir) return
    try {
      await downloadResearchArtifact(sessionId, `${stageDir}/${name}`)
    } catch {
      toast.error(t("autoResearch.form.downloadFailed"))
    }
  }

  // 打开编辑弹框并激活指定文件（缺省从第一个开始）。
  const openEdit = (name?: string) => {
    setEditFile(name ?? null)
    setEditOpen(true)
  }

  // 点文件名：目录只提示（不可编辑）；普通文件可编辑时进编辑弹框，否则只读预览。
  const openFile = (name: string) => {
    if (!stageDir) return
    if (canEditFiles && !isFolderEntry(name)) {
      openEdit(name)
    } else {
      setPreview(`${stageDir}/${name}`)
    }
  }

  return (
    <div className="bg-amber-500/[0.06]">
      <div className="flex items-center gap-2 px-3.5 pt-2.5 pb-1.5">
        <FlaskConical className="size-3.5 shrink-0 text-amber-500" />
        <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">
          {stage != null
            ? t("autoResearch.gate.awaitingStage", {
                stage,
                name: payload.stage_name ?? "",
              })
            : t("autoResearch.gate.awaiting")}
        </span>

        {/* 运行模式选择器 - 移到标题后面 */}
        <Select
          value={mode}
          onValueChange={(v) => onModeChange(v as ResearchMode)}
        >
          <SelectTrigger
            size="sm"
            aria-label={t("autoResearch.create.modeLabel")}
            className="h-6 w-auto gap-1.5 rounded-md border-border/60 bg-background/50 px-2 py-0 text-[11px] font-medium text-foreground/80 shadow-sm [&>svg:last-child]:size-3"
          >
            <Settings2 className="size-3 shrink-0" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MODE_OPTIONS.map((m) => (
              <SelectItem key={m} value={m} className="text-xs">
                {t(`autoResearch.mode.${m}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="ml-auto flex items-center gap-1.5">
          {(summary || outputFiles.length > 0) && (
            <button
              type="button"
              onClick={() => setDetailOpen((o) => !o)}
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronDown
                className={cn(
                  "size-3 transition-transform",
                  detailOpen && "rotate-180",
                )}
              />
              {t("autoResearch.gate.details")}
            </button>
          )}
          {headerRight}
        </div>
      </div>

      {detailOpen && (
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
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-border/60 bg-background/50 text-[11px] text-foreground/80"
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
                  className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-primary/40 bg-primary/10 text-primary text-[11px] font-medium hover:bg-primary/20 disabled:opacity-60 transition-colors"
                >
                  <Pencil className="size-3" />
                  {t("autoResearch.form.editFile")}
                </button>
              )}
            </div>
          )}

          {/* 引导提示：告知用户如何操作 - 移到产物后面 */}
          <div className="rounded-md bg-amber-500/[0.08] border border-amber-500/30 px-3 py-2">
            <p className="text-[11px] leading-relaxed text-foreground/80">
              {t("autoResearch.gate.confirmHint")}
            </p>
          </div>
        </div>
      )}

      {/* 输入框插槽：先写理由 */}
      {children}

      {/* 决策按钮：主决策（通过/驳回…）相对整行居中；abort 绝对定位靠右，不占居中计算 */}
      <div className="relative flex items-center px-3.5 pt-2 pb-2.5">
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
        {actions.includes("abort") && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onAction("abort")}
            className="absolute right-3.5 top-1/2 -translate-y-1/2 shrink-0 px-2 py-1 rounded-md text-[11px] text-muted-foreground/70 hover:text-destructive hover:bg-destructive/10 disabled:opacity-60 transition-colors"
          >
            {t("autoResearch.form.actions.abort", "abort")}
          </button>
        )}
      </div>

      <ArtifactPreviewDialog
        sessionId={sessionId}
        path={preview}
        onClose={() => setPreview(null)}
      />

      {canEditFiles && stageDir && (
        <GateEditDialog
          open={editOpen}
          sessionId={sessionId}
          stageDir={stageDir}
          files={editableFiles}
          initialFile={editFile}
          onConfirm={() => {
            setEditOpen(false)
            onAction("approve")
          }}
          onClose={() => setEditOpen(false)}
        />
      )}
    </div>
  )
}
