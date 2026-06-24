import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { z } from "zod"
import { LiveCodesService } from "@/client/sdk.gen"
import type { LiveCodeDetail, LiveCodeStrategy } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { STRATEGIES } from "./liveCodeStyles"

const formSchema = z.object({
  name: z.string().trim().min(1).max(255),
  strategy: z.enum(["sequential", "round_robin", "random"]),
  landing_title: z.string().max(255).optional(),
  landing_tip: z.string().max(512).optional(),
  description: z.string().max(2000).optional(),
})

type FormData = z.infer<typeof formSchema>

interface LiveCodeFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When provided, the dialog edits this live code; otherwise it creates one. */
  liveCode?: LiveCodeDetail | null
}

export function LiveCodeFormDialog({
  open,
  onOpenChange,
  liveCode,
}: LiveCodeFormDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const isEdit = !!liveCode

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    values: {
      name: liveCode?.name ?? "",
      strategy: (liveCode?.strategy ?? "sequential") as LiveCodeStrategy,
      landing_title: liveCode?.landing_title ?? "",
      landing_tip: liveCode?.landing_tip ?? "",
      description: liveCode?.description ?? "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: FormData) => {
      const payload = {
        name: data.name.trim(),
        strategy: data.strategy,
        landing_title: data.landing_title?.trim() || null,
        landing_tip: data.landing_tip?.trim() || null,
        description: data.description?.trim() || null,
      }
      return isEdit
        ? LiveCodesService.updateLiveCode({
            liveCodeId: liveCode!.id,
            requestBody: payload,
          })
        : LiveCodesService.createLiveCode({ requestBody: payload })
    },
    onSuccess: () => {
      toast.success(
        isEdit
          ? t("liveCode.form.updateSuccess")
          : t("liveCode.form.createSuccess"),
      )
      queryClient.invalidateQueries({ queryKey: ["live-codes"] })
      if (isEdit) {
        queryClient.invalidateQueries({
          queryKey: ["live-code", liveCode!.id],
        })
      }
      onOpenChange(false)
    },
  })

  const selectedStrategy = form.watch("strategy")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-lg max-h-[90vh] overflow-y-auto"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>
            {isEdit
              ? t("liveCode.form.editTitle")
              : t("liveCode.form.createTitle")}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? t("liveCode.form.editDescription")
              : t("liveCode.form.createDescription")}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit((d) => mutation.mutate(d))}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveCode.form.name")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={255}
                      placeholder={t("liveCode.form.namePlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="strategy"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveCode.strategy.label")}</FormLabel>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {STRATEGIES.map((s) => (
                        <SelectItem key={s} value={s}>
                          {t(`liveCode.strategy.${s}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    {t(`liveCode.strategy.hint.${selectedStrategy}`)}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="landing_title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveCode.form.landingTitle")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={255}
                      placeholder={t("liveCode.form.landingTitlePlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="landing_tip"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveCode.form.landingTip")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={512}
                      placeholder={t("liveCode.form.landingTipPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("liveCode.form.description")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      maxLength={2000}
                      rows={2}
                      className="resize-y"
                      placeholder={t("liveCode.form.descriptionPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter className="pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                {t("common.cancel")}
              </Button>
              <LoadingButton type="submit" loading={mutation.isPending}>
                {isEdit ? t("common.save") : t("common.create")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
