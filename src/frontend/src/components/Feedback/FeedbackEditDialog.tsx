import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { z } from "zod"
import { FeedbackService } from "@/client/sdk.gen"
import type { FeedbackAdmin, FeedbackUpdate } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
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

const editSchema = z.object({
  status: z.enum(["pending", "in_progress", "resolved", "closed", "rejected"]),
  priority: z.enum(["low", "medium", "high", "urgent"]),
  admin_reply: z.string().max(5000).optional(),
  tags: z.string().max(500).optional(),
  internal_notes: z.string().max(2000).optional(),
})

type EditFormData = z.infer<typeof editSchema>

interface FeedbackEditDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  feedback: FeedbackAdmin | null
}

export function FeedbackEditDialog({
  open,
  onOpenChange,
  feedback,
}: FeedbackEditDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const form = useForm<EditFormData>({
    resolver: zodResolver(editSchema),
    values: {
      status: feedback?.status ?? "pending",
      priority: feedback?.priority ?? "low",
      admin_reply: feedback?.admin_reply ?? "",
      tags: feedback?.tags ?? "",
      internal_notes: feedback?.internal_notes ?? "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: FeedbackUpdate) =>
      FeedbackService.updateFeedback({
        feedbackId: feedback!.id,
        requestBody: data,
      }),
    onSuccess: () => {
      toast.success(t("feedback.list.updateSuccess"))
      queryClient.invalidateQueries({ queryKey: ["feedback"] })
      queryClient.invalidateQueries({ queryKey: ["feedback-statistics"] })
      onOpenChange(false)
    },
  })

  const onSubmit = (data: EditFormData) => {
    const payload: FeedbackUpdate = {
      status: data.status,
      priority: data.priority,
      admin_reply: data.admin_reply || null,
      tags: data.tags || null,
      internal_notes: data.internal_notes || null,
    }
    mutation.mutate(payload)
  }

  if (!feedback) return null

  const STATUSES = [
    "pending",
    "in_progress",
    "resolved",
    "closed",
    "rejected",
  ] as const
  const PRIORITIES = ["low", "medium", "high", "urgent"] as const

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-lg max-h-[90vh] overflow-y-auto"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{t("feedback.list.editTitle")}</DialogTitle>
          <DialogDescription>
            {t("feedback.list.editDescription")}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="status"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("feedback.list.columns.status")}</FormLabel>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {STATUSES.map((s) => (
                          <SelectItem key={s} value={s}>
                            {t(`feedback.list.status.${s}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="priority"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("feedback.list.columns.priority")}</FormLabel>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {PRIORITIES.map((p) => (
                          <SelectItem key={p} value={p}>
                            {t(`feedback.list.priority.${p}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="admin_reply"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("feedback.list.adminReplyLabel")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      maxLength={5000}
                      rows={3}
                      className="resize-y"
                      placeholder={t("feedback.list.adminReplyPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="tags"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("feedback.list.tagsLabel")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={500}
                      placeholder={t("feedback.list.tagsPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="internal_notes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("feedback.list.internalNotesLabel")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      maxLength={2000}
                      rows={2}
                      className="resize-y"
                      placeholder={t("feedback.list.internalNotesPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter className="pt-2">
              <DialogClose asChild>
                <Button type="button" variant="outline">
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                {t("common.save")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
