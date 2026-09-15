import {
  ChevronDown,
  ChevronRight,
  Clock,
  Code,
  DownloadCloud,
  FileBarChart,
  FlaskConical,
  FolderTree,
  Info,
  Loader2,
  RefreshCw,
  ScrollText,
  Upload,
  X,
} from "lucide-react"
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchSessionItem } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  downloadResearchArtifactsArchive,
  useImportResearchArtifacts,
  useResearchArtifactTree,
  useResearchGenerated,
  useResearchState,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"
import AnalysisReport from "./AnalysisReport"
import ArtifactPreviewDialog from "./ArtifactPreviewDialog"
import {
  ArtifactIconButton,
  ArtifactLabelButton,
  ArtifactTree,
  ArtifactTreeControls,
  collectEditableFilePaths,
  collectFilePaths,
  useArtifactTreeExpansion,
} from "./ArtifactTree"
import ExperimentFullscreenDialog from "./ExperimentFullscreenDialog"
import ExperimentPanel from "./ExperimentPanel"
import IdeDialog from "./IdeDialog"
import ResearchLogDrawer, { type ResearchDrawerTab } from "./ResearchLogDrawer"
import { useExperimentAlgorithm } from "./useExperimentAlgorithm"
import { ML_VISION_PROFILE } from "./shared"
import { SectionLabel, StatusPill } from "./tech"

interface Props {
  session: ResearchSessionItem | null
  /**
   * @deprecated 右侧面板是否收起。产物操作按钮已固定在面板内的「产物」区，收起时
   * 不再向顶栏注入镜像按钮，故该字段当前不再参与渲染，保留仅为兼容调用方。
   */
  rightCollapsed?: boolean
}

/**
 * 右侧面板：三个区域——任务信息（常显）、实验（演化仿真 / 趋势分析，可收起）、
 * 产物（文件树，常显）。
 *
 * 无整体滚动条：面板本体不滚，每个区域内容各自纵向滚动（产物区占剩余空间并
 * 滚动，其余区域按内容高度、超出才滚）。产物区的操作按钮固定在操作行内常显，
 * 左侧为导入 / 导出，右侧为展开 / 折叠 / 刷新 / IDE，不再随折叠态在顶栏与面板
 * 之间跳动。产物树点文件名预览、点下载图标下载；每个目录可递归展开全部子孙。
 */
export default function ArtifactsPanel({ session, rightCollapsed }: Props) {
  const { t } = useTranslation()

  return (
    <aside className="h-full w-full flex flex-col bg-transparent overflow-hidden">
      {!session ? (
        <div className="flex-1 grid place-items-center p-6 text-xs text-muted-foreground/60 text-center">
          {t("autoResearch.artifacts.placeholder")}
        </div>
      ) : (
        <PanelInner session={session} rightCollapsed={rightCollapsed} />
      )}
    </aside>
  )
}

