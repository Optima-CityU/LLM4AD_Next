import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { HelpCircle, Lock, Pencil, Plug } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { Llm4AdProvidersService, type ProviderResponse } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import { ModelTagsInput } from "./ModelTagsInput"
import {
  TestProviderDialog,
  type TestProviderParams,
} from "./TestProviderDialog"

function createFormSchema(t: (key: string) => string) {
  return z.object({
    name: z
      .string()
      .min(1, { message: t("validation.providerNameRequired") })
      .max(255),
    type: z.enum(["openai", "anthropic", "openai_compatible", "mock"]),
    api_key: z.string().optional(),
    auth_token: z.string().optional(),
    base_url: z.string().max(512).optional().or(z.literal("")),
    model: z.string().max(255).optional(),
    temperature: z.coerce.number().min(0).max(2).optional(),
    max_tokens: z.coerce.number().int().min(1).optional(),
    timeout: z.coerce.number().int().min(1).optional(),
    max_retries: z.coerce.number().int().min(0).optional(),
  })
}

type FormData = z.infer<ReturnType<typeof createFormSchema>>

const providerTypeOptions = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "openai_compatible", label: "OpenAI Compatible" },
  { value: "mock", label: "Mock" },
] as const

interface EditProviderProps {
  provider: ProviderResponse
  onSuccess: () => void
}

