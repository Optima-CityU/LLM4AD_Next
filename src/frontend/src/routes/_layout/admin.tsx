import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import { Users } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { type UserPublic, UsersService } from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import { PageNumbers } from "@/components/Common/PageNumbers"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth from "@/hooks/useAuth"

const DEFAULT_PAGE_SIZE = 10

export const Route = createFileRoute("/_layout/admin")({
  component: Admin,
  beforeLoad: async () => {
    const user = await UsersService.readUserMe()
    if (!user.is_superuser) {
      throw redirect({
        to: "/projects",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Admin - LLM4AD_Next",
      },
    ],
  }),
})

function Admin() {
  const { t } = useTranslation()
  const { user: currentUser } = useAuth()
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)

  const { data, isLoading } = useQuery({
    queryKey: ["users", page, pageSize],
    queryFn: () =>
      UsersService.readUsers({ skip: page * pageSize, limit: pageSize }),
  })

  const tableData: UserTableData[] = (data?.data ?? []).map(
    (user: UserPublic) => ({
      ...user,
      isCurrentUser: currentUser?.id === user.id,
    }),
  )

  const total = data?.count ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center justify-between gap-2 pb-3">
        <p className="text-sm text-muted-foreground">
          {t("admin.usersSubtitle")}
        </p>
        <AddUser />
      </div>

      {/* Scrollable content area */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1 pt-2">
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
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-5 w-16 rounded-full" />
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-8 w-8 rounded-md ml-auto" />
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {!isLoading && tableData.length === 0 && (
          <div className="flex flex-col items-center justify-center text-center py-20">
            <div className="rounded-full bg-muted p-4 mb-4">
              <Users className="size-8 text-muted-foreground/40" />
            </div>
            <h3 className="text-lg font-medium text-muted-foreground">
              {t("admin.emptyTitle")}
            </h3>
            <p className="text-sm text-muted-foreground/70 mt-1 mb-6">
              {t("admin.emptyDescription")}
            </p>
            <AddUser />
          </div>
        )}

        {/* Table */}
        {!isLoading && tableData.length > 0 && (
          <DataTable columns={columns} data={tableData} manualPagination />
        )}
      </div>

      {/* Pagination - fixed at bottom */}
      {!isLoading && tableData.length > 0 && (
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
