import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import axios from "axios"
import { AlertTriangle, RefreshCw, Search, WalletCards } from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { OpenAPI, UsersService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { PageNumbers } from "@/components/Common/PageNumbers"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
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

const DEFAULT_PAGE_SIZE = 20

interface LiteLLMUserQuota {
  user_email: string | null
  full_name: string | null
  user_alias: string | null
  spend: number | null
  budget: number | null
  remaining: number | null
  teams: string[]
  created_at: string | null
  updated_at: string | null
}

interface LiteLLMUserQuotaResponse {
  available: boolean
  items: LiteLLMUserQuota[]
  total: number
  message: string
}

function authHeaders() {
  const token = localStorage.getItem("access_token") || ""
  return token ? { Authorization: `Bearer ${token}` } : undefined
}

async function fetchLiteLLMUserQuotas() {
  const response = await axios.get<LiteLLMUserQuotaResponse>(
    `${OpenAPI.BASE}/api/v1/llm4ad/providers/admin/litellm-user-quotas`,
    { headers: authHeaders() },
  )
  return response.data
}

function formatQuotaValue(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "-"
  }
  return value.toFixed(value >= 1 ? 2 : 4)
}

function formatDate(value: string | null | undefined) {
  if (!value) return "-"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

export const Route = createFileRoute("/_layout/litellm-quotas")({
  component: LiteLLMQuotas,
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
        title: "LiteLLM Quotas - LLM4AD_Next",
      },
    ],
  }),
})

function LiteLLMQuotas() {
  const { t } = useTranslation()
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["admin", "litellm-user-quotas"],
    queryFn: fetchLiteLLMUserQuotas,
    staleTime: 30 * 1000,
  })

  const items = data?.items ?? []
  const filteredItems = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return items
    return items.filter((item) =>
      [
        item.user_email,
        item.full_name,
        item.user_alias,
        ...(item.teams ?? []),
      ].some((value) => value?.toLowerCase().includes(needle)),
    )
  }, [items, search])

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / pageSize))
  const boundedPage = Math.min(page, totalPages - 1)
  const pageItems = filteredItems.slice(
    boundedPage * pageSize,
    boundedPage * pageSize + pageSize,
  )

  const columns = useMemo<ColumnDef<LiteLLMUserQuota>[]>(
    () => [
      {
        accessorKey: "full_name",
        header: t("litellmQuotas.columns.user"),
        cell: ({ row }) => {
          const name = row.original.full_name || row.original.user_alias || "-"
          return (
            <div className="min-w-0">
              <span className="truncate font-medium">{name}</span>
            </div>
          )
        },
      },
      {
        accessorKey: "user_email",
        header: t("common.email"),
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {row.original.user_email || "-"}
          </span>
        ),
      },
      {
        accessorKey: "spend",
        header: t("litellmQuotas.columns.spend"),
        cell: ({ row }) => formatQuotaValue(row.original.spend),
      },
      {
        accessorKey: "budget",
        header: t("litellmQuotas.columns.budget"),
        cell: ({ row }) => formatQuotaValue(row.original.budget),
      },
      {
        accessorKey: "remaining",
        header: t("litellmQuotas.columns.remaining"),
        cell: ({ row }) => {
          const remaining = row.original.remaining
          const tone =
            remaining !== null && remaining !== undefined && remaining <= 0
              ? "destructive"
              : "secondary"
          return (
            <Badge variant={tone}>
              {formatQuotaValue(row.original.remaining)}
            </Badge>
          )
        },
      },
      {
        accessorKey: "teams",
        header: t("litellmQuotas.columns.teams"),
        cell: ({ row }) => {
          const teams = row.original.teams ?? []
          if (!teams.length)
            return <span className="text-muted-foreground">-</span>
          return (
            <div className="flex max-w-64 flex-wrap gap-1">
              {teams.slice(0, 3).map((team) => (
                <Badge key={team} variant="outline">
                  {team}
                </Badge>
              ))}
              {teams.length > 3 && (
                <Badge variant="outline">+{teams.length - 3}</Badge>
              )}
            </div>
          )
        },
      },
      {
        accessorKey: "updated_at",
        header: t("common.updatedTime"),
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatDate(row.original.updated_at || row.original.created_at)}
          </span>
        ),
      },
    ],
    [t],
  )

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="shrink-0 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted">
            <WalletCards className="size-5 text-muted-foreground" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold">
                {t("litellmQuotas.title")}
              </h2>
              <Badge variant={data?.available ? "secondary" : "outline"}>
                {data?.available
                  ? t("litellmQuotas.statusReady")
                  : t("litellmQuotas.statusUnavailable")}
              </Badge>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("litellmQuotas.subtitle")}
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative min-w-64">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(0)
              }}
              placeholder={t("litellmQuotas.searchPlaceholder")}
              className="pl-9"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw
              data-icon="inline-start"
              className={isFetching ? "animate-spin" : undefined}
            />
            {t("common.refresh")}
          </Button>
        </div>
      </div>

      {!isLoading && data && !data.available && (
        <Alert>
          <AlertTriangle />
          <AlertTitle>{t("litellmQuotas.unavailableTitle")}</AlertTitle>
          <AlertDescription>
            {data.message || t("litellmQuotas.unavailableDescription")}
          </AlertDescription>
        </Alert>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto rounded-md border bg-background">
        {isLoading ? (
          <div className="space-y-3 p-4">
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton key={index} className="h-9 w-full" />
            ))}
          </div>
        ) : (
          <DataTable columns={columns} data={pageItems} manualPagination />
        )}
      </div>

      {!isLoading && filteredItems.length > 0 && (
        <div className="shrink-0 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <p className="text-sm text-muted-foreground">
              {t("common.showRange", {
                start: boundedPage * pageSize + 1,
                end: Math.min(
                  (boundedPage + 1) * pageSize,
                  filteredItems.length,
                ),
                total: filteredItems.length,
              })}
            </p>
            <div className="flex items-center gap-2">
              <p className="text-sm text-muted-foreground">
                {t("common.perPage")}
              </p>
              <Select
                value={`${pageSize}`}
                onValueChange={(value) => {
                  setPageSize(Number(value))
                  setPage(0)
                }}
              >
                <SelectTrigger className="h-8 w-[76px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent side="top">
                  {[10, 20, 50].map((size) => (
                    <SelectItem key={size} value={`${size}`}>
                      {size}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {totalPages > 1 && (
            <PageNumbers
              currentPage={boundedPage}
              totalPages={totalPages}
              onPageChange={setPage}
            />
          )}
        </div>
      )}
    </div>
  )
}
