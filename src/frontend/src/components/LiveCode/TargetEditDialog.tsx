import { useMutation, useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { LiveCodesService } from "@/client/sdk.gen"
import type { TargetPublic } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"

interface TargetEditDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  liveCodeId: string
  target: TargetPublic | null
}

/** Convert an ISO timestamp to a `datetime-local` input value (local tz). */
function toLocalInput(iso: string | null): string {
  if (!iso) return ""
  try {
    return format(new Date(iso), "yyyy-MM-dd'T'HH:mm")
  } catch {
    return ""
  }
}

export function TargetEditDialog({
  open,
  onOpenChange,
  liveCodeId,
  target,
}: TargetEditDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [label, setLabel] = useState("")
  const [expiresLocal, setExpiresLocal] = useState("")
  const [scanLimit, setScanLimit] = useState("")
  const [isEnabled, setIsEnabled] = useState(true)
  const [sortOrder, setSortOrder] = useState("0")

  // Hydrate fields from the target whenever the dialog opens.
  useEffect(() => {
    if (open && target) {
      setLabel(target.label ?? "")
      setExpiresLocal(toLocalInput(target.expires_at))
      setScanLimit(target.scan_limit != null ? String(target.scan_limit) : "")
      setIsEnabled(target.is_enabled)
      setSortOrder(String(target.sort_order))
    }
  }, [open, target])

  const mutation = useMutation({
    mutationFn: () => {
      const limit = scanLimit.trim() ? Number.parseInt(scanLimit, 10) : null
      const order = Number.parseInt(sortOrder, 10)
      return LiveCodesService.updateTarget({
        liveCodeId,
        targetId: target!.id,
        requestBody: {
          label: label.trim() || null,
          expires_at: expiresLocal
            ? new Date(expiresLocal).toISOString()
            : null,
          scan_limit: limit && Number.isFinite(limit) ? limit : null,
          is_enabled: isEnabled,
          sort_order: Number.isFinite(order) ? order : 0,
        },
      })
    },
    onSuccess: () => {
      toast.success(t("liveCode.edit.success"))
      queryClient.invalidateQueries({ queryKey: ["live-code", liveCodeId] })
      queryClient.invalidateQueries({ queryKey: ["live-codes"] })
      onOpenChange(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>{t("liveCode.edit.title")}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="target-label">{t("liveCode.edit.label")}</Label>
            <Input
              id="target-label"
              value={label}
              maxLength={255}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={t("liveCode.edit.labelPlaceholder")}
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="target-expires">
                {t("liveCode.edit.expiresAt")}
              </Label>
              {expiresLocal && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-muted-foreground"
                  onClick={() => setExpiresLocal("")}
                >
                  {t("liveCode.edit.clearExpiry")}
                </Button>
              )}
            </div>
            <Input
              id="target-expires"
              type="datetime-local"
              value={expiresLocal}
              onChange={(e) => setExpiresLocal(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="target-scan-limit">
              {t("liveCode.edit.scanLimit")}
            </Label>
            <Input
              id="target-scan-limit"
              type="number"
              min={1}
              value={scanLimit}
              onChange={(e) => setScanLimit(e.target.value)}
              placeholder={t("liveCode.edit.scanLimitPlaceholder")}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="target-sort">{t("liveCode.edit.sortOrder")}</Label>
            <Input
              id="target-sort"
              type="number"
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value)}
            />
          </div>

          <div className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={isEnabled}
              onCheckedChange={(v) => setIsEnabled(v === true)}
            />
            {t("liveCode.edit.isEnabled")}
          </div>
        </div>

        <DialogFooter className="pt-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t("common.cancel")}
          </Button>
          <LoadingButton
            onClick={() => mutation.mutate()}
            loading={mutation.isPending}
          >
            {t("common.save")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
