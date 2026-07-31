/**
 * 自动科研（AutoResearch）相关的 React Query hooks。
 *
 * 集中封装 folders / sessions / turns / messages / state / artifacts 六组端点，
 * 并给出统一的 query key 工厂，方便在 mutation 后精确失效缓存。
 *
 * 全部对齐 `AUTORESEARCH_API.md` 描述的新接口（`Llm4AdResearchService`）。
 */

import {
  type QueryClient,
  type UseQueryOptions,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"

import {
  Llm4AdResearchService,
  type ResearchCollabStartRequest,
  type ResearchArtifactTranslateRequest,
  type ResearchArtifactTranslateResponse,
  type ResearchCollabStartResponse,
  type ResearchFolderCreateRequest,
  type ResearchFolderListResponse,
  type ResearchFolderReorderRequest,
  type ResearchFolderUpdateRequest,
  type ResearchLogItem,
  type ResearchSessionCreateRequest,
  type ResearchSessionDetailResponse,
  type ResearchSessionItem,
  type ResearchSessionListResponse,
  type ResearchSessionStatus,
  type ResearchSessionUpdateRequest,
  type ResearchStageGuideRequest,
  type ResearchStateResponse,
  type ResearchTurnItem,
  type ResearchTurnRetryRequest,
  type ResearchTurnStartRequest,
  type ResearchTurnStartResponse,
} from "@/client"
import { authFetch } from "@/utils/auth"

// ---- Query key 工厂 ----

export const researchKeys = {
  all: ["autoresearch"] as const,
  folders: () => [...researchKeys.all, "folders"] as const,
  sessions: (params?: {
    folderId?: string | null
    ungrouped?: boolean
    status?: ResearchSessionStatus[]
    q?: string
  }) => [...researchKeys.all, "sessions", params ?? {}] as const,
  /** 某文件夹（或未分组）的会话无限分页列表。folderId=null → 未分组。 */
  folderSessions: (folderId: string | null) =>
    [
      ...researchKeys.all,
      "folderSessions",
      folderId ?? "__ungrouped__",
    ] as const,
  /** 搜索/筛选态的扁平跨文件夹分页列表（键含关键词 + 状态集合）。 */
  searchSessions: (q: string, status: ResearchSessionStatus[]) =>
    [
      ...researchKeys.all,
      "searchSessions",
      { q, status: [...status].sort() },
    ] as const,
  sessionDetail: (sessionId: string) =>
    [...researchKeys.all, "session", sessionId] as const,
  sessionMessages: (sessionId: string) =>
    [...researchKeys.all, "messages", sessionId] as const,
  state: (sessionId: string) =>
    [...researchKeys.all, "state", sessionId] as const,
  turns: (sessionId: string) =>
    [...researchKeys.all, "turns", sessionId] as const,
  turn: (sessionId: string, turnId: string) =>
    [...researchKeys.all, "turn", sessionId, turnId] as const,
  artifacts: (sessionId: string) =>
    [...researchKeys.all, "artifacts", sessionId] as const,
  artifactTree: (sessionId: string) =>
    [...researchKeys.all, "artifactTree", sessionId] as const,
  generated: (sessionId: string) =>
    [...researchKeys.all, "generated", sessionId] as const,
  analysis: (sessionId: string) =>
    [...researchKeys.all, "analysis", sessionId] as const,
}

// ---- 通用 helpers ----

/**
 * 失效所有会话列表相关缓存（文件夹分页 / 搜索分页 / 旧的扁平 sessions），
 * 并刷新文件夹计数。用于新建 / 删除 / 移动会话、turn 结束后同步侧栏。
 *
 * 用前缀匹配一次性覆盖所有 folderSessions/searchSessions 分页 key，避免逐个文件夹
 * 精确失效。React Query 默认按 key 前缀模糊匹配。
 *
 * 关键：只失效「列表 + 文件夹计数」，**不**碰当前打开会话的 messages / state /
 * turns / detail。否则在侧栏对其它会话重命名/移动/删除、或任意 turn 生命周期变更时，
 * 会连带把正在浏览的会话多页消息流全部 refetch，造成滚动跳动与网络放大。
 */
function invalidateSessionListsOn(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: [...researchKeys.all, "folderSessions"] })
  qc.invalidateQueries({ queryKey: [...researchKeys.all, "searchSessions"] })
  qc.invalidateQueries({ queryKey: [...researchKeys.all, "sessions"] })
  qc.invalidateQueries({ queryKey: researchKeys.folders() })
}

