import { MessageCircle } from "lucide-react"
import { useTranslation } from "react-i18next"

import { GITHUB_ISSUES_URL } from "@/lib/siteMetadata"
import { cn } from "@/lib/utils"

interface GithubFeedbackLinkProps {
  className?: string
  labelClassName?: string
  onClick?: () => void
}

export function GithubFeedbackLink({
  className,
  labelClassName,
  onClick,
}: GithubFeedbackLinkProps) {
  const { t } = useTranslation()
  const label = t("common.githubFeedback")

  return (
    <a
      href={GITHUB_ISSUES_URL}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "group inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-full border border-primary/30 bg-primary/5 px-2.5 text-primary transition-all hover:-translate-y-px hover:border-primary/50 hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
        className,
      )}
    >
      <MessageCircle className="size-3.5 transition-transform duration-300 group-hover:scale-110" />
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
