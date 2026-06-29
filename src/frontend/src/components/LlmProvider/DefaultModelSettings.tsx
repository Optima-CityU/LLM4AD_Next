import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Bot,
  Code,
  ExternalLink,
  FileText,
  GitBranch,
  Puzzle,
  RefreshCw,
  Settings,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import {
  Llm4AdUserDefaultModelsService,
  type ProviderResponse,
  type UserDefaultModelUpdate,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import useCustomToast from "@/hooks/useCustomToast"
import {
  useProviders,
  providersQueryKey,
  userDefaultModelsQueryKey,
  embeddingProvidersQueryKey,
  useEmbeddingProviders,
  useUserDefaultModels,
} from "@/hooks/useProviders"
import { handleError } from "@/utils"

type RoleKey = "planner" | "coder" | "report" | "other"
type ModelSlotKey = RoleKey
type ProviderField =
  | "planner_provider_id"
  | "coder_provider_id"
  | "report_provider_id"
  | "other_provider_id"
type ModelField =
  | "planner_model_name"
  | "coder_model_name"
  | "report_model_name"
  | "other_model_name"

interface RoleConfig {
  key: ModelSlotKey
  icon: React.ReactNode
  providerField: ProviderField
  modelField: ModelField
}

const ROLES: RoleConfig[] = [
  {
    key: "planner",
    icon: <Bot className="size-4" />,
    providerField: "planner_provider_id",
    modelField: "planner_model_name",
  },
  {
    key: "coder",
    icon: <Code className="size-4" />,
    providerField: "coder_provider_id",
    modelField: "coder_model_name",
  },
  {
    key: "report",
    icon: <FileText className="size-4" />,
    providerField: "report_provider_id",
    modelField: "report_model_name",
  },
  {
    key: "other",
    icon: <Puzzle className="size-4" />,
    providerField: "other_provider_id",
    modelField: "other_model_name",
  },
]

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

interface LocalState {
  planner_provider_id: string | null
  planner_model_name: string | null
  coder_provider_id: string | null
  coder_model_name: string | null
  report_provider_id: string | null
  report_model_name: string | null
  other_provider_id: string | null
  other_model_name: string | null
  embedding_enabled: boolean
  embedding_provider_id: string | null
}

const EMPTY_STATE: LocalState = {
  planner_provider_id: null,
  planner_model_name: null,
  coder_provider_id: null,
  coder_model_name: null,
  report_provider_id: null,
  report_model_name: null,
  other_provider_id: null,
  other_model_name: null,
  embedding_enabled: false,
  embedding_provider_id: null,
}

function RoleRowSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[132px_1fr_1fr] items-center gap-3">
      <Skeleton className="h-5 w-16" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </div>
  )
}

interface RoleRowProps {
  role: RoleConfig
  providers: ProviderResponse[]
  currentProviderId: string | null
  currentModelName: string | null
  onProviderChange: (providerId: string) => void
  onModelChange: (modelName: string) => void
  t: (key: string) => string
  highlightProviderId?: string
  disabled?: boolean
}

