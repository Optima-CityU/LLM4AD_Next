import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Code,
  Download,
  DownloadCloud,
  FileBarChart,
  ScrollText,
  FlaskConical,
  Folder,
  FolderOpen,
  FolderTree,
  Info,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchArtifactTreeNode, ResearchSessionItem } from "@/client"
import { UtilsCodeServerService } from "@/client"
import { useTheme } from "@/components/theme-provider"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  downloadResearchArtifact,
  downloadResearchArtifactsArchive,
  useResearchArtifactTree,
  useResearchGenerated,
  useResearchState,
} from "@/hooks/useAutoResearch"
import { useAutoResearchHeader } from "@/hooks/useAutoResearchHeader"
import { cn } from "@/lib/utils"
import AnalysisReport from "./AnalysisReport"
import ArtifactPreviewDialog, {
  FileKindIcon,
  formatSize,
} from "./ArtifactPreviewDialog"
import ExperimentFullscreenDialog from "./ExperimentFullscreenDialog"
import ExperimentPanel from "./ExperimentPanel"
import ResearchLogDrawer, {
  type ResearchDrawerTab,
} from "./ResearchLogDrawer"
import { ML_VISION_PROFILE } from "./shared"
import { SectionLabel, StatusPill, stageNameByLang } from "./tech"

// 重启 IDE 冷却：与 evolution 的 InitializedView 保持一致，防止连点。
const IDE_REFRESH_COOLDOWN_MS = 3000

interface Props {
  session: ResearchSessionItem | null
  /** 右侧产物面板是否收起：收起时把四枚操作按钮注入顶栏右侧。 */
  rightCollapsed?: boolean
}

