import type { ComponentProps, ReactNode } from "react"
import { TabsList } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

export default function ResearchWorkbenchTabsHeader({
  actions,
  label,
  listClassName,
  children,
  ...props
}: ComponentProps<"header"> & {
  actions?: ReactNode
  label?: string
  listClassName: string
}) {
  return (
    <header
      className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-card/55 p-3"
      {...props}
    >
      <TabsList
        aria-label={label}
        className={cn("grid min-w-0 flex-1", listClassName)}
      >
        {children}
      </TabsList>
      {actions}
    </header>
  )
}
