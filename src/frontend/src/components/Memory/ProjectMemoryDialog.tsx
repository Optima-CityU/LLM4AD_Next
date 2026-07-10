import { Database } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

import MemoryCardManager from "./MemoryCardManager"
import MemoryConfigEditor from "./MemoryConfigEditor"
import type { MemoryConfig } from "./types"

export default function ProjectMemoryDialog({
  projectId,
  projectName,
}: {
  projectId: string
  projectName?: string
}) {
  const [config, setConfig] = useState<MemoryConfig | null>(null)
  const memoryDisabled = !config?.system_runtime_available || !config?.mindmemos_binding_id

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-9 shrink-0"
          title="项目记忆"
          disabled={!projectId}
        >
          <Database className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>项目记忆</DialogTitle>
          <DialogDescription>
            {projectName ? `管理 ${projectName} 的项目级记忆和默认注入策略。` : "管理项目级记忆和默认注入策略。"}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <MemoryConfigEditor
            kind="project"
            projectId={projectId}
            title="项目默认记忆配置"
            description="控制该项目中新建任务默认继承的记忆注入策略。"
            onLoaded={setConfig}
            onSaved={setConfig}
          />
          <MemoryCardManager
            scope="project"
            projectId={projectId}
            title="项目级记忆"
            description="项目内跨任务复用的经验，适合沉淀领域知识和高价值算法模式。"
            disabled={memoryDisabled}
            disabledReason={
              !config?.system_runtime_available
                ? "MindMemOS 服务未就绪，当前无法新增或编辑远端记忆。"
                : "当前用户尚未绑定记忆模型，请先到全局记忆设置中绑定 Chat 与 Embedding。"
            }
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
