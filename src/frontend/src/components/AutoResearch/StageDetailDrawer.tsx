import {
  Check,
  CircleDot,
  Clock,
  Code,
  Loader2,
  MessageSquarePlus,
  X,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchArtifactTreeNode, ResearchStageSnapshot } from "@/client"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  useInjectStageGuidance,
  useResearchArtifactTree,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"
import ArtifactPreviewDialog from "./ArtifactPreviewDialog"
import {
  ArtifactIconButton,
  ArtifactTree,
  ArtifactTreeControls,
  useArtifactTreeExpansion,
} from "./ArtifactTree"
import IdeDialog from "./IdeDialog"
import type { StageCell, StageStatus } from "./tech"

interface Props {
  sessionId: string
  /** 选中的阶段（null = 关闭抽屉）。 */
  cell: StageCell | null
  /** 后端阶段快照数组，用于取该阶段的真实时间 / 错误信息。 */
  stages: ResearchStageSnapshot[]
  /** ml_vision 画像下 9-13/15 步骤改用不含 LLM4AD 文字的 ARC 原生描述。 */
  hideLlm4ad?: boolean
  onClose: () => void
}

/**
 * 阶段详情抽屉（Plan B）：点击顶部流水线某一步 → 右侧滑出，就地查看该阶段的
 * 状态 / 耗时 / 产物，并注入引导——把"横向看流程、侧边看细节"分工开来，避免
 * 顶部进度带越展开越高、挤压对话区。
 *
 * 三段式：① 状态与耗时；② 本阶段产物（从产物树里定位 `stage-NN` 目录，以与右侧
 * 产物面板同款的树展示，带展开/折叠/刷新/打开 IDE，点名预览、悬停看全路径与
 * mtime、点图标下载）；③ 注入引导（复用 `useInjectStageGuidance`）。
 */
