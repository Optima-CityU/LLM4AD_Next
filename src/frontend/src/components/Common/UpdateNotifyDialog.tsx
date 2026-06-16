import { RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

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
  clearUpdateNotify,
  subscribeUpdateNotify,
  type UpdateNotifyKind,
} from "@/utils/updateNotify"

/**
 * Global modal that prompts the user to refresh when the app needs it.
 *
 * Subscribes to the {@link subscribeUpdateNotify} signal and renders a
 * centered dialog whose copy depends on the notification kind:
 * - `deploying`: backend is being redeployed; ask the user to retry later.
 * - `stale-assets`: a new frontend build is live; ask the user to refresh.
 *
 * Mounted once near the app root. Has no props.
 */
export function UpdateNotifyDialog() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<UpdateNotifyKind>("deploying")

  useEffect(() => {
    return subscribeUpdateNotify((nextKind) => {
      setKind(nextKind)
      setOpen(true)
    })
  }, [])

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) {
      // Allow a later signal of the same kind to surface again.
      clearUpdateNotify()
    }
  }

  const handleRefresh = () => {
    window.location.reload()
  }

  const title =
    kind === "deploying"
      ? t("update.deploying.title")
      : t("update.staleAssets.title")
  const description =
    kind === "deploying"
      ? t("update.deploying.desc")
      : t("update.staleAssets.desc")

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            {t("update.dismiss")}
          </Button>
          <Button onClick={handleRefresh}>
            <RefreshCw />
            {t("update.refresh")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default UpdateNotifyDialog
