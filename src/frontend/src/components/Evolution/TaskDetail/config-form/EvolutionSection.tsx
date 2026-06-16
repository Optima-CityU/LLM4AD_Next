import { useFormContext } from "react-hook-form"
import { useTranslation } from "react-i18next"

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

const selectionStrategies = ["tournament", "roulette", "rank"] as const

export default function EvolutionSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="grid grid-cols-2 gap-4">
      <FormField
        control={control}
        name="evolution.type"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.type")}</FormLabel>
            <FormControl>
              <Input {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.planner_type"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.plannerType")}</FormLabel>
            <FormControl>
              <Input {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.population_size"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.populationSize")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.max_generations"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.maxGenerations")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.elite_ratio"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.eliteRatio")}</FormLabel>
            <FormControl>
              <Input type="number" step="0.01" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.mutation_rate"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.mutationRate")}</FormLabel>
            <FormControl>
              <Input type="number" step="0.01" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.crossover_rate"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.crossoverRate")}</FormLabel>
            <FormControl>
              <Input type="number" step="0.01" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.selection_strategy"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.selectionStrategy")}</FormLabel>
            <Select onValueChange={field.onChange} value={field.value}>
              <FormControl>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {selectionStrategies.map((s) => (
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
      <FormField
        control={control}
        name="evolution.tournament_size"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.tournamentSize")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.planner_provider"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.plannerProvider")}</FormLabel>
            <FormControl>
              <Input {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.coder_provider"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.coderProvider")}</FormLabel>
            <FormControl>
              <Input {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.early_stop_patience"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.earlyStopPatience")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.early_stop_threshold"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("configForm.evolution.earlyStopThreshold")}
            </FormLabel>
            <FormControl>
              <Input type="number" step="0.000001" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.checkpoint_interval"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("configForm.evolution.checkpointInterval")}
            </FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="evolution.max_checkpoints"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.evolution.maxCheckpoints")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}
