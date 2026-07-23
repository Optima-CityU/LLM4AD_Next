import { useTheme } from "@/components/theme-provider"
import { cn } from "@/lib/utils"

import TechCorner from "./TechCorner"

interface TechPanelProps {
  children: React.ReactNode
  className?: string
  cornerSize?: number
  cornerColor?: string
  /** 是否渲染四角切角装饰（默认 true）。圆角容器下可关闭以免与圆角冲突。 */
  showCorners?: boolean
}

export default function TechPanel({
  children,
  className,
  cornerSize = 20,
  cornerColor,
  showCorners = true,
}: TechPanelProps) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === "dark"

  return (
    <div
      className={cn(
        "relative overflow-hidden",
        !isDark && "border border-border/50",
        className,
      )}
      style={
        isDark
          ? {
              boxShadow:
                "inset 0 0 15px color-mix(in srgb, var(--primary) 3%, transparent), 0 0 8px color-mix(in srgb, var(--primary) 5%, transparent)",
            }
          : {
              boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
            }
      }
    >
      {showCorners && (
        <>
          <TechCorner
            position="top-left"
            size={cornerSize}
            color={cornerColor}
          />
          <TechCorner
            position="top-right"
            size={cornerSize}
            color={cornerColor}
          />
          <TechCorner
            position="bottom-left"
            size={cornerSize}
            color={cornerColor}
          />
          <TechCorner
            position="bottom-right"
            size={cornerSize}
            color={cornerColor}
          />
        </>
      )}
      {children}
    </div>
  )
}
