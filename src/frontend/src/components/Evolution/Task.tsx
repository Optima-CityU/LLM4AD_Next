import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FolderOpen, Pencil, Plus } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import {
  Llm4AdTasksService,
  type TaskCreate,
  type TaskResponse,
} from "@/client"
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
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import useCustomToast from "@/hooks/useCustomToast"
import { useEvolution } from "@/hooks/useEvolution"
import { handleError } from "@/utils"

function getTasksQueryKey(projectId: string) {
  return ["listTasks", projectId]
}

function getTasksQueryOptions(projectId: string) {
  return {
    queryFn: () =>
      Llm4AdTasksService.listTasks({ projectId, skip: 0, limit: 100 }),
    queryKey: getTasksQueryKey(projectId),
    enabled: !!projectId,
  }
}

const nameSchema = z.object({
  name: z.string().min(1, { message: "任务名称不能为空" }).max(255),
})

type NameFormData = z.infer<typeof nameSchema>

function CreateTaskDialog({
  projectId,
  onCreated,
}: {
  projectId: string
  onCreated: (task: TaskResponse) => void
}) {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<NameFormData>({
    resolver: zodResolver(nameSchema),
    mode: "onBlur",
    defaultValues: { name: "" },
  })

  const mutation = useMutation({
    mutationFn: (data: TaskCreate) =>
      Llm4AdTasksService.createTask({ requestBody: data }),
    onSuccess: (data) => {
      showSuccessToast("任务创建成功")
      form.reset()
      setIsOpen(false)
      onCreated(data)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: getTasksQueryKey(projectId) })
    },
  })

  const onSubmit = (data: NameFormData) => {
    mutation.mutate({ ...data, project_id: projectId })
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <SidebarGroupAction title="新建任务">
          <Plus />
        </SidebarGroupAction>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>创建任务</DialogTitle>
          <DialogDescription>输入任务名称以创建新任务。</DialogDescription>
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
                      任务名称 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input placeholder="请输入任务名称" {...field} />
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

function RenameTaskDialog({
  task,
  open,
  onOpenChange,
  projectId,
}: {
  task: { id: string; name: string }
  open: boolean
  onOpenChange: (open: boolean) => void
  projectId: string
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<NameFormData>({
    resolver: zodResolver(nameSchema),
    mode: "onBlur",
    defaultValues: { name: task.name },
  })

  const mutation = useMutation({
    mutationFn: (data: NameFormData) =>
      Llm4AdTasksService.updateTask({
        taskId: task.id,
        requestBody: { name: data.name },
      }),
    onSuccess: () => {
      showSuccessToast("任务重命名成功")
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: getTasksQueryKey(projectId) })
    },
  })

  const onSubmit = (data: NameFormData) => {
    mutation.mutate(data)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>重命名任务</DialogTitle>
          <DialogDescription>修改任务名称。</DialogDescription>
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
                      任务名称 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input placeholder="请输入任务名称" {...field} />
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

export function Task({ projectId }: { projectId: string }) {
  const { data: tasks } = useQuery(getTasksQueryOptions(projectId))
  const { selectedTask, setSelectedTask } = useEvolution()
  const [renameTask, setRenameTask] = useState<{
    id: string
    name: string
  } | null>(null)

  // Auto-select first task when project changes or tasks load
  useEffect(() => {
    if (!tasks) return
    const items = tasks.items
    if (items.length > 0) {
      // If no task selected or selected task doesn't belong to this project
      if (!selectedTask || selectedTask.project_id !== projectId) {
        setSelectedTask(items[0])
      }
    } else {
      setSelectedTask(null)
    }
  }, [projectId, tasks, selectedTask, setSelectedTask])

  if (!projectId) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 px-4 py-8 text-center">
        <FolderOpen className="size-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">请先选择或创建一个项目</p>
      </div>
    )
  }

  const items = tasks?.items ?? []

  return (
    <>
      <SidebarGroup className=" group-data-[collapsible=icon]:hidden">
        <SidebarGroupLabel>任务列表</SidebarGroupLabel>
        <CreateTaskDialog
          projectId={projectId}
          onCreated={(task) => setSelectedTask(task)}
        />
        <SidebarGroupContent>
          {items.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 px-4 py-8 text-center">
              <FolderOpen className="size-10 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                暂无任务，点击上方 + 创建任务
              </p>
            </div>
          ) : (
            <SidebarMenu>
              {items.map((task) => (
                <SidebarMenuItem key={task.id}>
                  <SidebarMenuButton
                    isActive={selectedTask?.id === task.id}
                    onClick={() => setSelectedTask(task)}
                  >
                    {task.name}
                  </SidebarMenuButton>
                  <SidebarMenuAction
                    showOnHover
                    onClick={() =>
                      setRenameTask({ id: task.id, name: task.name })
                    }
                  >
                    <Pencil />
                  </SidebarMenuAction>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          )}
        </SidebarGroupContent>
      </SidebarGroup>

      {renameTask && (
        <RenameTaskDialog
          task={renameTask}
          open={!!renameTask}
          onOpenChange={(open) => {
            if (!open) setRenameTask(null)
          }}
          projectId={projectId}
        />
      )}
    </>
  )
}
