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

const vcTypes = ["git_worktree", "none"] as const

export default function VersionControlSection() {
  const { control } = useFormContext()
  const { t } = useTranslation()

  return (
    <div className="grid grid-cols-2 gap-4">
      <FormField
        control={control}
        name="version_control.enabled"
        render={({ field }) => (
          <FormItem className="flex items-center gap-2 space-y-0 pt-2">
            <FormControl>
              <Checkbox
                checked={field.value}
                onCheckedChange={field.onChange}
              />
            </FormControl>
            <FormLabel className="font-normal">
              {t("configForm.versionControl.enabled")}
            </FormLabel>
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="version_control.type"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.versionControl.type")}</FormLabel>
            <Select onValueChange={field.onChange} value={field.value}>
              <FormControl>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {vcTypes.map((v) => (
                  <SelectItem key={v} value={v}>
                    {v}
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
        name="version_control.local_path"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.versionControl.localPath")}</FormLabel>
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
        name="version_control.remote_url"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.versionControl.remoteUrl")}</FormLabel>
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
        name="version_control.revision"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.versionControl.revision")}</FormLabel>
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
        name="version_control.default_branch"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("configForm.versionControl.defaultBranch")}
            </FormLabel>
            <FormControl>
              <Input {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="version_control.max_worktree_age_days"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("configForm.versionControl.maxWorktreeAgeDays")}
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
        name="version_control.max_worktrees"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("configForm.versionControl.maxWorktrees")}</FormLabel>
            <FormControl>
              <Input type="number" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="version_control.commit_message_template"
        render={({ field }) => (
          <FormItem className="col-span-2">
            <FormLabel>
              {t("configForm.versionControl.commitMessageTemplate")}
            </FormLabel>
            <FormControl>
              <Input {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="version_control.auto_initialize"
        render={({ field }) => (
          <FormItem className="flex items-center gap-2 space-y-0">
            <FormControl>
              <Checkbox
                checked={field.value}
                onCheckedChange={field.onChange}
              />
            </FormControl>
            <FormLabel className="font-normal">
              {t("configForm.versionControl.autoInitialize")}
            </FormLabel>
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="version_control.auto_cleanup"
        render={({ field }) => (
          <FormItem className="flex items-center gap-2 space-y-0">
            <FormControl>
              <Checkbox
                checked={field.value}
                onCheckedChange={field.onChange}
              />
            </FormControl>
            <FormLabel className="font-normal">
              {t("configForm.versionControl.autoCleanup")}
            </FormLabel>
          </FormItem>
        )}
      />
    </div>
  )
}
