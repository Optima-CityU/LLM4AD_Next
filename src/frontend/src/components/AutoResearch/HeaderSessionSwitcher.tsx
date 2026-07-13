import {
  ChevronDown,
  ChevronRight,
  Folder,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ResearchFolderItem, ResearchSessionItem } from "@/client"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  useInfiniteFolderSessions,
  useInfiniteSearchSessions,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import { sessionDotClasses } from "./tech"

interface Props {
  folders: ResearchFolderItem[]
  /** 未分组会话总数（来自 folders 响应）。 */
  ungroupedCount: number
  activeSession: ResearchSessionItem | null
  onSelectSession: (id: string) => void
  onCreateSession: (folderId: string | null) => void
}

/**
 * 顶栏「会话切换器」——仅在左侧会话侧栏收起时挂到 logo 右侧。
 *
 * 下拉呈现分组树（分组 → 组内会话，展开时懒加载并可翻页）+ 未分组会话，支持关键词
 * 过滤（扁平跨文件夹分页）、点击切换、一键新建研究。让无侧栏时仍可快速在会话间
 * 跳转，无需先展开侧栏。
 */
export default function HeaderSessionSwitcher({
  folders,
  ungroupedCount,
  activeSession,
  onSelectSession,
  onCreateSession,
}: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  // 分组树的本地折叠态（默认全部展开）。
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const kw = q.trim()
  const hasFilter = kw.length > 0

  // 分组默认折叠（避免一次展开 N 个分组、并发 N 个懒加载请求）；只有激活会话所在
  // 分组与「未分组」默认展开。用户显式点过的分组以其手动状态为准。
  const activeFolderId = activeSession?.folder_id ?? null
  const isCollapsed = (key: string, defaultExpanded: boolean) =>
    key in collapsed ? collapsed[key] : !defaultExpanded

  const pick = (id: string) => {
    onSelectSession(id)
    setOpen(false)
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={activeSession?.title ?? t("autoResearch.sidebar.title")}
          className={cn(
            "group inline-flex items-center gap-1.5 h-8 max-w-[220px] pl-2.5 pr-2 rounded-lg border transition-colors shrink-0",
            "border-border/60 bg-card/60 text-foreground/85 hover:border-primary/50 hover:bg-primary/10 hover:text-primary",
            open && "border-primary/50 bg-primary/10 text-primary",
          )}
        >
          {activeSession ? (
            <span
              className={cn(
                "size-1.5 rounded-full shrink-0",
                sessionDotClasses(activeSession.status),
              )}
            />
          ) : (
            <Folder className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate text-xs font-medium max-w-[150px]">
            {activeSession?.title ?? t("autoResearch.sidebar.title")}
          </span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground/70 group-hover:text-primary transition-colors" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="start"
        sideOffset={8}
        className="w-72 p-0 overflow-hidden"
      >
        {/* 搜索框 */}
        <div className="p-2 border-b border-border/60">
          <div className="group relative flex items-center">
            <Search className="pointer-events-none absolute left-2.5 size-3.5 text-muted-foreground/60 group-focus-within:text-primary transition-colors" />
            {/* stopPropagation：避免下拉把字母键当作 typeahead 抢焦点 */}
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
              placeholder={t("autoResearch.sidebar.searchPlaceholder")}
              className="w-full h-8 rounded-md border border-border/60 bg-card dark:bg-background/60 pl-8 pr-2 text-xs text-foreground placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30 transition-colors"
            />
          </div>
        </div>

        {/* 新建研究 */}
        <div className="p-1">
          <DropdownMenuItem
            onSelect={() => onCreateSession(null)}
            className="gap-2 text-primary focus:text-primary focus:bg-primary/10"
          >
            <Sparkles className="size-3.5" />
            <span className="text-xs font-medium">
              {t("autoResearch.sidebar.newResearch")}
            </span>
          </DropdownMenuItem>
        </div>

        <DropdownMenuSeparator />

        {/* 会话树 / 搜索结果 */}
        <div className="max-h-[52vh] overflow-y-auto p-1">
          {hasFilter ? (
            <SwitcherSearch
              keyword={kw}
              activeId={activeSession?.id ?? null}
              onPick={pick}
            />
          ) : (
            <>
              {folders.map((folder) => (
                <SwitcherFolder
                  key={folder.id}
                  folderId={folder.id}
                  label={folder.name}
                  totalCount={folder.session_count ?? 0}
                  activeId={activeSession?.id ?? null}
                  collapsed={isCollapsed(folder.id, folder.id === activeFolderId)}
                  onToggle={() =>
                    setCollapsed((s) => ({
                      ...s,
                      [folder.id]: !isCollapsed(
                        folder.id,
                        folder.id === activeFolderId,
                      ),
                    }))
                  }
                  onPick={pick}
                />
              ))}
              {ungroupedCount > 0 && (
                <SwitcherFolder
                  folderId={null}
                  label={t("autoResearch.sidebar.ungrouped")}
                  totalCount={ungroupedCount}
                  activeId={activeSession?.id ?? null}
                  collapsed={!!collapsed.__ungrouped__}
                  onToggle={() =>
                    setCollapsed((s) => ({
                      ...s,
                      __ungrouped__: !s.__ungrouped__,
                    }))
                  }
                  onPick={pick}
                  // 有分组时给未分组一个标题头；无分组时它就是唯一列表
                  showHeader={folders.length > 0}
                />
              )}
              {folders.length === 0 && ungroupedCount === 0 && (
                <p className="px-3 py-6 text-center text-xs text-muted-foreground/60">
                  {t("autoResearch.sidebar.noSessions")}
                </p>
              )}
            </>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 切换器里的单个文件夹分组：展开懒加载 + 游标翻页。 */
function SwitcherFolder({
  folderId,
  label,
  totalCount,
  activeId,
  collapsed,
  onToggle,
  onPick,
  showHeader = true,
}: {
  folderId: string | null
  label: string
  totalCount: number
  activeId: string | null
  collapsed: boolean
  onToggle: () => void
  onPick: (id: string) => void
  showHeader?: boolean
}) {
  const { t } = useTranslation()
  const query = useInfiniteFolderSessions(folderId, { enabled: !collapsed })
  const sessions = useMemo(
    () => (query.data?.pages ?? []).flatMap((p) => p.items ?? []),
    [query.data],
  )

  return (
    <div className="mb-0.5">
      {showHeader && (
        <button
          type="button"
          onClick={onToggle}
          className="flex w-full items-center gap-1 px-2 h-7 rounded text-muted-foreground/80 hover:text-foreground transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="size-3 shrink-0" />
          ) : (
            <ChevronDown className="size-3 shrink-0" />
          )}
          <Folder className="size-3.5 shrink-0 text-amber-500" />
          <span className="truncate text-xs font-medium flex-1 text-left">
            {label}
          </span>
          <span className="text-xs text-muted-foreground/50 tabular-nums shrink-0">
            {totalCount}
          </span>
        </button>
      )}
      {!collapsed && (
        <div
          className={cn(showHeader && "ml-2 pl-2 border-l border-border/40")}
        >
          {query.isLoading && (
            <div className="flex items-center gap-1.5 px-2 py-1.5 text-[11px] text-muted-foreground/50">
              <Loader2 className="size-3 animate-spin" />
              {t("autoResearch.sidebar.loading")}
            </div>
          )}
          {sessions.map((s) => (
            <SwitcherRow
              key={s.id}
              session={s}
              active={s.id === activeId}
              onSelect={onPick}
            />
          ))}
          {query.hasNextPage && (
            <SwitcherLoadMore
              loading={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            />
          )}
        </div>
      )}
    </div>
  )
}

/** 切换器的搜索态：扁平跨文件夹分页。 */
function SwitcherSearch({
  keyword,
  activeId,
  onPick,
}: {
  keyword: string
  activeId: string | null
  onPick: (id: string) => void
}) {
  const { t } = useTranslation()
  const query = useInfiniteSearchSessions(
    { q: keyword, status: [] },
    { enabled: true },
  )
  const sessions = useMemo(
    () => (query.data?.pages ?? []).flatMap((p) => p.items ?? []),
    [query.data],
  )

  if (query.isLoading) {
    return (
      <div className="flex items-center justify-center gap-1.5 py-6 text-[11px] text-muted-foreground/60">
        <Loader2 className="size-4 animate-spin" />
        {t("autoResearch.sidebar.loading")}
      </div>
    )
  }
  if (sessions.length === 0) {
    return (
      <p className="px-3 py-6 text-center text-xs text-muted-foreground/60">
        {t("autoResearch.sidebar.noResults")}
      </p>
    )
  }
  return (
    <>
      {sessions.map((s) => (
        <SwitcherRow
          key={s.id}
          session={s}
          active={s.id === activeId}
          onSelect={onPick}
        />
      ))}
      {query.hasNextPage && (
        <SwitcherLoadMore
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        />
      )}
    </>
  )
}

/** 「加载更多」行。 */
function SwitcherLoadMore({
  loading,
  onClick,
}: {
  loading: boolean
  onClick: () => void
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="w-full flex items-center justify-center gap-1.5 py-1.5 text-[11px] text-muted-foreground hover:text-primary transition-colors disabled:opacity-60"
    >
      {loading ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <ChevronDown className="size-3" />
      )}
      {t("autoResearch.chat.loadMore")}
    </button>
  )
}

/** 切换器里的单个会话行：状态点 + 标题（hover 显示全称）+ 最优目标。 */
function SwitcherRow({
  session,
  active,
  onSelect,
}: {
  session: ResearchSessionItem
  active: boolean
  onSelect: (id: string) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(session.id)}
      title={session.title}
      className={cn(
        "flex w-full items-center gap-2 pl-2 pr-2 h-8 rounded-md text-xs transition-colors",
        active
          ? "bg-primary/10 text-primary"
          : "text-foreground/80 hover:bg-accent/60 hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full shrink-0",
          sessionDotClasses(session.status),
        )}
      />
      <span className="flex-1 truncate text-left">{session.title}</span>
      {session.best_objective != null && (
        <span className="shrink-0 text-xs font-mono tabular-nums text-emerald-500/90">
          {session.best_objective}
        </span>
      )}
    </button>
  )
}
