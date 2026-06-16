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

const logLevels = [
  "TRACE",
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
] as const

export default function LoggingSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="grid grid-cols-2 gap-4">
      <FormField
        control={control}
        name="logging.level"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.logging.level")}</FormLabel>
            <Select onValueChange={field.onChange} value={field.value}>
              <FormControl>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {logLevels.map((l) => (
                  <SelectItem key={l} value={l}>
                    {l}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="logging.format"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.logging.format")}</FormLabel>
            <FormControl>
              <Input
                {...field}
                value={field.value ?? ""}
                onChange={(e) => field.onChange(e.target.value || null)}
                placeholder={t("configForm.logging.formatPlaceholder")}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="logging.file"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.logging.file")}</FormLabel>
            <FormControl>
              <Input
                {...field}
                value={field.value ?? ""}
                onChange={(e) => field.onChange(e.target.value || null)}
                placeholder={t("configForm.logging.filePlaceholder")}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <div className="flex items-center gap-6 pt-6">
        <FormField
          control={control}
          name="logging.console"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl>
                <Checkbox
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
              <FormLabel className="font-normal">
                {t("configForm.logging.console")}
              </FormLabel>
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="logging.json_format"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl>
                <Checkbox
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
              <FormLabel className="font-normal">
                {t("configForm.logging.jsonFormat")}
              </FormLabel>
            </FormItem>
          )}
        />
      </div>
    </div>
  )
}
