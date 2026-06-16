import { useFormContext } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { Checkbox } from "@/components/ui/checkbox"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

interface CoderSectionProps {
  coderType: string
}

const coderTypes = [
  { value: "custom", label: "Custom" },
  { value: "claude_code", label: "Claude Code" },
  { value: "opencode", label: "OpenCode" },
] as const

const coderDefaults: Record<string, Record<string, unknown>> = {
  custom: {
    type: "custom",
    timeout: 300,
    max_retries: 2,
    log_to_console: false,
    prompt_template: null,
    mutation_prompt_template: null,
    context_max_tokens: 4096,
    temperature: 0.7,
    max_gen_tokens: 4096,
    default_extension: "py",
  },
  claude_code: {
    type: "claude_code",
    timeout: 300,
    max_retries: 2,
    log_to_console: false,
    anthropic_api_key: "",
    anthropic_auth_token: "",
    anthropic_base_url: "",
    anthropic_model: "",
  },
  opencode: {
    type: "opencode",
    timeout: 300,
    max_retries: 2,
    log_to_console: false,
  },
}

export default function CoderSection({ coderType }: CoderSectionProps) {
  const { control, setValue } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="space-y-4">
      <div>
        <label className="text-sm font-medium">
          {t("configForm.coder.coderType")}
        </label>
        <Select
          value={coderType}
          onValueChange={(val) => {
            setValue("coder", coderDefaults[val])
          }}
        >
          <SelectTrigger className="mt-1 w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {coderTypes.map((ct) => (
              <SelectItem key={ct.value} value={ct.value}>
                {ct.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <FormField
          control={control}
          name="coder.timeout"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.coder.timeout")}</FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="coder.max_retries"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.coder.maxRetries")}</FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="coder.log_to_console"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0 pt-6">
              <FormControl>
                <Checkbox
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
              <FormLabel className="font-normal">
                {t("configForm.coder.logToConsole")}
              </FormLabel>
            </FormItem>
          )}
        />
      </div>

      {coderType === "custom" && (
        <div className="grid grid-cols-2 gap-4">
          <FormField
            control={control}
            name="coder.prompt_template"
            render={({ field }) => (
              <FormItem className="col-span-2">
                <FormLabel>{t("configForm.coder.promptTemplate")}</FormLabel>
                <FormControl>
                  <Input
                    {...field}
                    value={field.value ?? ""}
                    onChange={(e) => field.onChange(e.target.value || null)}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.mutation_prompt_template"
            render={({ field }) => (
              <FormItem className="col-span-2">
                <FormLabel>
                  {t("configForm.coder.mutationPromptTemplate")}
                </FormLabel>
                <FormControl>
                  <Input
                    {...field}
                    value={field.value ?? ""}
                    onChange={(e) => field.onChange(e.target.value || null)}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.context_max_tokens"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.contextMaxTokens")}</FormLabel>
                <FormControl>
                  <Input type="number" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.temperature"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.temperature")}</FormLabel>
                <FormControl>
                  <Input type="number" step="0.1" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.max_gen_tokens"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.maxGenTokens")}</FormLabel>
                <FormControl>
                  <Input type="number" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.default_extension"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.defaultExtension")}</FormLabel>
                <FormControl>
                  <Input {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
      )}

      {coderType === "claude_code" && (
        <div className="grid grid-cols-2 gap-4">
          <FormField
            control={control}
            name="coder.anthropic_base_url"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.anthropicBaseUrl")}</FormLabel>
                <FormControl>
                  <Input {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.anthropic_model"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.anthropicModel")}</FormLabel>
                <FormControl>
                  <Input {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.anthropic_api_key"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.coder.anthropicApiKey")}</FormLabel>
                <FormControl>
                  <Input type="password" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="coder.anthropic_auth_token"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {t("configForm.coder.anthropicAuthToken")}
                </FormLabel>
                <FormControl>
                  <Input type="password" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
      )}
    </div>
  )
}
