import {
  Check,
  CircleDot,
  Clock,
  FileText,
  Loader2,
  MessageSquarePlus,
  X,
} from "lucide-react"
import { type ReactNode, useCallback, useMemo, useState } from "react"
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
  downloadResearchArtifact,
  useInjectStageGuidance,
  useResearchArtifactTree,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import ArtifactPreviewDialog, {
  FileKindIcon,
  formatSize,
  type PreviewFile,
} from "./ArtifactPreviewDialog"
import type { StageCell, StageStatus } from "./tech"

interface Props {
  sessionId: string
  /** 选中的阶段（null = 关闭抽屉）。 */
  cell: StageCell | null
  /** 后端阶段快照数组，用于取该阶段的真实时间 / 错误信息。 */
  stages: ResearchStageSnapshot[]
  onClose: () => void
}

/**
 * 阶段详情抽屉（Plan B）：点击顶部流水线某一步 → 右侧滑出，就地查看该阶段的
 * 状态 / 耗时 / 产物，并注入引导——把"横向看流程、侧边看细节"分工开来，避免
 * 顶部进度带越展开越高、挤压对话区。
 *
 * 三段式：① 状态与耗时；② 本阶段产物（从产物树里定位 `stage-NN` 目录，列出
 * 文件，点名预览、点图标下载）；③ 注入引导（复用 `useInjectStageGuidance`）。
 */
export default function StageDetailDrawer({
  sessionId,
  cell,
  stages,
  onClose,
}: Props) {
  const { t } = useTranslation()
  const snapshot = useMemo(
    () => (cell ? (stages.find((s) => s.stage === cell.stage) ?? null) : null),
    [cell, stages],
  )

  const treeQ = useResearchArtifactTree(cell ? sessionId : null)
  const stageFiles = useMemo(
    () => (cell ? collectStageFiles(treeQ.data?.root ?? null, cell.stage) : []),
    [treeQ.data, cell],
  )
  const [preview, setPreview] = useState<PreviewFile | null>(null)

  // 包装 setPreview：点击同一文件时先关闭再打开，强制刷新
  const handlePreview = useCallback((file: PreviewFile) => {
    setPreview((prev) => {
      if (prev?.path === file.path) {
        setTimeout(() => setPreview(file), 0)
        return null
      }
      return file
    })
  }, [])

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

              <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-4">
                {/* ① 阶段描述 */}
                {cell && (
                  <Section title={t("common.description", "描述")}>
                    <p className="text-xs leading-relaxed text-foreground/80">
                      {t(`autoResearch.stages.descriptions.${cell.stage}`, "")}
                    </p>
                  </Section>
                )}

                {/* ② 时间 / 错误 */}
                <Timing snapshot={snapshot} />

                {/* ② 本阶段产物 */}
                <Section title={t("autoResearch.form.outputFiles")}>
                  {treeQ.isLoading ? (
                    <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
                      <Loader2 className="size-3.5 animate-spin" />
                      {t("autoResearch.artifacts.loading")}
                    </div>
                  ) : stageFiles.length === 0 ? (
                    <p className="py-1.5 text-[11px] text-muted-foreground/60">
                      {t("autoResearch.stages.noArtifacts")}
                    </p>
                  ) : (
                    <ul className="space-y-0.5">
                      {stageFiles.map((f) => (
                        <li
                          key={f.path}
                          className="group flex items-center gap-1.5 rounded px-1 py-1 text-xs hover:bg-primary/[0.06]"
                        >
                          <button
                            type="button"
                            onClick={() =>
                              handlePreview({
                                path: f.path,
                                name: f.name,
                                size: f.size ?? null,
                              })
                            }
                            className="flex-1 min-w-0 flex items-center gap-1.5 text-left"
                          >
                            <FileKindIcon name={f.name} />
                            <span
                              className="flex-1 truncate text-foreground/80"
                              title={f.path}
                            >
                              {f.name}
                            </span>
                            {f.size != null && (
                              <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/60">
                                {formatSize(f.size)}
                              </span>
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              void downloadResearchArtifact(
                                sessionId,
                                f.path,
                              ).catch((err: unknown) =>
                                toast.error(
                                  (err as Error)?.message ?? "download failed",
                                ),
                              )
                            }
                            title={t("autoResearch.artifacts.download")}
                            className="shrink-0 text-muted-foreground/50 opacity-0 transition-opacity hover:text-primary group-hover:opacity-100"
                          >
                            <FileText className="size-3" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>

                {/* ③ 注入引导 */}
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
            </>
          )}
        </SheetContent>
      </Sheet>

      <ArtifactPreviewDialog
        sessionId={sessionId}
        file={preview}
        fileList={stageFiles.length > 1 ? stageFiles : undefined}
        onFileChange={handlePreview}
        onClose={() => setPreview(null)}
      />
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
  children,
}: {
  title: string
  icon?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        {icon}
        <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground/80">
          {title}
        </span>
      </div>
      {children}
    </div>
  )
}

/** 递归收集某阶段目录（`stage-NN`）下的全部文件（扁平）。 */
function collectStageFiles(
  root: ResearchArtifactTreeNode | null,
  stage: number,
): PreviewFile[] {
  if (!root) return []
  const dirName = `stage-${String(stage).padStart(2, "0")}`
  const findDir = (
    node: ResearchArtifactTreeNode,
  ): ResearchArtifactTreeNode | null => {
    if (node.is_dir && node.name === dirName) return node
    for (const c of node.children ?? []) {
      const hit = findDir(c)
      if (hit) return hit
    }
    return null
  }
  const dir = findDir(root)
  if (!dir) return []
  const files: PreviewFile[] = []
  const walk = (node: ResearchArtifactTreeNode) => {
    for (const c of node.children ?? []) {
      if (c.is_dir) walk(c)
      else files.push({ path: c.path, name: c.name, size: c.size ?? null })
    }
  }
  walk(dir)
  return files
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
