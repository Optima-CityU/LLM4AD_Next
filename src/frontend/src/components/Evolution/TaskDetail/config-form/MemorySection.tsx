import { useFormContext } from "react-hook-form"
import { useTranslation } from "react-i18next"

import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export default function MemorySection() {
  const { control } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="grid grid-cols-2 gap-4">
      <FormField
        control={control}
        name="memory.enabled"
        render={({ field }) => (
          <FormItem className="col-span-2 flex items-center gap-2 space-y-0">
            <FormControl>
              <Checkbox
                checked={field.value}
                onCheckedChange={(checked) => field.onChange(checked === true)}
              />
            </FormControl>
            <FormLabel>{t("configForm.memory.enabled")}</FormLabel>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="memory.mindmemos_search_strategy"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.memory.mindmemosSearchStrategy")}</FormLabel>
            <Select value={field.value ?? "fast"} onValueChange={field.onChange}>
              <FormControl>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                <SelectItem value="fast">fast</SelectItem>
                <SelectItem value="agentic">agentic</SelectItem>
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )}
      />
      {[
        ["include_user_memory", "user_memory_limit", "includeUserMemory", "userMemoryLimit"],
        ["include_project_memory", "project_memory_limit", "includeProjectMemory", "projectMemoryLimit"],
        ["include_task_memory", "task_memory_limit", "includeTaskMemory", "taskMemoryLimit"],
      ].map(([enabledName, limitName, enabledLabel, limitLabel]) => (
        <div key={enabledName} className="col-span-2 grid grid-cols-[1fr_160px] gap-3 rounded-md border p-3">
          <FormField
            control={control}
            name={`memory.${enabledName}`}
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 space-y-0">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={(checked) => field.onChange(checked === true)}
                  />
                </FormControl>
                <FormLabel>{t(`configForm.memory.${enabledLabel}`)}</FormLabel>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name={`memory.${limitName}`}
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t(`configForm.memory.${limitLabel}`)}</FormLabel>
                <FormControl>
                  <Input type="number" min={0} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
      ))}
      <FormField
        control={control}
        name="memory.embedding_dim"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.memory.embeddingDimension")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="memory.max_entries"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.memory.maxEntries")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="memory.similarity_threshold"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.memory.similarityThreshold")}</FormLabel>
            <FormControl>
              <Input type="number" step="0.01" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="memory.decay_factor"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.memory.decayFactor")}</FormLabel>
            <FormControl>
              <Input type="number" step="0.01" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}
