import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { GitBranch, Plus, Trash2 } from "lucide-react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import {
  Llm4AdEmbeddingProvidersService,
  type EmbeddingProviderCreate,
} from "@/client"
import { Badge } from "@/components/ui/badge"
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
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
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

const formSchema = z
  .object({
    name: z.string().min(1).max(255),
    type: z.enum(["jina", "openai", "openai_compatible", "local", "mock"]),
    api_key: z.string().optional(),
    auth_token: z.string().optional(),
    base_url: z.string().max(512).optional().or(z.literal("")),
    mode: z.enum(["shared", "split"]),
    model: z.string().max(255).optional(),
    dim: z.coerce.number().int().min(1),
    timeout: z.coerce.number().min(1),
    embedding_func_max_async: z.coerce.number().int().min(1),
    text_type: z.enum(["openai", "jina", "openai_compatible", "local", "mock"]).optional(),
    text_base_url: z.string().max(512).optional().or(z.literal("")),
    text_api_key: z.string().optional(),
    text_auth_token: z.string().optional(),
    text_model: z.string().max(255).optional(),
    text_task: z.string().max(64).optional(),
    code_type: z.enum(["openai", "jina", "openai_compatible", "local", "mock"]).optional(),
    code_base_url: z.string().max(512).optional().or(z.literal("")),
    code_api_key: z.string().optional(),
    code_auth_token: z.string().optional(),
    code_model: z.string().max(255).optional(),
    code_task: z.string().max(64).optional(),
  })
  .superRefine((data, ctx) => {
    const addModelIssue = (path: "model" | "text_model" | "code_model") => {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: [path],
        message: "Use one embedding model per field.",
      })
    }

    if (data.model?.includes(";")) addModelIssue("model")
    if (data.text_model?.includes(";")) addModelIssue("text_model")
    if (data.code_model?.includes(";")) addModelIssue("code_model")

    if (data.type === "mock") return
    if (data.type !== "jina") {
      if (!data.text_model?.trim()) addModelIssue("text_model")
      if (!data.code_model?.trim()) addModelIssue("code_model")
      if (!data.text_api_key?.trim() && !data.text_auth_token?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["text_api_key"],
          message: "Text embedding requires an API key.",
        })
      }
      if (!data.code_api_key?.trim() && !data.code_auth_token?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["code_api_key"],
          message: "Code embedding requires an API key.",
        })
      }
      if (data.type !== "openai" && !data.text_base_url?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["text_base_url"],
          message: "Text embedding requires a base URL.",
        })
      }
      if (data.type !== "openai" && !data.code_base_url?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["code_base_url"],
          message: "Code embedding requires a base URL.",
        })
      }
      return
    }
    if (!data.model?.trim()) addModelIssue("model")
  })

type FormData = z.infer<typeof formSchema>