/** 让 mutation 后失效相关缓存。 */
function useInvalidator() {
  const qc = useQueryClient()
  return {
    // 仅失效会话列表 + 文件夹计数（不牵连当前会话的消息流 / state / 详情）。
    invalidateSessions: () => invalidateSessionListsOn(qc),
    invalidateSessionDetail: (sessionId: string) => {
      qc.invalidateQueries({ queryKey: researchKeys.sessionDetail(sessionId) })
      qc.invalidateQueries({ queryKey: researchKeys.state(sessionId) })
      qc.invalidateQueries({
        queryKey: researchKeys.sessionMessages(sessionId),
      })
      qc.invalidateQueries({ queryKey: researchKeys.turns(sessionId) })
    },
    invalidateFolders: () =>
      qc.invalidateQueries({ queryKey: researchKeys.folders() }),
  }
}

/**
 * 失效会话列表相关缓存的独立 hook（供页面级 handler 直接调用）。
 * 与 {@link useInvalidator} 的 `invalidateSessions` 同实现。
 */
export function useInvalidateSessionLists() {
  const qc = useQueryClient()
  return useCallback(() => invalidateSessionListsOn(qc), [qc])
}

// ---- Folders ----

export function useResearchFolders(
  opts?: Omit<
    UseQueryOptions<ResearchFolderListResponse>,
    "queryKey" | "queryFn"
  >,
) {
  return useQuery({
    queryKey: researchKeys.folders(),
    queryFn: () => Llm4AdResearchService.listFolders(),
    staleTime: 30_000,
    ...opts,
  })
}

export function useCreateResearchFolder() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: (body: ResearchFolderCreateRequest) =>
      Llm4AdResearchService.createFolder({ requestBody: body }),
    onSuccess: () => {
      inv.invalidateFolders()
    },
  })
}

export function useUpdateResearchFolder() {
  const inv = useInvalidator()
  return useMutation({
    // 新接口直接接收 ResearchFolderUpdateRequest；PATCH 语义：未提供的键不改，
    // 显式传 parent_id=null 表示移到根。JS 对象里出现的键即为「显式提供」。
    mutationFn: ({
      folderId,
      body,
    }: {
      folderId: string
      body: ResearchFolderUpdateRequest
    }) => Llm4AdResearchService.updateFolder({ folderId, requestBody: body }),
    onSuccess: () => {
      inv.invalidateFolders()
      inv.invalidateSessions()
    },
  })
}

export function useDeleteResearchFolder() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: (folderId: string) =>
      Llm4AdResearchService.deleteFolder({ folderId }),
    onSuccess: () => {
      inv.invalidateFolders()
      inv.invalidateSessions()
    },
  })
}

export function useReorderResearchFolders() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: (body: ResearchFolderReorderRequest) =>
      Llm4AdResearchService.reorderFolders({ requestBody: body }),
    onSuccess: () => {
      inv.invalidateFolders()
    },
  })
}

// ---- Sessions ----

export function useResearchSessions(
  params?: {
    folderId?: string | null
    ungrouped?: boolean
    status?: ResearchSessionStatus[]
    q?: string
    limit?: number
  },
  opts?: Omit<
    UseQueryOptions<ResearchSessionListResponse>,
    "queryKey" | "queryFn"
  >,
) {
  const limit = params?.limit ?? 100
  return useQuery({
    queryKey: researchKeys.sessions(params),
    queryFn: () =>
      Llm4AdResearchService.listSessions({
        folderId: params?.folderId ?? undefined,
        ungrouped: params?.ungrouped ?? false,
        statuses: params?.status ?? undefined,
        q: params?.q ?? undefined,
        limit,
      }),
    staleTime: 15_000,
    ...opts,
  })
}