function PanelInner({
  session,
  rightCollapsed: _rightCollapsed,
}: {
  session: ResearchSessionItem
  rightCollapsed?: boolean
}) {
  const { t } = useTranslation()
  const state = useResearchState(session.id, {
    // SSE 实时推送，不需要轮询
    refetchInterval: false,
  })
  const treeQ = useResearchArtifactTree(session.id)
  const root = treeQ.data?.root ?? null
  // ml_vision 画像不接 LLM4AD 演化引擎：隐藏「实验」区，也不再拉 generated 数据。
  const hideExperiment = session.profile === ML_VISION_PROFILE
  // 与 ExperimentPanel 共享同一 query（按 key 去重，不产生额外请求），
  // 仅为把实验区刷新按钮提到区域标题行、保持与产物区一致。
  const genQ = useResearchGenerated(
    session.id,
    session.status === "running" || session.status === "paused",
    !hideExperiment,
  )
  // 实验区「当前算法」的**唯一真源**：右侧面板与全屏弹框（演化仿真 / 趋势分析）都从这里
  // 取，任一处切换其余跟着切。放在这里是因为这两个组件是同级的兄弟节点。
  const { selected: expAlgorithm, onSelect: handleExpAlgorithm } =
    useExperimentAlgorithm(
      session.id,
      session.status === "running" || session.status === "paused",
      !hideExperiment,
    )
  // 产物预览弹框：只保存目标文件路径，弹框内部据此拉树 + 定位 + 预览。
  const [previewPath, setPreviewPath] = useState<string | null>(null)
  // 右侧面板可编辑产物：与门控编辑同口径——凡产物树里出现的文件名均可就地编辑。
  // 后端 write_artifact 只拒绝 `.` 开头的内部点文件（树构建时已过滤），并把原文
  // 备份到 hitl/snapshots/；故树里能点到的文件（含 checkpoint.json、hitl/ 引导文件）
  // 都允许保存，与中间门控区点击产物后可编辑保持一致。
  const editablePaths = useMemo(() => collectEditableFilePaths(root), [root])

  // 产物树展开态（默认展开第一层、全部展开/收起）由共享 hook 持有，与阶段详情
  // 抽屉里的树行为一致；切换会话时按默认策略重新展开。
  const tree = useArtifactTreeExpansion(root, session.id)

  // 任务信息取最新态：state 查询比 session prop 更实时，缺失时回退 session。
  const status = state.data?.status ?? session.status

  // 运行中每秒 tick，驱动进行中会话的运行时长实时走秒（fmtDuration 无 end 时
  // 用 Date.now() 计算，不 tick 就会定格在首帧）。非进行中不启用，避免空转。
  const isRunning = status === "running" || status === "paused"
  const [, forceDurationTick] = useState(0)
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => forceDurationTick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [isRunning])

  // 报告分析 / IDE 弹层（点击弹出全屏；IDE 内嵌 code-server iframe）。
  const [tool, setTool] = useState<"report" | "ide" | null>(null)

  // 日志 / 运行历史底部抽屉（右侧面板唯一入口，按需查看）。
  const [logDrawerOpen, setLogDrawerOpen] = useState(false)
  const [logDrawerTab, setLogDrawerTab] = useState<ResearchDrawerTab>("logs")

  // 打包下载全部产物：进行中禁用按钮，失败 toast。
  const [zipping, setZipping] = useState(false)
  const handleDownloadAll = useCallback(() => {
    if (zipping) return
    setZipping(true)
    void downloadResearchArtifactsArchive(session.id)
      .catch((err: unknown) =>
        toast.error((err as Error)?.message ?? "download failed"),
      )
      .finally(() => setZipping(false))
  }, [session.id, zipping])

  // 导入产物 zip：解压覆盖到 run_dir。成功后失效产物树/列表/演化解并提示统计；
  // 部分条目失败只警告，不中断（后端单条目失败会记入 failed 并继续）。
  // 导入前先解析 zip 文件名列表，与会话现有产物比对：有同名冲突时先弹二次确认，
  // 避免用户对「覆盖已存在文件」无感知。
  const importMut = useImportResearchArtifacts()
  const fileInputRef = useRef<HTMLInputElement>(null)
  // 待确认的导入文件 + 预检出的同名覆盖数。null = 无待确认文件。
  const [pendingImport, setPendingImport] = useState<{
    file: File
    overwriteCount: number
  } | null>(null)
  // 解析 zip（读取本地文件）进行中：禁用导入按钮防止连点。
  const [analyzingZip, setAnalyzingZip] = useState(false)

  const handlePickArtifactZip = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  // 预检：从树里收集所有文件相对路径，用于判定「同名覆盖」。
  const existingPaths = useMemo(() => collectFilePaths(root), [root])

  // 真正发起导入：成功失效产物树/列表/演化解并提示统计；部分条目失败只警告。
  const doImport = useCallback(
    (file: File) => {
      importMut.mutate(
        { sessionId: session.id, file },
        {
          onSuccess: (res) => {
            toast.success(
              t("autoResearch.artifacts.importSuccess", {
                imported: res.imported ?? 0,
                overwritten: res.overwritten ?? 0,
              }),
            )
            if ((res.failed?.length ?? 0) > 0) {
              toast.warning(
                t("autoResearch.artifacts.importPartial", {
                  count: res.failed!.length,
                }),
              )
            }
          },
          onError: (err: unknown) => {
            const e2 = err as { body?: { detail?: string }; message?: string }
            toast.error(e2?.body?.detail || e2?.message || "import failed")
          },
        },
      )
    },
    [session.id, importMut, t],
  )

  const handleImportZip = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      e.target.value = "" // 允许再次选择同一文件
      if (!file) return
      setAnalyzingZip(true)
      try {
        // 解析 zip 内文件名（仅 central directory，不解压内容），与现有产物比对。
        const names = await readZipEntryNames(file)
        const overwriteCount = names.reduce((n, name) => {
          const p = name.replace(/\\/g, "/").replace(/^\.?\//, "")
          return n + (existingPaths.has(p) ? 1 : 0)
        }, 0)
        if (overwriteCount > 0) {
          setPendingImport({ file, overwriteCount })
          return
        }
      } catch {
        // 解析失败时放弃预检：直接走导入（由后端幂等处理），确认逻辑跳过。
      } finally {
        setAnalyzingZip(false)
      }
      doImport(file)
    },
    [doImport, existingPaths],
  )

  // 用户确认覆盖后真正导入。
  const confirmImport = useCallback(() => {
    if (!pendingImport) return
    const { file } = pendingImport
    setPendingImport(null)
    doImport(file)
  }, [pendingImport, doImport])

  // 面板收起时不再向顶栏注入任何操作按钮：产物相关的四枚按钮已固定在「产物」区
  // 的操作行（面板收起时随面板一起隐藏，展开即恢复），不再需要在顶栏留一份镜像。

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 任务信息（面板顶部，常显不可折叠）：主题 + 状态 + 运行进度。
          不再包在可折叠区里——它是这个会话的基本信息，应始终可见。 */}
      <div className="shrink-0 border-b border-border/40">
        {/* 标题行：图标 + 标题 + 状态徽标 */}
        <div className="flex items-center h-9 px-3 gap-2">
          <Info className="size-3.5 text-primary/80 shrink-0" />
          <span className="flex-1 min-w-0 text-[11px] font-semibold uppercase tracking-wider text-foreground/80">
            {t("autoResearch.state.taskInfo")}
          </span>
          <StatusPill status={status} />
        </div>
        <div className="px-3 pb-3 space-y-3">
          {/* 研究主题：直接显示内容，无 label、无嵌套面板；最多两行，
              超出省略并挂 title 供悬停看全文 */}
          <p
            className="text-[13px] font-semibold text-foreground leading-relaxed line-clamp-2"
            title={session.topic}
          >
            {session.topic || "—"}
          </p>

          {/* 运行元信息：创建时间 + 运行时长并排一行，不写 label——时间戳的形态
              与等宽时长本身可辨，标签只是冗余。右侧时长用浅底 chip 与左侧时间戳
              拉开层次（「什么时候建的」是背景信息，「跑了多久」才是要盯的值），
              未结束时点一颗脉冲圆点表示仍在累计。悬停仍有 title 兜底说明。 */}
          <div className="flex items-center gap-2 text-[11px]">
            <Clock className="size-3 shrink-0 text-muted-foreground/50" />
            <span
              className="min-w-0 truncate font-mono tabular-nums text-muted-foreground"
              title={t("autoResearch.state.createdAt")}
            >
              {fmtDateTime(session.created_time) ?? "—"}
            </span>
            <span
              className={cn(
                "ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-md bg-muted/50 px-1.5 py-0.5 font-mono text-[10px] tabular-nums",
                isRunning ? "text-primary/90" : "text-foreground/70",
              )}
              title={t("autoResearch.state.duration")}
            >
              {isRunning && (
                <span className="relative flex size-1.5">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" />
                  <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
                </span>
              )}
              {fmtDuration(session.created_time, session.ended_time) ?? "—"}
            </span>
          </div>

          {/* 错误信息（仅失败时） */}
          {session.error && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/6 p-3 space-y-1.5">
              <SectionLabel className="block text-red-500">
                {t("autoResearch.state.error")}
              </SectionLabel>
              <p className="text-xs text-red-600 dark:text-red-300/90 leading-relaxed break-words">
                {session.error}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* 面板级操作条：报告分析 / 研究日志。这两项是对整个会话的平级入口（弹层 / 抽屉），
          故置于任务信息下方、其余区域之上。统一主色点缀（无语义的多色只会制造噪音）：
          图标着主色以保证可供性/可点感，表面中性实底 + 细阴影强化按钮质感。
          产物相关的操作（打开 IDE / 下载 / 导入 / 刷新）已下移到「产物」区，见下方。 */}
      <div className="grid grid-cols-2 gap-1.5 px-3 py-2.5 shrink-0 border-b border-border/40">
        {/* 报告分析 */}
        <button
          type="button"
          onClick={() => setTool("report")}
          title={t("autoResearch.mainTabs.report")}
          className="group flex items-center justify-center gap-2 px-2 py-1.5 rounded-lg border border-border/70 bg-card shadow-sm hover:bg-primary/5 hover:border-primary/40 transition-colors duration-200"
        >
          <div className="grid place-items-center size-6 rounded-md bg-primary/10 group-hover:bg-primary/15 transition-colors duration-200">
            <FileBarChart className="size-3.5 text-primary" />
          </div>
          <div className="min-w-0 text-[11px] font-semibold text-foreground/90 truncate">
            {t("autoResearch.mainTabs.report")}
          </div>
        </button>

        {/* 完整日志（整会话，区别于中间面板的「本轮日志」） */}
        <button
          type="button"
          onClick={() => {
            setLogDrawerTab("logs")
            setLogDrawerOpen(true)
          }}
          title={t("autoResearch.mainTabs.logsFull", {
            defaultValue: "完整日志",
          })}
          className="group flex items-center justify-center gap-2 px-2 py-1.5 rounded-lg border border-border/70 bg-card shadow-sm hover:bg-primary/5 hover:border-primary/40 transition-colors duration-200"
        >
          <div className="grid place-items-center size-6 rounded-md bg-primary/10 group-hover:bg-primary/15 transition-colors duration-200">
            <ScrollText className="size-3.5 text-primary" />
          </div>
          <div className="min-w-0 text-[11px] font-semibold text-foreground/90 truncate">
            {t("autoResearch.mainTabs.logsFull", {
              defaultValue: "完整日志",
            })}
          </div>
        </button>
      </div>

      {/* llm4ad 实验：视图切换在左，全屏按钮在最右侧（ml_vision 画像下隐藏） */}
      {!hideExperiment && (
        <CollapsibleSection
          icon={FlaskConical}
          title={t("autoResearch.experiment.title")}
          right={
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => void genQ.refetch()}
                disabled={genQ.isFetching}
                title={t("autoResearch.artifacts.refresh")}
                className="grid place-items-center size-6 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
              >
                <RefreshCw
                  className={cn("size-3.5", genQ.isFetching && "animate-spin")}
                />
              </button>
              <ExperimentFullscreenDialog
                sessionId={session.id}
                running={
                  session.status === "running" || session.status === "paused"
                }
                algorithm={expAlgorithm}
                onAlgorithmChange={handleExpAlgorithm}
              />
            </div>
          }
        >
          <ExperimentPanel
            sessionId={session.id}
            running={
              session.status === "running" || session.status === "paused"
            }
            algorithm={expAlgorithm}
            onAlgorithmChange={handleExpAlgorithm}
          />
        </CollapsibleSection>
      )}

      {/* 2. 产物（中间）：常显不可折叠——操作行（图标 + 靠右按钮组）+ 文件树；
          树占剩余空间并内部滚动。 */}
      <div className="flex-1 min-h-0 flex flex-col border-b border-border/40">
        {/* 操作行：左侧是文件进出的两枚（导入 / 导出）——它们作用于整棵产物树，
            属于「区级」动作，靠左更贴近产物的归属感；右侧是视图与工具类动作
            （展开 → 折叠 → 刷新 → IDE），全部为纯图标 + tooltip，与刷新按钮同款。
            展开/折叠为两枚独立按钮，分别对应「一键展开全部」与「一键折叠到第一
            层」，不做 toggle 合并——两个方向各自可见，不必先试点一次才知道当前
            处于哪一侧。文字标题去掉（面板本身就在右侧，标签是重复信息）。 */}
        <div className="flex items-center h-9 px-3 gap-1.5 shrink-0">
          <FolderTree className="size-3.5 text-primary/80 shrink-0" />
          {/* 图标与左侧按钮拉开一点距离：两者是「区标记」与「区动作」两种语义，
              贴太近会被读成同一个按钮组。 */}
          <span className="w-2 shrink-0" aria-hidden />
          {/* 导入产物（zip 覆盖导入） */}
          <ArtifactLabelButton
            icon={Upload}
            label={t("autoResearch.artifacts.importShort")}
            title={t("autoResearch.artifacts.import")}
            busy={importMut.isPending || analyzingZip}
            disabled={importMut.isPending || analyzingZip}
            onClick={handlePickArtifactZip}
          />
          {/* 下载产物（打包为 zip） */}
          <ArtifactLabelButton
            icon={DownloadCloud}
            label={t("autoResearch.artifacts.export")}
            title={t("autoResearch.artifacts.downloadAll")}
            busy={zipping}
            disabled={zipping}
            onClick={handleDownloadAll}
          />
          {/* gap-0.5 与上方「实验」标题行的右侧按钮组完全对齐：两行都是 size-6 图标
             钮并排，间距（2px）必须同值，否则同一列里的按钮会错开半格。
             -mr-1 补的是两行基准内边距的差：本行 px-3（12px），实验标题行是
             pr-2 + pl-1（8px + 4px），不补这一下最右侧按钮会左偏 4px。 */}
          <div className="ml-auto -mr-1 flex items-center gap-0.5 shrink-0">
            <ArtifactTreeControls
              canExpand={!!root}
              onExpandAll={tree.expandAll}
              onCollapseAll={tree.collapseAll}
              onRefresh={() => void treeQ.refetch()}
              refreshing={treeQ.isFetching}
              extra={
                <ArtifactIconButton
                  icon={Code}
                  title={t("autoResearch.mainTabs.ide")}
                  onClick={() => setTool("ide")}
                />
              }
            />
          </div>
        </div>

        {/* 文件树：占剩余空间，内部滚动 */}
        <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-3">
          {treeQ.isLoading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-3">
              <Loader2 className="size-4 animate-spin" />
              {t("autoResearch.artifacts.loading")}
            </div>
          ) : !root ? (
            <p className="text-xs text-muted-foreground/60 py-3">
              {t("autoResearch.artifacts.empty")}
            </p>
          ) : (
            <ArtifactTree
              root={root}
              sessionId={session.id}
              expanded={tree.expanded}
              onToggle={tree.toggleDir}
              onExpandDir={tree.expandDir}
              onPreview={setPreviewPath}
            />
          )}
        </div>
      </div>

      {/* 导入产物 zip 的隐藏 file input：仅接受 zip，由「产物」标题行的导入按钮触发。 */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".zip,application/zip"
        className="hidden"
        onChange={handleImportZip}
      />

      {/* 导入覆盖确认：zip 与会话内同名文件重名时先确认再覆盖。 */}
      <Dialog
        open={!!pendingImport}
        onOpenChange={(o) => !o && setPendingImport(null)}
      >
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>
              {t("autoResearch.artifacts.importOverwriteTitle")}
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t("autoResearch.artifacts.importOverwriteConfirm", {
              count: pendingImport?.overwriteCount ?? 0,
            })}
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={importMut.isPending}
              onClick={() => setPendingImport(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button disabled={importMut.isPending} onClick={confirmImport}>
              {importMut.isPending && (
                <Loader2 className="size-4 animate-spin" />
              )}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ArtifactPreviewDialog
        sessionId={session.id}
        path={previewPath}
        onClose={() => setPreviewPath(null)}
        readOnly={false}
        editablePaths={editablePaths}
      />

      {/* 日志 / 运行历史底部抽屉（右侧面板唯一入口） */}
      <ResearchLogDrawer
        sessionId={session.id}
        open={logDrawerOpen}
        onOpenChange={setLogDrawerOpen}
        tab={logDrawerTab}
        onTabChange={setLogDrawerTab}
      />

      {/* 报告分析弹层（全屏）：IDE 走独立的 IdeDialog，打开时才挂载。 */}
      <Dialog
        open={tool === "report"}
        onOpenChange={(o) => !o && setTool(null)}
      >
        <DialogContent
          showCloseButton={false}
          className="max-w-none w-screen h-screen sm:max-w-none translate-x-0 translate-y-0 top-0 left-0 rounded-none border-0 p-0 gap-0 grid-rows-[auto_minmax(0,1fr)] bg-background/95 backdrop-blur"
        >
          <DialogHeader className="flex flex-row items-center justify-between gap-2 h-14 px-5 border-b border-border/60 space-y-0 text-left">
            <DialogTitle className="flex items-center gap-2 text-base">
              <FileBarChart className="size-4 text-primary" />
              {t("autoResearch.mainTabs.report")}
            </DialogTitle>
            <div className="flex items-center gap-1">
              <DialogClose asChild>
                <button
                  type="button"
                  aria-label={t("common.close")}
                  className="grid place-items-center size-8 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
                >
                  <X className="size-4" />
                </button>
              </DialogClose>
            </div>
          </DialogHeader>
          <div className="min-h-0 overflow-hidden">
            <AnalysisReport sessionId={session.id} />
          </div>
        </DialogContent>
      </Dialog>

      {/* IDE 全屏弹层（code-server iframe）：与阶段详情抽屉共用同一个组件。 */}
      {tool === "ide" && (
        <IdeDialog
          sessionId={session.id}
          open
          onOpenChange={() => setTool(null)}
        />
      )}
    </div>
  )
}

