import { Camera, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react"
import { useRef, useState } from "react"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"

import type { SessionSnapshot } from "./mock-data"

function formatTimeAgo(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
}

export default function SnapshotDialog({
  open,
  onOpenChange,
  snapshots,
  onSave,
  onRestore,
  onDelete,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  snapshots: SessionSnapshot[]
  onSave: (name: string) => void
  onRestore: (snapshotId: string) => void
  onDelete: (snapshotId: string) => void
}) {
  const { t } = useTranslation()
  const [isCreating, setIsCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [confirmAction, setConfirmAction] = useState<{
    type: "restore" | "delete"
    snapshot: SessionSnapshot
  } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSave = () => {
    const name = newName.trim()
    if (!name) return
    onSave(name)
    setNewName("")
    setIsCreating(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSave()
    if (e.key === "Escape") {
      setIsCreating(false)
      setNewName("")
    }
  }

  const handleConfirm = () => {
    if (!confirmAction) return
    if (confirmAction.type === "restore") {
      onRestore(confirmAction.snapshot.id)
    } else {
      onDelete(confirmAction.snapshot.id)
    }
    setConfirmAction(null)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md" preventOutsideClose>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {t("research.snapshots.title")}
              {snapshots.length > 0 && (
                <Badge variant="secondary" className="text-xs">
                  {snapshots.length}
                </Badge>
              )}
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {/* Save section */}
            {!isCreating ? (
              <Button
                variant="outline"
                className="w-full justify-start gap-2"
                onClick={() => {
                  setIsCreating(true)
                  setTimeout(() => inputRef.current?.focus(), 0)
                }}
              >
                <Camera className="h-4 w-4" />
                {t("research.snapshots.saveButton")}
              </Button>
            ) : (
              <div className="flex gap-2">
                <Input
                  ref={inputRef}
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={t("research.snapshots.namePlaceholder")}
                  className="flex-1"
                  autoFocus
                />
                <Button
                  size="sm"
                  onClick={handleSave}
                  disabled={!newName.trim()}
                >
                  {t("research.snapshots.save")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setIsCreating(false)
                    setNewName("")
                  }}
                >
                  {t("research.snapshots.cancel")}
                </Button>
              </div>
            )}

            {/* Snapshot list */}
            {snapshots.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                {t("research.snapshots.empty")}
              </p>
            ) : (
              <ScrollArea className="max-h-[360px]">
                <div className="space-y-2">
                  {snapshots.map((snapshot) => (
                    <div
                      key={snapshot.id}
                      className="flex items-center justify-between rounded-lg border border-border/40 px-3 py-2.5 hover:bg-muted/40 transition-colors"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium truncate">
                          {snapshot.name}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {formatTimeAgo(snapshot.createdAt)}
                        </p>
                      </div>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0"
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onClick={() =>
                              setConfirmAction({ type: "restore", snapshot })
                            }
                          >
                            <RotateCcw className="h-4 w-4 mr-2" />
                            {t("research.snapshots.restore")}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-destructive focus:text-destructive"
                            onClick={() =>
                              setConfirmAction({ type: "delete", snapshot })
                            }
                          >
                            <Trash2 className="h-4 w-4 mr-2" />
                            {t("research.snapshots.delete")}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Confirmation dialog */}
      <AlertDialog
        open={!!confirmAction}
        onOpenChange={(open) => !open && setConfirmAction(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction?.type === "restore"
                ? t("research.snapshots.restoreConfirmTitle")
                : t("research.snapshots.deleteConfirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction?.type === "restore"
                ? t("research.snapshots.restoreConfirmDescription", {
                    name: confirmAction.snapshot.name,
                  })
                : t("research.snapshots.deleteConfirmDescription", {
                    name: confirmAction?.snapshot.name,
                  })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t("research.snapshots.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirm}
              className={
                confirmAction?.type === "delete"
                  ? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  : ""
              }
            >
              {t("research.snapshots.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