/** 会话列表分页默认每页条数（企业级：文件夹级懒加载，逐页拉取）。 */
export const SESSIONS_PAGE_SIZE = 10

/**
 * 某文件夹（或未分组）的会话无限分页。
 *
 * folderId=null → 未分组（ungrouped=true）；否则按 folder_id 过滤。走后端游标分页
 * （cursor=上一页最后一条 updated_time，next_cursor 为 None 时无更多）。默认每页
 * ``SESSIONS_PAGE_SIZE`` 条；``enabled`` 关闭时（分组折叠）不请求，实现懒加载。
 */
export function useInfiniteFolderSessions(
  folderId: string | null,
  opts?: { enabled?: boolean; pageSize?: number },
) {
  const pageSize = opts?.pageSize ?? SESSIONS_PAGE_SIZE
  return useInfiniteQuery({
    queryKey: researchKeys.folderSessions(folderId),
    queryFn: ({ pageParam }) =>
      Llm4AdResearchService.listSessions({
        folderId: folderId ?? undefined,
        ungrouped: folderId === null,
        cursor: (pageParam as string | undefined) ?? undefined,
        limit: pageSize,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: opts?.enabled ?? true,
    staleTime: 15_000,
  })
}

/**
 * 搜索 / 筛选态的扁平跨文件夹会话分页。
 *
 * 命中关键词或状态筛选时，不按文件夹分桶，跨所有文件夹用 q + statuses 查询并游标
 * 分页。``enabled`` 由「是否处于检索态」控制。
 */
export function useInfiniteSearchSessions(
  params: { q: string; status: ResearchSessionStatus[] },
  opts?: { enabled?: boolean; pageSize?: number },
) {
  const pageSize = opts?.pageSize ?? SESSIONS_PAGE_SIZE
  return useInfiniteQuery({
    queryKey: researchKeys.searchSessions(params.q, params.status),
    queryFn: ({ pageParam }) =>
      Llm4AdResearchService.listSessions({
        q: params.q || undefined,
        statuses: params.status.length ? params.status : undefined,
        cursor: (pageParam as string | undefined) ?? undefined,
        limit: pageSize,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: opts?.enabled ?? true,
    staleTime: 10_000,
  })
}

/**
 * 会话详情（会话元数据 + active_turn）。默认不拉消息——消息走独立的
 * ``useResearchSessionMessages`` 无限分页，避免详情刷新时抖动整段历史。
 */
export function useResearchSessionDetail(
  sessionId: string | null,
  opts?: Omit<
    UseQueryOptions<ResearchSessionDetailResponse>,
    "queryKey" | "queryFn" | "enabled"
  >,
) {
  return useQuery({
    queryKey: sessionId
      ? researchKeys.sessionDetail(sessionId)
      : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.getSession({ sessionId: sessionId as string }),
    enabled: !!sessionId,
    staleTime: 5_000,
    ...opts,
  })
}

/**
 * 会话级历史消息，游标分页（越翻越旧）。
 *
 * 走独立的 ``GET /sessions/{id}/messages`` 端点（不再借 ``getSession`` 附带
 * 分页），支持类型过滤，让消息列表与日志面板各建一个查询、各自分页，互不饥饿：
 * 消息主列表默认不传过滤条件，保留 log 与其它持久化事件；日志面板如需独立
 * 翻页可另传 ``eventType=["log"]``。
 *
 * ``has_more`` 为真时用本页最早一条消息 id 做 ``before`` 继续往前翻。React Query
 * 把「更旧的一页」作为下一页（``fetchNextPage``），组件渲染时把 pages 反转拼接
 * 即得升序全量。``kind`` 参与 query key，两个查询缓存互不覆盖。
 */
export function useResearchSessionMessages(
  sessionId: string | null,
  opts?: {
    pageSize?: number
    kind?: string
    eventType?: string[]
  },
) {
  const pageSize = opts?.pageSize ?? 100
  const kind = opts?.kind ?? "all"
  return useInfiniteQuery({
    queryKey: sessionId
      ? [...researchKeys.sessionMessages(sessionId), kind]
      : [...researchKeys.all, "messages", "none", kind],
    queryFn: ({ pageParam }) =>
      // 统一 listMessages：不传 turn_id = 会话级；order=desc 历史往旧翻，
      // 游标用后端回传的 next_cursor 原样续传（不再手拼消息 id）。log 已拆到
      // /logs 端点，本端点天然不含 log，无需再排除。
      Llm4AdResearchService.listMessages({
        sessionId: sessionId as string,
        limit: pageSize,
        order: "desc",
        cursor: (pageParam as string | undefined) ?? undefined,
        eventType: opts?.eventType ?? undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: !!sessionId,
    staleTime: 5_000,
  })
}

// ---- 日志（独立 research_log 表，双端游标窗口） ----

/** {@link useResearchLogs} 返回的日志控制器。 */
export interface ResearchLogsController {
  /**
   * 已累积日志。前端不排序、不去重，完全按后端返回顺序拼接：顶部（更旧）
   * 前插、底部（更新）追加。依赖后端每页内升序 + 两端游标为排他边界。
   */
  entries: ResearchLogItem[]
  /** 首屏（或筛选变更后重载）加载中。 */
  isLoading: boolean
  /** 首屏是否已取过一次（成功/失败/空都算）。用于判断续传探针已 settle。 */
  hasLoaded: boolean
  /** 正在向上加载更旧一页。 */
  isFetchingOlder: boolean
  /** 正在向下加载更新一页。 */
  isFetchingLatest: boolean
  /** 上方是否还有更旧的日志可加载。 */
  hasOlder: boolean
  /** 加载出错信息（首屏）。 */
  error: string | null
  /** 向上翻：用最旧游标 + order=desc 取更旧一页并前插。 */
  loadOlder: () => void
  /** 向下翻：用最新游标 + order=asc 取更新一页并追加（无更多也无妨）。 */
  loadLatest: () => void
  /** 丢弃当前累积并按当前筛选从最新一页重载。 */
  reload: () => void
}

/**
 * 日志双端游标分页 hook（对齐后端 ``listLogs`` 的对称双向游标窗口）。
 *
 * 后端每次返回 ``items``（恒升序）+ 两端游标：``older_cursor`` 指向本批最旧、
 * ``newer_cursor`` 指向本批最新，配合 ``has_older`` / ``has_newer``。本 hook **不排序、
 * 不去重**，完全按后端返回顺序拼接：向上翻用 ``older_cursor`` + ``order=desc`` 把新一批
 * 前插到最前，向下翻用 ``newer_cursor`` + ``order=asc`` 追加到最后，游标全部原样回传、
 * 不手拼。依赖后端每页内升序 + 两端游标为排他边界（相邻页不重叠）。
 *
 * - 首屏（及 turnId/level/q 变更后）以 ``order=desc`` 无游标拉最新一页；
 * - ``loadOlder`` 前插更旧一页，``hasOlder`` 反映是否还有更旧；
 * - ``loadLatest`` 追加更新一页，「获取最新」按钮恒可点（拿不到也无妨）。
 *
 * 未走 React Query：需要「向上/向下双向累积 + 恒显示获取最新」的手控语义，
 * useInfiniteQuery 的单向 hasNextPage 表达不了，手管游标 + 请求代次更清晰。
 */
export function useResearchLogs(
  sessionId: string | null,
  opts?: {
    turnId?: string | null
    level?: string[]
    q?: string
    limit?: number
    enabled?: boolean
  },
): ResearchLogsController {
  const turnId = opts?.turnId ?? null
  const level = opts?.level
  const q = opts?.q ?? ""
  const limit = opts?.limit ?? 100
  const enabled = opts?.enabled ?? true

  const [entries, setEntries] = useState<ResearchLogItem[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isFetchingOlder, setIsFetchingOlder] = useState(false)
  const [isFetchingLatest, setIsFetchingLatest] = useState(false)
  const [hasOlder, setHasOlder] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 首屏是否「已取过一次」（成功/失败/空都算，仅未发起或 disabled 为 false）。
  // 调用方据此判断续传探针已 settle，可安全据 entries 计算 last_id 后再连 SSE，
  // 避免「isLoading 初始 false」造成的假 settle 窗口。
  const [hasLoaded, setHasLoaded] = useState(false)

  // 窗口两端游标（后端原样回传，不手拼）。
  const olderCursorRef = useRef<string | null>(null)
  const newerCursorRef = useRef<string | null>(null)
  // 请求代次：筛选变更即自增，用于丢弃过期响应。
  const genRef = useRef(0)
  // 并发闸门，防止同方向重复触发。
  const olderBusyRef = useRef(false)
  const latestBusyRef = useRef(false)

  // 筛选签名：任一变化都重载。level 数组转字符串参与依赖。
  const levelKey = level && level.length ? [...level].sort().join(",") : ""
  const filterKey = `${sessionId ?? ""}|${turnId ?? ""}|${levelKey}|${q}|${enabled}`

  // 把最新筛选值存 ref，供翻页回调读取（避免闭包过期）。
  const paramsRef = useRef({ sessionId, turnId, level, q, limit })
  paramsRef.current = { sessionId, turnId, level, q, limit }

  // 首屏 / 筛选变更：以 order=desc 无游标拉最新一页。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 以 filterKey 聚合筛选依赖
  useEffect(() => {
    if (!sessionId || !enabled) {
      setEntries([])
      setHasOlder(false)
      setError(null)
      setHasLoaded(false)
      olderCursorRef.current = null
      newerCursorRef.current = null
      return
    }
    const gen = ++genRef.current
    setIsLoading(true)
    // 新一轮首屏开始：标记未 settle，待成功/失败后再置 true。换筛选（含换 turn）
    // 时据此让依赖它的 SSE gating 短暂掉下来、待新探针 settle 再连，避免用旧 last_id。
    setHasLoaded(false)
    setError(null)
    const p = paramsRef.current
    Llm4AdResearchService.listLogs({
      sessionId,
      turnId: p.turnId ?? undefined,
      order: "desc",
      limit: p.limit,
      level: p.level && p.level.length ? p.level : undefined,
      q: p.q || undefined,
    })
      .then((page) => {
        if (gen !== genRef.current) return
        // 首屏直接用后端返回（页内已升序），不做任何排序。
        setEntries(page.items ?? [])
        olderCursorRef.current = page.older_cursor ?? null
        newerCursorRef.current = page.newer_cursor ?? null
        setHasOlder(!!page.has_older)
        setIsLoading(false)
        setHasLoaded(true)
      })
      .catch((e: unknown) => {
        if (gen !== genRef.current) return
        setError(e instanceof Error ? e.message : String(e))
        setIsLoading(false)
        // 失败也算 settle：让 SSE 回退 0-0 全量重放，胜过永久卡住不连。
        setHasLoaded(true)
      })
  }, [filterKey])

  const loadOlder = useCallback(() => {
    const p = paramsRef.current
    if (!p.sessionId || olderBusyRef.current) return
    if (!olderCursorRef.current) return
    olderBusyRef.current = true
    const gen = genRef.current
    setIsFetchingOlder(true)
    Llm4AdResearchService.listLogs({
      sessionId: p.sessionId,
      turnId: p.turnId ?? undefined,
      order: "desc",
      cursor: olderCursorRef.current,
      limit: p.limit,
      level: p.level && p.level.length ? p.level : undefined,
      q: p.q || undefined,
    })
      .then((page) => {
        if (gen !== genRef.current) return
        const items = page.items ?? []
        // 更旧的一批直接前插到最前（页内升序，整体仍升序）。
        if (items.length) setEntries((prev) => [...items, ...prev])
        olderCursorRef.current = page.older_cursor ?? olderCursorRef.current
        setHasOlder(!!page.has_older)
      })
      .finally(() => {
        olderBusyRef.current = false
        if (gen === genRef.current) setIsFetchingOlder(false)
      })
  }, [])

  const loadLatest = useCallback(() => {
    const p = paramsRef.current
    if (!p.sessionId || latestBusyRef.current) return
    latestBusyRef.current = true
    const gen = genRef.current
    setIsFetchingLatest(true)
    // 有最新游标 → order=asc 取更新一页；无（极端兜底）→ desc 无游标重取最新一页。
    const cursor = newerCursorRef.current
    Llm4AdResearchService.listLogs({
      sessionId: p.sessionId,
      turnId: p.turnId ?? undefined,
      order: cursor ? "asc" : "desc",
      cursor: cursor ?? undefined,
      limit: p.limit,
      level: p.level && p.level.length ? p.level : undefined,
      q: p.q || undefined,
    })
      .then((page) => {
        if (gen !== genRef.current) return
        const items = page.items ?? []
        // 更新的一批直接追加到最后（页内升序，整体仍升序）。
        if (items.length) setEntries((prev) => [...prev, ...items])
        // 仅在真的取到更新一批时推进最新游标，避免回退到旧位。
        if (page.newer_cursor) newerCursorRef.current = page.newer_cursor
      })
      .finally(() => {
        latestBusyRef.current = false
        if (gen === genRef.current) setIsFetchingLatest(false)
      })
  }, [])

  const reload = useCallback(() => {
    genRef.current++
    // 触发首屏 effect 之外的手动重载：直接复用首屏路径。
    const p = paramsRef.current
    if (!p.sessionId) return
    const gen = genRef.current
    setIsLoading(true)
    setError(null)
    Llm4AdResearchService.listLogs({
      sessionId: p.sessionId,
      turnId: p.turnId ?? undefined,
      order: "desc",
      limit: p.limit,
      level: p.level && p.level.length ? p.level : undefined,
      q: p.q || undefined,
    })
      .then((page) => {
        if (gen !== genRef.current) return
        setEntries(page.items ?? [])
        olderCursorRef.current = page.older_cursor ?? null
        newerCursorRef.current = page.newer_cursor ?? null
        setHasOlder(!!page.has_older)
        setIsLoading(false)
      })
      .catch((e: unknown) => {
        if (gen !== genRef.current) return
        setError(e instanceof Error ? e.message : String(e))
        setIsLoading(false)
      })
  }, [])

  return {
    entries,
    isLoading,
    hasLoaded,
    isFetchingOlder,
    isFetchingLatest,
    hasOlder,
    error,
    loadOlder,
    loadLatest,
    reload,
  }
}

export function useCreateResearchSession() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: (body: ResearchSessionCreateRequest) =>
      Llm4AdResearchService.createSession({ requestBody: body }),
    onSuccess: () => {
      inv.invalidateSessions()
    },
  })
}

export function useUpdateResearchSession() {
  const inv = useInvalidator()
  return useMutation({
    // 新接口直接接收 ResearchSessionUpdateRequest；folder_id=null 显式移到未分组，
    // 不提供该键则不改。
    mutationFn: ({
      sessionId,
      body,
    }: {
      sessionId: string
      body: ResearchSessionUpdateRequest
    }) => Llm4AdResearchService.updateSession({ sessionId, requestBody: body }),
    onSuccess: (updated: ResearchSessionItem) => {
      inv.invalidateSessions()
      inv.invalidateSessionDetail(updated.id)
    },
  })
}

export function useDeleteResearchSession() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: (sessionId: string) =>
      Llm4AdResearchService.deleteSession({ sessionId }),
    onSuccess: (_, sessionId) => {
      inv.invalidateSessions()
      inv.invalidateSessionDetail(sessionId)
    },
  })
}