function RoleRow({
  role,
  providers,
  currentProviderId,
  currentModelName,
  onProviderChange,
  onModelChange,
  t,
  highlightProviderId,
  disabled = false,
}: RoleRowProps) {
  const selectedProvider = providers.find((p) => p.id === currentProviderId)
  const availableModels = selectedProvider ? parseModels(selectedProvider.model) : []

  return (
    <div
      className={`grid grid-cols-1 sm:grid-cols-[132px_1fr_1fr] items-center gap-3 ${
        disabled ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        {role.icon}
        <span>{t(`llmProvider.defaultModel.roles.${role.key}`)}</span>
      </div>

      <Select
        value={currentProviderId ?? ""}
        onValueChange={onProviderChange}
        disabled={disabled}
      >
        <SelectTrigger className="w-full">
          <SelectValue
            placeholder={t("llmProvider.defaultModel.selectProvider")}
          />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>
              {t("llmProvider.defaultModel.providerLabel")}
            </SelectLabel>
            {providers.map((provider) => (
              <SelectItem key={provider.id} value={provider.id}>
                <div className="flex items-center gap-2">
                  <span>{provider.name}</span>
                  <Badge variant="outline" className="text-xs font-normal">
                    {provider.type}
                  </Badge>
                  {provider.is_builtin && (
                    <Badge
                      variant="secondary"
                      className="text-[11px] font-medium px-1.5 py-0 border-amber-300 bg-amber-100 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-400"
                    >
                      {t("llmProvider.builtin")}
                    </Badge>
                  )}
                  {provider.id === highlightProviderId && (
                    <Badge
                      variant="secondary"
                      className="text-[11px] font-medium px-1.5 py-0 bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30"
                    >
                      NEW
                    </Badge>
                  )}
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={currentModelName ?? ""}
        onValueChange={onModelChange}
        disabled={
          disabled || !currentProviderId || availableModels.length === 0
        }
      >
        <SelectTrigger className="w-full">
          <SelectValue
            placeholder={
              !currentProviderId
                ? t("llmProvider.defaultModel.selectProviderFirst")
                : availableModels.length === 0
                  ? t("llmProvider.defaultModel.noModels")
                  : t("llmProvider.defaultModel.selectModel")
            }
          />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>
              {t("llmProvider.defaultModel.modelLabel")}
            </SelectLabel>
            {availableModels.map((model) => (
              <SelectItem key={model} value={model}>
                {model}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  )
}

export default function DefaultModelSettings({
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  highlightProviderId,
}: {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  highlightProviderId?: string
} = {}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [internalOpen, setInternalOpen] = useState(false)
  const [localState, setLocalState] = useState<LocalState>(EMPTY_STATE)

  const isControlled = controlledOpen !== undefined
  const isOpen = isControlled ? controlledOpen : internalOpen
  const setIsOpen = isControlled
    ? (v: boolean) => controlledOnOpenChange?.(v)
    : setInternalOpen

  const { data: defaults, isLoading: isLoadingDefaults } =
    useUserDefaultModels()

  const { data: providers, isLoading: isLoadingProviders } = useProviders()
  const { data: embeddingProviders, isLoading: isLoadingEmbeddingProviders } =
    useEmbeddingProviders()

  useEffect(() => {
    if (isOpen) {
      queryClient.invalidateQueries({ queryKey: providersQueryKey })
    }
  }, [isOpen, queryClient])

  useEffect(() => {
    if (defaults && isOpen) {
      setLocalState({
        planner_provider_id: defaults.planner_provider_id,
        planner_model_name: defaults.planner_model_name,
        coder_provider_id: defaults.coder_provider_id,
        coder_model_name: defaults.coder_model_name,
        report_provider_id: defaults.report_provider_id,
        report_model_name: defaults.report_model_name,
        other_provider_id: defaults.other_provider_id,
        other_model_name: defaults.other_model_name,
        embedding_enabled: defaults.embedding_enabled,
        embedding_provider_id: defaults.embedding_provider_id,
      })
    }
  }, [defaults, isOpen])

  const mutation = useMutation({
    mutationFn: (data: UserDefaultModelUpdate) =>
      Llm4AdUserDefaultModelsService.updateUserDefaultModel({
        requestBody: data,
      }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.defaultModel.updateSuccess"))
      queryClient.invalidateQueries({ queryKey: userDefaultModelsQueryKey })
      setIsOpen(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const isLoading =
    isLoadingDefaults || isLoadingProviders || isLoadingEmbeddingProviders
  const providerList = providers?.items ?? []
  const embeddingProviderList = embeddingProviders?.items ?? []

  const handleProviderChange = (role: RoleConfig, providerId: string) => {
    const provider = providerList.find((p) => p.id === providerId)
    const models = provider ? parseModels(provider.model) : []
    const autoModel = models.length === 1 ? models[0] : null

    setLocalState((prev) => ({
      ...prev,
      [role.providerField]: providerId,
      [role.modelField]: autoModel,
    }))
  }

  const handleModelChange = (role: RoleConfig, modelName: string) => {
    setLocalState((prev) => ({
      ...prev,
      [role.modelField]: modelName,
    }))
  }

  const handleSave = () => {
    mutation.mutate(localState)
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      {!isControlled && (
        <DialogTrigger asChild>
          <Button variant="outline">
            <Settings className="mr-2 size-4" />
            {t("llmProvider.defaultModel.title")}
          </Button>
        </DialogTrigger>
      )}
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{t("llmProvider.defaultModel.title")}</DialogTitle>
          <DialogDescription className="flex items-center justify-between">
            <span>{t("llmProvider.defaultModel.description")}</span>
            <button
              type="button"
              onClick={() => window.open("/llm_rovider", "_blank")}
              className="inline-flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors shrink-0 ml-2"
            >
              <ExternalLink className="size-3" />
              <span>{t("llmProvider.defaultModel.manageProviders")}</span>
            </button>
          </DialogDescription>
        </DialogHeader>

        <div className="py-4 space-y-5">
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-[132px_1fr_1fr] gap-3">
              <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                {t("llmProvider.defaultModel.roleHeader")}
              </div>
              <div className="flex items-center gap-1 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                {t("llmProvider.defaultModel.providerHeader")}
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground transition-colors p-0.5 rounded"
                  onClick={() => {
                    queryClient.invalidateQueries({ queryKey: ["providers"] })
                    queryClient.invalidateQueries({
                      queryKey: embeddingProvidersQueryKey,
                    })
                  }}
                >
                  <RefreshCw className="size-3" />
                </button>
              </div>
              <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                {t("llmProvider.defaultModel.modelHeader")}
              </div>
            </div>

            <div className="space-y-3">
              {isLoading
                ? ROLES.map((role) => <RoleRowSkeleton key={role.key} />)
                : ROLES.map((role) => (
                    <RoleRow
                      key={role.key}
                      role={role}
                      providers={providerList}
                      currentProviderId={localState[role.providerField]}
                      currentModelName={localState[role.modelField]}
                      onProviderChange={(id) => handleProviderChange(role, id)}
                      onModelChange={(model) => handleModelChange(role, model)}
                      t={t}
                      highlightProviderId={highlightProviderId}
                    />
                  ))}
            </div>
          </div>

          <div className="border-t border-border/60 pt-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
              <div className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
                <Checkbox
                  aria-label={t("llmProvider.defaultModel.embeddingTitle")}
                  checked={localState.embedding_enabled}
                  onCheckedChange={(checked) =>
                    setLocalState((prev) => ({
                      ...prev,
                      embedding_enabled: checked === true,
                    }))
                  }
                />
                <GitBranch className="size-4 text-muted-foreground" />
                <span>
                  {localState.embedding_enabled
                    ? t("llmProvider.defaultModel.embeddingEnabled")
                    : t("llmProvider.defaultModel.embeddingDisabled")}
                </span>
              </div>
              {isLoading ? (
                <Skeleton className="h-9 w-full" />
              ) : (
                <Select
                  value={localState.embedding_provider_id ?? ""}
                  onValueChange={(providerId) =>
                    setLocalState((prev) => ({
                      ...prev,
                      embedding_provider_id: providerId,
                    }))
                  }
                  disabled={
                    !localState.embedding_enabled ||
                    embeddingProviderList.length === 0
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue
                      placeholder={
                        embeddingProviderList.length === 0
                          ? t("llmProvider.defaultModel.noEmbeddingProviders")
                          : t("llmProvider.defaultModel.selectEmbeddingProvider")
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>
                        {t("llmProvider.defaultModel.embeddingProviderLabel")}
                      </SelectLabel>
                      {embeddingProviderList.map((provider) => (
                        <SelectItem key={provider.id} value={provider.id}>
                          {provider.name} · {provider.type} ·{" "}
                          {provider.mode === "split"
                            ? `${provider.text_model} / ${provider.code_model}`
                            : provider.model}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setIsOpen(false)}>
            {t("common.cancel")}
          </Button>
          <LoadingButton loading={mutation.isPending} onClick={handleSave}>
            {t("common.save")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
