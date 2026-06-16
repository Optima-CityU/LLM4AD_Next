import { useFormContext } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { Checkbox } from "@/components/ui/checkbox"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"

export default function WorkspaceSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="space-y-4">
      <FormField
        control={control}
        name="workspace.auto_create"
        render={({ field }) => (
          <FormItem className="flex items-center gap-2 space-y-0">
            <FormControl>
              <Checkbox
                checked={field.value}
                onCheckedChange={field.onChange}
              />
            </FormControl>
            <FormLabel className="font-normal">
              {t("configForm.workspace.autoCreate")}
            </FormLabel>
          </FormItem>
        )}
      />
      <p className="text-xs text-muted-foreground">
        {t("configForm.workspace.subdirectoriesHint")}
      </p>
    </div>
  )
}
