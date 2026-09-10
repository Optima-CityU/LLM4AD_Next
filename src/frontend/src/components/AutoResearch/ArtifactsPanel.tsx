import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Clock,
  Code,
  Download,
  DownloadCloud,
  FileBarChart,
  FlaskConical,
  Folder,
  FolderOpen,
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

import type { ResearchArtifactTreeNode, ResearchSessionItem } from "@/client"
import { UtilsCodeServerService } from "@/client"
import { useTheme } from "@/components/theme-provider"
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
  downloadResearchArtifact,
  downloadResearchArtifactsArchive,
  useImportResearchArtifacts,
  useResearchArtifactTree,
  useResearchGenerated,
  useResearchState,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"
import AnalysisReport from "./AnalysisReport"
import ArtifactPreviewDialog, {
  FileKindIcon,
  formatSize,
} from "./ArtifactPreviewDialog"
import ExperimentFullscreenDialog from "./ExperimentFullscreenDialog"
import ExperimentPanel from "./ExperimentPanel"
import ResearchLogDrawer, { type ResearchDrawerTab } from "./ResearchLogDrawer"
import { ML_VISION_PROFILE } from "./shared"
import { SectionLabel, StatusPill } from "./tech"

// 重启 IDE 冷却：与 evolution 的 InitializedView 保持一致，防止连点。
const IDE_REFRESH_COOLDOWN_MS = 3000

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
 * 滚动，其余区域按内容高度、超出才滚）。产物区的操作按钮（打开 IDE / 下载 /
 * 导入 / 刷新）固定在操作行内常显，不再随折叠态在顶栏与面板之间跳动。
 * 产物树点文件名预览、点下载图标下载；每个目录可递归展开全部子孙。
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
  // 产物预览弹框：只保存目标文件路径，弹框内部据此拉树 + 定位 + 预览。
  const [previewPath, setPreviewPath] = useState<string | null>(null)
  // 右侧面板可编辑产物：与门控编辑同口径——凡产物树里出现的文件名均可就地编辑。
  // 后端 write_artifact 只拒绝 `.` 开头的内部点文件（树构建时已过滤），并把原文
  // 备份到 hitl/snapshots/；故树里能点到的文件（含 checkpoint.json、hitl/ 引导文件）
  // 都允许保存，与中间门控区点击产物后可编辑保持一致。
  const editablePaths = useMemo(() => collectEditableFilePaths(root), [root])

  // 产物树展开态提升到此处，让标题行的「全部收起」能控制它。
  const [treeExpanded, setTreeExpanded] = useState<Set<string>>(new Set())
  // 每个会话首次拿到树时默认展开第一层；之后树内容刷新不重置用户的展开状态。
  const initedRef = useRef<string | null>(null)
  useEffect(() => {
    if (root && initedRef.current !== session.id) {
      initedRef.current = session.id
      setTreeExpanded(topLevelDirs(root))
    }
  }, [root, session.id])

  const toggleDir = (path: string) =>
    setTreeExpanded((s) => {
      const n = new Set(s)
      if (n.has(path)) n.delete(path)
      else n.add(path)
      return n
    })
  const expandDir = (node: ResearchArtifactTreeNode) =>
    setTreeExpanded((s) => {
      const n = new Set(s)
      for (const p of collectDirPaths(node)) n.add(p)
      return n
    })
  // 树级「全部展开 / 全部收起」：此前挂在可折叠标题行的右侧，产物区改为常显后
  // 移到标题行右端（操作行留给四枚产物按钮），保持「一键折叠到第一层」的能力。
  const expandAllDirs = () =>
    setTreeExpanded(root ? new Set(collectDirPaths(root)) : new Set())

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

  // IDE：与 evolution 一致，先拿 code token 启动容器，再加载 iframe。
  const { resolvedTheme } = useTheme()
  const [ideState, setIdeState] = useState<
    "idle" | "loading" | "success" | "error"
  >("idle")
  const [ideError, setIdeError] = useState<string>("")
  const [iframeKey, setIframeKey] = useState(0)
  const [ideRefreshing, setIdeRefreshing] = useState(false)
  // 请求代次：每次发起 loadCodeToken 领一个号，回调里只认最新号。
  // 弹层关闭时递增此值，作废所有在途 promise，避免其 setState 覆盖复位后的 idle 态
  // （否则下次打开时 ideState 非 idle，自动加载 effect 不触发，显示上次的陈旧 iframe）。
  const ideReqRef = useRef(0)
  const loadCodeToken = useCallback(() => {
    const reqId = ++ideReqRef.current
    setIdeState("loading")
    return UtilsCodeServerService.getCodeToken({
      dark: resolvedTheme === "dark",
    })
      .then(() => {
        if (ideReqRef.current !== reqId) return
        setIdeState("success")
        setIframeKey((k) => k + 1)
      })
      .catch((err: unknown) => {
        if (ideReqRef.current !== reqId) return
        setIdeState("error")
        const e = err as { body?: { detail?: string }; message?: string }
        setIdeError(
          e?.body?.detail || e?.message || t("evolution.getCodeTokenFailed"),
        )
      })
  }, [resolvedTheme, t])

  // 打开 IDE 弹层时（且尚未加载过）自动拉起 code token。
  useEffect(() => {
    if (tool === "ide" && ideState === "idle") {
      void loadCodeToken()
    }
  }, [tool, ideState, loadCodeToken])

  // 重启 IDE：重新拉 token 并重载 iframe，带冷却防连点（同 evolution）。
  const handleRestartIde = useCallback(() => {
    if (ideRefreshing) return
    setIdeRefreshing(true)
    void loadCodeToken().finally(() => {
      setTimeout(() => setIdeRefreshing(false), IDE_REFRESH_COOLDOWN_MS)
    })
  }, [ideRefreshing, loadCodeToken])

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
              />
            </div>
          }
        >
          <ExperimentPanel
            sessionId={session.id}
            running={
              session.status === "running" || session.status === "paused"
            }
          />
        </CollapsibleSection>
      )}

      {/* 2. 产物（中间）：常显不可折叠——标题行（图标 + 标题 + 操作按钮组）+ 文件树；
          树占剩余空间并内部滚动。 */}
      <div className="flex-1 min-h-0 flex flex-col border-b border-border/40">
        {/* 标题行：图标 + 标题 + 操作按钮组。按钮分两组——
            ① 打开 IDE / 导出 / 导入 + 刷新：会话级动作，前三枚带文字标签（面板宽度
               不够时优先压缩标题，这三枚的文案不会掉）；
            ② 全部展开 / 全部收起：树级视图动作，纯图标 + tooltip，用竖线与左侧隔开。
            该区常显，故不再需要折叠箭头。 */}
        <div className="flex items-center h-9 pl-3 pr-1.5 gap-1.5 shrink-0">
          <FolderTree className="size-3.5 text-primary/80 shrink-0" />
          <span className="flex-1 min-w-0 text-[11px] font-semibold uppercase tracking-wider text-foreground/80 truncate">
            {t("autoResearch.tabs.artifacts")}
          </span>
          <div className="flex items-center gap-1 shrink-0">
            {/* 打开 IDE 编辑器弹层 */}
            <ArtifactLabelButton
              icon={Code}
              label={t("autoResearch.artifacts.ideEditor")}
              title={t("autoResearch.mainTabs.ide")}
              onClick={() => setTool("ide")}
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
            {/* 导入产物（zip 覆盖导入） */}
            <ArtifactLabelButton
              icon={Upload}
              label={t("autoResearch.artifacts.importShort")}
              title={t("autoResearch.artifacts.import")}
              busy={importMut.isPending || analyzingZip}
              disabled={importMut.isPending || analyzingZip}
              onClick={handlePickArtifactZip}
            />
            {/* 刷新产物列表 */}
            <ArtifactIconButton
              icon={RefreshCw}
              title={t("autoResearch.artifacts.refresh")}
              busy={treeQ.isFetching}
              disabled={treeQ.isFetching}
              onClick={() => void treeQ.refetch()}
            />
            {root && (
              <>
                <span
                  aria-hidden
                  className="ml-0.5 h-4 w-px shrink-0 bg-border/60"
                />
                <ArtifactIconButton
                  icon={ChevronsUpDown}
                  title={t("autoResearch.artifacts.expandAll", {
                    defaultValue: "全部展开",
                  })}
                  onClick={expandAllDirs}
                />
                <ArtifactIconButton
                  icon={ChevronsDownUp}
                  title={t("autoResearch.artifacts.collapseAll")}
                  onClick={() => setTreeExpanded(new Set())}
                />
              </>
            )}
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
            <ul className="space-y-0.5">
              {(root.children ?? []).map((node) => (
                <TreeNode
                  key={node.path}
                  node={node}
                  depth={0}
                  sessionId={session.id}
                  expanded={treeExpanded}
                  onToggle={toggleDir}
                  onExpandDir={expandDir}
                  onPreview={setPreviewPath}
                />
              ))}
            </ul>
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

      {/* 报告分析 / IDE 弹层（全屏） */}
      <Dialog
        open={tool !== null}
        onOpenChange={(o) => {
          if (!o) {
            setTool(null)
            // 关闭后复位 IDE，下次打开重新拉 token。递增代次作废在途请求，
            // 避免其迟到的 setState 把状态从 idle 又改回 success/error。
            ideReqRef.current++
            setIdeState("idle")
            setIdeError("")
          }
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="max-w-none w-screen h-screen sm:max-w-none translate-x-0 translate-y-0 top-0 left-0 rounded-none border-0 p-0 gap-0 grid-rows-[auto_minmax(0,1fr)] bg-background/95 backdrop-blur"
        >
          <DialogHeader className="flex flex-row items-center justify-between gap-2 h-14 px-5 border-b border-border/60 space-y-0 text-left">
            <DialogTitle className="flex items-center gap-2 text-base">
              {tool === "ide" ? (
                <Code className="size-4 text-primary" />
              ) : (
                <FileBarChart className="size-4 text-primary" />
              )}
              {tool === "ide"
                ? t("autoResearch.mainTabs.ide")
                : t("autoResearch.mainTabs.report")}
            </DialogTitle>
            <div className="flex items-center gap-1">
              {/* 重启服务：仅 IDE 弹层显示，放在关闭按钮左侧（同 evolution 逻辑） */}
              {tool === "ide" && (
                <>
                  {/* 打包下载全部产物：逻辑同产物区的下载按钮，放在重启按钮左侧 */}
                  <button
                    type="button"
                    aria-label={t("autoResearch.artifacts.downloadAll")}
                    disabled={zipping}
                    onClick={handleDownloadAll}
                    title={t("autoResearch.artifacts.downloadAll")}
                    className="inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {zipping ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <DownloadCloud className="size-3" />
                    )}
                  </button>
                  <button
                    type="button"
                    aria-label={t("evolution.ideRefresh.label")}
                    disabled={ideRefreshing}
                    onClick={handleRestartIde}
                    title={t("evolution.ideRefresh.tooltip")}
                    className="inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <RefreshCw
                      className={cn("size-3", ideRefreshing && "animate-spin")}
                    />
                  </button>
                </>
              )}
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
            {tool === "report" ? (
              <AnalysisReport sessionId={session.id} />
            ) : tool === "ide" ? (
              <div className="h-full p-4">
                {ideState === "loading" && (
                  <div className="h-full flex items-center justify-center rounded-lg border border-dashed bg-card/50">
                    <Loader2 className="mr-2 size-5 animate-spin" />
                    <span className="text-muted-foreground">
                      {t("evolution.startingIDE")}
                    </span>
                  </div>
                )}
                {ideState === "error" && (
                  <div className="h-full flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive/50 bg-card/50">
                    <AlertTriangle className="size-8 text-destructive/70" />
                    <p className="max-w-md px-4 text-center text-sm text-destructive">
                      {ideError}
                    </p>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                      onClick={() => setIdeState("idle")}
                    >
                      <RefreshCw className="size-3.5" />
                      {t("common.retry")}
                    </button>
                  </div>
                )}
                {ideState === "success" && (
                  <iframe
                    key={iframeKey}
                    id="autoresearchVscodeFrame"
                    className="w-full h-full border rounded-lg"
                    src={`${import.meta.env.VITE_CODE_SERVER_URL || "/code_ide"}/?folder=/data/project_home/research/${session.id}/`}
                    title={t("evolution.vsCodeTitle")}
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
                    sandbox="allow-same-origin allow-scripts allow-popups allow-forms allow-modals allow-downloads allow-presentation"
                    loading="lazy"
                  />
                )}
              </div>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/**
 * 产物标题行里的图标按钮（刷新 / 全部展开 / 全部收起）：size-6、无文字，含义由
 * tooltip 承载。`busy` 时图标换成转圈并禁用，避免刷新连点。
 */
function ArtifactIconButton({
  icon: Icon,
  title,
  busy = false,
  disabled = false,
  onClick,
}: {
  icon: typeof RefreshCw
  title: string
  busy?: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className="grid place-items-center size-6 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
    >
      <Icon className={cn("size-3.5", busy && "animate-spin")} />
    </button>
  )
}

/**
 * 产物标题行里的带文字标签按钮（打开 IDE / 导出 / 导入）：核心动作，值得占用横向
 * 空间显式命名。面板默认宽度只有 384px，故做成 px-1.5 的窄胶囊（图标 3、文字 10px），
 * 文案本身不 truncate——宽度不够时优先压缩左侧标题。
 */
function ArtifactLabelButton({
  icon: Icon,
  label,
  title,
  busy = false,
  disabled = false,
  onClick,
}: {
  icon: typeof Code
  label: string
  title: string
  busy?: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className="inline-flex items-center gap-1 h-6 px-1.5 rounded border border-border/70 bg-card text-[10px] font-medium text-foreground/80 whitespace-nowrap shrink-0 shadow-sm hover:text-primary hover:border-primary/40 hover:bg-primary/5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-foreground/80 disabled:hover:border-border/70 disabled:hover:bg-card"
    >
      <Icon className={cn("size-3 shrink-0 text-primary", busy && "animate-spin")} />
      {label}
    </button>
  )
}

/**
 * 收集产物树里所有文件的相对路径（用于判定 zip 导入的同名覆盖）。
 * 与 collectEditableFilePaths 同构，但只取 path 字符串集合。
 */
function collectFilePaths(root: ResearchArtifactTreeNode | null): Set<string> {
  const out = new Set<string>()
  if (!root) return out
  const walk = (node: ResearchArtifactTreeNode) => {
    for (const c of node.children ?? []) {
      if (c.is_dir) walk(c)
      else out.add(c.path)
    }
  }
  walk(root)
  return out
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

/** 收集某目录及其所有子孙目录的 path（递归展开用）。 */
function collectDirPaths(node: ResearchArtifactTreeNode): string[] {
  if (!node.is_dir) return []
  const out = [node.path]
  for (const c of node.children ?? []) out.push(...collectDirPaths(c))
  return out
}

/** 顶层目录 path 集合（默认展开第一层）。 */
function topLevelDirs(root: ResearchArtifactTreeNode): Set<string> {
  const s = new Set<string>()
  for (const n of root.children ?? []) if (n.is_dir) s.add(n.path)
  return s
}

/**
 * 收集产物树里所有可编辑文件的相对路径（右侧面板编辑范围）。
 *
 * 与门控编辑同口径但作用域取整棵树：凡产物树里能点到的非点文件都允许就地保存
 * （后端 write_artifact 只拒绝 `.` 开头路径，树构建时已过滤这类内部点文件，并会
 * 把原文备份到 hitl/snapshots/）。此前只允许 `stage-NN/` 下的文件，导致 checkpoint /
 * hitl 等目录产物只读，与门控区可编辑行为不一致。
 */
function collectEditableFilePaths(
  root: ResearchArtifactTreeNode | null,
): string[] {
  if (!root) return []
  const out: string[] = []
  const walk = (node: ResearchArtifactTreeNode) => {
    for (const c of node.children ?? []) {
      if (c.is_dir) walk(c)
      else out.push(c.path)
    }
  }
  walk(root)
  return out
}

/**
 * 长文件名中段省略，保留前缀与扩展名（如 evaluator_a1b2…c9.json）。
 * 哈希类产物名很长，末尾 truncate 会吞掉扩展名，中段省略更可读。
 */
function middleEllipsis(name: string, head = 14, tail = 8): string {
  if (name.length <= head + tail + 1) return name
  return `${name.slice(0, head)}…${name.slice(-tail)}`
}

function TreeNode({
  node,
  depth,
  sessionId,
  expanded,
  onToggle,
  onExpandDir,
  onPreview,
}: {
  node: ResearchArtifactTreeNode
  depth: number
  sessionId: string
  expanded: Set<string>
  onToggle: (path: string) => void
  onExpandDir: (node: ResearchArtifactTreeNode) => void
  onPreview: (path: string) => void
}) {
  const { t } = useTranslation()
  const pad = { paddingLeft: `${depth * 12 + 4}px` }

  if (node.is_dir) {
    const open = expanded.has(node.path)
    return (
      <li>
        {/* 目录行：整行点击折叠；右侧「递归展开」按钮（hover 显示） */}
        <div
          style={pad}
          className="group flex items-center gap-1 py-1 pr-1 rounded hover:bg-primary/6 text-xs text-foreground/80"
        >
          <button
            type="button"
            onClick={() => onToggle(node.path)}
            className="flex-1 min-w-0 flex items-center gap-1 text-left"
          >
            {open ? (
              <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
            )}
            {open ? (
              <FolderOpen className="size-3.5 shrink-0 text-amber-500" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-amber-500" />
            )}
            <span className="truncate">{node.name}</span>
          </button>
          <button
            type="button"
            onClick={() => onExpandDir(node)}
            title={t("autoResearch.artifacts.expandDir")}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-muted-foreground hover:text-primary shrink-0 transition-opacity"
          >
            <ChevronsUpDown className="size-3" />
          </button>
        </div>
        {open && (node.children?.length ?? 0) > 0 && (
          <ul className="relative space-y-0.5">
            {/* 层级引导线：绝对定位、不占布局，对齐父级图标处 */}
            <span
              aria-hidden
              className="pointer-events-none absolute inset-y-0 w-px bg-border dark:bg-border/70"
              style={{ left: `${depth * 12 + 11}px` }}
            />
            {node.children?.map((c) => (
              <TreeNode
                key={c.path}
                node={c}
                depth={depth + 1}
                sessionId={sessionId}
                expanded={expanded}
                onToggle={onToggle}
                onExpandDir={onExpandDir}
                onPreview={onPreview}
              />
            ))}
          </ul>
        )}
      </li>
    )
  }

  return (
    <li>
      {/* 文件行：点文件名预览，点下载图标下载 */}
      <div
        style={pad}
        className="group flex items-center gap-1 py-1 pr-1 rounded hover:bg-primary/6 text-xs"
      >
        <button
          type="button"
          onClick={() => onPreview(node.path)}
          className="flex-1 min-w-0 flex items-center gap-1 text-left"
        >
          <span className="w-3 shrink-0" />
          <FileKindIcon name={node.name} />
          <span
            className="truncate flex-1 text-foreground/80"
            title={node.path}
          >
            {middleEllipsis(node.name)}
          </span>
          {node.size != null && (
            <span className="text-[10px] text-muted-foreground/60 shrink-0 tabular-nums">
              {formatSize(node.size)}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={() =>
            void downloadResearchArtifact(sessionId, node.path).catch(
              (err: unknown) =>
                toast.error((err as Error)?.message ?? "download failed"),
            )
          }
          title={t("autoResearch.artifacts.download")}
          className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-muted-foreground hover:text-primary shrink-0 transition-opacity"
        >
          <Download className="size-3.5" />
        </button>
      </div>
    </li>
  )
}
