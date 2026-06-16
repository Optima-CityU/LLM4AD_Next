import { Plus, Trash2 } from "lucide-react"
import { useFieldArray, useFormContext } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
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

import CollapsibleSection from "./CollapsibleSection"

const datasetModes = ["files", "directory", "glob"] as const

export default function EvaluatorSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()
  const { fields, append, remove } = useFieldArray({
    control,
    name: "evaluator.metrics",
  })

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <FormField
          control={control}
          name="evaluator.module"
          render={({ field }) => (
            <FormItem className="col-span-2">
              <FormLabel>{t("configForm.evaluator.module")}</FormLabel>
              <FormControl>
                <Input
                  placeholder={t("configForm.evaluator.modulePlaceholder")}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="evaluator.timeout"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.evaluator.timeout")}</FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="evaluator.max_retries"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.evaluator.maxRetries")}</FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="evaluator.batch_size"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.evaluator.batchSize")}</FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="evaluator.parallel"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0 pt-6">
              <FormControl>
                <Checkbox
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
              <FormLabel className="font-normal">
                {t("configForm.evaluator.parallel")}
              </FormLabel>
            </FormItem>
          )}
        />
      </div>

      <div className="space-y-2">
        <span className="text-sm font-medium">
          {t("configForm.evaluator.metrics")}
        </span>
        {fields.map((field, index) => (
          <div key={field.id} className="flex items-center gap-2">
            <FormField
              control={control}
              name={`evaluator.metrics.${index}`}
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormControl>
                    <Input
                      placeholder={t("configForm.evaluator.metricPlaceholder")}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => remove(index)}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append("")}
        >
          <Plus className="mr-1 size-4" />
          {t("configForm.evaluator.addMetric")}
        </Button>
      </div>

      <CollapsibleSection title={t("configForm.evaluator.datasetConfig")}>
        <div className="grid grid-cols-2 gap-4">
          <FormField
            control={control}
            name="evaluator.dataset.mode"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.evaluator.mode")}</FormLabel>
                <Select onValueChange={field.onChange} value={field.value}>
                  <FormControl>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {datasetModes.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
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
            name="evaluator.dataset.path"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.evaluator.path")}</FormLabel>
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
            name="evaluator.dataset.pattern"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("configForm.evaluator.pattern")}</FormLabel>
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
            name="evaluator.dataset.recursive"
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 space-y-0 pt-6">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
                <FormLabel className="font-normal">
                  {t("configForm.evaluator.recursive")}
                </FormLabel>
              </FormItem>
            )}
          />
        </div>
      </CollapsibleSection>
    </div>
  )
}
