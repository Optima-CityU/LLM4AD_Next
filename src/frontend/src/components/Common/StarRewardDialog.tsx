import { useQueryClient } from "@tanstack/react-query"
import { ExternalLink, Gift, Github } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { builtinProviderQuotaQueryKey } from "@/hooks/useProviders"
import { fetchStarRewardStatus, type StarRewardStatus } from "@/lib/star-reward"

interface StarRewardDialogProps {
  enabled: boolean
}

export function StarRewardDialog({ enabled }: StarRewardDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<StarRewardStatus | null>(null)
  const [open, setOpen] = useState(false)
  const notifiedGrantedRepos = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!enabled) return
    let cancelled = false

    void fetchStarRewardStatus()
      .then(async (nextStatus) => {
        if (cancelled) return
        setStatus(nextStatus)
        if (nextStatus.starred === false) {
          setOpen(true)
          return
        }
        if (nextStatus.reward_granted) {
          await queryClient.invalidateQueries({
            queryKey: builtinProviderQuotaQueryKey,
          })
          void queryClient.refetchQueries({
            queryKey: builtinProviderQuotaQueryKey,
            type: "active",
          })
          if (cancelled) return

          if (
            nextStatus.reward_granted_now &&
            !notifiedGrantedRepos.current.has(nextStatus.repo)
          ) {
            notifiedGrantedRepos.current.add(nextStatus.repo)
            toast.success(
              t("starReward.grantedToast", {
                amount: nextStatus.reward_amount,
              }),
              {
                description: nextStatus.api_key_cache_refreshed
                  ? t("starReward.apiKeyRefreshed")
                  : undefined,
              },
            )
          }
        }
      })
      .catch(() => {
        // Reward status should not block normal app usage.
      })

    return () => {
      cancelled = true
    }
  }, [enabled, queryClient, t])

  const repoUrl = useMemo(() => {
    if (!status?.repo) return "https://github.com"
    return `https://github.com/${status.repo}`
  }, [status?.repo])

  if (!status || status.starred !== false) return null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Gift className="text-primary" />
            {t("starReward.title", { amount: status.reward_amount })}
          </DialogTitle>
          <DialogDescription>
            {t("starReward.description", {
              amount: status.reward_amount,
              repo: status.repo,
            })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="outline" onClick={() => setOpen(false)}>
            {t("starReward.later")}
          </Button>
          <Button asChild>
            <a href={repoUrl} target="_blank" rel="noreferrer">
              <Github data-icon="inline-start" />
              {t("starReward.openGithub")}
              <ExternalLink data-icon="inline-end" />
            </a>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
