import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Clock,
  type Code,
  Download,
  Folder,
  FolderOpen,
  FolderTree,
  RefreshCw,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchArtifactTreeNode } from "@/client"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { downloadResearchArtifact } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import { FileKindIcon, formatSize } from "./ArtifactPreviewDialog"

/**
 * 产物树的可复用视图层：右侧产物面板与阶段详情抽屉共用同一套节点渲染与操作
 * 控件，避免两处各写一份树（样式/交互漂移是这类重复的必然结果）。
 *
 * 这里只放**纯展示 + 数据回调**的部件，数据获取与状态（展开集合、刷新、IDE）
 * 仍由各调用方自己持有——面板要跨阶段看整棵树，抽屉只关心本阶段子树，两者的
 * 数据范围与标题行布局都不同。
 */

/** 单行文件/目录的缩进基准（每层 12px）。 */
const INDENT_PER_DEPTH = 12

/**
 * 收集某目录及其所有子孙目录的 path（递归展开用）。
 *
 * @param node 起始节点；非目录返回空数组。
 */
export function collectDirPaths(node: ResearchArtifactTreeNode): string[] {
  if (!node.is_dir) return []
  const out = [node.path]
  for (const c of node.children ?? []) out.push(...collectDirPaths(c))
  return out
}

/** 顶层目录 path 集合（默认展开第一层）。 */
export function topLevelDirs(root: ResearchArtifactTreeNode): Set<string> {
  const s = new Set<string>()
  for (const n of root.children ?? []) if (n.is_dir) s.add(n.path)
  return s
}

/**
 * 收集产物树里所有文件的相对路径。
 *
 * @param root 树根；为空返回空数组。
 * @returns 文件路径集合（不含目录）。
 */