// ---- Turns ----

export function useStartResearchTurn() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: ({
      sessionId,
      body,
    }: {
      sessionId: string
      body: ResearchTurnStartRequest
    }) =>
      Llm4AdResearchService.startTurn({
        sessionId,
        requestBody: body,
      }),
    onSuccess: (resp: ResearchTurnStartResponse) => {
      inv.invalidateSessionDetail(resp.session.id)
      inv.invalidateSessions()
    },
  })
}

export function useStopResearchTurn() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: ({
      sessionId,
      turnId,
    }: {
      sessionId: string
      turnId: string
    }) => Llm4AdResearchService.stopTurn({ sessionId, turnId }),
    onSuccess: (_, vars) => {
      inv.invalidateSessionDetail(vars.sessionId)
      inv.invalidateSessions()
    },
  })
}

export function useRetryResearchTurn() {
  const inv = useInvalidator()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      sessionId,
      turnId,
      body,
    }: {
      sessionId: string
      turnId: string
      body?: ResearchTurnRetryRequest
    }) =>
      Llm4AdResearchService.retryTurn({
        sessionId,
        turnId,
        requestBody: body ?? {},
      }),
    onSuccess: (resp: ResearchTurnStartResponse) => {
      // 重试复用同一 turn_id：先用响应里的新 session/turn 同步 seed detail 缓存，
      // 让 activeTurn.status 立刻变 running（不等异步 refetch 落地），SSE 订阅门槛
      // (sseEnabled) 才能在同一 render 翻 true；再走常规失效补齐其余数据。
      qc.setQueryData<ResearchSessionDetailResponse>(
        researchKeys.sessionDetail(resp.session.id),
        (prev) =>
          prev
            ? { ...prev, session: resp.session, active_turn: resp.turn }
            : { session: resp.session, active_turn: resp.turn },
      )
      inv.invalidateSessionDetail(resp.session.id)
      inv.invalidateSessions()
    },
  })
}