/**
 * 读取 zip 内所有条目名（仅解析 central directory，不解压文件内容）。
 *
 * 不用外部 zip 库，避免为此引入新依赖：直接读 EOCD 定位 central directory，
 * 遍历条目取文件名。带目录前缀的条目名会被原样返回（调用方负责归一化），
 * 目录条目（以 `/` 结尾）不参与比较。解析失败时抛出，由调用方降级为直接导入。
 */
async function readZipEntryNames(file: File): Promise<string[]> {
  const buf = await file.arrayBuffer()
  const bytes = new Uint8Array(buf)
  const dv = new DataView(buf)

  // 从文件末尾向前扫描 EOCD 签名 0x06054b50（允许注释，最多 65535 + 22 字节）。
  const EOCD = 0x06054b50
  let eocd = -1
  const tail = Math.min(bytes.length, 22 + 65535)
  for (let i = bytes.length - tail; i <= bytes.length - 22; i++) {
    if (dv.getUint32(i, true) === EOCD) {
      eocd = i
      break
    }
  }
  if (eocd < 0) {
    // 空 zip 或非法结构：按无条目处理（不抛，避免阻断导入）。
    return []
  }

  const totalEntries = dv.getUint16(eocd + 10, true)
  const offset = dv.getUint32(eocd + 16, true)

  // ZIP64：EOCD 里条目数/偏移为 0xFFFF/0xFFFFFFFF 时，从 ZIP64 EOCD 读真实值。
  const locator = eocd - 20
  if (locator >= 0 && dv.getUint32(locator, true) === 0x07064b50) {
    const zip64 = Number(dv.getBigUint64(locator + 8, true))
    if (zip64 >= 0 && zip64 + 56 <= bytes.length) {
      try {
        const total = Number(dv.getBigUint64(zip64 + 32, true))
        const off = Number(dv.getBigUint64(zip64 + 48, true))
        if (total > 0) {
          return readCentralDir(bytes, dv, total, off)
        }
      } catch {
        // ZIP64 定位失败回退经典读取
      }
    }
  }

  return readCentralDir(bytes, dv, totalEntries, offset)
}