const defaultValues: FormData = {
  name: "Jina Embedding",
  type: "jina",
  api_key: "",
  auth_token: "",
  base_url: "",
  mode: "shared",
  model: JINA_DEFAULT_MODEL,
  dim: 2048,
  timeout: 60,
  embedding_func_max_async: 2,
  text_type: "openai_compatible",
  text_base_url: "",
  text_api_key: "",
  text_auth_token: "",
  text_model: "",
  text_task: "text-matching",
  code_type: "openai_compatible",
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

export default function EmbeddingProviderSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data, isLoading } = useEmbeddingProviders()
  const providers = data?.items ?? []

  const form = useForm({
    resolver: zodResolver(formSchema),
    mode: "onBlur" as const,
    defaultValues,
  })
  const type = form.watch("type")
  const isJina = type === "jina"
  const isMock = type === "mock"
  const usesSeparateTaskConfigs = !isJina && !isMock

  const createMutation = useMutation({
    mutationFn: (requestBody: EmbeddingProviderCreate) =>
      Llm4AdEmbeddingProvidersService.createEmbeddingProvider({
        requestBody,
      }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.embedding.createSuccess"))
      form.reset(defaultValues)
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

  const onSubmit = form.handleSubmit((data) => {
    const payload: EmbeddingProviderCreate = { ...data }
    if (isJina) {
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
    if (isMock) {
      payload.api_key = ""
      payload.auth_token = ""
      payload.base_url = ""
      payload.mode = "shared"
      payload.model = "mock"
    }
    if (usesSeparateTaskConfigs) {
      const taskProviderType = (data.type === "local" ? "openai_compatible" : data.type) as
        | "openai"
        | "openai_compatible"
      payload.mode = "split"
      payload.model = ""
      payload.api_key = ""
      payload.auth_token = ""
      payload.base_url = ""
      payload.text_type = taskProviderType
      payload.code_type = taskProviderType
    }
    createMutation.mutate(payload)
  })

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">
          <GitBranch className="mr-2 size-4" />
          {t("llmProvider.embedding.title")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("llmProvider.embedding.title")}</DialogTitle>
          <DialogDescription>
            {t("llmProvider.embedding.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <div className="space-y-2">
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
              providers.map((provider) => (
                <div
                  key={provider.id}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">
                      {provider.name}
                      {provider.is_builtin && (
                        <Badge variant="secondary" className="ml-2 align-middle text-[11px]">
                          {t("llmProvider.builtin")}
                        </Badge>
                      )}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {provider.type} · {provider.mode} ·{" "}
                      {provider.mode === "split"
                        ? `${provider.text_model} / ${provider.code_model}`
                        : provider.model || JINA_DEFAULT_MODEL}
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => deleteMutation.mutate(provider.id)}
                    disabled={deleteMutation.isPending || provider.is_builtin}
                  >
                    <Trash2 className="size-4" />
                    <span className="sr-only">{t("common.delete")}</span>
                  </Button>
                </div>
              ))
            )}
          </div>

          <Form {...form}>
            <form onSubmit={onSubmit} className="space-y-4" autoComplete="off">
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
                        onValueChange={(value) => {
                          field.onChange(value)
                          if (value === "jina") {
                            form.setValue("mode", "shared")
                            form.setValue("dim", 2048)
                            form.setValue("model", JINA_DEFAULT_MODEL)
                            form.setValue("text_type", "jina")
                            form.setValue("code_type", "jina")
                            form.setValue("text_task", "text-matching")
                            form.setValue("code_task", "code.passage")
                          } else if (value !== "mock") {
                            form.setValue("mode", "split")
                            const taskProviderType =
                              value === "local" ? "openai_compatible" : value
                            form.setValue("text_type", taskProviderType as FormData["text_type"])
                            form.setValue("code_type", taskProviderType as FormData["code_type"])
                          }
                        }}
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
                  <FormField
                    control={form.control}
                    name="api_key"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>API Key</FormLabel>
                        <FormControl>
                          <PasswordInput
                            autoComplete="new-password"
                            placeholder="sk-..."
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="base_url"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Base URL</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="https://api.jinaai.cn/v1"
                            {...field}
                          />
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
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-4 rounded-md border p-3">
                    <div className="text-sm font-medium">
                      {t("llmProvider.embedding.textConfig")}
                    </div>
                    <FormField
                      control={form.control}
                      name="text_base_url"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Base URL</FormLabel>
                          <FormControl>
                            <Input placeholder="http://localhost:8000/v1" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="text_api_key"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>API Key</FormLabel>
                          <FormControl>
                            <PasswordInput autoComplete="new-password" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="text_model"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.embedding.textModel")}</FormLabel>
                          <FormControl>
                            <Input placeholder="bge-text" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
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
                  </div>
                  <div className="space-y-4 rounded-md border p-3">
                    <div className="text-sm font-medium">
                      {t("llmProvider.embedding.codeConfig")}
                    </div>
                    <FormField
                      control={form.control}
                      name="code_base_url"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Base URL</FormLabel>
                          <FormControl>
                            <Input placeholder="http://localhost:8001/v1" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="code_api_key"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>API Key</FormLabel>
                          <FormControl>
                            <PasswordInput autoComplete="new-password" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="code_model"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("llmProvider.embedding.codeModel")}</FormLabel>
                          <FormControl>
                            <Input placeholder="bge-code" {...field} />
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
                </div>
              )}

              <div className="grid grid-cols-3 gap-4">
                <FormField
                  control={form.control}
                  name="dim"
                  render={({ field: { value, ...field } }) => (
                    <FormItem>
                      <FormLabel>{t("llmProvider.embedding.dim")}</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          value={(value as number) ?? ""}
                          {...field}
                        />
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
                        <Input
                          type="number"
                          value={(value as number) ?? ""}
                          {...field}
                        />
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
                        <Input
                          type="number"
                          value={(value as number) ?? ""}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <div className="flex justify-end">
                <LoadingButton
                  type="submit"
                  loading={createMutation.isPending}
                >
                  <Plus className="mr-2 size-4" />
                  {t("llmProvider.embedding.add")}
                </LoadingButton>
              </div>
            </form>
          </Form>
        </div>
      </DialogContent>
    </Dialog>
  )
}
