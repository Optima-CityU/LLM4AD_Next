import {
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
  PointerSensor,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core"
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable"
import { FolderPlus, Plus } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

import type { ResearchSession, SessionGroup } from "./mock-data"
import SessionGroupSection from "./SessionGroupSection"
import SessionItem from "./SessionItem"

function UngroupedDropZone({ children }: { children: React.ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: "ungrouped" })
  return (
    <div
      ref={setNodeRef}
      className={`min-h-[8px] rounded-lg transition-all duration-200 ${
        isOver ? "ring-1.5 ring-primary/20 bg-primary/3" : ""
      }`}
    >
      {children}
    </div>
  )
}

export default function SessionSidebar({
  open,
  sessions,
  groups,
  activeSessionId,
  onSelect,
  onDelete,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onToggleGroupCollapse,
  onMoveSession,
}: {
  open: boolean
  sessions: ResearchSession[]
  groups: SessionGroup[]
  activeSessionId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onCreateGroup: (name: string) => void
  onRenameGroup: (id: string, name: string) => void
  onDeleteGroup: (id: string) => void
  onToggleGroupCollapse: (id: string) => void
  onMoveSession: (sessionId: string, groupId: string | null) => void
}) {
  const { t } = useTranslation()
  const [deleteSessionId, setDeleteSessionId] = useState<string | null>(null)
  const [deleteGroupId, setDeleteGroupId] = useState<string | null>(null)
  const [createGroupOpen, setCreateGroupOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState("")
  const [dragActiveId, setDragActiveId] = useState<string | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  const ungroupedSessions = sessions.filter((s) => s.groupId === null)
  const ungroupedIds = ungroupedSessions.map((s) => s.id)

  const draggedSession = dragActiveId
    ? (sessions.find((s) => s.id === dragActiveId) ?? null)
    : null

  const deleteGroup = deleteGroupId
    ? groups.find((g) => g.id === deleteGroupId)
    : null
  const deleteGroupSessionCount = deleteGroupId
    ? sessions.filter((s) => s.groupId === deleteGroupId).length
    : 0

  const handleDragStart = (event: DragStartEvent) => {
    setDragActiveId(event.active.id as string)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    setDragActiveId(null)

    if (!over) return

    const sessionId = active.id as string
    const overId = over.id as string

    if (overId === "ungrouped") {
      onMoveSession(sessionId, null)
      return
    }

    const targetGroup = groups.find((g) => g.id === overId)
    if (targetGroup) {
      onMoveSession(sessionId, targetGroup.id)
      return
    }

    const targetSession = sessions.find((s) => s.id === overId)
    if (targetSession) {
      onMoveSession(sessionId, targetSession.groupId)
    }
  }

  const handleCreateGroup = () => {
    const trimmed = newGroupName.trim()
    if (trimmed) {
      onCreateGroup(trimmed)
      setNewGroupName("")
      setCreateGroupOpen(false)
    }
  }

  return (
    <aside
      className={`shrink-0 flex flex-col bg-muted/15 border-r border-border/30 overflow-hidden transition-all duration-300 ease-in-out ${
        open ? "w-60 opacity-100" : "w-0 opacity-0"
      }`}
    >
      <div className="w-60 h-full flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-12 border-b border-border/20">
          <span className="text-[11px] font-semibold text-foreground/60 uppercase tracking-[0.1em]">
            {t("research.sidebar.sessions")}
          </span>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setCreateGroupOpen(true)}
                  className="size-7 hover:bg-primary/10 hover:text-primary transition-colors"
                >
                  <FolderPlus className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">
                {t("research.sidebar.newGroup")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        {/* Session list with DnD */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          <DndContext
            sensors={sensors}
            collisionDetection={pointerWithin}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
          >
            {/* Ungrouped sessions */}
            {ungroupedSessions.length > 0 && (
              <UngroupedDropZone>
                <SortableContext
                  items={ungroupedIds}
                  strategy={verticalListSortingStrategy}
                >
                  <div className="space-y-0.5 py-1">
                    {ungroupedSessions.map((session) => (
                      <SessionItem
                        key={session.id}
                        session={session}
                        isActive={session.id === activeSessionId}
                        onSelect={onSelect}
                        onDelete={setDeleteSessionId}
                      />
                    ))}
                  </div>
                </SortableContext>
              </UngroupedDropZone>
            )}

            {ungroupedSessions.length === 0 && groups.length > 0 && (
              <UngroupedDropZone>
                <div className="py-2" />
              </UngroupedDropZone>
            )}

            {/* Groups */}
            {groups.length > 0 && (
              <div className="space-y-1 mt-1">
                {groups.map((group) => {
                  const groupSessions = sessions.filter(
                    (s) => s.groupId === group.id,
                  )
                  return (
                    <SessionGroupSection
                      key={group.id}
                      group={group}
                      sessions={groupSessions}
                      activeSessionId={activeSessionId}
                      onToggleCollapse={onToggleGroupCollapse}
                      onRename={onRenameGroup}
                      onDelete={setDeleteGroupId}
                      onSelectSession={onSelect}
                      onDeleteSession={setDeleteSessionId}
                    />
                  )
                })}
              </div>
            )}

            {/* Empty state */}
            {sessions.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 px-4">
                <div className="size-10 rounded-xl bg-muted/60 flex items-center justify-center mb-3">
                  <Plus className="size-5 text-muted-foreground/30" />
                </div>
                <p className="text-[11px] text-muted-foreground/40 text-center">
                  {t("research.sidebar.noSessions")}
                </p>
              </div>
            )}

            <DragOverlay>
              {draggedSession ? (
                <SessionItem
                  session={draggedSession}
                  isActive={false}
                  overlay
                />
              ) : null}
            </DragOverlay>
          </DndContext>
        </div>
      </div>

      {/* Delete session confirmation */}
      <AlertDialog
        open={!!deleteSessionId}
        onOpenChange={(open) => !open && setDeleteSessionId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("research.sidebar.deleteConfirm")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("research.sidebar.deleteDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (deleteSessionId) onDelete(deleteSessionId)
                setDeleteSessionId(null)
              }}
            >
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete group confirmation */}
      <AlertDialog
        open={!!deleteGroupId}
        onOpenChange={(open) => !open && setDeleteGroupId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("research.sidebar.deleteGroup")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("research.sidebar.deleteGroupConfirm", {
                name: deleteGroup?.name ?? "",
                count: deleteGroupSessionCount,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (deleteGroupId) onDeleteGroup(deleteGroupId)
                setDeleteGroupId(null)
              }}
            >
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Create group dialog */}
      <Dialog open={createGroupOpen} onOpenChange={setCreateGroupOpen}>
        <DialogContent className="sm:max-w-[360px]" preventOutsideClose>
          <DialogHeader>
            <DialogTitle>{t("research.sidebar.createGroup")}</DialogTitle>
          </DialogHeader>
          <div className="py-3">
            <Input
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              placeholder={t("research.sidebar.groupNamePlaceholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreateGroup()
              }}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setNewGroupName("")
                setCreateGroupOpen(false)
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={handleCreateGroup} disabled={!newGroupName.trim()}>
              {t("research.sidebar.createGroup")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
