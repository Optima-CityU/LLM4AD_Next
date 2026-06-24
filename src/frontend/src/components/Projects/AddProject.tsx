import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { Llm4AdProjectsService, type ProjectCreate } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import { DEFAULT_ICON_ID, IconPicker } from "./ProjectIcons"

type FormData = z.infer<ReturnType<typeof createFormSchema>>

function createFormSchema(t: (key: string) => string) {
  return z.object({
    name: z
      .string()
      .min(1, { message: t("validation.projectNameRequired") })
      .max(255),
    description: z.string().max(255).optional(),
    icon: z.string().max(255).optional(),
  })
}

export default function AddProject() {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t } = useTranslation()
  const navigate = useNavigate()

  const formSchema = createFormSchema(t)

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      name: "",
      description: "",
      icon: DEFAULT_ICON_ID,
    },
  })

  const mutation = useMutation({
    mutationFn: (data: ProjectCreate) =>
      Llm4AdProjectsService.createProject({ requestBody: data }),
    onSuccess: (data) => {
      showSuccessToast(t("projects.createSuccess"))
      form.reset()
      setIsOpen(false)
      navigate({ to: "/evolution", search: { projectId: data.id } })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["listProjects"] })
    },
  })

  const onSubmit = (data: FormData) => {
    mutation.mutate(data)
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button data-tour="new-project-btn">
          <Plus className="mr-2 size-4" />
          {t("projects.addProject")}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>{t("projects.addProject")}</DialogTitle>
          <DialogDescription>
            {t("projects.addProjectDescription")}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("projects.projectName")}{" "}
                      <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={t("projects.projectNamePlaceholder")}
                        type="text"
                        {...field}
                        required
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
                    <FormLabel>{t("projects.projectDescription")}</FormLabel>
                    <FormControl>
                      <Textarea
                        className="min-h-[80px] resize-y"
                        placeholder={t(
                          "projects.projectDescriptionPlaceholder",
                        )}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="icon"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("projects.projectIcon")}</FormLabel>
                    <FormControl>
                      <IconPicker
                        value={field.value ?? ""}
                        onChange={field.onChange}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                {t("common.create")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
