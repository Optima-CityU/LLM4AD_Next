import { CircleDot, Github, Landmark, Tag } from "lucide-react"

import {
  GITHUB_ISSUES_URL,
  GITHUB_PROJECT_URL,
  siteMetadata,
} from "@/lib/siteMetadata"
import { cn } from "@/lib/utils"

interface FooterMetadataLinksProps {
  className?: string
}

export function FooterMetadataLinks({ className }: FooterMetadataLinksProps) {
  const linkClassName =
    "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"

  return (
    <div className={cn("flex flex-wrap items-center justify-center gap-1", className)}>
      <a
        href={GITHUB_PROJECT_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClassName}
      >
        <Github className="size-3.5" />
        <span>GitHub</span>
      </a>
      <span className="inline-flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground">
        <Tag className="size-3.5" />
        <span>{siteMetadata.version}</span>
      </span>
      <a
        href={GITHUB_ISSUES_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClassName}
      >
        <CircleDot className="size-3.5" />
        <span>Issue</span>
      </a>
      {siteMetadata.beian && (
        <span className="inline-flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground">
          <Landmark className="size-3.5" />
          <span>{siteMetadata.beian}</span>
        </span>
      )}
    </div>
  )
}

export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="py-4 px-6 border-t border-border">
      <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
        <p className="text-muted-foreground text-sm">
          &copy; {currentYear} LLM4AD_Next Team. All rights reserved.
        </p>
        <FooterMetadataLinks />
      </div>
    </footer>
  )
}
