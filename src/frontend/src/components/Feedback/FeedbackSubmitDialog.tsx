import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { z } from "zod"
import { FeedbackService } from "@/client/sdk.gen"
import type { FeedbackCreate, FeedbackType } from "@/client/types.gen"
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
import useAuth from "@/hooks/useAuth"

const FEEDBACK_TYPES: FeedbackType[] = [
  "bug",
  "feature",
  "improvement",
  "question",
  "other",
]

const feedbackSchema = z.object({
  type: z.enum(["bug", "feature", "improvement", "question", "other"]),
  title: z.string().min(1).max(255),
  content: z.string().min(1).max(5000),
  contact_email: z.string().email().max(255).or(z.literal("")).optional(),
  contact_phone: z.string().max(50).optional(),
})

type FeedbackFormData = z.infer<typeof feedbackSchema>

interface FeedbackSubmitDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function FeedbackSubmitDialog({
  open,
  onOpenChange,
}: FeedbackSubmitDialogProps) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()

  const form = useForm<FeedbackFormData>({
    resolver: zodResolver(feedbackSchema),
    mode: "onBlur",
    defaultValues: {
      type: "bug",
      title: "",
      content: "",
      contact_email: "",
      contact_phone: "",
    },
  })

  useEffect(() => {
    if (user?.email && !form.getValues("contact_email")) {
      form.setValue("contact_email", user.email)
    }
  }, [user, form])

  const mutation = useMutation({
    mutationFn: (data: FeedbackCreate) =>
      FeedbackService.createFeedback({ requestBody: data }),
    onSuccess: () => {
      toast.success(t("feedback.submit.success"))
      form.reset({
        type: "bug",
        title: "",
        content: "",
        contact_email: user?.email || "",
        contact_phone: "",
      })
      queryClient.invalidateQueries({ queryKey: ["feedback"] })
      queryClient.invalidateQueries({ queryKey: ["feedback-statistics"] })
      onOpenChange(false)
    },
    onError: () => {
      toast.error(t("feedback.submit.error"))
    },
  })

  const onSubmit = (data: FeedbackFormData) => {
    const payload: FeedbackCreate = {
      type: data.type,
      title: data.title,
      content: data.content,
      contact_email: data.contact_email || null,
      contact_phone: data.contact_phone || null,
      browser_info: navigator.userAgent,
      page_url: window.location.href,
    }
    mutation.mutate(payload)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-xl max-h-[90vh] overflow-y-auto"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{t("feedback.submit.title")}</DialogTitle>
          <DialogDescription>
            {t("feedback.submit.description")}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t("feedback.submit.type")}
                    <span className="text-destructive ml-0.5">*</span>
                  </FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue
                          placeholder={t("feedback.submit.typePlaceholder")}
                        />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {FEEDBACK_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {t(`feedback.submit.typeOptions.${type}`)}
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
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t("feedback.submit.titleLabel")}
                    <span className="text-destructive ml-0.5">*</span>
                  </FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={255}
                      placeholder={t("feedback.submit.titlePlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="content"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t("feedback.submit.content")}
                    <span className="text-destructive ml-0.5">*</span>
                  </FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      maxLength={5000}
                      rows={5}
                      className="resize-y"
                      placeholder={t("feedback.submit.contentPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <FormField
                control={form.control}
                name="contact_email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("feedback.submit.contactEmail")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        maxLength={255}
                        placeholder={t(
                          "feedback.submit.contactEmailPlaceholder",
                        )}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="contact_phone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("feedback.submit.contactPhone")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        maxLength={50}
                        placeholder={t(
                          "feedback.submit.contactPhonePlaceholder",
                        )}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter className="pt-2">
              <DialogClose asChild>
                <Button type="button" variant="outline">
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                {t("feedback.submit.submitButton")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