/**
 * 右侧面板：三个可收起区域——实验（演化仿真 / 趋势分析）、产物（文件树）、
 * 最优解（快照 + 指标）。收起交互对齐 evolution 右侧面板。
 *
 * 无整体滚动条：面板本体不滚，每个区域内容各自纵向滚动（产物区占剩余空间并
 * 滚动，其余区域按内容高度、超出才滚）。实验切换与产物「全部收起」放在各自
 * 标题行以省纵向空间。产物树点文件名预览、点下载图标下载；顶部一键折叠到第一层，
 * 每个目录可递归展开全部子孙。
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
  rightCollapsed,
}: {
  session: ResearchSessionItem
  rightCollapsed?: boolean
}) {
  const { t, i18n } = useTranslation()
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

  // 任务信息取最新态：state 查询比 session prop 更实时，缺失时回退 session。
  const status = state.data?.status ?? session.status
  const activeStageNum = state.data?.active_stage ?? session.active_stage
  // 当前阶段名优先本地化短名（对齐顶部进度轨与消息流），回退后端英文枚举名，
  // 避免任务信息区出现 CODE_GENERATION 这类原始枚举。
  const activeStageName =
    (activeStageNum != null
      ? stageNameByLang(activeStageNum, i18n.language)
      : "") ||
    state.data?.active_stage_name ||
    session.active_stage_name

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
  const [logDrawerTab, setLogDrawerTab] =
    useState<ResearchDrawerTab>("logs")

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
          e?.body?.detail ||
            e?.message ||
            t("evolution.getCodeTokenFailed"),
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

  // 产物面板收起时：把「报告分析 / 研究日志 / 打开 IDE / 下载产物」四枚紧凑按钮
  // 注入顶栏右侧（语言/主题切换器左侧）。四者的弹层/抽屉/下载均走 portal 或纯动作，
  // 面板收起（width:0 + inert）也能正常触发；展开或会话卸载时自动清空注入。
  const { setHeaderRight } = useAutoResearchHeader()
  useEffect(() => {
    if (!rightCollapsed) {
      setHeaderRight(null)
      return
    }
    setHeaderRight(
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setTool("report")}
          title={t("autoResearch.mainTabs.report")}
          aria-label={t("autoResearch.mainTabs.report")}
          className="grid place-items-center size-7 rounded-md border border-border/70 bg-card shadow-sm text-primary hover:bg-primary/10 hover:border-primary/40 transition-colors"
        >
          <FileBarChart className="size-4" />
        </button>
        <button
          type="button"
          onClick={() => {
            setLogDrawerTab("logs")
            setLogDrawerOpen(true)
          }}
          title={t("autoResearch.mainTabs.logsFull", {
            defaultValue: "完整日志",
          })}
          aria-label={t("autoResearch.mainTabs.logsFull", {
            defaultValue: "完整日志",
          })}
          className="grid place-items-center size-7 rounded-md border border-border/70 bg-card shadow-sm text-primary hover:bg-primary/10 hover:border-primary/40 transition-colors"
        >
          <ScrollText className="size-4" />
        </button>
        <button
          type="button"
          onClick={() => setTool("ide")}
          title={t("autoResearch.mainTabs.ide")}
          aria-label={t("autoResearch.mainTabs.ide")}
          className="grid place-items-center size-7 rounded-md border border-border/70 bg-card shadow-sm text-primary hover:bg-primary/10 hover:border-primary/40 transition-colors"
        >
          <Code className="size-4" />
        </button>
        <button
          type="button"
          onClick={handleDownloadAll}
          disabled={zipping}
          title={t("autoResearch.artifacts.downloadAll")}
          aria-label={t("autoResearch.artifacts.downloadAll")}
          className="grid place-items-center size-7 rounded-md border border-border/70 bg-card shadow-sm text-primary hover:bg-primary/10 hover:border-primary/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {zipping ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <DownloadCloud className="size-4" />
          )}
        </button>
      </div>,
    )
    return () => setHeaderRight(null)
  }, [rightCollapsed, zipping, handleDownloadAll, setHeaderRight, t])

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
          {/* 研究主题：直接显示内容，无 label、无嵌套面板 */}
          <p
            className="text-[13px] font-semibold text-foreground leading-relaxed line-clamp-3"
            title={session.topic}
          >
            {session.topic || "—"}
          </p>

          {/* 运行进度：当前阶段 / 创建时间 / 运行时长（平铺，无嵌套面板） */}
          <div className="space-y-1.5">
            <InfoRow
              label={t("autoResearch.state.activeStage")}
              value={
                activeStageName ||
                (activeStageNum != null ? `#${activeStageNum}` : null)
              }
            />
            <InfoRow
              label={t("autoResearch.state.createdAt")}
              value={fmtDateTime(session.created_time)}
              mono
            />
            <InfoRow
              label={t("autoResearch.state.duration")}
              value={fmtDuration(session.created_time, session.ended_time)}
              mono
            />
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

      {/* 面板级操作条：报告分析 / 研究日志 / 打开 IDE / 下载产物。这四项是对整个
          研究会话的平级操作入口，不隶属「产物」，故置于任务信息下方、其余区域之上。
          统一主色点缀（无语义的多色只会制造噪音）：图标着主色以保证可供性/可点感，
          表面中性实底 + 细阴影强化按钮质感，hover 再整体提亮。 */}
      <div className="grid grid-cols-2 gap-1.5 px-3 py-2.5 shrink-0 border-b border-border/40">
        {/* 报告分析 */}
        <button
          type="button"
          onClick={() => setTool("report")}
          title={t("autoResearch.mainTabs.report")}
          className="group flex items-center gap-2 px-2 py-1.5 rounded-lg border border-border/70 bg-card shadow-sm hover:bg-primary/5 hover:border-primary/40 transition-colors duration-200"
        >
          <div className="grid place-items-center size-6 rounded-md bg-primary/10 group-hover:bg-primary/15 transition-colors duration-200">
            <FileBarChart className="size-3.5 text-primary" />
          </div>
          <div className="flex-1 text-left min-w-0">
            <div className="text-[11px] font-semibold text-foreground/90 truncate">
              {t("autoResearch.mainTabs.report")}
            </div>
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
          className="group flex items-center gap-2 px-2 py-1.5 rounded-lg border border-border/70 bg-card shadow-sm hover:bg-primary/5 hover:border-primary/40 transition-colors duration-200"
        >
          <div className="grid place-items-center size-6 rounded-md bg-primary/10 group-hover:bg-primary/15 transition-colors duration-200">
            <ScrollText className="size-3.5 text-primary" />
          </div>
          <div className="flex-1 text-left min-w-0">
            <div className="text-[11px] font-semibold text-foreground/90 truncate">
              {t("autoResearch.mainTabs.logsFull", {
                defaultValue: "完整日志",
              })}
            </div>
          </div>
        </button>

        {/* 打开 IDE */}
        <button
          type="button"
          onClick={() => setTool("ide")}
          title={t("autoResearch.mainTabs.ide")}
          className="group flex items-center gap-2 px-2 py-1.5 rounded-lg border border-border/70 bg-card shadow-sm hover:bg-primary/5 hover:border-primary/40 transition-colors duration-200"
        >
          <div className="grid place-items-center size-6 rounded-md bg-primary/10 group-hover:bg-primary/15 transition-colors duration-200">
            <Code className="size-3.5 text-primary" />
          </div>
          <div className="flex-1 text-left min-w-0">
            <div className="text-[11px] font-semibold text-foreground/90 truncate">
              {t("autoResearch.mainTabs.ide")}
            </div>
          </div>
        </button>

        {/* 下载产物 */}
        <button
          type="button"
          onClick={handleDownloadAll}
          disabled={zipping}
          title={t("autoResearch.artifacts.downloadAll")}
          className="group flex items-center gap-2 px-2 py-1.5 rounded-lg border border-border/70 bg-card shadow-sm hover:bg-primary/5 hover:border-primary/40 transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-card disabled:hover:border-border/70"
        >
          <div className="grid place-items-center size-6 rounded-md bg-primary/10 group-hover:bg-primary/15 transition-colors duration-200">
            {zipping ? (
              <Loader2 className="size-3.5 text-primary animate-spin" />
            ) : (
              <DownloadCloud className="size-3.5 text-primary" />
            )}
          </div>
          <div className="flex-1 text-left min-w-0">
            <div className="text-[11px] font-semibold text-foreground/90 truncate">
              下载产物
            </div>
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

      {/* 2. 产物文件树（中间）：占剩余空间并内部滚动；报告/IDE 入口置于内容顶部，
          刷新 / 全部收起放标题行右侧 */}
      <CollapsibleSection
        icon={FolderTree}
        title={t("autoResearch.tabs.artifacts")}
        grow
        right={
          root && (
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => void treeQ.refetch()}
                disabled={treeQ.isFetching}
                title={t("autoResearch.artifacts.refresh")}
                className="grid place-items-center size-6 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
              >
                <RefreshCw
                  className={cn(
                    "size-3.5",
                    treeQ.isFetching && "animate-spin",
                  )}
                />
              </button>
              <button
                type="button"
                onClick={() => setTreeExpanded(new Set())}
                title={t("autoResearch.artifacts.collapseAll")}
                className="grid place-items-center size-6 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
              >
                <ChevronsDownUp className="size-3.5" />
              </button>
            </div>
          )
        }
      >
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
      </CollapsibleSection>

      <ArtifactPreviewDialog
        sessionId={session.id}
        path={previewPath}
        onClose={() => setPreviewPath(null)}
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
 * 任务信息里的「标签 — 值」单行：标签左对齐、值右对齐并截断，值缺失显示占位符。
 */
function InfoRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value?: string | null
  mono?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span
        className={cn(
          "text-xs font-medium text-foreground/90 truncate",
          mono && "font-mono text-muted-foreground/90",
        )}
        title={value ?? undefined}
      >
        {value || "—"}
      </span>
    </div>
  )
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