/** 从 central directory 读取 `count` 个条目的文件名。 */
function readCentralDir(
  bytes: Uint8Array,
  dv: DataView,
  count: number,
  offset: number,
): string[] {
  const names: string[] = []
  const decoder = new TextDecoder()
  let ptr = offset
  for (let i = 0; i < count; i++) {
    // 中央目录条目头签名 0x02014b50
    if (ptr + 46 > bytes.length || dv.getUint32(ptr, true) !== 0x02014b50) {
      throw new Error("invalid central directory")
    }
    const nameLen = dv.getUint16(ptr + 28, true)
    const extraLen = dv.getUint16(ptr + 30, true)
    const commentLen = dv.getUint16(ptr + 32, true)
    const nameBytes = bytes.subarray(ptr + 46, ptr + 46 + nameLen)
    const name = decoder.decode(nameBytes)
    if (!name.endsWith("/")) names.push(name)
    ptr += 46 + nameLen + extraLen + commentLen
  }
  return names
}

/** ISO 时间串 → 本地可读「YYYY-MM-DD HH:mm」，无值返回 null。 */
function fmtDateTime(iso?: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ")
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`
}

/**
 * 运行时长：从 ``start`` 到 ``end``（无 end 视为进行中，算到当前）。
 * 输出人类可读（h/m/s），无起点返回 null。
 */
function fmtDuration(
  start?: string | null,
  end?: string | null,
): string | null {
  if (!start) return null
  const t0 = new Date(start).getTime()
  const t1 = end ? new Date(end).getTime() : Date.now()
  if (Number.isNaN(t0) || Number.isNaN(t1) || t1 < t0) return null
  const sec = Math.floor((t1 - t0) / 1000)
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

/**
 * 可收起区域：标题栏点击展开/收起，标题右侧可放操作控件（`right`）。
 * `grow` 的区域展开时占据剩余高度并内部滚动；其余区域按内容高度、超出才滚。
 */
function CollapsibleSection({
  icon: Icon,
  title,
  defaultOpen = true,
  grow = false,
  right,
  stickyTop,
  children,
}: {
  icon: typeof FlaskConical
  title: string
  defaultOpen?: boolean
  grow?: boolean
  right?: ReactNode
  /** 固定在标题行下方、可滚动内容上方的区域（不随内容滚动）。 */
  stickyTop?: ReactNode
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div
      className={cn(
        "flex flex-col border-b border-border/40",
        open && grow ? "flex-1 min-h-0" : "shrink-0",
      )}
    >
      {/* 标题行：toggle 按钮 + 右侧操作控件（并列 sibling，避免按钮嵌套） */}
      <div className="flex items-center h-9 pr-2 shrink-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex-1 min-w-0 flex items-center gap-2 px-3 h-full hover:bg-primary/6 transition-colors"
        >
          {open ? (
            <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />
          ) : (
            <ChevronRight className="size-3.5 text-muted-foreground shrink-0" />
          )}
          <Icon className="size-3.5 text-primary/80 shrink-0" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground/80">
            {title}
          </span>
        </button>
        {open && right && <div className="shrink-0 pl-1">{right}</div>}
      </div>
      {/* 固定区：位于标题行与滚动内容之间，不随内容一起滚动 */}
      {open && stickyTop && <div className="px-3 shrink-0">{stickyTop}</div>}
      {open && (
        <div
          className={cn(
            "px-3 pb-3",
            grow
              ? "flex-1 min-h-0 overflow-y-auto"
              : "max-h-[40vh] overflow-y-auto",
          )}
        >
          {children}
        </div>
      )}
    </div>
  )
}
