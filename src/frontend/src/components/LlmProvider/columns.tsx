import type { ColumnDef } from "@tanstack/react-table"

import type { ProviderResponse } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import i18n from "@/i18n"
import { formatDateTime } from "@/lib/utils"
import { ProviderActionsMenu } from "./ProviderActionsMenu"

const providerTypeLabels: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI Compatible",
  mock: "Mock",
}

function parseModels(raw: string | null | undefined): string[] {
  return (raw ?? "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

export const columns: ColumnDef<ProviderResponse>[] = [
  {
    accessorKey: "name",
    header: i18n.t("llmProvider.columns.name"),
    cell: ({ row }) => (
      <div className="flex items-center gap-2">
        <span className="font-medium">{row.original.name}</span>
        {row.original.is_builtin && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="secondary"
                className="cursor-help border-amber-300 bg-amber-100 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-400"
              >
                {i18n.t("llmProvider.builtin")}
              </Badge>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              {i18n.t("llmProvider.builtinHint")}
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    ),
  },
  {
    accessorKey: "type",
    header: i18n.t("llmProvider.columns.type"),
    cell: ({ row }) => (
      <Badge variant="secondary">
        {providerTypeLabels[row.original.type] ?? row.original.type}
      </Badge>
    ),
  },
  {
    accessorKey: "model",
    header: i18n.t("llmProvider.columns.modelName"),
    cell: ({ row }) => {
      const models = parseModels(row.original.model)
      if (models.length === 0) {
        return <span className="text-muted-foreground">-</span>
      }
      return (
        <div className="flex flex-wrap gap-1">
          {models.map((model, index) => (
            <Badge key={`${model}-${index}`} variant="outline">
              {model}
            </Badge>
          ))}
        </div>
      )
    },
  },
  {
    accessorKey: "created_time",
    header: i18n.t("llmProvider.columns.createdTime"),
    cell: ({ row }) => (
      <span className="text-muted-foreground text-sm">
        {formatDateTime(row.original.created_time)}
      </span>
    ),
  },
  {
    accessorKey: "updated_time",
    header: i18n.t("llmProvider.columns.updatedTime"),
    cell: ({ row }) => (
      <span className="text-muted-foreground text-sm">
        {formatDateTime(row.original.updated_time)}
      </span>
    ),
  },
  {
    id: "actions",
    header: () => (
      <span className="sr-only">{i18n.t("llmProvider.columns.actions")}</span>
    ),
    cell: ({ row }) => (
      <div className="flex justify-end">
        <ProviderActionsMenu provider={row.original} />
      </div>
    ),
  },
]
