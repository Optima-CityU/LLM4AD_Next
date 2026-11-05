import { Check, Copy } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"

export default function GatewayUserId() {
  const { user } = useAuth()
  const { t } = useTranslation()
  const [copied, copy] = useCopyToClipboard()
  if (!user?.id) return null
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium">{t("remoteEvaluation.userId")}</p>
      <div className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 break-all text-xs text-muted-foreground">
          {user.id}
        </code>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={t("remoteEvaluation.copyId")}
          onClick={() => void copy(user.id)}
        >
          {copied === user.id ? <Check /> : <Copy />}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {t("remoteEvaluation.userIdHint")}
      </p>
    </div>
  )
}
