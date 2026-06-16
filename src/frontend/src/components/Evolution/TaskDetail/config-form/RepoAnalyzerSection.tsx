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

export default function RepoAnalyzerSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="grid grid-cols-2 gap-4">
      <FormField
        control={control}
        name="repo_analyzer.type"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.repoAnalyzer.type")}</FormLabel>
            <FormControl>
              <Input {...field} value={field.value ?? "evolve_detector"} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <div />
      <FormField
        control={control}
        name="repo_analyzer.context_lines_before"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("configForm.repoAnalyzer.contextLinesBefore")}
            </FormLabel>
            <FormControl>
              <Input type="number" min={0} {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="repo_analyzer.context_lines_after"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("configForm.repoAnalyzer.contextLinesAfter")}
            </FormLabel>
            <FormControl>
              <Input type="number" min={0} {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="repo_analyzer.include"
        render={({ field }) => (
          <FormItem className="col-span-2">
            <FormLabel>
              {t("configForm.repoAnalyzer.includePatterns")}
            </FormLabel>
            <FormControl>
              <Input
                value={Array.isArray(field.value) ? field.value.join(", ") : ""}
                onChange={(e) =>
                  field.onChange(
                    e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                placeholder={t("configForm.repoAnalyzer.includePlaceholder")}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="repo_analyzer.exclude"
        render={({ field }) => (
          <FormItem className="col-span-2">
            <FormLabel>
              {t("configForm.repoAnalyzer.excludePatterns")}
            </FormLabel>
            <FormControl>
              <Input
                value={Array.isArray(field.value) ? field.value.join(", ") : ""}
                onChange={(e) =>
                  field.onChange(
                    e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                placeholder={t("configForm.repoAnalyzer.excludePlaceholder")}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}
