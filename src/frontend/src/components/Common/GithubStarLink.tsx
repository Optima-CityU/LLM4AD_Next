import { useQuery } from "@tanstack/react-query"
import { Star } from "lucide-react"
import { useTranslation } from "react-i18next"

import { isLoggedIn } from "@/hooks/useAuth"
import { GITHUB_PROJECT_URL } from "@/lib/siteMetadata"
import { starRewardStatusQueryOptions } from "@/lib/star-reward"
import { cn } from "@/lib/utils"

interface GithubStarLinkProps {
  className?: string
  labelClassName?: string
  onClick?: () => void
}

export function GithubStarLink({
  className,
  labelClassName,
  onClick,
}: GithubStarLinkProps) {
  const { t } = useTranslation()
  const label = t("common.starOnGithub")
  const loggedIn = isLoggedIn()
  const { data: rewardStatus, isPending } = useQuery({
    ...starRewardStatusQueryOptions,
    enabled: loggedIn,
  })

  if (loggedIn && (isPending || rewardStatus?.reward_granted)) return null

  return (
    <a
      href={GITHUB_PROJECT_URL}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "group relative inline-flex h-8 shrink-0 items-center justify-center gap-1.5 overflow-hidden rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 text-amber-700 shadow-[0_0_14px_-7px_rgba(245,158,11,0.9)] transition-all hover:-translate-y-px hover:border-amber-500/65 hover:bg-amber-500/20 hover:shadow-[0_0_18px_-6px_rgba(245,158,11,0.95)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60 dark:text-amber-300",
        className,
      )}
    >
      <span className="absolute inset-x-2 top-0 h-px bg-gradient-to-r from-transparent via-amber-300/80 to-transparent" />
      <Star className="size-3.5 fill-current transition-transform duration-300 group-hover:rotate-12 group-hover:scale-110" />
      <span
        className={cn(
          "whitespace-nowrap text-[11px] font-semibold tracking-wide",
          labelClassName,
        )}
      >
        {label}
      </span>
    </a>
  )
}
