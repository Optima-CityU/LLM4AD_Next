import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { FlaskConical, GitBranch, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { type Control, type Resolver, useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import {
  Llm4AdEmbeddingProvidersService,
  type EmbeddingProviderCreate,
  type EmbeddingProviderResponse,
  type EmbeddingProviderTestByIdRequest,
  type EmbeddingProviderTestRequest,
  type EmbeddingProviderUpdate,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import useCustomToast from "@/hooks/useCustomToast"
import {
  embeddingProvidersQueryKey,
  useEmbeddingProviders,
} from "@/hooks/useProviders"
import { handleError } from "@/utils"

const JINA_DEFAULT_MODEL = "jina-embeddings-v4"
const JINA_DEFAULT_BASE_URL = "https://api.jinaai.cn/v1"
const OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
const OPENAI_DEFAULT_MODEL = "text-embedding-3-small"
const OPENAI_DEFAULT_DIM = 1536
type TaskType = "text" | "code"
type SecretField =
  | "api_key"
  | "auth_token"
  | "text_api_key"
  | "text_auth_token"
  | "code_api_key"
  | "code_auth_token"

interface ExistingSecretState {
  shared: boolean
  text: boolean
  code: boolean
}

function createFormSchema(
  t: (key: string) => string,
  existingSecrets: ExistingSecretState,
) {
  const providerType = z.enum(["jina", "openai", "openai_compatible", "local", "mock"])
  const optionalString = z.string().max(512).optional().or(z.literal(""))
  const optionalModel = z.string().max(255).optional().or(z.literal(""))
  const optionalTask = z.string().max(64).optional().or(z.literal(""))

  return z
    .object({
      name: z.string().trim().min(1, t("validation.providerNameRequired")).max(255),
      type: providerType,
      api_key: z.string().optional(),
      auth_token: z.string().optional(),
      base_url: optionalString,
      mode: z.enum(["shared", "split"]),
      model: optionalModel,
      dim: z.coerce.number().int().min(1, t("validation.embedding.positiveInteger")),
      timeout: z.coerce.number().min(1, t("validation.embedding.positiveNumber")),
      embedding_func_max_async: z.coerce
        .number()
        .int()
        .min(1, t("validation.embedding.positiveInteger")),
      text_type: providerType.optional(),
      text_base_url: optionalString,
      text_api_key: z.string().optional(),
      text_auth_token: z.string().optional(),
      text_model: optionalModel,
      text_task: optionalTask,
      code_type: providerType.optional(),
      code_base_url: optionalString,
      code_api_key: z.string().optional(),
      code_auth_token: z.string().optional(),
      code_model: optionalModel,
      code_task: optionalTask,
    })
    .superRefine((data, ctx) => {
      const addIssue = (path: keyof FormData, message: string) => {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [path],
          message,
        })
      }
      const requireText = (path: keyof FormData, message: string) => {
        const value = data[path]
        if (typeof value !== "string" || !value.trim()) addIssue(path, message)
      }
      const rejectModelList = (path: keyof FormData) => {
        const value = data[path]
        if (typeof value === "string" && value.includes(";")) {
          addIssue(path, t("validation.embedding.singleModel"))
        }
      }

      rejectModelList("model")
      rejectModelList("text_model")
      rejectModelList("code_model")

      if (data.type === "mock") return

      if (data.type === "jina") {
        requireText("base_url", t("validation.embedding.baseUrlRequired"))
        requireText("model", t("validation.embedding.modelRequired"))
        requireText("text_task", t("validation.embedding.textTaskRequired"))
        requireText("code_task", t("validation.embedding.codeTaskRequired"))
        if (
          !existingSecrets.shared &&
          !data.api_key?.trim() &&
          !data.auth_token?.trim()
        ) {
          addIssue("api_key", t("validation.embedding.secretRequired"))
        }
        return
      }

      requireText("text_model", t("validation.embedding.textModelRequired"))
      requireText("code_model", t("validation.embedding.codeModelRequired"))
      requireText("text_base_url", t("validation.embedding.textBaseUrlRequired"))
      requireText("code_base_url", t("validation.embedding.codeBaseUrlRequired"))
      if (
        !existingSecrets.text &&
        !data.text_api_key?.trim() &&
        !data.text_auth_token?.trim()
      ) {
        addIssue("text_api_key", t("validation.embedding.textSecretRequired"))
      }
      if (
        !existingSecrets.code &&
        !data.code_api_key?.trim() &&
        !data.code_auth_token?.trim()
      ) {
        addIssue("code_api_key", t("validation.embedding.codeSecretRequired"))
      }
    })
}

type FormData = z.infer<ReturnType<typeof createFormSchema>>

const defaultValues: FormData = {
  name: "Jina Embedding",
  type: "jina",
  api_key: "",
  auth_token: "",
  base_url: JINA_DEFAULT_BASE_URL,
  mode: "shared",
  model: JINA_DEFAULT_MODEL,
  dim: 2048,
  timeout: 60,
  embedding_func_max_async: 2,
  text_type: "jina",
  text_base_url: "",
  text_api_key: "",
  text_auth_token: "",
  text_model: "",
  text_task: "text-matching",
  code_type: "jina",
  code_base_url: "",
  code_api_key: "",
  code_auth_token: "",
  code_model: "",
  code_task: "code.passage",
}

const providerTypes = [
  { value: "jina", label: "Jina" },
  { value: "openai", label: "OpenAI" },
  { value: "openai_compatible", label: "OpenAI Compatible" },
  { value: "local", label: "Local vLLM" },
  { value: "mock", label: "Mock" },
] as const

function taskProviderType(type: FormData["type"]) {
  return type === "local" ? "openai_compatible" : type
}

function taskConfigsMatch(provider: EmbeddingProviderResponse) {
  return (
    provider.mode === "split" &&
    provider.text_type === provider.code_type &&
    (provider.text_base_url ?? "") === (provider.code_base_url ?? "") &&
    provider.text_model === provider.code_model &&
    provider.text_task === provider.code_task &&
    Boolean(provider.text_api_key) === Boolean(provider.code_api_key) &&
    Boolean(provider.text_auth_token) === Boolean(provider.code_auth_token)
  )
}

function providerToFormData(provider: EmbeddingProviderResponse): FormData {
  return {
    name: provider.name,
    type: provider.type,
    api_key: "",
    auth_token: "",
    base_url:
      provider.base_url ?? (provider.type === "jina" ? JINA_DEFAULT_BASE_URL : ""),
    mode: provider.mode,
    model: provider.model || (provider.type === "jina" ? JINA_DEFAULT_MODEL : ""),
    dim: provider.dim,
    timeout: provider.timeout,
    embedding_func_max_async: provider.embedding_func_max_async,
    text_type: provider.text_type,
    text_base_url: provider.text_base_url ?? "",
    text_api_key: "",
    text_auth_token: "",
    text_model: provider.text_model,
    text_task: provider.text_task,
    code_type: provider.code_type,
    code_base_url: provider.code_base_url ?? "",
    code_api_key: "",
    code_auth_token: "",
    code_model: provider.code_model,
    code_task: provider.code_task,
  }
}

function normalizePayload(data: FormData): EmbeddingProviderCreate {
  const payload: EmbeddingProviderCreate = { ...data }
  if (data.type === "jina") {
    payload.mode = "shared"
    payload.text_type = "jina"
    payload.code_type = "jina"
    payload.text_base_url = ""
    payload.text_api_key = ""
    payload.text_auth_token = ""
    payload.text_model = ""
    payload.code_base_url = ""
    payload.code_api_key = ""
    payload.code_auth_token = ""
    payload.code_model = ""
  }
  if (data.type === "mock") {
    payload.api_key = ""
    payload.auth_token = ""
    payload.base_url = ""
    payload.mode = "shared"
    payload.model = "mock"
    payload.text_type = "mock"
    payload.code_type = "mock"
  }
  if (data.type !== "jina" && data.type !== "mock") {
    const taskProviderType = data.type === "local" ? "openai_compatible" : data.type
    payload.mode = "split"
    payload.model = ""
    payload.api_key = ""
    payload.auth_token = ""
    payload.base_url = ""
    payload.text_type = taskProviderType
    payload.code_type = taskProviderType
  }
  return payload
}

function stripEmptySecretsForUpdate(
  payload: EmbeddingProviderUpdate,
  clearedSecrets: Set<SecretField>,
) {
  for (const field of [
    "api_key",
    "auth_token",
    "text_api_key",
    "text_auth_token",
    "code_api_key",
    "code_auth_token",
  ] as const) {
    if (clearedSecrets.has(field)) {
      payload[field] = ""
    } else if (!payload[field]) {
      delete payload[field]
    }
  }
}

function secretWasConfigured(provider: EmbeddingProviderResponse | null, field: SecretField) {
  return Boolean(provider?.[field])
}

export default function EmbeddingProviderSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data, isLoading } = useEmbeddingProviders()
  const providers = data?.items ?? []
  const [editingProvider, setEditingProvider] = useState<EmbeddingProviderResponse | null>(null)
  const [clearedSecrets, setClearedSecrets] = useState<Set<SecretField>>(new Set())
  const [testingKey, setTestingKey] = useState<string | null>(null)
  const [shareTaskConfig, setShareTaskConfig] = useState(false)
  const existingSecrets = useMemo(
    () => ({
      shared: Boolean(
        (editingProvider?.api_key && !clearedSecrets.has("api_key")) ||
          (editingProvider?.auth_token && !clearedSecrets.has("auth_token")),
      ),
      text: Boolean(
        (editingProvider?.text_api_key && !clearedSecrets.has("text_api_key")) ||
          (editingProvider?.text_auth_token && !clearedSecrets.has("text_auth_token")),
      ),
      code: Boolean(
        (editingProvider?.code_api_key && !clearedSecrets.has("code_api_key")) ||
          (editingProvider?.code_auth_token && !clearedSecrets.has("code_auth_token")),
      ),
    }),
    [editingProvider, clearedSecrets],
  )

  const formSchema = useMemo(() => createFormSchema(t, existingSecrets), [t, existingSecrets])
  const form = useForm<FormData, unknown, FormData>({
    resolver: zodResolver(formSchema) as unknown as Resolver<FormData>,
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues,
  })

  const type = form.watch("type")
  const textBaseUrl = form.watch("text_base_url")
  const textApiKey = form.watch("text_api_key")
  const textAuthToken = form.watch("text_auth_token")
  const textModel = form.watch("text_model")
  const textTask = form.watch("text_task")
  const isJina = type === "jina"
  const isMock = type === "mock"
  const usesSeparateTaskConfigs = !isJina && !isMock
  const isEditing = Boolean(editingProvider)

  useEffect(() => {
    if (!shareTaskConfig || !usesSeparateTaskConfigs) return
    form.setValue("code_type", taskProviderType(type))
    form.setValue("code_base_url", textBaseUrl ?? "")
    form.setValue("code_api_key", textApiKey ?? "")
    form.setValue("code_auth_token", textAuthToken ?? "")
    form.setValue("code_model", textModel ?? "")
    form.setValue("code_task", textTask ?? "")
  }, [
    form,
    shareTaskConfig,
    textApiKey,
    textAuthToken,
    textBaseUrl,
    textModel,
    textTask,
    type,
    usesSeparateTaskConfigs,
  ])

  const createMutation = useMutation({
    mutationFn: (requestBody: EmbeddingProviderCreate) =>
      Llm4AdEmbeddingProvidersService.createEmbeddingProvider({ requestBody }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.embedding.createSuccess"))
      resetForm()
      queryClient.invalidateQueries({ queryKey: embeddingProvidersQueryKey })
    },
    onError: handleError.bind(showErrorToast),
  })

  const updateMutation = useMutation({
    mutationFn: ({
      providerId,
      requestBody,
    }: {
      providerId: string
      requestBody: EmbeddingProviderUpdate
    }) =>
      Llm4AdEmbeddingProvidersService.updateEmbeddingProvider({
        providerId,
        requestBody,
      }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.embedding.updateSuccess"))
      resetForm()
      queryClient.invalidateQueries({ queryKey: embeddingProvidersQueryKey })
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteMutation = useMutation({
    mutationFn: (providerId: string) =>
      Llm4AdEmbeddingProvidersService.deleteEmbeddingProvider({ providerId }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.embedding.deleteSuccess"))
      queryClient.invalidateQueries({ queryKey: embeddingProvidersQueryKey })
    },
    onError: handleError.bind(showErrorToast),
  })

  const testMutation = useMutation({
    mutationFn: async ({
      taskType,
      formData,
    }: {
      taskType: TaskType
      formData?: FormData
    }) => {
      const normalized = normalizePayload(formData ?? form.getValues())
      if (editingProvider) {
        const requestBody: EmbeddingProviderTestByIdRequest = {
          ...(normalized as EmbeddingProviderUpdate),
          task_type: taskType,
        }
        stripEmptySecretsForUpdate(requestBody, clearedSecrets)
        return Llm4AdEmbeddingProvidersService.testStoredEmbeddingProvider({
          providerId: editingProvider.id,
          requestBody,
        })
      }
      const requestBody: EmbeddingProviderTestRequest = {
        ...normalized,
        task_type: taskType,
      }
      return Llm4AdEmbeddingProvidersService.testEmbeddingProvider({ requestBody })
    },
    onSuccess: (result) => {
      if (result.success) {
        showSuccessToast(result.message || t("llmProvider.embedding.testSuccess"))
      } else {
        showErrorToast(result.message || t("llmProvider.embedding.testFailed"))
      }
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => setTestingKey(null),
  })

  function resetForm() {
    setEditingProvider(null)
    setClearedSecrets(new Set())
    setShareTaskConfig(false)
    form.reset(defaultValues)
  }

  function editProvider(provider: EmbeddingProviderResponse) {
    setEditingProvider(provider)
    setClearedSecrets(new Set())
    setShareTaskConfig(taskConfigsMatch(provider))
    form.reset(providerToFormData(provider))
  }

  function syncCodeFromText() {
    form.setValue("code_type", taskProviderType(form.getValues("type")))
    form.setValue("code_base_url", form.getValues("text_base_url") ?? "")
    form.setValue("code_api_key", form.getValues("text_api_key") ?? "")
    form.setValue("code_auth_token", form.getValues("text_auth_token") ?? "")
    form.setValue("code_model", form.getValues("text_model") ?? "")
    form.setValue("code_task", form.getValues("text_task") ?? "")
  }

  function setSharedTaskConfig(checked: boolean) {
    setShareTaskConfig(checked)
    if (checked) syncCodeFromText()
  }

  function setProviderType(value: FormData["type"]) {
    form.setValue("type", value)
    if (value === "jina") {
      setShareTaskConfig(false)
      form.setValue("mode", "shared")
      form.setValue("base_url", JINA_DEFAULT_BASE_URL)
      form.setValue("dim", 2048)
      form.setValue("model", JINA_DEFAULT_MODEL)
      form.setValue("text_type", "jina")
      form.setValue("code_type", "jina")
      form.setValue("text_task", "text-matching")
      form.setValue("code_task", "code.passage")
    } else if (value === "mock") {
      setShareTaskConfig(false)
      form.setValue("mode", "shared")
      form.setValue("model", "mock")
      form.setValue("text_type", "mock")
      form.setValue("code_type", "mock")
    } else if (value === "openai") {
      setShareTaskConfig(true)
      form.setValue("mode", "split")
      form.setValue("model", "")
      form.setValue("api_key", "")
      form.setValue("auth_token", "")
      form.setValue("base_url", "")
      form.setValue("dim", OPENAI_DEFAULT_DIM)
      form.setValue("text_type", "openai")
      form.setValue("code_type", "openai")
      form.setValue("text_base_url", OPENAI_DEFAULT_BASE_URL)
      form.setValue("code_base_url", OPENAI_DEFAULT_BASE_URL)
      form.setValue("text_model", OPENAI_DEFAULT_MODEL)
      form.setValue("code_model", OPENAI_DEFAULT_MODEL)
      form.setValue("text_task", "")
      form.setValue("code_task", "")
      const currentName = form.getValues("name").trim()
      if (!currentName || currentName === defaultValues.name) {
        form.setValue("name", "OpenAI Embedding")
      }
    } else {
      setShareTaskConfig(false)
      form.setValue("mode", "split")
      const providerType = taskProviderType(value)
      form.setValue("text_type", providerType)
      form.setValue("code_type", providerType)
    }
  }

  function toggleClearSecret(field: SecretField) {
    setClearedSecrets((current) => {
      const next = new Set(current)
      if (next.has(field)) next.delete(field)
      else next.add(field)
      if (shareTaskConfig && field === "text_api_key") {
        if (next.has(field)) next.add("code_api_key")
        else next.delete("code_api_key")
      }
      if (shareTaskConfig && field === "text_auth_token") {
        if (next.has(field)) next.add("code_auth_token")
        else next.delete("code_auth_token")
      }
      return next
    })
  }

  const onSubmit = form.handleSubmit((data) => {
    const payload = normalizePayload(data)
    if (editingProvider) {
      const updatePayload = { ...payload } as EmbeddingProviderUpdate
      stripEmptySecretsForUpdate(updatePayload, clearedSecrets)
      updateMutation.mutate({
        providerId: editingProvider.id,
        requestBody: updatePayload,
      })
      return
    }
    createMutation.mutate(payload)
  })

  async function testCurrentForm(taskType: TaskType) {
    const valid = await form.trigger()
    if (!valid) return
    setTestingKey(`form-${taskType}`)
    testMutation.mutate({ taskType, formData: form.getValues() })
  }

  const saving = createMutation.isPending || updateMutation.isPending

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">
          <GitBranch />
          {t("llmProvider.embedding.title")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("llmProvider.embedding.title")}</DialogTitle>
          <DialogDescription>
            {t("llmProvider.embedding.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[calc(90vh-8rem)] flex-col gap-5 overflow-hidden">
          <div className="flex flex-col gap-2">
            {isLoading ? (
              <>
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </>
            ) : providers.length === 0 ? (
              <p className="rounded-md border border-dashed px-3 py-3 text-sm text-muted-foreground">
                {t("llmProvider.embedding.empty")}
              </p>
            ) : (
              <ScrollArea className="max-h-56 pr-3">
                <div className="flex flex-col gap-2">
                  {providers.map((provider) => (
                    <div
                      key={provider.id}
                      className="flex flex-col gap-3 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">
                          {provider.name}
                        </div>
                        <div className="truncate text-xs text-muted-foreground">
                          {provider.type} · {provider.mode} ·{" "}
                          {provider.mode === "split"
                            ? `${provider.text_model} / ${provider.code_model}`
                            : provider.model || JINA_DEFAULT_MODEL}
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => editProvider(provider)}
                        >
                          <Pencil />
                          <span className="sr-only">{t("common.edit")}</span>
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => deleteMutation.mutate(provider.id)}
                          disabled={deleteMutation.isPending}
                        >
                          <Trash2 />
                          <span className="sr-only">{t("common.delete")}</span>
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            )}
          </div>

          <Form {...form}>
            <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col gap-4" autoComplete="off">
              <ScrollArea className="min-h-0 flex-1 pr-3">
                <div className="flex flex-col gap-4">
                  {isEditing && (
                    <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2 text-sm">
                      <span className="truncate">
                        {t("llmProvider.embedding.editing")}: {editingProvider?.name}
                      </span>
                      <Button type="button" variant="ghost" size="sm" onClick={resetForm}>
                        <X />
                        {t("common.cancel")}
                      </Button>
                    </div>
                  )}

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <FormField
                      control={form.control}
                      name="name"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.providerName")}</FormLabel>
                          <FormControl>
                            <Input {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="type"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.providerType")}</FormLabel>
                          <Select
                            value={field.value}
                            onValueChange={(value) => setProviderType(value as FormData["type"])}
                          >
                            <FormControl>
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {providerTypes.map((option) => (
                                <SelectItem key={option.value} value={option.value}>
                                  {option.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>

                  {!isMock && isJina && (
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <SecretFieldInput
                        control={form.control}
                        name="api_key"
                        label="API Key"
                        provider={editingProvider}
                        clearedSecrets={clearedSecrets}
                        onToggleClear={toggleClearSecret}
                        t={t}
                      />
                      <FormField
                        control={form.control}
                        name="base_url"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Base URL</FormLabel>
                            <FormControl>
                              <Input placeholder={JINA_DEFAULT_BASE_URL} {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  )}

                  {!isMock && isJina && (
                    <FormField
                      control={form.control}
                      name="model"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.modelName")}</FormLabel>
                          <FormControl>
                            <Input placeholder={JINA_DEFAULT_MODEL} {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}

                  {isJina && (
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <FormField
                        control={form.control}
                        name="text_task"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>{t("llmProvider.embedding.textTask")}</FormLabel>
                            <FormControl>
                              <Input placeholder="text-matching" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="code_task"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>{t("llmProvider.embedding.codeTask")}</FormLabel>
                            <FormControl>
                              <Input placeholder="code.passage" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  )}

                  {usesSeparateTaskConfigs && (
                    <div className="space-y-3">
                      <label className="flex items-start gap-3 rounded-md border bg-muted/20 px-3 py-2 text-sm">
                        <Checkbox
                          className="mt-0.5"
                          checked={shareTaskConfig}
                          onCheckedChange={(checked) => setSharedTaskConfig(checked === true)}
                        />
                        <span className="grid gap-1">
                          <span className="font-medium">
                            {t("llmProvider.embedding.shareTaskConfig")}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {t("llmProvider.embedding.shareTaskConfigHint")}
                          </span>
                        </span>
                      </label>
                      <div
                        className={
                          shareTaskConfig
                            ? "grid grid-cols-1 gap-4"
                            : "grid grid-cols-1 gap-4 sm:grid-cols-2"
                        }
                      >
                        <TaskConfigFields
                          control={form.control}
                          title={
                            shareTaskConfig
                              ? t("llmProvider.embedding.sharedTaskConfig")
                              : t("llmProvider.embedding.textConfig")
                          }
                          baseUrlName="text_base_url"
                          apiKeyName="text_api_key"
                          modelName="text_model"
                          taskName="text_task"
                          modelLabel={
                            shareTaskConfig
                              ? t("llmProvider.modelName")
                              : t("llmProvider.embedding.textModel")
                          }
                          taskLabel={t("llmProvider.embedding.textTask")}
                          modelPlaceholder={type === "openai" ? OPENAI_DEFAULT_MODEL : "bge-text"}
                          baseUrlPlaceholder={
                            type === "openai" ? OPENAI_DEFAULT_BASE_URL : "http://localhost:8000/v1"
                          }
                          taskPlaceholder={type === "openai" ? "" : "text-matching"}
                          provider={editingProvider}
                          clearedSecrets={clearedSecrets}
                          onToggleClear={toggleClearSecret}
                          t={t}
                        />
                        {!shareTaskConfig && (
                          <TaskConfigFields
                            control={form.control}
                            title={t("llmProvider.embedding.codeConfig")}
                            baseUrlName="code_base_url"
                            apiKeyName="code_api_key"
                            modelName="code_model"
                            taskName="code_task"
                            modelLabel={t("llmProvider.embedding.codeModel")}
                            taskLabel={t("llmProvider.embedding.codeTask")}
                            modelPlaceholder={type === "openai" ? OPENAI_DEFAULT_MODEL : "bge-code"}
                            baseUrlPlaceholder={
                              type === "openai"
                                ? OPENAI_DEFAULT_BASE_URL
                                : "http://localhost:8001/v1"
                            }
                            taskPlaceholder={type === "openai" ? "" : "code.passage"}
                            provider={editingProvider}
                            clearedSecrets={clearedSecrets}
                            onToggleClear={toggleClearSecret}
                            t={t}
                          />
                        )}
                      </div>
                    </div>
                  )}

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <FormField
                      control={form.control}
                      name="dim"
                      render={({ field: { value, ...field } }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.embedding.dim")}</FormLabel>
                          <FormControl>
                            <Input type="number" value={(value as number) ?? ""} {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="timeout"
                      render={({ field: { value, ...field } }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.timeout")}</FormLabel>
                          <FormControl>
                            <Input type="number" value={(value as number) ?? ""} {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="embedding_func_max_async"
                      render={({ field: { value, ...field } }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.embedding.concurrency")}</FormLabel>
                          <FormControl>
                            <Input type="number" value={(value as number) ?? ""} {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                </div>
              </ScrollArea>

              <div className="flex flex-col-reverse gap-2 border-t pt-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap items-center gap-2">
                  <LoadingButton
                    type="button"
                    variant="outline"
                    loading={testingKey === "form-text"}
                    onClick={() => testCurrentForm("text")}
                  >
                    <FlaskConical />
                    {t("llmProvider.embedding.testText")}
                  </LoadingButton>
                  <LoadingButton
                    type="button"
                    variant="outline"
                    loading={testingKey === "form-code"}
                    onClick={() => testCurrentForm("code")}
                  >
                    <FlaskConical />
                    {t("llmProvider.embedding.testCode")}
                  </LoadingButton>
                </div>
                <div className="flex justify-end gap-2">
                  {isEditing && (
                    <Button type="button" variant="outline" onClick={resetForm}>
                      <RotateCcw />
                      {t("common.cancel")}
                    </Button>
                  )}
                  <LoadingButton type="submit" loading={saving}>
                    {isEditing ? <Pencil /> : <Plus />}
                    {isEditing
                      ? t("llmProvider.embedding.saveEdit")
                      : t("llmProvider.embedding.add")}
                  </LoadingButton>
                </div>
              </div>
            </form>
          </Form>
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface SecretFieldInputProps {
  control: Control<FormData, unknown, FormData>
  name: SecretField
  label: string
  provider: EmbeddingProviderResponse | null
  clearedSecrets: Set<SecretField>
  onToggleClear: (field: SecretField) => void
  t: (key: string) => string
}

function SecretFieldInput({
  control,
  name,
  label,
  provider,
  clearedSecrets,
  onToggleClear,
  t,
}: SecretFieldInputProps) {
  const configured = secretWasConfigured(provider, name)
  const willClear = clearedSecrets.has(name)

  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <div className="flex items-center justify-between gap-2">
            <FormLabel>{label}</FormLabel>
            {configured && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onToggleClear(name)}
              >
                {willClear ? t("llmProvider.undoClearSecret") : t("llmProvider.clearSecret")}
              </Button>
            )}
          </div>
          <FormControl>
            <PasswordInput
              autoComplete="new-password"
              placeholder={configured ? t("llmProvider.secretUnchangedPlaceholder") : "sk-..."}
              disabled={willClear}
              {...field}
            />
          </FormControl>
          {willClear && (
            <p className="text-xs text-destructive">
              {t("llmProvider.secretWillBeCleared")}
            </p>
          )}
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

interface TaskConfigFieldsProps {
  control: Control<FormData, unknown, FormData>
  title: string
  baseUrlName: "text_base_url" | "code_base_url"
  apiKeyName: "text_api_key" | "code_api_key"
  modelName: "text_model" | "code_model"
  taskName: "text_task" | "code_task"
  modelLabel: string
  taskLabel: string
  modelPlaceholder: string
  baseUrlPlaceholder: string
  taskPlaceholder: string
  provider: EmbeddingProviderResponse | null
  clearedSecrets: Set<SecretField>
  onToggleClear: (field: SecretField) => void
  t: (key: string) => string
}

function TaskConfigFields({
  control,
  title,
  baseUrlName,
  apiKeyName,
  modelName,
  taskName,
  modelLabel,
  taskLabel,
  modelPlaceholder,
  baseUrlPlaceholder,
  taskPlaceholder,
  provider,
  clearedSecrets,
  onToggleClear,
  t,
}: TaskConfigFieldsProps) {
  return (
    <div className="flex flex-col gap-4 rounded-md border p-3">
      <div className="text-sm font-medium">{title}</div>
      <FormField
        control={control}
        name={baseUrlName}
        render={({ field }) => (
          <FormItem>
            <FormLabel>Base URL</FormLabel>
            <FormControl>
              <Input placeholder={baseUrlPlaceholder} {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <SecretFieldInput
        control={control}
        name={apiKeyName}
        label="API Key"
        provider={provider}
        clearedSecrets={clearedSecrets}
        onToggleClear={onToggleClear}
        t={t}
      />
      <FormField
        control={control}
        name={modelName}
        render={({ field }) => (
          <FormItem>
            <FormLabel>{modelLabel}</FormLabel>
            <FormControl>
              <Input placeholder={modelPlaceholder} {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name={taskName}
        render={({ field }) => (
          <FormItem>
            <FormLabel>{taskLabel}</FormLabel>
            <FormControl>
              <Input placeholder={taskPlaceholder} {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}