// ---- Collaborate Agent ----

export function useStartCollab() {
  const inv = useInvalidator()
  return useMutation({
    mutationFn: ({
      sessionId,
      body,
    }: {
      sessionId: string
      body: ResearchCollabStartRequest
    }) => Llm4AdResearchService.startCollab({ sessionId, requestBody: body }),
    onSuccess: (resp: ResearchCollabStartResponse) => {
      inv.invalidateSessionDetail(resp.session.id)
      inv.invalidateSessions()
    },
  })
}

/** 会话下所有轮次（倒序），运行历史 / 多轮对比用。 */
export function useResearchTurns(sessionId: string | null, limit = 30) {
  return useQuery({
    queryKey: sessionId
      ? researchKeys.turns(sessionId)
      : [...researchKeys.all, "turns", "none"],
    queryFn: () =>
      Llm4AdResearchService.listTurns({
        sessionId: sessionId as string,
        limit,
      }),
    enabled: !!sessionId,
    staleTime: 10_000,
  })
}

export function useResearchTurn(
  sessionId: string | null,
  turnId: string | null,
  opts?: Omit<
    UseQueryOptions<ResearchTurnItem>,
    "queryKey" | "queryFn" | "enabled"
  >,
) {
  return useQuery({
    queryKey:
      sessionId && turnId
        ? researchKeys.turn(sessionId, turnId)
        : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.getTurn({
        sessionId: sessionId as string,
        turnId: turnId as string,
      }),
    enabled: !!(sessionId && turnId),
    ...opts,
  })
}

