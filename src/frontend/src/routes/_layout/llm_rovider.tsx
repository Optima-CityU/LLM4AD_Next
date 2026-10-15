import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Unplug } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdProvidersService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { PageNumbers } from "@/components/Common/PageNumbers"
import AddProvider from "@/components/LlmProvider/AddProvider"
import { columns } from "@/components/LlmProvider/columns"
import DefaultModelSettings from "@/components/LlmProvider/DefaultModelSettings"
import EmbeddingProviderSettings from "@/components/LlmProvider/EmbeddingProviderSettings"
import LocalCodexCard from "@/components/LlmProvider/LocalCodexCard"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"

const DEFAULT_PAGE_SIZE = 10

export const Route = createFileRoute("/_layout/llm_rovider")({
  component: LlmProvider,
  head: () => ({
    meta: [
      {
        title: "LLM Provider - LLM4AD_Next",
      },
    ],
  }),
})

function LlmProvider() {
  const { t } = useTranslation()
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)

  const { data, isLoading } = useQuery({
    queryKey: ["providers", page, pageSize],
    queryFn: () =>
      Llm4AdProvidersService.listProviders({
        skip: page * pageSize,
        limit: pageSize,
      }),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center justify-between gap-2 pb-3">
        <p className="text-sm text-muted-foreground">
          {t("llmProvider.subtitle")}
        </p>
        <div className="flex items-center gap-2">
          <DefaultModelSettings />
          <EmbeddingProviderSettings />
          <AddProvider />
        </div>
      </div>

      {/* Scrollable content area */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1 pt-2">
        <LocalCodexCard />
        {/* Loading skeleton */}
        {isLoading && (
          <div className="rounded-md border">
            <div className="border-b px-4 py-3 flex gap-6">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-4 w-24" />
              ))}
            </div>
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-6 px-4 py-3 border-b last:border-b-0"
              >
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-5 w-16 rounded-full" />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-8 w-8 rounded-md ml-auto" />
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {!isLoading && items.length === 0 && (
          <div className="flex flex-col items-center justify-center text-center py-20">
            <div className="rounded-full bg-muted p-4 mb-4">
              <Unplug className="size-8 text-muted-foreground/40" />
            </div>
            <h3 className="text-lg font-medium text-muted-foreground">
              {t("llmProvider.emptyTitle")}
            </h3>
            <p className="text-sm text-muted-foreground/70 mt-1 mb-6">
              {t("llmProvider.emptyDescription")}
            </p>
            <AddProvider />
          </div>
        )}

        {/* Table */}
        {!isLoading && items.length > 0 && (
          <DataTable
            columns={columns}
            data={items}
            manualPagination
            rowClassName={(row) =>
              row.is_builtin ? "bg-amber-50/60 dark:bg-amber-500/5" : undefined
            }
          />
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
                    {[10, 20, 50].map((s) => (
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