const EditProvider = ({ provider, onSuccess }: EditProviderProps) => {
  const [isOpen, setIsOpen] = useState(false)
  const [testOpen, setTestOpen] = useState(false)
  const [testParams, setTestParams] = useState<TestProviderParams | null>(null)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t } = useTranslation()

  const isBuiltin = Boolean(provider.is_builtin)

  const formSchema = createFormSchema(t)

  const form = useForm({
    resolver: zodResolver(formSchema),
    mode: "onBlur" as const,
    criteriaMode: "all" as const,
    defaultValues: {
      name: provider.name,
      type: provider.type,
      // 凭据已脱敏，不回填占位符；留空表示"保持不变"，仅在用户输入新值时才更新
      api_key: "",
      auth_token: "",
      base_url: provider.base_url ?? "",
      model: provider.model,
      temperature: provider.temperature,
      max_tokens: provider.max_tokens,
      timeout: provider.timeout,
      max_retries: provider.max_retries,
    },
  })

  // 后端按是否已设置返回占位符；据此提示用户凭据已存在但不可见
  const hasApiKey = Boolean(provider.api_key)
  const hasAuthToken = Boolean(provider.auth_token)

  // 标记用户是否显式清除某项已存凭据。区别于"留空保持不变"：
  // 留空 -> 不提交该字段；标记清除 -> 提交空串覆盖，真正清空数据库中的值。
  const [clearApiKey, setClearApiKey] = useState(false)
  const [clearAuthToken, setClearAuthToken] = useState(false)

  const mutation = useMutation({
    mutationFn: (data: FormData) =>
      Llm4AdProvidersService.updateProvider({
        providerId: provider.id,
        requestBody: data,
      }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.updateSuccess"))
      setIsOpen(false)
      onSuccess()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] })
    },
  })

  const onSubmit = form.handleSubmit((data) => {
    const payload = { ...data }
    // 凭据字段三态：
    //   1) 标记清除 -> 提交空串，覆盖清空数据库中已存的凭据
    //   2) 填了新值 -> 提交新值
    //   3) 留空且未标记清除 -> 剔除字段，保持原值不变
    if (clearApiKey) payload.api_key = ""
    else if (!payload.api_key) delete payload.api_key
    if (clearAuthToken) payload.auth_token = ""
    else if (!payload.auth_token) delete payload.auth_token
    mutation.mutate(payload as FormData)
  })

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuItem
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        <Pencil />
        {t("common.edit")}
      </DropdownMenuItem>
      <DialogContent
        className="sm:max-w-2xl max-h-[85vh] overflow-y-auto"
        preventOutsideClose
      >
        <Form {...form}>
          <form onSubmit={onSubmit} autoComplete="off">
            <DialogHeader>
              <DialogTitle>
                {isBuiltin
                  ? t("llmProvider.viewProvider")
                  : t("llmProvider.editProvider")}
              </DialogTitle>
              <DialogDescription>
                {isBuiltin
                  ? t("llmProvider.builtinReadonlyDescription")
                  : t("llmProvider.editProviderDescription")}
              </DialogDescription>
            </DialogHeader>
            {isBuiltin && (
              <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-400">
                <Lock className="mt-0.5 size-3.5 shrink-0" />
                <span>{t("llmProvider.builtinReadonlyHint")}</span>
              </div>
            )}
            <div className="grid gap-4 py-4">
              <div className="grid grid-cols-2 gap-4 items-start">
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {t("llmProvider.providerName")}{" "}
                        <span className="text-destructive">*</span>
                      </FormLabel>
                      <FormControl>
                        <Input
                          autoComplete="one-time-code"
                          placeholder={t("llmProvider.providerNamePlaceholder")}
                          disabled={isBuiltin}
                          {...field}
                        />
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
                      <FormLabel>
                        {t("llmProvider.providerType")}{" "}
                        <span className="text-destructive">*</span>
                      </FormLabel>
                      <Select
                        onValueChange={field.onChange}
                        defaultValue={field.value}
                        disabled={isBuiltin}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue
                              placeholder={t("llmProvider.selectProviderType")}
                            />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {providerTypeOptions.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <FormField
                control={form.control}
                name="base_url"
                render={({ field }) => {
                  const providerType = form.watch("type")
                  const isAnthropic = providerType === "anthropic"
                  return (
                    <FormItem>
                      <FormLabel className="inline-flex items-center gap-1">
                        Base URL
                        {isAnthropic && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <HelpCircle className="size-3.5 text-muted-foreground cursor-help" />
                            </TooltipTrigger>
                            <TooltipContent>
                              {t("llmProvider.baseUrlAnthropicHint")}
                            </TooltipContent>
                          </Tooltip>
                        )}
                      </FormLabel>
                      <FormControl>
                        <Input
                          autoComplete="one-time-code"
                          placeholder={
                            isAnthropic
                              ? "https://api.anthropic.com"
                              : "https://api.openai.com/v1"
                          }
                          disabled={isBuiltin}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )
                }}
              />

              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="api_key"
                  render={({ field }) => (
                    <FormItem>
                      <div className="flex items-center justify-between">
                        <FormLabel>API Key</FormLabel>
                        {hasApiKey && !isBuiltin && (
                          <button
                            type="button"
                            className="text-xs text-muted-foreground hover:text-destructive"
                            onClick={() => {
                              const next = !clearApiKey
                              setClearApiKey(next)
                              if (next) form.setValue("api_key", "")
                            }}
                          >
                            {clearApiKey
                              ? t("llmProvider.undoClearSecret")
                              : t("llmProvider.clearSecret")}
                          </button>
                        )}
                      </div>
                      <FormControl>
                        <PasswordInput
                          autoComplete="new-password"
                          placeholder={
                            clearApiKey
                              ? t("llmProvider.secretWillBeCleared")
                              : hasApiKey
                                ? t("llmProvider.secretUnchangedPlaceholder")
                                : "sk-..."
                          }
                          disabled={isBuiltin || clearApiKey}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="auth_token"
                  render={({ field }) => (
                    <FormItem>
                      <div className="flex items-center justify-between">
                        <FormLabel>Auth Token</FormLabel>
                        {hasAuthToken && !isBuiltin && (
                          <button
                            type="button"
                            className="text-xs text-muted-foreground hover:text-destructive"
                            onClick={() => {
                              const next = !clearAuthToken
                              setClearAuthToken(next)
                              if (next) form.setValue("auth_token", "")
                            }}
                          >
                            {clearAuthToken
                              ? t("llmProvider.undoClearSecret")
                              : t("llmProvider.clearSecret")}
                          </button>
                        )}
                      </div>
                      <FormControl>
                        <PasswordInput
                          autoComplete="new-password"
                          placeholder={
                            clearAuthToken
                              ? t("llmProvider.secretWillBeCleared")
                              : hasAuthToken
                                ? t("llmProvider.secretUnchangedPlaceholder")
                                : t("llmProvider.apiKey")
                          }
                          disabled={isBuiltin || clearAuthToken}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <FormField
                control={form.control}
                name="model"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("llmProvider.modelName")}</FormLabel>
                    <FormControl>
                      <ModelTagsInput
                        value={field.value ?? ""}
                        onChange={field.onChange}
                        placeholder="gpt-4"
                        disabled={isBuiltin}
                      />
                    </FormControl>
                    <p className="text-muted-foreground text-xs">
                      {t("llmProvider.modelNameHint")}
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="grid grid-cols-4 gap-4">
                <FormField
                  control={form.control}
                  name="temperature"
                  render={({ field: { value, ...field } }) => (
                    <FormItem>
                      <FormLabel>Temperature</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          step="0.1"
                          value={(value as number) ?? ""}
                          disabled={isBuiltin}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="max_tokens"
                  render={({ field: { value, ...field } }) => (
                    <FormItem>
                      <FormLabel>Max Tokens</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          value={(value as number) ?? ""}
                          disabled={isBuiltin}
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
                          disabled={isBuiltin}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="max_retries"
                  render={({ field: { value, ...field } }) => (
                    <FormItem>
                      <FormLabel>{t("llmProvider.maxRetries")}</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          value={(value as number) ?? ""}
                          disabled={isBuiltin}
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                className="mr-auto gap-1.5"
                onClick={() => {
                  const v = form.getValues()
                  // 编辑态始终走"已存储凭据"路径并带上表单当前值作为字段级覆盖：
                  // 填了的字段用表单值，留空的回退到库中真实凭据，从而支持
                  // "表单新值 + 库中旧值"混合测试，且不会因脱敏留空而发出空 key。
                  setTestParams({
                    type: v.type,
                    api_key: v.api_key,
                    auth_token: v.auth_token,
                    base_url: v.base_url,
                    model: v.model,
                    providerId: provider.id,
                  })
                  setTestOpen(true)
                }}
              >
                <Plug className="size-3.5" />
                {t("llmProvider.test.button")}
              </Button>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  {isBuiltin ? t("common.close") : t("common.cancel")}
                </Button>
              </DialogClose>
              {!isBuiltin && (
                <LoadingButton type="submit" loading={mutation.isPending}>
                  {t("common.save")}
                </LoadingButton>
              )}
            </DialogFooter>
          </form>
        </Form>
        {testParams && (
          <TestProviderDialog
            open={testOpen}
            onOpenChange={setTestOpen}
            params={testParams}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

export default EditProvider