export function collectFilePaths(
  root: ResearchArtifactTreeNode | null,
): Set<string> {
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
 * 收集产物树里所有可编辑文件的相对路径（右侧面板编辑范围）。
 *
 * 与门控编辑同口径但作用域取整棵树：凡产物树里能点到的非点文件都允许就地保存
 * （后端 write_artifact 只拒绝 `.` 开头路径，树构建时已过滤这类内部点文件，并会
 * 把原文备份到 hitl/snapshots/）。此前只允许 `stage-NN/` 下的文件，导致 checkpoint /
 * hitl 等目录产物只读，与门控区可编辑行为不一致。
 */
export function collectEditableFilePaths(
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
export function middleEllipsis(name: string, head = 14, tail = 8): string {
  if (name.length <= head + tail + 1) return name
  return `${name.slice(0, head)}…${name.slice(-tail)}`
}

/**
 * 同 {@link fmtDateTime}，但精确到秒——产物文件的 mtime 常在同一分钟内密集变化
 * （一轮里连续产出多个文件），只到分钟看不出先后，故树里 hover 卡片用秒级。
 * 非法时间串原样返回（后端可能给出非 ISO 值），无值返回 null。
 */
export function fmtDateTimeSeconds(iso?: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 19).replace("T", " ")
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * 产物树操作行里的图标按钮（刷新 / 全部展开 / 全部收起）：size-6、无文字，含义由
 * tooltip 承载。`busy` 时图标换成转圈并禁用，避免刷新连点。
 */
export function ArtifactIconButton({
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
 * 产物操作行里的带文字标签按钮（导入 / 导出）：核心动作，值得占用横向空间显式
 * 命名。面板默认宽度只有 384px，故做成 px-1.5 的窄胶囊（图标 3、文字 10px），
 * 文案本身不 truncate——宽度不够时优先压缩左侧标题。
 */
export function ArtifactLabelButton({
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
      <Icon
        className={cn("size-3 shrink-0 text-primary", busy && "animate-spin")}
      />
      {label}
    </button>
  )
}

/**
 * 产物树主体：递归渲染节点，展开集合与预览回调由调用方提供。
 *
 * @param root 树的根节点；其自身不渲染，只渲染 `children`。
 * @param expanded 当前展开的目录 path 集合（受控）。
 * @param onToggle 点击目录行切换展开态。
 * @param onExpandDir 点击目录行右侧「展开全部子孙」。
 * @param onPreview 点击文件名预览。
 */
export function ArtifactTree({
  root,
  sessionId,
  expanded,
  onToggle,
  onExpandDir,
  onPreview,
}: {
  root: ResearchArtifactTreeNode
  sessionId: string
  expanded: Set<string>
  onToggle: (path: string) => void
  onExpandDir: (node: ResearchArtifactTreeNode) => void
  onPreview: (path: string) => void
}) {
  return (
    <ul className="space-y-0.5">
      {(root.children ?? []).map((node) => (
        <TreeNode
          key={node.path}
          node={node}
          depth={0}
          sessionId={sessionId}
          expanded={expanded}
          onToggle={onToggle}
          onExpandDir={onExpandDir}
          onPreview={onPreview}
        />
      ))}
    </ul>
  )
}

/**
 * 产物树的展开集合管理：默认展开第一层，之后树内容刷新不重置用户的展开状态。
 *
 * @param root 当前树根；用于首帧展开与「全部展开」。
 * @param resetKey 会话/阶段标识，变化时重新按默认策略展开（切换目标即换一棵树）。
 */
export function useArtifactTreeExpansion(
  root: ResearchArtifactTreeNode | null,
  resetKey: string | null,
) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [initedKey, setInitedKey] = useState<string | null>(null)

  useEffect(() => {
    if (!root || !resetKey || initedKey === resetKey) return
    setInitedKey(resetKey)
    setExpanded(topLevelDirs(root))
  }, [root, resetKey, initedKey])

  const toggleDir = useCallback((path: string) => {
    setExpanded((s) => {
      const n = new Set(s)
      if (n.has(path)) n.delete(path)
      else n.add(path)
      return n
    })
  }, [])

  const expandDir = useCallback((node: ResearchArtifactTreeNode) => {
    setExpanded((s) => {
      const n = new Set(s)
      for (const p of collectDirPaths(node)) n.add(p)
      return n
    })
  }, [])

  const expandAll = useCallback(() => {
    setExpanded(root ? new Set(collectDirPaths(root)) : new Set())
  }, [root])

  const collapseAll = useCallback(() => setExpanded(new Set()), [])

  return { expanded, setExpanded, toggleDir, expandDir, expandAll, collapseAll }
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
  const pad = { paddingLeft: `${depth * INDENT_PER_DEPTH + 4}px` }

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
              style={{ left: `${depth * INDENT_PER_DEPTH + 11}px` }}
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
      {/* 文件行：点文件名预览，点下载图标下载。整行 hover 弹出「全路径 + 最后修改
          时间」卡片——树里的文件名被中段省略、目录前缀也只在嵌套层级里隐含，
          悬停卡片把这两项补全，方便确认到底在改哪个文件。 */}
      <div
        style={pad}
        className="group flex items-center gap-1 py-1 pr-1 rounded hover:bg-primary/6 text-xs"
      >
        <HoverCard openDelay={160} closeDelay={80}>
          <HoverCardTrigger asChild>
            <button
              type="button"
              onClick={() => onPreview(node.path)}
              className="flex-1 min-w-0 flex items-center gap-1 text-left"
            >
              <span className="w-3 shrink-0" />
              <FileKindIcon name={node.name} />
              <span className="truncate flex-1 text-foreground/80">
                {middleEllipsis(node.name)}
              </span>
              {node.size != null && (
                <span className="text-[10px] text-muted-foreground/60 shrink-0 tabular-nums">
                  {formatSize(node.size)}
                </span>
              )}
            </button>
          </HoverCardTrigger>
          <HoverCardContent
            side="left"
            align="start"
            collisionPadding={12}
            className="w-auto max-w-[22rem] space-y-1.5 p-2.5 text-[11px]"
          >
            {/* 全路径：含 run_dir 下的完整相对路径，长路径换行不截断 */}
            <div className="flex items-start gap-2">
              <span className="shrink-0 pt-px text-muted-foreground/70">
                <FolderTree className="size-3" />
              </span>
              <span className="min-w-0 break-all font-mono leading-relaxed text-foreground/90">
                {node.path}
              </span>
            </div>
            {/* 最后修改时间：后端 mtime 为 UTC，这里按本地时区显示到秒 */}
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-muted-foreground/70">
                <Clock className="size-3" />
              </span>
              <span className="font-mono tabular-nums text-muted-foreground">
                {fmtDateTimeSeconds(node.mtime) ??
                  t("autoResearch.artifacts.mtimeUnknown", {
                    defaultValue: "时间未知",
                  })}
              </span>
            </div>
          </HoverCardContent>
        </HoverCard>
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

/** 展开/折叠/刷新 三枚按钮的公共组合（面板与抽屉操作行共用）。 */
export function ArtifactTreeControls({
  canExpand,
  onExpandAll,
  onCollapseAll,
  onRefresh,
  refreshing,
  extra,
}: {
  /** 是否有可展开的树（无树时展开/折叠无意义，不渲染）。 */
  canExpand: boolean
  onExpandAll: () => void
  onCollapseAll: () => void
  onRefresh: () => void
  refreshing: boolean
  /** 追加在刷新右侧的额外动作（如打开 IDE），与刷新同组，不再用竖线分隔。 */
  extra?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <>
      {canExpand && (
        <>
          <ArtifactIconButton
            icon={ChevronsUpDown}
            title={t("autoResearch.artifacts.expandAll", {
              defaultValue: "全部展开",
            })}
            onClick={onExpandAll}
          />
          <ArtifactIconButton
            icon={ChevronsDownUp}
            title={t("autoResearch.artifacts.collapseAll")}
            onClick={onCollapseAll}
          />
          <span aria-hidden className="h-4 w-px shrink-0 bg-border/60" />
        </>
      )}
      {/* 刷新与 extra（IDE）同属「工具」一组，中间不画竖线；竖线只分隔上一组
          （展开 / 折叠）。 */}
      <ArtifactIconButton
        icon={RefreshCw}
        title={t("autoResearch.artifacts.refresh")}
        busy={refreshing}
        disabled={refreshing}
        onClick={onRefresh}
      />
      {extra}
    </>
  )
}
