import { Plus, Trash2 } from "lucide-react"
import { useFieldArray, useFormContext } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
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

const plannerSelectionStrategies = [
  "random",
  "weighted",
  "tournament",
  "roulette",
] as const

export default function PlannerSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()
  const { fields, append, remove } = useFieldArray({
    control,
    name: "planner.samplers",
  })

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <FormField
          control={control}
          name="planner.type"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.planner.type")}</FormLabel>
              <FormControl>
                <Input {...field} value={field.value ?? ""} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="planner.selection_strategy"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("configForm.planner.selectionStrategy")}</FormLabel>
              <Select
                onValueChange={field.onChange}
                value={field.value ?? "weighted"}
              >
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {plannerSelectionStrategies.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>

      <div className="space-y-3">
        <span className="text-sm font-medium">
          {t("configForm.planner.samplers")}
        </span>
        {fields.map((field, index) => (
          <div
            key={field.id}
            className="relative rounded-lg border bg-card p-4"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-muted-foreground">
                {t("configForm.planner.samplerIndex", { index: index + 1 })}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => remove(index)}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
            <FormField
              control={control}
              name={`planner.samplers.${index}.name`}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("configForm.planner.name")}</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append({ name: "", config: {} })}
        >
          <Plus className="mr-1 size-4" />
          {t("configForm.planner.addSampler")}
        </Button>
      </div>
    </div>
  )
}