// ---- Stage 引导注入（HITL） ----

export function useInjectStageGuidance() {
  return useMutation({
    mutationFn: ({
      sessionId,
      stageNum,
      body,
    }: {
      sessionId: string
      stageNum: number
      body: ResearchStageGuideRequest
    }) =>
      Llm4AdResearchService.injectStageGuidance({
        sessionId,
        stageNum,
        requestBody: body,
      }),
  })
}

// ---- State snapshot ----

export function useResearchState(
  sessionId: string | null,
  opts?: Omit<
    UseQueryOptions<ResearchStateResponse>,
    "queryKey" | "queryFn" | "enabled"
  >,
) {
  return useQuery({
    queryKey: sessionId ? researchKeys.state(sessionId) : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.getState({ sessionId: sessionId as string }),
    enabled: !!sessionId,
    staleTime: 5_000,
    ...opts,
  })
}

// ---- Artifacts ----

export function useResearchArtifacts(sessionId: string | null) {
  return useQuery({
    queryKey: sessionId ? researchKeys.artifacts(sessionId) : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.listArtifacts({
        sessionId: sessionId as string,
      }),
    enabled: !!sessionId,
    staleTime: 15_000,
  })
}

export function useResearchArtifactTree(sessionId: string | null) {
  return useQuery({
    queryKey: sessionId
      ? researchKeys.artifactTree(sessionId)
      : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.artifactTree({
        sessionId: sessionId as string,
      }),
    enabled: !!sessionId,
    staleTime: 30_000,
  })
}

