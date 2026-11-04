import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  ArrowDownAZ,
  ArrowUpAZ,
  FolderOpen,
  LayoutGrid,
  List,
  Search,
  X,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdProjectsService } from "@/client"
import { PageNumbers } from "@/components/Common/PageNumbers"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import AddProject from "@/components/Projects/AddProject"
import DemoProjectCard from "@/components/Projects/DemoProjectCard"
import ProjectCard from "@/components/Projects/ProjectCard"
import ProjectListItem from "@/components/Projects/ProjectListItem"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/projects")({
  component: ProjectsPage,
  head: () => ({
    meta: [
      {
        title: "Algorithm Evolution Projects - LLM4AD_Next",
      },
    ],
  }),
})

const DEFAULT_PAGE_SIZE = 12

type SortField = "created_time" | "name"
type SortOrder = "asc" | "desc"
type ViewMode = "card" | "list"

const VIEW_MODE_STORAGE_KEY = "projects.viewMode"

function getInitialViewMode(): ViewMode {
  if (typeof window === "undefined") return "card"
  const saved = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY)
  return saved === "list" ? "list" : "card"
}

function ProjectsPage() {
  const { t } = useTranslation()
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState<SortField>("created_time")
  const [order, setOrder] = useState<SortOrder>("desc")
  const [viewMode, setViewMode] = useState<ViewMode>(getInitialViewMode)

  // Persist view mode
  useEffect(() => {
    window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, viewMode)
  }, [viewMode])

  // Debounce search input -> actual query
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim())
      setPage(0)
    }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  const queryArgs = useMemo(
    () => ({
      skip: page * pageSize,
      limit: pageSize,
      sort,
      order,
      q: search || undefined,
    }),
    [page, pageSize, sort, order, search],
  )

  const { data, isLoading } = useQuery({
    queryKey: ["listProjects", queryArgs],
    queryFn: () => Llm4AdProjectsService.listProjects(queryArgs),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const handleSortChange = (value: string) => {
    setSort(value as SortField)
    setPage(0)
  }

  const toggleOrder = () => {
    setOrder((o) => (o === "desc" ? "asc" : "desc"))
    setPage(0)
  }

  return (
    <div className="flex flex-col h-full">
      <OnboardingTour
        tourId="projects"
        enabled={!isLoading}
        steps={[
          {
            selector: '[data-tour="demo-project-card"]',
            title: t("tour.projects.demoTitle", {
              defaultValue: "Try the example project first",
            }),
            content: t("tour.projects.demoContent", {
              defaultValue:
                "Click this read-only example to walk through a full algorithm-design run in seconds. No LLM keys needed.",
            }),
            placement: "bottom",
          },
          {
            selector: '[data-tour="new-project-btn"]',
            title: t("tour.projects.createProjectTitle"),
            content: t("tour.projects.createProjectContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="llm-provider-link"]',
            title: t("tour.projects.configProviderTitle"),
            content: t("tour.projects.configProviderContent"),
            placement: "right",
          },
        ]}
      />
      {/* Toolbar: search + sort + view toggle + add */}
      <div className="shrink-0 flex flex-col sm:flex-row sm:items-center gap-2 pb-3">
        <div className="relative flex-1 min-w-0 sm:max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("projects.searchPlaceholder")}
            className="pl-8 pr-8 h-9"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => setSearchInput("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              aria-label={t("common.clear")}
            >
              <X className="size-4" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Select value={sort} onValueChange={handleSortChange}>
            <SelectTrigger className="h-9 w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="created_time">
                {t("projects.sortByCreated")}
              </SelectItem>
              <SelectItem value="name">{t("projects.sortByName")}</SelectItem>
            </SelectContent>
          </Select>

          <Button
            variant="outline"
            size="icon"
            className="size-9 shrink-0"
            onClick={toggleOrder}
            title={
              order === "desc"
                ? t("projects.orderDesc")
                : t("projects.orderAsc")
            }
            aria-label={
              order === "desc"
                ? t("projects.orderDesc")
                : t("projects.orderAsc")
            }
          >
            {order === "desc" ? (
              <ArrowDownAZ className="size-4" />
            ) : (
              <ArrowUpAZ className="size-4" />
            )}
          </Button>

          {/* View mode toggle */}
          <div className="flex items-center rounded-md border bg-background overflow-hidden divide-x divide-border">
            <button
              type="button"
              onClick={() => setViewMode("card")}
              className={cn(
                "flex items-center justify-center size-9 transition-colors",
                viewMode === "card"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
              title={t("projects.viewCard")}
              aria-label={t("projects.viewCard")}
              aria-pressed={viewMode === "card"}
            >
              <LayoutGrid className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => setViewMode("list")}
              className={cn(
                "flex items-center justify-center size-9 transition-colors",
                viewMode === "list"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
              title={t("projects.viewList")}
              aria-label={t("projects.viewList")}
              aria-pressed={viewMode === "list"}
            >
              <List className="size-4" />
            </button>
          </div>
        </div>

        <div className="ml-auto">
          <AddProject />
        </div>
      </div>

      {/* Scrollable content area */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1 pt-2">
        {/* Loading state */}
        {isLoading &&
          (viewMode === "card" ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 xl:gap-5">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="rounded-lg border bg-card p-4 space-y-2"
                >
                  <Skeleton className="h-5 w-3/4" />
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/3 mt-1" />
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="rounded-lg border bg-card px-4 py-3 flex items-center gap-4"
                >
                  <Skeleton className="size-10 rounded-md" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-4 w-1/3" />
                    <Skeleton className="h-3 w-2/3" />
                  </div>
                  <Skeleton className="h-7 w-20 rounded-full" />
                </div>
              ))}
            </div>
          ))}

        {/* Empty state */}
        {!isLoading && items.length === 0 && (
          <div className="space-y-6">
            {!search && (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 xl:gap-5">
                <DemoProjectCard />
              </div>
            )}
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <FolderOpen className="size-16 text-muted-foreground/40 mb-4" />
              <h3 className="text-lg font-medium text-muted-foreground">
                {search
                  ? t("projects.noSearchResults")
                  : t("projects.emptyTitle")}
              </h3>
              <p className="text-sm text-muted-foreground/70 mt-1 mb-6">
                {search
                  ? t("projects.noSearchResultsDescription", { q: search })
                  : t("projects.emptyDescription")}
              </p>
              {search ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSearchInput("")}
                >
                  <X className="mr-2 size-4" />
                  {t("common.clear")}
                </Button>
              ) : (
                <AddProject />
              )}
            </div>
          </div>
        )}

        {/* Card grid */}
        {!isLoading && items.length > 0 && viewMode === "card" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 xl:gap-5">
            {page === 0 && !search && <DemoProjectCard />}
            {items.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        )}

        {/* List view */}
        {!isLoading && items.length > 0 && viewMode === "list" && (
          <div className="space-y-2">
            {items.map((project) => (
              <ProjectListItem key={project.id} project={project} />
            ))}
          </div>
        )}
      </div>

      {/* Pagination - fixed at bottom */}
      {!isLoading && items.length > 0 && (
        <div className="shrink-0 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pt-3 mt-2">
          <div className="flex flex-col sm:flex-row sm:items-center gap-4">
            <p className="text-sm text-muted-foreground">
              {t("common.showRange", {
                start: total ? page * pageSize + 1 : 0,
                end: Math.min((page + 1) * pageSize, total),
                total,
              })}
            </p>
            {totalPages > 1 && (
              <div className="flex items-center gap-x-2">
                <p className="text-sm text-muted-foreground">
                  {t("common.perPage")}
                </p>
                <Select
                  value={`${pageSize}`}
                  onValueChange={(v) => {
                    setPageSize(Number(v))
                    setPage(0)
                  }}
                >
                  <SelectTrigger className="h-8 w-[70px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent side="top">
                    {[12, 24, 48].map((s) => (
                      <SelectItem key={s} value={`${s}`}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
          {totalPages > 1 && (
            <div className="flex items-center gap-2">
              <PageNumbers
                currentPage={page}
                totalPages={totalPages}
                onPageChange={setPage}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
