import { zodResolver } from "@hookform/resolvers/zod"
import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { Check, ChevronDown, Pencil, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import {
  Llm4AdProjectsService,
  type ProjectCreate,
  type ProjectUpdate,
} from "@/client"
import ProjectMemoryDialog from "@/components/Memory/ProjectMemoryDialog"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const PROJECTS_QUERY_KEY = ["listProjects"]

export function getProjectsQueryOptions() {
  return {
    queryFn: () => Llm4AdProjectsService.listProjects({ skip: 0, limit: 50 }),
    queryKey: PROJECTS_QUERY_KEY,
  }
}

const createFormSchema = z.object({
  name: z.string().min(1, { message: "项目名称不能为空" }).max(255),
  description: z.string().max(255).optional(),
})

type CreateFormData = z.infer<typeof createFormSchema>

function CreateProjectDialog({
  onCreated,
}: {
  onCreated: (id: string) => void
}) {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<CreateFormData>({
    resolver: zodResolver(createFormSchema),
    mode: "onBlur",
    defaultValues: { name: "", description: "" },
  })

  const mutation = useMutation({
    mutationFn: (data: ProjectCreate) =>
      Llm4AdProjectsService.createProject({ requestBody: data }),
    onSuccess: (data) => {
      showSuccessToast("项目创建成功")
      form.reset()
      setIsOpen(false)
      onCreated(data.id)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY })
    },
  })

  const onSubmit = (data: CreateFormData) => {
    mutation.mutate(data)
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" className="size-7">
          <Plus className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>创建项目</DialogTitle>
          <DialogDescription>填写项目信息以创建新项目。</DialogDescription>
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
                      项目名称 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input placeholder="请输入项目名称" {...field} />
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
                    <FormLabel>项目描述</FormLabel>
                    <FormControl>
                      <Input placeholder="请输入项目描述" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  取消
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                创建
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function EditProjectDialog({
  project,
  open,
  onOpenChange,
}: {
  project: { id: string; name: string; description: string | null }
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<CreateFormData>({
    resolver: zodResolver(createFormSchema),
    mode: "onBlur",
    defaultValues: {
      name: project.name,
      description: project.description ?? "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: ProjectUpdate) =>
      Llm4AdProjectsService.updateProject({
        projectId: project.id,
        requestBody: data,
      }),
    onSuccess: () => {
      showSuccessToast("项目修改成功")
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY })
    },
  })

  const onSubmit = (data: CreateFormData) => {
    mutation.mutate(data)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>编辑项目</DialogTitle>
          <DialogDescription>修改项目名称和描述。</DialogDescription>
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
                      项目名称 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input placeholder="请输入项目名称" {...field} />
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
                    <FormLabel>项目描述</FormLabel>
                    <FormControl>
                      <Input placeholder="请输入项目描述" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  取消
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                保存
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function DeleteProjectDialog({
  projectId,
  open,
  onOpenChange,
  onDeleted,
}: {
  projectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { handleSubmit } = useForm()

  const mutation = useMutation({
    mutationFn: () => Llm4AdProjectsService.deleteProject({ projectId }),
    onSuccess: () => {
      showSuccessToast("项目删除成功")
      onOpenChange(false)
      onDeleted()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY })
    },
  })

  const onSubmit = () => {
    mutation.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit(onSubmit)}>
          <DialogHeader>
            <DialogTitle>删除项目</DialogTitle>
            <DialogDescription>
              此操作将永久删除该项目及其下的所有任务，且无法撤销。确定要继续吗？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-4">
            <DialogClose asChild>
              <Button variant="outline" disabled={mutation.isPending}>
                取消
              </Button>
            </DialogClose>
            <LoadingButton
              variant="destructive"
              type="submit"
              loading={mutation.isPending}
            >
              删除
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function Project({
  selectedProjectId,
  onSelectProject,
}: {
  selectedProjectId: string
  onSelectProject: (id: string) => void
}) {
  const { data: projects } = useSuspenseQuery(getProjectsQueryOptions())
  const [deleteProjectId, setDeleteProjectId] = useState<string | null>(null)
  const [editProject, setEditProject] = useState<{
    id: string
    name: string
    description: string | null
  } | null>(null)

  const selectedProject = projects.items.find((p) => p.id === selectedProjectId)

  return (
    <div className="flex items-center gap-1 group-data-[collapsible=icon]:hidden">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" className="flex-1 min-w-0 justify-between">
            <span className="truncate">
              {selectedProject?.name || "选择项目"}
            </span>
            <ChevronDown className="size-4 shrink-0 opacity-50" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="w-[var(--radix-dropdown-menu-trigger-width)]"
        >
          {projects.items.map((project) => {
            const isSelected = project.id === selectedProjectId
            return (
              <DropdownMenuItem
                key={project.id}
                className="group gap-2"
                title={project.description || undefined}
                onClick={() => onSelectProject(project.id)}
              >
                <span className="size-4 shrink-0 flex items-center justify-center">
                  {isSelected && <Check className="size-4" />}
                </span>
                <span className="truncate flex-1">{project.name}</span>
                {!isSelected && (
                  <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 shrink-0">
                    <button
                      className="hover:text-foreground"
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditProject({
                          id: project.id,
                          name: project.name,
                          description: project.description,
                        })
                      }}
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      className="text-destructive hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation()
                        setDeleteProjectId(project.id)
                      }}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                )}
              </DropdownMenuItem>
            )
          })}
        </DropdownMenuContent>
      </DropdownMenu>
      {selectedProject && (
        <ProjectMemoryDialog
          projectId={selectedProject.id}
          projectName={selectedProject.name}
        />
      )}
      <CreateProjectDialog onCreated={(id) => onSelectProject(id)} />
      {editProject && (
        <EditProjectDialog
          project={editProject}
          open={!!editProject}
          onOpenChange={(open) => {
            if (!open) setEditProject(null)
          }}
        />
      )}
      {deleteProjectId && (
        <DeleteProjectDialog
          projectId={deleteProjectId}
          open={!!deleteProjectId}
          onOpenChange={(open) => {
            if (!open) setDeleteProjectId(null)
          }}
          onDeleted={() => setDeleteProjectId(null)}
        />
      )}
    </div>
  )
}