/**
 * 实验产物（generated 演化解），内容内联、按 stage 分组。
 *
 * 一次拿全 ``generated*.json`` 解（后端已剥离大字段），供实验面板画演化仿真 /
 * 趋势图。running/paused 时可传 ``poll`` 兜底轮询新解。
 */
export function useResearchGenerated(
  sessionId: string | null,
  poll = false,
  enabled = true,
) {
  return useQuery({
    queryKey: sessionId ? researchKeys.generated(sessionId) : researchKeys.all,
    queryFn: () =>
      Llm4AdResearchService.listGenerated({
        sessionId: sessionId as string,
      }),
    enabled: !!sessionId && enabled,
    staleTime: 15_000,
    refetchInterval: poll ? 15_000 : false,
  })
}

// ---- 文件下载 helpers（产物 / config.yaml） ----

const API_BASE = `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/research`

/**
 * 通过 ``authFetch`` 拉取二进制并触发浏览器下载。
 *
 * SDK 里 download* 端点声明返回 ``unknown`` 且会尝试 JSON 解析，直接用不合适；
 * 这里手动走 blob，保留 Bearer 头。
 */
async function downloadBlob(url: string, filename: string) {
  const resp = await authFetch(url)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}

/** 下载单个产物文件（相对 run_dir 路径）。 */
export function downloadResearchArtifact(sessionId: string, path: string) {
  const url = `${API_BASE}/sessions/${sessionId}/artifacts/download?path=${encodeURIComponent(
    path,
  )}`
  const filename = path.split("/").pop() || "artifact"
  return downloadBlob(url, filename)
}

