import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Brain, Loader2, Settings2 } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdMemoryService, type TaskResponse } from "@/client"
import MemoryCardManager from "@/components/Memory/MemoryCardManager"
import MemoryProviderBindingEditor from "@/components/Memory/MemoryProviderBindingEditor"
import type { MemoryScope } from "@/components/Memory/types"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function AutoDesignMemoryDialog({
  task,
  projectId,
}: {
  task?: TaskResponse | null
  projectId?: string | null
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<string>()
  const queryClient = useQueryClient()
  const config = useQuery({
    queryKey: ["autodesign-memory", "config"],
    queryFn: () => Llm4AdMemoryService.getUserMemoryConfig(),
    enabled: open,
    staleTime: 0,
  })
  const binding = useQuery({
    queryKey: ["autodesign-memory", "binding"],
    queryFn: () => Llm4AdMemoryService.getMemoryProviderBinding(),
    enabled: open,
    staleTime: 0,
  })
  const resolvedProjectId = task?.project_id ?? projectId ?? undefined
  const scopes: MemoryScope[] = [
    ...(task ? ["task" as const] : []),
    ...(resolvedProjectId ? ["project" as const] : []),
    "user",
  ]
  const ready = Boolean(
    config.data?.system_runtime_available && binding.data?.configured,
  )
  const loading = config.isPending || binding.isPending
  const failed = config.isError || binding.isError
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["autodesign-memory"] })
  const reason = loading
    ? t("common.loading")
    : failed
      ? t("memory.projectDialog.disabled.checkFailed")
      : !config.data?.system_runtime_available
        ? t("memory.projectDialog.disabled.serviceUnavailable")
        : t("autodesign.memory.bindingRequired")

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Brain data-icon="inline-start" />
          {t("autodesign.memory.title")}
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{t("autodesign.memory.title")}</DialogTitle>
          <DialogDescription>
            {t("autodesign.memory.description")}
          </DialogDescription>
        </DialogHeader>
        <Tabs
          value={view ?? scopes[0]}
          onValueChange={setView}
          className="min-h-0 flex-1 gap-4"
        >
          <TabsList className="w-full shrink-0">
            {scopes.map((scope) => (
              <TabsTrigger
                key={scope}
                value={scope}
                aria-label={t(`autodesign.memory.scopes.${scope}`)}
              >
                <span className="sm:hidden">
                  {t(`autodesign.memory.shortScopes.${scope}`)}
                </span>
                <span className="hidden sm:inline">
                  {t(`autodesign.memory.scopes.${scope}`)}
                </span>
              </TabsTrigger>
            ))}
            <TabsTrigger
              value="settings"
              aria-label={t("memory.providerBinding.title")}
              title={t("memory.providerBinding.title")}
            >
              <Settings2 />
              <span className="hidden sm:inline">
                {t("memory.providerBinding.title")}
              </span>
            </TabsTrigger>
          </TabsList>
          {scopes.map((scope) => (
            <TabsContent
              key={scope}
              value={scope}
              className="min-h-0 overflow-y-auto"
            >
              {!ready && (
                <Alert className="mb-4">
                  <AlertDescription className="flex items-center gap-3">
                    {loading && <Loader2 className="size-4 animate-spin" />}
                    {reason}
                    {failed && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void refresh()}
                      >
                        {t("common.retry")}
                      </Button>
                    )}
                    {!loading &&
                      !failed &&
                      config.data?.system_runtime_available && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setView("settings")}
                        >
                          {t("autodesign.memory.configure")}
                        </Button>
                      )}
                  </AlertDescription>
                </Alert>
              )}
              <MemoryCardManager
                scope={scope}
                taskId={scope === "task" ? task?.id : undefined}
                projectId={resolvedProjectId}
                title={t(`autodesign.memory.scopes.${scope}`)}
                description={t("autodesign.memory.cardsDescription")}
                embedded
                disabled={!ready}
                disabledReason={reason}
                loadEnabled={open && ready}
                refreshSignal={binding.dataUpdatedAt}
                promotionProjectId={
                  scope === "task" ? resolvedProjectId : undefined
                }
              />
            </TabsContent>
          ))}
          <TabsContent value="settings" className="min-h-0 overflow-y-auto">
            <MemoryProviderBindingEditor
              binding={
                binding.data
                  ? {
                      ...binding.data,
                      configured: binding.data.configured ?? false,
                      embedding_locked: binding.data.embedding_locked ?? false,
                      message: binding.data.message ?? "",
                    }
                  : undefined
              }
              onSaved={() => void refresh()}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