export default function StageDetailDrawer({
  sessionId,
  cell,
  stages,
  hideLlm4ad,
  onClose,
}: Props) {
  const { t } = useTranslation()
  const snapshot = useMemo(
    () => (cell ? (stages.find((s) => s.stage === cell.stage) ?? null) : null),
    [cell, stages],
  )

  const treeQ = useResearchArtifactTree(cell ? sessionId : null)
  // 本阶段子树（`stage-NN` 目录节点）：抽屉只展示该阶段的产物，与右侧面板的
  // 整棵树不同源但同款渲染（共用 ArtifactTree）。
  const stageRoot = useMemo(
    () => (cell ? findStageDir(treeQ.data?.root ?? null, cell.stage) : null),
    [treeQ.data, cell],
  )
  // 树形态下默认展开第一层，切换阶段时按阶段号重新展开。
  const tree = useArtifactTreeExpansion(
    stageRoot,
    cell ? String(cell.stage) : null,
  )
  // 产物预览弹框：保存目标文件路径，弹框内部据此拉树 + 定位 + 预览。
  const [previewPath, setPreviewPath] = useState<string | null>(null)
  // IDE 弹层：与右侧产物面板同一入口，直接打开本会话工作区。
  const [ideOpen, setIdeOpen] = useState(false)

  const [text, setText] = useState("")
  const [noteError, setNoteError] = useState(false)
  const injectMut = useInjectStageGuidance()

  const submitGuidance = async () => {
    if (!cell) return
    const msg = text.trim()
    if (!msg) {
      setNoteError(true)
      return
    }
    try {
      await injectMut.mutateAsync({
        sessionId,
        stageNum: cell.stage,
        body: { message: msg },
      })
      toast.success(t("autoResearch.stages.guidanceSaved"))
      setText("")
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err as Error)?.message ??
        "error"
      toast.error(detail)
    }
  }

  const open = !!cell
  const status = (cell?.status ?? "pending") as StageStatus
  const v = statusVisual(status)

  return (
    <>
      <Sheet
        open={open}
        onOpenChange={(o) => {
          if (!o) {
            setText("")
            setNoteError(false)
            onClose()
          }
        }}
      >
        <SheetContent
          side="right"
          className="w-[380px] sm:max-w-[380px] gap-0 p-0"
        >
          {cell && (
            <>
              <SheetHeader className="gap-1.5 border-b border-border/50 px-4 pt-4 pb-3">
                <div className="flex items-center gap-2 pr-8">
                  <span
                    className={cn(
                      "grid size-6 shrink-0 place-items-center rounded-full",
                      v.badge,
                    )}
                  >
                    <v.Icon
                      className={cn("size-3.5", v.spin && "animate-spin")}
                    />
                  </span>
                  <SheetTitle className="flex-1 min-w-0 truncate text-sm">
                    <span className="font-mono text-muted-foreground/70 mr-1">
                      #{cell.stage}
                    </span>
                    {cell.name}
                  </SheetTitle>
                  <span
                    className={cn(
                      "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                      v.pill,
                    )}
                  >
                    {t(`autoResearch.stageStatus.${status}`, status)}
                  </span>
                </div>
                <SheetDescription className="sr-only">
                  {t("autoResearch.stages.detailTitle")}
                </SheetDescription>
              </SheetHeader>

              {/* 三段固定骨架：头部信息 → 只让产物树滚 → 引导区钉在底部。
                  滚动条只出现在产物树：引导的 textarea + 保存按钮常驻可见，不再
                  被长产物列表挤出视口；产物区吃掉中间剩余高度，树越长它越省空间。 */}
              <div className="flex flex-col flex-1 min-h-0">
                {/* ① 阶段描述 / ② 时间、错误：定高，不参与滚动 */}
                <div className="shrink-0 px-4 pt-3 pb-3 space-y-4 border-b border-border/40">
                  {cell && (
                    <Section title={t("common.description", "描述")}>
                      <p className="text-xs leading-relaxed text-foreground/80">
                        {(hideLlm4ad &&
                          t(
                            `autoResearch.stages.descriptionsMlVision.${cell.stage}`,
                            "",
                          )) ||
                          t(
                            `autoResearch.stages.descriptions.${cell.stage}`,
                            "",
                          )}
                      </p>
                    </Section>
                  )}

                  <Timing snapshot={snapshot} />
                </div>

                {/* ③ 本阶段产物：唯一滚动区，与右侧产物面板同款的树（图标 + 操作行
                    + 悬停卡片），只是作用域收敛到本阶段的 `stage-NN` 目录 */}
                <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
                  <Section
                    title={t("autoResearch.form.outputFiles")}
                    right={
                      <ArtifactTreeControls
                        canExpand={!!stageRoot?.children?.length}
                        onExpandAll={tree.expandAll}
                        onCollapseAll={tree.collapseAll}
                        onRefresh={() => void treeQ.refetch()}
                        refreshing={treeQ.isFetching}
                        extra={
                          <ArtifactIconButton
                            icon={Code}
                            title={t("autoResearch.mainTabs.ide")}
                            onClick={() => setIdeOpen(true)}
                          />
                        }
                      />
                    }
                  >
                    {treeQ.isLoading ? (
                      <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
                        <Loader2 className="size-3.5 animate-spin" />
                        {t("autoResearch.artifacts.loading")}
                      </div>
                    ) : !stageRoot?.children?.length ? (
                      <p className="py-1.5 text-[11px] text-muted-foreground/60">
                        {t("autoResearch.stages.noArtifacts")}
                      </p>
                    ) : (
                      <ArtifactTree
                        root={stageRoot}
                        sessionId={sessionId}
                        expanded={tree.expanded}
                        onToggle={tree.toggleDir}
                        onExpandDir={tree.expandDir}
                        onPreview={setPreviewPath}
                      />
                    )}
                  </Section>
                </div>

                {/* ④ 注入引导：钉在底部常驻，textarea 自身可拉伸但区块高度固定 */}
                <div className="shrink-0 border-t border-border/50 bg-card/30 px-4 py-3">
                  <Section
                    title={t("autoResearch.stages.injectGuidance")}
                    icon={
                      <MessageSquarePlus className="size-3.5 text-primary/70" />
                    }
                  >
                    <p className="mb-1.5 text-[10px] leading-snug text-muted-foreground/70">
                      {t("autoResearch.stages.guidanceHint")}
                    </p>
                    <textarea
                      value={text}
                      onChange={(e) => {
                        setText(e.target.value)
                        if (noteError) setNoteError(false)
                      }}
                      rows={4}
                      placeholder={t("autoResearch.stages.guidancePlaceholder")}
                      className={cn(
                        "w-full resize-none rounded-md border bg-background/60 px-2.5 py-2 text-xs transition-colors focus:outline-none focus:ring-1",
                        noteError
                          ? "border-destructive focus:ring-destructive/30"
                          : "border-border/60 focus:border-primary/50 focus:ring-primary/30",
                      )}
                    />
                    <div className="mt-2 flex justify-end">
                      <button
                        type="button"
                        onClick={() => void submitGuidance()}
                        disabled={injectMut.isPending}
                        className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/15 px-3 py-1.5 text-xs font-medium text-primary shadow-[0_0_10px] shadow-primary/20 transition-all hover:bg-primary/25 disabled:opacity-60"
                      >
                        {injectMut.isPending ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <MessageSquarePlus className="size-3.5" />
                        )}
                        {t("autoResearch.stages.saveGuidance")}
                      </button>
                    </div>
                  </Section>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <ArtifactPreviewDialog
        sessionId={sessionId}
        path={previewPath}
        onClose={() => setPreviewPath(null)}
      />

      {/* IDE 弹层（按需挂载，关闭即卸载，加载态随之复位） */}
      {ideOpen && (
        <IdeDialog
          sessionId={sessionId}
          open={ideOpen}
          onOpenChange={setIdeOpen}
        />
      )}
    </>
  )
}