/** 打包下载会话全部产物（zip；文件名由后端 Content-Disposition 决定，此处兜底）。 */
export function downloadResearchArtifactsArchive(sessionId: string) {
  const url = `${API_BASE}/sessions/${sessionId}/artifacts/archive`
  return downloadBlob(url, `artifacts-${sessionId.slice(0, 8)}.zip`)
}

/**
 * 拉取单个产物文件的原始响应（预览用）。
 *
 * 复用下载端点（后端直接回文件字节），调用方按需读 ``text()`` / ``blob()`` 以
 * 在弹框内渲染文本 / 图片 / PDF。保留 Bearer 头。
 */
export async function fetchResearchArtifact(
  sessionId: string,
  path: string,
): Promise<Response> {
  const url = `${API_BASE}/sessions/${sessionId}/artifacts/download?path=${encodeURIComponent(
    path,
  )}`
  const resp = await authFetch(url)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp
}

/** 触发单个产物文件翻译：命中缓存直接回内容，否则返回 source_hash 供 SSE 拉流。 */
export function translateResearchArtifact(
  sessionId: string,
  path: string,
  body: ResearchArtifactTranslateRequest,
): Promise<ResearchArtifactTranslateResponse> {
  return Llm4AdResearchService.translateArtifact({
    sessionId,
    path,
    requestBody: body,
  })
}

/** 停止在跑的产物翻译：清后端 generation_id，让翻译协程协作式退出、不落缓存。 */
export function stopTranslateResearchArtifact(
  sessionId: string,
  sourceHash: string,
  targetLanguage: string,
): Promise<unknown> {
  return Llm4AdResearchService.stopTranslateArtifact({
    sessionId,
    sourceHash,
    targetLanguage,
  })
}

/** 构造产物翻译 SSE 地址（实际拉流仍由 ``authFetch`` 执行以保留 Bearer 头）。 */
export function buildResearchArtifactTranslateStreamUrl(
  sessionId: string,
  sourceHash: string,
  targetLanguage: string,
): string {
  return `${API_BASE}/sessions/${sessionId}/artifacts/translate/stream?source_hash=${encodeURIComponent(
    sourceHash,
  )}&target_language=${encodeURIComponent(targetLanguage)}`
}

/** 下载会话的 config.arc.yaml（调试用）。 */
export function downloadResearchConfig(sessionId: string) {
  const url = `${API_BASE}/sessions/${sessionId}/config.yaml`
  return downloadBlob(url, "config.arc.yaml")
}
