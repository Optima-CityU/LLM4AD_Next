import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, HelpCircle, Plug, Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import {
  Llm4AdProvidersService,
  Llm4AdUserDefaultModelsService,
  type ProviderCreate,
} from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import DefaultModelSettings from "./DefaultModelSettings"
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

const defaultValues: FormData = {
  name: "",
  type: "openai",
  api_key: "",
  auth_token: "",
  base_url: "",
  model: "gpt-4",
  temperature: 0.7,
  max_tokens: 16384,
  timeout: 120,
  max_retries: 3,
}

const AddProvider = () => {
  const [isOpen, setIsOpen] = useState(false)
  const [testOpen, setTestOpen] = useState(false)
  const [testParams, setTestParams] = useState<TestProviderParams | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [newProviderId, setNewProviderId] = useState<string | null>(null)
  const [defaultModelOpen, setDefaultModelOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t } = useTranslation()

  const formSchema = createFormSchema(t)

  const form = useForm({
    resolver: zodResolver(formSchema),
    mode: "onBlur" as const,
    criteriaMode: "all" as const,
    defaultValues,
  })

  const mutation = useMutation({
    mutationFn: (data: ProviderCreate) =>
      Llm4AdProvidersService.createProvider({ requestBody: data }),
    onSuccess: async (created) => {
      showSuccessToast(t("llmProvider.createSuccess"))
      form.reset()
      setIsOpen(false)
      try {
        const defaults =
          await Llm4AdUserDefaultModelsService.getUserDefaultModel()
        const hasEmpty =
          !defaults.planner_provider_id ||
          !defaults.coder_provider_id ||
          !defaults.report_provider_id ||
          !defaults.other_provider_id
        if (hasEmpty) {
          setNewProviderId(created.id)
          setConfirmOpen(true)
        }
      } catch {
        // ignore — non-critical check
      }
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] })
    },
  })

  const onSubmit = form.handleSubmit((data) => {
    mutation.mutate(data as ProviderCreate)
  })

  return (
    <>
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogTrigger asChild>
          <Button>
            <Plus className="mr-2" />
            {t("llmProvider.addProvider")}
          </Button>
        </DialogTrigger>
        <DialogContent
          className="sm:max-w-2xl max-h-[85vh] overflow-y-auto"
          preventOutsideClose
        >
          <DialogHeader>
            <DialogTitle>{t("llmProvider.addProvider")}</DialogTitle>
            <DialogDescription>
              {t("llmProvider.addProviderDescription")}
            </DialogDescription>
          </DialogHeader>
          <Form {...form}>
            <form onSubmit={onSubmit} autoComplete="off">
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
                            placeholder={t(
                              "llmProvider.providerNamePlaceholder",
                            )}
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
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue
                                placeholder={t(
                                  "llmProvider.selectProviderType",
                                )}
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
                    name="auth_token"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Auth Token</FormLabel>
                        <FormControl>
                          <PasswordInput
                            autoComplete="new-password"
                            placeholder={t("llmProvider.apiKey")}
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
                    name="max_retries"
                    render={({ field: { value, ...field } }) => (
                      <FormItem>
                        <FormLabel>{t("llmProvider.maxRetries")}</FormLabel>
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
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  className="mr-auto gap-1.5"
                  onClick={() => {
                    const v = form.getValues()
                    setTestParams({
                      type: v.type,
                      api_key: v.api_key,
                      auth_token: v.auth_token,
                      base_url: v.base_url,
                      model: v.model,
                    })
                    setTestOpen(true)
                  }}
                >
                  <Plug className="size-3.5" />
                  {t("llmProvider.test.button")}
                </Button>
                <DialogClose asChild>
                  <Button variant="outline" disabled={mutation.isPending}>
                    {t("common.cancel")}
                  </Button>
                </DialogClose>
                <LoadingButton type="submit" loading={mutation.isPending}>
                  {t("common.save")}
                </LoadingButton>
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
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <div className="flex size-9 items-center justify-center rounded-full bg-amber-500/15">
                <AlertTriangle className="size-5 text-amber-500" />
              </div>
              {t("llmProvider.defaultModelPrompt.title")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("llmProvider.defaultModelPrompt.description")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: "default" })}
              onClick={() => {
                setDefaultModelOpen(true)
              }}
            >
              {t("llmProvider.defaultModelPrompt.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <DefaultModelSettings
        open={defaultModelOpen}
        onOpenChange={setDefaultModelOpen}
        highlightProviderId={newProviderId ?? undefined}
      />
    </>
  )
}

export default AddProvider