/** 阶段耗时 / 错误信息块。 */
function Timing({ snapshot }: { snapshot: ResearchStageSnapshot | null }) {
  const { t } = useTranslation()
  const started = snapshot?.started_at
    ? new Date(snapshot.started_at).toLocaleString()
    : null
  const ended = snapshot?.ended_at
    ? new Date(snapshot.ended_at).toLocaleString()
    : null

  return (
    <div className="rounded-md border border-border/50 bg-card/40 px-3 py-2 space-y-1 text-[11px]">
      <Row
        icon={<Clock className="size-3 text-muted-foreground/60" />}
        label={t("autoResearch.stages.startedAt")}
        value={started ?? "—"}
      />
      <Row
        icon={<Clock className="size-3 text-muted-foreground/60" />}
        label={t("autoResearch.stages.endedAt")}
        value={ended ?? "—"}
      />
      {snapshot?.error && (
        <p className="mt-1 rounded bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
          {snapshot.error}
        </p>
      )}
    </div>
  )
}

function Row({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string
}) {
  return (
    <div className="flex items-center gap-1.5">
      {icon}
      <span className="text-muted-foreground/70">{label}</span>
      <span
        className="ml-auto truncate font-mono text-foreground/80"
        title={value}
      >
        {value}
      </span>
    </div>
  )
}

function Section({
  title,
  icon,
  right,
  children,
}: {
  title: string
  icon?: ReactNode
  /** 标题行右侧的动作区（如产物树的展开/折叠/刷新/IDE）。 */
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        {icon}
        <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground/80">
          {title}
        </span>
        {right && <div className="ml-auto flex items-center">{right}</div>}
      </div>
      {children}
    </div>
  )
}

/**
 * 在产物树里定位某阶段的目录节点（`stage-NN`）。阶段目录固定在 run_dir 直下，
 * 但仍递归查找以兼容未来可能的中间层级。
 *
 * @returns 命中返回该目录节点，未命中返回 null。
 */
function findStageDir(
  root: ResearchArtifactTreeNode | null,
  stage: number,
): ResearchArtifactTreeNode | null {
  if (!root) return null
  const dirName = `stage-${String(stage).padStart(2, "0")}`
  const walk = (
    node: ResearchArtifactTreeNode,
  ): ResearchArtifactTreeNode | null => {
    if (node.is_dir && node.name === dirName) return node
    for (const c of node.children ?? []) {
      const hit = walk(c)
      if (hit) return hit
    }
    return null
  }
  return walk(root)
}

/** 阶段状态的图标 + 徽章 + 胶囊配色（与消息胶囊 / 顶部进度轨一致）。 */
function statusVisual(status: StageStatus): {
  Icon: typeof Check
  spin: boolean
  badge: string
  pill: string
} {
  switch (status) {
    case "done":
      return {
        Icon: Check,
        spin: false,
        badge: "bg-emerald-500/15 text-emerald-500",
        pill: "border-emerald-500/30 bg-emerald-500/10 text-emerald-500",
      }
    case "running":
      return {
        Icon: Loader2,
        spin: true,
        badge: "bg-primary/15 text-primary",
        pill: "border-primary/30 bg-primary/10 text-primary",
      }
    case "waiting":
      return {
        Icon: CircleDot,
        spin: false,
        badge: "bg-amber-500/15 text-amber-500",
        pill: "border-amber-500/30 bg-amber-500/10 text-amber-500",
      }
    case "failed":
      return {
        Icon: X,
        spin: false,
        badge: "bg-red-500/15 text-red-500",
        pill: "border-red-500/30 bg-red-500/10 text-red-500",
      }
    default:
      return {
        Icon: CircleDot,
        spin: false,
        badge: "bg-muted/50 text-muted-foreground",
        pill: "border-border/50 bg-muted/40 text-muted-foreground",
      }
  }
}
